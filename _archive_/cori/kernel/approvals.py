"""The person's end of the harness: sessions, conversations, cards, and the
approval records only the kernel mints. Seams §3.7; architecture §7; tech
stack §13.

Every line of authority in the system ends at the person, and this module is
where that line is authenticated. Three rules hold it together:

- **The kernel mints.** An `ApprovalRecord` is constructed here and nowhere
  else (blind-spot review finding 12), from a `Reply` an adapter relayed over
  a session the kernel opened. No adapter code can create authority, which is
  why approval adapters may sit outside the trust boundary (tech stack §10).
- **The kernel renders.** A card is built by a constructor in this module
  from structured fields (architecture §7). Agent prose reaches the person
  only in the marked, length-capped `note` region, so no worker writes a
  field the person reads as the kernel's own.
- **An approval is bound and consumed once.** A record carries the contract
  revision the person said yes to and, from M2, the digest of the exact
  argument batch. `check` refuses a stale revision or a changed argument, and
  `consume` refuses a second use (architecture §8, README "Effects have
  classes").

Module state is small and deliberate: the live session set, the attached
surface, and the current conversation per space. All three belong to one
process's terminal and none of them is authority; every fact that matters is
a row or an event.
"""

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from kernel.events import append, read_for, single_flight
from ports.surface import ApprovalSurface
from schemas.approval import (
    CARD_FIELDS,
    OBJECTIVE_CARD_KINDS,
    ApprovalKind,
    ApprovalRecord,
    Card,
    CardKind,
    Conversation,
    Option,
    Reply,
    render_instant,
    render_value,
)
from schemas.brief import Brief
from schemas.budget import Budget
from schemas.effect import EffectOutcome
from schemas.ids import (
    ApprovalId,
    BriefId,
    CardId,
    ConversationId,
    ObjectiveId,
    SpaceId,
    new_id,
)
from schemas.inbound import InboundItem
from schemas.objective import Objective
from schemas.report import Verdict
from schemas.space import UNASSIGNED_SPACE_ID
from schemas.trace import Question

# ---------------------------------------------------------------------------
# Exceptions


class NotATerminal(ValueError):
    """`open_terminal_session` on a pipe, or on a tty another user owns."""


class UnknownSession(ValueError):
    """A reply whose session id is not one this process opened."""


class UnknownSpace(ValueError):
    """A switch to a space with no manifest and not `unassigned`."""


class UnknownCard(ValueError):
    """A reply naming a card id with no row."""


class UnknownOption(ValueError):
    """An option key outside the kind's table."""


class AlreadyDecided(ValueError):
    """A second decision on one card. The unique constraint is the arbiter."""


class AlreadyConsumed(ValueError):
    """A second consumption of one approval."""


class CardExpired(ValueError):
    """A reply at a wall clock past the card's expiry."""


class SpaceMismatch(ValueError):
    """A card issued into a conversation of another space."""


class NoConversation(ValueError):
    """A constructor that must name a conversation, in a space with none."""


class UnknownApproval(ValueError):
    """`consume` on an id with no `approvals` row."""


class NotAnApproval(ValueError):
    """`check` on a record whose kind carries information, not authority."""


class StaleRevision(ValueError):
    """`check` against a contract revision the person never saw."""


class ArgumentMismatch(ValueError):
    """`check` against a digest the record does not carry."""


# ---------------------------------------------------------------------------
# Module state: the terminal this process owns

# session id -> (uid, tty). Opened by `open_terminal_session`, read by `mint`
# to refuse a reply from anywhere else.
_sessions: dict[str, tuple[int, str]] = {}
_recorded: set[str] = set()
_surface: ApprovalSurface | None = None
# space -> the conversation the person is in. Set by `open_conversation`,
# `latest_conversation`, and `switch_space`; read by a constructor that
# receives no conversation of its own.
_current: dict[SpaceId, Conversation] = {}


def open_terminal_session(fd: int = 0) -> str:
    """A session id for a terminal this process's user owns.

    Tech stack §13: on the local surface an approval is an interactive
    confirmation the kernel attributes to a terminal session under the
    person's user, never a file or socket write a worker could make. A pipe
    is refused because a worker can hold one end of a pipe and cannot hold a
    tty the person is logged in to.
    """
    if not os.isatty(fd):
        raise NotATerminal(
            f"fd {fd} is not a terminal; an approval is an interactive "
            "confirmation, never a file or socket write (tech stack §13)"
        )
    uid = os.fstat(fd).st_uid
    if uid != os.getuid():
        raise NotATerminal(
            f"fd {fd} is a terminal owned by uid {uid}, not {os.getuid()}"
        )
    session_id = new_id()
    _sessions[session_id] = (uid, os.ttyname(fd))
    return session_id


def session_is_live(session_id: str) -> bool:
    return session_id in _sessions


def attach(surface: ApprovalSurface) -> None:
    """Once at boot. `issue_card` and `open_conversation` reach the person
    through the surface attached here."""
    global _surface
    _surface = surface


def _attached() -> ApprovalSurface | None:
    return _surface


async def record_session(conn, space: SpaceId) -> None:
    """Append `session.opened` for every live session not yet recorded.

    Seam amendment 11: `open_terminal_session` has no connection and a read
    must not write, so the event is one explicit call the entry point makes
    after it has chosen the conversation, into that conversation's space. A
    second call for the same session appends nothing.
    """
    for session_id, (uid, tty) in _sessions.items():
        if session_id in _recorded:
            continue
        await append(
            conn,
            space_id=space,
            type="session.opened",
            payload={"session_id": session_id, "uid": uid, "tty": tty},
        )
        _recorded.add(session_id)


# ---------------------------------------------------------------------------
# Conversations


async def _insert_conversation(conn, conversation: Conversation) -> None:
    await conn.execute(
        "INSERT INTO conversations (id, space_id, opened_at) VALUES (%s, %s, %s)",
        (conversation.id, conversation.space, conversation.opened_at),
    )


async def _open(conn, space: SpaceId, conversation_id: ConversationId) -> Conversation:
    conversation = Conversation(
        id=conversation_id, space=space, opened_at=datetime.now(UTC)
    )
    await _insert_conversation(conn, conversation)
    await append(
        conn,
        space_id=space,
        type="conversation.opened",
        payload={"conversation": conversation.model_dump(mode="json")},
    )
    _current[space] = conversation
    surface = _attached()
    if surface is not None:
        await surface.open(conversation)
    return conversation


async def open_conversation(conn, space: SpaceId) -> Conversation:
    """A new conversation in one space: the row, `conversation.opened`, and
    the surface's `open`. Architecture §7: a conversation is always in one
    space, and every card names it."""
    return await _open(conn, space, new_id())


async def latest_conversation(conn, space: SpaceId) -> Conversation | None:
    """The newest conversation of a space, or None. A read: it writes no row
    and appends no event (plan 10, critique 13).

    Ids are UUIDv7 and sort by mint time (seams §0), so `ORDER BY id DESC` is
    "newest" without a second column. A restart is a resume, so this is what
    the entry point calls before it considers opening one.
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT id, space_id, opened_at FROM conversations "
            "WHERE space_id = %s ORDER BY id DESC LIMIT 1",
            (space,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    conversation = Conversation(
        id=row["id"], space=row["space_id"], opened_at=row["opened_at"]
    )
    _current[space] = conversation
    return conversation


def current_conversation(space: SpaceId) -> Conversation:
    """The conversation the person is in, for a constructor that receives
    none. `NoConversation` when the entry point has opened none in `space`."""
    conversation = _current.get(space)
    if conversation is None:
        raise NoConversation(f"no conversation is open in space {space!r}")
    return conversation


async def switch_space(
    conn, conversation_id: ConversationId, to_space: SpaceId
) -> Conversation:
    """Seal the current thread to its space and start a new one.

    Architecture §1: a space switch seals the current thread and starts a new
    one. The `space.switched` event carries `new_conversation_id`, so a
    reader of the old conversation finds its successor without scanning
    (amendment 4). `unassigned` is a legal target, because the person needs
    somewhere to see a `route_inbound` card; opening an objective there stays
    refused by the tree (seams §0).
    """
    from kernel.spaces import load_all

    if to_space != UNASSIGNED_SPACE_ID and to_space not in load_all():
        raise UnknownSpace(f"space {to_space!r} has no manifest")
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT id, space_id, opened_at FROM conversations WHERE id = %s",
            (conversation_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise NoConversation(f"no conversation {conversation_id!r}")
    from_space = row["space_id"]
    new_id_ = new_id()
    async with single_flight(conn, f"thread:{conversation_id}"):
        await append(
            conn,
            space_id=from_space,
            type="space.switched",
            payload={
                "conversation_id": conversation_id,
                "from": from_space,
                "to": to_space,
                "new_conversation_id": new_id_,
            },
        )
    return await _open(conn, to_space, new_id_)


# ---------------------------------------------------------------------------
# Card rows


def _row_to_card(row: dict[str, Any]) -> Card:
    return Card(
        id=row["id"],
        kind=row["kind"],
        space=row["space_id"],
        conversation_id=row["conversation_id"],
        regards=row["regards"],
        contract_revision=row["contract_revision"],
        fields=row["fields"],
        options=[Option(**option) for option in row["options"]],
        note=row["note"],
        expires_at=row["expires_at"],
        issued_at=row["issued_at"],
    )


async def _card_row(conn, card_id: CardId) -> dict[str, Any]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("SELECT * FROM cards WHERE id = %s", (card_id,))
        row = await cur.fetchone()
    if row is None:
        raise UnknownCard(f"no card {card_id!r}")
    return row


def _objective_id_of(card: Card) -> ObjectiveId | None:
    """An objective card is one whose `regards` is an objective id (plan 10,
    critique 10). The column, not the kind, is what the lock, the revision
    binding, and the expiry sweep key on; the kinds here are the ones whose
    constructors set it."""
    return card.regards if card.kind in OBJECTIVE_CARD_KINDS else None


async def _insert_card(conn, card: Card, *, issued: bool) -> None:
    await conn.execute(
        "INSERT INTO cards (id, kind, space_id, conversation_id, regards, "
        "objective_id, contract_revision, fields, options, note, expires_at, "
        "issued_at, issued) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            card.id,
            card.kind,
            card.space,
            card.conversation_id,
            card.regards,
            _objective_id_of(card),
            card.contract_revision,
            Jsonb(card.fields),
            Jsonb([option.model_dump(mode="json") for option in card.options]),
            card.note,
            card.expires_at,
            card.issued_at,
            issued,
        ),
    )


async def pending_cards(conn, conversation_id: ConversationId) -> list[Card]:
    """Issued, undecided, unexpired cards of one conversation, oldest first.

    The entry point re-shows these at start, so a restart loses no card the
    person had not answered (architecture §1: a kill is lossless for durable
    state).
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT c.* FROM cards c LEFT JOIN approvals a ON a.card_id = c.id "
            "WHERE c.conversation_id = %s AND c.issued AND a.id IS NULL "
            "AND c.expires_at > %s ORDER BY c.id",
            (conversation_id, datetime.now(UTC)),
        )
        rows = await cur.fetchall()
    return [_row_to_card(row) for row in rows]


# ---------------------------------------------------------------------------
# Card constructors: the kernel renders, the caller supplies facts
#
# Architecture §7: a card is rendered by the kernel from structured fields.
# Every constructor here fills `CARD_FIELDS[kind]` exactly, in that order,
# through `render_value`, so an adapter formats nothing. `note` is empty:
# the seams give no constructor a prose argument, and the fields a worker
# supplies (a question, a verdict summary) have their own keys.

SEVEN_DAYS = timedelta(days=7)


def _fields(kind: CardKind, **values: Any) -> dict[str, str]:
    """`CARD_FIELDS[kind]` in order, each value through `render_value`."""
    rendered = {key: render_value(values[key]) for key in CARD_FIELDS[kind]}
    for key, value in values.items():
        if key not in rendered:
            rendered[key] = render_value(value)
    return rendered


def _objective_card(
    kind: CardKind,
    objective: Objective,
    *,
    fields: dict[str, str],
    options: list[Option],
) -> Card:
    """The four kinds that regard an objective: `regards` is the objective id,
    `contract_revision` is its current revision, and the expiry is the
    contract's deadline (architecture §7)."""
    return Card(
        id=new_id(),
        kind=kind,
        space=objective.space,
        conversation_id=objective.conversation_id,
        regards=objective.id,
        contract_revision=objective.contract_revision,
        fields=fields,
        options=options,
        note="",
        expires_at=objective.contract.ceilings.deadline,
        issued_at=datetime.now(UTC),
    )


def question_card(objective: Objective, brief: Brief, question: Question) -> Card:
    """A worker asked and the supervisor chose to relay rather than answer.
    `answer` is recommended because the worker is waiting."""
    return _objective_card(
        "question",
        objective,
        fields=_fields(
            "question",
            brief_id=brief.id,
            question_id=question.question_id,
            question=question.text,
        ),
        options=[
            Option(key="answer", label="Answer the worker", recommended=True),
            Option(key="abort", label="Abort the worker"),
        ],
    )


def verification_failure_card(
    objective: Objective, brief: Brief | BriefId, verdict: Verdict
) -> Card:
    """The Verifier failed or abstained. `brief` is the Executor brief, or its
    id from the `verdict.recorded` payload when the verdict is the kernel's
    and there is no Brief object (Seam amendments 13).

    `failed_criteria` is every criterion the verdict did not meet, an abstain
    (`met is None`) included, because an abstain is what the person is being
    asked about as much as a failure.
    """
    brief_id = brief.id if isinstance(brief, Brief) else brief
    failed = [c.criterion for c in verdict.criteria if c.met is not True]
    return _objective_card(
        "verification_failure",
        objective,
        fields=_fields(
            "verification_failure",
            brief_id=brief_id,
            verdict_summary=verdict.summary,
            failed_criteria=failed,
        ),
        options=[
            Option(key="retry", label="Retry the work", recommended=True),
            Option(key="cancel", label="Cancel the objective"),
        ],
    )


def budget_increase_card(
    objective: Objective,
    brief_id: BriefId,
    spent: Budget,
    produced: list[str],
    requested: Budget,
) -> Card:
    """An Executor ran out of money. The person sees what it cost, what it
    left behind, and what the supervisor asks for, all in dollars
    (architecture §4: overrun is a card, and the person reads money)."""
    return _objective_card(
        "budget_increase",
        objective,
        fields=_fields(
            "budget_increase",
            brief_id=brief_id,
            spent=spent,
            produced=produced,
            requested=requested,
        ),
        options=[
            Option(key="grant", label="Grant the increase", recommended=True),
            Option(key="deny", label="Deny and cancel"),
        ],
    )


def commit_card(objective: Objective, audience: str) -> Card:
    """The contract in the form the person approves.

    `stop_conditions` is the canonical JSON of the contract's ceilings,
    because `Contract` (seams §1.4) has no `stop_conditions` field and the
    ceilings are what stops a node (Findings 6, for the lead).
    """
    contract = objective.contract
    ceilings = contract.ceilings
    return _objective_card(
        "commit",
        objective,
        fields=_fields(
            "commit",
            premise=contract.premise,
            non_goals=contract.non_goals,
            success_criteria=contract.success_criteria,
            budget=contract.budget,
            basis=contract.basis,
            effect_ceiling=ceilings.max_effect_class,
            deadline=render_instant(ceilings.deadline),
            stop_conditions={
                "deadline": render_instant(ceilings.deadline),
                "max_effect_class": ceilings.max_effect_class,
                "max_data_class": ceilings.max_data_class,
            },
            audience=audience,
        ),
        options=[
            Option(key="approve", label="Approve and start", recommended=True),
            Option(key="edit", label="Edit the contract"),
            Option(key="reject", label="Reject"),
        ],
    )


def route_inbound_card(item: InboundItem, candidates: list[SpaceId]) -> Card:
    """Which space an unassigned item belongs to.

    The first candidate is recommended, because the router ordered them. A
    card offers two or three options (seams §1.12): one candidate is paired
    with `keep_unassigned`, so the person can decline, and more than three
    are cut to the first three. Nothing issues one of these at M0; the
    constructor arrives with the plan that does (critique 11).
    """
    if not candidates:
        raise ValueError("a route card offers at least one candidate space")
    options = [
        Option(key=space_id, label=f"Assign to {space_id}", recommended=index == 0)
        for index, space_id in enumerate(candidates[:3])
    ]
    if len(options) == 1:
        options.append(Option(key="keep_unassigned", label="Leave it unassigned"))
    issued_at = datetime.now(UTC)
    return Card(
        id=new_id(),
        kind="route_inbound",
        space=item.space,
        conversation_id=current_conversation(item.space).id,
        regards=item.id,
        contract_revision=None,
        fields=_fields("route_inbound", headers=item.headers, candidates=candidates),
        options=options,
        note="",
        expires_at=issued_at + SEVEN_DAYS,
        issued_at=issued_at,
    )


def unknown_outcome_card(outcome: EffectOutcome) -> Card:
    """An effect whose state on the target is in doubt.

    `outcome.result` carries `space_id`, `action_type`, and
    `idempotency_key` from the intent row (Seam amendments 12), and the card
    goes to the space's current conversation, since `EffectOutcome` names no
    conversation. `done` is recommended because the card exists so the person
    can look at the target; `rerun` and `drop` are the two errors spike 02
    measured, so neither is the default. Expiry is seven days, decided in the
    plan, since the constructor holds no objective deadline.
    """
    result = outcome.result
    space = result["space_id"]
    issued_at = datetime.now(UTC)
    return Card(
        id=new_id(),
        kind="unknown_outcome",
        space=space,
        conversation_id=current_conversation(space).id,
        regards=outcome.effect_id,
        contract_revision=None,
        fields=_fields(
            "unknown_outcome",
            effect_id=outcome.effect_id,
            action_type=result["action_type"],
            idempotency_key=result["idempotency_key"],
        ),
        options=[
            Option(key="done", label="It is there; mark it done", recommended=True),
            Option(key="rerun", label="It is not there; run it again"),
            Option(key="drop", label="Drop it"),
        ],
        note="",
        expires_at=issued_at + SEVEN_DAYS,
        issued_at=issued_at,
    )


async def issue_card(conn, card: Card, *, now: datetime | None = None) -> None:
    """Show a card to the person and record that it was shown.

    Under `single_flight("objective:<id>")` for an objective card, so a card
    binding a contract revision cannot be issued while the contract is being
    revised; no lock otherwise, since a non-objective card binds nothing.
    The card's space must be the conversation's (`SpaceMismatch`): a card is
    the person's view of one space and nothing crosses that line.

    Charges nothing (seams version 3, item 1). Whether a card is worth the
    person's attention is the caller's judgment; what the kernel keeps is the
    record that it was issued.
    """
    del now  # the seams' parameter; nothing derived from the clock is charged
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT space_id FROM conversations WHERE id = %s",
            (card.conversation_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise NoConversation(f"no conversation {card.conversation_id!r}")
    if row["space_id"] != card.space:
        raise SpaceMismatch(
            f"a {card.kind} card in space {card.space!r} cannot be issued into "
            f"conversation {card.conversation_id!r}, which is in {row['space_id']!r}"
        )

    async def write() -> None:
        await _insert_card(conn, card, issued=True)
        await append(
            conn,
            space_id=card.space,
            type="card.issued",
            payload={"card": card.model_dump(mode="json")},
        )

    objective_id = _objective_id_of(card)
    if objective_id is None:
        await write()
    else:
        async with single_flight(conn, f"objective:{objective_id}"):
            await write()
    surface = _attached()
    if surface is not None:
        await surface.show(card)


# ---------------------------------------------------------------------------
# Approvals: the kernel mints, and nothing else does
#
# One table maps a card kind and the option the person picked to the kind of
# record it mints (plan 10, "Options and the approval kind"; seams §1.12).
# `answered` is the kind for any reply that supplies information rather than
# authority, which is why `check` refuses it.

OPTION_KINDS: dict[CardKind, dict[str, ApprovalKind]] = {
    "commit": {
        "approve": "approved",
        "edit": "approved_with_edit",
        "reject": "rejected",
    },
    "question": {"answer": "answered", "abort": "rejected"},
    "verification_failure": {"retry": "approved", "cancel": "rejected"},
    "budget_increase": {"grant": "approved", "deny": "rejected"},
    "unknown_outcome": {"done": "answered", "rerun": "approved", "drop": "rejected"},
    # `route_inbound` options are the candidate space ids, so the kind comes
    # from the card's own option list rather than from a fixed key.
    "route_inbound": {},
}

# The kinds that carry authority. Everything else informs (critique 8).
APPROVING: frozenset[str] = frozenset(
    {"approved", "approved_with_edit", "self_approved"}
)


def _kind_for(card_kind: CardKind, option: str, offered: list[str]) -> ApprovalKind:
    if option not in offered:
        raise UnknownOption(
            f"{option!r} is not an option on this {card_kind} card; it offered {offered}"
        )
    if card_kind == "route_inbound":
        return "answered"
    return OPTION_KINDS[card_kind][option]


async def _insert_approval(conn, record: ApprovalRecord) -> None:
    """The insert, with the unique violation on `card_id` named for what it
    means. A savepoint, so the caller's transaction survives the refusal."""
    try:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO approvals (id, card_id, kind, space_id, objective_id, "
                "contract_revision, argument_sha256, raw_message, session_id, "
                "decided_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    record.id,
                    record.card_id,
                    record.kind,
                    record.space,
                    record.objective_id,
                    record.contract_revision,
                    record.argument_sha256,
                    record.raw_message,
                    record.session_id,
                    record.decided_at,
                ),
            )
    except psycopg.errors.UniqueViolation as exc:
        raise AlreadyDecided(
            f"card {record.card_id} already carries a decision"
        ) from exc


async def _mint_event(conn, record: ApprovalRecord, text: str | None) -> None:
    await append(
        conn,
        space_id=record.space,
        type="approval.minted",
        payload={"approval": record.model_dump(mode="json"), "text": text},
    )


async def mint(conn, reply: Reply) -> ApprovalRecord:
    """The one constructor of an `ApprovalRecord` (blind-spot review finding
    12). An adapter relays a `Reply`; only this turns one into authority.

    The clock is the kernel's wall clock, never `reply.at`, which is the
    adapter's and outside the trust boundary (critique 7). A reply past the
    card's expiry is `CardExpired` whether or not `expire_due` has swept, and
    a second reply is `AlreadyDecided`, decided by the unique constraint on
    `approvals.card_id` rather than by a read this function could race.
    """
    if not session_is_live(reply.session_id):
        raise UnknownSession(
            f"session {reply.session_id!r} is not one this process opened; "
            "an approval is attributed to a terminal session (tech stack §13)"
        )
    row = await _card_row(conn, reply.card_id)
    now = datetime.now(UTC)
    if now > row["expires_at"]:
        raise CardExpired(
            f"card {reply.card_id} expired at {render_instant(row['expires_at'])}"
        )
    offered = [option["key"] for option in row["options"]]
    kind = _kind_for(row["kind"], reply.option, offered)
    if kind == "approved_with_edit" and reply.edit is None:
        raise ValueError("an 'edit' reply carries the edited contract fields")
    record = ApprovalRecord(
        id=new_id(),
        card_id=reply.card_id,
        kind=kind,
        space=row["space_id"],
        objective_id=row["objective_id"],
        contract_revision=row["contract_revision"],
        argument_sha256=row["fields"].get("argument_sha256"),
        raw_message=reply.raw_message,
        session_id=reply.session_id,
        decided_at=now,
        text=reply.text,
    )
    await _insert_approval(conn, record)
    await _mint_event(conn, record, reply.text)
    return record


async def self_approve(
    conn, objective_id: ObjectiveId, revision: int
) -> ApprovalRecord:
    """The supervisor's own approval of a contract at `read` or `propose`.

    Architecture §2: the supervisor cannot self-approve anything above class
    1. The commit card is written unissued, with no `card.issued` event and
    no `show`, because the row is the record of the contract in the exact
    form the person would have seen, budget and basis included (seams
    §1.12), and nothing was shown.
    """
    from kernel.tree import project

    objective = await project(conn, objective_id)
    ceiling = objective.contract.ceilings.max_effect_class
    if ceiling == "act":
        raise ValueError(
            f"objective {objective_id} has an {ceiling!r} ceiling; the supervisor "
            "cannot self-approve above class 1 (architecture §2)"
        )
    if revision != objective.contract_revision:
        raise StaleRevision(
            f"objective {objective_id} is at revision "
            f"{objective.contract_revision}, not {revision}"
        )
    card = commit_card(objective, audience="sandbox")
    await _insert_card(conn, card, issued=False)
    record = ApprovalRecord(
        id=new_id(),
        card_id=card.id,
        kind="self_approved",
        space=objective.space,
        objective_id=objective.id,
        contract_revision=revision,
        argument_sha256=None,
        raw_message="",
        session_id="kernel",
        decided_at=datetime.now(UTC),
        text=None,
    )
    await _insert_approval(conn, record)
    await _mint_event(conn, record, None)
    return record


def argument_digest(actions: Sequence[Any]) -> str:
    """sha256 over the canonical JSON of the batch, in order.

    Order is part of the digest: a person approving a batch approved the
    sequence, so two batches that differ only in order differ here.
    """
    body = json.dumps(
        [action.model_dump(mode="json") for action in actions],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def check(
    approval: ApprovalRecord,
    *,
    contract_revision: int | None = None,
    argument_sha256: str | None = None,
) -> None:
    """Is this record authority for what is about to happen? Pure.

    Called by `tree.approve` (seams §3.2) and, at M2, by `broker.perform`.
    `answered` is refused with `rejected` and `expired`, because a question's
    answer is information and never a contract approval (critique 8). A
    digest offered against a record that carries none is a mismatch: nothing
    bound that record to a batch, so it approves no batch.
    """
    if approval.kind not in APPROVING:
        raise NotAnApproval(
            f"{approval.kind!r} carries information, not authority; only "
            f"{sorted(APPROVING)} approve"
        )
    if (
        contract_revision is not None
        and approval.contract_revision != contract_revision
    ):
        raise StaleRevision(
            f"approval {approval.id} is for revision {approval.contract_revision}, "
            f"and the contract is at {contract_revision}; any later revision "
            "clears the approval (architecture §2)"
        )
    if argument_sha256 is not None and approval.argument_sha256 != argument_sha256:
        raise ArgumentMismatch(
            f"approval {approval.id} is bound to {approval.argument_sha256!r}, "
            f"not {argument_sha256!r}"
        )


# ---------------------------------------------------------------------------
# Expiry
#
# `expire_due` is named by task 5's acceptance check as well as task 6's, so
# it lands here beside `mint`.


async def expire_due(conn, *, now: datetime | None = None) -> list[CardId]:
    """Close every issued card whose deadline has passed and that carries no
    decision. Returns the card ids closed by this call.

    Transitions nothing (seams Round two). An objective card's `expires_at`
    is the node's deadline, and `tree.expire_due` cancels the node with
    reason `deadline` on the same sweep, so the two coincide without either
    calling the other; a card on a node that is already terminal expires the
    same way (critique 1).

    Idempotent, and safe against a reply arriving in the same instant: the
    unique constraint on `approvals.card_id` decides, and the loser of that
    race is simply not in the returned list.
    """
    moment = now if now is not None else datetime.now(UTC)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT c.id, c.space_id FROM cards c "
            "LEFT JOIN approvals a ON a.card_id = c.id "
            "WHERE c.issued AND a.id IS NULL AND c.expires_at <= %s ORDER BY c.id",
            (moment,),
        )
        due = await cur.fetchall()
    expired: list[CardId] = []
    for row in due:
        card = await _card_row(conn, row["id"])
        record = ApprovalRecord(
            id=new_id(),
            card_id=card["id"],
            kind="expired",
            space=card["space_id"],
            objective_id=card["objective_id"],
            contract_revision=card["contract_revision"],
            argument_sha256=None,
            raw_message="",
            session_id="kernel",
            decided_at=moment,
            text=None,
        )
        try:
            await _insert_approval(conn, record)
        except AlreadyDecided:
            continue
        await append(
            conn,
            space_id=card["space_id"],
            type="card.expired",
            payload={"card_id": card["id"]},
        )
        expired.append(card["id"])
    return expired


# ---------------------------------------------------------------------------
# Consumption


async def consume(conn, approval_id: ApprovalId) -> None:
    """Spend an approval. Once, ever.

    Architecture §8: an approval authorises one thing and is consumed by it.
    Consumption is the event `approval.consumed` and never a column (seams
    §1.12), so the record stays immutable and "consumed once" is a count of
    events rather than a flag someone could set twice. The count is read and
    written under `single_flight("approval:<id>")`, so two callers in the
    same instant serialize and the second sees the first's event.
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT a.space_id, c.regards FROM approvals a JOIN cards c "
            "ON c.id = a.card_id WHERE a.id = %s",
            (approval_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise UnknownApproval(f"no approval {approval_id!r}")
    async with single_flight(conn, f"approval:{approval_id}"):
        spent = await read_for(
            conn, space_id=row["space_id"], key="approval_id", value=approval_id
        )
        if any(event.type == "approval.consumed" for event in spent):
            raise AlreadyConsumed(f"approval {approval_id} was already consumed")
        await append(
            conn,
            space_id=row["space_id"],
            type="approval.consumed",
            payload={"approval_id": approval_id, "by": row["regards"]},
        )
