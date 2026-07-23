"""Integration tests: memory-distill-backfill end-to-end (memory-distilled-ingest, Phase 3).

Exercises reflections/memory/memory_distill_backfill.py's run() against the
REAL Memory model and real Redis (the autouse redis_test_db fixture) -- per
this repo's "no mocks, use actual APIs" testing philosophy -- with only the
LLM call (agent.memory_extraction.distill_human_prompt_async) stubbed, since
that is the one genuine external dependency (Anthropic API).

Covers the two terminal-state transitions described in the plan's Data Flow
and Success Criteria:
  1. provisional -> distilled (a valid distillation settles the record).
  2. provisional -> distill_abandoned (attempt-cap breach; also exercised
     without ever needing to reach the LLM, since the cap-check branch skips
     the call entirely).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.integration]

_PROJECT_KEY_PREFIX = "test-distill-backfill-"


def _unique_project_key(suffix: str) -> str:
    return f"{_PROJECT_KEY_PREFIX}{suffix}-{uuid.uuid4().hex[:8]}"


def _cleanup(project_key: str) -> None:
    """Delete every Memory record under project_key via the Popoto ORM."""
    from models.memory import Memory

    for record in Memory.query.filter(project_key=project_key):
        record.delete()


class TestProvisionalToDistilled:
    """A provisional record is distilled into a settled fact end-to-end."""

    @pytest.mark.asyncio
    async def test_provisional_record_becomes_distilled(self):
        from config.memory_defaults import PROVISIONAL_INGEST_IMPORTANCE
        from models.memory import SOURCE_HUMAN, Memory
        from reflections.memory.memory_distill_backfill import run as run_backfill

        project_key = _unique_project_key("happy")
        try:
            saved = Memory.safe_save(
                agent_id=project_key,
                project_key=project_key,
                content="Rewrite the justfile in a way that groups related tasks",
                importance=PROVISIONAL_INGEST_IMPORTANCE,
                source=SOURCE_HUMAN,
                metadata={
                    "distill_status": "provisional",
                    "distill_attempts": 0,
                    "distill_last_attempt_at": 0,
                },
            )
            assert saved is not None

            distilled_fact = "Tom wants the justfile rewritten to group related tasks"
            mock_llm = AsyncMock(return_value={"fact": distilled_fact, "category": "decision"})
            with patch("agent.memory_extraction.distill_human_prompt_async", mock_llm):
                result = await run_backfill()

            assert result["status"] == "ok"

            refreshed = Memory.query.filter(project_key=project_key).first()
            assert refreshed is not None
            assert refreshed.content == distilled_fact
            assert refreshed.metadata["distill_status"] == "distilled"
            assert refreshed.metadata["distill_attempts"] == 1
            assert refreshed.metadata["distill_model"]
            assert refreshed.metadata["distill_prompt_version"]
            # decision category (4.0) + DISTILL_SOURCE_WEIGHT (2.0) = 6.0
            assert refreshed.importance == pytest.approx(6.0)
        finally:
            _cleanup(project_key)

    @pytest.mark.asyncio
    async def test_distilled_record_is_retrievable_via_recall(self):
        """The settled fact re-indexes BM25/bloom on the new content."""
        from config.memory_defaults import PROVISIONAL_INGEST_IMPORTANCE
        from models.memory import SOURCE_HUMAN, Memory
        from reflections.memory.memory_distill_backfill import run as run_backfill

        project_key = _unique_project_key("recall")
        unique_token = f"zzqdistillrecall{uuid.uuid4().hex[:8]}"
        try:
            saved = Memory.safe_save(
                agent_id=project_key,
                project_key=project_key,
                content="some raw human utterance about a preference",
                importance=PROVISIONAL_INGEST_IMPORTANCE,
                source=SOURCE_HUMAN,
                metadata={
                    "distill_status": "provisional",
                    "distill_attempts": 0,
                    "distill_last_attempt_at": 0,
                },
            )
            assert saved is not None

            distilled_fact = f"Tom prefers {unique_token} conventions for local tooling"
            mock_llm = AsyncMock(return_value={"fact": distilled_fact, "category": "pattern"})
            with patch("agent.memory_extraction.distill_human_prompt_async", mock_llm):
                await run_backfill()

            from agent.memory_retrieval import retrieve_memories

            results = retrieve_memories(unique_token, project_key, limit=10, min_rrf_score=None)
            assert len(results) >= 1
            assert any(r.memory_id == saved.memory_id for r in results)
        finally:
            _cleanup(project_key)


class TestProvisionalToAbandoned:
    """A provisional record at the attempt ceiling reaches terminal distill_abandoned."""

    @pytest.mark.asyncio
    async def test_capped_record_becomes_abandoned_without_llm_call(self):
        from config.memory_defaults import (
            MAX_DISTILL_ATTEMPTS,
            PROVISIONAL_INGEST_IMPORTANCE,
        )
        from models.memory import SOURCE_HUMAN, Memory
        from reflections.memory.memory_distill_backfill import run as run_backfill

        project_key = _unique_project_key("capped")
        try:
            saved = Memory.safe_save(
                agent_id=project_key,
                project_key=project_key,
                content="a human utterance that has failed distillation repeatedly",
                importance=PROVISIONAL_INGEST_IMPORTANCE,
                source=SOURCE_HUMAN,
                metadata={
                    "distill_status": "provisional",
                    "distill_attempts": MAX_DISTILL_ATTEMPTS,
                    "distill_last_attempt_at": 12345,
                },
            )
            assert saved is not None

            mock_llm = AsyncMock()
            with patch("agent.memory_extraction.distill_human_prompt_async", mock_llm):
                result = await run_backfill()

            assert result["status"] == "ok"
            mock_llm.assert_not_called()

            refreshed = Memory.query.filter(project_key=project_key).first()
            assert refreshed is not None
            assert refreshed.metadata["distill_status"] == "distill_abandoned"
            # Content is left verbatim on an abandon transition -- only metadata changes.
            assert refreshed.content == "a human utterance that has failed distillation repeatedly"
        finally:
            _cleanup(project_key)

    @pytest.mark.asyncio
    async def test_persistent_llm_failure_reaches_abandoned_after_cap_runs(self):
        """Repeated LLM failures across MAX_DISTILL_ATTEMPTS runs terminate the record."""
        from config.memory_defaults import (
            MAX_DISTILL_ATTEMPTS,
            PROVISIONAL_INGEST_IMPORTANCE,
        )
        from models.memory import SOURCE_HUMAN, Memory
        from reflections.memory.memory_distill_backfill import run as run_backfill

        project_key = _unique_project_key("persistent-fail")
        try:
            saved = Memory.safe_save(
                agent_id=project_key,
                project_key=project_key,
                content="a human utterance the LLM keeps refusing to distill",
                importance=PROVISIONAL_INGEST_IMPORTANCE,
                source=SOURCE_HUMAN,
                metadata={
                    "distill_status": "provisional",
                    "distill_attempts": 0,
                    "distill_last_attempt_at": 0,
                },
            )
            assert saved is not None

            mock_llm = AsyncMock(return_value=None)  # always fails-open
            with patch("agent.memory_extraction.distill_human_prompt_async", mock_llm):
                for _ in range(MAX_DISTILL_ATTEMPTS):
                    result = await run_backfill()
                    assert result["status"] == "ok"

            refreshed = Memory.query.filter(project_key=project_key).first()
            assert refreshed is not None
            assert refreshed.metadata["distill_status"] == "distill_abandoned"
            assert refreshed.metadata["distill_attempts"] == MAX_DISTILL_ATTEMPTS
        finally:
            _cleanup(project_key)


class TestSweepIntegration:
    @pytest.mark.asyncio
    async def test_sweep_abandons_real_provisional_record(self):
        from config.memory_defaults import PROVISIONAL_INGEST_IMPORTANCE
        from models.memory import SOURCE_HUMAN, Memory
        from reflections.memory.memory_distill_backfill import (
            sweep_provisional_to_abandoned,
        )

        project_key = _unique_project_key("sweep")
        try:
            saved = Memory.safe_save(
                agent_id=project_key,
                project_key=project_key,
                content="a stranded provisional record from a disabled feature",
                importance=PROVISIONAL_INGEST_IMPORTANCE,
                source=SOURCE_HUMAN,
                metadata={
                    "distill_status": "provisional",
                    "distill_attempts": 0,
                    "distill_last_attempt_at": 0,
                },
            )
            assert saved is not None

            result = sweep_provisional_to_abandoned()
            assert result["status"] == "ok"
            assert result["abandoned"] >= 1

            refreshed = Memory.query.filter(project_key=project_key).first()
            assert refreshed is not None
            assert refreshed.metadata["distill_status"] == "distill_abandoned"
            assert refreshed.content == "a stranded provisional record from a disabled feature"
        finally:
            _cleanup(project_key)
