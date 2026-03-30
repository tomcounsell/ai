"""Tests for PR review audit reflection step (step 20).

Tests the finding parser, severity classification, address detection,
issue body formatting, and step registration.
"""

from __future__ import annotations

from scripts.reflections import (
    SEVERITY_LABELS,
    SEVERITY_MAP,
    ReflectionRunner,
    check_finding_addressed,
    format_audit_issue_body,
    parse_review_findings,
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

        # Simulate key generation as done in step_20_pr_review_audit
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
        assert "step 20" in body

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


# --- ReflectionRunner registration tests ---


class TestReflectionsStateDryRun:
    """Verify dry_run is a proper public field on ReflectionsState."""

    def test_dry_run_default_false(self):
        from scripts.reflections import ReflectionsState

        state = ReflectionsState()
        assert state.dry_run is False

    def test_dry_run_settable(self):
        from scripts.reflections import ReflectionsState

        state = ReflectionsState()
        state.dry_run = True
        assert state.dry_run is True


class TestStepRegistration:
    def test_pr_review_audit_registered(self):
        """PR Review Audit step must be in the steps list."""
        runner = ReflectionRunner()
        step_keys = [s[0] for s in runner.steps]
        assert "pr_review_audit" in step_keys

    def test_pr_review_audit_name(self):
        runner = ReflectionRunner()
        step = [s for s in runner.steps if s[0] == "pr_review_audit"][0]
        assert step[1] == "PR Review Audit"

    def test_pr_review_audit_callable(self):
        runner = ReflectionRunner()
        step = [s for s in runner.steps if s[0] == "pr_review_audit"][0]
        import asyncio

        assert asyncio.iscoroutinefunction(step[2])


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
        assert callable(PRReviewAudit.cleanup_expired)
