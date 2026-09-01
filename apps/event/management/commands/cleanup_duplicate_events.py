from django.core.management.base import BaseCommand
from django.db.models import Count
from apps.event.models import (
    Event, EventTimeline, EventLineup,
    EventStatistics, EventPlayerStats, EventHighlight,
)


class Command(BaseCommand):
    help = "Safely merge and delete duplicate Event records in database."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simulate cleanup without deleting records.',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        self.stdout.write(self.style.NOTICE(f"Starting duplicate event cleanup (dry_run={dry_run})..."))

        # Find duplicate event groups based on home_entity, away_entity and date
        dup_groups = (
            Event.objects.filter(home_entity__isnull=False, away_entity__isnull=False)
            .values('home_entity_id', 'away_entity_id', 'start_time__date')
            .annotate(count=Count('id'))
            .filter(count__gt=1)
        )

        total_groups = dup_groups.count()
        self.stdout.write(f"Found {total_groups} duplicate match groups.")

        deleted_count = 0
        merged_count = 0

        for group in dup_groups:
            h_id = group['home_entity_id']
            a_id = group['away_entity_id']
            d = group['start_time__date']

            events = list(
                Event.objects.filter(
                    home_entity_id=h_id,
                    away_entity_id=a_id,
                    start_time__date=d,
                ).order_by('-updated_at')
            )

            if len(events) <= 1:
                continue

            # Pick the best keeper event
            keeper = None
            for ev in events:
                has_score = ev.home_score is not None or ev.away_score is not None
                is_active = ev.status in ('live', 'completed')
                is_statpal = ev.api_source == 'statpal'

                if keeper is None:
                    keeper = ev
                    continue

                keeper_score = keeper.home_score is not None or keeper.away_score is not None
                keeper_active = keeper.status in ('live', 'completed')

                if (not keeper_score and has_score) or \
                   (not keeper_active and is_active) or \
                   (keeper.api_source != 'statpal' and is_statpal and not keeper_score):
                    keeper = ev

            duplicates_to_delete = [ev for ev in events if ev.id != keeper.id]

            if not dry_run:
                for dup in duplicates_to_delete:
                    try:
                        dup.delete()
                        deleted_count += 1
                    except Exception as e:
                        self.stderr.write(f"Error deleting event {dup.id}: {e}")
                merged_count += 1
            else:
                deleted_count += len(duplicates_to_delete)
                merged_count += 1

        action_word = "Would delete" if dry_run else "Successfully deleted"
        self.stdout.write(
            self.style.SUCCESS(
                f"Completed! {action_word} {deleted_count} duplicate events across {merged_count} match groups."
            )
        )
