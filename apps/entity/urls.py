from django.urls import path
from . import views
from . import views_search

urlpatterns = [
    # ── Discovery ────────────────────────────────────────────────────────
    path('search/', views_search.search_entities, name='search_entities'),
    path('search-ai/', views_search.search_entities_ai, name='search_entities_ai'),
    path('suggest-canonical/', views_search.suggest_canonical_entity, name='suggest_canonical'),
    path('list/', views.list_entities, name='list_entities'),
    path('trending/', views.get_trending, name='get_trending'),

    # ── Entity detail ─────────────────────────────────────────────────────
    path('<int:entity_id>/', views.get_entity_detail, name='entity_detail'),
    path('slug/<slug:slug>/', views.get_entity_by_slug, name='entity_by_slug'),

    # ── UNIVERSAL endpoints (frontend uses these) ──────────────────────────
    path('<int:entity_id>/stats/',     views.get_entity_stats,     name='entity_stats'),
    path('<int:entity_id>/fixtures/',  views.get_entity_fixtures,  name='entity_fixtures'),
    path('<int:entity_id>/roster/',    views.get_entity_roster,    name='entity_roster'),
    path('<int:entity_id>/standings/', views.get_entity_standings, name='entity_standings'),

    # ── OLD specific endpoints (keep, don't delete) ───────────────────────
    path('team/<int:team_id>/stats/',      views.get_team_stats,      name='team_stats'),
    path('team/<int:team_id>/roster/',     views.get_team_roster,     name='team_roster'),
    path('team/<int:team_id>/standings/',  views.get_team_standings,  name='team_standings'),
    path('team/<int:team_id>/fixtures/',   views.get_team_fixtures,   name='team_fixtures'),
    path('athlete/<int:athlete_id>/stats/', views.get_athlete_stats,  name='athlete_stats'),
    path('athlete/<int:athlete_id>/bio/',   views.get_athlete_bio,    name='athlete_bio'),
    
    path('league/<int:league_id>/standings/', views.get_league_standings, name='league_standings'),
    path('league/<int:league_id>/leaders/',   views.get_league_leaders,   name='league_leaders'),
    path('league/<int:league_id>/fixtures/',  views.get_league_fixtures,  name='league_fixtures'),
]