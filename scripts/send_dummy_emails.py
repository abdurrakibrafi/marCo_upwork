import os
import sys
import django
from datetime import datetime

# Setup django environment
sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from types import SimpleNamespace
from apps.notification.email_service import EmailService

TARGET_EMAIL = "dev.mamun50@gmail.com"
USER_NAME = "Mamun"

print(f"==================================================")
print(f"🚀 Sending Dummy Test Emails to: {TARGET_EMAIL}")
print(f"==================================================")

dummy_user = SimpleNamespace(
    email=TARGET_EMAIL,
    name=USER_NAME,
    username="mamun",
)

# 1. Welcome Email
print("\n[1/4] Sending Welcome Email...")
welcome_ok = EmailService.send_welcome_email(dummy_user)
print(f" -> Welcome Email Status: {'✅ SUCCESS' if welcome_ok else '❌ FAILED'}")

# 2. Breaking News / Notification Email
print("\n[2/4] Sending Breaking News Notification Email...")
dummy_notif = SimpleNamespace(
    recipient=dummy_user,
    notification_type="breaking_news",
    title="Kylian Mbappé scores a sensational hat-trick!",
    body="Real Madrid cruised to a 4-1 victory in an exhilarating Champions League night with Mbappé leading the charge.",
    image_url="https://images.unsplash.com/photo-1508098682722-e99c43a406b2?w=800&auto=format&fit=crop",
    data={"url": "https://marco.sports/feed/breaking-mbappe"},
)
notif_ok = EmailService.send_notification_email(dummy_notif)
print(f" -> Notification Email Status: {'✅ SUCCESS' if notif_ok else '❌ FAILED'}")

# 3. Match Reminder Email
print("\n[3/4] Sending Match Day Reminder Email...")
dummy_event = {
    "home_team": "Real Madrid",
    "away_team": "FC Barcelona",
    "league_name": "La Liga — El Clásico",
    "start_time_display": "Tonight at 8:00 PM UTC",
    "venue": "Santiago Bernabéu, Madrid",
    "match_url": "https://marco.sports/matches/el-clasico",
}
match_ok = EmailService.send_match_reminder(dummy_user, dummy_event)
print(f" -> Match Reminder Status: {'✅ SUCCESS' if match_ok else '❌ FAILED'}")

# 4. Daily Sports Digest Email
print("\n[4/4] Sending Daily Sports Digest Email...")
dummy_articles = [
    {
        "title": "Arsenal eye summer swoop for Bundesliga midfield prodigy",
        "url": "https://marco.sports/news/arsenal-midfield-target",
        "summary": "Mikel Arteta has identified key reinforcements to bolster Arsenal's title charge next season.",
        "publisher_name": "The Athletic",
        "published_at": datetime.now(),
    },
    {
        "title": "Lakers secure clutch overtime victory behind LeBron's triple-double",
        "url": "https://marco.sports/news/lakers-ot-win",
        "summary": "A vintage 32-point performance propelled the Lakers to a crucial western conference win.",
        "publisher_name": "ESPN",
        "published_at": datetime.now(),
    },
    {
        "title": "ICC T20 World Cup: Top 5 performers of the Super 8 round",
        "url": "https://marco.sports/news/t20-world-cup-super8",
        "summary": "Breakdown of the star players making decisive impacts heading into the semi-finals.",
        "publisher_name": "Cricbuzz",
        "published_at": datetime.now(),
    },
]

dummy_upcoming = [
    {
        "home_team": "Manchester City",
        "away_team": "Liverpool",
        "league_name": "Premier League",
        "start_time_display": "5:30 PM UTC",
    },
    {
        "home_team": "Boston Celtics",
        "away_team": "Miami Heat",
        "league_name": "NBA",
        "start_time_display": "11:00 PM UTC",
    },
]

digest_ok = EmailService.send_daily_digest(
    dummy_user,
    articles=dummy_articles,
    upcoming_events=dummy_upcoming,
)
print(f" -> Daily Digest Status: {'✅ SUCCESS' if digest_ok else '❌ FAILED'}")

# 5. Weekly Fan Performance Summary Report
print("\n[5/5] Sending Weekly Fan Performance Report...")
dummy_report_data = {
    "total_wins": 3,
    "total_draws": 1,
    "total_losses": 0,
    "summary_text": "An undefeated week! Your followed teams achieved 3 win(s) with 0 losses.",
    "team_results": [
        {
            "home_team": "Real Madrid",
            "away_team": "Barcelona",
            "home_score": 3,
            "away_score": 1,
            "league_name": "La Liga",
            "date": "Sep 02",
            "outcome": "WIN",
        },
        {
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "home_score": 2,
            "away_score": 0,
            "league_name": "Premier League",
            "date": "Aug 31",
            "outcome": "WIN",
        },
        {
            "home_team": "Liverpool",
            "away_team": "Manchester City",
            "home_score": 2,
            "away_score": 2,
            "league_name": "Premier League",
            "date": "Aug 29",
            "outcome": "DRAW",
        },
    ],
    "top_articles": [
        {
            "title": "Real Madrid extends lead at top of table after Clásico masterclass",
            "summary": "Vinicius Jr and Bellingham starred as Madrid took a 5-point lead in Spain.",
        },
    ],
    "upcoming_matches": [
        {
            "home_team": "Real Madrid",
            "away_team": "Real Sociedad",
            "league_name": "La Liga",
            "start_time_display": "Sat 7:00 PM UTC",
        },
    ],
}
report_ok = EmailService.send_weekly_fan_report(dummy_user, dummy_report_data)
print(f" -> Weekly Fan Report Status: {'✅ SUCCESS' if report_ok else '❌ FAILED'}")

print(f"\n==================================================")
print(f"🏁 Completed Dummy Email Test Batch!")
print(f"==================================================")
