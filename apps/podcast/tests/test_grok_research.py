"""
Tests for the Grok Deep Research CLI tool.

Uses mocking to avoid live API calls. Follows the same pattern as other
research tool tests in this project.
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from apps.podcast.tools.grok_deep_research import (
    extract_metadata,
    get_api_key,
    run_grok_research,
    save_metadata,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_response():
    """A valid Grok API response matching OpenAI chat completions format."""
    return {
        "id": "chatcmpl-abc123",
        "object": "chat.completion",
        "created": 1712345678,
        "model": "grok-3",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Research results about quantum computing...",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 50,
            "completion_tokens": 200,
            "total_tokens": 250,
        },
    }


@pytest.fixture
def mock_env_no_key():
    """Ensure GROK_API_KEY is not set in the environment."""
    with patch.dict(os.environ, {}, clear=True):
        # Also patch Path to prevent .env file fallback
        mock_cwd = MagicMock()
        mock_cwd.parents = []
        mock_cwd.__truediv__ = MagicMock(
            return_value=MagicMock(exists=MagicMock(return_value=False))
        )
        with patch("apps.podcast.tools.grok_deep_research.Path") as mock_path:
            mock_path.cwd.return_value = mock_cwd
            yield


# ---------------------------------------------------------------------------
# API Key Tests
# ---------------------------------------------------------------------------


class TestGetApiKey:
    def test_api_key_from_env(self):
        """get_api_key() returns the key when set in environment."""
        with patch.dict(os.environ, {"GROK_API_KEY": "xai-test-key-123"}):
            assert get_api_key() == "xai-test-key-123"

    def test_api_key_missing(self, mock_env_no_key):
        """get_api_key() returns None when env var is unset and no .env file."""
        assert get_api_key() is None


# ---------------------------------------------------------------------------
# Core Function Tests
# ---------------------------------------------------------------------------


class TestRunGrokResearch:
    def test_missing_api_key_returns_none(self, mock_env_no_key):
        """run_grok_research() returns (None, {}) when API key is missing."""
        content, result = run_grok_research("test prompt", verbose=False)
        assert content is None
        assert result == {}

    @patch("apps.podcast.tools.grok_deep_research.get_api_key")
    @patch("apps.podcast.tools.grok_deep_research.requests.post")
    def test_successful_response(self, mock_post, mock_key, sample_response):
        """Successful API call returns content and full response dict."""
        mock_key.return_value = "xai-test-key"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample_response
        mock_post.return_value = mock_resp

        content, result = run_grok_research("test prompt", verbose=False)

        assert content == "Research results about quantum computing..."
        assert result["model"] == "grok-3"
        assert result["usage"]["total_tokens"] == 250

        # Verify correct API call
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs.kwargs["url"] == "https://api.x.ai/v1/chat/completions"
        payload = call_kwargs.kwargs["json"]
        assert payload["model"] == "grok-3"
        assert payload["stream"] is False

    @patch("apps.podcast.tools.grok_deep_research.get_api_key")
    @patch("apps.podcast.tools.grok_deep_research.requests.post")
    def test_auth_error_401(self, mock_post, mock_key):
        """401 response returns (None, {})."""
        mock_key.return_value = "xai-bad-key"
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {"error": "Invalid API key"}
        mock_post.return_value = mock_resp

        content, result = run_grok_research("test prompt", verbose=False)
        assert content is None
        assert result == {}

    @patch("apps.podcast.tools.grok_deep_research.get_api_key")
    @patch("apps.podcast.tools.grok_deep_research.requests.post")
    def test_rate_limit_429(self, mock_post, mock_key):
        """429 response returns (None, {})."""
        mock_key.return_value = "xai-test-key"
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.json.return_value = {"error": "Rate limit exceeded"}
        mock_post.return_value = mock_resp

        content, result = run_grok_research("test prompt", verbose=False)
        assert content is None
        assert result == {}

    @patch("apps.podcast.tools.grok_deep_research.get_api_key")
    @patch("apps.podcast.tools.grok_deep_research.requests.post")
    def test_server_error_500(self, mock_post, mock_key):
        """500 response returns (None, {})."""
        mock_key.return_value = "xai-test-key"
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {"error": "Internal server error"}
        mock_post.return_value = mock_resp

        content, result = run_grok_research("test prompt", verbose=False)
        assert content is None
        assert result == {}

    @patch("apps.podcast.tools.grok_deep_research.get_api_key")
    @patch("apps.podcast.tools.grok_deep_research.requests.post")
    def test_timeout_handling(self, mock_post, mock_key):
        """Timeout triggers retries, eventually returns (None, {})."""
        mock_key.return_value = "xai-test-key"
        mock_post.side_effect = requests.exceptions.Timeout("Connection timed out")

        content, result = run_grok_research(
            "test prompt", verbose=False, timeout=10, max_retries=2
        )
        assert content is None
        assert result == {}
        # Should have retried
        assert mock_post.call_count == 2

    @patch("apps.podcast.tools.grok_deep_research.get_api_key")
    @patch("apps.podcast.tools.grok_deep_research.requests.post")
    def test_malformed_json_response(self, mock_post, mock_key):
        """200 with invalid JSON returns (None, {})."""
        mock_key.return_value = "xai-test-key"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = json.JSONDecodeError("", "", 0)
        mock_resp.text = "not json"
        mock_post.return_value = mock_resp

        content, result = run_grok_research("test prompt", verbose=False)
        assert content is None
        assert result == {}


# ---------------------------------------------------------------------------
# Metadata Tests
# ---------------------------------------------------------------------------


class TestMetadata:
    def test_extract_metadata(self, sample_response):
        """extract_metadata() returns structured metadata from response."""
        meta = extract_metadata(sample_response)

        assert meta["model"] == "grok-3"
        assert "timestamp" in meta
        assert meta["usage"]["prompt_tokens"] == 50
        assert meta["usage"]["completion_tokens"] == 200
        assert meta["usage"]["total_tokens"] == 250
        assert "cost" in meta
        assert meta["cost"]["total"] > 0

    def test_extract_metadata_no_usage(self):
        """extract_metadata() handles responses without usage data."""
        result = {"model": "grok-3"}
        meta = extract_metadata(result)

        assert meta["model"] == "grok-3"
        assert "usage" not in meta

    def test_save_metadata_creates_sidecar(self, tmp_path):
        """save_metadata() creates a .meta.json sidecar file."""
        output_file = tmp_path / "results.md"
        output_file.write_text("test content")

        meta = {"model": "grok-3", "timestamp": "2026-04-04T12:00:00"}
        meta_path = save_metadata(meta, str(output_file))

        assert Path(meta_path).exists()
        assert meta_path.endswith(".meta.json")

        saved = json.loads(Path(meta_path).read_text())
        assert saved["model"] == "grok-3"
