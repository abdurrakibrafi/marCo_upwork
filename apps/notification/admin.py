from django.contrib import admin
from apps.notification.models import (
    DeviceToken,
    Notification,
    NotificationPreference,
)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "recipient", "notification_type", "is_read", "created_at")
    list_filter = ("notification_type", "is_read", "created_at")
    search_fields = ("title", "body", "recipient__email")
    readonly_fields = ("created_at", "updated_at", "read_at")


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "device_type", "is_active", "token_preview", "updated_at")
    list_filter = ("device_type", "is_active", "created_at")
    search_fields = ("user__email", "token", "device_name")
    readonly_fields = ("created_at", "updated_at")

    def token_preview(self, obj):
        return f"{obj.token[:24]}..." if obj.token else ""

    token_preview.short_description = "Token Preview"


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "push_enabled",
        "email_enabled",
        "match_reminders",
        "score_updates",
        "news_alerts",
        "community_activity",
        "streak_reminders",
    )
    list_filter = ("push_enabled", "email_enabled", "match_reminders", "score_updates", "news_alerts")
    search_fields = ("user__email",)
