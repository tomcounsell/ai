"""Unit tests for tools/session_tags.py — session tagging system.

Tests cover:
- add_tags, remove_tags, get_tags CRUD operations
- sessions_by_tag query
- auto_tag_session with all auto-tag rules
- Graceful handling of missing sessions
"""

import shutil
import time
from pathlib import Path

import pytest

from models.agent_session import AgentSession

# Will be implemented in tools/session_tags.py
from tools.session_tags import (
    add_tags,
    auto_tag_session,
    get_tags,
    remove_tags,
    sessions_by_tag,
)

# Transcript log directory (matches bridge/session_transcript.py)
SESSION_LOGS_DIR = Path(__file__).resolve().parent.parent.parent / "logs" / "sessions"


def _create_session(
    session_id: str = "test-session-1",
    project_key: str = "test",
    classification_type: str | None = None,
    branch_name: str | None = None,
    sender: str | None = None,
    work_item_slug: str | None = None,
    turn_count: int = 5,
    tags: list | None = None,
) -> AgentSession:
    """Helper to create a AgentSession for testing."""
    return AgentSession.create(
        session_id=session_id,
        project_key=project_key,
        status="active",
        created_at=time.time(),
        started_at=time.time(),
        last_activity=time.time(),
        turn_count=turn_count,
        tool_call_count=0,
        classification_type=classification_type,
        branch_name=branch_name,
        sender_name=sender,
        work_item_slug=work_item_slug,
        tags=tags,
    )


def _write_transcript(session_id: str, lines: list[str]) -> Path:
    """Write transcript lines for a session and return the path."""
    session_dir = SESSION_LOGS_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    transcript = session_dir / "transcript.txt"
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return transcript


@pytest.fixture(autouse=True)
def cleanup_transcript_dirs():
    """Clean up any transcript directories created during tests."""
    created_dirs = []
    yield created_dirs
    for d in created_dirs:
        shutil.rmtree(d, ignore_errors=True)


# ===================================================================
# add_tags / remove_tags / get_tags
# ===================================================================


class TestTagCRUD:
    """Tests for add_tags, remove_tags, get_tags."""

    def test_add_tags_to_session(self):
        _create_session(session_id="crud-1")
        add_tags("crud-1", ["bug", "hotfix"])
        assert sorted(get_tags("crud-1")) == ["bug", "hotfix"]

    def test_add_tags_deduplicates(self):
        _create_session(session_id="crud-2")
        add_tags("crud-2", ["bug", "hotfix"])
        add_tags("crud-2", ["bug", "new-tag"])
        tags = get_tags("crud-2")
        assert tags.count("bug") == 1
        assert "hotfix" in tags
        assert "new-tag" in tags

    def test_remove_tags(self):
        _create_session(session_id="crud-3", tags=["bug", "hotfix", "sdlc"])
        remove_tags("crud-3", ["hotfix"])
        assert sorted(get_tags("crud-3")) == ["bug", "sdlc"]

    def test_remove_nonexistent_tag(self):
        _create_session(session_id="crud-4", tags=["bug"])
        remove_tags("crud-4", ["nonexistent"])
        assert get_tags("crud-4") == ["bug"]

    def test_get_tags_empty(self):
        _create_session(session_id="crud-5")
        assert get_tags("crud-5") == []

    def test_get_tags_missing_session(self):
        assert get_tags("nonexistent-session") == []

    def test_add_tags_missing_session(self):
        # Should not raise — graceful no-op
        add_tags("nonexistent-session", ["tag1"])

    def test_remove_tags_missing_session(self):
        # Should not raise — graceful no-op
        remove_tags("nonexistent-session", ["tag1"])


# ===================================================================
# sessions_by_tag
# ===================================================================


class TestSessionsByTag:
    def test_find_sessions_by_tag(self):
        _create_session(session_id="tag-search-1", project_key="proj-a", tags=["bug"])
        _create_session(
            session_id="tag-search-2", project_key="proj-a", tags=["feature"]
        )
        _create_session(
            session_id="tag-search-3", project_key="proj-a", tags=["bug", "hotfix"]
        )

        results = sessions_by_tag("bug")
        session_ids = [s.session_id for s in results]
        assert "tag-search-1" in session_ids
        assert "tag-search-3" in session_ids
        assert "tag-search-2" not in session_ids

    def test_filter_by_project_key(self):
        _create_session(session_id="proj-1", project_key="proj-a", tags=["bug"])
        _create_session(session_id="proj-2", project_key="proj-b", tags=["bug"])

        results = sessions_by_tag("bug", project_key="proj-a")
        session_ids = [s.session_id for s in results]
        assert "proj-1" in session_ids
        assert "proj-2" not in session_ids

    def test_no_matches(self):
        _create_session(session_id="no-match-1", tags=["feature"])
        results = sessions_by_tag("nonexistent-tag")
        assert results == []


# ===================================================================
# auto_tag_session — classification_type rules
# ===================================================================


class TestAutoTagClassification:
    def test_bug_classification(self):
        _create_session(session_id="cls-bug", classification_type="bug")
        auto_tag_session("cls-bug")
        assert "bug" in get_tags("cls-bug")

    def test_feature_classification(self):
        _create_session(session_id="cls-feat", classification_type="feature")
        auto_tag_session("cls-feat")
        assert "feature" in get_tags("cls-feat")

    def test_chore_classification(self):
        _create_session(session_id="cls-chore", classification_type="chore")
        auto_tag_session("cls-chore")
        assert "chore" in get_tags("cls-chore")

    def test_no_classification(self):
        _create_session(session_id="cls-none")
        auto_tag_session("cls-none")
        tags = get_tags("cls-none")
        assert "bug" not in tags
        assert "feature" not in tags
        assert "chore" not in tags


# ===================================================================
# auto_tag_session — branch name rules
# ===================================================================


class TestAutoTagBranch:
    def test_session_branch_gets_sdlc_tag(self):
        _create_session(session_id="br-sdlc", branch_name="session/fix-something")
        auto_tag_session("br-sdlc")
        assert "sdlc" in get_tags("br-sdlc")

    def test_non_session_branch_no_sdlc_tag(self):
        _create_session(session_id="br-main", branch_name="main")
        auto_tag_session("br-main")
        assert "sdlc" not in get_tags("br-main")

    def test_no_branch_no_sdlc_tag(self):
        _create_session(session_id="br-none")
        auto_tag_session("br-none")
        assert "sdlc" not in get_tags("br-none")


# ===================================================================
# auto_tag_session — transcript pattern rules
# ===================================================================


class TestAutoTagTranscript:
    def test_pr_created_tag(self, cleanup_transcript_dirs):
        _create_session(session_id="tr-pr")
        session_dir = SESSION_LOGS_DIR / "tr-pr"
        _write_transcript(
            "tr-pr",
            [
                "[2025-01-01T00:00:00] TOOL_CALL: Bash(git add .)",
                "[2025-01-01T00:01:00] TOOL_CALL: Bash(gh pr create --title 'Fix')",
                "[2025-01-01T00:02:00] SESSION_END: status=completed",
            ],
        )
        cleanup_transcript_dirs.append(session_dir)

        auto_tag_session("tr-pr")
        assert "pr-created" in get_tags("tr-pr")

    def test_tested_tag_from_pytest(self, cleanup_transcript_dirs):
        _create_session(session_id="tr-pytest")
        session_dir = SESSION_LOGS_DIR / "tr-pytest"
        _write_transcript(
            "tr-pytest",
            [
                "[2025-01-01T00:00:00] TOOL_CALL: Bash(pytest tests/ -v)",
                "[2025-01-01T00:01:00] SESSION_END: status=completed",
            ],
        )
        cleanup_transcript_dirs.append(session_dir)

        auto_tag_session("tr-pytest")
        assert "tested" in get_tags("tr-pytest")

    def test_tested_tag_from_skill(self, cleanup_transcript_dirs):
        _create_session(session_id="tr-skill")
        session_dir = SESSION_LOGS_DIR / "tr-skill"
        _write_transcript(
            "tr-skill",
            [
                "[2025-01-01T00:00:00] TOOL_CALL: Skill(do-test)",
                "[2025-01-01T00:01:00] SESSION_END: status=completed",
            ],
        )
        cleanup_transcript_dirs.append(session_dir)

        auto_tag_session("tr-skill")
        assert "tested" in get_tags("tr-skill")

    def test_no_transcript_no_transcript_tags(self):
        _create_session(session_id="tr-none")
        auto_tag_session("tr-none")
        tags = get_tags("tr-none")
        assert "pr-created" not in tags
        assert "tested" not in tags


# ===================================================================
# auto_tag_session — reflections detection
# ===================================================================


class TestAutoTagReflections:
    def test_sender_contains_reflections(self):
        _create_session(session_id="dd-sender", sender="reflections-script")
        auto_tag_session("dd-sender")
        assert "reflections" in get_tags("dd-sender")

    def test_session_id_contains_reflections(self):
        _create_session(session_id="reflections-20250101")
        auto_tag_session("reflections-20250101")
        assert "reflections" in get_tags("reflections-20250101")

    def test_no_reflections_signal(self):
        _create_session(session_id="normal-session", sender="valor")
        auto_tag_session("normal-session")
        assert "reflections" not in get_tags("normal-session")


# ===================================================================
# auto_tag_session — work_item_slug and turn_count
# ===================================================================


class TestAutoTagMisc:
    def test_planned_work_tag(self):
        _create_session(session_id="pw-1", work_item_slug="fix-login-bug")
        auto_tag_session("pw-1")
        assert "planned-work" in get_tags("pw-1")

    def test_no_slug_no_planned_work_tag(self):
        _create_session(session_id="pw-none")
        auto_tag_session("pw-none")
        assert "planned-work" not in get_tags("pw-none")

    def test_long_session_tag(self):
        _create_session(session_id="ls-long", turn_count=25)
        auto_tag_session("ls-long")
        assert "long-session" in get_tags("ls-long")

    def test_short_session_no_long_tag(self):
        _create_session(session_id="ls-short", turn_count=5)
        auto_tag_session("ls-short")
        assert "long-session" not in get_tags("ls-short")

    def test_boundary_turn_count_20(self):
        _create_session(session_id="ls-boundary", turn_count=20)
        auto_tag_session("ls-boundary")
        assert "long-session" in get_tags("ls-boundary")


# ===================================================================
# auto_tag_session — graceful handling
# ===================================================================


class TestAutoTagGraceful:
    def test_missing_session_no_error(self):
        # Should not raise any exception
        auto_tag_session("totally-nonexistent-session-xyz")

    def test_preserves_existing_tags(self):
        _create_session(
            session_id="preserve-1",
            tags=["manual-tag"],
            classification_type="bug",
        )
        auto_tag_session("preserve-1")
        tags = get_tags("preserve-1")
        assert "manual-tag" in tags
        assert "bug" in tags

    def test_multiple_rules_combined(self, cleanup_transcript_dirs):
        _create_session(
            session_id="combo-1",
            classification_type="feature",
            branch_name="session/new-feature",
            work_item_slug="new-feature",
            turn_count=25,
        )
        session_dir = SESSION_LOGS_DIR / "combo-1"
        _write_transcript(
            "combo-1",
            [
                "[2025-01-01T00:00:00] TOOL_CALL: Bash(pytest tests/ -v)",
                "[2025-01-01T00:01:00] TOOL_CALL: Bash(gh pr create --title 'New feature')",
            ],
        )
        cleanup_transcript_dirs.append(session_dir)

        auto_tag_session("combo-1")
        tags = get_tags("combo-1")
        assert "feature" in tags
        assert "sdlc" in tags
        assert "planned-work" in tags
        assert "long-session" in tags
        assert "pr-created" in tags
        assert "tested" in tags
