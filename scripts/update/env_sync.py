"""Sync specific env vars from the Valor vault to the project .env.

The vault at ~/Desktop/Valor/.env (iCloud-synced) is the source of truth
for API keys shared across machines. This module copies specific keys
into the project's .env if they are missing or outdated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Keys to sync from vault to project .env.
# Add new keys here as services are added.
SYNC_KEYS: list[str] = [
    "VOYAGE_API_KEY",
]

VAULT_ENV_PATH = Path.home() / "Desktop" / "Valor" / ".env"


@dataclass
class EnvSyncResult:
    """Result of env sync operation."""

    success: bool = True
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    error: str | None = None


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file into key-value pairs. Ignores comments and blank lines."""
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def sync_env_from_vault(project_dir: Path) -> EnvSyncResult:
    """Copy SYNC_KEYS from vault .env to project .env if missing or changed."""
    result = EnvSyncResult()
    project_env = project_dir / ".env"

    if not VAULT_ENV_PATH.is_file():
        result.error = f"Vault .env not found at {VAULT_ENV_PATH}"
        return result

    vault_vars = _parse_env_file(VAULT_ENV_PATH)
    project_vars = _parse_env_file(project_env)

    changes: list[str] = []
    for key in SYNC_KEYS:
        vault_value = vault_vars.get(key)
        if vault_value is None:
            result.skipped.append(key)
            continue

        project_value = project_vars.get(key)
        if project_value == vault_value:
            result.skipped.append(key)
            continue

        if project_value is None:
            result.added.append(key)
        else:
            result.updated.append(key)

        project_vars[key] = vault_value
        changes.append(key)

    if not changes:
        return result

    # Rewrite project .env preserving existing content, updating changed keys
    try:
        lines: list[str] = []
        written_keys: set[str] = set()

        if project_env.is_file():
            for line in project_env.read_text().splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key = stripped.partition("=")[0].strip()
                    if key in changes:
                        lines.append(f"{key}={project_vars[key]}")
                        written_keys.add(key)
                        continue
                lines.append(line)

        # Append any new keys not already in the file
        for key in changes:
            if key not in written_keys:
                lines.append(f"{key}={project_vars[key]}")

        project_env.write_text("\n".join(lines) + "\n")
    except OSError as e:
        result.success = False
        result.error = str(e)

    return result
