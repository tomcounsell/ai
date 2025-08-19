"""
Pytest configuration for the public app tests.

This file contains fixtures and configuration for the public app tests,
including those needed for end-to-end browser testing with browser-use.
"""

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings

User = get_user_model()


# Add 'testserver' to ALLOWED_HOSTS for client tests
@pytest.fixture(autouse=True, scope="session")
def allowed_hosts_setup():
    """Configure ALLOWED_HOSTS to include 'testserver' for tests."""
    with override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"]):
        yield


@pytest.fixture
def auth_user():
    """Fixture to provide an authenticated user."""
    user = User.objects.create_user(
        username="testuser", email="testuser@example.com", password="password123"
    )
    return user


@pytest.fixture
def admin_user():
    """Fixture to provide an admin user."""
    admin = User.objects.create_user(
        username="admin",
        email="admin@example.com",
        password="adminpass123",
        is_staff=True,
        is_superuser=True,
    )
    return admin


# Add browser testing fixtures if available
try:
    import playwright.async_api
    import pytest_asyncio

    BROWSER_TESTING_AVAILABLE = True

    @pytest_asyncio.fixture
    async def async_browser():
        """Fixture to provide an async browser instance."""
        playwright_instance = await playwright.async_api.async_playwright().start()
        browser = await playwright_instance.chromium.launch(headless=True)
        yield browser
        await browser.close()
        await playwright_instance.stop()

    @pytest_asyncio.fixture
    async def async_context(async_browser):
        """Fixture to provide a browser context."""
        context = await async_browser.new_context(
            viewport={"width": 1280, "height": 720}
        )
        yield context
        await context.close()

    @pytest_asyncio.fixture
    async def async_page(async_context):
        """Fixture to provide a browser page."""
        page = await async_context.new_page()
        yield page
        await page.close()

except ImportError:
    BROWSER_TESTING_AVAILABLE = False
