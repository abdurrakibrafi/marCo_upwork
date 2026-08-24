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
        logo = obj.logo_url
        is_invalid_logo = logo and "statpal.io" in logo
        if (not logo or is_invalid_logo) and obj.type == 'team':
            from apps.entity.utils.matcher import find_team_logo_by_name
            logo = find_team_logo_by_name(obj.name)
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
        logo = obj.logo_url
        is_invalid_logo = logo and "statpal.io" in logo
        if (not logo or is_invalid_logo) and obj.type == 'team':
            from apps.entity.utils.matcher import find_team_logo_by_name
            logo = find_team_logo_by_name(obj.name)
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