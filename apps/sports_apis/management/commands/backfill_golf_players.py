import requests
import time
import re
import json
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from apps.entity.models import Entity, Athlete

class Command(BaseCommand):
    help = "Scrape and seed top 100 golf players from PGA Tour OWGR stats page"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run the command without saving anything to the database'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING("=== DRY RUN MODE: Database changes will not be saved ==="))

        url = "https://www.pgatour.com/stats/detail/186"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.pgatour.com/stats",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1"
        }

        self.stdout.write(f"Fetching OWGR golfers from {url}...")
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                self.stdout.write(self.style.ERROR(f"Failed to fetch OWGR page, HTTP status: {resp.status_code}"))
                return
            soup = BeautifulSoup(resp.text, 'html.parser')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to make request: {e}"))
            return

        next_data_tag = soup.find('script', id='__NEXT_DATA__')
        if not next_data_tag:
            self.stdout.write(self.style.ERROR("Could not find __NEXT_DATA__ script tag on page."))
            return

        try:
            next_data = json.loads(next_data_tag.get_text())
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to parse __NEXT_DATA__ JSON: {e}"))
            return

        queries = next_data.get('props', {}).get('pageProps', {}).get('dehydratedState', {}).get('queries', [])
        target_rows = None
        for q in queries:
            query_key = q.get('queryKey', [])
            if len(query_key) > 1 and isinstance(query_key[1], dict):
                if query_key[1].get('statId') == '186':
                    target_rows = q.get('state', {}).get('data', {}).get('rows', [])
                    break

        if not target_rows:
            self.stdout.write(self.style.ERROR("Could not find OWGR stats data (statId=186) on the page."))
            return

        # Filter top 100 golfers
        golfers = []
        for row in target_rows:
            rank = row.get('rank')
            if rank and rank <= 100:
                golfers.append(row)

        self.stdout.write(f"Found {len(golfers)} golfers in the OWGR top 100.")

        created_count = 0
        updated_count = 0

        for row in golfers:
            fullname = row.get('playerName', '').strip()
            country = row.get('country', 'Neutral').strip()
            player_id = row.get('playerId', '').strip()
            rank = row.get('rank')

            if not fullname:
                continue

            # Parse first/last name
            parts = fullname.split(' ')
            first_name = parts[0]
            last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

            if dry_run:
                self.stdout.write(f"  [Dry-run] Golfer Rank {rank}: {fullname} ({country}, ID: {player_id})")
                continue

            try:
                # Athlete entity
                ae, created = Entity.objects.update_or_create(
                    api_source='pgatour',
                    external_id=f"golf_pgatour_{player_id}",
                    defaults={
                        'type': 'athlete',
                        'name': fullname,
                        'sport': 'golf',
                        'has_api_data': True,
                        'is_active': True,
                    }
                )
                
                # Athlete details
                defaults = {
                    'first_name': first_name,
                    'last_name': last_name,
                    'position': 'Golfer',
                }
                if country != "Neutral":
                    defaults['nationality'] = country

                ath, ath_created = Athlete.objects.get_or_create(
                    entity=ae,
                    defaults=defaults
                )
                if not ath_created:
                    ath.first_name = first_name
                    ath.last_name = last_name
                    if country != "Neutral":
                        ath.nationality = country
                    ath.save()

                if created:
                    created_count += 1
                else:
                    updated_count += 1
            except Exception as save_err:
                self.stdout.write(self.style.ERROR(f"    Failed to save athlete {fullname}: {save_err}"))

            time.sleep(0.01)

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f"\nGolf backfill completed! Created {created_count} athletes, updated {updated_count} athletes."))
        else:
            self.stdout.write(self.style.SUCCESS("\nGolf backfill dry-run completed successfully!"))
