import requests
import time
import re
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from apps.entity.models import Entity, Athlete

class Command(BaseCommand):
    help = "Scrape and seed top tennis players (ATP & WTA) from Wikipedia"

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

        url = "https://en.wikipedia.org/wiki/Current_tennis_rankings"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        self.stdout.write(f"Fetching current tennis rankings from {url}...")
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                self.stdout.write(self.style.ERROR(f"Failed to fetch rankings page, HTTP status: {resp.status_code}"))
                return
            soup = BeautifulSoup(resp.text, 'html.parser')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to make request: {e}"))
            return

        tables = soup.find_all('table', class_='wikitable')
        if len(tables) < 5:
            self.stdout.write(self.style.ERROR(f"Could not find ranking tables on page. Found only {len(tables)} tables."))
            return

        # ATP Singles is Table 0, WTA Singles is Table 4
        targets = [
            ("ATP Singles", tables[0], "atp"),
            ("WTA Singles", tables[4], "wta")
        ]

        created_count = 0
        updated_count = 0

        for label, table, prefix in targets:
            self.stdout.write(f"\nProcessing {label}...")
            rows = table.find_all('tr')
            
            # Find data rows (they contain td elements)
            data_rows = [r for r in rows if r.find_all('td')]
            self.stdout.write(f"Found {len(data_rows)} data rows.")

            for r in data_rows:
                tds = r.find_all('td')
                if len(tds) < 3:
                    continue

                try:
                    rank_str = tds[0].get_text().strip()
                    rank = int(re.sub(r'\D', '', rank_str))
                except ValueError:
                    continue

                # Player name & country parsing
                player_td = tds[1]
                player_a = player_td.find('a')
                if not player_a:
                    continue
                fullname = player_a.get_text().strip()
                
                # Check for country abbreviation in parentheses (e.g. (ITA))
                country = None
                text_match = re.search(r"\(([A-Z]{3})\)", player_td.get_text())
                if text_match:
                    country = text_match.group(1)
                else:
                    # Fallback to flagicon
                    flagicon = player_td.find(class_='flagicon')
                    if flagicon:
                        img = flagicon.find('img')
                        if img and 'alt' in img.attrs:
                            country = img.attrs['alt'].strip()

                if not country:
                    country = "Neutral"

                self.stdout.write(f"  Rank {rank}: {fullname} ({country})")

                # Parse first/last name
                parts = fullname.split(' ')
                first_name = parts[0]
                last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

                if dry_run:
                    self.stdout.write(f"    [Dry-run] Would create/update Athlete: {fullname} (Rank: {rank}, Country: {country})")
                    continue

                try:
                    # Athlete entity
                    ae, created = Entity.objects.update_or_create(
                        api_source='wikipedia',
                        external_id=f"tennis_{prefix}_{rank}",
                        defaults={
                            'type': 'athlete',
                            'name': fullname,
                            'sport': 'tennis',
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
                            'position': 'Player',
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
            self.stdout.write(self.style.SUCCESS(f"\nTennis backfill completed! Created {created_count} athletes, updated {updated_count} athletes."))
        else:
            self.stdout.write(self.style.SUCCESS("\nTennis backfill dry-run completed successfully!"))
