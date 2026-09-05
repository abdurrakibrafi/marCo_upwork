import json
import logging
import os
from typing import Any, Dict, List, Optional, Union

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.notification.models import (
    DeviceToken,
    Notification,
    NotificationPreference,
)

logger = logging.getLogger(__name__)

# Lazy Firebase initialization
_firebase_initialized = False


def get_firebase_app():
    """Lazily initialize and return Firebase Admin App from environment variables or credentials file."""
    global _firebase_initialized
    if _firebase_initialized:
        return True

    try:
        import firebase_admin
        from firebase_admin import credentials

        if firebase_admin._apps:
            _firebase_initialized = True
            return True

        # 1. Check for raw JSON string in env (FIREBASE_CREDENTIALS_JSON)
        cred_json_str = (
            getattr(settings, "FIREBASE_CREDENTIALS_JSON", None)
            or os.getenv("FIREBASE_CREDENTIALS_JSON", "")
        ).strip()

        if cred_json_str:
            try:
                cred_dict = json.loads(cred_json_str)
                # Fix escaped newlines in private key if present in string env
                if "private_key" in cred_dict and "\\n" in cred_dict["private_key"]:
                    cred_dict["private_key"] = cred_dict["private_key"].replace("\\n", "\n")
                cred = credentials.Certificate(cred_dict)
                firebase_admin.initialize_app(cred)
                _firebase_initialized = True
                logger.info("Firebase initialized successfully using FIREBASE_CREDENTIALS_JSON.")
                return True
            except Exception as e:
                logger.error("Failed to parse FIREBASE_CREDENTIALS_JSON: %s", e)

        # 2. Check for file path in env (FIREBASE_CREDENTIALS_PATH)
        cred_path = (
            getattr(settings, "FIREBASE_CREDENTIALS_PATH", None)
            or os.getenv("FIREBASE_CREDENTIALS_PATH", "")
        ).strip()

        if cred_path:
            # Check relative to BASE_DIR if not absolute
            if not os.path.isabs(cred_path) and hasattr(settings, "BASE_DIR"):
                full_path = os.path.join(settings.BASE_DIR, cred_path)
            else:
                full_path = cred_path

            if os.path.exists(full_path):
                cred = credentials.Certificate(full_path)
                firebase_admin.initialize_app(cred)
                _firebase_initialized = True
                logger.info("Firebase initialized successfully using file: %s", full_path)
                return True
            else:
                logger.warning("FIREBASE_CREDENTIALS_PATH file not found at: %s", full_path)

        # 3. Fallback: Default application credentials or empty init
        firebase_admin.initialize_app()
        _firebase_initialized = True
        return True

    except Exception as exc:
        logger.warning("Firebase Admin SDK could not be initialized: %s", exc)
        return False


class FCMService:
    """Service handling Firebase Cloud Messaging push notifications."""

    @staticmethod
    def send_multicast(
        tokens: List[str],
        title: str,
        body: str,
        data: Optional[Dict[str, Any]] = None,
        image_url: Optional[str] = None,
    ) -> Dict[str, int]:
        """Send push notification to multiple device tokens via FCM.

        Returns:
            dict: {"success": count, "failure": count}
        """
        if not tokens:
            return {"success": 0, "failure": 0}

        if not get_firebase_app():
            logger.info("FCM not configured or initialized. Skipping push to %d tokens.", len(tokens))
            return {"success": 0, "failure": len(tokens)}

        try:
            from firebase_admin import messaging

            # Ensure all data values are stringified for FCM
            formatted_data = {}
            if data:
                for k, v in data.items():
                    formatted_data[str(k)] = str(v)

            # Notification payload
            notification = messaging.Notification(
                title=title,
                body=body,
                image=image_url if image_url else None,
            )

            # Multicast message
            message = messaging.MulticastMessage(
                tokens=tokens,
                notification=notification,
                data=formatted_data,
            )

            # Send messages
            response = messaging.send_each_for_multicast(message)

            # Identify invalid / unregistered tokens to deactivate
            invalid_tokens = []
            for idx, resp in enumerate(response.responses):
                if not resp.success and resp.exception:
                    err_code = getattr(resp.exception, "code", "")
                    if "registration-token-not-registered" in str(err_code) or "invalid-argument" in str(err_code):
                        invalid_tokens.append(tokens[idx])

            if invalid_tokens:
                DeviceToken.objects.filter(token__in=invalid_tokens).update(is_active=False)
                logger.info("Deactivated %d invalid device tokens.", len(invalid_tokens))

            logger.info(
                "FCM multicast complete: %d success, %d failure.",
                response.success_count,
                response.failure_count,
            )
            return {"success": response.success_count, "failure": response.failure_count}

        except Exception as exc:
            logger.error("Error sending FCM multicast notification: %s", exc)
            return {"success": 0, "failure": len(tokens)}


class NotificationService:
    """Core domain service for creating and dispatching notifications."""

    @classmethod
    def send(
        cls,
        user,
        title: str,
        body: str,
        notification_type: str = "general",
        data: Optional[Dict[str, Any]] = None,
        image_url: Optional[str] = None,
        send_push: bool = True,
        send_email: bool = True,
    ) -> Optional[Notification]:
        """Create an in-app notification and optionally trigger push and email notifications."""
        if not user or not user.is_authenticated:
            return None

        # Check notification preference
        pref, _ = NotificationPreference.objects.get_or_create(user=user)
        allow_push = pref.is_type_allowed(notification_type)
        allow_email = pref.email_enabled and pref.is_type_allowed(notification_type)

        # Create in-app notification record
        notification = Notification.objects.create(
            recipient=user,
            notification_type=notification_type,
            title=title,
            body=body,
            image_url=image_url,
            data=data or {},
        )

        # Dispatch push notification if allowed
        if send_push and allow_push:
            try:
                from apps.notification.tasks import send_push_notification_task

                send_push_notification_task.delay(notification.id)
            except Exception as exc:
                logger.warning("Failed to queue push notification task: %s", exc)

        # Dispatch email notification if allowed
        if send_email and allow_email:
            try:
                from apps.notification.tasks import send_notification_email_task

                send_notification_email_task.delay(notification.id)
            except Exception as exc:
                logger.warning("Failed to queue email notification task: %s", exc)

        return notification

    @classmethod
    def send_bulk(
        cls,
        users: List[Any],
        title: str,
        body: str,
        notification_type: str = "general",
        data: Optional[Dict[str, Any]] = None,
        image_url: Optional[str] = None,
        send_push: bool = True,
    ) -> int:
        """Efficiently create and dispatch notifications to multiple users."""
        if not users:
            return 0

        notifications_to_create = [
            Notification(
                recipient=user,
                notification_type=notification_type,
                title=title,
                body=body,
                image_url=image_url,
                data=data or {},
            )
            for user in users
            if user and user.is_authenticated
        ]

        with transaction.atomic():
            created = Notification.objects.bulk_create(notifications_to_create)

        if send_push:
            user_ids = [n.recipient_id for n in created]
            try:
                from apps.notification.tasks import send_bulk_push_notification_task

                send_bulk_push_notification_task.delay(
                    user_ids=user_ids,
                    title=title,
                    body=body,
                    notification_type=notification_type,
                    data=data or {},
                    image_url=image_url,
                )
            except Exception as exc:
                logger.warning("Failed to queue bulk push task: %s", exc)

        return len(created)

    @classmethod
    def get_unread_count(cls, user) -> int:
        """Get the count of unread notifications for the user."""
        if not user or not user.is_authenticated:
            return 0
        return Notification.objects.filter(recipient=user, is_read=False).count()

    @classmethod
    def mark_as_read(
        cls,
        user,
        notification_id: Optional[int] = None,
        notification_ids: Optional[List[int]] = None,
        mark_all: bool = False,
    ) -> int:
        """Mark notification(s) as read and return updated unread count."""
        if not user or not user.is_authenticated:
            return 0

        qs = Notification.objects.filter(recipient=user, is_read=False)

        now = timezone.now()
        if mark_all:
            qs.update(is_read=True, read_at=now)
        elif notification_ids:
            qs.filter(id__in=notification_ids).update(is_read=True, read_at=now)
        elif notification_id:
            qs.filter(id=notification_id).update(is_read=True, read_at=now)

        return cls.get_unread_count(user)
