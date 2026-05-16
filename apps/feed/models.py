from django.db import models
from django.contrib.auth import get_user_model
from apps.entity.models import Entity
import hashlib

User = get_user_model()


"""
feed/models.py — updated Source model with RSS pipeline fields.
Add these fields to your existing Source model migration.
"""

from django.db import models
from apps.entity.models import Entity


class Source(models.Model):
    """
    An RSS/Atom feed source for a sports entity.
    Discovered via Brave Search + RSS autodiscovery, then polled forever.
    """

    DISCOVERY_SOURCES = [
        ('brave', 'Brave Search Discovery'),
        ('known', 'Known Sports Feed'),
        ('manual', 'Manually Added'),
    ]

    # Core fields
    name = models.CharField(max_length=255, blank=True)
    rss_url = models.URLField(unique=True, default='', blank=True, null=True)
    domain = models.CharField(max_length=5000, blank=True)
    favicon_url = models.URLField(blank=True, max_length=5000)

    # M2M — one RSS feed can cover multiple entities (e.g. ESPN covers all)
    entities = models.ManyToManyField(Entity, related_name='sources', blank=True)

    # Discovery metadata
    discovery_source = models.CharField(
        max_length=20,
        choices=DISCOVERY_SOURCES,
        default='brave'
    )

    # Polling config
    is_active = models.BooleanField(default=True)
    poll_interval_minutes = models.PositiveIntegerField(default=30)
    last_polled_at = models.DateTimeField(null=True, blank=True)
    poll_failures = models.PositiveIntegerField(default=0)

    # Feed quality
    is_verified = models.BooleanField(default=False)  # manually verified high-quality
    is_premium = models.BooleanField(default=False)   # shown only to ad-free subscribers

    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_verified', 'name']

    def __str__(self):
        return self.name or self.rss_url

    @property
    def is_healthy(self):
        return self.is_active and self.poll_failures < 5


class FeedItem(models.Model):
    """
    A single article from an RSS feed, linked to one or more entities.
    """

    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name='items')
    entities = models.ManyToManyField(Entity, related_name='feed_items', blank=True)

    # Article data
    title = models.CharField(max_length=500)
    url = models.URLField(max_length=5000)
    url_hash = models.CharField(max_length=500, unique=True, db_index=True)  # MD5 of URL
    summary = models.TextField(blank=True)
    thumbnail_url = models.URLField(blank=True, max_length=5000)
    published_at = models.DateTimeField(db_index=True)

    # Feed metadata
    is_trending = models.BooleanField(default=False, db_index=True)
    is_breaking = models.BooleanField(default=False)
    views = models.PositiveIntegerField(default=0)

    # Raw data intentionally NOT stored (Brave API policy compliance)
    raw_data = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        ordering = ['-published_at']
        indexes = [
            models.Index(fields=['-published_at']),
            models.Index(fields=['url_hash']),
        ]

    def __str__(self):
        return self.title[:80]


class UserSource(models.Model):
    """User has explicitly followed a source"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='followed_sources')
    source = models.ForeignKey(Source, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        unique_together = ('user', 'source')


class HiddenSource(models.Model):
    """User has hidden a source from their feed"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='hidden_sources')
    source = models.ForeignKey(Source, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        unique_together = ('user', 'source')


class Bookmark(models.Model):
    """User has saved a feed item to read later"""
 
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='bookmarks',
    )
    feed_item = models.ForeignKey(
        FeedItem,
        on_delete=models.CASCADE,
        related_name='bookmarked_by',
    )
    created_at = models.DateTimeField(auto_now_add=True)
 
    class Meta:
        unique_together = ('user', 'feed_item')
        ordering = ['-created_at']
 
    def __str__(self):
        return f"{self.user.email} bookmarked: {self.feed_item.title[:60]}"
 

class Like(models.Model):
    """User liked a feed item"""
 
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='likes',
    )
    feed_item = models.ForeignKey(
        FeedItem,
        on_delete=models.CASCADE,
        related_name='liked_by',
    )
    created_at = models.DateTimeField(auto_now_add=True)
 
    class Meta:
        unique_together = ('user', 'feed_item')
        ordering = ['-created_at']
 
    def __str__(self):
        return f"{self.user.email} liked: {self.feed_item.title[:60]}"


class RSSSource(models.Model):
    """
    Admin-managed RSS feed source.
    Used for automatic, scheduled news fetching for entities.
    """
    
    SPORT_CHOICES = Entity.SPORT_CHOICES
    
    # Feed info
    name = models.CharField(max_length=200, help_text="e.g., 'La Liga Official'")
    url = models.URLField(max_length=2000, unique=True)
    
    # Sport/entity targeting
    sport = models.CharField(
        max_length=50,
        choices=SPORT_CHOICES,
        help_text="Sport this feed covers"
    )
    entities = models.ManyToManyField(
        Entity,
        related_name='rss_sources',
        blank=True,
        help_text="Entities this feed provides news for"
    )
    
    # Keywords (for smart matching)
    keywords = models.JSONField(
        default=list,
        help_text='["La Liga", "Spanish Football", "LALIGA"]'
    )
    
    # Configuration
    is_active = models.BooleanField(default=True)
    fetch_interval_hours = models.PositiveIntegerField(default=6)
    last_fetched_at = models.DateTimeField(null=True, blank=True)
    fetch_failures = models.PositiveIntegerField(default=0)
    
    # Quality
    is_verified = models.BooleanField(default=False, help_text="Admin verified")
    estimated_quality = models.CharField(
        max_length=20,
        choices=[('high', 'High'), ('medium', 'Medium'), ('low', 'Low')],
        default='medium'
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['sport', 'name']
        indexes = [
            models.Index(fields=['sport', 'is_active']),
            models.Index(fields=['is_verified']),
        ]
    
    def __str__(self):
        return f"{self.name} ({self.sport})"


class EntitySource(models.Model):
    """
    Link between a UserNest entity and user-selected sources for that entity.
    
    When user adds Barcelona to nest + selects ESPN as source,
    this tracks that ESPN should be included in Barcelona's feed.
    """
    
    from apps.nest.models import UserNest
    
    user_nest = models.ForeignKey(
        UserNest,
        on_delete=models.CASCADE,
        related_name='selected_sources'
    )
    source = models.ForeignKey(
        Source,
        on_delete=models.CASCADE,
        related_name='entity_selections'
    )
    
    # User preference for this source
    priority = models.IntegerField(
        default=0,
        help_text="Higher = shown first in feed"
    )
    
    added_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['user_nest', 'source']
        ordering = ['-priority', '-added_at']
    
    def __str__(self):
        return f"{self.user_nest.entity.name} ← {self.source.name}"