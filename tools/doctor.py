"""Unified health check CLI for local development environments.

Consolidates checks from monitoring/health.py, scripts/update/verify.py,
and monitoring/resource_monitor.py into a single diagnostic command.

Note: Importing scripts/update/verify.py modifies os.environ["PATH"] to
include pyenv, homebrew, and other tool locations. This is intentional --
it ensures the doctor can find the same tools the update system uses.

Usage:
    python -m tools.doctor           # Run all standard checks
    python -m tools.doctor --quick   # Skip slow checks
    python -m tools.doctor --quality # Include ruff/pytest checks
    python -m tools.doctor --json    # Machine-readable output
    python -m tools.doctor --install-hook  # Install git pre-push hook
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Project root (ai/)
PROJECT_DIR = Path(__file__).resolve().parent.parent


@dataclass
class CheckResult:
    """Result of a single health check."""

    name: str
    category: str
    passed: bool
    message: str
    fix: str | None = None

    def to_dict(self) -> dict:
        """Serialize for JSON output."""
        d = {
            "name": self.name,
            "category": self.category,
            "passed": self.passed,
            "message": self.message,
        }
        if self.fix:
            d["fix"] = self.fix
        return d


# ---------------------------------------------------------------------------
# Check wrappers
# ---------------------------------------------------------------------------


def _check_python_version() -> CheckResult:
    """Check Python version is 3.12+."""
    import platform

    version = platform.python_version()
    major, minor = sys.version_info[:2]
    ok = major == 3 and minor >= 12
    return CheckResult(
        name="python_version",
        category="Environment",
        passed=ok,
        message=f"Python {version}",
        fix=None if ok else "Install Python 3.12+: brew install python@3.12",
    )


def _check_venv() -> CheckResult:
    """Check that a virtualenv exists."""
    venv_python = PROJECT_DIR / ".venv" / "bin" / "python"
    ok = venv_python.exists()
    return CheckResult(
        name="virtualenv",
        category="Environment",
        passed=ok,
        message="virtualenv found" if ok else "No .venv/bin/python",
        fix=None if ok else "Run: uv venv && uv pip install -r requirements.txt",
    )


def _check_system_tools() -> list[CheckResult]:
    """Check system-level tools via verify.py."""
    results = []
    try:
        from scripts.update.verify import check_system_tools

        for tc in check_system_tools():
            results.append(
                CheckResult(
                    name=tc.name,
                    category="Environment",
                    passed=tc.available,
                    message=tc.version or ("available" if tc.available else "missing"),
                    fix=tc.error if not tc.available else None,
                )
            )
    except Exception as e:
        results.append(
            CheckResult(
                name="system_tools",
                category="Environment",
                passed=False,
                message=f"Could not check system tools: {e}",
            )
        )
    return results


def _check_python_deps() -> list[CheckResult]:
    """Check core Python dependencies via verify.py."""
    results = []
    try:
        from scripts.update.verify import check_python_deps

        for tc in check_python_deps(PROJECT_DIR):
            results.append(
                CheckResult(
                    name=f"dep:{tc.name}",
                    category="Environment",
                    passed=tc.available,
                    message="installed" if tc.available else "missing",
                    fix=tc.error if not tc.available else None,
                )
            )
    except Exception as e:
        results.append(
            CheckResult(
                name="python_deps",
                category="Environment",
                passed=False,
                message=f"Could not check deps: {e}",
            )
        )
    return results


def _check_redis() -> CheckResult:
    """Check Redis connectivity via HealthChecker."""
    try:
        from monitoring.health import HealthChecker, HealthStatus

        hc = HealthChecker()
        result = hc.check_database()
        passed = result.status == HealthStatus.HEALTHY
        return CheckResult(
            name="redis",
            category="Services",
            passed=passed,
            message=result.message,
            fix=None if passed else "Start Redis: brew services start redis",
        )
    except Exception as e:
        return CheckResult(
            name="redis",
            category="Services",
            passed=False,
            message=f"Redis check failed: {e}",
            fix="Start Redis: brew services start redis",
        )


def _check_bridge() -> CheckResult:
    """Check if Telegram bridge is running."""
    try:
        from scripts.update.service import is_bridge_running

        running = is_bridge_running()
        return CheckResult(
            name="bridge",
            category="Services",
            passed=running,
            message="running" if running else "not running",
            fix=None if running else "Start bridge: ./scripts/valor-service.sh restart",
        )
    except Exception as e:
        return CheckResult(
            name="bridge",
            category="Services",
            passed=False,
            message=f"Could not check bridge: {e}",
        )


def _check_worker() -> CheckResult:
    """Check if standalone worker is running."""
    try:
        from scripts.update.service import is_worker_running

        running = is_worker_running()
        return CheckResult(
            name="worker",
            category="Services",
            passed=running,
            message="running" if running else "not running",
            fix=None if running else "Start worker: ./scripts/valor-service.sh worker-start",
        )
    except Exception as e:
        return CheckResult(
            name="worker",
            category="Services",
            passed=False,
            message=f"Could not check worker: {e}",
        )


def _check_telegram_session(*, quick: bool = False) -> CheckResult:
    """Check Telegram session auth."""
    if quick:
        # In quick mode, just check that session file exists
        data_dir = PROJECT_DIR / "data"
        session_files = list(data_dir.glob("*.session"))
        ok = len(session_files) > 0
        return CheckResult(
            name="telegram_session",
            category="Auth",
            passed=ok,
            message=f"{len(session_files)} session file(s) found" if ok else "No session files",
            fix=None if ok else "Run: python scripts/telegram_login.py",
        )

    try:
        from scripts.update.verify import check_telegram_session

        tc = check_telegram_session(PROJECT_DIR)
        return CheckResult(
            name="telegram_session",
            category="Auth",
            passed=tc.available,
            message=tc.version or ("authorized" if tc.available else "unauthorized"),
            fix=tc.error if not tc.available else None,
        )
    except Exception as e:
        return CheckResult(
            name="telegram_session",
            category="Auth",
            passed=False,
            message=f"Could not check session: {e}",
            fix="Run: python scripts/telegram_login.py",
        )


def _check_api_keys() -> list[CheckResult]:
    """Check API keys via HealthChecker."""
    results = []
    try:
        from monitoring.health import HealthChecker, HealthStatus

        hc = HealthChecker()
        api_results = hc.check_api_keys()
        for name, hcr in api_results.items():
            passed = hcr.status == HealthStatus.HEALTHY
            results.append(
                CheckResult(
                    name=f"api_key:{name}",
                    category="Auth",
                    passed=passed,
                    message=hcr.message,
                    fix=None if passed else f"Set {hcr.details.get('env_var', name)} in .env",
                )
            )
    except Exception as e:
        results.append(
            CheckResult(
                name="api_keys",
                category="Auth",
                passed=False,
                message=f"Could not check API keys: {e}",
            )
        )
    return results


def _check_sdk_auth() -> CheckResult:
    """Check SDK authentication status."""
    try:
        from scripts.update.verify import check_sdk_auth

        auth = check_sdk_auth(PROJECT_DIR)
        api_key_ok = auth.get("api_key_configured", False)
        return CheckResult(
            name="sdk_auth",
            category="Auth",
            passed=api_key_ok,
            message="API key configured" if api_key_ok else "API key not configured",
            fix=None if api_key_ok else "Add ANTHROPIC_API_KEY=sk-ant-... to .env",
        )
    except Exception as e:
        return CheckResult(
            name="sdk_auth",
            category="Auth",
            passed=False,
            message=f"Could not check SDK auth: {e}",
        )


def _check_disk_space() -> CheckResult:
    """Check disk space via HealthChecker."""
    try:
        from monitoring.health import HealthChecker, HealthStatus

        hc = HealthChecker()
        result = hc.check_disk_space()
        passed = result.status == HealthStatus.HEALTHY
        return CheckResult(
            name="disk_space",
            category="Resources",
            passed=passed,
            message=result.message,
            fix=None if passed else "Free up disk space",
        )
    except Exception as e:
        return CheckResult(
            name="disk_space",
            category="Resources",
            passed=False,
            message=f"Could not check disk: {e}",
        )


def _check_memory() -> CheckResult:
    """Check memory usage via resource monitor."""
    try:
        from monitoring.resource_monitor import PSUTIL_AVAILABLE, ResourceSnapshot

        if not PSUTIL_AVAILABLE:
            return CheckResult(
                name="memory",
                category="Resources",
                passed=True,
                message="psutil not installed (skipped)",
            )

        snap = ResourceSnapshot.capture()
        ok = snap.memory_mb < 800  # Critical threshold from CLAUDE.md
        return CheckResult(
            name="memory",
            category="Resources",
            passed=ok,
            message=f"Process memory: {snap.memory_mb:.0f}MB",
            fix=None if ok else "High memory usage detected. Restart services.",
        )
    except Exception as e:
        return CheckResult(
            name="memory",
            category="Resources",
            passed=True,
            message=f"Could not check memory: {e} (non-critical)",
        )


def _check_cpu() -> CheckResult:
    """Check CPU usage."""
    try:
        from monitoring.resource_monitor import PSUTIL_AVAILABLE, ResourceSnapshot

        if not PSUTIL_AVAILABLE:
            return CheckResult(
                name="cpu",
                category="Resources",
                passed=True,
                message="psutil not installed (skipped)",
            )

        snap = ResourceSnapshot.capture()
        ok = snap.cpu_percent < 95  # Critical threshold from CLAUDE.md
        return CheckResult(
            name="cpu",
            category="Resources",
            passed=ok,
            message=f"CPU: {snap.cpu_percent:.1f}%",
            fix=None if ok else "CPU critically high. Check for runaway processes.",
        )
    except Exception as e:
        return CheckResult(
            name="cpu",
            category="Resources",
            passed=True,
            message=f"Could not check CPU: {e} (non-critical)",
        )


def _check_ruff_lint() -> CheckResult:
    """Run ruff check (quality gate)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "."],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=60,
        )
        ok = result.returncode == 0
        if ok:
            msg = "No lint issues"
        else:
            lines = result.stdout.strip().splitlines()
            count = len([line for line in lines if line and not line.startswith("Found")])
            msg = f"{count} lint issue(s)"
        return CheckResult(
            name="ruff_lint",
            category="Quality",
            passed=ok,
            message=msg,
            fix=None if ok else "Run: python -m ruff check . --fix",
        )
    except Exception as e:
        return CheckResult(
            name="ruff_lint",
            category="Quality",
            passed=False,
            message=f"ruff check failed: {e}",
        )


def _check_ruff_format() -> CheckResult:
    """Run ruff format --check (quality gate)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "format", "--check", "."],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=60,
        )
        ok = result.returncode == 0
        if ok:
            msg = "All files formatted"
        else:
            lines = result.stderr.strip().splitlines()
            msg = f"{len(lines)} file(s) need formatting"
        return CheckResult(
            name="ruff_format",
            category="Quality",
            passed=ok,
            message=msg,
            fix=None if ok else "Run: python -m ruff format .",
        )
    except Exception as e:
        return CheckResult(
            name="ruff_format",
            category="Quality",
            passed=False,
            message=f"ruff format check failed: {e}",
        )


def _check_pytest() -> CheckResult:
    """Run pytest unit tests (quality gate)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/unit/", "-x", "-q", "--tb=no"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=300,
        )
        ok = result.returncode == 0
        # Extract summary line (e.g., "42 passed in 5.23s")
        lines = result.stdout.strip().splitlines()
        summary = lines[-1] if lines else "no output"
        return CheckResult(
            name="pytest",
            category="Quality",
            passed=ok,
            message=summary,
            fix=None if ok else "Run: pytest tests/unit/ -x to see failures",
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="pytest",
            category="Quality",
            passed=False,
            message="pytest timed out (>5min)",
            fix="Run pytest manually: pytest tests/unit/ -x",
        )
    except Exception as e:
        return CheckResult(
            name="pytest",
            category="Quality",
            passed=False,
            message=f"pytest failed: {e}",
        )


# ---------------------------------------------------------------------------
# Check registry
# ---------------------------------------------------------------------------


def get_checks(
    *,
    quick: bool = False,
    quality: bool = False,
) -> list[Callable[[], CheckResult | list[CheckResult]]]:
    """Build the ordered list of check functions to run.

    Args:
        quick: If True, skip slow checks (Telegram session probe, model verification).
        quality: If True, include ruff and pytest checks.

    Returns:
        List of callables that return CheckResult or list[CheckResult].
    """
    checks: list[Callable] = [
        # Environment
        _check_python_version,
        _check_venv,
        _check_system_tools,
        _check_python_deps,
        # Services
        _check_redis,
        _check_bridge,
        _check_worker,
        # Auth
        lambda: _check_telegram_session(quick=quick),
        _check_api_keys,
        _check_sdk_auth,
        # Resources
        _check_disk_space,
        _check_memory,
        _check_cpu,
    ]

    if quality:
        checks.extend(
            [
                _check_ruff_lint,
                _check_ruff_format,
                _check_pytest,
            ]
        )

    return checks


def run_checks(*, quick: bool = False, quality: bool = False) -> list[CheckResult]:
    """Execute all registered checks and return results.

    Each check is wrapped in try/except so a single failure
    does not crash the entire run.
    """
    check_fns = get_checks(quick=quick, quality=quality)
    results: list[CheckResult] = []

    for fn in check_fns:
        try:
            result = fn()
            if isinstance(result, list):
                results.extend(result)
            else:
                results.append(result)
        except Exception as e:
            # Determine a name from the function
            name = getattr(fn, "__name__", "unknown")
            if name == "<lambda>":
                name = "check"
            results.append(
                CheckResult(
                    name=name,
                    category="Unknown",
                    passed=False,
                    message=f"Check crashed: {e}",
                )
            )

    return results


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def format_text(results: list[CheckResult]) -> str:
    """Format results as a human-readable report."""
    lines: list[str] = []
    lines.append("")
    lines.append("=== Local Doctor Report ===")
    lines.append("")

    # Group by category
    categories: dict[str, list[CheckResult]] = {}
    for r in results:
        categories.setdefault(r.category, []).append(r)

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    for category, checks in categories.items():
        lines.append(f"--- {category} ---")
        for c in checks:
            icon = "[PASS]" if c.passed else "[FAIL]"
            lines.append(f"  {icon} {c.name}: {c.message}")
            if c.fix and not c.passed:
                lines.append(f"         Fix: {c.fix}")
        lines.append("")

    lines.append(f"Summary: {passed}/{total} passed, {failed} failed")
    if failed == 0:
        lines.append("All checks passed.")
    else:
        lines.append(f"{failed} check(s) need attention.")

    return "\n".join(lines)


def format_json(results: list[CheckResult]) -> str:
    """Format results as JSON."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    output = {
        "passed": failed == 0,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
        },
        "checks": [r.to_dict() for r in results],
    }
    return json.dumps(output, indent=2)


# ---------------------------------------------------------------------------
# Git hook installer
# ---------------------------------------------------------------------------


def install_pre_push_hook() -> bool:
    """Install a git pre-push hook that runs doctor --quick.

    Returns True if installed successfully.
    """
    hooks_dir = PROJECT_DIR / ".git" / "hooks"
    if not hooks_dir.exists():
        print(f"Error: {hooks_dir} does not exist. Are you in a git repo?")
        return False

    hook_path = hooks_dir / "pre-push"
    hook_content = """#!/usr/bin/env bash
# Installed by: python -m tools.doctor --install-hook
# Runs quick health checks before pushing.

set -e

echo "Running doctor checks..."
python -m tools.doctor --quick

if [ $? -ne 0 ]; then
    echo "Doctor checks failed. Fix issues before pushing."
    exit 1
fi
"""

    hook_path.write_text(hook_content)
    hook_path.chmod(0o755)
    print(f"Installed pre-push hook at {hook_path}")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code (0=pass, 1=fail)."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.doctor",
        description="Unified health check for the local development environment.",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Skip slow checks (Telegram session probe, model verification).",
    )
    parser.add_argument(
        "--quality",
        action="store_true",
        help="Include code quality checks (ruff lint, ruff format, pytest).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON.",
    )
    parser.add_argument(
        "--install-hook",
        action="store_true",
        help="Install a git pre-push hook that runs doctor --quick.",
    )

    args = parser.parse_args(argv)

    if args.install_hook:
        ok = install_pre_push_hook()
        return 0 if ok else 1

    results = run_checks(quick=args.quick, quality=args.quality)

    if args.json_output:
        print(format_json(results))
    else:
        print(format_text(results))

    all_passed = all(r.passed for r in results)
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
