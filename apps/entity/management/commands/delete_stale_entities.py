import json
from collections import defaultdict
from django.core.management.base import BaseCommand
from django.db import transaction
from apps.entity.models import Entity, Athlete, Team
from apps.feed.models import FeedItem, Source, RSSSource
from apps.nest.models import UserNest, RecentSearch
from apps.event.models import Event, EventTimeline, EventLineup, EventStatistics, EventPlayerStats

class Command(BaseCommand):
    help = 'Safely identify and delete stale non-StatPal entities.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--commit',
            action='store_true',
            help='Actually commit the deletions to the database'
        )

    def handle(self, *args, **options):
        commit = options['commit']

        self.stdout.write("Gathering active database references for exclusions...")

        # Pre-fetch all active references in memory to optimize query time
        feeditem_entity_ids = set(FeedItem.entities.through.objects.values_list('entity_id', flat=True))
        source_entity_ids = set(Source.entities.through.objects.values_list('entity_id', flat=True))
        rsssource_entity_ids = set(RSSSource.entities.through.objects.values_list('entity_id', flat=True))
        usernest_entity_ids = set(UserNest.objects.values_list('entity_id', flat=True))
        recentsearch_entity_ids = set(RecentSearch.objects.values_list('entity_id', flat=True))

        # Event-related references
        event_home_ids = set(Event.objects.values_list('home_entity_id', flat=True))
        event_away_ids = set(Event.objects.exclude(away_entity_id__isnull=True).values_list('away_entity_id', flat=True))
        event_league_ids = set(Event.objects.exclude(league_id__isnull=True).values_list('league_id', flat=True))

        # Additional Event details references (for maximum safety)
        timeline_team_ids = set(EventTimeline.objects.exclude(team_id__isnull=True).values_list('team_id', flat=True))
        timeline_player_ids = set(EventTimeline.objects.exclude(player_id__isnull=True).values_list('player_id', flat=True))
        lineup_team_ids = set(EventLineup.objects.values_list('team_id', flat=True))
        lineup_player_ids = set(EventLineup.objects.values_list('player_id', flat=True))
        statistics_team_ids = set(EventStatistics.objects.values_list('team_id', flat=True))
        player_stats_player_ids = set(EventPlayerStats.objects.values_list('player_id', flat=True))
        player_stats_team_ids = set(EventPlayerStats.objects.values_list('team_id', flat=True))

        # Cross-entity references
        athlete_team_ids = set(Athlete.objects.exclude(current_team_id__isnull=True).values_list('current_team_id', flat=True))
        team_league_ids = set(Team.objects.exclude(league_id__isnull=True).values_list('league_id', flat=True))
        canonical_entity_ids = set(Entity.objects.exclude(canonical_entity_id__isnull=True).values_list('canonical_entity_id', flat=True))

        self.stdout.write("Querying target stale entities (api_source != 'statpal')...")
        
        target_sports = ['basketball', 'cricket', 'soccer']
        targets = Entity.objects.filter(
            sport__in=target_sports
        ).exclude(
            api_source='statpal'
        )

        total_scanned = targets.count()
        self.stdout.write(f"Scanned {total_scanned} matching entities in database.")

        # Data structure for reporting: (sport, api_source) -> details
        report_data = defaultdict(lambda: {
            'total': 0,
            'excluded_reasons': defaultdict(int),
            'deletable': []
        })

        for ent in targets:
            key = (ent.sport, ent.api_source or 'unknown')
            report_data[key]['total'] += 1

            exclusions = []
            if ent.id in feeditem_entity_ids:
                exclusions.append('feed_feeditem_entities')
            if ent.id in source_entity_ids:
                exclusions.append('feed_source_entities')
            if ent.id in rsssource_entity_ids:
                exclusions.append('feed_rsssource_entities')
            if ent.id in usernest_entity_ids:
                exclusions.append('usernest')
            if ent.id in recentsearch_entity_ids:
                exclusions.append('recentsearch')
            if (ent.id in event_home_ids or 
                ent.id in event_away_ids or 
                ent.id in event_league_ids or
                ent.id in timeline_team_ids or
                ent.id in timeline_player_ids or
                ent.id in lineup_team_ids or
                ent.id in lineup_player_ids or
                ent.id in statistics_team_ids or
                ent.id in player_stats_player_ids or
                ent.id in player_stats_team_ids):
                exclusions.append('event_fixture')
            if ent.id in athlete_team_ids:
                exclusions.append('athlete_current_team')
            if ent.id in team_league_ids:
                exclusions.append('team_league')
            if ent.id in canonical_entity_ids:
                exclusions.append('canonical_entity_reference')

            if exclusions:
                # Log exclusions breakdown
                for reason in exclusions:
                    report_data[key]['excluded_reasons'][reason] += 1
            else:
                # Eligible for deletion
                report_data[key]['deletable'].append(ent)

        # Print Dry-Run Report
        self.stdout.write("\n" + "="*80)
        self.stdout.write("DRY-RUN REPORT: STALE ENTITIES CLEANUP")
        self.stdout.write("="*80)

        grand_total_deletable = 0
        grand_total_excluded = 0

        for key, stats in sorted(report_data.items()):
            sport, api_source = key
            deletable_count = len(stats['deletable'])
            excluded_count = stats['total'] - deletable_count
            grand_total_deletable += deletable_count
            grand_total_excluded += excluded_count

            self.stdout.write(self.style.MIGRATE_HEADING(
                f"\nSport: {sport.upper()} | API Source: {api_source}"
            ))
            self.stdout.write(f"  Total Scanned: {stats['total']}")
            self.stdout.write(f"  Exclusions Breakdowns:")
            if stats['excluded_reasons']:
                for reason, count in sorted(stats['excluded_reasons'].items()):
                    self.stdout.write(f"    - {reason}: {count}")
            else:
                self.stdout.write("    None")
            self.stdout.write(f"  Actually Eligible for Deletion: {deletable_count}")

        self.stdout.write("\n" + "="*80)
        self.stdout.write(f"GRAND TOTAL EXCLUDED: {grand_total_excluded}")
        self.stdout.write(f"GRAND TOTAL ELIGIBLE FOR DELETION: {grand_total_deletable}")
        self.stdout.write("="*80 + "\n")

        if grand_total_deletable == 0:
            self.stdout.write(self.style.SUCCESS("No stale entities found that can be deleted safely."))
            return

        # Prepare list of items to delete
        all_deletable_entities = []
        for key, stats in report_data.items():
            all_deletable_entities.extend(stats['deletable'])

        # Backup deleted entities to JSON file
        backup_filename = 'deleted_entities_backup.json'
        backup_data = [
            {
                'id': ent.id,
                'name': ent.name,
                'sport': ent.sport,
                'api_source': ent.api_source,
                'external_id': ent.external_id
            }
            for ent in all_deletable_entities
        ]

        try:
            with open(backup_filename, 'w', encoding='utf-8') as f:
                json.dump(backup_data, f, indent=4, ensure_ascii=False)
            self.stdout.write(self.style.SUCCESS(
                f"Successfully wrote backup of {len(backup_data)} entities to '{backup_filename}'"
            ))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to write backup file: {e}"))
            if commit:
                self.stdout.write(self.style.ERROR("Aborting commit due to backup logging failure for safety."))
                return

        if not commit:
            self.stdout.write(self.style.WARNING(
                "DRY-RUN ONLY: None of these entities were deleted. Run with '--commit' to execute deletions."
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f"COMMITTING DELETIONS: Deleting {len(all_deletable_entities)} stale entities..."
            ))
            
            with transaction.atomic():
                delete_ids = [ent.id for ent in all_deletable_entities]
                deleted_count, detail = Entity.objects.filter(id__in=delete_ids).delete()
                
            self.stdout.write(self.style.SUCCESS(
                f"Successfully deleted {deleted_count} database rows. (Details: {detail})"
            ))
