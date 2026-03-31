"""Pytest configuration and fixtures for podcast E2E browser tests.

Run with:
    python tools/testing/browser_test_runner.py apps/podcast/tests/e2e/

This conftest provides:
- Playwright browser and page fixtures
- Session-scoped E2E test data via Django ORM
- Automatic cleanup of E2E data after test session
"""

import os

import django
import pytest

# Ensure Django is configured before importing models
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
django.setup()

from playwright.sync_api import sync_playwright  # noqa: E402

from apps.podcast.tests.e2e.e2e_fixtures import (  # noqa: E402
    E2E_PASSWORD,
    cleanup_e2e_data,
    setup_e2e_data,
)
from apps.podcast.tests.e2e.e2e_helpers import is_production  # noqa: E402

# ---------------------------------------------------------------------------
# Marks
# ---------------------------------------------------------------------------


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "local_only: skip when running against production"
    )


def pytest_collection_modifyitems(config, items):
    """Auto-skip local_only tests when PRODUCTION_URL is set."""
    if not is_production():
        return
    skip_marker = pytest.mark.skip(reason="local_only: skipped in production mode")
    for item in items:
        if "local_only" in item.keywords:
            item.add_marker(skip_marker)


# ---------------------------------------------------------------------------
# Playwright fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def browser_type():
    """Return the browser type name from environment (default: chromium)."""
    return os.environ.get("TEST_BROWSER", "chromium")


@pytest.fixture(scope="session")
def playwright_instance():
    """Start and stop Playwright for the test session."""
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright_instance, browser_type):
    """Launch a browser instance for the test session."""
    headless = os.environ.get("TEST_HEADLESS", "1") == "1"
    slow_mo = int(os.environ.get("TEST_SLOW_MO", "0"))

    launcher = getattr(playwright_instance, browser_type)
    browser = launcher.launch(headless=headless, slow_mo=slow_mo)
    yield browser
    browser.close()


@pytest.fixture
def page(browser):
    """Create a fresh browser page for each test."""
    page = browser.new_page()
    yield page
    page.close()


# ---------------------------------------------------------------------------
# E2E data fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def e2e_data(django_db_blocker):
    """Create all E2E fixture data once per test session.

    Skipped entirely when running in production mode (PRODUCTION_URL set).

    Uses ``django_db_blocker.unblock()`` because session-scoped fixtures
    cannot use the ``django_db`` mark -- only function-scoped fixtures can.
    """
    if is_production():
        yield None
        return

    with django_db_blocker.unblock():
        data = setup_e2e_data()
    yield data
    with django_db_blocker.unblock():
        cleanup_e2e_data()


@pytest.fixture
def staff_password():
    """Return the shared E2E password for convenience."""
    return E2E_PASSWORD
