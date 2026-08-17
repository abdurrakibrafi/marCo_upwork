import logging
import requests
from django.shortcuts import get_object_or_404
from django.core.cache import cache
from django.utils import timezone
from django.db.models import Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.entity.models import Entity, Athlete, EntityStats, Team
from apps.entity.serializers import EntitySerializer
from .common import _current_season, HEADERS_SPORTS, HEADERS_BDL, resolve_team_venue

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# TEAM STATS HELPERS & FALLBACKS
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_soccer_team_stats_thesportsdb(team_entity):
    """Fallback: Search team on TheSportsDB API, calculate stats, and update logo_url from TheSportsDB."""
    try:
        from urllib.parse import quote
        from django.conf import settings

        api_key = getattr(settings, 'THESPORTSDB_KEY', None) or '092552'
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


CRICKET_TEAM_ALIAS_MAP = {
    'usa': 'united states of america',
    'u.s.a.': 'united states of america',
    'uae': 'united arab emirates',
    'u.a.e.': 'united arab emirates',
    'uk': 'england',
    'ban': 'bangladesh',
    'bd': 'bangladesh',
    'ind': 'india',
    'aus': 'australia',
    'pak': 'pakistan',
    'eng': 'england',
    'sa': 'south africa',
    'nz': 'new zealand',
    'sl': 'sri lanka',
    'wi': 'west indies',
    'afg': 'afghanistan',
    'zim': 'zimbabwe',
    'ire': 'ireland',
    'sco': 'scotland',
    'ned': 'netherlands',
}


def _normalize_cricket_team_key(name):
    if not name:
        return ''
    import re
    clean = str(name).lower().replace('cricket', '').replace('team', '').strip()
    clean = re.sub(r'\s+', ' ', clean)
    return CRICKET_TEAM_ALIAS_MAP.get(clean, clean)


def fetch_live_icc_rankings():
    """
    Scrape live Men's and Women's ICC Team Rankings (Test, ODI, T20I, WODI, WT20I) for ALL countries from Cricbuzz and cache for 24 hours.
    Includes a robust pre-baked seed fallback so VPS never returns empty rankings if external HTTP GET fails.
    """
    cache_key = 'scraped_icc_team_rankings_v8'
    try:
        cached_data = cache.get(cache_key)
        if cached_data:
            return cached_data
    except Exception:
        pass

    rankings_by_team = {}
    tables_by_format = {'test': [], 'odi': [], 't20i': [], 'wodi': [], 'wt20i': []}

    targets = [
        ('https://www.cricbuzz.com/cricket-stats/icc-rankings/men/teams', False),
        ('https://www.cricbuzz.com/cricket-stats/icc-rankings/women/teams', True),
    ]

    try:
        import re

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }

        for url, is_women in targets:
            try:
                res = requests.get(url, headers=headers, timeout=7)
                if res.status_code == 200:
                    format_matches = re.finditer(r'\\"(odi|test|t20)\\":\{\\"rank\\":\[(.*?)\]\}', res.text)
                    for m in format_matches:
                        fmt_key = m.group(1)
                        if is_women:
                            fmt_name = 'wodi' if fmt_key == 'odi' else ('wt20i' if fmt_key == 't20' else 'wtest')
                        else:
                            fmt_name = 't20i' if fmt_key == 't20' else fmt_key

                        items_raw = m.group(2)
                        item_matches = re.finditer(r'\\"rank\\":\\"(?P<rank>\d+)\\",\\"name\\":\\"(?P<name>[^\\]+)\\",\\"matches\\":\\"(?P<matches>\d+)\\",\\"rating\\":\\"(?P<rating>\d+)\\",\\"points\\":\\"(?P<points>\d+)\\"', items_raw)
                        for it in item_matches:
                            d = it.groupdict()
                            display_name = d['name']
                            rank_val = int(d['rank'])
                            matches_val = int(d['matches'])
                            points_val = int(d['points'])
                            rating_val = int(d['rating'])

                            team_key = display_name.lower().replace('cricket', '').strip()

                            if team_key not in rankings_by_team:
                                rankings_by_team[team_key] = {}
                            rankings_by_team[team_key][fmt_name] = {
                                'rank': rank_val,
                                'matches': matches_val,
                                'points': points_val,
                                'rating': rating_val,
                            }

                            if fmt_name in tables_by_format:
                                tables_by_format[fmt_name].append({
                                    'rank': rank_val,
                                    'team_name': display_name,
                                    'matches': matches_val,
                                    'points': points_val,
                                    'rating': rating_val,
                                    'rating': rating_val,
                                })
            except Exception:
                pass
    except Exception:
        pass

    # Build seed fallback if scraping yielded no data
    if not rankings_by_team or not any(tables_by_format.values()):
        seed_tables = {
            'test': [
                {'rank': 1, 'team_name': 'Australia', 'matches': 24, 'points': 3138, 'rating': 131},
                {'rank': 2, 'team_name': 'South Africa', 'matches': 19, 'points': 2256, 'rating': 119},
                {'rank': 3, 'team_name': 'New Zealand', 'matches': 22, 'points': 2336, 'rating': 106},
                {'rank': 4, 'team_name': 'India', 'matches': 26, 'points': 2714, 'rating': 104},
                {'rank': 5, 'team_name': 'England', 'matches': 32, 'points': 3158, 'rating': 99},
                {'rank': 6, 'team_name': 'Sri Lanka', 'matches': 16, 'points': 1222, 'rating': 76},
                {'rank': 7, 'team_name': 'Pakistan', 'matches': 19, 'points': 1427, 'rating': 75},
                {'rank': 8, 'team_name': 'West Indies', 'matches': 27, 'points': 2008, 'rating': 74},
                {'rank': 9, 'team_name': 'Bangladesh', 'matches': 21, 'points': 1539, 'rating': 73},
                {'rank': 10, 'team_name': 'Zimbabwe', 'matches': 13, 'points': 217, 'rating': 17},
            ],
            'odi': [
                {'rank': 1, 'team_name': 'India', 'matches': 33, 'points': 3841, 'rating': 116},
                {'rank': 2, 'team_name': 'New Zealand', 'matches': 35, 'points': 3809, 'rating': 109},
                {'rank': 3, 'team_name': 'Australia', 'matches': 29, 'points': 2965, 'rating': 102},
                {'rank': 4, 'team_name': 'South Africa', 'matches': 28, 'points': 2855, 'rating': 102},
                {'rank': 5, 'team_name': 'Pakistan', 'matches': 32, 'points': 3215, 'rating': 100},
                {'rank': 6, 'team_name': 'Sri Lanka', 'matches': 36, 'points': 3456, 'rating': 96},
                {'rank': 7, 'team_name': 'England', 'matches': 30, 'points': 2820, 'rating': 94},
                {'rank': 8, 'team_name': 'Afghanistan', 'matches': 26, 'points': 2361, 'rating': 91},
                {'rank': 9, 'team_name': 'Bangladesh', 'matches': 39, 'points': 3251, 'rating': 83},
                {'rank': 10, 'team_name': 'West Indies', 'matches': 34, 'points': 2624, 'rating': 77},
            ],
            't20i': [
                {'rank': 1, 'team_name': 'India', 'matches': 61, 'points': 16366, 'rating': 268},
                {'rank': 2, 'team_name': 'England', 'matches': 38, 'points': 10186, 'rating': 268},
                {'rank': 3, 'team_name': 'Australia', 'matches': 38, 'points': 9868, 'rating': 260},
                {'rank': 4, 'team_name': 'New Zealand', 'matches': 50, 'points': 12348, 'rating': 247},
                {'rank': 5, 'team_name': 'South Africa', 'matches': 48, 'points': 11717, 'rating': 244},
                {'rank': 6, 'team_name': 'West Indies', 'matches': 46, 'points': 11040, 'rating': 240},
                {'rank': 7, 'team_name': 'Pakistan', 'matches': 52, 'points': 12064, 'rating': 232},
                {'rank': 8, 'team_name': 'Bangladesh', 'matches': 53, 'points': 11860, 'rating': 224},
                {'rank': 9, 'team_name': 'Sri Lanka', 'matches': 44, 'points': 9768, 'rating': 222},
                {'rank': 10, 'team_name': 'Afghanistan', 'matches': 38, 'points': 8360, 'rating': 220},
            ],
            'wodi': [
                {'rank': 1, 'team_name': 'Australia Women', 'matches': 28, 'points': 4565, 'rating': 163},
                {'rank': 2, 'team_name': 'England Women', 'matches': 26, 'points': 3247, 'rating': 125},
                {'rank': 3, 'team_name': 'India Women', 'matches': 30, 'points': 3712, 'rating': 124},
                {'rank': 4, 'team_name': 'South Africa Women', 'matches': 36, 'points': 3614, 'rating': 100},
                {'rank': 5, 'team_name': 'New Zealand Women', 'matches': 24, 'points': 2312, 'rating': 96},
                {'rank': 6, 'team_name': 'Sri Lanka Women', 'matches': 24, 'points': 2133, 'rating': 89},
                {'rank': 7, 'team_name': 'West Indies Women', 'matches': 26, 'points': 1934, 'rating': 74},
                {'rank': 8, 'team_name': 'Bangladesh Women', 'matches': 21, 'points': 1537, 'rating': 73},
                {'rank': 9, 'team_name': 'Pakistan Women', 'matches': 26, 'points': 1902, 'rating': 73},
                {'rank': 10, 'team_name': 'Ireland Women', 'matches': 22, 'points': 1013, 'rating': 46},
            ],
            'wt20i': [
                {'rank': 1, 'team_name': 'Australia Women', 'matches': 28, 'points': 8151, 'rating': 291},
                {'rank': 2, 'team_name': 'England Women', 'matches': 38, 'points': 10504, 'rating': 276},
                {'rank': 3, 'team_name': 'India Women', 'matches': 41, 'points': 10743, 'rating': 262},
                {'rank': 4, 'team_name': 'New Zealand Women', 'matches': 32, 'points': 7966, 'rating': 249},
                {'rank': 5, 'team_name': 'South Africa Women', 'matches': 38, 'points': 9310, 'rating': 245},
                {'rank': 6, 'team_name': 'West Indies Women', 'matches': 33, 'points': 7829, 'rating': 237},
                {'rank': 7, 'team_name': 'Sri Lanka Women', 'matches': 37, 'points': 8763, 'rating': 237},
                {'rank': 8, 'team_name': 'Pakistan Women', 'matches': 34, 'points': 7189, 'rating': 211},
                {'rank': 9, 'team_name': 'Ireland Women', 'matches': 42, 'points': 8520, 'rating': 203},
                {'rank': 10, 'team_name': 'Bangladesh Women', 'matches': 37, 'points': 7240, 'rating': 196},
            ]
        }
        seed_teams = {}
        for fmt, rows in seed_tables.items():
            for r in rows:
                t_key = r['team_name'].lower().replace('cricket', '').strip()
                if t_key not in seed_teams:
                    seed_teams[t_key] = {}
                seed_teams[t_key][fmt] = {
                    'rank': r['rank'],
                    'matches': r['matches'],
                    'points': r['points'],
                    'rating': r['rating'],
                }
        rankings_by_team = seed_teams
        tables_by_format = seed_tables

    result = {
        'by_team': rankings_by_team,
        'by_format': tables_by_format,
    }

    try:
        cache.set(cache_key, result, 86400)
    except Exception:
        pass

    return result


def fetch_live_fifa_rankings():
    """
    Dynamically scrape/fetch live Men's and Women's FIFA World Rankings for ALL National Teams (Rank 1 to 211).
    Cached in Redis for 24 hours.
    """
    cache_key = 'scraped_fifa_team_rankings_live_v3'
    try:
        cached_data = cache.get(cache_key)
        if cached_data:
            return cached_data
    except Exception:
        pass

    import re
    from bs4 import BeautifulSoup

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    }

    scraped_men = []
    try:
        for page in range(1, 6):
            url = f'https://football-ranking.com/fifa-rankings?page={page}'
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                for row in soup.find_all('tr'):
                    cols = [c.text.strip() for c in row.find_all(['td', 'th']) if c.text.strip()]
                    if len(cols) >= 3 and cols[0].isdigit():
                        rank = int(cols[0])
                        team_name = cols[1].split('(')[0].strip()
                        pts_str = cols[2].replace(',', '').strip()
                        try:
                            pts = float(pts_str)
                        except Exception:
                            pts = 0.0
                        prev_rank = int(cols[4]) if len(cols) >= 5 and cols[4].isdigit() else rank
                        if team_name and len(team_name) > 1:
                            scraped_men.append({
                                'rank': rank,
                                'team_name': team_name,
                                'points': pts,
                                'previous_rank': prev_rank,
                            })
    except Exception as e:
        logger.warning(f"Live FIFA scraping failed: {e}")

    # Fallback to Wikipedia if primary source failed
    if not scraped_men:
        try:
            res = requests.get('https://en.wikipedia.org/wiki/FIFA_Men%27s_World_Ranking', headers=headers, timeout=10)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                tables = soup.find_all('table', {'class': 'wikitable'})
                for tbl in tables:
                    headers_text = ' '.join([th.text.strip().lower() for th in tbl.find_all('th')])
                    if 'year' in headers_text or 'team of the year' in headers_text:
                        continue
                    for row in tbl.find_all('tr'):
                        cols = [c.text.strip() for c in row.find_all(['td', 'th'])]
                        if len(cols) >= 4 and cols[0].isdigit():
                            rank = int(cols[0])
                            if not (1 <= rank <= 220):
                                continue
                            team_name = re.sub(r'\[.*?\]', '', cols[2]).strip()
                            team_name = re.sub(r'\s*\(\+?[\d.]+\s*pts\)', '', team_name).strip()
                            pts_str = re.sub(r'[^\d.]', '', cols[3])
                            pts = float(pts_str) if pts_str else 0.0
                            scraped_men.append({
                                'rank': rank,
                                'team_name': team_name,
                                'points': pts,
                                'previous_rank': rank,
                            })
        except Exception as e:
            logger.warning(f"Wikipedia FIFA fallback scraping failed: {e}")

    scraped_men.sort(key=lambda x: x['rank'])

    seed_tables = {
        'men': scraped_men if scraped_men else [],
        'women': []
    }

    rankings_by_team = {}
    from apps.entity.utils.matcher import find_team_logo_by_name
    for gender_key, rows in seed_tables.items():
        for r in rows:
            clean = r['team_name'].lower().replace(' w', '').strip()
            if clean not in rankings_by_team:
                rankings_by_team[clean] = {}
            if not r.get('logo_url'):
                r['logo_url'] = find_team_logo_by_name(r['team_name'])
            rankings_by_team[clean][gender_key] = r

    result = {
        'by_team': rankings_by_team,
        'by_format': seed_tables,
    }

    try:
        cache.set(cache_key, result, timeout=86400)
    except Exception:
        pass

    return result


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


def _get_tennis_rankings_helper(tour: str = "atp"):
    tour_slug = str(tour).lower()
    if tour_slug not in ("atp", "wta"):
        tour_slug = "atp"
    cache_key = f"standings:tennis:{tour_slug}"
    rankings = cache.get(cache_key)
    if not rankings:
        from apps.entity.tasks import update_tennis_rankings
        try:
            update_tennis_rankings()
            rankings = cache.get(cache_key)
        except Exception:
            pass
    return rankings or []


def _get_golf_leaderboard_helper():
    cache_key = "standings:golf:pga"
    leaderboard = cache.get(cache_key)
    if not leaderboard:
        from apps.entity.tasks import update_golf_leaderboards
        try:
            update_golf_leaderboards()
            leaderboard = cache.get(cache_key)
        except Exception:
            pass
    return leaderboard or []


# ─────────────────────────────────────────────────────────────────────────────
# GET TEAM STATS VIEW
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


# ─────────────────────────────────────────────────────────────────────────────
# TEAM ROSTER
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
# TEAM STANDINGS
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
            for fmt, rows in icc_tables.items():
                for r in rows:
                    r_item = dict(r)
                    r_item['format'] = fmt.upper()
                    cricket_standings_list.append(r_item)

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
            fmt_rows = []
            for row in rows:
                t_name = row.get('team_name', '')
                t_key = t_name.lower().replace(' w', '').strip()
                is_hl = (t_key == clean_name) or (clean_name and (clean_name in t_key or t_key in clean_name))
                if is_hl:
                    is_national = True
                row_copy = dict(row)
                row_copy['is_highlighted'] = is_hl
                fmt_rows.append(row_copy)
            fifa_tables[fmt] = fmt_rows

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
    if team_entity.sport == 'baseball':
        mlb_standings = _fetch_statpal_hierarchical_standings('baseball', f'standings:baseball:mlb:{season}')
        if mlb_standings:
            clean_team = team_entity.name.lower().replace(' fc', '').replace(' utd', ' united').strip()
            for row in mlb_standings:
                t_name = str(row.get('team_name', '')).lower().replace(' fc', '').replace(' utd', ' united').strip()
                is_hl = (clean_team in t_name) or (t_name in clean_team) or (str(row.get('team_external_id')) == str(team_entity.external_id))
                row['is_highlighted'] = is_hl
            return Response({
                'team': EntitySerializer(team_entity, context={'request': request}).data,
                'season': season,
                'league_name': 'Major League Baseball (MLB)',
                'standings': mlb_standings,
                'source': 'statpal_mlb',
            })

    # 6. Basketball (NBA) Standings
    if team_entity.sport in ('basketball', 'nba'):
        nba_standings = _fetch_statpal_hierarchical_standings('basketball', f'standings:nba:{season}')
        if nba_standings:
            clean_team = team_entity.name.lower().replace(' fc', '').replace(' utd', ' united').strip()
            for row in nba_standings:
                t_name = str(row.get('team_name', '')).lower().replace(' fc', '').replace(' utd', ' united').strip()
                is_hl = (clean_team in t_name) or (t_name in clean_team) or (str(row.get('team_external_id')) == str(team_entity.external_id))
                row['is_highlighted'] = is_hl
            return Response({
                'team': EntitySerializer(team_entity, context={'request': request}).data,
                'season': season,
                'league_name': 'National Basketball Association (NBA)',
                'standings': nba_standings,
                'source': 'statpal_nba',
            })

    # 7. Hockey (NHL) Standings
    if team_entity.sport in ('hockey', 'ice_hockey', 'nhl'):
        nhl_standings = _fetch_statpal_hierarchical_standings('hockey', f'standings:nhl:{season}')
        if nhl_standings:
            clean_team = team_entity.name.lower().replace(' fc', '').replace(' utd', ' united').strip()
            for row in nhl_standings:
                t_name = str(row.get('team_name', '')).lower().replace(' fc', '').replace(' utd', ' united').strip()
                is_hl = (clean_team in t_name) or (t_name in clean_team) or (str(row.get('team_external_id')) == str(team_entity.external_id))
                row['is_highlighted'] = is_hl
            return Response({
                'team': EntitySerializer(team_entity, context={'request': request}).data,
                'season': season,
                'league_name': 'National Hockey League (NHL)',
                'standings': nhl_standings,
                'source': 'statpal_nhl',
            })

    # 8. American Football (NFL) Standings
    if team_entity.sport in ('american_football', 'football', 'nfl'):
        nfl_standings = _fetch_statpal_hierarchical_standings('american_football', f'standings:nfl:{season}')
        if nfl_standings:
            clean_team = team_entity.name.lower().replace(' fc', '').replace(' utd', ' united').strip()
            for row in nfl_standings:
                t_name = str(row.get('team_name', '')).lower().replace(' fc', '').replace(' utd', ' united').strip()
                is_hl = (clean_team in t_name) or (t_name in clean_team) or (str(row.get('team_external_id')) == str(team_entity.external_id))
                row['is_highlighted'] = is_hl
            return Response({
                'team': EntitySerializer(team_entity, context={'request': request}).data,
                'season': season,
                'league_name': 'National Football League (NFL)',
                'standings': nfl_standings,
                'source': 'statpal_nfl',
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
# TEAM FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

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
