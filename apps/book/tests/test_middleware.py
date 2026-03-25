import pytest
from django.test import RequestFactory

from apps.common.utilities.django.middleware import DomainRoutingMiddleware


class TestDomainRoutingMiddleware:
    """Test the DomainRoutingMiddleware routes requests by hostname."""

    def _make_middleware(self):
        """Create middleware with a passthrough get_response."""
        return DomainRoutingMiddleware(lambda request: request)

    def test_book_domain_sets_urlconf(self):
        middleware = self._make_middleware()
        factory = RequestFactory()
        request = factory.get("/", HTTP_HOST="blendedworkforce.ai")
        middleware(request)
        assert request.site_name == "book"
        assert request.urlconf == "apps.book.root_urls"

    def test_www_book_domain_sets_urlconf(self):
        middleware = self._make_middleware()
        factory = RequestFactory()
        request = factory.get("/", HTTP_HOST="www.blendedworkforce.ai")
        middleware(request)
        assert request.site_name == "book"
        assert request.urlconf == "apps.book.root_urls"

    def test_book_domain_with_port_sets_urlconf(self):
        middleware = self._make_middleware()
        factory = RequestFactory()
        request = factory.get("/", HTTP_HOST="blendedworkforce.ai:8000")
        middleware(request)
        assert request.site_name == "book"
        assert request.urlconf == "apps.book.root_urls"

    def test_cuttlefish_domain_no_urlconf_override(self):
        middleware = self._make_middleware()
        factory = RequestFactory()
        request = factory.get("/", HTTP_HOST="ai.yuda.me")
        middleware(request)
        assert request.site_name == "cuttlefish"
        assert not hasattr(request, "urlconf")

    def test_localhost_no_urlconf_override(self):
        middleware = self._make_middleware()
        factory = RequestFactory()
        request = factory.get("/", HTTP_HOST="localhost:8000")
        middleware(request)
        assert request.site_name == "cuttlefish"
        assert not hasattr(request, "urlconf")

    def test_missing_host_defaults_to_cuttlefish(self):
        middleware = self._make_middleware()
        factory = RequestFactory()
        request = factory.get("/")
        # Remove HTTP_HOST if set
        request.META.pop("HTTP_HOST", None)
        middleware(request)
        assert request.site_name == "cuttlefish"
        assert not hasattr(request, "urlconf")

    def test_case_insensitive_host(self):
        middleware = self._make_middleware()
        factory = RequestFactory()
        request = factory.get("/", HTTP_HOST="BlendedWorkforce.AI")
        middleware(request)
        assert request.site_name == "book"
        assert request.urlconf == "apps.book.root_urls"


@pytest.mark.django_db
class TestDomainRoutingIntegration:
    """Integration tests verifying domain routing end-to-end."""

    def test_cuttlefish_urls_still_work(self, client):
        """Verify Cuttlefish home page renders normally (no regression)."""
        response = client.get("/", HTTP_HOST="localhost")
        assert response.status_code == 200

    def test_book_and_cuttlefish_are_independent(self, client):
        """Verify book domain doesn't serve Cuttlefish content."""
        book_response = client.get("/", HTTP_HOST="blendedworkforce.ai")
        cuttlefish_response = client.get("/", HTTP_HOST="localhost")
        # They should use different templates
        book_templates = [t.name for t in book_response.templates]
        cuttlefish_templates = [t.name for t in cuttlefish_response.templates]
        assert "book/landing.html" in book_templates
        assert "book/landing.html" not in cuttlefish_templates
