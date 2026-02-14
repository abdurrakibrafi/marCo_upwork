from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from datetime import timedelta
import random
from .models import OTP, UserProfile
from django.core.mail import send_mail
from django.conf import settings
# from apps.accounts.utils.send_otp_email import send_otp_email
from apps.identity.local_mail import send_otp_email



User = get_user_model()


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    password = serializers.CharField(required=True, style={'input_type': 'password'})

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True, required=True, validators=[validate_password]
    )
    password2 = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = User
        fields = ("email", "password", "password2")

    def validate(self, attrs):
        if attrs["password"] != attrs["password2"]:
            raise serializers.ValidationError(
                {"password": "Password fields didn't match."}
            )
        return attrs

    def create(self, validated_data):
        validated_data.pop("password2")
        user = User.objects.create(
            email=validated_data["email"],
            is_active=False,
        )
        user.set_password(validated_data["password"])
        user.save()

        self.send_verification_otp(user)
        return user

    def send_verification_otp(self, user):
        otp_code = "".join(random.choices("0123456789", k=4))

        otp = OTP.objects.create(   
            user=user,
            otp=otp_code,
            purpose="verification",
            expires_at=timezone.now() + timedelta(minutes=10),
        )   

        send_otp_email(user, otp_code, "verification")  

        return otp


class VerifyEmailSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(max_length=4, min_length=4)

    def validate(self, attrs):
        email = attrs.get("email")
        otp_code = attrs.get("otp")

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise serializers.ValidationError(
                {"email": "User with this email does not exist."}
            )

        try:
            otp = OTP.objects.filter(
                user=user, purpose="verification", otp=otp_code, is_used=False
            ).latest("created_at")

            if not otp.is_valid():
                raise serializers.ValidationError({"otp": "OTP has expired."})

            otp.is_used = True
            otp.save()

            user.is_active = True
            user.save()

            attrs["user"] = user
            return attrs

        except OTP.DoesNotExist:
            raise serializers.ValidationError({"otp": "Invalid OTP."})


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        try:
            User.objects.get(email=value)
        except User.DoesNotExist:
            pass
        return value

    def send_reset_otp(self, email):
        try:
            user = User.objects.get(email=email)

            otp_code = "".join(random.choices("0123456789", k=4))

            otp = OTP.objects.create(
                user=user,
                otp=otp_code,
                purpose="password_reset",
                expires_at=timezone.now() + timedelta(minutes=10),
            )
            
            send_otp_email(user, otp_code, "password_reset")

            return True
        except User.DoesNotExist:
            return True


class PasswordResetOTPVerifySerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(max_length=4, min_length=4)

    def validate(self, attrs):
        email = attrs.get("email")
        otp_code = attrs.get("otp")

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise serializers.ValidationError(
                {"email": "User with this email does not exist."}
            )

        try:
            otp = OTP.objects.filter(
                user=user, purpose="password_reset", otp=otp_code, is_used=False
            ).latest("created_at")

            if not otp.is_valid():
                raise serializers.ValidationError({"otp": "OTP has expired."})

            attrs["user"] = user
            attrs["otp_object"] = otp

            return attrs

        except OTP.DoesNotExist:
            raise serializers.ValidationError({"otp": "Invalid OTP."})


class PasswordResetConfirmSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(max_length=4, min_length=4)
    new_password = serializers.CharField(validators=[validate_password])
    new_password2 = serializers.CharField()

    def validate(self, attrs):
        email = attrs["email"]
        otp_code = attrs["otp"]
        new_password = attrs["new_password"]
        new_password2 = attrs["new_password2"]

        if new_password != new_password2:
            raise serializers.ValidationError(
                {"new_password": "Password fields didn't match."}
            )

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise serializers.ValidationError(
                {"email": "User with this email doesn't exist."}
            )

        try:
            otp = OTP.objects.filter(
                user=user, purpose="password_reset", otp=otp_code, is_used=False
            ).latest("created_at")

            if not otp.is_valid():
                raise serializers.ValidationError({"otp": "OTP has expired."})

            otp.is_used = True
            otp.save()

            user.set_password(new_password)
            user.save()

            attrs["user"] = user
            return attrs

        except OTP.DoesNotExist:
            raise serializers.ValidationError({"otp": "Invalid OTP"})


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True, validators=[validate_password])
    new_password2 = serializers.CharField(required=True)

    def validate(self, attrs):
        if attrs["new_password"] != attrs["new_password2"]:
            raise serializers.ValidationError(
                {"new_password": "Password fields didn't match."}
            )
        return attrs


class SocialLoginSerializer(serializers.Serializer):
    provider = serializers.CharField(required=True)
    access_token = serializers.CharField(required=True)


class ResendOTPSerializer(serializers.Serializer):
    email = serializers.EmailField()
    purpose = serializers.ChoiceField(choices=["verification", "password_reset"])


class AccountSoftDeleteSerializer(serializers.Serializer):
    confirm = serializers.BooleanField(required=True)

    def validate_confirm(self, value):
        if not value:
            raise serializers.ValidationError(
                "You must confirm to delete your account."
            )
        return value

class ParmanentAccountDeleteSerializer(serializers.Serializer):
    confirm = serializers.BooleanField(required=True)

    def validate_confirm(self, value):
        if not value:
            raise serializers.ValidationError(
                "You must confirm to parmanent delete your account."
            )
        return value

class AccountRestoreSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)

    def validate_email(self, value):
        try:
            user = User.objects.get(email=value)
        except User.DoesNotExist:
            raise serializers.ValidationError(  # Fixed typo here
                {"email": "User with this email does not exist."}
            )
        if not user.is_deleted:
            raise serializers.ValidationError({"email": "This account is not deleted."})
        # Store the user in the serializer for later use
        self.user = user
        return value


class ProfileUpdateSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", required=False)

    class Meta:
        model = UserProfile
        fields = ["name", "email", "phone", "date_of_birth", "gender", "bio"]

    def validate_email(self, value):
        user = self.instance.user

        # If email hasn't changed, no need for validation
        if user.email == value:
            return value

        # Check if email is already taken
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("This email is already in use.")

        return value

    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", {})
        email_changed = False
        new_email = None

        # Check if email is being changed
        if "email" in user_data and user_data["email"] != instance.user.email:
            email_changed = True
            new_email = user_data.pop("email")  # Remove email from immediate update

            # Store temporary email
            instance.temp_email = new_email

            # Generate and send OTP
            otp_code = "".join(random.choices("0123456789", k=4))
            OTP.objects.create(
                user=instance.user,
                otp=otp_code,
                purpose="email_change",
                expires_at=timezone.now() + timedelta(minutes=10),
            )

            # Send verification email
            send_mail(
                "Verify Your New Email",
                f"Your verification code is: {otp_code}. Valid for 10 minutes.",
                settings.DEFAULT_FROM_EMAIL,
                [new_email],
                fail_silently=False,
            )

        # Update User model fields (except email)
        if user_data:
            user = instance.user
            for attr, value in user_data.items():
                setattr(user, attr, value)
            user.save()

        # Update UserProfile fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        return instance, email_changed


class VerifyEmailChangeSerializer(serializers.Serializer):
    otp = serializers.CharField(max_length=4, min_length=4)

    def validate(self, attrs):
        user = self.context["request"].user
        otp_code = attrs.get("otp")

        if not user.profile.temp_email:
            raise serializers.ValidationError({"detail": "No email change pending."})

        try:
            otp = OTP.objects.filter(
                user=user, purpose="email_change", otp=otp_code, is_used=False
            ).latest("created_at")

            if not otp.is_valid():
                raise serializers.ValidationError({"otp": "OTP has expired."})

            attrs["otp_object"] = otp
            return attrs

        except OTP.DoesNotExist:
            raise serializers.ValidationError({"otp": "Invalid OTP."})

    def save(self):
        user = self.context["request"].user
        otp = self.validated_data["otp_object"]

        old_email = user.email
        new_email = user.profile.temp_email

        otp.is_used = True
        otp.save()

        user.email = new_email
        user.save()

        user.profile.temp_email = None
        user.profile.save()

        send_mail(
            "Email Address Changed",
            f"Your email has been changed from {old_email} to {new_email}.",
            settings.DEFAULT_FROM_EMAIL,
            [old_email],
            fail_silently=False,
        )

        return user




from apps.identity.models import SOCIAL_AUTH_PROVIDERS


class SocialAuthSerializer(serializers.Serializer):
    email = serializers.EmailField()
    provider = serializers.ChoiceField(choices=SOCIAL_AUTH_PROVIDERS)  # Add validation
    name = serializers.CharField(required=False, allow_blank=True)

    def create_or_login_user(self):
        email = self.validated_data["email"]
        provider = self.validated_data["provider"]
        name = self.validated_data.get("name", "")

        # Check if user exists
        try:
            user = User.objects.get(email=email)
            # User exists, just login
            if not user.is_active:
                user.is_active = True
                user.save()

            # Update provider if not set
            if not user.social_auth_provider:
                user.social_auth_provider = provider
                user.save()

        except User.DoesNotExist:
            # Create new user
            user = User.objects.create(
                email=email,
                is_active=True,
                social_auth_provider=provider,  # Set provider
            )

        # Update profile with social data
        if name and hasattr(user, "profile"):
            user.profile.name = name
            user.profile.save()

        return user


class UserProfileSerializer(serializers.ModelSerializer):
    profile_picture = serializers.ImageField(required=False)
    email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model = UserProfile
        fields = [
            'email', 'name', 'phone', 
            'bio', 'profile_picture', 'profile_completed'
        ]
        
    def validate_date_of_birth(self, value):
        from datetime import date
        if value and value > date.today():
            raise serializers.ValidationError("Date of birth cannot be in the future.")
        return value