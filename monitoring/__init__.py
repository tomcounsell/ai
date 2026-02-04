"""Resource monitoring and health check system.

This module provides:
- Real-time resource monitoring (memory, CPU)
- Session tracking and management
- Alert system for threshold violations
- Health checks for system components

Example usage:
    from monitoring import ResourceMonitor, AlertManager, HealthChecker

    monitor = ResourceMonitor()
    snapshot = monitor.get_current_snapshot()
    health_score = monitor.calculate_health_score()

    if health_score < 80:
        alerts = AlertManager().check_all()
        for alert in alerts:
            print(f"Alert: {alert.message}")
"""

from monitoring.alerts import Alert, AlertLevel, AlertManager
from monitoring.health import HealthChecker, HealthStatus
from monitoring.resource_monitor import (
    ResourceLimits,
    ResourceMonitor,
    ResourceSnapshot,
)
from monitoring.session_tracker import Session, SessionTracker

__all__ = [
    "ResourceMonitor",
    "ResourceSnapshot",
    "ResourceLimits",
    "AlertManager",
    "Alert",
    "AlertLevel",
    "HealthChecker",
    "HealthStatus",
    "SessionTracker",
    "Session",
]
