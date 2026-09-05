import logging
from celery import shared_task
from django.contrib.auth import get_user_model
from django.utils import timezone

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

    from apps.notification.services import NotificationService

    for user in users_to_remind:
        streak_count = getattr(user.streak, "current_streak", 1)
        NotificationService.send(
            user=user,
            title="🔥 Keep Your Streak Alive!",
            body=f"You're on a {streak_count}-day streak! Check out today's top matches and news now.",
            notification_type="streak_reminder",
            data={"route": "/home", "streak_count": streak_count},
        )


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


@shared_task(name="apps.notification.tasks.send_daily_digest_task")
def send_daily_digest_task():
    """Periodic Celery Beat task to compile and send daily sports digest to active users."""
    from datetime import timedelta
    from django.db.models import Q
    from apps.notification.email_service import EmailService
    from apps.nest.models import UserNest
    from apps.feed.models import FeedItem
    from apps.event.models import Event

    User = get_user_model()
    now = timezone.now()
    since_yesterday = now - timedelta(hours=24)
    end_of_today = now.replace(hour=23, minute=59, second=59)

    # Eligible users with active email preference
    eligible_users = User.objects.filter(
        is_active=True,
        notification_preferences__email_enabled=True,
        notification_preferences__news_alerts=True,
    ).distinct()

    for user in eligible_users:
        user_entity_ids = list(
            UserNest.objects.filter(user=user).values_list("entity_id", flat=True)
        )
        if not user_entity_ids:
            continue

        # Top articles from followed entities
        articles_qs = (
            FeedItem.objects.filter(
                entities__id__in=user_entity_ids,
                published_at__gte=since_yesterday,
            )
            .distinct()
            .order_by("-published_at")[:5]
        )

        articles = [
            {
                "title": item.title,
                "url": item.url,
                "summary": item.summary,
                "publisher_name": item.publisher_name or (item.source.name if item.source else ""),
                "published_at": item.published_at,
            }
            for item in articles_qs
        ]

        # Upcoming matches today for followed entities
        upcoming_events_qs = (
            Event.objects.filter(
                status="upcoming",
                start_time__gte=now,
                start_time__lte=end_of_today,
            )
            .filter(
                Q(home_entity_id__in=user_entity_ids)
                | Q(away_entity_id__in=user_entity_ids)
            )
            .select_related("home_entity", "away_entity", "league")
            .order_by("start_time")[:3]
        )

        upcoming_events = [
            {
                "home_team": ev.home_entity.name if ev.home_entity else "TBD",
                "away_team": ev.away_entity.name if ev.away_entity else "TBD",
                "league_name": ev.league.name if ev.league else ev.sport.title(),
                "start_time_display": ev.start_time.strftime("%I:%M %p UTC"),
            }
            for ev in upcoming_events_qs
        ]

        if articles or upcoming_events:
            EmailService.send_daily_digest(
                user=user,
                articles=articles,
                upcoming_events=upcoming_events,
            )


@shared_task(name="apps.notification.tasks.send_upcoming_match_reminders_task")
def send_upcoming_match_reminders_task():
    """Periodic task running every 30 mins to notify users about followed matches starting in next 2 hours."""
    from datetime import timedelta
    from apps.notification.email_service import EmailService
    from apps.nest.models import UserNest
    from apps.event.models import Event

    now = timezone.now()
    window_end = now + timedelta(hours=2)

    upcoming_events = (
        Event.objects.filter(
            status="upcoming",
            start_time__gte=now,
            start_time__lte=window_end,
        )
        .select_related("home_entity", "away_entity", "league")
    )

    if not upcoming_events.exists():
        return

    User = get_user_model()

    for event in upcoming_events:
        team_ids = [tid for tid in [event.home_entity_id, event.away_entity_id] if tid]
        if not team_ids:
            continue

        subscribed_user_ids = set(
            UserNest.objects.filter(
                entity_id__in=team_ids,
                user__notification_preferences__email_enabled=True,
                user__notification_preferences__match_reminders=True,
            ).values_list("user_id", flat=True)
        )

        if not subscribed_user_ids:
            continue

        users = User.objects.filter(id__in=subscribed_user_ids)
        event_data = {
            "home_team": event.home_entity.name if event.home_entity else "Home Team",
            "away_team": event.away_entity.name if event.away_entity else "Away Team",
            "league_name": event.league.name if event.league else event.sport.title(),
            "start_time_display": event.start_time.strftime("%I:%M %p UTC"),
            "venue": event.venue_name,
            "match_url": f"{EmailService.get_site_url()}/matches/{event.id}",
        }

        for user in users:
            EmailService.send_match_reminder(user, event_data)
