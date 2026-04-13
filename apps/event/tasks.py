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