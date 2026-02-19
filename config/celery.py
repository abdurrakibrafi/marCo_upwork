import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('config')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# Celery Beat Schedule
app.conf.beat_schedule = {
    # ===== LIVE SCORES (EVERY 30 SECONDS) =====
    # 'update-live-scores-nba': {
    #     'task': 'sports_apis.tasks.update_nba_live_scores',
    #     'schedule': 30.0,
    # },
    # 'update-live-scores-nfl': {
    #     'task': 'sports_apis.tasks.update_nfl_live_scores',
    #     'schedule': 30.0,
    # },
    # 'update-live-scores-soccer': {
    #     'task': 'sports_apis.tasks.update_soccer_live_scores',
    #     'schedule': 30.0,
    # },
    # 'update-live-scores-cricket': {
    #     'task': 'sports_apis.tasks.update_cricket_live_scores',
    #     'schedule': 30.0,
    # },
    # ===== FIXTURES (DAILY UPDATES) =====
    'update-all-fixtures-daily-1am': {
        'task': 'calendar.tasks.update_all_fixtures',
        'schedule': crontab(hour=1, minute=0),
    },

    # ===== TODAY'S FIXTURES (EVERY HOUR) =====
    'update-nba-fixtures-hourly': {
        'task': 'calendar.tasks.update_nba_fixtures',
        'schedule': crontab(minute=0),
    },
    'update-nfl-fixtures-hourly': {
        'task': 'calendar.tasks.update_nfl_fixtures',
        'schedule': crontab(minute=5),
    },
    'update-soccer-fixtures-hourly': {
        'task': 'calendar.tasks.update_soccer_fixtures',
        'schedule': crontab(minute=10),
    },
    'update-cricket-fixtures-hourly': {
        'task': 'calendar.tasks.update_cricket_fixtures',
        'schedule': crontab(minute=15),
    },
}

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')