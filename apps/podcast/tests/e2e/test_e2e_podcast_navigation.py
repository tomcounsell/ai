"""E2E tests for podcast navigation flows.

Layer 1: Foundation -- verifies list -> detail -> create navigation paths
and breadcrumb links work correctly in a real browser.

Run with:
    python tools/testing/browser_test_runner.py apps/podcast/tests/e2e/test_e2e_podcast_navigation.py
"""

import pytest
from playwright.sync_api import Page, expect

from apps.podcast.tests.e2e.e2e_helpers import (
    login_as,
    podcast_detail_url,
    podcast_list_url,
)


class TestPodcastListPage:
    """Verify the podcast list page loads and displays content."""

    def test_podcast_list_loads(self, page: Page, e2e_data):
        """Podcast list page at /podcast/ loads successfully."""
        response = page.goto(podcast_list_url())
        assert response.status == 200

    def test_podcast_list_shows_title(self, page: Page, e2e_data):
        """Podcast list page displays the E2E podcast title."""
        page.goto(podcast_list_url())
        expect(page.locator("body")).to_contain_text(e2e_data.podcast.title)

    def test_podcast_list_has_link_to_detail(self, page: Page, e2e_data):
        """Podcast list contains a link to the podcast detail page."""
        page.goto(podcast_list_url())
        # Find a link that contains the podcast slug in its href
        link = page.locator(f'a[href*="{e2e_data.podcast.slug}"]').first
        expect(link).to_be_visible()

    @pytest.mark.local_only
    def test_podcast_list_click_navigates_to_detail(self, page: Page, e2e_data):
        """Clicking podcast title link navigates to the detail page."""
        page.goto(podcast_list_url())
        link = page.locator(f'a[href*="{e2e_data.podcast.slug}"]').first
        link.click()
        page.wait_for_load_state("domcontentloaded")
        assert e2e_data.podcast.slug in page.url


class TestPodcastDetailPage:
    """Verify the podcast detail page loads and shows expected elements."""

    def test_podcast_detail_loads(self, page: Page, e2e_data):
        """Podcast detail page returns 200."""
        response = page.goto(podcast_detail_url(e2e_data.podcast.slug))
        assert response.status == 200

    def test_podcast_detail_shows_title(self, page: Page, e2e_data):
        """Podcast detail page displays the podcast title."""
        page.goto(podcast_detail_url(e2e_data.podcast.slug))
        expect(page.locator("body")).to_contain_text(e2e_data.podcast.title)

    def test_podcast_detail_shows_description(self, page: Page, e2e_data):
        """Podcast detail page displays the podcast description."""
        page.goto(podcast_detail_url(e2e_data.podcast.slug))
        expect(page.locator("body")).to_contain_text(e2e_data.podcast.description)

    @pytest.mark.local_only
    def test_podcast_detail_shows_new_episode_button_for_owner(
        self, page: Page, e2e_data, staff_password
    ):
        """Owner sees 'New Episode' button on podcast detail page."""
        login_as(page, e2e_data.owner_user.username, staff_password)
        page.goto(podcast_detail_url(e2e_data.podcast.slug))
        expect(page.locator("body")).to_contain_text("New Episode")

    @pytest.mark.local_only
    def test_podcast_detail_hides_new_episode_for_anon(self, page: Page, e2e_data):
        """Anonymous user does NOT see 'New Episode' button."""
        page.goto(podcast_detail_url(e2e_data.podcast.slug))
        body_text = page.locator("body").text_content()
        assert "New Episode" not in body_text

    def test_breadcrumb_navigation(self, page: Page, e2e_data):
        """Podcast detail has a breadcrumb link back to /podcast/."""
        page.goto(podcast_detail_url(e2e_data.podcast.slug))
        # Should have a link to the podcast list
        breadcrumb_link = page.locator('a[href="/podcast/"]').first
        expect(breadcrumb_link).to_be_visible()

    @pytest.mark.local_only
    def test_breadcrumb_click_returns_to_list(self, page: Page, e2e_data):
        """Clicking breadcrumb link navigates back to podcast list."""
        page.goto(podcast_detail_url(e2e_data.podcast.slug))
        breadcrumb_link = page.locator('a[href="/podcast/"]').first
        breadcrumb_link.click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.rstrip("/").endswith("/podcast")


class TestPublishedEpisodeNavigation:
    """Verify navigation to published episodes from the podcast detail page."""

    def test_published_episode_visible_on_detail(self, page: Page, e2e_data):
        """Published episode title appears on the podcast detail page."""
        page.goto(podcast_detail_url(e2e_data.podcast.slug))
        expect(page.locator("body")).to_contain_text(e2e_data.published_episode.title)

    def test_published_episode_link_exists(self, page: Page, e2e_data):
        """Published episode has a clickable link on the podcast detail page."""
        page.goto(podcast_detail_url(e2e_data.podcast.slug))
        link = page.locator(f'a[href*="{e2e_data.published_episode.slug}"]').first
        expect(link).to_be_visible()

    @pytest.mark.local_only
    def test_published_episode_click_navigates(self, page: Page, e2e_data):
        """Clicking published episode link navigates to episode detail page."""
        page.goto(podcast_detail_url(e2e_data.podcast.slug))
        link = page.locator(f'a[href*="{e2e_data.published_episode.slug}"]').first
        link.click()
        page.wait_for_load_state("domcontentloaded")
        assert e2e_data.published_episode.slug in page.url
