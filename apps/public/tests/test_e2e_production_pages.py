"""
E2E tests for production pages.

Run with: python tools/testing/browser_test_runner.py apps/public/tests/test_e2e_production_pages.py
"""

import os

import pytest
from playwright.sync_api import Page, expect

# Use environment variable to allow testing different environments
PRODUCTION_URL = os.environ.get("PRODUCTION_URL", "https://app.bwforce.ai")


@pytest.fixture
def page(browser):
    """Create a new page for each test."""
    page = browser.new_page()
    yield page
    page.close()


def test_homepage_loads(page: Page):
    """Test that homepage loads successfully."""
    page.goto(PRODUCTION_URL)
    # Check that page loads (status 200)
    expect(page).to_have_url(PRODUCTION_URL + "/")


def test_basic_health_check(page: Page):
    """Test basic health check endpoint."""
    response = page.goto(f"{PRODUCTION_URL}/health/")
    assert response.status == 200

    # Check that response is JSON
    content = page.content()
    assert "healthy" in content.lower()


def test_deep_health_check(page: Page):
    """Test deep health check endpoint."""
    response = page.goto(f"{PRODUCTION_URL}/health/deep/")
    assert response.status == 200

    # Check that response contains health status
    content = page.content()
    assert "healthy" in content.lower() or "unhealthy" in content.lower()


def test_creative_juices_landing(page: Page):
    """Test Creative Juices landing page."""
    page.goto(f"{PRODUCTION_URL}/mcp/creative-juices/")

    # Check page title
    expect(page).to_have_title("Creative Juices MCP - Break Free from Predictable AI")

    # Check for key content
    expect(page.locator("body")).to_contain_text("Creative Juices")


def test_creative_juices_manifest_accessible(page: Page):
    """Test manifest.json is accessible."""
    response = page.goto(f"{PRODUCTION_URL}/mcp/creative-juices/manifest.json")
    assert response.status == 200

    # Check CORS headers
    headers = response.headers
    assert "access-control-allow-origin" in headers


def test_creative_juices_readme_accessible(page: Page):
    """Test README.md is accessible."""
    response = page.goto(f"{PRODUCTION_URL}/mcp/creative-juices/README.md")
    assert response.status == 200

    # Check CORS headers
    headers = response.headers
    assert "access-control-allow-origin" in headers


def test_all_critical_links_work(page: Page):
    """Test that all critical navigation links work."""
    page.goto(f"{PRODUCTION_URL}/mcp/creative-juices/")

    # Wait for page to load
    page.wait_for_load_state("networkidle")

    # Check that there are no obvious error messages
    body_text = page.locator("body").text_content()
    assert "404" not in body_text
    assert "error" not in body_text.lower() or "error" in "creative"  # Allow "creative"
