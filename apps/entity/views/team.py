import logging
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db.models import Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.entity.models import Entity, Athlete, EntityStats
from apps.entity.serializers import EntitySerializer
from .common import _current_season

# Import helpers from modular helper modules
from .helpers.team_rankings import (
    CRICKET_TEAM_ALIAS_MAP,
    _normalize_cricket_team_key,
    fetch_live_icc_rankings,
    fetch_live_fifa_rankings,
    _get_tennis_rankings_helper,
    _get_golf_leaderboard_helper,
)
from .helpers.team_stats import (
    _fetch_soccer_team_stats_thesportsdb,
    _fetch_stats_from_db_events,
    _normalize_team_stats,
    _fetch_cricket_team_stats,
    _fetch_nfl_team_stats,
    _fetch_nhl_team_stats,
    _fetch_mlb_team_stats,
    _fetch_soccer_team_stats,
    _fetch_nba_team_stats,
    _fetch_soccer_team_stats_statpal,
    _fetch_nba_team_stats_statpal,
    _filter_by_team_division,
    _fetch_team_fixtures_live,
)

logger = logging.getLogger(__name__)

# Re-export for backward compatibility
__all__ = [
    'get_team_stats',
    'get_team_roster',
    'get_team_standings',
    'get_team_fixtures',
    'fetch_live_icc_rankings',
    'fetch_live_fifa_rankings',
    '_fetch_soccer_team_stats_thesportsdb',
    '_fetch_stats_from_db_events',
    '_normalize_cricket_team_key',
    'CRICKET_TEAM_ALIAS_MAP',
    '_normalize_team_stats',
    '_get_tennis_rankings_helper',
    '_get_golf_leaderboard_helper',
    '_fetch_cricket_team_stats',
    '_fetch_nfl_team_stats',
    '_fetch_nhl_team_stats',
    '_fetch_mlb_team_stats',
    '_fetch_soccer_team_stats',
    '_fetch_nba_team_stats',
    '_fetch_soccer_team_stats_statpal',
    '_fetch_nba_team_stats_statpal',
    '_filter_by_team_division',
    '_fetch_team_fixtures_live',
]


# ─────────────────────────────────────────────────────────────────────────────
# 1. GET TEAM STATS VIEW
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([AllowAny])
def get_team_stats(request, team_id):
    """
    GET /api/entities/team/{team_id}/stats/?season=2024
    """
    from .athlete import get_athlete_stats, _fetch_thesportsdb_player_stats
    from .league import _get_standings_for_league

    team_entity = get_object_or_404(Entity, id=team_id, type='team')
    team_entity = team_entity.canonical_entity or team_entity

    if team_entity.type == 'athlete':
        raw_req = getattr(request, '_request', request)
        res = get_athlete_stats(raw_req, team_entity.id)
        if res.status_code == 200 and isinstance(res.data, dict):
            data = dict(res.data)
            if 'athlete' in data:
                data['team'] = data.pop('athlete')
            return Response(data)
        return res

    season = request.GET.get('season') or str(_current_season(team_entity.sport))
    stats_season = (
        f"{season}-{str(int(season) + 1)[-2:]}"
        if team_entity.sport == 'basketball' and '-' not in season
        else season
    )
    api_season = int(str(season).split('-', 1)[0])

    # 1 — try DB first
    stats = EntityStats.objects.filter(entity=team_entity, season=stats_season).first()
    has_valid_db_stats = (
        stats and stats.stats_data and 
        (stats.stats_data.get('played', 0) >= 15 or stats.stats_data.get('matches_played', 0) >= 15) and
        stats.updated_at and (timezone.now() - stats.updated_at).total_seconds() < 86400
    )

    if has_valid_db_stats:
        normalized_stats = _normalize_team_stats(stats.stats_data, team_entity=team_entity)
        return Response({
            'team': EntitySerializer(team_entity, context={'request': request}).data,
            'season': stats_season,
            'stats': normalized_stats,
            'source': 'db',
        })

    # 2 — live API fallback
    stats_data = {}

    if team_entity.sport == 'soccer':
        try:
            league = None
            try:
                if team_entity.team_details.league:
                    league = team_entity.team_details.league
            except Exception:
                pass

            if not league:
                from apps.sports_apis.services.thesportsdb import TheSportsDBService
                tsdb_info = TheSportsDBService().search_team(team_entity.name)
                if tsdb_info and tsdb_info.get('idLeague'):
                    league = Entity(
                        name=tsdb_info.get('strLeague', ''),
                        external_id=str(tsdb_info.get('idLeague')),
                        api_source='thesportsdb',
                        type='league',
                        sport=team_entity.sport or 'soccer'
                    )

            if league:
                st_res = _get_standings_for_league(request, league, season, highlight_team_id=team_entity.external_id, highlight_team_name=team_entity.name)
                if st_res.data and st_res.data.get('standings'):
                    clean_team = team_entity.name.lower().replace(' fc', '').replace(' utd', ' united').strip()
                    for row in st_res.data['standings']:
                        t_name = str(row.get('team_name', '')).lower().replace(' fc', '').replace(' utd', ' united').strip()
                        is_match = (clean_team in t_name) or (t_name in clean_team) or (str(row.get('team_id')) == str(team_entity.id)) or (str(row.get('team_external_id')) == str(team_entity.external_id))
                        if is_match:
                            p_num = row.get('played', 0)
                            w_num = row.get('wins', row.get('win', 0))
                            stats_data = {
                                'rank': row.get('rank', 0),
                                'points': row.get('points', 0),
                                'played': p_num,
                                'matches_played': p_num,
                                'wins': w_num,
                                'draws': row.get('draws', row.get('draw', 0)),
                                'losses': row.get('losses', row.get('lose', 0)),
                                'goals_for': row.get('goals_for', 0),
                                'goals_against': row.get('goals_against', 0),
                                'goal_diff': row.get('goal_diff', 0),
                                'form': row.get('form', ''),
                                'win_percentage': round((w_num / p_num) * 100, 1) if p_num else 0,
                            }
                            break
        except Exception as e:
            logger.warning(f"Error resolving team stats from league standings: {e}")

        if not stats_data or (isinstance(stats_data, dict) and stats_data.get('played', 0) == 0 and stats_data.get('matches_played', 0) == 0):
            sp_data = _fetch_soccer_team_stats_statpal(team_entity.external_id, api_season)
            if sp_data:
                stats_data = sp_data
        if not stats_data:
            stats_data = _fetch_soccer_team_stats_thesportsdb(team_entity)

    elif team_entity.sport in ('basketball', 'nba'):
        stats_data = _fetch_nba_team_stats_statpal(team_entity.external_id, api_season, team_name=team_entity.name)

    elif team_entity.sport in ('football', 'american_football', 'nfl'):
        stats_data = _fetch_nfl_team_stats(team_entity.external_id, api_season, team_name=team_entity.name)

    elif team_entity.sport in ('hockey', 'ice_hockey', 'nhl'):
        stats_data = _fetch_nhl_team_stats(team_entity.name, api_season)

    elif team_entity.sport in ('baseball', 'mlb'):
        stats_data = _fetch_mlb_team_stats(team_entity.external_id, api_season, team_name=team_entity.name)

    elif team_entity.sport in ['handball', 'volleyball']:
        try:
            league = team_entity.team_details.league if hasattr(team_entity, 'team_details') else None
            if league:
                st_res = _get_standings_for_league(request, league, season, highlight_team_id=team_entity.external_id, highlight_team_name=team_entity.name)
                if st_res.data and st_res.data.get('standings'):
                    for row in st_res.data['standings']:
                        if str(row.get('team_id')) == str(team_entity.id) or str(row.get('team_external_id')) == str(team_entity.external_id) or team_entity.name.lower() in str(row.get('team_name', '')).lower():
                            stats_data = row
                            break
        except Exception as e:
            logger.warning(f"Error fetching {team_entity.sport} team stats: {e}")

    elif team_entity.sport == 'tennis':
        tour = request.GET.get('tour', '').lower()
        if not tour:
            tour = 'wta' if ('wta' in team_entity.name.lower() or 'women' in team_entity.name.lower()) else 'atp'
        rankings = _get_tennis_rankings_helper(tour)
        player_stats = _fetch_thesportsdb_player_stats(team_entity.name, athlete_entity=team_entity) or {}

        clean_name = team_entity.name.lower().strip()
        matched_rank = None
        for r in rankings:
            r_name = str(r.get('player_name', '')).lower().strip()
            if clean_name and ((clean_name in r_name) or (r_name in clean_name)):
                matched_rank = r
                break

        if not matched_rank and tour == 'atp':
            wta_rankings = _get_tennis_rankings_helper('wta')
            for r in wta_rankings:
                r_name = str(r.get('player_name', '')).lower().strip()
                if clean_name and ((clean_name in r_name) or (r_name in clean_name)):
                    matched_rank = r
                    tour = 'wta'
                    break

        stats_data = player_stats or {}
        if matched_rank:
            stats_data['rank'] = matched_rank.get('rank')
            stats_data['points'] = matched_rank.get('points')
            stats_data['tour'] = tour.upper()
        elif not stats_data:
            stats_data = {'rankings': rankings, 'tour': tour.upper()}

    elif team_entity.sport == 'golf':
        stats_data = {'leaderboard': _get_golf_leaderboard_helper(), 'tour': 'PGA'}

    elif team_entity.sport == 'cricket':
        stats_data = _fetch_cricket_team_stats(team_entity.external_id, season)

    # Fallback to local DB Event calculation if live APIs return empty
    if not stats_data:
        stats_data = _fetch_stats_from_db_events(team_entity)

    stats_data = _normalize_team_stats(stats_data, team_entity=team_entity)

    # 3 — save to DB
    if stats_data:
        EntityStats.objects.update_or_create(
            entity=team_entity,
            season=stats_season,
            stat_type='season',
            defaults={'stats_data': stats_data},
        )

    return Response({
        'team': EntitySerializer(team_entity, context={'request': request}).data,
        'season': stats_season,
        'stats': stats_data,
        'source': 'live_api' if stats_data else 'empty',
    })


# ─────────────────────────────────────────────────────────────────────────────
# 2. TEAM ROSTER
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([AllowAny])
def get_team_roster(request, team_id):
    team_entity = get_object_or_404(Entity, id=team_id, type='team')
    team_entity = team_entity.canonical_entity or team_entity

    athletes = Athlete.objects.filter(
        Q(current_team=team_entity)
        | Q(current_team__external_id=team_entity.external_id, current_team__sport=team_entity.sport)
    ).select_related('entity').distinct()

    if athletes.count() < 10:
        try:
            from apps.sports_apis.services.thesportsdb import TheSportsDBService
            tsdb = TheSportsDBService()
            tsdb_players = tsdb.get_team_roster(
                team_id=team_entity.external_id if team_entity.api_source == 'thesportsdb' else None,
                team_name=team_entity.name
            )

            if tsdb_players and isinstance(tsdb_players, list):
                for p in tsdb_players:
                    if not isinstance(p, dict):
                        continue
                    p_name = str(p.get('name') or p.get('strPlayer') or '').strip()
                    if not p_name:
                        continue
                    p_ext_id = str(p.get('id_player') or p.get('idPlayer') or f"tsdb_{p_name.replace(' ', '_').lower()}")
                    player_entity, _ = Entity.objects.get_or_create(
                        api_source='thesportsdb',
                        external_id=p_ext_id,
                        defaults={
                            'type': 'athlete',
                            'name': p_name,
                            'sport': team_entity.sport,
                            'logo_url': p.get('headshot_url', '') or '',
                            'has_api_data': True,
                        }
                    )
                    name_parts = p_name.split()
                    first_name = name_parts[0] if name_parts else ''
                    last_name = ' '.join(name_parts[1:]) if len(name_parts) > 1 else ''

                    Athlete.objects.get_or_create(
                        entity=player_entity,
                        defaults={
                            'first_name': first_name,
                            'last_name': last_name,
                            'current_team': team_entity,
                            'position': p.get('position', '') or '',
                            'nationality': p.get('nationality', '') or '',
                        }
                    )

                athletes = Athlete.objects.filter(
                    Q(current_team=team_entity)
                    | Q(current_team__external_id=team_entity.external_id, current_team__sport=team_entity.sport)
                    | Q(current_team__name__iexact=team_entity.name, current_team__sport=team_entity.sport)
                ).select_related('entity').distinct()
        except Exception as err:
            logger.warning(f"TheSportsDB roster fetch error for {team_entity.name}: {err}")

    # Fallback to Wikipedia if TheSportsDB had no or insufficient players (< 8)
    if athletes.count() < 8:
        try:
            from apps.sports_apis.services.wikipedia import wikipedia_service
            wiki_players = wikipedia_service.get_team_roster(team_name=team_entity.name, sport=team_entity.sport)
            if wiki_players:
                for p in wiki_players:
                    p_name = str(p.get('name') or '').strip()
                    if not p_name:
                        continue
                    p_ext_id = f"wiki_{p_name.replace(' ', '_').lower()}_{team_entity.id}"
                    player_entity = Entity.objects.filter(
                        name=p_name,
                        type='athlete',
                        sport=team_entity.sport
                    ).first()
                    if not player_entity:
                        player_entity = Entity.objects.create(
                            name=p_name,
                            type='athlete',
                            sport=team_entity.sport,
                            api_source='wikipedia',
                            external_id=p_ext_id,
                            country=p.get('nationality', '') or team_entity.country or '',
                            has_api_data=True,
                        )

                    name_parts = p_name.split(' ', 1)
                    first_name = name_parts[0] if name_parts else ''
                    last_name = name_parts[1] if len(name_parts) > 1 else ''

                    ath_obj = Athlete.objects.filter(entity=player_entity).first()
                    if not ath_obj:
                        ath_obj = Athlete.objects.create(
                            entity=player_entity,
                            first_name=first_name,
                            last_name=last_name,
                            current_team=team_entity,
                            position=p.get('position', '') or '',
                            jersey_number=p.get('jersey_number'),
                            nationality=p.get('nationality', '') or team_entity.country or '',
                        )
                    else:
                        if ath_obj.current_team != team_entity:
                            ath_obj.current_team = team_entity
                            ath_obj.save()

                athletes = Athlete.objects.filter(
                    Q(current_team=team_entity)
                    | Q(current_team__external_id=team_entity.external_id, current_team__sport=team_entity.sport)
                    | Q(current_team__name__iexact=team_entity.name, current_team__sport=team_entity.sport)
                ).select_related('entity').distinct()
        except Exception as wiki_err:
            logger.warning(f"Wikipedia roster fallback error for {team_entity.name}: {wiki_err}")

    if not athletes.exists():
        INDIVIDUAL_SPORTS = ['tennis', 'golf', 'mma', 'boxing', 'combat_sports', 'motorsport', 'formula1', 'f1']
        if team_entity.sport in INDIVIDUAL_SPORTS:
            matching_ath = Athlete.objects.filter(
                Q(entity=team_entity) | Q(entity__canonical_entity=team_entity)
            ).first()
            if not matching_ath:
                name_parts = team_entity.name.replace('.', ' ').split()
                last_name = name_parts[-1] if name_parts else ''
                if len(last_name) > 2:
                    matching_ath = Athlete.objects.filter(
                        entity__sport=team_entity.sport,
                        last_name__iexact=last_name
                    ).first()
                    if not matching_ath:
                        ath_entity = Entity.objects.filter(
                            sport=team_entity.sport,
                            type='athlete',
                            name__icontains=last_name
                        ).first()
                        if ath_entity:
                            matching_ath = getattr(ath_entity, 'athlete_details', None)

            if matching_ath:
                roster = [{
                    'id':            matching_ath.entity.id,
                    'name':          f"{matching_ath.first_name} {matching_ath.last_name}".strip() or matching_ath.entity.name,
                    'position':      matching_ath.position or 'Player',
                    'jersey_number': matching_ath.jersey_number,
                    'photo':         matching_ath.entity.logo_url or team_entity.logo_url or '',
                    'height_cm':     matching_ath.height_cm,
                    'weight_kg':     matching_ath.weight_kg,
                    'nationality':   matching_ath.nationality or team_entity.country or '',
                }]
            else:
                roster = [{
                    'id':            team_entity.id,
                    'name':          team_entity.name,
                    'position':      'Player',
                    'jersey_number': None,
                    'photo':         team_entity.logo_url or '',
                    'height_cm':     None,
                    'weight_kg':     None,
                    'nationality':   team_entity.country or '',
                }]

            return Response({
                'team':         EntitySerializer(team_entity, context={'request': request}).data,
                'roster_count': len(roster),
                'roster':       roster,
            })

    if not athletes.exists() and team_entity.api_source == 'statpal':
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
# 3. TEAM STANDINGS
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([AllowAny])
def get_team_standings(request, team_id):
    """
    GET /api/entities/team/{team_id}/standings/
    Returns the official primary league standings for clubs or national rankings for national teams.
    """
    from .league import _get_standings_for_league, _fetch_statpal_hierarchical_standings

    entity = get_object_or_404(Entity, id=team_id)
    entity = entity.canonical_entity or entity

    if entity.type == 'league':
        season = request.GET.get('season') or str(_current_season(entity.sport))
        return _get_standings_for_league(request, entity, season)

    team_entity = entity
    season = request.GET.get('season') or str(_current_season(team_entity.sport))

    # 1. Cricket National Team -> ICC World Rankings
    if team_entity.sport == 'cricket':
        clean_name = _normalize_cricket_team_key(team_entity.name)
        icc_res = fetch_live_icc_rankings()
        by_format = icc_res.get('by_format', {}) if isinstance(icc_res, dict) else {}

        icc_tables = {}
        cricket_standings_list = []
        is_national = False
        for fmt, rows in by_format.items():
            fmt_rows = []
            for row in rows:
                t_name = row.get('team_name', '')
                t_key = _normalize_cricket_team_key(t_name)
                is_hl = (t_key == clean_name) or (clean_name and (clean_name in t_key or t_key in clean_name))
                if is_hl:
                    is_national = True
                row_copy = dict(row)
                row_copy['is_highlighted'] = is_hl
                fmt_rows.append(row_copy)
            icc_tables[fmt] = fmt_rows

        if is_national:
            active_fmt = request.GET.get('format', 'tests').lower()
            if active_fmt not in icc_tables:
                active_fmt = 'tests'
            cricket_standings_list = icc_tables.get(active_fmt, [])

            return Response({
                'team': EntitySerializer(team_entity, context={'request': request}).data,
                'season': season,
                'standings': cricket_standings_list,
                'icc_rankings': icc_tables,
                'source': 'icc_rankings',
                'message': 'ICC Rankings provided for Cricket national team.',
            })

    # 2. Soccer National Team -> FIFA World Rankings
    if team_entity.sport == 'soccer':
        clean_name = team_entity.name.lower().replace(' w', '').strip()
        fifa_res = fetch_live_fifa_rankings()
        by_format = fifa_res.get('by_format', {}) if isinstance(fifa_res, dict) else {}

        fifa_tables = {}
        is_national = False
        for fmt, rows in by_format.items():
            fifa_rows = []
            for row in rows:
                t_name = row.get('team_name', '')
                t_key = t_name.lower().replace(' w', '').strip()
                is_hl = (t_key == clean_name) or (clean_name and (clean_name in t_key or t_key in clean_name))
                if is_hl:
                    is_national = True
                row_copy = dict(row)
                row_copy['is_highlighted'] = is_hl
                fifa_rows.append(row_copy)
            fifa_tables[fmt] = fifa_rows

        if is_national:
            active_gender = 'women' if ' w' in team_entity.name.lower() else 'men'
            selected_table = fifa_tables.get(active_gender, [])
            return Response({
                'team': EntitySerializer(team_entity, context={'request': request}).data,
                'season': season,
                'standings': selected_table,
                'fifa_rankings': fifa_tables,
                'source': 'fifa_rankings',
                'message': 'FIFA World Rankings provided for Soccer national team.',
            })

    # 3. Tennis -> ATP / WTA Global Player Rankings
    if team_entity.sport == 'tennis':
        tour = request.GET.get('tour', 'atp').lower()
        rankings = _get_tennis_rankings_helper(tour)
        clean_name = team_entity.name.lower().strip()
        for r in rankings:
            r_name = str(r.get('player_name', '')).lower().strip()
            r['is_highlighted'] = bool(clean_name and ((clean_name in r_name) or (r_name in clean_name)))
        return Response({
            'team': EntitySerializer(team_entity, context={'request': request}).data,
            'season': season,
            'tour': tour.upper(),
            'standings': rankings,
            'source': 'tennis_rankings',
        })

    # 4. Golf -> PGA Tour Leaderboards
    if team_entity.sport == 'golf':
        leaderboards = _get_golf_leaderboard_helper()
        return Response({
            'team': EntitySerializer(team_entity, context={'request': request}).data,
            'season': season,
            'tour': 'PGA',
            'standings': leaderboards,
            'source': 'golf_leaderboards',
        })

    # 5. Baseball (MLB) Standings
    if team_entity.sport in ('baseball', 'mlb'):
        mlb_standings = _fetch_statpal_hierarchical_standings('baseball', f'standings:baseball:mlb:{season}')
        if mlb_standings:
            selected, conf_name, div_name = _filter_by_team_division(mlb_standings, team_entity)
            return Response({
                'team': EntitySerializer(team_entity, context={'request': request}).data,
                'season': season,
                'league_name': 'Major League Baseball (MLB)',
                'standings': selected,
            })

    # 6. Basketball (NBA) Standings
    if team_entity.sport in ('basketball', 'nba'):
        nba_standings = _fetch_statpal_hierarchical_standings('basketball', f'standings:nba:{season}')
        if nba_standings:
            selected, conf_name, div_name = _filter_by_team_division(nba_standings, team_entity)
            return Response({
                'team': EntitySerializer(team_entity, context={'request': request}).data,
                'season': season,
                'league_name': 'National Basketball Association (NBA)',
                'standings': selected,
            })

    # 7. Hockey (NHL) Standings
    if team_entity.sport in ('hockey', 'ice_hockey', 'nhl'):
        nhl_standings = _fetch_statpal_hierarchical_standings('hockey', f'standings:nhl:{season}')
        if nhl_standings:
            selected, conf_name, div_name = _filter_by_team_division(nhl_standings, team_entity)
            return Response({
                'team': EntitySerializer(team_entity, context={'request': request}).data,
                'season': season,
                'league_name': 'National Hockey League (NHL)',
                'standings': selected,
            })

    # 8. American Football (NFL) Standings
    if team_entity.sport in ('american_football', 'football', 'nfl'):
        nfl_standings = _fetch_statpal_hierarchical_standings('american_football', f'standings:nfl:{season}')
        if nfl_standings:
            selected, conf_name, div_name = _filter_by_team_division(nfl_standings, team_entity)
            return Response({
                'team': EntitySerializer(team_entity, context={'request': request}).data,
                'season': season,
                'league_name': 'National Football League (NFL)',
                'standings': selected,
            })

    # 9. For Club Teams -> Primary Official League Standings Lookup
    league = None
    try:
        if team_entity.team_details.league:
            league = team_entity.team_details.league
    except Exception:
        pass

    if not league:
        from apps.sports_apis.services.thesportsdb import TheSportsDBService
        tsdb_info = TheSportsDBService().search_team(team_entity.name)
        if tsdb_info and tsdb_info.get('idLeague'):
            league = Entity(
                name=tsdb_info.get('strLeague', ''),
                external_id=str(tsdb_info.get('idLeague')),
                api_source='thesportsdb',
                type='league',
                sport=team_entity.sport or 'soccer'
            )

    if league:
        res = _get_standings_for_league(request, league, season, highlight_team_id=team_entity.external_id, highlight_team_name=team_entity.name)
        if res.data and res.data.get('standings'):
            clean_team = team_entity.name.lower().replace(' fc', '').replace(' utd', ' united').strip()
            for row in res.data['standings']:
                t_name = str(row.get('team_name', '')).lower().replace(' fc', '').replace(' utd', ' united').strip()
                is_hl = (clean_team in t_name) or (t_name in clean_team) or (str(row.get('team_external_id')) == str(team_entity.external_id))
                row['is_highlighted'] = is_hl
            return res

    return Response({
        'team': EntitySerializer(team_entity, context={'request': request}).data,
        'season': season,
        'standings': [],
        'message': 'No standings available from provider API for this team.',
    })


# ─────────────────────────────────────────────────────────────────────────────
# 4. TEAM FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

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
