"""Kernel workspaces: one directory per task, provisioned before the task
starts, from a project spec the kernel reads (never the candidate).

    <work_dir>/
      cache/<name>.git     bare clone per repository, fetched by the kernel
      bin/                 shared tools
      <task_id>/
        repo/              the builder's clone: the working session's cwd
        origin.git/        local bare origin: push_branch's target, and the
                           merge's when the spec has no merge_url; no turn
                           writes it
        kernel.git/        the kernel mirror: the base, plan commits,
                           candidates, docs heads; no turn writes it
        home/              pgpass, profiles/*.sb
        cache/             the builder's uv and npm caches
        state/work/        the working session's Claude Code config directory
        checks/<name>/     a fresh session's repo/, tmp/, claude/
        pg/data            the task's Postgres cluster (TCP on loopback only)
        redis/             the task's redis-server, when the project asks
        ports.json         the task's service ports, written when they are
                           chosen, so no other start takes them

Every program the kernel starts here runs inside a sandbox-exec profile
written by this module: the turn's (`turn.sb`, also every fresh session's)
and the services' (`service.sb`). A profile allows by default and denies
four things: the kernel key directory, the machine cluster's data
directory, the backup disk, and the machine cluster on loopback (port and
socket). It marks what runs under it with a mach name the profile denies,
`valor.turn.<VALOR_TURN>` or `valor.service.<task_id>`, which is how
`core.runs` finds a turn's or a task's services' processes and stops them,
also after the kernel died.

The kernel mirror is fed from the builder's clone by `fetch_into_mirror`:
the sending side run inside the turn's own sandbox, the receiving side with
fsck.
"""

import atexit
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
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
import tomllib
import urllib.parse
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core import binaries, channels, credentials, git, runs
from core.settings import settings

KINDS = ("python-uv", "django", "node", "plain")
SERVICES = ("postgres", "redis")
HOME = Path.home()
# The businesses' knowledge bases, one folder each, under the user's home.
VAULT = "work-vault"


class Refused(ValueError):
    """A project or a provisioning step the kernel will not take."""


# -- project specs -----------------------------------------------------------------


@dataclass(frozen=True)
class Spec:
    """A project, read once at start and copied into the Brief, so editing
    the file never changes a running task. `suite` names the command the
    kernel runs to decide red or pass; a candidate never chooses it.

    `chats` lists the chats whose messages start tasks under this project
    (`"telegram:<chat id>"`, `"email:<sender address>"`,
    `"slack:<team id>/<channel or user id>"`; each id normalised as
    `core/channels.py` says), and `machine` the
    machine whose bridges receive them; none means
    `settings.default_machine`, so each chat has one owner.

    `knowledge_base` names the business's folder in `~/work-vault`, which a
    working task's turns read and write.

    `client` names the client the project is for (one Mac per client); every
    memory record ingested from its tasks carries it (docs/memory.md)."""

    name: str
    repo: str
    kind: str
    suite: str
    branch: str | None = None
    services: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    setup: tuple[str, ...] = ()
    lint: str | None = None
    merge_url: str | None = None
    target_branch: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    max_output_tokens: int | None = None
    chats: tuple[str, ...] = ()
    machine: str | None = None
    knowledge_base: str | None = None
    client: str | None = None

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
            if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", r) or r in ("app", "postgres"):
                raise Refused(f"role name {r!r} is not allowed")
        if roles and "postgres" not in services:
            raise Refused("roles need the postgres service")
        extensions = tuple(raw.get("extensions") or ())
        for e in extensions:
            if not isinstance(e, str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", e):
                raise Refused(f"extension name {e!r} is not allowed")
        if extensions and "postgres" not in services:
            raise Refused("extensions need the postgres service")
        cap = raw.get("max_output_tokens")
        if cap is not None and (not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0):
            raise Refused("max_output_tokens is a positive integer")
        env = raw.get("env") or {}
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            raise Refused("a project's env maps names to strings")
        chats = tuple(raw.get("chats") or ())
        normal = []
        for chat in chats:
            channel, _, ident = chat.partition(":") if isinstance(chat, str) else ("", "", "")
            row = channels.get(channel)
            if row is None or not ident or not row.listable(ident):
                raise Refused(f"chat {chat!r} is not {channels.hints()}")
            normal.append(f"{channel}:{row.normal(ident)}")
        machine = raw.get("machine")
        if machine is not None and (not isinstance(machine, str) or not machine):
            raise Refused("machine names a machine")
        kb = raw.get("knowledge_base")
        if kb is not None and (
            not isinstance(kb, str) or kb in ("", ".", "..") or any(c in kb for c in '/"\\\n')
        ):
            raise Refused(f"knowledge_base {kb!r} is not a folder name in ~/{VAULT}")
        client = raw.get("client")
        if client is not None and (not isinstance(client, str) or not client.strip()):
            raise Refused("client names a client")
        return cls(
            name=raw["name"],
            repo=raw["repo"],
            kind=raw["kind"],
            suite=raw["suite"],
            branch=raw.get("branch"),
            services=services,
            roles=roles,
            extensions=extensions,
            setup=tuple(raw.get("setup") or ()),
            lint=raw.get("lint"),
            merge_url=raw.get("merge_url"),
            target_branch=raw.get("target_branch"),
            env=dict(env),
            max_output_tokens=cap,
            chats=tuple(normal),
            machine=machine,
            knowledge_base=kb,
            client=client,
        )

    @classmethod
    def all(cls) -> list[Spec]:
        """Every project spec in `settings.projects_dir`, by name. A spec
        that does not load is left out; `start` names its error."""
        found = []
        for path in sorted(Path(settings.projects_dir).glob("*.toml")):
            try:
                found.append(cls.load(str(path)))
            except Refused:
                continue
        return found

    @classmethod
    def here(cls) -> list[Spec]:
        """`all`, less the specs placed on another machine: this Mac's
        projects (a spec with no `machine` is the default machine's)."""
        return [s for s in cls.all() if (s.machine or settings.default_machine) == settings.machine]

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


def knowledge_base(spec: Spec, home: Path = HOME) -> Path | None:
    """The spec's business folder, `~/work-vault/<knowledge_base>`, or None."""
    return home / VAULT / spec.knowledge_base if spec.knowledge_base else None


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
    def setup(self) -> Path:
        return self.root / "setup"

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
    return [
        Path(settings.pg_passfile).parent,
        Path(settings.pg_data_dir),
        Path(settings.backup_dir),
    ]


# -- sandbox profiles -----------------------------------------------------------------


def _paths(kind: str, paths) -> list[str]:
    return [f'    ({kind} "{p}")' for p in paths]


def _ancestors(paths) -> list[str]:
    """Every directory above each path but `/`, for the path as written and
    with symlinks resolved."""
    found = set()
    for p in paths:
        for form in {str(p), os.path.realpath(p)}:
            found.update(str(a) for a in Path(form).parents if str(a) != "/")
    return sorted(found)


def profile(*, service: str | None = None, kernel: list[Path] | None = None) -> str:
    """The one sandbox-exec profile: allow by default; deny the kernel paths
    (`kernel_paths`, as written and with symlinks resolved, since the
    sandbox matches the resolved path), renaming any directory above them,
    and the machine cluster on loopback, its port and its socket (the
    demonstration's Postgres trust hole). A `service` profile is marked by
    the mach name `valor.service.<service>`; a turn's by
    `valor.turn.<VALOR_TURN>`, which `core.runs` reaps by."""
    kernel = kernel if kernel is not None else kernel_paths()
    forms = sorted({f for p in kernel for f in (str(p), os.path.realpath(p))})
    if service:
        mark = f'(deny mach-lookup (global-name "valor.service.{service}"))'
    else:
        mark = '(deny mach-lookup (global-name (string-append "valor.turn." (param "VALOR_TURN"))))'
    lines = [
        "(version 1)",
        "(allow default)",
        "(deny file-read* file-write*",
        *_paths("subpath", forms),
        ")",
        "(deny file-write*",
        *_paths("literal", _ancestors(kernel)),
        ")",
        "(deny network-outbound",
        f'    (remote ip "localhost:{settings.pgport}")',
        f'    (remote unix-socket (path-literal "{settings.pg_socket_real}"))',
        f'    (remote unix-socket (path-literal "{settings.pg_socket}")))',
        mark,
    ]
    return "\n".join(lines) + "\n"


def turn_profile(*, kernel: list[Path] | None = None) -> str:
    """The profile of every turn, setup command, and fresh session."""
    return profile(kernel=kernel)


def service_profile(task_id: str, *, kernel: list[Path] | None = None) -> str:
    """The services' profile, marked `valor.service.<task_id>`."""
    return profile(service=task_id, kernel=kernel)


def _write(path: Path, text: str) -> None:
    """Write a profile whole: a sandbox starting beside the write reads the
    old one or the new one, never half of either."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
    tmp.write_text(text)
    os.replace(tmp, path)


def current_profile(path: str) -> str:
    """`path`, a turn's `sandbox_profile`, rewritten from today's `profile()`
    when it is a kernel-kept one (under `work_dir()`), so a task provisioned
    before a profile change runs under the current one. A profile the
    operator named elsewhere is left as it is."""
    p = Path(path)
    if p.name == "turn.sb" and p.is_relative_to(work_dir()):
        _write(p, turn_profile())
    return path


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


def cache_path(origin: str, work: Path) -> Path:
    """Where the kernel's bare clone of `origin` (a local path or a URL)
    lives under `work`: `cache/<name>-<digest>.git`, keyed by the URL's
    digest so two repositories with one basename never share a cache."""
    local = Path(origin).expanduser()
    url = str(local.resolve()) if local.exists() else origin
    stem = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", stem)[:40] + "-" + hashlib.sha256(url.encode()).hexdigest()[:12]
    return work / "cache" / f"{name}.git"


def _cache(
    spec: Spec,
    source: str | None,
    work: Path,
    *,
    credential: str | Path | None = None,
    loopback: bool = False,
) -> Path:
    """The kernel's bare clone of the repository, fetched by its trusted git:
    a local path, or an HTTPS URL. Without `credential` (provisioning) the
    URL is fetched anonymously and a repository that needs a credential to
    fetch is refused. With `credential`, the GitHub key file (the conditions
    routine), a remote URL is fetched as Valor's GitHub account: each git
    call against it gets a header file of its own
    (`core.credentials.header_file`) and the pins of `git.credential_pins`,
    as the merge's push does. `loopback` lets a loopback http URL be
    fetched and named in the header file, for tests only."""
    origin = source or spec.repo
    local = Path(origin).expanduser()
    url = str(local.resolve()) if local.exists() else origin
    cache = cache_path(origin, work)
    keyed = (
        credential is not None
        and not local.exists()
        and (loopback or urllib.parse.urlsplit(url).hostname == "github.com")
    )
    if not local.exists() and not url.startswith("https://") and not loopback:
        raise Refused(f"{origin} is neither a local repository nor an https URL")
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        git.trusted(cache.parent, "init", "-q", "--bare", str(cache))

    def remote(*args: str) -> str:
        if not keyed:
            return git.trusted(cache, *args)
        with credentials.header_file(credential, url, loopback=loopback) as header:
            return git.out(cache, *args, credential=header, url=url)

    try:
        remote(
            "fetch",
            "-q",
            "--no-tags",
            "--upload-pack=git-upload-pack",
            url,
            "+refs/heads/*:refs/heads/*",
        )
        listed = remote("ls-remote", "--symref", "--upload-pack=git-upload-pack", url, "HEAD")
    except git.GitError as exc:
        if needs_credential(str(exc)):
            if keyed:
                raise Refused(f"{origin} refused Valor's GitHub credential; rotate it: {exc}") from None
            raise Refused(
                f"{origin} needs a credential to fetch; private repositories are not fetched"
            ) from None
        raise Refused(f"fetching {origin}: {exc}") from None
    except credentials.CredentialError as exc:
        raise Refused(f"fetching {origin} as Valor: {exc}") from None
    for line in listed.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            git.trusted(cache, "symbolic-ref", "HEAD", line[len("ref: ") : -len("\tHEAD")])
    return cache


def needs_credential(error: str) -> bool:
    """Whether git's error says the remote wants a credential: the kernel
    fetches no private repository."""
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
    directory's `bin/`) first on PATH, then Postgres's, then the operator's; the caches under `lay`, the database and Redis it was given, `passfile` as the
    Postgres password file, and the business's knowledge base folder
    (`VALOR_KNOWLEDGE_BASE`) when the spec names one. The directories are
    passed, never derived from `lay`, so a check's own service layout gets
    the task's `bin/`."""
    # Postgres's own programs, `pg_config` among them, when the task has a
    # database: Homebrew links only `pg_config-18`, and a driver such as
    # psycopg2 builds with it. Then the operator's own PATH.
    pg = [settings.pg_bin] if "postgres" in spec.services else []
    env = {
        "PATH": os.pathsep.join([str(bin_dir), *pg, os.environ.get("PATH", "/usr/bin:/bin")]),
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
    kb = knowledge_base(spec)
    if kb:
        env["VALOR_KNOWLEDGE_BASE"] = str(kb)
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
        # Its git calls carry the mark `provision-<task>` (`git.marked`), set
        # in the thread `provision` runs in: a kernel killed mid-provision
        # leaves git running in its own session, and `remove` reaps it.
        with git.marked(f"provision-{task_id}"):
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
    for d in (lay.repo.parent, lay.home, lay.profiles, lay.cache, lay.work_state / "claude",
              lay.work_state / "pi", lay.checks, lay.root.parent / "bin"):  # fmt: skip
        d.mkdir(parents=True, exist_ok=True)
    _install_tools(lay.root.parent / "bin")
    # The clone: history up to the base only, no tags, one work branch.
    ref = f"refs/heads/valor-base/{task_id}"
    git.trusted(cache, "update-ref", ref, base_sha)
    try:
        git.trusted(lay.root, "clone", "-q", "--no-tags", "--single-branch", "--branch",
                    f"valor-base/{task_id}", f"file://{cache}", str(lay.repo))  # fmt: skip
    finally:
        with git.uninterrupted():  # the shared cache keeps no ref of an interrupted task
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
    # Home: the profiles.
    kb = knowledge_base(spec)
    _write(lay.profiles / "turn.sb", turn_profile())
    passwords: dict[str, str] = {}
    if spec.services:
        _write(lay.profiles / "service.sb", service_profile(task_id))
    if "postgres" in spec.services:
        passwords = _init_postgres(lay, task_id, spec, ports["postgres"])
    if "redis" in spec.services:
        lay.redis.mkdir(parents=True, exist_ok=True)
    env = harness_env(
        lay, spec, ports, passwords, bin_dir=lay.root.parent / "bin", passfile=lay.home / "pgpass"
    )
    harness = {
        "sandbox_profile": str(lay.profiles / "turn.sb"),
        "claude_config_dir": str(lay.work_state / "claude"),
        "pi_agent_dir": str(lay.work_state / "pi"),
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
        "extensions": list(spec.extensions),
        "ports": ports,
        "repo": spec.repo,
        "merge_url": spec.merge_url,
        "env": spec.env,
        "knowledge_base": str(kb) if kb else None,
    }
    return Provisioned(
        workspace=str(lay.repo),
        mirror=str(lay.mirror),
        # push_branch lands on the task's own bare origin; the merge lands on
        # the spec's merge_url when it has one, recorded here in the Brief.
        push_url=str(lay.origin),
        origin_url=spec.merge_url or str(lay.origin),
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
    """Each setup command once, in the clone, under the turn's profile, with
    the turn's environment; a failure is recorded, never fatal. A command
    ends when its own process ends, and the child it leaves running is
    reaped by the command's mark. Its stdout and stderr are one pipe, which
    the kernel copies whole into `<task root>/setup/<n>.log` until EOF,
    which comes once the reap has ended every process of the command. Each
    result names that file. There is no time limit; provisioning under
    `git.interruptible()` ends it when interrupted."""
    out: list[dict[str, Any]] = []
    lay.setup.mkdir(parents=True, exist_ok=True)
    for command in spec.setup:
        mark = f"setup-{task_id}-{len(out)}"
        argv, env = setup_command(harness, mark, command)
        held = git.watch()
        path = lay.setup / f"{len(out)}.log"
        with path.open("wb") as log:
            kwargs = {"cwd": lay.repo, "env": env, "stdin": subprocess.DEVNULL, "bufsize": 0,
                      "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}  # fmt: skip
            proc = git.start(argv, **kwargs)
            pump = threading.Thread(target=shutil.copyfileobj, args=(proc.stdout, log))
            pump.start()
            try:
                code = proc.wait()
            finally:
                if held:
                    held.finished(proc)
                runs.reap(mark)
                # Every process the command left has ended, so the pipe is at EOF.
                pump.join()
                proc.stdout.close()
            if held and held.interrupted:
                raise git.Interrupted()
        out.append({"command": command, "exit": code, "output": str(path)})
        if code != 0:
            return {"ok": False, "commands": out}
    return {"ok": True, "commands": out}


def setup_command(harness: dict[str, Any], mark: str, command: str) -> tuple[list[str], dict[str, str]]:
    """One setup command's argv and environment: under the harness's
    profile, marked `mark`, with the turn's environment."""
    argv = sandboxed(Path(current_profile(harness["sandbox_profile"])), mark, "/bin/bash", "-c", command)
    return argv, {**turn_environment(harness), runs.TURN_ENV: mark}


# The kernel's own credentials and Claude Code's own session state stay
# behind; the rest of the operator's environment is the turn's.
DROP_PREFIXES = ("ANTHROPIC_", "CLAUDE", "AI_AGENT")
DROP_NAMES = ("OPENAI_API_KEY", "TYPESAFE_API_KEY", "OPENROUTER_API_KEY", "GITHUB_PUSH_TOKEN",
              "PGPASSWORD", "PGPASSFILE", "VALOR_PG_PASSFILE")  # fmt: skip


def environment() -> dict[str, str]:
    """The operator's environment less the kernel's secrets: what a turn,
    and every sandboxed step of a task, starts from."""
    return {k: v for k, v in os.environ.items() if not k.startswith(DROP_PREFIXES) and k not in DROP_NAMES}


def turn_environment(harness: dict[str, Any]) -> dict[str, str]:
    """The environment a sandboxed step of the task (setup, suite, lint)
    gets: the turn's, the operator's less the kernel's secrets, and the
    task's own."""
    env = environment()
    env.update(harness.get("env", {}))
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


# -- services -----------------------------------------------------------------------------


def _pg(name: str) -> str:
    return str(Path(settings.pg_bin) / name)


def _service_run(lay: Layout, task_id: str, *argv: str) -> subprocess.CompletedProcess:
    """A service program under the service profile, to its end. There is no
    time limit: under `git.interruptible()` (provisioning, a run starting
    the services) an interrupt ends it, and `git.Interrupted` is raised.
    Its output goes to files the kernel holds (`git.output_file`), so the
    call ends when the program exits, whatever a daemon it started holds."""
    full = [binaries.require(binaries.SANDBOX_EXEC), "-f", str(lay.profiles / "service.sb"), *argv]
    held = git.watch()
    with git.output_file() as out, git.output_file() as err:
        proc = git.start(
            full,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            env={"PATH": "/usr/bin:/bin", "HOME": str(lay.pg), "LANG": "C", "LC_ALL": "C"},
        )
        try:
            proc.wait()
        finally:
            if held:
                held.finished(proc)
        if held and held.interrupted:
            raise git.Interrupted()
        stdout, stderr = git.read_output(out, True), git.read_output(err, True)
    return subprocess.CompletedProcess(full, proc.returncode, stdout, stderr)


# The initdb arguments every task cluster is made with, past `-D` and the
# password file.
INITDB_ARGS = ("-U", "postgres", "--auth=scram-sha-256", "-E", "UTF8", "--locale=C")
# Per process, per `_pg_template_key`: a template data directory and its
# superuser's password, or None where a clone failed (`_make_cluster`).
_PG_TEMPLATES: dict[str, tuple[Path, str] | None] = {}
_PG_TEMPLATES_LOCK = threading.Lock()


def _pg_template_key() -> str:
    """The Postgres a cluster is made by: `initdb` and `postgres` by real
    path (a Homebrew upgrade changes it) and file identity, and the initdb
    arguments."""
    h = hashlib.sha256()
    for name in ("initdb", "postgres"):
        real = os.path.realpath(_pg(name))
        st = os.stat(real)
        h.update(f"{real} {st.st_ino} {st.st_size} {st.st_mtime_ns}\n".encode())
    h.update(json.dumps(INITDB_ARGS).encode())
    return h.hexdigest()[:16]


def _clone_dir(src: Path, dest: Path) -> None:
    """`src` copied to a new `dest` as an APFS clone (`cp -c`), modes kept.
    Raises `subprocess.CalledProcessError` where the volume cannot clone."""
    subprocess.run(["/bin/cp", "-c", "-p", "-R", str(src), str(dest)], check=True, capture_output=True)


def _drop_pg_templates() -> None:
    with _PG_TEMPLATES_LOCK:
        held = [t[0] for t in _PG_TEMPLATES.values() if t is not None]
        _PG_TEMPLATES.clear()
    for path in held:
        shutil.rmtree(path, ignore_errors=True)


atexit.register(_drop_pg_templates)


def _drop_dead_templates(root: Path) -> None:
    """Remove the templates in `root` (named `<pid>-...`) whose process has
    exited without removing its own (a kernel killed)."""
    for entry in root.iterdir():
        pid = entry.name.split("-", 1)[0]
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            shutil.rmtree(entry, ignore_errors=True)
        except PermissionError:
            pass


def _make_cluster(lay: Layout, task_id: str, data: Path) -> str:
    """A new cluster at `data`, as `initdb` with `INITDB_ARGS` makes it;
    returns its superuser's password, which the caller replaces.

    The first cluster a process makes for a Postgres (`_pg_template_key`)
    is initdb'd under the task's service profile with a random password
    held in memory only, then cloned (`cp -c`) into a template under
    `settings.performing_dir`; the
    template is removed when the process exits. Each later cluster is a
    clone of the template. Where cloning fails (a volume that is not
    APFS), every cluster is initdb'd as before."""
    key = _pg_template_key()
    with _PG_TEMPLATES_LOCK:
        held = _PG_TEMPLATES.get(key, ())
    if held:
        template, password = held
        try:
            _clone_dir(template, data)
            return password
        except subprocess.CalledProcessError, OSError:
            shutil.rmtree(data, ignore_errors=True)
            with _PG_TEMPLATES_LOCK:
                _PG_TEMPLATES[key] = None
            shutil.rmtree(template, ignore_errors=True)
    password = secrets.token_urlsafe(24)
    pwfile = lay.pg / "init.pw"
    pwfile.write_text(password + "\n")
    pwfile.chmod(0o600)
    try:
        done = _service_run(
            lay, task_id, _pg("initdb"), "-D", str(data), f"--pwfile={pwfile}", *INITDB_ARGS
        )  # fmt: skip
        if done.returncode != 0:
            raise Refused(f"initdb: {done.stderr.strip()}")
    finally:
        pwfile.unlink(missing_ok=True)
    if held == ():  # no template yet, and none failed: this cluster becomes it
        Path(settings.performing_dir).mkdir(mode=0o700, parents=True, exist_ok=True)
        root = Path(settings.performing_dir) / "pg-template"
        root.mkdir(mode=0o700, exist_ok=True)
        _drop_dead_templates(root)
        template = root / f"{os.getpid()}-{key}-{secrets.token_hex(4)}"
        try:
            _clone_dir(data, template)
        except subprocess.CalledProcessError, OSError:
            shutil.rmtree(template, ignore_errors=True)
            made = None
        else:
            made = (template, password)
        with _PG_TEMPLATES_LOCK:
            if key not in _PG_TEMPLATES:
                _PG_TEMPLATES[key] = made
                made = None
        if made is not None:  # another thread's template is kept
            shutil.rmtree(made[0], ignore_errors=True)
    return password


def _init_postgres(lay: Layout, task_id: str, spec: Spec, port: int) -> dict[str, str]:
    """A cluster of the task's own with password auth on every login. The
    cluster is made by `_make_cluster` (a clone of the process's template
    cluster, or initdb), its superuser password replaced by a random one of
    the task's own as the server's first statement, and removed from the
    role once the roles exist. `app` is `CREATEDB` (and
    `CREATEROLE` with the spec's roles) and a member of `pg_signal_backend`
    and `pg_read_all_settings`, nothing more. The spec's extensions are
    created by the superuser in `template1`, so `app`'s database and every
    database it creates (Django's test database) hold them: `app` is not a
    superuser and cannot create an untrusted extension such as `vector`."""
    data = lay.pg / "data"
    lay.pg.mkdir(parents=True, exist_ok=True)
    made_pw = _make_cluster(lay, task_id, data)
    super_pw = secrets.token_urlsafe(24)
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
            host="127.0.0.1", port=port, dbname="postgres", user="postgres", password=made_pw,
            autocommit=True,
        ) as conn:  # fmt: skip
            conn.execute(sql.SQL("ALTER ROLE postgres PASSWORD {}").format(sql.Literal(super_pw)))
        if spec.extensions:
            with psycopg.connect(
                host="127.0.0.1", port=port, dbname="template1", user="postgres", password=super_pw,
                autocommit=True,
            ) as conn:  # fmt: skip
                for e in spec.extensions:
                    conn.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(sql.Identifier(e)))
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
            # A test fixture's `DROP DATABASE ... WITH (FORCE)` ends the
            # other backends on it (autovacuum's, a role `app` created), and
            # the kernel's suite reads `data_directory`. Never the
            # server-file or program roles: each reaches the server's files.
            conn.execute("GRANT pg_signal_backend, pg_read_all_settings TO app")
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
        raise Refused(f"redis-server did not start: {done.stderr.strip()}")
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
    """Start the task's services if they are down (idempotent), under
    today's service profile."""
    lay = lay or layout(task_id)
    if services:
        _write(lay.profiles / "service.sb", service_profile(task_id))
    if "postgres" in services:
        _start_postgres(lay, task_id)
    if "redis" in services:
        _start_redis(lay, task_id, int(ports["redis"]))


def stop_services(task_id: str, lay: Layout | None = None) -> list[dict[str, Any]]:
    """Stop the task's services: Postgres cleanly, then every process left
    under its service mark. Returns every process that was running under
    the mark: those Postgres stopped cleanly (`stopped`) and those the mark
    reaped (`SIGTERM`, `SIGKILL`). It runs even after an interrupt: it is
    the interrupted work's cleanup."""
    lay = lay or layout(task_id)
    mark = f"valor.service.{task_id}"
    before = runs.sandboxed_pids(mark, "valor.service.none")
    names = runs.commands(before)
    if (lay.profiles / "service.sb").exists():
        with git.uninterrupted():
            _stop_postgres(lay, task_id)
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


def fetch_into_mirror(
    mirror: str | Path,
    source: str | Path,
    sha: str,
    ref: str,
    turn_profile_path: str | Path,
    mark: str,
) -> str:
    """Fetch one commit from a clone a turn controls into the kernel mirror,
    under `ref`, and return its full id. `sha` is the commit's id or an
    abbreviation of it, resolved in the clone under the turn's profile.
    Raises `FetchRefused` with the reason."""
    source, mirror = Path(source), Path(mirror)
    if not re.fullmatch(r"[0-9a-f]{4,40}", sha):
        raise FetchRefused(f"{sha!r} is not a commit id")
    if not ref.startswith("refs/valor/"):
        raise FetchRefused(f"{ref} is not a mirror ref")
    if len(sha) < 40:
        try:
            full = git.commit_named(source, sha, turn_profile_path, mark)
        finally:
            runs.reap(mark)
        if full is None:
            raise FetchRefused(f"{sha!r} names no single commit in the clone")
        sha = full
    git_bin = git.binary()
    upload = " ".join(
        shlex.quote(a)
        for a in [
            binaries.require(binaries.SANDBOX_EXEC), "-D", "GATEWAY_PORT=1", "-D", f"VALOR_TURN={mark}",
            "-f", str(turn_profile_path), git_bin, "-c", "uploadpack.allowAnySHA1InWant=true", "upload-pack",
        ]
    )  # fmt: skip
    try:
        done = git._git(
            mirror,
            "-c", "transfer.fsckObjects=true", "-c", "fetch.fsckObjects=true", "-c", "protocol.file.allow=always",
            "fetch", "-q", "--no-tags", "--no-write-fetch-head", "--no-recurse-submodules",
            f"--upload-pack={upload}", str(source), f"+{sha}:{ref}",
        )  # fmt: skip
    finally:
        runs.reap(mark)
    if done.returncode != 0:
        raise FetchRefused(
            f"the fetch into the kernel mirror failed ({done.returncode}): {done.stderr.strip()}"
        )
    return sha


def bring_merged(
    repo: str | Path,
    mirror: str | Path,
    origin: str | Path,
    head: str,
    target: str,
    turn_profile_path: str | Path,
    mark: str,
) -> None:
    """Bring a merged head into the task's work branch, so the next round
    builds on what the merge landed: the candidate and the docs check's
    commits, which the kernel keeps only in its mirror. The kernel pushes
    the head from the mirror to the bare origin's `target` (already there
    when the merge landed on that origin), then git in `repo` fetches it and
    fast-forwards the work branch, under the turn's own profile. Raises
    `git.GitError` with the reason when the branch cannot fast-forward."""
    git.trusted(mirror, "push", "-q", "--no-verify", str(origin), f"{head}:refs/heads/{target}")
    prefix = [
        binaries.require(binaries.SANDBOX_EXEC), "-D", "GATEWAY_PORT=1", "-D", f"VALOR_TURN={mark}",
        "-f", str(turn_profile_path),
    ]  # fmt: skip
    try:
        for args in (
            ("fetch", "-q", "--no-tags", "--no-recurse-submodules", str(origin),
             f"+refs/heads/{target}:refs/remotes/origin/{target}"),
            ("merge", "-q", "--ff-only", "--no-edit", head),
        ):  # fmt: skip
            done = git._git(repo, *args, prefix=prefix)
            if done.returncode != 0:
                raise git.GitError(f"git {args[0]}: {done.stderr.strip()}")
    finally:
        runs.reap(mark)


VALOR_DIR = ".valor"


def fresh_dir(check_dir: Path) -> Path:
    """Remove and make a fresh session's directory with `rmtree`, so a
    read-only entry a sandboxed run left goes too, never following a link."""
    rmtree(check_dir)
    for d in (check_dir / "tmp", check_dir / "claude", check_dir / "pi"):
        d.mkdir(parents=True)
    return check_dir


def check_harness(
    lay: Layout, check_dir: Path, ports: list[int], env: dict[str, str], *, services: bool = False
) -> dict[str, Any]:
    """The harness settings of one fresh session: the turn profile, its own
    Claude Code config directory. A session that runs nothing against the
    task's services (critique) gets none of their ports and only the PATH
    of the task's environment, so no database credential. With `services`
    (test, review) the session gets the environment `check_services` made
    for its fresh instances and its caches under `<check_dir>/cache/`."""
    path = lay.profiles / "turn.sb"
    _write(path, turn_profile())
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
    return {
        "sandbox_profile": str(path),
        "claude_config_dir": str(check_dir / "claude"),
        "pi_agent_dir": str(check_dir / "pi"),
        "env": fresh_env,
    }


def setup_harness(lay: Layout, check_dir: Path, ports: list[int], harness: dict[str, Any]) -> dict[str, Any]:
    """The harness a fresh session's setup runs with: the session's own."""
    return harness


def write_inputs(checkout: Path, files: dict[str, str]) -> None:
    """Make `.valor/inputs/` in a checkout the kernel just made and write each
    input there, every step relative to a descriptor, nothing followed
    through a link, and no file overwritten."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC
    root = os.open(checkout, flags)
    fds = [root]
    try:
        with contextlib.suppress(FileExistsError):
            os.mkdir(VALOR_DIR, 0o755, dir_fd=root)
        valor = os.open(VALOR_DIR, flags, dir_fd=root)
        fds.append(valor)
        os.mkdir("inputs", 0o755, dir_fd=valor)
        inputs = os.open("inputs", flags, dir_fd=valor)
        fds.append(inputs)
        write_files(inputs, files)
    finally:
        for fd in reversed(fds):
            os.close(fd)


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
    """The components of a plain relative path, or None. A path holding a
    NUL character is not one: no file name holds NUL, and `os.open` raises
    on it."""
    parts = relpath.split("/")
    if relpath.startswith("/") or "\x00" in relpath or any(p in ("", ".", "..") for p in parts):
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
        extensions=tuple(project.get("extensions") or ()),
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
            (svc.profiles / "service.sb").write_text(service_profile(task_id))
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
    its `lstat`. Off macOS the owner has no such flag or ACL to clear, so it
    is the `lstat` alone."""
    if sys.platform != "darwin":
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
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
    """Remove the task's directory: a stopped or merged task's workspace, an
    orphan's, or one a provisioning that died left. What could still write
    into it or the shared cache is stopped first, since each runs in a
    session of its own and outlives a killed kernel: its services, its
    provisioning's git (the mark `provision-<task>`), and what its setup
    commands left (the mark `setup-<task>-<n>` of each `<n>.log` under
    `setup/`, opened before command `n` starts). The tree is cleared with
    `rmtree`, which follows no link and stops at no flag or ACL; a missing
    root is nothing to clear, a dangling link at the root is cleared."""
    lay = lay or layout(task_id)
    stop_services(task_id, lay)
    runs.reap(f"provision-{task_id}")
    if lay.setup.is_dir():
        for log in lay.setup.iterdir():
            if log.suffix == ".log" and log.stem.isdigit():
                runs.reap(f"setup-{task_id}-{log.stem}")
    rmtree(lay.root)


# -- other tasks' services --------------------------------------------------------------------


async def sweep(conn, task_id: str, work: Path | None = None, *, after_scan=None) -> list[dict[str, Any]]:
    """Stop the services a killed kernel left up: those of every other task
    whose run is not live and whose services no kernel keeps (its router
    lock `run:<task>` and its `services:<task>` can both be taken), and
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
        # A task started by message carries its workspace in its latest
        # `workspace.provisioned` row, laid over the document (`tasks.brief`).
        rows = await (
            await conn.execute(
                "SELECT id, body->>'mirror' FROM (SELECT d.id, d.body || COALESCE(("
                "  SELECT e.payload->'fields' FROM events e WHERE e.task_id = d.id "
                "  AND e.type = 'workspace.provisioned' ORDER BY e.id DESC LIMIT 1), '{}'::jsonb) AS body "
                "  FROM documents d WHERE d.kind = 'task' AND d.id <> %s "
                "  AND NOT EXISTS (SELECT 1 FROM events e WHERE e.task_id = d.id "
                "  AND e.type = 'workspace.removed')) t "
                "WHERE jsonb_array_length(COALESCE(body->'project'->'services', '[]'::jsonb)) > 0",
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
            # A task's services are another process's while its run is live
            # or while a kernel keeps them up between steps.
            keys = [f"{kind}:{other}"] + ([f"services:{other}"] if kind == "run" else [])
            taken = []
            for key in keys:
                got = await (
                    await conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,))
                ).fetchone()
                if not got[0]:
                    break
                taken.append(key)
            if len(taken) < len(keys):
                for key in taken:
                    await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))
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
                for key in taken:
                    await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))
    finally:
        await conn.execute("SELECT pg_advisory_unlock(hashtextextended('workspace:ports', 0))")
    return stopped
