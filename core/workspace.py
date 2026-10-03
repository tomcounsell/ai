"""Kernel workspaces: one directory per task, provisioned before the task
starts, from a project spec the kernel reads (never the candidate).

    <work_dir>/
      cache/<name>.git     bare clone per repository, fetched by the kernel
      bin/                 shared tools (uv), read-only to every sandbox
      <task_id>/
        repo/              the builder's clone: the working session's cwd
        origin.git/        local bare origin: push_branch's target (and, until
                           1.4d, the merge's); no turn writes it
        kernel.git/        the kernel mirror: the base, plan commits,
                           candidates, docs heads; no turn writes it
        home/              gitconfig, empty gh config, pgpass, profiles/*.sb
        cache/             the builder's uv and npm caches
        state/work/        the working session's own TMPDIR and Claude Code
                           config directory (so its session files)
        checks/<name>/     a fresh session's repo/, tmp/, claude/
        pg/data            the task's Postgres cluster (TCP on loopback only)
        redis/             the task's redis-server, when the project asks
        ports.json         the task's service ports, written when they are
                           chosen, so no other start takes them

Every program the kernel starts here that is not root's alone runs inside a
sandbox-exec profile written by this module: the working session's
(`turn.sb`), a fresh session's (`<stage>-<key>.sb`), and the services'
(`service.sb`). Profiles start from `(allow default)` and deny before they
allow. Each denies the whole work directory and allows back only its own
paths, so a fresh session cannot read the builder's clone, its TMPDIR, its
Claude Code state, or its transcripts (what the builder writes elsewhere in
the user's home, a cache for one, is outside this; see harnesses.md, Known
openings); each denies writes to the user's startup places (launch
agents, shell rc files, `~/.local/bin`, Claude Code's install, git's global
config, Homebrew's prefix), which narrows, and does not close, the opening
that a turn leaves a program for a later unsandboxed process of the user.

The services (`initdb`, `pg_ctl`, `postgres`, `redis-server`) are Homebrew's,
in the user's own prefix, so they only ever run under `service.sb`, marked by
the mach name `valor.service.<task_id>`, which is how they are found and
stopped, also after the kernel died.

The kernel mirror is fed from the builder's clone by `fetch_into_mirror`:
the clone's config checked first, a gitfile, alternates, and shallow clones
refused, a tree holding `.valor` refused,
the sending side run inside the turn's own sandbox, the receiving side with
fsck, one pack file under a file-size limit, and a footprint watchdog.
"""

import contextlib
import ctypes
import errno
import functools
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import socket
import stat
import struct
import subprocess
import time
import tomllib
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core import binaries, git, runs
from core.settings import settings

KINDS = ("python-uv", "django", "node", "plain")
SERVICES = ("postgres", "redis")
DEV_PORTS = range(8000, 8010)
HOME = Path.home()
GITCONFIG = "[user]\n\tname = Valor Engels\n\temail = valor@yuda.me\n[init]\n\tdefaultBranch = main\n"
# Read and write denied to every workspace sandbox, under the user's home.
HOME_DENIED = (
    "src",
    "work-vault",
    "Desktop",
    "Documents",
    "Downloads",
    "Dropbox",
    "Library/CloudStorage",
    "Library/Mobile Documents",
    "Library/Mail",
    "Library/Messages",
    ".ssh",
    ".config/gh",
)
# Writes denied to every workspace sandbox: where a later unsandboxed
# process of the user would run what a turn left.
HOME_WRITE_DENIED = (
    "Library/LaunchAgents",
    ".local/bin",
    ".local/share/claude",
    ".claude",
    ".config/git",
    "Library/Caches/ms-playwright",
)
HOME_WRITE_DENIED_FILES = (
    ".zshrc",
    ".zprofile",
    ".zshenv",
    ".zlogin",
    ".bash_profile",
    ".bashrc",
    ".profile",
    ".claude.json",
    ".gitconfig",
)
SYSTEM_WRITE_DENIED = ("/opt/homebrew",)


class Refused(ValueError):
    """A project or a provisioning step the kernel will not take."""


# -- project specs -----------------------------------------------------------------


@dataclass(frozen=True)
class Spec:
    """A project, read once at start and copied into the Brief, so editing
    the file never changes a running task. `suite` names the command the
    kernel runs to decide red or pass; a candidate never chooses it."""

    name: str
    repo: str
    kind: str
    suite: str
    branch: str | None = None
    services: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    setup: tuple[str, ...] = ()
    lint: str | None = None
    merge_url: str | None = None
    target_branch: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    max_output_tokens: int | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Spec:
        for key in ("name", "repo", "kind", "suite"):
            if not isinstance(raw.get(key), str) or not raw[key]:
                raise Refused(f"a project spec names {key!r}")
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(raw) - known)
        if unknown:
            raise Refused(f"unknown project spec keys: {unknown}")
        if raw["kind"] not in KINDS:
            raise Refused(f"kind {raw['kind']!r} is not one of {', '.join(KINDS)}")
        services = tuple(raw.get("services") or ())
        bad = sorted(set(services) - set(SERVICES))
        if bad:
            raise Refused(f"unknown services {bad}; known: {', '.join(SERVICES)}")
        roles = tuple(raw.get("roles") or ())
        for r in roles:
            if not re.fullmatch(r"[a-z_][a-z0-9_]{0,40}", r) or r in ("app", "postgres"):
                raise Refused(f"role name {r!r} is not allowed")
        if roles and "postgres" not in services:
            raise Refused("roles need the postgres service")
        cap = raw.get("max_output_tokens")
        if cap is not None and (not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0):
            raise Refused("max_output_tokens is a positive integer")
        env = raw.get("env") or {}
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            raise Refused("a project's env maps names to strings")
        return cls(
            name=raw["name"],
            repo=raw["repo"],
            kind=raw["kind"],
            suite=raw["suite"],
            branch=raw.get("branch"),
            services=services,
            roles=roles,
            setup=tuple(raw.get("setup") or ()),
            lint=raw.get("lint"),
            merge_url=raw.get("merge_url"),
            target_branch=raw.get("target_branch"),
            env=dict(env),
            max_output_tokens=cap,
        )

    @classmethod
    def load(cls, name_or_path: str) -> Spec:
        path = Path(name_or_path)
        if not path.suffix:
            path = Path(settings.projects_dir) / f"{name_or_path}.toml"
        try:
            raw = tomllib.loads(path.read_text())
        except FileNotFoundError:
            raise Refused(f"no project spec at {path}") from None
        except tomllib.TOMLDecodeError as exc:
            raise Refused(f"{path}: {exc}") from None
        return cls.from_dict(raw)


# -- paths --------------------------------------------------------------------------


def work_dir() -> Path:
    return Path(settings.work_dir).expanduser()


@dataclass(frozen=True)
class Layout:
    root: Path

    @property
    def repo(self) -> Path:
        return self.root / "repo"

    @property
    def origin(self) -> Path:
        return self.root / "origin.git"

    @property
    def mirror(self) -> Path:
        return self.root / "kernel.git"

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def profiles(self) -> Path:
        return self.home / "profiles"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def work_state(self) -> Path:
        return self.root / "state" / "work"

    @property
    def checks(self) -> Path:
        return self.root / "checks"

    @property
    def pg(self) -> Path:
        return self.root / "pg"

    @property
    def redis(self) -> Path:
        return self.root / "redis"


def layout(task_id: str, base: Path | None = None) -> Layout:
    if not re.fullmatch(r"[0-9a-f]{6,64}", task_id):
        raise Refused(f"{task_id!r} is not a task id")
    return Layout((base or work_dir()) / task_id)


def kernel_paths() -> list[Path]:
    """What no workspace sandbox may read or write: the kernel key
    directory, the machine cluster's data directory, the backup disk."""
    return [Path(settings.pg_passfile).parent, Path(settings.pg_data_dir), Path(settings.backup_dir)]


# -- sandbox profiles -----------------------------------------------------------------


def _paths(kind: str, paths) -> list[str]:
    return [f'    ({kind} "{p}")' for p in paths]


def profile(
    *,
    rw: list[Path],
    ro: list[Path] = (),
    ports: list[int] = (),
    work: Path | None = None,
    home: Path = HOME,
    fresh: bool = False,
    kernel: list[Path] | None = None,
    service: str | None = None,
    bind_ports: list[int] = (),
) -> str:
    """A sandbox-exec profile. `rw` and `ro` are allowed back after the
    denies; `work` (the work directory) is denied as a whole first. A
    `fresh` profile also denies `/private/tmp`, `/private/var/folders`, and
    the user's Claude Code state, since a fresh session has its own. A
    `service` profile is marked by the mach name `valor.service.<service>`
    and may bind `bind_ports` only; a turn's profile is marked by
    `valor.turn.<VALOR_TURN>`, may bind the dev ports, and reaches the
    gateway (`GATEWAY_PORT`) and `ports` on loopback."""
    denied = [home / d for d in HOME_DENIED]
    if work is not None:
        denied.append(work)
    lines = [
        "(version 1)",
        "(allow default)",
        "(deny file-read* file-write*",
        *_paths("subpath", denied),
        f'    (subpath "{home / ".claude" / "projects"}")',
        f'    (literal "{home / ".claude" / "history.jsonl"}"))',
    ]
    if fresh:
        lines += [
            "(deny file-read* file-write*",
            '    (subpath "/private/tmp")',
            '    (subpath "/private/var/tmp")',
            '    (subpath "/private/var/folders")',
            f'    (subpath "{home / ".claude"}")',
            f'    (literal "{home / ".claude.json"}"))',
        ]
    lines += [
        "(deny file-write*",
        *_paths("subpath", [home / d for d in HOME_WRITE_DENIED]),
        *_paths("literal", [home / f for f in HOME_WRITE_DENIED_FILES]),
        *_paths("subpath", SYSTEM_WRITE_DENIED),
        ")",
        "(allow file-read* file-write*",
        *_paths("subpath", rw),
        ")",
    ]
    if ro:
        lines += ["(allow file-read*", *_paths("subpath", ro), ")"]
    ancestors = sorted({str(a) for p in [*rw, *ro] for a in Path(p).parents})
    lines += ["(allow file-read-metadata", *_paths("literal", ancestors), ")"]
    lines += [
        "(deny file-read* file-write*",
        *_paths("subpath", kernel if kernel is not None else kernel_paths()),
        ")",
        '(deny process-exec (regex #"/git-credential-osxkeychain$"))',
    ]
    if service:
        lines += [
            f'(deny mach-lookup (global-name "valor.service.{service}"))',
            "(deny network-bind network-inbound)",
            "(allow network-bind network-inbound",
            *[f'    (local ip "localhost:{p}")' for p in bind_ports],
            *[f'    (local unix-socket (subpath "{p}"))' for p in rw],
            ")",
            "(deny network-outbound",
            '    (remote ip "*:*"))',
            "(allow network-outbound",
            *[f'    (remote unix-socket (subpath "{p}"))' for p in rw],
            ")",
        ]
        return "\n".join(lines) + "\n"
    lines += [
        '(deny mach-lookup (global-name (string-append "valor.turn." (param "VALOR_TURN"))))',
        "(deny network-bind network-inbound)",
        "(allow network-bind network-inbound",
        *(f'    (local ip "localhost:{p}")' for p in DEV_PORTS),
        *[f'    (local unix-socket (subpath "{p}"))' for p in rw],
        ")",
        "(deny network-outbound",
        '    (remote ip "localhost:*")',
        f'    (remote ip "localhost:{settings.pgport}")',
        f'    (remote unix-socket (path-literal "{settings.pg_socket_real}"))',
        f'    (remote unix-socket (path-literal "{settings.pg_socket}")))',
        "(allow network-outbound",
        '    (remote ip (string-append "localhost:" (param "GATEWAY_PORT")))',
        *(f'    (remote ip "localhost:{p}")' for p in [*DEV_PORTS, *ports]),
    ]
    lines[-1] += ")"
    return "\n".join(lines) + "\n"


def turn_profile(
    lay: Layout, ports: list[int], *, home: Path = HOME, kernel: list[Path] | None = None
) -> str:
    """The working session's (and provisioning setup's) profile: its clone,
    its caches, its own state; its task's home and origin read-only."""
    return profile(
        rw=[lay.repo, lay.cache, lay.work_state],
        ro=[lay.home, lay.origin, lay.root.parent / "bin"],
        ports=ports,
        work=lay.root.parent,
        home=home,
        kernel=kernel,
    )


def check_profile(
    lay: Layout, check_dir: Path, ports: list[int], *, home: Path = HOME, kernel: list[Path] | None = None
) -> str:
    """A fresh session's profile: its own checkout, tmp, and Claude Code
    config, and nothing else of the work directory."""
    return profile(
        rw=[check_dir],
        ro=[lay.root.parent / "bin"],
        ports=ports,
        work=lay.root.parent,
        home=home,
        fresh=True,
        kernel=kernel,
    )


def service_profile(
    lay: Layout,
    task_id: str,
    ports: list[int],
    *,
    work: Path,
    home: Path = HOME,
    kernel: list[Path] | None = None,
) -> str:
    """The services' profile: their data directories, the whole work
    directory `work` denied first. `work` is passed, never derived from
    `lay`: a check's service layout sits under the task's `checks/`."""
    return profile(
        rw=[lay.pg, lay.redis],
        work=work,
        home=home,
        kernel=kernel,
        service=task_id,
        bind_ports=ports,
    )


def sandboxed(profile_path: Path, mark: str, *argv: str) -> list[str]:
    """An argv run under `profile_path`, with the turn mark `mark` (a turn
    or a provisioning step). It has no gateway: the profile's gateway
    parameter is given port 1, where nothing listens."""
    return [
        binaries.require(binaries.SANDBOX_EXEC),
        "-D",
        "GATEWAY_PORT=1",
        "-D",
        f"VALOR_TURN={mark}",
        "-f",
        str(profile_path),
        *argv,
    ]


# -- ports ----------------------------------------------------------------------------


def _bindable(port: int) -> bool:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


PORTS_FILE = "ports.json"


def reserve(task_id: str, ports: dict[str, int], work: Path | None = None) -> Layout:
    """Make the task's directory and record its ports in it, so a start that
    chooses ports after this one, before this task's row exists, sees them
    taken. Called under the `workspace:ports` lock."""
    lay = layout(task_id, work)
    lay.root.mkdir(parents=True, exist_ok=False)
    (lay.root / PORTS_FILE).write_text(json.dumps(ports))
    return lay


def reserved_ports(work: Path | None = None) -> set[int]:
    """The ports every task directory under the work directory recorded,
    finished, in progress, or left by a provisioning that died."""
    out: set[int] = set()
    base = work or work_dir()
    if not base.is_dir():
        return out
    for f in base.glob(f"*/{PORTS_FILE}"):
        try:
            out.update(int(v) for v in json.loads(f.read_text()).values())
        except ValueError, OSError, AttributeError:
            continue
    return out


async def taken_ports(conn, work: Path | None = None) -> set[int]:
    """Every service port named by a task whose workspace has not been
    removed, and every port a task directory has reserved."""
    rows = await (
        await conn.execute(
            "SELECT d.body->'project'->'ports' FROM documents d WHERE d.kind = 'task' "
            "AND d.body->'project'->'ports' IS NOT NULL AND NOT EXISTS ("
            "  SELECT 1 FROM events e WHERE e.task_id = d.id AND e.type = 'workspace.removed')"
        )
    ).fetchall()
    out = reserved_ports(work)
    for (ports,) in rows:
        out.update(int(v) for v in (ports or {}).values())
    return out


def choose_port(span: tuple[int, int], taken: set[int]) -> int:
    for port in range(span[0], span[1] + 1):
        if port not in taken and _bindable(port):
            return port
    raise Refused(f"no free port in {span[0]} to {span[1]}; held by tasks' workspaces: {sorted(taken)}")


# -- provisioning -----------------------------------------------------------------------


def _cache(spec: Spec, source: str | None, work: Path) -> Path:
    """The kernel's bare clone of the repository, fetched by its trusted git:
    a local path, or a public HTTPS URL fetched anonymously. A private
    repository needs the credential, which is 1.4d's."""
    origin = source or spec.repo
    local = Path(origin).expanduser()
    url = str(local.resolve()) if local.exists() else origin
    stem = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    # Keyed by the URL's digest: two repositories with one basename never
    # share a cache.
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", stem)[:40] + "-" + hashlib.sha256(url.encode()).hexdigest()[:12]
    cache = work / "cache" / f"{name}.git"
    if not local.exists() and not url.startswith("https://"):
        raise Refused(f"{origin} is neither a local repository nor an https URL")
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        git.trusted(cache.parent, "init", "-q", "--bare", str(cache))
    try:
        git.trusted(
            cache,
            "fetch",
            "-q",
            "--no-tags",
            "--upload-pack=git-upload-pack",
            url,
            "+refs/heads/*:refs/heads/*",
        )
    except git.GitError as exc:
        if needs_credential(str(exc)):
            raise Refused(
                f"{origin} needs a credential to fetch; private repositories wait for 1.4d"
            ) from None
        raise Refused(f"fetching {origin}: {exc}") from None
    listed = git.trusted(cache, "ls-remote", "--symref", "--upload-pack=git-upload-pack", url, "HEAD")
    for line in listed.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            git.trusted(cache, "symbolic-ref", "HEAD", line[len("ref: ") : -len("\tHEAD")])
    return cache


def needs_credential(error: str) -> bool:
    """Whether git's error says the remote wants a credential, which the
    kernel has none of until 1.4d."""
    return "Authentication" in error or "could not read Username" in error


def _commit_of(cache: Path, rev: str) -> str:
    try:
        return git.trusted(cache, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    except git.GitError:
        raise Refused(f"{rev} is not a commit of the repository") from None


@dataclass
class Provisioned:
    """What `provision` made, as Brief fields."""

    workspace: str
    mirror: str
    push_url: str
    origin_url: str
    target_branch: str
    base_sha: str
    harness: dict[str, Any]
    project: dict[str, Any]

    def brief_fields(self) -> dict[str, Any]:
        return asdict(self)


def harness_env(
    lay: Layout,
    spec: Spec,
    ports: dict[str, int],
    passwords: dict[str, str],
    *,
    bin_dir: Path,
    passfile: Path,
) -> dict[str, str]:
    """The turn's environment for this project: tools (`bin_dir`, the work
    directory's `bin/`) first on PATH, the caches under `lay`, the database
    and Redis it was given, and `passfile` as the Postgres password file.
    The directories are passed, never derived from `lay`, so a check's own
    service layout gets the task's `bin/`."""
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
        "VALOR_BROWSER": settings.browser,
        "UV_CACHE_DIR": str(lay.cache / "uv"),
        "UV_PYTHON_INSTALL_DIR": str(lay.cache / "python"),
        "npm_config_cache": str(lay.cache / "npm"),
    }
    if "postgres" in spec.services:
        port = ports["postgres"]
        env.update(
            {
                "PGHOST": "127.0.0.1",
                "PGPORT": str(port),
                "PGUSER": "app",
                "PGDATABASE": "app",
                "PGPASSFILE": str(passfile),
                "DATABASE_URL": f"postgresql://app:{passwords['app']}@127.0.0.1:{port}/app",
                "TEST_DB_HOST": "127.0.0.1",
                "TEST_DB_PORT": str(port),
                "TEST_DB_USER": "app",
                "TEST_DB_PASSWORD": passwords["app"],
                "TEST_DB_NAME": "test_app",
            }
        )
    if "redis" in spec.services:
        port = ports["redis"]
        env.update({"REDIS_URL": f"redis://127.0.0.1:{port}/0", "REDIS_HOST": "127.0.0.1",
                    "REDIS_PORT": str(port)})  # fmt: skip
    env.update(
        {
            k: v.replace("{port}", str(ports.get("postgres", ""))).replace("{passfile}", str(passfile))
            for k, v in spec.env.items()
        }
    )
    return env


TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
WORKSPACE_TOOLS = ("look",)


def _install_tools(bin_dir: Path) -> None:
    """Write each workspace tool into the shared `bin/`, to a temporary name
    and renamed over, so a running turn never runs a half-written script."""
    for name in WORKSPACE_TOOLS:
        tmp = bin_dir / f".{name}.{os.getpid()}.tmp"
        shutil.copyfile(TOOLS_DIR / name, tmp)
        tmp.chmod(0o755)
        tmp.replace(bin_dir / name)


def provision(task_id: str, spec: Spec, ports: dict[str, int], *, base: str | None = None,
              source: str | None = None, work: Path | None = None) -> Provisioned:  # fmt: skip
    """Build the task's workspace. Anything that fails removes what was
    made and raises `Refused`; no task row exists yet."""
    lay = layout(task_id, work)
    if lay.root.exists() and sorted(p.name for p in lay.root.iterdir()) != [PORTS_FILE]:
        raise Refused(f"{lay.root} already exists")
    if not lay.root.exists():
        reserve(task_id, ports, work)
    try:
        return _provision(lay, task_id, spec, ports, base=base, source=source)
    except BaseException as exc:
        stop_services(task_id, lay)
        shutil.rmtree(lay.root, ignore_errors=True)
        if isinstance(exc, (git.GitError, subprocess.SubprocessError, OSError)):
            raise Refused(f"provisioning failed: {exc}") from None
        raise


def _provision(lay: Layout, task_id: str, spec: Spec, ports: dict[str, int], *, base: str | None,
               source: str | None) -> Provisioned:  # fmt: skip
    cache = _cache(spec, source, lay.root.parent)
    branch = spec.branch or _remote_head(cache)
    base_sha = _commit_of(cache, base or f"refs/heads/{branch}")
    target = spec.target_branch or branch
    for d in (lay.repo.parent, lay.home / "gh", lay.profiles, lay.cache, lay.work_state / "tmp",
              lay.work_state / "claude", lay.checks):  # fmt: skip
        d.mkdir(parents=True, exist_ok=True)
    (lay.root.parent / "bin").mkdir(exist_ok=True)
    _install_tools(lay.root.parent / "bin")
    # The clone: history up to the base only, no tags, one work branch.
    ref = f"refs/heads/valor-base/{task_id}"
    git.trusted(cache, "update-ref", ref, base_sha)
    try:
        git.trusted(lay.root, "clone", "-q", "--no-tags", "--single-branch", "--branch",
                    f"valor-base/{task_id}", f"file://{cache}", str(lay.repo))  # fmt: skip
    finally:
        git.trusted(cache, "update-ref", "-d", ref)
    work_branch = f"valor/{task_id[:8]}"
    git.trusted(lay.repo, "checkout", "-q", "-b", work_branch)
    git.trusted(lay.repo, "branch", "-q", "-D", f"valor-base/{task_id}")
    git.trusted(lay.repo, "remote", "remove", "origin")
    git.trusted(lay.repo, "reflog", "expire", "--expire=now", "--all")
    git.trusted(lay.repo, "gc", "-q", "--prune=now")
    with (lay.repo / ".git" / "info" / "exclude").open("a") as f:
        f.write(".valor/\n")
    # The bare origin: the target branch at the base.
    git.trusted(lay.root, "init", "-q", "--bare", str(lay.origin))
    git.trusted(lay.origin, "symbolic-ref", "HEAD", f"refs/heads/{target}")
    git.trusted(lay.origin, "config", "core.logAllRefUpdates", "always")
    git.trusted(lay.origin, "config", "receive.denyNonFastForwards", "true")
    git.trusted(lay.repo, "remote", "add", "origin", str(lay.origin))
    git.trusted(lay.repo, "push", "-q", "--no-verify", str(lay.origin), f"{base_sha}:refs/heads/{target}")
    git.trusted(lay.repo, "fetch", "-q", "origin")
    # The kernel mirror, seeded with the base from the kernel's own cache.
    git.trusted(lay.root, "init", "-q", "--bare", str(lay.mirror))
    git.trusted(lay.mirror, "fetch", "-q", "--no-tags", f"file://{cache}", f"{base_sha}:refs/valor/base")
    # Home: identity, empty gh, the profiles.
    (lay.home / "gitconfig").write_text(GITCONFIG)
    service_ports = [ports[s] for s in spec.services]
    (lay.profiles / "turn.sb").write_text(turn_profile(lay, service_ports))
    passwords: dict[str, str] = {}
    if spec.services:
        (lay.profiles / "service.sb").write_text(
            service_profile(lay, task_id, service_ports, work=lay.root.parent)
        )
    if "postgres" in spec.services:
        passwords = _init_postgres(lay, task_id, spec, ports["postgres"])
    if "redis" in spec.services:
        lay.redis.mkdir(parents=True, exist_ok=True)
    env = harness_env(
        lay, spec, ports, passwords, bin_dir=lay.root.parent / "bin", passfile=lay.home / "pgpass"
    )
    harness = {
        "sandbox_profile": str(lay.profiles / "turn.sb"),
        "gitconfig": str(lay.home / "gitconfig"),
        "gh_config_dir": str(lay.home / "gh"),
        "tmpdir": str(lay.work_state / "tmp"),
        "claude_config_dir": str(lay.work_state / "claude"),
        "env": env,
        **({"max_output_tokens": spec.max_output_tokens} if spec.max_output_tokens else {}),
    }
    start_services(task_id, lay, spec.services, ports)
    try:
        setup = _setup(lay, spec, harness, task_id)
    finally:
        stop_services(task_id, lay)
    project = {
        "name": spec.name,
        "kind": spec.kind,
        "suite": spec.suite,
        "lint": spec.lint,
        "setup": list(spec.setup),
        "setup_result": setup,
        "services": list(spec.services),
        "roles": list(spec.roles),
        "ports": ports,
        "repo": spec.repo,
        "merge_url": spec.merge_url,
        "env": spec.env,
    }
    return Provisioned(
        workspace=str(lay.repo),
        mirror=str(lay.mirror),
        push_url=str(lay.origin),
        # Until 1.4d every merge lands on the task's own bare origin.
        origin_url=str(lay.origin),
        target_branch=target,
        base_sha=base_sha,
        harness=harness,
        project=project,
    )


def _remote_head(cache: Path) -> str:
    try:
        return git.trusted(cache, "symbolic-ref", "--short", "HEAD")
    except git.GitError:
        raise Refused("the repository names no default branch; give the spec a branch") from None


def _setup(lay: Layout, spec: Spec, harness: dict[str, Any], task_id: str) -> dict[str, Any]:
    """The spec's setup in the builder's clone; a failure is recorded, never
    fatal."""
    return run_setup(lay.repo, harness, spec.setup, f"setup-{task_id}")


def setup_command(harness: dict[str, Any], mark: str, command: str) -> tuple[list[str], dict[str, str]]:
    """One setup command's argv and environment: under the harness's
    profile, marked `mark`, with the turn's environment."""
    argv = sandboxed(Path(harness["sandbox_profile"]), mark, "/bin/bash", "-c", command)
    return argv, {**turn_environment(harness), runs.TURN_ENV: mark}


def run_setup(checkout: Path, harness: dict[str, Any], commands, mark: str) -> dict[str, Any]:
    """Each setup command once, in `checkout`, under the harness's profile,
    with the turn's environment, marked `<mark>-<n>` and reaped; it stops at
    the first failure. Returns `ok` and each command's exit and output
    tail."""
    out: list[dict[str, Any]] = []
    for command in commands:
        step = f"{mark}-{len(out)}"
        argv, env = setup_command(harness, step, command)
        try:
            ran = subprocess.run(
                argv, cwd=checkout, env=env, capture_output=True, text=True,
                timeout=settings.setup_timeout_s, check=False,
            )  # fmt: skip
            code, text = ran.returncode, ran.stdout + ran.stderr
        except subprocess.TimeoutExpired as exc:
            code, text = "timeout", str(exc)
        runs.reap(step)
        out.append({"command": command, "exit": code, "tail": text[-1500:]})
        if code != 0:
            return {"ok": False, "commands": out}
    return {"ok": True, "commands": out}


def turn_environment(harness: dict[str, Any]) -> dict[str, str]:
    """The environment a sandboxed step of the task gets: the allowlist the
    harness uses, the task's own, its TMPDIR, no credential."""
    keep = ("HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TERM")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update(harness.get("env", {}))
    if harness.get("tmpdir"):
        env["TMPDIR"] = harness["tmpdir"]
    if harness.get("gitconfig"):
        env["GIT_CONFIG_GLOBAL"] = harness["gitconfig"]
        env["GIT_CONFIG_NOSYSTEM"] = "1"
    if harness.get("gh_config_dir"):
        env["GH_CONFIG_DIR"] = harness["gh_config_dir"]
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


# -- services -----------------------------------------------------------------------------


def _pg(name: str) -> str:
    return str(Path(settings.pg_bin) / name)


def _service_run(lay: Layout, task_id: str, *argv: str, timeout: float = 120) -> subprocess.CompletedProcess:
    """A service program under the service profile."""
    full = [binaries.require(binaries.SANDBOX_EXEC), "-f", str(lay.profiles / "service.sb"), *argv]
    return subprocess.run(
        full,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={"PATH": "/usr/bin:/bin", "HOME": str(lay.pg), "LANG": "C", "LC_ALL": "C"},
    )


def _init_postgres(lay: Layout, task_id: str, spec: Spec, port: int) -> dict[str, str]:
    """A cluster of the task's own with password auth on every login. The
    superuser password lives in a file only until the roles exist, then is
    removed from the role and the file deleted."""
    data = lay.pg / "data"
    lay.pg.mkdir(parents=True, exist_ok=True)
    super_pw = secrets.token_urlsafe(24)
    pwfile = lay.pg / "init.pw"
    pwfile.write_text(super_pw + "\n")
    pwfile.chmod(0o600)
    try:
        done = _service_run(
            lay, task_id, _pg("initdb"), "-D", str(data), "-U", "postgres", "--auth=scram-sha-256",
            f"--pwfile={pwfile}", "-E", "UTF8", "--locale=C",
        )  # fmt: skip
        if done.returncode != 0:
            raise Refused(f"initdb: {done.stderr.strip()[-400:]}")
    finally:
        pwfile.unlink(missing_ok=True)
    with (data / "postgresql.conf").open("a") as f:
        f.write(
            f"\n# valor workspace: TCP on loopback only, no unix socket\nlisten_addresses = '127.0.0.1'\n"
            f"port = {port}\nunix_socket_directories = ''\n"
        )
    _start_postgres(lay, task_id)
    passwords = {r: secrets.token_urlsafe(24) for r in ("app", *spec.roles)}
    try:
        import psycopg
        from psycopg import sql

        with psycopg.connect(
            host="127.0.0.1", port=port, dbname="postgres", user="postgres", password=super_pw,
            autocommit=True,
        ) as conn:  # fmt: skip
            extra = sql.SQL(" CREATEROLE") if spec.roles else sql.SQL("")
            conn.execute(
                sql.SQL("CREATE ROLE app LOGIN CREATEDB{} PASSWORD {}").format(
                    extra, sql.Literal(passwords["app"])
                )
            )
            conn.execute("CREATE DATABASE app OWNER app")
            for r in spec.roles:
                conn.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(r), sql.Literal(passwords[r])
                    )
                )
                conn.execute(sql.SQL("GRANT {} TO app WITH ADMIN OPTION").format(sql.Identifier(r)))
            conn.execute("ALTER ROLE postgres PASSWORD NULL")
    finally:
        _stop_postgres(lay, task_id)
    passfile = lay.home / "pgpass"
    passfile.write_text("".join(f"127.0.0.1:{port}:*:{r}:{pw}\n" for r, pw in passwords.items()))
    passfile.chmod(0o600)
    return passwords


def _start_postgres(lay: Layout, task_id: str) -> None:
    data = lay.pg / "data"
    if _service_run(lay, task_id, _pg("pg_ctl"), "-D", str(data), "status").returncode == 0:
        return
    done = _service_run(
        lay,
        task_id,
        _pg("pg_ctl"),
        "-D",
        str(data),
        "-l",
        str(lay.pg / "postgres.log"),
        "-w",
        "-t",
        "60",
        "start",
    )
    if done.returncode != 0:
        raise Refused(f"the task's Postgres did not start; see {lay.pg / 'postgres.log'}")


def _stop_postgres(lay: Layout, task_id: str) -> None:
    data = lay.pg / "data"
    if (data / "postmaster.pid").exists():
        _service_run(lay, task_id, _pg("pg_ctl"), "-D", str(data), "-m", "fast", "-w", "-t", "60", "stop")


def _start_redis(lay: Layout, task_id: str, port: int) -> None:
    pidfile = lay.redis / "redis.pid"
    if pidfile.exists() and _alive(pidfile):
        return
    redis = shutil.which("redis-server", path="/opt/homebrew/bin:/usr/local/bin:/usr/bin")
    if redis is None:
        raise Refused("no redis-server installed")
    done = _service_run(
        lay, task_id, redis, "--port", str(port), "--bind", "127.0.0.1", "--protected-mode", "yes",
        "--save", "", "--appendonly", "no", "--unixsocket", "", "--dir", str(lay.redis),
        "--enable-protected-configs", "no", "--enable-debug-command", "no", "--enable-module-command", "no",
        "--daemonize", "yes", "--pidfile", str(pidfile), "--logfile", str(lay.redis / "redis.log"),
    )  # fmt: skip
    if done.returncode != 0:
        raise Refused(f"redis-server did not start: {done.stderr.strip()[-300:]}")
    for _ in range(100):
        if _connects(port):
            return
        time.sleep(0.05)
    raise Refused(f"redis-server did not come up on {port}; see {lay.redis / 'redis.log'}")


def _alive(pidfile: Path) -> bool:
    try:
        os.kill(int(pidfile.read_text().strip()), 0)
        return True
    except ValueError, OSError:
        return False


def _connects(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.2)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def start_services(task_id: str, lay: Layout | None, services, ports: dict[str, int]) -> None:
    """Start the task's services if they are down (idempotent)."""
    lay = lay or layout(task_id)
    if "postgres" in services:
        _start_postgres(lay, task_id)
    if "redis" in services:
        _start_redis(lay, task_id, int(ports["redis"]))


def stop_services(task_id: str, lay: Layout | None = None) -> list[dict[str, Any]]:
    """Stop the task's services: Postgres cleanly, then every process left
    under its service mark. Returns every process that was running under
    the mark: those Postgres stopped cleanly (`stopped`) and those the mark
    reaped (`SIGTERM`, `SIGKILL`)."""
    lay = lay or layout(task_id)
    mark = f"valor.service.{task_id}"
    before = runs.sandboxed_pids(mark, "valor.service.none")
    names = runs.commands(before)
    if (lay.profiles / "service.sb").exists():
        try:
            _stop_postgres(lay, task_id)
        except subprocess.TimeoutExpired:
            pass
    reaped = runs.reap_sandboxed(mark, "valor.service.none")
    killed = {r["pid"] for r in reaped}
    return reaped + [
        {"pid": pid, "command": names.get(pid, ""), "signal": "stopped"}
        for pid in before
        if pid not in killed
    ]


def services_of(brief) -> tuple[list[str], dict[str, int]]:
    project = getattr(brief, "project", None) or {}
    return list(project.get("services") or []), {k: int(v) for k, v in (project.get("ports") or {}).items()}


# -- the kernel mirror ------------------------------------------------------------------------


class FetchRefused(RuntimeError):
    pass


@functools.cache
def _rusage():
    lib = ctypes.CDLL("/usr/lib/libproc.dylib")
    lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    lib.proc_pid_rusage.restype = ctypes.c_int
    return lib.proc_pid_rusage


def footprint(pid: int) -> int:
    """A process's physical footprint in bytes (`rusage_info_v2`), or 0."""
    buf = ctypes.create_string_buffer(512)
    if _rusage()(pid, 2, buf) != 0:
        return 0
    return int.from_bytes(buf.raw[72:80], "little")


def _group(pgid: int) -> list[int]:
    listing = subprocess.run(
        [binaries.require(binaries.PS), "-A", "-o", "pid=,pgid="], capture_output=True, text=True, check=False
    ).stdout
    return [
        int(p) for p, g in (line.split() for line in listing.splitlines() if line.strip()) if int(g) == pgid
    ]


def bounded(argv: list[str], *, cwd: Path, env: dict[str, str], max_bytes: int, max_footprint: int,
            timeout: float) -> tuple[int | str, str]:  # fmt: skip
    """Run `argv` in its own process group with a file-size limit, killing
    the whole group when its summed footprint passes `max_footprint` or the
    time limit passes. Returns the exit code (or `footprint`, `timeout`) and
    the stderr tail."""

    # The file-size limit is set by /bin/bash (root's) before it execs the
    # command, since a preexec function is unsafe in a threaded process.
    # bash counts `ulimit -f` in 1024-byte blocks.
    blocks = max(1, max_bytes // 1024)
    wrapped = ["/bin/bash", "-c", f'ulimit -f {blocks} && exec "$@"', "bash", *argv]
    proc = subprocess.Popen(
        wrapped, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True
    )
    deadline = time.monotonic() + timeout
    why: int | str | None = None
    while proc.poll() is None:
        if time.monotonic() > deadline:
            why = "timeout"
        elif sum(footprint(p) for p in _group(proc.pid)) > max_footprint:
            why = "footprint"
        if why:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            break
        time.sleep(0.5)
    _, err = proc.communicate()
    return (why or proc.returncode), err.decode(errors="replace")[-400:]


def fetch_into_mirror(
    mirror: str | Path,
    source: str | Path,
    sha: str,
    ref: str,
    turn_profile_path: str | Path,
    mark: str,
    *,
    max_bytes: int | None = None,
    max_footprint: int | None = None,
    timeout: float | None = None,
) -> None:
    """Fetch one commit from a clone a turn controls into the kernel mirror,
    under `ref`. Raises `FetchRefused` with the reason."""
    source, mirror = Path(source), Path(mirror)
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise FetchRefused(f"{sha!r} is not a full commit id")
    if not ref.startswith("refs/valor/"):
        raise FetchRefused(f"{ref} is not a mirror ref")
    _no_borrowed_objects(source)
    try:
        found = git.hostile(source, turn_profile_path, mark)
    except git.GitError as exc:
        raise FetchRefused(str(exc)) from None
    if found:
        raise FetchRefused("the clone's git config names what the kernel will not run: " + "; ".join(found))
    git_bin = git.binary()
    upload = " ".join(
        shlex.quote(a)
        for a in [
            binaries.require(binaries.SANDBOX_EXEC), "-D", "GATEWAY_PORT=1", "-D", f"VALOR_TURN={mark}",
            "-f", str(turn_profile_path), git_bin, "-c", "uploadpack.allowAnySHA1InWant=true", "upload-pack",
        ]
    )  # fmt: skip
    argv = [
        git_bin, "-C", str(mirror), *git.PINNED,
        "-c", "transfer.fsckObjects=true", "-c", "fetch.fsckObjects=true", "-c", "fetch.unpackLimit=1",
        "-c", "transfer.unpackLimit=1", "-c", "protocol.file.allow=always",
        "fetch", "-q", "--no-tags", "--no-write-fetch-head", "--no-recurse-submodules",
        f"--upload-pack={upload}", str(source), f"+{sha}:{ref}",
    ]  # fmt: skip
    code, err = bounded(
        argv,
        cwd=mirror,
        env=git.env(),
        max_bytes=max_bytes or settings.mirror_fetch_max_bytes,
        max_footprint=max_footprint or settings.mirror_fetch_max_footprint_mb * 1024 * 1024,
        timeout=git.remaining(timeout or settings.git_timeout_s),
    )
    runs.reap(mark)
    if code != 0:
        try:
            git.trusted(mirror, "update-ref", "-d", ref)
        except git.GitError:
            pass
        clean_partial(mirror)
        if code == "footprint":
            raise FetchRefused("the fetch into the kernel mirror passed its memory limit and was killed")
        if code == "timeout":
            raise FetchRefused("the fetch into the kernel mirror did not finish in time")
        raise FetchRefused(f"the fetch into the kernel mirror failed ({code}): {err.strip()}")


def _no_borrowed_objects(source: Path) -> None:
    """Refuse a clone whose `.git` is not a plain directory, or that has
    alternates, a shallow file, or a common directory, every lookup relative
    to a descriptor and never through a link. With no `.git`, the clone is
    bare and the names are looked up in it."""
    try:
        root = os.open(source, DIR_FLAGS)
    except OSError as exc:
        raise FetchRefused(f"the clone is not a plain directory ({exc.strerror})") from None
    try:
        dotgit, why = open_turn_dir(root, ".git")
        if why:
            raise FetchRefused("the clone's .git is not a directory (a gitfile moves the real one elsewhere)")
        gitdir = root if dotgit is None else dotgit
        try:
            for name in ("objects/info/alternates", "objects/info/http-alternates", "shallow", "commondir"):
                if _present(gitdir, name):
                    raise FetchRefused(f"the clone has {name}, which the kernel will not fetch from")
        finally:
            if dotgit is not None:
                os.close(dotgit)
    finally:
        os.close(root)


def _present(dir_fd: int, relpath: str) -> bool:
    """Whether `relpath` names an entry: a link or a non-directory at a
    directory component counts, as does any entry at the last one."""
    head, _, last = relpath.rpartition("/")
    parent = dir_fd
    if head:
        parent, why = open_turn_dir(dir_fd, head)
        if parent is None:
            return why is not None
    try:
        os.stat(last, dir_fd=parent, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False
    finally:
        if parent != dir_fd:
            os.close(parent)


def clean_partial(repo: Path) -> None:
    """Delete what a refused or killed fetch left in the repository: the
    temporary pack and index files and the quarantine directories."""
    objects = Path(repo) / "objects"
    for p in [*objects.glob("pack/tmp_*"), *objects.glob("incoming-*"), *objects.glob("tmp_objdir-*")]:
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)


VALOR_DIR = ".valor"


def tree_has_valor(
    repo: str | Path, rev: str, *, trusted: bool, extra_env: dict[str, str] | None = None
) -> bool:
    """Whether the commit's tree holds a top-level `.valor` entry, in any
    letter case (this Mac's file system ignores case). A committed `.valor`
    would sit where the kernel writes a fresh session's inputs and reads its
    verdict, so no such plan or candidate counts."""
    args = ("ls-tree", "--name-only", "-z", rev)
    listing = (
        git.trusted(repo, *args, extra_env=extra_env, text=False)
        if trusted
        else git.out(repo, *args, text=False)
    )  # a tree entry's name is any bytes but NUL and `/`
    return any(
        name.decode(errors="surrogateescape").casefold() == VALOR_DIR for name in listing.split(b"\0") if name
    )


# -- fresh checkouts -----------------------------------------------------------------------------

KERNEL_IDENTITY = {
    "GIT_AUTHOR_NAME": "Valor kernel",
    "GIT_AUTHOR_EMAIL": "kernel@valor.invalid",
    "GIT_COMMITTER_NAME": "Valor kernel",
    "GIT_COMMITTER_EMAIL": "kernel@valor.invalid",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
}


def fresh_dir(check_dir: Path) -> Path:
    """Remove and make a fresh session's directory with `rmtree`, so a
    read-only entry a sandboxed run left goes too, never following a link."""
    rmtree(check_dir)
    for d in (check_dir / "tmp", check_dir / "claude"):
        d.mkdir(parents=True)
    return check_dir


class ValorInTree(git.GitError):
    """A commit's tree holds a `.valor` entry: the commit's own doing, so
    no checkout of it ever succeeds."""

    def __init__(self, rev: str):
        super().__init__(f"{rev[:12]}'s tree holds a .valor entry")
        self.rev = rev


def blind_checkout(mirror: str | Path, base: str, rev: str, dest: Path) -> dict[str, str]:
    """A repository at `dest` holding exactly two commits the kernel made:
    `base` (the base's tree) and `candidate` (`rev`'s tree), with only the
    objects those trees reach, so no builder commit message, intermediate
    commit, or object outside the two trees exists in it. A `rev` whose
    tree holds `.valor` raises `ValorInTree`; the base's tree is never
    checked out, so its entries do not count."""
    mirror = Path(mirror)
    dest.parent.mkdir(parents=True, exist_ok=True)
    git.trusted(dest.parent, "init", "-q", "-b", "main", str(dest))
    borrow = {"GIT_ALTERNATE_OBJECT_DIRECTORIES": str(mirror / "objects"), **KERNEL_IDENTITY}
    if tree_has_valor(dest, rev, trusted=True, extra_env=borrow):
        raise ValorInTree(rev)
    base_tree = git.trusted(dest, "rev-parse", f"{base}^{{tree}}", extra_env=borrow)
    rev_tree = git.trusted(dest, "rev-parse", f"{rev}^{{tree}}", extra_env=borrow)
    first = git.trusted(dest, "commit-tree", base_tree, "-m", "base", extra_env=borrow)
    second = git.trusted(dest, "commit-tree", rev_tree, "-p", first, "-m", "candidate", extra_env=borrow)
    git.trusted(dest, "update-ref", "refs/heads/main", second, extra_env=borrow)
    git.trusted(dest, "repack", "-a", "-d", "-q", extra_env=borrow)
    git.trusted(dest, "checkout", "-q", "-f", "main")
    with (dest / ".git" / "info" / "exclude").open("a") as f:
        f.write(".valor/\n")
    return {"base": first, "candidate": second}


def check_harness(
    lay: Layout, check_dir: Path, ports: list[int], env: dict[str, str], *, services: bool = False
) -> dict[str, Any]:
    """The harness settings of one fresh session: its own profile, TMPDIR,
    Claude Code config directory, and the trusted git first on PATH. A
    session that runs nothing against the task's services (critique) gets
    none of their ports and only the PATH of the task's environment, so no
    database credential. With `services` (test, review) the session gets
    the environment `check_services` made for its fresh instances, those
    ports, and its caches under `<check_dir>/cache/`."""
    name = check_dir.name
    path = lay.profiles / f"{name}.sb"
    path.write_text(check_profile(lay, check_dir, ports if services else []))
    git_dir = str(Path(git.binary()).parent)
    if services:
        # The check's own copy of the caches, never the builder's.
        cache = check_dir / "cache"
        fresh_env = {
            **env,
            "UV_CACHE_DIR": str(cache / "uv"),
            "UV_PYTHON_INSTALL_DIR": str(cache / "python"),
            "npm_config_cache": str(cache / "npm"),
        }
    else:
        fresh_env = {"PATH": env.get("PATH", "/usr/bin:/bin")}
    fresh_env["PATH"] = f"{git_dir}:{fresh_env.get('PATH', '/usr/bin:/bin')}"
    return {
        "sandbox_profile": str(path),
        "tmpdir": str(check_dir / "tmp"),
        "claude_config_dir": str(check_dir / "claude"),
        "env": fresh_env,
    }


def write_inputs(checkout: Path, files: dict[str, str]) -> None:
    """Make `.valor/inputs/` in a checkout the kernel just made and write each
    input there, every step relative to a descriptor: `.valor` must not
    exist yet, nothing is followed through a link, and no file is
    overwritten. So nothing committed in the tree can redirect a write, and
    no verdict file exists before the session's turn."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC
    root = os.open(checkout, flags)
    fds = [root]
    try:
        os.mkdir(VALOR_DIR, 0o755, dir_fd=root)  # FileExistsError if the tree brought one
        valor = os.open(VALOR_DIR, flags, dir_fd=root)
        fds.append(valor)
        os.mkdir("inputs", 0o755, dir_fd=valor)
        inputs = os.open("inputs", flags, dir_fd=valor)
        fds.append(inputs)
        write_files(inputs, files)
        no_verdict_yet(valor)
    finally:
        for fd in reversed(fds):
            os.close(fd)


def no_verdict_yet(valor_fd: int) -> None:
    """Refuse a `.valor` that already holds a verdict file (or anything by
    that name) before the session's turn."""
    try:
        os.stat("verdict.json", dir_fd=valor_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise FileExistsError("a verdict file exists before the session's turn")


def write_files(dir_fd: int, files: dict[str, str]) -> None:
    """Each file created new in the directory `dir_fd` names: refused when
    the name is taken (`O_EXCL`) or is a link (`O_NOFOLLOW`)."""
    for name, text in files.items():
        if "/" in name or name.startswith("."):
            raise ValueError(f"input name {name!r}")
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644,
                     dir_fd=dir_fd)  # fmt: skip
        with os.fdopen(fd, "w") as f:
            f.write(text)


# -- files a turn controls ------------------------------------------------------------
#
# Every read of a path a turn (or a fresh session) can write goes through
# these: each component opened relative to its parent's descriptor with
# `O_NOFOLLOW | O_NONBLOCK`, so no link is followed and no FIFO blocks, and a
# file is read only when `fstat` says a regular file with one link whose
# blocks on disk cover its size. A sparse file is refused: its size costs the
# turn nothing, so reading it is unbounded, while a file whose size is backed
# by disk the turn wrote is as large as the turn could make it. A missing
# entry is `(None, None)`; an entry that exists and is refused is
# `(None, why)`, and its contents are never read.

DIR_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_NONBLOCK | os.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def _parts(relpath: str) -> list[str] | None:
    parts = relpath.split("/")
    if relpath.startswith("/") or any(p in ("", ".", "..") for p in parts):
        return None
    return parts


def _open_dir(dir_fd: int, name: str, shown: str) -> tuple[int | None, str | None]:
    try:
        return os.open(name, DIR_FLAGS, dir_fd=dir_fd), None
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        # With O_DIRECTORY, macOS answers a link with ENOTDIR, as for a file.
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            return None, f"{shown} is not a plain directory"
        return None, f"{shown} is not a plain directory ({exc.strerror})"


def open_turn_dir(dir_fd: int, relpath: str) -> tuple[int | None, str | None]:
    """The directory at `relpath` under `dir_fd` as a new descriptor the
    caller closes, or (None, why), or (None, None) when it does not exist."""
    parts = _parts(relpath)
    if parts is None:
        return None, f"{relpath!r} is not a plain relative path"
    fd = dir_fd
    for i, part in enumerate(parts):
        nxt, why = _open_dir(fd, part, "/".join(parts[: i + 1]))
        if fd != dir_fd:
            os.close(fd)
        if nxt is None:
            return None, why
        fd = nxt
    return fd, None


def open_turn_file(dir_fd: int, relpath: str) -> tuple[int | None, str | None]:
    """The regular file with one link and no holes at `relpath` under
    `dir_fd`, opened for reading as a descriptor the caller closes, or (None,
    why), or (None, None) when it does not exist. Nothing is read."""
    fd, _size, why = _open_checked(dir_fd, relpath)
    return fd, why


def _open_checked(dir_fd: int, relpath: str) -> tuple[int | None, int, str | None]:
    """`open_turn_file`, with the size `fstat` saw when it checked the file."""
    fd, st, why = open_plain_file(dir_fd, relpath)
    if fd is None:
        return None, 0, why
    if st.st_blocks * 512 < st.st_size:
        os.close(fd)
        return None, 0, f"{relpath} is sparse ({st.st_size} bytes claimed, {st.st_blocks * 512} on disk)"
    return fd, st.st_size, None


def open_plain_file(dir_fd: int, relpath: str) -> tuple[int | None, os.stat_result | None, str | None]:
    """The regular file with one link at `relpath` under `dir_fd`, opened
    for reading, and its `fstat`; or (None, None, why), or (None, None, None)
    when it does not exist. A sparse file is not refused here: this is for a
    caller that sizes a file and never reads it."""
    parts = _parts(relpath)
    if parts is None:
        return None, None, f"{relpath!r} is not a plain relative path"
    parent = dir_fd
    if len(parts) > 1:
        parent, why = open_turn_dir(dir_fd, "/".join(parts[:-1]))
        if parent is None:
            return None, None, why
    try:
        try:
            fd = os.open(parts[-1], FILE_FLAGS, dir_fd=parent)
        except FileNotFoundError:
            return None, None, None
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                return None, None, f"{relpath} is a link, not a plain file"
            return None, None, f"{relpath} is not a plain file ({exc.strerror})"
    finally:
        if parent != dir_fd:
            os.close(parent)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        return None, None, f"{relpath} is not a regular file"
    if st.st_nlink != 1:
        os.close(fd)
        return None, None, f"{relpath} has {st.st_nlink} links"
    return fd, st, None


def read_turn_file(dir_fd: int, relpath: str) -> tuple[bytes | None, str | None]:
    """The file `open_turn_file` opens, read to the size `fstat` saw when it
    checked the file and no further, so a file grown after the check is not
    read past what was checked; or why not, or (None, None) when it does not
    exist."""
    fd, size, why = _open_checked(dir_fd, relpath)
    if fd is None:
        return None, why
    chunks = []
    try:
        while size > 0 and (chunk := os.read(fd, min(size, 1 << 20))):
            chunks.append(chunk)
            size -= len(chunk)
    finally:
        os.close(fd)
    return b"".join(chunks), None


def _file_away(
    src_fd: int, name: str, valor_fd: int, turn_id: str, sub: tuple[str, ...] = ()
) -> tuple[int | None, str | None]:
    """Move the entry `name` in `src_fd` to `.valor/handled/<turn_id>/<sub...>/`
    under `valor_fd`, every step relative to a descriptor and never through a
    link, and return that directory's descriptor (the caller closes it), so
    the entry is read only once it is filed. `os.rename` moves the entry
    itself, never what a link names. A missing entry is (None, None). When
    the move is refused, the entry is removed unread (a link is unlinked,
    not its target) and the reason returned, so nothing is left for a later
    turn to read."""
    try:
        os.stat(name, dir_fd=src_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, f"{name} cannot be looked up ({exc.strerror})"
    parent, why = valor_fd, None
    for part in ("handled", turn_id, *sub):
        try:
            os.mkdir(part, 0o755, dir_fd=parent)
        except FileExistsError:
            pass
        except OSError as exc:
            why = f"handled/{part} cannot be made ({exc.strerror})"
            break
        nxt, refused = _open_dir(parent, part, f"handled/{part}")
        if parent != valor_fd:
            os.close(parent)
        parent = nxt
        if nxt is None:
            why = refused or f"handled/{part} vanished"
            break
    if why is None:
        try:
            os.rename(name, name, src_dir_fd=src_fd, dst_dir_fd=parent)
            return parent, None
        except OSError as exc:
            why = f"{name} cannot be filed away ({exc.strerror})"
    if parent is not None and parent != valor_fd:
        os.close(parent)
    return None, _remove_unread(src_fd, name, why)


def _remove_unread(dir_fd: int, name: str, why: str) -> str:
    try:
        os.unlink(name, dir_fd=dir_fd)
    except IsADirectoryError, PermissionError:
        try:
            os.rmdir(name, dir_fd=dir_fd)
        except OSError:
            return f"{why}; left in place unread"
    except FileNotFoundError:
        pass
    except OSError:
        return f"{why}; left in place unread"
    return f"{why}; removed unread"


def read_verdict(checks: Path, name: str, turn_id: str) -> tuple[dict[str, Any] | None, str | None]:
    """The verdict a fresh session left at `<checks>/<name>/repo/.valor/verdict.json`,
    walked from the kernel's checks directory without following a link at
    any component (the check directory included, which the session can
    replace) and without blocking; moved to `.valor/handled/<turn_id>/`
    first and read there. Returns (verdict, why not)."""
    try:
        root = os.open(checks, DIR_FLAGS)
    except OSError as exc:
        return None, f"the checks directory cannot be opened: {exc.strerror}"
    try:
        valor, why = open_turn_dir(root, f"{name}/repo/.valor")
        if valor is None:
            return None, why or "no .valor/verdict.json"
        try:
            dest, why = _file_away(valor, "verdict.json", valor, turn_id)
            if dest is None:
                return None, why or "no .valor/verdict.json"
            try:
                body, why = read_turn_file(dest, "verdict.json")
            finally:
                if dest != valor:
                    os.close(dest)
        finally:
            os.close(valor)
    finally:
        os.close(root)
    if body is None:
        return None, why or "no .valor/verdict.json"
    try:
        data = json.loads(body)
    except ValueError:
        return None, "verdict.json is not JSON"
    if not isinstance(data, dict):
        return None, "verdict.json is not a JSON object"
    return data, None


# -- a check's own services and caches -------------------------------------------------------

SVC_SUFFIX = "-svc"


def remove_check_services(lay: Layout) -> list[str]:
    """Remove every `checks/*-svc/` directory, a check's service data left
    by a run that ended or a kernel that was killed. Call only after
    `stop_services` has reaped the task's mark."""
    gone = []
    if lay.checks.is_dir():
        for d in lay.checks.iterdir():
            if d.name.endswith(SVC_SUFFIX):
                rmtree(d)
                gone.append(d.name)
    return gone


def spec_of(project: dict[str, Any]) -> Spec:
    """The project spec a Brief's `project` copy names (services, roles,
    env), enough to build a check's services and environment."""
    return Spec(
        name=project.get("name") or "project",
        repo=project.get("repo") or "",
        kind=project.get("kind") or "plain",
        suite=project.get("suite") or "true",
        services=tuple(project.get("services") or ()),
        roles=tuple(project.get("roles") or ()),
        setup=tuple(project.get("setup") or ()),
        env=dict(project.get("env") or {}),
    )


@contextlib.contextmanager
def check_services(
    lay: Layout, check_dir: Path, project: dict[str, Any], task_id: str
) -> Iterator[dict[str, str]]:
    """Fresh service instances for one check, on the task's own ports.

    The task's own instances are stopped first (every process under
    `valor.service.<task>`, whichever layout started it) and every stale
    `checks/*-svc/` removed; a layout at `checks/<name>-svc/` gets a new
    Postgres cluster with the project's roles and new passwords, and an
    empty Redis. The password file is copied to `<check_dir>/tmp/pgpass`
    (`O_NOFOLLOW | O_EXCL`). Yields the check's environment
    (`harness_env` over the fresh instances). On exit the check's instances
    are stopped, their directory removed, and the task's own started again,
    even when the stop or the removal raises (that exception is raised, the
    restart's failure, if any, as its cause); the task's Postgres keeps its
    data, its Redis starts empty."""
    spec = spec_of(project)
    names = list(spec.services)
    ports = {k: int(v) for k, v in (project.get("ports") or {}).items()}
    work = lay.root.parent
    svc = Layout(lay.checks / f"{check_dir.name}{SVC_SUFFIX}")
    stop_services(task_id, lay)
    remove_check_services(lay)
    try:
        passwords: dict[str, str] = {}
        passfile = check_dir / "tmp" / "pgpass"
        if names:
            svc.profiles.mkdir(parents=True)
            (svc.profiles / "service.sb").write_text(
                service_profile(svc, task_id, [ports[n] for n in names], work=work)
            )
        if "postgres" in names:
            passwords = _init_postgres(svc, task_id, spec, ports["postgres"])
            copy_new(svc.home / "pgpass", passfile)
        if "redis" in names:
            svc.redis.mkdir(parents=True, exist_ok=True)
        start_services(task_id, svc, names, ports)
        yield harness_env(svc, spec, ports, passwords, bin_dir=work / "bin", passfile=passfile)
    finally:
        try:
            stop_services(task_id, svc)
            rmtree(svc.root)
        except BaseException as exc:
            if names:
                try:
                    start_services(task_id, lay, names, ports)
                except Exception as restart:
                    raise exc from restart  # the first failure is the one raised
            raise
        if names:
            start_services(task_id, lay, names, ports)


def copy_new(src: Path, dest: Path) -> None:
    """Copy `src` to `dest`, refusing a `dest` that exists or is a link
    (`O_EXCL | O_NOFOLLOW`): a check's directory is the suite's to write."""
    data = src.read_bytes()
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def clone_tree(src: Path, dest: Path) -> None:
    """`src` copied to `dest` as an APFS clone (`cp -c`), near free on disk;
    a file written in one never shows in the other."""
    rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["/bin/cp", "-c", "-R", str(src), str(dest)], check=True, capture_output=True)


def rmtree(path: Path) -> None:
    """Remove a tree a sandboxed program wrote, entries of any mode
    included, never following a link. Each entry's user flags and its ACL
    are cleared, without reading either, before it is examined, opened,
    moved or unlinked: a mode alone leaves a flag such as `uchg` or
    `uappnd`, or an ACL entry, in the way, and an ACL denying `readattr` or
    `readsecurity` makes even its `lstat` fail. Works through directory
    descriptors with no recursion: each directory found below `path` is
    moved up to sit directly in `path` before it is emptied, so no path
    grows past one level and at most two directories are open at once,
    however deep the tree."""
    try:
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except FileNotFoundError:
        return
    try:
        try:
            st = _cleared(path.name, parent)
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(st.st_mode):
            os.unlink(path.name, dir_fd=parent)
            return
        root = _open_own_dir(path.name, parent)
        try:
            _empty_dir(root)
        finally:
            os.close(root)
        os.rmdir(path.name, dir_fd=parent)
    finally:
        os.close(parent)


# setattrlistat(2): `struct attrlist` naming ATTR_CMN_FLAGS (0x40000) and
# ATTR_CMN_EXTENDED_SECURITY (0x400000); the value buffer is the flags (0),
# then an attrreference to a `kauth_filesec` whose entry count is
# KAUTH_FILESEC_NOACL, which removes the ACL. FSOPT_NOFOLLOW is 1.
_ATTRS = struct.pack("=HHIIIII", 5, 0, 0x40000 | 0x400000, 0, 0, 0, 0)
_NO_ACL = struct.pack("=I16s16sII", 0x012CC16D, b"", b"", 0xFFFFFFFF, 0)
_CLEARED = struct.pack("=IiI", 0, 8, len(_NO_ACL)) + _NO_ACL


@functools.cache
def _setattrlistat():
    fn = ctypes.CDLL(None, use_errno=True).setattrlistat
    fn.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_size_t,
        ctypes.c_uint32,
    ]
    fn.restype = ctypes.c_int
    return fn


def _cleared(name: str, dir_fd: int) -> os.stat_result:
    """Clear every user flag and remove the ACL of the entry `name` under
    `dir_fd` in one write that reads neither (the owner may always write
    both, and an ACL may deny reading them), never following a link; then
    its `lstat`."""
    if _setattrlistat()(dir_fd, os.fsencode(name), _ATTRS, _CLEARED, len(_CLEARED), 1) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), name)
    return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)


def _open_own_dir(name: str, dir_fd: int) -> int:
    """The cleared directory `name` under `dir_fd`, made 0700 first so it
    can be listed, searched and emptied; a link there is never followed."""
    os.chmod(name, 0o700, dir_fd=dir_fd, follow_symlinks=False)
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)


def _empty_dir(root: int) -> None:
    moved = 0
    while names := os.listdir(root):
        for name in names:
            st = _cleared(name, root)
            if not stat.S_ISDIR(st.st_mode):
                os.unlink(name, dir_fd=root)
                continue
            fd = _open_own_dir(name, root)
            try:
                for sub in os.listdir(fd):
                    sub_st = _cleared(sub, fd)
                    if not stat.S_ISDIR(sub_st.st_mode):
                        os.unlink(sub, dir_fd=fd)
                        continue
                    # Moving a directory to a new parent rewrites its `..`.
                    os.chmod(sub, 0o700, dir_fd=fd, follow_symlinks=False)
                    while True:
                        moved += 1
                        try:
                            os.stat(f".rm{moved}", dir_fd=root, follow_symlinks=False)
                        except FileNotFoundError:
                            break
                        except PermissionError:
                            pass  # taken, by an entry whose ACL denies reading its attributes
                    os.rename(sub, f".rm{moved}", src_dir_fd=fd, dst_dir_fd=root)
            finally:
                os.close(fd)
            os.rmdir(name, dir_fd=root)


def remove(task_id: str, lay: Layout | None = None) -> None:
    lay = lay or layout(task_id)
    stop_services(task_id, lay)
    if lay.root.exists():
        shutil.rmtree(lay.root)


# -- other tasks' services --------------------------------------------------------------------


async def sweep(conn, task_id: str, work: Path | None = None, *, after_scan=None) -> list[dict[str, Any]]:
    """Stop the services a killed kernel left up: those of every other task
    whose run is not live (its router lock `run:<task>` can be taken), and
    those of any directory under `work` with no task row whose provisioning
    is not live (its `provision:<id>` lock can be taken), which a kernel
    killed mid-setup leaves. Takes the `workspace:ports` lock only if it is
    free, so a start never waits on a sweep, nor a sweep on a start; a busy
    lock skips this sweep, and the next run's does it. Returns what was
    stopped, each entry naming its task or directory. `after_scan`, when
    given, is awaited between the scan and the locks (tests drive the
    window in which a task row can appear)."""
    import asyncio

    stopped: list[dict[str, Any]] = []
    got = await (
        await conn.execute("SELECT pg_try_advisory_lock(hashtextextended('workspace:ports', 0))")
    ).fetchone()
    if not got[0]:
        return stopped
    try:
        rows = await (
            await conn.execute(
                "SELECT d.id, d.body->>'mirror' FROM documents d WHERE d.kind = 'task' AND d.id <> %s "
                "AND jsonb_array_length(COALESCE(d.body->'project'->'services', '[]'::jsonb)) > 0 "
                "AND NOT EXISTS (SELECT 1 FROM events e WHERE e.task_id = d.id AND e.type = 'workspace.removed')",
                (task_id,),
            )
        ).fetchall()
        candidates: dict[str, tuple[str, Layout | None]] = {
            other: ("run", Layout(Path(mirror).parent) if mirror else None) for other, mirror in rows
        }
        if work is not None and work.is_dir():
            dirs = [d.name for d in work.iterdir() if d.is_dir() and re.fullmatch(r"[0-9a-f]{12}", d.name)]
            known = {
                r[0]
                for r in await (
                    await conn.execute(
                        "SELECT id FROM documents WHERE kind = 'task' AND id = ANY(%s)", (dirs,)
                    )
                ).fetchall()
            }
            for d in dirs:
                if d not in known and d != task_id:
                    candidates[d] = ("provision", Layout(work / d))
        marked = await asyncio.to_thread(runs.marked_services, list(candidates))
        if after_scan is not None:
            await after_scan()
        for other in marked:
            kind, lay = candidates[other]
            key = f"{kind}:{other}"
            got = await (
                await conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,))
            ).fetchone()
            if not got[0]:
                continue
            try:
                if (
                    kind == "provision"
                    and await (
                        await conn.execute(
                            "SELECT 1 FROM documents WHERE kind = 'task' AND id = %s", (other,)
                        )
                    ).fetchone()
                ):
                    continue  # it became a task while we looked: its run decides
                found = await asyncio.to_thread(stop_services, other, lay)
                stopped += [{**r, "task": other, "orphan": kind == "provision"} for r in found]
            finally:
                await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))
    finally:
        await conn.execute("SELECT pg_advisory_unlock(hashtextextended('workspace:ports', 0))")
    return stopped
