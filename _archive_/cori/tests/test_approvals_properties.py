"""The surface's invariants as Hypothesis properties. Plan 10, Properties.

Two hundred examples for the pure properties, fifty for the ones that talk to
the local database. Sync test functions drive the async kernel on a private
loop, the pattern `tests/test_tree_properties.py` established.

Every property here is a claim about authority: an approval is spent once, a
card carries one decision, a record is bound to what the person saw, a reply
comes from a session the kernel opened, a card belongs to one space, and an
expiry closes a card without moving a node.
"""

import asyncio
import os
import pty
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import psycopg
import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

import kernel.approvals as approvals
import kernel.tree as tree
from kernel.events import read, read_for
from schemas.approval import ApprovalRecord, Reply
from schemas.effect import PushBranch
from schemas.ids import new_id
from schemas.trace import Question
from tests.conftest import dsn, requires_postgres
from tests.tree_fakes import bind, contract, force_state, make_space

pytestmark = requires_postgres

SPACE = f"space-{uuid.uuid4().hex[:8]}"
OTHER = SPACE + "-other"
SPACES = {sid: make_space(sid) for sid in (SPACE, OTHER)}
DB_SETTINGS = dict(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
APPROVING = sorted(approvals.APPROVING)
INFORMING = ["answered", "rejected", "expired"]


class Recorder:
    """A surface that keeps what it was shown. `issue_card` calls `show`, so
    one is attached for these properties to exercise the real path."""

    def __init__(self):
        self.opened, self.shown, self.said = [], [], []

    async def open(self, conversation):
        self.opened.append(conversation)

    async def show(self, card):
        self.shown.append(card)

    async def say(self, conversation_id, text):
        self.said.append((conversation_id, text))

    def inbound(self):
        raise NotImplementedError


@pytest.fixture(scope="module", autouse=True)
def bound():
    """One bound tree, one attached surface, one live terminal session for the
    whole module. All three are process state, not per example."""
    master, slave = pty.openpty()
    with pytest.MonkeyPatch.context() as mp:
        bind(mp, SPACES)
        mp.setattr("kernel.spaces.load_all", lambda *a, **k: SPACES)
        approvals.attach(Recorder())
        yield approvals.open_terminal_session(slave)
    os.close(master)
    os.close(slave)
    approvals._sessions.clear()
    approvals._recorded.clear()
    approvals._current.clear()
    approvals._surface = None


@pytest.fixture(scope="module")
def session(bound):
    return bound


def run(coro):
    return asyncio.run(coro)


async def connect():
    return await psycopg.AsyncConnection.connect(dsn("kernel_rw"))


async def objective_in(conn, conversation):
    objective_id = await tree.open_objective(
        conn,
        space=conversation.space,
        conversation_id=conversation.id,
        contract=contract(),
    )
    return await tree.project(conn, objective_id)


def question_card_for(objective):
    """A question card without a Brief row: the constructor reads the brief's
    id and nothing else, and these properties are about the card."""
    return approvals.question_card(
        objective,
        SimpleNamespace(id=new_id()),
        Question(question_id=new_id(), text="who?", tool_seq=1),
    )


def reply_to(card, option, session_id):
    return Reply(
        card_id=card.id,
        option=option,
        edit=None,
        raw_message=f"#1 {option}",
        session_id=session_id,
        at=datetime.now(UTC),
    )


async def ledger_total(conn, space):
    row = await (
        await conn.execute(
            "SELECT count(*) FROM budget_ledger WHERE space_id = %s", (space,)
        )
    ).fetchone()
    return row[0]


async def decisions(conn, card_id):
    rows = await (
        await conn.execute("SELECT kind FROM approvals WHERE card_id = %s", (card_id,))
    ).fetchall()
    return [kind for (kind,) in rows]


async def moves_in(conn, space):
    return len(await read(conn, space_id=space, types=["objective.state_changed"]))


# ---------------------------------------------------------------------------
# 1. Consumed once


@settings(**DB_SETTINGS)
@given(data=st.data())
def test_consume_at_most_once(data, session):
    """For every approval id the count of `approval.consumed` is at most one,
    and every consume after the first raises `AlreadyConsumed`, whether the
    second caller is this task or one racing it on its own connection."""

    async def race(approval_id):
        conn = await connect()
        try:
            await approvals.consume(conn, approval_id)
            await conn.commit()
            return "consumed"
        except approvals.AlreadyConsumed:
            await conn.rollback()
            return "already"
        finally:
            await conn.close()

    async def go():
        conn = await connect()
        try:
            conversation = await approvals.open_conversation(conn, SPACE)
            objective = await objective_in(conn, conversation)
            card = question_card_for(objective)
            await approvals.issue_card(conn, card)
            record = await approvals.mint(conn, reply_to(card, "answer", session))
            await conn.commit()

            spent = 0
            for op in data.draw(
                st.lists(st.sampled_from(["once", "again", "race"]), max_size=4)
            ):
                if op == "race":
                    outcomes = await asyncio.gather(
                        *(race(record.id) for _ in range(data.draw(st.integers(2, 4))))
                    )
                    spent += outcomes.count("consumed")
                else:
                    try:
                        await approvals.consume(conn, record.id)
                        await conn.commit()
                        spent += 1
                    except approvals.AlreadyConsumed:
                        await conn.rollback()
                assert spent <= 1, "an approval was consumed twice"
                events = await read_for(
                    conn, space_id=SPACE, key="approval_id", value=record.id
                )
                await conn.commit()
                consumed = [e for e in events if e.type == "approval.consumed"]
                assert len(consumed) == spent
        finally:
            await conn.close()

    run(go())


# ---------------------------------------------------------------------------
# 2. One decision per card


@settings(**DB_SETTINGS)
@given(
    expired=st.booleans(),
    ops=st.lists(st.sampled_from(["reply", "sweep"]), min_size=1, max_size=5),
)
def test_one_decision_per_card(expired, ops, session):
    """At most one `approvals` row per card. A reply past the deadline is
    `CardExpired` whether or not a sweep has run, a second reply before it is
    `AlreadyDecided`, and a sweep after a decision changes nothing.

    `mint` reads the kernel's own wall clock and takes no `now`, so the clock
    moves here by drawing the card's deadline on either side of it rather than
    by drawing the instant of the reply.

    The property's last clause, a reply and a sweep racing inside one instant,
    needs two connections and is in `test_reply_and_sweep_racing_leave_one_row`
    below. These operations are strictly sequential on one connection.
    """

    async def go():
        conn = await connect()
        try:
            conversation = await approvals.open_conversation(conn, SPACE)
            objective = await objective_in(conn, conversation)
            deadline = datetime.now(UTC) + timedelta(seconds=-5 if expired else 3600)
            card = question_card_for(objective).model_copy(
                update={"expires_at": deadline}
            )
            await approvals.issue_card(conn, card)
            await conn.commit()

            decided = None
            for op in ops:
                if op == "reply" and expired:
                    with pytest.raises(approvals.CardExpired):
                        await approvals.mint(conn, reply_to(card, "answer", session))
                elif op == "reply" and decided is None:
                    record = await approvals.mint(
                        conn, reply_to(card, "answer", session)
                    )
                    decided = record.kind
                elif op == "reply":
                    with pytest.raises(approvals.AlreadyDecided):
                        await approvals.mint(conn, reply_to(card, "abort", session))
                else:
                    closed = await approvals.expire_due(conn, now=datetime.now(UTC))
                    if expired and decided is None:
                        assert card.id in closed
                        decided = "expired"
                    else:
                        assert card.id not in closed
                await conn.commit()
                assert await decisions(conn, card.id) == (
                    [] if decided is None else [decided]
                )
        finally:
            await conn.close()

    run(go())


@settings(**DB_SETTINGS)
@given(sweep_first=st.booleans())
def test_reply_and_sweep_racing_leave_one_row(sweep_first, session):
    """The race clause of property 2: a reply and a sweep inside one instant
    leave exactly one `approvals` row, and the `mint` side sees
    `AlreadyDecided` when it is the one that loses.

    Each side runs on its own `kernel_rw` connection in its own transaction,
    so the unique constraint on `approvals.card_id` is what decides, not the
    order of two statements on one connection. The instant is built from the
    seam's own `now` parameter: the card's expiry is an hour out, so the wall
    clock `mint` reads accepts it, and the sweep is handed an instant just
    past that expiry.

    The two sides see a loss differently, by design, and the plan's sentence
    names only one of them. A losing `mint` raises `AlreadyDecided`, because
    a person's reply that did not land is something the caller has to know. A
    losing sweep catches the same violation and leaves the card out of its
    return, because a card that someone answered is not a card to expire.

    Two cards, because the winner of a true race is not the test's to choose:
    `expire_due` walks its due rows in id order and the card this example
    just minted is the newest, so the reply usually gets there first. The
    first card is the race itself, and asserts that whoever wins, one row
    exists and the other side reports the loss in its own way. The second
    card closes the sweep-wins branch on purpose, by letting the sweep commit
    before the reply is attempted, which is the only way to reach
    `AlreadyDecided` on the `mint` side while the wall clock is still inside
    the card's expiry.
    """

    async def card_in(conn, conversation, expires_at):
        objective = await objective_in(conn, conversation)
        card = question_card_for(objective).model_copy(
            update={"expires_at": expires_at}
        )
        await approvals.issue_card(conn, card)
        return card

    async def reply(card):
        conn = await connect()
        try:
            try:
                await approvals.mint(conn, reply_to(card, "answer", session))
                await conn.commit()
                return "minted"
            except approvals.AlreadyDecided:
                await conn.commit()
                return "already"
        finally:
            await conn.close()

    async def sweep(card, at):
        conn = await connect()
        try:
            closed = await approvals.expire_due(conn, now=at)
            await conn.commit()
            return "expired" if card.id in closed else "skipped"
        finally:
            await conn.close()

    async def go():
        expires_at = datetime.now(UTC) + timedelta(hours=1)
        past_it = expires_at + timedelta(seconds=1)
        setup = await connect()
        try:
            conversation = await approvals.open_conversation(setup, SPACE)
            raced = await card_in(setup, conversation, expires_at)
            await setup.commit()
        finally:
            await setup.close()

        sides = (
            (sweep(raced, past_it), reply(raced))
            if sweep_first
            else (reply(raced), sweep(raced, past_it))
        )
        results = await asyncio.gather(*sides)
        swept, minted = results if sweep_first else results[::-1]

        # A second card, written after that sweep so the sweep cannot have
        # taken it already. This sweep commits first, so the reply loses.
        setup = await connect()
        try:
            loser = await card_in(setup, conversation, expires_at)
            await setup.commit()
        finally:
            await setup.close()
        assert await sweep(loser, past_it) == "expired"
        assert await reply(loser) == "already"

        conn = await connect()
        try:
            raced_rows = await decisions(conn, raced.id)
            loser_rows = await decisions(conn, loser.id)
        finally:
            await conn.close()

        assert (swept, minted) in {("expired", "already"), ("skipped", "minted")}
        assert raced_rows == ["expired" if swept == "expired" else "answered"]
        assert loser_rows == ["expired"]

    run(go())


# ---------------------------------------------------------------------------
# 3. Revision and argument binding. Pure.


def action(branch, space=SPACE):
    return PushBranch(
        space=space,
        objective_id="obj-1",
        brief_id="brief-1",
        repo="yudame/cori-sandbox",
        branch=branch,
        source_dir="/tmp/worktree",
        head_sha="0" * 40,
    )


def held(kind, revision, digest):
    return ApprovalRecord(
        id=new_id(),
        card_id=new_id(),
        kind=kind,
        space=SPACE,
        objective_id="obj-1",
        contract_revision=revision,
        argument_sha256=digest,
        raw_message="",
        session_id="s1",
        decided_at=datetime.now(UTC),
    )


@settings(max_examples=200)
@given(
    kind=st.sampled_from(APPROVING),
    revision=st.integers(1, 50),
    other=st.integers(1, 50),
    branches=st.lists(
        st.text("abcdef", min_size=1, max_size=4), min_size=1, max_size=4, unique=True
    ),
    informing=st.sampled_from(INFORMING),
)
def test_check_binds_revision_and_arguments(kind, revision, other, branches, informing):
    batch = [action(branch) for branch in branches]
    digest = approvals.argument_digest(batch)
    record = held(kind, revision, digest)

    approvals.check(record, contract_revision=revision, argument_sha256=digest)
    if other != revision:
        with pytest.raises(approvals.StaleRevision):
            approvals.check(record, contract_revision=other)

    # Any batch differing in a field or in order digests differently, and a
    # record bound to one refuses the other.
    for changed in (
        [action(branch + "z") for branch in branches],
        list(reversed(batch)),
        [action(branch, space=OTHER) for branch in branches],
    ):
        if [a.model_dump(mode="json") for a in changed] == [
            a.model_dump(mode="json") for a in batch
        ]:
            continue  # a one-element batch reversed is the same batch
        theirs = approvals.argument_digest(changed)
        assert theirs != digest
        with pytest.raises(approvals.ArgumentMismatch):
            approvals.check(record, argument_sha256=theirs)

    # A record bound to no batch approves no batch.
    with pytest.raises(approvals.ArgumentMismatch):
        approvals.check(held(kind, revision, None), argument_sha256=digest)

    # Information is never authority, whatever the arguments.
    with pytest.raises(approvals.NotAnApproval):
        approvals.check(
            held(informing, revision, digest),
            contract_revision=revision,
            argument_sha256=digest,
        )


# ---------------------------------------------------------------------------
# 4. Session refusal. Pure over the live set.


@settings(max_examples=200)
@given(
    session_id=st.text(max_size=40),
    option=st.sampled_from(["answer", "abort", "approve", "reject"]),
)
def test_mint_refuses_every_unminted_session(session_id, option, session):
    """`mint` refuses a session outside the live set before it touches the
    database, which the `None` connection here is the proof of: a read would
    raise `AttributeError` instead."""
    assume(not approvals.session_is_live(session_id))
    reply = Reply(
        card_id=new_id(),
        option=option,
        edit=None,
        raw_message=f"#1 {option}",
        session_id=session_id,
        at=datetime.now(UTC),
    )
    with pytest.raises(approvals.UnknownSession):
        run(approvals.mint(None, reply))
    assert approvals.session_is_live(session)


# ---------------------------------------------------------------------------
# 5. Card space


@settings(**DB_SETTINGS)
@given(matching=st.booleans(), card_moved=st.booleans())
def test_card_space_matches_conversation_and_touches_no_ledger(matching, card_moved):
    """`issue_card` succeeds only into a conversation of the card's own space.
    On success exactly one row and one `card.issued` event, and the ledger
    untouched, because a card charges nothing. On refusal nothing at all."""

    async def go():
        conn = await connect()
        try:
            conversation = await approvals.open_conversation(conn, SPACE)
            objective = await objective_in(conn, conversation)
            card = question_card_for(objective)
            if not matching:
                elsewhere = await approvals.open_conversation(conn, OTHER)
                card = card.model_copy(
                    update=(
                        {"space": OTHER}
                        if card_moved
                        else {"conversation_id": elsewhere.id}
                    )
                )
            await conn.commit()
            before = await ledger_total(conn, SPACE)

            if matching:
                await approvals.issue_card(conn, card)
            else:
                with pytest.raises(approvals.SpaceMismatch):
                    await approvals.issue_card(conn, card)
            await conn.commit()

            row = await (
                await conn.execute(
                    "SELECT count(*) FROM cards WHERE id = %s", (card.id,)
                )
            ).fetchone()
            issued = [
                event
                for event in await read(
                    conn, space_id=card.space, types=["card.issued"]
                )
                if event.payload["card"]["id"] == card.id
            ]
            assert row[0] == (1 if matching else 0)
            assert len(issued) == (1 if matching else 0)
            assert await ledger_total(conn, SPACE) == before
        finally:
            await conn.close()

    run(go())


# ---------------------------------------------------------------------------
# 6. Expiry is monotone, idempotent, and transitions nothing


@settings(**DB_SETTINGS)
@given(
    offsets=st.lists(st.integers(-60, 60), min_size=1, max_size=4),
    terminal=st.booleans(),
    steps=st.lists(st.integers(0, 90), min_size=1, max_size=5),
)
def test_expire_due_monotone_idempotent(offsets, terminal, steps):
    """Each card comes back exactly once, at the first `now` at or past its
    deadline and never before it, and no node moves.

    The sweep is over every space, so the assertions are about this example's
    own cards; another example's card in the returned list is expected.
    """

    async def go():
        conn = await connect()
        try:
            base = datetime.now(UTC)
            conversation = await approvals.open_conversation(conn, SPACE)
            objective = await objective_in(conn, conversation)
            if terminal:
                for state, reason, by in (
                    ("APPROVED", "approved", "approve"),
                    ("RUNNING", "running", "delegate"),
                    ("FAILED", "worker_failed", "test"),
                ):
                    await force_state(conn, objective.id, state, reason, by=by)
            state_before = (await tree.project(conn, objective.id)).state

            deadlines = {}
            for offset in offsets:
                card = question_card_for(objective).model_copy(
                    update={"expires_at": base + timedelta(seconds=offset)}
                )
                await approvals.issue_card(conn, card)
                deadlines[card.id] = base + timedelta(seconds=offset)
            await conn.commit()
            moves = await moves_in(conn, SPACE)

            seen = {}
            for now in sorted(base + timedelta(seconds=step) for step in steps):
                closed = set(await approvals.expire_due(conn, now=now)) & set(deadlines)
                await conn.commit()
                for card_id in closed:
                    assert card_id not in seen, "a card expired twice"
                    assert deadlines[card_id] <= now, "a card expired before its time"
                    seen[card_id] = now
                due = {c for c, d in deadlines.items() if d <= now}
                assert due == set(seen), "a due card was left open"

            assert (await tree.project(conn, objective.id)).state == state_before
            assert await moves_in(conn, SPACE) == moves
        finally:
            await conn.close()

    run(go())
