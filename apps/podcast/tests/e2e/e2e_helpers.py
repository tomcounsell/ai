"""Shared navigation and assertion helpers for podcast E2E tests.

These helpers abstract common operations (login, navigation, URL construction)
so individual test files stay focused on what they verify.
"""

import os

BASE_URL = os.environ.get("PRODUCTION_URL", "http://localhost:8000")
LOGIN_URL = f"{BASE_URL}/account/login"


def is_production() -> bool:
    """Return True if PRODUCTION_URL is set (tests target a live environment)."""
    return bool(os.environ.get("PRODUCTION_URL"))


def login_as(page, username: str, password: str) -> None:
    """Navigate to login page, fill credentials, submit, and wait for redirect.

    Args:
        page: Playwright Page object.
        username: Login username / email.
        password: Login password.
    """
    page.goto(LOGIN_URL)
    page.wait_for_load_state("domcontentloaded")

    page.fill("#id_username", username)
    page.fill("#id_password", password)
    page.get_by_role("button", name="Login").click()

    # Wait for navigation away from login page
    page.wait_for_load_state("domcontentloaded")


def podcast_list_url() -> str:
    """Return the podcast list URL."""
    return f"{BASE_URL}/podcast/"


def podcast_detail_url(podcast_slug: str) -> str:
    """Return the podcast detail URL."""
    return f"{BASE_URL}/podcast/{podcast_slug}/"


def episode_create_url(podcast_slug: str) -> str:
    """Return the episode creation form URL."""
    return f"{BASE_URL}/podcast/{podcast_slug}/new/"


def episode_workflow_url(podcast_slug: str, episode_slug: str, step: int = 1) -> str:
    """Return the episode workflow URL for a given step."""
    return f"{BASE_URL}/podcast/{podcast_slug}/{episode_slug}/edit/{step}/"


def episode_detail_url(podcast_slug: str, episode_slug: str) -> str:
    """Return the episode detail page URL."""
    return f"{BASE_URL}/podcast/{podcast_slug}/{episode_slug}/"
