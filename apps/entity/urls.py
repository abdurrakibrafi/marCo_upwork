from django.urls import path
from . import views

urlpatterns = [
    path('search/', views.search_entities, name='search_entities'),
    path('trending/', views.get_trending, name='get_trending'),
    path('<int:entity_id>/', views.get_entity_detail, name='entity_detail'),
    path('slug/<slug:slug>/', views.get_entity_by_slug, name='entity_by_slug'),
]