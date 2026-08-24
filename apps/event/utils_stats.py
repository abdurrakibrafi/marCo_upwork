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
    """Normalize team match performance statistics into a consistent, standard schema.

    Standardizes metric names across fouls, saves, shots, passes, corners, offsides,
    redcards, yellowcards, expected_goals, possession_percent, shot_on_goal,
    shot_off_goal, block_shots, and pass_accuracy.

    Args:
        stats_dict (dict): Raw statistics dictionary from database or live sports API.

    Returns:
        dict: Cleaned and normalized statistics payload with guaranteed default values.
    """
    if not stats_dict or not isinstance(stats_dict, dict):
        return {}

    # Ignore basic score & side metadata when checking for team performance stats
    SCORE_AND_SIDE_KEYS = {'side', 'et_away', 'et_home', 'ft_away', 'ft_home', 'ht_away', 'ht_home', 'score', 'runs'}
    has_real_data = False
    if stats_dict.get('is_fallback') or any(k in stats_dict for k in ['goals', 'yellowcards', 'redcards', 'substitutions']):
        has_real_data = True
    else:
        for k, v in stats_dict.items():
            if k in SCORE_AND_SIDE_KEYS:
                continue
            if isinstance(v, dict) and v:
                has_real_data = True
                break
            if v is not None and v != "" and v != 0 and v != "0" and v != "0%":
                has_real_data = True
                break

    if not has_real_data:
        return {}

    is_fallback = bool(stats_dict.get('is_fallback', False))
    normalized = {
        'side': stats_dict.get('side', 'home'),
        'is_fallback': is_fallback,
    }

    if 'goals' in stats_dict:
        normalized['goals'] = str(stats_dict['goals'])
    if 'substitutions' in stats_dict or 'substitution' in stats_dict:
        normalized['substitution'] = str(stats_dict.get('substitution') or stats_dict.get('substitutions'))
    if 'formation' in stats_dict and stats_dict['formation']:
        normalized['formation'] = str(stats_dict['formation'])
    if 'penalties' in stats_dict and stats_dict['penalties'] is not None and stats_dict['penalties'] != "":
        normalized['penalties'] = str(stats_dict['penalties'])
    if 'ft_home' in stats_dict:
        normalized['ft_home'] = str(stats_dict['ft_home'])
    if 'ft_away' in stats_dict:
        normalized['ft_away'] = str(stats_dict['ft_away'])

    def _extract_metric(key_name):
        v = stats_dict.get(key_name)
        if isinstance(v, dict):
            val = v.get("total") if "total" in v else (v.get("pct") if "pct" in v else None)
            if val is not None and val != "":
                return str(val) if isinstance(val, (str, bool)) else val
            return None
        if v is not None and v != "":
            return str(v) if isinstance(v, (str, bool)) else v
        return None

    normalized["fouls"] = _extract_metric("fouls")
    normalized["saves"] = _extract_metric("saves")
    normalized["shots"] = _extract_metric("shots")
    normalized["passes"] = _extract_metric("passes")
    normalized["corners"] = _extract_metric("corners")
    normalized["offsides"] = _extract_metric("offsides")
    normalized["redcards"] = _extract_metric("redcards")
    normalized["yellowcards"] = _extract_metric("yellowcards")
    normalized["expected_goals"] = _extract_metric("expected_goals")
    normalized["goals_prevented"] = _extract_metric("goals_prevented")

    # possession_percent
    pos_v = stats_dict.get("possession_percent") or stats_dict.get("ball_possession")
    if isinstance(pos_v, dict):
        val = pos_v.get("total") if "total" in pos_v else (pos_v.get("pct") if "pct" in pos_v else None)
        normalized["possession_percent"] = str(val) if val is not None and val != "" else None
    elif pos_v is not None and pos_v != "":
        normalized["possession_percent"] = str(pos_v)
    else:
        normalized["possession_percent"] = None

    shots = stats_dict.get("shots") if isinstance(stats_dict.get("shots"), dict) else {}
    passes = stats_dict.get("passes") if isinstance(stats_dict.get("passes"), dict) else {}

    # shot_on_goal
    s_on = (
        shots.get("ongoal") if shots.get("ongoal") is not None else (
        shots.get("on_target") if shots.get("on_target") is not None else (
        shots.get("on") if shots.get("on") is not None else (
        stats_dict.get("shot_on_goal") if stats_dict.get("shot_on_goal") is not None else (
        stats_dict.get("shots_on_goal") if stats_dict.get("shots_on_goal") is not None else (
        stats_dict.get("Shots on Goal") if stats_dict.get("Shots on Goal") is not None else (
        stats_dict.get("shots_on_target") if stats_dict.get("shots_on_target") is not None else (
        stats_dict.get("Shots on Target") if stats_dict.get("Shots on Target") is not None else (
        stats_dict.get("on_target")
        ))))))))
    )
    normalized["shot_on_goal"] = s_on if s_on is not None and s_on != "" else None

    # shot_off_goal
    s_off = (
        shots.get("offgoal") if shots.get("offgoal") is not None else (
        shots.get("off_target") if shots.get("off_target") is not None else (
        shots.get("off") if shots.get("off") is not None else (
        stats_dict.get("shot_off_goal") if stats_dict.get("shot_off_goal") is not None else (
        stats_dict.get("shots_off_goal") if stats_dict.get("shots_off_goal") is not None else (
        stats_dict.get("Shots off Goal") if stats_dict.get("Shots off Goal") is not None else (
        stats_dict.get("shots_off_target") if stats_dict.get("shots_off_target") is not None else (
        stats_dict.get("Shots off Target") if stats_dict.get("Shots off Target") is not None else (
        stats_dict.get("off_target")
        ))))))))
    )
    normalized["shot_off_goal"] = s_off if s_off is not None and s_off != "" else None

    # block_shots
    s_blk = (
        shots.get("blocked") if shots.get("blocked") is not None else (
        shots.get("block") if shots.get("block") is not None else (
        stats_dict.get("block_shots") if stats_dict.get("block_shots") is not None else (
        stats_dict.get("blocked_shots") if stats_dict.get("blocked_shots") is not None else (
        stats_dict.get("shots_blocked") if stats_dict.get("shots_blocked") is not None else (
        stats_dict.get("Blocked Shots") if stats_dict.get("Blocked Shots") is not None else (
        stats_dict.get("Blocked shots")
        ))))))
    )
    normalized["block_shots"] = s_blk if s_blk is not None and s_blk != "" else None

    # pass_accuracy
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
        pct = (
            stats_dict.get("pass_accuracy") or
            stats_dict.get("pass_accuracy_percent") or
            stats_dict.get("passes_percentage") or
            stats_dict.get("passes_%") or
            stats_dict.get("Pass Accuracy") or
            stats_dict.get("Passes %")
        )

    if pct is not None and pct != "":
        val_str = f"{pct}%" if isinstance(pct, (int, float)) and "%" not in str(pct) else str(pct)
        normalized["pass_accuracy"] = val_str
    else:
        normalized["pass_accuracy"] = None

    DEFAULT_KEYS = {
        'fouls': '0',
        'saves': '0',
        'shots': '0',
        'passes': '0',
        'corners': '0',
        'offsides': '0',
        'redcards': '0',
        'yellowcards': '0',
        'expected_goals': '0',
        'goals_prevented': '0',
        'possession_percent': '0%',
        'shot_on_goal': '0',
        'shot_off_goal': '0',
        'block_shots': '0',
        'pass_accuracy': '0%',
        'substitution': '0',
        'penalties': '0',
    }
    for k, default_val in DEFAULT_KEYS.items():
        if k not in normalized or normalized[k] is None:
            normalized[k] = default_val

    return normalized
