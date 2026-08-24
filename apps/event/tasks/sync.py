import logging
import time
from datetime import datetime, timedelta
from celery import shared_task
from django.core.cache import cache
from django.utils import timezone
from apps.score.models import LiveScore
from apps.sports_apis.services.statpal import statpal_service
from apps.sports_apis.tasks import _publish
from .parsers import (
    _nfl_rows, _soccer_rows, _cricket_rows, _nba_rows, _tennis_rows,
    _mlb_rows, _handball_rows, _volleyball_rows, _golf_rows, _f1_rows,
    _horse_racing_rows, _tsdb_soccer_row
)
from .helpers import _save_event, _save_livescore

logger = logging.getLogger(__name__)


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
            
            time.sleep(0.5)

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


@shared_task
def update_soccer_live_scores_only():
    """Trigger background synchronization of soccer live scores via StatPal."""
    sync_statpal_data.delay()
    return "Delegated to sync_statpal_data"


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

        for day_offset in range(1, 31):
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

        stale_cutoff = timezone.now() - timezone.timedelta(hours=3)
        stale_deleted, _ = (
            LiveScore.objects.filter(status="live", updated_at__lt=stale_cutoff).delete()
        )
        if stale_deleted:
            logger.info("[StatPal] Cleaned up %d stale live score(s) older than 3h.", stale_deleted)

        saved, skipped, errors = 0, 0, 0

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
        daily_offset_sports = [
            ("soccer",     statpal_service.get_soccer_fixtures,   _soccer_rows),
            ("nba",        statpal_service.get_nba_fixtures,      _nba_rows),
            ("football",   statpal_service.get_nfl_fixtures,      _nfl_rows),
            ("tennis",     statpal_service.get_tennis_fixtures,   _tennis_rows),
            ("baseball",   statpal_service.get_mlb_fixtures,      _mlb_rows),
            ("handball",   statpal_service.get_handball_fixtures, _handball_rows),
            ("volleyball", statpal_service.get_volleyball_fixtures, _volleyball_rows),
        ]

        bulk_sports = [
            ("cricket", statpal_service.get_cricket_fixtures, _cricket_rows),
            ("golf",    statpal_service.get_golf_schedule,    _golf_rows),
        ]

        saved, skipped, errors = 0, 0, 0

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

        for offset in range(-7, 8):
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

                time.sleep(0.3)

        msg = f"sync_statpal_fixtures_data — saved={saved}, skipped={skipped}, errors={errors}"
        logger.info(msg)
        return msg
    finally:
        cache.delete(lock_id)
