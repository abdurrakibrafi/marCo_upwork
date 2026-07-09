import os
import django
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.sports_apis.services.statpal import statpal_service

print("--- Calling get_soccer_match_stats(256) ---")
res = statpal_service.get_soccer_match_stats(256)
print("Success:", res.get("success"))
if res.get("success"):
    data = res.get("data", {})
    print("Root keys:", list(data.keys()))
    # print structural preview
    for k, v in data.items():
        if isinstance(v, dict):
            print(f"Key '{k}' is dict with keys: {list(v.keys())}")
            for sub_k, sub_v in v.items():
                if isinstance(sub_v, dict):
                    print(f"  Subkey '{sub_k}' keys: {list(sub_v.keys())}")
                elif isinstance(sub_v, list) and sub_v:
                    print(f"  Subkey '{sub_k}' is list of {len(sub_v)} items, first item type: {type(sub_v[0])}")
                    if isinstance(sub_v[0], dict):
                        print(f"    First item keys: {list(sub_v[0].keys())}")
