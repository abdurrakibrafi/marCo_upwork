import requests
import time
import re
import urllib.parse
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from apps.entity.models import Entity, Athlete

WIKI_API = "https://en.wikipedia.org/w/api.php"
BATCH_SIZE = 50


class Command(BaseCommand):
    help = "Scrape and seed cricket player rosters from Wikipedia, with thumbnail images"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Preview players; fetches thumbnails for verification but skips DB writes')

    # ------------------------------------------------------------------ #
    #  Helper: extract Wikipedia page title from /wiki/<title> href
    # ------------------------------------------------------------------ #
    def extract_title(self, href: str) -> str | None:
        if '/wiki/' in href:
            return href.split('/wiki/', 1)[1].split('#')[0]
        return None

    # ------------------------------------------------------------------ #
    #  Helper: batch-fetch thumbnails for a list of page titles
    #  Returns dict: title (lower-stripped) -> thumbnail_url
    # ------------------------------------------------------------------ #
    def fetch_thumbnails(self, titles: list[str]) -> dict:
        """
        Batch-fetch Wikipedia pageimage thumbnails for a list of page titles.
        Titles may be URL-encoded (e.g. from /wiki/... hrefs) — they are decoded
        before being sent to the API so Wikipedia can resolve them correctly.
        Returns dict keyed by lowercased title (both API-returned and original).
        """
        thumb_map = {}
        for i in range(0, len(titles), BATCH_SIZE):
            batch = titles[i: i + BATCH_SIZE]
            # Decode URL-encoded chars (e.g. %27 -> ') before sending to API
            decoded_batch = [urllib.parse.unquote(t) for t in batch]
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
                resp = requests.get(WIKI_API, params=params, timeout=15)
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
                        # Index under normalised Wikipedia title (spaces, canonical caps)
                        thumb_map[page_title.lower()] = thumb
                        # Also index under underscore variant so lookup always works
                        thumb_map[page_title.replace(" ", "_").lower()] = thumb
            except Exception as exc:
                self.stdout.write(self.style.WARNING(
                    f"  [pageimages] batch request failed: {exc}. Continuing without thumbnails for this batch."
                ))
        return thumb_map

    # ------------------------------------------------------------------ #
    #  Main handler
    # ------------------------------------------------------------------ #
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

        wiki_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        created_count = 0
        updated_count = 0

        for team in teams:
            self.stdout.write(f"\nProcessing Team: {team.name}")

            # 1. Find team's Wikipedia page via OpenSearch
            search_url = (
                f"{WIKI_API}?action=opensearch"
                f"&search={urllib.parse.quote(team.name + ' cricket team')}"
                f"&limit=1&namespace=0&format=json"
            )
            time.sleep(0.5)
            try:
                r = requests.get(search_url, headers=wiki_headers, timeout=10)
                links = r.json()[3] if r.status_code == 200 else []
                if not links:
                    self.stdout.write(self.style.WARNING("  No Wikipedia page found."))
                    continue
                wiki_url = links[0]
                page_resp = requests.get(wiki_url, headers=wiki_headers, timeout=15)
                soup = BeautifulSoup(page_resp.text, 'html.parser')
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Error fetching Wikipedia data: {e}"))
                continue

            # 2. Locate squad table (strict: wikitable with batting/bowling headers)
            squad_table = None
            for table in soup.find_all('table', class_=re.compile(r'wikitable')):
                ths = " ".join(th.get_text().lower() for th in table.find_all('th'))
                if ("player" in ths or "name" in ths) and ("batting" in ths or "bowling" in ths):
                    squad_table = table
                    break

            if not squad_table:
                self.stdout.write(self.style.WARNING("  No valid squad table found."))
                continue

            # 3. Parse rows -> collect player data list (do NOT save to DB yet)
            player_list = []  # each entry: {fullname, href, page_title, jersey_number, position}
            for row in squad_table.find_all('tr'):
                tds = row.find_all('td')
                if len(tds) < 2:
                    continue

                # Name cell: first td in first 3 cols that links to a /wiki/ person page
                name_cell = None
                player_href = ""
                for td in tds[:3]:
                    a = td.find('a')
                    if not a:
                        continue
                    href = a.get('href', '')
                    if '/wiki/' in href and ':' not in href.split('/wiki/', 1)[1]:
                        name_cell = td
                        player_href = href
                        break
                if not name_cell:
                    continue

                a_tag = name_cell.find('a')
                fullname = re.sub(r'\s*\([^)]*\)', '', a_tag.get_text()).strip()

                # Must look like "Firstname Lastname" (at least two words, alpha chars)
                if not re.match(r'^[A-Za-z][A-Za-z.\'\-]*(\s+[A-Za-z][A-Za-z.\'\-]*)+$', fullname):
                    continue
                if len(fullname) < 4:
                    continue

                # Jersey number (from 2nd cell)
                jersey_number = None
                m = re.search(r'\b\d{1,3}\b', tds[1].get_text().strip())
                if m:
                    jersey_number = int(m.group(0))

                # Position/role
                position = "All-rounder"
                ROLE_KEYWORDS = {
                    "batsman", "bowler", "wicket-keeper", "all-rounder",
                    "wicketkeeper batter", "batter",
                    "bowling all-rounder", "batting all-rounder",
                }
                for td in tds:
                    if td.get_text().strip().lower() in ROLE_KEYWORDS:
                        position = td.get_text().strip()
                        break

                page_title = self.extract_title(player_href)
                player_list.append({
                    "fullname": fullname,
                    "page_title": page_title,
                    "jersey_number": jersey_number,
                    "position": position,
                })

            self.stdout.write(f"  Collected {len(player_list)} valid players.")

            if not player_list:
                continue

            # 4. Batch-fetch Wikipedia thumbnails for all players in this team
            titles = [p["page_title"] for p in player_list if p["page_title"]]
            thumb_map = self.fetch_thumbnails(titles)  # key: lowercased title

            # 5. Save (or dry-print) each player with their thumbnail URL
            for player in player_list:
                page_title = player["page_title"]
                # Try exact title match; fallback to URL-decoded variant
                thumbnail_url = (
                    thumb_map.get((page_title or "").lower())
                    or thumb_map.get(urllib.parse.unquote(page_title or "").lower())
                    or ""
                )

                fullname     = player["fullname"]
                jersey_number = player["jersey_number"]
                position     = player["position"]

                self.stdout.write(
                    f"    Player: {fullname} | Jersey: {jersey_number} | "
                    f"Role: {position} | Thumbnail: {thumbnail_url or '(none)'}"
                )

                if dry_run:
                    continue

                parts      = fullname.split(' ')
                first_name = parts[0]
                last_name  = " ".join(parts[1:]) if len(parts) > 1 else ""

                try:
                    ae, created = Entity.objects.update_or_create(
                        api_source='wikipedia',
                        external_id=f"cricket_wp_{team.id}_{self.normalize_string(fullname)}",
                        defaults={
                            'type': 'athlete',
                            'name': fullname,
                            'sport': 'cricket',
                            'has_api_data': True,
                            'is_active': True,
                            'logo_url': thumbnail_url,
                        }
                    )
                    Athlete.objects.update_or_create(
                        entity=ae,
                        defaults={
                            'first_name': first_name,
                            'last_name': last_name,
                            'nationality': "",
                            'position': position,
                            'jersey_number': jersey_number,
                            'current_team': team,
                        }
                    )
                    if created:
                        created_count += 1
                    else:
                        updated_count += 1
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"      Failed to save {fullname}: {e}"))

            self.stdout.write(f"  Processed {len(player_list)} players for {team.name}")

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"\nCricket backfill completed! Created {created_count}, updated {updated_count}."
            ))
        else:
            self.stdout.write(self.style.SUCCESS("\nDry-run completed."))

    def normalize_string(self, val: str) -> str:
        return re.sub(r'[^a-z0-9]', '', val.lower())