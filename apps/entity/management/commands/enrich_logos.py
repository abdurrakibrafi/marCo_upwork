import time
from django.core.management.base import BaseCommand
from apps.entity.models import Entity
from apps.sports_apis.services.thesportsdb import thesportsdb_service

class Command(BaseCommand):
    help = 'Enrich missing team and league logos from TheSportsDB API'

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

    def handle(self, *args, **options):
        limit = options['limit']
        sport = options['sport']

        self.stdout.write("Finding entities with missing logos (logo_url is empty)...")
        
        query = Entity.objects.filter(logo_url='')
        if sport:
            query = query.filter(sport=sport.lower())
            
        query = query.order_by('type', 'sport', 'name')
        total_count = query.count()
        self.stdout.write(f"Found {total_count} matching entities in database.")

        if limit:
            query = query[:limit]
            self.stdout.write(f"Limiting to first {limit} entities.")

        updated = 0
        skipped = 0
        not_found = 0

        for entity in query:
            name = entity.name
            self.stdout.write(f"\nSearching logo for [{entity.type.upper()}] {name} ({entity.sport})...")
            
            # Skip obscure cricket tour series - TheSportsDB won't have them
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
                    logo_url = thesportsdb_service.get_team_badge(name)

                    # If no exact match, try stripping common suffixes
                    if not logo_url:
                        stripped = (name.replace(' Women', '')
                                    .replace(' Men', '')
                                    .replace(' FC', '')
                                    .replace(' CF', '')
                                    .replace(' SC', '')
                                    .strip())
                        if stripped != name:
                            logo_url = thesportsdb_service.get_team_badge(stripped)

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

            # Respect rate limit — 2.5s delay to stay well within limits
            time.sleep(2.5)

        self.stdout.write(self.style.SUCCESS(
            f"\nFinished: {updated} logos updated, {skipped} skipped, {not_found} not found."
        ))
