# =============================================================================
# FILE 2: apps/event/serializers.py
# REPLACE your full serializers.py with this
#
# What changed:
#   - EventDetailSerializer now includes real statistics, lineups,
#     player stats, timeline, and highlights
#   - EventSerializer stays lean for list views (feed, calendar)
# =============================================================================

from rest_framework import serializers
from apps.event.models import (
    Event, EventTimeline, EventLineup,
    EventStatistics, EventPlayerStats, EventHighlight,
)
from apps.entity.models import Entity


class EntityMinimalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Entity
        fields = ['id', 'name', 'logo_url', 'type', 'sport']


class EventTimelineSerializer(serializers.ModelSerializer):
    team   = EntityMinimalSerializer(read_only=True)
    player = EntityMinimalSerializer(read_only=True)

    class Meta:
        model = EventTimeline
        fields = [
            'id', 'event_type', 'minute', 'extra_minute',
            'team', 'player', 'description',
        ]


class EventLineupSerializer(serializers.ModelSerializer):
    player = EntityMinimalSerializer(read_only=True)
    team   = EntityMinimalSerializer(read_only=True)

    class Meta:
        model = EventLineup
        fields = [
            'id', 'team', 'player', 'position_type',
            'position', 'jersey_number', 'grid_position',
        ]


class EventStatisticsSerializer(serializers.ModelSerializer):
    team = EntityMinimalSerializer(read_only=True)

    class Meta:
        model = EventStatistics
        fields = ['team', 'stats']


class EventPlayerStatsSerializer(serializers.ModelSerializer):
    player = EntityMinimalSerializer(read_only=True)
    team   = EntityMinimalSerializer(read_only=True)

    class Meta:
        model = EventPlayerStats
        fields = ['player', 'team', 'stats', 'points_or_goals']


class EventHighlightSerializer(serializers.ModelSerializer):
    class Meta:
        model = EventHighlight
        fields = ['id', 'title', 'video_url', 'thumbnail_url', 'duration_seconds', 'views']


# ── Lean serializer for list views (feed, calendar, ticker) ──────────────────

class EventSerializer(serializers.ModelSerializer):
    home_entity = EntityMinimalSerializer(read_only=True)
    away_entity = EntityMinimalSerializer(read_only=True)
    league      = EntityMinimalSerializer(read_only=True)

    class Meta:
        model = Event
        fields = [
            'id', 'sport', 'status', 'status_detail',
            'home_entity', 'away_entity', 'league',
            'home_score', 'away_score',
            'start_time', 'venue_name', 'venue_city',
            'broadcaster', 'stream_url',
        ]


# ── Full serializer for event detail screen ───────────────────────────────────

class EventDetailSerializer(serializers.ModelSerializer):
    home_entity  = EntityMinimalSerializer(read_only=True)
    away_entity  = EntityMinimalSerializer(read_only=True)
    league       = EntityMinimalSerializer(read_only=True)

    # Related data
    timeline     = EventTimelineSerializer(many=True, read_only=True)
    lineups      = EventLineupSerializer(many=True, read_only=True)
    statistics   = EventStatisticsSerializer(many=True, read_only=True)
    player_stats = EventPlayerStatsSerializer(many=True, read_only=True)
    highlights   = EventHighlightSerializer(many=True, read_only=True)

    # Computed fields
    has_stats    = serializers.SerializerMethodField()
    has_lineups  = serializers.SerializerMethodField()
    key_players  = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = [
            'id', 'sport', 'status', 'status_detail',
            'home_entity', 'away_entity', 'league',
            'home_score', 'away_score',
            'start_time', 'end_time',
            'venue_name', 'venue_city', 'venue_country',
            'broadcaster', 'stream_url',
            'timeline', 'lineups', 'statistics',
            'player_stats', 'highlights',
            'has_stats', 'has_lineups', 'key_players',
        ]

    def get_has_stats(self, obj):
        return obj.statistics.exists()

    def get_has_lineups(self, obj):
        return obj.lineups.exists()

    def get_key_players(self, obj):
        """
        Top 3 performers by goals/points in this match.
        Shown on the event detail Stats tab as 'Key Player Stats'.
        """
        top = (
            obj.player_stats
            .select_related('player', 'team')
            .order_by('-points_or_goals')[:3]
        )
        result = []
        for ps in top:
            result.append({
                'player_id':     ps.player.id,
                'name':          ps.player.name,
                'photo':         ps.player.logo_url,
                'team':          ps.team.name if ps.team else '',
                'goals':         ps.points_or_goals,
                'rating':        ps.stats.get('rating'),
                'assists':       ps.stats.get('assists', 0),
                'minutes':       ps.stats.get('minutes', 0),
            })
        return result