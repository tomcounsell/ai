"""Shared formatting utilities for Telegram message composition.

Extracted from bridge/summarizer.py to be reusable by both the summarizer
and the PM's send_telegram tool (tools/send_telegram.py).
"""

import re


def linkify_references(text: str, project_key: str | None = None) -> str:
    """Convert plain PR #N and Issue #N references to markdown links.

    Uses the project_key to look up the GitHub org/repo from the registered
    project config. If no project config is found or the text already contains
    markdown links for a reference, it is left unchanged.

    Args:
        text: The text potentially containing PR #N or Issue #N references.
        project_key: Project key for GitHub org/repo lookup. If None, text
            is returned unchanged.

    Returns:
        Text with plain references converted to markdown links.
    """
    if not text or not project_key or not str(project_key).strip():
        return text

    # Look up GitHub org/repo from registered project config
    try:
        from agent.agent_session_queue import get_project_config

        config = get_project_config(str(project_key))
        github_config = config.get("github", {})
        org = github_config.get("org")
        repo = github_config.get("repo")
    except Exception:
        return text

    if not org or not repo:
        return text

    base_url = f"https://github.com/{org}/{repo}"

    # Replace PR #N -> [PR #N](url/pull/N)
    # Negative lookbehind for [ ensures we don't double-link already-linked refs
    text = re.sub(
        r"(?<!\[)PR #(\d+)(?!\])",
        lambda m: f"[PR #{m.group(1)}]({base_url}/pull/{m.group(1)})",
        text,
    )

    # Replace Issue #N -> [Issue #N](url/issues/N)
    text = re.sub(
        r"(?<!\[)Issue #(\d+)(?!\])",
        lambda m: f"[Issue #{m.group(1)}]({base_url}/issues/{m.group(1)})",
        text,
    )

    return text


def linkify_references_from_session(text: str, session) -> str:
    """Convenience wrapper that extracts project_key from a session object.

    Args:
        text: The text to linkify.
        session: Object with a project_key attribute (e.g., AgentSession).

    Returns:
        Text with plain references converted to markdown links.
    """
    if not session:
        return text
    project_key = getattr(session, "project_key", None)
    return linkify_references(text, project_key)
