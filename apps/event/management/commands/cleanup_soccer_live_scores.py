from django.core.management.base import BaseCommand
from apps.score.models import LiveScore

class Command(BaseCommand):
    help = "Cleanup soccer live scores where has_live_stats is 'False'"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview matches to be deleted without actually modifying the database',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        # Get all soccer LiveScore records
        soccer_scores = LiveScore.objects.filter(sport="soccer")
        
        total_scanned = soccer_scores.count()
        deleted_count = 0

        self.stdout.write(self.style.NOTICE(f"Scanning {total_scanned} soccer live scores..."))

        for score in soccer_scores:
            raw_data = score.raw_data or {}
            # Safely check has_live_stats flag
            has_live_stats_val = raw_data.get("has_live_stats", "True")
            
            if str(has_live_stats_val).strip().lower() == "false":
                self.stdout.write(
                    self.style.WARNING(
                        f"Targeted: ID={score.id} | ExternalID={score.external_id} | {score.home_team} vs {score.away_team} | Status={score.status}"
                    )
                )
                if not dry_run:
                    score.delete()
                deleted_count += 1

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"[DRY-RUN DONE] Would delete {deleted_count} soccer live scores out of {total_scanned} scanned."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"[DONE] Successfully deleted {deleted_count} soccer live scores out of {total_scanned} scanned."
                )
            )
