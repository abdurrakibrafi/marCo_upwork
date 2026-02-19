from rest_framework import serializers
from apps.event.models import (
    Event, EventTimeline, EventLineup, 
    EventStatistics, EventPlayerStats, EventHighlight
)
from apps.entity.serializers import EntitySerializer

class EventSerializer(serializers.ModelSerializer):
    """Basic event serializer"""
    
    home_entity = EntitySerializer()
    away_entity = EntitySerializer()
    league = EntitySerializer()
    
    class Meta:
        model = Event
        fields = [
            'id', 'sport', 'home_entity', 'away_entity', 'league',
            'start_time', 'end_time', 'status', 'status_detail',
            'home_score', 'away_score', 'venue_name', 'venue_city',
            'broadcaster', 'updated_at'
        ]


class EventTimelineSerializer(serializers.ModelSerializer):
    """Event timeline serializer"""
    
    team = EntitySerializer()
    player = EntitySerializer()
    
    class Meta:
        model = EventTimeline
        fields = [
            'id', 'event_type', 'minute', 'extra_minute',
            'team', 'player', 'description', 'metadata'
        ]


class EventLineupSerializer(serializers.ModelSerializer):
    """Lineup serializer"""
    
    player = EntitySerializer()
    
    class Meta:
        model = EventLineup
        fields = [
            'player', 'position_type', 'position',
            'jersey_number', 'grid_position'
        ]


class EventStatisticsSerializer(serializers.ModelSerializer):
    """Team statistics serializer"""
    
    team = EntitySerializer()
    
    class Meta:
        model = EventStatistics
        fields = ['team', 'stats', 'updated_at']


class EventPlayerStatsSerializer(serializers.ModelSerializer):
    """Player statistics serializer"""
    
    player = EntitySerializer()
    
    class Meta:
        model = EventPlayerStats
        fields = ['player', 'stats', 'points_or_goals']


class EventHighlightSerializer(serializers.ModelSerializer):
    """Highlight serializer"""
    
    class Meta:
        model = EventHighlight
        fields = [
            'id', 'title', 'description', 'video_url',
            'thumbnail_url', 'duration_seconds', 'source',
            'external_id', 'views'
        ]


class EventDetailSerializer(serializers.ModelSerializer):
    """Detailed event serializer with all related data"""
    
    home_entity = EntitySerializer()
    away_entity = EntitySerializer()
    league = EntitySerializer()
    timeline = EventTimelineSerializer(many=True)
    lineups = EventLineupSerializer(many=True)
    statistics = EventStatisticsSerializer(many=True)
    player_stats = EventPlayerStatsSerializer(many=True)
    highlights = EventHighlightSerializer(many=True)
    
    class Meta:
        model = Event
        fields = [
            'id', 'sport', 'home_entity', 'away_entity', 'league',
            'start_time', 'end_time', 'status', 'status_detail',
            'home_score', 'away_score', 
            'venue_name', 'venue_city', 'venue_country',
            'broadcaster', 'stream_url',
            'timeline', 'lineups', 'statistics', 
            'player_stats', 'highlights',
            'metadata', 'updated_at'
        ]