from django.urls import path
from . import views

urlpatterns = [
    # Main feeds
    path('nest/', views.get_nest_feed, name='nest_feed'),
    path('entity/<int:entity_id>/', views.get_entity_feed, name='entity_feed'),
    path('item/<int:item_id>/', views.get_feed_item, name='feed_item'),
    
    # Special feeds
    path('breaking/', views.get_breaking_news, name='breaking_news'),
    path('trending/', views.get_trending_feed, name='trending_feed'),
    
    # Source management
    path('source/hide/', views.hide_source, name='hide_source'),
    path('source/unhide/', views.unhide_source, name='unhide_source'),
    path('sources/hidden/', views.get_hidden_sources, name='hidden_sources'),
    
    # Updates
    path('entity/<int:entity_id>/update/', views.trigger_feed_update, name='trigger_feed_update'),
]