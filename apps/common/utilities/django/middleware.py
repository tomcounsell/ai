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


BOOK_DOMAINS: set[str] = {"blendedworkforce.ai", "www.blendedworkforce.ai"}


class DomainRoutingMiddleware:
    """Route requests to different URL configs based on the incoming hostname.

    Uses Django's per-request ``request.urlconf`` override so that book-domain
    requests resolve against ``apps.book.urls`` instead of the default
    ``ROOT_URLCONF``.  This is a built-in Django feature documented at
    https://docs.djangoproject.com/en/5.1/topics/http/urls/#how-django-processes-a-request

    For any hostname in ``BOOK_DOMAINS``, the middleware sets:
    - ``request.site_name = "book"``
    - ``request.urlconf = "apps.book.urls"``

    All other hostnames fall through to the default Cuttlefish URL config.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = self._get_host(request)
        if host in BOOK_DOMAINS:
            request.site_name = "book"
            request.urlconf = "apps.book.root_urls"
        else:
            request.site_name = "cuttlefish"
        return self.get_response(request)

    @staticmethod
    def _get_host(request) -> str:
        """Extract the hostname from the request, stripping port if present."""
        try:
            host = request.headers.get("host", "")
            # Strip port number (e.g. "blendedworkforce.ai:8000" -> "blendedworkforce.ai")
            return host.split(":")[0].lower()
        except (AttributeError, TypeError):
            return ""


class APIHeaderMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["X-Required-Main-Build"] = os.environ.get(
            "Required-Main-Build", "unknown"
        )
        return response
