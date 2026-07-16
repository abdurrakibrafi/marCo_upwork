from django.core.management.base import BaseCommand
from apps.entity.models import Entity

class Command(BaseCommand):
    help = "Clean up broken StatPal logo URLs for non-soccer teams in the database"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without saving to the database'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING("=== DRY RUN MODE ==="))

        # Find all team entities of non-soccer sports that have a statpal.io logo URL
        broken_teams = Entity.objects.filter(
            type='team',
            logo_url__contains='statpal.io'
        ).exclude(sport='soccer')

        self.stdout.write(f"Found {broken_teams.count()} non-soccer teams with broken StatPal logo URLs.")

        cleaned_count = 0
        for team in broken_teams:
            self.stdout.write(f"  Clearing logo for {team.name} (Sport: {team.sport}, Logo: {team.logo_url})")
            if not dry_run:
                team.logo_url = ""
                team.save(update_fields=['logo_url'])
                cleaned_count += 1

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f"Successfully cleared broken logo URLs for {cleaned_count} teams."))
        else:
            self.stdout.write(self.style.SUCCESS("Dry-run preview completed."))
