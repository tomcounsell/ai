"""Reports, verdicts, challenged assumptions, and the deadline sweep.
Plan 02 task 10."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

import kernel.tree as tree
from kernel.events import read_for
from schemas.objective import Assumption
from tests.conftest import requires_postgres
from tests.tree_fakes import (
    approval,
    bind,
    contract,
    delta,
    executor_report,
    make_space,
    receipt,
    request,
    scribe_report,
    terminal,
    token,
    verdict,
)

pytestmark = requires_postgres


@pytest.fixture
def bound(space, monkeypatch):
    return bind(monkeypatch, {space: make_space(space, max_effect_class="act")})


async def running(kernel, space, **over):
    oid = await tree.open_objective(
        kernel, space=space, conversation_id="c1", contract=contract(**over)
    )
    await tree.approve(kernel, oid, approval("approved", oid, 1))
    executor = await tree.delegate(
        kernel,
        request(oid, "Executor", space=space, budget=1000),
        issuer=frozenset(),
        parent_brief="turn-1",
    )
    return oid, executor


async def verifying(kernel, space):
    oid, executor = await running(kernel, space)
    await tree.land_report(kernel, token(executor), terminal(report=executor_report()))
    verifier = await tree.delegate(
        kernel,
        request(oid, "Verifier", space=space, budget=1000, effect_class="read"),
        issuer=frozenset(),
        parent_brief="turn-1",
    )
    return oid, verifier


async def events_of(kernel, space, oid, type=None):
    events = await read_for(kernel, space_id=space, key="objective_id", value=oid)
    return [e for e in events if type is None or e.type == type]


async def test_an_executor_report_moves_the_node_to_verifying(kernel, space, bound):
    oid, executor = await running(kernel, space)
    await tree.land_report(kernel, token(executor), terminal(report=executor_report()))
    node = await tree.project(kernel, oid)
    assert (node.state, node.state_reason) == ("VERIFYING", "verifying")
    (landed,) = await events_of(kernel, space, oid, "report.landed")
    assert [(r.brief_id, r.event_id, r.summary) for r in node.reports] == [
        (executor.id, landed.id, "done")
    ]
    assert landed.payload["objective_id"] == oid
    assert len(node.evidence) == 1
    assert node.owner_brief is None
    with pytest.raises(tree.NotLive):
        await tree.land_report(
            kernel, token(executor), terminal(report=executor_report())
        )
    await kernel.commit()


@pytest.mark.parametrize(
    "outcome, state, reason",
    [
        ("pass", "SUCCEEDED", "verified"),
        ("fail", "FAILED", "verification_failed"),
        ("abstain", "AWAITING_APPROVAL", "awaiting_decision"),
    ],
)
async def test_a_verdict_transitions_the_node(
    kernel, space, bound, outcome, state, reason
):
    oid, verifier = await verifying(kernel, space)
    await tree.land_report(kernel, token(verifier), terminal(report=verdict(outcome)))
    node = await tree.project(kernel, oid)
    assert (node.state, node.state_reason) == (state, reason)
    assert [r.brief_id for r in node.reports][-1] == verifier.id
    await kernel.commit()


async def test_a_failed_verdict_can_be_approved_and_run_again(kernel, space, bound):
    oid, verifier = await verifying(kernel, space)
    await tree.land_report(kernel, token(verifier), terminal(report=verdict("fail")))
    await tree.approve(kernel, oid, approval("approved", oid, 1))
    node = await tree.project(kernel, oid)
    assert node.state == "APPROVED"
    assert len(node.reports) == 2
    again = await tree.delegate(
        kernel,
        request(oid, "Executor", space=space, budget=1000),
        issuer=frozenset(),
        parent_brief="turn-2",
    )
    assert again.generation == node.generation == 1
    assert (await tree.project(kernel, oid)).state == "RUNNING"
    await kernel.commit()


async def test_the_wrong_report_shape_is_refused(kernel, space, bound):
    oid, executor = await running(kernel, space)
    with pytest.raises(tree.Refused):
        await tree.land_report(kernel, token(executor), terminal(report=verdict()))
    with pytest.raises(tree.Refused):
        await tree.land_report(kernel, token(executor), terminal(outcome="aborted"))
    assert await events_of(kernel, space, oid, "report.landed") == []
    await kernel.commit()


async def test_a_challenged_assumption_stops_the_node_for_a_decision(
    kernel, space, bound
):
    issuer, _ = bound
    oid, executor = await running(
        kernel,
        space,
        assumptions=[
            Assumption(statement="tests exist", falsification_test="ls tests"),
            Assumption(statement="CI is green", falsification_test="gh run list"),
        ],
    )
    scribe = await tree.delegate(
        kernel,
        request(oid, "Scribe", space=space, budget=100),
        issuer=frozenset(),
        parent_brief="turn-1",
    )
    report = executor_report(
        deltas=[delta("tests exist", "supported"), delta("CI is green", "refuted")]
    )
    await tree.land_report(kernel, token(executor), terminal(report=report))
    node = await tree.project(kernel, oid)
    assert (node.state, node.state_reason) == (
        "AWAITING_APPROVAL",
        "challenged_assumption",
    )
    (challenged,) = await events_of(kernel, space, oid, "assumption.challenged")
    assert challenged.payload["ancestor_id"] == oid
    assert challenged.payload["statement"] == "CI is green"
    assert challenged.payload["evidence"][0]["kind"] == "test_output"
    stopped = await events_of(kernel, space, oid, "brief.stopped")
    assert [e.payload["brief_id"] for e in stopped] == [scribe.id]
    assert issuer.revoked == [(kernel, scribe.id)]
    assert node.generation == 2
    assert len(node.reports) == 1
    # the stop that fenced the Scribe fenced the node, so the old token is stale
    with pytest.raises(tree.StaleGeneration):
        await tree.land_report(kernel, token(executor), terminal(report=report))
    await kernel.commit()


async def test_a_failure_after_the_first_event_leaves_no_row(
    kernel, space, bound, monkeypatch
):
    oid, executor = await running(kernel, space)
    await kernel.commit()
    calls = 0
    real = tree.append

    async def one_then_fail(conn, **kw):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("forced after the first event")
        return await real(conn, **kw)

    monkeypatch.setattr(tree, "append", one_then_fail)
    with pytest.raises(RuntimeError):
        await tree.land_report(
            kernel,
            token(executor),
            terminal(report=executor_report([delta("tests exist")])),
        )
    assert calls == 2
    await kernel.rollback()
    for type in ("report.landed", "assumption.challenged"):
        assert await events_of(kernel, space, oid, type) == []
    changes = await events_of(kernel, space, oid, "objective.state_changed")
    assert [e.payload["to"] for e in changes] == ["APPROVED", "RUNNING"]
    assert (await tree.project(kernel, oid)).state == "RUNNING"


async def test_a_delta_on_an_unknown_statement_is_refused(kernel, space, bound):
    oid, executor = await running(kernel, space)
    with pytest.raises(tree.Refused):
        await tree.land_report(
            kernel,
            token(executor),
            terminal(report=executor_report([delta("the moon")])),
        )
    assert await events_of(kernel, space, oid, "report.landed") == []
    assert (await tree.project(kernel, oid)).state == "RUNNING"
    await kernel.commit()


async def test_an_abort_after_a_stop_writes_nothing(kernel, space, bound):
    oid, executor = await running(kernel, space)
    await tree.stop(kernel, oid, "stopped_by_person")
    before = await events_of(kernel, space, oid)
    await tree.land_report(kernel, token(executor), terminal(outcome="aborted"))
    assert await events_of(kernel, space, oid) == before
    with pytest.raises(tree.StaleGeneration):
        await tree.land_report(
            kernel, token(executor), terminal(report=executor_report())
        )
    with pytest.raises(tree.StaleGeneration):
        await tree.land_report(kernel, token(executor), terminal(outcome="failed"))
    await kernel.commit()


async def test_a_failed_terminal(kernel, space, bound):
    oid, executor = await running(kernel, space)
    await tree.land_report(
        kernel, token(executor), terminal(outcome="failed", error="oom")
    )
    node = await tree.project(kernel, oid)
    assert (node.state, node.state_reason) == ("FAILED", "worker_failed")
    (failed,) = await events_of(kernel, space, oid, "brief.failed")
    assert failed.payload == {
        "brief_id": executor.id,
        "objective_id": oid,
        "error": "oom",
    }
    assert node.generation == 1  # the failed Brief is closed, not stopped
    oid, verifier = await verifying(kernel, space)
    await tree.land_report(
        kernel, token(verifier), terminal(outcome="failed", error="x")
    )
    node = await tree.project(kernel, oid)
    assert (node.state, node.state_reason) == ("FAILED", "worker_failed")
    await kernel.commit()


async def test_a_scribe_lands_with_and_without_a_node(kernel, space, bound):
    oid, executor = await running(kernel, space)
    scribe = await tree.delegate(
        kernel,
        request(oid, "Scribe", space=space, budget=100),
        issuer=frozenset(),
        parent_brief="turn-1",
    )
    await tree.land_report(kernel, token(scribe), terminal(report=scribe_report()))
    node = await tree.project(kernel, oid)
    assert node.state == "RUNNING"
    assert [r.brief_id for r in node.reports] == [scribe.id]
    root = await tree.delegate(
        kernel,
        request(None, "Scribe", space=space, budget=100, max_data_class="OPERATOR"),
        issuer=frozenset(),
        parent_brief="turn-3",
    )
    await tree.land_report(kernel, token(root), terminal(report=scribe_report()))
    (landed,) = [
        e
        for e in await read_for(kernel, space_id=space, key="brief_id", value=root.id)
        if e.type == "report.landed"
    ]
    assert landed.payload["objective_id"] is None
    assert landed.payload["report"] == {"written": [], "proposed": [], "could_not": []}
    with pytest.raises(tree.NotLive):
        await tree.stop_brief(kernel, root.id, "stopped_by_person")
    failing = await tree.delegate(
        kernel,
        request(None, "Scribe", space=space, budget=100, max_data_class="OPERATOR"),
        issuer=frozenset(),
        parent_brief="turn-3",
    )
    await tree.land_report(
        kernel, token(failing), terminal(outcome="failed", error="e")
    )
    (failed,) = [
        e
        for e in await read_for(
            kernel, space_id=space, key="brief_id", value=failing.id
        )
        if e.type == "brief.failed"
    ]
    assert failed.payload["objective_id"] is None
    await kernel.commit()


async def test_expire_due_cancels_a_node_past_its_deadline(kernel, space, bound):
    issuer, _ = bound
    oid, executor = await running(
        kernel, space, deadline=datetime.now(UTC) + timedelta(milliseconds=500)
    )
    fresh, fresh_executor = await running(kernel, space)
    # the sweep is over the whole database, so other tests' nodes may be due
    assert oid not in await tree.expire_due(kernel)
    await asyncio.sleep(1.0)
    assert oid in await tree.expire_due(kernel)
    node = await tree.project(kernel, oid)
    assert (node.state, node.state_reason, node.generation) == (
        "CANCELLED",
        "deadline",
        2,
    )
    (stopped,) = await events_of(kernel, space, oid, "brief.stopped")
    assert stopped.payload["brief_id"] == executor.id
    # the sweep revokes every due node's brief, including leftovers other
    # runs committed, so the issuer is checked for this test's briefs only
    assert issuer.revoked.count((kernel, executor.id)) == 1
    assert fresh_executor.id not in [brief_id for _, brief_id in issuer.revoked]
    assert (await tree.project(kernel, fresh)).state == "RUNNING"
    assert oid not in await tree.expire_due(kernel)
    with pytest.raises(tree.StaleGeneration):
        await tree.land_report(
            kernel, token(executor), terminal(report=executor_report())
        )
    await tree.confirm_stop(kernel, executor.id, receipt())
    await kernel.commit()
