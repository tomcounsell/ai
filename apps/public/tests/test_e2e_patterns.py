"""
End-to-end testing patterns using browser-use.

This module provides examples and utilities for creating end-to-end tests
with browser-use that can be run in local development environments.
These patterns demonstrate how to:
- Set up browser-use for testing Django applications
- Create reusable test patterns for common workflows
- Test HTMX interactions and dynamic UI updates
- Verify CSS styles and responsive layouts
- Test form submissions and validation
"""

import os
import sys
from typing import TYPE_CHECKING, Tuple

import pytest
from django.contrib.auth import get_user_model

# Import browser-use components
try:
    # Import the Agent class from browser-use
    from browser_use import Agent

    # For backwards compatibility
    BrowserAgent = Agent

    # Import Playwright components
    import playwright.async_api
    from playwright.async_api import Browser, BrowserContext

    # Define Page for type hints
    if TYPE_CHECKING:
        from playwright.async_api import Page
    else:
        Page = playwright.async_api.Page

    BROWSER_USE_AVAILABLE = True

except ImportError as e:
    # Debug import error
    print(f"Warning: browser-use import error: {e}")
    BROWSER_USE_AVAILABLE = False

    # Create dummy classes for type hints when imports fail
    class Agent:
        async def run(self, **kwargs):
            return "Agent not available"

    class Page:
        pass

    class Browser:
        pass

    class BrowserContext:
        pass

    # For backwards compatibility
    BrowserAgent = Agent


# Check for pytest-asyncio
try:
    import pytest_asyncio

    HAS_PYTEST_ASYNCIO = True
except ImportError:
    HAS_PYTEST_ASYNCIO = False

# Output status of required packages
if not BROWSER_USE_AVAILABLE or not HAS_PYTEST_ASYNCIO:
    missing_packages = []
    if not BROWSER_USE_AVAILABLE:
        missing_packages.append("browser-use")
    if not HAS_PYTEST_ASYNCIO:
        missing_packages.append("pytest-asyncio")

    print(
        f"Warning: E2E Browser tests may be skipped. Missing packages: {', '.join(missing_packages)}"
    )
    print("Install with: uv add --dev browser-use playwright pytest-asyncio")

# Check if Playwright is installed separately
try:
    import playwright

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    print("Warning: Playwright not found. Install with: uv add --dev playwright")

# Mark browser tests to skip if required packages are not installed
browser_test = pytest.mark.skipif(
    not BROWSER_USE_AVAILABLE or not HAS_PYTEST_ASYNCIO or not HAS_PLAYWRIGHT,
    reason="Required packages not installed. Run `uv add --dev browser-use playwright pytest-asyncio` to install.",
)

# Define asyncio mark conditionally to avoid warnings
if HAS_PYTEST_ASYNCIO:
    asyncio_mark = pytest.mark.asyncio
else:
    # Create a no-op marker if pytest-asyncio is not available
    asyncio_mark = lambda f: f

# Settings for local development testing
LOCAL_TEST_SERVER_URL = "http://localhost:8000"

User = get_user_model()


try:
    from apps.public.tests.e2e_test_config import (
        BROWSER_CONFIG,
        SCREENSHOTS_BASE_DIR,
        SERVER_URL,
        VIEWPORTS,
        LiveServerMixin,
        ensure_directories,
        is_server_running,
    )
except ImportError:
    # Default values if the config module is not available
    SERVER_URL = "http://localhost:8000"
    BROWSER_CONFIG = {
        "headless": True,
        "slow_mo": 100,
        "browser_type": "chromium",
        "default_timeout": 5000,
        "navigation_timeout": 10000,
    }
    VIEWPORTS = {
        "desktop": {"width": 1280, "height": 800},
    }
    SCREENSHOTS_BASE_DIR = "test_screenshots"

    def ensure_directories():
        """Create screenshot directory if it doesn't exist."""
        import os

        os.makedirs(SCREENSHOTS_BASE_DIR, exist_ok=True)

    def is_server_running(host="localhost", port=8000):
        """Check if the Django server is running."""
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0

    class LiveServerMixin:
        """Dummy LiveServerMixin for when the real one is not available."""

        @classmethod
        def get_server_url(cls, live_server=None) -> str:
            """Get the server URL to use for tests."""
            if live_server:
                return live_server.url
            return SERVER_URL

        @classmethod
        def setup_class(cls):
            """Set up the test class."""
            # Create necessary directories
            ensure_directories()

            # Check if a live server is running
            if not is_server_running():
                import pytest

                pytest.skip(
                    "Test server not running. Start with 'python manage.py runserver' "
                    "or use pytest-django live_server fixture."
                )


class EndToEndTestConfig:
    """Configuration class for end-to-end tests."""

    # Test server URL to use
    server_url: str = SERVER_URL

    # Playwright browser options
    headless: bool = BROWSER_CONFIG["headless"]
    slow_mo: int = BROWSER_CONFIG["slow_mo"]
    browser_type: str = BROWSER_CONFIG["browser_type"]

    # Viewport size for responsive testing
    viewport_width: int = VIEWPORTS["desktop"]["width"]
    viewport_height: int = VIEWPORTS["desktop"]["height"]

    # Wait times in milliseconds
    default_timeout: int = BROWSER_CONFIG["default_timeout"]
    navigation_timeout: int = BROWSER_CONFIG["navigation_timeout"]

    # Screenshots directory relative to project root
    screenshots_dir: str = SCREENSHOTS_BASE_DIR


@pytest_asyncio.fixture
async def browser_context(request):
    """Fixture to provide a browser context for tests."""
    import playwright.async_api

    config = EndToEndTestConfig()

    # Setup screenshots directory if needed
    if not os.path.exists(config.screenshots_dir):
        os.makedirs(config.screenshots_dir)

    # Create browser instance
    playwright_instance = await playwright.async_api.async_playwright().start()
    browser_instance = await getattr(playwright_instance, config.browser_type).launch(
        headless=config.headless, slow_mo=config.slow_mo
    )

    # Create context with viewport settings
    context = await browser_instance.new_context(
        viewport={"width": config.viewport_width, "height": config.viewport_height}
    )

    # Set default timeout values
    context.set_default_timeout(config.default_timeout)
    context.set_default_navigation_timeout(config.navigation_timeout)

    # Return context for use in tests
    yield context

    # Cleanup
    try:
        await context.close()
        await browser_instance.close()
        await playwright_instance.stop()
    except Exception as e:
        print(f"Error during browser context cleanup: {e}")


@pytest_asyncio.fixture
async def browser_page(browser_context):
    """Fixture to provide a browser page for tests."""
    page = await browser_context.new_page()
    yield page
    try:
        await page.close()
    except Exception as e:
        print(f"Error closing browser page: {e}")


class E2ETestBase(LiveServerMixin):
    """Base class for end-to-end tests with browser-use and Playwright."""

    config = EndToEndTestConfig()

    async def login_user(
        self, page: Page, username: str, password: str, live_server=None
    ) -> bool:
        """
        Log in a user through the browser.

        Args:
            page: Playwright page object
            username: Username for login
            password: Password for login
            live_server: The pytest-django live_server fixture, if available

        Returns:
            bool: True if login successful, False otherwise
        """
        try:
            # Get the server URL (works with live_server fixture or local server)
            server_url = self.get_server_url(live_server)

            # Navigate to login page
            await page.goto(f"{server_url}/accounts/login/")

            # Fill login form
            await page.fill('input[name="username"]', username)
            await page.fill('input[name="password"]', password)

            # Submit form
            await page.click('button[type="submit"]')

            # Wait for navigation to complete
            await page.wait_for_load_state("networkidle")

            # Check if login was successful (could check for elements visible after login)
            if await page.locator(".account-menu").count() > 0:
                return True

            # Alternative check for successful login
            url = page.url
            if "/accounts/login/" not in url:
                return True

            return False
        except Exception as e:
            print(f"Login failed: {e}")
            return False

    async def create_user_and_login(
        self, page: Page, live_server=None
    ) -> tuple[User, bool]:
        """
        Create a test user and log them in.

        Args:
            page: Playwright page object
            live_server: The pytest-django live_server fixture, if available

        Returns:
            Tuple[User, bool]: Created user object and login success boolean
        """
        # Create a test user
        username = f"testuser_{os.urandom(4).hex()}"
        password = "testpassword123"
        user = User.objects.create_user(
            username=username, email=f"{username}@example.com", password=password
        )

        # Login with this user
        login_success = await self.login_user(page, username, password, live_server)

        return user, login_success

    async def take_screenshot(self, page: Page, filename: str) -> str:
        """
        Take a screenshot during test execution.

        Args:
            page: Playwright page object
            filename: Screenshot filename

        Returns:
            str: Path to the saved screenshot
        """
        screenshot_path = os.path.join(self.config.screenshots_dir, filename)
        await page.screenshot(path=screenshot_path)
        return screenshot_path

    async def assert_element_visible(
        self, page: Page, selector: str, timeout: int = 5000
    ) -> bool:
        """
        Assert that an element is visible on the page.

        Args:
            page: Playwright page object
            selector: CSS selector for the element
            timeout: Timeout in milliseconds

        Returns:
            bool: True if element is visible
        """
        element = page.locator(selector)
        await element.wait_for(state="visible", timeout=timeout)
        return True

    async def assert_element_contains_text(
        self, page: Page, selector: str, text: str
    ) -> bool:
        """
        Assert that an element contains specific text.

        Args:
            page: Playwright page object
            selector: CSS selector for the element
            text: Text to check for

        Returns:
            bool: True if element contains the text
        """
        element = page.locator(selector)
        element_text = await element.inner_text()
        assert (
            text in element_text
        ), f"Element {selector} does not contain text '{text}'"
        return True

    async def wait_for_htmx_request(self, page: Page, timeout: int = 5000) -> None:
        """
        Wait for an HTMX request to complete.

        Args:
            page: Playwright page object
            timeout: Timeout in milliseconds
        """
        # Wait for the htmx request to start
        try:
            await page.wait_for_selector("[hx-request]", timeout=timeout)
            # Wait for the request to complete (htmx removes the attribute)
            await page.wait_for_function(
                'document.querySelectorAll("[hx-request]").length === 0',
                timeout=timeout,
            )
        except:
            # It's possible the request completed very quickly or wasn't marked
            pass


@browser_test
@asyncio_mark
class TestLoginForm(E2ETestBase):
    """Test the login form functionality."""

    async def test_login_form_submission(self, browser_page, live_server=None):
        """Test that the login form can be submitted."""
        page = browser_page
        username = "testuser"
        password = "testpassword123"

        # Create test user
        User.objects.create_user(username=username, password=password)

        # Get the server URL to use (works with live_server fixture or local server)
        server_url = self.get_server_url(live_server)

        # Navigate to login page
        await page.goto(f"{server_url}/accounts/login/")

        # Check login form is visible
        assert await self.assert_element_visible(page, "form")

        # Fill in login form
        await page.fill('input[name="username"]', username)
        await page.fill('input[name="password"]', password)

        # Take screenshot before submission
        await self.take_screenshot(page, "login_form_filled.png")

        # Submit form
        await page.click('button[type="submit"]')

        # Wait for navigation to complete
        await page.wait_for_load_state("networkidle")

        # Take screenshot after login
        await self.take_screenshot(page, "after_login.png")

        # Check login was successful by looking for account menu
        assert await self.assert_element_visible(page, ".account-menu")


@browser_test
@asyncio_mark
class TestHTMXInteractions(E2ETestBase):
    """Test HTMX interactions."""

    async def test_htmx_load_component(self, browser_page, live_server=None):
        """Test loading a component via HTMX."""
        page = browser_page

        # Login with test user
        user, login_success = await self.create_user_and_login(page, live_server)
        assert login_success, "Login failed"

        # Get the server URL to use
        server_url = self.get_server_url(live_server)

        # Navigate to a page with HTMX components
        await page.goto(f"{server_url}/")

        # Find an element with hx-get attribute that loads content
        htmx_trigger = page.locator("[hx-get]").first

        # Get the target of the HTMX request
        target_id = await htmx_trigger.get_attribute("hx-target")

        # Take screenshot before clicking
        await self.take_screenshot(page, "before_htmx.png")

        # Click the trigger
        await htmx_trigger.click()

        # Wait for the HTMX request to complete
        await self.wait_for_htmx_request(page)

        # Take screenshot after HTMX request
        await self.take_screenshot(page, "after_htmx.png")

        # Assert the target was updated
        target = page.locator(target_id)
        assert await target.count() > 0, f"Target element {target_id} not found"

        # Additional verification could check specific content in the target


@browser_test
@asyncio_mark
class TestResponsiveLayout(E2ETestBase):
    """Test responsive layout at different screen sizes."""

    @pytest_asyncio.fixture
    async def custom_browser_context(self):
        """Custom fixture for browser context to allow viewport changes."""
        import playwright.async_api

        config = EndToEndTestConfig()

        # Create browser instance
        playwright_instance = await playwright.async_api.async_playwright().start()
        browser_instance = await getattr(
            playwright_instance, config.browser_type
        ).launch(headless=config.headless, slow_mo=config.slow_mo)

        # Return the browser directly (not a context)
        yield browser_instance

        # Cleanup
        try:
            await browser_instance.close()
            await playwright_instance.stop()
        except Exception as e:
            print(f"Error during custom browser cleanup: {e}")

    async def test_responsive_navbar(self, custom_browser_context, live_server=None):
        """Test navbar responsiveness at different screen sizes."""
        # Get the server URL to use
        server_url = self.get_server_url(live_server)

        # Define viewport sizes to test
        viewports = [
            {"width": 1280, "height": 800},  # Desktop
            {"width": 768, "height": 1024},  # Tablet
            {"width": 375, "height": 667},  # Mobile
        ]

        for i, viewport in enumerate(viewports):
            # Create a new context with this viewport
            context = await custom_browser_context.new_context(viewport=viewport)

            try:
                # Create a new page in this context
                page = await context.new_page()

                # Navigate to home page
                await page.goto(f"{server_url}/")

                # Take screenshot
                device_type = (
                    "desktop"
                    if viewport["width"] >= 1024
                    else "tablet" if viewport["width"] >= 768 else "mobile"
                )
                await self.take_screenshot(page, f"navbar_{device_type}.png")

                # Additional specific checks based on viewport
                if viewport["width"] < 768:  # Mobile
                    # Check if mobile menu button is visible
                    assert await self.assert_element_visible(
                        page, ".mobile-menu-button"
                    )
                else:  # Desktop/Tablet
                    # Check if navbar links are visible
                    assert await self.assert_element_visible(page, ".navbar-links")
            finally:
                # Always close the context after use
                await context.close()


@browser_test
@asyncio_mark
class TestBrowserAgentAutomation(E2ETestBase):
    """Test using BrowserAgent from browser-use for more complex flows."""

    async def test_complete_user_flow(self, live_server=None):
        """Test a complete user flow using BrowserAgent."""
        # Skip if browser-use is not available
        if not BROWSER_USE_AVAILABLE:
            pytest.skip("browser-use not installed")

        # Get the server URL to use
        server_url = self.get_server_url(live_server)

        # Create a test user
        username = f"testuser_{os.urandom(4).hex()}"
        password = "testpassword123"
        User.objects.create_user(
            username=username, email=f"{username}@example.com", password=password
        )

        # Set up tasks for the agent
        tasks = [
            f"Go to {server_url}/accounts/login/",
            f"Fill in the username field with {username}",
            f"Fill in the password field with {password}",
            "Click the Submit or Login button",
            "Wait for page to load",
            "Navigate to your team page",
            "Verify the team information is displayed",
            "Look for a Logout link or button in the navigation or account menu and click it",
        ]

        try:
            # Create an agent to perform these tasks
            agent = Agent()

            # Configure browser options
            browser_options = {
                "browser_type": self.config.browser_type,
                "headless": self.config.headless,
                "slow_mo": self.config.slow_mo,
                "screenshot_dir": self.config.screenshots_dir,
            }

            # Run the agent with the tasks
            result = await agent.run(tasks=tasks, **browser_options)

            # Verify the result
            assert (
                "success" in result.lower() or "completed" in result.lower()
            ), f"Agent failed to complete user flow: {result}"

        except Exception as e:
            # Take screenshot of failure if possible
            import traceback

            traceback.print_exc()
            pytest.fail(f"BrowserAgent test failed with error: {str(e)}")


def main():
    """Run the example test directly when the module is executed."""
    if not BROWSER_USE_AVAILABLE:
        print("browser-use not installed. Install with:")
        print("uv add --dev browser-use playwright pytest-asyncio")
        sys.exit(1)

    # Check if Django server is running
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(("localhost", 8000))
    if result != 0:
        print("Django server not running. Start with:")
        print("python manage.py runserver")
        sys.exit(1)

    # Run the test using pytest
    import pytest

    pytest.main(["-xvs", __file__])


if __name__ == "__main__":
    main()
