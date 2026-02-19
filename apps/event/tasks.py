from celery import shared_task
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone
from datetime import datetime, timedelta
from apps.event.models import Event
from apps.entity.models import Entity
from sports_apis.services.balldontlie import balldontlie_service
from sports_apis.services.api_sports import api_sports_service
from sports_apis.services.api_cricket import api_cricket_service
import logging

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
                    'start_time': game['date'],
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
                    'start_time': game['date'],
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
                    'venue_name': venue_data.get('name', ''),
                    'venue_city': venue_data.get('city', ''),
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
        fixtures = result['data'].get('response', [])
        
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
            
            status_str = fixture.get('status', '').lower()
            if 'finished' in status_str or 'complete' in status_str:
                status = 'completed'
            elif 'live' in status_str or 'progress' in status_str:
                status = 'live'
            else:
                status = 'upcoming'
            
            Event.objects.update_or_create(
                api_source='api_cricket',
                external_id=str(fixture['id']),
                defaults={
                    'sport': 'cricket',
                    'home_entity': home_team,
                    'away_entity': away_team,
                    'start_time': fixture['date'],
                    'status': status,
                    'status_detail': fixture.get('status', ''),
                    'venue_name': fixture.get('venue', ''),
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
    """Helper to get or create team entity"""
    entity, created = Entity.objects.get_or_create(
        api_source=api_source,
        external_id=external_id,
        defaults={
            'type': 'team',
            'name': name,
            'sport': sport,
            'logo_url': logo_url,
            'has_api_data': True,
        }
    )
    return entity


def _get_or_create_league_entity(api_source, external_id, name, sport, logo_url=''):
    """Helper to get or create league entity"""
    entity, created = Entity.objects.get_or_create(
        api_source=api_source,
        external_id=external_id,
        defaults={
            'type': 'league',
            'name': name,
            'sport': sport,
            'logo_url': logo_url,
            'has_api_data': True,
        }
    )
    return entity