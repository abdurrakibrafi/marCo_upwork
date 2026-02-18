from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from django.core.cache import cache
from .models import LiveScore
from .serializers import LiveScoreSerializer

@api_view(['GET'])
def live_scores(request):
    """
    Get all live scores across all sports
    Frontend polls this every 15 seconds
    """
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
        
        return Response({
            'status': 'success',
            'count': live_games.count(),
            'games': serializer.data
        })
    else:
        # Cache miss - return empty and celery will update soon
        return Response({
            'status': 'loading',
            'message': 'Live scores are being updated...',
            'games': []
        })

@api_view(['GET'])
def live_scores_by_sport(request, sport):
    """
    Get live scores for a specific sport
    Example: /api/scores/live/nba
    """
    if sport not in ['nba', 'nfl', 'mlb', 'nhl', 'soccer', 'cricket']:
        return Response(
            {'error': f'Sport {sport} not supported'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    cache_key = f'live_scores_{sport}'
    cached_data = cache.get(cache_key)
    
    if cached_data:
        # Get from database
        live_games = LiveScore.objects.filter(sport=sport, status='live')
        serializer = LiveScoreSerializer(live_games, many=True)
        
        return Response({
            'status': 'success',
            'sport': sport,
            'count': live_games.count(),
            'games': serializer.data
        })
    else:
        return Response({
            'status': 'loading',
            'sport': sport,
            'games': []
        })