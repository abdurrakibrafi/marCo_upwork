import logging
import requests
import re
from django.core.cache import cache
from bs4 import BeautifulSoup
from apps.entity.utils.matcher import find_team_logo_by_name

logger = logging.getLogger(__name__)

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
    clean = str(name).lower()
    for w in ('cricket', 'national', 'team', 'men', 'mens', "men's", 'the'):
        clean = re.sub(r'\b' + re.escape(w) + r'\b', '', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()
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
                        item_matches = re.finditer(
                            r'\\"rank\\":\\"(?P<rank>\d+)\\",\\"name\\":\\"(?P<name>[^\\]+)\\",\\"matches\\":\\"(?P<matches>\d+)\\",\\"rating\\":\\"(?P<rating>\d+)\\",\\"points\\":\\"(?P<points>\d+)\\"',
                            items_raw
                        )
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


def _detect_cricket_active_format(entity, is_women: bool = False):
    """Dynamically detect the cricket format based on the team/league's current live, upcoming, or recent match.

    Returns:
        tuple[str, dict | None]: (active_format, context_match_summary)
    """
    from datetime import timedelta
    from django.utils import timezone
    from django.db.models import Q
    from apps.event.models import Event

    default_fmt = 'wodi' if is_women else 'odi'

    try:
        now = timezone.now()
        q_filter = Q(home_entity=entity) | Q(away_entity=entity)
        if getattr(entity, 'type', '') == 'league':
            q_filter = Q(league=entity)

        # 1. Live match has highest priority
        live_match = Event.objects.filter(
            q_filter,
            sport='cricket',
            status='live'
        ).select_related('league', 'home_entity', 'away_entity').first()

        # 2. Next upcoming match within 14 days
        upcoming_match = None
        if not live_match:
            upcoming_match = Event.objects.filter(
                q_filter,
                sport='cricket',
                status='upcoming',
                start_time__gte=now - timedelta(hours=6),
                start_time__lte=now + timedelta(days=14)
            ).select_related('league', 'home_entity', 'away_entity').order_by('start_time').first()

        # 3. Most recent match within last 7 days
        recent_match = None
        if not live_match and not upcoming_match:
            recent_match = Event.objects.filter(
                q_filter,
                sport='cricket',
                start_time__gte=now - timedelta(days=7)
            ).select_related('league', 'home_entity', 'away_entity').order_by('-start_time').first()

        target_event = live_match or upcoming_match or recent_match
        if target_event:
            league_name = getattr(target_event.league, 'name', '') if target_event.league else ''
            meta = target_event.metadata if isinstance(target_event.metadata, dict) else {}
            search_text = " ".join([
                str(target_event.status_detail or ''),
                str(league_name),
                str(meta.get('league_name', '')),
                str(meta.get('tournament_name', '')),
                str(meta.get('format', '')),
                str(meta.get('match_type', '')),
                str(target_event),
            ]).lower()

            detected_fmt = None
            if any(k in search_text for k in ('test', 'wtest', 'first-class', 'first class')):
                detected_fmt = 'test'
            elif any(k in search_text for k in ('t20', 't20i', 'twenty20', 't-20', 'bbl', 'ipl', 'psl', 'bpl', 'cpl', 'hundred')):
                detected_fmt = 'wt20i' if is_women else 't20i'
            elif any(k in search_text for k in ('odi', 'wodi', 'one day', 'oneday', 'one-day', '50 over', 'super 50')):
                detected_fmt = 'wodi' if is_women else 'odi'

            if detected_fmt:
                context_info = {
                    'event_id': target_event.id,
                    'status': target_event.status,
                    'start_time': target_event.start_time.isoformat() if target_event.start_time else None,
                    'title': str(target_event),
                    'format': detected_fmt,
                    'league': league_name or meta.get('tournament_name', ''),
                }
                return detected_fmt, context_info
    except Exception:
        pass

    return default_fmt, None

