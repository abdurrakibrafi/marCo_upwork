"""
apps/source/urls.py
"""
 
from django.urls import path
from . import views
 
urlpatterns = [
    # Search (AI-powered)
    path('search/', views.search_sources, name='source_search'),
 
    # User source management
    path('add/', views.add_source, name='source_add'),
    path('my/', views.list_my_sources, name='source_list'),
    path('<int:source_id>/remove/', views.remove_source, name='source_remove'),
    path('<int:source_id>/refresh/', views.refresh_source, name='source_refresh'),
    path('<int:source_id>/feed/', views.get_source_feed, name='source_feed'),
    path('preview/', views.preview_source, name='source_preview'),
]
 