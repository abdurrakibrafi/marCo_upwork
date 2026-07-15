import requests
import time
import re
import urllib.parse
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from apps.event.models import Event
from apps.entity.models import Entity, Athlete

class Command(BaseCommand):
    help = "Scrape and seed horse racing jockey profiles from Wikipedia"

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

        # Get all horse racing events
        events = Event.objects.filter(sport='horse_racing')
        self.stdout.write(f"Found {events.count()} horse racing events in the database.")

        # Extract unique jockeys
        jockeys = set()
        for e in events:
            raw = e.raw or {}
            runners = raw.get('runners', {}).get('horse', [])
            if isinstance(runners, dict):
                runners = [runners]
            elif not isinstance(runners, list):
                runners = []

            for h in runners:
                jname = h.get('jockey')
                if jname and str(jname).strip():
                    jockeys.add(str(jname).strip())

        self.stdout.write(f"Found {len(jockeys)} unique jockeys from horse racing events.")

        if not jockeys:
            self.stdout.write(self.style.WARNING("No jockeys found to process. Seeding will be skipped."))
            return

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        created_count = 0
        updated_count = 0

        for idx, jockey in enumerate(sorted(jockeys)):
            self.stdout.write(f"\nProcessing Jockey ({idx+1}/{len(jockeys)}): {jockey}")

            # 1. Search Wikipedia using OpenSearch API
            search_query = f"{jockey} jockey"
            encoded_query = urllib.parse.quote(search_query)
            search_url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={encoded_query}&limit=1&namespace=0&format=json"

            time.sleep(0.5)  # Rate limiting safety
            try:
                search_resp = requests.get(search_url, headers=headers, timeout=10)
                if search_resp.status_code != 200:
                    self.stdout.write(self.style.WARNING(f"  Wikipedia search failed (HTTP {search_resp.status_code})"))
                    continue
                search_data = search_resp.json()
                links = search_data[3]
                if not links:
                    # Retry search with just name
                    self.stdout.write(f"  Could not find article for '{search_query}'. Retrying with '{jockey}'...")
                    search_url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={urllib.parse.quote(jockey)}&limit=1&namespace=0&format=json"
                    time.sleep(0.5)
                    search_resp = requests.get(search_url, headers=headers, timeout=10)
                    search_data = search_resp.json()
                    links = search_data[3]

                if not links:
                    self.stdout.write(self.style.WARNING(f"  No Wikipedia page found for jockey '{jockey}'"))
                    continue

                wiki_url = links[0]
                self.stdout.write(f"  Matched Wikipedia page: {wiki_url}")
                
                # 2. Fetch page HTML
                page_resp = requests.get(wiki_url, headers=headers, timeout=15)
                if page_resp.status_code != 200:
                    self.stdout.write(self.style.WARNING(f"  Failed to fetch Wikipedia page (HTTP {page_resp.status_code})"))
                    continue
                soup = BeautifulSoup(page_resp.text, 'html.parser')
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Error fetching Wikipedia data: {e}"))
                continue

            # 3. Parse infobox for nationality/details
            country = "Neutral"
            infobox = soup.find('table', class_='infobox')
            if infobox:
                rows = infobox.find_all('tr')
                for r in rows:
                    th = r.find('th')
                    td = r.find('td')
                    if th and td:
                        th_text = th.get_text().strip().lower()
                        if "nationality" in th_text or "country" in th_text or "citizenship" in th_text:
                            # Clean nationality text
                            country = re.sub(r'\[\d+\]', '', td.get_text()).strip()
                            break

            # If country is not found in infobox nationality, check for flagicon
            if country == "Neutral" and infobox:
                flagicon = infobox.find(class_='flagicon')
                if flagicon:
                    img = flagicon.find('img')
                    if img and 'alt' in img.attrs:
                        country = img.attrs['alt'].strip()

            self.stdout.write(f"  Nationality: {country}")

            # Parse first/last name
            parts = jockey.split(' ')
            first_name = parts[0]
            last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

            if dry_run:
                continue

            try:
                # Athlete entity
                ae, created = Entity.objects.update_or_create(
                    api_source='wikipedia',
                    external_id=f"jockey_wp_{self.normalize_string(jockey)}",
                    defaults={
                        'type': 'athlete',
                        'name': jockey,
                        'sport': 'horse_racing',
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
                        'position': 'Jockey',
                    }
                )
                if created:
                    created_count += 1
                else:
                    updated_count += 1
            except Exception as save_err:
                self.stdout.write(self.style.ERROR(f"    Failed to save jockey {jockey}: {save_err}"))

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f"\nJockey seeding completed! Created {created_count} athletes, updated {updated_count} athletes."))
        else:
            self.stdout.write(self.style.SUCCESS("\nJockey seeding dry-run completed successfully!"))

    def normalize_string(self, val: str) -> str:
        val = val.lower()
        val = re.sub(r'[^a-z0-9]', '', val)
        return val
