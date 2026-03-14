from django.db import models
from django.utils.text import slugify

class Entity(models.Model):
    """Base model for all sports entities"""
    
    TYPE_CHOICES = [
        ('team', 'Team'),
        ('athlete', 'Athlete'),
        ('league', 'League'),
    ]
    
    SPORT_CHOICES = [
        ('basketball', 'Basketball'),
        ('football', 'American Football'),
        ('soccer', 'Soccer'),
        ('baseball', 'Baseball'),
        ('hockey', 'Hockey'),
        ('cricket', 'Cricket'),
        ('tennis', 'Tennis'),
        ('f1', 'Formula 1'),
        ('mma', 'MMA'),
        ('golf', 'Golf'),
    ]
    
    # Core fields
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=250, unique=True, blank=True)
    sport = models.CharField(max_length=50, choices=SPORT_CHOICES)
    
    # API Integration
    api_source = models.CharField(max_length=50, blank=True)  # 'balldontlie', 'api_sports', etc.
    external_id = models.CharField(max_length=100, blank=True)
    
    # Media
    logo_url = models.URLField(blank=True)
    cover_image_url = models.URLField(blank=True)
    
    # Metadata
    description = models.TextField(blank=True)
    country = models.CharField(max_length=100, blank=True)
    founded_year = models.IntegerField(null=True, blank=True)
    
    # Stats
    follower_count = models.IntegerField(default=0)
    
    # Data storage
    metadata = models.JSONField(default=dict)  # Flexible storage for API-specific data
    
    # Flags
    is_active = models.BooleanField(default=True)
    has_api_data = models.BooleanField(default=False)  # True if supported by API
    rss_discovery_done = models.BooleanField(default=False)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-follower_count', 'name']
        indexes = [
            models.Index(fields=['type', 'sport']),
            models.Index(fields=['api_source', 'external_id']),
            models.Index(fields=['slug']),
        ]
    
    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(f"{self.name}-{self.type}")
            slug = base_slug
            counter = 1
            # Keep incrementing until we find a unique slug
            while Entity.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"


class Team(models.Model):
    """Extended model for teams"""
    
    entity = models.OneToOneField(Entity, on_delete=models.CASCADE, related_name='team_details')
    
    # Team-specific fields
    league = models.ForeignKey(
        Entity, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='teams',
        limit_choices_to={'type': 'league'}
    )
    
    venue_name = models.CharField(max_length=200, blank=True)
    venue_city = models.CharField(max_length=100, blank=True)
    venue_capacity = models.IntegerField(null=True, blank=True)
    
    # Stats
    total_wins = models.IntegerField(default=0)
    total_losses = models.IntegerField(default=0)
    win_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    
    # Social
    website_url = models.URLField(blank=True)
    twitter_handle = models.CharField(max_length=100, blank=True)
    youtube_channel_id = models.CharField(max_length=100, blank=True)
    
    def __str__(self):
        return self.entity.name


class Athlete(models.Model):
    """Extended model for athletes"""
    
    entity = models.OneToOneField(Entity, on_delete=models.CASCADE, related_name='athlete_details')
    
    # Personal info
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    date_of_birth = models.DateField(null=True, blank=True)
    nationality = models.CharField(max_length=100, blank=True)
    
    # Physical attributes
    height_cm = models.IntegerField(null=True, blank=True)
    weight_kg = models.IntegerField(null=True, blank=True)
    
    # Career
    current_team = models.ForeignKey(
        Entity,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='current_athletes',
        limit_choices_to={'type': 'team'}
    )
    position = models.CharField(max_length=50, blank=True)
    jersey_number = models.IntegerField(null=True, blank=True)
    
    # Contract (if available)
    salary_usd = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    contract_years_remaining = models.IntegerField(null=True, blank=True)
    
    # Social
    twitter_handle = models.CharField(max_length=100, blank=True)
    instagram_handle = models.CharField(max_length=100, blank=True)
    
    def __str__(self):
        return f"{self.first_name} {self.last_name}"
    
    @property
    def age(self):
        if self.date_of_birth:
            from datetime import date
            today = date.today()
            return today.year - self.date_of_birth.year - (
                (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day)
            )
        return None


class League(models.Model):
    """Extended model for leagues"""
    
    entity = models.OneToOneField(Entity, on_delete=models.CASCADE, related_name='league_details')
    
    # League info
    current_season = models.CharField(max_length=20, blank=True)
    number_of_teams = models.IntegerField(default=0)
    
    # Competition format
    has_playoffs = models.BooleanField(default=False)
    has_divisions = models.BooleanField(default=False)
    
    def __str__(self):
        return self.entity.name


class EntityStats(models.Model):
    """Cached statistics for entities"""
    
    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name='stats')
    
    season = models.CharField(max_length=20)
    stat_type = models.CharField(max_length=50)  # 'season', 'career', 'game_average'
    
    stats_data = models.JSONField(default=dict)  # Flexible storage for different sports
    
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ['entity', 'season', 'stat_type']
        ordering = ['-season']
    
    def __str__(self):
        return f"{self.entity.name} - {self.season} ({self.stat_type})"