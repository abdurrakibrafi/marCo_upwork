import logging
from datetime import timedelta
from celery import shared_task
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.event.models import Event
from apps.nest.models import UserNest
from apps.notification.email_service import EmailService
from apps.notification.services import NotificationService

logger = logging.getLogger(__name__)


@shared_task(name="apps.notification.tasks.send_upcoming_match_reminders_task")
def send_upcoming_match_reminders_task():
    """Periodic task running every 30 mins to notify users about followed matches starting in next 2 hours."""
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


@shared_task(name="apps.notification.tasks.send_match_result_alert_task")
def send_match_result_alert_task(event_id: int):
    """Event-driven task triggered strictly when a match transitions to completed.

    Zero polling overhead. Identifies winner, generates instant celebration or final score alert
    for subscribed Nest fans.
    """
    try:
        event = Event.objects.select_related("home_entity", "away_entity", "league").get(id=event_id)
    except Event.DoesNotExist:
        return

    h_score = event.home_score
    a_score = event.away_score

    # Only alert if valid scores are registered
    if h_score is None or a_score is None:
        return

    home_name = event.home_entity.name if event.home_entity else "Home"
    away_name = event.away_entity.name if event.away_entity else "Away"
    league_name = event.league.name if event.league else event.sport.title()

    # Determine winner or draw
    if h_score > a_score:
        winner_id = event.home_entity_id
        winner_name = home_name
        loser_name = away_name
        final_score = f"{h_score}-{a_score}"
        is_draw = False
    elif a_score > h_score:
        winner_id = event.away_entity_id
        winner_name = away_name
        loser_name = home_name
        final_score = f"{a_score}-{h_score}"
        is_draw = False
    else:
        is_draw = True
        final_score = f"{h_score}-{a_score}"

    User = get_user_model()

    if not is_draw and winner_id:
        # Fans following the winning club
        fan_user_ids = set(
            UserNest.objects.filter(
                entity_id=winner_id,
                user__notification_preferences__score_updates=True,
            ).values_list("user_id", flat=True)
        )

        title = f"Victory! {winner_name} won {final_score}"
        body = f"{winner_name} secured a great win against {loser_name} in {league_name}."

        fans = User.objects.filter(id__in=fan_user_ids)
        for fan in fans:
            NotificationService.send(
                user=fan,
                title=title,
                body=body,
                notification_type="score_update",
                data={"event_id": event.id, "route": f"/matches/{event.id}"},
            )
