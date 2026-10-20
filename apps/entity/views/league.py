import logging
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.entity.models import Entity, Team, Athlete, EntityStats
from apps.entity.serializers import EntitySerializer
from .common import _current_season

# Import helpers to keep league.py modular and maintain full backwards-compatibility
from .helpers.league_standings import (
    _get_standings_for_league,
    _fetch_league_standings_thesportsdb,
    _fetch_statpal_hierarchical_standings,
    _fetch_soccer_standings,
)
from .helpers.league_leaders import (
    _fetch_soccer_leaders,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# LEAGUE STANDINGS
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([AllowAny])
def get_league_standings(request, league_id):
    """Retrieve league standings and table rankings for a specific season.

    Args:
        request (Request): HTTP GET request with optional 'season' query parameter.
        league_id (int): Primary key ID of the league entity.

    Returns:
        Response: Standings array with teams, points, goals, form, and records.
    """
    league_entity = get_object_or_404(Entity, id=league_id, type='league')
    league_entity = league_entity.canonical_entity or league_entity
    season = request.GET.get('season') or str(_current_season('soccer'))
    return _get_standings_for_league(request, league_entity, season)


# ─────────────────────────────────────────────────────────────────────────────
# LEAGUE LEADERS
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([AllowAny])
def get_league_leaders(request, league_id):
    """Retrieve the top player statistical leaders (goals, assists, cards) for a league.

    Args:
        request (Request): HTTP GET request with optional 'season' and 'stat' query parameters.
        league_id (int): Primary key ID of the league entity.

    Returns:
        Response: Ranked list of top athletes and their metric values.
    """
    league_entity = get_object_or_404(Entity, id=league_id, type='league')
    league_entity = league_entity.canonical_entity or league_entity
    season    = request.GET.get('season') or str(_current_season('soccer'))
    stat_type = request.GET.get('stat', 'goals')

    canonical = Entity.objects.filter(
        type='league',
        api_source=league_entity.api_source,
        external_id=league_entity.external_id,
    ).first() or league_entity

    teams_in_league = Team.objects.filter(
        league__api_source=canonical.api_source,
        league__external_id=canonical.external_id,
    )
    team_ext_ids = [t.entity.external_id for t in teams_in_league]

    athletes = Athlete.objects.filter(
        current_team__api_source=canonical.api_source,
        current_team__external_id__in=team_ext_ids,
    ).select_related('entity', 'current_team')

    leaders_data = []
    for a in athletes:
        stats = EntityStats.objects.filter(
            entity=a.entity, season=season, stat_type='season'
        ).first()
        if stats and stats.stats_data and stat_type in stats.stats_data:
            leaders_data.append({
                'athlete_id': a.entity.id,
                'name':       f"{a.first_name} {a.last_name}",
                'photo':      a.entity.logo_url,
                'country':    a.entity.country,
                'team':       a.current_team.name if a.current_team else '',
                'team_logo':  a.current_team.logo_url if a.current_team else '',
                stat_type:    stats.stats_data.get(stat_type, 0),
            })

    if leaders_data:
        leaders_data.sort(key=lambda x: x.get(stat_type, 0), reverse=True)
        return Response({
            'league':    EntitySerializer(league_entity, context={'request': request}).data,
            'season':    season,
            'stat_type': stat_type,
            'leaders':   leaders_data[:20],
            'source':    'db',
        })

    # Live API fallback
    if canonical.api_source == 'statpal':
        live_leaders = _fetch_soccer_leaders(canonical.external_id, int(season), stat_type)
        return Response({
            'league':    EntitySerializer(league_entity, context={'request': request}).data,
            'season':    season,
            'stat_type': stat_type,
            'leaders':   live_leaders,
            'source':    'live_api' if live_leaders else 'empty',
        })

    return Response({
        'league':    EntitySerializer(league_entity, context={'request': request}).data,
        'season':    season,
        'stat_type': stat_type,
        'leaders':   [],
        'source':    'empty',
    })


# ─────────────────────────────────────────────────────────────────────────────
# LEAGUE FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([AllowAny])
def get_league_fixtures(request, league_id):
    """Retrieve recent results and upcoming schedule of fixtures for a league.

    Args:
        request (Request): HTTP GET request.
        league_id (int): Primary key ID of the league entity.

    Returns:
        Response: Serialized match event schedule.
    """
    from apps.event.models import Event
    from apps.event.serializers import EventSerializer as EvSerializer

    league_entity = get_object_or_404(Entity, id=league_id, type='league')
    league_entity = league_entity.canonical_entity or league_entity
    canonical = Entity.objects.filter(
        type='league',
        api_source=league_entity.api_source,
        external_id=league_entity.external_id,
    ).first() or league_entity

    events = Event.objects.filter(
        league__api_source=canonical.api_source,
        league__external_id=canonical.external_id,
    ).select_related('home_entity', 'away_entity').order_by('-start_time')[:50]

    from apps.event.views import _deduplicate_events
    events_list = _deduplicate_events(list(events))

    return Response({
        'league':   EntitySerializer(league_entity, context={'request': request}).data,
        'fixtures': EvSerializer(events_list, many=True).data,
    })


__all__ = [
    'get_league_standings',
    '_get_standings_for_league',
    '_fetch_league_standings_thesportsdb',
    '_fetch_statpal_hierarchical_standings',
    '_fetch_soccer_standings',
    'get_league_leaders',
    '_fetch_soccer_leaders',
    'get_league_fixtures',
]
