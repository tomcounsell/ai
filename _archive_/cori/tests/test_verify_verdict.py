"""`record_verdict` records only; a failed check is a kernel verdict. Plan 11
task 9 and Properties; seams §3.6, §4, §6; Round two, verifier rulings.

The real tree, events, and store with the tree's collaborators faked as the
tree tests fake them; `run_checks` is replaced by a recorder that writes the
drawn checks as rows and the event, so the property runs without a sandbox.
"""

import asyncio
import uuid

import psycopg
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import kernel.tree as tree
from kernel import runs, verify
from kernel.events import read_for
from schemas.report import CriterionResult, Verdict
from tests.conftest import dsn, requires_postgres
from tests.tree_fakes import (
    approval,
    bind,
    contract,
    executor_report,
    fake_root_capabilities,
    force_state,
    make_space,
    request,
    snapshot,
    terminal,
    token,
)
from workers.verifier import load_prompt

pytestmark = requires_postgres

DB_SETTINGS = dict(
    deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
CRITERIA = ["pytest green", "the cap holds", "negative attempts raise"]


def connect():
    return psycopg.AsyncConnection.connect(dsn("kernel_rw"))


def run(coro):
    return asyncio.run(coro)


def wire(mp, space):
    """Bind the tree's and the verifier's collaborators for one space."""
    spaces = {space: make_space(space, max_effect_class="propose")}
    bind(mp, spaces)
    mp.setattr(verify, "load_all", lambda: spaces)
    mp.setattr(verify, "root_capabilities", fake_root_capabilities)
    mp.setattr(runs, "connect", connect)
    mp.setattr(runs, "sandbox", object())
    mp.setattr(runs, "worker", object())
    mp.setattr(runs, "door", object())
    return spaces


@pytest.fixture
def bound(space, monkeypatch):
    return wire(monkeypatch, space)


async def verifying(conn, space, criteria=CRITERIA):
    """A node at VERIFYING with one Executor report landed and released."""
    c = contract(budget=5_000_000)
    c = c.model_copy(update={"success_criteria": list(criteria)})
    oid = await tree.open_objective(conn, space=space, conversation_id="c1", contract=c)
    await tree.approve(conn, oid, approval("approved", oid, 1))
    executor = await tree.delegate(
        conn,
        request(oid, "Executor", space=space, budget=1_000_000),
        issuer=frozenset(),
        parent_brief="turn-1",
    )
    await tree.land_report(conn, token(executor), terminal(report=executor_report()))
    await tree.release(conn, executor.id)
    await conn.commit()
    return oid, executor


async def verifier_for(conn, oid, space):
    return await tree.delegate(
        conn,
        request(oid, "Verifier", space=space, budget=100_000, effect_class="read"),
        issuer=frozenset(),
        parent_brief=None,
    )


async def verdict_rows(conn, oid):
    cur = await conn.execute(
        "SELECT brief_id, verifier_brief_id, model_ref, prompt_sha256, outcome, "
        "predicted_failure, criteria, scope_findings, summary, sampled_with "
        "FROM verdicts WHERE objective_id = %s ORDER BY id",
        (oid,),
    )
    return await cur.fetchall()


async def events_of(conn, space, oid, type_=None):
    events = await read_for(conn, space_id=space, key="objective_id", value=oid)
    return [e for e in events if type_ is None or e.type == type_]


def model_verdict(outcome="pass", criteria=CRITERIA):
    met = {"pass": True, "fail": False, "abstain": None}[outcome]
    return Verdict(
        outcome=outcome,
        predicted_failure=0.2,
        criteria=[
            CriterionResult(criterion=c, met=met, reason="judged") for c in criteria
        ],
        scope_findings=["touched the README"],
        summary="looked at it",
    )


# --- examples ----------------------------------------------------------------


async def test_kernel_verdict_row_has_null_verifier_brief_and_model_ref_kernel(
    space, bound
):
    async with await connect() as conn:
        oid, executor = await verifying(conn, space)
        objective = await tree.project(conn, oid)
        checks = [
            verify.check_result("build", True, "ok"),
            verify.check_result("tests", False, "1 failed"),
        ]
        verdict = verify.kernel_verdict(objective, checks)
        sha = load_prompt("code")[1]
        await verify.record_verdict(
            conn,
            None,
            verdict,
            1.0,
            model_ref="kernel",
            prompt_sha256=sha,
            objective_id=oid,
        )
        await conn.commit()
        (row,) = await verdict_rows(conn, oid)
    assert row[0] == executor.id
    assert row[1] is None
    assert row[2] == "kernel"
    assert row[3] == sha
    assert row[4] == "fail" and row[5] == 1.0 and row[9] == 1.0
    assert all(c["met"] is False for c in row[6])
    assert "check tests failed" in row[8]


def test_kernel_verdict_passes_the_verdict_validator(space, bound):
    async def objective():
        async with await connect() as conn:
            oid, _ = await verifying(conn, space)
            return await tree.project(conn, oid)

    obj = run(objective())
    checks = [
        verify.check_result("citations_resolve", False, "3. x: unresolved"),
        verify.check_result("length", False, "50000 characters, cap 40000"),
    ]
    verdict = verify.kernel_verdict(obj, checks)
    again = Verdict.model_validate(verdict.model_dump())
    assert again.outcome == "fail" and again.predicted_failure == 1.0
    assert [c.criterion for c in again.criteria] == CRITERIA
    assert all(c.met is False for c in again.criteria)
    assert all(
        "citations_resolve" in c.reason and "length" in c.reason for c in again.criteria
    )
    assert "check citations_resolve failed" in again.summary


async def test_payload_brief_id_is_the_executor_brief(space, bound):
    async with await connect() as conn:
        oid, executor = await verifying(conn, space)
        verifier = await verifier_for(conn, oid, space)
        await conn.commit()
        sha = load_prompt("code")[1]
        await verify.record_verdict(
            conn,
            verifier,
            model_verdict(),
            1.0,
            model_ref=verifier.model_ref,
            prompt_sha256=sha,
        )
        await conn.commit()
        (row,) = await verdict_rows(conn, oid)
        (recorded,) = await events_of(conn, space, oid, "verdict.recorded")
    assert row[0] == executor.id and row[1] == verifier.id
    assert row[2] == verifier.model_ref
    assert recorded.payload["brief_id"] == executor.id
    assert recorded.payload["verifier_brief_id"] == verifier.id
    assert recorded.payload["model_ref"] == verifier.model_ref
    assert recorded.payload["sampled_with"] == 1.0
    assert recorded.payload["verdict"]["outcome"] == "pass"
    assert recorded.payload["prompt_sha256"] == sha


# --- properties --------------------------------------------------------------

STARTS = [
    ("VERIFYING", None),
    ("SUCCEEDED", "verified"),
    ("FAILED", "verification_failed"),
    ("AWAITING_APPROVAL", "awaiting_decision"),
    ("CANCELLED", "stopped_by_person"),
]


@given(
    outcome=st.sampled_from(["pass", "fail", "abstain"]),
    start=st.sampled_from(STARTS),
    by_kernel=st.booleans(),
)
@settings(max_examples=25, **DB_SETTINGS)
def test_record_verdict_writes_row_and_event_and_no_transition(
    outcome, start, by_kernel
):
    space = f"space-{uuid.uuid4().hex[:8]}"

    async def go():
        async with await connect() as conn:
            oid, _ = await verifying(conn, space)
            state, reason = start
            if reason is not None:
                await force_state(conn, oid, state, reason, by="land_report")
                await conn.commit()
            before = await tree.project(conn, oid)
            rows_before = len(await verdict_rows(conn, oid))
            events_before = len(await events_of(conn, space, oid))
            if by_kernel:
                await verify.record_verdict(
                    conn,
                    None,
                    model_verdict(outcome),
                    0.7,
                    model_ref="kernel",
                    prompt_sha256="0" * 64,
                    objective_id=oid,
                )
            else:
                verifier = (
                    await verifier_for(conn, oid, space)
                    if before.state == "VERIFYING"
                    else None
                )
                await verify.record_verdict(
                    conn,
                    verifier,
                    model_verdict(outcome),
                    0.7,
                    model_ref="claude-opus-4-8",
                    prompt_sha256="0" * 64,
                    objective_id=oid,
                )
            await conn.commit()
            after = await tree.project(conn, oid)
            rows_after = await verdict_rows(conn, oid)
            events_after = await events_of(conn, space, oid)
            return before, after, rows_before, rows_after, events_before, events_after

    with pytest.MonkeyPatch.context() as mp:
        wire(mp, space)
        before, after, rows_before, rows_after, events_before, events_after = run(go())
    assert (after.state, after.state_reason) == (before.state, before.state_reason)
    assert len(rows_after) == rows_before + 1
    assert rows_after[-1][4] == outcome
    added = [e for e in events_after[events_before:]]
    verdict_events = [e for e in added if e.type == "verdict.recorded"]
    assert len(verdict_events) == 1
    assert not [e for e in added if e.type == "objective.state_changed"]


@st.composite
def failing_check_lists(draw):
    names = draw(
        st.lists(st.sampled_from(["build", "tests", "length"]), min_size=1, max_size=4)
    )
    passed = draw(st.lists(st.booleans(), min_size=len(names), max_size=len(names)))
    passed[draw(st.integers(min_value=0, max_value=len(names) - 1))] = False
    return [verify.check_result(n, p, f"{n} output") for n, p in zip(names, passed)]


@given(checks=failing_check_lists(), n_criteria=st.integers(min_value=1, max_value=6))
@settings(max_examples=25, **DB_SETTINGS)
def test_failed_check_records_kernel_verdict_transitions_failed_and_no_brief(
    checks, n_criteria
):
    space = f"space-{uuid.uuid4().hex[:8]}"
    criteria = [f"criterion {i}" for i in range(n_criteria)]

    async def fake_run_checks(objective, snap):
        async with await connect() as conn:
            await verify._record_checks(
                conn, objective, objective.reports[-1].brief_id, checks, {}
            )
            await conn.commit()
        return checks

    async def go():
        async with await connect() as conn:
            oid, executor = await verifying(conn, space, criteria)
        verdict = await verify.verify_objective(oid, snapshot())
        async with await connect() as conn:
            node = await tree.project(conn, oid)
            rows = await verdict_rows(conn, oid)
            events = await events_of(conn, space, oid)
        return executor, verdict, node, rows, events

    with pytest.MonkeyPatch.context() as mp:
        wire(mp, space)
        mp.setattr(verify, "run_checks", fake_run_checks)
        executor, verdict, node, rows, events = run(go())
    failed_names = {c.name for c in checks if not c.passed}
    assert verdict is not None and verdict.outcome == "fail"
    assert [c.criterion for c in verdict.criteria] == criteria
    assert all(c.met is False for c in verdict.criteria)
    assert all(any(n in c.reason for n in failed_names) for c in verdict.criteria)
    Verdict.model_validate(verdict.model_dump())
    (row,) = rows
    assert row[0] == executor.id and row[1] is None and row[2] == "kernel"
    assert row[3] == load_prompt("code")[1]
    assert (node.state, node.state_reason) == ("FAILED", "verification_failed")
    issued = [
        e
        for e in events
        if e.type == "brief.issued" and e.payload["brief"]["agent_class"] == "Verifier"
    ]
    assert issued == []
    types = [e.type for e in events]
    assert types.index("verification.sampled") < types.index("checks.recorded")
    assert types.index("checks.recorded") < types.index("verdict.recorded")
    assert types.index("verdict.recorded") < len(types) - 1
    assert events[-1].type == "objective.state_changed"
    assert events[-1].payload["reason"] == "verification_failed"
