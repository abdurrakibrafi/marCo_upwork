from rest_framework import serializers
from apps.entity.models import Entity, Team, Athlete, League, EntityStats


class EntityCompactSerializer(serializers.ModelSerializer):
    """Minimal entity serializer for nested responses"""
    
    class Meta:
        model = Entity
        fields = ['id', 'type', 'name', 'slug', 'sport', 'logo_url']


class EntitySerializer(serializers.ModelSerializer):
    """Basic entity serializer"""
    
    in_nest = serializers.SerializerMethodField()
    
    class Meta:
        model = Entity
        fields = [
            'id', 'type', 'name', 'slug', 'sport',
            'logo_url', 'cover_image_url', 'description',
            'country', 'follower_count', 'has_api_data',
            'in_nest', 'created_at'
        ]
    
    def get_in_nest(self, obj):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if request and user and user.is_authenticated:
            return obj.usernest_set.filter(user=user).exists()
        return False


class TeamDetailSerializer(serializers.ModelSerializer):
    """Detailed team serializer"""
    
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
    """Detailed athlete serializer"""
    
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
    """Detailed league serializer"""
    
    entity = EntitySerializer()
    
    class Meta:
        model = League
        fields = [
            'entity', 'current_season', 'number_of_teams',
            'has_playoffs', 'has_divisions'
        ]


class EntityStatsSerializer(serializers.ModelSerializer):
    """Entity statistics serializer"""
    
    class Meta:
        model = EntityStats
        fields = ['season', 'stat_type', 'stats_data', 'updated_at']