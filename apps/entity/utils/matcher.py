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


def _needs_logo(entity, sport: str) -> bool:
    # Only use StatPal logo for soccer, and only if the team does not already have a logo.
    # Other sports don't have valid logos in StatPal, and we never want to overwrite
    # an existing logo (e.g. custom user uploads or Wikipedia logos).
    if sport != "soccer":
        return False
    return not entity.logo_url


def _needs_logo_update(entity, new_logo) -> bool:
    if not new_logo:
        return False
    current_logo = entity.logo_url
    if not current_logo:
        return True
    # If current logo is an invalid StatPal logo (not soccer), update it
    if "statpal.io" in current_logo and "/soccer/" not in current_logo:
        return True
    return False


def find_team_logo_by_name(name):
    """
    Search database for any team with name matching `name` (case-insensitive)
    that has a non-empty logo_url, and return the logo_url. Uses Django cache to avoid N+1 queries.
    """
    if not name or not str(name).strip():
        return ""
    name_clean = name.strip()
    cache_key = f"logo_by_name_{name_clean.lower().replace(' ', '_')}"
    
    from django.core.cache import cache
    cached_logo = cache.get(cache_key)
    if cached_logo is not None:
        return cached_logo

    # Look for an exact match (case-insensitive) of type 'team'
    logos = Entity.objects.filter(
        name__iexact=name_clean,
        type="team"
    ).exclude(logo_url="").values_list("logo_url", flat=True)
    
    logo_val = ""
    for l in logos:
        # Avoid invalid StatPal URLs
        if l and ("statpal.io" not in l or "/soccer/" in l):
            logo_val = l
            break
    # Cache for 24 hours (86400 seconds)
    cache.set(cache_key, logo_val, timeout=86400)
    return logo_val


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
    logo         = _logo_url(entity_type, statpal_id, sport) if sport == "soccer" else ""
    if not logo and entity_type == "team":
        logo = find_team_logo_by_name(name)

    # Guard against empty/blank names
    if not name or not str(name).strip():
        # Try to find existing entity by ID to reuse it (avoiding overwrite/placeholder name)
        existing = Entity.objects.filter(
            api_source="statpal",
            external_id=statpal_id,
            type=entity_type
        ).first()
        if existing:
            return existing
        # Fallback to placeholder name if it does not exist
        name = f"Unknown {entity_type.capitalize()}"

    # 1 — exact StatPal ID match
    entity = Entity.objects.filter(
        api_source="statpal", 
        external_id=statpal_id,
        type=entity_type
    ).first()
    if entity:
        if _needs_logo(entity, sport):
            entity.logo_url = logo
            entity.save(update_fields=["logo_url"])
        elif _needs_logo_update(entity, logo):
            entity.logo_url = logo
            entity.save(update_fields=["logo_url"])
        return entity

    # 1.1 — Try matching through CanonicalEntity mappings
    from apps.entity.models import CanonicalEntity

    # Check by StatPal external_id mapping
    canonical = CanonicalEntity.objects.filter(
        sport=entity_sport,
        entity_type=entity_type,
        external_ids__statpal=statpal_id
    ).first()

    if not canonical:
        # Check by canonical name
        canonical = CanonicalEntity.objects.filter(
            sport=entity_sport,
            entity_type=entity_type,
            canonical_name__iexact=name
        ).first()

    if not canonical:
        # Check by name variations
        try:
            canonical = CanonicalEntity.objects.filter(
                sport=entity_sport,
                entity_type=entity_type,
                name_variations__contains=name
            ).first()
        except Exception:
            # Fallback for SQLite in local unit tests which doesn't support JSON contains lookup
            all_canonicals = CanonicalEntity.objects.filter(
                sport=entity_sport,
                entity_type=entity_type
            )
            for c in all_canonicals:
                if name in (c.name_variations or []):
                    canonical = c
                    break

    if canonical:
        entity = canonical.entity
        update_fields = []
        if entity.api_source != "statpal" or entity.external_id != statpal_id:
            entity.api_source = "statpal"
            entity.external_id = statpal_id
            update_fields.extend(["api_source", "external_id"])
        if _needs_logo(entity, sport):
            entity.logo_url = logo
            update_fields.append("logo_url")
        elif _needs_logo_update(entity, logo):
            entity.logo_url = logo
            update_fields.append("logo_url")
        if update_fields:
            entity.save(update_fields=update_fields)

        if canonical.external_ids.get("statpal") != statpal_id:
            canonical.external_ids["statpal"] = statpal_id
            canonical.save(update_fields=["external_ids"])
        return entity

    # 2 — name + sport + type match (exact)
    entity = Entity.objects.filter(
        name__iexact=name,
        sport=entity_sport,
        type=entity_type,
    ).first()
    if entity:
        update_fields = ["api_source", "external_id"]
        entity.api_source  = "statpal"
        entity.external_id = statpal_id
        if _needs_logo(entity, sport):
            entity.logo_url = logo
            update_fields.append("logo_url")
        elif _needs_logo_update(entity, logo):
            entity.logo_url = logo
            update_fields.append("logo_url")
        entity.save(update_fields=update_fields)
        logger.debug("Linked existing entity '%s' → StatPal id=%s", name, statpal_id)
        
        # Link or create CanonicalEntity for it
        CanonicalEntity.objects.get_or_create(
            entity=entity,
            defaults={
                'sport': entity_sport,
                'entity_type': entity_type,
                'canonical_name': entity.name,
                'name_variations': [entity.name],
                'external_ids': {'statpal': statpal_id}
            }
        )
        return entity

    # 2.5 — name similarity fallback (fuzzy matching)
    similar_entities = Entity.objects.filter(
        type=entity_type,
        sport=entity_sport,
    ).exclude(api_source="statpal")

    from apps.entity.utils.normalizers import find_similar_entity
    similar_entity, score = find_similar_entity(name, similar_entities, threshold=0.85)
    if similar_entity:
        similar_entity.api_source = "statpal"
        similar_entity.external_id = statpal_id
        update_fields = ["api_source", "external_id"]
        if _needs_logo(similar_entity, sport):
            similar_entity.logo_url = logo
            update_fields.append("logo_url")
        elif _needs_logo_update(similar_entity, logo):
            similar_entity.logo_url = logo
            update_fields.append("logo_url")
        similar_entity.save(update_fields=update_fields)

        # Ensure CanonicalEntity exists
        canonical, created = CanonicalEntity.objects.get_or_create(
            entity=similar_entity,
            defaults={
                'sport': entity_sport,
                'entity_type': entity_type,
                'canonical_name': similar_entity.name,
                'name_variations': [similar_entity.name, name],
                'external_ids': {'statpal': statpal_id}
            }
        )
        if not created:
            if name not in canonical.name_variations:
                canonical.name_variations.append(name)
                canonical.save(update_fields=['name_variations'])
            if canonical.external_ids.get('statpal') != statpal_id:
                canonical.external_ids['statpal'] = statpal_id
                canonical.save(update_fields=['external_ids'])

        logger.info("Linked existing similar entity '%s' to StatPal id=%s (score: %.2f)", similar_entity.name, statpal_id, score)
        return similar_entity

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