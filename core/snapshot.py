"""A nightly copy of the iCloud Valor folder into a subfolder of a git
vault, committed and pushed, so the folder has a history while iCloud
stays the live copy and nothing reads the vault's (docs/plans/d998b118-
valor-folder-history-and-tailscale.md). The `update` routine runs it on the
Mac whose job sets `VALOR_SNAPSHOT_DIR`.

`run` copies with rsync, leaving out what `snapshot-exclude.txt` in the
folder names (no list, no copy: the list is private, beside what it
excludes) and any file of `MAX_MB` or more, the vault sync's own cap.
rsync deletes nothing after an I/O error, so a file iCloud could not
fetch keeps its last copy. The vault sync job (`.sync/vault-sync.sh` in the
vault, every 5 minutes) is paused by its own lock while the copy runs, so
it never commits half a copy; the snapshot waits up to `LOCK_WAIT_S` for
it, keeps the lock's `started` fresh (the sync clears a lock older than 15
minutes), and skips a vault that is not on `main` or is mid-rebase or
merge, as the sync does. It commits only its own subfolder, with each
path that changed, and pushes; a push refused is left to the vault sync,
which rebases and pushes.
"""

import shutil
import subprocess
import time
from collections import Counter
from datetime import date
from pathlib import Path

from core.settings import settings

EXCLUDES = "snapshot-exclude.txt"
MAX_MB = 50
LOCK_WAIT_S = 60.0
LISTED = 200
RSYNC = "/usr/bin/rsync"
VERBS = {"A": "added", "M": "modified", "D": "deleted"}


class Failed(RuntimeError):
    """A snapshot that committed nothing, and why."""


def _git(vault: Path, *argv: str, check: bool = True) -> subprocess.CompletedProcess:
    done = subprocess.run(
        [settings.git_bin, "-C", str(vault), *argv], capture_output=True, text=True, check=False
    )
    if check and done.returncode != 0:
        raise Failed(f"git {argv[0]} exited {done.returncode}: {done.stderr.strip()[-200:]}")
    return done


def _lock(git_dir: Path) -> Path:
    """The vault sync's lock, taken as it takes it (`mkdir`)."""
    lock = git_dir / "vault-sync" / "lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            lock.mkdir()
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise Failed("vault busy: its sync held the lock") from None
            time.sleep(1)
            continue
        (lock / "started").write_text(f"{time.time():.0f}\n")
        return lock


def message(changes: list[tuple[str, str]], day: date) -> str:
    """The commit message: the counts, then each path under its verb."""
    counts = Counter(VERBS.get(s[0], "modified") for s, _ in changes)
    head = ", ".join(f"{counts[v]} {v}" for v in VERBS.values() if counts[v])
    lines = [f"{VERBS.get(s[0], 'modified')}: {p}" for s, p in changes[:LISTED]]
    if len(changes) > LISTED:
        lines.append(f"and {len(changes) - LISTED} more")
    return (
        f"Valor folder snapshot {day.isoformat()}: {len(changes)} files ({head})\n\n"
        + "\n".join(lines)
        + "\n"
    )


def run(source: Path, dest: Path, day: date) -> str:
    """Copy, commit and push (module docstring). Returns what was done;
    raises `Failed` when nothing was committed for a reason to report."""
    excludes = source / EXCLUDES
    if not excludes.is_file():
        raise Failed(f"no exclude list at {excludes}")
    dest.mkdir(parents=True, exist_ok=True)
    vault = Path(_git(dest, "rev-parse", "--show-toplevel").stdout.strip())
    git_dir = Path(_git(vault, "rev-parse", "--absolute-git-dir").stdout.strip())
    branch = _git(vault, "symbolic-ref", "--short", "-q", "HEAD", check=False).stdout.strip()
    if branch != "main":
        raise Failed(f"vault is on {branch or 'a detached head'}, not main")
    if any((git_dir / n).exists() for n in ("rebase-merge", "rebase-apply", "MERGE_HEAD")):
        raise Failed("vault has a rebase or merge in progress")
    lock = _lock(git_dir)
    try:
        copied = subprocess.run(
            [RSYNC, "-a", "--delete", "--delete-excluded", f"--max-size={MAX_MB}m",
             f"--exclude-from={excludes}", f"{source}/", f"{dest}/"],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        (lock / "started").write_text(f"{time.time():.0f}\n")
        if copied.returncode not in (0, 24):  # 24: a file vanished while copying
            raise Failed(f"rsync exited {copied.returncode}: {copied.stderr.strip()[-300:]}")
        sub = str(dest.relative_to(vault))
        _git(vault, "add", "-A", "--", sub)
        listed = _git(vault, "diff", "--cached", "--name-status", "--no-renames", "-z", "--", sub).stdout
        fields = listed.split("\0")[:-1]
        changes = [(fields[i], str(Path(fields[i + 1]).relative_to(sub))) for i in range(0, len(fields), 2)]
        if not changes:
            return "no change"
        _git(vault, "commit", "-q", "-m", message(changes, day), "--", sub)
    finally:
        shutil.rmtree(lock, ignore_errors=True)
    pushed = _git(vault, "push", "-q", "origin", "HEAD:main", check=False)
    done = f"committed {len(changes)} files"
    if pushed.returncode != 0:
        return f"{done}; push left to the vault sync: {pushed.stderr.strip()[-200:]}"
    return f"{done} and pushed"
