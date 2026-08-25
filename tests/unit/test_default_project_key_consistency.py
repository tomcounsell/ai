"""Regression tests for canonical project_key resolution (issue #1171).

Asserts:
  - The writer-side default (``tools.agent_session_scheduler.DEFAULT_PROJECT_KEY``)
    matches the reader-side fallback resolved by the recovery code at
    ``reflections.redis_access.get_project_key()`` and the inline fallbacks at
    ``agent.session_pickup`` / ``agent.agent_session_queue``.
  - Empty / whitespace-only ``VALOR_PROJECT_KEY`` env values fall back to
    ``"valor"`` (defense in depth — protects against a misconfigured
    ``VALOR_PROJECT_KEY=`` line in ``.env`` that the plist injector would
    otherwise propagate as an empty string).

These tests catch the "writer ↔ reader namespace drift" failure mode that
left ``paused_circuit`` sessions stranded in production prior to the fix.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch


def _resolve_session_pickup_pk() -> str:
    """Mirror the inline resolution in ``agent.session_pickup`` (line 180)."""
    _v = os.environ.get("VALOR_PROJECT_KEY", "").strip()
    return _v or "valor"


def _resolve_agent_session_queue_pk() -> str:
    """Mirror the inline resolution in ``agent.agent_session_queue`` (line 1408)."""
    _v = os.environ.get("VALOR_PROJECT_KEY", "").strip()
    return _v or "valor"


class TestWriterReaderConsistency(unittest.TestCase):
    """The writer default and reader fallback MUST agree."""

    def test_writer_default_is_valor(self):
        from tools.agent_session_scheduler import DEFAULT_PROJECT_KEY

        assert DEFAULT_PROJECT_KEY == "valor", (
            f"AgentSession writer default drifted: {DEFAULT_PROJECT_KEY!r}"
        )

    def test_reader_default_matches_writer_when_env_unset(self):
        """With VALOR_PROJECT_KEY unset, the reader fallback must equal the writer default."""
        from reflections.redis_access import get_project_key
        from tools.agent_session_scheduler import DEFAULT_PROJECT_KEY

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VALOR_PROJECT_KEY", None)
            resolved = get_project_key()

        assert resolved == DEFAULT_PROJECT_KEY, (
            f"reader fallback ({resolved!r}) != writer default ({DEFAULT_PROJECT_KEY!r}); "
            "AgentSessions tagged by writer will be invisible to recovery code"
        )

    def test_all_three_fallback_paths_agree_when_env_unset(self):
        """redis_access, session_pickup, and agent_session_queue must all resolve identically."""
        from reflections.redis_access import get_project_key

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VALOR_PROJECT_KEY", None)
            canonical = get_project_key()
            sp = _resolve_session_pickup_pk()
            asq = _resolve_agent_session_queue_pk()

        assert canonical == sp == asq == "valor", (
            "fallback drift: "
            f"redis_access={canonical!r}, session_pickup={sp!r}, agent_session_queue={asq!r}"
        )


class TestEmptyEnvDefense(unittest.TestCase):
    """Empty or whitespace VALOR_PROJECT_KEY must fall back to 'valor', not ''."""

    def test_empty_env_falls_back_to_valor(self):
        from reflections.redis_access import get_project_key

        with patch.dict(os.environ, {"VALOR_PROJECT_KEY": ""}):
            assert get_project_key() == "valor"
            assert _resolve_session_pickup_pk() == "valor"
            assert _resolve_agent_session_queue_pk() == "valor"

    def test_whitespace_env_falls_back_to_valor(self):
        from reflections.redis_access import get_project_key

        with patch.dict(os.environ, {"VALOR_PROJECT_KEY": "   "}):
            assert get_project_key() == "valor"
            assert _resolve_session_pickup_pk() == "valor"
            assert _resolve_agent_session_queue_pk() == "valor"

    def test_tab_only_env_falls_back_to_valor(self):
        from reflections.redis_access import get_project_key

        with patch.dict(os.environ, {"VALOR_PROJECT_KEY": "\t\t"}):
            assert get_project_key() == "valor"


class TestValidEnvOverride(unittest.TestCase):
    """A valid (non-empty, non-whitespace) value still overrides the fallback."""

    def test_valid_env_takes_precedence(self):
        from reflections.redis_access import get_project_key

        with patch.dict(os.environ, {"VALOR_PROJECT_KEY": "popoto"}):
            assert get_project_key() == "popoto"
            assert _resolve_session_pickup_pk() == "popoto"
            assert _resolve_agent_session_queue_pk() == "popoto"

    def test_value_is_stripped_before_use(self):
        """Surrounding whitespace is stripped (defensive parity with reader paths)."""
        from reflections.redis_access import get_project_key

        with patch.dict(os.environ, {"VALOR_PROJECT_KEY": "  popoto  "}):
            assert get_project_key() == "popoto"


if __name__ == "__main__":
    unittest.main()
