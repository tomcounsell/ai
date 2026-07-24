"""The claude ``-p`` CLI harness adapter (plan #2000, Phase 2).

All ``claude -p`` subprocess knowledge lives here: argv/env assembly,
stream-json parsing, the stale-UUID and image-dimension retry fallbacks, and
health/turn-input helpers. Extracted byte-identically (Task 2.2's golden
argv/env test) from the pre-extraction ``agent/sdk_client.py`` free
functions -- this module IS the "claude -p" knowledge the rest of the
system used to reach through ``agent.sdk_client``. ``agent/sdk_client.py``
re-exports the public names here for its remaining (non-runner) callers so
this extraction is behavior-preserving; new call sites should import
directly from this module.

:class:`ClaudeHarnessAdapter` is the concrete
:class:`~agent.session_runner.harness.base.HarnessAdapter` the session
runner (``agent/session_runner/role_driver.py``) drives: it wraps the
module-level ``get_response_via_harness`` call, translating its
callback-based side channel (spawn/init/stdout/exit-status/usage) into a
normalized ``TurnResult`` carrying an ``events`` list, while still invoking
``on_event`` synchronously so the runner can persist the resume handle the
instant it is known (Race 1 -- see ``base.py``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import socket
import time
from collections.abc import Awaitable, Callable
from typing import Any

from agent.session_runner.harness import events as _events
from agent.session_runner.harness.base import TurnEvent, TurnRequest, TurnResult
from agent.session_runner.harness.claude_diagnostics import (
    HARNESS_TLS_CONSECUTIVE_SUPPRESS,
    HarnessExitClass,
    build_spawn_diagnostic,
    classify_harness_early_exit,
    describe_harness_exit_for_sentry,
)
from agent.session_runner.hook_edge import HEADLESS_ENV_OVERRIDES
from config.enums import ClassificationType

logger = logging.getLogger(__name__)

# The three ANTHROPIC_* auth keys `stripped_harness_env` pops so a
# subscription-auth (OAuth) claude child never inherits an API-key base URL or
# auth token (issue #2100 AC7). Module constant so both spawn sites strip the
# identical set.
_STRIPPED_HARNESS_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
)

# Bounded TTL for the per-session TLS-streak key (issue #2100 surface 4). The
# streak only needs to survive across a burst of consecutive resume attempts, so
# a short window is enough; a genuine intermittent failure resets it via DELETE
# on the next non-TLS class. Provisional/tunable — override with
# HARNESS_TLS_STREAK_TTL_S. Named locally (#1968) — single-file knob.
_HARNESS_TLS_STREAK_TTL_S = int(os.environ.get("HARNESS_TLS_STREAK_TTL_S", "300"))


def stripped_harness_env(base: dict) -> dict:
    """Return a copy of ``base`` with all three ANTHROPIC_* auth vars popped.

    Pops ``ANTHROPIC_API_KEY``, ``ANTHROPIC_BASE_URL``, and
    ``ANTHROPIC_AUTH_TOKEN`` so a subscription-auth (OAuth) ``claude`` child can
    never inherit an API-key base URL or auth token from the worker's
    environment (issue #2100 AC7). Used at BOTH claude spawn sites
    (``get_response_via_harness`` and ``verify_harness_health``).
    """
    out = dict(base)
    for key in _STRIPPED_HARNESS_ENV_KEYS:
        out.pop(key, None)
    return out


def _default_worker_label() -> str:
    """Derive a worker label for the spawn diagnostic from env + hostname.

    ``VALOR_WORKER_MODE`` (e.g. ``standalone``) when set, joined with the
    hostname, else just the hostname. Purely descriptive — used only to
    attribute a ``[harness-spawn]`` diagnostic to a machine/mode.
    """
    host = socket.gethostname()
    mode = os.environ.get("VALOR_WORKER_MODE", "").strip()
    return f"{mode}@{host}" if mode else host


# === CLI Harness Streaming ===

# Maximum input chars for CLI harness arguments. Conservative cap below the
# claude binary's internal chunk limit (~200KB+). Prevents "Separator is not
# found, and chunk exceed the limit" crashes on long PM session resumes.
HARNESS_MAX_INPUT_CHARS = 100_000

# Per-readline liveness cap for the harness health probe (verify_harness_health).
# This is NOT a git/gh subprocess timeout (settings.timeouts.git_subprocess_s)
# nor a generic whole-subprocess timeout (settings.timeouts.subprocess_default_s):
# it bounds how long we wait for the NEXT stdout line while streaming a
# short-lived `claude ... test` health-check subprocess, so the probe can
# fail fast if the binary hangs before emitting its system-init event. A
# single logic-coupled call site (issue #1968 promote-vs-name-locally
# criterion: name-locally for one-offs) -- not promoted to `settings` because
# it isn't duplicated elsewhere and isn't the kind of knob operators tune
# per-machine.
_HARNESS_HEALTH_READLINE_TIMEOUT_S = 10.0


def _apply_context_budget(message: str, max_chars: int = HARNESS_MAX_INPUT_CHARS) -> str:
    """Trim oldest context from harness input if it exceeds max_chars.

    Preserves everything from the final 'MESSAGE:' marker onward -- the
    steering message must never be truncated. If no MESSAGE: marker exists,
    trims from the start of the string.

    Returns the original string unchanged if within budget.
    """
    if len(message) <= max_chars:
        return message

    # Find the MESSAGE: boundary -- steering message must be preserved in full
    marker = "\nMESSAGE: "
    idx = message.rfind(marker)
    if idx != -1:
        tail = message[idx:]  # "\nMESSAGE: ..." must stay intact
        budget_for_prefix = max_chars - len(tail)
        if budget_for_prefix <= 0:
            # Steering message alone exceeds budget -- pass through unchanged
            # (harness may still fail, but we preserve message fidelity)
            return message
        # Take the end of the prefix (newest context) that fits the budget
        trim_marker = "[CONTEXT TRIMMED — oldest context omitted to fit harness budget]\n"
        available = budget_for_prefix - len(trim_marker)
        if available <= 0:
            return trim_marker + tail
        trimmed_prefix = message[idx - available : idx]
        return trim_marker + trimmed_prefix + tail
    else:
        # No MESSAGE: marker -- trim from start
        trim_marker = "[CONTEXT TRIMMED]\n"
        available = max_chars - len(trim_marker)
        if available <= 0:
            return trim_marker
        return trim_marker + message[len(message) - available :]


# Map harness names to CLI command templates
_HARNESS_COMMANDS: dict[str, list[str]] = {
    "claude-cli": [
        "claude",
        "-p",
        "--verbose",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        "--permission-mode",
        "bypassPermissions",
    ],
    "opencode": ["opencode", "--non-interactive"],
}


_UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# Sentinel string used to detect Claude Code's image-dimension error.
#
# Design note: this checks ``result_text`` (stdout, structured result), NOT stderr.
# The stale-UUID fallback intentionally avoids stderr substring gates because stderr
# is unstructured log noise that changes across CLI versions and locales.
# This sentinel is distinct for three reasons:
#   1. It checks result_text (stdout result), not stderr — stdout result strings are
#      stable protocol output produced by Claude Code as the turn's text response.
#   2. It only fires when prior_uuid was set (resume path) — the error is meaningless
#      on first-turn paths and would never appear there.
#   3. The image-dimension error arrives with exit code 0, making the returncode != 0
#      fallback structurally unable to catch it — a separate check is required.
IMAGE_DIMENSION_SENTINEL = "exceeds the dimension limit"


# Thinking-block corruption sentinel (issue #1099, Mode 1).
#
# When extended-thinking + compaction interact pathologically, the Claude CLI
# exits non-zero and its stderr contains the substring ``redacted_thinking``.
# Both the primary harness call AND the stale-UUID fallback fail the same way,
# so today the caller receives an empty ``""`` result text and the session is
# marked ``completed`` with nothing to deliver — the user gets silence.
#
# Detection rule: stderr contains ``THINKING_BLOCK_SENTINEL`` AND the final
# ``returncode != 0``. Both conditions are required; a healthy session exits
# with code 0 and never triggers. The sentinel is matched as a substring
# (``in`` operator), not a regex, to minimize false-positive surface.
#
# The string is taken from the amux "Every way Claude Code crashes" blog post
# and is **not** yet confirmed against Anthropic's published error taxonomy.
# To bound the blast radius during initial deployment:
#   * Every sentinel match emits ``logger.warning("THINKING_BLOCK_SENTINEL matched: ...")``
#     BEFORE raising, giving operators a grep-friendly audit trail.
#   * Operators may disable the check at runtime via
#     ``DISABLE_THINKING_SENTINEL=1`` (or any truthy value) without a code
#     rollback. When disabled, corruption falls through to the existing empty-
#     string behavior (still suboptimal, but no worse than today).
THINKING_BLOCK_SENTINEL = "redacted_thinking"

# Env-gated kill-switch for the Mode 1 sentinel check. Read once at module
# load; operators must restart the process to toggle. See docstring above.
_DISABLE_THINKING_SENTINEL = os.environ.get("DISABLE_THINKING_SENTINEL", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


class HarnessThinkingBlockCorruptionError(Exception):
    """Raised by ``get_response_via_harness`` when the harness subprocess exits
    non-zero AND its stderr contains ``THINKING_BLOCK_SENTINEL``.

    Indicates the extended-thinking + compaction interaction has corrupted the
    session's transcript beyond in-process recovery. The caller is expected to
    catch this and finalize the session as ``failed`` with the exception's
    message as the user-visible reason. See issue #1099 Mode 1 for the full
    rationale and the ``DISABLE_THINKING_SENTINEL`` escape hatch.
    """


async def get_response_via_harness(
    message: str,
    working_dir: str,
    harness_cmd: list[str] | None = None,
    env: dict[str, str] | None = None,
    *,
    prior_uuid: str | None = None,
    session_id: str | None = None,
    full_context_message: str | None = None,
    model: str | None = None,
    system_prompt: str | None = None,
    settings_path: str | None = None,
    role: str | None = None,
    start_new_session: bool = False,
    json_schema: dict | None = None,
    worker_label: str | None = None,
    on_sdk_started: Callable[[int], None] | None = None,
    on_sdk_finished: Callable[[], None] | None = None,
    on_stdout_event: Callable[[], None] | None = None,
    on_init: Callable[[dict], None] | None = None,
    on_exit_status: Callable[[int | None, bool], None] | None = None,
    on_usage: Callable[[dict | None, float | None], None] | None = None,
    on_structured_output: Callable[[dict | None], None] | None = None,
) -> str:
    """Run a CLI harness (e.g. claude -p) and return the final result text.

    ``on_exit_status`` fires once per subprocess invocation (primary and any
    fallback retry) with ``(returncode, result_event_fired)``; the last
    invocation's status is the turn's authoritative exit shape. The session
    runner's role driver uses it to classify a nonzero exit without a
    ``result`` event as a failed turn (residual #1916 surface).

    ``json_schema`` (plan #2000 Task 2.3), when provided, is JSON-encoded
    onto ``--json-schema`` in the subprocess argv, requesting a
    schema-validated ``StructuredOutput`` tool call. ``on_structured_output``
    fires once, after the LAST subprocess invocation (mirroring
    ``result_text``'s own fallback-retry reassignment), with the parsed
    object from the terminal ``result`` event's ``structured_output`` key —
    or ``None`` when the key is absent (no schema requested, or the CLI's
    own schema validation gave up; Task 2.1 empirical finding: neither exit
    code nor ``is_error`` distinguishes the two, only key presence does).

    ``start_new_session=True`` spawns the subprocess in its own process
    group (session-runner role turns) so a preempt watcher can signal the
    whole subprocess tree via ``killpg`` and the worker orphan sweep can
    reap survivors. Default False preserves behavior for every other
    harness consumer (message drafter, drafter-review, probes).

    Parses stdout as stream-json line-by-line. Extracts the final result from
    the ``result`` event, or falls back to accumulated ``content_block_delta``
    text if no result event fires. No streaming callback is used — intermediate
    chunks are accumulated internally and never delivered mid-session.

    When ``prior_uuid`` is provided and valid, injects ``--resume <uuid>`` into
    the subprocess argv. ``_apply_context_budget()`` is applied **unconditionally**
    to the final argv message, regardless of ``--resume`` state. On the typical
    resume path the message is small and the function is a no-op; on first turns
    and pathological large single messages it bounds the argv to prevent the
    binary's "Separator is found" overflow crash.

    If the resumed subprocess exits with **any** non-zero return code **without
    having emitted a ``result`` event**, retries once using
    ``full_context_message`` without ``--resume`` (no stderr substring gate —
    substring matching is brittle across CLI versions and locales). Issue #1980:
    a non-zero exit that follows a fired ``result`` event keeps the captured
    completion and does NOT retry — the ``result`` event is the protocol's
    completion signal, and a fresh retry would clobber a valid answer.

    A separate exit-code-0 sentinel check (``IMAGE_DIMENSION_SENTINEL``) is placed
    **above** the ``returncode != 0`` guard to handle Claude Code's image-dimension
    error, which returns exit code 0.  This check fires only on resume paths and
    inspects ``result_text`` (stdout), not stderr.  See ``IMAGE_DIMENSION_SENTINEL``
    for the full design rationale.

    When ``session_id`` is provided, stores the captured Claude Code UUID on
    the AgentSession record after a successful turn (Popoto/Redis side effect).
    Tests that pass ``session_id`` must mock ``_store_claude_session_uuid``.

    Args:
        message: The prompt to send to the CLI.
        working_dir: Working directory for the subprocess.
        harness_cmd: Override CLI command (default: claude -p stream-json).
        env: Extra environment variables for the subprocess.
        prior_uuid: Claude Code session UUID from a prior turn (enables --resume).
        session_id: Bridge/Telegram session ID for UUID storage after the turn.
        full_context_message: Full-context first-turn message for stale-UUID fallback.
        model: Short alias (``opus``/``sonnet``/``haiku``) or full Claude model
            name to pin this turn to. When truthy, ``--model <value>`` is
            injected into ``harness_cmd`` so the Claude CLI honors the choice.
            When None/empty, the CLI uses its own default. Part of the per-
            session model routing cascade (see
            ``agent/session_executor.py::_resolve_session_model``).
        system_prompt: Optional persona/role text appended to Claude Code's
            default system prompt via ``--append-system-prompt`` (issue #1148).
            Use ``--append`` (not ``--system-prompt``) to preserve the default
            tool-handling protocol — the persona is additive guidance. Eng
            sessions pass ``load_eng_system_prompt(working_dir)`` here; drafter
            sessions must keep this ``None``. Strings larger than
            512KB are dropped with a warning to avoid ARG_MAX overflows.
        on_usage: Optional observer fired once, immediately before return,
            with the same ``(usage, cost_usd)`` values used for
            ``accumulate_session_tokens`` below. Additive (plan #2000
            Task 2.2) -- purely observational, no behavior change for
            existing callers that omit it. Lets ``ClaudeHarnessAdapter``
            populate ``TurnResult.usage`` without re-parsing stdout.
    """
    # Deferred import: agent.sdk_client owns token/cost/turn-count
    # bookkeeping (Popoto-backed, not CLI-specific); a module-level import
    # here would cycle back through agent.sdk_client's re-export of this
    # module (plan #2000 Task 2.2 -- see harness/claude.py module docstring).
    from agent.sdk_client import (  # noqa: PLC0415
        _log_context_usage_if_risky,
        _store_claude_session_uuid,
        _store_exit_returncode,
        _usage_field,
        accumulate_session_tokens,
    )

    # Validate prior_uuid format; treat empty or invalid as None
    if prior_uuid and not _UUID_PATTERN.match(prior_uuid):
        logger.warning(f"[harness] Invalid prior_uuid format, ignoring: {prior_uuid!r}")
        prior_uuid = None
    if not prior_uuid:
        prior_uuid = None

    if harness_cmd is None:
        harness_cmd = list(_HARNESS_COMMANDS["claude-cli"])
    else:
        # Defensive copy — callers may hand us a shared constant (e.g. test
        # fixtures sharing a module-level list). We must not mutate their
        # list when appending --model below.
        harness_cmd = list(harness_cmd)

    # Inject per-session model when caller supplied one. --model must live
    # inside harness_cmd so it precedes the positional message (and any
    # --resume <uuid>) in the final argv assembly below.
    if model:
        harness_cmd.extend(["--model", model])
        logger.info(f"[harness] Using --model {model} for session_id={session_id}")

    # Plan #1842 (headless leg): inject the #1688 --settings hook set so the
    # single-shot `claude -p` writes turn-end (Stop) / needs-human / compaction
    # edges to the per-session NDJSON edge file, letting the HeadlessRoleDriver
    # reconcile turn-end from a TURN_END envelope when it lands (else fall back
    # to the subprocess result/clean-exit). --settings must precede the
    # positional message.
    if settings_path:
        harness_cmd.extend(["--settings", settings_path])
        logger.info(f"[harness] Using --settings {settings_path} for session_id={session_id}")
    else:
        # No per-session settings file (message drafter, probes,
        # drafter-review): pass HEADLESS_ENV_OVERRIDES inline so every
        # headless spawn disables agent teams, not just role sessions. The
        # role-session settings file written by generate_hook_settings
        # carries the same env block. A --settings source is the only layer
        # that outranks the user settings' env — see
        # docs/features/agent-teams-headless-policy.md.
        harness_cmd.extend(["--settings", json.dumps({"env": HEADLESS_ENV_OVERRIDES})])

    # Schema-first routing (plan #2000 Task 2.3): request a schema-validated
    # StructuredOutput tool call. Applies to every subprocess invocation for
    # this turn (primary + any fallback retry below share ``harness_cmd``),
    # so a stale-UUID/image-dimension fallback still asks for the same
    # structured shape.
    if json_schema:
        harness_cmd.extend(["--json-schema", json.dumps(json_schema)])

    # System prompt injection (issue #1148). Use --append-system-prompt
    # (NOT --system-prompt) so Claude Code's default tool-handling protocol is
    # preserved — the PM persona is additive guidance, not a full replacement.
    # Defensive size cap: macOS ARG_MAX is 1MB; we cap at 512KB to leave room
    # for the rest of the argv. The current PM persona is ~25KB.
    if system_prompt:
        if len(system_prompt) > 512_000:
            logger.warning(
                f"[harness] system_prompt is {len(system_prompt)} bytes; "
                "exceeds 512KB soft cap, omitting to avoid ARG_MAX (session_id="
                f"{session_id})"
            )
        else:
            # Direction A (issue #1227): move per-machine dynamic sections (cwd,
            # env info, memory paths, git status) into the first user message
            # instead of the system prompt.  This stabilises the system-prompt
            # prefix so Anthropic's server-side prompt cache can reuse it across
            # consecutive PM sessions that share the same working_directory.
            # Only injected when --append-system-prompt is present (PM sessions);
            # the flag is silently ignored when --system-prompt is used instead.
            harness_cmd.append("--exclude-dynamic-system-prompt-sections")
            harness_cmd.extend(["--append-system-prompt", system_prompt])
            logger.info(
                f"[harness] Appending {len(system_prompt)}-char system prompt for "
                f"session_id={session_id} (cache-stable: --exclude-dynamic-system-prompt-sections)"
            )

    # Build subprocess env: inherit current env, merge extras, strip ALL THREE
    # ANTHROPIC_* auth vars (issue #2100 AC7 — a subscription-auth claude child
    # must never inherit an API-key base URL or auth token). Strip AFTER merging
    # `env` so a caller-supplied ANTHROPIC_* can't sneak back in.
    proc_env = stripped_harness_env(os.environ)
    if env:
        proc_env.update(env)
        proc_env = stripped_harness_env(proc_env)
    # Force wide COLUMNS so Claude Code CLI doesn't narrow-wrap result text
    # (mid-hyphen breaks observed in drafted messages when launched without a TTY).
    proc_env["COLUMNS"] = "999"

    # Apply context budget unconditionally. On resumed turns with a typical
    # small message this is a no-op (one length comparison). On first turns
    # and on pathological large single messages (pasted transcripts, forwarded
    # logs) it bounds the argv to prevent the binary's chunk-limit crash.
    original_len = len(message)
    message = _apply_context_budget(message)
    if len(message) < original_len:
        logger.info(
            f"[harness] Context budget applied: trimmed {original_len} → {len(message)} chars"
        )

    if prior_uuid:
        logger.info(f"[harness] Resuming Claude session {prior_uuid} for session_id={session_id}")
        cmd = harness_cmd + ["--resume", prior_uuid, message]
    else:
        cmd = harness_cmd + [message]

    # TTFT metadata for first-token instrumentation (issue #1227).  Only
    # emitted on first-turn invocations (no prior_uuid) so we don't pollute
    # the JSONL with resume-turn data.  session_type is inferred from whether
    # a system_prompt was supplied (PM path) vs. not (dev/teammate path).
    _ttft_meta: dict | None = None
    if not prior_uuid:
        _session_type_tag = "eng" if system_prompt else "other"
        _ttft_meta = {
            "session_id": session_id or "",
            "session_type": _session_type_tag,
            "working_dir": working_dir,
            "prompt_chars": len(system_prompt) if system_prompt else 0,
            "model": model or "default",
        }

    # Capture whether the PRIMARY invocation emitted a `result` event (issue
    # #1980). `_run_harness_subprocess` reports this as the second arg of
    # `on_exit_status(returncode, result_event_fired)` — it is the ONLY precise
    # signal for "a result event fired." The returned `result_text` cannot stand
    # in for it: `_run_harness_subprocess` returns a non-None string both when a
    # result event fired (BRANCH A) AND when no result event fired but partial
    # streamed text accumulated (BRANCH B), so `result_text is None` is true only
    # in BRANCH C. The stale-UUID fallback below gates on this boolean so a
    # resumed turn that produced a valid completion is never discarded by a
    # destructive fresh-session retry on a post-turn non-zero exit.
    primary_result_event_fired = False

    def _capture_primary_exit(rc: int | None, fired: bool) -> None:
        nonlocal primary_result_event_fired
        primary_result_event_fired = fired
        # Chain to the caller's callback so the role driver's exit_statuses
        # list still receives every subprocess invocation (residual #1916).
        if on_exit_status is not None:
            on_exit_status(rc, fired)

    # Worker label for the [harness-spawn] diagnostic. Resolve the caller-
    # supplied value or derive it from VALOR_WORKER_MODE / hostname (issue #2100).
    resolved_worker_label = worker_label or _default_worker_label()

    # Per-session TLS-streak containment (issue #2100 surface 4, follow-up #3).
    # The key is scoped per in-flight session (NOT per-host) so one session's
    # reset can never clear another interleaved session's streak. INCR on each
    # TLS_TRUST classification, DELETE on any non-TLS_TRUST class. The
    # stale-UUID fresh-session retry is suppressed only once the streak reaches
    # HARNESS_TLS_CONSECUTIVE_SUPPRESS — the FIRST TLS_TRUST exit still retries
    # (an intermittent chain race could self-heal).
    _tls_streak_key = (
        f"harness:tls_streak:{socket.gethostname()}:{session_id}" if session_id else None
    )
    _tls_state = {"streak": 0, "last_class": None}

    def _handle_early_exit_class(exit_class: HarnessExitClass | None) -> None:
        _tls_state["last_class"] = exit_class
        if _tls_streak_key is None or exit_class is None:
            return
        try:
            # Watchdog/telemetry raw key (NOT a Popoto-managed model key), so
            # raw Redis INCR/EXPIRE/DELETE is the sanctioned path here —
            # mirrors _record_critical_status / _increment_down_ticks.
            from popoto.redis_db import POPOTO_REDIS_DB as _R  # noqa: PLC0415

            if exit_class == HarnessExitClass.TLS_TRUST:
                count = _R.incr(_tls_streak_key)
                _R.expire(_tls_streak_key, _HARNESS_TLS_STREAK_TTL_S)
                _tls_state["streak"] = int(count)
            else:
                _R.delete(_tls_streak_key)
                _tls_state["streak"] = 0
        except Exception as _tls_err:  # noqa: BLE001
            logger.warning("[harness] TLS-streak bookkeeping failed (non-fatal): %s", _tls_err)

    # Call site 1 of 3 — primary harness invocation. 9-tuple unpack
    # (issue #1099 Mode 1 added stderr_snippet; issue #1245 added num_turns
    # and tool_call_count; plan #2000 Task 2.3 added structured_output).
    (
        result_text,
        session_id_from_harness,
        returncode,
        usage,
        cost_usd,
        stderr_snippet,
        this_num_turns,
        this_tool_call_count,
        structured_output,
    ) = await _run_harness_subprocess(
        cmd,
        working_dir,
        proc_env,
        start_new_session=start_new_session,
        worker_label=resolved_worker_label,
        on_sdk_started=on_sdk_started,
        on_sdk_finished=on_sdk_finished,
        on_stdout_event=on_stdout_event,
        on_init=on_init,
        on_exit_status=_capture_primary_exit,
        on_early_exit_class=_handle_early_exit_class,
        ttft_metadata=_ttft_meta,
        true_session_id=session_id,
    )
    # Issue #1245: accumulate counts across primary + fallback subprocess
    # invocations (image-dimension fallback, stale-UUID fallback). Each
    # call_site below adds its counts to these locals so the final values
    # passed to the Popoto write reflect ALL subprocesses for this turn.
    total_num_turns = this_num_turns
    total_tool_call_count = this_tool_call_count

    # Image-dimension sentinel: Claude Code returns the image-dimension error with
    # exit code 0, so the returncode != 0 fallback below cannot catch it.  This
    # check fires only on resume paths (prior_uuid set) and inspects result_text
    # (stdout result), not stderr.  See IMAGE_DIMENSION_SENTINEL for rationale.
    if prior_uuid and result_text and IMAGE_DIMENSION_SENTINEL in result_text:
        logger.warning(
            f"[harness] Image dimension error on --resume for session_id={session_id}; "
            "triggering full_context_message fallback"
        )
        if full_context_message is not None:
            fallback_msg = _apply_context_budget(full_context_message)
            fallback_cmd = harness_cmd + [fallback_msg]
            # Call site 2 of 3 — image-dimension fallback. 9-tuple unpack
            # (issue #1099 Mode 1 + issue #1245; plan #2000 Task 2.3 added
            # structured_output).
            (
                result_text,
                session_id_from_harness,
                _,
                usage,
                cost_usd,
                stderr_snippet,
                this_num_turns,
                this_tool_call_count,
                structured_output,
            ) = await _run_harness_subprocess(
                fallback_cmd,
                working_dir,
                proc_env,
                start_new_session=start_new_session,
                worker_label=resolved_worker_label,
                on_sdk_started=on_sdk_started,
                on_sdk_finished=on_sdk_finished,
                on_stdout_event=on_stdout_event,
                on_init=on_init,
                on_exit_status=on_exit_status,
                on_early_exit_class=_handle_early_exit_class,
                true_session_id=session_id,
            )
            total_num_turns += this_num_turns
            total_tool_call_count += this_tool_call_count
        else:
            logger.error(
                f"[harness] Image dimension error on --resume for session_id={session_id}, "
                "no full_context_message available — returning plain-language error"
            )
            result_text = (
                "I couldn't resume because the session history contains images that are "
                "too large. Please start a new thread."
            )

    # Mandatory stale-UUID fallback: when prior_uuid was set and the subprocess
    # exits with ANY non-zero return code WITHOUT having emitted a `result` event,
    # retry once without --resume using the full-context message. The fallback
    # does NOT inspect stderr — substring matching is brittle across CLI versions
    # and locales, and an unnecessary retry on a non-stale-UUID error costs only
    # one extra subprocess spawn.
    #
    # Issue #1980: gate on `not primary_result_event_fired`. A resumed turn that
    # emitted a `result` event is a SUCCESSFUL resume; its completion is the
    # protocol's completion signal (mirrors the role driver's residual-#1916
    # contract at role_driver.py: "a nonzero exit AFTER a result event keeps the
    # result"). Without this gate, a valid completion followed by a post-turn
    # non-zero exit triggered a fresh-session retry whose empty output clobbered
    # the good result_text — the wrap-up guard then delivered the canned
    # OPERATOR_TERMINAL_MESSAGE instead of the real answer. The fallback still
    # fires when no result event fired (BRANCH B partial text, or BRANCH C genuine
    # stale UUID), preserving all pre-existing recovery.
    #
    # Issue #2100 (surface 4): suppress this fresh-session retry ONLY once the
    # per-session TLS_TRUST streak has reached HARNESS_TLS_CONSECUTIVE_SUPPRESS.
    # A repeated hard TLS/trust failure would only re-trigger the destructive
    # macOS Keychain dialog, so after M consecutive TLS_TRUST exits we stop
    # retrying. The FIRST TLS_TRUST exit still falls through to the normal
    # recovery path below (transient-safe).
    _tls_suppress_retry = (
        _tls_state["last_class"] == HarnessExitClass.TLS_TRUST
        and _tls_state["streak"] >= HARNESS_TLS_CONSECUTIVE_SUPPRESS
    )
    if (
        prior_uuid
        and returncode is not None
        and returncode != 0
        and not primary_result_event_fired
        and _tls_suppress_retry
    ):
        logger.warning(
            "[harness] Suppressing stale-UUID fresh-session retry after %d consecutive "
            "TLS_TRUST exits for session_id=%s — a retry only re-triggers the Claude Code "
            "CLI TLS/trust failure and its keychain dialog. Do NOT reset the login keychain; "
            "inspect the [harness-spawn] diagnostic and the certificate chain.",
            _tls_state["streak"],
            session_id,
        )
    elif (
        prior_uuid and returncode is not None and returncode != 0 and not primary_result_event_fired
    ):
        if full_context_message is not None:
            logger.warning(
                f"[harness] Stale UUID {prior_uuid} for session_id={session_id}, "
                "falling back to first-turn path"
            )
            original_len = len(full_context_message)
            fallback_msg = _apply_context_budget(full_context_message)
            if len(fallback_msg) < original_len:
                logger.info(
                    f"[harness] Fallback budget: {original_len} → {len(fallback_msg)} chars"
                )
            fallback_cmd = harness_cmd + [fallback_msg]
            # Call site 3 of 3 — stale-UUID fallback. 9-tuple unpack
            # (issue #1099 Mode 1 + issue #1245; plan #2000 Task 2.3 added
            # structured_output).
            # We now DO capture the final returncode + stderr_snippet because the
            # Mode 1 sentinel check below inspects the LAST subprocess call's
            # exit state (both primary and fallback must fail to declare
            # thinking-block corruption).
            (
                result_text,
                session_id_from_harness,
                returncode,
                usage,
                cost_usd,
                stderr_snippet,
                this_num_turns,
                this_tool_call_count,
                structured_output,
            ) = await _run_harness_subprocess(
                fallback_cmd,
                working_dir,
                proc_env,
                start_new_session=start_new_session,
                worker_label=resolved_worker_label,
                on_sdk_started=on_sdk_started,
                on_sdk_finished=on_sdk_finished,
                on_stdout_event=on_stdout_event,
                on_init=on_init,
                on_exit_status=on_exit_status,
                on_early_exit_class=_handle_early_exit_class,
                true_session_id=session_id,
            )
            total_num_turns += this_num_turns
            total_tool_call_count += this_tool_call_count
        else:
            logger.error(
                f"[harness] Stale UUID {prior_uuid} for session_id={session_id}, "
                "falling back to first-turn path — no full_context_message available"
            )
            result_text = None
    elif prior_uuid and returncode is not None and returncode != 0 and primary_result_event_fired:
        # Issue #1980 observability: a resumed turn exited non-zero but a result
        # event fired first, so the stale-UUID fallback is deliberately skipped
        # and the captured completion is kept. Logged so incident triage can tell
        # this apart from a fallback that fired.
        logger.info(
            "[harness] Resumed turn exited %s AFTER a result event for "
            "session_id=%s — keeping the completion, skipping stale-UUID fallback "
            "(issue #1980)",
            returncode,
            session_id,
        )

    # Store the Claude Code UUID for next-turn --resume (#976)
    if session_id and session_id_from_harness:
        _store_claude_session_uuid(session_id, session_id_from_harness)

    # Accumulate tokens + cost on the AgentSession (issue #1128). Mirrors
    # the (deleted) SDK client path's equivalent in-handler call. Invoked
    # here as a side effect so the public signature stays `-> str` and
    # no caller of `get_response_via_harness` has to change. `usage` /
    # `cost_usd` may be None on harness error paths or older CLI
    # versions — the helper treats missing fields as 0.
    #
    # Schema diet (#1927): `accumulate_session_tokens` collapsed to a single
    # `total_*` write path (the separate metered-leg field set it used to
    # write for `metered=True` callers is gone), so `metered`/`role` are no
    # longer forwarded — every caller now accumulates onto the same fields.
    if session_id and (usage is not None or cost_usd is not None):
        accumulate_session_tokens(
            session_id,
            _usage_field(usage, "input_tokens"),
            _usage_field(usage, "output_tokens"),
            _usage_field(usage, "cache_read_input_tokens"),
            cost_usd,
        )
        # Additive telemetry tap — no behavior change
        from agent.session_telemetry import record_telemetry_event

        record_telemetry_event(
            session_id,
            {
                "type": "token_usage",
                "usage": usage if isinstance(usage, dict) else {},
                "total_cost_usd": cost_usd,
            },
        )

    # Issue #1245: persist turn_count + tool_call_count onto the AgentSession
    # via Popoto. Accumulating (`+=`) so primary + fallback subprocess
    # invocations sum across this single get_response_via_harness call.
    # Wrapped in try/except — Popoto failure must never crash the harness path.
    if session_id and (total_num_turns or total_tool_call_count):
        try:
            from models.agent_session import AgentSession

            # session_id is a Field (not KeyField), so multiple records can share
            # an id across resumes — pick the newest by created_at to avoid
            # accumulating onto a stale record.
            sessions = list(AgentSession.query.filter(session_id=session_id))
            if sessions:
                sessions.sort(key=lambda s: s.created_at or 0, reverse=True)
                session = sessions[0]
                if total_num_turns:
                    session.turn_count = (session.turn_count or 0) + total_num_turns
                if total_tool_call_count:
                    session.tool_call_count = (session.tool_call_count or 0) + total_tool_call_count
                # update_fields=[...] to avoid clobbering concurrent writes to
                # other fields like status/updated_at (matches accumulate_session_tokens
                # convention).
                session.save(update_fields=["turn_count", "tool_call_count"])
        except Exception as e:
            logger.warning(
                "Failed to persist turn/tool counts for session %s: %s",
                session_id,
                e,
            )

    # Mode 2 (issue #1099) — emit a single WARNING if per-turn context usage
    # crosses 75%. Pure observability; no state change, no behavior change.
    _log_context_usage_if_risky(session_id, model, usage)

    # Mode 4 (issue #1099) — persist the last subprocess exit code best-effort
    # so the health-check recovery branch can distinguish OS-initiated OOM
    # kills from health-check-initiated kills. Fail-quiet.
    _store_exit_returncode(session_id, returncode)

    # Mode 1 (issue #1099) — thinking-block corruption sentinel. Fires only
    # when BOTH (a) the final subprocess exited non-zero AND (b) its stderr
    # contains ``THINKING_BLOCK_SENTINEL``. A healthy session exits 0 and
    # never triggers. Disable at runtime with ``DISABLE_THINKING_SENTINEL=1``.
    if (
        not _DISABLE_THINKING_SENTINEL
        and returncode is not None
        and returncode != 0
        and stderr_snippet
        and THINKING_BLOCK_SENTINEL in stderr_snippet
    ):
        # Always log BEFORE raising so operators can grep for false positives
        # during initial deployment. The WARNING is grep-friendly by design.
        logger.warning(
            "[harness] THINKING_BLOCK_SENTINEL matched: session_id=%s returncode=%d "
            "stderr_prefix=%r",
            session_id,
            returncode,
            stderr_snippet[:200],
        )
        raise HarnessThinkingBlockCorruptionError(
            "Session context corrupted — please start a new thread"
        )

    if on_usage is not None:
        try:
            on_usage(usage, cost_usd)
        except Exception as _cb_err:  # noqa: BLE001
            logger.warning("on_usage callback raised: %s", _cb_err)

    # Schema-first routing (plan #2000 Task 2.3): fires once, after the LAST
    # subprocess invocation — mirrors result_text's own fallback-retry
    # reassignment, so a stale-UUID/image-dimension retry's structured
    # output (or lack thereof) is what the caller sees.
    if on_structured_output is not None:
        try:
            on_structured_output(structured_output)
        except Exception as _cb_err:  # noqa: BLE001
            logger.warning("on_structured_output callback raised: %s", _cb_err)

    if result_text is not None:
        return result_text
    return ""


# Bounds for the init-hang stderr diagnostic (issue #2181). Env-tunable; grain
# of salt — provisional. The read is guarded by a short timeout so a wedged pipe
# can never stall the teardown, and the logged tail is bounded to keep the log
# line small.
INIT_HANG_STDERR_TIMEOUT_S: float = float(os.environ.get("INIT_HANG_STDERR_TIMEOUT_S", "2.0"))
INIT_HANG_STDERR_MAX_CHARS: int = int(os.environ.get("INIT_HANG_STDERR_MAX_CHARS", "4000"))


async def _log_init_hang_stderr(proc: Any, session_id: str | None) -> None:
    """Drain and log a killed subprocess's buffered stderr for issue #2181.

    Best-effort diagnostic for the ``communicated=False`` init hang: the
    subprocess held its process (heartbeats fired) but never emitted a stdout
    event, so the real init blocker (MCP-server load / oauth / model connect)
    would otherwise be lost with the killed pipe. The caller SIGKILLs first, so
    the stderr pipe is at EOF and the read returns promptly; a short
    ``wait_for`` guards against any residual block.

    Never raises and never masks the cancellation propagating through the
    caller's ``finally`` — a re-delivered ``CancelledError`` from the inner
    ``await`` is caught here; the ORIGINAL exception that triggered the
    ``finally`` still re-raises on ``finally`` exit.
    """
    stderr_reader = getattr(proc, "stderr", None)
    if stderr_reader is None:
        return
    try:
        data = await asyncio.wait_for(
            stderr_reader.read(INIT_HANG_STDERR_MAX_CHARS),
            timeout=INIT_HANG_STDERR_TIMEOUT_S,
        )
        text = data.decode("utf-8", errors="replace").strip() if data else ""
        logger.warning(
            "[harness] init-hang diagnostics session_id=%s communicated=False stderr_tail=%r",
            session_id,
            (text[:INIT_HANG_STDERR_MAX_CHARS] if text else "<empty>"),
        )
    except (TimeoutError, asyncio.CancelledError) as _diag_timeout:
        logger.warning(
            "[harness] init-hang diagnostics session_id=%s communicated=False "
            "stderr unavailable (%s)",
            session_id,
            type(_diag_timeout).__name__,
        )
    except Exception as _diag_err:  # noqa: BLE001 — diagnostic must never raise
        logger.debug("[harness] init-hang stderr drain failed (non-fatal): %s", _diag_err)


async def _run_harness_subprocess(
    cmd: list[str],
    working_dir: str,
    proc_env: dict[str, str],
    *,
    start_new_session: bool = False,
    worker_label: str | None = None,
    on_sdk_started: Callable[[int], None] | None = None,
    on_sdk_finished: Callable[[], None] | None = None,
    on_stdout_event: Callable[[], None] | None = None,
    on_init: Callable[[dict], None] | None = None,
    on_exit_status: Callable[[int | None, bool], None] | None = None,
    on_early_exit_class: Callable[[HarnessExitClass | None], None] | None = None,
    ttft_metadata: dict | None = None,
    true_session_id: str | None = None,
) -> tuple[
    str | None,
    str | None,
    int | None,
    dict | None,
    float | None,
    str | None,
    int,
    int,
    dict | None,
]:
    """Execute a harness subprocess and parse stream-json output.

    ``on_exit_status`` (optional) fires once per subprocess, after exit, with
    ``(returncode, result_event_fired)`` — the session runner's role driver
    uses it to classify a nonzero exit WITHOUT a ``result`` event as a failed
    turn even when partial streamed text accumulated (residual #1916
    surface). Callback exceptions are caught and logged.

    Returns ``(result_text, session_id_from_harness, returncode, usage, cost_usd,
    stderr_snippet, num_turns, tool_call_count, structured_output)``.

    * ``result_text``: parsed result string from the final `result` event, or
      accumulated text from stream events when no result event fires, or
      ``None`` when neither is available.
    * ``session_id_from_harness``: Claude Code UUID for next-turn `--resume`.
    * ``returncode``: process exit code (0 on success, non-zero on failure, or
      ``None`` on binary-not-found).
    * ``usage``: dict from the `result` event's `usage` field (keys include
      ``input_tokens``, ``output_tokens``, ``cache_read_input_tokens``,
      ``cache_creation_input_tokens``). ``None`` when no `result` event fired
      or the event omitted it. Consumed by ``accumulate_session_tokens`` in
      ``get_response_via_harness`` — this is the harness-side half of the
      two-path token tracker introduced for issue #1128.
    * ``cost_usd``: raw ``total_cost_usd`` from the `result` event, taken
      verbatim and never recomputed locally so the value tracks upstream
      Anthropic pricing automatically.
    * ``stderr_snippet``: first 2000 chars of decoded stderr when
      ``returncode != 0``; ``None`` otherwise. Issue #1099 Mode 1 uses this
      for sentinel-based thinking-block corruption detection. Truncation
      bounds memory usage; sentinel matches reliably fall within this window
      per the amux report.
    * ``num_turns``: integer pulled from the `result` event's ``num_turns``
      field (issue #1245). Defaults to 0 when the field is absent. Caller
      accumulates onto AgentSession.turn_count.
    * ``tool_call_count``: count of ``tool_use`` content blocks observed
      across `assistant` events during this subprocess (issue #1245).
      Caller accumulates onto AgentSession.tool_call_count.
    * ``structured_output``: the `result` event's ``structured_output``
      dict (plan #2000 Task 2.3), present only when a schema was requested
      (``--json-schema``) AND the CLI's own schema validation succeeded.
      ``None`` when no schema was requested, or the CLI gave up after its
      internal compliance-nudge retry (Task 2.1 empirical finding: this is
      the ONLY reliable fallback-detection signal — exit code and
      ``is_error`` do not change).

    On binary-not-found, returncode is None and result_text carries the
    error message (usage, cost_usd, stderr_snippet, structured_output are
    all None, num_turns and tool_call_count are 0).

    Optional callbacks (issue #1036, #1269):
        on_sdk_started(pid): fires once, immediately after the subprocess is
            spawned with a valid pid. Callback exceptions are caught + logged.
        on_sdk_finished(): fires once, immediately after `proc.communicate()`
            returns (subprocess has exited). Paired with on_sdk_started so the
            caller can clear any per-subprocess PID tracking. Callback
            exceptions are caught + logged.
        on_stdout_event(): fires on each non-empty stdout line from the SDK.
            Callback exceptions are caught + logged.

    Optional TTFT measurement (issue #1227):
        ttft_metadata: dict with keys {session_id, session_type, prompt_chars,
            model} that are merged into the JSONL entry written to
            ``logs/cold_start_metrics.jsonl`` on first-stdout-byte.  Omitting
            this parameter disables TTFT logging (all non-PM call sites).

    Turn-boundary liveness (issue #1935):
        true_session_id: the true ``AgentSession.session_id`` (NOT the
            Claude UUID reported on the ``result`` event, NOT the
            ``agent_session_id`` env value) — passed explicitly to
            ``agent.hooks.liveness_writers.record_turn_boundary`` on each
            ``result`` event so ``last_turn_at`` is written from the worker
            process, where ``AGENT_SESSION_ID`` is never set.
    """
    # TTFT baseline: record spawn timestamp before exec (issue #1227).
    _spawn_ts = time.monotonic()

    # Default asyncio StreamReader limit is 64KB. The claude CLI outputs its
    # full result as a single JSON line — long responses (e.g. multi-cycle
    # analyses) can exceed that, raising LimitExceededError: "Separator is
    # found, but chunk is longer than limit". Set limit to 16MB to cover any
    # realistic Claude response.
    #
    # Sanitized pre-exec spawn diagnostic (issue #2100 AC1/AC2/AC7). Attributes
    # a version-named claude child (macOS logs it as e.g. "2.1.202") back to
    # Claude Code and proves the no-secret env guarantee: auth mode is
    # presence-only, trust-env values are paths/flags, and the prompt (cmd[-1])
    # is never included. Emitted for every claude spawn, immediately before exec.
    try:
        _spawn_diag = build_spawn_diagnostic(
            cmd,
            proc_env,
            working_dir,
            true_session_id,
            worker_label or _default_worker_label(),
        )
        logger.info("[harness-spawn] %s", json.dumps(_spawn_diag))
    except Exception as _diag_err:  # noqa: BLE001
        logger.warning("[harness] spawn diagnostic emit failed (non-fatal): %s", _diag_err)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
            env=proc_env,
            limit=16 * 1024 * 1024,  # 16 MB — covers any realistic Claude response
            # Own process group when requested (session-runner role turns):
            # lets a preempt watcher SIGTERM/SIGKILL the whole subprocess tree
            # via killpg without touching the worker's own group, and lets the
            # worker-startup orphan sweep reap survivors (Race 2, plan #1924).
            start_new_session=start_new_session,
        )
    except FileNotFoundError as e:
        logger.error(f"Harness binary not found: {e}")
        # Issue #2100 §2: a missing binary is BINARY_MISSING (returncode is
        # None). Surface the classification so the caller's streak bookkeeping
        # resets (a missing binary is not a TLS/trust failure).
        if on_early_exit_class is not None:
            try:
                on_early_exit_class(HarnessExitClass.BINARY_MISSING)
            except Exception as _cb_err:  # noqa: BLE001
                logger.warning("on_early_exit_class callback raised: %s", _cb_err)
        return (f"Error: CLI harness not found — {e}", None, None, None, None, None, 0, 0, None)

    # Fire SDK-started callback once the pid is known (#1036).
    if on_sdk_started is not None and proc.pid is not None:
        try:
            on_sdk_started(proc.pid)
        except Exception as _cb_err:
            logger.warning("on_sdk_started callback raised: %s", _cb_err)

    full_text = ""
    result_text = None
    session_id_from_harness = None
    # Schema-first routing (plan #2000 Task 2.3): the `result` event's
    # `structured_output` key, present only on a schema-validated success.
    structured_output: dict | None = None
    _first_stdout_seen = False  # TTFT sentinel (issue #1227)
    # Token + cost fields extracted off the `result` event (issue #1128).
    # Mirrors the SDK path's `ResultMessage.usage` / `.total_cost_usd`
    # so `accumulate_session_tokens` can be fed from either path.
    usage: dict | None = None
    cost_usd: float | None = None
    # Turn + tool-call counters (issue #1245). num_turns comes off the
    # `result` event; tool_call_count is summed from `assistant` events'
    # tool_use content blocks. Caller accumulates both onto AgentSession.
    num_turns: int = 0
    tool_call_count: int = 0
    # Whether a system/init event fired (issue #2100 §2). Feeds
    # classify_harness_early_exit — a nonzero exit with no init and no TLS/auth
    # stderr match classifies as STALE_UUID.
    init_seen: bool = False

    # Generic-harness cancellation backstop (issue #1938): if the awaiting
    # coroutine is torn down (CancelledError) mid-stream or mid-communicate, the
    # subprocess would otherwise survive parented to the worker. The ``finally``
    # SIGKILLs it. This covers the three non-runner call sites (2534/2576/2632)
    # uniformly; the runner path has its own dedicated reap in
    # ``SessionRunner._run_one_turn``. CancelledError is NOT swallowed — a plain
    # try/finally re-raises it after signalling, preserving cancellation
    # semantics.
    try:
        async for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            # TTFT measurement: log first-stdout-byte elapsed time (issue #1227).
            # Best-effort — any write failure is silently swallowed so that a
            # permissions error on logs/ never blocks PM session output.
            if not _first_stdout_seen and ttft_metadata is not None:
                _first_stdout_seen = True
                _ttft_seconds = time.monotonic() - _spawn_ts
                try:
                    from agent.cold_start_metrics import record_ttft

                    record_ttft(ttft_seconds=_ttft_seconds, **ttft_metadata)
                except Exception as _ttft_err:
                    logger.warning("[TTFT] metric write failed (non-fatal): %s", _ttft_err)

            # Fire stdout-event callback for liveness tracking (#1036). Do NOT
            # block the harness loop if the callback raises.
            if on_stdout_event is not None:
                try:
                    on_stdout_event()
                except Exception as _cb_err:
                    logger.warning("on_stdout_event callback raised: %s", _cb_err)

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                logger.debug(f"Harness: skipping malformed JSON line: {line[:120]}")
                continue

            event_type = data.get("type")

            # Capture-at-init (plan #1924, Race 5): the `system/init` event names
            # the NEW invocation's session_id before any work happens. Callers
            # (session runner) persist it immediately so a preempted/killed turn's
            # partial transcript remains the resume target — never the stale
            # pre-turn uuid. Callback exceptions are caught + logged.
            if event_type == "system" and data.get("subtype") == "init":
                init_seen = True
                if on_init is not None:
                    try:
                        on_init(data)
                    except Exception as _cb_err:
                        logger.warning("on_init callback raised: %s", _cb_err)
                continue

            if event_type == "result":
                result_text = data.get("result", "")
                session_id_from_harness = data.get("session_id")
                # Schema-first routing (plan #2000 Task 2.3): present iff
                # --json-schema was requested AND the CLI's own validation
                # succeeded (Task 2.1 empirical finding — absence, not a
                # malformed value, is the fallback-detection signal).
                raw_structured = data.get("structured_output")
                if isinstance(raw_structured, dict):
                    structured_output = raw_structured
                # Pillar A turn boundary (issue #1172). Bumps last_turn_at on
                # the in-flight AgentSession so the dashboard can show how
                # recently the SDK completed a turn. Best-effort, never raises.
                # Passes the true AgentSession.session_id explicitly (issue
                # #1935) — NOT data.get("session_id") (the Claude UUID) and NOT
                # the env AGENT_SESSION_ID (unset in the worker process; the
                # explicit id is what makes this write actually land).
                try:
                    from agent.hooks.liveness_writers import record_turn_boundary

                    record_turn_boundary(session_id=true_session_id)
                except Exception as _liveness_err:
                    logger.debug("liveness turn-boundary write failed: %s", _liveness_err)
                # Extract per-turn token + cost counts (issue #1128). These
                # are the harness-side counterpart of `ResultMessage.usage`
                # and `ResultMessage.total_cost_usd` from the SDK path. The
                # `claude -p stream-json` protocol emits them on the same
                # `result` event. `usage` is a dict; missing fields default
                # to 0 inside `accumulate_session_tokens`. `total_cost_usd`
                # is taken verbatim so it tracks upstream Anthropic pricing
                # without a local price table.
                raw_usage = data.get("usage")
                if isinstance(raw_usage, dict):
                    usage = raw_usage
                raw_cost = data.get("total_cost_usd")
                if isinstance(raw_cost, (int, float)):
                    cost_usd = float(raw_cost)
                # Issue #1245: extract per-call turn count off the result event.
                # The `claude -p stream-json` protocol emits num_turns alongside
                # cost/usage. Caller accumulates this onto AgentSession.turn_count
                # so primary + fallback subprocess invocations sum correctly.
                raw_turns = data.get("num_turns")
                if raw_turns is None:
                    # Spike (issue #1245) confirmed num_turns is always present on the
                    # result event in real harness runs. If it's ever missing, log once
                    # at debug — the test fixture covers this path explicitly.
                    logger.debug("[harness] result event missing num_turns")
                else:
                    try:
                        num_turns = int(raw_turns)
                    except (TypeError, ValueError):
                        logger.warning("[harness] result event num_turns not int: %r", raw_turns)
                if session_id_from_harness:
                    logger.debug(f"Harness session_id for resume: {session_id_from_harness}")
                break

            if event_type == "assistant":
                # Issue #1245: count tool_use blocks from assistant messages.
                # Each tool invocation appears as a content block with
                # type=="tool_use" inside data["message"]["content"]. The shape
                # is the real `claude -p stream-json` protocol — top-level
                # event.type=="tool_use" does NOT fire.
                message = data.get("message", {}) or {}
                content_blocks = message.get("content", []) or []
                tool_use_blocks = [
                    b for b in content_blocks if isinstance(b, dict) and b.get("type") == "tool_use"
                ]
                tool_call_count += len(tool_use_blocks)
                continue

            if event_type == "stream_event":
                event = data.get("event", {})
                if event.get("type") == "content_block_start":
                    full_text = ""
                elif event.get("type") == "content_block_delta":
                    delta = event.get("delta", {}) or {}
                    delta_type = delta.get("type")
                    if delta_type == "text_delta":
                        chunk = delta.get("text", "")
                        if chunk:
                            full_text += chunk
                    elif delta_type == "thinking_delta":
                        # Pillar A (issue #1172): bubble extended-thinking content
                        # to the dashboard so operators can see what the agent is
                        # mulling. Best-effort; throttled in liveness_writers.
                        chunk = delta.get("thinking", "") or delta.get("text", "")
                        if chunk:
                            try:
                                from agent.hooks.liveness_writers import (
                                    record_thinking_excerpt,
                                )

                                record_thinking_excerpt(chunk)
                            except Exception as _liveness_err:
                                logger.debug(
                                    "liveness thinking-delta write failed: %s", _liveness_err
                                )

        _, stderr_data = await proc.communicate()
        returncode = proc.returncode if proc.returncode is not None else 0
    finally:
        # Kill the subprocess if it is still alive because the awaiting
        # coroutine is being torn down (e.g. CancelledError). SAFETY: only
        # ``killpg`` when ``start_new_session`` is True — the subprocess then
        # owns its own group (pgid == pid). When it is False, ``os.getpgid``
        # returns the WORKER's group and ``killpg`` would kill the worker, so
        # fall back to a single-pid ``proc.kill()``. ``ProcessLookupError`` is
        # swallowed (group/pid already gone). CancelledError re-raises naturally.
        if proc.returncode is None:
            try:
                if start_new_session:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                else:
                    proc.kill()
            except ProcessLookupError:
                pass
            except Exception as _reap_err:  # noqa: BLE001
                logger.warning("[harness] cancellation reap failed (non-fatal): %s", _reap_err)
            # Init-hang diagnostics (issue #2181). When this subprocess is torn
            # down before it EVER emitted a stdout event (``not _first_stdout_seen``
            # — the communicated=False init-hang shape: no ``system/init``, no
            # ``result``), the real init blocker (MCP-server load, oauth, model
            # connect) is otherwise lost with the killed pipe. Drain and log the
            # buffered stderr now that the SIGKILL above has closed the pipes so
            # the read returns promptly at EOF. Best-effort and bounded; never
            # masks the cancellation being propagated by this ``finally``.
            if not _first_stdout_seen:
                await _log_init_hang_stderr(proc, true_session_id)

    # Fire SDK-finished callback once the subprocess has exited (#1269).
    # Paired with on_sdk_started — together they bracket the subprocess
    # lifetime so the worker can clear AgentSession.harness_pid the instant
    # the subprocess dies. This defeats PID-recycling false positives in
    # the dashboard liveness probe: a worker-spawned gh/git/pytest subprocess
    # would otherwise inherit the freed PID and be misreported as the live
    # harness. Single-digit milliseconds between this point and the callback
    # firing is the residual risk window — see Race 4 in the plan.
    if on_sdk_finished is not None:
        try:
            on_sdk_finished()
        except Exception as _cb_err:
            logger.warning("on_sdk_finished callback raised: %s", _cb_err)

    # Capture first 2000 chars of stderr for Mode 1 sentinel checks (issue #1099).
    # Bound the snippet at 2000 chars: ~4x the 500-char log-only window already
    # used below, enough for sentinel matching while keeping memory tight.
    stderr_snippet: str | None = None
    if returncode != 0:
        stderr_text = stderr_data.decode("utf-8", errors="replace") if stderr_data else ""
        stderr_snippet = stderr_text[:2000]
        logger.warning(f"Harness exited with code {returncode}: {stderr_text[:500]}")

    # Surface the subprocess exit shape to the caller (session-runner role
    # driver): result_text is non-None here iff a `result` event fired —
    # the accumulated-text fallback below returns full_text separately.
    if on_exit_status is not None:
        try:
            on_exit_status(returncode, result_text is not None)
        except Exception as _cb_err:
            logger.warning("on_exit_status callback raised: %s", _cb_err)

    # Early-exit classification (issue #2100 §2). Classify the exit shape into
    # a HarnessExitClass and, on a TLS/trust failure, emit a WARNING with an
    # explicitly non-destructive remediation line (NEVER mentions Keychain
    # reset/repair — a mis-click on the macOS dialog is the failure mode we are
    # designing out). The classification is surfaced via on_early_exit_class so
    # the caller can drive its per-session TLS-streak bookkeeping + retry
    # suppression.
    exit_class = classify_harness_early_exit(
        returncode=returncode,
        stderr_snippet=stderr_snippet,
        init_seen=init_seen,
        result_event_fired=result_text is not None,
    )
    if exit_class == HarnessExitClass.TLS_TRUST:
        logger.warning(
            "[harness] TLS trust failure from Claude Code CLI child (session_id=%s); "
            "inspect the [harness-spawn] diagnostic and the certificate chain. "
            "Do NOT reset the login keychain.",
            true_session_id,
        )
    if on_early_exit_class is not None:
        try:
            on_early_exit_class(exit_class)
        except Exception as _cb_err:
            logger.warning("on_early_exit_class callback raised: %s", _cb_err)

    if result_text is not None:
        return (
            result_text,
            session_id_from_harness,
            returncode,
            usage,
            cost_usd,
            stderr_snippet,
            num_turns,
            tool_call_count,
            structured_output,
        )
    if full_text:
        logger.warning(
            "Harness exited without result event, returning %d chars of accumulated text",
            len(full_text),
        )
        return (
            full_text,
            session_id_from_harness,
            returncode,
            usage,
            cost_usd,
            stderr_snippet,
            num_turns,
            tool_call_count,
            structured_output,
        )
    # BRANCH C: no result event and no accumulated text (issue #2219). Split the
    # single over-broad Sentry bucket (VALOR-2M) by exit class:
    #   - CLEAN_NO_OUTPUT (benign exit-0 empty turn) → logger.WARNING, which sits
    #     below Sentry's error threshold, so the dominant noise source drops out.
    #   - every other class → logger.ERROR inside an isolated sentry_sdk scope
    #     carrying the class tag + a per-class fingerprint
    #     (["harness-exit-no-result", <class>]) so each cause becomes its own
    #     Sentry issue that can be resolved/ignored independently.
    # The scope/tagging is best-effort: a tagging or import failure must never
    # mask the log line. The return tuple below is unchanged — no caller
    # behavior changes; the caller still handles text=None as an empty turn.
    log_level, sentry_payload = describe_harness_exit_for_sentry(
        exit_class,
        returncode,
        init_seen,
        stderr_snippet,
    )
    if log_level == logging.WARNING:
        logger.warning(
            "Harness exited cleanly (rc=0) with no result event and no streamed text; "
            "treating as empty turn"
        )
    else:
        try:
            import sentry_sdk  # noqa: PLC0415 — local import mirrors agent/index_drift.py

            with sentry_sdk.new_scope() as scope:
                for _tag, _val in sentry_payload["tags"].items():
                    scope.set_tag(_tag, _val)
                for _ctx_key, _ctx_val in sentry_payload["context"].items():
                    scope.set_context(_ctx_key, _ctx_val)
                scope.fingerprint = sentry_payload["fingerprint"]
                logger.error("Harness exited without a result event and no accumulated text")
        except Exception:  # noqa: BLE001 — tagging must never mask the log line
            logger.error("Harness exited without a result event and no accumulated text")
    return (
        None,
        session_id_from_harness,
        returncode,
        usage,
        cost_usd,
        stderr_snippet,
        num_turns,
        tool_call_count,
        structured_output,
    )


async def verify_harness_health(harness_name: str) -> bool:
    """Check if a CLI harness is available and working.

    For claude-cli: verifies the binary exists on PATH and can produce
    a system init event. Checks apiKeySource for billing warnings.

    Returns True if healthy, False otherwise.
    """
    if harness_name not in _HARNESS_COMMANDS:
        logger.warning(f"Unknown harness: {harness_name}")
        return False

    cmd_template = _HARNESS_COMMANDS[harness_name]
    binary = cmd_template[0]

    if not shutil.which(binary):
        logger.warning(f"Harness binary not found on PATH: {binary}")
        return False

    try:
        # Run a minimal test command — we only need the system init event
        # (emitted before any API call), so kill the process immediately
        # after receiving it to avoid a full API round-trip.
        test_cmd = cmd_template + ["test"]
        # Issue #2100 follow-up #1: this second claude spawn site previously
        # passed NO env= and inherited full os.environ (including any
        # ANTHROPIC_*), violating AC7's "every claude spawn strips the three
        # auth vars" guarantee. Build an explicit stripped env and emit the
        # sanitized spawn diagnostic here too (scoped to the claude health probe).
        health_env = stripped_harness_env(os.environ)
        try:
            _spawn_diag = build_spawn_diagnostic(
                test_cmd,
                health_env,
                os.getcwd(),
                None,
                _default_worker_label(),
            )
            logger.info("[harness-spawn] %s", json.dumps(_spawn_diag))
        except Exception as _diag_err:  # noqa: BLE001
            logger.warning("[harness] health-probe spawn diagnostic failed: %s", _diag_err)
        proc = await asyncio.create_subprocess_exec(
            *test_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=health_env,
        )

        # Read stdout line-by-line, kill as soon as we see the system event
        healthy = False
        assert proc.stdout is not None
        while True:
            try:
                raw_line = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=_HARNESS_HEALTH_READLINE_TIMEOUT_S
                )
            except TimeoutError:
                logger.warning(f"Harness {harness_name} timed out waiting for system init event")
                break
            if not raw_line:
                break  # EOF
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            if data.get("type") == "system":
                api_source = data.get("apiKeySource", "unknown")
                if api_source not in ("none", ""):
                    logger.warning(
                        f"Harness {harness_name} using API key billing (apiKeySource={api_source})"
                    )
                logger.info(
                    f"Harness {harness_name} health check passed (apiKeySource={api_source})"
                )
                healthy = True
                break

        # Terminate immediately — no need to wait for the full API response
        try:
            proc.kill()
        except ProcessLookupError:
            pass  # Already exited
        await proc.wait()

        if not healthy:
            logger.warning(f"Harness {harness_name} did not produce system init event")
        return healthy

    except Exception as e:
        logger.error(f"Harness health check failed for {harness_name}: {e}")
        return False


async def build_harness_turn_input(
    message: str,
    session_id: str,
    sender_name: str | None,
    chat_title: str | None,
    project: dict | None,
    task_list_id: str | None,
    session_type: str | None,
    sender_id: int | None,
    classification: str | None = None,
    is_cross_repo: bool = False,
    *,
    skip_prefix: bool = False,
    persona: str | None = None,
    email_from: str | None = None,
    email_sender_name: str | None = None,
) -> str:
    """Build context-enriched message for CLI harness execution.

    Extracts the message enrichment logic that was previously inline in
    the (deleted) SDK client's response handler into a standalone function. Produces a
    context-prefixed message with PROJECT, FROM, SESSION_ID, TASK_SCOPE,
    and SCOPE headers suitable for any session type.

    When ``skip_prefix`` is True, returns the raw ``message`` unchanged.
    Used on resumed turns where the CLI binary already has prior context
    from its session file (#976).

    Args:
        message: Raw message text (already media-enriched by process_session).
        session_id: Session ID for conversation continuity.
        sender_name: Name of the sender (omitted from output if None).
        chat_title: Chat title for logging context.
        project: Project configuration dict from projects.json.
        task_list_id: Optional task list ID for sub-agent scoping.
        session_type: Session type (dev, pm, teammate).
        sender_id: Telegram user ID for permission checking.
        classification: Classification type from bridge (e.g., "sdlc", "question").
        is_cross_repo: Whether this is a cross-repo project (project_key != "valor").
        skip_prefix: If True, return raw message without context headers.
        persona: Resolved persona name. Passed to build_context_prefix so that
            customer-service sessions are not given the teammate read-only restriction.
        email_from: Email address of the contact being served (email bridge only).
        email_sender_name: Display name paired with email_from.

    Returns:
        Enriched message string with context headers prepended, or raw message
        if skip_prefix is True.
    """
    if skip_prefix:
        return message
    from bridge.context import build_context_prefix

    enriched = build_context_prefix(
        project,
        session_type,
        sender_id,
        persona=persona,
        email_from=email_from,
        sender_name=email_sender_name,
    )

    if sender_name:
        enriched += f"\n\nFROM: {sender_name}"
        if chat_title:
            enriched += f" in {chat_title}"
    elif chat_title:
        enriched += f"\n\nin {chat_title}"

    if session_id:
        enriched += f"\nSESSION_ID: {session_id}"
    if task_list_id:
        enriched += f"\nTASK_SCOPE: {task_list_id}"

    enriched += (
        "\nSCOPE: This session is scoped to the message below from this sender. "
        "When reporting completion or summarizing work, only reference tasks and "
        "work initiated in this specific session. Do not include work, PRs, or "
        "requests from other sessions, other senders, or prior conversation threads."
    )

    # Cross-repo SDLC: inject target repo context
    if classification == ClassificationType.SDLC and is_cross_repo:
        project_name = project.get("name", "Unknown") if project else "Unknown"
        project_working_dir = project.get("working_directory", "") if project else ""
        github_config = project.get("github", {}) if project else {}
        github_org = github_config.get("org", "")
        github_repo = github_config.get("repo", "")
        enriched += (
            f"\nWORK REQUEST for project {project_name}.\nTARGET REPO: {project_working_dir}"
        )
        if github_org and github_repo:
            enriched += f"\nGITHUB: {github_org}/{github_repo}"

    enriched += f"\nMESSAGE: {message}"
    return enriched


class ClaudeHarnessAdapter:
    """:class:`~agent.session_runner.harness.base.HarnessAdapter` for the
    ``claude -p`` CLI.

    Wraps :func:`get_response_via_harness` (this module), translating its
    callback-based side channel (spawn / init / stdout / exit-status /
    usage) into a normalized :class:`TurnResult` carrying an ``events``
    list. All claude-specific subprocess/argv/stream-json knowledge stays
    inside :func:`get_response_via_harness` / :func:`_run_harness_subprocess`
    -- this class only normalizes their callback surface for the runner.
    """

    def __init__(self, harness_fn: Callable[..., Awaitable[str]] | None = None) -> None:
        # Injectable for tests -- mirrors the pre-seam `harness_fn`
        # constructor param on HeadlessRoleDriver. A bare async callable
        # with the get_response_via_harness(message, working_dir, **kwargs)
        # signature keeps working unchanged through this seam. Left None
        # here (NOT resolved to the module-local get_response_via_harness at
        # construction time): the default is resolved at call time in
        # run_turn() via a deferred import from agent.sdk_client, so tests
        # that patch "agent.sdk_client.get_response_via_harness" (the public
        # re-exported name callers have patched since before this seam
        # existed) keep intercepting the DEFAULT (no-injection) construction
        # path used by production code (HeadlessRoleDriver's default
        # ClaudeHarnessAdapter()), not just tests that pass harness_fn=
        # explicitly.
        self._harness_fn = harness_fn

    async def run_turn(
        self,
        request: TurnRequest,
        *,
        on_event: Callable[[TurnEvent], None] | None = None,
    ) -> TurnResult:
        """Run one claude -p turn and return a normalized :class:`TurnResult`.

        Emits :data:`~agent.session_runner.harness.events.SESSION_STARTED`
        via ``on_event`` the instant the subprocess reports its session id
        (Race 1) -- before this coroutine returns -- so a caller that
        persists the resume handle from ``on_event`` is crash-safe even if
        the worker process dies mid-turn.
        """
        collected_events: list[TurnEvent] = []
        resume_handle: str | None = None
        returncode: int | None = None
        result_event_fired: bool | None = None
        usage: dict | None = None
        cost_usd: float | None = None
        structured_output: dict[str, Any] | None = None

        def _emit(event_type: str, data: dict | None = None) -> None:
            evt = TurnEvent(type=event_type, data=data or {})
            collected_events.append(evt)
            if on_event is not None:
                try:
                    on_event(evt)
                except Exception as _cb_err:  # noqa: BLE001
                    logger.warning("[harness-adapter] on_event callback raised: %s", _cb_err)

        def _on_init(data: dict) -> None:
            nonlocal resume_handle
            sid = data.get("session_id")
            resume_handle = str(sid) if sid else None
            _emit(_events.SESSION_STARTED, {"handle": resume_handle, "raw": data})

        def _on_sdk_started(pid: int) -> None:
            _emit(_events.TURN_SPAWNED, {"pid": pid})

        def _on_sdk_finished() -> None:
            _emit(_events.TURN_EXITED, {})

        def _on_stdout_event() -> None:
            _emit(_events.ITEM_STDOUT, {})

        def _on_exit_status(rc: int | None, fired: bool) -> None:
            nonlocal returncode, result_event_fired
            returncode = rc
            result_event_fired = fired

        def _on_usage(u: dict | None, c: float | None) -> None:
            nonlocal usage, cost_usd
            usage = u
            cost_usd = c

        def _on_structured_output(so: dict[str, Any] | None) -> None:
            nonlocal structured_output
            structured_output = so

        harness_fn = self._harness_fn
        if harness_fn is None:
            # Deferred import (not module-load-time): resolves whatever
            # get_response_via_harness currently IS on agent.sdk_client at
            # call time, so `patch("agent.sdk_client.get_response_via_harness",
            # ...)` still intercepts the default (no-injection) path.
            from agent.sdk_client import get_response_via_harness as harness_fn  # noqa: PLC0415

        final_text = await harness_fn(
            request.message,
            request.working_dir,
            harness_cmd=request.harness_cmd,
            env=request.env,
            prior_uuid=request.prior_uuid,
            session_id=request.session_id,
            full_context_message=request.full_context_message,
            model=request.model,
            system_prompt=request.system_prompt,
            settings_path=request.settings_path,
            role=request.role,
            start_new_session=request.start_new_session,
            json_schema=request.json_schema,
            on_sdk_started=_on_sdk_started,
            on_sdk_finished=_on_sdk_finished,
            on_stdout_event=_on_stdout_event,
            on_init=_on_init,
            on_exit_status=_on_exit_status,
            on_usage=_on_usage,
            on_structured_output=_on_structured_output,
        )

        _emit(
            _events.TURN_COMPLETED,
            {
                "usage": usage,
                "cost_usd": cost_usd,
                "returncode": returncode,
                "result_event_fired": result_event_fired,
            },
        )

        return TurnResult(
            resume_handle=resume_handle,
            final_text=final_text,
            structured_output=structured_output,
            events=collected_events,
            usage=usage,
            cost_usd=cost_usd,
            returncode=returncode,
            result_event_fired=result_event_fired,
        )
