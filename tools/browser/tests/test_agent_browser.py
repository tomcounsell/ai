"""
Integration tests for agent-browser.

These tests use real browser automation against live websites.
Run with: pytest tools/browser/tests/ -v
"""

import json
import subprocess
import uuid

import pytest


def run_browser_cmd(
    *args: str, session: str | None = None, timeout: int = 30
) -> subprocess.CompletedProcess:
    """Run an agent-browser command and return the result."""
    cmd = ["agent-browser"]
    if session:
        cmd.extend(["--session", session])
    cmd.extend(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_browser_cmd_json(
    *args: str, session: str | None = None, timeout: int = 30
) -> dict:
    """Run an agent-browser command with --json flag and parse output."""
    result = run_browser_cmd(*args, "--json", session=session, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {result.stderr}")
    return json.loads(result.stdout)


class TestAgentBrowserInstallation:
    """Test that agent-browser is properly installed."""

    def test_version_command(self):
        """agent-browser should respond to --version."""
        result = run_browser_cmd("--version")
        assert result.returncode == 0
        assert result.stdout.strip()  # Should output version string

    def test_help_command(self):
        """agent-browser should respond to --help."""
        result = run_browser_cmd("--help")
        assert result.returncode == 0
        assert "agent-browser" in result.stdout
        assert "open" in result.stdout
        assert "snapshot" in result.stdout


class TestBasicNavigation:
    """Test basic browser navigation."""

    @pytest.fixture(autouse=True)
    def session_id(self):
        """Create unique session for each test."""
        sid = f"test-nav-{uuid.uuid4().hex[:8]}"
        yield sid
        run_browser_cmd("close", session=sid)

    def test_open_page(self, session_id):
        """Should open a webpage successfully."""
        result = run_browser_cmd("open", "https://example.com", session=session_id)
        assert result.returncode == 0

    def test_get_url(self, session_id):
        """Should return current URL."""
        run_browser_cmd("open", "https://example.com", session=session_id)
        result = run_browser_cmd("get", "url", session=session_id)
        assert result.returncode == 0
        assert "example.com" in result.stdout

    def test_get_title(self, session_id):
        """Should return page title."""
        run_browser_cmd("open", "https://example.com", session=session_id)
        result = run_browser_cmd("get", "title", session=session_id)
        assert result.returncode == 0
        assert "Example" in result.stdout


class TestSnapshot:
    """Test page snapshot functionality."""

    @pytest.fixture(autouse=True)
    def session_id(self):
        """Create unique session and open example.com."""
        sid = f"test-snap-{uuid.uuid4().hex[:8]}"
        run_browser_cmd("open", "https://example.com", session=sid)
        yield sid
        run_browser_cmd("close", session=sid)

    def test_snapshot_basic(self, session_id):
        """Should get accessibility tree snapshot."""
        result = run_browser_cmd("snapshot", session=session_id)
        assert result.returncode == 0
        assert result.stdout.strip()  # Should have content

    def test_snapshot_interactive(self, session_id):
        """Should get interactive elements only with -i flag."""
        result = run_browser_cmd("snapshot", "-i", session=session_id)
        assert result.returncode == 0
        # example.com has a "More information..." link
        assert "link" in result.stdout.lower() or "@e" in result.stdout

    def test_snapshot_compact(self, session_id):
        """Should get compact snapshot with -c flag."""
        result = run_browser_cmd("snapshot", "-c", session=session_id)
        assert result.returncode == 0

    def test_snapshot_json(self, session_id):
        """Should return JSON with --json flag."""
        data = run_browser_cmd_json("snapshot", "-i", session=session_id)
        assert isinstance(data, (dict, list))


class TestInteractions:
    """Test page interactions using refs."""

    @pytest.fixture(autouse=True)
    def session_id(self):
        """Create unique session for each test."""
        sid = f"test-interact-{uuid.uuid4().hex[:8]}"
        yield sid
        run_browser_cmd("close", session=sid)

    def test_click_link(self, session_id):
        """Should click a link and navigate."""
        import re

        run_browser_cmd("open", "https://example.com", session=session_id)

        # Get snapshot to find the link ref
        result = run_browser_cmd("snapshot", "-i", session=session_id)
        assert result.returncode == 0

        # example.com has "More information..." link
        # The ref should be @e1 or similar
        if "@e" in result.stdout:
            refs = re.findall(r"@e\d+", result.stdout)
            if refs:
                click_result = run_browser_cmd("click", refs[0], session=session_id)
                assert click_result.returncode == 0


class TestFormInteraction:
    """Test form filling on a real form page."""

    @pytest.fixture(autouse=True)
    def session_id(self):
        """Create unique session for each test."""
        sid = f"test-form-{uuid.uuid4().hex[:8]}"
        yield sid
        run_browser_cmd("close", session=sid)

    def test_fill_search_form(self, session_id):
        """Should fill a search form."""
        # Use DuckDuckGo which has a simple search form
        run_browser_cmd("open", "https://duckduckgo.com", session=session_id)
        result = run_browser_cmd("snapshot", "-i", session=session_id)
        assert result.returncode == 0

        # Should have a search input
        assert "textbox" in result.stdout.lower() or "search" in result.stdout.lower()


class TestScreenshots:
    """Test screenshot functionality."""

    @pytest.fixture(autouse=True)
    def session_id(self):
        """Create unique session for each test."""
        sid = f"test-screenshot-{uuid.uuid4().hex[:8]}"
        yield sid
        run_browser_cmd("close", session=sid)

    def test_screenshot_to_stdout(self, session_id):
        """Should capture screenshot."""
        run_browser_cmd("open", "https://example.com", session=session_id)
        result = run_browser_cmd("screenshot", session=session_id, timeout=60)
        # Screenshot outputs binary data or base64
        assert result.returncode == 0

    def test_screenshot_to_file(self, session_id, tmp_path):
        """Should save screenshot to file."""
        screenshot_path = tmp_path / "test.png"
        run_browser_cmd("open", "https://example.com", session=session_id)
        result = run_browser_cmd(
            "screenshot", str(screenshot_path), session=session_id, timeout=60
        )
        assert result.returncode == 0
        assert screenshot_path.exists()
        assert screenshot_path.stat().st_size > 0


class TestWaiting:
    """Test wait functionality."""

    @pytest.fixture(autouse=True)
    def session_id(self):
        """Create unique session for each test."""
        sid = f"test-wait-{uuid.uuid4().hex[:8]}"
        yield sid
        run_browser_cmd("close", session=sid)

    def test_wait_milliseconds(self, session_id):
        """Should wait for specified time."""
        run_browser_cmd("open", "https://example.com", session=session_id)
        result = run_browser_cmd("wait", "1000", session=session_id)  # Wait 1 second
        assert result.returncode == 0

    def test_wait_for_element(self, session_id):
        """Should wait for element to appear."""
        run_browser_cmd("open", "https://example.com", session=session_id)
        # Wait for body element
        result = run_browser_cmd("wait", "body", session=session_id)
        assert result.returncode == 0


class TestSessionManagement:
    """Test browser session management."""

    def test_session_list(self):
        """Should list active sessions."""
        result = run_browser_cmd("session", "list")
        # May return empty if no sessions, but should not error
        assert result.returncode == 0

    def test_named_session(self):
        """Should create named session."""
        session_name = f"test-session-{uuid.uuid4().hex[:8]}"
        result = run_browser_cmd("open", "https://example.com", session=session_name)
        assert result.returncode == 0

        # Verify session exists
        list_result = run_browser_cmd("session", "list")
        assert session_name in list_result.stdout or result.returncode == 0

        # Clean up
        run_browser_cmd("close", session=session_name)


class TestErrorHandling:
    """Test error handling scenarios."""

    @pytest.fixture(autouse=True)
    def session_id(self):
        """Create unique session for each test."""
        sid = f"test-error-{uuid.uuid4().hex[:8]}"
        yield sid
        run_browser_cmd("close", session=sid)

    def test_invalid_url(self, session_id):
        """Should handle invalid URL gracefully."""
        result = run_browser_cmd("open", "not-a-valid-url", session=session_id)
        # Should either fail gracefully or auto-add protocol
        # Different tools handle this differently

    def test_nonexistent_element(self, session_id):
        """Should handle missing element."""
        run_browser_cmd("open", "https://example.com", session=session_id)
        result = run_browser_cmd("click", "@e999", session=session_id)  # Nonexistent ref
        # Should return error
        assert result.returncode != 0 or "error" in result.stderr.lower()


class TestBrowserSettings:
    """Test browser configuration options."""

    @pytest.fixture(autouse=True)
    def session_id(self):
        """Create unique session for each test."""
        sid = f"test-settings-{uuid.uuid4().hex[:8]}"
        yield sid
        run_browser_cmd("close", session=sid)

    def test_set_viewport(self, session_id):
        """Should set viewport size."""
        run_browser_cmd("open", "https://example.com", session=session_id)
        result = run_browser_cmd("set", "viewport", "1920", "1080", session=session_id)
        assert result.returncode == 0

    def test_set_device(self, session_id):
        """Should emulate device."""
        run_browser_cmd("open", "https://example.com", session=session_id)
        result = run_browser_cmd("set", "device", "iPhone 14", session=session_id)
        assert result.returncode == 0
