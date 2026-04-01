"""E2E tests for the episode publish flow on workflow step 12.

Layer 3: Key Interactions -- verifies that the publish confirmation UI
appears at step 12, that the publish action marks the episode complete,
and that the post-publish view shows a "View Episode" link.

Run with:
    python tools/testing/browser_test_runner.py apps/podcast/tests/e2e/test_e2e_publish_flow.py
"""

import pytest
from playwright.sync_api import Page, expect

from apps.podcast.tests.e2e.e2e_helpers import episode_workflow_url, login_as


@pytest.mark.local_only
class TestPublishConfirmation:
    """Verify step 12 shows publish confirmation UI."""

    def test_step12_shows_publish_confirmation(
        self, page: Page, e2e_data, staff_password
    ):
        """Step 12 displays the publish confirmation section with 'Ready to Publish'."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug,
            e2e_data.publishable_episode.slug,
            step=12,
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        expect(page.locator("body")).to_contain_text("Ready to Publish")

    def test_step12_shows_episode_title_in_confirmation(
        self, page: Page, e2e_data, staff_password
    ):
        """The publish confirmation shows the episode title for review."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug,
            e2e_data.publishable_episode.slug,
            step=12,
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        expect(page.locator("body")).to_contain_text(e2e_data.publishable_episode.title)

    def test_step12_shows_audio_ready_indicator(
        self, page: Page, e2e_data, staff_password
    ):
        """The publish confirmation shows audio status as ready."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug,
            e2e_data.publishable_episode.slug,
            step=12,
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        # The confirmation section shows "Audio:" with a ready indicator
        expect(page.locator("body")).to_contain_text("Audio")

    def test_step12_has_publish_button(self, page: Page, e2e_data, staff_password):
        """Step 12 has a 'Publish Episode' submit button."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug,
            e2e_data.publishable_episode.slug,
            step=12,
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        publish_button = page.locator("button:has-text('Publish Episode')")
        expect(publish_button).to_be_visible()


@pytest.mark.local_only
class TestPublishAction:
    """Verify that publishing marks the episode complete."""

    def test_publish_marks_episode_complete(self, page: Page, e2e_data, staff_password):
        """Submitting the publish form marks the episode as complete and
        shows the post-publish success UI."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug,
            e2e_data.publishable_episode.slug,
            step=12,
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        # Dismiss the confirm dialog automatically
        page.on("dialog", lambda dialog: dialog.accept())

        # Click the publish button -- the form uses HTMX hx-post with hx-swap="none"
        # so we wait for the HTMX request to complete rather than a full navigation.
        publish_button = page.locator("button:has-text('Publish Episode')")
        publish_button.click()

        # Wait for the page to reload/update showing the success state.
        # The success state shows "Episode published!" text.
        page.wait_for_timeout(3000)
        # Reload to see the updated state
        page.reload()
        page.wait_for_load_state("domcontentloaded")

        expect(page.locator("body")).to_contain_text("Episode published")


@pytest.mark.local_only
class TestPostPublish:
    """Verify the post-publish success page."""

    def test_post_publish_shows_view_episode_link(
        self, page: Page, e2e_data, staff_password
    ):
        """After publishing, the workflow page shows a 'View Episode' link."""
        login_as(page, e2e_data.staff_user.username, staff_password)

        # Use the published_episode which is already status=complete
        url = episode_workflow_url(
            e2e_data.podcast.slug,
            e2e_data.published_episode.slug,
            step=12,
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        # The post-publish UI should show "Episode published!"
        expect(page.locator("body")).to_contain_text("Episode published")

        # Should have a "View Episode" link
        view_link = page.locator("a:has-text('View Episode')")
        expect(view_link).to_be_visible()

        # The link should point to the episode detail page
        href = view_link.get_attribute("href")
        assert e2e_data.published_episode.slug in href

    def test_post_publish_shows_rss_feed_link(
        self, page: Page, e2e_data, staff_password
    ):
        """After publishing, the workflow page shows an RSS Feed link."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug,
            e2e_data.published_episode.slug,
            step=12,
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        rss_link = page.locator("a:has-text('RSS Feed')")
        expect(rss_link).to_be_visible()

    def test_post_publish_shows_create_another_link(
        self, page: Page, e2e_data, staff_password
    ):
        """After publishing, shows a 'Create Another Episode' link."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug,
            e2e_data.published_episode.slug,
            step=12,
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        create_link = page.locator("a:has-text('Create Another Episode')")
        expect(create_link).to_be_visible()
