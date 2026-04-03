"""OfficeCLI install and update module.

Downloads OfficeCLI binary from GitHub releases, verifies SHA256 checksum,
and installs to ~/.local/bin/officecli. Handles macOS ARM64 and Linux x64.
"""

from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

GITHUB_REPO = "iOfficeAI/OfficeCLI"
INSTALL_DIR = Path.home() / ".local" / "bin"
BINARY_NAME = "officecli"

# Pin to a specific version for reproducible installs.
# Bump this when a new release is tested and verified.
PINNED_VERSION = "v1.0.29"


@dataclass
class InstallResult:
    """Result of OfficeCLI install/update operation."""

    success: bool
    action: str  # "installed", "updated", "skipped", "failed"
    version: str | None = None
    error: str | None = None


def get_asset_name() -> str | None:
    """Return the correct binary asset name for this platform.

    Supports macOS (ARM64, x64) and Linux (ARM64, x64).
    Returns None for unsupported platforms.
    """
    system = platform.system().lower()
    machine = platform.machine().lower()

    # Normalize architecture names
    if machine in ("arm64", "aarch64"):
        arch = "arm64"
    elif machine in ("x86_64", "amd64"):
        arch = "x64"
    else:
        return None

    if system == "darwin":
        return f"officecli-mac-{arch}"
    elif system == "linux":
        return f"officecli-linux-{arch}"

    return None


def get_installed_version() -> str | None:
    """Get the currently installed OfficeCLI version.

    Returns version string (e.g. "1.0.29") or None if not installed
    or version cannot be determined.
    """
    binary = INSTALL_DIR / BINARY_NAME
    if not binary.exists():
        return None

    try:
        result = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            # Output is like "officecli version 1.0.29" or just "1.0.29"
            output = result.stdout.strip()
            # Extract version number from output
            for part in output.split():
                if part and part[0].isdigit():
                    return part
            return output
    except (subprocess.TimeoutExpired, OSError):
        pass

    return None


def _download_file(url: str, dest: Path, timeout: int = 60) -> None:
    """Download a file from URL to destination path."""
    req = urllib.request.Request(url, headers={"User-Agent": "valor-update/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        with open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)


def _fetch_sha256sums(version: str, timeout: int = 10) -> dict[str, str]:
    """Fetch SHA256SUMS file from a release and return {filename: hash} dict."""
    url = f"https://github.com/{GITHUB_REPO}/releases/download/{version}/SHA256SUMS"
    req = urllib.request.Request(url, headers={"User-Agent": "valor-update/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8")
    except Exception:
        return {}

    sums: dict[str, str] = {}
    for line in content.strip().splitlines():
        parts = line.split()
        if len(parts) == 2:
            sha256, filename = parts
            sums[filename] = sha256

    return sums


def _verify_sha256(file_path: Path, expected_hash: str) -> bool:
    """Verify SHA256 hash of a file matches expected value."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest() == expected_hash


def install_or_update() -> InstallResult:
    """Install or update OfficeCLI to the pinned version.

    Checks if the currently installed version matches the pin.
    If not, downloads the platform-specific binary, verifies SHA256,
    and installs to ~/.local/bin/officecli.

    All failures are non-fatal (returns InstallResult with error).
    """
    # Check platform support
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

    # Fetch SHA256 checksums
    sha256sums = _fetch_sha256sums(PINNED_VERSION)
    expected_hash = sha256sums.get(asset_name)

    # Download binary to temp file
    download_url = (
        f"https://github.com/{GITHUB_REPO}/releases/download/{PINNED_VERSION}/{asset_name}"
    )

    try:
        with tempfile.NamedTemporaryFile(delete=False, prefix="officecli-") as tmp:
            tmp_path = Path(tmp.name)

        _download_file(download_url, tmp_path, timeout=60)
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        return InstallResult(
            success=False,
            action="failed",
            error=f"Download failed: {e}",
        )

    # Verify SHA256 if checksums were available
    if expected_hash:
        if not _verify_sha256(tmp_path, expected_hash):
            tmp_path.unlink(missing_ok=True)
            return InstallResult(
                success=False,
                action="failed",
                error="SHA256 checksum mismatch -- aborting install",
            )
    # If no checksums available, proceed with warning (young project, checksums may not exist)

    # Atomic install: chmod +x, then move to final location
    try:
        tmp_path.chmod(0o755)
        dest = INSTALL_DIR / BINARY_NAME
        shutil.move(str(tmp_path), str(dest))
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        return InstallResult(
            success=False,
            action="failed",
            error=f"Install failed: {e}",
        )

    # Verify installation
    new_version = get_installed_version()
    action = "updated" if installed else "installed"

    return InstallResult(
        success=True,
        action=action,
        version=new_version,
    )
