"""`validate_verdict_terminal`: a Verdict whose criterion strings are not
the contract's lands as an abstain that says why. Plan 11 task 10 and
Properties; seams Round two §3.5; the §1.6 validator."""

import asyncio
import uuid

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import kernel.tree as tree
from kernel import verify
from schemas.report import CriterionResult, Report, Verdict
from schemas.trace import Terminal
from tests.conftest import requires_postgres
from tests.test_verify_verdict import (
    CRITERIA,
    connect,
    model_verdict,
    verdict_rows,
    verifier_for,
    verifying,
    wire,
)
from tests.tree_fakes import terminal, token
from workers.verifier import derive_outcome, load_prompt

pytestmark = requires_postgres

DB_SETTINGS = dict(
    deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def bound(space, monkeypatch):
    return wire(monkeypatch, space)


async def brief_at_verifying(space, criteria=CRITERIA):
    async with await connect() as conn:
        oid, executor = await verifying(conn, space, criteria)
        verifier = await verifier_for(conn, oid, space)
        await conn.commit()
    return oid, executor, verifier


async def test_matching_terminal_is_returned_unchanged(space, bound):
    _, _, verifier = await brief_at_verifying(space)
    for outcome in ("pass", "fail", "abstain"):
        t = terminal(report=model_verdict(outcome))
        assert await verify.validate_verdict_terminal(verifier, t) is t
    reordered = terminal(report=model_verdict("pass", list(reversed(CRITERIA))))
    assert await verify.validate_verdict_terminal(verifier, reordered) is reordered


async def test_mismatched_terminal_becomes_abstain_with_problems_first(space, bound):
    _, _, verifier = await brief_at_verifying(space)
    wrong = Verdict(
        outcome="fail",
        predicted_failure=0.9,
        criteria=[
            CriterionResult(criterion="pytest green", met=True, reason="ran"),
            CriterionResult(criterion="the cap holds", met=False, reason="no"),
            CriterionResult(criterion="something else", met=True, reason="?"),
        ],
        scope_findings=["a finding"],
        summary="the model's own words",
    )
    out = await verify.validate_verdict_terminal(verifier, terminal(report=wrong))
    assert out.outcome == "report"
    v = out.report
    assert v.outcome == "abstain"
    assert v.predicted_failure == 0.9
    assert v.scope_findings == ["a finding"]
    assert [c.criterion for c in v.criteria] == CRITERIA
    assert all(c.met is None and c.reason == verify.NOT_JUDGED for c in v.criteria)
    problems, _, rest = v.summary.partition("\n\n")
    assert set(problems.split("\n")) == {
        "criterion not judged: 'negative attempts raise'",
        "criterion not in the contract: 'something else'",
    }
    assert rest == "the model's own words"
    Verdict.model_validate(v.model_dump())


async def test_mismatched_terminal_lands_as_awaiting_decision(space, bound):
    oid, executor, verifier = await brief_at_verifying(space)
    wrong = model_verdict("pass", ["not", "the", "contract's"])

    async def stub_run_brief(brief, t):
        validated = await verify.validate_verdict_terminal(brief, t)
        async with await connect() as conn:
            await tree.land_report(conn, token(brief), validated)
            await tree.release(conn, brief.id)
            await conn.commit()
        return validated

    landed = await stub_run_brief(verifier, terminal(report=wrong))
    async with await connect() as conn:
        node = await tree.project(conn, oid)
        assert (node.state, node.state_reason) == (
            "AWAITING_APPROVAL",
            "awaiting_decision",
        )
        await verify.record_verdict(
            conn,
            verifier,
            landed.report,
            1.0,
            model_ref=verifier.model_ref,
            prompt_sha256=load_prompt("code")[1],
        )
        await conn.commit()
        (row,) = await verdict_rows(conn, oid)
    assert row[0] == executor.id and row[1] == verifier.id
    assert row[4] == "abstain"
    assert row[8].startswith("criterion not judged: 'pytest green'")


async def test_non_verdict_terminals_pass_through(space, bound):
    _, executor, verifier = await brief_at_verifying(space)
    failed = Terminal(outcome="failed", report=None, error="boom")
    aborted = Terminal(outcome="aborted", report=None, error=None)
    report = terminal(
        report=Report(artifact_refs=[], evidence=[], assumption_deltas=[], summary="s")
    )
    for t in (failed, aborted, report):
        assert await verify.validate_verdict_terminal(verifier, t) is t
        assert await verify.validate_verdict_terminal(executor, t) is t


# Criterion strings a stored contract can hold: Postgres jsonb refuses NUL
# (`\u0000 cannot be converted to text`), and `st.text` already leaves out
# the surrogates.
text = st.text(
    alphabet=st.characters(exclude_characters="\x00"), min_size=1, max_size=20
).filter(str.strip)


@st.composite
def contracts_and_verdicts(draw):
    contract = draw(st.lists(text, min_size=1, max_size=4, unique=True))
    mode = draw(
        st.sampled_from(["same", "shuffled", "subset", "extra", "dup", "other"])
    )
    if mode == "same":
        named = list(contract)
    elif mode == "shuffled":
        named = draw(st.permutations(contract))
    elif mode == "subset":
        named = contract[: max(1, len(contract) - 1)]
    elif mode == "extra":
        named = contract + [draw(text.filter(lambda s: s not in contract))]
    elif mode == "dup":
        named = contract + [contract[0]]
    else:
        named = draw(st.lists(text, min_size=1, max_size=4, unique=True))
    mets = draw(
        st.lists(
            st.sampled_from([True, False, None]),
            min_size=len(named),
            max_size=len(named),
        )
    )
    criteria = [
        CriterionResult(criterion=c, met=m, reason="r") for c, m in zip(named, mets)
    ]
    verdict = Verdict(
        outcome=derive_outcome(criteria),
        predicted_failure=draw(st.floats(min_value=0, max_value=1)),
        criteria=criteria,
        scope_findings=draw(st.lists(text, max_size=2)),
        summary=draw(st.text(max_size=2000)),
    )
    return contract, verdict


@given(contracts_and_verdicts())
@settings(max_examples=25, **DB_SETTINGS)
def test_validated_terminal_always_satisfies_the_verdict_validator(drawn):
    contract, verdict = drawn
    space = f"space-{uuid.uuid4().hex[:8]}"

    async def go():
        _, _, verifier = await brief_at_verifying(space, contract)
        t = terminal(report=verdict)
        return t, await verify.validate_verdict_terminal(verifier, t)

    with pytest.MonkeyPatch.context() as mp:
        wire(mp, space)
        t, out = run(go())
    Verdict.model_validate(out.report.model_dump())
    assert sorted(c.criterion for c in out.report.criteria) == sorted(contract)
    matched = sorted(c.criterion for c in verdict.criteria) == sorted(contract)
    assert (out is t) == matched
    if not matched:
        assert out.report.outcome == "abstain"
        assert out.report.predicted_failure == verdict.predicted_failure
        assert len(out.report.summary) <= 2000
        assert out.report.summary.startswith("criterion ")
