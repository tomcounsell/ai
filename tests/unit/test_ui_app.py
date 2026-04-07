"""Tests for the web UI FastAPI application factory and route handlers.

Tests use FastAPI's TestClient to verify routes return expected status codes
and content without starting a real server.
"""

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.webui]


@pytest.fixture
def app():
    """Create a test app instance."""
    from ui.app import create_app

    return create_app()


@pytest.fixture
def client(app):
    """Create a TestClient for the app."""
    from fastapi.testclient import TestClient

    return TestClient(app)


class TestAppFactory:
    """Tests for the create_app() factory function."""

    def test_create_app_returns_fastapi_instance(self):
        from ui.app import create_app

        app = create_app()
        assert app is not None
        assert app.title == "Valor System Dashboard"

    def test_docs_disabled(self, app):
        """API docs should be disabled (not a REST API)."""
        assert app.docs_url is None
        assert app.redoc_url is None

    def test_jinja2_filters_registered(self, app):
        """Custom Jinja2 filters should be available."""
        templates = app.state.templates
        env = templates.env
        assert "format_timestamp" in env.filters
        assert "format_duration" in env.filters
        assert "format_interval_filter" in env.filters
        assert "format_relative" in env.filters


class TestJinja2Filters:
    """Tests for custom Jinja2 template filters."""

    def test_format_duration_seconds(self):
        from ui.app import _filter_format_duration

        assert _filter_format_duration(5.2) == "<1m"
        assert _filter_format_duration(59.9) == "<1m"

    def test_format_duration_minutes(self):
        from ui.app import _filter_format_duration

        assert _filter_format_duration(120.0) == "2m"

    def test_format_duration_hours(self):
        from ui.app import _filter_format_duration

        assert _filter_format_duration(7200.0) == "2h"

    def test_format_duration_none(self):
        from ui.app import _filter_format_duration

        assert _filter_format_duration(None) == "-"

    def test_format_timestamp_none(self):
        from ui.app import _filter_format_timestamp

        assert _filter_format_timestamp(None) == "-"

    def test_format_timestamp_value(self):
        from ui.app import _filter_format_timestamp

        result = _filter_format_timestamp(1711000000.0)
        assert isinstance(result, str)
        assert result != "-"

    def test_format_interval(self):
        from ui.app import _filter_format_interval

        assert _filter_format_interval(300) == "5m"
        assert _filter_format_interval(3600) == "1h"
        assert _filter_format_interval(86400) == "1d"
        assert _filter_format_interval(None) == "-"
        assert _filter_format_interval(0) == "-"

    def test_format_relative(self):
        from ui.app import _filter_format_relative

        assert _filter_format_relative(None) == "-"
        assert "in" in _filter_format_relative(300)
        assert "overdue" in _filter_format_relative(-300)


class TestRootRoute:
    """Tests for the single-page dashboard."""

    def test_root_returns_200(self, client):
        r = client.get("/")
        assert r.status_code == 200

    def test_root_contains_sections(self, client):
        r = client.get("/")
        assert "Agent Sessions" in r.text
        assert "Reflections" in r.text

    def test_root_has_sessions_htmx_polling(self, client):
        r = client.get("/")
        assert "/_partials/sessions/" in r.text
        assert "every 5s" in r.text

    def test_root_contains_htmx(self, client):
        r = client.get("/")
        assert "htmx.org" in r.text

    def test_root_contains_css_link(self, client):
        r = client.get("/")
        assert "style.css" in r.text

    def test_root_has_no_nav_bar(self, client):
        r = client.get("/")
        assert "top-nav" not in r.text


class TestSessionsPartial:
    """Tests for the sessions HTMX partial endpoint."""

    def test_partial_sessions_returns_200(self, client):
        r = client.get("/_partials/sessions/")
        assert r.status_code == 200


class TestStaticFiles:
    """Tests for static file serving."""

    def test_css_file_served(self, client):
        r = client.get("/static/style.css")
        assert r.status_code == 200
        assert "text/css" in r.headers.get("content-type", "")

    def test_css_has_dark_theme(self, client):
        r = client.get("/static/style.css")
        assert "--bg-primary" in r.text
        assert "#0d1117" in r.text


class TestLocalhostBinding:
    """Tests to verify the server binds to localhost only."""

    def test_app_factory_does_not_expose_network(self):
        """Verify the __main__ module uses 127.0.0.1 not 0.0.0.0."""
        import importlib

        spec = importlib.util.find_spec("ui.__main__")
        assert spec is not None
        source = spec.origin
        with open(source) as f:
            content = f.read()
        assert "127.0.0.1" in content
        assert "0.0.0.0" not in content
