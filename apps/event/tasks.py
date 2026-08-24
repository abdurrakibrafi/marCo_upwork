from celery import shared_task
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone
from datetime import datetime, timedelta
from apps.event.models import Event, EventStatistics, EventLineup, EventPlayerStats, EventTimeline
from apps.score.models import LiveScore
from apps.sports_apis.tasks import _publish
import logging
from django.utils.timezone import make_aware
import time
import requests as req
from apps.entity.utils.matcher import get_or_create_precise_entity
from apps.entity.models import Entity
from apps.sports_apis.services.statpal import statpal_service

logger = logging.getLogger(__name__)


# ================================================================
# NFL FIXTURES (BallDontLie — StatPal doesn't cover NFL)
# ================================================================

@shared_task
def update_nfl_fixtures(dates: list[str] = None):
    """Synchronize NFL fixtures using StatPal provider endpoints.

    Args:
        dates (list[str], optional): List of ISO date strings (YYYY-MM-DD) to fetch.

    Returns:
        str: Summary of updated fixtures count.
    """
    if not dates:
        dates = [timezone.now().date().isoformat()]

    total_updated = 0
    for date in dates:
        logger.info(f"Updating NFL fixtures for {date} using StatPal")
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
            offset = (target_date - timezone.now().date()).days
        except Exception:
            offset = 0

        result = statpal_service.get_nfl_fixtures(offset=offset)
        if result['success']:
            rows = _nfl_rows(result['data'])
            for row in rows:
                _save_event(row)
            total_updated += len(rows)
            logger.info(f"NFL: Updated {len(rows)} fixtures for {date} using StatPal")
        time.sleep(1)
    return f"NFL: {total_updated} fixtures updated"


# ================================================================
# SOCCER FIXTURES (StatPal V2)
# ================================================================

@shared_task
def update_soccer_fixtures(date=None):
    """Synchronize soccer match fixtures for a specific date using StatPal.

    Args:
        date (str, optional): ISO date string (YYYY-MM-DD). Defaults to today.

    Returns:
        str: Result summary string.
    """
    if not date:
        date = timezone.now().date().isoformat()
    
    logger.info(f"Updating soccer fixtures for {date} using StatPal")
    
    try:
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
        offset = (target_date - timezone.now().date()).days
    except Exception:
        offset = 0

    result = statpal_service.get_soccer_fixtures(offset=offset)
    if result['success']:
        rows = _soccer_rows(result['data'])
        for row in rows:
            _save_event(row)
        logger.info(f"Soccer: Updated {len(rows)} fixtures for {date} using StatPal")
        return f"Soccer: {len(rows)} fixtures updated"
    
    return "Soccer fixtures update failed"


# ================================================================
# ALL FIXTURES ORCHESTRATOR
# ================================================================

@shared_task
def update_statpal_fixtures_for_dates(dates: list[str] = None):
    """Fetch and synchronize upcoming/past fixtures from StatPal across all sports.

    Orchestrates ingestion for Soccer, NBA, NFL, Cricket, Tennis, Baseball, and Handball.

    Args:
        dates (list[str], optional): List of date strings in YYYY-MM-DD format.

    Returns:
        str: Completion summary of saved and updated fixtures.
    """
    if not dates:
        dates = [timezone.now().date().isoformat()]

    parsed_dates = []
    for d in dates:
        try:
            parsed_dates.append(datetime.strptime(d, "%Y-%m-%d").date())
        except Exception:
            pass
    parsed_dates.sort()

    # Cricket: Update all cricket fixtures (does not take date offset, returns bulk future/current schedule)
    try:
        logger.info("StatPal: Fetching cricket fixtures")
        result = statpal_service.get_cricket_fixtures()
        if result.get('success'):
            rows = _cricket_rows(result['data'])
            for row in rows:
                _save_event(row)
            logger.info(f"StatPal: Saved {len(rows)} cricket fixtures")
    except Exception as exc:
        logger.exception("Cricket fixtures update failed: %s", exc)

    # Daily offset sports
    sports_configs = [
        ("soccer", statpal_service.get_soccer_fixtures, _soccer_rows),
        ("nba", statpal_service.get_nba_fixtures, _nba_rows),
        ("football", statpal_service.get_nfl_fixtures, _nfl_rows),
        ("tennis", statpal_service.get_tennis_fixtures, _tennis_rows),
        ("baseball", statpal_service.get_mlb_fixtures, _mlb_rows),
        ("handball", statpal_service.get_handball_fixtures, _handball_rows),
    ]

    today = timezone.now().date()
    total_saved = 0
    for target_date in parsed_dates:
        offset = (target_date - today).days
        date_str = target_date.isoformat()

        for sport, fetch_fn, extract_fn in sports_configs:
            # Skip offset 0 for tennis, baseball, handball since they return empty or not supported
            if offset == 0 and sport in ["tennis", "baseball", "handball"]:
                continue

            try:
                logger.info(f"StatPal: Fetching {sport} fixtures for {date_str} (offset={offset})")
                res = fetch_fn(offset=offset)
                if res.get('success'):
                    rows = extract_fn(res['data'])
                    for row in rows:
                        _save_event(row)
                    total_saved += len(rows)
                    logger.info(f"StatPal: Saved {len(rows)} {sport} fixtures for {date_str}")
            except Exception as exc:
                logger.exception(f"StatPal: {sport} fixtures failed for {date_str}: %s", exc)
            
            time.sleep(0.5)  # Throttling prevention

    return f"Completed: Saved/Updated {total_saved} fixtures across daily sports."


@shared_task
def update_all_fixtures():
    """Trigger background fixture synchronization covering the historical past 30 days to upcoming 90 days.

    Returns:
        str: Dispatched task notification message.
    """
    dates = [
        (timezone.now().date() + timedelta(days=i)).isoformat()
        for i in range(-30, 91)
    ]
    update_statpal_fixtures_for_dates.delay(dates)
    logger.info(f"update_all_fixtures: Triggered update_statpal_fixtures_for_dates for {len(dates)} days.")
    return f"Fixture updates triggered for {dates[0]} to {dates[-1]}"


# ================================================================
# HELPERS
# ================================================================

def _get_or_create_team_entity(api_source: str, external_id: str, name: str, sport: str, logo_url: str = ''):
    """Retrieve or create a team Entity and associated Team model record.

    Args:
        api_source (str): Source identifier (e.g. 'statpal', 'thesportsdb').
        external_id (str): Remote ID.
        name (str): Team name.
        sport (str): Sport slug.
        logo_url (str, optional): Team badge/logo URL.

    Returns:
        Entity: Resolved team entity instance.
    """
    entity, created = Entity.objects.get_or_create(
        api_source=api_source,
        external_id=str(external_id),
        type='team',
        defaults={
            'name': name,
            'sport': sport,
            'logo_url': logo_url or '',
            'has_api_data': True,
        }
    )

    if not created and logo_url and not entity.logo_url:
        entity.logo_url = logo_url
        entity.save(update_fields=['logo_url'])

    from apps.entity.models import Team
    Team.objects.get_or_create(entity=entity)
    return entity


def _get_or_create_league_entity(api_source: str, external_id: str, name: str, sport: str, logo_url: str = ''):
    """Retrieve or create a league Entity and associated League model record.

    Args:
        api_source (str): Source identifier.
        external_id (str): Remote ID.
        name (str): League name.
        sport (str): Sport slug.
        logo_url (str, optional): League logo URL.

    Returns:
        Entity: Resolved league entity instance.
    """
    entity, created = Entity.objects.get_or_create(
        api_source=api_source,
        external_id=str(external_id),
        type='league',
        defaults={
            'name': name,
            'sport': sport,
            'logo_url': logo_url or '',
            'has_api_data': True,
        }
    )

    if not created and logo_url and not entity.logo_url:
        entity.logo_url = logo_url
        entity.save(update_fields=['logo_url'])

    from apps.entity.models import League
    League.objects.get_or_create(entity=entity)
    return entity


# ================================================================
# SOCCER LIVE SCORES ONLY (API-Sports light update)
# ================================================================

@shared_task
def update_soccer_live_scores_only():
    """Trigger background synchronization of soccer live scores via StatPal."""
    sync_statpal_data.delay()
    return "Delegated to sync_statpal_data"


# ================================================================
# MATCH DETAILS (deep stats for completed games)
# ================================================================

def _extract_minute(val) -> int:
    """Extract numeric match minute from string or int representations (e.g. '45+2').

    Args:
        val (Any): Input minute representation.

    Returns:
        int: Parsed integer minute.
    """
    import re
    if not val:
        return 0
    val_str = str(val).strip()
    match = re.search(r'\d+', val_str)
    if match:
        try:
            return int(match.group(0))
        except (ValueError, TypeError):
            return 0
    return 0


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

            # Map StatPal event types to our model types
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

            # Resolve team entity
            team_entity = None
            if team_side == 'home':
                team_entity = event.home_entity
            elif team_side == 'away':
                team_entity = event.away_entity

            # Build description
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
            # For substitutions
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
                player=None,  # StatPal doesn't provide player entity IDs
                description=description,
                metadata=ev,
            )
        except Exception as ex:
            logger.warning(f"Failed to parse or create timeline entry in flat list for event {event.id}: {ex}")

    # Update HT/FT/ET scores from metadata (Option B: sum ft + et)
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

    # Store scoreboard breakdown as EventStatistics
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
    
    sport = event.sport
    today = timezone.now().date()
    offset = (event.start_time.date() - today).days
    
    # Check if the sport is a daily offset sport
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
            # For offset sports, offset must be in -7 to 7 range (except soccer)
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
            
    # For other/all sports (or if daily lookup failed), try live scores endpoint
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
        
    # For cricket, try the tournament schedule if we have a league
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

    # Special fallback for soccer match stats
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

    # ── StatPal / default events: parse metadata for timeline/scores ──
    _populate_statpal_event_details(event)

    # Mark as checked in metadata so background tasks do not poll it repeatedly
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


# ================================================================
# STATPAL — Unified Sync (Event + LiveScore for Soccer/NBA/Cricket)
# ================================================================

_FINISHED = {
    "ft", "aet", "pen", "finished", "after over time",
    "full-time", "retired", "walk over", "walkover", "awarded",
}

_CANCELLED = {
    "cancelled", "cancl", "abandoned", "abd", "canc",
}

_LIVE = {
    # General
    "1h", "2h", "ht", "et", "bt", "p", "susp", "int", "live",
    "in progress", "in play",
    # Basketball
    "q1", "q2", "q3", "q4", "ot", "halftime",
    # Tennis
    "1st set", "2nd set", "3rd set", "4th set", "5th set", "break",
    "set 1", "set 2", "set 3", "set 4", "set 5",
    # Cricket
    "stumps", "innings break", "lunch", "tea", "rain delay",
}


def _map_status(raw: str, sport: str = None, metadata: dict = None) -> str:
    """Normalize inconsistent provider match status strings into standard internal choices.

    Maps varied raw statuses (e.g. 'FT', 'AET', 'Q3', 'Set 2', 'Rain Delay') to:
    'upcoming', 'live', 'completed', or 'cancelled'.

    Args:
        raw (str): Raw status string from data provider.
        sport (str, optional): Sport slug identifier.
        metadata (dict, optional): Match metadata for multi-set analysis.

    Returns:
        str: Normalized status choice string.
    """
    import re
    if not raw:
        return "upcoming"

    raw_normalized = raw.lower().strip().rstrip('.')

    # soccer-only numeric minute check
    if sport == "soccer" and re.match(r"^\d+(\+\d+)?$", raw_normalized):
        return "live"

    if raw_normalized in _CANCELLED:
        return "cancelled"

    if raw_normalized in _FINISHED or "final" in raw_normalized:
        return "completed"

    if raw_normalized in _LIVE:
        return "live"

    # Tennis-specific live check via populated score fields
    if sport == "tennis" and metadata:
        players = metadata.get("player", [])
        if isinstance(players, list):
            score_populated = False
            for p in players:
                if not isinstance(p, dict):
                    continue
                for key in ["s1", "s2", "s3", "s4", "s5", "totalscore"]:
                    if str(p.get(key, "")).strip() != "":
                        score_populated = True
                        break
                if score_populated:
                    break
            if score_populated:
                return "live"
        
    # Check for Baseball live inning indicators (e.g., "Top 5th", "Bottom 8th", "End 6th", "Middle 2nd")
    if any(ind in raw_normalized for ind in ["top ", "bottom ", "middle ", "end "]):
        if any(ind in raw_normalized for ind in ["1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th", "10th", "st", "nd", "rd", "th"]):
            return "live"
    if "inning" in raw_normalized:
        return "live"
        
    return "upcoming"


def _parse_dt(date_str: str, time_str: str) -> datetime:
    """Parse date and time strings (DD.MM.YYYY HH:MM) into a timezone-aware datetime.

    Args:
        date_str (str): Date string.
        time_str (str): Time string.

    Returns:
        datetime: Aware datetime object.
    """
    try:
        naive = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
        return timezone.make_aware(naive, timezone.get_current_timezone())
    except Exception:
        return timezone.now()


def _safe_int(val) -> int | None:
    """Safely convert numeric string or object into an integer, ignoring non-numeric characters.

    Args:
        val (Any): Input value.

    Returns:
        int or None: Parsed integer or None.
    """
    if val is None or str(val).strip() in ("", "?", "-", "None", "null", "undefined"):
        return None
    try:
        return int(str(val).split("/")[0].split("&")[0].strip())
    except Exception:
        return None


def _soccer_rows(data: dict) -> list:
    """Extract and normalize soccer match fixture dictionaries from raw StatPal response payload.

    Args:
        data (dict): StatPal soccer API response.

    Returns:
        list: Normalized fixture dictionaries.
    """
    root_data = None
    if "live_matches" in data:
        root_data = data["live_matches"]
    else:
        for v in data.values():
            if isinstance(v, dict) and "league" in v:
                root_data = v
                break

    if not root_data:
        return []

    leagues = root_data.get("league", [])
    if isinstance(leagues, dict): # Ensure leagues is always a list
        leagues = [leagues]

    rows = []
    for lg in leagues:
        matches = lg.get("match", [])
        if isinstance(matches, dict):
            matches = [matches]
        elif not isinstance(matches, list):
            matches = []

        for m in matches:
            # BUG FIX: API can sometimes return a string instead of a match dict
            if not isinstance(m, dict):
                continue

            home = m.get("home", {})
            away = m.get("away", {})
            rows.append({
                "external_id": str(m.get("main_id") or m.get("id", "")),
                "sport": "soccer",
                "league_id":   str(lg.get("id", "")),
                "league_name": lg.get("name", ""),
                "home_id":   str(home.get("id", "")),
                "home_name": home.get("name", ""),
                "away_id":   str(away.get("id", "")),
                "away_name": away.get("name", ""),
                "home_score": _safe_int(home.get("goals") or home.get("score")),
                "away_score": _safe_int(away.get("goals") or away.get("score")),
                "status_raw": m.get("status", "NS"),
                "date":  m.get("date", ""),
                "time":  m.get("time", "00:00"),
                "venue": m.get("venue", ""),
                "raw":   m,
            })
    return rows


def _generic_sport_rows(data: dict, sport_name: str) -> list:
    """Extract and normalize match fixtures for generic multi-tournament sports (NBA, NFL, NHL, Tennis, etc.).

    Args:
        data (dict): Raw StatPal API response payload.
        sport_name (str): Sport slug identifier.

    Returns:
        list: Normalized fixture dictionaries.
    """
    tournaments_data = (
        data.get("livescores", {}).get("tournament")
        or data.get("scores", {}).get("tournament", {})
        or []
    )

    # API sometimes sends dict, sometimes list of dicts. Normalize to list.
    if isinstance(tournaments_data, dict):
        tournaments_data = [tournaments_data]

    rows = []
    for tournament in tournaments_data:
        if not isinstance(tournament, dict):
            continue

        league_id   = str(tournament.get("id", ""))
        league_name = tournament.get("league") or tournament.get("name") or ""

        matches = tournament.get("match", [])
        if isinstance(matches, dict):
            matches = [matches]
        elif not isinstance(matches, list):
            matches = []

        for m in matches:
            if not isinstance(m, dict):
                continue
            home = m.get("home", {})
            away = m.get("away", {})

            # Tennis and other individual sports may use player array instead of home/away keys
            players = m.get("player", [])
            if (not home or not away) and isinstance(players, list) and len(players) >= 2:
                home = players[0]
                away = players[1]

            rows.append({
                "external_id": str(m.get("id", "")),
                "sport": sport_name,
                "league_id":   league_id,
                "league_name": league_name,
                "home_id":   str(home.get("id", "")),
                "home_name": home.get("name", ""),
                "away_id":   str(away.get("id", "")),
                "away_name": away.get("name", ""),
                "home_score": _safe_int(home.get("totalscore")),
                "away_score": _safe_int(away.get("totalscore")),
                "status_raw": m.get("status", "NS"),
                "date":  m.get("date", ""),
                "time":  m.get("time", "00:00"),
                "venue": m.get("venue", ""),
                "raw":   m,
            })
    return rows


def _nba_rows(data: dict) -> list:
    """Extract NBA basketball match fixture rows from StatPal response."""
    return _generic_sport_rows(data, "nba")


def _nfl_rows(data: dict) -> list:
    """Extract American football (NFL) match fixture rows from StatPal response."""
    return _generic_sport_rows(data, "football")


def _hockey_rows(data: dict) -> list:
    """Extract ice hockey match fixture rows from StatPal response."""
    return _generic_sport_rows(data, "hockey")


def _tennis_rows(data: dict) -> list:
    """Extract tennis match fixture rows from StatPal response."""
    return _generic_sport_rows(data, "tennis")


def _mlb_rows(data: dict) -> list:
    """Extract baseball (MLB) match fixture rows from StatPal response."""
    return _generic_sport_rows(data, "baseball")


def _handball_rows(data: dict) -> list:
    """Extract handball match fixture rows from StatPal response."""
    return _generic_sport_rows(data, "handball")


def _volleyball_rows(data: dict) -> list:
    """Extract volleyball match fixture rows from StatPal response."""
    return _generic_sport_rows(data, "volleyball")


def _cricket_rows(data: dict) -> list:
    """Extract cricket match fixture rows from StatPal response payload.

    Args:
        data (dict): Raw cricket scores response.

    Returns:
        list: Normalized cricket fixture dictionaries.
    """
    categories = (
        data.get("scores", {}).get("category", [])
        or data.get("fixtures", {}).get("category", [])
    )
    if isinstance(categories, dict):
        categories = [categories]

    rows = []
    for cat in categories:
        m = cat.get("match")
        if not m:
            continue
        match_list = m if isinstance(m, list) else [m]
        for match in match_list:
            home = match.get("home", {})
            away = match.get("away", {})
            rows.append({
                "external_id": str(match.get("id", "")),
                "sport": "cricket",
                "league_id":   str(cat.get("id", "")),
                "league_name": cat.get("name", ""),
                "home_id":   str(home.get("id", "")),
                "home_name": home.get("name", ""),
                "away_id":   str(away.get("id", "")),
                "away_name": away.get("name", ""),
                "home_score": _safe_int(home.get("totalscore")),
                "away_score": _safe_int(away.get("totalscore")),
                "status_raw": match.get("status", "NS"),
                "date":  match.get("date", ""),
                "time":  match.get("time", "00:00"),
                "venue": match.get("venue", ""),
                "raw":   match,
            })
    return rows


def _f1_rows(data: dict) -> list:
    """Extract Formula 1 grand prix race events from StatPal response payload.

    Args:
        data (dict): Raw F1 race response.

    Returns:
        list: Normalized F1 race event dictionaries.
    """
    races_data = data.get("livescores", {}).get("tournament") or data.get("tournament") or data.get("races")
    if not races_data:
        return []

    tournaments = races_data if isinstance(races_data, list) else [races_data]
    rows = []
    for tour in tournaments:
        if not isinstance(tour, dict):
            continue
        race_id = str(tour.get("id", ""))
        race_name = tour.get("name", "Formula 1 Grand Prix")
        rows.append({
            "external_id": f"f1_{race_id}",
            "sport": "f1",
            "league_id": race_id,
            "league_name": "Formula 1",
            "home_id": f"f1_{race_id}",
            "home_name": race_name,
            "away_id": None,
            "away_name": None,
            "home_score": None,
            "away_score": None,
            "status_raw": tour.get("status", "NS"),
            "date": tour.get("date", tour.get("start_date", "")),
            "time": tour.get("time", "00:00"),
            "venue": tour.get("circuit", tour.get("venue", "")),
            "raw": tour,
        })
    return rows


def _golf_position_sort_key(p) -> int:
    """Safely convert player leaderboard position string (e.g. 'T1', 'CUT') into an integer sort key.

    Args:
        p (dict): Golf player record.

    Returns:
        int: Numeric sort key.
    """
    pos = p.get('pos', '999')
    if not pos:
        return 999
    pos = str(pos).lstrip('T').strip()  # "T1" -> "1"
    try:
        return int(pos)
    except (ValueError, TypeError):
        return 999

        

def _golf_rows(data: dict) -> list:
    """Extract golf tournament event and leaderboard rows from StatPal response payload.

    Args:
        data (dict): Raw golf tournament payload.

    Returns:
        list: Normalized golf tournament event dictionaries.
    """
    tour_data = (
        data.get("livescore", {}).get("tournament")
        or data.get("fixtures", {}).get("tournament")
        or data.get("tournament")
    )
    if not tour_data:
        return []

    tournaments = tour_data if isinstance(tour_data, list) else [tour_data]
    rows = []
    for tour in tournaments:
        if not isinstance(tour, dict):
            continue
        league_name = tour.get("name", "Golf Event")
        league_id = str(tour.get("id", ""))
        players = tour.get("player", [])
        if isinstance(players, dict):
            players = [players]
        elif not isinstance(players, list):
            players = []

        leader_score, leader_name = None, None
        if players:
            try:
                leader = sorted(players, key=_golf_position_sort_key)[0]
                leader_name = leader.get('name')
                leader_score = leader.get('par')
            except Exception:
                pass

        rows.append({
            "external_id": f"golf_{league_id}",
            "sport": "golf",
            "league_id": league_id,
            "league_name": league_name,
            "home_id": f"golf_{league_id}",
            "home_name": league_name,
            "away_id": None,
            "away_name": None,
            "home_score": leader_score,
            "away_score": tour.get('par'),
            "status_raw": tour.get("status", "NS"),
            "date": tour.get("start_date", timezone.now().strftime("%d.%m.%Y")),
            "time": "00:00",
            "venue": tour.get("venue", ""),
            "raw": tour,
        })
    return rows


def _horse_racing_rows(data: dict) -> list:
    """Extract horse racing tournament fixture rows from StatPal response payload.

    Args:
        data (dict): Raw horse racing response.

    Returns:
        list: Normalized horse race event dictionaries.
    """
    tournaments = data.get("scores", {}).get("tournament", [])
    if not isinstance(tournaments, list):
        tournaments = [tournaments]

    rows = []
    for tour in tournaments:
        races = tour.get("race", [])
        if not isinstance(races, list):
            races = [races]

        for race in races:
            race_id = str(race.get("id", ""))
            race_name = race.get("name", "Horse Race")
            
            rows.append({
                "external_id": f"hr_{race_id}",
                "sport": "horse_racing",
                "league_id": str(tour.get("id", "")),
                "league_name": tour.get("name", "Racecourse"),
                "home_id": f"hr_{race_id}",
                "home_name": race_name,
                "away_id": None,
                "away_name": None,
                "home_score": None,
                "away_score": None,
                "status_raw": race.get("status", "NS"),
                "date": tour.get("date", ""),
                "time": race.get("time", "00:00"),
                "venue": tour.get("name", ""),
                "raw": race,
            })
    return rows


def _clean_score(val) -> int | None:
    """Clean and parse raw match score value into an integer.

    Args:
        val (Any): Input score value.

    Returns:
        int or None: Parsed integer score or None.
    """
    if val is None or str(val).strip() in ("", "?", "-", "None", "null", "undefined"):
        return None
    try:
        return int(str(val).split("/")[0].split("&")[0].strip())
    except Exception:
        return None


def _save_event(row: dict, api_source: str = "statpal") -> Event | None:
    """Save or update an Event record in the database from a normalized fixture row.

    Resolves participants, venues, and status mappings, triggering detail population on completion.

    Args:
        row (dict): Normalized fixture data row.
        api_source (str, optional): API source identifier. Defaults to 'statpal'.

    Returns:
        Event or None: Persisted Event instance, or None if skipped/cancelled.
    """
    status = _map_status(row["status_raw"], sport=row.get("sport"), metadata=row.get("raw"))
    if status is None:
        if row.get("external_id"):
            LiveScore.objects.filter(sport=row["sport"], external_id=row["external_id"]).delete()
        return None
    if not row["external_id"]:
        return None

    # Prevent overwriting "live" status with "upcoming" from subsequent fixture updates
    existing_event = Event.objects.filter(external_id=row["external_id"]).first()
    if existing_event and existing_event.status == "live" and status == "upcoming":
        status = "live"
        row["status_raw"] = existing_event.status_detail
        if existing_event.metadata and not row["raw"].get("inning") and not row["raw"].get("events"):
            row["raw"] = existing_event.metadata

    sport = row["sport"]
    league = get_or_create_precise_entity(
        row["league_id"], row["league_name"], sport, entity_type="league"
    )
    home = get_or_create_precise_entity(
        row["home_id"], row["home_name"], sport, entity_type="team"
    )
    from apps.entity.utils.matcher import normalize_statpal_logo_url
    if row.get("home_logo") and not home.logo_url:
        norm_logo = normalize_statpal_logo_url(row["home_logo"], home.name, "team", sport)
        if norm_logo:
            home.logo_url = norm_logo
            home.save(update_fields=['logo_url'])
    # For individual sports, away entity can be null
    away = None
    if row.get("away_id") and row.get("away_name"):
        away = get_or_create_precise_entity(
            row["away_id"], row["away_name"], sport, entity_type="team"
        )
        if row.get("away_logo") and not away.logo_url:
            norm_logo = normalize_statpal_logo_url(row["away_logo"], away.name, "team", sport)
            if norm_logo:
                away.logo_url = norm_logo
                away.save(update_fields=['logo_url'])
    # Prevent fixture updates or incomplete rows from wiping out live scores
    home_score_val = _clean_score(row.get("home_score"))
    away_score_val = _clean_score(row.get("away_score"))
    if existing_event and home_score_val is None and existing_event.home_score is not None:
        home_score_val = existing_event.home_score
    if existing_event and away_score_val is None and existing_event.away_score is not None:
        away_score_val = existing_event.away_score

    start_time = _parse_dt(row["date"], row["time"])

    metadata_val = row["raw"]
    if existing_event and existing_event.metadata:
        merged_meta = dict(existing_event.metadata)
        if isinstance(row.get("raw"), dict):
            merged_meta.update(row["raw"])
        metadata_val = merged_meta

    # Extract venue fields from row or metadata
    venue_val = row.get("venue") or (existing_event.venue_name if existing_event else "")
    venue_city_val = row.get("venue_city") or (existing_event.venue_city if existing_event else "")
    venue_country_val = row.get("venue_country") or (existing_event.venue_country if existing_event else "")

    if isinstance(metadata_val, dict):
        matchinfo = metadata_val.get('matchinfo', {})
        if isinstance(matchinfo, dict):
            for itm in matchinfo.get('info', []):
                if isinstance(itm, dict):
                    i_name = str(itm.get('name', '')).strip().lower()
                    i_val = str(itm.get('value', '')).strip()
                    if i_name in ('venue', 'stadium') and not venue_val:
                        venue_val = i_val
                    elif i_name in ('city', 'location') and not venue_city_val:
                        venue_city_val = i_val
                    elif i_name in ('country', 'nation') and not venue_country_val:
                        venue_country_val = i_val

    event, _ = Event.objects.update_or_create(
        api_source=api_source,
        external_id=row["external_id"],
        defaults={
            "sport":         sport,
            "home_entity":   home,
            "away_entity":   away,
            "league":        league,
            "status":        status,
            "status_detail": row["status_raw"],
            "home_score":    home_score_val,
            "away_score":    away_score_val,
            "venue_name":    venue_val,
            "venue_city":    venue_city_val,
            "venue_country": venue_country_val,
            "start_time":    start_time,
            "metadata":      metadata_val,
        },
    )
    if status == "completed":
        try:
            _populate_statpal_event_details(event)
        except Exception as e:
            logger.warning(f"Failed to auto-populate statpal event details for {event.id}: {e}")
    return event


def _save_livescore(row: dict, event: Event):
    """Save or synchronize an active LiveScore record corresponding to an ongoing match event.

    Args:
        row (dict): Normalized match data payload.
        event (Event): Associated Event instance.

    Returns:
        LiveScore or None: Updated LiveScore model instance if active, else None.
    """
    status = event.status
    ls_sport = row["sport"]
    external_id = row["external_id"]

    # Keep only live matches in LiveScore model.
    # Delete from LiveScore once match is completed.
    if status != "live":
        LiveScore.objects.filter(sport=ls_sport, external_id=external_id).delete()
        return None



    from apps.entity.utils.matcher import find_team_logo_by_name
    home_logo_raw = event.home_entity.logo_url if event.home_entity else ""
    if home_logo_raw and "statpal.io" in home_logo_raw:
        home_logo_raw = ""
    home_logo_val = home_logo_raw or find_team_logo_by_name(row["home_name"])

    away_logo_raw = event.away_entity.logo_url if event.away_entity else ""
    if away_logo_raw and "statpal.io" in away_logo_raw:
        away_logo_raw = ""
    away_logo_val = away_logo_raw or find_team_logo_by_name(row["away_name"])

    existing_ls = LiveScore.objects.filter(sport=ls_sport, external_id=external_id).first()
    home_score_ls = _clean_score(row.get("home_score"))
    away_score_ls = _clean_score(row.get("away_score"))
    if existing_ls and home_score_ls is None and existing_ls.home_score is not None:
        home_score_ls = existing_ls.home_score
    if existing_ls and away_score_ls is None and existing_ls.away_score is not None:
        away_score_ls = existing_ls.away_score

    live_obj, _ = LiveScore.objects.update_or_create(
        sport=ls_sport,
        external_id=external_id,
        defaults={
            "home_team":     row["home_name"],
            "away_team":     row["away_name"],
            "home_logo":     home_logo_val,
            "away_logo":     away_logo_val,
            "home_score":    home_score_ls,
            "away_score":    away_score_ls,
            "status":        status,
            "status_detail": row["status_raw"],
            "start_time":    event.start_time,
            "raw_data":      row["raw"],
        },
    )

    # Force-save to ensure `updated_at` is always current for WebSocket publishing.
    live_obj.save(update_fields=['updated_at'])

    cache.set(f"live_scores_{ls_sport}", True, timeout=120)
    return live_obj



# ================================================================
# THESPORTSDB — Long-range upcoming fixtures (30-day window)
# StatPal is limited to ±7 day offset; TheSportsDB eventsday.php
# covers soccer fixtures up to ~30 days ahead.
# ================================================================

def _tsdb_soccer_row(event: dict) -> dict:
    """Convert a raw TheSportsDB event payload into the standardized internal row structure.

    Prefixes IDs with 'tsdb_' to prevent key collisions with StatPal fixture records.

    Args:
        event (dict): TheSportsDB match event dictionary.

    Returns:
        dict: Standardized fixture dictionary.
    """
    from datetime import datetime as _dt

    date_raw = event.get('dateEvent', '')    # YYYY-MM-DD
    try:
        d = _dt.strptime(date_raw, '%Y-%m-%d')
        date_str = d.strftime('%d.%m.%Y')   # _parse_dt expects DD.MM.YYYY
    except Exception:
        date_str = ''

    time_raw = (event.get('strTime') or '00:00:00').strip()
    time_str = time_raw[:5]                  # HH:MM

    home_score = None
    away_score = None
    try:
        if event.get('intHomeScore') not in (None, ''):
            home_score = int(event['intHomeScore'])
    except (ValueError, TypeError):
        pass
    try:
        if event.get('intAwayScore') not in (None, ''):
            away_score = int(event['intAwayScore'])
    except (ValueError, TypeError):
        pass

    return {
        'external_id': f"tsdb_{event.get('idEvent', '')}",
        'sport':        'soccer',
        'league_id':    f"tsdb_league_{event.get('idLeague', '')}",
        'league_name':  event.get('strLeague', ''),
        'home_id':      f"tsdb_team_{event.get('idHomeTeam', '')}",
        'home_name':    event.get('strHomeTeam', ''),
        'away_id':      f"tsdb_team_{event.get('idAwayTeam', '')}",
        'away_name':    event.get('strAwayTeam', ''),
        'home_score':   home_score,
        'away_score':   away_score,
        'status_raw':   event.get('strStatus') or 'NS',
        'date':         date_str,
        'time':         time_str,
        'venue':        event.get('strVenue', '') or '',
        'home_logo':    event.get('strHomeTeamBadge', '') or '',
        'away_logo':    event.get('strAwayTeamBadge', '') or '',
        'raw':          event,
    }


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def sync_thesportsdb_upcoming_fixtures(self):
    """Fetch long-range soccer fixtures for the next 30 days from TheSportsDB (eventsday.php).

    Fills the scheduling horizon beyond StatPal's ±7 day window.

    Args:
        self: Bound Celery task instance.

    Returns:
        str: Task execution summary message.
    """
    from apps.sports_apis.services.thesportsdb import thesportsdb_service

    lock_id = 'sync_thesportsdb_upcoming_fixtures_lock'
    if not cache.add(lock_id, 'true', timeout=3600):
        logger.info('sync_thesportsdb_upcoming_fixtures already running, skipping')
        return 'skipped — already running'

    try:
        today = timezone.now().date()
        saved, skipped, errors = 0, 0, 0

        for day_offset in range(1, 31):          # tomorrow → 30 days ahead
            target_date = today + timedelta(days=day_offset)
            date_str = target_date.strftime('%Y-%m-%d')

            try:
                events = thesportsdb_service.get_soccer_fixtures_for_date(date_str)
            except Exception as exc:
                errors += 1
                logger.warning(
                    '[TSDB Fixtures] fetch failed for %s: %s', date_str, exc
                )
                continue

            for ev in events:
                try:
                    row = _tsdb_soccer_row(ev)
                    if not row['external_id'] or not row['home_name']:
                        skipped += 1
                        continue

                    from django.db import transaction
                    with transaction.atomic():
                        event_obj = _save_event(row, api_source='thesportsdb')
                        if event_obj is None:
                            skipped += 1
                        else:
                            saved += 1
                except Exception as exc:
                    errors += 1
                    logger.warning(
                        '[TSDB Fixtures] save failed for event %s: %s',
                        ev.get('idEvent'), exc
                    )

        msg = (
            f'sync_thesportsdb_upcoming_fixtures — '
            f'saved={saved}, skipped={skipped}, errors={errors}'
        )
        logger.info(msg)
        return msg

    except Exception as exc:
        logger.exception('sync_thesportsdb_upcoming_fixtures failed: %s', exc)
        raise self.retry(exc=exc)
    finally:
        cache.delete(lock_id)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sync_statpal_data(self):
    """Fetch active live matches across all supported sports and publish updates via WebSockets.

    Saves data to Event and LiveScore tables and broadcasts real-time score updates to connected clients.

    Args:
        self: Bound Celery task instance.

    Returns:
        str: Task execution summary message.
    """
    lock_id = "sync_statpal_data_lock"
    if not cache.add(lock_id, "true", timeout=90):
        logger.info("sync_statpal_data already running, skipping this cycle")
        return "skipped — already running"

    try:
        fetches = [
            ("soccer", statpal_service.get_soccer_live, _soccer_rows, {}),
            ("nba", statpal_service.get_nba_live, _nba_rows, {}),
            ("football", statpal_service.get_nfl_live, _nfl_rows, {}),
            ("cricket", statpal_service.get_cricket_live, _cricket_rows, {}),
            ("tennis", statpal_service.get_tennis_live, _tennis_rows, {}),
            ("baseball", statpal_service.get_mlb_live, _mlb_rows, {}),
            ("handball", statpal_service.get_handball_live, _handball_rows, {}),
            ("volleyball", statpal_service.get_volleyball_live, _volleyball_rows, {}),
            ("golf", statpal_service.get_golf_live, _golf_rows, {}),
            ("f1", statpal_service.get_f1_live, _f1_rows, {}),
            ("horse_racing", lambda: statpal_service.get_horse_racing_live('uk'), _horse_racing_rows, {}),
            ("horse_racing", lambda: statpal_service.get_horse_racing_live('usa'), _horse_racing_rows, {}),
        ]

        # ── Stale LiveScore cleanup ──────────────────────────────────────────────
        stale_cutoff = timezone.now() - timezone.timedelta(hours=3)
        stale_deleted, _ = (
            LiveScore.objects.filter(status="live", updated_at__lt=stale_cutoff).delete()
        )
        if stale_deleted:
            logger.info("[StatPal] Cleaned up %d stale live score(s) older than 3h.", stale_deleted)

        saved, skipped, errors = 0, 0, 0
        live_objects_to_publish = []

        for fetch_config in fetches:
            sport, fetch_fn, extract_fn, params = fetch_config

            try:
                result = fetch_fn(**params)

                if not result["success"]:
                    logger.warning("[StatPal] %s fetch failed: %s", sport, result.get("error"))
                    continue

                extracted_rows = extract_fn(result["data"])
                if not isinstance(extracted_rows, list):
                    extracted_rows = []
            except Exception as exc:
                errors += 1
                logger.exception("[StatPal] %s fetch/extract crashed: %s", sport, exc)
                continue

            for row in extracted_rows:
                try:
                    from django.db import transaction
                    with transaction.atomic():
                        event_obj = _save_event(row)
                        if event_obj is None:
                            skipped += 1
                            continue
                        live_obj = _save_livescore(row, event_obj)
                        if live_obj:
                            _publish(live_obj)
                        saved += 1
                except Exception as exc:
                    errors += 1
                    logger.exception(
                        "[StatPal] Save failed — external_id=%r sport=%s: %s",
                        row.get("external_id"), sport, exc,
                    )

        msg = f"sync_statpal_data — saved={saved}, skipped={skipped}, errors={errors}"
        logger.info(msg)
        return msg
    finally:
        cache.delete(lock_id)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def sync_statpal_fixtures_data(self):
    """Fetch and sync upcoming and past match fixtures from StatPal within the ±7 day window.

    Args:
        self: Bound Celery task instance.

    Returns:
        str: Task execution summary message.
    """
    lock_id = "sync_statpal_fixtures_data_lock"
    if not cache.add(lock_id, "true", timeout=600):
        logger.info("sync_statpal_fixtures_data already running, skipping this cycle")
        return "skipped — already running"

    try:
        # Sports that use a daily offset parameter
        daily_offset_sports = [
            ("soccer",     statpal_service.get_soccer_fixtures,   _soccer_rows),
            ("nba",        statpal_service.get_nba_fixtures,      _nba_rows),
            ("football",   statpal_service.get_nfl_fixtures,      _nfl_rows),
            ("tennis",     statpal_service.get_tennis_fixtures,   _tennis_rows),
            ("baseball",   statpal_service.get_mlb_fixtures,      _mlb_rows),
            ("handball",   statpal_service.get_handball_fixtures, _handball_rows),
            ("volleyball", statpal_service.get_volleyball_fixtures, _volleyball_rows),
        ]

        # Bulk sports (no offset — API returns full upcoming schedule)
        bulk_sports = [
            ("cricket", statpal_service.get_cricket_fixtures, _cricket_rows),
            ("golf",    statpal_service.get_golf_schedule,    _golf_rows),
        ]

        saved, skipped, errors = 0, 0, 0

        # --- Bulk sports first ---
        for sport, fetch_fn, extract_fn in bulk_sports:
            try:
                result = fetch_fn()
                if not result["success"]:
                    continue
                extracted_rows = extract_fn(result["data"])
            except Exception as exc:
                errors += 1
                logger.exception("[StatPal Fixtures] %s bulk fetch failed: %s", sport, exc)
                continue

            for row in extracted_rows:
                try:
                    from django.db import transaction
                    with transaction.atomic():
                        event_obj = _save_event(row)
                        if event_obj is None:
                            skipped += 1
                            continue
                        _save_livescore(row, event_obj)
                        saved += 1
                except Exception as exc:
                    errors += 1

        # --- Daily-offset sports: StatPal limit is -7 to +7 ---
        for offset in range(-7, 8):  # -7 … 0 … +7
            for sport, fetch_fn, extract_fn in daily_offset_sports:
                try:
                    result = fetch_fn(offset=offset)
                    if not result["success"]:
                        continue
                    extracted_rows = extract_fn(result["data"])
                except Exception as exc:
                    errors += 1
                    logger.exception(
                        "[StatPal Fixtures] %s fetch failed (offset=%d): %s", sport, offset, exc
                    )
                    continue

                for row in extracted_rows:
                    try:
                        from django.db import transaction
                        with transaction.atomic():
                            event_obj = _save_event(row)
                            if event_obj is None:
                                skipped += 1
                                continue
                            _save_livescore(row, event_obj)
                            saved += 1
                    except Exception as exc:
                        errors += 1

                time.sleep(0.3)  # Throttling prevention between API calls

        msg = f"sync_statpal_fixtures_data — saved={saved}, skipped={skipped}, errors={errors}"
        logger.info(msg)
        return msg
    finally:
        cache.delete(lock_id)


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