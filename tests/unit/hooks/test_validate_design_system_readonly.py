"""Unit tests for the design-system read-only artifact validator hook."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = (
    Path(__file__).resolve().parents[3]
    / ".claude/hooks/validators/validate_design_system_readonly.py"
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
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_blocks_write_to_generated_markdown():
    rc, stdout, _ = _run_hook(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": "tests/fixtures/design_system/design-system.md"},
        }
    )
    assert rc == 0
    data = json.loads(stdout)
    assert data["decision"] == "block"


def test_blocks_edit_to_brand_css():
    rc, stdout, _ = _run_hook(
        {"tool_name": "Edit", "tool_input": {"file_path": "static/css/brand.css"}}
    )
    assert rc == 0
    data = json.loads(stdout)
    assert data["decision"] == "block"


def test_blocks_export_files():
    for name in (
        "docs/designs/exports/tokens.dtcg.json",
        "docs/designs/exports/tailwind.theme.json",
    ):
        rc, stdout, _ = _run_hook({"tool_name": "Write", "tool_input": {"file_path": name}})
        assert rc == 0, name
        data = json.loads(stdout)
        assert data["decision"] == "block", name


def test_permits_pen_writes():
    rc, stdout, _ = _run_hook(
        {"tool_name": "Write", "tool_input": {"file_path": "docs/designs/design-system.pen"}}
    )
    assert rc == 0
    assert stdout == ""


def test_permits_unrelated_files():
    rc, stdout, _ = _run_hook({"tool_name": "Write", "tool_input": {"file_path": "src/app.py"}})
    assert rc == 0
    assert stdout == ""


def test_path_anchor_rejects_false_positive_suffixes():
    """my-brand.css should NOT be blocked (not a design-system artifact)."""
    rc, stdout, _ = _run_hook(
        {"tool_name": "Write", "tool_input": {"file_path": "notes/my-brand.css"}}
    )
    assert rc == 0
    assert stdout == ""


def test_escape_hatch_permits_would_block_write():
    rc, stdout, _ = _run_hook(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": "tests/fixtures/design_system/design-system.md"},
        },
        env_extra={"DESIGN_SYSTEM_HOOK_DISABLED": "1"},
    )
    assert rc == 0
    assert stdout == ""


def test_non_write_edit_tool_is_no_op():
    rc, stdout, _ = _run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert rc == 0
    assert stdout == ""
