import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('config')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
# Ensure Celery explicitly discovers tasks in the sports_apis package
app.autodiscover_tasks(['apps.sports_apis'])

# Celery Beat Schedule
app.conf.beat_schedule = {
    # ===== LIVE SCORES (EVERY 2 MINUTES - REDUCED) =====
    'update-live-scores-nba': {
        'task': 'apps.sports_apis.tasks.update_nba_live_scores',
        'schedule': 1200.0,  # 2 minutes instead of 30 seconds
    },
    'update-live-scores-nfl': {
        'task': 'apps.sports_apis.tasks.update_nfl_live_scores',
        'schedule': 1200.0,  # 2 minutes instead of 30 seconds
    },
    'update-live-scores-soccer': {
        'task': 'apps.sports_apis.tasks.update_soccer_live_scores',
        'schedule': 1200.0,  # 2 minutes instead of 30 seconds
    },
    'update-live-scores-cricket': {
        'task': 'apps.sports_apis.tasks.update_cricket_live_scores',
        'schedule': 1200.0,  # 2 minutes instead of 30 seconds
    },

    # ===== RSS POLLING (EVERY 5 MINUTES) =====
    'poll-all-rss-sources': {
        'task': 'apps.feed.tasks.poll_all_active_sources',
        'schedule': 3000.0,  # 5 minutes
    },

    # ===== CLEANUP (DAILY) =====
    'cleanup-old-feeds-daily-4am': {
        'task': 'apps.feed.tasks.cleanup_old_feed_items',
        'schedule': crontab(hour=4, minute=0),
    },

    # ===== TRENDING (HOURLY) =====
    'mark-trending-items-hourly': {
        'task': 'apps.feed.tasks.mark_trending_items',
        'schedule': crontab(minute=30),
    },

    # ===== STATS UPDATES (HOURLY) =====
    'update-all-team-stats-hourly': {
        'task': 'apps.entity.tasks.update_all_team_stats',
        'schedule': crontab(minute=45),  # Every hour at :45
    },
}

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')