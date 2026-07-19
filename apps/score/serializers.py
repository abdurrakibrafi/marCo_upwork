from rest_framework import serializers
from apps.score.models import LiveScore


class LiveScoreSerializer(serializers.ModelSerializer):
    home_logo = serializers.SerializerMethodField()
    away_logo = serializers.SerializerMethodField()

    class Meta:
        model = LiveScore
        fields = [
            'id', 'sport', 'home_team', 'away_team',
            'home_logo', 'away_logo', 'home_score', 'away_score',
            'status', 'status_detail', 'start_time', 'updated_at'
        ]

    def _absolute(self, relative_url):
        if not relative_url:
            return ''
        if relative_url.startswith('http://') or relative_url.startswith('https://'):
            return relative_url
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(relative_url)
        # No request in context (e.g. called from a Celery task / WebSocket
        # publish) -- fall back to BASE_URL from settings/.env, which is
        # environment-specific (local vs VPS).
        try:
            from django.conf import settings
            base = getattr(settings, 'BASE_URL', 'http://localhost:8005').rstrip('/')
            return f'{base}{relative_url}'
        except ImportError:
            return relative_url

    def get_home_logo(self, obj):
        logo = obj.home_logo
        if logo and "statpal.io" in logo:
            from apps.entity.utils.matcher import normalize_statpal_logo_url
            logo = normalize_statpal_logo_url(logo, obj.home_team, "team", obj.sport)
            
        if not logo and hasattr(obj, 'home_team'):
            from apps.entity.utils.matcher import find_team_logo_by_name
            logo = find_team_logo_by_name(obj.home_team)
        return self._absolute(logo)

    def get_away_logo(self, obj):
        logo = obj.away_logo
        if logo and "statpal.io" in logo:
            from apps.entity.utils.matcher import normalize_statpal_logo_url
            logo = normalize_statpal_logo_url(logo, obj.away_team, "team", obj.sport)
            
        if not logo and hasattr(obj, 'away_team'):
            from apps.entity.utils.matcher import find_team_logo_by_name
            logo = find_team_logo_by_name(obj.away_team)
        return self._absolute(logo)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        home_abbr = get_team_abbreviation(instance.home_team)
        away_abbr = get_team_abbreviation(instance.away_team)
        # Collision — use 4 chars to distinguish
        if home_abbr == away_abbr:
            home_abbr = get_team_abbreviation(instance.home_team, length=4)
            away_abbr = get_team_abbreviation(instance.away_team, length=4)
        data['home_team'] = home_abbr
        data['away_team'] = away_abbr
        return data


def get_team_abbreviation(name: str, length: int = 3) -> str:
    if not name:
        return ""
    name_clean = name.strip().lower()
    
    custom_map = {
        'brazil': 'BRA',
        'argentina': 'ARG',
        'bangladesh': 'BAN',
        'sri lanka': 'SRI',
        'sri lanka a': 'SLA',
        'west indies': 'WI',
        'india': 'IND',
        'india a': 'IDA',
        'portugal': 'POR',
        'norway': 'NOR',
        'australia': 'AUS',
        'england': 'ENG',
        'south africa': 'RSA',
        'pakistan': 'PAK',
        'new zealand': 'NZ',
        'afghanistan': 'AFG',
        'zimbabwe': 'ZIM',
        'ireland': 'IRE',
        'scotland': 'SCO',
        'netherlands': 'NED',
        'united states': 'USA',
        'canada': 'CAN',
        'germany': 'GER',
        'spain': 'ESP',
        'france': 'FRA',
        'italy': 'ITA',
        'belgium': 'BEL',
        'croatia': 'CRO',
        'sweden': 'SWE',
        'denmark': 'DEN',
        'uzbekistan': 'UZB',
        'colombia': 'COL',
        'jordan': 'JOR',
        'finland': 'FIN',
    }
    
    if name_clean in custom_map and length <= 3:
        return custom_map[name_clean]

    parts = name.split()
    if len(parts) > 1:
        # Handle "Bangladesh A", "Sri Lanka B" → append suffix letter
        last = parts[-1]
        if len(last) == 1 and last.isalpha():
            base = get_team_abbreviation(' '.join(parts[:-1]), length=length)
            return (base + last.upper())[:length + 1]

        first_word = parts[0].lower()
        if first_word in ('fc', 'ac', 'real', '1.', 'de'):
            # e.g. "Real Madrid" → take letters from each part
            abbr = "".join([p[:2] for p in parts[1:] if p]).upper()
            return abbr[:length]
        else:
            # e.g. "Band-e-Amir Dragons" length=3 → "BAD", length=4 → "BAND"
            # Take more chars from first word when length > initials count
            initials = "".join([p[0] for p in parts if p]).upper()
            if len(initials) >= length:
                return initials[:length]
            # Not enough words — pad from first word
            first = parts[0].upper()
            return (first + initials[1:])[:length]

    return name[:length].upper()
