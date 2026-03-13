from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from apps.feed.models import FeedItem
from apps.entity.models import Entity
import logging

logger = logging.getLogger(__name__)


@shared_task
def update_all_entity_feeds(entity_id: int):
    """
    Trigger all feed updates for an entity.
    RSS/Brave pipeline tasks will be chained here once built.
    """
    # TODO: chain RSS discovery + polling tasks here
    return f"Triggered feed updates for entity {entity_id}"


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
def cleanup_old_feed_items():
    """Delete feed items older than 30 days"""
    cutoff_date = timezone.now() - timedelta(days=30)
    deleted_count = FeedItem.objects.filter(published_at__lt=cutoff_date).delete()[0]

    logger.info(f"Deleted {deleted_count} old feed items")
    return f"Deleted {deleted_count} old feed items"


@shared_task
def mark_trending_items():
    """Mark top 100 items from the last 24h as trending"""
    FeedItem.objects.update(is_trending=False)

    last_24h = timezone.now() - timedelta(hours=24)
    trending_items = FeedItem.objects.filter(published_at__gte=last_24h).order_by('-views')[:100]
    trending_items.update(is_trending=True)

    return f"Marked {trending_items.count()} items as trending"