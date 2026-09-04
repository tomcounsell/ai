"""Promise gate — honesty gate for agent-to-user delivery paths.

This module is the centralised judgment function that decides whether an
outbound message contains an *empty forward-deferral promise* (e.g.
"I'll come back with X", "will follow up", "stay tuned", "more soon",
"I'll report back") that the agent cannot keep, because the agent's
session is ending by the time the message reaches the user.

Architecture
------------
The gate is **LLM-first**. The primary judgment layer is a Haiku call
with a strengthened few-shot prompt that names a *forward-deferral*
class. A regex backstop (``_evaluate_promise_heuristic``) is the
**fail-closed-only** last line that fires solely on the heuristic-
fallback branch (no API key / SDK exception / parse failure). The
heuristic does NOT override an LLM ``ALLOW``.

This split — LLM primary, regex fail-closed-only — is mandated by the
issue (#1219) and the user-memory record ``feedback_llm_drafter_over_regex``.

Posture
-------
* **Heuristic fallback fail-closed** (the regex branch returns BLOCK on
  match without evidence). The cost of false-positive is loud and
  recoverable: the sender (agent or operator) sees the BLOCK on stderr,
  rephrases per the recovery template, and the second call almost always
  passes. Compare with ``bridge/read_the_room.py`` which is a redundancy
  gate where false-positive is silent message loss → fail-open.
* **Infrastructure failures fail-open** (``cli_check_or_exit`` swallows
  unexpected ``evaluate_promise()`` raises). Distinct from the heuristic
  fallback above: an asyncio nested-loop, an ImportError from a circular
  import, or an AttributeError from a Popoto schema migration is an
  infrastructure failure, not a judgment failure. Cost of false-positive
  here is silent message loss, identical to RTR's failure mode → fail-open
  is correct.

Public surface
--------------
* ``PromiseVerdict`` — the verdict dataclass returned to call sites.
* ``evaluate_promise(text, *, transport, session_id=None,
  classifier_verdict=None) -> PromiseVerdict`` — sync judgment function.
* ``evaluate_promise_async(...) -> PromiseVerdict`` — the async core
  ``evaluate_promise`` wraps. Use directly from an already-running event
  loop (e.g. the drafter's main path) instead of the sync wrapper, which
  raises inside a running loop.
* ``cli_check_or_exit(text, transport, session_id) -> None`` — the
  CLI helper. Calls ``evaluate_promise`` and on BLOCK prints the
  recovery template to stderr + ``sys.exit(1)``. There is **no
  per-call bypass flag**.

Operator escape hatch
---------------------
The only escape hatch is the process-wide kill switch
``PROMISE_GATE_ENABLED=false``, set in the operator's env file or shell
startup. It is **NOT** advertised in the recovery template — the
template must not teach the bypass syntax to the agent. The kill
switch exists for incident response (e.g. a regression rolling out a
100% block rate), not as a per-message bypass.

Telemetry
---------
Two channels with documented asymmetry:

1. **Audit JSONL** (``logs/classification_audit.jsonl``) — universal.
   Fires on every gate call regardless of session_id provenance. Uses
   a forked ``_write_promise_audit`` helper that writes verdict-specific
   fields (``action``, ``reason``, ``class_``, ``transport``,
   ``session_id``, ``source``, ``kind="promise_gate"``).
2. **session_events** — conditional. Fires only when
   ``AgentSession.get_by_id(session_id)`` returns a real session
   (real ``VALOR_SESSION_ID`` from the worker harness). Synthetic
   ``cli-{epoch}`` IDs silently skip session_events emission.

The ``session_id`` provenance differs across the CLI paths:
``send_message.py`` reads real ``VALOR_SESSION_ID`` (or accepts whatever its
caller passes); ``valor_telegram.py`` and ``valor_email.py`` use synthetic IDs.

Latency
-------
Budget for the LLM path: p50 <= 2500ms, p99 <= 5000ms (owner ruling
2026-09-03, set at roughly 1.5x the p50/p99 of 1619ms/2463ms that the
ruling was computed against; provisional/tunable, re-derive from
post-merge audit JSONL). The zero-LLM short path
(<200 chars, non-SDLC, no artifacts) keeps its existing guarantee of
p50 ~= 0ms and is unchanged by this budget. This latency budget is
separate from the SDK-level per-call timeout: the semaphore acquire and
the API call are each separately bounded at 3 seconds via the
RTR-correct pattern: ``async with
semaphore_slot(timeout=RTR_SDK_TIMEOUT): async with
anthropic.AsyncAnthropic(timeout=RTR_SDK_TIMEOUT, max_retries=0) as
client:``. Stacking both 3-second bounds gives a structural worst case
of ~6 seconds, above the documented p99 of 5000ms — this is a
structural bound on the worst case, not the measured distribution
(the current measurement over the LLM-path bucket, n=592, is p50
~1637ms / p99 ~2482ms, comfortably inside the 5000ms budget).
``max_retries=0`` is load-bearing for the stated bound: the SDK default
(``DEFAULT_MAX_RETRIES = 2``) retries client-side timeouts, which would
silently turn the 3s worst case into ~3 attempts plus backoff (~10s) on
this call's now-inline delivery path.
The other anthropic-client helper (the convenience one that
constructs the client for you) is **not** used here — it does not
accept a ``timeout`` argument and would silently violate the 3-second
budget. Coroutine-level timeouts are forbidden (PR #1055 invariant —
they leak httpx connections under cancellation).

The ``RTR_SDK_TIMEOUT`` constant is **imported** from
``bridge.read_the_room`` rather than redefined locally — both gates
share the same SDK invariant from PR #1055; copying the literal would
risk drift.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import anthropic

from agent.anthropic_client import semaphore_slot
from bridge.read_the_room import RTR_SDK_TIMEOUT  # cycle-3 C-CYCLE3-1: import, do NOT redefine
from config.models import MODEL_FAST
from utils.api_keys import get_anthropic_api_key

logger = logging.getLogger(__name__)


# === Verdict dataclass ===


@dataclass
class PromiseVerdict:
    """Outcome of a promise-gate evaluation.

    Attributes:
        action: One of ``"allow"`` or ``"block"`` (two-state — no WARN).
        reason: Short machine-readable reason string.
        class_: Optional class label (e.g. ``"forward_deferral"``,
            ``"behavioral_change"``). ``None`` for ALLOW verdicts.
    """

    action: Literal["allow", "block"]
    reason: str = ""
    class_: str | None = None


# === System prompt (LLM-first; forward-deferral class explicitly named) ===

PROMISE_GATE_SYSTEM_PROMPT = """\
You are a pre-send honesty gate for an AI assistant that emits messages \
that reach a human via Telegram or email. The assistant has just produced \
a draft. Your job is to decide whether the draft contains an *empty \
forward-deferral promise* the assistant cannot keep.

CRITICAL CONTEXT: By the time a draft reaches the user, the assistant's \
session is OVER. There is no future execution. The assistant cannot \
"will do" or "come back with" anything — it has already finished. Any \
"I'll come back with X", "will follow up", "stay tuned", "more soon", \
"I'll report back" language is a forward-deferral promise UNLESS the \
draft references a verifiable autonomous-delivery mechanism (a queued \
session ID, a scheduled cron, a scheduled agent — surfaced as a \
``session_id``, ``schedule_id``, or PR URL).

The forward-deferral class is forbidden unless the deferral itself names \
a verifiable autonomous-delivery reference. **Even when the deferral is \
combined with substantive content (file paths, commit hashes, \
descriptions of work done), the deferral itself is the violation and \
must be classified as BLOCK.** The exception is when the deferral itself \
names a verifiable autonomous-delivery reference.

There is also a legacy *behavioral-change* class: "got it / will do / \
going forward / won't happen again" without evidence — these are also \
BLOCK unless the draft includes evidence (commit hash, file path, \
memory write, service restart).

Decide one of two actions:

- "allow" — the draft is honest. Either it claims no future work \
("I did X with evidence Y", "I didn't do X because Y"), or it references \
a verifiable autonomous-delivery mechanism for any forward-deferral.
- "block" — the draft contains a forward-deferral or behavioral-change \
promise without evidence and without a verifiable autonomous-delivery \
reference.

Few-shot examples:

Input: "Reading the docs now, will come back with thoughts."
Output: action=block, class=forward_deferral, reason="Forward-deferral \
without evidence or verifiable scheduled-delivery reference"

Input: "I queued session abc1234ef. You'll get a Telegram message when \
it completes."
Output: action=allow, reason="Forward-deferral with verifiable \
scheduled-delivery reference (session ID)"

Input: "Found three issues in `bridge/foo.py`. I'll come back with \
fixes once tests run."
Output: action=block, class=forward_deferral, reason="Forward-deferral \
combined with substantive content but no scheduled-delivery reference \
— the deferral itself is the violation"

Input: "Got it. Will report final results and blockers only."
Output: action=block, class=behavioral_change, reason="Behavioral-change \
acknowledgment without evidence of a durable change"

Input: "Updated bridge/foo.py to handle the edge case. Committed \
abc1234."
Output: action=allow, reason="Concrete action with file path and commit \
hash evidence"

Input: "I'll send a follow-up email later."
Output: action=block, class=forward_deferral, reason="Ambiguous \
forward-deferral with no delivery mechanism"

You MUST call the `promise_verdict` tool with a flat structured result \
(`action`, `reason`, `class_`)."""


# === Tool schema (Haiku tool_use) ===

_PROMISE_VERDICT_TOOL = {
    "name": "promise_verdict",
    "description": (
        "Return the promise-gate verdict for the candidate draft. "
        "Action must be one of allow|block."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["allow", "block"],
                "description": "Verdict action.",
            },
            "reason": {
                "type": "string",
                "description": "Short machine-readable reason string.",
            },
            "class_": {
                "type": ["string", "null"],
                "description": (
                    "Class label (e.g. 'forward_deferral', "
                    "'behavioral_change'). Null for allow verdicts."
                ),
            },
        },
        "required": ["action", "reason"],
    },
}


# === Heuristic patterns (fail-closed-only fallback branch) ===

# Forward-deferral phrases: agent commits to deliver future information
# without same-session evidence. These fire only inside the heuristic
# branch (no API key / SDK exception / parse failure).
_FORWARD_DEFERRAL_PATTERNS = [
    r"\bi'?ll\s+(?:come|get|circle|loop)\s+back\b",
    r"\bi'?ll\s+(?:report|follow)\s+(?:back|up)\b",
    r"\bstay\s+tuned\b",
    r"\bmore\s+(?:soon|to\s+come)\b",
    r"\bwill\s+(?:report|follow|circle)\s+(?:back|up)\b",
]

# Behavioral-change acknowledgment patterns (legacy class — preserved
# verbatim from ``bridge.message_drafter._detect_empty_promise``).
_BEHAVIORAL_CHANGE_PATTERNS = [
    r"\b(?:got it|understood|noted|will do|roger|acknowledged|fair point)\b",
    r"\b(?:you're right|good point|makes sense|point taken)\b",
    r"\b(?:i'll update|i'll change|i'll fix|i'll adjust|i'll modify)\b",
    r"\b(?:won't happen again|will remember|going forward)\b",
    r"\byou'll see the difference\b",
]

# Evidence patterns — concrete proof that a change was made. Override
# the BLOCK verdict on a behavioral-change match.
_EVIDENCE_PATTERNS = [
    r"\b[0-9a-f]{7,40}\b",  # commit hash
    r"\bcommit(?:ted)?\b.*\b[0-9a-f]{7}\b",  # "committed abc1234"
    # file paths (saved/wrote/created/updated to some/file.ext)
    r"(?:saved|wrote|created|updated|edited|modified)\s+(?:to\s+)?[`'\"]?[\w/]+\.\w+",
    r"\bmemory\b.*\b(?:saved|written|created|updated)\b",
    r"\b(?:saved|written|created)\b.*\bmemory\b",
    r"https?://github\.com/.+/commit/",
    r"\brestarted?\b.*\b(?:bridge|service)\b",
    r"\b(?:scheduled|queued)\b.*\bsession[_-]?[a-f0-9]{6,}\b",  # session ID
]

# Scheduled-delivery sub-pattern — the ONLY override for a
# forward-deferral match (per Blocker B2 decided rule). Substantive
# content (file paths, commit hashes) does NOT override BLOCK on a
# forward-deferral; only verifiable scheduled-delivery references do.
_SCHEDULED_DELIVERY_PATTERNS = [
    r"\b(?:scheduled|queued)\s+session[_-]?[a-f0-9]{6,}\b",
    r"\b(?:scheduled|queued)\s+session\s+[a-f0-9]{6,}\b",
    r"\bschedule_id[=:]?\s*[a-f0-9-]{6,}\b",
    r"https?://github\.com/.+/pull/\d+",  # PR URL surfaces autonomous delivery
]


def _matches_any(text_lower: str, patterns: list[str]) -> bool:
    return any(re.search(p, text_lower) for p in patterns)


def _evaluate_promise_heuristic(text: str) -> PromiseVerdict:
    """Heuristic fallback verdict (fail-closed-only).

    Fires only when the LLM cannot return a parseable verdict. Returns
    BLOCK on a forward-deferral pattern match without a scheduled-
    delivery reference, OR a behavioral-change pattern match without
    evidence. Otherwise ALLOW.
    """
    text_lower = (text or "").lower()

    if not text_lower.strip():
        return PromiseVerdict(action="allow", reason="empty_input")

    # Forward-deferral branch — overridden ONLY by scheduled-delivery
    # references, NOT by general substantive-content evidence (per
    # Blocker B2 decided rule).
    if _matches_any(text_lower, _FORWARD_DEFERRAL_PATTERNS):
        if _matches_any(text_lower, _SCHEDULED_DELIVERY_PATTERNS):
            return PromiseVerdict(
                action="allow",
                reason="forward_deferral_with_scheduled_delivery",
            )
        return PromiseVerdict(
            action="block",
            reason="Forward-deferral without verifiable scheduled-delivery reference",
            class_="forward_deferral",
        )

    # Behavioral-change branch — overridden by general evidence.
    if _matches_any(text_lower, _BEHAVIORAL_CHANGE_PATTERNS):
        if _matches_any(text_lower, _EVIDENCE_PATTERNS):
            return PromiseVerdict(
                action="allow",
                reason="behavioral_change_with_evidence",
            )
        return PromiseVerdict(
            action="block",
            reason="Behavioral-change acknowledgment without evidence",
            class_="behavioral_change",
        )

    return PromiseVerdict(action="allow", reason="no_promise_detected")


# === Backward-compat alias used by ``bridge.message_drafter`` ===


def _detect_empty_promise(text_lower: str) -> bool:
    """Return True if the text looks like an unfulfilled behavioral promise.

    Evaluates the text against the promise-gate heuristic. Returns ``True``
    if the result is BLOCK (forward-deferral or behavioral-change
    acknowledgment without evidence), ``False`` otherwise.
    """
    verdict = _evaluate_promise_heuristic(text_lower)
    return verdict.action == "block"


# === Kill-switch (env-var, read fresh per call) ===


def _gate_enabled() -> bool:
    """Read ``PROMISE_GATE_ENABLED`` env var fresh on each call.

    Default is ``"true"`` (gate enabled). Per the plan's documented
    contract (§Failure Path Test Strategy → Kill Switch Coverage):

      ``PROMISE_GATE_ENABLED=`` (empty), ``PROMISE_GATE_ENABLED``
      (unset), or any value not in {``"1"``, ``"true"``, ``"yes"``,
      ``"on"``} → gate is enabled (default-on).

    The structural shape mirrors ``bridge/read_the_room.py:_read_enabled``,
    but RTR's default is ``"false"`` (opt-in feature) so an empty-string
    env var matches RTR's default-off state invisibly. Here the default
    is ``"true"`` (default-on safety control), so empty-string MUST be
    treated as the default rather than as a disable signal — otherwise
    a stray ``PROMISE_GATE_ENABLED=`` in an env file silently disables
    the gate while telemetry shows ``source="promise_gate_disabled"``
    on every send.

    Only an explicit non-empty value that is not in the allow-set
    disables the gate. Whitespace-only values are treated as empty
    (no operator would intend whitespace as a disable signal).
    """
    raw = os.environ.get("PROMISE_GATE_ENABLED", "true")
    normalized = raw.strip().lower()
    # Empty / whitespace-only → treat as the default ("true") per the
    # documented contract. Without this branch the default would only
    # fire on a missing key, leaving a stray ``PROMISE_GATE_ENABLED=``
    # silently disabling the gate.
    if not normalized:
        normalized = "true"
    return normalized in ("1", "true", "yes", "on")


# === Advisory expectation flow (#2494 Task 14, generalized by #2708) ===
#
# The gate is ADVISORY to the PM: on a deferral-shaped outbound it returns a
# revise-or-override suggestion instead of mechanically writing an
# obligation (no trigger class ever writes an obligation from a verdict).
# Standing by an obligation means the PM records an INBOUND expectation on
# the bound Job via ``tools/job_tool expectation-add --direction inbound``
# — and a recorded OPEN inbound expectation is the override signal the gate
# honors on the resend (read-only check). The advisory should also
# recognize, in prose, the issue-comment grammar: a bare reassurance leaks
# an inbound obligation; a third-party future ("the lane will ship X")
# leaks an outbound one worth recording with --direction outbound. No new
# regexes back this — mechanical broadening is unfalsifiable at ~0.44
# blocks/day (two false-positive incidents).


def promise_override_active(session) -> bool:
    """True when the PM has stood by an inbound expectation on the bound Job.

    Read-only: resolves the session's Job through the reply index and
    checks for any OPEN **inbound** expectation entry. An open inbound
    expectation means the deferral is backed by a durable, reconciled
    record, so the honesty gate's premise ("the session ends and nothing
    will deliver this") no longer holds. Discharged expectations do not
    override, and OUTBOUND expectations (what a spawned lane owes the PM —
    including the spawn chokepoint's mechanical null-fallback entries)
    never clear this gate: they say nothing about what we owe the
    requester.

    **The override is JOB-scoped by design**, not per-message: ANY open
    inbound expectation on the bound Job clears the gate for every outbound
    on that Job until discharge. This is the advisory framing (#2494 Task
    14, carried into #2708) — the gate is a suggestion to the intelligent
    actor, and once the PM has durably stood by an obligation, re-blocking
    each subsequent deferral on the same Job would be a nag machine. Not an
    accident; do not "tighten" this to per-message matching without an
    owner ruling.
    """
    try:
        from bridge.job_router import job_for_session

        job = job_for_session(session)
        return bool(job is not None and job.open_expectations(direction="inbound"))
    except Exception as e:  # noqa: BLE001 — override check must never break the gate
        logger.debug("[promise-gate] override check failed: %s", e)
        return False


def build_promise_advisory(text: str, verdict: PromiseVerdict, session) -> str:
    """Compose the revise-or-override suggestion for a promise-flagged draft.

    Returned to the PM through the self-draft steering path — a suggestion
    to the intelligent actor, never a mechanical write (this function and
    everything it calls are strictly read-only; the zero-writes test
    enforces that). While the bound Job's goal is still the mint
    placeholder, the same pass carries the goal-authoring nudge, giving the
    goal mandate its second enforcement point (priming is the first).

    Grammar the advisory recognizes (prose guidance, deliberately NOT new
    regexes): a bare reassurance ("on it", "handled") leaks an inbound
    obligation; a third-party future ("the lane will ship the PR") leaks an
    outbound one the PM should record with ``--direction outbound``.
    """
    job = None
    try:
        from bridge.job_router import job_for_session

        job = job_for_session(session)
    except Exception as e:  # noqa: BLE001 — advisory must survive resolution failure
        logger.debug("[promise-gate] advisory job resolution failed: %s", e)

    lines = [
        "Advisory: this message sounds like you're promising future work "
        f"({verdict.class_ or 'promise'}: {verdict.reason}). We don't make "
        "false promises — by the time this message is read, this session may "
        "be over.",
        "Either REVISE the message to claim only what is already done (with "
        "evidence), or STAND BY the obligation by recording an expectation:",
    ]
    if job is not None:
        lines.append(
            f"  python -m tools.job_tool expectation-add --job-id {job.job_id} "
            '--direction inbound --owner pm --text "<exactly what you promised>"'
        )
        lines.append(
            "then resend — a recorded open inbound expectation clears this "
            "gate and keeps the Job active until you discharge it "
            f"(expectation-remove) on delivery. This message's Job: {job.job_id}. "
            "If a spawned lane will deliver it, also record the lane's side: "
            "expectation-add --direction outbound --owner <lane id/slug>."
        )
        if job.goal_is_placeholder():
            lines.append(
                "Also: this Job's goal is still the mechanical mint placeholder "
                f"({job.current_goal()!r}). Author the real goal now — "
                f"python -m tools.job_tool author-goal --job-id {job.job_id} "
                '--text "<what done looks like>" — it is your mandated first '
                "step on any Job."
            )
    else:
        lines.append(
            "  (no Job is bound to this session, so a promise cannot be "
            "recorded — revise the message to claim only delivered work)"
        )
    return "\n".join(lines)


# === Telemetry: forked audit helper + best-effort session_event emission ===

_AUDIT_LOG_PATH = Path(__file__).parent.parent / "logs" / "classification_audit.jsonl"
_AUDIT_LOG_MAX_SIZE = 10 * 1024 * 1024  # 10 MB


def _write_promise_audit(
    text: str,
    verdict: PromiseVerdict,
    *,
    transport: str,
    session_id: str | None,
    source: str,
    elapsed_ms: float | None = None,
    queue_wait_ms: float | None = None,
) -> None:
    """Appends a JSONL entry to the classification audit log
    (``logs/classification_audit.jsonl``) with verdict-specific fields:
    ``{ts, kind: "promise_gate", text_preview, action, reason, class_,
    transport, session_id, source}``, plus optional ``elapsed_ms`` /
    ``queue_wait_ms`` latency fields (omitted when not measured — e.g. the
    kill-switch and classifier-delegation short-circuits never call the LLM,
    so there is nothing to time). Readers must tolerate rows written before
    this instrumentation existed (no ``elapsed_ms``/``queue_wait_ms`` keys)
    and legacy rows with no ``kind`` field at all.
    """
    try:
        from datetime import UTC, datetime

        _AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

        if _AUDIT_LOG_PATH.exists() and _AUDIT_LOG_PATH.stat().st_size > _AUDIT_LOG_MAX_SIZE:
            rotated = _AUDIT_LOG_PATH.with_suffix(".jsonl.1")
            _AUDIT_LOG_PATH.rename(rotated)

        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "kind": "promise_gate",
            "text_preview": (text or "")[:200],
            "action": verdict.action,
            "reason": verdict.reason,
            "class_": verdict.class_,
            "transport": transport,
            "session_id": session_id,
            "source": source,
        }
        if elapsed_ms is not None:
            entry["elapsed_ms"] = round(elapsed_ms, 2)
        if queue_wait_ms is not None:
            entry["queue_wait_ms"] = round(queue_wait_ms, 2)
        with open(_AUDIT_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.debug(f"promise_gate audit log write failed (non-fatal): {e}")


def _emit_session_event_if_real(
    session_id: str | None,
    event: dict[str, Any],
) -> None:
    """Best-effort session_events emission, conditional on real AgentSession.

    Calls ``AgentSession.get_by_id(session_id)`` (Popoto ORM via the
    canonical raw-string lookup helper, never raw Redis per CLAUDE.md).
    On real-session hit, appends ``event`` to ``session.session_events``
    and saves. On miss (synthetic ``cli-{epoch}`` ID, stale ID, lookup
    error), silently no-ops.

    This honors Concern C6 — the gate makes no AgentSession state-driven
    decision; the existence check on the explicit input is for
    telemetry routing only.
    """
    if not session_id:
        return
    try:
        from models.agent_session import AgentSession

        session = AgentSession.get_by_id(session_id)
        if session is None:
            return
        events = list(getattr(session, "session_events", None) or [])
        events.append(event)
        session.session_events = events
        if hasattr(session, "save"):
            session.save()
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"promise_gate session_events emission failed (non-fatal): {e}")


def _make_event(
    event_type: str,
    *,
    text: str | None,
    transport: str,
    session_id: str | None,
    verdict: PromiseVerdict,
    source: str,
) -> dict[str, Any]:
    """Build a session_events dict for the promise gate."""
    return {
        "type": event_type,
        "ts": time.time(),
        "transport": transport,
        "session_id": session_id,
        "action": verdict.action,
        "reason": verdict.reason,
        "class_": verdict.class_,
        "source": source,
        "text_preview": (text or "")[:200],
    }


# === LLM async helper (RTR-correct SDK pattern) ===

# Per-Task queue-wait measurement (Risk 1b). A ``ContextVar`` rather than a
# module-level mutable is required for correctness under concurrent
# fire-and-forget ``draft_message`` calls: ``asyncio.create_task`` copies the
# current ``Context`` at task-creation time, so concurrent tasks never see
# each other's writes, while calls within the SAME task (a plain ``await``
# chain, as here) share one Context and the write is visible to the caller
# after the callee returns.
_queue_wait_ms: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "_promise_gate_queue_wait_ms", default=None
)


async def _evaluate_promise_async(text: str) -> PromiseVerdict | None:
    """Run the Haiku call for the LLM-primary path.

    Returns the parsed verdict on success, or ``None`` on any failure that
    is NOT a timeout (no API key, parse failure, non-timeout SDK exception).
    The caller falls through to the heuristic on ``None``.

    Raises:
        anthropic.APITimeoutError: the SDK-level 3s call timeout fired.
        asyncio.TimeoutError: the semaphore acquisition (queue wait) timed
            out — see Risk 1b in the plan.
        Both propagate to the caller rather than being swallowed here, so
        the caller can discriminate "timeout" from "other failure" and
        write ``source="promise_gate_timeout"`` instead of the generic
        ``"promise_gate_heuristic"`` fallthrough source.

    SDK pattern follows ``bridge.read_the_room`` verbatim:
    ``async with semaphore_slot(timeout=RTR_SDK_TIMEOUT): async with
    anthropic.AsyncAnthropic(timeout=RTR_SDK_TIMEOUT, max_retries=0) as
    client:``. See the module docstring for why ``max_retries=0`` is
    load-bearing for the stated 3-second worst case.
    Honors PR #1055 httpx-cleanup invariant. Coroutine-level timeouts
    around the API call are forbidden — they leak httpx connections under
    cancellation. The ``semaphore_slot`` timeout is NOT a coroutine-level
    timeout around the API call; it only bounds how long this call waits
    for a semaphore slot, so it does not reintroduce the #1055 hazard.
    """
    api_key = get_anthropic_api_key()
    if not api_key:
        return None

    acquire_start = time.monotonic()
    try:
        async with semaphore_slot(timeout=RTR_SDK_TIMEOUT):
            _queue_wait_ms.set((time.monotonic() - acquire_start) * 1000)
            async with anthropic.AsyncAnthropic(
                api_key=api_key,
                timeout=RTR_SDK_TIMEOUT,
                max_retries=0,
            ) as client:
                message = await client.messages.create(
                    model=MODEL_FAST,
                    max_tokens=300,
                    system=PROMISE_GATE_SYSTEM_PROMPT,
                    tools=[_PROMISE_VERDICT_TOOL],
                    tool_choice={"type": "tool", "name": "promise_verdict"},
                    messages=[{"role": "user", "content": text}],
                )
    except (TimeoutError, anthropic.APITimeoutError):
        # Timeout is its own discriminator — caller maps to
        # source="promise_gate_timeout". Re-raised, not swallowed: an
        # earlier version of this function caught the timeout and returned
        # None here, so the caller's timeout-discriminating except clause
        # was unreachable dead code — every fallthrough audited generically.
        raise
    except Exception as e:
        logger.warning(f"promise_gate LLM call failed: {e!r}")
        return None

    # Parse the tool_use block.
    content = getattr(message, "content", None) or []
    for block in content:
        if (
            getattr(block, "type", None) == "tool_use"
            and getattr(block, "name", None) == "promise_verdict"
        ):
            payload = getattr(block, "input", None) or {}
            action = payload.get("action")
            if action not in ("allow", "block"):
                return None
            reason = str(payload.get("reason") or "")
            class_ = payload.get("class_")
            if not isinstance(class_, str) or not class_:
                class_ = None
            return PromiseVerdict(
                action=action,
                reason=reason,
                class_=class_,
            )
    return None


async def _evaluate_promise_llm_or_heuristic(
    text: str,
) -> tuple[PromiseVerdict, str, float, float | None]:
    """Attempt the LLM verdict, falling through to the heuristic on failure.

    Shared by both the CLI-facing ``evaluate_promise_async`` and the
    drafter's main-path ``_evaluate_drafter_promise`` (Task 5) so the
    LLM-attempt / timeout-discrimination / heuristic-fallthrough logic is
    written once.

    Returns ``(verdict, source_suffix, elapsed_ms, queue_wait_ms)``.
    ``source_suffix`` is one of ``"llm"``, ``"heuristic"``, ``"timeout"`` —
    callers prefix their own audit-source namespace (``"promise_gate_"`` for
    the CLI path, ``"promise_gate_drafter_"`` for the drafter path).
    ``queue_wait_ms`` is ``None`` whenever the semaphore-acquire line was
    never reached (no API key configured, so ``_evaluate_promise_async``
    short-circuits before attempting the call) or the LLM call raised
    before setting it; otherwise it is the measured wait in milliseconds
    (``0.0`` or more, including on an immediate acquire).
    """
    start = time.monotonic()
    _queue_wait_ms.set(None)
    llm_verdict: PromiseVerdict | None = None
    timeout_hit = False
    try:
        llm_verdict = await _evaluate_promise_async(text)
    except (TimeoutError, anthropic.APITimeoutError):
        timeout_hit = True
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"promise_gate LLM path raised: {e!r}")
    elapsed_ms = (time.monotonic() - start) * 1000
    queue_wait_ms = _queue_wait_ms.get()

    if llm_verdict is not None:
        return llm_verdict, "llm", elapsed_ms, queue_wait_ms

    heuristic_verdict = _evaluate_promise_heuristic(text)
    suffix = "timeout" if timeout_hit else "heuristic"
    return heuristic_verdict, suffix, elapsed_ms, queue_wait_ms


# === Public async + sync API ===


async def evaluate_promise_async(
    text: str | None,
    *,
    transport: str,
    session_id: str | None = None,
    classifier_verdict: Any = None,
) -> PromiseVerdict:
    """Evaluate a draft for empty-promise / forward-deferral content (async core).

    Public async API — extracted so async callers (the drafter's main path,
    Task 5) can ``await`` it directly instead of going through
    ``evaluate_promise``'s ``_run_async_safely``/``asyncio.run`` wrapper,
    which raises inside an already-running event loop. ``evaluate_promise``
    (sync) is a thin wrapper over this function; its signature and behavior
    are frozen (Risk 5) — every step below is verbatim what it did before
    the extraction.

    Call ordering (cycle-3 C-CYCLE3-2 — observable from telemetry):

    1. **Empty/whitespace check** → ALLOW return, NO audit JSONL written
       (empty input is a no-op, not a gate event).
    2. **Kill-switch check** — when ``_gate_enabled()`` is False:
       (a) write audit JSONL with ``source="promise_gate_disabled"`` first,
       (b) attempt ``_emit_session_event_if_real(... promise_gate.disabled)``
       second, (c) return ALLOW third.
    3. **Classifier-verdict short-circuit** — when ``classifier_verdict``
       is provided (drafter path), derive verdict from it. Skip the LLM
       call. Write audit with ``source="promise_gate_drafter_delegation"``.
    4. **CLI Haiku path** — call ``_evaluate_promise_async`` via the shared
       ``_evaluate_promise_llm_or_heuristic`` helper. Write audit with
       ``source="promise_gate_llm"`` on success, ``"promise_gate_timeout"``
       on SDK/semaphore timeout, or ``"promise_gate_heuristic"`` on any
       other heuristic fallthrough. ``elapsed_ms`` and ``queue_wait_ms``
       are recorded on every Step-4 audit row.

    Args:
        text: The draft text to evaluate. ``None`` and whitespace-only
            inputs are treated as no-ops returning ALLOW.
        transport: One of ``"telegram"``, ``"email"``, ``"polymorphic"``,
            ``"drafter"``. Logged in the audit JSONL.
        session_id: Optional session_id for audit JSONL (logged literally)
            and session_events emission (best-effort lookup via
            ``AgentSession.query.get``; no-op on synthetic IDs). Never
            used for state-driven gate judgment.
        classifier_verdict: Optional pre-computed classification result.
            When provided, short-circuits the LLM call. Kept for backward
            compatibility; the drafter no longer delegates here.

    Returns:
        ``PromiseVerdict``. Two-state action: ``"allow"`` or ``"block"``.
    """
    # Step 1: empty-input check (no audit).
    if text is None or not str(text).strip():
        return PromiseVerdict(action="allow", reason="empty_input")

    # Step 2: kill-switch check.
    if not _gate_enabled():
        verdict = PromiseVerdict(action="allow", reason="gate_disabled")
        _write_promise_audit(
            text,
            verdict,
            transport=transport,
            session_id=session_id,
            source="promise_gate_disabled",
        )
        _emit_session_event_if_real(
            session_id,
            _make_event(
                "promise_gate.disabled",
                text=text,
                transport=transport,
                session_id=session_id,
                verdict=verdict,
                source="promise_gate_disabled",
            ),
        )
        return verdict

    # Step 3: classifier-verdict short-circuit (drafter path delegation).
    if classifier_verdict is not None:
        verdict = _derive_from_classifier_verdict(text, classifier_verdict)
        _write_promise_audit(
            text,
            verdict,
            transport=transport,
            session_id=session_id,
            source="promise_gate_drafter_delegation",
        )
        if verdict.action == "block":
            _emit_session_event_if_real(
                session_id,
                _make_event(
                    "promise_gate.blocked",
                    text=text,
                    transport=transport,
                    session_id=session_id,
                    verdict=verdict,
                    source="promise_gate_drafter_delegation",
                ),
            )
        return verdict

    # Step 4: CLI Haiku path with heuristic fallthrough.
    verdict, suffix, elapsed_ms, queue_wait_ms = await _evaluate_promise_llm_or_heuristic(text)
    source = f"promise_gate_{suffix}"
    _write_promise_audit(
        text,
        verdict,
        transport=transport,
        session_id=session_id,
        source=source,
        elapsed_ms=elapsed_ms,
        queue_wait_ms=queue_wait_ms,
    )
    if verdict.action == "block":
        _emit_session_event_if_real(
            session_id,
            _make_event(
                "promise_gate.blocked",
                text=text,
                transport=transport,
                session_id=session_id,
                verdict=verdict,
                source=source,
            ),
        )
    if suffix == "timeout":
        _emit_session_event_if_real(
            session_id,
            _make_event(
                "promise_gate.timeout",
                text=text,
                transport=transport,
                session_id=session_id,
                verdict=verdict,
                source=source,
            ),
        )
    return verdict


def evaluate_promise(
    text: str | None,
    *,
    transport: str,
    session_id: str | None = None,
    classifier_verdict: Any = None,
) -> PromiseVerdict:
    """Evaluate a draft for empty-promise / forward-deferral content.

    Public sync API — thin wrapper over ``evaluate_promise_async`` via
    ``_run_async_safely`` (``asyncio.run`` under the hood). Signature and
    behavior are frozen (Risk 5): every non-async CLI consumer
    (``tools/send_message.py``, ``tools/valor_telegram.py``,
    ``tools/valor_email.py``, ``agent/session_health.py``) is unaffected by
    the async extraction.

    See ``evaluate_promise_async`` for the full step-by-step contract.
    """
    return _run_async_safely(
        evaluate_promise_async(
            text,
            transport=transport,
            session_id=session_id,
            classifier_verdict=classifier_verdict,
        )
    )


def _run_async_safely(coro):
    """Run an async coroutine from a sync context without blowing up if a
    loop is already running.

    On a running event loop (e.g. test harness), raises a controlled
    ``RuntimeError`` that ``evaluate_promise`` treats as "LLM unavailable".
    """
    try:
        return asyncio.run(coro)
    except RuntimeError as e:
        # asyncio.run() refuses to run inside a running loop.
        if "running event loop" in str(e):
            # asyncio.run raises BEFORE it ever touches `coro`, so the
            # eagerly-created coroutine is neither awaited nor closed. Close it
            # deterministically here — otherwise it leaks and CPython emits
            # `coroutine '_evaluate_promise_async' was never awaited` at
            # GC/teardown, which wedges the full pytest suite (#2120, follow-up
            # to #2118). This branch is only reachable under a test harness /
            # async caller; production reaches _run_async_safely from a sync CLI
            # context with no running loop, so asyncio.run succeeds and the
            # coroutine is really awaited.
            coro.close()
            logger.warning("promise_gate: asyncio.run inside running loop, falling through")
            return None
        # Any other RuntimeError means asyncio.run started the coroutine and it
        # raised from inside; the coroutine is already finalized. Re-raise.
        raise


def _derive_from_classifier_verdict(
    text: str,
    classifier_verdict: Any,
) -> PromiseVerdict:
    """Derive a PromiseVerdict from a drafter ``ClassificationResult``.

    Drafter path delegation (Concern C5): the drafter has already paid
    one Haiku call. Reuse its verdict instead of paying a second.

    BLOCK when ``output_type == STATUS_UPDATE`` AND ``nudge_feedback``
    contains a forward-deferral signal (or the strengthened few-shot
    block already classified the input as STATUS_UPDATE for empty-promise
    reasons). ALLOW otherwise.
    """
    try:
        output_type = getattr(classifier_verdict, "output_type", None)
        nudge_feedback = getattr(classifier_verdict, "nudge_feedback", None) or ""
        # Drafter classifies empty promises as STATUS_UPDATE with
        # nudge_feedback. We treat any STATUS_UPDATE with nudge_feedback
        # as a BLOCK signal — that is the drafter's existing contract.
        # Compare by string for robustness across enum import paths.
        is_status = (
            getattr(output_type, "value", None) == "status"
            or str(output_type).lower().endswith("status_update")
            or str(output_type).lower() == "outputtype.status_update"
        )
        if is_status and nudge_feedback.strip():
            return PromiseVerdict(
                action="block",
                reason=f"Drafter classified as status_update with nudge: {nudge_feedback[:100]}",
                class_="drafter_delegation",
            )
        return PromiseVerdict(
            action="allow",
            reason="Drafter classifier returned non-blocking verdict",
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"promise_gate: classifier_verdict shape unexpected ({e!r}); allowing")
        return PromiseVerdict(action="allow", reason="classifier_verdict_unparseable")


# === Recovery template (CLI BLOCK stderr output) ===
# The template MUST NOT mention any bypass mechanism (operator-mode env
# var, per-call CLI flag, kill-switch env var). The agent reads its
# own stderr to recover and would learn the bypass on the very first
# BLOCK (cycle-2 Blocker B-NEW-2 retired the cycle-1 design for exactly
# this reason). Anti-leak is enforced by tests in
# ``tests/unit/test_promise_gate.py::TestRecoveryTemplate``.

_RECOVERY_TEMPLATE = """\
Empty forward-deferral promise blocked by bridge/promise_gate.
The phrase '{phrase}' was rejected.

Your session is ending. Do not promise future work. Choose one of:
  (a) Deliver findings now: 'I did X with evidence Y'
  (b) State explicitly that you didn't: 'I didn't do X because Y'
  (c) If the work legitimately cannot finish this turn, schedule a real
      check-in and cite it:
        python -m tools.agent_session_scheduler checkin \\
          --prompt "<what to do when it fires>" --in 30m
      then include the returned 'schedule_id=<hex>' in your message.

See docs/features/promise-gate.md and docs/features/checkin-primitive.md.
"""


def _format_recovery_template(text: str, verdict: PromiseVerdict) -> str:
    """Render the BLOCK recovery template for stderr.

    Intentionally does NOT name any bypass syntax. The agent's loop reads
    this template to recover; teaching the bypass syntax would defeat the
    gate on the first BLOCK (cycle-2 B-NEW-2).
    """
    # Pull the offending phrase from the text (best-effort: use the first
    # forward-deferral pattern that matches; otherwise fall back to a
    # short text preview).
    text_lower = (text or "").lower()
    phrase = None
    for p in _FORWARD_DEFERRAL_PATTERNS + _BEHAVIORAL_CHANGE_PATTERNS:
        m = re.search(p, text_lower)
        if m:
            phrase = m.group(0)
            break
    if not phrase:
        phrase = (text or "").strip()[:80]
    return _RECOVERY_TEMPLATE.format(phrase=phrase)


# === CLI helper ===


def cli_check_or_exit(
    text: str | None,
    transport: str,
    session_id: str | None,
) -> None:
    """Run the gate from a CLI tool and exit non-zero on BLOCK.

    There is **NO** ``no_gate`` parameter and **NO** ``--no-promise-gate``
    flag (cycle-2 B-NEW-2 — bypass retired). The only escape hatch is
    the process-wide kill switch ``PROMISE_GATE_ENABLED=false``, set in
    the env file or shell startup.

    Exception-swallow semantics (cycle-3 C-CYCLE3-3): wraps the
    ``evaluate_promise`` call in ``try/except Exception``. On unexpected
    exception (asyncio nested-loop, ImportError from a circular import,
    AttributeError from a Popoto schema migration), logs a warning,
    writes a fail-open audit JSONL entry with
    ``source="promise_gate_cli_exception"``, and **returns silently**
    (does NOT block delivery on infrastructure failure). Heuristic-
    branch fail-closed posture (cycle-2 C-NEW-3) covers judgment-
    fallback failures; this guard covers infrastructure failures —
    the two postures are coherent.

    Args:
        text: The draft to evaluate.
        transport: One of ``"telegram"``, ``"email"``, ``"polymorphic"``.
        session_id: Optional session_id for audit + session_events.
    """
    try:
        verdict = evaluate_promise(text, transport=transport, session_id=session_id)
    except Exception as e:
        logger.warning(f"promise_gate.cli_check_or_exit unexpected error: {e!r}; allowing send")
        # Best-effort fail-open audit (also try/except-wrapped to avoid
        # recursive failure).
        try:
            _write_promise_audit(
                text or "",
                PromiseVerdict(action="allow", reason=f"cli_exception: {e!r}"),
                transport=transport,
                session_id=session_id,
                source="promise_gate_cli_exception",
            )
        except Exception:  # noqa: S110 -- fail-open audit; no recursive failure
            pass
        return

    if verdict.action == "block":
        sys.stderr.write(_format_recovery_template(text or "", verdict))
        sys.exit(1)
    return


# === Public re-exports ===

__all__ = [
    "PromiseVerdict",
    "PROMISE_GATE_SYSTEM_PROMPT",
    "evaluate_promise",
    "evaluate_promise_async",
    "cli_check_or_exit",
    "_detect_empty_promise",  # bool wrapper over the heuristic; consumed by tests only
]
