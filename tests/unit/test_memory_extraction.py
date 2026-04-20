"""Unit tests for post-session memory extraction and outcome detection."""

import pytest


def _make_async_anthropic_mock(mock_message):
    """Build a MagicMock that mimics anthropic.AsyncAnthropic for `async with` (hotfix #1055).

    Returns a ``MagicMock`` instance that:
      * supports ``async with AsyncAnthropic(...) as client:`` (__aenter__/__aexit__)
      * exposes ``client.messages.create`` as an ``AsyncMock`` returning ``mock_message``
    """
    from unittest.mock import AsyncMock, MagicMock

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.messages.create = AsyncMock(return_value=mock_message)
    return mock_client


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
        from unittest.mock import MagicMock, patch

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

        mock_client = _make_async_anthropic_mock(mock_message)

        mock_memory = MagicMock()
        mock_memory.safe_save.return_value = MagicMock(memory_id="test-id")

        with (
            patch("anthropic.AsyncAnthropic", return_value=mock_client),
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

        mock_client = _make_async_anthropic_mock(mock_message)

        mock_memory = MagicMock()
        mock_memory.safe_save.return_value = MagicMock(memory_id="test-id")

        with (
            patch("anthropic.AsyncAnthropic", return_value=mock_client),
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

        mock_client = _make_async_anthropic_mock(mock_message)

        mock_memory = MagicMock()
        mock_memory.safe_save.return_value = MagicMock(memory_id="test-id")

        with (
            patch("anthropic.AsyncAnthropic", return_value=mock_client),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("models.memory.Memory", mock_memory),
            patch("models.memory.SOURCE_AGENT", "agent"),
        ):
            result = await extract_post_merge_learning("Add recall weights", "Description", "diff")

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


class TestJudgeOutcomesLlm:
    """Test agent/memory_extraction.py _judge_outcomes_llm().

    hotfix #1055: _judge_outcomes_llm is async-native (AsyncAnthropic). All
    tests use @pytest.mark.asyncio and await the call.
    """

    @pytest.mark.asyncio
    async def test_parses_valid_llm_response(self):
        import json
        from unittest.mock import MagicMock, patch

        from agent.memory_extraction import _judge_outcomes_llm

        llm_response = json.dumps(
            [
                {
                    "index": 0,
                    "outcome": "acted",
                    "reasoning": "Response used the deployment strategy.",
                },
                {"index": 1, "outcome": "dismissed", "reasoning": "No relationship found."},
            ]
        )

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=llm_response)]
        mock_client = _make_async_anthropic_mock(mock_message)

        with (
            patch("anthropic.AsyncAnthropic", return_value=mock_client),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await _judge_outcomes_llm(
                [("key1", "use blue-green deployment"), ("key2", "kubernetes config")],
                "We deployed using blue-green strategy.",
            )

        assert result is not None
        assert result["key1"]["outcome"] == "acted"
        assert result["key2"]["outcome"] == "dismissed"
        assert "deployment" in result["key1"]["reasoning"]

    @pytest.mark.asyncio
    async def test_echoed_maps_to_dismissed(self):
        import json
        from unittest.mock import MagicMock, patch

        from agent.memory_extraction import _judge_outcomes_llm

        llm_response = json.dumps(
            [
                {
                    "index": 0,
                    "outcome": "echoed",
                    "reasoning": "Keywords overlap but no causal link.",
                },
            ]
        )

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=llm_response)]
        mock_client = _make_async_anthropic_mock(mock_message)

        with (
            patch("anthropic.AsyncAnthropic", return_value=mock_client),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await _judge_outcomes_llm(
                [("key1", "redis connection pooling")],
                "Redis connections are managed via pooling.",
            )

        assert result is not None
        assert result["key1"]["outcome"] == "dismissed"

    @pytest.mark.asyncio
    async def test_returns_none_on_api_failure(self):
        from unittest.mock import patch

        from agent.memory_extraction import _judge_outcomes_llm

        with patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"):
            with patch("anthropic.AsyncAnthropic", side_effect=Exception("API down")):
                result = await _judge_outcomes_llm(
                    [("key1", "some thought")],
                    "some response",
                )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_api_key(self):
        from unittest.mock import patch

        from agent.memory_extraction import _judge_outcomes_llm

        with patch("utils.api_keys.get_anthropic_api_key", return_value=None):
            result = await _judge_outcomes_llm(
                [("key1", "some thought")],
                "some response",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_fills_missing_thoughts(self):
        """Thoughts not covered by LLM response get dismissed by default."""
        import json
        from unittest.mock import MagicMock, patch

        from agent.memory_extraction import _judge_outcomes_llm

        # LLM only returns judgment for index 0, not index 1
        llm_response = json.dumps(
            [
                {"index": 0, "outcome": "acted", "reasoning": "Influenced the response."},
            ]
        )

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=llm_response)]
        mock_client = _make_async_anthropic_mock(mock_message)

        with (
            patch("anthropic.AsyncAnthropic", return_value=mock_client),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await _judge_outcomes_llm(
                [("key1", "thought one"), ("key2", "thought two")],
                "response text",
            )

        assert result is not None
        assert result["key1"]["outcome"] == "acted"
        assert result["key2"]["outcome"] == "dismissed"

    @pytest.mark.asyncio
    async def test_caps_at_max_thoughts(self):
        """Only first 5 thoughts are sent to the LLM."""
        import json
        from unittest.mock import MagicMock, patch

        from agent.memory_extraction import _OUTCOME_MAX_THOUGHTS, _judge_outcomes_llm

        # Create 7 thoughts
        thoughts = [(f"key{i}", f"thought number {i} with enough text") for i in range(7)]

        llm_response = json.dumps(
            [
                {"index": i, "outcome": "acted", "reasoning": "yes"}
                for i in range(_OUTCOME_MAX_THOUGHTS)
            ]
        )

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=llm_response)]
        mock_client = _make_async_anthropic_mock(mock_message)

        with (
            patch("anthropic.AsyncAnthropic", return_value=mock_client),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await _judge_outcomes_llm(thoughts, "response text")

        assert result is not None
        # Only the first 5 should be in the result
        assert len(result) == _OUTCOME_MAX_THOUGHTS
        assert "key5" not in result
        assert "key6" not in result

    @pytest.mark.asyncio
    async def test_invalid_json_returns_none(self):
        from unittest.mock import MagicMock, patch

        from agent.memory_extraction import _judge_outcomes_llm

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="not valid json at all")]
        mock_client = _make_async_anthropic_mock(mock_message)

        with (
            patch("anthropic.AsyncAnthropic", return_value=mock_client),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await _judge_outcomes_llm(
                [("key1", "thought")],
                "response",
            )

        assert result is None


class TestComputeActRate:
    """Test agent/memory_extraction.py compute_act_rate()."""

    def test_empty_history(self):
        from agent.memory_extraction import compute_act_rate

        assert compute_act_rate([]) is None

    def test_all_acted(self):
        from agent.memory_extraction import compute_act_rate

        history = [{"outcome": "acted"}, {"outcome": "acted"}]
        assert compute_act_rate(history) == 1.0

    def test_all_dismissed(self):
        from agent.memory_extraction import compute_act_rate

        history = [{"outcome": "dismissed"}, {"outcome": "dismissed"}]
        assert compute_act_rate(history) == 0.0

    def test_mixed(self):
        from agent.memory_extraction import compute_act_rate

        history = [
            {"outcome": "acted"},
            {"outcome": "dismissed"},
            {"outcome": "acted"},
            {"outcome": "dismissed"},
        ]
        assert compute_act_rate(history) == 0.5

    def test_single_entry(self):
        from agent.memory_extraction import compute_act_rate

        assert compute_act_rate([{"outcome": "acted"}]) == 1.0
        assert compute_act_rate([{"outcome": "dismissed"}]) == 0.0


class TestOutcomeHistory:
    """Test outcome_history persistence in _persist_outcome_metadata()."""

    def test_appends_to_outcome_history(self):
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = {}
        m.importance = 2.0

        _persist_outcome_metadata([m], {"mem1": "acted"}, {"mem1": "Response used the strategy"})

        history = m.metadata["outcome_history"]
        assert len(history) == 1
        assert history[0]["outcome"] == "acted"
        assert history[0]["reasoning"] == "Response used the strategy"
        assert "ts" in history[0]

    def test_caps_at_max_history(self):
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata
        from config.memory_defaults import MAX_OUTCOME_HISTORY

        m = MagicMock()
        m.memory_id = "mem1"
        # Pre-fill with MAX entries
        m.metadata = {
            "outcome_history": [
                {"outcome": "dismissed", "reasoning": "", "ts": i}
                for i in range(MAX_OUTCOME_HISTORY)
            ]
        }
        m.importance = 2.0

        _persist_outcome_metadata([m], {"mem1": "acted"}, {"mem1": "new entry"})

        history = m.metadata["outcome_history"]
        assert len(history) == MAX_OUTCOME_HISTORY
        # The newest entry should be last
        assert history[-1]["outcome"] == "acted"
        assert history[-1]["reasoning"] == "new entry"
        # The oldest entry (ts=0) should have been dropped
        assert history[0]["ts"] == 1

    def test_backward_compatible_no_history(self):
        """Old memories without outcome_history get it initialized."""
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = {"dismissal_count": 1, "last_outcome": "dismissed"}
        m.importance = 2.0

        _persist_outcome_metadata([m], {"mem1": "dismissed"})

        assert "outcome_history" in m.metadata
        assert len(m.metadata["outcome_history"]) == 1
        assert m.metadata["outcome_history"][0]["outcome"] == "dismissed"

    def test_reasoning_defaults_to_empty_string(self):
        """When no reasoning_map provided, reasoning is empty string."""
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = {}
        m.importance = 2.0

        _persist_outcome_metadata([m], {"mem1": "acted"})

        history = m.metadata["outcome_history"]
        assert history[0]["reasoning"] == ""

    def test_corrupted_history_gets_reset(self):
        """Non-list outcome_history is replaced with fresh list."""
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = {"outcome_history": "corrupted"}
        m.importance = 2.0

        _persist_outcome_metadata([m], {"mem1": "acted"})

        history = m.metadata["outcome_history"]
        assert isinstance(history, list)
        assert len(history) == 1


class TestPersonaPromptContainsIntentionalMemory:
    """Verify the base persona prompt includes intentional memory instructions."""

    def test_persona_has_intentional_memory_section(self):
        import pathlib

        persona_path = pathlib.Path("config/personas/segments/work-patterns.md")
        content = persona_path.read_text()
        assert "## Intentional Memory" in content

    def test_persona_has_save_examples(self):
        import pathlib

        persona_path = pathlib.Path("config/personas/segments/work-patterns.md")
        content = persona_path.read_text()
        assert "memory_search save" in content
        assert "importance 8.0" in content or "--importance 8.0" in content

    def test_persona_has_trigger_categories(self):
        import pathlib

        persona_path = pathlib.Path("config/personas/segments/work-patterns.md")
        content = persona_path.read_text()
        assert "User corrections" in content or "user corrections" in content.lower()
        assert "remember this" in content.lower()
        assert "Architectural decisions" in content or "architectural decisions" in content.lower()

    def test_persona_has_when_not_to_save(self):
        import pathlib

        persona_path = pathlib.Path("config/personas/segments/work-patterns.md")
        content = persona_path.read_text()
        assert "When NOT to Save" in content

    def test_persona_has_when_to_search(self):
        import pathlib

        persona_path = pathlib.Path("config/personas/segments/work-patterns.md")
        content = persona_path.read_text()
        assert "When to Search" in content
        assert "--category correction" in content
        assert "--tag" in content


# -----------------------------------------------------------------------------
# Hotfix #1055 — event-loop safety guards for async Anthropic extraction.
# -----------------------------------------------------------------------------


class TestEventLoopSafety:
    """Verify memory_extraction never blocks the worker event loop (hotfix #1055).

    Tests model the exact failure mode observed in #1055: a half-open TCP
    socket where the sync client never returns. We stub AsyncAnthropic with a
    ``messages.create`` coroutine that calls ``time.sleep(40)`` (REAL sync
    block, not ``asyncio.sleep`` — cooperative suspension does not model the
    observed failure).
    """

    @staticmethod
    def _make_hung_async_anthropic_mock():
        """Build an AsyncAnthropic mock whose messages.create internally ``time.sleep(40)``."""
        from unittest.mock import AsyncMock, MagicMock

        async def _hung_create(*args, **kwargs):
            import time

            time.sleep(40)  # REAL sync block — models half-open TCP socket stall

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.messages.create = _hung_create
        return mock_client

    @pytest.mark.asyncio
    async def test_hard_timeout_caught_and_logged_extract_observations(self, caplog):
        """extract_observations_async times out within ~35s on hung client."""
        import logging
        import time
        from unittest.mock import patch

        import agent.memory_extraction as ext

        caplog.set_level(logging.WARNING, logger="agent.memory_extraction")

        mock_client = self._make_hung_async_anthropic_mock()

        # Tighten the outer hard timeout so the test runs in <2s while still
        # exercising the asyncio.TimeoutError path. The sleep(40) inside the
        # stubbed create() is synchronous and will be cancelled by the
        # asyncio event loop when wait_for fires — except the coroutine's
        # body is still blocking a thread. To keep the test fast while still
        # hitting the TimeoutError branch, we mock asyncio.wait_for to raise
        # TimeoutError immediately.
        async def _raise_timeout(coro_or_awaitable=None, *a, **kw):
            # Close the unwawaited coroutine to suppress RuntimeWarning, then
            # raise TimeoutError to exercise the outer branch.
            if coro_or_awaitable is not None and hasattr(coro_or_awaitable, "close"):
                coro_or_awaitable.close()
            raise __import__("asyncio").TimeoutError

        # Capture analytics counter calls
        recorded = []

        def _stub_record(name, value, tags):
            recorded.append((name, tags))

        with (
            patch("anthropic.AsyncAnthropic", return_value=mock_client),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("asyncio.wait_for", side_effect=_raise_timeout),
            patch("analytics.collector.record_metric", side_effect=_stub_record),
        ):
            start = time.time()
            result = await ext.extract_observations_async(
                "sess-test",
                "A" * 200,  # >50 chars to pass the short-circuit guard
                project_key="test-proj",
            )
            elapsed = time.time() - start

        assert result == [], "extract_observations_async must return [] on timeout"
        assert elapsed < 5.0, f"hard-timeout path must return quickly (got {elapsed:.2f}s)"
        assert any("hard timeout" in rec.message.lower() for rec in caplog.records), (
            "must log WARNING with 'hard timeout' wording"
        )
        assert any(
            name == "memory.extraction.error" and tags.get("error_class") == "timeouterror"
            for name, tags in recorded
        ), f"must emit memory.extraction.error with error_class=timeouterror (got {recorded})"

    @pytest.mark.asyncio
    async def test_hard_timeout_caught_and_logged_detect_outcomes(self, caplog):
        """detect_outcomes_async (via _judge_outcomes_llm) times out gracefully."""
        import logging
        import time
        from unittest.mock import patch

        import agent.memory_extraction as ext

        caplog.set_level(logging.WARNING, logger="agent.memory_extraction")

        mock_client = self._make_hung_async_anthropic_mock()

        async def _raise_timeout(coro_or_awaitable=None, *a, **kw):
            # Close the unwawaited coroutine to suppress RuntimeWarning, then
            # raise TimeoutError to exercise the outer branch.
            if coro_or_awaitable is not None and hasattr(coro_or_awaitable, "close"):
                coro_or_awaitable.close()
            raise __import__("asyncio").TimeoutError

        recorded = []

        def _stub_record(name, value, tags):
            recorded.append((name, tags))

        with (
            patch("anthropic.AsyncAnthropic", return_value=mock_client),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("asyncio.wait_for", side_effect=_raise_timeout),
            patch("analytics.collector.record_metric", side_effect=_stub_record),
        ):
            start = time.time()
            # detect_outcomes_async first tries LLM; on LLM timeout, falls back
            # to bigram. We want to confirm: (a) the TimeoutError does NOT
            # propagate, (b) the counter is recorded, (c) the fallback still
            # returns something sensible.
            result = await ext.detect_outcomes_async(
                [("key1", "some thought content text goes here")],
                "some response text that mentions different topics entirely",
            )
            elapsed = time.time() - start

        assert isinstance(result, dict), "detect_outcomes_async must never raise on timeout"
        assert elapsed < 5.0, f"hard-timeout path must return quickly (got {elapsed:.2f}s)"
        assert any(
            name == "memory.extraction.error" and tags.get("error_class") == "timeouterror"
            for name, tags in recorded
        ), f"must emit memory.extraction.error with error_class=timeouterror (got {recorded})"

    @pytest.mark.asyncio
    async def test_hard_timeout_caught_and_logged_post_merge_learning(self, caplog):
        """extract_post_merge_learning times out gracefully."""
        import logging
        import time
        from unittest.mock import patch

        import agent.memory_extraction as ext

        caplog.set_level(logging.WARNING, logger="agent.memory_extraction")

        mock_client = self._make_hung_async_anthropic_mock()

        async def _raise_timeout(coro_or_awaitable=None, *a, **kw):
            # Close the unwawaited coroutine to suppress RuntimeWarning, then
            # raise TimeoutError to exercise the outer branch.
            if coro_or_awaitable is not None and hasattr(coro_or_awaitable, "close"):
                coro_or_awaitable.close()
            raise __import__("asyncio").TimeoutError

        recorded = []

        def _stub_record(name, value, tags):
            recorded.append((name, tags))

        with (
            patch("anthropic.AsyncAnthropic", return_value=mock_client),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("asyncio.wait_for", side_effect=_raise_timeout),
            patch("analytics.collector.record_metric", side_effect=_stub_record),
        ):
            start = time.time()
            result = await ext.extract_post_merge_learning(
                "PR Title",
                "PR body content",
                "diff summary",
            )
            elapsed = time.time() - start

        assert result is None, "extract_post_merge_learning must return None on timeout"
        assert elapsed < 5.0, f"hard-timeout path must return quickly (got {elapsed:.2f}s)"
        assert any("hard timeout" in rec.message.lower() for rec in caplog.records), (
            "must log WARNING with 'hard timeout' wording"
        )
        assert any(
            name == "memory.extraction.error" and tags.get("error_class") == "timeouterror"
            for name, tags in recorded
        ), f"must emit memory.extraction.error with error_class=timeouterror (got {recorded})"

    @pytest.mark.asyncio
    async def test_real_asyncio_wait_for_fires_with_tightened_constants(self, caplog):
        """Exercise the real asyncio.wait_for with tightened constants (hotfix #1055 review nit).

        The three tests above patch ``asyncio.wait_for`` directly to raise
        ``TimeoutError`` immediately, which verifies the ``except asyncio.TimeoutError``
        code path fires and the counter is recorded — but does NOT prove that
        ``asyncio.wait_for`` is still wired in. A future refactor that silently
        removes the ``wait_for`` wrapper would pass those three tests.

        This test tightens ``_EXTRACTION_SDK_TIMEOUT`` to 0.05s and
        ``_EXTRACTION_HARD_TIMEOUT`` to 0.1s, then lets the REAL ``asyncio.wait_for``
        run against a cooperatively-hung ``AsyncAnthropic`` stub. If the
        production code no longer wraps the call in ``asyncio.wait_for``, the
        test will hang past its 2s assertion budget and fail — catching the
        regression that the mocked-``wait_for`` tests miss.
        """
        import logging
        import time
        from unittest.mock import AsyncMock, MagicMock, patch

        import agent.memory_extraction as ext

        caplog.set_level(logging.WARNING, logger="agent.memory_extraction")

        # Cooperative hang (asyncio.sleep, not time.sleep) so the REAL
        # asyncio.wait_for can cancel the inner coroutine at its await point
        # — production flows through AsyncAnthropic + httpx which are
        # cancellable the same way.
        async def _cooperative_hang(*args, **kwargs):
            import asyncio as _asyncio

            await _asyncio.sleep(10)  # long enough to always exceed 0.1s

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.messages.create = _cooperative_hang

        recorded = []

        def _stub_record(name, value, tags):
            recorded.append((name, tags))

        with (
            patch("anthropic.AsyncAnthropic", return_value=mock_client),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("analytics.collector.record_metric", side_effect=_stub_record),
            # Tighten the module constants — the REAL asyncio.wait_for runs
            # against these, NOT a mock. If the wrapper is removed, the test
            # hangs past its 2s budget.
            patch.object(ext, "_EXTRACTION_SDK_TIMEOUT", 0.05),
            patch.object(ext, "_EXTRACTION_HARD_TIMEOUT", 0.1),
        ):
            start = time.time()
            result = await ext.extract_observations_async(
                "sess-real-timeout",
                "A" * 200,
                project_key="test-proj",
            )
            elapsed = time.time() - start

        assert result == [], "extract_observations_async must return [] on real timeout"
        assert elapsed < 2.0, (
            f"real asyncio.wait_for must fire within ~0.1s + overhead (got {elapsed:.2f}s). "
            "If elapsed >> 2s, the production code likely no longer wraps "
            "messages.create in asyncio.wait_for — hotfix #1055 regression."
        )
        assert elapsed >= 0.05, (
            f"elapsed ({elapsed:.2f}s) is suspiciously short — did asyncio.wait_for "
            "short-circuit? The test must actually exercise the timeout wait."
        )
        assert any(
            name == "memory.extraction.error" and tags.get("error_class") == "timeouterror"
            for name, tags in recorded
        ), f"must emit memory.extraction.error with error_class=timeouterror (got {recorded})"

    @pytest.mark.asyncio
    async def test_sdk_api_timeout_caught_and_logged(self):
        """anthropic.APITimeoutError is caught by outer except Exception and counter recorded."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import anthropic

        import agent.memory_extraction as ext

        # APITimeoutError is raised inside messages.create
        async def _raise_api_timeout(*args, **kwargs):
            # The public constructor signature varies between SDK versions;
            # construct via __new__ to avoid coupling to arg shape.
            try:
                raise anthropic.APITimeoutError(request=MagicMock())
            except TypeError:
                # Older SDK signature: may need a message arg
                raise anthropic.APITimeoutError("simulated timeout")

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.messages.create = _raise_api_timeout

        recorded = []

        def _stub_record(name, value, tags):
            recorded.append((name, tags))

        with (
            patch("anthropic.AsyncAnthropic", return_value=mock_client),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("analytics.collector.record_metric", side_effect=_stub_record),
        ):
            result = await ext.extract_observations_async(
                "sess-api-timeout",
                "A" * 200,
                project_key="test-proj",
            )

        assert result == [], "APITimeoutError must not crash extract_observations_async"
        # Outer except Exception catches the SDK APITimeoutError and records
        # the metric with error_class from type(e).__name__.
        assert any(
            name == "memory.extraction.error" and tags.get("error_class") == "apitimeouterror"
            for name, tags in recorded
        ), f"must emit memory.extraction.error with error_class=apitimeouterror (got {recorded})"

    def test_no_sync_anthropic_client_grep_canary(self):
        """Regression canary: no sync anthropic.Anthropic( calls in memory_extraction."""
        import subprocess

        result = subprocess.run(
            ["grep", "-n", "anthropic\\.Anthropic(", "agent/memory_extraction.py"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, (
            "No sync anthropic.Anthropic( calls allowed in agent/memory_extraction.py — "
            "use anthropic.AsyncAnthropic( with asyncio.wait_for (see hotfix #1055). "
            f"Offending lines:\n{result.stdout}"
        )


def test_extract_post_merge_learning_runs_inside_asyncio_run(monkeypatch):
    """Guards the .claude hook subprocess call path (hotfix #1055, nit 3).

    Hook at .claude/hooks/hook_utils/memory_bridge.py::post_merge_extract()
    calls asyncio.run(extract_post_merge_learning(...)) inside a short-lived
    subprocess. Converting the function to AsyncAnthropic must NOT introduce
    a nested ``asyncio.run()`` (which raises RuntimeError: This event loop is
    already running). This test runs the function via asyncio.run with a
    minimal valid AsyncAnthropic mock and asserts no such error is raised.

    See docs/plans/agent_wiki.md:157 for the regression class this guards.
    """
    import asyncio
    import json

    from agent.memory_extraction import extract_post_merge_learning

    json_response = json.dumps(
        {
            "observation": "Use dependency injection for testability in hooks",
            "category": "pattern",
            "tags": ["testing", "hooks"],
            "file_paths": ["hooks/example.py"],
        }
    )

    mock_message_content = [type("Block", (), {"text": json_response})()]
    mock_message = type("Message", (), {"content": mock_message_content})()
    mock_client = _make_async_anthropic_mock(mock_message)

    # Also mock Memory.safe_save so we don't touch Redis from a subprocess-like test
    from unittest.mock import MagicMock, patch

    mock_memory_module = MagicMock()
    mock_memory_module.safe_save.return_value = MagicMock(memory_id="mock-mem-id")

    with (
        patch("anthropic.AsyncAnthropic", return_value=mock_client),
        patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        patch("models.memory.Memory", mock_memory_module),
        patch("models.memory.SOURCE_AGENT", "agent"),
    ):
        # asyncio.run is the entry point used by .claude/hooks/hook_utils/memory_bridge.py.
        # If the new AsyncAnthropic path inadvertently nested asyncio.run, this would
        # raise "RuntimeError: This event loop is already running".
        result = asyncio.run(
            extract_post_merge_learning(
                "PR title",
                "PR body content longer than twenty chars",
                "files_changed.py",
            )
        )

    # Result may be a dict (memory saved) or None — the critical assertion is that
    # asyncio.run did not raise. Accept either outcome.
    assert result is None or isinstance(result, dict)
