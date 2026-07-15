import requests
import time
import re
import urllib.parse
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from apps.entity.models import Entity, Athlete

class Command(BaseCommand):
    help = "Seed horse racing jockey profiles from Wikipedia List of jockeys"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run the command without saving anything to the database'
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=100,
            help='Max number of jockeys to process (default: 100)'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']
        if dry_run:
            self.stdout.write(self.style.WARNING("=== DRY RUN MODE: Database changes will not be saved ==="))

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        # --- Step 1: Scrape jockey names from Wikipedia List of jockeys ---
        list_url = "https://en.wikipedia.org/wiki/List_of_jockeys"
        self.stdout.write(f"Fetching jockey list from {list_url}...")
        try:
            resp = requests.get(list_url, headers=headers, timeout=15)
            if resp.status_code != 200:
                self.stdout.write(self.style.ERROR(f"Failed to fetch jockey list (HTTP {resp.status_code})"))
                return
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to make request: {e}"))
            return

        # The page is an A-Z alphabetical list with <a> links to individual jockey articles
        content = soup.find("div", id="mw-content-text")
        jockeys = []
        seen = set()
        skip_titles = {
            "Jockey", "Horse racing", "Cross-country riding", "Field hunter",
            "Fox hunting", "Hunter pacing", "Mounted orienteering",
            "Pleasure riding", "Trail riding",
            "Techniques de Randonnée Équestre de Compétition",
            "List of historical horses", "Women in equestrianism",
        }
        for a in content.find_all("a", href=True):
            href = a.get("href", "")
            title = a.get("title", "")
            text = a.get_text(strip=True)
            # Only person article links
            if (
                text and len(text) > 3 and
                "wiki" in href and
                not any(ns in href for ns in ["Category:", "File:", "Wikipedia:", "Help:", "Special:", "Template:"]) and
                not href.startswith("#") and
                title not in skip_titles and
                text not in skip_titles and
                text not in seen
            ):
                seen.add(text)
                # Handle both absolute (//en.wikipedia.org/wiki/...) and relative (/wiki/...) hrefs
                if href.startswith("//"):
                    full_url = "https:" + href
                elif href.startswith("/"):
                    full_url = "https://en.wikipedia.org" + href
                else:
                    full_url = href
                jockeys.append((text, full_url))

        self.stdout.write(f"Found {len(jockeys)} jockeys on the list page. Processing up to {limit}.")
        jockeys = jockeys[:limit]

        if not jockeys:
            self.stdout.write(self.style.WARNING("No jockeys extracted from Wikipedia list."))
            return

        created_count = 0
        updated_count = 0

        for idx, (jockey, wiki_url) in enumerate(jockeys):
            self.stdout.write(f"\nProcessing Jockey ({idx+1}/{len(jockeys)}): {jockey}")

            time.sleep(0.4)  # Rate limiting safety
            try:
                # Fetch individual jockey Wikipedia page directly (URL already known)
                page_resp = requests.get(wiki_url, headers=headers, timeout=15)
                if page_resp.status_code != 200:
                    self.stdout.write(self.style.WARNING(f"  Failed to fetch Wikipedia page (HTTP {page_resp.status_code})"))
                    continue
                soup = BeautifulSoup(page_resp.text, "html.parser")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Error fetching Wikipedia data: {e}"))
                continue

            # Parse infobox for nationality/details
            country = "Neutral"
            infobox = soup.find("table", class_=re.compile(r"infobox"))
            if infobox:
                rows = infobox.find_all("tr")
                for r in rows:
                    th = r.find("th")
                    td = r.find("td")
                    if th and td:
                        th_text = th.get_text().strip().lower()
                        if "nationality" in th_text or "country" in th_text or "citizenship" in th_text:
                            for sup in td.find_all(["sup", "cite"]):
                                sup.decompose()
                            raw = td.get_text(" ").strip()
                            # Remove ALL bracketed content e.g. [2], [ 2 ], [note 1]
                            raw = re.sub(r"\[[^\]]*\]", "", raw)
                            raw = re.sub(r"\s+", " ", raw).strip()
                            country = raw
                            break

            # Fallback: check flagicon in infobox
            if country == "Neutral" and infobox:
                flagicon = infobox.find(class_="flagicon")
                if flagicon:
                    img = flagicon.find("img")
                    if img and "alt" in img.attrs:
                        country = img.attrs["alt"].strip()

            # Fallback: extract country from the "born" field (last comma-separated segment)
            if country == "Neutral" and infobox:
                for r in infobox.find_all("tr"):
                    th = r.find("th")
                    td = r.find("td")
                    if th and td and "born" in th.get_text().strip().lower():
                        for sup in td.find_all(["sup", "cite"]):
                            sup.decompose()
                        born_text = td.get_text(" ").strip()
                        # Remove ALL bracketed content e.g. [2], [ 2 ], [note 1]
                        born_text = re.sub(r"\[[^\]]*\]", "", born_text)
                        # Remove parenthetical date/age blocks e.g. "(1970-12-15)15 December 1970(age 55)"
                        born_text = re.sub(r"\(age\s*\d+\)", "", born_text, flags=re.IGNORECASE)
                        born_text = re.sub(r"\(\d{4}[^)]*\)", "", born_text)
                        born_text = re.sub(r"\d{1,2}\s+\w+\s+\d{4}", "", born_text)
                        born_text = re.sub(r"\s+", " ", born_text).strip()
                        parts_born = [p.strip() for p in born_text.split(",") if p.strip()]
                        if parts_born:
                            country = parts_born[-1].strip()
                        break

            self.stdout.write(f"  Nationality: {country}")

            # Parse first/last name
            parts = jockey.split(" ")
            first_name = parts[0]
            last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

            if dry_run:
                self.stdout.write(f"  [Dry-run] Would seed: {jockey} ({country})")
                continue

            try:
                ae, created = Entity.objects.update_or_create(
                    api_source="wikipedia",
                    external_id=f"jockey_wp_{self.normalize_string(jockey)}",
                    defaults={
                        "type": "athlete",
                        "name": jockey,
                        "sport": "horse_racing",
                        "has_api_data": True,
                        "is_active": True,
                    }
                )
                Athlete.objects.update_or_create(
                    entity=ae,
                    defaults={
                        "first_name": first_name,
                        "last_name": last_name,
                        "nationality": country,
                        "position": "Jockey",
                    }
                )
                if created:
                    created_count += 1
                else:
                    updated_count += 1
            except Exception as save_err:
                self.stdout.write(self.style.ERROR(f"    Failed to save jockey {jockey}: {save_err}"))

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"\nJockey seeding completed! Created {created_count} athletes, updated {updated_count} athletes."
            ))
        else:
            self.stdout.write(self.style.SUCCESS("\nJockey seeding dry-run completed successfully!"))

    def normalize_string(self, val: str) -> str:
        val = val.lower()
        val = re.sub(r'[^a-z0-9]', '', val)
        return val
