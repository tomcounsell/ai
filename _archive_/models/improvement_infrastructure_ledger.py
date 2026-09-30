"""Unit-3 budget ledger: reservations, refusals, and credit declarations.

Lane 7 (#3274). The decision records around the ``improvement:budget:unit3:``
window counter are ordinary Popoto models, read and written through the ORM.
Only the counter itself is a raw Redis string key (see
``tools/infrastructure_budget.py``), because Popoto offers no compare-and-set
and the counter must be reserved atomically.

Record mapping, stated once so no reader has to infer it:

- reservations, refusals, releases, and credit declarations live here as
  :class:`InfrastructureReservation` rows. A credit is fields on the row that
  declared it, not a second model: the report never queries credits apart
  from the admission that declared them.
- settlements live in :class:`ImprovementEvidence` as ``spend_receipt`` rows,
  recorded through ``record_once`` with the settlement id as ``source_ref``.

Kind ownership: this lane does not add an evidence kind here. ``spend_receipt``
is declared in ``models/improvement_evidence.py`` beside its lane-7 siblings.

TTL decision: 90 days. A budget week is 7 days and the evidence that settles
it expires after 30, so a reservation row must outlive both to stay joinable
to its settlement during audit. Anything needing longer lineage is distilled
onto the immortal ``ImprovementCase``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from popoto import (
    AutoKeyField,
    DatetimeField,
    Field,
    FloatField,
    IndexedField,
    KeyField,
    Model,
    SortedField,
)

#: Every state an :class:`InfrastructureReservation` row may hold. Four values,
#: low-cardinality on purpose — ``state`` backs an index.
RESERVATION_STATES: tuple[str, ...] = (
    "reserved",
    "released",
    "settled",
    "refused",
)


class InfrastructureReservation(Model):
    """One unit-3 admission decision and everything that happened to it.

    Fields:
        id: Auto-generated Popoto primary key.
        project_key: Partition key.
        created_at: Recency sort, partitioned by ``project_key``.
        window_key: The ISO-week window this decision belongs to, e.g.
            ``2026-W37``. Matches the ``improvement:budget:unit3:{window_key}``
            counter suffix.
        resource: Which resource was admitted or refused.
        amount_usd: Dollars reserved against the window (the forecast).
        forecast_usd: The forecast the decision was made on. Equals
            ``amount_usd`` unless the row is a refusal, where it carries the
            forecast that was computed before refusal.
        state: One of :data:`RESERVATION_STATES`.
        reservation_id: Opaque id returned by ``admit()``. The release path is
            idempotent on it: a release recorded here is never applied twice.
        reason: Why a refusal was refused, or why a release happened. The two
            refusal reasons the report distinguishes are ``no forecastable
            rate`` and ``week exhausted`` — they are different states and must
            not both render as "not acquired".
        settled_usd: Actual spend once settled, else None.
        credit_expires_at: When the declared credit covering this resource
            ends, else None. A credit expiring inside the window converts to a
            forecast charge on its expiry day.
        credit_paid_rate_usd: The weekly rate that applies after
            ``credit_expires_at``, else None.
        budget_week_start / budget_day_boundary: The boundary settings this
            decision was computed under, echoed into every record because a
            reservation resetting on an undisclosed boundary cannot be audited.
    """

    id = AutoKeyField()
    project_key = KeyField()
    created_at = SortedField(type=datetime, partition_by="project_key")
    window_key = Field()
    resource = Field()
    amount_usd = FloatField(default=0.0)
    forecast_usd = FloatField(default=0.0)
    state = IndexedField(default="reserved")
    reservation_id = Field()
    reason = Field(null=True)
    settled_usd = FloatField(null=True)
    credit_expires_at = DatetimeField(null=True)
    credit_paid_rate_usd = FloatField(null=True)
    budget_week_start = Field(default="monday")
    budget_day_boundary = Field(default="UTC")

    class Meta:
        # 90 days: outlives the 7-day budget week and the 30-day evidence TTL
        # so a reservation stays joinable to its spend_receipt during audit.
        ttl = 86400 * 90

    @classmethod
    def record_decision(
        cls,
        project_key: str,
        *,
        window_key: str,
        resource: str,
        amount_usd: float,
        forecast_usd: float,
        state: str,
        reservation_id: str,
        reason: str | None = None,
        credit_expires_at: datetime | None = None,
        credit_paid_rate_usd: float | None = None,
        budget_week_start: str = "monday",
        budget_day_boundary: str = "UTC",
    ) -> InfrastructureReservation:
        """Write one ledger row for an admission decision."""
        if not project_key or not project_key.strip():
            raise ValueError("project_key must be a non-empty string")
        if state not in RESERVATION_STATES:
            raise ValueError(f"unknown reservation state {state!r}")
        return cls.create(
            project_key=project_key,
            created_at=datetime.now(UTC),
            window_key=window_key,
            resource=resource,
            amount_usd=amount_usd,
            forecast_usd=forecast_usd,
            state=state,
            reservation_id=reservation_id,
            reason=reason,
            credit_expires_at=credit_expires_at,
            credit_paid_rate_usd=credit_paid_rate_usd,
            budget_week_start=budget_week_start,
            budget_day_boundary=budget_day_boundary,
        )
