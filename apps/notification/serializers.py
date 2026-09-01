from rest_framework import serializers
from apps.notification.models import (
    Notification,
    DeviceToken,
    NotificationPreference,
)


class NotificationSerializer(serializers.ModelSerializer):
    """Serializer for reading in-app notifications."""

    class Meta:
        model = Notification
        fields = [
            "id",
            "notification_type",
            "title",
            "body",
            "image_url",
            "data",
            "is_read",
            "read_at",
            "created_at",
        ]
        read_only_fields = fields


class DeviceTokenSerializer(serializers.ModelSerializer):
    """Serializer for registering or updating device push tokens."""

    class Meta:
        model = DeviceToken
        fields = [
            "token",
            "device_type",
            "device_name",
            "is_active",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]

    def create(self, validated_data):
        user = self.context.get("request").user if self.context.get("request") else None
        token = validated_data.get("token")
        device_type = validated_data.get("device_type", "android")
        device_name = validated_data.get("device_name", "")
        is_active = validated_data.get("is_active", True)

        device_token, _ = DeviceToken.objects.update_or_create(
            token=token,
            defaults={
                "user": user if (user and user.is_authenticated) else None,
                "device_type": device_type,
                "device_name": device_name,
                "is_active": is_active,
            },
        )
        return device_token


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    """Serializer for viewing and updating user notification preferences."""

    class Meta:
        model = NotificationPreference
        fields = [
            "push_enabled",
            "email_enabled",
            "match_reminders",
            "score_updates",
            "news_alerts",
            "community_activity",
            "streak_reminders",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]


class MarkReadRequestSerializer(serializers.Serializer):
    """Serializer validating mark-as-read requests."""

    notification_id = serializers.IntegerField(required=False, help_text="Specific notification ID to mark as read.")
    notification_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        help_text="List of notification IDs to mark as read.",
    )
    all = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Set to true to mark all notifications as read.",
    )

    def validate(self, attrs):
        if not attrs.get("all") and not attrs.get("notification_id") and not attrs.get("notification_ids"):
            # Default to marking all if nothing specified, or allow empty payload as mark all
            attrs["all"] = True
        return attrs
