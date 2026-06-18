# apps/core/utils/image_helpers.py
from django.conf import settings

def get_entity_logo(entity):
    """
    Returns StatPal logo if exists, else fallback to TheSportsDB, 
    else returns a generic default image to prevent app crashes.
    """
    if entity.logo_url:
        return entity.logo_url
    
    # logic to fetch from TheSportsDB could go here if needed 
    # but for now, we return a safe default URL
    return "https://mysportsnest.com/static/images/default-team.png"