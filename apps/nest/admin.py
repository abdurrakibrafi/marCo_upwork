from django.contrib import admin
from apps.nest.models import UserNest, UserPreferences, RecentSearch

@admin.register(UserNest)
class UserNestAdmin(admin.ModelAdmin):
    list_display = ['user', 'entity', 'position', 'added_at']
    list_filter = ['entity__type', 'entity__sport']
    search_fields = ['user__username', 'entity__name']
    list_select_related = ['user', 'entity']
    raw_id_fields = ['user', 'entity']

@admin.register(UserPreferences)
class UserPreferencesAdmin(admin.ModelAdmin):
    list_display = ['user', 'show_live_scores', 'sources_limit', 'sources_used']
    search_fields = ['user__username']
    list_select_related = ['user']
    raw_id_fields = ['user']

@admin.register(RecentSearch)
class RecentSearchAdmin(admin.ModelAdmin):
    list_display = ['user', 'query', 'entity', 'searched_at']
    list_filter = ['searched_at']
    search_fields = ['user__username', 'query']
    list_select_related = ['user', 'entity']
    raw_id_fields = ['user', 'entity']