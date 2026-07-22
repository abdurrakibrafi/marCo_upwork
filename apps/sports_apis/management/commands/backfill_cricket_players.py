import requests
import time
import re
import urllib.parse
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from apps.entity.models import Entity, Athlete

class Command(BaseCommand):
    help = "Scrape and seed cricket player rosters from Wikipedia"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def find_col(self, headers_list, keywords):
        for i, h in enumerate(headers_list):
            for kw in keywords:
                if kw in h:
                    return i
        return None

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING("=== DRY RUN MODE ==="))

        teams = Entity.objects.filter(type='team', sport='cricket')
        self.stdout.write(f"Found {teams.count()} cricket teams in the database.")
        if teams.count() == 0:
            return

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        created_count = 0
        updated_count = 0

        for team in teams:
            self.stdout.write(f"\nProcessing Team: {team.name}")
            search_query = f"{team.name} cricket team"
            search_url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={urllib.parse.quote(search_query)}&limit=1&namespace=0&format=json"

            time.sleep(0.5)
            try:
                r = requests.get(search_url, headers=headers, timeout=10)
                links = r.json()[3] if r.status_code == 200 else []
                if not links:
                    continue
                wiki_url = links[0]
                page = requests.get(wiki_url, headers=headers, timeout=15)
                soup = BeautifulSoup(page.text, 'html.parser')
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Error: {e}"))
                continue

            squad_table = None
            for table in soup.find_all('table', class_=re.compile(r'wikitable')):
                headers_text = " ".join(th.get_text().lower() for th in table.find_all('th'))
                has_player = "player" in headers_text or "name" in headers_text
                has_style = "batting" in headers_text or "bowling" in headers_text
                if has_player and has_style:
                    squad_table = table
                    break

            if not squad_table:
                self.stdout.write(self.style.WARNING("  No valid squad table found."))
                continue

            rows = squad_table.find_all('tr')

            # Detect column indices from the header row
            header_cells = rows[0].find_all(['th', 'td'])
            headers_list = [c.get_text().strip().lower() for c in header_cells]
            jersey_col = self.find_col(headers_list, ['no.', 'sweater', 'cap no', 'squad no', 's/n', 'shirt'])
            batting_col = self.find_col(headers_list, ['batting'])
            bowling_col = self.find_col(headers_list, ['bowling'])
            col_count = len(headers_list)

            players_found = 0
            for r in rows:
                tds = r.find_all('td')
                if len(tds) < 2:
                    continue

                name_cell = None
                for td in tds[:3]:
                    a = td.find('a')
                    if not a:
                        continue
                    href = a.get('href', '')
                    if '/wiki/' in href and ':' not in href.split('/wiki/', 1)[1]:
                        name_cell = td
                        break
                if not name_cell:
                    continue

                a_tag = name_cell.find('a')
                raw_fullname = a_tag.get_text().strip()
                is_wicketkeeper = bool(re.search(r'\(\s*wk\s*\)', raw_fullname, re.I)) or bool(re.search(r'\(\s*wk\s*\)', name_cell.get_text(), re.I))
                fullname = re.sub(r'\s*\([^)]*\)', '', raw_fullname).strip()

                if not re.match(r'^[A-Za-z][A-Za-z\.\'\-]*(\s+[A-Za-z][A-Za-z\.\'\-]*)+$', fullname):
                    continue
                if len(fullname) < 4:
                    continue

                # Jersey number: only trust it if row column count matches header count
                jersey_number = None
                if jersey_col is not None and len(tds) == col_count:
                    num_match = re.search(r'\b\d{1,3}\b', tds[jersey_col].get_text().strip())
                    if num_match:
                        jersey_number = int(num_match.group(0))

                # Position: wicket-keeper marker > bowling style presence > default batter/all-rounder
                if is_wicketkeeper:
                    position = "Wicket-keeper"
                elif bowling_col is not None and len(tds) == col_count:
                    bowl_text = tds[bowling_col].get_text().strip().lower()
                    if not bowl_text or bowl_text in ("n/a", "—n/a", "-", "—", "none"):
                        position = "Batter"
                    else:
                        position = "All-rounder"
                else:
                    position = "All-rounder"

                country = ""

                players_found += 1
                self.stdout.write(f"    Player: {fullname} (Jersey: {jersey_number}, Role: {position}, Nat: {country})")

                if dry_run:
                    continue

                parts = fullname.split(' ')
                first_name = parts[0]
                last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

                try:
                    ae, created = Entity.objects.update_or_create(
                        api_source='wikipedia',
                        external_id=f"cricket_wp_{team.id}_{self.normalize_string(fullname)}",
                        defaults={'type': 'athlete', 'name': fullname, 'sport': 'cricket',
                                  'has_api_data': True, 'is_active': True}
                    )
                    Athlete.objects.update_or_create(
                        entity=ae,
                        defaults={'first_name': first_name, 'last_name': last_name,
                                  'nationality': country, 'position': position,
                                  'jersey_number': jersey_number, 'current_team': team}
                    )
                    if created:
                        created_count += 1
                    else:
                        updated_count += 1
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"      Failed: {e}"))

            self.stdout.write(f"  Processed {players_found} valid players for {team.name}")

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f"\nCricket backfill completed! Created {created_count}, updated {updated_count}."))
        else:
            self.stdout.write(self.style.SUCCESS("\nDry-run completed."))

    def normalize_string(self, val: str) -> str:
        return re.sub(r'[^a-z0-9]', '', val.lower())
