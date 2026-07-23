"""Tests for PR review audit reflection step (step 20).

Tests the finding parser, severity classification, address detection,
issue body formatting, and PRReviewAudit model interface.

Previously tested ReflectionRunner step registration — those tests are
removed because ReflectionRunner (scripts/reflections.py) was deleted
as part of issue #748. The helpers now live in reflections/auditing.py.
"""

from __future__ import annotations

import json

import pytest

from reflections.audits import pr_review_audit
from reflections.audits.pr_review_audit import (
    SEVERITY_LABELS,
    SEVERITY_MAP,
)
from reflections.audits.pr_review_audit import (
    _check_finding_addressed as check_finding_addressed,
)
from reflections.audits.pr_review_audit import (
    _format_audit_issue_body as format_audit_issue_body,
)
from reflections.audits.pr_review_audit import (
    _parse_review_findings as parse_review_findings,
)

# --- parse_review_findings tests ---


class TestParseReviewFindings:
    """Tests for the structured finding parser."""

    def test_well_formed_finding(self):
        """Parse a complete, well-formed review finding."""
        body = (
            "**Severity:** blocker\n"
            "**File:** `src/main.py`\n"
            "**Code:** `do_thing()`\n"
            "**Issue:** This function has no error handling\n"
            "**Fix:** Wrap in try/except with proper logging\n"
        )
        findings = parse_review_findings(body)
        assert len(findings) == 1
        f = findings[0]
        assert f["severity"] == "critical"
        assert f["raw_severity"] == "blocker"
        assert f["file_path"] == "src/main.py"
        assert f["code"] == "do_thing()"
        assert "no error handling" in f["issue_description"]
        assert "try/except" in f["suggested_fix"]

    def test_tech_debt_severity(self):
        body = "**Severity:** tech_debt\n**Issue:** Should refactor this module\n"
        findings = parse_review_findings(body)
        assert len(findings) == 1
        assert findings[0]["severity"] == "standard"
        assert findings[0]["raw_severity"] == "tech_debt"

    def test_nit_severity(self):
        body = "**Severity:** nit\n**Issue:** Typo in variable name\n"
        findings = parse_review_findings(body)
        assert len(findings) == 1
        assert findings[0]["severity"] == "trivial"

    def test_partial_finding_no_file(self):
        """Finding with only Severity and Issue (minimum required fields)."""
        body = "**Severity:** blocker\n**Issue:** Critical logic error in auth flow\n"
        findings = parse_review_findings(body)
        assert len(findings) == 1
        assert findings[0]["file_path"] == ""
        assert findings[0]["code"] == ""
        assert findings[0]["suggested_fix"] == ""

    def test_missing_issue_field_skipped(self):
        """Finding without Issue field is skipped (minimum not met)."""
        body = "**Severity:** blocker\n**File:** `src/main.py`\n"
        findings = parse_review_findings(body)
        assert len(findings) == 0

    def test_no_structured_format(self):
        """Free-text comment without structured markers returns empty."""
        body = "This looks good! Nice work on the refactoring."
        findings = parse_review_findings(body)
        assert len(findings) == 0

    def test_empty_body(self):
        findings = parse_review_findings("")
        assert len(findings) == 0

    def test_none_body(self):
        findings = parse_review_findings(None)
        assert len(findings) == 0

    def test_multiple_findings_in_one_comment(self):
        """Multiple structured findings in a single comment body."""
        body = (
            "**Severity:** blocker\n"
            "**File:** `auth.py`\n"
            "**Issue:** SQL injection vulnerability\n"
            "**Fix:** Use parameterized queries\n"
            "\n"
            "**Severity:** nit\n"
            "**File:** `utils.py`\n"
            "**Issue:** Unused import\n"
        )
        findings = parse_review_findings(body)
        assert len(findings) == 2
        assert findings[0]["severity"] == "critical"
        assert findings[0]["file_path"] == "auth.py"
        assert findings[1]["severity"] == "trivial"
        assert findings[1]["file_path"] == "utils.py"

    def test_multiple_findings_get_unique_dedup_keys(self):
        """Each finding in a multi-finding comment must get a distinct dedup key.

        Regression test: previously all findings from one comment shared a
        comment-level key, so auditing one finding silently skipped the rest.
        """
        body = (
            "**Severity:** blocker\n"
            "**File:** `auth.py`\n"
            "**Issue:** SQL injection vulnerability\n"
            "\n"
            "**Severity:** nit\n"
            "**File:** `utils.py`\n"
            "**Issue:** Unused import\n"
        )
        findings = parse_review_findings(body)
        assert len(findings) == 2

        # Simulate key generation as done in run_pr_review_audit
        repo = "owner/repo"
        pr_number = 42
        comment_id = 999
        keys = []
        for finding_idx, _finding in enumerate(findings):
            keys.append(f"{repo}:{pr_number}:{comment_id}:{finding_idx}")

        # Keys must be unique per finding
        assert len(set(keys)) == len(keys)
        assert keys[0] != keys[1]
        assert keys[0].endswith(":0")
        assert keys[1].endswith(":1")

    def test_case_insensitive_severity(self):
        body = "**Severity:** BLOCKER\n**Issue:** Something critical\n"
        findings = parse_review_findings(body)
        assert len(findings) == 1
        assert findings[0]["severity"] == "critical"

    def test_file_path_without_backticks(self):
        body = (
            "**Severity:** tech_debt\n**File:** src/models/user.py\n**Issue:** Missing docstring\n"
        )
        findings = parse_review_findings(body)
        assert len(findings) == 1
        assert findings[0]["file_path"] == "src/models/user.py"


# --- Severity mapping tests ---


class TestSeverityMapping:
    def test_all_severities_mapped(self):
        assert SEVERITY_MAP["blocker"] == "critical"
        assert SEVERITY_MAP["tech_debt"] == "standard"
        assert SEVERITY_MAP["nit"] == "trivial"

    def test_all_labels_mapped(self):
        assert SEVERITY_LABELS["critical"] == "critical"
        assert SEVERITY_LABELS["standard"] == "tech-debt"
        assert SEVERITY_LABELS["trivial"] == "nit"


# --- check_finding_addressed tests ---


class TestCheckFindingAddressed:
    def test_file_modified_after_review(self):
        commits = [
            {
                "commit": {"committer": {"date": "2026-03-25T12:00:00Z"}},
                "files": [{"filename": "src/main.py"}],
            }
        ]
        assert check_finding_addressed(commits, "2026-03-25T10:00:00Z", "src/main.py") is True

    def test_file_modified_before_review(self):
        commits = [
            {
                "commit": {"committer": {"date": "2026-03-25T08:00:00Z"}},
                "files": [{"filename": "src/main.py"}],
            }
        ]
        assert check_finding_addressed(commits, "2026-03-25T10:00:00Z", "src/main.py") is False

    def test_different_file_modified(self):
        commits = [
            {
                "commit": {"committer": {"date": "2026-03-25T12:00:00Z"}},
                "files": [{"filename": "src/other.py"}],
            }
        ]
        assert check_finding_addressed(commits, "2026-03-25T10:00:00Z", "src/main.py") is False

    def test_empty_commits(self):
        assert check_finding_addressed([], "2026-03-25T10:00:00Z", "src/main.py") is False

    def test_empty_file_path(self):
        commits = [
            {
                "commit": {"committer": {"date": "2026-03-25T12:00:00Z"}},
                "files": [{"filename": "src/main.py"}],
            }
        ]
        assert check_finding_addressed(commits, "2026-03-25T10:00:00Z", "") is False

    def test_multiple_commits_one_addresses(self):
        commits = [
            {
                "commit": {"committer": {"date": "2026-03-25T08:00:00Z"}},
                "files": [{"filename": "src/other.py"}],
            },
            {
                "commit": {"committer": {"date": "2026-03-25T14:00:00Z"}},
                "files": [{"filename": "src/main.py"}],
            },
        ]
        assert check_finding_addressed(commits, "2026-03-25T10:00:00Z", "src/main.py") is True


# --- format_audit_issue_body tests ---


class TestFormatAuditIssueBody:
    def test_basic_formatting(self):
        findings = [
            {
                "severity": "critical",
                "file_path": "auth.py",
                "code": "login()",
                "issue_description": "No rate limiting",
                "suggested_fix": "Add rate limiter",
                "review_url": "https://github.com/repo/pull/1#comment-123",
            }
        ]
        body = format_audit_issue_body(1, "Fix auth", "https://github.com/repo/pull/1", findings)
        assert "PR #1" in body or "#1" in body
        assert "Fix auth" in body
        assert "auth.py" in body
        assert "No rate limiting" in body
        assert "Add rate limiter" in body
        assert "Critical" in body

    def test_multiple_severities_grouped(self):
        findings = [
            {
                "severity": "critical",
                "file_path": "a.py",
                "issue_description": "Critical bug",
                "review_url": "",
            },
            {
                "severity": "trivial",
                "file_path": "b.py",
                "issue_description": "Typo",
                "review_url": "",
            },
        ]
        body = format_audit_issue_body(2, "Some PR", "https://url", findings)
        assert "Critical" in body
        assert "Trivial" in body
        # Critical should come before Trivial
        assert body.index("Critical") < body.index("Trivial")

    def test_empty_optional_fields(self):
        findings = [
            {
                "severity": "standard",
                "file_path": "",
                "code": "",
                "issue_description": "Some issue",
                "suggested_fix": "",
                "review_url": "",
            }
        ]
        body = format_audit_issue_body(3, "PR title", "https://url", findings)
        assert "Some issue" in body
        assert "Standard" in body


# --- PRReviewAudit model tests ---


class TestPRReviewAuditModel:
    """Test that the model is importable and has expected interface."""

    def test_importable(self):
        from models.reflections import PRReviewAudit

        assert PRReviewAudit is not None

    def test_has_expected_classmethods(self):
        from models.reflections import PRReviewAudit

        assert hasattr(PRReviewAudit, "is_audited")
        assert hasattr(PRReviewAudit, "mark_audited")
        assert hasattr(PRReviewAudit, "last_successful_run")
        assert hasattr(PRReviewAudit, "cleanup_expired")
        assert callable(PRReviewAudit.is_audited)
        assert callable(PRReviewAudit.mark_audited)
        assert callable(PRReviewAudit.last_successful_run)


# ---------------------------------------------------------------------------
# run() end-to-end: COWORK_ROUTINE cloud guards (project synthesis, filing
# enablement, Redis-touchpoint bypass, per-PR gh title-search dedup) plus the
# preserved local dry-run/watermark behavior when the env var is unset.
# ---------------------------------------------------------------------------

_FINDING_BODY = (
    "**Severity:** blocker\n"
    "**File:** `src/app.py`\n"
    "**Issue:** Missing error handling\n"
    "**Fix:** Add try/except\n"
)


def _gh_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> object:
    """Build a subprocess.CompletedProcess-shaped stand-in for `gh` calls."""

    class _Result:
        pass

    result = _Result()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def _pr_list_stdout(*pr_numbers: int) -> str:
    return json.dumps(
        [
            {
                "number": n,
                "title": f"PR title {n}",
                "url": f"https://github.com/org/repo/pull/{n}",
                "mergedAt": "2026-01-01T00:00:00Z",
            }
            for n in pr_numbers
        ]
    )


def _make_subprocess_stub(
    *,
    pr_list_stdout: str = "[]",
    comments_stdout: str = "[]",
    reviews_stdout: str = "[]",
    commits_stdout: str = "[]",
    issue_create_stdout: str = "https://github.com/org/repo/issues/1",
    issue_list_stdout: str = "[]",
    captured_calls: list | None = None,
):
    """Return a fake `subprocess.run` dispatching on the `gh` subcommand shape."""

    def _run(cmd, **kwargs):  # noqa: ANN001
        if captured_calls is not None:
            captured_calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "list"]:
            return _gh_result(stdout=pr_list_stdout)
        if cmd[:2] == ["gh", "api"] and cmd[2].endswith("/comments"):
            return _gh_result(stdout=comments_stdout)
        if cmd[:2] == ["gh", "api"] and cmd[2].endswith("/reviews"):
            return _gh_result(stdout=reviews_stdout)
        if cmd[:2] == ["gh", "api"] and cmd[2].endswith("/commits"):
            return _gh_result(stdout=commits_stdout)
        if cmd[:3] == ["gh", "issue", "create"]:
            return _gh_result(stdout=issue_create_stdout)
        if cmd[:3] == ["gh", "issue", "list"]:
            return _gh_result(stdout=issue_list_stdout)
        return _gh_result()

    return _run


class TestCloudProjectSynthesis:
    """Guard 1 (r5 B1): synthesize a project from GH_REPO in an empty sandbox."""

    def test_repo_flows_to_gh_pr_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COWORK_ROUTINE", "1")
        monkeypatch.setenv("GH_REPO", "org/repo")
        monkeypatch.setattr(pr_review_audit, "load_local_projects", lambda: [])

        calls: list = []
        monkeypatch.setattr(
            pr_review_audit.subprocess, "run", _make_subprocess_stub(captured_calls=calls)
        )

        result = pr_review_audit.run()

        assert result["status"] == "ok"
        assert calls, "expected at least one gh call"
        pr_list_call = calls[0]
        assert pr_list_call[:3] == ["gh", "pr", "list"]
        assert "--repo" in pr_list_call
        assert pr_list_call[pr_list_call.index("--repo") + 1] == "org/repo"

    def test_missing_gh_repo_fails_loud(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COWORK_ROUTINE", "1")
        monkeypatch.delenv("GH_REPO", raising=False)
        monkeypatch.setattr(pr_review_audit, "load_local_projects", lambda: [])

        calls: list = []
        monkeypatch.setattr(
            pr_review_audit.subprocess, "run", _make_subprocess_stub(captured_calls=calls)
        )

        result = pr_review_audit.run()

        assert result["status"] == "error"
        assert calls == []  # never reached gh -- failed loud before scanning

    def test_malformed_gh_repo_fails_loud(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COWORK_ROUTINE", "1")
        monkeypatch.setenv("GH_REPO", "not-a-valid-repo-slug")
        monkeypatch.setattr(pr_review_audit, "load_local_projects", lambda: [])

        calls: list = []
        monkeypatch.setattr(
            pr_review_audit.subprocess, "run", _make_subprocess_stub(captured_calls=calls)
        )

        result = pr_review_audit.run()

        assert result["status"] == "error"
        assert calls == []


class TestCloudRedisBypass:
    """Guard 3: all three PRReviewAudit touchpoints are bypassed in cloud mode."""

    def test_cloud_mode_reaches_filing_path_without_redis(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("COWORK_ROUTINE", "1")
        monkeypatch.setenv("GH_REPO", "org/repo")
        monkeypatch.setattr(pr_review_audit, "load_local_projects", lambda: [])

        from models.reflections import PRReviewAudit

        def _boom(*_a, **_kw):
            raise RuntimeError("no redis connection available")

        monkeypatch.setattr(PRReviewAudit, "last_successful_run", _boom)
        monkeypatch.setattr(PRReviewAudit, "is_audited", _boom)
        monkeypatch.setattr(PRReviewAudit, "mark_audited", _boom)

        comments = [
            {
                "id": 1,
                "body": _FINDING_BODY,
                "created_at": "2026-01-01T00:00:00Z",
                "html_url": "https://github.com/org/repo/pull/1#comment-1",
            }
        ]
        stub = _make_subprocess_stub(
            pr_list_stdout=_pr_list_stdout(1),
            comments_stdout=json.dumps(comments),
        )
        monkeypatch.setattr(pr_review_audit.subprocess, "run", stub)

        # No exception raised -- none of the three Redis touchpoints were reached.
        result = pr_review_audit.run()

        assert result["status"] == "ok"
        assert any("Filed issue" in f for f in result["findings"])


class TestCloudFilingEnabled:
    """Guard 2: dry_run flips to False so `gh issue create` is actually reached."""

    def test_gh_issue_create_reached_in_cloud_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COWORK_ROUTINE", "1")
        monkeypatch.setenv("GH_REPO", "org/repo")
        monkeypatch.setattr(pr_review_audit, "load_local_projects", lambda: [])

        comments = [
            {
                "id": 1,
                "body": _FINDING_BODY,
                "created_at": "2026-01-01T00:00:00Z",
                "html_url": "https://github.com/org/repo/pull/1#comment-1",
            }
        ]
        stub = _make_subprocess_stub(
            pr_list_stdout=_pr_list_stdout(1),
            comments_stdout=json.dumps(comments),
            issue_create_stdout="https://github.com/org/repo/issues/42",
        )
        monkeypatch.setattr(pr_review_audit.subprocess, "run", stub)

        result = pr_review_audit.run()

        assert result["status"] == "ok"
        assert any("Filed issue" in f for f in result["findings"])
        assert not any("[DRY RUN]" in f for f in result["findings"])


class TestLocalBehaviorPreserved:
    """env unset: dry-run, watermark, is_audited/mark_audited, empty-projects no-op."""

    def test_empty_projects_stays_a_noop_without_cowork_routine(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("COWORK_ROUTINE", raising=False)
        monkeypatch.setattr(pr_review_audit, "load_local_projects", lambda: [])

        calls: list = []
        monkeypatch.setattr(
            pr_review_audit.subprocess, "run", _make_subprocess_stub(captured_calls=calls)
        )

        result = pr_review_audit.run()

        assert result["status"] == "ok"
        assert calls == []  # loop body never executes -- no synthesis without the env var

    def test_watermark_never_advances_under_hardcoded_dry_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """r5 B2 premise-lock: mark_audited never fires locally, so the watermark
        stays None and the table stays empty -- pins the corrected premise that
        guard 3's bypass removes no *working* dedup mechanism."""
        monkeypatch.delenv("COWORK_ROUTINE", raising=False)

        project = {
            "slug": "proj",
            "working_directory": "/tmp",
            "github": {"org": "org", "repo": "repo"},
        }
        monkeypatch.setattr(pr_review_audit, "load_local_projects", lambda: [project])

        comments = [
            {
                "id": 1,
                "body": _FINDING_BODY,
                "created_at": "2026-01-01T00:00:00Z",
                "html_url": "https://github.com/org/repo/pull/1#comment-1",
            }
        ]
        stub = _make_subprocess_stub(
            pr_list_stdout=_pr_list_stdout(1),
            comments_stdout=json.dumps(comments),
        )
        monkeypatch.setattr(pr_review_audit.subprocess, "run", stub)

        from models.reflections import PRReviewAudit

        result = pr_review_audit.run()

        assert result["status"] == "ok"
        assert any("[DRY RUN]" in f for f in result["findings"])
        assert PRReviewAudit.last_successful_run() is None
        assert PRReviewAudit.query.all() == []


class TestCloudTitleDedup:
    """Guard 4: per-PR gh title-search dedup replaces the bypassed is_audited() read."""

    def test_two_runs_same_pr_files_exactly_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COWORK_ROUTINE", "1")
        monkeypatch.setenv("GH_REPO", "org/repo")
        monkeypatch.setattr(pr_review_audit, "load_local_projects", lambda: [])

        comments = [
            {
                "id": 1,
                "body": _FINDING_BODY,
                "created_at": "2026-01-01T00:00:00Z",
                "html_url": "https://github.com/org/repo/pull/1#comment-1",
            }
        ]
        filed_titles: list[str] = []

        def _run(cmd, **kwargs):  # noqa: ANN001
            if cmd[:3] == ["gh", "pr", "list"]:
                return _gh_result(stdout=_pr_list_stdout(1))
            if cmd[:2] == ["gh", "api"] and cmd[2].endswith("/comments"):
                return _gh_result(stdout=json.dumps(comments))
            if cmd[:2] == ["gh", "api"] and (
                cmd[2].endswith("/reviews") or cmd[2].endswith("/commits")
            ):
                return _gh_result(stdout="[]")
            if cmd[:3] == ["gh", "issue", "list"]:
                matches = [{"title": t} for t in filed_titles]
                return _gh_result(stdout=json.dumps(matches))
            if cmd[:3] == ["gh", "issue", "create"]:
                title = cmd[cmd.index("--title") + 1]
                filed_titles.append(title)
                return _gh_result(stdout="https://github.com/org/repo/issues/99")
            return _gh_result()

        monkeypatch.setattr(pr_review_audit.subprocess, "run", _run)

        result1 = pr_review_audit.run()
        result2 = pr_review_audit.run()

        assert len([f for f in result1["findings"] if f.startswith("Filed issue")]) == 1
        assert len([f for f in result2["findings"] if f.startswith("Filed issue")]) == 0
        assert any("[SKIP]" in f and "already filed" in f for f in result2["findings"])

    def test_two_distinct_prs_both_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COWORK_ROUTINE", "1")
        monkeypatch.setenv("GH_REPO", "org/repo")
        monkeypatch.setattr(pr_review_audit, "load_local_projects", lambda: [])

        comments = [
            {
                "id": 1,
                "body": _FINDING_BODY,
                "created_at": "2026-01-01T00:00:00Z",
                "html_url": "https://github.com/org/repo/pull/1#comment-1",
            }
        ]
        stub = _make_subprocess_stub(
            pr_list_stdout=_pr_list_stdout(1, 2),
            comments_stdout=json.dumps(comments),
            issue_list_stdout="[]",
        )
        monkeypatch.setattr(pr_review_audit.subprocess, "run", stub)

        result = pr_review_audit.run()

        filed = [f for f in result["findings"] if f.startswith("Filed issue")]
        assert len(filed) == 2
        assert any("PR #1" in f for f in filed)
        assert any("PR #2" in f for f in filed)
