"""
End-to-end browser tests for account settings.

These tests require Playwright and a running Django dev server.
They are skipped when Playwright is not installed or the server
is not running.

Run with: python tools/testing/browser_test_runner.py apps/public/tests/test_account_browser.py
"""

import os
import uuid

import pytest
from django.contrib.auth import get_user_model

try:
    import playwright.sync_api  # noqa: F401

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

User = get_user_model()

pytestmark = [
    pytest.mark.skipif(
        not PLAYWRIGHT_AVAILABLE,
        reason="Playwright not installed. Run: uv sync --extra e2e",
    ),
]

SERVER_URL = "http://localhost:8000"
SCREENSHOTS_DIR = "test_screenshots/account"


def _is_server_running() -> bool:
    """Check if the Django server is running."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(("localhost", 8000))
    sock.close()
    return result == 0


@pytest.fixture
def ensure_server():
    """Skip test if the Django dev server is not running."""
    if not _is_server_running():
        pytest.skip("Django server not running at http://localhost:8000")


@pytest.fixture
def test_user(db):
    """Create a test user for browser tests."""
    username = f"browseruser_{uuid.uuid4().hex[:8]}"
    password = "securepass123"
    email = f"{username}@example.com"
    user = User.objects.create_user(
        username=username,
        email=email,
        password=password,
        first_name="Browser",
        last_name="Test",
    )
    return user, username, password, email


def _login_user(page, username: str, password: str) -> bool:
    """Log in the user through the browser."""
    try:
        page.goto(f"{SERVER_URL}/account/login")
        page.fill('input[name="username"]', username)
        page.fill('input[name="password"]', password)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        if "/login" not in page.url:
            return True

        return False
    except Exception as e:
        print(f"Login error: {e}")
        return False


@pytest.mark.usefixtures("ensure_server")
class TestAccountSettingsBrowser:
    """Browser-based tests for account settings functionality."""

    @classmethod
    def setup_class(cls):
        """Set up class for testing."""
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

    def test_view_account_settings(self, page, test_user):
        """Test viewing account settings page."""
        user, username, password, email = test_user

        login_success = _login_user(page, username, password)
        assert login_success, "Failed to log in"

        page.goto(f"{SERVER_URL}/account/settings")
        page.wait_for_load_state("networkidle")
        page.screenshot(path=os.path.join(SCREENSHOTS_DIR, "account_settings.png"))

        assert page.locator('input[name="email"]').count() > 0, "Email field not found"
        assert (
            page.locator('input[name="first_name"]').count() > 0
        ), "First name field not found"
        assert (
            page.locator('input[name="last_name"]').count() > 0
        ), "Last name field not found"

        email_value = page.input_value('input[name="email"]')
        assert email_value == email, f"Email field shows {email_value}, not {email}"

    def test_update_profile(self, page, test_user):
        """Test updating user profile information."""
        user, username, password, email = test_user

        login_success = _login_user(page, username, password)
        assert login_success, "Failed to log in"

        page.goto(f"{SERVER_URL}/account/settings")
        page.wait_for_load_state("networkidle")

        page.fill('input[name="first_name"]', "Updated")
        page.fill('input[name="last_name"]', "Name")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        success_message = (
            page.locator('.alert-success, .notification, [role="alert"]').count() > 0
        )
        assert success_message, "Success message not displayed"

        first_name = page.input_value('input[name="first_name"]')
        last_name = page.input_value('input[name="last_name"]')

        assert first_name == "Updated", f"First name shows {first_name}, not 'Updated'"
        assert last_name == "Name", f"Last name shows {last_name}, not 'Name'"

        user.refresh_from_db()
        assert user.first_name == "Updated"
        assert user.last_name == "Name"

    def test_form_validation(self, page, test_user):
        """Test form validation with invalid data."""
        user, username, password, email = test_user

        login_success = _login_user(page, username, password)
        assert login_success, "Failed to log in"

        page.goto(f"{SERVER_URL}/account/settings")
        page.wait_for_load_state("networkidle")

        page.fill('input[name="email"]', "not-an-email")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        error_message = (
            page.locator('.invalid-feedback, .error, [role="alert"]').count() > 0
        )
        assert error_message, "Error message not displayed"

        assert "settings" in page.url, "Not on settings page after validation error"

        user.refresh_from_db()
        assert (
            user.email == email
        ), f"User email changed to {user.email} despite validation error"
