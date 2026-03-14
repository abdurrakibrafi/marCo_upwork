from django.urls import path
from . import views

urlpatterns = [
    # Calendar
    path('nest/', views.get_nest_calendar, name='nest_calendar'),
    path('matches-of-day/', views.get_matches_of_day, name='matches_of_day'),
    path('entity/<int:entity_id>/', views.get_entity_calendar, name='entity_calendar'),

    # Events
    path('events/<int:event_id>/', views.get_event_detail, name='event_detail'),
    path('events/live/', views.get_live_events, name='live_events'),
    path('events/upcoming/', views.get_upcoming_events, name='upcoming_events'),
    path('events/date/<str:date>/', views.get_events_by_date, name='events_by_date'),
]