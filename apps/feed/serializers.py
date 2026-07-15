from rest_framework import serializers
from .models import FeedItem, Source, UserSource, HiddenSource, Bookmark, Like
from apps.entity.serializers import EntitySerializer


# Known publisher name → domain mapping for favicon resolution.
# Keys are lowercase publisher names as they appear in Google News RSS.
_PUBLISHER_DOMAIN = {
    'espn': 'espn.com',
    'reuters': 'reuters.com',
    'the guardian': 'theguardian.com',
    'guardian': 'theguardian.com',
    'ap news': 'apnews.com',
    'associated press': 'apnews.com',
    'yahoo sports': 'sports.yahoo.com',
    'yahoo': 'yahoo.com',
    'new york times': 'nytimes.com',
    'the new york times': 'nytimes.com',
    'nyt': 'nytimes.com',
    'bbc': 'bbc.com',
    'bbc sport': 'bbc.com',
    'bbc news': 'bbc.com',
    'sky sports': 'skysports.com',
    'goal': 'goal.com',
    'marca': 'marca.com',
    'cnn': 'cnn.com',
    'fox sports': 'foxsports.com',
    'bleacher report': 'bleacherreport.com',
    'nbc sports': 'nbcsports.com',
    'cbs sports': 'cbssports.com',
    'cbs news': 'cbsnews.com',
    'the athletic': 'theathletic.com',
    'bloomberg': 'bloomberg.com',
    'bloomberg.com': 'bloomberg.com',
    'ndtv': 'ndtv.com',
    'rfi': 'rfi.fr',
    'heavy.com': 'heavy.com',
    'heavy': 'heavy.com',
    'toffeeweb': 'toffeeweb.com',
    'athlon sports': 'athlonsports.com',
    'the times': 'thetimes.co.uk',
    'daily mail': 'dailymail.co.uk',
    'mirror': 'mirror.co.uk',
    'the sun': 'thesun.co.uk',
    'talksport': 'talksport.com',
    'skysports.com': 'skysports.com',
    'india today': 'indiatoday.in',
    'cricinfo': 'espncricinfo.com',
    'espncricinfo': 'espncricinfo.com',
    'cricbuzz': 'cricbuzz.com',
    'the telegraph': 'telegraph.co.uk',
    'telegraph': 'telegraph.co.uk',
    'forbes': 'forbes.com',
    'sportstar': 'sportstar.thehindu.com',
    'tmx newsfile': 'tmxnewsfile.com',
    'new haven register': 'nhregister.com',
    'the lufkin daily news': 'lufkindailynews.com',
}


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
            'views', 'source', 'content', 'ai_summary', 'content_fetched',
        ]

    def get_entity_names(self, obj):
        return [e.name for e in obj.entities.all()]


class FeedItemCompactSerializer(serializers.ModelSerializer):
    source_name = serializers.SerializerMethodField()
    source_logo = serializers.SerializerMethodField()
    publisher_name = serializers.SerializerMethodField()
    publisher_logo = serializers.SerializerMethodField()
    entity_name = serializers.SerializerMethodField()
    entity_logo = serializers.SerializerMethodField()
    entity_names = serializers.SerializerMethodField()
    entities = EntitySerializer(many=True, read_only=True)
    is_bookmarked = serializers.SerializerMethodField()
    is_liked = serializers.SerializerMethodField()

    class Meta:
        model = FeedItem
        fields = [
            'id', 'source_name', 'source_logo', 'publisher_name', 'publisher_logo',
            'entity_name', 'entity_logo', 'entity_names', 'entities', 'title', 'summary', 'thumbnail_url', 'url',
            'published_at', 'is_breaking', 'is_trending', 'views', 'is_bookmarked', 'is_liked'
        ]

    def get_entity_name(self, obj):
        entity = obj.entities.first()
        return entity.name if entity else ''

    def get_entity_logo(self, obj):
        entity = obj.entities.first()
        return entity.logo_url if entity else ''

    def get_entity_names(self, obj):
        return [e.name for e in obj.entities.all()]

    def get_source_name(self, obj):
        entity = obj.entities.first()
        if entity:
            return entity.name
        return getattr(obj.source, 'name', '')

    def get_source_logo(self, obj):
        entity = obj.entities.first()
        if entity and entity.logo_url:
            return entity.logo_url
        return self.get_publisher_logo(obj)

    def get_publisher_name(self, obj):
        if obj.publisher_name:
            return obj.publisher_name
        return getattr(obj.source, 'name', '')

    def get_publisher_logo(self, obj):
        # ── 1. Per-item publisher logo (e.g. ESPN, Reuters from Google News) ──
        publisher = getattr(obj, 'publisher_name', '').strip().lower()
        if publisher:
            domain = _PUBLISHER_DOMAIN.get(publisher)
            if domain:
                return f'https://www.google.com/s2/favicons?domain={domain}&sz=64'
            # Generic fallback: try publisher name as domain guess
            # (covers obscure publishers not in our map)
            guessed = publisher.replace(' ', '').replace('.', '') + '.com'
            return f'https://www.google.com/s2/favicons?domain={guessed}&sz=64'

        # ── 2. Source favicon_url stored explicitly ──
        source_favicon = getattr(obj.source, 'favicon_url', None)
        if source_favicon:
            return source_favicon

        # ── 3. Source domain favicon ──
        domain = getattr(obj.source, 'domain', None)
        if domain and domain != 'news.google.com':
            clean = domain.replace('https://', '').replace('http://', '').rstrip('/')
            return f'https://www.google.com/s2/favicons?domain={clean}&sz=64'

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
 