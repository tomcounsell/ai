"""Ledger-aware completion guard for the headless session runner (issue #2158).

The PM's terminal turn may carry ``route: "complete"``. Historically the
runner honored that verbatim — with no consultation of the per-issue SDLC
ledger — so the PM repeatedly finalized a run as ``completed`` mid-pipeline
(after PLAN/CRITIQUE, with BUILD/TEST/REVIEW/DOCS/MERGE still pending and no
merged PR). See issue #2158 for the five documented instances.

This module is the **pure decision function** that gates that route. It never
performs I/O: the runner (``agent/session_runner/runner.py``) resolves the
ledger + PR-open state and passes them in, so this function stays trivially
unit-testable with hand-built dicts.

Decision table (evaluated in order) — see :func:`evaluate_completion`:

1. Not an SDLC pipeline session (``session_type != "eng"`` or no
   ``issue_number``) → **ALLOW** (``not_sdlc``). Teammate and ad-hoc
   conversational sessions are unaffected.
2. Ledger query failed (an exception in the runner's ``_load_ledger``) →
   **ALLOW** (``query_failed_fail_open``). A broken query must NEVER trap a
   session in the refusal ladder.
3. Empty-but-successful ledger (``stage_states`` is falsy — ``query_enriched``
   returns ``{"stages": {}}`` for a not-found / not-yet-started pipeline) →
   **ALLOW** (``ledger_empty``). This is a normal empty return, not an
   exception, so it must be fail-opened explicitly (critique concern #1).
4. Terminal ledger (``is_pipeline_complete`` returns ``True`` — ``MERGE``
   completed, or the docs-only terminal path with a closed PR) → **ALLOW**
   (``terminal``).
5. Docs-only terminal candidate whose PR-open state could not be resolved
   (``is_pipeline_complete`` returns ``pr_state_unavailable`` because the
   caller's ``pr_open`` is ``None`` — a ``gh`` error / timeout) → **ALLOW**
   (``terminal_pr_state_unavailable_fail_open``). A ``gh`` hiccup must never
   trap a genuinely-done docs-only session (critique BLOCKER).
6. An explicit, non-empty ``blocked_reason`` was supplied via the PM turn
   schema → **ALLOW** (``blocked_reason_given``). Structured escape hatch for a
   genuinely abandoned / superseded pipeline — no keyword matching of prose
   (CLAUDE.md principle 3). This is the structured path acceptance criterion
   (b) names explicitly; it lets the PM terminate a non-terminal pipeline
   WITHOUT the noise of a spurious human escalation, which the refusal ladder
   would otherwise force.
7. Non-terminal, no blocked reason, refusals exhausted
   (``refusal_count >= MAX_COMPLETION_REFUSALS``) → **REFUSE + escalate**
   (``escalate_exhausted``): the runner surfaces the incomplete state to the
   human. Never silently completes, never spins to ``max_turns``.
8. Otherwise → **REFUSE** (``refused_non_terminal``) with a corrective
   ``reroute_message`` that names the router's next dispatch (``next_skill``,
   e.g. ``/do-plan`` for a NEEDS REVISION verdict — binding acceptance
   criterion (a) to the reroute path).
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.pipeline_complete import is_pipeline_complete

# Bounded refusal ladder, mirroring ``MAX_SAME_STAGE_DISPATCHES`` in
# ``agent/sdlc_router.py``. After this many refusals with no ledger advance,
# the guard escalates to a human rather than looping forever.
MAX_COMPLETION_REFUSALS = 3


@dataclass(frozen=True)
class CompletionDecision:
    """The completion guard's verdict for a PM ``complete`` route.

    ``allow`` — honor the ``complete`` route (finalize the session).
    ``reason`` — machine-readable classification (see the module decision
        table). Stable string so callers can telemetry-classify without
        parsing free text.
    ``reroute_message`` — corrective nudge fed back to the PM as a ``continue``
        turn, populated ONLY when ``allow is False`` and
        ``escalate_to_user is False``.
    ``escalate_to_user`` — surface the incomplete state to the human (the
        refusal ladder is exhausted). Mutually exclusive with a non-None
        ``reroute_message``.
    """

    allow: bool
    reason: str
    reroute_message: str | None = None
    escalate_to_user: bool = False


def _reroute_message(issue_number: int | None, next_skill: str | None) -> str:
    """Build the corrective nudge naming the router's next dispatch."""
    target = next_skill or "the next pipeline stage (run `sdlc-tool next-skill`)"
    issue_ref = f"#{issue_number}" if issue_number else "this issue"
    return (
        f"The SDLC pipeline for {issue_ref} is NOT terminal — MERGE has not been "
        f"recorded and no PR is merged, so `route: complete` is refused. Do not "
        f"complete mid-pipeline (bug #2158). Continue the pipeline by dispatching "
        f"{target} to your dev subagent. If the work is genuinely blocked or "
        f"abandoned, route `complete` again WITH a non-empty `blocked_reason` "
        f"explaining why."
    )


def evaluate_completion(
    *,
    session_type: str | None,
    issue_number: int | None,
    stage_states: dict,
    blocked_reason: str | None,
    refusal_count: int,
    next_skill: str | None,
    ledger_query_ok: bool = True,
    pr_open: bool | None = None,
) -> CompletionDecision:
    """Decide whether a PM ``complete`` route may be honored.

    Pure function — all ledger / PR-open I/O is performed by the caller
    (``runner._load_ledger``) and passed in. See the module docstring for the
    ordered decision table.

    Args:
        session_type: The session's discriminator (``"eng"`` / ``"teammate"``).
        issue_number: The tracking issue, or ``None`` for an unslugged / ad-hoc
            session.
        stage_states: The ``stages`` dict from ``query_enriched`` (stage name →
            status, e.g. ``{"BUILD": "ready", ...}``). Empty ``{}`` signals a
            not-found / not-yet-started ledger.
        blocked_reason: The optional structured ``blocked_reason`` from the PM
            turn schema. Whitespace-only is treated as absent.
        refusal_count: How many times this session has already been refused
            (persisted keyed by issue_number so the ladder does not restart on
            resume).
        next_skill: The router's next dispatch skill (from
            ``decide_next_dispatch``), quoted in the reroute nudge.
        ledger_query_ok: ``False`` when the runner's ledger query raised — fail
            open.
        pr_open: Resolved PR-open state for the docs-only terminal branch
            (``True`` open, ``False`` closed/none, ``None`` unresolved). Ignored
            by ``is_pipeline_complete`` on the ``MERGE``-success fast path.

    Returns:
        A :class:`CompletionDecision`.
    """
    # 1. Non-SDLC session — never gated.
    if (session_type or "").strip().lower() != "eng" or not issue_number:
        return CompletionDecision(allow=True, reason="not_sdlc")

    # 2. Ledger query failed — fail open (never wedge a session).
    if not ledger_query_ok:
        return CompletionDecision(allow=True, reason="query_failed_fail_open")

    # 3. Empty-but-successful ledger — fail open (concern #1). This is a normal
    #    empty return from query_enriched, not an exception.
    if not stage_states:
        return CompletionDecision(allow=True, reason="ledger_empty")

    # 4/5. Terminal check — delegated entirely to is_pipeline_complete (one
    #      source of truth with the bridge path). pr_open is resolved by the
    #      caller for the docs-only branch.
    is_complete, terminal_reason = is_pipeline_complete(stage_states, "success", pr_open=pr_open)
    if is_complete:
        return CompletionDecision(allow=True, reason="terminal")
    if terminal_reason == "pr_state_unavailable":
        # Docs-only terminal candidate whose PR state could not be resolved
        # (gh error / timeout). Fail open — a hiccup must not trap a done
        # session (critique BLOCKER).
        return CompletionDecision(allow=True, reason="terminal_pr_state_unavailable_fail_open")

    # 6. Explicit structured blocked reason — allow.
    if blocked_reason and blocked_reason.strip():
        return CompletionDecision(allow=True, reason="blocked_reason_given")

    # 7. Refusals exhausted — escalate to the human.
    if refusal_count >= MAX_COMPLETION_REFUSALS:
        return CompletionDecision(allow=False, reason="escalate_exhausted", escalate_to_user=True)

    # 8. Refuse + re-route.
    return CompletionDecision(
        allow=False,
        reason="refused_non_terminal",
        reroute_message=_reroute_message(issue_number, next_skill),
    )
