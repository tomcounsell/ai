"""TeammateMetrics model - Popoto-backed teammate classification metrics.

Single-instance pattern: one record keyed by a fixed identifier ("global")
stores all classification counters and response time data. This replaces
the raw Redis counters that were previously in agent/teammate_metrics.py.

Fields:
    key: Fixed identifier ("global") for the single-instance pattern
    teammate_classified_count: Number of teammate classifications above threshold
    teammate_low_confidence_count: Number of teammate classifications below threshold
    work_classified_count: Number of work classifications
    teammate_response_times: Sorted set of response times (score=timestamp)
    work_response_times: Sorted set of response times (score=timestamp)
"""

from popoto import IntField, KeyField, Model, SortedField


class TeammateMetrics(Model):
    """Popoto model for teammate mode classification metrics.

    Uses a single-instance pattern: one record keyed by "global" stores
    all counters. This mirrors the raw Redis pattern (fixed key prefixes)
    but with proper ORM lifecycle, index management, and cleanup.
    """

    key = KeyField(default="global")
    teammate_classified_count = IntField(default=0)
    teammate_low_confidence_count = IntField(default=0)
    work_classified_count = IntField(default=0)
    teammate_response_times = SortedField(default=dict)
    work_response_times = SortedField(default=dict)

    # Max response time entries to keep per mode
    _MAX_RESPONSE_TIMES = 1000

    @classmethod
    def get_or_create(cls) -> "TeammateMetrics":
        """Get the singleton metrics record, creating if needed."""
        existing = cls.query.filter(key="global")
        if existing:
            return existing[0]
        return cls.create(
            key="global",
            teammate_classified_count=0,
            teammate_low_confidence_count=0,
            work_classified_count=0,
        )
