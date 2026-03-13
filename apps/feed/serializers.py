from rest_framework import serializers
from .models import FeedItem, Source, UserSource, HiddenSource
from apps.entity.serializers import EntitySerializer


class SourceSerializer(serializers.ModelSerializer):
    entity_ids = serializers.SerializerMethodField()

    class Meta:
        model = Source
        fields = [
            'id', 'name', 'rss_url', 'domain', 'favicon_url',
            'is_verified', 'is_active', 'entity_ids',
        ]

    def get_entity_ids(self, obj):
        return list(obj.entities.values_list('id', flat=True))


class FeedItemSerializer(serializers.ModelSerializer):
    source = SourceSerializer(read_only=True)
    entity_names = serializers.SerializerMethodField()

    class Meta:
        model = FeedItem
        fields = [
            'id', 'title', 'url', 'summary', 'thumbnail_url',
            'published_at', 'entity_names', 'is_trending', 'is_breaking',
            'views', 'source',
        ]

    def get_entity_names(self, obj):
        return [e.name for e in obj.entities.all()]



class FeedItemCompactSerializer(serializers.ModelSerializer):
    source_name = serializers.CharField(source='source.name')
    source_logo = serializers.URLField(source='source.favicon_url')
    entity_names = serializers.SerializerMethodField()

    class Meta:
        model = FeedItem
        fields = [
            'id', 'source_name', 'source_logo', 'entity_names',
            'title', 'summary', 'thumbnail_url', 'url',
            'published_at', 'is_breaking', 'is_trending', 'views'
        ]

    def get_entity_names(self, obj):
        return [e.name for e in obj.entities.all()]

class UserSourceSerializer(serializers.ModelSerializer):
    """User source serializer"""
    
    source = SourceSerializer()
    entity = EntitySerializer()
    
    class Meta:
        model = UserSource
        fields = ['id', 'source', 'entity', 'is_active', 'added_at']


class AddSourceSerializer(serializers.Serializer):
    """Serializer for adding sources"""
    
    entity_id = serializers.IntegerField()
    source_name = serializers.CharField(max_length=200)
    source_type = serializers.ChoiceField(choices=['rss', 'youtube', 'website'])
    url = serializers.URLField()
    
    def validate(self):
        data = super().validate()
        
        # Validate entity exists
        from entities.models import Entity
        try:
            Entity.objects.get(id=data['entity_id'])
        except Entity.DoesNotExist:
            raise serializers.ValidationError("Entity not found")
        
        return data