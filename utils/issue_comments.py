"""Utility for reading and writing SDLC stage comments on GitHub issues.

Uses the `gh` CLI for all GitHub API interactions -- no new Python dependencies.
Both functions handle errors gracefully (returning empty list / False, never raising).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)

# Marker used to identify stage comments posted by this module
STAGE_COMMENT_MARKER = "<!-- sdlc-stage-comment -->"
STAGE_HEADER_RE = re.compile(r"## Stage: (\w+)\s*\n\*\*Outcome:\*\*\s*(.+)")


def _resolve_gh_repo() -> str | None:
    """Resolve the GitHub repo from GH_REPO or SDLC_REPO env vars."""
    return os.environ.get("GH_REPO") or os.environ.get("SDLC_REPO")


def fetch_stage_comments(issue_number: int, repo: str | None = None) -> list[dict]:
    """Fetch prior SDLC stage comments from a GitHub issue.

    Args:
        issue_number: The GitHub issue number.
        repo: Optional owner/repo string. Falls back to GH_REPO env var.

    Returns:
        List of dicts with keys: stage, outcome, body. Empty list on failure.
    """
    if not issue_number:
        return []

    repo = repo or _resolve_gh_repo()
    if not repo:
        logger.warning("[issue_comments] No repo configured, cannot fetch comments")
        return []

    try:
        cmd = [
            "gh",
            "api",
            f"repos/{repo}/issues/{issue_number}/comments",
            "--jq",
            ".[].body",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            logger.warning(
                f"[issue_comments] gh api failed (rc={result.returncode}): {result.stderr[:200]}"
            )
            return []

        comments = []
        # gh --jq '.[].body' outputs each body separated by newlines,
        # but bodies themselves contain newlines. Split on the marker instead.
        raw = result.stdout
        if not raw.strip():
            return []

        # Re-fetch with JSON output for reliable parsing
        cmd_json = [
            "gh",
            "api",
            f"repos/{repo}/issues/{issue_number}/comments",
        ]
        result_json = subprocess.run(cmd_json, capture_output=True, text=True, timeout=10)
        if result_json.returncode != 0:
            return []

        try:
            all_comments = json.loads(result_json.stdout)
        except json.JSONDecodeError:
            logger.warning("[issue_comments] Failed to parse JSON response")
            return []

        for comment in all_comments:
            body = comment.get("body", "")
            if STAGE_COMMENT_MARKER not in body:
                continue

            match = STAGE_HEADER_RE.search(body)
            if match:
                comments.append(
                    {
                        "stage": match.group(1),
                        "outcome": match.group(2).strip(),
                        "body": body,
                    }
                )

        return comments

    except subprocess.TimeoutExpired:
        logger.warning("[issue_comments] gh api timed out fetching comments")
        return []
    except Exception as e:
        logger.warning(f"[issue_comments] Failed to fetch comments: {e}")
        return []


def format_stage_comment(
    stage: str,
    outcome: str,
    findings: list[str] | None = None,
    files: list[str] | None = None,
    notes: str | None = None,
) -> str:
    """Format a structured SDLC stage comment body.

    Args:
        stage: Stage name (e.g., BUILD, TEST, REVIEW).
        outcome: Brief outcome description.
        findings: List of key findings/discoveries.
        files: List of files modified.
        notes: Notes for the next stage.

    Returns:
        Formatted markdown comment body.
    """
    lines = [
        STAGE_COMMENT_MARKER,
        f"## Stage: {stage}",
        f"**Outcome:** {outcome}",
        "",
    ]

    if findings:
        lines.append("### Key Findings")
        for finding in findings:
            lines.append(f"- {finding}")
        lines.append("")
    else:
        lines.append("### Key Findings")
        lines.append("- No notable findings")
        lines.append("")

    if files:
        lines.append("### Files Modified")
        for f in files:
            lines.append(f"- `{f}`")
        lines.append("")

    if notes:
        lines.append("### Notes for Next Stage")
        lines.append(notes)
        lines.append("")

    return "\n".join(lines)


def post_stage_comment(
    issue_number: int,
    stage: str,
    outcome: str,
    findings: list[str] | None = None,
    files: list[str] | None = None,
    notes: str | None = None,
    repo: str | None = None,
) -> bool:
    """Post a structured stage summary comment to a GitHub issue.

    Args:
        issue_number: The GitHub issue number.
        stage: Stage name (e.g., BUILD, TEST, REVIEW).
        outcome: Brief outcome description.
        findings: List of key findings/discoveries.
        files: List of files modified.
        notes: Notes for the next stage.
        repo: Optional owner/repo string. Falls back to GH_REPO env var.

    Returns:
        True on success, False on failure. Never raises.
    """
    if not issue_number:
        return False

    repo = repo or _resolve_gh_repo()
    if not repo:
        logger.warning("[issue_comments] No repo configured, cannot post comment")
        return False

    body = format_stage_comment(stage, outcome, findings, files, notes)

    try:
        cmd = [
            "gh",
            "issue",
            "comment",
            str(issue_number),
            "--repo",
            repo,
            "--body",
            body,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            logger.warning(
                f"[issue_comments] gh issue comment failed (rc={result.returncode}): "
                f"{result.stderr[:200]}"
            )
            return False

        logger.info(
            f"[issue_comments] Posted stage comment: {stage} -> {outcome} on issue #{issue_number}"
        )
        return True

    except subprocess.TimeoutExpired:
        logger.warning("[issue_comments] gh issue comment timed out")
        return False
    except Exception as e:
        logger.warning(f"[issue_comments] Failed to post comment: {e}")
        return False


def format_prior_context(comments: list[dict], max_comments: int = 5) -> str:
    """Format prior stage comments into a context string for prompt injection.

    Args:
        comments: List of stage comment dicts from fetch_stage_comments().
        max_comments: Maximum number of recent comments to include.

    Returns:
        Formatted context string, or empty string if no comments.
    """
    if not comments:
        return ""

    recent = comments[-max_comments:]
    lines = ["## Prior Stage Findings", ""]
    for c in recent:
        lines.append(f"**{c['stage']}**: {c['outcome']}")
    lines.append("")
    return "\n".join(lines)
