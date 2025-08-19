"""
End-to-end tests for Django admin site login and logout.

This module demonstrates a practical implementation of browser-based
testing for essential admin functionality that works with both
the standard Django test client and browser-use when available.
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


class AdminSiteTestCase(TestCase):
    """Tests for Django admin site login/logout using the Django test client."""

    def setUp(self):
        """Set up test data with a superuser."""
        # Create a unique admin user for each test
        self.username = f"admin_{uuid.uuid4().hex[:8]}"
        self.password = "securepass123"
        self.admin = User.objects.create_superuser(
            username=self.username,
            email=f"{self.username}@example.com",
            password=self.password,
        )
        self.client = Client()

    def test_admin_login(self):
        """Test that an admin user can log in to the admin site."""
        # Get the admin login URL
        login_url = reverse("admin:login")

        # Try accessing a protected admin page - should redirect to login
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 302)

        # Login with correct credentials
        response = self.client.post(
            login_url,
            {"username": self.username, "password": self.password, "next": "/admin/"},
            follow=True,
        )

        # Check we're logged in and can access the admin
        self.assertEqual(response.status_code, 200)

        # Check for common admin elements - this project uses Django Unfold
        self.assertContains(response, "Database")  # Admin section title
        self.assertContains(response, "Users")  # Users model in admin

    def test_admin_logout(self):
        """Test that an admin user can log out from the admin site."""
        # First log in
        self.client.login(username=self.username, password=self.password)

        # Check we can access the admin
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)

        # Now log out - Django uses POST for logout
        logout_url = reverse("admin:logout")
        response = self.client.post(logout_url, follow=True)

        # Check we're logged out successfully
        self.assertEqual(response.status_code, 200)

        # Try accessing admin again - should fail or redirect
        response = self.client.get(reverse("admin:index"))
        self.assertNotEqual(response.status_code, 200)


@browser_test
@asyncio_mark
class AdminBrowserTestCase(TestCase):
    """Tests for Django admin site login/logout using a real browser."""

    # Screenshot directory
    SCREENSHOT_DIR = "test_screenshots"

    @classmethod
    def setup_class(cls):
        """Set up test data once for all test methods."""
        # Create screenshots directory if it doesn't exist
        if not os.path.exists(cls.SCREENSHOT_DIR):
            os.makedirs(cls.SCREENSHOT_DIR)

        # Create a unique admin user
        cls.username = f"admin_{uuid.uuid4().hex[:8]}"
        cls.password = "securepass123"
        cls.admin = User.objects.create_superuser(
            username=cls.username,
            email=f"{cls.username}@example.com",
            password=cls.password,
        )

        # Server URL
        cls.server_url = "http://localhost:8000"

    @pytest.fixture(scope="function")
    async def browser(self):
        """Fixture to provide a browser instance."""
        # Start browser
        playwright_obj = await playwright.async_api.async_playwright().start()
        browser = await playwright_obj.chromium.launch(headless=True)

        yield browser

        # Cleanup
        await browser.close()
        await playwright_obj.stop()

    @pytest.fixture(scope="function")
    async def context(self, browser):
        """Fixture to provide a browser context."""
        context = await browser.new_context(viewport={"width": 1280, "height": 720})
        yield context
        await context.close()

    @pytest.fixture(scope="function")
    async def page(self, context):
        """Fixture to provide a browser page."""
        page = await context.new_page()
        yield page
        await page.close()

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

    async def test_admin_login_browser(self, page):
        """Test logging into the admin site using a real browser."""
        # Check if server is running
        if not await self.is_server_running():
            pytest.skip("Django server not running at http://localhost:8000")

        # Go to the admin login page
        await page.goto(f"{self.server_url}/admin/login/")

        # Take screenshot of login page
        await self.take_screenshot(page, "admin_login_page.png")

        # Fill in the login form
        await page.fill('input[name="username"]', self.username)
        await page.fill('input[name="password"]', self.password)

        # Take screenshot before submitting
        await self.take_screenshot(page, "admin_login_filled.png")

        # Submit the form
        await page.click('input[type="submit"]')

        # Wait for navigation to complete
        await page.wait_for_load_state("networkidle")

        # Take screenshot after login
        await self.take_screenshot(page, "admin_logged_in.png")

        # Verify we're logged in by checking for admin elements
        page_content = await page.content()
        assert "Database" in page_content  # Admin section title
        assert "Users" in page_content  # Users model

    async def test_admin_logout_browser(self, page):
        """Test logging out from the admin site using a real browser."""
        # Check if server is running
        if not await self.is_server_running():
            pytest.skip("Django server not running at http://localhost:8000")

        # Login first
        await page.goto(f"{self.server_url}/admin/login/")
        await page.fill('input[name="username"]', self.username)
        await page.fill('input[name="password"]', self.password)
        await page.click('input[type="submit"]')
        await page.wait_for_load_state("networkidle")

        # Verify login was successful
        page_content = await page.content()
        assert "Database" in page_content

        # Take screenshot while logged in
        await self.take_screenshot(page, "admin_before_logout.png")

        # In Unfold admin, the logout link might be in a dropdown menu
        # First check if it's directly visible
        logout_link_visible = await page.locator('a:has-text("Log out")').count() > 0

        if logout_link_visible:
            # Click the logout link directly if visible
            await page.click('a:has-text("Log out")')
        else:
            # It might be in a user menu that needs to be opened first
            # Find and click the user menu toggle
            await page.click('.user-menu, .user-dropdown, [aria-label="User menu"]')
            # Wait for the dropdown to open
            await page.wait_for_timeout(300)  # Short wait for animation
            # Now click the logout link
            await page.click('a:has-text("Log out")')

        await page.wait_for_load_state("networkidle")

        # Take screenshot after logout
        await self.take_screenshot(page, "admin_after_logout.png")

        # Verify we're logged out - either on login page or logged out confirmation
        page_content = await page.content()
        assert any(
            [
                "login" in page_content.lower(),
                "logged out" in page_content.lower(),
                "sign in" in page_content.lower(),
            ]
        )

        # Try to access admin page again
        await page.goto(f"{self.server_url}/admin/")
        await page.wait_for_load_state("networkidle")

        # Should be redirected to login page
        current_url = page.url
        assert "login" in current_url.lower()
