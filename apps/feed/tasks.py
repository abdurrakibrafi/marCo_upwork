from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from django.db.models import Q

from apps.feed.models import FeedItem, Source
from apps.entity.models import Entity
from apps.sports_apis.services.brave import brave_service
from apps.sports_apis.services.rss import rss_discovery_service, rss_polling_service

import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _extract_domain(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        if not parsed.scheme:
            url = f"https://{url}"
            parsed = urlparse(url)
        if not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return None


@shared_task(bind=True, max_retries=1)
def discover_rss_feeds_for_entity(self, entity_id: int):
    """Discover RSS sources for an entity (runs once per entity)."""
    try:
        entity = Entity.objects.get(id=entity_id, is_active=True)
    except Entity.DoesNotExist:
        return f"Entity {entity_id} does not exist"

    if getattr(entity, 'rss_discovery_done', False):
        return f"Discovery already completed for {entity.name}"

    domains = brave_service.discover_sources_for_entity(entity.name, entity.type, entity.sport)
    for domain in domains:
        extract_rss_from_domain.delay(entity_id, domain)

    entity.rss_discovery_done = True
    entity.save(update_fields=['rss_discovery_done'])
    return f"Discovered {len(domains)} domains for {entity.name}"


@shared_task(bind=True, max_retries=1)
def extract_rss_from_domain(self, entity_id: int, domain: str):
    """Given a domain, discover valid RSS feeds and store them."""
    feeds = rss_discovery_service.discover_feeds_for_domain(domain)

    for feed_url in feeds:
        store_validated_feed.delay(entity_id, feed_url, discovery_source='brave')

    return f"Found {len(feeds)} feeds for {domain}"


@shared_task(bind=True, max_retries=1)
def store_validated_feed(self, entity_id: int, feed_url: str, discovery_source: str = 'brave'):
    """Validate a feed URL and store it as a Source linked to the entity."""
    try:
        entity = Entity.objects.get(id=entity_id, is_active=True)
    except Entity.DoesNotExist:
        return f"Entity {entity_id} does not exist"

    # Ensure feed is valid first
    if not rss_discovery_service._validate_feed(feed_url):
        return f"Feed {feed_url} is not valid"

    domain = _extract_domain(feed_url)
    if not domain:
        domain = feed_url

    source, created = Source.objects.get_or_create(
        rss_url=feed_url,
        defaults={
            'name': domain,
            'domain': domain,
            'is_active': True,
        }
    )

    # Keep rss_feed_url up to date in case we discovered a better URL later
    # if source.rss_feed_url != feed_url:
    #     source.rss_feed_url = feed_url
    #     source.save(update_fields=['rss_feed_url'])

    # Link to entity (many-to-many)
    source.entities.add(entity)

    # Immediately poll once
    poll_single_source.delay(source.id)

    return f"Stored source {source.id} for {entity.name} ({'created' if created else 'updated'})"


@shared_task
def update_all_entity_feeds(entity_id: int):
    """Trigger all feed updates for an entity."""
    return discover_rss_feeds_for_entity.delay(entity_id)


@shared_task
def update_user_nest_feeds(user_id: int):
    """Update feeds for all entities in a user's nest"""
    from apps.nest.models import UserNest

    nest_entity_ids = list(
        UserNest.objects.filter(user_id=user_id).values_list('entity_id', flat=True)
    )

    for entity_id in nest_entity_ids:
        update_all_entity_feeds.delay(entity_id)

    return f"Triggered feed updates for {len(nest_entity_ids)} entities in user {user_id}'s nest"


@shared_task
def update_trending_entities_feeds():
    """Update feeds for top 50 trending entities by follower count"""
    trending = Entity.objects.filter(is_active=True).order_by('-follower_count')[:50]

    for entity in trending:
        update_all_entity_feeds.delay(entity.id)

    return f"Triggered feed updates for {trending.count()} trending entities"


@shared_task
def poll_all_active_sources():
    """Enqueue polling for all RSS sources that are due."""
    now = timezone.now()
    due_sources = Source.objects.filter(
        is_active=True
    ).filter(
        Q(last_polled_at__isnull=True) |
        Q(last_polled_at__lte=now - timedelta(minutes=1))
    )

    # Only poll sources that are due based on their interval
    to_poll = []
    for source in due_sources:
        if not source.last_polled_at:
            to_poll.append(source.id)
            continue
        elapsed = (now - source.last_polled_at).total_seconds()
        if elapsed >= source.poll_interval_minutes * 60:
            to_poll.append(source.id)

    for source_id in to_poll:
        poll_single_source.delay(source_id)

    return f"Queued {len(to_poll)} sources for polling"


@shared_task(bind=True, max_retries=2)
def poll_single_source(self, source_id: int):
    """Poll a single Source and create FeedItems for new entries."""
    try:
        source = Source.objects.get(id=source_id, is_active=True)
    except Source.DoesNotExist:
        return f"Source {source_id} not found or inactive"

    result = rss_polling_service.poll_feed(source)
    if not result.get('success'):
        source.poll_failures += 1
        source.save(update_fields=['poll_failures'])
        logger.warning(f"Polling failed for source {source.id}: {result.get('error')}")
        raise self.retry(exc=Exception(result.get('error')))

    new_items = 0
    for entry in result.get('entries', []):
        url = entry.get('url')
        if not url:
            continue

        # Determine which entities this item is relevant for
        candidate_entities = list(source.entities.all())
        if not candidate_entities:
            # Fallback: match against all active entities (expensive; avoid if possible)
            candidate_entities = list(Entity.objects.filter(is_active=True))

        text = f"{entry.get('title','')} {entry.get('summary','')}".lower()

        candidate_entities_that_matched = []
        for entity in candidate_entities:
            if entity.name.lower() in text:
                candidate_entities_that_matched.append(entity)

        if not candidate_entities_that_matched:
            continue  # Skip if no entities match

        import hashlib

        url_hash = hashlib.md5(url.encode()).hexdigest()

        obj, created = FeedItem.objects.get_or_create(
            url_hash=url_hash,
            defaults={
                'source': source,
                'title': entry.get('title', '')[:500],
                'url': url,
                'summary': entry.get('summary', ''),
                'thumbnail_url': entry.get('thumbnail_url', ''),
                'published_at': entry.get('published_at') or timezone.now(),
            }
        )
        if created:
            obj.entities.set(candidate_entities_that_matched)
            new_items += 1

    # Update polling metadata
    source.last_polled_at = timezone.now()
    source.poll_failures = 0
    source.save(update_fields=['last_polled_at', 'poll_failures'])

    logger.info(f"Polled source {source.id}: {new_items} new items")
    return f"Polled source {source.id}: {new_items} new items"


@shared_task
def cleanup_old_feed_items():
    """Delete feed items older than 30 days"""
    cutoff_date = timezone.now() - timedelta(days=30)
    deleted_count = FeedItem.objects.filter(published_at__lt=cutoff_date).delete()[0]

    logger.info(f"Deleted {deleted_count} old feed items")
    return f"Deleted {deleted_count} old feed items"


@shared_task
def mark_trending_items():
    FeedItem.objects.update(is_trending=False)

    last_24h = timezone.now() - timedelta(hours=24)
    
    # Get IDs first, THEN update — can't update a sliced queryset
    trending_ids = list(
        FeedItem.objects.filter(
            published_at__gte=last_24h
        ).order_by('-views')[:100].values_list('id', flat=True)
    )
    
    FeedItem.objects.filter(id__in=trending_ids).update(is_trending=True)
    
    return f"Marked {len(trending_ids)} items as trending"





@shared_task
def fetch_brave_news_for_entity(entity_id: int):
    """
    Fetch latest news for a single entity via Brave Search News API.
    Creates FeedItems directly — no RSS needed.
    """
    from apps.sports_apis.services.brave_news import brave_news_service

    try:
        entity = Entity.objects.get(id=entity_id, is_active=True)
    except Entity.DoesNotExist:
        return f"Entity {entity_id} not found"

    articles = brave_news_service.fetch_news_for_entity(
        entity.name, entity.type, entity.sport
    )

    if not articles:
        return f"No articles found for {entity.name}"

    new_count = 0
    for article in articles:
        # Get or create a Source for this domain
        source_domain = article.get('source_domain', '')
        source_name = article.get('source_name', 'Unknown')

        if source_domain:
            source, _ = Source.objects.get_or_create(
                domain=source_domain,
                defaults={
                    'name': source_name,
                    'rss_url': None,
                    'is_active': True,
                    'discovery_source': 'brave',
                }
            )
        else:
            # Fallback source
            source, _ = Source.objects.get_or_create(
                name='Brave News',
                defaults={
                    'domain': 'brave.com',
                    'rss_url': None,
                    'is_active': True,
                    'discovery_source': 'brave',
                }
            )

        # Create FeedItem if not already exists
        obj, created = FeedItem.objects.get_or_create(
            url_hash=article['url_hash'],
            defaults={
                'source': source,
                'title': article['title'],
                'url': article['url'],
                'summary': article['summary'],
                'thumbnail_url': article['thumbnail_url'],
                'published_at': article['published_at'],
            }
        )

        if created:
            obj.entities.add(entity)
            new_count += 1
        else:
            # Always make sure entity is linked even if article existed
            obj.entities.add(entity)

    logger.info(f"Brave news for {entity.name}: {new_count} new articles")
    return f"Fetched {len(articles)} articles for {entity.name}, {new_count} new"


@shared_task
def fetch_brave_news_for_all_nest_entities():
    """
    Fetch Brave news for ALL entities currently in any user's nest.
    Schedule this every 30 minutes in celery.py.
    """
    from apps.nest.models import UserNest

    entity_ids = list(
        UserNest.objects.values_list('entity_id', flat=True).distinct()
    )

    for entity_id in entity_ids:
        fetch_brave_news_for_entity.delay(entity_id)

    return f"Triggered Brave news fetch for {len(entity_ids)} entities"


@shared_task
def fetch_brave_news_for_trending():
    """
    Fetch Brave news for top 20 trending entities.
    Keeps the public feed fresh even for non-logged-in users.
    """
    entities = Entity.objects.filter(
        is_active=True
    ).order_by('-follower_count')[:20]

    for entity in entities:
        fetch_brave_news_for_entity.delay(entity.id)

    return f"Triggered Brave news fetch for {entities.count()} trending entities"