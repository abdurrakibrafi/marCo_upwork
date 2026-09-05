import hashlib
import logging
import urllib.parse
from datetime import timedelta
from celery import shared_task
from django.utils import timezone
from django.db.models import Q

from apps.feed.models import FeedItem, Source
from apps.entity.models import Entity
from apps.sports_apis.services.rss import rss_polling_service
from .helpers import (
    _strip_html,
    _extract_publisher,
    _resolve_thumbnail_for_article,
    _entity_matches_text,
)

logger = logging.getLogger(__name__)


@shared_task(name='apps.feed.tasks.update_all_entity_feeds')
def update_all_entity_feeds(entity_id: int):
    """Trigger asynchronous RSS feed discovery task for a specific entity.

    Args:
        entity_id (int): Primary key of the Entity.
    """
    from .discovery import discover_rss_feeds_for_entity
    return discover_rss_feeds_for_entity.delay(entity_id)


@shared_task(name='apps.feed.tasks.update_user_nest_feeds')
def update_user_nest_feeds(user_id: int):
    """Trigger feed updates for all entities saved in a specific user's Nest.

    Args:
        user_id (int): Primary key of the User.

    Returns:
        str: Task dispatch summary.
    """
    from apps.nest.models import UserNest
    nest_entity_ids = list(
        UserNest.objects.filter(user_id=user_id).values_list('entity_id', flat=True)
    )
    for entity_id in nest_entity_ids:
        update_all_entity_feeds.delay(entity_id)
    return f"Triggered feed updates for {len(nest_entity_ids)} entities"


@shared_task(name='apps.feed.tasks.update_trending_entities_feeds')
def update_trending_entities_feeds():
    """Trigger feed discovery and polling for the top 50 followed entities.

    Returns:
        str: Task dispatch summary.
    """
    trending = Entity.objects.filter(is_active=True).order_by('-follower_count')[:50]
    for entity in trending:
        update_all_entity_feeds.delay(entity.id)
    return f"Triggered feed updates for {trending.count()} trending entities"


@shared_task(name='apps.feed.tasks.poll_all_active_sources')
def poll_all_active_sources():
    """Poll all active RSS sources that are due based on their individual polling intervals.

    Staggers polling tasks to prevent CPU and network spikes.

    Returns:
        str: Polling queue execution report.
    """
    now = timezone.now()
    due_sources = Source.objects.filter(
        is_active=True,
        rss_url__isnull=False,
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

    # Process maximum 20 sources per run, staggered 3s apart to avoid worker CPU spikes
    batch = to_poll[:20]
    for i, source_id in enumerate(batch):
        poll_single_source.apply_async(
            args=[source_id],
            countdown=i * 3,
        )

    return f"Queued {len(batch)} of {len(to_poll)} due sources for polling (staggered over {len(batch) * 3}s)"


@shared_task(name='apps.feed.tasks.poll_single_source', bind=True, max_retries=2)
def poll_single_source(self, source_id: int):
    """Fetch and parse new RSS articles from a single configured Source feed.

    Extracts publishers, matches articles against candidate entities, and persists unique items.

    Args:
        self: Bound Celery task.
        source_id (int): Primary key of the Source.

    Returns:
        str: Polling status and count of newly ingested items.
    """
    try:
        source = Source.objects.get(id=source_id, is_active=True)
    except Source.DoesNotExist:
        return f"Source {source_id} not found or inactive"

    if not source.rss_url:
        return f"Source {source_id} has no RSS url — skipping (Brave-only source)"

    # Auto-fix old YouTube RSS query URLs on the fly
    if source.domain == 'youtube.com' or 'site:youtube.com' in (source.rss_url or ''):
        entity = source.entities.first()
        entity_name = entity.name if entity else source.name.replace('YouTube Video - ', '').strip()
        if entity_name:
            from apps.entity.utils.matcher import is_national_team
            sport_term = (getattr(entity, 'sport', '') or '').strip().lower() if entity else ''
            if entity and is_national_team(entity_name) and sport_term and sport_term != 'none':
                if sport_term == 'soccer':
                    sport_term = 'soccer OR football'
                elif sport_term in ('football', 'american_football', 'nfl'):
                    sport_term = 'nfl OR "american football"'
                elif sport_term in ('basketball', 'nba'):
                    sport_term = 'nba OR basketball'
                elif sport_term in ('baseball', 'mlb'):
                    sport_term = 'mlb OR baseball'
                elif sport_term in ('hockey', 'ice_hockey', 'nhl'):
                    sport_term = 'nhl OR hockey'
                clean_query = urllib.parse.quote(f'"{entity_name}" AND ({sport_term}) site:youtube.com')
            else:
                clean_query = urllib.parse.quote(f'"{entity_name}" site:youtube.com')
            clean_url = f"https://news.google.com/rss/search?q={clean_query}&hl=en&gl=US&ceid=US:en"
            if source.rss_url != clean_url:
                source.rss_url = clean_url
                source.poll_failures = 0
                source.save(update_fields=['rss_url', 'poll_failures'])

    result = rss_polling_service.poll_feed(source)
    if not result.get('success'):
        source.poll_failures += 1
        source.last_polled_at = timezone.now()
        if source.poll_failures >= 3:
            source.is_active = False
            source.save(update_fields=['poll_failures', 'is_active', 'last_polled_at'])
            logger.warning(f"Source {source.id} ({source.name}) deactivated after 3 consecutive failures: {result.get('error')}")
        else:
            source.save(update_fields=['poll_failures', 'last_polled_at'])
            logger.warning(f"Polling failed for source {source.id} (attempt {source.poll_failures}/3): {result.get('error')}")
        return f"Polling failed for source {source.id}: {result.get('error')}"

    candidate_entities = list(source.entities.all())
    is_global_source = not candidate_entities
    if is_global_source:
        candidate_entities = list(Entity.objects.filter(is_active=True, follower_count__gt=0)[:300]) or list(Entity.objects.filter(is_active=True)[:100])

    from apps.feed.utils_url import resolve_real_article_url

    new_items = 0
    entries = result.get('entries', [])[:30]

    for entry in entries:
        raw_url = entry.get('url')
        if not raw_url:
            continue

        text = f"{entry.get('title', '')} {entry.get('summary', '')}".lower()

        matched_entities = [e for e in candidate_entities if _entity_matches_text(e, text)]

        if not matched_entities:
            if not is_global_source and candidate_entities:
                from apps.entity.utils.matcher import is_national_team
                valid_candidates = [
                    e for e in candidate_entities
                    if not is_national_team(e.name)
                ]
                if valid_candidates:
                    matched_entities = valid_candidates
                else:
                    continue
            else:
                continue

        url = resolve_real_article_url(raw_url)
        url_hash = hashlib.md5(url.encode()).hexdigest()

        existing_item = FeedItem.objects.filter(url_hash=url_hash).first()
        if existing_item:
            if matched_entities:
                existing_item.entities.add(*matched_entities)
            continue

        thumbnail_url = entry.get('thumbnail_url', '')
        if not thumbnail_url:
            thumbnail_url = _resolve_thumbnail_for_article(
                title=entry.get('title', ''),
                entities=matched_entities
            )

        obj, created = FeedItem.objects.get_or_create(
            url_hash=url_hash,
            defaults={
                'source': source,
                'title': entry.get('title', '')[:500],
                'url': url,
                'summary': _strip_html(entry.get('summary', '')),
                'publisher_name': _extract_publisher(entry.get('summary', '')),
                'thumbnail_url': thumbnail_url,
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
