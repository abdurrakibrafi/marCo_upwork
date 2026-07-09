import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.production')
django.setup()

from apps.sports_apis.services.statpal import statpal_service

print("Listing ALL leagues returned by StatPal for offset 3 (July 12, 2026):")
result = statpal_service.get_soccer_fixtures(offset=3)
if result.get('success'):
    data = result.get('data', {})
    matches_key = [k for k in data.keys() if k.startswith('matches_') or k == 'matches']
    if matches_key:
        leagues = data[matches_key[0]].get('league', [])
        if isinstance(leagues, dict):
            leagues = [leagues]
        print(f"Total leagues: {len(leagues)}")
        for l in leagues:
            print(f"  League: {l.get('name')} | Matches: {len(l.get('match', []))}")
