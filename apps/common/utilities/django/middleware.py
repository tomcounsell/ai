import os


def show_debug_toolbar(request):
    from django.conf import settings

    if not settings.DEBUG:
        return False
    if request.META.get("REMOTE_ADDR") not in settings.INTERNAL_IPS:
        return False
    return request.COOKIES.get("debug_toolbar", "on") == "on"


class HtmxLoginRedirectMiddleware:
    """Prevent login page from rendering inside HTMX targets.

    When an HTMX request hits a LoginRequiredMixin view, Django returns a 302
    to the login URL. HTMX follows the redirect and swaps the login page HTML
    into the target element. This middleware intercepts that redirect and
    returns a 200 with an HX-Redirect header instead, causing HTMX to perform
    a full-page navigation to the login page.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if (
            getattr(request, "htmx", False)
            and response.status_code in (301, 302)
            and self._is_login_redirect(response)
        ):
            from django.http import HttpResponse as DjangoHttpResponse

            redirect_response = DjangoHttpResponse(status=200)
            redirect_response["HX-Redirect"] = response["Location"]
            return redirect_response

        return response

    @staticmethod
    def _is_login_redirect(response):
        from django.conf import settings

        location = response.get("Location", "")
        login_url = getattr(settings, "LOGIN_URL", "/account/login")
        return location.startswith(login_url)


class APIHeaderMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["X-Required-Main-Build"] = os.environ.get(
            "Required-Main-Build", "unknown"
        )
        return response
