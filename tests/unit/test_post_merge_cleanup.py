"""Unit tests for scripts/post_merge_cleanup.py exit-code behavior (issue #1357)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "post_merge_cleanup.py"


def _load_script_module():
    """Import scripts/post_merge_cleanup.py as a module for direct main() calls."""
    spec = importlib.util.spec_from_file_location("post_merge_cleanup_test_target", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script_module():
    return _load_script_module()


def test_blocked_session_exits_2(monkeypatch, capsys, script_module):
    """When cleanup_after_merge returns blocked_by_session, script exits 2."""
    fake_result = {
        "slug": "sdlc-1218",
        "worktree_removed": False,
        "branch_deleted": False,
        "already_clean": False,
        "errors": ["blocked: worktree in use by session_id=0_LIVE"],
        "blocked_by_session": "0_LIVE",
    }
    monkeypatch.setattr(script_module, "cleanup_after_merge", lambda _r, _s: fake_result)
    monkeypatch.setattr(sys, "argv", ["post_merge_cleanup.py", "sdlc-1218"])

    rc = script_module.main()
    assert rc == 2

    captured = capsys.readouterr()
    assert "session_id=0_LIVE" in captured.err
    assert ".worktrees/sdlc-1218" in captured.err
    # Stdout should NOT carry the error message
    assert "session_id=0_LIVE" not in captured.out


def test_clean_exits_0(monkeypatch, capsys, script_module):
    """already_clean returns 0."""
    fake_result = {
        "slug": "fresh",
        "worktree_removed": False,
        "branch_deleted": False,
        "already_clean": True,
        "errors": [],
    }
    monkeypatch.setattr(script_module, "cleanup_after_merge", lambda _r, _s: fake_result)
    monkeypatch.setattr(sys, "argv", ["post_merge_cleanup.py", "fresh"])

    rc = script_module.main()
    assert rc == 0
    captured = capsys.readouterr()
    assert "already clean" in captured.out


def test_generic_error_exits_1(monkeypatch, script_module):
    """Generic errors (no blocked_by_session) keep the historical exit-1."""
    fake_result = {
        "slug": "broken",
        "worktree_removed": False,
        "branch_deleted": False,
        "already_clean": False,
        "errors": ["Failed to remove worktree .worktrees/broken"],
    }
    monkeypatch.setattr(script_module, "cleanup_after_merge", lambda _r, _s: fake_result)
    monkeypatch.setattr(sys, "argv", ["post_merge_cleanup.py", "broken"])

    rc = script_module.main()
    assert rc == 1


def test_success_exits_0(monkeypatch, capsys, script_module):
    """Successful cleanup with actions returns 0."""
    fake_result = {
        "slug": "win",
        "worktree_removed": True,
        "branch_deleted": True,
        "already_clean": False,
        "errors": [],
    }
    monkeypatch.setattr(script_module, "cleanup_after_merge", lambda _r, _s: fake_result)
    monkeypatch.setattr(sys, "argv", ["post_merge_cleanup.py", "win"])

    rc = script_module.main()
    assert rc == 0
    captured = capsys.readouterr()
    assert "Removed worktree" in captured.out
    assert "Deleted branch" in captured.out
