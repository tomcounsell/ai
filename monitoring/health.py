"""Health check system for validating system components."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


class HealthStatus(Enum):
    """Health check status."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthCheckResult:
    """Result of a health check."""

    component: str
    status: HealthStatus
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "component": self.component,
            "status": self.status.value,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "details": self.details,
        }


@dataclass
class OverallHealth:
    """Overall system health summary."""

    status: HealthStatus
    score: float
    checks: list[HealthCheckResult]
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "status": self.status.value,
            "score": self.score,
            "checks": [c.to_dict() for c in self.checks],
            "timestamp": self.timestamp.isoformat(),
        }


class HealthChecker:
    """System health checker for validating components."""

    def __init__(self, data_dir: Path | None = None):
        """Initialize the health checker.

        Args:
            data_dir: Path to data directory. Defaults to data/.
        """
        self.data_dir = data_dir or Path(__file__).parent.parent / "data"

    def check_database(self) -> HealthCheckResult:
        """Check database connectivity and status.

        Returns:
            HealthCheckResult for database.
        """
        db_path = self.data_dir / "valor.db"

        if not db_path.exists():
            # Check for session files instead
            session_files = list(self.data_dir.glob("*.session"))
            if session_files:
                return HealthCheckResult(
                    component="database",
                    status=HealthStatus.HEALTHY,
                    message="Session files present",
                    details={"session_count": len(session_files)},
                )
            return HealthCheckResult(
                component="database",
                status=HealthStatus.DEGRADED,
                message="No database or session files found",
                details={"path": str(db_path)},
            )

        try:
            import sqlite3

            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            conn.close()
            return HealthCheckResult(
                component="database",
                status=HealthStatus.HEALTHY,
                message="Database connected successfully",
                details={
                    "path": str(db_path),
                    "size_mb": db_path.stat().st_size / (1024 * 1024),
                },
            )
        except Exception as e:
            return HealthCheckResult(
                component="database",
                status=HealthStatus.UNHEALTHY,
                message=f"Database error: {str(e)}",
                details={"error": str(e)},
            )

    def check_telegram_connection(self) -> HealthCheckResult:
        """Check Telegram connection status.

        Returns:
            HealthCheckResult for Telegram.
        """
        # Check if session file exists
        session_file = self.data_dir / "valor_bridge.session"

        if not session_file.exists():
            return HealthCheckResult(
                component="telegram",
                status=HealthStatus.DEGRADED,
                message="No Telegram session file found",
                details={"path": str(session_file)},
            )

        # Check if bridge is running by looking for PID file or process
        try:
            import subprocess

            result = subprocess.run(
                ["pgrep", "-f", "telegram_bridge"], capture_output=True, text=True
            )
            if result.returncode == 0:
                return HealthCheckResult(
                    component="telegram",
                    status=HealthStatus.HEALTHY,
                    message="Telegram bridge running",
                    details={"pids": result.stdout.strip().split("\n")},
                )
            return HealthCheckResult(
                component="telegram",
                status=HealthStatus.DEGRADED,
                message="Telegram bridge not running",
                details={"session_exists": True},
            )
        except Exception as e:
            return HealthCheckResult(
                component="telegram",
                status=HealthStatus.UNKNOWN,
                message=f"Could not check Telegram status: {str(e)}",
                details={"error": str(e)},
            )

    def check_api_keys(self) -> dict[str, HealthCheckResult]:
        """Check if required API keys are configured.

        Returns:
            Dictionary mapping API name to HealthCheckResult.
        """
        api_keys = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "perplexity": "PERPLEXITY_API_KEY",
            "github": "GITHUB_TOKEN",
            "sentry": "SENTRY_API_KEY",
        }

        results: dict[str, HealthCheckResult] = {}

        for name, env_var in api_keys.items():
            value = os.environ.get(env_var)
            if value:
                results[name] = HealthCheckResult(
                    component=f"api_key_{name}",
                    status=HealthStatus.HEALTHY,
                    message=f"{name} API key configured",
                    details={"env_var": env_var, "length": len(value)},
                )
            else:
                results[name] = HealthCheckResult(
                    component=f"api_key_{name}",
                    status=HealthStatus.DEGRADED,
                    message=f"{name} API key not set",
                    details={"env_var": env_var},
                )

        return results

    def check_disk_space(self) -> HealthCheckResult:
        """Check available disk space.

        Returns:
            HealthCheckResult for disk space.
        """
        try:
            total, used, free = shutil.disk_usage("/")
            percent_used = (used / total) * 100
            free_gb = free / (1024**3)

            if percent_used > 95:
                status = HealthStatus.UNHEALTHY
                message = f"Critical: Only {free_gb:.1f}GB free"
            elif percent_used > 85:
                status = HealthStatus.DEGRADED
                message = f"Warning: {free_gb:.1f}GB free"
            else:
                status = HealthStatus.HEALTHY
                message = f"Healthy: {free_gb:.1f}GB free"

            return HealthCheckResult(
                component="disk_space",
                status=status,
                message=message,
                details={
                    "total_gb": total / (1024**3),
                    "used_gb": used / (1024**3),
                    "free_gb": free_gb,
                    "percent_used": percent_used,
                },
            )
        except Exception as e:
            return HealthCheckResult(
                component="disk_space",
                status=HealthStatus.UNKNOWN,
                message=f"Could not check disk space: {str(e)}",
                details={"error": str(e)},
            )

    def check_clawdbot(self) -> HealthCheckResult:
        """Check if clawdbot is available.

        Returns:
            HealthCheckResult for clawdbot.
        """
        try:
            import subprocess

            result = subprocess.run(
                ["which", "clawdbot"], capture_output=True, text=True
            )
            if result.returncode == 0:
                return HealthCheckResult(
                    component="clawdbot",
                    status=HealthStatus.HEALTHY,
                    message="Clawdbot installed",
                    details={"path": result.stdout.strip()},
                )
            return HealthCheckResult(
                component="clawdbot",
                status=HealthStatus.UNHEALTHY,
                message="Clawdbot not found",
            )
        except Exception as e:
            return HealthCheckResult(
                component="clawdbot",
                status=HealthStatus.UNKNOWN,
                message=f"Could not check clawdbot: {str(e)}",
                details={"error": str(e)},
            )

    def get_overall_health(self) -> OverallHealth:
        """Run all health checks and return overall status.

        Returns:
            OverallHealth summary.
        """
        checks: list[HealthCheckResult] = []

        # Run all checks
        checks.append(self.check_database())
        checks.append(self.check_telegram_connection())
        checks.append(self.check_disk_space())
        checks.append(self.check_clawdbot())

        # Add API key checks
        api_results = self.check_api_keys()
        checks.extend(api_results.values())

        # Calculate overall status
        unhealthy_count = sum(1 for c in checks if c.status == HealthStatus.UNHEALTHY)
        degraded_count = sum(1 for c in checks if c.status == HealthStatus.DEGRADED)
        healthy_count = sum(1 for c in checks if c.status == HealthStatus.HEALTHY)

        total = len(checks)
        score = (healthy_count * 100 + degraded_count * 50) / total

        if unhealthy_count > 0:
            status = HealthStatus.UNHEALTHY
        elif degraded_count > healthy_count:
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.HEALTHY

        return OverallHealth(
            status=status,
            score=score,
            checks=checks,
        )
