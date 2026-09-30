"""Session-scoped Codex Dev MCP server (plan #2001, Phase 3).

Stateless stdio FastMCP server exposing ONE tool, ``codex_dev_run``, to
the Claude PM of a flagged eng session ONLY. It is never registered
globally (no ``.mcp.json`` / ``~/.claude.json`` entry): the runner
generates a session-local MCP config carrying this server plus
``AGENT_SESSION_ID`` in its environment, and only for
``dev_harness=codex`` eng sessions.

Per-call contract (runtime authorization is mandatory even though the
tool is hidden from unflagged sessions):

1. Resolve ``AGENT_SESSION_ID`` from the request environment.
2. Fetch the session; enforce ``session_type=eng`` and persisted
   ``dev_harness=codex``. Anything else is a typed refusal, never a
   spawn.
3. Reject empty/whitespace-only instructions before spawning.
4. Acquire the session's dev-lane lease (bounded wait, ``DevLaneBusy``
   maps to a busy error, never a parallel resume).
5. Re-read persisted Codex context AFTER acquisition (thread id, turn
   count, fence token); enforce the resume bound and the fence match.
6. Run one adapter turn (first or ``exec resume``). The prompt travels on
   stdin; the child env is the explicit allowlist plus single-use
   ``CODEX_API_KEY``.
7. Write-or-kill persistence: the ``session.started`` callback persists
   thread id + version + count synchronously; a failed save kills the
   full Codex tree (``kill_codex_tree``) and returns a typed error rather
   than orphaning a thread with no persisted handle.
8. ``codex_turn_count`` increments only after ``create_subprocess_exec``
   returns a live child; every spawn-failure branch re-saves the
   unchanged count under the same lease, so failed spawns never burn a
   resumed turn.
9. Every executed turn appends one ``log_codex_turn`` evidence line
   (Task 3 is the schema of record); ``"degraded"`` returns are surfaced
   in the report, never silent.

Transport: stdio. The server holds no session state between calls.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

# Project root must be importable when spawned as a stdio MCP server from a
# Claude Code subprocess. Same defensive pattern as memory_server.py.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("codex_dev")


def _refusal(message: str) -> dict:
    """A typed tool refusal — the PM sees actionable text, never a traceback."""
    return {"ok": False, "error": message, "report": None}


@mcp.tool()
def codex_dev_run(instruction: str) -> dict:
    """Run one Codex dev turn and return the developer report.

    Args:
        instruction: The literal developer instruction (non-empty). Sent on
            Codex stdin, never interpolated into argv.

    Returns:
        Dict with ``ok``, ``report`` (the Codex developer report plus a
        harness/model-version/turns/usage attribution line), ``complete``,
        ``thread_id``, ``turn_count``, and ``error`` (None on success).
        Every failure is a typed dict — never a protocol error.
    """
    started_wall = time.monotonic()
    return _run_turn(instruction, started_wall)


def _run_turn(instruction: str, started_wall: float) -> dict:
    import logging

    logger = logging.getLogger(__name__)

    if not instruction or not instruction.strip():
        return _refusal(
            "Empty developer instruction — Codex was not spawned. "
            "Provide a specific, actionable instruction."
        )

    session_id = os.environ.get("AGENT_SESSION_ID")
    if not session_id:
        return _refusal(
            "Missing AGENT_SESSION_ID in the tool environment — refusing. "
            "The Codex dev tool is session-scoped and cannot run without it."
        )

    try:
        from config.enums import SessionType
        from models.agent_session import AgentSession
    except Exception as exc:  # noqa: BLE001
        return _refusal(f"Codex dev tool unavailable (imports failed: {exc}).")

    try:
        session = AgentSession.get_by_id(session_id)
    except Exception as exc:  # noqa: BLE001
        return _refusal(f"Codex dev tool session lookup failed: {exc}.")
    if session is None:
        return _refusal(f"No session found for AGENT_SESSION_ID={session_id!r} — refusing.")
    if session.session_type != SessionType.ENG:
        return _refusal(
            f"Codex dev is eng-only (this session is {session.session_type!r}) — refusing."
        )
    if getattr(session, "dev_harness", None) != "codex":
        return _refusal(
            "This session is not flagged for the Codex dev lane "
            "(dev_harness is not 'codex') — refusing. "
            "Use Agent(dev) or create the session with --dev-harness codex."
        )
    # Ownership split with exec_harness: the top-level selector stays
    # fixed to claude on every flagged eng row.
    if getattr(session, "exec_harness", None) not in (None, "claude"):
        return _refusal(
            f"Refusing: exec_harness is {getattr(session, 'exec_harness')!r} "
            "on a codex dev-lane row (must stay 'claude')."
        )

    try:
        from agent.codex_dev_lease import DevLaneBusy, acquire_dev_lease
        from agent.codex_turn_log import lane_path_for, log_codex_turn
        from agent.session_runner.harness.base import TurnRequest
        from agent.session_runner.harness.codex import (
            CodexHarnessAdapter,
            kill_codex_tree,
            preflight_codex,
        )
        from agent.session_telemetry import record_codex_dev_turn
        from config.settings import settings
    except Exception as exc:  # noqa: BLE001
        return _refusal(f"Codex dev tool unavailable (imports failed: {exc}).")

    codex_cfg = getattr(settings, "codex", None)
    sandbox = getattr(codex_cfg, "sandbox", "workspace-write") or "workspace-write"
    turn_timeout_s = float(getattr(codex_cfg, "turn_timeout_s", 600.0) or 600.0)
    max_resumed = int(getattr(codex_cfg, "max_resumed_turns", 10) or 10)
    # Preflight version for telemetry attribution; bound before any
    # _log_evidence call (the guard-exhausted path fires pre-preflight).
    version: str | None = None

    try:
        lease = acquire_dev_lease(str(session.id), timeout_s=30.0)
    except DevLaneBusy as exc:
        return _refusal(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _refusal(f"Dev-lane lease acquisition failed: {exc}.")

    def _log_evidence(outcome: str, tid, count, use, wall_ms) -> str:
        try:
            status = log_codex_turn(
                lane_path_for(str(session.session_id or session.id)),
                {
                    "thread_id": tid,
                    "turn_count": count,
                    "outcome": outcome,
                    "usage": use,
                    "wall_clock_ms": wall_ms,
                },
            )
        except Exception:  # noqa: BLE001 -- evidence must never mask outcome
            logger.error(
                "codex_turn_log_failed lane=%s",
                lane_path_for(str(session.session_id or session.id)),
            )
            status = "degraded"
        # Task 4b harness-dimensioned telemetry: the machine-readable twin
        # of the PM-visible attribution (usage totals only — never prompts,
        # credentials, or stderr). Fail-quiet by contract.
        record_codex_dev_turn(
            str(session.session_id or session.id),
            thread_id=tid,
            turn_count=count,
            outcome=outcome,
            usage=use if isinstance(use, dict) else None,
            model_version=getattr(session, "codex_version", None) or version,
            wall_clock_ms=wall_ms,
        )
        return status

    with lease:
        # Re-read persisted context AFTER lease acquisition (Race 3/5).
        try:
            session = AgentSession.get_by_id(session_id) or session
        except Exception:  # noqa: BLE001
            pass
        persisted_thread = getattr(session, "codex_thread_id", None) or None
        persisted_count = getattr(session, "codex_turn_count", None) or 0
        persisted_fence = getattr(session, "dev_lane_fence", None)
        if persisted_count >= max_resumed:
            wall_ms = int((time.monotonic() - started_wall) * 1000)
            _log_evidence("guard-exhausted", persisted_thread, persisted_count, None, wall_ms)
            return {
                "ok": False,
                "error": (
                    f"Codex resume bound reached ({persisted_count}/{max_resumed} turns). "
                    "The thread is preserved — narrow the work or ask the operator "
                    "to raise CODEX__MAX_RESUMED_TURNS. No new thread was started."
                ),
                "report": None,
                "thread_id": persisted_thread,
                "turn_count": persisted_count,
            }

        worktree = getattr(session, "working_dir", None) or getattr(session, "runner_cwd", None)
        if not worktree:
            return _refusal("Session has no working_dir — refusing Codex spawn.")
        api_key = os.environ.get("CODEX_API_KEY")
        version, preflight_error = preflight_codex(
            sandbox=sandbox, worktree=str(worktree), api_key=api_key
        )
        if preflight_error is not None:
            wall_ms = int((time.monotonic() - started_wall) * 1000)
            # Failed spawns never burn a resumed turn: re-save the unchanged
            # count under the lease (Race 5).
            try:
                session.codex_turn_count = persisted_count
                session.save()
            except Exception:  # noqa: BLE001
                pass
            _log_evidence("preflight-failed", persisted_thread, persisted_count, None, wall_ms)
            return {
                "ok": False,
                "error": preflight_error,
                "report": None,
                "thread_id": persisted_thread,
                "turn_count": persisted_count,
            }

        adapter = CodexHarnessAdapter(sandbox=sandbox, turn_timeout_s=turn_timeout_s)
        request = TurnRequest(
            message=instruction,
            working_dir=str(worktree),
            env={"CODEX_API_KEY": api_key} if api_key else {},
            prior_uuid=persisted_thread,
        )
        spawned_pid: list[int | None] = [None]
        persisted_here: list[bool] = [False]

        def _on_event(evt) -> None:
            # Synchronous first-sight persistence (Race 1, write-or-kill):
            # the adapter fires session.started in-line, before run_turn()
            # returns, so persisting here is crash-safe.
            if evt.type == "session.started":
                handle = (evt.data or {}).get("handle")
                if handle and not persisted_here[0]:
                    try:
                        session.codex_thread_id = handle
                        session.codex_version = version
                        session.codex_turn_count = persisted_count + 1
                        session.save()
                        persisted_here[0] = True
                    except Exception as exc:
                        pid = spawned_pid[0]
                        if pid is not None:
                            kill_codex_tree(pid)
                        raise RuntimeError(
                            f"Codex thread {handle!r} started but persistence failed "
                            f"({exc}); the Codex process tree was killed so no "
                            "orphan thread survives without a handle."
                        )
            if evt.type == "turn.spawned":
                pid = (evt.data or {}).get("pid")
                if isinstance(pid, int):
                    spawned_pid[0] = pid

        # Increment-after-live-spawn accounting (Race 5): the count moves
        # only once create_subprocess_exec returns a live child. The
        # adapter emits turn.spawned right after spawn; if run_turn raises
        # before any spawn, re-save the unchanged count below.
        try:
            result = asyncio.run(adapter.run_turn(request, on_event=_on_event))
        except RuntimeError as exc:
            # Write-or-kill persist failure (raised from _on_event above).
            wall_ms = int((time.monotonic() - started_wall) * 1000)
            _log_evidence("persist-failed", persisted_thread, persisted_count, None, wall_ms)
            return {
                "ok": False,
                "error": str(exc),
                "report": None,
                "thread_id": persisted_thread,
                "turn_count": persisted_count,
            }
        except Exception as exc:  # noqa: BLE001
            try:
                session.codex_turn_count = persisted_count
                session.save()
            except Exception:  # noqa: BLE001
                pass
            wall_ms = int((time.monotonic() - started_wall) * 1000)
            _log_evidence("spawn-failed", persisted_thread, persisted_count, None, wall_ms)
            return {
                "ok": False,
                "error": f"Codex turn failed before spawn completed: {exc}.",
                "report": None,
                "thread_id": persisted_thread,
                "turn_count": persisted_count,
            }

        wall_ms = int((time.monotonic() - started_wall) * 1000)
        # Server-side resume-budget fallback: a resume turn (prior thread
        # set) whose transcript omitted thread.started never fires the
        # session.started callback above, so without this the turn would
        # ride free against max_resumed_turns (fail-open toward unbounded
        # thread growth). Consume one resumed turn against the known thread
        # instead of silently not counting. A save failure here keeps the
        # persisted count honest and logs loudly; the turn already ran, so
        # there is no orphan-thread risk to kill for.
        if persisted_thread is not None and not persisted_here[0]:
            try:
                session.codex_turn_count = persisted_count + 1
                session.save()
                persisted_here[0] = True
            except Exception:  # noqa: BLE001
                logger.error(
                    "codex_resume_fallback_persist_failed thread=%s",
                    persisted_thread,
                )

        final_thread = result.resume_handle or persisted_thread
        final_count = persisted_count + 1 if persisted_here[0] else persisted_count
        # Fence-token resume check: a TTL lease expiry that raced a
        # still-live child cannot resume a superseded thread.
        try:
            fresh = AgentSession.get_by_id(session_id)
            if fresh is not None and getattr(fresh, "dev_lane_fence", None) != persisted_fence:
                _log_evidence("fence-mismatch", final_thread, final_count, result.usage, wall_ms)
                return {
                    "ok": False,
                    "error": (
                        "Dev-lane fence mismatch — the session's lane was "
                        "recreated while this turn ran. The result was "
                        "discarded; retry the instruction."
                    ),
                    "report": None,
                    "thread_id": final_thread,
                    "turn_count": final_count,
                }
        except Exception:  # noqa: BLE001
            pass

        if result.error_detail is not None:
            log_status = _log_evidence(
                "native-failure", final_thread, final_count, result.usage, wall_ms
            )
            err = result.error_detail
            if log_status == "degraded":
                err += " (turn evidence log degraded: codex_turn_log_failed — see worker logs.)"
            return {
                "ok": False,
                "error": err,
                "report": None,
                "thread_id": final_thread,
                "turn_count": final_count,
            }

        usage = dict(result.usage or {})
        model_version = getattr(session, "codex_version", None) or version
        attribution = (
            f"\n\n[dev harness=codex model={model_version} turns={final_count} usage={usage}]"
        )
        report = (result.final_text or "") + attribution
        log_status = _log_evidence("ok", final_thread, final_count, usage, wall_ms)
        if log_status == "degraded":
            report += " (turn evidence log degraded: codex_turn_log_failed — see worker logs.)"
        structured = dict(result.structured_output or {})
        return {
            "ok": True,
            "error": None,
            "report": report,
            "complete": bool(structured.get("complete", False)),
            "thread_id": final_thread,
            "turn_count": final_count,
            "usage": usage,
        }


if __name__ == "__main__":
    mcp.run()
