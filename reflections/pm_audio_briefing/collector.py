"""
reflections/pm_audio_briefing/collector.py — Raw-signal collector.

Pulls per-project signals (yesterday's merges, open bugs, upvote queue) using
the project's working_directory and github.org/repo. v1 ships exactly 3
categories: merges, open-bugs, upvote-queue. Unknown categories in
angles.include are logged-warned and skipped (not raised).

The collector is pure-Python: subprocess is used for git/gh only. No Anthropic
calls, no Redis, no Telegram. Output is a dict[category, list[item]] consumed
by builder.build().
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

logger = logging.getLogger("reflections.pm_audio_briefing.collector")


def _run(cmd: list[str], cwd: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr).

    Never raises -- timeout/FileNotFoundError are caught and surfaced as a
    non-zero return code with a synthetic stderr.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except FileNotFoundError as e:
        return 127, "", f"command not found: {e}"
    except Exception as e:
        return 1, "", f"unexpected error: {e}"


# --- Per-category collectors -------------------------------------------------


def _collect_merges(project: dict) -> list[dict]:
    """Collect merge commits from yesterday in the project's working directory.

    Returns a list of {sha, subject, pr_number} dicts. PR number is parsed
    from the subject line if present (e.g. "Merge pull request #123" or
    "(#123)" suffix).
    """
    cwd = project.get("working_directory", ".")
    rc, out, err = _run(
        [
            "git",
            "log",
            "--merges",
            "--since=yesterday",
            "--until=midnight",
            "--pretty=format:%H|%s",
        ],
        cwd=cwd,
    )
    if rc != 0:
        logger.warning("git log failed for %s: %s", cwd, err.strip()[:200])
        return []

    items: list[dict] = []
    for line in out.splitlines():
        if "|" not in line:
            continue
        sha, _, subject = line.partition("|")
        # Try to parse PR number from common merge subject formats
        pr_number: int | None = None
        for token in subject.split():
            t = token.strip("()#").lstrip("#")
            if t.isdigit() and 1 <= len(t) <= 6:
                pr_number = int(t)
                break
        items.append(
            {
                "sha": sha.strip()[:12],
                "subject": subject.strip(),
                "pr_number": pr_number,
            }
        )
    return items


def _gh_issue_list(repo: str, labels: list[str], cwd: str, limit: int = 20) -> list[dict]:
    """Run `gh issue list` for the given repo and labels; return parsed items.

    Returns a list of dicts with keys: number, title, url, labels.
    Empty list on any failure (logged at WARNING).
    """
    label_args: list[str] = []
    for label in labels:
        label_args.extend(["--label", label])
    cmd = [
        "gh",
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--json",
        "number,title,url,labels",
        "--limit",
        str(limit),
        *label_args,
    ]
    rc, out, err = _run(cmd, cwd=cwd)
    if rc != 0:
        logger.warning(
            "gh issue list failed for %s (labels=%s): %s",
            repo,
            ",".join(labels) or "(none)",
            err.strip()[:200],
        )
        return []
    try:
        parsed = json.loads(out or "[]")
        if not isinstance(parsed, list):
            return []
        return parsed
    except json.JSONDecodeError as e:
        logger.warning("gh issue list returned non-JSON for %s: %s", repo, e)
        return []


def _project_repo(project: dict) -> str | None:
    """Resolve the GitHub `org/repo` slug from the project dict.

    Returns None if the project has no `github.org` + `github.repo` pair.
    """
    gh = project.get("github") or {}
    org = (gh.get("org") or "").strip()
    repo = (gh.get("repo") or "").strip()
    if not org or not repo:
        return None
    return f"{org}/{repo}"


def _collect_open_bugs(project: dict) -> list[dict]:
    """Collect open issues labeled `bug` in the project's GitHub repo."""
    repo = _project_repo(project)
    if not repo:
        return []
    cwd = project.get("working_directory", ".")
    return _gh_issue_list(repo, ["bug"], cwd=cwd)


def _collect_upvote_queue(project: dict) -> list[dict]:
    """Collect open issues labeled `upvote` (the "queued for today" lane)."""
    repo = _project_repo(project)
    if not repo:
        return []
    cwd = project.get("working_directory", ".")
    return _gh_issue_list(repo, ["upvote"], cwd=cwd)


# --- Public API --------------------------------------------------------------


_COLLECTORS = {
    "merges": _collect_merges,
    "open-bugs": _collect_open_bugs,
    "upvote-queue": _collect_upvote_queue,
}


def collect(
    project: dict,
    angles_include: list[str] | None = None,
    angles_exclude: list[str] | None = None,
) -> dict[str, list[dict]]:
    """Pull raw signals for a project, filtered by include/exclude angles.

    Args:
        project: Project dict from load_local_projects() (includes
            working_directory, github.{org,repo}, etc.).
        angles_include: Categories to collect. Unknown categories are
            log-warned and skipped (not raised). Empty/None means "no
            categories".
        angles_exclude: Optional substrings to filter OUT after collection.
            Each item is matched as a case-insensitive substring against
            either the merge subject or the issue title. Used to suppress
            noise like "lockfile-bumps" or "plan-only-commits".

    Returns:
        Dict[category, list[item]]. Categories present in angles_include but
        with no results map to an empty list.
    """
    include = list(angles_include or [])
    exclude = list(angles_exclude or [])

    out: dict[str, list[dict]] = {}
    for cat in include:
        fn = _COLLECTORS.get(cat)
        if fn is None:
            logger.warning(
                "Unknown angle category %r (known: %s); skipping",
                cat,
                ", ".join(sorted(_COLLECTORS.keys())),
            )
            continue
        try:
            items = fn(project) or []
        except Exception as e:
            # Defensive: any unexpected collector error is logged and the
            # category is recorded as empty so the briefing can continue.
            logger.warning("Collector %r raised: %s; treating as empty", cat, e)
            items = []
        out[cat] = items

    if exclude and out:
        out = _apply_exclude_filter(out, exclude)

    return out


def _apply_exclude_filter(
    collected: dict[str, list[dict]], exclude: list[str]
) -> dict[str, list[dict]]:
    """Filter items whose subject/title contains any exclude substring.

    Case-insensitive substring match against `subject` (merges) or `title`
    (issues). Non-matching items pass through unchanged.
    """
    needles = [e.lower() for e in exclude if e and e.strip()]
    if not needles:
        return collected

    filtered: dict[str, list[dict]] = {}
    for cat, items in collected.items():
        kept: list[dict] = []
        for item in items:
            haystack = (str(item.get("subject", "")) + " " + str(item.get("title", ""))).lower()
            if any(n in haystack for n in needles):
                continue
            kept.append(item)
        filtered[cat] = kept
    return filtered


def is_empty(collected: dict[str, list[Any]]) -> bool:
    """Return True if all categories in `collected` are empty."""
    return all(not v for v in collected.values())
