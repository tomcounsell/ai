"""
reflections/auditing.py — Auditing reflection callables.

Extracted from scripts/reflections.py steps:
  - step_review_logs         → run_log_review
  - step_audit_docs          → run_documentation_audit
  - step_skills_audit        → run_skills_audit
  - step_hooks_audit         → run_hooks_audit
  - step_feature_docs_audit  → run_feature_docs_audit
  - step_pr_review_audit     → run_pr_review_audit

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

from reflections.utils import PROJECT_ROOT, extract_structured_errors, load_local_projects

logger = logging.getLogger("reflections.auditing")

# PR Review audit helper patterns (from monolith module level)
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


async def run_log_review() -> dict:
    """Review previous day's logs per project.

    Maps to monolith step: step_review_logs
    """
    from bridge.utc import utc_now

    projects = load_local_projects()
    findings: list[str] = []
    total_files_analyzed = 0
    yesterday = (utc_now() - timedelta(days=1)).strftime("%Y-%m-%d")

    # Query Redis BridgeEvent for structured errors
    try:
        from models.bridge_event import BridgeEvent

        all_events = BridgeEvent.query.filter(event_type="error")
        redis_errors = []
        for event in all_events:
            if event.timestamp:
                event_date = datetime.fromtimestamp(event.timestamp).strftime("%Y-%m-%d")
                if event_date == yesterday:
                    data = event.data or {}
                    redis_errors.append(
                        {
                            "timestamp": datetime.fromtimestamp(event.timestamp).strftime(
                                "%Y-%m-%d %H:%M:%S"
                            ),
                            "message": data.get("error", data.get("message", str(data))),
                        }
                    )
        if redis_errors:
            findings.append(f"Redis BridgeEvent: {len(redis_errors)} error events yesterday")
            for error in redis_errors[-5:]:
                msg = error["message"][:200]
                findings.append(f"  [BridgeEvent] {error['timestamp']}: {msg}")
    except Exception as e:
        logger.warning(f"Could not query BridgeEvent: {e}")

    for project in projects:
        slug = project["slug"]
        project_dir = Path(project["working_directory"])
        logs_dir = project_dir / "logs"

        if not logs_dir.exists():
            continue

        log_files = list(logs_dir.glob("*.log"))

        for log_file in log_files:
            if not log_file.is_file():
                continue

            try:
                from bridge.utc import utc_now as _utc_now

                mtime = datetime.fromtimestamp(log_file.stat().st_mtime, tz=UTC)
                if mtime < _utc_now() - timedelta(days=7):
                    findings.append(f"[{slug}] Log file {log_file.name} is older than 7 days")

                size_mb = log_file.stat().st_size / (1024 * 1024)
                if size_mb > 10:
                    findings.append(
                        f"[{slug}] Log file {log_file.name} is {size_mb:.1f}MB - consider rotation"
                    )

                errors = extract_structured_errors(log_file)
                if errors:
                    findings.append(
                        f"[{slug}] {log_file.name}: {len(errors)} structured errors extracted"
                    )
                    for error in errors[-5:]:
                        msg = error["message"][:200]
                        findings.append(f"  [{error['level']}] {error['timestamp']}: {msg}")

                with open(log_file) as f:
                    lines = f.readlines()[-1000:]
                warning_count = sum(1 for line in lines if "WARNING" in line)
                if warning_count > 10:
                    findings.append(
                        f"[{slug}] {log_file.name}: {warning_count} warnings in recent logs"
                    )

                # Detect nudge-stomp regression
                with open(log_file) as f:
                    log_content = f.read()
                stale_index_count = log_content.count("Stale index entry")
                if stale_index_count > 0:
                    findings.append(
                        f"[{slug}] {log_file.name}: {stale_index_count} 'Stale index entry' "
                        "warnings (regression marker for #898)"
                    )

            except Exception as e:
                findings.append(f"[{slug}] Could not analyze {log_file.name}: {str(e)}")

        total_files_analyzed += len(log_files)

    summary = f"Log review: analyzed {total_files_analyzed} files, {len(findings)} finding(s)"
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}


async def run_documentation_audit() -> dict:
    """Audit documentation against codebase.

    Maps to monolith step: step_audit_docs
    Delegates to DocsAuditor for intelligent audit.
    """
    import asyncio

    try:
        from scripts.docs_auditor import DocsAuditor

        auditor = DocsAuditor(repo_root=PROJECT_ROOT, dry_run=False)
        summary_obj = await asyncio.to_thread(auditor.run)

        findings = []
        if summary_obj.skipped:
            findings.append(f"Docs audit skipped: {summary_obj.skip_reason}")
        else:
            if len(summary_obj.updated) > 0:
                findings.append(f"Updated {len(summary_obj.updated)} docs with corrections")
            if len(summary_obj.deleted) > 0:
                findings.append(f"Deleted {len(summary_obj.deleted)} stale/inaccurate docs")
            if (
                len(summary_obj.kept) > 0
                and len(summary_obj.updated) == 0
                and len(summary_obj.deleted) == 0
            ):
                findings.append(f"All {len(summary_obj.kept)} docs verified accurate")

        summary = (
            f"Docs audit: kept={len(summary_obj.kept)}, "
            f"updated={len(summary_obj.updated)}, "
            f"deleted={len(summary_obj.deleted)}"
        )
        logger.info(summary)
        return {"status": "ok", "findings": findings, "summary": summary}

    except Exception as e:
        logger.warning(f"Documentation audit failed: {e}")
        return {"status": "error", "findings": [], "summary": f"Docs audit error: {e}"}


async def run_skills_audit() -> dict:
    """Run skills audit to validate all SKILL.md files.

    Maps to monolith step: step_skills_audit
    """
    audit_script = (
        PROJECT_ROOT / ".claude" / "skills" / "do-skills-audit" / "scripts" / "audit_skills.py"
    )
    if not audit_script.exists():
        logger.warning("Skills audit script not found, skipping")
        return {"status": "ok", "findings": [], "summary": "Skills audit script not found, skipped"}

    try:
        result = subprocess.run(
            [sys.executable, str(audit_script), "--no-sync", "--json"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(PROJECT_ROOT),
        )
        audit_data = json.loads(result.stdout) if result.stdout else {}
        sub_summary = audit_data.get("summary", {})
        fails = sub_summary.get("fail", 0)
        warns = sub_summary.get("warn", 0)
        total = sub_summary.get("total_skills", 0)

        findings = []
        if fails > 0:
            findings.append(f"{fails} skill(s) have FAIL findings")
            for f in audit_data.get("findings", []):
                if f.get("severity") == "FAIL":
                    findings.append(f"  {f.get('skill')}: {f.get('message')}")

        summary = f"Skills audit: {total} skills, {fails} fails, {warns} warns"
        logger.info(summary)
        return {"status": "ok", "findings": findings, "summary": summary}

    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        logger.error(f"Skills audit failed: {e}")
        return {"status": "error", "findings": [], "summary": f"Skills audit error: {e}"}


async def run_hooks_audit() -> dict:
    """Audit Claude Code hooks for safety and configuration issues.

    Maps to monolith step: step_hooks_audit
    Checks: hooks.log for recent errors, settings.json hook configuration.
    """
    findings: list[str] = []
    error_count = 0
    settings_issues = 0

    from bridge.utc import utc_now

    # 1. Scan hooks.log for recent errors
    hooks_log = PROJECT_ROOT / "logs" / "hooks.log"
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

    # 2. Validate settings.json hook configuration
    settings_path = PROJECT_ROOT / ".claude" / "settings.json"
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
                                full_path = PROJECT_ROOT / script_path
                                if not full_path.exists():
                                    findings.append(f"WARN: Hook script not found: {script_path}")
                                    settings_issues += 1
                                break
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to parse settings.json: {e}")
            settings_issues += 1

    summary = f"Hooks audit: {error_count} log errors, {settings_issues} settings issues"
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}


async def run_feature_docs_audit() -> dict:
    """Audit feature documentation for staleness and accuracy.

    Maps to monolith step: step_feature_docs_audit
    Checks: stale references, README index, stub docs, dead code refs.
    """
    findings: list[str] = []
    features_dir = PROJECT_ROOT / "docs" / "features"

    if not features_dir.exists():
        return {"status": "ok", "findings": [], "summary": "No docs/features dir found"}

    feature_files = sorted(features_dir.glob("*.md"))
    readme_path = features_dir / "README.md"

    stale_terms = {
        "SessionLog": "AgentSession",
        "RedisJob": "AgentSession",
        "session_log": "agent_session",
        "redis_job": "agent_session",
    }

    stats = {
        "total_docs": len(feature_files),
        "current": 0,
        "stale_refs": 0,
        "stubs": 0,
        "plan_masquerade": 0,
        "dead_code_refs": 0,
    }

    for doc_file in feature_files:
        if doc_file.name == "README.md":
            continue

        text = doc_file.read_text(errors="replace")
        doc_findings: list[str] = []

        for old_term, new_term in stale_terms.items():
            if old_term in text:
                migration_context = (
                    f"renamed to {new_term}" in text
                    or f"replaced by {new_term}" in text
                    or f"now {new_term}" in text
                    or f"formerly {old_term}" in text
                    or f"Replaces {old_term}" in text
                    or f"replaces {old_term}" in text
                )
                if not migration_context:
                    doc_findings.append(f"stale term '{old_term}' (now '{new_term}')")

        content_lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
        if len(content_lines) < 5:
            stats["stubs"] += 1
            doc_findings.append("stub doc (<5 content lines)")

        unchecked = re.findall(r"- \[ \]", text)
        checked_boxes = re.findall(r"- \[[xX]\]", text)
        if unchecked and len(unchecked) > len(checked_boxes):
            stats["plan_masquerade"] += 1
            doc_findings.append(
                f"looks like a plan ({len(unchecked)} unchecked, "
                f"{len(checked_boxes)} checked checkboxes)"
            )

        code_refs = re.findall(
            r"(?:`|\b)((?:agent|bridge|models|tools|scripts|config)/\S+\.py)",
            text,
        )
        for ref in code_refs:
            ref_path = PROJECT_ROOT / ref
            if not ref_path.exists():
                stats["dead_code_refs"] += 1
                doc_findings.append(f"references non-existent file: {ref}")

        if doc_findings:
            stats["stale_refs"] += len([f for f in doc_findings if f.startswith("stale term")])
            for df in doc_findings:
                findings.append(f"{doc_file.name}: {df}")
        else:
            stats["current"] += 1

    # README index validation
    if readme_path.exists():
        readme_text = readme_path.read_text(errors="replace")
        actual_files = {f.name for f in feature_files if f.name != "README.md"}
        readme_refs = set(re.findall(r"\[.*?\]\(([^)]+\.md)\)", readme_text))
        readme_refs = {r.lstrip("./") for r in readme_refs}

        for f in sorted(actual_files - readme_refs):
            findings.append(f"README.md missing entry for: {f}")
        for f in sorted(readme_refs - actual_files):
            findings.append(f"README.md references non-existent doc: {f}")

    summary = (
        f"Feature docs audit: {stats['total_docs']} docs, "
        f"{stats['current']} current, {len(findings)} finding(s)"
    )
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}


async def run_pr_review_audit() -> dict:
    """Audit merged PRs for unaddressed review findings.

    Maps to monolith step: step_pr_review_audit
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

        repo = github_config.get("repo", "")
        if not repo:
            continue

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
                logger.warning(f"PR review audit: gh pr list failed for {slug}")
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
