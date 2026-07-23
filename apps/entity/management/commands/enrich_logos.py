import time
from django.core.management.base import BaseCommand
from django.db.models import Q
from apps.entity.models import Entity
from apps.event.models import Event
from apps.nest.models import UserNest
from apps.sports_apis.services.thesportsdb import thesportsdb_service

class Command(BaseCommand):
    help = 'Enrich missing team and league logos from TheSportsDB API with priority ordering and sport isolation'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit the number of entities processed'
        )
        parser.add_argument(
            '--sport',
            type=str,
            default=None,
            help='Filter by specific sport (e.g. basketball, cricket, soccer)'
        )
        parser.add_argument(
            '--popular-only',
            action='store_true',
            help='Only process entities associated with events or user nests'
        )
        parser.add_argument(
            '--force-refetch',
            action='store_true',
            help='Force re-fetch and overwrite logos even if logo_url is already present'
        )

    def handle(self, *args, **options):
        limit = options['limit']
        sport = options['sport']
        popular_only = options['popular_only']
        force_refetch = options['force_refetch']

        if force_refetch:
            self.stdout.write(self.style.WARNING("=== FORCE RE-FETCH MODE: Overwriting existing logos ==="))
            query = Entity.objects.all()
        else:
            self.stdout.write("Finding entities with missing logos...")
            query = Entity.objects.filter(Q(logo_url='') | Q(logo_url__contains='statpal.io'))

        if sport:
            query = query.filter(sport=sport.lower())

        # Collect popular entity IDs (from active/upcoming Event and UserNest)
        event_entity_ids = set(
            Event.objects.exclude(status='completed')
            .values_list('home_entity_id', flat=True)
        ) | set(
            Event.objects.exclude(status='completed')
            .values_list('away_entity_id', flat=True)
        ) | set(
            Event.objects.exclude(status='completed')
            .values_list('league_id', flat=True)
        )
        event_entity_ids.discard(None)

        nest_entity_ids = set(UserNest.objects.values_list('entity_id', flat=True))
        popular_ids = event_entity_ids | nest_entity_ids

        if popular_only:
            query = query.filter(id__in=popular_ids)
            self.stdout.write(self.style.SUCCESS(f"Filtered {query.count()} popular/active entities."))

        entities = list(query)
        # Prioritize: 1. Popular entities (in events/nest), 2. High follower count, 3. Name
        entities.sort(key=lambda e: (0 if e.id in popular_ids else 1, -e.follower_count, e.name))

        total_count = len(entities)
        self.stdout.write(f"Found {total_count} matching entities to process.")

        if limit:
            entities = entities[:limit]
            self.stdout.write(f"Limiting to first {limit} entities.")

        updated = 0
        skipped = 0
        not_found = 0

        for entity in entities:
            name = entity.name
            self.stdout.write(f"\nSearching logo for [{entity.type.upper()}] {name} ({entity.sport})...")
            
            skip_keywords = ['tour of', 'under-19', 'under-23', 'u19', 'u23',
                             'emerging', 'a v ', ' a tour', 'tri-series',
                             'women tour', 'invite']
            if any(kw in name.lower() for kw in skip_keywords) and entity.type == 'league':
                self.stdout.write(self.style.WARNING(f"Skipped obscure league/tour: {name}"))
                skipped += 1
                continue

            logo_url = ''

            try:
                if entity.type == 'team':
                    logo_url = thesportsdb_service.get_team_badge(name, sport=entity.sport)

                    if not logo_url:
                        stripped = (name.replace(' Women', '')
                                    .replace(' Men', '')
                                    .replace(' FC', '')
                                    .replace(' CF', '')
                                    .replace(' SC', '')
                                    .strip())
                        if stripped != name:
                            logo_url = thesportsdb_service.get_team_badge(stripped, sport=entity.sport)

                elif entity.type == 'league':
                    major_keywords = ['ipl', 'bbl', 'cpl', 'psl', 'icc', 'test',
                                      'world cup', 'champions', 'premier', 'super',
                                      'league', 't20', 'one day', 'odi']
                    if any(kw in name.lower() for kw in major_keywords):
                        logo_url = thesportsdb_service.get_league_badge(name, entity.sport)

                elif entity.type == 'athlete':
                    logo_url = thesportsdb_service.get_player_headshot(name)

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error calling TheSportsDB for {name}: {e}"))

            if logo_url:
                entity.logo_url = logo_url
                entity.save(update_fields=['logo_url'])
                updated += 1
                self.stdout.write(self.style.SUCCESS(f"✓ Found & saved: {logo_url}"))
            else:
                not_found += 1
                self.stdout.write(self.style.NOTICE(f"✗ Logo not found on TheSportsDB for {name}"))

            # Respect rate limit delay
            time.sleep(2.5)

        self.stdout.write(self.style.SUCCESS(
            f"\nFinished: {updated} logos updated, {skipped} skipped, {not_found} not found."
        ))
