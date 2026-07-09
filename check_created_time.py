import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.production')
django.setup()

from apps.event.models import Event

try:
    e = Event.objects.get(id=18048)
    print(f"Event ID: {e.id}")
    print(f"Created At: {e.created_at}")
    print(f"Updated At: {e.updated_at}")
except Event.DoesNotExist:
    print("Event 18048 not found.")
