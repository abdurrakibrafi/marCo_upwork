from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from apps.core.utils.mixins import BaseResponseMixin
from .serializers import LiveScoreSerializer
from .services import get_live_score_detail_data
from .selectors import (
    get_user_live_scores_queryset,
    _get_user_live_scores_queryset,
)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def live_scores(request):
    """Retrieve active live scores strictly filtered for the user's followed Nest entities.

    Args:
        request: Django HTTP request.

    Returns:
        Response: Standardized JSON payload with count and serialized live score objects.
    """
    mixin = BaseResponseMixin()
    try:
        sport = request.query_params.get('sport')
        live_games = get_user_live_scores_queryset(request.user, sport=sport)
        serializer = LiveScoreSerializer(live_games, many=True)

        data = {
            'count': live_games.count(),
            'games': serializer.data
        }
        return mixin.success_response(data=data, message='Live scores retrieved successfully')
    except Exception as exc:
        return mixin.handle_exception(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def nest_live_scores(request):
    """Retrieve active live scores filtered exclusively for entities followed in the user's Nest.

    Args:
        request: Django HTTP request with optional ?sport= query parameter.

    Returns:
        Response: Standardized JSON payload containing matching user-relevant live games.
    """
    mixin = BaseResponseMixin()
    try:
        sport = request.query_params.get('sport')
        live_games = get_user_live_scores_queryset(request.user, sport=sport)
        serializer = LiveScoreSerializer(live_games, many=True)
        return mixin.success_response(
            data={'count': live_games.count(), 'games': serializer.data}
        )
    except Exception as exc:
        return mixin.handle_exception(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def live_score_detail(request, score_id: int):
    """Retrieve comprehensive match center details (box score, events timeline, statistics, and lineups).

    Delegates payload generation to the service layer.

    Args:
        request: Django HTTP request.
        score_id: Primary key of target LiveScore or Event model.

    Returns:
        Response: Detailed game metrics, rosters, commentary, and period breakdowns.
    """
    mixin = BaseResponseMixin()
    try:
        data = get_live_score_detail_data(score_id, request=request)
        if not data:
            return mixin.error_response(
                message='Game not found',
                status_code=status.HTTP_404_NOT_FOUND
            )
        return mixin.success_response(data=data)
    except Exception as exc:
        return mixin.handle_exception(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def live_scores_by_sport(request, sport: str):
    """Retrieve active live scores filtered by sport league code (e.g. nba, nfl, soccer, cricket).

    Args:
        request: Django HTTP request.
        sport (str): Target sport slug.

    Returns:
        Response: Serialized scoreboard data for the requested sport.
    """
    mixin = BaseResponseMixin()
    try:
        sport_lower = sport.lower()
        supported_sports = {
            'nba', 'nfl', 'mlb', 'nhl', 'soccer', 'cricket', 'tennis',
            'baseball', 'handball', 'volleyball', 'golf', 'horse_racing',
            'basketball', 'football', 'hockey', 'formula1', 'mma'
        }
        if sport_lower not in supported_sports:
            return mixin.error_response(
                message=f'Sport {sport} not supported',
                status_code=status.HTTP_400_BAD_REQUEST
            )

        live_games = get_user_live_scores_queryset(request.user, sport=sport_lower)
        serializer = LiveScoreSerializer(live_games, many=True)

        data = {
            'sport': sport_lower,
            'count': live_games.count(),
            'games': serializer.data
        }
        return mixin.success_response(data=data)
    except Exception as exc:
        return mixin.handle_exception(exc)
