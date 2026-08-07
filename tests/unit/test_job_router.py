"""bridge/job_router.py — bind-or-mint Job routing on local granite (Task 13).

Modeled on the retired session-router structure: zero-candidate
short-circuit, top-5 recency cap, post-hoc valid_ids membership check,
total fail-open to NEW — worst case is an extra Job, never a lost or
wrong-bound message. The permanent ``reply:{...}`` message→job index is a
standalone non-Popoto string key bound via SET NX (schema-gate ruling 1),
no TTL, so reply-to routing never needs a model call.

Granite is never called for real here: tests monkeypatch
``bridge.job_router.run_typed_local``.
"""

import uuid

import pytest

from agent.llm import LLMCallError
from bridge.job_router import (
    JOB_ROUTER_CONFIDENCE_THRESHOLD,
    JobRouteDecision,
    bind_message_to_job,
    lookup_job_for_message,
    route_message,
    telegram_message_key,
)
from models.job import Job


@pytest.fixture
def scratch_room_id():
    rid = f"test-jobrouter-{uuid.uuid4().hex[:8]}|telegram:1"
    yield rid
    for job in Job.query.filter(room_id=rid):
        job.delete()


@pytest.fixture
def scratch_message_key():
    """Unique reply-index key; raw-key cleanup (non-Popoto, sanctioned)."""
    key = f"test-msg-{uuid.uuid4().hex[:8]}"
    yield key
    from popoto.redis_db import POPOTO_REDIS_DB

    POPOTO_REDIS_DB.delete(f"reply:{key}")


def _decision(monkeypatch, decision: JobRouteDecision):
    async def fake_run_typed_local(prompt, output_type, **kwargs):
        return decision

    monkeypatch.setattr("bridge.job_router.run_typed_local", fake_run_typed_local)


def _forbid_model_call(monkeypatch):
    async def exploding(prompt, output_type, **kwargs):
        raise AssertionError("model must not be called on this path")

    monkeypatch.setattr("bridge.job_router.run_typed_local", exploding)


class TestReplyIndex:
    def test_message_key_is_chat_scoped(self):
        # Telegram message ids are per-chat; the key must carry the chat id.
        assert telegram_message_key(-100123, 42) == "-100123:42"

    def test_bind_is_setnx_first_writer_wins(self, scratch_message_key, scratch_room_id):
        assert bind_message_to_job(scratch_message_key, "job-a", room_id=scratch_room_id) is True
        assert bind_message_to_job(scratch_message_key, "job-b", room_id=scratch_room_id) is False
        bound = lookup_job_for_message(scratch_message_key)
        assert bound == ("job-a", scratch_room_id)

    def test_lookup_unbound_message_is_none(self):
        assert lookup_job_for_message(f"test-unbound-{uuid.uuid4().hex}") is None

    def test_binding_has_no_ttl(self, scratch_message_key, scratch_room_id):
        from popoto.redis_db import POPOTO_REDIS_DB

        bind_message_to_job(scratch_message_key, "job-a", room_id=scratch_room_id)
        assert POPOTO_REDIS_DB.ttl(f"reply:{scratch_message_key}") == -1


class TestRouteMessage:
    async def test_zero_candidates_mints_without_model_call(
        self, monkeypatch, scratch_room_id, scratch_message_key
    ):
        _forbid_model_call(monkeypatch)

        job = await route_message(scratch_room_id, "fix the tests", scratch_message_key)

        assert job.room_id == scratch_room_id
        assert job.goal_is_placeholder()
        # And the message is bound for idempotency.
        assert lookup_job_for_message(scratch_message_key) == (job.job_id, scratch_room_id)

    async def test_bind_verdict_binds_to_existing_job(
        self, monkeypatch, scratch_room_id, scratch_message_key
    ):
        existing = Job.mint(scratch_room_id, "deploy the fix")
        _decision(
            monkeypatch,
            JobRouteDecision(decision="bind", job_id=existing.job_id, confidence=0.95),
        )

        job = await route_message(scratch_room_id, "did the deploy finish?", scratch_message_key)

        assert job.job_id == existing.job_id

    async def test_reply_to_routes_via_index_without_model_call(
        self, monkeypatch, scratch_room_id, scratch_message_key
    ):
        existing = Job.mint(scratch_room_id, "deploy the fix")
        # Seed another candidate so a model call WOULD have candidates.
        Job.mint(scratch_room_id, "other work")
        parent_key = f"test-msg-{uuid.uuid4().hex[:8]}"
        bind_message_to_job(parent_key, existing.job_id, room_id=scratch_room_id)
        _forbid_model_call(monkeypatch)
        try:
            job = await route_message(
                scratch_room_id,
                "yes go ahead",
                scratch_message_key,
                reply_to_message_key=parent_key,
            )
        finally:
            from popoto.redis_db import POPOTO_REDIS_DB

            POPOTO_REDIS_DB.delete(f"reply:{parent_key}")

        assert job.job_id == existing.job_id

    async def test_invalid_job_id_from_model_is_new(
        self, monkeypatch, scratch_room_id, scratch_message_key
    ):
        existing = Job.mint(scratch_room_id, "deploy the fix")
        _decision(
            monkeypatch,
            JobRouteDecision(decision="bind", job_id="not-a-real-id", confidence=0.99),
        )

        job = await route_message(scratch_room_id, "unrelated", scratch_message_key)

        assert job.job_id != existing.job_id
        assert job.goal_is_placeholder()

    async def test_below_threshold_is_new(self, monkeypatch, scratch_room_id, scratch_message_key):
        existing = Job.mint(scratch_room_id, "deploy the fix")
        low = JOB_ROUTER_CONFIDENCE_THRESHOLD - 0.05
        _decision(
            monkeypatch,
            JobRouteDecision(decision="bind", job_id=existing.job_id, confidence=low),
        )

        job = await route_message(scratch_room_id, "unrelated", scratch_message_key)

        assert job.job_id != existing.job_id

    async def test_model_error_fails_open_to_new(
        self, monkeypatch, scratch_room_id, scratch_message_key
    ):
        Job.mint(scratch_room_id, "deploy the fix")

        async def unreachable(prompt, output_type, **kwargs):
            raise LLMCallError("ollama unreachable")

        monkeypatch.setattr("bridge.job_router.run_typed_local", unreachable)

        job = await route_message(scratch_room_id, "unrelated", scratch_message_key)

        assert job.goal_is_placeholder()

    async def test_reroute_is_idempotent_on_existing_binding(
        self, monkeypatch, scratch_room_id, scratch_message_key
    ):
        """Race 4: a crash-after-bind re-route no-ops via the SETNX index."""
        first = await_result = None
        _forbid_model_call(monkeypatch)
        first = await route_message(scratch_room_id, "fix the tests", scratch_message_key)
        await_result = await route_message(scratch_room_id, "fix the tests", scratch_message_key)

        assert await_result.job_id == first.job_id
        assert len(list(Job.query.filter(room_id=scratch_room_id))) == 1

    async def test_bound_job_is_revived_and_touched(
        self, monkeypatch, scratch_room_id, scratch_message_key
    ):
        existing = Job.mint(scratch_room_id, "deploy the fix")
        existing.mark_at_rest()
        _decision(
            monkeypatch,
            JobRouteDecision(decision="bind", job_id=existing.job_id, confidence=0.95),
        )

        job = await route_message(scratch_room_id, "status?", scratch_message_key)

        assert job.status == "active"
