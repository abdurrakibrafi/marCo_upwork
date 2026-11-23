import re
from datetime import datetime
from django.utils import timezone

# Status Constants
_FINISHED = {
    "ft", "aet", "pen", "finished", "after over time",
    "full-time", "retired", "walk over", "walkover", "awarded", "ended",
}

_CANCELLED = {
    "cancelled", "cancl", "abandoned", "abd", "canc", "postponed", "postp.",
}

_LIVE = {
    # General
    "1h", "2h", "ht", "et", "bt", "p", "susp", "int", "live",
    "in progress", "in play", "ongoing",
    # Basketball / American Football
    "q1", "q2", "q3", "q4", "ot", "halftime", "half time",
    "1st quarter", "2nd quarter", "3rd quarter", "4th quarter",
    "quarter 1", "quarter 2", "quarter 3", "quarter 4",
    # Hockey / Ice Hockey Periods
    "1st period", "2nd period", "3rd period",
    "period 1", "period 2", "period 3",
    "p1", "p2", "p3", "so", "shootout", "penalties", "overtime",
    # Tennis
    "1st set", "2nd set", "3rd set", "4th set", "5th set", "break",
    "set 1", "set 2", "set 3", "set 4", "set 5",
    # Cricket
    "stumps", "innings break", "lunch", "tea", "rain delay", "drinks", "dinner",
}


def _extract_minute(val) -> int:
    """Extract numeric match minute from string or int representations (e.g. '45+2').

    Args:
        val (Any): Input minute representation.

    Returns:
        int: Parsed integer minute.
    """
    if not val:
        return 0
    val_str = str(val).strip()
    match = re.search(r'\d+', val_str)
    if match:
        try:
            return int(match.group(0))
        except (ValueError, TypeError):
            return 0
    return 0


def _map_status(raw: str, sport: str = None, metadata: dict = None) -> str:
    """Normalize inconsistent provider match status strings into standard internal choices.

    Maps varied raw statuses (e.g. 'FT', 'AET', 'Q3', 'Set 2', 'Rain Delay') to:
    'upcoming', 'live', 'completed', or 'cancelled'.

    Args:
        raw (str): Raw status string from data provider.
        sport (str, optional): Sport slug identifier.
        metadata (dict, optional): Match metadata for multi-set analysis.

    Returns:
        str: Normalized status choice string.
    """
    if not raw:
        return "upcoming"

    raw_normalized = raw.lower().strip().rstrip('.')

    # General in-progress indicators
    if any(sig in raw_normalized for sig in ["in progress", "in play", "ongoing"]):
        return "live"

    # Soccer live minute representations (e.g. "45", "45'", "45+2", "1H 23'", "2H 70'", "HT", "ET")
    if sport in ("soccer", "football"):
        if re.search(r"^\d+(\+\d+)?['′]?$", raw_normalized):
            return "live"
        if re.search(r"\b(1h|2h)\s*\d+['′]?\b", raw_normalized):
            return "live"
        if re.search(r"\b\d+['′]\b", raw_normalized):
            return "live"

    # Ice Hockey / Field Hockey period indicators (e.g. "1st Period", "2nd Period", "3rd Period", "P1", "OT", "SO")
    if sport in ("ice_hockey", "hockey"):
        if any(p in raw_normalized for p in ["period", "1st per", "2nd per", "3rd per"]) or re.search(r"\bp[1-3]\b", raw_normalized):
            return "live"
        if raw_normalized in ("ot", "so", "shootout", "overtime", "penalties"):
            return "live"

    # Basketball / American Football quarter indicators (e.g. "Q1", "1st Quarter", "Halftime", "OT")
    if sport in ("basketball", "nba", "american_football", "nfl"):
        if any(q in raw_normalized for q in ["quarter", "1st qtr", "2nd qtr", "3rd qtr", "4th qtr"]) or re.search(r"\bq[1-4]\b", raw_normalized):
            return "live"
        if raw_normalized in ("halftime", "half time", "ot", "overtime"):
            return "live"

    if raw_normalized in _CANCELLED:
        return "cancelled"

    if raw_normalized in _FINISHED or "final" in raw_normalized:
        return "completed"

    if raw_normalized in _LIVE:
        return "live"

    # Tennis-specific live check via populated score fields
    if sport == "tennis" and metadata:
        players = metadata.get("player", [])
        if isinstance(players, list):
            score_populated = False
            for p in players:
                if not isinstance(p, dict):
                    continue
                for key in ["s1", "s2", "s3", "s4", "s5", "totalscore"]:
                    if str(p.get(key, "")).strip() != "":
                        score_populated = True
                        break
                if score_populated:
                    break
            if score_populated:
                return "live"
        
    # Check for Baseball live inning indicators (e.g., "Top 5th", "Bottom 8th", "Bot 9th", "Mid 2nd", "End 6th")
    if any(ind in raw_normalized for ind in ["top ", "bottom ", "bot ", "middle ", "mid ", "end "]):
        if any(ind in raw_normalized for ind in ["1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th", "10th", "st", "nd", "rd", "th"]) or re.search(r"\b\d+\b", raw_normalized):
            return "live"
    if "inning" in raw_normalized:
        return "live"
        
    return "upcoming"


def _parse_dt(date_str: str, time_str: str) -> datetime:
    """Parse date and time strings (supporting DD.MM.YYYY, YYYY-MM-DD, DD/MM/YYYY, HH:MM, HH:MM:SS) into a timezone-aware datetime.

    Args:
        date_str (str): Date string.
        time_str (str): Time string.

    Returns:
        datetime: Aware datetime object.
    """
    if not date_str:
        return timezone.now()

    d_str = str(date_str).strip()
    t_str = str(time_str or "00:00").strip()
    if len(t_str) > 5 and ":" in t_str:
        t_str = t_str[:5]
    if not re.match(r"^\d{1,2}:\d{2}$", t_str):
        t_str = "00:00"

    formats = [
        ("%d.%m.%Y %H:%M", f"{d_str} {t_str}"),
        ("%Y-%m-%d %H:%M", f"{d_str} {t_str}"),
        ("%d/%m/%Y %H:%M", f"{d_str} {t_str}"),
        ("%Y/%m/%d %H:%M", f"{d_str} {t_str}"),
        ("%d-%m-%Y %H:%M", f"{d_str} {t_str}"),
        ("%d.%m.%Y", d_str),
        ("%Y-%m-%d", d_str),
        ("%d/%m/%Y", d_str),
    ]
    for fmt, val in formats:
        try:
            naive = datetime.strptime(val, fmt)
            return timezone.make_aware(naive, timezone.get_current_timezone())
        except Exception:
            continue
    return timezone.now()


def _safe_int(val) -> int | None:
    """Safely convert numeric string or object into an integer, ignoring non-numeric characters.

    Args:
        val (Any): Input value.

    Returns:
        int or None: Parsed integer or None.
    """
    if val is None or str(val).strip() in ("", "?", "-", "None", "null", "undefined"):
        return None
    try:
        s = str(val).strip()
        # e.g. "503/9d" -> 503, "(fo) 290 and 229/6" -> 229
        # if slash or & present, look at the first section
        s_part = s.split("/")[0].split("&")[0].strip()
        digits = re.findall(r'\d+', s_part)
        if digits:
            return int(digits[-1])
        # fallback to any digits in entire string
        all_digits = re.findall(r'\d+', s)
        if all_digits:
            return int(all_digits[0])
        return None
    except Exception:
        return None


def _clean_score(val) -> int | None:
    """Clean and parse raw match score value into an integer.

    Args:
        val (Any): Input score value.

    Returns:
        int or None: Parsed integer score or None.
    """
    if val is None or str(val).strip() in ("", "?", "-", "None", "null", "undefined"):
        return None
    try:
        return int(str(val).split("/")[0].split("&")[0].strip())
    except Exception:
        return None


def _soccer_rows(data: dict) -> list:
    """Extract and normalize soccer match fixture dictionaries from raw StatPal response payload.

    Args:
        data (dict): StatPal soccer API response.

    Returns:
        list: Normalized fixture dictionaries.
    """
    root_data = None
    if "live_matches" in data:
        root_data = data["live_matches"]
    else:
        for v in data.values():
            if isinstance(v, dict) and "league" in v:
                root_data = v
                break

    if not root_data:
        return []

    leagues = root_data.get("league", [])
    if isinstance(leagues, dict):
        leagues = [leagues]

    rows = []
    for lg in leagues:
        matches = lg.get("match", [])
        if isinstance(matches, dict):
            matches = [matches]
        elif not isinstance(matches, list):
            matches = []

        for m in matches:
            if not isinstance(m, dict):
                continue

            home = m.get("home", {})
            away = m.get("away", {})
            rows.append({
                "external_id": str(m.get("main_id") or m.get("id", "")),
                "sport": "soccer",
                "league_id":   str(lg.get("id", "")),
                "league_name": lg.get("name", ""),
                "home_id":   str(home.get("id", "")),
                "home_name": home.get("name", ""),
                "away_id":   str(away.get("id", "")),
                "away_name": away.get("name", ""),
                "home_score": _safe_int(home.get("goals") or home.get("score")),
                "away_score": _safe_int(away.get("goals") or away.get("score")),
                "status_raw": m.get("status", "NS"),
                "date":  m.get("date", ""),
                "time":  m.get("time", "00:00"),
                "venue": m.get("venue", ""),
                "raw":   m,
            })
    return rows


def _generic_sport_rows(data: dict, sport_name: str) -> list:
    """Extract and normalize match fixtures for generic multi-tournament sports (NBA, NFL, NHL, Tennis, etc.).

    Args:
        data (dict): Raw StatPal API response payload.
        sport_name (str): Sport slug identifier.

    Returns:
        list: Normalized fixture dictionaries.
    """
    tournaments_data = (
        data.get("livescores", {}).get("tournament")
        or data.get("scores", {}).get("tournament", {})
        or []
    )

    if isinstance(tournaments_data, dict):
        tournaments_data = [tournaments_data]

    rows = []
    for tournament in tournaments_data:
        if not isinstance(tournament, dict):
            continue

        league_id   = str(tournament.get("id", ""))
        league_name = tournament.get("league") or tournament.get("name") or ""

        matches = tournament.get("match", [])
        if isinstance(matches, dict):
            matches = [matches]
        elif not isinstance(matches, list):
            matches = []

        for m in matches:
            if not isinstance(m, dict):
                continue
            home = m.get("home", {})
            away = m.get("away", {})

            players = m.get("player", [])
            if (not home or not away) and isinstance(players, list) and len(players) >= 2:
                home = players[0]
                away = players[1]

            rows.append({
                "external_id": str(m.get("id", "")),
                "sport": sport_name,
                "league_id":   league_id,
                "league_name": league_name,
                "home_id":   str(home.get("id", "")),
                "home_name": home.get("name", ""),
                "away_id":   str(away.get("id", "")),
                "away_name": away.get("name", ""),
                "home_score": _safe_int(home.get("totalscore")),
                "away_score": _safe_int(away.get("totalscore")),
                "status_raw": m.get("status", "NS"),
                "date":  m.get("date", ""),
                "time":  m.get("time", "00:00"),
                "venue": m.get("venue", ""),
                "raw":   m,
            })
    return rows


def _nba_rows(data: dict) -> list:
    """Extract NBA basketball match fixture rows from StatPal response."""
    return _generic_sport_rows(data, "basketball")


def _nfl_rows(data: dict) -> list:
    """Extract American football (NFL) match fixture rows from StatPal response."""
    return _generic_sport_rows(data, "american_football")


def _hockey_rows(data: dict) -> list:
    """Extract ice hockey match fixture rows from StatPal response."""
    return _generic_sport_rows(data, "ice_hockey")


def _tennis_rows(data: dict) -> list:
    """Extract tennis match fixture rows from StatPal response."""
    return _generic_sport_rows(data, "tennis")


def _mlb_rows(data: dict) -> list:
    """Extract baseball (MLB) match fixture rows from StatPal response."""
    return _generic_sport_rows(data, "baseball")


def _handball_rows(data: dict) -> list:
    """Extract handball match fixture rows from StatPal response."""
    return _generic_sport_rows(data, "handball")


def _volleyball_rows(data: dict) -> list:
    """Extract volleyball match fixture rows from StatPal response."""
    return _generic_sport_rows(data, "volleyball")


def _cricket_rows(data: dict) -> list:
    """Extract cricket match fixture rows from StatPal response payload.

    Args:
        data (dict): Raw cricket scores response.

    Returns:
        list: Normalized cricket fixture dictionaries.
    """
    categories = (
        data.get("scores", {}).get("category", [])
        or data.get("fixtures", {}).get("category", [])
    )
    if isinstance(categories, dict):
        categories = [categories]

    rows = []
    for cat in categories:
        m = cat.get("match")
        if not m:
            continue
        match_list = m if isinstance(m, list) else [m]
        for match in match_list:
            home = match.get("home", {})
            away = match.get("away", {})
            status_raw = match.get("status", "NS")
            home_stat_val = home.get("totalscore") or home.get("stat")
            away_stat_val = away.get("totalscore") or away.get("stat")
            rows.append({
                "external_id": str(match.get("id", "")),
                "sport": "cricket",
                "league_id":   str(cat.get("id", "")),
                "league_name": cat.get("name", ""),
                "home_id":   str(home.get("id", "")),
                "home_name": home.get("name", ""),
                "away_id":   str(away.get("id", "")),
                "away_name": away.get("name", ""),
                "home_score": _safe_int(home_stat_val),
                "away_score": _safe_int(away_stat_val),
                "status_raw": status_raw,
                "date":  match.get("date", ""),
                "time":  match.get("time", "00:00"),
                "venue": match.get("venue", ""),
                "raw":   match,
            })
    return rows


def _f1_rows(data: dict) -> list:
    """Extract Formula 1 grand prix race events from StatPal response payload.

    Args:
        data (dict): Raw F1 race response.

    Returns:
        list: Normalized F1 race event dictionaries.
    """
    races_data = data.get("livescores", {}).get("tournament") or data.get("tournament") or data.get("races")
    if not races_data:
        return []

    tournaments = races_data if isinstance(races_data, list) else [races_data]
    rows = []
    for tour in tournaments:
        if not isinstance(tour, dict):
            continue
        race_id = str(tour.get("id", ""))
        race_name = tour.get("name", "Formula 1 Grand Prix")
        rows.append({
            "external_id": f"f1_{race_id}",
            "sport": "f1",
            "league_id": race_id,
            "league_name": "Formula 1",
            "home_id": f"f1_{race_id}",
            "home_name": race_name,
            "away_id": None,
            "away_name": None,
            "home_score": None,
            "away_score": None,
            "status_raw": tour.get("status", "NS"),
            "date": tour.get("date", tour.get("start_date", "")),
            "time": tour.get("time", "00:00"),
            "venue": tour.get("circuit", tour.get("venue", "")),
            "raw": tour,
        })
    return rows


def _golf_position_sort_key(p) -> int:
    """Safely convert player leaderboard position string (e.g. 'T1', 'CUT') into an integer sort key.

    Args:
        p (dict): Golf player record.

    Returns:
        int: Numeric sort key.
    """
    pos = p.get('pos', '999')
    if not pos:
        return 999
    pos = str(pos).lstrip('T').strip()  # "T1" -> "1"
    try:
        return int(pos)
    except (ValueError, TypeError):
        return 999


def _golf_rows(data: dict) -> list:
    """Extract golf tournament event and leaderboard rows from StatPal response payload.

    Args:
        data (dict): Raw golf tournament payload.

    Returns:
        list: Normalized golf tournament event dictionaries.
    """
    tour_data = (
        data.get("livescore", {}).get("tournament")
        or data.get("fixtures", {}).get("tournament")
        or data.get("tournament")
    )
    if not tour_data:
        return []

    tournaments = tour_data if isinstance(tour_data, list) else [tour_data]
    rows = []
    for tour in tournaments:
        if not isinstance(tour, dict):
            continue
        league_name = tour.get("name", "Golf Event")
        league_id = str(tour.get("id", ""))
        players = tour.get("player", [])
        if isinstance(players, dict):
            players = [players]
        elif not isinstance(players, list):
            players = []

        leader_score, leader_name = None, None
        if players:
            try:
                leader = sorted(players, key=_golf_position_sort_key)[0]
                leader_name = leader.get('name')
                leader_score = leader.get('par')
            except Exception:
                pass

        rows.append({
            "external_id": f"golf_{league_id}",
            "sport": "golf",
            "league_id": league_id,
            "league_name": league_name,
            "home_id": f"golf_{league_id}",
            "home_name": league_name,
            "away_id": None,
            "away_name": None,
            "home_score": leader_score,
            "away_score": tour.get('par'),
            "status_raw": tour.get("status", "NS"),
            "date": tour.get("start_date", timezone.now().strftime("%d.%m.%Y")),
            "time": "00:00",
            "venue": tour.get("venue", ""),
            "raw": tour,
        })
    return rows


def _horse_racing_rows(data: dict) -> list:
    """Extract horse racing tournament fixture rows from StatPal response payload.

    Args:
        data (dict): Raw horse racing response.

    Returns:
        list: Normalized horse race event dictionaries.
    """
    tournaments = data.get("scores", {}).get("tournament", [])
    if not isinstance(tournaments, list):
        tournaments = [tournaments]

    rows = []
    for tour in tournaments:
        races = tour.get("race", [])
        if not isinstance(races, list):
            races = [races]

        tour_name = str(tour.get("name", "Horse Racing") or "Horse Racing").strip()
        tour_id_raw = str(tour.get("id", "") or "").strip()
        tour_id = f"hr_tour_{tour_id_raw}" if tour_id_raw and tour_id_raw.lower() not in ("none", "null", "0") else f"hr_tour_{re.sub(r'[^a-zA-Z0-9]', '', tour_name.lower()) or 'course'}"

        for race in races:
            race_id = str(race.get("id", ""))
            race_name = race.get("name", "Horse Race")
            
            rows.append({
                "external_id": f"hr_{race_id}",
                "sport": "horse_racing",
                "league_id": tour_id,
                "league_name": tour_name,
                "home_id": f"hr_{race_id}",
                "home_name": race_name,
                "away_id": None,
                "away_name": None,
                "home_score": None,
                "away_score": None,
                "status_raw": race.get("status", "NS"),
                "date": tour.get("date", ""),
                "time": race.get("time", "00:00"),
                "venue": tour_name,
                "raw": race,
            })
    return rows


def _tsdb_soccer_row(event: dict) -> dict:
    """Convert a raw TheSportsDB event payload into the standardized internal row structure.

    Prefixes IDs with 'tsdb_' to prevent key collisions with StatPal fixture records.

    Args:
        event (dict): TheSportsDB match event dictionary.

    Returns:
        dict: Standardized fixture dictionary.
    """
    date_raw = event.get('dateEvent', '')
    try:
        d = datetime.strptime(date_raw, '%Y-%m-%d')
        date_str = d.strftime('%d.%m.%Y')
    except Exception:
        date_str = ''

    time_raw = (event.get('strTime') or '00:00:00').strip()
    time_str = time_raw[:5]

    home_score = None
    away_score = None
    try:
        if event.get('intHomeScore') not in (None, ''):
            home_score = int(event['intHomeScore'])
    except (ValueError, TypeError):
        pass
    try:
        if event.get('intAwayScore') not in (None, ''):
            away_score = int(event['intAwayScore'])
    except (ValueError, TypeError):
        pass

    sport_raw = str(event.get('strSport', '') or 'Soccer').lower().strip()
    sport_map = {
        'soccer': 'soccer',
        'basketball': 'basketball',
        'american football': 'american_football',
        'baseball': 'baseball',
        'ice hockey': 'ice_hockey',
        'cricket': 'cricket',
        'rugby': 'rugby',
        'motorsport': 'motorsports',
        'golf': 'golf',
        'tennis': 'tennis',
    }
    sport = sport_map.get(sport_raw, 'soccer')

    return {
        'external_id': f"tsdb_{event.get('idEvent', '')}",
        'sport':        sport,
        'league_id':    f"tsdb_league_{event.get('idLeague', '')}",
        'league_name':  event.get('strLeague', ''),
        'home_id':      f"tsdb_team_{event.get('idHomeTeam', '')}",
        'home_name':    event.get('strHomeTeam', ''),
        'away_id':      f"tsdb_team_{event.get('idAwayTeam', '')}",
        'away_name':    event.get('strAwayTeam', ''),
        'home_score':   home_score,
        'away_score':   away_score,
        'status_raw':   event.get('strStatus') or 'NS',
        'date':         date_str,
        'time':         time_str,
        'venue':        event.get('strVenue', '') or '',
        'home_logo':    event.get('strHomeTeamBadge', '') or '',
        'away_logo':    event.get('strAwayTeamBadge', '') or '',
        'raw':          event,
    }
