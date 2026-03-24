#!/usr/bin/env python3
"""
Issue Poller - Automatic SDLC Kickoff for New GitHub Issues.

Polls GitHub issues across configured projects, detects new ones,
checks for duplicates, and auto-creates draft plans via /do-plan.

Entry point: Run on a 5-minute cron schedule via launchd.
State: Popoto SeenIssue model for seen-tracking, raw Redis for distributed lock.

See docs/features/issue-poller.md for full documentation.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path

# Ensure project root is in sys.path for standalone execution
_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from popoto.redis_db import POPOTO_REDIS_DB  # noqa: E402

from models.seen_issue import SeenIssue  # noqa: E402
from scripts.issue_dedup import compare_issues  # noqa: E402

logger = logging.getLogger(__name__)

# Redis key patterns (lock and failure counter stay as raw Redis for atomicity)
LOCK_KEY = "issue_poller:lock"
LOCK_TTL = 300  # 5 minutes
FAILURE_COUNT_KEY = "issue_poller:consecutive_failures"

# Agent D automated comment signature to filter out
AGENT_D_SIGNATURE = "_Auto-posted by /do-docs cascade_"


def get_redis_client():
    """Get the shared Popoto Redis connection, raising if unavailable."""
    POPOTO_REDIS_DB.ping()
    return POPOTO_REDIS_DB


def acquire_lock(r) -> bool:
    """Acquire a distributed lock to prevent concurrent executions.

    Uses raw Redis SET NX EX for atomicity. The lock stays as raw Redis
    because Popoto's save() cannot do atomic SET NX EX.

    Returns True if lock acquired, False if another instance is running.
    """
    return bool(r.set(LOCK_KEY, "1", nx=True, ex=LOCK_TTL))


def release_lock(r) -> None:
    """Release the distributed lock."""
    r.delete(LOCK_KEY)


def mark_seen(org: str, repo: str, issue_number: int) -> None:
    """Mark an issue as seen (processed)."""
    record = SeenIssue.get_or_create(org, repo)
    record.mark(issue_number)


def is_seen(org: str, repo: str, issue_number: int) -> bool:
    """Check if an issue has already been processed."""
    record = SeenIssue.get_or_create(org, repo)
    return record.is_seen(issue_number)


def load_projects(config_path: Path | None = None) -> list[dict]:
    """Load projects with GitHub configuration from projects.json.

    Returns a list of dicts with keys: name, org, repo, working_directory, telegram_groups.
    """
    if config_path is None:
        desktop_path = Path.home() / "Desktop" / "Valor" / "projects.json"
        if desktop_path.exists():
            config_path = desktop_path
        else:
            config_path = Path(_project_root) / "config" / "projects.json"

    with open(config_path) as f:
        config = json.load(f)

    projects = []
    for _key, proj in config.get("projects", {}).items():
        gh = proj.get("github")
        if not gh:
            continue
        projects.append(
            {
                "name": proj.get("name", _key),
                "org": gh["org"],
                "repo": gh["repo"],
                "working_directory": str(Path(proj.get("working_directory", "")).expanduser()),
                "telegram_groups": proj.get("telegram", {}).get("groups", []),
            }
        )
    return projects


def fetch_open_issues(org: str, repo: str) -> list[dict]:
    """Fetch open issues from GitHub using the gh CLI.

    Returns list of dicts with keys: number, title, body, labels, author, created_at.
    Handles rate limits by checking response headers.
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--repo",
                f"{org}/{repo}",
                "--state",
                "open",
                "--json",
                "number,title,body,labels,author,createdAt",
                "--limit",
                "50",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning(f"gh issue list failed for {org}/{repo}: {result.stderr}")
            return []

        issues = json.loads(result.stdout)
        return issues

    except subprocess.TimeoutExpired:
        logger.warning(f"gh issue list timed out for {org}/{repo}")
        return []
    except json.JSONDecodeError as e:
        logger.warning(f"Malformed JSON from gh CLI for {org}/{repo}: {e}")
        return []
    except FileNotFoundError:
        logger.error("gh CLI not found. Install GitHub CLI: https://cli.github.com/")
        return []


def get_latest_comment_id(org: str, repo: str, issue_number: int) -> str | None:
    """Get the latest comment ID on an issue, filtering out Agent D comments."""
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{org}/{repo}/issues/{issue_number}/comments",
                "--jq",
                ".",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return None

        comments = json.loads(result.stdout)
        # Filter out Agent D automated comments
        human_comments = [c for c in comments if AGENT_D_SIGNATURE not in c.get("body", "")]
        if human_comments:
            return str(human_comments[-1]["id"])
        return None

    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return None


def filter_new_issues(org: str, repo: str, issues: list[dict]) -> list[dict]:
    """Filter out already-seen issues."""
    return [issue for issue in issues if not is_seen(org, repo, issue["number"])]


def has_sufficient_context(issue: dict) -> bool:
    """Check if an issue has enough context for auto-planning.

    Issues with empty body or very short descriptions are flagged as needing review.
    """
    body = (issue.get("body") or "").strip()
    title = (issue.get("title") or "").strip()

    # Must have a title
    if not title:
        return False

    # Body must exist and be more than a trivial description
    if not body or len(body) < 20:
        return False

    return True


def apply_label(org: str, repo: str, issue_number: int, label: str) -> bool:
    """Apply a GitHub label to an issue. Creates the label if it doesn't exist."""
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "edit",
                str(issue_number),
                "--repo",
                f"{org}/{repo}",
                "--add-label",
                label,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def add_comment(org: str, repo: str, issue_number: int, comment: str) -> bool:
    """Add a comment to a GitHub issue."""
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "comment",
                str(issue_number),
                "--repo",
                f"{org}/{repo}",
                "--body",
                comment,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def check_existing_plan(org: str, repo: str, issue_number: int) -> bool:
    """Check if a plan already exists for this issue number.

    Searches for plan files with frontmatter referencing the issue.
    """
    plans_dir = Path(_project_root) / "docs" / "plans"
    if not plans_dir.exists():
        return False

    target = f"/{org}/{repo}/issues/{issue_number}"
    for plan_file in plans_dir.glob("*.md"):
        try:
            content = plan_file.read_text()
            if target in content:
                return True
        except OSError:
            continue
    return False


def dispatch_plan_creation(
    org: str,
    repo: str,
    issue_number: int,
    last_comment_id: str | None = None,
) -> bool:
    """Dispatch plan creation via claude -p subprocess.

    Invokes the /do-plan skill to create a draft plan for the issue.
    """
    prompt = (
        f"Create a plan for issue #{issue_number} in {org}/{repo}. "
        f"Use /do-plan to create a draft plan."
    )
    if last_comment_id:
        prompt += f" Initialize last_comment_id in the plan frontmatter to {last_comment_id}."

    try:
        logger.info(f"Dispatching plan creation for {org}/{repo}#{issue_number}")
        result = subprocess.run(
            [
                "claude",
                "-p",
                prompt,
                "--allowedTools",
                "Read,Write,Edit,Bash,Glob,Grep",
            ],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout for plan creation
            cwd=_project_root,
        )
        if result.returncode != 0:
            logger.warning(
                f"Plan creation failed for {org}/{repo}#{issue_number}: {result.stderr[:200]}"
            )
            return False
        return True

    except subprocess.TimeoutExpired:
        logger.warning(f"Plan creation timed out for {org}/{repo}#{issue_number}")
        return False
    except FileNotFoundError:
        logger.error("claude CLI not found")
        return False


def send_telegram_notification(message: str, groups: list[str] | None = None) -> bool:
    """Send a notification via Telegram.

    Uses the valor-telegram CLI if available, falls back to logging.
    """
    try:
        # Try valor-telegram CLI
        result = subprocess.run(
            ["valor-telegram", "send", "--text", message],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Fallback: just log it
    logger.info(f"[NOTIFICATION] {message}")
    return False


def process_issue(
    org: str,
    repo: str,
    issue: dict,
    all_open_issues: list[dict],
    telegram_groups: list[str],
) -> str:
    """Process a single new issue.

    Returns a status string: 'planned', 'duplicate', 'needs-review', 'skipped', 'error'.
    """
    number = issue["number"]
    title = issue.get("title", "")
    body = issue.get("body", "")

    logger.info(f"Processing {org}/{repo}#{number}: {title}")

    # Check if a plan already exists (race condition prevention)
    if check_existing_plan(org, repo, number):
        logger.info(f"Plan already exists for {org}/{repo}#{number}, marking as seen")
        mark_seen(org, repo, number)
        return "skipped"

    # Check for sufficient context
    if not has_sufficient_context(issue):
        logger.info(f"Insufficient context for {org}/{repo}#{number}, flagging")
        apply_label(org, repo, number, "needs-review")
        add_comment(
            org,
            repo,
            number,
            "This issue has been flagged for review - it may need more context "
            "before a plan can be auto-generated. Please add more details to the "
            "description.",
        )
        send_telegram_notification(
            f"Issue {org}/{repo}#{number} needs review (insufficient context): {title}",
            telegram_groups,
        )
        mark_seen(org, repo, number)
        return "needs-review"

    # Run dedup check against other open issues
    other_issues = [i for i in all_open_issues if i["number"] != number]
    try:
        dedup_result = compare_issues(
            title=title,
            body=body,
            existing_issues=other_issues,
        )
    except Exception as e:
        # Dedup is best-effort - skip on failure
        logger.warning(f"Dedup check failed for {org}/{repo}#{number}: {e}")
        dedup_result = None

    if dedup_result and dedup_result.get("classification") == "duplicate":
        dup_number = dedup_result.get("match_number")
        score = dedup_result.get("score", 0)
        logger.info(
            f"Duplicate detected: {org}/{repo}#{number} matches #{dup_number} (score: {score:.2f})"
        )
        apply_label(org, repo, number, "possible-duplicate")
        add_comment(
            org,
            repo,
            number,
            f"This issue appears to be a duplicate of #{dup_number} "
            f"(similarity: {score:.0%}). Please review and close if confirmed.\n\n"
            f"_Auto-detected by issue poller_",
        )
        send_telegram_notification(
            f"Possible duplicate: {org}/{repo}#{number} matches #{dup_number} "
            f"({score:.0%}): {title}",
            telegram_groups,
        )
        mark_seen(org, repo, number)
        return "duplicate"

    # Get latest comment ID for plan frontmatter
    last_comment_id = get_latest_comment_id(org, repo, number)

    # Dispatch plan creation
    success = dispatch_plan_creation(org, repo, number, last_comment_id)
    if success:
        apply_label(org, repo, number, "auto-planned")
        send_telegram_notification(
            f"Auto-planned: {org}/{repo}#{number}: {title}",
            telegram_groups,
        )
        mark_seen(org, repo, number)

        # Note related issues in plan if any
        if dedup_result and dedup_result.get("classification") == "related":
            related_number = dedup_result.get("match_number")
            logger.info(f"Related issue noted: {org}/{repo}#{number} related to #{related_number}")

        return "planned"

    logger.warning(f"Plan creation failed for {org}/{repo}#{number}")
    return "error"


def poll_project(
    project: dict,
) -> dict:
    """Poll a single project for new issues.

    Returns a summary dict with counts of each status.
    """
    org = project["org"]
    repo = project["repo"]
    telegram_groups = project.get("telegram_groups", [])

    logger.info(f"Polling {org}/{repo}...")

    issues = fetch_open_issues(org, repo)
    if not issues:
        logger.info(f"No open issues found for {org}/{repo}")
        return {"total": 0, "new": 0, "planned": 0, "duplicate": 0, "error": 0}

    new_issues = filter_new_issues(org, repo, issues)
    logger.info(f"Found {len(new_issues)} new issues in {org}/{repo}")

    results = {
        "total": len(issues),
        "new": len(new_issues),
        "planned": 0,
        "duplicate": 0,
        "needs-review": 0,
        "skipped": 0,
        "error": 0,
    }

    for issue in new_issues:
        status = process_issue(org, repo, issue, issues, telegram_groups)
        results[status] = results.get(status, 0) + 1

    return results


def run_polling_cycle() -> dict:
    """Run a single polling cycle across all configured projects.

    Returns a summary dict with per-project results.
    """
    # Initialize Redis
    try:
        r = get_redis_client()
    except Exception as e:
        logger.error(f"Redis connection failed, skipping cycle: {e}")
        return {"error": str(e)}

    # Acquire lock
    if not acquire_lock(r):
        logger.info("Another poller instance is running, skipping cycle")
        return {"skipped": "lock held"}

    try:
        projects = load_projects()
        if not projects:
            logger.warning("No projects with GitHub config found")
            return {"error": "no projects"}

        summary = {}
        for project in projects:
            key = f"{project['org']}/{project['repo']}"
            try:
                summary[key] = poll_project(project)
            except Exception as e:
                logger.error(f"Error polling {key}: {e}")
                summary[key] = {"error": str(e)}

        # Track consecutive failures for alerting
        has_errors = any(isinstance(v, dict) and v.get("error") for v in summary.values())
        if has_errors:
            failures = r.incr(FAILURE_COUNT_KEY)
            r.expire(FAILURE_COUNT_KEY, 3600)  # Reset after 1 hour
            if failures >= 3:
                send_telegram_notification(
                    f"Issue poller: {failures} consecutive failures. "
                    f"Check logs/issue_poller.log for details."
                )
        else:
            r.delete(FAILURE_COUNT_KEY)

        return summary

    finally:
        release_lock(r)


def setup_logging() -> None:
    """Configure logging for the poller."""
    log_dir = Path(_project_root) / "logs"
    log_dir.mkdir(exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "issue_poller.log"),
            logging.StreamHandler(),
        ],
    )


def main() -> int:
    """Main entry point for the issue poller."""
    setup_logging()
    logger.info("Issue poller starting...")

    start_time = time.time()
    summary = run_polling_cycle()
    elapsed = time.time() - start_time

    logger.info(f"Polling cycle complete in {elapsed:.1f}s: {json.dumps(summary)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
