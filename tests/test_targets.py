"""The merge-target list (`core/targets.py`) and `merge_url` honoured at
`start`: Tom grants a (URL, branch) pair, a merge to a remote lands only on
a granted pair, and never on the branch the remote's HEAD names.

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

from core import db, ledger, targets, tasks
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


async def grant(dsn, url, branch):
    async with await db.connect(dsn) as conn:
        await targets.grant(conn, url, branch, "test")


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


# -- the command line ------------------------------------------------------------------------


def test_merge_target_add_is_tom_s_and_remove_is_anyone_s(dsn, tmp_path):
    url, branch = f"https://github.com/example/{name()}", "valor/rebuild"
    for bad in (
        "http://github.com/a/b",
        "https://github.com/a/../b",
        'https://github.com/a"/b',
        "https://u@h.com/a",
    ):
        out = cli(tmp_path, "merge-target", "add", bad, branch, "--note", "x")
        assert out.returncode == 1 and "is not https://host/path" in out.stderr
    malformed = cli(tmp_path, "merge-target", "add", url, "bad..branch", "--note", "x")
    assert malformed.returncode == 1 and "is not a branch name" in malformed.stderr
    dashed = cli(tmp_path, "merge-target", "add", url, "-x", "--note", "x")
    assert dashed.returncode != 0
    played = cli(tmp_path, "merge-target", "add", url, branch, "--note", "x", "--role-played")
    assert played.returncode == 2
    by = cli(tmp_path, "merge-target", "add", url, branch, "--note", "x", "--by", "valor")
    assert by.returncode == 2
    added = cli(tmp_path, "merge-target", "add", url, branch, "--note", "the rebuild")
    assert added.returncode == 0, added.stderr
    assert f"{url}  {branch}  the rebuild" in cli(tmp_path, "merge-target", "list").stdout
    nameless = cli(tmp_path, "merge-target", "remove", url, branch, "--note", "done")
    assert nameless.returncode == 2 and "--by" in nameless.stderr
    assert url in cli(tmp_path, "merge-target", "list").stdout
    removed = cli(tmp_path, "merge-target", "remove", url, branch, "--note", "done", "--by", "valor")
    assert removed.returncode == 0, removed.stderr
    assert url not in cli(tmp_path, "merge-target", "list").stdout

    async def rows():
        async with await db.connect(dsn) as conn:
            return [r for r in await ledger.read(conn, targets.STREAM) if r["payload"]["url"] == url]

    granted, revoked = run(rows())
    assert granted["type"] == targets.GRANTED and revoked["type"] == targets.REVOKED
    assert granted["payload"]["provenance"]["by"] == "tom"
    assert granted["payload"]["provenance"]["role_played"] is False
    assert revoked["payload"]["provenance"]["by"] == "valor"


def test_check_reads_the_latest_row(dsn):
    url, branch = f"https://github.com/example/{name()}", "rebuild"

    async def go():
        async with await db.connect(dsn) as conn:
            seen = [await targets.check(conn, url, branch)]
            await targets.grant(conn, url, branch, "x")
            seen.append(await targets.check(conn, url, branch))
            seen.append(await targets.check(conn, url, "other"))
            await targets.revoke(conn, url, branch, "x", by="valor")
            seen.append(await targets.check(conn, url, branch))
            await targets.grant(conn, url, branch, "again")
            seen.append(await targets.check(conn, url, branch))
            seen.append(await targets.check(conn, "/a/local/origin.git", "main"))
            seen.append(await targets.check(conn, None, "main"))
            return seen

    ungranted, granted, other, revoked, again, local, nameless = run(go())
    assert f"python -m core merge-target add {url} {branch} --note TEXT" in ungranted
    assert granted is None and "not a granted" in other and "not a granted" in revoked
    assert again is None and local is None and "names its URL" in nameless


# -- start --project ------------------------------------------------------------------------


def test_start_project_refuses_an_ungranted_pair_naming_the_command(dsn, tmp_path):
    url = f"https://github.com/example/{name()}"
    out = cli(tmp_path, "start", "go", "--project", str(spec_file(tmp_path, url)))
    assert out.returncode == 1 and "merge-target add" in out.stderr
    assert task_dirs(tmp_path) == []


def test_start_project_with_a_granted_pair_merges_to_the_merge_url(dsn, tmp_path):
    with Server(tmp_path / "remote") as server:
        repo = name()
        remote(server, tmp_path, repo)
        url = server.url(repo)
        run(grant(dsn, url, "rebuild"))
        spec = spec_file(tmp_path, url)
        started = cli(tmp_path, "start", "go", "--project", str(spec))
        assert started.returncode == 0, started.stderr
        shown = json.loads(cli(tmp_path, "workspace", "show", started.stdout.strip()).stdout)
        # Editing the spec changes nothing for the task, and a later task
        # from the edited spec needs its own grant.
        spec_file(tmp_path, url.replace(repo, name()))
        again = cli(tmp_path, "start", "go", "--project", str(spec))
    assert shown["origin_url"] == url and shown["target_branch"] == "rebuild"
    assert shown["push_url"] != url and shown["project"]["merge_url"] == url
    assert again.returncode == 1 and "merge-target add" in again.stderr

    async def brief():
        async with await db.connect(dsn) as conn:
            return await tasks.brief(conn, started.stdout.strip())

    assert run(brief()).origin_url == url


def test_start_project_refuses_the_remote_s_default_branch_and_removes_the_workspace(dsn, tmp_path):
    with Server(tmp_path / "remote") as server:
        repo = name()
        remote(server, tmp_path, repo, head="rebuild")
        url = server.url(repo)
        run(grant(dsn, url, "rebuild"))
        out = cli(tmp_path, "start", "go", "--project", str(spec_file(tmp_path, url)))
    assert out.returncode == 1 and f"rebuild is the default branch of {url}" in out.stderr
    assert task_dirs(tmp_path) == []


def test_start_project_refuses_an_unreadable_remote_and_an_unborn_head(dsn, tmp_path):
    with Server(tmp_path / "remote") as server:
        missing = server.url(name())
        run(grant(dsn, missing, "rebuild"))
        unreadable = cli(tmp_path, "start", "go", "--project", str(spec_file(tmp_path, missing)))
        unborn_repo = name()
        server.bare(unborn_repo, head="nothing")
        unborn = server.url(unborn_repo)
        run(grant(dsn, unborn, "rebuild"))
        empty = cli(tmp_path, "start", "go", "--project", str(spec_file(tmp_path, unborn)))
    assert unreadable.returncode == 1 and "cannot read the remote's HEAD" in unreadable.stderr
    assert empty.returncode == 1 and "the remote's HEAD names no branch" in empty.stderr
    assert task_dirs(tmp_path) == []


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


def test_provisioning_from_a_spec_with_a_branch_targets_it_and_the_default_stays_refused(tmp_path):
    """The shape of a message-started task: `workspace.provision` from the
    spec alone, no `--branch`, against a remote whose default is `main`."""
    with Server(tmp_path / "remote") as server:
        repo = name()
        url = server.url(repo)
        remote(server, tmp_path, repo)
        named = kws.Spec.load(str(spec_file(tmp_path, url)))
        made = kws.provision("a" * 32, named, {}, source=None)
        with pytest.raises(kws.Refused, match=f"main is the default branch of {url}"):
            kws.provision("b" * 32, dataclasses.replace(named, branch=None), {}, source=None)
    assert made.target_branch == "rebuild"
    assert (
        subprocess.run(
            ["git", "-C", made.push_url, "symbolic-ref", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        == "refs/heads/rebuild"
    )


def test_the_valor_spec_names_the_rebuild_branch():
    spec = kws.Spec.load(str(ROOT / "projects" / "valor.toml"))
    assert spec.branch == "valor-cori-rebuild" and spec.target_branch is None
