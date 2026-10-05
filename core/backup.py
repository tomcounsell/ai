"""Backups of the kernel database, and the scratch clusters a restore runs in.

Serves "Reliable stop, recovery, and correction": a ledger nobody can edit
is still lost with the disk. The ledger itself is kept forever and never
pruned; the dumps of it are kept to `settings.backup_keep`.

`dump` writes one `pg_dump` custom-format file to `settings.backup_dir` (an
external disk) and a manifest beside it: the event and document counts and
SHA-256 digests, computed from the same snapshot the dump reads, and the
dump file's own SHA-256. A dump is written as `.partial` and renamed only
after its manifest is on disk, so a complete name always has a manifest.
Pruning considers only complete pairs. `restore` checks a dump against its
manifest, restores it into a scratch cluster, recomputes the digests, and
compares.

`backup_dir` is never created: an unmounted volume must fail the dump, not
fill the boot disk. Names carry no colons, since the disk is exFAT, and
exFAT's `._` AppleDouble files never match a dump's name.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg import sql

from core.settings import settings

ROOT = Path(__file__).resolve().parent.parent
LABEL = "com.valor.backup"

DIGESTS = {
    "events": (
        "SELECT count(*), COALESCE(max(id), 0), encode(sha256(convert_to(COALESCE(string_agg("
        "id::text || '|' || task_id || '|' || type || '|' || payload::text || '|' || at::text, "
        "E'\\n' ORDER BY id), ''), 'UTF8')), 'hex') FROM events"
    ),
    "documents": (
        "SELECT count(*), 0, encode(sha256(convert_to(COALESCE(string_agg("
        "kind || '|' || id || '|' || body::text || '|' || created_at::text, "
        "E'\\n' ORDER BY kind, id), ''), 'UTF8')), 'hex') FROM documents"
    ),
}


class BackupError(RuntimeError):
    pass


def pattern(database: str) -> re.Pattern:
    return re.compile(rf"^{re.escape(database)}-\d{{8}}T\d{{6}}Z\.dump$")


def digests(conn: psycopg.Connection) -> dict:
    """Counts and digests of both tables, as this connection sees them."""
    conn.execute("SET TIME ZONE 'UTC'")
    out = {}
    for table, query in DIGESTS.items():
        rows, max_id, digest = conn.execute(query).fetchone()
        out[table] = {"rows": rows, "sha256": digest}
        if table == "events":
            out[table]["max_id"] = max_id
    return out


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fsync_write(path: Path, text: str) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _check_disks(cluster_data: Path, backup_dir: Path) -> None:
    """The `pg_data_dir` setting must name the cluster's real data directory,
    since both turn sandbox profiles deny that setting's path (after a
    Postgres upgrade it goes stale), and a dump must land on another disk."""
    expected = Path(settings.pg_data_dir)
    if not expected.exists() or expected.resolve() != cluster_data.resolve():
        raise BackupError(
            f"the pg_data_dir setting ({expected}) is not the cluster's data directory ({cluster_data}); "
            "the turn sandbox profiles deny the setting's path, so correct the setting "
            "(after a Postgres upgrade, also run `python -m core secure-login`)"
        )
    if os.stat(backup_dir).st_dev == os.stat(cluster_data).st_dev:
        raise BackupError(
            f"{backup_dir} is on the same disk as the cluster's data ({cluster_data}); not a backup"
        )


def dump(
    *,
    backup_dir: str | Path | None = None,
    database: str | None = None,
    dsn: str | None = None,
    pg_bin: str | None = None,
    keep: int | None = None,
    now: datetime | None = None,
) -> tuple[Path, dict, list[Path]]:
    """Dump the kernel database, then prune. Returns the dump's path, its
    manifest, and the files pruned. One dump at a time per database and
    backup directory, under an exclusive lock on `.<database>.lock` there
    (the disk is exFAT, which has neither hard links nor an exclusive
    rename, so the lock is what keeps two dumps from taking one name)."""
    backup_dir = Path(backup_dir or settings.backup_dir)
    database = database or settings.database
    dsn = dsn or settings.dsn(owner=True, database=database)
    pg_bin = Path(pg_bin or settings.pg_bin)
    keep = settings.backup_keep if keep is None else keep
    if not backup_dir.is_dir():
        raise BackupError(f"backup directory {backup_dir} does not exist (is the disk mounted?)")
    with open(backup_dir / f".{database}.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
        final = backup_dir / f"{database}-{stamp}.dump"
        manifest_path = Path(f"{final}.json")
        partial = Path(f"{final}.partial")
        if final.exists() or manifest_path.exists():
            raise BackupError(f"{final} already exists")
        with psycopg.connect(dsn) as conn:
            _check_disks(Path(conn.execute("SHOW data_directory").fetchone()[0]), backup_dir)
            conn.rollback()
            fd = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
                snapshot = conn.execute("SELECT pg_export_snapshot()").fetchone()[0]
                tables = digests(conn)
                dumped = subprocess.run(
                    [str(pg_bin / "pg_dump"), "--format=custom", f"--snapshot={snapshot}", f"--dbname={dsn}"],
                    stdout=fd,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
                conn.rollback()
                if dumped.returncode != 0:
                    raise BackupError(f"pg_dump failed: {dumped.stderr.strip()}")
                os.fsync(fd)
            except BaseException:
                os.close(fd)
                partial.unlink(missing_ok=True)
                raise
            os.close(fd)
        version = subprocess.run(
            [str(pg_bin / "pg_dump"), "--version"], capture_output=True, text=True, check=False
        ).stdout.strip()
        manifest = {
            "database": database,
            "at": stamp,
            "pg_dump": version,
            "dump_sha256": file_sha256(partial),
            "dump_bytes": partial.stat().st_size,
            **tables,
        }
        staged = Path(f"{manifest_path}.partial")
        _fsync_write(staged, json.dumps(manifest, indent=2) + "\n")
        os.replace(staged, manifest_path)
        os.replace(partial, final)
        _fsync_dir(backup_dir)
        return final, manifest, prune(backup_dir, database, keep)


def prune(backup_dir: Path, database: str, keep: int) -> list[Path]:
    """Delete complete dump and manifest pairs past the newest `keep`.
    Anything that is not a complete pair of ours is left alone."""
    match = pattern(database)
    pairs = sorted(
        (p for p in backup_dir.iterdir() if match.match(p.name) and Path(f"{p}.json").is_file()),
        key=lambda p: p.name,
        reverse=True,
    )
    removed = []
    for p in pairs[keep:]:
        for f in (p, Path(f"{p}.json")):
            f.unlink()
            removed.append(f)
    return removed


@dataclass
class Cluster:
    """A running scratch Postgres cluster."""

    root: Path
    data: Path
    host: str
    port: int
    owner: str

    def dsn(self, database: str = "postgres", user: str | None = None) -> str:
        return f"host={self.host} port={self.port} dbname={database} user={user or self.owner}"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_cluster(
    pg_bin: str | Path | None = None, *, tcp: bool = False, prefix: str = "vk-", port: int | None = None
) -> Cluster:
    """`initdb` and start a throwaway cluster that trusts local logins, the
    way the machine cluster does. Its directory is
    `<pg_scratch>/<prefix><random>`, so a caller can find its own; the
    `pg_scratch` setting is inside the kernel key directory, which no
    sandbox profile can read or write, since `initdb`, `pg_ctl` and the
    server run here outside any sandbox. Its socket sits in that directory,
    a short path, since a socket path past 103 bytes does not bind on
    macOS. With `tcp` it also listens on 127.0.0.1 and ::1 at `port`, a
    free one when none is given.
    Its log (`postgres.log` in `root`) prefixes each line with the
    SQLSTATE. Its bootstrap superuser is `settings.owner_role`, the role
    that owns the kernel databases."""
    pg_bin = Path(pg_bin or settings.pg_bin)
    scratch = Path(settings.pg_scratch)
    scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix=prefix, dir=scratch))
    data = root / "data"
    owner = settings.owner_role
    try:
        subprocess.run(
            [
                str(pg_bin / "initdb"),
                "-D",
                str(data),
                "-U",
                owner,
                "--auth=trust",
                "-E",
                "UTF8",
                "--locale=C",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        port = (port or _free_port()) if tcp else 5432
        with open(data / "postgresql.conf", "a") as conf:
            conf.write(
                f"\nlisten_addresses = '{'127.0.0.1,::1' if tcp else ''}'\n"
                f"port = {port}\nunix_socket_directories = '{root}'\n"
                "log_line_prefix = '%m [%p] %e '\n"
            )
        subprocess.run(
            [str(pg_bin / "pg_ctl"), "-D", str(data), "-l", str(root / "postgres.log"), "-w", "start"],
            capture_output=True,
            text=True,
            check=True,
        )
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return Cluster(root=root, data=data, host=str(root), port=port, owner=owner)


def stop_cluster(cluster: Cluster, pg_bin: str | Path | None = None) -> None:
    pg_bin = Path(pg_bin or settings.pg_bin)
    subprocess.run(
        [str(pg_bin / "pg_ctl"), "-D", str(cluster.data), "-m", "immediate", "-w", "stop"],
        capture_output=True,
        check=False,
    )
    shutil.rmtree(cluster.root, ignore_errors=True)


@contextlib.contextmanager
def scratch_cluster(
    pg_bin: str | Path | None = None, *, tcp: bool = False, prefix: str = "vk-", port: int | None = None
) -> Iterator[Cluster]:
    cluster = start_cluster(pg_bin, tcp=tcp, prefix=prefix, port=port)
    try:
        yield cluster
    finally:
        stop_cluster(cluster, pg_bin)


def restore(
    dump_path: str | Path, *, pg_bin: str | Path | None = None, keep: bool = False, prefix: str = "vk-"
) -> dict:
    """Restore a dump into a scratch cluster and compare it with its
    manifest. Raises `BackupError` on any difference or failure, with the
    cluster removed. Returns a summary, which names the cluster when `keep`
    leaves it running. `prefix` names the scratch cluster's directory
    under the `pg_scratch` setting (see `start_cluster`)."""
    dump_path = Path(dump_path)
    manifest_path = Path(f"{dump_path}.json")
    if not manifest_path.is_file():
        raise BackupError(f"no manifest {manifest_path}")
    manifest = json.loads(manifest_path.read_text())
    if file_sha256(dump_path) != manifest["dump_sha256"]:
        raise BackupError(f"{dump_path} does not match its manifest's SHA-256 (truncated or altered)")
    cluster = start_cluster(pg_bin, prefix=prefix)
    try:
        database = manifest["database"]
        with psycopg.connect(cluster.dsn(), autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(settings.kernel_role)))
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        restored = subprocess.run(
            [
                str(Path(pg_bin or settings.pg_bin) / "pg_restore"),
                "--exit-on-error",
                "--no-owner",
                f"--dbname={cluster.dsn(database)}",
                str(dump_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if restored.returncode != 0:
            raise BackupError(f"pg_restore failed: {restored.stderr.strip()}")
        with psycopg.connect(cluster.dsn(database)) as conn:
            found = digests(conn)
        for table in DIGESTS:
            if found[table] != manifest[table]:
                raise BackupError(f"{table} differs: manifest {manifest[table]}, restored {found[table]}")
    except BaseException:
        stop_cluster(cluster, pg_bin)
        raise
    summary = {
        "dump": str(dump_path),
        "database": database,
        "events": found["events"]["rows"],
        "max_event_id": found["events"]["max_id"],
        "documents": found["documents"]["rows"],
        "match": True,
    }
    if keep:
        summary["cluster"] = {"socket_dir": cluster.host, "port": cluster.port, "data": str(cluster.data)}
    else:
        stop_cluster(cluster, pg_bin)
    return summary


# Settings a launchd job needs carried in its environment: launchd starts
# jobs with almost none, and an override Tom set when he generated the plist
# has to reach the job too.
PLIST_ENV = (
    "VALOR_PGHOST",
    "VALOR_PGPORT",
    "VALOR_DB",
    "VALOR_PG_OWNER",
    "VALOR_PG_BIN",
    "VALOR_PG_DATA",
    "VALOR_PG_PASSFILE",
    "VALOR_BACKUP_DIR",
    "VALOR_BACKUP_KEEP",
)


def plist(*, python: str | None = None, root: Path = ROOT) -> bytes:
    """The launchd job: `python -m core backup` at 03:00 every day, from
    this checkout. A job missed while the Mac slept runs once on wake."""
    env = {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    env.update({k: os.environ[k] for k in PLIST_ENV if k in os.environ})
    log = Path(settings.log_dir) / "backup.log"
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": [python or sys.executable, "-m", "core", "backup"],
            "WorkingDirectory": str(root),
            "EnvironmentVariables": env,
            "StartCalendarInterval": {"Hour": 3, "Minute": 0},
            "StandardOutPath": str(log),
            "StandardErrorPath": str(log),
        }
    )
