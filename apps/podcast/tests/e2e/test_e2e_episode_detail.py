"""E2E tests for the published episode detail page.

Layer 4: Published Episode -- read-only tests that verify the episode detail
page renders all expected elements (audio player, download button, resource
links, platform links, RSS, back navigation).

These tests are production-compatible (no @pytest.mark.local_only) since they
only read public pages and never mutate data.

Run with:
    python tools/testing/browser_test_runner.py apps/podcast/tests/e2e/test_e2e_episode_detail.py
"""

from playwright.sync_api import Page, expect

from apps.podcast.tests.e2e.e2e_helpers import episode_detail_url, podcast_detail_url


class TestEpisodeDetailLoads:
    """Verify the episode detail page loads and renders correctly."""

    def test_episode_detail_loads(self, page: Page, e2e_data):
        """Episode detail page at /podcast/{slug}/{ep_slug}/ returns 200."""
        url = episode_detail_url(
            e2e_data.podcast.slug,
            e2e_data.published_episode.slug,
        )
        response = page.goto(url)
        assert response.status == 200

    def test_episode_detail_shows_title(self, page: Page, e2e_data):
        """Episode detail page displays the episode title."""
        url = episode_detail_url(
            e2e_data.podcast.slug,
            e2e_data.published_episode.slug,
        )
        page.goto(url)
        expect(page.locator("body")).to_contain_text(e2e_data.published_episode.title)


class TestAudioPlayer:
    """Verify the audio player and download button are present."""

    def test_audio_player_present(self, page: Page, e2e_data):
        """Page contains an <audio> element with the correct source URL."""
        url = episode_detail_url(
            e2e_data.podcast.slug,
            e2e_data.published_episode.slug,
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        audio = page.locator("audio")
        expect(audio).to_be_visible()

        source = page.locator("audio source")
        src_attr = source.get_attribute("src")
        assert src_attr == e2e_data.published_episode.audio_url

    def test_download_button_present(self, page: Page, e2e_data):
        """Page has a download link pointing to the audio URL."""
        url = episode_detail_url(
            e2e_data.podcast.slug,
            e2e_data.published_episode.slug,
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        download_link = page.locator("a:has-text('Download Audio')")
        expect(download_link).to_be_visible()
        href = download_link.get_attribute("href")
        assert href == e2e_data.published_episode.audio_url


class TestResourceLinks:
    """Verify report and sources links on the episode detail page."""

    def test_report_link_works(self, page: Page, e2e_data):
        """'View Report' link is visible and contains the correct URL path."""
        url = episode_detail_url(
            e2e_data.podcast.slug,
            e2e_data.published_episode.slug,
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        report_link = page.locator("a:has-text('View Report')")
        expect(report_link).to_be_visible()
        href = report_link.get_attribute("href")
        assert e2e_data.published_episode.slug in href
        assert "report" in href.lower()

    def test_sources_link_works(self, page: Page, e2e_data):
        """'View Sources' link is visible and contains the correct URL path."""
        url = episode_detail_url(
            e2e_data.podcast.slug,
            e2e_data.published_episode.slug,
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        sources_link = page.locator("a:has-text('View Sources')")
        expect(sources_link).to_be_visible()
        href = sources_link.get_attribute("href")
        assert e2e_data.published_episode.slug in href
        assert "sources" in href.lower()


class TestPlatformAndRSSLinks:
    """Verify platform links and RSS feed link are present."""

    def test_platform_links_present(self, page: Page, e2e_data):
        """Spotify and Apple Podcasts links are visible on the detail page."""
        url = episode_detail_url(
            e2e_data.podcast.slug,
            e2e_data.published_episode.slug,
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        spotify_link = page.locator("a:has-text('Spotify')")
        expect(spotify_link).to_be_visible()

        apple_link = page.locator("a:has-text('Apple Podcasts')")
        expect(apple_link).to_be_visible()

    def test_rss_link_present(self, page: Page, e2e_data):
        """RSS feed link is visible on the episode detail page."""
        url = episode_detail_url(
            e2e_data.podcast.slug,
            e2e_data.published_episode.slug,
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        rss_link = page.locator("a:has-text('RSS')")
        expect(rss_link).to_be_visible()


class TestBackNavigation:
    """Verify back navigation from episode detail to podcast detail."""

    def test_back_navigation_to_podcast(self, page: Page, e2e_data):
        """Back link navigates to the podcast detail page."""
        url = episode_detail_url(
            e2e_data.podcast.slug,
            e2e_data.published_episode.slug,
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        back_link = page.locator(f'a[href*="{e2e_data.podcast.slug}"]').filter(
            has_text="Back to"
        )
        expect(back_link).to_be_visible()

        href = back_link.get_attribute("href")
        expected_path = podcast_detail_url(e2e_data.podcast.slug).replace(
            "http://localhost:8000", ""
        )
        assert expected_path in href or e2e_data.podcast.slug in href
