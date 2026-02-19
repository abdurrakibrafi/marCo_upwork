from django.contrib import admin
from apps.event.models import (
    Event, EventTimeline, EventLineup,
    EventStatistics, EventPlayerStats, EventHighlight
)

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'sport', 'home_entity', 'away_entity',
        'start_time', 'status', 'home_score', 'away_score'
    ]
    list_filter = ['sport', 'status', 'start_time']
    search_fields = ['home_entity__name', 'away_entity__name']
    date_hierarchy = 'start_time'

@admin.register(EventTimeline)
class EventTimelineAdmin(admin.ModelAdmin):
    list_display = ['event', 'event_type', 'minute', 'team', 'player']
    list_filter = ['event_type']
    search_fields = ['event__home_entity__name', 'player__name']

@admin.register(EventLineup)
class EventLineupAdmin(admin.ModelAdmin):
    list_display = ['event', 'team', 'player', 'position_type', 'jersey_number']
    list_filter = ['position_type']
    search_fields = ['player__name', 'team__name']

@admin.register(EventStatistics)
class EventStatisticsAdmin(admin.ModelAdmin):
    list_display = ['event', 'team', 'updated_at']
    search_fields = ['event__home_entity__name', 'team__name']

@admin.register(EventPlayerStats)
class EventPlayerStatsAdmin(admin.ModelAdmin):
    list_display = ['event', 'player', 'team', 'points_or_goals']
    search_fields = ['player__name']

@admin.register(EventHighlight)
class EventHighlightAdmin(admin.ModelAdmin):
    list_display = ['event', 'title', 'source', 'views', 'created_at']
    list_filter = ['source', 'created_at']
    search_fields = ['title', 'event__home_entity__name']