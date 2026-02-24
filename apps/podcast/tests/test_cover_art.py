"""Tests for podcast cover art generation pipeline.

Tests cover:
- generate_cover_image() in tools/generate_cover.py
- apply_branding() in tools/add_logo_watermark.py
- generate_cover_art() service in services/publishing.py
- step_cover_art task (no NotImplementedError handling)
"""

import base64
import io
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from apps.podcast.models import Episode, EpisodeArtifact, EpisodeWorkflow, Podcast

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def podcast():
    """Create a test podcast."""
    return Podcast.objects.create(
        title="Test Podcast",
        slug="test-podcast",
        description="Test podcast description",
        author_name="Test Author",
        author_email="test@example.com",
    )


@pytest.fixture
def episode(podcast):
    """Create a test episode with report text."""
    return Episode.objects.create(
        podcast=podcast,
        title="Test Episode",
        slug="test-episode",
        description="Test description",
        report_text="This is a test report about artificial intelligence and its impacts.",
    )


@pytest.fixture
def episode_no_report(podcast):
    """Create a test episode without report text."""
    return Episode.objects.create(
        podcast=podcast,
        title="Bare Episode",
        slug="bare-episode",
        description="Episode without report",
    )


def _make_png_bytes(width=100, height=100, color=(245, 241, 232)):
    """Helper: create minimal PNG bytes."""
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _make_b64_data_url(png_bytes: bytes) -> str:
    """Wrap raw PNG bytes in a data-URL string."""
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode()


# ---------------------------------------------------------------------------
# generate_cover_image()
# ---------------------------------------------------------------------------


class TestGenerateCoverImage:
    """Unit tests for tools/generate_cover.py::generate_cover_image."""

    def test_returns_bytes_from_data_url(self):
        """generate_cover_image returns decoded PNG bytes from a data-URL response."""
        from apps.podcast.tools.generate_cover import generate_cover_image

        png = _make_png_bytes()
        data_url = _make_b64_data_url(png)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"images": [data_url]}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response):
            result = generate_cover_image("test prompt", "fake-key")

        assert isinstance(result, bytes)
        assert result == png

    def test_returns_bytes_from_dict_image(self):
        """generate_cover_image handles images returned as dict objects."""
        from apps.podcast.tools.generate_cover import generate_cover_image

        png = _make_png_bytes()
        data_url = _make_b64_data_url(png)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"images": [{"image_url": {"url": data_url}}]}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response):
            result = generate_cover_image("test prompt", "fake-key")

        assert result == png

    def test_raises_on_no_choices(self):
        """generate_cover_image raises RuntimeError when no choices returned."""
        from apps.podcast.tools.generate_cover import generate_cover_image

        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": []}
        mock_response.raise_for_status = MagicMock()

        with (
            patch("requests.post", return_value=mock_response),
            pytest.raises(RuntimeError, match="No valid response"),
        ):
            generate_cover_image("test prompt", "fake-key")

    def test_raises_on_no_images(self):
        """generate_cover_image raises RuntimeError when no images in response."""
        from apps.podcast.tools.generate_cover import generate_cover_image

        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": [{"message": {"images": []}}]}
        mock_response.raise_for_status = MagicMock()

        with (
            patch("requests.post", return_value=mock_response),
            pytest.raises(RuntimeError, match="No images returned"),
        ):
            generate_cover_image("test prompt", "fake-key")

    def test_raises_on_empty_image_url(self):
        """generate_cover_image raises RuntimeError when image URL is empty."""
        from apps.podcast.tools.generate_cover import generate_cover_image

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"images": [{"image_url": {"url": ""}}]}}]
        }
        mock_response.raise_for_status = MagicMock()

        with (
            patch("requests.post", return_value=mock_response),
            pytest.raises(RuntimeError, match="Could not extract image URL"),
        ):
            generate_cover_image("test prompt", "fake-key")


# ---------------------------------------------------------------------------
# apply_branding()
# ---------------------------------------------------------------------------


class TestApplyBranding:
    """Unit tests for tools/add_logo_watermark.py::apply_branding."""

    def test_returns_bytes(self):
        """apply_branding returns PNG bytes."""
        from apps.podcast.tools.add_logo_watermark import apply_branding

        png = _make_png_bytes(1024, 1024)
        result = apply_branding(png)

        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_output_is_valid_png(self):
        """apply_branding output can be opened as a valid image."""
        from apps.podcast.tools.add_logo_watermark import apply_branding

        png = _make_png_bytes(1024, 1024)
        result = apply_branding(png)

        img = Image.open(io.BytesIO(result))
        assert img.format == "PNG"
        assert img.size == (1024, 1024)

    def test_with_series_text(self):
        """apply_branding works with series_text parameter."""
        from apps.podcast.tools.add_logo_watermark import apply_branding

        png = _make_png_bytes(512, 512)
        result = apply_branding(png, series_text="Algorithms for Life")

        assert isinstance(result, bytes)
        img = Image.open(io.BytesIO(result))
        assert img.size == (512, 512)

    def test_dark_background_uses_white_text(self):
        """apply_branding handles dark backgrounds (white text path)."""
        from apps.podcast.tools.add_logo_watermark import apply_branding

        dark_png = _make_png_bytes(256, 256, color=(20, 20, 20))
        result = apply_branding(dark_png)

        assert isinstance(result, bytes)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# generate_cover_art() service
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGenerateCoverArtService:
    """Integration tests for services/publishing.py::generate_cover_art."""

    def test_skips_when_no_api_key(self, episode):
        """generate_cover_art returns None and creates placeholder when no API key."""
        from apps.podcast.services.publishing import generate_cover_art

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}, clear=False):
            result = generate_cover_art(episode.id)

        assert result is None
        artifact = EpisodeArtifact.objects.get(episode=episode, title="cover-art")
        assert artifact.metadata["skipped"] is True
        assert "missing_api_key" in artifact.metadata["reason"]

    def test_full_pipeline(self, episode):
        """generate_cover_art runs full pipeline: generate, brand, upload, save."""
        from apps.podcast.services.publishing import generate_cover_art

        png = _make_png_bytes(1024, 1024)

        with (
            patch.dict("os.environ", {"OPENROUTER_API_KEY": "fake-key"}),
            patch(
                "apps.podcast.tools.generate_cover.generate_cover_image",
                return_value=png,
            ) as mock_gen,
            patch(
                "apps.podcast.tools.add_logo_watermark.apply_branding",
                return_value=png,
            ) as mock_brand,
            patch(
                "apps.common.services.storage.store_file",
                return_value="https://storage.example.com/cover.png",
            ) as mock_store,
        ):
            result = generate_cover_art(episode.id)

        assert result == "https://storage.example.com/cover.png"

        # Verify generate was called with a prompt and the API key
        mock_gen.assert_called_once()
        args = mock_gen.call_args
        assert args[0][1] == "fake-key"  # api_key

        # Verify branding was called with the podcast series name
        mock_brand.assert_called_once()
        assert mock_brand.call_args[1]["series_text"] == "Test Podcast"

        # Verify upload
        mock_store.assert_called_once()

        # Verify episode was updated
        episode.refresh_from_db()
        assert episode.cover_image_url == "https://storage.example.com/cover.png"

        # Verify artifact was created
        artifact = EpisodeArtifact.objects.get(episode=episode, title="cover-art")
        assert artifact.metadata["skipped"] is False

    def test_uses_title_only_prompt_when_no_report(self, episode_no_report):
        """generate_cover_art uses a fallback prompt when no report or content plan."""
        from apps.podcast.services.publishing import generate_cover_art

        png = _make_png_bytes(1024, 1024)

        with (
            patch.dict("os.environ", {"OPENROUTER_API_KEY": "fake-key"}),
            patch(
                "apps.podcast.tools.generate_cover.generate_cover_image",
                return_value=png,
            ) as mock_gen,
            patch(
                "apps.podcast.tools.add_logo_watermark.apply_branding",
                return_value=png,
            ),
            patch(
                "apps.common.services.storage.store_file",
                return_value="https://storage.example.com/cover.png",
            ),
        ):
            generate_cover_art(episode_no_report.id)

        # Prompt should contain the episode title
        prompt = mock_gen.call_args[0][0]
        assert "Bare Episode" in prompt

    def test_episode_not_found_raises(self):
        """generate_cover_art raises DoesNotExist for invalid episode_id."""
        from apps.podcast.services.publishing import generate_cover_art

        with pytest.raises(Episode.DoesNotExist):
            generate_cover_art(99999)


# ---------------------------------------------------------------------------
# step_cover_art task
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStepCoverArtTask:
    """Tests for tasks.py::step_cover_art (no NotImplementedError catch)."""

    def test_wrong_step_raises_value_error(self, episode):
        """step_cover_art raises ValueError when workflow is at wrong step."""
        from apps.podcast.tasks import step_cover_art

        EpisodeWorkflow.objects.create(
            episode=episode,
            current_step="Setup",
            status="running",
            history=[],
        )

        with pytest.raises(ValueError, match="not 'Publishing Assets'"):
            step_cover_art.call(episode.id)

    def test_calls_generate_cover_art(self, episode):
        """step_cover_art delegates to publishing.generate_cover_art."""
        from apps.podcast.tasks import step_cover_art

        EpisodeWorkflow.objects.create(
            episode=episode,
            current_step="Publishing Assets",
            status="running",
            history=[],
        )

        with patch("apps.podcast.services.publishing.generate_cover_art") as mock_gen:
            step_cover_art.call(episode.id)

        mock_gen.assert_called_once_with(episode.id)

    def test_exception_fails_step(self, episode):
        """step_cover_art calls fail_step and re-raises on unexpected error."""
        from apps.podcast.tasks import step_cover_art

        EpisodeWorkflow.objects.create(
            episode=episode,
            current_step="Publishing Assets",
            status="running",
            history=[],
        )

        with (
            patch(
                "apps.podcast.services.publishing.generate_cover_art",
                side_effect=RuntimeError("API down"),
            ),
            patch("apps.podcast.services.workflow.fail_step") as mock_fail,
            pytest.raises(RuntimeError, match="API down"),
        ):
            step_cover_art.call(episode.id)

        mock_fail.assert_called_once_with(episode.id, "Publishing Assets", "API down")
