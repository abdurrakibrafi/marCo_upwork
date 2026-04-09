import resend
from django.template.loader import render_to_string
from django.conf import settings

resend.api_key = settings.RESEND_API_KEY


def send_otp_email(user, otp_code, purpose):
    subject = (
        "Verification Code" if purpose == "verification" else "Password Reset Code"
    )

    context = {"user": user, "otp": otp_code, "purpose": purpose, "valid_minutes": 10}

    html_content = render_to_string("accounts/otp_email.html", context)
    text_content = render_to_string("accounts/otp_email.txt", context)

    params: resend.Emails.SendParams = {
        "from": "MySportsNest <noreply@mail.mysportsnest.com>",
        "to": [user.email],
        "subject": subject,
        "html": html_content,
        "text": text_content,
    }

    email = resend.Emails.send(params)
    return email