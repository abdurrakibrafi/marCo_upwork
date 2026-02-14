from django.db import models
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.base_user import BaseUserManager
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.conf import settings


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra_fields)


SOCIAL_AUTH_PROVIDERS = (
    ("google", "Google"),
    ("apple", "Apple"),
    ("facebook", "Facebook"),
    ("github", "GitHub"),
    ("twitter", "Twitter"),
    ("linkedin", "LinkedIn"),
    ("microsoft", "Microsoft"),
    ("amazon", "Amazon"),
    ("discord", "Discord"),
    ("twitch", "Twitch"),
    ("slack", "Slack"),
    ("instagram", "Instagram"),
    ("pinterest", "Pinterest"),
    ("reddit", "Reddit"),
    ("snapchat", "Snapchat"),
    ("tiktok", "TikTok"),
    ("youtube", "YouTube"),
    ("whatsapp", "WhatsApp"),
    ("telegram", "Telegram"),
    ("other", "Other"),
)


class User(AbstractUser):
    email = models.EmailField(unique=True)
    username = models.CharField(max_length=150, unique=False, blank=True, null=True)

    social_auth_provider = models.CharField(
        max_length=50, choices=SOCIAL_AUTH_PROVIDERS, blank=True, null=True
    )

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    is_blocked = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email

    def soft_delete(self):
        """Mark the user as deleted."""
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.is_active = False
        self.save()

    def restore(self):
        """Restore a soft-deleted user."""
        self.is_deleted = False
        self.deleted_at = None
        self.is_active = True
        self.save()

    class Meta:
        db_table = ""
        managed = True
        verbose_name = "User"
        verbose_name_plural = "Users"


class OTP(models.Model):
    PURPOSE_STATUS = [
        ("verification", "Email Verification"),
        ("password_reset", "Password Reset"),
        ("password_setup", "Password Setup"),
        ("email_change", "Email Change"),
        ("other", "Other"),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    otp = models.CharField(max_length=4)
    purpose = models.CharField(max_length=50, choices=PURPOSE_STATUS)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.email} - {self.purpose} OTP"

    def is_valid(self):
        return not self.is_used and self.expires_at > timezone.now()

    class Meta:
        verbose_name = "OTP"
        verbose_name_plural = "OTPs"


class UserProfile(models.Model):
    GENDER_CHOICES = (
        ("male", "Male"),
        ("female", "Female"),
        ("other", "Other"),
        ("prefer_not_to_say", "Prefer not to say"),
    )

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    full_name = models.CharField(max_length=100, blank=True, null=True)
    temp_email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=15, blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)
    gender = models.CharField(
        max_length=20, choices=GENDER_CHOICES, blank=True, null=True
    )

    bio = models.TextField(blank=True, null=True)
    profile_picture = models.ImageField(
            upload_to="identity/profile_pictures/", blank=True, null=True
    )

    profile_completed = models.BooleanField(default=False)

    onboarding_completed = models.BooleanField(default=False)
    last_active = models.DateTimeField(auto_now=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile for {self.user.email}"


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def save_user_profile(sender, instance, **kwargs):
    instance.profile.save()
