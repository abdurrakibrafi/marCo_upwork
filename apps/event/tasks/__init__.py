"""Event Celery background tasks and synchronization routines package.

Re-exports all tasks and utility functions to maintain full backward compatibility with
existing Celery Beat schedules and app imports.
"""

from .parsers import (
    _FINISHED,
    _CANCELLED,
    _LIVE,
    _extract_minute,
    _map_status,
    _parse_dt,
    _safe_int,
    _clean_score,
    _soccer_rows,
    _generic_sport_rows,
    _nba_rows,
    _nfl_rows,
    _hockey_rows,
    _tennis_rows,
    _mlb_rows,
    _handball_rows,
    _volleyball_rows,
    _cricket_rows,
    _f1_rows,
    _golf_position_sort_key,
    _golf_rows,
    _horse_racing_rows,
    _tsdb_soccer_row,
)

from .helpers import (
    _get_or_create_team_entity,
    _get_or_create_league_entity,
    _save_event,
    _save_livescore,
)

from .details import (
    _populate_statpal_event_details,
    _on_the_fly_update_statpal_event,
    fetch_event_details,
    check_completed_events,
    cleanup_stale_live_events,
    reprocess_all_events_stats,
)

from .sync import (
    update_nfl_fixtures,
    update_soccer_fixtures,
    update_statpal_fixtures_for_dates,
    update_all_fixtures,
    update_soccer_live_scores_only,
    sync_thesportsdb_upcoming_fixtures,
    sync_statpal_data,
    sync_statpal_fixtures_data,
)

__all__ = [
    "_FINISHED",
    "_CANCELLED",
    "_LIVE",
    "_extract_minute",
    "_map_status",
    "_parse_dt",
    "_safe_int",
    "_clean_score",
    "_soccer_rows",
    "_generic_sport_rows",
    "_nba_rows",
    "_nfl_rows",
    "_hockey_rows",
    "_tennis_rows",
    "_mlb_rows",
    "_handball_rows",
    "_volleyball_rows",
    "_cricket_rows",
    "_f1_rows",
    "_golf_position_sort_key",
    "_golf_rows",
    "_horse_racing_rows",
    "_tsdb_soccer_row",
    "_get_or_create_team_entity",
    "_get_or_create_league_entity",
    "_save_event",
    "_save_livescore",
    "_populate_statpal_event_details",
    "_on_the_fly_update_statpal_event",
    "fetch_event_details",
    "check_completed_events",
    "cleanup_stale_live_events",
    "reprocess_all_events_stats",
    "update_nfl_fixtures",
    "update_soccer_fixtures",
    "update_statpal_fixtures_for_dates",
    "update_all_fixtures",
    "update_soccer_live_scores_only",
    "sync_thesportsdb_upcoming_fixtures",
    "sync_statpal_data",
    "sync_statpal_fixtures_data",
]
