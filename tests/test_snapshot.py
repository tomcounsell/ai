"""The Valor folder's nightly copy into the work vault (core/snapshot.py):
what is left out, what each commit says, the vault sync's lock and states,
and the update routine running it on the Mac that sets it."""

import asyncio
import dataclasses
import subprocess
import threading
import time
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from core import db, snapshot
from core.settings import settings

pytestmark = pytest.mark.spend(usd=0)

DAY = date(2026, 11, 16)
EXCLUDES = """.env
*.env
.env.*
*token*
*key*
*.session
dumps/
*.mp3
"""


def git(cwd: Path, *argv: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *argv], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def folders(tmp_path):
    source = tmp_path / "Valor"
    (source / "brew").mkdir(parents=True)
    (source / "projects" / "dumps").mkdir(parents=True)
    (source / "snapshot-exclude.txt").write_text(EXCLUDES)
    (source / "brew" / "brew-nightly-update.sh").write_text("#!/bin/bash\necho one\n")
    (source / "projects" / "valor.toml").write_text('name = "valor"\n')
    (source / ".env").write_text("SECRET=1\n")
    (source / "google_token.json").write_text("{}\n")
    (source / "projects" / "client" / "gemini-key-undo.env").parent.mkdir()
    (source / "projects" / "client" / "gemini-key-undo.env").write_text("K=1\n")
    (source / "projects" / "dumps" / "memory.dump").write_text("rows\n")
    (source / "episode.mp3").write_text("audio\n")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    vault = tmp_path / "work-vault"
    subprocess.run(["git", "clone", "-q", str(origin), str(vault)], check=True, capture_output=True)
    for k, v in (("user.name", "t"), ("user.email", "t@example.com")):
        git(vault, "config", k, v)
    git(vault, "checkout", "-q", "-b", "main")
    (vault / "notes.md").write_text("vault\n")
    git(vault, "add", "notes.md")
    git(vault, "commit", "-qm", "start")
    git(vault, "push", "-q", "origin", "main")
    return source, vault / "_valor-desktop_", vault, origin


def kept(dest: Path) -> list[str]:
    return sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())


def test_the_folder_is_copied_without_secrets_committed_alone_and_pushed(folders):
    source, dest, vault, origin = folders
    (vault / "draft.md").write_text("Tom's unsaved note\n")  # the vault sync's to commit, not ours
    assert snapshot.run(source, dest, DAY) == "committed 3 files and pushed"
    assert kept(dest) == ["brew/brew-nightly-update.sh", "projects/valor.toml", "snapshot-exclude.txt"]
    tree = git(origin, "ls-tree", "-r", "--name-only", "main").split()
    assert sorted(tree) == ["_valor-desktop_/brew/brew-nightly-update.sh", "_valor-desktop_/projects/valor.toml",
                            "_valor-desktop_/snapshot-exclude.txt", "notes.md"]  # fmt: skip
    assert git(origin, "log", "-1", "--format=%B", "main") == (
        "Valor folder snapshot 2026-11-16: 3 files (3 added)\n\n"
        "added: brew/brew-nightly-update.sh\nadded: projects/valor.toml\nadded: snapshot-exclude.txt\n\n"
    )
    assert "?? draft.md" in git(vault, "status", "--short")


def test_each_change_is_listed_under_its_verb_and_no_change_commits_nothing(folders):
    source, dest, _vault, origin = folders
    snapshot.run(source, dest, DAY)
    assert snapshot.run(source, dest, DAY) == "no change"
    (source / "brew" / "brew-nightly-update.sh").write_text(
        "#!/bin/bash\necho two, longer\n"
    )  # rsync compares size and time
    (source / "projects" / "valor.toml").unlink()
    (source / "rooms.md").write_text("rooms\n")
    (source / "old_keys.txt").write_text("sk-...\n")  # matched by *key*: never copied
    assert snapshot.run(source, dest, DAY) == "committed 3 files and pushed"
    assert git(origin, "log", "-1", "--format=%B", "main") == (
        "Valor folder snapshot 2026-11-16: 3 files (1 added, 1 modified, 1 deleted)\n\n"
        "modified: brew/brew-nightly-update.sh\ndeleted: projects/valor.toml\nadded: rooms.md\n\n"
    )
    assert len(git(origin, "log", "--format=%h", "main").split()) == 3


def test_a_newly_excluded_file_leaves_the_vault_copy_and_a_large_file_is_never_copied(folders, monkeypatch):
    source, dest, _vault, _ = folders
    snapshot.run(source, dest, DAY)
    (source / "snapshot-exclude.txt").write_text(EXCLUDES + "brew/\n")
    monkeypatch.setattr(snapshot, "MAX_MB", 1)
    (source / "big.pdf").write_bytes(b"x" * (2 * 1024 * 1024))
    snapshot.run(source, dest, DAY)
    assert kept(dest) == ["projects/valor.toml", "snapshot-exclude.txt"]


def test_with_no_exclude_list_nothing_is_copied(folders):
    source, dest, _vault, _ = folders
    (source / "snapshot-exclude.txt").unlink()
    with pytest.raises(snapshot.Failed, match="no exclude list"):
        snapshot.run(source, dest, DAY)
    assert not dest.exists() or kept(dest) == []


def test_a_vault_off_main_or_mid_rebase_is_skipped_as_the_vault_sync_skips_it(folders):
    source, dest, vault, _ = folders
    git(vault, "checkout", "-q", "-b", "elsewhere")
    with pytest.raises(snapshot.Failed, match="on elsewhere, not main"):
        snapshot.run(source, dest, DAY)
    git(vault, "checkout", "-q", "main")
    (vault / ".git" / "rebase-merge").mkdir()
    with pytest.raises(snapshot.Failed, match="rebase or merge"):
        snapshot.run(source, dest, DAY)
    assert kept(dest) == []


def test_a_held_sync_lock_is_waited_for_and_the_snapshot_holds_it_as_the_sync_reads_it(folders, monkeypatch):
    source, dest, vault, _ = folders
    lock = vault / ".git" / "vault-sync" / "lock"
    lock.mkdir(parents=True)
    monkeypatch.setattr(snapshot, "LOCK_WAIT_S", 0.5)
    with pytest.raises(snapshot.Failed, match="vault busy"):
        snapshot.run(source, dest, DAY)
    assert lock.is_dir() and kept(dest) == []  # the sync's lock is left to the sync

    # Released while waiting: the snapshot takes it, with a fresh `started`.
    monkeypatch.setattr(snapshot, "LOCK_WAIT_S", 10)
    seen = []
    copy = snapshot.subprocess.run

    def rsync(argv, **kw):
        if argv[0] == snapshot.RSYNC:
            seen.append(int((lock / "started").read_text()))
        return copy(argv, **kw)

    monkeypatch.setattr(snapshot.subprocess, "run", rsync)
    threading.Timer(1.2, lambda: lock.rmdir()).start()
    assert snapshot.run(source, dest, DAY).startswith("committed")
    assert abs(seen[0] - time.time()) < 30 and not lock.exists()


def test_the_vault_sync_waits_on_the_snapshots_lock(folders, monkeypatch):
    """The vault's own sync script, run while the snapshot copies, skips
    the run rather than committing half a copy (.sync/vault-sync.sh)."""
    source, dest, vault, _ = folders
    sync = Path.home() / "work-vault" / ".sync" / "vault-sync.sh"
    if not sync.is_file():
        pytest.skip("the work vault's sync script is not on this Mac")
    (vault / ".sync").mkdir()
    (vault / ".sync" / "vault-sync.sh").write_text(sync.read_text())
    out = []
    copy = snapshot.subprocess.run

    def rsync(argv, **kw):
        if argv[0] == snapshot.RSYNC:
            out.append(subprocess.run(["bash", str(vault / ".sync" / "vault-sync.sh")], capture_output=True,
                                      text=True, env={"PATH": "/usr/bin:/bin", "VAULT_HOST": "t"}, check=False).stdout)  # fmt: skip
        return copy(argv, **kw)

    monkeypatch.setattr(snapshot.subprocess, "run", rsync)
    snapshot.run(source, dest, DAY)
    assert "another sync is running; skipping" in out[0]


def test_a_refused_push_is_left_to_the_vault_sync(folders):
    source, dest, vault, origin = folders
    git(vault, "remote", "set-url", "origin", str(origin.parent / "missing.git"))
    done = snapshot.run(source, dest, DAY)
    assert done.startswith("committed 3 files; push left to the vault sync")
    assert "Valor folder snapshot" in git(vault, "log", "-1", "--format=%s")


def test_the_update_routine_snapshots_only_where_set_and_reports_a_failure_once_a_day(
    fresh, folders, monkeypatch
):
    import core.__main__ as main
    from core import routines
    from tests.test_rollout import reports

    source, dest, _vault, _origin = folders
    uv = source.parent / "uv"
    uv.write_text("#!/bin/sh\n")
    uv.chmod(0o755)
    monkeypatch.setattr(main, "VALOR_FOLDER", source)

    async def update(snapshot_dir, day):
        monkeypatch.setattr(
            main, "settings", dataclasses.replace(settings, uv=str(uv), snapshot_dir=snapshot_dir)
        )
        async with await db.connect(fresh) as conn:
            ctx = routines.Context(conn, None, "update", datetime(2026, 11, day, 20, tzinfo=UTC), None)
            return await main.update_runner(ctx)

    ran = asyncio.run(update(None, 3))
    assert "snapshot" not in ran.summary and not dest.exists()
    ran = asyncio.run(update(str(dest), 3))
    assert ran.summary.endswith("; snapshot committed 3 files and pushed")
    (source / "snapshot-exclude.txt").unlink()
    for day in (4, 4, 5):
        ran = asyncio.run(update(str(dest), day))
    assert "snapshot failed: no exclude list" in ran.summary
    got = [n for _, n in reports(fresh) if n["about_key"].startswith("snapshot:")]
    assert [n["about_key"] for n in got] == ["snapshot:2026-11-04", "snapshot:2026-11-05"]
    assert got[0]["text"].startswith(
        "Tonight's copy of the Valor folder into the work vault committed nothing"
    )
