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


def _get_standings_for_league(request, league_entity, season, highlight_team_id=None, highlight_team_name=None):
    """Compute and format league standings table with DB cache and multi-API fallback.

    Args:
        request (Request): HTTP request context.
        league_entity (Entity): League entity instance.
        season (str or int): Season identifier.
        highlight_team_id (str or int, optional): Target team ID to mark with 'is_highlighted'.
        highlight_team_name (str, optional): Target team name to match.

    Returns:
        Response: Serialized standings payload with league data.
    """
    sport_clean = str(getattr(league_entity, 'sport', '') or '').lower()
    if sport_clean == 'cricket':
        from .helpers.team_rankings import fetch_live_icc_rankings, _normalize_cricket_team_key, _detect_cricket_active_format
        league_name_lower = league_entity.name.lower()
        is_women_league = bool(
            'women' in league_name_lower or
            league_name_lower.endswith(' w') or
            ' w ' in league_name_lower or
            '(w)' in league_name_lower
        )

        icc_res = fetch_live_icc_rankings()
        by_format = icc_res.get('by_format', {}) if isinstance(icc_res, dict) else {}

        icc_tables = {}
        for fmt, rows in by_format.items():
            fmt_is_women = fmt in ('wodi', 'wt20i', 'wtest')
            fmt_rows = []
            for row in rows:
                t_name = row.get('team_name', '')
                t_key = _normalize_cricket_team_key(t_name)
                is_hl = False
                if highlight_team_name and (is_women_league == fmt_is_women):
                    clean_hl = _normalize_cricket_team_key(highlight_team_name)
                    base_hl = clean_hl.replace('women', '').strip()
                    row_base = t_key.replace('women', '').strip()
                    is_hl = (t_key == clean_hl) or (bool(base_hl) and base_hl == row_base)
                row_copy = dict(row)
                row_copy['is_highlighted'] = is_hl
                row_copy.setdefault('played', row.get('matches', 0))
                fmt_rows.append(row_copy)
            icc_tables[fmt] = fmt_rows

        raw_fmt = str(request.GET.get('format', '')).lower().strip()
        context_match = None
        if raw_fmt:
            if is_women_league:
                if raw_fmt in ('t20', 't20i', 't20s', 'wt20', 'wt20i', 'women_t20', 'women-t20'):
                    active_fmt = 'wt20i'
                else:
                    active_fmt = 'wodi'
            else:
                if raw_fmt in ('test', 'tests'):
                    active_fmt = 'test'
                elif raw_fmt in ('t20', 't20i', 't20s'):
                    active_fmt = 't20i'
                elif raw_fmt in ('wodi', 'women_odi', 'women-odi'):
                    active_fmt = 'wodi'
                elif raw_fmt in ('wt20', 'wt20i', 'women_t20', 'women-t20'):
                    active_fmt = 'wt20i'
                else:
                    active_fmt = 'odi'
        else:
            active_fmt, context_match = _detect_cricket_active_format(league_entity, is_women=is_women_league)

        target_default = 'wodi' if is_women_league else 'odi'
        if active_fmt not in icc_tables or not icc_tables[active_fmt]:
            if target_default in icc_tables and icc_tables[target_default]:
                active_fmt = target_default
            elif icc_tables:
                active_fmt = list(icc_tables.keys())[0]

        standings = icc_tables.get(active_fmt, [])

        if is_women_league:
            tabs = [
                {'key': 'wodi', 'label': 'ODI', 'is_active': (active_fmt == 'wodi')},
                {'key': 'wt20i', 'label': 'T20I', 'is_active': (active_fmt == 'wt20i')},
            ]
        else:
            tabs = [
                {'key': 'odi', 'label': 'ODI', 'is_active': (active_fmt == 'odi')},
                {'key': 't20i', 'label': 'T20I', 'is_active': (active_fmt == 't20i')},
                {'key': 'test', 'label': 'Test', 'is_active': (active_fmt == 'test')},
            ]

        return Response({
            'league': _safe_league_data(league_entity, request),
            'season': season,
            'format': active_fmt,
            'context_match': context_match,
            'available_formats': [t['key'] for t in tabs],
            'tabs': tabs,
            'standings': standings,
            'icc_rankings': icc_tables,
            'source': 'icc_rankings',
            'message': 'ICC Rankings provided for Cricket national match.',
        })

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
    if teams_in_league:
        team_entities = [team.entity for team in teams_in_league if team.entity]
        stats_qs = EntityStats.objects.filter(
            entity__in=team_entities, season=str(season), stat_type='season'
        )
        stats_map = {s.entity_id: s for s in stats_qs}
    else:
        stats_map = {}

    for team in teams_in_league:
        stats = stats_map.get(team.entity_id) if team.entity else None
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

    sport_clean = str(getattr(canonical, 'sport', '') or getattr(league_entity, 'sport', '') or '').lower()

    if not live_standings:
        if sport_clean == 'baseball':
            live_standings = _fetch_statpal_hierarchical_standings('baseball', f'standings:baseball:mlb:{season}')
        elif sport_clean in ('basketball', 'nba'):
            live_standings = _fetch_statpal_hierarchical_standings('basketball', f'standings:nba:{season}')
        elif sport_clean in ('hockey', 'ice_hockey', 'nhl'):
            live_standings = _fetch_statpal_hierarchical_standings('hockey', f'standings:nhl:{season}')
        elif sport_clean in ('american_football', 'football', 'nfl'):
            live_standings = _fetch_statpal_hierarchical_standings('american_football', f'standings:nfl:{season}')
        elif getattr(canonical, 'api_source', '') == 'statpal' or sport_clean == 'soccer':
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
                'logo':      (team_ent.logo_url if team_ent and team_ent.logo_url else '') or row.get('team_logo', ''),
                'points':    row.get('points', 0),
                'played':    row.get('played', 0),
                'wins':      row.get('wins') if row.get('wins') is not None else row.get('win', 0),
                'draws':     row.get('draws') if row.get('draws') is not None else row.get('draw', 0),
                'losses':    row.get('losses') if row.get('losses') is not None else row.get('lose', 0),
                'goals_for': row.get('goals_for', 0),
                'goals_against': row.get('goals_against', 0),
                'goal_diff': row.get('goal_diff', 0),
                'form':      row.get('form', ''),
                'conference': row.get('conference', ''),
                'division':  row.get('division', ''),
                'win_pct':   row.get('win_pct', 0),
                'is_highlighted': (
                    str(row.get('team_external_id')) == str(highlight_team_id) or
                    (team_ent and str(team_ent.id) == str(highlight_team_id)) or
                    (bool(highlight_team_name) and (
                        highlight_team_name.lower().replace(' fc', '').replace(' utd', ' united').strip() in row.get('team_name', '').lower().replace(' fc', '').replace(' utd', ' united').strip() or
                        row.get('team_name', '').lower().replace(' fc', '').replace(' utd', ' united').strip() in highlight_team_name.lower().replace(' fc', '').replace(' utd', ' united').strip()
                    ))
                ),
            })
        if sport_clean in ('baseball', 'basketball', 'nba', 'american_football', 'football', 'nfl'):
            live_response.sort(key=lambda x: (x.get('rank') or 999, -x.get('wins', 0)))
        else:
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


def _fetch_league_standings_thesportsdb(league_entity, season) -> list:
    """Fetch league table standings using TheSportsDB league lookup and table endpoints.

    Args:
        league_entity (Entity): League entity object.
        season (int or str): Target season year or range (e.g. 2024 or '2024-2025').

    Returns:
        list: Standings rows with team records.
    """
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


def _fetch_statpal_hierarchical_standings(sport: str, cache_key: str) -> list:
    """Fetch structured conference/division standings from StatPal API (NBA, NFL, MLB, NHL).

    Args:
        sport (str): Sport slug ('nba', 'mlb', 'nfl', 'nhl', etc.).
        cache_key (str): Redis cache key string.

    Returns:
        list: Formatted standings row dictionaries.
    """
    try:
        cached = cache.get(cache_key)
        if cached:
            return cached
    except Exception:
        pass

    try:
        from apps.sports_apis.services.statpal import statpal_service
        sport_lower = str(sport).lower()
        if sport_lower == 'baseball':
            result = statpal_service.get_mlb_standings()
        elif sport_lower in ('basketball', 'nba'):
            result = statpal_service.get_nba_standings()
        elif sport_lower in ('american_football', 'football', 'nfl'):
            result = statpal_service.get_nfl_standings()
        elif sport_lower in ('hockey', 'ice_hockey', 'nhl'):
            result = statpal_service.get_nhl_standings()
        else:
            return []

        if not result or not result.get('success'):
            return []

        standings_data = result.get('data', {}).get('standings', {})
        container = standings_data.get('category') or standings_data.get('tournament') or {}
        leagues = container.get('league', [])
        if isinstance(leagues, dict):
            leagues = [leagues]

        rows = []
        for lg in leagues:
            lg_name = lg.get('name', '')
            divs = lg.get('division', [])
            if isinstance(divs, dict):
                divs = [divs]
            for div in divs:
                div_name = div.get('name', '')
                teams = div.get('team', [])
                if isinstance(teams, dict):
                    teams = [teams]
                for t in teams:
                    t_name = str(t.get('name', '')).strip()
                    if not t_name:
                        continue
                    wins = int(t.get('won') or t.get('wins') or t.get('win') or 0)
                    losses = int(t.get('lost') or t.get('losses') or t.get('lose') or 0)
                    ot_losses = int(t.get('ot_losses') or t.get('ot') or 0)
                    draws = int(t.get('draw') or t.get('draws') or t.get('ties') or 0)
                    played = int(t.get('games_played') or t.get('played') or (wins + losses + ot_losses + draws))
                    points = int(t.get('points') or 0)
                    goals_for = int(t.get('goals_for') or t.get('runs_scored') or t.get('runs_for') or t.get('points_for') or 0)
                    goals_against = int(t.get('goals_against') or t.get('runs_allowed') or t.get('runs_against') or t.get('points_against') or 0)
                    goal_diff = int(t.get('runs_diff') or t.get('difference') or t.get('net_points') or (goals_for - goals_against))
                    streak = str(t.get('current_streak') or t.get('streak') or '')
                    pct = float(t.get('pct') or t.get('win_percentage') or (round(wins / played, 3) if played else 0.0))

                    rows.append({
                        'rank': int(t.get('position') or t.get('rank') or 0),
                        'team_external_id': str(t.get('id', '')),
                        'team_name': t_name,
                        'team_logo': t.get('logo', ''),
                        'points': points,
                        'played': played,
                        'win': wins,
                        'wins': wins,
                        'draw': draws,
                        'draws': draws,
                        'lose': losses,
                        'losses': losses,
                        'ot_losses': ot_losses,
                        'goals_for': goals_for,
                        'goals_against': goals_against,
                        'goal_diff': goal_diff,
                        'form': streak,
                        'conference': lg_name,
                        'division': div_name,
                        'win_pct': pct,
                    })

        if rows:
            try:
                cache.set(cache_key, rows, timeout=3600)
            except Exception:
                pass
        return rows
    except Exception as e:
        logger.warning(f"Error fetching StatPal standings for {sport}: {e}")
        return []


def _fetch_soccer_standings(external_id, season) -> list:
    """Fetch soccer league standings table from API-Football.

    Args:
        external_id (str or int): League external ID in API-Football.
        season (int or str): Season year.

    Returns:
        list: Standings rows with team records and points.
    """
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


def _fetch_soccer_leaders(external_id, season, stat_type) -> list:
    """Fetch top statistical players (scorers/assists/cards) for a league from API-Football.

    Args:
        external_id (str or int): API-Football league ID.
        season (int or str): Season year.
        stat_type (str): Target metric ('goals', 'assists', 'yellow_cards', 'red_cards').

    Returns:
        list: Leader records formatted with athlete info.
    """
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

    return Response({
        'league':   EntitySerializer(league_entity, context={'request': request}).data,
        'fixtures': EvSerializer(events, many=True).data,
    })
