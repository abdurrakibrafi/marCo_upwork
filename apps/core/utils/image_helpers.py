# apps/core/utils/image_helpers.py
from django.conf import settings

def get_entity_logo(entity):
    """Retrieve the logo URL for an entity or provide a safe default fallback.

    Returns the logo URL if present on the entity instance, otherwise falls back
    to a default placeholder image URL to prevent frontend crashes.

    Args:
        entity (Entity): The entity model instance (team, league, athlete, etc.).

    Returns:
        str: Absolute URL to the entity's logo or fallback image.
    """
    if entity.logo_url:
        return entity.logo_url
    
    # logic to fetch from TheSportsDB could go here if needed 
    # but for now, we return a safe default URL
    return "https://mysportsnest.com/static/images/default-team.png"