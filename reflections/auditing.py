"""
reflections/auditing.py — Auditing reflection callables.

All functions accept no arguments and return:
  {"status": "ok"|"error", "findings": [...], "summary": str}
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from reflections.utils import (
    PROJECT_ROOT,
    extract_structured_errors,
    load_local_projects,
    run_per_project_audit,
)

logger = logging.getLogger("reflections.auditing")


# PR Review audit helper patterns
_FINDING_SEVERITY_RE = re.compile(r"\*\*Severity:\*\*\s*(blocker|tech_debt|nit)", re.IGNORECASE)
_FINDING_FILE_RE = re.compile(r"\*\*File:\*\*\s*`?([^\n`]+)`?")
_FINDING_CODE_RE = re.compile(r"\*\*Code:\*\*\s*`?([^\n`]+)`?")
_FINDING_ISSUE_RE = re.compile(r"\*\*Issue:\*\*\s*(.+?)(?=\n\*\*|\Z)", re.DOTALL)
_FINDING_FIX_RE = re.compile(r"\*\*Fix:\*\*\s*(.+?)(?=\n\*\*|\Z)", re.DOTALL)

SEVERITY_MAP = {
    "blocker": "critical",
    "tech_debt": "standard",
    "nit": "trivial",
}

SEVERITY_LABELS = {
    "critical": "critical",
    "standard": "tech-debt",
    "trivial": "nit",
}


def _parse_review_findings(body: str) -> list[dict[str, str]]:
    """Extract structured findings from a PR review comment body."""
    if not body:
        return []

    findings = []
    severity_matches = list(_FINDING_SEVERITY_RE.finditer(body))
    if not severity_matches:
        return []

    for i, sev_match in enumerate(severity_matches):
        start = sev_match.start()
        end = severity_matches[i + 1].start() if i + 1 < len(severity_matches) else len(body)
        section = body[start:end]

        raw_severity = sev_match.group(1).lower()
        severity = SEVERITY_MAP.get(raw_severity, "standard")

        issue_match = _FINDING_ISSUE_RE.search(section)
        if not issue_match:
            continue

        file_match = _FINDING_FILE_RE.search(section)
        code_match = _FINDING_CODE_RE.search(section)
        fix_match = _FINDING_FIX_RE.search(section)

        findings.append(
            {
                "severity": severity,
                "raw_severity": raw_severity,
                "file_path": file_match.group(1).strip() if file_match else "",
                "code": code_match.group(1).strip() if code_match else "",
                "issue_description": issue_match.group(1).strip(),
                "suggested_fix": fix_match.group(1).strip() if fix_match else "",
            }
        )

    return findings


def _check_finding_addressed(pr_commits: list[dict], review_timestamp: str, file_path: str) -> bool:
    """Check if a finding was addressed by a commit after the review."""
    if not file_path or not pr_commits:
        return False

    for commit in pr_commits:
        commit_date = commit.get("commit", {}).get("committer", {}).get("date", "")
        if not commit_date:
            continue
        if commit_date > review_timestamp:
            files = commit.get("files", [])
            for f in files:
                if f.get("filename", "") == file_path:
                    return True
    return False


def _format_audit_issue_body(
    pr_number: int, pr_title: str, pr_url: str, unaddressed: list[dict]
) -> str:
    """Format the GitHub issue body for unaddressed PR review findings."""
    lines = [
        "## Unaddressed PR Review Findings",
        "",
        f"**Source PR:** [{pr_title}]({pr_url}) (#{pr_number})",
        "",
    ]

    by_severity: dict[str, list[dict]] = {}
    for finding in unaddressed:
        sev = finding.get("severity", "standard")
        by_severity.setdefault(sev, []).append(finding)

    for severity in ["critical", "standard", "trivial"]:
        group = by_severity.get(severity, [])
        if not group:
            continue
        lines.append(f"### {severity.title()} ({len(group)})")
        lines.append("")
        for finding in group:
            lines.append(f"- **File:** `{finding.get('file_path', 'N/A')}`")
            if finding.get("code"):
                lines.append(f"  **Code:** `{finding['code']}`")
            lines.append(f"  **Issue:** {finding.get('issue_description', 'N/A')}")
            if finding.get("suggested_fix"):
                lines.append(f"  **Fix:** {finding['suggested_fix']}")
            if finding.get("review_url"):
                lines.append(f"  [Review comment]({finding['review_url']})")
            lines.append("")

    lines.append("---")
    lines.append("*Filed automatically by the reflections PR review audit.*")
    return "\n".join(lines)


def _skills_audit_script_path(repo_root: Path) -> Path:
    """Return the path to a repo's `audit_skills.py` (does not check existence)."""
    return repo_root / ".claude" / "skills" / "do-skills-audit" / "scripts" / "audit_skills.py"


def _skills_audit_for_project(project: dict) -> dict:
    """Per-project body for skills-audit.

    Invokes the TARGET repo's copy of ``audit_skills.py`` (NOT the AI repo's
    copy) so each repo audits its own skills. The script self-derives
    REPO_ROOT from its own file location.
    """
    import time as _time

    wd = project.get("working_directory", "")
    repo_root = Path(wd) if wd else PROJECT_ROOT
    audit_script = _skills_audit_script_path(repo_root)

    t0 = _time.time()
    try:
        result = subprocess.run(
            [sys.executable, str(audit_script), "--no-sync", "--json"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(repo_root),
        )
        audit_data = json.loads(result.stdout) if result.stdout else {}
        sub_summary = audit_data.get("summary", {})
        fails = sub_summary.get("fail", 0)
        warns = sub_summary.get("warn", 0)
        total = sub_summary.get("total_skills", 0)

        findings: list[str] = []
        if fails > 0:
            findings.append(f"{fails} skill(s) have FAIL findings")
            for f in audit_data.get("findings", []):
                if f.get("severity") == "FAIL":
                    findings.append(f"  {f.get('skill')}: {f.get('message')}")

        return {
            "status": "ok",
            "findings": findings,
            "summary": f"Skills audit: {total} skills, {fails} fails, {warns} warns",
            "duration": _time.time() - t0,
        }
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        logger.error(f"Skills audit failed for {project.get('slug')}: {e}")
        return {
            "status": "error",
            "findings": [],
            "summary": f"Skills audit error: {e}",
            "duration": _time.time() - t0,
            "error": str(e),
        }


def run_skills_audit() -> dict:
    """Run skills audit per project.

    Iterates every local project that has its own
    ``.claude/skills/do-skills-audit/scripts/audit_skills.py`` script and
    runs the audit there. Projects without the script are silently skipped.
    """

    def skip_if(repo_root: Path) -> bool:
        return not _skills_audit_script_path(repo_root).exists()

    return run_per_project_audit(_skills_audit_for_project, skip_if=skip_if, name="skills-audit")


def _hooks_audit_for_project(project: dict) -> dict:
    """Per-project body for hooks-audit.

    Scans the project's ``logs/hooks.log`` for recent errors AND validates
    its ``.claude/settings.json`` hook configuration. Both file paths are
    rooted at the project's working_directory.
    """
    import time as _time

    from bridge.utc import utc_now

    wd = project.get("working_directory", "")
    repo_root = Path(wd) if wd else PROJECT_ROOT

    findings: list[str] = []
    error_count = 0
    settings_issues = 0
    t0 = _time.time()

    hooks_log = repo_root / "logs" / "hooks.log"
    if hooks_log.exists():
        try:
            errors = extract_structured_errors(hooks_log)
            cutoff = (utc_now() - timedelta(days=1)).strftime("%Y-%m-%d")
            recent = [e for e in errors if e.get("timestamp", "") >= cutoff]
            error_count = len(recent)
            if recent:
                hook_names = set()
                for e in recent:
                    msg = e.get("message", "")
                    parts = msg.split(" - ")
                    if parts:
                        hook_names.add(parts[0].strip())
                names = ", ".join(sorted(hook_names)) or "unknown"
                findings.append(f"{error_count} hook error(s) in last 24h from: {names}")
        except Exception as e:
            logger.warning(f"Failed to scan hooks.log: {e}")

    settings_path = repo_root / ".claude" / "settings.json"
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
            hooks = settings.get("hooks", {})

            for event_type, matchers in hooks.items():
                for matcher_block in matchers:
                    for hook in matcher_block.get("hooks", []):
                        cmd = hook.get("command", "")
                        has_or_true = "|| true" in cmd

                        if event_type in ("Stop", "SubagentStop") and not has_or_true:
                            findings.append(f"FAIL: {event_type} hook missing || true: {cmd[:60]}")
                            settings_issues += 1

                        for part in cmd.replace("|| true", "").split():
                            if part.endswith(".py") or part.endswith(".sh"):
                                script_path = part.replace('"$CLAUDE_PROJECT_DIR"/', "").replace(
                                    "$CLAUDE_PROJECT_DIR/", ""
                                )
                                full_path = repo_root / script_path
                                if not full_path.exists():
                                    findings.append(f"WARN: Hook script not found: {script_path}")
                                    settings_issues += 1
                                break
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to parse settings.json: {e}")
            settings_issues += 1

    return {
        "status": "ok",
        "findings": findings,
        "summary": f"Hooks audit: {error_count} log errors, {settings_issues} settings issues",
        "duration": _time.time() - t0,
    }


def run_hooks_audit() -> dict:
    """Audit Claude Code hooks per project.

    Iterates every local project with EITHER ``logs/hooks.log`` OR
    ``.claude/settings.json`` present (skips projects with neither).

    Hotfix (sibling of PR #1056): the per-project body does only sync I/O
    (``extract_structured_errors``, ``json.loads``, ``Path.read_text``,
    ``Path.exists``) with no awaits — see ``test_event_loop_safe_callables_are_sync``.
    """

    def skip_if(repo_root: Path) -> bool:
        return not (
            (repo_root / "logs" / "hooks.log").exists()
            or (repo_root / ".claude" / "settings.json").exists()
        )

    return run_per_project_audit(_hooks_audit_for_project, skip_if=skip_if, name="hooks-audit")


def run_pr_review_audit() -> dict:
    """Audit merged PRs for unaddressed review findings.

    Runs in dry_run=True mode by default to avoid spurious issue creation.
    """
    from bridge.utc import utc_now

    try:
        from models.reflections import PRReviewAudit
    except Exception as e:
        logger.warning(f"PR review audit: could not import PRReviewAudit: {e}")
        return {"status": "error", "findings": [], "summary": f"Import error: {e}"}

    projects = load_local_projects()
    dry_run = True  # Safe default: log but don't file issues
    prs_scanned = 0
    findings_total = 0
    findings_unaddressed = 0
    issues_filed = 0
    findings: list[str] = []

    last_run = PRReviewAudit.last_successful_run()
    if last_run:
        last_audit_date = datetime.fromtimestamp(last_run, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        last_audit_date = (utc_now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    search_date = last_audit_date[:10]

    for project in projects:
        slug = project["slug"]
        github_config = project.get("github")
        if not github_config:
            continue

        org = github_config.get("org", "")
        repo_name = github_config.get("repo", "")
        if not org or not repo_name:
            continue
        repo = f"{org}/{repo_name}"

        project_wd = project["working_directory"]

        try:
            pr_result = subprocess.run(
                [
                    "gh",
                    "pr",
                    "list",
                    "--repo",
                    repo,
                    "--state",
                    "merged",
                    "--limit",
                    "20",
                    "--search",
                    f"merged:>={search_date}",
                    "--json",
                    "number,title,url,mergedAt",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=project_wd,
            )

            if pr_result.returncode != 0:
                logger.warning(
                    f"PR review audit: gh pr list failed for {slug} (repo={repo}): "
                    f"{pr_result.stderr.strip()}"
                )
                continue

            prs = json.loads(pr_result.stdout) if pr_result.stdout.strip() else []

            for pr in prs:
                pr_number = pr.get("number")
                pr_title = pr.get("title", "")
                pr_url = pr.get("url", "")
                prs_scanned += 1

                try:
                    comments_result = subprocess.run(
                        ["gh", "api", f"repos/{repo}/pulls/{pr_number}/comments", "--paginate"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        cwd=project_wd,
                    )
                    reviews_result = subprocess.run(
                        ["gh", "api", f"repos/{repo}/pulls/{pr_number}/reviews", "--paginate"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        cwd=project_wd,
                    )

                    all_comments: list[dict] = []
                    if comments_result.returncode == 0 and comments_result.stdout.strip():
                        for comment in json.loads(comments_result.stdout):
                            all_comments.append(
                                {
                                    "id": comment.get("id", 0),
                                    "body": comment.get("body", ""),
                                    "created_at": comment.get("created_at", ""),
                                    "html_url": comment.get("html_url", ""),
                                }
                            )
                    if reviews_result.returncode == 0 and reviews_result.stdout.strip():
                        for review in json.loads(reviews_result.stdout):
                            body = review.get("body", "")
                            if body and body.strip():
                                all_comments.append(
                                    {
                                        "id": review.get("id", 0),
                                        "body": body,
                                        "created_at": review.get("submitted_at", ""),
                                        "html_url": review.get("html_url", ""),
                                    }
                                )

                    if not all_comments:
                        continue

                    commits_result = subprocess.run(
                        ["gh", "api", f"repos/{repo}/pulls/{pr_number}/commits", "--paginate"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        cwd=project_wd,
                    )
                    pr_commits: list[dict] = []
                    if commits_result.returncode == 0 and commits_result.stdout.strip():
                        pr_commits = json.loads(commits_result.stdout)

                    unaddressed_for_pr: list[dict] = []
                    for comment in all_comments:
                        comment_findings = _parse_review_findings(comment["body"])
                        for finding_idx, finding in enumerate(comment_findings):
                            findings_total += 1
                            comment_key = f"{repo}:{pr_number}:{comment['id']}:{finding_idx}"

                            if PRReviewAudit.is_audited(comment_key):
                                continue

                            if _check_finding_addressed(
                                pr_commits, comment["created_at"], finding["file_path"]
                            ):
                                if not dry_run:
                                    PRReviewAudit.mark_audited(
                                        comment_key=comment_key,
                                        repo=repo,
                                        pr_number=pr_number,
                                        severity=finding["severity"],
                                        issue_url=None,
                                    )
                                continue

                            findings_unaddressed += 1
                            finding["review_url"] = comment["html_url"]
                            finding["comment_key"] = comment_key
                            unaddressed_for_pr.append(finding)

                    if unaddressed_for_pr:
                        if dry_run:
                            findings.append(
                                f"[DRY RUN] Would file issue for PR #{pr_number} in {slug}: "
                                f"{len(unaddressed_for_pr)} unaddressed findings"
                            )
                        else:
                            # Actual filing (dry_run=False not enabled by default)
                            labels = ["pr-review-audit"] + sorted(
                                {
                                    SEVERITY_LABELS.get(f["severity"], "tech-debt")
                                    for f in unaddressed_for_pr
                                }
                            )
                            issue_body = _format_audit_issue_body(
                                pr_number, pr_title, pr_url, unaddressed_for_pr
                            )
                            issue_result = subprocess.run(
                                [
                                    "gh",
                                    "issue",
                                    "create",
                                    "--repo",
                                    repo,
                                    "--title",
                                    f"PR #{pr_number}: unaddressed review findings",
                                    "--body",
                                    issue_body,
                                ]
                                + [arg for label in labels for arg in ("--label", label)],
                                capture_output=True,
                                text=True,
                                timeout=30,
                                cwd=project_wd,
                            )
                            if issue_result.returncode == 0:
                                issue_url = issue_result.stdout.strip()
                                issues_filed += 1
                                findings.append(
                                    f"Filed issue for PR #{pr_number}: "
                                    f"{len(unaddressed_for_pr)} unaddressed findings -> {issue_url}"
                                )

                except Exception as e:
                    logger.warning(f"PR review audit: failed processing PR #{pr_number}: {e}")

        except Exception as e:
            logger.warning(f"PR review audit: failed for project {slug}: {e}")

    summary = (
        f"PR review audit: {prs_scanned} PRs scanned, "
        f"{findings_unaddressed} unaddressed findings, {issues_filed} issues filed"
    )
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}
