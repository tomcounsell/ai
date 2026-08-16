"""Isolation tests for import-time logging side effects (issue #2643).

Importing `monitoring.bridge_watchdog`, `monitoring.worker_watchdog`, or
`scripts.log_rotate` must have zero logging side effects: no handler on root,
no handler holding a production log file, no directory created. Configuration
happens exactly once, at each script's entry point (`if __name__ ==
"__main__":`), where launchd reaches it.

Every subprocess probe pins its own environment (`PYTHONPATH=REPO_ROOT`) and
starts with a checkout self-check (`m.__file__.startswith(str(REPO_ROOT))`)
so a wrong-checkout import fails loudly instead of reporting green about
unmodified source. Every in-process case that calls a configure function runs
inside `_snapshot_logging_state`, which snapshots and restores the root
logger's level and handler list plus both named loggers' handlers,
`propagate`, and `level`, and `close()`s every handler it created — this
module mutates process-global logging state, and leaking it corrupts sibling
tests in the same xdist worker.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import pathlib
import re
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _subprocess_env() -> dict:
    return {**os.environ, "PYTHONPATH": str(REPO_ROOT)}


def _run_probe(probe_src: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", probe_src, str(REPO_ROOT)],
        cwd=REPO_ROOT,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def _snapshot_logging_state():
    """Snapshot/restore root + the two named module loggers around a test.

    Also closes any handler this fixture did not see before the test, so a
    RotatingFileHandler opened mid-test does not leak an open fd into later
    tests.
    """
    root = logging.getLogger()
    named = [
        logging.getLogger("monitoring.bridge_watchdog"),
        logging.getLogger("monitoring.worker_watchdog"),
        logging.getLogger("log_rotate"),
    ]
    before = {
        id(root): (root.level, list(root.handlers)),
        **{id(lg): (lg.level, list(lg.handlers), lg.propagate) for lg in named},
    }
    yield
    root.level, root.handlers[:] = before[id(root)][0], before[id(root)][1]
    for lg in named:
        level, handlers, propagate = before[id(lg)]
        for h in lg.handlers:
            if h not in handlers:
                try:
                    h.close()
                except Exception:
                    pass
        lg.level = level
        lg.handlers[:] = handlers
        lg.propagate = propagate


# --------------------------------------------------------------------------
# TC1 / TC1a — bridge watchdog import leaves root untouched
# --------------------------------------------------------------------------


def test_tc1_bridge_import_leaves_root_clean():
    probe = f"""
import json, logging, sys
sys.path.insert(0, {str(REPO_ROOT)!r})
import monitoring.bridge_watchdog as m
assert m.__file__.startswith({str(REPO_ROOT)!r}), m.__file__
r = logging.getLogger()
print(json.dumps({{"handlers": [type(h).__name__ for h in r.handlers], "level": r.level}}))
"""
    result = _run_probe(probe)
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout.strip().splitlines()[-1])
    assert state["handlers"] == [], state
    assert state["level"] == logging.WARNING, state


def test_tc1a_basicconfig_spy_records_no_forbidden_caller():
    probe = f"""
import json, logging, sys
sys.path.insert(0, {str(REPO_ROOT)!r})
callers = []
_orig = logging.basicConfig
def _spy(*a, **kw):
    callers.append(sys._getframe(1).f_code.co_filename)
    return _orig(*a, **kw)
logging.basicConfig = _spy
import monitoring.bridge_watchdog as m
assert m.__file__.startswith({str(REPO_ROOT)!r}), m.__file__
print(json.dumps(callers))
"""
    result = _run_probe(probe)
    assert result.returncode == 0, result.stderr
    callers = json.loads(result.stdout.strip().splitlines()[-1])
    monitoring_dir = str(REPO_ROOT / "monitoring")
    log_rotate_path = str(REPO_ROOT / "scripts" / "log_rotate.py")
    forbidden = [c for c in callers if c.startswith(monitoring_dir) or c == log_rotate_path]
    assert forbidden == [], callers


# --------------------------------------------------------------------------
# TC2 — no logger anywhere holds a handler on watchdog.log
# --------------------------------------------------------------------------


def test_tc2_no_handler_holds_watchdog_log():
    probe = f"""
import json, logging, sys
sys.path.insert(0, {str(REPO_ROOT)!r})
import monitoring.bridge_watchdog as m
assert m.__file__.startswith({str(REPO_ROOT)!r}), m.__file__
hits = []
loggers = [logging.getLogger()] + [
    logging.getLogger(name) for name in logging.root.manager.loggerDict
]
for lg in loggers:
    if not isinstance(lg, logging.Logger):
        continue
    for h in getattr(lg, "handlers", []):
        path = getattr(h, "baseFilename", "")
        if path and __import__("pathlib").Path(path).name == "watchdog.log":
            hits.append(lg.name)
print(json.dumps(hits))
"""
    result = _run_probe(probe)
    assert result.returncode == 0, result.stderr
    hits = json.loads(result.stdout.strip().splitlines()[-1])
    assert hits == [], hits


# --------------------------------------------------------------------------
# TC3 / TC4 — worker watchdog import leaves root untouched, no log handle
# --------------------------------------------------------------------------


def test_tc3_worker_import_leaves_root_clean():
    probe = f"""
import json, logging, sys
sys.path.insert(0, {str(REPO_ROOT)!r})
import monitoring.worker_watchdog as m
assert m.__file__.startswith({str(REPO_ROOT)!r}), m.__file__
r = logging.getLogger()
print(json.dumps({{"handlers": [type(h).__name__ for h in r.handlers], "level": r.level}}))
"""
    result = _run_probe(probe)
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout.strip().splitlines()[-1])
    assert state["handlers"] == [], state
    assert state["level"] == logging.WARNING, state


def test_tc4_no_handler_holds_worker_watchdog_log():
    probe = f"""
import json, logging, sys
sys.path.insert(0, {str(REPO_ROOT)!r})
import monitoring.worker_watchdog as m
assert m.__file__.startswith({str(REPO_ROOT)!r}), m.__file__
hits = []
loggers = [logging.getLogger()] + [
    logging.getLogger(name) for name in logging.root.manager.loggerDict
]
for lg in loggers:
    if not isinstance(lg, logging.Logger):
        continue
    for h in getattr(lg, "handlers", []):
        path = getattr(h, "baseFilename", "")
        if path and __import__("pathlib").Path(path).name == "worker_watchdog.log":
            hits.append(lg.name)
print(json.dumps(hits))
"""
    result = _run_probe(probe)
    assert result.returncode == 0, result.stderr
    hits = json.loads(result.stdout.strip().splitlines()[-1])
    assert hits == [], hits


# --------------------------------------------------------------------------
# TC5 / TC5b — AST scope-aware guard over the 14-file glob
# --------------------------------------------------------------------------


def _guarded_files() -> list[pathlib.Path]:
    files = sorted((REPO_ROOT / "monitoring").glob("*.py")) + [
        REPO_ROOT / "scripts" / "log_rotate.py"
    ]
    assert len(files) >= 14, files
    assert {p.name for p in files} >= {
        "bridge_watchdog.py",
        "worker_watchdog.py",
        "log_rotate.py",
    }, files
    return files


def _is_entrypoint_guard(node) -> bool:
    import ast

    t = node.test
    return (
        isinstance(t, ast.Compare)
        and isinstance(t.left, ast.Name)
        and t.left.id == "__name__"
        and len(t.ops) == 1
        and isinstance(t.ops[0], ast.Eq)
        and len(t.comparators) == 1
        and isinstance(t.comparators[0], ast.Constant)
        and t.comparators[0].value == "__main__"
    )


_FORBIDDEN_CALL_NAMES = {
    "basicConfig",
    "addHandler",
    "mkdir",
    "_configure_logger",
    "_configure_logging",
}


def _module_scope_forbidden_calls(tree, path: pathlib.Path) -> list[str]:
    """Walk module-level statements (not entry-point guard bodies, not
    def/class bodies) and report forbidden calls."""
    import ast

    hits: list[str] = []

    def call_name(node) -> str | None:
        if not isinstance(node, ast.Call):
            return None
        f = node.func
        if isinstance(f, ast.Attribute):
            return f.attr
        if isinstance(f, ast.Name):
            return f.id
        return None

    def walk(body, in_try_or_with_ok=True):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(node, ast.If):
                if _is_entrypoint_guard(node):
                    walk(node.orelse)
                    continue
                walk(node.body)
                walk(node.orelse)
                continue
            if isinstance(node, (ast.Try,)):
                walk(node.body)
                for handler in node.handlers:
                    walk(handler.body)
                walk(node.orelse)
                walk(node.finalbody)
                continue
            if isinstance(node, ast.With):
                walk(node.body)
                continue
            for sub in ast.walk(node):
                name = call_name(sub)
                if name in _FORBIDDEN_CALL_NAMES:
                    hits.append(f"{path}:{getattr(sub, 'lineno', '?')} {name}")

    import ast as _ast  # noqa: F401

    walk(tree.body)
    return hits


def test_tc5_no_module_scope_logging_side_effects():
    import ast

    all_hits: list[str] = []
    for path in _guarded_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        all_hits.extend(_module_scope_forbidden_calls(tree, path))
    assert all_hits == [], all_hits


def test_tc5b_configure_call_is_direct_child_of_main_guard():
    """Per module, the configure call is a direct child of `if __name__ ==
    "__main__":` — a separate walker from TC5's exemption walk."""
    import ast

    expectations = {
        REPO_ROOT / "monitoring" / "bridge_watchdog.py": "_configure_logging",
        REPO_ROOT / "monitoring" / "worker_watchdog.py": "_configure_logger",
        REPO_ROOT / "scripts" / "log_rotate.py": "basicConfig",
    }
    for path, expected_name in expectations.items():
        tree = ast.parse(path.read_text(), filename=str(path))
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and _is_entrypoint_guard(node):
                for stmt in node.body:
                    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                        f = stmt.value.func
                        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                        if name == expected_name:
                            found = True
        assert found, f"{path} does not call {expected_name}() as a direct child of __main__ guard"


# --------------------------------------------------------------------------
# TC6 — bridge logger attributes
# --------------------------------------------------------------------------


def test_tc6_bridge_logger_attributes():
    probe = f"""
import json, sys
sys.path.insert(0, {str(REPO_ROOT)!r})
import logging
import monitoring.bridge_watchdog as bw
assert bw.__file__.startswith({str(REPO_ROOT)!r}), bw.__file__
print(json.dumps({{
    "propagate": bw.logger.propagate,
    "level": bw.logger.level,
    "enabled_for_info": bw.logger.isEnabledFor(logging.INFO),
}}))
"""
    result = _run_probe(probe)
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout.strip().splitlines()[-1])
    assert state["propagate"] is False, state
    assert state["level"] == logging.INFO, state
    assert state["enabled_for_info"] is True, state


# --------------------------------------------------------------------------
# TC7-TC9, TC12 — bridge _configure_logging() behavior
# --------------------------------------------------------------------------


def test_tc7_configure_logging_writes_formatted_record(tmp_path, _snapshot_logging_state):
    import monitoring.bridge_watchdog as bw

    target = tmp_path / "nested" / "watchdog.log"
    bw.WATCHDOG_LOG_FILE = target
    try:
        bw._configure_logging()
        bw.logger.info("probe")
        for h in bw.logger.handlers:
            h.flush()
        assert target.exists()
        lines = [ln for ln in target.read_text().splitlines() if ln]
        assert len(lines) == 1
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \[INFO\] probe$", lines[0])
        owned = [h for h in bw.logger.handlers if getattr(h, "_watchdog_owned", False)]
        assert len(owned) == 1
        assert owned[0].maxBytes == 10 * 1024 * 1024
        assert owned[0].backupCount == 5
    finally:
        for h in list(bw.logger.handlers):
            if getattr(h, "_watchdog_owned", False):
                bw.logger.removeHandler(h)
                h.close()


def test_tc8_configure_logging_does_not_touch_root(tmp_path, _snapshot_logging_state):
    import monitoring.bridge_watchdog as bw

    target = tmp_path / "watchdog.log"
    bw.WATCHDOG_LOG_FILE = target
    root_handlers_before = list(logging.getLogger().handlers)
    try:
        bw._configure_logging()
        assert logging.getLogger().handlers == root_handlers_before
        assert not any(getattr(h, "_watchdog_owned", False) for h in logging.getLogger().handlers)
    finally:
        for h in list(bw.logger.handlers):
            if getattr(h, "_watchdog_owned", False):
                bw.logger.removeHandler(h)
                h.close()


def test_tc9_configure_logging_is_idempotent(tmp_path, _snapshot_logging_state):
    import monitoring.bridge_watchdog as bw

    target = tmp_path / "watchdog.log"
    bw.WATCHDOG_LOG_FILE = target
    try:
        bw._configure_logging()
        bw._configure_logging()
        bw.logger.info("probe")
        for h in bw.logger.handlers:
            h.flush()
        lines = [ln for ln in target.read_text().splitlines() if ln]
        assert len(lines) == 1
        owned = [h for h in bw.logger.handlers if getattr(h, "_watchdog_owned", False)]
        assert len(owned) == 1
    finally:
        for h in list(bw.logger.handlers):
            if getattr(h, "_watchdog_owned", False):
                bw.logger.removeHandler(h)
                h.close()


def test_tc12_configure_logging_preserves_unowned_handlers(tmp_path, _snapshot_logging_state):
    import monitoring.bridge_watchdog as bw

    target = tmp_path / "watchdog.log"
    bw.WATCHDOG_LOG_FILE = target
    sentinel = logging.NullHandler()
    bw.logger.addHandler(sentinel)
    try:
        bw._configure_logging()
        assert sentinel in bw.logger.handlers
    finally:
        for h in list(bw.logger.handlers):
            if getattr(h, "_watchdog_owned", False):
                bw.logger.removeHandler(h)
                h.close()
        if sentinel in bw.logger.handlers:
            bw.logger.removeHandler(sentinel)


# --------------------------------------------------------------------------
# TC10 / TC11 — worker mirror of TC7-TC9
# --------------------------------------------------------------------------


def test_tc10_worker_configure_logger_writes_formatted_record(tmp_path, _snapshot_logging_state):
    import monitoring.worker_watchdog as wwd

    target = tmp_path / "nested" / "worker_watchdog.log"
    wwd.LOG_FILE = target
    try:
        wwd._configure_logger()
        wwd.logger.info("probe")
        for h in wwd.logger.handlers:
            h.flush()
        lines = [ln for ln in target.read_text().splitlines() if ln]
        assert len(lines) == 1
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \[INFO\] probe$", lines[0])
        owned = [h for h in wwd.logger.handlers if getattr(h, "_watchdog_owned", False)]
        assert len(owned) == 1
        assert owned[0].maxBytes == 5 * 1024 * 1024
        assert owned[0].backupCount == 3
        assert wwd.logger.propagate is False
    finally:
        for h in list(wwd.logger.handlers):
            if getattr(h, "_watchdog_owned", False):
                wwd.logger.removeHandler(h)
                h.close()


def test_tc11_worker_configure_logger_is_idempotent(tmp_path, _snapshot_logging_state):
    import monitoring.worker_watchdog as wwd

    target = tmp_path / "worker_watchdog.log"
    wwd.LOG_FILE = target
    try:
        wwd._configure_logger()
        wwd._configure_logger()
        wwd.logger.info("probe")
        for h in wwd.logger.handlers:
            h.flush()
        lines = [ln for ln in target.read_text().splitlines() if ln]
        assert len(lines) == 1
        owned = [h for h in wwd.logger.handlers if getattr(h, "_watchdog_owned", False)]
        assert len(owned) == 1
    finally:
        for h in list(wwd.logger.handlers):
            if getattr(h, "_watchdog_owned", False):
                wwd.logger.removeHandler(h)
                h.close()


# --------------------------------------------------------------------------
# TC13 / TC14 — log_rotate
# --------------------------------------------------------------------------


def test_tc13_log_rotate_import_leaves_root_clean():
    probe = f"""
import json, logging, sys
sys.path.insert(0, {str(REPO_ROOT)!r})
import scripts.log_rotate as m
assert m.__file__.startswith({str(REPO_ROOT)!r}), m.__file__
r = logging.getLogger()
print(json.dumps({{"handlers": [type(h).__name__ for h in r.handlers], "level": r.level}}))
"""
    result = _run_probe(probe)
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout.strip().splitlines()[-1])
    assert state["handlers"] == [], state
    assert state["level"] == logging.WARNING, state


def test_tc14_log_rotate_logger_pinned_leveled_propagating():
    probe = f"""
import json, logging, sys
sys.path.insert(0, {str(REPO_ROOT)!r})
import scripts.log_rotate as lr
assert lr.__file__.startswith({str(REPO_ROOT)!r}), lr.__file__
print(json.dumps({{
    "name": lr.logger.name,
    "level": lr.logger.level,
    "propagate": lr.logger.propagate,
}}))
"""
    result = _run_probe(probe)
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout.strip().splitlines()[-1])
    assert state["name"] == "log_rotate", state
    assert state["level"] == logging.INFO, state
    assert state["propagate"] is True, state


# --------------------------------------------------------------------------
# TC15 — log_rotate entry-point proof, in a fresh subprocess against a
# temp-dir copy of the real source (never the real checkout's logs/).
# --------------------------------------------------------------------------


def test_tc15_log_rotate_script_mode_entrypoint_proof():
    real_source = REPO_ROOT / "scripts" / "log_rotate.py"
    probe = f"""
import json, logging, runpy, shutil, sys, tempfile
from pathlib import Path

# This probe runs in a fresh subprocess (spawned below via subprocess.run),
# so it starts with a clean root regardless of how dirty the parent pytest
# process's root is (LogCaptureHandler, etc.) — that dirtiness is the "parent
# deliberately dirtied" proof and needs no extra staging here.

tmpdir = Path(tempfile.mkdtemp())
scripts_dir = tmpdir / "scripts"
scripts_dir.mkdir()
copy_path = scripts_dir / "log_rotate.py"
shutil.copy({str(real_source)!r}, copy_path)

sys.argv = ["log_rotate.py"]
exit_code = 0
try:
    runpy.run_path(str(copy_path), run_name="__main__")
except SystemExit as e:
    exit_code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)

root = logging.getLogger()
handler_kinds = [type(h).__name__ for h in root.handlers]
stream_handlers = [
    h for h in root.handlers
    if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
]
fmt = stream_handlers[0].formatter._fmt if stream_handlers else None
stream_is_stderr = stream_handlers[0].stream is sys.stderr if stream_handlers else False
lr_logger = logging.getLogger("log_rotate")

print(json.dumps({{
    "exit_code": exit_code,
    "root_handlers": handler_kinds,
    "root_level": root.level,
    "stream_is_stderr": stream_is_stderr,
    "fmt": fmt,
    "propagate": lr_logger.propagate,
}}))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout.strip().splitlines()[-1])
    assert state["exit_code"] == 0, state
    assert state["root_handlers"].count("StreamHandler") == 1, state
    assert state["root_level"] == logging.INFO, state
    assert state["stream_is_stderr"] is True, state
    assert state["fmt"] == "%(asctime)s %(levelname)s %(message)s", state
    assert state["propagate"] is True, state
    stderr_lines = [ln for ln in result.stderr.splitlines() if ln]
    pattern = re.compile(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} INFO done: rotated=0 skipped=0$"
    )
    assert any(pattern.match(ln) for ln in stderr_lines), result.stderr
