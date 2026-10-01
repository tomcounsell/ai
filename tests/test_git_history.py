"""The kernel's git reads history the turn cannot rewrite.

A turn owns its workspace's `.git/refs/replace/` and `.git/info/grafts`,
which are not config, so `git.hostile` cannot see them. Every kernel git
call runs with `GIT_NO_REPLACE_OBJECTS=1` and `GIT_GRAFT_FILE=/dev/null`
(carried from 1.2's open finding, pending Tom), so neither a replace ref nor
a graft changes what `diff_paths` reports or what `is_ancestor` answers.
"""

import subprocess
from pathlib import Path

from core import git as kgit


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip


def commit(ws: Path, path: str, text: str) -> str:
    p = ws / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    sh(ws, "add", path)
    sh(ws, "commit", "-qm", path)
    return sh(ws, "rev-parse", "HEAD")


def repo(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    sh(ws, "init", "-q", "-b", "main")
    return ws


def test_a_replace_ref_cannot_make_a_code_commit_read_as_docs_only(tmp_path):
    ws = repo(tmp_path)
    base = commit(ws, "README.md", "a\n")
    code = commit(ws, "core/x.py", "x = 1\n")
    sh(ws, "checkout", "-q", "-b", "fake", base)
    docs_only = commit(ws, "docs/y.md", "y\n")
    sh(ws, "checkout", "-q", "main")
    sh(ws, "replace", code, docs_only)
    plain = sh(ws, "diff", "--name-only", base, code).split()
    assert plain == ["docs/y.md"]  # the turn's own git honours the replace ref
    assert kgit.diff_paths(ws, base, code) == ["core/x.py"]


def test_a_graft_cannot_change_ancestry(tmp_path):
    ws = repo(tmp_path)
    base = commit(ws, "README.md", "a\n")
    sh(ws, "checkout", "-q", "-b", "side")
    side = commit(ws, "side.md", "s\n")
    sh(ws, "checkout", "-q", "main")
    tip = commit(ws, "core/x.py", "x = 1\n")
    (ws / ".git" / "info").mkdir(exist_ok=True)
    (ws / ".git" / "info" / "grafts").write_text(f"{tip} {side}\n")
    grafted = subprocess.run(
        ["git", "merge-base", "--is-ancestor", side, tip], cwd=ws, capture_output=True, check=False
    ).returncode
    assert grafted == 0  # plain git follows the graft
    assert kgit.is_ancestor(ws, side, tip) is False
    assert kgit.is_ancestor(ws, base, tip) is True
