"""Dependency management for update system."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass, field
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
    # Format: "telethon==1.40.0",  # CRITICAL — comment
    for line in content.split("\n"):
        if package in line and "==" in line:
            # Extract version from line
            parts = line.split("==")
            if len(parts) >= 2:
                version_part = parts[1]
                # Remove trailing comma, quotes, and comments
                # First strip the quote and comma: 1.40.0",  # comment -> 1.40.0
                if '"' in version_part:
                    version_part = version_part.split('"')[0]
                version = version_part.strip().rstrip(",")
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

        results.append(
            VersionInfo(
                package=dep,
                version=installed,
                expected=expected,
                matches=matches,
            )
        )

    return results


def check_dep_files_changed(changed_files: list[str]) -> bool:
    """Check if dependency files are in the changed files list."""
    dep_files = {"pyproject.toml", "uv.lock", "requirements.txt"}
    return bool(dep_files & set(changed_files))


# ---------------------------------------------------------------------------
# PyPI version checking and auto-bump
# ---------------------------------------------------------------------------

AUTO_BUMP_PACKAGES = ["anthropic", "claude-agent-sdk"]


@dataclass
class BumpResult:
    """Result of a single package version bump."""

    package: str
    old_version: str | None
    new_version: str | None
    bumped: bool
    error: str | None = None


@dataclass
class AutoBumpResult:
    """Result of auto-bumping all critical deps."""

    bumps: list[BumpResult] = field(default_factory=list)
    synced: bool = False
    sync_error: str | None = None
    smoke_passed: bool = False
    smoke_output: str = ""
    rolled_back: bool = False

    @property
    def any_bumped(self) -> bool:
        return any(b.bumped for b in self.bumps)


def get_pypi_latest(package: str, timeout: int = 10) -> str | None:
    """Fetch the latest version of a package from PyPI.

    Tries ``pip index versions`` first (works regardless of SSL config),
    falls back to the PyPI JSON API.
    """
    # Method 1: pip index versions (most reliable)
    try:
        result = run_cmd(
            ["pip", "index", "versions", package],
            check=False,
            timeout=timeout,
        )
        if result.returncode == 0 and result.stdout:
            # Output like: "anthropic (0.84.0)\nAvailable versions: ..."
            first_line = result.stdout.strip().split("\n")[0]
            if "(" in first_line and ")" in first_line:
                return first_line.split("(")[1].split(")")[0]
    except Exception:
        pass

    # Method 2: PyPI JSON API
    url = f"https://pypi.org/pypi/{package}/json"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return data.get("info", {}).get("version")
    except Exception:
        return None


def bump_pin_in_pyproject(project_dir: Path, package: str, new_version: str) -> bool:
    """Update the pinned version for a package in pyproject.toml.

    Matches lines like: "anthropic==0.62.0",  # CRITICAL — ...
    Replaces only the version portion, preserving comments.
    """
    pyproject = project_dir / "pyproject.toml"
    if not pyproject.exists():
        return False

    content = pyproject.read_text()
    # Match: "package==VERSION" with optional trailing content
    pattern = re.compile(
        rf'("{re.escape(package)})==([^"]+)"',
    )
    new_content, count = pattern.subn(rf'"\1=={new_version}"', content)
    if count == 0:
        return False

    pyproject.write_text(new_content)
    return True


def run_smoke_test(project_dir: Path) -> tuple[bool, str]:
    """Run a minimal smoke test to verify deps still work.

    Imports critical packages and runs a fast subset of tests.
    Returns (passed, output).
    """
    python_path = project_dir / ".venv" / "bin" / "python"
    if not python_path.exists():
        return False, "No Python venv found"

    # Phase 1: import check
    import_check = (
        "import anthropic; import claude_agent_sdk; print(f'anthropic={anthropic.__version__}')"
    )
    try:
        result = run_cmd(
            [str(python_path), "-c", import_check],
            cwd=project_dir,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            return False, f"Import check failed: {result.stderr}"
    except subprocess.TimeoutExpired:
        return False, "Import check timed out"

    # Phase 2: run one fast test file
    try:
        result = run_cmd(
            [str(python_path), "-m", "pytest", "tests/test_docs_auditor.py", "-x", "-q"],
            cwd=project_dir,
            check=False,
            timeout=60,
        )
        output = result.stdout + result.stderr
        if result.returncode != 0:
            return False, f"Smoke test failed:\n{output}"
        return True, output.strip()
    except subprocess.TimeoutExpired:
        return False, "Smoke test timed out (60s)"


def auto_bump_deps(project_dir: Path) -> AutoBumpResult:
    """Check PyPI for newer versions of critical deps, bump pins, sync, and test.

    If the smoke test fails after bumping, rolls back pyproject.toml.
    """
    result = AutoBumpResult()
    pyproject = project_dir / "pyproject.toml"

    # Save original content for rollback
    original_content = pyproject.read_text() if pyproject.exists() else ""

    for package in AUTO_BUMP_PACKAGES:
        current = get_pinned_version(project_dir, package)
        latest = get_pypi_latest(package)

        if not latest or not current:
            result.bumps.append(
                BumpResult(
                    package=package,
                    old_version=current,
                    new_version=latest,
                    bumped=False,
                    error="Could not determine current or latest version",
                )
            )
            continue

        if current == latest:
            result.bumps.append(
                BumpResult(
                    package=package,
                    old_version=current,
                    new_version=latest,
                    bumped=False,
                )
            )
            continue

        if bump_pin_in_pyproject(project_dir, package, latest):
            result.bumps.append(
                BumpResult(
                    package=package,
                    old_version=current,
                    new_version=latest,
                    bumped=True,
                )
            )
        else:
            result.bumps.append(
                BumpResult(
                    package=package,
                    old_version=current,
                    new_version=latest,
                    bumped=False,
                    error="Failed to update pyproject.toml",
                )
            )

    if not result.any_bumped:
        return result

    # Sync dependencies with new pins
    sync_result = sync_dependencies(project_dir)
    result.synced = sync_result.success
    if not sync_result.success:
        result.sync_error = sync_result.error
        # Roll back
        pyproject.write_text(original_content)
        sync_dependencies(project_dir)  # restore old deps
        result.rolled_back = True
        return result

    # Run smoke test
    passed, output = run_smoke_test(project_dir)
    result.smoke_passed = passed
    result.smoke_output = output

    if not passed:
        # Roll back
        pyproject.write_text(original_content)
        sync_dependencies(project_dir)
        result.rolled_back = True

    return result
