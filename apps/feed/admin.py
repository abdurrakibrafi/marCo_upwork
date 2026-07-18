from django.contrib import admin
from apps.feed.models import Source, FeedItem, UserSource, HiddenSource, RSSSource, EntitySource


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'domain', 'discovery_source', 'is_verified', 'is_active', 'poll_failures']
    list_filter = ['discovery_source', 'is_verified', 'is_active']
    search_fields = ['name', 'rss_url', 'domain']


@admin.register(RSSSource)
class RSSSourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'sport', 'is_active', 'is_verified', 'estimated_quality', 'fetch_failures', 'last_fetched_at']
    list_filter = ['sport', 'is_active', 'is_verified', 'estimated_quality']
    search_fields = ['name', 'url']
    filter_horizontal = ['entities']
    fieldsets = (
        ('Basic Info', {
            'fields': ('name', 'url', 'sport', 'keywords')
        }),
        ('Configuration', {
            'fields': ('is_active', 'fetch_interval_hours', 'estimated_quality')
        }),
        ('Targeting', {
            'fields': ('entities',)
        }),
        ('Status', {
            'fields': ('is_verified', 'last_fetched_at', 'fetch_failures'),
            'classes': ('collapse',)
        }),
    )


@admin.register(EntitySource)
class EntitySourceAdmin(admin.ModelAdmin):
    list_display = ['user_nest', 'source', 'priority', 'added_at']
    list_filter = ['priority', 'added_at']
    search_fields = ['user_nest__user__email', 'source__name']
    list_select_related = ['user_nest', 'source']
    raw_id_fields = ['user_nest', 'source']


@admin.register(FeedItem)
class FeedItemAdmin(admin.ModelAdmin):
    list_display = [
        'title', 'source', 'published_at', 'views', 'is_breaking', 'is_trending'
    ]
    list_filter = ['is_breaking', 'is_trending', 'published_at']
    search_fields = ['title', 'source__name']
    date_hierarchy = 'published_at'
    list_select_related = ['source']
    raw_id_fields = ['source']


@admin.register(UserSource)
class UserSourceAdmin(admin.ModelAdmin):
    list_display = ['user', 'source', 'created_at']
    list_filter = ['created_at']
    search_fields = ['user__username', 'source__name']
    list_select_related = ['user', 'source']
    raw_id_fields = ['user', 'source']


@admin.register(HiddenSource)
class HiddenSourceAdmin(admin.ModelAdmin):
    list_display = ['user', 'source', 'created_at']
    search_fields = ['user__username', 'source__name']
    list_select_related = ['user', 'source']
    raw_id_fields = ['user', 'source']