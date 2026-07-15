import requests
import time
import re
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from apps.entity.models import Entity, Athlete

class Command(BaseCommand):
    help = "Scrape and seed top golf players (PGA Tour card holders) from Wikipedia"

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

        url = "https://en.wikipedia.org/wiki/List_of_2019_PGA_Tour_card_holders"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        self.stdout.write(f"Fetching PGA Tour card holders from {url}...")
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                self.stdout.write(self.style.ERROR(f"Failed to fetch card holders, HTTP status: {resp.status_code}"))
                return
            soup = BeautifulSoup(resp.text, 'html.parser')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to make request: {e}"))
            return

        tables = soup.find_all('table', class_='wikitable')
        if not tables:
            self.stdout.write(self.style.ERROR("Could not find any tables on page."))
            return

        created_count = 0
        updated_count = 0
        global_count = 0

        # We will parse Table 0 and Table 1 which contain the primary card holders lists
        for idx, table in enumerate(tables[:2]):
            self.stdout.write(f"\nProcessing Table {idx}...")
            rows = table.find_all('tr')
            for r in rows:
                tds = r.find_all('td')
                if not tds:
                    continue

                player_td = tds[0]
                all_a = player_td.find_all('a')
                player_a = None
                for a in all_a:
                    if not a.find_parent(class_='flagicon'):
                        player_a = a
                        break

                if not player_a:
                    continue

                fullname = player_a.get_text().strip()
                
                # Extract country/nationality from flagicon
                country = None
                flagicon = player_td.find(class_='flagicon')
                if flagicon:
                    img = flagicon.find('img')
                    if img and 'alt' in img.attrs:
                        country = img.attrs['alt'].strip()

                if not country:
                    country = "Neutral"

                global_count += 1
                self.stdout.write(f"  Golfer {global_count}: {fullname} ({country})")

                # Parse first/last name
                parts = fullname.split(' ')
                first_name = parts[0]
                last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

                if dry_run:
                    self.stdout.write(f"    [Dry-run] Would create/update Athlete: {fullname} (Country: {country})")
                    continue

                try:
                    # Athlete entity
                    ae, created = Entity.objects.update_or_create(
                        api_source='wikipedia',
                        external_id=f"golf_pga_{global_count}",
                        defaults={
                            'type': 'athlete',
                            'name': fullname,
                            'sport': 'golf',
                            'has_api_data': True,
                            'is_active': True,
                        }
                    )
                    # Athlete details
                    Athlete.objects.update_or_create(
                        entity=ae,
                        defaults={
                            'first_name': first_name,
                            'last_name': last_name,
                            'nationality': country,
                            'position': 'Golfer',
                        }
                    )
                    if created:
                        created_count += 1
                    else:
                        updated_count += 1
                except Exception as save_err:
                    self.stdout.write(self.style.ERROR(f"    Failed to save athlete: {save_err}"))

                # Slight throttle
                time.sleep(0.05)

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f"\nGolf backfill completed! Created {created_count} athletes, updated {updated_count} athletes."))
        else:
            self.stdout.write(self.style.SUCCESS("\nGolf backfill dry-run completed successfully!"))
