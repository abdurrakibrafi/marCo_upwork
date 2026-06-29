"""
apps/sports_apis/parsers/nhl.py

Pure parsing functions for StatPal NHL responses.
No DB access — just transform raw API dicts into clean Python dicts
that the Celery tasks can safely persist.

These functions are kept separate so they can be unit-tested without
Django / DB setup.
"""

from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional

import pytz

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Datetime helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_datetime(date_str: str, time_str: str = None) -> Optional[datetime]:
    """
    Convert StatPal date/time strings into a UTC-aware datetime or None.
    StatPal typically uses DD.MM.YYYY and HH:MM (assumed UTC).
    """
    if not date_str:
        return None
    try:
        if time_str:
            dt = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
        else:
            dt = datetime.strptime(date_str, "%d.%m.%Y")
        return pytz.UTC.localize(dt)
    except (ValueError, TypeError):
        logger.debug("NHL _parse_datetime failed: date=%s time=%s", date_str, time_str)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Status helpers
# ─────────────────────────────────────────────────────────────────────────────

# StatPal live status is a numeric string (period + time) e.g. "1", "2", "OT"
# We also handle explicit string statuses from the daily endpoint.

_LIVE_PERIOD_KEYWORDS = {"1st", "2nd", "3rd", "ot", "overtime", "so", "shootout"}
_COMPLETED_KEYWORDS   = {"final", "finished", "completed", "f/ot", "f/so"}
_POSTPONED_KEYWORDS   = {"postponed", "cancelled", "suspended", "abandoned"}


def _derive_status(raw_status: str) -> str:
    """
    Map a raw StatPal status string to our internal status:
        'live' | 'completed' | 'upcoming' | 'postponed'
    """
    if not raw_status:
        return "upcoming"

    s = str(raw_status).lower().strip()

    # Numeric period indicators → live  (e.g. "1", "2", "3", "45+2")
    if s.replace("'", "").replace("+", "").isdigit():
        return "live"

    if any(k in s for k in _LIVE_PERIOD_KEYWORDS):
        return "live"
    if any(k in s for k in _COMPLETED_KEYWORDS):
        return "completed"
    if any(k in s for k in _POSTPONED_KEYWORDS):
        return "postponed"

    return "upcoming"


# ─────────────────────────────────────────────────────────────────────────────
# Live Scores parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_live_scores(payload: dict) -> list[dict]:
    """
    Parse StatPal /nhl/livescores response.
    Returns a list of normalised match dicts — only truly live games.

    Expected payload shape:
        { "livescores": { "tournament": { "match": [ ... ] } } }
    or tournament may be a list of tournaments.
    """
    results = []

    livescores = payload.get("livescores", {})
    tournament = livescores.get("tournament", {})

    # tournament can be a single dict or a list
    tournaments = tournament if isinstance(tournament, list) else [tournament]

    for t in tournaments:
        if not t:
            continue
        matches = t.get("match", [])
        if isinstance(matches, dict):
            matches = [matches]

        for match in matches:
            raw_status = str(match.get("status", "")).strip()

            # Only keep genuinely live matches
            if _derive_status(raw_status) != "live":
                continue

            parsed = _parse_match(match)
            if parsed:
                results.append(parsed)

    logger.debug("parse_live_scores: %d live NHL games found", len(results))
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Daily / Fixtures parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_daily(payload: dict) -> list[dict]:
    """
    Parse StatPal /nhl/daily/{token} response.
    Returns ALL matches for the day (live, completed, upcoming).

    Expected shape:
        { "scores": { "tournament": { "match": [ ... ] } } }
    """
    results = []

    scores = payload.get("scores", {})
    tournament = scores.get("tournament", {})
    tournaments = tournament if isinstance(tournament, list) else [tournament]

    for t in tournaments:
        if not t:
            continue
        matches = t.get("match", [])
        if isinstance(matches, dict):
            matches = [matches]

        for match in matches:
            parsed = _parse_match(match)
            if parsed:
                results.append(parsed)

    logger.debug("parse_daily: %d NHL games parsed", len(results))
    return results


def _parse_match(match: dict) -> Optional[dict]:
    """
    Normalise a single match dict.
    Returns None if the match is missing a usable external_id.
    """
    external_id = (
        str(match.get("id", ""))
        or str(match.get("main_id", ""))
        or str(match.get("event_id", ""))
    )
    if not external_id:
        return None

    home = match.get("home", {}) or {}
    away = match.get("away", {}) or {}

    raw_status  = str(match.get("status", "")).strip()
    game_status = _derive_status(raw_status)

    home_goals = match.get("home_goals", home.get("goals", match.get("home_score", 0)))
    away_goals = match.get("away_goals", away.get("goals", match.get("away_score", 0)))

    def _safe_int(v) -> Optional[int]:
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    start_time = _parse_datetime(match.get("date"), match.get("time"))

    return {
        "external_id":   external_id,
        "home_id":       str(home.get("id", "")),
        "away_id":       str(away.get("id", "")),
        "home_team":     home.get("name", match.get("home_name", "")),
        "away_team":     away.get("name", match.get("away_name", "")),
        "home_abbr":     home.get("abbreviation", home.get("abbr", "")),
        "away_abbr":     away.get("abbreviation", away.get("abbr", "")),
        "home_score":    _safe_int(home_goals),
        "away_score":    _safe_int(away_goals),
        "status":        game_status,
        "status_detail": raw_status,
        "start_time":    start_time,
        "venue":         match.get("venue", match.get("stadium", "")),
        "league":        match.get("league", match.get("tournament_name", "NHL")),
        "raw_data":      match,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Standings parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_standings(payload: dict) -> list[dict]:
    """
    Parse StatPal /nhl/standings response.
    Returns a flat list of team standing dicts.

    Expected shape (typical NHL structure):
        {
          "standings": {
            "tournament": {
              "conference": [
                {
                  "name": "Eastern",
                  "division": [
                    {
                      "name": "Atlantic",
                      "team": [ ... ]
                    }
                  ]
                }
              ]
            }
          }
        }
    conference and division may be dicts instead of lists (single item).
    """
    results = []

    standings_root = payload.get("standings", {})
    tournament     = standings_root.get("tournament", {})

    conferences = tournament.get("conference", []) or tournament.get("league", [])
    if isinstance(conferences, dict):
        conferences = [conferences]

    for conf in conferences:
        conf_name = conf.get("name", "")

        divisions = conf.get("division", [])
        if isinstance(divisions, dict):
            divisions = [divisions]

        # Some responses have teams directly under tournament (no conference/division)
        if not divisions:
            teams = conf.get("team", [])
            if isinstance(teams, dict):
                teams = [teams]
            for team in teams:
                entry = _parse_standing_entry(team, conf_name, "")
                if entry:
                    results.append(entry)
            continue

        for div in divisions:
            div_name = div.get("name", "")
            teams = div.get("team", [])
            if isinstance(teams, dict):
                teams = [teams]

            for team in teams:
                entry = _parse_standing_entry(team, conf_name, div_name)
                if entry:
                    results.append(entry)

    logger.debug("parse_standings: %d NHL teams parsed", len(results))
    return results


def _parse_standing_entry(team: dict, conference: str, division: str) -> Optional[dict]:
    team_id = str(team.get("id", "")).strip()
    name    = team.get("name", "").strip()
    if not name:
        return None

    def _i(key, default=0):
        try:
            return int(team.get(key, default) or default)
        except (ValueError, TypeError):
            return default

    def _f(key, default=0.0):
        try:
            return float(team.get(key, default) or default)
        except (ValueError, TypeError):
            return default

    return {
        "team_id":        team_id,
        "team_name":      name,
        "team_abbr":      team.get("abbreviation", team.get("abbr", "")),
        "conference":     conference,
        "division":       division,
        "rank":           _i("position", _i("rank")),
        "wins":           _i("wins",  _i("w")),
        "losses":         _i("losses", _i("l")),
        "ot_losses":      _i("ot_losses", _i("otl")),
        "points":         _i("points", _i("pts")),
        "games_played":   _i("games_played", _i("gp")),
        "goals_for":      _i("goals_for",  _i("gf")),
        "goals_against":  _i("goals_against", _i("ga")),
        "goal_diff":      _i("goal_diff", _i("diff")),
        "home_record":    team.get("home_record", ""),
        "away_record":    team.get("away_record", ""),
        "last_10":        team.get("last_10", ""),
        "streak":         team.get("streak", ""),
        "win_pct":        _f("win_pct", _f("pct")),
        "raw_data":       team,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Roster parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_roster(payload: dict) -> list[dict]:
    """
    Parse StatPal /nhl/rosters/{abbr} response.
    Returns a list of player dicts.

    Expected shape:
        { "team": { "player": [ ... ] } }
    """
    results = []

    team_data = payload.get("team", {})
    team_id   = str(team_data.get("id", ""))
    team_name = team_data.get("name", "")
    team_abbr = team_data.get("abbreviation", team_data.get("abbr", ""))

    players = team_data.get("player", [])
    if isinstance(players, dict):
        players = [players]

    for p in players:
        parsed = _parse_player(p, team_id, team_name, team_abbr)
        if parsed:
            results.append(parsed)

    logger.debug("parse_roster: %d NHL players for %s", len(results), team_abbr)
    return results


def _parse_player(p: dict, team_id: str, team_name: str, team_abbr: str) -> Optional[dict]:
    player_id = str(p.get("id", "")).strip()
    name      = p.get("name", p.get("full_name", "")).strip()
    if not name:
        return None

    def _i(key, default=None):
        try:
            v = p.get(key)
            return int(v) if v is not None else default
        except (ValueError, TypeError):
            return default

    return {
        "player_id":      player_id,
        "name":           name,
        "first_name":     p.get("first_name", ""),
        "last_name":      p.get("last_name", ""),
        "jersey_number":  _i("jersey_number", _i("number")),
        "position":       p.get("position", p.get("pos", "")),
        "nationality":    p.get("nationality", p.get("country", "")),
        "birthdate":      p.get("birthdate", p.get("dob", "")),
        "height":         p.get("height", ""),
        "weight":         p.get("weight", ""),
        "shoots_catches": p.get("shoots_catches", p.get("shoots", p.get("catches", ""))),
        "status":         p.get("status", "active"),
        "team_id":        team_id,
        "team_name":      team_name,
        "team_abbr":      team_abbr,
        "image_url":      p.get("image_url", p.get("photo", "")),
        "raw_data":       p,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Team Stats parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_team_stats(payload: dict, team_abbr: str) -> dict:
    """
    Parse StatPal /nhl/team-stats/{abbr} response.
    Returns a flat dict of stat_key → value for storage in a JSONField.

    StatPal returns stats grouped by category → stat_name.
    We flatten everything into a single dict.
    """
    stats = {}
    statistics = payload.get("statistics", {})

    categories = statistics.get("category", [])
    if isinstance(categories, dict):
        categories = [categories]

    for cat in categories:
        cat_name   = cat.get("name", "general").lower().replace(" ", "_")
        cat_players = cat.get("player", [])   # team-level rows also called "player" in StatPal
        if isinstance(cat_players, dict):
            cat_players = [cat_players]

        for row in cat_players:
            # Each row might be a team-level stat; store every key
            for k, v in row.items():
                if k in ("id", "name", "abbreviation"):
                    continue
                stat_key = f"{cat_name}__{k}"
                stats[stat_key] = v

    logger.debug("parse_team_stats: %d stat entries for %s", len(stats), team_abbr)
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# Injuries parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_injuries(payload: dict) -> list[dict]:
    """
    Parse StatPal /nhl/injuries/{abbr} response.
    Returns a list of injury dicts.

    Expected shape:
        { "injuries": { "player": [ ... ] } }
    """
    results = []

    injuries_root = payload.get("injuries", {})
    players = injuries_root.get("player", [])
    if isinstance(players, dict):
        players = [players]

    for p in players:
        player_id   = str(p.get("id", "")).strip()
        player_name = p.get("name", "").strip()
        if not player_name:
            continue

        results.append({
            "player_id":       player_id,
            "player_name":     player_name,
            "injury_type":     p.get("injury_type", p.get("type", "")),
            "injury_detail":   p.get("injury", p.get("detail", "")),
            "status":          p.get("status", ""),          # Out / Day-to-Day / Suspended
            "expected_return": p.get("expected_return", p.get("return_date", "")),
            "notes":           p.get("notes", p.get("comment", "")),
            "raw_data":        p,
        })

    logger.debug("parse_injuries: %d injury records parsed", len(results))
    return results