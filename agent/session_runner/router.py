"""PM-prefix routing and exit classification for the headless session runner.

``classify_pm_prefix`` is a deterministic regex parse of the first line of the
PM's turn text — the ``[/user]`` / ``[/complete]`` convention the PM persona
is primed to follow. It is stateless and never an LLM call.

The input is the PM's turn text as parsed from the stream-json event stream
(or the flush-safe JSONL transcript named by the ``Stop`` hook payload).
Stream-json carries no ANSI escapes, so — unlike the retired PTY classifier —
there is no escape-stripping step here.

The ``[/dev]`` token is retired: the PM spawns and continues its ``dev``
subagent *inside* its own turn via the harness's agent mechanism (plan
#1924, D1-amended), so there is no external Dev relay to route to. A legacy
``[/dev]`` emission is still recognized defensively (the runner treats it as
"continue", never as a delivery), and the ``[/dev:pi]``-style harness suffix
is gone entirely — Dev runs on the claude harness only.

This module also owns the exit-classification tables: which
``exit_reason`` values are clean, which trigger the wrap-up guard, and which
are anomalies. Historical vocabulary (``pm_complete`` / ``pm_user`` /
``dev_hang`` ...) is preserved for telemetry continuity; only the PTY-only
producers (``startup_unresolved``, plateau) have no place here. ``pm_user``
is a real ``[/user]`` answer the PM chose to deliver; ``pm_needs_human`` is
a runner-forwarded needs-input prompt (a ``needs_human`` hook edge firing on
an otherwise-unroutable turn) — both are clean exits but distinguishable
downstream.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

# ---------------------------------------------------------------------------
# Prefix-token classification (deterministic regex parse)
# ---------------------------------------------------------------------------

# The prefix-token convention. The PM persona body primes the PM to begin
# every output with one of these literal tokens on a line of its own. The
# strict regex requires the token to be the entire content of its line (no
# trailing text, allowed trailing whitespace). It is matched against the
# **first non-empty line** of the PM's turn text using re.match. No harness
# suffix is recognized — Dev runs on the claude harness only.
PREFIX_TOKEN_RE = re.compile(r"^\[/(dev|user|complete)\]\s*$")
PREFIX_TOKEN_FALLBACK_RE = re.compile(r"\[/(dev|user|complete)\]")

# Destination: where the routed output goes. "dev" is legacy-defensive only
# (the runner continues the loop on it); "unknown" is a compliance miss.
Destination = Literal["dev", "user", "complete", "unknown"]


@dataclass
class ClassificationResult:
    """The classifier's routing decision for a PM turn.

    ``destination`` is the routing target. ``payload`` is the verbatim text
    following the prefix token — for ``user``, the user-facing message; for
    ``complete``, the trailing one-sentence summary; for ``unknown``, an
    empty string (the runner surfaces a compliance miss).

    ``compliance_miss`` is True iff the PM turn text had no strict prefix
    token on its first line.
    """

    destination: Destination
    payload: str
    compliance_miss: bool
    raw_first_line: str


def classify_pm_prefix(pm_text: str) -> ClassificationResult:
    """Classify the PM's turn text by parsing the first line for a prefix token.

    The PM persona body primes the PM to begin every output with one of:
      ``[/user]`` — followed by the user-facing message
      ``[/complete]`` — followed by a one-sentence completion summary

    The first line is parsed with :data:`PREFIX_TOKEN_RE` (strict — token
    must be the only content on the line). If the strict regex doesn't
    match, a fallback regex (:data:`PREFIX_TOKEN_FALLBACK_RE`) is tried on
    the first 200 chars. If neither matches, the result is ``unknown`` and
    ``compliance_miss=True``.

    The classification is **stateless** — no call history, no persona
    context, no LLM call. It is a regex parse over protocol-sourced text
    (stream-json / transcript JSONL), which carries no ANSI escapes.
    """
    # Find the first non-empty line.
    first_line = ""
    for line in pm_text.splitlines():
        if line.strip():
            first_line = line
            break

    if not first_line:
        return ClassificationResult(
            destination="unknown",
            payload="",
            compliance_miss=True,
            raw_first_line="",
        )

    m = PREFIX_TOKEN_RE.match(first_line)
    if m:
        token = m.group(1)
        # The payload is the rest of the text (the lines after the prefix
        # token), stripped of leading/trailing whitespace.
        rest = pm_text[pm_text.index(first_line) + len(first_line) :].strip()
        return ClassificationResult(
            destination=token,  # type: ignore[arg-type]
            payload=rest,
            compliance_miss=False,
            raw_first_line=first_line,
        )

    # Strict match failed; try a more permissive fallback. The PM may have
    # included the token mid-line or with light surrounding text — that's a
    # compliance miss by the strict definition but a correct classification.
    # The matched token is STRIPPED from the payload: the payload is delivered
    # verbatim to the human, and no raw system string may ever reach the CEO.
    fallback = PREFIX_TOKEN_FALLBACK_RE.search(pm_text[:200])
    if fallback:
        before = pm_text[: fallback.start()].strip()
        after = pm_text[fallback.end() :].strip()
        payload = f"{before}\n{after}" if before and after else (before or after)
        return ClassificationResult(
            destination=fallback.group(1),  # type: ignore[arg-type]
            payload=payload,
            compliance_miss=True,
            raw_first_line=first_line,
        )

    return ClassificationResult(
        destination="unknown",
        payload="",
        compliance_miss=True,
        raw_first_line=first_line,
    )


# ---------------------------------------------------------------------------
# Exit classification
# ---------------------------------------------------------------------------


class ExitReason(StrEnum):
    """The exit-reason vocabulary, with classification declared per member.

    Each member is ``(value, is_clean, wrapup_eligible, is_anomaly)``. String
    VALUES are byte-identical to the pre-enum vocabulary — telemetry
    (``exit_summary`` session events, ``AgentSession.exit_reason``) depends on
    them. Members ARE ``str`` (StrEnum), so plain-string comparisons and
    frozenset membership at every existing import site keep working unchanged.

    Classification semantics:

    * ``is_clean`` — the run ended the way a healthy session ends. Everything
      else is non-clean and routes to the error reaction / persona-safe
      apology. (``steer_abort`` is an operator-requested abort with its own
      user-facing confirmation.)
    * ``wrapup_eligible`` — successful-shaped terminal states that must still
      be driven to produce a user-facing message when none was delivered
      (``user_facing_routed=False``). Distinct from clean — e.g.
      ``pm_max_turns`` is wrapup-eligible but not clean.
    * ``is_anomaly`` — operator-actionable failures that warrant an
      error-level log (Sentry capture) and a dashboard session_events entry.
      The PTY-only producers (``startup_unresolved``) do not exist headless —
      a turn either yields a stream-json ``result`` or the subprocess errored.

    A member may carry several flags (``pm_complete`` is clean AND
    wrapup-eligible) or none (turn-level slugs like ``empty_output`` are
    translated by the runner before summary classification, so their
    disposition is all-False by declaration). A new member without a
    deliberate classification fails the completeness test in
    ``tests/unit/session_runner/test_exit_reason.py``.
    """

    is_clean: bool
    wrapup_eligible: bool
    is_anomaly: bool

    def __new__(
        cls, value: str, is_clean: bool, wrapup_eligible: bool, is_anomaly: bool
    ) -> ExitReason:
        member = str.__new__(cls, value)
        member._value_ = value
        member.is_clean = is_clean
        member.wrapup_eligible = wrapup_eligible
        member.is_anomaly = is_anomaly
        return member

    # -- Adapter default (RunSummary before terminal classification) --------
    #                                  value                clean  wrapup anomaly
    IN_PROGRESS = ("in_progress", False, False, False)

    # -- Summary-level terminal reasons --------------------------------------
    PM_COMPLETE = ("pm_complete", True, True, False)
    PM_USER = ("pm_user", True, True, False)
    PM_NEEDS_HUMAN = ("pm_needs_human", True, True, False)
    PM_FLOOR_DELIVERED = ("pm_floor_delivered", True, True, False)
    STEER_ABORT = ("steer_abort", True, False, False)
    PM_MAX_TURNS = ("pm_max_turns", False, True, False)
    PM_EMPTY_TURN = ("pm_empty_turn", False, False, False)
    TURN_TIMEOUT = ("turn_timeout", False, False, False)
    PM_NO_USER_MESSAGE = ("pm_no_user_message", False, False, True)
    EXCEPTION = ("exception", False, False, True)
    ERROR = ("error", False, False, True)

    # -- Historical vocabulary preserved for telemetry continuity ------------
    PM_HANG = ("pm_hang", False, False, True)
    DEV_HANG = ("dev_hang", False, False, True)

    # -- Turn-level reasons minted by the role driver (TurnFailure.reason) ---
    EMPTY_OUTPUT = ("empty_output", False, False, False)
    HEADLESS_TURN_TIMEOUT = ("headless_turn_timeout", False, False, False)
    HEADLESS_THINKING_CORRUPTION = ("headless_thinking_corruption", False, False, False)
    HEADLESS_SUBPROCESS_ERROR = ("headless_subprocess_error", False, False, False)
    HEADLESS_BINARY_MISSING = ("headless_binary_missing", False, False, False)
    HEADLESS_NONZERO_EXIT_NO_RESULT = ("headless_nonzero_exit_no_result", False, False, False)


@dataclass(frozen=True)
class TurnFailure:
    """A failed role-driver turn: structured reason + free-form detail.

    Pre-enum, the driver smuggled exception detail into the reason string
    (``f"headless_subprocess_error: {e}"``); this separates them. ``str()``
    reproduces the legacy wire format byte-for-byte, so the runner's
    ``exit_message`` telemetry is unchanged.
    """

    reason: ExitReason
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.reason}: {self.detail}" if self.detail else str(self.reason)


# The classification frozensets, derived from the per-member declarations.
# Every pre-enum import site keeps working unchanged: a frozenset of StrEnum
# members compares equal to (and contains) the raw strings.
CLEAN_EXIT_REASONS = frozenset(r for r in ExitReason if r.is_clean)
WRAPUP_ELIGIBLE_EXIT_REASONS = frozenset(r for r in ExitReason if r.wrapup_eligible)
ANOMALY_EXIT_REASONS = frozenset(r for r in ExitReason if r.is_anomaly)

# Cap on the size of a terminal ``exit_message``. A multi-KB traceback can
# land on the exception branch, and the message is published toward the
# Telegram relay — keep it bounded so a single failure doesn't flood a chat.
# 500 chars matches the relay's downstream clamp.
EXIT_MESSAGE_MAX_CHARS = 500


def truncate_exit_message(text: str) -> str:
    """Bound an exit message to :data:`EXIT_MESSAGE_MAX_CHARS`.

    A short ellipsis marker preserves the "we truncated" signal in the
    published message.
    """
    if len(text) <= EXIT_MESSAGE_MAX_CHARS:
        return text
    return text[: EXIT_MESSAGE_MAX_CHARS - 3] + "..."
