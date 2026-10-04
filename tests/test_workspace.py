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
import stat
import struct
import subprocess
import tempfile
import time
import uuid
import zlib
from pathlib import Path

import psycopg
import pytest

from core import db, ledger, router, runs, tasks
from core import git as kgit
from core import workspace as kws
from core.gateway import Gateway
from core.settings import settings
from tests import scripted
from tests.ports import span as ports_span

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
        elif mode == "mkdir":
            os.mkdir(arg)
            os.rmdir(arg)
        elif mode == "rename":
            src, _, dst = arg.partition(">")
            os.rename(src, dst)
        elif mode == "plant":  # move a directory aside, link a planted one in its place
            src, _, planted = arg.partition(">")
            os.rename(src, src + ".old")
            os.symlink(planted, src)
        elif mode == "swap":  # renamex_np(RENAME_SWAP)
            import ctypes
            src, _, dst = arg.partition(">")
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.renamex_np(src.encode(), dst.encode(), 2) != 0:
                err = ctypes.get_errno()
                raise OSError(err, os.strerror(err))
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
        ports["postgres"] = kws.choose_port(ports_span((5540, 5579)), set())
    if "redis" in services:
        ports["redis"] = kws.choose_port(ports_span((6440, 6459)), set(ports.values()))
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
    port_b = kws.choose_port(ports_span((5540, 5579)), taken)
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


def test_no_directory_above_a_denied_path_can_be_moved_and_nothing_mounts(tmp_path):
    """Renaming a directory above a denied path, by any name or call, would
    move the denied path's contents to a name no rule covers, or let a turn
    put its own directory in its place; so would a mount."""
    home = tmp_path / "home"
    for d in (
        ".local/share/claude/versions",
        ".local/share/uv/python",
        ".config/other",
        "Library/Caches",
        ".cache/uv",
    ):
        (home / d).mkdir(parents=True)
    (home / ".local/bin").mkdir()
    claude = home / ".local/bin/claude"
    claude.write_text("the real one")
    keys = home / ".config/valor-kernel"
    keys.mkdir()
    (keys / "pgpass").write_text("secret")
    planted = tmp_path / "planted"
    (planted / "bin").mkdir(parents=True)
    (planted / "bin/claude").write_text("planted")
    lay = kws.Layout(tmp_path / "work" / "abc123")
    profile = tmp_path / "turn.sb"
    # The fake home is under pytest's temp directory, which a turn's profile
    # denies whole; `tmp` leaves it reachable so the rules shown are the
    # ancestors' and the home's own.
    profile.write_text(
        kws.turn_profile(lay, [], home=home, kernel=[keys, home / "absent" / "keys"], tmp=True)
    )
    got = probe(
        profile,
        f"rename:{home / '.local'}>{home / '.local-old'}",
        f"rename:{home / '.local/share'}>{home / '.local/share-old'}",
        f"plant:{home / '.local'}>{planted}",
        f"swap:{home / '.local'}>{planted}",
        f"rename:{home / '.LOCAL'}>{home / '.local-alias'}",
        f"rename:{home / '.config'}>{home / '.config-old'}",
        f"read:{keys / 'pgpass'}",
        f"rename:{home / 'Library'}>{home / 'Library-old'}",
        f"rename:{home}>{tmp_path / 'home-old'}",
        f"mkdir:{home / 'absent'}",
        f"create:{home / '.cache/uv/x'}",
        f"create:{home / '.local/share/uv/python/x'}",
        f"create:{home / '.local/x'}",
        f"create:{home / '.config/x'}",
        f"create:{home / 'Library/Caches/x'}",
        f"mkdir:{home / '.config/newapp'}",
        f"rename:{home / '.config/other'}>{home / '.config/other2'}",
    )
    assert got == ["denied"] * 12 + ["open"] * 5
    assert claude.read_text() == "the real one" and (keys / "pgpass").read_text() == "secret"

    image = tmp_path / "planted.dmg"
    subprocess.run(
        ["/usr/bin/hdiutil", "create", "-quiet", "-size", "2m", "-fs", "HFS+", "-volname", "vprobe",
         "-srcfolder", str(planted / "bin"), str(image)],
        check=True, capture_output=True,
    )  # fmt: skip
    mountpoint = home / ".local/bin"
    try:
        done = subprocess.run(
            ["/usr/bin/sandbox-exec", "-D", "GATEWAY_PORT=1", "-D", "VALOR_TURN=probe", "-f", str(profile),
             "/usr/bin/hdiutil", "attach", "-nobrowse", "-mountpoint", str(mountpoint), str(image)],
            capture_output=True, text=True, check=False, env={"PATH": "/usr/bin:/bin"},
        )  # fmt: skip
        info = subprocess.run(["/usr/bin/hdiutil", "info"], capture_output=True, text=True, check=True).stdout
        assert not os.path.ismount(mountpoint) and str(image) not in info, done.stdout
        assert claude.read_text() == "the real one"
    finally:
        if os.path.ismount(mountpoint):
            subprocess.run(["/usr/bin/hdiutil", "detach", "-force", str(mountpoint)], check=False)


def test_a_turn_mounts_nothing_and_opens_nothing_outside_its_sandbox(tmp_path):
    """A disk image mounted at its own `/Volumes/<label>` can stand in for the
    backup disk, and `open` hands an image, or an app the turn wrote, to a
    process outside the sandbox."""
    label = f"vprobe{uuid.uuid4().hex[:8]}"
    volume = Path("/Volumes") / label
    image = tmp_path / "planted.dmg"
    subprocess.run(
        ["/usr/bin/hdiutil", "create", "-quiet", "-size", "2m", "-fs", "HFS+", "-volname", label, str(image)],
        check=True, capture_output=True,
    )  # fmt: skip
    marker = tmp_path / "denied" / "ran"
    marker.parent.mkdir()
    app = tmp_path / "Planted.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    exe = app / "Contents/MacOS/planted"
    exe.write_text(f"#!/bin/sh\n/usr/bin/touch {marker}\n")
    exe.chmod(0o755)
    (app / "Contents/Info.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict>'
        "<key>CFBundleExecutable</key><string>planted</string>"
        f"<key>CFBundleIdentifier</key><string>valor.probe.{label}</string>"
        "<key>LSBackgroundOnly</key><true/></dict></plist>"
    )
    profile = tmp_path / "turn.sb"
    lay = kws.Layout(tmp_path / "work" / "abc123")
    profile.write_text(kws.turn_profile(lay, [], kernel=[marker.parent]))
    attempts = [
        ["/usr/bin/hdiutil", "attach", "-nobrowse", str(image)],
        ["/usr/sbin/diskutil", "mount", label],
        ["/usr/bin/open", "-W", str(image)],
        ["/usr/bin/open", "-W", "-a", "/System/Library/CoreServices/DiskImageMounter.app", str(image)],
        ["/usr/bin/open", "-W", "-g", str(app)],
    ]
    try:
        for argv in attempts:
            subprocess.run(
                ["/usr/bin/sandbox-exec", "-D", "GATEWAY_PORT=1", "-D", "VALOR_TURN=probe", "-f", str(profile), *argv],
                capture_output=True, text=True, check=False, env={"PATH": "/usr/bin:/bin"},
            )  # fmt: skip
            info = subprocess.run(
                ["/usr/bin/hdiutil", "info"], capture_output=True, text=True, check=True
            ).stdout
            assert str(image) not in info and not volume.exists() and not marker.exists(), argv
    finally:
        if volume.exists():
            subprocess.run(["/usr/bin/hdiutil", "detach", "-force", str(volume)], check=False)


def test_a_turn_can_neither_read_nor_write_a_scratch_cluster(tmp_path):
    """`initdb`, `pg_ctl` and the server run on a scratch cluster outside any
    sandbox, so its directory is inside the kernel key directory, which every
    profile denies."""
    from core import backup
    from core.settings import settings

    assert Path(settings.pg_scratch).parent == Path(settings.pg_passfile).parent
    profile = tmp_path / "turn.sb"
    profile.write_text(kws.turn_profile(kws.Layout(tmp_path / "work" / "abc123"), [], home=tmp_path / "home"))
    with backup.scratch_cluster(prefix=f"vk-{uuid.uuid4().hex[:6]}-") as cluster:
        assert cluster.root.parent == Path(settings.pg_scratch)
        conf = cluster.data / "postgresql.conf"
        assert probe(profile, f"list:{cluster.root}", f"read:{conf}", f"append:{conf}") == ["denied"] * 3


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


def test_a_clone_reached_through_a_link_is_refused_at_every_lookup(tmp_path):
    _task, made = provision(tmp_path)
    sha = candidate(made)
    repo = Path(made.workspace)
    linked = tmp_path / "linked-ws"
    linked.symlink_to(repo)
    with pytest.raises(kws.FetchRefused, match="not a plain directory"):
        kws.fetch_into_mirror(
            made.mirror, linked, sha, "refs/valor/candidates/l", made.harness["sandbox_profile"], "f"
        )
    assert not git_repo(linked) and git_repo(repo)
    # objects moved out and linked back: refused, though nothing names alternates in the clone itself.
    outside = tmp_path / "outside-objects"
    (repo / ".git" / "objects").rename(outside)
    (outside / "info" / "alternates").write_text(str(tmp_path) + "\n")
    (repo / ".git" / "objects").symlink_to(outside)
    with pytest.raises(kws.FetchRefused, match="objects/info/alternates"):
        fetch(made, sha)
    (repo / ".git" / "objects").unlink()
    outside.rename(repo / ".git" / "objects")
    (repo / ".git" / "objects" / "info" / "alternates").unlink()
    fetch(made, sha)
    assert git(made.mirror, "rev-parse", "refs/valor/candidates/t") == sha


def test_a_clone_with_no_dot_git_has_the_names_looked_up_in_itself(tmp_path):
    _task, made = provision(tmp_path)
    sha = candidate(made)
    bare = tmp_path / "bare.git"
    git(tmp_path, "clone", "-q", "--bare", made.workspace, str(bare))
    (bare / "shallow").write_text(sha + "\n")
    with pytest.raises(kws.FetchRefused, match="the clone has shallow"):
        kws.fetch_into_mirror(
            made.mirror, bare, sha, "refs/valor/candidates/b", made.harness["sandbox_profile"], "f"
        )


def git_repo(path) -> bool:
    return kgit.is_repo(str(path))


def checks_with_verdict(tmp_path: Path, text: str = '{"verdict": "sound", "findings": []}') -> Path:
    checks = tmp_path / "checks"
    valor = checks / "critique-abc" / "repo" / ".valor"
    valor.mkdir(parents=True)
    (valor / "verdict.json").write_text(text)
    return checks


def test_a_verdict_is_moved_aside_then_read(tmp_path):
    checks = checks_with_verdict(tmp_path)
    verdict, why = kws.read_verdict(checks, "critique-abc", "t1")
    assert verdict == {"verdict": "sound", "findings": []} and why is None
    valor = checks / "critique-abc" / "repo" / ".valor"
    assert (valor / "handled" / "t1" / "verdict.json").is_file() and not (valor / "verdict.json").exists()
    assert kws.read_verdict(checks, "critique-abc", "t2") == (None, "no .valor/verdict.json")


def test_a_hard_linked_verdict_is_refused(tmp_path):
    checks = checks_with_verdict(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text('{"verdict": "sound", "findings": ["outside-marker"]}')
    verdict_file = checks / "critique-abc" / "repo" / ".valor" / "verdict.json"
    verdict_file.unlink()
    os.link(outside, verdict_file)
    verdict, why = kws.read_verdict(checks, "critique-abc", "t1")
    assert verdict is None and why == "verdict.json has 2 links"


def test_a_sparse_verdict_is_refused_unread(tmp_path):
    checks = checks_with_verdict(tmp_path)
    with open(checks / "critique-abc" / "repo" / ".valor" / "verdict.json", "r+b") as f:
        f.truncate(1 << 50)
    verdict, why = kws.read_verdict(checks, "critique-abc", "t1")
    assert verdict is None and why == f"verdict.json is sparse ({1 << 50} bytes claimed, 4096 on disk)"


def test_a_check_directory_swapped_for_a_link_gives_no_verdict(tmp_path):
    outside = tmp_path / "outside"
    (outside / "repo" / ".valor").mkdir(parents=True)
    (outside / "repo" / ".valor" / "verdict.json").write_text('{"verdict": "sound", "findings": []}')
    before = sorted(str(p) for p in outside.rglob("*"))
    checks = tmp_path / "checks"
    checks.mkdir()
    (checks / "critique-abc").symlink_to(outside)
    verdict, why = kws.read_verdict(checks, "critique-abc", "t1")
    assert verdict is None and why == "critique-abc is not a plain directory"
    assert sorted(str(p) for p in outside.rglob("*")) == before


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
        cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, max_bytes=1024 * 1024, max_footprint=1024**3,
    )  # fmt: skip
    assert code != 0 and out.stat().st_size == 1024 * 1024


def test_a_stopped_caller_kills_the_command_and_its_group(tmp_path):
    pidfile = tmp_path / "pid"

    async def go():
        argv = ["/bin/sh", "-c", f"/bin/sleep 30 & echo $! > {pidfile}; wait"]
        t = asyncio.create_task(
            kgit.threaded(lambda: kws.bounded(argv, cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, max_bytes=1024,
                                              max_footprint=1024**3))
        )  # fmt: skip
        while not pidfile.exists() or not pidfile.read_text().strip():
            await asyncio.sleep(0.05)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        return int(pidfile.read_text())

    child = run(go())
    for _ in range(200):  # the group was sent SIGKILL; wait for the kernel to retire the child
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("the command's child outlived its stopped caller")


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _detached_holder(tmp_path: Path) -> str:
    """Shell that starts a program which leaves git's process group with
    setsid, keeps git's stdout and stderr open, and writes its pid to
    `held.pid` once it has left; the program sleeps until the test kills it."""
    held = tmp_path / "held.pid"
    return (
        '/usr/bin/perl -MPOSIX -e \'POSIX::setsid() or die; open F, ">", $ARGV[0]; print F $$; close F; '
        f"sleep 600' {held} &\n"
        f"while [ ! -s {held} ]; do /bin/sleep 0.05; done\n"
    )


def _fake_git(tmp_path: Path, monkeypatch, tail: str) -> Path:
    script = tmp_path / "fake-git"
    script.write_text(f"#!/bin/bash\n{_detached_holder(tmp_path)}echo $$ > {tmp_path / 'git.pid'}\n{tail}")
    script.chmod(0o755)
    monkeypatch.setattr(kgit, "binary", lambda: str(script))
    return tmp_path / "held.pid"


def test_a_git_call_ends_when_git_exits_though_a_program_it_started_holds_its_output(tmp_path, monkeypatch):
    held = _fake_git(tmp_path, monkeypatch, "echo out; echo err >&2; exit 0\n")
    try:
        done = kgit._git(tmp_path, "version")
        assert (done.returncode, done.stdout, done.stderr) == (0, "out\n", "err\n")
        assert not _gone(int(held.read_text()))
    finally:
        os.kill(int(held.read_text()), 9)


def test_a_stopped_git_call_returns_though_a_program_git_started_in_its_own_session_holds_its_output(
    tmp_path, monkeypatch, caplog
):
    """Git's group is killed and the cancel is raised while the program
    that left the group still runs; nothing is logged as an error."""
    held = _fake_git(tmp_path, monkeypatch, "exec /bin/sleep 600\n")

    async def go():
        t = asyncio.create_task(kgit.threaded(kgit._git, tmp_path, "version"))
        while not (tmp_path / "git.pid").exists() or not (tmp_path / "git.pid").read_text().strip():
            await asyncio.sleep(0.05)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    try:
        with caplog.at_level("ERROR"):
            run(go())
        assert _gone(int((tmp_path / "git.pid").read_text()))
        assert not _gone(int(held.read_text()))
        assert [r for r in caplog.records if r.levelname == "ERROR"] == []
    finally:
        os.kill(int(held.read_text()), 9)


def test_a_bounded_command_ends_when_it_exits_though_a_program_it_started_holds_its_stderr(tmp_path):
    argv = ["/bin/bash", "-c", _detached_holder(tmp_path) + "echo err >&2; exit 3\n"]
    try:
        code, err = kws.bounded(argv, cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, max_bytes=1024**2,
                                max_footprint=1024**3)  # fmt: skip
        assert (code, err) == (3, "err\n")
        assert not _gone(int((tmp_path / "held.pid").read_text()))
    finally:
        os.kill(int((tmp_path / "held.pid").read_text()), 9)


def test_no_turn_can_open_or_list_the_kernels_output_files_and_git_output_is_read_whole(
    tmp_path, monkeypatch
):
    """The output files have a path, in `git.output_dir`, between creation
    and unlink. A builder turn's profile (the kernel paths from settings)
    cannot list that directory or open a file in it, and a large output of
    the kernel's own git comes back whole from files made there."""
    out = kgit.output_dir()
    assert out.is_relative_to(Path(settings.performing_dir))
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    profile = tmp_path / "turn.sb"
    profile.write_text(kws.turn_profile(kws.Layout(tmp_path / "work" / "abc123"), [], home=tmp_path / "home"))
    with tempfile.NamedTemporaryFile(prefix="valor-output-", dir=out) as f:
        assert probe(profile, f"list:{out}", f"read:{f.name}", f"append:{f.name}") == ["denied"] * 3

    made = []
    real = tempfile.TemporaryFile

    def spy(*a, **kw):
        made.append(Path(kw["dir"]))
        return real(*a, **kw)

    monkeypatch.setattr(kgit.tempfile, "TemporaryFile", spy)
    repo = tmp_path / "repo"
    kgit._git(tmp_path, "init", "-q", str(repo))
    blob = tmp_path / "blob"
    blob.write_bytes(os.urandom(5 * 1024**2))
    sha = kgit._git(repo, "hash-object", "-w", str(blob)).stdout.strip()
    assert kgit._git(repo, "cat-file", "blob", sha, text=False).stdout == blob.read_bytes()
    assert made and set(made) == {out}


def test_the_index_dirty_reads_is_made_in_the_output_directory(tmp_path, monkeypatch):
    """`dirty` reads HEAD into a fresh index of its own; that index is
    made in `git.output_dir`, which no task profile can reach, while git
    reads and compares it, and is gone after."""
    repo = tmp_path / "repo"
    kgit._git(tmp_path, "init", "-q", str(repo))
    (repo / "a").write_text("one\n")
    kgit._git(repo, "add", "a")
    kgit._git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "a")
    (repo / "a").write_text("two\n")
    seen = []
    real = kgit.out

    def spy(workspace, *args, extra_env=None, **kw):
        if extra_env and "GIT_INDEX_FILE" in extra_env:
            index = Path(extra_env["GIT_INDEX_FILE"])
            seen.append((index, index.parent.exists()))
        return real(workspace, *args, extra_env=extra_env, **kw)

    monkeypatch.setattr(kgit, "out", spy)
    assert kgit.dirty(repo) == ["M a"]
    assert len(seen) == 2
    for index, existed in seen:
        assert existed and index.parent.parent == kgit.output_dir()
        assert index.parent.name.startswith("valor-index-")
        assert not index.parent.exists()


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
    port = kws.choose_port(ports_span((5540, 5579)), set())
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
    port = kws.choose_port(ports_span((5540, 5579)), set())
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


def test_a_turn_file_is_read_whole_and_a_verdict_of_any_size_is_filed_away(tmp_path):
    repo = tmp_path / "docs-abc" / "repo"
    valor = repo / ".valor"
    valor.mkdir(parents=True)
    big = {"verdict": "sound", "findings": [{"kind": "x", "text": "y" * 400_000}]}
    (valor / "verdict.json").write_text(json.dumps(big))
    fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY)
    try:
        body, why = kws.read_turn_file(fd, ".valor/verdict.json")
    finally:
        os.close(fd)
    assert why is None and json.loads(body) == big
    assert kws.read_verdict(tmp_path, "docs-abc", "t1") == (big, None)
    assert (valor / "handled" / "t1" / "verdict.json").exists() and not (valor / "verdict.json").exists()


@pytest.mark.parametrize("mode", [0o000, 0o100, 0o300])
@pytest.mark.parametrize("where", ["inside", "top"])
def test_fresh_dir_removes_a_directory_with_no_read_bit(tmp_path, mode, where):
    check = tmp_path / "checks" / "test-head-abc"
    stuck = check / "repo" / "x" if where == "inside" else check
    (stuck / "y").mkdir(parents=True)
    (stuck / "y" / "f").write_text("left behind")
    (stuck / "y").chmod(mode)
    stuck.chmod(mode)
    assert kws.fresh_dir(check) == check
    assert sorted(p.name for p in check.iterdir()) == ["claude", "pi", "tmp"]


def test_fresh_dir_removes_a_read_only_tree_a_run_left(tmp_path):
    check = tmp_path / "checks" / "test-head-abc"
    stuck = check / "repo" / "x" / "y"
    stuck.mkdir(parents=True)
    (stuck / "f").write_text("left behind")
    stuck.chmod(0o555)
    (check / "repo" / "x").chmod(0o555)
    assert kws.fresh_dir(check) == check
    assert sorted(p.name for p in check.iterdir()) == ["claude", "pi", "tmp"]


def _chain(top: Path, depth: int, name: str, mode: int, bottom: int | None = None) -> None:
    """`depth` nested `name` directories under `top`, each left at `mode`
    (the deepest at `bottom` when given) with a file and a link to
    `top`'s parent at the bottom, built through descriptors since the
    path outgrows PATH_MAX."""
    top.mkdir(parents=True)
    cur = os.open(top, os.O_RDONLY | os.O_DIRECTORY)
    for i in range(depth):
        os.mkdir(name, dir_fd=cur)
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY, dir_fd=cur)
        os.fchmod(cur, mode)
        os.close(cur)
        cur = child
    fd = os.open("f", os.O_WRONLY | os.O_CREAT, 0o600, dir_fd=cur)
    os.close(fd)
    os.symlink(str(top.parent), "out", dir_fd=cur)
    os.fchmod(cur, mode if bottom is None else bottom)
    os.close(cur)


def test_rmtree_removes_a_deep_chain_of_directories_with_no_mode_bits(tmp_path):
    """600 nested 0000 directories (300 overflowed the recursion that
    removed them one level at a time)."""
    (tmp_path / "keep").write_text("outside")
    _chain(tmp_path / "top", 600, "a", 0o000)
    kws.rmtree(tmp_path / "top")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["keep"]
    assert (tmp_path / "keep").read_text() == "outside"


@pytest.mark.parametrize("bottom", [0o000, 0o500, 0o300, 0o444])
def test_rmtree_removes_a_locked_directory_past_path_max(tmp_path, bottom):
    """400 readable levels, 1600 bytes of path, then one directory a
    path-based chmod could not reach or that could be listed but not
    searched."""
    (tmp_path / "keep").write_text("outside")
    _chain(tmp_path / "top", 400, "abc", 0o755, bottom=bottom)
    kws.rmtree(tmp_path / "top")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["keep"]


def test_rmtree_removes_a_directory_that_can_be_listed_but_not_searched(tmp_path):
    check = tmp_path / "checks" / "test-head-abc"
    (check / "repo" / "a" / "b").mkdir(parents=True)
    (check / "repo" / "a" / "b" / "f").write_text("x")
    (check / "repo" / "a").chmod(0o444)
    assert kws.fresh_dir(check) == check
    assert sorted(p.name for p in check.iterdir()) == ["claude", "pi", "tmp"]


def test_rmtree_unlinks_a_link_and_never_touches_its_target(tmp_path):
    target = tmp_path / "target"
    (target / "d").mkdir(parents=True)
    target.chmod(0o500)
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "to_target").symlink_to(target)
    (tree / "sub").mkdir()
    (tree / "sub" / "to_target").symlink_to(target / "d")
    kws.rmtree(tree)
    assert not tree.exists()
    assert (target / "d").is_dir() and target.stat().st_mode & 0o777 == 0o500
    target.chmod(0o700)
    link = tmp_path / "link"
    link.symlink_to(target)
    kws.rmtree(link)
    assert not link.is_symlink() and (target / "d").is_dir()


def _locked_by_a_turn(tmp_path: Path, script: str) -> Path:
    """A tree under `tmp_path/tree` that `script` (sh, run in it) locked
    under the real turn profile, the checkout its one writable place."""
    tree = tmp_path / "tree"
    (tree / "a" / "b").mkdir(parents=True)
    (tree / "a" / "f").write_text("x")
    (tree / "a" / "b" / "f").write_text("x")
    profile = tmp_path / "turn.sb"
    profile.write_text(kws.profile(rw=[tree], work=tmp_path))
    subprocess.run(
        ["/usr/bin/sandbox-exec", "-D", "GATEWAY_PORT=1", "-D", "VALOR_TURN=probe", "-f", str(profile),
         "/bin/sh", "-ec", script],
        cwd=tree, capture_output=True, text=True, check=True, env={"PATH": "/usr/bin:/bin"},
    )  # fmt: skip
    return tree


@pytest.mark.parametrize(
    "script",
    [
        "chflags uchg a/f",
        "chflags uchg a/b/f a/b a",
        "chflags uappnd a/b a",
        "chflags -h uchg a/link",
        'chmod +a "everyone deny delete" a/f a/b/f',
        'chmod +a "everyone deny list,search,delete_child" a/b a',
        'chmod +a "everyone deny delete" a/b a',
        'chmod +a "everyone deny delete,writesecurity,writeattr" a/b/f; chflags uchg a/b/f',
    ],
    ids=[
        "uchg-file",
        "uchg-dirs",
        "uappnd-dirs",
        "uchg-link",
        "acl-file",
        "acl-list",
        "acl-dir",
        "acl-and-flag",
    ],
)
def test_rmtree_clears_the_flags_and_acls_a_turn_sets(tmp_path, script):
    """A turn may set `uchg`, `uappnd` or an ACL deny entry on anything in
    its checkout; a mode alone moved none of them, and every rerun of a
    check named by its sha met the same tree."""
    (tmp_path / "keep").write_text("outside")
    tree = _locked_by_a_turn(tmp_path, f"ln -s {tmp_path / 'keep'} a/link; {script}")
    # `rm -rf` takes what nothing locks and stops at the rest.
    assert subprocess.run(["/bin/rm", "-rf", str(tree)], capture_output=True, check=False).returncode != 0
    kws.rmtree(tree)
    assert not tree.exists()
    assert (tmp_path / "keep").read_text() == "outside"
    assert os.stat(tmp_path / "keep").st_flags == 0


# chflags(2) is syscall 34; chflags(1) stats first, which such an ACL refuses.
_CHFLAGS = """/usr/bin/perl -e 'for (@ARGV) { my ($p, $v) = split /=/; syscall(34, $p, $v + 0) == 0 or die "$p: $!" }' """


@pytest.mark.parametrize(
    ("script", "hidden"),
    [
        ('chmod +a "everyone deny readattr" a/f', "a/f"),
        ('chmod +a "everyone deny readsecurity" a/f', "a/f"),
        ('chmod +a "everyone deny readattr" a/b/f a/b', "a/b"),
        ('chmod +a "everyone deny readsecurity" a/b', "a/b"),
        ('chmod +a "everyone deny readattr" .', "."),
        ('chmod +a "everyone deny readsecurity" .', "."),
        ('chmod +a "everyone deny readattr,readsecurity" a/f a/b .; ' + _CHFLAGS + "a/f=2 a/b=6 .=2", "."),
        ('mkdir .rm1 .rm2; chmod +a "everyone deny readattr" .rm1 .rm2 a/b', ".rm1"),
    ],
    ids=[
        "readattr-file",
        "readsecurity-file",
        "readattr-dir",
        "readsecurity-dir",
        "readattr-root",
        "readsecurity-root",
        "acl-then-flags",
        "readattr-moved-name",
    ],
)
def test_rmtree_clears_an_acl_that_hides_the_entry(tmp_path, script, hidden):
    """An ACL denying `readattr` or `readsecurity` makes an entry's `lstat`
    fail even for its owner, and a turn may set one on anything in its
    checkout, the checkout itself included, then flags it could no longer
    read; the check directory is named by its sha, so every rerun met the
    same tree."""
    (tmp_path / "keep").write_text("outside")
    tree = _locked_by_a_turn(tmp_path, f"ln -s {tmp_path / 'keep'} a/link; {script}")
    with pytest.raises(PermissionError):
        os.lstat(tree / hidden)
    kws.rmtree(tree)
    assert not tree.exists()
    assert (tmp_path / "keep").read_text() == "outside"
    assert os.stat(tmp_path / "keep").st_flags == 0


def test_tree_has_valor_reads_a_tree_name_that_is_not_utf8(tmp_path):
    """A tree entry's name is any bytes but NUL and `/`; `git mktree`
    writes one that is no UTF-8, and the read neither crashes nor misses a
    `.valor` beside it."""
    repo = scripted.toy_repo(tmp_path)
    blob = scripted.git(repo, "rev-parse", "HEAD:README.md")

    def commit_of(*names: bytes) -> str:
        listing = b"".join(b"100644 blob " + blob.encode() + b"\t" + n + b"\0" for n in names)
        tree = (
            subprocess.run(
                ["git", "-C", str(repo), "mktree", "-z"], input=listing, capture_output=True, check=True
            )
            .stdout.decode()
            .strip()
        )
        return scripted.git(
            repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit-tree", tree, "-m", "x"
        )

    plain, marked = commit_of(b"\xff\xfename"), commit_of(b"\xff\xfename", b".Valor")
    for trusted in (True, False):
        assert not kws.tree_has_valor(repo, plain, trusted=trusted)
        assert kws.tree_has_valor(repo, marked, trusted=trusted)


# -- provisioning has no time limit; an interrupt ends it ---------------------------------


def _running(marker: str) -> list[str]:
    """Commands of this user's processes whose command line carries `marker`."""
    listing = subprocess.run(["/bin/ps", "-A", "-o", "command="], capture_output=True, text=True, check=True)
    return [line for line in listing.stdout.splitlines() if marker in line and "/bin/ps" not in line]


def _in_thread(held, work):
    """`work()` on a thread that carries the current context (and so the
    watch); returns the thread and a dict that gets its result or error."""
    import contextvars
    import threading

    out: dict = {}
    ctx = contextvars.copy_context()

    def body():
        try:
            out["value"] = ctx.run(work)
        except BaseException as exc:  # noqa: BLE001  handed back to the test
            out["error"] = exc

    thread = threading.Thread(target=body)
    thread.start()
    return thread, out


def _wait_for(check, seconds: float = 20) -> None:
    import time

    ends = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < ends, "timed out"
        time.sleep(0.05)


def test_no_provisioning_git_call_carries_a_time_limit(tmp_path, monkeypatch):
    """Inside a watch or outside one, every git call provisioning makes
    waits for git with no time limit; a stop ends a hung one."""
    from core import git

    scripted.toy_repo(tmp_path)
    limits = []
    real = subprocess.Popen.wait

    def spy(self, timeout=None):
        if self.args[0] == git.binary():
            limits.append((self.args[self.args.index("-C") + 2 + len(git.PINNED)], timeout))
        return real(self, timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", spy)
    _task, made = provision(tmp_path)
    with git.interruptible():
        _task, made = provision(tmp_path / "again")
    assert Path(made.workspace).is_dir() and len(limits) > 10
    assert all(t is None for _cmd, t in limits) and ("clone", None) in limits


def test_interrupting_start_kills_provisioning_git(tmp_path):
    from core import git

    repo = scripted.toy_repo(tmp_path)
    marker = f"sleep 61.{uuid.uuid4().int % 10**6}"
    with git.interruptible() as held:
        thread, out = _in_thread(held, lambda: git.trusted(repo, "-c", f"alias.hang=!{marker}", "hang"))
    _wait_for(lambda: _running(marker))
    held.interrupt()
    thread.join(20)
    assert not thread.is_alive() and isinstance(out.get("error"), git.Interrupted)
    assert _running(marker) == []
    with pytest.raises(git.Interrupted):  # nothing starts once interrupted
        held.start(["/usr/bin/true"])


def test_a_call_marked_uninterrupted_runs_after_an_interrupt(tmp_path):
    from core import git

    repo = scripted.toy_repo(tmp_path)
    with git.interruptible() as held:
        held.interrupt()
        with pytest.raises(git.Interrupted):
            git.trusted(repo, "rev-parse", "HEAD")
        with git.uninterrupted():
            assert len(git.trusted(repo, "rev-parse", "HEAD")) == 40


def test_interrupting_start_kills_a_setup_command(tmp_path):
    from core import git

    scripted.toy_repo(tmp_path)
    marker = f"sleep 62.{uuid.uuid4().int % 10**6}"
    with git.interruptible() as held:
        thread, out = _in_thread(held, lambda: provision(tmp_path, setup=[f"{marker} & {marker}"]))
    _wait_for(lambda: len(_running(marker)) >= 2, 60)
    held.interrupt()
    thread.join(30)
    assert not thread.is_alive()
    assert isinstance(out.get("error"), kws.Refused) and "interrupted" in str(out["error"])
    assert _running(marker) == []


def test_a_setup_command_whose_child_holds_its_output_still_ends(tmp_path):
    import time

    marker = f"sleep 63.{uuid.uuid4().int % 10**6}"
    started = time.monotonic()
    _task, made = provision(tmp_path, setup=[f"({marker} &); echo done"])
    assert time.monotonic() - started < 30
    result = made.project["setup_result"]
    assert result["ok"] is True and Path(result["commands"][0]["output"]).read_text() == "done\n"
    assert _running(marker) == []  # reaped by its mark


def test_a_setup_commands_whole_output_is_in_its_log_which_no_turn_can_write(tmp_path):
    _task, made = provision(
        tmp_path,
        setup=["head -c 100000 /dev/zero | tr '\\0' x; echo; echo end >&2", "echo forged > ../setup/0.log"],
    )
    lay = kws.Layout(Path(made.mirror).parent)
    first, second = made.project["setup_result"]["commands"]
    assert first == {"command": first["command"], "exit": 0, "output": str(lay.setup / "0.log")}
    assert (lay.setup / "0.log").read_text() == "x" * 100000 + "\nend\n"  # stdout and stderr, whole
    assert second["exit"] != 0 and second["output"] == str(
        lay.setup / "1.log"
    )  # the profile refuses the write
    assert not hasattr(kws, "SETUP_TAIL")


def test_a_node_setup_command_starts_under_the_turn_profile_and_its_log_holds_its_output(tmp_path):
    from core.settings import settings

    if not Path(settings.node).exists():
        pytest.skip("no node at the node setting")
    script = "process.stdout.write('o'.repeat(200000)); console.error('end')"
    _task, made = provision(tmp_path, setup=[f'{settings.node} -e "{script}"'])
    (only,) = made.project["setup_result"]["commands"]
    assert only["exit"] == 0
    text = Path(only["output"]).read_text()  # one pipe for both: node may interleave them
    assert text.count("o") == 200000 and text.replace("o", "") == "end\n"


def _service_layout(tmp_path) -> kws.Layout:
    lay = kws.Layout(tmp_path / "task")
    lay.profiles.mkdir(parents=True)
    (lay.profiles / "service.sb").write_text("(version 1)\n(allow default)\n")
    return lay


def test_a_service_program_has_no_time_limit_and_an_interrupt_ends_it(tmp_path):
    import inspect
    import time

    from core import git

    assert "timeout" not in inspect.signature(kws._service_run).parameters
    lay = _service_layout(tmp_path)
    marker = f"64.{uuid.uuid4().int % 10**6}"
    with git.interruptible() as held:
        thread, out = _in_thread(held, lambda: kws._service_run(lay, "t", "/bin/sleep", marker))
    _wait_for(lambda: _running(marker), 20)
    time.sleep(1.5)
    assert thread.is_alive()
    held.interrupt()
    thread.join(30)
    assert isinstance(out.get("error"), git.Interrupted) and _running(marker) == []


def test_a_service_programs_whole_stderr_is_in_its_refusal(tmp_path, monkeypatch):
    lay = _service_layout(tmp_path)
    long = "e" * 5000
    monkeypatch.setattr(
        kws, "_service_run", lambda lay, task_id, *argv: subprocess.CompletedProcess(argv, 1, "", long + "\n")
    )
    with pytest.raises(kws.Refused) as refused:
        kws._init_postgres(lay, "t", spec(tmp_path), 5540)
    assert str(refused.value) == f"initdb: {long}"


def test_a_failing_bounded_command_returns_its_whole_stderr(tmp_path):
    code, err = kws.bounded(
        ["/bin/bash", "-c", "head -c 5000 /dev/zero | tr '\\0' e >&2; exit 4"], cwd=tmp_path, env={},
        max_bytes=10**6, max_footprint=10**9,
    )  # fmt: skip
    assert code == 4 and err == "e" * 5000


def test_a_stop_of_the_task_interrupts_its_services_starting(dsn, tmp_path, monkeypatch):
    lay = _service_layout(tmp_path)
    marker = f"65.{uuid.uuid4().int % 10**6}"
    monkeypatch.setattr(
        kws, "start_services", lambda task_id, lay, *a: kws._service_run(lay, task_id, "/bin/sleep", marker)
    )

    async def go():
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="test"))
        services = router._Services(task, dsn, ["postgres"], {"postgres": 5540}, lay)
        up = asyncio.ensure_future(services.up())
        while not _running(marker):
            await asyncio.sleep(0.05)
        await asyncio.sleep(1)
        assert not up.done()
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, task, reason="test")
        return await up, services

    why, services = run(go())
    assert why == "the task was stopped while its services started" and _running(marker) == []
    assert services.started is True  # so `down` stops whatever had started


def test_a_cancelled_run_interrupts_its_services_starting(dsn, tmp_path, monkeypatch):
    lay = _service_layout(tmp_path)
    marker = f"66.{uuid.uuid4().int % 10**6}"
    monkeypatch.setattr(
        kws, "start_services", lambda task_id, lay, *a: kws._service_run(lay, task_id, "/bin/sleep", marker)
    )

    async def go():
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="test"))
        up = asyncio.ensure_future(router._Services(task, dsn, ["postgres"], {}, lay).up())
        while not _running(marker):
            await asyncio.sleep(0.05)
        up.cancel()
        with pytest.raises(asyncio.CancelledError):
            await up

    run(go())
    assert _running(marker) == []


def test_a_run_whose_services_are_refused_returns_the_reason(dsn, monkeypatch):
    def refused(*args):
        raise kws.Refused("the task's Postgres did not start")

    monkeypatch.setattr(kws, "start_services", refused)
    services = router._Services("t", dsn, ["postgres"], {"postgres": 5540}, kws.Layout(Path("/nonexistent")))
    assert run(services.up()) == "the task's Postgres did not start"


def test_a_63_character_role_is_accepted_and_created(tmp_path):
    role = "r" * 63
    task, made = provision(tmp_path, services=["postgres"], roles=[role])
    lay = kws.Layout(Path(made.mirror).parent)
    port = made.project["ports"]["postgres"]
    passwords = {
        line.split(":")[3]: line.split(":")[4] for line in (lay.home / "pgpass").read_text().splitlines()
    }
    kws.start_services(task, lay, ["postgres"], {"postgres": port})
    try:
        with psycopg.connect(host="127.0.0.1", port=port, dbname="app", user=role,
                             password=passwords[role]) as conn:  # fmt: skip
            assert conn.execute("SELECT current_user").fetchone()[0] == role
    finally:
        kws.stop_services(task, lay)


def test_a_64_character_role_is_refused():
    with pytest.raises(kws.Refused):
        kws.Spec.from_dict(
            {
                "name": "x",
                "repo": "r",
                "kind": "plain",
                "suite": "true",
                "services": ["postgres"],
                "roles": ["r" * 64],
            }
        )
