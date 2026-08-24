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
    """Serializer for RSS source definitions with related entity ID resolution."""
    entity_ids = serializers.SerializerMethodField()

    class Meta:
        model = Source
        fields = [
            'id', 'name', 'rss_url', 'canonical_url', 'sitemap_url', 'domain', 'favicon_url',
            'is_verified', 'is_active', 'entity_ids',
        ]

    def get_entity_ids(self, obj):
        """Retrieve a list of associated entity primary keys."""
        return list(obj.entities.values_list('id', flat=True))


class FeedItemSerializer(serializers.ModelSerializer):
    """Full detail serializer for feed articles including AI summaries and parsed contents."""
    source = SourceSerializer(read_only=True)
    entity_names = serializers.SerializerMethodField()
    entities = EntitySerializer(many=True, read_only=True)
    url = serializers.SerializerMethodField()

    class Meta:
        model = FeedItem
        fields = [
            'id', 'title', 'url', 'summary', 'thumbnail_url',
            'published_at', 'entity_names', 'entities', 'is_trending', 'is_breaking',
            'views', 'source', 'content', 'ai_summary', 'content_fetched',
        ]

    def get_entity_names(self, obj):
        """Extract a list of entity names associated with the article."""
        return [e.name for e in obj.entities.all()]

    def get_url(self, obj):
        """Resolve raw Google News redirect links to canonical publisher URLs."""
        if obj.url and "news.google.com" in obj.url:
            from apps.feed.utils_url import resolve_real_article_url
            return resolve_real_article_url(obj.url)
        return obj.url


class FeedItemCompactSerializer(serializers.ModelSerializer):
    """Compact and high-performance feed article serializer optimized for list feeds and infinite scroll."""
    source_name = serializers.SerializerMethodField()
    source_logo = serializers.SerializerMethodField()
    publisher_name = serializers.SerializerMethodField()
    publisher_logo = serializers.SerializerMethodField()
    entity_name = serializers.SerializerMethodField()
    entity_logo = serializers.SerializerMethodField()
    entity_names = serializers.SerializerMethodField()
    entities = serializers.SerializerMethodField()
    url = serializers.SerializerMethodField()
    is_bookmarked = serializers.SerializerMethodField()
    is_liked = serializers.SerializerMethodField()
    like_count = serializers.SerializerMethodField()

    class Meta:
        model = FeedItem
        fields = [
            'id', 'source_name', 'source_logo', 'publisher_name', 'publisher_logo',
            'entity_name', 'entity_logo', 'entity_names', 'entities', 'title', 'summary', 'thumbnail_url', 'url',
            'published_at', 'is_breaking', 'is_trending', 'views', 'is_bookmarked', 'is_liked', 'like_count'
        ]

    def get_url(self, obj):
        """Resolve canonical publisher URL for redirect items."""
        if obj.url and "news.google.com" in obj.url:
            from apps.feed.utils_url import resolve_real_article_url
            return resolve_real_article_url(obj.url)
        return obj.url

    def _get_primary_entity(self, obj):
        """Select primary entity based on active contextual filter or default ordering."""
        selected_entity_types = self.context.get('selected_entity_types')
        entities = list(obj.entities.all())
        if not entities:
            return None
        if selected_entity_types:
            for e in entities:
                if e.type in selected_entity_types:
                    return e
        return entities[0]

    def get_entity_name(self, obj):
        """Return the primary entity name."""
        entity = self._get_primary_entity(obj)
        return entity.name if entity else ''

    def get_entity_logo(self, obj):
        """Return the primary entity logo URL."""
        entity = self._get_primary_entity(obj)
        return entity.logo_url if entity else ''

    def get_entity_names(self, obj):
        """Return filtered list of associated entity names."""
        selected_entity_types = self.context.get('selected_entity_types')
        entities = list(obj.entities.all())
        if selected_entity_types:
            entities = [e for e in entities if e.type in selected_entity_types]
        return [e.name for e in entities]

    def get_entities(self, obj):
        """Serialize associated entities applying active entity type filters."""
        selected_entity_types = self.context.get('selected_entity_types')
        entities = list(obj.entities.all())
        if selected_entity_types:
            entities = [e for e in entities if e.type in selected_entity_types]
        return EntitySerializer(entities, many=True, context=self.context).data

    def get_source_name(self, obj):
        """Return display source/entity name."""
        entity = self._get_primary_entity(obj)
        if entity:
            return entity.name
        return getattr(obj.source, 'name', '')

    def get_source_logo(self, obj):
        """Return source/entity display badge."""
        entity = self._get_primary_entity(obj)
        if entity and entity.logo_url:
            return entity.logo_url
        return self.get_publisher_logo(obj)

    def get_publisher_name(self, obj):
        """Return publisher branding name."""
        if obj.publisher_name:
            return obj.publisher_name
        return getattr(obj.source, 'name', '')

    def get_publisher_logo(self, obj):
        """Resolve publisher logo or fallback favicon."""
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
        """Check if authenticated request user has bookmarked the item."""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        user_bookmarked_ids = self.context.get('user_bookmarked_ids')
        if user_bookmarked_ids is not None:
            return obj.id in user_bookmarked_ids
        return Bookmark.objects.filter(user=request.user, feed_item=obj).exists()

    def get_is_liked(self, obj):
        """Check if authenticated request user has liked the item."""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        user_liked_ids = self.context.get('user_liked_ids')
        if user_liked_ids is not None:
            return obj.id in user_liked_ids
        return Like.objects.filter(user=request.user, feed_item=obj).exists()

    def get_like_count(self, obj):
        """Retrieve total like count from context cache or database."""
        if hasattr(obj, 'like_count'):
            return obj.like_count
        like_counts_map = self.context.get('like_counts_map')
        if like_counts_map is not None:
            return like_counts_map.get(obj.id, 0)
        return Like.objects.filter(feed_item=obj).count()


class UserSourceSerializer(serializers.ModelSerializer):
    """Serializer for user followed news sources."""
    source = SourceSerializer()

    class Meta:
        model = UserSource
        fields = ['id', 'source', 'created_at']


class AddSourceSerializer(serializers.Serializer):
    """Validation serializer for linking a custom source to an entity."""
    entity_id = serializers.IntegerField()
    source_name = serializers.CharField(max_length=200)
    source_type = serializers.ChoiceField(choices=['rss', 'youtube', 'website'])
    url = serializers.URLField()

    def validate(self, data):
        """Verify that target entity exists."""
        from apps.entity.models import Entity
        try:
            Entity.objects.get(id=data['entity_id'])
        except Entity.DoesNotExist:
            raise serializers.ValidationError({'entity_id': 'Entity not found'})
        return data


class BookmarkSerializer(serializers.ModelSerializer):
    """Serializer for user article bookmarks."""
    feed_item = FeedItemCompactSerializer(read_only=True)
 
    class Meta:
        model = Bookmark
        fields = ['id', 'feed_item', 'created_at']


class LikeSerializer(serializers.ModelSerializer):
    """Serializer for user article like interactions."""
    feed_item = FeedItemCompactSerializer(read_only=True)
 
    class Meta:
        model = Like
        fields = ['id', 'feed_item', 'created_at']
 