"""Alert system for monitoring threshold violations."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable

logger = logging.getLogger(__name__)


class AlertLevel(Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


@dataclass
class Alert:
    """Represents a monitoring alert."""
    level: AlertLevel
    message: str
    metric: str
    current_value: float
    threshold: float
    timestamp: datetime = field(default_factory=datetime.now)
    recommendations: list[str] = field(default_factory=list)
    acknowledged: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "level": self.level.value,
            "message": self.message,
            "metric": self.metric,
            "current_value": self.current_value,
            "threshold": self.threshold,
            "timestamp": self.timestamp.isoformat(),
            "recommendations": self.recommendations,
            "acknowledged": self.acknowledged,
        }

    def __str__(self) -> str:
        return f"[{self.level.value.upper()}] {self.message}"


class AlertManager:
    """Manages alert checking, storage, and notifications."""

    def __init__(self):
        """Initialize the alert manager."""
        self._alerts: list[Alert] = []
        self._handlers: list[Callable[[Alert], None]] = []
        self._max_alerts = 1000

    def register_handler(self, handler: Callable[[Alert], None]) -> None:
        """Register a handler to be called when alerts are sent.

        Args:
            handler: Callable that takes an Alert object.
        """
        self._handlers.append(handler)

    def check_all(self) -> list[Alert]:
        """Run all threshold checks and return new alerts.

        Returns:
            List of new Alert objects.
        """
        from monitoring.resource_monitor import ResourceMonitor

        monitor = ResourceMonitor()
        alerts = monitor.check_thresholds()

        for alert in alerts:
            self.send_alert(alert)

        return alerts

    def send_alert(self, alert: Alert) -> None:
        """Send an alert through all registered handlers.

        Args:
            alert: The Alert to send.
        """
        # Store alert
        self._alerts.append(alert)
        if len(self._alerts) > self._max_alerts:
            self._alerts = self._alerts[-self._max_alerts:]

        # Log the alert
        log_method = {
            AlertLevel.INFO: logger.info,
            AlertLevel.WARNING: logger.warning,
            AlertLevel.CRITICAL: logger.error,
            AlertLevel.EMERGENCY: logger.critical,
        }.get(alert.level, logger.warning)

        log_method(str(alert))

        # Call registered handlers
        for handler in self._handlers:
            try:
                handler(alert)
            except Exception as e:
                logger.error(f"Alert handler failed: {e}")

    def get_recent_alerts(self, hours: int = 24) -> list[Alert]:
        """Get alerts from the last N hours.

        Args:
            hours: Number of hours to look back.

        Returns:
            List of recent alerts.
        """
        cutoff = datetime.now() - timedelta(hours=hours)
        return [a for a in self._alerts if a.timestamp >= cutoff]

    def get_unacknowledged(self) -> list[Alert]:
        """Get all unacknowledged alerts.

        Returns:
            List of unacknowledged alerts.
        """
        return [a for a in self._alerts if not a.acknowledged]

    def acknowledge_alert(self, index: int) -> bool:
        """Acknowledge an alert by index.

        Args:
            index: Index of the alert in recent alerts.

        Returns:
            True if acknowledged, False if index invalid.
        """
        if 0 <= index < len(self._alerts):
            self._alerts[index].acknowledged = True
            return True
        return False

    def clear_old_alerts(self, hours: int = 168) -> int:
        """Clear alerts older than specified hours.

        Args:
            hours: Age threshold in hours (default 1 week).

        Returns:
            Number of alerts cleared.
        """
        cutoff = datetime.now() - timedelta(hours=hours)
        old_count = len(self._alerts)
        self._alerts = [a for a in self._alerts if a.timestamp >= cutoff]
        return old_count - len(self._alerts)

    def get_alert_summary(self) -> dict:
        """Get summary of alert counts by level.

        Returns:
            Dictionary with counts per level.
        """
        summary = {level.value: 0 for level in AlertLevel}
        for alert in self.get_recent_alerts(24):
            summary[alert.level.value] += 1
        return summary
