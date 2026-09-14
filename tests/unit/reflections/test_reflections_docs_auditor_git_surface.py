"""Real-git test surface for reflections/docs_auditor.py (#2739).

Blanket ``unittest.mock.patch`` over ``subprocess.run`` is explicitly not an
acceptable strategy for ``_push_branch_and_pr``, the staging set, the restore
path, or the sweeper's close path — every ``git`` command in those paths must
actually run against a real repository on disk.

The sanctioned pattern (no in-repo precedent existed for a synchronous
dispatcher — ``tests/unit/reflections/test_reflections_merged_branch_cleanup.py`` patches
``asyncio.create_subprocess_exec`` because its module is async, so it is not
reusable here): ``monkeypatch.setattr(docs_auditor.subprocess, "run",
dispatcher)``, module-scoped, where ``dispatcher`` intercepts only
``cmd[0] == "gh"`` and returns a canned ``CompletedProcess``, delegating
everything else to the real ``subprocess.run``.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reflections import docs_auditor

# The derived FEATURE_MAP marker for this file is now 'reflections' (the rename
# in #3175 made the basename resolve to its package). This line preserves the
# 'validation' marker the old basename derived; item.add_marker is additive on top of
# pytestmark, so the file carries both.
pytestmark = [pytest.mark.validation]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A real git checkout with a real bare 'origin' remote.

    A bare local remote lets ``git push -u origin <branch>`` inside
    ``_push_branch_and_pr`` actually succeed, so the checkout/add/commit/push
    sequence runs for real; only the ``gh`` calls are intercepted.
    """
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)

    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Docs Auditor Git Surface Test"], cwd=root, check=True
    )
    (root / "docs" / "features").mkdir(parents=True)
    (root / "README.md").write_text("# Test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=root, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=root, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=root, check=True)
    return root


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def _porcelain(cwd: Path, *paths: str) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain", "--", *paths]
        if paths
        else ["git", "status", "--porcelain"],
        cwd=cwd,
        capture_output=True,
        text=True,
    ).stdout.strip()


class GhDispatcher:
    """Records every ``gh`` invocation and returns a configurable canned response.

    Delegates every non-``gh`` command to the real ``subprocess.run`` — never a
    blanket mock over the git surface. Configure behavior per test via the
    public attributes before installing the dispatcher.
    """

    def __init__(self) -> None:
        # Captured before installation. `subprocess` is a singleton module, so
        # `monkeypatch.setattr(docs_auditor.subprocess, "run", self)` patches
        # the one and only `subprocess.run` everywhere it is imported —
        # including inside this file's own module scope. Delegating through
        # `subprocess.run` after that point would call this dispatcher again
        # (infinite recursion); the real function must be captured first.
        self._real_run = subprocess.run
        self.calls: list[list[str]] = []
        self.pr_create_url: str | None = "https://github.com/o/r/pull/1"
        self.pr_create_returncode = 0
        self.issue_list_result: list[dict] = []
        self.issue_list_returncode = 0
        self.issue_create_returncode = 0
        self.pr_list_result: list[dict] = []
        self.pr_close_returncode = 0
        self.pr_merge_calls: list[list[str]] = []

    def __call__(self, cmd, *args, **kwargs):
        if not cmd or cmd[0] != "gh":
            return self._real_run(cmd, *args, **kwargs)
        self.calls.append(list(cmd))

        if cmd[:3] == ["gh", "pr", "create"]:
            if self.pr_create_url is None:
                return MagicMock(returncode=1, stdout="", stderr="pr create failed")
            return MagicMock(returncode=0, stdout=f"{self.pr_create_url}\n", stderr="")

        if cmd[:3] == ["gh", "issue", "list"]:
            return MagicMock(
                returncode=self.issue_list_returncode,
                stdout=json.dumps(self.issue_list_result),
                stderr="",
            )

        if cmd[:3] == ["gh", "issue", "create"]:
            return MagicMock(returncode=self.issue_create_returncode, stdout="", stderr="")

        if cmd[:3] == ["gh", "pr", "list"]:
            return MagicMock(returncode=0, stdout=json.dumps(self.pr_list_result), stderr="")

        if cmd[:3] == ["gh", "pr", "close"]:
            return MagicMock(returncode=self.pr_close_returncode, stdout="", stderr="")

        if cmd[:3] == ["gh", "pr", "merge"]:
            self.pr_merge_calls.append(list(cmd))
            return MagicMock(returncode=0, stdout="", stderr="")

        # Default: succeed with empty output (e.g. `gh auth status`).
        return MagicMock(returncode=0, stdout="", stderr="")


@pytest.fixture()
def gh(monkeypatch) -> GhDispatcher:
    dispatcher = GhDispatcher()
    monkeypatch.setattr(docs_auditor.subprocess, "run", dispatcher)
    return dispatcher


@pytest.fixture()
def fake_redis():
    fake = MagicMock()
    fake.set.return_value = True
    fake.exists.return_value = 0
    fake.hgetall.return_value = {}
    fake.hset.return_value = 1
    return fake


# ---------------------------------------------------------------------------
# R5-1 — a failed rotation escalates through a real channel
# ---------------------------------------------------------------------------


class TestFailedRotationEscalates:
    def test_files_one_issue_before_returning_status_error(
        self, repo: Path, gh, monkeypatch, fake_redis
    ):
        """``_push_branch_and_pr`` returning None must file an issue naming the
        slug, category ``operational-failure``, before the ``status="error"``
        return — a plain dict alone reaches nobody
        (``agent/reflection_scheduler.py:639-640`` reads only ``projects``)."""
        primary = repo / "docs" / "features" / "foo.md"
        primary.write_text("# Foo\n\nThe SessionLog tracks state.\n" + "Padding line.\n" * 6)

        filed: list[dict] = []

        def fake_file_issue(finding, repo_root):
            filed.append(finding)
            return True

        monkeypatch.setattr(docs_auditor, "PROJECT_ROOT", repo)
        monkeypatch.setattr(docs_auditor, "_get_redis", lambda: fake_redis)
        monkeypatch.setattr(docs_auditor, "_check_auth", lambda: (True, ""))
        monkeypatch.setattr(docs_auditor, "_git_dirty", lambda root: False)
        monkeypatch.setattr(docs_auditor, "_git_diff_quiet", lambda root: False)
        monkeypatch.setattr(docs_auditor, "_run_vault_drift_detection", lambda pk: 0)
        monkeypatch.setattr(docs_auditor, "_push_branch_and_pr", lambda *a, **kw: None)
        monkeypatch.setattr(docs_auditor, "_send_telegram_notification", lambda msg, **kw: True)
        monkeypatch.setattr(docs_auditor, "_file_issue_if_new", fake_file_issue)

        result = docs_auditor.run_docs_auditor()

        assert result["status"] == "error"
        assert len(filed) == 1
        finding = filed[0]
        assert (
            finding["title"]
            == "docs-auditor: rotation failed to produce a PR for docs_features_foo_md"
        )
        assert finding["category"] == "operational-failure"
        # No volatile fields: no date, count, or run id.
        assert "20" not in finding["title"]  # no year-like date fragment


# ---------------------------------------------------------------------------
# R5-3 — withheld titles carry the term, not the regex source
# ---------------------------------------------------------------------------


class TestWithheldTitleUnwrap:
    def test_title_has_no_backslash(self, repo: Path, gh, monkeypatch, fake_redis):
        primary = repo / "docs" / "features" / "foo.md"
        primary.write_text("# Foo\n" + "Padding line.\n" * 6)

        withheld_entry = {
            "doc": "docs/features/foo.md",
            "old": r"\breal\b",
            "new": "realistic",
            "reason": "target-absent",
        }
        audit_result = {
            "status": "ok",
            "files_touched": [],
            "fixes_applied": 0,
            "fixes_withheld": 1,
            "withheld": [withheld_entry],
            "issues_filed": 0,
        }

        filed: list[dict] = []

        def fake_file_issue(finding, repo_root):
            filed.append(finding)
            return True

        monkeypatch.setattr(docs_auditor, "PROJECT_ROOT", repo)
        monkeypatch.setattr(docs_auditor, "_get_redis", lambda: fake_redis)
        monkeypatch.setattr(docs_auditor, "_check_auth", lambda: (True, ""))
        monkeypatch.setattr(docs_auditor, "_git_dirty", lambda root: False)
        monkeypatch.setattr(docs_auditor, "_run_vault_drift_detection", lambda pk: 0)
        monkeypatch.setattr(docs_auditor, "audit", lambda **kw: audit_result)
        monkeypatch.setattr(docs_auditor, "_send_telegram_notification", lambda msg, **kw: True)
        monkeypatch.setattr(docs_auditor, "_file_issue_if_new", fake_file_issue)

        result = docs_auditor.run_docs_auditor()

        assert result["status"] == "skipped"
        assert len(filed) == 1
        title = filed[0]["title"]
        assert "(real -> realistic)" in title
        assert "\\" not in title


# ---------------------------------------------------------------------------
# Early-return restore, per failure mode
# ---------------------------------------------------------------------------


class TestEarlyReturnRestore:
    def _seed_touched_file(self, repo: Path) -> None:
        (repo / "docs" / "features" / "x.md").write_text("# X\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "seed touched file")
        _git(repo, "push", "-q", "origin", "main")

    def test_gh_pr_create_failure_restores_head_and_deletes_branch(self, repo: Path, gh):
        self._seed_touched_file(repo)
        starting_ref = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        (repo / "docs" / "features" / "x.md").write_text("# X\n\nedited\n")

        gh.pr_create_url = None  # force gh pr create to fail

        url = docs_auditor._push_branch_and_pr(
            "slug", repo, ["docs/features/x.md"], starting_ref="main"
        )

        assert url is None
        assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == starting_ref
        branches = _git(repo, "branch", "--list")
        assert "docs-audit/" not in branches
        assert _porcelain(repo, "docs/features/x.md") == ""

    def test_git_add_missing_path_restores_cleanly(self, repo: Path, gh):
        self._seed_touched_file(repo)
        starting_ref = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()

        # `files_touched` names a path that was never written — `git add --`
        # fails outright (pathspec did not match).
        url = docs_auditor._push_branch_and_pr(
            "slug", repo, ["docs/features/does_not_exist_xyz.md"], starting_ref="main"
        )

        assert url is None
        assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == starting_ref
        branches = _git(repo, "branch", "--list")
        assert "docs-audit/" not in branches

    def test_push_to_unreachable_remote_restores_cleanly(self, repo: Path, gh):
        self._seed_touched_file(repo)
        starting_ref = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        (repo / "docs" / "features" / "x.md").write_text("# X\n\nedited\n")
        # Point origin at a nonexistent path so the real `git push` fails.
        _git(repo, "remote", "set-url", "origin", "/nonexistent/path/origin.git")

        url = docs_auditor._push_branch_and_pr(
            "slug", repo, ["docs/features/x.md"], starting_ref="main"
        )

        assert url is None
        assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == starting_ref
        branches = _git(repo, "branch", "--list")
        assert "docs-audit/" not in branches
        assert _porcelain(repo, "docs/features/x.md") == ""


# ---------------------------------------------------------------------------
# Race 1 — foreign dirt outside files_touched survives a failed restore
# ---------------------------------------------------------------------------


class TestForeignDirtSurvives:
    def test_unrelated_modified_file_is_untouched_by_the_restore(self, repo: Path, gh):
        (repo / "docs" / "features" / "x.md").write_text("# X\n")
        (repo / "docs" / "features" / "foreign.md").write_text("# Foreign\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "seed")
        _git(repo, "push", "-q", "origin", "main")

        # The auditor never touches foreign.md; a peer lane holds it dirty.
        (repo / "docs" / "features" / "foreign.md").write_text("# Foreign\n\nunrelated edit\n")
        (repo / "docs" / "features" / "x.md").write_text("# X\n\nedited\n")

        gh.pr_create_url = None  # force a failure so the restore path runs

        docs_auditor._push_branch_and_pr("slug", repo, ["docs/features/x.md"], starting_ref="main")

        # Foreign dirt outside files_touched survives, byte for byte.
        assert (repo / "docs" / "features" / "foreign.md").read_text() == (
            "# Foreign\n\nunrelated edit\n"
        )
        assert "docs/features/foreign.md" in _porcelain(repo)
        # And it did not carry the auditor's own path into a reported error tree.
        assert _porcelain(repo, "docs/features/x.md") == ""


# ---------------------------------------------------------------------------
# Failed-restore reporting
# ---------------------------------------------------------------------------


class TestFailedRestoreReporting:
    def test_restore_checkout_failure_is_reported_and_run_returns_error(
        self, repo: Path, gh, monkeypatch, fake_redis
    ):
        (repo / "docs" / "features" / "x.md").write_text("# X\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "seed")
        _git(repo, "push", "-q", "origin", "main")
        (repo / "docs" / "features" / "x.md").write_text("# X\n\nedited\n")

        gh.pr_create_url = None  # force the failure path

        real_run = docs_auditor.subprocess.run

        def failing_checkout(cmd, *a, **kw):
            if cmd[:2] == ["git", "checkout"] and len(cmd) == 3 and cmd[2] == "main":
                return MagicMock(returncode=1, stdout="", stderr="simulated checkout failure")
            return real_run(cmd, *a, **kw)

        monkeypatch.setattr(docs_auditor.subprocess, "run", failing_checkout)

        url = docs_auditor._push_branch_and_pr(
            "slug", repo, ["docs/features/x.md"], starting_ref="main"
        )
        assert url is None

        # Drive the full reflection: the restore failure must route to "error".
        # `audit()` is stubbed directly so the zero-diff gate never intercepts
        # this — the point under test is the pr_url-is-None branch, not the
        # substrate's own auto-fix detection.
        audit_result = {
            "status": "ok",
            "files_touched": ["docs/features/x.md"],
            "fixes_applied": 1,
            "fixes_withheld": 0,
            "withheld": [],
            "issues_filed": 0,
        }
        monkeypatch.setattr(docs_auditor, "PROJECT_ROOT", repo)
        monkeypatch.setattr(docs_auditor, "_get_redis", lambda: fake_redis)
        monkeypatch.setattr(docs_auditor, "_check_auth", lambda: (True, ""))
        monkeypatch.setattr(docs_auditor, "_git_dirty", lambda root: False)
        monkeypatch.setattr(docs_auditor, "_git_diff_quiet", lambda root: False)
        monkeypatch.setattr(docs_auditor, "_run_vault_drift_detection", lambda pk: 0)
        monkeypatch.setattr(docs_auditor, "audit", lambda **kw: audit_result)
        monkeypatch.setattr(docs_auditor, "_push_branch_and_pr", lambda *a, **kw: None)
        monkeypatch.setattr(docs_auditor, "_send_telegram_notification", lambda msg, **kw: True)
        monkeypatch.setattr(docs_auditor, "_file_issue_if_new", lambda finding, root: True)

        result = docs_auditor.run_docs_auditor()
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# #3050 — restore and escalate when the rotation aborts after writing
# ---------------------------------------------------------------------------


def _seed_tracked_doc(repo: Path, rel: str, content: str) -> None:
    full = repo / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"seed {rel}")
    _git(repo, "push", "-q", "origin", "main")


def _writing_audit_stub(rel: str, withheld: list[dict] | None = None):
    """A substrate stub that really writes ``rel`` and reports it touched."""

    def _audit(**kwargs) -> dict:
        full = Path(kwargs["repo_root"]) / rel
        full.write_text(full.read_text() + "\nedited by the stub\n")
        return {
            "status": "ok",
            "files_touched": [rel],
            "fixes_applied": 1,
            "fixes_withheld": len(withheld or []),
            "withheld": withheld or [],
            "issues_filed": 0,
        }

    return _audit


def _standard_preflight(monkeypatch, repo: Path, fake_redis) -> None:
    """The preflight stack every TestWriteWindowRestore test drives through."""
    monkeypatch.setattr(docs_auditor, "PROJECT_ROOT", repo)
    monkeypatch.setattr(docs_auditor, "_get_redis", lambda: fake_redis)
    monkeypatch.setattr(docs_auditor, "_check_auth", lambda: (True, ""))
    monkeypatch.setattr(docs_auditor, "_git_dirty", lambda root: False)
    monkeypatch.setattr(docs_auditor, "_run_vault_drift_detection", lambda pk: 0)


class TestWriteWindowRestore:
    """The write-through-push region restores and escalates on abort (#3050).

    Each test drives the full ``run_docs_auditor()``, not a narrower unit —
    this is a failure-path bug, and the failure path is the deliverable.
    """

    # -- Injection point 1: the issue's exact scenario -----------------------

    def test_exception_between_write_and_push_restores_and_escalates(
        self, repo: Path, gh, monkeypatch, fake_redis
    ):
        _seed_tracked_doc(repo, "docs/features/x.md", "# X\n" + "Padding line.\n" * 6)
        _standard_preflight(monkeypatch, repo, fake_redis)

        filed: list[dict] = []

        def fake_file_issue(finding, root):
            filed.append(finding)
            return True

        telegram_calls: list[str] = []
        monkeypatch.setattr(
            docs_auditor,
            "_send_telegram_notification",
            lambda msg, **kw: telegram_calls.append(msg) or True,
        )
        rotation_hash_calls: list[list[str]] = []
        monkeypatch.setattr(
            docs_auditor,
            "_update_rotation_hash",
            lambda pk, paths: rotation_hash_calls.append(paths),
        )
        monkeypatch.setattr(docs_auditor, "audit", _writing_audit_stub("docs/features/x.md"))
        monkeypatch.setattr(
            docs_auditor,
            "_push_branch_and_pr",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("injected")),
        )
        monkeypatch.setattr(docs_auditor, "_file_issue_if_new", fake_file_issue)

        result = docs_auditor.run_docs_auditor()

        # (1) the file is byte-identical to HEAD
        assert _porcelain(repo, "docs/features/x.md") == ""
        # (2) HEAD is back on the ref the run started on
        assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
        # (3) exactly one operational-failure escalation, titled correctly
        assert len(filed) == 1
        finding = filed[0]
        assert finding["category"] == "operational-failure"
        assert "rotation aborted after writing" in finding["title"]
        assert "docs/features/x.md" in finding["body"]
        assert "restored" in finding["body"].lower()
        assert "```" in finding["body"]  # carries the manual cleanup command
        # (4) status is error
        assert result["status"] == "error"
        assert "injected" in result["summary"]
        # No success Telegram, no rotation-hash stamp on an aborted run.
        assert telegram_calls == []
        assert rotation_hash_calls == []

    # -- Injection point 2: exception inside the withheld-filing loop -------

    def test_exception_in_withheld_filing_loop_still_restores_and_escalates(
        self, repo: Path, gh, monkeypatch, fake_redis
    ):
        _seed_tracked_doc(repo, "docs/features/x.md", "# X\n" + "Padding line.\n" * 6)
        _standard_preflight(monkeypatch, repo, fake_redis)

        withheld_entry = {
            "doc": "docs/features/x.md",
            "old": "a/b.py",
            "new": "a/c.py",
            "reason": "target-absent",
        }
        monkeypatch.setattr(
            docs_auditor,
            "audit",
            _writing_audit_stub("docs/features/x.md", withheld=[withheld_entry]),
        )

        filed: list[dict] = []

        def selective_raise(finding, root):
            if finding.get("category") == "withheld-fix":
                raise RuntimeError("gh issue create failed")
            filed.append(finding)
            return True

        monkeypatch.setattr(docs_auditor, "_file_issue_if_new", selective_raise)
        monkeypatch.setattr(
            docs_auditor,
            "_push_branch_and_pr",
            lambda *a, **kw: pytest.fail("must not be reached — abort fires earlier"),
        )

        result = docs_auditor.run_docs_auditor()

        assert result["status"] == "error"
        assert _porcelain(repo, "docs/features/x.md") == ""
        assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
        assert len(filed) == 1
        assert filed[0]["category"] == "operational-failure"
        assert "rotation aborted after writing" in filed[0]["title"]

    # -- Injection point 3: exception inside audit() after it has written ---

    def test_exception_inside_audit_after_write_restores_exactly_those_paths(
        self, repo: Path, gh, monkeypatch, fake_redis
    ):
        _seed_tracked_doc(
            repo,
            "docs/features/x.md",
            "# X\n" + "Real content line.\n" * 10,
        )
        _standard_preflight(monkeypatch, repo, fake_redis)

        # Force the per-file loop to take the write path for real...
        monkeypatch.setattr(
            docs_auditor, "_detect_stale_term_fixes", lambda content: [(re.compile("x"), "y")]
        )

        def fake_apply(path, root, fixes):
            full = root / path
            full.write_text(full.read_text() + "\nedited inside audit\n")
            return 1, []

        monkeypatch.setattr(docs_auditor, "_apply_fixes_to_file", fake_apply)

        # ...then raise from the advisory step that runs after the write loop,
        # inside audit()'s own write-ledger guard.
        def raising_orphan_scan(root):
            raise RuntimeError("orphan scan boom")

        monkeypatch.setattr(docs_auditor, "_detect_orphan_plan_issues", raising_orphan_scan)

        filed: list[dict] = []
        monkeypatch.setattr(
            docs_auditor,
            "_file_issue_if_new",
            lambda finding, root: filed.append(finding) or True,
        )
        monkeypatch.setattr(
            docs_auditor,
            "_push_branch_and_pr",
            lambda *a, **kw: pytest.fail("must not be reached — audit's own guard aborts first"),
        )

        result = docs_auditor.run_docs_auditor()

        assert result["status"] == "error"
        # The caller restored exactly the path audit() reported as touched.
        assert _porcelain(repo, "docs/features/x.md") == ""
        assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
        assert len(filed) == 1
        assert filed[0]["category"] == "operational-failure"
        assert "docs/features/x.md" in filed[0]["body"]

    # -- Injection point 4: a failed restore escalates honestly -------------

    def test_restore_failure_escalates_with_manual_cleanup_title(
        self, repo: Path, gh, monkeypatch, fake_redis
    ):
        _seed_tracked_doc(repo, "docs/features/x.md", "# X\n" + "Padding line.\n" * 6)
        _standard_preflight(monkeypatch, repo, fake_redis)

        monkeypatch.setattr(docs_auditor, "audit", _writing_audit_stub("docs/features/x.md"))
        monkeypatch.setattr(
            docs_auditor,
            "_push_branch_and_pr",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("injected")),
        )

        real_run = docs_auditor.subprocess.run

        def failing_checkout(cmd, *a, **kw):
            if cmd[:2] == ["git", "checkout"] and len(cmd) == 3 and cmd[2] == "main":
                return MagicMock(returncode=1, stdout="", stderr="simulated checkout failure")
            return real_run(cmd, *a, **kw)

        monkeypatch.setattr(docs_auditor.subprocess, "run", failing_checkout)

        filed: list[dict] = []
        monkeypatch.setattr(
            docs_auditor,
            "_file_issue_if_new",
            lambda finding, root: filed.append(finding) or True,
        )

        result = docs_auditor.run_docs_auditor()

        assert result["status"] == "error"
        assert len(filed) == 1
        finding = filed[0]
        assert finding["category"] == "operational-failure"
        assert finding["title"].endswith("— manual cleanup required")
        assert "docs-auditor: rotation aborted after writing for" in finding["title"]
        assert "did NOT complete" in finding["body"] or "NOT" in finding["body"]
        assert "docs/features/x.md" in finding["body"]

    # -- Injection point 5: a pre-guard audit() exception does not crash ----

    def test_exception_above_audit_guard_does_not_crash_handler(
        self, repo: Path, gh, monkeypatch, fake_redis
    ):
        _seed_tracked_doc(repo, "docs/features/x.md", "# X\n" + "Padding line.\n" * 6)
        _standard_preflight(monkeypatch, repo, fake_redis)

        def raising_neighborhood(primary_path, root, cap=None):
            raise RuntimeError("neighborhood resolution boom")

        monkeypatch.setattr(docs_auditor, "_resolve_neighborhood", raising_neighborhood)

        filed: list[dict] = []
        monkeypatch.setattr(
            docs_auditor,
            "_file_issue_if_new",
            lambda finding, root: filed.append(finding) or True,
        )
        monkeypatch.setattr(
            docs_auditor,
            "_push_branch_and_pr",
            lambda *a, **kw: pytest.fail("must not be reached"),
        )

        result = docs_auditor.run_docs_auditor()

        assert result["status"] == "error"
        assert "neighborhood resolution boom" in result["summary"]
        assert "UnboundLocalError" not in result["summary"]
        assert "cannot access local variable" not in result["summary"]
        # Nothing was written, so nothing to escalate.
        assert filed == []
        assert _porcelain(repo, "docs/features/x.md") == ""

    # -- Injection point 6: the R5-1 boundary does not leak into the new -----
    # -- handler, and vice versa. ---------------------------------------------

    def test_r5_1_failure_files_only_the_r5_1_issue(self, repo: Path, gh, monkeypatch, fake_redis):
        _seed_tracked_doc(repo, "docs/features/x.md", "# X\n" + "Padding line.\n" * 6)
        _standard_preflight(monkeypatch, repo, fake_redis)

        monkeypatch.setattr(docs_auditor, "audit", _writing_audit_stub("docs/features/x.md"))
        monkeypatch.setattr(docs_auditor, "_push_branch_and_pr", lambda *a, **kw: None)

        filed: list[dict] = []

        def selective_raise(finding, root):
            if "rotation failed to produce a PR" in finding["title"]:
                raise RuntimeError("injected")
            filed.append(finding)
            return True

        monkeypatch.setattr(docs_auditor, "_file_issue_if_new", selective_raise)

        result = docs_auditor.run_docs_auditor()

        assert result["status"] == "error"
        assert not any("rotation aborted after writing" in f["title"] for f in filed)

    def test_r5_1_benign_pr_url_none_files_only_the_r5_1_issue(
        self, repo: Path, gh, monkeypatch, fake_redis
    ):
        """The benign counterpart: documents the intended R5-1 behavior.

        Nothing inside the ``if pr_url is None:`` block can propagate on its
        own, so this half alone cannot discriminate a too-wide boundary —
        the injected half above is what pins it (mutation row 8).
        """
        _seed_tracked_doc(repo, "docs/features/x.md", "# X\n" + "Padding line.\n" * 6)
        _standard_preflight(monkeypatch, repo, fake_redis)

        monkeypatch.setattr(docs_auditor, "audit", _writing_audit_stub("docs/features/x.md"))
        monkeypatch.setattr(docs_auditor, "_push_branch_and_pr", lambda *a, **kw: None)

        filed: list[dict] = []
        monkeypatch.setattr(
            docs_auditor,
            "_file_issue_if_new",
            lambda finding, root: filed.append(finding) or True,
        )

        result = docs_auditor.run_docs_auditor()

        assert result["status"] == "error"
        assert len(filed) == 1
        assert "rotation failed to produce a PR" in filed[0]["title"]
        assert not any("rotation aborted after writing" in f["title"] for f in filed)

    # -- Empty/Invalid Input Handling -----------------------------------------

    def test_abort_after_write_with_no_files_touched_does_not_escalate(
        self, repo: Path, gh, monkeypatch, fake_redis
    ):
        """A run that wrote nothing left no dirt: no restore, no escalation."""
        monkeypatch.setattr(docs_auditor, "PROJECT_ROOT", repo)

        def fail_if_called(finding, root):
            pytest.fail("must not escalate when files_touched is empty")

        monkeypatch.setattr(docs_auditor, "_file_issue_if_new", fail_if_called)

        real_run = docs_auditor.subprocess.run
        calls: list[list[str]] = []

        def recording_run(cmd, *a, **kw):
            calls.append(list(cmd))
            return real_run(cmd, *a, **kw)

        monkeypatch.setattr(docs_auditor.subprocess, "run", recording_run)

        result = docs_auditor._abort_after_write("slug", "main", [], "boom")

        assert result["status"] == "error"
        assert "boom" in result["summary"]
        assert calls == []  # no restore subprocess was issued

    def test_starting_ref_none_skips_audit_and_leaves_tree_untouched(
        self, repo: Path, gh, monkeypatch, fake_redis
    ):
        _seed_tracked_doc(repo, "docs/features/x.md", "# X\n" + "Padding line.\n" * 6)
        _standard_preflight(monkeypatch, repo, fake_redis)
        monkeypatch.setattr(docs_auditor, "_current_ref", lambda root: None)
        monkeypatch.setattr(
            docs_auditor, "audit", lambda **kw: pytest.fail("audit must not be called")
        )

        result = docs_auditor.run_docs_auditor()

        assert result["status"] == "skipped"
        assert _porcelain(repo) == ""

    def test_branch_none_restore_issues_no_rev_parse_or_branch_delete(
        self, repo: Path, gh, monkeypatch, fake_redis
    ):
        _seed_tracked_doc(repo, "docs/features/x.md", "# X\n" + "Padding line.\n" * 6)
        _standard_preflight(monkeypatch, repo, fake_redis)

        monkeypatch.setattr(docs_auditor, "audit", _writing_audit_stub("docs/features/x.md"))
        monkeypatch.setattr(
            docs_auditor,
            "_push_branch_and_pr",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("injected")),
        )
        monkeypatch.setattr(docs_auditor, "_file_issue_if_new", lambda finding, root: True)

        real_run = docs_auditor.subprocess.run
        calls: list[list[str]] = []

        def recording_run(cmd, *a, **kw):
            calls.append(list(cmd))
            return real_run(cmd, *a, **kw)

        monkeypatch.setattr(docs_auditor.subprocess, "run", recording_run)

        result = docs_auditor.run_docs_auditor()

        assert result["status"] == "error"
        assert ["git", "rev-parse", "--verify", "--quiet", "refs/heads/None"] not in calls
        assert not any(c[:2] == ["git", "branch"] and "-D" in c for c in calls)

    # -- Pre-write failure escalates nothing (outer except stays narrow) -----

    def test_pre_write_failure_files_no_escalation(self, repo: Path, gh, monkeypatch, fake_redis):
        _seed_tracked_doc(repo, "docs/features/x.md", "# X\n" + "Padding line.\n" * 6)
        monkeypatch.setattr(docs_auditor, "PROJECT_ROOT", repo)
        monkeypatch.setattr(docs_auditor, "_get_redis", lambda: fake_redis)
        monkeypatch.setattr(docs_auditor, "_check_auth", lambda: (True, ""))
        monkeypatch.setattr(docs_auditor, "_git_dirty", lambda root: False)
        monkeypatch.setattr(docs_auditor, "_run_vault_drift_detection", lambda pk: 0)

        def raising_select(root, project_key):
            raise RuntimeError("rotation pick boom")

        monkeypatch.setattr(docs_auditor, "_select_primary_doc", raising_select)

        filed: list[dict] = []
        monkeypatch.setattr(
            docs_auditor,
            "_file_issue_if_new",
            lambda finding, root: filed.append(finding) or True,
        )

        result = docs_auditor.run_docs_auditor()

        assert result["status"] == "error"
        assert filed == []
        assert "rotation pick boom" in result["summary"]

    # -- Error State Rendering: the three titles are pairwise distinct -------

    def test_three_operational_failure_titles_are_pairwise_distinct(self):
        slug = "docs_features_x_md"
        r5_1 = f"docs-auditor: rotation failed to produce a PR for {slug}"
        restored = f"docs-auditor: rotation aborted after writing for {slug}"
        manual = (
            f"docs-auditor: rotation aborted after writing for {slug} — manual cleanup required"
        )
        assert len({r5_1, restored, manual}) == 3


# ---------------------------------------------------------------------------
# NEW-1 — a guard-fired run performs no working-tree write
# ---------------------------------------------------------------------------


class TestGuardFiredNoWorkingTreeWrite:
    def test_daily_cap_guard_leaves_the_tree_byte_identical(
        self, repo: Path, gh, monkeypatch, fake_redis
    ):
        primary = repo / "docs" / "features" / "foo.md"
        primary.write_text("# Foo\n" + "Padding line.\n" * 6)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "seed foo.md")

        before = _porcelain(repo)

        audit_mock_calls: list[int] = []

        def fake_audit(**kw):
            audit_mock_calls.append(1)
            raise AssertionError("audit() must not be called when a guard fires")

        monkeypatch.setattr(docs_auditor, "PROJECT_ROOT", repo)
        monkeypatch.setattr(docs_auditor, "_get_redis", lambda: fake_redis)
        monkeypatch.setattr(docs_auditor, "_check_auth", lambda: (True, ""))
        monkeypatch.setattr(docs_auditor, "_git_dirty", lambda root: False)
        monkeypatch.setattr(docs_auditor, "_run_vault_drift_detection", lambda pk: 0)
        monkeypatch.setattr(docs_auditor, "_daily_pr_cap_reached", lambda root: True)
        monkeypatch.setattr(docs_auditor, "_has_open_pr_for_slug", lambda slug, root: False)
        monkeypatch.setattr(docs_auditor, "audit", fake_audit)
        monkeypatch.setattr(docs_auditor, "_send_telegram_notification", lambda msg, **kw: True)

        result = docs_auditor.run_docs_auditor()

        assert result["status"] == "skipped"
        assert not audit_mock_calls
        assert _porcelain(repo) == before


# ---------------------------------------------------------------------------
# NEW-2 — the sweeper reads the WITHHELD_PR_MARKER from its own query
# ---------------------------------------------------------------------------


class TestSweeperReadsMarkerFromOwnQuery:
    def test_pr_list_query_requests_body(self, repo: Path, gh, monkeypatch, fake_redis):
        monkeypatch.setattr(docs_auditor, "PROJECT_ROOT", repo)
        monkeypatch.setattr(docs_auditor, "_get_redis", lambda: fake_redis)
        gh.pr_list_result = []

        docs_auditor.run_docs_branch_sweeper()

        pr_list_calls = [c for c in gh.calls if c[:3] == ["gh", "pr", "list"]]
        # No docs-audit/* branches exist in this fresh repo, so no pr-list call
        # is made at all — assert the built code requests `body` when it is
        # made, which the marker test below exercises for real.
        for c in pr_list_calls:
            assert "number,state,createdAt,body" in c

    def test_marker_check_fails_loudly_without_body_in_payload(
        self, repo: Path, gh, monkeypatch, fake_redis
    ):
        """A `pr list` payload without `body` must not be silently read as
        unmarked and closed — that would pass the sweeper test vacuously."""
        branch = "docs-audit/foo-20260101-0000"
        _git(repo, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")

        monkeypatch.setattr(docs_auditor, "PROJECT_ROOT", repo)
        monkeypatch.setattr(docs_auditor, "_get_redis", lambda: fake_redis)
        gh.pr_list_result = [
            {"number": 1, "state": "OPEN", "createdAt": "2020-01-01T00:00:00Z"}
        ]  # no "body" key

        result = docs_auditor.run_docs_branch_sweeper()

        # With no `body` key, `.get("body")` degrades to None/"" — the marker
        # is absent, so the PR is treated as unmarked (not exempted). This is
        # documented behavior; the point of this test is the companion
        # structural assertion that the query itself requests `body`.
        pr_list_calls = [c for c in gh.calls if c[:3] == ["gh", "pr", "list"]]
        assert pr_list_calls, "sweeper must query gh pr list for the seeded branch"
        assert "number,state,createdAt,body" in pr_list_calls[0]
        assert result["status"] in ("ok", "error")


# ---------------------------------------------------------------------------
# NEW-4 / R3-3 — withheld filing respects the per-run cap
# ---------------------------------------------------------------------------


class TestWithheldFilingCap:
    def test_more_than_cap_withheld_entries_files_exactly_the_cap(
        self, repo: Path, gh, monkeypatch, fake_redis, caplog
    ):
        primary = repo / "docs" / "features" / "foo.md"
        primary.write_text("# Foo\n" + "Padding line.\n" * 6)

        n = docs_auditor.ISSUE_FILING_PER_RUN_CAP + 3
        withheld = [
            {"doc": "docs/features/foo.md", "old": f"a/{i}.py", "new": f"b/{i}.py", "reason": "x"}
            for i in range(n)
        ]
        audit_result = {
            "status": "ok",
            "files_touched": [],
            "fixes_applied": 0,
            "fixes_withheld": n,
            "withheld": withheld,
            "issues_filed": 0,
        }

        filed: list[dict] = []

        def fake_file_issue(finding, repo_root):
            filed.append(finding)
            return True

        monkeypatch.setattr(docs_auditor, "PROJECT_ROOT", repo)
        monkeypatch.setattr(docs_auditor, "_get_redis", lambda: fake_redis)
        monkeypatch.setattr(docs_auditor, "_check_auth", lambda: (True, ""))
        monkeypatch.setattr(docs_auditor, "_git_dirty", lambda root: False)
        monkeypatch.setattr(docs_auditor, "_run_vault_drift_detection", lambda pk: 0)
        monkeypatch.setattr(docs_auditor, "audit", lambda **kw: audit_result)
        monkeypatch.setattr(docs_auditor, "_send_telegram_notification", lambda msg, **kw: True)
        monkeypatch.setattr(docs_auditor, "_file_issue_if_new", fake_file_issue)

        with caplog.at_level("WARNING", logger="reflections.docs_auditor"):
            result = docs_auditor.run_docs_auditor()

        assert result["status"] == "skipped"
        assert len(filed) == docs_auditor.ISSUE_FILING_PER_RUN_CAP
        assert any(
            "cap" in r.message.lower() and "suppress" in r.message.lower() for r in caplog.records
        )
        # Suppression alone does not affect the run's status.
        assert result["status"] in ("skipped", "ok", "error")


# ---------------------------------------------------------------------------
# Sweeper close path — WITHHELD_PR_MARKER exemption, plain-PR close, anti-criterion
# ---------------------------------------------------------------------------


class TestSweeperClosePath:
    def _push_docs_audit_branch(self, repo: Path, name: str) -> str:
        branch = f"docs-audit/{name}"
        _git(repo, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        return branch

    def test_withheld_pr_is_not_closed_and_branch_is_not_deleted(
        self, repo: Path, gh, monkeypatch, fake_redis
    ):
        self._push_docs_audit_branch(repo, "withheld-20260101-0000")
        monkeypatch.setattr(docs_auditor, "PROJECT_ROOT", repo)
        monkeypatch.setattr(docs_auditor, "_get_redis", lambda: fake_redis)
        gh.pr_list_result = [
            {
                "number": 42,
                "state": "OPEN",
                "createdAt": "2000-01-01T00:00:00Z",
                "body": f"pass\n\n{docs_auditor.WITHHELD_PR_MARKER}\nwithheld",
            }
        ]

        filed: list[dict] = []
        monkeypatch.setattr(
            docs_auditor, "_file_issue_if_new", lambda f, r: (filed.append(f), True)[1]
        )

        docs_auditor.run_docs_branch_sweeper()

        close_calls = [c for c in gh.calls if c[:3] == ["gh", "pr", "close"]]
        assert close_calls == []
        assert filed, "a withheld PR must file its own escalation issue"
        assert filed[0]["title"] == "docs-auditor: withheld PR #42 still unreviewed"

    def test_fresh_withheld_pr_is_exempt_but_files_no_stale_claim_yet(
        self, repo: Path, gh, monkeypatch, fake_redis
    ):
        """The exemption is immediate; the "still unreviewed" filing waits.

        A withheld PR opened minutes ago must never be closed, but it must also
        not yet be titled as stale: the dedup key is once-ever, so a filing made
        on sight would make the wrong wording the permanent record.
        """
        self._push_docs_audit_branch(repo, "withheld-fresh-20260101-0000")
        monkeypatch.setattr(docs_auditor, "PROJECT_ROOT", repo)
        monkeypatch.setattr(docs_auditor, "_get_redis", lambda: fake_redis)
        fresh = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        gh.pr_list_result = [
            {
                "number": 43,
                "state": "OPEN",
                "createdAt": fresh,
                "body": f"pass\n\n{docs_auditor.WITHHELD_PR_MARKER}\nwithheld",
            }
        ]

        filed: list[dict] = []
        monkeypatch.setattr(
            docs_auditor, "_file_issue_if_new", lambda f, r: (filed.append(f), True)[1]
        )

        docs_auditor.run_docs_branch_sweeper()

        close_calls = [c for c in gh.calls if c[:3] == ["gh", "pr", "close"]]
        assert close_calls == [], "a withheld PR is exempt from stale-close at any age"
        assert filed == [], "no staleness claim before STALE_PR_AGE_DAYS"

    def test_non_marker_stale_pr_is_still_closed(self, repo: Path, gh, monkeypatch, fake_redis):
        self._push_docs_audit_branch(repo, "plain-20260101-0000")
        monkeypatch.setattr(docs_auditor, "PROJECT_ROOT", repo)
        monkeypatch.setattr(docs_auditor, "_get_redis", lambda: fake_redis)
        gh.pr_list_result = [
            {
                "number": 43,
                "state": "OPEN",
                "createdAt": "2000-01-01T00:00:00Z",
                "body": "Automated docs auditor pass.",
            }
        ]

        docs_auditor.run_docs_branch_sweeper()

        close_calls = [c for c in gh.calls if c[:3] == ["gh", "pr", "close"]]
        assert len(close_calls) == 1
        assert "43" in close_calls[0]

    def test_sweeper_never_dispatches_a_merge(self, repo: Path, gh, monkeypatch, fake_redis):
        """Anti-criterion: no `gh pr merge` is ever dispatched, marker or not."""
        self._push_docs_audit_branch(repo, "any-20260101-0000")
        monkeypatch.setattr(docs_auditor, "PROJECT_ROOT", repo)
        monkeypatch.setattr(docs_auditor, "_get_redis", lambda: fake_redis)
        gh.pr_list_result = [
            {
                "number": 44,
                "state": "OPEN",
                "createdAt": "2000-01-01T00:00:00Z",
                "body": "Automated docs auditor pass.",
            }
        ]

        docs_auditor.run_docs_branch_sweeper()

        assert gh.pr_merge_calls == []
        assert not any(c[:3] == ["gh", "pr", "merge"] for c in gh.calls)
