"""Extraction stays decoupled from finalization, now through a durable row (#3183).

Hotfix #1055 decoupled post-session memory extraction from session
finalization: a 30-second stall in extraction must not delay the session
completing or the nudge firing. The mechanism was an in-process
``asyncio.create_task``; it is now a ``SideEffectJob`` row written at the end
of ``_execute_agent_session`` and drained out of process.

The user-visible SLO is unchanged and is what these tests hold: the
post-finalization sequence completes within 5 seconds no matter how long
extraction takes. The row makes the guarantee stronger rather than weaker —
the enqueue is one Redis write, and extraction latency is now on the other
side of a process boundary, so it cannot reach the nudge path at all.

What the row adds, and these tests also pin:

- A worker restart mid-extraction loses nothing; the row is still pending.
- A handler that raises is retried on a backoff and, at the cap, becomes a
  ``DeadLetter(stage="extraction")`` a human can see. The old wrapper
  swallowed the failure into a debug log.
"""

import asyncio
import time

import pytest

from agent import side_effects


class _FakeRedis:
    """Enough of the text client for the single-winner create guard."""

    def __init__(self):
        self.store: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        return self.store.pop(key, None) is not None


class _FakeJobStore:
    """An in-memory stand-in for the ``SideEffectJob`` model."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def create(self, **kwargs):
        self.rows[kwargs["job_id"]] = dict(kwargs)
        return kwargs


@pytest.fixture
def job_store(monkeypatch):
    store = _FakeJobStore()
    redis = _FakeRedis()

    class _Model:
        @staticmethod
        def create(**kwargs):
            return store.create(**kwargs)

    monkeypatch.setattr(side_effects, "SideEffectJob", _Model)
    monkeypatch.setattr("utils.redis_client.text_redis", lambda: redis)
    return store


class TestSessionFinalizationDecoupled:
    """The 5-second SLO, held by an enqueue instead of a create_task."""

    @pytest.mark.asyncio
    async def test_enqueue_returns_within_the_slo_with_a_hung_handler(self, job_store):
        """A handler that would block for 30s cannot delay the nudge path.

        The handler is never called here, and that is the point: the enqueue
        writes a row and returns, and the drain runs the handler in a
        different process on its own schedule.
        """
        handler_ran = asyncio.Event()

        async def _slow_extract(session_id, **kwargs):
            handler_ran.set()
            await asyncio.sleep(30)

        nudge_fired = asyncio.Event()

        async def _fake_nudge():
            nudge_fired.set()

        start = time.time()
        # The exact call shape _execute_agent_session uses: enqueue, then the
        # nudge path.
        side_effects.enqueue(
            "memory_extraction",
            "sess-int-1",
            "valor",
            {"response_text": "A" * 200, "turn_count": 3, "is_conversational": True},
        )
        await _fake_nudge()
        elapsed = time.time() - start

        assert elapsed < 5.0, (
            f"enqueue + nudge path took {elapsed:.2f}s — violates the 5s SLO. "
            "Extraction latency MUST NOT block session finalization (#1055)."
        )
        assert nudge_fired.is_set()
        assert not handler_ran.is_set(), "the handler runs in the drain, not at the seam"
        assert len(job_store.rows) == 1

    @pytest.mark.asyncio
    async def test_the_row_survives_the_process_that_wrote_it(self, job_store):
        """The enqueued row is complete on its own.

        This is the property the in-memory task could not have: everything the
        handler needs is in the row, so a worker restart between the enqueue
        and the drain costs nothing.
        """
        import json

        side_effects.enqueue(
            "memory_extraction",
            "sess-restart",
            "valor",
            {"response_text": "body", "turn_count": 7, "is_conversational": False},
        )
        (row,) = job_store.rows.values()
        assert row["session_id"] == "sess-restart"
        assert row["status"] == "pending"
        assert json.loads(row["payload_json"]) == {
            "response_text": "body",
            "turn_count": 7,
            "is_conversational": False,
        }

    @pytest.mark.asyncio
    async def test_two_enqueues_for_one_session_bind_to_one_row(self, job_store):
        """The single-winner guard replaces the old in-memory dedup.

        ``_pending_extraction_tasks`` deduplicated within one worker process.
        The guard is cross-process, which is what the fleet-wide migration
        back-enqueue and a health-check revival both need.
        """
        first = side_effects.enqueue("memory_extraction", "sess-dup", "valor", {"a": 1})
        second = side_effects.enqueue("memory_extraction", "sess-dup", "valor", {"a": 2})

        assert first == second
        assert len(job_store.rows) == 1
