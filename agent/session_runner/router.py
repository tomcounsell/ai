"""PM-turn routing and exit classification for the headless session runner.

Routing is **schema-first** (plan #2000 Task 2.3): the PM's terminal turn
carries a ``--json-schema``-validated ``structured_output`` object
(``{route, message, file_paths?}``) that :func:`validate_structured_route`
turns directly into a :class:`ClassificationResult` — no text parsing.
``classify_pm_prefix`` — a deterministic regex parse of the first line of
the PM's turn text, the pre-schema ``[/user]`` / ``[/complete]`` convention
— is demoted to a **fallback only**, used exactly when
``structured_output`` is absent or fails :func:`validate_structured_route`
(the CLI's own schema-validation failure signal — see
``docs/plans/harness-adapter-seam.md`` "Task 2.1 empirical results"). The
compliance nudge (``agent/session_runner/runner.py``) remains the final
backstop when even the regex fallback misses. Both classifiers are
stateless — no call history, no persona context, no LLM call.

The regex-fallback input is the PM's turn text as parsed from the
stream-json event stream (or the flush-safe JSONL transcript named by the
``Stop`` hook payload). Stream-json carries no ANSI escapes, so — unlike
the retired PTY classifier — there is no escape-stripping step here.

The ``[/dev]`` token is retired: the PM spawns and continues its ``dev``
subagent *inside* its own turn via the harness's agent mechanism (plan
#1924, D1-amended), so there is no external Dev relay to route to. A legacy
``[/dev]`` emission is still recognized defensively by the regex fallback
(the runner treats it as "continue", never as a delivery), and the
``[/dev:pi]``-style harness suffix is gone entirely — Dev runs on the
claude harness only. The schema's ``route`` enum has no ``"dev"`` member —
a schema-validated turn can only ever route ``user``/``complete``/``continue``.

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
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Schema-first classification (plan #2000 Task 2.3)
# ---------------------------------------------------------------------------

# The PM turn schema, passed to the claude harness via ``--json-schema``
# (``agent/session_runner/harness/claude.py``). The CLI validates the
# model's ``StructuredOutput`` tool call against this schema out-of-band and
# — on success only — attaches the parsed object as ``structured_output`` on
# the terminal stream-json ``result`` event (Task 2.1 empirical finding).
# ``route`` has no ``"dev"`` member: the dev subagent is spawned inline via
# the Agent tool, never routed to externally (see module docstring).
# ``file_paths`` is optional — closes #1802 (PM file-capable send path).
PM_TURN_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": ["user", "complete", "continue"]},
        "message": {"type": "string"},
        "file_paths": {"type": "array", "items": {"type": "string"}},
        # ``blocked_reason`` (issue #2158) is the structured escape hatch that
        # lets a PM finalize a NON-terminal SDLC pipeline as ``complete`` when
        # the work is genuinely blocked / abandoned / superseded — without the
        # runner's ledger-aware completion guard refusing and re-routing it.
        # Optional and additive: turns that omit it behave exactly as before.
        # The runner treats a whitespace-only value as absent (no keyword
        # matching of the free-text ``message``).
        "blocked_reason": {"type": "string"},
    },
    "required": ["route", "message"],
}

# Analytics metric names for the schema-routing fallback-rate alert (plan
# #2000 Task 2.3 "Schema routing" — a healthy schema path is ~0%; a
# sustained breach means the schema contract has silently regressed).
# Recorded via ``analytics.collector.record_metric`` by the runner on every
# classified top-level turn; ``monitoring/schema_routing_alert.py`` queries
# them for the rolling-window threshold check.
SCHEMA_ROUTING_TURN_METRIC = "session_runner.pm_turn_routed"
SCHEMA_ROUTING_FALLBACK_METRIC = "session_runner.schema_routing_fallback"

# The session-telemetry event type emitted whenever routing falls back from
# the schema to the prefix-regex classifier (observable, never silent).
SCHEMA_ROUTING_FALLBACK_EVENT = "schema_routing_fallback"

# ---------------------------------------------------------------------------
# Prefix-token classification (deterministic regex parse — fallback only)
# ---------------------------------------------------------------------------

# The prefix-token convention. Demoted to a fallback (Task 2.3): only
# consulted when the schema-validated ``structured_output`` is absent or
# invalid. The strict regex requires the token to be the entire content of
# its line (no trailing text, allowed trailing whitespace). It is matched
# against the **first non-empty line** of the PM's turn text using re.match.
# No harness suffix is recognized — Dev runs on the claude harness only.
PREFIX_TOKEN_RE = re.compile(r"^\[/(dev|user|complete)\]\s*$")
PREFIX_TOKEN_FALLBACK_RE = re.compile(r"\[/(dev|user|complete)\]")

# Destination: where the routed output goes. "dev" is legacy-defensive only
# (the runner continues the loop on it) and reachable only via the
# prefix-regex fallback — the schema's ``route`` enum has no "dev" member.
# "continue" is a deliberate schema decision (not a compliance miss);
# "unknown" is a compliance miss (neither schema nor regex classified it).
Destination = Literal["dev", "user", "complete", "continue", "unknown"]


@dataclass
class ClassificationResult:
    """The classifier's routing decision for a PM turn.

    ``destination`` is the routing target. ``payload`` is the user-facing
    text — for ``user``, the user-facing message; for ``complete``, the
    trailing one-sentence summary; for ``unknown``, an empty string (the
    runner surfaces a compliance miss). ``file_paths`` is the schema's
    optional attachment list (always ``[]`` on a regex-fallback result — the
    prefix-token convention carries no file slot).

    ``compliance_miss`` is True iff neither the schema nor (on fallback) the
    strict prefix regex classified the turn.
    """

    destination: Destination
    payload: str
    compliance_miss: bool
    raw_first_line: str
    file_paths: list[str] = field(default_factory=list)
    # ``blocked_reason`` (issue #2158): the schema's optional structured
    # escape hatch, present only on a schema-validated ``complete`` route that
    # supplied it. Always ``None`` on a regex-fallback result — the prefix-token
    # convention carries no such slot, so the runner must source it defensively
    # (``getattr(classification, "blocked_reason", None)``).
    blocked_reason: str | None = None


def validate_structured_route(structured: dict[str, Any] | None) -> ClassificationResult | None:
    """Build a :class:`ClassificationResult` from schema-validated PM output.

    ``structured`` is :attr:`~agent.session_runner.harness.base.TurnResult.
    structured_output` — present only when the claude CLI's own
    ``--json-schema`` validation succeeded (Task 2.1 empirical finding: an
    invalid/refused structured output surfaces as the key being ABSENT from
    the terminal ``result`` event, not a malformed value). This function
    still re-validates defensively (shape drift, a stubbed/faked
    ``structured_output`` in tests) rather than trusting presence alone.

    Returns ``None`` — never raises — when ``structured`` is absent or does
    not match the expected shape; the caller falls back to
    :func:`classify_pm_prefix` and should emit
    :data:`SCHEMA_ROUTING_FALLBACK_EVENT` telemetry in that case.
    """
    if not isinstance(structured, dict):
        return None
    route = structured.get("route")
    if route not in ("user", "complete", "continue"):
        return None
    message = structured.get("message")
    if not isinstance(message, str):
        return None
    raw_file_paths = structured.get("file_paths")
    if isinstance(raw_file_paths, list) and all(isinstance(p, str) for p in raw_file_paths):
        file_paths = list(raw_file_paths)
    else:
        file_paths = []
    payload = message.strip()
    first_line = next((line for line in message.splitlines() if line.strip()), "")
    raw_blocked = structured.get("blocked_reason")
    blocked_reason = raw_blocked if isinstance(raw_blocked, str) and raw_blocked.strip() else None
    return ClassificationResult(
        destination=route,  # type: ignore[arg-type]
        payload=payload,
        compliance_miss=False,
        raw_first_line=first_line,
        file_paths=file_paths,
        blocked_reason=blocked_reason,
    )


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
