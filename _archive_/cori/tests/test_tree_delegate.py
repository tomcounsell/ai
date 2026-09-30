"""One Brief per delegation: attenuated, funded, seated, profiled, fenced,
tokened. Plan 02 task 8."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

import kernel.tree as tree
from infra.models import load as load_models
from kernel.events import read_for
from schemas.brief import DelegateRequest
from schemas.budget import Budget
from schemas.ids import new_id
from tests.conftest import requires_postgres
from tests.tree_fakes import (
    FakeProfileFor,
    RecordingIssuer,
    approval,
    bind,
    block,
    contract,
    force_state,
    make_space,
    request,
)

pytestmark = requires_postgres

NO_ISSUER = frozenset()


@pytest.fixture
def bound(space, monkeypatch):
    spaces = {space: make_space(space, max_effect_class="act")}
    issuer, profile_for = bind(monkeypatch, spaces)
    return issuer, profile_for


async def opened(kernel, space, *, approve=True, **over):
    oid = await tree.open_objective(
        kernel, space=space, conversation_id="c1", contract=contract(**over)
    )
    if approve:
        await tree.approve(kernel, oid, approval("approved", oid, 1))
    return oid


async def delegate(kernel, req, parent_brief="turn-1", **kw):
    return await tree.delegate(
        kernel, req, issuer=NO_ISSUER, parent_brief=parent_brief, **kw
    )


async def ledger(kernel, space):
    cur = await kernel.execute(
        "SELECT node_id, brief_id, kind, usd_micros FROM budget_ledger "
        "WHERE space_id = %s ORDER BY id",
        (space,),
    )
    return await cur.fetchall()


async def test_executor_refused_on_framed_issued_on_approved(kernel, space, bound):
    oid = await opened(kernel, space, approve=False)
    with pytest.raises(tree.NotLive):
        await delegate(kernel, request(oid, "Executor", space=space))
    await tree.approve(kernel, oid, approval("approved", oid, 1))
    brief = await delegate(kernel, request(oid, "Executor", space=space))
    node = await tree.project(kernel, oid)
    assert (node.state, node.state_reason, node.owner_brief) == (
        "RUNNING",
        "running",
        brief.id,
    )
    with pytest.raises(tree.AlreadyOwned):
        await delegate(kernel, request(oid, "Executor", space=space))
    events = await read_for(kernel, space_id=space, key="objective_id", value=oid)
    changed = [e for e in events if e.type == "objective.state_changed"]
    assert [e.payload["to"] for e in changed] == ["APPROVED", "RUNNING"]
    assert changed[-1].payload["reason"] == "running"
    await kernel.commit()


async def test_class_rows_refuse_names_classes_and_profiles(kernel, space, bound):
    oid = await opened(kernel, space)
    for bad in (
        request(oid, "Scribe", space=space, names=("read", "write")),
        request(oid, "Scribe", space=space, effect_class="act"),
        request(oid, "Scribe", space=space, sandbox_profile="worktree"),
        request(oid, "Planner", space=space),
        request(oid, "Executor", space=space, effect_class="act"),
        request(oid, "Executor", space=space, max_data_class="OPERATOR"),
    ):
        with pytest.raises(tree.Refused):
            await delegate(kernel, bad)
    await force_state(kernel, oid, "RUNNING", "running", by="delegate")
    await force_state(kernel, oid, "VERIFYING", "verifying", by="land_report")
    with pytest.raises(tree.Refused):
        await delegate(kernel, request(oid, "Verifier", space=space, names=("ask",)))
    with pytest.raises(tree.Refused):
        await delegate(
            kernel,
            request(oid, "Verifier", space=space, effect_class="propose"),
        )
    with pytest.raises(tree.NotLive):
        await delegate(kernel, request(oid, "Executor", space=space))
    assert await ledger(kernel, space) == []
    await kernel.commit()


async def test_verify_carries_its_snapshot_and_the_schema_demands_it(
    kernel, space, bound
):
    _, profile_for = bound
    oid = await opened(kernel, space)
    await force_state(kernel, oid, "RUNNING", "running", by="delegate")
    await force_state(kernel, oid, "VERIFYING", "verifying", by="land_report")
    req = request(oid, "Verifier", space=space, effect_class="read")
    brief = await delegate(kernel, req)
    (call,) = profile_for.calls
    assert call["snapshot"] is req.snapshot
    assert (call["name"], call["key"], call["root"]) == ("verify", brief.id, "/repo")
    assert brief.model_ref == load_models().model("verifier").id
    with pytest.raises(ValueError):
        DelegateRequest(
            objective_id=oid,
            agent_class="Verifier",
            budget=req.budget,
            capabilities=req.capabilities,
            sandbox_profile="verify",
            report_schema="Verdict",
            max_data_class="PROJECT",
            context_slice=req.context_slice,
            snapshot=None,
        )
    await kernel.commit()


async def test_slice_checks(kernel, space, bound):
    oid = await opened(kernel, space)
    with pytest.raises(tree.Refused):
        await delegate(
            kernel,
            request(
                oid, "Executor", space=space, blocks=[block(data_class="OPERATOR")]
            ),
        )
    with pytest.raises(tree.Refused):
        await delegate(
            kernel, request(oid, "Executor", space=space, slice_space="other")
        )
    with pytest.raises(tree.Refused):
        await delegate(kernel, request(oid, "Executor", space=space, sha256="0" * 64))
    assert (await tree.project(kernel, oid)).state == "APPROVED"
    assert await ledger(kernel, space) == []
    await kernel.commit()


async def test_the_token_never_lands_and_the_issuer_is_called_once(
    kernel, space, bound
):
    issuer, profile_for = bound
    oid = await opened(kernel, space)
    brief = await delegate(kernel, request(oid, "Executor", space=space))
    token = brief.gateway_token.get_secret_value()
    sha = hashlib.sha256(token.encode()).hexdigest()
    cur = await kernel.execute(
        "SELECT brief, gateway_token_sha256, objective_id, agent_class, generation "
        "FROM briefs WHERE id = %s",
        (brief.id,),
    )
    stored, stored_sha, objective_id, agent_class, generation = await cur.fetchone()
    assert (stored_sha, objective_id, agent_class, generation) == (
        sha,
        oid,
        "Executor",
        1,
    )
    assert "gateway_token" not in stored
    assert token not in str(stored)
    (issued,) = [
        e
        for e in await read_for(kernel, space_id=space, key="objective_id", value=oid)
        if e.type == "brief.issued"
    ]
    assert issued.payload["gateway_token_sha256"] == sha
    assert "gateway_token" not in issued.payload["brief"]
    assert token not in str(issued.payload)
    assert issued.payload["brief"] == stored
    assert issuer.issued == [
        dict(brief_id=brief.id, generation=1, model_ref=brief.model_ref, space=space)
    ]
    assert brief.model_ref == load_models().model("frontier").id
    (call,) = profile_for.calls
    assert call == dict(
        name="worktree",
        space=space,
        key=oid,
        artifact_kind="code",
        max_data_class="PROJECT",
        snapshot=None,
        root="/repo",
    )
    assert brief.harness == "pydantic_ai"
    assert brief.ceilings.max_effect_class == "propose"
    assert (
        brief.ceilings.deadline
        == (await tree.project(kernel, oid)).contract.ceilings.deadline
    )
    await kernel.commit()


async def test_scribe_under_an_objective_and_as_its_own_root(kernel, space, bound):
    issuer, profile_for = bound
    oid = await opened(kernel, space, budget=5000, max_data_class="OPERATOR")
    brief = await delegate(
        kernel,
        request(oid, "Scribe", space=space, budget=1200, max_data_class="OPERATOR"),
        parent_brief="turn-7",
    )
    assert await tree.remaining(kernel, oid) == Budget(usd_micros=3800)
    assert await ledger(kernel, space) == [(oid, brief.id, "allocate", 1200)]
    assert brief.model_ref == load_models().model("summarizer").id
    assert profile_for.calls[-1]["key"] == brief.id
    assert profile_for.calls[-1]["root"] == "/repo"

    root = await delegate(
        kernel,
        request(None, "Scribe", space=space, budget=300, max_data_class="OPERATOR"),
        parent_brief="turn-8",
    )
    assert await ledger(kernel, space) == [
        (oid, brief.id, "allocate", 1200),
        (root.id, None, "grant", 300),
    ]
    assert root.objective_id is None
    assert root.generation == 1
    assert root.ceilings.max_effect_class == "propose"
    assert root.ceilings.max_data_class == "OPERATOR"
    assert timedelta(minutes=59) < root.ceilings.deadline - datetime.now(UTC)
    assert root.ceilings.deadline - datetime.now(UTC) <= timedelta(hours=1)
    assert profile_for.calls[-1] == dict(
        name="scratch",
        space=space,
        key=root.id,
        artifact_kind="document",
        max_data_class="OPERATOR",
        snapshot=None,
        root=None,
    )
    cur = await kernel.execute(
        "SELECT objective_id, generation FROM briefs WHERE id = %s", (root.id,)
    )
    assert await cur.fetchone() == (None, 1)
    assert await tree.remaining(kernel, root.id) == Budget(usd_micros=300)
    assert issuer.issued[-1]["generation"] == 1

    with pytest.raises(tree.Refused):
        await delegate(kernel, request(None, "Scribe", space=space), parent_brief=None)
    with pytest.raises(tree.Refused):
        await delegate(
            kernel, request(None, "Scribe", space=space), parent_brief=brief.id
        )
    assert len(await ledger(kernel, space)) == 2
    await kernel.commit()


async def test_budget_is_carved_or_refused(kernel, space, bound):
    oid = await opened(kernel, space, budget=1000)
    with pytest.raises(tree.BudgetExceeded):
        await delegate(kernel, request(oid, "Executor", space=space, budget=1001))
    assert (await tree.project(kernel, oid)).state == "APPROVED"
    brief = await delegate(kernel, request(oid, "Executor", space=space, budget=1000))
    assert brief.budget == Budget(usd_micros=1000)
    assert await tree.remaining(kernel, oid) == Budget(usd_micros=0)
    await kernel.commit()


async def test_deadline_and_unknown_space_refuse(kernel, space, bound):
    oid = await opened(kernel, space, deadline=datetime.now(UTC) + timedelta(seconds=1))
    import asyncio

    await asyncio.sleep(1.1)
    with pytest.raises(tree.Refused):
        await delegate(kernel, request(oid, "Executor", space=space))
    with pytest.raises(tree.Refused):
        await delegate(
            kernel, request(None, "Scribe", space="nowhere"), parent_brief="turn-1"
        )
    await kernel.commit()


async def test_a_worker_parent_uses_the_issuer_it_holds(kernel, space, bound):
    oid = await opened(kernel, space)
    executor = await delegate(kernel, request(oid, "Executor", space=space))
    narrow = frozenset(c for c in executor.capabilities if c.name == "read")
    with pytest.raises(tree.Refused):
        await tree.delegate(
            kernel,
            request(oid, "Scribe", space=space, names=("read", "ask")),
            issuer=narrow,
            parent_brief=executor.id,
        )
    scribe = await tree.delegate(
        kernel,
        request(oid, "Scribe", space=space, names=("read",)),
        issuer=narrow,
        parent_brief=executor.id,
    )
    assert scribe.capabilities == narrow
    await kernel.commit()
