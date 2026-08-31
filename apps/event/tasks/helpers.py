import logging
from django.core.cache import cache
from apps.entity.models import Entity, Team, League
from apps.entity.utils.matcher import get_or_create_precise_entity, normalize_statpal_logo_url, find_team_logo_by_name
from apps.event.models import Event
from apps.score.models import LiveScore
from .parsers import _map_status, _clean_score, _parse_dt
from .details import _populate_statpal_event_details

logger = logging.getLogger(__name__)


def _get_or_create_team_entity(api_source: str, external_id: str, name: str, sport: str, logo_url: str = ''):
    """Retrieve or create a team Entity and associated Team model record.

    Args:
        api_source (str): Source identifier (e.g. 'statpal', 'thesportsdb').
        external_id (str): Remote ID.
        name (str): Team name.
        sport (str): Sport slug.
        logo_url (str, optional): Team badge/logo URL.

    Returns:
        Entity: Resolved team entity instance.
    """
    entity, created = Entity.objects.get_or_create(
        api_source=api_source,
        external_id=str(external_id),
        type='team',
        defaults={
            'name': name,
            'sport': sport,
            'logo_url': logo_url or '',
            'has_api_data': True,
        }
    )

    if not created and logo_url and not entity.logo_url:
        entity.logo_url = logo_url
        entity.save(update_fields=['logo_url'])

    Team.objects.get_or_create(entity=entity)
    return entity


def _get_or_create_league_entity(api_source: str, external_id: str, name: str, sport: str, logo_url: str = ''):
    """Retrieve or create a league Entity and associated League model record.

    Args:
        api_source (str): Source identifier.
        external_id (str): Remote ID.
        name (str): League name.
        sport (str): Sport slug.
        logo_url (str, optional): League logo URL.

    Returns:
        Entity: Resolved league entity instance.
    """
    entity, created = Entity.objects.get_or_create(
        api_source=api_source,
        external_id=str(external_id),
        type='league',
        defaults={
            'name': name,
            'sport': sport,
            'logo_url': logo_url or '',
            'has_api_data': True,
        }
    )

    if not created and logo_url and not entity.logo_url:
        entity.logo_url = logo_url
        entity.save(update_fields=['logo_url'])

    League.objects.get_or_create(entity=entity)
    return entity


def _save_event(row: dict, api_source: str = "statpal") -> Event | None:
    """Save or update an Event record in the database from a normalized fixture row.

    Resolves participants, venues, and status mappings, triggering detail population on completion.

    Args:
        row (dict): Normalized fixture data row.
        api_source (str, optional): API source identifier. Defaults to 'statpal'.

    Returns:
        Event or None: Persisted Event instance, or None if skipped/cancelled.
    """
    status = _map_status(row["status_raw"], sport=row.get("sport"), metadata=row.get("raw"))
    if status is None:
        if row.get("external_id"):
            LiveScore.objects.filter(sport=row["sport"], external_id=row["external_id"]).delete()
        return None
    if not row["external_id"]:
        return None

    # Prevent overwriting "live" status with "upcoming" from subsequent fixture updates
    existing_event = Event.objects.filter(external_id=row["external_id"]).first()
    if existing_event and existing_event.status == "live" and status == "upcoming":
        status = "live"
        row["status_raw"] = existing_event.status_detail
        if existing_event.metadata and not row["raw"].get("inning") and not row["raw"].get("events"):
            row["raw"] = existing_event.metadata

    sport = row["sport"]
    league = get_or_create_precise_entity(
        row["league_id"], row["league_name"], sport, entity_type="league"
    )
    home = get_or_create_precise_entity(
        row["home_id"], row["home_name"], sport, entity_type="team"
    )
    if row.get("home_logo") and not home.logo_url:
        norm_logo = normalize_statpal_logo_url(row["home_logo"], home.name, "team", sport)
        if norm_logo:
            home.logo_url = norm_logo
            home.save(update_fields=['logo_url'])
            
    # For individual sports, away entity can be null
    away = None
    if row.get("away_id") and row.get("away_name"):
        away = get_or_create_precise_entity(
            row["away_id"], row["away_name"], sport, entity_type="team"
        )
        if row.get("away_logo") and not away.logo_url:
            norm_logo = normalize_statpal_logo_url(row["away_logo"], away.name, "team", sport)
            if norm_logo:
                away.logo_url = norm_logo
                away.save(update_fields=['logo_url'])
                
    # Prevent fixture updates or incomplete rows from wiping out live scores
    home_score_val = _clean_score(row.get("home_score"))
    away_score_val = _clean_score(row.get("away_score"))
    if existing_event and home_score_val is None and existing_event.home_score is not None:
        home_score_val = existing_event.home_score
    if existing_event and away_score_val is None and existing_event.away_score is not None:
        away_score_val = existing_event.away_score

    start_time = _parse_dt(row["date"], row["time"])

    metadata_val = row["raw"]
    if existing_event and existing_event.metadata:
        merged_meta = dict(existing_event.metadata)
        if isinstance(row.get("raw"), dict):
            merged_meta.update(row["raw"])
        metadata_val = merged_meta

    # Normalize baseball in1..in9 flat keys from nested innings.inning list
    # StatPal API always sends in1..in9 as empty strings; real data is in innings.inning list
    if isinstance(metadata_val, dict) and row.get("sport") in ("baseball", "mlb"):
        for side in ("home", "away"):
            side_data = metadata_val.get(side)
            if not isinstance(side_data, dict):
                continue
            # Only fill if all in1..in9 are empty
            flat_keys_empty = all(side_data.get(f"in{i}", "") == "" for i in range(1, 10))
            if flat_keys_empty:
                nested = side_data.get("innings", {})
                inning_list = nested.get("inning", []) if isinstance(nested, dict) else []
                if isinstance(inning_list, dict):
                    inning_list = [inning_list]
                for inn in inning_list:
                    num = inn.get("number")
                    score = inn.get("score")
                    if num and score is not None:
                        key = f"in{num}"
                        if key in side_data:
                            side_data[key] = score

    # Extract venue fields from row or metadata
    venue_val = row.get("venue") or (existing_event.venue_name if existing_event else "")
    venue_city_val = row.get("venue_city") or (existing_event.venue_city if existing_event else "")
    venue_country_val = row.get("venue_country") or (existing_event.venue_country if existing_event else "")

    if isinstance(metadata_val, dict):
        matchinfo = metadata_val.get('matchinfo', {})
        if isinstance(matchinfo, dict):
            for itm in matchinfo.get('info', []):
                if isinstance(itm, dict):
                    i_name = str(itm.get('name', '')).strip().lower()
                    i_val = str(itm.get('value', '')).strip()
                    if i_name in ('venue', 'stadium') and not venue_val:
                        venue_val = i_val
                    elif i_name in ('city', 'location') and not venue_city_val:
                        venue_city_val = i_val
                    elif i_name in ('country', 'nation') and not venue_country_val:
                        venue_country_val = i_val

    event, _ = Event.objects.update_or_create(
        api_source=api_source,
        external_id=row["external_id"],
        defaults={
            "sport":         sport,
            "home_entity":   home,
            "away_entity":   away,
            "league":        league,
            "status":        status,
            "status_detail": row["status_raw"],
            "home_score":    home_score_val,
            "away_score":    away_score_val,
            "venue_name":    venue_val,
            "venue_city":    venue_city_val,
            "venue_country": venue_country_val,
            "start_time":    start_time,
            "metadata":      metadata_val,
        },
    )
    if status == "completed":
        try:
            _populate_statpal_event_details(event)
        except Exception as e:
            logger.warning(f"Failed to auto-populate statpal event details for {event.id}: {e}")
    return event


def _save_livescore(row: dict, event: Event):
    """Save or synchronize an active LiveScore record corresponding to an ongoing match event.

    Args:
        row (dict): Normalized match data payload.
        event (Event): Associated Event instance.

    Returns:
        LiveScore or None: Updated LiveScore model instance if active, else None.
    """
    status = event.status
    ls_sport = row["sport"]
    external_id = row["external_id"]

    if status != "live":
        LiveScore.objects.filter(sport=ls_sport, external_id=external_id).delete()
        return None

    home_logo_raw = event.home_entity.logo_url if event.home_entity else ""
    if home_logo_raw and "statpal.io" in home_logo_raw:
        home_logo_raw = ""
    home_logo_val = home_logo_raw or find_team_logo_by_name(row["home_name"])

    away_logo_raw = event.away_entity.logo_url if event.away_entity else ""
    if away_logo_raw and "statpal.io" in away_logo_raw:
        away_logo_raw = ""
    away_logo_val = away_logo_raw or find_team_logo_by_name(row["away_name"])

    existing_ls = LiveScore.objects.filter(sport=ls_sport, external_id=external_id).first()
    home_score_ls = _clean_score(row.get("home_score"))
    away_score_ls = _clean_score(row.get("away_score"))
    if existing_ls and home_score_ls is None and existing_ls.home_score is not None:
        home_score_ls = existing_ls.home_score
    if existing_ls and away_score_ls is None and existing_ls.away_score is not None:
        away_score_ls = existing_ls.away_score

    live_obj, _ = LiveScore.objects.update_or_create(
        sport=ls_sport,
        external_id=external_id,
        defaults={
            "home_team":     row["home_name"],
            "away_team":     row["away_name"],
            "home_logo":     home_logo_val,
            "away_logo":     away_logo_val,
            "home_score":    home_score_ls,
            "away_score":    away_score_ls,
            "status":        status,
            "status_detail": row["status_raw"],
            "start_time":    event.start_time,
            "raw_data":      row["raw"],
        },
    )

    live_obj.save(update_fields=['updated_at'])
    cache.set(f"live_scores_{ls_sport}", True, timeout=120)
    return live_obj
