import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.production')
django.setup()

from apps.event.models import Event
from apps.score.models import LiveScore

print("=== Event Table ===")
print("Total events in DB:", Event.objects.count())
print("Events with status 'live':", Event.objects.filter(status='live').count())
for e in Event.objects.filter(status='live'):
    print(f"  ID: {e.id} | {e.home_entity.name} vs {e.away_entity.name if e.away_entity else 'None'} | {e.sport}")

print("\n=== LiveScore Table ===")
print("Total records in LiveScore table:", LiveScore.objects.count())
print("LiveScore records with status 'live':", LiveScore.objects.filter(status='live').count())
for ls in LiveScore.objects.filter(status='live'):
    print(f"  ID: {ls.id} | {ls.home_team} vs {ls.away_team} | {ls.sport}")
