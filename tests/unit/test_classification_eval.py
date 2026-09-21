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


# --- the fit path: run_fit, the landing gate, the audit's head provenance -------------


@pytest.fixture
def fit_env(monkeypatch, tmp_path):
    """The fit path with no network and no ONNX: the leg's ``_embed`` is the
    deterministic fake, the staged and served head directories are temp
    dirs, and case claims are recorded on a list instead of the substrate."""
    from types import SimpleNamespace

    leg = pytest.importorskip("agent.llm.backends.local_encoder")
    from tools.classification_eval import fit, records

    served_dir = tmp_path / "served"
    monkeypatch.setattr(leg, "_embed", _fake_embed)
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
    return SimpleNamespace(leg=leg, fit=fit, served_dir=served_dir, claims=claims)


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
    the staged head (a scripted flipper when ``encoder_flip``), and a fake
    ``anthropic`` arm that misses the bar when ``anthropic_flip``."""
    from tools.classification_eval.arms import local_encoder_arm

    def build(name, staged):
        if name == "anthropic":
            return _arm("anthropic", "anthropic", flip_every=anthropic_flip)
        if encoder_flip:
            return _arm("local_encoder", "local_encoder", flip_every=encoder_flip)
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
    local_encoder`` exits 2 before the reference arm is built."""
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
