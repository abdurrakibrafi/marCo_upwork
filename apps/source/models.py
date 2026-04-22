from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class UserCustomSource(models.Model):
    """
    A source manually added by the user through the Source Search screen.
    When this exists, the source's FeedItems appear in the user's nest feed.
    """
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='custom_sources'
    )
    source = models.ForeignKey(
        'feed.Source', on_delete=models.CASCADE, related_name='custom_followers'
    )
    # What the user typed when they found this source — useful for analytics
    search_query = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'source')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user} → {self.source.name}"