"""Unit tests for the PydanticAI non-harness LLM wrapper (#1925).

``agent.llm.run_typed`` is the single construction point for non-harness
LLM calls (classification, extraction, judging). These tests prove the
wrapper's contract without hitting the real Anthropic API:

* Structured output validates against ``output_type`` and is returned.
* PydanticAI's single auto-retry on schema mismatch fires exactly once,
  then the wrapper surfaces failure (not an infinite loop).
* Provider errors propagate as :class:`agent.llm.LLMCallError` and are
  logged, not silently swallowed.
* The shared ``agent.anthropic_client.semaphore_slot()`` is held for the
  *entire* ``Agent.run()`` call, matching the hotfix #1055/#1111
  per-call-slot invariant (Spike Results spike-1).
* The ``AsyncAnthropic`` client the wrapper constructs is the one
  PydanticAI's ``AnthropicProvider`` actually uses (injection took
  effect) -- not one PydanticAI built itself.
* The outer ``asyncio.wait_for(hard_timeout)`` bounds wall-clock time
  regardless of a larger SDK-level ``timeout`` kwarg.
* Empty/None/whitespace-only prompts fail fast with no LLM call and no
  hang.

Network isolation: every test monkeypatches ``anthropic.AsyncAnthropic``
(a fake, non-network client) and the memoized loader
``agent.llm.wrapper._load_stack`` so its ``AnthropicModel`` is PydanticAI's
``FunctionModel`` rather than a real HTTP-backed model. No test makes a
real Anthropic API call. The loader replaced the old
``wrapper_mod.AnthropicModel`` seam when the import-safety contract (#3001)
moved every third-party symbol out of the wrapper's module scope.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import logging
import subprocess

import anthropic
import pytest
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent.llm import LLMCallError, LLMStackIncompatible, run_typed
from agent.llm import wrapper as wrapper_mod


class Classification(BaseModel):
    """A minimal structured output type used across tests."""

    label: str
    confidence: float


class FakeAsyncAnthropic:
    """Stand-in for ``anthropic.AsyncAnthropic`` -- no real network I/O.

    Records every construction (api_key/timeout kwargs) and supports the
    ``async with`` protocol the wrapper relies on for hotfix #1055 httpx
    cleanup. Tests assert on ``instances`` to prove the wrapper builds a
    fresh client per call and that PydanticAI's provider ends up wired to
    *that* instance.
    """

    instances: list[FakeAsyncAnthropic] = []

    def __init__(self, *, api_key: str | None = None, timeout: float | None = None) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.closed = False
        FakeAsyncAnthropic.instances.append(self)

    async def __aenter__(self) -> FakeAsyncAnthropic:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.closed = True
        return None


class SpySemaphoreSlot:
    """Stand-in for ``agent.anthropic_client.semaphore_slot()``.

    Records enter/exit so a test can assert the slot is held for the
    *whole* wrapped call (not released before the LLM call happens).
    """

    def __init__(self) -> None:
        self.entered = False
        self.exited = False
        self.held_during_model_call: bool | None = None

    async def __aenter__(self) -> None:
        self.entered = True
        return None

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.exited = True
        return None


def _tool_response(info: AgentInfo, args: dict) -> ModelResponse:
    """Build a ``ModelResponse`` calling PydanticAI's structured-output tool."""
    tool_name = info.output_tools[0].name if info.output_tools else None
    return ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args=args)])


@pytest.fixture(autouse=True)
def _isolate_anthropic_client(monkeypatch):
    """Every test gets a network-free ``anthropic.AsyncAnthropic``."""
    FakeAsyncAnthropic.instances = []
    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeAsyncAnthropic)
    monkeypatch.setattr(wrapper_mod, "get_anthropic_api_key", lambda: "fake-test-key")
    yield


@pytest.fixture
def spy_semaphore_slot(monkeypatch):
    """Replace ``semaphore_slot`` with a recording spy; return the spy instance."""
    spy = SpySemaphoreSlot()
    monkeypatch.setattr(wrapper_mod, "semaphore_slot", lambda: spy)
    return spy


def _install_function_model(monkeypatch, fn, *, capture: dict | None = None):
    """Swap the loader's ``AnthropicModel`` for an in-process ``FunctionModel``.

    Preserves the wrapper's real ``AnthropicProvider(anthropic_client=...)``
    construction (the caller still builds and passes ``provider``); only the
    outbound HTTP-backed model is swapped for PydanticAI's in-process test
    double so no network call happens. When ``capture`` is provided, the
    ``provider`` instance PydanticAI would have used is stashed under
    ``capture["provider"]`` for post-call assertions.

    The seam is ``wrapper_mod._load_stack`` (#3001): the third-party stack
    is no longer imported at the wrapper's module scope, so
    ``wrapper_mod.AnthropicModel`` no longer exists as an attribute to
    patch. The loader is the *only* route from this module to a real model
    class, which makes this seam strictly stronger than the old one.
    """

    def fake_anthropic_model(model_name, *, provider):
        if capture is not None:
            capture["provider"] = provider
        return FunctionModel(fn, model_name=model_name)

    real = wrapper_mod._load_stack()
    fake = dataclasses.replace(real, AnthropicModel=fake_anthropic_model)
    monkeypatch.setattr(wrapper_mod, "_load_stack", lambda: fake)


class TestStructuredOutputSuccess:
    async def test_valid_response_returns_output_type_instance(self, monkeypatch):
        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return _tool_response(info, {"label": "greeting", "confidence": 0.95})

        _install_function_model(monkeypatch, fn)

        result = await run_typed("classify: hello there", Classification)

        assert isinstance(result, Classification)
        assert result.label == "greeting"
        assert result.confidence == 0.95


class TestSingleAutoRetryOnSchemaMismatch:
    async def test_retries_exactly_once_then_raises(self, monkeypatch):
        call_count = {"n": 0}

        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            call_count["n"] += 1
            # Always missing `confidence` -- never a valid Classification.
            return _tool_response(info, {"label": "x"})

        _install_function_model(monkeypatch, fn)

        with pytest.raises(LLMCallError):
            await run_typed("classify: hello there", Classification)

        # 1 initial attempt + PydanticAI's single default auto-retry = 2.
        # Not infinite, not zero.
        assert call_count["n"] == 2

    async def test_retry_recovers_on_second_attempt(self, monkeypatch):
        call_count = {"n": 0}

        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _tool_response(info, {"label": "x"})  # invalid: missing confidence
            return _tool_response(info, {"label": "x", "confidence": 0.5})  # valid

        _install_function_model(monkeypatch, fn)

        result = await run_typed("classify: hello there", Classification)

        assert result.label == "x"
        assert call_count["n"] == 2


class TestErrorSurfacing:
    async def test_provider_error_propagates_as_llm_call_error(self, monkeypatch):
        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            raise RuntimeError("simulated provider error")

        _install_function_model(monkeypatch, fn)

        with pytest.raises(LLMCallError) as exc_info:
            await run_typed("classify: hello there", Classification)

        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert "simulated provider error" in str(exc_info.value.__cause__)

    async def test_provider_error_is_logged(self, monkeypatch, caplog):
        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            raise RuntimeError("simulated provider error")

        _install_function_model(monkeypatch, fn)

        with caplog.at_level(logging.ERROR, logger="agent.llm.wrapper"):
            with pytest.raises(LLMCallError):
                await run_typed("classify: hello there", Classification)

        assert any(
            "provider error" in record.message.lower()
            or "simulated provider error" in record.message
            for record in caplog.records
        ), f"expected a logged error record, got: {[r.message for r in caplog.records]}"

    async def test_schema_exhaustion_is_logged(self, monkeypatch, caplog):
        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return _tool_response(info, {"label": "x"})  # always invalid

        _install_function_model(monkeypatch, fn)

        with caplog.at_level(logging.ERROR, logger="agent.llm.wrapper"):
            with pytest.raises(LLMCallError):
                await run_typed("classify: hello there", Classification)

        assert any(record.levelno == logging.ERROR for record in caplog.records)


class TestSemaphoreSlotAcquisition:
    async def test_slot_held_for_entire_agent_run(self, monkeypatch, spy_semaphore_slot):
        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            # Snapshot slot state *during* the simulated model call.
            spy_semaphore_slot.held_during_model_call = (
                spy_semaphore_slot.entered and not spy_semaphore_slot.exited
            )
            return _tool_response(info, {"label": "x", "confidence": 0.5})

        _install_function_model(monkeypatch, fn)

        await run_typed("classify: hello there", Classification)

        assert spy_semaphore_slot.entered is True
        assert spy_semaphore_slot.exited is True
        assert spy_semaphore_slot.held_during_model_call is True, (
            "semaphore_slot() must be held for the whole Agent.run() call, "
            "not released before the LLM call happens"
        )

    async def test_slot_released_even_on_failure(self, monkeypatch, spy_semaphore_slot):
        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            raise RuntimeError("boom")

        _install_function_model(monkeypatch, fn)

        with pytest.raises(LLMCallError):
            await run_typed("classify: hello there", Classification)

        assert spy_semaphore_slot.exited is True


class TestInjectedClientTookEffect:
    async def test_provider_client_is_the_wrapper_constructed_client(self, monkeypatch):
        capture: dict = {}

        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return _tool_response(info, {"label": "x", "confidence": 0.5})

        _install_function_model(monkeypatch, fn, capture=capture)

        await run_typed("classify: hello there", Classification)

        assert len(FakeAsyncAnthropic.instances) == 1
        wrapper_client = FakeAsyncAnthropic.instances[0]
        assert capture["provider"].client is wrapper_client, (
            "AnthropicProvider must be wired to the client run_typed constructed "
            "per-call, not one PydanticAI built itself"
        )

    async def test_fresh_client_per_call_not_shared(self, monkeypatch):
        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return _tool_response(info, {"label": "x", "confidence": 0.5})

        _install_function_model(monkeypatch, fn)

        await run_typed("first call", Classification)
        await run_typed("second call", Classification)

        assert len(FakeAsyncAnthropic.instances) == 2
        assert FakeAsyncAnthropic.instances[0] is not FakeAsyncAnthropic.instances[1]

    async def test_sdk_timeout_passed_to_constructed_client(self, monkeypatch):
        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return _tool_response(info, {"label": "x", "confidence": 0.5})

        _install_function_model(monkeypatch, fn)

        await run_typed("classify: hello there", Classification, sdk_timeout=7.5)

        assert FakeAsyncAnthropic.instances[0].timeout == 7.5


class TestHardTimeoutBound:
    async def test_slow_call_bounded_by_hard_timeout_regardless_of_sdk_timeout(self, monkeypatch):
        async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            await asyncio.sleep(5.0)  # much longer than hard_timeout below
            return _tool_response(info, {"label": "x", "confidence": 0.5})

        _install_function_model(monkeypatch, fn)

        loop = asyncio.get_event_loop()
        start = loop.time()
        with pytest.raises(LLMCallError) as exc_info:
            # sdk_timeout is deliberately much larger than hard_timeout to prove
            # the outer asyncio.wait_for bound governs, not the SDK kwarg.
            await run_typed(
                "classify: hello there",
                Classification,
                sdk_timeout=60.0,
                hard_timeout=0.2,
            )
        elapsed = loop.time() - start

        assert isinstance(exc_info.value.__cause__, TimeoutError)
        assert elapsed < 2.0, f"hard_timeout=0.2s should bound the call, took {elapsed:.2f}s"

    async def test_hard_timeout_none_disables_outer_cap(self, monkeypatch):
        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return _tool_response(info, {"label": "x", "confidence": 0.5})

        _install_function_model(monkeypatch, fn)

        result = await run_typed("classify: hello there", Classification, hard_timeout=None)
        assert result.label == "x"


class TestEmptyPromptHandling:
    @pytest.mark.parametrize("bad_prompt", ["", "   ", "\n\t  "])
    async def test_empty_or_whitespace_prompt_raises_without_llm_call(
        self, monkeypatch, spy_semaphore_slot, bad_prompt
    ):
        call_count = {"n": 0}

        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            call_count["n"] += 1
            return _tool_response(info, {"label": "x", "confidence": 0.5})

        _install_function_model(monkeypatch, fn)

        with pytest.raises(ValueError):
            await run_typed(bad_prompt, Classification)

        assert call_count["n"] == 0, "no LLM call should be attempted for a bad prompt"
        assert spy_semaphore_slot.entered is False, (
            "no semaphore slot should be acquired for a bad prompt (fail fast)"
        )

    async def test_none_prompt_raises_without_llm_call(self, monkeypatch, spy_semaphore_slot):
        call_count = {"n": 0}

        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            call_count["n"] += 1
            return _tool_response(info, {"label": "x", "confidence": 0.5})

        _install_function_model(monkeypatch, fn)

        with pytest.raises(ValueError):
            await run_typed(None, Classification)  # type: ignore[arg-type]

        assert call_count["n"] == 0
        assert spy_semaphore_slot.entered is False

    async def test_bad_prompt_does_not_hang(self, monkeypatch):
        """Bounded wait proves the failure is immediate, not a stall."""

        async def never_call_this(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            await asyncio.sleep(999)
            raise AssertionError("should never reach the model call")

        _install_function_model(monkeypatch, never_call_this)

        with pytest.raises(ValueError):
            await asyncio.wait_for(run_typed("", Classification), timeout=2.0)


class TestDefaultModelFromConfig:
    async def test_default_model_is_config_model_fast(self, monkeypatch):
        from config.models import MODEL_FAST

        seen_model_name = {}

        def fake_anthropic_model(model_name, *, provider):
            seen_model_name["value"] = model_name

            def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
                return _tool_response(info, {"label": "x", "confidence": 0.5})

            return FunctionModel(fn, model_name=model_name)

        real = wrapper_mod._load_stack()
        fake = dataclasses.replace(real, AnthropicModel=fake_anthropic_model)
        monkeypatch.setattr(wrapper_mod, "_load_stack", lambda: fake)

        await run_typed("classify: hello there", Classification)

        assert seen_model_name["value"] == MODEL_FAST


class TestDegradedStack:
    """A degraded LLM stack fails fast, typed, before any client work (#3001)."""

    def test_typed_exception_preserves_existing_fail_safes(self):
        # Subclassing is the whole compatibility story: every existing
        # `except LLMCallError` site keeps its own conservative default.
        assert issubclass(LLMStackIncompatible, LLMCallError)

    async def test_run_typed_raises_llm_stack_incompatible(self, monkeypatch):
        from agent.llm import compat

        def never_call_this(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            raise AssertionError("a degraded stack must not reach the model call")

        _install_function_model(monkeypatch, never_call_this)
        monkeypatch.setattr(compat, "_DEGRADED", True)
        monkeypatch.setattr(compat, "_LOADER_OK", True)
        monkeypatch.setattr(compat, "_COMPATIBLE", False)

        with pytest.raises(LLMStackIncompatible):
            await run_typed("classify: hello there", Classification)

    async def test_load_stack_import_error_raises_llm_stack_incompatible(self, monkeypatch):
        """``_guard_stack`` can pass while ``_load_stack()`` still fails.

        ``LLM_STACK_COMPAT_OVERRIDE=healthy`` short-circuits the guard
        before the predicate ever runs, so a genuinely broken stack can
        still reach ``_load_stack()``. A raw ``ImportError`` there would
        bypass every existing ``except LLMCallError`` fail-safe -- exactly
        the property ``LLMStackIncompatible`` exists to preserve.
        """
        from agent.llm import compat

        monkeypatch.setattr(compat, "_DEGRADED", False)
        monkeypatch.setattr(compat, "_LOADER_OK", True)
        monkeypatch.setattr(compat, "_COMPATIBLE", True)

        def _boom():
            raise ImportError("no module named anthropic")

        monkeypatch.setattr(wrapper_mod, "_load_stack", _boom)

        with pytest.raises(LLMStackIncompatible):
            await run_typed("classify: hello there", Classification)


class TestSkipGuardSingleCallSite:
    """``_skip_guard`` (wrapper.py:146) is documented-not-enforced (review nit).

    A future caller passing ``_skip_guard=True`` bypasses ``_guard_stack``: on
    a genuinely degraded stack no alert fires, no marker is written, and a
    signature break surfaces as a generic ``LLMCallError`` instead of the
    diagnostic ``LLMStackIncompatible``. The parameter's docstring says
    "internal-only (#3001)" but nothing in the code enforces that -- this
    test is the enforcement, keyed on the real call graph rather than a
    trusted comment. It scans **tracked** source only (``git ls-files``),
    matching ``test_hub_alias_references.py``'s convention: a stale
    ``__pycache__`` embeds string literals verbatim and would produce an
    unreproducible phantom hit in a fresh checkout (#2807).

    AST-based, not a text grep: ``_skip_guard`` appears in several
    docstrings (this module's own module-level comment included) that
    quote the keyword-argument spelling as prose. A text grep for
    ``_skip_guard=True`` would count those as call sites; only an actual
    ``ast.Call`` keyword argument node is one.
    """

    def _skip_guard_call_sites(self) -> list[str]:
        """``"path.py:lineno"`` for every ``_skip_guard=...`` keyword argument
        in a ``Call`` node, across every tracked ``*.py`` file."""
        tracked = subprocess.run(
            ["git", "ls-files", "*.py"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()

        sites: list[str] = []
        for path in tracked:
            try:
                source = open(path, encoding="utf-8").read()
                tree = ast.parse(source, filename=path)
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg == "_skip_guard":
                        sites.append(f"{path}:{node.lineno}")
        return sites

    def test_skip_guard_has_exactly_one_call_site_outside_wrapper(self):
        sites = self._skip_guard_call_sites()
        outside_wrapper = [s for s in sites if not s.startswith("agent/llm/wrapper.py:")]

        assert len(outside_wrapper) == 1, (
            f"expected exactly one `_skip_guard=` call site outside wrapper.py, "
            f"found {len(outside_wrapper)}: {outside_wrapper}. `_skip_guard` is "
            "meant to be reached from exactly one place: the compat gate's "
            "`_check_network` probe. A new hit here means a caller outside the "
            "compat gate now bypasses `_guard_stack` -- confirm that is "
            "intentional before letting this test move."
        )
        assert outside_wrapper[0].startswith("agent/llm/compat.py:"), (
            f"the one call site outside wrapper.py should be the compat gate's "
            f"_check_network probe, found {outside_wrapper[0]!r} instead"
        )
