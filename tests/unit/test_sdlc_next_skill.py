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
    def _fake_git(*branches: str):
        """Fake ``git ls-remote --heads origin`` listing the given branch names."""
        lines = "\n".join(f"{'a' * 40}\trefs/heads/{b}" for b in branches)

        def _run(cmd, **kwargs):
            proc = MagicMock()
            if cmd[:3] == ["git", "ls-remote", "--heads"]:
                proc.returncode = 0
                proc.stdout = lines
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
            self._fake_git("main", "session/my-feature-slug"),
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
            self._fake_git("main", "session/sdlc-2003"),
        )

        context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=2003)

        assert context["branch_exists"] is True

    def test_false_and_silent_when_no_slug_recorded(self, monkeypatch):
        """No recorded slug and no PR -> cannot affirm existence -> False, and
        no live call (#3065: resolve_branch_truth short-circuits before any
        subprocess when there is nothing to check)."""
        monkeypatch.setattr("tools.lane_identity.resolve_lane_slug", lambda *a, **k: None)
        run_mock = MagicMock()
        monkeypatch.setattr("subprocess.run", run_mock)

        context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=2003)

        assert context["branch_exists"] is False
        assert not any(
            call.args and call.args[0][:3] == ["git", "ls-remote", "--heads"]
            for call in run_mock.call_args_list
        )

    def test_false_when_branch_absent(self, monkeypatch):
        monkeypatch.setattr(
            "tools.lane_identity.resolve_lane_slug", lambda *a, **k: "my-feature-slug"
        )
        monkeypatch.setattr("subprocess.run", self._fake_git("main"))

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
        """_build_context's branch_exists probe (via resolve_branch_truth's
        ``git ls-remote --heads origin``) runs with cwd pinned at the target
        checkout, not the process cwd (#2078, #3065)."""
        target = tmp_path / "target"
        target.mkdir()
        self._init_fixture_repo(target, "sdlc-2078-fixture")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        monkeypatch.setenv("SDLC_TARGET_REPO", str(target))
        monkeypatch.setattr(
            "tools.lane_identity.resolve_lane_slug", lambda *a, **k: "sdlc-2078-fixture"
        )

        calls = []

        def _fake_run(cmd, **kwargs):
            proc = MagicMock()
            if cmd[:3] == ["git", "ls-remote", "--heads"]:
                calls.append(kwargs.get("cwd"))
                proc.returncode = 0
                sha = "a" * 40
                proc.stdout = f"{sha}\trefs/heads/session/sdlc-2078-fixture\n"
            else:
                proc.returncode = 1
                proc.stdout = ""
            return proc

        monkeypatch.setattr("subprocess.run", _fake_run)

        context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=2078)

        assert context["branch_exists"] is True
        assert calls == [str(target)], "ls-remote must run with cwd pinned at SDLC_TARGET_REPO"


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

    assert result["decision"] == "dispatch"
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
            "decision": "blocked",
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
            "decision": "dispatch",
            "recorded": False,
            "recorded_reason": sdlc_next_skill.NOT_RECORDED_REASON,
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
        assert result["decision"] == "dispatch"

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
        is MERGED -> still a no-op, and ``resolve_branch_truth`` must not
        even run (poisoned to explode if called) once the PR's live state
        proves MERGED -- a deleted branch is the expected side effect of a
        delete-branch-on-merge policy, not evidence of a fabricated claim.

        Calls ``_verify_stage_artifacts_live`` directly (not through
        ``_build_context``) so this test is isolated from the UNRELATED
        Row-5 ``branch_exists`` probe, which always resolves branch truth
        once per tick regardless of PATCH's MERGED-skip (#3065)."""
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: None)
        monkeypatch.setattr("tools.lane_identity.resolve_lane_slug", lambda *a, **k: "my-slug")
        monkeypatch.setattr("subprocess.run", self._fake_gh_pr_state("MERGED"))

        def _poison(*a, **k):
            raise AssertionError("resolve_branch_truth must not run once PR is MERGED")

        monkeypatch.setattr(sdlc_next_skill, "resolve_branch_truth", _poison)

        stage_states = {"PATCH": "completed"}
        meta = {"pr_number": 555}

        context = sdlc_next_skill._verify_stage_artifacts_live(stage_states, meta, 1267)

        assert "stage_artifacts_verified" not in context
        assert "unverified_stage" not in context

    def test_patch_claim_found_via_pr_head_when_recorded_slug_is_wrong(self, monkeypatch, tmp_path):
        """#3065 acceptance: a WRONG recorded lane slug with a live branch
        under a DIFFERENT name must NOT dispatch /do-patch. The PR's head SHA
        (git-first, never a bare `gh` read) uniquely matches a branch that
        disagrees with the recorded slug -- resolve_branch_truth reports
        ``found`` on the REAL branch, not a falsified PATCH claim. This is the
        keystone regression: the old ``_check_branch_pushed`` probed only the
        wrong-slug-derived name and reported it gone."""
        plan_path = tmp_path / "my-slug.md"
        plan_path.write_text("---\nstatus: Ready\n---\n\n# Plan\n")
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: plan_path)
        # The recorded slug names a branch that does not exist on origin --
        # the wrong-recorded-slug state this task fixes.
        monkeypatch.setattr("tools.lane_identity.resolve_lane_slug", lambda *a, **k: "wrong-slug")

        sha = "b" * 40

        def _fake_run(cmd, **kwargs):
            proc = MagicMock()
            if cmd[:3] == ["gh", "pr", "view"]:
                proc.returncode = 0
                proc.stdout = json.dumps({"state": "OPEN"})
            elif cmd[:3] == ["git", "ls-remote", "--heads"]:
                proc.returncode = 0
                # The lane's REAL branch is named differently than the
                # (wrong) recorded slug's derived name.
                proc.stdout = f"{sha}\trefs/heads/session/dev-actual-branch\n"
            elif cmd[:2] == ["git", "ls-remote"]:
                # refs/pull/<N>/head single-ref query inside resolve_pr_head_sha.
                proc.returncode = 0
                proc.stdout = f"{sha}\trefs/pull/555/head\n"
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
        assert context["branch_truth"] == sdlc_next_skill.BRANCH_TRUTH_FOUND
        assert context["branch_truth_branch"] == "session/dev-actual-branch"

    def test_patch_claim_absent_when_pr_open_and_branch_genuinely_gone(self, monkeypatch, tmp_path):
        """A PATCH claim against a still-OPEN PR whose head resolves to
        nothing in the listing (ambiguous/mid-push territory) must defer
        (indeterminate), not falsify -- see
        test_patch_claim_with_no_pr_and_absent_branch_is_falsified below for
        the one shape that DOES falsify (no PR at all)."""
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
                proc.stdout = ""  # nothing matches anywhere -- Race 1
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

        # Race 1: a listing that does not contain the PR's head is a
        # possibly-stale negative (mid-push), never an absence, while a PR
        # is open. G8 must step aside rather than dispatch /do-patch on an
        # unreadable fact.
        assert "stage_artifacts_verified" not in context
        assert "unverified_stage" not in context
        assert context["branch_truth"] == sdlc_next_skill.BRANCH_TRUTH_INDETERMINATE

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

    # -- Issue #2757: unverifiable is not falsified ------------------------
    #
    # Three states, not two. An artifact is VERIFIED when its identifier
    # resolves and the world confirms it, FALSIFIED when the identifier
    # resolves and the world contradicts it, and UNVERIFIABLE when there is no
    # identifier to resolve at all. Only the middle one may set
    # stage_artifacts_verified=False. The gate previously collapsed the first
    # and third, reporting a mismatch on the strength of a `gh` call it had
    # never placed -- which re-dispatched /do-build against already-merged
    # work.

    @pytest.mark.parametrize(
        "meta,label",
        [
            ({}, "pr_number key absent entirely"),
            ({"pr_number": None}, "pr_number is None"),
            ({"pr_number": 0}, "pr_number is 0"),
        ],
    )
    def test_unverifiable_build_claim_makes_no_live_call(self, monkeypatch, meta, label):
        """BUILD claims completed with no usable PR number -> ZERO subprocess
        calls and both flags left unset.

        assert_not_called() is the load-bearing half (following
        test_no_claimed_artifact_is_a_noop): asserting only that the flags are
        unset would also pass for an implementation that calls `gh`, gets
        nothing back, and then declines to conclude -- a different, slower,
        rate-limit-consuming behavior. The guard must run BEFORE the
        subprocess, not after it.

        The falsy forms are parametrized rather than split into three tests:
        this is one falsiness check, and `bool(pr_number)` is what decides it.
        """
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: None)
        monkeypatch.setattr("tools.lane_identity.resolve_lane_slug", lambda *a, **k: None)
        run_mock = MagicMock()
        monkeypatch.setattr("subprocess.run", run_mock)

        context = sdlc_next_skill._build_context(
            proposed_skill=None,
            issue_number=2757,
            stage_states={"BUILD": "completed"},
            meta=dict(meta),
        )

        assert "stage_artifacts_verified" not in context, label
        assert "unverified_stage" not in context, label
        run_mock.assert_not_called()

    def test_patch_claim_with_no_pr_and_absent_branch_is_falsified(self, monkeypatch, tmp_path):
        """#3065: PATCH claims completed, no PR number is recorded, and the
        recorded branch is genuinely absent from origin -> FALSIFIED (PATCH),
        not "unverifiable".

        Before #3065, a missing PR number made this claim unverifiable
        outright, because the old two-valued branch probe could not tell
        "branch gone" from "deleted on merge" without a PR's state to
        consult. ``resolve_branch_truth`` closes that ambiguity a different
        way: a merge is impossible without a PR, so "no PR, branch absent"
        can never be a deletion-on-merge false positive -- it can only mean
        the branch was never pushed. This is the acceptance case for "a
        genuinely unpushed branch STILL dispatches /do-patch".
        """
        plan_path = tmp_path / "my-slug.md"
        plan_path.write_text("---\nstatus: Ready\n---\n\n# Plan\n")
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: plan_path)
        monkeypatch.setattr("tools.lane_identity.resolve_lane_slug", lambda *a, **k: "my-slug")

        def _fake_run(cmd, **kwargs):
            proc = MagicMock()
            if cmd[:3] == ["git", "ls-remote", "--heads"]:
                proc.returncode = 0
                proc.stdout = ""  # nothing on origin at all
            else:
                proc.returncode = 1
                proc.stdout = ""
            return proc

        monkeypatch.setattr("subprocess.run", _fake_run)

        context = sdlc_next_skill._build_context(
            proposed_skill=None,
            issue_number=2757,
            stage_states={"PATCH": "completed"},
            meta={"pr_number": None},
        )

        assert context["stage_artifacts_verified"] is False
        assert context["unverified_stage"] == "PATCH"
        assert context["branch_truth"] == sdlc_next_skill.BRANCH_TRUTH_ABSENT

    def test_unverifiable_build_skip_logs_at_debug_not_warning(self, monkeypatch, caplog):
        """The skip is a normal, expected state and logs at DEBUG.

        The level is the user-visible contract here, not decoration: a
        ledger-degraded issue is re-evaluated every tick, so a WARNING would
        emit once per tick per affected issue and train operators to ignore
        the channel the genuine falsified-claim mismatch also uses.

        `caplog.set_level(logging.DEBUG)` is mandatory -- caplog's default
        floor is WARNING, so without it "a DEBUG record exists" would assert
        against an empty list and pass vacuously whatever level the code used.
        The assertion is deliberately one-directional: it pins the level of
        the NEW log and the ABSENCE of a warning for this input, without
        re-pinning the existing mismatch warning's level as an interface.
        """
        caplog.set_level(logging.DEBUG, logger="tools.sdlc_next_skill")
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: None)
        monkeypatch.setattr("tools.lane_identity.resolve_lane_slug", lambda *a, **k: None)
        monkeypatch.setattr("subprocess.run", MagicMock())

        sdlc_next_skill._build_context(
            proposed_skill=None,
            issue_number=2757,
            stage_states={"BUILD": "completed"},
            meta={"pr_number": None},
        )

        verify_logs = [r for r in caplog.records if "stage-artifact-verify" in r.getMessage()]
        assert verify_logs, "the skip must be observable to an operator debugging the tick"
        assert all(r.levelno == logging.DEBUG for r in verify_logs), [
            (r.levelname, r.getMessage()) for r in verify_logs
        ]
        # Reason and issue number both present, so the log answers "why did
        # next-skill skip that check" without a code read.
        assert any(
            "2757" in r.getMessage() and "no PR number" in r.getMessage() for r in verify_logs
        )

    def test_unverifiable_build_claim_is_a_noop_even_if_subprocess_would_raise(self, monkeypatch):
        """Positive proof of ordering: the guard runs before the subprocess.

        With a `subprocess.run` that raises on ANY call, the unverifiable
        BUILD claim must still return cleanly with no flags set. If the guard
        ran after the call, this would instead take the fail-open infra path
        (returning {} for a different reason) or re-raise -- both of which
        would mean a `pr_number`-less claim still costs a live call.
        """

        def _explode(cmd, **kwargs):
            raise AssertionError(f"no subprocess may run for an unverifiable claim: {cmd}")

        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: None)
        monkeypatch.setattr("tools.lane_identity.resolve_lane_slug", lambda *a, **k: None)
        monkeypatch.setattr("subprocess.run", _explode)

        context = sdlc_next_skill._build_context(
            proposed_skill=None,
            issue_number=2757,
            stage_states={"BUILD": "completed"},
            meta={"pr_number": None},
        )

        assert "stage_artifacts_verified" not in context
        assert "unverified_stage" not in context


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
        """No recorded verdict → the WS3d head_sha verdict-staleness signal's
        OWN lookup must not run, and the key stays omitted (the router's
        no-verdict recovery rows own that state; the signal stays inert).

        Branch-truth resolution (#3065) independently calls
        ``_fetch_pr_head_sha`` whenever a PR is recorded, regardless of
        REVIEW verdicts -- ``resolve_branch_truth`` is stubbed out here so
        that unrelated call site cannot pollute the ``called`` list this test
        uses to prove the WS3d signal itself never fired.
        """
        called = []
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: None)
        monkeypatch.setattr(
            sdlc_next_skill,
            "resolve_branch_truth",
            lambda *a, **k: sdlc_next_skill.BranchTruth(
                status=sdlc_next_skill.BRANCH_TRUTH_INDETERMINATE, reason="stubbed"
            ),
        )
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

        assert result["decision"] == "dispatch"
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

        assert result["decision"] == "dispatch"
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
        assert result["decision"] == "dispatch"
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

        assert result["decision"] == "dispatch"
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
        assert out.get("decision") == "dispatch", out
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
        assert out.get("decision") == "dispatch", out
        assert out["skill"] == "/do-build", out
        assert out["row_id"] == "4c", out
        assert "residual concerns accepted unreviewed" in out["reason"].lower(), out


class TestPersistenceHonesty:
    """Issue #2897: next-skill's payload must never assert a persistence that
    never happened.

    ``next-skill`` is a **pure decision** call. It writes nothing, ever --
    with or without ``--run-id`` (which is only a read-only issue-lock peek
    identity, #2766). Recording the dispatch into the ledger is a separate
    ``sdlc-tool dispatch record`` call. The old ``"dispatched": true`` key
    read as "a dispatch happened", so a supervisor that skipped the record
    step believed the ledger had advanced when it had not: the router
    re-derived from a history that never grew, pinned on the prior row, and
    the repeated re-dispatches later tripped G4 (stage oscillation).

    The payload now states the decision (``decision``) separately from the
    persistence (``recorded``), and the persistence field is honest.
    """

    @staticmethod
    def _patch_dispatch(monkeypatch, states, meta=None):
        from models.session_lifecycle import IssueLockResult

        meta = meta if meta is not None else {}
        monkeypatch.setattr(
            "models.session_lifecycle.touch_issue_lock",
            lambda *a, **k: IssueLockResult(acquired=True, owner_session_id=None),
        )
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

    @staticmethod
    def _mid_pipeline_states():
        return {
            "ISSUE": STATUS_COMPLETED,
            "PLAN": STATUS_COMPLETED,
            "CRITIQUE": "pending",
            "BUILD": "pending",
            "TEST": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
            "MERGE": "pending",
        }

    @pytest.mark.parametrize("run_id", [None, "run-abc123"], ids=["no-run-id", "with-run-id"])
    def test_dispatch_payload_never_claims_persistence(self, monkeypatch, run_id):
        """The persistence claim is honest whether or not ``--run-id`` is passed.

        ``--run-id`` does not make next-skill persist anything, so gating the
        claim on it would still be a lie on the run-id path.
        """
        self._patch_dispatch(monkeypatch, self._mid_pipeline_states())

        result = sdlc_next_skill.decide(issue_number=28970, run_id=run_id)

        assert result["decision"] == "dispatch"
        assert result["recorded"] is False
        assert result["recorded_reason"] == sdlc_next_skill.NOT_RECORDED_REASON
        assert result["skill"]

    def test_dispatched_key_is_gone(self, monkeypatch):
        """The ambiguous ``dispatched`` key is removed outright, not shimmed --
        a caller reading it must fail loudly rather than read a stale lie."""
        self._patch_dispatch(monkeypatch, self._mid_pipeline_states())

        result = sdlc_next_skill.decide(issue_number=28971, run_id="run-abc123")

        assert "dispatched" not in result

    def test_blocked_payload_carries_blocked_decision(self, monkeypatch):
        """Blocked results state ``decision: "blocked"`` and make no
        persistence claim at all."""
        from models.session_lifecycle import IssueLockResult

        monkeypatch.setattr(
            "models.session_lifecycle.touch_issue_lock",
            lambda *a, **k: IssueLockResult(
                acquired=False, owner_session_id="other", owner_run_id="rival"
            ),
        )

        result = sdlc_next_skill.decide(issue_number=28972, run_id="mine")

        assert result["blocked"] is True
        assert result["decision"] == "blocked"
        assert "dispatched" not in result
        assert "recorded" not in result

    def test_error_payload_carries_error_decision(self, monkeypatch):
        """A fatal lookup failure states ``decision: "error"`` -- no truthy
        dispatch claim survives the exception path."""
        monkeypatch.setattr(
            "models.session_lifecycle.touch_issue_lock",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        result = sdlc_next_skill.decide(issue_number=28973)

        assert result["decision"] == "error"
        assert "boom" in result["error"]
        assert "dispatched" not in result

    def test_cli_usage_error_payload_and_exit_code(self, capsys):
        """The wrapper-level usage error emits the same honest shape."""
        rc = sdlc_next_skill.main([])

        assert rc == 2
        out = json.loads(capsys.readouterr().out)
        assert out["decision"] == "error"
        assert "dispatched" not in out

    def test_cli_exits_1_on_error_payload(self, monkeypatch, capsys):
        """An error decision still exits 1 now that the exit-code branch no
        longer consults ``dispatched``."""
        monkeypatch.setattr(
            sdlc_next_skill,
            "decide",
            lambda **kwargs: {"error": "boom", "decision": "error"},
        )

        rc = sdlc_next_skill.main(["--issue-number", "28974"])

        assert rc == 1
        assert json.loads(capsys.readouterr().out)["decision"] == "error"


class TestTerminalDecisionShape:
    """Issues #2894/#2817: a finished lane gets its own output shape.

    The router's terminal guard preempts the dispatch table on a shipped lane.
    Before it existed, that lane fell through to ``Blocked(NO_RULE)`` and every
    consumer read a successful pipeline as a routing bug. ``decision:
    "terminal"`` is therefore a fourth shape, deliberately NOT a flavour of
    ``blocked``: it carries no ``blocked`` key, and it is a SUCCESS, so the CLI
    exits 0.
    """

    @staticmethod
    def _patch_terminal(monkeypatch, states, meta=None):
        from models.session_lifecycle import IssueLockResult

        monkeypatch.setattr(
            "models.session_lifecycle.touch_issue_lock",
            lambda *a, **k: IssueLockResult(acquired=True, owner_session_id=None),
        )
        monkeypatch.setattr(
            sdlc_next_skill,
            "_resolve_enriched",
            lambda issue_number, session_id: {"stages": states, "_meta": meta or {}},
        )
        monkeypatch.setattr(
            sdlc_next_skill,
            "_build_context",
            lambda proposed_skill, issue_number, stage_states=None, meta=None: {},
        )

    @pytest.mark.parametrize(
        ("states", "meta", "evidence"),
        [
            ({"MERGE": STATUS_COMPLETED}, {}, "merge_marker"),
            ({"BUILD": STATUS_COMPLETED}, {"pr_state": "MERGED", "pr_number": 4242}, "merged_pr"),
        ],
        ids=["merge-marker", "merged-pr"],
    )
    def test_terminal_ledger_emits_terminal_shape(self, monkeypatch, states, meta, evidence):
        """Both terminal evidence branches surface through the CLI decision."""
        self._patch_terminal(monkeypatch, states, meta)

        result = sdlc_next_skill.decide(issue_number=28980, run_id="mine")

        assert result["decision"] == "terminal", result
        assert result["evidence"] == evidence, result
        assert result["row_id"] == "T", result
        assert result["reason"], result

    def test_terminal_is_distinct_from_blocked_and_dispatch(self, monkeypatch):
        """A shipped lane must not read as an escalation or a dispatch (#2817)."""
        self._patch_terminal(monkeypatch, {"MERGE": STATUS_COMPLETED})

        result = sdlc_next_skill.decide(issue_number=28981, run_id="mine")

        assert "blocked" not in result
        assert "guard_id" not in result
        assert "error" not in result
        assert "skill" not in result
        assert "recorded" not in result

    def test_cli_exits_0_on_terminal_payload(self, monkeypatch, capsys):
        """Terminal is a successful outcome — only ``error`` exits 1."""
        monkeypatch.setattr(
            sdlc_next_skill,
            "decide",
            lambda **kwargs: {
                "decision": "terminal",
                "reason": "Pipeline complete",
                "evidence": "merge_marker",
                "row_id": "T",
            },
        )

        rc = sdlc_next_skill.main(["--issue-number", "28982"])

        assert rc == 0
        assert json.loads(capsys.readouterr().out)["decision"] == "terminal"


class TestDecisionEvidenceReachesTheCliJson:
    """#3065 Cluster D: a decision's evidence is worthless if it dies inside
    the router. These drive the actual CLI surface (``decide()``), following
    ``test_decide_warm_cache_open_pr_defers_to_pr_review_not_plan``'s pattern
    of injecting stage_states/meta rather than resolving live session state.
    """

    def _inject(self, monkeypatch, states, meta):
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

    def _no_rule_fixture(self, unconfirmed=False):
        states = {
            "ISSUE": STATUS_COMPLETED,
            "PLAN": STATUS_COMPLETED,
            "CRITIQUE": STATUS_COMPLETED,
            "BUILD": STATUS_COMPLETED,
            "TEST": STATUS_COMPLETED,
            "REVIEW": STATUS_COMPLETED,
            "DOCS": STATUS_COMPLETED,
            "MERGE": "pending",
            "_verdicts": {
                "REVIEW": {"verdict": "LGTM", "recorded_at": "2026-09-01T00:00:00+00:00"}
            },
            "_sdlc_dispatches": [
                {
                    "skill": "/do-test",
                    "at": "2026-09-03T00:00:00+00:00",
                    "stage_snapshot": {},
                    "confirmed": not unconfirmed,
                }
            ],
        }
        meta = {
            "pr_number": 4242,
            "pr_merge_state": "CLEAN",
            "pr_state": "OPEN",
            "latest_review_verdict": "LGTM",
            "last_dispatched_skill": "/do-test",
            "ci_all_passing": True,
        }
        return states, meta

    def test_no_rule_block_round_trips_its_inputs_through_the_cli_json(self, monkeypatch):
        states, meta = self._no_rule_fixture()
        self._inject(monkeypatch, states, meta)

        result = sdlc_next_skill.decide(issue_number=28981)

        assert result["decision"] == "blocked"
        assert result["guard_id"] == "NO_RULE"
        # Serialized exactly as the CLI would print it -- the whole point is
        # that a supervisor can read the inputs out of the JSON.
        payload = json.loads(json.dumps(result))
        assert payload["decision_inputs"]["meta"]["pr_number"] == 4242
        assert payload["decision_inputs"]["stage_states"]["REVIEW"] == STATUS_COMPLETED

    def test_a_routed_decision_carries_no_decision_inputs_key(self, monkeypatch):
        """Negative pole: the key is not sprayed onto every payload."""
        states, meta = self._no_rule_fixture()
        states["_verdicts"]["REVIEW"]["verdict"] = "CHANGES REQUESTED"
        meta["latest_review_verdict"] = "CHANGES REQUESTED"
        self._inject(monkeypatch, states, meta)

        result = sdlc_next_skill.decide(issue_number=28981)

        assert result["decision"] == "dispatch"
        assert "decision_inputs" not in result

    def test_dispatch_payload_reports_an_unrecorded_previous_dispatch(self, monkeypatch):
        states, meta = self._no_rule_fixture(unconfirmed=True)
        states["_verdicts"]["REVIEW"]["verdict"] = "CHANGES REQUESTED"
        meta["latest_review_verdict"] = "CHANGES REQUESTED"
        self._inject(monkeypatch, states, meta)

        result = sdlc_next_skill.decide(issue_number=28981)

        assert result["decision"] == "dispatch"
        assert result["unrecorded_dispatch"]["confirmed"] is False
        # Distinct from ``recorded``, which is about THIS decision and is
        # always False because decide() never writes (#2897).
        assert result["recorded"] is False

    def test_dispatch_payload_omits_the_signal_when_the_record_confirms(self, monkeypatch):
        """Negative pole, one boolean apart from the test above."""
        states, meta = self._no_rule_fixture(unconfirmed=False)
        states["_verdicts"]["REVIEW"]["verdict"] = "CHANGES REQUESTED"
        meta["latest_review_verdict"] = "CHANGES REQUESTED"
        self._inject(monkeypatch, states, meta)

        result = sdlc_next_skill.decide(issue_number=28981)

        assert result["decision"] == "dispatch"
        assert "unrecorded_dispatch" not in result


# ---------------------------------------------------------------------------
# #3065 Task 5 -- branch truth is three-valued and shared by both router callers
# ---------------------------------------------------------------------------

_SHA_A = "a" * 40
_SHA_B = "b" * 40


class TestResolveBranchTruth:
    """``resolve_branch_truth`` answers found / absent / indeterminate.

    The two-valued ``_check_branch_pushed`` it replaces gave the same answer —
    and the same fail-closed ``/do-patch`` dispatch — to a wrong-but-present
    recorded slug, a genuinely unpushed branch, and an unreachable remote.
    """

    def _heads(self, monkeypatch, heads):
        monkeypatch.setattr(sdlc_next_skill, "_ls_remote_heads", lambda: heads)

    def _head_sha(self, monkeypatch, sha):
        monkeypatch.setattr(sdlc_next_skill, "_fetch_pr_head_sha", lambda pr, repo=None: sha)

    def test_pr_head_uniquely_matching_a_head_is_found(self, monkeypatch):
        self._heads(
            monkeypatch,
            {"refs/heads/session/real-name": _SHA_A, "refs/heads/main": _SHA_B},
        )
        self._head_sha(monkeypatch, _SHA_A)

        truth = sdlc_next_skill.resolve_branch_truth("session/wrong-slug", pr_number=7)

        assert truth.status == sdlc_next_skill.BRANCH_TRUTH_FOUND
        # The branch is an OUTPUT of the SHA match, not the name we asked about.
        assert truth.branch == "session/real-name"

    def test_two_or_more_matches_are_indeterminate(self, monkeypatch):
        self._heads(
            monkeypatch,
            {"refs/heads/session/a": _SHA_A, "refs/heads/session/b": _SHA_A},
        )
        self._head_sha(monkeypatch, _SHA_A)

        truth = sdlc_next_skill.resolve_branch_truth("session/a", pr_number=7)

        assert truth.status == sdlc_next_skill.BRANCH_TRUTH_INDETERMINATE
        assert "ambiguous" in truth.reason

    def test_unreachable_remote_is_indeterminate(self, monkeypatch):
        self._heads(monkeypatch, None)

        truth = sdlc_next_skill.resolve_branch_truth("session/a", pr_number=7)

        assert truth.status == sdlc_next_skill.BRANCH_TRUTH_INDETERMINATE
        assert "unreachable" in truth.reason

    def test_ls_remote_raising_is_indeterminate(self, monkeypatch):
        def _boom():
            raise subprocess.TimeoutExpired(cmd="git ls-remote", timeout=10)

        monkeypatch.setattr(sdlc_next_skill, "_ls_remote_heads", _boom)

        truth = sdlc_next_skill.resolve_branch_truth("session/a", pr_number=7)

        assert truth.status == sdlc_next_skill.BRANCH_TRUTH_INDETERMINATE

    def test_listing_without_the_pr_head_is_indeterminate_not_absent(self, monkeypatch):
        """Race 1: a mid-push (or merged-and-deleted) listing is a stale negative."""
        self._heads(monkeypatch, {"refs/heads/main": _SHA_B})
        self._head_sha(monkeypatch, _SHA_A)

        truth = sdlc_next_skill.resolve_branch_truth("session/a", pr_number=7)

        assert truth.status == sdlc_next_skill.BRANCH_TRUTH_INDETERMINATE

    def test_unresolvable_pr_head_is_indeterminate(self, monkeypatch):
        self._heads(monkeypatch, {"refs/heads/session/a": _SHA_A})
        self._head_sha(monkeypatch, None)

        truth = sdlc_next_skill.resolve_branch_truth("session/a", pr_number=7)

        assert truth.status == sdlc_next_skill.BRANCH_TRUTH_INDETERMINATE

    def test_no_pr_and_branch_missing_is_absent(self, monkeypatch):
        self._heads(monkeypatch, {"refs/heads/main": _SHA_B})

        truth = sdlc_next_skill.resolve_branch_truth("session/a", pr_number=None)

        assert truth.status == sdlc_next_skill.BRANCH_TRUTH_ABSENT

    def test_no_pr_and_branch_present_is_found(self, monkeypatch):
        self._heads(monkeypatch, {"refs/heads/session/a": _SHA_A})

        truth = sdlc_next_skill.resolve_branch_truth("session/a", pr_number=None)

        assert truth.status == sdlc_next_skill.BRANCH_TRUTH_FOUND
        assert truth.branch == "session/a"

    def test_no_branch_and_no_pr_is_indeterminate_never_absent(self, monkeypatch):
        """Nothing to ask about is not evidence of absence, and guessing is #2718."""
        self._heads(monkeypatch, {"refs/heads/session/a": _SHA_A})

        truth = sdlc_next_skill.resolve_branch_truth(None, pr_number=None)

        assert truth.status == sdlc_next_skill.BRANCH_TRUTH_INDETERMINATE

    def test_the_pr_head_is_resolved_through_the_authoritative_resolver(self, monkeypatch):
        """Structural: the SHA comes from ``pr_head_resolver``, never a bare ``gh`` read.

        A stale ``gh`` head SHA is what flipped the verdict-staleness gate
        fail-open in #2895, so this asserts the call actually routes through
        ``resolve_pr_head_sha``.
        """
        seen: list[int] = []

        def _fake(pr_number, repo=None, repo_root=None, **kwargs):
            seen.append(pr_number)
            return _SHA_A

        monkeypatch.setattr("tools.pr_head_resolver.resolve_pr_head_sha", _fake)
        self._heads(monkeypatch, {"refs/heads/session/a": _SHA_A})

        truth = sdlc_next_skill.resolve_branch_truth("session/a", pr_number=99)

        assert seen == [99]
        assert truth.status == sdlc_next_skill.BRANCH_TRUTH_FOUND


class TestG8ConsumesBranchTruth:
    """G8 may fail closed on *absent* only; *indeterminate* makes it step aside."""

    def _setup(self, monkeypatch, *, slug="sdlc-3065", heads, head_sha=None):
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: None)
        monkeypatch.setattr("tools.lane_identity.resolve_lane_slug", lambda *a, **k: slug)
        monkeypatch.setattr(sdlc_next_skill, "_ls_remote_heads", lambda: heads)
        monkeypatch.setattr(sdlc_next_skill, "_fetch_pr_head_sha", lambda pr, repo=None: head_sha)
        monkeypatch.setattr(sdlc_next_skill, "_fetch_pr_state", lambda pr, repo=None: "OPEN")

    def _context(self, monkeypatch, stage_states, meta):
        return sdlc_next_skill._build_context(None, 3065, stage_states, meta)

    def test_wrong_recorded_slug_with_a_live_branch_does_not_dispatch_patch(self, monkeypatch):
        """The demonstrated red: pre-#3065 this force-dispatched ``/do-patch``.

        The recorded slug is stale (``sdlc-3065``) but the lane's work really
        is pushed, on ``session/renamed-lane``, and the PR head proves it.
        """
        from agent.sdlc_router import guard_g8_artifact_verification

        self._setup(
            monkeypatch,
            heads={"refs/heads/session/renamed-lane": _SHA_A},
            head_sha=_SHA_A,
        )
        context = self._context(
            monkeypatch, {"PATCH": "completed"}, {"pr_number": 41, "_resolved_target_repo": "o/r"}
        )

        assert context.get("stage_artifacts_verified") is not False
        assert context["branch_truth"] == sdlc_next_skill.BRANCH_TRUTH_FOUND
        assert guard_g8_artifact_verification({"PATCH": "completed"}, {}, context) is None

    def test_genuinely_unpushed_branch_still_dispatches_patch(self, monkeypatch):
        from agent.sdlc_router import guard_g8_artifact_verification

        self._setup(monkeypatch, heads={"refs/heads/main": _SHA_B})
        context = self._context(monkeypatch, {"PATCH": "completed"}, {})

        assert context["stage_artifacts_verified"] is False
        assert context["unverified_stage"] == "PATCH"
        assert context["branch_truth"] == sdlc_next_skill.BRANCH_TRUTH_ABSENT

        decision = guard_g8_artifact_verification({"PATCH": "completed"}, {}, context)
        assert decision is not None
        assert decision.skill == "/do-patch"

    def test_ambiguous_branch_truth_defers(self, monkeypatch):
        from agent.sdlc_router import guard_g8_artifact_verification

        self._setup(
            monkeypatch,
            heads={"refs/heads/session/a": _SHA_A, "refs/heads/session/b": _SHA_A},
            head_sha=_SHA_A,
        )
        context = self._context(
            monkeypatch, {"PATCH": "completed"}, {"pr_number": 41, "_resolved_target_repo": "o/r"}
        )

        assert context.get("stage_artifacts_verified") is not False
        assert context["branch_truth"] == sdlc_next_skill.BRANCH_TRUTH_INDETERMINATE
        assert context["branch_truth_reason"]
        assert guard_g8_artifact_verification({"PATCH": "completed"}, {}, context) is None

    def test_unreachable_remote_defers(self, monkeypatch):
        from agent.sdlc_router import guard_g8_artifact_verification

        self._setup(monkeypatch, heads=None)
        context = self._context(monkeypatch, {"PATCH": "completed"}, {})

        assert context.get("stage_artifacts_verified") is not False
        assert context["branch_truth"] == sdlc_next_skill.BRANCH_TRUTH_INDETERMINATE
        assert guard_g8_artifact_verification({"PATCH": "completed"}, {}, context) is None

    def test_infra_error_is_reported_as_indeterminate_not_as_a_clean_pass(self, monkeypatch):
        """The fail-open direction is right; the silence was not."""

        def _boom(stage_states, meta, issue_number, branch_truth=None):
            raise subprocess.TimeoutExpired(cmd="gh pr view", timeout=10)

        monkeypatch.setattr(sdlc_next_skill, "_verify_stage_artifacts_live", _boom)

        result = sdlc_next_skill._verify_stage_artifacts({"PATCH": "completed"}, {}, 3065)

        assert result["artifact_verification_indeterminate"] is True
        assert "TimeoutExpired" in result["artifact_verification_reason"]
        # Still fail-open for routing: G8 reads neither key.
        assert "stage_artifacts_verified" not in result


class TestCliAndInProcessPathsAgree:
    """Both ``decide_next_dispatch`` callers assemble context the same way.

    ``agent/session_runner/runner.py`` used to pass no context at all, so every
    context-fed guard was permanently inert there and the in-process answer
    could differ from the CLI's on the same lane (#3065 Cluster A).
    """

    def _lane(self):
        stage_states = {
            "ISSUE": STATUS_COMPLETED,
            "PLAN": STATUS_COMPLETED,
            "CRITIQUE": STATUS_COMPLETED,
            "BUILD": STATUS_COMPLETED,
            "TEST": STATUS_COMPLETED,
            "PATCH": STATUS_COMPLETED,
        }
        return stage_states, {}

    def _patch_world(self, monkeypatch, stage_states, meta):
        monkeypatch.setattr("tools.lane_identity.find_plan_path", lambda issue_number: None)
        monkeypatch.setattr("tools.lane_identity.resolve_lane_slug", lambda *a, **k: "sdlc-30651")
        # A genuinely unpushed lane branch: readable remote, no matching head,
        # no PR. Branch truth is *absent*, so G8 must fail closed on BOTH paths.
        monkeypatch.setattr(
            sdlc_next_skill, "_ls_remote_heads", lambda: {"refs/heads/main": _SHA_B}
        )
        monkeypatch.setattr(
            "tools.sdlc_stage_query.query_enriched",
            lambda **kwargs: {"stages": dict(stage_states), "_meta": dict(meta)},
        )

    def test_both_paths_reach_the_same_dispatch(self, monkeypatch):
        from agent.sdlc_router import decide_next_dispatch

        stage_states, meta = self._lane()
        self._patch_world(monkeypatch, stage_states, meta)

        cli_context = sdlc_next_skill.build_decision_context(30651, dict(stage_states), dict(meta))
        cli_decision = decide_next_dispatch(dict(stage_states), dict(meta), cli_context)

        runner = self._make_runner()
        _, _, in_process_skill, _, ok = runner._load_ledger(30651)

        assert ok is True
        assert getattr(cli_decision, "skill", None) == "/do-patch"
        assert in_process_skill == cli_decision.skill

    def test_in_process_path_without_the_shared_builder_would_disagree(self, monkeypatch):
        """Pins WHY the two paths agree: the empty context reaches a different answer."""
        from agent.sdlc_router import decide_next_dispatch

        stage_states, meta = self._lane()
        self._patch_world(monkeypatch, stage_states, meta)

        with_context = decide_next_dispatch(
            dict(stage_states),
            dict(meta),
            sdlc_next_skill.build_decision_context(30651, dict(stage_states), dict(meta)),
        )
        without_context = decide_next_dispatch(dict(stage_states), dict(meta))

        assert getattr(with_context, "skill", None) == "/do-patch"
        assert getattr(without_context, "skill", None) != "/do-patch"

    @staticmethod
    def _make_runner():
        from agent.session_runner.adapter import SessionRunnerAdapter
        from agent.session_runner.runner import SessionRunner

        class _Session:
            session_id = "sess-3065-agree"
            chat_id = 1
            telegram_message_id = 2
            session_events = None
            issue_number = 30651
            session_type = "eng"

            def save(self, update_fields=None):
                pass

        session = _Session()
        adapter = SessionRunnerAdapter(
            session, "test-proj", "telegram", resolve_callbacks=lambda pk, t: (None, None)
        )
        return SessionRunner(
            agent_session=session,
            adapter=adapter,
            working_dir="/tmp/wd",
            session_type="eng",
            driver=None,
            steering_pop_fn=lambda: [],
        )
