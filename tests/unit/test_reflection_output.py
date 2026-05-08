"""Unit tests for sink-kind dispatch in agent/reflection_output.py."""

from __future__ import annotations

import logging
import time

from agent.reflection_output import deliver
from models.reflection import Reflection
from models.reflection_run import ReflectionRun


def _create_pair(sink: str):
    name = f"sink-{int(time.time() * 1e6)}"
    r = Reflection.create(name=name, output_sink=sink)
    run = ReflectionRun.get_or_create_for(name=name, timestamp=time.time())
    return r, run


def test_log_only_logs(caplog):
    r, run = _create_pair("log_only")
    with caplog.at_level(logging.INFO, logger="agent.reflection_output"):
        deliver(r, run, "hello")
    assert any("hello" in rec.message or "hello" in str(rec.args) for rec in caplog.records)


def test_empty_sink_falls_back_to_log_only(caplog):
    r, run = _create_pair("")
    with caplog.at_level(logging.INFO, logger="agent.reflection_output"):
        deliver(r, run, "hi")
    # Should not warn; should log
    assert not any(rec.levelno >= logging.WARNING for rec in caplog.records)


def test_dashboard_only_writes_output_summary():
    r, run = _create_pair("dashboard_only")
    deliver(r, run, "summary text" * 200)  # very long
    fetched = list(ReflectionRun.query.filter(name=r.name))
    assert len(fetched) == 1
    summary = fetched[0].output_summary or ""
    assert summary.startswith("summary text")
    assert len(summary) <= 500


def test_unknown_sink_warns_and_falls_back(caplog):
    r, run = _create_pair("zzz:weird")
    with caplog.at_level(logging.WARNING, logger="agent.reflection_output"):
        deliver(r, run, "x")
    assert any("unknown output_sink" in rec.message for rec in caplog.records)


def test_memory_sink_default_importance(monkeypatch):
    captured = []

    class FakeMemory:
        @classmethod
        def safe_save(cls, **kwargs):
            captured.append(kwargs)
            return None

    import models.memory as memory_module

    monkeypatch.setattr(memory_module, "Memory", FakeMemory)
    r, run = _create_pair("memory:")
    deliver(r, run, "watch this")
    assert len(captured) == 1
    assert captured[0]["importance"] == 5.0


def test_memory_sink_explicit_importance(monkeypatch):
    captured = []

    class FakeMemory:
        @classmethod
        def safe_save(cls, **kwargs):
            captured.append(kwargs)
            return None

    import models.memory as memory_module

    monkeypatch.setattr(memory_module, "Memory", FakeMemory)
    r, run = _create_pair("memory:7.0")
    deliver(r, run, "important!")
    assert len(captured) == 1
    assert captured[0]["importance"] == 7.0


def test_memory_sink_invalid_importance_defaults(monkeypatch, caplog):
    captured = []

    class FakeMemory:
        @classmethod
        def safe_save(cls, **kwargs):
            captured.append(kwargs)
            return None

    import models.memory as memory_module

    monkeypatch.setattr(memory_module, "Memory", FakeMemory)
    r, run = _create_pair("memory:abc")
    with caplog.at_level(logging.WARNING, logger="agent.reflection_output"):
        deliver(r, run, "garbage importance")
    assert len(captured) == 1
    assert captured[0]["importance"] == 5.0
