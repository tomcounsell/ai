"""Tests for the direct-to-main issue-disposition gate (#2540).

Two layers:

* :func:`find_violation` -- the pure decision, exercised without a repo. The
  load-bearing cases are the three real commits tabulated in issue #2540,
  because those are the behaviours the gate exists to change.
* The hooks themselves -- subprocessed against an ephemeral throwaway repo, so
  the wiring (executable bit, ``core.hooksPath``, git's stdin protocols) is
  proven rather than assumed. Mirrors the approach in
  ``tests/unit/test_session_branch_guard.py``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_issue_disposition import (  # noqa: E402
    find_violation,
    has_disposition,
    in_scope_paths,
    is_generated_message,
)

CODE = ["bridge/telegram_bridge.py"]
SKILL_MD = [".claude/skills/sdlc/SKILL.md"]
PLAN = ["docs/archive/plans-completed/foo.md"]

# The literal stage of a `Migrate completed plan: X` commit (#2878). The mover
# performs a rename, so git stages BOTH endpoints: the deletion under
# `docs/plans/` and the addition under the archive. If either prefix falls out
# of the exemption the mover's own commit is refused by the commit-msg hook,
# which breaks /do-merge's plan-completion step.
MIGRATION_STAGE = ["docs/plans/foo.md", "docs/archive/plans-completed/foo.md"]


class TestRealCommitsFromIssue2540:
    """The issue's own table. Each row is a commit that actually landed."""

    def test_9fe58f45d_hotfix_with_no_linkage_is_blocked(self):
        """ "Hotfix: remove the watchdog crash-storm alert delivery path" carried
        no issue reference. It fully obsoleted #2479 and resolved #2429's Group
        C; both stayed open for two days until a manual audit caught it."""
        assert (
            find_violation(
                "Hotfix: remove the watchdog crash-storm alert delivery path",
                "main",
                CODE,
            )
            is not None
        )

    def test_f695d2bed_bare_mentions_on_skill_files_are_blocked(self):
        """Four bare `#N` mentions, no keyword; three of the four issues are
        still open with no record of whether that was deliberate. The commit
        touched only `.md` skill files, which is why prose is NOT exempt --
        only `docs/plans/` is."""
        assert (
            find_violation(
                "Hotfix SDLC skill-file defects: #2493, #2492, #2465, #2419",
                "main",
                SKILL_MD,
            )
            is not None
        )

    def test_ac3a87d51_closing_keyword_is_allowed(self):
        """The working counter-example: the mechanism already works on the
        hotfix path when used. The gate must not disturb it."""
        assert (
            find_violation(
                "Fix #2489: clear `deferred_self_draft_pending` on successful resend",
                "main",
                CODE,
            )
            is None
        )


class TestAcceptedDispositions:
    @pytest.mark.parametrize(
        "message",
        [
            "Do a thing\n\nCloses #2540",
            "Do a thing\n\nFixes #2540",
            "Do a thing\n\nResolves #2540",
            "Fix #2489: subject-line form",
            "closes #1 lowercase",
            "Do a thing\n\nRefs #2429",
            "Do a thing\n\nRef #2429",
            "ui(dashboard): reflow the header\n\nNo-issue: cosmetic tweak",
            "tweak\n\nno-issue: whitespace",
        ],
    )
    def test_accepted(self, message):
        assert has_disposition(message) is True
        assert find_violation(message, "main", CODE) is None

    @pytest.mark.parametrize(
        "message",
        [
            "Hotfix: do a thing",
            "Hotfix touching #2493 and #2492",  # bare mentions carry no semantics
            "See issue 2540",  # no `#`
            "tweak\n\nNo-issue:",  # opt-out with no reason says nothing
            "tweak\n\nNo-issue:   ",
        ],
    )
    def test_rejected(self, message):
        assert has_disposition(message) is False
        assert find_violation(message, "main", CODE) is not None


class TestScope:
    def test_feature_branch_is_never_gated(self):
        """The PR path already enforces linkage via the PR body (do-merge).
        Gating side-branch commits would double-charge for it."""
        assert find_violation("anything at all", "session/ws-sdlc", CODE) is None

    def test_detached_head_is_not_gated(self):
        assert find_violation("anything at all", None, CODE) is None

    def test_plan_only_commit_is_exempt(self):
        """`Migrate completed plan: X` is the bulk of legitimate
        direct-to-main traffic and never resolves an issue by itself."""
        assert find_violation("Migrate completed plan: foo", "main", PLAN) is None

    def test_migration_commit_rename_pair_is_exempt(self):
        """The mover's own commit must pass this gate (#2878).

        `migrate_plan_to_completed()` git-mv's a plan out of `docs/plans/` into
        `docs/archive/plans-completed/` and commits on `main` with a fixed
        message that carries no disposition. Git stages both endpoints of the
        rename, so exempting only the source prefix would refuse the commit and
        leave the tree with a staged-but-uncommitted move.
        """
        assert find_violation("Migrate completed plan: foo", "main", MIGRATION_STAGE) is None

    def test_plan_plus_code_is_not_exempt(self):
        """The exemption is per-commit, not per-file: one code file in the
        stage pulls the whole commit into scope."""
        assert find_violation("Plan (x): notes", "main", PLAN + CODE) is not None

    def test_empty_stage_is_not_gated(self):
        assert find_violation("whatever", "main", []) is None

    def test_842212ace_plan_only_closing_keyword_is_blocked(self):
        """The real commit that closed #2783 while the defect was live (#2890).

        A plan-only commit needs no disposition, but a closing keyword in its
        body still fires on push. `Refs #2836` was present and did not help:
        GitHub scans the whole message.
        """
        message = (
            "Plan revision (verification-runner-convergence): address critique "
            "findings (Refs #2836)\n\n"
            "Open Questions resolved into Decisions; PR carries Closes #2783."
        )
        assert find_violation(message, "main", PLAN) is not None

    @pytest.mark.parametrize(
        "keyword",
        ["Closes #123", "closed #123", "Fixes #123", "fixed #123", "Resolves #123"],
    )
    def test_plan_only_blocks_every_github_closing_keyword(self, keyword):
        """Blocking must track GitHub's keyword set, not just `Closes`."""
        assert find_violation(f"Plan revision: notes\n\n{keyword}", "main", PLAN) is not None

    @pytest.mark.parametrize(
        "message",
        [
            "Migrate completed plan: foo",
            "Plan revision (x): the PR body carries a closing keyword for 123",
            "Plan critique round 3 (x): findings table (Refs #123)",
            "Plan revision (x): close all six critique concerns (#123)",
        ],
    )
    def test_plan_only_non_firing_references_still_pass(self, message):
        """The narrow rule: only a *firing* keyword is refused. Bare mentions,
        `Refs`, and prose like "close ... concerns (#N)" -- which GitHub does
        not honour because the keyword is not adjacent to the reference -- stay
        exempt, or the gate would block most legitimate plan traffic.
        """
        assert find_violation(message, "main", PLAN) is None

    def test_plan_plus_code_may_still_close(self):
        """The new rule is scoped to plan-*only* commits. A commit carrying real
        code may legitimately close an issue, which is the #2540 contract."""
        assert find_violation("Fix the thing\n\nCloses #123", "main", PLAN + CODE) is None

    def test_in_scope_paths_filters_only_the_plans_prefix(self):
        paths = ["docs/plans/a.md", "docs/features/b.md", "docs/plansible.py"]
        assert in_scope_paths(paths) == ["docs/features/b.md", "docs/plansible.py"]

    @pytest.mark.parametrize(
        "message",
        [
            "Merge branch 'main' into session/x",
            'Revert "Do a thing"',
            "fixup! Do a thing",
            "squash! Do a thing",
        ],
    )
    def test_git_generated_messages_are_exempt(self, message):
        """Demanding a trailer on a message git wrote fights the tool."""
        assert is_generated_message(message) is True
        assert find_violation(message, "main", CODE) is None

    def test_comment_lines_do_not_mask_the_subject(self):
        """A commit template's leading `#` comments must not be mistaken for
        the subject when classifying generated messages."""
        assert is_generated_message("# please enter a message\nHotfix: thing") is False


class TestBlockMessage:
    def test_names_the_three_remedies_and_the_offending_files(self):
        violation = find_violation("Hotfix: thing", "main", CODE)
        assert violation is not None
        assert "Closes #" in violation
        assert "Refs #" in violation
        assert "No-issue:" in violation
        assert "bridge/telegram_bridge.py" in violation
        assert "--no-verify" in violation

    def test_long_file_lists_are_truncated(self):
        paths = [f"pkg/mod_{i}.py" for i in range(25)]
        violation = find_violation("Hotfix: thing", "main", paths)
        assert violation is not None
        assert "and 15 more" in violation


# ---------------------------------------------------------------------------
# Hook wiring, against a real throwaway repo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hook", ["commit-msg", "pre-push"])
def test_hook_is_committed_executable(hook):
    """git silently IGNORES a non-executable hook, printing only a `hint:` line.

    This is not hypothetical: `.githooks/pre-push` was first committed 100644
    and the push leg was dead on arrival. The repo-fixture tests below did not
    catch it because the fixture chmods its copies, so the mode in the index is
    asserted here directly. A gate that does not run looks exactly like a gate
    that passes.
    """
    proc = subprocess.run(
        ["git", "ls-files", "-s", f".githooks/{hook}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    mode = proc.stdout.split()[0]
    assert mode == "100755", f".githooks/{hook} is committed as {mode}; git will ignore it"


@pytest.fixture
def repo(tmp_path):
    """A throwaway repo with a bare remote and both hooks installed."""
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True)

    hooks = work / ".githooks"
    hooks.mkdir()
    for name in ("commit-msg", "pre-push"):
        dest = hooks / name
        dest.write_text((REPO_ROOT / ".githooks" / name).read_text())
        dest.chmod(0o755)
    (work / "scripts").mkdir()
    (work / "scripts" / "check_issue_disposition.py").write_text(
        (REPO_ROOT / "scripts" / "check_issue_disposition.py").read_text()
    )
    (work / "bridge").mkdir()
    (work / "docs" / "plans").mkdir(parents=True)

    def git(*args, **kw):
        return subprocess.run(["git", *args], cwd=work, capture_output=True, text=True, **kw)

    git("config", "core.hooksPath", ".githooks")
    git("config", "user.email", "t@e.com")
    git("config", "user.name", "T")
    git("symbolic-ref", "HEAD", "refs/heads/main")
    (work / "README.md").write_text("seed\n")
    git("add", "-A")
    git("commit", "-qm", "seed\n\nNo-issue: repo seed")
    git("push", "-q", "origin", "main")
    return work, git


class TestCommitMsgHook:
    def test_undisposed_code_commit_on_main_is_refused(self, repo):
        work, git = repo
        (work / "bridge" / "a.py").write_text("x = 1\n")
        git("add", "-A")
        proc = git("commit", "-m", "Hotfix: remove the alert delivery path")
        assert proc.returncode != 0
        assert "COMMIT BLOCKED (#2540)" in proc.stderr

    def test_heredoc_message_is_still_inspected(self, repo):
        """The `-F -` form carries the message on stdin, invisible to any hook
        that inspects the command string. The commit-msg stage sees it. This
        is the case that decided the enforcement point."""
        work, git = repo
        (work / "bridge" / "a.py").write_text("x = 1\n")
        git("add", "-A")
        proc = subprocess.run(
            ["git", "commit", "-F", "-"],
            cwd=work,
            input="Hotfix SDLC skill-file defects: #2493, #2492\n",
            capture_output=True,
            text=True,
        )
        assert proc.returncode != 0
        assert "COMMIT BLOCKED (#2540)" in proc.stderr

    def test_disposed_commit_is_allowed(self, repo):
        work, git = repo
        (work / "bridge" / "a.py").write_text("x = 1\n")
        git("add", "-A")
        proc = git("commit", "-m", "Hotfix: remove the alert path\n\nCloses #2479")
        assert proc.returncode == 0, proc.stderr

    def test_plan_only_commit_is_allowed(self, repo):
        work, git = repo
        (work / "docs" / "plans" / "foo.md").write_text("plan\n")
        git("add", "-A")
        proc = git("commit", "-m", "Migrate completed plan: foo")
        assert proc.returncode == 0, proc.stderr


class TestPrePushHook:
    def test_side_branch_commit_pushed_to_main_is_refused(self, repo):
        """The worktree hotfix shape: commit on session/{slug}, then
        `git push origin HEAD:main`. The commit-msg leg never sees these
        because at commit time the branch was not main."""
        work, git = repo
        git("checkout", "-q", "-b", "session/demo")
        (work / "bridge" / "b.py").write_text("z = 3\n")
        git("add", "-A")
        commit = git("commit", "-m", "Fix the thing nobody linked")
        assert commit.returncode == 0, "the commit itself is fine; only the push lands on main"

        proc = git("push", "origin", "HEAD:main")
        assert proc.returncode != 0
        assert "PUSH BLOCKED (#2540)" in proc.stderr

    def test_side_branch_push_to_its_own_ref_is_untouched(self, repo):
        work, git = repo
        git("checkout", "-q", "-b", "session/demo")
        (work / "bridge" / "b.py").write_text("z = 3\n")
        git("add", "-A")
        git("commit", "-m", "Fix the thing nobody linked")
        proc = git("push", "origin", "HEAD:refs/heads/session/demo")
        assert proc.returncode == 0, proc.stderr

    def test_disposed_side_branch_commit_pushes_to_main(self, repo):
        work, git = repo
        git("checkout", "-q", "-b", "session/demo")
        (work / "bridge" / "b.py").write_text("z = 3\n")
        git("add", "-A")
        git("commit", "-m", "Fix the thing\n\nCloses #1234")
        proc = git("push", "origin", "HEAD:main")
        assert proc.returncode == 0, proc.stderr
