import logging
from datetime import timedelta
from celery import shared_task
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from apps.event.models import Event
from apps.feed.models import FeedItem
from apps.nest.models import UserNest
from apps.notification.email_service import EmailService
from apps.notification.services import NotificationService

logger = logging.getLogger(__name__)


@shared_task(name="apps.notification.tasks.send_daily_digest_task")
def send_daily_digest_task():
    """Periodic Celery Beat task to compile and send daily sports digest to active users."""
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


@shared_task(name="apps.notification.tasks.send_weekly_fan_report_task")
def send_weekly_fan_report_task():
    """Periodic Celery Beat task running ONCE weekly (Sunday night) to deliver fan performance summaries.

    Uses fast in-memory Python calculations and lightweight ORM queries with zero external AI calls.
    """
    User = get_user_model()
    now = timezone.now()
    seven_days_ago = now - timedelta(days=7)
    next_week = now + timedelta(days=7)

    # Process active users subscribed to email updates
    eligible_users = User.objects.filter(
        is_active=True,
        notification_preferences__email_enabled=True,
    ).distinct()

    for user in eligible_users:
        user_entity_ids = list(
            UserNest.objects.filter(user=user).values_list("entity_id", flat=True)
        )
        if not user_entity_ids:
            continue

        # Completed matches involving user's followed teams in past 7 days
        completed_events = (
            Event.objects.filter(
                status="completed",
                start_time__gte=seven_days_ago,
            )
            .filter(
                Q(home_entity_id__in=user_entity_ids)
                | Q(away_entity_id__in=user_entity_ids)
            )
            .select_related("home_entity", "away_entity", "league")
            .order_by("-start_time")
        )

        total_wins = 0
        total_draws = 0
        total_losses = 0
        team_results = []

        for ev in completed_events:
            h_score = ev.home_score if ev.home_score is not None else 0
            a_score = ev.away_score if ev.away_score is not None else 0

            # Determine outcome relative to user's followed team
            is_home_fan = ev.home_entity_id in user_entity_ids
            is_away_fan = ev.away_entity_id in user_entity_ids

            if h_score == a_score:
                outcome = "DRAW"
                total_draws += 1
            elif (h_score > a_score and is_home_fan) or (a_score > h_score and is_away_fan and not is_home_fan):
                outcome = "WIN"
                total_wins += 1
            else:
                outcome = "LOSS"
                total_losses += 1

            team_results.append({
                "home_team": ev.home_entity.name if ev.home_entity else "Home",
                "away_team": ev.away_entity.name if ev.away_entity else "Away",
                "home_score": h_score,
                "away_score": a_score,
                "league_name": ev.league.name if ev.league else ev.sport.title(),
                "date": ev.start_time.strftime("%b %d"),
                "outcome": outcome,
            })

        # Skip users with no sports activity in the week
        if not team_results:
            continue

        # Pure Python dynamic summary phrasing (no AI latency)
        if total_wins > 0 and total_losses == 0:
            summary_text = f"An undefeated week! Your followed teams achieved {total_wins} win(s) with 0 losses."
        elif total_wins >= total_losses:
            summary_text = f"A strong winning week with {total_wins} win(s) across your followed clubs."
        else:
            summary_text = f"A challenging matchweek with {total_wins} win(s) and {total_losses} defeat(s). Big opportunities ahead next week!"

        # Top 2 headlines
        top_articles_qs = (
            FeedItem.objects.filter(
                entities__id__in=user_entity_ids,
                published_at__gte=seven_days_ago,
            )
            .distinct()
            .order_by("-views", "-published_at")[:2]
        )
        top_articles = [
            {"title": item.title, "summary": item.summary}
            for item in top_articles_qs
        ]

        # Next week preview
        upcoming_qs = (
            Event.objects.filter(
                status="upcoming",
                start_time__gte=now,
                start_time__lte=next_week,
            )
            .filter(
                Q(home_entity_id__in=user_entity_ids)
                | Q(away_entity_id__in=user_entity_ids)
            )
            .select_related("home_entity", "away_entity", "league")
            .order_by("start_time")[:3]
        )
        upcoming_matches = [
            {
                "home_team": u.home_entity.name if u.home_entity else "TBD",
                "away_team": u.away_entity.name if u.away_entity else "TBD",
                "league_name": u.league.name if u.league else u.sport.title(),
                "start_time_display": u.start_time.strftime("%a %I:%M %p UTC"),
            }
            for u in upcoming_qs
        ]

        report_data = {
            "total_wins": total_wins,
            "total_draws": total_draws,
            "total_losses": total_losses,
            "summary_text": summary_text,
            "team_results": team_results[:5],
            "top_articles": top_articles,
            "upcoming_matches": upcoming_matches,
        }

        # 1. Send Email Report
        EmailService.send_weekly_fan_report(user, report_data)

        # 2. In-App Notification
        NotificationService.send(
            user=user,
            title=f"📊 Your Weekly Recap: {total_wins}W - {total_losses}L",
            body=summary_text,
            notification_type="general",
            send_email=False,  # Email already sent above
            data={"route": "/notifications"},
        )
