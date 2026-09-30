"""Unit tests for the PydanticAI non-harness LLM wrapper (#1925, #3410).

``agent.llm.run_typed`` is the single entry point for non-harness LLM
calls (classification, extraction, judging). These tests prove the
wrapper's contract, driven end to end through the Anthropic leg
(``agent/llm/backends/anthropic.py``), without hitting the real API:

* Structured output validates against ``output_type`` and is returned.
* PydanticAI's single auto-retry on schema mismatch fires exactly once,
  then the wrapper surfaces failure (not an infinite loop).
* Provider errors propagate as :class:`agent.llm.LLMCallError` and are
  logged, not silently swallowed.
* The shared ``agent.anthropic_client.semaphore_slot()`` is held for the
  *entire* ``Agent.run()`` call, matching the hotfix #1055/#1111
  per-call-slot invariant (Spike Results spike-1).
* The ``AsyncAnthropic`` client the leg constructs is the one
  PydanticAI's ``AnthropicProvider`` actually uses (injection took
  effect) -- not one PydanticAI built itself.
* The outer ``asyncio.wait_for(hard_timeout)`` is applied by the wrapper,
  outside the leg, and bounds wall-clock time regardless of a larger
  SDK-level ``timeout`` kwarg.
* Empty/None/whitespace-only prompts fail fast with no LLM call and no
  hang, and a call without ``task=`` raises ``TypeError`` naming it.
* Routing and the fallback budget (Task 2), with both legs faked at the
  ``_LEGS`` table and the wrapper's ``monotonic`` patched: the per-backend
  SDK timer read from ``settings.timeouts``, the fallback's ``sdk_timeout``
  / ``slot_timeout`` / ``max_retries=0`` / ``deadline``, the skip under
  0.5 s of budget, and the ``llm_route`` / ``llm_fallback`` log lines.

Network isolation: every test monkeypatches ``anthropic.AsyncAnthropic``
(a fake, non-network client) and the memoized loader
``agent.llm.wrapper._load_stack`` so its ``AnthropicModel`` is PydanticAI's
``FunctionModel`` rather than a real HTTP-backed model. The wrapper hands
that stack to the leg, so the one seam covers both. No test makes a real
Anthropic API call. The leg-only behaviours (slot starvation, the deadline
re-check) live in ``tests/unit/test_llm_backend_anthropic.py``.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import logging
import re
import subprocess

import anthropic
import pytest
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent.llm import LLMCallError, LLMStackIncompatible, LLMTask, run_typed
from agent.llm import wrapper as wrapper_mod
from agent.llm.backends import anthropic as anthropic_leg
from agent.llm.tasks import Backend, TaskKind

THINK = LLMTask(site="test.think", kind=TaskKind.THINKING, backend=Backend.ANTHROPIC)


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
    monkeypatch.setattr(anthropic_leg, "get_anthropic_api_key", lambda: "fake-test-key")
    yield


@pytest.fixture
def spy_semaphore_slot(monkeypatch):
    """Replace ``semaphore_slot`` with a recording spy; return the spy instance."""
    spy = SpySemaphoreSlot()
    monkeypatch.setattr(anthropic_leg, "semaphore_slot", lambda timeout=None: spy)
    return spy


def _install_function_model(monkeypatch, fn, *, capture: dict | None = None):
    """Swap the loader's ``AnthropicModel`` for an in-process ``FunctionModel``.

    Preserves the leg's real ``AnthropicProvider(anthropic_client=...)``
    construction (the leg still builds and passes ``provider``); only the
    outbound HTTP-backed model is swapped for PydanticAI's in-process test
    double so no network call happens. When ``capture`` is provided, the
    ``provider`` instance PydanticAI would have used is stashed under
    ``capture["provider"]`` for post-call assertions.

    The seam is ``wrapper_mod._load_stack`` (#3001): the wrapper resolves
    the stack once per call and hands it to the leg, so the loader is the
    *only* route from either module to a real model class.
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

        result = await run_typed("classify: hello there", Classification, task=THINK)

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
            await run_typed("classify: hello there", Classification, task=THINK)

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

        result = await run_typed("classify: hello there", Classification, task=THINK)

        assert result.label == "x"
        assert call_count["n"] == 2


class TestErrorSurfacing:
    async def test_provider_error_propagates_as_llm_call_error(self, monkeypatch):
        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            raise RuntimeError("simulated provider error")

        _install_function_model(monkeypatch, fn)

        with pytest.raises(LLMCallError) as exc_info:
            await run_typed("classify: hello there", Classification, task=THINK)

        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert "simulated provider error" in str(exc_info.value.__cause__)

    async def test_provider_error_is_logged(self, monkeypatch, caplog):
        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            raise RuntimeError("simulated provider error")

        _install_function_model(monkeypatch, fn)

        with caplog.at_level(logging.ERROR, logger="agent.llm.wrapper"):
            with pytest.raises(LLMCallError):
                await run_typed("classify: hello there", Classification, task=THINK)

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
                await run_typed("classify: hello there", Classification, task=THINK)

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

        await run_typed("classify: hello there", Classification, task=THINK)

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
            await run_typed("classify: hello there", Classification, task=THINK)

        assert spy_semaphore_slot.exited is True


class TestInjectedClientTookEffect:
    async def test_provider_client_is_the_wrapper_constructed_client(self, monkeypatch):
        capture: dict = {}

        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return _tool_response(info, {"label": "x", "confidence": 0.5})

        _install_function_model(monkeypatch, fn, capture=capture)

        await run_typed("classify: hello there", Classification, task=THINK)

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

        await run_typed("first call", Classification, task=THINK)
        await run_typed("second call", Classification, task=THINK)

        assert len(FakeAsyncAnthropic.instances) == 2
        assert FakeAsyncAnthropic.instances[0] is not FakeAsyncAnthropic.instances[1]

    async def test_sdk_timeout_passed_to_constructed_client(self, monkeypatch):
        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return _tool_response(info, {"label": "x", "confidence": 0.5})

        _install_function_model(monkeypatch, fn)

        await run_typed("classify: hello there", Classification, task=THINK, sdk_timeout=7.5)

        assert FakeAsyncAnthropic.instances[0].timeout == 7.5


class TestHardTimeoutBound:
    """``hard_timeout`` is the wrapper's, applied outside the leg (#3410)."""

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
                task=THINK,
                sdk_timeout=60.0,
                hard_timeout=0.2,
            )
        elapsed = loop.time() - start

        assert isinstance(exc_info.value.__cause__, TimeoutError)
        assert exc_info.value.reason == "timeout"
        assert elapsed < 2.0, f"hard_timeout=0.2s should bound the call, took {elapsed:.2f}s"

    async def test_hard_timeout_none_disables_outer_cap(self, monkeypatch):
        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return _tool_response(info, {"label": "x", "confidence": 0.5})

        _install_function_model(monkeypatch, fn)

        result = await run_typed(
            "classify: hello there", Classification, task=THINK, hard_timeout=None
        )
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
            await run_typed(bad_prompt, Classification, task=THINK)

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
            await run_typed(None, Classification, task=THINK)  # type: ignore[arg-type]

        assert call_count["n"] == 0
        assert spy_semaphore_slot.entered is False

    async def test_bad_prompt_does_not_hang(self, monkeypatch):
        """Bounded wait proves the failure is immediate, not a stall."""

        async def never_call_this(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            await asyncio.sleep(999)
            raise AssertionError("should never reach the model call")

        _install_function_model(monkeypatch, never_call_this)

        with pytest.raises(ValueError):
            await asyncio.wait_for(run_typed("", Classification, task=THINK), timeout=2.0)


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

        await run_typed("classify: hello there", Classification, task=THINK)

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
            await run_typed("classify: hello there", Classification, task=THINK)

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
            await run_typed("classify: hello there", Classification, task=THINK)


class TestSkipGuardSingleCallSite:
    """``_skip_guard`` on ``run_typed`` is documented-not-enforced (review nit).

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


# ---------------------------------------------------------------------------
# Routing, per-backend timers and the fallback budget (#3410, Task 2)
# ---------------------------------------------------------------------------
#
# These drive the wrapper with both legs faked at the ``_LEGS`` table, so
# they see exactly the kwargs the wrapper hands a leg and nothing below it.
# ``wrapper_mod.monotonic`` is the wrapper's clock; patching it makes the
# budget arithmetic deterministic.

from agent.llm.tasks import ErrorCost  # noqa: E402
from config.settings import settings  # noqa: E402
from tools import improvement_eligibility  # noqa: E402

C1 = LLMTask(
    site="test.c1", kind=TaskKind.CLASSIFICATION, backend=Backend.OLLAMA, error_cost=ErrorCost.HIGH
)
C1_ON_ANTHROPIC = LLMTask(site="test.c1a", kind=TaskKind.CLASSIFICATION, backend=Backend.ANTHROPIC)
C1_ON_ENCODER = LLMTask(
    site="test.c1e", kind=TaskKind.CLASSIFICATION, backend=Backend.LOCAL_ENCODER
)
C1_ON_DECISIONS = LLMTask(
    site="test.c1d",
    kind=TaskKind.CLASSIFICATION,
    backend=Backend.DECISIONS,
    error_cost=ErrorCost.HIGH,
)


class _Clock:
    """A fake ``monotonic`` the leg fakes can advance."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class _LegFake:
    """Records every call the wrapper makes; raises or returns on demand.

    ``result`` overrides the default answer (``label="ok", confidence=1.0``
    on ``output_type``) so a test can return an instance without a
    ``confidence`` attribute.
    """

    def __init__(
        self, *, raise_with=None, advance: float = 0.0, clock: _Clock | None = None, result=None
    ):
        self.calls: list[dict] = []
        self.raise_with = raise_with
        self.advance = advance
        self.clock = clock
        self.result = result

    async def __call__(self, prompt, output_type, route, **kwargs):
        self.calls.append({"prompt": prompt, "route": route, **kwargs})
        if self.clock is not None:
            self.clock.now += self.advance
        if self.raise_with is not None:
            raise self.raise_with
        if self.result is not None:
            return self.result
        return output_type(label="ok", confidence=1.0)


@pytest.fixture
def legs(monkeypatch):
    """Every leg faked; ``_install(ollama, anthropic, encoder=None)`` returns the pair
    (plus the encoder fake as a third element when one is given)."""
    improvement_eligibility._clear_cache()

    def _no_gh(*args, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(improvement_eligibility.subprocess, "run", _no_gh)

    def _install(ollama: _LegFake, anthropic_fake, encoder: _LegFake | None = None):
        monkeypatch.setitem(wrapper_mod._LEGS, Backend.OLLAMA, ollama)
        monkeypatch.setitem(wrapper_mod._LEGS, Backend.ANTHROPIC, anthropic_fake)
        monkeypatch.setitem(wrapper_mod._LEGS, Backend.LOCAL_ENCODER, encoder or _LegFake())
        if encoder is None:
            return ollama, anthropic_fake
        return ollama, anthropic_fake, encoder

    yield _install
    improvement_eligibility._clear_cache()


class TestRequiredTask:
    async def test_missing_task_raises_type_error_naming_the_kwarg(self, legs):
        legs(_LegFake(), _LegFake())
        with pytest.raises(TypeError, match="task"):
            await run_typed("classify: hello", Classification)  # type: ignore[call-arg]

    async def test_task_none_raises_type_error_naming_the_kwarg(self, legs):
        ollama, anth = legs(_LegFake(), _LegFake())
        with pytest.raises(TypeError, match="task"):
            await run_typed("classify: hello", Classification, task=None)  # type: ignore[arg-type]
        assert ollama.calls == [] and anth.calls == []

    async def test_empty_prompt_is_checked_before_task(self, legs):
        legs(_LegFake(), _LegFake())
        with pytest.raises(ValueError):
            await run_typed("   ", Classification, task=None)  # type: ignore[arg-type]


class TestPerBackendSdkTimer:
    """The leg's timer comes from ``settings.timeouts``, unless the caller says."""

    @pytest.mark.parametrize(
        ("task", "key", "sdk_timeout", "backend", "expected_attr"),
        [
            pytest.param(C1, "valor", None, Backend.OLLAMA, "local_typed_hard_s", id="c12-shaped"),
            pytest.param(
                C1_ON_ANTHROPIC, "valor", None, Backend.ANTHROPIC, "anthropic_sdk_s", id="c1-anth"
            ),
            pytest.param(C1, "acme", None, Backend.ANTHROPIC, "anthropic_sdk_s", id="client-key"),
            pytest.param(C1, None, None, Backend.ANTHROPIC, "anthropic_sdk_s", id="no-key"),
            pytest.param(C1, "valor", 3.0, Backend.OLLAMA, None, id="c8-shaped-ollama"),
            pytest.param(C1, "acme", 3.0, Backend.ANTHROPIC, None, id="c8-shaped-anthropic"),
            pytest.param(
                C1_ON_ENCODER,
                "valor",
                None,
                Backend.LOCAL_ENCODER,
                "local_typed_hard_s",
                id="encoder-default",
            ),
            pytest.param(
                C1_ON_ENCODER, "valor", 3.0, Backend.LOCAL_ENCODER, None, id="encoder-explicit"
            ),
            pytest.param(
                C1_ON_ENCODER,
                "acme",
                None,
                Backend.ANTHROPIC,
                "anthropic_sdk_s",
                id="encoder-client",
            ),
        ],
    )
    async def test_leg_receives_its_timer(
        self, legs, task, key, sdk_timeout, backend, expected_attr
    ):
        fakes = dict(
            zip(
                (Backend.OLLAMA, Backend.ANTHROPIC, Backend.LOCAL_ENCODER),
                legs(_LegFake(), _LegFake(), _LegFake()),
                strict=True,
            )
        )
        await run_typed(
            "classify: hello", Classification, task=task, project_key=key, sdk_timeout=sdk_timeout
        )
        called = fakes.pop(backend)
        assert len(called.calls) == 1 and all(idle.calls == [] for idle in fakes.values())
        expected = (
            sdk_timeout if expected_attr is None else getattr(settings.timeouts, expected_attr)
        )
        assert called.calls[0]["sdk_timeout"] == expected
        assert called.calls[0]["route"].backend is backend

    async def test_primary_leg_gets_no_deadline_and_the_callers_retries(self, legs):
        ollama, _ = legs(_LegFake(), _LegFake())
        await run_typed(
            "classify: hello",
            Classification,
            task=C1,
            project_key="valor",
            max_retries=4,
            slot_timeout=2.5,
            system="be brief",
        )
        call = ollama.calls[0]
        assert call["deadline"] is None
        assert call["max_retries"] == 4
        assert call["slot_timeout"] == 2.5
        assert call["system"] == "be brief"
        assert call["stack"] is not None


class TestFallbackBudget:
    """Data Flow step 6 on a faked clock."""

    async def test_c1_timeout_on_granite_gets_one_haiku_attempt_in_the_remainder(
        self, legs, monkeypatch, caplog
    ):
        clock = _Clock(start=1000.0)
        monkeypatch.setattr(wrapper_mod, "monotonic", clock)
        ollama, anth = legs(
            _LegFake(raise_with=LLMCallError("slow", reason="timeout"), advance=20.0, clock=clock),
            _LegFake(clock=clock, advance=1.0),
        )

        with caplog.at_level(logging.INFO, logger="agent.llm.wrapper"):
            result = await run_typed(
                "classify: hello", Classification, task=C1, project_key="valor"
            )

        assert result.label == "ok"
        assert len(ollama.calls) == 1 and len(anth.calls) == 1
        fb = anth.calls[0]
        assert fb["sdk_timeout"] == 15.0
        assert fb["slot_timeout"] == 15.0
        assert fb["max_retries"] == 0
        assert fb["deadline"] == 1000.0 + 35.0
        assert fb["route"].backend is Backend.ANTHROPIC and fb["route"].fallback is None

        messages = [r.getMessage() for r in caplog.records if r.name == "agent.llm.wrapper"]
        fallback_lines = [m for m in messages if m.startswith("llm_fallback site=")]
        route_lines = [m for m in messages if m.startswith("llm_route site=")]
        assert fallback_lines == [
            "llm_fallback site=test.c1 primary=ollama fallback=anthropic reason=timeout "
            "elapsed_ms=20000"
        ]
        # ``Classification`` carries ``confidence``, so the route line ends with it (#3420).
        assert route_lines == [
            "llm_route site=test.c1 backend=anthropic elapsed_ms=21000 confidence=1.000"
        ]
        assert messages.index(fallback_lines[0]) < messages.index(route_lines[0])
        warning = next(r for r in caplog.records if r.getMessage() == fallback_lines[0])
        assert warning.levelno == logging.WARNING
        info = next(r for r in caplog.records if r.getMessage() == route_lines[0])
        assert info.levelno == logging.INFO

    async def test_callers_slot_timeout_is_capped_by_the_remainder(self, legs, monkeypatch):
        clock = _Clock()
        monkeypatch.setattr(wrapper_mod, "monotonic", clock)
        _, anth = legs(
            _LegFake(
                raise_with=LLMCallError("down", reason="transport"), advance=10.0, clock=clock
            ),
            _LegFake(),
        )
        await run_typed(
            "classify: hello", Classification, task=C1, project_key="valor", slot_timeout=60.0
        )
        # budget 35 - 10 elapsed = 25 remaining; min(anthropic_sdk_s=30, 25) = 25.
        assert anth.calls[0]["sdk_timeout"] == 25.0
        assert anth.calls[0]["slot_timeout"] == 25.0

    async def test_explicit_sdk_timeout_is_the_budget(self, legs, monkeypatch, caplog):
        """A 3 s site: the fallback gets what is left of the caller's 3 s."""
        clock = _Clock(start=1000.0)
        monkeypatch.setattr(wrapper_mod, "monotonic", clock)
        ollama, anth = legs(
            _LegFake(raise_with=LLMCallError("down", reason="transport"), advance=1.0, clock=clock),
            _LegFake(),
        )
        with caplog.at_level(logging.WARNING, logger="agent.llm.wrapper"):
            await run_typed(
                "classify: hello",
                Classification,
                task=C1,
                project_key="valor",
                sdk_timeout=3.0,
                hard_timeout=None,
            )
        fb = anth.calls[0]
        assert fb["sdk_timeout"] == 2.0
        assert fb["slot_timeout"] == 2.0
        assert fb["deadline"] == 1000.0 + 3.0
        assert any(
            r.getMessage().startswith("llm_fallback site=test.c1 primary=ollama fallback=anthropic")
            for r in caplog.records
        )

    async def test_spent_budget_skips_the_fallback_and_raises_the_primary_error(
        self, legs, monkeypatch, caplog
    ):
        clock = _Clock()
        monkeypatch.setattr(wrapper_mod, "monotonic", clock)
        primary_error = LLMCallError("slow", reason="timeout")
        ollama, anth = legs(
            _LegFake(raise_with=primary_error, advance=34.6, clock=clock), _LegFake()
        )
        with caplog.at_level(logging.INFO, logger="agent.llm.wrapper"):
            with pytest.raises(LLMCallError) as exc_info:
                await run_typed("classify: hello", Classification, task=C1, project_key="valor")
        assert exc_info.value is primary_error
        assert anth.calls == []
        messages = [r.getMessage() for r in caplog.records]
        assert not any(m.startswith("llm_fallback site=") for m in messages)
        assert not any(m.startswith("llm_route site=") for m in messages)

    async def test_uncapped_budget_gives_the_fallback_its_default_timer(self, legs, monkeypatch):
        clock = _Clock()
        monkeypatch.setattr(wrapper_mod, "monotonic", clock)
        _, anth = legs(
            _LegFake(raise_with=LLMCallError("down"), advance=500.0, clock=clock), _LegFake()
        )
        await run_typed(
            "classify: hello", Classification, task=C1, project_key="valor", hard_timeout=None
        )
        fb = anth.calls[0]
        assert fb["sdk_timeout"] == settings.timeouts.anthropic_sdk_s
        assert fb["slot_timeout"] == settings.timeouts.anthropic_sdk_s
        assert fb["deadline"] is None

    async def test_fallback_runs_once_and_its_error_propagates(self, legs):
        fallback_error = LLMCallError("anthropic down too", reason="transport")
        ollama, anth = legs(
            _LegFake(raise_with=LLMCallError("down")), _LegFake(raise_with=fallback_error)
        )
        with pytest.raises(LLMCallError) as exc_info:
            await run_typed("classify: hello", Classification, task=C1, project_key="valor")
        assert exc_info.value is fallback_error
        assert len(ollama.calls) == 1 and len(anth.calls) == 1

    async def test_no_fallback_on_an_anthropic_route(self, legs):
        error = LLMCallError("down")
        ollama, anth = legs(_LegFake(), _LegFake(raise_with=error))
        with pytest.raises(LLMCallError) as exc_info:
            await run_typed("classify: hello", Classification, task=C1, project_key="acme")
        assert exc_info.value is error
        assert ollama.calls == [] and len(anth.calls) == 1

    async def test_primary_success_logs_one_route_line_and_no_fallback(self, legs, caplog):
        ollama, anth = legs(_LegFake(), _LegFake())
        with caplog.at_level(logging.INFO, logger="agent.llm.wrapper"):
            await run_typed("classify: hello", Classification, task=C1, project_key="valor")
        messages = [r.getMessage() for r in caplog.records if r.name == "agent.llm.wrapper"]
        route_lines = [m for m in messages if m.startswith("llm_route site=test.c1 backend=ollama")]
        assert len(route_lines) == 1
        assert route_lines[0].startswith("llm_route site=test.c1 backend=ollama elapsed_ms=")
        assert not any(m.startswith("llm_fallback") for m in messages)
        assert anth.calls == []

    async def test_encoder_leg_error_falls_back_to_anthropic_inside_the_budget(
        self, legs, monkeypatch, caplog
    ):
        """Lane B: the encoder leg raises ``transport`` (no extra, no weights, no head) and
        Haiku answers once in what is left of the budget, with both log lines."""
        clock = _Clock(start=1000.0)
        monkeypatch.setattr(wrapper_mod, "monotonic", clock)
        ollama, anth, encoder = legs(
            _LegFake(),
            _LegFake(clock=clock, advance=1.0),
            _LegFake(
                raise_with=LLMCallError("no head", reason="transport"), advance=0.5, clock=clock
            ),
        )

        with caplog.at_level(logging.INFO, logger="agent.llm.wrapper"):
            result = await run_typed(
                "classify: hello",
                Classification,
                task=C1_ON_ENCODER,
                project_key="valor",
                sdk_timeout=3.0,
                hard_timeout=None,
            )

        assert result.label == "ok"
        assert len(encoder.calls) == 1 and len(anth.calls) == 1 and ollama.calls == []
        assert encoder.calls[0]["route"].model == C1_ON_ENCODER.site
        fb = anth.calls[0]
        assert fb["sdk_timeout"] == 2.5
        assert fb["max_retries"] == 0
        assert fb["deadline"] == 1000.0 + 3.0
        messages = [r.getMessage() for r in caplog.records if r.name == "agent.llm.wrapper"]
        assert messages == [
            "llm_fallback site=test.c1e primary=local_encoder fallback=anthropic "
            "reason=transport elapsed_ms=500",
            "llm_route site=test.c1e backend=anthropic elapsed_ms=1500 confidence=1.000",
        ]

    async def test_hard_timeout_caps_primary_and_fallback_together(self, legs):
        async def slow(prompt, output_type, route, **kwargs):
            await asyncio.sleep(5.0)
            raise AssertionError("unreachable")

        ollama, _ = legs(_LegFake(raise_with=LLMCallError("down")), slow)
        loop = asyncio.get_event_loop()
        started = loop.time()
        with pytest.raises(LLMCallError) as exc_info:
            await run_typed(
                "classify: hello", Classification, task=C1, project_key="valor", hard_timeout=1.0
            )
        assert exc_info.value.reason == "timeout"
        assert isinstance(exc_info.value.__cause__, TimeoutError)
        assert len(ollama.calls) == 1
        assert loop.time() - started < 3.0, "the outer cap must cover the fallback leg too"


class TestRouteLineConfidence:
    """The ``confidence=`` token on ``llm_route`` (#3420): present only with the attribute."""

    async def test_a_result_with_confidence_logs_it_to_three_decimals(self, legs, caplog):
        encoder = _LegFake(result=Classification(label="bind", confidence=0.91))
        legs(_LegFake(), _LegFake(), encoder)
        with caplog.at_level(logging.INFO, logger="agent.llm.wrapper"):
            await run_typed(
                "classify: hello", Classification, task=C1_ON_ENCODER, project_key="valor"
            )
        route_lines = [
            r.getMessage()
            for r in caplog.records
            if r.name == "agent.llm.wrapper" and r.getMessage().startswith("llm_route ")
        ]
        assert len(route_lines) == 1
        assert re.fullmatch(
            r"llm_route site=test\.c1e backend=local_encoder elapsed_ms=\d+ confidence=0\.910",
            route_lines[0],
        ), route_lines[0]

    async def test_a_result_without_the_attribute_logs_the_lane_a_line_byte_for_byte(
        self, legs, caplog
    ):
        class Bare(BaseModel):
            label: str

        legs(_LegFake(result=Bare(label="ok")), _LegFake())
        with caplog.at_level(logging.INFO, logger="agent.llm.wrapper"):
            await run_typed("classify: hello", Bare, task=C1, project_key="valor")
        route_lines = [
            r.getMessage()
            for r in caplog.records
            if r.name == "agent.llm.wrapper" and r.getMessage().startswith("llm_route ")
        ]
        assert len(route_lines) == 1
        assert re.fullmatch(
            r"llm_route site=test\.c1 backend=ollama elapsed_ms=\d+", route_lines[0]
        ), route_lines[0]

    async def test_confidence_none_logs_the_lane_a_line(self, legs, caplog):
        class Maybe(BaseModel):
            label: str
            confidence: float | None = None

        legs(_LegFake(result=Maybe(label="ok")), _LegFake())
        with caplog.at_level(logging.INFO, logger="agent.llm.wrapper"):
            await run_typed("classify: hello", Maybe, task=C1, project_key="valor")
        route_lines = [
            r.getMessage()
            for r in caplog.records
            if r.name == "agent.llm.wrapper" and r.getMessage().startswith("llm_route ")
        ]
        assert route_lines and "confidence" not in route_lines[0]


class TestDecisionsRoute:
    """Rule 5 through the wrapper (#3421): Jev primary, granite fallback, no third leg."""

    def test_legs_table_covers_every_backend(self):
        assert set(wrapper_mod._LEGS) == set(Backend)

    def test_the_decisions_entry_is_the_decisions_leg(self):
        from agent.llm.backends import decisions as decisions_leg

        assert wrapper_mod._LEGS[Backend.DECISIONS] is decisions_leg.call

    async def test_decisions_transport_error_falls_to_ollama_inside_the_budget(
        self, legs, monkeypatch, caplog
    ):
        clock = _Clock(start=1000.0)
        monkeypatch.setattr(wrapper_mod, "monotonic", clock)
        ollama, anth = legs(_LegFake(clock=clock, advance=1.0), _LegFake())
        decisions = _LegFake(
            raise_with=LLMCallError("403", reason="transport"), advance=0.5, clock=clock
        )
        monkeypatch.setitem(wrapper_mod._LEGS, Backend.DECISIONS, decisions)

        with caplog.at_level(logging.INFO, logger="agent.llm.wrapper"):
            result = await run_typed(
                "classify: hello", Classification, task=C1_ON_DECISIONS, project_key="valor"
            )

        assert result.label == "ok"
        assert len(decisions.calls) == 1 and len(ollama.calls) == 1 and anth.calls == []
        primary = decisions.calls[0]
        assert primary["route"].backend is Backend.DECISIONS
        assert primary["sdk_timeout"] == settings.timeouts.decisions_sdk_s
        assert primary["deadline"] is None
        fb = ollama.calls[0]
        assert fb["route"].backend is Backend.OLLAMA and fb["route"].fallback is None
        # budget 35 - 0.5 elapsed = 34.5; min(local_typed_hard_s=20, 34.5) = 20.
        assert fb["sdk_timeout"] == min(settings.timeouts.local_typed_hard_s, 34.5)
        assert fb["max_retries"] == 0
        assert fb["deadline"] == 1000.0 + 35.0

        messages = [r.getMessage() for r in caplog.records if r.name == "agent.llm.wrapper"]
        assert messages == [
            "llm_fallback site=test.c1d primary=decisions fallback=ollama reason=transport "
            "elapsed_ms=500",
            "llm_route site=test.c1d backend=ollama elapsed_ms=1500 confidence=1.000",
        ]

    async def test_decisions_timeout_with_the_budget_spent_raises_the_primary_error(
        self, legs, monkeypatch, caplog
    ):
        clock = _Clock(start=1000.0)
        monkeypatch.setattr(wrapper_mod, "monotonic", clock)
        ollama, anth = legs(_LegFake(), _LegFake())
        primary_error = LLMCallError("slow", reason="timeout")
        decisions = _LegFake(raise_with=primary_error, advance=2.7, clock=clock)
        monkeypatch.setitem(wrapper_mod._LEGS, Backend.DECISIONS, decisions)

        with caplog.at_level(logging.INFO, logger="agent.llm.wrapper"):
            with pytest.raises(LLMCallError) as exc_info:
                await run_typed(
                    "classify: hello",
                    Classification,
                    task=C1_ON_DECISIONS,
                    project_key="valor",
                    sdk_timeout=3.0,
                    hard_timeout=None,
                )

        assert exc_info.value is primary_error
        assert len(decisions.calls) == 1 and ollama.calls == [] and anth.calls == []
        messages = [r.getMessage() for r in caplog.records if r.name == "agent.llm.wrapper"]
        assert messages == [
            "llm_no_fallback site=test.c1d primary=decisions reason=timeout elapsed_ms=2700 "
            "budget_s=3.0"
        ]

    async def test_a_client_key_on_a_decisions_site_runs_anthropic_with_no_fallback(
        self, legs, monkeypatch
    ):
        ollama, anth = legs(_LegFake(), _LegFake())
        decisions = _LegFake()
        monkeypatch.setitem(wrapper_mod._LEGS, Backend.DECISIONS, decisions)
        await run_typed("classify: hello", Classification, task=C1_ON_DECISIONS, project_key="acme")
        assert decisions.calls == [] and ollama.calls == [] and len(anth.calls) == 1
        assert anth.calls[0]["route"].fallback is None
        assert anth.calls[0]["sdk_timeout"] == settings.timeouts.anthropic_sdk_s
