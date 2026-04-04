"""Rodney install and update module.

Downloads Rodney prebuilt binary from GitHub releases and installs
to ~/.local/bin/rodney. Rodney is a headless Chrome test runner used
by the happy path testing pipeline.

Supports macOS (ARM64, x64) and Linux (ARM64, x64).
No Go toolchain required -- uses prebuilt binaries.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

GITHUB_REPO = "nicois/rodney"
INSTALL_DIR = Path.home() / ".local" / "bin"
BINARY_NAME = "rodney"

# Pin to a specific version for reproducible installs.
PINNED_VERSION = "v0.4.0"


@dataclass
class InstallResult:
    """Result of Rodney install/update operation."""

    success: bool
    action: str  # "installed", "updated", "skipped", "failed"
    version: str | None = None
    error: str | None = None


def get_asset_name() -> str | None:
    """Return the correct tarball asset name for this platform.

    Returns None for unsupported platforms.
    """
    system = platform.system().lower()
    machine = platform.machine().lower()

    if machine in ("arm64", "aarch64"):
        arch = "arm64"
    elif machine in ("x86_64", "amd64"):
        arch = "amd64"
    else:
        return None

    if system not in ("darwin", "linux"):
        return None

    return f"rodney-{system}-{arch}.tar.gz"


def get_installed_version() -> str | None:
    """Get the currently installed Rodney version.

    Returns version string (e.g. "0.4.0") or None if not installed.
    """
    binary = shutil.which(BINARY_NAME)
    if not binary:
        return None

    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            # Extract version number
            for part in output.split():
                if part and part[0].isdigit():
                    return part
                if part.startswith("v") and len(part) > 1 and part[1].isdigit():
                    return part.lstrip("v")
            return output
    except (subprocess.TimeoutExpired, OSError):
        pass

    return None


def install_or_update() -> InstallResult:
    """Install or update Rodney to the pinned version.

    Downloads the platform-specific tarball from GitHub Releases,
    extracts the binary, and installs to ~/.local/bin/rodney.

    All failures are non-fatal (returns InstallResult with error).
    """
    asset_name = get_asset_name()
    if not asset_name:
        return InstallResult(
            success=False,
            action="failed",
            error=f"Unsupported platform: {platform.system()} {platform.machine()}",
        )

    # Check if already at pinned version
    installed = get_installed_version()
    pinned_bare = PINNED_VERSION.lstrip("v")
    if installed == pinned_bare:
        return InstallResult(
            success=True,
            action="skipped",
            version=installed,
        )

    # Ensure install directory exists
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)

    # Download tarball
    download_url = (
        f"https://github.com/{GITHUB_REPO}/releases/download/{PINNED_VERSION}/{asset_name}"
    )

    try:
        with tempfile.TemporaryDirectory(prefix="rodney-") as tmp_dir:
            tmp_path = Path(tmp_dir) / asset_name

            req = urllib.request.Request(download_url, headers={"User-Agent": "valor-update/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                with open(tmp_path, "wb") as f:
                    shutil.copyfileobj(resp, f)

            # Extract rodney binary from tarball
            with tarfile.open(tmp_path, "r:gz") as tar:
                # Look for the rodney binary in the archive
                members = tar.getnames()
                rodney_member = None
                for name in members:
                    if name == BINARY_NAME or name.endswith(f"/{BINARY_NAME}"):
                        rodney_member = name
                        break

                if not rodney_member:
                    return InstallResult(
                        success=False,
                        action="failed",
                        error=f"Binary '{BINARY_NAME}' not found in archive. Contents: {members}",
                    )

                tar.extract(rodney_member, tmp_dir, filter="data")
                extracted_path = Path(tmp_dir) / rodney_member

            # Install binary
            dest = INSTALL_DIR / BINARY_NAME
            extracted_path.chmod(0o755)
            shutil.move(str(extracted_path), str(dest))

    except Exception as e:
        return InstallResult(
            success=False,
            action="failed",
            error=f"Download/install failed: {e}",
        )

    # Verify installation
    new_version = get_installed_version()
    action = "updated" if installed else "installed"

    return InstallResult(
        success=True,
        action=action,
        version=new_version or pinned_bare,
    )
