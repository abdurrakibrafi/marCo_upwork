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
from apps.core.utils.mixins import BaseResponseMixin
from apps.nest.models import UserNest


# @api_view(['GET'])
# @permission_classes([IsAuthenticated])
# def get_nest_calendar(request):
#     """
#     Get calendar events for user's nest entities
#     GET /api/calendar/nest?start_date=2026-03-14&end_date=2026-03-21
#     """
#     mixin = BaseResponseMixin()
#     try:
#         nest_entities = UserNest.objects.filter(
#             user=request.user
#         ).values_list('entity_id', flat=True)

#         if not nest_entities:
#             return mixin.success_response(
#                 data={'events': []},
#                 message='No entities in your nest'
#             )

#         # Date range — default to current week
#         start_date_str = request.GET.get('start_date')
#         end_date_str = request.GET.get('end_date')

#         try:
#             start_date = datetime.fromisoformat(start_date_str).date() if start_date_str else timezone.now().date()
#             end_date = datetime.fromisoformat(end_date_str).date() if end_date_str else start_date + timedelta(days=7)
#         except ValueError:
#             return mixin.error_response(
#                 message='Invalid date format. Use YYYY-MM-DD',
#                 status_code=status.HTTP_400_BAD_REQUEST
#             )

#         events = Event.objects.filter(
#             start_time__date__gte=start_date,
#             start_time__date__lte=end_date,
#         ).filter(
#             Q(home_entity_id__in=nest_entities) |
#             Q(away_entity_id__in=nest_entities)
#         ).select_related(
#             'home_entity', 'away_entity', 'league'
#         ).order_by('start_time')

#         # Materialize queryset once to avoid duplicate serialization work
#         events_list = list(events)
#         serialized_events = EventSerializer(events_list, many=True).data

#         grouped = {}
#         for i, event in enumerate(events_list):
#             date_key = event.start_time.date().isoformat()
#             if date_key not in grouped:
#                 grouped[date_key] = []
#             grouped[date_key].append(serialized_events[i])

#         data = {
#             'start_date': start_date.isoformat(),
#             'end_date': end_date.isoformat(),
#             'total_count': len(events_list),
#             'events_by_date': grouped,  # for calendar grid
#             'events': serialized_events,
#         }
#         return mixin.success_response(data=data)
#     except Exception as exc:
#         return mixin.handle_exception(exc)


"""
apps/event/views.py  — get_nest_calendar এবং get_event_detail

তোমার existing views-এর সাথে merge করো।
BaseResponseMixin, EventSerializer, UserNest — তোমার existing imports রাখো।
"""
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

# তোমার existing imports অনুযায়ী রাখো — নিচের lines পরিবর্তন করো না
from apps.core.utils.mixins import BaseResponseMixin
from apps.event.models import Event
from apps.event.serializers import EventSerializer
def _deduplicate_events(events_list):
    """
    Deduplicate a list of Event objects by (home_team, away_team, start_time_date).
    If duplicate found, prefer 'statpal' api source.
    """
    seen_matches = {}
    unique_events = []
    for event in events_list:
        home_name = event.home_entity.name.lower() if event.home_entity else ''
        away_name = event.away_entity.name.lower() if event.away_entity else ''
        match_key = (home_name, away_name, event.start_time.date())
        
        existing = seen_matches.get(match_key)
        if existing is None:
            seen_matches[match_key] = event
            unique_events.append(event)
        else:
            if event.api_source == 'statpal' and existing.api_source != 'statpal':
                unique_events.remove(existing)
                seen_matches[match_key] = event
                unique_events.append(event)
    return unique_events


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_nest_calendar(request):
    """
    Query params (optional):
      sport  — 'soccer' | 'nba' | 'cricket'
      status — 'live' | 'upcoming'
      days   — upcoming (default 7)

    Response:
    {
      "Success": true,
      "Data": {
        "start_date": "YYYY-MM-DD",
        "total_count": 12,
        "events_by_date": {
          "2025-12-15": [ {...}, ... ],
          "2025-12-16": [ {...}, ... ]
        },
        "events": [ flat list ]
      }
    }
    """
    mixin = BaseResponseMixin()
    try:
        base_entity_ids = list(
            UserNest.objects.filter(user=request.user)
            .values_list("entity_id", flat=True)
        )
        if not base_entity_ids:
            return mixin.success_response(
                data={
                    "start_date": timezone.now().date().isoformat(),
                    "total_count": 0,
                    "events_by_date": {},
                    "events": [],
                },
                message="No entities in your nest.",
            )

        # Include duplicate / canonical entities to handle cross-source data variations robustly
        from apps.entity.models import Entity
        nest_entities = Entity.objects.filter(id__in=base_entity_ids)
        nest_entity_ids = set(base_entity_ids)
        for ent in nest_entities:
            # Match by name, sport, type (fuzzy/exact fallback)
            duplicates = Entity.objects.filter(
                name__iexact=ent.name,
                sport=ent.sport,
                type=ent.type
            ).values_list("id", flat=True)
            nest_entity_ids.update(duplicates)
            
            # Match by explicit canonical mapping
            if ent.canonical_entity_id:
                nest_entity_ids.add(ent.canonical_entity_id)
                other_dups = Entity.objects.filter(
                    canonical_entity_id=ent.canonical_entity_id
                ).values_list("id", flat=True)
                nest_entity_ids.update(other_dups)
            
            child_dups = Entity.objects.filter(
                canonical_entity_id=ent.id
            ).values_list("id", flat=True)
            nest_entity_ids.update(child_dups)
            
        nest_entity_ids = list(nest_entity_ids)

        # Classify nest entities by type to support leagues and athletes
        resolved_entities = Entity.objects.filter(id__in=nest_entity_ids)
        team_ids = set()
        league_ids = set()
        athlete_ids = set()
        for ent in resolved_entities:
            if ent.type == 'team':
                team_ids.add(ent.id)
            elif ent.type == 'league':
                league_ids.add(ent.id)
            elif ent.type == 'athlete':
                athlete_ids.add(ent.id)

        # For followed athletes, find their team IDs
        if athlete_ids:
            from apps.entity.models import Athlete
            athlete_teams = Athlete.objects.filter(entity_id__in=athlete_ids).values_list('current_team_id', flat=True)
            team_ids.update(athlete_teams)

        # 3. Queryset
        qs = (
            Event.objects.filter(
                Q(home_entity_id__in=team_ids)
                | Q(away_entity_id__in=team_ids)
                | Q(league_id__in=league_ids)
            )
        )

        now = timezone.now()
        status_param = request.query_params.get("status")
        if status_param == "upcoming":
            qs = qs.filter(start_time__gte=now)
        elif status_param == "completed":
            qs = qs.filter(Q(start_time__lt=now) | Q(status="completed"))
        elif status_param == "live":
            qs = qs.filter(status="live")
        else:
            # Default: show all match data present in the database for followed teams
            pass

        qs = qs.select_related("home_entity", "away_entity", "league").order_by("start_time")

        # 5. Optional sport filter
        sport = request.query_params.get("sport")
        if sport:
            # Entity.sport 'basketball' কিন্তু Event.sport 'nba' — তাই raw slug দিয়ে filter
            qs = qs.filter(sport=sport.lower())

        # 6. Deduplicate and Serialize
        events_list = _deduplicate_events(list(qs))
        serialized   = EventSerializer(events_list, many=True).data

        # 7. Group by date
        events_by_date: dict = {}
        for event_obj, event_data in zip(events_list, serialized):
            date_key = event_obj.start_time.date().isoformat()
            events_by_date.setdefault(date_key, []).append(event_data)

        return mixin.success_response(
            data={
                "start_date":     timezone.now().date().isoformat(),
                "total_count":    len(events_list),
                "events_by_date": events_by_date,
                "events":         list(serialized),
            }
        )

    except Exception as exc:
        return mixin.handle_exception(exc)


@api_view(["GET"])
@permission_classes([AllowAny])
def get_event_detail(request, event_id: int):
    """
    Full detail for a single event including metadata (lineups, etc.)

    Response shape:
    {
        "Success": true,
        "Data": { <EventSerializer with metadata> }
    }
    """
    mixin = BaseResponseMixin()
    try:
        event = get_object_or_404(
            Event.objects.select_related(
                "home_entity", "away_entity", "league"
            ).prefetch_related(
                "timeline", "lineups", "statistics",
                "player_stats", "highlights"
            ),
            id=event_id
        )

        # On-the-fly details population for completed/finished events (if missing)
        is_completed = (event.status == "completed") or (
            event.status == "upcoming" and event.start_time and event.start_time < timezone.now()
        )
        if is_completed and not event.metadata.get("details_fetched"):
            if event.api_source == "statpal":
                from apps.event.tasks import _on_the_fly_update_statpal_event
                try:
                    _on_the_fly_update_statpal_event(event)
                    event.metadata["details_fetched"] = True
                    event.save(update_fields=["metadata"])
                    # Re-fetch event to include newly created timeline and stats
                    event = Event.objects.select_related(
                        "home_entity", "away_entity", "league"
                    ).prefetch_related(
                        "timeline", "lineups", "statistics",
                        "player_stats", "highlights"
                    ).get(id=event_id)
                except Exception:
                    pass
            elif event.api_source == "api_sports":
                from apps.event.tasks import fetch_event_details
                try:
                    fetch_event_details(event.id)
                    # Re-fetch event to include newly fetched data
                    event = Event.objects.select_related(
                        "home_entity", "away_entity", "league"
                    ).prefetch_related(
                        "timeline", "lineups", "statistics",
                        "player_stats", "highlights"
                    ).get(id=event_id)
                    event.metadata["details_fetched"] = True
                    event.save(update_fields=["metadata"])
                except Exception:
                    pass

        return mixin.success_response(data=EventDetailSerializer(event).data)
    except Exception as exc:
        return mixin.handle_exception(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_matches_of_day(request):
    """
    Get matches of the day for user's nest entities
    GET /api/calendar/matches-of-day?date=2026-03-14
    
    Used for the 'Matches of the Day' card on the Nest Calendar screen.
    """
    mixin = BaseResponseMixin()
    try:
        date_str = request.GET.get('date')
        try:
            query_date = datetime.fromisoformat(date_str).date() if date_str else timezone.now().date()
        except ValueError:
            return mixin.error_response(
                message='Invalid date format. Use YYYY-MM-DD',
                status_code=status.HTTP_400_BAD_REQUEST
            )

        nest_entities = list(UserNest.objects.filter(
            user=request.user
        ).values_list('entity_id', flat=True))

        # Include duplicate / canonical entities to handle cross-source data variations robustly
        from apps.entity.models import Entity
        resolved_entities = Entity.objects.filter(id__in=nest_entities)
        nest_entity_ids = set(nest_entities)
        for ent in resolved_entities:
            duplicates = Entity.objects.filter(
                name__iexact=ent.name,
                sport=ent.sport,
                type=ent.type
            ).values_list("id", flat=True)
            nest_entity_ids.update(duplicates)
            if ent.canonical_entity_id:
                nest_entity_ids.add(ent.canonical_entity_id)
                other_dups = Entity.objects.filter(
                    canonical_entity_id=ent.canonical_entity_id
                ).values_list("id", flat=True)
                nest_entity_ids.update(other_dups)
            child_dups = Entity.objects.filter(
                canonical_entity_id=ent.id
            ).values_list("id", flat=True)
            nest_entity_ids.update(child_dups)

        resolved_entities = Entity.objects.filter(id__in=nest_entity_ids)
        team_ids = set()
        league_ids = set()
        athlete_ids = set()
        for ent in resolved_entities:
            if ent.type == 'team':
                team_ids.add(ent.id)
            elif ent.type == 'league':
                league_ids.add(ent.id)
            elif ent.type == 'athlete':
                athlete_ids.add(ent.id)

        # For followed athletes, find their team IDs
        if athlete_ids:
            from apps.entity.models import Athlete
            athlete_teams = Athlete.objects.filter(entity_id__in=athlete_ids).values_list('current_team_id', flat=True)
            team_ids.update(athlete_teams)

        # Get all matches on this date involving user's nest entities
        matches_qs = Event.objects.filter(
            start_time__date=query_date,
        ).filter(
            Q(home_entity_id__in=team_ids) |
            Q(away_entity_id__in=team_ids) |
            Q(league_id__in=league_ids)
        ).select_related(
            'home_entity', 'away_entity', 'league'
        ).order_by('start_time')

        # If no nest matches, show popular matches of the day
        if not matches_qs.exists():
            matches_qs = Event.objects.filter(
                start_time__date=query_date,
            ).select_related(
                'home_entity', 'away_entity', 'league'
            ).order_by('start_time')[:10]

        matches = _deduplicate_events(list(matches_qs))

        # Separate live vs upcoming vs completed
        live = [e for e in matches if e.status == 'live']
        upcoming = [e for e in matches if e.status == 'upcoming']
        completed = [e for e in matches if e.status == 'completed']

        data = {
            'date': query_date.isoformat(),
            'total_count': len(matches),
            'live': EventSerializer(live, many=True).data,
            'upcoming': EventSerializer(upcoming, many=True).data,
            'completed': EventSerializer(completed, many=True).data,
        }
        return mixin.success_response(data=data)
    except Exception as exc:
        return mixin.handle_exception(exc)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_entity_calendar(request, entity_id):
    """
    Get calendar events for a specific entity
    GET /api/calendar/entity/{entity_id}?start_date=2026-03-14
    """
    mixin = BaseResponseMixin()
    try:
        start_date_str = request.GET.get('start_date')
        end_date_str = request.GET.get('end_date')

        try:
            start_date = datetime.fromisoformat(start_date_str).date() if start_date_str else timezone.now().date()
            end_date = datetime.fromisoformat(end_date_str).date() if end_date_str else start_date + timedelta(days=30)
        except ValueError:
            return mixin.error_response(
                message='Invalid date format. Use YYYY-MM-DD',
                status_code=status.HTTP_400_BAD_REQUEST
            )

        # Expand entity_id to include duplicate / canonical entity IDs
        from apps.entity.models import Entity
        try:
            ent = Entity.objects.get(id=entity_id)
            related_ids = set([entity_id])
            
            # Exact name/sport/type match
            duplicates = Entity.objects.filter(
                name__iexact=ent.name,
                sport=ent.sport,
                type=ent.type
            ).values_list("id", flat=True)
            related_ids.update(duplicates)
            
            # Canonical entity matching
            if ent.canonical_entity_id:
                related_ids.add(ent.canonical_entity_id)
                related_ids.update(
                    Entity.objects.filter(canonical_entity_id=ent.canonical_entity_id).values_list("id", flat=True)
                )
            related_ids.update(
                Entity.objects.filter(canonical_entity_id=ent.id).values_list("id", flat=True)
            )
            related_ids = list(related_ids)
        except Entity.DoesNotExist:
            related_ids = [entity_id]

        events_qs = Event.objects.filter(
            start_time__date__gte=start_date,
            start_time__date__lte=end_date,
        ).filter(
            Q(home_entity_id__in=related_ids) | Q(away_entity_id__in=related_ids)
        ).select_related(
            'home_entity', 'away_entity', 'league'
        ).order_by('start_time')

        events_list = _deduplicate_events(list(events_qs))
        now = timezone.now()
        upcoming = [e for e in events_list if e.status == 'upcoming' and e.start_time >= now]
        live = [e for e in events_list if e.status == 'live']
        recent = sorted([e for e in events_list if e.start_time < now], key=lambda x: x.start_time, reverse=True)[:10]

        data = {
            'entity_id': entity_id,
            'upcoming': EventSerializer(upcoming, many=True).data,
            'live': EventSerializer(live, many=True).data,
            'recent': EventSerializer(recent, many=True).data,
        }
        return mixin.success_response(data=data)
    except Exception as exc:
        return mixin.handle_exception(exc)





@api_view(['GET'])
@permission_classes([AllowAny])
def get_live_events(request):
    """
    GET /api/events/live?sport=soccer
    """
    mixin = BaseResponseMixin()
    try:
        events = Event.objects.filter(
            status='live'
        ).select_related('home_entity', 'away_entity', 'league').order_by('-start_time')

        sport = request.GET.get('sport')
        if sport:
            events = events.filter(sport=sport)

        data = {
            'count': events.count(),
            'events': EventSerializer(events, many=True).data,
        }
        return mixin.success_response(data=data)
    except Exception as exc:
        return mixin.handle_exception(exc)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_upcoming_events(request):
    """
    GET /api/events/upcoming?days=7&sport=soccer
    """
    mixin = BaseResponseMixin()
    try:
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

        data = {
            'days': days,
            'count': events.count(),
            'events': EventSerializer(events, many=True).data,
        }
        return mixin.success_response(data=data)
    except Exception as exc:
        return mixin.handle_exception(exc)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_events_by_date(request, date):
    """
    GET /api/events/date/2026-03-14
    """
    mixin = BaseResponseMixin()
    try:
        try:
            query_date = datetime.fromisoformat(date).date()
        except ValueError:
            return mixin.error_response(
                message='Invalid date format. Use YYYY-MM-DD',
                status_code=status.HTTP_400_BAD_REQUEST
            )

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

        data = {
            'date': date,
            'total_count': events.count(),
            'events_by_sport': grouped,
        }
        return mixin.success_response(data=data)
    except Exception as exc:
        return mixin.handle_exception(exc)
 
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def trigger_event_detail_fetch(request, event_id):
    """
    Manually trigger stats + lineups + player stats fetch for an event.
    POST /api/events/{event_id}/fetch-details/
 
    Use this in Postman to populate a completed event for testing.
    Example: find a completed soccer event id in your DB, then call this.
    """
    mixin = BaseResponseMixin()
    try:
        from apps.event.tasks import fetch_event_details
        from apps.event.models import Event

        event = get_object_or_404(Event, id=event_id)

        if event.api_source not in ['api_sports', 'statpal']:
            return mixin.error_response(
                message=f'Only api_sports and statpal events supported. This event is from {event.api_source}',
                status_code=status.HTTP_400_BAD_REQUEST
            )

        fetch_event_details.delay(event.id)

        data = {
            'event_id': event_id,
            'fixture_id': event.external_id,
        }
        return mixin.success_response(
            data=data,
            message=f'Detail fetch triggered for event {event_id} ({event})'
        )
    except Exception as exc:
        return mixin.handle_exception(exc)
 