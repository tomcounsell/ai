"""Persistent Claude Code session wrapper for the granite-agent-loop PoC.

Wraps `claude -p --verbose --input-format stream-json --output-format stream-json`
as a persistent subprocess so the granite operator can drive multi-turn
interaction without using `claude-agent-sdk`, the Anthropic API key path, or
per-turn `--resume` respawns.

Key behaviors:
- Subprocess is started once and reused for every turn.
- `send_message(text)` writes one JSON envelope to stdin (turn input).
- `read_until_result(timeout)` consumes stdout line-by-line, parsing each
  line as a stream-json event, and returns once a `{"type": "result"}` event
  is observed (or a timeout occurs). All exceptions during the read are
  caught and surfaced as synthetic events; nothing propagates to the caller.
- `restart()` kills the subprocess and respawns it. Callers are expected to
  re-prime any persona context themselves.

Authentication: callers must strip `ANTHROPIC_API_KEY` from the inherited
environment so Claude Code falls back to the Max subscription OAuth path.
`ClaudeSession.start()` does this automatically.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import signal
import subprocess
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_READ_TIMEOUT_S = 120
PER_LINE_READ_TIMEOUT_S = 30
DEFAULT_BIN = "claude"


@dataclass
class ClaudeSessionConfig:
    """Configuration for spawning a Claude Code session subprocess."""

    model: str
    """Anthropic model alias: 'opus', 'sonnet', etc."""

    cwd: str
    """Working directory for the subprocess."""

    permission_mode: str = "bypassPermissions"
    """Claude permission mode flag value."""

    system_prompt: str | None = None
    """Optional --append-system-prompt content."""

    task_list_id: str | None = None
    """CLAUDE_CODE_TASK_LIST_ID for task list isolation. Auto-generated if None."""

    extra_env: dict[str, str] = field(default_factory=dict)
    """Extra environment variables merged on top of the cleaned os.environ."""

    binary: str = DEFAULT_BIN
    """Claude CLI binary name or absolute path."""


class ClaudeSessionError(RuntimeError):
    """Raised for explicit caller-facing errors (e.g. empty send_message)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_env(extra_env: dict[str, str], task_list_id: str) -> dict[str, str]:
    """Build the subprocess env: inherit os.environ, blank the API key.

    Setting `ANTHROPIC_API_KEY=""` (rather than removing it) is the documented
    way to force Claude Code onto the Max subscription OAuth path even when
    a key happens to be present in the inherited environment.
    """
    env = dict(os.environ)
    env["ANTHROPIC_API_KEY"] = ""
    env["CLAUDE_CODE_TASK_LIST_ID"] = task_list_id
    env.update(extra_env)
    return env


def _build_cmd(cfg: ClaudeSessionConfig) -> list[str]:
    binary = shutil.which(cfg.binary) or cfg.binary
    cmd: list[str] = [
        binary,
        "-p",
        "--verbose",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--model",
        cfg.model,
        "--permission-mode",
        cfg.permission_mode,
    ]
    if cfg.system_prompt:
        cmd.extend(["--append-system-prompt", cfg.system_prompt])
    return cmd


def _envelope(text: str) -> str:
    """Stream-json input envelope for one user turn.

    Verified empirically via spike:
        {"type":"user","message":{"role":"user","content":"..."}}
    is accepted by `claude -p --input-format stream-json`.
    """
    return (
        json.dumps(
            {"type": "user", "message": {"role": "user", "content": text}},
            ensure_ascii=False,
        )
        + "\n"
    )


# ---------------------------------------------------------------------------
# ClaudeSession
# ---------------------------------------------------------------------------


class ClaudeSession:
    """Persistent Claude Code subprocess driven via stream-json stdio.

    The session is single-threaded by design: callers must call
    `send_message()` and then `read_until_result()` before the next
    `send_message()`. Concurrent reads/writes are not supported.
    """

    def __init__(self, cfg: ClaudeSessionConfig) -> None:
        self.cfg = cfg
        self._proc: subprocess.Popen[str] | None = None
        self._task_list_id = cfg.task_list_id or f"granite-poc-{uuid.uuid4().hex[:8]}"
        self._started_at: float | None = None

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return  # already running
        env = _build_env(self.cfg.extra_env, self._task_list_id)
        cmd = _build_cmd(self.cfg)
        self._proc = subprocess.Popen(  # noqa: S603 -- args are constructed locally
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.cfg.cwd,
            env=env,
            text=True,
            bufsize=1,  # line-buffered
        )
        self._started_at = time.monotonic()

    def restart(self) -> None:
        """Kill the current subprocess (if any) and respawn a fresh one."""
        self.stop()
        self.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        """Terminate the subprocess. Safe to call multiple times."""
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is not None:
            self._proc = None
            return
        try:
            try:
                proc.stdin.close()  # type: ignore[union-attr]
            except (BrokenPipeError, OSError, ValueError):
                pass
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=timeout_s)
            except ProcessLookupError:
                pass
        finally:
            self._proc = None

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def task_list_id(self) -> str:
        return self._task_list_id

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    # --- I/O ---------------------------------------------------------------

    def send_message(self, text: str) -> None:
        """Write one user-turn envelope to the subprocess stdin.

        Raises:
            ClaudeSessionError: empty input (Claude hangs on empty prompt).
            BrokenPipeError: subprocess has died -- caller must restart.
        """
        if not text or not text.strip():
            raise ClaudeSessionError("send_message: empty text would hang Claude Code")
        if self._proc is None or self._proc.stdin is None:
            raise ClaudeSessionError("send_message: session is not started")
        if self._proc.poll() is not None:
            raise BrokenPipeError("Claude subprocess has exited")
        envelope = _envelope(text)
        try:
            self._proc.stdin.write(envelope)
            self._proc.stdin.flush()
        except BrokenPipeError:
            raise

    def read_until_result(self, timeout: int = DEFAULT_READ_TIMEOUT_S) -> list[dict]:
        """Read stream-json events until a `result` event or timeout.

        Returns a list of decoded events (dicts). On any failure mode --
        per-line timeout, JSON decode error, broken pipe -- a synthetic
        event is appended and the list is returned so the caller can still
        make a routing decision rather than crash.

        Synthetic events:
            {"type": "timeout", "reason": "..."}
            {"type": "decode_error", "raw": "...", "error": "..."}
            {"type": "broken_pipe", "reason": "..."}
        """
        events: list[dict] = []
        if self._proc is None or self._proc.stdout is None:
            events.append({"type": "broken_pipe", "reason": "session not started"})
            return events

        deadline = time.monotonic() + timeout
        per_line_deadline = time.monotonic() + PER_LINE_READ_TIMEOUT_S

        stdout = self._proc.stdout
        while True:
            now = time.monotonic()
            if now >= deadline:
                events.append({"type": "timeout", "reason": f"overall {timeout}s deadline reached"})
                return events
            remaining = min(deadline - now, per_line_deadline - now)
            if remaining <= 0:
                events.append(
                    {
                        "type": "timeout",
                        "reason": f"per-line {PER_LINE_READ_TIMEOUT_S}s deadline reached",
                    }
                )
                return events

            try:
                ready, _, _ = select.select([stdout], [], [], remaining)
            except (ValueError, OSError) as exc:
                events.append({"type": "broken_pipe", "reason": f"select: {exc}"})
                return events
            if not ready:
                # No data available within remaining budget; loop will re-check
                # deadlines and either timeout or wait more.
                continue

            try:
                line = stdout.readline()
            except (BrokenPipeError, ValueError, OSError) as exc:
                events.append({"type": "broken_pipe", "reason": f"readline: {exc}"})
                return events
            if line == "":
                # EOF -- subprocess closed stdout
                events.append({"type": "broken_pipe", "reason": "stdout EOF"})
                return events

            line = line.strip()
            if not line:
                continue
            per_line_deadline = time.monotonic() + PER_LINE_READ_TIMEOUT_S

            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                events.append({"type": "decode_error", "raw": line[:500], "error": str(exc)})
                continue

            if not isinstance(event, dict):
                events.append(
                    {
                        "type": "decode_error",
                        "raw": line[:500],
                        "error": "top-level JSON is not an object",
                    }
                )
                continue

            events.append(event)
            if event.get("type") == "result":
                return events

    # --- iteration helper --------------------------------------------------

    def iter_events(self) -> Iterable[dict]:
        """Yield raw events one at a time until result/timeout.

        Convenience wrapper for callers that want to stream rather than
        collect; internally it just reads and yields from read_until_result.
        """
        yield from self.read_until_result()

    # --- context manager ---------------------------------------------------

    def __enter__(self) -> ClaudeSession:
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: D401
        self.stop()
