from django.core.management.base import BaseCommand
from apps.event.models import Event
from apps.event.tasks import _FINISHED, _populate_statpal_event_details

class Command(BaseCommand):
    help = "Backfill completed status to events stuck as upcoming"

    def add_arguments(self, parser):
        parser.add_argument(
            '--commit',
            action='store_true',
            help='Actually update the database (defaults to dry-run)',
        )

    def handle(self, *args, **options):
        commit = options['commit']
        dry_run = not commit

        if dry_run:
            self.stdout.write(self.style.WARNING("RUNNING IN DRY-RUN MODE - NO CHANGES WILL BE SAVED"))
        else:
            self.stdout.write(self.style.NOTICE("RUNNING IN COMMIT MODE - SAVING TO DATABASE"))

        # Normalize finished keywords
        finished_normalized = {f.lower().strip().rstrip('.') for f in _FINISHED}

        upcoming_events = Event.objects.filter(status='upcoming')
        
        corrected_count = 0

        self.stdout.write(self.style.NOTICE(f"Scanning {upcoming_events.count()} upcoming events..."))

        for event in upcoming_events:
            status_detail = event.status_detail or ""
            sd_norm = status_detail.lower().strip().rstrip('.')

            if sd_norm in finished_normalized or "final" in sd_norm:
                corrected_count += 1
                self.stdout.write(
                    f"Event ID {event.id} ({event.sport}): "
                    f"Status: {event.status} -> completed | status_detail: '{status_detail}'"
                )

                if commit:
                    event.status = 'completed'
                    event.save(update_fields=['status'])
                    if event.sport == 'soccer':
                        try:
                            _populate_statpal_event_details(event)
                        except Exception as e:
                            self.stdout.write(
                                self.style.WARNING(
                                    f"  Failed to populate stats for soccer event {event.id}: {e}"
                                )
                            )

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"[DRY-RUN DONE] Previewed completions for {corrected_count} events."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"[DONE] Successfully completed {corrected_count} events."
                )
            )
