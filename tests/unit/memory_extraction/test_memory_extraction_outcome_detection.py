"""Tests for agent/memory_extraction.py: outcome detection and scoring.

Covers bigram extraction, outcome detection, act-rate computation,
outcome history, metadata persistence, and the LLM outcome judge.
Split out of the former ``tests/unit/test_memory_extraction.py`` monolith (#2879). The
``memory_extraction`` filename prefix is load-bearing: ``tests/conftest.py``
derives feature markers from the module basename via ``FEATURE_MAP``.
"""

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
    async def test_fallback_always_deferred_on_overlap(self):
        """When the LLM judge is unavailable, the bigram-overlap fallback must
        never emit "acted" -- even when the thought and response share
        keywords. A cheap heuristic must not manufacture positive
        corroboration for the confidence-learning signal (precision over
        recall). Only the LLM judge may emit "acted"."""
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import detect_outcomes_async

        thoughts = [("key1", "deployment strategy uses blue green")]
        response = "We use a blue green deployment strategy with rollback"

        with patch(
            "agent.memory_extraction._judge_outcomes_llm",
            new=AsyncMock(return_value=None),
        ):
            result = await detect_outcomes_async(thoughts, response)

        assert result.get("key1") == "deferred"

    @pytest.mark.asyncio
    async def test_fallback_always_deferred_without_overlap(self):
        """The fallback must also never emit "dismissed" -- absence of
        keyword overlap is not evidence the memory was unused. Both
        directions resolve to the neutral "deferred" outcome."""
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import detect_outcomes_async

        thoughts = [("key1", "kubernetes helm charts")]
        response = "The database migration completed successfully with zero downtime"

        with patch(
            "agent.memory_extraction._judge_outcomes_llm",
            new=AsyncMock(return_value=None),
        ):
            result = await detect_outcomes_async(thoughts, response)

        assert result.get("key1") == "deferred"

    @pytest.mark.asyncio
    async def test_never_crashes(self):
        from agent.memory_extraction import detect_outcomes_async

        # Bad inputs should not raise
        result = await detect_outcomes_async([("", "")], "test")
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_used_outcome_not_remapped(self):
        """'used' outcome from LLM judge must survive coercion guard unchanged.

        Tests that the popoto v1.5.0 'used' outcome (consumed but did not drive
        the response) passes through detect_outcomes_async without being coerced
        to 'dismissed'.
        """
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import detect_outcomes_async

        memory_key = "test-memory-used-123"
        # Simulate _judge_outcomes_llm returning "used" for the memory
        mock_llm_result = {
            memory_key: {
                "outcome": "used",
                "reasoning": "Agent read the memory but did not use it to drive the response",
            }
        }

        with patch(
            "agent.memory_extraction._judge_outcomes_llm",
            new=AsyncMock(return_value=mock_llm_result),
        ):
            thoughts = [(memory_key, "deployment pipeline red-green canary strategy")]
            result = await detect_outcomes_async(thoughts, "The weather is nice today.")

        # "used" must not be coerced to "dismissed"
        assert result.get(memory_key) == "used", (
            f"Expected 'used' outcome to survive coercion guard, got: {result.get(memory_key)!r}"
        )


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

    def test_deferred_leaves_dismissal_count_unchanged(self):
        """The 'deferred' outcome (fallback-unavailable or orphaned-sidecar
        resolution) must be a no-op with respect to dismissal_count -- it
        neither resets it (would manufacture a false positive) nor
        increments it (would manufacture a false negative)."""
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = {"dismissal_count": 2}
        m.importance = 2.0

        _persist_outcome_metadata([m], {"mem1": "deferred"})

        assert m.metadata["dismissal_count"] == 2
        assert m.metadata["last_outcome"] == "deferred"
        m.save.assert_called_once()

    def test_dismissal_prune_supersedes_floored_zero_act_accessed_record(self):
        """CONCERN 3b (issue #2203): the dismissal-dominated corpus exit.

        A previously-accessed record (access_count > 0) is excluded from BOTH
        decay-prune tiers (they require access_count == 0), and MIN_IMPORTANCE_FLOOR
        (0.2) sits above the 0.15 write floor, so dismissal decay floors it at 0.2
        and it never enters tier-1's < 0.15 band -- it has no prune exit through the
        reflection. When one more `dismissed` decays such a record that is ALREADY
        at the floor with a 0% act rate over >= DISMISSAL_DECAY_THRESHOLD outcomes,
        it is superseded directly and prune_count is incremented (this is the
        corpus exit for the "Ahhh"-class record: recalled, dismissed, floored)."""
        from unittest.mock import MagicMock

        from popoto.redis_db import POPOTO_REDIS_DB as _R

        from agent.memory_extraction import _persist_outcome_metadata
        from config.memory_defaults import (
            DEFAULT_PROJECT_KEY,
            DISMISSAL_DECAY_THRESHOLD,
            MIN_IMPORTANCE_FLOOR,
        )

        def _counter(pk):
            v = _R.get(f"{pk}:memory-gate:prune_count")
            return int(v) if v else 0

        pk = "test-dismissal-prune-3b"
        before = _counter(pk)

        m = MagicMock()
        m.memory_id = "ahhh"
        m.project_key = pk
        m.access_count = 4  # previously recalled -> excluded from prune tiers
        m.importance = MIN_IMPORTANCE_FLOOR  # already at the floor
        # All-dismissed history (0% act rate), long enough to satisfy the guard;
        # dismissal_count one below threshold so this dismissed trips decay.
        m.metadata = {
            "dismissal_count": DISMISSAL_DECAY_THRESHOLD - 1,
            "outcome_history": [
                {"outcome": "dismissed", "reasoning": "", "ts": 0}
                for _ in range(DISMISSAL_DECAY_THRESHOLD)
            ],
        }

        _persist_outcome_metadata([m], {"ahhh": "dismissed"})

        # Superseded (tombstoned) directly, and prune_count incremented.
        assert m.superseded_by == "dismissal-prune"
        assert _counter(pk) == before + 1
        # A named-project record must NOT leak into the DEFAULT_PROJECT_KEY counter.
        assert pk != DEFAULT_PROJECT_KEY

    def test_dismissal_prune_skips_record_with_prior_acted(self):
        """A record whose history includes an 'acted' outcome (act rate > 0) is NOT
        superseded by the dismissal-dominated exit, even when floored."""
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata
        from config.memory_defaults import DISMISSAL_DECAY_THRESHOLD, MIN_IMPORTANCE_FLOOR

        m = MagicMock()
        m.memory_id = "mixed"
        m.importance = MIN_IMPORTANCE_FLOOR
        m.metadata = {
            "dismissal_count": DISMISSAL_DECAY_THRESHOLD - 1,
            "outcome_history": [
                {"outcome": "acted", "reasoning": "", "ts": 0},
                {"outcome": "dismissed", "reasoning": "", "ts": 0},
                {"outcome": "dismissed", "reasoning": "", "ts": 0},
            ],
        }

        _persist_outcome_metadata([m], {"mixed": "dismissed"})

        # Not superseded: act rate > 0 means it is not dismissal-dominated.
        # (The impl only assigns the sentinel when the exit fires; here it must not.)
        assert m.superseded_by != "dismissal-prune"


class TestJudgeOutcomesLlm:
    """Test agent/memory_extraction.py _judge_outcomes_llm().

    #1925: _llm_call now routes through agent.llm.run_typed. These tests
    mock run_typed at its module-level import site in agent.memory_extraction
    -- no real network call and no dependence on PydanticAI's internal
    Anthropic tool-calling wire format. Each test still exercises the real
    json.loads-based parsing in _judge_outcomes_llm via ExtractionResult.text.
    """

    @pytest.mark.asyncio
    async def test_parses_valid_llm_response(self):
        import json
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import ExtractionResult, _judge_outcomes_llm

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

        mock_run_typed = AsyncMock(return_value=ExtractionResult(text=llm_response))
        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
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
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import ExtractionResult, _judge_outcomes_llm

        llm_response = json.dumps(
            [
                {
                    "index": 0,
                    "outcome": "echoed",
                    "reasoning": "Keywords overlap but no causal link.",
                },
            ]
        )

        mock_run_typed = AsyncMock(return_value=ExtractionResult(text=llm_response))
        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
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
        from unittest.mock import AsyncMock, patch

        from agent.llm import LLMCallError
        from agent.memory_extraction import _judge_outcomes_llm

        mock_run_typed = AsyncMock(side_effect=LLMCallError("simulated API failure"))
        with (
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("agent.memory_extraction.run_typed", mock_run_typed),
        ):
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
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import ExtractionResult, _judge_outcomes_llm

        # LLM only returns judgment for index 0, not index 1
        llm_response = json.dumps(
            [
                {"index": 0, "outcome": "acted", "reasoning": "Influenced the response."},
            ]
        )

        mock_run_typed = AsyncMock(return_value=ExtractionResult(text=llm_response))
        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
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
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import (
            _OUTCOME_MAX_THOUGHTS,
            ExtractionResult,
            _judge_outcomes_llm,
        )

        # Create 7 thoughts
        thoughts = [(f"key{i}", f"thought number {i} with enough text") for i in range(7)]

        llm_response = json.dumps(
            [
                {"index": i, "outcome": "acted", "reasoning": "yes"}
                for i in range(_OUTCOME_MAX_THOUGHTS)
            ]
        )

        mock_run_typed = AsyncMock(return_value=ExtractionResult(text=llm_response))
        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
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
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import ExtractionResult, _judge_outcomes_llm

        mock_run_typed = AsyncMock(return_value=ExtractionResult(text="not valid json at all"))
        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await _judge_outcomes_llm(
                [("key1", "thought")],
                "response",
            )

        assert result is None
