import re
from apps.event.tasks import _extract_minute


def _normalize_list(val):
    """Ensure raw scalar, dictionary, or list API inputs are normalized into a Python list.

    Args:
        val: Input data of arbitrary type.

    Returns:
        list: Normalized list container.
    """
    if not val:
        return []
    if isinstance(val, dict):
        return [val]
    if isinstance(val, list):
        return val
    return []


def _clean_cricket_league_name(league_name: str) -> str:
    """Remove erroneous persisted ODI suffix from a cricket competition tour name.

    Args:
        league_name (str): Raw league/series title.

    Returns:
        str: Cleaned series name.
    """
    if not isinstance(league_name, str):
        return league_name
    return re.sub(r'\s*-\s*ODI\s*$', '', league_name, flags=re.IGNORECASE)


def _convert_statpal_stats_to_api_sports(team_stats, home_team_name, home_team_id, away_team_name, away_team_id):
    """Convert soccer match statistics from StatPal format into API-Sports standardized schema.

    Args:
        team_stats (dict): Raw team statistics dictionary.
        home_team_name (str): Home club name.
        home_team_id: Home team identifier.
        away_team_name (str): Away club name.
        away_team_id: Away team identifier.

    Returns:
        list: List of team metric dictionary items.
    """
    if not team_stats or not isinstance(team_stats, dict):
        return []

    metrics = [
        ("Shots on Goal", lambda s: s.get("shots", {}).get("ongoal")),
        ("Shots off Goal", lambda s: s.get("shots", {}).get("offgoal")),
        ("Total Shots", lambda s: s.get("shots", {}).get("total")),
        ("Blocked Shots", lambda s: s.get("shots", {}).get("blocked")),
        ("Shots insidebox", lambda s: s.get("shots", {}).get("insidebox")),
        ("Shots outsidebox", lambda s: s.get("shots", {}).get("outsidebox")),
        ("Fouls", lambda s: s.get("fouls", {}).get("total")),
        ("Corner Kicks", lambda s: s.get("corners", {}).get("total")),
        ("Offsides", lambda s: s.get("offsides", {}).get("total")),
        ("Ball Possession", lambda s: s.get("possession_percent", {}).get("total")),
        ("Yellow Cards", lambda s: s.get("yellowcards", {}).get("total")),
        ("Red Cards", lambda s: s.get("redcards", {}).get("total")),
        ("Goalkeeper Saves", lambda s: s.get("saves", {}).get("total")),
        ("Total passes", lambda s: s.get("passes", {}).get("total")),
        ("Passes accurate", lambda s: s.get("passes", {}).get("accurate")),
    ]

    home_stats = []
    away_stats = []

    home_data = team_stats.get("home", {})
    away_data = team_stats.get("away", {})

    for metric_name, extractor in metrics:
        try:
            home_val = extractor(home_data)
            if home_val is not None:
                home_stats.append({"type": metric_name, "value": home_val})
        except Exception:
            pass

        try:
            away_val = extractor(away_data)
            if away_val is not None:
                away_stats.append({"type": metric_name, "value": away_val})
        except Exception:
            pass

    return [
        {
            "team": {"id": home_team_id, "name": home_team_name},
            "statistics": home_stats
        },
        {
            "team": {"id": away_team_id, "name": away_team_name},
            "statistics": away_stats
        }
    ]


def _convert_tennis_stats(match_data, home_player_name, home_team_id, away_player_name, away_team_id):
    """Extract and normalize tennis serve, return, and point statistics for competing players.

    Args:
        match_data (dict): Raw tennis match metadata dictionary.
        home_player_name (str): Player 1 name.
        home_team_id: Player 1 identifier.
        away_player_name (str): Player 2 name.
        away_team_id: Player 2 identifier.

    Returns:
        list: Formatted player statistics structure.
    """
    players = match_data.get('player', [])
    if not isinstance(players, list) or len(players) < 2:
        return []

    p1, p2 = players[0], players[1]

    p1_stats = {}
    p2_stats = {}

    p1_periods = p1.get('stats', {}).get('period', [])
    if isinstance(p1_periods, dict):
        p1_periods = [p1_periods]
    elif not isinstance(p1_periods, list):
        p1_periods = []

    for p in p1_periods:
        if isinstance(p, dict) and p.get('name') == 'match':
            types = p.get('type', [])
            if isinstance(types, dict):
                types = [types]
            for t in types:
                if not isinstance(t, dict):
                    continue
                stats_list = t.get('stat', [])
                if isinstance(stats_list, dict):
                    stats_list = [stats_list]
                for s in stats_list:
                    if isinstance(s, dict) and s.get('name'):
                        p1_stats[s.get('name')] = s.get('value')

    p2_periods = p2.get('stats', {}).get('period', [])
    if isinstance(p2_periods, dict):
        p2_periods = [p2_periods]
    elif not isinstance(p2_periods, list):
        p2_periods = []

    for p in p2_periods:
        if isinstance(p, dict) and p.get('name') == 'match':
            types = p.get('type', [])
            if isinstance(types, dict):
                types = [types]
            for t in types:
                if not isinstance(t, dict):
                    continue
                stats_list = t.get('stat', [])
                if isinstance(stats_list, dict):
                    stats_list = [stats_list]
                for s in stats_list:
                    if isinstance(s, dict) and s.get('name'):
                        p2_stats[s.get('name')] = s.get('value')

    metrics = [
        "Aces",
        "Double Faults",
        "1st serve percentage",
        "1st serve points won",
        "2nd serve points won",
        "Break Points Saved",
        "Break Points Converted",
        "Service Points Won",
        "Return Points Won",
        "Total Points Won",
    ]

    home_stats = []
    away_stats = []

    for metric in metrics:
        v1 = p1_stats.get(metric)
        if v1 is not None:
            home_stats.append({"type": metric, "value": v1})
        v2 = p2_stats.get(metric)
        if v2 is not None:
            away_stats.append({"type": metric, "value": v2})

    return [
        {
            "team": {"id": home_team_id, "name": p1.get('name') or home_player_name},
            "statistics": home_stats
        },
        {
            "team": {"id": away_team_id, "name": p2.get('name') or away_player_name},
            "statistics": away_stats
        }
    ]


def _convert_generic_team_stats(team_stats, home_team_name, home_team_id, away_team_name, away_team_id):
    """Recursively traverse key-value metric maps for generic sports (basketball, baseball, hockey, etc.).

    Args:
        team_stats (dict): Raw team statistics payload.
        home_team_name (str): Home team name.
        home_team_id: Home team identifier.
        away_team_name (str): Away team name.
        away_team_id: Away team identifier.

    Returns:
        list: Formatted team metric lists.
    """
    if not team_stats or not isinstance(team_stats, dict):
        return []

    home_data = team_stats.get("home", {}) or {}
    away_data = team_stats.get("away", {}) or {}

    def extract_metrics(data, prefix=""):
        res = []
        if isinstance(data, dict):
            for k, v in data.items():
                label = f"{prefix} {k}".strip().replace("_", " ").title()
                if isinstance(v, (str, int, float)) and str(v).strip():
                    res.append({"type": label, "value": str(v)})
                elif isinstance(v, dict):
                    res.extend(extract_metrics(v, label))
        return res

    home_stats = extract_metrics(home_data)
    away_stats = extract_metrics(away_data)

    if not home_stats and not away_stats:
        return []

    return [
        {
            "team": {"id": home_team_id, "name": home_team_name},
            "statistics": home_stats
        },
        {
            "team": {"id": away_team_id, "name": away_team_name},
            "statistics": away_stats
        }
    ]


def _convert_statpal_events_to_api_sports(match_data, home_team_name, home_team_id, away_team_name, away_team_id):
    """Normalize in-game timeline events (goals, cards, substitutions, VAR reviews) into API-Sports event format.

    Args:
        match_data (dict): Raw match event summary dictionary.
        home_team_name (str): Home team name.
        home_team_id: Home team identifier.
        away_team_name (str): Away team name.
        away_team_id: Away team identifier.

    Returns:
        list: Chronologically sorted list of match events.
    """
    events = []
    summary = match_data.get("event_summary", {})
    subs = match_data.get("substitutions", {})

    has_summary_or_subs = (isinstance(summary, dict) and summary) or (isinstance(subs, dict) and subs)

    if has_summary_or_subs:
        if isinstance(summary, dict):
            for side in ["home", "away"]:
                side_team_name = home_team_name if side == "home" else away_team_name
                side_team_id = home_team_id if side == "home" else away_team_id

                side_events = summary.get(side, {})
                if not isinstance(side_events, dict):
                    continue

                # Goals
                goals_list = _normalize_list(side_events.get("goals", {}).get("event", []))
                for g in goals_list:
                    detail = "Normal Goal"
                    if g.get("penalty") == "True":
                        detail = "Penalty"
                    elif g.get("own_goal") == "True":
                        detail = "Own Goal"
                    events.append({
                        "time": {
                            "elapsed": _extract_minute(g.get("minute")),
                            "extra": _extract_minute(g.get("extra_min")) or None
                        },
                        "team": {"id": side_team_id, "name": side_team_name},
                        "player": {"id": g.get("player_id"), "name": g.get("player_name")},
                        "assist": {"id": g.get("assist_player_id"), "name": g.get("assist_player_name")} if g.get("assist_player_id") else {"id": None, "name": None},
                        "type": "Goal",
                        "detail": detail,
                        "comments": None
                    })

                # Yellow Cards
                yc_list = _normalize_list(side_events.get("yellowcards", {}).get("event", []))
                for yc in yc_list:
                    events.append({
                        "time": {
                            "elapsed": _extract_minute(yc.get("minute")),
                            "extra": _extract_minute(yc.get("extra_min")) or None
                        },
                        "team": {"id": side_team_id, "name": side_team_name},
                        "player": {"id": yc.get("player_id"), "name": yc.get("player_name")},
                        "assist": {"id": None, "name": None},
                        "type": "Card",
                        "detail": "Yellow Card",
                        "comments": yc.get("comment") or None
                    })

                # Red Cards
                rc_list = _normalize_list(side_events.get("redcards", {}).get("event", []))
                for rc in rc_list:
                    events.append({
                        "time": {
                            "elapsed": _extract_minute(rc.get("minute")),
                            "extra": _extract_minute(rc.get("extra_min")) or None
                        },
                        "team": {"id": side_team_id, "name": side_team_name},
                        "player": {"id": rc.get("player_id"), "name": rc.get("player_name")},
                        "assist": {"id": None, "name": None},
                        "type": "Card",
                        "detail": "Red Card",
                        "comments": rc.get("comment") or None
                    })

                # VAR
                var_list = _normalize_list(side_events.get("var", {}).get("event", []))
                for var in var_list:
                    events.append({
                        "time": {
                            "elapsed": _extract_minute(var.get("minute")),
                            "extra": _extract_minute(var.get("extra_min")) or None
                        },
                        "team": {"id": side_team_id, "name": side_team_name},
                        "player": {"id": var.get("player_id"), "name": var.get("player_name")},
                        "assist": {"id": None, "name": None},
                        "type": "Var",
                        "detail": var.get("event_type") or "VAR Decision",
                        "comments": var.get("ref_decision") or None
                    })

        # Substitutions
        if isinstance(subs, dict):
            for side in ["home", "away"]:
                side_team_name = home_team_name if side == "home" else away_team_name
                side_team_id = home_team_id if side == "home" else away_team_id

                sub_list = _normalize_list(subs.get(side, {}).get("substitution", []))
                for s in sub_list:
                    events.append({
                        "time": {
                            "elapsed": _extract_minute(s.get("minute")),
                            "extra": _extract_minute(s.get("extra_min")) or None
                        },
                        "team": {"id": side_team_id, "name": side_team_name},
                        "player": {"id": s.get("player_off_id"), "name": s.get("player_off")},
                        "assist": {"id": s.get("player_on_id"), "name": s.get("player_on")},
                        "type": "subst",
                        "detail": "Substitution",
                        "comments": None
                    })
    else:
        # Fallback: parse generic events list format
        raw_events = match_data.get("events", {})
        if isinstance(raw_events, dict):
            event_list = raw_events.get("event", [])
            if isinstance(event_list, dict):
                event_list = [event_list]
            elif not isinstance(event_list, list):
                event_list = []

            for ev in event_list:
                if not isinstance(ev, dict):
                    continue

                side = ev.get("team")
                side_team_name = home_team_name if side == "home" else away_team_name
                side_team_id = home_team_id if side == "home" else away_team_id

                ev_type = ev.get("type", "").lower()
                api_type = "Goal"
                detail = "Normal Goal"
                if "goal" in ev_type:
                    api_type = "Goal"
                    detail = "Normal Goal"
                elif "card" in ev_type or "yellow" in ev_type or "red" in ev_type:
                    api_type = "Card"
                    detail = "Red Card" if "red" in ev_type else "Yellow Card"
                elif "sub" in ev_type:
                    api_type = "Subst"
                    detail = "Substitution"

                events.append({
                    "time": {
                        "elapsed": _extract_minute(ev.get("minute")),
                        "extra": _extract_minute(ev.get("extra_min")) or None
                    },
                    "team": {"id": side_team_id, "name": side_team_name},
                    "player": {"id": ev.get("player_id"), "name": ev.get("player")},
                    "assist": {"id": ev.get("assist_id"), "name": ev.get("assist_player")} if ev.get("assist_id") else {"id": None, "name": None},
                    "type": api_type,
                    "detail": detail,
                    "comments": ev.get("result") or None
                })

    events.sort(key=lambda x: x["time"]["elapsed"])
    return events


def _make_absolute(url: str, request=None) -> str:
    """Resolve absolute URL for static images and logos across development and production environments.

    Args:
        url (str): Relative or absolute resource path.
        request (optional): Django HTTP request object.

    Returns:
        str: Absolute URI.
    """
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if request:
        try:
            return request.build_absolute_uri(url)
        except Exception:
            pass
    base = "http://localhost:8000"
    return f"{base}{url}" if url.startswith("/") else f"{base}/{url}"
