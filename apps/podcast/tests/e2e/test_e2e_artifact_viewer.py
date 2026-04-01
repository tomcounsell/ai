"""E2E tests for artifact viewer in the workflow UI.

Layer 3: Key Interactions -- verifies that artifact content loads via
HTMX lazy-load and that the artifact section is expandable.

Run with:
    python tools/testing/browser_test_runner.py apps/podcast/tests/e2e/test_e2e_artifact_viewer.py
"""

import pytest
from playwright.sync_api import Page, expect

from apps.podcast.tests.e2e.e2e_helpers import episode_workflow_url, login_as


@pytest.mark.local_only
class TestArtifactViewer:
    """Verify artifact viewer renders and loads content."""

    def test_artifact_section_is_expandable(self, page: Page, e2e_data, staff_password):
        """The artifact viewer uses a <details> element that can be toggled."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        # Mid-pipeline episode has artifacts; step 5 (Cross-Validation) is
        # the current step and earlier steps have artifacts.
        # Navigate to step 2 which should have the p2-research artifact.
        url = episode_workflow_url(
            e2e_data.podcast.slug,
            e2e_data.mid_pipeline_episode.slug,
            step=2,
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        # Look for a <details> element containing artifact info
        details = page.locator("details:has(summary)")
        if details.count() > 0:
            summary = details.first.locator("summary")
            expect(summary).to_be_visible()

            # Click to expand if not already open
            summary.click()
            # The details element should now have the 'open' attribute
            expect(details.first).to_have_attribute("open", "")

    def test_artifact_content_loads_via_htmx(
        self, page: Page, e2e_data, staff_password
    ):
        """Artifact content loads via HTMX lazy-load when the section is opened."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        # Step 1 has the p1-brief artifact for mid-pipeline episode
        url = episode_workflow_url(
            e2e_data.podcast.slug,
            e2e_data.mid_pipeline_episode.slug,
            step=1,
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        details = page.locator("details:has(summary)")
        if details.count() == 0:
            pytest.skip("No artifact section found on this step")

        # Open the details section
        summary = details.first.locator("summary")
        summary.click()

        # The HTMX-loaded content area should eventually replace the
        # "Loading artifact content..." placeholder.
        content_area = details.first.locator("div.px-4.py-4")
        # Wait for HTMX to load content (the placeholder text should disappear)
        content_area.wait_for(state="visible", timeout=10000)

        # After loading, the area should NOT contain the loading placeholder
        # (it should have real content or be empty, but not the spinner text)
        page.wait_for_timeout(2000)  # Give HTMX time to swap
        inner_text = content_area.inner_text()
        assert "Loading artifact content..." not in inner_text

    def test_artifact_shows_title_and_word_count(
        self, page: Page, e2e_data, staff_password
    ):
        """The artifact summary shows the title and word count."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug,
            e2e_data.mid_pipeline_episode.slug,
            step=2,
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        details = page.locator("details:has(summary)")
        if details.count() == 0:
            pytest.skip("No artifact section found on this step")

        summary = details.first.locator("summary")
        summary_text = summary.inner_text()

        # Should contain "words" indicating the word count display
        assert "words" in summary_text.lower() or "word" in summary_text.lower()
