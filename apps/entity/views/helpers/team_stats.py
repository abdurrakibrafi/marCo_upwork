import logging
import requests
from django.core.cache import cache
from django.db.models import Q

from apps.entity.models import Entity
from apps.entity.views.common import _current_season, HEADERS_SPORTS, HEADERS_BDL, resolve_team_venue
from apps.entity.views.helpers.team_rankings import (
    fetch_live_icc_rankings,
    fetch_live_fifa_rankings,
    _normalize_cricket_team_key,
)

logger = logging.getLogger(__name__)


def _fetch_soccer_team_stats_thesportsdb(team_entity):
    """Fallback: Search team on TheSportsDB API, calculate stats, and update logo_url from TheSportsDB."""
    try:
        from urllib.parse import quote
        from django.conf import settings

        api_key = getattr(settings, 'THESPORTSDB_KEY', None)
        if not api_key:
            return {}
        team_name = team_entity.name if hasattr(team_entity, 'name') else str(team_entity)
        safe_name = quote(team_name)
        url = f"https://www.thesportsdb.com/api/v1/json/{api_key}/searchteams.php?t={safe_name}"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            teams = res.json().get('teams') or []
            if teams:
                target_sport = getattr(team_entity, 'sport', '').lower()
                team_data = None
                if target_sport:
                    for t in teams:
                        str_sport = str(t.get('strSport', '')).lower()
                        if target_sport in str_sport or str_sport in target_sport:
                            team_data = t
                            break
                if not team_data:
                    team_data = teams[0]

                team_id = team_data.get('idTeam')
                league_id = team_data.get('idLeague')

                # Replace api-sports logo_url with TheSportsDB badge URL
                badge = team_data.get('strBadge') or team_data.get('strLogo')
                if badge and hasattr(team_entity, 'logo_url') and getattr(team_entity, 'pk', None):
                    try:
                        if not team_entity.logo_url or 'api-sports' in team_entity.logo_url:
                            team_entity.logo_url = badge
                            team_entity.save(update_fields=['logo_url'])
                    except Exception:
                        pass

                # 1. Try fetching standings table directly from TSDB lookuptable
                season = str(_current_season(getattr(team_entity, 'sport', None)))
                if league_id:
                    tbl_res = requests.get(f"https://www.thesportsdb.com/api/v1/json/{api_key}/lookuptable.php?l={league_id}&s={season}", timeout=10)
                    if tbl_res.status_code == 200:
                        table = tbl_res.json().get('table') or []
                        for item in table:
                            if str(item.get('idTeam')) == str(team_id):
                                g_for = int(item.get('intGoalsFor') or 0)
                                g_against = int(item.get('intGoalsAgainst') or 0)
                                pld = int(item.get('intPlayed') or 0)
                                w = int(item.get('intWin') or 0)
                                l = int(item.get('intLoss') or 0)
                                return {
                                    'form': item.get('strForm', ''),
                                    'played': pld,
                                    'matches_played': pld,
                                    'wins': w,
                                    'draws': int(item.get('intDraw') or 0),
                                    'losses': l,
                                    'goals_for': g_for,
                                    'goals_against': g_against,
                                    'goal_diff': int(item.get('intGoalDifference') or (g_for - g_against)),
                                    'points': int(item.get('intPoints') or 0),
                                    'rank': int(item.get('intRank') or 0),
                                    'win_percentage': round(w / pld * 100, 1) if pld > 0 else 0.0,
                                }

                # 2. Fallback to recent events from TSDB eventslast
                events_res = requests.get(f"https://www.thesportsdb.com/api/v1/json/{api_key}/eventslast.php?id={team_id}", timeout=10)
                if events_res.status_code == 200:
                    results = events_res.json().get('results') or []
                    wins = losses = draws = 0
                    goals_for = goals_against = 0
                    for ev in results:
                        status = str(ev.get('strStatus', '')).upper()
                        if status in ('FT', 'AET', 'PEN', 'FINISHED', 'COMPLETED', '') and ev.get('intHomeScore') is not None and ev.get('intAwayScore') is not None:
                            is_home = (str(ev.get('idHomeTeam')) == str(team_id))
                            try:
                                h_score = int(ev.get('intHomeScore') or 0)
                                a_score = int(ev.get('intAwayScore') or 0)
                                t_score = h_score if is_home else a_score
                                o_score = a_score if is_home else h_score
                                goals_for += t_score
                                goals_against += o_score
                                if t_score > o_score:
                                    wins += 1
                                elif t_score < o_score:
                                    losses += 1
                                else:
                                    draws += 1
                            except (ValueError, TypeError):
                                pass
                    played = wins + losses + draws
                    if played > 0:
                        return {
                            'form': '',
                            'played': played,
                            'matches_played': played,
                            'wins': wins,
                            'draws': draws,
                            'losses': losses,
                            'goals_for': goals_for,
                            'goals_against': goals_against,
                            'win_percentage': round(wins / played * 100, 1),
                            'rank': 0,
                        }
    except Exception:
        pass
    return {}


def _fetch_stats_from_db_events(team_entity):
    """Fallback: Calculate team stats from completed Event records in local DB or return clean default structure."""
    from apps.event.models import Event

    events = Event.objects.filter(
        Q(home_entity=team_entity) | Q(away_entity=team_entity),
        status='completed'
    )

    wins = losses = draws = 0
    goals_for = goals_against = 0

    for event in events:
        is_home = (event.home_entity_id == team_entity.id)
        team_score = event.home_score if is_home else event.away_score
        opp_score = event.away_score if is_home else event.home_score

        if team_score is not None and opp_score is not None:
            goals_for += team_score
            goals_against += opp_score
            if team_score > opp_score:
                wins += 1
            elif team_score < opp_score:
                losses += 1
            else:
                draws += 1

    played = wins + losses + draws
    return {
        'form': '',
        'played': played,
        'wins': wins,
        'draws': draws,
        'losses': losses,
        'goals_for': goals_for,
        'goals_against': goals_against,
        'win_percentage': round(wins / played * 100, 1) if played > 0 else 0.0,
    }


def _normalize_team_stats(stats_data, team_entity=None):
    """
    Ensure all team stats responses contain standard fields across all sports:
    - matches_played, win_percentage, draws, goals_for, goals_against, points, goal_diff, rank
    """
    if not isinstance(stats_data, dict) or not stats_data:
        return stats_data

    if 'rankings' in stats_data or 'leaderboard' in stats_data or 'position' in stats_data or 'date_of_birth' in stats_data:
        return stats_data

    wins = int(stats_data.get('wins') or 0)
    losses = int(stats_data.get('losses') or 0)
    draws = int(stats_data.get('draws') or stats_data.get('ties') or stats_data.get('ot_losses') or 0)

    matches_played = int(
        stats_data.get('matches_played') or 
        stats_data.get('played') or 
        (wins + losses + draws)
    )

    win_perc = stats_data.get('win_percentage')
    if win_perc is None:
        win_perc = stats_data.get('win_pct')
    if win_perc is None:
        win_perc = round(wins / matches_played * 100, 1) if matches_played > 0 else 0.0
    else:
        try:
            win_perc = float(win_perc)
        except (ValueError, TypeError):
            win_perc = 0.0

    goals_for = int(stats_data.get('goals_for') or stats_data.get('points_for') or 0)
    goals_against = int(stats_data.get('goals_against') or stats_data.get('points_against') or 0)

    # points
    pts = stats_data.get('points')
    if pts is None:
        pts = (wins * 3) + (draws * 1)
    else:
        try:
            pts = int(pts)
        except (ValueError, TypeError):
            pts = (wins * 3) + (draws * 1)

    # goal_diff
    g_diff = stats_data.get('goal_diff')
    if g_diff is None:
        g_diff = stats_data.get('difference')
    if g_diff is None or isinstance(g_diff, str):
        g_diff = goals_for - goals_against
    else:
        try:
            g_diff = int(g_diff)
        except (ValueError, TypeError):
            g_diff = goals_for - goals_against

    # rank
    rnk = stats_data.get('rank')
    if rnk is None:
        rnk = stats_data.get('position')
    if rnk is not None:
        try:
            rnk = int(rnk)
        except (ValueError, TypeError):
            rnk = 0
    else:
        rnk = 0

    stats_data['matches_played'] = matches_played
    stats_data['played'] = matches_played
    stats_data['win_percentage'] = win_perc
    stats_data['draws'] = draws
    stats_data['goals_for'] = goals_for
    stats_data['goals_against'] = goals_against
    stats_data['points'] = pts
    stats_data['goal_diff'] = g_diff
    stats_data['rank'] = rnk

    # Cricket readable aliases
    team_name = ''
    if team_entity and hasattr(team_entity, 'name'):
        team_name = team_entity.name.lower()
    elif 'team' in stats_data and isinstance(stats_data['team'], dict):
        team_name = str(stats_data['team'].get('name', '')).lower()
    elif stats_data.get('team_name'):
        team_name = str(stats_data.get('team_name')).lower()

    is_cricket = (team_entity and getattr(team_entity, 'sport', '').lower() == 'cricket') or ('cricket' in team_name)
    if is_cricket:
        clean_name = _normalize_cricket_team_key(team_name)
        icc_res = fetch_live_icc_rankings()
        icc_map = icc_res.get('by_team', {}) if isinstance(icc_res, dict) and 'by_team' in icc_res else icc_res
        icc_info = None
        if clean_name:
            for k, v in icc_map.items():
                ck = _normalize_cricket_team_key(k)
                if ck == clean_name or ck in clean_name or clean_name in ck:
                    icc_info = v
                    break

        if icc_info:
            stats_data['icc_rankings'] = icc_info

        stats_data['rank'] = 0

        if goals_for > 0 or goals_against > 0:
            stats_data['runs_scored'] = goals_for
            stats_data['runs_conceded'] = goals_against
            stats_data['run_difference'] = g_diff
        else:
            stats_data.pop('runs_scored', None)
            stats_data.pop('runs_conceded', None)
            stats_data.pop('run_difference', None)

        stats_data.pop('goals_for', None)
        stats_data.pop('goals_against', None)
        stats_data.pop('goal_diff', None)

    is_soccer = (team_entity and getattr(team_entity, 'sport', '').lower() == 'soccer') or ('soccer' in team_name)
    if is_soccer:
        fifa_res = fetch_live_fifa_rankings()
        fifa_map = fifa_res.get('by_team', {}) if isinstance(fifa_res, dict) and 'by_team' in fifa_res else {}
        clean_name = team_name.lower().replace(' w', '').strip()
        fifa_info = None
        if clean_name:
            for k, v in fifa_map.items():
                if k == clean_name or k in clean_name or clean_name in k:
                    fifa_info = v
                    break

        if fifa_info:
            stats_data['fifa_rankings'] = fifa_info
            fifa_rank = None
            if isinstance(fifa_info, dict):
                if 'men' in fifa_info and isinstance(fifa_info['men'], dict) and 'rank' in fifa_info['men']:
                    fifa_rank = fifa_info['men']['rank']
                elif 'women' in fifa_info and isinstance(fifa_info['women'], dict) and 'rank' in fifa_info['women']:
                    fifa_rank = fifa_info['women']['rank']
                elif 'rank' in fifa_info:
                    fifa_rank = fifa_info['rank']

            if fifa_rank is not None:
                try:
                    f_rank_int = int(fifa_rank)
                    if f_rank_int > 0 and stats_data.get('rank', 0) == 0:
                        stats_data['rank'] = f_rank_int
                except (ValueError, TypeError):
                    pass

    return stats_data


def _fetch_cricket_team_stats(external_id, season):
    cache_key = f'team_stats:cricket:{external_id}:{season}:statpal'
    cached = cache.get(cache_key)
    if cached:
        return cached

    try:
        from apps.sports_apis.services.statpal import statpal_service

        tours_resp = statpal_service.get_cricket_tournaments()
        if not tours_resp.get('success'):
            return {}

        tours_raw = tours_resp.get('data', {}).get('tours', {}).get('category', [])
        if isinstance(tours_raw, dict):
            tours_raw = [tours_raw]

        wins = losses = draws = no_results = 0

        for tour in tours_raw:
            tour_id   = tour.get('id')
            tour_uri  = tour.get('schedule_uri', '')

            if not tour_id or not tour_uri:
                continue

            parts = [p for p in tour_uri.strip('/').split('/') if p]
            if len(parts) < 2:
                continue
            tournament_type = parts[0]
            tournament_id   = parts[1]

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
                    if str(match.get('status', '')).lower() not in ('finished', 'complete', 'completed'):
                        continue

                    home = match.get('home', {})
                    away = match.get('away', {})
                    home_id = str(home.get('id', ''))
                    away_id = str(away.get('id', ''))

                    if str(external_id) not in (home_id, away_id):
                        continue

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


def _fetch_nfl_team_stats(external_id, season, team_name=None):
    clean_name = str(team_name or '').lower().strip()
    cache_key = f'team_stats:football:{external_id}:{clean_name}:{season}:statpal'
    try:
        cached = cache.get(cache_key)
        if cached:
            return cached
    except Exception:
        pass

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
                        t_id = str(t.get('id', ''))
                        t_name = str(t.get('name', '')).lower().strip()
                        is_match = (external_id and str(external_id) == t_id) or (
                            clean_name and (clean_name in t_name or t_name in clean_name)
                        )
                        if is_match:
                            wins   = int(t.get('won') or 0)
                            losses = int(t.get('lost') or 0)
                            ties   = int(t.get('ties') or 0)
                            played = wins + losses + ties
                            win_pct_raw = str(t.get('win_percentage') or '0')
                            if win_pct_raw.startswith('.'):
                                win_pct_raw = '0' + win_pct_raw
                            try:
                                win_pct = float(win_pct_raw)
                            except ValueError:
                                win_pct = 0.0

                            stats_data = {
                                'wins':           wins,
                                'losses':         losses,
                                'ties':           ties,
                                'matches_played': played,
                                'win_percentage': round(win_pct * 100, 1) if win_pct <= 1 else win_pct,
                                'points_for':     int(t.get('points_for') or 0),
                                'points_against': int(t.get('points_against') or 0),
                                'runs_diff':      int(t.get('difference') or 0),
                                'goal_diff':      int(t.get('difference') or 0),
                                'conference':     lg.get('name', ''),
                                'division':       div.get('name', ''),
                                'rank':           int(t.get('position') or 0),
                                'streak':         t.get('streak', ''),
                                'home_record':    t.get('home_record', ''),
                                'road_record':    t.get('road_record', ''),
                                'conference_record': t.get('conference_record', ''),
                                'division_record': t.get('division_record', ''),
                            }
                            try:
                                cache.set(cache_key, stats_data, timeout=3600)
                            except Exception:
                                pass
                            return stats_data
        return {}
    except Exception:
        return {}


def _fetch_nhl_team_stats(team_name, season):
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


def _fetch_mlb_team_stats(external_id, season, team_name=None):
    clean_name = str(team_name or '').lower().strip()
    cache_key = f'team_stats:baseball:{external_id}:{clean_name}:{season}:statpal'
    try:
        cached = cache.get(cache_key)
        if cached:
            return cached
    except Exception:
        pass

    try:
        from apps.sports_apis.services.statpal import statpal_service
        result = statpal_service.get_mlb_standings()
        if result.get('success'):
            standings_data = result.get('data', {}).get('standings', {})
            container = standings_data.get('category') or standings_data.get('tournament') or {}
            leagues = container.get('league', [])
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
                        t_id = str(t.get('id', ''))
                        t_name = str(t.get('name', '')).lower().strip()
                        is_match = (external_id and str(external_id) == t_id) or (
                            clean_name and (clean_name in t_name or t_name in clean_name)
                        )
                        if is_match:
                            wins = int(t.get('won') or t.get('wins') or 0)
                            losses = int(t.get('lost') or t.get('losses') or 0)
                            played = int(t.get('games_played') or (wins + losses))
                            pct = float(t.get('pct') or t.get('win_percentage') or (round(wins / played, 3) if played else 0.0))
                            stats_data = {
                                'wins': wins,
                                'losses': losses,
                                'matches_played': played,
                                'win_percentage': round(pct * 100, 1) if pct <= 1 else pct,
                                'runs_for': int(t.get('runs_scored') or t.get('runs_for') or 0),
                                'runs_against': int(t.get('runs_allowed') or t.get('runs_against') or 0),
                                'runs_diff': int(t.get('runs_diff') or 0),
                                'streak': str(t.get('current_streak') or t.get('streak') or ''),
                                'conference': lg.get('name', ''),
                                'division': div.get('name', ''),
                                'rank': int(t.get('position') or t.get('rank') or 0),
                            }
                            try:
                                cache.set(cache_key, stats_data, timeout=3600)
                            except Exception:
                                pass
                            return stats_data
    except Exception:
        pass

    try:
        from apps.sports_apis.services.statpal import statpal_service
        wins = losses = 0

        for offset in range(-7, 0):
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
            'note':           'Last 7 days only (StatPal MLB fixtures fallback)',
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
        # Get the first league linked to this team
        team_entity = Entity.objects.filter(external_id=str(external_id)).first()
        league_id = None
        if team_entity:
            try:
                league_id = team_entity.team_details.league.external_id
            except Exception:
                pass

        if not league_id and team_entity:
            from apps.event.models import Event
            event = Event.objects.filter(
                Q(home_entity=team_entity) | Q(away_entity=team_entity),
                league__isnull=False
            ).select_related('league').first()
            if event and event.league:
                league_id = event.league.external_id

        if not league_id:
            try:
                resp = requests.get(
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

        resp = requests.get(
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
        resp = requests.get(
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


def _fetch_nba_team_stats_statpal(external_id, season, team_name=None):
    clean_name = str(team_name or '').lower().strip()
    cache_key = f'team_stats:nba:{external_id}:{clean_name}:{season}:statpal'
    try:
        cached = cache.get(cache_key)
        if cached:
            return cached
    except Exception:
        pass

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
                    s_id = str(standing.get('id', ''))
                    s_name = str(standing.get('name', '')).lower().strip()
                    is_match = (external_id and str(external_id) == s_id) or (
                        clean_name and (clean_name in s_name or s_name in clean_name)
                    )
                    if is_match:
                        wins = int(standing.get('won') or 0)
                        losses = int(standing.get('lost') or 0)
                        total = wins + losses
                        stats_data = {
                            'wins':       wins,
                            'losses':     losses,
                            'matches_played': total,
                            'win_pct':    round(wins / total * 100, 1) if total else 0,
                            'win_percentage': round(wins / total * 100, 1) if total else 0,
                            'conference': conf.get('name', ''),
                            'division':   lg.get('name', ''),
                            'rank':       int(standing.get('position') or 0),
                        }
                        try:
                            cache.set(cache_key, stats_data, timeout=3600)
                        except Exception:
                            pass
                        return stats_data
        return {}
    except Exception:
        return {}


def _filter_by_team_division(standings_list, team_entity):
    """
    Given hierarchical standings rows (MLB, NBA, NFL, NHL),
    find the team's exact division (and conference), and return ONLY
    the teams belonging to that division, ordered by their official rank (1..N).
    """
    if not standings_list:
        return [], None, None

    clean_team = team_entity.name.lower().replace(' fc', '').replace(' utd', ' united').strip()
    target_conf = None
    target_div = None

    # Find the team's conference and division
    for row in standings_list:
        t_name = str(row.get('team_name', '')).lower().replace(' fc', '').replace(' utd', ' united').strip()
        is_match = (clean_team in t_name) or (t_name in clean_team) or (team_entity.external_id and str(row.get('team_external_id')) == str(team_entity.external_id))
        if is_match:
            target_conf = str(row.get('conference') or '').strip()
            target_div = str(row.get('division') or '').strip()
            break

    # If team's division is found, filter to that exact division (and conference)
    division_teams = []
    if target_div:
        for row in standings_list:
            row_conf = str(row.get('conference') or '').strip()
            row_div = str(row.get('division') or '').strip()
            if row_div == target_div and (not target_conf or row_conf == target_conf):
                row_copy = dict(row)
                t_name = str(row_copy.get('team_name', '')).lower().replace(' fc', '').replace(' utd', ' united').strip()
                row_copy['is_highlighted'] = bool((clean_team in t_name) or (t_name in clean_team) or (team_entity.external_id and str(row_copy.get('team_external_id')) == str(team_entity.external_id)))
                division_teams.append(row_copy)

    # Sort division teams by official rank (1, 2, 3...)
    if division_teams:
        division_teams.sort(key=lambda x: int(x.get('rank') or 999))
        return division_teams, target_conf, target_div

    # Fallback to original standings if division match wasn't found
    return standings_list, target_conf, target_div


def _fetch_team_fixtures_live(team_entity):
    """Fallback to live provider API (TheSportsDB) for team fixtures when DB has 0 events."""
    try:
        from apps.sports_apis.services.thesportsdb import TheSportsDBService
        tsdb = TheSportsDBService()
        
        NOISE = {'fc', 'united', 'city', 'real', 'club', 'town', 'athletic', 'rovers', 'wanderers', 'county', 'saint', 'st', 'de', 'la', 'sports', 'league', 'team', 'national', 'field', 'men', "men's", 'mens', 'women', "women's", 'womens'}
        query_vars = [team_entity.name]
        cleaned = [w for w in team_entity.name.split() if w.lower() not in NOISE]
        if cleaned:
            query_vars.append(" ".join(cleaned))
        if len(cleaned) > 1:
            query_vars.append(cleaned[0])

        team_info = None
        for qv in query_vars:
            team_info = tsdb.search_team(qv)
            if team_info and team_info.get('idTeam'):
                break

        if not team_info or not team_info.get('idTeam'):
            return []
        
        team_id = str(team_info.get('idTeam'))
        next_data = tsdb._get('eventsnext.php', {'id': team_id})
        last_data = tsdb._get('eventslast.php', {'id': team_id})
        
        raw_events = (last_data.get('results') or []) + (next_data.get('events') or [])
        fixtures = []
        for ev in raw_events:
            try:
                home_name = ev.get('strHomeTeam', '')
                away_name = ev.get('strAwayTeam', '')
                home_logo = ev.get('strHomeTeamBadge', '')
                away_logo = ev.get('strAwayTeamBadge', '')
                league_name = ev.get('strLeague', '')
                league_logo = ev.get('strLeagueBadge', '')

                home_ent = Entity.objects.filter(name__iexact=home_name).first()
                away_ent = Entity.objects.filter(name__iexact=away_name).first()

                h_score = ev.get('intHomeScore')
                a_score = ev.get('intAwayScore')

                v_name = ev.get('strVenue', '') or ''
                v_city = ev.get('strCity', '') or ev.get('strCountry', '') or ''
                if not v_name or not v_city:
                    auto_v_name, auto_v_city, auto_v_country = resolve_team_venue(home_name)
                    if not v_name:
                        v_name = auto_v_name
                    if not v_city:
                        v_city = auto_v_city

                fixtures.append({
                    'id': str(ev.get('idEvent', '')),
                    'sport': (ev.get('strSport') or team_entity.sport or 'soccer').lower(),
                    'status': 'completed' if ev.get('strStatus') in ('FT', 'AET', 'PEN') else 'upcoming',
                    'status_detail': ev.get('strStatus') or '',
                    'home_entity': {
                        'id': home_ent.id if home_ent else None,
                        'name': home_name,
                        'logo_url': home_logo,
                        'type': 'team',
                        'sport': team_entity.sport or 'soccer',
                    },
                    'away_entity': {
                        'id': away_ent.id if away_ent else None,
                        'name': away_name,
                        'logo_url': away_logo,
                        'type': 'team',
                        'sport': team_entity.sport or 'soccer',
                    },
                    'league': {
                        'id': None,
                        'name': league_name,
                        'logo_url': league_logo,
                        'type': 'league',
                        'sport': team_entity.sport or 'soccer',
                    },
                    'home_score': int(h_score) if h_score is not None and str(h_score).isdigit() else None,
                    'away_score': int(a_score) if a_score is not None and str(a_score).isdigit() else None,
                    'start_time': ev.get('strTimestamp') or ev.get('dateEvent'),
                    'venue_name': v_name,
                    'venue_city': v_city,
                    'broadcaster': '',
                    'stream_url': ev.get('strVideo', '') or '',
                    # Convenience aliases
                    'event_name': ev.get('strEvent', ''),
                    'home_team': home_name,
                    'away_team': away_name,
                    'home_logo': home_logo,
                    'away_logo': away_logo,
                    'video_url': ev.get('strVideo', '') or '',
                })
            except Exception:
                continue

        if not fixtures:
            try:
                from apps.sports_apis.services.statpal import statpal_service
                sp_data = statpal_service.get_fixtures(team_entity.sport or 'soccer')
                raw_sp_events = sp_data.get('data') or sp_data.get('fixtures') or sp_data.get('livescore') or []
                if isinstance(raw_sp_events, list):
                    t_name_lower = team_entity.name.lower()
                    for item in raw_sp_events:
                        if isinstance(item, dict):
                            h_name = item.get('home_team', '') or item.get('home', '') or item.get('homeTeam', '')
                            a_name = item.get('away_team', '') or item.get('away', '') or item.get('awayTeam', '')
                            if t_name_lower in h_name.lower() or t_name_lower in a_name.lower():
                                fixtures.append({
                                    'id': str(item.get('id', '')),
                                    'sport': team_entity.sport or 'soccer',
                                    'status': item.get('status', 'upcoming'),
                                    'status_detail': item.get('status', ''),
                                    'home_entity': {'id': None, 'name': h_name, 'logo_url': item.get('home_logo', ''), 'type': 'team', 'sport': team_entity.sport},
                                    'away_entity': {'id': None, 'name': a_name, 'logo_url': item.get('away_logo', ''), 'type': 'team', 'sport': team_entity.sport},
                                    'league': {'id': None, 'name': item.get('league_name', ''), 'logo_url': '', 'type': 'league', 'sport': team_entity.sport},
                                    'home_score': item.get('home_score'),
                                    'away_score': item.get('away_score'),
                                    'start_time': item.get('start_time') or item.get('date'),
                                    'venue_name': '',
                                    'venue_city': '',
                                    'broadcaster': '',
                                    'stream_url': '',
                                    'event_name': f"{h_name} vs {a_name}",
                                    'home_team': h_name,
                                    'away_team': a_name,
                                    'home_logo': item.get('home_logo', ''),
                                    'away_logo': item.get('away_logo', ''),
                                    'video_url': '',
                                })
            except Exception as sp_err:
                logger.debug(f"StatPal fallback error in _fetch_team_fixtures_live: {sp_err}")

        return fixtures
    except Exception as e:
        logger.error(f"Error in _fetch_team_fixtures_live: {str(e)}")
        return []
