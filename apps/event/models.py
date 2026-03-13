from django.db import models
from apps.entity.models import Entity

class Event(models.Model):
    """Sports events/fixtures"""
    
    STATUS_CHOICES = [
        ('upcoming', 'Upcoming'),
        ('live', 'Live'),
        ('completed', 'Completed'),
        ('postponed', 'Postponed'),
        ('cancelled', 'Cancelled'),
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
    
    # Basic info
    sport = models.CharField(max_length=50, choices=SPORT_CHOICES)
    
    # Teams/Participants
    home_entity = models.ForeignKey(
        Entity,
        on_delete=models.CASCADE,
        related_name='home_events'
    )
    away_entity = models.ForeignKey(
        Entity,
        on_delete=models.CASCADE,
        related_name='away_events',
        null=True,
        blank=True  # For individual sports like F1, golf
    )
    
    # Competition
    league = models.ForeignKey(
        Entity,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='league_events',
        limit_choices_to={'type': 'league'}
    )
    
    # Timing
    start_time = models.DateTimeField(db_index=True)
    end_time = models.DateTimeField(null=True, blank=True)
    
    # Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='upcoming', db_index=True)
    status_detail = models.CharField(max_length=100, blank=True)  # "2nd Quarter", "Half Time"
    
    # Scores
    home_score = models.IntegerField(null=True, blank=True)
    away_score = models.IntegerField(null=True, blank=True)
    
    # Venue
    venue_name = models.CharField(max_length=200, blank=True)
    venue_city = models.CharField(max_length=100, blank=True)
    venue_country = models.CharField(max_length=100, blank=True)
    
    # Broadcast
    broadcaster = models.CharField(max_length=100, blank=True)
    stream_url = models.URLField(blank=True)
    
    # API Integration
    api_source = models.CharField(max_length=50, blank=True)
    external_id = models.CharField(max_length=100, blank=True)
    
    # Additional data
    metadata = models.JSONField(default=dict)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ['api_source', 'external_id']
        ordering = ['start_time']
        indexes = [
            models.Index(fields=['sport', 'status', 'start_time']),
            models.Index(fields=['start_time']),
            models.Index(fields=['home_entity', 'start_time']),
            models.Index(fields=['away_entity', 'start_time']),
        ]
    
    def __str__(self):
        if self.away_entity:
            return f"{self.home_entity.name} vs {self.away_entity.name}"
        return f"{self.home_entity.name} - {self.sport}"


class EventTimeline(models.Model):
    """Timeline events during a match (goals, cards, etc.)"""
    
    EVENT_TYPES = [
        ('goal', 'Goal'),
        ('yellow_card', 'Yellow Card'),
        ('red_card', 'Red Card'),
        ('substitution', 'Substitution'),
        ('penalty', 'Penalty'),
        ('var', 'VAR Decision'),
        ('injury', 'Injury'),
        ('timeout', 'Timeout'),
        ('period_start', 'Period Start'),
        ('period_end', 'Period End'),
    ]
    
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='timeline')
    
    event_type = models.CharField(max_length=50, choices=EVENT_TYPES)
    minute = models.IntegerField()  # Minute of the event
    extra_minute = models.IntegerField(default=0)  # Extra time
    
    team = models.ForeignKey(Entity, on_delete=models.CASCADE, null=True, blank=True)
    player = models.ForeignKey(
        Entity,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='player_events',
        limit_choices_to={'type': 'athlete'}
    )
    
    description = models.TextField(blank=True)
    
    # Additional data
    metadata = models.JSONField(default=dict)
    
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    
    class Meta:
        ordering = ['minute', 'extra_minute']
    
    def __str__(self):
        return f"{self.event} - {self.event_type} at {self.minute}'"


class EventLineup(models.Model):
    """Team lineups for events"""
    
    POSITION_TYPES = [
        ('starting', 'Starting XI'),
        ('substitute', 'Substitute'),
        ('coach', 'Coach'),
    ]
    
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='lineups')
    team = models.ForeignKey(Entity, on_delete=models.CASCADE)
    player = models.ForeignKey(
        Entity,
        on_delete=models.CASCADE,
        related_name='lineup_appearances',
        limit_choices_to={'type': 'athlete'}
    )
    
    position_type = models.CharField(max_length=20, choices=POSITION_TYPES)
    position = models.CharField(max_length=50, blank=True)  # "Forward", "Midfielder"
    jersey_number = models.IntegerField(null=True, blank=True)
    
    # Formation
    grid_position = models.CharField(max_length=10, blank=True)  # e.g., "4:3:2"
    
    # Stats during this match
    metadata = models.JSONField(default=dict)
    
    class Meta:
        unique_together = ['event', 'team', 'player']
    
    def __str__(self):
        return f"{self.player.name} - {self.team.name} ({self.event})"


class EventStatistics(models.Model):
    """Match statistics"""
    
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='statistics')
    team = models.ForeignKey(Entity, on_delete=models.CASCADE)
    
    # Store all stats as JSON for flexibility across sports
    stats = models.JSONField(default=dict)
    # Example for soccer: {"possession": 65, "shots": 15, "shots_on_target": 8}
    # Example for NBA: {"field_goals": 45, "three_pointers": 12, "rebounds": 42}
    
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ['event', 'team']
    
    def __str__(self):
        return f"{self.team.name} stats - {self.event}"


class EventPlayerStats(models.Model):
    """Individual player statistics in a match"""
    
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='player_stats')
    player = models.ForeignKey(
        Entity,
        on_delete=models.CASCADE,
        limit_choices_to={'type': 'athlete'}
    )
    team = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name='team_player_stats')
    
    # Store all stats as JSON for flexibility
    stats = models.JSONField(default=dict)
    # Example for soccer: {"goals": 2, "assists": 1, "shots": 5}
    # Example for NBA: {"points": 32, "rebounds": 8, "assists": 5}
    
    # Quick access fields (denormalized for performance)
    points_or_goals = models.IntegerField(default=0)
    
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ['event', 'player']
    
    def __str__(self):
        return f"{self.player.name} - {self.event}"


class EventHighlight(models.Model):
    """Video highlights for events"""
    
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='highlights')
    
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    
    # Video info
    video_url = models.URLField()
    thumbnail_url = models.URLField(blank=True)
    duration_seconds = models.IntegerField(null=True, blank=True)
    
    # Source
    source = models.CharField(max_length=50, default='youtube')
    external_id = models.CharField(max_length=100, blank=True)  # YouTube video ID
    
    views = models.IntegerField(default=0)
    
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Highlight: {self.title}"