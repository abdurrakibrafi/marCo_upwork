from rest_framework import generics, status, permissions
from rest_framework.response import Response
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.views import APIView
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.hashers import check_password
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from allauth.socialaccount.providers.apple.views import AppleOAuth2Adapter
from allauth.socialaccount.providers.oauth2.client import OAuth2Client
from dj_rest_auth.registration.views import SocialLoginView
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

from apps.feed.models import Bookmark
from apps.nest.models import UserNest
from .serializers import (
    RegisterSerializer,
    ResendEmailChangeOTPSerializer,
    VerifyEmailSerializer,
    PasswordResetRequestSerializer,
    PasswordResetOTPVerifySerializer,
    PasswordResetConfirmSerializer,
    ChangePasswordSerializer,
    SocialLoginSerializer,
    ResendOTPSerializer,
    AccountSoftDeleteSerializer,
    AccountRestoreSerializer,
    ProfileUpdateSerializer,
    VerifyEmailChangeSerializer,
    LoginSerializer,
    SocialAuthSerializer,
    UserProfileSerializer ,
    ParmanentAccountDeleteSerializer,
    ChangeEmailSerializer
)
from apps.identity.utils.mail_service import send_otp_email
# from apps.identity.utils.local_mail import send_otp_email

# from apps.notification.services.notification_service import NotificationService
from rest_framework.generics import RetrieveUpdateAPIView
from django.conf import settings
from apps.core.utils.mixins import BaseResponseMixin
from apps.identity.models import OTP, UserProfile  
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser

import random

from datetime import timedelta
from django.utils import timezone

User = get_user_model()

class RegisterView(BaseResponseMixin, generics.CreateAPIView):
    """API view to register a new user or resend verification OTP for unverified accounts."""

    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = (permissions.AllowAny,)
    throttle_classes = [AnonRateThrottle]

    def create(self, request, *args, **kwargs):
        """Process registration request, check if user exists, and send verification OTP.

        Args:
            request (Request): HTTP request with registration data.

        Returns:
            Response: Success response with registration or OTP resend status.
        """
        email = request.data.get("email")
        
        # Check if user exists
        if User.objects.filter(email=email).exists():
            user = User.objects.get(email=email)
            
            if user.is_active:
                # User is already verified
                return self.success_response(
                    data={
                        "email": email,
                        "is_sent": False
                    },
                    message="User with this email already exists and is verified.",
                    status_code=status.HTTP_200_OK
                )
            else:
                # User exists but not verified - resend OTP
                serializer = self.get_serializer()
                serializer.send_verification_otp(user)
                return self.success_response(
                    data={
                        "email": email,
                        "is_sent": True
                    },
                    message="User not verified. New OTP sent to your email.",
                    status_code=status.HTTP_200_OK
                )
        
        # New user - create normally
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return self.success_response(
            data={
                "email": user.email,
                "is_sent": True
            },
            message="Registration successful. Please check your email for verification code.",
            status_code=status.HTTP_201_CREATED
        )
    

class VerifyEmailView(BaseResponseMixin, generics.GenericAPIView):
    """API view to verify user email address via OTP and activate account."""

    serializer_class = VerifyEmailSerializer
    permission_classes = (permissions.AllowAny,)
    throttle_classes = [AnonRateThrottle]

    def post(self, request):
        """Verify email OTP and return JWT access/refresh token pair.

        Args:
            request (Request): HTTP request with email and otp.

        Returns:
            Response: JSON response containing JWT tokens upon successful verification.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
                "message": "Email verified successfully",
            },
            status=status.HTTP_200_OK,
        )


class ResendOTPView(BaseResponseMixin, generics.GenericAPIView):
    """API view to re-send OTP code for verification or password reset."""

    serializer_class = ResendOTPSerializer
    permission_classes = (permissions.AllowAny,)
    throttle_classes = [AnonRateThrottle]

    def post(self, request):
        """Re-generate and send OTP code to user's email.

        Args:
            request (Request): HTTP request with email and purpose.

        Returns:
            Response: Standard success response.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        purpose = serializer.validated_data["purpose"]

        try:
            user = User.objects.get(email=email)
            
            # CHECK IF USER IS BLOCKED
            if user.is_blocked:
                return self.error_response(
                    message="Your account has been suspended. Please contact support.",
                    status_code=status.HTTP_403_FORBIDDEN
                )

            if purpose == "verification":
                if user.is_active:
                    return self.error_response(
                        message="Email is already verified",
                        status_code=status.HTTP_400_BAD_REQUEST
                    )
                register_serializer = RegisterSerializer()
                register_serializer.send_verification_otp(user)
            elif purpose == "password_reset":
                reset_serializer = PasswordResetRequestSerializer()
                reset_serializer.send_reset_otp(email)

            return self.success_response(
                message="OTP has been sent to your email"
            )

        except User.DoesNotExist:
            # Return success message for security (don't reveal if email exists)
            return self.success_response(
                message="If the email exists, an OTP has been sent"
            )


class LoginView(BaseResponseMixin, generics.GenericAPIView):
    """API view for standard email/password user authentication."""

    serializer_class = LoginSerializer
    permission_classes = (permissions.AllowAny,)
    throttle_classes = [AnonRateThrottle]

    def post(self, request):
        """Authenticate user credentials and issue JWT tokens.

        Args:
            request (Request): HTTP request with email and password.

        Returns:
            Response: Response with JWT tokens and user summary if valid.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        email = serializer.validated_data.get("email")
        password = serializer.validated_data.get("password")

        # First check if user exists and is blocked
        try:
            user = User.objects.get(email=email)
            
            # CHECK IF USER IS BLOCKED - This is where we stop them! 🛑
            if user.is_blocked:
                return self.error_response(
                    message="Your account has been suspended. Please contact support.",
                    status_code=status.HTTP_403_FORBIDDEN
                )
                
        except User.DoesNotExist:
            pass

        user = authenticate(username=email, password=password)

        if user is not None:
            # Double check after authentication (extra safety)
            if user.is_blocked:
                return self.error_response(
                    message="Your account has been suspended. Please contact support.",
                    status_code=status.HTTP_403_FORBIDDEN
                )
                
            if not user.is_active:
                return self.error_response(
                    message="Please verify your email before logging in",
                    status_code=status.HTTP_401_UNAUTHORIZED
                )

            refresh = RefreshToken.for_user(user)
            return self.success_response(
                data={
                    "refresh": str(refresh),
                    "access": str(refresh.access_token),
                    "user": {
                        "id": user.id,
                        "email": user.email,
                    }
                },
                message="Login successful"
            )
        else:
            return self.error_response(
                message="Invalid email or password",
                status_code=status.HTTP_401_UNAUTHORIZED
            )

class LogoutView(BaseResponseMixin, generics.GenericAPIView):
    """API view to log out user by blacklisting their JWT refresh token."""

    permission_classes = (permissions.IsAuthenticated,)
    throttle_classes = [UserRateThrottle]

    def post(self, request):
        """Blacklist refresh token to invalidate session.

        Args:
            request (Request): HTTP request with refresh token.

        Returns:
            Response: Logout status response.
        """
        try:
            refresh_token = request.data.get("refresh")
            if not refresh_token:
                return self.error_response(
                    message="Refresh token is required",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            
            token = RefreshToken(refresh_token)
            token.blacklist()
            
            return self.success_response(
                message="Logged out successfully",
                status_code=status.HTTP_200_OK
            )
        except Exception as e:
            return self.error_response(
                message="Invalid or expired refresh token",
                status_code=status.HTTP_400_BAD_REQUEST
            )
        
class PasswordResetRequestView(BaseResponseMixin, generics.GenericAPIView):
    """API view to initiate password reset by requesting an OTP."""

    serializer_class = PasswordResetRequestSerializer
    permission_classes = (permissions.AllowAny,)
    throttle_classes = [AnonRateThrottle]

    def post(self, request):
        """Process reset request and send OTP email.

        Args:
            request (Request): HTTP request with target email.

        Returns:
            Response: Generic confirmation response.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        serializer.send_reset_otp(email)

        return self.success_response(
            message="If the email exists, a password reset OTP has been sent",
            status_code=status.HTTP_200_OK,
        )

class PasswordResetOTPVerifyView(BaseResponseMixin, generics.GenericAPIView):
    """API view to verify password reset OTP code before setting new password."""

    serializer_class = PasswordResetOTPVerifySerializer
    permission_classes = (permissions.AllowAny,)
    throttle_classes = [AnonRateThrottle]

    def post(self, request):
        """Verify OTP validity.

        Args:
            request (Request): HTTP request containing email and OTP.

        Returns:
            Response: Confirmation response if OTP is valid.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return self.success_response(
            data={"email": serializer.validated_data["email"]},
            message="OTP verified successfully",
            status_code=status.HTTP_200_OK,
        )


class PasswordResetConfirmView(BaseResponseMixin, generics.GenericAPIView):
    """API view to finalize password reset using verified OTP and new password."""

    serializer_class = PasswordResetConfirmSerializer
    permission_classes = (permissions.AllowAny,)
    throttle_classes = [AnonRateThrottle]

    def post(self, request):
        """Set new password for user after verifying OTP.

        Args:
            request (Request): HTTP request with email, OTP, and new password.

        Returns:
            Response: Password reset success response.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return self.success_response(
            message="Password has been reset successfully",
            status_code=status.HTTP_200_OK,
        )
    


class ChangePasswordView(BaseResponseMixin, generics.GenericAPIView):
    """API view for logged-in users to change their account password."""

    serializer_class = ChangePasswordSerializer
    permission_classes = (permissions.IsAuthenticated,)
    throttle_classes = [UserRateThrottle]

    def post(self, request):
        """Validate old password and set new password.

        Args:
            request (Request): HTTP request with old and new password.

        Returns:
            Response: Password change status response.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        old_password = serializer.validated_data["old_password"]
        new_password = serializer.validated_data["new_password"]

        if not check_password(old_password, user.password):
            return self.error_response(
                message="Current password is incorrect",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        user.set_password(new_password)
        user.save()

        return self.success_response(
            message="Password changed successfully"
        )


class GoogleLogin(SocialLoginView):
    """OAuth2 login callback view for Google authentication."""

    adapter_class = GoogleOAuth2Adapter
    client_class = OAuth2Client
    callback_url = settings.GOOGLE_CALLBACK_URL


class AppleLogin(SocialLoginView):
    """OAuth2 login callback view for Apple authentication."""

    adapter_class = AppleOAuth2Adapter
    client_class = OAuth2Client
    callback_url = settings.APPLE_CALLBACK_URL


class ParmanentAccountDeleteView(BaseResponseMixin, APIView):
    """API view to permanently remove a user account and all associated records."""

    permission_classes = (permissions.IsAuthenticated,)
    throttle_classes = [UserRateThrottle]

    def post(self, request):
        """Permanently delete authenticated user account.

        Args:
            request (Request): HTTP request confirming deletion.

        Returns:
            Response: Deletion confirmation response.
        """
        serializer = ParmanentAccountDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        user.delete()

        return self.success_response(
            message="Account has been deleted successfully"
        )

class AccountRestoreView(BaseResponseMixin, APIView):
    """Admin API view to restore a soft-deleted user account."""

    permission_classes = (permissions.IsAdminUser,)

    def post(self, request):
        """Restore soft-deleted account by email.

        Args:
            request (Request): HTTP request containing target email.

        Returns:
            Response: Restore status response.
        """
        serializer = AccountRestoreSerializer(data=request.data)
        if not serializer.is_valid():
            return self.error_response(
                message="Invalid data provided",
                errors=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST
            )

        user = serializer.user
        user.restore()

        return self.success_response(
            data={"email": user.email},
            message="Account restored successfully"
        )

class ProfileUpdateView(BaseResponseMixin, generics.UpdateAPIView):
    """API view to update user profile information and manage email change requests."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProfileUpdateSerializer

    def get_object(self):
        """Retrieve profile associated with the authenticated user."""
        return self.request.user.profile

    def update(self, request, *args, **kwargs):
        """Update profile fields and notify user if an email change OTP was dispatched.

        Args:
            request (Request): HTTP request containing updated profile attributes.

        Returns:
            Response: Response containing updated profile data.
        """
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        instance, email_changed = serializer.save()

        response_data = self.get_serializer(instance).data

        if email_changed:
            response_data["email_verification_pending"] = True
            message = (
                "Profile updated. Please verify your new email address with the code sent."
            )
        else:
            message = "Profile updated successfully."

        return self.success_response(data=response_data, message=message)


class VerifyEmailChangeView(BaseResponseMixin, generics.GenericAPIView):
    """API view to verify OTP and finalize email address update."""

    permission_classes = [IsAuthenticated]
    serializer_class = VerifyEmailChangeSerializer

    def post(self, request):
        """Verify email change OTP and swap user primary email.

        Args:
            request (Request): HTTP request containing email change OTP.

        Returns:
            Response: Success response with old and new email addresses.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        otp_code = serializer.validated_data["otp"]

        try:
            try:
                otp = OTP.objects.get(
                    user=request.user,
                    otp=otp_code,
                    purpose="email_change",
                    is_used=False,
                )

                if not otp.is_valid():
                    return self.bad_request_response(
                        message="OTP is invalid or expired", error_code="INVALID_OTP"
                    )

                # Get profile and update email
                profile = request.user.profile
                if not profile.temp_email:
                    return self.bad_request_response(
                        message="No pending email change found",
                        error_code="NO_PENDING_EMAIL_CHANGE",
                    )

                old_email = request.user.email
                new_email = profile.temp_email

                # Update user email
                request.user.email = new_email
                request.user.save()

                # Clear temp email
                profile.temp_email = None
                profile.save()

                # Mark OTP as used
                otp.is_used = True
                otp.save()

                return self.success_response(
                    data={
                        "old_email": old_email,
                        "new_email": new_email,
                        "email_changed": True,
                    },
                    message="Email changed successfully",
                )

            except OTP.DoesNotExist:
                return self.bad_request_response(
                    message="Invalid OTP", error_code="INVALID_OTP"
                )

        except Exception as e:
            return self.handle_exception(e)


class ResendEmailChangeOTPView(BaseResponseMixin, generics.GenericAPIView):
    """API view to re-send verification OTP for a pending email change."""

    permission_classes = [IsAuthenticated]
    serializer_class = ResendEmailChangeOTPSerializer

    def post(self, request):
        """Invalidate old email change OTPs and generate/send a new one.

        Args:
            request (Request): HTTP request with pending new email.

        Returns:
            Response: Status response indicating OTP was sent.
        """
        try:
            profile = request.user.profile

            if not profile.temp_email:
                return self.bad_request_response(
                    message="No pending email change found",
                )

            # Invalidate old OTPs
            OTP.objects.filter(
                user=request.user, purpose="email_change", is_used=False
            ).update(is_used=True)

            # Generate new OTP
            otp_code = str(random.randint(1000, 9999))
            OTP.objects.create(
                user=request.user,
                otp=otp_code,
                purpose="email_change",
                expires_at=timezone.now() + timedelta(minutes=10),
            )

            send_otp_email(profile.user, otp_code, "email_change", to_email=profile.temp_email)


            return self.success_response(
                data={"temp_email": profile.temp_email, "otp_sent": True},
                message="OTP sent to your new email address",
            )

        except Exception as e:
            return self.handle_exception(e)


class CancelEmailChangeView(BaseResponseMixin, generics.GenericAPIView):
    """API view to cancel a pending email change request."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Clear pending email change from profile and invalidate active OTPs.

        Args:
            request (Request): HTTP request to cancel email change.

        Returns:
            Response: Cancellation confirmation response.
        """
        try:
            profile = request.user.profile

            if not profile.temp_email:
                return self.bad_request_response(
                    message="No pending email change found",
                )

            # Clear temp email
            profile.temp_email = None
            profile.save()

            # Invalidate OTPs
            OTP.objects.filter(
                user=request.user, purpose="email_change", is_used=False
            ).update(is_used=True)

            return self.success_response(message="Email change cancelled successfully")

        except Exception as e:
            return self.handle_exception(e)


class SocialAuthView(BaseResponseMixin, generics.GenericAPIView):
    """API view for third-party social authentication / registration."""

    serializer_class = SocialAuthSerializer
    permission_classes = (permissions.AllowAny,)
    throttle_classes = [AnonRateThrottle]

    def post(self, request):
        """Authenticate or create user via social provider data and issue JWT tokens.

        Args:
            request (Request): HTTP request containing provider and user details.

        Returns:
            Response: Response containing JWT tokens and user profile information.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.create_or_login_user()

        # Generate tokens
        refresh = RefreshToken.for_user(user)

        return self.success_response(
            data={
                "refresh": str(refresh),
                "access": str(refresh.access_token),
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "full_name": user.profile.full_name if hasattr(user, "profile") else "",
                    "social_auth_provider": user.social_auth_provider,  # Include provider info
                },
            },
            message="Login successful",
            status_code=status.HTTP_200_OK,
        )
    
class UserProfileGenericView(BaseResponseMixin, RetrieveUpdateAPIView):
    """API view to retrieve and update user profile details with file upload support."""

    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    
    def get_object(self):
        """Get or create the profile for the authenticated user."""
        profile, created = UserProfile.objects.get_or_create(user=self.request.user)
        return profile
    
    def get(self, request, *args, **kwargs):
        """Retrieve user profile data.

        Args:
            request (Request): HTTP GET request.

        Returns:
            Response: Profile data.
        """
        profile = self.get_object()
        serializer = self.get_serializer(profile)
        
        return self.success_response(
            data=serializer.data,
            message="Profile retrieved successfully"
        )
    
    def put(self, request, *args, **kwargs):
        """Update user profile fields and/or upload avatar image.

        Args:
            request (Request): HTTP PUT/PATCH request with form data.

        Returns:
            Response: Updated profile data or validation error details.
        """
        profile = self.get_object()
        serializer = self.get_serializer(profile, data=request.data, partial=True)
        
        if serializer.is_valid():
            serializer.save()
            
            return self.success_response(
                data=serializer.data,
                message='Profile updated successfully'
            )
        
        errors = serializer.errors
        if 'gender' in errors:
            valid_genders = [choice[0] for choice in UserProfile.GENDER_CHOICES]
            errors['gender_choices'] = valid_genders
        
        return self.error_response(
            message="Validation failed",
            errors=errors,
            status_code=status.HTTP_400_BAD_REQUEST
        )
    
    def post(self, request, *args, **kwargs):
        """Handle POST requests as PUT/partial update for profile."""
        return self.put(request, *args, **kwargs)
    
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_user_profile_info(request):
    """Retrieve aggregated user statistics including streak, nest counts, and bookmarks.

    Args:
        request (Request): Authenticated HTTP GET request.

    Returns:
        Response: Summary dictionary with user email, full name, streak count, nests count, and bookmarks count.
    """
    user = request.user
    profile = getattr(user, 'profile', None)

    return Response({
        'email': user.email,
        'full_name': profile.full_name if profile else None,
        'daily_streak': user.streak.current_streak if hasattr(user, 'streak') else 0,
        'nest_count': UserNest.objects.filter(user=user).count(),
        'saved_posts_count': Bookmark.objects.filter(user=user).count(),
    })


class ChangeEmailView(BaseResponseMixin, generics.GenericAPIView):
    """API view to initiate an email address change request and dispatch verification OTP."""

    permission_classes = [IsAuthenticated]
    serializer_class = ChangeEmailSerializer

    def post(self, request):
        """Initiate email change workflow by assigning temp email and sending OTP.

        Args:
            request (Request): HTTP request with proposed new email.

        Returns:
            Response: Status response indicating OTP has been sent.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_email = serializer.validated_data['new_email']
        user = request.user

        profile = user.profile
        profile.temp_email = new_email
        profile.save()

        OTP.objects.filter(user=user, purpose="email_change", is_used=False).update(is_used=True)

        otp_code = "".join(random.choices("0123456789", k=4))
        OTP.objects.create(
            user=user,
            otp=otp_code,
            purpose="email_change",
            expires_at=timezone.now() + timedelta(minutes=10),
        )

        send_otp_email(user, otp_code, "email_change", to_email=new_email)

        return self.success_response(
            data={"temp_email": new_email},
            message="OTP sent to your new email address. Please verify to complete the change."
        )