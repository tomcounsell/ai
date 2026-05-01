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
   ``AgentSession.query.get(session_id)`` returns a real session
   (real ``VALOR_SESSION_ID`` from the worker harness). Synthetic
   ``cli-{epoch}`` IDs silently skip session_events emission.

The ``session_id`` provenance differs across the four CLI paths:
``send_telegram.py`` reads real ``VALOR_SESSION_ID``;
``valor_telegram.py`` and ``valor_email.py`` use synthetic IDs;
``send_message.py`` accepts whatever its caller passes.

Latency
-------
Budget: p50 < 500ms, p99 < 3s. SDK-level 3-second timeout via the
RTR-correct pattern: ``async with semaphore_slot(): async with
anthropic.AsyncAnthropic(timeout=RTR_SDK_TIMEOUT) as client:``.
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
    """Backward-compat shim for ``bridge.message_drafter._classify_with_heuristics``.

    The original ``_detect_empty_promise`` returned ``True`` if the text
    looked like a behavioral-change acknowledgment without evidence. The
    new heuristic also covers forward-deferrals. Returns ``True`` if the
    new heuristic returns BLOCK.
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
) -> None:
    """Append a JSONL entry to the classification audit log.

    Fork of ``bridge.message_drafter._write_classification_audit`` —
    writes to the SAME file (``logs/classification_audit.jsonl``) for
    unified observability, but with verdict-specific fields:
    ``{ts, kind: "promise_gate", text_preview, action, reason, class_,
    transport, session_id, source}``. The original
    ``_write_classification_audit`` is unchanged.
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
        with open(_AUDIT_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.debug(f"promise_gate audit log write failed (non-fatal): {e}")


def _emit_session_event_if_real(
    session_id: str | None,
    event: dict[str, Any],
) -> None:
    """Best-effort session_events emission, conditional on real AgentSession.

    Calls ``AgentSession.query.get(session_id)`` (Popoto ORM, never
    raw Redis per CLAUDE.md). On real-session hit, appends ``event`` to
    ``session.session_events`` and saves. On miss (synthetic
    ``cli-{epoch}`` ID, stale ID, lookup error), silently no-ops.

    This honors Concern C6 — the gate makes no AgentSession state-driven
    decision; the existence check on the explicit input is for
    telemetry routing only.
    """
    if not session_id:
        return
    try:
        from models.agent_session import AgentSession

        session = AgentSession.query.get(session_id)
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


async def _evaluate_promise_async(text: str) -> PromiseVerdict | None:
    """Run the Haiku call for the LLM-primary path.

    Returns the parsed verdict on success, or ``None`` on any failure
    (no API key, SDK exception, parse failure, timeout). The caller
    falls through to the heuristic on ``None``.

    SDK pattern follows ``bridge.read_the_room`` verbatim:
    ``async with semaphore_slot(): async with
    anthropic.AsyncAnthropic(timeout=RTR_SDK_TIMEOUT) as client:``.
    Honors PR #1055 httpx-cleanup invariant. Coroutine-level timeouts
    are forbidden — they leak httpx connections under cancellation.
    """
    api_key = get_anthropic_api_key()
    if not api_key:
        return None

    try:
        async with semaphore_slot():
            async with anthropic.AsyncAnthropic(
                api_key=api_key,
                timeout=RTR_SDK_TIMEOUT,
            ) as client:
                message = await client.messages.create(
                    model=MODEL_FAST,
                    max_tokens=300,
                    system=PROMISE_GATE_SYSTEM_PROMPT,
                    tools=[_PROMISE_VERDICT_TOOL],
                    tool_choice={"type": "tool", "name": "promise_verdict"},
                    messages=[{"role": "user", "content": text}],
                )

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
    except anthropic.APITimeoutError:
        # Timeout is its own discriminator — caller maps to source="promise_gate_timeout".
        return None
    except Exception as e:
        logger.warning(f"promise_gate LLM call failed: {e!r}")
        return None


# === Public sync API ===


def evaluate_promise(
    text: str | None,
    *,
    transport: str,
    session_id: str | None = None,
    classifier_verdict: Any = None,
) -> PromiseVerdict:
    """Evaluate a draft for empty-promise / forward-deferral content.

    Public sync API. Internally runs ``asyncio.run(_evaluate_promise_async)``
    when the LLM path is taken.

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
    4. **CLI Haiku path** — call ``_evaluate_promise_async``. Write audit
       with ``source="promise_gate_llm"`` on success,
       ``"promise_gate_timeout"`` on SDK timeout, or
       ``"promise_gate_heuristic"`` on heuristic fallthrough.

    Args:
        text: The draft text to evaluate. ``None`` and whitespace-only
            inputs are treated as no-ops returning ALLOW.
        transport: One of ``"telegram"``, ``"email"``, ``"polymorphic"``,
            ``"drafter"``. Logged in the audit JSONL.
        session_id: Optional session_id for audit JSONL (logged literally)
            and session_events emission (best-effort lookup via
            ``AgentSession.query.get``; no-op on synthetic IDs). Never
            used for state-driven gate judgment.
        classifier_verdict: Optional ``ClassificationResult`` from
            ``bridge.message_drafter.classify_output``. When provided,
            short-circuits the LLM call (drafter path delegation).

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
    llm_verdict: PromiseVerdict | None = None
    timeout_hit = False
    try:
        # Detect timeout vs other failures: rerun with explicit detection.
        llm_verdict = _run_async_safely(_evaluate_promise_async(text))
    except _PromiseTimeoutError:
        timeout_hit = True
        llm_verdict = None
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"promise_gate.evaluate_promise LLM path raised: {e!r}")
        llm_verdict = None

    if llm_verdict is not None:
        # LLM-wins precedence (Blocker B3): heuristic only fires when
        # llm_verdict is None.
        _write_promise_audit(
            text,
            llm_verdict,
            transport=transport,
            session_id=session_id,
            source="promise_gate_llm",
        )
        if llm_verdict.action == "block":
            _emit_session_event_if_real(
                session_id,
                _make_event(
                    "promise_gate.blocked",
                    text=text,
                    transport=transport,
                    session_id=session_id,
                    verdict=llm_verdict,
                    source="promise_gate_llm",
                ),
            )
        return llm_verdict

    # Heuristic fallthrough.
    heuristic_verdict = _evaluate_promise_heuristic(text)
    source = "promise_gate_timeout" if timeout_hit else "promise_gate_heuristic"
    _write_promise_audit(
        text,
        heuristic_verdict,
        transport=transport,
        session_id=session_id,
        source=source,
    )
    if heuristic_verdict.action == "block":
        _emit_session_event_if_real(
            session_id,
            _make_event(
                "promise_gate.blocked",
                text=text,
                transport=transport,
                session_id=session_id,
                verdict=heuristic_verdict,
                source=source,
            ),
        )
    if timeout_hit:
        _emit_session_event_if_real(
            session_id,
            _make_event(
                "promise_gate.timeout",
                text=text,
                transport=transport,
                session_id=session_id,
                verdict=heuristic_verdict,
                source=source,
            ),
        )
    return heuristic_verdict


class _PromiseTimeoutError(Exception):
    """Internal signal that the LLM SDK timed out (not a generic exception)."""


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
            logger.warning("promise_gate: asyncio.run inside running loop, falling through")
            return None
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

See docs/features/promise-gate.md for the full contract.
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
        except Exception:
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
    "cli_check_or_exit",
    "_detect_empty_promise",  # backward-compat for message_drafter
]
