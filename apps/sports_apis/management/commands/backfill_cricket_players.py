import requests
import time
import re
import urllib.parse
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from apps.entity.models import Entity, Athlete

WIKI_API = "https://en.wikipedia.org/w/api.php"
BATCH_SIZE = 50
WIKI_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


class Command(BaseCommand):
    help = "Scrape and seed cricket player rosters from Wikipedia, with thumbnail images"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Preview players; fetches thumbnails for verification but skips DB writes')

    def extract_title(self, href):
        if '/wiki/' in href:
            raw = href.split('/wiki/', 1)[1].split('#')[0]
            return urllib.parse.unquote(raw)
        return None

    def find_col(self, headers_list, keywords):
        for i, h in enumerate(headers_list):
            for kw in keywords:
                if kw in h:
                    return i
        return None

    def fetch_thumbnails(self, titles):
        """
        Batch-fetch Wikipedia pageimage thumbnails for a list of page titles.
        """
        thumb_map = {}
        for i in range(0, len(titles), BATCH_SIZE):
            batch = titles[i: i + BATCH_SIZE]
            decoded_batch = [urllib.parse.unquote(t) for t in batch if t]
            if not decoded_batch:
                continue
            pipe_titles = "|".join(decoded_batch)
            params = {
                "action": "query",
                "titles": pipe_titles,
                "prop": "pageimages",
                "format": "json",
                "pithumbsize": 300,
            }
            try:
                time.sleep(0.3)
                resp = requests.get(WIKI_API, params=params, headers=WIKI_HEADERS, timeout=15)
                if resp.status_code != 200:
                    self.stdout.write(self.style.WARNING(
                        f"  [pageimages] HTTP {resp.status_code} for batch starting '{decoded_batch[0]}'"
                    ))
                    continue
                pages = resp.json().get("query", {}).get("pages", {})
                for page in pages.values():
                    page_title = page.get("title", "")
                    thumb = page.get("thumbnail", {}).get("source", "")
                    if thumb:
                        thumb_map[page_title.lower()] = thumb
                        thumb_map[page_title.replace(" ", "_").lower()] = thumb
            except Exception as exc:
                self.stdout.write(self.style.WARNING(
                    f"  [pageimages] batch request failed: {exc}. Continuing without thumbnails for this batch."
                ))
        return thumb_map

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING(
                "=== DRY RUN MODE: DB writes skipped; thumbnails are fetched and printed ==="
            ))

        teams = Entity.objects.filter(type='team', sport='cricket')
        self.stdout.write(f"Found {teams.count()} cricket teams in the database.")
        if teams.count() == 0:
            return

        created_count = 0
        updated_count = 0

        for team in teams:
            self.stdout.write(f"\nProcessing Team: {team.name}")
            search_query = f"{team.name} cricket team"
            search_url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={urllib.parse.quote(search_query)}&limit=1&namespace=0&format=json"

            time.sleep(0.5)
            try:
                r = requests.get(search_url, headers=WIKI_HEADERS, timeout=10)
                links = r.json()[3] if r.status_code == 200 else []
                if not links:
                    self.stdout.write("  No Wikipedia page found.")
                    continue
                wiki_url = links[0]
                page = requests.get(wiki_url, headers=WIKI_HEADERS, timeout=15)
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

            header_cells = rows[0].find_all(['th', 'td'])
            headers_list = [c.get_text().strip().lower() for c in header_cells]
            jersey_col = self.find_col(headers_list, ['no.', 'sweater', 'cap no', 'squad no', 's/n', 'shirt'])
            bowling_col = self.find_col(headers_list, ['bowling'])
            col_count = len(headers_list)

            # Pass 1: collect valid player candidates
            candidates = []
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
                is_wicketkeeper = bool(re.search(r'\(\s*wk\s*\)', raw_fullname, re.I)) or bool(
                    re.search(r'\(\s*wk\s*\)', name_cell.get_text(), re.I))
                fullname = re.sub(r'\s*\([^)]*\)', '', raw_fullname).strip()

                if not re.match(r'^[A-Za-z][A-Za-z\.\'\-]*(\s+[A-Za-z][A-Za-z\.\'\-]*)+$', fullname):
                    continue
                if len(fullname) < 4:
                    continue

                jersey_number = None
                if jersey_col is not None and len(tds) == col_count:
                    num_match = re.search(r'\b\d{1,3}\b', tds[jersey_col].get_text().strip())
                    if num_match:
                        jersey_number = int(num_match.group(0))

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

                href = a_tag.get('href', '')
                page_title = self.extract_title(href)

                candidates.append({
                    'fullname': fullname,
                    'jersey_number': jersey_number,
                    'position': position,
                    'page_title': page_title,
                })

            # Fetch thumbnails in batch for this team's players
            titles = [c['page_title'] for c in candidates if c['page_title']]
            thumb_map = self.fetch_thumbnails(titles) if titles else {}

            players_found = 0
            for c in candidates:
                fullname = c['fullname']
                jersey_number = c['jersey_number']
                position = c['position']
                page_title = c['page_title']
                country = ""

                thumbnail_url = ""
                if page_title:
                    key1 = page_title.lower()
                    key2 = page_title.replace(" ", "_").lower()
                    key3 = page_title.replace("_", " ").lower()
                    thumbnail_url = thumb_map.get(key1) or thumb_map.get(key2) or thumb_map.get(key3) or ""

                players_found += 1
                self.stdout.write(
                    f"    Player: {fullname} | Jersey: {jersey_number} | Role: {position} | "
                    f"Thumbnail: {thumbnail_url or '(none)'}"
                )

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
                                  'has_api_data': True, 'is_active': True,
                                  'logo_url': thumbnail_url or ''}
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

            self.stdout.write(f"  Processed {players_found} players for {team.name}")

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"\nCricket backfill completed! Created {created_count}, updated {updated_count}."))
        else:
            self.stdout.write(self.style.SUCCESS("\nDry-run completed."))

    def normalize_string(self, val):
        return re.sub(r'[^a-z0-9]', '', val.lower())
