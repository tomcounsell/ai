"""Post-session extraction is a durable job, not a fire-and-forget task (#3183).

Extraction used to be an ``asyncio.create_task`` the worker held in memory
(hotfix #1055). It is now a ``SideEffectJob`` row written at the end of
``_execute_agent_session``, drained out of process by the
``side-effect-drain`` reflection. These tests pin the properties that made the
old scheduler safe and the ones the row adds:

- The enqueue happens at the end of ``_execute_agent_session``, and it is an
  enqueue rather than a ``create_task``.
- ``payload_json`` carries ``response_text``, ``turn_count`` and
  ``is_conversational``, so the #1822 trivial-session gate survives the
  process boundary. Reading either signal inside the drain would not: teardown
  clears the in-memory turn tracker, which is why they are captured by value.
- An enqueue failure is non-fatal at that call site. A raise there would skip
  the session teardown that follows it.
"""

import inspect
import json

import pytest

from tests.unit.session_lookup_mock import wire_session_lookup


class TestExtractionSeamIsAJobEnqueue:
    """The seam is a durable row, and the old machinery is gone."""

    def test_fire_and_forget_scheduler_is_gone(self):
        """The in-memory scheduler and its shutdown drain no longer exist.

        Their presence would mean two extraction paths, and the one that
        cannot survive a worker restart would still be live.
        """
        from agent import session_executor as se

        assert not hasattr(se, "_schedule_post_session_extraction")
        assert not hasattr(se, "drain_pending_extractions")
        assert not hasattr(se, "_pending_extraction_tasks")

    def test_executor_enqueues_rather_than_creating_a_task(self):
        """``_execute_agent_session`` reaches extraction through ``enqueue``."""
        from agent import session_executor as se

        source = inspect.getsource(se._execute_agent_session)
        assert 'enqueue(\n                "memory_extraction",' in source, (
            "the extraction seam must be a SideEffectJob enqueue"
        )
        assert "post_session_extraction" not in source, (
            "no in-process extraction task may survive at the seam"
        )

    def test_enqueue_writes_the_gate_signals_into_the_payload(self, monkeypatch):
        """The three payload values round-trip through ``payload_json``.

        ``turn_count`` and ``is_conversational`` are the #1822 trivial-session
        gate. They are captured before teardown and snapshotted into the row;
        re-deriving either in the drain minutes later would silently restore
        the bug they were added to fix.
        """
        from agent import side_effects

        created = {}

        class _FakeJob:
            @staticmethod
            def create(**kwargs):
                created.update(kwargs)
                return None

        class _FakeRedis:
            def set(self, *args, **kwargs):
                return True

            def get(self, key):
                return None

        monkeypatch.setattr(side_effects, "SideEffectJob", _FakeJob)
        monkeypatch.setattr("utils.redis_client.text_redis", lambda: _FakeRedis())

        side_effects.enqueue(
            "memory_extraction",
            "sess-gate",
            "valor",
            {"response_text": "text", "turn_count": 1, "is_conversational": False},
        )

        payload = json.loads(created["payload_json"])
        assert payload == {
            "response_text": "text",
            "turn_count": 1,
            "is_conversational": False,
        }
        assert created["session_id"] == "sess-gate"
        assert created["status"] == "pending"


class TestEnqueueFailureIsNonFatalAtTheSeam:
    """A Redis error at the seam must cost an extraction, never a teardown."""

    def test_seam_wraps_the_enqueue(self):
        """The call site carries its own try/except with a non-fatal log.

        ``enqueue()`` itself keeps raising — the migration back-enqueue depends
        on that — so the guard has to live here. The statement sits inside
        ``_execute_agent_session`` with no enclosing try, and a raise would
        skip the error-case snapshot, the steering-queue rescue, and the
        reaction/nudge path to the end of the function.
        """
        from agent import session_executor as se

        source = inspect.getsource(se._execute_agent_session)
        head = source[: source.index('enqueue(\n                "memory_extraction",')]
        assert head.rstrip().endswith("try:"), "the enqueue must sit directly under a try:"
        assert "SideEffectJob enqueue failed (non-fatal)" in source


class TestTrivialSessionGateSignals:
    """Issue #1822 Fix 2: the capture helpers the payload is built from."""

    def test_is_conversational_session_telegram_origin(self):
        """A session with initial_telegram_message is conversational (must always extract)."""
        from types import SimpleNamespace

        from agent import session_executor as se

        tg = SimpleNamespace(initial_telegram_message={"message_text": "hi", "sender_name": "Tom"})
        cli = SimpleNamespace(initial_telegram_message=None)
        assert se._is_conversational_session(tg) is True
        assert se._is_conversational_session(cli) is False

    def test_is_conversational_session_defaults_true_on_error(self):
        """Unreadable origin signal defaults to conversational (never over-skips)."""
        from agent import session_executor as se

        class _Boom:
            @property
            def initial_telegram_message(self):
                raise RuntimeError("popoto exploded")

        assert se._is_conversational_session(_Boom()) is True

    def test_capture_turn_count_returns_none_on_query_failure(self, monkeypatch):
        """A failed re-fetch yields None so the gate stays a safe no-op."""
        from unittest.mock import MagicMock

        from agent import session_executor as se

        fake_cls = MagicMock()
        fake_cls.query.filter.side_effect = RuntimeError("redis down")
        wire_session_lookup(fake_cls)
        monkeypatch.setattr("models.agent_session.AgentSession", fake_cls)

        assert se._capture_turn_count("whatever") is None


class TestCorrelationReachesTheSubprocess:
    """Lane 5a: the harness env carries the session's correlation id."""

    def test_harness_env_declares_correlation_id(self):
        from agent import session_executor as se

        source = inspect.getsource(se._execute_agent_session)
        assert '"VALOR_CORRELATION_ID": cid or ""' in source


@pytest.mark.asyncio
async def test_run_due_is_a_noop_at_limit_zero(monkeypatch):
    """``run_due(limit=0)`` touches nothing at all."""
    from agent import side_effects

    def _boom(*args, **kwargs):
        raise AssertionError("run_due(limit=0) must not read Redis")

    monkeypatch.setattr(side_effects, "SideEffectJob", _boom)
    assert await side_effects.run_due(limit=0) == {
        "ran": 0,
        "failed": 0,
        "dead_lettered": 0,
        "skipped": 0,
    }
