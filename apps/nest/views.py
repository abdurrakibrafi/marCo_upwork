from django.shortcuts import render

# Create your views here.
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from apps.nest.models import UserNest, UserPreferences, RecentSearch
from apps.entity.models import Entity
from apps.nest.serializers import (
    UserNestSerializer, AddToNestSerializer,
    UserPreferencesSerializer, RecentSearchSerializer
)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_user_nest(request):
    """
    Get user's nest
    GET /api/nest
    """
    nest_items = UserNest.objects.filter(user=request.user).select_related('entity')
    
    # Paginate for 360° view (15 at a time)
    offset = int(request.GET.get('offset', 0))
    limit = int(request.GET.get('limit', 15))
    
    total_count = nest_items.count()
    paginated_items = nest_items[offset:offset + limit]
    
    serializer = UserNestSerializer(paginated_items, many=True)
    
    return Response({
        'total_count': total_count,
        'offset': offset,
        'limit': limit,
        'has_more': (offset + limit) < total_count,
        'entities': serializer.data
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def add_to_nest(request):
    """
    Add entity to user's nest
    POST /api/nest/add
    Body: {"entity_id": 123}
    """
    serializer = AddToNestSerializer(data=request.data)
    
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    entity_id = serializer.validated_data['entity_id']
    entity = get_object_or_404(Entity, id=entity_id)
    
    # Check if already in nest
    if UserNest.objects.filter(user=request.user, entity=entity).exists():
        return Response(
            {'error': 'Entity already in your nest'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Get next position
    last_position = UserNest.objects.filter(user=request.user).count()
    
    # Create nest item
    nest_item = UserNest.objects.create(
        user=request.user,
        entity=entity,
        position=last_position
    )
    
    # Update entity follower count
    entity.follower_count += 1
    entity.save(update_fields=['follower_count'])

    # Trigger feed discovery/polling for this entity
    from apps.feed.tasks import update_all_entity_feeds
    update_all_entity_feeds.delay(entity.id)

    # Mark onboarding as complete (if applicable)
    if hasattr(request.user, 'profile'):
        request.user.profile.onboarding_completed = True
        request.user.profile.save(update_fields=['onboarding_completed'])

    return Response({
        'success': True,
        'message': f'{entity.name} added to your nest',
        'nest_item': UserNestSerializer(nest_item).data
    }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def remove_from_nest(request):
    """
    Remove entity from user's nest
    POST /api/nest/remove
    Body: {"entity_id": 123}
    """
    entity_id = request.data.get('entity_id')
    
    if not entity_id:
        return Response(
            {'error': 'entity_id is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    nest_item = get_object_or_404(UserNest, user=request.user, entity_id=entity_id)
    entity = nest_item.entity
    
    # Delete nest item
    nest_item.delete()
    
    # Update entity follower count
    if entity.follower_count > 0:
        entity.follower_count -= 1
        entity.save(update_fields=['follower_count'])
    
    # Reorder positions
    remaining_items = UserNest.objects.filter(user=request.user).order_by('position')
    for idx, item in enumerate(remaining_items):
        item.position = idx
        item.save(update_fields=['position'])
    
    return Response({
        'success': True,
        'message': f'{entity.name} removed from your nest',
        'nest_count': remaining_items.count()
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_nest_summary(request):
    """
    Get summary of user's nest
    GET /api/nest/summary
    """
    nest_items = UserNest.objects.filter(user=request.user).select_related('entity')
    
    teams = [item for item in nest_items if item.entity.type == 'team']
    athletes = [item for item in nest_items if item.entity.type == 'athlete']
    leagues = [item for item in nest_items if item.entity.type == 'league']
    
    return Response({
        'total_count': nest_items.count(),
        'teams_count': len(teams),
        'athletes_count': len(athletes),
        'leagues_count': len(leagues),
        'entities': UserNestSerializer(nest_items, many=True).data
    })


@api_view(['GET', 'PUT'])
@permission_classes([IsAuthenticated])
def user_preferences(request):
    """
    Get or update user preferences
    GET /api/user/preferences
    PUT /api/user/preferences
    """
    preferences, created = UserPreferences.objects.get_or_create(user=request.user)
    
    if request.method == 'GET':
        serializer = UserPreferencesSerializer(preferences)
        return Response(serializer.data)
    
    elif request.method == 'PUT':
        serializer = UserPreferencesSerializer(preferences, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def recent_searches(request):
    """
    Get user's recent searches
    GET /api/search/recent
    """
    searches = RecentSearch.objects.filter(user=request.user)[:10]
    serializer = RecentSearchSerializer(searches, many=True)
    return Response(serializer.data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def save_search(request):
    """
    Save a search query
    POST /api/search/save
    Body: {"query": "Lakers", "entity_id": 123}
    """
    query = request.data.get('query')
    entity_id = request.data.get('entity_id')
    
    if not query:
        return Response(
            {'error': 'query is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    entity = None
    if entity_id:
        entity = get_object_or_404(Entity, id=entity_id)
    
    # Create search record
    search = RecentSearch.objects.create(
        user=request.user,
        query=query,
        entity=entity
    )
    
    return Response({
        'success': True,
        'search': RecentSearchSerializer(search).data
    }, status=status.HTTP_201_CREATED)