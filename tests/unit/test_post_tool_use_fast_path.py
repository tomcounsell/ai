"""Fast-path / import-deferral tests for the PostToolUse catch-all hook.

The hook fires on EVERY tool call, so its latency is dominated by whether it
imports the popoto-heavy ``hook_utils.memory_bridge`` module (via
``config.memory_defaults`` -> ``from popoto import Defaults``). These tests pin
the optimization: the common "ignored tool / counter-only" path must early-exit
WITHOUT importing that module, while still producing sidecar state identical to
the real ``memory_bridge.recall()``.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.db_claim import subprocess_env

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOK = REPO_ROOT / ".claude" / "hooks" / "post_tool_use.py"

_HEAVY_MODULES = ("popoto", "redis", "config.settings", "models.memory", "models.agent_session")

_GUARD_SHIM_DUMP = """\
import time: self [us] | cumulative | imported package
import time:       120 |        120 |   site
import time:        45 |         45 |     tools.redis_flush_guard
import time:        30 |         30 |     _redis_flush_guard_boot
import time: cached    | cached     | _redis_flush_guard_boot
import time: truncated output
import time:  120 |  120
"""

_REAL_HEAVY_DUMP = """\
import time: self [us] | cumulative | imported package
import time:       310 |        310 |   redis
import time:       220 |        530 |     popoto.models
import time:       140 |        140 |   config.settings
"""


def _importtime_module_names(stderr: str) -> list[str]:
    """Parse module names out of a ``-X importtime`` stderr dump.

    Row contract (stable since Python 3.7, verified on CPython 3.14.5):
    each import row is ``import time: {self_us} | {cumulative_us} |
    {indent}{module_name}``, preceded by a header row whose final column is
    the literal ``imported package`` (the only column with an interior
    space, which is what lets us skip the header generically). ``-X
    importtime=2`` additionally emits ``import time: cached | cached |
    {name}`` rows sharing the same three-column shape. Dotted submodule
    names appear in full (e.g. ``json.scanner``), not as bare leaves.
    Malformed rows (fewer than 3 pipe-delimited fields — CPython documents
    that importtime output "may be broken in multi-threaded applications")
    are skipped rather than treated as fatal.
    """
    names = []
    for line in stderr.splitlines():
        if not line.startswith("import time:"):
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        name = parts[-1].strip().lstrip("│├└─ \t")
        if not name or " " in name:
            continue
        names.append(name)
    return names


def _heavy_import_offenders(stderr: str, heavy: tuple[str, ...] = _HEAVY_MODULES) -> list[str]:
    """Return which ``heavy`` module names are genuinely present in a
    ``-X importtime`` stderr dump.

    Matches on module *identity* (exact name or dotted descendant) rather
    than substring containment. Substring matching produced a false
    positive for #2692: ``tools.redis_flush_guard`` and
    ``_redis_flush_guard_boot`` merely contain "redis" as a substring — they
    are not the ``redis`` package and not descendants of it — so a plain
    ``"redis" in stderr`` check blamed the hook for an import it never
    performed.
    """
    names = _importtime_module_names(stderr)
    offenders = {h for h in heavy for n in names if n == h or n.startswith(h + ".")}
    return sorted(offenders)


def _sidecar(session_id: str) -> Path:
    return REPO_ROOT / "data" / "sessions" / session_id / "memory_buffer.json"


def _run_hook(event: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        env=subprocess_env(project_root=str(REPO_ROOT)),
    )


@pytest.fixture
def clean_session():
    sid = "test-post-tool-fast-path"
    d = REPO_ROOT / "data" / "sessions" / sid
    shutil.rmtree(d, ignore_errors=True)
    yield sid
    shutil.rmtree(d, ignore_errors=True)


def test_ignored_tool_event_exits_zero(clean_session):
    """An ignored tool (Read) event runs cleanly and exits 0."""
    event = {
        "session_id": clean_session,
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/x.txt"},
        "tool_output": "hello",
        "cwd": str(REPO_ROOT),
    }
    result = _run_hook(event)
    assert result.returncode == 0, result.stderr


def test_counter_only_call_does_not_import_popoto(clean_session):
    """The counter-only fast path must NOT pull memory_bridge/popoto/redis.

    Uses ``-X importtime`` so we observe the real subprocess import graph.
    """
    event = {
        "session_id": clean_session,
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/x.txt"},
        "tool_output": "hello",
        "cwd": str(REPO_ROOT),
    }
    proc = subprocess.run(
        [sys.executable, "-X", "importtime", str(HOOK)],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        env=subprocess_env(project_root=str(REPO_ROOT)),
    )
    assert proc.returncode == 0, proc.stderr
    offenders = _heavy_import_offenders(proc.stderr)
    assert not offenders, f"counter-only path imported heavy modules: {offenders}"


def test_heavy_detection_ignores_lookalike_module_names():
    """``tools.redis_flush_guard`` / ``_redis_flush_guard_boot`` must NOT be
    mistaken for the ``redis`` package: they merely contain "redis" as a
    substring. Also covers Risk 2's malformed-row and empty-input guards."""
    assert _heavy_import_offenders(_GUARD_SHIM_DUMP) == []
    assert _heavy_import_offenders("") == []


def test_heavy_detection_still_catches_real_imports():
    """A genuine top-level ``redis``, a ``popoto.models`` descendant, and an
    exact ``config.settings`` must still be flagged (over-loosening guard)."""
    assert set(_heavy_import_offenders(_REAL_HEAVY_DUMP)) == {
        "redis",
        "popoto",
        "config.settings",
    }


def test_counter_only_call_bumps_sidecar_counter(clean_session):
    """The fast path still persists the sliding-window counter + buffer."""
    event = {
        "session_id": clean_session,
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/x.txt"},
        "tool_output": "hello",
        "cwd": str(REPO_ROOT),
    }
    _run_hook(event)
    state = json.loads(_sidecar(clean_session).read_text())
    assert state["count"] == 1
    assert state["buffer"] == [{"tool_name": "Read", "tool_input": {"file_path": "/tmp/x.txt"}}]


def test_fast_path_state_matches_real_recall():
    """The hook helper tracks the same window as ``recall()`` but never injects.

    ``count`` and ``buffer`` stay in lockstep with ``memory_bridge.recall()``
    -- that is the sidecar contract ``prefetch()`` load-modify-saves around.

    ``injected`` deliberately does NOT: PostToolUse has no channel to deliver
    a thought stub, so the hook path must never mark a memory as injected.
    Doing so suppressed it from ``prefetch()`` (which excludes anything in
    ``injected[]``) and fed the outcome loop an access the model never saw.

    This assertion is one-sided on purpose. It reads as vacuous whenever the
    fixture's synthetic tool names miss the bloom filter and ``recall()``
    injects nothing either -- which is the common case here. It is still the
    assertion that catches a regression on a populated store.
    """
    sys.path.insert(0, str(REPO_ROOT / ".claude" / "hooks"))
    sys.path.insert(0, str(REPO_ROOT))
    import post_tool_use as p
    from hook_utils import memory_bridge as mb

    events = [{"tool_name": f"T{i}", "tool_input": {"file_path": f"/x/{i}.py"}} for i in range(7)]

    a, b = "test-parity-recall", "test-parity-inline"
    for sid in (a, b):
        shutil.rmtree(REPO_ROOT / "data" / "sessions" / sid, ignore_errors=True)
    try:
        for e in events:
            mb.recall(a, e["tool_name"], e["tool_input"], cwd=str(REPO_ROOT))
        for e in events:
            p._run_memory_recall(
                {
                    "session_id": b,
                    "tool_name": e["tool_name"],
                    "tool_input": e["tool_input"],
                    "cwd": str(REPO_ROOT),
                }
            )
        state_a = json.loads(_sidecar(a).read_text())
        state_b = json.loads(_sidecar(b).read_text())
        assert state_a["count"] == state_b["count"]
        assert state_a["buffer"] == state_b["buffer"]
        # The hook path never records injections, whatever recall() did.
        assert not state_b.get("injected")
    finally:
        for sid in (a, b):
            shutil.rmtree(REPO_ROOT / "data" / "sessions" / sid, ignore_errors=True)
