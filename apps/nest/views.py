from django.shortcuts import render
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
    """Retrieve all entities followed by the authenticated user in their 360-degree Nest.

    Supports offset-limit windowing for dial navigation.

    Args:
        request: HTTP request with optional offset and limit query parameters.

    Returns:
        Response: Serialized nest entities with pagination metadata.
    """
    nest_items = UserNest.objects.filter(user=request.user).select_related('entity')
    
    # Paginate for 360° view (15 at a time)
    offset = int(request.GET.get('offset', 0))
    limit = int(request.GET.get('limit', 15))
    
    total_count = nest_items.count()
    paginated_items = nest_items[offset:offset + limit]
    
    user_nest_ids = set(item.entity_id for item in paginated_items)
    serializer = UserNestSerializer(paginated_items, many=True, context={'request': request, 'user_nest_entity_ids': user_nest_ids})
    
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
    """Add a sports entity (team, athlete, or league) to the authenticated user's Nest.

    Increments entity follower counts and triggers asynchronous background news feed ingestion.

    Args:
        request: HTTP request with payload {"entity_id": 123}.

    Returns:
        Response: Created nest item object and confirmation.
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
    """Remove an entity from the authenticated user's Nest and re-index remaining positions.

    Args:
        request: HTTP request with payload {"entity_id": 123}.

    Returns:
        Response: Removal confirmation and updated nest count.
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
    remaining_items = list(UserNest.objects.filter(user=request.user).order_by('position'))
    for idx, item in enumerate(remaining_items):
        item.position = idx
    UserNest.objects.bulk_update(remaining_items, ['position'])
    
    return Response({
        'success': True,
        'message': f'{entity.name} removed from your nest',
        'nest_count': len(remaining_items)
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_nest_summary(request):
    """Retrieve summary counts of teams, athletes, and leagues configured in the user's Nest.

    Args:
        request: HTTP request.

    Returns:
        Response: Categorized counts and serialized entity records.
    """
    nest_items = UserNest.objects.filter(user=request.user).select_related('entity')
    
    teams = [item for item in nest_items if item.entity.type == 'team']
    athletes = [item for item in nest_items if item.entity.type == 'athlete']
    leagues = [item for item in nest_items if item.entity.type == 'league']
    
    user_nest_ids = set(item.entity_id for item in nest_items)
    return Response({
        'total_count': nest_items.count(),
        'teams_count': len(teams),
        'athletes_count': len(athletes),
        'leagues_count': len(leagues),
        'entities': UserNestSerializer(nest_items, many=True, context={'request': request, 'user_nest_entity_ids': user_nest_ids}).data
    })


@api_view(['GET', 'PUT'])
@permission_classes([IsAuthenticated])
def user_preferences(request):
    """Retrieve or partially update global notification, score display, and source limit preferences.

    Args:
        request: GET request or PUT request with preference updates.

    Returns:
        Response: Current or updated user preferences dictionary.
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
    """Retrieve the 10 most recent search history items for the authenticated user.

    Args:
        request: HTTP request.

    Returns:
        Response: List of serialized recent search entries.
    """
    searches = RecentSearch.objects.filter(user=request.user).select_related('entity')[:10]
    user_nest_ids = set(UserNest.objects.filter(user=request.user).values_list('entity_id', flat=True))
    serializer = RecentSearchSerializer(searches, many=True, context={'request': request, 'user_nest_entity_ids': user_nest_ids})
    return Response(serializer.data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def save_search(request):
    """Save a user query or selected entity search to the recent search history.

    Args:
        request: HTTP request with body {"query": "Lakers", "entity_id": 123}.

    Returns:
        Response: Created search history record.
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