"""
Path constants derived from project root.

All paths are derived from Path(__file__) -- no hardcoded usernames.
Import these instead of constructing paths manually.

Usage:
    from config.paths import PROJECT_ROOT, DATA_DIR, CONFIG_DIR, VALOR_DIR
"""

from pathlib import Path

# Project root: parent of config/ directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Standard directories
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
CONFIG_DIR = PROJECT_ROOT / "config"

# Home-relative paths (for user-specific locations)
HOME_DIR = Path.home()
SRC_DIR = HOME_DIR / "src"


def _resolve_valor_dir() -> Path:
    """Resolve the Valor vault directory at import time.

    Defers to ``config.settings.VaultSettings`` cascade. Falls back to the
    established default ``~/Desktop/Valor`` when the vault is unresolved
    (fresh checkout before /setup, broken symlink, etc.) so existing call
    sites that read the constant don't crash on a not-yet-configured machine.
    """
    try:
        from config.settings import vault

        return vault.dir
    except Exception:
        return HOME_DIR / "Desktop" / "Valor"


# Canonical credentials directory (Google OAuth, DM whitelist, calendar config).
# Resolves via the vault cascade — see `_resolve_valor_dir` above. Configurable
# per machine via `VALOR_VAULT_DIR`; default `~/Desktop/Valor`.
VALOR_DIR = _resolve_valor_dir()
