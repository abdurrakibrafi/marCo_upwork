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

        url = "https://en.wikipedia.org/wiki/List_of_male_golfers_who_have_been_in_the_world_top_10"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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

        tables = soup.find_all('table', class_='wikitable')
        if not tables or len(tables) < 1:
            self.stdout.write(self.style.ERROR("Could not find required ranking tables on page."))
            return

        def extract_player_and_country(cell):
            player_a = None
            all_a = cell.find_all('a')
            for a in all_a:
                if not a.find_parent(class_='flagicon'):
                    player_a = a
                    break
            if not player_a:
                return None, None
            
            fullname = player_a.get_text().strip()
            country = "Neutral"
            flagicon = cell.find(class_='flagicon')
            if not flagicon and cell.parent:
                flagicon = cell.parent.find(class_='flagicon')
                
            if flagicon:
                img = flagicon.find('img')
                if img and 'alt' in img.attrs:
                    country = img.attrs['alt'].strip()
            return fullname, country

        golfers = {}

        # 1. Table 0: Current Top 10
        if len(tables) > 0:
            self.stdout.write("Parsing Current Top 10...")
            for r in tables[0].find_all('tr')[1:]:
                tds = r.find_all('td')
                if len(tds) > 2:
                    name, country = extract_player_and_country(tds[2])
                    if name:
                        golfers[name] = country

        # 2. Table 1: World Number Ones
        if len(tables) > 1:
            self.stdout.write("Parsing World Number Ones...")
            for r in tables[1].find_all('tr')[1:]:
                tds = r.find_all('td')
                if len(tds) > 0:
                    name, country = extract_player_and_country(tds[0])
                    if name:
                        if name not in golfers or golfers[name] == "Neutral":
                            golfers[name] = country

        # 3. Table 13: 2025 Top 10
        if len(tables) > 13:
            self.stdout.write("Parsing 2025 rankings...")
            for r in tables[13].find_all('tr')[1:]:
                tds = r.find_all('td')
                if len(tds) > 1:
                    name, country = extract_player_and_country(tds[1])
                    if name:
                        if name not in golfers or golfers[name] == "Neutral":
                            golfers[name] = country

        # 4. Tables 14-16 (Year-end Top 10s: 2016-2024)
        for idx in range(14, 17):
            if len(tables) > idx:
                self.stdout.write(f"Parsing year-end table {idx}...")
                for r in tables[idx].find_all('tr')[1:]:
                    tds = r.find_all('td')
                    for p_col in [1, 3, 5]:
                        if len(tds) > p_col:
                            name, country = extract_player_and_country(tds[p_col])
                            if name:
                                if name not in golfers or golfers[name] == "Neutral":
                                    golfers[name] = country

        self.stdout.write(f"Found {len(golfers)} unique golfers from OWGR.")

        created_count = 0
        updated_count = 0

        for idx, (fullname, country) in enumerate(sorted(golfers.items())):
            # Parse first/last name
            parts = fullname.split(' ')
            first_name = parts[0]
            last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

            slug = re.sub(r'[^a-z0-9]', '', fullname.lower())
            
            if dry_run:
                self.stdout.write(f"  [Dry-run] Golfer {idx+1}: {fullname} ({country})")
                continue

            try:
                # Athlete entity
                ae, created = Entity.objects.update_or_create(
                    api_source='wikipedia',
                    external_id=f"golf_owgr_{slug}",
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

            time.sleep(0.02)

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f"\nGolf backfill completed! Created {created_count} athletes, updated {updated_count} athletes."))
        else:
            self.stdout.write(self.style.SUCCESS("\nGolf backfill dry-run completed successfully!"))
