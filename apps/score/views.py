from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.core.cache import cache
from .models import LiveScore
from .serializers import LiveScoreSerializer
from apps.core.utils.mixins import BaseResponseMixin

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def live_scores(request):
    """
    Get all live scores across all sports
    Frontend polls this every 15 seconds
    """
    mixin = BaseResponseMixin()
    try:
        # Get from cache first
        cached_nba = cache.get('live_scores_nba')
        cached_nfl = cache.get('live_scores_nfl')
        cached_soccer = cache.get('live_scores_soccer')
        cached_cricket = cache.get('live_scores_cricket')
        
        # If cache exists, use it (faster)
        if any([cached_nba, cached_nfl, cached_soccer, cached_cricket]):
            # Get live games from database (last 30 seconds)
            live_games = LiveScore.objects.filter(status='live').order_by('-updated_at')[:20]
            serializer = LiveScoreSerializer(live_games, many=True)
            
            data = {
                'count': live_games.count(),
                'games': serializer.data
            }
            return mixin.success_response(data=data, message='Live scores retrieved successfully')
        else:
            # Cache miss - return empty and celery will update soon
            return mixin.success_response(
                data={'games': []},
                message='Live scores are being updated...'
            )
    except Exception as exc:
        return mixin.handle_exception(exc)
    
# apps/score/views.py
# Replace your live_score_detail function with this

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def live_score_detail(request, score_id):
    mixin = BaseResponseMixin()
    try:
        game = LiveScore.objects.get(id=score_id)
        raw = game.raw_data
        if isinstance(raw, list):
            raw = raw[0] if raw else {}

        sport = game.sport

        if sport == 'cricket':
            comments = raw.get('comments', {})
            if isinstance(comments, list):
                ball_by_ball = comments[-10:]
            elif isinstance(comments, dict):
                ball_by_ball = comments.get('Live', [])[-10:]
            else:
                ball_by_ball = []

            data = {
                'id': game.id,
                'sport': sport,
                'home_team': game.home_team,
                'away_team': game.away_team,
                'home_logo': game.home_logo,
                'away_logo': game.away_logo,
                'status': game.status,
                'status_detail': game.status_detail,
                'start_time': game.start_time,
                'match_type': raw.get('event_type', ''),
                'toss': raw.get('event_toss', ''),
                'status_info': raw.get('event_status_info', ''),
                'stadium': raw.get('event_stadium', ''),
                'league': raw.get('league_name', ''),
                'home_rr': raw.get('event_home_rr'),
                'away_rr': raw.get('event_away_rr'),
                'scorecard': raw.get('scorecard', {}),
                'ball_by_ball': ball_by_ball,
                'wickets': raw.get('wickets', {}),
                'lineups': raw.get('lineups', {}),
            }

        elif sport == 'soccer':
            from apps.sports_apis.services.api_sports import api_sports_service

            detail_raw = {}
            result = api_sports_service.get_fixture_details(int(game.external_id))
            if result['success']:
                resp = result['data'].get('response', [])
                if resp:
                    detail_raw = resp[0]

            fix_data    = detail_raw.get('fixture', raw.get('fixture', {}))
            league_data = detail_raw.get('league', raw.get('league', {}))
            events      = detail_raw.get('events', raw.get('events', []))
            lineups     = detail_raw.get('lineups', [])
            statistics  = detail_raw.get('statistics', [])
            players     = detail_raw.get('players', [])
            goals       = detail_raw.get('goals', raw.get('goals', {}))
            score       = detail_raw.get('score', raw.get('score', {}))

            data = {
                'id': game.id,
                'sport': sport,
                'home_team': game.home_team,
                'away_team': game.away_team,
                'home_logo': game.home_logo,
                'away_logo': game.away_logo,
                'status': game.status,
                'status_detail': game.status_detail,
                'start_time': game.start_time,
                'match_type': '',
                'toss': '',
                'status_info': fix_data.get('status', {}).get('long', ''),
                'stadium': fix_data.get('venue', {}).get('name', ''),
                'league': league_data.get('name', ''),
                'league_logo': league_data.get('logo', ''),
                'league_country': league_data.get('country', ''),
                'home_rr': None,
                'away_rr': None,
                'home_score': goals.get('home'),
                'away_score': goals.get('away'),
                'halftime_score': score.get('halftime', {}),
                'fulltime_score': score.get('fulltime', {}),
                'referee': fix_data.get('referee', ''),
                'scorecard': {},
                'ball_by_ball': [],
                'wickets': {},
                'events': events,
                'lineups': lineups,
                'statistics': statistics,
                'players': players,
            }

        else:
            # NBA / NFL — just return raw for now
            data = {
                'id': game.id,
                'sport': sport,
                'home_team': game.home_team,
                'away_team': game.away_team,
                'home_logo': game.home_logo,
                'away_logo': game.away_logo,
                'status': game.status,
                'status_detail': game.status_detail,
                'start_time': game.start_time,
                'home_score': game.home_score,
                'away_score': game.away_score,
                'raw': raw,
            }

        return mixin.success_response(data=data)
    except LiveScore.DoesNotExist:
        return mixin.error_response(message='Game not found', status_code=404)
    

    
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def live_scores_by_sport(request, sport):
    """
    Get live scores for a specific sport
    Example: /api/scores/live/nba
    """
    mixin = BaseResponseMixin()
    try:
        if sport not in ['nba', 'nfl', 'mlb', 'nhl', 'soccer', 'cricket']:
            return mixin.error_response(
                message=f'Sport {sport} not supported',
                status_code=status.HTTP_400_BAD_REQUEST
            )
        
        cache_key = f'live_scores_{sport}'
        cached_data = cache.get(cache_key)
        
        if cached_data:
            # Get from database
            live_games = LiveScore.objects.filter(sport=sport, status='live')
            serializer = LiveScoreSerializer(live_games, many=True)
            
            data = {
                'sport': sport,
                'count': live_games.count(),
                'games': serializer.data
            }
            return mixin.success_response(data=data)
        else:
            return mixin.success_response(
                data={'sport': sport, 'games': []},
                message='Live scores are being updated...'
            )
    except Exception as exc:
        return mixin.handle_exception(exc)