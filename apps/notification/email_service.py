import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


class EmailService:
    """Core domain service for compiling, rendering, and delivering branded HTML emails."""

    @classmethod
    def get_site_url(cls) -> str:
        """Get base application frontend URL from settings or default fallback."""
        return getattr(settings, "FRONTEND_URL", "https://marco.sports").rstrip("/")

    @classmethod
    def send_html_email(
        cls,
        subject: str,
        recipient_email: str,
        template_name: str,
        context: Dict[str, Any],
        from_email: Optional[str] = None,
    ) -> bool:
        """Render and dispatch a multi-part (HTML + plain text fallback) email.

        Args:
            subject (str): Email subject line.
            recipient_email (str): Target recipient email address.
            template_name (str): Relative template path (e.g. 'emails/welcome_email.html').
            context (dict): Context dictionary for template rendering.
            from_email (str, optional): Sender email address. Defaults to settings.DEFAULT_FROM_EMAIL.

        Returns:
            bool: True if email was queued/sent successfully, False otherwise.
        """
        if not recipient_email:
            logger.warning("Attempted to send email with empty recipient_email.")
            return False

        sender = from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@marco.sports")

        # Inject default context variables
        full_context = {
            "site_url": cls.get_site_url(),
            "year": datetime.now().year,
            **context,
        }

        try:
            html_content = render_to_string(template_name, full_context)
            text_content = strip_tags(html_content)

            # 1. Primary delivery via Resend if API key is present
            resend_key = getattr(settings, "RESEND_API_KEY", None)
            if resend_key:
                try:
                    import resend
                    resend.api_key = resend_key
                    resend_from = from_email or "MySportsNest <noreply@updates.mysportsnest.com>"
                    params = {
                        "from": resend_from,
                        "to": [recipient_email],
                        "subject": subject,
                        "html": html_content,
                        "text": text_content,
                    }
                    resend.Emails.send(params)
                    logger.info("Email '%s' sent via Resend to %s", subject, recipient_email)
                    return True
                except Exception as r_err:
                    logger.warning("Resend delivery failed, falling back to Django backend: %s", r_err)

            # 2. Fallback to standard Django mail backend
            msg = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=sender,
                to=[recipient_email],
            )
            msg.attach_alternative(html_content, "text/html")
            msg.send(fail_silently=False)
            logger.info("Email '%s' sent successfully to %s via Django mail", subject, recipient_email)
            return True

        except Exception as exc:
            logger.error("Failed to send email '%s' to %s: %s", subject, recipient_email, exc)
            return False

    @classmethod
    def send_welcome_email(cls, user) -> bool:
        """Send a personalized onboarding welcome email to a new user."""
        if not user or not user.email:
            return False

        user_name = getattr(user, "name", "") or getattr(user, "username", "") or "Sports Fan"

        return cls.send_html_email(
            subject="Welcome to MarCo Sports! 🏆",
            recipient_email=user.email,
            template_name="emails/welcome_email.html",
            context={
                "user_name": user_name,
            },
        )

    @classmethod
    def send_notification_email(cls, notification) -> bool:
        """Send an in-app notification to the user's email inbox."""
        if not notification or not notification.recipient or not notification.recipient.email:
            return False

        type_labels = {
            "match_reminder": "Match Reminder ⚽",
            "score_update": "Live Score Update ⚡",
            "breaking_news": "Breaking Sports Alert 🚨",
            "nest_interaction": "Nest Activity 💬",
            "streak_reminder": "Daily Streak Reminder 🔥",
            "general": "Sports Update 📢",
        }

        label = type_labels.get(notification.notification_type, "Sports Update")
        data = notification.data or {}
        cta_url = data.get("url") or data.get("route")
        if cta_url and not cta_url.startswith("http"):
            cta_url = f"{cls.get_site_url()}{cta_url if cta_url.startswith('/') else '/' + cta_url}"

        return cls.send_html_email(
            subject=f"[{label}] {notification.title}",
            recipient_email=notification.recipient.email,
            template_name="emails/general_notification.html",
            context={
                "title": notification.title,
                "body": notification.body,
                "image_url": notification.image_url,
                "notification_type_label": label,
                "cta_url": cta_url,
            },
        )

    @classmethod
    def send_match_reminder(cls, user, event_data: Dict[str, Any]) -> bool:
        """Send an upcoming game reminder for a followed team."""
        if not user or not user.email:
            return False

        home_team = event_data.get("home_team", "Home Team")
        away_team = event_data.get("away_team", "Away Team")

        return cls.send_html_email(
            subject=f"Match Alert: {home_team} vs {away_team} kicks off soon!",
            recipient_email=user.email,
            template_name="emails/match_reminder.html",
            context=event_data,
        )

    @classmethod
    def send_daily_digest(
        cls,
        user,
        articles: List[Dict[str, Any]],
        upcoming_events: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Send a personalized daily or weekly sports digest."""
        if not user or not user.email:
            return False

        user_name = getattr(user, "name", "") or getattr(user, "username", "") or "Sports Fan"

        return cls.send_html_email(
            subject="Your Daily Sports Briefing — MarCo",
            recipient_email=user.email,
            template_name="emails/daily_digest.html",
            context={
                "user_name": user_name,
                "articles": articles,
                "upcoming_events": upcoming_events or [],
            },
        )
