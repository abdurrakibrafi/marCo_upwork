import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('config')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
app.autodiscover_tasks(['apps.sports_apis'])

app.conf.beat_schedule = {
    # ── Live scores (keep as is) ─────────────────────────────────────────
    'live-scores-nba': {
        'task': 'apps.sports_apis.tasks.update_nba_live_scores',
        'schedule': 120.0,
    },
    'live-scores-nfl': {
        'task': 'apps.sports_apis.tasks.update_nfl_live_scores',
        'schedule': 120.0,
    },
    'live-scores-soccer': {
        'task': 'apps.sports_apis.tasks.update_soccer_live_scores',
        'schedule': 120.0,
    },
    'live-scores-cricket': {
        'task': 'apps.sports_apis.tasks.update_cricket_live_scores',
        'schedule': 120.0,
    },

    # ── Fixtures ─────────────────────────────────────────────────────────
    'fixtures-daily': {
        'task': 'apps.event.tasks.update_all_fixtures',
        'schedule': crontab(hour=6, minute=0),
    },

    # ── Event detail population ──────────────────────────────────────────
    'check-completed-events': {
        'task': 'apps.event.tasks.check_completed_events',
        'schedule': 300.0,
    },

    # ── RSS / news ────────────────────────────────────────────────────────
    'poll-rss-sources': {
        'task': 'apps.feed.tasks.poll_all_active_sources',
        'schedule': 300.0,
    },
    
    # FIXED: Brave news - ONCE per week for ALL entities
    'brave-news-all-entities-weekly': {
        'task': 'apps.feed.tasks.fetch_brave_news_for_all_entities',
        'schedule': crontab(hour=3, minute=0, day_of_week=0),  # Sunday 3am only
    },

    # OPTIONAL: High-priority entities only (top 100) - twice weekly
    # DISABLED: fetch_brave_news_for_priority_entities function doesn't exist
    # 'brave-news-priority-entities': {
    #     'task': 'apps.feed.tasks.fetch_brave_news_for_priority_entities',
    #     'schedule': crontab(hour=3, minute=0, day_of_week=3),  # Wednesday 3am
    # },

    # ── Bootstrap (monthly, not weekly) ──────────────────────────────────
    'bootstrap-all-entities': {
        'task': 'apps.entity.tasks.bootstrap_all_entities',
        'schedule': crontab(hour=3, minute=0, day_of_week=0, day_of_month=1),  # 1st Sunday
    },

    # ── Cleanup / trending ────────────────────────────────────────────────
    'cleanup-feeds-4am': {
        'task': 'apps.feed.tasks.cleanup_old_feed_items',
        'schedule': crontab(hour=4, minute=0),
    },
    'mark-trending-hourly': {
        'task': 'apps.feed.tasks.mark_trending_items',
        'schedule': crontab(minute=30),
    },
    'cleanup-stale-live-events': {
        'task': 'apps.event.tasks.cleanup_stale_live_events',
        'schedule': crontab(minute=0),  # every hour
    },

    # ── Stats (weekly instead of hourly) ──────────────────────────────────
    'team-stats-weekly': {
        'task': 'apps.entity.tasks.update_all_team_stats',
        'schedule': crontab(hour=5, minute=0, day_of_week=0),  # Sunday 5am
    },

    'enrich-logos-weekly': {
        'task': 'apps.sports_apis.tasks.enrich_missing_logos',
        'schedule': crontab(hour=2, minute=0, day_of_week=1),  # Monday 2am
    },
    'enrich-highlights-daily': {
        'task': 'apps.sports_apis.tasks.enrich_event_highlights_today',
        'schedule': crontab(hour=23, minute=30),  # 11:30pm daily
    },
    'fetch-highlights-recently-completed': {
        'task': 'apps.sports_apis.tasks.fetch_highlights_for_recently_completed_events',
        'schedule': 1800.0,  # every 30 minutes
    },
}

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')