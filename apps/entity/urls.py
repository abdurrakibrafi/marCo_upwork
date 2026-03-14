from django.urls import path
from . import views

urlpatterns = [
    # Existing routes
    path('search/', views.search_entities, name='search_entities'),
    path('list/', views.list_entities, name='list_entities'),
    path('trending/', views.get_trending, name='get_trending'),
    path('<int:entity_id>/', views.get_entity_detail, name='entity_detail'),
    path('slug/<slug:slug>/', views.get_entity_by_slug, name='entity_by_slug'),
    
    # Team stats & data
    path('team/<int:team_id>/stats/', views.get_team_stats, name='team_stats'),
    path('team/<int:team_id>/roster/', views.get_team_roster, name='team_roster'),
    path('team/<int:team_id>/standings/', views.get_team_standings, name='team_standings'),
    
    # Athlete data
    path('athlete/<int:athlete_id>/stats/', views.get_athlete_stats, name='athlete_stats'),
    path('athlete/<int:athlete_id>/bio/', views.get_athlete_bio, name='athlete_bio'),
    
    # League data
    path('league/<int:league_id>/standings/', views.get_league_standings, name='league_standings'),
    path('league/<int:league_id>/leaders/', views.get_league_leaders, name='league_leaders'),
    path('league/<int:league_id>/fixtures/', views.get_league_fixtures, name='league_fixtures'),
]