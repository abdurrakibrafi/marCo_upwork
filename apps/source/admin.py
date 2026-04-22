from django.contrib import admin
from .models import UserCustomSource


@admin.register(UserCustomSource)
class UserCustomSourceAdmin(admin.ModelAdmin):
    list_display = ('user', 'source', 'is_active', 'created_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('user__username', 'user__email', 'source__name', 'search_query')
    readonly_fields = ('created_at',)
    date_hierarchy = 'created_at'
    
    fieldsets = (
        (None, {
            'fields': ('user', 'source', 'search_query')
        }),
        ('Status', {
            'fields': ('is_active',)
        }),
        ('Timestamps', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )