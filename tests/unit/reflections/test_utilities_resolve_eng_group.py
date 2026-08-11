"""Direct unit tests for reflections/utilities.py::resolve_eng_group.

Every call site in tests/unit/reflections/test_sdlc_upvote_lanes.py
monkeypatches `resolve_eng_group` away, so its real body has no direct
coverage. These tests exercise it in isolation.
"""

from __future__ import annotations

import pytest

from reflections.utilities import resolve_eng_group

pytestmark = [pytest.mark.unit]


def test_well_formed_eng_group_returns_name_and_chat_id():
    project = {"telegram": {"groups": {"Eng: Valor": {"chat_id": -100123}}}}
    assert resolve_eng_group(project) == ("Eng: Valor", -100123)


def test_missing_telegram_key_returns_none():
    project = {}
    assert resolve_eng_group(project) is None


def test_groups_value_is_string_returns_none():
    project = {"telegram": {"groups": "not-a-dict"}}
    assert resolve_eng_group(project) is None


def test_matching_group_missing_chat_id_returns_none():
    project = {"telegram": {"groups": {"Eng: Valor": {"persona": "engineer"}}}}
    assert resolve_eng_group(project) is None


def test_matching_group_non_integer_chat_id_returns_none():
    project = {"telegram": {"groups": {"Eng: Valor": {"chat_id": "-100123"}}}}
    assert resolve_eng_group(project) is None
