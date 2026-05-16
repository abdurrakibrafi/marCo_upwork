import logging
import resend
from django.template.loader import render_to_string
from django.conf import settings

logger = logging.getLogger(__name__)

# Set the API key if provided in settings; avoid raising on import when not set.
resend_api_key = getattr(settings, "RESEND_API_KEY", None)
if resend_api_key:
    resend.api_key = resend_api_key
else:
    logger.warning("RESEND_API_KEY is not set; email sending may fail in this environment")


def send_otp_email(user, otp_code, purpose, to_email=None):
    if purpose == "verification":
        subject = "Verification Code"
    elif purpose == "password_reset":
        subject = "Password Reset Code"
    elif purpose == "email_change":
        subject = "Email Change Verification Code"
    else:
        subject = "OTP Code"

    context = {"user": user, "otp": otp_code, "purpose": purpose, "valid_minutes": 10}

    recipient = to_email if to_email else user.email

    html_content = render_to_string("accounts/otp_email.html", context)
    text_content = render_to_string("accounts/otp_email.txt", context)

    params: resend.Emails.SendParams = {
        "from": "MySportsNest <noreply@mail.mysportsnest.com>",
        "to": [recipient],
        "subject": subject,
        "html": html_content,
        "text": text_content,
    }

    email = resend.Emails.send(params)
    return email