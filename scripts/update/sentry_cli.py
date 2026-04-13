"""Sentry CLI install and verification module.

Installs sentry-cli via the official installer script and verifies it's available.
Used by the update orchestrator to ensure sentry-cli is present on all machines.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class InstallResult:
    """Result of sentry-cli install/update operation."""

    success: bool
    action: str  # "installed", "skipped", "failed"
    version: str | None = None
    error: str | None = None


def check_sentry_cli() -> InstallResult:
    """Check if sentry-cli is installed and return its version.

    Returns:
        InstallResult with action "skipped" if present, "failed" if absent.
    """
    binary = shutil.which("sentry-cli")
    if not binary:
        return InstallResult(
            success=False,
            action="failed",
            error="sentry-cli not found on PATH",
        )

    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            # Output is like "sentry-cli 2.x.y"
            version = result.stdout.strip()
            # Extract version number
            for part in version.split():
                if part and part[0].isdigit():
                    version = part
                    break
            return InstallResult(
                success=True,
                action="skipped",
                version=version,
            )
    except (subprocess.TimeoutExpired, OSError) as e:
        return InstallResult(
            success=False,
            action="failed",
            error=f"Version check failed: {e}",
        )

    return InstallResult(
        success=False,
        action="failed",
        error=f"sentry-cli returned exit code {result.returncode}",
    )


def install_sentry_cli() -> InstallResult:
    """Install sentry-cli using the official installer script.

    Runs: curl -sL https://sentry.io/get-cli/ | bash

    Returns:
        InstallResult with action "installed" on success, "failed" on error.
    """
    try:
        result = subprocess.run(
            ["bash", "-c", "curl -sL https://sentry.io/get-cli/ | bash"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            return InstallResult(
                success=False,
                action="failed",
                error=f"Installer failed: {error_msg}",
            )
    except subprocess.TimeoutExpired:
        return InstallResult(
            success=False,
            action="failed",
            error="Installer timed out after 120s",
        )
    except OSError as e:
        return InstallResult(
            success=False,
            action="failed",
            error=f"Could not run installer: {e}",
        )

    # Verify installation succeeded
    check = check_sentry_cli()
    if check.success:
        return InstallResult(
            success=True,
            action="installed",
            version=check.version,
        )

    return InstallResult(
        success=False,
        action="failed",
        error="Installer completed but sentry-cli not found on PATH",
    )


def install_or_update() -> InstallResult:
    """Install sentry-cli if not already present.

    Checks if sentry-cli is available; if so, skips. Otherwise runs
    the official installer. All failures are non-fatal.

    Returns:
        InstallResult with action "installed", "skipped", or "failed".
    """
    check = check_sentry_cli()
    if check.success:
        return check  # Already installed, skip

    return install_sentry_cli()
