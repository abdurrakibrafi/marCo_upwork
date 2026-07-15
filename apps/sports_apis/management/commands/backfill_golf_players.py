import requests
import time
from django.core.management.base import BaseCommand
from apps.entity.models import Entity, Athlete


class Command(BaseCommand):
    help = "Seed top 100 golf players from the official OWGR JSON API"

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

        api_url = "https://apiweb.owgr.com/api/owgr/rankings/getRankings"
        params = {"pageSize": 100, "pageNumber": 1}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://www.owgr.com/ranking"
        }

        self.stdout.write(f"Fetching OWGR top 100 golfers from {api_url}...")
        try:
            resp = requests.get(api_url, params=params, headers=headers, timeout=15)
            if resp.status_code != 200:
                self.stdout.write(self.style.ERROR(f"Failed to fetch OWGR API, HTTP status: {resp.status_code}"))
                return
            data = resp.json()
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to make request: {e}"))
            return

        rankings_list = data.get("rankingsList", [])
        if not rankings_list:
            self.stdout.write(self.style.ERROR("No rankings data returned from OWGR API."))
            return

        self.stdout.write(f"Found {len(rankings_list)} golfers in the OWGR top 100.")

        created_count = 0
        updated_count = 0

        for entry in rankings_list:
            rank = entry.get("rank")
            player = entry.get("player", {})
            player_id = str(player.get("id", ""))
            first_name = (player.get("firstName") or "").strip()
            last_name = (player.get("lastName") or "").strip()
            fullname = (player.get("fullName") or f"{first_name} {last_name}").strip()
            country_obj = player.get("country") or {}
            country = (country_obj.get("name") or "Neutral").strip()

            if not fullname:
                continue

            if dry_run:
                self.stdout.write(f"  [Dry-run] Rank {rank}: {fullname} ({country}, ID: {player_id})")
                continue

            try:
                ae, created = Entity.objects.update_or_create(
                    api_source="owgr",
                    external_id=f"golf_owgr_{player_id}",
                    defaults={
                        "type": "athlete",
                        "name": fullname,
                        "sport": "golf",
                        "has_api_data": True,
                        "is_active": True,
                    }
                )

                defaults = {
                    "first_name": first_name,
                    "last_name": last_name,
                    "position": "Golfer",
                }
                if country != "Neutral":
                    defaults["nationality"] = country

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
            self.stdout.write(self.style.SUCCESS(
                f"\nGolf backfill completed! Created {created_count} athletes, updated {updated_count} athletes."
            ))
        else:
            self.stdout.write(self.style.SUCCESS("\nGolf backfill dry-run completed successfully!"))
