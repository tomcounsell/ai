"""Session tagging system for SessionLog categorization.

Provides CRUD operations for session tags and rule-based auto-tagging
that runs at session completion time. Tags are stored in the SessionLog
model's ListField.

Auto-tag rules are pattern-based (no LLM):
- classification_type -> bug/feature/chore tags
- branch name starting with session/ -> sdlc tag
- transcript patterns -> pr-created, tested tags
- daydream signals -> daydream tag
- work_item_slug set -> planned-work tag
- turn_count >= 20 -> long-session tag

Public API:
    add_tags(session_id, tags)
    remove_tags(session_id, tags)
    get_tags(session_id) -> list[str]
    sessions_by_tag(tag, project_key=None) -> list
    auto_tag_session(session_id)
"""

import logging
from collections import deque
from pathlib import Path

logger = logging.getLogger(__name__)

# Transcript files location (matches bridge/session_transcript.py)
SESSION_LOGS_DIR = Path(__file__).parent.parent / "logs" / "sessions"

# How many lines from the end of the transcript to scan for patterns
TRANSCRIPT_TAIL_LINES = 50


def _get_session(session_id: str):
    """Look up a SessionLog by session_id. Returns None if not found."""
    from models.session_log import SessionLog

    try:
        sessions = list(SessionLog.query.filter(session_id=session_id))
        return sessions[0] if sessions else None
    except Exception as e:
        logger.debug(f"Failed to look up session {session_id}: {e}")
        return None


def add_tags(session_id: str, tags: list[str]) -> None:
    """Add tags to a session. Deduplicates automatically.

    Args:
        session_id: The session to tag.
        tags: List of tag strings to add.
    """
    session = _get_session(session_id)
    if session is None:
        logger.debug(f"add_tags: session {session_id} not found, skipping")
        return

    existing = list(session.tags or [])
    for tag in tags:
        if tag not in existing:
            existing.append(tag)
    session.tags = existing
    session.save()


def remove_tags(session_id: str, tags: list[str]) -> None:
    """Remove tags from a session.

    Args:
        session_id: The session to modify.
        tags: List of tag strings to remove.
    """
    session = _get_session(session_id)
    if session is None:
        logger.debug(f"remove_tags: session {session_id} not found, skipping")
        return

    existing = list(session.tags or [])
    updated = [t for t in existing if t not in tags]
    session.tags = updated
    session.save()


def get_tags(session_id: str) -> list[str]:
    """Get all tags for a session.

    Args:
        session_id: The session to query.

    Returns:
        List of tag strings, or empty list if session not found or has no tags.
    """
    session = _get_session(session_id)
    if session is None:
        return []
    return list(session.tags or [])


def sessions_by_tag(tag: str, project_key: str | None = None) -> list:
    """Find all sessions with a given tag.

    Performs Python-side filtering since Popoto ListField may not
    support native contains queries.

    Args:
        tag: The tag to search for.
        project_key: Optional project key to narrow the search.

    Returns:
        List of SessionLog instances matching the tag.
    """
    from models.session_log import SessionLog

    try:
        if project_key:
            all_sessions = list(SessionLog.query.filter(project_key=project_key))
        else:
            all_sessions = list(SessionLog.query.all())
    except Exception as e:
        logger.warning(f"sessions_by_tag: failed to query sessions: {e}")
        return []

    return [s for s in all_sessions if s.tags and tag in s.tags]


def _read_transcript_tail(
    session_id: str, num_lines: int = TRANSCRIPT_TAIL_LINES
) -> str:
    """Read the last N lines of a session's transcript file.

    Args:
        session_id: Session to read transcript for.
        num_lines: Number of lines from the end to read.

    Returns:
        Concatenated string of the last N lines, or empty string if
        the transcript doesn't exist or can't be read.
    """
    transcript_path = SESSION_LOGS_DIR / session_id / "transcript.txt"
    if not transcript_path.exists():
        return ""

    try:
        # Use a deque with maxlen for efficient tail reading
        with transcript_path.open("r", encoding="utf-8") as f:
            tail = deque(f, maxlen=num_lines)
        return "".join(tail)
    except Exception as e:
        logger.debug(f"Failed to read transcript tail for {session_id}: {e}")
        return ""


def auto_tag_session(session_id: str) -> None:
    """Apply auto-tags to a session based on its metadata and transcript.

    Reads session metadata (classification_type, branch_name, sender,
    work_item_slug, turn_count) and the last 50 lines of the transcript
    to determine which tags to apply.

    This function is safe to call multiple times — it only adds tags,
    never removes existing ones.

    Args:
        session_id: The session to auto-tag.
    """
    session = _get_session(session_id)
    if session is None:
        logger.debug(f"auto_tag_session: session {session_id} not found, skipping")
        return

    new_tags: list[str] = []

    # Rule 1: classification_type -> bug/feature/chore
    if session.classification_type in ("bug", "feature", "chore"):
        new_tags.append(session.classification_type)

    # Rule 2: branch name starts with session/ -> sdlc
    if session.branch_name and session.branch_name.startswith("session/"):
        new_tags.append("sdlc")

    # Rule 3: Transcript pattern matching
    transcript_tail = _read_transcript_tail(session_id)
    if transcript_tail:
        # gh pr create -> pr-created
        if "gh pr create" in transcript_tail:
            new_tags.append("pr-created")

        # pytest or Skill(do-test -> tested
        if "pytest" in transcript_tail or "Skill(do-test" in transcript_tail:
            new_tags.append("tested")

    # Rule 4: Daydream detection
    sender = session.sender or ""
    if "daydream" in sender.lower() or "daydream" in session_id.lower():
        new_tags.append("daydream")

    # Rule 5: work_item_slug set -> planned-work
    if session.work_item_slug:
        new_tags.append("planned-work")

    # Rule 6: turn_count >= 20 -> long-session
    turn_count = session.turn_count or 0
    if turn_count >= 20:
        new_tags.append("long-session")

    # Apply all new tags (add_tags handles deduplication)
    if new_tags:
        add_tags(session_id, new_tags)
