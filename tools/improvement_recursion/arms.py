"""The arm runner seam (lane 6, #3218).

A comparison runs two research processes on the same fresh opportunities
under the same cap and scores the paired validated gains. The process
itself lives in lane 5; this module is the seam it plugs into. An
:class:`ArmRunner` has one method. The comparison finds one in two ways:
``resolve_arm_runner("module:attr")`` imports lazily at the moment a
comparison runs, and ``get_arm_runner()`` answers the runner registered
in-process by ``register_arm_runner``. Absence is a named condition,
:class:`ArmRunnerAbsent`, never an import error.

Lane 5 imports exactly four names from here: ``ArmResult``, ``BudgetUse``,
``register_arm_runner``, ``get_arm_runner``. ``BudgetUse`` and ``BudgetCap``
are defined in ``budget.py`` and re-exported below through a plain
module-level import, so the names exist at call time and never only for a
type checker. The dependency runs arms → budget only.

:class:`ReplayArmRunner` returns gains and budget use from a fixture and
admits its unit-3 spend through lane 7's ``admit()`` under the
``arm:<arm_run_id>:`` resource prefix, so ``LedgerBudgetReader`` is
exercised on the same path a production runner takes.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from tools.improvement_recursion.budget import BudgetCap, BudgetUse

__all__ = [
    "ArmRunner",
    "ArmResult",
    "ArmRunnerAbsent",
    "BudgetCap",
    "BudgetUse",
    "ReplayArmRunner",
    "get_arm_runner",
    "register_arm_runner",
    "resolve_arm_runner",
]

ARM_RUNNER_ABSENT = "ARM_RUNNER_ABSENT"


class ArmRunnerAbsent(Exception):  # noqa: N818 -- plan-mandated name, lane 5's seam (#3218)
    """No arm runner could be found. ``code`` is the refusal, ``detail`` the cause."""

    def __init__(self, code: str = ARM_RUNNER_ABSENT, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class ArmResult:
    """What one arm produced.

    ``gains`` maps opportunity id to validated gain. ``None`` (or a missing
    id) means the arm rejected the opportunity or reached no conclusion;
    the comparison scores that as 0 through :meth:`scored_gain`.
    ``budget_use`` is the arm's own report; dollars are re-read from records
    by ``budget.accounted_use`` and only turns and wall seconds are taken
    from here.
    """

    gains: dict[str, float | None]
    budget_use: BudgetUse

    def scored_gain(self, opportunity_id: str) -> float:
        """The gain as the comparison scores it: rejected or inconclusive is 0."""
        value = self.gains.get(opportunity_id)
        return 0.0 if value is None else float(value)


class ArmRunner(Protocol):
    """Run one research process on a set of opportunities under a cap."""

    def run(
        self,
        process_digest: str,
        opportunity_ids: list[str],
        budget_cap: BudgetCap,
        arm_run_id: str,
    ) -> ArmResult: ...


_registered: ArmRunner | None = None


def register_arm_runner(runner: ArmRunner | None) -> None:
    """Register the in-process runner; ``None`` clears the registry."""
    global _registered
    _registered = runner


def get_arm_runner() -> ArmRunner:
    """The registered runner, or :class:`ArmRunnerAbsent`."""
    if _registered is None:
        raise ArmRunnerAbsent(
            ARM_RUNNER_ABSENT,
            "no arm runner registered in this process; pass --arm-runner module:attr "
            "or call register_arm_runner(...)",
        )
    return _registered


def resolve_arm_runner(spec: str) -> ArmRunner:
    """Instantiate a runner named ``module:attr`` by lazy import.

    ``ImportError``, ``AttributeError``, and a malformed spec each become
    :class:`ArmRunnerAbsent` with the underlying error text in ``detail``.
    The registry is untouched.
    """
    module_name, sep, attr = spec.partition(":")
    if not sep or not module_name or not attr:
        raise ArmRunnerAbsent(
            ARM_RUNNER_ABSENT, f"malformed arm runner spec {spec!r}; expected module:attr"
        )
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, attr)
    except (ImportError, AttributeError, ValueError) as exc:
        raise ArmRunnerAbsent(ARM_RUNNER_ABSENT, str(exc)) from exc
    return factory()


@dataclass(frozen=True)
class ReplayArm:
    """One arm's fixture: gains per opportunity and the budget it reports.

    ``budget_use.unit3_usd``, when known, is admitted through the ledger
    and settled at exactly that figure under ``arm:<arm_run_id>:<resource>``.
    """

    gains: dict[str, float | None] = field(default_factory=dict)
    budget_use: BudgetUse = field(default_factory=BudgetUse)
    resource: str = "replay"


class ReplayArmRunner:
    """An :class:`ArmRunner` that replays fixtures, keyed by process digest.

    Constructible with no arguments so ``resolve_arm_runner`` can name it;
    a run against a digest with no fixture raises ``LookupError``. Unit-3
    spend goes through ``admit()`` and ``settle()`` against the ledger for
    ``project_key`` so the comparison's ``LedgerBudgetReader`` reads the
    fixture back the way it reads a production arm. A refused admission
    raises ``RuntimeError`` naming the refusal.
    """

    def __init__(
        self,
        arms: Mapping[str, ReplayArm] | None = None,
        *,
        project_key: str = "valor",
        settings=None,
        now: datetime | None = None,
    ) -> None:
        self._arms: dict[str, ReplayArm] = dict(arms or {})
        self.project_key = project_key
        self.settings = settings
        self.now = now

    def run(
        self,
        process_digest: str,
        opportunity_ids: list[str],
        budget_cap: BudgetCap,
        arm_run_id: str,
    ) -> ArmResult:
        arm = self._arms.get(process_digest)
        if arm is None:
            raise LookupError(f"no replay fixture for process digest {process_digest}")
        spend = arm.budget_use.unit3_usd
        if spend is not None:
            self._admit_spend(arm_run_id, arm.resource, float(spend))
        gains = {oid: arm.gains.get(oid) for oid in opportunity_ids}
        return ArmResult(gains=gains, budget_use=arm.budget_use)

    def _admit_spend(self, arm_run_id: str, resource: str, spend: float) -> None:
        from tools.improvement_recursion.budget import arm_resource_prefix
        from tools.infrastructure_budget import ResourceDecl, admit, settle

        decision = admit(
            ResourceDecl(name=arm_resource_prefix(arm_run_id) + resource, weekly_rate_usd=spend),
            project_key=self.project_key,
            settings=self.settings,
            now=self.now,
        )
        if not decision.admitted:
            raise RuntimeError(
                f"replay arm {arm_run_id!r} refused admission for {resource!r}: {decision.reason}"
            )
        settle(decision.reservation_id, lambda: spend, project_key=self.project_key)
