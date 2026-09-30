"""Ensure every Valor machine sources the cross-machine zshenv loader.

The vault at ``~/Desktop/Valor/zshenv.sh`` (iCloud-synced) holds cross-machine
shell config — primarily a ``set -a; source ~/Desktop/Valor/.env; set +a;``
block that exports shared secrets (GITHUB_PAT_*, SENTRY_PERSONAL_TOKEN, etc.)
into every shell on every Valor machine.

``~/.zshenv`` itself does NOT sync (it lives in ``$HOME``, not Desktop), so
each machine needs a one-line bootstrap to source the vault loader. This
module:

1. Seeds ``~/Desktop/Valor/zshenv.sh`` with a minimal default if it's absent
   (only happens on the very first machine — subsequent machines pick it up
   via iCloud).
2. Ensures the local ``~/.zshenv`` contains the source guard line. Idempotent:
   detects the existing line by its file path and skips if already present.

If the vault directory itself is missing (iCloud not signed in / not synced
yet), we still write the source guard into ``~/.zshenv`` — the ``[ -f ]``
test inside the guard means the missing vault is a no-op at shell startup,
and the line activates as soon as iCloud lands the file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

VAULT_ZSHENV_PATH = Path.home() / "Desktop" / "Valor" / "zshenv.sh"
LOCAL_ZSHENV_PATH = Path.home() / ".zshenv"

# Marker the guard line must contain. Used for idempotency check; matching by
# path is more robust than matching the exact line, which lets us evolve the
# guard syntax later without re-adding duplicates.
GUARD_MARKER = "~/Desktop/Valor/zshenv.sh"

GUARD_BLOCK = """
# Cross-machine shell config + secrets, synced via iCloud (~/Desktop/Valor/).
# Managed by scripts/update/zshenv_sync.py — do not edit the loader content here;
# put cross-machine shell config in ~/Desktop/Valor/zshenv.sh instead.
[ -f ~/Desktop/Valor/zshenv.sh ] && source ~/Desktop/Valor/zshenv.sh
"""

DEFAULT_VAULT_CONTENT = """# Cross-machine shell environment for Valor machines.
# Synced via ~/Desktop/Valor/ (iCloud). Bootstrapped per machine by a
# single `source` line in each machine's ~/.zshenv (added by
# `scripts/update/zshenv_sync.py` or the /setup skill).
#
# Keep this file machine-agnostic — anything that varies per host
# (PATH tweaks tied to a specific Homebrew prefix, hostname checks,
# etc.) belongs in the local ~/.zshenv or ~/.zshrc, not here.

# Load cross-machine secrets (GITHUB_PAT_*, SENTRY_PERSONAL_TOKEN, etc.)
[ -f ~/Desktop/Valor/.env ] && { set -a; source ~/Desktop/Valor/.env; set +a; }
"""

logger = logging.getLogger(__name__)


@dataclass
class ZshenvSyncResult:
    """Result of zshenv bootstrap verification."""

    vault_ok: bool = False
    vault_seeded: bool = False
    guard_ok: bool = False
    guard_added: bool = False
    error: str | None = None


def _ensure_vault_zshenv() -> tuple[bool, bool, str | None]:
    """Seed the vault zshenv.sh if the vault dir exists but the file does not.

    Returns (vault_ok, seeded, error). vault_ok is True if the file ends up
    present after this call; seeded is True only when we just wrote it.
    """
    vault_dir = VAULT_ZSHENV_PATH.parent
    if not vault_dir.exists():
        # iCloud not synced / signed in yet. Not an error — the guard line in
        # ~/.zshenv will activate as soon as the vault lands.
        return False, False, None

    if VAULT_ZSHENV_PATH.exists():
        return True, False, None

    try:
        VAULT_ZSHENV_PATH.write_text(DEFAULT_VAULT_CONTENT)
        logger.info("Seeded %s with default vault loader", VAULT_ZSHENV_PATH)
        return True, True, None
    except OSError as exc:
        return False, False, str(exc)


def _ensure_local_guard() -> tuple[bool, bool, str | None]:
    """Append the source guard to ~/.zshenv if missing.

    Returns (guard_ok, added, error).
    """
    existing = ""
    if LOCAL_ZSHENV_PATH.exists():
        try:
            existing = LOCAL_ZSHENV_PATH.read_text()
        except OSError as exc:
            return False, False, f"could not read {LOCAL_ZSHENV_PATH}: {exc}"

    if GUARD_MARKER in existing:
        return True, False, None

    # Preserve any trailing-newline contract — append a single leading newline
    # if the file doesn't already end with one.
    sep = "" if existing.endswith("\n") or not existing else "\n"
    try:
        with LOCAL_ZSHENV_PATH.open("a") as fh:
            fh.write(sep + GUARD_BLOCK)
        logger.info("Appended Valor zshenv guard to %s", LOCAL_ZSHENV_PATH)
        return True, True, None
    except OSError as exc:
        return False, False, f"could not write {LOCAL_ZSHENV_PATH}: {exc}"


def sync_zshenv() -> ZshenvSyncResult:
    """Bootstrap cross-machine shell env loading on this machine.

    - Seeds ``~/Desktop/Valor/zshenv.sh`` with a default loader if the vault
      dir exists but the file does not.
    - Adds a source guard to ``~/.zshenv`` if missing.

    Both steps are independent and idempotent. The function returns a single
    aggregated result; failures in one step do not prevent the other from
    running.
    """
    result = ZshenvSyncResult()

    vault_ok, vault_seeded, vault_err = _ensure_vault_zshenv()
    result.vault_ok = vault_ok
    result.vault_seeded = vault_seeded

    guard_ok, guard_added, guard_err = _ensure_local_guard()
    result.guard_ok = guard_ok
    result.guard_added = guard_added

    errors = [e for e in (vault_err, guard_err) if e]
    if errors:
        result.error = "; ".join(errors)

    return result
