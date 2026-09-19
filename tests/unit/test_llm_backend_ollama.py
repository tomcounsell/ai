"""The Ollama (local granite) leg of ``run_typed`` (#3410, #2494 Task 13).

``agent/llm/backends/ollama.py::call`` is the body ``run_typed`` runs for a
task the router resolves to ``Backend.OLLAMA``. These tests drive it end to
end through ``run_typed`` with a fake ``AsyncOpenAI`` injected by
``dataclasses.replace(real_stack, AsyncOpenAI=Fake)`` on the wrapper's
``_load_stack`` seam (#3001), so the real ``OllamaProvider`` and
``OpenAIChatModel`` run against an in-process client and no socket opens.

What is pinned:

* The four failure shapes each raise ``LLMCallError`` with a distinct
  ``reason``: connection refused and HTTP 5xx are ``transport``,
  schema-validation exhaustion is ``validation``, the SDK timer is
  ``timeout``.
* The per-call client is closed (``__aexit__`` ran exactly once) on
  success and on every failure.
* The client carries the leg's SDK-level timer and ``max_retries=0``, the
  Ollama base URL, and a placeholder key; the deadline re-check runs once
  before the client exists.
* No ``asyncio.wait_for`` around the request (the hotfix #1055 invariant,
  also asserted by the enumeration test's check 6).
"""

from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path

import httpx
import openai
import pytest
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)
from pydantic import BaseModel

from agent.llm import LLMCallError, LLMStackIncompatible, LLMTask, run_typed
from agent.llm import wrapper as wrapper_mod
from agent.llm.backends import ollama as ollama_leg
from agent.llm.router import Route
from agent.llm.tasks import Backend, TaskKind
from config.models import OLLAMA_CLASSIFIER_MODEL
from config.settings import settings

LOCAL = LLMTask(site="test.local", kind=TaskKind.CLASSIFICATION, backend=Backend.OLLAMA)
_REQUEST = httpx.Request("POST", "http://localhost:11434/v1/chat/completions")


class Decision(BaseModel):
    decision: str
    confidence: float


def _completion(tool_name: str, args: dict) -> ChatCompletion:
    """A real ``ChatCompletion`` calling PydanticAI's structured-output tool."""
    return ChatCompletion(
        id="chatcmpl-test",
        choices=[
            Choice(
                finish_reason="tool_calls",
                index=0,
                message=ChatCompletionMessage(
                    role="assistant",
                    tool_calls=[
                        ChatCompletionMessageToolCall(
                            id="call-1",
                            type="function",
                            function=Function(name=tool_name, arguments=json.dumps(args)),
                        )
                    ],
                ),
            )
        ],
        created=0,
        model="granite-test",
        object="chat.completion",
    )


class _Completions:
    def __init__(self, behaviour):
        self._behaviour = behaviour
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return await self._behaviour(kwargs)


class _Chat:
    def __init__(self, behaviour):
        self.completions = _Completions(behaviour)


class FakeAsyncOpenAI:
    """Stand-in for ``openai.AsyncOpenAI``: records construction and close."""

    instances: list[FakeAsyncOpenAI] = []
    behaviour = None

    def __init__(self, *, base_url, api_key, timeout, max_retries):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.aexit_count = 0
        self.chat = _Chat(type(self).behaviour)
        FakeAsyncOpenAI.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.aexit_count += 1
        return None


def _install(monkeypatch, behaviour) -> None:
    FakeAsyncOpenAI.instances = []
    FakeAsyncOpenAI.behaviour = staticmethod(behaviour)
    real = wrapper_mod._load_stack()
    fake = dataclasses.replace(real, AsyncOpenAI=FakeAsyncOpenAI)
    monkeypatch.setattr(wrapper_mod, "_load_stack", lambda: fake)


def _tool_name(kwargs: dict) -> str:
    return kwargs["tools"][0]["function"]["name"]


async def _ok(kwargs):
    return _completion(_tool_name(kwargs), {"decision": "bind", "confidence": 0.9})


async def _invalid(kwargs):
    return _completion(_tool_name(kwargs), {"decision": "bind"})


async def _refused(kwargs):
    raise openai.APIConnectionError(request=_REQUEST)


async def _five_xx(kwargs):
    raise openai.InternalServerError(
        "upstream fell over", response=httpx.Response(500, request=_REQUEST), body=None
    )


async def _timed_out(kwargs):
    raise openai.APITimeoutError(request=_REQUEST)


async def _call(**kwargs) -> Decision:
    return await run_typed("route: hello", Decision, task=LOCAL, project_key="valor", **kwargs)


class TestSuccess:
    async def test_valid_response_returns_output_type_instance(self, monkeypatch):
        _install(monkeypatch, _ok)

        result = await _call()

        assert isinstance(result, Decision)
        assert result.decision == "bind"
        assert result.confidence == 0.9

    async def test_client_shape(self, monkeypatch):
        """Base URL, placeholder key, the leg's SDK timer, and no SDK retries."""
        _install(monkeypatch, _ok)

        await _call()

        (client,) = FakeAsyncOpenAI.instances
        assert client.base_url == f"{settings.models.ollama_host.rstrip('/')}/v1"
        assert client.api_key == "ollama"
        assert client.timeout == settings.timeouts.local_typed_hard_s
        assert client.max_retries == 0
        assert client.chat.completions.calls[0]["model"] == OLLAMA_CLASSIFIER_MODEL

    async def test_explicit_sdk_timeout_wins(self, monkeypatch):
        _install(monkeypatch, _ok)

        await _call(sdk_timeout=3.0)

        assert FakeAsyncOpenAI.instances[0].timeout == 3.0

    async def test_fresh_client_per_call(self, monkeypatch):
        _install(monkeypatch, _ok)

        await _call()
        await _call()

        assert len(FakeAsyncOpenAI.instances) == 2
        assert FakeAsyncOpenAI.instances[0] is not FakeAsyncOpenAI.instances[1]

    async def test_system_prompt_reaches_the_request(self, monkeypatch):
        _install(monkeypatch, _ok)

        await _call(system="You are a router.")

        messages = FakeAsyncOpenAI.instances[0].chat.completions.calls[0]["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are a router."


class TestFailureReasons:
    @pytest.mark.parametrize(
        ("behaviour", "reason", "cause"),
        [
            (_refused, "transport", openai.APIConnectionError),
            (_five_xx, "transport", openai.InternalServerError),
            (_invalid, "validation", None),
            (_timed_out, "timeout", openai.APITimeoutError),
        ],
        ids=["connection-refused", "http-5xx", "schema-exhaustion", "sdk-timeout"],
    )
    async def test_each_failure_carries_its_reason(self, monkeypatch, behaviour, reason, cause):
        _install(monkeypatch, behaviour)

        with pytest.raises(LLMCallError) as exc_info:
            await _call()

        assert exc_info.value.reason == reason
        if cause is not None:
            # The provider error is the root of the chained cause.
            root = exc_info.value.__cause__
            while root.__cause__ is not None:
                root = root.__cause__
            assert isinstance(root, cause)

    async def test_schema_exhaustion_is_pydantic_ai_s_single_retry(self, monkeypatch):
        _install(monkeypatch, _invalid)

        with pytest.raises(LLMCallError):
            await _call()

        # 1 initial attempt + PydanticAI's single default auto-retry = 2.
        assert len(FakeAsyncOpenAI.instances[0].chat.completions.calls) == 2

    async def test_failure_is_logged(self, monkeypatch, caplog):
        import logging

        _install(monkeypatch, _refused)

        with caplog.at_level(logging.ERROR, logger="agent.llm.backends.ollama"):
            with pytest.raises(LLMCallError):
                await _call()

        assert any("ollama leg transport" in r.message for r in caplog.records)


class TestClientClosedOnceEveryCall:
    @pytest.mark.parametrize(
        "behaviour",
        [_ok, _refused, _five_xx, _invalid, _timed_out],
        ids=["success", "connection-refused", "http-5xx", "schema-exhaustion", "sdk-timeout"],
    )
    async def test_aexit_ran_exactly_once(self, monkeypatch, behaviour):
        _install(monkeypatch, behaviour)

        try:
            await _call()
        except LLMCallError:
            pass

        (client,) = FakeAsyncOpenAI.instances
        assert client.aexit_count == 1


class TestDeadline:
    """The fallback path's re-check runs once, before the client exists."""

    async def test_under_half_a_second_raises_timeout_without_a_client(self, monkeypatch):
        _install(monkeypatch, _ok)
        monkeypatch.setattr(ollama_leg, "monotonic", lambda: 100.0)
        stack = wrapper_mod._load_stack()

        with pytest.raises(LLMCallError) as exc_info:
            await ollama_leg.call(
                "route: hello",
                Decision,
                Route(Backend.OLLAMA, OLLAMA_CLASSIFIER_MODEL),
                system=None,
                sdk_timeout=20.0,
                slot_timeout=None,
                max_retries=None,
                deadline=100.4,
                stack=stack,
            )

        assert exc_info.value.reason == "timeout"
        assert FakeAsyncOpenAI.instances == []

    async def test_remainder_bounds_the_sdk_timer(self, monkeypatch):
        _install(monkeypatch, _ok)
        monkeypatch.setattr(ollama_leg, "monotonic", lambda: 100.0)
        stack = wrapper_mod._load_stack()

        await ollama_leg.call(
            "route: hello",
            Decision,
            Route(Backend.OLLAMA, OLLAMA_CLASSIFIER_MODEL),
            system=None,
            sdk_timeout=20.0,
            slot_timeout=None,
            max_retries=None,
            deadline=105.0,
            stack=stack,
        )

        assert FakeAsyncOpenAI.instances[0].timeout == 5.0


class TestGuardAndLoader:
    async def test_severed_loader_raises_llm_stack_incompatible(self, monkeypatch):
        """``_load_stack`` is the only route to a client; severing it is total."""

        class LoaderSeveredError(RuntimeError):
            pass

        def _sever():
            raise LoaderSeveredError("no stack for you")

        monkeypatch.setattr(wrapper_mod, "_load_stack", _sever)

        with pytest.raises(LLMStackIncompatible) as exc_info:
            await _call()
        assert isinstance(exc_info.value.__cause__, LoaderSeveredError)

    @pytest.mark.parametrize("bad_prompt", ["", "   ", None])
    async def test_empty_prompt_fails_fast(self, monkeypatch, bad_prompt):
        _install(monkeypatch, _ok)

        with pytest.raises(ValueError):
            await run_typed(bad_prompt, Decision, task=LOCAL, project_key="valor")

        assert FakeAsyncOpenAI.instances == []


def test_no_third_party_import_at_module_scope():
    """#3001: the leg takes every third-party symbol from the stack."""
    tree = ast.parse(Path(ollama_leg.__file__).read_text())
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"openai", "pydantic_ai", "anthropic", "httpx"}, imported


def test_no_wait_for_inside_the_leg():
    """Hotfix #1055: the SDK timer is the only timer around the request."""
    tree = ast.parse(Path(ollama_leg.__file__).read_text())
    hits = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "wait_for"
    ]
    assert hits == []
