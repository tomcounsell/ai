"""Shared utilities for SDLC session and plan lookups.

Extracted from tools/sdlc_stage_query.py, sdlc_verdict.py, and sdlc_dispatch.py
to avoid duplicating session-lookup and plan-path logic across SDLC tool modules.

This module only imports models.agent_session — no circular import risk.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

from models.agent_session import AgentSession

logger = logging.getLogger(__name__)


def _git_toplevel(cwd: Path | None = None) -> Path | None:
    """Return the git working-tree root for ``cwd`` (default: process cwd).

    Returns None when ``git`` is missing, the directory is not a git repo, or
    the call times out. Callers fall through to the next resolution step.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd or Path.cwd()),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug(f"_git_toplevel failed: {e}")
        return None
    if result.returncode != 0:
        return None
    top = result.stdout.strip()
    return Path(top) if top else None


def find_session_by_issue(issue_number: int):
    """Find a PM session tracking the given issue number.

    Two-pass match over PM sessions:

    1. Primary pass: ``issue_url`` endswith ``/issues/{issue_number}``.
    2. Fallback pass: ``message_text`` matches the case-insensitive regex
       ``\\bissue\\s*#?\\s*{issue_number}\\b``. This catches Telegram-
       originated PM sessions that have no ``issue_url`` (the bridge builds
       sessions from message text, not URLs) so operators running SDLC over
       the bridge are still findable by issue number.

    The ``issue_url`` pass takes priority: if any session matches there, it
    is returned without running the ``message_text`` scan. When multiple
    sessions could match via ``message_text`` alone (e.g., a conversation
    mentioning two issue numbers), the first iterated session wins — this is
    an acceptable limitation because bridge sessions today carry a single
    originating message and multi-issue mentions are rare.

    Args:
        issue_number: GitHub issue number to search for.

    Returns:
        AgentSession or None.
    """
    if not issue_number or issue_number < 1:
        return None

    try:
        # NOTE: Linear scan of PM sessions — acceptable for current scale (typically
        # <100 PM sessions). If PM session count grows significantly, consider adding
        # an indexed lookup by issue_url or caching issue->session mappings.
        pm_sessions = list(AgentSession.query.filter(session_type="pm"))
        target_suffix = f"/issues/{issue_number}"
        for s in pm_sessions:
            issue_url = getattr(s, "issue_url", None) or ""
            if issue_url.endswith(target_suffix):
                return s

        # Fallback: match by message_text for bridge-originated sessions that
        # have no issue_url. Word boundaries prevent matches like
        # "tissue 1147" — only "issue 1147", "issue #1147", "SDLC issue 1147".
        pattern = re.compile(rf"\bissue\s*#?\s*{issue_number}\b", re.IGNORECASE)
        for s in pm_sessions:
            message_text = getattr(s, "message_text", None) or ""
            if message_text and pattern.search(message_text):
                return s

        return None
    except Exception as e:
        logger.debug(f"find_session_by_issue failed: {e}")
        return None


def find_session(session_id: str | None = None, issue_number: int | None = None):
    """Resolve a PM AgentSession by session_id or issue_number.

    Checks (in order): explicit session_id arg → VALOR_SESSION_ID env →
    AGENT_SESSION_ID env → issue_number lookup via find_session_by_issue.
    Returns the session object or None.
    """
    resolved_id = (
        session_id or os.environ.get("VALOR_SESSION_ID") or os.environ.get("AGENT_SESSION_ID")
    )
    if resolved_id:
        try:
            sessions = list(AgentSession.query.filter(session_id=resolved_id))
            if sessions:
                for s in sessions:
                    if getattr(s, "session_type", None) == "pm":
                        return s
                return sessions[0]
        except Exception as e:
            logger.debug(f"find_session by id failed: {e}")

    if issue_number is not None:
        try:
            return find_session_by_issue(issue_number)
        except Exception as e:
            logger.debug(f"find_session_by_issue failed: {e}")

    return None


def find_plan_path(issue_number: int) -> Path | None:
    """Locate the plan file tracking this issue.

    Walks ``docs/plans/`` and returns the first ``.md`` file referencing the
    issue, matching either the bare ``#{issue_number}`` or the tracking-URL
    forms (``issues/{issue_number}``). A trailing digit boundary prevents
    ``#1455`` from matching issue ``145``. Returns None if not found.

    Plans-directory resolution order (D1 — portability):

    1. ``SDLC_TARGET_REPO`` env var (explicit override wins — preserves
       backward-compatible cross-repo override semantics).
    2. Else the cwd's git working-tree root (``git rev-parse --show-toplevel``)
       so the pipeline finds plans in whatever repo it is invoked from.
    3. Else the ``__file__``-relative ``~/src/ai/docs/plans`` fallback.

    Each step falls through on failure (not a git repo, ``git`` missing) so a
    missing env var degrades to "correct" rather than "silently wrong".
    """
    if not issue_number:
        return None

    repo_root_env = os.environ.get("SDLC_TARGET_REPO")
    if repo_root_env:
        plans_dir = Path(repo_root_env) / "docs" / "plans"
    else:
        toplevel = _git_toplevel()
        if toplevel is not None:
            plans_dir = toplevel / "docs" / "plans"
        else:
            plans_dir = Path(__file__).resolve().parent.parent / "docs" / "plans"

    if not plans_dir.is_dir():
        return None

    # Match `#145`, `issues/145`, and the full tracking URL, but NOT `#1455`
    # (the trailing non-digit lookahead enforces the boundary).
    ref_re = re.compile(rf"(?:#|issues/){issue_number}(?![0-9])")
    try:
        for entry in plans_dir.iterdir():
            if not entry.is_file() or entry.suffix != ".md":
                continue
            try:
                text = entry.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if ref_re.search(text):
                return entry
    except Exception as e:
        logger.debug(f"find_plan_path walk failed: {e}")
    return None
