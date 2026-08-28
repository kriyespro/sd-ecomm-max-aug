"""Uniform error envelope so no internal detail leaks to storefront clients."""

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_handler


def api_exception_handler(exc, context):
    response = drf_handler(exc, context)
    if response is not None:
        detail = response.data
        code = _code(exc)
        if code == "error" and response.status_code == 404:
            code = "not_found"
        if isinstance(detail, dict) and "detail" in detail and len(detail) == 1:
            payload = {"error": {"code": code, "message": str(detail["detail"])}}
        elif isinstance(detail, list):
            payload = {"error": {"code": code, "message": "; ".join(str(x) for x in detail)}}
        else:
            payload = {"error": {"code": code, "message": "Request failed.", "fields": detail}}
        response.data = payload
        return response

    if isinstance(exc, Http404):
        return Response({"error": {"code": "not_found", "message": "Not found."}}, status=404)
    if isinstance(exc, DjangoPermissionDenied):
        return Response({"error": {"code": "permission_denied", "message": "Permission denied."}}, status=403)
    # Anything unhandled -> let Django's 500 machinery log it; don't expose it.
    return None


def _code(exc):
    if isinstance(exc, exceptions.ValidationError):
        return "validation_error"
    if isinstance(exc, exceptions.AuthenticationFailed):
        return "authentication_failed"
    if isinstance(exc, exceptions.NotAuthenticated):
        return "not_authenticated"
    if isinstance(exc, exceptions.PermissionDenied):
        return "permission_denied"
    if isinstance(exc, exceptions.NotFound):
        return "not_found"
    if isinstance(exc, exceptions.Throttled):
        return "throttled"
    return "error"
