"""End-to-end round trip for ``sdlc-tool verdict finalize`` / ``selfcheck`` (#2193).

Covers the "Agent Integration" section of the plan, which is the one Test
Impact checklist item explicitly deferred from the unit-test suite because
the load-bearing enforcement point (the ``/do-sdlc`` supervisor gate) is
skill-body prose, not a Python function:

    "Integration check: a scripted local `sdlc-tool verdict finalize`
    round-trip asserts verdict+trailer+marker all persist and `sdlc-tool
    verdict selfcheck` returns `ok:true`; then a deliberately-incomplete
    state asserts `selfcheck` returns `ok:false` and the supervisor refuses
    to advance."

This test drives the real orchestration functions in
``tools.sdlc_review_finalize`` (``finalize`` / ``check_review_persistence``,
the same function the CLI's ``_cli_finalize``/``_cli_selfcheck`` delegate
into -- see ``tools/sdlc_verdict.py``'s ``finalize``/``selfcheck``
subparsers) against real Redis, a real ``PipelineLedger``, and a real issue
lease -- exercising the exact code path a supervised ``/do-sdlc`` run takes,
in-process rather than via ``subprocess`` (mirrors
``tests/integration/test_sdlc_run_identity_resume.py`` and
``tests/integration/test_sdlc_session_ensure_integration.py``: real
substrate, only the live ``gh`` boundaries are stubbed).

Two live ``gh`` boundaries are monkeypatched -- both orthogonal to what this
plan changes:
  - ``tools.sdlc_review_finalize._fetch_pr_head_sha`` -- the PR's head SHA
    (this plan's own logic reads the *value*, but resolving it needs a real
    GitHub PR).
  - ``tools.sdlc_stage_marker._review_artifact_posted`` -- the WS-D
    (#2124) posted-review-artifact gate, unrelated to the #2193 trailer
    conjunct under test; a real check needs a real PR with a real posted
    review.

Everything else -- the issue lease, ``record_verdict``, the WS3c/WS-D/#2193
completion-marker gate, and the ledger read-back -- is exercised for real.
"""

from __future__ import annotations

import random
from unittest.mock import patch

import pytest

from models.agent_session import AgentSession

pytestmark = [pytest.mark.integration, pytest.mark.sdlc]

TEST_PROJECT_KEY = "test-sdlc-2193-finalize"
TEST_REPO_SLUG = "test-owner/test-repo-2193-finalize"
_HEAD_SHA = "c" * 40


def _delete_local_session(issue_number: int) -> None:
    """ORM-delete the sdlc-local-{N} session this test may have minted."""
    try:
        for s in AgentSession.query.filter(session_id=f"sdlc-local-{issue_number}"):
            s.delete()
    except Exception:
        pass
    try:
        for s in AgentSession.query.all():
            if getattr(s, "project_key", None) == TEST_PROJECT_KEY:
                s.delete()
    except Exception:
        pass


def _delete_ledger(issue_number: int) -> None:
    try:
        from agent.pipeline_ledger import PipelineLedger

        key = f"{TEST_REPO_SLUG}:{issue_number}"
        for rec in PipelineLedger.query.filter(ledger_key=key):
            rec.delete()
    except Exception:
        pass


@pytest.fixture
def issue_number():
    """A fresh, never-real issue number per run -- no pre-existing lease/ledger."""
    return 2_193_000 + random.randint(0, 999)


@pytest.fixture
def cleanup(issue_number):
    def _cleanup():
        _delete_local_session(issue_number)
        _delete_ledger(issue_number)

    _cleanup()
    yield
    _cleanup()


def _mint_run_id(issue_number, monkeypatch):
    """Establish a real issue lease + run_id via the sessionless ensure path.

    Mirrors ``tests/integration/test_sdlc_session_ensure_integration.py``'s
    ``test_new_anchor_session_created_with_is_ledger_true``: GH_REPO pins the
    target repo at rung 0 (no live ``gh repo view``), and no bridge/env
    session is present, so ``ensure_session`` mints a fresh ``sdlc-local-{N}``
    anchor and acquires the issue lock under its own run_id.
    """
    from tools.sdlc_session_ensure import ensure_session

    monkeypatch.setenv("GH_REPO", TEST_REPO_SLUG)
    monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
    monkeypatch.setattr("tools.valor_session.resolve_project_key", lambda cwd: TEST_PROJECT_KEY)
    monkeypatch.setattr(
        "tools.valor_session._resolve_project_working_directory",
        lambda project_key: ("/tmp", {}),
    )

    result = ensure_session(issue_number=issue_number)
    run_id = result.get("run_id")
    assert run_id, result
    return run_id


class TestFinalizeSelfcheckRoundTrip:
    def test_finalize_then_selfcheck_ok_true(self, monkeypatch, issue_number, cleanup):
        """Happy path: a scripted `finalize` call persists verdict + trailer +
        marker atomically, and a SEPARATE `selfcheck`-equivalent read
        (`check_review_persistence`, the exact function the CLI's `selfcheck`
        subcommand delegates into) reports `ok:true` off the real substrate."""
        from tools.sdlc_review_finalize import check_review_persistence, finalize

        run_id = _mint_run_id(issue_number, monkeypatch)
        pr_number = 918_275

        with (
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA),
            patch("tools.sdlc_stage_marker._review_artifact_posted", return_value=True),
        ):
            result = finalize(
                pr=pr_number,
                issue_number=issue_number,
                verdict="APPROVED",
                run_id=run_id,
            )
            assert result["ok"] is True, result

            # A fully independent read call -- exactly what `sdlc-tool verdict
            # selfcheck` invokes -- must agree off the real substrate, with no
            # writes of its own.
            selfcheck_result = check_review_persistence(pr=pr_number, issue_number=issue_number)

        assert selfcheck_result == {
            "ok": True,
            "verdict_present": True,
            "trailer_matches_head": True,
            "marker_completed": True,
            "reason": None,
        }

        # Real ledger read-back: the REVIEW stage really did land `completed`.
        from agent.pipeline_state import PipelineStateMachine

        sm = PipelineStateMachine.for_issue(TEST_REPO_SLUG, issue_number)
        assert sm.states.get("REVIEW") == "completed", sm.states

        from models.session_lifecycle import release_issue_lock

        release_issue_lock(issue_number, run_id)

    def test_incomplete_state_selfcheck_ok_false_supervisor_would_refuse(
        self, monkeypatch, issue_number, cleanup
    ):
        """The deliberately-incomplete state from the plan's Agent Integration
        section: a verdict recorded WITHOUT going through the atomic
        `finalize` helper (the exact #1642/#2193 desync -- a skill that wrote
        the verdict but skipped the trailer). `selfcheck` must fail closed
        with a named reason off the real substrate, never a false `ok:true`
        -- this is the signal the `/do-sdlc` supervisor gate branches on to
        halt instead of advancing past REVIEW."""
        from agent.pipeline_ledger import PipelineLedger
        from tools.sdlc_review_finalize import check_review_persistence
        from tools.sdlc_verdict import record_verdict

        run_id = _mint_run_id(issue_number, monkeypatch)
        pr_number = 918_276

        ledger = PipelineLedger.get_or_create(TEST_REPO_SLUG, issue_number)
        # A hand-run `verdict record` with NO REVIEW_CONTEXT head_sha trailer
        # -- exactly failure #2 from the incident this plan closes.
        record = record_verdict(
            ledger, stage="REVIEW", verdict="APPROVED", issue_number=issue_number
        )
        assert record, "record_verdict must persist the trailer-less verdict for this fixture"

        # No REVIEW completed marker was ever written -- the atomic finalize
        # step (which would have appended the trailer AND written the
        # marker) was skipped entirely.
        with patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA):
            selfcheck_result = check_review_persistence(pr=pr_number, issue_number=issue_number)

        assert selfcheck_result["ok"] is False
        assert selfcheck_result["verdict_present"] is True
        assert selfcheck_result["trailer_matches_head"] is False
        assert selfcheck_result["marker_completed"] is False
        assert selfcheck_result["reason"] == "REVIEW_TRAILER_MISSING"

        # And the REVIEW marker really is not `completed` in the real ledger
        # -- the supervisor's advance-past-REVIEW gate has nothing to advance
        # on; a fresh `write_marker(..., status="completed")` attempt would
        # itself be refused by the WS3c/#2193 gate (covered directly by
        # tests/unit/test_sdlc_stage_marker.py::TestReviewTrailerPresent).
        from agent.pipeline_state import PipelineStateMachine

        sm = PipelineStateMachine.for_issue(TEST_REPO_SLUG, issue_number)
        assert sm.states.get("REVIEW") != "completed", sm.states

        from models.session_lifecycle import release_issue_lock

        release_issue_lock(issue_number, run_id)

    def test_no_verdict_ever_recorded_selfcheck_ok_false(self, monkeypatch, issue_number, cleanup):
        """The other incident failure mode (#1 -- the skill wrote nothing at
        all): a fresh issue lease with NO verdict recorded at all. selfcheck
        must report ok:false / verdict_present:false off the real substrate
        (mirrors the load-bearing unit case in
        test_sdlc_review_finalize.py::TestCheckReviewPersistence, but here
        against a real, freshly-leased issue rather than a mocked resolver)."""
        from tools.sdlc_review_finalize import check_review_persistence

        run_id = _mint_run_id(issue_number, monkeypatch)
        pr_number = 918_277

        selfcheck_result = check_review_persistence(pr=pr_number, issue_number=issue_number)

        assert selfcheck_result == {
            "ok": False,
            "verdict_present": False,
            "trailer_matches_head": False,
            "marker_completed": False,
            "reason": "REVIEW_VERDICT_MISSING",
        }

        from models.session_lifecycle import release_issue_lock

        release_issue_lock(issue_number, run_id)
