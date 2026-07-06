"""Headless (`claude -p`) probes for the session-runner turn-end contract.

Salvaged from the deleted granite-faults harness (#1924 Test Impact: SALVAGE)
and repointed at the live post-cutover modules — the probes now exercise the
PRODUCTION ``agent/session_runner/`` plumbing under the real subscription-auth
substrate instead of the retired ollama harness (``granite_faults.ollama_env``
died with the PTY substrate).

Two empirical questions this module answers, both against the REAL ``claude``
binary:

**Probe A — hook FIRING under ``-p`` (turn-end signal).** A single-shot
``claude -p`` invocation runs exactly one turn and exits. Does the ``Stop``
hook flush a ``TURN_END`` envelope to the per-session NDJSON edge file BEFORE
the subprocess exits? This is the empirical ground the
``HeadlessRoleDriver``'s hook-edge turn-end reconciliation stands on: the
``TURN_END`` envelope is the primary signal, with the stream-json ``result``
event / clean exit as the documented fallback.

**Probe B — prime RESOLUTION under ``-p``.** Does the role prime slash
command (``role_driver._slash_command_for``), passed as the first prompt to
``claude -p``, actually resolve to the persona command body? Verified by
checking whether the primed PM persona's routing-token convention
(``[/user]``/``[/complete]``) surfaces in the model's reply to a message the
prime command's own persona doc says should get a bare ``[/user]`` ack.
Resolution is asserted on the token appearing at all
(``LOOSE_ROUTING_TOKEN_RE``); the production line-alone contract
(``STRICT_ROUTING_TOKEN_RE``) is reported as a separate fidelity diagnostic.

Both probes reuse the PRODUCTION hook-settings plumbing
(``agent.session_runner.hook_edge.generate_hook_settings`` /
``HookEdgeConsumer``) and the PRODUCTION subscription-auth env overlay
(``agent.session_runner.role_driver.subscription_auth_env``) — the same
seams ``HeadlessRoleDriver`` uses in every real turn.

Consumed by ``tests/integration/test_headless_probe_e2e.py`` (opt-in real-CLI
smoke, gated on ``HEADLESS_PROBE_SMOKE=1``).
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass

from agent.session_runner.hook_edge import TURN_END, HookEdgeConsumer, generate_hook_settings
from agent.session_runner.role_driver import _slash_command_for, subscription_auth_env

# A short, deterministic single-turn prompt — Probe A only needs ONE parent
# Stop, not a Task-tool fan-out. Keeping it trivial minimizes wall-clock time.
TURN_END_PROMPT = "Reply with exactly the word DONE and nothing else."

# A trivial acknowledgment task. prime-pm-role.md's own persona doc says
# trivial messages get a one-line ack ending in a bare [/user] routing token.
# If the prime command resolves, this input reliably produces a [/user] line;
# if it does not resolve, the model sees only the literal slash-command text
# and a generic assistant has no reason to emit the bracket-token convention.
PRIME_ACK_TASK = "thanks, that's all for now!"

# The exact routing-token contract from the post-cutover PM prime (the
# simplified two-token route table: [/user] | [/complete]). Matching THIS
# proves full formatting fidelity: the token alone on its own line.
STRICT_ROUTING_TOKEN_RE = re.compile(r"^\[/(user|complete)\]\s*$", re.MULTILINE)

# The RESOLUTION oracle: the routing-token convention appearing ANYWHERE in
# the reply. A model that never saw the persona doc has no reason to emit
# `[/user]`/`[/complete]` at all, so any occurrence proves the slash command
# resolved and the persona loaded.
LOOSE_ROUTING_TOKEN_RE = re.compile(r"\[/(user|complete)\]")

_DEFAULT_TIMEOUT_S = 300.0
_POLL_INTERVAL_S = 0.1
_POST_EXIT_SETTLE_S = 0.5


def claude_binary_available() -> bool:
    """True when a ``claude`` executable is on PATH."""
    return shutil.which("claude") is not None


def _build_child_env() -> dict[str, str]:
    """Full child env: process env with the production subscription-auth
    overlay merged on top (the same posture ``HeadlessRoleDriver`` spawns
    with — API key and third-party base URL/auth blanked)."""
    return {**os.environ, **subscription_auth_env()}


@dataclass
class HeadlessTurnEndProbeResult:
    """Everything Probe A's assertions need from one headless turn."""

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
        (the runner's production route-parsing contract)."""
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
    prompt: str = TURN_END_PROMPT,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> HeadlessTurnEndProbeResult:
    """Spawn ONE real ``claude -p`` turn and empirically observe whether a
    ``Stop``/``TURN_END`` envelope lands in the edge file BEFORE the
    subprocess exits.

    Uses the PRODUCTION hook-settings plumbing (``generate_hook_settings`` /
    ``HookEdgeConsumer``) — the same seam ``HeadlessRoleDriver`` uses.

    Raises ``RuntimeError`` if the ``claude`` binary is not on PATH.
    """
    if not claude_binary_available():
        raise RuntimeError("`claude` binary not on PATH — cannot run the turn-end probe.")

    env = _build_child_env()
    session_id = str(uuid.uuid4())
    started = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="runner-headless-turnend-") as scratch_str:
        scratch = pathlib.Path(scratch_str)
        edge_path = scratch / "edge.ndjson"
        settings_path, edge_path_str = generate_hook_settings(
            scratch, edge_path, filename="headless_probe_settings.json"
        )

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
                    # alive at the moment we observed it". Best-effort at
                    # ~100ms granularity — the failure mode under test is a
                    # Stop hook that never flushes pre-exit at all, not a
                    # 100ms race.
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
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> PrimeResolutionProbeResult:
    """Spawn ONE real ``claude -p`` turn primed via the role's prime slash
    command (resolved through the production ``_slash_command_for`` seam) and
    empirically observe whether the prime resolved (persona surfaces in the
    reply) or not (the model saw only the literal slash-command text).

    Runs from the repo root so the project-level ``.claude/commands/`` prime
    files are in scope for slash-command resolution.

    Raises ``RuntimeError`` if the ``claude`` binary is not on PATH.
    """
    if not claude_binary_available():
        raise RuntimeError("`claude` binary not on PATH — cannot run the prime probe.")

    env = _build_child_env()
    started = time.monotonic()

    prompt = f"{_slash_command_for(role)} {task}"
    repo_root = pathlib.Path(__file__).resolve().parents[3]

    with tempfile.TemporaryDirectory(prefix="runner-headless-prime-") as scratch_str:
        scratch = pathlib.Path(scratch_str)
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
            prompt,
        ]

        with open(stdout_path, "wb") as stdout_f:
            proc = subprocess.Popen(
                cmd,
                env=env,
                cwd=str(repo_root),
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
        elapsed_s=elapsed,
        returncode=proc.returncode,
        result_text=result_text,
        stderr_tail=stderr_tail,
    )
