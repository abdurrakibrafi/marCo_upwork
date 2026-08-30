from rest_framework import serializers
from apps.entity.models import Entity, Team, Athlete, League, EntityStats


def make_logo_url_absolute(url, request=None):
    """Convert relative logo URLs into absolute URLs using request context or settings.BASE_URL.

    Args:
        url (str): Image URL or relative path.
        request (Request, optional): HTTP request context.

    Returns:
        str: Absolute URL to the logo image.
    """
    if not url:
        return ''
    if url.startswith('http://') or url.startswith('https://'):
        return url
    if request:
        return request.build_absolute_uri(url)
    try:
        from django.conf import settings
        base = getattr(settings, 'BASE_URL', 'http://localhost:8005').rstrip('/')
        return f'{base}{url}'
    except Exception:
        return url


def find_entity_logo(entity):
    """Resolve and backfill entity logo or athlete headshot URL."""
    if not entity:
        return ""
    logo = entity.logo_url
    is_invalid_logo = logo and "statpal.io" in logo
    if logo and not is_invalid_logo:
        return logo

    # 1. If team, search team logo by name
    if entity.type == 'team':
        from apps.entity.utils.matcher import find_team_logo_by_name
        return find_team_logo_by_name(entity.name)

    # 2. If athlete:
    if entity.type == 'athlete':
        from apps.entity.models import Entity
        # 2a. Check if another entity instance with same name has logo_url
        alt_logo = Entity.objects.filter(
            name__iexact=entity.name,
            type='athlete'
        ).exclude(logo_url="").values_list("logo_url", flat=True).first()
        if alt_logo and "statpal.io" not in alt_logo:
            return alt_logo

        # 2b. Check cover_image_url
        if entity.cover_image_url and "statpal.io" not in entity.cover_image_url:
            return entity.cover_image_url

        # 2c. Check athlete_details -> current_team logo
        ad = getattr(entity, 'athlete_details', None)
        if ad and ad.current_team:
            team_logo = ad.current_team.logo_url
            if team_logo and "statpal.io" not in team_logo:
                return team_logo
            from apps.entity.utils.matcher import find_team_logo_by_name
            t_logo = find_team_logo_by_name(ad.current_team.name)
            if t_logo:
                return t_logo

        # 2d. TheSportsDB live headshot lookup fallback
        try:
            from django.core.cache import cache
            cache_key = f"athlete_headshot_{entity.name.lower().strip().replace(' ', '_')}"
            cached_headshot = cache.get(cache_key)
            if cached_headshot:
                return cached_headshot
            from apps.sports_apis.services.thesportsdb import thesportsdb_service
            p_info = thesportsdb_service.get_player_details(entity.name) or {}
            headshot = p_info.get('headshot_url') or p_info.get('thumb_url') or ''
            if headshot:
                cache.set(cache_key, headshot, timeout=604800)
                entity.logo_url = headshot
                entity.save(update_fields=['logo_url'])
                return headshot
        except Exception:
            pass

    # 3. If league, check other league entities or cover_image_url
    if entity.type == 'league':
        from apps.entity.models import Entity
        alt_logo = Entity.objects.filter(
            name__iexact=entity.name,
            type='league'
        ).exclude(logo_url="").values_list("logo_url", flat=True).first()
        if alt_logo and "statpal.io" not in alt_logo:
            return alt_logo
        if entity.cover_image_url and "statpal.io" not in entity.cover_image_url:
            return entity.cover_image_url

    return ""


class EntityCompactSerializer(serializers.ModelSerializer):
    """Minimal entity serializer for lightweight embedding in nested responses."""
    
    logo_url = serializers.SerializerMethodField()
    
    class Meta:
        model = Entity
        fields = ['id', 'type', 'name', 'slug', 'sport', 'logo_url']

    def get_logo_url(self, obj):
        """Retrieve cleaned absolute logo URL for the entity.

        Args:
            obj (Entity): Entity instance.

        Returns:
            str: Resolved absolute logo URL.
        """
        logo = find_entity_logo(obj)
        return make_logo_url_absolute(logo, self.context.get('request'))


class EntitySerializer(serializers.ModelSerializer):
    """Primary entity serializer with following/nest status and country backfills."""
    
    in_nest = serializers.SerializerMethodField()
    logo_url = serializers.SerializerMethodField()
    
    class Meta:
        model = Entity
        fields = [
            'id', 'type', 'name', 'slug', 'sport',
            'logo_url', 'cover_image_url', 'description',
            'country', 'follower_count', 'has_api_data',
            'in_nest', 'created_at'
        ]
    
    def get_in_nest(self, obj):
        """Determine if the requesting user has added this entity to their nest.

        Args:
            obj (Entity): Entity instance.

        Returns:
            bool: True if entity is in the user's nest.
        """
        user_nest_entity_ids = self.context.get('user_nest_entity_ids')
        if user_nest_entity_ids is not None:
            return obj.id in user_nest_entity_ids
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if request and user and user.is_authenticated:
            return obj.usernest_set.filter(user=user).exists()
        return False

    def get_logo_url(self, obj):
        """Resolve and format absolute logo URL.

        Args:
            obj (Entity): Entity instance.

        Returns:
            str: Resolved absolute logo URL.
        """
        logo = find_entity_logo(obj)
        return make_logo_url_absolute(logo, self.context.get('request'))

    def to_representation(self, instance):
        """Backfill country representation from nationality or current team if missing.

        Args:
            instance (Entity): Entity instance.

        Returns:
            dict: Serialized dictionary data.
        """
        ret = super().to_representation(instance)
        if not ret.get('country') and instance.type == 'athlete':
            ad = getattr(instance, 'athlete_details', None)
            if ad:
                if ad.nationality:
                    ret['country'] = ad.nationality
                elif ad.current_team and ad.current_team.country:
                    ret['country'] = ad.current_team.country
        return ret


class TeamDetailSerializer(serializers.ModelSerializer):
    """Detailed team serializer with venue, record, and social media links."""
    
    entity = EntitySerializer()
    league = EntitySerializer()
    
    class Meta:
        model = Team
        fields = [
            'entity', 'league', 'venue_name', 'venue_city',
            'venue_capacity', 'total_wins', 'total_losses',
            'win_percentage', 'website_url', 'twitter_handle',
            'youtube_channel_id'
        ]


class AthleteDetailSerializer(serializers.ModelSerializer):
    """Detailed athlete serializer with personal, physical, and contract information."""
    
    entity = EntitySerializer()
    current_team = EntitySerializer()
    age = serializers.ReadOnlyField()
    
    class Meta:
        model = Athlete
        fields = [
            'entity', 'first_name', 'last_name', 'date_of_birth',
            'age', 'nationality', 'height_cm', 'weight_kg',
            'current_team', 'position', 'jersey_number',
            'salary_usd', 'contract_years_remaining',
            'twitter_handle', 'instagram_handle'
        ]


class LeagueDetailSerializer(serializers.ModelSerializer):
    """Detailed league serializer with competition formats and team count."""
    
    entity = EntitySerializer()
    
    class Meta:
        model = League
        fields = [
            'entity', 'current_season', 'number_of_teams',
            'has_playoffs', 'has_divisions'
        ]


class EntityStatsSerializer(serializers.ModelSerializer):
    """Serializer for cached entity season and game performance statistics."""
    
    class Meta:
        model = EntityStats
        fields = ['season', 'stat_type', 'stats_data', 'updated_at']