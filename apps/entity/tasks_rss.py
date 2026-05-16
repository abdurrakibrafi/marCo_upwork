"""
Celery tasks for entity embeddings, RSS fetching, and feed aggregation.
"""
from celery import shared_task
from celery.utils.log import get_task_logger
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta
import feedparser
import logging

from apps.entity.models import Entity, CanonicalEntity
from apps.feed.models import RSSSource, FeedItem, EntitySource
from apps.entity.utils.embeddings import get_embedding_service
from apps.entity.utils.normalizers import normalize_entity_name

logger = get_task_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# EMBEDDINGS — Generate for all entities
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=3)
def generate_entity_embeddings(self, entity_id=None):
    """
    Generate embeddings for entities (AI matching).
    If entity_id provided, do just that one. Else, batch all missing.
    
    Run: python manage.py celery_worker
    Trigger: generate_entity_embeddings.delay()  or specify entity_id
    """
    embedding_service = get_embedding_service()
    if not embedding_service:
        logger.warning("Embedding service not available")
        return f"Skipped (no OpenAI key)"
    
    if entity_id:
        entities = Entity.objects.filter(id=entity_id, embedding__isnull=True)
        logger.info(f"Generating embedding for entity {entity_id}")
    else:
        # Find all without embeddings
        entities = Entity.objects.filter(
            embedding__isnull=True,
            has_api_data=True,
            is_active=True
        ).order_by('-updated_at')[:50]  # Batch of 50
        logger.info(f"Generating embeddings for {entities.count()} entities")
    
    generated = 0
    for entity in entities:
        try:
            # Generate embedding for: "Team Name Sport Type"
            text = f"{entity.name} {entity.sport} {entity.type}"
            embedding = embedding_service.generate_embedding(text)
            
            if embedding:
                entity.embedding = embedding
                entity.save(update_fields=['embedding'])
                generated += 1
        except Exception as e:
            logger.error(f"Embedding failed for {entity.name}: {e}")
            if self.request.retries < self.max_retries:
                self.retry(exc=e, countdown=60)
            continue
    
    logger.info(f"Generated {generated} embeddings")
    return f"Generated embeddings for {generated} entities"


# ─────────────────────────────────────────────────────────────────────────────
# RSS FETCHING — Scheduled daily
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=2)
def fetch_rss_feed(self, rss_source_id):
    """
    Fetch and parse a single RSS feed.
    Deduplicates articles, links to entities.
    """
    try:
        rss_source = RSSSource.objects.get(id=rss_source_id)
    except RSSSource.DoesNotExist:
        logger.error(f"RSSSource {rss_source_id} not found")
        return f"RSSSource {rss_source_id} not found"
    
    if not rss_source.is_active:
        logger.info(f"RSSSource {rss_source.name} is inactive, skipping")
        return f"Skipped (inactive): {rss_source.name}"
    
    try:
        logger.info(f"Fetching RSS: {rss_source.name} from {rss_source.url}")
        
        # Parse RSS
        feed = feedparser.parse(rss_source.url, timeout=15)
        
        if feed.bozo and feed.bozo_exception:
            logger.warning(f"RSS parse warning for {rss_source.name}: {feed.bozo_exception}")
        
        entries = feed.entries if hasattr(feed, 'entries') else []
        logger.info(f"Fetched {len(entries)} entries from {rss_source.name}")
        
        created_count = 0
        
        for entry in entries:
            try:
                # Extract article data
                title = entry.get('title', '')
                link = entry.get('link', '')
                summary = entry.get('summary', '')
                published = entry.get('published_parsed')
                
                if not title or not link:
                    continue
                
                # Generate URL hash for deduplication
                import hashlib
                url_hash = hashlib.md5(link.encode()).hexdigest()
                
                # Convert published_parsed to datetime
                if published:
                    from datetime import datetime
                    published_dt = datetime(*published[:6])
                else:
                    published_dt = timezone.now()
                
                # Create or update FeedItem
                feed_item, created = FeedItem.objects.get_or_create(
                    url_hash=url_hash,
                    defaults={
                        'source': rss_source,
                        'title': title[:500],
                        'url': link[:5000],
                        'summary': summary[:2000],
                        'published_at': published_dt,
                    }
                )
                
                if created:
                    # Link to entities based on RSSSource.entities
                    feed_item.entities.set(rss_source.entities.all())
                    created_count += 1
            
            except Exception as e:
                logger.warning(f"Error processing entry in {rss_source.name}: {e}")
                continue
        
        # Update metadata
        rss_source.last_fetched_at = timezone.now()
        rss_source.fetch_failures = 0
        rss_source.save(update_fields=['last_fetched_at', 'fetch_failures'])
        
        logger.info(f"✅ {rss_source.name}: created {created_count} new articles")
        return f"{rss_source.name}: {created_count} new articles"
    
    except Exception as e:
        logger.error(f"RSS fetch failed for {rss_source.name}: {e}")
        rss_source.fetch_failures += 1
        rss_source.save(update_fields=['fetch_failures'])
        
        if self.request.retries < self.max_retries:
            self.retry(exc=e, countdown=300)  # Retry in 5 min
        
        return f"Failed to fetch {rss_source.name}: {str(e)}"


@shared_task
def fetch_all_rss_feeds():
    """
    Orchestrator: fetch all active RSS sources (called daily or 6-hourly).
    """
    rss_sources = RSSSource.objects.filter(is_active=True)
    logger.info(f"Starting to fetch {rss_sources.count()} RSS sources")
    
    for rss_source in rss_sources:
        # Check if needs fetching
        if rss_source.last_fetched_at:
            elapsed = timezone.now() - rss_source.last_fetched_at
            if elapsed < timedelta(hours=rss_source.fetch_interval_hours):
                continue  # Too recent, skip
        
        fetch_rss_feed.apply_async(
            args=[rss_source.id],
            countdown=2  # Spread out requests
        )
    
    logger.info(f"Queued {rss_sources.count()} RSS fetch tasks")
    return f"Queued {rss_sources.count()} tasks"


# ─────────────────────────────────────────────────────────────────────────────
# AGGREGATE FEED — Combine RSS + Sources + Brave for entity
# ─────────────────────────────────────────────────────────────────────────────

@shared_task
def aggregate_entity_feed(entity_id: int, user_id: int = None):
    """
    Aggregate feed for an entity considering:
    - RSS sources (global)
    - User-selected sources (EntitySource)
    - Brave Search (on-demand)
    
    Returns combined feed sorted by relevance/date.
    """
    from apps.nest.models import UserNest
    
    try:
        entity = Entity.objects.get(id=entity_id)
    except Entity.DoesNotExist:
        return f"Entity {entity_id} not found"
    
    # Get RSS articles for this entity
    rss_items = FeedItem.objects.filter(
        entities=entity,
        source__discovery_source='known'  # From RSS
    ).order_by('-published_at')[:20]
    
    # If user specified, also get their selected sources
    user_sources = []
    if user_id:
        try:
            user_nest = UserNest.objects.get(user_id=user_id, entity=entity)
            entity_sources = user_nest.selected_sources.all()
            user_sources = [es.source for es in entity_sources]
        except UserNest.DoesNotExist:
            pass
    
    # Combine
    result = {
        'entity_id': entity_id,
        'entity_name': entity.name,
        'rss_count': rss_items.count(),
        'user_sources': len(user_sources),
        'feed_items': list(rss_items.values('id', 'title', 'url', 'published_at')),
    }
    
    logger.info(f"Aggregated feed for {entity.name}: {rss_items.count()} RSS items")
    return result
