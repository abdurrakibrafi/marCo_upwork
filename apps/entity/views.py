
from django.shortcuts import render
from django.db.models import Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from django.shortcuts import get_object_or_404
from django.core.cache import cache
from datetime import datetime
import requests as req
from django.conf import settings
 
from apps.entity.models import Entity, Team, Athlete, League, EntityStats
from apps.entity.serializers import (
    EntitySerializer, TeamDetailSerializer,
    AthleteDetailSerializer, LeagueDetailSerializer
)
from apps.entity.services import EntitySearchService
from apps.core.utils.mixins import BaseResponseMixin
 
HEADERS_SPORTS = {'x-apisports-key': settings.API_SPORTS_KEY}
HEADERS_BDL    = {'Authorization': settings.BALLDONTLIE_KEY}
 
 
def _current_season(sport='soccer'):
    now = datetime.now()
    year, month = now.year, now.month
    if sport == 'soccer':
        return year if month >= 8 else year - 1
    elif sport == 'basketball':
        return year if month >= 10 else year - 1
    return year
 
 
# ─────────────────────────────────────────────────────────────────────────────
# Search / list / detail  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────
 
@api_view(['GET'])
@permission_classes([AllowAny])
def search_entities(request):
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
    query   = request.GET.get('q', '').strip()
    country = request.GET.get('country')
    sport   = request.GET.get('sport')
    entity_type = request.GET.get('type')  # optional: filter by type

    base_qs = Entity.objects.filter(is_active=True)

    if country:
        base_qs = base_qs.filter(country__icontains=country)

    if query:
        # Search mode — filter by name, group by type
        base_qs = base_qs.filter(name__icontains=query)

        if sport:
            base_qs = base_qs.filter(sport=sport)

        if entity_type:
            base_qs = base_qs.filter(type=entity_type)

        teams    = base_qs.filter(type='team')[:10]
        athletes = base_qs.filter(type='athlete')[:10]
        leagues  = base_qs.filter(type='league')[:10]
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

    entity = get_object_or_404(Entity, id=entity_id)
    entity = entity.canonical_entity or entity

    if entity.type == 'team':
        events = Event.objects.filter(
            Q(home_entity=entity) | Q(away_entity=entity)
        ).select_related(
            'home_entity', 'away_entity', 'league'
        ).order_by('-start_time')[:50]

    elif entity.type == 'league':
        events = Event.objects.filter(
            league__api_source=entity.api_source,
            league__external_id=entity.external_id,
        ).select_related(
            'home_entity', 'away_entity', 'league'
        ).order_by('-start_time')[:50]

    else:
        events = Event.objects.none()

    return Response({
        'entity':          EntitySerializer(entity, context={'request': request}).data,
        'fixtures_count':  events.count(),
        'fixtures':        EvSerializer(events, many=True).data,
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
    entity = get_object_or_404(Entity, id=entity_id)
    entity = entity.canonical_entity or entity
    season = request.GET.get('season') or str(_current_season('soccer'))

    if entity.type == 'team':
        return get_team_standings(request._request, entity.id)

    elif entity.type == 'league':
        return _get_standings_for_league(request, entity, season)

    return Response({
        'entity':    EntitySerializer(entity, context={'request': request}).data,
        'standings': [],
        'message':   'Standings only available for teams and leagues',
    })


# ─────────────────────────────────────────────────────────────────────────────
# TEAM STATS  — DB first, live API fallback
# ─────────────────────────────────────────────────────────────────────────────
 
@api_view(['GET'])
@permission_classes([AllowAny])
def get_team_stats(request, team_id):
    """
    GET /api/entities/team/{team_id}/stats/?season=2024
    """
    team_entity = get_object_or_404(Entity, id=team_id, type='team')
    team_entity = team_entity.canonical_entity or team_entity
    season = request.GET.get('season') or str(_current_season(team_entity.sport))
 
    # 1 — try DB first
    stats = EntityStats.objects.filter(entity=team_entity, season=season).first()
    if stats and stats.stats_data:
        return Response({
            'team': EntitySerializer(team_entity, context={'request': request}).data,
            'season': season,
            'stats': stats.stats_data,
            'source': 'db',
        })
 
    # 2 — live API fallback
    stats_data = {}
 
    if team_entity.sport == 'soccer':
        if team_entity.api_source == 'api_sports':
            stats_data = _fetch_soccer_team_stats(team_entity.external_id, int(season))
        else:
            # statpal or any other source
            stats_data = _fetch_soccer_team_stats_statpal(team_entity.external_id, int(season))
 
    elif team_entity.sport == 'basketball':
        # Always use StatPal standings (balldontlie is no longer in use)
        stats_data = _fetch_nba_team_stats_statpal(team_entity.external_id, int(season))
 
    elif team_entity.sport == 'football':
        stats_data = _fetch_nfl_team_stats(team_entity.external_id, int(season))
 
    elif team_entity.sport == 'hockey':
        stats_data = _fetch_nhl_team_stats(team_entity.name, int(season))
 
    elif team_entity.sport == 'baseball':
        stats_data = _fetch_mlb_team_stats(team_entity.external_id, int(season))
 
    elif team_entity.sport == 'cricket':
        stats_data = _fetch_cricket_team_stats(team_entity.external_id, int(season))
 
    # tennis / golf / mma / f1 have no team-standings API — return empty gracefully
 
    # 3 — save to DB so next call is instant
    if stats_data:
        EntityStats.objects.update_or_create(
            entity=team_entity,
            season=season,
            stat_type='season',
            defaults={'stats_data': stats_data},
        )
 
    return Response({
        'team': EntitySerializer(team_entity, context={'request': request}).data,
        'season': season,
        'stats': stats_data,
        'source': 'live_api' if stats_data else 'empty',
    })
 
 
def _fetch_cricket_team_stats(external_id, season):
    """
    Build win/loss/draw stats for a cricket team by scanning all active
    StatPal tours and filtering completed matches that involve this team.

    Cricket national teams play bilateral series (no single league table),
    so we aggregate across every tour in the tour-list that overlaps the
    requested season year.  Draws ("Match drawn") and No-results are
    counted separately from losses.
    """
    cache_key = f'team_stats:cricket:{external_id}:{season}:statpal'
    cached = cache.get(cache_key)
    if cached:
        return cached

    try:
        from apps.sports_apis.services.statpal import statpal_service

        # 1. Fetch the tour list
        tours_resp = statpal_service.get_cricket_tournaments()
        if not tours_resp.get('success'):
            return {}

        tours_raw = tours_resp.get('data', {}).get('tours', {}).get('category', [])
        if isinstance(tours_raw, dict):
            tours_raw = [tours_raw]

        wins = losses = draws = no_results = 0

        for tour in tours_raw:
            tour_id   = tour.get('id')
            tour_uri  = tour.get('schedule_uri', '')  # e.g. '/tour/1114' or '/intl/5536'

            if not tour_id or not tour_uri:
                continue

            # Derive tournament_type from the URI prefix
            parts = [p for p in tour_uri.strip('/').split('/') if p]
            if len(parts) < 2:
                continue
            tournament_type = parts[0]   # 'tour' or 'intl'
            tournament_id   = parts[1]

            # 2. Fetch season-schedule for this tour
            try:
                sched_resp = statpal_service.get_cricket_schedule(tournament_type, tournament_id)
            except Exception:
                continue

            if not sched_resp.get('success'):
                continue

            scores = sched_resp.get('data', {}).get('scores', {})
            cats   = scores.get('category', [])
            if isinstance(cats, dict):
                cats = [cats]

            for cat in cats:
                matches = cat.get('match', [])
                if isinstance(matches, dict):
                    matches = [matches]

                for match in matches:
                    # Only count completed matches
                    if str(match.get('status', '')).lower() not in ('finished', 'complete', 'completed'):
                        continue

                    home = match.get('home', {})
                    away = match.get('away', {})
                    home_id = str(home.get('id', ''))
                    away_id = str(away.get('id', ''))

                    if str(external_id) not in (home_id, away_id):
                        continue

                    # Determine result
                    comment_post = str(match.get('comment', {}).get('post', '')).lower()
                    home_winner = str(home.get('winner', '')).lower()
                    away_winner = str(away.get('winner', '')).lower()

                    if 'drawn' in comment_post or 'draw' in comment_post:
                        draws += 1
                    elif 'no result' in comment_post or 'abandoned' in comment_post:
                        no_results += 1
                    else:
                        team_is_home = (str(external_id) == home_id)
                        team_won = (team_is_home and home_winner == 'true') or \
                                   (not team_is_home and away_winner == 'true')
                        if team_won:
                            wins += 1
                        else:
                            losses += 1

        matches_played = wins + losses + draws + no_results
        if matches_played == 0:
            return {}

        stats_data = {
            'wins':           wins,
            'losses':         losses,
            'draws':          draws,
            'no_results':     no_results,
            'matches_played': matches_played,
            'win_percentage': round(wins / matches_played * 100, 1),
        }
        cache.set(cache_key, stats_data, timeout=3600)
        return stats_data

    except Exception:
        return {}

def _fetch_nfl_team_stats(external_id, season):
    """
    NFL stats from StatPal /nfl/standings.
    Standings structure: standings → category[] → league[] → division[] → team[]
    Fields: won, lost, ties, win_percentage, points_for, points_against, difference.
    """
    cache_key = f'team_stats:football:{external_id}:{season}:statpal'
    cached = cache.get(cache_key)
    if cached:
        return cached

    try:
        from apps.sports_apis.services.statpal import statpal_service
        result = statpal_service.get_nfl_standings()
        if not result.get('success'):
            return {}

        cats = result['data'].get('standings', {}).get('category', [])
        if isinstance(cats, dict):
            cats = [cats]

        for cat in cats:
            leagues = cat.get('league', [])
            if isinstance(leagues, dict):
                leagues = [leagues]
            for lg in leagues:
                divs = lg.get('division', [])
                if isinstance(divs, dict):
                    divs = [divs]
                for div in divs:
                    teams = div.get('team', [])
                    if isinstance(teams, dict):
                        teams = [teams]
                    for t in teams:
                        if str(t.get('id', '')) == str(external_id):
                            wins   = int(t.get('won') or 0)
                            losses = int(t.get('lost') or 0)
                            ties   = int(t.get('ties') or 0)
                            played = wins + losses + ties
                            stats_data = {
                                'wins':           wins,
                                'losses':         losses,
                                'ties':           ties,
                                'matches_played': played,
                                'win_percentage': float(t.get('win_percentage', '0').replace('.', '0.', 1)
                                                        if t.get('win_percentage', '').startswith('.') else
                                                        t.get('win_percentage') or 0),
                                'points_for':     int(t.get('points_for') or 0),
                                'points_against': int(t.get('points_against') or 0),
                                'conference':     lg.get('name', ''),
                                'division':       div.get('name', ''),
                                'rank':           int(t.get('position') or 0),
                                'streak':         t.get('streak', ''),
                                'home_record':    t.get('home_record', ''),
                                'road_record':    t.get('road_record', ''),
                            }
                            cache.set(cache_key, stats_data, timeout=3600)
                            return stats_data
        return {}
    except Exception:
        return {}


def _fetch_nhl_team_stats(team_name, season):
    """
    NHL stats from StatPal /nhl/standings.
    Standings structure: standings → tournament → league[] → division[] → team[]
    Matches by team name (case-insensitive) because StatPal's team id is a
    numeric internal id that differs from the abbreviation stored in external_id.
    Fields: won, lost, ot_losses, points, games_played, goals_for, goals_against.
    """
    cache_key = f'team_stats:hockey:{team_name}:{season}:statpal'
    cached = cache.get(cache_key)
    if cached:
        return cached

    try:
        from apps.sports_apis.services.statpal import statpal_service
        result = statpal_service.get_nhl_standings()
        if not result.get('success'):
            return {}

        tourn = result['data'].get('standings', {}).get('tournament', {})
        leagues = tourn.get('league', [])
        if isinstance(leagues, dict):
            leagues = [leagues]

        for lg in leagues:
            divs = lg.get('division', [])
            if isinstance(divs, dict):
                divs = [divs]
            for div in divs:
                teams = div.get('team', [])
                if isinstance(teams, dict):
                    teams = [teams]
                for t in teams:
                    if str(t.get('name', '')).lower() == str(team_name).lower():
                        wins      = int(t.get('won') or t.get('regular_ot_wins') or 0)
                        losses    = int(t.get('lost') or 0)
                        ot_losses = int(t.get('ot_losses') or 0)
                        played    = int(t.get('games_played') or (wins + losses + ot_losses))
                        stats_data = {
                            'wins':           wins,
                            'losses':         losses,
                            'ot_losses':      ot_losses,
                            'points':         int(t.get('points') or 0),
                            'matches_played': played,
                            'goals_for':      int(t.get('goals_for') or 0),
                            'goals_against':  int(t.get('goals_against') or 0),
                            'difference':     t.get('difference', ''),
                            'conference':     lg.get('name', ''),
                            'division':       div.get('name', ''),
                            'rank':           int(t.get('position') or 0),
                            'streak':         t.get('streak', ''),
                            'home_record':    t.get('home_record', ''),
                            'road_record':    t.get('road_record', ''),
                        }
                        cache.set(cache_key, stats_data, timeout=3600)
                        return stats_data
        return {}
    except Exception:
        return {}


def _fetch_mlb_team_stats(external_id, season):
    """
    MLB stats — StatPal doesn't expose a full MLB standings endpoint.
    We aggregate wins/losses from the last 7 days of daily schedules
    (d-7 to d-1) for completed matches involving this team.
    """
    cache_key = f'team_stats:baseball:{external_id}:{season}:statpal'
    cached = cache.get(cache_key)
    if cached:
        return cached

    try:
        from apps.sports_apis.services.statpal import statpal_service
        wins = losses = 0

        # Scan recent days for finished MLB matches
        for offset in range(-7, 0):   # d-7 through d-1
            result = statpal_service.get_mlb_fixtures(offset=offset)
            if not result.get('success'):
                continue

            scores = result['data'].get('scores', {})
            tourn  = scores.get('tournament', {})
            matches = tourn.get('match', [])
            if isinstance(matches, dict):
                matches = [matches]

            for match in matches:
                if str(match.get('status', '')).lower() != 'finished':
                    continue
                home = match.get('home', {})
                away = match.get('away', {})
                if str(external_id) not in (str(home.get('id', '')), str(away.get('id', ''))):
                    continue

                try:
                    home_score = int(home.get('totalscore') or 0)
                    away_score = int(away.get('totalscore') or 0)
                except (ValueError, TypeError):
                    continue

                team_is_home = str(external_id) == str(home.get('id', ''))
                if team_is_home:
                    if home_score > away_score:
                        wins += 1
                    elif home_score < away_score:
                        losses += 1
                else:
                    if away_score > home_score:
                        wins += 1
                    elif away_score < home_score:
                        losses += 1

        played = wins + losses
        if played == 0:
            return {}

        stats_data = {
            'wins':           wins,
            'losses':         losses,
            'matches_played': played,
            'win_percentage': round(wins / played * 100, 1),
            'note':           'Last 7 days only (StatPal MLB has no standings endpoint)',
        }
        cache.set(cache_key, stats_data, timeout=3600)
        return stats_data

    except Exception:
        return {}


def _fetch_soccer_team_stats(external_id, season):
    """Hit API-Sports /teams/statistics for one team."""
    cache_key = f'team_stats:soccer:{external_id}:{season}'
    cached = cache.get(cache_key)
    if cached:
        return cached
 
    try:
        # We need the league id — get the first league linked to this team
        team_entity = Entity.objects.filter(
            api_source='api_sports', external_id=str(external_id)
        ).first()
        league_id = None
        if team_entity:
            try:
                league_id = team_entity.team_details.league.external_id
            except Exception:
                pass
 
        if not league_id:
            # Fallback 1: Try to find a league from the team's events in DB
            from django.db.models import Q
            from apps.event.models import Event
            event = Event.objects.filter(
                Q(home_entity=team_entity) | Q(away_entity=team_entity),
                league__isnull=False
            ).select_related('league').first()
            if event:
                league_id = event.league.external_id

        if not league_id:
            # Fallback 2: Query API-Sports leagues endpoint directly
            try:
                resp = req.get(
                    'https://v3.football.api-sports.io/leagues',
                    headers=HEADERS_SPORTS,
                    params={'team': external_id, 'season': season},
                    timeout=10,
                )
                if resp.status_code == 200:
                    leagues_data = resp.json().get('response', [])
                    if leagues_data:
                        league_id = leagues_data[0].get('league', {}).get('id')
            except Exception:
                pass

        if not league_id:
            return {}
 
        resp = req.get(
            'https://v3.football.api-sports.io/teams/statistics',
            headers=HEADERS_SPORTS,
            params={'team': external_id, 'season': season, 'league': league_id},
            timeout=10,
        )
        if resp.status_code != 200:
            return {}
 
        data = resp.json().get('response', {})
        if not data:
            return {}
 
        fixtures = data.get('fixtures', {})
        goals    = data.get('goals', {})
 
        stats_data = {
            'form':           data.get('form', ''),
            'played':         fixtures.get('played', {}).get('total', 0),
            'wins':           fixtures.get('wins', {}).get('total', 0),
            'draws':          fixtures.get('draws', {}).get('total', 0),
            'losses':         fixtures.get('loses', {}).get('total', 0),
            'goals_for':      goals.get('for', {}).get('total', {}).get('total', 0),
            'goals_against':  goals.get('against', {}).get('total', {}).get('total', 0),
            'clean_sheets':   data.get('clean_sheet', {}).get('total', 0),
            'failed_to_score':data.get('failed_to_score', {}).get('total', 0),
        }
        cache.set(cache_key, stats_data, timeout=3600)
        return stats_data
 
    except Exception:
        return {}
 
 
def _fetch_nba_team_stats(external_id, season):
    """Hit BallDontLie standings for one NBA team."""
    cache_key = f'team_stats:nba:{external_id}:{season}'
    cached = cache.get(cache_key)
    if cached:
        return cached
 
    try:
        resp = req.get(
            'https://api.balldontlie.io/v1/standings',
            headers=HEADERS_BDL,
            params={'season': season},
            timeout=10,
        )
        if resp.status_code != 200:
            return {}
 
        standings = resp.json().get('data', [])
        for s in standings:
            if str(s.get('team', {}).get('id', '')) == str(external_id):
                wins   = s.get('wins', 0)
                losses = s.get('losses', 0)
                total  = wins + losses
                stats_data = {
                    'wins':       wins,
                    'losses':     losses,
                    'win_pct':    round(wins / total * 100, 1) if total else 0,
                    'conference': s.get('conference', ''),
                    'division':   s.get('division', ''),
                    'rank':       s.get('rank', 0),
                }
                cache.set(cache_key, stats_data, timeout=3600)
                return stats_data
        return {}
 
    except Exception:
        return {}


def _fetch_soccer_team_stats_statpal(external_id, season):
    cache_key = f'team_stats:soccer:{external_id}:{season}:statpal'
    cached = cache.get(cache_key)
    if cached:
        return cached
        
    try:
        from apps.sports_apis.services.statpal import statpal_service
        result = statpal_service.get_soccer_team(external_id)
        if not result['success']:
            return {}
            
        leagues = result['data'].get('team', {}).get('league_stats', {}).get('league', [])
        if isinstance(leagues, dict):
            leagues = [leagues]
            
        lstat = None
        for l in leagues:
            if str(l.get('season')) == str(season):
                lstat = l
                break
        if not lstat and leagues:
            lstat = leagues[0]
            
        if not lstat:
            return {}
            
        ft = lstat.get('fulltime', {})
        wins = int(ft.get('win', {}).get('total') or 0)
        losses = int(ft.get('lost', {}).get('total') or 0)
        draws = int(ft.get('draw', {}).get('total') or 0)
        played = wins + losses + draws
        
        stats_data = {
            'form':           '',
            'played':         played,
            'wins':           wins,
            'draws':          draws,
            'losses':         losses,
            'goals_for':      int(ft.get('goals_for', {}).get('total') or 0),
            'goals_against':  int(ft.get('goals_against', {}).get('total') or 0),
            'clean_sheets':   int(ft.get('clean_sheet', {}).get('total') or 0),
            'failed_to_score':int(ft.get('failed_to_score', {}).get('total') or 0),
        }
        cache.set(cache_key, stats_data, timeout=3600)
        return stats_data
    except Exception:
        return {}


def _fetch_nba_team_stats_statpal(external_id, season):
    cache_key = f'team_stats:nba:{external_id}:{season}:statpal'
    cached = cache.get(cache_key)
    if cached:
        return cached
        
    try:
        from apps.sports_apis.services.statpal import statpal_service
        result = statpal_service.get_nba_standings()
        if not result['success']:
            return {}
            
        standings = result['data'].get('standings', {})
        leagues = standings.get('tournament', {}).get('league', [])
        if isinstance(leagues, dict):
            leagues = [leagues]
            
        for lg in leagues:
            conferences = lg.get('division', [])
            if isinstance(conferences, dict):
                conferences = [conferences]
                
            for conf in conferences:
                teams_list = conf.get('team', [])
                if isinstance(teams_list, dict):
                    teams_list = [teams_list]
                    
                for standing in teams_list:
                    if str(standing.get('id', '')) == str(external_id):
                        wins = int(standing.get('won') or 0)
                        losses = int(standing.get('lost') or 0)
                        total = wins + losses
                        stats_data = {
                            'wins':       wins,
                            'losses':     losses,
                            'win_pct':    round(wins / total * 100, 1) if total else 0,
                            'conference': conf.get('name', ''),
                            'division':   lg.get('name', ''),
                            'rank':       int(standing.get('position') or 0),
                        }
                        cache.set(cache_key, stats_data, timeout=3600)
                        return stats_data
        return {}
    except Exception:
        return {}
 
 
# ─────────────────────────────────────────────────────────────────────────────
# TEAM ROSTER  (unchanged — reads from Athlete table which is already seeded)
# ─────────────────────────────────────────────────────────────────────────────
 
@api_view(['GET'])
@permission_classes([AllowAny])
def get_team_roster(request, team_id):
    team_entity = get_object_or_404(Entity, id=team_id, type='team')
    team_entity = team_entity.canonical_entity or team_entity
 
    from django.db.models import Q
    athletes = Athlete.objects.filter(
        Q(current_team=team_entity)
        | Q(current_team__external_id=team_entity.external_id, current_team__sport=team_entity.sport)
    ).select_related('entity').distinct()
 
    if not athletes.exists() and team_entity.api_source == 'api_sports':
        from apps.entity.tasks import seed_players_for_team
        season = _current_season(team_entity.sport)
        seed_players_for_team.delay(team_entity.external_id, season)
        return Response({
            'team': EntitySerializer(team_entity, context={'request': request}).data,
            'roster_count': 0,
            'roster': [],
            'message': 'Roster is being fetched, try again in 10 seconds'
        })
 
    roster = []
    for a in athletes:
        roster.append({
            'id':            a.entity.id,
            'name':          f"{a.first_name} {a.last_name}",
            'position':      a.position,
            'jersey_number': a.jersey_number,
            'photo':         a.entity.logo_url,
            'height_cm':     a.height_cm,
            'weight_kg':     a.weight_kg,
            'nationality':   a.nationality,
        })
 
    return Response({
        'team':         EntitySerializer(team_entity, context={'request': request}).data,
        'roster_count': len(roster),
        'roster':       roster,
    })
 
# ─────────────────────────────────────────────────────────────────────────────
# TEAM STANDINGS  — DB first, live API fallback
# ─────────────────────────────────────────────────────────────────────────────
 
@api_view(['GET'])
@permission_classes([AllowAny])
def get_team_standings(request, team_id):
    """
    GET /api/entities/team/{team_id}/standings/
    Returns the full league table so the app can highlight this team's row.
    """
    team_entity = get_object_or_404(Entity, id=team_id, type='team')
    team_entity = team_entity.canonical_entity or team_entity
    season = request.GET.get('season') or str(_current_season(team_entity.sport))
 
    try:
        league = team_entity.team_details.league
    except Exception:
        league = None
 
    if not league:
        # Fallback 1: Try to find a league from the team's events in DB
        from django.db.models import Q
        from apps.event.models import Event
        event = Event.objects.filter(
            Q(home_entity=team_entity) | Q(away_entity=team_entity),
            league__isnull=False
        ).select_related('league').first()
        if event:
            league = event.league

    if not league:
        # Fallback 2: Query API-Sports leagues endpoint to find a league entity
        if team_entity.api_source == 'api_sports':
            try:
                resp = req.get(
                    'https://v3.football.api-sports.io/leagues',
                    headers=HEADERS_SPORTS,
                    params={'team': team_entity.external_id, 'season': season},
                    timeout=10,
                )
                if resp.status_code == 200:
                    leagues_data = resp.json().get('response', [])
                    if leagues_data:
                        first_league = leagues_data[0]
                        lg_id = first_league.get('league', {}).get('id')
                        lg_name = first_league.get('league', {}).get('name')
                        # Check if League Entity exists
                        league = Entity.objects.filter(api_source='api_sports', external_id=str(lg_id)).first()
                        if not league:
                            # Create a temporary/mock League Entity so we can fetch standings
                            league = Entity.objects.create(
                                name=lg_name,
                                type='league',
                                sport='soccer',
                                api_source='api_sports',
                                external_id=str(lg_id),
                                has_api_data=True
                            )
                            from apps.entity.models import League
                            League.objects.get_or_create(entity=league)
            except Exception:
                pass

    if not league:
        return Response({
            'team':     EntitySerializer(team_entity, context={'request': request}).data,
            'season':   season,
            'standings': [],
            'message':  'No league linked to this team',
        })
 
    # Delegate to league standings view logic
    return _get_standings_for_league(request, league, season, highlight_team_id=team_entity.external_id)
 
 
# ─────────────────────────────────────────────────────────────────────────────
# ATHLETE STATS  — DB first, live API fallback
# ─────────────────────────────────────────────────────────────────────────────
 
@api_view(['GET'])
@permission_classes([AllowAny])
def get_athlete_stats(request, athlete_id):
    """
    GET /api/entities/athlete/{athlete_id}/stats/?season=2024
    """
    athlete_entity = get_object_or_404(Entity, id=athlete_id, type='athlete')
    athlete_entity = athlete_entity.canonical_entity or athlete_entity
    season = request.GET.get('season') or str(_current_season(athlete_entity.sport))
 
    # 1 — try DB first
    stats = EntityStats.objects.filter(entity=athlete_entity, season=season).first()
    if stats and stats.stats_data:
        return Response({
            'athlete': EntitySerializer(athlete_entity, context={'request': request}).data,
            'season':  season,
            'stats':   stats.stats_data,
            'source':  'db',
        })
 
    # 2 — live API fallback
    stats_data = {}
 
    if athlete_entity.sport == 'soccer' and athlete_entity.api_source == 'api_sports':
        stats_data = _fetch_soccer_player_stats(athlete_entity.external_id, int(season))
 
    # 3 — save to DB
    if stats_data:
        EntityStats.objects.update_or_create(
            entity=athlete_entity,
            season=season,
            stat_type='season',
            defaults={'stats_data': stats_data},
        )
 
    return Response({
        'athlete': EntitySerializer(athlete_entity, context={'request': request}).data,
        'season':  season,
        'stats':   stats_data,
        'source':  'live_api' if stats_data else 'empty',
    })
 
 
def _fetch_soccer_player_stats(external_id, season):
    cache_key = f'player_stats:soccer:{external_id}:{season}'
    cached = cache.get(cache_key)
    if cached:
        return cached
 
    try:
        resp = req.get(
            'https://v3.football.api-sports.io/players',
            headers=HEADERS_SPORTS,
            params={'id': external_id, 'season': season},
            timeout=10,
        )
        if resp.status_code != 200:
            return {}
 
        response = resp.json().get('response', [])
        if not response:
            return {}
 
        player   = response[0]
        p_info   = player.get('player', {})
        # Use the first statistics entry (primary league/team)
        s        = player.get('statistics', [{}])[0]
        games    = s.get('games', {})
        goals    = s.get('goals', {})
        passes   = s.get('passes', {})
        cards    = s.get('cards', {})
        shots    = s.get('shots', {})
        dribbles = s.get('dribbles', {})
 
        stats_data = {
            'appearances':  games.get('appearences', 0),
            'minutes':      games.get('minutes', 0),
            'rating':       games.get('rating'),
            'goals':        goals.get('total', 0),
            'assists':      goals.get('assists', 0),
            'shots_total':  shots.get('total', 0),
            'shots_on':     shots.get('on', 0),
            'passes_total': passes.get('total', 0),
            'passes_key':   passes.get('key', 0),
            'pass_accuracy':passes.get('accuracy', 0),
            'dribbles_success': dribbles.get('success', 0),
            'yellow_cards': cards.get('yellow', 0),
            'red_cards':    cards.get('red', 0),
            # Bio enrichment while we're here
            'nationality':  p_info.get('nationality', ''),
            'height':       p_info.get('height', ''),
            'weight':       p_info.get('weight', ''),
            'age':          p_info.get('age', 0),
        }
        cache.set(cache_key, stats_data, timeout=3600)
        return stats_data
 
    except Exception:
        return {}
 
 
# ─────────────────────────────────────────────────────────────────────────────
# ATHLETE BIO  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────
 
@api_view(['GET'])
@permission_classes([AllowAny])
def get_athlete_bio(request, athlete_id):
    athlete_entity = get_object_or_404(Entity, id=athlete_id, type='athlete')
    athlete_entity = athlete_entity.canonical_entity or athlete_entity
    try:
        athlete = athlete_entity.athlete_details
    except Athlete.DoesNotExist:
        return Response({'error': 'Athlete details not found'}, status=404)
 
    return Response({
        'id':                     athlete_entity.id,
        'name':                   f"{athlete.first_name} {athlete.last_name}",
        'photo':                  athlete_entity.logo_url,
        'date_of_birth':          athlete.date_of_birth,
        'age':                    athlete.age,
        'nationality':            athlete.nationality,
        'height_cm':              athlete.height_cm,
        'weight_kg':              athlete.weight_kg,
        'current_team':           EntitySerializer(athlete.current_team, context={'request': request}).data if athlete.current_team else None,
        'position':               athlete.position,
        'jersey_number':          athlete.jersey_number,
        'twitter':                athlete.twitter_handle,
        'instagram':              athlete.instagram_handle,
        'bio':                    athlete_entity.description,
    })
 
 
# ─────────────────────────────────────────────────────────────────────────────
# LEAGUE STANDINGS  — DB first, live API fallback
# ─────────────────────────────────────────────────────────────────────────────
 
@api_view(['GET'])
@permission_classes([AllowAny])
def get_league_standings(request, league_id):
    """
    GET /api/entities/league/{league_id}/standings/?season=2024
    """
    league_entity = get_object_or_404(Entity, id=league_id, type='league')
    league_entity = league_entity.canonical_entity or league_entity
    season = request.GET.get('season') or str(_current_season('soccer'))
    return _get_standings_for_league(request, league_entity, season)
 
 
def _get_standings_for_league(request, league_entity, season, highlight_team_id=None):
    """
    Shared logic used by both get_league_standings and get_team_standings.
    DB first → live API fallback → write back to DB.
    """
    # Resolve canonical league
    canonical = Entity.objects.filter(
        type='league',
        api_source=league_entity.api_source,
        external_id=league_entity.external_id,
    ).first() or league_entity
 
    # Try DB first
    teams_in_league = Team.objects.filter(
        league__api_source=canonical.api_source,
        league__external_id=canonical.external_id,
        entity__type='team',
    ).select_related('entity')
 
    standings = []
    has_db_data = False
 
    for team in teams_in_league:
        stats = EntityStats.objects.filter(
            entity=team.entity, season=str(season), stat_type='season'
        ).first()
        if stats and stats.stats_data.get('rank'):
            has_db_data = True
        standings.append({
            'rank':       stats.stats_data.get('rank', 0) if stats else 0,
            'team_id':    team.entity.id,
            'team_name':  team.entity.name,
            'logo':       team.entity.logo_url,
            'country':    team.entity.country,
            'points':     stats.stats_data.get('points', 0) if stats else 0,
            'played':     stats.stats_data.get('played', 0) if stats else 0,
            'wins':       stats.stats_data.get('wins') or stats.stats_data.get('win', team.total_wins) if stats else team.total_wins,
            'draws':      stats.stats_data.get('draws') or stats.stats_data.get('draw', 0) if stats else 0,
            'losses':     stats.stats_data.get('losses') or stats.stats_data.get('lose', team.total_losses) if stats else team.total_losses,
            'goals_for':  stats.stats_data.get('goals_for', 0) if stats else 0,
            'goals_against': stats.stats_data.get('goals_against', 0) if stats else 0,
            'goal_diff':  stats.stats_data.get('goal_diff', 0) if stats else 0,
            'form':       stats.stats_data.get('form', '') if stats else '',
            'is_highlighted': str(team.entity.external_id) == str(highlight_team_id),
        })
 
    if has_db_data:
        standings.sort(key=lambda x: (
            -x['points'],
            -x['goal_diff'],
            -x['goals_for'],
            x['team_name'].lower(),
        ))
        for i, item in enumerate(standings, 1):
            item['rank'] = i
        return Response({
            'league':   EntitySerializer(league_entity, context={'request': request}).data,
            'season':   season,
            'standings': standings,
            'source':   'db',
        })
 
    # Live API fallback — only for soccer/api_sports for now
    if canonical.api_source == 'api_sports':
        live_standings = _fetch_soccer_standings(canonical.external_id, int(season))
        if live_standings:
            # Write each team's standing back to DB
            for row in live_standings:
                team_entity = Entity.objects.filter(
                    api_source='api_sports', external_id=str(row['team_external_id'])
                ).first()
                if team_entity:
                    EntityStats.objects.update_or_create(
                        entity=team_entity,
                        season=str(season),
                        stat_type='season',
                        defaults={'stats_data': row},
                    )
                    # Also update win/loss on Team model
                    try:
                        t = team_entity.team_details
                        t.total_wins   = row.get('win', 0)
                        t.total_losses = row.get('lose', 0)
                        played = row.get('played', 0)
                        t.win_percentage = round(row.get('win', 0) / played * 100, 2) if played else 0
                        t.save(update_fields=['total_wins', 'total_losses', 'win_percentage'])
                    except Exception:
                        pass
 
            # Build response from live data
            live_response = []
            for row in live_standings:
                live_response.append({
                    'rank':      row.get('rank', 0),
                    'team_id':   None,  # client can look up by name if needed
                    'team_name': row.get('team_name', ''),
                    'logo':      row.get('team_logo', ''),
                    'points':    row.get('points', 0),
                    'played':    row.get('played', 0),
                    'wins':      row.get('win', 0),
                    'draws':     row.get('draw', 0),
                    'losses':    row.get('lose', 0),
                    'goals_for': row.get('goals_for', 0),
                    'goals_against': row.get('goals_against', 0),
                    'goal_diff': row.get('goal_diff', 0),
                    'form':      row.get('form', ''),
                    'is_highlighted': str(row.get('team_external_id')) == str(highlight_team_id),
                })
            live_response.sort(key=lambda x: (
                -x['points'],
                -x['goal_diff'],
                -x['goals_for'],
                x['team_name'].lower(),
            ))
            for i, item in enumerate(live_response, 1):
                item['rank'] = i
            return Response({
                'league':    EntitySerializer(league_entity, context={'request': request}).data,
                'season':    season,
                'standings': live_response,
                'source':    'live_api',
            })
 
    # Nothing available
    return Response({
        'league':    EntitySerializer(league_entity, context={'request': request}).data,
        'season':    season,
        'standings': standings,
        'source':    'empty',
    })
 
 
def _fetch_soccer_standings(external_id, season):
    cache_key = f'standings:soccer:{external_id}:{season}'
    cached = cache.get(cache_key)
    if cached:
        return cached
 
    try:
        resp = req.get(
            'https://v3.football.api-sports.io/standings',
            headers=HEADERS_SPORTS,
            params={'league': external_id, 'season': season},
            timeout=10,
        )
        if resp.status_code != 200:
            return []
 
        response = resp.json().get('response', [])
        if not response:
            return []
 
        standings_list = response[0].get('league', {}).get('standings', [[]])[0]
        result = []
        for s in standings_list:
            all_s = s.get('all', {})
            goals = all_s.get('goals', {})
            result.append({
                'rank':            s.get('rank', 0),
                'team_external_id': str(s.get('team', {}).get('id', '')),
                'team_name':       s.get('team', {}).get('name', ''),
                'team_logo':       s.get('team', {}).get('logo', ''),
                'points':          s.get('points', 0),
                'played':          all_s.get('played', 0),
                'win':             all_s.get('win', 0),
                'draw':            all_s.get('draw', 0),
                'lose':            all_s.get('lose', 0),
                'goals_for':       goals.get('for', 0),
                'goals_against':   goals.get('against', 0),
                'goal_diff':       s.get('goalsDiff', 0),
                'form':            s.get('form', ''),
            })
 
        cache.set(cache_key, result, timeout=3600)
        return result
 
    except Exception:
        return []
 
 
# ─────────────────────────────────────────────────────────────────────────────
# LEAGUE LEADERS  — DB first, live API fallback
# ─────────────────────────────────────────────────────────────────────────────
 
@api_view(['GET'])
@permission_classes([AllowAny])
def get_league_leaders(request, league_id):
    """
    GET /api/entities/league/{league_id}/leaders/?season=2024&stat=goals
    """
    league_entity = get_object_or_404(Entity, id=league_id, type='league')
    league_entity = league_entity.canonical_entity or league_entity
    season    = request.GET.get('season') or str(_current_season('soccer'))
    stat_type = request.GET.get('stat', 'goals')
 
    # DB path
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
        if stats and stat_type in stats.stats_data:
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
 
    # Live API fallback — top scorers / assists from API-Sports
    if canonical.api_source == 'api_sports':
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
 
 
def _fetch_soccer_leaders(external_id, season, stat_type):
    cache_key = f'leaders:soccer:{external_id}:{season}:{stat_type}'
    cached = cache.get(cache_key)
    if cached:
        return cached
 
    # Map our stat_type to the right API endpoint
    endpoint_map = {
        'goals':   'topscorers',
        'assists': 'topassists',
        'yellow_cards': 'topyellowcards',
        'red_cards':    'topredcards',
    }
    endpoint = endpoint_map.get(stat_type, 'topscorers')
 
    try:
        resp = req.get(
            f'https://v3.football.api-sports.io/players/{endpoint}',
            headers=HEADERS_SPORTS,
            params={'league': external_id, 'season': season},
            timeout=10,
        )
        if resp.status_code != 200:
            return []
 
        response = resp.json().get('response', [])
        result = []
        for item in response[:20]:
            p    = item.get('player', {})
            s    = item.get('statistics', [{}])[0]
            goals_data  = s.get('goals', {})
            cards_data  = s.get('cards', {})
            team_data   = s.get('team', {})
 
            stat_value = {
                'goals':        goals_data.get('total', 0),
                'assists':      goals_data.get('assists', 0),
                'yellow_cards': cards_data.get('yellow', 0),
                'red_cards':    cards_data.get('red', 0),
            }.get(stat_type, 0)
 
            result.append({
                'athlete_id':  None,
                'name':        p.get('name', ''),
                'photo':       p.get('photo', ''),
                'nationality': p.get('nationality', ''),
                'age':         p.get('age', 0),
                'team':        team_data.get('name', ''),
                'team_logo':   team_data.get('logo', ''),
                stat_type:     stat_value,
            })
 
        cache.set(cache_key, result, timeout=3600)
        return result
 
    except Exception:
        return []
 
 
# ─────────────────────────────────────────────────────────────────────────────
# LEAGUE FIXTURES  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────
 
@api_view(['GET'])
@permission_classes([AllowAny])
def get_league_fixtures(request, league_id):
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
 
    return Response({
        'league':   EntitySerializer(league_entity, context={'request': request}).data,
        'fixtures': EvSerializer(events, many=True).data,
    })
 
 
# ─────────────────────────────────────────────────────────────────────────────
# LIST ENTITIES  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────
 
@api_view(['GET'])
@permission_classes([AllowAny])
def list_entities(request):
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
 

@api_view(['GET'])
@permission_classes([AllowAny])
def get_team_fixtures(request, team_id):
    """
    GET /api/entities/team/{team_id}/fixtures/
    """
    from apps.event.models import Event
    from apps.event.serializers import EventSerializer as EvSerializer

    team_entity = get_object_or_404(Entity, id=team_id, type='team')
    team_entity = team_entity.canonical_entity or team_entity

    from django.db.models import Q
    events = Event.objects.filter(
        Q(home_entity=team_entity)
        | Q(away_entity=team_entity)
        | Q(home_entity__external_id=team_entity.external_id, home_entity__sport=team_entity.sport)
        | Q(away_entity__external_id=team_entity.external_id, away_entity__sport=team_entity.sport)
    ).distinct().select_related(
        'home_entity', 'away_entity', 'league'
    ).order_by('-start_time')[:50]

    return Response({
        'team': EntitySerializer(team_entity, context={'request': request}).data,
        'fixtures_count': events.count(),
        'fixtures': EvSerializer(events, many=True).data,
    })