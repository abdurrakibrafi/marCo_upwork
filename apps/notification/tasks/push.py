import logging
from celery import shared_task

from apps.notification.models import (
    DeviceToken,
    Notification,
    NotificationPreference,
)
from apps.notification.services import FCMService

logger = logging.getLogger(__name__)


@shared_task(name="apps.notification.tasks.send_push_notification_task")
def send_push_notification_task(notification_id: int):
    """Celery task to send FCM push notification for a single notification."""
    try:
        notification = Notification.objects.select_related("recipient").get(id=notification_id)
    except Notification.DoesNotExist:
        logger.warning("Notification %s not found for push task.", notification_id)
        return

    tokens = list(
        DeviceToken.objects.filter(
            user=notification.recipient,
            is_active=True,
        ).values_list("token", flat=True)
    )

    if not tokens:
        logger.debug("No active device tokens for user %s.", notification.recipient.id)
        return

    FCMService.send_multicast(
        tokens=tokens,
        title=notification.title,
        body=notification.body,
        data=notification.data,
        image_url=notification.image_url,
    )


@shared_task(name="apps.notification.tasks.send_bulk_push_notification_task")
def send_bulk_push_notification_task(
    user_ids: list,
    title: str,
    body: str,
    notification_type: str = "general",
    data: dict = None,
    image_url: str = None,
):
    """Celery task to send bulk push notifications to allowed users."""
    if not user_ids:
        return

    # Filter out users who disabled this notification type
    pref_filter = {"push_enabled": True}
    if notification_type in ["match_reminder"]:
        pref_filter["match_reminders"] = True
    elif notification_type in ["score_update"]:
        pref_filter["score_updates"] = True
    elif notification_type in ["breaking_news", "news"]:
        pref_filter["news_alerts"] = True
    elif notification_type in ["nest_interaction", "community"]:
        pref_filter["community_activity"] = True
    elif notification_type in ["streak_reminder", "streak"]:
        pref_filter["streak_reminders"] = True

    allowed_user_ids = set(
        NotificationPreference.objects.filter(
            user_id__in=user_ids,
            **pref_filter,
        ).values_list("user_id", flat=True)
    )

    # Fetch active tokens
    tokens = list(
        DeviceToken.objects.filter(
            user_id__in=allowed_user_ids,
            is_active=True,
        ).values_list("token", flat=True)
    )

    if tokens:
        FCMService.send_multicast(
            tokens=tokens,
            title=title,
            body=body,
            data=data or {},
            image_url=image_url,
        )
