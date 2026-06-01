"""Top-level loop for the granite-agent-loop PoC.

`GraniteAgentLoop` wires together two `ClaudeSession` subprocesses (PM=Opus,
Dev=Sonnet) and a `GraniteRouter` (granite4.1:3b) into a sequential turn
loop:

    granite.route(task)              -> initial PM prompt
    PMSession.send_message(prompt)   -> read_until_result
    granite.route(pm_events)         -> Dev prompt / done / probe
    DevSession.send_message(prompt)  -> read_until_result
    granite.route(dev_events)        -> next PM prompt / done
    ... repeat ...

Each iteration is appended to `logs/granite_poc_trace.jsonl`. On every exit
path (success, max-turns, crash, KeyboardInterrupt) both subprocesses are
torn down via `atexit` + `signal.SIGTERM` handlers so no zombie Claude
processes leak.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import signal
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from agent.claude_session import (
    ClaudeSession,
    ClaudeSessionConfig,
    ClaudeSessionError,
)
from agent.granite_router import GraniteRouter, GraniteRoutingError, RouterDecision

logger = logging.getLogger(__name__)


DEFAULT_MAX_TURNS = 10
TRACE_LOG_PATH = "logs/granite_poc_trace.jsonl"


# ---------------------------------------------------------------------------
# Loop result
# ---------------------------------------------------------------------------


@dataclass
class LoopResult:
    """Final outcome of one GraniteAgentLoop.run() call."""

    status: str  # 'done', 'max_turns_reached', 'crash', 'granite_routing_error'
    turns: int
    final_payload: str
    trace_path: str
    elapsed_s: float
    pm_task_list_id: str
    dev_task_list_id: str
    error: str | None = None


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------


@dataclass
class GraniteAgentLoop:
    """Sequential PM/Dev/granite turn loop."""

    pm_model: str = "opus"
    dev_model: str = "sonnet"
    cwd: str | None = None  # default to current working dir at run-time
    trace_path: str = TRACE_LOG_PATH
    router: GraniteRouter | None = None
    pm_session: ClaudeSession | None = field(default=None, init=False)
    dev_session: ClaudeSession | None = field(default=None, init=False)
    _signal_handlers_installed: bool = field(default=False, init=False)
    _atexit_registered: bool = field(default=False, init=False)
    _slug: str = field(default="", init=False)

    def __post_init__(self) -> None:
        self._slug = uuid.uuid4().hex[:8]
        if self.router is None:
            self.router = GraniteRouter()
        if not self._atexit_registered:
            atexit.register(self._cleanup)
            self._atexit_registered = True
        if not self._signal_handlers_installed:
            try:
                signal.signal(signal.SIGTERM, self._on_signal)
                signal.signal(signal.SIGINT, self._on_signal)
                self._signal_handlers_installed = True
            except ValueError:
                # We may be called from a non-main thread (e.g. unit tests);
                # signal.signal() is not allowed there. Cleanup still runs
                # via atexit, so this is safe to skip.
                pass

    # --- lifecycle ---------------------------------------------------------

    def _on_signal(self, signum, _frame) -> None:
        logger.warning("granite_agent_loop: received signal %s, tearing down", signum)
        self._log_resume_hints()
        self._cleanup()
        # Re-raise default behavior for SIGTERM/SIGINT so the process actually exits
        sys.exit(128 + (signum or 0))

    def _log_resume_hints(self) -> None:
        """On ctrl-c / SIGTERM, surface how to resume each session by hand."""
        for label, session in (("PM", self.pm_session), ("Dev", self.dev_session)):
            sid = getattr(session, "session_id", None) if session else None
            if sid:
                logger.warning("granite_agent_loop: resume %s with: claude --resume %s", label, sid)

    def _cleanup(self) -> None:
        for session in (self.pm_session, self.dev_session):
            if session is None:
                continue
            try:
                session.stop()
            except Exception:  # noqa: BLE001 -- best effort during teardown
                logger.exception("granite_agent_loop: error stopping session")

    # --- main entry --------------------------------------------------------

    def run(self, task: str, max_turns: int = DEFAULT_MAX_TURNS) -> LoopResult:
        if not task or not task.strip():
            raise ValueError("GraniteAgentLoop.run: task must be a non-empty string")

        cwd = self.cwd or os.getcwd()
        Path(self.trace_path).parent.mkdir(parents=True, exist_ok=True)

        pm_task_list = f"granite-poc-pm-{self._slug}"
        dev_task_list = f"granite-poc-dev-{self._slug}"

        self.pm_session = ClaudeSession(
            ClaudeSessionConfig(
                model=self.pm_model,
                cwd=cwd,
                task_list_id=pm_task_list,
                system_prompt=(
                    "You are the PM session in a two-session granite-operated PoC. "
                    "The user provides one initial task. Your job is to issue ONE "
                    "concise instruction at a time that the Dev session can execute, "
                    "evaluate Dev's reported result, and indicate completion. When "
                    "the user's task is fully satisfied, reply with the EXACT phrase "
                    "'TASK COMPLETE' followed by a one-line summary."
                ),
            )
        )
        self.dev_session = ClaudeSession(
            ClaudeSessionConfig(
                model=self.dev_model,
                cwd=cwd,
                task_list_id=dev_task_list,
                system_prompt=(
                    "You are the Dev session in a granite-operated PoC. Execute "
                    "exactly the instruction you receive. When finished, report a "
                    "short status (one to three lines) including any file paths "
                    "you wrote and any commands you ran. Do not ask the PM for "
                    "clarification -- make a reasonable choice and report it."
                ),
            )
        )
        started = time.monotonic()
        try:
            self.pm_session.start()
            self.dev_session.start()
            return self._run_loop(task, max_turns, started, pm_task_list, dev_task_list)
        finally:
            self._cleanup()

    # --- inner loop --------------------------------------------------------

    def _run_loop(
        self,
        task: str,
        max_turns: int,
        started: float,
        pm_task_list: str,
        dev_task_list: str,
    ) -> LoopResult:
        assert self.router is not None
        assert self.pm_session is not None and self.dev_session is not None

        last_payload = ""
        turn = 0
        # Initial routing: granite picks the first PM prompt from the task.
        try:
            decision = self.router.route(task=task)
        except GraniteRoutingError as exc:
            self._trace({"turn": 0, "stage": "initial_route", "error": str(exc)})
            return LoopResult(
                status="granite_routing_error",
                turns=0,
                final_payload="",
                trace_path=self.trace_path,
                elapsed_s=time.monotonic() - started,
                pm_task_list_id=pm_task_list,
                dev_task_list_id=dev_task_list,
                error=str(exc),
            )

        for turn in range(1, max_turns + 1):
            turn_started = time.monotonic()
            stage = decision.action
            try:
                if decision.action == "done":
                    self._trace(
                        {
                            "turn": turn,
                            "stage": "done",
                            "payload": decision.payload[:500],
                            "duration_ms": int((time.monotonic() - turn_started) * 1000),
                        }
                    )
                    return LoopResult(
                        status="done",
                        turns=turn,
                        final_payload=decision.payload,
                        trace_path=self.trace_path,
                        elapsed_s=time.monotonic() - started,
                        pm_task_list_id=pm_task_list,
                        dev_task_list_id=dev_task_list,
                    )

                target_session, label = self._target_for(decision)
                if target_session is None:
                    # Unknown action -- log and probe.
                    self._trace(
                        {"turn": turn, "stage": "unknown_action", "decision": decision.action}
                    )
                    decision = self.router.route(operator_events=[{"type": "unknown_action"}])
                    continue

                self._trace(
                    {
                        "turn": turn,
                        "stage": stage,
                        "session": label,
                        "granite_tool": decision.tool_name,
                        "prompt_preview": decision.payload[:400],
                    }
                )
                try:
                    target_session.send_message(decision.payload or "continue")
                except (ClaudeSessionError, BrokenPipeError) as exc:
                    self._trace({"turn": turn, "stage": "send_failed", "error": str(exc)})
                    # Prefer a context-preserving resume; fall back to a fresh
                    # session if no session_id was captured before the crash.
                    resumed = target_session.resume()
                    self._trace(
                        {
                            "turn": turn,
                            "stage": "resumed" if resumed else "restarted",
                            "session": label,
                            "session_id": getattr(target_session, "session_id", None),
                        }
                    )
                    decision = self.router.route(
                        operator_events=[
                            {
                                "type": "crash",
                                "session": label,
                                "recovered_via": "resume" if resumed else "restart",
                            }
                        ]
                    )
                    continue

                events = target_session.read_until_result(timeout=180)
                operator_events = [
                    e for e in events if e.get("type") in {"timeout", "decode_error", "broken_pipe"}
                ]
                duration_ms = int((time.monotonic() - turn_started) * 1000)
                self._trace(
                    {
                        "turn": turn,
                        "stage": f"{stage}_result",
                        "session": label,
                        "events_count": len(events),
                        "operator_events": operator_events,
                        "duration_ms": duration_ms,
                    }
                )

                # Persist the textual payload for the LoopResult.
                for ev in events:
                    if ev.get("type") == "result":
                        result_text = ev.get("result")
                        if isinstance(result_text, str):
                            last_payload = result_text

                # Detect explicit PM completion phrase before routing.
                if label == "pm" and "TASK COMPLETE" in (last_payload or "").upper():
                    self._trace(
                        {
                            "turn": turn,
                            "stage": "pm_signaled_done",
                            "payload": last_payload[:300],
                        }
                    )
                    return LoopResult(
                        status="done",
                        turns=turn,
                        final_payload=last_payload,
                        trace_path=self.trace_path,
                        elapsed_s=time.monotonic() - started,
                        pm_task_list_id=pm_task_list,
                        dev_task_list_id=dev_task_list,
                    )

                # Hand the result back to granite for routing.
                pm_events = events if label == "pm" else None
                dev_events = events if label == "dev" else None
                decision = self.router.route(
                    pm_events=pm_events,
                    dev_events=dev_events,
                    operator_events=operator_events or None,
                )
            except GraniteRoutingError as exc:
                self._trace({"turn": turn, "stage": "granite_error", "error": str(exc)})
                return LoopResult(
                    status="granite_routing_error",
                    turns=turn,
                    final_payload=last_payload,
                    trace_path=self.trace_path,
                    elapsed_s=time.monotonic() - started,
                    pm_task_list_id=pm_task_list,
                    dev_task_list_id=dev_task_list,
                    error=str(exc),
                )

        # Max turns hit.
        self._trace({"turn": turn, "stage": "max_turns_reached"})
        return LoopResult(
            status="max_turns_reached",
            turns=turn,
            final_payload=last_payload,
            trace_path=self.trace_path,
            elapsed_s=time.monotonic() - started,
            pm_task_list_id=pm_task_list,
            dev_task_list_id=dev_task_list,
        )

    # --- helpers -----------------------------------------------------------

    def _target_for(self, decision: RouterDecision) -> tuple[ClaudeSession | None, str]:
        if decision.target == "pm":
            return self.pm_session, "pm"
        if decision.target == "dev":
            return self.dev_session, "dev"
        return None, "none"

    def _trace(self, entry: dict) -> None:
        entry = dict(entry)
        entry.setdefault("ts", time.time())
        try:
            with open(self.trace_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError:
            logger.exception("granite_agent_loop: failed to write trace entry")
