from django.db import models

class LiveScore(models.Model):
    """Cached live score data"""
    
    SPORTS_CHOICES = [
        ('nba', 'NBA'),
        ('nfl', 'NFL'),
        ('mlb', 'MLB'),
        ('nhl', 'NHL'),
        ('soccer', 'Soccer'),
        ('cricket', 'Cricket'),
    ]
    
    STATUS_CHOICES = [
        ('live', 'Live'),
        ('upcoming', 'Upcoming'),
        ('completed', 'Completed'),
    ]
    
    sport = models.CharField(max_length=20, choices=SPORTS_CHOICES)
    external_id = models.CharField(max_length=100)  # API's game ID
    
    home_team = models.CharField(max_length=200)
    away_team = models.CharField(max_length=200)
    home_logo = models.URLField(blank=True)
    away_logo = models.URLField(blank=True)
    
    home_score = models.IntegerField(null=True, blank=True)
    away_score = models.IntegerField(null=True, blank=True)
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    status_detail = models.CharField(max_length=50, blank=True)  # "2nd Quarter", "Half Time", etc.
    
    start_time = models.DateTimeField()
    
    raw_data = models.JSONField(default=dict)  # Store full API response
    metadata = models.JSONField(default=dict, blank=True)  # Sport-specific metadata (cricket: toss, stadium, etc.)
    
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    
    class Meta:
        unique_together = ['sport', 'external_id']
        ordering = ['-start_time']
        indexes = [
            models.Index(fields=['sport', 'status']),
            models.Index(fields=['start_time']),
        ]
    
    def __str__(self):
        return f"{self.home_team} vs {self.away_team} ({self.sport})"