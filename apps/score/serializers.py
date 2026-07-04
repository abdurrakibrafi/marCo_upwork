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
