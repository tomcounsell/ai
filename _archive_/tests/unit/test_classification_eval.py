"""The comparison runner (#3410, lane A Task 3): ``tools/classification_eval``.

Everything here runs without a network: arms are injectable callables, the
``launchctl`` output is a string, and the record write goes through the
Popoto ORM against the claimed test db (autouse ``redis_test_db``).
"""

from __future__ import annotations

import json
from typing import Literal

import pytest
from pydantic import BaseModel

from agent.llm.tasks import Backend, ErrorCost, LLMTask, TaskKind
from models.improvement_evidence import EVIDENCE_KINDS, ImprovementEvidence
from tools.classification_eval import (
    EVIDENCE_KIND,
    Arm,
    Input,
    Price,
    ShortfallError,
    Site,
    audit,
    compare,
    declared_classification_tasks,
    evaluate_bar,
    is_contended,
    latest_record,
    render_report,
    write_record,
)
from tools.classification_eval.__main__ import main, parse_candidates

PK = "test-3410-eval"


class Verdict(BaseModel):
    answer: Literal["yes", "no"]


def _task(site: str, backend: Backend, cost: ErrorCost = ErrorCost.HIGH, **kw) -> LLMTask:
    return LLMTask(site=site, kind=TaskKind.CLASSIFICATION, backend=backend, error_cost=cost, **kw)


def _site(site_id: str = "test.site", *, minimum_n: int = 50, budget_s: float | None = None):
    return Site(
        id=site_id,
        task=_task(site_id, Backend.ANTHROPIC),
        prompt=lambda inp: f"Q: {inp.text}",
        output_type=Verdict,
        label=lambda out: out.answer,
        reference="anthropic",
        minimum_n=minimum_n,
        budget_s=budget_s,
        fixtures=lambda: [Input("fixture?", "fixture")],
    )


def _inputs(n_real: int, n_fixture: int) -> list[Input]:
    real = [Input(f"real message {i}{'?' if i % 2 else ''}", "real") for i in range(n_real)]
    fixture = [Input(f"fixture {i}{'?' if i % 3 else ''}", "fixture") for i in range(n_fixture)]
    return real + fixture


PRICE = Price(
    model="fake", usd_per_mtoken_in=1.0, usd_per_mtoken_out=5.0, retrieved_at="2026-01-01"
)


def _truth(prompt: str) -> Verdict:
    return Verdict(answer="yes" if "?" in prompt else "no")


def _arm(
    name: str,
    backend: str,
    *,
    flip_every: int = 0,
    error_every: int = 0,
    cost: float = 0.001,
) -> Arm:
    """A fake arm: answers the truth, disagreeing every ``flip_every`` calls
    and raising every ``error_every`` calls (counted over all calls), at
    ``cost`` USD per call."""
    seen = {"calls": 0}

    async def call(prompt: str, system: str | None, output_type: type[BaseModel]):
        seen["calls"] += 1
        n = seen["calls"]
        if error_every and n % error_every == 0:
            raise RuntimeError("arm exploded")
        truth = _truth(prompt)
        if flip_every and n % flip_every == 0:
            truth = Verdict(answer="no" if truth.answer == "yes" else "yes")
        return truth, cost

    return Arm(name=name, backend=backend, model=f"{backend}-model", price=PRICE, call=call)


# --- compare() ---------------------------------------------------------------


async def test_compare_math_on_fake_arms():
    inputs = _inputs(40, 20)
    reference = _arm("reference", "anthropic")
    candidate = _arm("ollama", "ollama", flip_every=10, error_every=17)
    record = await compare(
        _site(), inputs, reference=reference, candidates=[candidate], contended=False
    )

    assert (record.n, record.n_real, record.n_fixture) == (60, 40, 20)
    assert record.site == "test.site" and record.tier == "high" and record.minimum_n == 50
    assert record.contended is False and record.latency_only is False
    assert record.reference.backend == "anthropic"
    assert record.reference.error_rate == 0.0
    assert record.reference.calls == 120  # concurrency 1 and 4 passes over every input
    assert len(record.reference.latency_c1) == 60 and len(record.reference.latency_c4) == 60
    assert record.reference.cost_per_call_usd == pytest.approx(0.001)
    assert record.reference.price["retrieved_at"] == "2026-01-01"

    arm = record.candidates["ollama"]
    # Agreement pass is the first 60 calls: errors on call 17, 34, 51 (3 inputs
    # drop out), flips on 10, 20, 30, 40, 50, 60 (6 disagreements over 57 pairs).
    assert arm.agreement is not None
    assert arm.agreement["n"] == 57
    assert arm.agreement["mean"] == pytest.approx(51 / 57)
    assert 0.0 < arm.agreement["lower"] <= arm.agreement["mean"] <= arm.agreement["upper"] <= 1.0
    assert arm.errors == 120 // 17 and arm.calls == 120
    assert arm.error_rate == pytest.approx(7 / 120)
    assert arm.p95_c4 >= arm.p50_c4 >= 0.0
    assert record.as_dict()["candidates"]["ollama"]["agreement"]["mean"] == arm.agreement["mean"]
    assert len(record.as_dict()["candidates"]["ollama"]["labels"]) == 60
    assert record.as_dict()["candidates"]["ollama"]["labels"][16] is None  # call 17 errored


async def test_compare_refuses_fewer_inputs_than_the_site_minimum():
    inputs = _inputs(6, 4)
    with pytest.raises(ShortfallError) as excinfo:
        await compare(
            _site(minimum_n=50),
            inputs,
            reference=_arm("reference", "anthropic"),
            candidates=[_arm("ollama", "ollama")],
            contended=False,
        )
    message = str(excinfo.value)
    assert "test.site" in message and "10" in message and "50" in message and "40" in message


async def test_latency_only_record_carries_no_agreement():
    record = await compare(
        _site(),
        _inputs(30, 25),
        reference=None,
        candidates=[_arm("ollama", "ollama")],
        contended=True,
    )
    assert record.latency_only is True and record.reference is None
    assert record.candidates["ollama"].agreement is None
    assert record.contended is True


async def test_reference_system_reaches_the_reference_and_the_candidate_inherits_it():
    """A site whose production call carries a ``system`` (C9, C10) runs the
    reference arm with it verbatim; the candidate gets the same one unless the
    row sets ``candidate_system``."""
    from dataclasses import replace

    seen: dict[str, list[str | None]] = {"reference": [], "ollama": []}

    def arm(name: str) -> Arm:
        async def call(prompt: str, system: str | None, output_type: type[BaseModel]):
            seen[name].append(system)
            return Verdict(answer="yes"), 0.0

        return Arm(name=name, backend=name, model="m", price=PRICE, call=call)

    site = replace(_site(minimum_n=2), system="be strict")
    await compare(
        site, _inputs(2, 0), reference=arm("reference"), candidates=[arm("ollama")], contended=False
    )
    assert set(seen["reference"]) == {"be strict"} and set(seen["ollama"]) == {"be strict"}

    seen["reference"].clear()
    seen["ollama"].clear()
    tuned = replace(site, candidate_system="be terse")
    await compare(
        tuned,
        _inputs(2, 0),
        reference=arm("reference"),
        candidates=[arm("ollama")],
        contended=False,
    )
    assert set(seen["reference"]) == {"be strict"} and set(seen["ollama"]) == {"be terse"}


def test_site_inputs_use_the_row_loader_when_it_has_one():
    """A site whose production input is not an inbound message (C11's activity
    window, C14's memory row) names its own real-input loader; every other row
    draws real inbound messages through the default loader."""
    from dataclasses import replace

    from tools.classification_eval.arms import site_inputs

    default_calls: list[int] = []

    def default(limit: int, *, project_key: str) -> list[Input]:
        default_calls.append(limit)
        return [Input("real inbound", "real")]

    plain = _site()
    assert site_inputs(plain, 7, project_key="valor", default=default) == [
        Input("fixture?", "fixture"),
        Input("real inbound", "real"),
    ]
    assert default_calls == [7]

    own = replace(plain, real_inputs=lambda limit: [Input(f"row {limit}", "real")])
    assert site_inputs(own, 3, project_key="valor", default=default) == [
        Input("fixture?", "fixture"),
        Input("row 3", "real"),
    ]
    assert default_calls == [7]


# --- the bar and the miss report -----------------------------------------------


def _record_dict(
    *,
    tier: str = "high",
    agreement: float = 0.97,
    p95_c4: float = 1.2,
    reference_p95_c4: float = 1.0,
    contended: bool = False,
    error_rate: float = 0.0,
    n: int = 200,
    n_real: int = 120,
    minimum_n: int = 200,
    budget_s: float | None = None,
    latency_only: bool = False,
    candidates: tuple[str, ...] = ("ollama",),
    site: str = "test.site",
    cost: float = 0.0,
    reference_cost: float = 0.0,
    reference_backend: str = "anthropic",
    reference_name: str | None = None,
    reference_error_rate: float = 0.0,
    run_id: str = "run-1",
    created_at: str = "2026-09-19T00:00:00+00:00",
) -> dict:
    """A record dict in the shape ``ComparisonRecord.as_dict()`` writes.

    Every candidate arm is named after its backend and carries ``cost``;
    the reference arm is on ``reference_backend`` (named after it unless
    ``reference_name`` says otherwise) and carries ``reference_cost``.
    """

    def arm(name, backend=None):
        return {
            "name": name,
            "backend": backend or name,
            "model": f"{name}-model",
            "calls": n * 2,
            "errors": int(error_rate * n * 2),
            "error_rate": error_rate,
            "p50_c1": 0.5,
            "p95_c1": 0.9,
            "p50_c4": 0.7,
            "p95_c4": p95_c4,
            "cost_per_call_usd": cost,
            "cost_metering": "local",
            "price": {},
            "agreement": None
            if latency_only
            else {"mean": agreement, "lower": agreement - 0.03, "upper": 1.0, "n": n},
        }

    reference = (
        None
        if latency_only
        else dict(
            arm(reference_name or reference_backend, reference_backend),
            p95_c4=reference_p95_c4,
            cost_per_call_usd=reference_cost,
            error_rate=reference_error_rate,
            errors=int(reference_error_rate * n * 2),
        )
    )
    return {
        "site": site,
        "tier": tier,
        "minimum_n": minimum_n,
        "budget_s": budget_s,
        "n": n,
        "n_real": n_real,
        "n_fixture": n - n_real,
        "contended": contended,
        "latency_only": latency_only,
        "reference": reference,
        "candidates": {name: arm(name) for name in candidates},
        "run_id": run_id,
        "created_at": created_at,
    }


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, []),
        ({"agreement": 0.93}, ["agreement"]),
        ({"tier": "medium", "agreement": 0.93}, []),
        ({"tier": "low", "agreement": 0.84}, ["agreement"]),
        ({"p95_c4": 2.5, "reference_p95_c4": 1.0}, ["p95_c4"]),
        ({"p95_c4": 3.1, "budget_s": 3.0}, ["p95_c4"]),
        ({"p95_c4": 2.9, "budget_s": 3.0, "reference_p95_c4": 0.5}, []),
        ({"contended": True}, ["contended"]),
        ({"error_rate": 0.03}, ["error_rate"]),
        ({"n": 150, "n_real": 100}, ["n"]),
        ({"n_real": 99}, ["n_real"]),
        ({"latency_only": True, "p95_c4": 9.0}, []),
        ({"latency_only": True, "p95_c4": 3.5, "budget_s": 3.0}, ["p95_c4"]),
        # A latency-only record measures no agreement, so the real-message
        # share (Risk 7, an agreement safeguard) is not one of its criteria;
        # the total input minimum and the error rate still are.
        ({"latency_only": True, "n_real": 5}, []),
        ({"latency_only": True, "n_real": 5, "n": 150}, ["n"]),
        ({"agreement": 0.5, "contended": True, "n_real": 1}, ["agreement", "contended", "n_real"]),
    ],
)
def test_evaluate_bar_names_every_failing_criterion(kwargs, expected):
    assert evaluate_bar(_record_dict(**kwargs), "ollama") == expected


def test_miss_report_names_the_failing_criterion():
    report = render_report(_record_dict(agreement=0.80, error_rate=0.05))
    assert "MISS" in report
    assert "agreement" in report and "0.950" in report
    assert "error_rate" in report and "0.020" in report
    assert "test.site" in report and "n_real=120" in report

    passing = render_report(_record_dict())
    assert "PASS" in passing and "MISS" not in passing


# --- the cost criterion (#3421) ----------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        # Haiku reference: the candidate pays at most one tenth of the reference.
        ({"reference_cost": 0.001, "cost": 0.0001}, []),
        ({"reference_cost": 0.001, "cost": 0.00011}, ["cost"]),
        ({"reference_cost": 0.001, "cost": 0.0}, []),
        # A reference on granite or gemma carries no price to compare against.
        ({"reference_backend": "ollama", "reference_cost": 0.001, "cost": 0.5}, []),
        ({"reference_backend": "openrouter", "reference_cost": 0.001, "cost": 0.5}, []),
        # A Haiku reference whose cost is 0.0 skips the criterion: never a division.
        ({"reference_cost": 0.0, "cost": 0.5}, []),
    ],
)
def test_evaluate_bar_cost_criterion_applies_only_against_an_anthropic_reference(kwargs, expected):
    assert evaluate_bar(_record_dict(candidates=("decisions",), **kwargs), "decisions") == expected


def test_evaluate_bar_cost_skips_a_candidate_on_the_references_own_backend():
    """The ``anthropic`` candidate a fit run measures as the encoder landing's
    restructured-shape fallback (#3420) is Haiku against Haiku: priced like
    the reference by construction, judged for agreement, never for cost. The
    decisions candidate on the same record still faces the bound."""
    record = _record_dict(candidates=("anthropic", "decisions"), reference_cost=0.001, cost=0.001)
    assert evaluate_bar(record, "anthropic") == []
    assert evaluate_bar(record, "decisions") == ["cost"]
    assert "anthropic: PASS" in render_report(record)
    assert "decisions: MISS on cost" in render_report(record)


def test_evaluate_bar_cost_skips_a_reference_without_a_cost_field():
    record = _record_dict(candidates=("decisions",), cost=0.5)
    del record["reference"]["cost_per_call_usd"]
    assert evaluate_bar(record, "decisions") == []


async def test_claims_carry_the_cost_and_its_verdict():
    """One claim per candidate names its per-call cost and, against a priced
    Anthropic reference, the ``cost`` criterion in its bar verdict."""
    from tools.classification_eval import claims_for

    async def priced(prompt: str, system: str | None, output_type: type[BaseModel]):
        return _truth(prompt), 0.002

    reference = Arm(name="anthropic", backend="anthropic", model="m", price=PRICE, call=priced)
    record = await compare(
        _site(minimum_n=2),
        _inputs(2, 0),
        reference=reference,
        candidates=[_arm("decisions", "decisions")],
        contended=False,
    )
    (claim,) = claims_for(record, "ev-1")
    assert "cost_per_call_usd=0.001000" in claim["claim"]
    assert "bar=MISS cost" in claim["claim"]


def test_miss_report_prints_the_cost_threshold_line():
    report = render_report(
        _record_dict(candidates=("decisions",), reference_cost=0.001, cost=0.0005)
    )
    assert "MISS on cost" in report
    assert "cost/call $0.000500 > one tenth of reference $0.001000" in report


# --- evaluate_reference: the fallback proof on a granite reference (#3421) ---------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, []),
        ({"reference_p95_c4": 2.9, "budget_s": 3.0}, []),
        ({"reference_p95_c4": 3.1, "budget_s": 3.0}, ["p95_c4"]),
        # No site budget: a reference arm has no reference of its own to be
        # relative to, so latency is unjudged.
        ({"reference_p95_c4": 9.0}, []),
        ({"contended": True}, ["contended"]),
        ({"reference_error_rate": 0.03}, ["error_rate"]),
        ({"n": 150, "n_real": 100}, ["n"]),
        # The reference arm's own criteria, never the candidates': a candidate
        # error rate and a thin real share leave the reference verdict alone.
        ({"error_rate": 0.5, "n_real": 1}, []),
        (
            {
                "reference_p95_c4": 4.0,
                "budget_s": 3.0,
                "contended": True,
                "reference_error_rate": 0.1,
                "n": 10,
            },
            ["p95_c4", "contended", "error_rate", "n"],
        ),
    ],
)
def test_evaluate_reference_applies_the_latency_only_criteria_to_the_reference_arm(
    kwargs, expected
):
    from tools.classification_eval import evaluate_reference

    record = _record_dict(reference_backend="ollama", candidates=("decisions",), **kwargs)
    assert evaluate_reference(record) == expected


def test_evaluate_reference_needs_a_reference_arm():
    from tools.classification_eval import evaluate_reference

    with pytest.raises(ValueError, match="reference"):
        evaluate_reference(_record_dict(latency_only=True))


# --- contention (Race 3) --------------------------------------------------------


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("", False),
        ("-\t0\tcom.valor.log-rotate\n1678\t0\tcom.valor.caffeinate\n", False),
        ("-\t0\tcom.valor.log-rotate\n64553\t-9\tcom.valor.worker\n", True),
        ("-\t0\tcom.valor.bridge\n", True),
        ("91393\t0\tcom.valor.reflection-worker\n", True),
        ("123\t0\tcom.apple.Finder\n", False),
    ],
)
def test_is_contended_reads_fake_launchctl_output(output, expected):
    assert is_contended(output) is expected


# --- records through the ORM ------------------------------------------------------


def test_evidence_kind_is_registered():
    assert EVIDENCE_KIND == "classifier_comparison"
    assert EVIDENCE_KIND in EVIDENCE_KINDS


async def test_write_record_and_latest_record_round_trip_through_the_orm():
    record = await compare(
        _site(minimum_n=5),
        _inputs(4, 4),
        reference=_arm("reference", "anthropic"),
        candidates=[_arm("ollama", "ollama")],
        contended=False,
    )
    evidence_id = write_record(record, project_key=PK)
    rows = list(ImprovementEvidence.query.filter(project_key=PK, kind=EVIDENCE_KIND))
    assert [row.id for row in rows] == [evidence_id]
    assert json.loads(rows[0].detail)["site"] == "test.site"

    found = latest_record("test.site", project_key=PK)
    assert found is not None
    assert found[0] == evidence_id and found[1]["n"] == 8
    assert latest_record("other.site", project_key=PK) is None


# --- --audit ----------------------------------------------------------------------


def _seed(record: dict) -> str:
    """Write ``record`` as an evidence row whose ``created_at`` (the recency
    the audit orders by) is the record's own ``created_at`` stamp."""
    from datetime import datetime

    row = ImprovementEvidence.record_once(
        PK,
        EVIDENCE_KIND,
        source_ref=f"classification_eval:{record['site']}:{record['run_id']}",
        text=record["site"],
        detail=json.dumps(record),
    )
    assert row is not None
    row.created_at = datetime.fromisoformat(record["created_at"])
    row.save()
    return row.id


@pytest.mark.parametrize(
    ("backend", "record_kwargs", "expected_exit", "marker"),
    [
        (Backend.OLLAMA, None, 1, "no record"),
        (Backend.OLLAMA, {"latency_only": True}, 0, "PASS"),
        (Backend.ANTHROPIC, None, 1, "no record"),
        (Backend.ANTHROPIC, {"agreement": 0.5}, 0, "agreement"),
        (
            Backend.ANTHROPIC,
            {"candidates": ("ollama",), "latency_only": True},
            1,
            "no anthropic arm",
        ),
    ],
)
def test_audit_exit_codes(backend, record_kwargs, expected_exit, marker, capsys):
    """The no-record, latency-only, and ``ANTHROPIC`` rows; the local-landing
    rows are parametrized over every local backend further down."""
    task = _task("test.site", backend)
    if record_kwargs is not None:
        _seed(_record_dict(site="test.site", **record_kwargs))
    code = audit([task], project_key=PK)
    out = capsys.readouterr().out
    assert code == expected_exit
    assert marker in out
    assert "test.site" in out and backend.value in out


def test_audit_skips_client_only_sites_and_exits_zero(capsys):
    task = _task("email_cs.triage", Backend.ANTHROPIC, client_only=True)
    assert audit([task], project_key=PK) == 0
    assert "client_only" in capsys.readouterr().out


def test_audit_over_two_sites_fails_when_either_misses():
    passing = _task("a.site", Backend.OLLAMA)
    _seed(_record_dict(site="a.site"))
    missing = _task("b.site", Backend.OLLAMA)
    assert audit([passing], project_key=PK) == 0
    assert audit([passing, missing], project_key=PK) == 1


# --- --audit: the DECISIONS branch (#3421) --------------------------------------------


def _decisions_record(**kw) -> dict:
    """A decisions comparison: Haiku reference, ``decisions`` and ``ollama``
    candidates, everything passing unless ``kw`` says otherwise."""
    kw.setdefault("candidates", ("decisions", "ollama"))
    return _record_dict(**kw)


def _granite_reference_record(**kw) -> dict:
    """A decisions comparison at a granite-reference site (C12 to C14): the
    ollama arm is the reference, ``decisions`` the one candidate."""
    kw.setdefault("candidates", ("decisions",))
    kw.setdefault("reference_backend", "ollama")
    return _record_dict(**kw)


@pytest.mark.parametrize(
    ("record", "expected_exit", "markers"),
    [
        (_decisions_record(), 0, ["PASS", "fallback=ollama PASS"]),
        (_decisions_record(candidates=("ollama", "decisions")), 0, ["fallback=ollama PASS"]),
        # The ollama candidate misses: its own criteria are named as the fallback's.
        (
            _decisions_record(agreement=0.90),
            1,
            ["decisions=MISS agreement", "MISS fallback agreement"],
        ),
        # The decisions candidate misses while granite passes: still a miss.
        (_decisions_record(contended=True), 1, ["decisions=MISS contended"]),
        (
            _granite_reference_record(budget_s=3.0, reference_p95_c4=2.5),
            0,
            ["PASS", "fallback=ollama PASS"],
        ),
        (
            _granite_reference_record(budget_s=3.0, reference_p95_c4=3.5),
            1,
            ["MISS fallback p95_c4"],
        ),
        (
            _granite_reference_record(reference_error_rate=0.05),
            1,
            ["MISS fallback error_rate"],
        ),
        # No ollama arm anywhere in the record: the row fails naming the absence.
        (_decisions_record(candidates=("decisions",)), 1, ["no ollama arm in the record"]),
        (
            _decisions_record(candidates=("decisions", "anthropic")),
            1,
            ["no ollama arm in the record"],
        ),
    ],
)
def test_audit_decisions_branch_judges_the_ollama_arm_of_the_same_record(
    record, expected_exit, markers, capsys
):
    task = _task("test.site", Backend.DECISIONS)
    _seed(record)
    code = audit([task], project_key=PK)
    out = capsys.readouterr().out
    assert code == expected_exit, out
    for marker in markers:
        assert marker in out, out


def test_audit_decisions_reads_the_reference_slot_on_a_name_collision(capsys):
    """A record carrying granite in both slots under the same name: the
    reference arm (judged by ``evaluate_reference``) misses ``p95_c4`` while a
    same-named ``candidates["ollama"]`` clears ``evaluate_bar``. The branch
    selects the reference slot explicitly; a by-name lookup from the merged
    arms dict would read the candidate's inflated self-agreement as the
    fallback PASS (critique round 2)."""
    record = _record_dict(
        candidates=("decisions", "ollama"),
        reference_backend="ollama",
        reference_name="ollama",
        budget_s=3.0,
        reference_p95_c4=3.5,
        p95_c4=1.0,
        agreement=0.99,
    )
    assert evaluate_bar(record, "ollama") == []
    _seed(record)
    code = audit([_task("test.site", Backend.DECISIONS)], project_key=PK)
    out = capsys.readouterr().out
    assert code == 1, out
    assert "MISS fallback p95_c4" in out and "fallback=ollama" in out


def test_audit_decisions_never_rescues_the_landing_record_from_an_older_one(capsys):
    """A passing ollama arm in an older record never stands in for a failing
    one in the landing record: the audit judges one record and substitutes none."""
    _seed(_decisions_record(run_id="older", created_at="2026-09-19T00:00:00+00:00"))
    _seed(
        _decisions_record(run_id="newest", created_at="2026-09-20T00:00:00+00:00", error_rate=0.05)
    )
    code = audit([_task("test.site", Backend.DECISIONS)], project_key=PK)
    out = capsys.readouterr().out
    assert code == 1, out
    assert "MISS fallback error_rate" in out


def test_audit_decisions_has_no_record_with_a_decisions_arm(capsys):
    _seed(_record_dict())
    code = audit([_task("test.site", Backend.DECISIONS)], project_key=PK)
    out = capsys.readouterr().out
    assert code == 1 and "no decisions arm in the record" in out


def test_audit_ollama_branch_keeps_its_landing_record_under_a_newer_decisions_comparison(
    capsys,
):
    """A granite site's landing evidence is the newest record carrying granite
    as a candidate (a comparison or a latency-only measurement); a later
    decisions comparison whose reference is the ollama arm never displaces it."""
    _seed(_record_dict(latency_only=True, run_id="landing", created_at="2026-09-19T00:00:00+00:00"))
    _seed(
        _granite_reference_record(
            run_id="later", created_at="2026-09-20T00:00:00+00:00", contended=True
        )
    )
    code = audit([_task("test.site", Backend.OLLAMA)], project_key=PK)
    out = capsys.readouterr().out
    assert code == 0, out
    assert "latency-only" in out and "PASS" in out


def test_landing_record_prefers_the_newest_record_carrying_the_backend_as_a_candidate():
    from tools.classification_eval import landing_record

    older = _seed(_decisions_record(run_id="older", created_at="2026-09-19T00:00:00+00:00"))
    newest = _seed(_decisions_record(run_id="newest", created_at="2026-09-20T00:00:00+00:00"))
    reference_only = _seed(
        _granite_reference_record(run_id="ref", created_at="2026-09-21T00:00:00+00:00")
    )
    found = landing_record("test.site", "ollama", project_key=PK)
    assert found is not None and found[0] == newest and found[0] != older
    found = landing_record("test.site", "decisions", project_key=PK)
    assert found is not None and found[0] == reference_only
    assert landing_record("test.site", "anthropic", project_key=PK) is None
    assert landing_record("other.site", "ollama", project_key=PK) is None


# --- declared sites and the CLI ----------------------------------------------------


def test_declared_classification_tasks_are_discovered_from_source():
    tasks = {task.site: task for task in declared_classification_tasks()}
    assert tasks["job_router.route"].backend is Backend.OLLAMA
    assert tasks["job_router.route"].error_cost is ErrorCost.HIGH
    assert tasks["classifier.intake_intent"].backend is Backend.OLLAMA
    assert all(task.kind is TaskKind.CLASSIFICATION for task in tasks.values())


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["ollama"], ["ollama"]),
        (["anthropic,ollama"], ["anthropic", "ollama"]),
        (["anthropic", "ollama"], ["anthropic", "ollama"]),
        (["ollama", "ollama"], ["ollama"]),
        (["decisions,ollama"], ["decisions", "ollama"]),
    ],
)
def test_parse_candidates_accepts_repeats_and_comma_lists(raw, expected):
    assert parse_candidates(raw) == expected


def _fake_arm_builders(monkeypatch) -> list[str]:
    """Replace the live arm constructors with fakes that record which were
    built, so ``_candidate_arms`` touches no network, Redis, or key."""
    from tools.classification_eval import arms

    built: list[str] = []

    def builder(backend: str):
        def build(site_id: str, **kw) -> Arm:
            built.append(backend)
            return _arm(kw.get("name", backend), backend)

        return build

    monkeypatch.setattr(arms, "anthropic_arm", builder("anthropic"))
    monkeypatch.setattr(arms, "ollama_arm", builder("ollama"))
    monkeypatch.setattr(arms, "decisions_arm", builder("decisions"))
    return built


def test_candidate_arms_builds_the_decisions_arm(monkeypatch):
    from tools.classification_eval.__main__ import _candidate_arms

    built = _fake_arm_builders(monkeypatch)
    arms = _candidate_arms("routing.needs_response", ["decisions", "ollama", "anthropic"])
    assert [arm.backend for arm in arms] == ["decisions", "ollama", "anthropic"]
    assert built == ["decisions", "ollama", "anthropic"]


def test_candidate_arms_drops_ollama_when_reference_is_ollama(monkeypatch, capsys):
    """A C12 to C14 record carries granite exactly once, as the reference
    arm; a second copy under the same default name would be a
    granite-against-granite self-comparison (critique round 2)."""
    from tools.classification_eval.__main__ import _candidate_arms

    built = _fake_arm_builders(monkeypatch)
    arms = _candidate_arms("job_router.route", ["decisions", "ollama"])
    assert [arm.backend for arm in arms] == ["decisions"]
    assert built == ["decisions"]
    assert "ollama candidate dropped: the reference arm at job_router.route is granite" in (
        capsys.readouterr().out
    )

    built.clear()
    arms = _candidate_arms("routing.needs_response", ["decisions", "ollama"])
    assert [arm.backend for arm in arms] == ["decisions", "ollama"]
    assert built == ["decisions", "ollama"]


def test_candidate_arms_keeps_the_only_ollama_candidate_at_a_granite_reference_site(
    monkeypatch, capsys
):
    """Regression (#3421 review blocker 2): ``--latency-only`` with no
    ``--candidate`` defaults ``candidates`` to ``["ollama"]``; at a C12 to
    C14 site that used to be unconditionally dropped, leaving ``compare()``
    zero candidate arms and a bare ``ValueError`` from
    ``core.py::compare``. A latency-only run never builds a reference arm,
    so there is no self-comparison to avoid and the candidate must survive."""
    from tools.classification_eval.__main__ import _candidate_arms

    built = _fake_arm_builders(monkeypatch)
    arms = _candidate_arms("classifier.intake_intent", ["ollama"], latency_only=True)
    assert [arm.backend for arm in arms] == ["ollama"]
    assert built == ["ollama"]
    assert capsys.readouterr().out == ""

    # With a reference arm in play the same list is a self-comparison and
    # is refused loudly rather than built: keeping it would put granite in
    # both record slots under one name, which the audit's OLLAMA branch
    # reads as passing landing evidence (#3421 review, tech debt 1).
    built.clear()
    with pytest.raises(ValueError, match="compares granite against itself"):
        _candidate_arms("classifier.intake_intent", ["ollama"], latency_only=False)
    assert built == []


@pytest.mark.parametrize("extra", [[], ["--candidate", "ollama"]])
def test_cli_refuses_ollama_alone_at_a_granite_reference_site_without_latency_only(
    monkeypatch, capsys, extra
):
    """An explicit ``--candidate ollama`` (or the same list spelled twice) at
    C12 to C14 without ``--latency-only`` is a parser error naming the two
    commands that make sense; no input is drawn and no arm is built."""
    from tools.classification_eval import __main__ as cli

    def boom(*a, **kw):
        raise AssertionError("_run_site must not run")

    monkeypatch.setattr(cli, "_run_site", boom)
    with pytest.raises(SystemExit) as exc:
        main(["--site", "classifier.intake_intent", "--candidate", "ollama", *extra])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "compares granite against itself" in err
    assert "use --latency-only, or add a second candidate" in err


def test_cli_refuses_an_unknown_site_as_a_parser_error_naming_the_rows(monkeypatch, capsys):
    from tools.classification_eval import __main__ as cli

    def boom(*a, **kw):
        raise AssertionError("_run_site must not run")

    monkeypatch.setattr(cli, "_run_site", boom)
    with pytest.raises(SystemExit) as exc:
        main(["--site", "no.such_site", "--candidate", "decisions"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "unknown --site 'no.such_site'" in err
    assert "classifier.intake_intent" in err and "Traceback" not in err


def test_cli_lets_ollama_alone_through_at_a_granite_site_with_latency_only(monkeypatch):
    from tools.classification_eval import __main__ as cli

    seen: dict[str, object] = {}

    async def fake_run_site(args, candidates):
        seen["candidates"] = candidates
        return 0

    monkeypatch.setattr(cli, "_run_site", fake_run_site)
    assert (
        main(["--site", "classifier.intake_intent", "--candidate", "ollama", "--latency-only"]) == 0
    )
    assert seen["candidates"] == ["ollama"]
    assert main(["--site", "routing.needs_response", "--candidate", "ollama"]) == 0


@pytest.mark.parametrize(
    "site_id", ["job_router.route", "classifier.intake_intent", "memory_audit.classify"]
)
def test_granite_sites_declare_the_ollama_reference(site_id):
    from tools.classification_eval.sites import site_for

    assert site_for(site_id).reference == "ollama"


def test_candidate_help_names_the_decisions_arm():
    from tools.classification_eval.__main__ import _build_parser

    assert "decisions" in _build_parser().format_help()


async def test_run_site_builds_the_ollama_reference_and_meters_the_decisions_arm(monkeypatch):
    """At a granite-reference site ``_run_site`` measures granite as the
    reference (``ollama_arm(site.id, name="ollama")``) and brackets the run
    with the decisions arm's per-run reservation: one reserve before
    ``compare`` for ``max(0.01, CALL_BOUND_USD * 2 * n)``, one settle after."""
    import argparse

    from agent.llm.backends import decisions as leg
    from tools.classification_eval import __main__ as cli
    from tools.classification_eval import arms as live

    seen: dict[str, object] = {}
    meter_calls: list[tuple[str, object]] = []

    def fake_reserve(project_key, usd, *, purpose, case_id=None, **_):
        from tools import paid_inference_meter as meter

        meter_calls.append(("reserve", (project_key, usd, purpose, case_id)))
        return meter.Reservation("res-1", project_key, round(usd * 100), purpose, case_id, "d")

    def fake_settle(project_key, reservation_id, usd, *, metering):
        meter_calls.append(("settle", (project_key, reservation_id, usd, metering)))

    monkeypatch.setattr(leg.meter, "reserve", fake_reserve)
    monkeypatch.setattr(leg.meter, "settle", fake_settle)
    monkeypatch.setattr(live, "ollama_arm", lambda site_id, **kw: _arm(kw["name"], "ollama"))
    monkeypatch.setattr(live, "site_inputs", lambda site, limit, **kw: _inputs(300, 0))
    monkeypatch.setattr(cli, "is_contended", lambda: False)

    async def fake_compare(site, inputs, *, reference, candidates, contended):
        seen["reference"] = reference
        seen["candidates"] = candidates
        seen["meter_at_compare"] = list(meter_calls)
        raise RuntimeError("stop before any record is written")

    monkeypatch.setattr(cli, "compare", fake_compare)
    args = argparse.Namespace(
        site="job_router.route",
        inputs=None,
        save_inputs=None,
        real_limit=0,
        project_key="test-3421",
        latency_only=False,
        reference_model="free",
        no_attach=True,
    )
    with pytest.raises(RuntimeError, match="stop before"):
        await cli._run_site(args, ["decisions"])

    assert seen["reference"].backend == "ollama" and seen["reference"].name == "ollama"
    assert [arm.backend for arm in seen["candidates"]] == ["decisions"]
    assert isinstance(seen["candidates"][0].call, live.DecisionsArm)
    expected_usd = max(0.01, leg.CALL_BOUND_USD * 2 * 300)
    assert seen["meter_at_compare"] == [
        ("reserve", ("test-3421", expected_usd, leg.METER_PURPOSE, leg.CASE_ID))
    ]
    assert meter_calls[-1] == ("settle", ("test-3421", "res-1", 0.0, "exact"))
    assert len(meter_calls) == 2


async def test_decisions_arm_prices_each_call_from_its_input_tokens(monkeypatch):
    """The arm calls the leg directly with its own envelope; a call's cost is
    ``input_tokens × JEV price / 1e6``, and a response with no ``input_tokens``
    returns ``None`` (the runner estimates) and marks the envelope unknown."""
    from agent.llm.backends import decisions as leg
    from config.models import JEV, JEV_PRICE_USD_PER_MTOKEN
    from tools.classification_eval import arms as live

    tokens = iter([400, None, 250])
    seen: list[dict] = []

    async def fake_call(prompt, output_type, route, *, envelope, **kw):
        seen.append({"prompt": prompt, "route": route, "envelope": envelope, **kw})
        count = next(tokens)
        if count is None:
            envelope.mark_unknown()
        else:
            envelope.add_tokens(count)
        return Verdict(answer="yes")

    monkeypatch.setattr(leg, "call", fake_call)
    arm = live.decisions_arm("routing.needs_response")
    assert (arm.name, arm.backend, arm.model) == ("decisions", "decisions", JEV)
    assert arm.price.model == JEV and arm.price.retrieved_at == "2026-09-21"
    assert arm.price.usd_per_mtoken_in == JEV_PRICE_USD_PER_MTOKEN
    assert arm.price.usd_per_mtoken_out == 0.0

    transport = arm.call
    assert isinstance(transport, live.DecisionsArm)
    first = await transport("p1", None, Verdict)
    second = await transport("p2", "sys", Verdict)
    third = await transport("p3", None, Verdict)
    assert first[0].answer == "yes"
    assert first[1] == pytest.approx(400 * JEV_PRICE_USD_PER_MTOKEN / 1e6)
    assert second[1] is None
    assert third[1] == pytest.approx(250 * JEV_PRICE_USD_PER_MTOKEN / 1e6)
    assert transport.metering == "unknown"
    assert all(s["envelope"] is transport.envelope for s in seen)
    assert seen[1]["system"] == "sys" and seen[1]["route"].model == JEV
    assert seen[0]["route"].backend is Backend.DECISIONS


def test_decisions_arm_refuses_to_build_without_a_known_price(monkeypatch):
    """A ``None`` price constant means unknown, never zero: the record's
    ``price`` block and cost estimate must not read 0.0 beside an envelope
    that settles ``unknown``."""
    from tools.classification_eval import arms as live

    monkeypatch.setattr(live, "JEV_PRICE_USD_PER_MTOKEN", None)
    with pytest.raises(ValueError, match="JEV_PRICE_USD_PER_MTOKEN is None"):
        live.decisions_arm("routing.needs_response")


def test_parse_candidates_rejects_an_unknown_backend():
    with pytest.raises(SystemExit):
        parse_candidates(["gliclass"])


def test_candidate_arm_builders_cover_every_backend():
    """A ``Backend`` member without a runner arm builder fails here by name,
    so ``--candidate <member>`` (accepted by derivation from the enum) can
    never reach a ``KeyError`` in the builders dict."""
    from tools.classification_eval.arms import arm_builders

    builders = arm_builders("test.site")
    missing = sorted(b.value for b in Backend if b.value not in builders)
    assert not missing, f"Backend members without an arm builder: {missing}"
    assert set(builders) == {b.value for b in Backend}
    assert "local_encoder" in parse_candidates(["local_encoder"])


def test_cli_audit_exit_code_is_the_audit_result(capsys):
    task = _task("cli.site", Backend.OLLAMA)
    assert main(["--audit", "--project-key", PK], tasks=[task]) == 1
    assert "no record" in capsys.readouterr().out


def test_cli_site_requires_a_candidate_unless_latency_only():
    with pytest.raises(SystemExit):
        main(["--site", "job_router.route"])


# --- live-arm helpers that are pure logic ------------------------------------------------


def test_real_messages_filters_dedupes_and_orders_deterministically(monkeypatch):
    from types import SimpleNamespace

    from tools.classification_eval import arms

    rows = [
        SimpleNamespace(source="human", agent_id="Tom", content="fix the login bug"),
        SimpleNamespace(source="human", agent_id="Tom", content="fix the login bug"),
        SimpleNamespace(source="human", agent_id="Tom", content="  "),
        SimpleNamespace(source="agent", agent_id="extraction-1", content="an observation"),
        SimpleNamespace(source="human", agent_id="tui-abc", content="In a 19-event session"),
        SimpleNamespace(source="human", agent_id="Tom", content="thanks!"),
    ]
    first = arms.real_messages(10, records=rows)
    second = arms.real_messages(10, records=list(reversed(rows)))
    assert sorted(i.text for i in first) == ["fix the login bug", "thanks!"]
    assert first == second
    assert all(i.source == "real" for i in first)
    assert arms.real_messages(1, records=rows) == first[:1]

    monkeypatch.setattr("tools.improvement_eligibility.is_open_source", lambda key: False)
    with pytest.raises(RuntimeError, match="§7"):
        arms.real_messages(10, project_key="client-x", records=rows)
    assert arms.real_messages(10, project_key="valor", records=rows) == first


def test_inputs_round_trip_through_json_lines():
    from tools.classification_eval import arms

    inputs = [Input("hello?", "real"), Input("fixture", "fixture", {"session_context": "ctx"})]
    assert arms.load_inputs(arms.dump_inputs(inputs)) == inputs


@pytest.mark.parametrize(
    "text",
    ['{"answer": "yes"}', 'Sure. {"answer": "yes"} done', '```json\n{"answer":"yes"}\n```'],
)
def test_parse_json_output_finds_the_object_in_free_text(text):
    from tools.classification_eval.arms import parse_json_output

    assert parse_json_output(text, Verdict).answer == "yes"


def test_parse_json_output_rejects_prose():
    from tools.classification_eval.arms import parse_json_output

    with pytest.raises(ValueError):
        parse_json_output("yes, it is a promise", Verdict)


# --- the site table (Task 7) ---------------------------------------------------------


def test_every_non_client_classification_site_has_a_row():
    """Task 7: every declared classification site except the client-only one
    has a ``sites.py`` row whose task is the declaration itself, so a row cannot
    drift from the declared tier or backend."""
    from tools.classification_eval.sites import SITES

    declared = {t.site: t for t in declared_classification_tasks() if not t.client_only}
    assert set(SITES) == set(declared)
    for site_id, row in SITES.items():
        assert row.id == site_id
        assert row.task == declared[site_id]
        assert row.tier == declared[site_id].error_cost.value


@pytest.mark.parametrize(
    "site_id", sorted(__import__("tools.classification_eval.sites", fromlist=["SITES"]).SITES)
)
def test_site_row_fixtures_build_prompts_and_labels(site_id):
    """Each row's fixture loader yields inputs its prompt builders accept, the
    candidate builders accept, and the label reducer reads from the row's
    output type; a broken row fails here before any reference spend."""
    from tools.classification_eval.sites import SITES

    row = SITES[site_id]
    fixtures = row.fixtures()
    assert fixtures and all(inp.source == "fixture" for inp in fixtures)
    assert len({inp.text for inp in fixtures}) == len(fixtures), "duplicate fixture text"
    for inp in fixtures:
        assert row.prompt(inp).strip()
        if row.candidate_prompt is not None:
            assert row.candidate_prompt(inp).strip()
            assert row.candidate_prompt(inp) != row.prompt(inp), "candidate tail did not apply"
    sample = (row.candidate_output_type or row.output_type).model_json_schema()
    assert sample["properties"]
    # A saved draw (--save-inputs) must round-trip, so the row's context is plain data.
    from tools.classification_eval.arms import dump_inputs, load_inputs

    assert load_inputs(dump_inputs(fixtures)) == fixtures


# --- the gemma reference arm's pacing (C15) ------------------------------------------


async def test_gemma_arm_paces_requests_and_retries_a_429(monkeypatch):
    """OpenRouter's free models allow about twenty requests a minute; the arm
    spaces its requests and, on a 429, waits and retries instead of counting
    the call as a reference error."""
    import httpx

    from tools.classification_eval.arms import OpenRouterGemmaArm

    statuses = iter([429, 200, 200])
    sleeps: list[float] = []
    stamps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        stamps.append(len(sleeps))
        if status == 429:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"answer": "yes"}'}}],
                "usage": {"cost": 0.0},
            },
        )

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    arm = OpenRouterGemmaArm(
        "test-key", transport=httpx.MockTransport(handler), min_interval_s=3.0, sleep=fake_sleep
    )
    first, cost, waited = await arm("p1", None, Verdict)
    second, _, _ = await arm("p2", None, Verdict)
    assert first.answer == "yes" and second.answer == "yes" and cost == 0.0
    # The 429 cost one retry wait; the second call waited out the interval;
    # the arm reports the waiting so the runner keeps it out of the latency.
    assert len(sleeps) >= 2 and all(s > 0 for s in sleeps)
    assert waited >= 0.0
    assert arm.metering == "exact"


async def test_an_arm_s_reported_rate_limit_wait_is_left_out_of_its_latency():
    """A paced reference arm returns the seconds it spent waiting on its own
    limiter as a third element; the runner subtracts it, so the reference p95
    (and the budget derived from it) measures the request, not the pacing."""

    async def call(prompt: str, system: str | None, output_type: type[BaseModel]):
        return Verdict(answer="yes"), 0.0, 5.0

    arm = Arm(name="paced", backend="openrouter", model="m", price=PRICE, call=call)
    record = await compare(
        _site(minimum_n=2),
        _inputs(2, 0),
        reference=arm,
        candidates=[_arm("ollama", "ollama")],
        contended=False,
    )
    assert record.reference.p95_c1 < 1.0 and record.reference.p95_c4 < 1.0
    assert all(latency >= 0.0 for latency in record.reference.latency_c1)


def test_gemma_arm_paid_route_carries_its_own_model_and_price(monkeypatch):
    from config.settings import settings
    from tools.classification_eval import arms

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(settings.api, "openrouter_api_key", "test-key")
    free_arm, _ = arms.openrouter_gemma_arm()
    paid_arm, transport = arms.openrouter_gemma_arm(paid=True)
    assert free_arm.model.endswith(":free") and free_arm.price.usd_per_mtoken_in == 0.0
    assert paid_arm.model == free_arm.model.removesuffix(":free") == transport.model
    assert paid_arm.price.usd_per_mtoken_in > 0.0 and paid_arm.price.retrieved_at


# --- the fit path (#3420 lane B): split, fit, head files -------------------------------


def _fake_embed(text: str):
    """A deterministic 384-d unit vector per text with class-correlated
    structure (the ``?`` that decides the truth pushes along one axis), so a
    linear head can learn the reference labels from it."""
    import hashlib

    import numpy as np

    from config.models import LOCAL_ENCODER_DIM

    seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
    vector = np.random.default_rng(seed).standard_normal(LOCAL_ENCODER_DIM)
    vector[0] += 4.0 if "?" in text else -4.0
    return (vector / np.linalg.norm(vector)).astype(np.float32)


def test_split_by_digest_is_a_function_of_the_inputs_alone():
    """Two draws of the same input set in different orders give the same
    held-out set; the held-out split takes real inputs first (up to half the
    minimum), then fills to the minimum by digest; the rest is training."""
    import random

    from tools.classification_eval.fit import split_by_digest

    inputs = _inputs(30, 40)
    shuffled = list(inputs)
    random.Random(7).shuffle(shuffled)
    held_a, train_a = split_by_digest(inputs, 50)
    held_b, train_b = split_by_digest(shuffled, 50)
    assert held_a == held_b and train_a == train_b
    assert len(held_a) == 50 and len(train_a) == 20
    assert sum(1 for i in held_a if i.source == "real") >= 25
    assert {i.text for i in held_a}.isdisjoint({i.text for i in train_a})
    assert {i.text for i in held_a} | {i.text for i in train_a} == {i.text for i in inputs}


def test_split_by_digest_at_the_routing_boundary_trains_on_fixtures_only():
    """A routing site at exactly 100 real (188 fixtures, minimum 200): the
    held-out split takes all 100 real and 100 fixtures, so training is 88
    fixtures and no real message (Risk 1)."""
    from tools.classification_eval.fit import split_by_digest

    held, train = split_by_digest(_inputs(100, 188), 200)
    assert len(held) == 200 and sum(1 for i in held if i.source == "real") == 100
    assert len(train) == 88 and sum(1 for i in train if i.source == "real") == 0


@pytest.mark.parametrize(
    ("n_real", "n_fixture", "minimum_n"),
    [(10, 100, 50), (24, 100, 50), (30, 10, 50)],
)
def test_split_by_digest_refuses_under_the_minimums(n_real, n_fixture, minimum_n):
    from tools.classification_eval.fit import split_by_digest

    with pytest.raises(ShortfallError):
        split_by_digest(_inputs(n_real, n_fixture), minimum_n)


def test_fit_head_is_deterministic_and_learns_the_labels():
    import numpy as np

    from tools.classification_eval.fit import cv_agreement, fit_head, predict

    texts = [f"text {i}{'?' if i % 2 else ''}" for i in range(60)]
    vectors = np.stack([_fake_embed(t) for t in texts])
    labels = ["yes" if "?" in t else "no" for t in texts]
    first = fit_head(vectors, labels, ["no", "yes"])
    second = fit_head(vectors, labels, ["no", "yes"])
    assert first.W.tobytes() == second.W.tobytes() and first.b.tobytes() == second.b.tobytes()
    assert first.W.shape == (vectors.shape[1], 2) and first.b.shape == (2,)
    assert predict(vectors, first, ["no", "yes"]) == labels
    assert cv_agreement(vectors, labels, ["no", "yes"]) == 1.0
    assert cv_agreement(vectors, labels, ["no", "yes"], seed=1) == 1.0


def test_cv_agreement_denominator_excludes_a_degenerate_folds_held_items():
    """A single minority-class example forces exactly one fold's training
    split down to one class (skipped via ``continue``), whichever fold holds
    it. That fold's held-out items must not count in the denominator: the
    remaining folds are perfectly learnable, so the ratio must be exactly
    1.0, not deflated by the skipped fold's uncounted items."""
    import numpy as np

    from tools.classification_eval.fit import cv_agreement

    texts = [f"text {i}" for i in range(20)] + ["text 20?"]
    vectors = np.stack([_fake_embed(t) for t in texts])
    labels = ["yes" if "?" in t else "no" for t in texts]

    assert cv_agreement(vectors, labels, ["no", "yes"]) == 1.0
    assert cv_agreement(vectors, labels, ["no", "yes"], seed=7) == 1.0


# --- the fit path: run_fit, the landing gate, the audit's head provenance -------------


@pytest.fixture
def fit_env(monkeypatch, tmp_path):
    """The fit path with no network and no ONNX: the leg's ``embed`` is the
    deterministic fake, its ``load_runtime`` a no-op (``runtime_loads``
    counts the pre-spend verification), the staged and served head
    directories are temp dirs, and case claims are recorded on a list
    instead of the substrate."""
    from types import SimpleNamespace

    leg = pytest.importorskip("agent.llm.backends.local_encoder")
    from tools.classification_eval import fit, records

    served_dir = tmp_path / "served"
    runtime_loads = {"n": 0}

    def load_runtime():
        runtime_loads["n"] += 1
        return object()

    monkeypatch.setattr(leg, "embed", _fake_embed)
    monkeypatch.setattr(leg, "load_runtime", load_runtime)
    monkeypatch.setattr(fit, "STAGED_HEADS_DIR", tmp_path / "staged")
    monkeypatch.setattr(fit, "served_head_path", lambda site: served_dir / f"{site}.json")
    claims: list[tuple[str, tuple]] = []
    monkeypatch.setattr(
        records, "attach_claims", lambda record, evidence_id, **kw: claims.append(("run", ()))
    )
    monkeypatch.setattr(
        records,
        "attach_precheck_claim",
        lambda site_id, agreement, bar, **kw: claims.append(("precheck", (site_id, agreement))),
    )
    return SimpleNamespace(
        leg=leg, fit=fit, served_dir=served_dir, claims=claims, runtime_loads=runtime_loads
    )


def _counting_reference(**kw) -> tuple[Arm, dict]:
    """The fake reference arm with a visible call counter (the spend gauge)."""
    calls = {"n": 0}
    inner = _arm("reference", "anthropic", **kw)

    async def call(prompt, system, output_type):
        calls["n"] += 1
        return await inner.call(prompt, system, output_type)

    return Arm(name="reference", backend="anthropic", model="haiku", price=PRICE, call=call), calls


def _builder(*, encoder_flip: int = 0, anthropic_flip: int = 0):
    """``build_arm`` for :func:`run_fit`: the real ``local_encoder`` arm on
    the staged head (a scripted flipper when ``encoder_flip``, free per call
    like the real arm), and a fake ``anthropic`` arm that misses the bar
    when ``anthropic_flip``."""
    from tools.classification_eval.arms import local_encoder_arm

    def build(name, staged):
        if name == "anthropic":
            return _arm("anthropic", "anthropic", flip_every=anthropic_flip)
        if encoder_flip:
            return _arm("local_encoder", "local_encoder", flip_every=encoder_flip, cost=0.0)
        return local_encoder_arm("test.site", head_path=staged)

    return build


async def _fit(
    fit_env, *, land=False, inputs=None, reference=None, build=None, contended=False, **kw
):
    from tools.classification_eval.fit import run_fit

    reference = reference or _counting_reference()[0]
    return await run_fit(
        _site(minimum_n=50),
        inputs if inputs is not None else _inputs(40, 40),
        reference=reference,
        candidates=["local_encoder", "anthropic"],
        build_arm=build or _builder(),
        contended=contended,
        land=land,
        project_key=PK,
        out=lambda line: None,
        **kw,
    )


def _sha256(path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


async def test_fit_measures_on_the_held_out_split_and_the_head_round_trips(fit_env, capsys):
    """A measure-only run: the staged head is written and loads through the
    leg's validating loader, the record is measured on the held-out split only
    (n = minimum), carries the ``fit`` block with ``landed: false``, and the
    served path is never written."""
    from tools.classification_eval.fit import run_fit

    reference, calls = _counting_reference()
    lines: list[str] = []
    outcome = await run_fit(
        _site(minimum_n=50),
        _inputs(40, 40),
        reference=reference,
        candidates=["local_encoder", "anthropic"],
        build_arm=_builder(),
        contended=False,
        project_key=PK,
        out=lines.append,
    )
    record = outcome.record.as_dict()
    assert record["n"] == 50 and record["n_real"] >= 25
    n_train_real = 40 - record["n_real"]
    assert record["fit"] == {
        "head_run_id": outcome.run_id,
        "n_train": 30,
        "n_train_real": n_train_real,
        "split": "digest",
        "landed": False,
        "miss_arms": [],
        "miss_criteria": {},
    }
    assert record["run_id"] == outcome.run_id
    assert evaluate_bar(record, "local_encoder") == [] and evaluate_bar(record, "anthropic") == []
    # Training labels once (30), then the two passes over the 50 held-out inputs.
    assert calls["n"] == 30 + 100
    assert any("training-split agreement 1.000" in line for line in lines)

    head = fit_env.leg.load_head(outcome.staged_path)
    assert head.run_id == outcome.run_id and head.site == "test.site"
    assert head.classes == ["no", "yes"] and head.n_train == 30
    assert head.n_train_real == n_train_real
    assert head.fit_settings == {"epochs": 300, "lr": 0.5, "l2": 0.001, "normalized": True}
    assert not fit_env.served_dir.exists()
    assert latest_record("test.site", project_key=PK)[1]["fit"]["landed"] is False
    assert fit_env.claims == [("run", ())]
    assert f"fit: n_train=30 n_train_real={n_train_real}" in render_report(record)


async def test_fit_refuses_under_the_minimums_before_any_arm_call(fit_env):
    reference, calls = _counting_reference()
    with pytest.raises(ShortfallError):
        await _fit(fit_env, inputs=_inputs(10, 100), reference=reference)
    assert calls["n"] == 0
    assert latest_record("test.site", project_key=PK) is None


async def test_fit_refuses_when_contended_before_any_arm_call(fit_env):
    from tools.classification_eval.fit import FitRefusalError

    reference, calls = _counting_reference()
    with pytest.raises(FitRefusalError, match="services are loaded"):
        await _fit(fit_env, reference=reference, contended=True)
    assert calls["n"] == 0


async def test_land_without_the_anthropic_candidate_refuses_before_any_spend(fit_env):
    from tools.classification_eval.fit import FitRefusalError, run_fit

    reference, calls = _counting_reference()
    with pytest.raises(FitRefusalError, match="anthropic"):
        await run_fit(
            _site(minimum_n=50),
            _inputs(40, 40),
            reference=reference,
            candidates=["local_encoder"],
            build_arm=_builder(),
            contended=False,
            land=True,
            project_key=PK,
            out=lambda line: None,
        )
    assert calls["n"] == 0 and not fit_env.served_dir.exists()


def test_cli_land_without_anthropic_exits_2_with_no_spend(fit_env, monkeypatch):
    """The CLI shape of the same refusal: ``--fit --land --candidate
    local_encoder`` exits 2 with no reference call and no record (the
    reference arm object is built first; it is never called)."""
    from tools.classification_eval import arms

    built = {"reference": 0}

    def anthropic_arm(site_id, **kw):
        built["reference"] += 1
        return _arm("anthropic", "anthropic")

    monkeypatch.setattr(arms, "anthropic_arm", anthropic_arm)
    monkeypatch.setattr(arms, "site_inputs", lambda site, limit, **kw: _inputs(40, 40))
    monkeypatch.setattr(
        "tools.classification_eval.sites.site_for", lambda site_id: _site(minimum_n=50)
    )
    monkeypatch.setattr("tools.classification_eval.__main__.is_contended", lambda: False)
    code = main(
        [
            "--site",
            "test.site",
            "--fit",
            "--land",
            "--candidate",
            "local_encoder",
            "--project-key",
            PK,
        ]
    )
    assert code == 2
    assert latest_record("test.site", project_key=PK) is None


def test_cli_fit_refuses_the_decisions_candidate_before_any_spend(fit_env, monkeypatch, capsys):
    """``--fit --candidate decisions`` (alone or alongside the fit arms) is a
    parser error, exit 2, naming the ``--site`` path the decisions arm belongs
    to: ``_run_fit`` never starts, no reference arm is built, no record is
    written, and no head is staged."""
    from tools.classification_eval import __main__ as cli
    from tools.classification_eval import arms

    def boom(*a, **kw):
        raise AssertionError("_run_fit must not run")

    monkeypatch.setattr(cli, "_run_fit", boom)
    monkeypatch.setattr(arms, "anthropic_arm", boom)
    monkeypatch.setattr(
        "tools.classification_eval.sites.site_for", lambda site_id: _site(minimum_n=50)
    )
    monkeypatch.setattr("tools.classification_eval.__main__.is_contended", lambda: False)
    for candidate in ("decisions", "local_encoder,anthropic,decisions"):
        with pytest.raises(SystemExit) as exc:
            main(["--site", "test.site", "--fit", "--candidate", candidate, "--project-key", PK])
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "--fit measures the local_encoder head" in err
        assert "compared through --site without --fit" in err
    assert latest_record("test.site", project_key=PK) is None
    assert fit_env.runtime_loads["n"] == 0
    assert not (fit_env.fit.STAGED_HEADS_DIR).exists() and not fit_env.served_dir.exists()


def test_a_served_head_path_is_not_gitignored():
    """The broad ``*.json`` rule in ``.gitignore`` would swallow the head a
    landing commits (#3544 step "commit the head"); the negation for
    ``agent/llm/backends/heads/*.json`` keeps ``git add -A`` staging it.
    ``git check-ignore`` exits 1 for a path no rule ignores, and with ``-v``
    names the negating rule when one matched last."""
    import subprocess
    from pathlib import Path

    from agent.llm.backends.local_encoder import served_head_path

    repo_root = Path(__file__).resolve().parents[2]
    relative = served_head_path("routing.needs_response").relative_to(repo_root).as_posix()
    assert relative == "agent/llm/backends/heads/routing.needs_response.json"

    def check_ignore(path: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "check-ignore", "-v", path],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )

    # -v prints the last matching rule; a negation there (exit 0) or no rule at
    # all (exit 1) both mean the head is staged. A plain `*.json` line is the
    # regression.
    probe = check_ignore(relative)
    assert probe.returncode in (0, 1), probe.stderr
    rule = probe.stdout.split("\t", 1)[0] if probe.stdout else ""
    assert probe.returncode == 1 or rule.endswith("!agent/llm/backends/heads/*.json"), probe.stdout
    # The control: a json beside the heads dir is still swallowed by `*.json`.
    control = check_ignore("agent/llm/backends/scratch.json")
    assert control.returncode == 0 and control.stdout.split("\t", 1)[0].endswith("*.json"), (
        control.stdout
    )


async def test_precheck_gate_skips_with_zero_reference_calls_and_one_claim(fit_env):
    reference, calls = _counting_reference()
    outcome = await _fit(fit_env, reference=reference, precheck_agreement=0.84)
    assert outcome.skipped == "precheck_below_bar" and outcome.record is None
    assert calls["n"] == 0
    assert latest_record("test.site", project_key=PK) is None
    assert fit_env.claims == [("precheck", ("test.site", 0.84))]

    # Inside the margin (bar 0.95 - 0.10 = 0.85) the fit runs.
    fit_env.claims.clear()
    outcome = await _fit(fit_env, reference=reference, precheck_agreement=0.85)
    assert outcome.skipped is None and outcome.record is not None and calls["n"] > 0


async def test_encoder_runtime_is_verified_before_the_first_reference_call(fit_env, monkeypatch):
    """A machine that cannot load the encoder (extra, weights) refuses the fit
    with zero reference calls and no record; on a machine that can, the
    runtime is loaded before labeling starts."""
    from agent.llm.errors import LLMCallError
    from tools.classification_eval.fit import FitRefusalError

    reference, calls = _counting_reference()
    order: list[str] = []

    async def counting_call(prompt, system, output_type):
        order.append("reference")
        return await reference.call(prompt, system, output_type)

    counting = Arm(
        name="reference", backend="anthropic", model="m", price=PRICE, call=counting_call
    )

    def load_runtime():
        order.append("runtime")
        return object()

    monkeypatch.setattr(fit_env.leg, "load_runtime", load_runtime)
    await _fit(fit_env, reference=counting)
    assert order[0] == "runtime" and order.count("runtime") == 1 and "reference" in order

    def refuse():
        raise LLMCallError("weights file onnx/model_int8.onnx missing", reason="transport")

    monkeypatch.setattr(fit_env.leg, "load_runtime", refuse)
    reference, calls = _counting_reference()
    fit_env.claims.clear()
    with pytest.raises(FitRefusalError, match="encoder runtime unavailable.*model_int8"):
        await _fit(fit_env, reference=reference)
    assert calls["n"] == 0
    assert not (fit_env.fit.STAGED_HEADS_DIR / "test.site.json").exists()
    assert fit_env.claims == []


def test_cli_encoder_runtime_refusal_exits_2(fit_env, monkeypatch, capsys):
    from agent.llm.errors import LLMCallError
    from tools.classification_eval import arms

    reference, calls = _counting_reference()
    monkeypatch.setattr(arms, "anthropic_arm", lambda site_id, **kw: reference)
    monkeypatch.setattr(arms, "site_inputs", lambda site, limit, **kw: _inputs(40, 40))
    monkeypatch.setattr(
        "tools.classification_eval.sites.site_for", lambda site_id: _site(minimum_n=50)
    )
    monkeypatch.setattr("tools.classification_eval.__main__.is_contended", lambda: False)

    def refuse():
        raise LLMCallError("classification-local extra not installed", reason="transport")

    monkeypatch.setattr(fit_env.leg, "load_runtime", refuse)
    argv = ["--site", "test.site", "--fit", "--candidate", "local_encoder,anthropic"]
    code = main([*argv, "--project-key", PK])
    assert code == 2 and calls["n"] == 0
    assert "extra not installed" in capsys.readouterr().err
    assert latest_record("test.site", project_key=PK) is None


async def test_empty_training_split_is_refused_before_any_spend(fit_env):
    """A draw of exactly ``minimum_n`` inputs passes the held-out rule with
    nothing left to train on; that is a refusal naming the cause, not a
    one-class abort after labeling."""
    from tools.classification_eval.fit import FitRefusalError

    reference, calls = _counting_reference()
    with pytest.raises(FitRefusalError, match="nothing to train on"):
        await _fit(fit_env, inputs=_inputs(25, 25), reference=reference)
    assert calls["n"] == 0
    assert latest_record("test.site", project_key=PK) is None


async def test_training_error_rate_over_the_limit_aborts_with_no_head(fit_env):
    from tools.classification_eval.fit import FitError

    reference, _ = _counting_reference(error_every=10)
    with pytest.raises(FitError, match="error rate"):
        await _fit(fit_env, reference=reference)
    assert not (fit_env.fit.STAGED_HEADS_DIR).exists() and not fit_env.served_dir.exists()
    assert latest_record("test.site", project_key=PK) is None


async def test_one_class_training_labels_write_no_head(fit_env):
    from tools.classification_eval.fit import FitError

    async def always_no(prompt, system, output_type):
        return Verdict(answer="no"), 0.0

    reference = Arm(name="reference", backend="anthropic", model="m", price=PRICE, call=always_no)
    with pytest.raises(FitError, match="one class"):
        await _fit(fit_env, reference=reference)
    assert not (fit_env.fit.STAGED_HEADS_DIR).exists()


async def test_land_copies_the_head_only_when_both_arms_clear_the_bar(fit_env):
    outcome = await _fit(fit_env, land=True)
    served = fit_env.served_dir / "test.site.json"
    assert outcome.landed is True and outcome.miss_arms == []
    assert served.exists() and _sha256(served) == _sha256(outcome.staged_path)
    record = latest_record("test.site", project_key=PK)[1]
    assert record["fit"]["landed"] is True and record["fit"]["head_run_id"] == outcome.run_id
    assert fit_env.leg.load_head(served).run_id == outcome.run_id


@pytest.mark.parametrize(
    ("builder_kwargs", "expected_miss"),
    [({"anthropic_flip": 3}, ["anthropic"]), ({"encoder_flip": 3}, ["local_encoder"])],
)
async def test_land_miss_on_either_arm_leaves_no_served_head(
    fit_env, builder_kwargs, expected_miss
):
    """An Anthropic-arm miss (the restructured-shape fallback under the bar,
    Risk 3) or an encoder-arm miss is a landing MISS: a pre-existing served
    head is deleted, ``fit.landed`` is false, ``fit.miss_arms`` names the arm,
    and the report prints the arm and criterion."""
    landed = await _fit(fit_env, land=True)
    served = fit_env.served_dir / "test.site.json"
    assert served.exists() and landed.landed

    outcome = await _fit(fit_env, land=True, build=_builder(**builder_kwargs))
    assert outcome.landed is False and outcome.miss_arms == expected_miss
    assert not served.exists()
    record = outcome.record.as_dict()
    assert record["fit"]["landed"] is False
    assert record["fit"]["miss_arms"] == expected_miss
    assert record["fit"]["miss_criteria"] == {expected_miss[0]: ["agreement"]}
    report = render_report(record)
    assert f"landing MISS: {expected_miss[0]} on agreement" in report
    assert "landed=false" in report


async def test_measure_only_runs_never_touch_the_served_head(fit_env, capsys):
    """Served-head protection: land once, then two measure-only runs with a
    scripted MISS leave the served file byte-identical, write their records
    with ``fit.landed: false``, and the audit still judges the landed record."""
    import time

    landed = await _fit(fit_env, land=True)
    served = fit_env.served_dir / "test.site.json"
    before = _sha256(served)

    for _ in range(2):
        time.sleep(0.002)
        outcome = await _fit(fit_env, build=_builder(anthropic_flip=3, encoder_flip=3))
        assert outcome.landed is False and outcome.record.fit["landed"] is False
        assert evaluate_bar(outcome.record.as_dict(), "local_encoder") == ["agreement"]
        assert served.exists() and _sha256(served) == before

    newest = latest_record("test.site", project_key=PK)[1]
    assert newest["fit"]["landed"] is False and newest["run_id"] != landed.run_id
    assert audit([_task("test.site", Backend.LOCAL_ENCODER)], project_key=PK) == 0
    out = capsys.readouterr().out
    assert f"head={landed.run_id}" in out and "PASS" in out


async def test_audit_head_provenance_for_a_local_encoder_landing(fit_env, capsys):
    """The audit ties the committed head to the latest landed record: a head
    whose ``run_id`` differs exits 1; no head file exits 1; only measure-only
    records exit 1; no record at all exits 1."""
    import json as json_module

    task = _task("test.site", Backend.LOCAL_ENCODER)
    assert audit([task], project_key=PK) == 1
    assert "no landed record" in capsys.readouterr().out

    outcome = await _fit(fit_env)  # measure-only: a record, no head
    assert audit([task], project_key=PK) == 1
    assert "no landed record" in capsys.readouterr().out

    landed = await _fit(fit_env, land=True)
    assert audit([task], project_key=PK) == 0
    assert f"head={landed.run_id}" in capsys.readouterr().out

    served = fit_env.served_dir / "test.site.json"
    payload = json_module.loads(served.read_text())
    payload["run_id"] = "someone-elses-fit"
    served.write_text(json_module.dumps(payload))
    assert audit([task], project_key=PK) == 1
    assert f"head run_id someone-elses-fit != record fit.head_run_id {landed.run_id}" in (
        capsys.readouterr().out
    )

    served.unlink()
    assert audit([task], project_key=PK) == 1
    assert "no head file" in capsys.readouterr().out
    assert outcome.run_id != landed.run_id


async def test_audit_renders_an_unreadable_committed_head_as_a_miss_row(fit_env, capsys):
    """A committed head the leg's loader refuses (malformed JSON here, a
    wrong digest or shape the same way) is a MISS row naming the site and
    the error, and the walk goes on to the next site's row."""
    landed = await _fit(fit_env, land=True)
    served = fit_env.served_dir / "test.site.json"
    served.write_text("{not json")

    tasks = [_task("test.site", Backend.LOCAL_ENCODER), _task("zz.site", Backend.ANTHROPIC)]
    assert audit(tasks, project_key=PK) == 1
    out = capsys.readouterr().out
    rows = {line.split()[0]: line for line in out.splitlines() if line.strip()}
    assert "MISS" in rows["test.site"] and "head unreadable" in rows["test.site"]
    assert str(served) in rows["test.site"] and "unreadable head file" in rows["test.site"]
    assert rows["zz.site"].endswith("no record")
    assert out.rstrip().endswith("audit: FAIL")
    assert landed.run_id not in rows["test.site"].split("head unreadable")[1]


@pytest.mark.parametrize("anthropic_arm", ["missing", "under_the_bar"])
def test_audit_local_encoder_landed_record_needs_the_anthropic_arm_clear(
    anthropic_arm, monkeypatch, capsys
):
    """A landed record whose ``anthropic`` arm misses the bar, or carries no
    ``anthropic`` candidate arm at all, is a MISS even with the encoder arm
    clear and the head in place (Risk 3)."""
    from tools.classification_eval import records

    monkeypatch.setattr(records, "committed_head_run_id", lambda site: "head-1")
    candidates = (
        ("local_encoder",) if anthropic_arm == "missing" else ("local_encoder", "anthropic")
    )
    record = _record_dict(candidates=candidates)
    if anthropic_arm == "under_the_bar":
        record["candidates"]["anthropic"]["agreement"]["mean"] = 0.5
    record["fit"] = {"head_run_id": "head-1", "landed": True}
    _seed(record)
    assert audit([_task("test.site", Backend.LOCAL_ENCODER)], project_key=PK) == 1
    assert "anthropic fallback arm must clear" in capsys.readouterr().out


@pytest.mark.parametrize("backend", [Backend.OLLAMA, Backend.LOCAL_ENCODER])
@pytest.mark.parametrize(
    ("record_kwargs", "expected_exit", "marker"),
    [
        ({"contended": True}, 1, "contended"),
        ({"n_real": 50}, 1, "n_real"),
        ({"agreement": 0.9}, 1, "agreement"),
        ({}, 0, "PASS"),
    ],
)
def test_audit_landed_arm_rule_applies_to_every_local_backend(
    backend, record_kwargs, expected_exit, marker, capsys, monkeypatch
):
    """The landed-arm rule (a candidate on the declared backend clears the
    bar) applies to every backend other than ``ANTHROPIC``; for
    ``LOCAL_ENCODER`` the record must also be landed with the head in place."""
    from tools.classification_eval import records

    record = _record_dict(candidates=(backend.value, "anthropic"), **record_kwargs)
    record["fit"] = {"head_run_id": "run-1", "landed": True}
    _seed(record)
    monkeypatch.setattr(records, "committed_head_run_id", lambda site: "run-1")
    code = audit([_task("test.site", backend)], project_key=PK)
    out = capsys.readouterr().out
    assert code == expected_exit and marker in out and backend.value in out


def test_precheck_scores_every_site_with_a_record_through_fit_head(fit_env, monkeypatch, capsys):
    """``--precheck`` pairs the record's fixture-prefix reference labels with
    ``site.fixtures()``, scores them five-fold through the one ``fit_head``,
    excludes C12 and client-only sites, and prints a markdown table ordered
    by agreement relative to the bar. Zero spend."""
    from dataclasses import replace

    from tools.classification_eval import fit

    fixtures = [Input(f"fx {i}{'?' if i % 2 else ''}", "fixture") for i in range(24)]
    labels = ["yes" if "?" in inp.text else "no" for inp in fixtures]
    rows = {
        "a.site": replace(_site("a.site"), fixtures=lambda: fixtures),
        "b.site": replace(
            _site("b.site"),
            task=_task("b.site", Backend.ANTHROPIC, ErrorCost.LOW),
            fixtures=lambda: fixtures,
        ),
        "job_router.route": replace(_site("job_router.route"), fixtures=lambda: fixtures),
        "email_cs.triage": replace(
            _site("email_cs.triage"),
            task=_task("email_cs.triage", Backend.ANTHROPIC, client_only=True),
            fixtures=lambda: fixtures,
        ),
        "c.site": _site("c.site"),
    }
    # b.site's reference disagrees with the fake embedding's structure on a third.
    noisy = [("no" if i % 3 == 0 else label) for i, label in enumerate(labels)]
    for site_id in ("a.site", "b.site", "job_router.route", "email_cs.triage"):
        record = _record_dict(site=site_id, n=30, n_real=6, minimum_n=30)
        record["reference"]["labels"] = (noisy if site_id == "b.site" else labels) + ["yes"] * 6
        _seed(record)

    calls = {"n": 0}
    real_fit_head = fit.fit_head

    def spy(vectors, labels, classes):
        calls["n"] += 1
        return real_fit_head(vectors, labels, classes)

    monkeypatch.setattr(fit, "fit_head", spy)
    assert fit.precheck(sites=rows, project_key=PK) == 0
    out = capsys.readouterr().out
    assert calls["n"] == 10  # five folds per scored site, two sites scored
    lines = out.splitlines()
    assert lines[0].startswith("| site |") and lines[1].startswith("|---")
    a_cells = [cell.strip() for cell in lines[2].strip("|").split("|")]
    b_cells = [cell.strip() for cell in lines[3].strip("|").split("|")]
    assert a_cells[0] == "a.site" and a_cells[1] == "24" and float(a_cells[2]) >= 0.9
    assert a_cells[3:] == ["0.500", "0.95", "0.85", "fit"]
    assert b_cells[0] == "b.site" and float(b_cells[2]) < float(a_cells[2])
    assert b_cells[4:6] == ["0.85", "0.75"]
    assert b_cells[6] == ("fit" if float(b_cells[2]) >= 0.75 else "precheck_below_bar")
    assert "job_router.route" not in out and "email_cs.triage" not in out
    assert "skipped: c.site: no lane A record" in out

    # run_fit fits through the same fit_head (the gate reads the head that serves).
    import asyncio

    calls["n"] = 0
    asyncio.run(_fit(fit_env))
    assert calls["n"] == 1


def test_precheck_marks_a_site_under_the_gate(monkeypatch):
    from tools.classification_eval.fit import PrecheckRow, render_precheck

    row = PrecheckRow(site="routing.terminus", n=188, agreement=0.824, majority=0.809, bar=0.95)
    assert row.gate == pytest.approx(0.85) and row.mark == "precheck_below_bar"
    table = render_precheck([row], ["x.site: no lane A record with a reference arm"])
    assert "| routing.terminus | 188 | 0.824 | 0.809 | 0.95 | 0.85 | precheck_below_bar |" in table
    assert table.endswith("skipped: x.site: no lane A record with a reference arm")


@pytest.mark.parametrize(
    ("n_real", "expected_exit", "routing_line", "small_line"),
    [
        (
            12,
            1,
            "this store has 12: short by 88",
            "this store has 12: short by 13",
        ),
        (
            100,
            0,
            "this store has 100: ok; n_train = 88 (188 fixtures + 100 real - 200);"
            " n_train_real = 0",
            "this store has 100: ok; n_train = 90 (40 fixtures + 100 real - 50); n_train_real = ",
        ),
    ],
)
def test_preflight_reports_the_real_count_against_each_site_s_need(
    n_real, expected_exit, routing_line, small_line, capsys
):
    """``--preflight`` prints the real-message count, each site's held-out
    need, and the training arithmetic from ``split_by_digest`` on the actual
    draw; exit 1 under the routing sites' need. At exactly 100 real a routing
    site trains on 88 fixtures and no real message (Risk 1)."""
    from dataclasses import replace

    from tools.classification_eval.fit import preflight

    real = [Input(f"real {i}{'?' if i % 2 else ''}", "real") for i in range(n_real)]
    sites = {
        "routing.site": replace(
            _site("routing.site", minimum_n=200),
            fixtures=lambda: [Input(f"fx {i}", "fixture") for i in range(188)],
        ),
        "small.site": replace(
            _site("small.site", minimum_n=50),
            fixtures=lambda: [Input(f"fx {i}", "fixture") for i in range(40)],
        ),
        "client.site": replace(
            _site("client.site"), task=_task("client.site", Backend.ANTHROPIC, client_only=True)
        ),
    }
    code = preflight(2000, sites=sites, real=lambda limit, *, project_key: real[:limit])
    out = capsys.readouterr().out
    assert code == expected_exit
    assert out.splitlines()[0] == f"real messages available: {n_real}"
    assert "routing.site" in out and "needs >= 100 real in the held-out split" in out
    assert routing_line in out and small_line in out
    assert "needs >= 25 real" in out and "client.site" not in out
    if n_real == 100:
        from tools.classification_eval.fit import split_by_digest

        _, train = split_by_digest(sites["small.site"].fixtures() + real, 50)
        n_train_real = sum(1 for inp in train if inp.source == "real")
        assert f"n_train_real = {n_train_real}\n" in out and 25 <= n_train_real <= 75
    if expected_exit:
        assert "routing sites need >= 100 real in the held-out split; this store has 12" in out
    else:
        assert out.rstrip().endswith("preflight: PASS")


def test_miss_report_with_a_fit_block_prints_the_fit_line_and_the_landing_miss():
    record = _record_dict(candidates=("local_encoder", "anthropic"), agreement=0.80)
    record["fit"] = {
        "head_run_id": "run-9",
        "n_train": 120,
        "n_train_real": 30,
        "split": "digest",
        "landed": False,
        "miss_arms": ["local_encoder", "anthropic"],
        "miss_criteria": {"local_encoder": ["agreement"], "anthropic": ["agreement", "p95_c4"]},
    }
    report = render_report(record)
    assert "fit: n_train=120 n_train_real=30 head=run-9 landed=false" in report
    assert "landing MISS: local_encoder on agreement" in report
    assert "landing MISS: anthropic on agreement, p95_c4" in report
    assert "local_encoder: MISS on agreement" in report


def test_precheck_claim_goes_through_the_case_investigation_path(monkeypatch):
    """The ``precheck_below_bar`` claim takes the same ``open_investigation`` +
    ``record_claims`` path the run claims take."""
    from types import SimpleNamespace

    from tools.classification_eval.records import attach_precheck_claim

    seen: dict[str, object] = {}

    def open_investigation(project_key, **kw):
        seen["open"] = (project_key, kw)
        return SimpleNamespace(accepted=True, investigation_id="inv-1", reason="", message="")

    def record_claims(investigation_id, claims, sources=None):
        seen["claims"] = (investigation_id, claims)
        return len(claims)

    monkeypatch.setattr("tools.improvement_investigations.open_investigation", open_investigation)
    monkeypatch.setattr("tools.improvement_investigations.record_claims", record_claims)
    assert attach_precheck_claim("routing.terminus", 0.824, 0.95, project_key=PK) == "inv-1"
    assert seen["open"][0] == PK and seen["open"][1]["kind"] == "probe"
    investigation_id, claims = seen["claims"]
    assert investigation_id == "inv-1" and len(claims) == 1
    assert "precheck_below_bar" in claims[0]["claim"] and "0.824" in claims[0]["claim"]
    assert "gate=0.85" in claims[0]["claim"] and claims[0]["url"].endswith("/3420")


def test_cli_precheck_reports_a_leg_refusal_as_exit_1(monkeypatch, capsys):
    """Without the extra or the weights the leg refuses to embed with an
    ``LLMCallError`` naming the fix; ``--precheck`` prints it and exits 1
    instead of a traceback."""
    from agent.llm.errors import LLMCallError
    from tools.classification_eval import fit

    def refuse(**kw):
        raise LLMCallError("classification-local extra not installed", reason="transport")

    monkeypatch.setattr(fit, "precheck", refuse)
    assert main(["--precheck", "--project-key", PK]) == 1
    assert "extra not installed" in capsys.readouterr().err


async def test_encoder_embeds_and_is_measured_on_the_message_text_not_the_reference_prompt(
    fit_env, monkeypatch
):
    """Without a row ``encoder_text`` (Task 8's composition function) the
    encoder embeds the message text, never the reference prompt's instruction
    block (which would dominate a CLS embedding and truncate the message), and
    ``compare`` hands the ``local_encoder`` arm that same text."""
    from tools.classification_eval.fit import encoder_text

    embedded: list[str] = []

    def spy(text):
        embedded.append(text)
        return _fake_embed(text)

    monkeypatch.setattr(fit_env.leg, "embed", spy)
    site = _site(minimum_n=50)
    assert encoder_text(site, Input("hello?", "real")) == "hello?"
    seen: list[str] = []

    def build(name, staged):
        arm = _builder()(name, staged)

        async def call(prompt, system, output_type):
            seen.append(prompt)
            return await arm.call(prompt, system, output_type)

        return Arm(name=arm.name, backend=arm.backend, model=arm.model, price=PRICE, call=call)

    outcome = await _fit(fit_env, build=build)
    texts = {inp.text for inp in _inputs(40, 40)}
    assert embedded and set(embedded) <= texts, "the fit embedded a prompt, not the message"
    assert seen and set(seen) <= texts, "compare handed the arms a prompt, not the message"
    assert evaluate_bar(outcome.record.as_dict(), "local_encoder") == []


_INSTRUCTIONS = "Classify the draft. Reply with only the JSON object."


def _lane_a_row(**fields):
    """A row carrying lane A's instruction-bearing granite prompt and system,
    the shape ``routing.terminus``, ``agent_catchup.judge`` and
    ``promise_gate.verdict`` have in ``sites.py``."""
    from dataclasses import replace

    return replace(
        _site(minimum_n=50),
        system="reference system",
        candidate_prompt=lambda inp: f"{_INSTRUCTIONS}\n<<<\n{inp.text}\n>>>",
        candidate_system="granite system: " + _INSTRUCTIONS,
        **fields,
    )


def test_encoder_text_never_returns_a_rows_candidate_prompt():
    """``encoder_text`` reads the encoder lane's own field, so a row whose
    ``candidate_prompt`` wraps the message in instructions (lane A's granite
    prompt) is still embedded as the bare message; with ``encoder_text`` set,
    the composition is used and the instruction block still never appears."""
    from dataclasses import replace

    from tools.classification_eval.fit import encoder_text

    inp = Input("hello there?", "real", {"thread": "earlier: hi"})
    row = _lane_a_row()
    assert _INSTRUCTIONS in row.candidate_prompt(inp)
    assert encoder_text(row, inp) == "hello there?"

    composed = replace(row, encoder_text=lambda inp: f"{inp.text}\n{inp.context['thread']}")
    assert encoder_text(composed, inp) == "hello there?\nearlier: hi"
    assert _INSTRUCTIONS not in encoder_text(composed, inp)


def test_encoder_text_on_every_site_row_is_free_of_its_candidate_prompt_instructions():
    """Over the real site table: no row sets ``encoder_text`` yet, so the
    encoder embeds ``inp.text`` on every row, including the three rows whose
    ``candidate_prompt`` carries instructions; a row whose composition ever
    re-embedded its ``candidate_prompt`` fails here."""
    from tools.classification_eval.fit import encoder_text
    from tools.classification_eval.sites import SITES

    wrapped = 0
    for row in SITES.values():
        inp = row.fixtures()[0]
        text = encoder_text(row, inp)
        if row.encoder_text is None:
            assert text == inp.text, row.id
        if row.candidate_prompt is not None:
            candidate = row.candidate_prompt(inp)
            assert text != candidate, f"{row.id}: the encoder embeds lane A's candidate prompt"
            instructions = candidate.replace(inp.text, "").strip()
            assert instructions and instructions not in text, row.id
            wrapped += 1
    assert wrapped >= 3  # C2, C6, C9 carry an instruction-bearing candidate_prompt


async def test_encoder_lane_measures_both_arms_on_the_served_shape(fit_env, monkeypatch):
    """On a row with lane A's ``candidate_prompt``/``candidate_system``, the fit
    embeds the bare message, and ``compare`` hands the ``local_encoder`` arm
    that message and the ``anthropic`` arm that message with the row's
    ``encoder_system`` (falling back to the row's ``system``) as its system
    prompt and the site's own output type: the restructured fallback shape a
    landed site serves. Lane A's prompt and system never reach either arm."""
    from dataclasses import replace

    from tools.classification_eval.fit import measured_site, run_fit

    class Tighter(BaseModel):
        answer: Literal["yes", "no"]
        extra: str = ""

    embedded: list[str] = []

    def spy(text):
        embedded.append(text)
        return _fake_embed(text)

    monkeypatch.setattr(fit_env.leg, "embed", spy)
    seen: dict[str, list[tuple]] = {"local_encoder": [], "anthropic": []}

    def build(name, staged):
        arm = _builder()(name, staged)

        async def call(prompt, system, output_type):
            seen[name].append((prompt, system, output_type))
            return await arm.call(prompt, system, output_type)

        return Arm(name=arm.name, backend=arm.backend, model=arm.model, price=PRICE, call=call)

    texts = {inp.text for inp in _inputs(40, 40)}
    for row, expected_system in (
        (_lane_a_row(candidate_output_type=Tighter), "reference system"),
        (_lane_a_row(encoder_system="served instructions"), "served instructions"),
    ):
        embedded.clear()
        seen = {"local_encoder": [], "anthropic": []}
        outcome = await run_fit(
            row,
            _inputs(40, 40),
            reference=_counting_reference()[0],
            candidates=["local_encoder", "anthropic"],
            build_arm=build,
            contended=False,
            project_key=PK,
            out=lambda line: None,
        )
        assert embedded and set(embedded) <= texts
        assert all(_INSTRUCTIONS not in text for text in embedded)
        for name in ("local_encoder", "anthropic"):
            prompts = {prompt for prompt, _, _ in seen[name]}
            assert prompts and prompts <= texts, name
            assert {system for _, system, _ in seen[name]} == {expected_system}, name
            assert {output_type for _, _, output_type in seen[name]} == {Verdict}, name
        assert evaluate_bar(outcome.record.as_dict(), "local_encoder") == []

    measured = measured_site(_lane_a_row(candidate_output_type=Tighter))
    assert measured.candidate_output_type is None and measured.candidate_system is None
    assert measured.candidate_prompt(Input("x?", "real")) == "x?"
    assert measured_site(replace(_lane_a_row(), encoder_system="s")).candidate_system == "s"
