from rest_framework import serializers
from apps.nest.models import UserNest, UserPreferences, RecentSearch
from apps.entity.serializers import EntitySerializer

class UserNestSerializer(serializers.ModelSerializer):
    """Serializer for user nest entries with nested Entity details."""
    
    entity = EntitySerializer()
    
    class Meta:
        model = UserNest
        fields = [
            'id', 'entity', 'position', 'notify_on_games',
            'notify_on_news', 'added_at'
        ]


class AddToNestSerializer(serializers.Serializer):
    """Validation serializer for adding an entity to the authenticated user's Nest."""
    
    entity_id = serializers.IntegerField()
    
    def validate_entity_id(self, value):
        """Validate that target entity exists and is active."""
        from apps.entity.models import Entity
        try:
            Entity.objects.get(id=value)
        except Entity.DoesNotExist:
            raise serializers.ValidationError("Entity not found")
        return value


class UserPreferencesSerializer(serializers.ModelSerializer):
    """Serializer for managing user notification, score display, and news filter preferences."""
    
    class Meta:
        model = UserPreferences
        fields = [
            'show_live_scores', 'breaking_news_only',
            'breaking_news_notifications', 'game_start_notifications',
            'sources_limit', 'sources_used'
        ]


class RecentSearchSerializer(serializers.ModelSerializer):
    """Serializer for user search history records."""
    
    entity = EntitySerializer()
    
    class Meta:
        model = RecentSearch
        fields = ['id', 'query', 'entity', 'searched_at']