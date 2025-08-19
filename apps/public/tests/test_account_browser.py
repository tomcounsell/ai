"""
End-to-end browser tests for account settings.

This module provides browser-based tests for the account settings functionality,
using the browser-use and Playwright framework.
"""

import os
import uuid

import pytest
from django.contrib.auth import get_user_model

# Import test pattern base classes if available
try:
    import playwright.async_api
    from browser_use import BrowserAgent
    from playwright.async_api import Browser, BrowserContext, Page

    from apps.public.tests.test_e2e_patterns import (
        E2ETestBase,
        asyncio_mark,
        browser_test,
    )

    BROWSER_TESTING_AVAILABLE = True
except ImportError:
    # Create dummy classes and decorators for when dependencies aren't available
    class E2ETestBase:
        pass

    browser_test = lambda cls: cls
    asyncio_mark = lambda f: f

    class Page:
        pass

    BROWSER_TESTING_AVAILABLE = False

# Get User model
User = get_user_model()

# Skip all tests if browser testing dependencies aren't available
pytestmark = [
    pytest.mark.skipif(
        not BROWSER_TESTING_AVAILABLE,
        reason="Browser testing dependencies not installed",
    ),
    pytest.mark.django_db,  # Allow database access for all tests
]

# Test configuration
SERVER_URL = "http://localhost:8000"
SCREENSHOTS_DIR = "test_screenshots/account"


@browser_test
@pytest.mark.asyncio
class TestAccountSettingsBrowser:
    """Browser-based tests for account settings functionality."""

    @classmethod
    def setup_class(cls):
        """Set up class for testing."""
        # Create screenshots directory
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

        # Check if server is running
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(("localhost", 8000))
        sock.close()
        if result != 0:
            pytest.skip("Django server not running at http://localhost:8000")

    @pytest.fixture
    def test_user(self):
        """Fixture to create a test user."""
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

    @pytest.fixture
    async def browser(self):
        """Fixture to create a browser instance."""
        playwright_obj = await playwright.async_api.async_playwright().start()
        browser_instance = await playwright_obj.chromium.launch(headless=True)
        yield browser_instance
        await browser_instance.close()
        await playwright_obj.stop()

    @pytest.fixture
    async def page(self, browser):
        """Fixture to create a browser page."""
        context = await browser.new_context(viewport={"width": 1280, "height": 720})
        page = await context.new_page()
        yield page
        await context.close()

    async def take_screenshot(self, page, filename):
        """Take a screenshot and save it to the screenshots directory."""
        filepath = os.path.join(SCREENSHOTS_DIR, filename)
        await page.screenshot(path=filepath)
        return filepath

    async def login_user(self, page, username, password):
        """Log in the user through the browser."""
        try:
            # Navigate to login page
            await page.goto(f"{SERVER_URL}/account/login")

            # Fill login form
            await page.fill('input[name="username"]', username)
            await page.fill('input[name="password"]', password)

            # Take a screenshot before submission
            await self.take_screenshot(page, f"login_{username}.png")

            # Submit form
            await page.click('button[type="submit"]')

            # Wait for navigation to complete
            await page.wait_for_load_state("networkidle")

            # Check if login was successful
            logout_present = (
                await page.locator(
                    "form.logout-form, .account-menu, .user-menu"
                ).count()
                > 0
            )

            if not logout_present:
                # Alternative check - if we're redirected away from login page
                if "/login" not in page.url:
                    return True

                # Take screenshot of failed login
                await self.take_screenshot(page, f"login_failed_{username}.png")
                return False

            return True

        except Exception as e:
            print(f"Login error: {str(e)}")
            await self.take_screenshot(page, "login_error.png")
            return False

    async def test_view_account_settings(self, page, test_user):
        """Test viewing account settings page."""
        user, username, password, email = test_user

        # Login
        login_success = await self.login_user(page, username, password)
        assert login_success, "Failed to log in"

        # Navigate to account settings
        await page.goto(f"{SERVER_URL}/account/settings")

        # Wait for page to load
        await page.wait_for_load_state("networkidle")

        # Take screenshot
        await self.take_screenshot(page, "account_settings.png")

        # Check that page contains expected elements
        assert (
            await page.locator('input[name="email"]').count() > 0
        ), "Email field not found"
        assert (
            await page.locator('input[name="first_name"]').count() > 0
        ), "First name field not found"
        assert (
            await page.locator('input[name="last_name"]').count() > 0
        ), "Last name field not found"

        # Verify the email field shows the current email
        email_value = await page.input_value('input[name="email"]')
        assert email_value == email, f"Email field shows {email_value}, not {email}"

    async def test_update_profile(self, page, test_user):
        """Test updating user profile information."""
        user, username, password, email = test_user

        # Login
        login_success = await self.login_user(page, username, password)
        assert login_success, "Failed to log in"

        # Navigate to account settings
        await page.goto(f"{SERVER_URL}/account/settings")
        await page.wait_for_load_state("networkidle")

        # Update profile information
        await page.fill('input[name="first_name"]', "Updated")
        await page.fill('input[name="last_name"]', "Name")

        # Take screenshot before submission
        await self.take_screenshot(page, "update_profile_form.png")

        # Submit form
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("networkidle")

        # Take screenshot after submission
        await self.take_screenshot(page, "update_profile_result.png")

        # Check for success message
        success_message = (
            await page.locator('.alert-success, .notification, [role="alert"]').count()
            > 0
        )
        assert success_message, "Success message not displayed"

        # Verify form fields were updated
        first_name = await page.input_value('input[name="first_name"]')
        last_name = await page.input_value('input[name="last_name"]')

        assert first_name == "Updated", f"First name shows {first_name}, not 'Updated'"
        assert last_name == "Name", f"Last name shows {last_name}, not 'Name'"

        # Refresh the user from database to verify changes were saved
        user.refresh_from_db()
        assert (
            user.first_name == "Updated"
        ), f"User first_name is {user.first_name}, not 'Updated'"
        assert (
            user.last_name == "Name"
        ), f"User last_name is {user.last_name}, not 'Name'"

    async def test_form_validation(self, page, test_user):
        """Test form validation with invalid data."""
        user, username, password, email = test_user

        # Login
        login_success = await self.login_user(page, username, password)
        assert login_success, "Failed to log in"

        # Navigate to account settings
        await page.goto(f"{SERVER_URL}/account/settings")
        await page.wait_for_load_state("networkidle")

        # Enter invalid email
        await page.fill('input[name="email"]', "not-an-email")

        # Take screenshot before submission
        await self.take_screenshot(page, "invalid_email_form.png")

        # Submit form
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("networkidle")

        # Take screenshot after submission
        await self.take_screenshot(page, "invalid_email_result.png")

        # Check for validation error
        error_message = (
            await page.locator('.invalid-feedback, .error, [role="alert"]').count() > 0
        )
        assert error_message, "Error message not displayed"

        # Verify we're still on the settings page
        assert "settings" in page.url, "Not on settings page after validation error"

        # Verify the database wasn't updated
        user.refresh_from_db()
        assert (
            user.email == email
        ), f"User email changed to {user.email} despite validation error"
