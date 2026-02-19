from django.urls import path
from . import views

urlpatterns = [
    # Nest management
    path('', views.get_user_nest, name='get_nest'),
    path('add/', views.add_to_nest, name='add_to_nest'),
    path('remove/', views.remove_from_nest, name='remove_from_nest'),
    path('summary/', views.get_nest_summary, name='nest_summary'),
    
    # User preferences
    path('preferences/', views.user_preferences, name='user_preferences'),
    
    # Search history
    path('search/recent/', views.recent_searches, name='recent_searches'),
    path('search/save/', views.save_search, name='save_search'),
]