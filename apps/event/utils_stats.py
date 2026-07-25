"""
apps/event/utils_stats.py

Normalization utility for team statistics to ensure all standard metrics
(shot on goal, shot off goal, block shots, pass accuracy, possession, etc.)
are consistently present across all sports event API responses.
"""

import logging

logger = logging.getLogger(__name__)


def normalize_event_stats(stats_dict: dict) -> dict:
    """
    Normalizes team statistics from StatPal or API-Sports raw formats into
    a comprehensive dictionary containing all standard fields and aliases:
      - shot_on_goal / shots_on_goal / shots_on_target / "Shots on Goal"
      - shot_off_goal / shots_off_goal / "Shots off Goal"
      - block_shots / blocked_shots / "Blocked Shots"
      - pass_accuracy / passes_percentage / "Pass Accuracy" / "Passes %"
    """
    if not stats_dict or not isinstance(stats_dict, dict):
        return {}

    normalized = {}

    def set_stat(key_aliases, value):
        if value is None:
            return
        for alias in key_aliases:
            normalized[alias] = value

    # 1. Process nested StatPal dicts if present (e.g. shots, passes, etc.)
    shots = stats_dict.get("shots")
    if isinstance(shots, dict):
        set_stat(["total_shots", "shots", "Total Shots"], shots.get("total"))
        set_stat(["shots_on_goal", "shot_on_goal", "shots_on_target", "Shots on Goal"], shots.get("ongoal"))
        set_stat(["shots_off_goal", "shot_off_goal", "Shots off Goal"], shots.get("offgoal"))
        set_stat(["blocked_shots", "block_shots", "shots_blocked", "Blocked Shots"], shots.get("blocked"))
        set_stat(["shots_insidebox", "Shots insidebox"], shots.get("insidebox"))
        set_stat(["shots_outsidebox", "Shots outsidebox"], shots.get("outsidebox"))
    elif isinstance(shots, (int, float, str)):
        set_stat(["total_shots", "shots", "Total Shots"], shots)

    passes = stats_dict.get("passes")
    if isinstance(passes, dict):
        set_stat(["total_passes", "passes", "Total passes"], passes.get("total"))
        set_stat(["passes_accurate", "accurate_passes", "Passes accurate"], passes.get("accurate"))
        
        pct = passes.get("percentage") or passes.get("percent")
        if pct is None and passes.get("accurate") is not None and passes.get("total"):
            try:
                acc = float(passes["accurate"])
                tot = float(passes["total"])
                if tot > 0:
                    pct = f"{int(round((acc / tot) * 100))}%"
            except (ValueError, TypeError, ZeroDivisionError):
                pass
        set_stat(["pass_accuracy", "pass_accuracy_percent", "passes_percentage", "Pass Accuracy", "Passes %"], pct)
    elif isinstance(passes, (int, float, str)):
        set_stat(["total_passes", "passes", "Total passes"], passes)

    possession = (
        stats_dict.get("possession_percent") or 
        stats_dict.get("possession") or 
        stats_dict.get("ball_possession")
    )
    if isinstance(possession, dict):
        val = possession.get("total") or possession.get("value")
        if val is not None:
            val_str = f"{val}%" if isinstance(val, (int, float)) and "%" not in str(val) else str(val)
            set_stat(["possession", "ball_possession", "possession_percent", "Ball Possession"], val_str)
    elif possession is not None:
        val_str = f"{possession}%" if isinstance(possession, (int, float)) and "%" not in str(possession) else str(possession)
        set_stat(["possession", "ball_possession", "possession_percent", "Ball Possession"], val_str)

    fouls = stats_dict.get("fouls")
    if isinstance(fouls, dict):
        set_stat(["fouls", "Fouls"], fouls.get("total") or fouls.get("value"))
    elif fouls is not None:
        set_stat(["fouls", "Fouls"], fouls)

    corners = stats_dict.get("corners") or stats_dict.get("corner_kicks")
    if isinstance(corners, dict):
        set_stat(["corners", "corner_kicks", "Corner Kicks"], corners.get("total") or corners.get("value"))
    elif corners is not None:
        set_stat(["corners", "corner_kicks", "Corner Kicks"], corners)

    offsides = stats_dict.get("offsides")
    if isinstance(offsides, dict):
        set_stat(["offsides", "Offsides"], offsides.get("total") or offsides.get("value"))
    elif offsides is not None:
        set_stat(["offsides", "Offsides"], offsides)

    yellowcards = stats_dict.get("yellowcards") or stats_dict.get("yellow_cards")
    if isinstance(yellowcards, dict):
        set_stat(["yellow_cards", "yellowcards", "Yellow Cards"], yellowcards.get("total") or yellowcards.get("value"))
    elif yellowcards is not None:
        set_stat(["yellow_cards", "yellowcards", "Yellow Cards"], yellowcards)

    redcards = stats_dict.get("redcards") or stats_dict.get("red_cards")
    if isinstance(redcards, dict):
        set_stat(["red_cards", "redcards", "Red Cards"], redcards.get("total") or redcards.get("value"))
    elif redcards is not None:
        set_stat(["red_cards", "redcards", "Red Cards"], redcards)

    saves = stats_dict.get("saves") or stats_dict.get("goalkeeper_saves")
    if isinstance(saves, dict):
        set_stat(["saves", "goalkeeper_saves", "Goalkeeper Saves"], saves.get("total") or saves.get("value"))
    elif saves is not None:
        set_stat(["saves", "goalkeeper_saves", "Goalkeeper Saves"], saves)

    # 2. Process flat key-value pairs (API-Sports or pre-flattened dicts)
    for k, v in stats_dict.items():
        if isinstance(v, dict):
            continue
        k_clean = str(k).lower().strip().replace(" ", "_")
        normalized[k] = v
        normalized[k_clean] = v

        if k_clean in ("shots_on_goal", "shots_on_target", "shots_ongoal", "shot_on_goal"):
            set_stat(["shots_on_goal", "shot_on_goal", "shots_on_target", "Shots on Goal"], v)
        elif k_clean in ("shots_off_goal", "shots_offgoal", "shot_off_goal"):
            set_stat(["shots_off_goal", "shot_off_goal", "Shots off Goal"], v)
        elif k_clean in ("blocked_shots", "shots_blocked", "block_shots"):
            set_stat(["blocked_shots", "block_shots", "shots_blocked", "Blocked Shots"], v)
        elif k_clean in ("passes_%", "pass_accuracy", "passes_accuracy", "passes_percentage"):
            val_str = f"{v}%" if isinstance(v, (int, float)) and "%" not in str(v) else str(v)
            set_stat(["pass_accuracy", "pass_accuracy_percent", "passes_percentage", "Pass Accuracy", "Passes %"], val_str)
        elif k_clean in ("total_shots", "shots"):
            set_stat(["total_shots", "shots", "Total Shots"], v)
        elif k_clean in ("total_passes", "passes"):
            set_stat(["total_passes", "passes", "Total passes"], v)
        elif k_clean in ("passes_accurate", "accurate_passes"):
            set_stat(["passes_accurate", "accurate_passes", "Passes accurate"], v)

    # 3. Ensure required metric aliases are guaranteed to exist
    default_metrics = [
        (["shots_on_goal", "shot_on_goal", "shots_on_target", "Shots on Goal"], 0),
        (["shots_off_goal", "shot_off_goal", "Shots off Goal"], 0),
        (["blocked_shots", "block_shots", "shots_blocked", "Blocked Shots"], 0),
        (["pass_accuracy", "pass_accuracy_percent", "passes_percentage", "Pass Accuracy", "Passes %"], "0%"),
        (["possession", "ball_possession", "possession_percent", "Ball Possession"], "50%"),
        (["total_shots", "shots", "Total Shots"], 0),
        (["total_passes", "passes", "Total passes"], 0),
        (["fouls", "Fouls"], 0),
        (["corner_kicks", "corners", "Corner Kicks"], 0),
    ]

    for aliases, default_val in default_metrics:
        if not any(alias in normalized for alias in aliases):
            for alias in aliases:
                normalized[alias] = default_val

    return normalized
