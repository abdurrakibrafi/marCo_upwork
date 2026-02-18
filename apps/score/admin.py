from django.contrib import admin
from .models import LiveScore


@admin.register(LiveScore)
class LiveScoreAdmin(admin.ModelAdmin):
    """Admin configuration for LiveScore model"""
    
    list_display = [
        'sport', 
        'home_team', 
        'away_team', 
        'home_score', 
        'away_score', 
        'status', 
        'start_time',
        'updated_at'
    ]
    
    list_filter = [
        'sport',
        'status',
        'updated_at',
        'start_time'
    ]
    
    search_fields = [
        'home_team',
        'away_team',
        'external_id'
    ]
    
    readonly_fields = [
        'created_at',
        'updated_at'
    ]
    
    fieldsets = [
        (
            'Game Information',
            {
                'fields': [
                    'sport',
                    'external_id',
                    'status',
                    'status_detail',
                    'start_time'
                ]
            }
        ),
        (
            'Teams',
            {
                'fields': [
                    'home_team',
                    'away_team',
                    'home_logo',
                    'away_logo'
                ]
            }
        ),
        (
            'Scores',
            {
                'fields': [
                    'home_score',
                    'away_score'
                ]
            }
        ),
        (
            'Data',
            {
                'fields': [
                    'raw_data'
                ],
                'classes': ['collapse']
            }
        ),
        (
            'Timestamps',
            {
                'fields': [
                    'created_at',
                    'updated_at'
                ]
            }
        ),
    ]
    
    date_hierarchy = 'start_time'
    
    list_editable = [
        'home_score',
        'away_score',
        'status'
    ]
    
    list_per_page = 50
    
    actions = ['mark_as_live', 'mark_as_completed']
    
    def mark_as_live(self, request, queryset):
        """Admin action to mark selected games as live"""
        updated = queryset.update(status='live')
        self.message_user(request, f'{updated} games marked as live.')
    mark_as_live.short_description = "Mark selected games as Live"
    
    def mark_as_completed(self, request, queryset):
        """Admin action to mark selected games as completed"""
        updated = queryset.update(status='completed')
        self.message_user(request, f'{updated} games marked as completed.')
    mark_as_completed.short_description = "Mark selected games as Completed"