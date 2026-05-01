"""Test that tools.memory_search.search() returns titles in result dicts.

Regression test for issue #1178 cycle-3 review: search() previously omitted
the `title` field, causing the MCP `memory_search` tool to render every
stub as `[category]`-only — silently neutering the active-search half of
progressive disclosure.
"""

from unittest.mock import MagicMock, patch


class TestSearchReturnsTitle:
    def test_search_result_dicts_include_title_field(self):
        """search() must include 'title' in each result dict so MCP stubs render correctly."""
        # Mock the Memory record before the bloom check by patching might_exist
        # to always return True (so bloom passes), and mock retrieve_memories.
        mock_record = MagicMock()
        mock_record.content = "Don't use raw Redis on Popoto-managed keys"
        mock_record.title = "Avoid raw Redis on Popoto keys"
        mock_record.score = 0.85
        mock_record.confidence = 0.9
        mock_record.source = "tools/memory_search/__init__.py"
        mock_record.access_count = 3
        mock_record.memory_id = "mem_test_123"
        mock_record.metadata = {"category": "correction", "tags": ["redis"]}

        with patch("agent.memory_retrieval.retrieve_memories", return_value=[mock_record]):
            with patch("tools.memory_search._resolve_project_key", return_value=""):
                # Bypass bloom by forcing it to always-pass. The bloom field check
                # uses Memory._meta.fields.get("bloom") so we patch might_exist if present.
                from models.memory import Memory

                bloom_field = Memory._meta.fields.get("bloom")
                if bloom_field is not None:
                    with patch.object(bloom_field, "might_exist", return_value=True):
                        from tools.memory_search import search

                        result = search("redis lookup", limit=5)
                else:
                    from tools.memory_search import search

                    result = search("redis lookup", limit=5)

        assert result["results"], "search returned no results"
        first = result["results"][0]
        assert "title" in first, "search() result dict missing 'title' field"
        assert first["title"] == "Avoid raw Redis on Popoto keys"

    def test_search_result_title_none_when_record_has_no_title(self):
        """When Memory.title is None (unbacked), search should still include it as None."""
        mock_record = MagicMock()
        mock_record.content = "Some observation without a populated title"
        mock_record.title = None  # async title-gen hasn't run yet
        mock_record.score = 0.5
        mock_record.confidence = 0.7
        mock_record.source = ""
        mock_record.access_count = 0
        mock_record.memory_id = "mem_no_title"
        mock_record.metadata = {"category": "memory"}

        with patch("agent.memory_retrieval.retrieve_memories", return_value=[mock_record]):
            with patch("tools.memory_search._resolve_project_key", return_value=""):
                from models.memory import Memory

                bloom_field = Memory._meta.fields.get("bloom")
                if bloom_field is not None:
                    with patch.object(bloom_field, "might_exist", return_value=True):
                        from tools.memory_search import search

                        result = search("anything thing more", limit=5)
                else:
                    from tools.memory_search import search

                    result = search("anything thing more", limit=5)

        assert result["results"]
        assert result["results"][0]["title"] is None
