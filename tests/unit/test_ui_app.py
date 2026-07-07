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

        assert _filter_format_duration(5.2) == "5s"
        assert _filter_format_duration(59.9) == "60s"

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


class TestEmailHealthAlerts:
    """The dashboard's ``email`` health field surfaces email:auth_failed (A3)
    and email:resolver_unavailable (A2) — issue #1817. Both /health and
    /dashboard.json expose the same email_alert / email_alert_detail fields
    rather than a new alert surface."""

    def test_health_surfaces_auth_failed_alert(self, client):
        from unittest.mock import MagicMock, patch

        mock_r = MagicMock()
        mock_r.get.side_effect = lambda key: {
            "email:auth_failed": "1700000000.0:AUTHENTICATIONFAILED",
        }.get(key)

        with patch("redis.Redis.from_url", return_value=mock_r):
            response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "error"
        assert data["email_alert"] == "auth_failed"
        assert "AUTHENTICATIONFAILED" in data["email_alert_detail"]

    def test_health_surfaces_resolver_unavailable_alert(self, client):
        from unittest.mock import MagicMock, patch

        mock_r = MagicMock()
        mock_r.get.side_effect = lambda key: {
            "email:resolver_unavailable": "1700000000.0:<msg-1@example.com>",
        }.get(key)

        with patch("redis.Redis.from_url", return_value=mock_r):
            response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "error"
        assert data["email_alert"] == "resolver_unavailable"
        assert "<msg-1@example.com>" in data["email_alert_detail"]

    def test_health_no_alert_when_keys_absent(self, client):
        from unittest.mock import MagicMock, patch

        mock_r = MagicMock()
        mock_r.get.return_value = None

        with patch("redis.Redis.from_url", return_value=mock_r):
            response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["email_alert"] is None
        assert data["email_alert_detail"] is None

    def test_dashboard_json_surfaces_auth_failed_alert(self, client):
        from unittest.mock import MagicMock, patch

        mock_r = MagicMock()
        mock_r.get.side_effect = lambda key: {
            "email:auth_failed": "1700000000.0:AUTHENTICATIONFAILED",
        }.get(key)

        with patch("redis.Redis.from_url", return_value=mock_r):
            response = client.get("/dashboard.json")

        assert response.status_code == 200
        health = response.json()["health"]
        assert health["email"] == "error"
        assert health["email_alert"] == "auth_failed"


class TestDashboardSessionSerialization:
    """dashboard.json session objects carry the headless-runner resume
    scalars (#1924 Success Criterion 3): dev_agent_id, runner_cwd,
    claude_version."""

    def test_dashboard_json_sessions_include_resume_scalars(self, client):
        from unittest.mock import patch

        from ui.data.sdlc import PipelineProgress

        progress = PipelineProgress(
            agent_session_id="resume-scalars-1",
            dev_agent_id="agent-dev42",
            runner_cwd="/Users/x/src/ai/.worktrees/slug",
            claude_version="2.0.5",
        )
        with patch("ui.data.sdlc.get_all_sessions", return_value=[progress]):
            response = client.get("/dashboard.json")

        assert response.status_code == 200
        (session,) = [
            s for s in response.json()["sessions"] if s["agent_session_id"] == "resume-scalars-1"
        ]
        assert session["dev_agent_id"] == "agent-dev42"
        assert session["runner_cwd"] == "/Users/x/src/ai/.worktrees/slug"
        assert session["claude_version"] == "2.0.5"

    def test_dashboard_json_resume_scalars_default_none(self, client):
        """PipelineProgress without the scalars set serializes them as None
        (old records never break the dashboard)."""
        from unittest.mock import patch

        from ui.data.sdlc import PipelineProgress

        progress = PipelineProgress(agent_session_id="resume-scalars-2")
        with patch("ui.data.sdlc.get_all_sessions", return_value=[progress]):
            response = client.get("/dashboard.json")

        assert response.status_code == 200
        (session,) = [
            s for s in response.json()["sessions"] if s["agent_session_id"] == "resume-scalars-2"
        ]
        assert session["dev_agent_id"] is None
        assert session["runner_cwd"] is None
        assert session["claude_version"] is None


class TestArchiveHealth:
    """The dashboard's ``archive`` health block surfaces
    ``agent.session_archive.get_archive_status()`` -- issue #1825,
    docs/plans/session-archive-sqlite.md Task 4 (operator surfaces)."""

    def test_dashboard_json_has_archive_block(self, client):
        from unittest.mock import patch

        fake_status = {
            "db_path": "/tmp/session_archive.db",
            "exists": True,
            "row_count": 3,
            "last_export_ts": 1700000000.0,
            "last_export_age_s": 5.0,
            "last_periodic_export_ts": 1700000000.0,
            "last_periodic_export_age_s": 5.0,
            "kind": "periodic",
            "healthy": True,
        }
        with patch("agent.session_archive.get_archive_status", return_value=fake_status):
            response = client.get("/dashboard.json")

        assert response.status_code == 200
        health = response.json()["health"]
        assert "archive" in health
        archive = health["archive"]
        assert archive["status"] == "ok"
        assert archive["healthy"] is True
        assert archive["row_count"] == 3
        assert archive["last_export_age_s"] == 5.0
        assert archive["last_periodic_export_age_s"] == 5.0
        assert archive["kind"] == "periodic"

    def test_dashboard_json_archive_degrades_gracefully_when_missing(
        self, client, tmp_path, monkeypatch
    ):
        """A nonexistent archive DB (e.g. fresh machine, worker never ran) must
        never 500 the dashboard -- it surfaces a clean 'missing' status."""
        missing_path = tmp_path / "does-not-exist" / "session_archive.db"
        monkeypatch.setenv("SESSION_ARCHIVE_DB_PATH", str(missing_path))

        response = client.get("/dashboard.json")

        assert response.status_code == 200
        archive = response.json()["health"]["archive"]
        assert archive["status"] == "missing"
        assert archive["healthy"] is False
        assert archive["last_export_age_s"] is None
        assert archive["row_count"] == 0

    def test_health_endpoint_surfaces_archive_flat_keys(self, client):
        from unittest.mock import patch

        fake_status = {
            "db_path": "/tmp/session_archive.db",
            "exists": True,
            "row_count": 7,
            "last_export_ts": 1700000000.0,
            "last_export_age_s": 12.0,
            "last_periodic_export_ts": 1700000000.0,
            "last_periodic_export_age_s": 12.0,
            "kind": "terminal",
            "healthy": True,
        }
        with patch("agent.session_archive.get_archive_status", return_value=fake_status):
            response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["archive"] == "ok"
        assert data["archive_healthy"] is True
        assert data["archive_row_count"] == 7
        assert data["archive_last_export_age_s"] == 12.0


class TestRedisOffloadDashboardMetric:
    """The dashboard's ``health.redis_offload`` block surfaces the drain-loop
    idle-check latency gauges from agent/redis_offload.py — issue #1826."""

    def test_dashboard_json_has_redis_offload_block(self, client):
        from agent.redis_offload import reset_max_redis_latency

        reset_max_redis_latency()

        response = client.get("/dashboard.json")

        assert response.status_code == 200
        health = response.json()["health"]
        assert "redis_offload" in health
        redis_offload = health["redis_offload"]
        assert redis_offload["label"] == "drain-loop idle-check latency"
        assert redis_offload["p95_latency_s"] == 0.0
        assert redis_offload["max_latency_s"] == 0.0
        assert redis_offload["last_latency_s"] == 0.0

    def test_dashboard_json_redis_offload_values_are_numeric(self, client):
        from agent.redis_offload import _record

        _record(0.05)
        _record(0.2)

        response = client.get("/dashboard.json")

        assert response.status_code == 200
        redis_offload = response.json()["health"]["redis_offload"]
        assert isinstance(redis_offload["p95_latency_s"], (int, float))
        assert isinstance(redis_offload["max_latency_s"], (int, float))
        assert isinstance(redis_offload["last_latency_s"], (int, float))
        assert redis_offload["p95_latency_s"] is not None
        assert redis_offload["max_latency_s"] is not None
        assert redis_offload["last_latency_s"] is not None


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
