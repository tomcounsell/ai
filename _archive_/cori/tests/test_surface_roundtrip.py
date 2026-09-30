"""One card, from the kernel to a real terminal and back. Plan 10 task 9.

Everything here is the production path: a pty the process's user owns, the CLI
adapter over asyncio streams on it, the kernel's own constructors, and the
local database. The only thing the test stands in for is the person, who types
one line into the master end.
"""

import asyncio
import os
import pty
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

import kernel.approvals as approvals
import kernel.tree as tree
from adapters.cli import CliSurface
from kernel.events import read
from schemas.brief import Brief
from schemas.budget import Budget
from schemas.ids import new_id
from schemas.sandbox import SandboxProfile
from schemas.trace import Question
from tests.conftest import requires_postgres
from tests.tree_fakes import bind, caps, context_slice, contract, make_space

pytestmark = requires_postgres

REPLY = "#1 answer Tom"


@pytest.fixture(autouse=True)
def clean_module_state():
    approvals._sessions.clear()
    approvals._recorded.clear()
    approvals._current.clear()
    approvals._surface = None
    yield
    approvals._sessions.clear()
    approvals._recorded.clear()
    approvals._current.clear()
    approvals._surface = None


@pytest.fixture
def spaces(space, monkeypatch):
    made = {space: make_space(space)}
    monkeypatch.setattr("kernel.spaces.load_all", lambda *a, **k: made)
    bind(monkeypatch, made)
    return made


def brief_of(objective) -> Brief:
    return Brief(
        id=new_id(),
        objective_id=objective.id,
        space=objective.space,
        agent_class="Executor",
        harness="pydantic_ai",
        generation=1,
        context_slice=context_slice(objective.space),
        instruction=None,
        max_data_class="PROJECT",
        budget=Budget(usd_micros=1000),
        ceilings=objective.contract.ceilings,
        capabilities=caps(objective.space),
        sandbox_profile=SandboxProfile(
            name="worktree",
            space=objective.space,
            mount_source="/tmp/cori-test",
            readonly=False,
            network="hostonly",
            key="k",
            env={},
        ),
        model_ref="claude-sonnet-5",
        gateway_token=SecretStr("t"),
        report_schema="Report",
        issued_at=datetime.now(UTC),
    )


async def streams(slave: int):
    """An asyncio reader and writer on one end of the pty, the pair
    `kernel/__main__.py` hands the adapter at boot."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    read_end = os.fdopen(os.dup(slave), "rb", buffering=0)
    write_end = os.fdopen(os.dup(slave), "wb", buffering=0)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), read_end)
    transport, protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin, write_end
    )
    return reader, asyncio.StreamWriter(transport, protocol, reader, loop)


async def test_question_card_round_trip(kernel, space, spaces):
    """A worker's question reaches the terminal as card #1, the person answers
    it, and the answer becomes an approval that is minted once and spent
    once. The log of the space is the whole story in order."""
    master, slave = pty.openpty()
    try:
        session = approvals.open_terminal_session(slave)
        reader, writer = await streams(slave)
        surface = CliSurface(reader, writer, session)
        approvals.attach(surface)

        conversation = await approvals.open_conversation(kernel, space)
        await approvals.record_session(kernel, space)
        objective_id = await tree.open_objective(
            kernel,
            space=space,
            conversation_id=conversation.id,
            contract=contract(),
        )
        objective = await tree.project(kernel, objective_id)
        brief = brief_of(objective)
        card = approvals.question_card(
            objective,
            brief,
            Question(question_id=new_id(), text="What is your first name?", tool_seq=1),
        )
        await approvals.issue_card(kernel, card)
        await kernel.commit()

        # The person reads the block and types one line.
        os.write(master, (REPLY + "\n").encode())
        inbound = surface.inbound()
        reply = await asyncio.wait_for(anext(inbound), timeout=5)
        assert reply.card_id == card.id and reply.text == "Tom"

        record = await approvals.mint(kernel, reply)
        await approvals.consume(kernel, record.id)
        await kernel.commit()
    finally:
        os.close(master)
        os.close(slave)

    assert [e.type for e in await read(kernel, space_id=space)] == [
        "conversation.opened",
        "session.opened",
        "objective.opened",
        "card.issued",
        "approval.minted",
        "approval.consumed",
    ]

    row = await (
        await kernel.execute(
            "SELECT kind, raw_message, session_id FROM approvals WHERE card_id = %s",
            (card.id,),
        )
    ).fetchone()
    assert row == ("answered", REPLY, session)

    minted = await read(kernel, space_id=space, types=["approval.minted"])
    assert minted[0].payload["text"] == "Tom"
    assert minted[0].payload["approval"]["id"] == record.id

    spent = await read(kernel, space_id=space, types=["approval.consumed"])
    assert spent[0].payload == {"approval_id": record.id, "by": objective.id}

    ledger = await (
        await kernel.execute(
            "SELECT count(*) FROM budget_ledger WHERE space_id = %s", (space,)
        )
    ).fetchone()
    assert ledger == (0,)

    # The person saw the conversation line and the card block, and the block
    # carries the number the reply named.
    assert surface.number_of(card.id) == 1
