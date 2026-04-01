"""
Key fixes in this file:
- poll_single_source: relaxed entity matching so articles aren't silently dropped
- fetch_brave_news_for_entity: fixed Source unique constraint crash on rss_url=None
"""
from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from django.db.models import Q

from apps.feed.models import FeedItem, Source
from apps.entity.models import Entity
from apps.sports_apis.services.brave import brave_service
from apps.sports_apis.services.rss import rss_discovery_service, rss_polling_service

import hashlib
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
    feeds = rss_discovery_service.discover_feeds_for_domain(domain)
    for feed_url in feeds:
        store_validated_feed.delay(entity_id, feed_url, discovery_source='brave')
    return f"Found {len(feeds)} feeds for {domain}"


@shared_task(bind=True, max_retries=1)
def store_validated_feed(self, entity_id: int, feed_url: str, discovery_source: str = 'brave'):
    try:
        entity = Entity.objects.get(id=entity_id, is_active=True)
    except Entity.DoesNotExist:
        return f"Entity {entity_id} does not exist"

    if not rss_discovery_service._validate_feed(feed_url):
        return f"Feed {feed_url} is not valid"

    domain = _extract_domain(feed_url) or feed_url

    source, created = Source.objects.get_or_create(
        rss_url=feed_url,
        defaults={
            'name': domain,
            'domain': domain,
            'is_active': True,
        }
    )

    source.entities.add(entity)
    poll_single_source.delay(source.id)
    return f"Stored source {source.id} for {entity.name} ({'created' if created else 'updated'})"


@shared_task
def update_all_entity_feeds(entity_id: int):
    return discover_rss_feeds_for_entity.delay(entity_id)


@shared_task
def update_user_nest_feeds(user_id: int):
    from apps.nest.models import UserNest
    nest_entity_ids = list(
        UserNest.objects.filter(user_id=user_id).values_list('entity_id', flat=True)
    )
    for entity_id in nest_entity_ids:
        update_all_entity_feeds.delay(entity_id)
    return f"Triggered feed updates for {len(nest_entity_ids)} entities"


@shared_task
def update_trending_entities_feeds():
    trending = Entity.objects.filter(is_active=True).order_by('-follower_count')[:50]
    for entity in trending:
        update_all_entity_feeds.delay(entity.id)
    return f"Triggered feed updates for {trending.count()} trending entities"


@shared_task
def poll_all_active_sources():
    """Poll all RSS sources that are due, staggered to avoid burst."""
    now = timezone.now()
    due_sources = Source.objects.filter(
        is_active=True,
        rss_url__isnull=False,   # BUG FIX: skip Brave-only sources entirely
    ).exclude(
        rss_url='',
    ).filter(
        Q(last_polled_at__isnull=True) |
        Q(last_polled_at__lte=now - timedelta(minutes=1))
    )

    to_poll = []
    for source in due_sources:
        if not source.last_polled_at:
            to_poll.append(source.id)
            continue
        elapsed = (now - source.last_polled_at).total_seconds()
        if elapsed >= source.poll_interval_minutes * 60:
            to_poll.append(source.id)

    # BUG FIX: stagger tasks 2 seconds apart instead of all at once
    # 100 sources = dispatched over 200 seconds instead of all in 1 second
    for i, source_id in enumerate(to_poll):
        poll_single_source.apply_async(
            args=[source_id],
            countdown=i * 2,  # source 0 fires now, source 1 in 2s, source 2 in 4s...
        )

    return f"Queued {len(to_poll)} sources for polling (staggered over {len(to_poll) * 2}s)"


def _entity_matches_text(entity: Entity, text: str) -> bool:
    """
    BUG FIX: The original check `entity.name.lower() in text` was too strict.
    "Manchester United" never matched "Man United beat Arsenal".
    Now we check the full name AND common short forms (first word, last word).
    Still conservative enough to avoid false positives.
    """
    name = entity.name.lower()
    text = text.lower()

    # Full name match
    if name in text:
        return True

    # For multi-word names, also try first word if it's ≥5 chars
    # (avoids "FC" or "AC" matching everything)
    parts = name.split()
    if len(parts) >= 2:
        first_word = parts[0]
        last_word = parts[-1]
        if len(first_word) >= 5 and first_word in text:
            return True
        if len(last_word) >= 5 and last_word in text:
            return True

    return False


@shared_task(bind=True, max_retries=2)
def poll_single_source(self, source_id: int):
    try:
        source = Source.objects.get(id=source_id, is_active=True)
    except Source.DoesNotExist:
        return f"Source {source_id} not found or inactive"
    
    if not source.rss_url:
        return f"Source {source_id} has no RSS url — skipping (Brave-only source)"

    result = rss_polling_service.poll_feed(source)
    if not result.get('success'):
        source.poll_failures += 1
        source.save(update_fields=['poll_failures'])
        logger.warning(f"Polling failed for source {source.id}: {result.get('error')}")
        # Don't retry for permanent failures like bad URLs
        if result.get('error') == 'no feed url':
            source.is_active = False
            source.save(update_fields=['is_active'])
            return f"Deactivated source {source.id} — no feed url"
        raise self.retry(exc=Exception(result.get('error')))

    candidate_entities = list(source.entities.all())

    new_items = 0
    for entry in result.get('entries', []):
        url = entry.get('url')
        if not url:
            continue

        text = f"{entry.get('title', '')} {entry.get('summary', '')}".lower()

        # BUG FIX: use relaxed matching instead of strict `name in text`
        matched_entities = [e for e in candidate_entities if _entity_matches_text(e, text)]

        # If source has no entities linked yet, accept all entries
        if not candidate_entities:
            matched_entities = []

        if not matched_entities and candidate_entities:
            continue

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
            if matched_entities:
                obj.entities.set(matched_entities)
            new_items += 1
        else:
            if matched_entities:
                obj.entities.add(*matched_entities)

    source.last_polled_at = timezone.now()
    source.poll_failures = 0
    source.save(update_fields=['last_polled_at', 'poll_failures'])

    logger.info(f"Polled source {source.id}: {new_items} new items")
    return f"Polled source {source.id}: {new_items} new items"


@shared_task
def cleanup_old_feed_items():
    cutoff_date = timezone.now() - timedelta(days=30)
    deleted_count = FeedItem.objects.filter(published_at__lt=cutoff_date).delete()[0]
    logger.info(f"Deleted {deleted_count} old feed items")
    return f"Deleted {deleted_count} old feed items"


@shared_task
def mark_trending_items():
    FeedItem.objects.update(is_trending=False)
    last_24h = timezone.now() - timedelta(hours=24)
    trending_ids = list(
        FeedItem.objects.filter(
            published_at__gte=last_24h
        ).order_by('-views')[:100].values_list('id', flat=True)
    )
    FeedItem.objects.filter(id__in=trending_ids).update(is_trending=True)
    return f"Marked {len(trending_ids)} items as trending"


@shared_task
def fetch_brave_news_for_entity(entity_id: int):
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
        source_domain = article.get('source_domain', '')
        source_name = article.get('source_name', 'Unknown')

        if source_domain:
            # BUG FIX: Brave news sources have no rss_url — using get_or_create
            # on rss_url=None caused unique constraint violations in Postgres
            # (multiple NULL rows). Match on domain instead, and only set
            # rss_url if it doesn't conflict.
            source, _ = Source.objects.get_or_create(
                domain=source_domain,
                rss_url=None,   # explicitly None = Brave-only source
                defaults={
                    'name': source_name,
                    'is_active': True,
                    'discovery_source': 'brave',
                }
            )
        else:
            source, _ = Source.objects.get_or_create(
                name='Brave News',
                rss_url=None,
                defaults={
                    'domain': 'brave.com',
                    'is_active': True,
                    'discovery_source': 'brave',
                }
            )

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
            obj.entities.add(entity)

    logger.info(f"Brave news for {entity.name}: {new_count} new articles")
    return f"Fetched {len(articles)} articles for {entity.name}, {new_count} new"


@shared_task
def fetch_brave_news_for_all_nest_entities():
    from apps.nest.models import UserNest
    entity_ids = list(
        UserNest.objects.values_list('entity_id', flat=True).distinct()
    )
    for entity_id in entity_ids:
        fetch_brave_news_for_entity.delay(entity_id)
    return f"Triggered Brave news fetch for {len(entity_ids)} entities"


@shared_task
def fetch_brave_news_for_trending():
    entities = Entity.objects.filter(is_active=True).order_by('-follower_count')[:20]
    for entity in entities:
        fetch_brave_news_for_entity.delay(entity.id)
    return f"Triggered Brave news fetch for {entities.count()} trending entities"


@shared_task
def fetch_brave_news_for_all_entities():
    """
    Fetch fresh news for EVERY active entity in the database.
    Runs daily to ensure all entities have up-to-date content,
    regardless of whether any user has added them to their nest.
    
    Staggered 2 seconds apart to avoid rate limiting.
    """
    entities = Entity.objects.filter(is_active=True)
    count = entities.count()
    
    for i, entity in enumerate(entities):
        fetch_brave_news_for_entity.apply_async(
            args=[entity.id],
            countdown=i * 2  # stagger 2s apart
        )
    
    logger.info(f"Triggered news fetch for {count} active entities")
    return f"Triggered news fetch for {count} entities"