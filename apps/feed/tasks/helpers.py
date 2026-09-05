import re
import logging
from urllib.parse import urlparse
from django.conf import settings
import requests

from apps.entity.models import Entity

logger = logging.getLogger(__name__)


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode common HTML entities from a string."""
    if not text:
        return ''
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Decode common HTML entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ').replace('&quot;', '"').replace('&#39;', "'")
    # Collapse multiple whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _extract_publisher(html: str) -> str:
    """Extract the actual publisher name from Google News RSS HTML.

    Google News encodes the publisher like:
        <font color="#6f6f6f">ESPN</font>
    Returns the publisher name, e.g. 'ESPN', or '' if not found.
    """
    if not html:
        return ''
    match = re.search(r'<font[^>]*color=["\']#6f6f6f["\'][^>]*>([^<]+)</font>', html, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(r'</a>\s*(?:&nbsp;)*\s*([A-Za-z0-9 .\-]+)\s*$', html)
    if match:
        candidate = match.group(1).strip()
        if 2 < len(candidate) < 80:
            return candidate
    return ''


def _extract_domain(url: str) -> str | None:
    """Extract scheme and netloc (e.g., https://espn.com) from a raw URL.

    Args:
        url (str): Target web URL.

    Returns:
        str or None: Extracted domain string or None.
    """
    try:
        parsed = urlparse(url)
        if not parsed.scheme:
            url = f"https://{url}"
            parsed = urlparse(url)
        if not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return None


def _resolve_thumbnail_for_article(title: str, entities: list) -> str:
    """Find and resolve a relevant image thumbnail for an article via Brave Search API.

    Args:
        title (str): News article title.
        entities (list): List of linked Entity instances.

    Returns:
        str: Resolved image URL or empty string.
    """
    brave_key = getattr(settings, 'BRAVESEARCH_KEY', '')
    if brave_key:
        try:
            query_clean = re.sub(r'[^\w\s]', ' ', title).strip()
            url = "https://api.search.brave.com/res/v1/news/search"
            headers = {
                "X-Subscription-Token": brave_key,
                "Accept": "application/json"
            }
            resp = requests.get(url, headers=headers, params={"q": query_clean, "count": 1}, timeout=5)
            if resp.status_code == 200:
                results = resp.json().get('results', [])
                if results:
                    thumb = results[0].get('thumbnail')
                    if isinstance(thumb, dict) and (thumb.get('src') or thumb.get('original')):
                        return thumb.get('src') or thumb.get('original')
                    elif isinstance(thumb, str) and thumb:
                        return thumb
        except Exception as e:
            logger.warning(f"Brave Search thumbnail search failed: {e}")

    return ''


def _entity_matches_text(entity: Entity, text: str) -> bool:
    """Check if a feed article text (title/summary) matches a specific Entity.

    Matches based on exact phrases, common team aliases, and non-generic individual keywords.
    Enforces sports domain keyword filtering for national team entities.

    Args:
        entity (Entity): Sports entity to match against.
        text (str): Combined article title and summary string.

    Returns:
        bool: True if article pertains to the entity, otherwise False.
    """
    from apps.entity.utils.normalizers import normalize_entity_name

    name = normalize_entity_name(entity.name)
    text_norm = normalize_entity_name(text)

    # Enforce sports context for national team entities (e.g. country names)
    from apps.entity.utils.matcher import is_national_team
    sport_clean = (getattr(entity, 'sport', '') or '').strip().lower()
    is_nat_team = is_national_team(entity.name)

    # ── Per-sport cross-contamination guards ────────────────────────────
    if sport_clean == 'soccer':
        other_sports_pattern = re.compile(
            r'\b(cricket|wicket|batsman|batter|bowler|ipl|bcci|bbl|psl|cpl|odi|t20|t20i|test match|century|'
            r'nba|wnba|basketball|slam dunk|three-pointer|triple-double|'
            r'volleyball|vnl|'
            r'baseball|mlb|home run|strikeout|'
            r'tennis|wimbledon|us open|french open|australian open|roland garros|atp|wta|'
            r'formula 1|f1|grand prix)\b', re.I
        )
        soccer_specific_pattern = re.compile(
            r'\b(soccer|football|fifa|uefa|copa|conmebol|concacaf|champions league|world cup|striker|midfield|'
            r'defend|goalkeep|goal|penalty|clean sheet|red card|yellow card|hat-trick|neymar|messi|ronaldo|'
            r'vinicius|mbappe|rodrygo|alisson|ederson|pele|dorival|club|squad|rost|nwsl|premier league|'
            r'la liga|serie a|bundesliga|ligue 1|ligue1|mls)\b', re.I
        )
        if other_sports_pattern.search(text_norm) and not soccer_specific_pattern.search(text_norm):
            return False
        if is_nat_team and not soccer_specific_pattern.search(text_norm):
            return False

    elif sport_clean == 'tennis':
        other_sports_pattern = re.compile(
            r'\b(fifa|uefa|premier league|la liga|serie a|bundesliga|ligue 1|soccer|football goal|'
            r'nba|wnba|basketball|slam dunk|three-pointer|'
            r'cricket|ipl|bcci|wicket|batsman|'
            r'baseball|mlb|home run|'
            r'formula 1|f1|grand prix)\b', re.I
        )
        tennis_specific_pattern = re.compile(
            r'\b(tennis|atp|wta|wimbledon|us open|french open|australian open|roland garros|'
            r'grand slam|set|serve|deuce|ace|forehand|backhand|racket|court|match point|'
            r'djokovic|federer|nadal|sinner|alcaraz|swiatek|sabalenka)\b', re.I
        )
        if other_sports_pattern.search(text_norm) and not tennis_specific_pattern.search(text_norm):
            return False
        if is_nat_team and not tennis_specific_pattern.search(text_norm):
            return False

    elif sport_clean == 'cricket':
        other_sports_pattern = re.compile(
            r'\b(fifa|uefa|premier league|la liga|serie a|bundesliga|ligue 1|mls|el clasico|'
            r'nba|wnba|basketball|slam dunk|three-pointer|'
            r'volleyball|vnl|'
            r'baseball|mlb|home run|'
            r'tennis|wimbledon|atp|wta|'
            r'formula 1|f1)\b', re.I
        )
        cricket_specific_pattern = re.compile(
            r'\b(cricket|icc|bcci|ipl|bpl|psl|cpl|bbl|test match|odi|t20|t20i|'
            r'wicket|batsman|batter|bowler|bowling|batting|innings|century|half-century|pitch|ashes|'
            r'cricinfo|cricbuzz|shakib|kohli|rohit|babar|root)\b', re.I
        )
        if other_sports_pattern.search(text_norm) and not cricket_specific_pattern.search(text_norm):
            return False
        if is_nat_team and not cricket_specific_pattern.search(text_norm):
            return False

    elif sport_clean == 'basketball':
        other_sports_pattern = re.compile(
            r'\b(cricket|wicket|batsman|bowler|ipl|bcci|fifa|uefa|soccer|premier league|tennis|atp|wta)\b', re.I
        )
        basket_specific_pattern = re.compile(
            r'\b(basketball|nba|wnba|fiba|dunk|three-pointer|3-pointer|rebound|assist|triple-double|free throw|playoffs|lebron|curry|giannis)\b', re.I
        )
        if other_sports_pattern.search(text_norm) and not basket_specific_pattern.search(text_norm):
            return False
        if is_nat_team and not basket_specific_pattern.search(text_norm):
            return False

    elif sport_clean in ('american_football', 'football'):
        amfoot_specific_pattern = re.compile(
            r'\b(nfl|touchdown|quarterback|super bowl|american football|gridiron|wide receiver|offensive line|'
            r'defensive end|tight end|running back|field goal|kickoff|punter|mahomes|brady|manning)\b', re.I
        )
        if is_nat_team and not amfoot_specific_pattern.search(text_norm):
            return False

    elif sport_clean in ('ice_hockey', 'hockey'):
        hockey_specific_pattern = re.compile(
            r'\b(hockey|nhl|puck|goalie|power play|penalty shot|hat trick|icing|face-off|slapshot|'
            r'ovechkin|crosby|mcdavid|stanley cup)\b', re.I
        )
        if is_nat_team and not hockey_specific_pattern.search(text_norm):
            return False

    elif sport_clean == 'baseball':
        baseball_specific_pattern = re.compile(
            r'\b(baseball|mlb|home run|strikeout|pitcher|batter|inning|shortstop|outfield|bullpen|'
            r'world series|yankees|dodgers|mets|cubs|braves)\b', re.I
        )
        if is_nat_team and not baseball_specific_pattern.search(text_norm):
            return False

    elif is_nat_team:
        sports_pattern = re.compile(
            r'\b(sport|game|match|play|coach|stadium|cup|tourn|leagu|champ|win|won|lost|lose|beat|defeat|'
            r'scor|goal|team|club|squad|rost|train|seaso|jersey|manag|quali|friend|vs|draw|lineup|transf|'
            r'victo|fan|ref|ump|offic|capt|skip|boss|injur)\w*\b'
        )
        if not sports_pattern.search(text_norm):
            return False

    # 1. Exact phrase/name match
    escaped_name = re.escape(name)
    if re.search(r'\b' + escaped_name + r'\b', text_norm):
        return True

    # 2. Known Aliases / short forms
    aliases_map = {
        'manchester united': {'man united', 'man utd', 'mufc'},
        'manchester city': {'man city', 'mancity', 'mcfc'},
        'real madrid': {'real madrid', 'los blancos'},
        'barcelona': {'barca', 'fc barcelona'},
        'paris saint-germain (psg)': {'psg', 'paris sg', 'paris saint-germain'},
        'paris saint-germain': {'psg', 'paris sg', 'paris saint-germain'},
        'psg': {'psg', 'paris sg', 'paris saint-germain'},
        'inter milan': {'inter', 'internazionale', 'inter milan'},
    }

    aliases = aliases_map.get(name, set())
    for alias in aliases:
        if re.search(r'\b' + re.escape(alias) + r'\b', text_norm):
            return True

    # 3. Individual word matching (only for clubs/athletes, NOT for leagues or national teams)
    if is_nat_team or entity.type == 'league':
        return False

    GENERIC_WORDS = {
        'fc', 'ac', 'sc', 'cf', 'utd', 'united', 'city', 'town', 'county', 'club', 'sports',
        'miami', 'manchester', 'madrid', 'milan', 'london', 'york', 'los', 'angeles', 'boston',
        'chicago', 'houston', 'dallas', 'san', 'diego', 'francisco', 'jose', 'la', 'de', 'deportivo',
        'real', 'atletico', 'athletic', 'sporting', 'racing', 'union', 'saint', 'st', 'germain',
        'inter', 'sheffield', 'west', 'north', 'south', 'east', 'port', 'rovers', 'wanderers',
        'rangers', 'celtic', 'hearts', 'hibernian', 'albion', 'forest', 'villa', 'palace', 'team',
        'division', 'championship', 'cup', 'state', 'green', 'white', 'red', 'blue', 'black',
        'super', 'shield', 'league', 'asian', 'nations', 'primera', 'women', 'womens', 'sudamericano'
    }

    words = [w for w in name.split() if len(w) >= 4]
    for word in words:
        if word not in GENERIC_WORDS:
            if re.search(r'\b' + re.escape(word) + r'\b', text_norm):
                return True

    return False
