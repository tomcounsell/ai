"""Unit tests for the pure parsing helpers in `scripts/granite_questions_game.py`.

The live game (a real Claude session + ollama) is gated in
`tests/integration/test_granite_questions_game.py`. The option-parsing and
result-extraction helpers are pure and run everywhere -- they are the part most
likely to break on a stream-json shape change, so they are pinned here.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest


def _load_game_module():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts",
        "granite_questions_game.py",
    )
    spec = importlib.util.spec_from_file_location("granite_questions_game", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["granite_questions_game"] = mod  # required for dataclass introspection
    spec.loader.exec_module(mod)
    return mod


gqg = _load_game_module()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Which?\n1. a\n2. b\n3. c", {"1", "2", "3"}),
        ("Which?\n  ❯ 1. a\n  2. b", {"1", "2"}),  # TUI arrow marker
        ("Pick\n1) a\n2) b", {"1", "2"}),  # paren style
        ("No options here, just prose.", set()),
        ("Numbers in prose like 3 dogs but no option lines", set()),
    ],
)
def test_valid_option_numbers(text, expected):
    assert gqg._valid_option_numbers(text) == expected


def test_result_text_prefers_result_event():
    events = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "interim"}]}},
        {"type": "result", "result": "final answer"},
    ]
    assert gqg._result_text(events) == "final answer"


def test_result_text_falls_back_to_assistant_text():
    events = [
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "no result event"}]},
        },
    ]
    assert gqg._result_text(events) == "no result event"


def test_quiz_prompt_mentions_count_and_marker():
    prompt = gqg._quiz_prompt(7)
    assert "7" in prompt
    assert gqg.RESULT_DONE_MARKER in prompt


def test_game_report_rates_are_zero_safe():
    r = gqg.GameReport(model="haiku", questions_requested=5)
    assert r.handle_choice_rate == 0.0
    assert r.in_range_rate == 0.0
    assert r.mean_router_latency_s == 0.0
