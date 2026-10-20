import logging
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from apps.event.models import (
    Event, EventTimeline, EventLineup, EventStatistics, EventPlayerStats, EventHighlight
)
from apps.event.views import _teams_name_match

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Find and merge duplicate sporting events in the database (e.g. from StatPal vs TheSportsDB)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--sport',
            type=str,
            default='all',
            help="Filter by sport (e.g. 'basketball', 'nba', 'soccer', 'all'). Default is 'all'."
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Perform a dry run without actually deleting duplicate records."
        )

    def handle(self, *args, **options):
        sport_filter = options.get('sport', 'all').lower()
        dry_run = options.get('dry_run', False)

        qs = Event.objects.exclude(home_entity__isnull=True).exclude(away_entity__isnull=True).select_related(
            'home_entity', 'away_entity', 'league'
        ).order_by('start_time')

        if sport_filter != 'all':
            if sport_filter in ('basketball', 'nba'):
                qs = qs.filter(sport__in=['basketball', 'nba'])
            elif sport_filter in ('american_football', 'football', 'nfl'):
                qs = qs.filter(sport__in=['american_football', 'football', 'nfl'])
            elif sport_filter in ('ice_hockey', 'hockey', 'nhl'):
                qs = qs.filter(sport__in=['ice_hockey', 'hockey', 'nhl'])
            elif sport_filter in ('baseball', 'mlb'):
                qs = qs.filter(sport__in=['baseball', 'mlb'])
            else:
                qs = qs.filter(sport=sport_filter)

        self.stdout.write(f"Scanning events for duplicate removal (sport={sport_filter}, dry_run={dry_run})...")

        events = list(qs)
        self.stdout.write(f"Loaded {len(events)} events to inspect.")

        deleted_ids = set()
        duplicates_merged = 0

        for i in range(len(events)):
            e1 = events[i]
            if e1.id in deleted_ids:
                continue

            for j in range(i + 1, min(i + 60, len(events))):
                e2 = events[j]
                if e2.id in deleted_ids:
                    continue

                if not e1.start_time or not e2.start_time:
                    continue

                time_diff = (e2.start_time - e1.start_time).total_seconds()
                if time_diff > 18 * 3600:
                    break

                # Sport check
                sport_matched = (
                    e1.sport == e2.sport
                    or {e1.sport, e2.sport}.issubset({'basketball', 'nba'})
                    or {e1.sport, e2.sport}.issubset({'american_football', 'football', 'nfl'})
                    or {e1.sport, e2.sport}.issubset({'ice_hockey', 'hockey', 'nhl'})
                    or {e1.sport, e2.sport}.issubset({'baseball', 'mlb'})
                )
                if not sport_matched:
                    continue

                # Team check
                h1_id = e1.home_entity.canonical_entity_id or e1.home_entity_id
                a1_id = e1.away_entity.canonical_entity_id or e1.away_entity_id
                h2_id = e2.home_entity.canonical_entity_id or e2.home_entity_id
                a2_id = e2.away_entity.canonical_entity_id or e2.away_entity_id

                sig1 = tuple(sorted([str(h1_id), str(a1_id)]))
                sig2 = tuple(sorted([str(h2_id), str(a2_id)]))

                teams_matched = False
                if sig1 == sig2:
                    teams_matched = True
                else:
                    h1_name = e1.home_entity.name
                    a1_name = e1.away_entity.name
                    h2_name = e2.home_entity.name
                    a2_name = e2.away_entity.name
                    if (_teams_name_match(h1_name, h2_name) and _teams_name_match(a1_name, a2_name)) or \
                       (_teams_name_match(h1_name, a2_name) and _teams_name_match(a1_name, h2_name)):
                        teams_matched = True

                if not teams_matched:
                    continue

                # Decide primary and secondary
                # Prefer statpal over thesportsdb
                # Prefer completed/live or score-rich record
                e1_score = e1.home_score is not None or e1.away_score is not None
                e2_score = e2.home_score is not None or e2.away_score is not None

                if e2.api_source == 'statpal' and e1.api_source != 'statpal':
                    primary, secondary = e2, e1
                elif e1.api_source == 'statpal' and e2.api_source != 'statpal':
                    primary, secondary = e1, e2
                elif not e1_score and e2_score:
                    primary, secondary = e2, e1
                elif e1.status == 'upcoming' and e2.status in ('live', 'completed'):
                    primary, secondary = e2, e1
                else:
                    primary, secondary = e1, e2

                primary_id = primary.id
                secondary_id = secondary.id
                was_e1_deleted = (secondary.id == e1.id)

                if not dry_run:
                    with transaction.atomic():
                        # Re-point child entities to primary before deleting secondary
                        EventTimeline.objects.filter(event_id=secondary_id).update(event_id=primary_id)
                        EventHighlight.objects.filter(event_id=secondary_id).update(event_id=primary_id)

                        for lineup in EventLineup.objects.filter(event_id=secondary_id):
                            if not EventLineup.objects.filter(event_id=primary_id, player_id=lineup.player_id).exists():
                                lineup.event_id = primary_id
                                lineup.save(update_fields=['event'])
                            else:
                                lineup.delete()

                        for stat in EventStatistics.objects.filter(event_id=secondary_id):
                            if not EventStatistics.objects.filter(event_id=primary_id, team_id=stat.team_id).exists():
                                stat.event_id = primary_id
                                stat.save(update_fields=['event'])
                            else:
                                stat.delete()

                        for ps in EventPlayerStats.objects.filter(event_id=secondary_id):
                            if not EventPlayerStats.objects.filter(event_id=primary_id, player_id=ps.player_id).exists():
                                ps.event_id = primary_id
                                ps.save(update_fields=['event'])
                            else:
                                ps.delete()

                        # Copy score or venue if primary was missing it
                        updates = []
                        if primary.home_score is None and secondary.home_score is not None:
                            primary.home_score = secondary.home_score
                            primary.away_score = secondary.away_score
                            updates.extend(['home_score', 'away_score'])
                        if not primary.venue_name and secondary.venue_name:
                            primary.venue_name = secondary.venue_name
                            updates.append('venue_name')
                        if updates:
                            primary.save(update_fields=updates)

                        Event.objects.filter(id=secondary_id).delete()

                deleted_ids.add(secondary_id)
                duplicates_merged += 1
                self.stdout.write(
                    f"Merged duplicate: removed Event #{secondary_id} ({secondary.api_source}) "
                    f"into primary Event #{primary_id} ({primary.api_source}) - "
                    f"{primary.home_entity.name} vs {primary.away_entity.name} ({primary.start_time})"
                )

                if was_e1_deleted:
                    break

        status_msg = "DRY-RUN COMPLETE" if dry_run else "SUCCESS"
        self.stdout.write(self.style.SUCCESS(
            f"\n{status_msg}: Merged and cleaned up {duplicates_merged} duplicate events."
        ))
