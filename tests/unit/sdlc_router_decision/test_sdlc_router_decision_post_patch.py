"""Post-patch row-8b ownership tests for agent.sdlc_router.decide_next_dispatch() (#2879)."""

from __future__ import annotations

from agent.sdlc_router import (
    SKILL_DO_BUILD,
    SKILL_DO_DOCS,
    SKILL_DO_MERGE,
    SKILL_DO_PATCH,
    SKILL_DO_PR_REVIEW,
    Blocked,
    Dispatch,
    _review_verdict_is_stale,
    _rule_patch_applied_after_review,
    decide_next_dispatch,
)

# ---------------------------------------------------------------------------
# Row 8b widening — stale REVIEW verdict ownership (#2767b)
# ---------------------------------------------------------------------------


_VERDICT_AT = "2026-08-13T10:00:00+00:00"
_PATCH_BEFORE_VERDICT = "2026-08-13T09:00:00+00:00"
_PATCH_AFTER_VERDICT = "2026-08-13T11:00:00+00:00"


def _post_patch_states(
    *,
    verdict: str | None = "CHANGES REQUESTED",
    recorded_at: str | None = _VERDICT_AT,
    patch_dispatched_at: str | None = _PATCH_AFTER_VERDICT,
    review_status: str = "completed",
) -> dict:
    """A PR-stage state after build → review → patch.

    ``patch_dispatched_at`` controls staleness: a ``/do-patch`` dispatch
    recorded AFTER ``recorded_at`` makes the verdict stale.
    """
    verdicts: dict = {}
    if verdict is not None:
        record: dict = {"verdict": verdict}
        if recorded_at is not None:
            record["recorded_at"] = recorded_at
        verdicts["REVIEW"] = record

    dispatches: list = [{"skill": SKILL_DO_BUILD, "at": "2026-08-13T08:00:00+00:00"}]
    if patch_dispatched_at is not None:
        dispatches.append({"skill": SKILL_DO_PATCH, "at": patch_dispatched_at})

    return {
        "ISSUE": "ready",
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "BUILD": "completed",
        "TEST": "completed",
        "PATCH": "completed",
        "REVIEW": review_status,
        "DOCS": "pending",
        "MERGE": "pending",
        "_verdicts": verdicts,
        "_sdlc_dispatches": dispatches,
    }


class TestRow8bOwnsStaleVerdict:
    """The spike-1 four-scenario table (#2767b). All four must Dispatch.

    Before the widening, the fourth row (stale verdict + last dispatch
    ``/do-pr-review``) was owned by nobody and returned
    ``Blocked('no matching dispatch rule')``.
    """

    def test_fresh_changes_requested_last_patch_routes_to_patch(self):
        states = _post_patch_states(patch_dispatched_at=_PATCH_BEFORE_VERDICT)
        meta = {"pr_number": 4242, "last_dispatched_skill": SKILL_DO_PATCH}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch), f"expected Dispatch, got {result!r}"
        assert result.skill == SKILL_DO_PATCH
        assert result.row_id == "8"

    def test_fresh_changes_requested_last_review_routes_to_patch(self):
        states = _post_patch_states(patch_dispatched_at=_PATCH_BEFORE_VERDICT)
        meta = {"pr_number": 4242, "last_dispatched_skill": SKILL_DO_PR_REVIEW}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch), f"expected Dispatch, got {result!r}"
        assert result.skill == SKILL_DO_PATCH
        assert result.row_id == "8"

    def test_no_verdict_at_all_routes_to_review(self):
        for last in (SKILL_DO_PATCH, SKILL_DO_PR_REVIEW):
            states = _post_patch_states(verdict=None, review_status="pending")
            meta = {"pr_number": 4242, "last_dispatched_skill": last}
            result = decide_next_dispatch(states, meta)
            assert isinstance(result, Dispatch), (
                f"expected Dispatch for last={last}, got {result!r}"
            )
            assert result.skill == SKILL_DO_PR_REVIEW
            assert result.row_id == "7"

    def test_stale_changes_requested_last_patch_routes_to_review(self):
        states = _post_patch_states()
        meta = {"pr_number": 4242, "last_dispatched_skill": SKILL_DO_PATCH}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch), f"expected Dispatch, got {result!r}"
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.row_id == "8b"

    def test_stale_changes_requested_last_review_routes_to_review(self):
        """THE #2767b BUG: previously Blocked('no matching dispatch rule')."""
        states = _post_patch_states()
        meta = {"pr_number": 4242, "last_dispatched_skill": SKILL_DO_PR_REVIEW}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch), f"expected Dispatch, got {result!r}"
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.row_id == "8b"

    def test_stale_verdict_owned_regardless_of_last_dispatched_skill(self):
        """Even an unrelated/absent last dispatch leaves the state owned."""
        for last in (SKILL_DO_BUILD, "", None):
            states = _post_patch_states()
            meta = {"pr_number": 4242, "last_dispatched_skill": last}
            result = decide_next_dispatch(states, meta)
            assert isinstance(result, Dispatch), f"expected Dispatch for last={last!r}"
            assert result.row_id == "8b"


class TestRows8And9Unchanged:
    """The widening must not disturb the fresh-verdict paths."""

    def test_fresh_changes_requested_still_row_8(self):
        states = _post_patch_states(patch_dispatched_at=None)
        meta = {"pr_number": 4242, "last_dispatched_skill": SKILL_DO_PR_REVIEW}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PATCH
        assert result.row_id == "8"

    def test_fresh_approved_review_completed_still_row_9(self):
        states = _post_patch_states(verdict="APPROVED", patch_dispatched_at=None)
        meta = {"pr_number": 4242, "last_dispatched_skill": SKILL_DO_PR_REVIEW}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_DOCS
        assert result.row_id == "9"


class TestStaleApprovedIsReReviewedNotAdvanced:
    """The widening's one genuine ownership TRANSFER, pinned deliberately.

    Row 8b precedes rows 8f/9/10 in ``DISPATCH_RULES``, so a **stale** APPROVED
    verdict -- one recorded before the latest ``/do-patch`` dispatch -- is now
    re-reviewed rather than advanced to ``/do-docs`` or the merge fast-path.

    This is not incidental. An APPROVED verdict that predates a patch does not
    describe the code the patch produced, and advancing on it would merge code
    no review ever saw. Spike-2's "rows 8 and 9 unchanged" holds for FRESH
    verdicts only; these cases are the boundary of that claim.

    It converges: a re-review records a verdict newer than the patch dispatch,
    which is then fresh, and row 9 owns it again. G4 bounds the loop if it
    somehow does not.
    """

    def test_stale_approved_review_completed_routes_to_re_review(self):
        states = _post_patch_states(verdict="APPROVED", review_status="completed")
        meta = {"pr_number": 4242, "last_dispatched_skill": SKILL_DO_PR_REVIEW}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch), f"expected Dispatch, got {result!r}"
        assert result.skill == SKILL_DO_PR_REVIEW
        assert result.row_id == "8b"

    def test_a_fresh_re_review_then_advances_to_docs(self):
        """Convergence, executable: the same state with a verdict recorded
        AFTER the patch dispatch is fresh, and row 9 owns it."""
        states = _post_patch_states(
            verdict="APPROVED", review_status="completed", patch_dispatched_at=None
        )
        meta = {"pr_number": 4242, "last_dispatched_skill": SKILL_DO_PR_REVIEW}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_DOCS
        assert result.row_id == "9"


class TestRow8bDisjointFromEmptyVerdictRows:
    """Rows 8c/8d/8e require NO recorded verdict; the new staleness disjunct is
    identically False there because ``recorded_at`` only exists alongside a
    recorded verdict (``tools.sdlc_verdict.record_verdict`` refuses to write an
    empty verdict)."""

    def test_row_8c_in_progress_no_verdict_still_wins(self):
        states = _post_patch_states(verdict=None, review_status="in_progress")
        states["PATCH"] = "pending"
        meta = {"pr_number": 4242, "last_dispatched_skill": SKILL_DO_PR_REVIEW}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.row_id == "8c"

    def test_row_8d_crash_after_dispatch_still_wins(self):
        states = _post_patch_states(verdict=None, review_status="failed")
        meta = {"pr_number": 4242, "last_dispatched_skill": SKILL_DO_PR_REVIEW}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.row_id == "8d"

    def test_row_8e_completed_no_verdict_still_wins(self):
        states = _post_patch_states(verdict=None, review_status="completed")
        meta = {"pr_number": 4242, "last_dispatched_skill": SKILL_DO_BUILD}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.row_id == "8e"

    def test_staleness_disjunct_is_false_on_every_empty_verdict_state(self):
        """The proof, executable: no recorded verdict → never stale."""
        for review_status in ("in_progress", "failed", "completed", "pending"):
            states = _post_patch_states(verdict=None, review_status=review_status)
            assert _review_verdict_is_stale(states) is False


class TestRow8bFailurePaths:
    def test_malformed_recorded_at_is_not_stale_and_row_8_keeps_control(self):
        """``_review_verdict_is_stale`` fails safe to False on a garbage
        timestamp, so a fresh-looking CHANGES REQUESTED verdict still routes to
        ``/do-patch`` rather than spinning in a re-review loop."""
        states = _post_patch_states(recorded_at="not-a-timestamp")
        assert _review_verdict_is_stale(states) is False
        meta = {"pr_number": 4242, "last_dispatched_skill": SKILL_DO_PR_REVIEW}
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_PATCH
        assert result.row_id == "8"

    def test_predicate_never_raises_on_malformed_verdicts_payload(self):
        malformed = [
            {"_verdicts": None},
            {"_verdicts": "nonsense"},
            {"_verdicts": []},
            {"_verdicts": {"REVIEW": None}},
            {"_verdicts": {"REVIEW": "legacy bare string"}},
            {"_verdicts": {"REVIEW": {}}},
            {"_verdicts": {"REVIEW": {"recorded_at": None}}},
            {"_verdicts": {"REVIEW": {"recorded_at": 12345}}},
            {"_verdicts": {"REVIEW": ["not", "a", "dict"]}},
        ]
        meta = {"pr_number": 4242, "last_dispatched_skill": SKILL_DO_PR_REVIEW}
        for extra in malformed:
            states = _post_patch_states()
            states.update(extra)
            # Must not raise, and must answer a plain bool.
            assert _rule_patch_applied_after_review(states, meta, {}) in (True, False), (
                f"row 8b predicate misbehaved on {extra!r}"
            )


class TestNoRuleBlockIsDistinguishable:
    """The no-rule fallthrough carries the ``NO_RULE`` sentinel (#2767b) so a
    caller can tell "the table has a hole" from "a numbered guard fired"
    without parsing the reason string."""

    def _unowned_state(self) -> tuple[dict, dict]:
        # A genuine hole in the dispatch table: REVIEW is *pending* while an
        # APPROVED verdict is already recorded. Row 8e needs REVIEW completed,
        # row 10 needs it settled, and row 7 needs no verdict — so nothing owns
        # it. Found by enumerating the row predicates, not guessed.
        #
        # This deliberately no longer sets MERGE: "completed" (#2894/#2817).
        # That shape is now a Terminal, not a NO_RULE — a finished pipeline is
        # a success state, not a hole in the table. Using it here would assert
        # the exact confusion the terminal guard exists to remove.
        states = {
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "REVIEW": "pending",
            "DOCS": "completed",
            "MERGE": "pending",
            "_verdicts": {"REVIEW": {"verdict": "APPROVED", "recorded_at": _VERDICT_AT}},
        }
        meta = {
            "pr_number": 4242,
            "last_dispatched_skill": SKILL_DO_MERGE,
            # Resolved mergeability, so the separate UNKNOWN-merge-state Blocked
            # branch stays out of the way and the fallthrough is what fires.
            "pr_merge_state": "BLOCKED",
        }
        return states, meta

    def test_no_rule_block_uses_no_rule_sentinel(self):
        states, meta = self._unowned_state()
        result = decide_next_dispatch(states, meta)
        assert isinstance(result, Blocked), f"expected Blocked, got {result!r}"
        assert result.reason == "no matching dispatch rule"
        assert result.guard_id == "NO_RULE"

    def test_no_rule_block_renders_distinguishably_from_a_guard_block(self):
        states, meta = self._unowned_state()
        no_rule = decide_next_dispatch(states, meta)
        guard = decide_next_dispatch(
            {},
            {"same_stage_dispatch_count": 99, "last_dispatched_skill": SKILL_DO_PR_REVIEW},
        )
        assert isinstance(no_rule, Blocked)
        assert isinstance(guard, Blocked)
        assert no_rule.guard_id != guard.guard_id
        assert no_rule.reason != guard.reason
        assert guard.guard_id == "G4"
