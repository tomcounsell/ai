"""The dashboard read side of the degraded-LLM-stack signal (#3001).

``/dashboard.json`` is served by a **separate uvicorn process**, so an
in-process degraded flag in the bridge or worker can never reach it. The
marker file on disk is the transport, exactly as ``data/last_connected`` is
for bridge health — and this is the plan's only *standing* signal, the one
that is still visible an hour after the Sentry event scrolled past. A
channel described that way cannot ship untested.

The markers here are written under the directory the autouse
``tests/unit/conftest.py`` fixture redirects to (mirrored onto
``ui.app.LLM_MARKER_DIR``), never the live ``data/`` a running bridge,
worker, and dashboard share on this machine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import ui.app
from agent.llm import compat
from ui.app import create_app


def _marker_payload(proc: str, **overrides) -> dict:
    payload = {
        "process": proc,
        "axis": "signature",
        "loader_ok": True,
        "compatible": False,
        "anthropic_version": "0.125.0",
        "pydantic_ai_version": "2.9.0",
        "exc_type": None,
        "reason": "pydantic_ai forwards temperature, which anthropic no longer accepts",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def marker_dir(monkeypatch) -> Path:
    """Point the dashboard at the same redirected directory the resolver uses."""
    monkeypatch.setattr(ui.app, "LLM_MARKER_DIR", compat._MARKER_DIR)
    return compat._MARKER_DIR


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _write_marker(directory: Path, proc: str, **overrides) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"llm-stack-degraded.{proc}"
    path.write_text(json.dumps(_marker_payload(proc, **overrides)))
    return path


def _health(client: TestClient) -> dict:
    response = client.get("/dashboard.json")
    assert response.status_code == 200
    return response.json()["health"]


def test_no_marker_renders_healthy(marker_dir, client):
    health = _health(client)
    assert health["llm_stack_degraded"] is False
    assert health["llm_stack_degraded_processes"] == []


def test_marker_renders_red_with_versions_and_process(marker_dir, client):
    _write_marker(marker_dir, "bridge")

    health = _health(client)
    assert health["llm_stack_degraded"] is True
    assert health["llm_stack_degraded_processes"] == ["bridge"]

    (detail,) = health["llm_stack_degraded_detail"]
    assert detail["process"] == "bridge"
    assert detail["anthropic_version"] == "0.125.0"
    assert detail["pydantic_ai_version"] == "2.9.0"
    assert detail["axis"] == "signature"


def test_two_markers_name_both_processes(marker_dir, client):
    _write_marker(marker_dir, "bridge")
    _write_marker(marker_dir, "worker", axis="loader", loader_ok=False)

    health = _health(client)
    assert health["llm_stack_degraded"] is True
    assert health["llm_stack_degraded_processes"] == ["bridge", "worker"]


def test_board_stays_red_while_one_marker_survives(marker_dir, client):
    """The bridge clears its own marker; the worker's keeps the board red.

    The worker defers its restart while jobs run, so it can still be raising
    ``LLMStackIncompatible`` long after the bridge came back healthy. A
    green board there is worse than a stuck-red one — nobody investigates
    green.
    """
    bridge_marker = _write_marker(marker_dir, "bridge")
    _write_marker(marker_dir, "worker")

    bridge_marker.unlink()

    health = _health(client)
    assert health["llm_stack_degraded"] is True
    assert health["llm_stack_degraded_processes"] == ["worker"]


def test_unreadable_marker_does_not_break_the_payload(marker_dir, client):
    """The fail-quiet ``OSError`` leg: a health payload must never 500."""
    marker = _write_marker(marker_dir, "bridge")
    marker.chmod(0o000)
    try:
        health = _health(client)
    finally:
        marker.chmod(0o644)

    assert health["llm_stack_degraded"] is True
    assert health["llm_stack_degraded_processes"] == ["bridge"]
    assert health["llm_stack_degraded_detail"][0]["unreadable"] is True


def test_non_object_marker_json_does_not_break_the_payload(marker_dir, client):
    """Valid JSON that isn't an object (`5`, `[1, 2]`) must not 500 either.

    ``dict.update`` raises ``TypeError`` on a non-mapping argument, which is
    a different exception class than the malformed-JSON ``ValueError`` leg
    this shares an except clause with.
    """
    marker = marker_dir / "llm-stack-degraded.bridge"
    marker.write_text("5")

    health = _health(client)

    assert health["llm_stack_degraded"] is True
    assert health["llm_stack_degraded_processes"] == ["bridge"]
    assert health["llm_stack_degraded_detail"][0]["unreadable"] is True


def test_missing_marker_directory_is_quiet(monkeypatch, tmp_path, client):
    monkeypatch.setattr(ui.app, "LLM_MARKER_DIR", tmp_path / "does-not-exist")
    health = _health(client)
    assert health["llm_stack_degraded"] is False
