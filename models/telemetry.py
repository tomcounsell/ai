"""ObserverTelemetry model - observer agent telemetry tracking.

Provides counter and event tracking for the observer agent's decisions
and interjections. Uses daily rollup keys with 7-day TTL for automatic
cleanup of old telemetry data.
"""

from popoto import Field, IntField, KeyField, ListField, Model


class ObserverTelemetry(Model):
    """Tracks observer agent telemetry data per day.

    Each day gets its own record keyed by date string (e.g. "2026-03-24").
    Counters track decision and interjection counts, while the events list
    stores recent interjection details for dashboard display.

    Fields:
        date_key: Date string in YYYY-MM-DD format
        decisions: Count of observer decisions made
        interjections: Count of interjections triggered
        skips: Count of decisions to skip/not interject
        events: List of recent interjection event descriptions
    """

    date_key = KeyField()
    decisions = IntField(default=0)
    interjections = IntField(default=0)
    skips = IntField(default=0)
    events = ListField(default=list)
    last_decision_context = Field(null=True)

    class Meta:
        ttl = 604800  # 7 days

    # Max events to keep per day
    _MAX_EVENTS = 100

    @classmethod
    def get_or_create(cls, date_key: str) -> "ObserverTelemetry":
        """Get existing record for a date, or create a new one."""
        existing = cls.query.filter(date_key=date_key)
        if existing:
            return existing[0]
        return cls.create(
            date_key=date_key,
            decisions=0,
            interjections=0,
            skips=0,
            events=[],
        )

    def record_decision(self, context: str | None = None) -> None:
        """Record an observer decision."""
        self.decisions = (self.decisions or 0) + 1
        if context:
            self.last_decision_context = context
        self.save()

    def record_interjection(self, description: str) -> None:
        """Record an observer interjection with details."""
        self.interjections = (self.interjections or 0) + 1
        events = list(self.events or [])
        events.append(description)
        if len(events) > self._MAX_EVENTS:
            events = events[-self._MAX_EVENTS :]
        self.events = events
        self.save()

    def record_skip(self) -> None:
        """Record a decision to skip/not interject."""
        self.skips = (self.skips or 0) + 1
        self.save()
