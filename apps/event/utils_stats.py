"""
apps/event/utils_stats.py

Clean normalization utility for team statistics.
Preserves existing match stats and appends ONLY the requested 4 metric fields:
  - shot_on_goal
  - shot_off_goal
  - block_shots
  - pass_accuracy
"""

import logging

logger = logging.getLogger(__name__)


def normalize_event_stats(stats_dict: dict) -> dict:
    """
    Normalizes team statistics. Preserves existing match stats and appends
    ONLY the 4 specific requested fields without duplicating alias keys.
    """
    if not stats_dict or not isinstance(stats_dict, dict):
        stats_dict = {}

    normalized = {}

    # Preserve existing primitive keys (e.g. side, ft_home, ft_away, ht_home, ht_away, possession, etc.)
    for k, v in stats_dict.items():
        if not isinstance(v, dict):
            normalized[k] = v

    shots = stats_dict.get("shots") if isinstance(stats_dict.get("shots"), dict) else {}
    passes = stats_dict.get("passes") if isinstance(stats_dict.get("passes"), dict) else {}

    # 1. shot_on_goal
    shot_on_goal = (
        shots.get("ongoal") or 
        stats_dict.get("shot_on_goal") or 
        stats_dict.get("shots_on_goal") or 
        stats_dict.get("Shots on Goal") or 
        stats_dict.get("shots_on_target") or 
        0
    )
    normalized["shot_on_goal"] = shot_on_goal

    # 2. shot_off_goal
    shot_off_goal = (
        shots.get("offgoal") or 
        stats_dict.get("shot_off_goal") or 
        stats_dict.get("shots_off_goal") or 
        stats_dict.get("Shots off Goal") or 
        0
    )
    normalized["shot_off_goal"] = shot_off_goal

    # 3. block_shots
    block_shots = (
        shots.get("blocked") or 
        stats_dict.get("block_shots") or 
        stats_dict.get("blocked_shots") or 
        stats_dict.get("Blocked Shots") or 
        0
    )
    normalized["block_shots"] = block_shots

    # 4. pass_accuracy
    pct = None
    if passes:
        pct = passes.get("percentage") or passes.get("percent")
        if pct is None and passes.get("accurate") is not None and passes.get("total"):
            try:
                acc = float(passes["accurate"])
                tot = float(passes["total"])
                if tot > 0:
                    pct = f"{int(round((acc / tot) * 100))}%"
            except (ValueError, TypeError, ZeroDivisionError):
                pass

    if pct is None:
        pct = (
            stats_dict.get("pass_accuracy") or 
            stats_dict.get("passes_%") or 
            stats_dict.get("Pass Accuracy") or 
            stats_dict.get("Passes %") or 
            "0%"
        )

    val_str = f"{pct}%" if isinstance(pct, (int, float)) and "%" not in str(pct) else str(pct)
    normalized["pass_accuracy"] = val_str

    return normalized
