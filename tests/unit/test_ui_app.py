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

        assert _filter_format_duration(5.2) == "5.2s"
        assert _filter_format_duration(59.9) == "59.9s"

    def test_format_duration_minutes(self):
        from ui.app import _filter_format_duration

        assert _filter_format_duration(120.0) == "2.0m"

    def test_format_duration_hours(self):
        from ui.app import _filter_format_duration

        assert _filter_format_duration(7200.0) == "2.0h"

    def test_format_duration_none(self):
        from ui.app import _filter_format_duration

        assert _filter_format_duration(None) == "-"

    def test_format_timestamp_none(self):
        from ui.app import _filter_format_timestamp

        assert _filter_format_timestamp(None) == "-"

    def test_format_timestamp_value(self):
        from ui.app import _filter_format_timestamp

        result = _filter_format_timestamp(1711000000.0)
        assert "2024" in result  # Should contain a year

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
    """Tests for the root dashboard listing route."""

    def test_root_returns_200(self, client):
        r = client.get("/")
        assert r.status_code == 200

    def test_root_contains_dashboard_links(self, client):
        r = client.get("/")
        assert "Reflections" in r.text
        assert "SDLC" in r.text
        assert "/reflections/" in r.text
        assert "/sdlc/" in r.text

    def test_root_contains_htmx(self, client):
        r = client.get("/")
        assert "htmx.org" in r.text

    def test_root_contains_css_link(self, client):
        r = client.get("/")
        assert "style.css" in r.text


class TestReflectionsRoutes:
    """Tests for reflections dashboard routes."""

    def test_overview_returns_200(self, client):
        r = client.get("/reflections/")
        assert r.status_code == 200

    def test_schedule_returns_200(self, client):
        r = client.get("/reflections/schedule/")
        assert r.status_code == 200

    def test_ignores_returns_200(self, client):
        r = client.get("/reflections/ignores/")
        assert r.status_code == 200

    def test_overview_has_nav_active(self, client):
        r = client.get("/reflections/")
        # The reflections nav link should have the active class
        assert "active" in r.text

    def test_history_nonexistent_reflection(self, client):
        r = client.get("/reflections/nonexistent/history/")
        assert r.status_code == 200
        assert "No run history" in r.text

    def test_detail_nonexistent_run(self, client):
        r = client.get("/reflections/nonexistent/history/0/")
        assert r.status_code == 200
        assert "not found" in r.text.lower()

    def test_partial_status_grid(self, client):
        """HTMX partial endpoint should return HTML fragment."""
        r = client.get("/reflections/_partials/status-grid/")
        assert r.status_code == 200


class TestSdlcRoutes:
    """Tests for SDLC observer routes."""

    def test_overview_returns_200(self, client):
        r = client.get("/sdlc/")
        assert r.status_code == 200

    def test_completed_returns_200(self, client):
        r = client.get("/sdlc/completed/")
        assert r.status_code == 200
        assert "not found" not in r.text.lower()

    def test_detail_nonexistent_pipeline(self, client):
        r = client.get("/sdlc/nonexistent-id/")
        assert r.status_code == 200
        assert "not found" in r.text.lower()

    def test_partial_active_pipelines(self, client):
        """HTMX partial endpoint should return HTML fragment."""
        r = client.get("/sdlc/_partials/active/")
        assert r.status_code == 200

    def test_overview_has_htmx_polling(self, client):
        r = client.get("/sdlc/")
        assert "hx-trigger" in r.text
        assert "every 5s" in r.text


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
