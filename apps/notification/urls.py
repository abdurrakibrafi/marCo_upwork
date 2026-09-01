from django.urls import path
from apps.notification.views import (
    DeviceTokenView,
    NotificationDetailDeleteView,
    NotificationListView,
    NotificationMarkReadView,
    NotificationPreferenceView,
)

app_name = "notification"

urlpatterns = [
    # List notifications with unread count & filters
    path("", NotificationListView.as_view(), name="notification-list"),
    # Mark single, multiple, or all as read
    path("mark-read/", NotificationMarkReadView.as_view(), name="notification-mark-read"),
    # Delete individual notification
    path("<int:pk>/", NotificationDetailDeleteView.as_view(), name="notification-delete"),
    # Register/deactivate device push token
    path("device/", DeviceTokenView.as_view(), name="device-token"),
    # View and update preferences
    path("preferences/", NotificationPreferenceView.as_view(), name="notification-preferences"),
]
