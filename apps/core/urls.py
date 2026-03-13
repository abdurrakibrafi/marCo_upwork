from django.urls import path
from apps.core.views import *

urlpatterns = [
    # Individual seed endpoints
    path('seed/nba-teams/',seed_nba_teams, name='seed_nba_teams'),
    path('seed/nba-players/', seed_nba_players, name='seed_nba_players'),
    path('seed/soccer-leagues/', seed_soccer_leagues, name='seed_soccer_leagues'),
    path('seed/soccer-teams/', seed_soccer_teams, name='seed_soccer_teams'),
    path('seed/soccer-players/', seed_soccer_players, name='seed_soccer_players'),
    path('seed/cricket-leagues/', seed_cricket_leagues, name='seed_cricket_leagues'),
    path('seed/epl-teams/', seed_epl_teams, name='seed_epl_teams'),
    path('seed/epl-players/', seed_epl_players,name='seed_epl_players'),

    # Seed everything at once
    path('seed/all/', seed_all, name='seed_all'),
]