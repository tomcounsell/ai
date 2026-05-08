"""
reflections/sentry_triage.py — Sentry issue triage reflection callable.

Queries the Sentry API for unresolved issues across all projects in the org,
classifies them (A–E), files GitHub issues for actionable ones (dry-run by
default), and sends a summary to Telegram.

Classification:
  A — Ignore: test/mock/harness noise
  B — Low: known transients (rate limits, network timeouts)
  C — Actionable: real bugs worth a GitHub issue
  D — Investigate: ambiguous, needs human review
  E — Stale: no events in 30 days, candidate for resolve

All functions accept no arguments and return:
  {"status": "ok"|"error"|"disabled", "findings": [...], "summary": str}
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

from reflections.utils import PROJECT_ROOT, load_local_projects

logger = logging.getLogger("reflections.sentry_triage")

SENTRY_API_BASE = "https://yudame.sentry.io/api/0"

DRY_RUN = True  # Safe default: log only, don't file GitHub issues

# Classification patterns
_CLASS_A_PATTERNS = [
    "test_",
    "simulated",
    "_HARNESS_",
    "harness]",
    "harness exploded",
    "harness binary not found",
    "[test_session]",
    "[continuation-pm-blocked]",
    "executor-guard]",
    "dead letter",
    "drafter dead",
    "Pass 1 failure",
    "Pass 2 returned _HARNESS_NOT_FOUND",
    "MagicMock",
    "must be set",  # config errors like "TELEGRAM_API_ID and TELEGRAM_API_HASH must be set"
    "No such file or directory: '/nonexistent",
    'No such file or directory: "/nonexistent',
    'No such file or directory: "/tmp/fake',
    'No such file or directory: "/private/tmp/foo',
    "[test-project]",
    "[test]",
    "[test] failed",
    "All drafting backends failed",
    "All summarization backends failed",
    "Refusing to clean up worktree",
]

_CLASS_B_PATTERNS = [
    "rate limit",
    "429",
    "throttl",
    "Network error",
    "ConnectionError",
    "ConnectionRefusedError",
    "Connection refused",
    "connection lost",
    "TimeoutError",
    "timed out",
    "ETIMEDOUT",
    "ECONNREFUSED",
    "ECONNRESET",
    "PeerIdInvalidError",
    "BAD_REQUEST",
    "BadRequestError",
    "Invalid 'input",
    "maximum input length",
    "redis down",
    "Redis down",
    "redis connection lost",
    "Redis connection lost",
    "API down",
    "API error",
    "auth",
    "401",
    "403",
    "AuthenticationError",
    "Permission denied",
    "Event loop is closed",
    "self-draft",
    "transient error",
    "Cloudflare",
    "SMTP refused",
]

_STALE_DAYS = 30
_STALE_MAX_EVENTS = 50


def _get_auth_token() -> str | None:
    """Return the Sentry auth token from env or .env file."""
    token = os.environ.get("SENTRY_AUTH_TOKEN")
    if token:
        return token
    env_file = PROJECT_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("SENTRY_AUTH_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _get_org_slug() -> str:
    """Return the Sentry org slug from env or .env file, defaulting to 'yudame'."""
    slug = os.environ.get("SENTRY_ORG_SLUG")
    if slug:
        return slug
    env_file = PROJECT_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("SENTRY_ORG_SLUG="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "yudame"


def _fetch_unresolved_issues(auth_token: str, org_slug: str) -> list[dict]:
    """Fetch all unresolved issues from the Sentry API across all projects.

    Returns a flat list of issue dicts. Paginates up to 5 pages (500 issues).
    """
    all_issues: list[dict] = []
    url = f"{SENTRY_API_BASE}/organizations/{org_slug}/issues/"
    headers = {"Authorization": f"Bearer {auth_token}"}
    params = {"query": "is:unresolved", "limit": 100}

    for _ in range(5):  # max 5 pages
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"sentry_triage: API request failed: {e}")
            break

        page = resp.json()
        if not isinstance(page, list):
            break
        all_issues.extend(page)
        if len(page) < 100:
            break

        # Follow pagination
        next_link = resp.links.get("next", {}).get("url")
        if next_link:
            url = next_link
            params = {}  # params are in the URL for pagination links
        else:
            break

    return all_issues


def _classify_issue(issue: dict) -> tuple[str, str]:
    """Classify a Sentry issue into (class_letter, reason).

    Returns:
        Tuple of (class, reason). Class is one of A, B, C, D, E.
    """
    title = issue.get("title", "")
    last_seen = issue.get("lastSeen", "")
    event_count = int(issue.get("count", 0))

    # Class E: stale issues (no recent events)
    if last_seen:
        try:
            last_dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            days_old = (datetime.now(UTC) - last_dt).days
            if days_old > _STALE_DAYS and event_count <= _STALE_MAX_EVENTS:
                return "E", f"stale ({days_old}d old, {event_count} events)"
        except (ValueError, TypeError):
            pass

    # Class A: test/mock/harness noise
    for pattern in _CLASS_A_PATTERNS:
        if pattern.lower() in title.lower():
            return "A", f"noise pattern: '{pattern}'"

    # Class B: known transients
    for pattern in _CLASS_B_PATTERNS:
        if pattern.lower() in title.lower():
            return "B", f"transient pattern: '{pattern}'"

    # Default: Class C (actionable) if high event count, Class D (investigate) otherwise
    if event_count >= 10:
        return "C", f"actionable bug ({event_count} events)"
    return "D", f"needs investigation ({event_count} events)"


def _issue_already_filed(title: str, cwd: str) -> bool:
    """Check if an open GitHub issue with this title already exists."""
    search_term = title[:50]
    try:
        result = subprocess.run(
            ["gh", "issue", "list", "--state", "open", "--search", search_term],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            cwd=cwd,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


def _file_github_issue(
    issue: dict, project_slug: str, repo_root: Path, classification: str, reason: str
) -> str | None:
    """File a GitHub issue for a Class C Sentry finding. Returns URL or None.

    Deduplicates by checking for existing open issues via gh CLI.
    """
    title = f"[Sentry] {issue.get('title', 'Unknown error')}"
    short_title = title[:72]  # gh issue title length practical limit

    # Dedup: check for existing open issue with same search term
    if _issue_already_filed(short_title, str(repo_root)):
        logger.info(f"sentry_triage: dedup skip — issue already exists for '{short_title[:50]}'")
        return None

    issue_url = issue.get("permalink", "")
    short_id = issue.get("shortId", "?")
    event_count = issue.get("count", "?")
    first_seen = issue.get("firstSeen", "?")
    last_seen = issue.get("lastSeen", "?")

    body_parts = [
        f"## Sentry Issue: {short_id}",
        "",
        f"**Title:** {issue.get('title', 'Unknown')}",
        f"**Project:** {project_slug}",
        f"**Events:** {event_count}",
        f"**First seen:** {first_seen}",
        f"**Last seen:** {last_seen}",
        f"**Classification:** {classification} — {reason}",
        f"**Link:** {issue_url}",
        "",
        "---",
        "*Filed automatically by sentry-issue-triage reflection.*",
    ]
    body = "\n".join(body_parts)

    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "create",
                "--title",
                short_title,
                "--body",
                body,
                "--label",
                "bug",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            cwd=str(repo_root),
        )
        if result.returncode == 0:
            filed_url = result.stdout.strip()
            logger.info(f"sentry_triage: filed GitHub issue: {filed_url}")
            return filed_url
        else:
            logger.warning(
                f"sentry_triage: gh issue create failed (rc={result.returncode}): "
                f"{result.stderr.strip()[:200]}"
            )
            return None
    except Exception as e:
        logger.warning(f"sentry_triage: gh issue create exception: {e}")
        return None


def _send_telegram_notification(message: str) -> None:
    """Best-effort Telegram notification. Swallows all subprocess failures."""
    try:
        subprocess.run(
            ["valor-telegram", "send", "--chat", "Dev: Valor", message],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except FileNotFoundError:
        logger.warning("sentry_triage: valor-telegram not on PATH; skipping Telegram notify")
    except subprocess.TimeoutExpired:
        logger.warning("sentry_triage: valor-telegram send timed out")
    except Exception as e:
        logger.warning(f"sentry_triage: valor-telegram send failed: {e}")


def run_sentry_triage() -> dict:
    """Triage unresolved Sentry issues across all projects.

    Queries the Sentry API, classifies each issue (A–E), files GitHub
    issues for actionable bugs (dry-run by default), and sends a summary
    to Telegram.

    Returns:
        Dict with status, findings, and summary.
    """
    t0 = time.time()

    auth_token = _get_auth_token()
    if not auth_token:
        return {
            "status": "disabled",
            "findings": [],
            "summary": "sentry-issue-triage: no SENTRY_AUTH_TOKEN configured",
        }

    org_slug = _get_org_slug()

    # Fetch issues
    issues = _fetch_unresolved_issues(auth_token, org_slug)
    if not issues:
        summary = f"sentry-issue-triage: 0 unresolved issues (org={org_slug})"
        logger.info(summary)
        return {"status": "ok", "findings": [], "summary": summary}

    # Group by project
    by_project: dict[str, list[dict]] = {}
    for issue in issues:
        proj = issue.get("project", {}).get("slug", "unknown")
        by_project.setdefault(proj, []).append(issue)

    # Classify
    classified: dict[str, list[tuple[dict, str, str]]] = {
        "A": [],
        "B": [],
        "C": [],
        "D": [],
        "E": [],
    }
    for issue in issues:
        cls, reason = _classify_issue(issue)
        classified[cls].append((issue, cls, reason))

    findings: list[str] = []
    issues_filed = 0

    # Report Class A (noise) — just count
    if classified["A"]:
        findings.append(f"Class A (noise): {len(classified['A'])} issues to ignore")
        for issue, cls, reason in classified["A"][:5]:
            findings.append(f"  {issue.get('shortId', '?')}: {reason}")

    # Report Class B (transients) — just count
    if classified["B"]:
        findings.append(f"Class B (transient): {len(classified['B'])} issues to monitor")
        for issue, cls, reason in classified["B"][:5]:
            findings.append(f"  {issue.get('shortId', '?')}: {reason}")

    # Report Class C (actionable) — file GitHub issues
    if classified["C"]:
        findings.append(f"Class C (actionable): {len(classified['C'])} issues to fix")
        for issue, cls, reason in classified["C"]:
            short_id = issue.get("shortId", "?")
            title = issue.get("title", "?")[:80]
            proj = issue.get("project", {}).get("slug", "?")
            findings.append(f"  {short_id} [{proj}]: {title} ({reason})")

            if DRY_RUN:
                findings.append("    [DRY RUN] would file GitHub issue")
            else:
                # Determine project working directory for gh CLI
                proj_wd = None
                for project in load_local_projects():
                    if project.get("slug") == proj:
                        proj_wd = project.get("working_directory")
                        break

                if proj_wd:
                    filed_url = _file_github_issue(issue, proj, Path(proj_wd), cls, reason)
                    if filed_url:
                        issues_filed += 1
                        findings.append(f"    Filed: {filed_url}")
                else:
                    findings.append(f"    [SKIP] no working directory for project {proj}")

    # Report Class D (investigate) — list for review
    if classified["D"]:
        findings.append(f"Class D (investigate): {len(classified['D'])} issues needing review")
        for issue, cls, reason in classified["D"][:5]:
            findings.append(f"  {issue.get('shortId', '?')}: {reason}")

    # Report Class E (stale) — list for resolve
    if classified["E"]:
        findings.append(f"Class E (stale): {len(classified['E'])} issues to resolve")
        for issue, cls, reason in classified["E"][:5]:
            findings.append(f"  {issue.get('shortId', '?')}: {reason}")

    elapsed = time.time() - t0

    summary = (
        f"sentry-issue-triage: {len(issues)} issues across {len(by_project)} project(s) "
        f"(A={len(classified['A'])} B={len(classified['B'])} "
        f"C={len(classified['C'])} D={len(classified['D'])} E={len(classified['E'])})"
    )
    if DRY_RUN:
        summary += " [DRY RUN]"
    if issues_filed:
        summary += f", {issues_filed} GitHub issues filed"

    logger.info(summary)

    # Telegram only when there's a genuine question for the human:
    # Class D = "investigate" — issues that can't be auto-classified and need review.
    # A (noise), B (transient), C (actionable, auto-filed as GH issues), E (stale, auto-resolvable)
    # are all handled or logged silently to logs/reflections.log.
    if classified["D"]:
        tg_lines = [f"Sentry triage: {len(classified['D'])} issue(s) need your review"]
        for issue, _, reason in classified["D"][:5]:
            tg_lines.append(f"  {issue.get('shortId', '?')}: {issue.get('title', '')[:60]}")
            tg_lines.append(f"    why: {reason}")
        _send_telegram_notification("\n".join(tg_lines))

    return {
        "status": "ok",
        "findings": findings,
        "summary": summary,
        "duration": elapsed,
    }
