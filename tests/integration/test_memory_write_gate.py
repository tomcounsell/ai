"""Integration tests: the Memory.save() content gate across every writer path (#2201).

Valor's subconscious memory system has five distinct code paths that
construct and save a Memory record. Before this issue, only one of them
(Claude Code hook ingest) applied any content-quality filtering; the other
four saved whatever they were given, relying only on WriteFilterMixin's
importance threshold. Issue #2201 moved the content gate into
`Memory.save()` itself so every path inherits it for free.

This module proves that claim empirically, one test per path, against the
real `Memory` model and real Redis (no mocks on the model/gate itself) --
per this repo's "no mocks, use actual APIs" testing philosophy. Each path
gets a junk-content case (asserting the write is dropped) and a
durable-content case (asserting it persists).

The five paths (see docs/plans/memory-write-gate-unification.md):
    1. Hook ingest              -- .claude/hooks/hook_utils/memory_bridge.py::ingest()
    2. Post-session extraction  -- agent/memory_extraction.py::extract_observations_async()
    3. Post-merge learning      -- agent/memory_extraction.py::extract_post_merge_learning()
    4. Telegram bridge          -- bridge/telegram_bridge.py (Memory.safe_save call site)
    5. Intentional CLI save     -- tools/memory_search/__init__.py::save()

Uses the autouse ``redis_test_db`` fixture (tests/conftest.py) for
per-worker Redis isolation; all test records are additionally scoped
under a ``test-gate-`` project_key prefix and cleaned up via the Popoto
ORM (never raw Redis) in a module-level teardown.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.integration]

# Hook code lives outside the `agent`/`models` package tree under
# .claude/hooks -- add it to sys.path the same way tests/unit/test_memory_bridge.py
# does, so `hook_utils.memory_bridge` is importable.
_HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

_PROJECT_KEY_PREFIX = "test-gate-"


def _unique_project_key(suffix: str) -> str:
    return f"{_PROJECT_KEY_PREFIX}{suffix}-{uuid.uuid4().hex[:8]}"


def _cleanup(project_key: str) -> None:
    """Delete every Memory record under project_key via the Popoto ORM.

    Never raw Redis (`delete`/`srem`/`sadd`/`zrem`) on Popoto-managed keys
    -- per-instance `.delete()` only, per repo convention.
    """
    from models.memory import Memory

    for record in Memory.query.filter(project_key=project_key):
        record.delete()


class TestHookIngestWriterPath:
    """Writer path #1: .claude/hooks/hook_utils/memory_bridge.py::ingest()."""

    def test_fragment_junk_is_dropped(self):
        from hook_utils.memory_bridge import ingest

        project_key = _unique_project_key("hook-fragment")
        try:
            # Dangling colon (fragment under classify_content), >= the hook's
            # own MIN_PROMPT_LENGTH=50 so it clears the hook's pre-filter and
            # reaches the model gate itself.
            junk = (
                "Here is a description of the deployment process that "
                "we still need to write up in full detail:"
            )
            assert len(junk.strip()) >= 50

            with patch("hook_utils.memory_bridge._get_project_key", return_value=project_key):
                result = ingest(junk)

            assert result is False

            from models.memory import Memory

            assert list(Memory.query.filter(project_key=project_key)) == []
        finally:
            _cleanup(project_key)

    def test_durable_content_persists(self):
        from hook_utils.memory_bridge import ingest

        project_key = _unique_project_key("hook-durable")
        try:
            durable = (
                f"Durable fact {uuid.uuid4().hex[:8]}: deploys run every Friday "
                "afternoon after the smoke suite is green."
            )
            assert len(durable.strip()) >= 50

            with patch("hook_utils.memory_bridge._get_project_key", return_value=project_key):
                result = ingest(durable)

            assert result is True

            from models.memory import Memory

            records = Memory.query.filter(project_key=project_key)
            assert len(records) == 1
            assert records[0].content == durable
        finally:
            _cleanup(project_key)


class TestPostSessionExtractionWriterPath:
    """Writer path #2: agent/memory_extraction.py::extract_observations_async()."""

    @pytest.mark.asyncio
    async def test_below_floor_observation_is_dropped(self):
        """A JSON observation between 10 (extraction's own floor) and 15
        (the model gate's MIN_CONTENT_LENGTH) chars passes extraction's own
        per-item filter but is still gated by Memory.save() -- proving the
        model-layer gate, not extraction's pre-existing length check, is
        what catches it.
        """
        import json

        from agent.memory_extraction import extract_observations_async

        project_key = _unique_project_key("extraction-short")
        try:
            # "Fixed at noon" == 13 chars: >= extraction's len(observation) < 10
            # drop, but < the model gate's MIN_CONTENT_LENGTH (15).
            raw = json.dumps([{"category": "decision", "observation": "Fixed at noon"}])
            assert 10 <= len("Fixed at noon") < 15

            mock_llm = AsyncMock(return_value=raw)
            with (
                patch("agent.memory_extraction._llm_call", mock_llm),
                patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            ):
                result = await extract_observations_async(
                    "sess-gate-extraction-short", raw, project_key=project_key
                )

            assert result == []

            from models.memory import Memory

            assert list(Memory.query.filter(project_key=project_key)) == []
        finally:
            _cleanup(project_key)

    @pytest.mark.asyncio
    async def test_durable_observation_persists(self):
        import json

        from agent.memory_extraction import extract_observations_async

        project_key = _unique_project_key("extraction-durable")
        try:
            observation = (
                f"Chose blue-green deployment {uuid.uuid4().hex[:8]} over rolling "
                "updates for zero-downtime releases"
            )
            raw = json.dumps([{"category": "decision", "observation": observation}])

            mock_llm = AsyncMock(return_value=raw)
            with (
                patch("agent.memory_extraction._llm_call", mock_llm),
                patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            ):
                result = await extract_observations_async(
                    "sess-gate-extraction-durable", raw, project_key=project_key
                )

            assert result != []

            from models.memory import Memory

            records = Memory.query.filter(project_key=project_key)
            assert len(records) == 1
            assert records[0].content == observation
        finally:
            _cleanup(project_key)


class TestPostMergeLearningWriterPath:
    """Writer path #3: agent/memory_extraction.py::extract_post_merge_learning()."""

    @pytest.mark.asyncio
    async def test_fragment_learning_is_dropped(self):
        from agent.memory_extraction import extract_post_merge_learning

        project_key = _unique_project_key("postmerge-fragment")
        try:
            # Dangling colon (fragment), >= post-merge's own 20-char floor.
            raw = "Includes a helper function we still need to write:"
            assert len(raw) >= 20

            mock_llm = AsyncMock(return_value=raw)
            with (
                patch("agent.memory_extraction._llm_call", mock_llm),
                patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            ):
                result = await extract_post_merge_learning(
                    "Fix flaky test", "body", "diff", project_key=project_key
                )

            assert result is None

            from models.memory import Memory

            assert list(Memory.query.filter(project_key=project_key)) == []
        finally:
            _cleanup(project_key)

    @pytest.mark.asyncio
    async def test_durable_learning_persists(self):
        from agent.memory_extraction import extract_post_merge_learning

        project_key = _unique_project_key("postmerge-durable")
        try:
            raw = (
                f"Chose blue-green deployment {uuid.uuid4().hex[:8]} for "
                "zero-downtime release cycles across all services."
            )

            mock_llm = AsyncMock(return_value=raw)
            with (
                patch("agent.memory_extraction._llm_call", mock_llm),
                patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            ):
                result = await extract_post_merge_learning(
                    "Adopt blue-green deploys", "body", "diff", project_key=project_key
                )

            assert result is not None

            from models.memory import Memory

            records = Memory.query.filter(project_key=project_key)
            assert len(records) == 1
            assert records[0].content == raw
        finally:
            _cleanup(project_key)


class TestTelegramBridgeWriterPath:
    """Writer path #4: bridge/telegram_bridge.py's Memory.safe_save call site.

    The save call itself (bridge/telegram_bridge.py:1335) is a plain
    `Memory.safe_save(agent_id=..., project_key=..., content=..., importance=
    InteractionWeight.HUMAN, source="human")` inside a full Telethon event
    handler. Reconstructing that handler (event objects, sender resolution,
    project routing) is out of scope for a write-gate test -- what this
    test proves is the exact call shape at that site funnels through the
    same gated Memory.save(), which is the property #2201 established.
    """

    def test_ack_only_message_is_dropped(self):
        from popoto import InteractionWeight

        from models.memory import Memory

        project_key = _unique_project_key("telegram-ack")
        try:
            result = Memory.safe_save(
                agent_id="test-sender",
                project_key=project_key,
                content="Yup",
                importance=InteractionWeight.HUMAN,
                source="human",
            )

            assert result is None
            assert list(Memory.query.filter(project_key=project_key)) == []
        finally:
            _cleanup(project_key)

    def test_durable_message_persists(self):
        from popoto import InteractionWeight

        from models.memory import Memory

        project_key = _unique_project_key("telegram-durable")
        try:
            content = f"Deploy on Fridays only {uuid.uuid4().hex[:8]}, after the smoke suite."

            result = Memory.safe_save(
                agent_id="test-sender",
                project_key=project_key,
                content=content,
                importance=InteractionWeight.HUMAN,
                source="human",
            )

            assert result is not None

            records = Memory.query.filter(project_key=project_key)
            assert len(records) == 1
            assert records[0].content == content
        finally:
            _cleanup(project_key)


class TestIntentionalCliSaveWriterPath:
    """Writer path #5: tools/memory_search/__init__.py::save()."""

    def test_fragment_content_is_dropped(self):
        from tools.memory_search import save

        project_key = _unique_project_key("cli-fragment")
        try:
            result = save("includes:", project_key=project_key)

            assert result is None

            from models.memory import Memory

            assert list(Memory.query.filter(project_key=project_key)) == []
        finally:
            _cleanup(project_key)

    def test_durable_content_persists(self):
        from tools.memory_search import save

        project_key = _unique_project_key("cli-durable")
        try:
            content = f"Deploy on Fridays {uuid.uuid4().hex[:8]} after the smoke suite is green."

            result = save(content, project_key=project_key)

            assert result is not None

            from models.memory import Memory

            records = Memory.query.filter(project_key=project_key)
            assert len(records) == 1
            assert records[0].content == content
        finally:
            _cleanup(project_key)
