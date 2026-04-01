"""E2E tests for audio upload and player on workflow step 9.

Layer 3: Key Interactions -- verifies the audio upload form appears when
an episode is paused at Audio Generation, that an audio player renders
when the episode already has an audio_url, and that the upload form
accepts a file submission.

Run with:
    python tools/testing/browser_test_runner.py apps/podcast/tests/e2e/test_e2e_audio_upload.py
"""

import pathlib

import pytest
from playwright.sync_api import Page, expect

from apps.podcast.tests.e2e.e2e_helpers import episode_workflow_url, login_as

SILENT_MP3 = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "silent.mp3"


@pytest.mark.local_only
class TestAudioUploadForm:
    """Verify step 9 shows an upload form when paused for audio generation."""

    def test_step9_shows_upload_form_when_paused(
        self, page: Page, e2e_data, staff_password
    ):
        """Step 9 displays an audio upload form when the episode is paused
        at Audio Generation and has no audio_url."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug, e2e_data.paused_episode.slug, step=9
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        # Upload form should be visible
        upload_form = page.locator("form:has(input[name='audio_file'])")
        expect(upload_form).to_be_visible()

        # Should contain file input accepting audio types
        file_input = page.locator("input[name='audio_file']")
        expect(file_input).to_be_visible()
        assert file_input.get_attribute("accept") == ".mp3,.wav,.m4a"

        # Should have upload button
        upload_button = page.locator("button:has-text('Upload Audio')")
        expect(upload_button).to_be_visible()

    def test_step9_shows_waiting_message(self, page: Page, e2e_data, staff_password):
        """Step 9 shows informational text about waiting for audio generation."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug, e2e_data.paused_episode.slug, step=9
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        expect(page.locator("body")).to_contain_text("Waiting for audio generation")

    def test_upload_form_accepts_file_submission(
        self, page: Page, e2e_data, staff_password
    ):
        """The upload form accepts a file and submits without client-side error."""
        assert SILENT_MP3.exists(), f"Fixture not found: {SILENT_MP3}"

        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug, e2e_data.paused_episode.slug, step=9
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        # Set the file on the input
        file_input = page.locator("input[name='audio_file']")
        file_input.set_input_files(str(SILENT_MP3))

        # Submit the form via the upload button
        with page.expect_navigation(timeout=15000):
            page.locator("button:has-text('Upload Audio')").click()

        # After submission we should get a 200 (either redirect or re-render)
        # and the page should not show a server error
        assert "500" not in page.title()


@pytest.mark.local_only
class TestAudioPlayer:
    """Verify step 9 shows an audio player when the episode has audio."""

    def test_step9_shows_audio_player_with_audio_url(
        self, page: Page, e2e_data, staff_password
    ):
        """Step 9 displays an <audio> element when episode has audio_url set."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug, e2e_data.published_episode.slug, step=9
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        audio_element = page.locator("audio")
        expect(audio_element).to_be_visible()

        # Audio source should point to the episode's audio_url
        source = page.locator("audio source")
        assert source.get_attribute("src") == e2e_data.published_episode.audio_url

    def test_step9_shows_audio_ready_message(
        self, page: Page, e2e_data, staff_password
    ):
        """Step 9 shows 'Audio file ready' text when audio_url is present."""
        login_as(page, e2e_data.staff_user.username, staff_password)
        url = episode_workflow_url(
            e2e_data.podcast.slug, e2e_data.published_episode.slug, step=9
        )
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        expect(page.locator("body")).to_contain_text("Audio file ready")
