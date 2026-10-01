"""Backups on real Postgres: dumps of the test database into a temp
directory, restores into scratch clusters.

A dump refuses a directory on the cluster's own disk, so these tests name a
data directory on another device (`/dev`) where the real one would be.

Live spend: none.
"""

import asyncio
import json
import plistlib
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core import backup, db, ledger, tasks
from core.settings import settings
from tests.conftest import TEST_DB

pytestmark = pytest.mark.spend(usd=0)

OTHER_DEVICE = "/dev"
NOW = datetime(2026, 10, 2, 3, 0, 0, tzinfo=UTC)


def _dump(backup_dir, **kw):
    args = {
        "backup_dir": backup_dir,
        "database": TEST_DB,
        "dsn": settings.dsn(owner=True, database=TEST_DB),
        "data_dir": OTHER_DEVICE,
        "now": NOW,
        **kw,
    }
    return backup.dump(**args)


def _clusters() -> set[str]:
    return {p.name for p in Path("/tmp").glob("vk-*")}


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


@pytest.fixture
def populated(dsn):
    async def go():
        async with await db.connect(dsn) as conn:
            await tasks.start(conn, tasks.Brief(instruction="backed up", budget_usd_micros=5))

    asyncio.run(go())
    return dsn


def test_a_dump_restores_into_a_scratch_cluster_and_matches_its_manifest(populated, tmp_path):
    before = _clusters()
    path, manifest, pruned = _dump(tmp_path)
    assert path.name == f"{TEST_DB}-20261002T030000Z.dump" and pruned == []
    assert json.loads(Path(f"{path}.json").read_text()) == manifest
    assert manifest["dump_sha256"] == backup.file_sha256(path) and manifest["events"]["rows"] > 0
    assert not list(tmp_path.glob("*.partial"))
    summary = backup.restore(path)
    assert summary["match"] is True and summary["events"] == manifest["events"]["rows"]
    assert _clusters() == before


def test_a_truncated_dump_fails_its_checksum_and_leaves_no_cluster(populated, tmp_path):
    path, _, _ = _dump(tmp_path)
    path.write_bytes(path.read_bytes()[:200])
    before = _clusters()
    with pytest.raises(backup.BackupError, match="SHA-256"):
        backup.restore(path)
    assert _clusters() == before


def test_a_dump_pg_restore_cannot_read_fails_and_leaves_no_cluster(populated, tmp_path):
    """Truncated, with its manifest made to agree: `pg_restore` itself
    fails, and the scratch cluster is removed."""
    path, manifest, _ = _dump(tmp_path)
    path.write_bytes(path.read_bytes()[:200])
    Path(f"{path}.json").write_text(json.dumps({**manifest, "dump_sha256": backup.file_sha256(path)}))
    before = _clusters()
    with pytest.raises(backup.BackupError, match="pg_restore failed"):
        backup.restore(path)
    assert _clusters() == before


def test_a_manifest_whose_ledger_digest_was_altered_fails_after_restore(populated, tmp_path):
    path, manifest, _ = _dump(tmp_path)
    manifest["events"]["sha256"] = "0" * 64
    Path(f"{path}.json").write_text(json.dumps(manifest))
    before = _clusters()
    with pytest.raises(backup.BackupError, match="events differs"):
        backup.restore(path)
    assert _clusters() == before


def test_a_missing_backup_directory_fails_naming_it_and_writes_nothing(populated, tmp_path):
    missing = tmp_path / "unmounted" / "valor_temp"
    with pytest.raises(backup.BackupError, match=str(missing)):
        _dump(missing)
    assert not missing.exists() and not missing.parent.exists()


def test_a_backup_directory_on_the_clusters_own_disk_is_refused(populated, tmp_path):
    with pytest.raises(backup.BackupError, match="same disk"):
        _dump(tmp_path, data_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_pruning_keeps_the_newest_pairs_and_nothing_that_is_not_a_complete_pair(populated, tmp_path):
    old = _pairs(tmp_path, 32, NOW)
    others = [
        tmp_path / f"._{old[0].name}",  # exFAT AppleDouble
        tmp_path / f"._{old[0].name}.json",
        tmp_path / f"{TEST_DB}-20200101T000000Z.dump",  # no manifest: not a pair
        tmp_path / f"{TEST_DB}-20200101T000001Z.dump.partial",
        tmp_path / "notes.txt",
    ]
    for f in others:
        f.write_text("keep")
    path, _, pruned = _dump(tmp_path, keep=30)
    kept = sorted(p.name for p in tmp_path.iterdir() if backup.pattern(TEST_DB).match(p.name))
    dropped = old[29:]  # the new dump and the 29 newest old ones stay
    assert sorted(pruned) == sorted([*dropped, *(Path(f"{p}.json") for p in dropped)])
    assert path.exists() and all(f.exists() for f in others)
    assert len([n for n in kept if (tmp_path / f"{n}.json").exists()]) == 30


def test_a_failed_pg_dump_leaves_no_partial_and_prunes_nothing(populated, tmp_path):
    pg_bin = _fake_bin(tmp_path / "x", "echo boom >&2\nexit 1\n")
    backups = tmp_path / "backups"
    backups.mkdir()
    old = _pairs(backups, 32, NOW)
    before = sorted(backups.iterdir())
    with pytest.raises(backup.BackupError, match="pg_dump failed: boom"):
        _dump(backups, pg_bin=pg_bin, keep=30)
    assert sorted(backups.iterdir()) == before and all(p.exists() for p in old)


def test_an_existing_dump_name_is_refused(populated, tmp_path):
    _dump(tmp_path)
    with pytest.raises(backup.BackupError, match="already exists"):
        _dump(tmp_path)


def test_rows_appended_while_dumping_do_not_break_the_match(populated, tmp_path):
    """The manifest and the dump read one exported snapshot."""
    pg_bin = _fake_bin(tmp_path / "x", f'sleep 1\nexec "{Path(settings.pg_bin) / "pg_dump"}" "$@"\n')
    out = {}
    dumping = threading.Thread(target=lambda: out.update(result=_dump(tmp_path, pg_bin=pg_bin)))
    dumping.start()

    async def append():
        async with await db.connect(populated) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="during", budget_usd_micros=1))
            while dumping.is_alive():
                async with conn.transaction():
                    await ledger.append(conn, task, "turn.started", {"turn_id": ledger.new_id()})
                await asyncio.sleep(0.05)
            return await (await conn.execute("SELECT count(*) FROM events")).fetchone()

    (rows_after,) = asyncio.run(append())
    dumping.join()
    path, manifest, _ = out["result"]
    assert rows_after > manifest["events"]["rows"]
    assert backup.restore(path)["match"] is True


def test_the_plist_runs_a_dump_with_only_the_environment_it_sets(populated, tmp_path, monkeypatch):
    for k in backup.PLIST_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VALOR_DB", TEST_DB)
    monkeypatch.setenv("VALOR_BACKUP_DIR", str(tmp_path))
    monkeypatch.setenv("VALOR_PG_DATA", OTHER_DEVICE)
    job = plistlib.loads(backup.plist())
    assert job["Label"] == backup.LABEL and job["StartCalendarInterval"] == {"Hour": 3, "Minute": 0}
    assert job["ProgramArguments"][0] == sys.executable and job["ProgramArguments"][1:] == [
        "-m",
        "core",
        "backup",
    ]
    assert Path(job["WorkingDirectory"]) == backup.ROOT
    env = job["EnvironmentVariables"]
    assert env["VALOR_BACKUP_DIR"] == str(tmp_path) and env["VALOR_DB"] == TEST_DB
    ran = subprocess.run(
        job["ProgramArguments"],
        cwd=job["WorkingDirectory"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ran.returncode == 0, ran.stderr
    assert len(list(tmp_path.glob(f"{TEST_DB}-*.dump"))) == 1


def test_the_default_plist_carries_no_backup_path(monkeypatch):
    """With no override set, the job reads the backup disk from settings."""
    for k in backup.PLIST_ENV:
        monkeypatch.delenv(k, raising=False)
    env = plistlib.loads(backup.plist())["EnvironmentVariables"]
    assert set(env) == {"HOME", "PATH"}


def test_only_a_dumps_own_name_matches():
    match = backup.pattern("x").match
    assert match("x-20261001T030000Z.dump")
    for other in (
        "._x-20261001T030000Z.dump",
        "x-20261001T030000Z.dump.json",
        "x-20261001T030000Z.dump.partial",
    ):
        assert not match(other)
