"""Tests for principal context loading and injection.

Tests load_principal_context() in agent/sdk_client.py and the Observer's
dynamic system prompt builder in bridge/observer.py.

Run with: pytest tests/unit/test_principal_context.py -v
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.sdk_client import load_principal_context, load_system_prompt

# Sample PRINCIPAL.md content for testing
SAMPLE_PRINCIPAL = """\
# Principal Context -- Test

---

## Mission

Build autonomous AI coworker systems that ship real software.

---

## Goals (6-12 Month Horizon)

1. **Valor as a reliable coworker** -- handle work requests autonomously.
2. **Multi-project throughput** -- context-switch between projects cleanly.

---

## Beliefs (Working Assumptions)

- AI agents will replace junior/mid dev capacity within 2 years.
- System > prompt.

---

## Strategies

1. Build the system that builds the system.

---

## Projects (Active Portfolio)

| Project | Strategic Role | Priority Signal |
|---------|---------------|-----------------|
| **Valor AI** | Core infrastructure | auto_merge: true |
| **Popoto** | Redis ORM | auto_merge: false |

**Inferred priority order:** Valor > Popoto
"""


def _mock_principal_path(content: str):
    """Create a temp file with the given content and return a patch for PRINCIPAL_PATH."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
    tmp.write(content)
    tmp.flush()
    tmp.close()
    return patch("agent.sdk_client.PRINCIPAL_PATH", Path(tmp.name)), tmp.name


# --- load_principal_context() tests ---


def test_load_principal_context_returns_string():
    """Principal context loader returns a non-empty string when file exists."""
    patcher, tmp_path = _mock_principal_path(SAMPLE_PRINCIPAL)
    try:
        with patcher:
            result = load_principal_context()
            assert isinstance(result, str)
            assert len(result) > 0
    finally:
        os.unlink(tmp_path)


def test_load_principal_context_condensed_extracts_sections():
    """Condensed mode extracts only Mission, Goals, and Projects sections."""
    patcher, tmp_path = _mock_principal_path(SAMPLE_PRINCIPAL)
    try:
        with patcher:
            result = load_principal_context(condensed=True)
            assert "Mission" in result
            assert "autonomous AI coworker" in result
            # Should NOT include Beliefs or Strategies (not extracted in condensed mode)
            assert "Beliefs" not in result
            assert "Strategies" not in result
    finally:
        os.unlink(tmp_path)


def test_load_principal_context_full_includes_all():
    """Full mode returns the complete file content."""
    patcher, tmp_path = _mock_principal_path(SAMPLE_PRINCIPAL)
    try:
        with patcher:
            full = load_principal_context(condensed=False)
            condensed = load_principal_context(condensed=True)
            # Full content should include sections not in condensed version
            assert "Beliefs" in full
            assert "Strategies" in full
            assert len(full) > len(condensed)
    finally:
        os.unlink(tmp_path)


def test_load_principal_context_missing_file():
    """Missing PRINCIPAL.md returns empty string without crashing."""
    with patch("agent.sdk_client.PRINCIPAL_PATH", Path("/nonexistent/PRINCIPAL.md")):
        result = load_principal_context()
        assert result == ""


def test_load_principal_context_empty_file():
    """Empty PRINCIPAL.md returns empty string without crashing."""
    patcher, tmp_path = _mock_principal_path("")
    try:
        with patcher:
            result = load_principal_context()
            assert result == ""
    finally:
        os.unlink(tmp_path)


def test_load_principal_context_whitespace_only():
    """Whitespace-only PRINCIPAL.md returns empty string."""
    patcher, tmp_path = _mock_principal_path("   \n\n  \n  ")
    try:
        with patcher:
            result = load_principal_context()
            assert result == ""
    finally:
        os.unlink(tmp_path)


def test_load_principal_context_condensed_token_budget():
    """Condensed summary should be reasonably short (under ~2000 chars / ~500 tokens)."""
    patcher, tmp_path = _mock_principal_path(SAMPLE_PRINCIPAL)
    try:
        with patcher:
            result = load_principal_context(condensed=True)
            # 500 tokens is roughly 2000 characters
            assert len(result) < 4000, f"Condensed principal context is too long: {len(result)} chars"
    finally:
        os.unlink(tmp_path)


def test_load_principal_context_no_matching_sections_fallback():
    """When no expected sections are found, falls back to first 500 chars."""
    content = "Just some random content without any markdown headers or sections at all. " * 20
    patcher, tmp_path = _mock_principal_path(content)
    try:
        with patcher:
            result = load_principal_context(condensed=True)
            assert len(result) <= 500
            assert result == content.strip()[:500]
    finally:
        os.unlink(tmp_path)


# --- load_system_prompt() integration ---


def test_system_prompt_includes_principal_context():
    """System prompt should include principal context section when PRINCIPAL.md exists."""
    patcher, tmp_path = _mock_principal_path(SAMPLE_PRINCIPAL)
    try:
        with patcher:
            prompt = load_system_prompt()
            assert "Principal Context" in prompt
    finally:
        os.unlink(tmp_path)


def test_system_prompt_includes_soul_and_principal():
    """System prompt contains both SOUL.md and PRINCIPAL.md content."""
    patcher, tmp_path = _mock_principal_path(SAMPLE_PRINCIPAL)
    try:
        with patcher:
            prompt = load_system_prompt()
            assert "Valor" in prompt  # From SOUL.md
            assert "Principal Context" in prompt  # Section header
            assert "Mission" in prompt  # From PRINCIPAL.md
    finally:
        os.unlink(tmp_path)


def test_system_prompt_graceful_without_principal():
    """System prompt works fine when PRINCIPAL.md is missing."""
    with patch("agent.sdk_client.PRINCIPAL_PATH", Path("/nonexistent/PRINCIPAL.md")):
        prompt = load_system_prompt()
        assert "Valor" in prompt  # SOUL.md still loaded
        assert "Principal Context" not in prompt  # No principal section


# --- Observer system prompt ---


def test_observer_prompt_builder_includes_principal():
    """Observer's dynamic prompt builder includes full principal context."""
    patcher, tmp_path = _mock_principal_path(SAMPLE_PRINCIPAL)
    try:
        with patcher:
            from bridge.observer import _build_observer_system_prompt

            prompt = _build_observer_system_prompt()
            assert "Observer Agent" in prompt
            assert "Principal Context" in prompt
            # Should include strategic content from full (non-condensed) context
            assert "Beliefs" in prompt
            assert "Strategies" in prompt
    finally:
        os.unlink(tmp_path)


def test_observer_prompt_builder_graceful_without_principal():
    """Observer prompt builder works when PRINCIPAL.md is missing."""
    with patch("agent.sdk_client.PRINCIPAL_PATH", Path("/nonexistent/PRINCIPAL.md")):
        from bridge.observer import _build_observer_system_prompt

        prompt = _build_observer_system_prompt()
        assert "Observer Agent" in prompt
        assert "STEER" in prompt
        assert "DELIVER" in prompt
