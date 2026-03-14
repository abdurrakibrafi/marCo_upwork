from celery import shared_task
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone
from datetime import datetime, timedelta
from apps.event.models import Event
from apps.entity.models import Entity
from apps.sports_apis.services.balldontlie import balldontlie_service
from apps.sports_apis.services.api_sports import api_sports_service
from apps.sports_apis.services.api_cricket import api_cricket_service
import logging
from django.utils.timezone import make_aware
logger = logging.getLogger(__name__)


@shared_task
def update_nba_fixtures(date=None):
    """Update NBA fixtures for a date"""
    if not date:
        date = timezone.now().date().isoformat()
    
    logger.info(f"Updating NBA fixtures for {date}")
    
    result = balldontlie_service.get_games_by_date('nba', date)
    
    if result['success']:
        games = result['data'].get('data', [])
        
        for game in games:
            home_team = _get_or_create_team_entity(
                'balldontlie',
                str(game['home_team']['id']),
                game['home_team']['full_name'],
                'basketball'
            )
            
            away_team = _get_or_create_team_entity(
                'balldontlie',
                str(game['visitor_team']['id']),
                game['visitor_team']['full_name'],
                'basketball'
            )
            
            # Determine status
            game_status = game.get('status', '')
            if game_status == 'Final':
                status = 'completed'
            elif game_status in ['1st Qtr', '2nd Qtr', '3rd Qtr', '4th Qtr', 'Half']:
                status = 'live'
            else:
                status = 'upcoming'
            
            Event.objects.update_or_create(
                api_source='balldontlie',
                external_id=str(game['id']),
                defaults={
                    'sport': 'basketball',
                    'home_entity': home_team,
                    'away_entity': away_team,
                    'start_time': make_aware(datetime.fromisoformat(game['date'])) if isinstance(game['date'], str) else game['date'],
                    'status': status,
                    'status_detail': game.get('period', ''),
                    'home_score': game.get('home_team_score'),
                    'away_score': game.get('visitor_team_score'),
                    'metadata': game,
                }
            )
        
        # Cache the fixtures
        cache_key = f'fixtures_nba_{date}'
        cache.set(cache_key, games, timeout=3600)  # 1 hour
        
        logger.info(f"NBA: Updated {len(games)} fixtures for {date}")
        return f"NBA: {len(games)} fixtures updated"
    
    logger.error(f"NBA fixtures update failed: {result.get('error')}")
    return "NBA fixtures update failed"


@shared_task
def update_nfl_fixtures(date=None):
    """Update NFL fixtures"""
    if not date:
        date = timezone.now().date().isoformat()
    
    logger.info(f"Updating NFL fixtures for {date}")
    
    # NFL uses week-based system, calculate current week
    season = timezone.now().year
    result = balldontlie_service.get_games_by_date('nfl', date)
    
    if result['success']:
        games = result['data'].get('data', [])
        
        for game in games:
            home_team = _get_or_create_team_entity(
                'balldontlie',
                str(game['home_team']['id']),
                game['home_team']['full_name'],
                'football'
            )
            
            away_team = _get_or_create_team_entity(
                'balldontlie',
                str(game['visitor_team']['id']),
                game['visitor_team']['full_name'],
                'football'
            )
            
            game_status = game.get('status', '')
            if game_status == 'Final':
                status = 'completed'
            elif game_status in ['Q1', 'Q2', 'Q3', 'Q4']:
                status = 'live'
            else:
                status = 'upcoming'
            
            Event.objects.update_or_create(
                api_source='balldontlie',
                external_id=str(game['id']),
                defaults={
                    'sport': 'football',
                    'home_entity': home_team,
                    'away_entity': away_team,
                    'start_time': make_aware(datetime.fromisoformat(game['date'])) if isinstance(game['date'], str) else game['date'],
                    'status': status,
                    'status_detail': game.get('quarter', ''),
                    'home_score': game.get('home_team_score'),
                    'away_score': game.get('visitor_team_score'),
                    'metadata': game,
                }
            )
        
        cache_key = f'fixtures_nfl_{date}'
        cache.set(cache_key, games, timeout=3600)
        
        logger.info(f"NFL: Updated {len(games)} fixtures")
        return f"NFL: {len(games)} fixtures updated"
    
    return "NFL fixtures update failed"


@shared_task
def update_soccer_fixtures(date=None):
    """Update soccer fixtures for a date"""
    if not date:
        date = timezone.now().date().isoformat()
    
    logger.info(f"Updating soccer fixtures for {date}")
    
    result = api_sports_service.get_fixtures_by_date(date)
    
    if result['success']:
        fixtures = result['data'].get('response', [])
        
        for fixture in fixtures:
            fixture_data = fixture.get('fixture', {})
            teams_data = fixture.get('teams', {})
            goals_data = fixture.get('goals', {})
            league_data = fixture.get('league', {})
            venue_data = fixture_data.get('venue', {})
            
            home_team = _get_or_create_team_entity(
                'api_sports',
                str(teams_data['home']['id']),
                teams_data['home']['name'],
                'soccer',
                logo_url=teams_data['home'].get('logo')
            )
            
            away_team = _get_or_create_team_entity(
                'api_sports',
                str(teams_data['away']['id']),
                teams_data['away']['name'],
                'soccer',
                logo_url=teams_data['away'].get('logo')
            )
            
            # Get or create league
            league = _get_or_create_league_entity(
                'api_sports',
                str(league_data['id']),
                league_data['name'],
                'soccer',
                logo_url=league_data.get('logo')
            )
            
            # Map status
            status_short = fixture_data.get('status', {}).get('short', '')
            if status_short in ['FT', 'AET', 'PEN']:
                status = 'completed'
            elif status_short in ['1H', '2H', 'HT', 'ET', 'BT', 'P', 'LIVE']:
                status = 'live'
            elif status_short in ['PST', 'CANC', 'ABD']:
                status = 'postponed'
            else:
                status = 'upcoming'
            
            Event.objects.update_or_create(
                api_source='api_sports',
                external_id=str(fixture_data['id']),
                defaults={
                    'sport': 'soccer',
                    'home_entity': home_team,
                    'away_entity': away_team,
                    'league': league,
                    'start_time': fixture_data['date'],
                    'status': status,
                    'status_detail': fixture_data.get('status', {}).get('long', ''),
                    'home_score': goals_data.get('home'),
                    'away_score': goals_data.get('away'),
                    'venue_name': venue_data.get('name') or '',     
                    'venue_city': venue_data.get('city') or '',     
                    'metadata': fixture,
                }
            )
        
        cache_key = f'fixtures_soccer_{date}'
        cache.set(cache_key, fixtures, timeout=3600)
        
        logger.info(f"Soccer: Updated {len(fixtures)} fixtures for {date}")
        return f"Soccer: {len(fixtures)} fixtures updated"
    
    return "Soccer fixtures update failed"


@shared_task
def update_cricket_fixtures(date=None):
    """Update cricket fixtures"""
    if not date:
        date = timezone.now().date().isoformat()
    
    logger.info(f"Updating cricket fixtures for {date}")
    
    result = api_cricket_service.get_fixtures_by_date(date)
    
    if result['success']:
        fixtures = result['data'].get('result', [])
        
        for fixture in fixtures:
            teams = fixture.get('teams', [])
            if len(teams) < 2:
                continue
            
            home_team = _get_or_create_team_entity(
                'api_cricket',
                str(teams[0].get('id')),
                teams[0].get('name'),
                'cricket'
            )
            
            away_team = _get_or_create_team_entity(
                'api_cricket',
                str(teams[1].get('id')),
                teams[1].get('name'),
                'cricket'
            )
            
            status_str = fixture.get('event_status', '').lower() or fixture.get('status', '').lower()
            if 'finished' in status_str or 'complete' in status_str:
                status = 'completed'
            elif 'live' in status_str or 'progress' in status_str:
                status = 'live'
            else:
                status = 'upcoming'
            
            Event.objects.update_or_create(
                api_source='api_cricket',
                external_id=str(fixture.get('event_key', fixture.get('id', ''))),
                defaults={
                    'sport': 'cricket',
                    'home_entity': home_team,
                    'away_entity': away_team,
                    'start_time': fixture.get('event_date_start') or fixture.get('date'),
                    'status': status,
                    'status_detail': fixture.get('event_status', ''),
                    'venue_name': fixture.get('event_stadium', fixture.get('venue', '')),
                    'metadata': fixture,
                }
            )
        
        cache_key = f'fixtures_cricket_{date}'
        cache.set(cache_key, fixtures, timeout=3600)
        
        logger.info(f"Cricket: Updated {len(fixtures)} fixtures")
        return f"Cricket: {len(fixtures)} fixtures updated"
    
    return "Cricket fixtures update failed"


@shared_task
def update_all_fixtures():
    """Update fixtures for all sports"""
    today = timezone.now().date().isoformat()
    tomorrow = (timezone.now() + timedelta(days=1)).date().isoformat()
    
    # Update today and tomorrow
    for date in [today, tomorrow]:
        update_nba_fixtures.delay(date)
        update_nfl_fixtures.delay(date)
        update_soccer_fixtures.delay(date)
        update_cricket_fixtures.delay(date)
    
    return "All fixtures update triggered"


# Helper functions

def _get_or_create_team_entity(api_source, external_id, name, sport, logo_url=''):
    # Try to get existing first — use filter+first to avoid MultipleObjectsReturned
    entity = Entity.objects.filter(
        api_source=api_source,
        external_id=external_id,
    ).first()

    if entity:
        return entity

    # Create new one
    entity = Entity.objects.create(
        api_source=api_source,
        external_id=external_id,
        type='team',
        name=name,
        sport=sport,
        logo_url=logo_url or '',
        has_api_data=True,
    )
    # Create Team sub-model
    from apps.entity.models import Team
    Team.objects.get_or_create(entity=entity)
    return entity


def _get_or_create_league_entity(api_source, external_id, name, sport, logo_url=''):
    # Ensure we always reuse the same league entity for the same api_source+external_id.
    # This prevents duplicate league entities that break fixtures/standings lookups.
    entity, created = Entity.objects.get_or_create(
        api_source=api_source,
        external_id=external_id,
        defaults={
            'type': 'league',
            'name': name,
            'sport': sport,
            'logo_url': logo_url or '',
            'has_api_data': True,
        }
    )

    if not created and logo_url and not entity.logo_url:
        entity.logo_url = logo_url
        entity.save(update_fields=['logo_url'])

    from apps.entity.models import League
    League.objects.get_or_create(entity=entity)
    return entity


@shared_task
def update_soccer_live_scores_only():
    """
    Lightweight task — only updates scores/status for fixtures
    already in DB that are live or starting soon.

    BUG FIX: The old setup had update_soccer_fixtures running every 2 min
    AND as part of update_all_fixtures daily — causing duplicate work.

    This task only calls the API for fixtures that are actually live,
    not the full day's fixture list.
    """
    from django.utils import timezone
    from datetime import timedelta

    now = timezone.now()
    soon = now + timedelta(hours=2)

    # Only fetch IDs of games that are live or starting within 2 hours
    active_fixture_ids = list(
        Event.objects.filter(
            sport='soccer',
            status__in=['live', 'upcoming'],
            start_time__lte=soon,
            api_source='api_sports',
        ).values_list('external_id', flat=True)[:20]  # max 20 at a time
    )

    if not active_fixture_ids:
        return "No active soccer fixtures to update"

    result = api_sports_service.get_live_fixtures()

    if not result['success']:
        return f"Failed to fetch live fixtures: {result.get('error')}"

    fixtures = result['data'].get('response', [])
    updated = 0

    for fixture in fixtures:
        fixture_data = fixture.get('fixture', {})
        goals_data = fixture.get('goals', {})
        external_id = str(fixture_data.get('id', ''))

        status_short = fixture_data.get('status', {}).get('short', '')
        if status_short in ['FT', 'AET', 'PEN']:
            status = 'completed'
        elif status_short in ['1H', '2H', 'HT', 'ET', 'BT', 'P', 'LIVE']:
            status = 'live'
        elif status_short in ['PST', 'CANC', 'ABD']:
            status = 'postponed'
        else:
            status = 'upcoming'

        rows = Event.objects.filter(
            api_source='api_sports',
            external_id=external_id,
        ).update(
            status=status,
            status_detail=fixture_data.get('status', {}).get('long', ''),
            home_score=goals_data.get('home'),
            away_score=goals_data.get('away'),
        )
        if rows:
            updated += 1

    return f"Live soccer: updated {updated} fixtures"