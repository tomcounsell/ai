"""Pipeline State Machine for SDLC stage tracking.

Replaces the inference-based stage detection system (stage_detector.py,
skill_outcome.py, checkpoint.py) with a programmatic state machine that
records transitions at the points where they actually happen.

The state machine wraps pipeline_graph.py and manages stage statuses:
- pending: stage has not started
- ready: predecessor completed, this stage can start
- in_progress: stage is currently running
- completed: stage finished successfully
- failed: stage finished with failure
- skipped: the pipeline never dispatched this stage because it does not apply
  to this issue (issue #2577). Only PLAN and CRITIQUE can ever hold it, only
  via the explicit `skip_stage()` call, and only when the caller has verified
  there is no plan document to build or critique.

`completed` and `skipped` together form SETTLED_STATUSES -- the stage is
behind us and downstream ordering checks treat it as satisfied. They are not
interchangeable in meaning: `completed` asserts the stage ran and succeeded,
`skipped` asserts it never ran and was not supposed to.

State is persisted as a JSON dict on AgentSession.stage_states.
Each PM session run creates a fresh state machine from the session.

Usage:
    from agent.pipeline_state import PipelineStateMachine

    sm = PipelineStateMachine(session)
    sm.start_stage("BUILD")      # validates PLAN is completed
    sm.complete_stage("BUILD")   # marks BUILD completed, TEST ready
    sm.get_display_progress()    # returns {stage: status} for display
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, field_validator

from agent.pipeline_graph import (
    DISPLAY_STAGES,
    PIPELINE_EDGES,
    get_next_stage,
)

if TYPE_CHECKING:
    from models.agent_session import AgentSession

logger = logging.getLogger(__name__)

# All known stages including PATCH (routing-only) and CRITIQUE
ALL_STAGES = ["ISSUE", "PLAN", "CRITIQUE", "BUILD", "TEST", "PATCH", "REVIEW", "DOCS", "MERGE"]

# Valid status values
VALID_STATUSES = frozenset({"pending", "ready", "in_progress", "completed", "failed", "skipped"})

# Statuses meaning "this stage is behind us" -- every ordering check
# (predecessor satisfaction, backfill, remaining-work, next-stage) accepts
# either. See the module docstring for why they stay distinct on read.
SETTLED_STATUSES = frozenset({"completed", "skipped"})

# The CLOSED set of stages that may ever hold the `skipped` status (issue
# #2577). Membership is deliberately minimal and is a security boundary, not a
# convenience list:
#
# - PLAN and CRITIQUE are here because their applicability is DERIVABLE from
#   repository state -- with no plan document for the issue there is nothing to
#   write and nothing to critique -- and because neither stage backs a merge-gate
#   guarantee. `tools/merge_predicate.py` never reads them.
# - REVIEW is deliberately absent and must stay absent. A `completed` REVIEW
#   marker is exactly what the merge predicate gates on, so a skippable REVIEW
#   would be a way to merge without a review. The same reasoning excludes DOCS
#   (group (b) of the predicate) and MERGE.
# - ISSUE, BUILD, TEST and PATCH are absent because nothing needs them to be
#   skippable: they carry no verdict invariant, so `_backfill_predecessors`
#   already promotes them without a refusal.
SKIPPABLE_STAGES = frozenset({"PLAN", "CRITIQUE"})

# Underscore-prefixed metadata keys the state machine itself owns and re-applies
# on every `_save()`. Every OTHER `_*` key belongs to a different writer and is
# merged back from the live store instead (see `_save` / `_load_preserved_metadata`).
_OWNED_METADATA_KEYS = frozenset({"_patch_cycle_count", "_critique_cycle_count", "_stage_skips"})

# Set of known stage names for fast lookup
_ALL_STAGES_SET = frozenset(ALL_STAGES)


class StageStates(BaseModel):
    """Validated container for stage_states JSON data.

    Enforces that stage names are from ALL_STAGES and status values are
    from VALID_STATUSES. Unknown stage names are dropped. Unknown status
    values default to 'pending' for backward compatibility.

    Used at read/write boundaries in PipelineStateMachine to ensure data
    integrity of stage_states persisted in Redis.
    """

    stages: dict[str, str]

    @field_validator("stages", mode="before")
    @classmethod
    def validate_stages(cls, v: dict) -> dict:
        """Drop unknown stage names; coerce unknown statuses to 'pending'."""
        if not isinstance(v, dict):
            return {}
        validated = {}
        for stage, status in v.items():
            # Skip internal metadata keys (e.g. _patch_cycle_count)
            if stage.startswith("_"):
                continue
            if stage not in _ALL_STAGES_SET:
                logger.debug(f"StageStates: dropping unknown stage {stage!r}")
                continue
            if status not in VALID_STATUSES:
                logger.debug(
                    f"StageStates: unknown status {status!r} for {stage}, defaulting to 'pending'"
                )
                status = "pending"
            validated[stage] = status
        return validated

    @classmethod
    def from_dict(cls, data: dict) -> StageStates:
        """Create StageStates from a raw dict, filtering out metadata keys."""
        return cls(stages=data)

    def to_dict(self) -> dict[str, str]:
        """Return the validated stages dict."""
        return dict(self.stages)


# Regex to match <!-- OUTCOME {...} --> blocks in agent output
_OUTCOME_RE = re.compile(r"<!-- OUTCOME (\{.*?\}) -->")


def _parse_outcome_contract(output_tail: str) -> dict | None:
    """Parse an OUTCOME contract from agent output tail.

    Scans for ``<!-- OUTCOME {...} -->`` blocks and parses the JSON payload.
    If multiple blocks exist, uses the last one (most recent).

    Args:
        output_tail: Last ~500 chars of agent output.

    Returns:
        Parsed dict with at least a ``status`` key, or None if no valid
        OUTCOME block is found.
    """
    if not output_tail:
        return None

    matches = _OUTCOME_RE.findall(output_tail)
    if not matches:
        return None

    # Use last match (most recent OUTCOME block)
    raw = matches[-1]
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.debug("_parse_outcome_contract: malformed JSON in OUTCOME block")
        return None

    if not isinstance(parsed, dict) or "status" not in parsed:
        logger.debug("_parse_outcome_contract: missing 'status' key in OUTCOME block")
        return None

    return parsed


def _record_stage_metric(metric_name: str, stage: str) -> None:
    """Record an analytics metric for a stage transition. Best-effort."""
    try:
        from analytics.collector import record_metric

        record_metric(metric_name, 1, {"stage": stage})
    except Exception:  # noqa: S110 -- optional analytics telemetry
        pass


# Canonical critique verdict strings, matched case-insensitively against the
# dev-session output tail. Order matters: the longer "ready to build (with
# concerns)" must be tested before the generic "ready to build" prefix so we
# always capture the richer form.
_CRITIQUE_VERDICT_PATTERNS = [
    ("READY TO BUILD (with concerns)", "ready to build (with concerns)"),
    ("READY TO BUILD (no concerns)", "ready to build (no concerns)"),
    ("MAJOR REWORK", "major rework"),
    ("NEEDS REVISION", "needs revision"),
    ("READY TO BUILD", "ready to build"),
]

# Canonical review verdicts.
_REVIEW_VERDICT_PATTERNS = [
    ("CHANGES REQUESTED", "changes requested"),
    ("APPROVED", "approved"),
    ("REVIEW PASSED", "review passed"),
    ("REVIEW FAILED", "review failed"),
]


def _extract_critique_verdict(output_tail: str) -> str | None:
    """Extract the canonical critique verdict string from the output tail."""
    if not output_tail:
        return None
    lower = output_tail.lower()
    for canonical, needle in _CRITIQUE_VERDICT_PATTERNS:
        if needle in lower:
            return canonical
    return None


def _extract_review_verdict(output_tail: str) -> str | None:
    """Extract the canonical review verdict string from the output tail.

    Prefers values carried by an ``<!-- OUTCOME {...} -->`` block when
    available, since the review skill emits structured outcome contracts
    (``status=success|partial|fail``). Falls back to literal string matching.
    """
    if not output_tail:
        return None
    contract = _parse_outcome_contract(output_tail)
    if contract and contract.get("stage") == "REVIEW":
        status = contract.get("status", "")
        artifacts = contract.get("artifacts") or {}
        blockers = artifacts.get("blockers", 0) or 0
        tech_debt = artifacts.get("tech_debt", 0) or 0
        if status == "success" and not blockers and not tech_debt:
            return "APPROVED"
        if status in ("partial", "fail") or blockers or tech_debt:
            return "CHANGES REQUESTED"
    lower = output_tail.lower()
    for canonical, needle in _REVIEW_VERDICT_PATTERNS:
        if needle in lower:
            return canonical
    return None


def _review_counts(output_tail: str) -> tuple[int | None, int | None]:
    """Extract blocker / tech-debt counts from the OUTCOME contract if present."""
    contract = _parse_outcome_contract(output_tail)
    if not contract or contract.get("stage") != "REVIEW":
        return (None, None)
    artifacts = contract.get("artifacts") or {}
    blockers = artifacts.get("blockers")
    tech_debt = artifacts.get("tech_debt")
    try:
        blockers_i = int(blockers) if blockers is not None else None
    except (TypeError, ValueError):
        blockers_i = None
    try:
        tech_debt_i = int(tech_debt) if tech_debt is not None else None
    except (TypeError, ValueError):
        tech_debt_i = None
    return (blockers_i, tech_debt_i)


def _record_verdict_from_output(session, stage: str, output_tail: str) -> None:
    """Best-effort: write the extracted verdict via tools.sdlc_verdict.

    This is the unification path called from ``classify_outcome()``. It is the
    ONLY indirect writer to ``_verdicts`` — the CLI path and this path both
    funnel through ``tools.sdlc_verdict.record_verdict``, which in turn uses
    the optimistic-retry helper. If the verdict cannot be extracted or
    recording fails, this function silently returns. It never raises.
    """
    if stage not in ("CRITIQUE", "REVIEW"):
        return
    if session is None:
        return
    try:
        if stage == "CRITIQUE":
            verdict = _extract_critique_verdict(output_tail)
            if not verdict:
                return
            from tools.sdlc_verdict import record_verdict

            record_verdict(session, "CRITIQUE", verdict)
        else:
            verdict = _extract_review_verdict(output_tail)
            if not verdict:
                return
            blockers, tech_debt = _review_counts(output_tail)
            from tools.sdlc_verdict import record_verdict

            record_verdict(
                session,
                "REVIEW",
                verdict,
                blockers=blockers,
                tech_debt=tech_debt,
            )
    except Exception as e:
        logger.debug(f"_record_verdict_from_output({stage}) failed: {e}")


class PipelineStateMachine:
    """Manages SDLC pipeline stage transitions with ordering enforcement.

    Reads/writes stage state from one of two backing stores, selected at
    construction time:

    - **Session-keyed** (``__init__(session)``): reads/writes
      ``AgentSession.stage_states``. This is the original, executor-scoped
      path -- still used by callers not yet migrated to the issue-keyed
      ledger (see issue #2012 task 2).
    - **Issue-keyed** (``for_issue(target_repo, issue_number)``): reads/
      writes a durable ``PipelineLedger`` record keyed by
      ``(target_repo, issue_number)`` -- see ``agent/pipeline_ledger.py``.
      This path survives every AgentSession lifecycle event (crash,
      completion, takeover) because the ledger never lived on the session
      in the first place.

    Exactly one of ``self.session`` / ``self._ledger`` is set (the other is
    ``None``) depending on which constructor was used. All stage-transition
    methods (``start_stage``, ``complete_stage``, ``fail_stage``, etc.) are
    identical across both paths -- only the load/store primitives
    (``_read_raw`` / ``_write_raw`` / ``_load_preserved_metadata``) branch on
    which backing store is active. The state machine is stateless across
    requests -- each invocation loads fresh state from its backing store.

    Attributes:
        session: The AgentSession this state machine operates on, or
            ``None`` when constructed via ``for_issue()``.
        states: Dict mapping stage name to status string.
        patch_cycle_count: Number of PATCH -> TEST cycles completed.
        critique_cycle_count: Number of CRITIQUE -> PLAN -> CRITIQUE cycles completed.
    """

    def __init__(self, session: AgentSession) -> None:
        """Initialize from an AgentSession (session-keyed path).

        Loads stage_states from the session's field. If the field is
        None or empty, initializes all stages to pending with ISSUE
        set to ready.

        Args:
            session: AgentSession instance to read/write state from.
        """
        self.session = session
        self._ledger = None
        self.states: dict[str, str] = {}
        self.patch_cycle_count: int = 0
        self.critique_cycle_count: int = 0
        self.stage_skips: dict[str, dict] = {}
        self._load_state()

    @classmethod
    def for_issue(cls, target_repo: str, issue_number: int) -> PipelineStateMachine:
        """Construct an issue-keyed state machine backed by a PipelineLedger.

        Loads (creating if absent -- see ``PipelineLedger.get_or_create``)
        the durable ledger record for ``(target_repo, issue_number)`` and
        populates ``states``/``patch_cycle_count``/``critique_cycle_count``
        exactly as ``__init__`` does for the session-keyed path, just reading
        from the ledger's ``stage_states_json`` blob instead of
        ``session.stage_states``.

        ``self.session`` is ``None`` on an instance built this way -- there
        is no session in this path. Callers that need session-derived
        context (e.g. verdict extraction from agent output text via
        ``classify_outcome``) must use the session-keyed ``__init__`` path
        instead; ``for_issue()`` is for durable stage/verdict/pr_number
        bookkeeping keyed on the issue, independent of any executor.

        Args:
            target_repo: Already-resolved ``owner/name`` GitHub slug. Must
                not be ``None``/empty -- callers resolve this once, at
                lease-acquire time, and pin it on the issue lock payload
                (see ``tools/sdlc_session_ensure.py``); this method does not
                re-resolve or validate it.
            issue_number: The GitHub issue number.

        Returns:
            A ``PipelineStateMachine`` backed by the issue-keyed ledger.
        """
        from agent.pipeline_ledger import PipelineLedger

        instance = cls.__new__(cls)
        instance.session = None
        instance._ledger = PipelineLedger.get_or_create(target_repo, issue_number)
        instance.states = {}
        instance.patch_cycle_count = 0
        instance.critique_cycle_count = 0
        instance.stage_skips = {}
        instance._load_state()
        return instance

    def _load_state(self) -> None:
        """Load ``states`` plus the owned metadata keys from whichever backing
        store is active (``self.session`` or ``self._ledger``), applying
        defaults for missing stages.

        Shared by both constructors so the parsing/validation/defaulting
        logic exists exactly once regardless of backing store.
        """

        # Defaults first so every construction path (including callers that
        # build the instance via __new__) lands with the full attribute set.
        self.stage_skips = {}
        self._skip_precondition_cache: dict[str, tuple[str, str] | None] = {}

        def _apply(data: dict) -> None:
            self.states = {k: v for k, v in data.items() if k in ALL_STAGES}
            self.patch_cycle_count = data.get("_patch_cycle_count", 0)
            self.critique_cycle_count = data.get("_critique_cycle_count", 0)
            skips = data.get("_stage_skips")
            self.stage_skips = dict(skips) if isinstance(skips, dict) else {}

        raw = self._read_raw()
        if raw and isinstance(raw, str):
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    _apply(data)
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    f"Invalid stage_states JSON on {self._store_label()}, initializing defaults"
                )
        elif raw and isinstance(raw, dict):
            _apply(raw)

        # Initialize defaults for any missing stages
        for stage in ALL_STAGES:
            if stage not in self.states:
                self.states[stage] = "pending"

        # If nothing has started yet, mark ISSUE as ready
        if all(v == "pending" for v in self.states.values()):
            self.states["ISSUE"] = "ready"

    def _store_label(self) -> str:
        """Human-readable identifier of the active backing store, for logs."""
        if self._ledger is not None:
            return f"ledger {getattr(self._ledger, 'ledger_key', '?')}"
        return f"session {getattr(self.session, 'session_id', '?')}"

    def _refresh_ledger(self) -> None:
        """Reload ``self._ledger`` from Redis in place.

        Unlike ``AgentSession.stage_states`` (a computed property that
        re-reads its backing session_events on every access -- see
        ``models/agent_session.py``), a Popoto ``Field()`` value is cached
        on the in-memory instance once loaded and does NOT auto-refresh.
        Without this, a ``PipelineLedger`` instance held across a
        construct-then-later-save gap (exactly the merge-on-save window
        ``_save()``/``_load_preserved_metadata()`` protect) would read and
        re-persist a stale snapshot, clobbering a concurrent writer's
        ``_verdicts``/``_sdlc_dispatches`` AND any sibling field written in
        the interim (e.g. ``pr_number``, written by a different writer's
        ledger instance) -- silently reproducing the exact staleness bug
        the session path never had. Called before every read AND every
        write of the ledger path so both halves of the merge-on-save
        protocol operate on a fresh instance. Best-effort: if the record
        was deleted out from under us, keeps the last-known instance rather
        than raising.
        """
        if self._ledger is None:
            return
        fresh = type(self._ledger).load(ledger_key=self._ledger.ledger_key)
        if fresh is not None:
            self._ledger = fresh

    def _read_raw(self):
        """Return the raw stage-state blob from whichever backing store is
        active. May be ``None``, a JSON string, or (session path only) a
        dict, mirroring what ``AgentSession.stage_states`` has historically
        returned."""
        if self._ledger is not None:
            self._refresh_ledger()
            return getattr(self._ledger, "stage_states_json", None)
        return getattr(self.session, "stage_states", None)

    def _write_raw(self, data: dict) -> None:
        """Persist ``data`` (a JSON-serializable dict) to whichever backing
        store is active, then save the record. Raises on failure -- callers
        (``_save()``) catch and log."""
        payload = json.dumps(data)
        if self._ledger is not None:
            self._refresh_ledger()
            self._ledger.stage_states_json = payload
            self._ledger.save()
        else:
            self.session.stage_states = payload
            self.session.save()

    def _save(self) -> None:
        """Persist state back to the active backing store (session or ledger).

        Validates stage_states via the StageStates Pydantic model before
        serializing. Validation errors log a warning but do not crash --
        the data is still saved to avoid losing progress.

        Metadata preservation invariant (regression #1040 blocker 1;
        extended to the ledger path by issue #2012): ``_save()`` is a write
        path that only knows about ``self.states`` plus the keys in
        ``_OWNED_METADATA_KEYS`` (``_patch_cycle_count``,
        ``_critique_cycle_count`` and ``_stage_skips`` — explicitly re-added
        below). Any OTHER underscore-prefixed metadata
        key (``_verdicts``, ``_sdlc_dispatches``, or any future ``_*`` key)
        would be silently dropped if we serialized ``self.states`` alone. To
        protect cross-writer invariants — especially the verdict recorder in
        ``tools.sdlc_verdict`` and the dispatch recorder in
        ``agent.sdlc_router.record_dispatch`` — we reload the latest raw
        stage-state blob from the active backing store BEFORE writing and
        merge every ``_*`` key we did not manage ourselves. This makes
        ``_save()`` a safe participant in the cross-process stage-state
        write protocol on EITHER backing store: two ``for_issue()``
        instances for the same issue merge rather than clobber each other's
        ``_verdicts``/``_sdlc_dispatches``, exactly like two session-keyed
        instances for the same session always have.
        """
        # Validate states before saving
        try:
            validated = StageStates.from_dict(self.states)
            self.states = validated.to_dict()
            # Re-add any missing stages as pending after validation
            for stage in ALL_STAGES:
                if stage not in self.states:
                    self.states[stage] = "pending"
        except Exception as e:
            logger.warning(
                f"StageStates validation failed for {self._store_label()}: {e}. Saving anyway."
            )

        # Load any concurrent metadata writes from the live backing store so
        # we can preserve underscore-prefixed keys we don't own (see
        # invariant in docstring above). This avoids clobbering
        # ``_verdicts`` / ``_sdlc_dispatches`` that the verdict/dispatch
        # recorders wrote between construction and this save.
        preserved_metadata = self._load_preserved_metadata()

        data = dict(self.states)
        # Owned metadata keys — re-applied explicitly each save.
        data["_patch_cycle_count"] = self.patch_cycle_count
        data["_critique_cycle_count"] = self.critique_cycle_count
        data["_stage_skips"] = dict(self.stage_skips)
        # Unowned underscore metadata keys — merged back in without
        # overwriting the owned keys above.
        for key, value in preserved_metadata.items():
            if key in _OWNED_METADATA_KEYS:
                continue
            data[key] = value

        # #2730: record the stage entry AFTER the merge, so it operates on the
        # freshest `_sdlc_dispatches` just read from the store rather than a
        # stale in-memory copy, and lands inside this same single write.
        self._apply_pending_stage_entry(data)

        try:
            self._write_raw(data)
        except Exception as e:
            logger.warning(f"Failed to save stage_states for {self._store_label()}: {e}")

    def _apply_pending_stage_entry(self, data: dict) -> None:
        """Record a pending stage entry into ``data``'s dispatch history (#2730).

        Every branch of ``start_stage`` funnels through ``_activate_stage``, and
        so does every actor that starts a stage: the skill bodies via
        ``sdlc-tool stage-marker``, the PreToolUse hook (which never touches
        ``write_marker`` and is the dominant marker writer on the bridge), and
        ``/do-sdlc``'s backfills. Recording here is what makes the dispatch
        history unbypassable. Previously only the router wrote it, so a stage
        reached by a skill-to-skill chain such as ``/do-build`` → ``/do-patch``
        left a marker and no ledger entry, and G4 had nothing to count.

        The double-count question is delegated to
        ``sdlc_router.confirm_or_append_stage_entry``: the router's own
        pre-invocation record is an *unconfirmed slot* that this upgrades in
        place, so a router-driven dispatch still yields exactly one record.

        Called from ``_save()`` after the preserved-metadata merge, so it sees
        the freshest history and rides the same single write -- there is no
        second read-modify-write to race with.

        Never raises: a stage must not become unrecordable because its audit
        entry failed.
        """
        pending = getattr(self, "_pending_stage_entry", None)
        if not pending:
            return
        self._pending_stage_entry = None
        stage, prior_status = pending
        try:
            from agent.sdlc_router import confirm_or_append_stage_entry

            confirm_or_append_stage_entry(data, stage, prior_status=prior_status)
        except Exception as e:  # pragma: no cover - audit must never block a write
            logger.debug(f"pipeline_state: stage-entry dispatch record failed for {stage}: {e}")

    def _load_preserved_metadata(self) -> dict:
        """Return underscore-prefixed metadata keys from the live backing store.

        ``_save()`` calls this to pick up writes other writers (e.g.
        ``tools.sdlc_verdict.record_verdict``, ``agent.sdlc_router.record_dispatch``)
        may have made between when this state machine was constructed and
        the current save. Returns only ``_*`` keys outside
        ``_OWNED_METADATA_KEYS`` (the state machine re-applies those itself).
        Never raises.
        """
        try:
            raw = self._read_raw()
            if not raw:
                return {}
            if isinstance(raw, str):
                data = json.loads(raw)
            elif isinstance(raw, dict):
                data = raw
            else:
                return {}
            if not isinstance(data, dict):
                return {}
            return {
                k: v for k, v in data.items() if k.startswith("_") and k not in _OWNED_METADATA_KEYS
            }
        except Exception as e:
            logger.debug(f"_load_preserved_metadata: failed on {self._store_label()}: {e}")
            return {}

    def _get_predecessors(self, stage: str) -> list[str]:
        """Get stages that must be completed before this stage can start.

        Uses PIPELINE_EDGES to find which stages have edges leading to
        the given stage.
        """
        predecessors = []
        for (src, outcome), dst in PIPELINE_EDGES.items():
            if dst == stage and outcome == "success":
                predecessors.append(src)
        return predecessors

    def _activate_stage(self, stage: str) -> None:
        """Set stage to in_progress, save, and record analytics."""
        prior_status = self.states.get(stage)
        self.states[stage] = "in_progress"
        # #2730: flag the entry for _save() to record. It cannot be applied
        # here -- _save() re-reads `_sdlc_dispatches` from the backing store and
        # merges that copy, so an in-memory mutation made now would be silently
        # discarded by the very next line. The pre-entry status rides along
        # because the record must snapshot the same side of this transition the
        # router's slot did; see confirm_or_append_stage_entry.
        self._pending_stage_entry = (stage, prior_status)
        self._save()
        _record_stage_metric("sdlc.stage_started", stage)

    def _reaches_issue(self, stage: str) -> bool:
        """True iff `stage` sits on the ISSUE-rooted success spine — its transitive
        success-predecessor set contains ISSUE (or it IS ISSUE). PATCH is off-spine
        (`_get_predecessors("PATCH") == []`), so this returns False for it — that is
        what stops a backfill reaching TEST (predecessors [BUILD, PATCH]) from
        pulling the off-happy-path PATCH into the promotion set.
        """
        if stage == "ISSUE":
            return True
        seen: set[str] = set()
        frontier = list(self._get_predecessors(stage))
        while frontier:
            p = frontier.pop()
            if p in seen:
                continue
            seen.add(p)
            if p == "ISSUE":
                return True
            frontier.extend(self._get_predecessors(p))
        return False

    def _backfill_predecessors(self, stage: str) -> list[str]:
        """Promote the ISSUE-rooted success spine behind `stage` to completed.

        Scan-then-mutate: collect every transitive ON-SPINE predecessor NOT already
        settled (i.e. currently in {pending, ready, in_progress} -- a `completed` or
        `skipped` predecessor is already behind us and is left exactly as it is); if
        ANY collected member is `failed`, raise
        ValueError BEFORE mutating (a failed predecessor is a real inconsistency,
        never silently erased, and no partial state is persisted). Then promote all
        collected members in one pass, persist with a single _save(), and emit
        sdlc.stage_backfilled per synthetic promotion. Off-spine predecessors (PATCH,
        reached via TEST's second success in-edge) are never walked or promoted.
        Returns the promoted stages.

        Verdict invariant (issue #2305 defect 4): REVIEW/CRITIQUE carry a
        *marker-completed ⇒ verdict-readable* invariant on the direct
        ``write_marker`` completed-path (``tools/sdlc_stage_marker.py``).
        This backfill is a second, open write path to the same `completed`
        state -- reached whenever ANY downstream stage starts with
        ``backfill_predecessors=True`` -- so it must enforce the SAME
        invariant or it can mint a REVIEW/CRITIQUE `completed` marker with no
        verdict. During the SCAN phase (before any mutation, preserving the
        scan-then-mutate no-partial-state property), any to-promote member
        that is REVIEW or CRITIQUE is checked via
        ``tools.sdlc_verdict.verdict_invariant_satisfied`` against
        ``self._ledger.issue_number``. Unsatisfied or unresolvable
        (session-keyed construction with no ``_ledger``, or a `None`
        ``issue_number``) both raise ValueError -- fail CLOSED, symmetric
        with the failed-predecessor raise above -- leaving REVIEW/CRITIQUE at
        their real state.

        Skipped predecessors (issue #2577): a spine member that is `skipped`, or
        that VERIFIABLY qualifies to be, is settled rather than force-completed.
        The scan routes a PLAN/CRITIQUE in :data:`SKIPPABLE_STAGES` into
        ``to_skip`` when ``tools.sdlc_stage_marker._skip_precondition_error``
        confirms the stage was never dispatched and does not apply -- the SAME
        predicate the explicit ``stage-marker --status skipped`` call runs, so
        the two entry points cannot drift. This is what makes the ordinary
        review path work for a PR that never entered the pipeline: an agent that
        reaches ``sdlc-tool verdict finalize`` on a hand-authored fix, a
        review-derived follow-up, or a dependabot bump gets a truthful ledger
        instead of an unsatisfiable refusal, without having to know in advance
        that this issue has no plan.

        What that does NOT open (this is the whole security argument):

        - The auto-skip fires only for :data:`SKIPPABLE_STAGES`. REVIEW is not
          in it, so a verdict-less REVIEW still raises here, exactly as before,
          on every path -- including the `--stage DOCS --status completed` call
          that would otherwise be a way to forge an approval.
        - It fires only where the explicit call would also have been accepted:
          no plan document, no recorded verdict, no recorded dispatch, and a
          `pending`/`ready` status. A CRITIQUE that ran, or one whose plan
          exists, fails the precondition and still raises with its remedy.
        - Every probe inside the precondition fails CLOSED, so an unreadable
          ledger refuses the skip and falls through to the refusal.
        """
        to_promote: list[str] = []
        to_skip: list[str] = []
        seen: set[str] = set()
        # Seed and extend the frontier with ON-SPINE predecessors only — PATCH,
        # being off-spine, is excluded here and never force-completed.
        frontier = [p for p in self._get_predecessors(stage) if self._reaches_issue(p)]
        while frontier:  # SCAN — no mutation in this loop
            pred = frontier.pop()
            if pred in seen:
                continue
            seen.add(pred)
            st = self.states.get(pred, "pending")
            if st == "failed":
                raise ValueError(f"Cannot backfill predecessors of {stage}: {pred} is failed")
            if st not in SETTLED_STATUSES:
                if pred in SKIPPABLE_STAGES and self._qualifies_as_never_dispatched(pred):
                    to_skip.append(pred)
                else:
                    to_promote.append(pred)
            frontier.extend(p for p in self._get_predecessors(pred) if self._reaches_issue(p))

        verdict_gated = [p for p in to_promote if p in ("REVIEW", "CRITIQUE")]
        if verdict_gated:
            from tools.sdlc_verdict import verdict_invariant_satisfied

            issue_number = getattr(self._ledger, "issue_number", None) if self._ledger else None
            for pred in verdict_gated:
                if not issue_number:
                    raise ValueError(
                        f"Cannot backfill predecessors of {stage}: {pred} would be "
                        "force-completed but no issue_number is resolvable to verify "
                        "its verdict (unverifiable verdict must never be promoted) — "
                        "this pipeline is not issue-keyed; rebuild it via "
                        f"PipelineStateMachine.for_issue(target_repo, issue_number) and "
                        f"re-run {pred} so its verdict is recorded, then retry."
                    )
                if not verdict_invariant_satisfied(pred, issue_number):
                    remedy = (
                        f"re-run {pred} for issue #{issue_number} (the {pred} stage, "
                        "e.g. `sdlc-tool verdict finalize`) to record a verdict, then "
                        "retry the backfill."
                    )
                    if pred in SKIPPABLE_STAGES:
                        # Issue #2577: for a PR that never entered the pipeline
                        # there is no plan, so no honest CRITIQUE verdict can
                        # exist and the remedy above is unsatisfiable. Name the
                        # sanctioned alternative rather than leaving break-glass
                        # as the only way through.
                        # The auto-skip above already handled the genuinely
                        # never-dispatched case, so reaching here means the
                        # precondition REFUSED. Name which fact refused it.
                        refusal = self._never_dispatched_refusal(pred)
                        remedy += (
                            f" This {pred} is not recordable as never-dispatched either: {refusal}"
                        )
                    raise ValueError(
                        f"Cannot backfill predecessors of {stage}: {pred} would be "
                        f"force-completed for issue #{issue_number} but carries no "
                        f"finalized verdict (verdict invariant unsatisfied) — {remedy}"
                    )

        for pred in to_promote:  # MUTATE — only after a clean scan
            self.states[pred] = "completed"
        for pred in to_skip:
            self.states[pred] = "skipped"
            self.stage_skips[pred] = {
                "reason": (
                    f"no plan document for this issue; {pred} was never dispatched and "
                    "does not apply to this work (recorded during predecessor backfill)"
                ),
                "recorded_at": datetime.now(UTC).isoformat(),
            }
        if to_promote or to_skip:
            self._save()  # single persist for the whole chain
            for pred in to_promote:
                _record_stage_metric("sdlc.stage_backfilled", pred)
            for pred in to_skip:
                _record_stage_metric("sdlc.stage_skipped", pred)
        return to_promote

    def _never_dispatched_refusal(self, stage: str) -> str:
        """Human-readable reason the never-dispatched precondition refused ``stage``."""
        refusal = self._skip_precondition(stage)
        return refusal[1] if refusal else "(precondition now satisfied; retry)"

    def _skip_precondition(self, stage: str):
        """Delegate to the marker tool's verified never-dispatched predicate.

        Single implementation, two call sites (issue #2577), mirroring how the
        REVIEW verdict/trailer probes are shared between ``write_marker``'s
        direct completed-path and this backfill (#2305 defect 4): the explicit
        ``sdlc-tool stage-marker --status skipped`` call and the auto-skip in
        :meth:`_backfill_predecessors` cannot come to different conclusions
        about the same stage.

        Returns ``None`` when the stage genuinely qualifies as never-dispatched,
        or a ``(reason, message)`` tuple naming what refused it. Fails CLOSED:
        any import or evaluation error refuses.

        Memoized per instance: the probe walks ``docs/plans/`` and re-reads the
        ledger, and the backfill consults it for both PLAN and CRITIQUE plus
        again on the refusal path. A state-machine instance is constructed per
        operation, so the cache never outlives the decision it informs.
        """
        if stage in self._skip_precondition_cache:
            return self._skip_precondition_cache[stage]
        try:
            from tools.sdlc_stage_marker import _skip_precondition_error

            issue_number = getattr(self._ledger, "issue_number", None) if self._ledger else None
            result = _skip_precondition_error(stage, issue_number, ledger=self._ledger)
        except Exception as e:
            logger.debug(f"_skip_precondition({stage!r}) failed: {e} — refusing the skip")
            result = ("STAGE_RAN_NOT_SKIPPABLE", f"the precondition probe failed ({e})")
        self._skip_precondition_cache[stage] = result
        return result

    def _qualifies_as_never_dispatched(self, stage: str) -> bool:
        """True iff ``stage`` verifiably never ran and does not apply to this issue."""
        return self._skip_precondition(stage) is None

    def skip_stage(self, stage: str, reason: str) -> None:
        """Record ``stage`` as ``skipped`` — never dispatched, not applicable here.

        This is the honest counterpart to :meth:`complete_stage` for work that did
        not originate inside the SDLC pipeline (issue #2577). A hand-authored bug
        fix, a review-derived follow-up, or a dependabot bump has no plan document,
        so CRITIQUE has nothing to critique and no truthful verdict can ever exist
        for it. Before this existed, the only ways past
        :meth:`_backfill_predecessors`' verdict invariant were to write a synthetic
        CRITIQUE verdict (forging a stage completion) or to break-glass the merge
        gate; both happened repeatedly, which is what made the break-glass stop
        being a signal.

        This method is deliberately narrow and does NOT verify the precondition
        itself — the caller must have established that the stage genuinely never
        ran and does not apply. ``tools/sdlc_stage_marker.py`` is the sanctioned
        caller and performs those checks (no plan document, no recorded verdict,
        no recorded dispatch) under an owned issue lease.

        Args:
            stage: Must be in :data:`SKIPPABLE_STAGES`. REVIEW, DOCS and MERGE are
                permanently outside it — see that constant for why.
            reason: Non-empty explanation, persisted under the ``_stage_skips``
                metadata key so the disposition survives with its justification.

        Raises:
            ValueError: If the stage is not skippable, the reason is empty, or the
                stage has already started (``in_progress``/``completed``/``failed``)
                — a stage that actually ran is never retroactively skippable.
        """
        if stage not in SKIPPABLE_STAGES:
            raise ValueError(
                f"Cannot skip {stage}: only {sorted(SKIPPABLE_STAGES)} may ever be "
                "recorded skipped. A skippable REVIEW/DOCS/MERGE would be a way to "
                "merge without the guarantee that stage exists to provide."
            )
        if not reason or not reason.strip():
            raise ValueError(f"Cannot skip {stage}: a non-empty reason is required")

        current = self.states.get(stage, "pending")
        if current == "skipped":
            logger.info(f"Stage {stage} already skipped, no-op")
            return
        if current not in ("pending", "ready"):
            raise ValueError(
                f"Cannot skip {stage}: current status is {current!r}; only a stage "
                "that never started ('pending' or 'ready') can be recorded skipped"
            )

        self.states[stage] = "skipped"
        self.stage_skips[stage] = {
            "reason": reason.strip(),
            "recorded_at": datetime.now(UTC).isoformat(),
        }

        # Propagate readiness exactly as complete_stage does — a skipped stage is
        # settled, so its successor becomes startable.
        next_info = get_next_stage(
            stage, "success", self.patch_cycle_count, self.critique_cycle_count
        )
        if next_info and self.states.get(next_info[0], "pending") == "pending":
            self.states[next_info[0]] = "ready"

        self._save()
        _record_stage_metric("sdlc.stage_skipped", stage)
        logger.info(f"Stage {stage} skipped: {reason.strip()}")

    def start_stage(self, stage: str, backfill_predecessors: bool = False) -> None:
        """Mark a stage as in_progress.

        Validates that at least one predecessor is settled — completed, or
        skipped as not-applicable (via success edge in PIPELINE_EDGES). ISSUE can
        always be started. PATCH can be started if TEST or REVIEW failed.

        Args:
            stage: Stage name to start.
            backfill_predecessors: When True, if the predecessor check would
                otherwise fail, promote the ISSUE-rooted spine of predecessors
                to completed (see `_backfill_predecessors`) and activate the
                stage instead of raising. Defaults to False so existing
                strict callers (router, pre_tool_use hook) are unaffected.

        Raises:
            ValueError: If stage is invalid or predecessor not completed
                (and backfill_predecessors is False, or a predecessor is
                `failed`).
        """
        if stage not in ALL_STAGES:
            raise ValueError(f"Invalid stage: {stage!r}. Valid stages: {ALL_STAGES}")

        current = self.states.get(stage, "pending")
        if current == "in_progress":
            logger.info(f"Stage {stage} already in_progress, no-op")
            return
        if current == "completed":
            # Allow re-entry for cycles (TEST can restart after PATCH)
            logger.info(f"Stage {stage} re-entering from completed (cycle)")

        # ISSUE is always startable (it's the first stage)
        if stage == "ISSUE":
            self._activate_stage(stage)
            return

        # PATCH is startable if TEST or REVIEW is failed/completed
        if stage == "PATCH":
            test_status = self.states.get("TEST", "pending")
            review_status = self.states.get("REVIEW", "pending")
            if test_status in ("failed", "completed") or review_status in ("failed", "completed"):
                self._activate_stage(stage)
                return
            raise ValueError(
                f"Cannot start PATCH: neither TEST ({test_status}) "
                f"nor REVIEW ({review_status}) has completed or failed"
            )

        # For cycle re-entry: PLAN can restart after CRITIQUE fails
        if stage == "PLAN" and self.states.get("CRITIQUE") in ("failed",):
            self._activate_stage(stage)
            return

        # For cycle re-entry: TEST can restart after PATCH completes
        if stage == "TEST" and self.states.get("PATCH") in ("completed", "in_progress"):
            self._activate_stage(stage)
            return

        # Check predecessors
        predecessors = self._get_predecessors(stage)
        if not predecessors:
            # No known predecessors — allow start
            self._activate_stage(stage)
            return

        for pred in predecessors:
            if self.states.get(pred) in SETTLED_STATUSES:
                self._activate_stage(stage)
                return

        if backfill_predecessors:
            self._backfill_predecessors(stage)
            self._activate_stage(stage)
            return

        pred_statuses = {p: self.states.get(p, "pending") for p in predecessors}
        raise ValueError(
            f"Cannot start {stage}: no predecessor completed. Predecessors: {pred_statuses}"
        )

    def complete_stage(self, stage: str) -> None:
        """Mark a stage as completed.

        Sets the stage to completed and marks the next stage as ready
        (based on success edge in PIPELINE_EDGES).

        Args:
            stage: Stage name to complete.

        Raises:
            ValueError: If stage is invalid or not in_progress.
        """
        if stage not in ALL_STAGES:
            raise ValueError(f"Invalid stage: {stage!r}. Valid stages: {ALL_STAGES}")

        current = self.states.get(stage, "pending")
        if current == "completed":
            logger.info(f"Stage {stage} already completed, no-op")
            return
        if current != "in_progress" and current != "ready":
            raise ValueError(
                f"Cannot complete stage {stage}: current status is "
                f"{current!r}, expected 'in_progress' or 'ready'"
            )

        self.states[stage] = "completed"

        # Track PATCH cycles
        if stage == "PATCH":
            self.patch_cycle_count += 1

        # Mark next stage as ready
        next_info = get_next_stage(
            stage, "success", self.patch_cycle_count, self.critique_cycle_count
        )
        if next_info:
            next_stage = next_info[0]
            next_current = self.states.get(next_stage, "pending")
            if next_current in ("pending", "failed"):
                self.states[next_stage] = "ready"

        self._save()
        _record_stage_metric("sdlc.stage_completed", stage)
        logger.info(
            f"Stage {stage} completed. "
            f"Patch cycles: {self.patch_cycle_count}. "
            f"Next: {next_info[0] if next_info else 'terminal'}"
        )

    def fail_stage(self, stage: str) -> None:
        """Mark a stage as failed.

        Failed stages can trigger PATCH cycles (for TEST and REVIEW).
        Failing an already-completed stage is a no-op with warning.

        Args:
            stage: Stage name to fail.
        """
        if stage not in ALL_STAGES:
            raise ValueError(f"Invalid stage: {stage!r}. Valid stages: {ALL_STAGES}")

        current = self.states.get(stage, "pending")
        if current == "completed":
            logger.warning(f"Stage {stage} already completed, fail_stage is no-op")
            return

        self.states[stage] = "failed"

        # Track CRITIQUE cycles (incremented on failure since it triggers PLAN revision)
        if stage == "CRITIQUE":
            self.critique_cycle_count += 1

        # Mark next stage based on failure edge
        next_info = get_next_stage(stage, "fail", self.patch_cycle_count, self.critique_cycle_count)
        if next_info:
            next_stage = next_info[0]
            next_current = self.states.get(next_stage, "pending")
            if next_current in ("pending", "completed", "failed"):
                self.states[next_stage] = "ready"

        self._save()
        logger.info(
            f"Stage {stage} failed. Next: {next_info[0] if next_info else 'terminal (escalate)'}"
        )

    def get_display_progress(self) -> dict[str, str]:
        """Return stage statuses for display (excludes PATCH).

        Returns stored state only. Stage completion is exclusively determined
        by PipelineStateMachine stored state — no artifact inference.

        Returns:
            Dict mapping display stage names to their status strings.
            Only includes DISPLAY_STAGES (not PATCH).
        """
        return {stage: self.states.get(stage, "pending") for stage in DISPLAY_STAGES}

    def current_stage(self) -> str | None:
        """Return the stage currently in_progress, or None.

        If multiple stages are in_progress (shouldn't happen normally),
        returns the latest one in pipeline order.
        """
        for stage in reversed(ALL_STAGES):
            if self.states.get(stage) == "in_progress":
                return stage
        return None

    def next_stage(self, outcome: str = "success") -> tuple[str, str] | None:
        """Determine the next stage to transition to.

        Delegates to pipeline_graph.get_next_stage() using the current
        in_progress or last completed stage.

        Args:
            outcome: Result of current stage ("success", "fail", "partial").

        Returns:
            Tuple of (stage_name, skill_command), or None if pipeline
            is complete or should escalate to human.
        """
        current = self.current_stage()
        if current:
            return get_next_stage(
                current, outcome, self.patch_cycle_count, self.critique_cycle_count
            )

        # No stage in_progress — find the last settled stage. A `skipped` stage
        # counts: the pipeline is past it and must not be routed back to it.
        last_settled = None
        for stage in ALL_STAGES:
            if self.states.get(stage) in SETTLED_STATUSES:
                last_settled = stage

        if last_settled:
            return get_next_stage(
                last_settled, outcome, self.patch_cycle_count, self.critique_cycle_count
            )

        # Nothing started yet — return first stage
        return get_next_stage(None)

    def has_remaining_stages(self) -> bool:
        """Check if any display stages are not yet settled.

        Returns True if pipeline progression should continue.
        Returns False when MERGE is completed or no transitions remain. A
        `skipped` stage is settled — it is work the pipeline will never do, so
        it never keeps the pipeline "remaining".
        """
        # If MERGE is completed, pipeline is done
        if self.states.get("MERGE") == "completed":
            return False

        # Check if any display stage is still outstanding
        for stage in DISPLAY_STAGES:
            status = self.states.get(stage, "pending")
            if status not in SETTLED_STATUSES:
                return True

        return False

    def has_failed_stage(self) -> bool:
        """Check if any stage has failed.

        Returns True if any stage is in failed status.
        """
        return any(self.states.get(stage) == "failed" for stage in DISPLAY_STAGES)

    def classify_outcome(
        self,
        stage: str,
        stop_reason: str | None,
        output_tail: str = "",
    ) -> str:
        """Classify a stage's outcome from SDK stop_reason and output patterns.

        Three-tier approach:
        0. OUTCOME contract: structured ``<!-- OUTCOME {...} -->`` block in output.
           If found with a valid status, returns immediately.
        1. stop_reason from SDK: anything other than "end_turn" is a process
           failure (rate_limited, timeout, etc.)
        2. For "end_turn": deterministic tail patterns scoped to the known stage.

        Args:
            stage: The stage that just ran.
            stop_reason: SDK stop reason string.
            output_tail: Last ~500 chars of worker output.

        Returns:
            "success", "fail", "partial", or "ambiguous".
        """
        # Tier 0: OUTCOME contract parsing
        contract = _parse_outcome_contract(output_tail)
        if contract:
            status = contract.get("status", "")
            contract_stage = contract.get("stage", "")
            if contract_stage and contract_stage != stage:
                logger.warning(
                    f"classify_outcome({stage}): OUTCOME contract stage mismatch "
                    f"(expected {stage}, got {contract_stage}) — falling through to Tier 1/2"
                )
            elif status in ("success", "fail", "partial"):
                # Record the verdict for CRITIQUE/REVIEW before returning so
                # structured OUTCOME blocks also populate _verdicts.
                _record_verdict_from_output(self.session, stage, output_tail)
                logger.info(f"classify_outcome({stage}): OUTCOME contract -> {status}")
                return status
            else:
                logger.debug(
                    f"classify_outcome({stage}): OUTCOME contract has unknown status "
                    f"{status!r} — falling through to Tier 1/2"
                )

        # Tier 1: SDK stop_reason
        if stop_reason and stop_reason != "end_turn":
            logger.info(f"classify_outcome({stage}): stop_reason={stop_reason} -> fail")
            return "fail"

        # Tier 2: deterministic output patterns per stage
        tail = output_tail.lower() if output_tail else ""

        if stage == "ISSUE":
            if "issues/" in tail or "issue created" in tail or "issue #" in tail:
                return "success"
        elif stage == "CRITIQUE":
            # Record the verdict before returning so the SDLC router can
            # consume it via `_verdicts["CRITIQUE"]`. This is the unification
            # point: bridge-initiated sessions funnel through the same
            # `tools.sdlc_verdict.record_verdict` writer as the CLI path.
            _record_verdict_from_output(self.session, "CRITIQUE", output_tail)
            if "ready to build" in tail:
                return "success"
            if "needs revision" in tail:
                return "fail"
            if "major rework" in tail:
                # Major rework escalates to human — return ambiguous so caller
                # can inspect and decide (typically escalate rather than auto-loop)
                return "ambiguous"
        elif stage == "PLAN":
            if "docs/plans/" in tail or "plan created" in tail or "plan finalized" in tail:
                return "success"
        elif stage == "BUILD":
            if "pull/" in tail or "pr created" in tail or "pr #" in tail:
                return "success"
            if "outcome" in tail and '"status":"success"' in tail:
                return "success"
        elif stage == "TEST":
            if "passed" in tail and "failed" not in tail:
                return "success"
            if "failed" in tail or "error" in tail:
                return "fail"
        elif stage == "PATCH":
            # PATCH succeeds if it produced commits
            if "commit" in tail or "pushed" in tail:
                return "success"
        elif stage == "REVIEW":
            # Record the verdict before returning (see CRITIQUE above for
            # rationale — unifies bridge and CLI write paths).
            _record_verdict_from_output(self.session, "REVIEW", output_tail)
            if "approved" in tail or "review passed" in tail:
                return "success"
            if "changes requested" in tail or "review failed" in tail:
                return "fail"
        elif stage == "DOCS":
            if "documentation" in tail and ("created" in tail or "updated" in tail):
                return "success"
        elif stage == "MERGE":
            if "merged" in tail:
                return "success"

        return "ambiguous"

    def to_dict(self) -> dict[str, Any]:
        """Serialize full state for debugging/logging."""
        return {
            "states": dict(self.states),
            "patch_cycle_count": self.patch_cycle_count,
            "critique_cycle_count": self.critique_cycle_count,
            "current_stage": self.current_stage(),
            "has_remaining": self.has_remaining_stages(),
        }

    @classmethod
    def derive_from_durable_signals(cls, session) -> dict[str, str]:
        """FALLBACK: Derive pipeline progress from durable artifacts.

        This is a FALLBACK path, not the primary signal source. It is only
        consulted by ``/do-merge`` when ``get_display_progress()`` returns
        an empty/all-``pending`` dict on a cold Redis (fresh machine, eviction,
        cleared Popoto session). The primary path remains Redis-backed
        ``stage_states`` written by the PipelineStateMachine itself.

        Signals consulted (per stage):
        - **PLAN** — plan file exists at ``origin/{branch}:docs/plans/{slug}.md``
          with a ``tracking:`` URL.
        - **BUILD** — ``gh pr list --search "#{issue}" --state open`` returns
          at least one PR whose ``headRefName`` equals ``session/{slug}``.
        - **TEST** — ``gh pr view --json statusCheckRollup`` shows all
          checks passing (no ``FAILURE``/``TIMED_OUT``/``CANCELLED``).
        - **REVIEW** — most recent ``## Review:`` issue comment on the PR
          starts with ``## Review: Approved``. Stale reviews are filtered
          using the commit-SHA filter (comments before the latest commit's
          ``committer_date`` are dropped), consistent with item 2's filter.
        - **DOCS** — **tri-OR derivation**: returns ``completed`` if ANY of:
          (a) ``gh pr diff --name-only`` shows at least one ``docs/`` file,
          (b) every ``- [ ]`` checkbox in the plan's ``## Documentation``
          section is ticked (``- [x]``),
          (c) the latest ``## Review:`` comment body matches
          ``docs (complete|updated|verified|reviewed)`` case-insensitively.
          If none of the three fire, DOCS returns ``pending``.

        **Note on downstream routing**: ``/do-merge`` is a terminal gate,
        NOT a router. When this function returns ``pending`` for a stage,
        the gate prints that state and returns GATES_FAILED; the PM session
        then reads the output and dispatches the appropriate remediation
        skill on its next turn. This is a two-step loop through the PM
        session — this function never dispatches anything.

        **Failure semantics**: Any subprocess error (``gh api``/``git show``
        failure, network error, JSON parse error, missing binaries) is
        caught at the top level and the corresponding stage is recorded as
        ``pending`` — equivalent to cold-Redis behavior. The function never
        raises. A warning is logged for each swallowed error.

        Args:
            session: AgentSession instance. The ``slug`` attribute is the
                sole required field; everything else is read via the
                subprocess helpers.

        Returns:
            Dict mapping DISPLAY_STAGES to one of ``"completed"``,
            ``"pending"``, or ``"failed"``. On complete subprocess failure,
            returns ``{}`` (matches the cold-Redis return shape).
        """
        slug = getattr(session, "slug", None)
        if not slug:
            logger.debug("derive_from_durable_signals: session has no slug")
            return {}

        states: dict[str, str] = {stage: "pending" for stage in DISPLAY_STAGES}
        states["ISSUE"] = "completed"  # Session exists → ISSUE has completed

        branch = f"session/{slug}"
        plan_path = f"docs/plans/{slug}.md"

        try:
            # --- PLAN ----------------------------------------------------
            plan_text = _durable_git_show(f"origin/{branch}:{plan_path}")
            if plan_text is None:
                plan_text = _durable_git_show(f"origin/main:{plan_path}")
            if plan_text and "tracking:" in plan_text:
                states["PLAN"] = "completed"

            # CRITIQUE: we treat CRITIQUE as completed if PLAN is completed
            # and the plan text contains a ``## Critique Results`` section
            # with any content (matches Rule 1 of the PM persona's artifact
            # verification table).
            if states["PLAN"] == "completed" and plan_text:
                if _plan_has_critique_results(plan_text):
                    states["CRITIQUE"] = "completed"

            # --- BUILD: look for an open PR on session/{slug} -------------
            pr_info = _durable_gh_pr_for_branch(branch)
            if pr_info:
                states["BUILD"] = "completed"

            # --- TEST: check statusCheckRollup on the PR -----------------
            latest_commit_date: str | None = None
            if pr_info:
                pr_number = pr_info.get("number")
                check_verdict = _durable_pr_checks_verdict(pr_number)
                if check_verdict == "success":
                    states["TEST"] = "completed"
                elif check_verdict == "failure":
                    states["TEST"] = "failed"
                # "pending"/"unknown" stays as pending

                latest_commit_date = _durable_pr_latest_commit_date(pr_number)

            # --- REVIEW: latest ``## Review:`` comment, SHA-filtered ------
            latest_review_body: str | None = None
            if pr_info:
                latest_review_body = _durable_latest_review_comment(
                    pr_info.get("number"), latest_commit_date
                )
                if latest_review_body:
                    if latest_review_body.startswith("## Review: Approved"):
                        states["REVIEW"] = "completed"
                    elif latest_review_body.startswith("## Review: Changes Requested"):
                        states["REVIEW"] = "failed"

            # --- DOCS: tri-OR derivation ---------------------------------
            # (a) docs/ files in PR diff
            # (b) all ## Documentation checkboxes ticked in plan
            # (c) latest ## Review: comment body mentions docs
            docs_completed = False
            if pr_info:
                if _durable_pr_diff_has_docs(pr_info.get("number")):
                    docs_completed = True

            if not docs_completed and plan_text and _plan_docs_checkboxes_all_ticked(plan_text):
                docs_completed = True

            if not docs_completed and latest_review_body:
                if _review_comment_mentions_docs(latest_review_body):
                    docs_completed = True

            if docs_completed:
                states["DOCS"] = "completed"

        except Exception as exc:  # top-level fail-closed guard
            logger.warning(
                "derive_from_durable_signals: unexpected error deriving signals for slug %s: %s",
                slug,
                exc,
            )
            # Leave any already-populated states in place; the rest stays
            # ``pending`` so the gate treats them as unknown-not-completed.
        return states


def resolve_pipeline_state_machine(session: AgentSession) -> tuple[PipelineStateMachine, bool, str]:
    """Resolve the ``PipelineStateMachine`` for a live session, preferring
    the issue-keyed ``PipelineLedger`` (issue #2012 follow-up).

    ``agent/hooks/pre_tool_use.py::_start_pipeline_stage`` and
    ``agent/hooks/post_tool_use.py::_complete_pipeline_stage`` fire INSIDE a
    live Eng session's process on every real ``/do-*`` stage-skill
    invocation/return. They used to construct ``PipelineStateMachine(session)``
    directly -- the OLD session-keyed path -- which meant the actual
    production pipeline still wrote primarily through the store this whole
    refactor exists to retire, split-brained against the offline
    ``sdlc-tool`` CLI writers that already moved to the ledger. This function
    is the shared cutover point for both hooks.

    Peeks the per-issue run_id lease (``tools._sdlc_utils.resolve_ledger_lease``)
    using the session's own ``issue_number``/``active_run_id``. When the
    lease confirms ``active_run_id`` as the live owner AND a ``target_repo``
    is pinned on it, returns a ``PipelineStateMachine.for_issue(target_repo,
    issue_number)`` instance -- the SAME ledger record a takeover session
    (different session_id/active_run_id, same issue_number, a freshly
    re-acquired lease) would resolve, because the key is ``(target_repo,
    issue_number)``, never the executor.

    Any other outcome -- missing ``issue_number``/``active_run_id``, an
    absent/foreign lease, or a lease with no ``target_repo`` pinned yet --
    falls back to the original session-keyed ``PipelineStateMachine(session)``
    path. This is intentionally a **fallback, not a hard-fail**: unlike the
    offline CLI writers (which refuse loudly on an unresolved lease), these
    hooks are best-effort and in-process -- their own docstrings say
    failures must never block the Agent tool. Never raises.

    Args:
        session: The AgentSession to resolve a state machine for.

    Returns:
        A 3-tuple ``(state_machine, used_ledger, detail)``:

        - ``state_machine``: the resolved ``PipelineStateMachine``.
        - ``used_ledger``: ``True`` iff the issue-keyed ledger path was used.
        - ``detail``: a short human-readable string for the caller's debug
          log -- either the resolved ``"{target_repo}:{issue_number}"`` key
          or the reason the ledger path was skipped.
    """
    issue_number = getattr(session, "issue_number", None)
    run_id = getattr(session, "active_run_id", None)

    if not issue_number or not run_id:
        return (
            PipelineStateMachine(session),
            False,
            "session fallback (missing issue_number/active_run_id)",
        )

    try:
        from tools._sdlc_utils import resolve_ledger_lease

        target_repo, error = resolve_ledger_lease(issue_number, run_id)
    except Exception as e:
        logger.debug(
            f"resolve_pipeline_state_machine: lease resolution raised for "
            f"issue #{issue_number}: {e}"
        )
        target_repo, error = None, {"reason": "LEASE_RESOLUTION_ERROR"}

    if target_repo:
        return (
            PipelineStateMachine.for_issue(target_repo, issue_number),
            True,
            f"ledger {target_repo}:{issue_number}",
        )

    reason = (error or {}).get("reason", "TARGET_REPO_MISSING")
    return (
        PipelineStateMachine(session),
        False,
        f"session fallback ({reason})",
    )


# ---------------------------------------------------------------------------
# Durable-signal helpers (item 1 of sdlc-1155)
#
# These helpers are NOT part of the PipelineStateMachine contract — they are
# private subprocess-based readers consulted only from
# ``PipelineStateMachine.derive_from_durable_signals``. Each helper returns a
# plain Python value (or ``None``) and catches every exception internally; the
# caller treats ``None`` as "signal absent" and marks the corresponding stage
# as ``pending``. No function here writes anything to disk or Redis.
# ---------------------------------------------------------------------------


def _durable_run(cmd: list[str], timeout: int = 15) -> str | None:
    """Run a subprocess, return stdout on exit 0, ``None`` on any failure.

    Never raises. All errors (missing binary, non-zero exit, timeout, decode
    failure) are caught and logged at WARNING/DEBUG and result in ``None``.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        logger.warning("derive_from_durable_signals: binary not found: %s", exc)
        return None
    except subprocess.TimeoutExpired as exc:
        logger.warning("derive_from_durable_signals: timeout running %s: %s", cmd[:2], exc)
        return None
    except Exception as exc:  # defensive catch-all; must never raise upward
        logger.warning("derive_from_durable_signals: unexpected error running %s: %s", cmd[:2], exc)
        return None

    if result.returncode != 0:
        logger.debug(
            "derive_from_durable_signals: %s exited %d (%s)",
            cmd[:2],
            result.returncode,
            (result.stderr or "").strip()[:200],
        )
        return None
    return result.stdout


def _durable_git_show(spec: str) -> str | None:
    """Return the contents of ``git show {spec}`` or ``None``."""
    return _durable_run(["git", "show", spec])


def _durable_gh_pr_for_branch(branch: str) -> dict | None:
    """Return a dict describing the (most recent) PR on ``branch``, or None.

    Reads open PRs first; if no open PR exists, falls back to any state so
    that closed/merged PRs still signal BUILD completion (they demonstrate
    the artifact was produced).
    """
    for state in ("open", "all"):
        out = _durable_run(
            [
                "gh",
                "pr",
                "list",
                "--head",
                branch,
                "--state",
                state,
                "--json",
                "number,headRefName,state",
                "--limit",
                "5",
            ]
        )
        if out is None:
            continue
        try:
            parsed = json.loads(out)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list) and parsed:
            return parsed[0]
    return None


def _durable_pr_checks_verdict(pr_number) -> str:
    """Return ``"success"``/``"failure"``/``"pending"``/``"unknown"``."""
    if pr_number is None:
        return "unknown"
    out = _durable_run(["gh", "pr", "view", str(pr_number), "--json", "statusCheckRollup"])
    if out is None:
        return "unknown"
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        return "unknown"
    checks = parsed.get("statusCheckRollup") or []
    if not checks:
        # PRs without CI configured default to success (no checks to fail).
        return "success"
    seen_failure = False
    seen_pending = False
    for check in checks:
        conclusion = (check.get("conclusion") or "").upper()
        state = (check.get("state") or "").upper()
        if conclusion in {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED"}:
            seen_failure = True
        elif conclusion in {"", "PENDING", "IN_PROGRESS", "QUEUED"} and state in {
            "",
            "PENDING",
            "IN_PROGRESS",
            "QUEUED",
        }:
            # No conclusion yet → still running.
            seen_pending = True
    if seen_failure:
        return "failure"
    if seen_pending:
        return "pending"
    return "success"


def _durable_pr_latest_commit_date(pr_number) -> str | None:
    """Return the ISO-8601 ``committer.date`` of the PR's latest commit, or None."""
    if pr_number is None:
        return None
    out = _durable_run(
        [
            "gh",
            "api",
            f"repos/:owner/:repo/pulls/{pr_number}/commits",
            "--jq",
            ".[-1].commit.committer.date",
        ]
    )
    if out is None:
        return None
    date = out.strip()
    return date or None


def _durable_latest_review_comment(pr_number, latest_commit_date: str | None) -> str | None:
    """Return the body of the most recent ``## Review:`` issue comment.

    If ``latest_commit_date`` is provided, comments with ``created_at`` strictly
    older than that are dropped (matches item 2's commit-SHA filter semantics
    so the durable fallback and the gate's comment check agree). Exact-time
    ties are kept (``>=`` comparison).
    """
    if pr_number is None:
        return None
    out = _durable_run(
        [
            "gh",
            "api",
            f"repos/:owner/:repo/issues/{pr_number}/comments",
            "--paginate",
            "--jq",
            ".[] | {body: .body, created_at: .created_at}",
        ]
    )
    if out is None:
        return None
    candidate_body: str | None = None
    for raw_line in out.splitlines():
        if not raw_line.strip():
            continue
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        body = entry.get("body") or ""
        if not body.startswith("## Review:"):
            continue
        created_at = entry.get("created_at") or ""
        if latest_commit_date and created_at and created_at < latest_commit_date:
            continue  # stale review, drop
        candidate_body = body  # gh api returns in ASC order → last kept = newest
    return candidate_body


def _durable_pr_diff_has_docs(pr_number) -> bool:
    """Return True if the PR diff touches at least one ``docs/`` file."""
    if pr_number is None:
        return False
    out = _durable_run(["gh", "pr", "diff", str(pr_number), "--name-only"])
    if out is None:
        return False
    for line in out.splitlines():
        if line.strip().startswith("docs/"):
            return True
    return False


def _plan_has_critique_results(plan_text: str) -> bool:
    """Return True if the plan contains a non-empty ``## Critique Results`` section."""
    match = re.search(r"(?m)^##\s+Critique Results\s*$", plan_text)
    if not match:
        return False
    tail = plan_text[match.end() :]
    # Non-empty = some non-whitespace content before next ## heading
    next_heading = re.search(r"(?m)^##\s+\S", tail)
    body = tail[: next_heading.start()] if next_heading else tail
    return bool(body.strip())


def _plan_docs_checkboxes_all_ticked(plan_text: str) -> bool:
    """Return True if every ``- [ ]`` in the plan's ``## Documentation`` section is ticked."""
    match = re.search(r"(?m)^##\s+Documentation\s*$", plan_text)
    if not match:
        return False
    tail = plan_text[match.end() :]
    next_heading = re.search(r"(?m)^##\s+\S", tail)
    body = tail[: next_heading.start()] if next_heading else tail
    # Any unticked checkbox → False
    if re.search(r"^\s*-\s+\[\s\]\s", body, flags=re.MULTILINE):
        return False
    # Must have at least one ticked checkbox to count as "all ticked"
    return bool(re.search(r"^\s*-\s+\[x\]\s", body, flags=re.MULTILINE | re.IGNORECASE))


_DOCS_REVIEW_ACK_RE = re.compile(
    r"docs\s+(complete|completed|updated|verified|reviewed)",
    re.IGNORECASE,
)


def _review_comment_mentions_docs(comment_body: str) -> bool:
    """Return True if the ``## Review:`` comment body acknowledges docs.

    Matches ``docs (complete|completed|updated|verified|reviewed)``
    case-insensitively (per Open Question #5 resolution).
    """
    if not comment_body:
        return False
    return bool(_DOCS_REVIEW_ACK_RE.search(comment_body))
