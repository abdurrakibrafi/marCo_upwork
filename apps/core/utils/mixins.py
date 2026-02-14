from rest_framework import status
from rest_framework.response import Response
from rest_framework.serializers import ValidationError as DRFValidationError
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotAuthenticated,
    PermissionDenied,
    NotFound,
    APIException,
    MethodNotAllowed,
    UnsupportedMediaType,
    Throttled
)
from django.http import Http404
from django.db import IntegrityError
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)


class BaseResponseMixin:
    
    def success_response(self, data=None, message="Success", status_code=status.HTTP_200_OK, **kwargs):
        """
        Generate a standardized success response.
        
        Args:
            data: Response data (optional)
            message: Success message
            status_code: HTTP status code
            **kwargs: Additional fields to include in response
        """
        response_data = {
            "success": True,
            "message": message,
            "timestamp": timezone.now().isoformat(),
            "status_code": status_code,
        }
        
        if data is not None:
            response_data["data"] = data
            
        # Add any additional fields
        response_data.update(kwargs)
        
        return Response(response_data, status=status_code)

    def error_response(self, message="Error", error_code=None, errors=None, 
                      status_code=status.HTTP_400_BAD_REQUEST, **kwargs):
        """
        Generate a standardized error response.
        
        Args:
            message: Error message
            error_code: Specific error code for frontend handling
            errors: Detailed error information (for validation errors)
            status_code: HTTP status code
            **kwargs: Additional fields to include in response
        """
        response_data = {
            "success": False,
            "message": message,
            "timestamp": timezone.now().isoformat(),
            "status_code": status_code,
        }
        
        if error_code:
            response_data["error_code"] = error_code
            
        if errors:
            response_data["errors"] = errors
            
        # Add any additional fields
        response_data.update(kwargs)
        
        return Response(response_data, status=status_code)

    def handle_exception(self, exc):
        """
        Handle exceptions and return consistent error responses.
        """
        # Log the exception for debugging
        logger.error(f"API Exception: {type(exc).__name__}: {str(exc)}")
        
        # DRF Validation errors (from serializer.is_valid())
        if isinstance(exc, DRFValidationError):
            return self.error_response(
                message="Validation failed",
                error_code="VALIDATION_ERROR",
                errors=exc.detail,
                status_code=status.HTTP_400_BAD_REQUEST
            )
        
        # Django validation errors
        elif isinstance(exc, DjangoValidationError):
            return self.error_response(
                message="Validation failed",
                error_code="VALIDATION_ERROR",
                errors=exc.message_dict if hasattr(exc, 'message_dict') else str(exc),
                status_code=status.HTTP_400_BAD_REQUEST
            )
        
        # Authentication errors
        elif isinstance(exc, NotAuthenticated):
            return self.error_response(
                message="Authentication credentials were not provided.",
                error_code="NOT_AUTHENTICATED",
                status_code=status.HTTP_401_UNAUTHORIZED
            )
        
        elif isinstance(exc, AuthenticationFailed):
            return self.error_response(
                message="Invalid authentication credentials.",
                error_code="AUTHENTICATION_FAILED",
                status_code=status.HTTP_401_UNAUTHORIZED
            )
        
        # Permission errors
        elif isinstance(exc, PermissionDenied):
            return self.error_response(
                message="You do not have permission to perform this action.",
                error_code="PERMISSION_DENIED",
                status_code=status.HTTP_403_FORBIDDEN
            )
        
        # Not found errors
        elif isinstance(exc, (NotFound, Http404)):
            return self.error_response(
                message="The requested resource was not found.",
                error_code="RESOURCE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND
            )
        
        # Method not allowed
        elif isinstance(exc, MethodNotAllowed):
            return self.error_response(
                message=f"Method '{exc.method}' not allowed.",
                error_code="METHOD_NOT_ALLOWED",
                status_code=status.HTTP_405_METHOD_NOT_ALLOWED
            )
        
        # Unsupported media type
        elif isinstance(exc, UnsupportedMediaType):
            return self.error_response(
                message="Unsupported media type in request.",
                error_code="UNSUPPORTED_MEDIA_TYPE",
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
            )
        
        # Rate limiting
        elif isinstance(exc, Throttled):
            return self.error_response(
                message=f"Request was throttled. Expected available in {exc.wait} seconds.",
                error_code="RATE_LIMITED",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                retry_after=exc.wait
            )
        
        # Database integrity errors
        elif isinstance(exc, IntegrityError):
            return self.error_response(
                message="Data integrity constraint violation. The operation conflicts with existing data.",
                error_code="INTEGRITY_ERROR",
                status_code=status.HTTP_409_CONFLICT
            )
        
        # Generic DRF API exceptions
        elif isinstance(exc, APIException):
            return self.error_response(
                message=str(exc.detail),
                error_code="API_EXCEPTION",
                status_code=exc.status_code
            )
        
        # Catch any other exceptions
        else:
            return self.error_response(
                message="An unexpected error occurred. Please try again later.",
                error_code="INTERNAL_ERROR",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    # Convenience methods for common responses
    def created_response(self, data=None, message="Resource created successfully"):
        """Convenience method for 201 Created responses"""
        return self.success_response(
            data=data, 
            message=message, 
            status_code=status.HTTP_201_CREATED
        )
    
    def updated_response(self, data=None, message="Resource updated successfully"):
        """Convenience method for update responses"""
        return self.success_response(
            data=data, 
            message=message, 
            status_code=status.HTTP_200_OK
        )
    
    def deleted_response(self, message="Resource deleted successfully"):
        """Convenience method for delete responses"""
        return self.success_response(
            message=message, 
            status_code=status.HTTP_204_NO_CONTENT
        )
    
    def not_found_response(self, message="Resource not found"):
        """Convenience method for 404 responses"""
        return self.error_response(
            message=message,
            error_code="RESOURCE_NOT_FOUND",
            status_code=status.HTTP_404_NOT_FOUND
        )
    
    def bad_request_response(self, message="Bad request", errors=None):
        """Convenience method for 400 responses"""
        return self.error_response(
            message=message,
            error_code="BAD_REQUEST",
            errors=errors,
            status_code=status.HTTP_400_BAD_REQUEST
        )
    
