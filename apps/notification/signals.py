from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.notification.models import NotificationPreference


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_notification_preference(sender, instance, created, **kwargs):
    """Ensure every newly created User has default NotificationPreference settings."""
    if created:
        NotificationPreference.objects.get_or_create(user=instance)
