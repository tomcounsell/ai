"""Budget accounting for a comparison arm (lane 6, #3218).

A comparison of two research processes is only a comparison of the
processes when both arms spent about the same. This module carries one cap
per arm in four units (paid inference in USD, infrastructure in USD,
subscription turns, wall seconds), reads what each arm actually spent, and
decides whether the two arms are comparable. Charter section 8 applied:
uncertain or missing metering is never zero cost, so any ``None`` refuses.

Unit 3 is read from lane 7's ledger. ``admit()`` writes ``reason="admitted"``
on every admitted row, so the arm run id rides on the one field an admitting
caller controls, the resource name: an arm runner admits every reservation
as ``ResourceDecl(name=f"arm:{arm_run_id}:{resource_name}", ...)`` and
:class:`LedgerBudgetReader` sums the rows carrying that prefix. Unit 1 has
no meter until lane 3 (#3215) lands; the reader answers ``None`` and the
comparison says ``BUDGET_UNKNOWN:unit1``.

This module imports nothing from ``arms.py``: ``ArmResult`` holds a
:class:`BudgetUse`, and the dependency runs arms → budget only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

Amount = float | int | None

#: Field name → label used in refusal reasons (``BUDGET_UNKNOWN:unit1``).
UNIT_LABELS: dict[str, str] = {
    "unit1_usd": "unit1",
    "unit3_usd": "unit3",
    "subscription_turns": "subscription_turns",
    "wall_seconds": "wall_seconds",
}

#: Reservation states that represent money spent or committed.
SPENT_STATES: tuple[str, ...] = ("reserved", "settled")

#: Default tolerance: the two arms may differ by this fraction of the cap.
DEFAULT_TOLERANCE = 0.10


@dataclass(frozen=True)
class BudgetCap:
    """One arm's ceiling in each unit. A ``None`` cap is never comparable."""

    unit1_usd: Amount = None
    unit3_usd: Amount = None
    subscription_turns: Amount = None
    wall_seconds: Amount = None


@dataclass(frozen=True)
class BudgetUse:
    """What one arm spent in each unit. ``None`` means unknown, never zero."""

    unit1_usd: Amount = None
    unit3_usd: Amount = None
    subscription_turns: Amount = None
    wall_seconds: Amount = None


class BudgetReader(Protocol):
    """Accounted spend per arm run, from records rather than from the arm."""

    def unit1_usd(self, arm_run_id: str) -> float | None: ...

    def unit3_usd(self, arm_run_id: str) -> float | None: ...


def arm_resource_prefix(arm_run_id: str) -> str:
    """The resource-name prefix every reservation of one arm run carries."""
    return f"arm:{arm_run_id}:"


class LedgerBudgetReader:
    """Reads unit 3 from ``InfrastructureReservation`` rows; unit 1 is unmetered.

    ``unit3_usd`` sums ``settled_usd`` where present, else ``amount_usd``,
    over rows in :data:`SPENT_STATES` whose ``resource`` starts with
    ``arm:<arm_run_id>:``. Zero matched rows answer ``None``: an arm that
    admitted nothing through the ledger is unknown, never free.
    """

    def __init__(self, project_key: str = "valor") -> None:
        self.project_key = project_key

    def unit1_usd(self, arm_run_id: str) -> float | None:
        """No paid-inference meter exists until lane 3 (#3215); always unknown."""
        return None

    def unit3_usd(self, arm_run_id: str) -> float | None:
        from models.improvement_infrastructure_ledger import InfrastructureReservation

        prefix = arm_resource_prefix(arm_run_id)
        total = 0.0
        matched = 0
        for row in InfrastructureReservation.query.filter(project_key=self.project_key):
            resource = row.resource or ""
            if not resource.startswith(prefix) or row.state not in SPENT_STATES:
                continue
            matched += 1
            settled = row.settled_usd
            total += float(settled if settled is not None else (row.amount_usd or 0.0))
        return total if matched else None


def accounted_use(reader: BudgetReader, arm_run_id: str, reported: BudgetUse) -> BudgetUse:
    """The arm's use as the comparison scores it.

    Dollars come from the reader (records), never from the arm's own report;
    subscription turns and wall seconds come from the arm's ``BudgetUse``
    because no record outside the arm carries them.
    """
    return BudgetUse(
        unit1_usd=reader.unit1_usd(arm_run_id),
        unit3_usd=reader.unit3_usd(arm_run_id),
        subscription_turns=reported.subscription_turns,
        wall_seconds=reported.wall_seconds,
    )


def budgets_comparable(
    a: BudgetUse, b: BudgetUse, cap: BudgetCap, tolerance: float = DEFAULT_TOLERANCE
) -> tuple[bool, list[str]]:
    """Decide whether two arms' spend supports a comparison.

    Returns ``(ok, reasons)``. Per unit, in field order, reasons are:

    - ``CAP_UNKNOWN:<unit>`` when the cap is ``None`` (never ok; the use
      checks below still run so the accounting is complete);
    - ``BUDGET_UNKNOWN:<unit>`` when either arm's use is ``None``, once per
      unit;
    - ``BUDGET_EXCEEDED:<arm>:<unit>`` (``a`` or ``b``) when a known use
      exceeds a known cap;
    - ``BUDGET_MISMATCH:<unit>`` when the two known uses differ by more
      than ``tolerance * cap``.

    A unit whose cap is explicitly ``0`` with ``0`` use on both sides is ok:
    a unit not budgeted is not a mismatch.
    """
    reasons: list[str] = []
    for field_name, unit in UNIT_LABELS.items():
        limit = getattr(cap, field_name)
        use_a = getattr(a, field_name)
        use_b = getattr(b, field_name)
        if limit is None:
            reasons.append(f"CAP_UNKNOWN:{unit}")
        if use_a is None or use_b is None:
            reasons.append(f"BUDGET_UNKNOWN:{unit}")
            continue
        if limit is None:
            continue
        for arm, use in (("a", use_a), ("b", use_b)):
            if use > limit:
                reasons.append(f"BUDGET_EXCEEDED:{arm}:{unit}")
        if abs(use_a - use_b) > tolerance * limit:
            reasons.append(f"BUDGET_MISMATCH:{unit}")
    return (not reasons), reasons
