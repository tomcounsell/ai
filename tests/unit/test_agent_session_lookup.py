"""
Tests for AgentSession.get_by_id() and regression guard against
positional AgentSession.query.get() calls.
"""

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# get_by_id unit tests
# ---------------------------------------------------------------------------


class TestGetById:
    def _make_session(self, session_id: str):
        s = MagicMock()
        s.id = session_id
        return s

    def test_returns_session_when_found(self):
        from models.agent_session import AgentSession

        fake = self._make_session("abc123")
        with patch.object(AgentSession.query, "filter", return_value=[fake]):
            result = AgentSession.get_by_id("abc123")
        assert result is fake

    def test_returns_none_when_not_found(self):
        from models.agent_session import AgentSession

        with patch.object(AgentSession.query, "filter", return_value=[]):
            result = AgentSession.get_by_id("missing-id")
        assert result is None

    def test_returns_none_for_empty_string(self):
        from models.agent_session import AgentSession

        with patch.object(AgentSession.query, "filter") as mock_filter:
            result = AgentSession.get_by_id("")
        assert result is None
        mock_filter.assert_not_called()

    def test_returns_none_for_none_input(self):
        from models.agent_session import AgentSession

        with patch.object(AgentSession.query, "filter") as mock_filter:
            result = AgentSession.get_by_id(None)
        assert result is None
        mock_filter.assert_not_called()

    def test_returns_none_for_whitespace_only(self):
        from models.agent_session import AgentSession

        with patch.object(AgentSession.query, "filter") as mock_filter:
            result = AgentSession.get_by_id("   ")
        assert result is None
        mock_filter.assert_not_called()

    def test_returns_first_match_only(self):
        """Should return first result if multiple somehow match (UUID collision guard)."""
        from models.agent_session import AgentSession

        first = self._make_session("dup-id")
        second = self._make_session("dup-id")
        with patch.object(AgentSession.query, "filter", return_value=[first, second]):
            result = AgentSession.get_by_id("dup-id")
        assert result is first


# ---------------------------------------------------------------------------
# Regression guard: no positional AgentSession.query.get() calls in source
# ---------------------------------------------------------------------------

_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_BROKEN_PATTERN = re.compile(r"AgentSession\.query\.get\(\s*(?!redis_key=|db_key=)[^\)]+\)")
_SKIP_DIRS = {".worktrees", ".venv", "__pycache__", ".git"}


def _iter_python_files():
    for path in _SOURCE_ROOT.rglob("*.py"):
        if any(skip in path.parts for skip in _SKIP_DIRS):
            continue
        if "test_" in path.name:
            continue
        yield path


@pytest.mark.parametrize("py_file", list(_iter_python_files()))
def test_no_positional_query_get(py_file: Path):
    """Fail if any source file calls AgentSession.query.get() with a positional arg."""
    source = py_file.read_text(encoding="utf-8", errors="ignore")
    # Strip comment lines before checking
    lines = [ln for ln in source.splitlines() if not ln.lstrip().startswith("#")]
    clean = "\n".join(lines)
    matches = _BROKEN_PATTERN.findall(clean)
    assert not matches, (
        f"{py_file.relative_to(_SOURCE_ROOT)} contains broken positional "
        f"AgentSession.query.get() call(s): {matches}\n"
        f"Use AgentSession.get_by_id(string) instead."
    )
