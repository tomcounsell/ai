"""Failure-path tests: the JSON direct-API tools report a truncated reply.

A Sonnet 5.5 reply can spend its whole ``max_tokens`` on thinking and stop
with ``stop_reason: max_tokens`` (Anthropic) or ``finish_reason: length``
(OpenRouter). The tools must surface that as an explicit truncation error,
never as a parse failure or empty content. Only the HTTP/SDK boundary is
faked; the canned responses mirror the live shapes from the plan's spikes.
"""

from unittest.mock import MagicMock, patch

import pytest
from anthropic.types import Message, ThinkingBlock, Usage

import tools.image_tagging as image_tagging
import tools.test_judge as test_judge


def _truncated_sdk_message() -> Message:
    return Message(
        id="msg_test",
        type="message",
        role="assistant",
        model="claude-sonnet-5-5",
        content=[ThinkingBlock(type="thinking", thinking="", signature="sig")],
        stop_reason="max_tokens",
        stop_sequence=None,
        usage=Usage(input_tokens=10, output_tokens=8192),
    )


def _http_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


_ANTHROPIC_TRUNCATED = {
    "stop_reason": "max_tokens",
    "content": [{"type": "thinking", "thinking": "", "signature": "sig"}],
}
_OPENROUTER_TRUNCATED = {
    "choices": [{"finish_reason": "length", "message": {"content": "", "reasoning": "..."}}]
}


def _judge():
    return test_judge.judge_test_result(
        test_output="The function returns 4 for add(2, 2).",
        expected_criteria=["Output states a result", "Result is correct"],
    )


def test_test_judge_anthropic_truncation_names_truncation(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = MagicMock()
    client.messages.create.return_value = _truncated_sdk_message()
    with patch.object(test_judge.anthropic, "Anthropic", return_value=client):
        result = _judge()
    assert "truncated" in result["error"].lower()
    assert "max_tokens=8192" in result["error"]


def test_test_judge_openrouter_truncation_names_truncation(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    with patch.object(
        test_judge.requests, "post", return_value=_http_response(_OPENROUTER_TRUNCATED)
    ):
        result = _judge()
    assert "truncated" in result["error"].lower()


@pytest.mark.parametrize(
    ("anthropic_key", "payload"),
    [
        pytest.param("test-key", _ANTHROPIC_TRUNCATED, id="anthropic"),
        pytest.param(None, _OPENROUTER_TRUNCATED, id="openrouter"),
    ],
)
def test_image_tagging_truncation_names_truncation(monkeypatch, anthropic_key, payload):
    if anthropic_key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", anthropic_key)
    else:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    with patch.object(image_tagging.requests, "post", return_value=_http_response(payload)):
        result = image_tagging.tag_image("data:image/png;base64,iVBORw0KGgo=")
    assert "truncated" in result["error"].lower()
    assert "max_tokens=4096" in result["error"]
