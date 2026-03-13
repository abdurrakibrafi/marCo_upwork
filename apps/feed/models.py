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
    domain = models.CharField(max_length=255, blank=True)
    favicon_url = models.URLField(blank=True)

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
    url = models.URLField(max_length=1000)
    url_hash = models.CharField(max_length=500, unique=True, db_index=True)  # MD5 of URL
    summary = models.TextField(blank=True)
    thumbnail_url = models.URLField(blank=True)
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