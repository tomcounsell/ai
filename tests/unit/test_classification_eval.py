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


def _arm(name: str, backend: str, *, flip_every: int = 0, error_every: int = 0) -> Arm:
    """A fake arm: answers the truth, disagreeing every ``flip_every`` calls
    and raising every ``error_every`` calls (counted over all calls)."""
    seen = {"calls": 0}

    async def call(prompt: str, system: str | None, output_type: type[BaseModel]):
        seen["calls"] += 1
        n = seen["calls"]
        if error_every and n % error_every == 0:
            raise RuntimeError("arm exploded")
        truth = _truth(prompt)
        if flip_every and n % flip_every == 0:
            truth = Verdict(answer="no" if truth.answer == "yes" else "yes")
        return truth, 0.001

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
        (Backend.OLLAMA, {"contended": True}, 1, "contended"),
        (Backend.OLLAMA, {"n_real": 50}, 1, "n_real"),
        (Backend.OLLAMA, {"agreement": 0.9}, 1, "agreement"),
        (Backend.OLLAMA, {}, 0, "PASS"),
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


def test_parse_candidates_rejects_an_unknown_backend():
    with pytest.raises(SystemExit):
        parse_candidates(["gliclass"])


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
