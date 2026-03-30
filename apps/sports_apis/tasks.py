from celery import shared_task
from django.core.cache import cache
from django.conf import settings
from apps.sports_apis.services.balldontlie import balldontlie_service
from apps.sports_apis.services.api_sports import api_sports_service
from apps.sports_apis.services.api_cricket import api_cricket_service
from apps.score.models import LiveScore
from apps.score.serializers import LiveScoreSerializer
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def _publish(live_score_obj):
    """Push live score updates to WebSocket consumers"""
    try:
        channel_layer = get_channel_layer()
        data = dict(LiveScoreSerializer(live_score_obj).data)
        payload = {'type': 'score_update', 'game': data}

        # push to global group
        async_to_sync(channel_layer.group_send)('live_scores', payload)

        # push to sport-specific group
        async_to_sync(channel_layer.group_send)(
            f"live_scores_{live_score_obj.sport}", payload
        )
    except Exception as e:
        logger.error(f"WebSocket publish failed: {e}")

@shared_task
def update_nba_live_scores():
    """Update NBA live scores - runs every 2 minutes"""
    logger.info("Updating NBA live scores...")
    
    result = balldontlie_service.get_live_games('nba')
    
    if result['success']:
        data = result['data']
        
        # Cache the raw data
        cache.set('live_scores_nba', data, timeout=settings.CACHE_TTLS['live_scores'])
        
        # Save to database
        games = data.get('data', [])
        for game in games:
            live_score, _ = LiveScore.objects.update_or_create(
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
            _publish(live_score)  # push to WebSocket
        
        logger.info(f"NBA: Updated {len(games)} live games")
        return f"NBA: {len(games)} games updated"
    else:
        logger.error(f"NBA update failed: {result.get('error')}")
        return f"NBA update failed: {result.get('error')}"

@shared_task
def update_nfl_live_scores():
    """Update NFL live scores - runs every 2 minutes"""
    logger.info("Updating NFL live scores...")
    
    result = balldontlie_service.get_live_games('nfl')
    
    if result['success']:
        data = result['data']
        cache.set('live_scores_nfl', data, timeout=settings.CACHE_TTLS['live_scores'])
        
        # For NFL, filter games that are currently live
        all_games = data.get('data', [])
        games = [game for game in all_games if game.get('status') == 'Live']
        
        for game in games:
            live_score, _ = LiveScore.objects.update_or_create(
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
            _publish(live_score)  # push to WebSocket
        
        logger.info(f"NFL: Updated {len(games)} live games")
        return f"NFL: {len(games)} games updated"
    else:
        logger.error(f"NFL update failed: {result.get('error')}")
        return f"NFL update failed"

@shared_task
def update_soccer_live_scores():
    """Update Soccer live scores - runs every 2 minutes"""
    logger.info("Updating Soccer live scores...")
    
    result = api_sports_service.get_live_fixtures()
    
    if result['success']:
        data = result['data']
        cache.set('live_scores_soccer', data, timeout=settings.CACHE_TTLS['live_scores'])
        
        fixtures = data.get('response', [])
        for fixture in fixtures:
            live_score, _ = LiveScore.objects.update_or_create(
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
            _publish(live_score)  # push to WebSocket
        
        logger.info(f"Soccer: Updated {len(fixtures)} live games")
        return f"Soccer: {len(fixtures)} games updated"
    else:
        logger.error(f"Soccer update failed: {result.get('error')}")
        return f"Soccer update failed"
    

@shared_task
def update_cricket_live_scores():
    """Update Cricket live scores - runs every 2 minutes"""
    logger.info("Updating Cricket live scores...")

    result = api_cricket_service.get_live_scores()

    if not result['success']:
        logger.error(f"Cricket update failed: {result.get('error')}")
        return "Cricket update failed"

    data = result['data']
    matches = data.get('result', [])

    if not matches:
        return "Cricket: 0 live matches right now"

    saved = 0
    for match in matches:
        home_team = match.get('event_home_team', '')
        away_team = match.get('event_away_team', '')
        
        # these are the actual score strings like "84/1"
        home_score_str = match.get('event_home_final_result', '') or ''
        away_score_str = match.get('event_away_final_result', '') or ''
        
        # run rates
        home_rr = match.get('event_home_rr') or ''
        away_rr = match.get('event_away_rr') or ''

        # match type: TEST / ODI / T20
        match_type = match.get('event_type', '')

        # toss info
        toss = match.get('event_toss', '')

        # status info like "Day 1 - Rhinos chose to bat"
        status_info = match.get('event_status_info', '')

        event_status = (match.get('event_status') or '').lower()
        if 'live' in event_status or 'progress' in event_status:
            status = 'live'
        elif 'finished' in event_status or 'stumps' in event_status:
            status = 'completed'
        else:
            status = 'live'  # lunch/tea/drinks are still live matches

        external_id = str(match.get('event_key', ''))
        if not external_id or not home_team:
            continue

        start_time = match.get('event_date_start')
        if not start_time:
            continue

        live_score, _ = LiveScore.objects.update_or_create(
            sport='cricket',
            external_id=external_id,
            defaults={
                'home_team': home_team,
                'away_team': away_team,
                'home_logo': match.get('event_home_team_logo') or '',
                'away_logo': match.get('event_away_team_logo') or '',
                'home_score': None,  # cricket scores aren't integers
                'away_score': None,
                'status': status,
                # frontend shows this on the card
                'status_detail': f"{home_score_str} | {away_score_str}".strip(' |'),
                'start_time': start_time,
                'raw_data': match,  # full scorecard/comments stored here
                'metadata': {
                    'match_type': match_type,
                    'toss': toss,
                    'status_info': status_info,
                    'home_rr': home_rr,
                    'away_rr': away_rr,
                    'league': match.get('league_name', ''),
                    'stadium': match.get('event_stadium', ''),
                }
            }
        )
        _publish(live_score)  # push to WebSocket
        saved += 1

    logger.info(f"Cricket: saved {saved} live matches")
    return f"Cricket: {saved} matches updated"