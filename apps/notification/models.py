from django.conf import settings
from django.db import models
from django.utils import timezone


class DeviceToken(models.Model):
    """Stores user device push tokens (FCM/APNS) for mobile and web push notifications."""

    DEVICE_TYPE_CHOICES = (
        ("android", "Android"),
        ("ios", "iOS"),
        ("web", "Web"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="device_tokens",
        null=True,
        blank=True,
        db_index=True,
    )
    token = models.CharField(max_length=512, unique=True, db_index=True)
    device_type = models.CharField(
        max_length=20,
        choices=DEVICE_TYPE_CHOICES,
        default="android",
    )
    device_name = models.CharField(max_length=100, blank=True, null=True)
    is_active = models.BooleanField(default=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Device Token"
        verbose_name_plural = "Device Tokens"
        ordering = ["-updated_at"]

    def __str__(self):
        user_str = self.user.email if self.user else "Anonymous"
        return f"{user_str} ({self.device_type}) - {self.token[:16]}..."


class NotificationPreference(models.Model):
    """Granular user notification settings and channel toggles."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )
    push_enabled = models.BooleanField(default=True)
    email_enabled = models.BooleanField(default=True)

    # Category Preferences
    match_reminders = models.BooleanField(
        default=True,
        help_text="Notifications before scheduled matches of followed teams start.",
    )
    score_updates = models.BooleanField(
        default=True,
        help_text="Live score changes and match end results.",
    )
    news_alerts = models.BooleanField(
        default=True,
        help_text="Breaking news and personalized feed updates.",
    )
    community_activity = models.BooleanField(
        default=True,
        help_text="Comments, mentions, and nest community activities.",
    )
    streak_reminders = models.BooleanField(
        default=True,
        help_text="Reminders to maintain daily app streaks.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Notification Preference"
        verbose_name_plural = "Notification Preferences"

    def __str__(self):
        return f"Preferences for {self.user.email}"

    def is_type_allowed(self, notification_type: str) -> bool:
        """Check whether user has enabled notifications for a specific type."""
        if not self.push_enabled:
            return False

        type_mapping = {
            "match_reminder": self.match_reminders,
            "score_update": self.score_updates,
            "breaking_news": self.news_alerts,
            "news": self.news_alerts,
            "nest_interaction": self.community_activity,
            "community": self.community_activity,
            "streak_reminder": self.streak_reminders,
            "streak": self.streak_reminders,
        }
        return type_mapping.get(notification_type, True)


class Notification(models.Model):
    """User in-app notification records with payload metadata."""

    NOTIFICATION_TYPE_CHOICES = (
        ("general", "General"),
        ("match_reminder", "Match Reminder"),
        ("score_update", "Score Update"),
        ("breaking_news", "Breaking News"),
        ("nest_interaction", "Nest Interaction"),
        ("streak_reminder", "Streak Reminder"),
    )

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        db_index=True,
    )
    notification_type = models.CharField(
        max_length=50,
        choices=NOTIFICATION_TYPE_CHOICES,
        default="general",
        db_index=True,
    )
    title = models.CharField(max_length=255)
    body = models.TextField()
    image_url = models.URLField(max_length=500, blank=True, null=True)
    data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Custom key-value pairs for in-app deep linking (e.g. event_id, feed_id).",
    )

    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read", "-created_at"]),
        ]

    def __str__(self):
        return f"[{self.notification_type}] {self.title} -> {self.recipient.email}"

    def mark_as_read(self):
        """Mark notification as read."""
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=["is_read", "read_at", "updated_at"])
