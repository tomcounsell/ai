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
) -> dict:
    def arm(name):
        return {
            "name": name,
            "backend": name,
            "model": f"{name}-model",
            "calls": n * 2,
            "errors": int(error_rate * n * 2),
            "error_rate": error_rate,
            "p50_c1": 0.5,
            "p95_c1": 0.9,
            "p50_c4": 0.7,
            "p95_c4": p95_c4,
            "cost_per_call_usd": 0.0,
            "cost_metering": "local",
            "price": {},
            "agreement": None
            if latency_only
            else {"mean": agreement, "lower": agreement - 0.03, "upper": 1.0, "n": n},
        }

    reference = None if latency_only else dict(arm("anthropic"), p95_c4=reference_p95_c4)
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
        "run_id": "run-1",
        "created_at": "2026-09-19T00:00:00+00:00",
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
    row = ImprovementEvidence.record_once(
        PK,
        EVIDENCE_KIND,
        source_ref=f"classification_eval:{record['site']}:{record['run_id']}",
        text=record["site"],
        detail=json.dumps(record),
    )
    assert row is not None
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
    ],
)
def test_parse_candidates_accepts_repeats_and_comma_lists(raw, expected):
    assert parse_candidates(raw) == expected


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
