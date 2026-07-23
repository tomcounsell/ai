"""Unit tests for the design-system drift validator hook."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = (
    Path(__file__).resolve().parents[3] / ".claude/hooks/validators/validate_design_system_sync.py"
)
REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_hook_module():
    """Import the hook module by path so we can probe its compiled regex."""
    spec = importlib.util.spec_from_file_location("_dss_hook_under_test", HOOK)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run_hook(payload: dict, env_extra: dict | None = None) -> tuple[int, str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(REPO_ROOT))
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_non_bash_tool_is_no_op():
    rc, stdout, _ = _run_hook({"tool_name": "Write", "tool_input": {"file_path": "x"}})
    assert rc == 0
    assert stdout == ""


def test_unrelated_commit_is_no_op():
    rc, stdout, _ = _run_hook({"tool_name": "Bash", "tool_input": {"command": "git add README.md"}})
    assert rc == 0
    assert stdout == ""


def test_path_anchored_regex_rejects_false_positive_suffixes():
    """my-brand.css / source.css.bak must NOT match (Risk 6).

    Asserts at the regex-compile level so the rejection is locked in
    even if `_find_pen_path` short-circuits earlier in the hook flow
    (which is what made the v1 test pass for the wrong reason).
    """
    pattern = _load_hook_module()._COMMAND_REGEX
    assert pattern.search("git add my-brand.css") is None
    assert pattern.search("git add foo/source.css.bak") is None
    assert pattern.search("git add foo/source.css.tmp") is None
    assert pattern.search("git add foo/source.css.orig") is None
    assert pattern.search("git add report.md") is None


def test_regex_matches_bare_filename_after_git_add():
    """`git add design-system.pen` (no leading dir) must match (Risk 6 fix).

    Regression: the prior `(?:^|/)` prefix only matched when the file
    sat under a subdirectory; users who `cd`-ed into the design-system
    folder could silently commit drift.
    """
    pattern = _load_hook_module()._COMMAND_REGEX
    for cmd in (
        "git add design-system.pen",
        "git add design-system.md",
        "git add brand.css",
        "git add source.css",
        "git add tests/fixtures/design_system/design-system.pen",
        "git commit -am 'update brand.css'",
    ):
        assert pattern.search(cmd) is not None, f"expected match: {cmd!r}"


def test_matching_clean_state_allows_commit():
    """Fixture is byte-identical with generator output → no block."""
    rc, stdout, _ = _run_hook(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "git add tests/fixtures/design_system/design-system.pen"},
        }
    )
    assert rc == 0
    assert stdout == ""


def test_escape_hatch_returns_immediately():
    rc, stdout, _ = _run_hook(
        {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}},
        env_extra={"DESIGN_SYSTEM_HOOK_DISABLED": "1"},
    )
    assert rc == 0
    assert stdout == ""


def test_drift_produces_block_decision(tmp_path: Path, monkeypatch):
    """Mutate a fixture file out-of-band; hook should emit decision:block."""
    fixture = REPO_ROOT / "tests/fixtures/design_system/css/brand.css"
    backup = fixture.read_text(encoding="utf-8")
    try:
        fixture.write_text(backup + "\n/* drift */\n", encoding="utf-8")
        rc, stdout, _ = _run_hook(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "git add tests/fixtures/design_system/css/brand.css"},
            }
        )
        assert rc == 0  # hook itself succeeds; block is conveyed in stdout
        data = json.loads(stdout)
        assert data["decision"] == "block"
        assert "drift" in data["reason"].lower() or "out of sync" in data["reason"].lower()
    finally:
        fixture.write_text(backup, encoding="utf-8")


def test_crashed_checker_fails_open(tmp_path: Path):
    """A --check subprocess that dies before checking anything (e.g. the hook
    interpreter is missing a dependency) is an internal error, not drift —
    the hook must fail open instead of blocking with a misleading
    "out of sync" message. Real drift is distinguished by the
    "differs from generated" marker every drift path emits.
    """
    # Shadow tools.design_system_sync with a module that crashes on import,
    # the same shape as the real-world `import yaml` ModuleNotFoundError.
    # `-m` puts the subprocess cwd first on sys.path, so running the hook
    # from tmp_path makes the fake win over the repo package.
    fake_pkg = tmp_path / "tools"
    fake_pkg.mkdir()
    (fake_pkg / "__init__.py").write_text("", encoding="utf-8")
    (fake_pkg / "design_system_sync.py").write_text(
        "raise ModuleNotFoundError(\"No module named 'yaml'\")\n", encoding="utf-8"
    )
    # _find_pen_path searches cwd for the fixture layout; give it a .pen so
    # the hook reaches the subprocess instead of short-circuiting.
    pen_dir = tmp_path / "tests" / "fixtures" / "design_system"
    pen_dir.mkdir(parents=True)
    real_pen = REPO_ROOT / "tests/fixtures/design_system/design-system.pen"
    (pen_dir / "design-system.pen").write_bytes(real_pen.read_bytes())

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "git add tests/fixtures/design_system/design-system.pen"},
            }
        ),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
    )
    assert proc.returncode == 0
    assert proc.stdout == ""  # no block decision
    assert "fail-open" in proc.stderr

    log_path = REPO_ROOT / "logs/validate_design_system_sync.jsonl"
    latest = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert latest["result"] == "error"
    assert "without drift marker" in latest["error"]


def test_jsonl_log_records_each_invocation():
    log_path = REPO_ROOT / "logs/validate_design_system_sync.jsonl"
    before = log_path.read_text(encoding="utf-8").splitlines() if log_path.is_file() else []
    _run_hook({"tool_name": "Bash", "tool_input": {"command": "git add README.md"}})
    after = log_path.read_text(encoding="utf-8").splitlines() if log_path.is_file() else []
    assert len(after) >= len(before) + 1
    latest = json.loads(after[-1])
    assert latest["tool_name"] == "Bash"
    assert latest["matched"] is False
    assert latest["result"] == "ok"


def test_jsonl_log_captures_bypass():
    log_path = REPO_ROOT / "logs/validate_design_system_sync.jsonl"
    before = log_path.read_text(encoding="utf-8").splitlines() if log_path.is_file() else []
    _run_hook(
        {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}},
        env_extra={"DESIGN_SYSTEM_HOOK_DISABLED": "1"},
    )
    after = log_path.read_text(encoding="utf-8").splitlines() if log_path.is_file() else []
    assert len(after) >= len(before) + 1
    latest = json.loads(after[-1])
    assert latest["result"] == "bypassed"


def test_log_path_anchored_to_repo_root_not_cwd(tmp_path: Path):
    """Regression for #1901: running from a non-repo-root cwd must not
    create a stray cwd-relative logs/ directory (flagged as a husk by the
    skills-audit reflection's rule_19_husk_directories).
    """
    log_path = REPO_ROOT / "logs/validate_design_system_sync.jsonl"
    before = log_path.read_text(encoding="utf-8").splitlines() if log_path.is_file() else []

    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(REPO_ROOT))
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "git add README.md"}}),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
    )
    assert proc.returncode == 0

    after = log_path.read_text(encoding="utf-8").splitlines() if log_path.is_file() else []
    assert len(after) >= len(before) + 1
    latest = json.loads(after[-1])
    assert latest["tool_name"] == "Bash"

    assert not (tmp_path / "logs").exists()
