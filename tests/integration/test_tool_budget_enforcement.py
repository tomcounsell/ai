"""End-to-end enforcement of the per-tool budget on BOTH hook surfaces (#1821).

Acceptance #2: an over-budget session is DENIED inline at tool dispatch —
``{"decision":"block"}`` on the SDK surface, ``exit 2`` on the CLI
(granite-PTY) surface — and the deny fires WITH NO BACKGROUND LOOP RUNNING (we
construct the over-budget state and invoke the hook directly; no health/timeout
loop is ever started). Under budget, both surfaces proceed.

Also covers the fail-open split (no-session → silent allow; infra error → allow
+ loud WARNING + ``resolution_errors`` counter), the CLI exit-2-propagation vs
check-bug fail-open (exit 2 vs exit 0, via real subprocesses), and the
deny-surfacing (``budget_tripped`` flag by default; status→``paused_budget`` +
Telegram queued once under ``TOOL_BUDGET_AUTO_PAUSE``; surfacing errors
fail-quiet).

Real Redis (integration-style), matching the liveness-hook suite.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from agent import tool_budget
from models.agent_session import AgentSession, SessionType

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _low_thresholds(monkeypatch):
    """Small deterministic cap, budget enabled, auto-pause OFF (the default)."""
    monkeypatch.setattr(tool_budget, "MAX_TOOL_CALLS_PER_SESSION", 5)
    monkeypatch.setattr(tool_budget, "SESSION_COST_CAP_USD", 5.0)
    monkeypatch.setattr(tool_budget, "TOOL_BUDGET_ENABLED", True)
    monkeypatch.setattr(tool_budget, "TOOL_BUDGET_AUTO_PAUSE", False)


@pytest.fixture
def make_session():
    created: list[AgentSession] = []

    def _make(*, calls=0, cost=0.0, chat_id="x", telegram_message_id=None):
        pk = f"test-tool-budget-{uuid.uuid4().hex[:8]}"
        kwargs = dict(
            project_key=pk,
            chat_id=chat_id,
            session_type=SessionType.ENG,
            message_text="x",
            sender_name="x",
            session_id=f"{pk}-sess",
            working_dir="/tmp",
        )
        if telegram_message_id is not None:
            kwargs["telegram_message_id"] = telegram_message_id
        s = AgentSession.create(**kwargs)
        s.tool_call_count = calls
        s.total_cost_usd = cost
        s.save()
        created.append(s)
        return s

    yield _make

    for s in created:
        try:
            s.delete()
        except Exception:
            pass


def _reset_liveness_cooldown():
    from agent.hooks import liveness_writers

    liveness_writers._reset_cooldown_for_tests()


def _run_sdk_hook(tool_name="Read"):
    from agent.hooks.pre_tool_use import pre_tool_use_hook

    return asyncio.run(
        pre_tool_use_hook(
            input_data={"tool_name": tool_name, "tool_input": {"file_path": "/etc/hosts"}},
            tool_use_id="tid",
            context=None,
        )
    )


def _reason_of(session_id: str):
    rows = AgentSession.query.filter(session_id=session_id)
    return rows[0].budget_tripped_reason if rows else None


def _status_of(session_id: str):
    rows = AgentSession.query.filter(session_id=session_id)
    return rows[0].status if rows else "<gone>"


# --------------------------------------------------------------------------- #
# SDK surface
# --------------------------------------------------------------------------- #
def test_sdk_hook_blocks_over_budget(make_session, monkeypatch):
    """Over-budget → block dict + budget_tripped flag set. No loop running."""
    s = make_session(calls=10)
    monkeypatch.setenv("AGENT_SESSION_ID", s.session_id)
    _reset_liveness_cooldown()

    result = _run_sdk_hook()

    assert result.get("decision") == "block"
    assert "budget" in result["reason"]
    # Deny-surfacing default: the race-free flag is set (no status write).
    assert _reason_of(s.session_id)
    assert _status_of(s.session_id) != "paused_budget", "AUTO_PAUSE off → status untouched"


def test_sdk_hook_allows_under_budget(make_session, monkeypatch):
    """Under budget → no block; the common path is unchanged (liveness fires)."""
    s = make_session(calls=1)
    monkeypatch.setenv("AGENT_SESSION_ID", s.session_id)
    _reset_liveness_cooldown()

    result = _run_sdk_hook()

    assert "decision" not in result
    # Liveness write still fired for the allowed tool (coordinated addition).
    refreshed = AgentSession.query.filter(session_id=s.session_id)[0]
    assert refreshed.current_tool_name == "Read"
    assert not _reason_of(s.session_id)


def test_sdk_hook_disabled_allows(make_session, monkeypatch):
    s = make_session(calls=10)
    monkeypatch.setattr(tool_budget, "TOOL_BUDGET_ENABLED", False)
    monkeypatch.setenv("AGENT_SESSION_ID", s.session_id)
    _reset_liveness_cooldown()

    assert "decision" not in _run_sdk_hook()


def test_sdk_no_session_silent_allow(monkeypatch):
    """Genuine no-session (unset AGENT_SESSION_ID) → silent allow, no counter."""
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
    monkeypatch.setenv("VALOR_PROJECT_KEY", f"test-nosess-{uuid.uuid4().hex[:6]}")
    _reset_liveness_cooldown()
    r = tool_budget._project_key_env()
    from popoto.redis_db import POPOTO_REDIS_DB

    before = POPOTO_REDIS_DB.get(f"{r}:tool-budget:resolution_errors")

    result = _run_sdk_hook()

    assert "decision" not in result  # allowed
    after = POPOTO_REDIS_DB.get(f"{r}:tool-budget:resolution_errors")
    assert before == after, "no-session must NOT bump resolution_errors"


def test_sdk_infra_error_allows_and_counts(monkeypatch):
    """Injected resolution EXCEPTION → allow + loud WARNING + resolution_errors++."""
    pk = f"test-infra-{uuid.uuid4().hex[:6]}"
    monkeypatch.setenv("VALOR_PROJECT_KEY", pk)
    monkeypatch.setenv("AGENT_SESSION_ID", "does-not-matter")

    from agent.hooks import pre_tool_use as sdk

    def _boom():
        raise RuntimeError("simulated Redis outage")

    monkeypatch.setattr(sdk, "_resolve_sdk_session", _boom)
    _reset_liveness_cooldown()

    from popoto.redis_db import POPOTO_REDIS_DB

    key = f"{pk}:tool-budget:resolution_errors"
    before = int(POPOTO_REDIS_DB.get(key) or 0)

    result = _run_sdk_hook()

    assert "decision" not in result, "infra error must fail OPEN (allow)"
    after = int(POPOTO_REDIS_DB.get(key) or 0)
    assert after == before + 1, "infra error must bump resolution_errors"


# --------------------------------------------------------------------------- #
# CLI surface — real subprocesses prove exit-code propagation
# --------------------------------------------------------------------------- #
def _write_sidecar(cli_session_id: str, agent_session_id: str) -> Path:
    d = REPO_ROOT / "data" / "sessions" / cli_session_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "agent_session.json").write_text(json.dumps({"agent_session_id": agent_session_id}))
    return d


def _run_cli_hook(cli_session_id: str, *, max_calls: str = "5"):
    """Run the CLI PreToolUse hook as a real subprocess; return the exit code.

    Point the subprocess's Popoto at the SAME per-worker test DB the in-process
    ``redis_test_db`` fixture swapped to (the fixture rebinds the client object
    at runtime; a fresh subprocess would otherwise import against db=0 and see
    no sessions → a false silent-allow).
    """
    from popoto.redis_db import POPOTO_REDIS_DB

    test_db = POPOTO_REDIS_DB.connection_pool.connection_kwargs.get("db", 0)
    env = {**os.environ}
    env["REDIS_URL"] = f"redis://127.0.0.1:6379/{test_db}"
    env["MAX_TOOL_CALLS_PER_SESSION"] = max_calls
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    payload = json.dumps(
        {"session_id": cli_session_id, "tool_name": "Read", "tool_input": {"file_path": "/x"}}
    )
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py")],
        input=payload,
        text=True,
        capture_output=True,
        env=env,
        timeout=180,
    )
    return proc


def test_cli_hook_denies_over_budget_exit_2(make_session):
    """Genuine deny → sys.exit(2) propagates through the module-level wrapper."""
    s = make_session(calls=10)
    cli_sid = f"cli-budget-{uuid.uuid4().hex[:6]}"
    import shutil

    sidecar_dir = _write_sidecar(cli_sid, s.agent_session_id)
    try:
        proc = _run_cli_hook(cli_sid, max_calls="5")
        assert proc.returncode == 2, f"expected exit 2, got {proc.returncode}: {proc.stderr}"
        assert "budget" in proc.stderr.lower()
    finally:
        shutil.rmtree(sidecar_dir, ignore_errors=True)


def test_cli_hook_allows_under_budget_exit_0(make_session):
    s = make_session(calls=1)
    cli_sid = f"cli-budget-ok-{uuid.uuid4().hex[:6]}"
    import shutil

    sidecar_dir = _write_sidecar(cli_sid, s.agent_session_id)
    try:
        proc = _run_cli_hook(cli_sid, max_calls="5")
        assert proc.returncode == 0, f"under-budget must exit 0: {proc.stderr}"
    finally:
        shutil.rmtree(sidecar_dir, ignore_errors=True)


def test_cli_hook_check_bug_fails_open_exit_0(make_session):
    """A check-internal bug (invalid MAX env → int() raises at import) fails OPEN.

    The Exception propagates out of ``main()`` → caught by the module-level
    ``except Exception`` wrapper → logged → exit 0. Contrast the genuine deny
    above, whose ``SystemExit(2)`` is NOT an ``Exception`` and DOES propagate.
    """
    s = make_session(calls=10)
    cli_sid = f"cli-budget-bug-{uuid.uuid4().hex[:6]}"
    import shutil

    sidecar_dir = _write_sidecar(cli_sid, s.agent_session_id)
    try:
        proc = _run_cli_hook(cli_sid, max_calls="not-a-number")
        assert proc.returncode == 0, f"check bug must fail OPEN (exit 0): {proc.stderr}"
    finally:
        shutil.rmtree(sidecar_dir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Deny-surfacing under TOOL_BUDGET_AUTO_PAUSE
# --------------------------------------------------------------------------- #
def test_auto_pause_transitions_and_queues_telegram_once(make_session, monkeypatch):
    """AUTO_PAUSE=1 deny → status→paused_budget + one Telegram reaction queued."""
    monkeypatch.setattr(tool_budget, "TOOL_BUDGET_AUTO_PAUSE", True)
    s = make_session(calls=10, chat_id="12345", telegram_message_id=999)

    from popoto.redis_db import POPOTO_REDIS_DB

    from agent.tool_budget import evaluate_tool_budget, record_budget_trip

    outbox_key = f"telegram:outbox:{s.session_id}"
    POPOTO_REDIS_DB.delete(outbox_key)  # plain outbox key (not Popoto-managed)

    verdict = evaluate_tool_budget(s)
    assert verdict.allow is False
    record_budget_trip(s, verdict)

    assert _status_of(s.session_id) == "paused_budget"
    assert _reason_of(s.session_id)
    assert POPOTO_REDIS_DB.llen(outbox_key) == 1, "exactly one reaction queued"

    # A second deny is deduped — no second reaction, no crash.
    record_budget_trip(s, verdict)
    assert POPOTO_REDIS_DB.llen(outbox_key) == 1


def test_surfacing_error_is_fail_quiet(make_session, monkeypatch):
    """A surfacing error NEVER crashes and NEVER flips the deny to allow."""
    monkeypatch.setattr(tool_budget, "TOOL_BUDGET_AUTO_PAUSE", True)
    s = make_session(calls=10, chat_id="12345", telegram_message_id=999)

    import models.session_lifecycle as lifecycle

    def _boom(*_a, **_k):
        raise RuntimeError("simulated transition failure")

    monkeypatch.setattr(lifecycle, "transition_status", _boom)

    from agent.tool_budget import evaluate_tool_budget, record_budget_trip

    verdict = evaluate_tool_budget(s)
    # Must not raise despite the transition blowing up.
    record_budget_trip(s, verdict)
    # The race-free flag still lands (independent best-effort step).
    assert _reason_of(s.session_id)
