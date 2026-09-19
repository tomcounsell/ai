"""The Anthropic leg of ``run_typed`` (#3410): slot, deadline, cancellation.

``agent/llm/backends/anthropic.py::call`` carries the hotfix #1055 / #1111
invariant for every Anthropic-routed call. ``tests/unit/test_llm_wrapper.py``
drives the leg's happy path through ``run_typed``; this file pins the parts
that only show under pressure, calling the leg directly with a fake stack:

* Slot starvation: with the shared semaphore saturated, ``slot_timeout``
  raises ``LLMCallError(reason="slot_timeout")`` and the ``AsyncAnthropic``
  constructor is never entered.
* Cancellation (the wrapper's ``hard_timeout``, a caller going away) is
  expected: it passes through, the slot is released, the client is closed.
* The deadline re-check after the slot (Race 1), on a faked clock: 10 s of a
  15 s remainder spent waiting gives a client ``timeout <= 5.0`` with
  ``max_retries == 0``; 14.8 s spent raises ``reason="timeout"`` with the
  constructor never entered.
* ``max_retries=None`` leaves the SDK default (the kwarg is not passed);
  an explicit value reaches the constructor.
* No ``asyncio.wait_for`` and no third-party import at module scope.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage, ModelResponse, SystemPromptPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent import anthropic_client
from agent.llm import LLMCallError
from agent.llm.backends import anthropic as anthropic_leg
from agent.llm.router import Route
from agent.llm.tasks import Backend
from config.models import MODEL_FAST

ROUTE = Route(Backend.ANTHROPIC, MODEL_FAST)


class Classification(BaseModel):
    label: str
    confidence: float


class FakeAsyncAnthropic:
    """Records constructor kwargs and the ``async with`` close."""

    instances: list[FakeAsyncAnthropic] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.closed = False
        FakeAsyncAnthropic.instances.append(self)

    async def __aenter__(self) -> FakeAsyncAnthropic:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.closed = True
        return None


def _tool_response(info: AgentInfo, args: dict) -> ModelResponse:
    tool_name = info.output_tools[0].name if info.output_tools else None
    return ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args=args)])


def _respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    return _tool_response(info, {"label": "x", "confidence": 0.5})


@dataclasses.dataclass
class _Harness:
    """The fake stack plus a switch for what the FunctionModel answers."""

    stack: object
    _holder: dict

    def respond_with(self, fn) -> None:
        self._holder["fn"] = fn


@pytest.fixture
def stack(monkeypatch) -> _Harness:
    """The real stack with a fake ``anthropic`` module and a FunctionModel."""
    FakeAsyncAnthropic.instances = []
    monkeypatch.setattr(anthropic_leg, "get_anthropic_api_key", lambda: "fake-test-key")
    real = anthropic_client._load_stack()
    holder = {"fn": _respond}

    def fake_anthropic_model(model_name, *, provider):
        return FunctionModel(holder["fn"], model_name=model_name)

    fake = dataclasses.replace(
        real,
        anthropic=SimpleNamespace(AsyncAnthropic=FakeAsyncAnthropic),
        AnthropicModel=fake_anthropic_model,
    )
    return _Harness(stack=fake, _holder=holder)


async def _call(harness: _Harness, **overrides) -> Classification:
    kwargs = {
        "system": None,
        "sdk_timeout": 30.0,
        "slot_timeout": None,
        "max_retries": None,
        "deadline": None,
        "stack": harness.stack,
    }
    kwargs.update(overrides)
    return await anthropic_leg.call("classify: hello", Classification, ROUTE, **kwargs)


class TestSlotStarvation:
    async def test_slot_timeout_raises_before_any_client_exists(self, monkeypatch, stack):
        monkeypatch.setattr(anthropic_client, "_semaphore", asyncio.Semaphore(0))

        with pytest.raises(LLMCallError) as exc_info:
            await _call(stack, slot_timeout=0.05)

        assert exc_info.value.reason == "slot_timeout"
        assert isinstance(exc_info.value.__cause__, TimeoutError)
        assert FakeAsyncAnthropic.instances == [], "the constructor must never be entered"

    async def test_slot_timeout_stays_inside_the_budget(self, monkeypatch, stack):
        monkeypatch.setattr(anthropic_client, "_semaphore", asyncio.Semaphore(0))
        loop = asyncio.get_running_loop()
        start = loop.time()

        with pytest.raises(LLMCallError):
            await _call(stack, slot_timeout=0.05, sdk_timeout=3.0)

        assert loop.time() - start < 1.0


class TestCancellation:
    async def test_cancel_releases_the_slot_and_closes_the_client(self, monkeypatch, stack):
        semaphore = asyncio.Semaphore(1)
        monkeypatch.setattr(anthropic_client, "_semaphore", semaphore)
        started = asyncio.Event()

        async def slow(messages, info) -> ModelResponse:
            started.set()
            await asyncio.sleep(30)
            return _respond(messages, info)

        stack.respond_with(slow)
        task = asyncio.create_task(_call(stack))
        await started.wait()
        assert semaphore.locked(), "the slot is held for the whole Agent.run()"

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert not semaphore.locked(), "cancellation must release the slot"
        (client,) = FakeAsyncAnthropic.instances
        assert client.closed is True, "cancellation must close the client (httpx cleanup)"


class _ClockSlot:
    """A ``semaphore_slot`` stand-in that spends ``consume`` seconds of a fake clock."""

    def __init__(self, clock: dict, consume: float) -> None:
        self._clock = clock
        self._consume = consume
        self.timeout = None

    def __call__(self, timeout=None):
        self.timeout = timeout
        return self

    async def __aenter__(self):
        self._clock["now"] += self._consume
        return None

    async def __aexit__(self, *exc):
        return None


class TestDeadlineRecheck:
    """Race 1: the slot wait and the request cannot each spend the remainder."""

    def _fake_clock(self, monkeypatch, consume: float) -> dict:
        clock = {"now": 1000.0}
        monkeypatch.setattr(anthropic_leg, "monotonic", lambda: clock["now"])
        monkeypatch.setattr(anthropic_leg, "semaphore_slot", _ClockSlot(clock, consume))
        return clock

    async def test_ten_seconds_in_the_slot_leaves_five_for_the_request(self, monkeypatch, stack):
        clock = self._fake_clock(monkeypatch, consume=10.0)
        deadline = clock["now"] + 15.0

        await _call(stack, sdk_timeout=15.0, slot_timeout=15.0, max_retries=0, deadline=deadline)

        (client,) = FakeAsyncAnthropic.instances
        assert client.kwargs["timeout"] <= 5.0
        assert client.kwargs["max_retries"] == 0

    async def test_almost_no_remainder_raises_timeout_with_no_client(self, monkeypatch, stack):
        clock = self._fake_clock(monkeypatch, consume=14.8)
        deadline = clock["now"] + 15.0

        with pytest.raises(LLMCallError) as exc_info:
            await _call(
                stack, sdk_timeout=15.0, slot_timeout=15.0, max_retries=0, deadline=deadline
            )

        assert exc_info.value.reason == "timeout"
        assert FakeAsyncAnthropic.instances == [], "the constructor must never be entered"

    async def test_no_deadline_keeps_the_sdk_timeout(self, monkeypatch, stack):
        self._fake_clock(monkeypatch, consume=10.0)

        await _call(stack, sdk_timeout=15.0)

        assert FakeAsyncAnthropic.instances[0].kwargs["timeout"] == 15.0


class TestClientConstruction:
    async def test_max_retries_none_leaves_the_sdk_default(self, stack):
        await _call(stack, max_retries=None)

        assert "max_retries" not in FakeAsyncAnthropic.instances[0].kwargs

    async def test_explicit_max_retries_reaches_the_client(self, stack):
        await _call(stack, max_retries=0)

        assert FakeAsyncAnthropic.instances[0].kwargs["max_retries"] == 0

    async def test_client_is_closed_after_a_successful_call(self, stack):
        await _call(stack)

        assert FakeAsyncAnthropic.instances[0].closed is True

    async def test_system_prompt_reaches_the_agent(self, stack):
        seen: dict = {}

        def capture(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            seen["messages"] = messages
            return _respond(messages, info)

        stack.respond_with(capture)

        await _call(stack, system="You are a classifier.")

        first_parts = seen["messages"][0].parts
        assert any(
            isinstance(p, SystemPromptPart) and p.content == "You are a classifier."
            for p in first_parts
        )


class TestFailureReasons:
    async def test_provider_error_is_transport(self, stack):
        def explode(messages, info) -> ModelResponse:
            raise RuntimeError("transport moved")

        stack.respond_with(explode)

        with pytest.raises(LLMCallError) as exc_info:
            await _call(stack)

        assert exc_info.value.reason == "transport"
        assert isinstance(exc_info.value.__cause__, RuntimeError)

    async def test_schema_exhaustion_is_validation(self, stack):
        def invalid(messages, info) -> ModelResponse:
            return _tool_response(info, {"label": "x"})

        stack.respond_with(invalid)

        with pytest.raises(LLMCallError) as exc_info:
            await _call(stack)

        assert exc_info.value.reason == "validation"

    async def test_sdk_timeout_error_is_timeout(self, stack):
        import anthropic
        import httpx

        def timed_out(messages, info) -> ModelResponse:
            raise anthropic.APITimeoutError(
                request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
            )

        stack.respond_with(timed_out)

        with pytest.raises(LLMCallError) as exc_info:
            await _call(stack)

        assert exc_info.value.reason == "timeout"


def test_no_third_party_import_at_module_scope():
    """#3001: the leg takes every third-party symbol from the stack."""
    tree = ast.parse(Path(anthropic_leg.__file__).read_text())
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"openai", "pydantic_ai", "anthropic", "httpx"}, imported


def test_no_wait_for_inside_the_leg():
    """Hotfix #1055: the SDK timer is the only timer around the request."""
    tree = ast.parse(Path(anthropic_leg.__file__).read_text())
    hits = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "wait_for"
    ]
    assert hits == []
