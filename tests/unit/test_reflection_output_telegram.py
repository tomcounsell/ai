"""Unit tests for telegram chat resolution + outbox push in agent/reflection_output.py."""

from __future__ import annotations

import json
import logging
import time

import pytest

from agent.reflection_output import _resolve_telegram_chat, deliver
from models.reflection import Reflection
from models.reflection_run import ReflectionRun


@pytest.fixture
def projects_fixture(tmp_path, monkeypatch):
    cfg = {
        "projects": {
            "valor": {
                "telegram": {
                    "groups": {
                        "Dev: Valor": {"chat_id": -1001111111111},
                    }
                }
            }
        },
        "dms": {
            "whitelist": [
                {"name": "tom", "id": 5555},
            ]
        },
    }
    p = tmp_path / "projects.json"
    p.write_text(json.dumps(cfg))
    monkeypatch.setenv("PROJECTS_CONFIG_PATH", str(p))
    # Force the loader to bypass bridge.routing.load_config
    import agent.reflection_output as ro

    monkeypatch.setattr(ro, "_load_projects_config", lambda: cfg)
    return cfg


def test_resolve_literal_int():
    assert _resolve_telegram_chat("12345") == 12345
    assert _resolve_telegram_chat("-1001234") == -1001234


def test_resolve_known_group(projects_fixture):
    assert _resolve_telegram_chat("Dev: Valor") == -1001111111111


def test_resolve_known_dm(projects_fixture):
    assert _resolve_telegram_chat("tom") == 5555


def test_resolve_unknown_returns_none(projects_fixture):
    assert _resolve_telegram_chat("Unknown Group") is None


def test_resolve_empty_returns_none():
    assert _resolve_telegram_chat("") is None


def test_telegram_unknown_chat_records_delivery_error(projects_fixture, caplog, monkeypatch):
    name = f"tg-unknown-{int(time.time() * 1e6)}"
    r = Reflection.create(name=name, output_sink="telegram:Nonexistent Chat")
    run = ReflectionRun.get_or_create_for(name=name, timestamp=time.time())

    with caplog.at_level(logging.WARNING, logger="agent.reflection_output"):
        deliver(r, run, "hello")

    fetched = list(ReflectionRun.query.filter(name=name))
    assert len(fetched) == 1
    err = fetched[0].delivery_error or ""
    assert "telegram_resolve_failed" in err
    # Run is NOT marked failed (caller sets status; we don't change it)
    # The status defaults to "success" when get_or_create_for is called.


def test_telegram_outbox_session_id_prefix(projects_fixture, monkeypatch):
    name = f"tg-outbox-{int(time.time() * 1e6)}"
    r = Reflection.create(name=name, output_sink="telegram:12345")
    run = ReflectionRun.get_or_create_for(name=name, timestamp=time.time())

    pushed: list = []

    import agent.reflection_output as ro

    def fake_push(payload):
        pushed.append(payload)

    monkeypatch.setattr(ro, "_push_outbox", fake_push)
    deliver(r, run, "ping")

    assert len(pushed) == 1
    payload = pushed[0]
    assert payload["chat_id"] == 12345
    assert payload["session_id"].startswith("reflection:")
    assert name in payload["session_id"]
