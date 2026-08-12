import logging
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

logger = logging.getLogger(__name__)

HEADERS_SPORTS = {'x-apisports-key': settings.API_SPORTS_KEY}
HEADERS_BDL    = {'Authorization': settings.BALLDONTLIE_KEY}
 
 
def _current_season(sport='soccer'):
    """Always return the current calendar year (e.g. 2026)."""
    return datetime.now().year
 
 
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
                    from apps.entity.models import Athlete
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
def _fetch_soccer_team_stats_thesportsdb(team_entity):
    """Fallback: Search team on TheSportsDB API, calculate stats, and update logo_url from TheSportsDB."""
    try:
        import requests
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
    from django.db.models import Q

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
        from django.core.cache import cache
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
        import requests, re

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
                                })
            except Exception:
                pass
    except Exception:
        pass

    # Build seed fallback if scraping yielded no data (e.g. VPS network block or HTTP error)
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
        from django.core.cache import cache
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
        from django.core.cache import cache
        cached_data = cache.get(cache_key)
        if cached_data:
            return cached_data
    except Exception:
        pass

    import requests, re
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
        from django.core.cache import cache
        cache.set(cache_key, result, timeout=86400)
    except Exception:
        pass

    return result


def _normalize_team_stats(stats_data, team_entity=None):
    """
    Ensure all team stats responses contain standard fields across all sports:
    - matches_played
    - win_percentage
    - draws
    - goals_for
    - goals_against
    - points
    - goal_diff
    - rank
    """
    if not isinstance(stats_data, dict) or not stats_data:
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

    # 1. points
    pts = stats_data.get('points')
    if pts is None:
        pts = (wins * 3) + (draws * 1)
    else:
        try:
            pts = int(pts)
        except (ValueError, TypeError):
            pts = (wins * 3) + (draws * 1)

    # 2. goal_diff
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

# ─────────────────────────────────────────────────────────────────────────────
    # 3. rank
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

    # Standardized keys across all sports
    stats_data['matches_played'] = matches_played
    stats_data['played'] = matches_played
    stats_data['win_percentage'] = win_perc
    stats_data['draws'] = draws
    stats_data['goals_for'] = goals_for
    stats_data['goals_against'] = goals_against
    stats_data['points'] = pts
    stats_data['goal_diff'] = g_diff
    stats_data['rank'] = rnk

    # Add cricket-specific readable aliases & clear irrelevant soccer fields
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

        stats_data['rank'] = 0  # Format-agnostic top level rank is 0 for cricket (rankings belong inside icc_rankings)

        # Only add runs_scored / runs_conceded if runs > 0
        if goals_for > 0 or goals_against > 0:
            stats_data['runs_scored'] = goals_for
            stats_data['runs_conceded'] = goals_against
            stats_data['run_difference'] = g_diff
        else:
            stats_data.pop('runs_scored', None)
            stats_data.pop('runs_conceded', None)
            stats_data.pop('run_difference', None)

        # Pop soccer specific goal terms for cricket so response is clean
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

    return stats_data


@api_view(['GET'])
@permission_classes([AllowAny])
def get_team_stats(request, team_id):
    """
    GET /api/entities/team/{team_id}/stats/?season=2024
    """
    team_entity = get_object_or_404(Entity, id=team_id, type='team')
    team_entity = team_entity.canonical_entity or team_entity
    season = request.GET.get('season') or str(_current_season(team_entity.sport))
    # NBA standings are stored with the full season label (for example,
    # ``2025-26``), while StatPal requests use the season's start year.
    stats_season = (
        f"{season}-{str(int(season) + 1)[-2:]}"
        if team_entity.sport == 'basketball' and '-' not in season
        else season
    )
    api_season = int(str(season).split('-', 1)[0])
 
    # 1 — try DB first (only return from DB if played > 0)
    stats = EntityStats.objects.filter(entity=team_entity, season=stats_season).first()
    has_valid_db_stats = (
        stats and stats.stats_data and 
        (stats.stats_data.get('played', 0) > 0 or stats.stats_data.get('matches_played', 0) > 0)
    )

    if not team_entity.logo_url or 'api-sports' in team_entity.logo_url:
        _fetch_soccer_team_stats_thesportsdb(team_entity)
        team_entity.refresh_from_db()

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
        stats_data = _fetch_soccer_team_stats_statpal(team_entity.external_id, api_season)
        if not stats_data:
            stats_data = _fetch_soccer_team_stats_thesportsdb(team_entity)
        if not stats_data and team_entity.api_source == 'api_sports':
            stats_data = _fetch_soccer_team_stats(team_entity.external_id, api_season)
 
    elif team_entity.sport == 'basketball':
        # Always use StatPal standings (balldontlie is no longer in use)
        stats_data = _fetch_nba_team_stats_statpal(team_entity.external_id, api_season)
 
    elif team_entity.sport == 'football':
        stats_data = _fetch_nfl_team_stats(team_entity.external_id, api_season)
 
    elif team_entity.sport == 'hockey':
        stats_data = _fetch_nhl_team_stats(team_entity.name, api_season)
 
    elif team_entity.sport == 'baseball':
        stats_data = _fetch_mlb_team_stats(team_entity.external_id, api_season)
 
    elif team_entity.sport == 'cricket':
        stats_data = _fetch_cricket_team_stats(team_entity.external_id, api_season)
        if not stats_data:
            stats_data = _fetch_soccer_team_stats_thesportsdb(team_entity)
 
    # tennis / golf / mma / f1 have no team-standings API — return empty gracefully
 
    # Fallback to local DB Event calculation if live APIs return empty
    if not stats_data:
        stats_data = _fetch_stats_from_db_events(team_entity)

    # Standardize output keys across all sports
    stats_data = _normalize_team_stats(stats_data, team_entity=team_entity)

    # 3 — save to DB so next call is instant
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
    Returns the official primary league standings for clubs or national rankings for national teams.
    """
    entity = get_object_or_404(Entity, id=team_id)
    entity = entity.canonical_entity or entity

    if entity.type == 'league':
        season = request.GET.get('season') or str(_current_season(entity.sport))
        return _get_standings_for_league(request, entity, season)

    team_entity = entity
    season = request.GET.get('season') or str(_current_season(team_entity.sport))

    # 1. Check if Cricket National Team -> ICC World Rankings
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

    # 2. Check if Soccer National Team -> FIFA World Rankings
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

    # 3. For Club Teams -> Primary Official League Standings Lookup via TheSportsDB
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
    force_refresh = request.GET.get('force_refresh', '').lower() in ('true', '1')

    # 1 — try DB first (unless force_refresh is requested)
    if not force_refresh:
        stats = EntityStats.objects.filter(entity=athlete_entity, season=season).first()
        if stats and stats.stats_data:
            non_empty_count = sum(1 for v in stats.stats_data.values() if bool(v))
            if non_empty_count >= 3:
                return Response({
                    'athlete': EntitySerializer(athlete_entity, context={'request': request}).data,
                    'season':  season,
                    'stats':   stats.stats_data,
                    'source':  'db',
                })

    # 2 — live API fallback & multi-source data merging
    stats_data = {}

    # A) Try TheSportsDB first for profile bio, stats, images, and attributes
    if athlete_entity.name:
        tsdb_stats = _fetch_thesportsdb_player_stats(athlete_entity.name, athlete_entity=athlete_entity, force_refresh=force_refresh)
        if tsdb_stats:
            stats_data.update(tsdb_stats)

    # B) Try API-Football performance stats if external_id is available and needed
    if not stats_data and athlete_entity.external_id and athlete_entity.sport == 'soccer':
        soccer_stats = _fetch_soccer_player_stats(athlete_entity.external_id, season)
        if soccer_stats:
            stats_data.update(soccer_stats)

    # C) Enrich remaining empty fields with local Athlete DB details if available
    ad = getattr(athlete_entity, 'athlete_details', None)
    if ad:
        if not stats_data.get('position') and ad.position:
            stats_data['position'] = ad.position
        if not stats_data.get('nationality') and ad.nationality:
            stats_data['nationality'] = ad.nationality
        if not stats_data.get('height') and ad.height_cm:
            stats_data['height'] = f"{ad.height_cm} cm"
        if not stats_data.get('weight') and ad.weight_kg:
            stats_data['weight'] = f"{ad.weight_kg} kg"
        if not stats_data.get('date_of_birth') and ad.date_of_birth:
            stats_data['date_of_birth'] = str(ad.date_of_birth)
        if not stats_data.get('team') and ad.current_team:
            stats_data['team'] = ad.current_team.name

    # 3 — save combined stats to DB
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


def _fetch_thesportsdb_player_stats(player_name, athlete_entity=None, force_refresh=False):
    cache_key = f'player_stats:thesportsdb:{player_name.lower().strip()}'
    if force_refresh:
        cache.delete(cache_key)

    cached = cache.get(cache_key)
    if cached and not force_refresh:
        if athlete_entity:
            try:
                ad = getattr(athlete_entity, 'athlete_details', None)
                if ad:
                    if not cached.get('position') and ad.position:
                        cached['position'] = ad.position
                    if not cached.get('nationality') and ad.nationality:
                        cached['nationality'] = ad.nationality
                    if not cached.get('height') and ad.height_cm:
                        cached['height'] = f"{ad.height_cm} cm"
                    if not cached.get('weight') and ad.weight_kg:
                        cached['weight'] = f"{ad.weight_kg} kg"
                    if not cached.get('date_of_birth') and ad.date_of_birth:
                        cached['date_of_birth'] = str(ad.date_of_birth)
                    if not cached.get('team') and ad.current_team:
                        cached['team'] = ad.current_team.name
                if not cached.get('description') and athlete_entity.description:
                    cached['description'] = athlete_entity.description
                if not cached.get('headshot_url') and athlete_entity.logo_url:
                    cached['headshot_url'] = athlete_entity.logo_url
            except Exception:
                pass
        return cached

    try:
        from apps.sports_apis.services.thesportsdb import thesportsdb_service
        player_info = thesportsdb_service.get_player_details(player_name) or {}

        raw = player_info.get('raw_data', {})
        pos = player_info.get('position', '')
        nat = player_info.get('nationality', '')
        h = player_info.get('height', '')
        w = player_info.get('weight', '')
        team = player_info.get('team_name', '')
        dob = player_info.get('date_of_birth', '')
        desc = player_info.get('description', '')
        headshot = player_info.get('headshot_url', '')

        # Enrich empty fields with local Athlete DB details if available
        if athlete_entity:
            try:
                ad = getattr(athlete_entity, 'athlete_details', None)
                if ad:
                    if not pos and ad.position:
                        pos = ad.position
                    if not nat and ad.nationality:
                        nat = ad.nationality
                    if not h and ad.height_cm:
                        h = f"{ad.height_cm} cm"
                    if not w and ad.weight_kg:
                        w = f"{ad.weight_kg} kg"
                    if not dob and ad.date_of_birth:
                        dob = str(ad.date_of_birth)
                    if not team and ad.current_team:
                        team = ad.current_team.name
                if not desc and athlete_entity.description:
                    desc = athlete_entity.description
                if not headshot and athlete_entity.logo_url:
                    headshot = athlete_entity.logo_url

                # Permanently persist fetched static fields into DB models (Entity & Athlete)
                entity_fields_to_save = []
                if desc and not athlete_entity.description:
                    athlete_entity.description = desc
                    entity_fields_to_save.append('description')
                if headshot and not athlete_entity.logo_url:
                    athlete_entity.logo_url = headshot
                    entity_fields_to_save.append('logo_url')
                if entity_fields_to_save:
                    athlete_entity.save(update_fields=entity_fields_to_save)

                from apps.entity.models import Athlete
                athlete_detail_obj, _ = Athlete.objects.get_or_create(
                    entity=athlete_entity,
                    defaults={'first_name': athlete_entity.name.split(' ')[0], 'last_name': ' '.join(athlete_entity.name.split(' ')[1:])}
                )
                ad_updated = False
                if pos and not athlete_detail_obj.position:
                    athlete_detail_obj.position = pos
                    ad_updated = True
                if nat and not athlete_detail_obj.nationality:
                    athlete_detail_obj.nationality = nat
                    ad_updated = True
                if dob and not athlete_detail_obj.date_of_birth:
                    try:
                        from datetime import datetime
                        athlete_detail_obj.date_of_birth = datetime.strptime(dob, '%Y-%m-%d').date()
                        ad_updated = True
                    except Exception:
                        pass
                if h and not athlete_detail_obj.height_cm:
                    try:
                        import re
                        m = re.search(r'\d+', str(h))
                        if m:
                            athlete_detail_obj.height_cm = int(m.group(0))
                            ad_updated = True
                    except Exception:
                        pass
                if w and not athlete_detail_obj.weight_kg:
                    try:
                        import re
                        m = re.search(r'\d+', str(w))
                        if m:
                            athlete_detail_obj.weight_kg = int(m.group(0))
                            ad_updated = True
                    except Exception:
                        pass
                if ad_updated:
                    athlete_detail_obj.save()
            except Exception as err:
                logger.debug(f"Failed to persist athlete_details for {athlete_entity.name}: {err}")

        stats_data = {
            'position': pos,
            'nationality': nat,
            'height': h,
            'weight': w,
            'team': team,
            'date_of_birth': dob,
            'birth_location': raw.get('strBirthLocation', '') or '',
            'number': raw.get('strNumber', '') or '',
            'side': raw.get('strSide', '') or '',
            'status': raw.get('strStatus', '') or '',
            'outfitter': raw.get('strOutfitter', '') or '',
            'agent': raw.get('strAgent', '') or '',
            'date_signed': raw.get('dateSigned', '') or '',
            'description': desc,
            'headshot_url': headshot,
            'signing_fee': raw.get('strSigning', '') or '',
            'wage': raw.get('strWage', '') or '',
            'kit': raw.get('strKit', '') or raw.get('strNumber', '') or '',
        }
        if any(stats_data.values()):
            cache.set(cache_key, stats_data, timeout=86400)
        return stats_data
    except Exception as e:
        logger.warning(f"TheSportsDB player stats lookup failed for '{player_name}': {e}")
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
 
    nationality = athlete.nationality or athlete_entity.country or ''
    bio = athlete_entity.description or ''
    photo = athlete_entity.logo_url or ''

    # Enrich missing fields from TheSportsDB if needed
    if not (athlete.nationality and bio and photo):
        try:
            from apps.sports_apis.services.thesportsdb import thesportsdb_service
            tsdb_info = thesportsdb_service.get_player_details(athlete_entity.name) or {}
            if tsdb_info:
                if tsdb_info.get('nationality'):
                    nationality = tsdb_info.get('nationality')
                    athlete.nationality = nationality
                    athlete.save(update_fields=['nationality'])
                if not bio and tsdb_info.get('description'):
                    bio = tsdb_info.get('description')
                    athlete_entity.description = bio
                    athlete_entity.save(update_fields=['description'])
                if not photo and tsdb_info.get('headshot_url'):
                    photo = tsdb_info.get('headshot_url')
                    athlete_entity.logo_url = photo
                    athlete_entity.save(update_fields=['logo_url'])
        except Exception:
            pass

    return Response({
        'id':                     athlete_entity.id,
        'name':                   f"{athlete.first_name} {athlete.last_name}".strip() or athlete_entity.name,
        'photo':                  athlete_entity.logo_url or '',
        'date_of_birth':          athlete.date_of_birth,
        'age':                    athlete.age,
        'nationality':            nationality,
        'height_cm':              athlete.height_cm,
        'weight_kg':              athlete.weight_kg,
        'current_team':           EntitySerializer(athlete.current_team, context={'request': request}).data if athlete.current_team else None,
        'position':               athlete.position,
        'jersey_number':          athlete.jersey_number,
        'twitter':                athlete.twitter_handle or '',
        'instagram':              athlete.instagram_handle or '',
        'bio':                    bio,
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


def _get_standings_for_league(request, league_entity, season, highlight_team_id=None, highlight_team_name=None):
    """
    Shared logic used by both get_league_standings and get_team_standings.
    DB first → live API fallback → write back to DB.
    """
    # Resolve canonical league safely
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
    has_db_data = False

    def _safe_league_data(ent, req):
        """Return league data safe for serialization — handles pk-less in-memory Entity objects."""
        if ent and getattr(ent, 'pk', None):
            return EntitySerializer(ent, context={'request': req}).data
        return {
            'id': None,
            'name': getattr(ent, 'name', ''),
            'external_id': getattr(ent, 'external_id', ''),
            'sport': getattr(ent, 'sport', ''),
            'type': 'league',
            'logo_url': getattr(ent, 'logo_url', '') if hasattr(ent, 'logo_url') else '',
        }

    from django.utils import timezone

    for team in teams_in_league:
        stats = EntityStats.objects.filter(
            entity=team.entity, season=str(season), stat_type='season'
        ).first()
        if stats and stats.stats_data.get('rank'):
            # Only consider DB data valid if updated within the last 24 hours (86,400s)
            if stats.updated_at and (timezone.now() - stats.updated_at).total_seconds() < 86400:
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
            'is_highlighted': str(team.entity.external_id) == str(highlight_team_id) or str(team.entity.id) == str(highlight_team_id),
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
            'league':    _safe_league_data(league_entity, request),
            'season':    season,
            'standings': standings,
            'source':    'db',
        })

    # Live API fallback — try TheSportsDB first, then API-Sports
    live_standings = _fetch_league_standings_thesportsdb(canonical, season)

    if not live_standings and getattr(canonical, 'api_source', '') == 'api_sports':
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

    # Nothing available
    return Response({
        'league':    _safe_league_data(league_entity, request),
        'season':    season,
        'standings': standings,
        'source':    'empty',
    })


def _fetch_league_standings_thesportsdb(league_entity, season):
    """Fallback: Search league on TheSportsDB API and fetch lookup table standings."""
    try:
        import requests
        league_name = league_entity.name if hasattr(league_entity, 'name') else str(league_entity)
        league_id = None

        if getattr(league_entity, 'api_source', '') == 'thesportsdb':
            league_id = league_entity.external_id

        if not league_id:
            res = requests.get('https://www.thesportsdb.com/api/v1/json/3/all_leagues.php', timeout=5)
            if res.status_code == 200:
                leagues = res.json().get('leagues') or []
                for l in leagues:
                    str_lg = str(l.get('strLeague', '')).lower()
                    lg_lower = league_name.lower()
                    if lg_lower in str_lg or str_lg in lg_lower:
                        league_id = l.get('idLeague')
                        break

        if league_id:
            try:
                s_year = int(str(season).split('-', 1)[0].split('/', 1)[0])
            except Exception:
                s_year = 2026

            # Use TheSportsDBService to benefit from premium API key
            from apps.sports_apis.services.thesportsdb import TheSportsDBService
            tsdb = TheSportsDBService()

            # Try current year, then walk back up to 2 seasons to find a full table
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
    except Exception:
        pass

    return []


def _fetch_team_fixtures_live(team_entity):
    """Fallback to live provider API (TheSportsDB) for team fixtures when DB has 0 events."""
    try:
        from apps.sports_apis.services.thesportsdb import TheSportsDBService
        tsdb = TheSportsDBService()
        team_info = tsdb.search_team(team_entity.name)
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
                    'venue_name': ev.get('strVenue', '') or '',
                    'venue_city': ev.get('strCity', '') or '',
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
        return fixtures
    except Exception as e:
        logger.error(f"Error in _fetch_team_fixtures_live: {str(e)}")
        return []


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
