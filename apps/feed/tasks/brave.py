import logging
from celery import shared_task
from apps.entity.models import Entity
from .discovery import discover_rss_feeds_for_entity

logger = logging.getLogger(__name__)


@shared_task(name='apps.feed.tasks.fetch_brave_news_for_entity')
def fetch_brave_news_for_entity(entity_id: int):
    """Trigger Brave RSS feed discovery for an entity if discovery hasn't been performed yet.

    Args:
        entity_id (int): Primary key of the Entity.

    Returns:
        str: Task execution summary message.
    """
    try:
        entity = Entity.objects.get(id=entity_id, is_active=True)
    except Entity.DoesNotExist:
        return f"Entity {entity_id} not found"

    if not entity.rss_discovery_done:
        discover_rss_feeds_for_entity.delay(entity_id)
        return f"Triggered Brave RSS source discovery for {entity.name}"

    return f"Entity {entity.name} already has RSS sources discovered — skipping Brave news search"


@shared_task(name='apps.feed.tasks.fetch_brave_news_for_all_nest_entities')
def fetch_brave_news_for_all_nest_entities():
    """Trigger Brave news source discovery across all unique entities followed in user nests.

    Returns:
        str: Task dispatch summary.
    """
    from apps.nest.models import UserNest
    entity_ids = list(
        UserNest.objects.values_list('entity_id', flat=True).distinct()
    )
    for entity_id in entity_ids:
        fetch_brave_news_for_entity.delay(entity_id)
    return f"Triggered Brave news fetch for {len(entity_ids)} entities"


@shared_task(name='apps.feed.tasks.fetch_brave_news_for_trending')
def fetch_brave_news_for_trending():
    """Trigger Brave news discovery for the top 20 most-followed entities.

    Returns:
        str: Task dispatch summary.
    """
    entities = Entity.objects.filter(is_active=True).order_by('-follower_count')[:20]
    for entity in entities:
        fetch_brave_news_for_entity.delay(entity.id)
    return f"Triggered Brave news fetch for {entities.count()} trending entities"


@shared_task(name='apps.feed.tasks.fetch_brave_news_for_all_entities')
def fetch_brave_news_for_all_entities():
    """Staggered trigger of Brave news discovery for all active entities with followers.

    Returns:
        str: Task dispatch summary.
    """
    entities = Entity.objects.filter(is_active=True, follower_count__gt=0)
    count = entities.count()

    for i, entity in enumerate(entities):
        fetch_brave_news_for_entity.apply_async(
            args=[entity.id],
            countdown=i * 2
        )

    logger.info(f"Triggered news fetch for {count} followed active entities")
    return f"Triggered news fetch for {count} entities"
