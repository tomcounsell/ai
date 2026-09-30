"""`kernel/approvals.py`: sessions, conversations, cards, and the records
only the kernel mints. Plan 10 tasks 3 to 6; seams §3.7.

One example test per behaviour; the invariants behind them are the
Hypothesis properties in `tests/test_approvals_properties.py`.
"""

import asyncio
import os
import pty
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from pydantic import SecretStr

import kernel.approvals as approvals
import kernel.tree as tree
from schemas.approval import CARD_FIELDS, ApprovalRecord, Reply, render_instant
from schemas.brief import Brief
from schemas.budget import ZERO, Budget
from schemas.effect import EffectOutcome, PushBranch
from schemas.ids import new_id
from schemas.inbound import InboundItem
from schemas.sandbox import SandboxProfile
from schemas.trace import Question
from tests.conftest import dsn, requires_postgres
from tests.tree_fakes import (
    bind,
    caps,
    context_slice,
    contract,
    force_state,
    make_space,
    verdict,
)

pytestmark = requires_postgres


class FakeSurface:
    """Records what the kernel asked the person to see. Stands in for
    `adapters/cli.py`, which task 8 builds."""

    def __init__(self):
        self.opened = []
        self.shown = []
        self.said = []

    async def open(self, conversation):
        self.opened.append(conversation)

    async def show(self, card):
        self.shown.append(card)

    async def say(self, conversation_id, text):
        self.said.append((conversation_id, text))

    def inbound(self):
        raise NotImplementedError("the fake surface relays nothing")


@pytest.fixture(autouse=True)
def clean_module_state():
    """The live set, the attached surface, and the current conversation are
    one process's terminal. A test never inherits another's."""
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
def surface():
    s = FakeSurface()
    approvals.attach(s)
    return s


@pytest.fixture
def terminal():
    """A pty pair. The slave is a terminal this process's user owns, so
    `open_terminal_session` accepts it; the pipe end beside it does not."""
    master, slave = pty.openpty()
    yield slave
    os.close(master)
    os.close(slave)


@pytest.fixture
def session(terminal):
    return approvals.open_terminal_session(terminal)


@pytest.fixture
def spaces(space, monkeypatch):
    """`space` and one other, real to `switch_space` and to the tree."""
    made = {sid: make_space(sid) for sid in (space, space + "-other")}
    monkeypatch.setattr("kernel.spaces.load_all", lambda *a, **k: made)
    bind(monkeypatch, made)
    return made


async def events_of(conn, space, types=None):
    from kernel.events import read

    rows = await read(conn, space_id=space, types=types)
    return [e.type for e in rows]


# ---------------------------------------------------------------------------
# Sessions


def test_terminal_session_refuses_a_pipe(terminal):
    """Tech stack §13: an approval is an interactive confirmation attributed
    to a terminal session, never a write a worker could make. A worker can
    hold a pipe; it cannot hold the person's tty."""
    session_id = approvals.open_terminal_session(terminal)
    assert approvals.session_is_live(session_id)

    read_end, write_end = os.pipe()
    try:
        with pytest.raises(approvals.NotATerminal):
            approvals.open_terminal_session(read_end)
        with pytest.raises(approvals.NotATerminal):
            approvals.open_terminal_session(write_end)
    finally:
        os.close(read_end)
        os.close(write_end)
    assert not approvals.session_is_live("made-up")


async def test_record_session_appends_session_opened_once_with_uid_and_tty(
    kernel, space, session, terminal
):
    from kernel.events import read

    # A read writes nothing, so nothing is in the log before the explicit call.
    assert await approvals.latest_conversation(kernel, space) is None
    assert await events_of(kernel, space) == []

    await approvals.record_session(kernel, space)
    await approvals.record_session(kernel, space)
    await kernel.commit()

    rows = await read(kernel, space_id=space, types=["session.opened"])
    assert len(rows) == 1
    assert rows[0].payload == {
        "session_id": session,
        "uid": os.getuid(),
        "tty": os.ttyname(terminal),
    }


# ---------------------------------------------------------------------------
# Conversations


async def test_open_conversation_writes_row_event_and_calls_surface_open(
    kernel, space, surface
):
    from kernel.events import read

    conversation = await approvals.open_conversation(kernel, space)
    await kernel.commit()

    row = await (
        await kernel.execute(
            "SELECT space_id FROM conversations WHERE id = %s", (conversation.id,)
        )
    ).fetchone()
    assert row == (space,)
    rows = await read(kernel, space_id=space, types=["conversation.opened"])
    assert [e.payload["conversation"]["id"] for e in rows] == [conversation.id]
    assert surface.opened == [conversation]
    assert approvals.current_conversation(space) == conversation


async def test_latest_conversation_resumes_after_restart(kernel, space, surface):
    """A restart is a resume: the conversation id survives it (architecture
    §1). The newest of several is the one the person is in."""
    assert await approvals.latest_conversation(kernel, space) is None
    first = await approvals.open_conversation(kernel, space)
    second = await approvals.open_conversation(kernel, space)
    await kernel.commit()

    approvals._current.clear()  # the restart
    resumed = await approvals.latest_conversation(kernel, space)
    assert resumed is not None and resumed.id == second.id != first.id
    assert approvals.current_conversation(space).id == second.id
    # The read appended nothing and called nothing on the surface.
    assert await events_of(kernel, space) == [
        "conversation.opened",
        "conversation.opened",
    ]
    assert len(surface.opened) == 2


class TestConversationSwitch:
    """A space switch seals one conversation and opens another, so these
    three are conversation tests and the task 3 check selects them by the
    class name rather than by a name each would have to repeat."""

    async def test_switch_space_seals_and_opens(self, kernel, space, spaces, surface):
        from kernel.events import read

        other = space + "-other"
        old = await approvals.open_conversation(kernel, space)
        new = await approvals.switch_space(kernel, old.id, other)
        await kernel.commit()

        assert new.id != old.id and new.space == other
        switched = await read(kernel, space_id=space, types=["space.switched"])
        assert len(switched) == 1
        assert switched[0].payload == {
            "conversation_id": old.id,
            "from": space,
            "to": other,
            "new_conversation_id": new.id,
        }
        # The new conversation's own event is in the target space, not the old one.
        assert await events_of(kernel, other) == ["conversation.opened"]
        assert await events_of(kernel, space) == [
            "conversation.opened",
            "space.switched",
        ]
        # The old conversation still belongs to the old space.
        row = await (
            await kernel.execute(
                "SELECT space_id FROM conversations WHERE id = %s", (old.id,)
            )
        ).fetchone()
        assert row == (space,)
        assert approvals.current_conversation(other) == new

    async def test_switch_to_unassigned_is_allowed(
        self, kernel, space, spaces, surface
    ):
        """The person needs somewhere to see a `route_inbound` card; opening an
        objective in `unassigned` stays refused by the tree (seams §0)."""
        old = await approvals.open_conversation(kernel, space)
        new = await approvals.switch_space(kernel, old.id, "unassigned")
        await kernel.rollback()
        assert new.space == "unassigned"

    async def test_switch_to_unknown_space_refused(
        self, kernel, space, spaces, surface
    ):
        old = await approvals.open_conversation(kernel, space)
        with pytest.raises(approvals.UnknownSpace):
            await approvals.switch_space(kernel, old.id, "nowhere")
        await kernel.rollback()
        with pytest.raises(approvals.NoConversation):
            await approvals.switch_space(kernel, f"conv-{uuid.uuid4().hex}", space)
        await kernel.rollback()


# ---------------------------------------------------------------------------
# Cards


async def objective_of(conn, conversation, **over):
    oid = await tree.open_objective(
        conn,
        space=conversation.space,
        conversation_id=conversation.id,
        contract=contract(**over),
    )
    return await tree.project(conn, oid)


def brief_of(objective, agent_class="Executor") -> Brief:
    """The smallest real Brief. `question_card` reads its id, and the seams
    type the parameter `Brief`, so the test hands it one."""
    return Brief(
        id=new_id(),
        objective_id=objective.id,
        space=objective.space,
        agent_class=agent_class,
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


def question_of(text="What is your first name?") -> Question:
    return Question(question_id=new_id(), text=text, tool_seq=1)


def inbound_item(space, **over) -> InboundItem:
    fields = dict(
        id=new_id(),
        connector="gmail",
        account="tom@example.com",
        external_id="m-1",
        headers={"from": "a@b.test", "subject": "invoice"},
        received_at=datetime.now(UTC),
        space=space,
        routed_by=None,
    )
    fields.update(over)
    return InboundItem(**fields)


async def ledger_rows(conn, space):
    return await (
        await conn.execute(
            "SELECT node_id, brief_id, kind, usd_micros FROM budget_ledger "
            "WHERE space_id = %s ORDER BY id",
            (space,),
        )
    ).fetchall()


async def test_question_card_regards_objective_and_binds_revision(
    kernel, space, spaces, surface
):
    conversation = await approvals.open_conversation(kernel, space)
    objective = await objective_of(kernel, conversation)
    brief = brief_of(objective)
    question = question_of()
    card = approvals.question_card(objective, brief, question)

    assert card.kind == "question"
    assert card.regards == objective.id
    assert card.space == space and card.conversation_id == conversation.id
    assert card.contract_revision == objective.contract_revision == 1
    assert card.expires_at == objective.contract.ceilings.deadline
    assert list(card.fields) == ["brief_id", "question_id", "question"]
    assert card.fields == {
        "brief_id": brief.id,
        "question_id": question.question_id,
        "question": "What is your first name?",
    }
    assert [(o.key, o.recommended) for o in card.options] == [
        ("answer", True),
        ("abort", False),
    ]
    assert card.note == ""

    # The binding follows the contract: a later revision gives a later card.
    await approvals.issue_card(kernel, card)
    revision = await tree.revise_contract(
        kernel, objective.id, contract(budget=2_000_000)
    )
    revised = await tree.project(kernel, objective.id)
    later = approvals.question_card(revised, brief, question)
    assert revision == 2 and later.contract_revision == 2
    # The row carries the objective id, which is what the lock and the sweep key on.
    row = await (
        await kernel.execute(
            "SELECT objective_id, issued FROM cards WHERE id = %s", (card.id,)
        )
    ).fetchone()
    assert row == (objective.id, True)
    await kernel.commit()


async def test_issue_card_appends_event_and_shows(kernel, space, spaces, surface):
    from kernel.events import read

    conversation = await approvals.open_conversation(kernel, space)
    objective = await objective_of(kernel, conversation)
    card = approvals.question_card(objective, brief_of(objective), question_of())
    await approvals.issue_card(kernel, card)
    await kernel.commit()

    events = await read(kernel, space_id=space, types=["card.issued"])
    assert len(events) == 1
    assert events[0].payload["card"] == card.model_dump(mode="json")
    assert surface.shown == [card]
    assert [c.id for c in await approvals.pending_cards(kernel, conversation.id)] == [
        card.id
    ]


async def test_issue_card_refuses_space_mismatch(kernel, space, spaces, surface):
    from kernel.events import read

    conversation = await approvals.open_conversation(kernel, space)
    objective = await objective_of(kernel, conversation)
    card = approvals.question_card(objective, brief_of(objective), question_of())
    elsewhere = card.model_copy(update={"space": space + "-other"})

    with pytest.raises(approvals.SpaceMismatch):
        await approvals.issue_card(kernel, elsewhere)
    await kernel.rollback()
    assert await read(kernel, space_id=space, types=["card.issued"]) == []
    assert surface.shown == []

    unknown = card.model_copy(update={"conversation_id": new_id()})
    with pytest.raises(approvals.NoConversation):
        await approvals.issue_card(kernel, unknown)
    await kernel.rollback()


async def test_issue_card_writes_no_ledger_row(kernel, space, spaces, surface):
    """Seams version 3, item 1: `issue_card` charges nothing. The card that
    would have failed under the old rule is the verification failure one,
    whose Executor brief is released by the time the supervisor sees the
    verdict (critique 2)."""
    conversation = await approvals.open_conversation(kernel, space)
    objective = await objective_of(kernel, conversation)
    executor = new_id()
    await tree._allocate(kernel, objective.id, executor, Budget(usd_micros=300))
    await tree.consume(kernel, executor, Budget(usd_micros=100))
    await tree.release(kernel, executor)
    assert await tree.remaining(kernel, executor) == ZERO
    before = await ledger_rows(kernel, space)

    card = approvals.verification_failure_card(
        objective, executor, verdict(outcome="fail")
    )
    await approvals.issue_card(kernel, card)
    await kernel.commit()

    assert await ledger_rows(kernel, space) == before
    assert card.fields["brief_id"] == executor
    assert card.fields["verdict_summary"] == "looked"
    assert card.fields["failed_criteria"] == '["pytest green"]'
    assert [(o.key, o.recommended) for o in card.options] == [
        ("retry", True),
        ("cancel", False),
    ]
    # An abstain is a criterion the verdict did not meet, so the person sees it.
    abstained = approvals.verification_failure_card(
        objective, brief_of(objective), verdict(outcome="abstain")
    )
    assert abstained.fields["failed_criteria"] == '["pytest green"]'


async def test_commit_card_renders_budget_as_dollars_and_carries_basis(
    kernel, space, spaces, surface
):
    conversation = await approvals.open_conversation(kernel, space)
    objective = await objective_of(kernel, conversation, budget=12_500_000)
    card = approvals.commit_card(objective, audience="sandbox")
    ceilings = objective.contract.ceilings
    deadline = render_instant(ceilings.deadline)

    assert list(card.fields) == list(CARD_FIELDS["commit"])
    assert card.fields["budget"] == "$12.50"
    assert card.fields["basis"] == "one Executor turn and one Verifier turn"
    assert card.fields["premise"] == "make the failing test pass"
    assert card.fields["non_goals"] == '["refactor"]'
    assert card.fields["success_criteria"] == '["pytest green"]'
    assert card.fields["effect_ceiling"] == ceilings.max_effect_class
    assert card.fields["deadline"] == deadline
    assert card.fields["stop_conditions"] == (
        '{"deadline":"%s","max_data_class":"PROJECT","max_effect_class":"%s"}'
        % (deadline, ceilings.max_effect_class)
    )
    assert card.fields["audience"] == "sandbox"
    assert [(o.key, o.recommended) for o in card.options] == [
        ("approve", True),
        ("edit", False),
        ("reject", False),
    ]


async def test_budget_increase_card_carries_spent_produced_requested(
    kernel, space, spaces, surface
):
    conversation = await approvals.open_conversation(kernel, space)
    objective = await objective_of(kernel, conversation)
    executor = new_id()
    card = approvals.budget_increase_card(
        objective,
        executor,
        spent=Budget(usd_micros=1_000_000),
        produced=["/repo/a.py", "/repo/b.py"],
        requested=Budget(usd_micros=500_000),
    )
    assert card.fields == {
        "brief_id": executor,
        "spent": "$1.00",
        "produced": '["/repo/a.py","/repo/b.py"]',
        "requested": "$0.50",
    }
    assert card.regards == objective.id and card.contract_revision == 1
    assert [(o.key, o.recommended) for o in card.options] == [
        ("grant", True),
        ("deny", False),
    ]


async def test_unknown_outcome_card_fields_options_and_expiry(
    kernel, space, spaces, surface
):
    outcome = EffectOutcome(
        effect_id=new_id(),
        kind="unknown",
        result={
            "space_id": space,
            "action_type": "push_branch",
            "idempotency_key": "k-1",
            "objective_id": "obj-1",
        },
    )
    # The card names the space's current conversation, and there is none yet.
    with pytest.raises(approvals.NoConversation):
        approvals.unknown_outcome_card(outcome)

    conversation = await approvals.open_conversation(kernel, space)
    card = approvals.unknown_outcome_card(outcome)
    assert card.conversation_id == conversation.id and card.space == space
    assert card.regards == outcome.effect_id and card.contract_revision is None
    assert card.fields == {
        "effect_id": outcome.effect_id,
        "action_type": "push_branch",
        "idempotency_key": "k-1",
    }
    assert [(o.key, o.recommended) for o in card.options] == [
        ("done", True),
        ("rerun", False),
        ("drop", False),
    ]
    assert card.expires_at - card.issued_at == timedelta(days=7)
    # Nothing objective about it: the row's objective_id stays null.
    await approvals.issue_card(kernel, card)
    row = await (
        await kernel.execute("SELECT objective_id FROM cards WHERE id = %s", (card.id,))
    ).fetchone()
    assert row == (None,)
    await kernel.commit()


async def test_route_inbound_card_options(kernel, space, spaces, surface):
    await approvals.open_conversation(kernel, space)
    item = inbound_item(space)

    one = approvals.route_inbound_card(item, ["alpha"])
    assert [(o.key, o.recommended) for o in one.options] == [
        ("alpha", True),
        ("keep_unassigned", False),
    ]
    assert one.regards == item.id and one.contract_revision is None
    assert one.expires_at - one.issued_at == timedelta(days=7)
    assert one.fields["candidates"] == '["alpha"]'
    assert one.fields["headers"] == '{"from":"a@b.test","subject":"invoice"}'

    five = approvals.route_inbound_card(item, list("abcde"))
    assert [(o.key, o.recommended) for o in five.options] == [
        ("a", True),
        ("b", False),
        ("c", False),
    ]
    assert five.fields["candidates"] == '["a","b","c","d","e"]'

    with pytest.raises(ValueError):
        approvals.route_inbound_card(item, [])


# ---------------------------------------------------------------------------
# Mint, self-approve, check


def reply_to(card, option, session, **over):
    fields = dict(
        card_id=card.id,
        option=option,
        edit=None,
        raw_message=f"#1 {option}",
        session_id=session,
        at=datetime.now(UTC),
    )
    fields.update(over)
    return Reply(**fields)


async def test_mint_refuses_unknown_session(kernel, space, spaces, surface, session):
    conversation = await approvals.open_conversation(kernel, space)
    objective = await objective_of(kernel, conversation)
    card = approvals.question_card(objective, brief_of(objective), question_of())
    await approvals.issue_card(kernel, card)

    with pytest.raises(approvals.UnknownSession):
        await approvals.mint(kernel, reply_to(card, "answer", "not-a-session"))
    # And it refuses before touching the database: no row for this card.
    rows = await (
        await kernel.execute(
            "SELECT count(*) FROM approvals WHERE card_id = %s", (card.id,)
        )
    ).fetchone()
    assert rows == (0,)
    with pytest.raises(approvals.UnknownCard):
        await approvals.mint(kernel, reply_to(card, "answer", session, card_id="none"))
    await kernel.rollback()


async def test_mint_maps_option_to_kind_per_table(
    kernel, space, spaces, surface, session
):
    conversation = await approvals.open_conversation(kernel, space)
    objective = await objective_of(kernel, conversation)
    brief = brief_of(objective)

    def fresh(kind):
        if kind == "commit":
            return approvals.commit_card(objective, audience="sandbox")
        if kind == "question":
            return approvals.question_card(objective, brief, question_of())
        if kind == "verification_failure":
            return approvals.verification_failure_card(
                objective, brief, verdict("fail")
            )
        if kind == "budget_increase":
            return approvals.budget_increase_card(
                objective,
                brief.id,
                spent=Budget(usd_micros=10),
                produced=[],
                requested=Budget(usd_micros=20),
            )
        raise AssertionError(kind)

    table = [
        ("commit", "approve", "approved"),
        ("commit", "edit", "approved_with_edit"),
        ("commit", "reject", "rejected"),
        ("question", "answer", "answered"),
        ("question", "abort", "rejected"),
        ("verification_failure", "retry", "approved"),
        ("verification_failure", "cancel", "rejected"),
        ("budget_increase", "grant", "approved"),
        ("budget_increase", "deny", "rejected"),
    ]
    for kind, option, expected in table:
        card = fresh(kind)
        await approvals.issue_card(kernel, card)
        edit = {"premise": "narrower"} if option == "edit" else None
        record = await approvals.mint(
            kernel, reply_to(card, option, session, edit=edit)
        )
        assert record.kind == expected, (kind, option)
        assert record.objective_id == objective.id
        assert record.contract_revision == 1
        assert record.space == space and record.argument_sha256 is None

    # An 'edit' with nothing edited is refused before the row is written.
    card = fresh("commit")
    await approvals.issue_card(kernel, card)
    with pytest.raises(ValueError):
        await approvals.mint(kernel, reply_to(card, "edit", session))
    with pytest.raises(approvals.UnknownOption):
        await approvals.mint(kernel, reply_to(card, "maybe", session))

    # A route card's options are the candidate space ids, and any of them answers.
    route = approvals.route_inbound_card(inbound_item(space), ["alpha", "beta"])
    await approvals.issue_card(kernel, route)
    answered = await approvals.mint(kernel, reply_to(route, "beta", session))
    assert answered.kind == "answered"
    assert answered.objective_id is None and answered.contract_revision is None
    await kernel.commit()


async def test_mint_uses_wall_clock_not_reply_at(
    kernel, space, spaces, surface, session
):
    """Critique 7: `reply.at` is the adapter's clock and the adapter is
    outside the trust boundary."""
    conversation = await approvals.open_conversation(kernel, space)
    objective = await objective_of(kernel, conversation)
    card = approvals.question_card(objective, brief_of(objective), question_of())
    await approvals.issue_card(kernel, card)

    before = datetime.now(UTC)
    long_ago = datetime(2020, 1, 1, tzinfo=UTC)
    record = await approvals.mint(
        kernel, reply_to(card, "answer", session, at=long_ago)
    )
    assert before <= record.decided_at <= datetime.now(UTC)
    assert record.decided_at != long_ago
    await kernel.commit()


async def test_mint_payload_carries_reply_text(kernel, space, spaces, surface, session):
    from kernel.events import read

    conversation = await approvals.open_conversation(kernel, space)
    objective = await objective_of(kernel, conversation)
    card = approvals.question_card(objective, brief_of(objective), question_of())
    await approvals.issue_card(kernel, card)
    record = await approvals.mint(
        kernel,
        reply_to(card, "answer", session, text="Tom", raw_message="#1 answer Tom"),
    )
    await kernel.commit()

    assert record.text == "Tom" and record.raw_message == "#1 answer Tom"
    events = await read(kernel, space_id=space, types=["approval.minted"])
    assert len(events) == 1
    assert events[0].payload["text"] == "Tom"
    assert events[0].payload["approval"] == record.model_dump(mode="json")


async def test_mint_refuses_second_reply_as_already_decided(
    kernel, space, spaces, surface, session
):
    conversation = await approvals.open_conversation(kernel, space)
    objective = await objective_of(kernel, conversation)
    card = approvals.question_card(objective, brief_of(objective), question_of())
    await approvals.issue_card(kernel, card)
    await approvals.mint(kernel, reply_to(card, "answer", session))
    with pytest.raises(approvals.AlreadyDecided):
        await approvals.mint(kernel, reply_to(card, "abort", session))
    # The savepoint kept the transaction alive, so the first decision stands.
    await kernel.commit()
    rows = await (
        await kernel.execute(
            "SELECT kind FROM approvals WHERE card_id = %s", (card.id,)
        )
    ).fetchall()
    assert rows == [("answered",)]


async def test_mint_refuses_expired_card_before_and_after_expire_due(
    kernel, space, spaces, surface, session
):
    """`CardExpired` both times, whether or not a sweep has run (critique 7).

    The expiry is already past when the card is written, rather than a second
    away behind a sleep, so the refusal is a fact of the row and not of how
    long the rest of the suite took to reach this line. `expire_due` sweeps
    every space, because `supervisor.serve` calls it once a minute for the
    whole process (seams §3.3), so the assertion names this card instead of
    the whole return.
    """
    conversation = await approvals.open_conversation(kernel, space)
    # The tree refuses a contract whose deadline has already passed, so the
    # objective keeps a real future deadline and the card's own expiry is
    # what moves back.
    objective = await objective_of(
        kernel, conversation, deadline=datetime.now(UTC) + timedelta(hours=1)
    )
    card = approvals.question_card(
        objective, brief_of(objective), question_of()
    ).model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
    await approvals.issue_card(kernel, card)
    await kernel.commit()

    # The wall clock is past the expiry; no sweep has run.
    with pytest.raises(approvals.CardExpired):
        await approvals.mint(kernel, reply_to(card, "answer", session))
    assert card.id in await approvals.expire_due(kernel)
    with pytest.raises(approvals.CardExpired):
        await approvals.mint(kernel, reply_to(card, "answer", session))
    await kernel.commit()


async def test_self_approve_writes_unissued_card_and_record_with_budget_and_basis(
    kernel, space, spaces, surface
):
    from kernel.events import read

    conversation = await approvals.open_conversation(kernel, space)
    objective = await objective_of(kernel, conversation, budget=3_000_000)
    record = await approvals.self_approve(kernel, objective.id, 1)
    await kernel.commit()

    assert record.kind == "self_approved"
    assert record.session_id == "kernel" and record.raw_message == ""
    assert record.objective_id == objective.id and record.contract_revision == 1
    row = await (
        await kernel.execute(
            "SELECT kind, issued, fields FROM cards WHERE id = %s", (record.card_id,)
        )
    ).fetchone()
    kind, issued, fields = row
    assert kind == "commit" and issued is False
    assert fields["budget"] == "$3.00"
    assert fields["basis"] == "one Executor turn and one Verifier turn"
    # Nothing was shown and no card.issued was appended: nothing was shown.
    assert surface.shown == []
    assert await read(kernel, space_id=space, types=["card.issued"]) == []
    minted = await read(kernel, space_id=space, types=["approval.minted"])
    assert [e.payload["text"] for e in minted] == [None]
    # The record is authority the tree accepts.
    approvals.check(record, contract_revision=1)
    # A stale revision on the call itself is refused before anything is written.
    with pytest.raises(approvals.StaleRevision):
        await approvals.self_approve(kernel, objective.id, 2)
    await kernel.rollback()


async def test_self_approve_refuses_act(kernel, space, spaces, surface):
    """Architecture §2: the supervisor cannot self-approve above class 1."""
    conversation = await approvals.open_conversation(kernel, space)
    objective = await objective_of(kernel, conversation, max_effect_class="act")
    with pytest.raises(ValueError):
        await approvals.self_approve(kernel, objective.id, 1)
    await kernel.rollback()


def record(kind="approved", *, revision=1, digest=None):
    return ApprovalRecord(
        id=new_id(),
        card_id=new_id(),
        kind=kind,
        space="psyoptimal",
        objective_id="obj-1",
        contract_revision=revision,
        argument_sha256=digest,
        raw_message="",
        session_id="s1",
        decided_at=datetime.now(UTC),
    )


def action(key="a", space="psyoptimal"):
    return PushBranch(
        space=space,
        objective_id="obj-1",
        brief_id="brief-1",
        repo="yudame/cori-sandbox",
        branch=key,
        source_dir="/tmp/worktree",
        head_sha="0" * 40,
    )


def test_check_refuses_stale_revision_and_argument_mismatch():
    batch = [action("a"), action("b")]
    digest = approvals.argument_digest(batch)
    held = record(revision=2, digest=digest)

    approvals.check(held)
    approvals.check(held, contract_revision=2, argument_sha256=digest)
    with pytest.raises(approvals.StaleRevision):
        approvals.check(held, contract_revision=3)
    # Order is part of what the person approved.
    reordered = approvals.argument_digest(list(reversed(batch)))
    assert reordered != digest
    with pytest.raises(approvals.ArgumentMismatch):
        approvals.check(held, argument_sha256=reordered)
    assert approvals.argument_digest(batch) == digest


def test_check_refuses_answered_rejected_expired():
    for kind in ("answered", "rejected", "expired"):
        with pytest.raises(approvals.NotAnApproval):
            approvals.check(record(kind))
        with pytest.raises(approvals.NotAnApproval):
            approvals.check(record(kind), contract_revision=1)
    for kind in ("approved", "approved_with_edit", "self_approved"):
        approvals.check(record(kind), contract_revision=1)


def test_check_refuses_a_digest_against_a_record_without_one():
    """Nothing bound the record to a batch, so it approves no batch."""
    held = record(digest=None)
    approvals.check(held)
    with pytest.raises(approvals.ArgumentMismatch):
        approvals.check(held, argument_sha256=approvals.argument_digest([action()]))


class TestMintOnBudgetIncrease:
    """A grant is the objective's approval, bound to the revision the person
    saw. The class name carries the task 5 selector."""

    async def test_budget_increase_grant_is_approved_bound_to_revision(
        self, kernel, space, spaces, surface, session
    ):
        conversation = await approvals.open_conversation(kernel, space)
        objective = await objective_of(kernel, conversation)
        card = approvals.budget_increase_card(
            objective,
            new_id(),
            spent=Budget(usd_micros=1_000_000),
            produced=["/repo/a.py"],
            requested=Budget(usd_micros=500_000),
        )
        await approvals.issue_card(kernel, card)
        granted = await approvals.mint(kernel, reply_to(card, "grant", session))
        assert granted.kind == "approved" and granted.contract_revision == 1
        approvals.check(granted, contract_revision=1)

        revision = await tree.revise_contract(
            kernel, objective.id, contract(budget=2_000_000)
        )
        assert revision == 2
        with pytest.raises(approvals.StaleRevision):
            approvals.check(granted, contract_revision=revision)
        await kernel.commit()


# ---------------------------------------------------------------------------
# Consume and expire


async def consumed_events(conn, space, approval_id):
    from kernel.events import read_for

    events = await read_for(conn, space_id=space, key="approval_id", value=approval_id)
    return [e for e in events if e.type == "approval.consumed"]


async def answered_card(kernel, space, session, *, objective=None, conversation=None):
    """One issued question card with one minted answer, committed."""
    if conversation is None:
        conversation = await approvals.open_conversation(kernel, space)
    if objective is None:
        objective = await objective_of(kernel, conversation)
    card = approvals.question_card(objective, brief_of(objective), question_of())
    await approvals.issue_card(kernel, card)
    record = await approvals.mint(kernel, reply_to(card, "answer", session))
    await kernel.commit()
    return card, record


async def test_consume_appends_event_with_by_equal_to_regards(
    kernel, space, spaces, surface, session
):
    card, record = await answered_card(kernel, space, session)
    await approvals.consume(kernel, record.id)
    await kernel.commit()

    events = await consumed_events(kernel, space, record.id)
    assert len(events) == 1
    assert events[0].payload == {"approval_id": record.id, "by": card.regards}

    with pytest.raises(approvals.AlreadyConsumed):
        await approvals.consume(kernel, record.id)
    await kernel.rollback()
    assert len(await consumed_events(kernel, space, record.id)) == 1
    with pytest.raises(approvals.UnknownApproval):
        await approvals.consume(kernel, new_id())
    await kernel.rollback()


async def test_sixteen_concurrent_consumes_succeed_exactly_once(
    kernel, space, spaces, surface, session
):
    """Sixteen connections, each in its own transaction, as spike 01's race
    did. `single_flight` serializes them and the count decides."""
    _, record = await answered_card(kernel, space, session)

    async def once():
        async with await psycopg.AsyncConnection.connect(dsn("kernel_rw")) as conn:
            try:
                await approvals.consume(conn, record.id)
            except approvals.AlreadyConsumed:
                await conn.rollback()
                return "already"
            await conn.commit()
            return "consumed"

    outcomes = await asyncio.gather(*(once() for _ in range(16)))
    assert sorted(outcomes) == ["already"] * 15 + ["consumed"]
    assert len(await consumed_events(kernel, space, record.id)) == 1


async def test_expire_due_writes_record_and_event_and_transitions_nothing(
    kernel, space, spaces, surface
):
    """Seams Round two: the card's expiry closes the card and nothing else.
    `tree.expire_due` cancels a live node past its deadline on the same
    sweep, and a card on a node that is already terminal expires the same
    way (critique 1)."""
    from kernel.events import read

    conversation = await approvals.open_conversation(kernel, space)
    deadline = datetime.now(UTC) + timedelta(hours=1)
    live = await objective_of(kernel, conversation, deadline=deadline)
    failed = await objective_of(kernel, conversation, deadline=deadline)
    for state, reason, by in (
        ("APPROVED", "approved", "approve"),
        ("RUNNING", "running", "delegate"),
        ("FAILED", "worker_failed", "test"),
    ):
        await force_state(kernel, failed.id, state, reason, by=by)
    failed = await tree.project(kernel, failed.id)
    assert failed.state == "FAILED"

    cards = []
    for objective in (live, failed):
        card = approvals.question_card(objective, brief_of(objective), question_of())
        await approvals.issue_card(kernel, card)
        cards.append(card)
    await kernel.commit()
    before = len(await read(kernel, space_id=space, types=["objective.state_changed"]))

    after_deadline = deadline + timedelta(seconds=1)
    closed = await approvals.expire_due(kernel, now=after_deadline)
    await kernel.commit()
    assert {c.id for c in cards} <= set(closed)

    for card in cards:
        row = await (
            await kernel.execute(
                "SELECT kind, session_id, objective_id FROM approvals "
                "WHERE card_id = %s",
                (card.id,),
            )
        ).fetchone()
        assert row == ("expired", "kernel", card.regards)
    expired = await read(kernel, space_id=space, types=["card.expired"])
    assert [e.payload["card_id"] for e in expired] == [c.id for c in cards]
    # Nothing transitioned: the tree's own sweep does that, on its own reason.
    assert (
        len(await read(kernel, space_id=space, types=["objective.state_changed"]))
        == before
    )
    assert (await tree.project(kernel, live.id)).state == "FRAMED"
    assert (await tree.project(kernel, failed.id)).state == "FAILED"

    # A rerun is a no-op: the unique constraint makes it one.
    assert set(await approvals.expire_due(kernel, now=after_deadline)).isdisjoint(
        {c.id for c in cards}
    )
    await kernel.commit()
    assert len(await read(kernel, space_id=space, types=["card.expired"])) == 2


async def test_expire_due_leaves_a_decided_card_alone(
    kernel, space, spaces, surface, session
):
    card, _ = await answered_card(kernel, space, session)
    closed = await approvals.expire_due(
        kernel, now=datetime.now(UTC) + timedelta(days=30)
    )
    await kernel.commit()
    assert card.id not in closed
    rows = await (
        await kernel.execute(
            "SELECT kind FROM approvals WHERE card_id = %s", (card.id,)
        )
    ).fetchall()
    assert rows == [("answered",)]
