import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('config')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks(lambda: [
    'apps.event',
    'apps.sports_apis',
    'apps.feed',
    'apps.entity',
    'apps.score',
    'apps.source',
    'apps.notification',
])

app.conf.beat_schedule = {
    # ── StatPal live scores (all sports) ─────────────────────────────────
    'sync-statpal-data-every-minute': {
        'task': 'apps.event.tasks.sync_statpal_data',
        'schedule': 60.0,
    },
    'sync-statpal-fixtures-every-6-hours': {
        'task': 'apps.event.tasks.sync_statpal_fixtures_data',
        'schedule': crontab(hour='*/6', minute=15),
    },
    # TheSportsDB: fills long-range gap (StatPal only supports ±7 days)
    # Runs daily at 7am — fetches soccer fixtures for next 30 days
    'sync-thesportsdb-upcoming-fixtures-daily': {
        'task': 'apps.event.tasks.sync_thesportsdb_upcoming_fixtures',
        'schedule': crontab(hour=7, minute=0),
    },


    # ── Fixtures ─────────────────────────────────────────────────────────
    'fixtures-daily': {
        'task': 'apps.event.tasks.update_all_fixtures',
        'schedule': crontab(hour=6, minute=0),
    },

    # ── Backup check for completed events (runs hourly, checks each match strictly ONCE) ──
    'check-completed-events-backup': {
        'task': 'apps.event.tasks.check_completed_events',
        'schedule': 3600.0,  # once every hour
    },

    # ── RSS / news ────────────────────────────────────────────────────────
    'poll-rss-sources': {
        'task': 'apps.feed.tasks.poll_all_active_sources',
        'schedule': 900.0,  # every 15 minutes
    },

    # ── Bootstrap (monthly) ────────────────────────────────────────────
    'bootstrap-all-entities': {
        'task': 'apps.entity.tasks.bootstrap_all_entities',
        'schedule': crontab(hour=3, minute=0, day_of_week=0, day_of_month=1),  # 1st Sunday
    },

    # ── Cleanup / trending ────────────────────────────────────────────────
    'cleanup-feeds-4am': {
        'task': 'apps.feed.tasks.cleanup_old_feed_items',
        'schedule': crontab(hour=4, minute=0),
    },
    # Venue cache pre-warm: populate venue name/city for all teams so API never blocks
    'warm-all-venue-caches-daily': {
        'task': 'apps.entity.tasks.warm_all_venue_caches',
        'schedule': crontab(hour=3, minute=30),  # 3:30am daily
    },
    'mark-trending-hourly': {
        'task': 'apps.feed.tasks.mark_trending_items',
        'schedule': crontab(minute=30),
    },
    'cleanup-stale-live-events': {
        'task': 'apps.event.tasks.cleanup_stale_live_events',
        'schedule': crontab(minute=0),  # every hour
    },

    # ── Stats & Standings (Every 2 hours) ───────────────────────────────
    'team-stats-every-2-hours': {
        'task': 'apps.entity.tasks.update_all_team_stats',
        'schedule': crontab(hour='*/2', minute=0),  # Runs every 2 hours to keep standings fresh
    },

    # ── Logos + highlights ───────────────────────────────────────────────
    'enrich-logos-daily': {
        'task': 'apps.sports_apis.tasks.enrich_missing_logos',
        'schedule': crontab(hour=2, minute=0),  # Daily at 2am
    },
    'enrich-highlights-daily': {
        'task': 'apps.sports_apis.tasks.enrich_event_highlights_today',
        'schedule': crontab(hour=23, minute=30),  # 11:30pm daily
    },
    'fetch-highlights-recently-completed': {
        'task': 'apps.sports_apis.tasks.fetch_highlights_for_recently_completed_events',
        'schedule': 7200.0,  # every 2 hours
    },
    # ── Weekly In-Season Roster Backfills (3-7 days cadence) ────────────
    # Each runs weekly at staggered times during active competition seasons.
    # Tasks include built-in active-season event guards to skip off-season cycles.

    # NBA Basketball — every Tuesday at 4:00 AM
    'backfill-basketball-players-weekly': {
        'task': 'apps.sports_apis.tasks.backfill_basketball_players_task',
        'schedule': crontab(hour=4, minute=0, day_of_week=2),
    },
    # MLB & NHL — every Thursday at 4:30 AM
    'backfill-mlb-nhl-rosters-weekly': {
        'task': 'apps.sports_apis.tasks.backfill_mlb_nhl_rosters_task',
        'schedule': crontab(hour=4, minute=30, day_of_week=4),
    },
    # Soccer — every Wednesday at 4:00 AM
    'backfill-soccer-players-weekly': {
        'task': 'apps.sports_apis.tasks.backfill_soccer_players_task',
        'schedule': crontab(hour=4, minute=0, day_of_week=3),
    },
    # Cricket — every Friday at 5:00 AM
    'backfill-cricket-players-weekly': {
        'task': 'apps.sports_apis.tasks.backfill_cricket_players_task',
        'schedule': crontab(hour=5, minute=0, day_of_week=5),
    },
    # Tennis — every Monday at 5:30 AM
    'backfill-tennis-players-weekly': {
        'task': 'apps.sports_apis.tasks.backfill_tennis_players_task',
        'schedule': crontab(hour=5, minute=30, day_of_week=1),
    },
    # Golf — every Monday at 5:00 AM
    'backfill-golf-players-weekly': {
        'task': 'apps.sports_apis.tasks.backfill_golf_players_task',
        'schedule': crontab(hour=5, minute=0, day_of_week=1),
    },
    # Handball — every Saturday at 5:00 AM
    'backfill-handball-players-weekly': {
        'task': 'apps.sports_apis.tasks.backfill_handball_players_task',
        'schedule': crontab(hour=5, minute=0, day_of_week=6),
    },
    # Volleyball — every Sunday at 5:30 AM
    'backfill-volleyball-players-weekly': {
        'task': 'apps.sports_apis.tasks.backfill_volleyball_players_task',
        'schedule': crontab(hour=5, minute=30, day_of_week=0),
    },
    # Broken logo cleanup — run monthly on the 1st at 3am
    'cleanup-broken-logos-monthly': {
        'task': 'apps.sports_apis.tasks.cleanup_broken_logos_task',
        'schedule': crontab(hour=3, minute=0, day_of_month=1),
    },
    # ── Email Notifications & Digests ────────────────────────────────────
    'daily-sports-digest-8am': {
        'task': 'apps.notification.tasks.send_daily_digest_task',
        'schedule': crontab(hour=8, minute=0),
    },
    'send-upcoming-match-reminders-30m': {
        'task': 'apps.notification.tasks.send_upcoming_match_reminders_task',
        'schedule': crontab(minute='*/30'),
    },
    'weekly-fan-report-sunday-night': {
        'task': 'apps.notification.tasks.send_weekly_fan_report_task',
        'schedule': crontab(hour=22, minute=0, day_of_week=0),  # Sunday 10:00 PM UTC (once weekly)
    },
}

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
