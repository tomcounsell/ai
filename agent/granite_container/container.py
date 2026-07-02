"""Container: the steady-state loop for the granite interactive-TUI session runner.

The container is the production execution path for bridge-originated
sessions under the standalone worker. It owns two PTYs (PM + Dev), the
persona-priming slash commands, the startup-phase parser, and the granite
classifier. It runs the loop:

  1. spawn both PTYs
  2. prime both personas via /granite:prime-{pm,dev}-role
  3. startup-phase parser watches both PTYs; on trust-folder
     prompt, dismiss with "1\\r"
  4. steady state: wait for PM idle -> read the PM's last assistant
     text from the JSONL transcript, classify it (regex parse),
     forward the verbatim [/dev] payload to Dev PTY -> wait for Dev
     idle -> read Dev's last assistant text verbatim from transcript
     -> write to PM PTY -> repeat
  5. exit on PM [/complete] prefix, max_turns safety cap, dev
     hang (await_idle timeout), startup_unresolved (neither PTY
     settles within STARTUP_HARD_CEILING_S), or any exception

Two-PTY coordination is the core synchronization concern. The
container's loop is single-threaded; reads from both PTYs are not
interleaved within a single tick. The loop processes one
PM->granite->Dev->granite->PM cycle per tick.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from agent.granite_container.builder import PiSubprocessBuilder, PtyClaudeBuilder
from agent.granite_container.byob_relogin import (
    RECOVERY_HARD_DEADLINE_S,
    ReloginOutcome,
    recover_login,
)
from agent.granite_container.granite_classifier import (
    ClassificationResult,
    classify_pm_prefix,
)
from agent.granite_container.hook_edge import (
    NEEDS_HUMAN,
    TURN_END,
    HookEdge,
    HookEdgeConsumer,
)
from agent.granite_container.pty_driver import (
    DEFAULT_MIN_CONTENT_BYTES,
    PTYDriver,
)
from agent.granite_container.startup_parser import (
    StartupEvent,
    parse_startup_frame,
)
from agent.granite_container.transcript_tailer import (
    last_assistant_text,
    text_bearing_count,
)

logger = logging.getLogger(__name__)

# Path to the persona-priming slash commands. Both files are shipped
# in the repo under .claude/commands/granite/. The container
# looks them up by name; the slash-command mechanism is part of the
# TUI's parser, not the container's.
PM_PRIME_SLASH_CMD = "/granite:prime-pm-role"
DEV_PRIME_SLASH_CMD = "/granite:prime-dev-role"
TEAMMATE_PRIME_SLASH_CMD = "/granite:prime-teammate-role"


def _resolve_pm_prime_cmd(session_type: str | None) -> str:
    """Return the PM prime slash command for the given session_type.

    - ``"teammate"`` sessions get primed with the teammate prime
      so they bend toward chitchat / CS / issue-creation behavior.
    - All other session types (``"eng"``, ``None``, etc.) get the
      standard PM prime.
    """
    if session_type == "teammate":
        return TEAMMATE_PRIME_SLASH_CMD
    return PM_PRIME_SLASH_CMD


# --- Hook-driven turn-return settings resolvers (plan #1688) ---
# Each resolver takes an explicit override (used by tests / callers) and falls
# back to settings.granite when None, with a hardcoded default if settings can
# not be loaded (bare unit-test import). Kept as free functions so the wiring
# has a single source of truth and stays testable without a Settings instance.


def _resolve_hook_driven(override: bool | None) -> bool:
    if override is not None:
        return override
    try:
        from config.settings import settings

        return bool(settings.granite.hook_driven_turn_end)
    except Exception:
        return True


def _resolve_hook_turn_end_wait_s(override: float | None) -> float:
    if override is not None:
        return override
    try:
        from config.settings import settings

        return float(settings.granite.hook_turn_end_wait_s)
    except Exception:
        return 600.0


def _resolve_crash_resume_cap(override: int | None) -> int:
    if override is not None:
        return override
    try:
        from config.settings import settings

        return int(settings.granite.hook_crash_resume_cap)
    except Exception:
        return 3


# The trust-folder prompt dismissal (per the F-probe at
# scripts/probe_slash_arguments.py:243-247).
TRUST_FOLDER_DISMISSAL = "1"

# Default max_turns safety cap. PM may run this many PM->Dev cycles
# before the container exits with pm_max_turns. The cap is a
# safety net; the steady-state exit is the PM [/complete] prefix.
DEFAULT_MAX_TURNS = 10

# Hard wall-clock ceiling for the startup phase. The startup loop
# keeps polling (short 3s reads) until both PTYs are idle; if they
# never settle within this ceiling, the container exits
# `startup_unresolved`. This is plan Risk 6's detection mode for a
# broken `--permission-mode` flag: when the flag is renamed or
# removed by a TUI upgrade, the bypass-permissions bar never paints,
# the C5 idle heuristic never fires, and the run exits with the
# distinct `startup_unresolved` signature instead of burning the
# steady-state budget and reporting a misleading `pm_hang`. The
# ceiling is deliberately long — persona load on a cold Opus
# high-effort PTY can run minutes past the prime post-write budget
# (PR #1612 live run, June 2026), and the short per-cycle reads
# make the extended wait cheap.
STARTUP_HARD_CEILING_S = 600.0

# Number of consecutive identical startup-loop fingerprints (keyed on
# parser verdict response ALONE -- not idle bools) before declaring a
# confirmed plateau and bailing early. At STARTUP_CYCLE_TIMEOUT_S=3s
# per cycle, 10 identical cycles ~ 30s of confirmed zero-progress --
# clears transient cold-start jitter but saves ~95% of the 600s ceiling.
STARTUP_PLATEAU_CYCLES = 10

# Maximum bytes captured from each PTY buffer in the startup diagnostic
# frame. Keeps the AgentSession field bounded and avoids ANSI noise.
_STARTUP_FRAME_BUF_CAP = 4000
# Maximum total persisted frame length (PM + Dev combined).
_STARTUP_FRAME_TOTAL_CAP = 6000

# Per-cycle idle ceiling. This is a SANITY BOUND for pathological /
# extreme cases, NOT a hang detector. "Did not reach idle within N
# seconds" is an unreliable hang signal: a genuinely hung PTY goes
# byte-silent and is therefore declared idle almost immediately,
# while a PTY doing real multi-minute work (research, multi-tool Dev
# turns, writing a GitHub issue) emits continuously and would be
# falsely killed by a short deadline. The old 120s value killed any
# Dev turn that took longer than two minutes — i.e. essentially all
# substantive engineering work — and surfaced as exit_reason=dev_hang
# despite the Dev actively making progress.
#
# Real hang detection lives in the heartbeat / liveness-recovery layer
# (agent/session_health.py + issue #1724): it observes actual progress
# signals and can cancel a wedged session's task. The container must
# NOT second-guess that with a fixed idle deadline. We keep a large
# 12-hour ceiling only so a truly stuck loop cannot wait forever; the
# recovery layer is expected to act long before this is reached.
CYCLE_IDLE_TIMEOUT_S = 12 * 60 * 60.0  # 12 hours — sanity ceiling, not a hang signal

# Per-poll interval for the hook-driven turn-end wait (plan #1688). Each tick
# drains the hook edge file, then does a short liveness/badge read on the PTY
# (read_until_idle with a tiny budget) so on_pty_read / byte accounting stay
# live and a crash (EOF / !isalive) is observed promptly. Short enough that a
# turn_end edge is picked up within ~1s of the Stop hook firing.
HOOK_POLL_INTERVAL_S = 1.0

# Per-cycle idle budget for the startup-phase poll loop. Short by
# design: the loop's job is to detect transient startup events
# (trust-folder, update notice), not to wait for a long model
# turn. A short per-read budget means the startup loop polls
# cheaply and frequently while waiting for the PTYs to settle
# under STARTUP_HARD_CEILING_S, and only burns the long
# steady-state budget (CYCLE_IDLE_TIMEOUT_S) once it enters the
# actual turn loop. Without this, the startup loop consumed
# 120s idle waits per cycle on a slow persona load (PR #1612
# live run, June 2026).
# HARD FLOOR: must stay strictly above pty_driver.QUIESCENCE_S
# (2.0s) — idle is only declared after that much byte-silence, so
# a startup poll shorter than QUIESCENCE_S can NEVER observe idle
# and silently reintroduces the startup_unresolved hang this
# constant exists to bound.
STARTUP_CYCLE_TIMEOUT_S = 3.0

# Trust-folder prompt pattern matched against the raw TUI buffer
# BEFORE the C5 idle heuristic. The workspace-trust dialog does
# NOT paint the bypass-permissions bar (different security layer
# from the per-tool permission dialogs that `bypassPermissions`
# suppresses), so the C5 heuristic's `bypass.{0,30}permissions`
# regex never matches. Dismissing with "1" unsticks the prime in
# <2s on the trust-folder path; the prior behavior silently
# burned 60s on `saw_idle=False` and never sent the prime
# command, deadlocking both PM and Dev (issue #1572 live run).
TRUST_FOLDER_RE = re.compile(r"(Yes, I trust this folder|trust this folder\?)", re.IGNORECASE)
# Pre-C5 trust dismissal budget: short, because we want to dismiss
# quickly and re-read. 10s catches a slow first paint without
# burning the full prime budget.
PRIME_TRUST_DISMISS_TIMEOUT_S = 10.0
# Pre-write C5 budget: the welcome frame paints fast, so this only
# has to cover initial render + any post-dismissal re-render.
PRIME_PRE_WRITE_TIMEOUT_S = 30.0
# Post-write C5 budget: persona body load + first-token wait. The
# pool's prewarmed PTY starts cold (no conversation history), so
# Opus 4.8 high-effort can take 90-180s for the slash command to
# actually be processed and "Worked for Ns" to print. PR #1612
# live run on June 2026 hit 120s saw_idle=False on PM; raise the
# post-write budget to absorb that latency without churning the
# startup-phase loop. The pre-write budget stays tight because it
# is bounded by render speed, not model latency.
PRIME_POST_WRITE_TIMEOUT_S = 360.0
# Legacy alias kept for tests and existing references. New code
# should reference the pre/post-write pair explicitly.
PRIME_C5_TIMEOUT_S = PRIME_POST_WRITE_TIMEOUT_S
# Post-write content floor. The bypass-permissions bar is a
# persistent footer, so an empty/minimal buffer can match the C5
# idle heuristic even before the model has produced any response
# content (the bar is what gated `_prime_session`'s pre-write
# read). Without a floor, the post-write read returns
# saw_idle=True on the stale pre-write buffer and `_prime_session`
# returns while the slash command is still being processed (PR
# #1612 live run, June 2026: Dev prime returned in 5ms with
# buffer_len=223 — the model never had time to load). 1500 bytes
# comfortably exceeds the welcome frame but is well under the
# persona-load response length.
PRIME_POST_WRITE_MIN_CONTENT_BYTES = 1500

# Cap on the size of `ContainerResult.exit_message`. A multi-KB
# traceback or ollama error body can land here on the exception
# branch, and the result is published into the Telegram relay —
# keep the message bounded so a single failure doesn't flood a
# chat. 500 chars matches the Telegram message clamp the relay
# already enforces downstream; truncating here keeps the relay
# clean and the JSON results doc readable.
EXIT_MESSAGE_MAX_CHARS = 500

# Corrective nudge written to PM's PTY when PM emits a prefix the
# classifier cannot route (no recognized [/dev]|[/user]|[/complete]
# token, or a recognized token with an empty payload). Without a
# write, PM stays idle on the same non-compliant buffer and the
# loop reclassifies the identical output every tick until max_turns
# — burning the safety cap on a stuck PM. The nudge re-prompts PM
# to re-emit a compliant prefix, so the next read sees fresh output.
PM_COMPLIANCE_NUDGE = (
    "Your last reply did not start with a routing prefix on its own "
    "line. Re-send your reply starting with exactly one of [/dev], "
    "[/user], or [/complete] on the first line, followed by the "
    "content."
)

# Wrap-up prompt written to PM when the run ends with no user-facing
# message delivered. Instructs PM to emit a [/user] or [/complete]
# summary so the human always receives a real message.
PM_WRAPUP_PROMPT = (
    "The developer has finished. Here is their final report:\n\n{seed}\n\n"
    "Send your [/user] or [/complete] summary to the human now. "
    "Include the specific outcomes from the report above — which files changed "
    "and what was done — not a generic acknowledgement."
)

# Maximum number of wrap-up attempts when PM still hasn't produced a
# user-facing message on exit. Capped at 1 to bound the extra latency;
# a PM that stays silent after the wrap-up prompt gets the canned
# OPERATOR_TERMINAL_MESSAGE instead.
MAX_WRAPUP_ATTEMPTS = 1

# Fallback seed string for the wrap-up prompt when the developer did
# not produce a captured report and the Dev PTY is no longer readable.
DEV_REPORT_UNAVAILABLE = "The developer did not produce a captured report."

# Canonical paths for Pi builder priming (two --append-system-prompt flags).
# The rails file is the single source of safety constraints shared by all
# granite personas; the persona file adds the Pi-tuned dev-role delta only.
# Both paths are resolved relative to the repo root at import time so the
# container never has to compute them per-turn.
_REPO_ROOT = Path(__file__).parent.parent.parent
PI_RAILS_PATH = str(_REPO_ROOT / ".claude" / "commands" / "granite" / "_prime-rails.md")
PI_PERSONA_PATH = str(_REPO_ROOT / "config" / "personas" / "granite" / "pi_dev_rails.md")

# Per-turn prefix-contract reminder appended to Dev-report text before
# it is written to PM's PTY. Restores the load-bearing per-turn contract
# assertion that the deleted --append-system-prompt path guaranteed.
# Kept to one line so the token cost is negligible; the full contract is
# in the one-shot /prime-pm-role slash command.
PM_TURN_CONTRACT_REMINDER = (
    "\n\nBegin your reply with `[/user]`, `[/complete]`, or `[/dev]` on its own line."
)

# Fallback user-visible message delivered directly (bypassing PM) when
# the wrap-up guard exhausts MAX_WRAPUP_ATTEMPTS without PM emitting a
# user-facing prefix. Guarantees the human always gets some message.
OPERATOR_TERMINAL_MESSAGE = (
    "I wasn't able to produce a response to this — please rephrase or follow up."
)

# RESERVED harness suffix. `[/dev:steer]` parses to harness="steer" but is NOT
# a real builder harness — `_get_builder` must NEVER receive it (only `claude`/
# `None` and `pi` are real harnesses). The dev-routing branch intercepts this
# suffix before `_get_builder` and writes the (token-stripped) payload straight
# to the Dev PTY as a mid-task steer, then continues without blocking on Dev idle.
STEER_HARNESS_SUFFIX = "steer"

# One-line continuation ack written to the PM PTY immediately after a
# [/dev:steer] Dev write, so PM produces its next turn rather than hanging on
# an empty idle read (Risk 3 — PM only runs when Dev is idle; with nothing
# written to PM after the steer it would idle-timeout into a spurious pm_hang).
PM_DEV_STEER_ACK = "Steering delivered to Dev; continuing."

# User-facing message delivered when an operator aborts a running session via a
# steering message flagged is_abort. Delivered through on_user_payload BEFORE the
# loop breaks (post-break output is dropped).
STEER_ABORT_USER_MESSAGE = "Session stopped at your request."

# --- Hook-driven turn returns (plan #1688) ---
# The verified-submit nudge written to a resumed claude session after a
# crash-during-turn (Practice 6, load-bearing minimum). A dropped `continue`
# re-wedges the crash path, so it is written like any other turn body.
CRASH_RESUME_CONTINUE = "continue"

# User-facing escalation delivered when repeated crashes on a single turn
# exhaust the crash-resume cap. Guarantees the human gets a terminal message
# instead of an infinite resume loop (issue acceptance criterion).
CRASH_ESCALATION_MESSAGE = (
    "This task kept crashing mid-response and I couldn't recover it after "
    "several attempts. Please try again or follow up."
)


@dataclass
class TurnWaitResult:
    """Result of a single hook-driven (or idle-fallback) turn-boundary wait.

    ``saw_turn`` plays the role of the legacy ``saw_idle`` so the routing code
    downstream is unchanged. The extra fields drive the two edges the hook
    channel adds (plan #1688):

    - ``needs_human`` — a ``Notification`` / ``PermissionRequest`` /
      ``AskUserQuestion`` edge fired; the caller routes to ``[/user]``
      deterministically (no classifier guess).
    - ``escalated`` — repeated crashes exhausted the crash-resume cap; the
      caller delivers the operator-terminal escalation message.
    - ``transcript_path`` — the ``Stop`` payload's transcript path, the
      flush-safe source of the final assistant message (may be None on the
      idle-fallback path, where the caller uses its precomputed transcript).
    """

    saw_turn: bool
    buffer: str = ""
    idle_marker: str = ""
    elapsed_ms: int = 0
    needs_human: HookEdge | None = None
    escalated: bool = False
    transcript_path: str | None = None
    accumulated_bytes: int = 0


@dataclass
class TurnRecord:
    """One cycle of the steady-state loop's PM->Dev handoff."""

    turn_index: int
    pm_idle_ms: int
    dev_idle_ms: int
    classification: str  # dev | user | complete | unknown
    compliance_miss: bool
    pm_first_line: str
    routed_payload_chars: int
    pm_idle_marker: str
    dev_idle_marker: str


@dataclass
class ContainerResult:
    """Final output of a container run.

    `exit_reason` is one of: pm_complete, pm_user, pm_max_turns,
    pm_floor_delivered, dev_hang, pm_hang, startup_unresolved,
    pm_no_user_message, exception. The worker renders this as the
    run's terminal verdict. pm_floor_delivered is a clean exit where
    the wrap-up guard delivered the PM's last assistant message
    directly (non-empty but prefix-less response).

    `user_facing_routed` is True when at least one [/user] or
    non-empty [/complete] payload was delivered to the user channel
    during the run. BridgeAdapter propagates this flag to
    agent_session.user_facing_routed so session_executor can choose
    the correct post-run emoji.
    """

    session_id: str
    user_message: str
    turns: list[TurnRecord] = field(default_factory=list)
    exit_reason: str = "in_progress"
    exit_message: str = ""
    total_pm_pty_bytes: int = 0
    total_dev_pty_bytes: int = 0
    parse_failures: int = 0
    classification_compliance_misses: int = 0
    transcript_fallback_count: int = 0
    resume_uuid: str | None = None
    startup_events: list[dict[str, Any]] = field(default_factory=list)
    coord_test_pass: bool | None = None
    user_facing_routed: bool = False
    # PTY identity fields: PID and deterministic transcript path for each role.
    # Populated by Container.run() after the PTY pair is acquired. Transcript
    # paths follow Claude Code's naming convention:
    #   ~/.claude/projects/{cwd-slug}/{session_id}.jsonl
    #   where cwd-slug = realpath(cwd).replace("/", "-").replace(".", "-")
    pm_pid: int | None = None
    pm_transcript_path: str | None = None
    dev_pid: int | None = None
    dev_transcript_path: str | None = None
    # Stable physical PTYPool slot index (0-based). Correlated to a
    # specific (pm_pid, dev_pid) pair only via co-persisted fields —
    # the slot itself is recycled after each session. Surfaced here so
    # the dashboard can show which pool slot a session occupied.
    pty_slot: int | None = None
    # === Startup failure diagnostic fields (issue #1710) ===
    # Populated on startup_unresolved exits (plateau and ceiling both).
    # startup_failure_kind: "plateau" or "ceiling".
    startup_failure_kind: str | None = None
    # startup_diagnostic_frame: stripped PM+Dev buffer snapshot at failure.
    startup_diagnostic_frame: str | None = None
    # startup_plateau_cycles: number of consecutive identical fingerprint cycles
    # that triggered the plateau bail (None for ceiling exits).
    startup_plateau_cycles: int | None = None


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------


def _transcript_path(cwd: str, session_id: str | None) -> str | None:
    """Compute the Claude Code transcript path for a PTY session.

    Claude Code names transcripts:
        ~/.claude/projects/{cwd-slug}/{session_id}.jsonl
    where {cwd-slug} replaces BOTH ``/`` and ``.`` in the realpath'd cwd with
    ``-``. The ``.`` substitution is load-bearing: every bridge session runs in
    a synthetic ``.worktrees/dev-{id}`` worktree, so the cwd always contains a
    dot (``.worktrees`` -> ``--worktrees``). Replacing only ``/`` produced a
    path Claude Code never writes to, so the transcript read came back
    file-missing every turn and the run shipped OPERATOR_TERMINAL_MESSAGE
    instead of the PM's real reply. Must stay in sync with
    ``bridge_adapter._transcript_path_from_spec``.

    Returns None when session_id is not known; callers that receive None
    should skip transcript tailing for that session.
    """
    if not session_id:
        return None
    # Resolve symlinks before slugging so the slug matches Claude Code's
    # own realpath-based naming. Guard on truthiness: os.path.realpath("")
    # returns the process CWD, which would silently corrupt the slug.
    if cwd:
        cwd = os.path.realpath(cwd)
    cwd_slug = cwd.replace("/", "-").replace(".", "-")
    return str(Path.home() / ".claude" / "projects" / cwd_slug / f"{session_id}.jsonl")


def _capture_pty_identity(
    result: ContainerResult,
    pm_pty: PTYDriver | None,
    dev_pty: PTYDriver | None,
    cwd: str,
) -> None:
    """Populate ContainerResult PTY identity fields from live PTY drivers.

    Captures PIDs via PTYDriver.pid (None when not alive or not spawned)
    and computes deterministic transcript paths from the session_id stored
    on each driver. All operations are best-effort — a missing PID or
    unknown session_id leaves the field as None; callers must tolerate None.
    """
    if pm_pty is not None:
        result.pm_pid = getattr(pm_pty, "pid", None)
        result.pm_transcript_path = _transcript_path(cwd, getattr(pm_pty, "_session_id", None))
    if dev_pty is not None:
        result.dev_pid = getattr(dev_pty, "pid", None)
        result.dev_transcript_path = _transcript_path(cwd, getattr(dev_pty, "_session_id", None))


def _truncate_exit_message(text: str) -> str:
    """Bound `ContainerResult.exit_message` to `EXIT_MESSAGE_MAX_CHARS`.

    The exception branch can capture multi-kilobyte tracebacks or
    ollama error bodies; the result is published into the Telegram
    relay, so we clamp the size here rather than letting a single
    failure flood a chat. A short ellipsis marker preserves the
    "we truncated" signal in the published message.
    """
    if len(text) <= EXIT_MESSAGE_MAX_CHARS:
        return text
    return text[: EXIT_MESSAGE_MAX_CHARS - 3] + "..."


def _capture_startup_frame(
    pm_level_tail: str,
    dev_level_tail: str,
    kind: str,
    cycles: int,
) -> str:
    """Capture a diagnostic frame string from PM+Dev level-triggered buffer tails.

    Pure helper (no PTY or IO). Strips printable text, caps each buffer tail and
    the combined total, and formats a human-readable artifact for the AgentSession
    field. Falls back from level_tail to edge_buffer: callers pass
    ``level_tail.strip() or edge_buffer`` so the frame is never blank when the
    level-triggered capture is empty but the edge-triggered delta is not.

    Returns a non-empty string even when both inputs are empty/None. The frame
    always includes kind + cycle count so the record is never a blank artifact.
    """
    import re as _re

    _printable_re = _re.compile(r"[^\x20-\x7e\n\r\t]")

    def _clean(text):
        if not text:
            return ""
        # Strip non-printable bytes (ANSI was already stripped by pty_driver).
        cleaned = _printable_re.sub("", text)
        # Cap per-buffer size.
        if len(cleaned) > _STARTUP_FRAME_BUF_CAP:
            cleaned = "..." + cleaned[-_STARTUP_FRAME_BUF_CAP + 3 :]
        return cleaned.strip()

    pm_clean = _clean(pm_level_tail)
    dev_clean = _clean(dev_level_tail)

    header = f"[startup-failure kind={kind} cycles={cycles}]\n"
    pm_section = f"--- PM ---\n{pm_clean}\n" if pm_clean else "--- PM ---\n(no content)\n"
    dev_section = f"--- Dev ---\n{dev_clean}\n" if dev_clean else "--- Dev ---\n(no content)\n"

    frame = header + pm_section + dev_section
    # Cap total size.
    if len(frame) > _STARTUP_FRAME_TOTAL_CAP:
        frame = frame[: _STARTUP_FRAME_TOTAL_CAP - 3] + "..."
    return frame


def _transcript_read_branch(pm_transcript: str | None) -> str:
    """Classify why a PM transcript read produced no text into a greppable branch.

    Returns one of three STABLE, greppable substrings:
      - ``transcript read: path-None``     — the resolved path is None.
      - ``transcript read: file-missing``  — path set but file absent on disk.
      - ``transcript read: no-new-entry``  — file present but no new text-bearing
        entry past baseline (valid file, PM emitted nothing this cycle).

    These substrings are load-bearing for log-grep diagnostics; do not rename.
    """
    if not pm_transcript:
        return "transcript read: path-None"
    if not os.path.exists(pm_transcript):
        return "transcript read: file-missing"
    return "transcript read: no-new-entry"


def _log_transcript_read_diagnostic(
    site: str,
    pm_transcript: str | None,
    pm_pty: Any,
    dev_pty: Any,
) -> None:
    """Emit a WARNING explaining why a PM transcript read came back empty.

    `site` labels the read site (prime-turn / steady-state / wrap-up guard).
    Logs the greppable branch substring plus the fully-resolved attempted
    path, the presence of `spec.pm_session_id` / `spec.dev_session_id`
    (sourced from each PTY driver's `_session_id`), and the live PM PTY's
    `_session_id` — the trio needed to root-cause a path/slug or session-id
    mismatch.
    """
    branch = _transcript_read_branch(pm_transcript)
    pm_session_id = getattr(pm_pty, "_session_id", None)
    dev_session_id = getattr(dev_pty, "_session_id", None)
    logger.warning(
        "[granite-container] %s: %s; path=%r "
        "pm_session_id=%r dev_session_id=%r pty_session_id=%r; "
        "using unknown classification",
        site,
        branch,
        pm_transcript,
        pm_session_id,
        dev_session_id,
        pm_session_id,
    )


def _unknown_classification() -> ClassificationResult:
    """Synthetic unknown classification for conservative PM-classify fallback.

    Used when the PM transcript read returns empty or the path is None.
    Drives the existing compliance-miss branch (PM_COMPLIANCE_NUDGE + re-poll).
    Never re-parses pm_buf -- the painted buffer is not a routing source.
    """
    return ClassificationResult(
        destination="unknown",
        compliance_miss=True,
        payload="",
        raw_first_line="",
    )


def _make_sandbox_cwd() -> tuple[str, str]:
    """Create a fresh sandbox tempdir for the container run.

    Returns (cwd, label) where label is a short prefix used for
    logging. The container writes nothing into the sandbox; it
    only uses it as the subprocess cwd. The sandbox is cleaned up
    on `__exit__` via a `try/finally` in `Container.run`.
    """
    sandbox_root = Path(tempfile.gettempdir()) / "granite"
    sandbox_root.mkdir(parents=True, exist_ok=True)
    sandbox = sandbox_root / f"run-{uuid.uuid4().hex[:8]}"
    sandbox.mkdir(parents=True, exist_ok=False)
    return str(sandbox), sandbox.name


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------


class RouteOutcome(NamedTuple):
    """Return value of `_route_pm_classification`.

    `should_break` True means the steady-state loop should exit after this
    routing decision. `exit_reason` carries the ContainerResult.exit_reason
    to set when `should_break` is True (None means the loop should continue
    — dev routing does not break the loop).
    """

    should_break: bool
    exit_reason: str | None


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------


class Container:
    """The steady-state loop. Owns two PTYs and the granite calls.

    Lifecycle: `__init__` -> `run()` -> close both PTYs. The
    container is single-shot (one operator invocation, one
    container.run). A multi-turn session is a series of operator
    invocations; the container does not persist state across them
    (per the plan's *Persistent Artifacts* section).
    """

    def __init__(
        self,
        user_message: str,
        cwd: str | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        pm_model: str | None = None,
        dev_model: str | None = None,
        on_user_payload: Callable[[str], None] | None = None,
        on_complete_payload: Callable[[str], None] | None = None,
        on_turn: Callable[[], None] | None = None,
        on_pty_read: Callable[[str], None] | None = None,
        pm_pty: PTYDriver | None = None,
        dev_pty: PTYDriver | None = None,
        session_type: str | None = None,
        poll_steering: Callable[[], list[dict]] | None = None,
        pm_session_id: str | None = None,
        dev_session_id: str | None = None,
        pm_hook_edge_file: str | None = None,
        dev_hook_edge_file: str | None = None,
        hook_driven: bool | None = None,
        hook_turn_end_wait_s: float | None = None,
        crash_resume_cap: int | None = None,
    ) -> None:
        if not user_message.strip():
            raise ValueError("Container.user_message must be non-empty")
        self.user_message = user_message
        self.cwd = cwd
        self.max_turns = max_turns
        self._pm_model = pm_model
        self._dev_model = dev_model
        self._on_user_payload = on_user_payload
        self._on_complete_payload = on_complete_payload
        # Per-turn progress hook: called once per classified PM turn
        # (every destination, including unknown). BridgeAdapter uses it
        # to bump `agent_session.last_turn_at` so the two-tier
        # no-progress detector's sub-check A stays live for granite
        # sessions (PR #1612 review TD1). Exceptions are swallowed —
        # progress signaling must never crash the loop.
        self._on_turn = on_turn
        # PTY read-loop hook: called from _cycle_idle once per turn-boundary
        # idle-return from read_until_idle, passing the ANSI-stripped (but not
        # cursor/spinner-normalized) turn buffer. BridgeAdapter uses it to stamp
        # last_pty_read_loop_at (unconditional) and last_pty_activity_at (only
        # when buffer differs from prior read) for the path-B mid-run wedge
        # detector (#1724). Exceptions are swallowed — liveness signaling must
        # never crash the loop.
        self._on_pty_read = on_pty_read
        # Optional pre-warmed PTY pair from the PTYPool. When both
        # are provided, Container skips _spawn_pair() and reuses
        # the pool's prewarmed pair (BridgeAdapter is the caller in
        # production). When None, Container spawns its own (used by
        # tests and run_ping_pong_test). The pool marks the pair
        # as _released_to_pool=True on its async with entry, so
        # _close_pair will NOT close them — the pool's __aexit__
        # owns the close.
        self._prewarmed_pm_pty = pm_pty
        self._prewarmed_dev_pty = dev_pty
        # session_type drives PM prime selection: "teammate" → TEAMMATE_PRIME_SLASH_CMD;
        # all others → PM_PRIME_SLASH_CMD. Stored as a plain string (StrEnum is str-compatible
        # so SessionType.TEAMMATE == "teammate" is True; storing str avoids an import cycle).
        self._session_type = session_type
        # Optional, storage-agnostic steering-poll callback (mid-run steering,
        # issue #1779). Called once at the top of each steady-state turn; returns
        # a list of pending steering message dicts, each with keys:
        #   - "text"     (str):  the message body to inject into PM's PTY
        #   - "sender"   (str):  who sent it (used in the [Steering from …] prefix)
        #   - "is_abort" (bool): True signals a graceful operator-requested abort
        # The Container NEVER imports Redis / agent.steering — the BridgeAdapter
        # supplies the closure (it owns the Redis-list drain). Default None
        # preserves every existing caller (CLI, tests) unchanged. Like _on_turn,
        # the call site is fail-silent: a raising callback yields [] and never
        # crashes the loop.
        self._poll_steering = poll_steering
        self._pm_pty: PTYDriver | None = None
        self._dev_pty: PTYDriver | None = None
        self._sandbox: tuple[str, str] | None = None
        # Last Dev report captured from the Dev JSONL transcript after
        # each dev branch cycle. Used as the seed for the wrap-up guard
        # prompt so the PM can deliver a specific summary.
        self._last_dev_report: str | None = None
        # One-shot flags for prime-turn relay (issue #1644).
        # _prime_relayed=True means the PM's prime-turn buffer was routed
        # to user/complete (not dev) — the first steady-state iteration
        # should force a fresh _cycle_idle before classifying so it reads
        # genuinely new PM output, not the stale prime buffer.
        self._prime_relayed: bool = False
        self._prime_pm_buf_hash: int | None = None
        # --- BYOB /login re-auth recovery (issue #1750) ---
        # Non-blocking, idempotent recovery dispatch state. When a LOGIN_PROMPT
        # is detected mid-startup, exactly one recovery thread is spawned (guarded
        # by _recovery_launched, set synchronously before spawn so the persisting
        # login frame across cycles never double-spawns the BYOBClient subprocess).
        # The thread builds the immutable ReloginOutcome locally, publishes it via
        # the single _recovery_outcome assignment, then sets _recovery_done (C1:
        # the loop checks _recovery_done.is_set() BEFORE dereferencing the outcome,
        # avoiding a torn/stale read).
        self._recovery_launched: bool = False
        self._recovery_done: threading.Event = threading.Event()
        self._recovery_outcome: ReloginOutcome | None = None
        # The PTY whose buffer showed the login frame (PM or Dev). Captured in
        # _handle_startup so recovery writes Enter/paste into the RIGHT PTY (B2 —
        # never hardcode PM). Its companion buffer is the edge buffer that matched.
        self._login_pty: PTYDriver | None = None
        self._login_pty_buffer: str = ""
        # One-shot guard so the session_events observability entry is recorded
        # exactly once when the recovery completion is first observed.
        self._recovery_event_recorded: bool = False
        # Testability seam: tests inject a fake recover_login here to avoid
        # spawning a real BYOBClient subprocess / driving a real browser.
        self._recover_login = recover_login

        # --- Hook-driven turn returns (plan #1688) ---
        # The turn-boundary authority. When hook-driven (the default, gated by
        # settings.granite.hook_driven_turn_end), the container waits on the
        # Claude Code `Stop` hook edge (via a HookEdgeConsumer over the
        # per-session edge file) instead of the PTY idle heuristic. The idle
        # heuristic (read_until_idle) is demoted to a running/idle badge,
        # liveness, and crash detection — its code is retained, not removed.
        # When no edge file is supplied, or the flag is off, the container falls
        # back to the pre-#1688 idle-completion path (the documented safety
        # valve).
        self._pm_session_id = pm_session_id
        self._dev_session_id = dev_session_id
        self._hook_driven = _resolve_hook_driven(hook_driven)
        self._hook_turn_end_wait_s = _resolve_hook_turn_end_wait_s(hook_turn_end_wait_s)
        self._crash_resume_cap = _resolve_crash_resume_cap(crash_resume_cap)
        self._pm_consumer: HookEdgeConsumer | None = (
            HookEdgeConsumer(pm_hook_edge_file, session_id=pm_session_id)
            if (self._hook_driven and pm_hook_edge_file)
            else None
        )
        self._dev_consumer: HookEdgeConsumer | None = (
            HookEdgeConsumer(dev_hook_edge_file, session_id=dev_session_id)
            if (self._hook_driven and dev_hook_edge_file)
            else None
        )
        # Deduped needs_human edges already routed to [/user] this run (keyed by
        # ts) so a re-poll never re-delivers the same prompt.
        self._needs_human_seen: set[float] = set()

    # -- Lifecycle --------------------------------------------------------

    def _spawn_pair(self) -> None:
        """Spawn both PTYs and prime both personas.

        Reuses a prewarmed pair when the ctor received one
        (production path: PTYPool -> BridgeAdapter -> Container).
        Spawns a fresh pair otherwise (tests, run_ping_pong_test).
        """
        if self._prewarmed_pm_pty is not None and self._prewarmed_dev_pty is not None:
            self._pm_pty = self._prewarmed_pm_pty
            self._dev_pty = self._prewarmed_dev_pty
            return

        if self.cwd is None:
            self._sandbox = _make_sandbox_cwd()
            cwd = self._sandbox[0]
        else:
            cwd = self.cwd

        self._pm_pty = PTYDriver(role="pm", cwd=cwd, model=self._pm_model)
        self._dev_pty = PTYDriver(role="dev", cwd=cwd, model=self._dev_model)
        self._pm_pty.spawn()
        self._dev_pty.spawn()

    def _close_pair(self) -> None:
        # Skip PTYs the pool already owns — its __aexit__ does the
        # close. Double-closing races the pool's respawn and can
        # leave the pool's respawned pair in a half-closed state
        # (see PTYPool contract: the slot's PTY is closed exactly
        # once, on release).
        for pty in (self._pm_pty, self._dev_pty):
            if pty is None:
                continue
            if getattr(pty, "_released_to_pool", False):
                continue
            try:
                pty.close(force=True)
            except Exception:
                pass
        if self._sandbox is not None:
            sandbox_path = Path(self._sandbox[0])
            if sandbox_path.exists():
                try:
                    shutil.rmtree(sandbox_path)
                except Exception:
                    pass

    def _uses_pool_pair(self) -> bool:
        """Whether this container runs on a PTYPool-prewarmed pair.

        Pool-backed containers receive a prewarmed PM+Dev PTY pair from the
        PTYPool; self-spawned containers (tests, ping-pong) create their own.
        The pool owns its PTY lifecycle via close-on-release and PID-targeted
        orphan kill at worker startup.
        """
        return self._prewarmed_pm_pty is not None and self._prewarmed_dev_pty is not None

    def _close_pair_and_reap(self) -> None:
        """Close PTYs, then reap orphaned process groups on the self-spawned path only.

        Pool-backed pairs are owned by the PTYPool (close-on-release + PID-targeted
        orphan kill at worker startup); signalling their process groups races the
        pool's respawn, so we never touch them. On the self-spawned path (tests,
        ping-pong), pexpect's force-close does not signal grandchildren (MCP/bash
        subprocesses in the child's pgroup), so we capture each PTY's pgid BEFORE
        close (close nulls _child, after which .pid returns None) and SIGTERM/SIGKILL
        the group after close.
        """
        reap_pgids: list[tuple[str, int]] = []
        if not self._uses_pool_pair():
            for _name, _pty in (("pm_pty", self._pm_pty), ("dev_pty", self._dev_pty)):
                if _pty is None:
                    continue
                _pid = getattr(_pty, "pid", None)
                if _pid is None:
                    continue
                try:
                    reap_pgids.append((_name, os.getpgid(_pid)))
                except (ProcessLookupError, PermissionError, OSError):
                    pass
        self._close_pair()
        for _name, _pgid in reap_pgids:
            try:
                os.killpg(_pgid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError) as _e:
                logger.debug("Could not SIGTERM %s pgid %s: %s", _name, _pgid, _e)
                continue
            time.sleep(0.5)
            try:
                os.killpg(_pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass  # group already gone after SIGTERM — expected

    # -- Startup phase ----------------------------------------------------

    def _handle_startup(self, buffer_pm: str, buffer_dev: str) -> str | None:
        """Run the startup-phase parser on both PTY buffers.

        Returns the response text to write to PM's PTY, or None when
        no known startup event is present. The parser is pure with
        respect to the input buffers (it never mutates them), so the
        caller keeps ownership of the buffer state and feeds the next
        delta on the following cycle.
        """
        result_pm = parse_startup_frame(buffer_pm)
        result_dev = parse_startup_frame(buffer_dev)

        # Trust-folder is the most likely co-occurring event; the
        # parser has already deduplicated. Pick the highest-priority
        # event: error > trust_folder > update > login > prime_ack
        # > unknown.
        chosen = None
        for r in (result_pm, result_dev):
            if r.event in (StartupEvent.ERROR_MODAL,):
                chosen = ("error", r)
                break
        if chosen is None:
            for r in (result_pm, result_dev):
                if r.event == StartupEvent.TRUST_FOLDER_PROMPT:
                    chosen = ("trust_folder", r)
                    break
        if chosen is None:
            for r in (result_pm, result_dev):
                if r.event == StartupEvent.UPDATE_NOTICE:
                    chosen = ("update", r)
                    break
        if chosen is None:
            for r in (result_pm, result_dev):
                if r.event == StartupEvent.LOGIN_PROMPT:
                    chosen = ("login", r)
                    # B2 — capture WHICH PTY matched + its buffer so recovery
                    # writes Enter/paste into the right PTY (never hardcode PM).
                    # The login dispatch happens in the startup loop based on
                    # _login_pty being set; _handle_startup stays fast and
                    # returns None for the login event (its canned response is
                    # None anyway). Trust/update/error paths are unaffected —
                    # only the login branch sets _login_pty.
                    if r is result_pm:
                        self._login_pty = self._pm_pty
                        self._login_pty_buffer = buffer_pm
                    else:
                        self._login_pty = self._dev_pty
                        self._login_pty_buffer = buffer_dev
                    break

        if chosen is None:
            return None

        _, r = chosen
        return r.response

    @staticmethod
    def _resolve_expected_identity() -> str | None:
        """Read the expected login identity (email) from ``config/identity.json``.

        Read defensively: a missing/unreadable file or absent ``email`` field
        returns None, which the fail-closed account guard in ``recover_login``
        treats as "cannot positively confirm identity" → abort to the alert
        path. Per-instance overrides at ``~/Desktop/Valor/identity.json`` win
        (shallow merge), mirroring the persona-segment loader.
        """
        email: str | None = None
        for path in (
            Path(__file__).resolve().parents[2] / "config" / "identity.json",
            Path.home() / "Desktop" / "Valor" / "identity.json",
        ):
            try:
                data = json.loads(path.read_text())
            except Exception:  # noqa: BLE001 — missing/unreadable → degrade
                continue
            value = data.get("email")
            if isinstance(value, str) and value.strip():
                email = value.strip()
        return email

    def _dispatch_login_recovery(self, deadline: float) -> None:
        """Spawn the non-blocking, idempotent BYOB recovery thread (issue #1750).

        Called from the startup loop when a LOGIN_PROMPT is detected and
        ``self._login_pty`` is set. The ``_recovery_launched`` guard is set
        SYNCHRONOUSLY before spawning so the login frame persisting across many
        startup cycles spawns exactly ONE BYOBClient subprocess (idempotency).

        The thread is a daemon (round-3 concern): an early loop exit
        (ceiling/exception) must not pin the worker waiting up to
        ``RECOVERY_HARD_DEADLINE_S`` for a non-daemon join. ``recover_login``'s
        own ``finally`` still closes the BYOBClient subprocess on the daemon
        thread (C1 — no orphaned ``tsx byob-mcp.ts``).

        Thread-safety (C1): the thread builds the complete immutable
        ``ReloginOutcome`` locally, assigns ``self._recovery_outcome`` as its
        FINAL data statement, then calls ``self._recovery_done.set()``. The loop
        checks ``_recovery_done.is_set()`` before dereferencing the outcome.
        """
        self._recovery_launched = True  # set SYNCHRONOUSLY before spawn (idempotency)
        login_pty = self._login_pty
        login_pty_buffer = self._login_pty_buffer
        expected_identity = self._resolve_expected_identity()
        recover = self._recover_login

        def _worker() -> None:
            outcome: ReloginOutcome
            try:
                outcome = recover(
                    login_pty,
                    login_pty_buffer,
                    deadline=time.monotonic() + RECOVERY_HARD_DEADLINE_S,
                    expected_identity=expected_identity,
                )
            except Exception as exc:  # noqa: BLE001 — recovery must never crash the worker
                logger.warning("container: login recovery thread errored: %s", exc)
                outcome = ReloginOutcome(
                    succeeded=False, flow=None, reason=f"recovery thread error: {exc}"
                )
            # C1: publish the fully-built immutable outcome, THEN set the event.
            self._recovery_outcome = outcome
            self._recovery_done.set()

        threading.Thread(target=_worker, name="granite-login-recovery", daemon=True).start()

    # -- Steady-state loop ------------------------------------------------

    def _cycle_idle(
        self, pty: PTYDriver, min_content_bytes: int = DEFAULT_MIN_CONTENT_BYTES
    ) -> tuple[bool, str, str, int]:
        """Wait for a single PTY to reach idle.

        Returns (saw_idle, buffer, idle_marker, elapsed_ms). If the
        timeout fires without an idle, the buffer is whatever the
        TUI has painted so far and saw_idle is False.

        The buffer is the PTY's per-turn capture (everything painted
        since the last write to that PTY), not just the bytes read
        during this call: the routed output may have streamed during an
        earlier read (e.g. PM's prime response streams during the
        prime's post-write wait), and the steady-state read then sees a
        quiescent PTY. The `or result.buffer` fallback covers drivers
        that don't populate `turn_buffer` (unit-test mocks).
        """
        result = pty.read_until_idle(
            min_content_bytes=min_content_bytes, timeout_s=CYCLE_IDLE_TIMEOUT_S
        )
        buffer = result.turn_buffer or result.buffer
        # Fire the PTY read-loop hook (path-B mid-run wedge detector, #1724).
        # Called unconditionally on every _cycle_idle so the bridge-adapter can
        # stamp last_pty_read_loop_at and diff-gate last_pty_activity_at.
        # Exceptions are swallowed — liveness signaling must never crash the run.
        # NOTE: on_pty_read fires once per _cycle_idle return (turn boundary), not
        # per inner read_until_idle poll iteration (pty_driver.py). A session wedged
        # mid-turn leaves last_pty_read_loop_at stale between _cycle_idle calls —
        # stage-1 ABSTAINs for that interval rather than false-firing. Safe for
        # observe-only stage-1; stamp the inner loop before stage-2 wires recovery
        # (requires adding a per-iteration callback param to PTYDriver.read_until_idle
        # and plumbing through Container + BridgeAdapter).
        if self._on_pty_read is not None:
            try:
                self._on_pty_read(buffer)
            except Exception as _pty_read_err:
                import logging as _log

                _log.getLogger(__name__).debug(
                    "[granite-container] on_pty_read hook raised: %s", _pty_read_err
                )
        return (result.saw_idle, buffer, result.idle_marker, result.elapsed_ms)

    # -- Hook-driven turn boundary (plan #1688) ---------------------------

    def _cycle_turn(
        self,
        pty: PTYDriver,
        consumer: HookEdgeConsumer | None,
        session_id: str | None,
        *,
        role: str,
    ) -> TurnWaitResult:
        """Wait for a single turn to complete — hook-driven or idle-fallback.

        When hook-driven (the default) and a consumer is present, the
        turn-completion authority is the Claude Code ``Stop`` hook edge (via
        :meth:`_await_turn_end`), racing a crash/timeout watchdog. Otherwise
        this is a thin wrapper over the idle heuristic (:meth:`_cycle_idle`) —
        the documented fallback path (feature flag off, or no edge file).

        Returns a :class:`TurnWaitResult` whose ``saw_turn`` plays the role of
        the old ``saw_idle`` so the routing code downstream is unchanged; extra
        fields (``needs_human``, ``escalated``, ``transcript_path``) drive the
        needs-human route and the crash-escalation terminal state.
        """
        if not self._hook_driven or consumer is None:
            saw, buf, marker, ms = self._cycle_idle(pty)
            return TurnWaitResult(saw_turn=saw, buffer=buf, idle_marker=marker, elapsed_ms=ms)
        return self._await_turn_end(pty, consumer, session_id, role=role)

    def _await_turn_end(
        self,
        pty: PTYDriver,
        consumer: HookEdgeConsumer,
        session_id: str | None,
        *,
        role: str,
    ) -> TurnWaitResult:
        """Wait for the ``Stop`` edge, racing PTY EOF / crash and a timeout.

        The wait is level-triggered against the append-only edge file (a Stop
        written before the wait arms is still read — Race 1). On each tick:

        1. Drain the edge file. A ``needs_human`` edge returns immediately
           (the deterministic ``[/user]`` route). A ``turn_end`` (parent
           ``Stop``, never ``SubagentStop`` — Practice 5) completes the turn;
           the final message is read from the payload's ``transcript_path``.
        2. Do a short liveness/badge read (``read_until_idle`` with a tiny
           budget) so ``on_pty_read`` and byte accounting stay live.
        3. If the PTY is dead, drain the edge file ONCE more (Race 2: a late
           ``Stop`` preceding a clean EOF is honored as completion, not a
           crash). A genuine crash (EOF, no ``turn_end``) resumes the session
           via ``--resume`` + a verified ``continue`` (bounded by the
           crash-resume cap → escalate).
        4. Past the outer budget with the PTY alive but silent → ``saw_turn``
           False (the caller reports ``pm_hang``); never an unbounded block.
        """
        start = time.monotonic()
        deadline = start + self._hook_turn_end_wait_s
        crash_attempts = 0
        accumulated_bytes = 0
        last_buffer = ""

        while True:
            # 1. Drain the edge file (level-triggered).
            edges = consumer.poll()
            needs = self._first_new_needs_human(edges, session_id)
            if needs is not None:
                return TurnWaitResult(
                    saw_turn=False,
                    buffer=last_buffer,
                    idle_marker="",
                    elapsed_ms=int((time.monotonic() - start) * 1000),
                    needs_human=needs,
                    accumulated_bytes=accumulated_bytes,
                )
            turn_edge = self._latest_turn_end(edges, session_id)
            if turn_edge is not None:
                return TurnWaitResult(
                    saw_turn=True,
                    buffer=last_buffer,
                    idle_marker="",
                    elapsed_ms=int((time.monotonic() - start) * 1000),
                    transcript_path=turn_edge.transcript_path,
                    accumulated_bytes=accumulated_bytes,
                )

            # 2. Liveness / badge pump (never the completion authority).
            alive = self._pty_alive(pty)
            idle = pty.read_until_idle(min_content_bytes=0, timeout_s=HOOK_POLL_INTERVAL_S)
            last_buffer = idle.turn_buffer or idle.buffer or last_buffer
            accumulated_bytes += len(idle.buffer)
            self._fire_pty_read(last_buffer)

            # 3. Crash detection with a Race-2 re-drain.
            if not alive:
                late = self._latest_turn_end(consumer.poll(), session_id)
                if late is not None:
                    return TurnWaitResult(
                        saw_turn=True,
                        buffer=last_buffer,
                        idle_marker="",
                        elapsed_ms=int((time.monotonic() - start) * 1000),
                        transcript_path=late.transcript_path,
                        accumulated_bytes=accumulated_bytes,
                    )
                crash_attempts += 1
                if crash_attempts > self._crash_resume_cap:
                    logger.error(
                        "[granite-container] %s crashed %d times on one turn — "
                        "escalating (crash-resume cap=%d)",
                        role,
                        crash_attempts,
                        self._crash_resume_cap,
                    )
                    return TurnWaitResult(
                        saw_turn=False,
                        buffer=last_buffer,
                        idle_marker="",
                        elapsed_ms=int((time.monotonic() - start) * 1000),
                        escalated=True,
                        accumulated_bytes=accumulated_bytes,
                    )
                resumed = self._resume_crashed_pty(pty, role)
                if resumed is None:
                    logger.error(
                        "[granite-container] %s crash-resume failed (no resume "
                        "handle or spawn error) — escalating",
                        role,
                    )
                    return TurnWaitResult(
                        saw_turn=False,
                        buffer=last_buffer,
                        idle_marker="",
                        elapsed_ms=int((time.monotonic() - start) * 1000),
                        escalated=True,
                        accumulated_bytes=accumulated_bytes,
                    )
                pty = resumed
                deadline = time.monotonic() + self._hook_turn_end_wait_s  # re-arm
                continue

            # 4. Bounded timeout: alive + quiet + no Stop past the budget.
            if time.monotonic() > deadline:
                logger.warning(
                    "[granite-container] %s no Stop edge within %.0fs while PTY "
                    "alive — reporting no-turn (idle-equivalent hang)",
                    role,
                    self._hook_turn_end_wait_s,
                )
                return TurnWaitResult(
                    saw_turn=False,
                    buffer=last_buffer,
                    idle_marker="",
                    elapsed_ms=int((time.monotonic() - start) * 1000),
                    accumulated_bytes=accumulated_bytes,
                )

    @staticmethod
    def _pty_alive(pty: PTYDriver) -> bool:
        try:
            return bool(pty.isalive())
        except Exception:
            return False

    def _fire_pty_read(self, buffer: str) -> None:
        """Fire the on_pty_read liveness hook (fail-silent)."""
        if self._on_pty_read is not None:
            try:
                self._on_pty_read(buffer)
            except Exception as e:
                logger.debug("[granite-container] on_pty_read hook raised: %s", e)

    @staticmethod
    def _latest_turn_end(edges: list[HookEdge], session_id: str | None) -> HookEdge | None:
        """Return the last parent ``Stop`` edge for this session, or None.

        Filters by ``session_id`` so a Dev/subagent Stop never ends a PM turn.
        ``SubagentStop`` is a distinct edge kind and is never returned here
        (Practice 5 — native disambiguation, no filtering heuristic).
        """
        matching = [
            e
            for e in edges
            if e.kind == TURN_END and (session_id is None or e.session_id == session_id)
        ]
        return matching[-1] if matching else None

    def _first_new_needs_human(
        self, edges: list[HookEdge], session_id: str | None
    ) -> HookEdge | None:
        """Return the first not-yet-seen ``needs_human`` edge for this session."""
        for e in edges:
            if e.kind != NEEDS_HUMAN:
                continue
            if session_id is not None and e.session_id not in (None, session_id):
                continue
            if e.ts in self._needs_human_seen:
                continue
            self._needs_human_seen.add(e.ts)
            return e
        return None

    def _resume_crashed_pty(self, dead_pty: PTYDriver, role: str) -> PTYDriver | None:
        """Resume a crashed PTY's claude session and re-arm it for the turn.

        Captures the ``--resume <uuid>`` handle from the dead PTY, spawns a
        fresh PTYDriver resuming that session (same cwd / model / env / hook
        settings), and verified-submits a ``continue`` nudge (Practice 6
        minimum — a dropped ``continue`` re-wedges the crash path). Swaps the
        container's PTY reference by role. Returns the new PTY, or None if no
        resume handle was captured or the spawn failed (→ caller escalates).
        """
        try:
            resume_uuid = dead_pty.last_resume_uuid()
        except Exception:
            resume_uuid = None
        if not resume_uuid:
            return None
        try:
            dead_pty.close(force=True)
        except Exception:
            pass
        try:
            new_pty = PTYDriver(
                role=role,
                cwd=dead_pty.cwd,
                model=dead_pty._explicit_model,
                env=dead_pty._extra_env,
                settings_path=dead_pty._settings_path,
                resume_uuid=resume_uuid,
            )
            new_pty.spawn()
            new_pty.write(CRASH_RESUME_CONTINUE)
        except Exception as e:
            logger.error("[granite-container] %s crash-resume spawn failed: %s", role, e)
            return None
        if role == "dev":
            self._dev_pty = new_pty
        else:
            self._pm_pty = new_pty
        logger.info(
            "[granite-container] %s resumed via --resume %s after crash; "
            "verified `continue` submitted",
            role,
            resume_uuid,
        )
        return new_pty

    def _needs_human_message(self, edge: HookEdge) -> str:
        """Build the human-facing text for a needs-human edge (best effort).

        Prefers the hook payload's own message (Notification ``message``,
        AskUserQuestion ``tool_input`` question); falls back to the PM's last
        assistant text (the question it just asked) read from the edge's
        transcript_path; then to a generic prompt. Never raises.
        """
        payload = edge.payload or {}
        msg = payload.get("message")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()
        tool_input = payload.get("tool_input")
        if isinstance(tool_input, dict):
            question = tool_input.get("question") or tool_input.get("prompt")
            if isinstance(question, str) and question.strip():
                return question.strip()
        try:
            if edge.transcript_path:
                text = last_assistant_text(edge.transcript_path)
                if text:
                    return text
        except Exception:
            pass
        return "This needs your input to continue — please reply."

    def _deliver_needs_human(self, edge: HookEdge, result: ContainerResult) -> None:
        """Route a needs-human edge to the human via the [/user] callback."""
        message = self._needs_human_message(edge)
        if self._on_user_payload is not None:
            try:
                self._on_user_payload(message)
                result.user_facing_routed = True
            except Exception as e:
                logger.warning(
                    "[granite-container] needs_human delivery via _on_user_payload failed: %s",
                    e,
                )
        result.exit_message = message

    def _deliver_crash_escalation(self, result: ContainerResult) -> None:
        """Deliver the operator-terminal escalation after crash-resume exhaustion."""
        if self._on_user_payload is not None:
            try:
                self._on_user_payload(CRASH_ESCALATION_MESSAGE)
                result.user_facing_routed = True
            except Exception as e:
                logger.warning("[granite-container] crash escalation delivery failed: %s", e)

    # -- Startup phase ----------------------------------------------------

    def _startup_cycle_idle(self, pty: PTYDriver) -> tuple[bool, str, str, str, int]:
        """Startup-phase idle read with a short per-cycle budget.

        Returns (saw_idle, edge_buffer, level_tail, idle_marker, elapsed_ms).

        - edge_buffer = result.buffer: text read during THIS call only (edge-
          triggered). Fed to _handle_startup so a startup event is not re-
          detected and re-answered on every poll cycle.
        - level_tail = result.turn_buffer: level-triggered capture since the
          last write(). Used ONLY for frame capture at failure time -- never
          for the startup-event parser or the plateau fingerprint.

        The plateau fingerprint does NOT read either buffer -- it reads the
        parser's verdict (response) and the idle bools, both stable across
        oscillating-event cycles where write() would reset turn_buffer.
        """
        result = pty.read_until_idle(min_content_bytes=0, timeout_s=STARTUP_CYCLE_TIMEOUT_S)
        level_tail = result.turn_buffer or result.buffer
        return (result.saw_idle, result.buffer, level_tail, result.idle_marker, result.elapsed_ms)

    def _prime_session(
        self, pty: PTYDriver, slash_cmd: str, *, include_user_message: bool = True
    ) -> None:
        """Send the persona-priming slash command to a PTY.

        The slash command body is invisible to the operator (F4);
        the only substrate signal is "did the model respond?". This
        helper sends the slash command and waits for the TUI to
        return to idle TWICE: once for the welcome frame (pre-write),
        and once for the model to actually finish processing the
        prime (post-write). The post-write wait is critical because
        the bypass bar is a persistent footer that is visible WHILE
        the model is still loading — without the post-write wait,
        `_prime_session` returns while the model is still
        "Sprouting…" / "Synthesizing…", and the startup-phase
        loop's idle-break condition (both PTYs idle) fires
        immediately on the stale buffer, racing past the actual
        prime. The steady-state loop then reads the still-stale
        buffer and misclassifies as `unknown`, hitting `pm_hang`
        on the first turn (PR #1612 live run, June 2026).

        Pre-C5 trust dismissal: a fresh PTY in an untrusted cwd
        (e.g., a per-session sandbox tempdir) shows the workspace
        trust dialog as its first paint. The dialog does NOT paint
        the bypass-permissions bar, so the C5 idle heuristic
        cannot recognize it as idle. We loop briefly, looking for
        the trust pattern, and dismiss with "1" (the documented
        response — see `scripts/probe_slash_arguments.py:241-247`).
        This converts a 60s silent stall into a <2s dismiss + a
        normal C5 wait.

        `include_user_message` controls whether `self.user_message` is
        appended to the slash command as $ARGUMENTS. The PM prime
        includes it (the PM needs the task context to plan the work);
        the Dev prime does NOT — the Dev must wait for the operator to
        relay the PM's first [/dev] instruction, not start work
        immediately on its own from the raw user message (issue #1644).
        """
        for _ in range(5):
            result = pty.read_until_idle(
                min_content_bytes=0,
                timeout_s=PRIME_TRUST_DISMISS_TIMEOUT_S,
            )
            if result.saw_idle:
                break
            if TRUST_FOLDER_RE.search(result.buffer):
                pty.write("1")
                # TUI may re-render briefly after dismissal; loop
                # and re-read until C5 idle or the buffer changes
                # shape (then fall through to the C5 wait).
                continue
            # No trust pattern, no idle — the TUI may be still
            # painting its first frame. Fall through to the C5
            # wait, which has the full prime budget.
            break
        # Wait for the TUI's initial idle (no content floor — the
        # first paint is the welcome frame, not a response).
        pty.read_until_idle(min_content_bytes=0, timeout_s=PRIME_PRE_WRITE_TIMEOUT_S)
        # Send the slash command. The PM prime appends self.user_message
        # so the PM immediately has the task context. The Dev prime
        # sends the slash command alone — Dev must wait for the
        # operator's first relay of the PM's [/dev] instruction
        # (issue #1644: Dev self-starting on the raw user message
        # raced ahead of the PM before any [/dev] routing decision).
        if include_user_message:
            pty.write(f"{slash_cmd} {self.user_message}")
        else:
            pty.write(slash_cmd)
        # Wait for the model to actually finish the prime. The
        # quiescence gate in read_until_idle blocks idle declaration
        # while the TUI is still painting (spinner animation /
        # streaming response repaint at >=1 Hz), so this read waits
        # for the model's response to settle (or times out at
        # PRIME_POST_WRITE_TIMEOUT_S).
        #
        # The content floor is critical: without it, the bypass-
        # permissions bar (a persistent footer) satisfies the C5
        # idle heuristic on the stale pre-write buffer, and
        # `_prime_session` returns while the model is still
        # processing the slash command. The pre-write C5 read
        # already declared idle on the welcome frame; the post-
        # write read needs a content floor that proves the model
        # actually produced response content.
        post = pty.read_until_idle(
            min_content_bytes=PRIME_POST_WRITE_MIN_CONTENT_BYTES,
            timeout_s=PRIME_POST_WRITE_TIMEOUT_S,
        )
        logger.info(
            "container: prime post-write wait saw_idle=%s buffer_len=%d elapsed_ms=%d",
            post.saw_idle,
            len(post.buffer),
            post.elapsed_ms,
        )

    def run(self) -> ContainerResult:
        """Run the steady-state loop end-to-end.

        Returns a `ContainerResult` with the per-turn trace, exit
        reason, byte counts, and resume UUID (if any). The caller
        writes the result to JSON for the results doc.
        """
        session_id = uuid.uuid4().hex[:12]
        result = ContainerResult(session_id=session_id, user_message=self.user_message)

        try:
            self._spawn_pair()
        except Exception as e:
            result.exit_reason = "exception"
            result.exit_message = _truncate_exit_message(f"spawn failed: {e}")
            # Reap any partially-spawned PTY children — iterate BOTH PTYs
            for _pty in [self._pm_pty, self._dev_pty]:
                if _pty is not None:
                    _pid = getattr(_pty, "pid", None)
                    if _pid is not None:
                        try:
                            _pgid = os.getpgid(_pid)
                            if _pty.isalive():
                                os.killpg(_pgid, signal.SIGTERM)
                                time.sleep(0.5)
                                if _pty.isalive():
                                    os.killpg(_pgid, signal.SIGKILL)
                        except (ProcessLookupError, PermissionError, OSError) as _e:
                            logger.debug(
                                "Could not kill PTY pid %s during spawn cleanup: %s",
                                _pid,
                                _e,
                            )
            return result

        logger.info(
            "container: spawned pair (cwd=%s)",
            self.cwd or "<sandbox>",
        )

        # Capture PTY identity (PIDs + deterministic transcript paths).
        # The effective cwd is the container's cwd or the sandbox tempdir.
        effective_cwd = self.cwd or (self._sandbox[0] if self._sandbox else "")
        _capture_pty_identity(result, self._pm_pty, self._dev_pty, effective_cwd)

        try:
            # Persona priming.
            # PM receives the user_message as $ARGUMENTS so it has full
            # task context immediately. Dev also receives the user_message
            # as $ARGUMENTS (background context only — prime-dev-role.md
            # instructs Dev to wait for the operator's [/dev] relay before
            # acting on it; issue #1692). The background context lets Dev
            # understand the user's intent when the PM's [/dev] instruction
            # arrives, without Dev self-starting (issue #1644 guard lives in
            # the prime text, not in the omission of the message).
            logger.info("container: priming PM")
            _pm_prime_cmd = _resolve_pm_prime_cmd(self._session_type)
            self._prime_session(self._pm_pty, _pm_prime_cmd, include_user_message=True)
            logger.info("container: PM prime done")
            logger.info("container: priming Dev")
            self._prime_session(self._dev_pty, DEV_PRIME_SLASH_CMD, include_user_message=True)
            logger.info("container: Dev prime done; entering startup loop")

            # Startup-phase loop. Watch both PTYs for known startup
            # events (trust-folder, update notice — the parser
            # dismisses them) and keep cycling on short 3s reads
            # until BOTH PTYs reach idle. The persona load on Opus
            # high-effort can run minutes past the prime post-write
            # budget, so a slow cold start simply keeps polling here
            # cheaply until the TUIs settle. If they never settle
            # within STARTUP_HARD_CEILING_S, the run exits
            # `startup_unresolved` — the distinct failure signature
            # plan Risk 6 relies on for a broken `--permission-mode`
            # flag (the bypass bar never paints, so idle never
            # fires).
            startup_settled = False
            startup_deadline = time.monotonic() + STARTUP_HARD_CEILING_S
            cycle = 0
            # Plateau detection state.
            # Fingerprint is the parser's verdict (response) ALONE. The idle bools
            # are NOT included in the fingerprint key because they can flicker on
            # an oscillating event path (write() resets turn_buffer so the next
            # edge read may not see idle immediately), which would break N-consecutive
            # accumulation. Silent-start detection uses a separate explicit sentinel
            # below (response is None and BOTH idle bools are False).
            _plateau_last_response: object = object()  # sentinel: "not set yet"
            _plateau_count: int = 0
            # Last-seen level tails for frame capture (updated every cycle).
            _last_pm_level_tail: str = ""
            _last_dev_level_tail: str = ""

            while time.monotonic() < startup_deadline:
                pm_idle = self._startup_cycle_idle(self._pm_pty)
                dev_idle = self._startup_cycle_idle(self._dev_pty)
                pm_saw_idle, pm_edge, pm_level, pm_marker, pm_ms = pm_idle
                dev_saw_idle, dev_edge, dev_level, dev_marker, dev_ms = dev_idle

                # Update level tails for frame capture at any exit point.
                _last_pm_level_tail = pm_level.strip() or pm_edge
                _last_dev_level_tail = dev_level.strip() or dev_edge

                response = self._handle_startup(pm_edge, dev_edge)
                logger.info(
                    "container: startup cycle=%d pm_idle=%s dev_idle=%s response=%r",
                    cycle,
                    pm_saw_idle,
                    dev_saw_idle,
                    response,
                )

                # --- BYOB /login re-auth recovery dispatch (issue #1750) ---
                # _handle_startup set self._login_pty when it matched a
                # LOGIN_PROMPT (B2: PM or Dev, never hardcoded). Non-blocking,
                # idempotent dispatch: spawn exactly one recovery thread the
                # first time, then let it run in flight across subsequent
                # cycles. On completion, recover_login has already written the
                # PTY response (Enter / pasted code) itself for both flows, so
                # success means: stop treating the persisting frame as a
                # plateau and let idle detection settle. On failure, fall
                # through to the existing ceiling/alert path.
                if self._login_pty is not None and not self._recovery_launched:
                    logger.info("container: LOGIN_PROMPT detected; dispatching BYOB login recovery")
                    self._dispatch_login_recovery(startup_deadline)
                # Record the observability event exactly once when the recovery
                # completion is first observed (C1: check is_set() before
                # dereferencing the outcome — no torn/stale read).
                if (
                    self._recovery_launched
                    and self._recovery_done.is_set()
                    and not self._recovery_event_recorded
                ):
                    outcome = self._recovery_outcome
                    self._recovery_event_recorded = True
                    result.startup_events.append(
                        {
                            "event": "login_recovery",
                            "outcome": "success"
                            if (outcome is not None and outcome.succeeded)
                            else "failed",
                            "flow": outcome.flow if outcome is not None else None,
                            "reason": outcome.reason if outcome is not None else "no outcome",
                        }
                    )
                    logger.info(
                        "container: login recovery completed outcome=%s flow=%s reason=%s",
                        "success" if (outcome is not None and outcome.succeeded) else "failed",
                        outcome.flow if outcome is not None else None,
                        outcome.reason if outcome is not None else "no outcome",
                    )

                # --- Plateau fingerprint (keyed on response ALONE) ---
                # Accumulate consecutive identical response values.
                # Reset on any change. This captures:
                #   (a) oscillating event: same non-None response every cycle
                #   (b) silent-start: see explicit sentinel below
                if response == _plateau_last_response:
                    _plateau_count += 1
                else:
                    _plateau_last_response = response
                    _plateau_count = 1

                # Explicit silent-start sentinel: response is None but neither
                # PTY has reached idle -- the startup loop is spinning with no
                # progress AND no recognized event.
                _silent_start = response is None and not pm_saw_idle and not dev_saw_idle

                # B1 — suppress the plateau early-bail WHILE recovery is in
                # flight (issue #1750). A running BYOB recovery produces exactly
                # the plateau signature (response=None, neither PTY idle as the
                # login frame persists), so the detector would reap it ~90s
                # before the 120s recovery deadline. Gate on
                # _recovery_done.is_set() (NOT _recovery_outcome is None, which
                # would reintroduce the C1 torn-read race). RECOVERY_HARD_DEADLINE_S
                # (120s) stays strictly under STARTUP_HARD_CEILING_S (600s), so
                # the outer ceiling never reaps a pending recovery either.
                _recovery_in_flight = self._recovery_launched and not self._recovery_done.is_set()

                if (
                    _plateau_count >= STARTUP_PLATEAU_CYCLES
                    and _silent_start
                    and not _recovery_in_flight
                ):
                    # Confirmed plateau: N consecutive identical response=None
                    # cycles with no idle. Bail early.
                    frame = _capture_startup_frame(
                        _last_pm_level_tail, _last_dev_level_tail, "plateau", _plateau_count
                    )
                    logger.error(
                        "[granite-container] startup plateau detected: "
                        "cycle=%d plateau_cycles=%d bailing early",
                        cycle,
                        _plateau_count,
                    )
                    result.exit_reason = "startup_unresolved"
                    result.exit_message = (
                        f"startup plateau: {_plateau_count} consecutive identical "
                        f"no-progress cycles (response=None, neither PTY idle); "
                        f"bailed at cycle {cycle}"
                    )
                    result.startup_failure_kind = "plateau"
                    result.startup_diagnostic_frame = frame
                    result.startup_plateau_cycles = _plateau_count
                    return result

                if response is None:
                    # No startup event in this window -- break if
                    # both PTYs are idle, otherwise keep watching.
                    if pm_saw_idle and dev_saw_idle:
                        logger.info("container: startup both idle, breaking")
                        startup_settled = True
                        break
                    cycle += 1
                    continue
                # The parser chose a startup event; respond. For
                # trust-folder, "1" is the dismissal. For update
                # notice, the response is "\r" (Enter to dismiss).
                if response:
                    # Pick the PTY that produced the event. Without
                    # a per-PTY tag, we send to PM (the first to
                    # reach the prompt in most sessions). This is
                    # a heuristic; a more rigorous version would
                    # track which PTY the parser saw the event on.
                    self._pm_pty.write(response)
                result.startup_events.append({"cycle": cycle, "response": response})
                cycle += 1
            if not startup_settled:
                # Ceiling exit: capture the frame for the diagnostic artifact.
                frame = _capture_startup_frame(
                    _last_pm_level_tail, _last_dev_level_tail, "ceiling", cycle
                )
                result.exit_reason = "startup_unresolved"
                result.exit_message = (
                    f"startup did not settle within {STARTUP_HARD_CEILING_S:.0f}s "
                    f"hard ceiling ({cycle} cycles)"
                )
                result.startup_failure_kind = "ceiling"
                result.startup_diagnostic_frame = frame
                return result

            # Prime-turn relay (issue #1644): after both primes complete,
            # read PM's prime-turn buffer and route it through
            # _route_pm_classification. PM may already have decided the
            # destination (user/complete/dev) during its prime response
            # rather than waiting for the first steady-state idle.
            pm_transcript = result.pm_transcript_path
            # Snapshot the count of text-bearing PM assistant entries before the
            # idle read so last_assistant_text can require a NEW text-bearing
            # entry this cycle (content-identity guard; immune to intra-turn
            # tool_use/tool_result writes that defeated the old mtime guard).
            pm_prime_baseline = text_bearing_count(pm_transcript) if pm_transcript else 0
            prime_wait = self._cycle_turn(
                self._pm_pty, self._pm_consumer, self._pm_session_id, role="pm"
            )
            pm_prime_buf = prime_wait.buffer
            if prime_wait.needs_human is not None:
                self._deliver_needs_human(prime_wait.needs_human, result)
                result.exit_reason = "pm_user"
            elif prime_wait.escalated:
                self._deliver_crash_escalation(result)
                result.exit_reason = "exception"
                result.exit_message = "crash-resume cap exhausted during prime turn"
            elif not prime_wait.saw_turn:
                result.exit_reason = "pm_hang"
                result.exit_message = (
                    f"PM did not reach turn-end after prime within {CYCLE_IDLE_TIMEOUT_S}s"
                )
            else:
                result.total_pm_pty_bytes += prime_wait.accumulated_bytes or len(pm_prime_buf)
                # Read PM's last assistant text verbatim from the JSONL
                # transcript (zero-LLM path: no classify on painted pm_buf).
                prime_read_path = prime_wait.transcript_path or pm_transcript
                pm_prime_text = (
                    last_assistant_text(prime_read_path, baseline_text_count=pm_prime_baseline)
                    if prime_read_path
                    else ""
                )
                if pm_prime_text:
                    prime_classification = classify_pm_prefix(pm_prime_text)
                else:
                    _log_transcript_read_diagnostic(
                        "prime-turn", pm_transcript, self._pm_pty, self._dev_pty
                    )
                    result.transcript_fallback_count += 1
                    prime_classification = _unknown_classification()
                if self._on_turn is not None:
                    try:
                        self._on_turn()
                    except Exception as e:
                        logger.warning("[granite-container] on_turn callback raised: %s", e)
                prime_outcome = self._route_pm_classification(
                    prime_classification, pm_prime_buf, turn_index=-1, result=result
                )
                if prime_outcome.should_break:
                    result.exit_reason = prime_outcome.exit_reason or result.exit_reason
                else:
                    # PM's prime turn was routed to Dev (or was unknown/
                    # empty-dev). Set the stale-buffer guard so the first
                    # steady-state iteration forces a fresh _cycle_idle
                    # before classifying (prevents re-reading the prime
                    # buffer on the very first turn).
                    self._prime_relayed = True
                    self._prime_pm_buf_hash = hash(pm_prime_buf)

            # Steady state.
            if result.exit_reason == "in_progress":
                for turn in range(self.max_turns):
                    # Stale-buffer guard (issue #1644): on the first
                    # iteration after the prime-turn relay, force a
                    # fresh idle read so we classify genuinely new PM
                    # output rather than the already-processed prime
                    # buffer.
                    if turn == 0 and self._prime_relayed and self._prime_pm_buf_hash is not None:
                        guard_idle, guard_buf, _, _ = self._cycle_idle(self._pm_pty)
                        if guard_idle and hash(guard_buf) == self._prime_pm_buf_hash:
                            # PM output is unchanged from the already-processed
                            # prime buffer; fall through to the fresh idle read
                            # below so we classify genuinely new output on the
                            # next cycle rather than re-processing stale content.
                            logger.info(
                                "container: prime stale-buffer guard fired — "
                                "PM buffer unchanged; falling through to fresh idle read"
                            )

                    # Mid-run steering injection (Part 1, issue #1779). Drain any
                    # pending steering messages and inject them into PM's PTY before
                    # PM's next classification. The drain is fail-silent (a raising
                    # callback must never crash the loop, mirroring _on_turn).
                    drained: list[dict] = []
                    if self._poll_steering is not None:
                        try:
                            drained = self._poll_steering() or []
                        except Exception as e:
                            logger.warning(
                                "[granite-container] poll_steering callback raised: %s", e
                            )
                            drained = []
                    if drained:
                        # An abort message takes precedence: deliver the fixed
                        # user-facing string and break cleanly (steer_abort). The
                        # delivery MUST precede the break — output emitted after the
                        # break is dropped (Task 2b strict ordering contract).
                        if any(m.get("is_abort") for m in drained):
                            if self._on_user_payload is not None:
                                try:
                                    self._on_user_payload(STEER_ABORT_USER_MESSAGE)
                                    result.user_facing_routed = True
                                except Exception as e:
                                    logger.warning(
                                        "[granite-container] steer_abort delivery via "
                                        "_on_user_payload failed: %s",
                                        e,
                                    )
                            result.exit_reason = "steer_abort"
                            result.exit_message = STEER_ABORT_USER_MESSAGE
                            break

                        # Non-abort steering: cycle PM to idle BEFORE writing so the
                        # injection lands as a fresh user turn and does NOT interrupt
                        # PM's in-flight tool execution (_cycle_idle waits for PM to
                        # finish whatever it is doing). This ordering is the whole
                        # guarantee of "steering does not corrupt PM's current turn".
                        steer_idle, _, _, _ = self._cycle_idle(self._pm_pty)
                        if not steer_idle:
                            # PM is wedged. The messages were already atomically
                            # LPOP'd from Redis, so they are lost — no re-queue
                            # (that would re-introduce the cross-process race). Log
                            # the loss so an operator can re-deliver via
                            # `valor-session steer`.
                            logger.warning(
                                "pm_hang during steering injection — %d msg(s) lost: %r",
                                len(drained),
                                [m.get("text") for m in drained],
                            )
                            result.exit_reason = "pm_hang"
                            result.exit_message = (
                                f"PM did not reach idle within {CYCLE_IDLE_TIMEOUT_S}s "
                                "during steering injection"
                            )
                            break
                        for m in drained:
                            text = (m.get("text") or "").strip()
                            if not text:
                                # Skip empty/whitespace steering messages.
                                continue
                            sender = m.get("sender") or "operator"
                            self._pm_pty.write(f"\n[Steering from {sender}]: {text}\n")
                        # Fall through to the existing per-turn idle read below, which
                        # (via the content-identity pm_baseline guard) captures PM's
                        # NEW response to the steering and routes it normally.

                    # Snapshot the count of text-bearing PM assistant entries
                    # before the idle read so last_assistant_text can require a
                    # NEW text-bearing entry this cycle (content-identity guard).
                    pm_baseline = text_bearing_count(pm_transcript) if pm_transcript else 0

                    # Wait for the PM turn to complete. Hook-driven (default):
                    # the parent Stop edge is the authority (#1688); the idle
                    # heuristic is the documented fallback. The needs-human and
                    # crash-escalation edges are handled before classification.
                    wait = self._cycle_turn(
                        self._pm_pty, self._pm_consumer, self._pm_session_id, role="pm"
                    )
                    pm_buf = wait.buffer
                    if wait.needs_human is not None:
                        # Deterministic [/user] route — no classifier guess.
                        self._deliver_needs_human(wait.needs_human, result)
                        result.exit_reason = "pm_user"
                        break
                    if wait.escalated:
                        self._deliver_crash_escalation(result)
                        result.exit_reason = "exception"
                        result.exit_message = "crash-resume cap exhausted"
                        break
                    if not wait.saw_turn:
                        result.exit_reason = "pm_hang"
                        result.exit_message = (
                            f"PM did not reach turn-end within {CYCLE_IDLE_TIMEOUT_S}s"
                        )
                        break

                    result.total_pm_pty_bytes += wait.accumulated_bytes or len(pm_buf)

                    # Read PM's last assistant text verbatim from the JSONL
                    # transcript (zero-LLM: classify on transcript, not
                    # painted PTY buffer pm_buf). The hook edge's transcript_path
                    # (the Stop payload) is preferred — it is the flush-safe
                    # source confirmed by the Stop; falls back to the precomputed
                    # session transcript on the idle path.
                    read_path = wait.transcript_path or pm_transcript
                    pm_text = (
                        last_assistant_text(read_path, baseline_text_count=pm_baseline)
                        if read_path
                        else ""
                    )
                    if pm_text:
                        classification = classify_pm_prefix(pm_text)
                    else:
                        _log_transcript_read_diagnostic(
                            f"steady-state turn {turn}",
                            pm_transcript,
                            self._pm_pty,
                            self._dev_pty,
                        )
                        result.transcript_fallback_count += 1
                        classification = _unknown_classification()
                    # Per-turn progress hook (TD1): every classified PM
                    # turn counts as progress for the two-tier no-progress
                    # detector, regardless of destination.
                    if self._on_turn is not None:
                        try:
                            self._on_turn()
                        except Exception as e:
                            logger.warning(
                                "[granite-container] on_turn callback raised: %s",
                                e,
                            )

                    outcome = self._route_pm_classification(
                        classification, pm_buf, turn_index=turn, result=result
                    )
                    if outcome.should_break:
                        result.exit_reason = outcome.exit_reason or result.exit_reason
                        break

                # If we ran out the for loop without breaking, max_turns
                # is the exit reason.
                if result.exit_reason == "in_progress":
                    result.exit_reason = "pm_max_turns"
                    result.exit_message = (
                        f"reached max_turns={self.max_turns} without a [/complete]"
                    )

            # Wrap-up guard (issue #1647): when the run is in a
            # successful-shaped terminal state but PM never delivered a
            # user-facing message, drive PM to produce one. This
            # guarantees the human always receives some output.
            # Invariant: failure exit_reasons (dev_hang, pm_hang, exception,
            # startup_unresolved, pm_no_user_message) never reach this gate —
            # they are set in the except/break paths above and never end up in
            # _wrapup_eligible_exits. No runtime sticky-failed guard is needed.
            # NOTE: _wrapup_eligible_exits is the TRIGGER set (wraps up when
            # user_facing_routed=False); it is distinct from _CLEAN_GRANITE_EXIT_REASONS
            # in session_executor (the clean-exit gate used for REACTION_ERROR routing).
            _wrapup_eligible_exits = {
                "pm_complete",
                "pm_user",
                "pm_max_turns",
                "pm_floor_delivered",
            }
            if result.exit_reason in _wrapup_eligible_exits and not result.user_facing_routed:
                self._run_wrapup_guard(result)

        except Exception as e:
            result.exit_reason = "exception"
            result.exit_message = _truncate_exit_message(f"{type(e).__name__}: {e}")
        finally:
            # Try to capture a resume UUID from the dying PM.
            try:
                if self._pm_pty is not None and self._pm_pty.isalive():
                    result.resume_uuid = self._pm_pty.last_resume_uuid()
            except Exception:
                pass
            self._close_pair_and_reap()

        return result

    # -- Routing helper ---------------------------------------------------

    def _route_pm_classification(
        self,
        classification: Any,
        pm_buf: str,
        turn_index: int,
        result: ContainerResult,
    ) -> RouteOutcome:
        """Route a single classified PM turn and update result in place.

        Handles all four routing destinations:
          - unknown / empty-dev: compliance miss — re-prompt PM with
            PM_COMPLIANCE_NUDGE and return (should_break=False). The
            COMPLIANCE_NUDGE is only for genuine mid-loop misses;
            the wrap-up path does not call this with unknown turns.
          - complete (non-empty): deliver via on_complete_payload,
            set result.user_facing_routed=True, return should_break=True.
          - complete (empty): NOT user-facing — falls through to wrap-up
            guard instead of delivering. Returns should_break=True with
            pm_complete so the loop exits; user_facing_routed stays False.
          - user: deliver via on_user_payload, set
            result.user_facing_routed=True, return should_break=True.
          - dev: forward classification.payload verbatim to Dev PTY,
            cycle Dev idle, read Dev transcript text, write to PM PTY,
            capture self._last_dev_report. Returns should_break=False.

        All exits (complete/user) set result.exit_reason before
        returning. The caller sets result.exit_reason from
        outcome.exit_reason only when should_break=True.
        """
        if classification.compliance_miss:
            result.classification_compliance_misses += 1

        if classification.destination == "unknown":
            result.parse_failures += 1
            turn_record = TurnRecord(
                turn_index=turn_index,
                pm_idle_ms=0,
                dev_idle_ms=0,
                classification="unknown",
                compliance_miss=classification.compliance_miss,
                pm_first_line=classification.raw_first_line,
                routed_payload_chars=0,
                pm_idle_marker="",
                dev_idle_marker="",
            )
            result.turns.append(turn_record)
            self._pm_pty.write(PM_COMPLIANCE_NUDGE)
            return RouteOutcome(should_break=False, exit_reason=None)

        if classification.destination == "complete":
            payload = classification.payload
            turn_record = TurnRecord(
                turn_index=turn_index,
                pm_idle_ms=0,
                dev_idle_ms=0,
                classification="complete",
                compliance_miss=classification.compliance_miss,
                pm_first_line=classification.raw_first_line,
                routed_payload_chars=len(payload),
                pm_idle_marker="",
                dev_idle_marker="",
            )
            result.turns.append(turn_record)
            result.exit_message = payload
            if payload.strip():
                # Non-empty [/complete] — deliver to user and mark routed.
                if self._on_complete_payload is not None:
                    try:
                        self._on_complete_payload(payload)
                        result.user_facing_routed = True
                    except Exception as e:
                        logger.warning(
                            "[granite-container] on_complete_payload callback raised: %s",
                            e,
                        )
            # Empty [/complete] is NOT user-facing — user_facing_routed
            # stays False and the wrap-up guard will drive PM to produce
            # a real summary.
            return RouteOutcome(should_break=True, exit_reason="pm_complete")

        if classification.destination == "user":
            payload = classification.payload
            turn_record = TurnRecord(
                turn_index=turn_index,
                pm_idle_ms=0,
                dev_idle_ms=0,
                classification="user",
                compliance_miss=classification.compliance_miss,
                pm_first_line=classification.raw_first_line,
                routed_payload_chars=len(payload),
                pm_idle_marker="",
                dev_idle_marker="",
            )
            result.turns.append(turn_record)
            result.exit_message = payload
            if self._on_user_payload is not None:
                try:
                    self._on_user_payload(payload)
                    result.user_facing_routed = True
                except Exception as e:
                    logger.warning(
                        "[granite-container] on_user_payload callback raised: %s",
                        e,
                    )
            return RouteOutcome(should_break=True, exit_reason="pm_user")

        # destination == "dev" — forward the verbatim payload to Dev.
        # classification.payload is the PM transcript text following the
        # [/dev] prefix token; it is forwarded verbatim (no LLM rewrite).
        dev_prompt = classification.payload
        if not dev_prompt.strip():
            # PM emitted [/dev] but no payload; compliance miss —
            # re-prompt PM so the next read sees fresh output.
            result.parse_failures += 1
            turn_record = TurnRecord(
                turn_index=turn_index,
                pm_idle_ms=0,
                dev_idle_ms=0,
                classification="unknown",
                compliance_miss=True,
                pm_first_line=classification.raw_first_line,
                routed_payload_chars=0,
                pm_idle_marker="",
                dev_idle_marker="",
            )
            result.turns.append(turn_record)
            self._pm_pty.write(PM_COMPLIANCE_NUDGE)
            return RouteOutcome(should_break=False, exit_reason=None)

        # [/dev:steer] mid-task steering (Part 2, issue #1779). The classifier
        # parses [/dev:steer] into harness="steer" — a RESERVED suffix that is NOT
        # a real builder harness. Intercept it here, before _get_builder (which
        # must never receive "steer"), and write the (token-stripped) payload
        # straight to the Dev PTY as a Dev turn WITHOUT blocking on Dev idle:
        # write-and-continue semantics — Dev picks it up on its next read (Risk 2).
        if getattr(classification, "harness", None) == STEER_HARNESS_SUFFIX:
            # Defensive token-strip: the single-line form `[/dev:steer] fix X`
            # fails the strict PREFIX_TOKEN_RE, falls to the fallback, and returns
            # the WHOLE tail INCLUDING the literal token as payload. Writing that
            # verbatim would poison Dev's instruction. The re.sub is a no-op for
            # the strict (already token-free) path and removes the leaked token
            # for the single-line fallback path.
            clean = re.sub(r"\[/dev:steer\]\s*", "", dev_prompt, count=1).strip()
            if not clean:
                # Token-only [/dev:steer] is a no-op steer — nudge PM (composes
                # with the empty-[/dev] guard above).
                self._pm_pty.write(PM_COMPLIANCE_NUDGE)
                return RouteOutcome(should_break=False, exit_reason=None)
            # Submit the steer as a Dev turn (no _get_builder, no Dev-idle wait).
            self._dev_pty.write(clean + "\n")
            # Write a one-line continuation ack to PM so it produces its next turn
            # rather than hanging on an empty idle read (Risk 3).
            self._pm_pty.write(PM_DEV_STEER_ACK)
            turn_record = TurnRecord(
                turn_index=turn_index,
                pm_idle_ms=0,
                dev_idle_ms=0,
                classification="dev_steer",
                compliance_miss=classification.compliance_miss,
                pm_first_line=classification.raw_first_line,
                routed_payload_chars=len(clean),
                pm_idle_marker="",
                dev_idle_marker="",
            )
            result.turns.append(turn_record)
            return RouteOutcome(should_break=False, exit_reason=None)

        # Resolve the builder harness for this turn.
        # harness_name is None → default claude PTY path; unknown names
        # route a compliance nudge back to PM.
        harness_name = getattr(classification, "harness", None)
        builder = self._get_builder(harness_name, result)
        if builder is None:
            # Unknown harness — _get_builder already wrote the nudge.
            return RouteOutcome(should_break=False, exit_reason=None)

        # Delegate the dev turn to the builder harness.
        # The builder performs: cycle_idle(dev) → write → baseline → cycle_idle(dev)
        # → last_assistant_text. It stores per-turn metadata as attributes.
        dev_text = builder.run_turn(dev_prompt)

        # Check for hang (pre-write or post-write idle timeout).
        if builder.last_hung:
            result.exit_message = "Dev did not reach idle within the cycle idle budget"
            return RouteOutcome(should_break=True, exit_reason="dev_hang")

        # Account for bytes captured from Dev's PTY buffer.
        result.total_dev_pty_bytes += len(builder.last_dev_buf)

        # Container-owned: empty-return fallback gate (Risk 5 — stays here,
        # not in the builder). If the transcript read returned empty, bump
        # the fallback count and substitute a placeholder so PM can continue.
        if not dev_text:
            logger.warning(
                "[granite-container] Dev transcript read returned empty; "
                "falling back to transcript_fallback_count bump"
            )
            result.transcript_fallback_count += 1
            # Still write something to PM so the loop can continue.
            dev_text = DEV_REPORT_UNAVAILABLE

        # Container-owned: capture the Dev text as the last Dev report for
        # the wrap-up guard (issue #1647). Stays here, not in the builder.
        self._last_dev_report = dev_text

        # Write Dev's verbatim text to PM's PTY, with a per-turn prefix-
        # contract reminder appended (issue #1719). Restores the per-turn
        # contract assertion lost when --append-system-prompt was removed in
        # #1694. The reminder is a single line; token cost is negligible.
        await_pm, _, _, _ = self._cycle_idle(self._pm_pty)
        if not await_pm:
            result.exit_message = "PM did not reach idle before Dev report"
            return RouteOutcome(should_break=True, exit_reason="pm_hang")
        self._pm_pty.write(dev_text + PM_TURN_CONTRACT_REMINDER)

        turn_record = TurnRecord(
            turn_index=turn_index,
            pm_idle_ms=0,
            dev_idle_ms=builder.last_dev_ms,
            classification="dev",
            compliance_miss=classification.compliance_miss,
            pm_first_line=classification.raw_first_line,
            routed_payload_chars=len(dev_prompt),
            pm_idle_marker="",
            dev_idle_marker=builder.last_dev_marker,
        )
        result.turns.append(turn_record)
        return RouteOutcome(should_break=False, exit_reason=None)

    def _get_builder(
        self,
        harness: str | None,
        result: ContainerResult,
    ) -> PtyClaudeBuilder | PiSubprocessBuilder | None:
        """Resolve a BuilderHarness for the given harness name.

        Returns a ``PtyClaudeBuilder`` for ``None`` or ``"claude"`` (the
        default), a ``PiSubprocessBuilder`` for ``"pi"``.  For unknown
        harness names, writes a compliance nudge to PM and returns ``None``
        — the caller must return ``RouteOutcome(should_break=False)``
        immediately.

        The ``PtyClaudeBuilder`` is constructed fresh per call so that the
        ``dev_transcript_getter`` lambda always captures the current
        ``result.dev_transcript_path`` value (which may change between turns
        if the PTY restarts).

        ``PiSubprocessBuilder`` is also constructed fresh per call; it
        receives ``builder_cwd = self._dev_pty.cwd`` — the same directory
        the claude Dev PTY runs in — grounded across both the prewarmed-pool
        and self-spawned paths.  A falsy cwd raises inside the constructor
        (Risk 6: never spawn Pi with cwd=None / repo root).
        """
        if harness is None or harness == "claude":
            return PtyClaudeBuilder(
                dev_pty=self._dev_pty,
                dev_transcript_getter=lambda: result.dev_transcript_path,
                cycle_idle_fn=self._cycle_idle,
            )
        if harness == "pi":
            return PiSubprocessBuilder(
                builder_cwd=self._dev_pty.cwd,
                rails_path=PI_RAILS_PATH,
                persona_path=PI_PERSONA_PATH,
            )
        # Unknown harness — PM sent [/dev:unknown_name] which the container
        # cannot fulfil.  Nudge PM to use a known harness.
        logger.warning(
            "[granite-container] Unknown builder harness %r — nudging PM",
            harness,
        )
        self._pm_pty.write(PM_COMPLIANCE_NUDGE)
        return None

    # -- Wrap-up guard (issue #1647) --------------------------------------

    def _run_wrapup_guard(self, result: ContainerResult) -> None:
        """Drive PM to produce a user-facing message when none was delivered.

        Called when the run exits in a successful-shaped state
        (pm_complete, pm_user, pm_max_turns, pm_floor_delivered) but
        result.user_facing_routed is still False. The guard:
          1. Builds a seed from self._last_dev_report (or a fresh Dev
             idle read + summarize, or DEV_REPORT_UNAVAILABLE).
          2. Writes PM_WRAPUP_PROMPT to PM's PTY.
          3. Cycles PM idle and routes via _route_pm_classification
             (capped at MAX_WRAPUP_ATTEMPTS=1).
          4. If PM still hasn't delivered, sends OPERATOR_TERMINAL_MESSAGE
             directly via on_user_payload so the human always gets something.

        Mutates result in place. All errors are swallowed — the wrap-up
        guard must never crash the run.
        """
        try:
            # Build the seed report from the last Dev transcript text
            # (zero-LLM: no summarize_for_pm rewrite).
            if self._last_dev_report:
                seed = self._last_dev_report
            else:
                dev_transcript = result.dev_transcript_path
                seed = (
                    last_assistant_text(dev_transcript) or DEV_REPORT_UNAVAILABLE
                    if dev_transcript
                    else DEV_REPORT_UNAVAILABLE
                )
                if seed == DEV_REPORT_UNAVAILABLE:
                    logger.warning(
                        "[granite-container] wrap-up guard: Dev transcript empty/missing; "
                        "using DEV_REPORT_UNAVAILABLE seed"
                    )
                    result.transcript_fallback_count += 1

            for _attempt in range(MAX_WRAPUP_ATTEMPTS):
                # Write the wrap-up prompt to PM.
                await_pm, _, _, _ = self._cycle_idle(self._pm_pty)
                if not await_pm:
                    logger.warning("[granite-container] wrap-up guard: PM hang waiting for idle")
                    break
                self._pm_pty.write(PM_WRAPUP_PROMPT.format(seed=seed))

                # Snapshot the count of text-bearing PM assistant entries before
                # the cycle so last_assistant_text can require a NEW text-bearing
                # entry this cycle (content-identity guard).
                pm_transcript = result.pm_transcript_path
                pm_baseline = text_bearing_count(pm_transcript) if pm_transcript else 0

                # Wait for PM to respond.
                pm_idle, pm_buf, _, _ = self._cycle_idle(self._pm_pty)
                if not pm_idle:
                    logger.warning("[granite-container] wrap-up guard: PM hung after wrapup prompt")
                    break

                # Read PM's last assistant text verbatim from the JSONL
                # transcript (zero-LLM path: no ollama classify on pm_buf).
                pm_text = (
                    last_assistant_text(pm_transcript, baseline_text_count=pm_baseline)
                    if pm_transcript
                    else ""
                )
                if pm_text:
                    wrapup_classification = classify_pm_prefix(pm_text)
                    if wrapup_classification.destination == "unknown":
                        # Non-empty but prefix-less: deliver directly as a
                        # user message (relaxed floor, issue #1719). Bypasses
                        # _route_pm_classification so PM_COMPLIANCE_NUDGE is
                        # not written into a PTY that is about to be torn down.
                        # OPERATOR_TERMINAL_MESSAGE is reserved for a genuinely
                        # empty transcript (pm_text falsy, handled below).
                        if self._on_user_payload is not None:
                            try:
                                self._on_user_payload(pm_text.strip())
                                result.user_facing_routed = True
                                result.exit_reason = "pm_floor_delivered"
                                logger.info(
                                    "[granite-container] wrap-up guard: floor-delivered "
                                    "prefix-less PM text (exit_reason=pm_floor_delivered)"
                                )
                            except Exception as e:
                                logger.warning(
                                    "[granite-container] wrap-up guard: floor delivery "
                                    "via _on_user_payload failed: %s",
                                    e,
                                )
                        return
                    # Prefix found — route normally via _route_pm_classification.
                    outcome = self._route_pm_classification(
                        wrapup_classification, pm_buf, turn_index=-2, result=result
                    )
                    if result.user_facing_routed:
                        result.exit_reason = outcome.exit_reason or result.exit_reason
                        return
                else:
                    _log_transcript_read_diagnostic(
                        "wrap-up guard", pm_transcript, self._pm_pty, self._dev_pty
                    )
                    result.transcript_fallback_count += 1
                    # Empty transcript — fall through to OPERATOR_TERMINAL_MESSAGE below.

            # PM still silent after MAX_WRAPUP_ATTEMPTS — deliver canned
            # terminal message directly so the human always gets something.
            if not result.user_facing_routed and self._on_user_payload is not None:
                try:
                    self._on_user_payload(OPERATOR_TERMINAL_MESSAGE)
                    result.user_facing_routed = True
                    result.exit_reason = "pm_no_user_message"
                    logger.info(
                        "[granite-container] wrap-up guard delivered OPERATOR_TERMINAL_MESSAGE"
                    )
                except Exception as e:
                    logger.warning(
                        "[granite-container] wrap-up guard: terminal message delivery failed: %s",
                        e,
                    )
        except Exception as e:
            logger.warning("[granite-container] wrap-up guard raised unexpectedly: %s", e)

    # -- Ping-pong (two-PTY coordination) test ---------------------------

    def run_ping_pong_test(self) -> bool:
        """Spawn both PTYs, prime both, ping each in turn.

        The two-PTY coordination test runs BEFORE the granite
        classification layer is added (per the plan's
        *Technical Approach*). If this fails, the multi-PTY idle
        heuristic is broken; do not add the classification layer.

        Returns True if both pings reached idle without a hang.
        """
        try:
            self._spawn_pair()
        except Exception as e:
            logger.warning("ping_pong spawn failed: %s", e)
            return False
        try:
            # NOTE: ping-pong is a PTY idle-heuristic test harness, not a production
            # session. It always uses the standard PM prime (never the teammate prime)
            # for test isolation — session_type routing does not apply here.
            self._prime_session(self._pm_pty, PM_PRIME_SLASH_CMD, include_user_message=True)
            self._prime_session(self._dev_pty, DEV_PRIME_SLASH_CMD, include_user_message=False)
            # Ping each in turn.
            self._pm_pty.write("ping")
            pm_result = self._pm_pty.read_until_idle(min_content_bytes=100, timeout_s=60.0)
            self._dev_pty.write("ping")
            dev_result = self._dev_pty.read_until_idle(min_content_bytes=100, timeout_s=60.0)
            return pm_result.saw_idle and dev_result.saw_idle
        except Exception as e:
            logger.warning("ping_pong failed: %s", e)
            return False
        finally:
            self._close_pair_and_reap()


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------


def result_to_json(result: ContainerResult) -> str:
    """Serialize a ContainerResult to a JSON string for the results doc.

    The shape is `ContainerResult` -> dict -> JSON. The classifier
    is the only place that knows the enums; the container's
    serialization uses the dataclass's asdict.
    """
    payload = asdict(result)
    return json.dumps(payload, indent=2, default=str)
