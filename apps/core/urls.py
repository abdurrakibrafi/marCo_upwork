from django.urls import path
from apps.core.views import *

urlpatterns = [
    # Individual seed endpoints
    path('nba-teams/',        seed_nba_teams,       name='seed_nba_teams'),
    path('nba-players/',      seed_nba_players,     name='seed_nba_players'),
    path('soccer-leagues/',   seed_soccer_leagues,  name='seed_soccer_leagues'),
    path('soccer-teams/',     seed_soccer_teams,    name='seed_soccer_teams'),
    path('soccer-players/',   seed_soccer_players,  name='seed_soccer_players'),
    path('cricket-leagues/',  seed_cricket_leagues, name='seed_cricket_leagues'),
    path('epl-teams/',        seed_epl_teams,       name='seed_epl_teams'),
    path('epl-players/',      seed_epl_players,     name='seed_epl_players'),

    # Seed everything at once
    path('all/',              seed_all,             name='seed_all'),
]