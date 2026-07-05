"""
apps/entity/utils/matcher.py

Entity.type choices  : 'team', 'athlete', 'league'
Entity.sport choices : 'basketball', 'soccer', 'cricket', ...
Entity.api_source    : CharField (blank=True)
Entity.external_id   : CharField (blank=True)
Entity.logo_url      : URLField  (blank=True)

"""
import logging
from django.conf import settings
from apps.entity.models import Entity, Team

logger = logging.getLogger(__name__)

_ACCESS_KEY = getattr(settings, "STATPAL_ACCESS_KEY", "bc343795-df19-407b-8fb2-301dd5cdb844")
_IMAGE_BASE = "https://statpal.io/api/v2/{sport}/images"
_LOGO_SUPPORTED_SPORTS = {
    "soccer", "cricket", "nba", "nfl", "basketball", "football",
    "baseball", "hockey", "handball", "volleyball", "tennis"
}
# StatPal sport name → Entity.sport choice
_SPORT_MAP = {
    "nba":     "basketball",   # Entity.sport = 'basketball' for NBA
    "cricket": "cricket",
    "soccer":  "soccer",
}


def _logo_url(entity_type: str, statpal_id: str, sport: str) -> str:
    if sport not in _LOGO_SUPPORTED_SPORTS:
        return ""
    base = _IMAGE_BASE.format(sport=sport)
    return f"{base}?type={entity_type}&id={statpal_id}&access_key={_ACCESS_KEY}"


def _needs_logo(entity) -> bool:
    return not entity.logo_url or "statpal.io" not in entity.logo_url


def get_or_create_precise_entity(
    statpal_id,
    name: str,
    sport: str,       
    entity_type: str = "team",
):
    """
    Match or create an Entity row for a StatPal data item.

    Lookup order:
      1. api_source='statpal' + external_id  (fastest)
      2. name__iexact + sport + type         (prevents cross-sport collision)
      3. Create new

    Returns an Entity instance — never None.
    """
    statpal_id  = str(statpal_id)
    entity_sport = _SPORT_MAP.get(sport, sport)   # 'nba' → 'basketball'
    logo         = _logo_url(entity_type, statpal_id, sport) 

    # 1 — exact StatPal ID match
    entity = Entity.objects.filter(
        api_source="statpal", 
        external_id=statpal_id,
        type=entity_type
    ).first()
    if entity:
        if _needs_logo(entity):
            entity.logo_url = logo
            entity.save(update_fields=["logo_url"])
        return entity

    # 2 — name + sport + type match
    entity = Entity.objects.filter(
        name__iexact=name,
        sport=entity_sport,
        type=entity_type,
    ).first()
    if entity:
        update_fields = ["api_source", "external_id"]
        entity.api_source  = "statpal"
        entity.external_id = statpal_id
        if _needs_logo(entity):
            entity.logo_url = logo
            update_fields.append("logo_url")
        entity.save(update_fields=update_fields)
        logger.debug("Linked existing entity '%s' → StatPal id=%s", name, statpal_id)
        return entity

    # 3 — create
    try:
        entity = Entity.objects.create(
            name=name,
            sport=entity_sport,
            type=entity_type,
            api_source="statpal",
            external_id=statpal_id,
            logo_url=logo,
        )
        logger.info("Created entity '%s' (sport=%s, type=%s)", name, entity_sport, entity_type)
        if entity_type == "team":
            Team.objects.get_or_create(entity=entity)
    except Exception:
        # Race condition: another worker beat us to it
        entity = Entity.objects.filter(
            api_source="statpal", external_id=statpal_id
        ).first()
        if entity is None:
            raise
    return entity