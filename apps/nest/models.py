from django.db import models
from django.conf import settings
from apps.entity.models import Entity

class UserNest(models.Model):
    """User followed entity in their personalized 360-degree Nest dashboard."""
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='nest')
    entity = models.ForeignKey(Entity, on_delete=models.CASCADE)
    
    # Position in 360° circle (0-359)
    position = models.IntegerField(default=0)
    
    # Preferences
    notify_on_games = models.BooleanField(default=True)
    notify_on_news = models.BooleanField(default=True)
    
    # Timestamps
    added_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['user', 'entity']
        ordering = ['position', '-added_at']
    
    def __str__(self):
        return f"{self.user.email} -> {self.entity.name}"


class UserPreferences(models.Model):
    """Global user application, notification, and source limit preferences."""
    
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='preferences')
    
    # Feed preferences
    show_live_scores = models.BooleanField(default=True)
    breaking_news_only = models.BooleanField(default=False)
    
    # Notification preferences
    breaking_news_notifications = models.BooleanField(default=True)
    game_start_notifications = models.BooleanField(default=True)
    
    # Source management
    sources_limit = models.IntegerField(default=3)  # Increases with ads
    sources_used = models.IntegerField(default=0)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"Preferences for {self.user.email}"


class RecentSearch(models.Model):
    """User search history entry recording search queries and selected entity links."""
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='recent_searches')
    query = models.CharField(max_length=200)
    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, null=True, blank=True)
    
    searched_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-searched_at']
    
    def __str__(self):
        return f"{self.user.email} searched: {self.query}"