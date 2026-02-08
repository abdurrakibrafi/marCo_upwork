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

# Optional: Clean up old sent notifications
# 'cleanup-old-notifications': {
#     'task': 'apps.notification.tasks.cleanup_old_notifications',
#     'schedule': crontab(hour=2, minute=0),  # Run daily at 2 AM
# },