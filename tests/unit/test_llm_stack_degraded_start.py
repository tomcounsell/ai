"""The degraded posture: start degraded, alert loudly (#3001).

The owner's recorded decision is that an incompatible LLM stack at bridge or
worker startup does **not** exit the process — degraded-but-running is what
hid the 2026-08-24 incident for six hours, and exiting would trade a silent
LLM outage for a total outage plus a launchd crash-loop. So **the alert is
the entire safety property**: if it does not fire, this lane shipped nothing.
Every case below is about the alert firing, reaching all three transports,
and clearing correctly on the way back to healthy.

The alert is bound to *flag resolution*, not to startup, which is why the
no-startup-hook path and the "alert fires while ``run_typed`` raises"
independence case are here: no entry path can reach the broken stack without
alarming, and the boot-vs-first-call ordering race is designed away.

Marker redirection comes from the autouse ``tests/unit/conftest.py``
fixture, so no case here monkeypatches ``_MARKER_DIR`` by hand. One case
asserts the *default* by resolving it from ``compat.__file__`` directly.
"""

from __future__ import annotations

import ast
import json
import logging
import sys
from pathlib import Path

import pytest
import sentry_sdk

from agent.llm import compat
from agent.llm import wrapper as wrapper_mod
from agent.llm.wrapper import LLMCallError, LLMStackIncompatible

REPO_ROOT = Path(__file__).resolve().parents[2]

BAD_SIGNATURE_REASON = "pydantic_ai forwards 1 keyword(s) that anthropic 0.0.0 does not accept"


def _signature_break() -> compat.CompatResult:
    """Loader fine, Anthropic create signature moved — the pinned pair's mode."""
    return compat.CompatResult(
        compatible=False,
        loader_ok=True,
        anthropic_version="0.0.0",
        pydantic_ai_version="9.9.9",
        reason=BAD_SIGNATURE_REASON,
        exc_type=None,
    )


def _loader_break() -> compat.CompatResult:
    return compat.CompatResult(
        compatible=False,
        loader_ok=False,
        anthropic_version=None,
        pydantic_ai_version="9.9.9",
        reason="LLM stack failed to import: No module named 'anthropic'",
        exc_type="ModuleNotFoundError",
    )


def _healthy() -> compat.CompatResult:
    return compat.CompatResult(
        compatible=True,
        loader_ok=True,
        anthropic_version="0.125.0",
        pydantic_ai_version="2.9.0",
    )


@pytest.fixture(autouse=True)
def fresh_memo(monkeypatch):
    """Every case starts from an unresolved flag and a clean environment.

    ``monkeypatch`` restores the pre-test values on teardown, so a case that
    drives the process into degraded cannot leak that verdict into the rest
    of the suite.
    """
    monkeypatch.setattr(compat, "_DEGRADED", None)
    monkeypatch.setattr(compat, "_LOADER_OK", True)
    monkeypatch.setattr(compat, "_COMPATIBLE", True)
    monkeypatch.setattr(compat, "_MARKER_DIR_WARNED", False)
    monkeypatch.delenv("LLM_STACK_COMPAT_OVERRIDE", raising=False)
    monkeypatch.delenv("LLM_STACK_MARKER_DIR", raising=False)


@pytest.fixture
def predicate(monkeypatch):
    """Install a fixed ``CompatResult`` behind the resolver."""

    def _install(result: compat.CompatResult) -> None:
        monkeypatch.setattr(compat, "check_llm_stack_compat", lambda allow_network=False: result)

    return _install


@pytest.fixture
def captures(monkeypatch) -> list[dict]:
    """Record ``sentry_sdk.capture_message`` calls instead of sending them."""
    calls: list[dict] = []

    def _capture(message, **kwargs):
        calls.append({"message": message, **kwargs})

    monkeypatch.setattr(sentry_sdk, "capture_message", _capture)
    return calls


def _markers() -> list[Path]:
    return sorted(compat._MARKER_DIR.glob("llm-stack-degraded*"))


# --------------------------------------------------------------------------
# The three channels
# --------------------------------------------------------------------------


def test_degraded_resolution_fires_all_three_channels(predicate, captures, caplog):
    predicate(_signature_break())
    caplog.set_level(logging.DEBUG, logger="agent.llm.compat")

    assert compat._resolve_degraded_flag("bridge") is True

    critical = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert critical, "no logger.critical sentinel emitted"
    assert compat.SENTINEL in critical[0].getMessage()
    assert "0.0.0" in critical[0].getMessage() and "9.9.9" in critical[0].getMessage()

    assert len(captures) == 1
    assert captures[0]["level"] == "fatal"
    assert compat.SENTINEL in captures[0]["message"]
    assert BAD_SIGNATURE_REASON in captures[0]["message"]

    marker = compat._marker_path("bridge")
    payload = json.loads(marker.read_text())
    assert payload["process"] == "bridge"
    assert payload["axis"] == "signature"
    assert payload["anthropic_version"] == "0.0.0"
    assert payload["pydantic_ai_version"] == "9.9.9"
    assert payload["exc_type"] is None
    assert payload["reason"] == BAD_SIGNATURE_REASON


def test_loader_axis_is_named_in_the_marker(predicate, captures):
    predicate(_loader_break())
    compat._resolve_degraded_flag("worker")

    payload = json.loads(compat._marker_path("worker").read_text())
    assert payload["axis"] == "loader"
    assert payload["exc_type"] == "ModuleNotFoundError"
    assert payload["loader_ok"] is False


def test_alert_body_is_static_plus_versions_and_exception(predicate, captures):
    """No drafter, no LLM composition — the thing being alarmed is the stack."""
    predicate(_loader_break())
    compat._resolve_degraded_flag("worker")

    body = captures[0]["message"]
    assert compat._ALERT_BODY in body
    assert "ModuleNotFoundError" in body
    assert "9.9.9" in body


def test_alert_fires_once_on_the_first_transition(predicate, captures):
    predicate(_signature_break())
    compat._resolve_degraded_flag("bridge")
    compat._resolve_degraded_flag("bridge")
    compat._resolve_degraded_flag()

    assert len(captures) == 1


def test_sentry_failure_suppresses_neither_log_nor_marker(predicate, monkeypatch, caplog):
    def _boom(*args, **kwargs):
        raise RuntimeError("sentry transport down")

    monkeypatch.setattr(sentry_sdk, "capture_message", _boom)
    predicate(_signature_break())
    caplog.set_level(logging.DEBUG, logger="agent.llm.compat")

    assert compat._resolve_degraded_flag("bridge") is True

    assert any(r.levelno == logging.CRITICAL for r in caplog.records)
    assert any("Sentry capture_message failed" in r.getMessage() for r in caplog.records)
    assert compat._marker_path("bridge").exists()


# --------------------------------------------------------------------------
# Who may write a marker, and who clears it
# --------------------------------------------------------------------------


def test_no_proc_caller_writes_no_marker_but_still_alerts(predicate, captures, caplog):
    """One-shot scripts and pytest processes alarm without stranding red.

    Only a process that will be around to *clear* its marker may write one:
    a pid-suffixed scheme has no clear leg for a process that exits while
    degraded, so on a genuinely degraded machine every one-shot would
    deposit another permanent red that survives the fix.
    """
    predicate(_signature_break())
    caplog.set_level(logging.DEBUG, logger="agent.llm.compat")

    assert compat._resolve_degraded_flag() is True

    assert _markers() == []
    assert len(captures) == 1
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)


def test_healthy_resolution_clears_only_this_processes_marker(predicate, captures):
    """The bridge's green re-resolution must not paint over a red worker.

    The worker's graceful restart defers while jobs are running, so it can
    still hold a memoized degraded flag long after the bridge has restarted
    onto the fixed pins. A glob clear (or a shared marker) would render the
    board green against a still-broken worker — the stuck-dashboard failure
    with the polarity flipped, and worse, because nobody investigates green.
    """
    predicate(_signature_break())
    compat._resolve_degraded_flag("bridge")
    compat._DEGRADED = None
    compat._resolve_degraded_flag("worker")
    assert len(_markers()) == 2

    # The bridge restarts onto fixed pins and re-resolves healthy.
    compat._DEGRADED = None
    predicate(_healthy())
    assert compat._resolve_degraded_flag("bridge") is False

    assert not compat._marker_path("bridge").exists()
    assert compat._marker_path("worker").exists()
    assert _markers(), "the board must stay red while any marker survives"


def test_healthy_resolution_with_no_marker_is_a_no_op(predicate, captures):
    predicate(_healthy())
    assert compat._resolve_degraded_flag("bridge") is False
    assert _markers() == []
    assert captures == []


# --------------------------------------------------------------------------
# The marker directory seam
# --------------------------------------------------------------------------


def test_marker_redirect_is_autouse(predicate, captures):
    """No per-file monkeypatch: the mechanism must supply the redirect.

    Fails if the ``tests/unit/conftest.py`` fixture is absent, misnamed, or
    not autouse — which is the point, since the per-file convention it
    replaced had already missed a degraded-driving file.
    """
    live_data = Path(compat.__file__).resolve().parents[2] / "data"
    assert compat._MARKER_DIR != live_data

    predicate(_signature_break())
    compat._resolve_degraded_flag("bridge")

    assert (compat._MARKER_DIR / "llm-stack-degraded.bridge").exists()
    assert not (live_data / "llm-stack-degraded.bridge").exists()


def test_default_marker_dir_matches_the_dashboards(monkeypatch):
    """The only case asserting the *default*, resolved from the module file.

    Deliberately does not read the patched global: the production default is
    what has to line up with the separate uvicorn process that globs it.
    """
    import ui.app

    resolver_default = Path(compat.__file__).resolve().parents[2] / "data"
    dashboard_default = Path(ui.app.__file__).parent.parent / "data"
    assert resolver_default == dashboard_default


def test_marker_dir_override_warns(monkeypatch, tmp_path, caplog):
    """A relocated marker dir announces itself under the same sentinel.

    A stale ``LLM_STACK_MARKER_DIR`` inherited from a plist or a cron env
    would silently move this system's only standing degraded signal and
    leave the dashboard green on a degraded bridge.
    """
    monkeypatch.setenv("LLM_STACK_MARKER_DIR", str(tmp_path))
    caplog.set_level(logging.DEBUG, logger="agent.llm.compat")

    first = compat._marker_path("bridge")
    second = compat._marker_path("worker")

    assert first.parent == tmp_path and second.parent == tmp_path
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and compat.SENTINEL in r.getMessage()
    ]
    assert len(warnings) == 1, "the override must announce itself exactly once per process"
    assert "OVERRIDDEN" in warnings[0].getMessage()


# --------------------------------------------------------------------------
# Break-glass override
# --------------------------------------------------------------------------


def test_override_short_circuits_the_resolver(monkeypatch, predicate, captures, caplog):
    monkeypatch.setenv("LLM_STACK_COMPAT_OVERRIDE", "healthy")
    predicate(_signature_break())
    caplog.set_level(logging.DEBUG, logger="agent.llm.compat")

    assert compat._resolve_degraded_flag("bridge") is False
    assert compat.stack_axes() == (True, True)
    assert captures == [], "an overridden machine must not alarm"
    assert any("OVERRIDDEN" in r.getMessage() for r in caplog.records)
    assert _markers() == []


def test_override_is_ignored_by_the_pure_predicate_and_the_cli(monkeypatch, captures, capsys):
    """An override must never let a bad pin pass the bump gate."""
    monkeypatch.setenv("LLM_STACK_COMPAT_OVERRIDE", "healthy")
    monkeypatch.setattr(
        compat,
        "_models_anthropic_source",
        lambda stack: (
            "class AnthropicModel:\n"
            "    async def _messages_create(self):\n"
            "        return await self.client.beta.messages.create(no_such_kwarg_xyz=1)\n"
        ),
    )

    assert compat.check_llm_stack_compat().compatible is False
    assert compat.main(["--json"]) == 1
    assert json.loads(capsys.readouterr().out)["compatible"] is False


def test_override_clears_marker(monkeypatch, predicate, captures, caplog):
    """Without this the board stays red forever on an overridden machine.

    The short-circuit happens *before* the predicate, so no future
    resolution can ever reach the healthy branch's clear.
    """
    predicate(_signature_break())
    compat._resolve_degraded_flag("bridge")
    assert compat._marker_path("bridge").exists()

    compat._DEGRADED = None
    monkeypatch.setenv("LLM_STACK_COMPAT_OVERRIDE", "healthy")
    caplog.set_level(logging.DEBUG, logger="agent.llm.compat")

    assert compat._resolve_degraded_flag("bridge") is False
    assert not compat._marker_path("bridge").exists()
    assert any("OVERRIDDEN" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------
# The typed exception, the two axes, and alert independence
# --------------------------------------------------------------------------


def test_llm_stack_incompatible_preserves_existing_fail_safes():
    assert issubclass(LLMStackIncompatible, LLMCallError)


async def test_alert_fires_while_run_typed_raises(predicate, captures, caplog):
    """Independence proof: the raise does not stand in for the alert.

    A typed exception at one call site is legible only to that call site.
    The alert is the fleet-wide signal, and it must fire on the same
    resolution that makes the call fail.
    """
    from pydantic import BaseModel

    class Out(BaseModel):
        answer: str

    predicate(_signature_break())
    caplog.set_level(logging.DEBUG, logger="agent.llm.compat")
    assert compat._DEGRADED is None, "the no-startup-hook path: nothing resolved yet"

    with pytest.raises(LLMStackIncompatible):
        await wrapper_mod.run_typed("hello", Out)

    assert len(captures) == 1
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)


async def test_two_axis_split_leaves_the_local_leg_running(predicate, captures):
    """A signature break must not fall the granite classifiers over.

    ``run_typed_local`` talks to localhost Ollama and never touches
    ``anthropic``, so gating it on the Anthropic create signature would
    re-collapse two domains spike-5 deliberately separated.
    """
    import dataclasses

    from pydantic import BaseModel
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    class Decision(BaseModel):
        decision: str

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        tool_name = info.output_tools[0].name if info.output_tools else None
        return ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args={"decision": "ok"})])

    real = wrapper_mod._load_stack()
    fake = dataclasses.replace(
        real,
        OpenAIChatModel=lambda model_name, *, provider: FunctionModel(
            _respond, model_name=model_name
        ),
    )
    predicate(_signature_break())

    with pytest.raises(LLMStackIncompatible):
        await wrapper_mod.run_typed("hello", Decision)

    import unittest.mock

    with unittest.mock.patch.object(wrapper_mod, "_load_stack", lambda: fake):
        result = await wrapper_mod.run_typed_local("hello", Decision)
    assert result.decision == "ok"


class _RaisingFinder:
    """A meta-path finder that refuses one top-level module."""

    def __init__(self, name: str) -> None:
        self.name = name

    def find_spec(self, fullname, path=None, target=None):
        if fullname == self.name or fullname.startswith(f"{self.name}."):
            raise ImportError(f"stubbed {fullname}")
        return None


async def test_anthropic_import_error_local_path(monkeypatch):
    """``loader_ok`` is stack-wide — asserted, not left to fall out.

    One memoized ``_load_stack()`` imports the whole set, so an
    ``anthropic`` ImportError sets ``loader_ok=False`` and the local leg
    raises too. That matches today's module-scope behavior exactly (no
    regression), and it is a live hazard: anthropic 1.0.0 moves its HTTP
    layer to ``httpx2``. Splitting the loader per domain is the honest
    upgrade and is out of scope for this lane, so the current contract is
    pinned here rather than left implicit.
    """
    from pydantic import BaseModel

    from agent import anthropic_client

    class Decision(BaseModel):
        decision: str

    monkeypatch.delitem(sys.modules, "anthropic", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_RaisingFinder("anthropic"), *sys.meta_path])
    anthropic_client._load_stack.cache_clear()
    try:
        result = compat.check_llm_stack_compat()
        assert result.loader_ok is False

        compat._DEGRADED = None
        with pytest.raises(LLMStackIncompatible):
            await wrapper_mod.run_typed_local("hello", Decision)
    finally:
        anthropic_client._load_stack.cache_clear()


# --------------------------------------------------------------------------
# Exception-total run boundary
# --------------------------------------------------------------------------


def test_unexpected_exception_class_resolves_degraded_not_raises(monkeypatch, captures, caplog):
    """The blocker this suite exists to close.

    An exception class neither ``check_llm_stack_compat`` nor
    ``resolve_degraded_flag`` anticipated (here: ``_find_create_sites``
    raising ``ValueError`` instead of the expected ``SyntaxError``) must
    still resolve to degraded, never propagate. An unguarded boot hook that
    lets this escape ``main()`` never starts the process at all — under
    launchd ``KeepAlive`` that is a crash-loop, exactly the failure class
    this lane exists to remove.
    """

    def _boom(source: str):
        raise ValueError("boom")

    monkeypatch.setattr(compat, "_find_create_sites", _boom)
    caplog.set_level(logging.DEBUG, logger="agent.llm.compat")

    assert compat._resolve_degraded_flag("bridge") is True

    assert compat._LOADER_OK is True
    assert compat._COMPATIBLE is False
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)
    assert len(captures) == 1
    assert compat._marker_path("bridge").exists()


def test_resolver_itself_never_propagates_an_unexpected_exception(monkeypatch, captures, caplog):
    """Defense in depth: even if ``check_llm_stack_compat`` itself raises.

    ``resolve_degraded_flag`` wraps the call to the predicate too, so a
    future defect inside ``check_llm_stack_compat`` that reintroduces an
    unguarded raise still cannot reach a boot hook unguarded.
    """

    def _boom(allow_network: bool = False):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(compat, "check_llm_stack_compat", _boom)
    caplog.set_level(logging.DEBUG, logger="agent.llm.compat")

    assert compat.resolve_degraded_flag("worker") is True
    assert compat._DEGRADED is True
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)
    assert len(captures) == 1


# --------------------------------------------------------------------------
# Startup hooks: degraded is not down
# --------------------------------------------------------------------------


def _startup_hook_args(module_path: str, func_name: str) -> list[str]:
    """Literal ``proc`` arguments passed to the resolver inside ``func_name``."""
    tree = ast.parse((REPO_ROOT / module_path).read_text())
    args: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name != func_name:
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "resolve_degraded_flag":
                args.extend(a.value for a in call.args if isinstance(a, ast.Constant))
    return args


@pytest.mark.parametrize(
    ("module_path", "func_name", "proc"),
    [
        ("bridge/telegram_bridge.py", "main", "bridge"),
        ("worker/__main__.py", "main", "worker"),
    ],
)
def test_startup_forces_resolution_with_its_own_proc(module_path, func_name, proc):
    assert _startup_hook_args(module_path, func_name) == [proc]


def test_degraded_startup_does_not_exit(predicate, captures):
    """The process comes up. Exiting would be the worse trade."""
    predicate(_loader_break())
    assert compat._resolve_degraded_flag("bridge") is True  # no SystemExit


async def test_intake_survives_a_degraded_stack(predicate, captures):
    """Telegram intake keeps routing: the classifier falls open, not over."""
    from tools.classifier import classify_message_intent_async

    predicate(_loader_break())

    result = await classify_message_intent_async("ship it", session_context="active work")
    assert result["intent"] == "new_work"


def test_hibernation_drop_exempts_the_sentinel(monkeypatch):
    """Hibernation is exactly when a broken stack most needs remote visibility.

    It is a persistent flag, not a brief window, so an unexempted drop would
    delete the only remote channel for as long as the bridge cannot reach
    Telegram.
    """
    import bridge.telegram_bridge as tb

    monkeypatch.setattr(tb, "is_hibernating", lambda: True)

    degraded = {"message": f"{compat.SENTINEL} DEGRADED: stack broken"}
    unrelated = {"message": "some other bridge error"}

    assert tb._sentry_before_send(degraded, None) is degraded
    assert tb._sentry_before_send(unrelated, None) is None
