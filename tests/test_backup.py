"""Backups on real Postgres: dumps of the test database onto a RAM disk,
restores into scratch clusters.

A dump refuses a directory on the cluster's own disk, so the dumps go to a
RAM disk this module attaches (a separate device, as the real backup disk
is) and ejects when it is done. Each restore names its scratch cluster with
a prefix of the test's own, so a test checks only the cluster it started,
never another agent's.

Live spend: none.
"""

import asyncio
import dataclasses
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from core import backup, db, ledger, tasks
from core.settings import settings
from tests.conftest import TEST_DB

pytestmark = [
    pytest.mark.spend(usd=0),
    pytest.mark.skipif(shutil.which("diskutil") is None, reason="needs macOS diskutil for a RAM disk"),
]

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 2, 3, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def ramdisk():
    attached = subprocess.run(
        ["diskutil", "image", "attach", "--noMount", "ram://65536"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    device = re.search(r"/dev/disk\d+", attached).group(0)
    name = f"vkbackup{os.getpid()}"
    try:
        subprocess.run(["diskutil", "erasevolume", "HFS+", name, device], capture_output=True, check=True)
        yield Path("/Volumes") / name
    finally:
        subprocess.run(["diskutil", "eject", device], capture_output=True, check=False)


@pytest.fixture
def backups(ramdisk):
    d = ramdisk / uuid.uuid4().hex[:8]
    d.mkdir()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def prefix():
    """A scratch-cluster prefix only this test uses."""
    return f"vk-{uuid.uuid4().hex[:6]}-"


def _mine(prefix: str) -> list[Path]:
    return list(Path(settings.pg_scratch).glob(f"{prefix}*"))


def _dump(backup_dir, **kw):
    args = {
        "backup_dir": backup_dir,
        "database": TEST_DB,
        "dsn": settings.dsn(owner=True, database=TEST_DB),
        "now": NOW,
        **kw,
    }
    return backup.dump(**args)


def _fake_bin(tmp_path: Path, script: str) -> Path:
    """A bin directory whose `pg_dump` is `script`; the rest is real."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("pg_restore", "initdb", "pg_ctl"):
        (bin_dir / name).symlink_to(Path(settings.pg_bin) / name)
    fake = bin_dir / "pg_dump"
    fake.write_text("#!/bin/sh\n" + script)
    fake.chmod(0o755)
    return bin_dir


def _pairs(d: Path, count: int, start: datetime) -> list[Path]:
    made = []
    for i in range(count):
        name = d / f"{TEST_DB}-{(start - timedelta(days=i + 1)).strftime('%Y%m%dT%H%M%SZ')}.dump"
        name.write_text("old")
        Path(f"{name}.json").write_text("{}")
        made.append(name)
    return made


def _cli(*args, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "core", *args],
        cwd=ROOT,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def populated(dsn):
    async def go():
        async with await db.connect(dsn) as conn:
            await tasks.start(conn, tasks.Brief(instruction="backed up"))

    asyncio.run(go())
    return dsn


# -- dump and restore --------------------------------------------------------------


def test_a_dump_restores_into_a_scratch_cluster_and_matches_its_manifest(populated, backups, prefix):
    path, manifest, pruned = _dump(backups)
    assert path.name == f"{TEST_DB}-20261002T030000Z.dump" and pruned == []
    assert json.loads(Path(f"{path}.json").read_text()) == manifest
    assert manifest["dump_sha256"] == backup.file_sha256(path) and manifest["events"]["rows"] > 0
    assert not list(backups.glob("*.partial"))
    summary = backup.restore(path, prefix=prefix)
    assert summary["match"] is True and summary["events"] == manifest["events"]["rows"]
    assert "cluster" not in summary and _mine(prefix) == []


def test_restore_with_keep_leaves_its_cluster_running_and_names_it(populated, backups, prefix):
    path, manifest, _ = _dump(backups)
    summary = backup.restore(path, prefix=prefix, keep=True)
    (root,) = _mine(prefix)
    try:
        cluster = summary["cluster"]
        assert cluster["socket_dir"] == str(root)
        dsn = f"host={cluster['socket_dir']} port={cluster['port']} dbname={TEST_DB}"
        with psycopg.connect(dsn) as conn:
            assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == manifest["events"]["rows"]
    finally:
        backup.stop_cluster(
            backup.Cluster(
                root=root, data=Path(cluster["data"]), host=str(root), port=cluster["port"], owner=""
            )
        )
    assert _mine(prefix) == []


def test_a_dump_without_its_manifest_is_refused(populated, backups, prefix):
    path, _, _ = _dump(backups)
    Path(f"{path}.json").unlink()
    with pytest.raises(backup.BackupError, match="no manifest"):
        backup.restore(path, prefix=prefix)
    assert _mine(prefix) == []


def test_a_truncated_dump_fails_its_checksum_and_leaves_no_cluster(populated, backups, prefix):
    path, _, _ = _dump(backups)
    path.write_bytes(path.read_bytes()[:200])
    with pytest.raises(backup.BackupError, match="SHA-256"):
        backup.restore(path, prefix=prefix)
    assert _mine(prefix) == []


def test_a_dump_pg_restore_cannot_read_fails_and_leaves_no_cluster(populated, backups, prefix):
    """Truncated, with its manifest made to agree: `pg_restore` itself
    fails, and the scratch cluster is removed."""
    path, manifest, _ = _dump(backups)
    path.write_bytes(path.read_bytes()[:200])
    Path(f"{path}.json").write_text(json.dumps({**manifest, "dump_sha256": backup.file_sha256(path)}))
    with pytest.raises(backup.BackupError, match="pg_restore failed"):
        backup.restore(path, prefix=prefix)
    assert _mine(prefix) == []


@pytest.mark.parametrize("table", ["events", "documents"])
def test_a_manifest_whose_digest_was_altered_fails_after_restore(populated, backups, prefix, table):
    path, manifest, _ = _dump(backups)
    manifest[table]["sha256"] = "0" * 64
    Path(f"{path}.json").write_text(json.dumps(manifest))
    with pytest.raises(backup.BackupError, match=f"{table} differs"):
        backup.restore(path, prefix=prefix)
    assert _mine(prefix) == []


def test_rows_appended_while_dumping_do_not_break_the_match(populated, backups, prefix, tmp_path):
    """The manifest and the dump read one exported snapshot."""
    pg_bin = _fake_bin(tmp_path, f'sleep 1\nexec "{Path(settings.pg_bin) / "pg_dump"}" "$@"\n')
    out = {}
    dumping = threading.Thread(target=lambda: out.update(result=_dump(backups, pg_bin=pg_bin)))
    dumping.start()

    async def append():
        async with await db.connect(populated) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="during"))
            while dumping.is_alive():
                async with conn.transaction():
                    await ledger.append(conn, task, "turn.started", {"turn_id": ledger.new_id()})
                await asyncio.sleep(0.05)
            return await (await conn.execute("SELECT count(*) FROM events")).fetchone()

    (rows_after,) = asyncio.run(append())
    dumping.join()
    path, manifest, _ = out["result"]
    assert rows_after > manifest["events"]["rows"]
    assert backup.restore(path, prefix=prefix)["match"] is True


# -- what a dump refuses -----------------------------------------------------------


def test_a_missing_backup_directory_fails_naming_it_and_writes_nothing(populated, tmp_path):
    missing = tmp_path / "unmounted" / "valor_temp"
    with pytest.raises(backup.BackupError, match=str(missing)):
        _dump(missing)
    assert not missing.exists() and not missing.parent.exists()


def test_a_backup_directory_on_the_clusters_own_disk_is_refused(populated, tmp_path):
    """`tmp_path` and the machine cluster's data share the boot disk."""
    with pytest.raises(backup.BackupError, match="same disk"):
        _dump(tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == [f".{TEST_DB}.lock"]


@pytest.mark.parametrize("stale", ["missing", "elsewhere"])
def test_a_stale_data_directory_setting_fails_the_dump(populated, backups, tmp_path, monkeypatch, stale):
    """After a Postgres upgrade the setting can name a dead directory, which
    both sandbox profiles would then deny instead of the real one."""
    wrong = tmp_path / "postgresql@17" if stale == "missing" else tmp_path
    monkeypatch.setattr(backup, "settings", dataclasses.replace(settings, pg_data_dir=str(wrong)))
    with pytest.raises(backup.BackupError, match="pg_data_dir setting"):
        _dump(backups)
    assert not list(backups.glob(f"{TEST_DB}-*"))


def test_an_existing_dump_name_is_refused(populated, backups):
    _dump(backups)
    with pytest.raises(backup.BackupError, match="already exists"):
        _dump(backups)


def test_two_dumps_in_the_same_second_never_overwrite_each_other(populated, backups, tmp_path):
    """The second waits on the directory's lock, then finds the name taken."""
    pg_bin = _fake_bin(tmp_path, f'sleep 1\nexec "{Path(settings.pg_bin) / "pg_dump"}" "$@"\n')
    results = []

    def one():
        try:
            results.append(_dump(backups, pg_bin=pg_bin))
        except backup.BackupError as exc:
            results.append(exc)

    threads = [threading.Thread(target=one) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    done = [r for r in results if isinstance(r, tuple)]
    refused = [r for r in results if isinstance(r, backup.BackupError)]
    assert len(done) == 1 and len(refused) == 1 and "already exists" in str(refused[0])
    path, manifest, _ = done[0]
    assert backup.file_sha256(path) == manifest["dump_sha256"]


def test_a_failed_pg_dump_leaves_no_partial_and_prunes_nothing(populated, backups, tmp_path):
    pg_bin = _fake_bin(tmp_path, "echo boom >&2\nexit 1\n")
    old = _pairs(backups, 32, NOW)
    before = sorted(p for p in backups.iterdir() if not p.name.startswith("."))
    with pytest.raises(backup.BackupError, match="pg_dump failed: boom"):
        _dump(backups, pg_bin=pg_bin, keep=30)
    assert sorted(p for p in backups.iterdir() if not p.name.startswith(".")) == before
    assert all(p.exists() for p in old)


# -- pruning -----------------------------------------------------------------------


def test_pruning_keeps_the_newest_pairs_and_nothing_that_is_not_a_complete_pair(populated, backups):
    old = _pairs(backups, 32, NOW)
    others = [
        backups / f"._{old[0].name}",  # exFAT AppleDouble
        backups / f"._{old[0].name}.json",
        backups / f"{TEST_DB}-20200101T000000Z.dump",  # no manifest: not a pair
        backups / f"{TEST_DB}-20200101T000001Z.dump.partial",
        backups / "notes.txt",
    ]
    for f in others:
        f.write_text("keep")
    path, _, pruned = _dump(backups, keep=30)
    kept = sorted(p.name for p in backups.iterdir() if backup.pattern(TEST_DB).match(p.name))
    dropped = old[29:]  # the new dump and the 29 newest old ones stay
    assert sorted(pruned) == sorted([*dropped, *(Path(f"{p}.json") for p in dropped)])
    assert path.exists() and all(f.exists() for f in others)
    assert len([n for n in kept if (backups / f"{n}.json").exists()]) == 30


def test_only_a_dumps_own_name_matches():
    match = backup.pattern("x").match
    assert match("x-20261001T030000Z.dump")
    for other in (
        "._x-20261001T030000Z.dump",
        "x-20261001T030000Z.dump.json",
        "x-20261001T030000Z.dump.partial",
    ):
        assert not match(other)


# -- the command line and launchd --------------------------------------------------


def test_the_cli_turns_a_backup_or_restore_failure_into_a_message_and_an_exit(tmp_path):
    missing = tmp_path / "unmounted"
    out = _cli("backup", env={"VALOR_BACKUP_DIR": str(missing), "VALOR_DB": TEST_DB})
    assert out.returncode == 1 and out.stderr.startswith("backup failed:") and str(missing) in out.stderr
    assert "Traceback" not in out.stderr
    out = _cli("restore", str(tmp_path / "nothing.dump"))
    assert out.returncode == 1 and out.stderr.startswith("restore failed: no manifest")
    assert "Traceback" not in out.stderr


def test_the_plist_runs_a_dump_with_only_the_environment_it_sets(populated, backups, monkeypatch):
    for k in backup.PLIST_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VALOR_DB", TEST_DB)
    monkeypatch.setenv("VALOR_BACKUP_DIR", str(backups))
    job = plistlib.loads(backup.plist())
    assert job["Label"] == backup.LABEL and job["StartCalendarInterval"] == {"Hour": 3, "Minute": 0}
    assert job["ProgramArguments"] == [sys.executable, "-m", "core", "backup"]
    assert Path(job["WorkingDirectory"]) == backup.ROOT
    env = job["EnvironmentVariables"]
    assert env["VALOR_BACKUP_DIR"] == str(backups) and env["VALOR_DB"] == TEST_DB
    ran = subprocess.run(
        job["ProgramArguments"],
        cwd=job["WorkingDirectory"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ran.returncode == 0, ran.stderr
    assert len(list(backups.glob(f"{TEST_DB}-*.dump"))) == 1


def test_the_default_plist_carries_no_backup_path(monkeypatch):
    """With no override set, the job reads the backup disk from settings."""
    for k in backup.PLIST_ENV:
        monkeypatch.delenv(k, raising=False)
    env = plistlib.loads(backup.plist())["EnvironmentVariables"]
    assert set(env) == {"HOME", "PATH"}
