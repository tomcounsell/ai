"""End-to-end merge path for a PR that never entered the SDLC pipeline (#2577).

Eleven break-glass merges in one night came from a gate with no satisfiable
path: a dependabot bump, a hand-authored fix and a review-derived follow-up all
lack a plan document, so CRITIQUE never ran, so no honest CRITIQUE verdict
exists, so the predecessor backfill behind every REVIEW and DOCS marker refused
with `verdict invariant unsatisfied`. The only ways through were to forge a
CRITIQUE verdict or to break glass.

This test drives the real substrate — a real issue lease, a real
``PipelineLedger``, the real ``write_marker`` / ``finalize`` /
``evaluate_merge_predicate`` code paths — and asserts the four properties the
fix has to hold simultaneously:

1. A hand-authored / review-derived PR with a real review reaches
   ``allowed: true`` through the sanctioned path.
2. A dependabot PR does too, on the same path with no exemption.
3. A PR with NO review still cannot reach it.
4. No call sequence produces a ``completed`` REVIEW marker without a genuine
   recorded verdict.

Only the live GitHub boundaries are stubbed — PR state, head SHA, and the
posted-review-artifact probe all need a real PR on github.com. Everything that
this fix touches (lease, ledger, skip preconditions, backfill, verdict
invariant, predicate groups b/c/d) runs for real.
"""

from __future__ import annotations

import itertools
import os
import uuid
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.sdlc]

TEST_REPO_SLUG = "test-owner/test-repo-2577"
_HEAD_SHA = "d" * 40

# Per-process issue-number sequence. Random draws collide across a 10-test run
# often enough to matter, and a collision hands one test another's lease.
_ISSUE_SEQ = itertools.count(2_577_000 + (os.getpid() % 1000) * 100)


def _delete_ledger(issue_number: int) -> None:
    try:
        from agent.pipeline_ledger import PipelineLedger

        for rec in PipelineLedger.query.filter(ledger_key=f"{TEST_REPO_SLUG}:{issue_number}"):
            rec.delete()
    except Exception:
        pass


@pytest.fixture
def issue_number():
    """A fresh, never-real issue number per test — no pre-existing lease/ledger."""
    return next(_ISSUE_SEQ)


@pytest.fixture
def cleanup(issue_number):
    def _cleanup():
        _delete_ledger(issue_number)

    _cleanup()
    yield
    _cleanup()


def _mint_run_id(issue_number, monkeypatch):
    """Acquire the REAL per-issue lease directly, the way session-ensure does.

    ``ensure_session`` is not exercised here on purpose: it mints an
    ``AgentSession`` anchor and scans ``AgentSession.query.all()``, neither of
    which this fix touches, and both of which make the test slow and sensitive
    to whatever else is in the shared Redis. ``touch_issue_lock`` is the actual
    authorization primitive every write path below revalidates against.
    """
    monkeypatch.setenv("GH_REPO", TEST_REPO_SLUG)
    run_id = uuid.uuid4().hex
    _hold_lease(issue_number, run_id)
    return run_id


def _hold_lease(issue_number: int, run_id: str) -> None:
    """(Re)assert the lease for ``run_id`` and confirm it is the live owner.

    Called immediately before every state-mutating step. A real long-running
    SDLC run renews its lease continuously; here it also insulates the test
    from this machine's shared Redis, where a concurrent agent's suite can drop
    the keyspace mid-run.
    """
    from models.session_lifecycle import ISSUE_LOCK_TTL_SECONDS, touch_issue_lock

    touch_issue_lock(
        issue_number,
        run_id,
        session_id=f"test-2577-{issue_number}",
        ttl=ISSUE_LOCK_TTL_SECONDS,
        target_repo=TEST_REPO_SLUG,
    )
    peek = touch_issue_lock(issue_number, run_id, peek=True)
    assert peek.owner_run_id == run_id, peek


def _retry_if_lease_evaporated(call, issue_number, run_id, attempts=3):
    """Run ``call()``, re-acquiring the lease and retrying on LEASE_ABSENT.

    Lease ownership is not what any test here asserts —
    ``test_skip_requires_the_issue_lease`` calls ``write_marker`` directly for
    that — so a lease that vanishes underneath a step because a concurrent
    suite dropped the shared Redis keyspace is noise, not signal. Every other
    refusal reason propagates untouched on the first attempt.
    """
    for attempt in range(attempts):
        _hold_lease(issue_number, run_id)
        result, code = call()
        if code == 0 or result.get("reason") not in ("LEASE_ABSENT", "ISSUE_LOCKED"):
            return result, code
        if attempt == attempts - 1:
            return result, code
    raise AssertionError("unreachable")


def _marker(stage, status, issue_number, run_id):
    """``write_marker`` with the lease freshly asserted."""
    from tools.sdlc_stage_marker import write_marker

    return _retry_if_lease_evaporated(
        lambda: write_marker(stage=stage, status=status, issue_number=issue_number, run_id=run_id),
        issue_number,
        run_id,
    )


def _finalize(pr, issue_number, run_id, verdict="APPROVED"):
    """``sdlc-tool verdict finalize``'s function, with the same lease retry."""
    from tools.sdlc_review_finalize import ReviewFinalizeError, finalize

    def _call():
        try:
            return finalize(pr=pr, issue_number=issue_number, verdict=verdict, run_id=run_id), 0
        except ReviewFinalizeError as e:
            reason = str(e).split(":", 1)[0].strip()
            return {"reason": reason, "message": str(e)}, 1

    return _retry_if_lease_evaporated(_call, issue_number, run_id)


def _record_skips(issue_number, run_id):
    """The sanctioned two calls: record PLAN/CRITIQUE as never-dispatched."""
    return [_marker(stage, "skipped", issue_number, run_id) for stage in ("PLAN", "CRITIQUE")]


def _gh_seams(pr_number: int, *, body: str):
    """Stub the three live-GitHub boundaries the predicate and marker consult."""
    pr_json = {
        "state": "OPEN",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [{"name": "ci", "conclusion": "SUCCESS"}],
        "reviewDecision": "APPROVED",
        "body": body,
        "headRefName": "dependabot/uv/uv-374350d79f",
    }
    return (
        patch("tools.merge_predicate._gh_pr_view", return_value=pr_json),
        patch("tools.merge_predicate._gh_repo_name_with_owner", return_value=TEST_REPO_SLUG),
        patch(
            "tools.merge_predicate._gh_latest_commit",
            return_value={"sha": _HEAD_SHA, "date": "2026-08-06T00:00:00Z"},
        ),
    )


def _in_process_substrate():
    """Run groups (b)/(c)'s substrate reads in-process instead of via subprocess.

    ``_run_stage_query``/``_run_verdict_get`` shell out to ``sdlc-tool``, which
    resolves ``AI_REPO_ROOT`` and connects to Redis from a fresh interpreter —
    a different DB from the one the popoto pytest plugin scopes this process to.
    These stubs call the exact functions the CLI subcommands delegate into, so
    the predicate's own logic (marker authority, verdict freshness, trailer
    matching) is still evaluated for real against the real ledger.
    """
    from agent.pipeline_ledger import PipelineLedger
    from agent.pipeline_state import PipelineStateMachine
    from tools.sdlc_verdict import get_verdict

    def stage_query(issue_number, repo_root):
        del repo_root
        sm = PipelineStateMachine.for_issue(TEST_REPO_SLUG, issue_number)
        return {"stages": dict(sm.states)}

    def verdict_get(issue_number, repo_root):
        del repo_root
        ledger = PipelineLedger.get(TEST_REPO_SLUG, issue_number)
        return dict(get_verdict(ledger, "REVIEW")) if ledger is not None else {}

    return (
        patch("tools.merge_predicate._run_stage_query", side_effect=stage_query),
        patch("tools.merge_predicate._run_verdict_get", side_effect=verdict_get),
    )


def _evaluate(pr_number: int, issue_number: int, run_id: str | None, body: str):
    from pathlib import Path

    from tools.merge_predicate import evaluate_merge_predicate

    view, repo, commit = _gh_seams(pr_number, body=body)
    stage_query, verdict_get = _in_process_substrate()
    with view, repo, commit, stage_query, verdict_get:
        return evaluate_merge_predicate(
            pr_number,
            repo_root=Path(__file__).resolve().parents[2],
            run_id=run_id,
        )


class TestOffPipelinePRReachesTheGate:
    """Requirements 1 and 2: the sanctioned path is satisfiable."""

    @pytest.mark.parametrize(
        "shape,pr_number",
        [("hand-authored / review-derived", 925_770), ("dependabot", 925_771)],
    )
    def test_pr_with_no_plan_reaches_allowed_true(
        self, monkeypatch, issue_number, cleanup, shape, pr_number
    ):
        """Both shapes take the identical route — there is no exemption path.

        A dependabot PR and a hand-authored one differ only in who wrote the
        diff; neither has a plan, both get a real review, both merge through
        the same four predicate groups.
        """
        from agent.pipeline_state import PipelineStateMachine

        run_id = _mint_run_id(issue_number, monkeypatch)
        body = f"Bump uv. Closes #{issue_number}"

        # THE ACCEPTANCE CASE: session-ensure, then finalize. Nothing in between.
        # This is what an agent actually types when it finishes reviewing a bug
        # fix filed while verifying main, and on main today it returns
        # STATE_MACHINE_REJECTED.
        with (
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA),
            patch("tools.sdlc_stage_marker._review_artifact_posted", return_value=True),
        ):
            reviewed, code = _finalize(pr_number, issue_number, run_id)
        assert code == 0, reviewed
        assert reviewed["ok"]

        # The planning stages the pipeline never dispatched are recorded as
        # skipped, with their justification — not force-completed, not forged.
        sm = PipelineStateMachine.for_issue(TEST_REPO_SLUG, issue_number)
        assert sm.states["PLAN"] == "skipped"
        assert sm.states["CRITIQUE"] == "skipped"
        assert sm.states["REVIEW"] == "completed"
        assert "never dispatched" in sm.stage_skips["CRITIQUE"]["reason"]

        # DOCS completion — the marker group (b) reads — now backfills cleanly.
        result, code = _marker("DOCS", "completed", issue_number, run_id)
        assert code == 0, result

        verdict = _evaluate(pr_number, issue_number, run_id, body)
        assert verdict.allowed is True, f"{shape}: {verdict.failed_checks}"

        from models.session_lifecycle import release_issue_lock

        release_issue_lock(issue_number, run_id)

    def test_explicit_skip_up_front_reaches_the_same_state(
        self, monkeypatch, issue_number, cleanup
    ):
        """The disposition is also recordable deliberately, before REVIEW runs.

        Same verified predicate, same resulting ledger — the auto-path heals at
        the point of refusal, this one states the fact up front.
        """
        from agent.pipeline_state import PipelineStateMachine

        run_id = _mint_run_id(issue_number, monkeypatch)
        for result, code in _record_skips(issue_number, run_id):
            assert code == 0, result
            assert result["status"] == "skipped"

        with (
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA),
            patch("tools.sdlc_stage_marker._review_artifact_posted", return_value=True),
        ):
            reviewed, code = _finalize(925_773, issue_number, run_id)
        assert code == 0, reviewed

        result, code = _marker("DOCS", "completed", issue_number, run_id)
        assert code == 0, result

        sm = PipelineStateMachine.for_issue(TEST_REPO_SLUG, issue_number)
        assert sm.states["PLAN"] == "skipped"
        assert sm.states["CRITIQUE"] == "skipped"
        assert sm.states["DOCS"] == "completed"
        assert "no plan document" in sm.stage_skips["CRITIQUE"]["reason"]

        from models.session_lifecycle import release_issue_lock

        release_issue_lock(issue_number, run_id)


class TestNoReviewStillCannotMerge:
    """Requirement 3: skipping PLAN/CRITIQUE weakens nothing about REVIEW."""

    def test_docs_marker_refused_without_a_review_verdict(self, monkeypatch, issue_number, cleanup):

        run_id = _mint_run_id(issue_number, monkeypatch)
        for _result, code in _record_skips(issue_number, run_id):
            assert code == 0

        # No review has happened. The DOCS marker's backfill walks REVIEW.
        result, code = _marker("DOCS", "completed", issue_number, run_id)
        assert code == 1
        assert result["reason"] == "STATE_MACHINE_REJECTED"

        verdict = _evaluate(925_772, issue_number, run_id, f"Closes #{issue_number}")
        assert verdict.allowed is False
        legs = " | ".join(verdict.failed_checks)
        assert "DOCS" in legs
        assert "no recorded REVIEW verdict" in legs

        from models.session_lifecycle import release_issue_lock

        release_issue_lock(issue_number, run_id)


class TestNoPostedReviewArtifactStillFails:
    """The posted-artifact leg is not replaced by any of this — it is preserved.

    ``_review_artifact_posted`` (WS-D, #2124) is what catches a run that exited
    with its judge subagents still in flight: a recorded verdict but no
    ``## Review:`` comment and no formal GitHub review. The auto-skip clears the
    planning stages off REVIEW's spine and nothing else, so this leg is reached
    now where it previously sat behind the CRITIQUE refusal.
    """

    def test_finalize_refuses_when_no_review_artifact_exists(
        self, monkeypatch, issue_number, cleanup
    ):
        from agent.pipeline_state import PipelineStateMachine

        run_id = _mint_run_id(issue_number, monkeypatch)

        with (
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA),
            patch("tools.sdlc_stage_marker._review_artifact_posted", return_value=False),
        ):
            result, code = _finalize(925_774, issue_number, run_id)

        assert code == 1
        assert result["reason"] == "REVIEW_ARTIFACT_MISSING", result

        # REVIEW is not completed, and the merge gate is unreachable.
        sm = PipelineStateMachine.for_issue(TEST_REPO_SLUG, issue_number)
        assert sm.states["REVIEW"] != "completed"

        docs_result, docs_code = _marker("DOCS", "completed", issue_number, run_id)
        assert docs_code == 1
        assert docs_result["reason"] == "STATE_MACHINE_REJECTED"

        verdict = _evaluate(925_774, issue_number, run_id, f"Closes #{issue_number}")
        assert verdict.allowed is False
        assert "DOCS" in " | ".join(verdict.failed_checks)

        from models.session_lifecycle import release_issue_lock

        release_issue_lock(issue_number, run_id)


class TestCannotForgeAnApproval:
    """Requirement 4: no call sequence mints a REVIEW completion with no verdict."""

    def test_review_cannot_be_skipped(self, monkeypatch, issue_number, cleanup):

        run_id = _mint_run_id(issue_number, monkeypatch)
        for stage in ("REVIEW", "DOCS", "MERGE"):
            result, code = _marker(stage, "skipped", issue_number, run_id)
            assert code == 1, (stage, result)
            assert result["reason"] == "STAGE_NOT_SKIPPABLE"

        from agent.pipeline_state import PipelineStateMachine

        sm = PipelineStateMachine.for_issue(TEST_REPO_SLUG, issue_number)
        assert sm.states["REVIEW"] == "pending"

        from models.session_lifecycle import release_issue_lock

        release_issue_lock(issue_number, run_id)

    def test_review_completion_still_requires_a_verdict_after_skips(
        self, monkeypatch, issue_number, cleanup
    ):
        """The direct route: mark REVIEW completed with the spine cleared."""

        run_id = _mint_run_id(issue_number, monkeypatch)
        for _r, code in _record_skips(issue_number, run_id):
            assert code == 0

        result, code = _marker("REVIEW", "completed", issue_number, run_id)
        assert code == 1
        assert result["reason"] == "REVIEW_VERDICT_MISSING"

        from models.session_lifecycle import release_issue_lock

        release_issue_lock(issue_number, run_id)

    def test_review_in_progress_does_not_bypass_the_verdict_gates(
        self, monkeypatch, issue_number, cleanup
    ):
        """The sneakiest route: put REVIEW `in_progress` first so the completion
        path's predecessor backfill is skipped entirely. The three REVIEW gates
        run before that branch, so the refusal is unchanged."""

        run_id = _mint_run_id(issue_number, monkeypatch)
        for _r, code in _record_skips(issue_number, run_id):
            assert code == 0

        result, code = _marker("REVIEW", "in_progress", issue_number, run_id)
        assert code == 0, result

        result, code = _marker("REVIEW", "completed", issue_number, run_id)
        assert code == 1
        assert result["reason"] == "REVIEW_VERDICT_MISSING"

        from models.session_lifecycle import release_issue_lock

        release_issue_lock(issue_number, run_id)

    def test_merge_marker_backfill_still_walks_review(self, monkeypatch, issue_number, cleanup):
        """Reaching further down the spine does not route around REVIEW either."""

        run_id = _mint_run_id(issue_number, monkeypatch)
        for _r, code in _record_skips(issue_number, run_id):
            assert code == 0

        result, code = _marker("MERGE", "completed", issue_number, run_id)
        assert code == 1
        assert result["reason"] == "STATE_MACHINE_REJECTED"

        from agent.pipeline_state import PipelineStateMachine

        sm = PipelineStateMachine.for_issue(TEST_REPO_SLUG, issue_number)
        assert sm.states["REVIEW"] == "pending"
        assert sm.states["MERGE"] == "pending"

        from models.session_lifecycle import release_issue_lock

        release_issue_lock(issue_number, run_id)

    def test_skipping_critique_after_a_real_critique_is_refused(
        self, monkeypatch, issue_number, cleanup
    ):
        """A CRITIQUE that produced a verdict is never retroactively skippable."""
        from agent.pipeline_ledger import PipelineLedger
        from tools.sdlc_verdict import record_verdict

        run_id = _mint_run_id(issue_number, monkeypatch)
        ledger = PipelineLedger.get_or_create(TEST_REPO_SLUG, issue_number)
        assert record_verdict(
            ledger, stage="CRITIQUE", verdict="READY TO BUILD", issue_number=issue_number
        )
        _hold_lease(issue_number, run_id)

        result, code = _marker("CRITIQUE", "skipped", issue_number, run_id)
        assert code == 1
        assert result["reason"] == "STAGE_RAN_NOT_SKIPPABLE"

        from models.session_lifecycle import release_issue_lock

        release_issue_lock(issue_number, run_id)

    def test_skipping_critique_with_a_plan_present_is_refused(
        self, monkeypatch, issue_number, cleanup, tmp_path
    ):
        """The precondition is derived from the repo, not claimed by the caller."""

        run_id = _mint_run_id(issue_number, monkeypatch)
        plan = tmp_path / "some-plan.md"
        plan.write_text(f"tracking: #{issue_number}\n")

        with patch("tools._sdlc_utils.find_plan_path", return_value=plan):
            result, code = _marker("CRITIQUE", "skipped", issue_number, run_id)
        assert code == 1
        assert result["reason"] == "PLAN_EXISTS_NOT_SKIPPABLE"

        from models.session_lifecycle import release_issue_lock

        release_issue_lock(issue_number, run_id)

    def test_skip_requires_the_issue_lease(self, monkeypatch, issue_number, cleanup):
        """A run that does not hold the lease cannot record a skip."""

        from tools.sdlc_stage_marker import write_marker

        run_id = _mint_run_id(issue_number, monkeypatch)
        result, code = write_marker(
            stage="CRITIQUE",
            status="skipped",
            issue_number=issue_number,
            run_id="forged-run-id",
        )
        assert code == 1
        assert result["reason"] in ("ISSUE_LOCKED", "LEASE_ABSENT")

        from models.session_lifecycle import release_issue_lock

        release_issue_lock(issue_number, run_id)
