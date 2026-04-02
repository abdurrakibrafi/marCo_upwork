from django.urls import path, include
from rest_framework import views
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    RegisterView,
    LoginView,
    LogoutView,
    VerifyEmailView,
    PasswordResetRequestView,
    PasswordResetOTPVerifyView,
    ChangePasswordView,
    GoogleLogin,
    AppleLogin,
    ResendOTPView,
    PasswordResetConfirmView,
    AccountRestoreView,
    ProfileUpdateView,
    VerifyEmailChangeView,
    SocialAuthView,
    UserProfileGenericView,
    ParmanentAccountDeleteView,
    get_user_profile_info
)

urlpatterns = [
    # Authentication endpoints
    path("register/", RegisterView.as_view(), name="register"),
    path("verify-email/", VerifyEmailView.as_view(), name="verify-email"),
    
    path("resend-otp/", ResendOTPView.as_view(), name="resend-otp"),

    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    
    # Password management
    path(
        "password/reset-request/",
        PasswordResetRequestView.as_view(),
        name="password-reset-request",
    ),
    path(
        "password/reset-verify-otp/",
        PasswordResetOTPVerifyView.as_view(),
        name="password-reset-verify-otp",
    ),
    path(
        "password/reset-confirm/",
        PasswordResetConfirmView.as_view(),
        name="password-reset-confirm-view",
    ),

    path("password/change/", ChangePasswordView.as_view(), name="password-change"),

    # path("google/login/", GoogleLogin.as_view(), name="google_login"),
    # path("apple/login/", AppleLogin.as_view(), name="apple_login"),

    # path("account/delete/", AccountSoftDeleteView.as_view(), name="account-delete"),
    path("account/parmanent/delete/", ParmanentAccountDeleteView.as_view(), name="parmanent-delete"),
    path("account/restore/", AccountRestoreView.as_view(), name="account-restore"),
    
    path("profile/update/", ProfileUpdateView.as_view(), name="profile-update"),
    path("profile/", UserProfileGenericView.as_view(), name="user-profile"),
    path(
        "profile/verify-email-change/",
        VerifyEmailChangeView.as_view(),
        name="verify-email-change",
    ),

    path("social-auth/", SocialAuthView.as_view(), name="social-auth"),

    path("profile-info/", get_user_profile_info, name="user-profile-info"),

]
