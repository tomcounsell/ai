"""Unit tests for tools.video_watch.grok.fetch_x_context.

No real network: httpx is patched. Covers the missing-key degrade, HTTP-error
degrade, malformed-response degrade, and the happy path.
"""

from unittest.mock import MagicMock, patch

from tools.video_watch import grok


def test_missing_key_returns_none(monkeypatch):
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    assert grok.fetch_x_context("https://x.com/user/status/1") is None


def test_happy_path_returns_content(monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "xai-test")
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "choices": [{"message": {"content": "  @user posted: a demo video showing a chart.  "}}]
    }
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.post.return_value = fake_response
    with patch.object(grok.httpx, "Client", return_value=fake_client):
        out = grok.fetch_x_context("https://x.com/user/status/1", "what chart?")
    assert out == "@user posted: a demo video showing a chart."
    # question was woven into the prompt
    sent = fake_client.post.call_args.kwargs["json"]
    assert "what chart?" in sent["messages"][0]["content"]


def test_http_error_returns_none(monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "xai-test")
    fake_response = MagicMock()
    fake_response.status_code = 500
    fake_response.text = "server error"
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.post.return_value = fake_response
    with patch.object(grok.httpx, "Client", return_value=fake_client):
        assert grok.fetch_x_context("https://x.com/user/status/1") is None


def test_empty_choices_returns_none(monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "xai-test")
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"choices": []}
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.post.return_value = fake_response
    with patch.object(grok.httpx, "Client", return_value=fake_client):
        assert grok.fetch_x_context("https://x.com/user/status/1") is None


def test_exception_is_non_fatal(monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "xai-test")
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.post.side_effect = RuntimeError("boom")
    with patch.object(grok.httpx, "Client", return_value=fake_client):
        assert grok.fetch_x_context("https://x.com/user/status/1") is None
