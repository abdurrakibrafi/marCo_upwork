"""Notification Celery background tasks package.

Provides modular background tasks for mobile push notifications, transactional emails,
periodic sports digests, and match event alerts. Re-exports all tasks to ensure
100% backward compatibility with existing imports and Celery Beat schedules.
"""

from .push import (
    send_push_notification_task,
    send_bulk_push_notification_task,
)
from .emails import (
    send_notification_email_task,
    send_welcome_email_task,
)
from .digests import (
    send_daily_digest_task,
    send_weekly_fan_report_task,
)
from .matches import (
    send_upcoming_match_reminders_task,
    send_match_result_alert_task,
)
from .streaks import (
    send_daily_streak_reminder_task,
)

__all__ = [
    # FCM Push
    "send_push_notification_task",
    "send_bulk_push_notification_task",
    # Transactional Emails
    "send_notification_email_task",
    "send_welcome_email_task",
    # Digests & Summaries
    "send_daily_digest_task",
    "send_weekly_fan_report_task",
    # Match Alerts & Reminders
    "send_upcoming_match_reminders_task",
    "send_match_result_alert_task",
    # Streaks
    "send_daily_streak_reminder_task",
]
