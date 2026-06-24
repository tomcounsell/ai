"""reflections/audits/pr_review_audit.py — Unaddressed PR-review-finding audit.

What it does: Scans recently merged PRs per project for structured review
    findings, checks whether each was addressed by a post-review commit, and
    (in non-dry-run mode) files a GitHub issue listing unaddressed findings.
    Defaults to dry_run=True (logs only, no issue creation).
Cadence: 86400s (PRs merge on a daily cadence; window keyed off last run)
Failure modes:
    - PRReviewAudit model import fails -> error status returned
    - gh pr list / gh api failure -> per-project/per-PR warning, skipped
    - per-PR processing exception -> logged, other PRs continue
Related reflections:
    - skills-audit: also auto-files GitHub issues for code-quality findings
See also: config/reflections.yaml (declaration), docs/features/reflections.md
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import UTC, datetime, timedelta

from reflections.utilities import load_local_projects

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


def run() -> dict:
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
