from django.urls import path
from . import views

urlpatterns = [
    path('live/', views.live_scores, name='live_scores'),
    path('live/detail/<int:score_id>/', views.live_score_detail, name='live_score_detail'),
    path('live/sport/<str:sport>/', views.live_scores_by_sport, name='live_scores_by_sport'),
]