"""Resource monitoring for system health tracking."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monitoring.alerts import Alert

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


@dataclass
class ResourceSnapshot:
    """Snapshot of system resources at a point in time."""
    timestamp: datetime
    memory_mb: float
    cpu_percent: float
    active_sessions: int
    total_processes: int
    disk_percent: float = 0.0

    @classmethod
    def capture(cls, active_sessions: int = 0) -> ResourceSnapshot:
        """Capture current resource state."""
        if not PSUTIL_AVAILABLE:
            return cls(
                timestamp=datetime.now(),
                memory_mb=0.0,
                cpu_percent=0.0,
                active_sessions=active_sessions,
                total_processes=1,
                disk_percent=0.0,
            )

        process = psutil.Process()
        return cls(
            timestamp=datetime.now(),
            memory_mb=process.memory_info().rss / (1024 * 1024),
            cpu_percent=process.cpu_percent(interval=0.1),
            active_sessions=active_sessions,
            total_processes=len(psutil.pids()),
            disk_percent=psutil.disk_usage("/").percent,
        )


@dataclass
class ResourceLimits:
    """Resource limit thresholds for monitoring."""
    max_memory_mb: float = 500.0
    max_memory_per_session_mb: float = 50.0
    max_cpu_percent: float = 80.0
    max_sessions: int = 100
    emergency_memory_mb: float = 800.0
    restart_memory_threshold_mb: float = 1200.0
    warning_memory_mb: float = 400.0
    warning_cpu_percent: float = 60.0
    max_disk_percent: float = 90.0


class ResourceMonitor:
    """Monitor system resources and provide health assessments."""

    def __init__(self, limits: ResourceLimits | None = None):
        """Initialize the resource monitor.

        Args:
            limits: Resource limits for threshold checking. Defaults to standard limits.
        """
        self.limits = limits or ResourceLimits()
        self._active_sessions = 0
        self._history: list[ResourceSnapshot] = []
        self._max_history = 100

    def get_current_snapshot(self) -> ResourceSnapshot:
        """Capture and return current resource state.

        Returns:
            ResourceSnapshot with current metrics.
        """
        snapshot = ResourceSnapshot.capture(self._active_sessions)
        self._history.append(snapshot)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
        return snapshot

    def calculate_health_score(self) -> float:
        """Calculate overall system health score (0-100).

        Returns:
            Health score from 0 (critical) to 100 (healthy).
        """
        snapshot = self.get_current_snapshot()

        # Memory health (40% weight)
        memory_percent = (snapshot.memory_mb / self.limits.max_memory_mb) * 100
        memory_health = max(0, 100 - (memory_percent * 1.5))

        # CPU health (30% weight)
        cpu_health = max(0, 100 - (snapshot.cpu_percent * 1.2))

        # Session load health (30% weight)
        session_load = self._active_sessions / max(self.limits.max_sessions, 1)
        session_health = max(0, 100 - (session_load * 100))

        return (memory_health * 0.4) + (cpu_health * 0.3) + (session_health * 0.3)

    def check_thresholds(self) -> list[Alert]:
        """Check all thresholds and return any violations.

        Returns:
            List of Alert objects for threshold violations.
        """
        from monitoring.alerts import Alert, AlertLevel

        snapshot = self.get_current_snapshot()
        alerts: list[Alert] = []

        # Memory checks
        if snapshot.memory_mb >= self.limits.emergency_memory_mb:
            alerts.append(Alert(
                level=AlertLevel.EMERGENCY,
                message=f"Emergency memory usage: {snapshot.memory_mb:.1f}MB",
                metric="memory_mb",
                current_value=snapshot.memory_mb,
                threshold=self.limits.emergency_memory_mb,
                recommendations=["Restart system immediately", "Kill non-essential processes"]
            ))
        elif snapshot.memory_mb >= self.limits.max_memory_mb:
            alerts.append(Alert(
                level=AlertLevel.CRITICAL,
                message=f"Critical memory usage: {snapshot.memory_mb:.1f}MB",
                metric="memory_mb",
                current_value=snapshot.memory_mb,
                threshold=self.limits.max_memory_mb,
                recommendations=["Trigger cleanup", "Close stale sessions"]
            ))
        elif snapshot.memory_mb >= self.limits.warning_memory_mb:
            alerts.append(Alert(
                level=AlertLevel.WARNING,
                message=f"High memory usage: {snapshot.memory_mb:.1f}MB",
                metric="memory_mb",
                current_value=snapshot.memory_mb,
                threshold=self.limits.warning_memory_mb,
                recommendations=["Monitor closely", "Prepare for cleanup"]
            ))

        # CPU checks
        if snapshot.cpu_percent >= self.limits.max_cpu_percent:
            alerts.append(Alert(
                level=AlertLevel.CRITICAL,
                message=f"Critical CPU usage: {snapshot.cpu_percent:.1f}%",
                metric="cpu_percent",
                current_value=snapshot.cpu_percent,
                threshold=self.limits.max_cpu_percent,
                recommendations=["Reduce parallel operations", "Check for runaway processes"]
            ))
        elif snapshot.cpu_percent >= self.limits.warning_cpu_percent:
            alerts.append(Alert(
                level=AlertLevel.WARNING,
                message=f"High CPU usage: {snapshot.cpu_percent:.1f}%",
                metric="cpu_percent",
                current_value=snapshot.cpu_percent,
                threshold=self.limits.warning_cpu_percent,
                recommendations=["Monitor for sustained high usage"]
            ))

        # Disk checks
        if snapshot.disk_percent >= self.limits.max_disk_percent:
            alerts.append(Alert(
                level=AlertLevel.CRITICAL,
                message=f"Critical disk usage: {snapshot.disk_percent:.1f}%",
                metric="disk_percent",
                current_value=snapshot.disk_percent,
                threshold=self.limits.max_disk_percent,
                recommendations=["Clean up logs and temp files", "Archive old data"]
            ))

        return alerts

    def get_recommendations(self) -> list[str]:
        """Get recommendations based on current state.

        Returns:
            List of recommendation strings.
        """
        recommendations: list[str] = []
        snapshot = self.get_current_snapshot()

        if snapshot.memory_mb > self.limits.warning_memory_mb:
            recommendations.append(f"Memory at {snapshot.memory_mb:.1f}MB - consider cleanup")

        if snapshot.cpu_percent > self.limits.warning_cpu_percent:
            recommendations.append(f"CPU at {snapshot.cpu_percent:.1f}% - reduce load")

        if self._active_sessions > self.limits.max_sessions * 0.8:
            recommendations.append(f"Session load high ({self._active_sessions}) - cleanup stale sessions")

        health_score = self.calculate_health_score()
        if health_score < 50:
            recommendations.append("System health critical - immediate attention needed")
        elif health_score < 70:
            recommendations.append("System health degraded - monitor closely")

        return recommendations

    def should_trigger_cleanup(self) -> bool:
        """Determine if cleanup should be triggered.

        Returns:
            True if cleanup is recommended.
        """
        snapshot = self.get_current_snapshot()
        return (
            snapshot.memory_mb > self.limits.warning_memory_mb or
            self._active_sessions > self.limits.max_sessions * 0.8 or
            self.calculate_health_score() < 70
        )

    def should_restart(self) -> bool:
        """Determine if system restart is recommended.

        Returns:
            True if restart is recommended.
        """
        snapshot = self.get_current_snapshot()
        return snapshot.memory_mb >= self.limits.restart_memory_threshold_mb

    def set_active_sessions(self, count: int) -> None:
        """Update the active session count.

        Args:
            count: Number of active sessions.
        """
        self._active_sessions = count

    def get_history(self, limit: int = 10) -> list[ResourceSnapshot]:
        """Get recent resource snapshots.

        Args:
            limit: Maximum number of snapshots to return.

        Returns:
            List of recent ResourceSnapshots.
        """
        return self._history[-limit:]
