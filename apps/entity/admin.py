from django.contrib import admin
from apps.entity.models import Entity, Team, Athlete, League, EntityStats

@admin.register(Entity)
class EntityAdmin(admin.ModelAdmin):
    list_display = ['name', 'type', 'sport', 'follower_count', 'has_api_data', 'is_active']
    list_filter = ['type', 'sport', 'has_api_data', 'is_active']
    search_fields = ['name', 'external_id']
    prepopulated_fields = {'slug': ('name',)}

@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ['entity', 'league', 'venue_name', 'total_wins', 'total_losses']
    search_fields = ['entity__name']

@admin.register(Athlete)
class AthleteAdmin(admin.ModelAdmin):
    list_display = ['entity', 'first_name', 'last_name', 'current_team', 'position']
    search_fields = ['first_name', 'last_name', 'entity__name']

@admin.register(League)
class LeagueAdmin(admin.ModelAdmin):
    list_display = ['entity', 'current_season', 'number_of_teams']
    search_fields = ['entity__name']

@admin.register(EntityStats)
class EntityStatsAdmin(admin.ModelAdmin):
    list_display = ['entity', 'season', 'stat_type', 'updated_at']
    list_filter = ['stat_type', 'season']