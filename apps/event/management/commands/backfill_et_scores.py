from django.core.management.base import BaseCommand
from django.db.models import Q
from apps.event.models import Event, EventStatistics

def safe_int(val):
    if val is None or str(val).strip() == '':
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None

class Command(BaseCommand):
    help = "Backfill extra time scores to home_score and away_score using Option B (sum ft + et)"

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

        stats_qs = EventStatistics.objects.filter(
            Q(stats__has_key='et_home') | Q(stats__has_key='et_away')
        ).select_related('event')

        # Filter for soccer events
        stats_qs = stats_qs.filter(event__sport='soccer')

        corrected_count = 0
        inconsistencies_count = 0
        processed_event_ids = set()

        self.stdout.write(self.style.NOTICE(f"Found {stats_qs.count()} event statistics matching extra time criteria."))

        for stats_obj in stats_qs:
            event = stats_obj.event
            if event.id in processed_event_ids:
                continue
            processed_event_ids.add(event.id)

            status_detail = event.status_detail or ""
            status_detail_lower = status_detail.lower().strip()

            # Check status keywords
            matched_by_status_kw = any(kw in status_detail_lower for kw in ['aet', 'pen', 'extra'])

            stats_dict = stats_obj.stats or {}
            ft_home = stats_dict.get('ft_home')
            ft_away = stats_dict.get('ft_away')
            et_home = stats_dict.get('et_home')
            et_away = stats_dict.get('et_away')

            et_present = (et_home is not None or et_away is not None)
            if not et_present:
                continue

            h_et = safe_int(et_home) or 0
            a_et = safe_int(et_away) or 0
            et_not_both_zero = (h_et != 0 or a_et != 0)

            # Decide if we should process this event
            if not (matched_by_status_kw or et_not_both_zero):
                continue

            # Log any event matched by et-data but not by status_detail keyword
            if et_not_both_zero and not matched_by_status_kw:
                inconsistencies_count += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"[INCONSISTENCY] Event ID {event.id} has extra time data ({h_et}-{a_et}) but status_detail is '{status_detail}'."
                    )
                )

            # Option B calculation: sum ft + et
            ft_h = safe_int(ft_home) or 0
            ft_a = safe_int(ft_away) or 0

            new_home = ft_h + h_et
            new_away = ft_a + a_et

            old_home = event.home_score
            old_away = event.away_score

            mismatch = (old_home != new_home or old_away != new_away)

            if mismatch:
                corrected_count += 1
                self.stdout.write(
                    f"Event ID {event.id}: "
                    f"Status='{status_detail}', "
                    f"FT={ft_h}-{ft_a}, ET={h_et}-{a_et} | "
                    f"Old Score={old_home}-{old_away} -> New Score={new_home}-{new_away}"
                )

                if commit:
                    event.home_score = new_home
                    event.away_score = new_away
                    event.save(update_fields=['home_score', 'away_score'])

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"[DRY-RUN DONE] Previewed corrections for {corrected_count} events. Found {inconsistencies_count} status/data naming inconsistencies."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"[DONE] Successfully corrected {corrected_count} events. Logged {inconsistencies_count} status/data naming inconsistencies."
                )
            )
