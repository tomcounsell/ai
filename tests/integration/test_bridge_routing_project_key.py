"""Integration: the bridge threads the chat's project key into the router (#3410).

Charter §7 is applied by ``agent/llm/router.py::resolve`` from the
``project_key`` each call carries, so a classifier that drops the key on the
floor resolves to the subscription backend on every message regardless of its
declared ``backend``. These tests drive the real bridge functions with a fake
``run_typed`` that records the ``task`` and ``project_key`` it receives, and
assert the key the bridge resolved for the chat is the one the router sees.

Offline: the wrapper is faked at each module's import seam; no Redis rows
are written and no model is called.

Covered here (Task 4): ``should_respond_async`` into the two routing
classifiers it reaches (C1 ``routing.needs_response`` on an unaddressed
group message, C2 ``routing.terminus`` on a reply to Valor) and the
``classify_work_request`` entry point (C3). The promise-gate CLI path, the
drafter path (``_evaluate_drafter_promise``) and ``read_the_room`` cases are
added by Tasks 5 and 6 of the same plan.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import bridge.routing as routing
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
