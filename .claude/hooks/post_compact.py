#!/usr/bin/env python3
"""Hook: PostCompact - Re-ground the agent after context compaction.

This hook fires after every context compaction event (auto or manual). It emits
a short, imperative nudge directing the agent to re-read the plan doc, check SDLC
stage progress, look at any PROGRESS.md scratchpad, and review the TodoWrite task
list. Exit code 0 causes the Claude CLI to surface the nudge as a user-visible
message at the start of the next turn.

Hook contract:
    Input (stdin): JSON with fields:
        hook_event_name: "PostCompact"
        session_id: claude session UUID (same as AgentSession.claude_session_uuid)
        trigger: "auto" | "manual"
        compact_summary: compacted summary text
        transcript_path: path to the JSONL transcript
        cwd: working directory of the Claude session

    Output (stdout, exit 0): nudge message text, or empty string if no output.
    Exit code: always 0 -- hook must never block the session.

Why CLI-only (not in build_hooks_config):
    The Claude SDK HookEvent type does not include PostCompact. Bridge sessions
    (SDK-based) rely on the existing defer_post_compact nudge guard in the output
    router and issue #1130 prompt instructions in builder.md. This hook is scoped
    to local interactive Claude Code CLI sessions only.

Bail-out guarantee:
    All exceptions are swallowed. On any error, the hook prints nothing and exits 0.
    This ensures the Claude CLI is never interrupted by hook failures.
"""

import logging
import os
import re
import sys
from pathlib import Path

# Standalone script -- sys.path mutation is safe (never imported as library)
# Add project root to path for model imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Add hook_utils to path
HOOKS_DIR = str(Path(__file__).resolve().parent)
sys.path.insert(0, HOOKS_DIR)

from hook_utils.constants import log_hook_error, read_hook_input  # noqa: E402

logger = logging.getLogger(__name__)


def _extract_issue_number(issue_url):
    """Extract trailing issue number from a GitHub issue URL.

    Args:
        issue_url: e.g. "https://github.com/org/repo/issues/1139"

    Returns:
        Integer issue number, or None if not parseable.
    """
    if not issue_url:
        return None
    match = re.search(r"/(\d+)/?$", issue_url.strip())
    if match:
        return int(match.group(1))
    return None


def _lookup_session(claude_session_uuid):
    """Look up an AgentSession by claude_session_uuid.

    Returns:
        Tuple of (plan_url, issue_url, stage_states_json).
        All three are None on any exception or if no row is found.
    """
    try:
        from models.agent_session import AgentSession

        rows = list(AgentSession.query.filter(claude_session_uuid=claude_session_uuid))
        if not rows:
            return None, None, None

        session = rows[0]
        plan_url = getattr(session, "plan_url", None) or None
        issue_url = getattr(session, "issue_url", None) or None

        # stage_states is a property returning a JSON string or None
        stage_states_json = None
        try:
            raw = session.stage_states
            if raw and isinstance(raw, str) and raw.strip() not in ("null", ""):
                stage_states_json = raw
        except Exception:
            pass

        return plan_url, issue_url, stage_states_json

    except Exception as exc:
        logger.warning("[post_compact] AgentSession lookup failed: %s", exc)
        return None, None, None


def _build_regrounding_nudge(plan_url, issue_url, stage_states_json, cwd):
    """Build the re-grounding nudge message.

    The nudge is directive, not descriptive. Each item is conditionally included
    only when its data is available. Token budget: < 300 tokens in all paths.

    Args:
        plan_url: URL to the plan document, or None.
        issue_url: GitHub issue URL (used to extract issue number), or None.
        stage_states_json: JSON string of SDLC stage states, or None.
        cwd: Working directory of the Claude session (may be empty string).

    Returns:
        The nudge string (non-empty). Always includes the header and TodoWrite item.
    """
    lines = ["Context was just compacted. Re-ground:"]
    item_num = 1

    if plan_url:
        lines.append(f"{item_num}. Re-read the plan: `{plan_url}`")
        item_num += 1

    if stage_states_json:
        issue_number = _extract_issue_number(issue_url)
        if issue_number:
            lines.append(
                f"{item_num}. Check SDLC stage progress: "
                f"`sdlc-tool stage-query --issue-number {issue_number}`"
            )
        else:
            lines.append(
                f"{item_num}. Check SDLC stage progress (stage_states are set on session)."
            )
        item_num += 1

    # Check for PROGRESS.md in cwd (guard against empty/missing cwd)
    effective_cwd = cwd or ""
    if effective_cwd and os.path.exists(os.path.join(effective_cwd, "PROGRESS.md")):
        lines.append(f"{item_num}. Re-read `PROGRESS.md` for working state.")
        item_num += 1

    lines.append(f"{item_num}. Re-read your current TodoWrite task list.")

    return "\n".join(lines)


def main():
    """Main hook entry point."""
    hook_input = read_hook_input()
    if not hook_input:
        return

    session_id = hook_input.get("session_id")
    if not session_id:
        # No session_id -- emit nothing (bare invocation with no context)
        return

    cwd = hook_input.get("cwd") or ""

    plan_url, issue_url, stage_states_json = _lookup_session(session_id)

    nudge = _build_regrounding_nudge(plan_url, issue_url, stage_states_json, cwd)
    if nudge:
        print(nudge)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_hook_error("post_compact", str(e))
