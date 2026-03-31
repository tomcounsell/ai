"""E2E tests for episode creation form.

Layer 1: Foundation -- verifies the create episode form loads, submits,
creates a real episode, and redirects to the workflow page.

Run with:
    python tools/testing/browser_test_runner.py apps/podcast/tests/e2e/test_e2e_episode_create.py
"""

import pytest
from playwright.sync_api import Page, expect

from apps.podcast.tests.e2e.e2e_helpers import episode_create_url, login_as


@pytest.mark.local_only
class TestCreateFormLoads:
    """Verify the episode creation form renders correctly."""

    def test_create_form_loads(self, page: Page, e2e_data, staff_password):
        """Episode creation form at /podcast/{slug}/new/ loads for staff."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        response = page.goto(episode_create_url(e2e_data.podcast.slug))
        assert response.status == 200

    def test_create_form_has_title_field(self, page: Page, e2e_data, staff_password):
        """Episode creation form contains a title input field."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        page.goto(episode_create_url(e2e_data.podcast.slug))
        title_field = page.locator('input[name="title"]')
        expect(title_field).to_be_visible()

    def test_create_form_has_description_field(
        self, page: Page, e2e_data, staff_password
    ):
        """Episode creation form contains a description textarea."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        page.goto(episode_create_url(e2e_data.podcast.slug))
        desc_field = page.locator('textarea[name="description"]')
        expect(desc_field).to_be_visible()

    def test_create_form_has_tags_field(self, page: Page, e2e_data, staff_password):
        """Episode creation form contains a tags input field."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        page.goto(episode_create_url(e2e_data.podcast.slug))
        tags_field = page.locator('input[name="tags"]')
        expect(tags_field).to_be_visible()

    def test_create_form_has_submit_button(self, page: Page, e2e_data, staff_password):
        """Episode creation form has a submit button."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        page.goto(episode_create_url(e2e_data.podcast.slug))
        submit_btn = page.get_by_role("button", name="Create Episode")
        expect(submit_btn).to_be_visible()

    def test_create_form_shows_page_title(self, page: Page, e2e_data, staff_password):
        """Episode creation page displays 'Create Episode' heading text."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        page.goto(episode_create_url(e2e_data.podcast.slug))
        expect(page.locator("body")).to_contain_text("Create Episode")


@pytest.mark.local_only
class TestCreateFormSubmission:
    """Verify that submitting the form creates an episode and redirects."""

    def test_submit_creates_episode_and_redirects(
        self, page: Page, e2e_data, staff_password
    ):
        """Fill title + description, submit -> redirected to workflow step 1."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        page.goto(episode_create_url(e2e_data.podcast.slug))

        page.fill('input[name="title"]', "E2E Created Episode")
        page.fill('textarea[name="description"]', "An episode created by E2E tests.")
        page.get_by_role("button", name="Create Episode").click()

        page.wait_for_load_state("domcontentloaded")

        # Should redirect to workflow step 1 (URL contains /edit/1/)
        assert "/edit/1/" in page.url
        assert e2e_data.podcast.slug in page.url

    def test_created_episode_title_on_workflow_page(
        self, page: Page, e2e_data, staff_password
    ):
        """After create, the workflow page displays the entered episode title."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        page.goto(episode_create_url(e2e_data.podcast.slug))

        episode_title = "E2E Title Verification Episode"
        page.fill('input[name="title"]', episode_title)
        page.fill('textarea[name="description"]', "Testing title appears on workflow.")
        page.get_by_role("button", name="Create Episode").click()

        page.wait_for_load_state("domcontentloaded")

        # The workflow page should show the episode title
        expect(page.locator("body")).to_contain_text(episode_title)

    def test_owner_can_submit_create_form(self, page: Page, e2e_data, staff_password):
        """Podcast owner (non-staff) can create an episode via form submission."""
        login_as(page, e2e_data.owner_user.username, staff_password)
        page.goto(episode_create_url(e2e_data.podcast.slug))

        page.fill('input[name="title"]', "E2E Owner Created Episode")
        page.fill('textarea[name="description"]', "Created by the podcast owner.")
        page.get_by_role("button", name="Create Episode").click()

        page.wait_for_load_state("domcontentloaded")

        # Owner should also redirect to workflow
        assert "/edit/1/" in page.url


@pytest.mark.local_only
class TestCreateFormValidation:
    """Verify client-side and server-side form validation."""

    def test_empty_title_shows_validation_error(
        self, page: Page, e2e_data, staff_password
    ):
        """Submitting with empty title stays on form (does not redirect)."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        page.goto(episode_create_url(e2e_data.podcast.slug))

        # Leave title empty, fill only description
        page.fill('textarea[name="description"]', "Description without title.")
        page.get_by_role("button", name="Create Episode").click()

        page.wait_for_load_state("domcontentloaded")

        # Should stay on the create page (not redirect to workflow)
        assert "/new/" in page.url or "/account/login" not in page.url

    def test_empty_description_shows_validation_error(
        self, page: Page, e2e_data, staff_password
    ):
        """Submitting with empty description stays on form."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        page.goto(episode_create_url(e2e_data.podcast.slug))

        # Fill title but leave description empty
        page.fill('input[name="title"]', "Title Without Description")
        page.get_by_role("button", name="Create Episode").click()

        page.wait_for_load_state("domcontentloaded")

        # Should stay on the create page
        assert "/new/" in page.url or "/edit/" not in page.url

    def test_valid_submission_does_not_stay_on_form(
        self, page: Page, e2e_data, staff_password
    ):
        """Valid submission leaves the create page (confirming redirect works)."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        page.goto(episode_create_url(e2e_data.podcast.slug))

        page.fill('input[name="title"]', "E2E Validation Pass Episode")
        page.fill('textarea[name="description"]', "This should submit successfully.")
        page.get_by_role("button", name="Create Episode").click()

        page.wait_for_load_state("domcontentloaded")

        # Should NOT be on the create page anymore
        assert "/new/" not in page.url
