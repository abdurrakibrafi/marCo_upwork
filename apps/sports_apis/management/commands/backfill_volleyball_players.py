import requests
import time
import re
import urllib.parse
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from apps.entity.models import Entity, Athlete

class Command(BaseCommand):
    help = "Scrape and seed volleyball club player rosters from Wikipedia"

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

        teams = Entity.objects.filter(type='team', sport='volleyball')
        self.stdout.write(f"Found {teams.count()} volleyball teams in the database.")

        if teams.count() == 0:
            self.stdout.write(self.style.WARNING("No volleyball teams found in database. Seeding will be skipped."))
            return

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        created_count = 0
        updated_count = 0

        for team in teams:
            self.stdout.write(f"\nProcessing Team: {team.name}")
            
            # 1. Search Wikipedia using OpenSearch API
            search_query = f"{team.name} volleyball"
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
                    # Retry search with just team name
                    self.stdout.write(f"  Could not find article for '{search_query}'. Retrying with '{team.name}'...")
                    search_url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={urllib.parse.quote(team.name)}&limit=1&namespace=0&format=json"
                    time.sleep(0.5)
                    search_resp = requests.get(search_url, headers=headers, timeout=10)
                    search_data = search_resp.json()
                    links = search_data[3]

                if not links:
                    self.stdout.write(self.style.WARNING(f"  No Wikipedia page found for team '{team.name}'"))
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

            # 3. Locate squad table
            tables = soup.find_all('table')
            squad_table = None
            for table in tables:
                headers_text = "".join(th.get_text().lower() for th in table.find_all('th'))
                if "player" in headers_text or "name" in headers_text:
                    if "pos" in headers_text or "position" in headers_text or "nat" in headers_text or "number" in headers_text:
                        squad_table = table
                        break

            if not squad_table:
                self.stdout.write(self.style.WARNING("  Could not find squad/roster table in Wikipedia page."))
                continue

            # 4. Parse squad table rows
            rows = squad_table.find_all('tr')
            players_found = 0
            for r in rows:
                tds = r.find_all('td')
                if len(tds) < 2:
                    continue

                # Identify cells by column header index
                th_elements = r.find_parent('table').find_all('tr')[0].find_all(['th', 'td'])
                headers_list = [th.get_text().strip().lower() for th in th_elements]

                if not any(headers_list) and len(r.find_parent('table').find_all('tr')) > 1:
                    th_elements = r.find_parent('table').find_all('tr')[1].find_all(['th', 'td'])
                    headers_list = [th.get_text().strip().lower() for th in th_elements]

                player_idx = 1
                pos_idx = -1
                num_idx = 0
                nat_idx = -1

                for idx, h in enumerate(headers_list):
                    if "player" in h or "name" in h:
                        player_idx = idx
                    elif "pos" in h or "position" in h:
                        pos_idx = idx
                    elif "no" in h or "number" in h or "#" in h:
                        num_idx = idx
                    elif "nat" in h or "nationality" in h or "country" in h:
                        nat_idx = idx

                if player_idx >= len(tds):
                    player_idx = min(1, len(tds) - 1)

                player_td = tds[player_idx]
                player_a = player_td.find('a')
                fullname = None
                if player_a:
                    fullname = player_a.get_text().strip()
                else:
                    fullname = re.sub(r'\[\d+\]', '', player_td.get_text()).strip()

                if not fullname or len(fullname) < 3 or fullname.lower() in ("player", "name", "position"):
                    continue

                fullname = re.sub(r'\s*\([^)]*\)', '', fullname)
                fullname = re.sub(r'\s*\(c\)', '', fullname, flags=re.I)
                fullname = fullname.strip()

                # Jersey number
                jersey_number = None
                if num_idx >= 0 and num_idx < len(tds):
                    num_text = tds[num_idx].get_text().strip()
                    num_match = re.search(r'\d+', num_text)
                    if num_match:
                        jersey_number = int(num_match.group(0))

                # Position
                position = "Player"
                if pos_idx >= 0 and pos_idx < len(tds):
                    pos_text = tds[pos_idx].get_text().strip()
                    if pos_text:
                        position = pos_text

                # Nationality/Country
                country = "Neutral"
                flagicon = None
                for cell in tds:
                    flagicon = cell.find(class_='flagicon')
                    if flagicon:
                        break
                if flagicon:
                    img = flagicon.find('img')
                    if img and 'alt' in img.attrs:
                        country = img.attrs['alt'].strip()

                players_found += 1
                self.stdout.write(f"    Player: {fullname} (Jersey: {jersey_number}, Pos: {position}, Nat: {country})")

                # Parse first/last name
                parts = fullname.split(' ')
                first_name = parts[0]
                last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

                if dry_run:
                    continue

                try:
                    # Athlete entity
                    ae, created = Entity.objects.update_or_create(
                        api_source='wikipedia',
                        external_id=f"volleyball_wp_{team.id}_{self.normalize_string(fullname)}",
                        defaults={
                            'type': 'athlete',
                            'name': fullname,
                            'sport': 'volleyball',
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
                            'position': position,
                            'jersey_number': jersey_number,
                            'current_team': team,
                        }
                    )
                    if created:
                        created_count += 1
                    else:
                        updated_count += 1
                except Exception as save_err:
                    self.stdout.write(self.style.ERROR(f"      Failed to save athlete {fullname}: {save_err}"))

            self.stdout.write(f"  Processed {players_found} players for {team.name}")

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f"\nVolleyball backfill completed! Created {created_count} athletes, updated {updated_count} athletes."))
        else:
            self.stdout.write(self.style.SUCCESS("\nVolleyball backfill dry-run completed successfully!"))

    def normalize_string(self, val: str) -> str:
        val = val.lower()
        val = re.sub(r'[^a-z0-9]', '', val)
        return val
