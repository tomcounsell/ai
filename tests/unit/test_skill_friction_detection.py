"""Tests for tools.skill_lifecycle module.

Covers friction detection, skill expiry with 48h safety window,
refresh batch-update logic, frontmatter parsing, and report output.
"""

import sqlite3
import textwrap
import time
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_memory(memory_id: str, content: str, metadata: dict | None = None):
    """Create a mock Memory object."""
    m = MagicMock()
    m.memory_id = memory_id
    m.content = content
    m.metadata = metadata or {}
    m.created_at = "2026-04-01T00:00:00Z"
    return m


def _make_skill_md(*, generated: bool = True, expires_at: str = "2026-04-01") -> str:
    """Build a SKILL.md with frontmatter."""
    lines = ["---"]
    lines.append("name: test-skill")
    lines.append('description: "A test skill"')
    if generated:
        lines.append("generated: true")
    if expires_at:
        lines.append(f"expires_at: {expires_at}")
    lines.append("---")
    lines.append("")
    lines.append("# Test Skill")
    lines.append("Body text.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Friction Detection
# ---------------------------------------------------------------------------


class TestDetectFriction:
    """Tests for detect_friction()."""

    def test_returns_matching_corrections_with_tool_tags(self):
        """Corrections with tool-related tags are returned."""
        from tools.skill_lifecycle import detect_friction

        mem = _make_memory(
            "m1", "Use --json flag", {"category": "correction", "tags": ["cli", "flags"]}
        )
        with patch("tools.skill_lifecycle._query_correction_memories", return_value=[mem]):
            results = detect_friction()

        assert len(results) == 1
        assert results[0]["memory_id"] == "m1"
        assert "cli" in results[0]["tags"]

    def test_skips_corrections_without_tool_tags(self):
        """Corrections without tool-related tags are excluded."""
        from tools.skill_lifecycle import detect_friction

        mem = _make_memory(
            "m2", "Remember to greet users", {"category": "correction", "tags": ["greeting"]}
        )
        with patch("tools.skill_lifecycle._query_correction_memories", return_value=[mem]):
            results = detect_friction()

        assert len(results) == 0

    def test_empty_memory_returns_empty_list(self):
        """No corrections at all yields empty list."""
        from tools.skill_lifecycle import detect_friction

        with patch("tools.skill_lifecycle._query_correction_memories", return_value=[]):
            results = detect_friction()

        assert results == []

    def test_handles_missing_metadata_gracefully(self):
        """Memories with None metadata do not crash."""
        from tools.skill_lifecycle import detect_friction

        mem = _make_memory("m3", "Some content", None)
        mem.metadata = None
        with patch("tools.skill_lifecycle._query_correction_memories", return_value=[mem]):
            results = detect_friction()

        assert results == []

    def test_handles_missing_tags_key(self):
        """Metadata without tags key does not crash."""
        from tools.skill_lifecycle import detect_friction

        mem = _make_memory("m4", "Some content", {"category": "correction"})
        with patch("tools.skill_lifecycle._query_correction_memories", return_value=[mem]):
            results = detect_friction()

        assert results == []


# ---------------------------------------------------------------------------
# Frontmatter Parsing
# ---------------------------------------------------------------------------


class TestFrontmatterParsing:
    """Tests for parse_skill_frontmatter()."""

    def test_parses_generated_flag(self):
        from tools.skill_lifecycle import parse_skill_frontmatter

        fm = parse_skill_frontmatter(_make_skill_md(generated=True))
        assert fm["generated"] is True

    def test_parses_expires_at(self):
        from tools.skill_lifecycle import parse_skill_frontmatter

        fm = parse_skill_frontmatter(_make_skill_md(expires_at="2026-05-01"))
        assert fm["expires_at"] == "2026-05-01"

    def test_non_generated_skill(self):
        from tools.skill_lifecycle import parse_skill_frontmatter

        fm = parse_skill_frontmatter(_make_skill_md(generated=False))
        assert fm.get("generated") is not True

    def test_no_frontmatter(self):
        from tools.skill_lifecycle import parse_skill_frontmatter

        fm = parse_skill_frontmatter("# No frontmatter here\nJust body.")
        assert fm == {}


# ---------------------------------------------------------------------------
# Expiry: 48h Safety Window
# ---------------------------------------------------------------------------


class TestExpirySafetyWindow:
    """Tests for should_expire_skill() with 48h safety window."""

    def test_recently_invoked_skill_is_not_expired(self):
        """Skill invoked within 48h should NOT be expired."""
        from tools.skill_lifecycle import should_expire_skill

        # Last invoked 1 hour ago
        last_invoked = time.time() - 3600
        assert should_expire_skill("test-skill", last_invoked_ts=last_invoked) is False

    def test_stale_skill_is_expired(self):
        """Skill not invoked for >48h should be expired."""
        from tools.skill_lifecycle import should_expire_skill

        # Last invoked 72 hours ago
        last_invoked = time.time() - (72 * 3600)
        assert should_expire_skill("test-skill", last_invoked_ts=last_invoked) is True

    def test_never_invoked_skill_is_expired(self):
        """Skill with no invocation record should be expired."""
        from tools.skill_lifecycle import should_expire_skill

        assert should_expire_skill("test-skill", last_invoked_ts=None) is True


# ---------------------------------------------------------------------------
# Refresh: Update expires_at in frontmatter
# ---------------------------------------------------------------------------


class TestRefreshFrontmatter:
    """Tests for update_expires_at_in_frontmatter()."""

    def test_updates_existing_expires_at(self):
        from tools.skill_lifecycle import update_expires_at_in_frontmatter

        content = _make_skill_md(expires_at="2026-04-01")
        updated = update_expires_at_in_frontmatter(content, "2026-05-11")
        assert "expires_at: 2026-05-11" in updated
        assert "expires_at: 2026-04-01" not in updated

    def test_adds_expires_at_if_missing(self):
        from tools.skill_lifecycle import update_expires_at_in_frontmatter

        content = textwrap.dedent("""\
            ---
            name: test-skill
            generated: true
            ---
            # Body
        """)
        updated = update_expires_at_in_frontmatter(content, "2026-05-11")
        assert "expires_at: 2026-05-11" in updated


# ---------------------------------------------------------------------------
# Report: Analytics query
# ---------------------------------------------------------------------------


class TestReport:
    """Tests for get_skill_report()."""

    def test_returns_empty_report_when_no_data(self):
        from tools.skill_lifecycle import get_skill_report

        with patch("tools.skill_lifecycle._get_analytics_connection", return_value=None):
            report = get_skill_report()

        assert report == []

    def test_returns_formatted_report_from_db(self, tmp_path):
        from tools.skill_lifecycle import get_skill_report

        db_path = tmp_path / "analytics.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE metrics ("
            "id INTEGER PRIMARY KEY, timestamp REAL, "
            "name TEXT, value REAL, dimensions TEXT)"
        )
        now = time.time()
        conn.execute(
            "INSERT INTO metrics (timestamp, name, value, dimensions) VALUES (?, ?, ?, ?)",
            (now, "skill.invocation", 1.0, '{"skill": "my-skill"}'),
        )
        conn.execute(
            "INSERT INTO metrics (timestamp, name, value, dimensions) VALUES (?, ?, ?, ?)",
            (now - 100, "skill.invocation", 1.0, '{"skill": "my-skill"}'),
        )
        conn.commit()

        with patch("tools.skill_lifecycle._get_analytics_connection", return_value=conn):
            report = get_skill_report()

        assert len(report) == 1
        assert report[0]["skill"] == "my-skill"
        assert report[0]["count"] == 2
        conn.close()
