"""npm global tool install and update module.

Manages npm global packages needed by Valor skills (e.g. excalidraw-export).
Uses `npm install -g` for install and `npm list -g` for version checks.
All failures are non-fatal.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field


@dataclass
class NpmToolResult:
    """Result of a single npm tool install/update."""

    name: str
    success: bool
    action: str  # "installed", "updated", "skipped", "failed"
    version: str | None = None
    error: str | None = None


@dataclass
class NpmToolsResult:
    """Aggregate result for all managed npm tools."""

    results: list[NpmToolResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return all(r.success for r in self.results)

    @property
    def any_installed_or_updated(self) -> bool:
        return any(r.action in ("installed", "updated") for r in self.results)


# npm packages to keep installed globally.
# Format: (package_name, pinned_version_or_None_for_latest)
# Use pinned versions for stability; None means "latest".
MANAGED_PACKAGES: list[tuple[str, str | None]] = [
    ("@moona3k/excalidraw-export", None),  # Excalidraw → PNG headless renderer
]


def _npm_bin() -> str | None:
    """Return path to npm, or None if not installed."""
    return shutil.which("npm")


def _get_installed_version(package: str) -> str | None:
    """Return the globally installed version of a package, or None."""
    npm = _npm_bin()
    if not npm:
        return None

    try:
        result = subprocess.run(
            [npm, "list", "-g", "--depth=0", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode not in (0, 1):  # npm list exits 1 when packages missing
            return None

        import json

        data = json.loads(result.stdout or "{}")
        dependencies = data.get("dependencies", {})
        pkg_data = dependencies.get(package, {})
        return pkg_data.get("version")
    except Exception:
        return None


def _install_package(package: str, version: str | None) -> tuple[bool, str | None, str | None]:
    """
    Install or update a global npm package.

    Returns (success, installed_version, error_message).
    """
    npm = _npm_bin()
    if not npm:
        return False, None, "npm not found — install Node.js first"

    spec = f"{package}@{version}" if version else package

    try:
        result = subprocess.run(
            [npm, "install", "-g", spec],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "unknown error").strip()
            return False, None, err[:200]

        # Re-check installed version after install
        installed = _get_installed_version(package)
        return True, installed, None
    except subprocess.TimeoutExpired:
        return False, None, "npm install timed out after 120s"
    except Exception as e:
        return False, None, str(e)


def install_or_update() -> NpmToolsResult:
    """Install or update all managed npm global packages.

    Skips packages that are already installed (any version when no pin,
    or matching version when pinned). All failures are non-fatal.
    """
    result = NpmToolsResult()

    if not _npm_bin():
        # npm not available — skip silently (non-fatal)
        result.results.append(
            NpmToolResult(
                name="npm",
                success=False,
                action="failed",
                error="npm not found — Node.js not installed",
            )
        )
        return result

    for package, pinned_version in MANAGED_PACKAGES:
        installed = _get_installed_version(package)

        if installed:
            if pinned_version is None or installed == pinned_version:
                # Already installed, no pin or version matches
                result.results.append(
                    NpmToolResult(
                        name=package,
                        success=True,
                        action="skipped",
                        version=installed,
                    )
                )
                continue

        # Not installed or version mismatch — install/update
        action = "updated" if installed else "installed"
        success, new_version, error = _install_package(package, pinned_version)
        result.results.append(
            NpmToolResult(
                name=package,
                success=success,
                action=action if success else "failed",
                version=new_version,
                error=error,
            )
        )

    return result
