"""The ``codex exec`` CLI harness adapter (plan #2001, Phase 3).

All ``codex exec`` subprocess knowledge lives here: argv/stdin construction,
version/auth preflight, the per-turn output-schema temp-file lifecycle, JSONL
lifecycle parsing, normalized events, usage, bounded error detail, and the
write-or-kill process-tree reaper. This module is the Codex counterpart of
``agent/session_runner/harness/claude.py`` and implements the same
:class:`~agent.session_runner.harness.base.HarnessAdapter` protocol, so the
Codex dev lane rides the normalized ``TurnRequest``/``TurnResult``/
``TurnEvent`` contract without generalizing ``HeadlessRoleDriver`` (which
stays statically Claude — top-level roles are never Codex).

Key Codex-vs-Claude differences (documented once, here):

- argv ordering is global-flags-before-``exec``: ``codex -a never
  -s <sandbox> -C <worktree> exec ...``. Resume inserts ``resume
  <thread_id>`` between ``exec`` and the turn flags.
- The prompt is NEVER an argv element: it travels on stdin (``-``), so
  hostile text (unicode, shell metacharacters, null bytes) cannot become
  shell input.
- Codex does not attach a parsed ``structured_output`` object to a terminal
  event: with ``--output-schema`` the schema-conforming JSON arrives as the
  final agent-message TEXT, which this adapter decodes and validates.
- Ephemeral threading is never requested (thread continuity is the point)
  and the deprecated full-auto approval mode is never enabled (``-a never``
  is the policy).
- First-event persistence contract: the caller persists the thread id from
  the synchronous ``session.started`` event (``on_event`` fires in-line,
  before ``run_turn()`` returns) — mirroring the Claude adapter's Race 1
  capture-at-init timing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Callable
from typing import Any

from agent.session_runner.harness import events as _events
from agent.session_runner.harness.base import TurnEvent, TurnRequest, TurnResult

logger = logging.getLogger(__name__)

# Live-probed minimum CLI (plan #2001 spike-1, re-verified 2026-09-10): the
# JSONL lifecycle / resume / --output-schema contract below was verified
# against this version. A version bump must update fixtures/probes before
# changing the gate.
CODEX_MIN_VERSION = (0, 144, 3)
CODEX_VERSION_FLOOR = "0.144.3"

# Sandbox policy. ``workspace-write`` is the deliberate default (codex exec
# defaults to read-only; the dev lane needs to write code).
# ``danger-full-access`` remains an explicit provisional setting and is never
# selected by the CLI flag path — the adapter only carries what typed
# settings pass in.
CODEX_SANDBOXES = ("workspace-write", "danger-full-access")
CODEX_DEFAULT_SANDBOX = "workspace-write"

# Codex thread ids are UUIDs (spike-1 observed
# ``019f5a7e-339a-7323-bf14-1d30931abc86``).
_CODEX_THREAD_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# Bounded native-failure detail (bytes/chars, never secrets — stderr is
# scrubbed before truncation).
_ERROR_DETAIL_MAX_CHARS = 2000

# Secret-shaped substrings scrubbed from captured stderr before it is stored
# or returned. Presence-only auth posture: tokens are never logged.
_SCRUB_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)['\"]?[^'\"\s]+['\"]?"),
    re.compile(r"(?i)(token\s*[:=]\s*)['\"]?[^'\"\s]+['\"]?"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~-]+"),
)

# Explicit child-env allowlist (plan #2001 Risk 5). The Codex child never
# inherits the worker ambient environment: it gets exactly these entries
# carried from ``os.environ`` plus the single-use ``CODEX_API_KEY`` when
# saved login is absent. ``env=`` is always passed explicitly to
# ``asyncio.create_subprocess_exec``.
_CODEX_SPAWN_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LC_MESSAGES",
        "TERM",
        "TZ",
        "USER",
        "LOGNAME",
        "SHELL",
        "CODEX_HOME",
    }
)


def scrub_secret_text(text: str) -> str:
    """Redact secret-shaped substrings from native stderr/log text."""
    out = text
    for pattern in _SCRUB_PATTERNS:
        out = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + "[REDACTED]", out)
    return out


def parse_codex_version(text: str) -> tuple[int, int, int] | None:
    """Parse ``X.Y.Z`` out of ``codex --version`` output (e.g. ``codex-cli 0.154.0``)."""
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def codex_binary() -> str | None:
    """Resolve the ``codex`` binary on PATH, or None when not installed."""
    return shutil.which("codex")


def codex_spawn_env(*, api_key: str | None = None) -> dict[str, str]:
    """Build the explicit Codex child environment.

    Carries only :data:`_CODEX_SPAWN_ENV_ALLOWLIST` entries from
    ``os.environ``. ``CODEX_API_KEY`` rides along ONLY when explicitly
    passed (single-invocation auth for machines without saved login) — it
    is never picked up ambiently, and ``OPENAI_API_KEY`` / any other
    ``*_API_KEY`` / ``*_TOKEN`` entry never appears (asserted by the
    spawn-env test).
    """
    env = {k: v for k, v in os.environ.items() if k in _CODEX_SPAWN_ENV_ALLOWLIST}
    if api_key:
        env["CODEX_API_KEY"] = api_key
    return env


def _saved_login_present() -> bool:
    """True when ``codex login status`` reports usable saved auth."""
    try:
        result = subprocess.run(
            ["codex", "login", "status"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


def preflight_codex(
    *,
    sandbox: str,
    worktree: str,
    api_key: str | None = None,
) -> tuple[str | None, str | None]:
    """Validate the Codex execution preconditions before any spawn.

    Returns ``(version, None)`` on success or ``(None, actionable_error)``
    on the first failing check, in this order: binary → minimum version →
    sandbox policy → exact worktree (must exist and be inside a git repo) →
    auth (saved login or ``CODEX_API_KEY``). Every error names the fix —
    raw stack traces never reach the PM.
    """
    binary = codex_binary()
    if binary is None:
        return None, (
            "Codex CLI not found on PATH. Install it (`npm install -g @openai/codex`) "
            "or enable the opt-in updater (scripts/update/codex_cli.py)."
        )
    try:
        version_out = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=15
        )
        version = parse_codex_version(version_out.stdout or "")
    except Exception:
        version = None
    if version is None:
        return None, (
            "Could not parse `codex --version` output — the CLI contract may have "
            f"drifted. Expected >= {CODEX_VERSION_FLOOR}."
        )
    if version < CODEX_MIN_VERSION:
        return None, (
            f"Codex CLI {'.'.join(map(str, version))} is below the verified minimum "
            f"{CODEX_VERSION_FLOOR} (JSONL/resume/schema contract). "
            "Upgrade (`npm install -g @openai/codex`) or enable the opt-in updater."
        )
    if sandbox not in CODEX_SANDBOXES:
        return None, (
            f"Invalid Codex sandbox {sandbox!r}. Allowed: {', '.join(CODEX_SANDBOXES)} "
            "(default workspace-write)."
        )
    if not os.path.isdir(worktree):
        return None, f"Codex worktree does not exist: {worktree!r}."
    try:
        repo_check = subprocess.run(
            ["git", "-C", worktree, "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if repo_check.returncode != 0:
            return None, (
                f"Codex worktree is not inside a git repository: {worktree!r}. "
                "Codex refuses non-repository roots by default."
            )
    except Exception as exc:
        return None, f"Could not verify git worktree {worktree!r}: {exc}."
    key = api_key or os.environ.get("CODEX_API_KEY")
    if not key and not _saved_login_present():
        return None, (
            "No Codex auth: neither saved `codex login` nor CODEX_API_KEY is usable. "
            "Run `codex login` on this machine or set CODEX_API_KEY for headless turns."
        )
    return ".".join(map(str, version)), None


def build_codex_argv(
    *,
    thread_id: str | None,
    schema_path: str,
    sandbox: str = CODEX_DEFAULT_SANDBOX,
    worktree: str,
) -> list[str]:
    """Assemble the ``codex`` argv (list form — never a shell string).

    Global flags (``-a never``, ``-s``, ``-C``) precede ``exec`` on BOTH
    first and resumed turns (they are CLI globals, so they apply to ``exec
    resume`` too). Resume inserts ``resume <thread_id>`` between ``exec``
    and the turn flags. ``--color never`` rides the first turn only —
    ``exec resume`` rejects ``--color`` (live-measured); its ``auto``
    default emits no color on a pipe. The prompt travels on stdin (final
    ``-``), never as an argv element. Neither ephemeral threading nor the
    full-auto approval mode is ever emitted (asserted by the Verification table).
    """
    argv = [
        "codex",
        "-a",
        "never",
        "-s",
        sandbox,
        "-C",
        worktree,
        "exec",
    ]
    if thread_id:
        argv.extend(["resume", thread_id])
    argv.extend(["--json"])
    if not thread_id:
        argv.extend(["--color", "never"])
    argv.extend(
        [
            "--output-schema",
            schema_path,
            "-",
        ]
    )
    return argv


# Default output schema: the shape the adapter decodes out of the final
# agent-message text. Kept minimal — ``report`` is the PM-visible developer
# report; ``complete`` lets Codex mark the work done. Attribution
# (harness/model-version/turns/usage) is appended by the MCP tool handler,
# not the model.
CODEX_DEV_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "report": {"type": "string"},
        "complete": {"type": "boolean"},
    },
    # The Codex API rejects schemas whose ``required`` omits any key in
    # ``properties`` (live-measured ``invalid_json_schema``) — both keys
    # stay required.
    "required": ["report", "complete"],
    "additionalProperties": False,
}


def _decode_schema_report(final_text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Decode the schema-conforming final agent-message text.

    Returns ``(parsed, None)`` when the text is valid JSON with a string
    ``report`` key, else ``(None, actionable_error)``. An empty/missing
    agent message is a typed failure — it must never spin a PM/tool loop.
    """
    if not final_text or not final_text.strip():
        return None, (
            "Codex returned no final message. The turn produced no developer report; "
            "retry the instruction once, then escalate if it repeats."
        )
    try:
        parsed = json.loads(final_text)
    except json.JSONDecodeError:
        return None, (
            "Codex final message was not schema-valid JSON (first 200 chars: "
            f"{final_text[:200]!r}). The turn produced no usable developer report."
        )
    if not isinstance(parsed, dict) or not isinstance(parsed.get("report"), str):
        return None, (
            "Codex final message missed the required string `report` field. "
            "The turn produced no usable developer report."
        )
    return parsed, None


def kill_codex_tree(root_pid: int) -> list[dict[str, Any]]:
    """SIGKILL a Codex process tree under the enumerate-kill-wait contract.

    Enumerates via ``psutil.Process(root).children(recursive=True)``
    capturing ``(pid, create_time)``, sends ``SIGKILL`` only when the live
    ``create_time`` still matches the captured value (a recycled PID with a
    mismatched ``create_time`` is NEVER signalled), then
    ``psutil.wait_procs(procs, timeout=5)`` and returns survivor dicts with
    cmdline. Covers sandboxed grandchildren, not just the direct child.
    Never kills by bare pid alone. Fail-quiet — returns survivors (possibly
    empty) and never raises.
    """
    import psutil

    survivors: list[dict[str, Any]] = []
    try:
        root = psutil.Process(root_pid)
        captured = [
            (child.pid, child.create_time(), child) for child in root.children(recursive=True)
        ]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return survivors
    targets = []
    for pid, create_time, proc in captured:
        try:
            if proc.create_time() != create_time:
                logger.warning(
                    "[codex] skipping recycled PID %s (create_time mismatch) — never signal",
                    pid,
                )
                continue
            os.kill(pid, signal.SIGKILL)
            targets.append(proc)
        except (ProcessLookupError, psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception as exc:  # noqa: BLE001 — reaper must never raise
            logger.warning("[codex] kill of PID %s failed (non-fatal): %s", pid, exc)
    try:
        gone, alive = psutil.wait_procs(targets, timeout=5)
        _ = gone
        for proc in alive:
            try:
                survivors.append({"pid": proc.pid, "cmdline": proc.cmdline()})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if survivors:
            logger.warning(
                "[codex] %d process(es) survived tree kill: %s", len(survivors), survivors
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[codex] wait_procs failed (non-fatal): %s", exc)
    try:
        os.kill(root_pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("[codex] root kill failed (non-fatal): %s", exc)
    return survivors


def _extract_thread_id(payload: dict[str, Any]) -> str | None:
    """Read the thread id off a ``thread.started`` payload (tolerant shapes)."""
    thread_id = payload.get("thread_id")
    if isinstance(thread_id, str) and thread_id:
        return thread_id
    thread = payload.get("thread")
    if isinstance(thread, dict):
        thread_id = thread.get("id")
        if isinstance(thread_id, str) and thread_id:
            return thread_id
    return None


class CodexHarnessAdapter:
    """:class:`~agent.session_runner.harness.base.HarnessAdapter` for ``codex exec``.

    Owns argv/stdin construction, version/auth preflight, schema temp-file
    lifecycle, JSONL parsing, normalized events, usage, bounded stderr, and
    cancellation cleanup. The prompt is sent on stdin; ``request.prior_uuid``
    carries the resume thread id; ``request.json_schema`` (or the default
    dev-report schema) is materialized to a per-turn secure temp file
    unlinked in ``finally``, including cancellation/failure paths.
    """

    def __init__(
        self,
        *,
        sandbox: str = CODEX_DEFAULT_SANDBOX,
        turn_timeout_s: float = 600.0,
        codex_bin: str | None = None,
    ) -> None:
        self._sandbox = sandbox
        self._turn_timeout_s = turn_timeout_s
        self._codex_bin = codex_bin or "codex"

    async def run_turn(
        self,
        request: TurnRequest,
        *,
        on_event: Callable[[TurnEvent], None] | None = None,
    ) -> TurnResult:
        """Run one ``codex exec`` turn and return a normalized :class:`TurnResult`.

        Emits ``session.started`` via ``on_event`` the instant
        ``thread.started`` arrives — before this coroutine returns — so a
        caller that persists the thread id from ``on_event`` is crash-safe.
        Native ``turn.failed`` / stream ``error`` / malformed JSONL / missing
        terminal events / nonzero exits surface as bounded, scrubbed
        ``error_detail`` — never raw stderr, tokens, or stack traces.
        """
        collected: list[TurnEvent] = []
        thread_id: str | None = None
        usage: dict[str, Any] | None = None
        returncode: int | None = None
        native_error: str | None = None
        malformed_lines = 0
        saw_terminal = False
        final_text = ""

        def _emit(event_type: str, data: dict | None = None) -> None:
            evt = TurnEvent(type=event_type, data=data or {})
            collected.append(evt)
            if on_event is not None:
                try:
                    on_event(evt)
                except Exception as _cb_err:  # noqa: BLE001
                    logger.warning("[codex-adapter] on_event callback raised: %s", _cb_err)

        instruction = request.message or ""
        if not instruction.strip():
            return TurnResult(
                resume_handle=request.prior_uuid,
                final_text="",
                events=collected,
                returncode=None,
                error_detail=(
                    "Empty developer instruction — Codex was not spawned. "
                    "Provide a specific, actionable instruction."
                ),
            )

        resume_thread = request.prior_uuid
        if resume_thread and not _CODEX_THREAD_ID_RE.match(str(resume_thread)):
            return TurnResult(
                resume_handle=None,
                final_text="",
                events=collected,
                returncode=None,
                error_detail=(
                    f"Malformed Codex thread id {resume_thread!r} — refusing resume. "
                    "A valid thread id is a UUID persisted from thread.started."
                ),
            )

        schema = request.json_schema or CODEX_DEV_REPORT_SCHEMA
        schema_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", prefix="codex-schema-", delete=False
        )
        schema_path = schema_file.name
        try:
            json.dump(schema, schema_file)
            schema_file.close()

            argv = build_codex_argv(
                thread_id=resume_thread,
                schema_path=schema_path,
                sandbox=self._sandbox,
                worktree=request.working_dir,
            )
            child_env = codex_spawn_env(
                api_key=(request.env or {}).get("CODEX_API_KEY"),
            )
            stdin_data = instruction.encode("utf-8", errors="replace")

            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=request.working_dir,
                    env=child_env,
                    limit=16 * 1024 * 1024,
                    # Stays in the caller's (PM) process group: steering's
                    # killpg reaches the whole tree (Race 2). Never
                    # start_new_session=True here.
                    start_new_session=False,
                )
            except FileNotFoundError as exc:
                # create_subprocess_exec raises FileNotFoundError for a
                # missing binary AND a missing cwd — disambiguate so a bad
                # working_dir never masquerades as a missing install.
                if not os.path.isdir(request.working_dir):
                    return TurnResult(
                        resume_handle=resume_thread,
                        final_text="",
                        events=collected,
                        returncode=None,
                        error_detail=(
                            f"Codex working directory not found: "
                            f"{request.working_dir!r} ({exc}). Refusing spawn."
                        ),
                    )
                return TurnResult(
                    resume_handle=resume_thread,
                    final_text="",
                    events=collected,
                    returncode=None,
                    error_detail=(
                        f"Codex CLI not found on PATH ({exc}). Install it (`npm install -g "
                        "@openai/codex`) or enable the opt-in updater."
                    ),
                )
            if proc.pid is not None:
                _emit(_events.TURN_SPAWNED, {"pid": proc.pid})

            stderr_text = ""
            try:
                stdout_data, raw_stderr = await asyncio.wait_for(
                    self._communicate(proc, stdin_data),
                    timeout=self._turn_timeout_s,
                )
                stderr_text = (raw_stderr or b"").decode("utf-8", errors="replace")
            except TimeoutError:
                logger.error(
                    "[codex] turn timed out after %.0fs — killing tree", self._turn_timeout_s
                )
                if proc.pid is not None:
                    kill_codex_tree(proc.pid)
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                _emit(_events.TURN_EXITED, {})
                return TurnResult(
                    resume_handle=thread_id or resume_thread,
                    final_text="",
                    events=collected,
                    usage=usage,
                    returncode=None,
                    error_detail=(
                        f"Codex turn timed out after {self._turn_timeout_s:.0f}s. "
                        "The thread is preserved — retry with a narrower instruction."
                    ),
                )
            except asyncio.CancelledError:
                if proc.pid is not None:
                    kill_codex_tree(proc.pid)
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                _emit(_events.TURN_EXITED, {})
                raise

            returncode = proc.returncode
            for raw_line in (stdout_data or b"").splitlines():
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                _emit(_events.ITEM_STDOUT, {})
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    malformed_lines += 1
                    logger.debug("[codex] skipping malformed JSONL line: %s", line[:120])
                    continue
                if not isinstance(payload, dict):
                    malformed_lines += 1
                    continue
                event_type = payload.get("type")
                if event_type == "thread.started":
                    observed = _extract_thread_id(payload)
                    if observed:
                        if thread_id is None:
                            thread_id = observed
                        elif thread_id != observed:
                            logger.warning(
                                "[codex] thread id drift within one turn: %s -> %s",
                                thread_id,
                                observed,
                            )
                        _emit(_events.SESSION_STARTED, {"handle": thread_id, "raw": payload})
                elif event_type == "turn.completed":
                    saw_terminal = True
                    raw_usage = payload.get("usage")
                    if isinstance(raw_usage, dict):
                        usage = raw_usage
                elif event_type == "turn.failed":
                    saw_terminal = True
                    err = payload.get("error")
                    native_error = (
                        json.dumps(err)[:_ERROR_DETAIL_MAX_CHARS]
                        if err is not None
                        else "Codex reported turn.failed with no error payload."
                    )
                elif event_type == "error":
                    message = payload.get("message", "Codex stream error with no message.")
                    native_error = native_error or str(message)[:_ERROR_DETAIL_MAX_CHARS]
                elif event_type == "item.completed":
                    item = payload.get("item") or {}
                    if isinstance(item, dict) and item.get("type") == "agent_message":
                        text = item.get("text", "")
                        if isinstance(text, str) and text:
                            final_text = text

            _emit(_events.TURN_EXITED, {})

            error_detail: str | None = None
            if native_error is not None:
                error_detail = f"Codex-native failure: {scrub_secret_text(native_error)}"
            elif returncode not in (0, None):
                tail = scrub_secret_text(stderr_text)[-_ERROR_DETAIL_MAX_CHARS:]
                error_detail = (
                    f"Codex exited with code {returncode} and no terminal turn event. "
                    f"Stderr tail: {tail!r}."
                    if tail.strip()
                    else (f"Codex exited with code {returncode} and no terminal turn event.")
                )
            elif not saw_terminal:
                error_detail = (
                    "Codex exited cleanly but emitted no terminal turn event "
                    f"(malformed JSONL lines skipped: {malformed_lines})."
                )

            structured: dict[str, Any] | None = None
            if error_detail is None:
                parsed, decode_error = _decode_schema_report(final_text)
                if decode_error is not None:
                    error_detail = decode_error
                else:
                    structured = parsed
                    final_text = parsed["report"] if isinstance(parsed, dict) else final_text

            _emit(
                _events.TURN_COMPLETED,
                {
                    "usage": usage,
                    "cost_usd": None,
                    "returncode": returncode,
                    "result_event_fired": saw_terminal and error_detail is None,
                    "malformed_lines": malformed_lines,
                },
            )
            return TurnResult(
                resume_handle=thread_id or resume_thread,
                final_text="" if error_detail is not None else final_text,
                structured_output=structured,
                events=collected,
                usage=usage,
                cost_usd=None,
                returncode=returncode,
                result_event_fired=(saw_terminal and error_detail is None),
                error_detail=error_detail,
            )
        finally:
            try:
                os.unlink(schema_path)
            except OSError:
                pass

    async def _communicate(
        self, proc: asyncio.subprocess.Process, stdin_data: bytes
    ) -> tuple[bytes | None, bytes | None]:
        """Write stdin, then drain stdout/stderr to completion."""
        try:
            if proc.stdin is not None:
                proc.stdin.write(stdin_data)
                await proc.stdin.drain()
                proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass
        return await proc.communicate()
