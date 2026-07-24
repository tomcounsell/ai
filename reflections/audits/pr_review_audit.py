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
    - gh issue create failure -> warning logged, [FAIL] finding appended,
      run returns status=error
Related reflections:
    - skills-audit: also auto-files GitHub issues for code-quality findings
See also: config/reflections.yaml (declaration), docs/features/reflections.md
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import UTC, datetime, timedelta

from config.settings import settings
from reflections.utilities import PROJECT_ROOT, load_local_projects

logger = logging.getLogger("reflections.auditing")

# Cloud-mode (COWORK_ROUTINE=1) lookback window in days, used in place of the
# PRReviewAudit watermark (which is bypassed entirely in cloud mode -- see
# guard 3 in run()). NOTE: provisional/tunable -- deliberately kept small so a
# steady-state daily cloud run only sees ~one day of newly merged PRs; widen
# via env if a one-off backfill run needs more history.
_DEFAULT_CLOUD_WINDOW_DAYS = 1
try:
    PR_REVIEW_AUDIT_CLOUD_WINDOW_DAYS = int(
        os.getenv("PR_REVIEW_AUDIT_CLOUD_WINDOW_DAYS", str(_DEFAULT_CLOUD_WINDOW_DAYS))
    )
except ValueError:
    logger.warning(
        "PR review audit: non-numeric PR_REVIEW_AUDIT_CLOUD_WINDOW_DAYS=%r; "
        "falling back to default %d",
        os.getenv("PR_REVIEW_AUDIT_CLOUD_WINDOW_DAYS"),
        _DEFAULT_CLOUD_WINDOW_DAYS,
    )
    PR_REVIEW_AUDIT_CLOUD_WINDOW_DAYS = _DEFAULT_CLOUD_WINDOW_DAYS


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


def _cloud_issue_already_filed(repo: str, pr_number: int, project_wd: str) -> bool:
    """Cloud-mode per-PR dedup: `gh` title-search in place of the bypassed
    `PRReviewAudit.is_audited()` Redis read (guard 3 skips all Redis
    touchpoints in cloud mode; guard 4 replaces per-finding dedup with this
    per-PR title-search since filing is per-PR, not per-finding).
    """
    title = f"PR #{pr_number}: unaddressed review findings"
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--repo",
                repo,
                "--state",
                "all",
                "--label",
                "pr-review-audit",
                "--search",
                f'in:title "{title}"',
                "--json",
                "title",
            ],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=project_wd,
        )
        if result.returncode != 0:
            return False
        issues = json.loads(result.stdout) if result.stdout.strip() else []
        return any(item.get("title") == title for item in issues)
    except Exception as e:
        logger.warning(f"PR review audit: cloud dedup check failed for {repo} PR #{pr_number}: {e}")
        return False


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

    # Guard: COWORK_ROUTINE=1 switches on the cloud-sandbox code path (project
    # synthesis, filing enablement, Redis-touchpoint bypass, gh title-dedup).
    # Inert (identical to today's behavior) when unset.
    cloud_mode = os.getenv("COWORK_ROUTINE") == "1"

    if cloud_mode:
        # Guard 1 (r5 B1): in cloud mode GH_REPO is ALWAYS the sole project
        # source. A fresh cloud sandbox has no ~/Desktop/Valor/projects.json,
        # so load_local_projects() returns [] there -- but on an operator
        # machine it would return every configured production repo, and a
        # COWORK_ROUTINE=1 smoke run (dry_run=False) would live-file against
        # all of them while silently ignoring GH_REPO. Synthesize the single
        # project record from GH_REPO unconditionally and fail loud if it is
        # unset or malformed (including extra path segments like "a/b/c").
        gh_repo = os.environ.get("GH_REPO", "")
        parts = gh_repo.split("/")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            msg = (
                f"PR review audit: COWORK_ROUTINE=1 but GH_REPO is unset/malformed "
                f"({gh_repo!r}); refusing to silently scan zero projects"
            )
            logger.error(msg)
            return {"status": "error", "findings": [], "summary": msg}
        org, repo_name = parts
        projects = [
            {
                "slug": f"{org}-{repo_name}",
                "working_directory": str(PROJECT_ROOT),
                "github": {"org": org, "repo": repo_name},
            }
        ]
    else:
        projects = load_local_projects()

    # Guard 2: cloud mode actually files.
    # NOTE: reviewer suggested writing this as `dry_run = not cloud_mode`; left
    # as an independent env read because the flags are conceptually independent
    # knobs (a future locally-enabled run could set dry_run=False without cloud
    # mode -- see the "future locally-enabled run" branch in the filing path).
    dry_run = os.getenv("COWORK_ROUTINE") != "1"
    prs_scanned = 0
    findings_total = 0
    findings_unaddressed = 0
    issues_filed = 0
    issues_failed = 0
    findings: list[str] = []

    if cloud_mode:
        # Guard 3: bypass the PRReviewAudit watermark entirely in cloud mode
        # (no Redis dependency) -- use a fixed lookback window instead. Note
        # (r5 B2): this watermark has never actually advanced locally either,
        # because dry_run has always been hardcoded True, so mark_audited (its
        # sole writer) has never fired -- last_successful_run() already
        # returns None on every real local run today.
        last_audit_date = (utc_now() - timedelta(days=PR_REVIEW_AUDIT_CLOUD_WINDOW_DAYS)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    else:
        last_run = PRReviewAudit.last_successful_run()
        if last_run:
            last_audit_date = datetime.fromtimestamp(last_run, tz=UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
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
                timeout=settings.timeouts.git_subprocess_s,
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
                        timeout=settings.timeouts.git_subprocess_s,
                        cwd=project_wd,
                    )
                    reviews_result = subprocess.run(
                        ["gh", "api", f"repos/{repo}/pulls/{pr_number}/reviews", "--paginate"],
                        capture_output=True,
                        text=True,
                        timeout=settings.timeouts.git_subprocess_s,
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
                        timeout=settings.timeouts.git_subprocess_s,
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

                            # Guard 3: skip the per-finding dedup read/write entirely
                            # in cloud mode -- no PRReviewAudit Redis call is reached.
                            # Cross-run dedup is delegated to the per-PR gh
                            # title-search below (guard 4) instead.
                            if not cloud_mode and PRReviewAudit.is_audited(comment_key):
                                continue

                            if _check_finding_addressed(
                                pr_commits, comment["created_at"], finding["file_path"]
                            ):
                                if not dry_run and not cloud_mode:
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
                        elif cloud_mode and _cloud_issue_already_filed(repo, pr_number, project_wd):
                            # Guard 4: per-PR gh title-search dedup, the cloud-mode
                            # replacement for the bypassed is_audited() read.
                            findings.append(
                                f"[SKIP] PR #{pr_number} in {slug} already filed "
                                "(cloud title-search dedup)"
                            )
                        else:
                            # Actual filing (dry_run=False, either cloud mode or a
                            # future locally-enabled run)
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
                                timeout=settings.timeouts.git_subprocess_s,
                                cwd=project_wd,
                            )
                            if issue_result.returncode == 0:
                                issue_url = issue_result.stdout.strip()
                                issues_filed += 1
                                findings.append(
                                    f"Filed issue for PR #{pr_number}: "
                                    f"{len(unaddressed_for_pr)} unaddressed findings -> {issue_url}"
                                )
                            else:
                                issues_failed += 1
                                stderr = issue_result.stderr.strip()
                                logger.warning(
                                    f"PR review audit: gh issue create failed for "
                                    f"PR #{pr_number} in {slug} (repo={repo}): {stderr}"
                                )
                                findings.append(
                                    f"[FAIL] gh issue create failed for PR #{pr_number} "
                                    f"in {slug}: {stderr}"
                                )

                except Exception as e:
                    logger.warning(f"PR review audit: failed processing PR #{pr_number}: {e}")

        except Exception as e:
            logger.warning(f"PR review audit: failed for project {slug}: {e}")

    summary = (
        f"PR review audit: {prs_scanned} PRs scanned, "
        f"{findings_unaddressed} unaddressed findings, {issues_filed} issues filed"
    )
    if issues_failed:
        summary += f", {issues_failed} issue creations FAILED"
        logger.warning(summary)
        return {"status": "error", "findings": findings, "summary": summary}
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}


# ---------------------------------------------------------------------------
# CLI entrypoint -- the committed on-demand recipe the Cowork CMA prompt
# invokes by name: `python -m reflections.audits.pr_review_audit --apply`.
# Filing/cloud-sandbox behavior is controlled entirely by the COWORK_ROUTINE
# and GH_REPO environment variables (set by the routine before invoking this
# module), not by CLI flags -- `--apply` is accepted for invocation-shape
# parity with other recipes and to make the intent explicit at the call site.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description=(
            "Run the PR review audit. Reads COWORK_ROUTINE/GH_REPO from the "
            "environment; COWORK_ROUTINE=1 enables cloud-sandbox mode "
            "(project synthesis, filing, Redis bypass, gh title-dedup)."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Documents intent to file issues; actual filing is gated on "
        "COWORK_ROUTINE=1 in the environment, not this flag.",
    )
    parser.parse_args()

    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") in ("ok", "disabled") else 1)
