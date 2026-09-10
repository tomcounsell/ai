"""Opt-in Codex CLI provisioning (plan #2001 Task 4b).

Installs/upgrades the ``@openai/codex`` npm package ONLY on machines that
opt in via ``CODEX__INSTALL_ENABLED=1`` (default OFF — the lane's
preflight fails closed everywhere else, and unprovisioned machines keep
their existing behavior byte-for-byte). When enabled, the installed
version must satisfy the ``CODEX__MIN_VERSION`` floor; anything older is
upgraded, anything at/above is skipped.

All failures are non-fatal: ``install_or_update`` never raises, it
returns a ``CodexCliResult`` the update runner logs and downgrades to a
warning.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

DEFAULT_NPM_PACKAGE = "@openai/codex"
DEFAULT_MIN_VERSION = "0.144.3"
_NPM_INSTALL_TIMEOUT_S = 180


@dataclass
class CodexCliResult:
    """Outcome of one Codex CLI provisioning check."""

    success: bool
    action: str  # "disabled" | "skipped" | "installed" | "updated" | "failed"
    version: str | None = None
    error: str | None = None


def _read_config() -> tuple[bool, str, str]:
    """Return (install_enabled, npm_package, min_version) from typed settings.

    Falls back to the safe defaults (disabled) when settings are
    unavailable — provisioning must never crash the update run.
    """
    try:
        from config.settings import settings as _settings

        codex_cfg = getattr(_settings, "codex", None)
        return (
            bool(getattr(codex_cfg, "install_enabled", False)),
            str(getattr(codex_cfg, "npm_package", None) or DEFAULT_NPM_PACKAGE),
            str(getattr(codex_cfg, "min_version", None) or DEFAULT_MIN_VERSION),
        )
    except Exception:  # noqa: BLE001 -- safe defaults, never crash update
        return False, DEFAULT_NPM_PACKAGE, DEFAULT_MIN_VERSION


def _parse_version(text: str) -> str | None:
    """Extract the first dotted version from ``codex --version`` output."""
    match = re.search(r"(\d+(?:\.\d+)+)", text or "")
    return match.group(1) if match else None


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split(".") if part.isdigit())


def _installed_version() -> str | None:
    """Return the installed Codex CLI version, or None when absent/unparseable."""
    codex = shutil.which("codex")
    if not codex:
        return None
    try:
        result = subprocess.run(
            [codex, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:  # noqa: BLE001 -- timeout/OSError: treat as absent
        return None
    if result.returncode != 0:
        return None
    return _parse_version(result.stdout or "")


def _npm_install(package: str) -> tuple[bool, str | None, str | None]:
    """Install/upgrade ``package`` globally via npm (latest).

    Returns (success, installed_version, error_message).
    """
    npm = shutil.which("npm")
    if not npm:
        return False, None, "npm not found — install Node.js first"
    try:
        result = subprocess.run(
            [npm, "install", "-g", package],
            capture_output=True,
            text=True,
            timeout=_NPM_INSTALL_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, None, f"npm install timed out after {_NPM_INSTALL_TIMEOUT_S}s"
    except Exception as exc:  # noqa: BLE001
        return False, None, str(exc)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "unknown error").strip()
        return False, None, err[:200]
    return True, _installed_version(), None


def install_or_update() -> CodexCliResult:
    """Install or upgrade the Codex CLI when opted in; never raises."""
    try:
        install_enabled, package, min_version = _read_config()
        if not install_enabled:
            return CodexCliResult(success=True, action="disabled", version=_installed_version())
        installed = _installed_version()
        if installed is not None and _version_tuple(installed) >= _version_tuple(min_version):
            return CodexCliResult(success=True, action="skipped", version=installed)
        action = "updated" if installed is not None else "installed"
        success, new_version, error = _npm_install(package)
        return CodexCliResult(
            success=success,
            action=action if success else "failed",
            version=new_version,
            error=error,
        )
    except Exception as exc:  # noqa: BLE001 -- non-fatal by contract
        return CodexCliResult(success=False, action="failed", error=str(exc))
