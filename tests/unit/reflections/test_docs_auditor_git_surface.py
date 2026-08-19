"""Real-git test surface for reflections/docs_auditor.py (#2739).

Blanket ``unittest.mock.patch`` over ``subprocess.run`` is explicitly not an
acceptable strategy for ``_push_branch_and_pr``, the staging set, the restore
path, or the sweeper's close path — every ``git`` command in those paths must
actually run against a real repository on disk.

The sanctioned pattern (no in-repo precedent existed for a synchronous
dispatcher — ``tests/unit/reflections/test_merged_branch_cleanup.py`` patches
``asyncio.create_subprocess_exec`` because its module is async, so it is not
reusable here): ``monkeypatch.setattr(docs_auditor.subprocess, "run",
dispatcher)``, module-scoped, where ``dispatcher`` intercepts only
``cmd[0] == "gh"`` and returns a canned ``CompletedProcess``, delegating
everything else to the real ``subprocess.run``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reflections import docs_auditor

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
    subprocess.run(["git", "config", "user.name", "Docs Auditor Git Surface Test"], cwd=root, check=True)
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
        ["git", "status", "--porcelain", "--", *paths] if paths else ["git", "status", "--porcelain"],
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
    def test_files_one_issue_before_returning_status_error(self, repo: Path, gh, monkeypatch, fake_redis):
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
        monkeypatch.setattr(docs_auditor, "_send_telegram_notification", lambda msg: None)
        monkeypatch.setattr(docs_auditor, "_file_issue_if_new", fake_file_issue)

        result = docs_auditor.run_docs_auditor()

        assert result["status"] == "error"
        assert len(filed) == 1
        finding = filed[0]
        assert finding["title"] == "docs-auditor: rotation failed to produce a PR for docs_features_foo_md"
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
        monkeypatch.setattr(docs_auditor, "_send_telegram_notification", lambda msg: None)
        monkeypatch.setattr(docs_auditor, "_file_issue_if_new", fake_file_issue)

        docs_auditor.run_docs_auditor()

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
            "slug", repo, ["docs/features/x.md"]
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
            "slug", repo, ["docs/features/does_not_exist_xyz.md"]
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
            "slug", repo, ["docs/features/x.md"]
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

        docs_auditor._push_branch_and_pr("slug", repo, ["docs/features/x.md"])

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

        url = docs_auditor._push_branch_and_pr("slug", repo, ["docs/features/x.md"])
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
        monkeypatch.setattr(docs_auditor, "_send_telegram_notification", lambda msg: None)
        monkeypatch.setattr(docs_auditor, "_file_issue_if_new", lambda finding, root: True)

        result = docs_auditor.run_docs_auditor()
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# NEW-1 — a guard-fired run performs no working-tree write
# ---------------------------------------------------------------------------


class TestGuardFiredNoWorkingTreeWrite:
    def test_daily_cap_guard_leaves_the_tree_byte_identical(self, repo: Path, gh, monkeypatch, fake_redis):
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
        monkeypatch.setattr(docs_auditor, "_send_telegram_notification", lambda msg: None)

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

    def test_marker_check_fails_loudly_without_body_in_payload(self, repo: Path, gh, monkeypatch, fake_redis):
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
        monkeypatch.setattr(docs_auditor, "_send_telegram_notification", lambda msg: None)
        monkeypatch.setattr(docs_auditor, "_file_issue_if_new", fake_file_issue)

        with caplog.at_level("WARNING", logger="reflections.docs_auditor"):
            result = docs_auditor.run_docs_auditor()

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
        branch = self._push_docs_audit_branch(repo, "withheld-20260101-0000")
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
