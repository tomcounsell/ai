"""Redundancy filter for the Telegram message drafter.

This module provides a deterministic, zero-cost pre-send guard that suppresses
near-verbatim repeated status messages from PM sessions.

## Suppression contract

``should_suppress()`` inspects the about-to-be-sent ``draft_text`` against the
session's ``recent_sent_drafts`` history and returns one of two verdicts:

- ``"send"``     — deliver the draft as normal.
- ``"suppress"`` — skip the text outbox write; queue a 👀 reaction instead.

## Deterministic vs LLM tradeoff

Read-the-Room (PR #1204) uses a Haiku call for nuanced suppression in personal
chats. This filter is intentionally lower cost: bigram Jaccard similarity
catches the near-verbatim PM status repeats that triggered issue #1205 without
any LLM call. The two layers compose — this filter runs first for SDLC
sessions; RTR (opt-in) runs after for non-SDLC sessions.

## SDLC scoping

This module contains only pure functions. The SDLC scoping decision
(``session.is_sdlc``) is enforced at the call site in
``agent/output_handler.py::TelegramRelayOutputHandler.send``. This module
never reads ``session`` directly — callers pass the pre-extracted fields
(recent_sent_drafts, expectations, session_status) to keep this module
side-effect-free and trivially testable.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

# ── Module-level constants (tuneable via env vars) ──────────────────────────
REDUNDANCY_THRESHOLD: float = float(os.environ.get("DRAFTER_REDUNDANCY_THRESHOLD", "0.65"))
RECENT_DRAFTS_N: int = int(os.environ.get("DRAFTER_RECENT_DRAFTS_N", "3"))
REDUNDANCY_WINDOW_SECONDS: int = int(os.environ.get("DRAFTER_REDUNDANCY_WINDOW_SECONDS", "600"))
SUPPRESSION_ENABLED: bool = os.environ.get(
    "DRAFTER_REDUNDANCY_SUPPRESSION_ENABLED", "true"
).lower() in ("1", "true", "yes", "on")

# Re-export the emoji constant so callers have a single import point.
# Defined here as a module-level constant; the original lives in
# bridge.read_the_room.RTR_SUPPRESS_EMOJI — both are "👀".
RTR_SUPPRESS_EMOJI: str = "👀"

# Terminal session statuses that force a ``send`` verdict.
_TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "blocked"})


@dataclass
class SuppressionVerdict:
    """Result of a ``should_suppress()`` call.

    Attributes:
        action:        ``"send"`` or ``"suppress"``.
        reason:        Human-readable explanation for the verdict.
        jaccard:       Bigram Jaccard score of the best-matching prior draft,
                       or ``None`` when the check was short-circuited.
        matched_index: Index in ``recent_sent_drafts`` of the matching entry,
                       or ``None`` when no match was found.
    """

    action: str  # "send" | "suppress"
    reason: str
    jaccard: float | None = field(default=None)
    matched_index: int | None = field(default=None)


def should_suppress(
    draft_text: str,
    draft_artifacts: dict | None,
    recent_sent_drafts: list[dict] | None,
    expectations: list | None,
    session_status: str | None,
) -> SuppressionVerdict:
    """Decide whether to suppress a Telegram draft that is about to be sent.

    All five termination conditions force a ``send`` verdict, evaluated in
    order:

    1. ``draft_text`` is empty or whitespace-only → ``send`` (never suppress
       empty text — caller may still queue a fallback).
    2. ``recent_sent_drafts`` is ``None`` or empty → ``send`` (no baseline to
       compare against).
    3. ``expectations`` is non-empty → ``send`` (drafter detected a question for
       the human; the send is intentional).
    4. ``session_status`` is terminal (``"completed"``, ``"failed"``,
       ``"blocked"``) → ``send`` (final message must always deliver).
    5. Draft contains a new artifact not present in any prior
       ``recent_sent_drafts`` entry that is within ``REDUNDANCY_WINDOW_SECONDS``
       → ``send`` (real progress: new PR URL, commit hash, error string, etc.).

    After the termination conditions, bigram Jaccard similarity is computed.
    If any prior draft within the time window has J >= ``REDUNDANCY_THRESHOLD``
    **and** no new artifact relative to that prior draft, the verdict is
    ``suppress``. Otherwise ``send``.

    Args:
        draft_text:          The final user-visible text about to be sent.
        draft_artifacts:     Artifacts extracted from the draft (from
                             ``bridge.message_drafter.extract_artifacts``).
                             Dict with optional keys ``commits``, ``urls``,
                             ``files_changed``, ``test_results``, ``errors``.
                             ``None`` is treated as ``{}``.
        recent_sent_drafts:  Session-scoped history of previously sent drafts,
                             each a dict with ``{ts, text, artifacts}``.
        expectations:        ``MessageDraft.expectations`` from the drafter.
                             Non-empty means the agent has a question for the
                             human — always ``send``.
        session_status:      Current ``AgentSession.status`` string.

    Returns:
        A :class:`SuppressionVerdict` with ``action`` ``"send"`` or
        ``"suppress"``.  On any unhandled exception inside this function the
        verdict is always ``SuppressionVerdict("send", reason="filter_error")``
        so delivery is never blocked by filter bugs.
    """
    try:
        return _should_suppress_inner(
            draft_text,
            draft_artifacts,
            recent_sent_drafts,
            expectations,
            session_status,
        )
    except Exception:
        return SuppressionVerdict(action="send", reason="filter_error")


# ── Internal implementation ─────────────────────────────────────────────────


def _should_suppress_inner(
    draft_text: str,
    draft_artifacts: dict | None,
    recent_sent_drafts: list[dict] | None,
    expectations: list | None,
    session_status: str | None,
) -> SuppressionVerdict:
    """Core suppression logic — called by ``should_suppress`` inside try/except."""
    from agent.memory_extraction import _extract_bigrams  # single canonical import

    # ── Termination condition 1: empty / whitespace-only draft ──────────────
    if not draft_text or not draft_text.strip():
        return SuppressionVerdict(action="send", reason="empty_draft")

    # ── Termination condition 2: no baseline ────────────────────────────────
    if not recent_sent_drafts:
        return SuppressionVerdict(action="send", reason="no_baseline")

    # ── Termination condition 3: drafter detected a question for the human ──
    if expectations:
        return SuppressionVerdict(action="send", reason="has_expectations")

    # ── Termination condition 4: session is in a terminal status ────────────
    if session_status in _TERMINAL_STATUSES:
        return SuppressionVerdict(action="send", reason="terminal_status")

    # ── Build the new artifact set for termination condition 5 and later ────
    new_artifacts_dict: dict = draft_artifacts or {}
    new_artifact_values: set[str] = set()
    for values in new_artifacts_dict.values():
        if isinstance(values, (list, tuple)):
            new_artifact_values.update(str(v) for v in values)
        elif values:
            new_artifact_values.add(str(values))

    # ── Compute bigrams for the new draft once ───────────────────────────────
    new_bigrams = _extract_bigrams(draft_text)

    now = time.time()
    best_jaccard: float = 0.0
    best_index: int | None = None

    for idx, prior in enumerate(recent_sent_drafts):
        prior_ts = prior.get("ts") or 0.0
        # Skip stale entries — they are outside the comparison window.
        if (now - prior_ts) > REDUNDANCY_WINDOW_SECONDS:
            continue

        prior_text = prior.get("text") or ""

        # ── Termination condition 5: new artifact not in this prior entry ────
        prior_artifacts_dict: dict = prior.get("artifacts") or {}
        prior_artifact_values: set[str] = set()
        for values in prior_artifacts_dict.values():
            if isinstance(values, (list, tuple)):
                prior_artifact_values.update(str(v) for v in values)
            elif values:
                prior_artifact_values.add(str(values))

        if new_artifact_values and not new_artifact_values.issubset(prior_artifact_values):
            # There is at least one new artifact relative to this prior draft.
            # Even if Jaccard would otherwise suppress, the new artifact is
            # evidence of real progress — force send.
            return SuppressionVerdict(action="send", reason="new_artifact")

        # ── Bigram Jaccard similarity ────────────────────────────────────────
        prior_bigrams = _extract_bigrams(prior_text)
        union = new_bigrams | prior_bigrams
        if not union:
            # Both drafts are so short they produced no bigrams — skip.
            continue

        intersection = new_bigrams & prior_bigrams
        jaccard = len(intersection) / len(union)

        if jaccard > best_jaccard:
            best_jaccard = jaccard
            best_index = idx

    # If the best matching prior is above threshold, suppress.
    if best_index is not None and best_jaccard >= REDUNDANCY_THRESHOLD:
        return SuppressionVerdict(
            action="suppress",
            reason=f"jaccard={best_jaccard:.2f}>=threshold={REDUNDANCY_THRESHOLD}",
            jaccard=best_jaccard,
            matched_index=best_index,
        )

    return SuppressionVerdict(action="send", reason="below_threshold")
