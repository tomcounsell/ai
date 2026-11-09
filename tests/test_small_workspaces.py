"""A task workspace holds only the task's own: its packages come from the
Mac's caches, and its clone, bare origin and kernel mirror borrow their git
history from the kernel's cache (the Captain fell to 416 MB free on
2026-11-06 with every task holding its own copy of both)."""

import subprocess
from pathlib import Path

import pytest

from core import workspace
from core.settings import settings
from tests import scripted

pytestmark = pytest.mark.spend(usd=0)

CACHE_VARS = {"UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "npm_config_cache"}


def git(repo: Path, *args: str) -> str:
    return subprocess.run([settings.git_bin, "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()  # fmt: skip


def own_objects(repo: Path) -> int:
    """Objects stored in `repo` itself, loose and packed, not borrowed."""
    found = dict(line.split(": ") for line in git(repo, "count-objects", "-v").splitlines())
    return int(found["count"]) + int(found["in-pack"])


@pytest.fixture
def made(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "_valor_key", lambda: None)
    ws, origin = scripted.workspace(tmp_path)
    for i in range(5):
        scripted.commit(ws, f"f{i}.txt", f"{i}\n", f"history {i}")
    git(ws, "push", "-q", "origin", "HEAD:refs/heads/main")
    work = tmp_path / "work"
    spec = workspace.Spec(name="app", repo=str(origin), kind="plain", suite="true")
    return (
        workspace.provision("a1b2c3d4e5f6", spec, {}, work=work),
        workspace.layout("a1b2c3d4e5f6", work),
        ws,
        origin,
        spec,
        work,
    )


def test_a_workspace_uses_the_macs_package_caches_and_keeps_none_of_its_own(made):
    p, lay, *_ = made
    assert not CACHE_VARS & set(p.harness["env"]) and not (lay.root / "cache").exists()
    for services in (True, False):
        check = workspace.check_harness(lay, lay.checks / "test-x", [], p.harness["env"], services=services)
        assert not CACHE_VARS & set(check["env"])


def test_the_clone_origin_and_mirror_borrow_the_caches_history(made):
    _, lay, _, _, spec, work = made
    cache = workspace.cache_path(spec.repo, work)
    for repo in (lay.repo, lay.origin, lay.mirror):
        git_dir = repo / ".git" if (repo / ".git").exists() else repo
        assert (git_dir / "objects" / "info" / "alternates").read_text().strip() == str(cache / "objects")
        assert own_objects(repo) == 0
        git(repo, "fsck", "--connectivity-only")
    assert own_objects(cache) >= 18  # six commits, their trees and blobs
    # A candidate reaches the mirror with only its own objects, and checks out there.
    sha = scripted.commit(lay.repo, "new.txt", "new\n", "candidate")
    # As `fetch_into_mirror` fetches, less the turn's sandbox (none nests in a turn).
    git(
        lay.mirror,
        "-c",
        "transfer.fsckObjects=true",
        "fetch",
        "-q",
        "--no-tags",
        str(lay.repo),
        f"+{sha}:refs/valor/c",
    )
    assert 0 < own_objects(lay.mirror) < 10  # the candidate's commit, tree and blob, not the history
    git(lay.mirror, "worktree", "add", "-q", "--detach", str(lay.checks / "wt"), sha)
    assert (lay.checks / "wt" / "new.txt").read_text() == "new\n"


def test_an_upstream_force_push_and_gc_in_the_cache_leave_the_workspace_whole(made, tmp_path):
    p, lay, _, origin, spec, work = made
    cache = workspace.cache_path(spec.repo, work)
    other = tmp_path / "other"
    subprocess.run([settings.git_bin, "init", "-q", "-b", "main", str(other)], check=True)
    scripted.commit(other, "README.md", "rewritten\n", "a new root")
    git(other, "push", "-q", "--force", str(origin), "HEAD:refs/heads/main")
    workspace._cache(spec, None, work)  # the kernel's next fetch
    git(cache, "gc", "-q")
    assert git(cache, "config", "gc.pruneExpire") == "never"
    for repo in (lay.repo, lay.origin, lay.mirror):
        git(repo, "fsck", "--connectivity-only")
    assert git(lay.repo, "cat-file", "-t", p.base_sha) == "commit"
    # Removing the workspace leaves the cache whole for the next task.
    workspace.remove("a1b2c3d4e5f6", lay)
    git(cache, "fsck", "--connectivity-only")
    nxt = workspace.provision("b1b2c3d4e5f6", spec, {}, work=work)
    assert (Path(nxt.workspace) / "README.md").read_text() == "rewritten\n"


def test_a_cache_made_before_this_change_is_set_to_keep_every_object(made):
    *_, spec, work = made
    cache = workspace.cache_path(spec.repo, work)
    git(cache, "config", "--unset", "gc.pruneExpire")
    workspace._cache(spec, None, work)
    assert git(cache, "config", "gc.pruneExpire") == "never"
