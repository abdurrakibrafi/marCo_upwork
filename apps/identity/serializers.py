from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from datetime import timedelta
import random
from .models import OTP, UserProfile
from django.core.mail import send_mail
from django.conf import settings

# from apps.identity.utils.mail_service import send_otp_email
# from apps.identity.utils.local_mail import send_otp_email
from apps.identity.utils.mail_service import send_otp_email

User = get_user_model()

class LoginSerializer(serializers.Serializer):
    """Serializer for authenticating users via email and password credentials."""

    email = serializers.EmailField(required=True)
    password = serializers.CharField(required=True, style={'input_type': 'password'})

class RegisterSerializer(serializers.ModelSerializer):
    """Serializer for user registration, profile initialization, and verification OTP generation."""

    full_name = serializers.CharField(required=True)
    password = serializers.CharField(
        write_only=True, required=True, validators=[validate_password]
    )
    password2 = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = User
        fields = ("email", "password", "password2", "full_name")

    def validate(self, attrs):
        """Validate that password and password confirmation match.

        Args:
            attrs (dict): Input attributes.

        Returns:
            dict: Validated attributes.

        Raises:
            serializers.ValidationError: If passwords do not match.
        """
        if attrs["password"] != attrs["password2"]:
            raise serializers.ValidationError(
                {"password": "Password fields didn't match."}
            )
        return attrs

    def create(self, validated_data):
        """Create an inactive user instance, initialize profile, and send verification OTP.

        Args:
            validated_data (dict): Validated input data.

        Returns:
            User: The newly created inactive User instance.
        """
        validated_data.pop("password2")
        full_name = validated_data.pop("full_name", "")
        
        user = User.objects.create(
            email=validated_data["email"],
            is_active=False,
        )
        user.set_password(validated_data["password"])
        user.save()

        # Save full_name to UserProfile
        profile, created = UserProfile.objects.get_or_create(user=user)
        profile.full_name = full_name
        profile.save()

        self.send_verification_otp(user)
        return user

    def send_verification_otp(self, user):
        """Generate and email a 4-digit verification OTP to the user.

        Args:
            user (User): The target user to verify.

        Returns:
            OTP: The created OTP model instance.
        """
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
    """Serializer for verifying user email address using a 4-digit OTP code."""

    email = serializers.EmailField()
    otp = serializers.CharField(max_length=4, min_length=4)

    def validate(self, attrs):
        """Validate the submitted OTP code and activate the user account upon success.

        Args:
            attrs (dict): Input attributes containing email and otp.

        Returns:
            dict: Validated attributes containing the activated user instance.

        Raises:
            serializers.ValidationError: If user is not found, OTP is invalid, or OTP expired.
        """
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
    """Serializer for requesting a password reset OTP."""

    email = serializers.EmailField()

    def validate_email(self, value):
        """Validate email format (avoids leaking whether email exists).

        Args:
            value (str): Email address.

        Returns:
            str: Validated email address.
        """
        try:
            User.objects.get(email=value)
        except User.DoesNotExist:
            pass
        return value

    def send_reset_otp(self, email):
        """Generate and email a password reset OTP to the user if the account exists.

        Args:
            email (str): Target email address.

        Returns:
            bool: Always returns True to prevent user enumeration.
        """
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
    """Serializer to verify that a password reset OTP is valid before changing the password."""

    email = serializers.EmailField()
    otp = serializers.CharField(max_length=4, min_length=4)

    def validate(self, attrs):
        """Validate that the password reset OTP exists and is not expired.

        Args:
            attrs (dict): Input data with email and otp.

        Returns:
            dict: Validated dictionary containing user and otp_object.

        Raises:
            serializers.ValidationError: If user does not exist or OTP is invalid/expired.
        """
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
    """Serializer to confirm password reset with a valid OTP and new password."""

    email = serializers.EmailField()
    otp = serializers.CharField(max_length=4, min_length=4)
    new_password = serializers.CharField(validators=[validate_password])
    new_password2 = serializers.CharField()

    def validate(self, attrs):
        """Validate matching passwords and OTP validity, then apply new password.

        Args:
            attrs (dict): Input attributes including email, otp, new_password, new_password2.

        Returns:
            dict: Validated attributes containing user instance.

        Raises:
            serializers.ValidationError: If passwords mismatch or OTP is invalid/expired.
        """
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
    """Serializer for authenticated users to change their current password."""

    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True, validators=[validate_password])
    new_password2 = serializers.CharField(required=True)

    def validate(self, attrs):
        """Ensure new passwords match.

        Args:
            attrs (dict): Input attributes.

        Returns:
            dict: Validated attributes.

        Raises:
            serializers.ValidationError: If new password confirmation does not match.
        """
        if attrs["new_password"] != attrs["new_password2"]:
            raise serializers.ValidationError(
                {"new_password": "Password fields didn't match."}
            )
        return attrs


class SocialLoginSerializer(serializers.Serializer):
    """Serializer for social authentication with third-party access token."""

    provider = serializers.CharField(required=True)
    access_token = serializers.CharField(required=True)


class ResendOTPSerializer(serializers.Serializer):
    """Serializer for requesting re-dispatch of a verification or password reset OTP."""

    email = serializers.EmailField()
    purpose = serializers.ChoiceField(choices=["verification", "password_reset"])


class AccountSoftDeleteSerializer(serializers.Serializer):
    """Serializer to confirm soft deletion of user account."""

    confirm = serializers.BooleanField(required=True)

    def validate_confirm(self, value):
        """Ensure deletion confirmation flag is True.

        Args:
            value (bool): Confirmation status.

        Returns:
            bool: True if confirmed.

        Raises:
            serializers.ValidationError: If confirm is False.
        """
        if not value:
            raise serializers.ValidationError(
                "You must confirm to delete your account."
            )
        return value

class ParmanentAccountDeleteSerializer(serializers.Serializer):
    """Serializer to confirm permanent deletion of user account."""

    confirm = serializers.BooleanField(required=True)

    def validate_confirm(self, value):
        """Ensure permanent deletion confirmation flag is True.

        Args:
            value (bool): Confirmation status.

        Returns:
            bool: True if confirmed.

        Raises:
            serializers.ValidationError: If confirm is False.
        """
        if not value:
            raise serializers.ValidationError(
                "You must confirm to parmanent delete your account."
            )
        return value

class AccountRestoreSerializer(serializers.Serializer):
    """Serializer for restoring a previously soft-deleted account."""

    email = serializers.EmailField(required=True)

    def validate_email(self, value):
        """Validate that user exists and is currently soft-deleted.

        Args:
            value (str): Email address of the account to restore.

        Returns:
            str: Validated email address.

        Raises:
            serializers.ValidationError: If user does not exist or is not deleted.
        """
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
    """Serializer for updating user profile attributes and managing email change OTP flow."""

    email = serializers.EmailField(source="user.email", required=False)

    class Meta:
        model = UserProfile
        fields = ["full_name", "email", "phone", "date_of_birth", "gender", "bio"]

    def validate_email(self, value):
        """Validate that the new email is not already registered by another user.

        Args:
            value (str): New email address.

        Returns:
            str: Validated email.

        Raises:
            serializers.ValidationError: If email is already in use by another account.
        """
        user = self.instance.user

        # If email hasn't changed, no need for validation
        if user.email == value:
            return value

        # Check if email is already taken
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("This email is already in use.")

        return value

    def update(self, instance, validated_data):
        """Update profile fields and dispatch verification OTP if email was modified.

        Args:
            instance (UserProfile): The existing profile instance.
            validated_data (dict): Validated update attributes.

        Returns:
            tuple: (instance, email_changed) where email_changed is a boolean flag.
        """
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
    """Serializer to verify email change OTP and update user's primary email address."""

    otp = serializers.CharField(max_length=4, min_length=4)

    def validate(self, attrs):
        """Validate OTP for pending email change.

        Args:
            attrs (dict): Input data containing otp.

        Returns:
            dict: Validated data containing otp_object.

        Raises:
            serializers.ValidationError: If no pending change or OTP is invalid/expired.
        """
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
        """Finalize email change, update User model, and send confirmation notice.

        Returns:
            User: Updated user instance.
        """
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


class ChangeEmailSerializer(serializers.Serializer):
    """Serializer for initiating an email change request."""

    new_email = serializers.EmailField(required=True)

    def validate_new_email(self, value):
        """Validate that new email is different from current and not already taken.

        Args:
            value (str): Proposed new email address.

        Returns:
            str: Validated email address.

        Raises:
            serializers.ValidationError: If email is same as current or already taken.
        """
        user = self.context['request'].user
        
        # Check if email is same as current
        if user.email == value:
            raise serializers.ValidationError("New email must be different from current email.")
        
        # Check if email is already taken
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("This email is already in use.")
        
        return value



class ResendEmailChangeOTPSerializer(serializers.Serializer):
    """Serializer for resending email change OTP to the pending email address."""

    new_email = serializers.EmailField(required=True)

    def validate_new_email(self, value):
        """Validate that new_email matches the pending email change.

        Args:
            value (str): The email address to resend to.

        Returns:
            str: Validated email.

        Raises:
            serializers.ValidationError: If no pending change or mismatch occurs.
        """
        user = self.context['request'].user
        
        # Check if user has a pending email change
        if not hasattr(user, 'profile') or not user.profile.temp_email:
            raise serializers.ValidationError("No pending email change found.")
        
        # Check if the new_email matches the pending change
        if user.profile.temp_email != value:
            raise serializers.ValidationError("Email does not match pending change.")
        
        return value


class SocialAuthSerializer(serializers.Serializer):
    """Serializer to authenticate or register users via OAuth / social providers."""

    email = serializers.EmailField()
    provider = serializers.ChoiceField(choices=SOCIAL_AUTH_PROVIDERS)  # Add validation
    full_name = serializers.CharField(required=False, allow_blank=True)

    def create_or_login_user(self):
        """Retrieve existing user or create a new user record associated with the social provider.

        Returns:
            User: Authenticated or newly created user instance.
        """
        email = self.validated_data["email"]
        provider = self.validated_data["provider"]
        full_name = self.validated_data.get("full_name", "")

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
        if full_name and hasattr(user, "profile"):
            user.profile.full_name = full_name
            user.profile.save()

        return user


class UserProfileSerializer(serializers.ModelSerializer):
    """Serializer for retrieving and updating user profile details."""

    profile_picture = serializers.ImageField(required=False, use_url=False)
    email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model = UserProfile
        fields = [
            'email', 'full_name', 'phone', 
            'bio', 'profile_picture', 'profile_completed'
        ]
        
    def to_representation(self, instance):
        """Format and ensure leading slash normalization for media profile pictures.

        Args:
            instance (UserProfile): The profile instance being serialized.

        Returns:
            dict: Serialized profile data.
        """
        data = super().to_representation(instance)
        profile_picture = data.get('profile_picture')
        if profile_picture:
            data['profile_picture'] = f"/media/{profile_picture.lstrip('/')}"
        return data

    def validate_date_of_birth(self, value):
        """Ensure date of birth is not in the future.

        Args:
            value (date): Date of birth.

        Returns:
            date: Validated date of birth.

        Raises:
            serializers.ValidationError: If date of birth is in the future.
        """
        from datetime import date
        if value and value > date.today():
            raise serializers.ValidationError("Date of birth cannot be in the future.")
        return value
    
