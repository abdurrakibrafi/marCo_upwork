from datetime import timedelta
import time
from celery import shared_task
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone
from apps.sports_apis.services.balldontlie import balldontlie_service
from apps.sports_apis.services.api_sports import api_sports_service
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
def update_nfl_live_scores():
    """Delegated to StatPal sync"""
    from apps.event.tasks import sync_statpal_data
    sync_statpal_data.delay()
    return "Delegated to sync_statpal_data"



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

        elif entity.type == 'athlete':
            logo_url = thesportsdb_service.get_player_headshot(name)

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
    elif entity.type == 'athlete':
        logo_url = thesportsdb_service.get_player_headshot(entity.name)

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
        fetch_highlight_for_event.apply_async(args=[event.id], countdown=count * 5)
        count += 1

    logger.info(
        f"fetch_highlights_for_recently_completed_events: triggered {count} tasks")
    return f"Triggered highlight checks for {count} completed events"


@shared_task(bind=True, max_retries=3, default_retry_delay=600)
def backfill_mlb_nhl_rosters_task(self):
    """Weekly task to backfill MLB and NHL rosters using official free APIs"""
    from django.core.management import call_command
    try:
        logger.info("Starting MLB/NHL rosters backfill task...")
        call_command('backfill_mlb_nhl_rosters')
        return "MLB/NHL rosters backfill completed successfully"
    except Exception as exc:
        logger.error(f"Error during MLB/NHL rosters backfill: {exc}")
        raise self.retry(exc=exc)
