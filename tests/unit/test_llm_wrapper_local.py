"""Unit tests for ``agent.llm.run_typed_local`` — the granite-on-Ollama leg.

Durability plan #2494 Task 13: both hot-path classifiers (intake, Job
router) run on the local granite model via PydanticAI. ``run_typed_local``
is the single construction point for those calls — same typed-output
contract as ``run_typed``, but against the local Ollama daemon (no
Anthropic client, no shared Anthropic semaphore, no API key).

Network isolation (#3001): the third-party stack is no longer imported at
``wrapper``'s module scope, so ``wrapper_mod.OpenAIChatModel`` is gone as a
monkeypatch seam. Every test instead replaces the memoized loader
``wrapper_mod._load_stack`` with one returning a stack whose
``OpenAIChatModel`` is a PydanticAI ``FunctionModel`` factory. That seam is
strictly stronger than the old one: it is the *only* path by which
``run_typed_local`` can reach a provider class at all, so no real Ollama (or
Anthropic) client is constructible from this file --
``test_no_real_network_client_is_reachable`` asserts that directly.
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent.llm import LLMCallError, LLMStackIncompatible, run_typed_local
from agent.llm import wrapper as wrapper_mod


class Decision(BaseModel):
    """Minimal structured output type used across these tests."""

    decision: str
    confidence: float


def _tool_response(info: AgentInfo, args: dict) -> ModelResponse:
    tool_name = info.output_tools[0].name if info.output_tools else None
    return ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args=args)])


def _install_function_model(monkeypatch, fn, *, capture: dict | None = None):
    """Swap the loader's ``OpenAIChatModel`` for an in-process FunctionModel.

    Replaces ``wrapper_mod._load_stack`` wholesale, so the real
    ``OpenAIChatModel`` is never constructed and no socket is opened. The
    remaining stack members are taken from the real loader (they are inert
    here -- ``run_typed_local`` uses only ``OllamaProvider``,
    ``OpenAIChatModel``, and ``Agent``).

    ``capture`` (when given) records the ``model_name`` and ``provider``
    the wrapper would have used, for post-call assertions.
    """

    def fake_openai_chat_model(model_name, *, provider):
        if capture is not None:
            capture["model_name"] = model_name
            capture["provider"] = provider
        return FunctionModel(fn, model_name=model_name)

    real = wrapper_mod._load_stack()
    fake = dataclasses.replace(real, OpenAIChatModel=fake_openai_chat_model)
    monkeypatch.setattr(wrapper_mod, "_load_stack", lambda: fake)


class TestNetworkIsolation:
    """The loader is the *only* route from this module to a real client."""

    def test_no_third_party_stack_attributes_on_module(self):
        # The old seam (`wrapper_mod.OpenAIChatModel`) must be gone, along
        # with every other module-scope stack symbol -- otherwise a future
        # edit could reintroduce a path around `_load_stack`.
        for name in (
            "OpenAIChatModel",
            "OllamaProvider",
            "AnthropicModel",
            "AnthropicProvider",
            "Agent",
            "anthropic",
        ):
            assert not hasattr(wrapper_mod, name), f"{name} leaked back to module scope"

    async def test_no_real_network_client_is_reachable(self, monkeypatch):
        """With the loader severed, no provider/model can be constructed.

        This is the discriminating form of the isolation claim: if any
        alternate path to a real ``OpenAIChatModel``/``OllamaProvider``
        existed, the call would proceed past the severed loader and try to
        reach a live Ollama daemon instead of surfacing this sentinel.
        """

        class LoaderSeveredError(RuntimeError):
            pass

        def _sever():
            raise LoaderSeveredError("no stack for you")

        monkeypatch.setattr(wrapper_mod, "_load_stack", _sever)

        with pytest.raises(LoaderSeveredError):
            await run_typed_local("route: hello", Decision)

    async def test_load_stack_import_error_raises_llm_stack_incompatible(self, monkeypatch):
        """``LLM_STACK_COMPAT_OVERRIDE=healthy`` can pass the guard while
        ``_load_stack()`` still fails; the raw ``ImportError`` must not
        bypass every existing ``except LLMCallError`` fail-safe (#3001).
        """
        from agent.llm import compat

        monkeypatch.setattr(compat, "_DEGRADED", False)
        monkeypatch.setattr(compat, "_LOADER_OK", True)
        monkeypatch.setattr(compat, "_COMPATIBLE", True)

        def _boom():
            raise ImportError("no module named anthropic")

        monkeypatch.setattr(wrapper_mod, "_load_stack", _boom)

        with pytest.raises(LLMStackIncompatible):
            await run_typed_local("route: hello", Decision)


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
