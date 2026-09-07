"""SideEffectJob: the durable row behind post-session side effects (#3183).

The row exists so a handler's arguments survive the process that scheduled
them. These tests hold the three properties that makes true:

- ``payload_json`` round-trips the handler's exact keyword arguments, so no
  handler is rewritten to fit the queue.
- ``enqueue`` is single-winner per ``(kind, session_id)``, cross-process, so
  a health-check revival and a fleet-wide migration cannot both create a row.
- ``run_due`` respects ``next_attempt_at``. That predicate is what makes the
  backoff real; a job whose next attempt is in the future must not run, and
  the field must be declared range-queryable for the drain's filter to be
  servable at all.
"""

import json
from datetime import timedelta

import pytest

from agent import side_effects
from utils.utc import utc_now


class FakeRedis:
    """The slice of the text client the create guard uses."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.set_calls = 0

    def set(self, key, value, nx=False, ex=None):
        self.set_calls += 1
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        return self.store.pop(key, None) is not None


class FakeJobs:
    """An in-memory stand-in for the model, with the drain's one query."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    # -- model surface ----------------------------------------------------
    def create(self, **kwargs):
        self.rows[kwargs["job_id"]] = dict(kwargs)
        return _Row(self, kwargs["job_id"])

    @property
    def query(self):
        return self

    def filter(self, status=None, next_attempt_at__lte=None):
        return [
            _Row(self, job_id)
            for job_id, row in self.rows.items()
            if (status is None or row["status"] == status)
            and (next_attempt_at__lte is None or row["next_attempt_at"] <= next_attempt_at__lte)
        ]

    def get(self, job_id=None):
        return _Row(self, job_id) if job_id in self.rows else None


class _Row:
    def __init__(self, store: FakeJobs, job_id: str):
        object.__setattr__(self, "_store", store)
        object.__setattr__(self, "job_id", job_id)

    def __getattr__(self, name):
        try:
            return self._store.rows[self.job_id][name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self._store.rows[self.job_id][name] = value

    def save(self):
        pass

    def delete(self):
        self._store.rows.pop(self.job_id, None)


@pytest.fixture
def jobs(monkeypatch):
    store = FakeJobs()
    redis = FakeRedis()
    monkeypatch.setattr(side_effects, "SideEffectJob", store)
    monkeypatch.setattr("utils.redis_client.text_redis", lambda: redis)
    store.redis = redis
    return store


class TestPayloadRoundTrip:
    """The handler's arguments travel in the row, not in anyone's memory."""

    def test_payload_is_stored_as_json(self, jobs):
        side_effects.enqueue(
            "memory_extraction",
            "s1",
            "valor",
            {"response_text": "hello", "turn_count": 4, "is_conversational": True},
        )
        (row,) = jobs.rows.values()
        assert json.loads(row["payload_json"]) == {
            "response_text": "hello",
            "turn_count": 4,
            "is_conversational": True,
        }

    @pytest.mark.asyncio
    async def test_payload_reaches_the_handler_as_keyword_arguments(self, jobs, monkeypatch):
        """``run_due`` calls the handler with its exact production signature."""
        seen = {}

        async def _handler(session_id, response_text=None, turn_count=None, is_conversational=True):
            seen.update(
                session_id=session_id,
                response_text=response_text,
                turn_count=turn_count,
                is_conversational=is_conversational,
            )

        monkeypatch.setattr(side_effects, "resolve_handler", lambda kind: _handler)
        side_effects.enqueue(
            "memory_extraction",
            "s1",
            "valor",
            {"response_text": "body", "turn_count": 2, "is_conversational": False},
        )

        summary = await side_effects.run_due()
        assert summary["ran"] == 1
        assert seen == {
            "session_id": "s1",
            "response_text": "body",
            "turn_count": 2,
            "is_conversational": False,
        }
        assert not jobs.rows, "a job that ran is deleted, not left behind"

    @pytest.mark.asyncio
    async def test_absent_payload_calls_the_handler_with_the_session_id_alone(
        self, jobs, monkeypatch
    ):
        """A null ``payload_json`` degrades to no kwargs rather than raising."""
        seen = []

        async def _handler(session_id, **kwargs):
            seen.append((session_id, kwargs))

        monkeypatch.setattr(side_effects, "resolve_handler", lambda kind: _handler)
        side_effects.enqueue("memory_extraction", "s1", "valor", None)
        await side_effects.run_due()
        assert seen == [("s1", {})]


class TestIdempotentCreate:
    """Single-winner per ``(kind, session_id)``, across processes."""

    def test_two_enqueues_produce_one_row_and_one_job_id(self, jobs):
        first = side_effects.enqueue("memory_extraction", "s1", "valor", {"a": 1})
        second = side_effects.enqueue("memory_extraction", "s1", "valor", {"a": 2})
        assert first == second
        assert len(jobs.rows) == 1

    def test_different_sessions_get_different_rows(self, jobs):
        side_effects.enqueue("memory_extraction", "s1", "valor")
        side_effects.enqueue("memory_extraction", "s2", "valor")
        assert len(jobs.rows) == 2

    def test_the_guard_key_names_the_pair(self, jobs):
        side_effects.enqueue("memory_extraction", "s1", "valor")
        assert "sideeffect:idem:memory_extraction:s1" in jobs.redis.store

    def test_an_unreachable_guard_raises_rather_than_creating_a_row(self, jobs, monkeypatch):
        """A possibly-duplicate row is worse than a visible failure.

        The one hot-path caller wraps this in its own try; every other caller
        needs the failure to propagate.
        """

        class _Down:
            def set(self, *args, **kwargs):
                raise ConnectionError("redis down")

        monkeypatch.setattr("utils.redis_client.text_redis", lambda: _Down())
        with pytest.raises(ConnectionError):
            side_effects.enqueue("memory_extraction", "s1", "valor")
        assert not jobs.rows


class TestNotDue:
    """``next_attempt_at`` is what makes the backoff real."""

    @pytest.mark.asyncio
    async def test_a_job_due_in_the_future_is_not_run(self, jobs, monkeypatch):
        """The row that catches a dropped ``__lte`` predicate.

        Dropping it would run every pending job on every 60s tick, erasing the
        backoff while every assertion about the stored value stayed green.
        """
        calls = []

        async def _handler(session_id, **kwargs):
            calls.append(session_id)

        monkeypatch.setattr(side_effects, "resolve_handler", lambda kind: _handler)
        job_id = side_effects.enqueue("memory_extraction", "s1", "valor")
        jobs.rows[job_id]["next_attempt_at"] = utc_now() + timedelta(hours=1)

        summary = await side_effects.run_due()
        assert calls == []
        assert summary["ran"] == 0
        assert jobs.rows[job_id]["attempts"] == 0

    def test_next_attempt_at_is_declared_range_queryable(self):
        """A plain DatetimeField cannot serve the drain's ``__lte`` filter."""
        from popoto.fields.sorted_field_mixin import SortedFieldMixin

        from models.side_effect_job import SideEffectJob

        field = SideEffectJob._meta.fields["next_attempt_at"]
        assert isinstance(field, SortedFieldMixin)


class TestFailureHandling:
    """One bad job never stops the batch, and a doomed job becomes visible."""

    @pytest.mark.asyncio
    async def test_a_raising_handler_backs_off_and_counts(self, jobs, monkeypatch):
        async def _boom(session_id, **kwargs):
            raise RuntimeError("handler exploded")

        monkeypatch.setattr(side_effects, "resolve_handler", lambda kind: _boom)
        job_id = side_effects.enqueue("memory_extraction", "s1", "valor")
        before = jobs.rows[job_id]["next_attempt_at"]

        summary = await side_effects.run_due()
        assert summary["failed"] == 1
        assert jobs.rows[job_id]["attempts"] == 1
        assert jobs.rows[job_id]["status"] == "pending"
        assert jobs.rows[job_id]["next_attempt_at"] > before

    @pytest.mark.asyncio
    async def test_at_the_cap_the_job_becomes_a_dead_letter(self, jobs, monkeypatch):
        recorded = []

        async def _boom(session_id, **kwargs):
            raise RuntimeError("still broken")

        monkeypatch.setattr(side_effects, "resolve_handler", lambda kind: _boom)
        monkeypatch.setattr(
            "bridge.dead_letters.record",
            lambda *args, **kwargs: recorded.append((args, kwargs)),
        )
        job_id = side_effects.enqueue("memory_extraction", "s1", "valor", {"response_text": "x"})
        jobs.rows[job_id]["attempts"] = side_effects.MAX_JOB_ATTEMPTS - 1

        summary = await side_effects.run_due()
        assert summary["dead_lettered"] == 1
        assert not jobs.rows, "the exhausted row is deleted once it is a dead letter"
        (args, kwargs) = recorded[0]
        assert args[0] == "extraction"
        assert args[1]["_session_id"] == "s1"

    @pytest.mark.asyncio
    async def test_a_bad_kind_does_not_stop_the_rest_of_the_batch(self, jobs, monkeypatch):
        ran = []

        async def _ok(session_id, **kwargs):
            ran.append(session_id)

        monkeypatch.setattr(
            side_effects,
            "resolve_handler",
            lambda kind: _ok if kind == "memory_extraction" else None,
        )
        monkeypatch.setattr("bridge.dead_letters.record", lambda *a, **k: None)
        side_effects.enqueue("memory_extraction", "good", "valor")
        side_effects.enqueue("no_such_kind", "bad", "valor")

        summary = await side_effects.run_due()
        assert ran == ["good"]
        assert summary["ran"] == 1
        assert summary["dead_lettered"] == 1

    @pytest.mark.asyncio
    async def test_paused_drain_runs_nothing(self, jobs, monkeypatch):
        """Pausing leaves the rows to drain when the pause lifts."""

        async def _handler(session_id, **kwargs):
            raise AssertionError("must not run while paused")

        monkeypatch.setattr(side_effects, "resolve_handler", lambda kind: _handler)
        monkeypatch.setattr(side_effects, "_paused", lambda: True)
        side_effects.enqueue("memory_extraction", "s1", "valor")

        summary = await side_effects.run_due()
        assert summary["skipped"] == 1
        assert len(jobs.rows) == 1
