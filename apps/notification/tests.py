from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.notification.models import (
    DeviceToken,
    Notification,
    NotificationPreference,
)
from apps.notification.services import NotificationService

User = get_user_model()


class NotificationSystemTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="testuser@example.com",
            password="testpassword123",
        )
        self.client.force_authenticate(user=self.user)

    def test_notification_preference_auto_created(self):
        """Verify NotificationPreference is automatically created for new user."""
        pref = NotificationPreference.objects.filter(user=self.user).first()
        self.assertIsNotNone(pref)
        self.assertTrue(pref.push_enabled)
        self.assertTrue(pref.match_reminders)

    def test_device_token_registration(self):
        """Verify registering a device token via API."""
        url = reverse("notification:device-token")
        payload = {
            "token": "sample_fcm_token_123456",
            "device_type": "android",
            "device_name": "Pixel 7 Pro",
            "is_active": True,
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])

        device_token = DeviceToken.objects.get(token="sample_fcm_token_123456")
        self.assertEqual(device_token.user, self.user)
        self.assertEqual(device_token.device_type, "android")

    def test_notification_list_and_unread_count(self):
        """Verify notification list endpoint includes unread_count and items."""
        # Create 2 unread and 1 read notifications
        NotificationService.send(
            user=self.user,
            title="Match Starting Soon",
            body="Your team plays in 15 minutes!",
            notification_type="match_reminder",
            send_push=False,
        )
        NotificationService.send(
            user=self.user,
            title="Breaking News",
            body="Big transfer update!",
            notification_type="breaking_news",
            send_push=False,
        )
        read_notif = NotificationService.send(
            user=self.user,
            title="Old Score",
            body="Match finished 2-1",
            notification_type="score_update",
            send_push=False,
        )
        read_notif.mark_as_read()

        url = reverse("notification:notification-list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"]["unread_count"], 2)
        self.assertEqual(response.data["data"]["count"], 3)

    def test_mark_notifications_as_read(self):
        """Verify marking notifications as read."""
        n1 = NotificationService.send(
            user=self.user,
            title="Notif 1",
            body="Body 1",
            send_push=False,
        )
        n2 = NotificationService.send(
            user=self.user,
            title="Notif 2",
            body="Body 2",
            send_push=False,
        )

        url = reverse("notification:notification-mark-read")

        # Mark single as read
        res1 = self.client.post(url, {"notification_id": n1.id}, format="json")
        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        self.assertEqual(res1.data["data"]["unread_count"], 1)

        # Mark all remaining as read
        res2 = self.client.post(url, {"all": True}, format="json")
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(res2.data["data"]["unread_count"], 0)

    def test_delete_notification(self):
        """Verify single notification deletion."""
        notif = NotificationService.send(
            user=self.user,
            title="To be deleted",
            body="Delete me",
            send_push=False,
        )
        url = reverse("notification:notification-delete", kwargs={"pk": notif.id})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Notification.objects.filter(id=notif.id).exists())

    def test_notification_preferences_view_and_patch(self):
        """Verify retrieving and updating preferences."""
        url = reverse("notification:notification-preferences")
        get_res = self.client.get(url)
        self.assertEqual(get_res.status_code, status.HTTP_200_OK)
        self.assertTrue(get_res.data["data"]["match_reminders"])

        # Toggle match_reminders off
        patch_res = self.client.patch(url, {"match_reminders": False}, format="json")
        self.assertEqual(patch_res.status_code, status.HTTP_200_OK)
        self.assertFalse(patch_res.data["data"]["match_reminders"])
