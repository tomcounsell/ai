"""Open, revise, approve, transition, project. Plan 02 task 5."""

import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

import kernel.tree as tree
from tests.conftest import requires_postgres
from tests.tree_fakes import (
    NotAnApproval,
    SpaceRefused,
    approval,
    bind,
    contract,
    force_state,
    make_space,
)

pytestmark = requires_postgres


@pytest.fixture
def spaces(space, monkeypatch):
    spaces = {space: make_space(space, max_effect_class="act")}
    bind(monkeypatch, spaces)
    return spaces


async def opened(kernel, space, **over):
    return await tree.open_objective(
        kernel, space=space, conversation_id="c1", contract=contract(**over)
    )


async def rows_for(kernel, space):
    n = (
        await (
            await kernel.execute(
                "SELECT count(*) FROM objectives WHERE space_id = %s", (space,)
            )
        ).fetchone()
    )[0]
    e = (
        await (
            await kernel.execute(
                "SELECT count(*) FROM events WHERE space_id = %s", (space,)
            )
        ).fetchone()
    )[0]
    return n, e


async def test_open_refuses_before_any_row(kernel, space, spaces, monkeypatch):
    # `unassigned` is shared with the whole database (spaces' `poll_connectors`
    # records `inbound.unassigned` there), so the check is that the refused
    # open added nothing to it; `space` is this test's own and stays empty
    unassigned_before = await rows_for(kernel, "unassigned")
    with pytest.raises(SpaceRefused):
        await opened(kernel, "unassigned")
    with pytest.raises(SpaceRefused):
        await opened(kernel, "nowhere")
    monkeypatch.setitem(spaces, space, make_space(space, max_effect_class="propose"))
    with pytest.raises(tree.Refused):
        await opened(kernel, space, max_effect_class="act")
    with pytest.raises(tree.Refused):
        await opened(kernel, space, deadline=datetime.now(UTC) - timedelta(seconds=1))
    with pytest.raises(tree.Refused):
        await opened(kernel, space, root="/elsewhere")
    assert await rows_for(kernel, space) == (0, 0)
    assert await rows_for(kernel, "unassigned") == unassigned_before


async def test_open_gives_framed_at_revision_one(kernel, space, spaces):
    oid = await opened(kernel, space)
    node = await tree.project(kernel, oid)
    assert (node.state, node.state_reason) == ("FRAMED", "framed")
    assert node.contract_revision == 1 and node.approved_revision is None
    assert node.generation == 1 and node.owner_brief is None
    assert node.depth == 0 and node.parent_id is None and node.children == []
    assert node.space == space and node.conversation_id == "c1"
    await kernel.commit()


async def test_self_approval_refused_on_act_and_free_on_propose(kernel, space, spaces):
    act = await opened(kernel, space, max_effect_class="act")
    with pytest.raises(tree.Refused):
        await tree.approve(kernel, act, approval("self_approved", act, 1))
    assert (await tree.project(kernel, act)).state == "FRAMED"
    big = await opened(kernel, space, max_effect_class="propose", budget=10**12)
    await tree.approve(kernel, big, approval("self_approved", big, 1))
    node = await tree.project(kernel, big)
    assert (node.state, node.approved_revision) == ("APPROVED", 1)
    await kernel.commit()


async def test_approve_refuses_other_objective_stale_revision_and_non_approvals(
    kernel, space, spaces
):
    oid = await opened(kernel, space)
    other = await opened(kernel, space)
    with pytest.raises(tree.Refused):
        await tree.approve(kernel, oid, approval("approved", other, 1))
    with pytest.raises(tree.StaleRevision):
        await tree.approve(kernel, oid, approval("approved", oid, 2))
    with pytest.raises(NotAnApproval):
        await tree.approve(kernel, oid, approval("rejected", oid, 1))
    assert (await tree.project(kernel, oid)).state == "FRAMED"


async def to_failed(kernel, oid):
    await tree.approve(kernel, oid, approval("approved", oid, 1))
    await force_state(kernel, oid, "RUNNING", "running", by="delegate")
    await tree.transition(kernel, oid, "FAILED", "worker_failed")
    assert (await tree.project(kernel, oid)).state == "FAILED"


async def test_failed_is_revived_by_an_approved_record_only(kernel, space, spaces):
    oid = await opened(kernel, space)
    await to_failed(kernel, oid)
    with pytest.raises(tree.IllegalTransition):
        await tree.approve(kernel, oid, approval("self_approved", oid, 1))
    with pytest.raises(tree.IllegalTransition):
        await tree.transition(kernel, oid, "APPROVED", "approved")
    await tree.approve(kernel, oid, approval("approved", oid, 1))
    node = await tree.project(kernel, oid)
    assert (node.state, node.state_reason) == ("APPROVED", "approved")
    await kernel.commit()


async def test_commit_edge_and_the_reserved_pairs(kernel, space, spaces):
    oid = await opened(kernel, space)
    assert await tree.transition(kernel, oid, "AWAITING_APPROVAL", "commit") == []
    node = await tree.project(kernel, oid)
    assert (node.state, node.state_reason) == ("AWAITING_APPROVAL", "commit")
    with pytest.raises(tree.IllegalTransition):
        await tree.transition(kernel, oid, "APPROVED", "approved")
    with pytest.raises(tree.IllegalTransition):
        await tree.transition(kernel, oid, "AWAITING_APPROVAL", "budget_increase")
    await tree.approve(kernel, oid, approval("approved", oid, 1))
    with pytest.raises(tree.IllegalTransition):
        await tree.transition(kernel, oid, "RUNNING", "running")
    with pytest.raises(tree.IllegalTransition):
        await tree.transition(kernel, oid, "PAUSED", "away")
    assert await tree.transition(kernel, oid, "CANCELLED", "stopped_by_person") == []
    with pytest.raises(tree.IllegalTransition):
        await tree.transition(kernel, oid, "APPROVED", "approved")
    await kernel.commit()


async def test_revise_only_before_work(kernel, space, spaces):
    oid = await opened(kernel, space)
    await tree.approve(kernel, oid, approval("approved", oid, 1))
    await force_state(kernel, oid, "RUNNING", "running", by="delegate")
    with pytest.raises(tree.IllegalTransition):
        await tree.revise_contract(kernel, oid, contract(budget=2))
    assert (await tree.project(kernel, oid)).contract_revision == 1


async def test_revise_refuses_what_the_space_refuses(kernel, space, spaces):
    oid = await opened(kernel, space)
    with pytest.raises(tree.Refused):
        await tree.revise_contract(kernel, oid, contract(root="/elsewhere"))
    spaces[space] = make_space(space, max_effect_class="propose")
    with pytest.raises(tree.Refused):
        await tree.revise_contract(kernel, oid, contract(max_effect_class="act"))
    assert (await tree.project(kernel, oid)).contract_revision == 1


async def test_project_after_open_revise_approve(kernel, space, spaces):
    oid = await opened(kernel, space)
    await tree.approve(kernel, oid, approval("approved", oid, 1))
    cancelled = await tree.transition(kernel, oid, "CANCELLED", "space_ended")
    assert cancelled == []
    oid = await opened(kernel, space)
    assert await tree.revise_contract(kernel, oid, contract(budget=5)) == 2
    node = await tree.project(kernel, oid)
    assert node.contract_revision == 2 and node.approved_revision is None
    assert node.contract.budget.usd_micros == 5
    with pytest.raises(tree.StaleRevision):
        await tree.approve(kernel, oid, approval("approved", oid, 1))
    await tree.approve(kernel, oid, approval("approved", oid, 2))
    node = await tree.project(kernel, oid)
    assert (node.contract_revision, node.approved_revision, node.state) == (
        2,
        2,
        "APPROVED",
    )
    revisions = await (
        await kernel.execute(
            "SELECT revision FROM objective_revisions WHERE objective_id = %s "
            "ORDER BY revision",
            (oid,),
        )
    ).fetchall()
    assert revisions == [(1,), (2,)]
    await kernel.commit()


def test_no_import_time_collaborators():
    code = (
        "import sys\n"
        "for m in ('kernel.spaces', 'kernel.approvals', 'gateway', "
        "'infra.sandbox.mounts'):\n"
        "    sys.modules[m] = None\n"
        "import kernel.tree as t\n"
        "assert t.check_may_open is None and t.approval_check is None\n"
        "assert t.default_token_issuer is None and t.profile_for is None\n"
        "print('ok')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "ok"
    code = (
        "import sys\n"
        "import kernel.tree\n"
        "absent = [m for m in ('kernel.spaces', 'kernel.approvals', 'gateway') "
        "if m not in sys.modules]\n"
        "assert len(absent) == 3, absent\n"
        "print('ok')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "ok"
