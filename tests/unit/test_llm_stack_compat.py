"""Unit tests for ``agent.llm.compat`` -- the LLM-stack compat predicate (#3001).

Two guards carry the weight here, because round 5 of this plan's critique
shipped a subset test that passed trivially against a predicate that would
have degraded the entire fleet:

* the **positive self-test** -- ``check_llm_stack_compat().compatible is
  True`` on the pinned pair, and
* **shape assertions on the derived set** -- it contains ``temperature`` /
  ``top_p`` / ``top_k`` (the three the 2026-08-24 incident removed) and no
  ``anthropic_``-prefixed name (the tell that the derivation slipped back
  onto ``AnthropicModelSettings.__annotations__``).

Every fail-closed path is exercised against a **synthetic module source**
fed through the ``_models_anthropic_source`` seam, so the cases do not
depend on ``pydantic_ai``'s installed files ever taking those shapes.

Purity is asserted **in process**: on an incompatible stack the predicate
and the ``--json`` entry function emit zero ``capture_message`` calls and
leave ``compat._DEGRADED`` unresolved. The memo assertion is the one that
separates pure from impure and it is only available in process. "Creates no
marker" is deliberately **not** asserted as purity evidence -- no caller
passes ``proc``, so it would hold either way. One subprocess run remains,
proving only what a parent can see: the out-of-process CLI contract.
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import sentry_sdk

from agent.llm import compat

REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------
# Synthetic module sources for the fail-closed paths
# --------------------------------------------------------------------------

NO_SITES_SOURCE = """
class AnthropicModel:
    async def _messages_create(self):
        return await self.client.messages.stream(model="m")
"""

TWO_SITES_SOURCE = """
class AnthropicModel:
    async def _messages_create(self, beta):
        if beta:
            return await self.client.beta.messages.create(model="m", temperature=1)
        return await self.client.messages.create(model="m", temperature=1)
"""

SPLAT_ONLY_SOURCE = """
class AnthropicModel:
    async def _messages_create(self, kwargs):
        return await self.client.beta.messages.create(**kwargs)
"""

UNRESOLVABLE_PATH_SOURCE = """
class AnthropicModel:
    async def _messages_create(self):
        return await self.client.no_such_resource.messages.create(model="m", temperature=1)
"""

BAD_KWARG_SOURCE = """
class AnthropicModel:
    async def _messages_create(self):
        return await self.client.beta.messages.create(
            model="m", temperature=1, no_such_kwarg_xyz=2
        )
"""


@pytest.fixture
def synthetic_source(monkeypatch):
    """Feed ``check_llm_stack_compat`` a hand-written module source."""

    def _install(source: str) -> None:
        monkeypatch.setattr(compat, "_models_anthropic_source", lambda stack: source)

    return _install


@pytest.fixture
def counting_capture(monkeypatch) -> list[tuple]:
    """Replace ``sentry_sdk.capture_message`` with a counting stub."""
    calls: list[tuple] = []
    monkeypatch.setattr(sentry_sdk, "capture_message", lambda *a, **k: calls.append((a, k)))
    return calls


# --------------------------------------------------------------------------
# The positive self-test and the shape of the derived set
# --------------------------------------------------------------------------


def test_pinned_pair_is_compatible() -> None:
    """The predicate must report the installed, working pair as healthy.

    The assertion whose absence let the round-5 blocker through: a
    predicate deriving from ``AnthropicModelSettings.__annotations__``
    returns ``compatible=False`` here on a healthy fleet.
    """
    result = compat.check_llm_stack_compat()
    assert result.loader_ok is True, result.reason
    assert result.compatible is True, result.reason
    assert result.reason is None
    assert result.exc_type is None
    assert result.anthropic_version
    assert result.pydantic_ai_version


def test_derived_forwarded_set_comes_from_the_call_site() -> None:
    """Shape assertions: the incident's three knobs in, settings keys out."""
    result = compat.check_llm_stack_compat()
    forwarded = set(result.forwarded)

    assert forwarded, "derivation produced an empty forwarded set"
    for name in compat.INCIDENT_KWARGS:
        assert name in forwarded, f"{name} missing from the derived set: {sorted(forwarded)}"

    settings_only = sorted(n for n in forwarded if n.startswith("anthropic_"))
    assert not settings_only, (
        "anthropic_-prefixed names indicate the derivation slipped back onto "
        f"AnthropicModelSettings.__annotations__: {settings_only}"
    )


def test_resolved_target_is_the_call_sites_own_path() -> None:
    """The target is read from the call site, not hardcoded.

    Specifically not ``anthropic.resources.messages.AsyncMessages``: the
    non-beta resource lacks four kwargs the call site passes.
    """
    from agent.anthropic_client import _load_stack

    stack = _load_stack()
    result = compat.check_llm_stack_compat()
    path = (result.call_site_path or "").split(".")

    assert path[:2] == ["self", "client"] and path[-1] == "create", result.call_site_path

    target = compat._resolve_create_target(stack, path)
    signature = inspect.signature(target)
    assert set(result.forwarded) <= set(signature.parameters)


# --------------------------------------------------------------------------
# Fail-closed paths -- one per introspection failure mode
# --------------------------------------------------------------------------


def test_loader_import_error_reports_loader_ok_false(monkeypatch) -> None:
    import agent.anthropic_client as anthropic_client

    def _boom() -> None:
        raise ImportError("no module named anthropic")

    monkeypatch.setattr(anthropic_client, "_load_stack", _boom)

    result = compat.check_llm_stack_compat()
    assert result.loader_ok is False
    assert result.compatible is False
    assert "no module named anthropic" in (result.reason or "")
    assert result.exc_type == "ImportError"
    # Versions still resolve from distribution metadata, which needs no import.
    assert result.anthropic_version


def test_getsource_failure_fails_closed(monkeypatch) -> None:
    def _boom(stack):
        raise OSError("source not available")

    monkeypatch.setattr(compat, "_models_anthropic_source", _boom)

    result = compat.check_llm_stack_compat()
    assert result.compatible is False
    assert result.loader_ok is True
    assert "source not available" in (result.reason or "")
    assert result.exc_type == "OSError"


def test_zero_create_sites_introspection_fails_closed(synthetic_source) -> None:
    synthetic_source(NO_SITES_SOURCE)

    result = compat.check_llm_stack_compat()
    assert result.compatible is False
    assert "found 0" in (result.reason or ""), result.reason


def test_multi_site_introspection_fails_closed(synthetic_source) -> None:
    """Never fall through to ``sites[0]``.

    A beta/non-beta branch split is the most natural shape the coupled
    set's next 25-minor-version move takes, and picking the first site
    silently checks the wrong signature.
    """
    synthetic_source(TWO_SITES_SOURCE)

    result = compat.check_llm_stack_compat()
    assert result.compatible is False
    reason = result.reason or ""
    assert "found 2" in reason, reason
    assert "self.client.beta.messages.create" in reason, reason
    assert "self.client.messages.create" in reason, reason


def test_splat_only_call_site_fails_closed(synthetic_source) -> None:
    """A ``create(**kwargs)`` site must not pass vacuously.

    Distinct from the count gate: the count is 1, the path resolves,
    ``getsource`` works, and the subset test would be vacuously true --
    ``compatible=True`` against a known-bad pair, on exactly the follower
    machine this gate exists to protect.
    """
    synthetic_source(SPLAT_ONLY_SOURCE)

    result = compat.check_llm_stack_compat()
    assert result.compatible is False
    reason = result.reason or ""
    assert "splat-only: 1 ** entries" in reason, reason


def test_unresolvable_call_site_path_fails_closed(synthetic_source) -> None:
    synthetic_source(UNRESOLVABLE_PATH_SOURCE)

    result = compat.check_llm_stack_compat()
    assert result.compatible is False
    reason = result.reason or ""
    assert "no_such_resource" in reason, reason
    assert result.exc_type == "AttributeError", result.exc_type


def test_unaccepted_kwarg_reports_the_name_verbatim(synthetic_source) -> None:
    """The spike-5 failure class, simulated: a forwarded kwarg the client drops."""
    synthetic_source(BAD_KWARG_SOURCE)

    result = compat.check_llm_stack_compat()
    assert result.compatible is False
    assert result.loader_ok is True
    assert "no_such_kwarg_xyz" in (result.reason or ""), result.reason


# --------------------------------------------------------------------------
# No network in local mode
# --------------------------------------------------------------------------


def test_local_mode_makes_no_network_call(monkeypatch) -> None:
    def _fail(*args, **kwargs):
        raise AssertionError("check_llm_stack_compat() must not make a network call")

    monkeypatch.setattr(compat, "_check_network", _fail)

    assert compat.check_llm_stack_compat().compatible is True


# --------------------------------------------------------------------------
# Purity -- the predicate and the CLI entry, in process
# --------------------------------------------------------------------------


def test_predicate_is_pure_on_an_incompatible_stack(
    synthetic_source, counting_capture, monkeypatch
) -> None:
    monkeypatch.setattr(compat, "_DEGRADED", None, raising=False)
    synthetic_source(BAD_KWARG_SOURCE)

    result = compat.check_llm_stack_compat()

    assert result.compatible is False
    assert counting_capture == [], "the predicate alerted; alerting belongs to the resolver"
    assert compat._DEGRADED is None, "the predicate resolved the memoized degraded flag"


def test_cli_json_entry_is_pure_on_an_incompatible_stack(
    synthetic_source, counting_capture, monkeypatch, capsys
) -> None:
    """The gate runs this against a stack it is about to roll back.

    An impure CLI would fire a fatal Sentry capture and strand a red marker
    on every *successful* rollback.
    """
    monkeypatch.setattr(compat, "_DEGRADED", None, raising=False)
    synthetic_source(BAD_KWARG_SOURCE)

    exit_code = compat.main(["--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["compatible"] is False
    assert counting_capture == [], "the --json CLI alerted"
    assert compat._DEGRADED is None, "the --json CLI resolved the memoized degraded flag"


# --------------------------------------------------------------------------
# The out-of-process CLI contract
# --------------------------------------------------------------------------


def test_cli_contract_out_of_process(tmp_path: Path) -> None:
    """Prove only what a parent can see: JSON on stdout, matching status.

    The marker-directory redirect goes through the child's environment --
    the autouse in-process fixture cannot reach a subprocess.
    """
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "LLM_STACK_MARKER_DIR": str(tmp_path / "markers"),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "agent.llm.compat", "--json"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    payload = json.loads(proc.stdout)
    for key in ("compatible", "loader_ok", "anthropic_version", "pydantic_ai_version"):
        assert key in payload, f"{key} missing from the CLI payload: {sorted(payload)}"
    assert payload["anthropic_version"]
    assert payload["pydantic_ai_version"]
    assert proc.returncode == (0 if payload["compatible"] else 1), proc.stderr
    # stderr is operator-facing: it is quoted verbatim into every gate-failure
    # message and into `verify.py`'s output. A `-m` double-import
    # RuntimeWarning there reads like a real defect in the gate.
    assert proc.stderr == "", f"the CLI wrote to stderr on a clean run:\n{proc.stderr}"


def test_cli_imports_nothing_from_scripts() -> None:
    """``verify.py`` and the bump gate call this; the dependency must not invert."""
    import ast

    tree = ast.parse((REPO_ROOT / "agent" / "llm" / "compat.py").read_text())
    offenders = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        and "scripts" in (getattr(node, "module", "") or "") + "".join(a.name for a in node.names)
    ]
    assert not offenders, [node.lineno for node in offenders]
