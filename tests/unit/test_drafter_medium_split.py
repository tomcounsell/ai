"""Tests for the medium-aware drafter split (issue #1268).

The drafter system prompt is now composed as
``BASE_DRAFTER_PROMPT + MEDIUM_RULES[medium]``. The "telegram" cell must
reproduce today's `DRAFTER_SYSTEM_PROMPT` byte-for-byte; "email" is a stub
that defaults to the same text until a follow-up plan diverges them.
"""

from __future__ import annotations

import pytest

from bridge.message_drafter import (
    BASE_DRAFTER_PROMPT,
    DRAFTER_SYSTEM_PROMPT,
    MEDIUM_RULES,
    _compose_drafter_prompt,
)


def test_telegram_cell_is_byte_identical_to_legacy_constant():
    """`BASE + MEDIUM_RULES["telegram"]` must equal the legacy constant."""
    assert BASE_DRAFTER_PROMPT + MEDIUM_RULES["telegram"] == DRAFTER_SYSTEM_PROMPT


def test_compose_default_medium_is_telegram():
    """`_compose_drafter_prompt()` with no arg defaults to telegram cell."""
    assert _compose_drafter_prompt() == DRAFTER_SYSTEM_PROMPT
    assert _compose_drafter_prompt("telegram") == DRAFTER_SYSTEM_PROMPT


def test_compose_email_stub_matches_telegram_today():
    """Today the email stub is identical to telegram. Once a follow-up plan
    diverges them, this test should be updated, not deleted."""
    assert _compose_drafter_prompt("email") == DRAFTER_SYSTEM_PROMPT


def test_compose_unknown_medium_falls_back_to_telegram():
    """Typos / unknown mediums must not produce an empty prompt."""
    assert _compose_drafter_prompt("not-a-medium") == DRAFTER_SYSTEM_PROMPT


@pytest.mark.parametrize("medium", ["telegram", "email"])
def test_every_known_medium_produces_nonempty_prompt(medium):
    prompt = _compose_drafter_prompt(medium)
    assert prompt
    assert len(prompt) > 100
