from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html
from django.urls import reverse
from django.utils import timezone
from .models import User, UserProfile, OTP


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'Profile'
    fieldsets = (
        ('Personal Information', {
            'fields': ('full_name', 'temp_email', 'phone', 'date_of_birth', 'gender')
        }),
        ('Profile Details', {
            'fields': ('bio', 'profile_picture', 'profile_completed', 'onboarding_completed')
        }),
        ('Activity', {
            'fields': ('last_active',)
        }),
    )
    readonly_fields = ('last_active', 'created_at', 'updated_at')


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)
    list_display = ('email', 'get_full_name', 'is_active', 'is_staff', 
                   'social_auth_provider', 'status_badge', 'created_at')
    list_filter = ('is_active', 'is_staff', 'is_superuser', 'is_deleted', 
                  'is_blocked', 'social_auth_provider', 'created_at')
    search_fields = ('email', 'profile__full_name')
    ordering = ('-created_at',)
    
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal Info', {
            'fields': ('username',),
            'classes': ('collapse',)
        }),
        ('Social Authentication', {
            'fields': ('social_auth_provider',),
            'classes': ('wide',)
        }),
        ('Permissions', {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
        }),
        ('Status', {
            'fields': ('is_deleted', 'is_blocked', 'deleted_at'),
            'classes': ('collapse',)
        }),
        ('Important dates', {
            'fields': ('last_login', 'date_joined', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2'),
        }),
    )
    
    readonly_fields = ('created_at', 'updated_at', 'last_login', 'date_joined', 'deleted_at')
    
    def get_full_name(self, obj):
        if hasattr(obj, 'profile') and obj.profile.full_name:
            return obj.profile.full_name
        return "-"
    get_full_name.short_description = 'Full Name'
    get_full_name.admin_order_field = 'profile__full_name'
    
    def status_badge(self, obj):
        if obj.is_deleted:
            return format_html('<span style="background-color: #dc3545; color: white; padding: 3px 10px; border-radius: 10px;">Deleted</span>')
        elif obj.is_blocked:
            return format_html('<span style="background-color: #ffc107; color: black; padding: 3px 10px; border-radius: 10px;">Blocked</span>')
        elif not obj.is_active:
            return format_html('<span style="background-color: #6c757d; color: white; padding: 3px 10px; border-radius: 10px;">Inactive</span>')
        else:
            return format_html('<span style="background-color: #28a745; color: white; padding: 3px 10px; border-radius: 10px;">Active</span>')
    status_badge.short_description = 'Status'
    
    actions = ['soft_delete_users', 'restore_users', 'block_users', 'unblock_users']
    
    def soft_delete_users(self, request, queryset):
        for user in queryset:
            user.soft_delete()
        self.message_user(request, f"{queryset.count()} user(s) were successfully soft-deleted.")
    soft_delete_users.short_description = "Soft delete selected users"
    
    def restore_users(self, request, queryset):
        for user in queryset:
            user.restore()
        self.message_user(request, f"{queryset.count()} user(s) were successfully restored.")
    restore_users.short_description = "Restore selected users"
    
    def block_users(self, request, queryset):
        queryset.update(is_blocked=True)
        self.message_user(request, f"{queryset.count()} user(s) were successfully blocked.")
    block_users.short_description = "Block selected users"
    
    def unblock_users(self, request, queryset):
        queryset.update(is_blocked=False)
        self.message_user(request, f"{queryset.count()} user(s) were successfully unblocked.")
    unblock_users.short_description = "Unblock selected users"


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user_email', 'full_name', 'phone', 'gender', 
                   'profile_completed', 'onboarding_completed', 'profile_picture_preview')
    list_filter = ('gender', 'profile_completed', 'onboarding_completed', 'created_at')
    search_fields = ('user__email', 'full_name', 'phone')
    ordering = ('-created_at',)
    
    fieldsets = (
        ('User Information', {
            'fields': ('user', 'full_name', 'temp_email')
        }),
        ('Contact Information', {
            'fields': ('phone',)
        }),
        ('Personal Details', {
            'fields': ('date_of_birth', 'gender')
        }),
        ('Profile Information', {
            'fields': ('bio', 'profile_picture', 'profile_completed', 'onboarding_completed')
        }),
        ('Activity', {
            'fields': ('last_active',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = ('user', 'created_at', 'updated_at', 'last_active')
    list_per_page = 50
    
    def user_email(self, obj):
        return obj.user.email
    user_email.short_description = 'User Email'
    user_email.admin_order_field = 'user__email'
    
    def profile_picture_preview(self, obj):
        if obj.profile_picture:
            return format_html('<img src="{}" style="max-height: 50px; max-width: 50px; border-radius: 50%;" />', 
                             obj.profile_picture.url)
        return format_html('<span style="color: #999;">No image</span>')
    profile_picture_preview.short_description = 'Profile Picture'
    
    actions = ['mark_profile_completed', 'mark_onboarding_completed']
    
    def mark_profile_completed(self, request, queryset):
        queryset.update(profile_completed=True)
        self.message_user(request, f"{queryset.count()} profile(s) marked as completed.")
    mark_profile_completed.short_description = "Mark profile as completed"
    
    def mark_onboarding_completed(self, request, queryset):
        queryset.update(onboarding_completed=True)
        self.message_user(request, f"{queryset.count()} profile(s) marked as onboarding completed.")
    mark_onboarding_completed.short_description = "Mark onboarding as completed"


@admin.register(OTP)
class OTPAdmin(admin.ModelAdmin):
    list_display = ('id', 'user_email', 'otp', 'purpose', 'status_badge', 
                   'expires_at', 'created_at')
    list_filter = ('purpose', 'is_used', 'created_at')
    search_fields = ('user__email', 'otp')
    ordering = ('-created_at',)
    
    fieldsets = (
        ('OTP Information', {
            'fields': ('user', 'otp', 'purpose')
        }),
        ('Status', {
            'fields': ('is_used', 'expires_at')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = ('created_at', 'updated_at')
    list_per_page = 50
    
    def user_email(self, obj):
        return obj.user.email
    user_email.short_description = 'User'
    user_email.admin_order_field = 'user__email'
    
    def status_badge(self, obj):
        if obj.is_used:
            return format_html('<span style="background-color: #6c757d; color: white; padding: 3px 10px; border-radius: 10px;">Used</span>')
        elif obj.expires_at < timezone.now():
            return format_html('<span style="background-color: #dc3545; color: white; padding: 3px 10px; border-radius: 10px;">Expired</span>')
        else:
            return format_html('<span style="background-color: #28a745; color: white; padding: 3px 10px; border-radius: 10px;">Valid</span>')
    status_badge.short_description = 'Status'
    
    actions = ['mark_as_used', 'cleanup_expired']
    
    def mark_as_used(self, request, queryset):
        queryset.update(is_used=True)
        self.message_user(request, f"{queryset.count()} OTP(s) marked as used.")
    mark_as_used.short_description = "Mark selected OTPs as used"
    
    def cleanup_expired(self, request, queryset):
        expired = queryset.filter(expires_at__lt=timezone.now(), is_used=False)
        count = expired.count()
        expired.delete()
        self.message_user(request, f"{count} expired OTP(s) were deleted.")
    cleanup_expired.short_description = "Delete expired OTPs"


# Customize admin site headers
admin.site.site_header = "Authentication System Administration"
admin.site.site_title = "Auth System Admin"
admin.site.index_title = "Welcome to Authentication System Admin Panel"