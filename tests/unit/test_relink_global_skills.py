"""Tests for the global-skill hardlink repair hook.

The hook exists because Write/Edit replace-and-rename, which allocates a new
inode and silently strands the ``~/.claude/`` copy on the pre-edit text. These
tests reproduce that exact breakage and assert the hook repairs it.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / ".claude" / "hooks" / "validators" / "relink_global_skills.py"


def _run_hook(file_path: Path, project_dir: Path, home: Path):
    """Invoke the hook the way Claude Code does: JSON payload on stdin."""
    env = {
        **os.environ,
        "CLAUDE_PROJECT_DIR": str(project_dir),
        "HOME": str(home),
    }
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_input": {"file_path": str(file_path)}}),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _break_link(src: Path) -> None:
    """Reproduce what Edit/Write do: write a replacement and rename over it.

    This is the whole bug — the content lands, the inode changes, the hardlink
    silently dies.
    """
    tmp = src.with_suffix(src.suffix + ".new")
    tmp.write_text(src.read_text() + "\nedited\n")
    os.replace(tmp, src)


@pytest.fixture
def synced_tree(tmp_path):
    """A project/.claude/skills-global file hardlinked into a fake ~/.claude."""
    project = tmp_path / "project"
    home = tmp_path / "home"
    src = project / ".claude" / "skills-global" / "demo" / "SKILL.md"
    dst = home / ".claude" / "skills" / "demo" / "SKILL.md"
    src.parent.mkdir(parents=True)
    dst.parent.mkdir(parents=True)
    src.write_text("original\n")
    os.link(src, dst)
    assert src.samefile(dst)
    return project, home, src, dst


def test_hook_file_exists():
    assert HOOK.is_file(), f"hook not found at {HOOK}"


def test_edit_breaks_the_hardlink(synced_tree):
    """Guard the premise: if this ever stops failing, the hook is obsolete."""
    _, _, src, dst = synced_tree
    _break_link(src)
    assert not src.samefile(dst)
    assert "edited" not in dst.read_text(), "destination should be stale"


def test_hook_repairs_broken_link(synced_tree):
    project, home, src, dst = synced_tree
    _break_link(src)

    result = _run_hook(src, project, home)

    assert result.returncode == 0
    assert src.samefile(dst), "hook did not re-establish the hardlink"
    assert "edited" in dst.read_text(), "live copy still serving pre-edit text"
    assert "re-established hardlink" in result.stderr


def test_hook_is_silent_when_already_linked(synced_tree):
    """A no-op on every unrelated edit must not spam the transcript."""
    project, home, src, dst = synced_tree
    result = _run_hook(src, project, home)
    assert result.returncode == 0
    assert result.stderr.strip() == ""
    assert src.samefile(dst)


def test_hook_ignores_paths_outside_synced_dirs(tmp_path):
    project = tmp_path / "project"
    home = tmp_path / "home"
    other = project / ".claude" / "hooks" / "thing.py"
    other.parent.mkdir(parents=True)
    home.mkdir()
    other.write_text("x = 1\n")

    result = _run_hook(other, project, home)

    assert result.returncode == 0
    assert result.stderr.strip() == ""


def test_hook_ignores_paths_outside_the_repo(tmp_path):
    project = tmp_path / "project"
    home = tmp_path / "home"
    project.mkdir()
    home.mkdir()
    stray = tmp_path / "elsewhere.md"
    stray.write_text("unrelated\n")

    result = _run_hook(stray, project, home)

    assert result.returncode == 0
    assert result.stderr.strip() == ""


def test_hook_creates_missing_destination(synced_tree):
    """A destination that was never synced should be established, not skipped."""
    project, home, src, dst = synced_tree
    dst.unlink()

    result = _run_hook(src, project, home)

    assert result.returncode == 0
    assert dst.exists() and src.samefile(dst)


def test_hook_registered_for_write_and_edit():
    """The repair only works if the harness actually calls it."""
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text())
    post = settings["hooks"]["PostToolUse"]
    for tool in ("Write", "Edit"):
        # The manifest may register the hook under a plain matcher ("Write")
        # or an alternation matcher ("Write|Edit"); both cover the tool.
        covering = [m for m in post if tool in m.get("matcher", "").split("|")]
        assert covering, f"no PostToolUse entry covering {tool}"
        commands = " ".join(h.get("command", "") for m in covering for h in m["hooks"])
        assert "relink_global_skills.py" in commands, f"relink hook not registered for {tool}"
