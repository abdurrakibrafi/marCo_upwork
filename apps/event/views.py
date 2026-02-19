from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db.models import Q
from datetime import datetime, timedelta
from apps.event.models import Event
from apps.event.serializers import EventSerializer, EventDetailSerializer
from apps.nest.models import UserNest

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_nest_calendar(request):
    """
    Get calendar events for user's nest entities
    GET /api/calendar/nest?start_date=2025-02-16&end_date=2025-02-23
    """
    # Get user's nest entities
    nest_entities = UserNest.objects.filter(user=request.user).values_list('entity_id', flat=True)
    
    if not nest_entities:
        return Response({
            'message': 'No entities in your nest',
            'events': []
        })
    
    # Date range
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    
    if not start_date:
        start_date = timezone.now().date()
    else:
        start_date = datetime.fromisoformat(start_date).date()
    
    if not end_date:
        end_date = start_date + timedelta(days=7)
    else:
        end_date = datetime.fromisoformat(end_date).date()
    
    # Query events
    events = Event.objects.filter(
        start_time__date__gte=start_date,
        start_time__date__lte=end_date
    ).filter(
        Q(home_entity_id__in=nest_entities) |
        Q(away_entity_id__in=nest_entities)
    ).select_related(
        'home_entity', 'away_entity', 'league'
    ).order_by('start_time')
    
    serializer = EventSerializer(events, many=True)
    
    return Response({
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'count': events.count(),
        'events': serializer.data
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_entity_calendar(request, entity_id):
    """
    Get calendar events for a specific entity
    GET /api/calendar/entity/{entity_id}?start_date=2025-02-16
    """
    # Date range
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    
    if not start_date:
        start_date = timezone.now().date()
    else:
        start_date = datetime.fromisoformat(start_date).date()
    
    if not end_date:
        end_date = start_date + timedelta(days=30)
    else:
        end_date = datetime.fromisoformat(end_date).date()
    
    # Query events
    events = Event.objects.filter(
        start_time__date__gte=start_date,
        start_time__date__lte=end_date
    ).filter(
        Q(home_entity_id=entity_id) |
        Q(away_entity_id=entity_id)
    ).select_related(
        'home_entity', 'away_entity', 'league'
    ).order_by('start_time')
    
    # Separate by status
    upcoming = events.filter(status='upcoming')
    live = events.filter(status='live')
    completed = events.filter(status='completed')[:10]  # Last 10 completed
    
    return Response({
        'entity_id': entity_id,
        'upcoming': EventSerializer(upcoming, many=True).data,
        'live': EventSerializer(live, many=True).data,
        'recent': EventSerializer(completed, many=True).data,
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_event_detail(request, event_id):
    """
    Get detailed event information
    GET /api/events/{event_id}
    """
    event = get_object_or_404(
        Event.objects.select_related(
            'home_entity', 'away_entity', 'league'
        ).prefetch_related(
            'timeline', 'lineups', 'statistics', 
            'player_stats', 'highlights'
        ),
        id=event_id
    )
    
    serializer = EventDetailSerializer(event)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_live_events(request):
    """
    Get all live events
    GET /api/events/live
    """
    sport = request.GET.get('sport')
    
    events = Event.objects.filter(status='live').select_related(
        'home_entity', 'away_entity', 'league'
    ).order_by('-start_time')
    
    if sport:
        events = events.filter(sport=sport)
    
    serializer = EventSerializer(events, many=True)
    
    return Response({
        'count': events.count(),
        'events': serializer.data
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_upcoming_events(request):
    """
    Get upcoming events
    GET /api/events/upcoming?days=7
    """
    days = int(request.GET.get('days', 7))
    sport = request.GET.get('sport')
    
    end_date = timezone.now() + timedelta(days=days)
    
    events = Event.objects.filter(
        status='upcoming',
        start_time__lte=end_date
    ).select_related(
        'home_entity', 'away_entity', 'league'
    ).order_by('start_time')
    
    if sport:
        events = events.filter(sport=sport)
    
    serializer = EventSerializer(events, many=True)
    
    return Response({
        'days': days,
        'count': events.count(),
        'events': serializer.data
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_events_by_date(request, date):
    """
    Get all events on a specific date
    GET /api/events/date/2025-02-16
    """
    try:
        query_date = datetime.fromisoformat(date).date()
    except ValueError:
        return Response(
            {'error': 'Invalid date format. Use YYYY-MM-DD'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    events = Event.objects.filter(
        start_time__date=query_date
    ).select_related(
        'home_entity', 'away_entity', 'league'
    ).order_by('start_time')
    
    # Group by sport
    grouped = {}
    for event in events:
        sport = event.sport
        if sport not in grouped:
            grouped[sport] = []
        grouped[sport].append(EventSerializer(event).data)
    
    return Response({
        'date': date,
        'total_count': events.count(),
        'events_by_sport': grouped
    })