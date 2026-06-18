from celery import shared_task
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone
from datetime import datetime, timedelta
from apps.event.models import Event
from apps.score.models import LiveScore
from apps.entity.models import Entity
from apps.sports_apis.services.balldontlie import balldontlie_service
from apps.sports_apis.services.api_sports import api_sports_service
from apps.sports_apis.services.api_cricket import api_cricket_service
import logging
from django.utils.timezone import make_aware
import requests as req
from django.conf import settings
from apps.event.models import (
    Event, EventStatistics, EventLineup, EventPlayerStats, EventTimeline
)
logger = logging.getLogger(__name__)
from apps.sports_apis.services.statpal import statpal_service

@shared_task
def update_nba_fixtures(date=None):
    """Update NBA fixtures for a date"""
    if not date:
        date = timezone.now().date().isoformat()
    
    logger.info(f"Updating NBA fixtures for {date}")
    
    result = balldontlie_service.get_games_by_date('nba', date)
    
    if result['success']:
        games = result['data'].get('data', [])
        
        for game in games:
            home_team = _get_or_create_team_entity(
                'balldontlie',
                str(game['home_team']['id']),
                game['home_team']['full_name'],
                'basketball'
            )
            
            away_team = _get_or_create_team_entity(
                'balldontlie',
                str(game['visitor_team']['id']),
                game['visitor_team']['full_name'],
                'basketball'
            )
            
            # Determine status
            game_status = game.get('status', '')
            if game_status == 'Final':
                status = 'completed'
            elif game_status in ['1st Qtr', '2nd Qtr', '3rd Qtr', '4th Qtr', 'Half']:
                status = 'live'
            else:
                status = 'upcoming'
            
            Event.objects.update_or_create(
                api_source='balldontlie',
                external_id=str(game['id']),
                defaults={
                    'sport': 'basketball',
                    'home_entity': home_team,
                    'away_entity': away_team,
                    'start_time': make_aware(datetime.fromisoformat(game['date'])) if isinstance(game['date'], str) else game['date'],
                    'status': status,
                    'status_detail': game.get('period', ''),
                    'home_score': game.get('home_team_score'),
                    'away_score': game.get('visitor_team_score'),
                    'metadata': game,
                }
            )
        
        # Cache the fixtures
        cache_key = f'fixtures_nba_{date}'
        cache.set(cache_key, games, timeout=3600)  # 1 hour
        
        logger.info(f"NBA: Updated {len(games)} fixtures for {date}")
        return f"NBA: {len(games)} fixtures updated"
    
    logger.error(f"NBA fixtures update failed: {result.get('error')}")
    return "NBA fixtures update failed"


@shared_task
def update_nfl_fixtures(date=None):
    """Update NFL fixtures"""
    if not date:
        date = timezone.now().date().isoformat()
    
    logger.info(f"Updating NFL fixtures for {date}")
    
    # NFL uses week-based system, calculate current week
    season = timezone.now().year
    result = balldontlie_service.get_games_by_date('nfl', date)
    
    if result['success']:
        games = result['data'].get('data', [])
        
        for game in games:
            home_team = _get_or_create_team_entity(
                'balldontlie',
                str(game['home_team']['id']),
                game['home_team']['full_name'],
                'football'
            )
            
            away_team = _get_or_create_team_entity(
                'balldontlie',
                str(game['visitor_team']['id']),
                game['visitor_team']['full_name'],
                'football'
            )
            
            game_status = game.get('status', '')
            if game_status == 'Final':
                status = 'completed'
            elif game_status in ['Q1', 'Q2', 'Q3', 'Q4']:
                status = 'live'
            else:
                status = 'upcoming'
            
            Event.objects.update_or_create(
                api_source='balldontlie',
                external_id=str(game['id']),
                defaults={
                    'sport': 'football',
                    'home_entity': home_team,
                    'away_entity': away_team,
                    'start_time': make_aware(datetime.fromisoformat(game['date'])) if isinstance(game['date'], str) else game['date'],
                    'status': status,
                    'status_detail': game.get('quarter', ''),
                    'home_score': game.get('home_team_score'),
                    'away_score': game.get('visitor_team_score'),
                    'metadata': game,
                }
            )
        
        cache_key = f'fixtures_nfl_{date}'
        cache.set(cache_key, games, timeout=3600)
        
        logger.info(f"NFL: Updated {len(games)} fixtures")
        return f"NFL: {len(games)} fixtures updated"
    
    return "NFL fixtures update failed"


@shared_task
def update_soccer_fixtures(date=None):
    """Update soccer fixtures for a date"""
    if not date:
        date = timezone.now().date().isoformat()
    
    logger.info(f"Updating soccer fixtures for {date}")
    
    result = api_sports_service.get_fixtures_by_date(date)
    
    if result['success']:
        fixtures = result['data'].get('response', [])
        
        for fixture in fixtures:
            fixture_data = fixture.get('fixture', {})
            teams_data = fixture.get('teams', {})
            goals_data = fixture.get('goals', {})
            league_data = fixture.get('league', {})
            venue_data = fixture_data.get('venue', {})
            
            home_team = _get_or_create_team_entity(
                'api_sports',
                str(teams_data['home']['id']),
                teams_data['home']['name'],
                'soccer',
                logo_url=teams_data['home'].get('logo')
            )
            
            away_team = _get_or_create_team_entity(
                'api_sports',
                str(teams_data['away']['id']),
                teams_data['away']['name'],
                'soccer',
                logo_url=teams_data['away'].get('logo')
            )
            
            # Get or create league
            league = _get_or_create_league_entity(
                'api_sports',
                str(league_data['id']),
                league_data['name'],
                'soccer',
                logo_url=league_data.get('logo')
            )
            
            # Map status
            status_short = fixture_data.get('status', {}).get('short', '')
            if status_short in ['FT', 'AET', 'PEN']:
                status = 'completed'
            elif status_short in ['1H', '2H', 'HT', 'ET', 'BT', 'P', 'LIVE']:
                status = 'live'
            elif status_short in ['PST', 'CANC', 'ABD']:
                status = 'postponed'
            else:
                status = 'upcoming'
            
            Event.objects.update_or_create(
                api_source='api_sports',
                external_id=str(fixture_data['id']),
                defaults={
                    'sport': 'soccer',
                    'home_entity': home_team,
                    'away_entity': away_team,
                    'league': league,
                    'start_time': fixture_data['date'],
                    'status': status,
                    'status_detail': fixture_data.get('status', {}).get('long', ''),
                    'home_score': goals_data.get('home'),
                    'away_score': goals_data.get('away'),
                    'venue_name': venue_data.get('name') or '',     
                    'venue_city': venue_data.get('city') or '',     
                    'metadata': fixture,
                }
            )
        
        cache_key = f'fixtures_soccer_{date}'
        cache.set(cache_key, fixtures, timeout=3600)
        
        logger.info(f"Soccer: Updated {len(fixtures)} fixtures for {date}")
        return f"Soccer: {len(fixtures)} fixtures updated"
    
    return "Soccer fixtures update failed"


# ─────────────────────────────────────────────────────────────────────────────
# CRICKET LIVE SCORES TASK REMOVED - now in apps/sports_apis/tasks.py
# DO NOT add it back here
# ─────────────────────────────────────────────────────────────────────────────

@shared_task
def update_cricket_fixtures(date=None):
    """Update cricket fixtures"""
    if not date:
        date = timezone.now().date().isoformat()
 
    logger.info(f"Updating cricket fixtures for {date}")
 
    result = api_cricket_service.get_fixtures_by_date(date)
 
    if not result['success']:
        return "Cricket fixtures update failed"
 
    # FIX: cricket uses 'result', not 'response'
    fixtures = result['data'].get('result', [])
 
    saved = 0
    for fixture in fixtures:
        # FIX: flat string fields, not a teams array
        home_name = fixture.get('event_home_team', '')
        away_name = fixture.get('event_away_team', '')
        home_key  = str(fixture.get('home_team_key', ''))
        away_key  = str(fixture.get('away_team_key', ''))
 
        if not home_name or not away_name:
            continue
 
        home_team = _get_or_create_team_entity(
            'api_cricket', home_key, home_name, 'cricket',
            logo_url=fixture.get('event_home_team_logo', '')
        )
        away_team = _get_or_create_team_entity(
            'api_cricket', away_key, away_name, 'cricket',
            logo_url=fixture.get('event_away_team_logo', '')
        )
 
        # League
        league_key  = str(fixture.get('league_key', ''))
        league_name = fixture.get('league_name', '')
        league = None
        if league_key and league_name:
            league = _get_or_create_league_entity(
                'api_cricket', league_key, league_name, 'cricket'
            )
 
        event_status = (fixture.get('event_status') or '').lower()
        if 'finished' in event_status or 'complete' in event_status:
            status = 'completed'
        elif 'live' in event_status or 'progress' in event_status:
            status = 'live'
        else:
            status = 'upcoming'
 
        external_id = str(fixture.get('event_key', ''))
        start_time  = fixture.get('event_date_start') or fixture.get('event_date_stop')
 
        if not external_id or not start_time:
            continue
 
        Event.objects.update_or_create(
            api_source='api_cricket',
            external_id=external_id,
            defaults={
                'sport': 'cricket',
                'home_entity': home_team,
                'away_entity': away_team,
                'league': league,
                'start_time': start_time,
                'status': status,
                'status_detail': fixture.get('event_status', ''),
                'venue_name': fixture.get('event_stadium', ''),
                'metadata': fixture,
            }
        )
        saved += 1
 
    logger.info(f"Cricket fixtures: saved {saved} for {date}")
    return f"Cricket: {saved} fixtures updated"
 
@shared_task
def update_all_fixtures():
    """Update fixtures for all sports — today + next 7 days"""
    dates = [
        (timezone.now().date() + timedelta(days=i)).isoformat()
        for i in range(8)
    ]
    
    for date in dates:
        update_soccer_fixtures.delay(date)
        update_nba_fixtures.delay(date)
        update_nfl_fixtures.delay(date)
        update_cricket_fixtures.delay(date)
    
    logger.info(f"update_all_fixtures: queued {len(dates)} days")
    return f"Fixtures triggered for {dates[0]} to {dates[-1]}"


# Helper functions

def _get_or_create_team_entity(api_source, external_id, name, sport, logo_url=''):
    # Try to get existing first — use filter+first to avoid MultipleObjectsReturned
    entity = Entity.objects.filter(
        api_source=api_source,
        external_id=external_id,
    ).first()

    if entity:
        return entity

    # Create new one
    entity = Entity.objects.create(
        api_source=api_source,
        external_id=external_id,
        type='team',
        name=name,
        sport=sport,
        logo_url=logo_url or '',
        has_api_data=True,
    )
    # Create Team sub-model
    from apps.entity.models import Team
    Team.objects.get_or_create(entity=entity)
    return entity


def _get_or_create_league_entity(api_source, external_id, name, sport, logo_url=''):
    # Ensure we always reuse the same league entity for the same api_source+external_id.
    # This prevents duplicate league entities that break fixtures/standings lookups.
    entity, created = Entity.objects.get_or_create(
        api_source=api_source,
        external_id=external_id,
        defaults={
            'type': 'league',
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


@shared_task
def update_soccer_live_scores_only():
    """
    Lightweight task — only updates scores/status for fixtures
    already in DB that are live or starting soon.

    BUG FIX: The old setup had update_soccer_fixtures running every 2 min
    AND as part of update_all_fixtures daily — causing duplicate work.

    This task only calls the API for fixtures that are actually live,
    not the full day's fixture list.
    """
    from django.utils import timezone
    from datetime import timedelta

    now = timezone.now()
    soon = now + timedelta(hours=2)

    # Only fetch IDs of games that are live or starting within 2 hours
    active_fixture_ids = list(
        Event.objects.filter(
            sport='soccer',
            status__in=['live', 'upcoming'],
            start_time__lte=soon,
            api_source='api_sports',
        ).values_list('external_id', flat=True)[:20]  # max 20 at a time
    )

    if not active_fixture_ids:
        return "No active soccer fixtures to update"

    result = api_sports_service.get_live_fixtures()

    if not result['success']:
        return f"Failed to fetch live fixtures: {result.get('error')}"

    fixtures = result['data'].get('response', [])
    updated = 0

    for fixture in fixtures:
        fixture_data = fixture.get('fixture', {})
        goals_data = fixture.get('goals', {})
        external_id = str(fixture_data.get('id', ''))

        status_short = fixture_data.get('status', {}).get('short', '')
        if status_short in ['FT', 'AET', 'PEN']:
            status = 'completed'
        elif status_short in ['1H', '2H', 'HT', 'ET', 'BT', 'P', 'LIVE']:
            status = 'live'
        elif status_short in ['PST', 'CANC', 'ABD']:
            status = 'postponed'
        else:
            status = 'upcoming'

        rows = Event.objects.filter(
            api_source='api_sports',
            external_id=external_id,
        ).update(
            status=status,
            status_detail=fixture_data.get('status', {}).get('long', ''),
            home_score=goals_data.get('home'),
            away_score=goals_data.get('away'),
        )
        if rows:
            updated += 1

    return f"Live soccer: updated {updated} fixtures"




@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def fetch_event_details(self, event_id: int):
    """
    Fetch full stats, lineups, and player stats for a completed event.
    Called automatically when a soccer game finishes.
    Can also be called manually for any event.
    """
    from apps.event.models import (
        Event, EventStatistics, EventLineup, EventPlayerStats, EventTimeline
    )
    from apps.entity.models import Entity
 
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
                ).first()
 
                if not team_entity:
                    continue
 
                # Convert list of {type, value} into a clean dict
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
                ).first()
 
                if not team_entity:
                    continue
 
                # Starting XI
                for player in team_lineup.get('startXI', []):
                    p = player.get('player', {})
                    player_entity = Entity.objects.filter(
                        api_source='api_sports',
                        external_id=str(p.get('id', '')),
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
 
                # Substitutes
                for player in team_lineup.get('substitutes', []):
                    p = player.get('player', {})
                    player_entity = Entity.objects.filter(
                        api_source='api_sports',
                        external_id=str(p.get('id', '')),
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
                ).first()
 
                if not team_entity:
                    continue
 
                for p in team_data.get('players', []):
                    player_info = p.get('player', {})
                    player_entity = Entity.objects.filter(
                        api_source='api_sports',
                        external_id=str(player_info.get('id', '')),
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
            # Clear old timeline for this event before re-inserting
            EventTimeline.objects.filter(event=event).delete()
 
            for ev in resp.json().get('response', []):
                team_data = ev.get('team', {})
                team_entity = Entity.objects.filter(
                    api_source='api_sports',
                    external_id=str(team_data.get('id', '')),
                ).first()
 
                ev_type = ev.get('type', '').lower()
                detail  = ev.get('detail', '').lower()
 
                # Map API type to our EventTimeline choices
                type_map = {
                    'goal':  'goal',
                    'card':  'yellow_card' if 'yellow' in detail else 'red_card',
                    'subst': 'substitution',
                    'var':   'var',
                }
                mapped_type = type_map.get(ev_type, ev_type)
 
                # Override for own goals / penalties
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
    from apps.event.models import Event, EventStatistics

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
        .order_by('-start_time')  # most recent first
    )

    count = 0
    for event in completed_without_stats[:50]:
        fetch_event_details.delay(event.id)
        count += 1

    logger.info(f"check_completed_events: triggered {count} detail fetches")
    return f"Triggered {count} event detail fetches"


@shared_task
def cleanup_stale_live_events():
    """
    Any event marked 'live' that started more than 5 hours ago
    and hasn't been updated → force to 'completed'
    """
    cutoff = timezone.now() - timedelta(hours=5)
    stale = Event.objects.filter(
        status='live',
        start_time__lte=cutoff,
    )
    count = stale.update(status='completed')
    logger.info(f"Cleaned up {count} stale live events")
    return f"Cleaned {count} stale live events"


"""--------------------new code (tatpal integration)------------------"""


from apps.entity.utils.matcher import get_or_create_precise_entity
from apps.sports_apis.services.statpal import statpal_service

# --- StatPal Calendar Sync Logic ---

@shared_task
def sync_statpal_fixtures_task(sport, days=7):
    today = timezone.now().date()
    
    for i in range(days + 1):
        target_date = (today + timedelta(days=i)).isoformat()
        logger.info(f"Syncing {sport} fixtures for {target_date} via StatPal")
        
        result = statpal_service.get_fixtures_by_date(sport, target_date)
        
        if not result['success']:
            continue
            
        # StatPal data mapping - adjusting based on typical structure
        # Note: If 'data' is a list directly, or inside a 'results' key
        raw_response = result.get('data', {})
        fixtures = raw_response if isinstance(raw_response, list) else raw_response.get('data', [])
        
        for fix in fixtures:
            try:
                # টিম ডাটা এক্সট্রাক্ট করা (StatPal keys অনুযায়ী)
                home = fix.get('home_team', fix.get('localteam', {}))
                away = fix.get('away_team', fix.get('visitorteam', {}))
                
                if not home.get('name') or not away.get('name'): continue

                # টিম ক্রিয়েট বা গেট করা
                home_team = _get_or_create_team_entity('statpal', str(home.get('id')), home.get('name'), sport, logo_url=home.get('logo'))
                away_team = _get_or_create_team_entity('statpal', str(away.get('id')), away.get('name'), sport, logo_url=away.get('logo'))

                # ইভেন্ট সেভ করা
                Event.objects.update_or_create(
                    api_source='statpal',
                    external_id=str(fix.get('id')),
                    defaults={
                        'sport': sport,
                        'home_entity': home_team,
                        'away_entity': away_team,
                        'start_time': fix.get('start_time') or fix.get('date_time'),
                        'status': _map_statpal_status(str(fix.get('status'))),
                        'home_score': fix.get('home_score', 0),
                        'away_score': fix.get('away_score', 0),
                        'metadata': fix
                    }
                )
            except Exception as e:
                logger.error(f"Error processing fixture {fix.get('id')}: {e}")

def _map_statpal_status(status_raw):
    """Internal helper to map StatPal status to DB choices"""
    s = status_raw.lower()
    if s in ['finished', 'ft', 'completed']: return 'completed'
    if s in ['live', 'in_play']: return 'live'
    if s in ['postponed']: return 'postponed'
    return 'upcoming'


from apps.entity.utils.matcher import get_or_create_precise_entity

@shared_task
def sync_statpal_calendar_unified(sport, offset=None):
    result = statpal_service.get_fixtures(sport, offset)
    if not result['success']: return

    raw = result['data']
    matches = []

    # --- Extract matches based on your JSON samples ---
    if sport == 'soccer':
        # Soccer pattern: matches_DD_MM_YYYY -> league -> match
        for key in raw.keys():
            if key.startswith('matches_'):
                for lg in raw[key].get('league', []):
                    matches.extend(lg.get('match', []))
    
    elif sport == 'nba':
        # NBA pattern: livescores/scores -> tournament -> match
        matches = raw.get('scores', {}).get('tournament', {}).get('match', [])
    
    elif sport == 'cricket':
        # Cricket pattern: scores -> category -> match
        for cat in raw.get('scores', {}).get('category', []):
            m = cat.get('match')
            if m: matches.append(m)

    # --- Processing with Precise ID Matching ---
    for m in matches:
        home_raw = m.get('home', {})
        away_raw = m.get('away', {})
        
        home = get_or_create_precise_entity(home_raw.get('id'), home_raw.get('name'), sport)
        away = get_or_create_precise_entity(away_raw.get('id'), away_raw.get('name'), sport)

        if home and away:
            Event.objects.update_or_create(
                api_source='statpal',
                external_id=str(m.get('id') or m.get('main_id')),
                defaults={
                    'sport': sport,
                    'home_entity': home,
                    'away_entity': away,
                    'start_time': m.get('date') + " " + m.get('time', '00:00'),
                    'status': 'completed' if m.get('status') == 'Finished' else 'upcoming',
                    'home_score': m.get('ft', {}).get('home_goals', home_raw.get('totalscore', 0)),
                    'away_score': m.get('ft', {}).get('away_goals', away_raw.get('totalscore', 0)),
                    'metadata': m
                }
            )


@shared_task
def sync_statpal_unified_task(sport, mode='fixtures', offset=None):
    from apps.entity.utils.matcher import get_or_create_precise_entity
    from apps.sports_apis.services.statpal import statpal_service
    import logging
    logger = logging.getLogger(__name__)

    result = statpal_service.get_live_scores(sport) if mode == 'live' else statpal_service.get_fixtures(sport, offset)
    
    if not result['success']:
        logger.error(f"StatPal API Call Failed: {result.get('error')}")
        return "Failed"

    data = result.get('data', {})
    matches = []

    # --- Resilient Parsing ---
    try:
        if sport == 'soccer':
            # সকারের ক্ষেত্রে root_key ডাইনামিক হয় (যেমন: matches_18_06_2026)
            for key, value in data.items():
                if isinstance(value, dict) and 'league' in value:
                    for lg in value.get('league', []):
                        matches.extend(lg.get('match', []))
        elif sport == 'nba':
            root = data.get('livescores') or data.get('scores', {})
            matches = root.get('tournament', {}).get('match', [])
        elif sport == 'cricket':
            root = data.get('scores', {})
            for cat in root.get('category', []):
                m = cat.get('match')
                if m: matches.append(m)
    except Exception as e:
        logger.error(f"JSON Parsing Error: {e}")

    logger.info(f"StatPal {sport}: Found {len(matches)} matches to process")

    saved = 0
    for m in matches:
        try:
            home_raw = m.get('home', {})
            away_raw = m.get('away', {})
            
            home = get_or_create_precise_entity(home_raw.get('id'), home_raw.get('name'), sport, logo=home_raw.get('logo'))
            away = get_or_create_precise_entity(away_raw.get('id'), away_raw.get('name'), sport, logo=away_raw.get('logo'))

            Event.objects.update_or_create(
                api_source='statpal',
                external_id=str(m.get('id') or m.get('main_id')),
                defaults={
                    'sport': sport,
                    'home_entity': home,
                    'away_entity': away,
                    'status': 'completed' if m.get('status') == 'Finished' else 'upcoming',
                    'home_score': home_raw.get('totalscore', 0) or m.get('home_score', 0),
                    'away_score': away_raw.get('totalscore', 0) or m.get('away_score', 0),
                    'start_time': timezone.now(), # Temporary for testing
                    'metadata': m
                }
            )
            saved += 1
        except Exception as e:
            logger.error(f"Error saving match: {e}")

    return f"StatPal {sport}: {saved} matches saved"



from apps.entity.utils.matcher import get_or_create_precise_entity
from apps.sports_apis.services.statpal import statpal_service
from django.utils import timezone

@shared_task
def sync_statpal_all_live_task():
    """
    Fetches Live Scores for all sports and saves them as Events.
    """
    sports = ['soccer', 'cricket', 'nba']
    total_saved = 0

    for sport in sports:
        if sport == 'soccer': res = statpal_service.get_soccer_live()
        elif sport == 'cricket': res = statpal_service.get_cricket_live()
        else: res = statpal_service.get_nba_live()

        if not res['success']: continue
        data = res['data']
        matches = []

        # --- parsing based on your samples ---
        if sport == 'soccer':
            for lg in data.get('live_matches', {}).get('league', []):
                matches.extend(lg.get('match', []))
        elif sport == 'cricket':
            for cat in data.get('scores', {}).get('category', []):
                m = cat.get('match')
                if m: matches.append(m)
        elif sport == 'nba':
            matches = data.get('livescores', {}).get('tournament', {}).get('match', [])

        # --- Saving to Event Table ---
        for m in matches:
            try:
                home_raw, away_raw = m.get('home', {}), m.get('away', {})
                # Get precise IDs
                home = get_or_create_precise_entity(home_raw.get('id'), home_raw.get('name'), sport)
                away = get_or_create_precise_entity(away_raw.get('id'), away_raw.get('name'), sport)

                Event.objects.update_or_create(
                    api_source='statpal',
                    external_id=str(m.get('main_id') or m.get('id')),
                    defaults={
                        'sport': 'basketball' if sport == 'nba' else sport,
                        'home_entity': home,
                        'away_entity': away,
                        'status': 'completed' if m.get('status') == 'Finished' else 'live',
                        'home_score': home_raw.get('goals') or home_raw.get('totalscore') or 0,
                        'away_score': away_raw.get('goals') or away_raw.get('totalscore') or 0,
                        'start_time': timezone.now(),
                        'metadata': m
                    }
                )
                total_saved += 1
            except Exception:
                continue

    return f"StatPal Sync: {total_saved} matches saved to DB"


from apps.entity.utils.matcher import get_or_create_precise_entity
from apps.sports_apis.services.statpal import statpal_service
from django.utils import timezone
from celery import shared_task

@shared_task
def sync_statpal_working_task():
    from apps.entity.utils.matcher import get_or_create_precise_entity
    from datetime import datetime

    res = statpal_service.get_soccer_live()
    if not res['success']: return "Failed"

    data = res['data']
    leagues_raw = data.get('live_matches', {}).get('league', [])
    
    saved_count = 0
    for lg_raw in leagues_raw:
        league_entity = get_or_create_precise_entity(
            lg_raw.get('id'), lg_raw.get('name'), 'soccer', entity_type='league'
        )

        matches = lg_raw.get('match', [])
        for m in matches:
            try:
                home_raw = m.get('home', {})
                away_raw = m.get('away', {})
                
                home = get_or_create_precise_entity(home_raw.get('id'), home_raw.get('name'), 'soccer')
                away = get_or_create_precise_entity(away_raw.get('id'), away_raw.get('name'), 'soccer')

                try:
                    dt_str = f"{m.get('date')} {m.get('time')}"
                    start_time = datetime.strptime(dt_str, "%d.%m.%Y %H:%M")
                except:
                    start_time = timezone.now()

                from apps.event.models import Event
                Event.objects.update_or_create(
                    api_source='statpal',
                    external_id=str(m.get('main_id')),
                    defaults={
                        'sport': 'soccer',
                        'home_entity': home,
                        'away_entity': away,
                        'league': league_entity, # লিগ ম্যাপ করা হলো
                        'status': 'live' if m.get('status') not in ['FT', 'Finished'] else 'completed',
                        'status_detail': m.get('status', 'live'),
                        'home_score': int(home_raw.get('goals', 0)),
                        'away_score': int(away_raw.get('goals', 0)),
                        'venue_name': m.get('venue', ''), 
                        'start_time': start_time,
                        'metadata': m
                    }
                )
                saved_count += 1
            except Exception as e:
                continue

    return f"Processed {saved_count} matches with full info."


@shared_task
def sync_statpal_orchestrator_task():
    """
    Unified task for Soccer, NBA, and Cricket.
    Updates both 'Event' (Calendar) and 'LiveScore' (Real-time).
    """
    from apps.score.models import LiveScore
    from apps.sports_apis.tasks import _publish
    from apps.event.models import Event

    sports = ['soccer', 'cricket', 'nba']
    total_matches = 0

    for sport in sports:
        if sport == 'soccer': res = statpal_service.get_soccer_live()
        elif sport == 'cricket': res = statpal_service.get_cricket_live()
        else: res = statpal_service.get_nba_live()

        if not res['success']: continue
        data = res['data']
        matches = []

        # --- Dynamic Parsing for V1 & V2 ---
        if sport == 'soccer':
            for lg in data.get('live_matches', {}).get('league', []):
                matches.extend(lg.get('match', []))
        elif sport == 'cricket':
            for cat in data.get('scores', {}).get('category', []):
                m = cat.get('match')
                if m: matches.append(m)
        elif sport == 'nba':
            matches = data.get('livescores', {}).get('tournament', {}).get('match', [])

        # --- Saving and Publishing ---
        for m in matches:
            try:
                h_raw, a_raw = m.get('home', {}), m.get('away', {})
                h_score = h_raw.get('goals') or h_raw.get('totalscore') or 0
                a_score = a_raw.get('goals') or a_raw.get('totalscore') or 0

                home = get_or_create_precise_entity(h_raw.get('id'), h_raw.get('name'), sport, logo=h_raw.get('logo'))
                away = get_or_create_precise_entity(a_raw.get('id'), a_raw.get('name'), sport, logo=a_raw.get('logo'))

                # 1. Update Event (For Calendar/Detail page)
                Event.objects.update_or_create(
                    api_source='statpal',
                    external_id=str(m.get('main_id') or m.get('id')),
                    defaults={
                        'sport': 'basketball' if sport == 'nba' else sport,
                        'home_entity': home, 'away_entity': away,
                        'status': 'live', 'home_score': int(h_score), 'away_score': int(a_score),
                        'start_time': timezone.now(), 'metadata': m
                    }
                )

                # 2. Update LiveScore (For WebSocket/Live Ticker)
                live_obj, _ = LiveScore.objects.update_or_create(
                    sport=sport, external_id=str(m.get('main_id') or m.get('id')),
                    defaults={
                        'home_team': home.name, 'away_team': away.name,
                        'home_score': int(h_score), 'away_score': int(a_score),
                        'status': 'live', 'status_detail': m.get('status', 'live'),
                        'start_time': timezone.now(), 'raw_data': m
                    }
                )
                
                # Push update to WebSocket
                _publish(live_obj)
                total_matches += 1
            except:
                continue

    return f"StatPal Orchestrator: Processed {total_matches} matches across all sports."



"""
apps/event/tasks.py

sync_statpal_data() — Soccer + NBA + Cricket একসাথে sync করে।

StatPal response root keys:
  Soccer live  : live_matches → league[]          → match[]
  Soccer daily : <dynamic_key> → league[]         → match[]
  NBA live     : livescores → tournament           → match[]
  NBA daily    : scores → tournament               → match[]
  Cricket live : scores → category[]              → match  (single object)
  Cricket fix  : fixtures → category[]            → match  (single object)

LiveScore fields: sport, external_id, home_team, away_team, home_logo, away_logo,
                  home_score, away_score, status, status_detail, start_time,
                  raw_data, updated_at  (unique_together: sport + external_id)

Event fields   : api_source, external_id, sport, home_entity, away_entity, league,
                 status, status_detail, home_score, away_score, venue_name,
                 start_time, metadata  (unique_together: api_source + external_id)
"""
import logging
from datetime import datetime

from celery import shared_task
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.event.models import Event
from apps.score.models import LiveScore
from apps.sports_apis.services.statpal import statpal_service
from apps.entity.utils.matcher import get_or_create_precise_entity

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Status helpers
# ------------------------------------------------------------------ #
_FINISHED = {
    "FT", "AET", "PEN", "Finished", "After Over Time",
    "Full-time", "finished", "ft", "aet", "CANC", "ABD",
}
_LIVE = {
    "1H", "2H", "HT", "ET", "BT", "P", "SUSP", "INT", "LIVE",
    "In Progress", "In Play", "live",
}


def _map_status(raw: str):
    """Returns 'live', 'upcoming', or None (= skip completed)."""
    if raw in _FINISHED:
        return None
    if raw in _LIVE:
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


# ------------------------------------------------------------------ #
# Extractors — return normalised list of dicts
# ------------------------------------------------------------------ #

def _soccer_rows(data: dict) -> list:
    if "live_matches" in data:
        leagues = data["live_matches"].get("league", [])
    else:
        leagues = []
        for v in data.values():
            if isinstance(v, dict) and "league" in v:
                leagues = v["league"]
                break

    rows = []
    for lg in leagues:
        for m in lg.get("match", []):
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


def _nba_rows(data: dict) -> list:
    tournament = (
        data.get("livescores", {}).get("tournament")
        or data.get("scores", {}).get("tournament")
        or {}
    )
    league_id   = str(tournament.get("id", "nba"))
    league_name = tournament.get("league", "NBA")

    rows = []
    for m in tournament.get("match", []):
        home = m.get("home", {})
        away = m.get("away", {})
        rows.append({
            "external_id": str(m.get("id", "")),
            "sport": "nba",
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


# ------------------------------------------------------------------ #
# _publish — existing WebSocket helper (imported at call site)
# ------------------------------------------------------------------ #

def _publish(live_obj: LiveScore):
    """
    Call your existing publish function.
    Adjust the import path to wherever _publish lives in your project.
    """
    try:
        from apps.score.consumers import publish_live_score   # adjust if needed
        publish_live_score(live_obj)
    except ImportError:
        pass   # WebSocket layer not available (e.g. during tests)
    except Exception:
        logger.exception("WebSocket publish failed for LiveScore id=%s", live_obj.pk)


# ------------------------------------------------------------------ #
# Core save helpers
# ------------------------------------------------------------------ #

def _save_event(row: dict) -> Event | None:
    """Save to Event model. Returns None if match is completed (skip)."""
    status = _map_status(row["status_raw"])
    if status is None or not row["external_id"]:
        return None

    sport = row["sport"]
    league = get_or_create_precise_entity(
        row["league_id"], row["league_name"], sport, entity_type="league"
    )
    home = get_or_create_precise_entity(
        row["home_id"], row["home_name"], sport, entity_type="team"
    )
    away = get_or_create_precise_entity(
        row["away_id"], row["away_name"], sport, entity_type="team"
    )
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
    """
    Upsert LiveScore (unique: sport + external_id).
    LiveScore.sport choices: 'nba', 'soccer', 'cricket', etc.
    Only saves when status is live or upcoming (completed already skipped upstream).
    """
    status = _map_status(row["status_raw"])
    if status is None:
        return

    # LiveScore.sport must match its SPORTS_CHOICES — 'nba' not 'basketball'
    ls_sport = row["sport"]   # 'soccer', 'nba', 'cricket'

    live_obj, _ = LiveScore.objects.update_or_create(
        sport=ls_sport,
        external_id=row["external_id"],
        defaults={
            "home_team":     row["home_name"],
            "away_team":     row["away_name"],
            "home_logo":     event.home_entity.logo_url,
            "away_logo":     event.away_entity.logo_url,
            "home_score":    row["home_score"] or None,
            "away_score":    row["away_score"] or None,
            "status":        status,
            "status_detail": row["status_raw"],
            "start_time":    event.start_time,
            "raw_data":      row["raw"],
        },
    )

    if status == "live":
        _publish(live_obj)
        cache.set(f"live_scores_{ls_sport}", True, timeout=120)


# ------------------------------------------------------------------ #
# Celery task
# ------------------------------------------------------------------ #

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sync_statpal_data(self):
    """
    Fetches live + today's fixtures for Soccer, NBA, Cricket.
    Saves to both Event and LiveScore models.
    Publishes via WebSocket for live matches.

    Recommended beat schedule: every 60 seconds.
    """
    fetches = [
        ("soccer",  statpal_service.get_soccer_live,     _soccer_rows),
        ("soccer",  statpal_service.get_soccer_fixtures,  _soccer_rows),
        ("nba",     statpal_service.get_nba_live,         _nba_rows),
        ("nba",     statpal_service.get_nba_fixtures,     _nba_rows),
        ("cricket", statpal_service.get_cricket_live,     _cricket_rows),
        ("cricket", statpal_service.get_cricket_fixtures, _cricket_rows),
    ]

    saved = skipped = errors = 0

    for sport, fetch_fn, extract_fn in fetches:
        result = fetch_fn()
        if not result["success"]:
            logger.warning("[StatPal] %s fetch failed: %s", sport, result.get("error"))
            continue

        for row in extract_fn(result["data"]):
            try:
                with transaction.atomic():
                    event = _save_event(row)
                    if event is None:
                        skipped += 1
                        continue
                    _save_livescore(row, event)
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