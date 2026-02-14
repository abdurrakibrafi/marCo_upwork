from django.template.loader import render_to_string
from django.core.mail import EmailMultiAlternatives
from django.conf import settings


def send_otp_email(user, otp_code, purpose):
    subject = (
        "Verification Code" if purpose == "verification" else "Password Reset Code"
    )

    context = {"user": user, "otp": otp_code, "purpose": purpose, "valid_minutes": 10}

    text_content = render_to_string("accounts/otp_email.txt", context)
    html_content = render_to_string("accounts/otp_email.html", context)

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    email.attach_alternative(html_content, "text/html")
    email.send()
