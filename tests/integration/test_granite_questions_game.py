"""Live integration test: granite4.1:3b answering a real Claude quiz.

This is the live counterpart to `tests/unit/test_granite_peculiarities.py`. It
spawns a REAL `claude` session (Max OAuth path) that runs a multiple-choice
quiz, and measures whether the real `GraniteRouter` can recognize each numbered
question and enter a valid in-range answer via `handle_choice`.

**Skipped by default.** It spends real Max-subscription tokens and needs a
local ollama with `granite4.1:3b`. Enable with:

    GRANITE_LIVE=1 pytest tests/integration/test_granite_questions_game.py -v -m slow

Acceptance bar (intentionally lenient -- this measures an operator's ability,
not Claude's quiz knowledge): granite must produce a valid in-range
`handle_choice` answer on the majority of question turns. A run that falls
below that is a signal the operator tool taxonomy or prompt needs work, not a
flaky-test failure -- read `logs/granite_questions_game.json`.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
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
    sys.modules["granite_questions_game"] = mod
    spec.loader.exec_module(mod)
    return mod


def _ollama_ready() -> bool:
    try:
        import ollama  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = [pytest.mark.slow, pytest.mark.integration]


@pytest.mark.skipif(
    os.environ.get("GRANITE_LIVE") != "1" or shutil.which("claude") is None or not _ollama_ready(),
    reason=(
        "live questions-game spike: set GRANITE_LIVE=1 and ensure `claude` "
        "(OAuth) + ollama(granite4.1:3b) are available. Spends real tokens."
    ),
)
def test_granite_answers_live_quiz():
    gqg = _load_game_module()
    report = gqg.play(questions=4, model=os.environ.get("GRANITE_GAME_MODEL", "haiku"))

    # The session must have actually posed questions.
    assert report.questions_seen >= 1, "Claude never emitted a numbered question"

    # Granite must answer the majority of questions with a valid in-range choice.
    assert report.in_range_rate >= 0.5, (
        f"granite entered a valid in-range answer on only "
        f"{report.in_range_count}/{report.questions_seen} questions; "
        f"see logs/granite_questions_game.json"
    )
