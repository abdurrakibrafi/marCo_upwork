from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.nest.models import UserNest


@receiver(post_save, sender=UserNest)
def on_entity_added_to_nest(sender, instance, created, **kwargs):
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
