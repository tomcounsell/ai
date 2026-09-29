"""Unit tests for tools/llm_reply.py: direct-API reply extraction and parsing.

Covers the Sonnet 5.5 reply shapes observed live (plan spike-1 / spike-2):
leading thinking blocks, truncation at max_tokens, reasoning prose ahead of
the JSON answer, and fenced JSON.
"""

import pytest
from anthropic.types import Message, TextBlock, ThinkingBlock, Usage

from tools.llm_reply import (
    ReplyTruncated,
    anthropic_text,
    openrouter_text,
    parse_last_json,
)


def _sdk_message(blocks, stop_reason="end_turn"):
    return Message(
        id="msg_test",
        type="message",
        role="assistant",
        model="claude-sonnet-5-5",
        content=blocks,
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=Usage(input_tokens=10, output_tokens=10),
    )


def _thinking_sdk():
    return ThinkingBlock(type="thinking", thinking="", signature="sig")


# --- anthropic_text -------------------------------------------------------


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(
            {
                "stop_reason": "end_turn",
                "content": [
                    {"type": "thinking", "thinking": "", "signature": "sig"},
                    {"type": "text", "text": '{"ok": true}'},
                ],
            },
            id="dict",
        ),
        pytest.param(
            _sdk_message([_thinking_sdk(), TextBlock(type="text", text='{"ok": true}')]),
            id="sdk-object",
        ),
    ],
)
def test_anthropic_text_skips_leading_thinking_block(response):
    assert anthropic_text(response) == '{"ok": true}'


def test_anthropic_text_joins_multiple_text_blocks():
    response = {
        "stop_reason": "end_turn",
        "content": [
            {"type": "text", "text": "part one "},
            {"type": "thinking", "thinking": ""},
            {"type": "text", "text": "part two"},
        ],
    }
    assert anthropic_text(response) == "part one part two"


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(
            {"stop_reason": "max_tokens", "content": [{"type": "thinking", "thinking": ""}]},
            id="dict",
        ),
        pytest.param(_sdk_message([_thinking_sdk()], stop_reason="max_tokens"), id="sdk-object"),
    ],
)
def test_anthropic_text_raises_on_max_tokens(response):
    with pytest.raises(ReplyTruncated, match="max_tokens=300"):
        anthropic_text(response, max_tokens=300)


def test_anthropic_text_thinking_only_end_turn_returns_empty():
    assert anthropic_text(_sdk_message([_thinking_sdk()])) == ""
    assert anthropic_text({"stop_reason": "end_turn", "content": []}) == ""


# --- openrouter_text ------------------------------------------------------


def test_openrouter_text_returns_content_ignoring_reasoning():
    result = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": "answer", "reasoning": "long thought"},
            }
        ]
    }
    assert openrouter_text(result) == "answer"


def test_openrouter_text_raises_on_length():
    result = {"choices": [{"finish_reason": "length", "message": {"content": ""}}]}
    with pytest.raises(ReplyTruncated, match="max_tokens=300"):
        openrouter_text(result, max_tokens=300)


@pytest.mark.parametrize(
    "result",
    [{}, {"choices": []}, {"choices": [{"finish_reason": "stop", "message": {"content": None}}]}],
)
def test_openrouter_text_empty_returns_empty_string(result):
    assert openrouter_text(result) == ""


# --- parse_last_json ------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            'Criterion 1 is met because {braces} appear.\nOverall it passes.\n{"pass_fail": true}',
            {"pass_fail": True},
            id="reasoning-prose-before-json",
        ),
        pytest.param('```json\n{"a": 1}\n```', {"a": 1}, id="fenced"),
        pytest.param(
            'Thoughts first.\n```json\n{"a": [1, 2]}\n```\n',
            {"a": [1, 2]},
            id="prose-then-fenced",
        ),
        pytest.param(
            '{"outer": {"inner": {"x": [1, {"y": 2}]}}, "b": "c"}',
            {"outer": {"inner": {"x": [1, {"y": 2}]}}, "b": "c"},
            id="nested",
        ),
        pytest.param('{"first": 1} then {"second": 2}', {"second": 2}, id="last-value-wins"),
        pytest.param('["a", "b"]', ["a", "b"], id="top-level-array"),
        pytest.param('{"a": 1}\nHope that helps.', {"a": 1}, id="trailing-prose"),
        pytest.param('{"a": "```x```"}', {"a": "```x```"}, id="fence-inside-string-bare"),
        pytest.param(
            'Reasoning.\n  ```json  \n{"reasoning": "run ```pytest``` first"}\n```\n',
            {"reasoning": "run ```pytest``` first"},
            id="fence-inside-string-in-fenced-block",
        ),
    ],
)
def test_parse_last_json(text, expected):
    assert parse_last_json(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "   \n ", "No JSON here at all.", '{"a": ', "```json\n```"],
    ids=["empty", "whitespace", "prose-only", "truncated", "empty-fence"],
)
def test_parse_last_json_raises_when_no_value(text):
    with pytest.raises(ValueError):
        parse_last_json(text)
