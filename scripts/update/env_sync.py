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


VAULT_REFLECTIONS_PATH = Path.home() / "Desktop" / "Valor" / "reflections.yaml"


@dataclass
class ReflectionsSyncResult:
    """Result of config/reflections.yaml symlink verification."""

    symlink_ok: bool = False
    created: bool = False
    skipped: bool = False
    error: str | None = None


def sync_reflections_yaml(project_dir: Path) -> ReflectionsSyncResult:
    """Verify config/reflections.yaml is a symlink to the vault. Create it if missing.

    If the vault file doesn't exist, the in-repo fallback is left intact and
    the result is marked skipped (not an error). This allows fresh machines
    that haven't synced the vault to continue using the in-repo config.

    Returns ReflectionsSyncResult with:
      - symlink_ok=True  if the symlink exists and points to the vault
      - created=True     if the symlink was just created (was missing)
      - skipped=True     if vault file doesn't exist (in-repo fallback active)
      - error            set if symlink could not be created
    """
    result = ReflectionsSyncResult()
    config_reflections = project_dir / "config" / "reflections.yaml"

    if not VAULT_REFLECTIONS_PATH.exists():
        # Vault file not present — use in-repo fallback. Not an error.
        result.skipped = True
        logger.info(
            "Vault reflections.yaml not found at %s — using in-repo fallback",
            VAULT_REFLECTIONS_PATH,
        )
        return result

    # Already a correct symlink — nothing to do.
    if (
        config_reflections.is_symlink()
        and config_reflections.resolve() == VAULT_REFLECTIONS_PATH.resolve()
    ):
        result.symlink_ok = True
        return result

    # Replace with symlink
    try:
        if config_reflections.exists() or config_reflections.is_symlink():
            config_reflections.unlink()
        config_reflections.symlink_to(VAULT_REFLECTIONS_PATH)
        result.symlink_ok = True
        result.created = True
        logger.info("Created config/reflections.yaml symlink → %s", VAULT_REFLECTIONS_PATH)
    except OSError as exc:
        result.error = str(exc)
        logger.warning("Failed to create config/reflections.yaml symlink: %s", exc)

    return result
