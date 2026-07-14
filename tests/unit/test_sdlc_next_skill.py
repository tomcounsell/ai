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


class TestBranchExistsCanonicalShape:
    """branch_exists must check the canonical `session/{slug}` branch shape (#2003).

    The repo NEVER creates `session/sdlc-{N}` branches (that shape is
    "fabricated" per tools/sdlc_stage_query.py) — the slug comes from the plan
    filename stem. Without a resolvable plan/slug, existence cannot be
    affirmed and branch_exists must be False.
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

    def test_true_when_canonical_slug_branch_exists(self, tmp_path, monkeypatch):
        plan = tmp_path / "my-feature-slug.md"
        plan.write_text("# Plan\n", encoding="utf-8")
        monkeypatch.setattr("tools._sdlc_utils.find_plan_path", lambda issue_number: plan)
        monkeypatch.setattr(
            "subprocess.run",
            self._fake_git("  main\n  session/my-feature-slug\n"),
        )

        context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=2003)

        assert context["branch_exists"] is True

    def test_false_when_only_fabricated_shape_present(self, tmp_path, monkeypatch):
        """A `session/sdlc-{N}` branch must NOT count — that shape is never created."""
        plan = tmp_path / "my-feature-slug.md"
        plan.write_text("# Plan\n", encoding="utf-8")
        monkeypatch.setattr("tools._sdlc_utils.find_plan_path", lambda issue_number: plan)
        monkeypatch.setattr(
            "subprocess.run",
            self._fake_git("  main\n  session/sdlc-2003\n"),
        )

        context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=2003)

        assert context["branch_exists"] is False

    def test_false_when_no_plan_resolvable(self, monkeypatch):
        """No plan → no slug → cannot affirm existence → False (never sdlc-{N})."""
        monkeypatch.setattr("tools._sdlc_utils.find_plan_path", lambda issue_number: None)
        monkeypatch.setattr(
            "subprocess.run",
            self._fake_git("  main\n  session/sdlc-2003\n  session/other-slug\n"),
        )

        context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=2003)

        assert context["branch_exists"] is False

    def test_false_when_branch_absent(self, tmp_path, monkeypatch):
        plan = tmp_path / "my-feature-slug.md"
        plan.write_text("# Plan\n", encoding="utf-8")
        monkeypatch.setattr("tools._sdlc_utils.find_plan_path", lambda issue_number: plan)
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

        assert sdlc_next_skill._check_plan_committed_on_main("sdlc-2078-fixture") is True

    def test_plan_committed_check_false_when_plan_absent_in_target(self, tmp_path, monkeypatch):
        target = tmp_path / "target"
        target.mkdir()
        self._init_fixture_repo(target, "sdlc-2078-fixture")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("SDLC_TARGET_REPO", str(target))

        assert sdlc_next_skill._check_plan_committed_on_main("no-such-slug") is False

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
            "tools._sdlc_utils.find_plan_path",
            lambda issue_number: target / "docs" / "plans" / "sdlc-2078-fixture.md",
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
        monkeypatch.setattr("tools._sdlc_utils.find_plan_path", lambda issue_number: None)
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
        monkeypatch.setattr("tools._sdlc_utils.find_plan_path", lambda issue_number: None)
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
        monkeypatch.setattr("tools._sdlc_utils.find_plan_path", lambda issue_number: None)
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
        monkeypatch.setattr("tools._sdlc_utils.find_plan_path", lambda issue_number: None)
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
        monkeypatch.setattr("tools._sdlc_utils.find_plan_path", lambda issue_number: plan_path)

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
        monkeypatch.setattr("tools._sdlc_utils.find_plan_path", lambda issue_number: plan_path)

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
        monkeypatch.setattr("tools._sdlc_utils.find_plan_path", lambda issue_number: None)

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
        monkeypatch.setattr("tools._sdlc_utils.find_plan_path", lambda issue_number: None)

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
        monkeypatch.setattr("tools._sdlc_utils.find_plan_path", lambda issue_number: None)

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
        monkeypatch.setattr("tools._sdlc_utils.find_plan_path", lambda issue_number: None)
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
        monkeypatch.setattr("tools._sdlc_utils.find_plan_path", lambda issue_number: None)
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

        monkeypatch.setattr("tools._sdlc_utils.find_plan_path", lambda issue_number: None)
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
        monkeypatch.setattr("tools._sdlc_utils.find_plan_path", lambda issue_number: None)
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
        monkeypatch.setattr("tools._sdlc_utils.find_plan_path", lambda issue_number: None)
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
        monkeypatch.setattr("tools._sdlc_utils.find_plan_path", lambda issue_number: None)
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
