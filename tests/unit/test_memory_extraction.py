"""Unit tests for post-session memory extraction and outcome detection."""

import pytest


class TestExtractBigrams:
    """Test agent/memory_extraction.py _extract_bigrams()."""

    def test_extracts_unigrams(self):
        from agent.memory_extraction import _extract_bigrams

        bigrams = _extract_bigrams("deploy rollback strategy")
        assert ("deploy",) in bigrams
        assert ("rollback",) in bigrams
        assert ("strategy",) in bigrams

    def test_extracts_bigrams(self):
        from agent.memory_extraction import _extract_bigrams

        bigrams = _extract_bigrams("deploy rollback strategy")
        assert ("deploy", "rollback") in bigrams
        assert ("rollback", "strategy") in bigrams

    def test_filters_short_words(self):
        from agent.memory_extraction import _extract_bigrams

        bigrams = _extract_bigrams("the big cat sat on a mat")
        # "the", "big", "cat", "sat" are all < 4 chars, filtered out
        assert ("the",) not in bigrams
        assert ("cat",) not in bigrams

    def test_empty_text(self):
        from agent.memory_extraction import _extract_bigrams

        bigrams = _extract_bigrams("")
        assert len(bigrams) == 0

    def test_case_insensitive(self):
        from agent.memory_extraction import _extract_bigrams

        bigrams = _extract_bigrams("Deploy ROLLBACK Strategy")
        assert ("deploy",) in bigrams
        assert ("rollback",) in bigrams


class TestDetectOutcomes:
    """Test agent/memory_extraction.py detect_outcomes_async()."""

    @pytest.mark.asyncio
    async def test_empty_thoughts(self):
        from agent.memory_extraction import detect_outcomes_async

        result = await detect_outcomes_async([], "some response text")
        assert result == {}

    @pytest.mark.asyncio
    async def test_empty_response(self):
        from agent.memory_extraction import detect_outcomes_async

        result = await detect_outcomes_async([("key1", "deployment strategy")], "")
        assert result == {}

    @pytest.mark.asyncio
    async def test_acted_on_overlap(self):
        from agent.memory_extraction import detect_outcomes_async

        thoughts = [("key1", "deployment strategy uses blue green")]
        response = "We use a blue green deployment strategy with rollback"

        result = await detect_outcomes_async(thoughts, response)
        assert result.get("key1") == "acted"

    @pytest.mark.asyncio
    async def test_dismissed_no_overlap(self):
        from agent.memory_extraction import detect_outcomes_async

        thoughts = [("key1", "kubernetes helm charts")]
        response = "The database migration completed successfully with zero downtime"

        result = await detect_outcomes_async(thoughts, response)
        assert result.get("key1") == "dismissed"

    @pytest.mark.asyncio
    async def test_never_crashes(self):
        from agent.memory_extraction import detect_outcomes_async

        # Bad inputs should not raise
        result = await detect_outcomes_async([("", "")], "test")
        assert isinstance(result, dict)


class TestRunPostSessionExtraction:
    """Test agent/memory_extraction.py run_post_session_extraction()."""

    @pytest.mark.asyncio
    async def test_short_response_skips(self):
        from agent.memory_extraction import extract_observations_async

        result = await extract_observations_async("test", "short")
        assert result == []

    @pytest.mark.asyncio
    async def test_never_crashes(self):
        from agent.memory_extraction import run_post_session_extraction

        # Should not raise even with bad session
        await run_post_session_extraction("nonexistent", "some text")


class TestParseCategorizedObservations:
    """Test agent/memory_extraction.py _parse_categorized_observations()."""

    def test_parses_correction_category(self):
        from agent.memory_extraction import CATEGORY_IMPORTANCE, _parse_categorized_observations

        raw = "CORRECTION: Redis SCAN is preferred over KEYS in production for large keyspaces"
        result = _parse_categorized_observations(raw)
        assert len(result) == 1
        content, importance, metadata = result[0]
        assert "Redis SCAN" in content
        assert importance == CATEGORY_IMPORTANCE["correction"]
        assert isinstance(metadata, dict)

    def test_parses_decision_category(self):
        from agent.memory_extraction import CATEGORY_IMPORTANCE, _parse_categorized_observations

        raw = "DECISION: chose blue-green deployment over rolling updates for zero-downtime"
        result = _parse_categorized_observations(raw)
        assert len(result) == 1
        assert result[0][1] == CATEGORY_IMPORTANCE["decision"]
        assert isinstance(result[0][2], dict)

    def test_parses_pattern_category(self):
        from agent.memory_extraction import CATEGORY_IMPORTANCE, _parse_categorized_observations

        raw = "PATTERN: all Popoto models use safe_save as the primary entry point for creation"
        result = _parse_categorized_observations(raw)
        assert len(result) == 1
        assert result[0][1] == CATEGORY_IMPORTANCE["pattern"]

    def test_parses_surprise_category(self):
        from agent.memory_extraction import CATEGORY_IMPORTANCE, _parse_categorized_observations

        raw = "SURPRISE: the bloom filter returns false positives more often than expected"
        result = _parse_categorized_observations(raw)
        assert len(result) == 1
        assert result[0][1] == CATEGORY_IMPORTANCE["surprise"]

    def test_parses_multiple_categories(self):
        from agent.memory_extraction import CATEGORY_IMPORTANCE, _parse_categorized_observations

        raw = (
            "CORRECTION: Redis SCAN is preferred over KEYS in production\n"
            "DECISION: chose ContextAssembler for memory search over raw queries\n"
            "PATTERN: all models use safe_save as their primary entry point"
        )
        result = _parse_categorized_observations(raw)
        assert len(result) == 3
        assert result[0][1] == CATEGORY_IMPORTANCE["correction"]
        assert result[1][1] == CATEGORY_IMPORTANCE["decision"]
        assert result[2][1] == CATEGORY_IMPORTANCE["pattern"]

    def test_case_insensitive_category(self):
        from agent.memory_extraction import CATEGORY_IMPORTANCE, _parse_categorized_observations

        raw = "correction: Redis SCAN is preferred over KEYS in production for large keyspaces"
        result = _parse_categorized_observations(raw)
        assert len(result) == 1
        assert result[0][1] == CATEGORY_IMPORTANCE["correction"]

    def test_fallback_uncategorized(self):
        from agent.memory_extraction import (
            DEFAULT_CATEGORY_IMPORTANCE,
            _parse_categorized_observations,
        )

        raw = "The deployment uses blue-green strategy for zero downtime"
        result = _parse_categorized_observations(raw)
        assert len(result) == 1
        assert result[0][1] == DEFAULT_CATEGORY_IMPORTANCE
        # Line-based fallback returns empty metadata
        assert result[0][2] == {}

    def test_mixed_categorized_and_uncategorized(self):
        """When some lines are categorized, uncategorized lines are dropped."""
        from agent.memory_extraction import _parse_categorized_observations

        raw = (
            "CORRECTION: Redis SCAN is preferred over KEYS in production\n"
            "Some uncategorized observation that should be dropped"
        )
        result = _parse_categorized_observations(raw)
        # Only the categorized line should be returned
        assert len(result) == 1
        assert "Redis SCAN" in result[0][0]

    def test_empty_input(self):
        from agent.memory_extraction import _parse_categorized_observations

        assert _parse_categorized_observations("") == []

    def test_none_response(self):
        from agent.memory_extraction import _parse_categorized_observations

        assert _parse_categorized_observations("NONE") == []

    def test_short_content_after_category_filtered(self):
        from agent.memory_extraction import _parse_categorized_observations

        # Content after category prefix is too short (< 10 chars)
        raw = "CORRECTION: short"
        result = _parse_categorized_observations(raw)
        assert len(result) == 0

    def test_json_array_parsing(self):
        """JSON array input is parsed with full metadata."""
        import json

        from agent.memory_extraction import CATEGORY_IMPORTANCE, _parse_categorized_observations

        raw = json.dumps(
            [
                {
                    "category": "correction",
                    "observation": "Redis SCAN is preferred over KEYS in production",
                    "file_paths": ["bridge/telegram_bridge.py"],
                    "tags": ["redis", "performance"],
                }
            ]
        )
        result = _parse_categorized_observations(raw)
        assert len(result) == 1
        content, importance, metadata = result[0]
        assert "Redis SCAN" in content
        assert importance == CATEGORY_IMPORTANCE["correction"]
        assert metadata["category"] == "correction"
        assert metadata["file_paths"] == ["bridge/telegram_bridge.py"]
        assert metadata["tags"] == ["redis", "performance"]

    def test_json_bare_dict_wrapped_in_list(self):
        """A single JSON object (not array) is handled gracefully."""
        import json

        from agent.memory_extraction import _parse_categorized_observations

        raw = json.dumps(
            {
                "category": "decision",
                "observation": "chose blue-green deployment over rolling updates",
                "file_paths": [],
                "tags": ["deployment"],
            }
        )
        result = _parse_categorized_observations(raw)
        assert len(result) == 1
        assert result[0][2]["category"] == "decision"

    def test_json_malformed_falls_back_to_line_parser(self):
        """Malformed JSON falls back to line-based parser."""
        from agent.memory_extraction import _parse_categorized_observations

        raw = '[{"category": "correction", broken json'
        # Should not raise, falls back to line-based
        result = _parse_categorized_observations(raw)
        assert isinstance(result, list)

    def test_returns_three_tuples(self):
        """All results are (content, importance, metadata) 3-tuples."""
        from agent.memory_extraction import _parse_categorized_observations

        raw = "CORRECTION: Redis SCAN is preferred over KEYS in production for large keyspaces"
        result = _parse_categorized_observations(raw)
        assert len(result) == 1
        assert len(result[0]) == 3


class TestExtractPostMergeLearning:
    """Test agent/memory_extraction.py extract_post_merge_learning()."""

    @pytest.mark.asyncio
    async def test_empty_title_returns_none(self):
        from agent.memory_extraction import extract_post_merge_learning

        result = await extract_post_merge_learning("", "body", "diff")
        assert result is None

    @pytest.mark.asyncio
    async def test_never_crashes(self):
        """Extraction should never raise, regardless of API key availability."""
        from agent.memory_extraction import extract_post_merge_learning

        # Should not raise under any circumstances
        result = await extract_post_merge_learning(
            "Add memory search tool",
            "Implements save/search/inspect/forget",
            "tools/memory_search/__init__.py",
        )
        # Result is either None (no API key / no takeaway) or a dict with memory_id
        assert result is None or (isinstance(result, dict) and "memory_id" in result)

    @pytest.mark.asyncio
    async def test_post_merge_prompt_format(self):
        """Verify the prompt template formats correctly."""
        from agent.memory_extraction import POST_MERGE_EXTRACTION_PROMPT

        formatted = POST_MERGE_EXTRACTION_PROMPT.format(
            title="Add feature X",
            body="Description of the PR",
            diff_summary="file1.py, file2.py",
        )
        assert "Add feature X" in formatted
        assert "Description of the PR" in formatted
        assert "file1.py, file2.py" in formatted

    def test_post_merge_prompt_requests_structured_json(self):
        """Verify the prompt asks for structured JSON with metadata fields."""
        from agent.memory_extraction import POST_MERGE_EXTRACTION_PROMPT

        assert "category" in POST_MERGE_EXTRACTION_PROMPT
        assert "tags" in POST_MERGE_EXTRACTION_PROMPT
        assert "file_paths" in POST_MERGE_EXTRACTION_PROMPT
        assert "JSON" in POST_MERGE_EXTRACTION_PROMPT


class TestPostMergeJsonParsing:
    """Test JSON parsing in extract_post_merge_learning()."""

    @pytest.mark.asyncio
    async def test_json_response_extracts_metadata(self):
        """When Haiku returns JSON, metadata is parsed and passed to safe_save."""
        import json
        from unittest.mock import AsyncMock, MagicMock, patch

        from agent.memory_extraction import extract_post_merge_learning

        json_response = json.dumps(
            {
                "observation": "Post-query re-ranking is safer than pre-query filtering",
                "category": "decision",
                "tags": ["memory", "recall"],
                "file_paths": ["agent/memory_hook.py"],
            }
        )

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=json_response)]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        mock_memory = MagicMock()
        mock_memory.safe_save.return_value = MagicMock(memory_id="test-id")

        with (
            patch("anthropic.Anthropic", return_value=mock_client),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("models.memory.Memory", mock_memory),
            patch("models.memory.SOURCE_AGENT", "agent"),
        ):
            result = await extract_post_merge_learning(
                "Add recall weights", "Description", "agent/memory_hook.py"
            )

        assert result is not None
        # Verify safe_save was called with metadata
        call_kwargs = mock_memory.safe_save.call_args[1]
        assert call_kwargs["metadata"]["category"] == "decision"
        assert call_kwargs["metadata"]["tags"] == ["memory", "recall"]
        assert call_kwargs["metadata"]["file_paths"] == ["agent/memory_hook.py"]

    @pytest.mark.asyncio
    async def test_non_json_response_uses_default_metadata(self):
        """When Haiku returns plain text, default metadata is used."""
        from unittest.mock import MagicMock, patch

        from agent.memory_extraction import extract_post_merge_learning

        mock_message = MagicMock()
        mock_message.content = [
            MagicMock(text="Post-query re-ranking is safer than pre-query filtering")
        ]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        mock_memory = MagicMock()
        mock_memory.safe_save.return_value = MagicMock(memory_id="test-id")

        with (
            patch("anthropic.Anthropic", return_value=mock_client),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("models.memory.Memory", mock_memory),
            patch("models.memory.SOURCE_AGENT", "agent"),
        ):
            result = await extract_post_merge_learning(
                "Add recall weights", "Description", "diff summary"
            )

        assert result is not None
        call_kwargs = mock_memory.safe_save.call_args[1]
        assert call_kwargs["metadata"]["category"] == "decision"

    @pytest.mark.asyncio
    async def test_json_short_observation_falls_back_to_raw(self):
        """When JSON observation is too short, falls back to raw text."""
        import json
        from unittest.mock import MagicMock, patch

        from agent.memory_extraction import extract_post_merge_learning

        json_response = json.dumps(
            {"observation": "short", "category": "pattern", "tags": [], "file_paths": []}
        )

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=json_response)]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        mock_memory = MagicMock()
        mock_memory.safe_save.return_value = MagicMock(memory_id="test-id")

        with (
            patch("anthropic.Anthropic", return_value=mock_client),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("models.memory.Memory", mock_memory),
            patch("models.memory.SOURCE_AGENT", "agent"),
        ):
            result = await extract_post_merge_learning(
                "Add recall weights", "Description", "diff"
            )

        assert result is not None
        # Should have used the raw JSON text since observation was too short
        call_kwargs = mock_memory.safe_save.call_args[1]
        assert json_response[:100] in call_kwargs["content"]


class TestPersistOutcomeMetadata:
    """Test agent/memory_extraction.py _persist_outcome_metadata()."""

    def test_dismissed_increments_count(self):
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = {}
        m.importance = 2.0

        _persist_outcome_metadata([m], {"mem1": "dismissed"})

        assert m.metadata["dismissal_count"] == 1
        assert m.metadata["last_outcome"] == "dismissed"
        m.save.assert_called_once()

    def test_acted_resets_dismissal_count(self):
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = {"dismissal_count": 2, "last_outcome": "dismissed"}
        m.importance = 2.0

        _persist_outcome_metadata([m], {"mem1": "acted"})

        assert m.metadata["dismissal_count"] == 0
        assert m.metadata["last_outcome"] == "acted"

    def test_threshold_breach_decays_importance(self):
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata
        from config.memory_defaults import DISMISSAL_DECAY_THRESHOLD

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = {"dismissal_count": DISMISSAL_DECAY_THRESHOLD - 1}
        m.importance = 2.0

        _persist_outcome_metadata([m], {"mem1": "dismissed"})

        # Should have decayed importance and reset count
        assert m.importance < 2.0
        assert m.metadata["dismissal_count"] == 0

    def test_importance_floor(self):
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata
        from config.memory_defaults import (
            DISMISSAL_DECAY_THRESHOLD,
            MIN_IMPORTANCE_FLOOR,
        )

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = {"dismissal_count": DISMISSAL_DECAY_THRESHOLD - 1}
        m.importance = 0.1  # already below floor

        _persist_outcome_metadata([m], {"mem1": "dismissed"})

        assert m.importance >= MIN_IMPORTANCE_FLOOR

    def test_save_failure_does_not_crash(self):
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = {}
        m.importance = 2.0
        m.save.side_effect = Exception("Redis connection error")

        # Should not raise
        _persist_outcome_metadata([m], {"mem1": "dismissed"})

    def test_none_metadata_defaults_to_empty_dict(self):
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = None
        m.importance = 2.0

        _persist_outcome_metadata([m], {"mem1": "dismissed"})

        assert m.metadata["dismissal_count"] == 1


class TestPersonaPromptContainsIntentionalMemory:
    """Verify the base persona prompt includes intentional memory instructions."""

    def test_persona_has_intentional_memory_section(self):
        import pathlib

        persona_path = pathlib.Path("config/personas/_base.md")
        content = persona_path.read_text()
        assert "## Intentional Memory" in content

    def test_persona_has_save_examples(self):
        import pathlib

        persona_path = pathlib.Path("config/personas/_base.md")
        content = persona_path.read_text()
        assert "memory_search save" in content
        assert "importance 8.0" in content or "--importance 8.0" in content

    def test_persona_has_trigger_categories(self):
        import pathlib

        persona_path = pathlib.Path("config/personas/_base.md")
        content = persona_path.read_text()
        assert "User corrections" in content or "user corrections" in content.lower()
        assert "remember this" in content.lower()
        assert "Architectural decisions" in content or "architectural decisions" in content.lower()

    def test_persona_has_when_not_to_save(self):
        import pathlib

        persona_path = pathlib.Path("config/personas/_base.md")
        content = persona_path.read_text()
        assert "When NOT to Save" in content

    def test_persona_has_when_to_search(self):
        import pathlib

        persona_path = pathlib.Path("config/personas/_base.md")
        content = persona_path.read_text()
        assert "When to Search" in content
        assert "--category correction" in content
        assert "--tag" in content
