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
        return self._absolute(obj.home_logo)

    def get_away_logo(self, obj):
        return self._absolute(obj.away_logo)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['home_team'] = get_team_abbreviation(instance.home_team)
        data['away_team'] = get_team_abbreviation(instance.away_team)
        return data


def get_team_abbreviation(name: str) -> str:
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
    
    if name_clean in custom_map:
        return custom_map[name_clean]
        
    parts = name.split()
    if len(parts) > 1:
        first_word = parts[0].lower()
        if first_word in ('fc', 'ac', 'real', '1.', 'de'):
            abbr = "".join([p[0] for p in parts if p]).upper()
            return abbr[:3]
        else:
            abbr = "".join([p[0] for p in parts if p]).upper()
            if len(abbr) >= 2:
                return abbr[:3]
                
    return name[:3].upper()
