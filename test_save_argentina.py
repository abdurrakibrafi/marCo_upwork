import os
import django
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.production')
django.setup()

from apps.sports_apis.services.statpal import statpal_service
from apps.event.tasks import _soccer_rows, _save_event

print("Fetching offset 3 daily fixtures...")
result = statpal_service.get_soccer_fixtures(offset=3)
if result.get('success'):
    data = result.get('data', {})
    matches_key = [k for k in data.keys() if k.startswith('matches_') or k == 'matches']
    if matches_key:
        leagues = data[matches_key[0]].get('league', [])
        if isinstance(leagues, dict):
            leagues = [leagues]
        for l in leagues:
            if 'world championship - play offs' in l.get('name', '').lower():
                matches = l.get('match', [])
                if isinstance(matches, dict):
                    matches = [matches]
                for m in matches:
                    if not isinstance(m, dict):
                        continue
                    home = m.get('home', {}).get('name')
                    away = m.get('away', {}).get('name')
                    if home == 'Argentina' or away == 'Argentina':
                        print(f"\nFound match in StatPal: {home} vs {away}")
                        # Extract row using task's extractor
                        # Since lg is needed in _soccer_rows, we can construct a mini league dict
                        mini_data = {
                            "live_matches": {
                                "league": [
                                    {
                                        "id": l.get("id"),
                                        "name": l.get("name"),
                                        "match": [m]
                                    }
                                ]
                            }
                        }
                        rows = _soccer_rows(mini_data)
                        print(f"Extracted row: {rows[0]}")
                        
                        # Save event
                        print("Saving event...")
                        try:
                            event_obj = _save_event(rows[0])
                            print(f"SUCCESS: Event saved! ID: {event_obj.id if event_obj else 'None'}")
                        except Exception as exc:
                            print(f"ERROR saving event: {exc}")
                            import traceback
                            traceback.print_exc()
