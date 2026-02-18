from celery import shared_task
from django.core.cache import cache
from django.conf import settings
from apps.sports_apis.services.balldontlie import balldontlie_service
from apps.sports_apis.services.api_sports import api_sports_service
from apps.sports_apis.services.api_cricket import api_cricket_service
from apps.score.models import LiveScore
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

@shared_task
def update_nba_live_scores():
    """Update NBA live scores - runs every 30 seconds"""
    logger.info("Updating NBA live scores...")
    
    result = balldontlie_service.get_live_games('nba')
    
    if result['success']:
        data = result['data']
        
        # Cache the raw data
        cache.set('live_scores_nba', data, timeout=settings.CACHE_TTLS['live_scores'])
        
        # Save to database
        games = data.get('data', [])
        for game in games:
            LiveScore.objects.update_or_create(
                sport='nba',
                external_id=str(game.get('id')),
                defaults={
                    'home_team': game.get('home_team', {}).get('name', ''),
                    'away_team': game.get('visitor_team', {}).get('name', ''),
                    'home_score': game.get('home_team_score'),
                    'away_score': game.get('visitor_team_score'),
                    'status': 'live' if game.get('status') == 'Live' else 'completed',
                    'status_detail': game.get('period', ''),
                    'start_time': game.get('date'),
                    'raw_data': game,
                }
            )
        
        logger.info(f"NBA: Updated {len(games)} live games")
        return f"NBA: {len(games)} games updated"
    else:
        logger.error(f"NBA update failed: {result.get('error')}")
        return f"NBA update failed: {result.get('error')}"

@shared_task
def update_nfl_live_scores():
    """Update NFL live scores - runs every 30 seconds"""
    logger.info("Updating NFL live scores...")
    
    result = balldontlie_service.get_live_games('nfl')
    
    if result['success']:
        data = result['data']
        cache.set('live_scores_nfl', data, timeout=settings.CACHE_TTLS['live_scores'])
        
        games = data.get('data', [])
        for game in games:
            LiveScore.objects.update_or_create(
                sport='nfl',
                external_id=str(game.get('id')),
                defaults={
                    'home_team': game.get('home_team', {}).get('name', ''),
                    'away_team': game.get('visitor_team', {}).get('name', ''),
                    'home_score': game.get('home_team_score'),
                    'away_score': game.get('visitor_team_score'),
                    'status': 'live' if game.get('status') == 'Live' else 'completed',
                    'status_detail': game.get('quarter', ''),
                    'start_time': game.get('date'),
                    'raw_data': game,
                }
            )
        
        logger.info(f"NFL: Updated {len(games)} live games")
        return f"NFL: {len(games)} games updated"
    else:
        logger.error(f"NFL update failed: {result.get('error')}")
        return f"NFL update failed"

@shared_task
def update_soccer_live_scores():
    """Update Soccer live scores - runs every 30 seconds"""
    logger.info("Updating Soccer live scores...")
    
    result = api_sports_service.get_live_fixtures()
    
    if result['success']:
        data = result['data']
        cache.set('live_scores_soccer', data, timeout=settings.CACHE_TTLS['live_scores'])
        
        fixtures = data.get('response', [])
        for fixture in fixtures:
            LiveScore.objects.update_or_create(
                sport='soccer',
                external_id=str(fixture.get('fixture', {}).get('id')),
                defaults={
                    'home_team': fixture.get('teams', {}).get('home', {}).get('name', ''),
                    'away_team': fixture.get('teams', {}).get('away', {}).get('name', ''),
                    'home_logo': fixture.get('teams', {}).get('home', {}).get('logo', ''),
                    'away_logo': fixture.get('teams', {}).get('away', {}).get('logo', ''),
                    'home_score': fixture.get('goals', {}).get('home'),
                    'away_score': fixture.get('goals', {}).get('away'),
                    'status': 'live',
                    'status_detail': fixture.get('fixture', {}).get('status', {}).get('short', ''),
                    'start_time': fixture.get('fixture', {}).get('date'),
                    'raw_data': fixture,
                }
            )
        
        logger.info(f"Soccer: Updated {len(fixtures)} live games")
        return f"Soccer: {len(fixtures)} games updated"
    else:
        logger.error(f"Soccer update failed: {result.get('error')}")
        return f"Soccer update failed"

@shared_task
def update_cricket_live_scores():
    """Update Cricket live scores - runs every 30 seconds"""
    logger.info("Updating Cricket live scores...")
    
    result = api_cricket_service.get_live_scores()
    
    if result['success']:
        data = result['data']
        cache.set('live_scores_cricket', data, timeout=settings.CACHE_TTLS['live_scores'])
        
        matches = data.get('response', [])
        for match in matches:
            LiveScore.objects.update_or_create(
                sport='cricket',
                external_id=str(match.get('id')),
                defaults={
                    'home_team': match.get('teams', [{}])[0].get('name', '') if match.get('teams') else '',
                    'away_team': match.get('teams', [{}])[1].get('name', '') if len(match.get('teams', [])) > 1 else '',
                    'home_score': match.get('scores', [{}])[0].get('score') if match.get('scores') else None,
                    'away_score': match.get('scores', [{}])[1].get('score') if len(match.get('scores', [])) > 1 else None,
                    'status': 'live',
                    'status_detail': match.get('status', ''),
                    'start_time': match.get('date'),
                    'raw_data': match,
                }
            )
        
        logger.info(f"Cricket: Updated {len(matches)} live games")
        return f"Cricket: {len(matches)} games updated"
    else:
        logger.error(f"Cricket update failed: {result.get('error')}")
        return f"Cricket update failed"