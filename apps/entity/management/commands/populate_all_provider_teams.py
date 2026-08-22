import logging
import requests
from django.core.management.base import BaseCommand
from apps.entity.models import Entity, Team, League
from apps.entity.utils.matcher import get_or_create_precise_entity
from apps.entity.utils.normalizers import normalize_entity_name
from apps.sports_apis.services.thesportsdb import TheSportsDBService
from apps.sports_apis.services.statpal import statpal_service

logger = logging.getLogger(__name__)

# List of major global leagues to populate teams from
POPULAR_LEAGUES = [
    # Soccer Leagues
    {'id': '4328', 'name': 'English Premier League', 'sport': 'soccer'},
    {'id': '4331', 'name': 'German Bundesliga', 'sport': 'soccer'},
    {'id': '4332', 'name': 'Italian Serie A', 'sport': 'soccer'},
    {'id': '4334', 'name': 'French Ligue 1', 'sport': 'soccer'},
    {'id': '4335', 'name': 'Spanish La Liga', 'sport': 'soccer'},
    {'id': '4346', 'name': 'American Major League Soccer', 'sport': 'soccer'},
    {'id': '4356', 'name': 'Australian A-League', 'sport': 'soccer'},
    {'id': '4406', 'name': 'Argentine Primera Division', 'sport': 'soccer'},
    {'id': '4351', 'name': 'Brazilian Serie A', 'sport': 'soccer'},
    {'id': '4337', 'name': 'Dutch Eredivisie', 'sport': 'soccer'},
    {'id': '4338', 'name': 'Portuguese Primeira Liga', 'sport': 'soccer'},
    {'id': '4344', 'name': 'Russian Premier League', 'sport': 'soccer'},
    {'id': '4339', 'name': 'Turkish Super Lig', 'sport': 'soccer'},
    {'id': '5011', 'name': 'Saudi Pro League', 'sport': 'soccer'},
    {'id': '4387', 'name': 'UEFA Champions League', 'sport': 'soccer'},
    
    # Cricket Leagues & International Teams
    {'id': '4460', 'name': 'Indian Premier League', 'sport': 'cricket'},
    {'id': '4808', 'name': 'Bangladesh Premier League', 'sport': 'cricket'},
    {'id': '4809', 'name': 'Caribbean Premier League', 'sport': 'cricket'},
    {'id': '4810', 'name': 'Big Bash League', 'sport': 'cricket'},
    {'id': '4811', 'name': 'Pakistan Super League', 'sport': 'cricket'},
    
    # Basketball
    {'id': '4387', 'name': 'NBA', 'sport': 'basketball'},
    {'id': '4388', 'name': 'EuroLeague Basketball', 'sport': 'basketball'},
    
    # Baseball
    {'id': '4424', 'name': 'MLB', 'sport': 'baseball'},
    
    # Ice Hockey
    {'id': '4380', 'name': 'NHL', 'sport': 'ice_hockey'},
    
    # American Football
    {'id': '4391', 'name': 'NFL', 'sport': 'american_football'},
]


class Command(BaseCommand):
    help = 'Populate all teams from TheSportsDB and StatPal into local DB'

    def handle(self, *args, **options):
        tsdb = TheSportsDBService()
        total_created = 0
        total_updated = 0

        self.stdout.write("Starting provider team seeding into database...")

        for league_info in POPULAR_LEAGUES:
            league_id = league_info['id']
            league_name = league_info['name']
            sport = league_info['sport']

            self.stdout.write(f"\nFetching teams for {league_name} ({sport})...")
            
            # Ensure league entity exists
            league_entity, _ = Entity.objects.get_or_create(
                name=league_name,
                type='league',
                sport=sport,
                defaults={'has_api_data': True}
            )
            League.objects.get_or_create(entity=league_entity)

            # Query TSDB for teams in league
            try:
                data = TheSportsDBService()._get('lookup_all_teams.php', {'id': league_id})
                teams_data = data.get('teams', []) or []
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Error fetching TSDB teams for {league_name}: {e}"))
                teams_data = []

            for t in teams_data:
                team_name = t.get('strTeam')
                if not team_name:
                    continue

                badge = t.get('strBadge') or t.get('strLogo') or ''
                country = t.get('strCountry') or ''
                ext_id = t.get('idTeam') or ''

                entity, created = Entity.objects.get_or_create(
                    name=team_name,
                    type='team',
                    sport=sport,
                    defaults={
                        'logo_url': badge,
                        'country': country,
                        'external_id': ext_id,
                        'api_source': 'thesportsdb',
                        'has_api_data': True
                    }
                )

                if created:
                    total_created += 1
                else:
                    updated = False
                    if badge and not entity.logo_url:
                        entity.logo_url = badge
                        updated = True
                    if country and not entity.country:
                        entity.country = country
                        updated = True
                    if updated:
                        entity.save()
                        total_updated += 1

                # Link Team instance
                team_obj, _ = Team.objects.get_or_create(entity=entity)
                team_obj.league = league_entity
                team_obj.save()

            self.stdout.write(self.style.SUCCESS(f"Finished {league_name}: {len(teams_data)} teams processed"))

        self.stdout.write(self.style.SUCCESS(
            f"\nSeeding Complete! Created {total_created} new team entities, updated {total_updated} team entities."
        ))
