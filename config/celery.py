import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('config')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
app.autodiscover_tasks(['apps.sports_apis'])

app.conf.beat_schedule = {
    # ── StatPal live scores (all sports) ─────────────────────────────────
    'sync-statpal-data-every-minute': {
        'task': 'apps.event.tasks.sync_statpal_data',
        'schedule': 60.0,
    },

    # ── NFL live scores (BallDontLie — only source for NFL) ──────────────
    'live-scores-nfl': {
        'task': 'apps.sports_apis.tasks.update_nfl_live_scores',
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
    
    'brave-news-all-entities-weekly': {
        'task': 'apps.feed.tasks.fetch_brave_news_for_all_entities',
        'schedule': crontab(hour=3, minute=0, day_of_week=0),  # Sunday 3am
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
    'mark-trending-hourly': {
        'task': 'apps.feed.tasks.mark_trending_items',
        'schedule': crontab(minute=30),
    },
    'cleanup-stale-live-events': {
        'task': 'apps.event.tasks.cleanup_stale_live_events',
        'schedule': crontab(minute=0),  # every hour
    },

    # ── Stats (weekly) ──────────────────────────────────────────────────
    'team-stats-weekly': {
        'task': 'apps.entity.tasks.update_all_team_stats',
        'schedule': crontab(hour=5, minute=0, day_of_week=0),  # Sunday 5am
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
        'schedule': 1800.0,  # every 30 minutes
    },
}

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
