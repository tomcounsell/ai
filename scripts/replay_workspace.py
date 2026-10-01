"""A replay's workspace: one repository at one base commit with no history
after it, a local bare repository as its only remote, the services its
tests need, and the isolation every turn runs under.

    .venv/bin/python scripts/replay_workspace.py OWNER/NAME BASE_SHA RUN_NAME \\
        [--services postgres,redis | none] [--branch valor/work] [--rebuild]
    .venv/bin/python scripts/replay_workspace.py --teardown RUN_NAME

OWNER/NAME may also be a local git repository's path (the smoke's toy repo).

Layout under $VALOR_DEMO (default /Users/tomcounsell/src/valor-demo):

    cache/<owner>__<name>.git   bare clone from GitHub, shared by every run of
                                the repository; fetched by SHA when a base is
                                missing. A turn cannot read it.
    runs/<run>/<name>/          the clone Valor works in: only the history up
                                to the base, no tags, no remote but `origin`
    runs/<run>/origin.git/      bare repository, the clone's `origin`, `main` at
                                the base; written only by the broker's
                                push_branch performer, after Tom's tap
    runs/<run>/home/            gitconfig, an empty gh config, sandbox.sb, and
                                harness.json (`core start --harness-config`);
                                a turn can read it and not write it
    runs/<run>/replay.json      what was built, for the driver and the judge
    pg/                         one Postgres cluster for every workspace, on
                                127.0.0.1:5439, password auth (made by
                                scripts/demo_workspace.sh); each run gets its
                                own database, owned by the `test` role
    bin/                        binaries every run shares (uv): first on the
                                turn's PATH, readable, not writable
    redis/                      redis-servers on 127.0.0.1, one per run that
                                asks for Redis (6391 to 6399; 6390 for runs
                                built before that), no persistence; logs, pids

The sandbox (`sandbox_profile`) lets a turn read and write its own run
directory and its own Claude Code transcripts and nothing else under ~/src
(other runs, the caches, the answer keys, results, Tom's checkouts), nor his
notes or keys. On loopback it reaches the gateway, 8000 to 8009, and the
services the run asked for; this Mac's own Postgres (5432) and Redis (6379)
stay out of reach. Denies come before allows: a network rule after the
loopback allows makes sandbox-exec refuse allowed ports at random.

A turn may listen only on 8000 to 8009 and on unix sockets inside its run.
sandbox-exec matches a bind on its port alone: `localhost:8001` admits
0.0.0.0:8001 and the LAN address too, and a literal address does not parse,
so the dev ports are the one place a turn can listen beyond loopback, and
only while the turn lasts (the kernel reaps what a turn leaves running).
The profile also denies the mach name `valor.turn.<VALOR_TURN>`, which
names the turn's processes to the reaper even after they daemonize.

Live spend: none. Network: a clone or fetch from GitHub, run as Tom.
"""

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay_common import DEMO, git, now, ok, sh

PG_BIN = Path("/opt/homebrew/opt/postgresql@18/bin")
PG_PORT = 5439
REDIS_PORT = 6390
REDIS_PORTS = range(6391, 6400)
DEV_PORTS = range(8000, 8010)
SERVICES = ("postgres", "redis")
HOME = Path.home()
TOOLS = DEMO / "bin"


def transcripts_dir(workdir: Path) -> Path:
    """Where Claude Code keeps the sessions of a turn run in `workdir`."""
    return HOME / ".claude" / "projects" / re.sub(r"[/.]", "-", str(workdir))


def sandbox_profile(
    *, run: Path, workdir: Path, ports: list[int], home: Path = HOME, tools: Path = TOOLS
) -> str:
    """The sandbox-exec profile for a turn working in `workdir` inside
    `run`; `GATEWAY_PORT` is a parameter, since the gateway takes whatever
    port the OS hands it. `tools` (binaries such as uv that the replays
    share) is readable and runnable, not writable. The run's ancestors are
    stat-able, not listable, so tools that resolve real paths (uv making a
    virtualenv) work inside the run."""
    denied = [
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
        ".claude/projects",
        ".claude/plans",
        ".claude/file-history",
    ]
    lines = [
        "(version 1)",
        "(allow default)",
        "(deny file-read* file-write*",
        *(f'    (subpath "{home / d}")' for d in denied),
        f'    (literal "{home / ".claude/history.jsonl"}"))',
        "(allow file-read* file-write*",
        f'    (subpath "{run}")',
        f'    (subpath "{transcripts_dir(workdir)}"))',
        f'(allow file-read* (subpath "{tools}"))',
        "(allow file-read-metadata",
        *(f'    (literal "{d}")' for d in reversed(run.parents)),
        ")",
        "(deny file-write*",
        f'    (subpath "{run / "origin.git"}")',
        f'    (subpath "{run / "home"}")',
        f'    (literal "{run / "replay.json"}"))',
        '(deny process-exec (regex #"/git-credential-osxkeychain$"))',
        '(deny mach-lookup (global-name (string-append "valor.turn." (param "VALOR_TURN"))))',
        "(deny network-bind network-inbound)",
        "(allow network-bind network-inbound",
        *(f'    (local ip "localhost:{p}")' for p in DEV_PORTS),
        f'    (local unix-socket (subpath "{run}")))',
        "(deny network-outbound",
        '    (remote ip "localhost:*")',
        '    (remote unix-socket (path-literal "/private/tmp/.s.PGSQL.5432"))',
        '    (remote unix-socket (path-literal "/tmp/.s.PGSQL.5432")))',
        "(allow network-outbound",
        '    (remote ip (string-append "localhost:" (param "GATEWAY_PORT")))',
        *(f'    (remote ip "localhost:{p}")' for p in ports),
    ]
    lines[-1] += ")"
    return "\n".join(lines) + "\n"


def ports_for(services: list[str], redis_port: int = REDIS_PORT) -> list[int]:
    ports = list(DEV_PORTS)
    if "postgres" in services:
        ports.append(PG_PORT)
    if "redis" in services:
        ports.append(redis_port)
    return ports


def db_name(run_name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", run_name.lower())


# -- services ------------------------------------------------------------------


def _psql(sql: str) -> str:
    return sh(
        str(PG_BIN / "psql"),
        "-h",
        "127.0.0.1",
        "-p",
        str(PG_PORT),
        "-U",
        "test",
        "-d",
        "postgres",
        "-qAt",
        "-c",
        sql,
        env={"PGPASSWORD": "test", "PATH": "/usr/bin:/bin"},
    )


def ensure_postgres() -> None:
    data = DEMO / "pg" / "data"
    if not (data / "PG_VERSION").exists():
        raise SystemExit(f"no Postgres cluster at {data}; scripts/demo_workspace.sh creates it")
    if not ok(str(PG_BIN / "pg_ctl"), "-D", str(data), "status"):
        sh(str(PG_BIN / "pg_ctl"), "-D", str(data), "-l", str(DEMO / "pg" / "postgres.log"), "-w", "start")


def ensure_redis(port: int = REDIS_PORT) -> None:
    """A redis-server of the replays' own on 127.0.0.1:6390: no persistence,
    no unix socket, and the protected configs (`dir`, `dbfilename`), DEBUG,
    and MODULE closed, since the server runs outside the sandbox and those
    would let a turn write files through it."""
    if sh("redis-cli", "-p", str(port), "ping", check=False) == "PONG":
        return
    d = DEMO / "redis"
    d.mkdir(parents=True, exist_ok=True)
    sh(
        "redis-server",
        "--port",
        str(port),
        "--bind",
        "127.0.0.1",
        "--protected-mode",
        "yes",
        "--save",
        "",
        "--appendonly",
        "no",
        "--dir",
        str(d),
        "--enable-protected-configs",
        "no",
        "--enable-debug-command",
        "no",
        "--enable-module-command",
        "no",
        "--daemonize",
        "yes",
        "--pidfile",
        str(d / f"redis-{port}.pid"),
        "--logfile",
        str(d / f"redis-{port}.log"),
    )
    for _ in range(50):
        if sh("redis-cli", "-p", str(port), "ping", check=False) == "PONG":
            return
        sh("sleep", "0.1")
    raise SystemExit(f"redis-server did not come up on {port}; see {d / f'redis-{port}.log'}")


def ensure_services(services: list[str], redis_port: int = REDIS_PORT) -> None:
    if "postgres" in services:
        ensure_postgres()
    if "redis" in services:
        ensure_redis(redis_port)


def _redis_port(run: Path) -> int:
    """The run's own redis-server port, so concurrent runs never flush each
    other's data: the one its replay.json records, 6390 for a run built
    before ports were per run, else the lowest port no other run holds."""
    own = run / "replay.json"
    if own.exists():
        return json.loads(own.read_text()).get("redis_port", REDIS_PORT)
    taken = {
        json.loads(f.read_text()).get("redis_port", REDIS_PORT) for f in (DEMO / "runs").glob("*/replay.json")
    }
    free = [p for p in REDIS_PORTS if p not in taken]
    if not free:
        raise SystemExit(f"no free replay Redis port in {REDIS_PORTS}")
    return free[0]


# -- the clone -----------------------------------------------------------------


def _cache(repo: str, base: str) -> Path:
    """A bare clone of `repo` holding `base`, shared by every run of it."""
    local = Path(repo).expanduser()
    name = local.resolve().name.removesuffix(".git") if local.exists() else repo.replace("/", "__")
    cache = DEMO / "cache" / f"{name}.git"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        if local.exists():
            sh("git", "clone", "--quiet", "--bare", str(local.resolve()), str(cache))
        else:
            sh("gh", "repo", "clone", repo, str(cache), "--", "--quiet", "--bare")
    if not _has(cache, base):
        if local.exists():
            git(cache, "fetch", "--quiet", str(local.resolve()), "+refs/heads/*:refs/heads/*")
        else:
            git(
                cache,
                "-c",
                "credential.helper=",
                "-c",
                "credential.helper=!gh auth git-credential",
                "fetch",
                "--quiet",
                f"https://github.com/{repo}.git",
                base,
            )
    if not _has(cache, base):
        raise SystemExit(f"{base} is not a commit of {repo}")
    return cache


def _has(repo: Path, sha: str) -> bool:
    return ok("git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}")


def build(
    repo: str,
    base: str,
    run_name: str,
    services: list[str],
    *,
    branch: str = "valor/work",
    max_output_tokens: int | None = None,
    rebuild: bool = False,
) -> dict:
    """Build what is missing of the run's workspace (all of it after
    `rebuild`), start its services, and return its replay.json."""
    unknown = set(services) - set(SERVICES)
    if unknown:
        raise SystemExit(f"unknown services {sorted(unknown)}; known: {', '.join(SERVICES)}")
    run = DEMO / "runs" / run_name
    if rebuild:
        teardown(run_name)
    redis_port = _redis_port(run) if "redis" in services else REDIS_PORT
    ensure_services(services, redis_port)
    cache = _cache(repo, base)
    base = git(cache, "rev-parse", f"{base}^{{commit}}")
    workdir = run / Path(repo).name.removesuffix(".git")

    if not workdir.exists():
        run.mkdir(parents=True, exist_ok=True)
        ref = f"refs/heads/replay/{base}"
        git(cache, "update-ref", ref, base)
        try:
            sh(
                "git",
                "clone",
                "--quiet",
                "--no-tags",
                "--single-branch",
                "--branch",
                f"replay/{base}",
                f"file://{cache}",
                str(workdir),
            )
        finally:
            git(cache, "update-ref", "-d", ref)
        git(workdir, "checkout", "--quiet", "-b", branch)
        git(workdir, "branch", "--quiet", "-D", f"replay/{base}")
        git(workdir, "remote", "remove", "origin")
        git(workdir, "reflog", "expire", "--expire=now", "--all")
        git(workdir, "gc", "--quiet", "--prune=now")
        with (workdir / ".git" / "info" / "exclude").open("a") as f:
            f.write(".valor/\n")
        origin = run / "origin.git"
        sh("git", "init", "--quiet", "--bare", str(origin))
        git(origin, "config", "core.logAllRefUpdates", "always")
        git(origin, "config", "receive.denyNonFastForwards", "true")
        git(workdir, "remote", "add", "origin", str(origin))
        git(workdir, "push", "--quiet", "origin", f"{base}:refs/heads/main")
        git(workdir, "fetch", "--quiet", "origin")

    home = run / "home"
    (home / "gh").mkdir(parents=True, exist_ok=True)
    (home / "gitconfig").write_text(
        "[user]\n\tname = Valor Engels\n\temail = valor@yuda.me\n[init]\n\tdefaultBranch = main\n"
    )
    (home / "sandbox.sb").write_text(
        sandbox_profile(run=run, workdir=workdir, ports=ports_for(services, redis_port))
    )
    env: dict[str, str] = {"PATH": f"{TOOLS}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
    if "postgres" in services:
        name = db_name(run_name)
        if not _psql(f"SELECT 1 FROM pg_database WHERE datname = '{name}'"):
            _psql(f'CREATE DATABASE "{name}"')
        env.update(
            {
                "TEST_DB_HOST": "127.0.0.1",
                "TEST_DB_PORT": str(PG_PORT),
                "TEST_DB_USER": "test",
                "TEST_DB_PASSWORD": "test",
                "TEST_DB_NAME": f"test_{name}",
                "DATABASE_URL": f"postgresql://test:test@127.0.0.1:{PG_PORT}/{name}",
                "PGHOST": "127.0.0.1",
                "PGPORT": str(PG_PORT),
                "PGUSER": "test",
                "PGPASSWORD": "test",
                "PGDATABASE": name,
            }
        )
    if "redis" in services:
        env.update(
            {
                "REDIS_URL": f"redis://127.0.0.1:{redis_port}/0",
                "REDIS_HOST": "127.0.0.1",
                "REDIS_PORT": str(redis_port),
            }
        )
    harness = {
        "sandbox_profile": str(home / "sandbox.sb"),
        "gitconfig": str(home / "gitconfig"),
        "gh_config_dir": str(home / "gh"),
        "env": env,
    }
    if max_output_tokens:
        harness["max_output_tokens"] = max_output_tokens
    (home / "harness.json").write_text(json.dumps(harness, indent=2) + "\n")

    info = {
        "run": run_name,
        "repo": repo,
        "base": base,
        "branch": branch,
        "services": services,
        **({"redis_port": redis_port} if "redis" in services else {}),
        "run_dir": str(run),
        "workdir": str(workdir),
        "origin": str(run / "origin.git"),
        "harness_config": str(home / "harness.json"),
        "built_at": now(),
    }
    replay_json = run / "replay.json"
    if replay_json.exists():
        info["built_at"] = json.loads(replay_json.read_text())["built_at"]
    replay_json.write_text(json.dumps(info, indent=2) + "\n")
    after = git(workdir, "rev-list", "--all", "--not", base)
    print(
        f"workspace {workdir} on {git(workdir, 'branch', '--show-current')} at {base[:10]}; "
        f"commits after the base: {len(after.split()) if after else 0}; services: {', '.join(services) or 'none'}",
        file=sys.stderr,
    )
    return info


def teardown(run_name: str) -> None:
    run = DEMO / "runs" / run_name
    if (DEMO / "pg" / "data" / "PG_VERSION").exists():
        ensure_postgres()
        _psql(f'DROP DATABASE IF EXISTS "{db_name(run_name)}" WITH (FORCE)')
    if run.exists():
        shutil.rmtree(run)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("repo", nargs="?")
    parser.add_argument("base", nargs="?")
    parser.add_argument("run_name", nargs="?")
    parser.add_argument("--services", default="none", help="comma list of postgres, redis; or none")
    parser.add_argument("--branch", default="valor/work")
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--teardown", metavar="RUN_NAME")
    args = parser.parse_args()
    if args.teardown:
        teardown(args.teardown)
        print(f"run {args.teardown} deleted")
        return
    if not (args.repo and args.base and args.run_name):
        parser.error("REPO BASE RUN_NAME are required")
    services = [] if args.services == "none" else [s for s in args.services.split(",") if s]
    info = build(
        args.repo,
        args.base,
        args.run_name,
        services,
        branch=args.branch,
        max_output_tokens=args.max_output_tokens,
        rebuild=args.rebuild,
    )
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
