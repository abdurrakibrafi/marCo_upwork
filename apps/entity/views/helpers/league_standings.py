import logging
import re
import requests
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response

from apps.entity.models import Entity, Team, EntityStats
from apps.entity.serializers import find_entity_logo, make_logo_url_absolute
from ..common import _safe_league_data, HEADERS_SPORTS

logger = logging.getLogger(__name__)


def _safe_int_val(val, default=0) -> int:
    """Safely convert value into an integer, handling floats, signs, and strings."""
    if val in (None, '', '-', 'None', 'null'):
        return default
    try:
        val_str = str(val).lstrip('+').strip()
        if '.' in val_str:
            return int(round(float(val_str)))
        return int(val_str)
    except Exception:
        return default


def _safe_float_val(val, default=0.0) -> float:
    """Safely convert value into a float, handling strings with + or - signs."""
    if val in (None, '', '-', 'None', 'null'):
        return default
    try:
        val_str = str(val).lstrip('+').strip()
        return float(val_str)
    except Exception:
        return default


def _get_sport_standings_columns(sport: str) -> list[dict]:
    """Return league-specific column headers and metric keys."""
    s = str(sport).lower().strip()
    if s in ('hockey', 'ice_hockey', 'nhl'):
        return [
            {'key': 'rank', 'label': '#'},
            {'key': 'team_name', 'label': 'Team'},
            {'key': 'played', 'label': 'GP'},
            {'key': 'wins', 'label': 'W'},
            {'key': 'losses', 'label': 'L'},
            {'key': 'ot_losses', 'label': 'OTL'},
            {'key': 'points', 'label': 'PTS'},
            {'key': 'goals_for', 'label': 'GF'},
            {'key': 'goals_against', 'label': 'GA'},
            {'key': 'goal_diff', 'label': 'DIFF'},
            {'key': 'form', 'label': 'STRK'},
        ]
    elif s in ('basketball', 'nba'):
        return [
            {'key': 'rank', 'label': '#'},
            {'key': 'team_name', 'label': 'Team'},
            {'key': 'wins', 'label': 'W'},
            {'key': 'losses', 'label': 'L'},
            {'key': 'win_pct', 'label': 'PCT'},
            {'key': 'gb', 'label': 'GB'},
            {'key': 'goal_diff', 'label': 'DIFF'},
            {'key': 'last_10', 'label': 'L10'},
            {'key': 'form', 'label': 'STRK'},
        ]
    elif s in ('baseball', 'mlb'):
        return [
            {'key': 'rank', 'label': '#'},
            {'key': 'team_name', 'label': 'Team'},
            {'key': 'wins', 'label': 'W'},
            {'key': 'losses', 'label': 'L'},
            {'key': 'win_pct', 'label': 'PCT'},
            {'key': 'gb', 'label': 'GB'},
            {'key': 'goals_for', 'label': 'RS'},
            {'key': 'goals_against', 'label': 'RA'},
            {'key': 'goal_diff', 'label': 'DIFF'},
            {'key': 'form', 'label': 'STRK'},
        ]
    elif s in ('american_football', 'football', 'nfl'):
        return [
            {'key': 'rank', 'label': '#'},
            {'key': 'team_name', 'label': 'Team'},
            {'key': 'wins', 'label': 'W'},
            {'key': 'losses', 'label': 'L'},
            {'key': 'draws', 'label': 'T'},
            {'key': 'win_pct', 'label': 'PCT'},
            {'key': 'goals_for', 'label': 'PF'},
            {'key': 'goals_against', 'label': 'PA'},
            {'key': 'goal_diff', 'label': 'DIFF'},
            {'key': 'form', 'label': 'STRK'},
        ]
    else:
        # Default / Soccer
        return [
            {'key': 'rank', 'label': '#'},
            {'key': 'team_name', 'label': 'Club'},
            {'key': 'played', 'label': 'MP'},
            {'key': 'wins', 'label': 'W'},
            {'key': 'draws', 'label': 'D'},
            {'key': 'losses', 'label': 'L'},
            {'key': 'goals_for', 'label': 'GF'},
            {'key': 'goals_against', 'label': 'GA'},
            {'key': 'goal_diff', 'label': 'GD'},
            {'key': 'points', 'label': 'Pts'},
            {'key': 'form', 'label': 'Form'},
        ]


def _handle_cricket_league_standings(request, league_entity, season, highlight_team_name=None):
    """Handle standings for cricket leagues by dynamically pulling ICC rankings."""
    from .team_rankings import fetch_live_icc_rankings, _normalize_cricket_team_key, _detect_cricket_active_format
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
        return _handle_cricket_league_standings(request, league_entity, season, highlight_team_name)

    canonical = Entity.objects.filter(
        type='league',
        api_source=league_entity.api_source,
        external_id=league_entity.external_id,
    ).first() or league_entity

    sport_clean = str(getattr(canonical, 'sport', '') or getattr(league_entity, 'sport', '') or '').lower().strip()
    if not sport_clean or sport_clean in ('unknown', 'other'):
        lname = (getattr(league_entity, 'name', '') or '').lower()
        if 'nba' in lname:
            sport_clean = 'basketball'
        elif 'nhl' in lname:
            sport_clean = 'hockey'
        elif 'mlb' in lname:
            sport_clean = 'baseball'
        elif 'nfl' in lname:
            sport_clean = 'american_football'

    if sport_clean == 'cricket':
        return _handle_cricket_league_standings(request, league_entity, season, highlight_team_name)

    live_standings = []

    # 1. Primary fast providers (StatPal official hierarchical standings)
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

    # 2. Database standings fallback (if live provider returned no data)
    standings = []
    if not live_standings:
        try:
            teams_in_league = list(Team.objects.filter(
                league__api_source=canonical.api_source,
                league__external_id=canonical.external_id,
                entity__type='team',
            ).select_related('entity'))
        except Exception:
            teams_in_league = []

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
            logo_url = make_logo_url_absolute(team.entity.logo_url, request) if (team.entity and team.entity.logo_url) else ''
            standings.append({
                'rank':       stats.stats_data.get('rank', 0) if stats else 0,
                'team_id':    team.entity.id,
                'team_name':  team.entity.name,
                'logo':       logo_url,
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
                'league':      _safe_league_data(league_entity, request),
                'season':      season,
                'structure':   'table',
                'columns':     _get_sport_standings_columns(sport_clean),
                'conferences': {},
                'divisions':   {},
                'standings':   standings,
                'source':      'db',
            })

    # 3. Fallback to TheSportsDB only if primary provider and DB returned no data
    if not live_standings:
        live_standings = _fetch_league_standings_thesportsdb(canonical, season)

    if live_standings:
        if isinstance(live_standings, dict):
            live_standings = (
                live_standings.get('table')
                or live_standings.get('standings')
                or live_standings.get('teams')
                or live_standings.get('team')
                or [v for v in live_standings.values() if isinstance(v, dict)]
            )
        if not isinstance(live_standings, list):
            live_standings = []

        # Collect team names and external IDs for batch querying
        team_names = [str(r.get('team_name', '')).strip() for r in live_standings if isinstance(r, dict) and r.get('team_name')]
        ext_ids = [str(r.get('team_external_id', '')).strip() for r in live_standings if isinstance(r, dict) and r.get('team_external_id')]

        entities_map = {}
        if team_names or ext_ids:
            try:
                found_ents = list(Entity.objects.filter(
                    Q(name__in=team_names) | Q(external_id__in=ext_ids),
                    type='team',
                ).select_related('canonical_entity'))
                for e in found_ents:
                    entities_map[e.name.lower()] = e
                    if e.external_id:
                        entities_map[f"ext:{e.external_id}"] = e
            except Exception:
                pass

        live_response = []
        for row in live_standings:
            if not isinstance(row, dict):
                continue

            t_name = str(row.get('team_name', '') or '').strip()
            ext_id = str(row.get('team_external_id', '') or '').strip()

            team_ent = None
            if ext_id and f"ext:{ext_id}" in entities_map:
                team_ent = entities_map[f"ext:{ext_id}"]
            elif t_name.lower() in entities_map:
                team_ent = entities_map[t_name.lower()]
            else:
                try:
                    team_ent = Entity.objects.filter(name__iexact=t_name, type='team').select_related('canonical_entity').first()
                    if not team_ent and ext_id:
                        team_ent = Entity.objects.filter(external_id=ext_id, type='team').select_related('canonical_entity').first()
                    if not team_ent and t_name:
                        stripped_name = re.sub(r'\b(fc|cf|sc|bc|utd|united)\b', '', t_name, flags=re.I).strip()
                        if stripped_name and len(stripped_name) > 3:
                            team_ent = Entity.objects.filter(name__icontains=stripped_name, type='team').select_related('canonical_entity').first()
                except Exception:
                    team_ent = None

            canonical_ent = getattr(team_ent, 'canonical_entity', None) or team_ent
            resolved_team_id = canonical_ent.id if canonical_ent else (team_ent.id if team_ent else None)

            # High-resolution logo resolution
            raw_logo = str(row.get('team_logo') or '')
            logo = ''
            if canonical_ent:
                logo = find_entity_logo(canonical_ent)
            if not logo and team_ent:
                logo = find_entity_logo(team_ent)
            if not logo:
                logo = raw_logo
            if not logo:
                from apps.entity.utils.matcher import find_team_logo_by_name
                logo = find_team_logo_by_name(t_name)
            if logo:
                logo = make_logo_url_absolute(logo, request)

            # Safely sync to EntityStats in background
            if canonical_ent or team_ent:
                try:
                    EntityStats.objects.update_or_create(
                        entity=canonical_ent or team_ent,
                        season=str(season),
                        stat_type='season',
                        defaults={'stats_data': row},
                    )
                except Exception:
                    pass

            wins = _safe_int_val(row.get('wins') if row.get('wins') is not None else row.get('win', 0))
            draws = _safe_int_val(row.get('draws') if row.get('draws') is not None else row.get('draw', 0))
            losses = _safe_int_val(row.get('losses') if row.get('losses') is not None else row.get('lose', 0))
            ot_losses = _safe_int_val(row.get('ot_losses') or row.get('ot', 0))
            played = _safe_int_val(row.get('played') or (wins + losses + draws + ot_losses))
            points = _safe_int_val(row.get('points', 0))
            goals_for = _safe_int_val(row.get('goals_for', 0))
            goals_against = _safe_int_val(row.get('goals_against', 0))
            goal_diff = _safe_int_val(row.get('goal_diff', 0))
            win_pct = _safe_float_val(row.get('win_pct', 0.0))
            if not win_pct and played > 0 and wins > 0:
                win_pct = round(wins / played, 3)

            is_hl = False
            if highlight_team_id:
                if str(highlight_team_id) in (ext_id, str(resolved_team_id or ''), str(getattr(team_ent, 'external_id', ''))):
                    is_hl = True
            if not is_hl and highlight_team_name and t_name:
                h_clean = highlight_team_name.lower().replace(' fc', '').replace(' utd', ' united').strip()
                t_clean = t_name.lower().replace(' fc', '').replace(' utd', ' united').strip()
                if h_clean in t_clean or t_clean in h_clean:
                    is_hl = True

            live_response.append({
                'rank':           _safe_int_val(row.get('rank', 0)),
                'team_id':        resolved_team_id,
                'team_name':      t_name,
                'logo':           logo,
                'team_logo':      logo,
                'logo_url':       logo,
                'conference':     str(row.get('conference', '') or '').strip(),
                'division':       str(row.get('division', '') or '').strip(),
                'played':         played,
                'wins':           wins,
                'draws':          draws,
                'losses':         losses,
                'ot_losses':      ot_losses,
                'points':         points,
                'goals_for':      goals_for,
                'goals_against':  goals_against,
                'goal_diff':      goal_diff,
                'win_pct':        win_pct,
                'gb':             str(row.get('gb', '-') or '-').strip(),
                'form':           str(row.get('form', '') or '').strip(),
                'last_10':        str(row.get('last_10', '') or '').strip(),
                'home_record':    str(row.get('home_record', '') or '').strip(),
                'away_record':    str(row.get('away_record', '') or '').strip(),
                'is_highlighted': is_hl,
            })

        if live_response:
            has_conference = any(bool(r.get('conference')) for r in live_response)
            has_division = any(bool(r.get('division')) for r in live_response)

            conferences = {}
            divisions = {}

            if has_conference or has_division:
                for r in live_response:
                    c_name = r.get('conference') or 'League'
                    d_name = r.get('division') or 'General'

                    if c_name not in conferences:
                        conferences[c_name] = {
                            'name': c_name,
                            'divisions': {},
                            'standings': [],
                        }
                    conferences[c_name]['standings'].append(r)

                    if d_name not in conferences[c_name]['divisions']:
                        conferences[c_name]['divisions'][d_name] = []
                    conferences[c_name]['divisions'][d_name].append(r)

                    if d_name not in divisions:
                        divisions[d_name] = []
                    divisions[d_name].append(r)

                for c_name, c_data in conferences.items():
                    if sport_clean in ('hockey', 'ice_hockey', 'nhl'):
                        c_data['standings'].sort(key=lambda x: (-x['points'], -x['wins'], -x['goal_diff']))
                    elif sport_clean in ('basketball', 'nba', 'baseball', 'mlb', 'american_football', 'football', 'nfl'):
                        c_data['standings'].sort(key=lambda x: (-x['win_pct'], -x['wins'], -x['goal_diff']))
                    for idx, team in enumerate(c_data['standings'], 1):
                        team['conference_rank'] = idx

                    for d_name, d_teams in c_data['divisions'].items():
                        if sport_clean in ('hockey', 'ice_hockey', 'nhl'):
                            d_teams.sort(key=lambda x: (-x['points'], -x['wins'], -x['goal_diff']))
                        elif sport_clean in ('basketball', 'nba', 'baseball', 'mlb', 'american_football', 'football', 'nfl'):
                            d_teams.sort(key=lambda x: (x.get('rank') or 99, -x['win_pct'], -x['wins']))
                        for idx, team in enumerate(d_teams, 1):
                            team['division_rank'] = idx

                if has_conference and has_division:
                    structure = 'conference_division'
                elif has_division:
                    structure = 'divisions'
                else:
                    structure = 'table'
            else:
                structure = 'table'
                live_response.sort(key=lambda x: (
                    -x.get('points', 0),
                    -x.get('goal_diff', 0),
                    -x.get('goals_for', 0),
                    x.get('team_name', '').lower(),
                ))
                for idx, item in enumerate(live_response, 1):
                    item['rank'] = idx

            return Response({
                'league':      _safe_league_data(league_entity, request),
                'season':      season,
                'structure':   structure,
                'columns':     _get_sport_standings_columns(sport_clean),
                'conferences': conferences,
                'divisions':   divisions,
                'standings':   live_response,
                'source':      'live_api',
            })

    return Response({
        'league':      _safe_league_data(league_entity, request),
        'season':      season,
        'structure':   'table',
        'columns':     _get_sport_standings_columns(sport_clean),
        'conferences': {},
        'divisions':   {},
        'standings':   standings,
        'source':      'empty',
    })


def _fetch_league_standings_thesportsdb(league_entity, season) -> list:
    """Fetch league table standings using TheSportsDB league lookup and table endpoints.

    Args:
        league_entity (Entity): League entity object.
        season (int or str): Target season year or range (e.g. 2024 or '2024-2025').

    Returns:
        list: Standings rows with team records.
    """
    cache_key = f'standings:thesportsdb:{getattr(league_entity, "external_id", "") or getattr(league_entity, "id", "")}:{season}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

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
            for s_fmt in [f"{s_year}-{s_year+1}", str(s_year), f"{s_year-1}-{s_year}"]:
                candidate = tsdb.get_league_table(str(league_id), s_fmt)
                if len(candidate) > len(table):
                    table = candidate
                if len(table) >= 10:
                    break

            cache.set(cache_key, table, timeout=3600)
            return table
    except Exception as e:
        logger.warning(f"Error fetching TSDB standings for {league_entity}: {e}")

    cache.set(cache_key, [], timeout=600)
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
                    wins = _safe_int_val(t.get('won') or t.get('wins') or t.get('win') or 0)
                    losses = _safe_int_val(t.get('lost') or t.get('losses') or t.get('lose') or 0)
                    ot_losses = _safe_int_val(t.get('ot_losses') or t.get('ot') or 0)
                    draws = _safe_int_val(t.get('draw') or t.get('draws') or t.get('ties') or 0)
                    played = _safe_int_val(t.get('games_played') or t.get('played') or (wins + losses + ot_losses + draws))
                    points = _safe_int_val(t.get('points') or 0)
                    goals_for = _safe_int_val(t.get('goals_for') or t.get('runs_scored') or t.get('runs_for') or t.get('points_for') or 0)
                    goals_against = _safe_int_val(t.get('goals_against') or t.get('runs_allowed') or t.get('runs_against') or t.get('points_against') or 0)
                    goal_diff = _safe_int_val(t.get('runs_diff') or t.get('difference') or t.get('net_points') or (goals_for - goals_against))
                    streak = str(t.get('current_streak') or t.get('streak') or '')
                    pct = _safe_float_val(t.get('pct') or t.get('percentage') or t.get('win_percentage') or (round(wins / played, 3) if played else 0.0))
                    gb = str(t.get('gb') or t.get('games_back') or '-')
                    last_10 = str(t.get('last_10') or t.get('last_ten') or '')
                    home_rec = str(t.get('home_record') or '')
                    away_rec = str(t.get('road_record') or t.get('away_record') or '')

                    rows.append({
                        'rank': _safe_int_val(t.get('position') or t.get('rank') or 0),
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
                        'gb': gb,
                        'last_10': last_10,
                        'home_record': home_rec,
                        'away_record': away_rec,
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
    """Fetch soccer league standings table from StatPal or fallback to API-Sports.

    Args:
        external_id (str or int): League external ID.
        season (int or str): Season year.

    Returns:
        list: Standings rows with team records and points.
    """
    cache_key = f'standings:soccer:{external_id}:{season}'
    cached = cache.get(cache_key)
    if cached:
        return cached

    # 1. Try StatPal first
    try:
        from apps.sports_apis.services.statpal import statpal_service
        sp_res = statpal_service.get_soccer_standings(external_id)
        if sp_res.get('success'):
            data = sp_res.get('data', {})
            standings_list = data.get('standings', {}).get('tournament', {}).get('team', [])
            if isinstance(standings_list, dict):
                standings_list = [standings_list]

            if standings_list:
                result = []
                for s in standings_list:
                    overall = s.get('overall', {})
                    total = s.get('total', {})
                    wins = int(overall.get('wins') or 0)
                    losses = int(overall.get('losses') or 0)
                    draws = int(overall.get('draws') or 0)
                    played = int(overall.get('games_played') or 0)
                    points = int(total.get('points') or 0)
                    gf = int(overall.get('goals_scored') or 0)
                    ga = int(overall.get('goals_allowed') or 0)
                    gd = int(total.get('goal_difference') or (gf - ga))

                    result.append({
                        'rank':             int(s.get('position') or 0),
                        'team_external_id': str(s.get('id', '')),
                        'team_name':        s.get('name', ''),
                        'team_logo':        '',
                        'points':           points,
                        'played':           played,
                        'win':              wins,
                        'draw':             draws,
                        'lose':             losses,
                        'goals_for':        gf,
                        'goals_against':    ga,
                        'goal_diff':        gd,
                        'form':             s.get('recent_form', ''),
                    })
                if result:
                    cache.set(cache_key, result, timeout=1800)  # 30 min cache
                    return result
    except Exception as exc:
        logger.warning(f"StatPal soccer standings error for league {external_id}: {exc}")

    # 2. Fallback to API-Sports
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

        cache.set(cache_key, result, timeout=1800)
        return result

    except Exception:
        return []



