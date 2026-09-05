import logging
from celery import shared_task
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.notification.services import NotificationService

logger = logging.getLogger(__name__)


@shared_task(name="apps.notification.tasks.send_daily_streak_reminder_task")
def send_daily_streak_reminder_task():
    """Periodic task to remind active streak users to maintain their streak today."""
    User = get_user_model()
    today = timezone.now().date()

    # Find users with streak > 0 whose last_active_date was yesterday and haven't logged in today
    users_to_remind = (
        User.objects.filter(
            is_active=True,
            streak__current_streak__gt=0,
            streak__last_active_date__lt=today,
            notification_preferences__streak_reminders=True,
            notification_preferences__push_enabled=True,
        )
        .select_related("streak")
        .distinct()
    )

    for user in users_to_remind:
        streak_count = getattr(user.streak, "current_streak", 1)
        NotificationService.send(
            user=user,
            title="🔥 Keep Your Streak Alive!",
            body=f"You're on a {streak_count}-day streak! Check out today's top matches and news now.",
            notification_type="streak_reminder",
            data={"route": "/home", "streak_count": streak_count},
        )
