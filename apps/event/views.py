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
import re


def _resolve_timezone(request):
    """Resolve requested timezone from query params (?timezone=, ?tz=) or headers (X-Timezone).

    Defaults to settings.TIME_ZONE (UTC).
    """
    if not request:
        return timezone.get_current_timezone()

    tz_param = None
    if hasattr(request, 'query_params'):
        tz_param = request.query_params.get("timezone") or request.query_params.get("tz")
    if not tz_param and hasattr(request, 'GET'):
        tz_param = request.GET.get("timezone") or request.GET.get("tz")
    if not tz_param and hasattr(request, 'headers'):
        tz_param = request.headers.get("X-Timezone")

    if not tz_param:
        return timezone.get_current_timezone()

    tz_str = str(tz_param).strip()

    # 1. Try pytz
    try:
        import pytz
        return pytz.timezone(tz_str)
    except Exception:
        pass

    # 2. Try zoneinfo
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo(tz_str)
    except Exception:
        pass

    # 3. Try offset format (e.g. '+06:00', '-05:00', '+6')
    try:
        from datetime import timezone as dt_timezone
        m = re.match(r'^([+-])(\d{1,2})(?::?(\d{2}))?$', tz_str)
        if m:
            sign = -1 if m.group(1) == '-' else 1
            hours = int(m.group(2))
            minutes = int(m.group(3)) if m.group(3) else 0
            offset = timedelta(hours=hours, minutes=minutes) * sign
            return dt_timezone(offset)
    except Exception:
        pass

    return timezone.get_current_timezone()


def _clean_entity_name_for_match(name: str) -> str:
    if not name:
        return ''
    s = name.lower().strip()
    s = re.sub(r'\b(fc|cf|club|united|utd|sc|afc|the)\b', '', s)
    return re.sub(r'[^a-z0-9]', '', s)


def _deduplicate_events(events_list: list) -> list:
    """Deduplicate a list of Event objects by canonical entity IDs, cleaned names, and start time.

    Prefers 'statpal' api source over other providers when duplicate fixture records exist.
    Matches events that share the same home/away teams (via canonical mapping or normalized name) and start
    within 12 hours of each other.

    Args:
        events_list (list): List of Event model instances.

    Returns:
        list: Deduplicated list of Event instances.
    """
    unique_events = []
    for event in events_list:
        if event.home_entity:
            home_key = event.home_entity.canonical_entity_id or event.home_entity_id
        else:
            home_key = 'none'

        if event.away_entity:
            away_key = event.away_entity.canonical_entity_id or event.away_entity_id
        else:
            away_key = 'none'

        duplicate_found = False
        for idx, existing in enumerate(unique_events):
            if existing.home_entity:
                ext_home_key = existing.home_entity.canonical_entity_id or existing.home_entity_id
            else:
                ext_home_key = 'none'

            if existing.away_entity:
                ext_away_key = existing.away_entity.canonical_entity_id or existing.away_entity_id
            else:
                ext_away_key = 'none'

            names_match = False
            if home_key == ext_home_key and away_key == ext_away_key and home_key != 'none':
                names_match = True
            else:
                h1 = event.home_entity.name.lower().strip() if event.home_entity else ''
                h2 = existing.home_entity.name.lower().strip() if existing.home_entity else ''
                a1 = event.away_entity.name.lower().strip() if event.away_entity else ''
                a2 = existing.away_entity.name.lower().strip() if existing.away_entity else ''
                if h1 == h2 and a1 == a2 and h1 != '':
                    names_match = True
                elif _clean_entity_name_for_match(h1) == _clean_entity_name_for_match(h2) and \
                     _clean_entity_name_for_match(a1) == _clean_entity_name_for_match(a2) and \
                     _clean_entity_name_for_match(h1) != '':
                    names_match = True
                elif h1 and h2 and not a1 and not a2 and (h1 == h2 or _clean_entity_name_for_match(h1) == _clean_entity_name_for_match(h2)):
                    names_match = True

            if names_match:
                time_diff = abs((event.start_time - existing.start_time).total_seconds()) if (event.start_time and existing.start_time) else 0
                if time_diff <= 12 * 3600:  # 12 hours
                    duplicate_found = True
                    # Prefer statpal over other sources, or prefer event with scores/live status
                    if (event.api_source == 'statpal' and existing.api_source != 'statpal') or \
                       (existing.home_score is None and event.home_score is not None) or \
                       (existing.status == 'upcoming' and event.status in ('live', 'completed')):
                        unique_events[idx] = event
                    break

        if not duplicate_found:
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

        user_tz = _resolve_timezone(request)
        now_local = timezone.now().astimezone(user_tz)

        # Date range support
        start_date_str = request.query_params.get("start_date")
        end_date_str = request.query_params.get("end_date")

        try:
            start_date = datetime.fromisoformat(start_date_str).date() if start_date_str else now_local.date() - timedelta(days=7)
            end_date = datetime.fromisoformat(end_date_str).date() if end_date_str else start_date + timedelta(days=97)
        except ValueError:
            return mixin.error_response(
                message="Invalid date format. Use YYYY-MM-DD",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        start_dt = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=user_tz)
        end_dt = datetime.combine(end_date, datetime.max.time()).replace(tzinfo=user_tz)

        # 3. Queryset
        qs = (
            Event.objects.filter(
                start_time__gte=start_dt,
                start_time__lte=end_dt,
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
            if sport.lower() in ('basketball', 'nba'):
                qs = qs.filter(sport__in=['basketball', 'nba'])
            else:
                qs = qs.filter(sport=sport.lower())

        # 6. Deduplicate and Serialize
        events_list = _deduplicate_events(list(qs))
        serialized   = EventSerializer(
            events_list,
            many=True,
            context={'request': request, 'nest_entity_ids': set(nest_entity_ids), 'timezone': user_tz}
        ).data

        # 7. Group by date in user's timezone
        events_by_date: dict = {}
        for event_obj, event_data in zip(events_list, serialized):
            local_dt = event_obj.start_time.astimezone(user_tz) if event_obj.start_time else None
            date_key = local_dt.date().isoformat() if local_dt else "TBD"
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
            is_baseball = event.sport in ('baseball', 'mlb')
            has_valid = any(normalize_event_stats(s.stats, sport=event.sport, event=event) for s in event.statistics.all() if s.stats)
            
            # If sport is baseball, ensure baseball stats with hits/errors/innings are present
            needs_update = not has_valid
            if is_baseball and not any(isinstance(s.stats, dict) and ('hits' in s.stats or s.stats.get('sport') == 'baseball') for s in event.statistics.all()):
                needs_update = True

            if needs_update:
                if is_baseball:
                    meta = event.metadata if isinstance(event.metadata, dict) else {}
                    for side, team in [('home', event.home_entity), ('away', event.away_entity)]:
                        side_meta = meta.get(side, {}) if isinstance(meta.get(side), dict) else {}
                        innings = {}
                        for i in range(1, 10):
                            k = f'in{i}'
                            if k in side_meta and side_meta[k] != '':
                                try:
                                    innings[str(i)] = int(side_meta[k])
                                except (ValueError, TypeError):
                                    innings[str(i)] = side_meta[k]
                        if side_meta.get('extra'):
                            innings['extra'] = side_meta['extra']

                        score_val = event.home_score if side == 'home' else event.away_score
                        runs = int(side_meta.get('totalscore') or (score_val if score_val is not None else 0))
                        hits = int(side_meta.get('hits') or 0)
                        errors = int(side_meta.get('errors') or 0)

                        stats_payload = {
                            'side': side,
                            'sport': 'baseball',
                            'runs': runs,
                            'hits': hits,
                            'errors': errors,
                            'innings': innings,
                            'is_fallback': False if side_meta else True,
                        }
                        EventStatistics.objects.update_or_create(
                            event=event,
                            team=team,
                            defaults={'stats': stats_payload}
                        )
                elif event.sport == 'cricket':
                    for side, team in [('home', event.home_entity), ('away', event.away_entity)]:
                        score_val = event.home_score if side == 'home' else event.away_score
                        stats_payload = {
                            'side': side,
                            'sport': 'cricket',
                            'runs': score_val if score_val is not None else 0,
                            'is_fallback': True,
                        }
                        EventStatistics.objects.update_or_create(
                            event=event,
                            team=team,
                            defaults={'stats': stats_payload}
                        )
                elif event.sport in ('basketball', 'nba'):
                    for side, team in [('home', event.home_entity), ('away', event.away_entity)]:
                        score_val = event.home_score if side == 'home' else event.away_score
                        stats_payload = {
                            'side': side,
                            'sport': 'basketball',
                            'points': score_val if score_val is not None else 0,
                            'is_fallback': True,
                        }
                        EventStatistics.objects.update_or_create(
                            event=event,
                            team=team,
                            defaults={'stats': stats_payload}
                        )
                else:
                    for side, team in [('home', event.home_entity), ('away', event.away_entity)]:
                        team_tl = event.timeline.filter(team=team)
                        score_val = event.home_score if side == 'home' else event.away_score

                        stats_payload = {
                            'side': side,
                            'sport': 'soccer',
                            'goals': str(score_val if score_val is not None else 0),
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

        user_tz = _resolve_timezone(request)
        return mixin.success_response(
            data=EventDetailSerializer(event, context={'request': request, 'timezone': user_tz}).data
        )
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
        user_tz = _resolve_timezone(request)
        now_local = timezone.now().astimezone(user_tz)
        date_str = request.GET.get('date')
        try:
            query_date = datetime.fromisoformat(date_str).date() if date_str else now_local.date()
        except ValueError:
            return mixin.error_response(
                message='Invalid date format. Use YYYY-MM-DD',
                status_code=status.HTTP_400_BAD_REQUEST
            )

        start_dt = datetime.combine(query_date, datetime.min.time()).replace(tzinfo=user_tz)
        end_dt = datetime.combine(query_date, datetime.max.time()).replace(tzinfo=user_tz)

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

        # Get all matches on this date in user's timezone involving user's nest entities
        matches_qs = Event.objects.filter(
            start_time__gte=start_dt,
            start_time__lte=end_dt,
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
                start_time__gte=start_dt,
                start_time__lte=end_dt,
            ).select_related(
                'home_entity', 'away_entity', 'league'
            ).order_by('start_time')[:10]

        matches = _deduplicate_events(list(matches_qs))

        # Separate live vs upcoming vs completed
        live = [e for e in matches if e.status == 'live']
        upcoming = [e for e in matches if e.status == 'upcoming']
        completed = [e for e in matches if e.status == 'completed']

        serializer_context = {'request': request, 'timezone': user_tz}
        data = {
            'date': query_date.isoformat(),
            'total_count': len(matches),
            'live': EventSerializer(live, many=True, context=serializer_context).data,
            'upcoming': EventSerializer(upcoming, many=True, context=serializer_context).data,
            'completed': EventSerializer(completed, many=True, context=serializer_context).data,
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
        user_tz = _resolve_timezone(request)
        now_local = timezone.now().astimezone(user_tz)
        start_date_str = request.GET.get('start_date')
        end_date_str = request.GET.get('end_date')

        try:
            start_date = datetime.fromisoformat(start_date_str).date() if start_date_str else now_local.date()
            end_date = datetime.fromisoformat(end_date_str).date() if end_date_str else start_date + timedelta(days=30)
        except ValueError:
            return mixin.error_response(
                message='Invalid date format. Use YYYY-MM-DD',
                status_code=status.HTTP_400_BAD_REQUEST
            )

        start_dt = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=user_tz)
        end_dt = datetime.combine(end_date, datetime.max.time()).replace(tzinfo=user_tz)

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
            start_time__gte=start_dt,
            start_time__lte=end_dt,
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

        serializer_context = {'request': request, 'timezone': user_tz}
        data = {
            'entity_id': entity_id,
            'upcoming': EventSerializer(upcoming, many=True, context=serializer_context).data,
            'live': EventSerializer(live, many=True, context=serializer_context).data,
            'recent': EventSerializer(recent, many=True, context=serializer_context).data,
        }
        return mixin.success_response(data=data)
    except Exception as exc:
        return mixin.handle_exception(exc)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_live_events(request):
    """Retrieve currently active live sports events filtered for user's followed Nest entities.

    Args:
        request (Request): HTTP GET request with optional 'sport' filter.

    Returns:
        Response: List of ongoing live match fixtures.
    """
    mixin = BaseResponseMixin()
    try:
        user_tz = _resolve_timezone(request)
        events = Event.objects.filter(
            status='live'
        ).select_related('home_entity', 'away_entity', 'league').order_by('-start_time')

        # Filter by Nest entities for authenticated users (unless ?all=true is explicitly requested)
        show_all = request.GET.get('all', '').lower() == 'true'
        if request.user and request.user.is_authenticated and not show_all:
            from apps.nest.models import UserNest
            from apps.entity.models import Entity
            from django.db.models import Q

            user_nest_ids = list(
                UserNest.objects.filter(user=request.user).values_list("entity_id", flat=True)
            )
            if not user_nest_ids:
                return mixin.success_response(data={'count': 0, 'events': []})

            nest_entities = Entity.objects.filter(id__in=user_nest_ids)
            all_ids = set(user_nest_ids)
            for ent in nest_entities:
                dups = Entity.objects.filter(name__iexact=ent.name, sport=ent.sport, type=ent.type).values_list("id", flat=True)
                all_ids.update(dups)
                if ent.canonical_entity_id:
                    all_ids.add(ent.canonical_entity_id)
                if ent.type == 'athlete':
                    ad = getattr(ent, 'athlete_details', None)
                    if ad and ad.current_team_id:
                        all_ids.add(ad.current_team_id)

            events = events.filter(
                Q(home_entity_id__in=all_ids)
                | Q(away_entity_id__in=all_ids)
                | Q(league_id__in=all_ids)
            )

        sport = request.GET.get('sport')
        if sport:
            if sport.lower() in ('basketball', 'nba'):
                events = events.filter(sport__in=['basketball', 'nba'])
            else:
                events = events.filter(sport=sport.lower())

        events_list = _deduplicate_events(list(events))

        serializer_context = {'request': request, 'timezone': user_tz}
        data = {
            'count': len(events_list),
            'events': EventSerializer(events_list, many=True, context=serializer_context).data,
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
        user_tz = _resolve_timezone(request)
        days = int(request.GET.get('days', 7))
        sport = request.GET.get('sport')
        end_date = timezone.now() + timedelta(days=days)

        events = Event.objects.filter(
            status='upcoming',
            start_time__lte=end_date,
            start_time__gte=timezone.now(),
        ).select_related('home_entity', 'away_entity', 'league').order_by('start_time')

        if sport:
            if sport.lower() in ('basketball', 'nba'):
                events = events.filter(sport__in=['basketball', 'nba'])
            else:
                events = events.filter(sport=sport.lower())

        events_list = _deduplicate_events(list(events))

        serializer_context = {'request': request, 'timezone': user_tz}
        data = {
            'days': days,
            'count': len(events_list),
            'events': EventSerializer(events_list, many=True, context=serializer_context).data,
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
        user_tz = _resolve_timezone(request)
        try:
            query_date = datetime.fromisoformat(date).date()
        except ValueError:
            return mixin.error_response(
                message='Invalid date format. Use YYYY-MM-DD',
                status_code=status.HTTP_400_BAD_REQUEST
            )

        start_dt = datetime.combine(query_date, datetime.min.time()).replace(tzinfo=user_tz)
        end_dt = datetime.combine(query_date, datetime.max.time()).replace(tzinfo=user_tz)

        events = Event.objects.filter(
            start_time__gte=start_dt,
            start_time__lte=end_dt,
        ).select_related('home_entity', 'away_entity', 'league').order_by('start_time')

        sport = request.GET.get('sport')
        if sport:
            if sport.lower() in ('basketball', 'nba'):
                events = events.filter(sport__in=['basketball', 'nba'])
            else:
                events = events.filter(sport=sport.lower())

        events_list = _deduplicate_events(list(events))

        serializer_context = {'request': request, 'timezone': user_tz}
        # Group by sport
        grouped = {}
        for event in events_list:
            s = event.sport
            if s not in grouped:
                grouped[s] = []
            grouped[s].append(EventSerializer(event, context=serializer_context).data)

        data = {
            'date': date,
            'total_count': len(events_list),
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

        event = get_object_or_404(Event, id=event_id)
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
 