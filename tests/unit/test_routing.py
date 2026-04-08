"""Unit tests for bridge.routing mention detection (config-only).

These tests cover the three-state behavior of get_valor_usernames after the
removal of the hardcoded VALOR_USERNAMES constant:

1. project=None -> empty set (test ergonomics)
2. project with empty mention_triggers -> empty set
3. project with mention_triggers -> normalized set
"""

from __future__ import annotations

import pytest

from bridge import routing
from bridge.routing import (
    get_valor_usernames,
    is_message_for_valor,
    is_message_for_others,
)


def test_get_valor_usernames_none_returns_empty_set():
    assert get_valor_usernames(None) == set()


def test_get_valor_usernames_empty_triggers_returns_empty_set(monkeypatch):
    # Force DEFAULT_MENTIONS empty so the dict.get default also yields []
    monkeypatch.setattr(routing, "DEFAULT_MENTIONS", [])
    project = {"telegram": {"mention_triggers": []}}
    assert get_valor_usernames(project) == set()


def test_get_valor_usernames_returns_normalized_triggers():
    project = {"telegram": {"mention_triggers": ["@Foo", "BAR", "valorengels"]}}
    assert get_valor_usernames(project) == {"foo", "bar", "valorengels"}


def test_get_valor_usernames_falls_back_to_default_mentions(monkeypatch):
    monkeypatch.setattr(routing, "DEFAULT_MENTIONS", ["@valor", "valor"])
    # No mention_triggers key on the project -> should use DEFAULT_MENTIONS
    project: dict = {"telegram": {}}
    assert get_valor_usernames(project) == {"valor"}


def test_is_message_for_valor_true_with_loaded_project():
    project = {"telegram": {"mention_triggers": ["@valor"]}}
    assert is_message_for_valor("hey @valor please help", project) is True


def test_is_message_for_valor_false_when_directed_elsewhere():
    project = {"telegram": {"mention_triggers": ["@valor"]}}
    assert is_message_for_valor("hey @somebody help", project) is False


def test_is_message_for_others_true_when_only_other_mentions():
    project = {"telegram": {"mention_triggers": ["@valor"]}}
    assert is_message_for_others("@bob look at this", project) is True


def test_is_message_for_others_false_when_valor_mentioned():
    project = {"telegram": {"mention_triggers": ["@valor"]}}
    assert is_message_for_others("@valor and @bob", project) is False


def test_no_legacy_valor_usernames_constant():
    """The hardcoded VALOR_USERNAMES constant must be gone."""
    assert not hasattr(routing, "VALOR_USERNAMES")
