from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import datetime, timezone as dt_timezone
import logging
from apps.event.models import Event
from apps.entity.models import Entity
from apps.sports_apis.services.thesportsdb import thesportsdb_service

logger = logging.getLogger(__name__)

def get_dynamic_season(sport: str, league_id: str = None) -> str:
    """Dynamically calculate active season based on sport type and calendar month."""
    now = timezone.now()
    year = now.year
    month = now.month

    # Single-year sports: MLB, MLS (4346), Formula 1, Golf
    if sport in ('baseball', 'f1', 'formula1', 'golf', 'motorsports') or str(league_id) == '4346':
        return str(year)

    # Cross-year sports: European Soccer, NBA, NFL, NHL
    # Seasons beginning July-December: e.g. 2026-2027; January-June: e.g. 2025-2026
    if month >= 7:
        return f"{year}-{year + 1}"
    else:
        return f"{year - 1}-{year}"


# Major league directory for TheSportsDB (league_id, name, sport)
MAJOR_LEAGUES = [
    # Baseball
    ('4424', 'USA: MLB', 'baseball'),
    # Soccer
    ('4328', 'English Premier League', 'soccer'),
    ('4335', 'Spanish La Liga', 'soccer'),
    ('4331', 'German Bundesliga', 'soccer'),
    ('4332', 'Italian Serie A', 'soccer'),
    ('4334', 'French Ligue 1', 'soccer'),
    ('4480', 'UEFA Champions League', 'soccer'),
    ('4346', 'American Major League Soccer', 'soccer'),
    # Basketball
    ('4387', 'USA: NBA', 'basketball'),
    # American Football
    ('4391', 'USA: NFL', 'american_football'),
    # Ice Hockey
    ('4380', 'USA: NHL', 'ice_hockey'),
]


class Command(BaseCommand):
    help = "Synchronize long-term full-season fixtures from TheSportsDB into the database with dynamic season detection."

    def add_arguments(self, parser):
        parser.add_argument(
            '--league-id',
            type=str,
            help='Specific TheSportsDB league ID to fetch (e.g. 4424 for MLB, 4328 for EPL)',
        )
        parser.add_argument(
            '--sport',
            type=str,
            help='Specific sport to filter (e.g. baseball, soccer, basketball)',
        )
        parser.add_argument(
            '--season',
            type=str,
            help='Optional manual season override (e.g. 2026, 2026-2027). Defaults to dynamic current season.',
        )

    def handle(self, *args, **options):
        custom_league_id = options.get('league_id')
        custom_sport = options.get('sport')
        custom_season = options.get('season')

        leagues_to_fetch = []
        if custom_league_id:
            sport = custom_sport or 'soccer'
            season = custom_season or get_dynamic_season(sport, custom_league_id)
            leagues_to_fetch.append((custom_league_id, f"League {custom_league_id}", sport, season))
        else:
            for l_id, name, sport in MAJOR_LEAGUES:
                if custom_sport and custom_sport.lower() != sport:
                    continue
                season = custom_season or get_dynamic_season(sport, l_id)
                leagues_to_fetch.append((l_id, name, sport, season))

        self.stdout.write(self.style.NOTICE(f"Starting TheSportsDB fixture sync for {len(leagues_to_fetch)} leagues..."))

        total_created = 0
        total_updated = 0

        for league_id, league_name, sport, season in leagues_to_fetch:
            self.stdout.write(f"Fetching {league_name} (ID: {league_id}, Season: {season})...")
            events = thesportsdb_service.get_league_season_events(league_id, season=season)

            if not events:
                self.stdout.write(self.style.WARNING(f"No events returned for {league_name}."))
                continue

            self.stdout.write(f"Processing {len(events)} matches for {league_name}...")

            # Find or get League entity
            league_ent = Entity.objects.filter(
                name__iexact=league_name,
                type='league'
            ).first() or Entity.objects.filter(type='league', sport=sport).first()

            for ev in events:
                home_name = (ev.get('strHomeTeam') or '').strip()
                away_name = (ev.get('strAwayTeam') or '').strip()
                if not home_name or not away_name:
                    continue

                date_str = ev.get('dateEvent') or ''
                time_str = ev.get('strTime') or '00:00:00'
                if not date_str:
                    continue

                # Parse start time UTC
                try:
                    # Clean time string (strip timezone suffix if present)
                    clean_time = time_str.split('+')[0].strip()
                    if len(clean_time.split(':')) == 2:
                        clean_time += ':00'
                    dt_naive = datetime.strptime(f"{date_str} {clean_time}", "%Y-%m-%d %H:%M:%S")
                    start_dt = dt_naive.replace(tzinfo=dt_timezone.utc)
                except Exception:
                    try:
                        start_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=dt_timezone.utc)
                    except Exception:
                        continue

                # Match or find entities
                home_ent = Entity.objects.filter(name__iexact=home_name, sport=sport, type='team').first() or \
                           Entity.objects.filter(name__icontains=home_name, sport=sport, type='team').first()
                away_ent = Entity.objects.filter(name__iexact=away_name, sport=sport, type='team').first() or \
                           Entity.objects.filter(name__icontains=away_name, sport=sport, type='team').first()

                if not home_ent:
                    home_ent = Entity.objects.create(
                        name=home_name,
                        sport=sport,
                        type='team',
                        logo_url=ev.get('strHomeTeamBadge') or ''
                    )
                if not away_ent:
                    away_ent = Entity.objects.create(
                        name=away_name,
                        sport=sport,
                        type='team',
                        logo_url=ev.get('strAwayTeamBadge') or ''
                    )

                ext_id = f"tsdb_{ev.get('idEvent', '')}" if ev.get('idEvent') else None
                status = 'completed' if ev.get('intHomeScore') is not None else ('upcoming' if start_dt > timezone.now() else 'completed')

                venue_name = ev.get('strVenue') or ''
                venue_city = ev.get('strCity') or ''
                venue_country = ev.get('strCountry') or ''

                home_score = None
                away_score = None
                try:
                    if ev.get('intHomeScore') is not None:
                        home_score = int(ev['intHomeScore'])
                    if ev.get('intAwayScore') is not None:
                        away_score = int(ev['intAwayScore'])
                except (ValueError, TypeError):
                    pass

                # Check if exact event exists
                event_obj = None
                if ext_id:
                    event_obj = Event.objects.filter(external_id=ext_id).first()
                if not event_obj:
                    event_obj = Event.objects.filter(
                        home_entity=home_ent,
                        away_entity=away_ent,
                        start_time=start_dt
                    ).first()

                if event_obj:
                    # Update metadata / scores if needed
                    updated = False
                    if not event_obj.venue_name and venue_name:
                        event_obj.venue_name = venue_name
                        updated = True
                    if event_obj.home_score is None and home_score is not None:
                        event_obj.home_score = home_score
                        event_obj.away_score = away_score
                        event_obj.status = status
                        updated = True
                    if updated:
                        event_obj.save()
                        total_updated += 1
                else:
                    Event.objects.create(
                        sport=sport,
                        home_entity=home_ent,
                        away_entity=away_ent,
                        league=league_ent,
                        start_time=start_dt,
                        status=status,
                        home_score=home_score,
                        away_score=away_score,
                        venue_name=venue_name,
                        venue_city=venue_city,
                        venue_country=venue_country,
                        api_source='thesportsdb',
                        external_id=ext_id,
                    )
                    total_created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Finished! Successfully created {total_created} fixtures and updated {total_updated} fixtures from TheSportsDB."
            )
        )
