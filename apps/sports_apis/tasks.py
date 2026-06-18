from datetime import timedelta
import time
from celery import shared_task
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone
from apps.sports_apis.services.balldontlie import balldontlie_service
from apps.sports_apis.services.api_sports import api_sports_service
from apps.sports_apis.services.api_cricket import api_cricket_service
from apps.score.models import LiveScore
from apps.score.serializers import LiveScoreSerializer
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from datetime import datetime, timedelta
import logging
from apps.sports_apis.services.statpal import statpal_service
logger = logging.getLogger(__name__)


def _publish(live_score_obj):
    """Push live score updates to WebSocket consumers"""
    try:
        channel_layer = get_channel_layer()
        data = dict(LiveScoreSerializer(live_score_obj).data)
        payload = {'type': 'score_update', 'game': data}

        # push to global group
        async_to_sync(channel_layer.group_send)('live_scores', payload)

        # push to sport-specific group
        async_to_sync(channel_layer.group_send)(
            f"live_scores_{live_score_obj.sport}", payload
        )
    except Exception as e:
        logger.error(f"WebSocket publish failed: {e}")


@shared_task
def update_nba_live_scores():
    """
    Update NBA live scores — runs every 2 minutes.
    FIXED: Uses /games?dates[]=today (free tier compatible)
    instead of /box_scores/live (paid only → was causing 401 every 2 min).
    """
    logger.info("Updating NBA live scores...")

    today = timezone.now().date().isoformat()
    result = balldontlie_service.get_games_by_date('nba', today)

    if not result['success']:
        logger.error(f"NBA update failed: {result.get('error')}")
        return f"NBA update failed: {result.get('error')}"

    data = result['data']
    cache.set('live_scores_nba', data,
              timeout=settings.CACHE_TTLS.get('live_scores', 120))

    all_games = data.get('data', [])

    # Status mapping for BallDontLie game statuses
    # status field is a string like "Final", "1st Qtr", "2nd Qtr", "Halftime" etc
    LIVE_STATUSES = {'1st Qtr', '2nd Qtr', '3rd Qtr', '4th Qtr',
                     'Halftime', 'Half', 'OT', '1 OT', '2 OT'}

    saved = 0
    for game in all_games:
        game_status_raw = game.get('status', '')

        if game_status_raw == 'Final':
            status = 'completed'
        elif game_status_raw in LIVE_STATUSES:
            status = 'live'
        else:
            status = 'upcoming'

        live_score, _ = LiveScore.objects.update_or_create(
            sport='nba',
            external_id=str(game.get('id')),
            defaults={
                'home_team':     game.get('home_team', {}).get('full_name', ''),
                'away_team':     game.get('visitor_team', {}).get('full_name', ''),
                'home_logo':     _nba_logo(game.get('home_team', {}).get('id')),
                'away_logo':     _nba_logo(game.get('visitor_team', {}).get('id')),
                'home_score':    game.get('home_team_score'),
                'away_score':    game.get('visitor_team_score'),
                'status':        status,
                'status_detail': game_status_raw,
                'start_time':    game.get('date'),
                'raw_data':      game,
            }
        )

        if status == 'live':
            _publish(live_score)

        saved += 1

    live_count = sum(1 for g in all_games if g.get(
        'status', '') in LIVE_STATUSES)
    logger.info(f"NBA: {saved} games saved, {live_count} live")
    return f"NBA: {saved} games ({live_count} live)"


def _nba_logo(team_id) -> str:
    """Return NBA CDN logo URL for a team ID."""
    if not team_id:
        return ''
    return f"https://cdn.nba.com/logos/nba/{team_id}/global/L/logo.svg"


@shared_task
def update_nfl_live_scores():
    """Update NFL live scores - runs every 2 minutes"""
    logger.info("Updating NFL live scores...")

    result = balldontlie_service.get_live_games('nfl')

    if result['success']:
        data = result['data']
        cache.set('live_scores_nfl', data,
                  timeout=settings.CACHE_TTLS['live_scores'])

        # For NFL, filter games that are currently live
        all_games = data.get('data', [])
        games = [game for game in all_games if game.get('status') == 'Live']

        for game in games:
            live_score, _ = LiveScore.objects.update_or_create(
                sport='nfl',
                external_id=str(game.get('id')),
                defaults={
                    'home_team': game.get('home_team', {}).get('name', ''),
                    'away_team': game.get('visitor_team', {}).get('name', ''),
                    'home_score': game.get('home_team_score'),
                    'away_score': game.get('visitor_team_score'),
                    'status': 'live' if game.get('status') == 'Live' else 'completed',
                    'status_detail': game.get('quarter', ''),
                    'start_time': game.get('date'),
                    'raw_data': game,
                }
            )
            _publish(live_score)  # push to WebSocket

        logger.info(f"NFL: Updated {len(games)} live games")
        return f"NFL: {len(games)} games updated"
    else:
        logger.error(f"NFL update failed: {result.get('error')}")
        return f"NFL update failed"


@shared_task
def update_soccer_live_scores():
    """Update Soccer live scores - runs every 2 minutes"""
    logger.info("Updating Soccer live scores...")

    result = api_sports_service.get_live_fixtures()

    if result['success']:
        data = result['data']
        cache.set('live_scores_soccer', data,
                  timeout=settings.CACHE_TTLS['live_scores'])

        fixtures = data.get('response', [])
        for fixture in fixtures:
            live_score, _ = LiveScore.objects.update_or_create(
                sport='soccer',
                external_id=str(fixture.get('fixture', {}).get('id')),
                defaults={
                    'home_team': fixture.get('teams', {}).get('home', {}).get('name', ''),
                    'away_team': fixture.get('teams', {}).get('away', {}).get('name', ''),
                    'home_logo': fixture.get('teams', {}).get('home', {}).get('logo', ''),
                    'away_logo': fixture.get('teams', {}).get('away', {}).get('logo', ''),
                    'home_score': fixture.get('goals', {}).get('home'),
                    'away_score': fixture.get('goals', {}).get('away'),
                    'status': 'live',
                    'status_detail': fixture.get('fixture', {}).get('status', {}).get('short', ''),
                    'start_time': fixture.get('fixture', {}).get('date'),
                    'raw_data': fixture,
                }
            )
            _publish(live_score)  # push to WebSocket

        logger.info(f"Soccer: Updated {len(fixtures)} live games")
        return f"Soccer: {len(fixtures)} games updated"
    else:
        logger.error(f"Soccer update failed: {result.get('error')}")
        return f"Soccer update failed"


@shared_task
def update_cricket_live_scores():
    """Update Cricket live scores - runs every 2 minutes"""
    logger.info("Updating Cricket live scores...")

    result = api_cricket_service.get_live_scores()

    if not result['success']:
        logger.error(f"Cricket update failed: {result.get('error')}")
        return "Cricket update failed"

    data = result['data']
    matches = data.get('result', [])

    if not matches:
        return "Cricket: 0 live matches right now"

    saved = 0
    for match in matches:
        home_team = match.get('event_home_team', '')
        away_team = match.get('event_away_team', '')

        # these are the actual score strings like "84/1"
        home_score_str = match.get('event_home_final_result', '') or ''
        away_score_str = match.get('event_away_final_result', '') or ''

        # run rates
        home_rr = match.get('event_home_rr') or ''
        away_rr = match.get('event_away_rr') or ''

        # match type: TEST / ODI / T20
        match_type = match.get('event_type', '')

        # toss info
        toss = match.get('event_toss', '')

        # status info like "Day 1 - Rhinos chose to bat"
        status_info = match.get('event_status_info', '')

        # event_status = (match.get('event_status') or '').lower()
        # if 'live' in event_status or 'progress' in event_status:
        #     status = 'live'
        # elif 'finished' in event_status or 'stumps' in event_status:
        #     status = 'completed'
        # else:
        #     status = 'live'

        event_status = (match.get('event_status') or '').lower()
        LIVE_KEYWORDS = ('live', 'progress', 'lunch',
                         'tea', 'drinks', 'innings break')
        COMPLETED_KEYWORDS = ('finished', 'stumps', 'result', 'ended', 'complete',
                              'won', 'drawn', 'tied', 'abandoned', 'cancelled',
                              'no result', 'postponed', 'suspended', 'interrupted')

        if any(k in event_status for k in LIVE_KEYWORDS):
            status = 'live'
        elif any(k in event_status for k in COMPLETED_KEYWORDS):
            status = 'completed'
        else:
            status = 'upcoming'

        external_id = str(match.get('event_key', ''))
        if not external_id or not home_team:
            continue

        start_time = match.get('event_date_start')
        if not start_time:
            continue

        live_score, _ = LiveScore.objects.update_or_create(
            sport='cricket',
            external_id=external_id,
            defaults={
                'home_team': home_team,
                'away_team': away_team,
                'home_logo': match.get('event_home_team_logo') or '',
                'away_logo': match.get('event_away_team_logo') or '',
                'home_score': None,  # cricket scores aren't integers
                'away_score': None,
                'status': status,
                # frontend shows this on the card
                'status_detail': f"{home_score_str} | {away_score_str}".strip(' |'),
                'start_time': start_time,
                'raw_data': match,  # full scorecard/comments stored here
                'metadata': {
                    'match_type': match_type,
                    'toss': toss,
                    'status_info': status_info,
                    'home_rr': home_rr,
                    'away_rr': away_rr,
                    'league': match.get('league_name', ''),
                    'stadium': match.get('event_stadium', ''),
                }
            }
        )
        _publish(live_score)  # push to WebSocket
        saved += 1

    logger.info(f"Cricket: saved {saved} live matches")
    return f"Cricket: {saved} matches updated"


@shared_task
def update_soccer_live_scores():
    """
    Update soccer live scores — runs every 2 minutes.
    Writes to LiveScore table AND pushes to WebSocket.
    Also updates the Event model status via the existing task.
    """
    logger.info("Updating Soccer live scores...")

    result = api_sports_service.get_live_fixtures()

    if not result['success']:
        logger.error(f"Soccer update failed: {result.get('error')}")
        return f"Soccer update failed: {result.get('error')}"

    data = result['data']
    cache.set(
        'live_scores_soccer',
        data,
        timeout=settings.CACHE_TTLS.get('live_scores', 120)
    )

    fixtures = data.get('response', [])

    if not fixtures:
        # No live games right now — mark all soccer LiveScore rows as completed
        stale = LiveScore.objects.filter(sport='soccer', status='live').count()
        if stale:
            LiveScore.objects.filter(
                sport='soccer', status='live').update(status='completed')
            logger.info(f"Soccer: no live games, marked {stale} as completed")
        return "Soccer: 0 live fixtures"

    saved = 0
    for fixture in fixtures:
        fix_data = fixture.get('fixture', {})
        teams_data = fixture.get('teams', {})
        goals_data = fixture.get('goals', {})
        league_data = fixture.get('league', {})

        external_id = str(fix_data.get('id', ''))
        if not external_id:
            continue

        status_short = fix_data.get('status', {}).get('short', '')
        status_long = fix_data.get('status', {}).get('long', '')
        elapsed = fix_data.get('status', {}).get('elapsed')

        # Map API-Sports status codes to our status
        if status_short in ('FT', 'AET', 'PEN', 'AWD', 'WO'):
            status = 'completed'
        elif status_short in ('PST', 'CANC', 'ABD', 'SUSP', 'INT'):
            status = 'completed'
        elif status_short in ('NS', 'TBD'):
            status = 'upcoming'
        else:
            # 1H, HT, 2H, ET, BT, P, LIVE, BREAK
            status = 'live'

        # Status detail shown on score card e.g. "45'" or "HT" or "FT"
        if elapsed and status == 'live':
            status_detail = f"{elapsed}'"
        else:
            status_detail = status_short

        live_score, _ = LiveScore.objects.update_or_create(
            sport='soccer',
            external_id=external_id,
            defaults={
                'home_team':     teams_data.get('home', {}).get('name', ''),
                'away_team':     teams_data.get('away', {}).get('name', ''),
                'home_logo':     teams_data.get('home', {}).get('logo', ''),
                'away_logo':     teams_data.get('away', {}).get('logo', ''),
                'home_score':    goals_data.get('home'),
                'away_score':    goals_data.get('away'),
                'status':        status,
                'status_detail': status_detail,
                'start_time':    fix_data.get('date'),
                'raw_data':      fixture,
                'metadata': {
                    'league_name': league_data.get('name', ''),
                    'league_logo': league_data.get('logo', ''),
                    'league_country': league_data.get('country', ''),
                    'venue': fix_data.get('venue', {}).get('name', ''),
                    'referee': fix_data.get('referee', ''),
                }
            }
        )

        if status == 'live':
            _publish(live_score)

        saved += 1

    # Clean up any soccer LiveScore rows that were live before but aren't anymore
    live_ext_ids = [
        str(f.get('fixture', {}).get('id', ''))
        for f in fixtures
        if f.get('fixture', {}).get('status', {}).get('short', '') not in
           ('FT', 'AET', 'PEN', 'NS', 'TBD', 'PST', 'CANC', 'ABD')
    ]
    stale = LiveScore.objects.filter(
        sport='soccer',
        status='live',
    ).exclude(external_id__in=live_ext_ids)
    stale_count = stale.count()
    if stale_count:
        stale.update(status='completed')
        logger.info(f"Soccer: cleaned up {stale_count} stale live scores")

    logger.info(f"Soccer: {saved} fixtures saved, {len(live_ext_ids)} live")

    # ── Sync LiveScore status → Event model ──────────────────
    try:
        from apps.event.models import Event as _Event

        # 1. Mark Events as live if their LiveScore is live
        live_ext_ids_for_sync = list(
            LiveScore.objects.filter(sport='soccer', status='live')
            .values_list('external_id', flat=True)
        )
        if live_ext_ids_for_sync:
            synced_live = _Event.objects.filter(
                api_source='api_sports',
                external_id__in=live_ext_ids_for_sync,
                status='upcoming',
            ).update(status='live')
            if synced_live:
                logger.info(f"Soccer sync: {synced_live} Events → live")

        # 2. Mark Events as completed if their LiveScore is completed
        completed_ext_ids = list(
            LiveScore.objects.filter(sport='soccer', status='completed')
            .values_list('external_id', flat=True)
        )
        if completed_ext_ids:
            synced_done = _Event.objects.filter(
                api_source='api_sports',
                external_id__in=completed_ext_ids,
                status__in=['live', 'upcoming'],
            ).update(status='completed')
            if synced_done:
                logger.info(f"Soccer sync: {synced_done} Events → completed")
                # Trigger stats fetch for newly completed events
                from apps.event.tasks import fetch_event_details, check_completed_events
                check_completed_events.delay()

    except Exception as e:
        logger.error(f"LiveScore→Event sync failed: {e}")

    return f"Soccer: {saved} fixtures ({len(live_ext_ids)} live)"


logger = logging.getLogger(__name__)


def _name_similarity(a: str, b: str) -> float:
    """Simple word overlap similarity 0.0-1.0."""
    a_words = set(a.lower().split())
    b_words = set(b.lower().split())
    if not a_words or not b_words:
        return 0.0
    overlap = a_words & b_words
    return len(overlap) / max(len(a_words), len(b_words))


@shared_task
def enrich_missing_logos(dry_run: bool = False):
    """
    Find all entities with no logo_url and try to fill from TheSportsDB.
    Focuses on: cricket teams, cricket leagues (major ones only), NBA teams.

    Rate limit: free key = 30 req/min, so we sleep 2s between calls.
    """
    from apps.entity.models import Entity
    from apps.sports_apis.services.thesportsdb import thesportsdb_service

    missing = Entity.objects.filter(
        logo_url='').order_by('type', 'sport', 'name')
    total = missing.count()
    logger.info(f"enrich_missing_logos: {total} entities need logos")

    updated = 0
    skipped = 0
    not_found = 0

    for entity in missing:
        # Skip obscure cricket tour series — TheSportsDB won't have them
        # e.g. "Afghanistan A tour of Oman", "Under-19s tour of Nepal"
        name = entity.name
        skip_keywords = ['tour of', 'under-19', 'under-23', 'u19', 'u23',
                         'emerging', 'a v ', ' a tour', 'tri-series',
                         'women tour', 'invite']
        if any(kw in name.lower() for kw in skip_keywords) and entity.type == 'league':
            skipped += 1
            continue

        logo_url = ''

        if entity.type == 'team':
            logo_url = thesportsdb_service.get_team_badge(name)

            # If no exact match, try stripping common suffixes
            if not logo_url:
                stripped = (name.replace(' Women', '')
                            .replace(' Men', '')
                            .replace(' FC', '')
                            .replace(' CF', '')
                            .replace(' SC', '')
                            .strip())
                if stripped != name:
                    logo_url = thesportsdb_service.get_team_badge(stripped)

        elif entity.type == 'league':
            # Only try major cricket leagues — skip obscure bilateral tours
            major_keywords = ['ipl', 'bbl', 'cpl', 'psl', 'icc', 'test',
                              'world cup', 'champions', 'premier', 'super',
                              'league', 't20', 'one day', 'odi']
            if any(kw in name.lower() for kw in major_keywords):
                logo_url = thesportsdb_service.get_league_badge(
                    name, entity.sport)

        if logo_url:
            if not dry_run:
                entity.logo_url = logo_url
                entity.save(update_fields=['logo_url'])
            updated += 1
            logger.info(f"  ✓ [{entity.type}] {name} → {logo_url[:60]}")
        else:
            not_found += 1

        # Respect rate limit — 30 req/min free = 1 req/2s
        time.sleep(2)

    result = f"Logo enrichment done: {updated} updated, {skipped} skipped (obscure tours), {not_found} not found on TheSportsDB"
    logger.info(result)
    return result


@shared_task
def enrich_entity_logo(entity_id: int):
    """
    Enrich logo for a single entity. Call this when a new entity is created
    and has no logo. Can be triggered from _get_or_create_team_entity.
    """
    from apps.entity.models import Entity
    from apps.sports_apis.services.thesportsdb import thesportsdb_service

    try:
        entity = Entity.objects.get(id=entity_id)
    except Entity.DoesNotExist:
        return f"Entity {entity_id} not found"

    if entity.logo_url:
        return f"Entity {entity_id} already has logo"

    logo_url = ''
    if entity.type == 'team':
        logo_url = thesportsdb_service.get_team_badge(entity.name)
    elif entity.type == 'league':
        logo_url = thesportsdb_service.get_league_badge(
            entity.name, entity.sport)

    if logo_url:
        entity.logo_url = logo_url
        entity.save(update_fields=['logo_url'])
        return f"Enriched {entity.name} → {logo_url[:60]}"

    return f"No logo found for {entity.name}"


@shared_task
def enrich_event_highlights_today():
    """
    Fetch YouTube highlights from TheSportsDB for completed events
    and store them in EventHighlight + event.metadata.

    TheSportsDB returns highlights with empty home_team/away_team fields.
    We parse the event_name string ("Team A vs Team B") instead.

    Runs daily at 11:30pm. Also checks yesterday in case of delays.
    """
    from apps.event.models import Event, EventHighlight
    from apps.sports_apis.services.thesportsdb import thesportsdb_service

    matched_total = 0

    # Check both yesterday and today — highlights often appear hours after game
    for days_ago in [0, 1]:
        check_date = (timezone.now() - timedelta(days=days_ago)).date()
        date_str = check_date.isoformat()

        highlights = thesportsdb_service.get_event_highlights(date_str)
        if not highlights:
            continue

        # Filter to sports we care about
        relevant = [
            h for h in highlights
            if h['sport'] in ('soccer', 'cricket', 'basketball', 'ice hockey', 'baseball')
        ]

        for hl in relevant:
            url = hl.get('highlight_url', '')
            if not url:
                continue

            # Parse "Team A vs Team B" from event_name
            event_name = hl.get('event_name', '')
            if ' vs ' in event_name:
                parts = event_name.split(' vs ', 1)
                home_str = parts[0].strip()
                away_str = parts[1].strip()
            elif ' v ' in event_name:
                parts = event_name.split(' v ', 1)
                home_str = parts[0].strip()
                away_str = parts[1].strip()
            else:
                # Can't parse — skip
                continue

            if not home_str or not away_str:
                continue

            # Find matching completed event in our DB
            candidates = Event.objects.filter(
                start_time__date=check_date,
                status='completed',
            ).select_related('home_entity', 'away_entity')

            best_event = None
            best_score = 0.0

            for event in candidates:
                home_name = event.home_entity.name if event.home_entity else ''
                away_name = event.away_entity.name if event.away_entity else ''

                home_sim = _name_similarity(home_str, home_name)
                away_sim = _name_similarity(away_str, away_name)
                combined = (home_sim + away_sim) / 2

                if combined > best_score:
                    best_score = combined
                    best_event = event

            # Require at least 40% combined similarity
            if not best_event or best_score < 0.55:
                continue

            # Save to EventHighlight table
            highlight_obj, created = EventHighlight.objects.get_or_create(
                event=best_event,
                video_url=url,
                defaults={
                    'title':         event_name,
                    'thumbnail_url': hl.get('thumbnail', ''),
                    'source':        'youtube',
                    'external_id':   url.split('v=')[-1].split('&')[0] if 'v=' in url else '',
                }
            )

            # Also store in metadata for quick access without join
            if created:
                meta = best_event.metadata or {}
                meta['highlight_url'] = url
                meta['highlight_thumb'] = hl.get('thumbnail', '')
                best_event.metadata = meta
                best_event.save(update_fields=['metadata'])
                matched_total += 1
                logger.info(
                    f"  ✓ Highlight matched: {event_name} → "
                    f"{best_event.home_entity.name} vs {best_event.away_entity.name} "
                    f"(score={best_score:.2f})"
                )

    return f"Highlights enriched: {matched_total} events matched"


@shared_task
def fix_stale_cricket_live_scores():
    """
    One-time + ongoing safety net: mark cricket LiveScore rows as completed
    if their raw_data event_status is not actually live.
    Run this manually after deploying the status fix in tasks.py.
    Also safe to run periodically as a cleanup.
    """
    from apps.score.models import LiveScore

    COMPLETED_STATUSES = [
        'Cancelled', 'Postponed', 'Suspended', 'Abandoned',
        'Interrupted', 'Result', 'Finished', 'Ended',
        'No Result', 'Drawn', 'Won', 'Tied',
    ]

    total_fixed = 0
    for status_str in COMPLETED_STATUSES:
        count = LiveScore.objects.filter(
            sport='cricket',
            status='live',
            raw_data__event_status=status_str,
        ).update(status='completed')
        if count:
            logger.info(f"  Fixed '{status_str}': {count}")
            total_fixed += count

    return f"Fixed {total_fixed} stale cricket live scores"


@shared_task(bind=True, max_retries=3, default_retry_delay=900)
def fetch_highlight_for_event(self, event_id: int):
    """
    Search for a highlight video (YouTube) for a given Event.
    Tries TheSportsDB first, then falls back to Brave Search.
    Retries up to 3 times (every 15 minutes) if not found.
    """
    from apps.event.models import Event, EventHighlight
    from apps.sports_apis.services.thesportsdb import thesportsdb_service
    import requests
    from django.conf import settings

    try:
        event = Event.objects.select_related(
            'home_entity', 'away_entity').get(id=event_id)
    except Event.DoesNotExist:
        return f"Event {event_id} not found"

    # Check if highlight already exists
    if EventHighlight.objects.filter(event=event).exists():
        return f"Highlight already exists for Event {event_id}"

    home_name = event.home_entity.name if event.home_entity else ''
    away_name = event.away_entity.name if event.away_entity else ''

    if not home_name or not away_name:
        return f"Event {event_id} is missing home/away entities"

    found_url = None
    found_thumb = None
    found_title = None

    # ── 1. Try TheSportsDB First ───────────────────────────────────────────
    try:
        date_str = event.start_time.date().isoformat()
        highlights = thesportsdb_service.get_event_highlights(date_str)
        if highlights:
            best_score = 0.0
            best_hl = None
            for hl in highlights:
                event_name = hl.get('event_name', '')
                if ' vs ' in event_name:
                    parts = event_name.split(' vs ', 1)
                    h_str, a_str = parts[0].strip(), parts[1].strip()
                elif ' v ' in event_name:
                    parts = event_name.split(' v ', 1)
                    h_str, a_str = parts[0].strip(), parts[1].strip()
                else:
                    continue

                home_sim = _name_similarity(h_str, home_name)
                away_sim = _name_similarity(a_str, away_name)
                combined = (home_sim + away_sim) / 2

                if combined > best_score:
                    best_score = combined
                    best_hl = hl

            if best_hl and best_score >= 0.55:
                found_url = best_hl.get('highlight_url')
                found_thumb = best_hl.get('thumbnail')
                found_title = best_hl.get('event_name')
                logger.info(
                    f"Highlight found via TheSportsDB for Event {event_id} (score={best_score:.2f})")
    except Exception as e:
        logger.warning(
            f"TheSportsDB highlight lookup failed for Event {event_id}: {e}")

    # ── 2. Fallback to Brave Search ────────────────────────────────────────
    if not found_url:
        brave_key = getattr(settings, 'BRAVESEARCH_KEY', '')
        if brave_key:
            query = f'"{home_name}" vs "{away_name}" {event.sport} highlights site:youtube.com'
            headers = {
                "X-Subscription-Token": brave_key,
                "Accept": "application/json",
            }
            url = "https://api.search.brave.com/res/v1/web/search"
            try:
                resp = requests.get(url, headers=headers, params={
                                    "q": query, "count": 3}, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    web_results = data.get('web', {}).get('results', [])
                    for item in web_results:
                        item_url = item.get('url', '')
                        if 'youtube.com/watch' in item_url or 'youtu.be/' in item_url:
                            found_url = item_url
                            found_title = item.get(
                                'title', f"{home_name} vs {away_name} Highlights")
                            found_thumb = item.get('thumbnail', {}).get(
                                'src', '') if isinstance(item.get('thumbnail'), dict) else ''
                            logger.info(
                                f"Highlight found via Brave Search for Event {event_id}: {found_url}")
                            break
            except Exception as e:
                logger.warning(
                    f"Brave search fallback failed for Event {event_id}: {e}")

    # ── 3. Save if found, otherwise retry ──────────────────────────────────
    if found_url:
        yt_id = ''
        if 'v=' in found_url:
            yt_id = found_url.split('v=')[-1].split('&')[0]
        elif 'youtu.be/' in found_url:
            yt_id = found_url.split('youtu.be/')[-1].split('?')[0]

        highlight_obj, created = EventHighlight.objects.get_or_create(
            event=event,
            video_url=found_url,
            defaults={
                'title': found_title or f"{home_name} vs {away_name} Highlights",
                'thumbnail_url': found_thumb or '',
                'source': 'youtube',
                'external_id': yt_id,
            }
        )

        # Sync to event metadata
        meta = event.metadata or {}
        meta['highlight_url'] = found_url
        meta['highlight_thumb'] = found_thumb or ''
        event.metadata = meta
        event.save(update_fields=['metadata'])

        return f"Successfully saved highlight for Event {event_id}"
    else:
        # Retry up to 3 times (with 15 min delays)
        if self.request.retries < self.max_retries:
            logger.info(
                f"Highlight not found yet for Event {event_id}. Retrying in 15 minutes (Retry {self.request.retries + 1}/{self.max_retries})...")
            raise self.retry()
        else:
            return f"Highlight search completed. No highlight found for Event {event_id}"


@shared_task
def fetch_highlights_for_recently_completed_events():
    """
    Scans for events completed in the last 24 hours that do not have highlights
    and triggers a fetch task for each of them.
    """
    from apps.event.models import Event, EventHighlight
    from django.utils import timezone
    from datetime import timedelta

    cutoff = timezone.now() - timedelta(hours=24)
    # Find completed events in last 24 hours
    events = Event.objects.filter(
        status='completed',
        start_time__gte=cutoff,
    ).exclude(
        id__in=EventHighlight.objects.values_list('event_id', flat=True)
    )

    count = 0
    for event in events:
        fetch_highlight_for_event.delay(event.id)
        count += 1

    logger.info(
        f"fetch_highlights_for_recently_completed_events: triggered {count} tasks")
    return f"Triggered highlight checks for {count} completed events"


"""========================================================

            (((--------New Code StatPal Integration---------)))

==========================================================="""


logger = logging.getLogger(__name__)

# ========================================================
# SOCCER PARSER
# ========================================================


def parse_soccer_live(payload):
    """Soccer live matches parser - শুধু numeric status (live) গুলো নিবে"""
    matches = []
    live_matches = payload.get('live_matches', {})
    leagues = live_matches.get('league', [])

    if isinstance(leagues, dict):
        leagues = [leagues]

    for league in leagues:
        league_matches = league.get('match', [])
        if isinstance(league_matches, dict):
            league_matches = [league_matches]

        for match in league_matches:
            status = match.get('status', '')
            # শুধু numeric status (live) গুলো নিবে
            if status and str(status).replace("'", "").replace("+", "").isdigit():
                matches.append({
                    'match': match,
                    'league': league.get('name', 'Unknown'),
                    'status_minute': status
                })

    return matches

# ========================================================
# CRICKET PARSER
# ========================================================


def parse_cricket_live(payload):
    """Cricket live matches parser - Stumps, Innings, Session ইত্যাদি live"""
    matches = []
    scores = payload.get('scores', {})
    categories = scores.get('category', [])

    if isinstance(categories, dict):
        categories = [categories]

    LIVE_KEYWORDS = ['stumps', 'innings',
                     'session', 'drinks', 'tea', 'rain', 'lunch']

    for cat in categories:
        match_data = cat.get('match')
        if not match_data:
            continue

        if isinstance(match_data, dict):
            match_data = [match_data]

        for match in match_data:
            status = str(match.get('status', '')).lower().strip()
            # Check if it's live
            is_live = any(keyword in status for keyword in LIVE_KEYWORDS)
            # Also check if it's not finished
            is_finished = status in ['finished',
                                     'completed', 'abandoned', 'cancelled']

            if is_live and not is_finished:
                matches.append({
                    'match': match,
                    'category': cat.get('name', 'Unknown'),
                    'status_detail': status
                })

    return matches

# ========================================================
# MAIN TASK: UPDATE LIVE SCORES
# ========================================================


@shared_task
def update_statpal_live_scores(sport):
    """
    StatPal থেকে লাইভ স্কোর এনে LiveScore মডেলে সেভ করবে
    """
    logger.info(f"🔄 Fetching live scores for {sport}...")
    
    result = statpal_service.get_live_scores(sport)
    if not result['success']:
        logger.error(f"❌ Failed to fetch {sport}: {result.get('error')}")
        return f"Failed: {result.get('error')}"
    
    payload = result.get('data', {})
    saved_count = 0
    skipped_count = 0
    
    # Sport-specific parsing
    if sport.lower() == 'soccer':
        parsed = parse_soccer_live(payload)
        logger.info(f"⚽ Found {len(parsed)} live soccer matches")
        
        for item in parsed:
            match = item['match']
            external_id = str(match.get('main_id', match.get('fallback_id_1', '')))
            if not external_id:
                skipped_count += 1
                continue
            
            home = match.get('home', {})
            away = match.get('away', {})
            
            # Get scores
            home_goals = home.get('goals', '0')
            away_goals = away.get('goals', '0')
            
            # Get start time
            start_time = None
            if match.get('date') and match.get('time'):
                try:
                    from datetime import datetime
                    start_time = f"{match.get('date')} {match.get('time')}"
                except:
                    start_time = match.get('date', '')
            
            # Save
            try:
                live_score, created = LiveScore.objects.update_or_create(
                    sport='soccer',
                    external_id=external_id,
                    defaults={
                        'home_team': home.get('name', ''),
                        'away_team': away.get('name', ''),
                        'home_score': int(home_goals) if str(home_goals).isdigit() else 0,
                        'away_score': int(away_goals) if str(away_goals).isdigit() else 0,
                        'status': 'live',
                        'status_detail': f"{item['status_minute']}'",
                        'start_time': start_time,
                        'raw_data': match,
                    }
                )
                saved_count += 1
                logger.info(f"✅ Soccer: {home.get('name')} vs {away.get('name')} ({item['status_minute']}')")
            except Exception as e:
                logger.error(f"❌ Error saving soccer match {external_id}: {e}")
                skipped_count += 1
    
    elif sport.lower() == 'cricket':
        parsed = parse_cricket_live(payload)
        logger.info(f"🏏 Found {len(parsed)} live cricket matches")
        
        for item in parsed:
            match = item['match']
            external_id = str(match.get('id', match.get('main_id', '')))
            if not external_id:
                skipped_count += 1
                continue
            
            home = match.get('home', {})
            away = match.get('away', {})
            
            # Get scores
            home_score = match.get('home_score', 0)
            away_score = match.get('away_score', 0)
            
            # Get start time
            start_time = match.get('start_time', match.get('date', ''))
            
            # Save
            try:
                live_score, created = LiveScore.objects.update_or_create(
                    sport='cricket',
                    external_id=external_id,
                    defaults={
                        'home_team': home.get('name', ''),
                        'away_team': away.get('name', ''),
                        'home_score': int(home_score) if str(home_score).isdigit() else 0,
                        'away_score': int(away_score) if str(away_score).isdigit() else 0,
                        'status': 'live',
                        'status_detail': item['status_detail'],
                        'start_time': start_time,
                        'raw_data': match,
                    }
                )
                saved_count += 1
                logger.info(f"✅ Cricket: {home.get('name')} vs {away.get('name')} ({item['status_detail']})")
            except Exception as e:
                logger.error(f"❌ Error saving cricket match {external_id}: {e}")
                skipped_count += 1
    
    else:
        return f"Unknown sport: {sport}"
    
    result_msg = f"✅ {sport}: {saved_count} saved, {skipped_count} skipped"
    logger.info(result_msg)
    return result_msg

# ========================================================
# BULK UPDATE TASK
# ========================================================


@shared_task
def trigger_all_statpal_live_updates():
    """সব স্পোর্টের লাইভ স্কোর আপডেট করুন"""
    sports = ['soccer', 'cricket']
    results = []

    for sport in sports:
        result = update_statpal_live_scores.delay(sport)
        results.append(result.id)

    return f"🔄 Triggered updates for: {', '.join(sports)}"

# ========================================================
# FORCE UPDATE (Direct - without Celery)
# ========================================================


def force_update_all_scores():
    """Celery ছাড়া সরাসরি আপডেট করুন (Debugging এর জন্য)"""
    from apps.score.models import LiveScore

    # Clear existing
    deleted = LiveScore.objects.filter(status='live').delete()
    print(f"🗑️ Deleted {deleted[0]} old live scores")

    results = {}

    # Soccer
    print("\n⚽ Updating Soccer...")
    results['soccer'] = update_statpal_live_scores('soccer')
    print(results['soccer'])

    # Cricket
    print("\n🏏 Updating Cricket...")
    results['cricket'] = update_statpal_live_scores('cricket')
    print(results['cricket'])

    # Summary
    print("\n" + "="*50)
    print("📊 FINAL SUMMARY")
    print("="*50)
    total = LiveScore.objects.filter(status='live').count()
    print(f"Total live scores: {total}")

    for sport in ['soccer', 'cricket']:
        count = LiveScore.objects.filter(sport=sport, status='live').count()
        print(f"{sport}: {count}")

    return results
