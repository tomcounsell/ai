"""Integration tests for the reflections MCP server tools.

Tests use the in-process tool functions directly (bypassing FastMCP transport)
since the @mcp.tool() decorator preserves the underlying callable behavior.
"""

from __future__ import annotations

import time

from mcp_servers import reflections_server as srv
from models.reflection import Reflection
from models.reflection_run import ReflectionRun


def _name(prefix="mcp-test"):
    return f"{prefix}-{int(time.time() * 1e6)}"


def _call(tool_name, **kwargs):
    """Look up the underlying callable for an mcp.tool() decorated function."""
    fn = getattr(srv, tool_name)
    # FastMCP @mcp.tool() typically replaces the function with a wrapper, but
    # the wrapper is still callable with the same signature.
    return fn(**kwargs)


# --- create / list / get -------------------------------------------------


def test_reflections_create_and_get(monkeypatch):
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
    monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
    name = _name()
    res = _call(
        "reflections_create",
        name=name,
        schedule="every:60s",
        execution_type="function",
        callable="x.y",
    )
    assert "error" not in res, res
    assert res["name"] == name
    assert isinstance(res["next_due"], float)

    got = _call("reflections_get", name=name)
    assert got.get("name") == name
    assert got.get("schedule") == "every:60s"


def test_reflections_create_bad_schedule(monkeypatch):
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
    monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
    res = _call(
        "reflections_create",
        name=_name(),
        schedule="hourly:60",
        execution_type="function",
        callable="x.y",
    )
    assert res.get("code") == "BAD_SCHEDULE"


def test_reflections_create_duplicate(monkeypatch):
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
    monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
    name = _name()
    a = _call(
        "reflections_create",
        name=name,
        schedule="every:60s",
        execution_type="function",
        callable="x.y",
    )
    assert "error" not in a
    b = _call(
        "reflections_create",
        name=name,
        schedule="every:60s",
        execution_type="function",
        callable="x.y",
    )
    assert b.get("code") == "DUPLICATE"


def test_reflections_list(monkeypatch):
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
    monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
    name = _name("list")
    _call(
        "reflections_create",
        name=name,
        schedule="every:60s",
        execution_type="function",
        callable="x.y",
    )
    res = _call("reflections_list")
    assert "reflections" in res
    names = [r["name"] for r in res["reflections"]]
    assert name in names


# --- auth: update ---------------------------------------------------------


def test_creator_can_update(monkeypatch):
    monkeypatch.setenv("AGENT_SESSION_ID", "creator-session")
    name = _name("upd")
    _call(
        "reflections_create",
        name=name,
        schedule="every:60s",
        execution_type="function",
        callable="x.y",
    )
    res = _call("reflections_update", name=name, schedule="every:5m")
    assert "error" not in res, res
    assert res["schedule"] == "every:5m"


def test_non_creator_blocked_from_update(monkeypatch):
    monkeypatch.setenv("AGENT_SESSION_ID", "alice")
    name = _name("upd-blocked")
    _call(
        "reflections_create",
        name=name,
        schedule="every:60s",
        execution_type="function",
        callable="x.y",
    )

    monkeypatch.setenv("AGENT_SESSION_ID", "bob")
    res = _call("reflections_update", name=name, schedule="every:5m")
    assert res.get("code") == "FORBIDDEN"


def test_no_env_caller_can_update(monkeypatch):
    """CLI/human caller (no env var) can update any reflection."""
    monkeypatch.setenv("AGENT_SESSION_ID", "alice")
    name = _name("upd-cli")
    _call(
        "reflections_create",
        name=name,
        schedule="every:60s",
        execution_type="function",
        callable="x.y",
    )

    monkeypatch.delenv("AGENT_SESSION_ID")
    monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
    res = _call("reflections_update", name=name, output_sink="memory:7.0")
    assert "error" not in res, res
    assert res["output_sink"] == "memory:7.0"


# --- auth: remove ---------------------------------------------------------


def test_creator_can_remove(monkeypatch):
    monkeypatch.setenv("AGENT_SESSION_ID", "creator-rm")
    name = _name("rm-creator")
    _call(
        "reflections_create",
        name=name,
        schedule="every:60s",
        execution_type="function",
        callable="x.y",
    )
    res = _call("reflections_remove", name=name)
    assert res == {"removed": name}


def test_non_creator_blocked_from_remove(monkeypatch):
    monkeypatch.setenv("AGENT_SESSION_ID", "alice")
    name = _name("rm-blocked")
    _call(
        "reflections_create",
        name=name,
        schedule="every:60s",
        execution_type="function",
        callable="x.y",
    )

    monkeypatch.setenv("AGENT_SESSION_ID", "bob")
    res = _call("reflections_remove", name=name)
    assert res.get("code") == "FORBIDDEN"


def test_no_env_caller_blocked_from_remove_without_registry_flag(monkeypatch):
    """No-env caller can update but NOT remove unless REFLECTIONS_REGISTRY_SOURCE=1."""
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
    monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
    monkeypatch.delenv("REFLECTIONS_REGISTRY_SOURCE", raising=False)

    name = _name("rm-cli")
    _call(
        "reflections_create",
        name=name,
        schedule="every:60s",
        execution_type="function",
        callable="x.y",
    )
    res = _call("reflections_remove", name=name)
    assert res.get("code") == "FORBIDDEN"


def test_no_env_caller_can_remove_with_registry_flag(monkeypatch):
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
    monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
    monkeypatch.setenv("REFLECTIONS_REGISTRY_SOURCE", "1")

    name = _name("rm-cli-registry")
    _call(
        "reflections_create",
        name=name,
        schedule="every:60s",
        execution_type="function",
        callable="x.y",
    )
    res = _call("reflections_remove", name=name)
    assert res == {"removed": name}


def test_agent_caller_cannot_mutate_registry_loaded(monkeypatch):
    """Agent caller cannot modify reflections with created_by_session_id=None."""
    # Simulate registry-loaded by creating directly with no creator
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
    monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

    name = _name("registry-loaded")
    Reflection.create(
        name=name, schedule="every:60s", execution_type="function", created_by_session_id=None
    )

    monkeypatch.setenv("AGENT_SESSION_ID", "some-agent")
    upd = _call("reflections_update", name=name, schedule="every:5m")
    assert upd.get("code") == "FORBIDDEN"
    rm = _call("reflections_remove", name=name)
    assert rm.get("code") == "FORBIDDEN"


# --- pause / resume / runs ------------------------------------------------


def test_pause_and_resume(monkeypatch):
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
    monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
    name = _name("pause")
    _call(
        "reflections_create",
        name=name,
        schedule="every:60s",
        execution_type="function",
        callable="x.y",
    )
    p = _call("reflections_pause", name=name)
    assert "paused_until" in p
    assert p["paused_until"] > time.time()

    r = _call("reflections_resume", name=name)
    assert r["resumed"] is True
    rec = Reflection.query.filter(name=name)[0]
    assert float(rec.paused_until) == 0.0


def test_runs_returns_history(monkeypatch):
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
    monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
    name = _name("runs")
    _call(
        "reflections_create",
        name=name,
        schedule="every:60s",
        execution_type="function",
        callable="x.y",
    )
    base = time.time()
    for i in range(3):
        run = ReflectionRun.get_or_create_for(name=name, timestamp=base - i * 10)
        run.status = "success"
        run.save()
    res = _call("reflections_runs", name=name)
    assert "runs" in res
    assert len(res["runs"]) == 3
    timestamps = [r["timestamp"] for r in res["runs"]]
    assert timestamps == sorted(timestamps, reverse=True)
