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


def normalize_event_stats(stats_dict: dict, sport: str = None, event = None) -> dict:
    """Normalize team match performance statistics into a consistent, sport-aware schema.

    Standardizes metric names according to the specific sport:
    - Baseball: runs, hits, errors, innings line-score.
    - Basketball: points, field_goals, three_pointers, free_throws, rebounds, assists, quarters.
    - Cricket: runs, wickets, overs, extras, run_rate.
    - Soccer: fouls, saves, shots, passes, corners, offsides, cards, possession_percent, expected_goals.

    Args:
        stats_dict (dict): Raw statistics dictionary from database or live sports API.
        sport (str, optional): Sport slug (e.g. 'baseball', 'soccer', 'basketball', 'cricket').
        event (Event, optional): Event instance for extracting rich metadata.

    Returns:
        dict: Cleaned and normalized statistics payload according to sport rules.
    """
    if not stats_dict or not isinstance(stats_dict, dict):
        return {}

    detected_sport = (
        sport or
        stats_dict.get('sport') or
        (getattr(event, 'sport', None) if event else None) or
        ''
    ).lower()

    # -------------------------------------------------------------------------
    # 1. BASEBALL / MLB
    # -------------------------------------------------------------------------
    if detected_sport in ('baseball', 'mlb') or any(k in stats_dict for k in ['hits', 'errors', 'innings']):
        side = stats_dict.get('side', 'home')
        side_meta = {}
        if event and isinstance(getattr(event, 'metadata', None), dict):
            side_meta = event.metadata.get(side, {}) if isinstance(event.metadata.get(side), dict) else {}

        innings = stats_dict.get('innings')
        if not innings and side_meta:
            innings = {}
            for i in range(1, 10):
                k = f'in{i}'
                if k in side_meta and side_meta[k] != '':
                    try:
                        innings[str(i)] = int(side_meta[k])
                    except (ValueError, TypeError):
                        innings[str(i)] = side_meta[k]
            if side_meta.get('extra'):
                innings['extra'] = side_meta['extra']

        runs_val = stats_dict.get('runs') or stats_dict.get('totalscore')
        if runs_val is None and side_meta:
            runs_val = side_meta.get('totalscore')
        if runs_val is None and event:
            runs_val = event.home_score if side == 'home' else event.away_score

        hits_val = stats_dict.get('hits') or side_meta.get('hits') or 0
        errors_val = stats_dict.get('errors') or side_meta.get('errors') or 0

        try:
            runs = int(runs_val if runs_val is not None else 0)
        except (ValueError, TypeError):
            runs = 0
        try:
            hits = int(hits_val)
        except (ValueError, TypeError):
            hits = 0
        try:
            errors = int(errors_val)
        except (ValueError, TypeError):
            errors = 0

        return {
            'side': side,
            'sport': 'baseball',
            'runs': runs,
            'hits': hits,
            'errors': errors,
            'innings': innings or {},
            'is_fallback': bool(stats_dict.get('is_fallback', False)) and not bool(side_meta),
        }

    # -------------------------------------------------------------------------
    # 2. BASKETBALL / NBA
    # -------------------------------------------------------------------------
    if detected_sport in ('basketball', 'nba') or any(k in stats_dict for k in ['field_goals', 'three_pointers', 'rebounds', 'assists', 'quarters']):
        side = stats_dict.get('side', 'home')
        res = {'side': side, 'sport': 'basketball'}
        for k in ['points', 'field_goals', 'three_pointers', 'free_throws', 'rebounds', 'assists', 'steals', 'blocks', 'turnovers', 'fouls', 'quarters']:
            if k in stats_dict:
                res[k] = stats_dict[k]
        if 'is_fallback' in stats_dict:
            res['is_fallback'] = stats_dict['is_fallback']
        return res

    # -------------------------------------------------------------------------
    # 3. CRICKET
    # -------------------------------------------------------------------------
    if detected_sport == 'cricket' or any(k in stats_dict for k in ['wickets', 'overs', 'run_rate']):
        side = stats_dict.get('side', 'home')
        res = {'side': side, 'sport': 'cricket'}
        for k in ['runs', 'wickets', 'overs', 'extras', 'run_rate', 'declared']:
            if k in stats_dict:
                res[k] = stats_dict[k]
        if 'is_fallback' in stats_dict:
            res['is_fallback'] = stats_dict['is_fallback']
        return res

    # -------------------------------------------------------------------------
    # 4. AMERICAN FOOTBALL / NFL
    # -------------------------------------------------------------------------
    if detected_sport in ('american_football', 'football', 'nfl') and not detected_sport.startswith('soc'):
        side = stats_dict.get('side', 'home')
        res = {'side': side, 'sport': 'american_football'}
        for k in ['points', 'touchdowns', 'field_goals', 'passing_yards', 'rushing_yards', 'turnovers', 'quarters']:
            if k in stats_dict:
                res[k] = stats_dict[k]
        if 'is_fallback' in stats_dict:
            res['is_fallback'] = stats_dict['is_fallback']
        return res

    # -------------------------------------------------------------------------
    # 5. HOCKEY / NHL
    # -------------------------------------------------------------------------
    if detected_sport in ('hockey', 'ice_hockey', 'nhl'):
        side = stats_dict.get('side', 'home')
        res = {'side': side, 'sport': 'hockey'}
        for k in ['goals', 'shots', 'saves', 'power_plays', 'penalty_minutes', 'periods']:
            if k in stats_dict:
                res[k] = stats_dict[k]
        if 'is_fallback' in stats_dict:
            res['is_fallback'] = stats_dict['is_fallback']
        return res

    # -------------------------------------------------------------------------
    # 6. SOCCER / FOOTBALL
    # -------------------------------------------------------------------------
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
