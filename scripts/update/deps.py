"""Dependency management for update system."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DepSyncResult:
    """Result of dependency sync operation."""
    success: bool
    method: str  # "uv", "pip", or "skipped"
    output: str
    error: str | None = None


@dataclass
class VersionInfo:
    """Installed version of a package."""
    package: str
    version: str | None
    expected: str | None = None
    matches: bool = True


def run_cmd(
    cmd: list[str],
    cwd: Path | None = None,
    check: bool = True,
    timeout: int = 300,
) -> subprocess.CompletedProcess:
    """Run a command and return result."""
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def has_uv() -> bool:
    """Check if uv is available."""
    return shutil.which("uv") is not None


def install_uv() -> bool:
    """Install uv package manager. Returns True if successful."""
    try:
        result = subprocess.run(
            ["curl", "-LsSf", "https://astral.sh/uv/install.sh"],
            capture_output=True,
            text=True,
            check=True,
        )
        install_result = subprocess.run(
            ["sh"],
            input=result.stdout,
            capture_output=True,
            text=True,
            check=True,
        )
        return install_result.returncode == 0
    except Exception:
        return False


def sync_with_uv(project_dir: Path, reinstall: bool = False) -> DepSyncResult:
    """Sync dependencies using uv."""
    cmd = ["uv", "sync", "--all-extras"]
    if reinstall:
        cmd.append("--reinstall")

    try:
        result = run_cmd(cmd, cwd=project_dir, timeout=600)

        # Also install in editable mode
        run_cmd(["uv", "pip", "install", "-e", "."], cwd=project_dir)

        return DepSyncResult(
            success=True,
            method="uv",
            output=result.stdout + result.stderr,
        )
    except subprocess.CalledProcessError as e:
        return DepSyncResult(
            success=False,
            method="uv",
            output=e.stdout + e.stderr if e.stdout else "",
            error=str(e),
        )
    except subprocess.TimeoutExpired:
        return DepSyncResult(
            success=False,
            method="uv",
            output="",
            error="Timeout: uv sync took longer than 10 minutes",
        )


def sync_with_pip(project_dir: Path) -> DepSyncResult:
    """Sync dependencies using pip (fallback)."""
    pip_path = project_dir / ".venv" / "bin" / "pip"

    if not pip_path.exists():
        return DepSyncResult(
            success=False,
            method="pip",
            output="",
            error="No pip found at .venv/bin/pip",
        )

    try:
        result = run_cmd(
            [str(pip_path), "install", "-e", str(project_dir)],
            cwd=project_dir,
            timeout=600,
        )
        return DepSyncResult(
            success=True,
            method="pip",
            output=result.stdout + result.stderr,
        )
    except subprocess.CalledProcessError as e:
        return DepSyncResult(
            success=False,
            method="pip",
            output=e.stdout + e.stderr if e.stdout else "",
            error=str(e),
        )
    except subprocess.TimeoutExpired:
        return DepSyncResult(
            success=False,
            method="pip",
            output="",
            error="Timeout: pip install took longer than 10 minutes",
        )


def sync_dependencies(project_dir: Path, reinstall: bool = False) -> DepSyncResult:
    """
    Sync dependencies using best available method.

    Prefers uv, falls back to pip.
    """
    if has_uv():
        return sync_with_uv(project_dir, reinstall=reinstall)

    # Try to install uv
    if install_uv() and has_uv():
        return sync_with_uv(project_dir, reinstall=reinstall)

    # Fall back to pip
    return sync_with_pip(project_dir)


def get_installed_version(project_dir: Path, package: str) -> str | None:
    """Get installed version of a package."""
    python_path = project_dir / ".venv" / "bin" / "python"

    if not python_path.exists():
        return None

    # Map package names to import names
    import_map = {
        "claude-agent-sdk": "claude_agent_sdk",
    }
    import_name = import_map.get(package, package)

    try:
        result = run_cmd(
            [
                str(python_path),
                "-c",
                f"import {import_name}; print({import_name}.__version__)",
            ],
            cwd=project_dir,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass

    return None


def get_pinned_version(project_dir: Path, package: str) -> str | None:
    """Get pinned version from pyproject.toml."""
    pyproject = project_dir / "pyproject.toml"

    if not pyproject.exists():
        return None

    content = pyproject.read_text()

    # Simple parser for == pins
    for line in content.split("\n"):
        if package in line and "==" in line:
            # Extract version from line like: "telethon==1.36.0",
            parts = line.split("==")
            if len(parts) >= 2:
                version = parts[1].strip().rstrip('",')
                return version

    return None


def verify_critical_versions(project_dir: Path) -> list[VersionInfo]:
    """Verify critical dependency versions match pins."""
    critical_deps = ["telethon", "anthropic", "claude-agent-sdk"]
    results = []

    for dep in critical_deps:
        installed = get_installed_version(project_dir, dep)
        expected = get_pinned_version(project_dir, dep)

        matches = True
        if installed and expected:
            matches = installed == expected
        elif expected and not installed:
            matches = False

        results.append(VersionInfo(
            package=dep,
            version=installed,
            expected=expected,
            matches=matches,
        ))

    return results


def check_dep_files_changed(changed_files: list[str]) -> bool:
    """Check if dependency files are in the changed files list."""
    dep_files = {"pyproject.toml", "uv.lock", "requirements.txt"}
    return bool(dep_files & set(changed_files))
