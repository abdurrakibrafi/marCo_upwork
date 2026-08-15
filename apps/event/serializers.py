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
    stats = serializers.SerializerMethodField()

    class Meta:
        model = EventStatistics
        fields = ['team', 'stats']

    def get_stats(self, obj):
        from apps.event.utils_stats import normalize_event_stats
        return normalize_event_stats(obj.stats)


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

    def to_representation(self, instance):
        data = super().to_representation(instance)
        from django.utils import timezone
        if instance.status == 'upcoming' and instance.start_time and instance.start_time < timezone.now():
            data['status'] = 'completed'
            status_det = str(data.get('status_detail') or '')
            if status_det in ('Not Started', '') or ':' in status_det:
                data['status_detail'] = 'FT'

        # Highlight followed Nest entity logo and ID for calendar entries (agent_task.md Section 14)
        nest_entity_ids = self.context.get('nest_entity_ids')
        if nest_entity_ids:
            if instance.home_entity_id in nest_entity_ids:
                data['nest_entity_id'] = instance.home_entity_id
                data['nest_entity_name'] = instance.home_entity.name if instance.home_entity else ''
                data['nest_entity_logo'] = instance.home_entity.logo_url if instance.home_entity else ''
                data['is_nest_entity_home'] = True
                data['opponent_entity_id'] = instance.away_entity_id
                data['opponent_entity_name'] = instance.away_entity.name if instance.away_entity else ''
                data['opponent_entity_logo'] = instance.away_entity.logo_url if instance.away_entity else ''
            elif instance.away_entity_id in nest_entity_ids:
                data['nest_entity_id'] = instance.away_entity_id
                data['nest_entity_name'] = instance.away_entity.name if instance.away_entity else ''
                data['nest_entity_logo'] = instance.away_entity.logo_url if instance.away_entity else ''
                data['is_nest_entity_home'] = False
                data['opponent_entity_id'] = instance.home_entity_id
                data['opponent_entity_name'] = instance.home_entity.name if instance.home_entity else ''
                data['opponent_entity_logo'] = instance.home_entity.logo_url if instance.home_entity else ''
            else:
                data['nest_entity_id'] = instance.home_entity_id
                data['nest_entity_name'] = instance.home_entity.name if instance.home_entity else ''
                data['nest_entity_logo'] = instance.home_entity.logo_url if instance.home_entity else ''
                data['is_nest_entity_home'] = True
                data['opponent_entity_id'] = instance.away_entity_id
                data['opponent_entity_name'] = instance.away_entity.name if instance.away_entity else ''
                data['opponent_entity_logo'] = instance.away_entity.logo_url if instance.away_entity else ''
        else:
            data['nest_entity_id'] = instance.home_entity_id
            data['nest_entity_name'] = instance.home_entity.name if instance.home_entity else ''
            data['nest_entity_logo'] = instance.home_entity.logo_url if instance.home_entity else ''
            data['is_nest_entity_home'] = True
            data['opponent_entity_id'] = instance.away_entity_id
            data['opponent_entity_name'] = instance.away_entity.name if instance.away_entity else ''
            data['opponent_entity_logo'] = instance.away_entity.logo_url if instance.away_entity else ''

        data['primary_logo_url'] = data.get('nest_entity_logo') or (instance.home_entity.logo_url if instance.home_entity else '')

        # Auto-fill missing venue name/city/country from home team lookup (non-blocking)
        if not data.get('venue_name') or not data.get('venue_city') or not data.get('venue_country'):
            try:
                from apps.entity.utils.matcher import resolve_team_venue_fast
                home_name = instance.home_entity.name if instance.home_entity else ''
                if home_name:
                    v_name, v_city, v_country = resolve_team_venue_fast(home_name)
                    if not data.get('venue_name') and v_name:
                        data['venue_name'] = v_name
                    if not data.get('venue_city') and v_city:
                        data['venue_city'] = v_city
                    if not data.get('venue_country') and v_country:
                        data['venue_country'] = v_country
            except Exception:
                pass

        return data


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
            'metadata',
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        from django.utils import timezone
        if instance.status == 'upcoming' and instance.start_time and instance.start_time < timezone.now():
            data['status'] = 'completed'
            status_det = str(data.get('status_detail') or '')
            if status_det in ('Not Started', '') or ':' in status_det:
                data['status_detail'] = 'FT'

        # Auto-fill missing venue name/city/country from home team lookup (non-blocking)
        if not data.get('venue_name') or not data.get('venue_city') or not data.get('venue_country'):
            try:
                from apps.entity.utils.matcher import resolve_team_venue_fast
                home_name = instance.home_entity.name if instance.home_entity else ''
                if home_name:
                    v_name, v_city, v_country = resolve_team_venue_fast(home_name)
                    if not data.get('venue_name') and v_name:
                        data['venue_name'] = v_name
                    if not data.get('venue_city') and v_city:
                        data['venue_city'] = v_city
                    if not data.get('venue_country') and v_country:
                        data['venue_country'] = v_country
            except Exception:
                pass

        # Exclude empty stats objects (e.g. when API has no stats for a minor league match)
        if data.get('statistics'):
            valid_stats = [s for s in data['statistics'] if s.get('stats') and any(k not in ('side', 'ft_home', 'ft_away') for k in s['stats'].keys())]
            data['statistics'] = valid_stats
            data['has_stats'] = len(valid_stats) > 0
        else:
            data['has_stats'] = False

        return data

    def get_has_stats(self, obj):
        from apps.event.utils_stats import normalize_event_stats
        return any(normalize_event_stats(s.stats) for s in obj.statistics.all() if s.stats)

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