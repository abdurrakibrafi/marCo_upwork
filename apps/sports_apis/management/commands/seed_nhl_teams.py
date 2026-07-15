import requests
from django.core.management.base import BaseCommand
from apps.entity.models import Entity, Team

class Command(BaseCommand):
    help = "Seed 32 official NHL teams into the database from the official NHL API"

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

        url = "https://api-web.nhle.com/v1/standings/now"
        self.stdout.write(f"Fetching NHL standings from {url}...")

        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code != 200:
                self.stdout.write(self.style.ERROR(f"Failed to fetch standings, HTTP status: {resp.status_code}"))
                return
            data = resp.json()
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Request failed: {e}"))
            return

        standings = data.get('standings', [])
        self.stdout.write(f"Found {len(standings)} teams in official NHL standings.")

        created_count = 0
        updated_count = 0

        for t in standings:
            abbrev = t.get('teamAbbrev', {}).get('default')
            name = t.get('teamName', {}).get('default')
            logo = t.get('teamLogo', '')

            if not abbrev or not name:
                self.stdout.write(self.style.WARNING("Skipping invalid team entry (missing name or abbreviation)"))
                continue

            self.stdout.write(f"Processing NHL Team: {name} ({abbrev})")

            if dry_run:
                self.stdout.write(f"  [Dry-run] Would create/update Entity and Team detail for: {name} (Abbrev: {abbrev}, Logo: {logo})")
                continue

            try:
                entity, created = Entity.objects.update_or_create(
                    type='team',
                    sport='hockey',
                    api_source='nhl_api',
                    external_id=abbrev,
                    defaults={
                        'name': name,
                        'logo_url': logo,
                        'has_api_data': True,
                        'is_active': True,
                    }
                )
                
                # Make sure the Team detail object exists
                Team.objects.get_or_create(entity=entity)

                if created:
                    created_count += 1
                    self.stdout.write(self.style.SUCCESS(f"  Successfully created team: {name}"))
                else:
                    updated_count += 1
                    self.stdout.write(f"  Successfully updated team: {name}")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Failed to save team {name}: {e}"))

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f"\nNHL Seeding completed! Created {created_count} teams, updated {updated_count} teams."))
        else:
            self.stdout.write(self.style.SUCCESS("\nNHL Seeding dry-run completed successfully!"))
