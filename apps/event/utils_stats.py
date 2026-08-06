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
    Normalizes team statistics. Preserves existing match stats from API sources
    and appends ONLY the 4 specific requested fields:
      - shot_on_goal
      - shot_off_goal
      - block_shots
      - pass_accuracy
    Returns {} if no real match statistics exist.
    """
    if not stats_dict or not isinstance(stats_dict, dict):
        return {}

    # Check if there is at least one meaningful match stat in stats_dict
    has_real_data = False
    for k, v in stats_dict.items():
        if isinstance(v, dict) and v:
            has_real_data = True
            break
        if v is not None and v != "" and v != 0 and v != "0" and v != "0%":
            has_real_data = True
            break

    if not has_real_data:
        return {}

    normalized = {}

    # Preserve primitive keys and extract StatPal sub-dict values (e.g. {'total': '5'})
    for k, v in stats_dict.items():
        if not isinstance(v, dict):
            normalized[k] = v
        elif isinstance(v, dict):
            if k == "possession_percent":
                val = v.get("total") or v.get("pct")
                if val:
                    normalized["possession_percent"] = str(val)
                    normalized["ball_possession"] = str(val)
            elif k in ["corners", "fouls", "saves", "shots", "passes", "offsides", "redcards", "yellowcards", "expected_goals", "goals_prevented"]:
                if "total" in v and v["total"] != "":
                    normalized[k] = v["total"]
                if k == "shots" and "total" in v and v["total"] != "":
                    normalized["total_shots"] = v["total"]
                elif k == "passes":
                    if "total" in v and v["total"] != "":
                        normalized["total_passes"] = v["total"]
                    if "accurate" in v and v["accurate"] != "":
                        normalized["passes_accurate"] = v["accurate"]

    shots = stats_dict.get("shots") if isinstance(stats_dict.get("shots"), dict) else {}
    passes = stats_dict.get("passes") if isinstance(stats_dict.get("passes"), dict) else {}

    # 1. shot_on_goal
    shot_on_goal = (
        shots.get("ongoal") or 
        shots.get("on_target") or 
        shots.get("on") or 
        stats_dict.get("shot_on_goal") or 
        stats_dict.get("shots_on_goal") or 
        stats_dict.get("Shots on Goal") or 
        stats_dict.get("shots_on_target") or 
        stats_dict.get("Shots on Target") or 
        stats_dict.get("on_target") or 
        0
    )
    normalized["shot_on_goal"] = shot_on_goal

    # 2. shot_off_goal
    shot_off_goal = (
        shots.get("offgoal") or 
        shots.get("off_target") or 
        shots.get("off") or 
        stats_dict.get("shot_off_goal") or 
        stats_dict.get("shots_off_goal") or 
        stats_dict.get("Shots off Goal") or 
        stats_dict.get("shots_off_target") or 
        stats_dict.get("Shots off Target") or 
        stats_dict.get("off_target") or 
        0
    )
    normalized["shot_off_goal"] = shot_off_goal

    # 3. block_shots
    block_shots = (
        shots.get("blocked") or 
        shots.get("block") or 
        stats_dict.get("block_shots") or 
        stats_dict.get("blocked_shots") or 
        stats_dict.get("shots_blocked") or 
        stats_dict.get("Blocked Shots") or 
        stats_dict.get("Blocked shots") or 
        0
    )
    normalized["block_shots"] = block_shots

    # 4. pass_accuracy
    pct = None
    if passes:
        pct = passes.get("percentage") or passes.get("percent") or passes.get("pct")
        if pct is None and passes.get("accurate") is not None and passes.get("total"):
            try:
                acc = float(passes["accurate"])
                tot = float(passes["total"])
                if tot > 0:
                    pct = f"{int(round((acc / tot) * 100))}%"
            except (ValueError, TypeError, ZeroDivisionError):
                pass

    if pct is None:
        acc_p = stats_dict.get("accurate_passes") or stats_dict.get("passes_accurate") or stats_dict.get("accurate")
        tot_p = stats_dict.get("total_passes") or stats_dict.get("passes") or stats_dict.get("Total passes")
        if acc_p is not None and tot_p:
            try:
                acc_f = float(acc_p)
                tot_f = float(tot_p)
                if tot_f > 0:
                    pct = f"{int(round((acc_f / tot_f) * 100))}%"
            except (ValueError, TypeError, ZeroDivisionError):
                pass

    if pct is None:
        pct = (
            stats_dict.get("pass_accuracy") or 
            stats_dict.get("pass_accuracy_percent") or 
            stats_dict.get("passes_percentage") or 
            stats_dict.get("passes_%") or 
            stats_dict.get("Pass Accuracy") or 
            stats_dict.get("Passes %") or 
            "0%"
        )

    val_str = f"{pct}%" if isinstance(pct, (int, float)) and "%" not in str(pct) else str(pct)
    normalized["pass_accuracy"] = val_str

    return normalized
