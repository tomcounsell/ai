"""Session tagging system for SessionLog categorization.

Provides CRUD operations for session tags and rule-based auto-tagging
that runs at session completion time. Tags are stored in the SessionLog
model's ListField.

Auto-tag rules are pattern-based (no LLM):
- classification_type -> bug/feature/chore tags
- branch name starting with session/ -> sdlc tag
- transcript patterns -> pr-created, tested tags
- reflections signals -> reflections tag
- slug set -> planned-work tag
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
    from models.agent_session import AgentSession

    try:
        sessions = list(AgentSession.query.filter(session_id=session_id))
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
    from models.agent_session import AgentSession

    try:
        if project_key:
            all_sessions = list(AgentSession.query.filter(project_key=project_key))
        else:
            all_sessions = list(AgentSession.query.all())
    except Exception as e:
        logger.warning(f"sessions_by_tag: failed to query sessions: {e}")
        return []

    return [s for s in all_sessions if s.tags and tag in s.tags]


def _read_transcript_tail(session_id: str, num_lines: int = TRANSCRIPT_TAIL_LINES) -> str:
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


def _derive_task_type(session, applied_tags: list[str]) -> str | None:
    """Derive a task_type from session fields using pattern-based rules.

    Priority order:
    1. rework_triggered=True → "rework-triggered"
    2. classification_type=="bug" → "bug-fix"
    3. SDLC stage markers in tags/transcript → "sdlc-{stage}"
       - "pr-created" in tags → "sdlc-build"
       - "tested" in tags and SDLC branch → "sdlc-test"
       - SDLC branch + slug but no PR yet → "sdlc-plan"
    4. SDLC branch + slug + "pr-created" → "sdlc-build" (already covered above)
    5. slug set + no PR created → "greenfield-feature"
    6. SDLC branch without slug → generic SDLC, skip (not specific enough)
    7. None — do not force classification

    No LLM calls — purely pattern-based.

    Args:
        session: AgentSession instance (already loaded).
        applied_tags: Tags just applied in this auto_tag_session() call (may
            include newly-added tags not yet persisted to session.tags).

    Returns:
        A task_type string from TASK_TYPE_VOCABULARY, or None if indeterminate.
    """

    classification = getattr(session, "classification_type", None) or ""
    branch = getattr(session, "branch_name", None) or ""
    slug = getattr(session, "slug", None)
    rework = getattr(session, "rework_triggered", None)

    # Combine persisted tags with newly-applied ones for pattern matching
    persisted_tags = list(session.tags or [])
    all_tags = set(persisted_tags) | set(applied_tags)

    is_sdlc_branch = branch.startswith("session/")

    # Priority 1: rework_triggered flag
    if str(rework).lower() == "true":
        return "rework-triggered"

    # Priority 2: bug classification
    if classification == "bug":
        return "bug-fix"

    # Priority 3: SDLC stage from transcript/tag markers
    if is_sdlc_branch:
        if "pr-created" in all_tags:
            return "sdlc-build"
        if "tested" in all_tags:
            return "sdlc-test"
        if slug and "pr-created" not in all_tags and "tested" not in all_tags:
            # Has a slug but no PR yet — likely in planning stage
            return "sdlc-plan"

    # Priority 4: slug set + no SDLC branch markers → greenfield feature
    if slug and not is_sdlc_branch:
        return "greenfield-feature"

    # Cannot classify — return None
    return None


def auto_tag_session(session_id: str) -> None:
    """Apply auto-tags to a session based on its metadata and transcript.

    Reads session metadata (classification_type, branch_name, sender,
    slug, turn_count) and the last 50 lines of the transcript
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

    # Rule 4: Reflections detection
    sender = session.sender or ""
    if "reflections" in sender.lower() or "reflections" in session_id.lower():
        new_tags.append("reflections")

    # Rule 5: slug set -> planned-work
    if session.slug:
        new_tags.append("planned-work")

    # Rule 6: turn_count >= 20 -> long-session
    turn_count = session.turn_count or 0
    if turn_count >= 20:
        new_tags.append("long-session")

    # Apply all new tags (add_tags handles deduplication)
    if new_tags:
        add_tags(session_id, new_tags)

    # Rule 7: derive task_type from session fields (idempotent — only set if not already set).
    # Re-read the session to pick up any tags just written by add_tags() above.
    if not getattr(session, "task_type", None):
        derived_type = _derive_task_type(session, new_tags)
        if derived_type:
            try:
                # Re-read from Redis to get the freshest state (add_tags may have saved tags)
                fresh_session = _get_session(session_id)
                if fresh_session and not getattr(fresh_session, "task_type", None):
                    fresh_session.task_type = derived_type
                    fresh_session.save()
                    logger.debug(
                        f"auto_tag_session: set task_type={derived_type!r} for {session_id}"
                    )
            except Exception as e:
                logger.debug(f"auto_tag_session: failed to set task_type for {session_id}: {e}")
