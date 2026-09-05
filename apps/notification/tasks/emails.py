import logging
from celery import shared_task
from django.contrib.auth import get_user_model

from apps.notification.models import Notification

logger = logging.getLogger(__name__)


@shared_task(name="apps.notification.tasks.send_notification_email_task")
def send_notification_email_task(notification_id: int):
    """Celery task to dispatch an in-app notification to the recipient's email address."""
    from apps.notification.email_service import EmailService

    try:
        notification = Notification.objects.select_related(
            "recipient", "recipient__notification_preferences"
        ).get(id=notification_id)
    except Notification.DoesNotExist:
        logger.warning("Notification %s not found for email task.", notification_id)
        return

    pref = getattr(notification.recipient, "notification_preferences", None)
    if pref and not pref.email_enabled:
        logger.debug("Email notifications disabled for user %s.", notification.recipient.id)
        return

    if pref and not pref.is_type_allowed(notification.notification_type):
        logger.debug(
            "Notification type %s not allowed for user %s.",
            notification.notification_type,
            notification.recipient.id,
        )
        return

    EmailService.send_notification_email(notification)


@shared_task(name="apps.notification.tasks.send_welcome_email_task")
def send_welcome_email_task(user_id: int):
    """Celery task to send onboarding welcome email to a newly verified user."""
    from apps.notification.email_service import EmailService

    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.warning("User %s not found for welcome email task.", user_id)
        return

    EmailService.send_welcome_email(user)
