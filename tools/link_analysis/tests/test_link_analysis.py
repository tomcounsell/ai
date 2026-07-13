"""
Integration tests for link-analysis tool.

Run with: pytest tools/link-analysis/tests/ -v
"""

import logging
import os
from unittest.mock import MagicMock

import pytest

import tools.link_analysis as link_analysis_module
from tools.link_analysis import (
    GROQ_TRANSCRIBE_URL,
    OPENAI_TRANSCRIBE_URL,
    analyze_text_links,
    analyze_url,
    extract_urls,
    get_metadata,
    transcribe_audio_file,
    validate_url,
)


class TestLinkAnalysisInstallation:
    """Verify tool is properly configured."""

    def test_import(self):
        """Tool can be imported."""
        from tools.link_analysis import extract_urls

        assert callable(extract_urls)


class TestExtractUrls:
    """Test URL extraction."""

    def test_single_url(self):
        """Extracts single URL."""
        result = extract_urls("Visit https://example.com for more info")

        assert result["count"] == 1
        assert "https://example.com" in result["urls"]

    def test_multiple_urls(self):
        """Extracts multiple URLs."""
        text = "Check https://example.com and https://test.com"
        result = extract_urls(text)

        assert result["count"] == 2

    def test_no_urls(self):
        """Returns empty for text without URLs."""
        result = extract_urls("No links here")

        assert result["count"] == 0
        assert result["urls"] == []

    def test_empty_text(self):
        """Handles empty text."""
        result = extract_urls("")

        assert result["count"] == 0

    def test_duplicate_urls(self):
        """Removes duplicate URLs."""
        text = "https://example.com and https://example.com again"
        result = extract_urls(text)

        assert result["count"] == 1

    def test_url_with_path(self):
        """Extracts URLs with paths."""
        text = "See https://example.com/path/to/page?query=1"
        result = extract_urls(text)

        assert result["count"] == 1
        assert "path/to/page" in result["urls"][0]


class TestValidateUrl:
    """Test URL validation."""

    def test_valid_url(self):
        """Validates accessible URL."""
        result = validate_url("https://httpbin.org/status/200")

        assert result["valid"] is True
        assert result["status_code"] == 200

    def test_invalid_format(self):
        """Rejects invalid URL format."""
        result = validate_url("not-a-url")

        assert result["valid"] is False

    def test_empty_url(self):
        """Rejects empty URL."""
        result = validate_url("")

        assert result["valid"] is False


class TestGetMetadata:
    """Test metadata extraction."""

    def test_get_metadata(self):
        """Gets metadata from URL."""
        result = get_metadata("https://httpbin.org/html")

        assert "url" in result
        assert "content_type" in result


@pytest.mark.skipif(not os.environ.get("PERPLEXITY_API_KEY"), reason="PERPLEXITY_API_KEY not set")
class TestAnalyzeUrl:
    """Test URL content analysis."""

    def test_analyze_url(self):
        """Analyzes URL content."""
        result = analyze_url("https://example.com")

        assert "error" not in result or "URL not accessible" not in result.get("error", "")

    def test_analyze_without_content(self):
        """Analysis without content fetch."""
        result = analyze_url("https://example.com", analyze_content=False)

        assert "validation" in result


class TestAnalyzeTextLinks:
    """Test text link analysis."""

    def test_analyze_text_links(self):
        """Analyzes links in text."""
        text = "Check https://httpbin.org/status/200"
        result = analyze_text_links(text, validate_links=True, analyze_content=False)

        assert result["urls_found"] == 1
        assert len(result["results"]) == 1


class _FakeResponse:
    """Minimal stand-in for an httpx.Response used by transcription tests."""

    def __init__(self, status_code: int, json_data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.text = text

    def json(self) -> dict:
        return self._json_data


class _FakeAsyncClient:
    """Fake httpx.AsyncClient that resolves responses/exceptions by URL."""

    def __init__(self, responses: dict):
        self._responses = responses
        self.calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, **kwargs):
        self.calls.append(url)
        outcome = self._responses.get(url)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class TestTranscribeAudioFile:
    """Backend-selection and fallback behavior for transcribe_audio_file()."""

    @staticmethod
    def _make_audio_file(tmp_path):
        filepath = tmp_path / "test.mp3"
        filepath.write_bytes(b"fake audio bytes")
        return filepath

    def _patch_client(self, monkeypatch, responses: dict) -> _FakeAsyncClient:
        fake_client = _FakeAsyncClient(responses)
        monkeypatch.setattr(
            link_analysis_module.httpx,
            "AsyncClient",
            MagicMock(return_value=fake_client),
        )
        return fake_client

    @pytest.mark.asyncio
    async def test_groq_success_no_openai_call(self, monkeypatch, tmp_path):
        """Groq succeeds; OpenAI is never contacted."""
        monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        filepath = self._make_audio_file(tmp_path)

        fake_client = self._patch_client(
            monkeypatch,
            {GROQ_TRANSCRIBE_URL: _FakeResponse(200, {"text": "groq transcript"})},
        )

        result = await transcribe_audio_file(filepath)

        assert result == "groq transcript"
        assert fake_client.calls == [GROQ_TRANSCRIBE_URL]

    @pytest.mark.asyncio
    async def test_groq_fails_falls_back_to_openai(self, monkeypatch, tmp_path, caplog):
        """Groq failure with an OpenAI key present falls back to OpenAI and logs a warning."""
        monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
        filepath = self._make_audio_file(tmp_path)

        fake_client = self._patch_client(
            monkeypatch,
            {
                GROQ_TRANSCRIBE_URL: _FakeResponse(500, text="groq server error"),
                OPENAI_TRANSCRIBE_URL: _FakeResponse(200, {"text": "openai transcript"}),
            },
        )

        with caplog.at_level(logging.WARNING):
            result = await transcribe_audio_file(filepath)

        assert result == "openai transcript"
        assert fake_client.calls == [GROQ_TRANSCRIBE_URL, OPENAI_TRANSCRIBE_URL]
        assert any("Groq" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_groq_fails_no_openai_key_returns_none(self, monkeypatch, tmp_path, caplog):
        """Groq failure with no OpenAI key configured returns None and logs a warning."""
        monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        filepath = self._make_audio_file(tmp_path)

        fake_client = self._patch_client(
            monkeypatch,
            {GROQ_TRANSCRIBE_URL: _FakeResponse(500, text="groq server error")},
        )

        with caplog.at_level(logging.WARNING):
            result = await transcribe_audio_file(filepath)

        assert result is None
        assert fake_client.calls == [GROQ_TRANSCRIBE_URL]
        assert any("Groq" in record.message for record in caplog.records)
        assert any("OPENAI_API_KEY" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_only_openai_key_set_uses_openai_directly(self, monkeypatch, tmp_path):
        """With no Groq key, OpenAI is used directly and Groq is never contacted."""
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
        filepath = self._make_audio_file(tmp_path)

        fake_client = self._patch_client(
            monkeypatch,
            {OPENAI_TRANSCRIBE_URL: _FakeResponse(200, {"text": "openai transcript"})},
        )

        result = await transcribe_audio_file(filepath)

        assert result == "openai transcript"
        assert fake_client.calls == [OPENAI_TRANSCRIBE_URL]

    @pytest.mark.asyncio
    async def test_no_keys_set_returns_none(self, monkeypatch, tmp_path, caplog):
        """With neither key set, returns None and logs a warning without any HTTP call."""
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        filepath = self._make_audio_file(tmp_path)

        with caplog.at_level(logging.WARNING):
            result = await transcribe_audio_file(filepath)

        assert result is None
        assert any(
            "GROQ_API_KEY" in record.message or "OPENAI_API_KEY" in record.message
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_groq_returns_empty_text_not_none(self, monkeypatch, tmp_path):
        """A 200 response with empty text returns an empty string, not None."""
        monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        filepath = self._make_audio_file(tmp_path)

        fake_client = self._patch_client(
            monkeypatch,
            {GROQ_TRANSCRIBE_URL: _FakeResponse(200, {"text": ""})},
        )

        result = await transcribe_audio_file(filepath)

        assert result == ""
        assert result is not None
        assert fake_client.calls == [GROQ_TRANSCRIBE_URL]
