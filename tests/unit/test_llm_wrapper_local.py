"""Unit tests for ``agent.llm.run_typed_local`` — the granite-on-Ollama leg.

Durability plan #2494 Task 13: both hot-path classifiers (intake, Job
router) run on the local granite model via PydanticAI. ``run_typed_local``
is the single construction point for those calls — same typed-output
contract as ``run_typed``, but against the local Ollama daemon (no
Anthropic client, no shared Anthropic semaphore, no API key).

Network isolation: every test monkeypatches ``wrapper_mod.OpenAIChatModel``
with a PydanticAI ``FunctionModel`` factory so no test talks to a real
Ollama daemon.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent.llm import LLMCallError, run_typed_local
from agent.llm import wrapper as wrapper_mod


class Decision(BaseModel):
    """Minimal structured output type used across these tests."""

    decision: str
    confidence: float


def _tool_response(info: AgentInfo, args: dict) -> ModelResponse:
    tool_name = info.output_tools[0].name if info.output_tools else None
    return ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args=args)])


def _install_function_model(monkeypatch, fn, *, capture: dict | None = None):
    """Swap ``wrapper_mod.OpenAIChatModel`` for an in-process FunctionModel.

    ``capture`` (when given) records the ``model_name`` and ``provider``
    the wrapper would have used, for post-call assertions.
    """

    def fake_openai_chat_model(model_name, *, provider):
        if capture is not None:
            capture["model_name"] = model_name
            capture["provider"] = provider
        return FunctionModel(fn, model_name=model_name)

    monkeypatch.setattr(wrapper_mod, "OpenAIChatModel", fake_openai_chat_model)


class TestStructuredOutputSuccess:
    async def test_valid_response_returns_output_type_instance(self, monkeypatch):
        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return _tool_response(info, {"decision": "bind", "confidence": 0.9})

        _install_function_model(monkeypatch, fn)

        result = await run_typed_local("route: hello", Decision)

        assert isinstance(result, Decision)
        assert result.decision == "bind"
        assert result.confidence == 0.9

    async def test_defaults_to_granite_classifier_model(self, monkeypatch):
        from config.models import OLLAMA_CLASSIFIER_MODEL

        capture: dict = {}

        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return _tool_response(info, {"decision": "new", "confidence": 0.1})

        _install_function_model(monkeypatch, fn, capture=capture)

        await run_typed_local("route: hello", Decision)

        assert capture["model_name"] == OLLAMA_CLASSIFIER_MODEL

    async def test_provider_points_at_ollama_host(self, monkeypatch):
        capture: dict = {}

        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return _tool_response(info, {"decision": "new", "confidence": 0.1})

        _install_function_model(monkeypatch, fn, capture=capture)

        await run_typed_local("route: hello", Decision)

        base_url = str(capture["provider"].base_url)
        assert "/v1" in base_url


class TestErrorSurfacing:
    async def test_provider_error_propagates_as_llm_call_error(self, monkeypatch):
        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            raise RuntimeError("ollama unreachable")

        _install_function_model(monkeypatch, fn)

        with pytest.raises(LLMCallError) as exc_info:
            await run_typed_local("route: hello", Decision)

        assert isinstance(exc_info.value.__cause__, RuntimeError)

    async def test_hard_timeout_bounds_wall_clock(self, monkeypatch):
        async def slow_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            await asyncio.sleep(30)
            return _tool_response(info, {"decision": "new", "confidence": 0.1})

        _install_function_model(monkeypatch, slow_fn)

        with pytest.raises(LLMCallError):
            await run_typed_local("route: hello", Decision, hard_timeout=0.05)


class TestInputValidation:
    @pytest.mark.parametrize("bad_prompt", ["", "   ", None])
    async def test_empty_prompt_fails_fast(self, bad_prompt, monkeypatch):
        calls = {"n": 0}

        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            calls["n"] += 1
            return _tool_response(info, {"decision": "new", "confidence": 0.1})

        _install_function_model(monkeypatch, fn)

        with pytest.raises(ValueError):
            await run_typed_local(bad_prompt, Decision)

        assert calls["n"] == 0
