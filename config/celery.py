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
    'update-fixtures-daily': {
    'task': 'apps.event.tasks.update_all_fixtures',
    'schedule': crontab(hour=6, minute=0),  # every day at 6am
    },
    'update-fixtures-live': {
        'task': 'apps.event.tasks.update_soccer_fixtures',
        'schedule': 120.0,  # every 2 min during live games
    },

    # ===== RSS POLLING (EVERY 5 MINUTES) =====
    'poll-all-rss-sources': {
        'task': 'apps.feed.tasks.poll_all_active_sources',
        'schedule': 300.0,  # 5 minutes
    },
    'fetch-brave-news-all-nests': {
        'task': 'apps.feed.tasks.fetch_brave_news_for_all_nest_entities',
        'schedule': 1800.0,  # every 30 minutes
    },
    'fetch-brave-news-trending': {
        'task': 'apps.feed.tasks.fetch_brave_news_for_trending',
        'schedule': 3600.0,  # every hour
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