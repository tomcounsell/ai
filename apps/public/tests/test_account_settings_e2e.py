"""
End-to-end tests for the account settings page functionality.

DEPRECATED: The browser tests in this file have been migrated to test_account_browser.py
which uses a more consistent pattern. Only the Django TestCase tests should be used from this file.

This test file demonstrates how to test a user-facing feature with Django's TestCase/Client.
These tests cover:
1. Viewing account settings page
2. Updating user profile information
3. Changing password
4. Form validation
"""

import os
import uuid
from typing import TYPE_CHECKING

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

User = get_user_model()

# Check for browser testing dependencies
try:
    import playwright.async_api
    import pytest_asyncio
    from playwright.async_api import Browser, BrowserContext

    # Define Page for type hints
    if TYPE_CHECKING:
        from playwright.async_api import Page
    else:
        Page = playwright.async_api.Page
    BROWSER_TESTING_AVAILABLE = True
except ImportError:
    BROWSER_TESTING_AVAILABLE = False

    # Create dummy classes for type hints
    class Page:
        pass


# Conditional asyncio marker
try:
    import pytest_asyncio

    HAS_PYTEST_ASYNCIO = True
    asyncio_mark = pytest.mark.asyncio
except ImportError:
    HAS_PYTEST_ASYNCIO = False
    asyncio_mark = lambda f: f

# Skip marker for browser tests
browser_test = pytest.mark.skipif(
    not BROWSER_TESTING_AVAILABLE or not HAS_PYTEST_ASYNCIO,
    reason="Browser testing packages not installed. Run `uv add --dev browser-use playwright pytest-asyncio`",
)


class AccountSettingsTestCase(TestCase):
    """Tests for account settings using Django's TestCase."""

    def setUp(self):
        """Set up test data with a regular user."""
        # Create a unique user for each test
        self.username = f"user_{uuid.uuid4().hex[:8]}"
        self.password = "securepass123"
        self.email = f"{self.username}@example.com"
        self.user = User.objects.create_user(
            username=self.username,
            email=self.email,
            password=self.password,
            first_name="Original",
            last_name="User",
        )
        self.client = Client()

        # Login the user
        login_success = self.client.login(
            username=self.username, password=self.password
        )
        self.assertTrue(login_success, "Login failed during setup")

    def test_view_settings_page(self):
        """Test that an authenticated user can view their settings page."""
        # Try accessing the settings page
        response = self.client.get(reverse("public:account-settings"))

        # Check we can access the page
        self.assertEqual(response.status_code, 200)

        # Verify the page contains expected elements
        self.assertContains(response, self.email)  # Should show current email
        self.assertContains(response, "first_name")  # Should have first name field
        self.assertContains(response, "last_name")  # Should have last name field

    def test_update_profile_info(self):
        """Test updating basic profile information."""
        # Prepare updated profile data
        updated_data = {
            "email": self.email,  # Keep the same email
            "first_name": "Updated",
            "last_name": "Name",
        }

        # Submit the profile update form
        response = self.client.post(
            reverse("public:account-settings"),
            updated_data,
            follow=True,  # Follow redirects
        )

        # Check the response is successful
        self.assertEqual(response.status_code, 200)

        # Verify the user was updated in the database
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Updated")
        self.assertEqual(self.user.last_name, "Name")

        # Check for success message
        message_list = list(response.context["messages"])
        self.assertTrue(len(message_list) > 0)
        self.assertIn("updated", str(message_list[0]).lower())

    def test_change_password(self):
        """Test changing the user's password."""
        # Define password change data
        password_data = {
            "old_password": self.password,
            "new_password1": "NewSecurePass456",
            "new_password2": "NewSecurePass456",
        }

        # Get the password change URL
        change_password_url = reverse("public:password-change")

        # Submit the password change form
        response = self.client.post(
            change_password_url,
            password_data,
            follow=True,  # Follow redirects
        )

        # Check the response is successful
        self.assertEqual(response.status_code, 200)

        # Verify the password was changed by logging in with the new password
        self.client.logout()
        login_success = self.client.login(
            username=self.username, password="NewSecurePass456"
        )
        self.assertTrue(login_success, "Login with new password failed")

    def test_form_validation(self):
        """Test form validation for invalid data."""
        # Try to update with invalid email
        invalid_data = {
            "email": "not-an-email",
            "first_name": "Valid",
            "last_name": "Name",
        }

        # Submit the form with invalid data
        response = self.client.post(reverse("public:account-settings"), invalid_data)

        # Should stay on the same page with errors
        self.assertEqual(response.status_code, 200)

        # Check for form errors (based on account.py's form context variable naming)
        self.assertTrue(response.context["user_form"].errors)

        # Verify the user was NOT updated
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.first_name, "Valid")
        self.assertEqual(self.user.first_name, "Original")


@browser_test
@pytest.mark.django_db
class AccountSettingsBrowserTestCase:
    """Tests for account settings using browser automation."""

    # Screenshot directory
    SCREENSHOT_DIR = "test_screenshots"

    @classmethod
    def setup_class(cls):
        """Set up test data and screenshots directory."""
        # Create screenshots directory if it doesn't exist
        if not os.path.exists(cls.SCREENSHOT_DIR):
            os.makedirs(cls.SCREENSHOT_DIR)

        # Server URL
        cls.server_url = "http://localhost:8000"

        # Check if server is running
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(("localhost", 8000))
        if result != 0:
            pytest.skip("Django server not running at http://localhost:8000")

    def setUp(self):
        """Set up a test user for each test."""
        # Create a unique user for each test
        self.username = f"buser_{uuid.uuid4().hex[:8]}"
        self.password = "securepass123"
        self.email = f"{self.username}@example.com"
        self.user = User.objects.create_user(
            username=self.username,
            email=self.email,
            password=self.password,
            first_name="Browser",
            last_name="Test",
        )

    async def take_screenshot(self, page: "Page", filename: str) -> str:
        """Take a screenshot and save it to the screenshots directory."""
        filepath = os.path.join(self.SCREENSHOT_DIR, filename)
        await page.screenshot(path=filepath)
        return filepath

    async def is_server_running(self) -> bool:
        """Check if the Django server is running."""
        try:
            import socket

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex(("localhost", 8000))
            sock.close()
            return result == 0
        except:
            return False

    async def login_user(self, page: "Page") -> bool:
        """Log in the test user through the browser."""
        try:
            # Go to login page
            await page.goto(f"{self.server_url}/account/login")

            # Fill login form
            await page.fill('input[name="username"]', self.username)
            await page.fill('input[name="password"]', self.password)

            # Submit form
            await page.click('button[type="submit"]')

            # Wait for navigation
            await page.wait_for_load_state("networkidle")

            # Check if we're logged in by looking for account-related elements
            logged_in = (
                await page.locator(
                    '.account-menu, .user-menu, [aria-label="User menu"]'
                ).count()
                > 0
            )

            # If we can't find a menu, check for the username on the page
            if not logged_in:
                page_content = await page.content()
                logged_in = self.username in page_content

            return logged_in
        except Exception as e:
            print(f"Login error: {e}")
            return False

    # Define fixtures with pytest_asyncio
    @pytest_asyncio.fixture
    async def browser(self):
        """Fixture to provide a browser instance."""
        playwright_obj = await playwright.async_api.async_playwright().start()
        browser = await playwright_obj.chromium.launch(headless=True)
        yield browser
        await browser.close()
        await playwright_obj.stop()

    @pytest_asyncio.fixture
    async def page(self, browser):
        """Fixture to provide a browser page."""
        context = await browser.new_context(viewport={"width": 1280, "height": 720})
        page = await context.new_page()
        yield page
        await context.close()

    @pytest.mark.asyncio
    async def test_update_profile_browser(self, page):
        """Test updating profile information using a real browser."""
        # Login
        login_success = await self.login_user(page)
        assert login_success, "Login failed"

        # Navigate to settings page
        await page.goto(f"{self.server_url}/account/settings")

        # Take screenshot of settings page
        await self.take_screenshot(page, "account_settings_page.png")

        # Fill in the form with updated info
        await page.fill('input[name="first_name"]', "Updated")
        await page.fill('input[name="last_name"]', "FromBrowser")

        # Take screenshot before submitting
        await self.take_screenshot(page, "account_settings_filled.png")

        # Submit the form
        await page.click('button[type="submit"]')

        # Wait for navigation and form processing
        await page.wait_for_load_state("networkidle")

        # Take screenshot after update
        await self.take_screenshot(page, "account_settings_after_update.png")

        # Verify success via success message or updated form values
        page_content = await page.content()

        # Look for success notification or toast
        success_indicators = [
            "success" in page_content.lower(),
            "updated" in page_content.lower(),
            "saved" in page_content.lower(),
        ]
        assert any(success_indicators), "Success message not found"

        # Verify the form shows updated values
        first_name_value = await page.input_value('input[name="first_name"]')
        last_name_value = await page.input_value('input[name="last_name"]')

        assert first_name_value == "Updated", "First name not updated"
        assert last_name_value == "FromBrowser", "Last name not updated"

    @pytest.mark.asyncio
    async def test_failed_validation_browser(self, page):
        """Test form validation with invalid data using a real browser."""
        # Login
        login_success = await self.login_user(page)
        assert login_success, "Login failed"

        # Navigate to settings page
        await page.goto(f"{self.server_url}/account/settings")

        # Fill in the form with invalid email
        await page.fill('input[name="email"]', "not-an-email")

        # Take screenshot before submitting
        await self.take_screenshot(page, "account_settings_invalid.png")

        # Submit the form
        await page.click('button[type="submit"]')

        # Wait for validation response
        await page.wait_for_load_state("networkidle")

        # Take screenshot after validation
        await self.take_screenshot(page, "account_settings_validation_error.png")

        # Check for validation error
        error_count = await page.locator(
            '.error, .invalid-feedback, [role="alert"]'
        ).count()
        assert error_count > 0, "Validation error not displayed"

        # Verify we're still on the settings page
        current_url = page.url
        assert (
            "settings" in current_url.lower()
        ), "Not on settings page after validation error"
