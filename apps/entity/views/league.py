import logging
import requests
from django.shortcuts import get_object_or_404
from django.core.cache import cache
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.entity.models import Entity, Team, Athlete, EntityStats
from apps.entity.serializers import EntitySerializer
from .common import _current_season, _safe_league_data, HEADERS_SPORTS

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# LEAGUE STANDINGS
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


def _get_standings_for_league(request, league_entity, season, highlight_team_id=None, highlight_team_name=None):
    """
    Shared logic used by both get_league_standings and get_team_standings.
    DB first → live API fallback → write back to DB.
    """
    try:
        canonical = Entity.objects.filter(
            type='league',
            api_source=league_entity.api_source,
            external_id=league_entity.external_id,
        ).first() or league_entity
        teams_in_league = list(Team.objects.filter(
            league__api_source=canonical.api_source,
            league__external_id=canonical.external_id,
            entity__type='team',
        ).select_related('entity'))
    except Exception:
        canonical = league_entity
        teams_in_league = []

    standings = []
    valid_db_teams_count = 0
    for team in teams_in_league:
        stats = EntityStats.objects.filter(
            entity=team.entity, season=str(season), stat_type='season'
        ).first()
        if stats and stats.stats_data and stats.stats_data.get('rank'):
            p_val = stats.stats_data.get('played') or stats.stats_data.get('points') or 0
            if p_val > 0 and stats.updated_at and (timezone.now() - stats.updated_at).total_seconds() < 86400:
                valid_db_teams_count += 1
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
            'is_highlighted': str(team.entity.external_id) == str(highlight_team_id) or str(team.entity.id) == str(highlight_team_id),
        })

    # Only consider DB standings valid if AT LEAST 10 teams have real data, or if ALL teams in a small DB league have data
    total_teams_in_league = len(teams_in_league)
    if valid_db_teams_count >= 10 or (total_teams_in_league > 0 and valid_db_teams_count == total_teams_in_league):
        standings.sort(key=lambda x: (
            -x['points'],
            -x['goal_diff'],
            -x['goals_for'],
            x['team_name'].lower(),
        ))
        for i, item in enumerate(standings, 1):
            item['rank'] = i
        return Response({
            'league':    _safe_league_data(league_entity, request),
            'season':    season,
            'standings': standings,
            'source':    'db',
        })

    # Live API fallback — try TheSportsDB first, then StatPal/API-Sports
    live_standings = _fetch_league_standings_thesportsdb(canonical, season)

    if not live_standings and getattr(canonical, 'api_source', '') == 'statpal':
        try:
            season_year = int(str(season).split('-', 1)[0].split('/', 1)[0])
        except Exception:
            season_year = 2026
        live_standings = _fetch_soccer_standings(canonical.external_id, season_year)

    if live_standings:
        # Write each team's standing back to DB if matching Entity exists
        for row in live_standings:
            try:
                team_entity = Entity.objects.filter(
                    name__iexact=row.get('team_name')
                ).first() or (
                    Entity.objects.filter(api_source='api_sports', external_id=str(row.get('team_external_id'))).first()
                    if row.get('team_external_id') else None
                )
                if team_entity:
                    EntityStats.objects.update_or_create(
                        entity=team_entity,
                        season=str(season),
                        stat_type='season',
                        defaults={'stats_data': row},
                    )
            except Exception:
                pass

        live_response = []
        for row in live_standings:
            try:
                team_ent = Entity.objects.filter(name__iexact=row.get('team_name')).first()
            except Exception:
                team_ent = None
            live_response.append({
                'rank':      row.get('rank', 0),
                'team_id':   team_ent.id if team_ent else None,
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
                'is_highlighted': (
                    str(row.get('team_external_id')) == str(highlight_team_id) or
                    (team_ent and str(team_ent.id) == str(highlight_team_id)) or
                    (bool(highlight_team_name) and (
                        highlight_team_name.lower().replace(' fc', '').replace(' utd', ' united').strip() in row.get('team_name', '').lower().replace(' fc', '').replace(' utd', ' united').strip() or
                        row.get('team_name', '').lower().replace(' fc', '').replace(' utd', ' united').strip() in highlight_team_name.lower().replace(' fc', '').replace(' utd', ' united').strip()
                    ))
                ),
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
            'league':    _safe_league_data(league_entity, request),
            'season':    season,
            'standings': live_response,
            'source':    'live_api',
        })

    return Response({
        'league':    _safe_league_data(league_entity, request),
        'season':    season,
        'standings': standings,
        'source':    'empty',
    })


def _fetch_league_standings_thesportsdb(league_entity, season):
    """Fallback: Search league on TheSportsDB API and fetch lookup table standings."""
    try:
        from apps.sports_apis.services.thesportsdb import TheSportsDBService
        tsdb = TheSportsDBService()
        league_name = league_entity.name if hasattr(league_entity, 'name') else str(league_entity)
        league_id = None

        if getattr(league_entity, 'api_source', '') == 'thesportsdb':
            league_id = league_entity.external_id

        if not league_id:
            all_l_data = tsdb._get('all_leagues.php')
            leagues = (all_l_data.get('leagues') if isinstance(all_l_data, dict) else []) or []
            clean_lname = league_name.lower().replace(' league', '').replace(' division', '').strip()
            for l in leagues:
                str_lg = str(l.get('strLeague', '')).lower()
                if clean_lname in str_lg or str_lg in clean_lname or league_name.lower() in str_lg:
                    league_id = l.get('idLeague')
                    break

        if league_id:
            try:
                s_year = int(str(season).split('-', 1)[0].split('/', 1)[0])
            except Exception:
                s_year = 2026

            table = []
            for try_year in [s_year, s_year - 1, s_year - 2]:
                for s_fmt in [f"{try_year}-{try_year+1}", str(try_year), f"{try_year-1}-{try_year}"]:
                    candidate = tsdb.get_league_table(str(league_id), s_fmt)
                    if len(candidate) > len(table):
                        table = candidate
                    if len(table) >= 10:
                        break
                if len(table) >= 10:
                    break

            return table
    except Exception as e:
        logger.warning(f"Error fetching TSDB standings for {league_entity}: {e}")

    return []


def _fetch_soccer_standings(external_id, season):
    cache_key = f'standings:soccer:{external_id}:{season}'
    cached = cache.get(cache_key)
    if cached:
        return cached

    try:
        resp = requests.get(
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
# LEAGUE LEADERS
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


def _fetch_soccer_leaders(external_id, season, stat_type):
    cache_key = f'leaders:soccer:{external_id}:{season}:{stat_type}'
    cached = cache.get(cache_key)
    if cached:
        return cached

    endpoint_map = {
        'goals':   'topscorers',
        'assists': 'topassists',
        'yellow_cards': 'topyellowcards',
        'red_cards':    'topredcards',
    }
    endpoint = endpoint_map.get(stat_type, 'topscorers')

    try:
        resp = requests.get(
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
# LEAGUE FIXTURES
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
