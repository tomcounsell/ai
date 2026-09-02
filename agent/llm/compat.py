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

# Memoized degraded verdict, owned by ``_resolve_degraded_flag()`` (task 4).
# Declared here because the predicate's purity is asserted against it: a
# ``check_llm_stack_compat`` call must leave this ``None``.
_DEGRADED: bool | None = None

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
    """
    target = stack.anthropic.AsyncAnthropic(api_key="x")
    for attr in path[2:]:
        target = getattr(target, attr)
    return target


def check_llm_stack_compat(allow_network: bool = False) -> CompatResult:
    """Is the installed ``anthropic`` + ``pydantic_ai`` pair usable?

    Pure: returns a verdict and touches no global state, no marker file, and
    no alert channel. See the module docstring's Purity section.

    ``allow_network=False`` (the default, and what runs at every service
    start and every ``/update``) is loader + AST introspection: sub-second,
    no sockets, no tokens. ``allow_network=True`` additionally makes one
    minimal real ``run_typed`` call, catching transport-class breaks the
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
    from agent.anthropic_client import _load_stack

    try:
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
    except SyntaxError as exc:
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
    """One minimal real ``run_typed`` call; ``None`` when it succeeds.

    Catches transport-class breaks (a moved HTTP layer, a changed auth
    header) that no signature comparison can see. Billed, so only the
    auto-bump ``llm`` gate turns it on, and only on cycles that bumped.
    """
    import asyncio

    from pydantic import BaseModel

    from agent.llm.wrapper import run_typed

    class _Probe(BaseModel):
        answer: str

    try:
        asyncio.run(run_typed("Reply with answer=hi", _Probe))
    except Exception as exc:
        return CompatResult(
            compatible=False,
            loader_ok=True,
            anthropic_version=anthropic_version,
            pydantic_ai_version=pydantic_ai_version,
            reason=f"live run_typed call failed: {exc}",
            exc_type=type(exc).__name__,
        )
    return None


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
