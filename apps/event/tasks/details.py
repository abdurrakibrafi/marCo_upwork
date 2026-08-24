import logging
from datetime import datetime, timedelta
from django.utils import timezone
from celery import shared_task
from apps.event.models import Event, EventStatistics, EventLineup, EventPlayerStats, EventTimeline
from apps.entity.utils.matcher import get_or_create_precise_entity
from .parsers import (
    _extract_minute, _soccer_rows, _nba_rows, _nfl_rows, _tennis_rows,
    _mlb_rows, _handball_rows, _cricket_rows, _golf_rows, _volleyball_rows, _horse_racing_rows
)

logger = logging.getLogger(__name__)


def _populate_statpal_event_details(event):
    """Parse StatPal metadata payload to populate EventTimeline, lineups, and match statistics.

    Extracts goals, yellow/red cards, substitutions, formations, and half-time/full-time scores.

    Args:
        event (Event): Event model instance to enrich.
    """
    meta = event.metadata or {}
    
    # 1. Fetch soccer stats if team_stats is missing from metadata
    if event.sport == 'soccer' and not meta.get('team_stats') and event.league:
        try:
            league_id = int(event.league.external_id)
            # Skip league 3962 because it returns HTTP 404, and honor inactive league status
            if league_id != 3962 and event.league.is_active:
                from apps.sports_apis.services.statpal import statpal_service
                result = statpal_service.get_soccer_match_stats(league_id)
                if result['success']:
                    matches = result['data'].get('match-stats', {}).get('tournament', {}).get('matches', [])
                    if isinstance(matches, dict):
                        matches = [matches]
                    elif not isinstance(matches, list):
                        matches = []
                    for m in matches:
                        if not isinstance(m, dict):
                            continue
                        ext_id = str(event.external_id)
                        match_ids = {
                            str(m.get('main_id', '')),
                            str(m.get('fallback_id_1', '')),
                            str(m.get('fallback_id_2', '')),
                            str(m.get('fallback_id_3', '')),
                            str(m.get('id', '')),
                        }
                        home_ext = str(event.home_entity.external_id) if event.home_entity and event.home_entity.external_id else None
                        away_ext = str(event.away_entity.external_id) if event.away_entity and event.away_entity.external_id else None
                        m_home = str(m.get('home', {}).get('id', ''))
                        m_away = str(m.get('away', {}).get('id', ''))

                        if ext_id in match_ids or (home_ext and home_ext != 'None' and m_home == home_ext) or (away_ext and away_ext != 'None' and m_away == away_ext):
                            # Merge fetched match stats into event metadata
                            if event.metadata:
                                event.metadata.update(m)
                            else:
                                event.metadata = m
                            event.save(update_fields=['metadata'])
                            meta = event.metadata
                            break
        except Exception as e:
            logger.warning(f"Failed to fetch soccer match stats for event {event.id}: {e}")

    # 2. Parse soccer-specific metadata structure
    if event.sport == 'soccer':
        EventTimeline.objects.filter(event=event).delete()
        EventLineup.objects.filter(event=event).delete()

        summary = meta.get('event_summary', {})
        if isinstance(summary, dict):
            for side in ['home', 'away']:
                team_entity = event.home_entity if side == 'home' else event.away_entity
                side_events = summary.get(side, {})
                if not isinstance(side_events, dict):
                    continue

                # Goals
                goals = side_events.get('goals', {})
                goals_list = goals.get('event', []) if isinstance(goals, dict) else []
                if isinstance(goals_list, dict):
                    goals_list = [goals_list]
                for g in goals_list:
                    if not isinstance(g, dict):
                        continue
                    try:
                        minute = _extract_minute(g.get('minute'))
                        extra = _extract_minute(g.get('extra_min'))
                        player_name = g.get('player_name', '')
                        assist_name = g.get('assist_player_name', '')
                        desc = player_name
                        if assist_name:
                            desc += f" (Assist: {assist_name})"
                        EventTimeline.objects.create(
                            event=event,
                            event_type='goal',
                            minute=minute,
                            extra_minute=extra,
                            team=team_entity,
                            description=desc,
                            metadata=g
                        )
                    except Exception as ex:
                        logger.warning(f"Failed to parse or create goal timeline entry for event {event.id}: {ex}")

                # Yellow Cards
                yc = side_events.get('yellowcards', {})
                yc_list = yc.get('event', []) if isinstance(yc, dict) else []
                if isinstance(yc_list, dict):
                    yc_list = [yc_list]
                for card in yc_list:
                    if not isinstance(card, dict):
                        continue
                    try:
                        minute = _extract_minute(card.get('minute'))
                        extra = _extract_minute(card.get('extra_min'))
                        player_name = card.get('player_name', '')
                        EventTimeline.objects.create(
                            event=event,
                            event_type='yellow_card',
                            minute=minute,
                            extra_minute=extra,
                            team=team_entity,
                            description=player_name,
                            metadata=card
                        )
                    except Exception as ex:
                        logger.warning(f"Failed to parse or create yellow card timeline entry for event {event.id}: {ex}")

                # Red Cards
                rc = side_events.get('redcards', {})
                rc_list = rc.get('event', []) if isinstance(rc, dict) else []
                if isinstance(rc_list, dict):
                    rc_list = [rc_list]
                for card in rc_list:
                    if not isinstance(card, dict):
                        continue
                    try:
                        minute = _extract_minute(card.get('minute'))
                        extra = _extract_minute(card.get('extra_min'))
                        player_name = card.get('player_name', '')
                        EventTimeline.objects.create(
                            event=event,
                            event_type='red_card',
                            minute=minute,
                            extra_minute=extra,
                            team=team_entity,
                            description=player_name,
                            metadata=card
                        )
                    except Exception as ex:
                        logger.warning(f"Failed to parse or create red card timeline entry for event {event.id}: {ex}")

                # Substitutions
                subs = side_events.get('substitutions', {})
                subs_list = subs.get('event', []) if isinstance(subs, dict) else []
                if isinstance(subs_list, dict):
                    subs_list = [subs_list]
                for sub in subs_list:
                    if not isinstance(sub, dict):
                        continue
                    try:
                        minute = _extract_minute(sub.get('minute'))
                        extra = _extract_minute(sub.get('extra_min'))
                        p_on = sub.get('player_on', '')
                        p_off = sub.get('player_off', '')
                        desc = f"IN: {p_on} — OUT: {p_off}"
                        EventTimeline.objects.create(
                            event=event,
                            event_type='substitution',
                            minute=minute,
                            extra_minute=extra,
                            team=team_entity,
                            description=desc,
                            metadata=sub
                        )
                    except Exception as ex:
                        logger.warning(f"Failed to parse or create substitution timeline entry for event {event.id}: {ex}")

        # Populate EventStatistics
        from apps.event.utils_stats import normalize_event_stats
        team_stats = meta.get('team_stats', {})
        has_statpal_team_stats = False
        if isinstance(team_stats, dict):
            for side in ['home', 'away']:
                team_entity = event.home_entity if side == 'home' else event.away_entity
                if not team_entity:
                    continue
                stats_dict = team_stats.get(side, {})
                if isinstance(stats_dict, dict):
                    lineups = meta.get('lineups', {})
                    if isinstance(lineups, dict) and side in lineups and isinstance(lineups[side], dict):
                        stats_dict['formation'] = lineups[side].get('formation')
                    if meta.get('penalties'):
                        stats_dict['penalties'] = meta.get('penalties')
                    flat_stats = normalize_event_stats(stats_dict)
                    if flat_stats:
                        has_statpal_team_stats = True
                        EventStatistics.objects.update_or_create(
                            event=event,
                            team=team_entity,
                            defaults={'stats': flat_stats}
                        )

        # Fallback: If StatPal has no extended team_stats, generate summary statistics from EventTimeline
        if not has_statpal_team_stats:
            for side in ['home', 'away']:
                team_entity = event.home_entity if side == 'home' else event.away_entity
                if not team_entity:
                    continue
                team_tl = EventTimeline.objects.filter(event=event, team=team_entity)
                score_val = event.home_score if side == 'home' else event.away_score
                fallback_stats = {
                    'side': side,
                    'goals': str(score_val if score_val is not None else team_tl.filter(event_type='goal').count()),
                    'yellowcards': str(team_tl.filter(event_type='yellow_card').count()),
                    'redcards': str(team_tl.filter(event_type='red_card').count()),
                    'substitutions': str(team_tl.filter(event_type='substitution').count()),
                    'is_fallback': True
                }
                EventStatistics.objects.update_or_create(
                    event=event,
                    team=team_entity,
                    defaults={'stats': fallback_stats}
                )

        # Populate EventLineups
        lineups = meta.get('lineups', {})
        if isinstance(lineups, dict):
            for side in ['home', 'away']:
                team_entity = event.home_entity if side == 'home' else event.away_entity
                if not team_entity:
                    continue
                players = lineups.get(side, {}).get('player', [])
                if isinstance(players, dict):
                    players = [players]
                for p in players:
                    if not isinstance(p, dict):
                        continue
                    player_name = p.get('name')
                    player_number = p.get('number')
                    player_pos = p.get('pos')
                    if player_name:
                        player_entity = get_or_create_precise_entity(
                            str(p.get('id', '')),
                            player_name,
                            'soccer',
                            entity_type='athlete'
                        )
                        EventLineup.objects.update_or_create(
                            event=event,
                            team=team_entity,
                            player=player_entity,
                            defaults={
                                'position_type': player_pos or '',
                                'jersey_number': int(player_number) if str(player_number).isdigit() else None
                            }
                        )

        ft = meta.get('ft')
        et = meta.get('et')
        if isinstance(et, dict) and (et.get('home_goals') is not None or et.get('away_goals') is not None):
            try:
                ft_home = int(ft.get('home_goals', 0) or 0) if isinstance(ft, dict) else 0
                ft_away = int(ft.get('away_goals', 0) or 0) if isinstance(ft, dict) else 0
                et_home = int(et.get('home_goals', 0) or 0)
                et_away = int(et.get('away_goals', 0) or 0)
                event.home_score = ft_home + et_home
                event.away_score = ft_away + et_away
                event.save(update_fields=['home_score', 'away_score'])
            except (ValueError, TypeError):
                pass
        elif isinstance(ft, dict):
            try:
                event.home_score = int(ft.get('home_goals', 0) or 0)
                event.away_score = int(ft.get('away_goals', 0) or 0)
                event.save(update_fields=['home_score', 'away_score'])
            except (ValueError, TypeError):
                pass
        if EventTimeline.objects.filter(event=event).exists():
            return

    raw_events = meta.get('events')
    if not raw_events:
        return

    # events can be a dict with 'event' key (list of events) or directly a list
    event_list = raw_events
    if isinstance(raw_events, dict):
        event_list = raw_events.get('event', [])
    if isinstance(event_list, dict):
        event_list = [event_list]
    if not isinstance(event_list, list):
        return

    # Clear old timeline entries before re-populating
    EventTimeline.objects.filter(event=event).delete()

    for ev in event_list:
        if not isinstance(ev, dict):
            continue

        try:
            ev_type_raw = ev.get('type', '').lower()
            team_side = ev.get('team', '')  # 'home' or 'away'
            minute = _extract_minute(ev.get('minute'))
            extra_min = _extract_minute(ev.get('extra_min'))

            type_map = {
                'goal': 'goal',
                'yellowcard': 'yellow_card',
                'yellow_card': 'yellow_card',
                'redcard': 'red_card',
                'red_card': 'red_card',
                'yellowred': 'red_card',
                'subst': 'substitution',
                'substitution': 'substitution',
                'penalty': 'penalty',
                'var': 'var',
            }
            mapped_type = type_map.get(ev_type_raw, ev_type_raw)

            team_entity = None
            if team_side == 'home':
                team_entity = event.home_entity
            elif team_side == 'away':
                team_entity = event.away_entity

            player_name = ev.get('player', '')
            result_text = ev.get('result', '')
            assist = ev.get('assist_player', '')
            description_parts = []
            if player_name:
                description_parts.append(player_name)
            if result_text:
                description_parts.append(result_text)
            if assist and assist.lower() not in ('', 'none'):
                description_parts.append(f"Assist: {assist}")
            player_on = ev.get('player_on', '')
            player_off = ev.get('player_off', '')
            if mapped_type == 'substitution':
                description_parts = []
                if player_on:
                    description_parts.append(f"IN: {player_on}")
                if player_off:
                    description_parts.append(f"OUT: {player_off}")

            description = ' — '.join(description_parts).strip(' —')

            EventTimeline.objects.create(
                event=event,
                event_type=mapped_type,
                minute=minute,
                extra_minute=extra_min,
                team=team_entity,
                player=None,
                description=description,
                metadata=ev,
            )
        except Exception as ex:
            logger.warning(f"Failed to parse or create timeline entry in flat list for event {event.id}: {ex}")

    # Update HT/FT/ET scores from metadata
    ht = meta.get('ht')
    ft = meta.get('ft')
    et = meta.get('et')
    if isinstance(et, dict) and (et.get('home_goals') is not None or et.get('away_goals') is not None):
        try:
            ft_home = int(ft.get('home_goals', 0) or 0) if isinstance(ft, dict) else 0
            ft_away = int(ft.get('away_goals', 0) or 0) if isinstance(ft, dict) else 0
            et_home = int(et.get('home_goals', 0) or 0)
            et_away = int(et.get('away_goals', 0) or 0)
            event.home_score = ft_home + et_home
            event.away_score = ft_away + et_away
            event.save(update_fields=['home_score', 'away_score'])
        except (ValueError, TypeError):
            pass
    elif isinstance(ft, dict):
        try:
            event.home_score = int(ft.get('home_goals', 0) or 0)
            event.away_score = int(ft.get('away_goals', 0) or 0)
            event.save(update_fields=['home_score', 'away_score'])
        except (ValueError, TypeError):
            pass

    scoreboard = {}
    if ht and isinstance(ht, dict):
        scoreboard['ht_home'] = ht.get('home_goals')
        scoreboard['ht_away'] = ht.get('away_goals')
    if ft and isinstance(ft, dict):
        scoreboard['ft_home'] = ft.get('home_goals')
        scoreboard['ft_away'] = ft.get('away_goals')
    et = meta.get('et')
    if et and isinstance(et, dict):
        scoreboard['et_home'] = et.get('home_goals')
        scoreboard['et_away'] = et.get('away_goals')
    penalties = meta.get('penalties')
    if penalties and isinstance(penalties, dict):
        scoreboard['pen_home'] = penalties.get('home_goals') or penalties.get('home')
        scoreboard['pen_away'] = penalties.get('away_goals') or penalties.get('away')

    if scoreboard and event.home_entity:
        EventStatistics.objects.update_or_create(
            event=event,
            team=event.home_entity,
            defaults={'stats': {**scoreboard, 'side': 'home'}},
        )
    if scoreboard and event.away_entity:
        EventStatistics.objects.update_or_create(
            event=event,
            team=event.away_entity,
            defaults={'stats': {**scoreboard, 'side': 'away'}},
        )

    logger.info(f"_populate_statpal_event_details: populated timeline for event {event.id}")


def _on_the_fly_update_statpal_event(event) -> bool:
    """Fetch and update live or completed match details on-the-fly for a StatPal event.

    Args:
        event (Event): Event model instance to refresh.

    Returns:
        bool: True if latest fixture data was retrieved and saved successfully.
    """
    from apps.sports_apis.services.statpal import statpal_service
    from .helpers import _save_event
    
    sport = event.sport
    today = timezone.now().date()
    offset = (event.start_time.date() - today).days
    
    configs = {
        "soccer": (statpal_service.get_soccer_fixtures, _soccer_rows),
        "nba": (statpal_service.get_nba_fixtures, _nba_rows),
        "football": (statpal_service.get_nfl_fixtures, _nfl_rows),
        "tennis": (statpal_service.get_tennis_fixtures, _tennis_rows),
        "baseball": (statpal_service.get_mlb_fixtures, _mlb_rows),
        "handball": (statpal_service.get_handball_fixtures, _handball_rows),
    }
    
    if sport in configs:
        fetch_fn, extract_fn = configs[sport]
        try:
            if sport == "soccer" or (-7 <= offset <= 7):
                if offset == 0 and sport in ["tennis", "baseball", "handball"]:
                    res = statpal_service.get_live_scores(sport)
                else:
                    res = fetch_fn(offset=offset)
                
                if res.get('success'):
                    rows = extract_fn(res['data'])
                    for row in rows:
                        if str(row.get("external_id")) == str(event.external_id):
                            _save_event(row)
                            return True
        except Exception as e:
            logger.warning(f"On-the-fly fixtures update failed for event {event.id} ({sport}): {e}")
            
    try:
        res = statpal_service.get_live_scores(sport)
        if res.get('success'):
            extract_fn = {
                "soccer": _soccer_rows,
                "nba": _nba_rows,
                "football": _nfl_rows,
                "tennis": _tennis_rows,
                "baseball": _mlb_rows,
                "handball": _handball_rows,
                "cricket": _cricket_rows,
                "golf": _golf_rows,
                "volleyball": _volleyball_rows,
                "horse_racing": _horse_racing_rows,
            }.get(sport)
            if extract_fn:
                rows = extract_fn(res['data'])
                for row in rows:
                    if str(row.get("external_id")) == str(event.external_id):
                        _save_event(row)
                        return True
    except Exception as e:
        logger.warning(f"On-the-fly live scores fetch failed for event {event.id}: {e}")
        
    if sport == "cricket" and event.league and event.league.external_id:
        try:
            res = statpal_service.get_cricket_tournaments()
            if res.get('success'):
                cats = res.get('data', {}).get('tours', {}).get('category', [])
                if isinstance(cats, dict):
                    cats = [cats]
                
                tour = next((c for c in cats if str(c.get('id')) == str(event.league.external_id)), None)
                if tour and tour.get('schedule_uri'):
                    parts = tour['schedule_uri'].strip('/').split('/')
                    if len(parts) >= 2:
                        t_type, t_id = parts[0], parts[1]
                        sched_res = statpal_service.get_cricket_schedule(t_type, t_id)
                        if sched_res.get('success'):
                            rows = _cricket_rows(sched_res['data'])
                            for row in rows:
                                if str(row.get("external_id")) == str(event.external_id):
                                    _save_event(row)
                                    return True
        except Exception as e:
            logger.warning(f"On-the-fly cricket tournament schedule fetch failed for event {event.id}: {e}")

    if sport == "soccer":
        try:
            _populate_statpal_event_details(event)
            return True
        except Exception:
            pass
            
    return False


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def fetch_event_details(self, event_id: int):
    """Fetch and populate full statistics, lineups, and timeline events for a completed event.

    Args:
        self: Bound Celery task instance.
        event_id (int): Primary key ID of the Event fixture.

    Returns:
        str: Task execution summary message.
    """
    try:
        event = Event.objects.select_related(
            'home_entity', 'away_entity', 'league'
        ).get(id=event_id)
    except Event.DoesNotExist:
        return f"Event {event_id} not found"

    _populate_statpal_event_details(event)

    if not isinstance(event.metadata, dict):
        event.metadata = {}
    event.metadata['details_checked'] = True
    event.save(update_fields=['metadata'])

    try:
        from apps.sports_apis.tasks import fetch_highlight_for_event
        fetch_highlight_for_event.apply_async(args=[event_id], countdown=900)
    except Exception as e:
        logger.error(f"Failed to queue highlight fetch for event {event_id}: {e}")
    return f"Event {event_id} (statpal) details populated"


@shared_task
def check_completed_events():
    """Backup synchronization task ensuring completed matches have populated statistics and timeline data.

    Returns:
        str: Summary of triggered event detail background tasks.
    """
    cutoff_recent = timezone.now() - timedelta(days=1)
    candidates = (
        Event.objects
        .filter(
            status='completed',
            sport='soccer',
            api_source='statpal',
            start_time__gte=cutoff_recent,
        )
        .order_by('-start_time')
    )

    to_process = [
        e for e in candidates[:100]
        if not (isinstance(e.metadata, dict) and e.metadata.get('backup_checked'))
    ][:20]

    count = 0
    for event in to_process:
        if not isinstance(event.metadata, dict):
            event.metadata = {}
        event.metadata['backup_checked'] = True
        event.metadata['details_checked'] = True
        event.save(update_fields=['metadata'])

        fetch_event_details.delay(event.id)
        count += 1

    if count > 0:
        logger.info(f"check_completed_events (backup): triggered {count} detail fetches")
    return f"Triggered {count} backup event detail fetches"


@shared_task
def cleanup_stale_live_events():
    """Auto-transition events that have been in 'live' status for over 5 hours into 'completed'."""
    cutoff = timezone.now() - timedelta(hours=5)
    stale = Event.objects.filter(
        status='live',
        start_time__lte=cutoff,
    )
    count = stale.update(status='completed')
    logger.info(f"Cleaned up {count} stale live events")
    return f"Cleaned {count} stale live events"


def reprocess_all_events_stats() -> str:
    """Reprocess and backfill all existing EventStatistics according to updated normalization schemas.

    Returns:
        str: Reprocessing summary report with updated and created statistics counts.
    """
    from apps.event.models import Event, EventStatistics
    from apps.event.utils_stats import normalize_event_stats

    events = Event.objects.filter(sport='soccer').prefetch_related('statistics', 'timeline')
    updated = 0
    created = 0

    for ev in events:
        stats_list = list(ev.statistics.all())
        has_real = False
        if stats_list:
            for s in stats_list:
                if s.stats and isinstance(s.stats, dict):
                    norm = normalize_event_stats(s.stats)
                    if norm:
                        s.stats = norm
                        s.save(update_fields=['stats'])
                        updated += 1
                        if not norm.get('is_fallback'):
                            has_real = True

        if not has_real and (ev.home_score is not None or ev.away_score is not None) and ev.home_entity and ev.away_entity:
            for side, team in [('home', ev.home_entity), ('away', ev.away_entity)]:
                team_tl = ev.timeline.filter(team=team)
                score_val = ev.home_score if side == 'home' else ev.away_score

                fallback_stats = {
                    'side': side,
                    'goals': str(score_val if score_val is not None else team_tl.filter(event_type='goal').count()),
                    'yellowcards': str(team_tl.filter(event_type='yellow_card').count()),
                    'redcards': str(team_tl.filter(event_type='red_card').count()),
                    'substitutions': str(team_tl.filter(event_type='substitution').count()),
                    'is_fallback': True,
                    'ft_home': str(ev.home_score or 0),
                    'ft_away': str(ev.away_score or 0),
                }
                norm_fb = normalize_event_stats(fallback_stats)
                if norm_fb:
                    EventStatistics.objects.update_or_create(
                        event=ev,
                        team=team,
                        defaults={'stats': norm_fb}
                    )
                    created += 1
    return f"Reprocessed stats for {events.count()} soccer matches! Updated: {updated}, Created Fallbacks: {created}."
