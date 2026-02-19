from django.db import models
from django.contrib.auth import get_user_model
from apps.entity.models import Entity
import hashlib

User = get_user_model()


class Source(models.Model):
    """Content sources (GNews, YouTube, RSS, etc.)"""
    
    SOURCE_TYPES = [
        ('gnews', 'GNews API'),
        ('youtube', 'YouTube'),
        ('rss', 'RSS Feed'),
        ('official', 'Official Source'),
    ]
    
    name = models.CharField(max_length=200)
    type = models.CharField(max_length=20, choices=SOURCE_TYPES)
    
    # URLs
    url = models.URLField(blank=True)
    rss_feed_url = models.URLField(blank=True)
    
    # YouTube specific
    youtube_channel_id = models.CharField(max_length=100, blank=True)
    youtube_channel_name = models.CharField(max_length=200, blank=True)
    
    # Branding
    logo_url = models.URLField(blank=True)
    description = models.TextField(blank=True)
    
    # Stats
    article_count = models.IntegerField(default=0)
    
    # Flags
    is_active = models.BooleanField(default=True)
    is_verified = models.BooleanField(default=False)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = [
            ['type', 'url'],
            ['type', 'youtube_channel_id']
        ]
    
    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"


class FeedItem(models.Model):
    """Individual content items in feeds"""
    
    CONTENT_TYPES = [
        ('article', 'Article'),
        ('video', 'Video'),
        ('image', 'Image'),
        ('post', 'Social Post'),
    ]
    
    # Content type
    content_type = models.CharField(max_length=20, choices=CONTENT_TYPES, db_index=True)
    
    # Related entities
    entity = models.ForeignKey(
        Entity,
        on_delete=models.CASCADE,
        related_name='feed_items'
    )
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name='feed_items')
    
    # Content
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    content = models.TextField(blank=True)  # Full content if available
    
    # Media
    image_url = models.URLField(blank=True)
    video_url = models.URLField(blank=True)
    thumbnail_url = models.URLField(blank=True)
    
    # Links
    url = models.URLField(unique=True)  # Original article/video URL
    
    # Deduplication
    url_hash = models.CharField(max_length=64, db_index=True)  # SHA256 of URL
    title_hash = models.CharField(max_length=64, db_index=True)  # SHA256 of normalized title
    
    # Author
    author = models.CharField(max_length=200, blank=True)
    
    # Timestamps
    published_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Stats
    views = models.IntegerField(default=0)
    
    # Flags
    is_breaking = models.BooleanField(default=False, db_index=True)
    is_trending = models.BooleanField(default=False, db_index=True)
    
    # Additional data
    metadata = models.JSONField(default=dict)
    
    class Meta:
        ordering = ['-published_at']
        indexes = [
            models.Index(fields=['entity', '-published_at']),
            models.Index(fields=['content_type', '-published_at']),
            models.Index(fields=['url_hash']),
            models.Index(fields=['title_hash', 'published_at']),
            models.Index(fields=['-published_at']),
        ]
    
    def save(self, *args, **kwargs):
        # Generate hashes for deduplication
        if not self.url_hash:
            self.url_hash = hashlib.sha256(self.url.encode()).hexdigest()
        
        if not self.title_hash:
            normalized_title = self.title.lower().strip()
            self.title_hash = hashlib.sha256(normalized_title.encode()).hexdigest()
        
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.title[:50]}... ({self.entity.name})"


class UserSource(models.Model):
    """User-added custom sources"""
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='custom_sources')
    source = models.ForeignKey(Source, on_delete=models.CASCADE)
    entity = models.ForeignKey(Entity, on_delete=models.CASCADE)
    
    # Flags
    is_active = models.BooleanField(default=True)
    
    # Timestamps
    added_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['user', 'source', 'entity']
    
    def __str__(self):
        return f"{self.user.username} -> {self.source.name} for {self.entity.name}"


class HiddenSource(models.Model):
    """Sources hidden by user"""
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='hidden_sources')
    source = models.ForeignKey(Source, on_delete=models.CASCADE)
    
    hidden_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['user', 'source']
    
    def __str__(self):
        return f"{self.user.username} hid {self.source.name}"


class FeedItemView(models.Model):
    """Track which items user has viewed"""
    
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    feed_item = models.ForeignKey(FeedItem, on_delete=models.CASCADE)
    
    viewed_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['user', 'feed_item']
    
    def __str__(self):
        return f"{self.user.username} viewed {self.feed_item.id}"