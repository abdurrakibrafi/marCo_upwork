import logging
from datetime import timedelta
from celery import shared_task
from django.utils import timezone

from apps.feed.models import FeedItem
from apps.entity.models import Entity
from .helpers import _entity_matches_text

logger = logging.getLogger(__name__)


@shared_task(name='apps.feed.tasks.cleanup_old_feed_items')
def cleanup_old_feed_items():
    """Delete feed articles published more than 30 days ago to conserve database storage.

    Returns:
        str: Deletion count report.
    """
    cutoff_date = timezone.now() - timedelta(days=30)
    deleted_count = FeedItem.objects.filter(published_at__lt=cutoff_date).delete()[0]
    logger.info(f"Deleted {deleted_count} old feed items")
    return f"Deleted {deleted_count} old feed items"


@shared_task(name='apps.feed.tasks.mark_trending_items')
def mark_trending_items():
    """Recalculate trending status on recent articles based on rolling 24-hour page views.

    Returns:
        str: Count of items marked trending.
    """
    FeedItem.objects.update(is_trending=False)
    last_24h = timezone.now() - timedelta(hours=24)
    trending_ids = list(
        FeedItem.objects.filter(
            published_at__gte=last_24h
        ).order_by('-views')[:100].values_list('id', flat=True)
    )
    FeedItem.objects.filter(id__in=trending_ids).update(is_trending=True)
    return f"Marked {len(trending_ids)} items as trending"


@shared_task(name='apps.feed.tasks.cleanup_non_sports_national_team_items')
def cleanup_non_sports_national_team_items():
    """Detach non-sports articles and videos from national team entities that were erroneously linked."""
    from apps.entity.utils.matcher import is_national_team

    national_teams = Entity.objects.filter(is_active=True, type='team')
    target_teams = [e for e in national_teams if is_national_team(e.name)]

    detached_count = 0
    for team in target_teams:
        items = FeedItem.objects.filter(entities=team)
        for item in items.iterator():
            text = f"{item.title} {item.summary or ''}".lower()
            if not _entity_matches_text(team, text):
                item.entities.remove(team)
                detached_count += 1

    logger.info(f"Cleaned up {detached_count} non-sports items from national teams")
    return f"Cleaned up {detached_count} non-sports items from national teams"
