"""The merge's GitHub credential (`core/credentials.py`, `tools/push_branch.py`)
against a loopback smart-HTTP server (`tests/smart_http.py`) that answers a
push without the expected header with 401 and logs every header.

The token is made up per test; no real key is read.

Live spend: none.
"""

import asyncio
import base64
import contextlib
import dataclasses
import hashlib
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core import broker, credentials, db, git, ledger, targets, tasks
from core import workspace as kws
from core.settings import settings
from tests import scripted
from tests.smart_http import Server
from tools import push_branch
from tools.push_branch import ROTATE, Merge, PushBranch

pytestmark = [pytest.mark.spend(usd=0)]

ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


def sh(cwd, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def token() -> str:
    return "ghp_test" + secrets.token_hex(16)


@pytest.fixture
def keyfile(tmp_path, token) -> Path:
    keys = tmp_path / "keys"
    keys.mkdir(mode=0o700)
    path = keys / "github-keys"
    path.write_text(f"{credentials.GITHUB_KEY}={token}\n")
    path.chmod(0o600)
    return path


def forms(token: str) -> list[str]:
    """The token, its header form, and its digest: none may leak."""
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return [token, basic, hashlib.sha256(token.encode()).hexdigest()]


def remote(server: Server, src: Path, name: str = "ai.git", head: str = "main") -> Path:
    bare = server.bare(name, head=head)
    subprocess.run(["git", "-C", str(src), "push", "-q", str(bare), "main", "rebuild"], check=True)
    return bare


def toy(tmp_path: Path) -> Path:
    src = tmp_path / "src" / "toy"
    if not src.exists():
        src = scripted.toy_repo(tmp_path)
        sh(src, "branch", "rebuild")
    return src


def provision(tmp_path: Path, merge_url: str, branch: str = "rebuild", work: str = "work") -> kws.Provisioned:
    spec = kws.Spec.from_dict({"name": "toy", "repo": str(toy(tmp_path)), "kind": "plain", "suite": "true",
                               "merge_url": merge_url, "branch": branch})  # fmt: skip
    return kws.provision(ledger.new_id(), spec, {}, work=tmp_path / work)


def candidate(repo: Path | str, parent: str, text: str = "candidate") -> str:
    """A commit in a kernel-owned repository whose parent is `parent`."""
    return sh(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit-tree", f"{parent}^{{tree}}",
              "-p", parent, "-m", text)  # fmt: skip


def merge_action(url: str, branch: str, sha: str) -> broker.Action:
    return broker.Action("merge", branch, {"url": url, "target_branch": branch, "head_sha": sha})


def leftovers(keyfile: Path) -> list[str]:
    return sorted(p.name for p in keyfile.parent.glob("github-push-*.gitconfig"))


# -- the header goes with the merge, from the mirror, and with nothing else ------------------


def test_a_merge_lands_from_the_mirror_with_the_header_and_push_branch_sends_none(tmp_path, keyfile, token):
    with Server(tmp_path / "remote", token=token) as server:
        bare = remote(server, toy(tmp_path))
        url = server.url("ai.git")
        made = provision(tmp_path, url)
        assert made.origin_url == url and made.push_url != url
        sha = candidate(made.mirror, made.base_sha)
        merge = Merge(made.mirror, url=url, branch="rebuild", credential=keyfile, loopback=True)
        before = len(server.log)
        landed = run(merge.perform(merge_action(url, "rebuild", sha), "k"))
        held = run(merge.lookup(merge_action(url, "rebuild", sha), "k"))
        merge_requests = server.log[before:]

        # push_branch in the same task goes to the task's own bare origin.
        scripted.commit(made.workspace, "a.txt", "a\n", "work")
        head = sh(made.workspace, "rev-parse", "HEAD")
        pushed = run(PushBranch(made.workspace, url=made.push_url).perform(
            broker.Action("push_branch", "valor/w", {"head_sha": head}), "k"))  # fmt: skip
    assert landed == {"remote": url, "branch": "rebuild", "sha": sha}
    assert sh(bare, "rev-parse", "rebuild") == sha and held is not None
    assert pushed["remote"] == made.push_url and sh(made.push_url, "rev-parse", "valor/w") == head
    assert any("git-receive-pack" in r["path"] for r in merge_requests)
    assert all(r["headers"].get("authorization") == server.expected for r in merge_requests)
    assert len(server.log) == len(merge_requests) + before  # push_branch never reached the server
    assert leftovers(keyfile) == []


def test_without_the_key_the_merge_fails_naming_the_command_and_sends_nothing(tmp_path, keyfile, token):
    keyfile.unlink()
    with Server(tmp_path / "remote", token=token) as server:
        remote(server, toy(tmp_path))
        url = server.url("ai.git")
        made = provision(tmp_path, url)
        before = len(server.log)
        merge = Merge(made.mirror, url=url, branch="rebuild", credential=keyfile, loopback=True)
        with pytest.raises(credentials.MissingKey, match="python -m core github-key"):
            run(merge.perform(merge_action(url, "rebuild", made.base_sha), "k"))
        assert server.log[before:] == [] and server.authorized() == []
        with pytest.raises(broker.Unknown):
            run(merge.lookup(merge_action(url, "rebuild", made.base_sha), "k"))


def test_a_refused_token_fails_the_merge_saying_rotate_it(tmp_path, keyfile, token):
    keyfile.write_text(f"{credentials.GITHUB_KEY}=ghp_wrong{secrets.token_hex(8)}\n")
    with Server(tmp_path / "remote", token=token) as server:
        bare = remote(server, toy(tmp_path))
        url = server.url("ai.git")
        made = provision(tmp_path, url)
        sha = candidate(made.mirror, made.base_sha)
        merge = Merge(made.mirror, url=url, branch="rebuild", credential=keyfile, loopback=True)
        with pytest.raises(git.GitError, match=ROTATE) as exc:
            run(merge.perform(merge_action(url, "rebuild", sha), "k"))
    assert sh(bare, "rev-parse", "rebuild") != sha
    assert not any(f in str(exc.value) for f in forms(token))
    assert leftovers(keyfile) == []


def test_a_workspace_task_merges_without_the_header(dsn, tmp_path, keyfile, token):
    """A `--workspace` task has no mirror: its merge gets no credential, so
    a granted remote answers it with 401, and its push_branch sends none."""
    with Server(tmp_path / "remote", token=token) as server:
        src = toy(tmp_path)
        bare = remote(server, src)
        url = server.url("ai.git")
        ws = tmp_path / "ws"
        subprocess.run(["git", "clone", "-q", "-b", "rebuild", str(src), str(ws)], check=True)
        sh(ws, "remote", "set-url", "origin", url)
        where = tasks.resolve_workspace(str(ws), "rebuild")
        assert where["origin_url"] == url
        b = tasks.Brief(instruction="x", max_effect_class="act", workspace=str(ws), **where)
        from core.__main__ import _performers

        performers = _performers(b)
        merge = performers.get("merge")
        assert merge.credential is None and merge.workspace == ws
        scripted.commit(ws, "a.txt", "a\n", "work")
        head = sh(ws, "rev-parse", "HEAD")
        with pytest.raises(git.GitError) as merged:
            run(merge.perform(merge_action(url, "rebuild", head), "k"))
        with pytest.raises(git.GitError):
            run(
                performers.get("push_branch").perform(
                    broker.Action("push_branch", "valor/w", {"head_sha": head}), "k"
                )
            )
        assert server.authorized() == []
    assert ROTATE not in str(merged.value)
    assert sh(bare, "rev-parse", "rebuild") != head


def test_a_mirror_whose_config_names_http_is_refused_before_any_request(tmp_path, keyfile, token):
    with Server(tmp_path / "remote", token=token) as server:
        remote(server, toy(tmp_path))
        url = server.url("ai.git")
        made = provision(tmp_path, url)
        before = len(server.log)
        for key, value in (("http.extraHeader", "X-Planted: 1"), ("url.http://elsewhere/.insteadOf", url)):
            sh(made.mirror, "config", key, value)
            merge = Merge(made.mirror, url=url, branch="rebuild", credential=keyfile, loopback=True)
            with pytest.raises(ValueError, match="could redirect the push"):
                run(merge.perform(merge_action(url, "rebuild", made.base_sha), "k"))
            sh(made.mirror, "config", "--unset", key)
        assert server.log[before:] == []
    assert leftovers(keyfile) == []


def test_a_redirect_never_carries_the_header_to_the_second_server(tmp_path, keyfile, token):
    """Left to itself git follows the first redirect and sends the header
    there; the header file and the command line pin
    `http.followRedirects=false`."""
    with Server(tmp_path / "second", token=token) as second:
        remote(second, toy(tmp_path))
        with Server(tmp_path / "first", token=token, redirect_to=f"http://127.0.0.1:{second.port}") as first:
            url = first.url("ai.git")
            made = provision(tmp_path, url)
            sha = candidate(made.mirror, made.base_sha)
            merge = Merge(made.mirror, url=url, branch="rebuild", credential=keyfile, loopback=True)
            with contextlib.suppress(git.GitError, ValueError):
                run(merge.perform(merge_action(url, "rebuild", sha), "k"))
        assert first.authorized()  # the header went to the granted URL
        assert second.authorized() == []


# The keys a mirror's own config could set to steer the credential. `run`
# refuses every one before git starts; with that refusal switched off, the
# command line pins and the URL-scoped header still keep the token on the
# granted URL.
STEERING = {
    "redirect": lambda url, second: ("http.followRedirects", "true"),
    "redirect for the url": lambda url, second: (f"http.{url}.followRedirects", "true"),
    "proxy": lambda url, second: ("http.proxy", f"http://127.0.0.1:{second.port}"),
    "proxy for the url": lambda url, second: (f"http.{url}.proxy", f"http://127.0.0.1:{second.port}"),
    "rewrite": lambda url, second: (f"url.{second.url('ai.git')}.insteadOf", url),
    "push url": lambda url, second: ("remote.origin.pushurl", second.url("ai.git")),
    "second header": lambda url, second: ("http.extraHeader", "X-Planted: 1"),
    "helper": lambda url, second: ("credential.helper", "!f() { echo username=u; echo password=p; }; f"),
}


@pytest.mark.parametrize("refusal", ["on", "off"])
@pytest.mark.parametrize("case", sorted(STEERING))
def test_a_mirror_config_never_steers_the_header_to_another_server(case, refusal, tmp_path, keyfile, token,
                                                                   monkeypatch):  # fmt: skip
    if refusal == "off":
        monkeypatch.setattr(git, "hostile", lambda workspace: [])
    with Server(tmp_path / "second", token=token) as second:
        remote(second, toy(tmp_path))
        redirect = f"http://127.0.0.1:{second.port}" if case.startswith("redirect") else None
        with Server(tmp_path / "first", token=token, redirect_to=redirect) as first:
            if redirect is None:
                remote(first, toy(tmp_path))
            url = first.url("ai.git")
            made = provision(tmp_path, url)
            sha = candidate(made.mirror, made.base_sha)
            sh(made.mirror, "config", *STEERING[case](url, second))
            before = (len(first.log), len(second.log))
            merge = Merge(made.mirror, url=url, branch="rebuild", credential=keyfile, loopback=True)
            with contextlib.suppress(git.GitError, ValueError):
                run(merge.perform(merge_action(url, "rebuild", sha), "k"))
        if refusal == "on":
            assert (len(first.log), len(second.log)) == before
        assert second.authorized() == []
    assert leftovers(keyfile) == []


def test_every_call_carrying_the_credential_pins_redirects_and_proxy_at_both_scopes():
    url = "https://github.com/tomcounsell/ai.git"
    pins = git.credential_pins(url)
    for key in ("followRedirects=false", "proxy="):
        assert f"http.{key}" in pins and f"http.{url}.{key}" in pins
    with pytest.raises(ValueError, match="names its URL"):
        git.run(".", "status", credential=Path("/nonexistent"))


# -- no leak ---------------------------------------------------------------------------------


def test_the_token_is_in_no_process_no_row_and_no_file_a_turn_can_open(
    dsn, tmp_path, keyfile, token, monkeypatch
):
    from tests.test_workspace import PYTHON, probe

    # The turn profile denies the key file's directory, as it denies the real one.
    monkeypatch.setattr(
        kws, "settings", dataclasses.replace(settings, pg_passfile=str(keyfile.parent / "pgpass"))
    )
    with Server(tmp_path / "remote", token=token) as server:
        bare = remote(server, toy(tmp_path))
        url = server.url("ai.git")
        made = provision(tmp_path, url)
        profile = made.harness["sandbox_profile"]
        sha = candidate(made.mirror, made.base_sha)
        merge = Merge(made.mirror, url=url, branch="rebuild", credential=keyfile, loopback=True)
        server.hold_push = True

        async def go():
            pushing = asyncio.create_task(merge.perform(merge_action(url, "rebuild", sha), "k"))
            while not server.holding.is_set():
                await asyncio.sleep(0.05)
            header = leftovers(keyfile)
            kernel_ps = (await asyncio.to_thread(subprocess.run, ["/bin/ps", "-E", "-ww", "-ax", "-o", "command"],
                                                 capture_output=True, text=True, check=False)).stdout  # fmt: skip
            turn_ps = await asyncio.to_thread(
                subprocess.run,
                ["/usr/bin/sandbox-exec", "-D", "GATEWAY_PORT=1", "-D", "VALOR_TURN=probe", "-f", profile,
                 "/bin/sh", "-c", "/bin/ps -E -ww -ax -o command; /usr/bin/pgrep -lf git"],
                capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}, check=False,
            )  # fmt: skip
            opened = probe(profile, f"read:{keyfile}", *(f"read:{keyfile.parent / h}" for h in header),
                           f"list:{keyfile.parent}")  # fmt: skip
            contents = (keyfile.parent / header[0]).read_text() if header else ""
            server.release.set()
            await pushing
            return header, kernel_ps, turn_ps.stdout + turn_ps.stderr, opened, contents

        header, kernel_ps, turn_ps, opened, contents = run(go())
    assert PYTHON and len(header) == 1 and "extraHeader" in contents
    assert "GIT_CONFIG_GLOBAL=" in kernel_ps  # the probe saw the push's git
    for text in (kernel_ps, turn_ps):
        assert not any(f in text for f in forms(token)) and contents not in text
    assert opened == ["denied"] * len(opened)
    assert sh(bare, "rev-parse", "rebuild") == sha and leftovers(keyfile) == []

    async def rows():
        async with await db.connect(dsn) as conn:
            events = await (await conn.execute("SELECT payload::text FROM events")).fetchall()
            docs = await (await conn.execute("SELECT kind, body FROM documents")).fetchall()
        return [e[0] for e in events], docs

    events, docs = run(rows())
    texts = events + [str(body) for _, body in docs]
    texts += [
        base64.b64decode(body["base64"]).decode("latin-1") for kind, body in docs if kind == "transcript"
    ]
    assert not any(f in t for t in texts for f in forms(token))


def test_leftover_header_files_older_than_twice_the_git_timeout_are_removed(tmp_path, keyfile):
    old = keyfile.parent / "github-push-old.gitconfig"
    young = keyfile.parent / "github-push-young.gitconfig"
    for p in (old, young):
        p.write_text("x")
    stale = time.time() - 2 * settings.git_timeout_s - 5
    os.utime(old, (stale, stale))
    with credentials.header_file(keyfile, "https://github.com/tomcounsell/ai.git") as path:
        assert path.exists() and oct(path.stat().st_mode & 0o777) == "0o600"
        assert path.read_text().startswith(
            '[http]\n\tfollowRedirects = false\n[http "https://github.com/tomcounsell/ai.git"]\n\textraHeader = '
        )
    assert not old.exists() and young.exists() and not path.exists()


def test_the_header_writer_refuses_a_url_it_would_not_write(tmp_path, keyfile):
    for url in (
        "http://github.com/a/b.git",
        'https://github.com/a"]\n[core]x/b',
        "http://127.0.0.1:6481/ai.git",
    ):
        with pytest.raises(credentials.CredentialError), credentials.header_file(keyfile, url):
            pass
    with credentials.header_file(keyfile, "http://127.0.0.1:6481/ai.git", loopback=True):
        pass
    assert leftovers(keyfile) == []


def test_github_key_prints_written_kept_and_missing_and_never_the_value(tmp_path, token):
    vault = tmp_path / "vault.env"
    vault.write_text(f"{credentials.GITHUB_KEY}={token}\n")
    env = {
        **os.environ,
        "VALOR_VAULT_ENV": str(vault),
        "VALOR_PG_PASSFILE": str(tmp_path / "kernel" / "pgpass"),
    }

    def cli():
        return subprocess.run([sys.executable, "-m", "core", "github-key"], cwd=ROOT, env=env,
                              capture_output=True, text=True, check=False)  # fmt: skip

    first, second = cli(), cli()
    vault.write_text("OTHER=1\n")
    third = cli()
    assert f"{credentials.GITHUB_KEY}: written" in first.stdout
    assert f"{credentials.GITHUB_KEY}: kept" in second.stdout
    assert f"{credentials.GITHUB_KEY}: missing" in third.stdout
    for out in (first, second, third):
        assert out.returncode == 0 and not any(f in out.stdout + out.stderr for f in forms(token))
    keyfile = tmp_path / "kernel" / "github-keys"
    assert oct(keyfile.stat().st_mode & 0o777) == "0o600"
    assert credentials.read_key(keyfile, credentials.GITHUB_KEY) == token


# -- performers are awaited ------------------------------------------------------------------


def test_two_merges_run_at_once_each_to_its_own_remote_while_the_loop_ticks(tmp_path, keyfile, token):
    with Server(tmp_path / "remote", token=token) as server:
        src = toy(tmp_path)
        bares = [remote(server, src, "ai.git"), remote(server, src, "other.git")]
        urls = [server.url("ai.git"), server.url("other.git")]
        made = [provision(tmp_path, urls[0], work="a"), provision(tmp_path, urls[1], work="b")]
        shas = [candidate(m.mirror, m.base_sha, f"c{i}") for i, m in enumerate(made)]
        merges = [Merge(m.mirror, url=u, branch="rebuild", credential=keyfile, loopback=True)
                  for m, u in zip(made, urls, strict=True)]  # fmt: skip
        server.hold_push = True

        async def go():
            ticks = 0

            async def timer():
                nonlocal ticks
                while not server.release.is_set():
                    ticks += 1
                    await asyncio.sleep(0.02)

            async def releaser():
                while (
                    len([r for r in server.log if r["method"] == "POST" and "receive-pack" in r["path"]]) < 2
                ):
                    await asyncio.sleep(0.05)
                await asyncio.sleep(0.3)
                server.release.set()

            out = await asyncio.gather(
                *(m.perform(merge_action(u, "rebuild", s), "k") for m, u, s in zip(merges, urls, shas, strict=True)),
                timer(), releaser(),
            )  # fmt: skip
            return out[:2], ticks

        landed, ticks = run(go())
    assert ticks > 10
    assert [x["sha"] for x in landed] == shas
    assert [sh(b, "rev-parse", "rebuild") for b in bares] == shas


def test_the_deadline_applies_inside_the_worker_thread(tmp_path, keyfile, token, monkeypatch):
    monkeypatch.setattr(push_branch, "settings", dataclasses.replace(settings, git_timeout_s=1.5))
    with Server(tmp_path / "remote", token=token) as server:
        remote(server, toy(tmp_path))
        url = server.url("ai.git")
        made = provision(tmp_path, url)
        sha = candidate(made.mirror, made.base_sha)
        merge = Merge(made.mirror, url=url, branch="rebuild", credential=keyfile, loopback=True)
        server.hold_push = True
        started = time.monotonic()
        with pytest.raises(git.GitError, match="did not finish|deadline"):
            run(merge.perform(merge_action(url, "rebuild", sha), "k"))
        elapsed = time.monotonic() - started
    assert elapsed < 10 and leftovers(keyfile) == []


def test_a_release_cancelled_mid_push_is_settled_by_reconcile(dsn, tmp_path):
    with Server(tmp_path / "remote") as server:
        ws, _origin = scripted.workspace(tmp_path)
        bare = server.bare("ws.git")
        sh(ws, "push", "-q", str(bare), "HEAD:refs/heads/main")
        url = server.url("ws.git")
        sh(ws, "remote", "set-url", "origin", url)
        perf = broker.Performers(PushBranch(ws, url=url))
        head = sh(ws, "rev-parse", "HEAD")
        server.hold_push = True

        async def go():
            task = await scripted.start(dsn, ws, judge=None)
            async with await db.connect(dsn) as conn:
                held = await broker.request(
                    conn, perf, task, broker.Action("push_branch", "valor/x", {"head_sha": head})
                )
                await broker.approve(conn, held.effect_id, note="push")

            async def release():
                async with await db.connect(dsn) as conn:
                    return await broker.release(conn, perf, held.effect_id)

            releasing = asyncio.create_task(release())
            while not server.holding.is_set():
                await asyncio.sleep(0.05)
            releasing.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await releasing
            server.release.set()
            for _ in range(200):
                found = await asyncio.to_thread(subprocess.run, ["git", "-C", str(bare), "rev-parse", "-q", "--verify",
                                                "valor/x"], capture_output=True, check=False)  # fmt: skip
                if found.returncode == 0:
                    break
                await asyncio.sleep(0.05)
            async with await db.connect(dsn) as conn:
                settled = await broker.reconcile(conn, perf, held.effect_id, settle_after_s=0)
                return settled, await ledger.read(conn, task)

        settled, written = run(go())
    assert settled is not None and settled.kind == "done"
    assert [r["type"] for r in written].count("effect.intent") == 1
    (outcome,) = [r["payload"] for r in written if r["type"] == "effect.outcome"]
    assert outcome["reconciled"] is True and sh(bare, "rev-parse", "valor/x") == head


# -- the merge-target list at release ----------------------------------------------------------


def test_merge_refuse_binds_the_brief_and_the_list(dsn, tmp_path):
    url, branch = "https://github.com/example/refuse-check.git", f"valor/r-{secrets.token_hex(4)}"
    ws, _ = scripted.workspace(tmp_path)
    merge = Merge(ws, url=url, branch=branch)

    async def go():
        async with await db.connect(dsn) as conn:
            ungranted = await merge.refuse(conn, merge_action(url, branch, "a" * 40))
            await targets.grant(conn, url, branch, "test")
            granted = await merge.refuse(conn, merge_action(url, branch, "a" * 40))
            other_url = await merge.refuse(conn, merge_action(url + "x", branch, "a" * 40))
            other_branch = await merge.refuse(conn, merge_action(url, "main", "a" * 40))
            shapeless = await merge.refuse(conn, broker.Action("merge", branch, {"url": url}))
            await targets.revoke(conn, url, branch, "done", by="valor")
            revoked = await merge.refuse(conn, merge_action(url, branch, "a" * 40))
            return ungranted, granted, other_url, other_branch, shapeless, revoked

    ungranted, granted, other_url, other_branch, shapeless, revoked = run(go())
    assert "merge-target add" in ungranted and granted is None
    assert "the task's Brief names" in other_url and "the task's Brief names" in other_branch
    assert "names its URL" in shapeless and "not a granted merge target" in revoked


def test_a_remote_head_moved_onto_the_target_after_start_fails_the_merge_before_any_push(
    tmp_path, keyfile, token
):
    with Server(tmp_path / "remote", token=token) as server:
        bare = remote(server, toy(tmp_path))
        url = server.url("ai.git")
        made = provision(tmp_path, url)
        sh(bare, "symbolic-ref", "HEAD", "refs/heads/rebuild")
        sha = candidate(made.mirror, made.base_sha)
        merge = Merge(made.mirror, url=url, branch="rebuild", credential=keyfile, loopback=True)
        with pytest.raises(ValueError, match="is the default branch of"):
            run(merge.perform(merge_action(url, "rebuild", sha), "k"))
        assert not [r for r in server.log if "receive-pack" in r["path"]]
    assert sh(bare, "rev-parse", "rebuild") != sha


def test_a_local_origin_needs_no_grant_and_its_head_refuses_nothing(dsn, tmp_path):
    ws, origin = scripted.workspace(tmp_path)  # origin's HEAD names main
    merge = Merge(ws, url=str(origin), branch="main")
    scripted.commit(ws, "a.txt", "a\n", "work")
    head = sh(ws, "rev-parse", "HEAD")

    async def go():
        async with await db.connect(dsn) as conn:
            return await merge.refuse(conn, merge_action(str(origin), "main", head))

    assert run(go()) is None
    run(merge.perform(merge_action(str(origin), "main", head), "k"))
    assert sh(origin, "rev-parse", "main") == head
