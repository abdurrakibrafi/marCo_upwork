from django.urls import path
from apps.core.views import *
 
urlpatterns = [
    # ── Step 1: Seed all leagues from API (run once) ──
    path('seed/all-leagues/',   seed_all_leagues,   name='seed_all_leagues'),
 
    # ── Step 2: Seed teams for all leagues in DB (batch, use offset) ──
    path('seed/all-teams/',     seed_all_teams,     name='seed_all_teams'),
 
    # ── Step 3: Seed player squads for all teams in DB (batch, use offset) ──
    path('seed/all-players/',   seed_all_players,   name='seed_all_players'),
 
    # ── NBA ──
    path('seed/nba-teams/',     seed_nba_teams,     name='seed_nba_teams'),
    path('seed/nba-players/',   seed_nba_players,   name='seed_nba_players'),
 
    # ── Cricket ──
    path('seed/cricket-leagues/', seed_cricket_leagues, name='seed_cricket_leagues'),
 
    # ── Wipe everything (testing only) ──
    path('seed/delete-all/',         delete_all_entities, name='delete_all_entities'),
]
 