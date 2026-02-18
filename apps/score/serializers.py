from rest_framework import serializers
from apps.score.models import LiveScore

class LiveScoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = LiveScore
        fields = [
            'id', 'sport', 'home_team', 'away_team',
            'home_logo', 'away_logo', 'home_score', 'away_score',
            'status', 'status_detail', 'start_time', 'updated_at'
        ]