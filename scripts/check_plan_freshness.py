#!/usr/bin/env python3
"""Check whether a plan document has incorporated the latest issue comments.

Exits 0 when the plan is fresh (or when there is no tracking issue / no
comments); exits 1 when the plan is stale and should be revised via
``/do-plan`` before building.

This script replaces the inline shell block at
``.claude/skills/do-build/SKILL.md`` step 4 that previously used ``gh api``
with command substitution and pipes. Both of those patterns are blocked for
PM sessions by ``agent/hooks/pre_tool_use.py`` (see the PM Bash allowlist),
so the freshness check is delegated here where a single allowlisted
``python scripts/check_plan_freshness.py {PLAN_PATH}`` invocation replaces
the multi-step shell pipeline.

The script intentionally uses ``gh issue view --json comments`` rather than
``gh api`` because ``gh api`` is excluded from the PM allowlist as a silent
mutation vector.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

_TRACKING_RE = re.compile(r"^tracking:\s*(.+?)\s*$", re.MULTILINE)
_ISSUE_NUM_RE = re.compile(r"/issues/(\d+)")
_LAST_COMMENT_RE = re.compile(r"^last_comment_id:\s*(.*?)\s*$", re.MULTILINE)


def _read_frontmatter(plan_path: Path) -> tuple[str | None, str | None]:
    """Return (issue_number, last_comment_id) from the plan frontmatter."""
    text = plan_path.read_text(encoding="utf-8")

    issue_num: str | None = None
    m = _TRACKING_RE.search(text)
    if m:
        url_match = _ISSUE_NUM_RE.search(m.group(1))
        if url_match:
            issue_num = url_match.group(1)

    last_comment_id: str | None = None
    m = _LAST_COMMENT_RE.search(text)
    if m and m.group(1):
        last_comment_id = m.group(1)

    return issue_num, last_comment_id


def _latest_issue_comment_id(issue_number: str) -> str | None:
    """Return the ID of the most recent comment on *issue_number*.

    Returns ``None`` if the issue has no comments or if the ``gh`` call
    fails for any reason. A failed call is treated as a soft non-failure
    (freshness cannot be verified) to avoid turning a transient network
    glitch into a build-blocking stale-plan error.
    """
    try:
        result = subprocess.run(
            ["gh", "issue", "view", issue_number, "--json", "comments"],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None

    comments = data.get("comments") or []
    if not comments:
        return None

    last = comments[-1]
    # ``gh issue view`` returns comments with a ``url`` field; the comment
    # id is the trailing path segment. Some gh versions also return an
    # ``id`` field directly -- try it first.
    if isinstance(last, dict):
        if "id" in last and last["id"]:
            return str(last["id"])
        url = last.get("url") or ""
        if "#issuecomment-" in url:
            return url.rsplit("#issuecomment-", 1)[-1]
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <plan-path>", file=sys.stderr)
        return 2

    plan_path = Path(argv[1])
    if not plan_path.exists():
        print(f"Plan path not found: {plan_path}", file=sys.stderr)
        return 2

    issue_num, plan_comment_id = _read_frontmatter(plan_path)
    if not issue_num:
        print("No tracking issue in plan frontmatter -- skipping freshness check.")
        return 0

    latest = _latest_issue_comment_id(issue_num)
    if latest is None:
        print(
            f"Could not resolve latest comment id for issue #{issue_num} "
            "(network error or no comments) -- skipping freshness check."
        )
        return 0

    if not plan_comment_id:
        print(
            f"STALE PLAN: issue #{issue_num} has comments "
            f"(latest: {latest}) but plan has no last_comment_id. "
            "Run /do-plan to incorporate the latest feedback before building."
        )
        return 1

    if latest != plan_comment_id:
        print(
            f"STALE PLAN: issue #{issue_num} has new comments "
            f"(latest: {latest}, plan has: {plan_comment_id}). "
            "Run /do-plan to incorporate the latest feedback before building."
        )
        return 1

    print(f"Plan is fresh against issue #{issue_num} (comment id {plan_comment_id}).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
