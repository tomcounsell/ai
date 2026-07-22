"""
reflections/sentry_triage.py — Sentry issue triage reflection callable.

Queries the Sentry API for unresolved issues across all projects in the org,
classifies them (A–E), files GitHub issues for actionable ones, and updates
Sentry state for A/B/E (gated by SENTRY_TRIAGE_APPLY env var, default off).
Telegram delivery is delta-based and exception-only: a summary is sent ONLY
when a genuinely NEW Class C/D issue appears since the previous run, or an
auto-action fails. The Class C/D human-review pile is a STANDING backlog — in
dry-run nothing drains it, so re-announcing the same pile every day is pure
noise. The set of already-surfaced C/D short-ids is persisted between runs; a
static backlog stays silent (live status is always on the dashboard), and the
first run seeds that set silently rather than replaying the whole backlog.

Classification:
  A — Ignore: test/mock/harness noise        -> Sentry status=ignored (permanent)
  B — Low: known transients                  -> Sentry status=ignored + ignoreUntilEscalating
  C — Actionable: real bugs worth a GH issue -> GitHub issue filed
  D — Investigate: ambiguous, needs review   -> listed only
  E — Stale: no events in 30 days            -> Sentry status=resolved

Apply gate:
  The env var SENTRY_TRIAGE_APPLY (default "0" = dry-run) controls BOTH the
  GitHub-issue filing for tier C AND the Sentry state updates for tiers
  A/B/E atomically. Set SENTRY_TRIAGE_APPLY=1 to enable live writes.

All functions accept no arguments and return:
  {"status": "ok"|"error"|"disabled", "findings": [...], "summary": str}
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

from config.settings import settings
from reflections.utilities import PROJECT_ROOT, load_local_projects

logger = logging.getLogger("reflections.sentry_triage")

SENTRY_API_BASE = "https://yudame.sentry.io/api/0"

# Persisted set of Class C/D issue short-ids surfaced to Tom on the previous run.
# Delta-based notification reads this to stay silent on a static standing backlog
# and ping only when a genuinely NEW actionable/investigate issue appears.
_SEEN_STATE_PATH = PROJECT_ROOT / "data" / "sentry_triage_seen.json"


def _load_seen_ids() -> set[str] | None:
    """Return the Class C/D short-ids surfaced on the previous run.

    Returns None when no state file exists yet (first run since delta-based
    delivery shipped) so the caller can seed silently instead of re-announcing
    the entire standing backlog. A corrupt/unreadable file is treated the same
    as a missing one.
    """
    try:
        raw = _SEEN_STATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        logger.warning(f"sentry_triage: could not read seen-state: {e}")
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if isinstance(data, dict):
        data = data.get("ids", [])
    if not isinstance(data, list):
        return None
    return {str(x) for x in data}


def _save_seen_ids(ids: set[str]) -> None:
    """Persist the current Class C/D short-id set. Best-effort; never raises."""
    try:
        _SEEN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SEEN_STATE_PATH.write_text(json.dumps({"ids": sorted(ids)}, indent=0), encoding="utf-8")
    except OSError as e:
        logger.warning(f"sentry_triage: could not persist seen-state: {e}")


def _apply_enabled() -> bool:
    """Return True if SENTRY_TRIAGE_APPLY=1 is set (live mode).

    Default is False (dry-run). When True, both GitHub-issue filing for
    tier C AND Sentry state updates for tiers A/B/E are committed.
    """
    return os.environ.get("SENTRY_TRIAGE_APPLY", "0") == "1"


# Tier -> Sentry PUT payload map.
#
# IMPORTANT: For tier B we want the UI-default "archived until escalating"
# behavior, which requires explicit statusDetails. A naive {"status":
# "ignored"} payload defaults the substatus to "archived_forever" instead.
# See https://github.com/getsentry/sentry-mcp/issues/878 and the Sentry
# "Update an Issue" API docs.
_TIER_ACTION_MAP: dict[str, dict] = {
    "A": {"status": "ignored"},
    "B": {"status": "ignored", "statusDetails": {"ignoreUntilEscalating": True}},
    "E": {"status": "resolved"},
}

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
    "ANTHROPIC_API_KEY",  # API key not configured — always a test/env-misconfiguration event
    "No such file or directory: '/nonexistent",
    'No such file or directory: "/nonexistent',
    'No such file or directory: "/tmp/fake',
    'No such file or directory: "/private/tmp/foo',
    "[test-project]",
    "[test]",
    "[test] failed",
    "Test error",  # test sentinel error message
    "surprise!",  # test sentinel
    "unknown_backend",  # test sentinel for storage backend
    "db dead",  # test sentinel
    "DB on fire",  # test sentinel
    "worker down",  # test sentinel
    "noapikey",  # test podcast slug used in Stripe tests
    "audit action=mcp_tool_call",  # security audit log lines, not errors
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
    "connection already closed",  # transient DB/socket connection drop
    "TimeoutError",
    "timed out",
    "provider timeout",  # external LLM/API provider timeout
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
    "API server error",  # external provider 5xx (Grok, Perplexity, etc.)
    "server error (500)",  # external provider 5xx
    "auth",
    "401",
    "403",
    "404",
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
            resp = requests.get(
                url, headers=headers, params=params, timeout=settings.timeouts.http_request_s
            )
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
            timeout=settings.timeouts.git_subprocess_s,
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
            timeout=settings.timeouts.git_subprocess_s,
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


def _update_sentry_issue(issue_id: str, auth_token: str, payload: dict) -> tuple[bool, str | None]:
    """Update a Sentry issue's state via PUT /api/0/issues/{id}/.

    Args:
        issue_id: Sentry issue id (numeric or short id accepted by the API).
        auth_token: Sentry bearer token.
        payload: Body for the PUT call (see _TIER_ACTION_MAP). For tier B,
            this MUST include ``statusDetails.ignoreUntilEscalating: True``,
            otherwise Sentry defaults the substatus to ``archived_forever``
            rather than ``archived_until_escalating``.

    Returns:
        (True, None) on 2xx, (False, error_message) otherwise. Per-issue
        failures are isolated -- callers should loop and surface failures
        in the digest without aborting the run.
    """
    if not issue_id:
        return False, "missing issue id"

    url = f"{SENTRY_API_BASE}/issues/{issue_id}/"
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.put(
            url, headers=headers, json=payload, timeout=settings.timeouts.http_request_s
        )
    except requests.RequestException as e:
        logger.warning(f"sentry_triage: update PUT failed for {issue_id}: {e}")
        return False, str(e)

    if 200 <= resp.status_code < 300:
        return True, None

    body_snippet = (resp.text or "")[:200]
    logger.warning(
        f"sentry_triage: update PUT non-2xx for {issue_id}: HTTP {resp.status_code}: {body_snippet}"
    )
    return False, f"HTTP {resp.status_code}: {body_snippet}"


def _send_telegram_notification(message: str) -> None:
    """Best-effort Telegram notification. Swallows all subprocess failures."""
    try:
        subprocess.run(
            ["valor-telegram", "send", "--chat", "Eng: Valor", message],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.subprocess_default_s,
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
    apply_on = _apply_enabled()

    # Auto-action counters: per-tier (success, total_attempted)
    auto_action_results: dict[str, dict[str, int]] = {
        "A": {"ok": 0, "total": 0, "failed": 0},
        "B": {"ok": 0, "total": 0, "failed": 0},
        "E": {"ok": 0, "total": 0, "failed": 0},
    }
    auto_action_failures: list[str] = []  # human-readable failure detail for digest

    def _auto_action_tier(tier: str, label: str) -> None:
        """Apply Sentry state change for all issues in a tier (or dry-run record)."""
        if not classified[tier]:
            return
        payload = _TIER_ACTION_MAP[tier]
        findings.append(f"Class {tier} ({label}): {len(classified[tier])} issues")
        for issue, _cls, reason in classified[tier]:
            short_id = issue.get("shortId", "?")
            findings.append(f"  {short_id}: {reason}")
            auto_action_results[tier]["total"] += 1
            if not apply_on:
                findings.append(f"    [DRY RUN] would PUT {payload} for {short_id}")
                continue
            issue_id = issue.get("id") or issue.get("shortId") or ""
            ok, err = _update_sentry_issue(str(issue_id), auth_token, payload)
            if ok:
                auto_action_results[tier]["ok"] += 1
                findings.append(f"    Auto-actioned: {short_id} -> {payload['status']}")
            else:
                auto_action_results[tier]["failed"] += 1
                auto_action_failures.append(f"{short_id}: {err}")
                findings.append(f"    FAILED: {short_id}: {err}")

    # Tier A (noise) — Sentry status=ignored
    _auto_action_tier("A", "noise")

    # Tier B (transient) — Sentry status=ignored + ignoreUntilEscalating
    _auto_action_tier("B", "transient")

    # Tier C (actionable) — file GitHub issues
    if classified["C"]:
        findings.append(f"Class C (actionable): {len(classified['C'])} issues to fix")
        for issue, cls, reason in classified["C"]:
            short_id = issue.get("shortId", "?")
            title = issue.get("title", "?")[:80]
            proj = issue.get("project", {}).get("slug", "?")
            findings.append(f"  {short_id} [{proj}]: {title} ({reason})")

            if not apply_on:
                findings.append("    [DRY RUN] would file GitHub issue")
            else:
                # Determine project working directory for gh CLI
                proj_wd = None
                for project in load_local_projects():
                    if project.get("slug") == proj:
                        proj_wd = project.get("working_directory")
                        break

                if proj_wd is None and os.environ.get("COWORK_ROUTINE") == "1":
                    proj_wd = str(PROJECT_ROOT)
                    logger.info(
                        f"[COWORK] defaulting working directory to repo root for project {proj}"
                    )

                if proj_wd:
                    filed_url = _file_github_issue(issue, proj, Path(proj_wd), cls, reason)
                    if filed_url:
                        issues_filed += 1
                        findings.append(f"    Filed: {filed_url}")
                else:
                    findings.append(f"    [SKIP] no working directory for project {proj}")

    # Tier D (investigate) — list for review, no auto-action
    if classified["D"]:
        findings.append(f"Class D (investigate): {len(classified['D'])} issues needing review")
        for issue, cls, reason in classified["D"][:5]:
            findings.append(f"  {issue.get('shortId', '?')}: {reason}")

    # Tier E (stale) — Sentry status=resolved
    _auto_action_tier("E", "stale")

    elapsed = time.time() - t0

    # Aggregate auto-action counts for the summary
    a_r, b_r, e_r = auto_action_results["A"], auto_action_results["B"], auto_action_results["E"]
    auto_action_summary = (
        f"A={a_r['ok']}/{a_r['total']} B={b_r['ok']}/{b_r['total']} E={e_r['ok']}/{e_r['total']}"
    )
    total_failed = a_r["failed"] + b_r["failed"] + e_r["failed"]

    summary = (
        f"sentry-issue-triage: {len(issues)} issues across {len(by_project)} project(s) "
        f"(A={len(classified['A'])} B={len(classified['B'])} "
        f"C={len(classified['C'])} D={len(classified['D'])} E={len(classified['E'])})"
    )
    if apply_on:
        summary += f", auto-actioned: {auto_action_summary}"
        if total_failed:
            summary += f" ({total_failed} failed)"
    else:
        summary += " [DRY RUN]"
    if issues_filed:
        summary += f", {issues_filed} GitHub issues filed"

    logger.info(summary)

    # Delta-based exception delivery. The human-review pile (Class C actionable +
    # Class D investigate) is a STANDING backlog — in dry-run nothing drains it,
    # so re-announcing the same pile every day is pure noise (the "still getting
    # too many" #1561/#1582 follow-up). Notify only when a genuinely NEW C/D
    # short-id appears since the last run, or an auto-action actually failed.
    # Tiers A/B/E are handled without human input and never notify. On the very
    # first run (no state yet) seed silently so we don't replay the whole
    # backlog. Live status is always available on the dashboard.
    current_cd_ids = {
        str(issue.get("shortId") or issue.get("id"))
        for issue, _cls, _reason in (classified["C"] + classified["D"])
    }
    prev_seen = _load_seen_ids()
    new_cd_ids: set[str] = set() if prev_seen is None else (current_cd_ids - prev_seen)
    _save_seen_ids(current_cd_ids)

    needs_attention = bool(new_cd_ids) or total_failed > 0
    if not needs_attention:
        logger.info(
            "sentry_triage: no new actionable issues since last run "
            f"({len(current_cd_ids)} in standing backlog); suppressing notification"
        )
        return {
            "status": "ok",
            "findings": findings,
            "summary": summary,
            "duration": elapsed,
        }

    # Telegram summary (concise). Lead with the new-issue count so it's clear why
    # the otherwise-silent triage spoke up.
    new_suffix = f" ({len(new_cd_ids)} new)" if new_cd_ids else ""
    tg_lines = [f"Sentry triage: {len(issues)} issues{new_suffix}"]
    for cls_label, cls_key in [
        ("Noise", "A"),
        ("Transient", "B"),
        ("Actionable", "C"),
        ("Review", "D"),
        ("Stale", "E"),
    ]:
        count = len(classified[cls_key])
        if count:
            tg_lines.append(f"  {cls_label} ({cls_key}): {count}")

    # Auto-action block — distinct from human-review pile (C+D)
    if a_r["total"] or b_r["total"] or e_r["total"]:
        verb = "Auto-actioned" if apply_on else "Would auto-action"
        tg_lines.append(f"{verb}: {auto_action_summary}")
        if total_failed:
            tg_lines.append(f"  ({total_failed} failed)")
            for detail in auto_action_failures[:3]:
                tg_lines.append(f"  ! {detail}")

    if classified["C"]:
        for issue, _, reason in classified["C"][:3]:
            tg_lines.append(f"  -> {issue.get('shortId', '?')}: {issue.get('title', '')[:60]}")
    if not apply_on and classified["C"]:
        tg_lines.append("[dry run — no GitHub issues filed]")
    if not apply_on and (a_r["total"] or b_r["total"] or e_r["total"]):
        tg_lines.append("[dry run — no Sentry state changes]")
    if apply_on:
        tg_lines.append("[LIVE — Sentry state changes applied]")
    _send_telegram_notification("\n".join(tg_lines))

    return {
        "status": "ok",
        "findings": findings,
        "summary": summary,
        "duration": elapsed,
    }
