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
import re
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

# The session UUID Claude embeds in stream-json `session_id` fields and prints
# in its on-exit hint line: `claude --resume <uuid>`. Capturing it lets a
# crashed/interrupted session be resumed with full context instead of respawned
# fresh.
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_RESUME_HINT_RE = re.compile(r"--resume\s+(" + _UUID_RE.pattern + r")")


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


def _build_cmd(cfg: ClaudeSessionConfig, resume_session_id: str | None = None) -> list[str]:
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
    if resume_session_id:
        cmd.extend(["--resume", resume_session_id])
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
        self._session_id: str | None = None

    # --- lifecycle ---------------------------------------------------------

    def _spawn(self, resume_session_id: str | None) -> None:
        env = _build_env(self.cfg.extra_env, self._task_list_id)
        cmd = _build_cmd(self.cfg, resume_session_id=resume_session_id)
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

    def start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return  # already running
        self._spawn(resume_session_id=None)

    def restart(self) -> None:
        """Kill the current subprocess and respawn a FRESH one (context lost)."""
        self.stop()
        self.start()

    def resume(self) -> bool:
        """Respawn resuming the captured Claude session, preserving context.

        Uses `claude --resume <session_id>` when a session id was captured from
        the stream-json output (or the on-exit hint). Returns True if a captured
        id was used; False if it fell back to a fresh session because none is
        known yet. This is the context-preserving counterpart to `restart()`.
        """
        self.stop()
        if self._session_id:
            self._spawn(resume_session_id=self._session_id)
            return True
        self._spawn(resume_session_id=None)
        return False

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
    def session_id(self) -> str | None:
        """The captured Claude session UUID, or None if not seen yet."""
        return self._session_id

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
                self._scan_stderr_for_session_id()
                events.append({"type": "broken_pipe", "reason": f"select: {exc}"})
                return events
            if not ready:
                # No data available within remaining budget; loop will re-check
                # deadlines and either timeout or wait more.
                continue

            try:
                line = stdout.readline()
            except (BrokenPipeError, ValueError, OSError) as exc:
                self._scan_stderr_for_session_id()
                events.append({"type": "broken_pipe", "reason": f"readline: {exc}"})
                return events
            if line == "":
                # EOF -- subprocess closed stdout. Grab the resume hint Claude
                # prints on exit if we never saw a session_id in the stream.
                self._scan_stderr_for_session_id()
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

            self._capture_session_id(event.get("session_id"))
            events.append(event)
            if event.get("type") == "result":
                return events

    # --- resume support ----------------------------------------------------

    def _capture_session_id(self, sid: object) -> None:
        """Record the Claude session UUID from a stream-json event field."""
        if isinstance(sid, str) and _UUID_RE.fullmatch(sid):
            self._session_id = sid

    def _scan_stderr_for_session_id(self, budget_s: float = 0.5) -> None:
        """Fallback: read buffered stderr for the `claude --resume <uuid>` hint.

        Claude prints this line when a session exits or is interrupted. We only
        consult it when no session_id was seen in the stream-json output, so a
        crash/ctrl-c that never emitted a `system/init` can still be resumed.
        """
        if self._session_id:
            return
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        deadline = time.monotonic() + budget_s
        buf = ""
        while time.monotonic() < deadline:
            try:
                ready, _, _ = select.select(
                    [proc.stderr], [], [], max(0.0, deadline - time.monotonic())
                )
            except (ValueError, OSError):
                break
            if not ready:
                break
            try:
                chunk = proc.stderr.readline()
            except (ValueError, OSError):
                break
            if chunk == "":
                break
            buf += chunk
            m = _RESUME_HINT_RE.search(buf)
            if m:
                self._session_id = m.group(1)
                return

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
