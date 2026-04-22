from django.template.loader import render_to_string
from django.core.mail import EmailMultiAlternatives
from django.conf import settings


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