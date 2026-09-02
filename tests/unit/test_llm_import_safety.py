"""The import-safety contract on ``agent/llm/`` (#3001).

The invariant: with the third-party LLM stack (``anthropic``,
``pydantic_ai``) broken at import time, ``import agent.llm`` **and**
``import bridge.telegram_bridge`` still succeed. A machine whose installed
stack is missing or incompatible keeps its Telegram intake running; the
failure surfaces at the call path, where it can be reported.

**This half must run out of process.** In-process the assertion cannot
fail: other test files in this suite already import
``bridge.telegram_bridge`` (whose module scope pulls ``bridge.routing`` →
``agent.llm``), so under xdist both ``import`` statements are
``sys.modules`` cache hits that execute no stack import at all -- a test
that passes unconditionally and is counted as coverage. A
``sys.modules``-purge variant is rejected for the same reason in a subtler
form: ``tests/unit/test_bridge_api_id_parse.py:112`` pops only
``bridge.telegram_bridge``, leaving a cached ``bridge.routing`` and
``agent.llm`` to short-circuit the chain, and the correct purge set is the
full transitive closure whose completeness nothing enforces. A fresh
interpreter has no cache to be wrong about.

The shim is a directory placed first on ``PYTHONPATH`` holding an
``anthropic`` module and a ``pydantic_ai`` package whose bodies raise
``ImportError``. ``PYTHONPATH`` entries precede site-packages, so the child
resolves the raising stubs rather than the installed distributions.

The alert / typed-exception half of the contract stays **in process** and
lives at the bottom of this file: with the loader raising, ``run_typed``
raises ``LLMStackIncompatible`` and all three alert channels fire on the
same resolution. The no-startup-hook path is asserted there too — a process
that never ran a startup hook still alarms, because the alert is bound to
flag resolution rather than to boot.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
import sentry_sdk

REPO_ROOT = Path(__file__).resolve().parents[2]

RAISE_ON_IMPORT = 'raise ImportError("stubbed by test_llm_import_safety")\n'


@pytest.fixture
def raising_stack_shim(tmp_path: Path) -> Path:
    """A ``PYTHONPATH`` entry whose ``anthropic``/``pydantic_ai`` raise."""
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "anthropic.py").write_text(RAISE_ON_IMPORT)
    pydantic_ai = shim / "pydantic_ai"
    pydantic_ai.mkdir()
    (pydantic_ai / "__init__.py").write_text(RAISE_ON_IMPORT)
    return shim


def _run_child(shim: Path, source: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": f"{shim}{os.pathsep}{REPO_ROOT}"}
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_shim_actually_shadows_the_real_stack(raising_stack_shim: Path) -> None:
    """Guard the guard: the shim must really win over site-packages.

    Without this, a shim that failed to shadow would make every other case
    in this file a test of the healthy stack.
    """
    proc = _run_child(raising_stack_shim, "import anthropic")
    assert proc.returncode != 0, f"shim did not shadow anthropic\n{proc.stdout}"
    assert "stubbed by test_llm_import_safety" in proc.stderr, proc.stderr

    proc = _run_child(raising_stack_shim, "import pydantic_ai")
    assert proc.returncode != 0, f"shim did not shadow pydantic_ai\n{proc.stdout}"
    assert "stubbed by test_llm_import_safety" in proc.stderr, proc.stderr


def test_agent_llm_imports_with_broken_stack(raising_stack_shim: Path) -> None:
    proc = _run_child(raising_stack_shim, "import agent.llm")
    assert proc.returncode == 0, (
        f"import agent.llm failed under a raising stack\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_bridge_imports_with_broken_stack(raising_stack_shim: Path) -> None:
    """The reason the contract exists: intake survives a broken stack."""
    proc = _run_child(raising_stack_shim, "import agent.llm, bridge.telegram_bridge")
    assert proc.returncode == 0, (
        f"import bridge.telegram_bridge failed under a raising stack\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_anthropic_client_imports_with_broken_stack(raising_stack_shim: Path) -> None:
    """``agent.anthropic_client`` owns the loader and must stay importable."""
    proc = _run_child(raising_stack_shim, "import agent.anthropic_client")
    assert proc.returncode == 0, (
        f"import agent.anthropic_client failed under a raising stack\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_loader_still_raises_under_broken_stack(raising_stack_shim: Path) -> None:
    """Import safety is deferral, not suppression.

    The loader must still surface the ImportError when actually called --
    otherwise a "successful" import would hide a stack that cannot work.
    """
    source = (
        "from agent.anthropic_client import _load_stack\n"
        "try:\n"
        "    _load_stack()\n"
        "except ImportError as exc:\n"
        "    assert 'stubbed by test_llm_import_safety' in str(exc), exc\n"
        "else:\n"
        "    raise AssertionError('_load_stack did not raise under a broken stack')\n"
    )
    proc = _run_child(raising_stack_shim, source)
    assert proc.returncode == 0, (
        f"loader did not surface the ImportError\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


# --------------------------------------------------------------------------
# The in-process half: the typed exception and the three alert channels
# --------------------------------------------------------------------------


@pytest.fixture
def broken_loader(monkeypatch):
    """Make ``_load_stack`` raise, as a missing/broken install would.

    Drives the real predicate down its ``loader_ok=False`` path rather than
    stubbing the verdict, so the resolver's own handling of an ImportError
    is what is under test.
    """
    from agent import anthropic_client
    from agent.llm import compat

    def _boom():
        raise ImportError("stubbed by test_llm_import_safety")

    monkeypatch.setattr(anthropic_client, "_load_stack", _boom)
    monkeypatch.setattr(compat, "_DEGRADED", None)
    monkeypatch.setattr(compat, "_LOADER_OK", True)
    monkeypatch.setattr(compat, "_COMPATIBLE", True)
    monkeypatch.delenv("LLM_STACK_COMPAT_OVERRIDE", raising=False)
    return compat


@pytest.fixture
def captures(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    monkeypatch.setattr(
        sentry_sdk,
        "capture_message",
        lambda message, **kw: calls.append({"message": message, **kw}),
    )
    return calls


async def test_run_typed_raises_typed_and_fires_all_channels(broken_loader, captures, caplog):
    """The no-startup-hook path: the first call resolves, alerts, and raises.

    A process that never ran a startup hook is exactly where the six-hour
    silent outage lived. Because the alert is bound to flag resolution, the
    first ``run_typed`` call is also the first alert.
    """
    from pydantic import BaseModel

    from agent.llm import run_typed
    from agent.llm.wrapper import LLMCallError, LLMStackIncompatible

    class Out(BaseModel):
        answer: str

    compat = broken_loader
    assert compat._DEGRADED is None, "no startup hook has run in this process"
    caplog.set_level(logging.DEBUG, logger="agent.llm.compat")

    with pytest.raises(LLMStackIncompatible) as excinfo:
        await run_typed("hello", Out)

    # Subclassing is what keeps every existing fail-safe working unchanged.
    assert isinstance(excinfo.value, LLMCallError)

    assert any(
        r.levelno == logging.CRITICAL and compat.SENTINEL in r.getMessage() for r in caplog.records
    )
    assert [c["level"] for c in captures] == ["fatal"]
    assert compat.SENTINEL in captures[0]["message"]

    marker = compat._marker_path("bridge")
    assert not marker.exists(), "a no-proc caller must strand no marker"

    # ...while a bridge-shaped resolution does write one.
    compat._DEGRADED = None
    compat._resolve_degraded_flag("bridge")
    assert json.loads(marker.read_text())["axis"] == "loader"
