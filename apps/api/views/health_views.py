"""
Health check endpoints for monitoring production services.
"""

from django.http import JsonResponse
from django.db import connection
from django.core.cache import cache
from django.conf import settings


def health_check(request):
    """
    Basic health check endpoint.

    Returns a simple status indicating the service is running.
    Used for basic uptime monitoring.
    """
    return JsonResponse(
        {
            "status": "healthy",
            "service": "cuttlefish",
            "environment": settings.DEPLOYMENT_TYPE,
        }
    )


def deep_health_check(request):
    """
    Detailed health check with dependency verification.

    Checks:
    - Database connectivity
    - Cache availability
    - Static files (assumed OK if server is running)

    Returns 503 if any critical service is unavailable.
    """
    checks = {
        "database": _check_database(),
        "cache": _check_cache(),
        "static_files": True,  # Assume OK if server is running
    }

    all_healthy = all(checks.values())
    status_code = 200 if all_healthy else 503

    return JsonResponse(
        {
            "status": "healthy" if all_healthy else "unhealthy",
            "checks": checks,
            "environment": settings.DEPLOYMENT_TYPE,
        },
        status=status_code,
    )


def _check_database():
    """Check database connectivity."""
    try:
        connection.ensure_connection()
        return True
    except Exception:
        return False


def _check_cache():
    """Check cache connectivity."""
    try:
        cache.set("health_check", "ok", 10)
        return cache.get("health_check") == "ok"
    except Exception:
        return False
