
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
    if entity.type == 'team':
        try:
            serializer = TeamDetailSerializer(entity.team_details, context={'request': request})
        except Team.DoesNotExist:
            serializer = EntitySerializer(entity, context={'request': request})
    elif entity.type == 'athlete':
        try:
            serializer = AthleteDetailSerializer(entity.athlete_details, context={'request': request})
        except Athlete.DoesNotExist:
            serializer = EntitySerializer(entity, context={'request': request})
    elif entity.type == 'league':
        try:
            serializer = LeagueDetailSerializer(entity.league_details, context={'request': request})
        except League.DoesNotExist:
            serializer = EntitySerializer(entity, context={'request': request})
    else:
        serializer = EntitySerializer(entity, context={'request': request})
    return Response(serializer.data)
 
 
@api_view(['GET'])
@permission_classes([AllowAny])
def get_entity_by_slug(request, slug):
    entity = get_object_or_404(Entity, slug=slug)
    if entity.type == 'team':
        try:
            serializer = TeamDetailSerializer(entity.team_details, context={'request': request})
        except Team.DoesNotExist:
            serializer = EntitySerializer(entity, context={'request': request})
    elif entity.type == 'athlete':
        try:
            serializer = AthleteDetailSerializer(entity.athlete_details, context={'request': request})
        except Athlete.DoesNotExist:
            serializer = EntitySerializer(entity, context={'request': request})
    elif entity.type == 'league':
        try:
            serializer = LeagueDetailSerializer(entity.league_details, context={'request': request})
        except League.DoesNotExist:
            serializer = EntitySerializer(entity, context={'request': request})
    else:
        serializer = EntitySerializer(entity, context={'request': request})
    return Response(serializer.data)
 
 
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
 
    if team_entity.sport == 'soccer' and team_entity.api_source == 'api_sports':
        stats_data = _fetch_soccer_team_stats(team_entity.external_id, int(season))
 
    elif team_entity.sport == 'basketball' and team_entity.api_source == 'balldontlie':
        stats_data = _fetch_nba_team_stats(team_entity.external_id, int(season))
 
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
 
 
# ─────────────────────────────────────────────────────────────────────────────
# TEAM ROSTER  (unchanged — reads from Athlete table which is already seeded)
# ─────────────────────────────────────────────────────────────────────────────
 
@api_view(['GET'])
@permission_classes([AllowAny])
def get_team_roster(request, team_id):
    team_entity = get_object_or_404(Entity, id=team_id, type='team')
 
    athletes = Athlete.objects.filter(
        current_team__api_source=team_entity.api_source,
        current_team__external_id=team_entity.external_id,
    ).select_related('entity')
 
    if not athletes.exists():
        athletes = Athlete.objects.filter(current_team=team_entity).select_related('entity')
 
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
    season = request.GET.get('season') or str(_current_season(team_entity.sport))
 
    try:
        league = team_entity.team_details.league
    except Exception:
        league = None
 
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
            'rank':      stats.stats_data.get('rank', 0) if stats else 0,
            'team_id':   team.entity.id,
            'team_name': team.entity.name,
            'logo':      team.entity.logo_url,
            'country':   team.entity.country,
            'points':    stats.stats_data.get('points', 0) if stats else 0,
            'played':    stats.stats_data.get('played', 0) if stats else 0,
            'wins':      stats.stats_data.get('win', team.total_wins) if stats else team.total_wins,
            'draws':     stats.stats_data.get('draw', 0) if stats else 0,
            'losses':    stats.stats_data.get('lose', team.total_losses) if stats else team.total_losses,
            'goals_for': stats.stats_data.get('goals_for', 0) if stats else 0,
            'goals_against': stats.stats_data.get('goals_against', 0) if stats else 0,
            'goal_diff': stats.stats_data.get('goal_diff', 0) if stats else 0,
            'form':      stats.stats_data.get('form', '') if stats else '',
            'is_highlighted': str(team.entity.external_id) == str(highlight_team_id),
        })
 
    if has_db_data:
        standings.sort(key=lambda x: x['rank'] if x['rank'] > 0 else 999)
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
            live_response.sort(key=lambda x: x['rank'])
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

    events = Event.objects.filter(
        Q(home_entity=team_entity) | Q(away_entity=team_entity)
    ).select_related(
        'home_entity', 'away_entity', 'league'
    ).order_by('-start_time')[:50]

    return Response({
        'team': EntitySerializer(team_entity, context={'request': request}).data,
        'fixtures_count': events.count(),
        'fixtures': EvSerializer(events, many=True).data,
    })