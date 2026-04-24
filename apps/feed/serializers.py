from rest_framework import serializers
from .models import FeedItem, Source, UserSource, HiddenSource, Bookmark, Like
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
    entities = EntitySerializer(many=True, read_only=True)

    class Meta:
        model = FeedItem
        fields = [
            'id', 'title', 'url', 'summary', 'thumbnail_url',
            'published_at', 'entity_names', 'entities', 'is_trending', 'is_breaking',
            'views', 'source',
        ]

    def get_entity_names(self, obj):
        return [e.name for e in obj.entities.all()]


class FeedItemCompactSerializer(serializers.ModelSerializer):
    source_name = serializers.CharField(source='source.name')
    source_logo = serializers.SerializerMethodField()
    entity_names = serializers.SerializerMethodField()
    entities = EntitySerializer(many=True, read_only=True)
    is_bookmarked = serializers.SerializerMethodField()
    is_liked = serializers.SerializerMethodField()

    class Meta:
        model = FeedItem
        fields = [
            'id', 'source_name', 'source_logo', 'entity_names', 'entities',
            'title', 'summary', 'thumbnail_url', 'url',
            'published_at', 'is_breaking', 'is_trending', 'views', 'is_bookmarked', 'is_liked'
        ]

    def get_entity_names(self, obj):
        return [e.name for e in obj.entities.all()]

    def get_source_name(self, obj):
        entity = obj.entities.first()
        if entity:
            return entity.name
        return getattr(obj.source, 'name', '')

    def get_source_logo(self, obj):
        source_favicon = getattr(obj.source, 'favicon_url', None)
        if source_favicon:
            return source_favicon

        domain = getattr(obj.source, 'domain', None)
        if domain:
            clean = domain.replace('https://', '').replace('http://', '').rstrip('/')
            return f'https://www.google.com/s2/favicons?domain={clean}&sz=64'

        entity = obj.entities.first()
        if entity and entity.logo_url:
            return entity.logo_url

        return ''

    def get_is_bookmarked(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return Bookmark.objects.filter(user=request.user, feed_item=obj).exists()
        return False

    def get_is_liked(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return Like.objects.filter(user=request.user, feed_item=obj).exists()
        return False


class UserSourceSerializer(serializers.ModelSerializer):
    """User source serializer"""
    source = SourceSerializer()

    class Meta:
        model = UserSource
        # BUG FIX: UserSource only has user, source, created_at.
        # Removed 'entity', 'is_active', 'added_at' — none of these
        # exist on the model, causing a crash on serialization.
        fields = ['id', 'source', 'created_at']


class AddSourceSerializer(serializers.Serializer):
    """Serializer for adding sources"""
    entity_id = serializers.IntegerField()
    source_name = serializers.CharField(max_length=200)
    source_type = serializers.ChoiceField(choices=['rss', 'youtube', 'website'])
    url = serializers.URLField()

    # BUG FIX: was `def validate(self)` — wrong signature, never called by DRF.
    # Also fixed import path: was `from entities.models` (wrong), now uses correct app path.
    def validate(self, data):
        from apps.entity.models import Entity
        try:
            Entity.objects.get(id=data['entity_id'])
        except Entity.DoesNotExist:
            raise serializers.ValidationError({'entity_id': 'Entity not found'})
        return data
    

from apps.feed.models import Bookmark, Like
 
class BookmarkSerializer(serializers.ModelSerializer):
    feed_item = FeedItemCompactSerializer(read_only=True)
 
    class Meta:
        model = Bookmark
        fields = ['id', 'feed_item', 'created_at']
 


class LikeSerializer(serializers.ModelSerializer):
    feed_item = FeedItemCompactSerializer(read_only=True)
 
    class Meta:
        model = Like
        fields = ['id', 'feed_item', 'created_at']
 