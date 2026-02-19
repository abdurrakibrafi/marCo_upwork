from django.contrib import admin
from apps.feed.models import Source, FeedItem, UserSource, HiddenSource, FeedItemView


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'type', 'article_count', 'is_verified', 'is_active']
    list_filter = ['type', 'is_verified', 'is_active']
    search_fields = ['name', 'url']


@admin.register(FeedItem)
class FeedItemAdmin(admin.ModelAdmin):
    list_display = [
        'title', 'entity', 'source', 'content_type',
        'published_at', 'views', 'is_breaking', 'is_trending'
    ]
    list_filter = ['content_type', 'is_breaking', 'is_trending', 'published_at']
    search_fields = ['title', 'entity__name', 'source__name']
    date_hierarchy = 'published_at'


@admin.register(UserSource)
class UserSourceAdmin(admin.ModelAdmin):
    list_display = ['user', 'source', 'entity', 'is_active', 'added_at']
    list_filter = ['is_active', 'added_at']
    search_fields = ['user__username', 'source__name', 'entity__name']


@admin.register(HiddenSource)
class HiddenSourceAdmin(admin.ModelAdmin):
    list_display = ['user', 'source', 'hidden_at']
    search_fields = ['user__username', 'source__name']


@admin.register(FeedItemView)
class FeedItemViewAdmin(admin.ModelAdmin):
    list_display = ['user', 'feed_item', 'viewed_at']
    search_fields = ['user__username', 'feed_item__title']