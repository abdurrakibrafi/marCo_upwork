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
# MATCH DETAILS (API-Sports deep stats for completed soccer games)
# ================================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def fetch_event_details(self, event_id: int):
    """
    Fetch full stats, lineups, and player stats for a completed event.
    Called automatically when a soccer game finishes.
    """
    try:
        event = Event.objects.select_related(
            'home_entity', 'away_entity', 'league'
        ).get(id=event_id)
    except Event.DoesNotExist:
        return f"Event {event_id} not found"
 
    if event.api_source != 'api_sports':
        return f"Event {event_id} is not from api_sports — skipping"
 
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
            api_source='api_sports',
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
    "FT", "AET", "PEN", "Finished", "After Over Time",
    "Full-time", "finished", "ft", "aet", "CANC", "ABD",
}
_LIVE = {
    # General
    "1H", "2H", "HT", "ET", "BT", "P", "SUSP", "INT", "LIVE",
    "In Progress", "In Play", "live",
    # Basketball
    "Q1", "Q2", "Q3", "Q4", "OT", "Halftime",
    # Tennis
    "1st Set", "2nd Set", "3rd Set", "4th Set", "5th Set", "Break",
    "Set 1", "Set 2", "Set 3", "Set 4", "Set 5",
    # Cricket
    "Stumps", "Innings Break", "Lunch", "Tea", "Rain Delay",
}


def _map_status(raw: str):
    raw_lower = raw.lower().strip()
    if raw_lower in [f.lower() for f in _FINISHED] or "final" in raw_lower:
        return "completed"
    if raw_lower in [l.lower() for l in _LIVE]:
        return "live"
        
    # Check for Baseball live inning indicators (e.g., "Top 5th", "Bottom 8th", "End 6th", "Middle 2nd")
    if any(ind in raw_lower for ind in ["top ", "bottom ", "middle ", "end "]):
        if any(ind in raw_lower for ind in ["1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th", "10th", "st", "nd", "rd", "th"]):
            return "live"
    if "inning" in raw_lower:
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
        league_name = tournament.get("league", "")

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
    tour = data.get("livescore", {}).get("tournament") or data.get("tournament")
    if not tour:
        return []

    rows = []
    league_name = tour.get("name", "Golf Event")
    league_id = str(tour.get("id", ""))
    players = tour.get("player", [])

    leader_score, leader_name = None, None
    if players:
        leader = sorted(players, key=_golf_position_sort_key)[0]
        leader_name = leader.get('name')
        leader_score = leader.get('par')

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


def _save_event(row: dict) -> Event | None:
    status = _map_status(row["status_raw"])
    if status is None:
        if row.get("external_id"):
            LiveScore.objects.filter(sport=row["sport"], external_id=row["external_id"]).delete()
        return None
    if not row["external_id"]:
        return None

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
            "home_score":   row["home_score"],
            "away_score":   row["away_score"],
            "venue_name":   row["venue"],
            "start_time":   start_time,
            "metadata":     row["raw"],
        },
    )
    return event


def _save_livescore(row: dict, event: Event):
    status = _map_status(row["status_raw"])
    ls_sport = row["sport"]
    external_id = row["external_id"]

    # শুধুমাত্র লাইভ ম্যাচগুলো LiveScore মডেলে রাখব।
    # খেলা শেষ হয়ে গেলে LiveScore থেকে মুছে ফেলা হবে।
    if status != "live":
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
            "home_score":    row["home_score"] or None,
            "away_score":    row["away_score"] or None,
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