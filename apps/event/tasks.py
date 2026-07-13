from celery import shared_task
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone
from datetime import datetime, timedelta
from apps.event.models import Event, EventStatistics, EventLineup, EventPlayerStats, EventTimeline
from apps.score.models import LiveScore
from apps.entity.models import Entity
from apps.sports_apis.services.balldontlie import balldontlie_service
from apps.sports_apis.tasks import _publish 
from apps.sports_apis.services.api_sports import api_sports_service
import logging
from django.utils.timezone import make_aware
import time
import requests as req
from apps.entity.utils.matcher import get_or_create_precise_entity
from apps.sports_apis.services.statpal import statpal_service

logger = logging.getLogger(__name__)


# ================================================================
# NFL FIXTURES (BallDontLie — StatPal doesn't cover NFL)
# ================================================================

@shared_task
def update_nfl_fixtures(dates: list[str] = None):
    """Update NFL fixtures using StatPal"""
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
    """Update soccer fixtures for a date using StatPal"""
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
    """
    Fetch and save upcoming/past fixtures from StatPal for all sports:
    Soccer, NBA, NFL, Cricket, Tennis, Baseball, Handball.
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
    """Update fixtures for all sports — today + next 7 days"""
    dates = [
        (timezone.now().date() + timedelta(days=i)).isoformat()
        for i in range(-7, 8)
    ]
    update_statpal_fixtures_for_dates.delay(dates)
    logger.info(f"update_all_fixtures: Triggered update_statpal_fixtures_for_dates for {len(dates)} days.")
    return f"Fixture updates triggered for {dates[0]} to {dates[-1]}"


# ================================================================
# HELPERS
# ================================================================

def _get_or_create_team_entity(api_source, external_id, name, sport, logo_url=''):
    entity = Entity.objects.filter(
        api_source=api_source,
        external_id=external_id,
        type='team',
    ).first()

    if entity:
        return entity

    entity = Entity.objects.create(
        api_source=api_source,
        external_id=external_id,
        type='team',
        name=name,
        sport=sport,
        logo_url=logo_url or '',
        has_api_data=True,
    )
    from apps.entity.models import Team
    Team.objects.get_or_create(entity=entity)
    return entity


def _get_or_create_league_entity(api_source, external_id, name, sport, logo_url=''):
    entity, created = Entity.objects.get_or_create(
        api_source=api_source,
        external_id=external_id,
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
    """Delegated to StatPal sync"""
    sync_statpal_data.delay()
    return "Delegated to sync_statpal_data"


# ================================================================
# MATCH DETAILS (deep stats for completed games)
# ================================================================

def _populate_statpal_event_details(event):
    """
    Parse StatPal metadata to populate EventTimeline (goals, cards, subs)
    and update HT/FT/ET scores for a completed event.
    """
    meta = event.metadata or {}
    
    # 1. Fetch soccer stats if they are missing from metadata
    if event.sport == 'soccer' and not meta.get('event_summary') and not meta.get('team_stats') and event.league:
        try:
            from apps.sports_apis.services.statpal import statpal_service
            league_id = int(event.league.external_id)
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
                    if str(m.get('main_id')) == str(event.external_id) or str(m.get('id')) == str(event.external_id):
                        event.metadata = m
                        event.save(update_fields=['metadata'])
                        meta = m
                        break
        except Exception as e:
            logger.warning(f"Failed to fetch soccer match stats for event {event.id}: {e}")

    # 2. Parse soccer-specific metadata structure
    if event.sport == 'soccer':
        EventTimeline.objects.filter(event=event).delete()
        EventStatistics.objects.filter(event=event).delete()
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
                    minute = int(g.get('minute') or 0) if g.get('minute') else 0
                    extra = int(g.get('extra_min') or 0) if g.get('extra_min') else 0
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

                # Yellow Cards
                yc = side_events.get('yellowcards', {})
                yc_list = yc.get('event', []) if isinstance(yc, dict) else []
                if isinstance(yc_list, dict):
                    yc_list = [yc_list]
                for card in yc_list:
                    if not isinstance(card, dict):
                        continue
                    minute = int(card.get('minute') or 0) if card.get('minute') else 0
                    extra = int(card.get('extra_min') or 0) if card.get('extra_min') else 0
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

                # Red Cards
                rc = side_events.get('redcards', {})
                rc_list = rc.get('event', []) if isinstance(rc, dict) else []
                if isinstance(rc_list, dict):
                    rc_list = [rc_list]
                for card in rc_list:
                    if not isinstance(card, dict):
                        continue
                    minute = int(card.get('minute') or 0) if card.get('minute') else 0
                    extra = int(card.get('extra_min') or 0) if card.get('extra_min') else 0
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

                # Substitutions
                subs = side_events.get('substitutions', {})
                subs_list = subs.get('event', []) if isinstance(subs, dict) else []
                if isinstance(subs_list, dict):
                    subs_list = [subs_list]
                for sub in subs_list:
                    if not isinstance(sub, dict):
                        continue
                    minute = int(sub.get('minute') or 0) if sub.get('minute') else 0
                    extra = int(sub.get('extra_min') or 0) if sub.get('extra_min') else 0
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

        # Populate EventStatistics
        team_stats = meta.get('team_stats', {})
        if isinstance(team_stats, dict):
            for side in ['home', 'away']:
                team_entity = event.home_entity if side == 'home' else event.away_entity
                if not team_entity:
                    continue
                stats_dict = team_stats.get(side, {})
                if isinstance(stats_dict, dict):
                    flat_stats = {}
                    for k, v in stats_dict.items():
                        if isinstance(v, dict):
                            val = v.get('value') or v.get('total')
                            if val is not None:
                                flat_stats[k] = val
                        else:
                            flat_stats[k] = v
                    EventStatistics.objects.update_or_create(
                        event=event,
                        team=team_entity,
                        defaults={'stats': flat_stats}
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
                        EventLineup.objects.create(
                            event=event,
                            team=team_entity,
                            player=player_entity,
                            position_type=player_pos or '',
                            jersey_number=int(player_number) if str(player_number).isdigit() else None
                        )

        # Update scores from ft / et (Option B: sum ft + et)
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

        ev_type_raw = ev.get('type', '').lower()
        team_side = ev.get('team', '')  # 'home' or 'away'
        minute = 0
        extra_min = 0
        try:
            minute = int(ev.get('minute', 0) or 0)
        except (ValueError, TypeError):
            pass
        try:
            extra_min = int(ev.get('extra_min', 0) or 0)
        except (ValueError, TypeError):
            pass

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


def _on_the_fly_update_statpal_event(event):
    """
    On-the-fly fetch and save latest event details/fixtures for a StatPal event.
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
    """
    Fetch full stats, lineups, and player stats for a completed event.
    Handles both api_sports and statpal events.
    """
    try:
        event = Event.objects.select_related(
            'home_entity', 'away_entity', 'league'
        ).get(id=event_id)
    except Event.DoesNotExist:
        return f"Event {event_id} not found"

    # ── StatPal events: parse metadata for timeline/scores ──
    if event.api_source == 'statpal':
        _populate_statpal_event_details(event)
        try:
            from apps.sports_apis.tasks import fetch_highlight_for_event
            fetch_highlight_for_event.apply_async(args=[event_id], countdown=900)
        except Exception as e:
            logger.error(f"Failed to queue highlight fetch for event {event_id}: {e}")
        return f"Event {event_id} (statpal) details populated"

    # ── api_sports events: fetch from API ──
    if event.api_source != 'api_sports':
        return f"Event {event_id} has unknown source '{event.api_source}' — skipping"
 
    fixture_id = event.external_id
    headers = {'x-apisports-key': settings.API_SPORTS_KEY}
 
    # ── 1. Team statistics ────────────────────────────────────────────────
    try:
        resp = req.get(
            'https://v3.football.api-sports.io/fixtures/statistics',
            headers=headers,
            params={'fixture': fixture_id},
            timeout=10,
        )
        if resp.status_code == 200:
            for team_stats in resp.json().get('response', []):
                team_data = team_stats.get('team', {})
                team_entity = Entity.objects.filter(
                    api_source='api_sports',
                    external_id=str(team_data.get('id', '')),
                    type='team',
                ).first()
 
                if not team_entity:
                    continue
 
                stats_dict = {
                    s['type'].lower().replace(' ', '_'): s['value']
                    for s in team_stats.get('statistics', [])
                    if s.get('type')
                }
 
                EventStatistics.objects.update_or_create(
                    event=event,
                    team=team_entity,
                    defaults={'stats': stats_dict},
                )
    except Exception as e:
        logger.warning(f"fetch_event_details: stats failed for {event_id}: {e}")
 
    # ── 2. Lineups ────────────────────────────────────────────────────────
    try:
        resp = req.get(
            'https://v3.football.api-sports.io/fixtures/lineups',
            headers=headers,
            params={'fixture': fixture_id},
            timeout=10,
        )
        if resp.status_code == 200:
            for team_lineup in resp.json().get('response', []):
                team_data  = team_lineup.get('team', {})
                team_entity = Entity.objects.filter(
                    api_source='api_sports',
                    external_id=str(team_data.get('id', '')),
                    type='team',
                ).first()
 
                if not team_entity:
                    continue
 
                for player in team_lineup.get('startXI', []):
                    p = player.get('player', {})
                    player_entity = Entity.objects.filter(
                        api_source='api_sports',
                        external_id=str(p.get('id', '')),
                        type='athlete',
                    ).first()
                    if not player_entity:
                        continue
 
                    EventLineup.objects.update_or_create(
                        event=event,
                        team=team_entity,
                        player=player_entity,
                        defaults={
                            'position_type': 'starting',
                            'position':      p.get('pos', ''),
                            'jersey_number': p.get('number'),
                            'grid_position': p.get('grid') or '',
                        },
                    )
 
                for player in team_lineup.get('substitutes', []):
                    p = player.get('player', {})
                    player_entity = Entity.objects.filter(
                        api_source='api_sports',
                        external_id=str(p.get('id', '')),
                        type='athlete',
                    ).first()
                    if not player_entity:
                        continue
 
                    EventLineup.objects.update_or_create(
                        event=event,
                        team=team_entity,
                        player=player_entity,
                        defaults={
                            'position_type': 'substitute',
                            'position':      p.get('pos', ''),
                            'jersey_number': p.get('number'),
                        },
                    )
    except Exception as e:
        logger.warning(f"fetch_event_details: lineups failed for {event_id}: {e}")
 
    # ── 3. Player statistics ──────────────────────────────────────────────
    try:
        resp = req.get(
            'https://v3.football.api-sports.io/fixtures/players',
            headers=headers,
            params={'fixture': fixture_id},
            timeout=10,
        )
        if resp.status_code == 200:
            for team_data in resp.json().get('response', []):
                team_entity = Entity.objects.filter(
                    api_source='api_sports',
                    external_id=str(team_data.get('team', {}).get('id', '')),
                    type='team',
                ).first()
 
                if not team_entity:
                    continue
 
                for p in team_data.get('players', []):
                    player_info = p.get('player', {})
                    player_entity = Entity.objects.filter(
                        api_source='api_sports',
                        external_id=str(player_info.get('id', '')),
                        type='athlete',
                    ).first()
 
                    if not player_entity:
                        continue
 
                    stats_raw = p.get('statistics', [{}])[0]
                    games   = stats_raw.get('games', {})
                    goals   = stats_raw.get('goals', {})
                    shots   = stats_raw.get('shots', {})
                    passes  = stats_raw.get('passes', {})
                    tackles = stats_raw.get('tackles', {})
                    cards   = stats_raw.get('cards', {})
                    dribbles= stats_raw.get('dribbles', {})
 
                    stats_dict = {
                        'minutes':      games.get('minutes', 0),
                        'rating':       games.get('rating'),
                        'captain':      games.get('captain', False),
                        'goals':        goals.get('total', 0) or 0,
                        'assists':      goals.get('assists', 0) or 0,
                        'shots_total':  shots.get('total', 0) or 0,
                        'shots_on':     shots.get('on', 0) or 0,
                        'passes_total': passes.get('total', 0) or 0,
                        'passes_key':   passes.get('key', 0) or 0,
                        'pass_accuracy':passes.get('accuracy', 0) or 0,
                        'tackles':      tackles.get('total', 0) or 0,
                        'blocks':       tackles.get('blocks', 0) or 0,
                        'interceptions':tackles.get('interceptions', 0) or 0,
                        'dribbles_success': dribbles.get('success', 0) or 0,
                        'yellow_cards': cards.get('yellow', 0) or 0,
                        'red_cards':    cards.get('red', 0) or 0,
                    }
 
                    EventPlayerStats.objects.update_or_create(
                        event=event,
                        player=player_entity,
                        defaults={
                            'team':           team_entity,
                            'stats':          stats_dict,
                            'points_or_goals': stats_dict['goals'],
                        },
                    )
    except Exception as e:
        logger.warning(f"fetch_event_details: player stats failed for {event_id}: {e}")
 
    # ── 4. Timeline (goals, cards, subs) ─────────────────────────────────
    try:
        resp = req.get(
            'https://v3.football.api-sports.io/fixtures/events',
            headers=headers,
            params={'fixture': fixture_id},
            timeout=10,
        )
        if resp.status_code == 200:
            EventTimeline.objects.filter(event=event).delete()
 
            for ev in resp.json().get('response', []):
                team_data = ev.get('team', {})
                team_entity = Entity.objects.filter(
                    api_source='api_sports',
                    external_id=str(team_data.get('id', '')),
                    type='team',
                ).first()
 
                player_data = ev.get('player', {})
                player_entity = None
                if player_data and player_data.get('id'):
                    player_entity = Entity.objects.filter(
                        api_source='api_sports',
                        external_id=str(player_data.get('id')),
                        type='athlete',
                    ).first()
 
                ev_type = ev.get('type', '').lower()
                detail  = ev.get('detail', '').lower()
 
                type_map = {
                    'goal':  'goal',
                    'card':  'yellow_card' if 'yellow' in detail else 'red_card',
                    'subst': 'substitution',
                    'var':   'var',
                }
                mapped_type = type_map.get(ev_type, ev_type)
 
                if 'own goal' in detail:
                    mapped_type = 'goal'
                if 'penalty' in detail and ev_type == 'goal':
                    mapped_type = 'penalty'
 
                EventTimeline.objects.create(
                    event=event,
                    event_type=mapped_type,
                    minute=ev.get('time', {}).get('elapsed', 0) or 0,
                    extra_minute=ev.get('time', {}).get('extra', 0) or 0,
                    team=team_entity,
                    player=player_entity,
                    description=f"{ev.get('detail', '')} — {ev.get('comments', '') or ''}".strip(' —'),
                    metadata=ev,
                )
    except Exception as e:
        logger.warning(f"fetch_event_details: timeline failed for {event_id}: {e}")
 
    logger.info(f"fetch_event_details: completed for event {event_id}")

    try:
        from apps.sports_apis.tasks import fetch_highlight_for_event
        fetch_highlight_for_event.apply_async(args=[event_id], countdown=900)
    except Exception as e:
        logger.error(f"Failed to queue highlight fetch for event {event_id}: {e}")

    return f"Event {event_id} details fetched"
 
 
@shared_task
def check_completed_events():
    completed_without_stats = (
        Event.objects
        .filter(
            status='completed',
            sport='soccer',
            api_source__in=['api_sports', 'statpal'],
        )
        .exclude(
            id__in=EventStatistics.objects.values_list('event_id', flat=True)
        )
        .order_by('-start_time')
    )

    count = 0
    for event in completed_without_stats[:50]:
        fetch_event_details.delay(event.id)
        count += 1

    logger.info(f"check_completed_events: triggered {count} detail fetches")
    return f"Triggered {count} event detail fetches"


@shared_task
def cleanup_stale_live_events():
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


def _map_status(raw: str, sport: str = None, metadata: dict = None):
    """
    Receives raw status strings from multiple different sports data providers (StatPal etc.)
    across 13 sports. Provider formatting is inconsistent (capitalization, periods, and
    abbreviations vary), so exact-string matching alone is fragile.
    Normalization is required, and any new provider integration should audit status strings
    against _FINISHED/_LIVE before assuming defaults are safe.
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
    try:
        naive = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
        return timezone.make_aware(naive, timezone.get_current_timezone())
    except Exception:
        return timezone.now()


def _safe_int(val) -> int:
    try:
        return int(str(val).split("/")[0].split("&")[0].strip())
    except Exception:
        return 0


def _soccer_rows(data: dict) -> list:
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
    """NBA, Hockey, Baseball ইত্যাদির জন্য জেনেরিক পার্সার"""
    tournaments_data = (
        data.get("livescores", {}).get("tournament")
        or data.get("scores", {}).get("tournament", {})
        or []
    )

    # API কখনও dict পাঠায়, কখনও list of dicts. সবসময় list হিসেবে কাজ করা হবে।
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
    return _generic_sport_rows(data, "nba")


def _nfl_rows(data: dict) -> list:
    return _generic_sport_rows(data, "football")


def _hockey_rows(data: dict) -> list:
    return _generic_sport_rows(data, "hockey")


def _tennis_rows(data: dict) -> list:
    return _generic_sport_rows(data, "tennis")


def _mlb_rows(data: dict) -> list:
    return _generic_sport_rows(data, "baseball")


def _handball_rows(data: dict) -> list:
    return _generic_sport_rows(data, "handball")


def _volleyball_rows(data: dict) -> list:
    return _generic_sport_rows(data, "volleyball")


def _cricket_rows(data: dict) -> list:
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


def _golf_position_sort_key(p):
    """পজিশন safely int এ কনভার্ট করে। blank/'T1'/'CUT' ইত্যাদি হ্যান্ডল করে।"""
    pos = p.get('pos', '999')
    if not pos:
        return 999
    pos = str(pos).lstrip('T').strip()  # "T1" -> "1"
    try:
        return int(pos)
    except (ValueError, TypeError):
        return 999

        

def _golf_rows(data: dict) -> list:
    tour_data = data.get("livescore", {}).get("tournament") or data.get("tournament")
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


def _clean_score(val):
    if val is None or str(val).strip() in ("", "None", "null", "undefined"):
        return None
    try:
        return int(str(val).split("/")[0].split("&")[0].strip())
    except Exception:
        return None


def _save_event(row: dict) -> Event | None:
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
    if row.get("home_logo") and not home.logo_url:
        home.logo_url = row["home_logo"]
        home.save(update_fields=['logo_url'])
    # For individual sports, away entity can be null
    away = None
    if row.get("away_id") and row.get("away_name"):
        away = get_or_create_precise_entity(
            row["away_id"], row["away_name"], sport, entity_type="team"
        )
        if row.get("away_logo") and not away.logo_url:
            away.logo_url = row["away_logo"]
            away.save(update_fields=['logo_url'])
    start_time = _parse_dt(row["date"], row["time"])

    event, _ = Event.objects.update_or_create(
        api_source="statpal",
        external_id=row["external_id"],
        defaults={
            "sport":        sport,
            "home_entity":  home,
            "away_entity":  away,
            "league":       league,
            "status":       status,
            "status_detail": row["status_raw"],
            "home_score":   _clean_score(row.get("home_score")),
            "away_score":   _clean_score(row.get("away_score")),
            "venue_name":   row["venue"],
            "start_time":   start_time,
            "metadata":     row["raw"],
        },
    )
    if status == "completed":
        try:
            _populate_statpal_event_details(event)
        except Exception as e:
            logger.warning(f"Failed to auto-populate statpal event details for {event.id}: {e}")
    return event


def _save_livescore(row: dict, event: Event):
    status = event.status
    ls_sport = row["sport"]
    external_id = row["external_id"]

    # শুধুমাত্র লাইভ ম্যাচগুলো LiveScore মডেলে রাখব।
    # খেলা শেষ হয়ে গেলে LiveScore থেকে মুছে ফেলা হবে।
    if status != "live":
        LiveScore.objects.filter(sport=ls_sport, external_id=external_id).delete()
        return None

    # Extra safety guard: StatPal has no live stats for this soccer match
    if ls_sport == "soccer":
        raw_match = row.get("raw", {})
        if str(raw_match.get("has_live_stats", "True")).strip().lower() == "false":
            LiveScore.objects.filter(sport=ls_sport, external_id=external_id).delete()
            return None

    live_obj, _ = LiveScore.objects.update_or_create(
        sport=ls_sport,
        external_id=external_id,
        defaults={
            "home_team":     row["home_name"],
            "away_team":     row["away_name"],
            "home_logo":     event.home_entity.logo_url if event.home_entity else "",
            "away_logo":     event.away_entity.logo_url if event.away_entity else "",
            "home_score":    _clean_score(row.get("home_score")),
            "away_score":    _clean_score(row.get("away_score")),
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

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sync_statpal_data(self):
    """
    Fetches live + today's fixtures for Soccer, NBA, Cricket.
    Saves to both Event and LiveScore models.
    Publishes via WebSocket for live matches.

    Recommended beat schedule: every 60 seconds.
    """
    fetches = [
        ("soccer", statpal_service.get_soccer_live, _soccer_rows, {}),
        ("soccer", statpal_service.get_soccer_fixtures, _soccer_rows, {'offset': 0}),
        ("nba", statpal_service.get_nba_live, _nba_rows, {}),
        ("nba", statpal_service.get_nba_fixtures, _nba_rows, {'offset': 0}),
        ("football", statpal_service.get_nfl_live, _nfl_rows, {}),
        ("football", statpal_service.get_nfl_fixtures, _nfl_rows, {'offset': 0}),
        ("cricket", statpal_service.get_cricket_live, _cricket_rows, {}),
        ("cricket", statpal_service.get_cricket_fixtures, _cricket_rows, {}),
        ("tennis", statpal_service.get_tennis_live, _tennis_rows, {}),
        ("tennis", statpal_service.get_tennis_fixtures, _tennis_rows, {'offset': 0}),
        ("baseball", statpal_service.get_mlb_live, _mlb_rows, {}),
        ("baseball", statpal_service.get_mlb_fixtures, _mlb_rows, {'offset': 0}),
        ("handball", statpal_service.get_handball_live, _handball_rows, {}),
        ("handball", statpal_service.get_handball_fixtures, _handball_rows, {'offset': 0}),
        ("volleyball", statpal_service.get_volleyball_live, _volleyball_rows, {}),
        ("golf", statpal_service.get_golf_live, _golf_rows, {}),
        ("horse_racing", lambda: statpal_service.get_horse_racing_live('uk'), _horse_racing_rows, {}),
        ("horse_racing", lambda: statpal_service.get_horse_racing_live('usa'), _horse_racing_rows, {}),
        # ("horse_racing", lambda: statpal_service.get_horse_racing_live('aus'), _horse_racing_rows, {}), # Returns HTTP 500
    ]

    # ── Stale LiveScore cleanup ──────────────────────────────────────────────
    # Any match still marked 'live' but not updated in the last 3 hours is
    # almost certainly finished and missed by the API. Remove it so the
    # WebSocket feed stays clean.
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
        except Exception as exc:
            errors += 1
            logger.exception("[StatPal] %s fetch/extract crashed: %s", sport, exc)
            continue

        logger.info(
            "[StatPal] Fetched '%s' (%s). API response parsed into %d rows.",
            sport, fetch_fn.__name__, len(extracted_rows)
        )

        for row in extracted_rows:
            try:
                # Skip soccer matches with no live stats
                if sport == "soccer":
                    raw_match = row.get("raw", {})
                    if str(raw_match.get("has_live_stats", "True")).strip().lower() == "false":
                        LiveScore.objects.filter(sport="soccer", external_id=row["external_id"]).delete()
                        skipped += 1
                        continue

                from django.db import transaction
                with transaction.atomic():
                    event_obj = _save_event(row)
                    if event_obj is None:
                        skipped += 1
                        continue
                    live_obj = _save_livescore(row, event_obj)
                    if live_obj:
                        live_objects_to_publish.append(live_obj)
                    saved += 1
            except Exception as exc:
                errors += 1
                logger.exception(
                    "[StatPal] Save failed — external_id=%r sport=%s: %s",
                    row.get("external_id"), sport, exc,
                )

    # Publish all collected live objects at the end to avoid multiple publishes for the same game
    for live_obj in live_objects_to_publish:
        _publish(live_obj)

    msg = f"sync_statpal_data — saved={saved}, skipped={skipped}, errors={errors}"
    logger.info(msg)
    return msg