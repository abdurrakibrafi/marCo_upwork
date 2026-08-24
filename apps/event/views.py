from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db.models import Q
from datetime import datetime, timedelta
from apps.event.models import Event, EventStatistics
from apps.event.serializers import EventSerializer, EventDetailSerializer
from apps.core.utils.mixins import BaseResponseMixin
from apps.nest.models import UserNest


def _deduplicate_events(events_list: list) -> list:
    """Deduplicate a list of Event objects by (home_team, away_team, start_time_date).

    Prefers 'statpal' api source over other providers when duplicate fixture records exist.

    Args:
        events_list (list): List of Event model instances.

    Returns:
        list: Deduplicated list of Event instances.
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
    """Retrieve scheduled, live, and recent events for entities in the authenticated user's Nest.

    Supports date range filtering, single entity scoping, and automatic cross-source entity resolution.

    Args:
        request (Request): HTTP GET request with optional query params 'start_date', 'end_date', 'entity_id', 'sport'.

    Returns:
        Response: Structured calendar payload grouped by date with followed nest entity highlights.
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

        # Optional single entity filter for Nest Calendar (agent_task.md Section 14)
        entity_id_param = request.query_params.get("entity_id")
        if entity_id_param:
            try:
                target_entity_id = int(entity_id_param)
                if target_entity_id not in base_entity_ids:
                    return mixin.success_response(
                        data={
                            "start_date": timezone.now().date().isoformat(),
                            "total_count": 0,
                            "events_by_date": {},
                            "events": [],
                        },
                        message="Specified entity is not in your nest.",
                    )
                base_entity_ids = [target_entity_id]
            except ValueError:
                pass

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

        # Date range support
        start_date_str = request.query_params.get("start_date")
        end_date_str = request.query_params.get("end_date")

        try:
            start_date = datetime.fromisoformat(start_date_str).date() if start_date_str else timezone.now().date() - timedelta(days=7)
            end_date = datetime.fromisoformat(end_date_str).date() if end_date_str else start_date + timedelta(days=97)
        except ValueError:
            return mixin.error_response(
                message="Invalid date format. Use YYYY-MM-DD",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        # 3. Queryset
        qs = (
            Event.objects.filter(
                start_time__date__gte=start_date,
                start_time__date__lte=end_date,
            ).filter(
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
            # Default: show matches in the specified date range
            pass

        qs = qs.select_related("home_entity", "away_entity", "league").order_by("start_time")

        # 5. Optional sport filter
        sport = request.query_params.get("sport")
        if sport:
            # Entity.sport is 'basketball' but Event.sport is 'nba' — filter using raw slug
            qs = qs.filter(sport=sport.lower())

        # 6. Deduplicate and Serialize
        events_list = _deduplicate_events(list(qs))
        serialized   = EventSerializer(
            events_list,
            many=True,
            context={'request': request, 'nest_entity_ids': set(nest_entity_ids)}
        ).data

        # 7. Group by date
        events_by_date: dict = {}
        for event_obj, event_data in zip(events_list, serialized):
            date_key = event_obj.start_time.date().isoformat()
            events_by_date.setdefault(date_key, []).append(event_data)

        return mixin.success_response(
            data={
                "start_date":     start_date.isoformat(),
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
    """Retrieve comprehensive event detail including lineups, timeline, statistics, and highlights.

    Performs on-the-fly live provider data enrichment and fallback statistic generation when required.

    Args:
        request (Request): HTTP GET request.
        event_id (int): Primary key ID of the Event fixture.

    Returns:
        Response: Serialized EventDetailSerializer payload.
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
        if is_completed and (not event.metadata.get("details_fetched") or (event.sport == "soccer" and not event.metadata.get("team_stats"))):
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

        # Auto-ensure EventStatistics exist for any sport if scores exist
        if (event.home_score is not None or event.away_score is not None) and event.home_entity and event.away_entity:
            from apps.event.utils_stats import normalize_event_stats
            has_valid = any(normalize_event_stats(s.stats) for s in event.statistics.all() if s.stats)
            if not has_valid:
                for side, team in [('home', event.home_entity), ('away', event.away_entity)]:
                    team_tl = event.timeline.filter(team=team)
                    score_val = event.home_score if side == 'home' else event.away_score
                    score_label = 'runs' if event.sport == 'cricket' else 'goals'

                    stats_payload = {
                        'side': side,
                        score_label: str(score_val if score_val is not None else 0),
                        'yellowcards': str(team_tl.filter(event_type='yellow_card').count()),
                        'redcards': str(team_tl.filter(event_type='red_card').count()),
                        'substitutions': str(team_tl.filter(event_type='substitution').count()),
                        'ft_home': str(event.home_score or 0),
                        'ft_away': str(event.away_score or 0),
                        'is_fallback': True,
                    }
                    EventStatistics.objects.update_or_create(
                        event=event,
                        team=team,
                        defaults={'stats': stats_payload}
                    )
                event = Event.objects.select_related(
                    "home_entity", "away_entity", "league"
                ).prefetch_related(
                    "timeline", "lineups", "statistics",
                    "player_stats", "highlights"
                ).get(id=event_id)

        return mixin.success_response(data=EventDetailSerializer(event).data)
    except Exception as exc:
        return mixin.handle_exception(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_matches_of_day(request):
    """Retrieve featured 'Matches of the Day' involving the user's followed Nest entities for a given date.

    Falls back to global popular matches if no followed nest fixtures take place on the requested date.

    Args:
        request (Request): HTTP GET request with optional query param 'date' (YYYY-MM-DD).

    Returns:
        Response: Structured payload separating live, upcoming, and completed matches of the day.
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
    """Retrieve schedule of upcoming, live, and recent events for a specific sports team or league.

    Args:
        request (Request): HTTP GET request with optional query params 'start_date' and 'end_date'.
        entity_id (int): Primary key ID of the Entity (team or league).

    Returns:
        Response: Structured payload separating upcoming, live, and recent events.
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
    """Retrieve all currently active live sports events across all supported sports.

    Args:
        request (Request): HTTP GET request with optional 'sport' filter.

    Returns:
        Response: List of ongoing live match fixtures.
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
    """Retrieve scheduled upcoming sporting events within a specified day horizon.

    Args:
        request (Request): HTTP GET request with optional query params 'days' (default 7) and 'sport'.

    Returns:
        Response: List of scheduled upcoming fixtures.
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
    """Retrieve all sporting events scheduled on a specific calendar date, grouped by sport.

    Args:
        request (Request): HTTP GET request with optional 'sport' filter.
        date (str): Date string in YYYY-MM-DD format.

    Returns:
        Response: Fixtures grouped by sport for the requested date.
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
    """Manually dispatch background Celery task to fetch detailed match statistics, lineups, and timeline.

    Args:
        request (Request): HTTP POST request.
        event_id (int): Primary key ID of the Event fixture.

    Returns:
        Response: Task dispatch confirmation payload.
    """
    mixin = BaseResponseMixin()
    try:
        from apps.event.tasks import fetch_event_details
        from apps.event.models import Event

        if event.api_source != 'statpal':
            return mixin.error_response(
                message=f'Only statpal events supported. This event is from {event.api_source}',
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
 