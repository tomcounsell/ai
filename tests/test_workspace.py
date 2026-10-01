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

from core import db, ledger, router, runs, tasks
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


def spec(src: Path, **kw) -> kws.Spec:
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
    assert made.origin_url == made.push_url  # until 1.4d the merge lands on the task's own origin
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
        kws.stop_services(task, lay)
    assert not (lay.pg / "data" / "postmaster.pid").exists()


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
                return await router.run(gateway, b, scripted.RUNNERS, dsn=dsn)
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


def test_after_the_sweep_the_services_left_fit_the_16_gb_budget(dsn, tmp_path):
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
