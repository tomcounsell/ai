"""Substrate B Stop-hook fidelity probe (plan #1688 Task 0, the HARD GATE).

Spawns ONE real ollama-backed ``claude`` TUI session with a ``--settings``
file registering ``Stop`` and ``SubagentStop`` hooks pointed at a minimal
fail-silent forwarder, drives a single Task-bearing turn that fans out a
subagent, and returns every hook envelope that landed in the per-session
edge file.

The assumption under test (plan spike-0): *Stop / SubagentStop hooks fire
under the ollama-backed ``claude`` binary, the parent ``Stop`` payload
carries ``transcript_path``, and the ``SubagentStop`` payload carries
``agent_id`` / ``agent_type`` on the fleet's pinned ``claude`` version.*
If this probe fails, the whole hook-driven turn-return design is invalid —
which is why the assertion lives in a durable integration test
(``tests/integration/test_granite_ollama_e2e.py``), not a one-off script.

TEST-ONLY. The forwarder generated here is the Task 0 *prototype* — the
production fail-silent forwarder is built in Task 1. Everything (forwarder,
settings file, edge file) is generated into a temp dir per run; nothing
production reads is touched.

Env contract is identical to Substrate B / the recorder:
``build_ollama_child_env`` (three ollama vars + ``CLAUDE_CODE_OAUTH_TOKEN``
pop) with ``assert_no_oauth_leak`` immediately before spawn.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import time
import uuid
from dataclasses import dataclass, field

import pexpect

from agent.granite_container.pty_driver import _strip_ansi
from tests.granite_faults.ollama_env import (
    assert_no_oauth_leak,
    build_ollama_child_env,
    pick_ollama_model,
)

# A prompt that forces a Task-tool fan-out so BOTH edges (parent Stop +
# SubagentStop) are exercised in one turn. Observed to complete in ~130s on a
# warm qwen coding model (Task 0 run, claude 2.1.198).
FANOUT_PROMPT = (
    "Use the Task tool to launch exactly one subagent (general-purpose type) "
    "with the instruction: 'Compute 17 + 25 and reply with only the number.' "
    "Wait for the subagent's result, then reply with exactly: SUBAGENT_DONE"
)

SPAWN_STARTUP_S = 45.0
# Overall wait budget for the Task-bearing turn. 600s matches the production
# `GraniteSettings.hook_turn_end_wait_s` default and accommodates both
# observed gate runs (126.2s pass; a 300s budget flaked on run 2 when the
# slow qwen parent turn outlived it).
TURN_WAIT_S = 600.0
# The parent turn continues AFTER the subagent ends — observed qwen parent
# turns run 2-6 min past the SubagentStop. Observing SubagentStop therefore
# re-arms the wait: the parent Stop gets at least this much additional time.
SUBAGENT_REARM_S = 360.0
SILENCE_S = 3.0
# After the gate condition is met, keep draining briefly so the settled
# repaint (and any straggler envelope) is captured too.
POST_STOP_SETTLE_S = 5.0

_FORWARDER_SOURCE = '''#!/usr/bin/env python3
"""Task 0 prototype hook forwarder: append raw stdin JSON to the edge file.

Minimal and fail-silent: always exits 0 so a forwarder bug can never block
the turn or splash stderr into the TUI (the production forwarder built in
Task 1 hardens this further).
"""
import json
import os
import sys
import time


def main() -> int:
    try:
        raw = sys.stdin.read()
    except Exception:
        return 0
    edge_path = os.environ.get("GRANITE_HOOK_EDGE_FILE")
    if not edge_path:
        return 0
    try:
        payload = json.loads(raw) if raw.strip() else {"_raw_empty": True}
    except Exception:
        payload = {"_raw_unparseable": raw[:500]}
    envelope = {"_forwarder_ts": time.time(), "payload": payload}
    try:
        with open(edge_path, "a") as f:
            f.write(json.dumps(envelope) + "\\n")
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


@dataclass
class HookFidelityResult:
    """Everything the fidelity gate assertions need from one probe run."""

    model: str
    session_id: str
    elapsed_s: float
    envelopes: list[dict] = field(default_factory=list)

    @property
    def payloads(self) -> list[dict]:
        return [e.get("payload", {}) for e in self.envelopes]

    @property
    def parent_stops(self) -> list[dict]:
        return [p for p in self.payloads if p.get("hook_event_name") == "Stop"]

    @property
    def subagent_stops(self) -> list[dict]:
        return [p for p in self.payloads if p.get("hook_event_name") == "SubagentStop"]


def _write_settings(dirpath: pathlib.Path, forwarder: pathlib.Path) -> pathlib.Path:
    """Generate the per-session ``--settings`` JSON registering both hooks."""
    hook_entry = [
        {
            "matcher": "",
            "hooks": [{"type": "command", "command": f"python3 {forwarder}", "timeout": 10}],
        }
    ]
    settings = {"hooks": {"Stop": hook_entry, "SubagentStop": hook_entry}}
    path = dirpath / "task0_settings.json"
    path.write_text(json.dumps(settings, indent=2))
    return path


def _read_envelopes(edge_path: pathlib.Path) -> list[dict]:
    """Parse the NDJSON edge file, skipping blank/torn lines (fail-silent)."""
    if not edge_path.exists():
        return []
    envelopes: list[dict] = []
    for line in edge_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            envelopes.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return envelopes


def _gate_satisfied(payloads: list[dict]) -> bool:
    """The Task 0 pass condition, evaluated over the hook payloads seen so far.

    PASS requires BOTH edges with their design-load-bearing fields:
    a parent ``Stop`` carrying ``transcript_path`` (the turn-end authority +
    flush-safe content source) AND a ``SubagentStop`` carrying ``agent_id``
    and ``agent_type`` (native subagent disambiguation, Practice 5).
    """
    has_parent_stop = any(
        p.get("hook_event_name") == "Stop" and p.get("transcript_path") for p in payloads
    )
    has_subagent_stop = any(
        p.get("hook_event_name") == "SubagentStop" and p.get("agent_id") and p.get("agent_type")
        for p in payloads
    )
    return has_parent_stop and has_subagent_stop


def _rearm_deadline(deadline: float, subagent_seen_at: float | None) -> float:
    """Extend the wait deadline once ``SubagentStop`` has been observed.

    The parent turn keeps running after the subagent ends (2-6 min on qwen),
    so the parent ``Stop`` gets at least :data:`SUBAGENT_REARM_S` of budget
    measured from the SubagentStop observation. Never shrinks the deadline.
    """
    if subagent_seen_at is None:
        return deadline
    return max(deadline, subagent_seen_at + SUBAGENT_REARM_S)


def run_hook_fidelity_probe(
    *,
    model: str | None = None,
    prompt: str = FANOUT_PROMPT,
    turn_wait_s: float = TURN_WAIT_S,
) -> HookFidelityResult:
    """Run one Task-bearing Substrate B turn and collect the hook envelopes.

    Raises ``RuntimeError`` if no tool-capable ollama model is served.
    """
    pick = model or pick_ollama_model()
    if not pick:
        raise RuntimeError(
            "No tool-capable ollama model is served — pull a qwen coding tag "
            "before running the Stop-hook fidelity gate."
        )

    env = build_ollama_child_env()

    session_id = str(uuid.uuid4())
    capture: list[str] = []
    started = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="granite-hook-gate-") as scratch_str:
        scratch = pathlib.Path(scratch_str)
        forwarder = scratch / "task0_forwarder.py"
        forwarder.write_text(_FORWARDER_SOURCE)
        settings_path = _write_settings(scratch, forwarder)
        edge_path = scratch / "hook_edges.ndjson"
        env["GRANITE_HOOK_EDGE_FILE"] = str(edge_path)

        # Blocker contract: assert on the FINAL child env, right before spawn.
        assert_no_oauth_leak(env)

        # Run the session in a nested scratch cwd (empty — no CLAUDE.md
        # prefill blowout; recorder Task 0 finding).
        cwd = scratch / "cwd"
        cwd.mkdir()

        child = pexpect.spawn(
            "claude",
            [
                "--model",
                pick,
                "--permission-mode",
                "bypassPermissions",
                "--session-id",
                session_id,
                "--settings",
                str(settings_path),
            ],
            env=env,
            cwd=str(cwd),
            echo=False,
            encoding="utf-8",
            timeout=int(turn_wait_s),
        )
        try:
            _drain(child, capture, max_s=SPAWN_STARTUP_S, silence_s=SILENCE_S)
            if "trust this folder" in _strip_ansi("".join(capture)).lower():
                child.send("\r")
                _drain(child, capture, max_s=SPAWN_STARTUP_S, silence_s=SILENCE_S)
            child.send(prompt)
            time.sleep(0.5)
            child.send("\r")
            _drain_until_gate_pass(
                child,
                capture,
                edge_path=edge_path,
                max_s=turn_wait_s,
                settle_s=POST_STOP_SETTLE_S,
            )
        finally:
            try:
                if child.isalive():
                    child.sendcontrol("c")
                    time.sleep(0.3)
                    child.sendcontrol("c")
                    time.sleep(0.3)
                    child.close(force=True)
            except Exception:
                pass

        envelopes = _read_envelopes(edge_path)

    return HookFidelityResult(
        model=pick,
        session_id=session_id,
        elapsed_s=round(time.monotonic() - started, 1),
        envelopes=envelopes,
    )


def _drain(child: pexpect.spawn, capture: list[str], *, max_s: float, silence_s: float) -> None:
    """Read from the child until ``silence_s`` of quiet or ``max_s`` elapses."""
    deadline = time.monotonic() + max_s
    last_data = time.monotonic()
    while time.monotonic() < deadline:
        try:
            chunk = child.read_nonblocking(size=4096, timeout=0.5)
        except pexpect.TIMEOUT:
            if time.monotonic() - last_data >= silence_s:
                return
            continue
        except pexpect.EOF:
            return
        if chunk:
            capture.append(chunk)
            last_data = time.monotonic()


def _drain_until_gate_pass(
    child: pexpect.spawn,
    capture: list[str],
    *,
    edge_path: pathlib.Path,
    max_s: float,
    settle_s: float,
) -> None:
    """Drain PTY output until the gate pass condition is met, then settle.

    This is the whole point of the gate: the completion signal is the hook
    edge itself (parent ``Stop`` with ``transcript_path`` AND ``SubagentStop``
    with ``agent_id``/``agent_type`` — :func:`_gate_satisfied`), not a frame
    glyph or an idle heuristic. ollama prefill can stay silent for tens of
    seconds, so the only timeout is the ``max_s`` budget — re-armed by
    :func:`_rearm_deadline` once ``SubagentStop`` is observed, because the
    parent turn continues 2-6 min after the subagent ends (gate run 2 flaked
    on exactly that with a flat 300s budget).
    """
    deadline = time.monotonic() + max_s
    subagent_seen_at: float | None = None
    pass_seen_at: float | None = None
    while time.monotonic() < _rearm_deadline(deadline, subagent_seen_at):
        try:
            chunk = child.read_nonblocking(size=4096, timeout=0.5)
            if chunk:
                capture.append(chunk)
        except pexpect.TIMEOUT:
            pass
        except pexpect.EOF:
            return
        if pass_seen_at is None:
            payloads = [e.get("payload", {}) for e in _read_envelopes(edge_path)]
            if subagent_seen_at is None and any(
                p.get("hook_event_name") == "SubagentStop" for p in payloads
            ):
                subagent_seen_at = time.monotonic()
            if _gate_satisfied(payloads):
                pass_seen_at = time.monotonic()
        elif time.monotonic() - pass_seen_at >= settle_s:
            return
