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
    Normalizes team statistics into a clean, compact, snake_case dictionary
    without duplicate alias keys.
    """
    if not stats_dict or not isinstance(stats_dict, dict):
        stats_dict = {}

    def _get_first(keys, default=0):
        for k in keys:
            if k in stats_dict and stats_dict[k] is not None and stats_dict[k] != "":
                return stats_dict[k]
        return default

    shots = stats_dict.get("shots") if isinstance(stats_dict.get("shots"), dict) else {}
    passes = stats_dict.get("passes") if isinstance(stats_dict.get("passes"), dict) else {}

    normalized = {}

    # 1. Basic match metadata
    if "side" in stats_dict:
        normalized["side"] = stats_dict["side"]
    for score_key in ["ft_home", "ft_away", "ht_home", "ht_away"]:
        if score_key in stats_dict:
            normalized[score_key] = stats_dict[score_key]

    # 2. Key match metrics with clean snake_case names
    pos = _get_first(["possession", "ball_possession", "possession_percent", "Ball Possession"], "0%")
    pos_str = f"{pos}%" if isinstance(pos, (int, float)) or (isinstance(pos, str) and "%" not in pos) else str(pos)
    normalized["possession"] = pos_str

    normalized["total_shots"] = _get_first(["total_shots", "Total Shots", "shots"], 0)

    # Shot breakdowns
    shot_on_goal = (
        shots.get("ongoal") or 
        _get_first(["shot_on_goal", "shots_on_goal", "Shots on Goal", "shots_on_target"], 0)
    )
    normalized["shot_on_goal"] = shot_on_goal

    shot_off_goal = (
        shots.get("offgoal") or 
        _get_first(["shot_off_goal", "shots_off_goal", "Shots off Goal"], 0)
    )
    normalized["shot_off_goal"] = shot_off_goal

    block_shots = (
        shots.get("blocked") or 
        _get_first(["block_shots", "blocked_shots", "shots_blocked", "Blocked Shots"], 0)
    )
    normalized["block_shots"] = block_shots

    # Pass metrics
    normalized["total_passes"] = _get_first(["total_passes", "Total passes", "passes"], 0)

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
        pct = _get_first(["pass_accuracy", "pass_accuracy_percent", "passes_percentage", "Pass Accuracy", "Passes %"], "0%")

    val_str = f"{pct}%" if isinstance(pct, (int, float)) or (isinstance(pct, str) and "%" not in pct) else str(pct)
    normalized["pass_accuracy"] = val_str

    # Discipline & match events
    normalized["fouls"] = _get_first(["fouls", "Fouls"], 0)
    normalized["corners"] = _get_first(["corners", "corner_kicks", "Corner Kicks"], 0)
    normalized["offsides"] = _get_first(["offsides", "Offsides"], 0)
    normalized["yellow_cards"] = _get_first(["yellow_cards", "yellowcards", "Yellow Cards"], 0)
    normalized["red_cards"] = _get_first(["red_cards", "redcards", "Red Cards"], 0)
    normalized["goalkeeper_saves"] = _get_first(["goalkeeper_saves", "saves", "Goalkeeper Saves"], 0)

    # Advanced metrics if present
    if "expected_goals" in stats_dict:
        normalized["expected_goals"] = stats_dict["expected_goals"]
    if "goals_prevented" in stats_dict:
        normalized["goals_prevented"] = stats_dict["goals_prevented"]

    return normalized
