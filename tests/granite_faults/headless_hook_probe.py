"""Substrate B headless (`claude -p`) probes for plan #1842 Task 0 (the HARD GATE).

Two empirical questions this module answers, both against the REAL ``claude``
binary backed by ollama (Substrate B, ``GRANITE_OLLAMA_SMOKE=1``):

**Probe A — hook FIRING under ``-p`` (turn-end blocker).** A single-shot
``claude -p`` invocation runs exactly one turn and exits. Does the ``Stop``
hook flush a ``TURN_END`` envelope to the per-session NDJSON edge file BEFORE
the subprocess exits? This determines whether the headless leg of the
per-role transport hedge (plan ``docs/plans/per-role-transport-hedge.md``) can
trust #1688's ``HookEdgeConsumer.poll()`` ``EdgeType.TURN_END`` envelope
exclusively, or must reconcile it with the subprocess ``result`` event / clean
exit as a fallback turn-end signal.

**Probe B — prime RESOLUTION under ``-p`` (concern 5).** Does
``/granite:prime-pm-role`` (or ``prime-dev-role``), passed as the first
prompt to ``claude -p``, actually resolve to the full persona command body
(the way it does in the interactive TUI), or does ``-p`` treat it as a
literal string with no slash-command expansion? Verified by checking whether
the primed PM persona's routing-token convention (``[/dev]``/``[/user]``/
``[/complete]``) surfaces in the model's reply to a message that the prime
command's own persona doc says should get a bare ``[/user]`` ack. Resolution
is asserted on the token appearing at all (``LOOSE_ROUTING_TOKEN_RE``); the
production line-alone contract (``STRICT_ROUTING_TOKEN_RE``) is reported as a
separate substrate-fidelity diagnostic.

Both probes reuse the PRODUCTION hook-settings plumbing from #1688
(``agent.granite_container.hook_edge.generate_hook_settings`` /
``HookEdgeConsumer``) rather than a prototype forwarder — Task 0 for THIS
plan runs after #1688 merged, so the production module is available and is
the surface later tasks (Task 2's ``HeadlessRoleDriver``) will actually use.

Env contract is identical to the rest of the granite ollama harness
(``tests/granite_faults/ollama_env.py``): three ollama vars set, forwarded
``CLAUDE_CODE_OAUTH_TOKEN`` popped, ``assert_no_oauth_leak`` asserted
immediately before spawn.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass

from agent.granite_container.hook_edge import TURN_END, HookEdgeConsumer, generate_hook_settings
from tests.granite_faults.ollama_env import (
    assert_no_oauth_leak,
    build_ollama_child_env,
    pick_ollama_model,
)

# A short, deterministic single-turn prompt — Probe A only needs ONE parent
# Stop, not a Task-tool fan-out (that's hook_fidelity.py's job for #1688 Task
# 0). Keeping it trivial minimizes wall-clock time on the (slow) qwen backend.
TURN_END_PROMPT = "Reply with exactly the word DONE and nothing else."

# Prime command names as registered under .claude/commands/granite/.
_PRIME_COMMANDS = {"pm": "/granite:prime-pm-role", "dev": "/granite:prime-dev-role"}

# A trivial acknowledgment task. prime-pm-role.md's own persona doc says
# (verbatim): "Trivial messages get a one-line ack, then you stop... e.g.
# 'we're back online', 'thanks', 'ok'" -> reply with a single [/user] line.
# If the prime command resolves, this input reliably produces a [/user]
# routing token; if it does not resolve, the model sees only the literal
# slash-command text and a generic assistant has no reason to emit the
# bracket-token convention at all.
PRIME_ACK_TASK = "thanks, that's all for now!"

# The exact routing-token regex from prime-pm-role.md itself (the granite
# classifier's parsing contract). Matching THIS proves full formatting
# fidelity: the token alone on its own line.
STRICT_ROUTING_TOKEN_RE = re.compile(
    r"^\[/(dev|user|complete)(?::([a-z0-9_-]+))?\]\s*$", re.MULTILINE
)

# The RESOLUTION oracle: the routing-token convention appearing ANYWHERE in
# the reply. A model that never saw the persona doc has no reason to emit
# `[/user]`/`[/dev]`/`[/complete]` at all, so any occurrence proves the slash
# command resolved and the persona loaded. The strict line-alone form above is
# a separate, substrate-sensitive formatting diagnostic (observed Task 0 run:
# qwen3.6 emitted `[/user] Sounds good — talk soon.` on one line — persona
# clearly loaded, line discipline imperfect on the weak ollama substrate).
LOOSE_ROUTING_TOKEN_RE = re.compile(r"\[/(dev|user|complete)(?::([a-z0-9_-]+))?\]")

_DEFAULT_TIMEOUT_S = 300.0
_POLL_INTERVAL_S = 0.1
_POST_EXIT_SETTLE_S = 0.5


@dataclass
class HeadlessTurnEndProbeResult:
    """Everything Probe A's assertions need from one headless turn."""

    model: str
    session_id: str
    elapsed_s: float
    returncode: int | None
    envelope_landed_pre_exit: bool
    turn_end_payload: dict | None
    result_event: dict | None
    stderr_tail: str = ""

    @property
    def turn_end_landed(self) -> bool:
        return self.turn_end_payload is not None


@dataclass
class PrimeResolutionProbeResult:
    """Everything Probe B's assertions need from one primed headless turn."""

    role: str
    model: str
    elapsed_s: float
    returncode: int | None
    result_text: str | None
    stderr_tail: str = ""

    @property
    def routing_token_present(self) -> bool:
        """Resolution oracle: the persona's routing token appears at all."""
        if not self.result_text:
            return False
        return bool(LOOSE_ROUTING_TOKEN_RE.search(self.result_text))

    @property
    def strict_token_line(self) -> bool:
        """Formatting-fidelity diagnostic: token alone on its own line
        (the granite classifier's production parsing contract)."""
        if not self.result_text:
            return False
        return bool(STRICT_ROUTING_TOKEN_RE.search(self.result_text))


def _parse_last_result_event(stdout_path: pathlib.Path) -> dict | None:
    """Return the last ``type == "result"`` stream-json event, or None."""
    result_event: dict | None = None
    try:
        text = stdout_path.read_text(errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("type") == "result":
            result_event = data
    return result_event


def run_headless_turn_end_probe(
    *,
    model: str | None = None,
    prompt: str = TURN_END_PROMPT,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> HeadlessTurnEndProbeResult:
    """Spawn ONE real Substrate B ``claude -p`` turn and empirically observe
    whether a ``Stop``/``TURN_END`` envelope lands in the edge file BEFORE
    the subprocess exits.

    Uses the PRODUCTION #1688 hook-settings plumbing
    (``generate_hook_settings`` / ``HookEdgeConsumer``), the same seam the
    ``HeadlessRoleDriver`` (plan #1842 Task 2) will use.

    Raises ``RuntimeError`` if no tool-capable ollama model is served.
    """
    pick = model or pick_ollama_model()
    if not pick:
        raise RuntimeError(
            "No tool-capable ollama model is served — pull a qwen coding tag "
            "before running the headless turn-end probe."
        )

    env = build_ollama_child_env()
    session_id = str(uuid.uuid4())
    started = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="granite-headless-turnend-") as scratch_str:
        scratch = pathlib.Path(scratch_str)
        edge_path = scratch / "edge.ndjson"
        settings_path, edge_path_str = generate_hook_settings(
            scratch, edge_path, filename="headless_probe_settings.json"
        )

        # Blocker contract: assert on the FINAL child env, right before spawn.
        assert_no_oauth_leak(env)

        cwd = scratch / "cwd"
        cwd.mkdir()
        stdout_path = scratch / "stdout.jsonl"

        cmd = [
            "claude",
            "-p",
            "--verbose",
            "--output-format",
            "stream-json",
            "--include-partial-messages",
            "--permission-mode",
            "bypassPermissions",
            "--model",
            pick,
            "--session-id",
            session_id,
            "--settings",
            settings_path,
            prompt,
        ]

        consumer = HookEdgeConsumer(edge_path_str, session_id=session_id)
        envelope_landed_pre_exit = False
        turn_end_payload: dict | None = None

        with open(stdout_path, "wb") as stdout_f:
            proc = subprocess.Popen(
                cmd,
                env=env,
                cwd=str(cwd),
                stdout=stdout_f,
                stderr=subprocess.PIPE,
            )
            try:
                deadline = time.monotonic() + timeout_s
                while True:
                    # Check liveness BEFORE polling edges so a TURN_END found
                    # in this iteration is attributable to "process was still
                    # alive at the moment we observed it" — the empirical
                    # question Task 0 asks. Best-effort at ~100ms granularity;
                    # a race this narrow is not the failure mode the plan is
                    # worried about (a `Stop` hook that never flushes at all
                    # pre-exit vs. one that reliably does).
                    still_alive = proc.poll() is None
                    for edge in consumer.poll():
                        if edge.kind == TURN_END and turn_end_payload is None:
                            turn_end_payload = edge.payload
                            envelope_landed_pre_exit = still_alive
                    if proc.poll() is not None:
                        break
                    if time.monotonic() > deadline:
                        proc.kill()
                        break
                    time.sleep(_POLL_INTERVAL_S)

                # Final drain after exit — captures an envelope flushed
                # concurrently with (or just after) process exit so the
                # result is still recorded, just not marked pre-exit.
                time.sleep(_POST_EXIT_SETTLE_S)
                for edge in consumer.poll():
                    if edge.kind == TURN_END and turn_end_payload is None:
                        turn_end_payload = edge.payload
                        envelope_landed_pre_exit = False
            finally:
                try:
                    _, stderr_data = proc.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    _, stderr_data = proc.communicate(timeout=30)

        elapsed = round(time.monotonic() - started, 1)
        result_event = _parse_last_result_event(stdout_path)
        stderr_tail = stderr_data.decode("utf-8", errors="replace")[-2000:] if stderr_data else ""

    return HeadlessTurnEndProbeResult(
        model=pick,
        session_id=session_id,
        elapsed_s=elapsed,
        returncode=proc.returncode,
        envelope_landed_pre_exit=envelope_landed_pre_exit,
        turn_end_payload=turn_end_payload,
        result_event=result_event,
        stderr_tail=stderr_tail,
    )


def run_prime_resolution_probe(
    *,
    role: str = "pm",
    task: str = PRIME_ACK_TASK,
    model: str | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> PrimeResolutionProbeResult:
    """Spawn ONE real Substrate B ``claude -p`` turn primed via
    ``/granite:prime-{role}-role`` and empirically observe whether the prime
    command resolved (persona surfaces in the reply) or not (the model saw
    only the literal slash-command text).

    Raises ``RuntimeError`` if no tool-capable ollama model is served, or if
    ``role`` is not a known prime command.
    """
    if role not in _PRIME_COMMANDS:
        raise RuntimeError(f"Unknown granite role for priming: {role!r}")

    pick = model or pick_ollama_model()
    if not pick:
        raise RuntimeError(
            "No tool-capable ollama model is served — pull a qwen coding tag "
            "before running the prime resolution probe."
        )

    env = build_ollama_child_env()
    started = time.monotonic()

    prompt = f"{_PRIME_COMMANDS[role]} {task}"

    with tempfile.TemporaryDirectory(prefix="granite-headless-prime-") as scratch_str:
        scratch = pathlib.Path(scratch_str)

        assert_no_oauth_leak(env)

        cwd = scratch / "cwd"
        cwd.mkdir()
        stdout_path = scratch / "stdout.jsonl"

        cmd = [
            "claude",
            "-p",
            "--verbose",
            "--output-format",
            "stream-json",
            "--include-partial-messages",
            "--permission-mode",
            "bypassPermissions",
            "--model",
            pick,
            prompt,
        ]

        with open(stdout_path, "wb") as stdout_f:
            proc = subprocess.Popen(
                cmd,
                env=env,
                cwd=str(cwd),
                stdout=stdout_f,
                stderr=subprocess.PIPE,
            )
            try:
                _, stderr_data = proc.communicate(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                _, stderr_data = proc.communicate(timeout=30)

        elapsed = round(time.monotonic() - started, 1)
        result_event = _parse_last_result_event(stdout_path)
        stderr_tail = stderr_data.decode("utf-8", errors="replace")[-2000:] if stderr_data else ""

    result_text = result_event.get("result") if result_event else None

    return PrimeResolutionProbeResult(
        role=role,
        model=pick,
        elapsed_s=elapsed,
        returncode=proc.returncode,
        result_text=result_text,
        stderr_tail=stderr_tail,
    )
