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

    def _get_val(keys, default=0):
        for k in keys:
            if k in stats_dict and stats_dict[k] is not None and stats_dict[k] != "":
                val = stats_dict[k]
                try:
                    if isinstance(val, str) and val.strip().replace('.', '', 1).isdigit():
                        return float(val) if '.' in val else int(val)
                except Exception:
                    pass
                return val
        return default

    shots_nested = stats_dict.get("shots") if isinstance(stats_dict.get("shots"), dict) else {}
    passes_nested = stats_dict.get("passes") if isinstance(stats_dict.get("passes"), dict) else {}

    normalized = {}

    # 1. Basic match metadata
    if "side" in stats_dict:
        normalized["side"] = stats_dict["side"]
    for score_key in ["ft_home", "ft_away", "ht_home", "ht_away"]:
        if score_key in stats_dict:
            normalized[score_key] = stats_dict[score_key]

    # 2. Key match metrics with clean snake_case names
    pos = _get_val(["possession", "ball_possession", "possession_percent", "Ball Possession"], "0%")
    pos_str = f"{pos}%" if isinstance(pos, (int, float)) or (isinstance(pos, str) and "%" not in pos) else str(pos)
    normalized["possession"] = pos_str

    normalized["total_shots"] = _get_val(["total_shots", "Total Shots", "shots_total"], 0)
    if normalized["total_shots"] == 0 and not isinstance(stats_dict.get("shots"), dict):
        normalized["total_shots"] = _get_val(["shots"], 0)

    # 1. Shot on Goal
    shot_on_goal = (
        shots_nested.get("ongoal") or 
        shots_nested.get("on_target") or 
        shots_nested.get("on") or 
        _get_val([
            "shot_on_goal", "shots_on_goal", "Shots on Goal", 
            "shots_on_target", "Shots on Target", "on_target", 
            "shots_ongoal", "shots_target"
        ], 0)
    )
    normalized["shot_on_goal"] = shot_on_goal

    # 2. Shot off Goal
    shot_off_goal = (
        shots_nested.get("offgoal") or 
        shots_nested.get("off_target") or 
        shots_nested.get("off") or 
        _get_val([
            "shot_off_goal", "shots_off_goal", "Shots off Goal", 
            "shots_off_target", "Shots off Target", "off_target", 
            "shots_offgoal"
        ], 0)
    )
    normalized["shot_off_goal"] = shot_off_goal

    # 3. Block Shots
    block_shots = (
        shots_nested.get("blocked") or 
        shots_nested.get("block") or 
        _get_val([
            "block_shots", "blocked_shots", "shots_blocked", 
            "Blocked Shots", "Blocked shots", "blocked"
        ], 0)
    )
    normalized["block_shots"] = block_shots

    # 4. Total Passes & Pass Accuracy
    normalized["total_passes"] = _get_val(["total_passes", "Total passes", "passes_total"], 0)
    if normalized["total_passes"] == 0 and not isinstance(stats_dict.get("passes"), dict):
        normalized["total_passes"] = _get_val(["passes"], 0)

    pct = None
    if passes_nested:
        pct = passes_nested.get("percentage") or passes_nested.get("percent") or passes_nested.get("pct")
        if pct is None and passes_nested.get("accurate") is not None and passes_nested.get("total"):
            try:
                acc = float(passes_nested["accurate"])
                tot = float(passes_nested["total"])
                if tot > 0:
                    pct = f"{int(round((acc / tot) * 100))}%"
            except (ValueError, TypeError, ZeroDivisionError):
                pass

    if pct is None:
        acc_p = _get_val(["accurate_passes", "passes_accurate", "accurate"], None)
        tot_p = normalized["total_passes"]
        if acc_p is not None and tot_p and float(tot_p) > 0:
            try:
                pct = f"{int(round((float(acc_p) / float(tot_p)) * 100))}%"
            except (ValueError, TypeError, ZeroDivisionError):
                pass

    if pct is None:
        pct = _get_val([
            "pass_accuracy", "pass_accuracy_percent", "passes_percentage", 
            "Pass Accuracy", "Passes %", "pass_pct", "accuracy"
        ], "0%")

    val_str = f"{pct}%" if isinstance(pct, (int, float)) or (isinstance(pct, str) and "%" not in pct) else str(pct)
    normalized["pass_accuracy"] = val_str

    # Discipline & match events
    normalized["fouls"] = _get_val(["fouls", "Fouls"], 0)
    normalized["corners"] = _get_val(["corners", "corner_kicks", "Corner Kicks"], 0)
    normalized["offsides"] = _get_val(["offsides", "Offsides"], 0)
    normalized["yellow_cards"] = _get_val(["yellow_cards", "yellowcards", "Yellow Cards"], 0)
    normalized["red_cards"] = _get_val(["red_cards", "redcards", "Red Cards"], 0)
    normalized["goalkeeper_saves"] = _get_val(["goalkeeper_saves", "saves", "Goalkeeper Saves"], 0)

    # Advanced metrics if present
    if "expected_goals" in stats_dict:
        normalized["expected_goals"] = stats_dict["expected_goals"]
    if "goals_prevented" in stats_dict:
        normalized["goals_prevented"] = stats_dict["goals_prevented"]

    return normalized
