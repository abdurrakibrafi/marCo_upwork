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


    # Bookmarks
    path('bookmark/', views.toggle_bookmark, name='toggle_bookmark'),
    path('bookmarks/', views.get_bookmarks, name='get_bookmarks'),
    path('bookmarks/<int:feed_item_id>/', views.remove_bookmark, name='remove_bookmark'),
    
    # Likes
    path('like/', views.toggle_like, name='toggle_like'),
    path('likes/', views.get_likes, name='get_likes'),
    path('likes/<int:feed_item_id>/', views.remove_like, name='remove_like'),
 
    
    # Updates
    path('entity/<int:entity_id>/update/', views.trigger_feed_update, name='trigger_feed_update'),
]