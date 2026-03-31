"""E2E tests for podcast authentication and access controls.

Layer 1: Foundation -- verifies login flows and route-level permissions
for the podcast workflow pages.

Run with:
    python tools/testing/browser_test_runner.py apps/podcast/tests/e2e/test_e2e_podcast_auth.py
"""

import pytest
from playwright.sync_api import Page, expect

from apps.podcast.tests.e2e.e2e_helpers import (
    BASE_URL,
    LOGIN_URL,
    episode_workflow_url,
    login_as,
)

# ---------------------------------------------------------------------------
# Login flow tests
# ---------------------------------------------------------------------------


@pytest.mark.local_only
class TestStaffLogin:
    """Verify that staff users can log in and reach authenticated pages."""

    def test_staff_can_login(self, page: Page, e2e_data, staff_password):
        """Staff user logs in and is redirected away from login page."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        assert "/account/login" not in page.url

    def test_staff_sees_authenticated_content(
        self, page: Page, e2e_data, staff_password
    ):
        """After login, staff user can reach the podcast list page."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        page.goto(f"{BASE_URL}/podcast/")
        expect(page.locator("body")).to_contain_text("Podcast")

    def test_login_page_loads(self, page: Page):
        """Login page itself loads and shows form elements."""
        page.goto(LOGIN_URL)
        expect(page.locator("#id_username")).to_be_visible()
        expect(page.locator("#id_password")).to_be_visible()
        expect(page.get_by_role("button", name="Login")).to_be_visible()


@pytest.mark.local_only
class TestOwnerLogin:
    """Verify that podcast owners can log in."""

    def test_owner_can_login(self, page: Page, e2e_data, staff_password):
        """Owner user logs in and is redirected away from login page."""
        login_as(page, e2e_data.owner_user.username, staff_password)
        assert "/account/login" not in page.url

    def test_owner_can_reach_podcast_detail(self, page: Page, e2e_data, staff_password):
        """Owner can navigate to their podcast detail page."""
        login_as(page, e2e_data.owner_user.username, staff_password)
        page.goto(f"{BASE_URL}/podcast/{e2e_data.podcast.slug}/")
        expect(page.locator("body")).to_contain_text(e2e_data.podcast.title)

    def test_owner_sees_new_episode_button(self, page: Page, e2e_data, staff_password):
        """Owner sees the New Episode button on their podcast detail page."""
        login_as(page, e2e_data.owner_user.username, staff_password)
        page.goto(f"{BASE_URL}/podcast/{e2e_data.podcast.slug}/")
        expect(page.locator("body")).to_contain_text("New Episode")


# ---------------------------------------------------------------------------
# Access control tests
# ---------------------------------------------------------------------------


@pytest.mark.local_only
class TestAccessControls:
    """Verify that unauthenticated and unauthorized users are blocked."""

    def test_anonymous_redirected_from_workflow(self, page: Page, e2e_data):
        """Anonymous GET to workflow URL redirects to login page."""
        workflow_url = episode_workflow_url(
            e2e_data.podcast.slug,
            e2e_data.draft_episode.slug,
            step=1,
        )
        page.goto(workflow_url)
        page.wait_for_load_state("domcontentloaded")
        assert "/account/login" in page.url

    def test_anonymous_redirected_from_create(self, page: Page, e2e_data):
        """Anonymous GET to episode create URL redirects to login page."""
        page.goto(f"{BASE_URL}/podcast/{e2e_data.podcast.slug}/new/")
        page.wait_for_load_state("domcontentloaded")
        assert "/account/login" in page.url

    def test_regular_user_denied_workflow_access(
        self, page: Page, e2e_data, staff_password
    ):
        """Regular (non-staff, non-owner) user gets 403 on workflow page."""
        login_as(page, e2e_data.regular_user.username, staff_password)
        workflow_url = episode_workflow_url(
            e2e_data.podcast.slug,
            e2e_data.draft_episode.slug,
            step=1,
        )
        response = page.goto(workflow_url)
        assert response.status == 403

    def test_staff_can_access_workflow(self, page: Page, e2e_data, staff_password):
        """Staff user gets 200 on workflow page."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        workflow_url = episode_workflow_url(
            e2e_data.podcast.slug,
            e2e_data.draft_episode.slug,
            step=1,
        )
        response = page.goto(workflow_url)
        assert response.status == 200

    def test_owner_can_access_create_form(self, page: Page, e2e_data, staff_password):
        """Podcast owner gets 200 on the new episode form."""
        login_as(page, e2e_data.owner_user.username, staff_password)
        response = page.goto(f"{BASE_URL}/podcast/{e2e_data.podcast.slug}/new/")
        assert response.status == 200

    def test_staff_can_access_create_form(self, page: Page, e2e_data, staff_password):
        """Staff user gets 200 on the new episode form."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        response = page.goto(f"{BASE_URL}/podcast/{e2e_data.podcast.slug}/new/")
        assert response.status == 200
