import logging
from django.shortcuts import get_object_or_404
from django.db.models import Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import PageNumberPagination

from apps.entity.models import Entity, Team, Athlete, League
from apps.entity.serializers import (
    EntitySerializer, TeamDetailSerializer,
    AthleteDetailSerializer, LeagueDetailSerializer
)
from apps.entity.services import EntitySearchService
from apps.core.utils.mixins import BaseResponseMixin

from .common import _current_season, resolve_team_venue

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Search / list / detail
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([AllowAny])
def search_entities(request):
    """Search active sports entities matching keyword and optional filters.

    Args:
        request (Request): HTTP request containing query params 'q', 'type', 'sport', 'country'.

    Returns:
        Response: Standard JSON response with matching entities list.
    """
    mixin = BaseResponseMixin()
    try:
        query = request.GET.get('q', '')
        entity_type = request.GET.get('type')
        sport = request.GET.get('sport')
        country = request.GET.get('country')

        if not query:
            return mixin.error_response(
                message='Query parameter "q" is required',
                status_code=status.HTTP_400_BAD_REQUEST
            )

        results = EntitySearchService.search(query, entity_type, sport, country)
        serializer = EntitySerializer(results, many=True, context={'request': request})
        data = {'query': query, 'count': len(results), 'results': serializer.data}
        return mixin.success_response(data=data)
    except Exception as exc:
        return mixin.handle_exception(exc)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_trending(request):
    """Retrieve trending sports entities grouped by type or query matching with fallback import.

    Args:
        request (Request): HTTP request with optional query params 'q', 'country', 'sport', 'type'.

    Returns:
        Response: Grouped entity results across teams, athletes, and leagues.
    """
    query   = request.GET.get('q', '').strip()
    country = request.GET.get('country')
    sport   = request.GET.get('sport')
    entity_type = request.GET.get('type')  # optional: filter by type

    base_qs = Entity.objects.filter(is_active=True)

    if country:
        base_qs = base_qs.filter(country__icontains=country)

    if query:
        # Search mode — filter by name, group by type
        filter_kwargs = {}
        if sport:
            filter_kwargs['sport'] = sport
        if entity_type:
            filter_kwargs['type'] = entity_type

        teams    = base_qs.filter(type='team', name__icontains=query, **filter_kwargs)[:10]
        athletes = base_qs.filter(type='athlete', name__icontains=query, **filter_kwargs)[:10]
        leagues  = base_qs.filter(type='league', name__icontains=query, **filter_kwargs)[:10]

        # Fallback 1: Typo handling (e.g. Casemero -> Casemiro)
        if not (teams.exists() or athletes.exists() or leagues.exists()):
            alt_query = query.replace('e', 'i') if 'e' in query.lower() else query.replace('i', 'e')
            teams    = base_qs.filter(type='team', name__icontains=alt_query, **filter_kwargs)[:10]
            athletes = base_qs.filter(type='athlete', name__icontains=alt_query, **filter_kwargs)[:10]
            leagues  = base_qs.filter(type='league', name__icontains=alt_query, **filter_kwargs)[:10]

        # Fallback 2: Auto-import from TheSportsDB if entity not in local DB
        if not (teams.exists() or athletes.exists() or leagues.exists()):
            try:
                from apps.sports_apis.services.thesportsdb import thesportsdb_service
                p_info = thesportsdb_service.get_player_details(query)
                if p_info:
                    p_name = p_info.get('name') or query
                    entity, _ = Entity.objects.get_or_create(
                        name=p_name,
                        type='athlete',
                        defaults={
                            'sport': p_info.get('sport') or 'soccer',
                            'logo_url': p_info.get('headshot_url') or '',
                            'country': p_info.get('nationality') or '',
                            'has_api_data': True,
                        }
                    )
                    Athlete.objects.get_or_create(
                        entity=entity,
                        defaults={
                            'first_name': p_name.split()[0] if p_name.split() else '',
                            'last_name': ' '.join(p_name.split()[1:]) if len(p_name.split()) > 1 else '',
                            'position': p_info.get('position', ''),
                            'nationality': p_info.get('nationality', ''),
                        }
                    )
                    athletes = Entity.objects.filter(id=entity.id)
            except Exception:
                pass
    else:
        # Trending mode — order by follower_count
        teams    = base_qs.filter(type='team').order_by('-follower_count')[:10]
        athletes = base_qs.filter(type='athlete').order_by('-follower_count')[:10]
        leagues  = base_qs.filter(type='league').order_by('-follower_count')[:10]

    return Response({
        'teams':    EntitySerializer(teams,    many=True, context={'request': request}).data,
        'athletes': EntitySerializer(athletes, many=True, context={'request': request}).data,
        'leagues':  EntitySerializer(leagues,  many=True, context={'request': request}).data,
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_entity_detail(request, entity_id):
    """Retrieve full details for an entity by primary key, routing to specific subtype serializers.

    Args:
        request (Request): HTTP GET request.
        entity_id (int): Primary key ID of the entity.

    Returns:
        Response: Detailed serialized entity representation.
    """
    entity = get_object_or_404(Entity, id=entity_id)
    entity = entity.canonical_entity or entity
    if entity.type == 'team':
        try:
            serializer = TeamDetailSerializer(entity.team_details, context={'request': request})
        except Team.DoesNotExist:
            mock_team = Team(entity=entity)
            serializer = TeamDetailSerializer(mock_team, context={'request': request})
    elif entity.type == 'athlete':
        try:
            serializer = AthleteDetailSerializer(entity.athlete_details, context={'request': request})
        except Athlete.DoesNotExist:
            mock_athlete = Athlete(entity=entity)
            serializer = AthleteDetailSerializer(mock_athlete, context={'request': request})
    elif entity.type == 'league':
        try:
            serializer = LeagueDetailSerializer(entity.league_details, context={'request': request})
        except League.DoesNotExist:
            mock_league = League(entity=entity)
            serializer = LeagueDetailSerializer(mock_league, context={'request': request})
    else:
        serializer = EntitySerializer(entity, context={'request': request})
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_entity_by_slug(request, slug):
    """Retrieve entity details using a slug identifier.

    Args:
        request (Request): HTTP GET request.
        slug (str): Unique slug string of the entity.

    Returns:
        Response: Detailed serialized entity representation.
    """
    entity = get_object_or_404(Entity, slug=slug)
    entity = entity.canonical_entity or entity
    if entity.type == 'team':
        try:
            serializer = TeamDetailSerializer(entity.team_details, context={'request': request})
        except Team.DoesNotExist:
            mock_team = Team(entity=entity)
            serializer = TeamDetailSerializer(mock_team, context={'request': request})
    elif entity.type == 'athlete':
        try:
            serializer = AthleteDetailSerializer(entity.athlete_details, context={'request': request})
        except Athlete.DoesNotExist:
            mock_athlete = Athlete(entity=entity)
            serializer = AthleteDetailSerializer(mock_athlete, context={'request': request})
    elif entity.type == 'league':
        try:
            serializer = LeagueDetailSerializer(entity.league_details, context={'request': request})
        except League.DoesNotExist:
            mock_league = League(entity=entity)
            serializer = LeagueDetailSerializer(mock_league, context={'request': request})
    else:
        serializer = EntitySerializer(entity, context={'request': request})
    return Response(serializer.data)


# ─────────────────────────────────────────────────────────────────────────────
# UNIVERSAL ENDPOINTS — frontend uses these for any entity type
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([AllowAny])
def get_entity_stats(request, entity_id):
    """
    Universal stats — works for team, league, athlete.
    GET /api/entities/{entity_id}/stats/

    - team    → single team stats card (wins/losses/form/points)
    - league  → list of all teams stats in that league
    - athlete → player stats (goals/assists/appearances)
    """
    from .team import get_team_stats
    from .athlete import get_athlete_stats
    from .league import _get_standings_for_league

    entity = get_object_or_404(Entity, id=entity_id)
    entity = entity.canonical_entity or entity

    if entity.type == 'team':
        return get_team_stats(request._request, entity.id)

    elif entity.type == 'league':
        season = request.GET.get('season') or str(_current_season('soccer'))
        return _get_standings_for_league(request, entity, season)

    elif entity.type == 'athlete':
        return get_athlete_stats(request._request, entity.id)

    return Response({
        'entity': EntitySerializer(entity, context={'request': request}).data,
        'stats': {},
        'message': 'No stats available for this entity type',
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_entity_fixtures(request, entity_id):
    """
    Universal fixtures — works for team and league.
    GET /api/entities/{entity_id}/fixtures/

    - team   → all matches where this team is home or away
    - league → all matches in this league
    """
    from apps.event.models import Event
    from apps.event.serializers import EventSerializer as EvSerializer
    from .team import _fetch_team_fixtures_live

    entity = get_object_or_404(Entity, id=entity_id)
    entity = entity.canonical_entity or entity

    if entity.type == 'team':
        team_ids = set(
            Entity.objects.filter(
                Q(id=entity.id) |
                Q(canonical_entity=entity) |
                Q(name__iexact=entity.name)
            ).values_list('id', flat=True)
        )
        events = Event.objects.filter(
            Q(home_entity_id__in=team_ids) | Q(away_entity_id__in=team_ids)
        ).select_related(
            'home_entity', 'away_entity', 'league'
        ).order_by('-start_time')[:50]

    elif entity.type == 'league':
        league_ids = set(
            Entity.objects.filter(
                Q(id=entity.id) |
                Q(canonical_entity=entity) |
                Q(name__iexact=entity.name)
            ).values_list('id', flat=True)
        )
        events = Event.objects.filter(
            Q(league_id__in=league_ids) |
            (Q(league__api_source=entity.api_source) & Q(league__external_id=entity.external_id))
        ).select_related(
            'home_entity', 'away_entity', 'league'
        ).order_by('-start_time')[:50]

    else:
        events = Event.objects.none()

    if not events.exists() and entity.type == 'team':
        live_fixtures = _fetch_team_fixtures_live(entity)
        if live_fixtures:
            return Response({
                'entity':          EntitySerializer(entity, context={'request': request}).data,
                'fixtures_count':  len(live_fixtures),
                'fixtures':        live_fixtures,
                'source':          'live_api',
            })

    serialized_fixtures = EvSerializer(events, many=True).data
    from apps.entity.utils.matcher import find_team_logo_by_name

    for f in serialized_fixtures:
        if f.get('home_entity') and not f['home_entity'].get('logo_url'):
            h_name = f['home_entity'].get('name', '')
            l_url = find_team_logo_by_name(h_name)
            if l_url:
                f['home_entity']['logo_url'] = l_url
                f['home_logo'] = l_url
                if f.get('is_nest_entity_home'):
                    f['nest_entity_logo'] = l_url
                    f['primary_logo_url'] = l_url
                else:
                    f['opponent_entity_logo'] = l_url

        if f.get('away_entity') and not f['away_entity'].get('logo_url'):
            a_name = f['away_entity'].get('name', '')
            l_url = find_team_logo_by_name(a_name)
            if l_url:
                f['away_entity']['logo_url'] = l_url
                f['away_logo'] = l_url
                if not f.get('is_nest_entity_home'):
                    f['nest_entity_logo'] = l_url
                    f['primary_logo_url'] = l_url
                else:
                    f['opponent_entity_logo'] = l_url

        # Auto-fill missing venue name, city, and country
        if not f.get('venue_name') or not f.get('venue_city') or not f.get('venue_country'):
            home_team_name = f.get('home_entity', {}).get('name') or f.get('home_team', '')
            v_name, v_city, v_country = resolve_team_venue(home_team_name)
            if not f.get('venue_name') and v_name:
                f['venue_name'] = v_name
            if not f.get('venue_city') and v_city:
                f['venue_city'] = v_city
            if not f.get('venue_country') and v_country:
                f['venue_country'] = v_country

    return Response({
        'entity':          EntitySerializer(entity, context={'request': request}).data,
        'fixtures_count':  len(serialized_fixtures),
        'fixtures':        serialized_fixtures,
        'source':          'db',
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_entity_roster(request, entity_id):
    """
    Universal roster — works for team and athlete.
    GET /api/entities/{entity_id}/roster/

    - team    → list of players in the team
    - athlete → just that athlete's bio/details
    """
    from .team import get_team_roster
    from .athlete import get_athlete_bio

    entity = get_object_or_404(Entity, id=entity_id)
    entity = entity.canonical_entity or entity

    if entity.type == 'team':
        return get_team_roster(request._request, entity.id)

    elif entity.type == 'athlete':
        return get_athlete_bio(request._request, entity.id)

    return Response({
        'entity':  EntitySerializer(entity, context={'request': request}).data,
        'roster':  [],
        'message': 'Roster only available for teams',
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_entity_standings(request, entity_id):
    """
    Universal standings — works for team and league.
    GET /api/entities/{entity_id}/standings/

    - team   → full league table with this team highlighted
    - league → full league table
    """
    from .team import get_team_standings
    from .league import _get_standings_for_league

    entity = get_object_or_404(Entity, id=entity_id)
    entity = entity.canonical_entity or entity
    get_params = getattr(request, 'GET', {}) or getattr(getattr(request, '_request', None), 'GET', {})
    season = get_params.get('season') or str(_current_season(entity.sport or 'soccer'))

    if entity.sport == 'cricket':
        django_req = getattr(request, '_request', request)
        return get_team_standings(django_req, entity.id)

    if entity.type == 'team':
        django_req = getattr(request, '_request', request)
        return get_team_standings(django_req, entity.id)

    elif entity.type == 'league':
        return _get_standings_for_league(request, entity, season)

    return Response({
        'entity':    EntitySerializer(entity, context={'request': request}).data,
        'standings': [],
        'message':   'Standings only available for teams and leagues',
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def list_entities(request):
    """Retrieve a paginated list of sports entities filtered by sport, type, or country.

    Args:
        request (Request): HTTP GET request with optional query params 'type', 'sport', 'country', 'limit'.

    Returns:
        Response: Paginated entity list response.
    """
    queryset = Entity.objects.filter(is_active=True).order_by('-follower_count', 'name')

    entity_type = request.GET.get('type')
    sport       = request.GET.get('sport')
    country     = request.GET.get('country')

    if entity_type:
        queryset = queryset.filter(type=entity_type)
    if sport:
        queryset = queryset.filter(sport=sport)
    if country:
        queryset = queryset.filter(country__icontains=country)

    paginator = PageNumberPagination()
    paginator.page_size     = int(request.GET.get('limit', 20))
    paginator.max_page_size = 100
    paginated = paginator.paginate_queryset(queryset, request)

    serializer = EntitySerializer(paginated, many=True, context={'request': request})
    return paginator.get_paginated_response(serializer.data)
