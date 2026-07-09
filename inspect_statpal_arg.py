import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.production')
django.setup()

from apps.sports_apis.services.statpal import statpal_service
from apps.event.tasks import _soccer_rows

print("Scanning StatPal daily soccer fixtures for offsets -7 to +8 for Argentina / Brazil matches...")

for offset in range(-7, 9):
    result = statpal_service.get_soccer_fixtures(offset=offset)
    if result.get('success'):
        rows = _soccer_rows(result['data'])
        for r in rows:
            home_name = r.get('home_name', '').lower()
            away_name = r.get('away_name', '').lower()
            if 'argentina' in home_name or 'argentina' in away_name or 'brazil' in home_name or 'brazil' in away_name:
                print(f"Offset: {offset} | FOUND: {r.get('home_name')} vs {r.get('away_name')} | Status: {r.get('status_raw')} | Time: {r.get('start_time')}")
print("Scan complete.")
