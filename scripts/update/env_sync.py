"""Verify that the project .env and config/projects.json are symlinks pointing to the Valor vault.

The vault at ~/Desktop/Valor/ (iCloud-synced) is the single source of truth for
secrets and project configuration. Both files must be symlinks — never regular files.

On a fresh machine or after accidental deletion, this module creates the symlinks
automatically so the update process is self-healing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

VAULT_ENV_PATH = Path.home() / "Desktop" / "Valor" / ".env"
VAULT_PROJECTS_PATH = Path.home() / "Desktop" / "Valor" / "projects.json"

logger = logging.getLogger(__name__)


@dataclass
class EnvSyncResult:
    """Result of .env symlink verification."""

    symlink_ok: bool = False
    created: bool = False
    error: str | None = None


def sync_env_from_vault(project_dir: Path) -> EnvSyncResult:
    """Verify project .env is a symlink to the vault. Create it if missing.

    Returns EnvSyncResult with:
      - symlink_ok=True  if the symlink exists and points to the vault
      - created=True     if the symlink was just created (was missing)
      - error            set if the vault is absent or symlink could not be created
    """
    result = EnvSyncResult()
    project_env = project_dir / ".env"

    if not VAULT_ENV_PATH.exists():
        result.error = (
            f"Vault .env not found at {VAULT_ENV_PATH} — "
            "iCloud may not have synced yet. Secrets unavailable until sync completes."
        )
        logger.warning(result.error)
        return result

    # Already a correct symlink — nothing to do.
    if project_env.is_symlink() and project_env.resolve() == VAULT_ENV_PATH.resolve():
        result.symlink_ok = True
        return result

    # Regular file (old behaviour) or broken/wrong symlink — replace with symlink.
    try:
        if project_env.exists() or project_env.is_symlink():
            project_env.unlink()
        project_env.symlink_to(VAULT_ENV_PATH)
        result.symlink_ok = True
        result.created = True
        logger.info("Created .env symlink → %s", VAULT_ENV_PATH)
    except OSError as exc:
        result.error = str(exc)
        logger.warning("Failed to create .env symlink: %s", exc)

    return result


@dataclass
class ProjectsSyncResult:
    """Result of config/projects.json symlink verification."""

    symlink_ok: bool = False
    created: bool = False
    error: str | None = None


def sync_projects_json(project_dir: Path) -> ProjectsSyncResult:
    """Verify config/projects.json is a symlink to the vault. Create it if missing.

    Returns ProjectsSyncResult with:
      - symlink_ok=True  if the symlink exists and points to the vault
      - created=True     if the symlink was just created (was missing)
      - error            set if the vault is absent or symlink could not be created
    """
    result = ProjectsSyncResult()
    config_projects = project_dir / "config" / "projects.json"

    if not VAULT_PROJECTS_PATH.exists():
        result.error = (
            f"Vault projects.json not found at {VAULT_PROJECTS_PATH} — "
            "iCloud may not have synced yet. Worker will not start until sync completes."
        )
        logger.warning(result.error)
        return result

    # Already a correct symlink — nothing to do.
    if config_projects.is_symlink() and config_projects.resolve() == VAULT_PROJECTS_PATH.resolve():
        result.symlink_ok = True
        return result

    # Regular file or broken/wrong symlink — replace with symlink.
    try:
        if config_projects.exists() or config_projects.is_symlink():
            config_projects.unlink()
        config_projects.symlink_to(VAULT_PROJECTS_PATH)
        result.symlink_ok = True
        result.created = True
        logger.info("Created config/projects.json symlink → %s", VAULT_PROJECTS_PATH)
    except OSError as exc:
        result.error = str(exc)
        logger.warning("Failed to create config/projects.json symlink: %s", exc)

    return result
