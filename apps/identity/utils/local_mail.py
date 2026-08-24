from django.template.loader import render_to_string
from django.core.mail import EmailMultiAlternatives
from django.conf import settings


def send_otp_email(user, otp_code, purpose, to_email=None):
    """Send an OTP code via Django's standard SMTP email backend.

    Renders text and HTML email templates with the OTP code and dispatches it
    to the user's primary or requested email address.

    Args:
        user (User): The User instance requesting the OTP.
        otp_code (str): The one-time password string.
        purpose (str): Purpose of the OTP ('verification', 'password_reset', 'email_change', etc.).
        to_email (str, optional): Target email address override. Defaults to user's registered email.
    """
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

    text_content = render_to_string("accounts/otp_email.txt", context)
    html_content = render_to_string("accounts/otp_email.html", context)

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    email.attach_alternative(html_content, "text/html")
    email.send()