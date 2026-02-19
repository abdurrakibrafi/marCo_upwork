from django.shortcuts import render

# Create your views here.
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from apps.entity.models import Entity, Team, Athlete, League
from apps.entity.serializers import (
    EntitySerializer, TeamDetailSerializer,
    AthleteDetailSerializer, LeagueDetailSerializer
)
from apps.entity.models import EntityStats
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from apps.entity.services import EntitySearchService

@api_view(['GET'])
@permission_classes([AllowAny])
def search_entities(request):
    """
    Global search for entities
    GET /api/entities/search?q=lakers&type=team&sport=basketball
    """
    query = request.GET.get('q', '')
    entity_type = request.GET.get('type')  # team, athlete, league
    sport = request.GET.get('sport')
    
    if not query:
        return Response(
            {'error': 'Query parameter "q" is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    results = EntitySearchService.search(query, entity_type, sport)
    serializer = EntitySerializer(results, many=True, context={'request': request})
    
    return Response({
        'query': query,
        'count': len(results),
        'results': serializer.data
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_trending(request):
    """
    Get trending entities
    GET /api/entities/trending
    """
    entities = EntitySearchService.get_trending()
    
    # Group by type
    teams = [e for e in entities if e.type == 'team']
    athletes = [e for e in entities if e.type == 'athlete']
    leagues = [e for e in entities if e.type == 'league']
    
    return Response({
        'teams': EntitySerializer(teams, many=True, context={'request': request}).data,
        'athletes': EntitySerializer(athletes, many=True, context={'request': request}).data,
        'leagues': EntitySerializer(leagues, many=True, context={'request': request}).data,
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_entity_detail(request, entity_id):
    """
    Get entity details by ID
    GET /api/entities/{id}
    """
    entity = get_object_or_404(Entity, id=entity_id)
    
    # Return type-specific serializer
    if entity.type == 'team':
        try:
            team = entity.team_details
            serializer = TeamDetailSerializer(team, context={'request': request})
        except Team.DoesNotExist:
            serializer = EntitySerializer(entity, context={'request': request})
    
    elif entity.type == 'athlete':
        try:
            athlete = entity.athlete_details
            serializer = AthleteDetailSerializer(athlete, context={'request': request})
        except Athlete.DoesNotExist:
            serializer = EntitySerializer(entity, context={'request': request})
    
    elif entity.type == 'league':
        try:
            league = entity.league_details
            serializer = LeagueDetailSerializer(league, context={'request': request})
        except League.DoesNotExist:
            serializer = EntitySerializer(entity, context={'request': request})
    
    else:
        serializer = EntitySerializer(entity, context={'request': request})
    
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_entity_by_slug(request, slug):
    """
    Get entity by slug
    GET /api/entities/slug/{slug}
    """
    entity = get_object_or_404(Entity, slug=slug)
    
    if entity.type == 'team':
        try:
            team = entity.team_details
            serializer = TeamDetailSerializer(team, context={'request': request})
        except Team.DoesNotExist:
            serializer = EntitySerializer(entity, context={'request': request})
    elif entity.type == 'athlete':
        try:
            athlete = entity.athlete_details
            serializer = AthleteDetailSerializer(athlete, context={'request': request})
        except Athlete.DoesNotExist:
            serializer = EntitySerializer(entity, context={'request': request})
    elif entity.type == 'league':
        try:
            league = entity.league_details
            serializer = LeagueDetailSerializer(league, context={'request': request})
        except League.DoesNotExist:
            serializer = EntitySerializer(entity, context={'request': request})
    else:
        serializer = EntitySerializer(entity, context={'request': request})
    
    return Response(serializer.data)




@api_view(['GET'])
@permission_classes([AllowAny])
def get_team_stats(request, team_id):
    """
    Get team statistics
    GET /api/entities/team/{team_id}/stats?season=2023
    """
    team_entity = get_object_or_404(Entity, id=team_id, type='team')
    season = request.GET.get('season', '2023-24')
    
    # Get stats from EntityStats model
    stats = EntityStats.objects.filter(
        entity=team_entity,
        season=season
    ).first()
    
    if stats:
        return Response({
            'team': EntitySerializer(team_entity, context={'request': request}).data,
            'season': season,
            'stats': stats.stats_data
        })
    else:
        # Trigger stats update
        from .tasks import update_nba_team_stats, update_soccer_team_stats
        
        if team_entity.sport == 'basketball':
            update_nba_team_stats.delay(team_id)
        elif team_entity.sport == 'soccer':
            update_soccer_team_stats.delay(team_id)
        
        return Response({
            'team': EntitySerializer(team_entity, context={'request': request}).data,
            'season': season,
            'stats': {},
            'message': 'Stats update triggered'
        })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_team_roster(request, team_id):
    """
    Get team roster
    GET /api/entities/team/{team_id}/roster
    """
    team_entity = get_object_or_404(Entity, id=team_id, type='team')
    
    # Get athletes on this team
    athletes = Athlete.objects.filter(
        current_team=team_entity
    ).select_related('entity')
    
    athlete_data = []
    for athlete in athletes:
        athlete_data.append({
            'id': athlete.entity.id,
            'name': f"{athlete.first_name} {athlete.last_name}",
            'position': athlete.position,
            'jersey_number': athlete.jersey_number,
            'photo': athlete.entity.logo_url,
            'height_cm': athlete.height_cm,
            'weight_kg': athlete.weight_kg,
            'salary_usd': float(athlete.salary_usd) if athlete.salary_usd else None,
            'contract_years': athlete.contract_years_remaining,
        })
    
    return Response({
        'team': EntitySerializer(team_entity, context={'request': request}).data,
        'roster_count': len(athlete_data),
        'roster': athlete_data
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_team_standings(request, team_id):
    """
    Get team's league standings
    GET /api/entities/team/{team_id}/standings
    """
    team_entity = get_object_or_404(Entity, id=team_id, type='team')
    
    if not team_entity.team_details.league:
        return Response({
            'error': 'Team does not have a league assigned'
        }, status=404)
    
    league = team_entity.team_details.league
    season = request.GET.get('season', '2023')
    
    # Get all teams in the league with stats
    teams_in_league = Team.objects.filter(
        league=league
    ).select_related('entity')
    
    standings_data = []
    for team in teams_in_league:
        stats = EntityStats.objects.filter(
            entity=team.entity,
            season=season,
            stat_type='season'
        ).first()
        
        standings_data.append({
            'team_id': team.entity.id,
            'team_name': team.entity.name,
            'logo': team.entity.logo_url,
            'wins': team.total_wins,
            'losses': team.total_losses,
            'win_pct': float(team.win_percentage),
            'rank': stats.stats_data.get('rank', 0) if stats else 0,
            'stats': stats.stats_data if stats else {}
        })
    
    # Sort by rank or win percentage
    standings_data.sort(key=lambda x: (-x['win_pct'], -x['wins']))
    
    return Response({
        'league': EntitySerializer(league, context={'request': request}).data,
        'season': season,
        'standings': standings_data
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_athlete_stats(request, athlete_id):
    """
    Get athlete statistics
    GET /api/entities/athlete/{athlete_id}/stats?season=2023
    """
    athlete_entity = get_object_or_404(Entity, id=athlete_id, type='athlete')
    season = request.GET.get('season', '2023')
    
    # Get stats
    stats = EntityStats.objects.filter(
        entity=athlete_entity,
        season=season
    ).first()
    
    if stats:
        return Response({
            'athlete': EntitySerializer(athlete_entity, context={'request': request}).data,
            'season': season,
            'stats': stats.stats_data
        })
    else:
        return Response({
            'athlete': EntitySerializer(athlete_entity, context={'request': request}).data,
            'season': season,
            'stats': {},
            'message': 'Stats not available'
        })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_athlete_bio(request, athlete_id):
    """
    Get athlete biography
    GET /api/entities/athlete/{athlete_id}/bio
    """
    athlete_entity = get_object_or_404(Entity, id=athlete_id, type='athlete')
    
    try:
        athlete = athlete_entity.athlete_details
    except Athlete.DoesNotExist:
        return Response({'error': 'Athlete details not found'}, status=404)
    
    return Response({
        'id': athlete_entity.id,
        'name': f"{athlete.first_name} {athlete.last_name}",
        'photo': athlete_entity.logo_url,
        'date_of_birth': athlete.date_of_birth,
        'age': athlete.age,
        'nationality': athlete.nationality,
        'height_cm': athlete.height_cm,
        'weight_kg': athlete.weight_kg,
        'current_team': EntitySerializer(athlete.current_team, context={'request': request}).data if athlete.current_team else None,
        'position': athlete.position,
        'jersey_number': athlete.jersey_number,
        'salary_usd': float(athlete.salary_usd) if athlete.salary_usd else None,
        'contract_years_remaining': athlete.contract_years_remaining,
        'twitter': athlete.twitter_handle,
        'instagram': athlete.instagram_handle,
        'bio': athlete_entity.description,
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_league_leaders(request, league_id):
    """
    Get league leaders (top performers)
    GET /api/entities/league/{league_id}/leaders?season=2023&stat=points
    """
    league_entity = get_object_or_404(Entity, id=league_id, type='league')
    season = request.GET.get('season', '2023')
    stat_type = request.GET.get('stat', 'points')
    
    # Get all athletes in this league's teams
    teams_in_league = Team.objects.filter(league=league_entity)
    athletes = Athlete.objects.filter(
        current_team__in=[t.entity for t in teams_in_league]
    ).select_related('entity', 'current_team')
    
    leaders_data = []
    for athlete in athletes:
        stats = EntityStats.objects.filter(
            entity=athlete.entity,
            season=season,
            stat_type='season'
        ).first()
        
        if stats and stat_type in stats.stats_data:
            leaders_data.append({
                'athlete_id': athlete.entity.id,
                'name': f"{athlete.first_name} {athlete.last_name}",
                'photo': athlete.entity.logo_url,
                'team': athlete.current_team.name if athlete.current_team else '',
                'team_logo': athlete.current_team.logo_url if athlete.current_team else '',
                stat_type: stats.stats_data.get(stat_type, 0)
            })
    
    # Sort by stat value
    leaders_data.sort(key=lambda x: x.get(stat_type, 0), reverse=True)
    
    return Response({
        'league': EntitySerializer(league_entity, context={'request': request}).data,
        'season': season,
        'stat_type': stat_type,
        'leaders': leaders_data[:20]  # Top 20
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_league_standings(request, league_id):
    """
    Get league standings
    GET /api/entities/league/{league_id}/standings?season=2023
    """
    league_entity = get_object_or_404(Entity, id=league_id, type='league')
    season = request.GET.get('season', '2023')
    
    # Get all teams in league
    teams_in_league = Team.objects.filter(
        league=league_entity
    ).select_related('entity')
    
    standings = []
    for team in teams_in_league:
        stats = EntityStats.objects.filter(
            entity=team.entity,
            season=season,
            stat_type='season'
        ).first()
        
        standings.append({
            'rank': stats.stats_data.get('rank', 0) if stats else 0,
            'team_id': team.entity.id,
            'team_name': team.entity.name,
            'logo': team.entity.logo_url,
            'wins': team.total_wins,
            'losses': team.total_losses,
            'win_pct': float(team.win_percentage),
            'stats': stats.stats_data if stats else {}
        })
    
    # Sort by rank
    standings.sort(key=lambda x: x['rank'] if x['rank'] > 0 else 999)
    
    return Response({
        'league': EntitySerializer(league_entity, context={'request': request}).data,
        'season': season,
        'standings': standings
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_league_fixtures(request, league_id):
    """
    Get league fixtures
    GET /api/entities/league/{league_id}/fixtures
    """
    from apps.event.models import Event
    from apps.event.serializers import EventSerializer
    
    league_entity = get_object_or_404(Entity, id=league_id, type='league')
    
    # Get upcoming and recent events
    events = Event.objects.filter(
        league=league_entity
    ).select_related('home_entity', 'away_entity').order_by('-start_time')[:50]
    
    serializer = EventSerializer(events, many=True)
    
    return Response({
        'league': EntitySerializer(league_entity, context={'request': request}).data,
        'fixtures': serializer.data
    })