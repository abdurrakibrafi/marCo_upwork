from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from apps.core.utils.mixins import BaseResponseMixin
from apps.notification.models import (
    Notification,
    NotificationPreference,
)
from apps.notification.serializers import (
    DeviceTokenSerializer,
    MarkReadRequestSerializer,
    NotificationPreferenceSerializer,
    NotificationSerializer,
)
from apps.notification.services import NotificationService


class NotificationPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class NotificationListView(BaseResponseMixin, APIView):
    """List notifications with unread badge count, and filter options."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List user notifications",
        description="Returns paginated notification history along with current unread badge count.",
        parameters=[
            OpenApiParameter(name="unread", type=bool, description="Filter only unread notifications (true/false)"),
            OpenApiParameter(name="type", type=str, description="Filter by notification type"),
            OpenApiParameter(name="page", type=int, description="Page number"),
            OpenApiParameter(name="page_size", type=int, description="Items per page"),
        ],
        responses={200: NotificationSerializer(many=True)},
    )
    def get(self, request):
        user = request.user
        queryset = Notification.objects.filter(recipient=user).order_by("-created_at")

        # Query Filters
        unread_filter = request.query_params.get("unread")
        if unread_filter is not None:
            if unread_filter.lower() in ["true", "1"]:
                queryset = queryset.filter(is_read=False)
            elif unread_filter.lower() in ["false", "0"]:
                queryset = queryset.filter(is_read=True)

        notif_type = request.query_params.get("type")
        if notif_type:
            queryset = queryset.filter(notification_type=notif_type)

        paginator = NotificationPagination()
        paginated_notifs = paginator.paginate_queryset(queryset, request)
        serializer = NotificationSerializer(paginated_notifs, many=True)

        unread_count = NotificationService.get_unread_count(user)

        data = {
            "unread_count": unread_count,
            "count": paginator.page.paginator.count,
            "next": paginator.get_next_link(),
            "previous": paginator.get_previous_link(),
            "results": serializer.data,
        }

        return self.success_response(
            data=data,
            message="Notifications retrieved successfully",
        )


class NotificationMarkReadView(BaseResponseMixin, APIView):
    """Mark single, multiple, or all notifications as read."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Mark notifications as read",
        description="Pass notification_id, a list of notification_ids, or all=true to mark as read. Returns fresh unread_count.",
        request=MarkReadRequestSerializer,
    )
    def post(self, request):
        serializer = MarkReadRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return self.error_response(
                message="Invalid payload",
                errors=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        validated = serializer.validated_data
        notif_id = validated.get("notification_id")
        notif_ids = validated.get("notification_ids")
        mark_all = validated.get("all", False)

        new_unread_count = NotificationService.mark_as_read(
            user=request.user,
            notification_id=notif_id,
            notification_ids=notif_ids,
            mark_all=mark_all,
        )

        return self.success_response(
            data={"unread_count": new_unread_count},
            message="Notification(s) marked as read",
        )


class NotificationDetailDeleteView(BaseResponseMixin, APIView):
    """Delete a single notification."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Delete a notification",
        description="Permanently delete a single notification belonging to the current user.",
    )
    def delete(self, request, pk):
        try:
            notif = Notification.objects.get(id=pk, recipient=request.user)
            notif.delete()
        except Notification.DoesNotExist:
            return self.error_response(
                message="Notification not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        unread_count = NotificationService.get_unread_count(request.user)
        return self.success_response(
            data={"unread_count": unread_count},
            message="Notification deleted successfully",
        )


class DeviceTokenView(BaseResponseMixin, APIView):
    """Register, update or deactivate device push token."""

    permission_classes = [AllowAny]

    @extend_schema(
        summary="Register or deactivate device push token",
        description="Register a device token for push notifications or deactivate it on logout.",
        request=DeviceTokenSerializer,
    )
    def post(self, request):
        serializer = DeviceTokenSerializer(
            data=request.data,
            context={"request": request},
        )
        if not serializer.is_valid():
            return self.error_response(
                message="Validation failed",
                errors=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        device_token = serializer.save()
        return self.success_response(
            data={
                "token": device_token.token,
                "device_type": device_token.device_type,
                "is_active": device_token.is_active,
            },
            message="Device token registered successfully",
            status_code=status.HTTP_200_OK,
        )


class NotificationPreferenceView(BaseResponseMixin, APIView):
    """View and update notification preferences."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get user notification preferences",
        description="Retrieve notification channel and category toggles for current user.",
        responses={200: NotificationPreferenceSerializer},
    )
    def get(self, request):
        pref, _ = NotificationPreference.objects.get_or_create(user=request.user)
        serializer = NotificationPreferenceSerializer(pref)
        return self.success_response(
            data=serializer.data,
            message="Notification preferences retrieved successfully",
        )

    @extend_schema(
        summary="Update user notification preferences",
        description="Partially update user notification preferences.",
        request=NotificationPreferenceSerializer,
        responses={200: NotificationPreferenceSerializer},
    )
    def patch(self, request):
        pref, _ = NotificationPreference.objects.get_or_create(user=request.user)
        serializer = NotificationPreferenceSerializer(
            pref,
            data=request.data,
            partial=True,
        )
        if not serializer.is_valid():
            return self.error_response(
                message="Validation error",
                errors=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        serializer.save()
        return self.success_response(
            data=serializer.data,
            message="Notification preferences updated successfully",
        )
