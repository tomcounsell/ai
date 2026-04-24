"""Unit tests for the design-system drift validator hook."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = (
    Path(__file__).resolve().parents[3] / ".claude/hooks/validators/validate_design_system_sync.py"
)
REPO_ROOT = Path(__file__).resolve().parents[3]


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
    """my-brand.css / source.css.bak must NOT match (Risk 6)."""
    rc, stdout, _ = _run_hook(
        {"tool_name": "Bash", "tool_input": {"command": "git add my-brand.css"}}
    )
    assert rc == 0
    assert stdout == ""
    rc, stdout, _ = _run_hook(
        {"tool_name": "Bash", "tool_input": {"command": "git add foo/source.css.bak"}}
    )
    assert rc == 0
    assert stdout == ""


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
