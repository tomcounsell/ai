"""Unit tests for the distillation core (memory-distilled-ingest, Phase 3).

Covers:
  - config.memory_defaults.compute_ingest_importance() -- the WriteFilterMixin
    floor regression guard (spike-2b).
  - A provisional-insert rank-band check: a record saved at
    PROVISIONAL_INGEST_IMPORTANCE remains retrievable via the real recall
    path before distillation happens.
  - agent.memory_extraction.distill_human_prompt_async() -- fail-open
    behavior on timeout / refusal / empty / unparseable output, and the
    happy-path JSON parse.

Per this repo's testing philosophy, the rank-band test exercises the real
Memory model against the per-worker isolated Redis test db (the autouse
``redis_test_db`` fixture in tests/conftest.py) rather than mocking recall.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest


class TestComputeIngestImportance:
    """config.memory_defaults.compute_ingest_importance() -- spike-2b floor guard."""

    @pytest.mark.parametrize(
        "source_weight,content_value",
        [
            (0.0, 0.0),
            (1.0, 4.0),
            (2.0, 1.0),
            (-5.0, -5.0),  # pathological negative inputs
            (-100.0, -100.0),
            (0.01, 0.01),
            (10.0, 10.0),
        ],
    )
    def test_never_returns_below_floor(self, source_weight, content_value):
        from config.memory_defaults import MEMORY_WF_MIN_THRESHOLD, compute_ingest_importance

        result = compute_ingest_importance(source_weight, content_value)
        assert result >= MEMORY_WF_MIN_THRESHOLD

    def test_normal_inputs_sum_unclamped(self):
        """When the sum is already above the floor, the raw sum is preserved
        (the clamp is a floor, not a rewrite of legitimate values)."""
        from config.memory_defaults import compute_ingest_importance

        assert compute_ingest_importance(2.0, 4.0) == 6.0

    def test_provisional_importance_above_floor(self):
        """PROVISIONAL_INGEST_IMPORTANCE itself must clear the write-filter
        floor -- otherwise every later partial save on a provisional record
        (distillation, terminal-abandon) would be silently dropped."""
        from config.memory_defaults import MEMORY_WF_MIN_THRESHOLD, PROVISIONAL_INGEST_IMPORTANCE

        assert PROVISIONAL_INGEST_IMPORTANCE > MEMORY_WF_MIN_THRESHOLD


class TestProvisionalRankBandRetrievability:
    """A freshly-ingested provisional record must stay retrievable via the
    real recall path in the pre-distillation window (spike-2b concern)."""

    def test_provisional_record_retrievable_before_distillation(self):
        from config.memory_defaults import PROVISIONAL_INGEST_IMPORTANCE
        from models.memory import SOURCE_HUMAN, Memory

        project_key = f"test-distill-rankband-{uuid.uuid4().hex[:8]}"
        unique_token = f"zzqrankband{uuid.uuid4().hex[:8]}"
        content = (
            f"Tom wants the justfile rewritten to use {unique_token} conventions "
            "for local task running"
        )

        try:
            saved = Memory.safe_save(
                agent_id=project_key,
                project_key=project_key,
                content=content,
                importance=PROVISIONAL_INGEST_IMPORTANCE,
                source=SOURCE_HUMAN,
                metadata={
                    "distill_status": "provisional",
                    "distill_attempts": 0,
                    "distill_last_attempt_at": 0,
                },
            )
            if saved is None:
                pytest.skip("Memory.safe_save returned None (bloom dedup or backend issue)")

            assert saved.importance == PROVISIONAL_INGEST_IMPORTANCE
            assert saved.metadata["distill_status"] == "provisional"

            from agent.memory_retrieval import retrieve_memories

            results = retrieve_memories(
                unique_token,
                project_key,
                limit=10,
                min_rrf_score=None,
            )
            assert len(results) >= 1, (
                "Provisional record must remain retrievable before distillation, "
                f"got {len(results)} results"
            )
            assert any(r.memory_id == saved.memory_id for r in results)
        finally:
            from models.memory import Memory as _Memory

            for record in _Memory.query.filter(project_key=project_key):
                record.delete()


class TestDistillHumanPromptAsync:
    """agent.memory_extraction.distill_human_prompt_async() -- fail-open contract."""

    @pytest.mark.asyncio
    async def test_empty_prompt_returns_none(self):
        from agent.memory_extraction import distill_human_prompt_async

        assert await distill_human_prompt_async("") is None
        assert await distill_human_prompt_async("   ") is None

    @pytest.mark.asyncio
    async def test_timeout_fails_open(self):
        from agent.memory_extraction import distill_human_prompt_async

        mock_llm = AsyncMock(side_effect=TimeoutError("hard timeout"))
        with patch("agent.memory_extraction._llm_call", mock_llm):
            result = await distill_human_prompt_async("Rewrite justfile in a way")

        assert result is None

    @pytest.mark.asyncio
    async def test_generic_exception_fails_open(self):
        """Any unexpected error (network, provider) fails open -- never raises."""
        from agent.memory_extraction import distill_human_prompt_async

        mock_llm = AsyncMock(side_effect=RuntimeError("provider exploded"))
        with patch("agent.memory_extraction._llm_call", mock_llm):
            result = await distill_human_prompt_async("Rewrite justfile in a way")

        assert result is None

    @pytest.mark.asyncio
    async def test_none_response_fails_open(self):
        from agent.memory_extraction import distill_human_prompt_async

        mock_llm = AsyncMock(return_value="NONE")
        with patch("agent.memory_extraction._llm_call", mock_llm):
            result = await distill_human_prompt_async("ok")

        assert result is None

    @pytest.mark.asyncio
    async def test_empty_string_response_fails_open(self):
        from agent.memory_extraction import distill_human_prompt_async

        mock_llm = AsyncMock(return_value="")
        with patch("agent.memory_extraction._llm_call", mock_llm):
            result = await distill_human_prompt_async("Rewrite justfile in a way")

        assert result is None

    @pytest.mark.asyncio
    async def test_refusal_output_fails_open(self):
        from agent.memory_extraction import distill_human_prompt_async

        mock_llm = AsyncMock(return_value="there is no agent session to distill")
        with patch("agent.memory_extraction._llm_call", mock_llm):
            result = await distill_human_prompt_async("Rewrite justfile in a way")

        assert result is None

    @pytest.mark.asyncio
    async def test_unparseable_response_fails_open(self):
        from agent.memory_extraction import distill_human_prompt_async

        mock_llm = AsyncMock(return_value="not json at all, just prose")
        with patch("agent.memory_extraction._llm_call", mock_llm):
            result = await distill_human_prompt_async("Rewrite justfile in a way")

        assert result is None

    @pytest.mark.asyncio
    async def test_missing_fact_field_fails_open(self):
        from agent.memory_extraction import distill_human_prompt_async

        mock_llm = AsyncMock(return_value=json.dumps({"category": "decision"}))
        with patch("agent.memory_extraction._llm_call", mock_llm):
            result = await distill_human_prompt_async("Rewrite justfile in a way")

        assert result is None

    @pytest.mark.asyncio
    async def test_success_returns_fact_and_category(self):
        from agent.memory_extraction import distill_human_prompt_async

        payload = json.dumps({"fact": "Tom wants the justfile rewritten", "category": "decision"})
        mock_llm = AsyncMock(return_value=payload)
        with patch("agent.memory_extraction._llm_call", mock_llm):
            result = await distill_human_prompt_async("Rewrite justfile in a way")

        assert result == {"fact": "Tom wants the justfile rewritten", "category": "decision"}
        mock_llm.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_success_lowercases_category(self):
        from agent.memory_extraction import distill_human_prompt_async

        payload = json.dumps({"fact": "Some standalone fact", "category": "Decision"})
        mock_llm = AsyncMock(return_value=payload)
        with patch("agent.memory_extraction._llm_call", mock_llm):
            result = await distill_human_prompt_async("some raw prompt")

        assert result["category"] == "decision"

    @pytest.mark.asyncio
    async def test_success_strips_markdown_fence(self):
        """The wrapper reuses extract_json_payload, so a fenced JSON response
        (a common Haiku output shape) still parses."""
        from agent.memory_extraction import distill_human_prompt_async

        fenced = (
            "```json\n"
            + json.dumps({"fact": "Tom prefers dark mode", "category": "pattern"})
            + "\n```"
        )
        mock_llm = AsyncMock(return_value=fenced)
        with patch("agent.memory_extraction._llm_call", mock_llm):
            result = await distill_human_prompt_async("dark mode please")

        assert result == {"fact": "Tom prefers dark mode", "category": "pattern"}

    @pytest.mark.asyncio
    async def test_never_raises_regardless_of_input(self):
        """Fail-open contract: no input should ever propagate an exception."""
        from agent.memory_extraction import distill_human_prompt_async

        mock_llm = AsyncMock(side_effect=Exception("anything"))
        with patch("agent.memory_extraction._llm_call", mock_llm):
            # Should not raise.
            result = await distill_human_prompt_async("x" * 5000)

        assert result is None
