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
    GET /api/calendar/nest?start_date=2026-03-14&end_date=2026-03-21
    """
    nest_entities = UserNest.objects.filter(
        user=request.user
    ).values_list('entity_id', flat=True)

    if not nest_entities:
        return Response({'message': 'No entities in your nest', 'events': []})

    # Date range — default to current week
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')

    try:
        start_date = datetime.fromisoformat(start_date_str).date() if start_date_str else timezone.now().date()
        end_date = datetime.fromisoformat(end_date_str).date() if end_date_str else start_date + timedelta(days=7)
    except ValueError:
        return Response({'error': 'Invalid date format. Use YYYY-MM-DD'}, status=400)

    events = Event.objects.filter(
        start_time__date__gte=start_date,
        start_time__date__lte=end_date,
    ).filter(
        Q(home_entity_id__in=nest_entities) |
        Q(away_entity_id__in=nest_entities)
    ).select_related(
        'home_entity', 'away_entity', 'league'
    ).order_by('start_time')

    # Materialize queryset once to avoid a second count() query
    events_list = list(events)

    grouped = {}
    for event in events_list:
        date_key = event.start_time.date().isoformat()
        if date_key not in grouped:
            grouped[date_key] = []
        grouped[date_key].append(EventSerializer(event).data)

    return Response({
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'total_count': len(events_list),
        'events_by_date': grouped,  # for calendar grid
        'events': EventSerializer(events_list, many=True).data,  # flat list
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_matches_of_day(request):
    """
    Get matches of the day for user's nest entities
    GET /api/calendar/matches-of-day?date=2026-03-14
    
    Used for the 'Matches of the Day' card on the Nest Calendar screen.
    """
    date_str = request.GET.get('date')
    try:
        query_date = datetime.fromisoformat(date_str).date() if date_str else timezone.now().date()
    except ValueError:
        return Response({'error': 'Invalid date format. Use YYYY-MM-DD'}, status=400)

    nest_entities = UserNest.objects.filter(
        user=request.user
    ).values_list('entity_id', flat=True)

    # Get all matches on this date involving user's nest entities
    matches = Event.objects.filter(
        start_time__date=query_date,
    ).filter(
        Q(home_entity_id__in=nest_entities) |
        Q(away_entity_id__in=nest_entities)
    ).select_related(
        'home_entity', 'away_entity', 'league'
    ).order_by('start_time')

    # If no nest matches, show popular matches of the day
    if not matches.exists():
        matches = Event.objects.filter(
            start_time__date=query_date,
        ).select_related(
            'home_entity', 'away_entity', 'league'
        ).order_by('start_time')[:10]

    # Separate live vs upcoming vs completed
    live = [e for e in matches if e.status == 'live']
    upcoming = [e for e in matches if e.status == 'upcoming']
    completed = [e for e in matches if e.status == 'completed']

    return Response({
        'date': query_date.isoformat(),
        'total_count': len(list(matches)),
        'live': EventSerializer(live, many=True).data,
        'upcoming': EventSerializer(upcoming, many=True).data,
        'completed': EventSerializer(completed, many=True).data,
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_entity_calendar(request, entity_id):
    """
    Get calendar events for a specific entity
    GET /api/calendar/entity/{entity_id}?start_date=2026-03-14
    """
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')

    try:
        start_date = datetime.fromisoformat(start_date_str).date() if start_date_str else timezone.now().date()
        end_date = datetime.fromisoformat(end_date_str).date() if end_date_str else start_date + timedelta(days=30)
    except ValueError:
        return Response({'error': 'Invalid date format. Use YYYY-MM-DD'}, status=400)

    events = Event.objects.filter(
        start_time__date__gte=start_date,
        start_time__date__lte=end_date,
    ).filter(
        Q(home_entity_id=entity_id) | Q(away_entity_id=entity_id)
    ).select_related(
        'home_entity', 'away_entity', 'league'
    ).order_by('start_time')

    return Response({
        'entity_id': entity_id,
        'upcoming': EventSerializer(events.filter(status='upcoming'), many=True).data,
        'live': EventSerializer(events.filter(status='live'), many=True).data,
        'recent': EventSerializer(events.filter(status='completed').order_by('-start_time')[:10], many=True).data,
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_event_detail(request, event_id):
    """
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
    return Response(EventDetailSerializer(event).data)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_live_events(request):
    """
    GET /api/events/live?sport=soccer
    """
    events = Event.objects.filter(
        status='live'
    ).select_related('home_entity', 'away_entity', 'league').order_by('-start_time')

    sport = request.GET.get('sport')
    if sport:
        events = events.filter(sport=sport)

    return Response({
        'count': events.count(),
        'events': EventSerializer(events, many=True).data,
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_upcoming_events(request):
    """
    GET /api/events/upcoming?days=7&sport=soccer
    """
    days = int(request.GET.get('days', 7))
    sport = request.GET.get('sport')
    end_date = timezone.now() + timedelta(days=days)

    events = Event.objects.filter(
        status='upcoming',
        start_time__lte=end_date,
        start_time__gte=timezone.now(),
    ).select_related('home_entity', 'away_entity', 'league').order_by('start_time')

    if sport:
        events = events.filter(sport=sport)

    return Response({
        'days': days,
        'count': events.count(),
        'events': EventSerializer(events, many=True).data,
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_events_by_date(request, date):
    """
    GET /api/events/date/2026-03-14
    """
    try:
        query_date = datetime.fromisoformat(date).date()
    except ValueError:
        return Response({'error': 'Invalid date format. Use YYYY-MM-DD'}, status=400)

    events = Event.objects.filter(
        start_time__date=query_date
    ).select_related('home_entity', 'away_entity', 'league').order_by('start_time')

    sport = request.GET.get('sport')
    if sport:
        events = events.filter(sport=sport)

    # Group by sport
    grouped = {}
    for event in events:
        s = event.sport
        if s not in grouped:
            grouped[s] = []
        grouped[s].append(EventSerializer(event).data)

    return Response({
        'date': date,
        'total_count': events.count(),
        'events_by_sport': grouped,
    })
 
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def trigger_event_detail_fetch(request, event_id):
    """
    Manually trigger stats + lineups + player stats fetch for an event.
    POST /api/events/{event_id}/fetch-details/
 
    Use this in Postman to populate a completed event for testing.
    Example: find a completed soccer event id in your DB, then call this.
    """
    from apps.event.tasks import fetch_event_details
    from apps.event.models import Event
 
    event = get_object_or_404(Event, id=event_id)
 
    if event.api_source != 'api_sports':
        return Response(
            {'error': f'Only api_sports events supported. This event is from {event.api_source}'},
            status=400,
        )
 
    fetch_event_details.delay(event.id)
 
    return Response({
        'success': True,
        'message': f'Detail fetch triggered for event {event_id} ({event})',
        'event_id': event_id,
        'fixture_id': event.external_id,
    })
 