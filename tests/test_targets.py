"""A merge URL's shape (`core/targets.py`) and `merge_url` honoured at
`start`: the merge lands on the spec's URL and the target branch the kernel
records in the Brief at start, whatever branch the remote's HEAD names.

Live spend: none.
"""

import asyncio
import dataclasses
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

from core import db, targets, tasks
from core import workspace as kws
from tests import scripted
from tests.conftest import TEST_DB
from tests.smart_http import Server

pytestmark = [pytest.mark.spend(usd=0)]

ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


def cli(tmp_path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "core", *args], cwd=ROOT, capture_output=True, text=True, check=False,
        env={**os.environ, "VALOR_DB": TEST_DB, "VALOR_WORK": str(tmp_path / "work")},
    )  # fmt: skip


def sh(cwd, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def name() -> str:
    return f"r{secrets.token_hex(4)}.git"


def spec_file(tmp_path: Path, merge_url: str, branch: str = "rebuild") -> Path:
    src = tmp_path / "src" / "toy"
    if not src.exists():
        src = scripted.toy_repo(tmp_path)
        sh(src, "branch", "rebuild")
    path = tmp_path / "toy.toml"
    path.write_text(
        f'name = "toy"\nrepo = "{src}"\nkind = "plain"\nsuite = "true"\n'
        f"merge_url = {json.dumps(merge_url)}\nbranch = {json.dumps(branch)}\n"
    )
    return path


def remote(server: Server, tmp_path: Path, repo: str, head: str = "main") -> Path:
    spec_file(tmp_path, "x")  # makes the toy source
    bare = server.bare(repo, head=head)
    subprocess.run(
        ["git", "-C", str(tmp_path / "src" / "toy"), "push", "-q", str(bare), "main", "rebuild"], check=True
    )
    return bare


def task_dirs(tmp_path: Path) -> list[str]:
    work = tmp_path / "work"
    return [d.name for d in work.iterdir() if d.name not in ("cache", "bin")] if work.exists() else []


# -- the URL's shape -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/tomcounsell/ai.git",
        "https://github.com/a/b",
        "https://git.example.com/a/b_c-d.e/f",
    ],
)
def test_a_plain_https_url_is_accepted(url):
    assert targets.url_ok(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a/b.git",
        "ssh://git@github.com/a/b.git",
        "git@github.com:a/b.git",
        "https://user@github.com/a/b.git",
        "https://x-access-token:t@github.com/a/b.git",
        "https://github.com:443/a/b.git",
        "https://github.com/a/b.git?x=1",
        "https://github.com/a/b.git#frag",
        'https://github.com/a/b".git',
        "https://github.com/a\\b.git",
        "https://github.com/a]/b.git",
        "https://github.com/a/b.git\n[core]",
        "https://github.com/a b.git",
        "https://github.com/a/../b.git",
        "https://github.com/./b.git",
        "https://github.com/",
        "https://github.com",
        "https://gith[ub.com/a/b",
        "/local/path",
        "http://127.0.0.1:6481/ai.git",
    ],
)
def test_a_url_that_could_reach_config_is_refused(url):
    assert not targets.url_ok(url)


def test_loopback_is_for_tests_only():
    assert targets.url_ok("http://127.0.0.1:6481/ai.git", loopback=True)
    assert not targets.url_ok("http://127.0.0.1/ai.git", loopback=True)
    assert not targets.url_ok("http://localhost:6481/ai.git", loopback=True)


def test_a_local_path_is_local_and_a_remote_is_not():
    assert targets.local("/tmp/origin.git") and targets.local("../origin.git")
    for url in ("https://github.com/a/b", "git@github.com:a/b", "file:///tmp/x"):
        assert not targets.local(url)


# -- start --project ------------------------------------------------------------------------


def test_start_project_records_the_merge_url_and_branch_in_the_brief(dsn, tmp_path):
    with Server(tmp_path / "remote") as server:
        repo = name()
        remote(server, tmp_path, repo)
        url = server.url(repo)
        spec = spec_file(tmp_path, url)
        started = cli(tmp_path, "start", "go", "--project", str(spec))
        assert started.returncode == 0, started.stderr
        shown = json.loads(cli(tmp_path, "workspace", "show", started.stdout.strip()).stdout)
        # Editing the spec changes nothing for the running task.
        spec_file(tmp_path, url.replace(repo, name()))
    assert shown["origin_url"] == url and shown["target_branch"] == "rebuild"
    assert shown["push_url"] != url and shown["project"]["merge_url"] == url

    async def brief():
        async with await db.connect(dsn) as conn:
            return await tasks.brief(conn, started.stdout.strip())

    made = run(brief())
    assert made.origin_url == url and made.target_branch == "rebuild"


def test_start_project_may_target_the_remote_s_default_branch(dsn, tmp_path):
    with Server(tmp_path / "remote") as server:
        repo = name()
        remote(server, tmp_path, repo, head="rebuild")
        url = server.url(repo)
        out = cli(tmp_path, "start", "go", "--project", str(spec_file(tmp_path, url)))
    assert out.returncode == 0, out.stderr
    assert task_dirs(tmp_path) == [out.stdout.strip()]


# -- start --workspace -----------------------------------------------------------------------


def test_start_workspace_with_an_unreachable_origin_is_refused(tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    with Server(tmp_path / "remote") as server:
        url = server.url(name())
        sh(ws, "remote", "set-url", "origin", url)
        with pytest.raises(tasks.WorkspaceRefused, match=r"cannot read origin's HEAD"):
            tasks.resolve_workspace(str(ws))
    assert tasks.resolve_workspace(str(ws), "main")["origin_url"] == url  # a given branch reads nothing


# -- a message start has no --branch ---------------------------------------------------------


def test_provisioning_from_a_spec_targets_its_branch_or_the_source_s_default(tmp_path):
    """The shape of a message-started task: `workspace.provision` from the
    spec alone, no `--branch`, against a remote whose default is `main`."""
    with Server(tmp_path / "remote") as server:
        repo = name()
        url = server.url(repo)
        remote(server, tmp_path, repo)
        named = kws.Spec.load(str(spec_file(tmp_path, url)))
        made = kws.provision("a" * 32, named, {}, source=None)
        default = kws.provision("b" * 32, dataclasses.replace(named, branch=None), {}, source=None)
    assert made.target_branch == "rebuild" and default.target_branch == "main"
    assert made.origin_url == url and default.origin_url == url
    assert (
        subprocess.run(
            ["git", "-C", made.push_url, "symbolic-ref", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        == "refs/heads/rebuild"
    )


def test_the_valor_spec_names_main():
    spec = kws.Spec.load(str(ROOT / "projects" / "valor.toml"))
    assert spec.branch == "main" and spec.target_branch is None
