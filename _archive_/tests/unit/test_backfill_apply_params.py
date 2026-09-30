"""Unit tests for the params-driven apply activation added to
reflections/memory/memory_embedding_backfill.py (issue #2203, Task 3).

Covers:
- params={"apply": True} engages apply mode when MEMORY_EMBEDDING_BACKFILL_APPLY
  is unset, and a healthy provider re-embeds a seeded vectorless record.
- An explicitly-set env var (true or false) overrides params in either
  direction (env-as-kill-switch precedence), mirroring
  reflections/memory/memory_decay_prune.py's `_resolve_tier_apply`.
- dry-run (no params, no env) saves nothing.
- Apply mode with an unavailable provider skips re-embeds (no re-save storm).

Follows the FakeMemory fixture pattern from test_decay_prune_apply_params.py.
Memory.query.all() is patched; the reflection only reads `embedding`/
`superseded_by` and calls `save(update_fields=["embedding"])`.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


class FakeMemory:
    """Minimal stand-in for a Memory record (the reflection reads embedding/
    superseded_by and calls save(update_fields=["embedding"]))."""

    def __init__(
        self,
        *,
        memory_id: str,
        embedding=None,
        superseded_by=None,
    ):
        self.memory_id = memory_id
        self.embedding = embedding
        self.superseded_by = superseded_by
        self.save_calls: list[list[str] | None] = []

    def save(self, update_fields=None):
        self.save_calls.append(update_fields)
        if update_fields == ["embedding"] and self.embedding is None:
            # Simulate a successful re-embed: a real embed call would populate
            # a vector. Tests that want a "still vectorless after save" case
            # (provider failure mid-save) construct their own scenario.
            self.embedding = [0.1, 0.2, 0.3]


def _run_with(memories, env, params=None, provider_available=True):
    """Run the reflection with Memory.query.all() patched to return `memories`."""
    from reflections.memory import memory_embedding_backfill

    fake_memory_cls = MagicMock()
    fake_memory_cls.query.all.return_value = memories

    with (
        patch.dict("os.environ", env, clear=False),
        patch("models.memory.Memory", fake_memory_cls),
        patch(
            "agent.embedding_provider.configure_embedding_provider",
            return_value=(object() if provider_available else None),
        ),
    ):
        return asyncio.run(memory_embedding_backfill.run(params=params))


def _clear_env(monkeypatch):
    monkeypatch.delenv("MEMORY_EMBEDDING_BACKFILL_APPLY", raising=False)


# --- params -> apply engages re-embed --------------------------------------


def test_params_apply_true_reembeds_vectorless_record_with_healthy_provider(monkeypatch):
    """params={"apply": True} with no env set + healthy provider re-embeds."""
    _clear_env(monkeypatch)
    m = FakeMemory(memory_id="b1", embedding=None)

    result = _run_with([m], {}, params={"apply": True}, provider_available=True)

    assert m.save_calls == [["embedding"]]
    assert m.embedding is not None
    assert "APPLIED" in result["summary"]
    assert result["status"] == "ok"


def test_params_apply_false_or_absent_stays_dry_run(monkeypatch):
    """No params, no env -> dry-run, nothing saved."""
    _clear_env(monkeypatch)
    m = FakeMemory(memory_id="b2", embedding=None)

    result = _run_with([m], {}, params=None, provider_available=True)

    assert m.save_calls == []
    assert m.embedding is None
    assert "DRY RUN" in result["summary"]


# --- env-as-kill-switch: overrides params in both directions --------------


def test_env_true_forces_apply_even_when_params_apply_false(monkeypatch):
    """Explicit env=true wins over params={"apply": False}."""
    monkeypatch.delenv("MEMORY_EMBEDDING_BACKFILL_APPLY", raising=False)
    m = FakeMemory(memory_id="b3", embedding=None)

    result = _run_with(
        [m],
        {"MEMORY_EMBEDDING_BACKFILL_APPLY": "true"},
        params={"apply": False},
        provider_available=True,
    )

    assert m.save_calls == [["embedding"]]
    assert "APPLIED" in result["summary"]


def test_env_false_forces_dry_run_even_when_params_apply_true(monkeypatch):
    """Explicit env=false wins over params={"apply": True} -- the emergency brake."""
    m = FakeMemory(memory_id="b4", embedding=None)

    result = _run_with(
        [m],
        {"MEMORY_EMBEDDING_BACKFILL_APPLY": "false"},
        params={"apply": True},
        provider_available=True,
    )

    assert m.save_calls == []
    assert m.embedding is None
    assert "DRY RUN" in result["summary"]


# --- apply mode with unavailable provider: no re-save storm ---------------


def test_apply_mode_skips_reembed_when_provider_unavailable(monkeypatch):
    _clear_env(monkeypatch)
    m = FakeMemory(memory_id="b5", embedding=None)

    result = _run_with([m], {}, params={"apply": True}, provider_available=False)

    assert m.save_calls == []
    assert m.embedding is None
    assert result["status"] == "ok"
    assert (
        "provider unavailable" in result["summary"].lower()
        or "skipped" in " ".join(result["findings"]).lower()
    )


# --- superseded records are never touched ----------------------------------


def test_superseded_record_never_reembedded(monkeypatch):
    _clear_env(monkeypatch)
    m = FakeMemory(memory_id="b6", embedding=None, superseded_by="some-tombstone")

    result = _run_with([m], {}, params={"apply": True}, provider_available=True)

    assert m.save_calls == []
    assert m.embedding is None
    assert result["status"] == "ok"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
