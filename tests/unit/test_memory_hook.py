"""Unit tests for the memory hook (thought injection)."""

import pytest


class TestExtractTopicKeywords:
    """Test agent/memory_hook.py extract_topic_keywords()."""

    def test_extracts_from_file_path(self):
        from agent.memory_hook import extract_topic_keywords

        keywords = extract_topic_keywords("Read", {"file_path": "/src/deploy/config.yaml"})
        assert "deploy" in keywords
        assert "config" in keywords

    def test_extracts_from_grep_pattern(self):
        from agent.memory_hook import extract_topic_keywords

        keywords = extract_topic_keywords("Grep", {"pattern": "rollback.*strategy"})
        assert "rollback" in keywords
        assert "strategy" in keywords

    def test_extracts_from_command(self):
        from agent.memory_hook import extract_topic_keywords

        keywords = extract_topic_keywords("Bash", {"command": "kubectl get pods"})
        assert "kubectl" in keywords

    def test_empty_input(self):
        from agent.memory_hook import extract_topic_keywords

        keywords = extract_topic_keywords("", {})
        assert isinstance(keywords, list)

    def test_non_dict_input(self):
        from agent.memory_hook import extract_topic_keywords

        keywords = extract_topic_keywords("Read", "just a string")
        assert isinstance(keywords, list)

    def test_filters_noise_words(self):
        from agent.memory_hook import extract_topic_keywords

        keywords = extract_topic_keywords("Read", {"file_path": "/usr/bin/test/file"})
        assert "usr" not in keywords
        assert "bin" not in keywords
        assert "test" not in keywords

    def test_caps_at_10_keywords(self):
        from agent.memory_hook import extract_topic_keywords

        # Long path with many segments
        long_path = "/a/b/c/d/e/f/g/h/i/j/k/l/m/n/o/p/q/r/s/t/u/v/w/x/y/z"
        keywords = extract_topic_keywords("Read", {"file_path": long_path})
        assert len(keywords) <= 10


class TestCheckAndInject:
    """Test agent/memory_hook.py check_and_inject()."""

    def test_returns_none_before_window(self):
        from agent.memory_hook import _tool_counts, check_and_inject

        # Reset state
        session = "test-inject-1"
        _tool_counts.pop(session, None)

        result = check_and_inject(session, "Read", {"file_path": "/test.py"})
        # First call (count=1) should return None (not multiple of WINDOW_SIZE=3)
        assert result is None

    def test_returns_none_for_empty_keywords(self):
        from agent.memory_hook import _tool_buffers, _tool_counts, check_and_inject

        session = "test-inject-2"
        _tool_counts[session] = 2  # Next call will be count=3 (window trigger)
        _tool_buffers[session] = [{"tool_name": "", "tool_input": {}}] * 2

        result = check_and_inject(session, "", {})
        # No meaningful keywords -> None
        assert result is None

    def test_never_crashes(self):
        from agent.memory_hook import check_and_inject

        # Should never raise, even with bad inputs
        result = check_and_inject("bad-session", None, None)
        assert result is None


class TestGetInjectedThoughts:
    """Test agent/memory_hook.py get_injected_thoughts()."""

    def test_returns_empty_for_unknown_session(self):
        from agent.memory_hook import get_injected_thoughts

        result = get_injected_thoughts("nonexistent-session")
        assert result == []

    def test_returns_list(self):
        from agent.memory_hook import _injected_thoughts, get_injected_thoughts

        _injected_thoughts["test-session"] = [("key1", "thought1")]
        result = get_injected_thoughts("test-session")
        assert len(result) == 1
        assert result[0] == ("key1", "thought1")
        # Cleanup
        del _injected_thoughts["test-session"]


class TestClearSession:
    """Test agent/memory_hook.py clear_session()."""

    def test_clears_all_state(self):
        from agent.memory_hook import (
            _injected_thoughts,
            _tool_buffers,
            _tool_counts,
            clear_session,
        )

        session = "test-clear"
        _tool_buffers[session] = [{"tool_name": "test", "tool_input": {}}]
        _tool_counts[session] = 5
        _injected_thoughts[session] = [("k", "v")]

        clear_session(session)

        assert session not in _tool_buffers
        assert session not in _tool_counts
        assert session not in _injected_thoughts

    def test_clear_nonexistent_session(self):
        from agent.memory_hook import clear_session

        # Should not raise
        clear_session("nonexistent-clear-session")
