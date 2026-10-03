"""Kernel workspaces (`core/workspace.py`) on real git, real Postgres and
Redis servers of the task's own, and the real `sandbox-exec`.

Provisioning, the per-task cluster and its roles, the profiles' reach, the
hardened fetch into the kernel mirror, and the services sweep. Each test
provisions from a toy repository under its own `tmp_path`.

Live spend: none.
"""

import asyncio
import hashlib
import json
import os
import struct
import subprocess
import uuid
import zlib
from pathlib import Path

import psycopg
import pytest

from core import db, ledger, runs, tasks
from core import workspace as kws
from core.gateway import Gateway
from tests import scripted

pytestmark = [pytest.mark.spend(usd=0)]

PYTHON = "/Library/Developer/CommandLineTools/usr/bin/python3"
PROBE = r"""
import os, socket, sys
for target in sys.argv[1:]:
    mode, _, arg = target.partition(":")
    try:
        if mode == "read":
            open(arg).read()
        elif mode == "create":
            fd = os.open(arg, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            os.unlink(arg)
        elif mode == "append":
            open(arg, "a").close()
        elif mode == "list":
            os.listdir(arg)
        elif mode == "connect":
            s = socket.socket()
            s.settimeout(1)
            try:
                s.connect(("127.0.0.1", int(arg)))
            finally:
                s.close()
        print("open")
    except PermissionError:
        print("denied")
    except OSError:
        print("open")
"""


def run(coro):
    return asyncio.run(coro)


def probe(profile: str | Path, *targets, mark: str = "probe") -> list[str]:
    done = subprocess.run(
        ["/usr/bin/sandbox-exec", "-D", "GATEWAY_PORT=1", "-D", f"VALOR_TURN={mark}", "-f", str(profile),
         PYTHON, "-I", "-S", "-c", PROBE, *targets],
        capture_output=True, text=True, check=True, env={"PATH": "/usr/bin:/bin", "TMPDIR": "/nonexistent"},
    )  # fmt: skip
    return done.stdout.split()


def spec(src: Path | str, **kw) -> kws.Spec:
    return kws.Spec.from_dict({"name": "toy", "repo": str(src), "kind": "plain", "suite": "true", **kw})


def provision(tmp_path: Path, services=(), task_id=None, base=None, **kw) -> tuple[str, kws.Provisioned]:
    src = tmp_path / "src" / "toy"
    if not src.exists():
        src = scripted.toy_repo(tmp_path)
    task_id = task_id or ledger.new_id()
    ports = {}
    if "postgres" in services:
        ports["postgres"] = kws.choose_port((5540, 5579), set())
    if "redis" in services:
        ports["redis"] = kws.choose_port((6440, 6459), set())
    made = kws.provision(
        task_id, spec(src, services=list(services), **kw), ports, base=base, work=tmp_path / "work"
    )
    return task_id, made


def git(cwd, *args, check=True) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=check
    ).stdout.strip()


# -- provisioning --------------------------------------------------------------------------


def test_a_provisioned_clone_holds_nothing_after_the_base_and_pushes_only_to_its_own_origin(tmp_path):
    _task, made = provision(tmp_path)
    repo, origin = Path(made.workspace), Path(made.push_url)
    assert git(repo, "rev-list", "--all", "--not", made.base_sha) == ""
    assert git(repo, "remote") == "origin" and git(repo, "remote", "get-url", "origin") == str(origin)
    assert git(origin, "rev-parse", made.target_branch) == made.base_sha
    assert ".valor/" in (repo / ".git" / "info" / "exclude").read_text()
    assert git(made.mirror, "rev-parse", "refs/valor/base") == made.base_sha
    scripted.commit(repo, "a.txt", "a\n")
    git(repo, "push", "-q", "origin", f"HEAD:refs/heads/{made.target_branch}")
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    scripted.commit(repo, "b.txt", "b\n")
    refused = subprocess.run(
        ["git", "-C", str(repo), "push", "-q", "--force", "origin", f"HEAD:refs/heads/{made.target_branch}"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert refused.returncode != 0  # non-fast-forward refused
    assert made.origin_url == made.push_url  # without merge_url the merge lands on the task's own origin
    for key in ("sandbox_profile", "gitconfig", "gh_config_dir", "tmpdir", "claude_config_dir"):
        assert Path(made.harness[key]).exists()


def test_a_bad_base_leaves_no_directory(tmp_path):
    with pytest.raises(kws.Refused, match="is not a commit"):
        provision(tmp_path, services=["postgres"], task_id="abcdef000001", branch="main", base="f" * 40)
    with pytest.raises(kws.Refused):
        kws.provision("abcdef000002", spec(tmp_path / "nowhere"), {}, work=tmp_path / "work")
    assert not (tmp_path / "work" / "abcdef000001").exists()
    assert not (tmp_path / "work" / "abcdef000002").exists()


def test_a_failing_setup_is_recorded_and_the_workspace_kept(tmp_path):
    _task, made = provision(tmp_path, setup=["echo made > made.txt", "exit 3"])
    result = made.project["setup_result"]
    assert result["ok"] is False and result["commands"][-1]["exit"] == 3
    assert (Path(made.workspace) / "made.txt").read_text() == "made\n"


def test_spec_refusals(tmp_path):
    for bad, why in [
        ({"kind": "rust"}, "kind"),
        ({"services": ["mysql"]}, "unknown services"),
        ({"roles": ["postgres"], "services": ["postgres"]}, "not allowed"),
        ({"roles": ["valor_kernel"]}, "need the postgres service"),
        ({"suite": ""}, "suite"),
        ({"surprise": 1}, "unknown project spec keys"),
    ]:
        with pytest.raises(kws.Refused, match=why):
            kws.Spec.from_dict({"name": "x", "repo": "r", "kind": "plain", "suite": "true", **bad})
    with pytest.raises(kws.Refused, match="no project spec"):
        kws.Spec.load(str(tmp_path / "missing.toml"))
    assert kws.Spec.load("valor").suite.startswith("uv run pytest")


# -- the task's Postgres --------------------------------------------------------------------


def test_the_task_cluster_takes_passwords_only_and_the_app_role_cannot_escape(tmp_path):
    task, made = provision(tmp_path, services=["postgres"], roles=["valor_kernel"])
    lay = kws.Layout(Path(made.mirror).parent)
    port = made.project["ports"]["postgres"]
    assert not any("init.pw" in p.name for p in lay.pg.iterdir())
    passwords = {
        line.split(":")[3]: line.split(":")[4] for line in (lay.home / "pgpass").read_text().splitlines()
    }
    kws.start_services(task, lay, ["postgres"], {"postgres": port})
    try:
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(host="127.0.0.1", port=port, dbname="postgres", user="postgres", password="")
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(host="127.0.0.1", port=port, dbname="app", user="app", password="wrong")
        with psycopg.connect(host="127.0.0.1", port=port, dbname="app", user="app", password=passwords["app"],
                             autocommit=True) as conn:  # fmt: skip
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("COPY (SELECT 1) TO PROGRAM 'touch /tmp/valor-escape'")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("CREATE ROLE boss SUPERUSER")
            conn.execute("CREATE DATABASE app_test")
            conn.execute("DROP DATABASE app_test")
            conn.execute("CREATE ROLE helper LOGIN")
            conn.execute("DROP ROLE helper")
        with psycopg.connect(host="127.0.0.1", port=port, dbname="app", user="valor_kernel",
                             password=passwords["valor_kernel"]) as conn:  # fmt: skip
            assert conn.execute("SELECT current_user").fetchone()[0] == "valor_kernel"
        # The servers run under the task's service sandbox, found by its mark.
        postmaster = int((lay.pg / "data" / "postmaster.pid").read_text().split()[0])
        assert runs._sandbox_check()(postmaster, f"valor.service.{task}")
    finally:
        stopped = kws.stop_services(task, lay)
    assert not (lay.pg / "data" / "postmaster.pid").exists()
    assert stopped and all(
        p["signal"] == "stopped" for p in stopped
    )  # Postgres stopped cleanly; nothing reaped
    assert postmaster in {p["pid"] for p in stopped}


def test_two_tasks_get_their_own_ports_and_neither_turn_reaches_the_other(tmp_path):
    _a, made_a = provision(tmp_path, services=["postgres"])
    taken = {made_a.project["ports"]["postgres"]}
    src = tmp_path / "src" / "toy"
    port_b = kws.choose_port((5540, 5579), taken)
    b = ledger.new_id()
    made_b = kws.provision(b, spec(src, services=["postgres"]), {"postgres": port_b}, work=tmp_path / "work")
    assert port_b != made_a.project["ports"]["postgres"]
    lay_a, lay_b = kws.Layout(Path(made_a.mirror).parent), kws.Layout(Path(made_b.mirror).parent)
    assert (lay_a.home / "pgpass").read_text() != (lay_b.home / "pgpass").read_text()
    profile = Path(made_a.harness["sandbox_profile"])
    got = probe(
        profile,
        f"connect:{port_b}",
        f"read:{lay_b.home / 'pgpass'}",
        f"list:{lay_b.root}",
        f"read:{lay_a.home / 'pgpass'}",
        f"connect:{made_a.project['ports']['postgres']}",
        f"list:{lay_a.mirror}",
    )
    assert got == ["denied", "denied", "denied", "open", "open", "denied"]


def test_redis_runs_sandboxed_and_is_gone_after_the_services_stop(tmp_path):
    task, made = provision(tmp_path, services=["redis"])
    lay = kws.Layout(Path(made.mirror).parent)
    port = made.project["ports"]["redis"]
    kws.start_services(task, lay, ["redis"], {"redis": port})
    pid = int((lay.redis / "redis.pid").read_text())
    assert runs._sandbox_check()(pid, f"valor.service.{task}")
    kws.remove(task, lay)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not lay.root.exists()


# -- the profiles' reach --------------------------------------------------------------------


def test_a_fresh_session_reads_nothing_the_builder_wrote(tmp_path):
    _task, made = provision(tmp_path)
    lay = kws.Layout(Path(made.mirror).parent)
    home = tmp_path / "home"
    for d in ("src", ".claude/todos", ".claude/projects/x", "Library/LaunchAgents", ".local/bin"):
        (home / d).mkdir(parents=True, exist_ok=True)
    (home / ".zshrc").write_text("# rc\n")
    builder_tmp = Path(made.harness["tmpdir"]) / "scratch.txt"
    builder_tmp.write_text("builder's scratch")
    todo = Path(made.harness["claude_config_dir"]) / "todos" / "t.json"
    todo.parent.mkdir(parents=True)
    todo.write_text("[]")
    done = lay.repo / ".valor" / "handled" / "t1" / "done.md"
    done.parent.mkdir(parents=True)
    done.write_text("I built it and it works.")
    shared_tmp = Path("/private/tmp") / f"valor-probe-{uuid.uuid4().hex}"
    shared_tmp.write_text("x")
    sibling = tmp_path / "sibling.txt"  # under /private/var/folders, outside the check
    sibling.write_text("x")
    check_dir = kws.fresh_dir(lay.checks / "critique-abc")
    (check_dir / "repo").mkdir()
    profile = lay.profiles / "probe.sb"
    profile.write_text(kws.check_profile(lay, check_dir, [], home=home))
    try:
        got = probe(
            profile,
            f"read:{done}",
            f"read:{builder_tmp}",
            f"read:{todo}",
            f"list:{lay.mirror}",
            f"read:{shared_tmp}",
            f"read:{sibling}",
            f"list:{home / '.claude' / 'todos'}",
            f"create:{check_dir / 'repo' / 'x.txt'}",
            f"create:{check_dir / 'tmp' / 'x.txt'}",
            f"create:{home / 'Library' / 'LaunchAgents' / 'x.plist'}",
            f"create:{home / '.local' / 'bin' / 'git'}",
            f"append:{home / '.zshrc'}",
        )
    finally:
        shared_tmp.unlink()
    assert got == ["denied"] * 7 + ["open", "open"] + ["denied"] * 3


def test_the_working_session_cannot_write_where_a_later_process_of_the_user_runs_things(tmp_path):
    _task, made = provision(tmp_path)
    home = tmp_path / "home"
    for d in ("Library/LaunchAgents", ".local/bin", ".local/share/claude", ".claude", ".config/git"):
        (home / d).mkdir(parents=True, exist_ok=True)
    for f in (".zshrc", ".bash_profile", ".gitconfig", ".claude.json"):
        (home / f).write_text("")
    lay = kws.Layout(Path(made.mirror).parent)
    profile = lay.profiles / "probe-turn.sb"
    profile.write_text(kws.turn_profile(lay, [], home=home))
    homebrew = Path("/opt/homebrew") / f".valor-probe-{uuid.uuid4().hex}"
    try:
        got = probe(
            profile,
            f"create:{home / 'Library/LaunchAgents/x.plist'}",
            f"create:{home / '.local/bin/git'}",
            f"create:{home / '.local/share/claude/x'}",
            f"create:{home / '.claude/x'}",
            f"create:{home / '.config/git/config'}",
            f"append:{home / '.zshrc'}",
            f"append:{home / '.bash_profile'}",
            f"append:{home / '.gitconfig'}",
            f"append:{home / '.claude.json'}",
            f"create:{homebrew}",
            f"create:{lay.repo / 'ok.txt'}",
            f"create:{lay.mirror / 'x'}",
            f"create:{lay.home / 'x'}",
            f"read:{lay.home / 'gitconfig'}",
        )
    finally:
        homebrew.unlink(missing_ok=True)
    assert got == ["denied"] * 10 + ["open", "denied", "denied", "open"]


# -- the kernel mirror ------------------------------------------------------------------------


def candidate(made) -> str:
    return scripted.commit(made.workspace, "greeting.txt", "hello\n", "a builder message")


def fetch(made, sha, ref="refs/valor/candidates/t", profile=None, **kw):
    kws.fetch_into_mirror(
        made.mirror, made.workspace, sha, ref, profile or made.harness["sandbox_profile"], "fetch-t", **kw
    )


def test_a_candidate_is_fetched_into_the_mirror_and_the_sending_side_is_sandboxed(tmp_path):
    _task, made = provision(tmp_path)
    sha = candidate(made)
    fetch(made, sha)
    assert git(made.mirror, "rev-parse", "refs/valor/candidates/t") == sha
    # A profile that denies the clone makes the fetch fail: upload-pack runs inside it.
    lay = kws.Layout(Path(made.mirror).parent)
    blind = lay.profiles / "no-repo.sb"
    blind.write_text(kws.profile(rw=[lay.cache], work=lay.root.parent))
    newer = scripted.commit(made.workspace, "more.txt", "more\n", "more")
    with pytest.raises(kws.FetchRefused):
        fetch(made, newer, ref="refs/valor/candidates/u", profile=blind)
    assert git(made.mirror, "rev-parse", "--verify", "--quiet", "refs/valor/candidates/u", check=False) == ""


@pytest.mark.parametrize("plant", ["alternates", "http-alternates", "shallow", "config"])
def test_a_clone_with_alternates_a_shallow_file_or_hostile_config_is_refused(tmp_path, plant):
    _task, made = provision(tmp_path)
    sha = candidate(made)
    gitdir = Path(made.workspace) / ".git"
    if plant == "alternates":
        (gitdir / "objects" / "info" / "alternates").write_text(str(tmp_path) + "\n")
    elif plant == "http-alternates":
        (gitdir / "objects" / "info" / "http-alternates").write_text("http://x\n")
    elif plant == "shallow":
        (gitdir / "shallow").write_text(sha + "\n")
    else:
        git(made.workspace, "config", "uploadpack.packObjectsHook", "touch /tmp/x")
    with pytest.raises(kws.FetchRefused):
        fetch(made, sha)


def test_grafts_and_replace_refs_in_the_clone_do_not_reach_the_mirror(tmp_path):
    _task, made = provision(tmp_path)
    repo = Path(made.workspace)
    real = candidate(made)
    git(repo, "checkout", "-q", "-b", "fake", made.base_sha)
    fake = scripted.commit(repo, "docs/only.md", "docs\n", "docs only")
    git(repo, "checkout", "-q", "-")
    git(repo, "replace", real, fake)
    (repo / ".git" / "info" / "grafts").write_text(f"{real} {fake}\n")
    fetch(made, real)
    mirror = Path(made.mirror)
    tree = subprocess.run(
        ["git", "-C", str(mirror), "ls-tree", "--name-only", "-r", real], capture_output=True, text=True, check=True,
        env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
    ).stdout.split()  # fmt: skip
    assert "greeting.txt" in tree and "docs/only.md" not in tree
    assert git(mirror, "rev-parse", f"{real}^") == made.base_sha
    assert git(mirror, "for-each-ref", "refs/replace") == ""


def test_a_fetch_past_its_size_limit_fails_and_leaves_no_ref(tmp_path):
    _task, made = provision(tmp_path)
    blob = Path(made.workspace) / "big.bin"
    blob.write_bytes(os.urandom(3 * 1024 * 1024))
    git(made.workspace, "add", "big.bin")
    sha = scripted.commit(made.workspace, "x.txt", "x\n", "big")
    with pytest.raises(kws.FetchRefused):
        fetch(made, sha, max_bytes=1024 * 1024)
    assert git(made.mirror, "rev-parse", "--verify", "--quiet", "refs/valor/candidates/t", check=False) == ""
    assert _partials(made.mirror) == []


def _pack_object(kind: int, data: bytes, size: int | None = None) -> bytes:
    size = len(data) if size is None else size
    header = bytearray()
    byte = (kind << 4) | (size & 0x0F)
    size >>= 4
    while size:
        header.append(byte | 0x80)
        byte = size & 0x7F
        size >>= 7
    header.append(byte)
    return bytes(header)


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _delta_bomb_repo(path: Path, expand_to: int) -> str:
    """A repository whose one commit's tree holds a 64 KiB blob and a blob
    stored as a delta of it that expands to `expand_to` bytes, the pack and
    its index written by hand (no git command ever inflates it)."""
    base = b"a" * 65536
    # A copy instruction with no offset or size bytes copies 0x10000 bytes
    # from offset 0: one byte of delta per 64 KiB of output.
    delta = _varint(len(base)) + _varint(expand_to) + b"\x80" * (expand_to // 65536)
    sha_base = hashlib.sha1(b"blob %d\0" % len(base) + base).digest()
    h = hashlib.sha1(b"blob %d\0" % expand_to)
    chunk = base
    for _ in range(expand_to // 65536):
        h.update(chunk)
    sha_big = h.digest()
    tree = b"100644 a.txt\0" + sha_base + b"100644 big.txt\0" + sha_big
    sha_tree = hashlib.sha1(b"tree %d\0" % len(tree) + tree).digest()
    commit = f"tree {sha_tree.hex()}\nauthor t <t@e> 0 +0000\ncommitter t <t@e> 0 +0000\n\nbomb\n".encode()
    sha_commit = hashlib.sha1(b"commit %d\0" % len(commit) + commit).digest()
    objects = []  # (sha, offset, bytes, crc)
    body = bytearray(b"PACK" + struct.pack(">II", 2, 4))
    for sha, kind, data in ((sha_commit, 1, commit), (sha_tree, 2, tree), (sha_base, 3, base)):
        off = len(body)
        raw = _pack_object(kind, data) + zlib.compress(data)
        body += raw
        objects.append((sha, off, zlib.crc32(raw)))
    base_off = objects[2][1]
    off = len(body)
    rel = off - base_off
    enc = bytearray([rel & 0x7F])
    rel >>= 7
    while rel:
        rel -= 1
        enc.insert(0, 0x80 | (rel & 0x7F))
        rel >>= 7
    raw = _pack_object(6, delta) + bytes(enc) + zlib.compress(delta)
    body += raw
    objects.append((sha_big, off, zlib.crc32(raw)))
    pack_sha = hashlib.sha1(body).digest()
    body += pack_sha
    objects.sort()
    fanout = [0] * 256
    for sha, _, _ in objects:
        for i in range(sha[0], 256):
            fanout[i] += 1
    idx = bytearray(b"\377tOc" + struct.pack(">I", 2))
    idx += b"".join(struct.pack(">I", n) for n in fanout)
    idx += b"".join(sha for sha, _, _ in objects)
    idx += b"".join(struct.pack(">I", crc) for _, _, crc in objects)
    idx += b"".join(struct.pack(">I", o) for _, o, _ in objects)
    idx += pack_sha
    idx += hashlib.sha1(idx).digest()
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    packs = path / ".git" / "objects" / "pack"
    (packs / f"pack-{pack_sha.hex()}.pack").write_bytes(bytes(body))
    (packs / f"pack-{pack_sha.hex()}.idx").write_bytes(bytes(idx))
    (path / ".git" / "refs" / "heads" / "main").write_text(sha_commit.hex() + "\n")
    return sha_commit.hex()


def test_a_delta_that_expands_past_the_limits_is_cut_and_leaves_no_ref(tmp_path):
    _task, made = provision(tmp_path)
    lay = kws.Layout(Path(made.mirror).parent)
    bomb = lay.root / "bomb"  # inside the task, where the turn's profile lets upload-pack read
    sha = _delta_bomb_repo(bomb, 3 * 1024**3)
    assert sum(p.stat().st_size for p in (bomb / ".git" / "objects" / "pack").iterdir()) < 200_000
    lay_profile = lay.profiles / "bomb.sb"
    lay_profile.write_text(kws.profile(rw=[bomb], work=lay.root.parent))
    with pytest.raises(kws.FetchRefused) as refused:
        kws.fetch_into_mirror(made.mirror, bomb, sha, "refs/valor/candidates/bomb", lay_profile, "fetch-bomb",
                              max_footprint=256 * 1024 * 1024)  # fmt: skip
    assert "memory limit" in str(refused.value), refused.value
    assert (
        git(made.mirror, "rev-parse", "--verify", "--quiet", "refs/valor/candidates/bomb", check=False) == ""
    )


# -- the sweep of other tasks' services ------------------------------------------------------


def _start(dsn, tmp_path, services):
    async def go():
        task, b = await scripted.provisioned(dsn, tmp_path, services=services)
        return task, b

    return run(go())


def _service_pids(task) -> list[int]:
    listing = subprocess.run(
        ["/bin/ps", "-A", "-o", "pid=,uid="], capture_output=True, text=True, check=True
    ).stdout
    check = runs._sandbox_check()
    out = []
    for line in listing.splitlines():
        pid, uid = (int(x) for x in line.split())
        if (
            uid == os.getuid()
            and check(pid, f"valor.service.{task}")
            and not check(pid, "valor.service.none")
        ):
            out.append(pid)
    return out


def test_a_run_stops_services_a_killed_kernel_left_up_unless_their_run_is_live(dsn, tmp_path):
    a, b_a = _start(dsn, tmp_path / "a", ["postgres", "redis"])
    b, _b_b = _start(dsn, tmp_path / "b", ["postgres"])
    lay_a = kws.Layout(Path(b_a.mirror).parent)
    names, ports = kws.services_of(b_a)
    # Task A's kernel died with its services up.
    kws.start_services(a, lay_a, names, ports)
    assert _service_pids(a)

    async def run_b(hold_a: bool):
        holder = await db.connect(dsn)
        try:
            if hold_a:
                await holder.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"run:{a}",))
            gateway = Gateway(dsn)
            await gateway.start()
            try:
                return await scripted.route(gateway, b, scripted.RUNNERS, dsn=dsn)
            finally:
                await gateway.close()
        finally:
            await holder.close()

    run(run_b(hold_a=True))  # A's run is live: its services stay
    assert _service_pids(a)
    run(run_b(hold_a=False))
    assert not _service_pids(a) and not _service_pids(b)

    async def reaped():
        async with await db.connect(dsn) as conn:
            return [r["payload"] for r in await ledger.read(conn, b) if r["type"] == "services.reaped"]

    found = run(reaped())
    assert found and {p["task"] for p in found[-1]["processes"]} == {a}
    assert all("redis" in p["command"] or "postgres" in p["command"] for p in found[-1]["processes"])


def test_after_the_sweep_the_services_left_fit_in_16_gb(dsn, tmp_path):
    a, b_a = _start(dsn, tmp_path / "a", ["postgres", "redis"])
    b, b_b = _start(dsn, tmp_path / "b", ["postgres"])
    kws.start_services(a, kws.Layout(Path(b_a.mirror).parent), *kws.services_of(b_a))
    lay_b = kws.Layout(Path(b_b.mirror).parent)
    names, ports = kws.services_of(b_b)

    async def sweep_and_up():
        async with await db.connect(dsn) as conn:
            await kws.sweep(conn, b)
        kws.start_services(b, lay_b, names, ports)

    run(sweep_and_up())
    try:
        assert not _service_pids(a)
        total = sum(kws.footprint(p) for p in _service_pids(b))
        assert 0 < total <= (400 + 50) * 1024 * 1024  # machine.md: workspace cluster 400 MB, Redis 50 MB
    finally:
        kws.stop_services(b, lay_b)


def test_workspace_remove_is_refused_while_the_task_runs_and_frees_the_ports_after_a_stop(dsn, tmp_path):
    task, b = _start(dsn, tmp_path, ["postgres"])
    env = {**os.environ, "VALOR_DB": __import__("tests.conftest", fromlist=["TEST_DB"]).TEST_DB}
    root = Path(__file__).resolve().parent.parent

    def cli(*args):
        return subprocess.run([__import__("sys").executable, "-m", "core", *args], cwd=root, env=env,
                              capture_output=True, text=True, check=False)  # fmt: skip

    refused = cli("workspace", "remove", task)
    assert refused.returncode == 1 and "only a stopped or merged" in refused.stderr
    shown = json.loads(cli("workspace", "show", task).stdout)
    assert shown["mirror"] == b.mirror and shown["project"]["ports"]["postgres"]

    async def stop_and_ports():
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, task, reason="test")
            return await kws.taken_ports(conn)

    port = b.project["ports"]["postgres"]
    assert port in run(stop_and_ports())
    removed = cli("workspace", "remove", task)
    assert removed.returncode == 0, removed.stderr
    assert not Path(b.mirror).parent.exists()

    async def after():
        async with await db.connect(dsn) as conn:
            return await kws.taken_ports(conn), [
                r for r in await ledger.read(conn, task) if r["type"] == "workspace.removed"
            ]

    ports, rows = run(after())
    assert port not in ports and rows[0]["payload"]["provenance"]["by"] == "tom"


def _partials(repo) -> list[str]:
    objects = Path(repo) / "objects"
    return [
        p.name
        for p in [*objects.glob("pack/tmp_*"), *objects.glob("incoming-*"), *objects.glob("tmp_objdir-*")]
    ]


def test_the_file_size_limit_is_the_limit_asked_for(tmp_path):
    out = tmp_path / "big"
    code, _ = kws.bounded(
        ["/bin/dd", "if=/dev/zero", f"of={out}", "bs=1024", "count=4096"],
        cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, max_bytes=1024 * 1024, max_footprint=1024**3, timeout=30,
    )  # fmt: skip
    assert code != 0 and out.stat().st_size == 1024 * 1024


def test_a_command_past_its_time_is_killed(tmp_path):
    code, _ = kws.bounded(
        ["/bin/sleep", "30"], cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, max_bytes=1024, max_footprint=1024**3,
        timeout=1,
    )  # fmt: skip
    assert code == "timeout"


def test_a_fetch_names_one_full_commit_and_one_mirror_ref(tmp_path):
    _, made = provision(tmp_path)
    sha = candidate(made)
    with pytest.raises(kws.FetchRefused, match="not a full commit"):
        fetch(made, sha[:12])
    with pytest.raises(kws.FetchRefused, match="not a mirror ref"):
        fetch(made, sha, ref="refs/heads/main")


@pytest.mark.parametrize("plant", ["gitfile", "commondir"])
def test_a_clone_whose_git_directory_lives_elsewhere_is_refused(tmp_path, plant):
    _, made = provision(tmp_path)
    sha = candidate(made)
    repo = Path(made.workspace)
    if plant == "gitfile":
        real = tmp_path / "real.git"
        (repo / ".git").rename(real)
        (repo / ".git").write_text(f"gitdir: {real}\n")
        match = "not a directory"
    else:
        other = scripted.toy_repo(tmp_path / "other")
        (repo / ".git" / "commondir").write_text(str(other / ".git") + "\n")
        match = "commondir"
    with pytest.raises(kws.FetchRefused, match=match):
        fetch(made, sha)


def test_two_repositories_with_one_name_never_share_a_cache(tmp_path):
    a = scripted.toy_repo(tmp_path / "a")
    b = scripted.toy_repo(tmp_path / "b")
    work = tmp_path / "work"
    ca = kws._cache(spec(a), None, work)
    cb = kws._cache(spec(b), None, work)
    assert ca != cb and ca.parent == cb.parent


def test_cache_refusals(tmp_path):
    with pytest.raises(kws.Refused, match="neither a local repository nor an https URL"):
        kws._cache(spec("git@github.com:tomcounsell/ai.git"), None, tmp_path)
    with pytest.raises(kws.Refused, match="fetching https://127.0.0.1:9/x.git"):
        kws._cache(spec("https://127.0.0.1:9/x.git"), None, tmp_path)  # no network needed: nothing listens
    assert kws.needs_credential(
        "fatal: could not read Username for 'https://github.com': terminal prompts disabled"
    )
    assert kws.needs_credential("remote: Invalid username or password.\nfatal: Authentication failed")
    assert not kws.needs_credential("fatal: unable to access: Failed to connect to 127.0.0.1 port 9")


def test_a_cluster_that_will_not_start_leaves_no_directory_and_no_services(tmp_path):
    import socket

    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    port = holder.getsockname()[1]
    src = scripted.toy_repo(tmp_path)
    task = ledger.new_id()
    try:
        with pytest.raises(kws.Refused, match="did not start"):
            kws.provision(task, spec(src, services=["postgres"]), {"postgres": port}, work=tmp_path / "work")
    finally:
        holder.close()
    assert not (tmp_path / "work" / task).exists()
    assert not runs.marked_services([task])


def test_a_provisioning_killed_mid_setup_is_swept_once_its_provisioning_is_not_live(dsn, tmp_path):
    other = ledger.new_id()
    work = tmp_path / "work"
    src = scripted.toy_repo(tmp_path)
    port = kws.choose_port((5540, 5579), set())
    kws.provision(other, spec(src, services=["postgres"]), {"postgres": port}, work=work)
    lay = kws.Layout(work / other)
    kws.start_services(other, lay, ["postgres"], {"postgres": port})  # as a kernel killed mid-setup left it

    async def sweep(hold: bool):
        holder = await db.connect(dsn)
        try:
            if hold:
                await holder.execute(
                    "SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"provision:{other}",)
                )
            async with await db.connect(dsn) as conn:
                return await kws.sweep(conn, "abcdef999999", work)
        finally:
            await holder.close()

    assert run(sweep(hold=True)) == [] and runs.marked_services([other])
    found = run(sweep(hold=False))
    assert found and all(p["task"] == other and p["orphan"] for p in found)
    assert not runs.marked_services([other])
    assert port in kws.reserved_ports(work)  # its ports stay reserved until it is removed


def test_a_sweep_never_waits_on_the_ports_lock(dsn, tmp_path):
    async def go():
        holder = await db.connect(dsn)
        try:
            await holder.execute("SELECT pg_advisory_lock(hashtextextended('workspace:ports', 0))")
            async with await db.connect(dsn) as conn:
                return await asyncio.wait_for(kws.sweep(conn, "abcdef999999", tmp_path), 5)
        finally:
            await holder.close()

    assert run(go()) == []


def test_a_run_fails_naming_the_log_when_the_tasks_postgres_will_not_start_and_spends_no_turn(dsn, tmp_path):
    task, b = run(scripted.provisioned(dsn, tmp_path, services=["postgres"]))
    lay = kws.Layout(Path(b.mirror).parent)
    (lay.pg / "data" / "PG_VERSION").write_text("1\n")

    async def go():
        gateway = Gateway(dsn)
        await gateway.start()
        try:
            return await scripted.route(gateway, task, scripted.RUNNERS, dsn=dsn)
        finally:
            await gateway.close()

    out = run(go())
    assert out["status"] == "failed" and "postgres.log" in out["turn"]["result"]
    assert not [r for r in run(_rows(dsn, task)) if r["type"] == "turn.started"]


def test_the_tasks_postgres_is_up_while_a_runners_turn_runs_and_down_after(dsn, tmp_path):
    task, b = run(scripted.provisioned(dsn, tmp_path, services=["postgres"]))
    port = b.project["ports"]["postgres"]
    scripted.steer(Path(b.workspace), probe_port=port)

    async def go():
        gateway = Gateway(dsn)
        await gateway.start()
        try:
            return await scripted.route(gateway, task, scripted.RUNNERS, dsn=dsn)
        finally:
            await gateway.close()

    run(go())
    assert scripted.turns(Path(b.workspace))[0]["port_open"] is True
    assert not runs.marked_services([task])


async def _rows(dsn, task):
    async with await db.connect(dsn) as conn:
        return await ledger.read(conn, task)


# -- the command line ---------------------------------------------------------------------


def _cli(tmp_path, *args):
    import sys

    from tests.conftest import TEST_DB

    root = Path(__file__).resolve().parent.parent
    return subprocess.run(
        [sys.executable, "-m", "core", *args], cwd=root, capture_output=True, text=True, check=False,
        env={**os.environ, "VALOR_DB": TEST_DB, "VALOR_WORK": str(tmp_path / "work")},
    )  # fmt: skip


def _spec_file(tmp_path, **extra) -> Path:
    src = tmp_path / "src" / "toy"
    if not src.exists():
        src = scripted.toy_repo(tmp_path)
        git(src, "branch", "rebuild")
    lines = ['name = "toy"', f'repo = "{src}"', 'kind = "plain"', 'suite = "true"']
    lines += [f"{k} = {json.dumps(v)}" for k, v in extra.items()]
    path = tmp_path / "toy.toml"
    path.write_text("\n".join(lines) + "\n")
    return path


def test_start_project_from_the_command_line(dsn, tmp_path):
    spec_path = _spec_file(tmp_path, services=["postgres"])
    for flag in (["--workspace", "x"], ["--harness-config", "x"], ["--target-branch", "x"]):
        refused = _cli(tmp_path, "start", "go", "--project", str(spec_path), *flag)
        assert refused.returncode == 1 and "takes no --workspace" in refused.stderr
    bad = _cli(tmp_path, "start", "go", "--project", str(spec_path), "--base", "0" * 40)
    assert bad.returncode == 1 and "start refused" in bad.stderr
    work = tmp_path / "work"
    assert not [
        d for d in work.iterdir() if d.name != "cache" and d.name != "bin"
    ]  # the workspace was removed
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        started = list(pool.map(lambda _: _cli(tmp_path, "start", "go", "--project",
                                               str(spec_path), "--branch", "rebuild"), range(2)))  # fmt: skip
    assert all(s.returncode == 0 for s in started), [s.stderr for s in started]
    shown = [json.loads(_cli(tmp_path, "workspace", "show", s.stdout.strip()).stdout) for s in started]
    assert shown[0]["project"]["ports"]["postgres"] != shown[1]["project"]["ports"]["postgres"]
    assert {s["target_branch"] for s in shown} == {"rebuild"}
    assert all(s["project"]["ports"]["postgres"] != 5439 for s in shown)


# -- writing a fresh session's inputs -------------------------------------------------------


def test_write_inputs_writes_nothing_through_what_it_finds(tmp_path):
    checkout = tmp_path / "repo"
    checkout.mkdir()
    (checkout / ".valor").mkdir()  # an uncommitted .valor already there
    with pytest.raises(FileExistsError):
        kws.write_inputs(checkout, {"request.md": "x"})
    assert not (checkout / ".valor" / "inputs").exists()
    # A verdict file placed where the kernel looks, bypassing its mkdir.
    planted = tmp_path / "planted"
    (planted / ".valor").mkdir(parents=True)
    (planted / ".valor" / "verdict.json").write_text('{"verdict": "sound"}')
    with pytest.raises(FileExistsError):
        kws.write_inputs(planted, {"request.md": "x"})
    target = tmp_path / "target.txt"
    target.write_text("untouched")
    (tmp_path / "link").symlink_to(checkout)
    with pytest.raises(OSError):
        kws.write_inputs(tmp_path / "link", {"request.md": "x"})  # O_NOFOLLOW on the checkout itself


def test_each_input_is_created_new_and_never_through_a_link(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("untouched")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "linked.md").symlink_to(target)  # a dangling-safe link to a real file
    (inputs / "existing.md").write_text("old")
    fd = os.open(inputs, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(FileExistsError):
            kws.write_files(fd, {"existing.md": "new"})
        with pytest.raises(OSError):
            kws.write_files(fd, {"linked.md": "new"})
        (inputs / "dangling.md").symlink_to(tmp_path / "would-be-created.txt")
        with pytest.raises(OSError):
            kws.write_files(fd, {"dangling.md": "new"})  # O_NOFOLLOW: no write through a dangling link
        kws.write_files(fd, {"fresh.md": "ok"})
    finally:
        os.close(fd)
    assert target.read_text() == "untouched" and (inputs / "existing.md").read_text() == "old"
    assert not (tmp_path / "would-be-created.txt").exists() and (inputs / "fresh.md").read_text() == "ok"


# -- a directory with no task row, through the command line ----------------------------------


def test_an_orphan_directory_is_shown_and_removed_only_when_its_provisioning_is_not_live(dsn, tmp_path):
    work = tmp_path / "work"
    orphan = ledger.new_id()
    src = scripted.toy_repo(tmp_path)
    kws.provision(orphan, spec(src), {}, work=work)
    shown = _cli(tmp_path, "workspace", "show", orphan)
    assert shown.returncode == 0 and json.loads(shown.stdout)["orphan"] == str(work / orphan)

    async def held_remove():
        holder = await db.connect(dsn)
        try:
            await holder.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"provision:{orphan}",))
            return await asyncio.to_thread(_cli, tmp_path, "workspace", "remove", orphan)
        finally:
            await holder.close()

    refused = run(held_remove())
    assert refused.returncode == 1 and "being provisioned now" in refused.stderr
    assert (work / orphan).exists()
    removed = _cli(tmp_path, "workspace", "remove", orphan)
    assert removed.returncode == 0, removed.stderr
    assert not (work / orphan).exists()
    assert _cli(tmp_path, "workspace", "remove", orphan).returncode == 1


def test_a_verdict_file_that_appears_after_the_kernels_mkdir_is_refused(tmp_path):
    valor = tmp_path / ".valor"
    valor.mkdir()
    fd = os.open(valor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        kws.no_verdict_yet(fd)  # an empty .valor passes
        (valor / "verdict.json").symlink_to(tmp_path / "nowhere")  # appeared after the kernel made .valor
        with pytest.raises(FileExistsError, match="before the session's turn"):
            kws.no_verdict_yet(fd)
    finally:
        os.close(fd)


def _orphan_with_services(tmp_path):
    work = tmp_path / "work"
    orphan = ledger.new_id()
    src = scripted.toy_repo(tmp_path)
    port = kws.choose_port((5540, 5579), set())
    kws.provision(orphan, spec(src, services=["postgres"]), {"postgres": port}, work=work)
    lay = kws.Layout(work / orphan)
    kws.start_services(orphan, lay, ["postgres"], {"postgres": port})
    return work, orphan, lay


async def _become_task(dsn, task_id):
    async with await db.connect(dsn) as conn:
        await tasks.start(conn, tasks.Brief(id=task_id, instruction="x"))


def test_a_directory_that_becomes_a_task_after_the_scan_is_left_to_its_run(dsn, tmp_path):
    work, orphan, lay = _orphan_with_services(tmp_path)
    try:

        async def go():
            async with await db.connect(dsn) as conn:
                return await kws.sweep(
                    conn, "abcdef999999", work, after_scan=lambda: _become_task(dsn, orphan)
                )

        assert run(go()) == []
        assert runs.marked_services([orphan])  # its services were not stopped
    finally:
        kws.stop_services(orphan, lay)


@pytest.mark.parametrize("when", ["before the lock", "under the lock"])
def test_orphan_removal_refuses_a_directory_that_became_a_task(dsn, tmp_path, monkeypatch, when):
    import argparse

    from core import __main__ as cli

    work, orphan, lay = _orphan_with_services(tmp_path)
    monkeypatch.setattr(kws, "work_dir", lambda: work)
    args = argparse.Namespace(task_id=orphan, workspace_command="remove", by="tom", via="test")
    try:

        async def go():
            if when == "before the lock":
                await _become_task(dsn, orphan)
            async with await db.connect(dsn) as conn:
                hook = None if when == "before the lock" else (lambda: _become_task(dsn, orphan))
                with pytest.raises(SystemExit, match="became a task"):
                    await cli._orphan(conn, args, after_lock=hook)

        run(go())
        assert lay.root.exists()
    finally:
        kws.stop_services(orphan, lay)


def test_the_mirror_fetch_keeps_to_the_callers_git_deadline(tmp_path):
    import time

    from core import git as kgit

    _, made = provision(tmp_path)
    sha = candidate(made)
    with kgit.deadline(0.01):
        time.sleep(0.05)
        with pytest.raises((kgit.GitError, kws.FetchRefused), match="deadline"):
            fetch(made, sha)
    assert kgit.remaining(5.0) == 5.0  # outside a deadline, the limit stands
