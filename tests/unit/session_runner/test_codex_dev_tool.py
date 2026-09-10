"""Unit tests for the session-scoped Codex Dev MCP lane (plan #2001 Task 3).

Covers runtime capability gating, session lookup, dev-lane lease,
write-or-kill thread persistence, fence-token resume check,
resume/count guard with increment-after-live-spawn plus
refund-on-spawn-failure accounting, the Task 3 turn-evidence schema of
record (``log_codex_turn`` ok/degraded), error rendering, and the
PM-visible attribution line. The Codex subprocess is never spawned: the
handler imports its collaborators inside ``_run_turn``, so tests patch
the source modules (``agent.codex_dev_lease``,
``agent.session_runner.harness.codex``, ``agent.codex_turn_log``,
``config.settings``, ``models.agent_session``) while the handler's own
gating/persistence/accounting logic runs for real. ``@mcp.tool()``
returns the raw function, so the handler is invoked by direct sync call.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from agent.session_runner.harness import events as harness_events
from agent.session_runner.harness.base import TurnEvent, TurnResult

THREAD_A = "01a08c06-6d38-7611-9144-c64562b4e27c"


def _session(**overrides):
    saved = {}

    class _S:
        def __init__(self):
            self.id = "agent-id-1"
            self.session_id = "sess-1"
            self.session_type = "eng"
            self.dev_harness = "codex"
            self.exec_harness = "claude"
            self.codex_thread_id = None
            self.codex_version = None
            self.codex_turn_count = 0
            self.dev_lane_fence = "fence-1"
            self.working_dir = "/tmp"
            for k, v in overrides.items():
                setattr(self, k, v)

        def save(self):
            saved["saves"] = saved.get("saves", 0) + 1
            saved["codex_thread_id"] = self.codex_thread_id
            saved["codex_version"] = self.codex_version
            saved["codex_turn_count"] = self.codex_turn_count
            saved["dev_harness"] = self.dev_harness

    return _S(), saved


class _LeaseCtx:
    """Sync context manager standing in for the Redis dev-lane lease."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_settings(monkeypatch, cfg):
    """Patch the real ``config.settings`` module's ``settings`` object.

    NOTE: ``config/__init__.py`` re-exports the ``settings`` instance, so
    ``import config.settings`` resolves to the instance, not the module.
    ``importlib.import_module`` returns the real module from ``sys.modules``,
    which is what the handler's ``from config.settings import settings``
    reads at call time.
    """
    import importlib

    module = importlib.import_module("config.settings")
    monkeypatch.setattr(module, "settings", SimpleNamespace(codex=cfg))


def _codex_cfg():
    return SimpleNamespace(sandbox="workspace-write", turn_timeout_s=5.0, max_resumed_turns=10)


def _handler_env(monkeypatch, sessions):
    """Point the handler's session lookup at fakes; return the server module."""
    import mcp_servers.codex_dev_server as server

    monkeypatch.setenv("AGENT_SESSION_ID", "agent-id-1")
    calls = {"lookups": 0}

    def _get_by_id(sid):
        calls["lookups"] += 1
        if isinstance(sessions, list):
            return sessions[min(calls["lookups"] - 1, len(sessions) - 1)]
        return sessions

    monkeypatch.setattr("models.agent_session.AgentSession.get_by_id", staticmethod(_get_by_id))
    return server, calls


def _patch_lane(monkeypatch, result=None, events=(), raise_before_spawn=None):
    """Patch lease + adapter + settings + evidence log for one handler call."""
    import agent.codex_dev_lease as lease_mod
    import agent.codex_turn_log as log_mod
    import agent.session_runner.harness.codex as codex_mod
    import agent.session_telemetry as telemetry_mod

    calls = {}

    class _FakeAdapter:
        def __init__(self, **kw):
            calls["adapter_kw"] = kw

        async def run_turn(self, request, on_event=None):
            calls["request"] = request
            for evt in events:
                on_event(evt)
            if raise_before_spawn is not None:
                raise raise_before_spawn
            return result

    monkeypatch.setattr(lease_mod, "acquire_dev_lease", lambda sid, timeout_s=30.0: _LeaseCtx())
    monkeypatch.setattr(codex_mod, "CodexHarnessAdapter", _FakeAdapter)
    monkeypatch.setattr(
        codex_mod, "preflight_codex", lambda sandbox, worktree, api_key=None: ("0.154.0", None)
    )
    logged = []
    monkeypatch.setattr(
        log_mod, "log_codex_turn", lambda path, record: logged.append((path, record)) or "ok"
    )
    monkeypatch.setattr(log_mod, "lane_path_for", lambda sid: Path("/tmp/lane.jsonl"))
    _patch_settings(monkeypatch, _codex_cfg())
    telemetered = []
    monkeypatch.setattr(
        telemetry_mod,
        "record_codex_dev_turn",
        lambda sid, **kw: telemetered.append((sid, kw)),
    )
    calls["telemetry"] = telemetered
    return calls, logged


def _call(monkeypatch, sessions, instruction="implement the feature", **lane_kw):
    server, _lookups = _handler_env(monkeypatch, sessions)
    calls, logged = _patch_lane(monkeypatch, **lane_kw)
    return server.codex_dev_run(instruction), calls, logged


def _ok_result(**overrides):
    base = {
        "resume_handle": THREAD_A,
        "final_text": "did the thing",
        "structured_output": {"report": "did the thing", "complete": True},
        "events": [],
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "returncode": 0,
        "result_event_fired": True,
        "error_detail": None,
    }
    base.update(overrides)
    return TurnResult(**base)


def _started_events():
    return [TurnEvent(type=harness_events.SESSION_STARTED, data={"handle": THREAD_A})]


# --- capability gating -------------------------------------------------------


def test_missing_agent_session_id_refuses(monkeypatch):
    import mcp_servers.codex_dev_server as server

    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
    out = server.codex_dev_run("do work")
    assert out["ok"] is False and "AGENT_SESSION_ID" in out["error"]


def test_empty_instruction_refuses_before_spawn(monkeypatch):
    session, _ = _session()
    server, _lookups = _handler_env(monkeypatch, session)
    out = server.codex_dev_run("   ")
    assert out["ok"] is False and "Empty" in out["error"]


def test_non_eng_session_refused(monkeypatch):
    session, _ = _session(session_type="teammate")
    server, _lookups = _handler_env(monkeypatch, session)
    out = server.codex_dev_run("work")
    assert out["ok"] is False and "eng-only" in out["error"]


def test_unflagged_session_refused(monkeypatch):
    session, _ = _session(dev_harness=None)
    server, _lookups = _handler_env(monkeypatch, session)
    out = server.codex_dev_run("work")
    assert out["ok"] is False and "Agent(dev)" in out["error"]


def test_non_claude_exec_harness_refused(monkeypatch):
    session, _ = _session(exec_harness="codex")
    server, _lookups = _handler_env(monkeypatch, session)
    out = server.codex_dev_run("work")
    assert out["ok"] is False and "exec_harness" in out["error"]


def test_busy_lane_returns_busy_error(monkeypatch):
    import agent.codex_dev_lease as lease_mod

    session, _ = _session()
    server, _lookups = _handler_env(monkeypatch, session)

    def _busy(sid, timeout_s=30.0):
        raise lease_mod.DevLaneBusy("dev lane busy for agent-id-1")

    monkeypatch.setattr(lease_mod, "acquire_dev_lease", _busy)
    out = server.codex_dev_run("work")
    assert out["ok"] is False and "busy" in out["error"].lower()


# --- success path ------------------------------------------------------------


def test_success_persists_thread_and_returns_attributed_report(monkeypatch):
    session, saved = _session()
    out, calls, logged = _call(monkeypatch, session, result=_ok_result(), events=_started_events())
    assert out["ok"] is True
    assert saved["codex_thread_id"] == THREAD_A
    assert saved["codex_turn_count"] == 1
    assert saved["codex_version"] == "0.154.0"
    assert "[dev harness=codex" in out["report"]
    assert "turns=1" in out["report"]
    assert out["thread_id"] == THREAD_A and out["turn_count"] == 1
    assert out["complete"] is True
    # Evidence: exactly one ok line with the schema-of-record fields.
    assert len(logged) == 1
    _path, record = logged[0]
    assert set(record) == {"thread_id", "turn_count", "outcome", "usage", "wall_clock_ms"}
    assert record["outcome"] == "ok" and record["thread_id"] == THREAD_A
    # Prompt traveled on the request message (adapter puts it on stdin).
    assert calls["request"].message == "implement the feature"
    # Task 4b telemetry: one codex_dev_turn record with usage totals only.
    # (The "harness": "codex" dimension is added inside the real
    # record_codex_dev_turn — pinned by TestRecordCodexDevTurn — so the
    # handler stub here carries only the call kwargs.)
    assert len(calls["telemetry"]) == 1
    _sid, event = calls["telemetry"][0]
    assert _sid == "sess-1"
    assert event["outcome"] == "ok" and event["turn_count"] == 1
    assert event["usage"] == {"input_tokens": 10, "output_tokens": 5}
    assert event["model_version"] == "0.154.0"
    assert "instruction" not in event and "prompt" not in event


def test_resume_reuses_persisted_thread(monkeypatch):
    session, saved = _session(codex_thread_id=THREAD_A, codex_turn_count=2)
    out, calls, _logged = _call(monkeypatch, session, result=_ok_result())
    assert out["ok"] is True
    assert calls["request"].prior_uuid == THREAD_A
    assert out["turn_count"] == 2  # no new thread.started: count untouched
    assert saved.get("saves", 0) == 0  # nothing changed: no write needed


def test_max_turn_guard_stops_with_thread_preserved(monkeypatch):
    session, _ = _session(codex_thread_id=THREAD_A, codex_turn_count=10)
    out, calls, logged = _call(monkeypatch, session, result=_ok_result())
    assert out["ok"] is False and "resume bound" in out["error"]
    assert out["thread_id"] == THREAD_A
    assert "request" not in calls  # adapter never ran
    assert logged[0][1]["outcome"] == "guard-exhausted"


def test_preflight_failure_preserves_count(monkeypatch):
    import agent.session_runner.harness.codex as codex_mod

    session, saved = _session(codex_turn_count=3)
    server, _lookups = _handler_env(monkeypatch, session)
    calls, logged = _patch_lane(monkeypatch, result=_ok_result())
    monkeypatch.setattr(
        codex_mod,
        "preflight_codex",
        lambda sandbox, worktree, api_key=None: (None, "No Codex auth."),
    )
    out = server.codex_dev_run("work")
    assert out["ok"] is False and "No Codex auth" in out["error"]
    assert saved["codex_turn_count"] == 3  # failed spawn never burns a turn
    assert logged[0][1]["outcome"] == "preflight-failed"


def test_spawn_failure_refunds_count(monkeypatch):
    session, saved = _session(codex_turn_count=4)
    # NOTE: OSError, not RuntimeError — RuntimeError is the handler's
    # reserved write-or-kill persist-failure channel; a pre-spawn failure
    # surfaces as OSError from create_subprocess_exec.
    out, _calls, logged = _call(
        monkeypatch, session, result=None, raise_before_spawn=OSError("spawn exploded")
    )
    assert out["ok"] is False
    assert saved["codex_turn_count"] == 4
    assert logged[0][1]["outcome"] == "spawn-failed"


def test_native_failure_renders_typed_error(monkeypatch):
    session, _ = _session()
    out, _calls, logged = _call(
        monkeypatch,
        session,
        result=_ok_result(error_detail="Codex-native failure: bad schema"),
        events=_started_events(),
    )
    assert out["ok"] is False and "Codex-native failure" in out["error"]
    assert logged[0][1]["outcome"] == "native-failure"


def test_persist_failure_tree_kill_on_save_error(monkeypatch):
    import agent.codex_dev_lease as lease_mod
    import agent.codex_turn_log as log_mod
    import agent.session_runner.harness.codex as codex_mod

    session, _ = _session()

    def _boom_save():
        raise RuntimeError("redis down")

    session.save = _boom_save
    server, _lookups = _handler_env(monkeypatch, session)
    monkeypatch.setattr(lease_mod, "acquire_dev_lease", lambda sid, timeout_s=30.0: _LeaseCtx())

    killed = []

    class _KillingAdapter:
        def __init__(self, **kw):
            pass

        async def run_turn(self, request, on_event=None):
            # turn.spawned precedes session.started, mirroring the real adapter.
            on_event(TurnEvent(type=harness_events.TURN_SPAWNED, data={"pid": 99999}))
            on_event(TurnEvent(type=harness_events.SESSION_STARTED, data={"handle": THREAD_A}))
            return _ok_result()

    monkeypatch.setattr(codex_mod, "CodexHarnessAdapter", _KillingAdapter)
    monkeypatch.setattr(
        codex_mod, "preflight_codex", lambda sandbox, worktree, api_key=None: ("0.154.0", None)
    )
    monkeypatch.setattr(codex_mod, "kill_codex_tree", lambda pid: killed.append(pid) or [])
    monkeypatch.setattr(log_mod, "log_codex_turn", lambda p, r: "ok")
    monkeypatch.setattr(log_mod, "lane_path_for", lambda sid: Path("/tmp/lane.jsonl"))
    _patch_settings(monkeypatch, _codex_cfg())
    import agent.session_telemetry as telemetry_mod

    monkeypatch.setattr(telemetry_mod, "record_codex_dev_turn", lambda sid, **kw: None)
    out = server.codex_dev_run("work")
    assert out["ok"] is False and "persistence failed" in out["error"]
    assert killed == [99999]  # write-or-kill reaped the tree
    assert "orphan" in out["error"]


def test_fence_mismatch_discards_result(monkeypatch):
    session, _ = _session()
    other, _ = _session(dev_lane_fence="fence-2")
    # Lookups in order: initial, post-lease re-read, fence check.
    server, _lookups = _handler_env(monkeypatch, [session, session, other])
    calls, logged = _patch_lane(monkeypatch, result=_ok_result(), events=_started_events())
    out = server.codex_dev_run("work")
    assert out["ok"] is False and "fence mismatch" in out["error"].lower()


def test_degraded_evidence_log_warns_but_returns_result(monkeypatch):
    import agent.codex_turn_log as log_mod

    session, _ = _session()
    server, _lookups = _handler_env(monkeypatch, session)
    _calls, _logged = _patch_lane(monkeypatch, result=_ok_result(), events=_started_events())
    monkeypatch.setattr(log_mod, "log_codex_turn", lambda p, r: "degraded")
    out = server.codex_dev_run("work")
    assert out["ok"] is True
    assert "codex_turn_log_failed" in out["report"]


# --- log_codex_turn schema of record ------------------------------------------


def test_log_codex_turn_writes_exact_schema(tmp_path):
    from agent.codex_turn_log import log_codex_turn

    lane = tmp_path / "sess.jsonl"
    status = log_codex_turn(
        lane,
        {
            "thread_id": THREAD_A,
            "turn_count": 2,
            "outcome": "ok",
            "usage": {"input_tokens": 1},
            "wall_clock_ms": 120,
            "prompt": "must be dropped",
            "stderr": "must be dropped",
        },
    )
    assert status == "ok"
    line = json.loads(lane.read_text().splitlines()[0])
    assert set(line) == {"thread_id", "turn_count", "outcome", "usage", "wall_clock_ms"}
    assert "must be dropped" not in lane.read_text()


def test_log_codex_turn_degraded_on_oserror(tmp_path, caplog):
    from unittest.mock import patch

    from agent.codex_turn_log import log_codex_turn

    lane = tmp_path / "nope" / "sess.jsonl"
    with patch("os.open", side_effect=OSError("disk full")):
        status = log_codex_turn(
            lane,
            {
                "thread_id": THREAD_A,
                "turn_count": 1,
                "outcome": "ok",
                "usage": {},
                "wall_clock_ms": 1,
            },
        )
    assert status == "degraded"
    assert "codex_turn_log_failed" in caplog.text


def test_lane_path_for_is_session_scoped():
    from agent.codex_turn_log import lane_path_for

    assert str(lane_path_for("abc")) != str(lane_path_for("def"))
    assert "abc" in str(lane_path_for("abc"))
