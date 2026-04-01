"""
Pytest configuration for the project.

This file configures pytest for running Django tests with proper
database setup and fixtures.
"""

import contextlib
import os

import django
import pytest
from django.conf import settings

# Optional Playwright import for browser tests
try:
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


def pytest_configure(config):
    """Configure Django for pytest if not already done."""
    # Check if Django is already configured
    if not settings.configured:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
        django.setup()

    # Set TESTING flag for error handling in utilities/logger.py
    settings.TESTING = True

    # Use plain StaticFilesStorage for tests so collectstatic is not required.
    # The default CompressedManifestStaticFilesStorage needs a manifest built
    # by collectstatic, which causes ValueError in tests.
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }

    # Reset Django's storage handler caches so the updated STORAGES setting
    # is picked up. The StorageHandler caches backends (cached_property) and
    # created storage instances (_storages dict).
    from django.core.files.storage import storages
    from django.utils.functional import empty

    storages._backends = None
    storages._storages = {}
    # Clear the cached_property so it re-reads from settings.STORAGES
    with contextlib.suppress(AttributeError):
        del storages.backends

    # Reset the lazy staticfiles_storage so it re-creates from the handler.
    from django.contrib.staticfiles.storage import staticfiles_storage

    staticfiles_storage._wrapped = empty

    # Register test markers
    config.addinivalue_line("markers", "unit: mark test as a unit test")
    config.addinivalue_line("markers", "integration: mark test as an integration test")
    config.addinivalue_line("markers", "e2e: mark test as an end-to-end test")
    config.addinivalue_line("markers", "visual: mark test as a visual test")
    config.addinivalue_line("markers", "api: mark test as an API test")
    config.addinivalue_line("markers", "model: mark test related to models")
    config.addinivalue_line("markers", "view: mark test related to views")
    config.addinivalue_line("markers", "form: mark test related to forms")
    config.addinivalue_line("markers", "workflow: mark test related to user workflows")
    config.addinivalue_line("markers", "component: mark test related to UI components")


@pytest.fixture(scope="session")
def django_db_setup(django_test_environment, django_db_blocker):
    """Create a fresh test database with all migrations applied.

    Overrides pytest-django's default django_db_setup to use Django's
    setup_databases / teardown_databases directly. This creates a
    disposable ``test_<dbname>`` database with the full migration chain
    applied, so tests always run against the current schema even when
    the local dev database has unapplied migrations.

    The test database is destroyed after the session via teardown_databases.
    Individual tests still get transaction isolation from pytest-django's
    ``@pytest.mark.django_db`` / ``TransactionTestCase`` machinery.
    """
    from django.test.utils import setup_databases, teardown_databases

    with django_db_blocker.unblock():
        db_cfg = setup_databases(
            verbosity=0,
            interactive=False,
            keepdb=False,
            serialized_aliases=set(),
        )

    yield

    with django_db_blocker.unblock():
        teardown_databases(db_cfg, verbosity=0)


# ---------------------------------------------------------------------------
# Playwright fixtures (available project-wide for browser tests)
# ---------------------------------------------------------------------------

if PLAYWRIGHT_AVAILABLE:

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
        _browser = launcher.launch(headless=headless, slow_mo=slow_mo)
        yield _browser
        _browser.close()

    @pytest.fixture
    def page(browser):
        """Create a fresh browser page for each test."""
        _page = browser.new_page()
        yield _page
        _page.close()
