"""
Configuration for end-to-end browser tests.

This module provides configuration and utilities for running
browser-based end-to-end tests either with a local development
server or with Django's LiveServerTestCase.
"""

import os
import socket
import time
from urllib.parse import urlparse

# CONSTANTS
SERVER_URL = os.environ.get("TEST_SERVER_URL", "http://localhost:8000")
SCREENSHOTS_BASE_DIR = "test_screenshots"

# Browser configuration
BROWSER_CONFIG = {
    "headless": os.environ.get("TEST_HEADLESS", "1") == "1",
    "slow_mo": int(os.environ.get("TEST_SLOW_MO", "50")),
    "browser_type": os.environ.get("TEST_BROWSER", "chromium").lower(),
    "default_timeout": 5000,
    "navigation_timeout": 10000,
}

# Viewport sizes
VIEWPORTS = {
    "desktop": {"width": 1280, "height": 800},
    "tablet": {"width": 768, "height": 1024},
    "mobile": {"width": 375, "height": 667},
}


def ensure_directories():
    """Create screenshot directory if it doesn't exist."""
    os.makedirs(SCREENSHOTS_BASE_DIR, exist_ok=True)


def is_server_running(host: str = "localhost", port: int = 8000) -> bool:
    """
    Check if the Django server is running at the specified host and port.

    Args:
        host: Hostname to check
        port: Port to check

    Returns:
        bool: True if server is running, False otherwise
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex((host, port))
    sock.close()
    return result == 0


def get_server_url(live_server=None) -> str:
    """
    Get the server URL to use for tests, either from the live_server fixture or environment.

    Args:
        live_server: The pytest-django live_server fixture, if available

    Returns:
        str: The server URL to use for tests
    """
    # If live_server fixture is provided, use its URL
    if live_server:
        return live_server.url

    # Otherwise, use environment variable or default
    return SERVER_URL


def wait_for_server(url: str, timeout: int = 30, interval: float = 0.5) -> bool:
    """
    Wait for the server to become available.

    Args:
        url: The URL to check
        timeout: Maximum time to wait in seconds
        interval: Time between checks in seconds

    Returns:
        bool: True if server became available within timeout, False otherwise
    """
    parsed_url = urlparse(url)
    host = parsed_url.hostname or "localhost"
    port = parsed_url.port or (443 if parsed_url.scheme == "https" else 80)

    start_time = time.time()
    while time.time() - start_time < timeout:
        if is_server_running(host, port):
            return True
        time.sleep(interval)

    return False


class LiveServerMixin:
    """
    Mixin to provide LiveServerTestCase functionality for E2E tests.

    This mixin adapts the E2E test classes to work with either:
    1. A running local development server
    2. The pytest-django live_server fixture
    3. Django's LiveServerTestCase

    Usage:
        class MyE2ETest(LiveServerMixin, E2ETestBase):
            async def test_something(self, live_server=None):
                server_url = self.get_server_url(live_server)
                # Use server_url for browser testing
    """

    @classmethod
    def get_server_url(cls, live_server=None) -> str:
        """
        Get the server URL to use for tests.

        Args:
            live_server: The pytest-django live_server fixture, if available

        Returns:
            str: The server URL to use for tests
        """
        return get_server_url(live_server)

    @classmethod
    def setup_class(cls):
        """Set up the test class."""
        # Create necessary directories
        ensure_directories()

        # Check if a live server is running (for local development testing)
        if not is_server_running():
            # This check is only relevant when not using live_server fixture
            # When using live_server, the server will be started by pytest-django
            import pytest

            pytest.skip(
                "Test server not running. Start with 'python manage.py runserver' "
                "or use pytest-django live_server fixture."
            )
