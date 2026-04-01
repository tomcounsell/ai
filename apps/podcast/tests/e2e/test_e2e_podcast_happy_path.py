"""E2E test for the full podcast happy path.

Layer 5: Full Composition -- one long test that chains the entire flow from
login through episode creation, workflow navigation, and published episode
verification. Steps 1-9 create and navigate a fresh episode. Steps 10-11 use
the pre-created ``published_episode`` fixture since the real AI pipeline
cannot run in tests.

Marked ``@pytest.mark.local_only`` because it creates data (episode creation).

Run with:
    python tools/testing/browser_test_runner.py apps/podcast/tests/e2e/test_e2e_podcast_happy_path.py
"""

import pytest
from playwright.sync_api import Page, expect

from apps.podcast.tests.e2e.e2e_helpers import (
    episode_create_url,
    episode_detail_url,
    episode_workflow_url,
    login_as,
    podcast_detail_url,
)


@pytest.mark.local_only
class TestPodcastHappyPath:
    """Full happy-path flow from login to published episode verification."""

    def test_full_podcast_flow(self, page: Page, e2e_data, staff_password):
        """Chain the entire podcast workflow: login -> create -> workflow -> publish view.

        Steps 1-9 exercise episode creation and workflow navigation on a fresh
        episode.  Steps 10-11 verify the published episode detail page using
        the pre-created ``published_episode`` fixture (we cannot run the real
        AI pipeline in tests).
        """
        podcast_slug = e2e_data.podcast.slug

        # ------------------------------------------------------------------
        # Step 1: Login as staff
        # ------------------------------------------------------------------
        login_as(page, e2e_data.staff_user.username, staff_password)
        # Verify we are logged in by checking we can reach the podcast detail
        page.goto(podcast_detail_url(podcast_slug))
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("body")).to_contain_text(e2e_data.podcast.title)

        # ------------------------------------------------------------------
        # Step 2: Verify podcast detail shows "New Episode" for staff
        # ------------------------------------------------------------------
        expect(page.locator("body")).to_contain_text("New Episode")

        # ------------------------------------------------------------------
        # Step 3: Navigate to the episode creation form
        # ------------------------------------------------------------------
        page.goto(episode_create_url(podcast_slug))
        page.wait_for_load_state("domcontentloaded")
        assert "/new/" in page.url

        # ------------------------------------------------------------------
        # Step 4: Fill creation form (title + description) and submit
        # ------------------------------------------------------------------
        episode_title = "E2E Happy Path Episode"
        page.fill('input[name="title"]', episode_title)
        page.fill(
            'textarea[name="description"]',
            "Created by the full happy-path E2E test.",
        )
        page.get_by_role("button", name="Create Episode").click()
        page.wait_for_load_state("domcontentloaded")

        # ------------------------------------------------------------------
        # Step 5: Verify redirect to workflow step 1
        # ------------------------------------------------------------------
        assert "/edit/1/" in page.url
        assert podcast_slug in page.url

        # ------------------------------------------------------------------
        # Step 6: Verify editable fields on step 1
        # ------------------------------------------------------------------
        expect(page.locator("body")).to_contain_text(episode_title)
        # Step 1 (Setup) should show the episode title somewhere on the page
        expect(page.locator("body")).to_contain_text("Setup")

        # ------------------------------------------------------------------
        # Step 7: Navigate through workflow steps via sidebar
        # ------------------------------------------------------------------
        # Extract the created episode slug from the current URL
        # URL pattern: /podcast/{podcast_slug}/{episode_slug}/edit/1/
        url_parts = page.url.rstrip("/").split("/")
        # Find 'edit' in the URL and the slug is two parts before it
        edit_index = url_parts.index("edit")
        created_episode_slug = url_parts[edit_index - 1]

        # Navigate to step 5 (Cross-Validation) via direct URL
        step5_url = episode_workflow_url(podcast_slug, created_episode_slug, step=5)
        page.goto(step5_url)
        page.wait_for_load_state("domcontentloaded")
        assert "/edit/5/" in page.url
        expect(page.locator("body")).to_contain_text("Cross-Validation")

        # ------------------------------------------------------------------
        # Step 8: At step 9, verify audio section (upload form or player)
        # ------------------------------------------------------------------
        step9_url = episode_workflow_url(podcast_slug, created_episode_slug, step=9)
        page.goto(step9_url)
        page.wait_for_load_state("domcontentloaded")
        assert "/edit/9/" in page.url
        expect(page.locator("body")).to_contain_text("Audio Generation")

        # ------------------------------------------------------------------
        # Step 9: Navigate to step 12, verify publish confirmation
        # ------------------------------------------------------------------
        step12_url = episode_workflow_url(podcast_slug, created_episode_slug, step=12)
        page.goto(step12_url)
        page.wait_for_load_state("domcontentloaded")
        assert "/edit/12/" in page.url
        expect(page.locator("body")).to_contain_text("Publish")

        # ------------------------------------------------------------------
        # Step 10: View published episode detail (pre-created fixture)
        # ------------------------------------------------------------------
        published_url = episode_detail_url(
            podcast_slug,
            e2e_data.published_episode.slug,
        )
        page.goto(published_url)
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("body")).to_contain_text(e2e_data.published_episode.title)

        # ------------------------------------------------------------------
        # Step 11: Verify audio player, resources, and platform links
        # ------------------------------------------------------------------
        # Audio player
        audio = page.locator("audio")
        expect(audio).to_be_visible()

        # Download button
        download_link = page.locator("a:has-text('Download Audio')")
        expect(download_link).to_be_visible()

        # Resource links
        report_link = page.locator("a:has-text('View Report')")
        expect(report_link).to_be_visible()

        sources_link = page.locator("a:has-text('View Sources')")
        expect(sources_link).to_be_visible()

        # Platform links
        spotify_link = page.locator("a:has-text('Spotify')")
        expect(spotify_link).to_be_visible()

        apple_link = page.locator("a:has-text('Apple Podcasts')")
        expect(apple_link).to_be_visible()

        # RSS link
        rss_link = page.locator("a:has-text('RSS')")
        expect(rss_link).to_be_visible()
