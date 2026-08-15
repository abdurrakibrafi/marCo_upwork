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


def _extract_event_venue_info(instance, data):
    """
    Extract venue_name, venue_city, venue_country with comprehensive fallbacks:
    1. Direct fields in data / instance (venue_name, venue_city, venue_country)
    2. Event metadata (e.g. cricket matchinfo.info, soccer match_info, venue, stadium, etc.)
    3. Home team venue resolution from local DB / TheSportsDB
    """
    v_name = data.get('venue_name') or getattr(instance, 'venue_name', '') or ''
    v_city = data.get('venue_city') or getattr(instance, 'venue_city', '') or ''
    v_country = data.get('venue_country') or getattr(instance, 'venue_country', '') or ''

    # 1. Check metadata (e.g. StatPal Cricket matchinfo.info, Soccer, Golf, etc.)
    meta = getattr(instance, 'metadata', None) or {}
    if isinstance(meta, dict):
        # Cricket matchinfo.info list
        matchinfo = meta.get('matchinfo', {})
        if isinstance(matchinfo, dict):
            info_list = matchinfo.get('info', [])
            if isinstance(info_list, dict):
                info_list = [info_list]
            elif not isinstance(info_list, list):
                info_list = []
            for item in info_list:
                if not isinstance(item, dict):
                    continue
                item_name = str(item.get('name', '')).strip().lower()
                item_val = str(item.get('value', '')).strip()
                if item_val:
                    if item_name in ('venue', 'stadium') and not v_name:
                        v_name = item_val
                    elif item_name in ('city', 'location') and not v_city:
                        v_city = item_val
                    elif item_name in ('country', 'nation') and not v_country:
                        v_country = item_val

        # Soccer / other match_info
        match_info = meta.get('match_info', {})
        if isinstance(match_info, dict):
            stadium_obj = match_info.get('stadium', {})
            if isinstance(stadium_obj, dict):
                stad_name = stadium_obj.get('name', '').strip()
                if stad_name and not v_name:
                    v_name = stad_name

        # Generic top-level metadata keys
        if not v_name:
            v_name = meta.get('venue') or meta.get('stadium') or meta.get('venue_name') or ''
        if not v_city:
            v_city = meta.get('city') or meta.get('location') or meta.get('venue_city') or ''
        if not v_country:
            v_country = meta.get('country') or meta.get('venue_country') or ''

    # 2. Fallback: Fast home team venue resolution from DB / TheSportsDB
    if not v_name or not v_city or not v_country:
        try:
            from apps.entity.utils.matcher import resolve_team_venue_fast
            home_name = instance.home_entity.name if instance.home_entity else ''
            if home_name:
                auto_name, auto_city, auto_country = resolve_team_venue_fast(home_name)
                if not v_name and auto_name:
                    v_name = auto_name
                if not v_city and auto_city:
                    v_city = auto_city
                if not v_country and auto_country:
                    v_country = auto_country
        except Exception:
            pass

    # 3. If v_name contains "Stadium, City" and city is empty, parse it cleanly
    if v_name and ',' in v_name and not v_city:
        parts = [p.strip() for p in v_name.split(',') if p.strip()]
        if len(parts) >= 2:
            v_city = parts[-1]

    data['venue_name'] = str(v_name).strip()
    data['venue_city'] = str(v_city).strip()
    data['venue_country'] = str(v_country).strip()
    return data


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
            'start_time', 'venue_name', 'venue_city', 'venue_country',
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

        # Auto-fill missing venue name/city/country from metadata / home team lookup
        data = _extract_event_venue_info(instance, data)
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

        # Auto-fill missing venue name/city/country from metadata / home team lookup
        data = _extract_event_venue_info(instance, data)

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