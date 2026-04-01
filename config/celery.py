import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('config')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
app.autodiscover_tasks(['apps.sports_apis'])

app.conf.beat_schedule = {
    # ── Live scores ────────────────────────────────────────────────────────
    'live-scores-nba': {
        'task': 'apps.sports_apis.tasks.update_nba_live_scores',
        'schedule': 120.0,
    },
    'live-scores-nfl': {
        'task': 'apps.sports_apis.tasks.update_nfl_live_scores',
        'schedule': 120.0,
    },
    # FIXED: was defined twice before — second definition silently killed first
    'live-scores-soccer': {
        'task': 'apps.event.tasks.update_soccer_live_scores_only',
        'schedule': 120.0,
    },
    'live-scores-cricket': {
        'task': 'apps.sports_apis.tasks.update_cricket_live_scores',
        'schedule': 120.0,
    },

    # ── Fixtures ───────────────────────────────────────────────────────────
    'fixtures-daily': {
        'task': 'apps.event.tasks.update_all_fixtures',
        'schedule': crontab(hour=6, minute=0),
    },

     # ── Event detail population (every 5 min) ─────────────────────────────
    'check-completed-events': {
        'task': 'apps.event.tasks.check_completed_events',
        'schedule': 300.0,  # every 5 minutes
    },

    # ── RSS / news ─────────────────────────────────────────────────────────
    'poll-rss-sources': {
        'task': 'apps.feed.tasks.poll_all_active_sources',
        'schedule': 300.0,
    },
    # Daily fresh news for ALL entities (not just nest + trending)
    'brave-news-all-entities-morning': {
        'task': 'apps.feed.tasks.fetch_brave_news_for_all_entities',
        'schedule': crontab(hour=2, minute=0),  # daily 2am
    },
    'brave-news-all-entities-afternoon': {
        'task': 'apps.feed.tasks.fetch_brave_news_for_all_entities',
        'schedule': crontab(hour=14, minute=0),  # daily 2pm
    },

    # ── Bootstrap (weekly, Sunday 3am) ─────────────────────────────────────
    'bootstrap-all-entities': {
        'task': 'apps.entity.tasks.bootstrap_all_entities',
        'schedule': crontab(hour=3, minute=0, day_of_week=0),
    },

    # ── Cleanup / trending ─────────────────────────────────────────────────
    'cleanup-feeds-4am': {
        'task': 'apps.feed.tasks.cleanup_old_feed_items',
        'schedule': crontab(hour=4, minute=0),
    },
    'mark-trending-hourly': {
        'task': 'apps.feed.tasks.mark_trending_items',
        'schedule': crontab(minute=30),
    },

    # ── Stats ──────────────────────────────────────────────────────────────
    'team-stats-hourly': {
        'task': 'apps.entity.tasks.update_all_team_stats',
        'schedule': crontab(minute=45),
    },
}


@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')