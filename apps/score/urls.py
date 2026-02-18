from django.urls import path
from . import views

urlpatterns = [
    path('live/', views.live_scores, name='live_scores'),
    path('live/<str:sport>/', views.live_scores_by_sport, name='live_scores_by_sport'),
]