"""Integration: the bridge threads the chat's project key into the router (#3410).

Charter §7 is applied by ``agent/llm/router.py::resolve`` from the
``project_key`` each call carries, so a classifier that drops the key on the
floor resolves to the subscription backend on every message regardless of its
declared ``backend``. These tests drive the real bridge functions with a fake
``run_typed`` that records the ``task`` and ``project_key`` it receives, and
assert the key the bridge resolved for the chat is the one the router sees.

Offline: the wrapper is faked at each module's import seam; no Redis rows
are written and no model is called.

Covered here: ``should_respond_async`` into the two routing classifiers it
reaches (C1 ``routing.needs_response`` on an unaddressed group message, C2
``routing.terminus`` on a reply to Valor), the ``classify_work_request``
entry point (C3), the promise gate's CLI path (C9 ``promise_gate.verdict``,
key resolved from a real ``AgentSession`` row by ``session_id``) and the
drafter's main path (``_evaluate_drafter_promise``, the caller every
outbound reply crosses, key read from its ``session``), and read-the-room
(``read_the_room.verdict``, key read from the ``session`` it is handed).

The promise-gate and read-the-room cases write one ``AgentSession`` row to
the claimed test db (``project_key="valor"`` is load-bearing for the pin, so
the row is created and deleted inside the test).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import bridge.promise_gate as promise_gate
import bridge.read_the_room as rtr_module
import bridge.routing as routing
from bridge.message_drafter import _evaluate_drafter_promise
from bridge.promise_gate import PROMISE_VERDICT, PromiseVerdictDecision, evaluate_promise_async
from bridge.read_the_room import READ_THE_ROOM, RoomVerdict, read_the_room
from bridge.routing import (
    NEEDS_RESPONSE,
    TERMINUS,
    WORK_REQUEST,
    NeedsResponseDecision,
    RoutingDecision,
    TerminusDecision,
    classify_work_request,
    should_respond_async,
)
from tests.helpers.llm_fakes import FakeRunTyped

PROJECT_KEY = "test-routing-project"


class _RecordingRunTyped:
    """A ``run_typed`` stand-in that answers by output type and records kwargs."""

    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, prompt, output_type, **kwargs):
        self.calls.append({"output_type": output_type, **kwargs})
        if output_type is NeedsResponseDecision:
            return NeedsResponseDecision(needs_response=True)
        if output_type is TerminusDecision:
            return TerminusDecision(verdict="RESPOND")
        if output_type is RoutingDecision:
            return RoutingDecision(category="sdlc")
        raise AssertionError(f"unexpected output type {output_type!r}")


@pytest.fixture
def recorder(monkeypatch):
    rec = _RecordingRunTyped()
    monkeypatch.setattr(routing, "run_typed", rec)
    return rec


def _project() -> dict:
    """An owned group whose unaddressed messages reach the C1 classifier."""
    return {
        "_key": PROJECT_KEY,
        "name": "Routing Project",
        "telegram": {
            "respond_to_all": False,
            "respond_to_unaddressed": True,
            "mention_triggers": ["@valorengels"],
        },
    }


class _FakeClient:
    def __init__(self, replied_msg=None):
        self._replied = replied_msg

    async def get_messages(self, chat_id, ids):
        return self._replied


def _event(text: str, *, reply_to_msg_id: int | None = None):
    async def get_sender():
        return SimpleNamespace(bot=False)

    message = SimpleNamespace(id=42, reply_to_msg_id=reply_to_msg_id, message=text)
    return SimpleNamespace(message=message, chat_id=-100123, get_sender=get_sender)


class TestShouldRespondAsyncThreadsTheProjectKey:
    async def test_unaddressed_group_message_reaches_needs_response_with_the_chat_key(
        self, recorder
    ):
        should, is_reply = await should_respond_async(
            _FakeClient(),
            _event("does anyone know why the deploy is red this morning"),
            "does anyone know why the deploy is red this morning",
            False,
            "Eng: Routing Project",
            _project(),
        )

        assert (should, is_reply) == (True, False)
        assert [c["output_type"] for c in recorder.calls] == [NeedsResponseDecision]
        assert recorder.calls[0]["task"] is NEEDS_RESPONSE
        assert recorder.calls[0]["project_key"] == PROJECT_KEY

    async def test_reply_to_valor_reaches_terminus_with_the_chat_key(self, recorder):
        replied = SimpleNamespace(out=True, message="Here is the fix for the flaky test.")
        should, is_reply = await should_respond_async(
            _FakeClient(replied_msg=replied),
            _event("can you also add a regression test for it", reply_to_msg_id=41),
            "can you also add a regression test for it",
            False,
            "Eng: Routing Project",
            _project(),
        )

        assert (should, is_reply) == (True, True)
        assert [c["output_type"] for c in recorder.calls] == [TerminusDecision]
        assert recorder.calls[0]["task"] is TERMINUS
        assert recorder.calls[0]["project_key"] == PROJECT_KEY


class TestClassifyWorkRequestThreadsTheProjectKey:
    async def test_work_request_reaches_the_router_with_the_given_key(self, recorder):
        result = await classify_work_request(
            "please refactor the retry loop in the worker", project_key=PROJECT_KEY
        )

        assert result == "sdlc"
        assert [c["output_type"] for c in recorder.calls] == [RoutingDecision]
        assert recorder.calls[0]["task"] is WORK_REQUEST
        assert recorder.calls[0]["project_key"] == PROJECT_KEY

    async def test_without_a_key_the_router_sees_none(self, recorder):
        """A caller that cannot know its project passes nothing; the router's
        fail-closed rule then resolves the call to the subscription backend."""
        await classify_work_request("please refactor the retry loop in the worker")

        assert recorder.calls[0]["project_key"] is None


# === C9: the promise gate resolves its key from the session ===


class _RecordingPromiseJudge:
    """A ``run_typed`` stand-in for the promise gate: allows, records kwargs."""

    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, prompt, output_type, **kwargs):
        assert output_type is PromiseVerdictDecision
        self.calls.append({"prompt": prompt, **kwargs})
        return PromiseVerdictDecision(action="allow", reason="evidence present", class_=None)


@pytest.fixture
def promise_judge(monkeypatch, tmp_path):
    judge = _RecordingPromiseJudge()
    monkeypatch.setattr(promise_gate, "run_typed", judge)
    monkeypatch.setattr(promise_gate, "get_anthropic_api_key", lambda: "test-key")
    monkeypatch.setattr(promise_gate, "_AUDIT_LOG_PATH", tmp_path / "classification_audit.jsonl")
    monkeypatch.delenv("PROMISE_GATE_ENABLED", raising=False)
    return judge


@pytest.fixture
def valor_session(redis_test_db):
    """A real ``AgentSession`` row keyed to ``valor``, deleted after the test."""
    from models.agent_session import AgentSession

    session = AgentSession.create(
        session_id=f"test-3410-promise-{datetime.now(tz=UTC).timestamp()}",
        session_type="eng",
        project_key="valor",
        working_dir="/tmp",
        status="running",
        chat_id="test-3410-chat",
        message_text="please open the PR",
        sender_name="tester",
        created_at=datetime.now(tz=UTC),
    )
    try:
        yield session
    finally:
        session.delete()


LONG_DRAFT = (
    "The retry loop in the worker now backs off exponentially and the regression "
    "test for it is in tests/unit/test_worker_retry.py; PR #3410 carries both, "
    "the suite is green at 2c921dbe3, and the merge is queued behind review."
)


class TestPromiseGateThreadsTheProjectKey:
    async def test_real_session_id_reaches_the_judge_with_the_session_key(
        self, promise_judge, valor_session
    ):
        verdict = await evaluate_promise_async(
            LONG_DRAFT, transport="telegram", session_id=valor_session.id
        )

        assert verdict.action == "allow"
        (call,) = promise_judge.calls
        assert call["task"] is PROMISE_VERDICT
        assert call["project_key"] == "valor"
        assert call["prompt"] == LONG_DRAFT

    async def test_synthetic_session_id_reaches_the_judge_with_none(self, promise_judge):
        """The CLI senders pass ``cli-{epoch}`` ids that name no row; the router's
        fail-closed rule then resolves the call to the subscription backend."""
        await evaluate_promise_async(LONG_DRAFT, transport="telegram", session_id="cli-1700000000")

        (call,) = promise_judge.calls
        assert call["task"] is PROMISE_VERDICT
        assert call["project_key"] is None


class TestDrafterPathThreadsTheProjectKey:
    async def test_drafter_main_path_reaches_the_judge_with_the_session_key(
        self, promise_judge, valor_session
    ):
        """``bridge/message_drafter.py::_evaluate_drafter_promise`` is the caller
        every outbound reply crosses; it reads the key from the session it holds."""
        verdict = await _evaluate_drafter_promise(
            LONG_DRAFT, medium="telegram", session=valor_session, use_llm=True
        )

        assert verdict.action == "allow"
        (call,) = promise_judge.calls
        assert call["task"] is PROMISE_VERDICT
        assert call["project_key"] == "valor"


# === read-the-room resolves its key from the session it is handed ===


class TestReadTheRoomThreadsTheProjectKey:
    async def test_group_draft_reaches_the_leg_with_the_session_key(
        self, monkeypatch, valor_session
    ):
        """``read_the_room(draft, chat_id, session)`` reads ``session.project_key``;
        a thinking task stays on Anthropic, so the key matters for the
        fail-closed rule's symmetry rather than for the leg it lands on."""
        fake = FakeRunTyped(result=RoomVerdict(action="send", reason="clean"))
        monkeypatch.setattr(rtr_module, "run_typed", fake)

        async def snapshot(chat_id, *, k, max_age_seconds):
            return [{"sender": "Tom", "content": "is the retry loop fixed yet?"}]

        monkeypatch.setattr(rtr_module, "_fetch_snapshot", snapshot)

        verdict = await read_the_room(LONG_DRAFT, "-1001234567890", session=valor_session)

        assert verdict.action == "send"
        (call,) = fake.calls
        assert call.output_type is RoomVerdict
        assert call.task is READ_THE_ROOM
        assert call.project_key == "valor"
        assert call.kwargs["hard_timeout"] is None
