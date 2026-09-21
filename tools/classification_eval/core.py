"""Types, the comparison, the acceptance bar, and the contention check (#3410).

See the package docstring for the shape of the work. Everything here is
pure orchestration over injectable :class:`Arm` callables and plain data;
nothing touches Redis or a network client.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel

from agent.llm.tasks import ErrorCost, LLMTask
from tools.improvement_eval.statistics import clustered_bootstrap_ci

logger = logging.getLogger(__name__)

CASE_ID = "1ec40086ca1d422e90ef747775ff7f64"
"""The improvement case every comparison record and its claims attach to."""

PROJECT_KEY = "valor"
EVIDENCE_KIND = "classifier_comparison"
ISSUE_URL = "https://github.com/tomcounsell/ai/issues/3410"

TIER_BAR: dict[str, float] = {
    ErrorCost.HIGH.value: 0.95,
    ErrorCost.MEDIUM.value: 0.90,
    ErrorCost.LOW.value: 0.85,
}
MAX_ERROR_RATE = 0.02
REFERENCE_LATENCY_SLACK_S = 1.0
"""Without a site budget, the candidate's p95 may exceed the reference's by this much."""

CONTENDED_LABELS = ("com.valor.bridge", "com.valor.worker", "com.valor.reflection-worker")
"""launchd labels whose processes hold Ollama clients (Race 3)."""

LATENCY_CONCURRENCY = 4
InputSource = Literal["real", "fixture"]


# --- inputs, arms, sites ------------------------------------------------------------


@dataclass(frozen=True)
class Input:
    """One comparison input: the message text, where it came from, and any
    site-specific context the prompt builder needs (session summary, job
    candidates). Real inbound ``valor`` messages carry ``source="real"``."""

    text: str
    source: InputSource
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Price:
    """A backend's list price with the date it was read, for the record."""

    model: str
    usd_per_mtoken_in: float
    usd_per_mtoken_out: float
    retrieved_at: str
    note: str = ""


ArmCall = Callable[[str, str | None, type[BaseModel]], Awaitable[tuple[Any, ...]]]
"""``(prompt, system, output_type) -> (validated output, cost in USD or None)``.

``None`` cost means the transport reported no usage; the runner estimates it
from the prompt and output lengths at the arm's :class:`Price` and stamps the
record ``cost_metering="estimated"``. An arm that paces itself against a
rate limit (the gemma reference arm) may return a third element, the seconds
it spent waiting on its own limiter, which the runner leaves out of the
call's latency. Any exception is one error for the arm."""


@dataclass(frozen=True)
class Arm:
    """One backend under measurement. ``name`` keys the record's arm block."""

    name: str
    backend: str
    model: str
    price: Price
    call: ArmCall


@dataclass(frozen=True)
class Site:
    """One row of the site table (``tools/classification_eval/sites.py``).

    ``prompt`` builds the reference prompt verbatim from an :class:`Input`
    and ``system`` is the reference ``system`` string when the production
    call carries one (C9, C10); the candidate side defaults to the same
    prompt, system, and output type, and the landing builder overrides
    ``candidate_prompt``, ``candidate_system``, or ``candidate_output_type``
    while iterating. ``label`` reduces an output to the one string agreement
    compares. ``reference`` names the live reference arm: ``"anthropic"``
    (Haiku through the Anthropic leg), ``"openrouter_gemma"`` (C15's
    ``main`` backend), or ``"ollama"`` (granite, the landed backend of C12,
    C13, and C14; a decisions comparison there measures granite once, as the
    reference, and judges it as the fallback through
    :func:`evaluate_reference`). ``budget_s`` is the site's own p95 budget when it has
    one (the 3 s sites), else ``None`` for the reference-relative rule.
    ``real_inputs`` is the row's own real-input loader for a site whose
    production input is not an inbound message (C11 reads activity windows
    from real transcripts, C14 reads real memory rows); ``None`` draws real
    inbound ``valor`` messages through the default loader.
    """

    id: str
    task: LLMTask
    prompt: Callable[[Input], str]
    output_type: type[BaseModel]
    label: Callable[[BaseModel], str]
    reference: Literal["anthropic", "openrouter_gemma", "ollama"]
    minimum_n: int
    budget_s: float | None
    fixtures: Callable[[], list[Input]]
    system: str | None = None
    candidate_prompt: Callable[[Input], str] | None = None
    candidate_system: str | None = None
    candidate_output_type: type[BaseModel] | None = None
    real_inputs: Callable[[int], list[Input]] | None = None
    model: str | None = None

    @property
    def tier(self) -> str:
        return self.task.error_cost.value


class ShortfallError(Exception):
    """Fewer inputs than the site minimum; no record is written."""


# --- results ------------------------------------------------------------------------


@dataclass
class ArmResult:
    """One arm's measurements. Latencies are seconds per call, by pass."""

    name: str
    backend: str
    model: str
    calls: int = 0
    errors: int = 0
    latency_c1: list[float] = field(default_factory=list)
    latency_c4: list[float] = field(default_factory=list)
    cost_usd_total: float = 0.0
    cost_metering: str = "exact"
    price: dict[str, Any] = field(default_factory=dict)
    labels: list[str | None] = field(default_factory=list)
    """The agreement-pass label per input, in input order (``None`` on an
    error); kept in the record so a disagreement can be read back per input."""
    agreement: dict[str, float | int] | None = None

    @property
    def error_rate(self) -> float:
        return self.errors / self.calls if self.calls else 0.0

    @property
    def p50_c1(self) -> float:
        return percentile(self.latency_c1, 0.50)

    @property
    def p95_c1(self) -> float:
        return percentile(self.latency_c1, 0.95)

    @property
    def p50_c4(self) -> float:
        return percentile(self.latency_c4, 0.50)

    @property
    def p95_c4(self) -> float:
        return percentile(self.latency_c4, 0.95)

    @property
    def cost_per_call_usd(self) -> float:
        succeeded = self.calls - self.errors
        return self.cost_usd_total / succeeded if succeeded else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "backend": self.backend,
            "model": self.model,
            "calls": self.calls,
            "errors": self.errors,
            "error_rate": self.error_rate,
            "p50_c1": self.p50_c1,
            "p95_c1": self.p95_c1,
            "p50_c4": self.p50_c4,
            "p95_c4": self.p95_c4,
            "cost_per_call_usd": self.cost_per_call_usd,
            "cost_metering": self.cost_metering,
            "price": self.price,
            "agreement": self.agreement,
            "labels": self.labels,
        }


@dataclass
class ComparisonRecord:
    """One run's record; ``as_dict()`` is what lands in ``ImprovementEvidence.detail``."""

    site: str
    tier: str
    minimum_n: int
    budget_s: float | None
    n: int
    n_real: int
    n_fixture: int
    contended: bool
    latency_only: bool
    reference: ArmResult | None
    candidates: dict[str, ArmResult]
    run_id: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "site": self.site,
            "tier": self.tier,
            "minimum_n": self.minimum_n,
            "budget_s": self.budget_s,
            "n": self.n,
            "n_real": self.n_real,
            "n_fixture": self.n_fixture,
            "contended": self.contended,
            "latency_only": self.latency_only,
            "reference": self.reference.as_dict() if self.reference else None,
            "candidates": {name: arm.as_dict() for name, arm in self.candidates.items()},
            "run_id": self.run_id,
            "created_at": self.created_at,
        }


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile; ``0.0`` on no samples."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


# --- the comparison -------------------------------------------------------------------


def _estimate_cost(price: Price, prompt: str, output: BaseModel) -> float:
    """Chars/4 token estimate at the arm's list price, for transports that
    report no usage (the PydanticAI legs return only the validated output)."""
    tokens_in = len(prompt) / 4
    tokens_out = len(output.model_dump_json()) / 4
    return (tokens_in / 1e6) * price.usd_per_mtoken_in + (
        tokens_out / 1e6
    ) * price.usd_per_mtoken_out


async def _one_call(
    arm: Arm, result: ArmResult, prompt: str, system: str | None, output_type: type[BaseModel]
) -> tuple[str | None, float]:
    """Run one call, account it on ``result``, return ``(output, latency)``."""
    started = perf_counter()
    try:
        output, cost, *rest = await arm.call(prompt, system, output_type)
    except Exception as e:
        elapsed = perf_counter() - started
        result.calls += 1
        result.errors += 1
        logger.warning("classification_eval: arm %s errored: %s", arm.name, e)
        return None, elapsed
    waited = float(rest[0]) if rest else 0.0
    elapsed = max(0.0, perf_counter() - started - waited)
    result.calls += 1
    if cost is None:
        cost = _estimate_cost(arm.price, prompt, output)
        result.cost_metering = "estimated"
    result.cost_usd_total += cost
    return output, elapsed


async def _run_arm(
    arm: Arm,
    inputs: Sequence[Input],
    prompt_of: Callable[[Input], str],
    system: str | None,
    output_type: type[BaseModel],
    label: Callable[[BaseModel], str],
) -> ArmResult:
    """The agreement pass at concurrency 1, then the latency pass at
    :data:`LATENCY_CONCURRENCY`; every input crosses the arm twice."""
    result = ArmResult(name=arm.name, backend=arm.backend, model=arm.model, price=asdict(arm.price))
    if arm.backend == "ollama":
        result.cost_metering = "local"
    for inp in inputs:
        output, elapsed = await _one_call(arm, result, prompt_of(inp), system, output_type)
        result.latency_c1.append(elapsed)
        result.labels.append(label(output) if output is not None else None)

    gate = asyncio.Semaphore(LATENCY_CONCURRENCY)

    async def timed(inp: Input) -> float:
        async with gate:
            _, elapsed = await _one_call(arm, result, prompt_of(inp), system, output_type)
            return elapsed

    result.latency_c4 = list(await asyncio.gather(*(timed(inp) for inp in inputs)))
    return result


def _agreement(reference: ArmResult, candidate: ArmResult) -> dict[str, float | int]:
    """Agreement rate with a bootstrap interval over the per-input agreement
    indicators. ``clustered_bootstrap_ci`` clusters by project; every input
    here belongs to the one project, so it delegates to the plain percentile
    bootstrap. Inputs where either arm errored are outside the comparison."""
    indicators = [
        1.0 if ref_label == cand_label else 0.0
        for ref_label, cand_label in zip(reference.labels, candidate.labels, strict=True)
        if ref_label is not None and cand_label is not None
    ]
    ci = clustered_bootstrap_ci({PROJECT_KEY: indicators} if indicators else {})
    return {"mean": ci.mean, "lower": ci.lower, "upper": ci.upper, "n": ci.n}


def require_minimum(site: Site, inputs: Sequence[Input]) -> int:
    """Raise :class:`ShortfallError` under the site minimum; return the real count."""
    n_real = sum(1 for inp in inputs if inp.source == "real")
    n = len(inputs)
    if n < site.minimum_n:
        raise ShortfallError(
            f"site {site.id}: {n} inputs ({n_real} real), minimum {site.minimum_n}; "
            f"short by {site.minimum_n - n}. No record written."
        )
    return n_real


async def compare(
    site: Site,
    inputs: Sequence[Input],
    *,
    reference: Arm | None,
    candidates: Sequence[Arm],
    contended: bool,
) -> ComparisonRecord:
    """Measure every arm over ``inputs`` and score the candidates.

    ``reference=None`` is the latency-only mode (C12, C13, C14 stay on
    granite; the record carries their latency and error rate and no
    agreement). Raises :class:`ShortfallError` under the site minimum before
    any arm runs, so no reference spend precedes the refusal.
    """
    n_real = require_minimum(site, inputs)
    n = len(inputs)
    if not candidates:
        raise ValueError("compare needs at least one candidate arm")

    ref_result = None
    if reference is not None:
        ref_result = await _run_arm(
            reference, inputs, site.prompt, site.system, site.output_type, site.label
        )

    cand_prompt = site.candidate_prompt or site.prompt
    cand_system = site.system if site.candidate_system is None else site.candidate_system
    cand_type = site.candidate_output_type or site.output_type
    cand_results: dict[str, ArmResult] = {}
    for arm in candidates:
        result = await _run_arm(arm, inputs, cand_prompt, cand_system, cand_type, site.label)
        if ref_result is not None:
            result.agreement = _agreement(ref_result, result)
        cand_results[arm.name] = result

    return ComparisonRecord(
        site=site.id,
        tier=site.tier,
        minimum_n=site.minimum_n,
        budget_s=site.budget_s,
        n=n,
        n_real=n_real,
        n_fixture=n - n_real,
        contended=contended,
        latency_only=reference is None,
        reference=ref_result,
        candidates=cand_results,
        run_id=uuid.uuid4().hex,
        created_at=datetime.now(UTC).isoformat(),
    )


# --- the bar ----------------------------------------------------------------------


def latency_budget_s(record: Mapping[str, Any]) -> float | None:
    """The site's own budget, else the reference p95 at 4 plus the slack;
    ``None`` when a latency-only record has no budget of its own."""
    if record.get("budget_s") is not None:
        return float(record["budget_s"])
    reference = record.get("reference")
    if reference is None:
        return None
    return float(reference["p95_c4"]) + REFERENCE_LATENCY_SLACK_S


COST_RATIO = 0.1
"""Against a paid (Anthropic) reference, a candidate may cost at most this
share of the reference's per-call cost (the seventh criterion, #3421)."""


def reference_cost_bound(record: Mapping[str, Any]) -> float | None:
    """The per-call cost a candidate must stay at or under, else ``None``.

    Only a reference arm on ``anthropic`` sets one (the local and free arms
    carry no price worth comparing against); a reference whose
    ``cost_per_call_usd`` is ``0.0`` or absent sets none either, so the
    criterion is skipped rather than divided by zero.
    """
    reference = record.get("reference")
    if not reference or reference.get("backend") != "anthropic":
        return None
    reference_cost = float(reference.get("cost_per_call_usd") or 0.0)
    if reference_cost <= 0.0:
        return None
    return reference_cost * COST_RATIO


def evaluate_bar(record: Mapping[str, Any], arm_name: str) -> list[str]:
    """The failing criteria for ``arm_name`` in ``record``; empty means it clears the bar.

    A latency-only record (a site that stays on granite by the plan, C12 to
    C14) measures no agreement, so the two agreement safeguards, the tier bar
    and the real-message share (Risk 7), are not among its criteria; the
    input minimum, the error rate, contention, and any site budget still are.
    The ``cost`` criterion (a candidate at or under one tenth of the
    reference's per-call cost) applies only against an Anthropic reference
    that recorded a cost (:func:`reference_cost_bound`).
    """
    arm = record["candidates"][arm_name]
    latency_only = bool(record.get("latency_only"))
    failed: list[str] = []
    if not latency_only:
        agreement = arm.get("agreement") or {}
        if float(agreement.get("mean", 0.0)) < TIER_BAR[record["tier"]]:
            failed.append("agreement")
    budget = latency_budget_s(record)
    if budget is not None and float(arm["p95_c4"]) > budget:
        failed.append("p95_c4")
    if record.get("contended"):
        failed.append("contended")
    if float(arm["error_rate"]) > MAX_ERROR_RATE:
        failed.append("error_rate")
    if int(record["n"]) < int(record["minimum_n"]):
        failed.append("n")
    if not latency_only and int(record["n_real"]) < int(record["minimum_n"]) / 2:
        failed.append("n_real")
    cost_bound = reference_cost_bound(record)
    if cost_bound is not None and float(arm.get("cost_per_call_usd") or 0.0) > cost_bound:
        failed.append("cost")
    return failed


def evaluate_reference(record: Mapping[str, Any]) -> list[str]:
    """The failing latency-only criteria for the record's reference arm.

    A reference arm measures no agreement against itself, so its criteria
    are the ones a latency-only candidate faces: ``p95_c4`` against the
    site's own budget when it has one (there is no reference to be relative
    to), ``contended``, ``error_rate``, and ``n``. This is how a granite
    reference (C12 to C14) is judged as the fallback of a decisions
    comparison on the record it was measured in (#3421). Raises
    ``ValueError`` on a record with no reference arm.
    """
    reference = record.get("reference")
    if not reference:
        raise ValueError("evaluate_reference needs a record with a reference arm")
    failed: list[str] = []
    budget = record.get("budget_s")
    if budget is not None and float(reference["p95_c4"]) > float(budget):
        failed.append("p95_c4")
    if record.get("contended"):
        failed.append("contended")
    if float(reference["error_rate"]) > MAX_ERROR_RATE:
        failed.append("error_rate")
    if int(record["n"]) < int(record["minimum_n"]):
        failed.append("n")
    return failed


def _fmt_arm(record: Mapping[str, Any], arm: Mapping[str, Any]) -> list[str]:
    lines = [
        f"    p50/p95 @1: {arm['p50_c1']:.3f}s / {arm['p95_c1']:.3f}s"
        f"    p50/p95 @4: {arm['p50_c4']:.3f}s / {arm['p95_c4']:.3f}s",
        f"    error_rate={arm['error_rate']:.3f} ({arm['errors']}/{arm['calls']})"
        f"    cost/call=${arm['cost_per_call_usd']:.6f} ({arm['cost_metering']})",
    ]
    price = arm.get("price") or {}
    if price:
        lines.append(
            f"    price: {price.get('model')} in ${price.get('usd_per_mtoken_in')}/M"
            f" out ${price.get('usd_per_mtoken_out')}/M (retrieved {price.get('retrieved_at')})"
        )
    agreement = arm.get("agreement")
    if agreement:
        lines.append(
            f"    agreement={agreement['mean']:.3f} [{agreement['lower']:.3f},"
            f" {agreement['upper']:.3f}] over {agreement['n']} pairs"
        )
    return lines


def render_report(record: Mapping[str, Any]) -> str:
    """The human report: every arm, then PASS or MISS per candidate with the
    failing criterion named against its bar."""
    budget = latency_budget_s(record)
    cost_bound = reference_cost_bound(record)
    reference_cost = float((record.get("reference") or {}).get("cost_per_call_usd") or 0.0)
    header = (
        f"site={record['site']} tier={record['tier']} n={record['n']}"
        f" n_real={record['n_real']} n_fixture={record['n_fixture']}"
        f" minimum={record['minimum_n']} contended={str(record['contended']).lower()}"
        f" latency_only={str(record['latency_only']).lower()}"
        f" run={record['run_id']}"
    )
    lines = [header]
    reference = record.get("reference")
    if reference:
        lines.append(f"  reference {reference['name']} ({reference['model']}):")
        lines.extend(_fmt_arm(record, reference))
    for name, arm in record["candidates"].items():
        lines.append(f"  candidate {name} ({arm['model']}):")
        lines.extend(_fmt_arm(record, arm))
        failed = evaluate_bar(record, name)
        if not failed:
            lines.append(f"  {name}: PASS")
            continue
        lines.append(f"  {name}: MISS on {', '.join(failed)}")
        thresholds = {
            "agreement": f"agreement {float((arm.get('agreement') or {}).get('mean', 0.0)):.3f}"
            f" < bar {TIER_BAR[record['tier']]:.3f} ({record['tier']})",
            "p95_c4": f"p95@4 {float(arm['p95_c4']):.3f}s > budget"
            f" {budget if budget is None else f'{budget:.3f}s'}",
            "contended": "record measured with com.valor services loaded (Race 3)",
            "error_rate": f"error_rate {float(arm['error_rate']):.3f} > {MAX_ERROR_RATE:.3f}",
            "n": f"n {record['n']} < minimum {record['minimum_n']}",
            "n_real": f"n_real {record['n_real']} < half the minimum"
            f" ({int(record['minimum_n']) / 2:g})",
            "cost": f"cost/call ${float(arm.get('cost_per_call_usd') or 0.0):.6f}"
            f" > one tenth of reference ${reference_cost:.6f}"
            f" ({cost_bound if cost_bound is None else f'${cost_bound:.6f}'})",
        }
        lines.extend(f"    {criterion}: {thresholds[criterion]}" for criterion in failed)
    return "\n".join(lines)


# --- contention (Race 3) -------------------------------------------------------------


def is_contended(launchctl_output: str | None = None) -> bool:
    """True when a bridge, worker, or reflection-worker label is loaded.

    Reads ``launchctl list`` unless ``launchctl_output`` is given (the test
    seam). A loaded label counts even with no PID, because ``KeepAlive``
    relaunches it; ``./scripts/valor-service.sh stop`` unloads, which is
    what clears the flag.
    """
    if launchctl_output is None:
        try:
            launchctl_output = subprocess.run(
                ["launchctl", "list"], capture_output=True, text=True, timeout=10, check=False
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            return False
    labels = {line.split("\t")[-1].strip() for line in launchctl_output.splitlines()}
    return any(label in labels for label in CONTENDED_LABELS)
