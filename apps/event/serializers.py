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
    """Minimal entity serializer for lightweight embedding within event participant responses."""
    logo_url = serializers.SerializerMethodField()

    class Meta:
        model = Entity
        fields = ['id', 'name', 'logo_url', 'type', 'sport']

    def get_logo_url(self, obj):
        if not obj:
            return ''
        try:
            from apps.entity.serializers import find_entity_logo, make_logo_url_absolute
            logo = find_entity_logo(obj)
            return make_logo_url_absolute(logo, self.context.get('request'))
        except Exception:
            return getattr(obj, 'logo_url', '') or ''


class EventTimelineSerializer(serializers.ModelSerializer):
    """Serializer for in-game timeline incidents (goals, cards, substitutions)."""
    team   = EntityMinimalSerializer(read_only=True)
    player = EntityMinimalSerializer(read_only=True)

    class Meta:
        model = EventTimeline
        fields = [
            'id', 'event_type', 'minute', 'extra_minute',
            'team', 'player', 'description',
        ]


class EventLineupSerializer(serializers.ModelSerializer):
    """Serializer for starting XI, substitute bench, and formation positions."""
    player = EntityMinimalSerializer(read_only=True)
    team   = EntityMinimalSerializer(read_only=True)

    class Meta:
        model = EventLineup
        fields = [
            'id', 'team', 'player', 'position_type',
            'position', 'jersey_number', 'grid_position',
        ]


class EventStatisticsSerializer(serializers.ModelSerializer):
    """Serializer for aggregated team match performance statistics with schema normalization."""
    team = EntityMinimalSerializer(read_only=True)
    stats = serializers.SerializerMethodField()

    class Meta:
        model = EventStatistics
        fields = ['team', 'stats']

    def get_stats(self, obj):
        """Retrieve and normalize match statistics according to the unified metric schema.

        Args:
            obj (EventStatistics): EventStatistics instance.

        Returns:
            dict: Normalized team stats mapping.
        """
        try:
            from apps.event.utils_stats import normalize_event_stats
            sport = getattr(obj.event, 'sport', None) or (getattr(obj.team, 'sport', None) if obj.team else None)
            return normalize_event_stats(obj.stats, sport=sport, event=getattr(obj, 'event', None))
        except Exception:
            return obj.stats if isinstance(obj.stats, dict) else {}


class EventPlayerStatsSerializer(serializers.ModelSerializer):
    """Serializer for individual player match metrics and scores."""
    player = EntityMinimalSerializer(read_only=True)
    team   = EntityMinimalSerializer(read_only=True)

    class Meta:
        model = EventPlayerStats
        fields = ['player', 'team', 'stats', 'points_or_goals']


class EventHighlightSerializer(serializers.ModelSerializer):
    """Serializer for video highlights and replay clips."""
    class Meta:
        model = EventHighlight
        fields = ['id', 'title', 'video_url', 'thumbnail_url', 'duration_seconds', 'views']


def _extract_event_venue_info(instance, data) -> dict:
    """Extract venue_name, venue_city, venue_country with metadata and home-team fallbacks.

    Args:
        instance (Event): Event instance.
        data (dict): Serialized event dictionary.

    Returns:
        dict: Updated data dictionary with guaranteed venue attributes.
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
    """Lean sporting fixture serializer designed for fast list rendering across feeds, calendars, and tickers."""
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
        """Format serialized event dictionary with followed nest highlights and venue enrichment.

        Args:
            instance (Event): Event instance.

        Returns:
            dict: Serialized event dictionary representation.
        """
        data = super().to_representation(instance)
        from django.utils import timezone
        if instance.status == 'upcoming' and instance.start_time and instance.start_time < timezone.now():
            data['status'] = 'completed'
            status_det = str(data.get('status_detail') or '')
            if status_det in ('Not Started', '') or ':' in status_det:
                data['status_detail'] = 'FT'

        # Timezone conversion for local_date, local_time, and status_detail
        user_tz = self.context.get('timezone')
        if not user_tz and self.context.get('request'):
            from apps.event.views import _resolve_timezone
            user_tz = _resolve_timezone(self.context.get('request'))

        if user_tz and instance.start_time:
            local_dt = instance.start_time.astimezone(user_tz)
            data['local_date'] = local_dt.date().isoformat()
            data['local_time'] = local_dt.strftime('%H:%M')
            if data.get('status') == 'upcoming' and (not data.get('status_detail') or ':' in str(data.get('status_detail'))):
                data['status_detail'] = local_dt.strftime('%H:%M')

        from apps.score.services import format_sport_status_detail
        meta = instance.metadata if isinstance(instance.metadata, dict) else {}
        data['status_detail'] = format_sport_status_detail(
            sport=instance.sport,
            status=data.get('status', instance.status),
            status_detail=data.get('status_detail', instance.status_detail),
            home_score=instance.home_score,
            away_score=instance.away_score,
            raw_data=meta,
            game=instance
        )

        # Highlight followed Nest entity logo and ID for calendar entries (agent_task.md Section 14)
        from apps.entity.serializers import find_entity_logo, make_logo_url_absolute
        req_ctx = self.context.get('request')
        nest_entity_ids = self.context.get('nest_entity_ids')
        if nest_entity_ids:
            if instance.home_entity_id in nest_entity_ids:
                data['nest_entity_id'] = instance.home_entity_id
                data['nest_entity_name'] = instance.home_entity.name if instance.home_entity else ''
                data['nest_entity_logo'] = make_logo_url_absolute(find_entity_logo(instance.home_entity), req_ctx)
                data['is_nest_entity_home'] = True
                data['opponent_entity_id'] = instance.away_entity_id
                data['opponent_entity_name'] = instance.away_entity.name if instance.away_entity else ''
                data['opponent_entity_logo'] = make_logo_url_absolute(find_entity_logo(instance.away_entity), req_ctx)
            elif instance.away_entity_id in nest_entity_ids:
                data['nest_entity_id'] = instance.away_entity_id
                data['nest_entity_name'] = instance.away_entity.name if instance.away_entity else ''
                data['nest_entity_logo'] = make_logo_url_absolute(find_entity_logo(instance.away_entity), req_ctx)
                data['is_nest_entity_home'] = False
                data['opponent_entity_id'] = instance.home_entity_id
                data['opponent_entity_name'] = instance.home_entity.name if instance.home_entity else ''
                data['opponent_entity_logo'] = make_logo_url_absolute(find_entity_logo(instance.home_entity), req_ctx)
            else:
                data['nest_entity_id'] = instance.home_entity_id
                data['nest_entity_name'] = instance.home_entity.name if instance.home_entity else ''
                data['nest_entity_logo'] = make_logo_url_absolute(find_entity_logo(instance.home_entity), req_ctx)
                data['is_nest_entity_home'] = True
                data['opponent_entity_id'] = instance.away_entity_id
                data['opponent_entity_name'] = instance.away_entity.name if instance.away_entity else ''
                data['opponent_entity_logo'] = make_logo_url_absolute(find_entity_logo(instance.away_entity), req_ctx)
        else:
            data['nest_entity_id'] = instance.home_entity_id
            data['nest_entity_name'] = instance.home_entity.name if instance.home_entity else ''
            data['nest_entity_logo'] = make_logo_url_absolute(find_entity_logo(instance.home_entity), req_ctx)
            data['is_nest_entity_home'] = True
            data['opponent_entity_id'] = instance.away_entity_id
            data['opponent_entity_name'] = instance.away_entity.name if instance.away_entity else ''
            data['opponent_entity_logo'] = make_logo_url_absolute(find_entity_logo(instance.away_entity), req_ctx)

        # Prevent displaying cross-sport mismatched league (e.g., horse_racing event with baseball league)
        if instance.league and instance.league.sport and instance.sport:
            ev_sport = instance.sport.lower()
            lg_sport = instance.league.sport.lower()
            if ev_sport != lg_sport and not (ev_sport in ('baseball', 'mlb') and lg_sport in ('baseball', 'mlb')) and not (ev_sport in ('basketball', 'nba') and lg_sport in ('basketball', 'nba')):
                data['league'] = None

        data['primary_logo_url'] = data.get('nest_entity_logo') or make_logo_url_absolute(find_entity_logo(instance.home_entity), req_ctx) or (make_logo_url_absolute(find_entity_logo(instance.league), req_ctx) if data.get('league') else '') or ''

        # Auto-fill missing venue name/city/country from metadata / home team lookup
        data = _extract_event_venue_info(instance, data)
        return data


# ── Full serializer for event detail screen ───────────────────────────────────

class EventDetailSerializer(serializers.ModelSerializer):
    """Detailed event match serializer including lineups, timeline, statistics, and top key players."""
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
        """Format detailed event dictionary with real-time statistics availability and venue metadata.

        Args:
            instance (Event): Event instance.

        Returns:
            dict: Serialized event detail dictionary.
        """
        try:
            data = super().to_representation(instance)
        except Exception:
            from apps.event.serializers import EventSerializer
            return EventSerializer(instance, context=self.context).data

        try:
            from django.utils import timezone
            if instance.status == 'upcoming' and instance.start_time and instance.start_time < timezone.now():
                data['status'] = 'completed'
                status_det = str(data.get('status_detail') or '')
                if status_det in ('Not Started', '') or ':' in status_det:
                    data['status_detail'] = 'FT'

            # Timezone conversion for local_date, local_time, and status_detail
            user_tz = self.context.get('timezone')
            if not user_tz and self.context.get('request'):
                from apps.event.views import _resolve_timezone
                user_tz = _resolve_timezone(self.context.get('request'))

            if user_tz and instance.start_time:
                local_dt = instance.start_time.astimezone(user_tz)
                data['local_date'] = local_dt.date().isoformat()
                data['local_time'] = local_dt.strftime('%H:%M')
                if data.get('status') == 'upcoming' and (not data.get('status_detail') or ':' in str(data.get('status_detail'))):
                    data['status_detail'] = local_dt.strftime('%H:%M')

            from apps.score.services import format_sport_status_detail
            meta = instance.metadata if isinstance(instance.metadata, dict) else {}
            data['status_detail'] = format_sport_status_detail(
                sport=instance.sport,
                status=data.get('status', instance.status),
                status_detail=data.get('status_detail', instance.status_detail),
                home_score=instance.home_score,
                away_score=instance.away_score,
                raw_data=meta,
                game=instance
            )

            # Prevent displaying cross-sport mismatched league (e.g., horse_racing event with baseball league)
            if instance.league and instance.league.sport and instance.sport:
                ev_sport = instance.sport.lower()
                lg_sport = instance.league.sport.lower()
                if ev_sport != lg_sport and not (ev_sport in ('baseball', 'mlb') and lg_sport in ('baseball', 'mlb')) and not (ev_sport in ('basketball', 'nba') and lg_sport in ('basketball', 'nba')):
                    data['league'] = None

            # Auto-fill missing venue name/city/country from metadata / home team lookup
            data = _extract_event_venue_info(instance, data)

            from apps.entity.serializers import find_entity_logo, make_logo_url_absolute
            req_ctx = self.context.get('request')
            data['primary_logo_url'] = make_logo_url_absolute(find_entity_logo(instance.home_entity), req_ctx) or (make_logo_url_absolute(find_entity_logo(instance.league), req_ctx) if data.get('league') else '') or ''

            # Exclude empty stats objects (e.g. when API has no stats for a minor league match)
            if data.get('statistics'):
                valid_stats = [
                    s for s in data['statistics']
                    if isinstance(s, dict) and isinstance(s.get('stats'), dict) and any(k not in ('side', 'ft_home', 'ft_away') for k in s['stats'].keys())
                ]
                data['statistics'] = valid_stats
                data['has_stats'] = len(valid_stats) > 0
            else:
                data['has_stats'] = False

            # Normalize baseball metadata in1..in9 flat keys from nested innings.inning list
            # StatPal API sends in1..in9 as empty strings; real data is in innings.inning list
            # This fixes existing events without needing a re-sync
            if isinstance(data.get('metadata'), dict) and instance.sport in ('baseball', 'mlb'):
                for side in ('home', 'away'):
                    side_data = data['metadata'].get(side)
                    if not isinstance(side_data, dict):
                        continue
                    flat_keys_empty = all(side_data.get(f'in{i}', '') == '' for i in range(1, 10))
                    if flat_keys_empty:
                        nested = side_data.get('innings', {})
                        inning_list = nested.get('inning', []) if isinstance(nested, dict) else []
                        if isinstance(inning_list, dict):
                            inning_list = [inning_list]
                        for inn in inning_list:
                            num = inn.get('number')
                            score = inn.get('score')
                            if num and score is not None:
                                key = f'in{num}'
                                if key in side_data:
                                    data['metadata'][side][key] = score
        except Exception:
            pass

        return data

    def get_has_stats(self, obj):
        """Determine if valid normalized match statistics are available.

        Args:
            obj (Event): Event instance.

        Returns:
            bool: True if event has statistics.
        """
        try:
            from apps.event.utils_stats import normalize_event_stats
            return any(normalize_event_stats(s.stats, sport=obj.sport, event=obj) for s in obj.statistics.all() if s.stats)
        except Exception:
            return False

    def get_has_lineups(self, obj):
        """Check if starting lineups have been submitted for this event.

        Args:
            obj (Event): Event instance.

        Returns:
            bool: True if lineups exist.
        """
        try:
            return obj.lineups.exists()
        except Exception:
            return False

    def get_key_players(self, obj):
        """Extract top 3 player performers by goals/points in this match.

        Args:
            obj (Event): Event instance.

        Returns:
            list: Top performing player statistics dictionaries.
        """
        top = (
            obj.player_stats
            .select_related('player', 'team')
            .order_by('-points_or_goals')[:3]
        )
        result = []
        for ps in top:
            if not ps.player:
                continue
            ps_stats = ps.stats if isinstance(ps.stats, dict) else {}
            result.append({
                'player_id':     ps.player.id,
                'name':          ps.player.name,
                'photo':         ps.player.logo_url or '',
                'team':          ps.team.name if ps.team else '',
                'goals':         ps.points_or_goals,
                'rating':        ps_stats.get('rating'),
                'assists':       ps_stats.get('assists', 0),
                'minutes':       ps_stats.get('minutes', 0),
            })
        return result