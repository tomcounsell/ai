"""Unit tests for tools.sdlc_next_skill._build_context and decide().

Covers the G5 activation regression (#1639): _build_context must populate
``current_plan_hash`` when a plan file exists for the issue, otherwise G5's
loop bound on router row 2b is inert in the CLI path.

Layer 3 (#1761): _build_context now uses compute_plan_body_hash (strips
revision_applied:) instead of compute_plan_hash, so writing
``revision_applied: true`` does not bust the G5 cache.

Issue #1954: decide() peek-checks the issue-level SDLC ownership lock before
any guard evaluation -- see TestIssueLockPreCheck below.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from agent.sdlc_router import SKILL_DO_PLAN, SKILL_DO_PR_REVIEW, STATUS_COMPLETED
from tools import sdlc_next_skill


def test_build_context_sets_current_plan_hash_when_plan_exists(tmp_path, monkeypatch):
    """A real plan file for the issue → context["current_plan_hash"] is non-None."""
    plan = tmp_path / "sdlc-1639.md"
    plan.write_text("# Plan\n\nbody content\n", encoding="utf-8")

    monkeypatch.setattr(
        "tools._sdlc_utils.find_plan_path",
        lambda issue_number: plan,
    )

    context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=1639)

    assert context.get("current_plan_hash") is not None
    assert context["current_plan_hash"].startswith("sha256:")


def test_build_context_omits_hash_when_no_plan(monkeypatch):
    """No plan file for the issue → current_plan_hash key is left unset (None-safe)."""
    monkeypatch.setattr(
        "tools._sdlc_utils.find_plan_path",
        lambda issue_number: None,
    )

    context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=999999)

    assert "current_plan_hash" not in context


def test_build_context_omits_hash_when_plan_unreadable(monkeypatch):
    """find_plan_path returns a missing path → compute_plan_hash None → key unset."""
    monkeypatch.setattr(
        "tools._sdlc_utils.find_plan_path",
        lambda issue_number: Path("/nonexistent/does-not-exist-plan.md"),
    )

    context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=1639)

    assert "current_plan_hash" not in context


def test_build_context_sets_issue_number_when_plan_exists(tmp_path, monkeypatch):
    """issue_number is set in context so G5 migration can resolve plan_path (#1761)."""
    plan = tmp_path / "sdlc-1761.md"
    plan.write_text("---\nstatus: active\n---\n# Plan\n", encoding="utf-8")

    monkeypatch.setattr(
        "tools._sdlc_utils.find_plan_path",
        lambda issue_number: plan,
    )

    context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=1761)

    assert context.get("issue_number") == 1761


def test_build_context_uses_body_hash_not_full_bytes(tmp_path, monkeypatch):
    """_build_context must use compute_plan_body_hash so revision_applied: true
    does NOT change the current_plan_hash value (#1761 Layer 3)."""
    plan_before = tmp_path / "before.md"
    plan_after = tmp_path / "after.md"
    plan_before.write_text("---\nstatus: active\n---\n# Plan body\n", encoding="utf-8")
    plan_after.write_text(
        "---\nstatus: active\nrevision_applied: true\n---\n# Plan body\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "tools._sdlc_utils.find_plan_path",
        lambda issue_number: plan_before,
    )
    ctx_before = sdlc_next_skill._build_context(proposed_skill=None, issue_number=1761)

    monkeypatch.setattr(
        "tools._sdlc_utils.find_plan_path",
        lambda issue_number: plan_after,
    )
    ctx_after = sdlc_next_skill._build_context(proposed_skill=None, issue_number=1761)

    # Both hashes must be equal — the only diff is revision_applied:, which is stripped.
    assert ctx_before["current_plan_hash"] == ctx_after["current_plan_hash"]


def test_decide_warm_cache_open_pr_defers_to_pr_review_not_plan(monkeypatch):
    """CLI smoke test (#1932 fix b3): sdlc-tool next-skill's decide() must emit a
    PR-stage skill, not /do-plan, for the warm-G5-cache + open-PR +
    non-plan-family-last-dispatch state.

    Mirrors TestG5OpenPrStepAside.test_g5_defers_to_pr_review_when_pr_open in
    tests/unit/test_sdlc_router.py, but drives it through the actual CLI
    entry point (``decide()``) instead of calling ``decide_next_dispatch``
    directly, so the fix is verified on the surface the agent actually
    invokes (``sdlc-tool next-skill``). A full subprocess invocation would
    resolve live gh/session state, which is impractical in a unit test — so
    ``_resolve_enriched`` and ``_build_context`` are monkeypatched to inject
    the fixture stage_states/meta/context in-process instead.
    """
    plan_hash = "sha256:cli-smoke-b3"
    states = {
        "ISSUE": STATUS_COMPLETED,
        "PLAN": STATUS_COMPLETED,
        "CRITIQUE": STATUS_COMPLETED,
        "BUILD": STATUS_COMPLETED,
        "TEST": STATUS_COMPLETED,
        "REVIEW": "pending",
        "DOCS": "pending",
        "MERGE": "pending",
        "_verdicts": {
            "CRITIQUE": {
                "verdict": "NEEDS REVISION",
                "artifact_hash": plan_hash,
            }
        },
    }
    meta = {
        "pr_number": 6789,
        "latest_critique_verdict": "NEEDS REVISION",
        "latest_review_verdict": None,
        "last_dispatched_skill": "/do-test",  # non-plan-family
        "same_stage_dispatch_count": 0,
        "revision_applied": False,
        "plan_revising": False,
    }

    monkeypatch.setattr(
        sdlc_next_skill,
        "_resolve_enriched",
        lambda issue_number, session_id: {"stages": states, "_meta": meta},
    )
    monkeypatch.setattr(
        sdlc_next_skill,
        "_build_context",
        lambda proposed_skill, issue_number: {"current_plan_hash": plan_hash},
    )

    result = sdlc_next_skill.decide(issue_number=6789)

    assert result["dispatched"] is True
    assert result["skill"] == SKILL_DO_PR_REVIEW
    assert result["skill"] != SKILL_DO_PLAN


class TestIssueLockPreCheck:
    """Issue #1954: decide() peek-checks the issue-level SDLC ownership lock
    at the very top, BEFORE _resolve_enriched/decide_next_dispatch run.
    peek=True means check-only -- next-skill must never itself claim or
    extend the lock. decide_next_dispatch() itself (the G1-G7 guard table)
    is untouched.
    """

    def test_returns_issue_locked_blocked_shape_ahead_of_guards(self, monkeypatch):
        """A contended lock short-circuits before any guard evaluation --
        _resolve_enriched must never even be called."""
        from models.session_lifecycle import IssueLockResult

        resolve_mock = MagicMock()
        monkeypatch.setattr(sdlc_next_skill, "_resolve_enriched", resolve_mock)

        lock_result = IssueLockResult(
            acquired=False,
            owner_session_id="sdlc-local-4001-other",
            owner_run_id="foreign-run",
            orphaned_lock=False,
        )

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch(
                "models.session_lifecycle.touch_issue_lock", return_value=lock_result
            ) as lock_mock,
        ):
            result = sdlc_next_skill.decide(issue_number=4001)

        assert result == {
            "blocked": True,
            "reason": "ISSUE_LOCKED",
            "guard_id": "ISSUE_LOCK",
            "owner_run_id": "foreign-run",
            "owner_session_id": "sdlc-local-4001-other",
            "orphaned_lock": False,
        }
        resolve_mock.assert_not_called()
        lock_mock.assert_called_once()
        args, kwargs = lock_mock.call_args
        assert args[0] == 4001
        assert kwargs.get("peek") is True

    def test_peek_never_acquires_or_renews(self, monkeypatch):
        """peek=True must be passed through on every call -- next-skill is a
        read-only probe, never a claim/renewal."""
        from models.session_lifecycle import IssueLockResult

        monkeypatch.setattr(
            sdlc_next_skill,
            "_resolve_enriched",
            lambda issue_number, session_id: {"stages": {}, "_meta": {}},
        )
        monkeypatch.setattr(
            sdlc_next_skill, "_build_context", lambda proposed_skill, issue_number: {}
        )

        lock_result = IssueLockResult(acquired=True, owner_session_id=None)
        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch(
                "models.session_lifecycle.touch_issue_lock", return_value=lock_result
            ) as lock_mock,
        ):
            sdlc_next_skill.decide(issue_number=4002, session_id="sdlc-local-4002")

        # peek=True on every call; the peek identity is the issue session's
        # active_run_id read-back (None here -- no issue session exists).
        lock_mock.assert_called_once_with(4002, None, session_id="sdlc-local-4002", peek=True)

    def test_no_issue_number_skips_lock_check(self, monkeypatch):
        """No issue_number supplied -- the lock pre-check must not run at all
        (nothing to peek)."""
        monkeypatch.setattr(
            sdlc_next_skill,
            "_resolve_enriched",
            lambda issue_number, session_id: {"stages": {}, "_meta": {}},
        )
        monkeypatch.setattr(
            sdlc_next_skill, "_build_context", lambda proposed_skill, issue_number: {}
        )

        lock_mock = MagicMock()
        with patch("models.session_lifecycle.touch_issue_lock", lock_mock):
            sdlc_next_skill.decide(session_id="sdlc-local-9999")

        lock_mock.assert_not_called()

    def test_normal_guard_dispatch_unaffected_when_lock_free(self, monkeypatch):
        """When no other session holds the issue lock, decide() proceeds
        through _resolve_enriched/decide_next_dispatch exactly as before --
        normal G1-G7 guard behavior is unaffected. Exercises the REAL
        touch_issue_lock() (no mocking) against the test Redis db, confirming
        a genuinely free lock never blocks dispatch.
        """
        from agent.sdlc_router import Dispatch, decide_next_dispatch

        states = {
            "ISSUE": STATUS_COMPLETED,
            "PLAN": STATUS_COMPLETED,
            "CRITIQUE": "pending",
            "BUILD": "pending",
            "TEST": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
            "MERGE": "pending",
        }
        meta: dict = {}

        monkeypatch.setattr(
            sdlc_next_skill,
            "_resolve_enriched",
            lambda issue_number, session_id: {"stages": states, "_meta": meta},
        )
        monkeypatch.setattr(
            sdlc_next_skill, "_build_context", lambda proposed_skill, issue_number: {}
        )

        expected = decide_next_dispatch(states, meta, {})
        assert isinstance(expected, Dispatch)

        result = sdlc_next_skill.decide(issue_number=4003)

        assert result == {
            "skill": expected.skill,
            "reason": expected.reason,
            "row_id": expected.row_id,
            "dispatched": True,
        }
