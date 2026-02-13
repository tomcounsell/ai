import os


def show_debug_toolbar(request):
    from django.conf import settings

    if not settings.DEBUG:
        return False
    if request.META.get("REMOTE_ADDR") not in settings.INTERNAL_IPS:
        return False
    return request.COOKIES.get("debug_toolbar", "on") == "on"


class APIHeaderMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["X-Required-Main-Build"] = os.environ.get(
            "Required-Main-Build", "unknown"
        )
        return response
