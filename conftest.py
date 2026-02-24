"""
Pytest configuration for the project.

This file configures pytest for running Django tests with proper
database setup and fixtures.
"""

import contextlib
import os
import warnings

import django
import pytest
from django.conf import settings

# Optional Selenium import (skip if not installed)
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.edge.options import Options as EdgeOptions
    from selenium.webdriver.firefox.options import Options as FirefoxOptions

    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False
    warnings.warn(
        "Selenium not installed. Browser tests will be skipped.",
        stacklevel=2,
    )


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


if SELENIUM_AVAILABLE:

    @pytest.fixture
    def driver(request):
        """
        Provide a Selenium WebDriver instance for browser testing.

        By default, this uses Chrome in headless mode, but you can override with:
        - TEST_BROWSER environment variable (chrome/firefox/edge)
        - TEST_HEADLESS environment variable (0/1)
        - TEST_SLOW_MO environment variable (milliseconds to slow down actions)
        """
        # Determine browser type from environment or default to Chrome
        browser_name = os.environ.get("TEST_BROWSER", "chromium").lower()

        # Determine headless mode from environment
        headless = os.environ.get("TEST_HEADLESS", "1") == "1"

        # Get slow motion value for debugging
        slow_mo = int(os.environ.get("TEST_SLOW_MO", "0"))

        if browser_name == "firefox":
            options = FirefoxOptions()
            if headless:
                options.add_argument("--headless")
            driver = webdriver.Firefox(options=options)
        elif browser_name == "edge":
            options = EdgeOptions()
            if headless:
                options.add_argument("--headless")
            driver = webdriver.Edge(options=options)
        else:  # Default to Chrome
            options = ChromeOptions()
            if headless:
                options.add_argument("--headless")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1920,1080")
            driver = webdriver.Chrome(options=options)

        # Set implicit wait time
        driver.implicitly_wait(10)

        # If slow motion is enabled, add delays between actions
        if slow_mo > 0:
            # This is a placeholder - Selenium doesn't have a direct slow_mo option
            # For real implementation, you'd need to add delays in your test code
            pass

        # Yield the driver for the test
        yield driver

        # Quit the driver after the test is complete
        driver.quit()
