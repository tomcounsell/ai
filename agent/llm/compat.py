"""LLM-stack compatibility predicate (#3001).

``anthropic`` and ``pydantic-ai-slim`` are a **coupled set**: an
independent bump of either one can leave a pair where ``pydantic_ai``
forwards a keyword the installed ``anthropic`` client no longer accepts.
That is a ``TypeError`` at the first non-harness LLM call, fleet-wide, with
nothing at ``/update`` time saying so. :func:`check_llm_stack_compat` is
the check that catches it before the call.

Import-safety contract
----------------------
Module scope here is **stdlib and our own code only**. The third-party
stack is reached exclusively through
:func:`agent.anthropic_client._load_stack`, from inside function bodies.
The direction is load-bearing: the predicate must be able to *report* an
ImportError, so nothing on this module's import path may raise one.

How the forwarded-kwarg set is derived
--------------------------------------
From the **call site**, not from ``AnthropicModelSettings.__annotations__``.
The settings TypedDict is a declared superset of provider-agnostic and
``anthropic_``-namespaced settings that ``AnthropicModel`` translates,
renames into headers, or drops: on the pinned, verified-working pair it has
31 keys of which 20 appear in no ``create`` signature. Deriving from it
would report ``compatible=False`` on a healthy fleet.

So: ``ast.parse(inspect.getsource(pydantic_ai.models.anthropic))``, collect
every ``.create(...)`` call on a dotted path rooted at ``self.client``, and
read the literal keyword names off that call.

Why the target resolves against ``anthropic.AsyncAnthropic``
------------------------------------------------------------
The call site names only the attribute *path* (``self.client.beta.messages``
on 2.9.0), never the client class. ``pydantic_ai.models.anthropic`` types
``self.client`` as the ``AsyncAnthropicClient`` union, which also admits
``AsyncAnthropicBedrock``, ``AsyncAnthropicBedrockMantle``, and
``AsyncAnthropicVertex`` -- whose ``create`` signatures differ.
``AsyncAnthropic`` is the correct resolution target **because that is the
class ``agent/llm/wrapper.py`` constructs**, and this repo uses none of the
others. A future reader debugging a Bedrock-shaped false positive should
start here. Construction is constructor-only (``api_key="x"``): no network,
no real key.

Hardcoding ``anthropic.resources.messages.AsyncMessages`` is equally wrong
in the other direction -- the non-beta resource lacks ``betas``,
``context_management``, ``mcp_servers``, and ``speed``, four kwargs the call
site does pass, so even the correct kwarg set would report four false
positives against it. The call site names its own target; read the target
from the call site.

Fail-closed, five ways
----------------------
A silently-passing predicate whose target moved is the same failure as no
gate at all, so every introspection failure returns ``compatible=False``
with the reason verbatim: ``getsource`` unavailable, **zero** ``create``
sites, **more than one** ``create`` site, the attribute path unresolvable
on the client, and the single site forwarding **no literal keywords**
(splat-only). The last is not covered by the count gate: on a site
refactored to ``create(**kwargs)`` the count is still 1, the path still
resolves, ``forwarded`` is empty, and the subset test would be vacuously
true.

Purity
------
:func:`check_llm_stack_compat` returns a :class:`CompatResult` and does
**nothing else**: it never touches the memoized degraded flag, never calls
``capture_message``, never writes or clears a marker file. Only
``_resolve_degraded_flag()`` alerts. This matters because the CLI's callers
are ``scripts/update/verify.py`` and the auto-bump ``llm`` gate phase, and
the gate deliberately runs the predicate against a stack it is *about to
roll back* -- an impure CLI would fire a fatal Sentry capture and strand a
red marker on every **successful** rollback.

Degraded posture: start degraded, alert loudly
----------------------------------------------
An incompatible stack at bridge or worker startup **does not exit the
process**. Telegram intake keeps running and AgentSessions keep enqueueing;
only the non-harness LLM calls fail fast, with the typed
``LLMStackIncompatible`` so every existing ``except LLMCallError`` fail-safe
keeps working. Degraded-but-running is precisely the state that hid the
2026-08-24 incident for six hours, so **the alert is the entire safety
property**.

:func:`_resolve_degraded_flag` is the flag: lazily self-resolving, memoizing
both axes, and **the first transition to degraded emits the alert from
inside the resolver**. Binding the alert to resolution rather than to
startup means no entry path can reach the broken stack without alarming, and
no ordering race between boot and first call exists -- the startup hooks in
``bridge/telegram_bridge.py::main`` and ``worker/__main__.py`` do nothing but
force resolution early.

Because the thing being alarmed *is* the LLM stack, the alert must not route
through it: no ``run_typed``, no message drafter, no persona pass, no
dynamic body composition. The body is a **static string** plus the two
resolved versions and the captured exception type and message. This is a
deliberate, named exception to the standing "never let raw text speak"
convention -- the drafter is unavailable by construction, and a silent alert
is the failure being prevented. Three independent transports carry it:
Sentry (``level="fatal"``, exempted from the bridge's hibernation drop),
``logger.critical`` with :data:`SENTINEL`, and the per-process marker file
the dashboard globs.
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import importlib.metadata
import inspect
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Sentinel token shared by every break-glass path in this module, so one
# grep over the logs finds them all.
SENTINEL = "LLM_STACK_COMPAT"

# Marker-directory seam. Production default is cwd-independent and matches
# ``ui/app.py``'s ``Path(__file__).parent.parent / "data"`` so the dashboard
# globs the same directory the resolver writes. Tests redirect it via the
# autouse ``tests/unit/conftest.py`` fixture rather than writing into the
# live ``data/`` a running bridge, worker, and dashboard share.
_MARKER_DIR = Path(__file__).resolve().parents[2] / "data"
_MARKER_STEM = "llm-stack-degraded"

# Memoized degraded verdict, owned by ``_resolve_degraded_flag()``.
# ``None`` means "not yet resolved" -- the predicate's purity is asserted
# against exactly that: a ``check_llm_stack_compat`` call must leave this
# ``None``. The two axes are memoized beside it because they are consumed
# separately: ``run_typed`` is gated on both, ``run_typed_local`` on
# ``loader_ok`` alone.
_DEGRADED: bool | None = None
_LOADER_OK: bool = True
_COMPATIBLE: bool = True

# Static alert body. Everything variable about a degraded resolution is
# appended as resolved versions + exception type/message; nothing here is
# composed by an LLM, because the LLM stack is the thing that is broken.
_ALERT_BODY = "LLM stack incompatible — non-harness LLM calls fail fast until this is fixed"

# One-shot latch so an active ``LLM_STACK_MARKER_DIR`` announces itself
# exactly once per process instead of once per marker touch.
_MARKER_DIR_WARNED = False

# The three knobs the 2026-08-24 incident removed from the client. Kept as
# the *assertion* target for ``tests/unit/test_llm_stack_compat.py`` -- the
# derived set must contain all three -- never as a fallback the predicate
# silently degrades to.
INCIDENT_KWARGS = ("temperature", "top_p", "top_k")


@dataclasses.dataclass(frozen=True)
class CompatResult:
    """Verdict on the installed LLM stack, on two independent axes.

    ``loader_ok`` -- the third-party stack imports at all.
    ``compatible`` -- the installed ``anthropic`` ``create`` signature
    accepts everything the installed ``pydantic_ai`` actually forwards.

    They are separate because ``run_typed_local`` (granite on Ollama) never
    touches ``anthropic``: an Anthropic *signature* break must not fall the
    two hot-path classifiers back to their conservative defaults fleet-wide.
    """

    compatible: bool
    loader_ok: bool
    anthropic_version: str | None = None
    pydantic_ai_version: str | None = None
    reason: str | None = None
    exc_type: str | None = None
    forwarded: tuple[str, ...] = ()
    call_site_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form, the ``--json`` CLI's payload."""
        return dataclasses.asdict(self) | {"forwarded": list(self.forwarded)}


def _marker_path(proc: str) -> Path:
    """Path of the degraded marker for ``proc`` (``bridge`` / ``worker``).

    The directory comes from the ``_MARKER_DIR`` module seam, overridable
    at runtime by ``LLM_STACK_MARKER_DIR`` -- read **lazily here**, both
    because a module-scope ``os.environ`` read is blocked by
    ``validate_no_module_scope_env.py`` and because the subprocess CLI test
    can only reach the child through its environment.

    The override is not silent. A stale value inherited from a launchd
    plist, an exported shell var, or a cron env would relocate the write
    path of this system's only *standing* degraded signal and leave the
    dashboard green on a degraded bridge with nothing saying why, so an
    active override logs once at ``logger.warning`` carrying
    :data:`SENTINEL`.

    Writing and clearing (``unlink(missing_ok=True)``) belong to
    ``_resolve_degraded_flag()``; this helper only computes the path, and
    only ``bridge`` and ``worker`` ever pass a ``proc``.
    """
    global _MARKER_DIR_WARNED

    directory = _MARKER_DIR
    override = os.environ.get("LLM_STACK_MARKER_DIR")
    if override:
        candidate = Path(override)
        if candidate != _MARKER_DIR:
            if not _MARKER_DIR_WARNED:
                logger.warning(
                    "%s marker directory OVERRIDDEN: %s (default %s)",
                    SENTINEL,
                    candidate,
                    _MARKER_DIR,
                )
                _MARKER_DIR_WARNED = True
            directory = candidate

    return directory / f"{_MARKER_STEM}.{proc}"


def _installed_version(dist: str) -> str | None:
    """Resolve a distribution version without importing the package.

    Kept import-free on purpose: a broken stack still reports the versions
    that are broken, which is the first thing an operator needs.
    """
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None


def _attribute_path(node: ast.AST) -> list[str] | None:
    """Flatten a dotted ``ast.Attribute``/``ast.Name`` chain to its parts.

    Returns ``None`` for a chain rooted at anything other than a bare name
    (a subscript, a call, a literal), which cannot be resolved against a
    client instance.
    """
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return list(reversed(parts))


def _find_create_sites(source: str) -> list[tuple[list[str], ast.Call]]:
    """Every ``self.client.<...>.create(...)`` call in ``source``."""
    tree = ast.parse(source)
    sites: list[tuple[list[str], ast.Call]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "create":
            continue
        path = _attribute_path(node.func)
        if path and len(path) >= 3 and path[0] == "self" and path[1] == "client":
            sites.append((path, node))
    return sites


def _models_anthropic_source(stack: Any) -> str:
    """Source of the module defining ``pydantic_ai``'s ``AnthropicModel``.

    A single seam: the synthetic-source tests (zero sites, two sites,
    splat-only) replace this function rather than reaching into
    ``pydantic_ai``'s installed files.
    """
    module = inspect.getmodule(stack.AnthropicModel)
    if module is None:
        raise TypeError("could not resolve the module defining AnthropicModel")
    return inspect.getsource(module)


def _resolve_create_target(stack: Any, path: list[str]) -> Any:
    """Walk the call site's own attribute path off ``AsyncAnthropic``.

    ``path`` is ``["self", "client", ..., "create"]``; the intermediate
    segments are walked and the final ``create`` attribute returned. See the
    module docstring for why ``AsyncAnthropic`` is the right class.

    Construction and the walk are **synchronous**, deliberately: this
    predicate must stay callable from *inside* a running event loop.
    ``run_typed`` (a coroutine) reaches ``check_llm_stack_compat`` via
    ``_guard_stack`` -> ``stack_axes()`` -> ``resolve_degraded_flag()``, and
    any caller that reaches ``run_typed`` without a boot hook resolving the
    flag first (a one-shot script, a pytest process, a nightly job) is
    running that whole chain inside ``asyncio.run(...)``'s loop. An earlier
    version of this function called ``asyncio.run()`` here to close the
    client's transport, which raises ``RuntimeError: asyncio.run() cannot
    be called from a running event loop`` in exactly that situation --
    turning a healthy stack into a *permanently* memoized false-degraded
    verdict (the resolver alerts and never re-checks). That is strictly
    worse than the leak this function's cleanup exists to fix.

    So: construct and walk synchronously, exactly as PydanticAI's own
    ``AsyncAnthropic(...)`` construction-only usage allows, then attempt a
    **best-effort** close in ``finally`` -- only when no loop is already
    running (``asyncio.get_running_loop()`` raising ``RuntimeError`` is the
    signal it's safe). Inside a running loop the close is skipped and the
    ``httpx.AsyncClient`` pool is left for GC; any exception from the close
    itself is swallowed, because a failed cleanup must never change the
    verdict this function computes.
    """
    import asyncio

    client = stack.anthropic.AsyncAnthropic(api_key="x")
    try:
        target: Any = client
        for attr in path[2:]:
            target = getattr(target, attr)
        return target
    finally:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No loop running -- safe to synchronously close the transport.
            try:
                asyncio.run(client.close())
            except Exception:
                # Best-effort only; a close failure must never affect the
                # resolved target already returned above.
                logger.debug("%s could not close introspection client", SENTINEL, exc_info=True)
        # else: a loop is already running (e.g. inside run_typed's
        # asyncio.run). Skip the close -- calling asyncio.run() here would
        # raise and, worse, would make an unrelated caller's healthy stack
        # look permanently degraded. The pool is released by GC.


def check_llm_stack_compat(allow_network: bool = False) -> CompatResult:
    """Is the installed ``anthropic`` + ``pydantic_ai`` pair usable?

    Pure: returns a verdict and touches no global state, no marker file, and
    no alert channel, on **both** branches. See the module docstring's
    Purity section. ``_check_network`` (the ``allow_network=True`` branch)
    talks to the third-party stack directly rather than through
    ``agent.llm.wrapper.run_typed`` -- going through ``run_typed`` would
    re-enter ``_guard_stack`` -> ``stack_axes()`` -> ``_resolve_degraded_flag()``,
    mutating this module's memoized globals and breaking purity on exactly
    the branch the auto-bump ``llm`` gate runs against a stack it is about
    to roll back.

    ``allow_network=False`` (the default, and what runs at every service
    start and every ``/update``) is loader + AST introspection: sub-second,
    no sockets, no tokens. ``allow_network=True`` additionally makes one
    minimal real ``create`` call, catching transport-class breaks the
    signature check cannot see; only the auto-bump ``llm`` gate uses it.
    """
    anthropic_version = _installed_version("anthropic")
    pydantic_ai_version = _installed_version("pydantic-ai-slim")

    def fail(reason: str, *, exc: BaseException | None = None, **extra: Any) -> CompatResult:
        return CompatResult(
            compatible=False,
            loader_ok=extra.pop("loader_ok", True),
            anthropic_version=anthropic_version,
            pydantic_ai_version=pydantic_ai_version,
            reason=reason,
            exc_type=type(exc).__name__ if exc is not None else None,
            **extra,
        )

    # --- Axis 1: does the third-party stack import at all? --------------
    try:
        from agent.anthropic_client import _load_stack

        stack = _load_stack()
    except Exception as exc:  # ImportError class, but report anything
        return fail(
            f"LLM stack failed to import: {exc}",
            exc=exc,
            loader_ok=False,
        )

    # --- Axis 2: does anthropic accept what pydantic_ai forwards? -------
    try:
        source = _models_anthropic_source(stack)
    except Exception as exc:
        return fail(
            f"could not read the source of pydantic_ai.models.anthropic: {exc}",
            exc=exc,
        )

    try:
        sites = _find_create_sites(source)
    except Exception as exc:  # SyntaxError is the expected case, but not the only one
        return fail(
            f"could not parse pydantic_ai.models.anthropic: {exc}",
            exc=exc,
        )

    if len(sites) != 1:
        found = [".".join(path) for path, _ in sites]
        return fail(
            "expected exactly 1 self.client.*.create call site in "
            f"pydantic_ai.models.anthropic, found {len(sites)}: {found}"
        )

    path, site = sites[0]
    site_path = ".".join(path)
    forwarded = [keyword.arg for keyword in site.keywords if keyword.arg]

    if not forwarded:
        splat_count = sum(1 for keyword in site.keywords if keyword.arg is None)
        return fail(
            "self.client.*.create call site in pydantic_ai.models.anthropic "
            f"forwards no literal keywords (splat-only: {splat_count} ** entries) "
            "— derivation cannot verify the signature",
            call_site_path=site_path,
        )

    try:
        target = _resolve_create_target(stack, path)
        signature = inspect.signature(target)
    except Exception as exc:
        return fail(
            f"could not resolve the call site's target {site_path!r} on "
            f"anthropic.AsyncAnthropic: {exc}",
            exc=exc,
            forwarded=tuple(forwarded),
            call_site_path=site_path,
        )

    parameters = signature.parameters
    accepts_var_keyword = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )
    missing = [name for name in forwarded if name not in parameters]

    if missing and not accepts_var_keyword:
        return fail(
            f"pydantic_ai forwards {len(missing)} keyword(s) that "
            f"anthropic {anthropic_version}'s {site_path} does not accept: "
            f"{missing}",
            forwarded=tuple(forwarded),
            call_site_path=site_path,
        )

    if allow_network:
        network = _check_network(anthropic_version, pydantic_ai_version)
        if network is not None:
            return dataclasses.replace(
                network,
                forwarded=tuple(forwarded),
                call_site_path=site_path,
            )

    return CompatResult(
        compatible=True,
        loader_ok=True,
        anthropic_version=anthropic_version,
        pydantic_ai_version=pydantic_ai_version,
        reason=None,
        exc_type=None,
        forwarded=tuple(forwarded),
        call_site_path=site_path,
    )


def _check_network(
    anthropic_version: str | None, pydantic_ai_version: str | None
) -> CompatResult | None:
    """One minimal real ``create`` call; ``None`` when it succeeds.

    Catches transport-class breaks (a moved HTTP layer, a changed auth
    header) that no signature comparison can see. Billed, so only the
    auto-bump ``llm`` gate turns it on, and only on cycles that bumped.

    Deliberately does **not** go through ``agent.llm.wrapper.run_typed``:
    that call goes through ``_guard_stack`` -> ``stack_axes()`` ->
    ``_resolve_degraded_flag()``, which memoizes this module's ``_DEGRADED``
    / ``_LOADER_OK`` / ``_COMPATIBLE`` globals -- mutating exactly the
    global state ``check_llm_stack_compat``'s Purity contract promises not
    to touch. This talks to the third-party stack directly instead, so the
    ``allow_network=True`` branch stays as pure as the default one.
    """
    import asyncio

    from pydantic import BaseModel

    from agent.anthropic_client import _load_stack
    from config.models import MODEL_FAST
    from utils.api_keys import get_anthropic_api_key

    class _Probe(BaseModel):
        answer: str

    async def _probe() -> None:
        stack = _load_stack()
        async with stack.anthropic.AsyncAnthropic(api_key=get_anthropic_api_key()) as client:
            provider = stack.AnthropicProvider(anthropic_client=client)
            pydantic_model = stack.AnthropicModel(MODEL_FAST, provider=provider)
            agent = stack.Agent(pydantic_model, output_type=_Probe)
            await agent.run("Reply with answer=hi")

    try:
        asyncio.run(_probe())
    except Exception as exc:
        return CompatResult(
            compatible=False,
            loader_ok=True,
            anthropic_version=anthropic_version,
            pydantic_ai_version=pydantic_ai_version,
            reason=f"live network probe call failed: {exc}",
            exc_type=type(exc).__name__,
        )
    return None


def _degraded_axis(result: CompatResult) -> str:
    """Which axis failed, in one word, for the alert and the marker."""
    if not result.loader_ok:
        return "loader"
    return "signature"


def _alert_degraded(result: CompatResult, proc: str | None) -> None:
    """Fire all three channels for a first transition to degraded.

    Order is deliberate: the stdlib log first (it is the signal of record
    and cannot fail), then Sentry, then the marker. Each later channel is
    independently guarded, so a Sentry outage suppresses neither the
    sentinel log nor the standing marker.
    """
    body = (
        f"{SENTINEL} DEGRADED: {_ALERT_BODY} "
        f"[axis={_degraded_axis(result)} "
        f"anthropic={result.anthropic_version} "
        f"pydantic-ai={result.pydantic_ai_version} "
        f"exc_type={result.exc_type}] {result.reason}"
    )

    logger.critical(body)

    try:
        import sentry_sdk

        sentry_sdk.capture_message(body, level="fatal")
    except Exception:
        # A Sentry capture failure must never crash the resolver or
        # suppress the other two channels -- the critical log above is
        # already the signal of record. Mirrors agent/index_drift.py.
        logger.warning("%s Sentry capture_message failed", SENTINEL, exc_info=True)

    _write_marker(result, proc)


def _write_marker(result: CompatResult, proc: str | None) -> None:
    """Write the standing dashboard marker -- only when ``proc`` is given.

    Only a process that will still be around to *clear* its marker may
    write one, so ``bridge`` and ``worker`` are the whole marker
    population. A one-shot script, a cron helper, or a pytest process gets
    the Sentry capture and the sentinel log and deposits nothing that would
    strand the board red after the fix lands.
    """
    if not proc:
        return

    path = _marker_path(proc)
    payload = {
        "process": proc,
        "axis": _degraded_axis(result),
        "loader_ok": result.loader_ok,
        "compatible": result.compatible,
        "anthropic_version": result.anthropic_version,
        "pydantic_ai_version": result.pydantic_ai_version,
        "exc_type": result.exc_type,
        "reason": result.reason,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    except OSError:
        # The resolver's write failure logs; it never raises. Losing the
        # dashboard channel must not take the process with it.
        logger.warning("%s could not write degraded marker %s", SENTINEL, path, exc_info=True)


def _clear_marker(proc: str | None) -> None:
    """Clear **only this process's own** marker path.

    Never a glob and never another process's file: the bridge's healthy
    re-resolution after a pin fix must not paint the board green over a
    worker that deferred its restart and is still raising.
    """
    if not proc:
        return
    try:
        _marker_path(proc).unlink(missing_ok=True)
    except OSError:
        logger.warning("%s could not clear degraded marker", SENTINEL, exc_info=True)


def resolve_degraded_flag(proc: str | None = None) -> bool:
    """Is this process's LLM stack degraded? Lazily resolved, memoized.

    The first read -- from a startup hook, or from a ``run_typed`` call in a
    process that never ran one -- evaluates the predicate and, on a first
    transition to degraded, emits the alert (see the module docstring).
    Subsequent reads are memo hits and alert nothing.

    ``proc`` names the marker file this process owns (``"bridge"`` /
    ``"worker"``). Callers that pass nothing write no marker; they still get
    Sentry and the sentinel log.

    Break-glass: ``LLM_STACK_COMPAT_OVERRIDE=healthy`` short-circuits to
    not-degraded, announced at ``logger.warning`` with :data:`SENTINEL`. The
    read is inside the function body because a module-scope ``os.environ``
    read is blocked by ``validate_no_module_scope_env.py``. The override
    clears this process's marker on the way out: the short-circuit happens
    *before* the predicate, so without the clear no future resolution could
    ever reach the healthy branch and the board would stay red forever on a
    machine the operator declared healthy. The override is deliberately not
    honoured by :func:`check_llm_stack_compat`, by the ``--json`` CLI, or by
    the auto-bump gate -- it must never let a bad pin ship.

    This is the run-boundary's actual startup API and the sole public entry
    point for ``bridge/telegram_bridge.py::main`` and
    ``worker/__main__.py`` -- exposed under a public name because it is
    exactly what a future reader greps for; ``stack_axes()`` cannot take a
    ``proc``. **Exception-total by contract**: this function must never let
    an unexpected exception escape. A launchd ``KeepAlive`` process turns an
    escaping exception into a crash-loop, which is the exact failure class
    this lane exists to remove one layer down -- reintroducing it at the
    boot hook would defeat the whole plan. Anything ``check_llm_stack_compat``
    itself did not catch is caught here too, and the flag fails closed to
    degraded (never to a process exit).
    """
    global _DEGRADED, _LOADER_OK, _COMPATIBLE

    if _DEGRADED is not None:
        return _DEGRADED

    if os.environ.get("LLM_STACK_COMPAT_OVERRIDE") == "healthy":
        logger.warning("%s OVERRIDDEN: LLM_STACK_COMPAT_OVERRIDE=healthy", SENTINEL)
        _LOADER_OK = True
        _COMPATIBLE = True
        _DEGRADED = False
        _clear_marker(proc)
        return False

    try:
        result = check_llm_stack_compat()
    except Exception as exc:  # run-boundary total: never propagate, never exit
        result = CompatResult(
            compatible=False,
            loader_ok=False,
            reason=f"check_llm_stack_compat raised unexpectedly: {exc}",
            exc_type=type(exc).__name__,
        )

    _LOADER_OK = result.loader_ok
    _COMPATIBLE = result.compatible
    _DEGRADED = not (result.loader_ok and result.compatible)

    if _DEGRADED:
        _alert_degraded(result, proc)
    else:
        _clear_marker(proc)

    return _DEGRADED


# Internal alias, retained for existing tests and any in-module callers that
# spell the private name. `resolve_degraded_flag` is the public API (#3001
# tech debt: a leading underscore on this repo's actual startup entry point
# told a grepping reader the opposite of the truth).
_resolve_degraded_flag = resolve_degraded_flag


def stack_axes() -> tuple[bool, bool]:
    """``(loader_ok, compatible)`` for this process, forcing resolution.

    The two axes stay separate all the way to the call sites:
    ``run_typed`` is gated on both, ``run_typed_local`` on ``loader_ok``
    alone, because the local granite-on-Ollama leg never touches
    ``anthropic`` and an Anthropic *signature* break must not fall the two
    hot-path classifiers back to their conservative defaults fleet-wide.
    """
    resolve_degraded_flag()
    return _LOADER_OK, _COMPATIBLE


def main(argv: list[str] | None = None) -> int:
    """``python -m agent.llm.compat --json`` -- the subprocess entry point.

    Calls **only** the pure predicate. Its callers (``verify.py``, the
    auto-bump ``llm`` gate) must never alarm production for a stack the gate
    is about to roll back, and the operator break-glass
    ``LLM_STACK_COMPAT_OVERRIDE`` is deliberately not honoured here: an
    override must never let a bad pin pass the bump gate.
    """
    parser = argparse.ArgumentParser(
        prog="python -m agent.llm.compat",
        description="Report whether the installed anthropic + pydantic-ai pair is usable.",
    )
    parser.add_argument("--json", action="store_true", help="emit the verdict as JSON")
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="additionally make one minimal billed run_typed call",
    )
    args = parser.parse_args(argv)

    result = check_llm_stack_compat(allow_network=args.allow_network)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    elif result.compatible:
        print(
            f"compatible: anthropic {result.anthropic_version} "
            f"/ pydantic-ai {result.pydantic_ai_version}"
        )
    else:
        print(f"INCOMPATIBLE: {result.reason}")

    return 0 if result.compatible else 1


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    sys.exit(main())
