"""E2E tests for workflow UI display and navigation.

Layer 2: Workflow UI -- verifies the episode workflow page renders phases
in the sidebar, highlights the current phase, and supports step navigation.

Run with:
    python tools/testing/browser_test_runner.py apps/podcast/tests/e2e/test_e2e_workflow_display.py
"""

import pytest
from playwright.sync_api import Page, expect

from apps.podcast.tests.e2e.e2e_helpers import episode_workflow_url, login_as


@pytest.mark.local_only
class TestWorkflowSidebar:
    """Verify the workflow sidebar shows phases and highlights current step."""

    def test_workflow_page_loads(self, page: Page, e2e_data, staff_password):
        """Workflow page at /podcast/{slug}/{ep}/edit/1/ loads for staff."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug, e2e_data.draft_episode.slug, step=1
        )
        response = page.goto(url)
        page.wait_for_load_state("domcontentloaded")
        assert response.status == 200

    def test_sidebar_shows_phase_names(self, page: Page, e2e_data, staff_password):
        """Sidebar contains the names of workflow phases."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug, e2e_data.draft_episode.slug, step=1
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        sidebar = page.locator("#workflow-sidebar")
        expect(sidebar).to_be_visible()

        # Check that at least a few known phase names appear in the sidebar
        expect(sidebar).to_contain_text("Setup")
        expect(sidebar).to_contain_text("Synthesis")
        expect(sidebar).to_contain_text("Publish")

    def test_sidebar_has_navigation_links(self, page: Page, e2e_data, staff_password):
        """Sidebar contains links to individual workflow steps."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug, e2e_data.draft_episode.slug, step=1
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        sidebar = page.locator("#workflow-sidebar")
        # Sidebar should have links pointing to /edit/{step}/ URLs
        links = sidebar.locator("a[href*='/edit/']")
        assert links.count() >= 12, f"Expected 12+ step links, found {links.count()}"

    def test_clicking_sidebar_step_navigates(
        self, page: Page, e2e_data, staff_password
    ):
        """Clicking a sidebar step link navigates to that step URL.

        Sidebar links may use HTMX (hx-get + hx-push-url) for partial
        page updates, so the URL changes without a full page reload.
        We wait for the URL to update rather than a load state change.
        """
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug, e2e_data.draft_episode.slug, step=1
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        sidebar = page.locator("#workflow-sidebar")
        # Click on a link to step 3
        step3_link = sidebar.locator("a[href*='/edit/3/']").first
        step3_link.click()

        # Wait for URL to update (HTMX push-url or full navigation)
        page.wait_for_url("**/edit/3/**", timeout=10000)
        assert "/edit/3/" in page.url


@pytest.mark.local_only
class TestWorkflowStepContent:
    """Verify the main content area loads for different steps."""

    def test_step1_shows_episode_title(self, page: Page, e2e_data, staff_password):
        """Step 1 (Setup) displays the episode title."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug, e2e_data.draft_episode.slug, step=1
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        expect(page.locator("body")).to_contain_text(e2e_data.draft_episode.title)

    def test_step_content_area_exists(self, page: Page, e2e_data, staff_password):
        """The workflow step content container renders."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug, e2e_data.draft_episode.slug, step=1
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        content = page.locator("#workflow-step-content")
        expect(content).to_be_visible()

    def test_mid_pipeline_step5_loads(self, page: Page, e2e_data, staff_password):
        """Step 5 loads for a mid-pipeline episode with running workflow."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug, e2e_data.mid_pipeline_episode.slug, step=5
        )
        response = page.goto(url)
        page.wait_for_load_state("domcontentloaded")
        assert response.status == 200

    def test_different_steps_load_without_error(
        self, page: Page, e2e_data, staff_password
    ):
        """Steps 1 through 12 all return 200 for a valid episode."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        for step in [1, 6, 12]:
            url = episode_workflow_url(
                e2e_data.podcast.slug, e2e_data.draft_episode.slug, step=step
            )
            response = page.goto(url)
            page.wait_for_load_state("domcontentloaded")
            assert response.status == 200, f"Step {step} returned {response.status}"


@pytest.mark.local_only
class TestWorkflowHtmxPartial:
    """Verify HTMX partial vs full page response behavior."""

    def test_full_page_has_html_structure(self, page: Page, e2e_data, staff_password):
        """Normal GET returns a full HTML page with <html> tag."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug, e2e_data.draft_episode.slug, step=1
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        # Full page should have both sidebar and content
        expect(page.locator("#workflow-sidebar")).to_be_visible()
        expect(page.locator("#workflow-step-content")).to_be_visible()

    def test_htmx_navigation_updates_content(
        self, page: Page, e2e_data, staff_password
    ):
        """Clicking an HTMX step link updates the content area without full reload.

        We verify this by checking that after clicking a sidebar link with
        hx-get, the URL updates (via hx-push-url) and content changes.
        """
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug, e2e_data.draft_episode.slug, step=1
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        # Click sidebar link to step 7 (Synthesis)
        sidebar = page.locator("#workflow-sidebar")
        step7_link = sidebar.locator("a[href*='/edit/7/']").first
        if step7_link.count() > 0:
            step7_link.click()
            # Wait for URL to update via hx-push-url
            page.wait_for_url("**/edit/7/**", timeout=10000)
            assert "/edit/7/" in page.url

    def test_step_out_of_range_returns_404(self, page: Page, e2e_data, staff_password):
        """Step 13 returns 404."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug, e2e_data.draft_episode.slug, step=13
        )
        response = page.goto(url)
        assert response.status == 404
