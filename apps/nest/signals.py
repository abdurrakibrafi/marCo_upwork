from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.cache import cache
from apps.nest.models import UserNest


def _clear_user_nest_feed_cache(user_id):
    """Invalidate nest feed cache keys for this user."""
    try:
        if hasattr(cache, 'delete_pattern'):
            cache.delete_pattern(f"nest_feed:{user_id}:*")
        else:
            for page in range(1, 10):
                for sort in ['newest', 'oldest', 'popular', 'trending', 'least', 'likes', 'most_liked', 'least_liked']:
                    cache.delete(f"nest_feed:{user_id}:p{page}:l10:s{sort}:f:t:src:q")
                    cache.delete(f"nest_feed:{user_id}:p{page}:l20:s{sort}:f:t:src:q")
    except Exception:
        pass


@receiver(post_save, sender=UserNest)
def on_entity_added_to_nest(sender, instance, created, **kwargs):
    _clear_user_nest_feed_cache(instance.user_id)

    if not created:
        return

    entity_id = instance.entity_id

    from apps.feed.tasks import ensure_entity_has_rss_source, discover_rss_feeds_for_entity
    from apps.entity.models import Entity

    ensure_entity_has_rss_source.delay(entity_id)

    try:
        entity = Entity.objects.get(id=entity_id)
        if not entity.rss_discovery_done:
            discover_rss_feeds_for_entity.delay(entity_id)
    except Entity.DoesNotExist:
        pass


@receiver(post_delete, sender=UserNest)
def on_entity_removed_from_nest(sender, instance, **kwargs):
    _clear_user_nest_feed_cache(instance.user_id)
