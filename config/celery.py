import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('config')
app.config_from_object('django.conf:settings', namespace='CELERY')

app.conf.timezone = 'UTC'
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')


# Configure beat schedule
app.conf.beat_schedule = {
    # Example task - runs every 5 minutes
    'test-task-every-5-min': {
        'task': 'config.celery.debug_task',
        'schedule': 100.0,
    },
}

# Use database scheduler (since you have django_celery_beat installed)
app.conf.beat_scheduler = 'django_celery_beat.schedulers:DatabaseScheduler'