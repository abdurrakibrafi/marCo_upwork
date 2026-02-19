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
from .services import EntitySearchService

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