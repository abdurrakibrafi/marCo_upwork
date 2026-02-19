from rest_framework import serializers
from .models import FeedItem, Source, UserSource, HiddenSource
from apps.entity.serializers import EntitySerializer


class SourceSerializer(serializers.ModelSerializer):
    """Source serializer"""
    
    class Meta:
        model = Source
        fields = [
            'id', 'name', 'type', 'url', 'logo_url',
            'description', 'article_count', 'is_verified'
        ]


class FeedItemSerializer(serializers.ModelSerializer):
    """Feed item serializer"""
    
    entity = EntitySerializer()
    source = SourceSerializer()
    
    class Meta:
        model = FeedItem
        fields = [
            'id', 'content_type', 'entity', 'source',
            'title', 'description', 'image_url', 'video_url',
            'thumbnail_url', 'url', 'author', 'published_at',
            'views', 'is_breaking', 'is_trending'
        ]


class FeedItemCompactSerializer(serializers.ModelSerializer):
    """Compact feed item serializer (for lists)"""
    
    entity_name = serializers.CharField(source='entity.name')
    entity_logo = serializers.URLField(source='entity.logo_url')
    source_name = serializers.CharField(source='source.name')
    source_logo = serializers.URLField(source='source.logo_url')
    
    class Meta:
        model = FeedItem
        fields = [
            'id', 'content_type', 'entity_name', 'entity_logo',
            'source_name', 'source_logo', 'title', 'description',
            'image_url', 'thumbnail_url', 'url', 'published_at',
            'is_breaking', 'is_trending'
        ]


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