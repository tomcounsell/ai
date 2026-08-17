"""Unit tests for tools.sdlc_next_skill._build_context and decide().

Covers the G5 activation regression (#1639): _build_context must populate
``current_plan_hash`` when a plan file exists for the issue, otherwise G5's
loop bound on router row 2b is inert in the CLI path.

Layer 3 (#1761): _build_context now uses compute_plan_body_hash (strips
revision_applied:) instead of compute_plan_hash, so writing
``revision_applied: true`` does not bust the G5 cache.

Issue #1954: decide() peek-checks the issue-level SDLC ownership lock before
any guard evaluation -- see TestIssueLockPreCheck below.

Issue #1267: _build_context now also runs the stage-advance artifact
verification gate (see TestStageArtifactVerification below) -- deterministic
live-world checks on the top-3 claimed side-effects (PR opened, branch
pushed, plan committed on main), reusing #2003's live-ref helper pattern.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.sdlc_router import SKILL_DO_PLAN, SKILL_DO_PR_REVIEW, STATUS_COMPLETED
from tools import sdlc_next_skill


def test_build_context_sets_current_plan_hash_when_plan_exists(tmp_path, monkeypatch):
    """A real plan file for the issue → context["current_plan_hash"] is non-None."""
    plan = tmp_path / "sdlc-1639.md"
    plan.write_text("# Plan\n\nbody content\n", encoding="utf-8")

    monkeypatch.setattr(
        "tools.lane_identity.find_plan_path",
        lambda issue_number: plan,
    )

    context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=1639)

    assert context.get("current_plan_hash") is not None
    assert context["current_plan_hash"].startswith("sha256:")


def test_build_context_omits_hash_when_no_plan(monkeypatch):
    """No plan file for the issue → current_plan_hash key is left unset (None-safe)."""
    monkeypatch.setattr(
        "tools.lane_identity.find_plan_path",
        lambda issue_number: None,
    )

    context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=999999)

    assert "current_plan_hash" not in context


def test_build_context_omits_hash_when_plan_unreadable(monkeypatch):
    """find_plan_path returns a missing path → compute_plan_hash None → key unset."""
    monkeypatch.setattr(
        "tools.lane_identity.find_plan_path",
        lambda issue_number: Path("/nonexistent/does-not-exist-plan.md"),
    )

    context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=1639)

    assert "current_plan_hash" not in context


def test_build_context_sets_issue_number_when_plan_exists(tmp_path, monkeypatch):
    """issue_number is set in context so G5 migration can resolve plan_path (#1761)."""
    plan = tmp_path / "sdlc-1761.md"
    plan.write_text("---\nstatus: active\n---\n# Plan\n", encoding="utf-8")

    monkeypatch.setattr(
        "tools.lane_identity.find_plan_path",
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
        "tools.lane_identity.find_plan_path",
        lambda issue_number: plan_before,
    )
    ctx_before = sdlc_next_skill._build_context(proposed_skill=None, issue_number=1761)

    monkeypatch.setattr(
        "tools.lane_identity.find_plan_path",
        lambda issue_number: plan_after,
    )
    ctx_after = sdlc_next_skill._build_context(proposed_skill=None, issue_number=1761)

    # Both hashes must be equal — the only diff is revision_applied:, which is stripped.
    assert ctx_before["current_plan_hash"] == ctx_after["current_plan_hash"]


class TestBranchExistsCanonicalShape:
    """branch_exists must probe the lane's RECORDED slug (#2003, #2718).

    The lane's identity is minted once at lane start and recorded on the
    ledger. Both the issue-derived shape and human-named shapes exist on this
    remote, so the branch name is READ, never derived from a plan filename --
    deriving it is what probed a branch that never existed and wedged #2663.
    With no recorded slug, existence cannot be affirmed and branch_exists must
    be False without probing anything.
    """

    @staticmethod
    def _fake_git(stdout: str):
        def _run(cmd, **kwargs):
            proc = MagicMock()
            if cmd[:2] == ["git", "branch"]:
                proc.returncode = 0
                proc.stdout = stdout
            else:
                proc.returncode = 1
                proc.stdout = ""
            return proc

        return _run

    def test_true_when_recorded_slug_branch_exists(self, monkeypatch):
        monkeypatch.setattr(
            "tools.lane_identity.resolve_lane_slug", lambda *a, **k: "my-feature-slug"
        )
        monkeypatch.setattr(
            "subprocess.run",
            self._fake_git("  main\n  session/my-feature-slug\n"),
        )

        context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=2003)

        assert context["branch_exists"] is True

    def test_true_when_issue_derived_recorded_slug_branch_exists(self, monkeypatch):
        """The issue-derived shape is REAL and counts when it is recorded.

        This case inverted: it used to assert False on the belief that this
        repo never creates such a branch. Ninety-nine of them exist on origin,
        and the lane that owns one records it like any other name.
        """
        monkeypatch.setattr("tools.lane_identity.resolve_lane_slug", lambda *a, **k: "sdlc-2003")
        monkeypatch.setattr(
            "subprocess.run",
            self._fake_git("  main\n  session/sdlc-2003\n"),
        )

        context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=2003)

        assert context["branch_exists"] is True

    def test_false_and_silent_when_no_slug_recorded(self, monkeypatch):
        """No recorded slug -> cannot affirm existence -> False, and no probe."""
        monkeypatch.setattr("tools.lane_identity.resolve_lane_slug", lambda *a, **k: None)
        run_mock = MagicMock()
        monkeypatch.setattr("subprocess.run", run_mock)

        context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=2003)

        assert context["branch_exists"] is False
        assert not any(
            call.args and call.args[0][:2] == ["git", "branch"] for call in run_mock.call_args_list
        )

    def test_false_when_branch_absent(self, monkeypatch):
        monkeypatch.setattr(
            "tools.lane_identity.resolve_lane_slug", lambda *a, **k: "my-feature-slug"
        )
        monkeypatch.setattr("subprocess.run", self._fake_git("  main\n"))

        context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=2003)

        assert context["branch_exists"] is False


class TestTargetRepoCwd:
    """Live git checks must run against SDLC_TARGET_REPO, not the process cwd (#2078).

    The local /do-sdlc wrapper pins the process cwd to the ai repo via
    ``uv run --directory``, so a bare ``subprocess.run(["git", ...])`` in the
    stage-artifact verifier inspects the wrong repo for non-ai targets: a
    genuinely-committed plan reads as unverified and G8 re-dispatches
    /do-plan forever. These tests build a real git fixture repo, force the
    process cwd elsewhere, and assert the checks follow SDLC_TARGET_REPO.
    """

    @staticmethod
    def _init_fixture_repo(root: Path, slug: str) -> None:
        """git repo at *root* with docs/plans/{slug}.md committed on main."""
        env_git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
        plan = root / "docs" / "plans" / f"{slug}.md"
        plan.parent.mkdir(parents=True)
        plan.write_text("# Plan\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
        subprocess.run(
            [*env_git, "-C", str(root), "commit", "-m", "plan"],
            check=True,
            capture_output=True,
        )

    def test_target_repo_cwd_none_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("SDLC_TARGET_REPO", raising=False)
        assert sdlc_next_skill._target_repo_cwd() is None

    def test_target_repo_cwd_none_when_env_empty(self, monkeypatch):
        monkeypatch.setenv("SDLC_TARGET_REPO", "")
        assert sdlc_next_skill._target_repo_cwd() is None

    def test_plan_committed_check_follows_target_repo(self, tmp_path, monkeypatch):
        """Plan committed on the TARGET's main verifies even when cwd is a non-repo."""
        target = tmp_path / "target"
        target.mkdir()
        self._init_fixture_repo(target, "sdlc-2078-fixture")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        monkeypatch.setenv("SDLC_TARGET_REPO", str(target))

        assert (
            sdlc_next_skill._check_plan_committed_on_main("docs/plans/sdlc-2078-fixture.md") is True
        )

    def test_plan_path_relativizes_against_the_repo_root_not_cwd(self, tmp_path, monkeypatch):
        """G8 must not read a committed plan as unverified from a subdirectory.

        `git show main:<path>` paths are always repo-root-relative whatever cwd
        the subprocess runs in, and `find_plan_path` resolves against the repo
        root too. Rebasing on the process cwd makes the two disagree from any
        subdirectory, producing a false negative that routes a healthy lane back
        to /do-plan -- the #2718 symptom through a different door.
        """
        target = tmp_path / "target"
        target.mkdir()
        self._init_fixture_repo(target, "sdlc-2078-fixture")
        monkeypatch.delenv("SDLC_TARGET_REPO", raising=False)
        # cwd is a SUBDIRECTORY of the repo, not its root.
        monkeypatch.chdir(target / "docs")

        stage_states = {"PLAN": "completed"}
        with patch.object(
            sdlc_next_skill,
            "_check_plan_committed_on_main",
            return_value=True,
        ) as checked:
            with patch(
                "tools.lane_identity.find_plan_path",
                return_value=target / "docs" / "plans" / "sdlc-2078-fixture.md",
            ):
                with patch("tools.lane_identity.resolve_lane_slug", return_value=None):
                    sdlc_next_skill._verify_stage_artifacts_live(stage_states, {}, 2078)

        assert checked.call_args is not None, "the PLAN-committed check never ran"
        assert checked.call_args[0][0] == "docs/plans/sdlc-2078-fixture.md", (
            f"plan path relativized against cwd, not the repo root: {checked.call_args[0][0]!r}"
        )

    def test_plan_committed_check_false_when_plan_absent_in_target(self, tmp_path, monkeypatch):
        target = tmp_path / "target"
        target.mkdir()
        self._init_fixture_repo(target, "sdlc-2078-fixture")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("SDLC_TARGET_REPO", str(target))

        assert sdlc_next_skill._check_plan_committed_on_main("docs/plans/no-such-slug.md") is False

    def test_branch_exists_probe_follows_target_repo(self, tmp_path, monkeypatch):
        """_build_context's branch_exists probe reads the target's branches."""
        target = tmp_path / "target"
        target.mkdir()
        self._init_fixture_repo(target, "sdlc-2078-fixture")
        subprocess.run(
            ["git", "-C", str(target), "branch", "session/sdlc-2078-fixture"],
            check=True,
            capture_output=True,
        )
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        monkeypatch.setenv("SDLC_TARGET_REPO", str(target))
        monkeypatch.setattr(
            "tools.lane_identity.resolve_lane_slug", lambda *a, **k: "sdlc-2078-fixture"
        )

        context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=2078)

        assert context["branch_exists"] is True


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
        lambda proposed_skill, issue_number, stage_states=None, meta=None: {
            "current_plan_hash": plan_hash
        },
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
            "peek_identity": "unresolved",
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
            sdlc_next_skill,
            "_build_context",
            lambda proposed_skill, issue_number, stage_states=None, meta=None: {},
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
            sdlc_next_skill,
            "_build_context",
            lambda proposed_skill, issue_number, stage_states=None, meta=None: {},
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
            sdlc_next_skill,
            "_build_context",
            lambda proposed_skill, issue_number, stage_states=None, meta=None: {},
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


class TestSelfLockPeekIdentity:
    """Issue #2766: a caller-stated ``--run-id`` peeks the issue lock under
    its own identity directly, never through ``find_session_by_issue`` --
    so a supervisor can never be told to stand down for a lock it holds
    itself, regardless of why the session lookup misses it.

    Unit-level cases here use mocks for the branch matrix (terminal status,
    non-eng session_type, lookup-raises); the demonstrated-red proof lives in
    ``tests/integration/test_sdlc_session_ensure_integration.py::TestSelfLockPeekIdentityEndToEnd``
    against a REAL session-ensure + REAL session record, per the plan's
    Success Criteria (a red produced only by patching
    ``find_session_by_issue`` proves the mock, not the bug).
    """

    def test_caller_run_id_bypasses_terminal_session_lookup_miss(self, monkeypatch):
        """The lock is held under the caller's own run_id; find_session_by_issue
        would return None (terminal status excluded it) but is never even
        consulted because --run-id was supplied."""
        from models.session_lifecycle import IssueLockResult

        monkeypatch.setattr(
            sdlc_next_skill,
            "_resolve_enriched",
            lambda issue_number, session_id: {"stages": {}, "_meta": {}},
        )
        monkeypatch.setattr(
            sdlc_next_skill,
            "_build_context",
            lambda proposed_skill, issue_number, stage_states=None, meta=None: {},
        )

        lookup_mock = MagicMock(return_value=None)  # simulates terminal-status exclusion
        lock_result = IssueLockResult(acquired=True, owner_session_id=None, owner_run_id="own-run")
        with (
            patch("tools._sdlc_utils.find_session_by_issue", lookup_mock),
            patch(
                "models.session_lifecycle.touch_issue_lock", return_value=lock_result
            ) as lock_mock,
            patch.object(
                sdlc_next_skill, "_recover_stage_states_from_durable_signals", return_value={}
            ),
        ):
            sdlc_next_skill.decide(issue_number=5001, run_id="own-run")

        lookup_mock.assert_not_called()
        lock_mock.assert_called_once_with(5001, "own-run", session_id="", peek=True)

    def test_caller_run_id_bypasses_non_eng_session_lookup_miss(self, monkeypatch):
        """Same shape, different find_session_by_issue exclusion axis
        (non-eng session_type) -- still never consulted."""
        from models.session_lifecycle import IssueLockResult

        monkeypatch.setattr(
            sdlc_next_skill,
            "_resolve_enriched",
            lambda issue_number, session_id: {"stages": {}, "_meta": {}},
        )
        monkeypatch.setattr(
            sdlc_next_skill,
            "_build_context",
            lambda proposed_skill, issue_number, stage_states=None, meta=None: {},
        )

        lookup_mock = MagicMock(return_value=None)  # simulates non-eng exclusion
        lock_result = IssueLockResult(acquired=True, owner_session_id=None, owner_run_id="own-run")
        with (
            patch("tools._sdlc_utils.find_session_by_issue", lookup_mock),
            patch(
                "models.session_lifecycle.touch_issue_lock", return_value=lock_result
            ) as lock_mock,
            patch.object(
                sdlc_next_skill, "_recover_stage_states_from_durable_signals", return_value={}
            ),
        ):
            sdlc_next_skill.decide(issue_number=5002, run_id="own-run")

        lookup_mock.assert_not_called()
        lock_mock.assert_called_once_with(5002, "own-run", session_id="", peek=True)

    def test_caller_run_id_bypasses_lookup_raising(self, monkeypatch):
        """find_session_by_issue raising must not matter either -- it is
        never called when --run-id is supplied."""
        from models.session_lifecycle import IssueLockResult

        monkeypatch.setattr(
            sdlc_next_skill,
            "_resolve_enriched",
            lambda issue_number, session_id: {"stages": {}, "_meta": {}},
        )
        monkeypatch.setattr(
            sdlc_next_skill,
            "_build_context",
            lambda proposed_skill, issue_number, stage_states=None, meta=None: {},
        )

        lookup_mock = MagicMock(side_effect=RuntimeError("boom"))
        lock_result = IssueLockResult(acquired=True, owner_session_id=None, owner_run_id="own-run")
        with (
            patch("tools._sdlc_utils.find_session_by_issue", lookup_mock),
            patch(
                "models.session_lifecycle.touch_issue_lock", return_value=lock_result
            ) as lock_mock,
            patch.object(
                sdlc_next_skill, "_recover_stage_states_from_durable_signals", return_value={}
            ),
        ):
            result = sdlc_next_skill.decide(issue_number=5003, run_id="own-run")

        lookup_mock.assert_not_called()
        lock_mock.assert_called_once_with(5003, "own-run", session_id="", peek=True)
        assert "error" not in result

    def test_no_run_id_still_hits_lookup_and_logs_unresolved(self, monkeypatch, caplog):
        """No --run-id supplied: unchanged inference path runs, and a raising
        lookup produces peek_identity == 'unresolved' with a debug log, not a
        silent swallow."""
        monkeypatch.setattr(
            sdlc_next_skill,
            "_resolve_enriched",
            lambda issue_number, session_id: {"stages": {}, "_meta": {}},
        )
        monkeypatch.setattr(
            sdlc_next_skill,
            "_build_context",
            lambda proposed_skill, issue_number, stage_states=None, meta=None: {},
        )
        from models.session_lifecycle import IssueLockResult

        lookup_mock = MagicMock(side_effect=RuntimeError("boom"))
        lock_result = IssueLockResult(
            acquired=False, owner_session_id="other", owner_run_id="foreign-run"
        )
        with (
            caplog.at_level(logging.DEBUG, logger="tools.sdlc_next_skill"),
            patch("tools._sdlc_utils.find_session_by_issue", lookup_mock),
            patch("models.session_lifecycle.touch_issue_lock", return_value=lock_result),
        ):
            result = sdlc_next_skill.decide(issue_number=5004)

        lookup_mock.assert_called_once()
        assert result["blocked"] is True
        assert result["peek_identity"] == "unresolved"

    def test_foreign_run_id_against_live_lock_still_blocks(self, monkeypatch):
        """Anti-regression: a supplied --run-id that does NOT match the live
        lock owner must still block. The flag changes only which identity is
        compared, never whether the comparison happens -- it is not a
        bypass."""
        from models.session_lifecycle import IssueLockResult

        resolve_mock = MagicMock()
        monkeypatch.setattr(sdlc_next_skill, "_resolve_enriched", resolve_mock)

        lock_result = IssueLockResult(
            acquired=False,
            owner_session_id="other-session",
            owner_run_id="rival-run",
            orphaned_lock=False,
        )
        with patch(
            "models.session_lifecycle.touch_issue_lock", return_value=lock_result
        ) as lock_mock:
            result = sdlc_next_skill.decide(issue_number=5005, run_id="my-run")

        resolve_mock.assert_not_called()
        lock_mock.assert_called_once_with(5005, "my-run", session_id="", peek=True)
        assert result["blocked"] is True
        assert result["reason"] == "ISSUE_LOCKED"
        assert result["owner_run_id"] == "rival-run"
        assert result["peek_identity"] == "caller"

    def test_stale_self_run_id_surfaces_session_mirror_diagnostic(self, monkeypatch):
        """Race 4: caller asserts run_id X, the live lock is held by Y, and
        the session mirror also reads Y (the caller's own successor id after
        an orphaned-lock re-ensure it forgot to rebind to). The block is
        correct and stays; session_mirror_run_id is added as a diagnostic
        only, never used to override it."""
        from models.session_lifecycle import IssueLockResult

        lock_result = IssueLockResult(
            acquired=False, owner_session_id="sess-y", owner_run_id="Y", orphaned_lock=False
        )
        mirror_session = MagicMock()
        mirror_session.active_run_id = "Y"
        with (
            patch("models.session_lifecycle.touch_issue_lock", return_value=lock_result),
            patch("tools._sdlc_utils.find_session_by_issue", return_value=mirror_session),
        ):
            result = sdlc_next_skill.decide(issue_number=5006, run_id="X")

        assert result["blocked"] is True
        assert result["peek_identity"] == "caller"
        assert result["session_mirror_run_id"] == "Y"

    def test_stale_self_diagnostic_lookup_failure_leaves_key_absent(self, monkeypatch):
        """The session-mirror diagnostic lookup on the --run-id-supplied
        blocked path must never turn a failure into an error -- a raise
        there just leaves the key absent, block intact."""
        from models.session_lifecycle import IssueLockResult

        lock_result = IssueLockResult(
            acquired=False, owner_session_id="sess-y", owner_run_id="Y", orphaned_lock=False
        )
        with (
            patch("models.session_lifecycle.touch_issue_lock", return_value=lock_result),
            patch(
                "tools._sdlc_utils.find_session_by_issue",
                side_effect=RuntimeError("boom"),
            ),
        ):
            result = sdlc_next_skill.decide(issue_number=5007, run_id="X")

        assert result["blocked"] is True
        assert "session_mirror_run_id" not in result
        assert "error" not in result

    def test_empty_and_whitespace_run_id_behave_as_omitted(self, monkeypatch):
        """--run-id "" or whitespace-only must fall through to the inference
        path exactly as if omitted -- never compared as an identity."""
        from models.session_lifecycle import IssueLockResult

        monkeypatch.setattr(
            sdlc_next_skill,
            "_resolve_enriched",
            lambda issue_number, session_id: {"stages": {}, "_meta": {}},
        )
        monkeypatch.setattr(
            sdlc_next_skill,
            "_build_context",
            lambda proposed_skill, issue_number, stage_states=None, meta=None: {},
        )

        lock_result = IssueLockResult(acquired=True, owner_session_id=None)
        for candidate in ("", "   "):
            with (
                patch("tools._sdlc_utils.find_session_by_issue", return_value=None) as lookup_mock,
                patch(
                    "models.session_lifecycle.touch_issue_lock", return_value=lock_result
                ) as lock_mock,
                patch.object(
                    sdlc_next_skill, "_recover_stage_states_from_durable_signals", return_value={}
                ),
            ):
                sdlc_next_skill.decide(issue_number=5008, run_id=candidate)

            lookup_mock.assert_called_once()
            lock_mock.assert_called_once_with(5008, None, session_id="", peek=True)

    def test_no_lock_present_with_run_id_proceeds_to_guards(self, monkeypatch):
        """No lock exists at all + --run-id supplied -> acquired=True (a free
        lock always peeks acquired), proceeds past the pre-check with no
        block."""
        from agent.sdlc_router import Dispatch, decide_next_dispatch
        from models.session_lifecycle import IssueLockResult

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
            sdlc_next_skill,
            "_build_context",
            lambda proposed_skill, issue_number, stage_states=None, meta=None: {},
        )

        expected = decide_next_dispatch(states, meta, {})
        assert isinstance(expected, Dispatch)

        lock_result = IssueLockResult(acquired=True, owner_session_id=None)
        with patch("models.session_lifecycle.touch_issue_lock", return_value=lock_result):
            result = sdlc_next_skill.decide(issue_number=5009, run_id="my-run")

        assert result.get("blocked") is not True
        assert result["dispatched"] is True

    def test_peek_identity_absent_on_dispatch_payload(self, monkeypatch):
        """peek_identity is a diagnostic on ISSUE_LOCKED payloads only --
        never on a dispatch result (no payload pollution)."""
        from agent.sdlc_router import Dispatch, decide_next_dispatch
        from models.session_lifecycle import IssueLockResult

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
            sdlc_next_skill,
            "_build_context",
            lambda proposed_skill, issue_number, stage_states=None, meta=None: {},
        )

        expected = decide_next_dispatch(states, meta, {})
        assert isinstance(expected, Dispatch)

        lock_result = IssueLockResult(acquired=True, owner_session_id=None)
        with patch("models.session_lifecycle.touch_issue_lock", return_value=lock_result):
            result = sdlc_next_skill.decide(issue_number=5010, run_id="my-run")

        assert "peek_identity" not in result


class TestStageArtifactVerification:
    """Issue #1267: the stage-advance outcome verification gate.

    ``_build_context`` verifies the top-3 claimed stage artifacts (PR opened,
    branch pushed, plan committed on main) against the live world when
    ``stage_states``/``meta`` are supplied, setting
    ``stage_artifacts_verified``/``unverified_stage`` on a mismatch. This is
    context-assembly ONLY -- no dispatch decision is made here (that is
    ``guard_g8_artifact_verification`` in ``agent/sdlc_router.py``, see
    ``tests/unit/test_sdlc_router_oscillation.py``).
    """

    @staticmethod
    def _fake_gh_pr_state(state: str):
        """Fake ``subprocess.run`` that answers ``gh pr view --json state``."""

        def _run(cmd, **kwargs):
            proc = MagicMock()
            if cmd[:3] == ["gh", "pr", "view"]:
                proc.returncode = 0
                proc.stdout = json.dumps({"state": state})
            else:
                proc.returncode = 1
                proc.stdout = ""
            return proc

        return _run

    def test_no_claimed_artifact_is_a_noop(self, monkeypatch):
        """No stage claims completion → verification never runs a live check
        and leaves stage_artifacts_verified/unverified_stage unset."""
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: None)
        monkeypatch.setattr("tools.lane_identity.resolve_lane_slug", lambda *a, **k: None)
        run_mock = MagicMock()
        monkeypatch.setattr("subprocess.run", run_mock)

        stage_states = {"PLAN": "completed", "BUILD": "in_progress", "PATCH": "pending"}
        meta: dict = {}

        context = sdlc_next_skill._build_context(
            proposed_skill=None,
            issue_number=1267,
            stage_states=stage_states,
            meta=meta,
        )

        # PLAN claims completed but no plan is resolvable (no slug) -- the
        # PLAN check itself no-ops without a slug; BUILD/PATCH are not
        # claimed completed. No live check should have run at all.
        assert "stage_artifacts_verified" not in context
        assert "unverified_stage" not in context
        run_mock.assert_not_called()

    def test_false_build_claim_sets_unverified_stage(self, monkeypatch, caplog):
        """BUILD claims completed but the claimed PR is not OPEN live →
        stage_artifacts_verified=False, unverified_stage='BUILD', and an
        observable warning names the stage and the missing artifact."""
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: None)
        monkeypatch.setattr("subprocess.run", self._fake_gh_pr_state("CLOSED"))

        stage_states = {"BUILD": "completed"}
        meta = {"pr_number": 555}

        with caplog.at_level(logging.WARNING):
            context = sdlc_next_skill._build_context(
                proposed_skill=None,
                issue_number=1267,
                stage_states=stage_states,
                meta=meta,
            )

        assert context["stage_artifacts_verified"] is False
        assert context["unverified_stage"] == "BUILD"
        assert any(
            "BUILD" in record.message and "555" in record.message for record in caplog.records
        )

    def test_true_build_claim_leaves_context_unset(self, monkeypatch):
        """BUILD claims completed and the PR really is OPEN live → no-op
        (advances normally, g8 never fires)."""
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: None)
        monkeypatch.setattr("subprocess.run", self._fake_gh_pr_state("OPEN"))

        stage_states = {"BUILD": "completed"}
        meta = {"pr_number": 555}

        context = sdlc_next_skill._build_context(
            proposed_skill=None,
            issue_number=1267,
            stage_states=stage_states,
            meta=meta,
        )

        assert "stage_artifacts_verified" not in context
        assert "unverified_stage" not in context

    def test_true_build_claim_leaves_context_unset_when_merged(self, monkeypatch):
        """#1267 g8 merged-pipeline misfire: BUILD claims completed and the
        live PR state is MERGED (not OPEN) -> still a no-op. A merged PR is
        the strongest possible proof the BUILD artifact was real; treating
        it as unverified would re-dispatch /do-build forever on an issue
        that already shipped."""
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: None)
        monkeypatch.setattr("subprocess.run", self._fake_gh_pr_state("MERGED"))

        stage_states = {"BUILD": "completed"}
        meta = {"pr_number": 555}

        context = sdlc_next_skill._build_context(
            proposed_skill=None,
            issue_number=1267,
            stage_states=stage_states,
            meta=meta,
        )

        assert "stage_artifacts_verified" not in context
        assert "unverified_stage" not in context

    def test_patch_claim_skips_branch_check_when_pr_merged(self, monkeypatch, tmp_path):
        """#1267 g8 merged-pipeline misfire: PATCH claims completed, the PR
        is MERGED, and the branch has already been deleted (delete-branch-
        on-merge policy) -> still a no-op. The branch-pushed check must not
        even run once the PR's live state proves MERGED."""
        plan_path = tmp_path / "my-slug.md"
        plan_path.write_text("---\nstatus: Ready\n---\n\n# Plan\n")
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: plan_path)

        ls_remote_calls = []

        def _fake_run(cmd, **kwargs):
            proc = MagicMock()
            if cmd[:3] == ["gh", "pr", "view"]:
                proc.returncode = 0
                proc.stdout = json.dumps({"state": "MERGED"})
            elif cmd[:2] == ["git", "ls-remote"]:
                ls_remote_calls.append(cmd)
                proc.returncode = 0
                proc.stdout = ""  # branch gone -- would fail if the check ran
            else:
                proc.returncode = 1
                proc.stdout = ""
            return proc

        monkeypatch.setattr("subprocess.run", _fake_run)

        stage_states = {"PATCH": "completed"}
        meta = {"pr_number": 555}

        context = sdlc_next_skill._build_context(
            proposed_skill=None,
            issue_number=1267,
            stage_states=stage_states,
            meta=meta,
        )

        assert "stage_artifacts_verified" not in context
        assert "unverified_stage" not in context
        assert ls_remote_calls == [], "branch-pushed check must be skipped once PR is MERGED"

    def test_patch_claim_still_checks_branch_when_pr_open(self, monkeypatch, tmp_path):
        """A PATCH claim against a still-OPEN PR (not yet merged) must still
        run the real branch-pushed live check -- the MERGED skip is scoped
        strictly to state == "MERGED", not to "PR exists"."""
        plan_path = tmp_path / "my-slug.md"
        plan_path.write_text("---\nstatus: Ready\n---\n\n# Plan\n")
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: plan_path)
        monkeypatch.setattr("tools.lane_identity.resolve_lane_slug", lambda *a, **k: "my-slug")

        def _fake_run(cmd, **kwargs):
            proc = MagicMock()
            if cmd[:3] == ["gh", "pr", "view"]:
                proc.returncode = 0
                proc.stdout = json.dumps({"state": "OPEN"})
            elif cmd[:2] == ["git", "ls-remote"]:
                proc.returncode = 0
                proc.stdout = ""  # branch gone -- should fail verification
            else:
                proc.returncode = 1
                proc.stdout = ""
            return proc

        monkeypatch.setattr("subprocess.run", _fake_run)

        stage_states = {"PATCH": "completed"}
        meta = {"pr_number": 555}

        context = sdlc_next_skill._build_context(
            proposed_skill=None,
            issue_number=1267,
            stage_states=stage_states,
            meta=meta,
        )

        assert context["stage_artifacts_verified"] is False
        assert context["unverified_stage"] == "PATCH"

    def test_fails_open_on_infra_error(self, monkeypatch, caplog):
        """subprocess.TimeoutExpired/OSError from the gh/git call → advances
        (stage_artifacts_verified stays unset/True) with a warning logged."""
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: None)

        def _raise_timeout(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=10)

        monkeypatch.setattr("subprocess.run", _raise_timeout)

        stage_states = {"BUILD": "completed"}
        meta = {"pr_number": 555}

        with caplog.at_level(logging.WARNING):
            context = sdlc_next_skill._build_context(
                proposed_skill=None,
                issue_number=1267,
                stage_states=stage_states,
                meta=meta,
            )

        assert "stage_artifacts_verified" not in context
        assert any("infra error" in record.message for record in caplog.records)

    def test_fails_open_on_os_error(self, monkeypatch, caplog):
        """OSError (e.g. gh binary missing) also fails open with a warning."""
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: None)

        def _raise_os_error(cmd, **kwargs):
            raise OSError("gh: command not found")

        monkeypatch.setattr("subprocess.run", _raise_os_error)

        stage_states = {"BUILD": "completed"}
        meta = {"pr_number": 555}

        with caplog.at_level(logging.WARNING):
            context = sdlc_next_skill._build_context(
                proposed_skill=None,
                issue_number=1267,
                stage_states=stage_states,
                meta=meta,
            )

        assert "stage_artifacts_verified" not in context
        assert any("infra error" in record.message for record in caplog.records)

    def test_non_infra_exception_does_not_silently_advance(self, monkeypatch, caplog):
        """A logic bug (TypeError from a malformed artifact spec) must NOT be
        swallowed by the narrowed fail-open catch -- it surfaces (raises)
        and is logged at error level, never silently advancing."""
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: None)

        def _raise_type_error(cmd, **kwargs):
            raise TypeError("malformed artifact spec")

        monkeypatch.setattr("subprocess.run", _raise_type_error)

        stage_states = {"BUILD": "completed"}
        meta = {"pr_number": 555}

        with caplog.at_level(logging.ERROR):
            with pytest.raises(TypeError):
                sdlc_next_skill._build_context(
                    proposed_skill=None,
                    issue_number=1267,
                    stage_states=stage_states,
                    meta=meta,
                )

        assert any("unexpected" in record.message.lower() for record in caplog.records)

    def test_missing_stage_states_or_meta_skips_verification(self, monkeypatch):
        """Legacy callers that only pass proposed_skill/issue_number (no
        stage_states/meta) must not trigger verification at all."""
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: None)
        monkeypatch.setattr("tools.lane_identity.resolve_lane_slug", lambda *a, **k: None)
        run_mock = MagicMock()
        monkeypatch.setattr("subprocess.run", run_mock)

        context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=1267)

        assert "stage_artifacts_verified" not in context
        run_mock.assert_not_called()


class TestPrHeadShaContext:
    """WS3d (#2062): _build_context assembles the live PR-head signal for the
    router's head_sha staleness check. FAIL-CLOSED on lookup failure: the
    signal is set to the empty sentinel (+ pr_head_sha_lookup_failed) so the
    router treats the verdict as stale — never silently omitted."""

    _SHA = "c" * 40

    def _states_with_verdict(self):
        return {
            "REVIEW": "completed",
            "_verdicts": {"REVIEW": {"verdict": "APPROVED", "recorded_at": "2026-07-13T00:00:00"}},
        }

    def test_head_sha_set_on_successful_lookup(self, monkeypatch):
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: None)
        monkeypatch.setattr(
            sdlc_next_skill, "_fetch_pr_head_sha", lambda pr_number, repo=None: self._SHA
        )
        context = sdlc_next_skill._build_context(
            proposed_skill=None,
            issue_number=2062,
            stage_states=self._states_with_verdict(),
            meta={"pr_number": 42},
        )
        assert context["pr_head_sha"] == self._SHA
        assert "pr_head_sha_lookup_failed" not in context

    def test_lookup_failure_fails_closed_toward_stale(self, monkeypatch):
        """A gh/network error must set the empty sentinel, never omit the key."""

        def _boom(pr_number, repo=None):
            raise RuntimeError("gh exploded")

        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: None)
        monkeypatch.setattr(sdlc_next_skill, "_fetch_pr_head_sha", _boom)
        context = sdlc_next_skill._build_context(
            proposed_skill=None,
            issue_number=2062,
            stage_states=self._states_with_verdict(),
            meta={"pr_number": 42},
        )
        assert context["pr_head_sha"] == ""
        assert context["pr_head_sha_lookup_failed"] is True

    def test_lookup_returning_none_fails_closed(self, monkeypatch):
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: None)
        monkeypatch.setattr(
            sdlc_next_skill, "_fetch_pr_head_sha", lambda pr_number, repo=None: None
        )
        context = sdlc_next_skill._build_context(
            proposed_skill=None,
            issue_number=2062,
            stage_states=self._states_with_verdict(),
            meta={"pr_number": 42},
        )
        assert context["pr_head_sha"] == ""
        assert context["pr_head_sha_lookup_failed"] is True

    def test_no_pr_number_skips_lookup_and_omits_key(self, monkeypatch):
        called = []
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: None)
        monkeypatch.setattr(
            sdlc_next_skill,
            "_fetch_pr_head_sha",
            lambda pr_number, repo=None: called.append(pr_number),
        )
        context = sdlc_next_skill._build_context(
            proposed_skill=None,
            issue_number=2062,
            stage_states=self._states_with_verdict(),
            meta={},
        )
        assert "pr_head_sha" not in context
        assert called == []

    def test_no_recorded_review_verdict_skips_lookup(self, monkeypatch):
        """No recorded verdict → no live call, key omitted (the router's
        no-verdict recovery rows own that state; the signal stays inert)."""
        called = []
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: None)
        monkeypatch.setattr(
            sdlc_next_skill,
            "_fetch_pr_head_sha",
            lambda pr_number, repo=None: called.append(pr_number),
        )
        context = sdlc_next_skill._build_context(
            proposed_skill=None,
            issue_number=2062,
            stage_states={"REVIEW": "completed", "_verdicts": {}},
            meta={"pr_number": 42},
        )
        assert "pr_head_sha" not in context
        assert called == []


class TestLedgerDurabilityRecovery:
    """Issue #2395: an empty PipelineLedger for an issue that actually has
    durable state (committed plan, open PR, review) must not be silently
    treated as "pipeline never started" and routed to /do-plan. decide()
    reconstructs stage_states from PipelineStateMachine.derive_from_durable_signals
    when -- and only when -- stage_states is EXACTLY the empty dict.

    Decision 1 (settled): the reconstruction hook lives in
    tools/sdlc_next_skill.decide(), never inside agent/sdlc_router's pure
    guard table.
    Decision 2 (settled): gated on ``stage_states == {}`` exactly, not a
    falsy check -- any partially-populated ledger must pass through
    untouched.
    """

    @staticmethod
    def _patch_common(monkeypatch, issue_session=MagicMock()):
        """Bypass the issue-lock pre-check so decide() reaches _resolve_enriched."""
        from models.session_lifecycle import IssueLockResult

        monkeypatch.setattr(
            "tools._sdlc_utils.find_session_by_issue", lambda issue_number: issue_session
        )
        monkeypatch.setattr(
            "models.session_lifecycle.touch_issue_lock",
            lambda *a, **k: IssueLockResult(acquired=True, owner_session_id=None),
        )

    def test_empty_ledger_with_durable_signals_skips_do_plan(self, monkeypatch):
        """Empty ledger + durable signals showing an open PR and committed
        plan -> reconstruction fires, decide_next_dispatch receives the
        reconstructed non-empty stage_states, and the router does NOT
        dispatch /do-plan."""
        self._patch_common(monkeypatch)
        monkeypatch.setattr(
            sdlc_next_skill,
            "_resolve_enriched",
            lambda issue_number, session_id: {"stages": {}, "_meta": {"pr_number": 4242}},
        )
        reconstructed = {
            "ISSUE": STATUS_COMPLETED,
            "PLAN": STATUS_COMPLETED,
            "CRITIQUE": STATUS_COMPLETED,
            "BUILD": STATUS_COMPLETED,
            "TEST": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
            "MERGE": "pending",
        }
        monkeypatch.setattr(
            "agent.pipeline_state.PipelineStateMachine.derive_from_durable_signals",
            lambda session: reconstructed,
        )

        result = sdlc_next_skill.decide(issue_number=4242)

        assert result["dispatched"] is True
        assert result["skill"] != SKILL_DO_PLAN

    def test_empty_ledger_no_durable_signals_still_dispatches_do_plan(self, monkeypatch):
        """Empty ledger + a genuinely fresh issue (derive_from_durable_signals
        finds nothing) -> reconstruction yields nothing meaningful, router
        still dispatches /do-plan. Critical: don't mask a genuinely fresh
        issue."""
        self._patch_common(monkeypatch)
        monkeypatch.setattr(
            sdlc_next_skill,
            "_resolve_enriched",
            lambda issue_number, session_id: {"stages": {}, "_meta": {}},
        )
        monkeypatch.setattr(
            "agent.pipeline_state.PipelineStateMachine.derive_from_durable_signals",
            lambda session: {},
        )

        result = sdlc_next_skill.decide(issue_number=4243)

        assert result["dispatched"] is True
        assert result["skill"] == SKILL_DO_PLAN

    def test_partial_ledger_passes_through_unchanged(self, monkeypatch):
        """A partially-populated stage_states (e.g. only ISSUE completed) must
        pass through UNCHANGED -- reconstruction must not fire at all when
        stage_states != {}, even if it's mostly-empty."""
        self._patch_common(monkeypatch)
        monkeypatch.setattr(
            sdlc_next_skill,
            "_resolve_enriched",
            lambda issue_number, session_id: {
                "stages": {"ISSUE": STATUS_COMPLETED},
                "_meta": {},
            },
        )
        recover_mock = MagicMock()
        monkeypatch.setattr(
            sdlc_next_skill, "_recover_stage_states_from_durable_signals", recover_mock
        )

        result = sdlc_next_skill.decide(issue_number=4244)

        recover_mock.assert_not_called()
        # Partial ledger (only ISSUE completed, no PLAN) still routes to /do-plan
        # via the normal guard table -- but the point under test is that the
        # reconstruction path was never invoked.
        assert result["dispatched"] is True
        assert result["skill"] == SKILL_DO_PLAN

    def test_reconstruction_failure_does_not_propagate(self, monkeypatch):
        """A failure inside the reconstruction path (derive_from_durable_signals
        raising, or find_session_by_issue raising) does not propagate --
        decide() still returns a normal result routing to /do-plan for the
        empty-ledger case."""
        from models.session_lifecycle import IssueLockResult

        monkeypatch.setattr(
            "tools._sdlc_utils.find_session_by_issue",
            lambda issue_number: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr(
            "models.session_lifecycle.touch_issue_lock",
            lambda *a, **k: IssueLockResult(acquired=True, owner_session_id=None),
        )
        monkeypatch.setattr(
            sdlc_next_skill,
            "_resolve_enriched",
            lambda issue_number, session_id: {"stages": {}, "_meta": {}},
        )

        result = sdlc_next_skill.decide(issue_number=4245)

        assert result["dispatched"] is True
        assert result["skill"] == SKILL_DO_PLAN

    def test_recover_helper_returns_empty_when_no_session_found(self, monkeypatch):
        """Direct unit coverage of the helper: no issue session resolvable ->
        returns {} without raising."""
        monkeypatch.setattr("tools._sdlc_utils.find_session_by_issue", lambda issue_number: None)

        result = sdlc_next_skill._recover_stage_states_from_durable_signals(9999)

        assert result == {}

    def test_recover_helper_swallows_derive_exception(self, monkeypatch):
        """Direct unit coverage: derive_from_durable_signals raising is
        swallowed and {} is returned."""
        monkeypatch.setattr(
            "tools._sdlc_utils.find_session_by_issue", lambda issue_number: MagicMock()
        )
        monkeypatch.setattr(
            "agent.pipeline_state.PipelineStateMachine.derive_from_durable_signals",
            lambda session: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        result = sdlc_next_skill._recover_stage_states_from_durable_signals(9999)

        assert result == {}


class TestConcernRoundCountReachesTheRouterThroughTheCLI:
    """#2787 gate item 3: the `_meta` plumb, end-to-end through the real CLI.

    Every other test in this feature calls ``decide_next_dispatch`` with a
    hand-built ``meta`` dict, so all of them pass whether or not
    ``_concern_round_count`` is actually projected. The production path is
    ``sdlc-tool next-skill`` -> ``_resolve_enriched`` -> ``query_enriched``,
    and ``query_enriched`` threads only ``("_verdicts", "_sdlc_dispatches")``
    out of raw stage_states into ``stages``. A bare underscore key is dropped
    on the floor. This is the ONLY test that fails if the counter loses its
    ``_meta`` projection -- without it the feature is green everywhere and
    inert in production.

    Real ``PipelineLedger`` in the claimed Redis test db, real plan file, real
    frontmatter parser, real ``query_enriched``, real ``main()``. Only the
    ``gh`` network reads are stubbed.
    """

    TARGET_REPO = "test-2787/with-concerns-fixture"
    ISSUE = 927871
    T_VERDICT = "2026-08-17T02:00:00"
    T_REVISION = "2026-08-17T03:00:00"  # postdates the verdict -> state S2

    def _write_plan(self, root: Path) -> Path:
        """Real git repo with the plan committed on main.

        G8's stage-artifact verification is live and correct: an uncommitted
        plan under a ``PLAN: completed`` marker really is unverified, and G8
        re-dispatches /do-plan before the CRITIQUE rows are ever reached. The
        fixture has to satisfy it or this test measures G8, not the plumb.
        """
        subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
        plan = root / "docs" / "plans" / "wc-2787-fixture.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(
            "---\n"
            "status: Ready\n"
            f"tracking: https://github.com/{self.TARGET_REPO}/issues/{self.ISSUE}\n"
            "revision_applied: true\n"
            f"revision_applied_at: {self.T_REVISION}\n"
            "---\n\n# Plan\n\nBody.\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=t@t",
                "-c",
                "user.name=t",
                "-C",
                str(root),
                "commit",
                "-m",
                "plan",
            ],
            check=True,
            capture_output=True,
        )
        return plan

    def _seed_ledger(self, concern_round_count: int, artifact_hash: str):
        from agent.pipeline_ledger import PipelineLedger

        ledger = PipelineLedger.get_or_create(self.TARGET_REPO, self.ISSUE)
        ledger.stage_states_json = json.dumps(
            {
                "ISSUE": "completed",
                "PLAN": "completed",
                "CRITIQUE": "completed",
                "BUILD": "pending",
                "_verdicts": {
                    "CRITIQUE": {
                        "verdict": "READY TO BUILD (WITH CONCERNS)",
                        "recorded_at": self.T_VERDICT,
                        "artifact_hash": artifact_hash,
                    }
                },
                # The raw underscore key, exactly as record_verdict writes it.
                "_concern_round_count": concern_round_count,
            }
        )
        ledger.save()
        return ledger

    def _run_cli(self, concern_round_count, tmp_path, monkeypatch, capsys) -> dict:
        from tools.sdlc_verdict import compute_plan_body_hash

        root = tmp_path / "target"
        root.mkdir()
        plan = self._write_plan(root)
        # Matching hash so G5's cached-verdict branch is genuinely LIVE here:
        # this exercises the with-concerns step-aside as well as the plumb.
        artifact_hash = compute_plan_body_hash(plan)
        assert artifact_hash, "fixture plan must hash"

        monkeypatch.setenv("GH_REPO", self.TARGET_REPO)
        monkeypatch.setenv("SDLC_TARGET_REPO", str(root))

        ledger = self._seed_ledger(concern_round_count, artifact_hash)
        try:
            with patch("tools.sdlc_stage_query._lookup_pr", return_value=None):
                rc = sdlc_next_skill.main(["--issue-number", str(self.ISSUE)])
            assert rc == 0
            return json.loads(capsys.readouterr().out)
        finally:
            # Scoped ORM delete of exactly what this test created.
            ledger.delete()

    def test_below_the_bound_the_cli_returns_do_plan_critique(self, tmp_path, monkeypatch, capsys):
        """State S2 below the bound: the concern-closing revision is judged."""
        out = self._run_cli(1, tmp_path, monkeypatch, capsys)
        assert out.get("dispatched") is True, out
        assert out["skill"] == "/do-plan-critique", out
        assert out["row_id"] == "2b", out

    def test_at_the_bound_the_cli_returns_do_build(self, tmp_path, monkeypatch, capsys):
        """The projection-drop detector.

        Below the bound and a DROPPED counter both read as ``0``, so only the
        at-the-bound case can tell them apart: if ``_concern_round_count``
        never reaches ``_meta``, the router sees ``0``, stays below the bound,
        and answers ``/do-plan-critique`` here instead of ``/do-build`` --
        an unbounded loop in production that every unit test misses.
        """
        from agent.pipeline_graph import MAX_CONCERN_RECRITIQUE_ROUNDS

        out = self._run_cli(MAX_CONCERN_RECRITIQUE_ROUNDS, tmp_path, monkeypatch, capsys)
        assert out.get("dispatched") is True, out
        assert out["skill"] == "/do-build", out
        assert out["row_id"] == "4c", out
        assert "residual concerns accepted unreviewed" in out["reason"].lower(), out
