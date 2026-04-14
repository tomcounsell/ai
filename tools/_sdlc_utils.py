"""Shared utilities for SDLC session lookup.

Extracted from tools/sdlc_stage_query.py to avoid duplicating session-lookup
logic across sdlc_stage_marker, sdlc_stage_query, and sdlc_session_ensure.

This module only imports models.agent_session — no circular import risk.
"""

from __future__ import annotations

import logging

from models.agent_session import AgentSession

logger = logging.getLogger(__name__)


def find_session_by_issue(issue_number: int):
    """Find a PM session tracking the given issue number.

    Scans recent PM sessions for an issue_url containing the issue number.
    Returns the session object or None.

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
        return None
    except Exception as e:
        logger.debug(f"find_session_by_issue failed: {e}")
        return None
