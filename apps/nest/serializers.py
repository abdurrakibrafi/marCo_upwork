from rest_framework import serializers
from apps.nest.models import UserNest, UserPreferences, RecentSearch
from apps.entity.serializers import EntitySerializer

class UserNestSerializer(serializers.ModelSerializer):
    """User's nest serializer"""
    
    entity = EntitySerializer()
    
    class Meta:
        model = UserNest
        fields = [
            'id', 'entity', 'position', 'notify_on_games',
            'notify_on_news', 'added_at'
        ]


class AddToNestSerializer(serializers.Serializer):
    """Serializer for adding entities to nest"""
    
    entity_id = serializers.IntegerField()
    
    def validate_entity_id(self, value):
        from apps.entity.models import Entity
        try:
            Entity.objects.get(id=value)
        except Entity.DoesNotExist:
            raise serializers.ValidationError("Entity not found")
        return value


class UserPreferencesSerializer(serializers.ModelSerializer):
    """User preferences serializer"""
    
    class Meta:
        model = UserPreferences
        fields = [
            'show_live_scores', 'breaking_news_only',
            'breaking_news_notifications', 'game_start_notifications',
            'sources_limit', 'sources_used'
        ]


class RecentSearchSerializer(serializers.ModelSerializer):
    """Recent search serializer"""
    
    entity = EntitySerializer()
    
    class Meta:
        model = RecentSearch
        fields = ['id', 'query', 'entity', 'searched_at']