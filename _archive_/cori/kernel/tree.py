"""The objective tree: nodes, contracts, approvals, Briefs, budgets, and the
generation fence. Plan 02; seams §3.2; architecture §2, §3, §4.

Every function takes the caller's connection and runs inside the caller's
transaction; the one lock a call takes is `events.single_flight` on
`lock_key` of the node it touches, and the caller commits. All SQL is
hand-written and parameterized under `kernel_rw` (tech stack §3). The
projection `Objective` is a pure fold of the `objectives` row, the node's
events, its ledger rows, and its child ids, so replay from the store and
`project` agree by construction.

Late-bound collaborators (plan 02, "Late-bound collaborators"): the tree is
built before `kernel/spaces.py`, `kernel/approvals.py`, and `gateway/`, so
the module-level names below start as `None` and are resolved by a lazy
import on first use. Tests bind fakes from `tests/tree_fakes.py` with
`monkeypatch` before the first call.
"""

import hashlib
import importlib
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Mapping, Protocol

import psycopg
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from infra.models import load as load_models
from kernel.events import append, read_for, single_flight
from schemas.brief import (
    Brief,
    BriefToken,
    ContextSlice,
    DelegateRequest,
    ReportSchemaName,
)
from schemas.budget import DATA_CLASS_RANK, ZERO, Budget, Ceilings
from schemas.capability import (
    Capabilities,
    CapabilityName,
    Refused,
    ceiling,
    issue,
    space_of,
)
from schemas.events import Event
from schemas.ids import (
    ApprovalId,
    BriefId,
    ConversationId,
    ObjectiveId,
    SpaceId,
    TurnId,
    new_id,
)
from schemas.objective import (
    Contract,
    Objective,
    ObjectiveState,
    ReportRef,
    StateReason,
)
from schemas.report import EvidenceRef, Report, ScribeReport, Verdict
from schemas.sandbox import SandboxProfileName, StopReceipt
from schemas.space import EFFECT_RANK, DataClass, EffectClass, Space, Strict
from schemas.trace import Terminal

__all__ = [
    "AlreadyOwned",
    "BudgetExceeded",
    "IllegalTransition",
    "NotLive",
    "Refused",
    "StaleGeneration",
    "StaleRevision",
    "Unconfirmed",
]


# ---------------------------------------------------------------------------
# Exceptions


class TreeError(Exception):
    """Base of the tree's refusals."""


class StaleGeneration(TreeError):
    """A token from before a stop on its node, or for no known Brief."""


class BudgetExceeded(TreeError):
    """An allocation or reservation that does not fit what remains."""


class IllegalTransition(TreeError):
    """A (from, to, reason) outside the state table, or a pair reserved for
    another writer, or a contract change in a state that admits none."""


class StaleRevision(TreeError):
    """An approval bound to a revision that is no longer current."""


class NotLive(TreeError):
    """A node or Brief the call needs live, and it is not, or does not exist."""


class AlreadyOwned(TreeError):
    """A second Executor requested while one is live on the node."""


class Unconfirmed(TreeError):
    """A stopped Brief on the node has no stop receipt yet (tech stack §4)."""


# ---------------------------------------------------------------------------
# Late-bound collaborators

check_may_open: Any = None  # kernel.spaces.check_may_open
root_capabilities: Any = None  # kernel.spaces.root_capabilities
load_all: Any = None  # kernel.spaces.load_all, when a call passes spaces=None
approval_check: Any = None  # kernel.approvals.check
profile_for: Any = None  # infra.sandbox.mounts.profile_for
default_token_issuer: Any = None  # the gateway module, when token_issuer is None

_IMPORTS: dict[str, tuple[str, str | None]] = {
    "check_may_open": ("kernel.spaces", "check_may_open"),
    "root_capabilities": ("kernel.spaces", "root_capabilities"),
    "load_all": ("kernel.spaces", "load_all"),
    "approval_check": ("kernel.approvals", "check"),
    "profile_for": ("infra.sandbox.mounts", "profile_for"),
    "default_token_issuer": ("gateway", None),
}


def _bind(name: str) -> Any:
    """The collaborator `name`, imported on first use and cached in the
    module so a test's `monkeypatch` of the name is what every later call
    sees."""
    value = globals()[name]
    if value is None:
        module_name, attr = _IMPORTS[name]
        module = importlib.import_module(module_name)
        value = module if attr is None else getattr(module, attr)
        globals()[name] = value
    return value


class TokenIssuer(Protocol):
    async def issue_token(
        self, conn, *, brief_id: str, generation: int, model_ref: str, space: SpaceId
    ) -> SecretStr: ...

    async def revoke(self, conn, brief_id: str) -> None: ...


def _spaces(spaces: Mapping[SpaceId, Space] | None) -> Mapping[SpaceId, Space]:
    return spaces if spaces is not None else _bind("load_all")()


# ---------------------------------------------------------------------------
# Locks


def lock_key(node_id: str, kind: Literal["objective", "brief"] = "objective") -> str:
    """`objective:<id>` for an objective, `brief:<id>` for a root Scribe
    (seams v3 §0). An id carries no type, so the caller names the kind."""
    return f"{kind}:{node_id}"


# ---------------------------------------------------------------------------
# The state machine (plan 02, "The state machine")

CANCEL_REASONS: frozenset[StateReason] = frozenset(
    {"deadline", "card_expired", "stopped_by_person", "subtree_revoked", "space_ended"}
)

# The states a node can be cancelled from and that expire_due scans.
LIVE_STATES: frozenset[ObjectiveState] = frozenset(
    {"FRAMED", "AWAITING_APPROVAL", "APPROVED", "RUNNING", "VERIFYING"}
)

TRANSITIONS: dict[tuple[ObjectiveState, ObjectiveState], frozenset[StateReason]] = {
    ("FRAMED", "AWAITING_APPROVAL"): frozenset({"commit"}),
    ("FRAMED", "APPROVED"): frozenset({"approved"}),
    ("AWAITING_APPROVAL", "APPROVED"): frozenset({"approved"}),
    ("FAILED", "APPROVED"): frozenset({"approved"}),
    ("APPROVED", "RUNNING"): frozenset({"running"}),
    ("APPROVED", "AWAITING_APPROVAL"): frozenset(
        {"challenged_assumption", "effect_class_elevation", "awaiting_decision"}
    ),
    ("RUNNING", "AWAITING_APPROVAL"): frozenset(
        {"challenged_assumption", "effect_class_elevation", "awaiting_decision"}
    ),
    ("RUNNING", "VERIFYING"): frozenset({"verifying"}),
    ("RUNNING", "FAILED"): frozenset({"worker_failed", "budget_exhausted"}),
    ("VERIFYING", "SUCCEEDED"): frozenset({"verified", "verification_sampled_out"}),
    ("VERIFYING", "FAILED"): frozenset(
        {"verification_failed", "worker_failed", "budget_exhausted"}
    ),
    ("VERIFYING", "AWAITING_APPROVAL"): frozenset({"awaiting_decision"}),
    **{(s, "CANCELLED"): CANCEL_REASONS for s in LIVE_STATES},
}

# Pairs only one function writes; `transition` refuses them for everyone else.
ONLY: dict[tuple[ObjectiveState, ObjectiveState], str] = {
    ("FRAMED", "APPROVED"): "approve",
    ("AWAITING_APPROVAL", "APPROVED"): "approve",
    ("FAILED", "APPROVED"): "approve",
    ("APPROVED", "RUNNING"): "delegate",
    ("RUNNING", "VERIFYING"): "land_report",
}

# Entering one of these stops the node's live Briefs in the same transaction.
STOPPING: frozenset[ObjectiveState] = frozenset(
    {"CANCELLED", "FAILED", "AWAITING_APPROVAL"}
)

REVISABLE: frozenset[ObjectiveState] = frozenset({"FRAMED", "AWAITING_APPROVAL"})


# ---------------------------------------------------------------------------
# Reading the node


class LedgerRow(Strict, frozen=True):
    node_id: str
    brief_id: str | None
    kind: Literal["grant", "allocate", "consume", "release"]
    usd_micros: int
    approval_id: str | None


class ObjectiveRow(Strict, frozen=True):
    id: ObjectiveId
    parent_id: ObjectiveId | None
    depth: int
    space_id: SpaceId
    conversation_id: ConversationId


class _Loaded(Strict, frozen=True):
    row: ObjectiveRow
    events: list[Event]
    ledger: list[LedgerRow]
    children: list[ObjectiveId]
    objective: Objective


def _live_briefs(events: list[Event]) -> list[tuple[BriefId, str]]:
    """(brief_id, agent_class) of every Brief issued on the node with no
    later brief.stopped, brief.failed, or report.landed, in issue order."""
    live: dict[BriefId, str] = {}
    for e in events:
        if e.type == "brief.issued":
            brief = e.payload["brief"]
            live[brief["id"]] = brief["agent_class"]
        elif e.type in ("brief.stopped", "brief.failed", "report.landed"):
            live.pop(e.payload["brief_id"], None)
    return list(live.items())


def _stopped_unconfirmed(events: list[Event]) -> set[BriefId]:
    stopped: set[BriefId] = set()
    for e in events:
        if e.type == "brief.stopped":
            stopped.add(e.payload["brief_id"])
        elif e.type == "brief.stop_confirmed":
            stopped.discard(e.payload["brief_id"])
    return stopped


def fold(
    row: ObjectiveRow,
    events: list[Event],
    ledger_rows: list[LedgerRow],
    children: list[ObjectiveId],
) -> Objective:
    """The projection, as a pure function of the store (plan 02, "Reading the
    node"). The chaos test replays it from the same inputs."""
    contract: Contract | None = None
    revision = 0
    approved_at: int | None = None
    state: ObjectiveState = "FRAMED"
    reason: StateReason = "framed"
    stopped = 0
    reports: list[ReportRef] = []
    evidence: list[EvidenceRef] = []
    for e in events:
        p = e.payload
        if e.type in ("objective.opened", "objective.contract_revised"):
            contract = Contract.model_validate(p["contract"])
            revision = int(p["revision"])
        elif e.type == "objective.approved":
            approved_at = int(p["revision"])
        elif e.type == "objective.state_changed":
            state, reason = p["to"], p["reason"]
        elif e.type == "brief.stopped":
            stopped += 1
        elif e.type == "report.landed":
            report = p["report"]
            reports.append(
                ReportRef(
                    brief_id=p["brief_id"],
                    event_id=e.id,
                    summary=str(report.get("summary", "")),
                )
            )
            evidence.extend(
                EvidenceRef.model_validate(x) for x in report.get("evidence", [])
            )
    if contract is None:
        raise NotLive(f"objective {row.id} has no objective.opened event")
    owner = next(
        (b for b, cls in reversed(_live_briefs(events)) if cls == "Executor"), None
    )
    allocated = sum(r.usd_micros for r in ledger_rows if r.kind == "allocate")
    released = sum(r.usd_micros for r in ledger_rows if r.kind == "release")
    consumed = sum(r.usd_micros for r in ledger_rows if r.kind == "consume")
    return Objective(
        id=row.id,
        parent_id=row.parent_id,
        depth=row.depth,
        space=row.space_id,
        conversation_id=row.conversation_id,
        contract=contract,
        contract_revision=revision,
        approved_revision=approved_at if approved_at == revision else None,
        state=state,
        state_reason=reason,
        generation=1 + stopped,
        budget_consumed=Budget(usd_micros=consumed),
        budget_allocated=Budget(usd_micros=max(allocated - released, 0)),
        owner_brief=owner,
        reports=reports,
        evidence=evidence,
        children=children,
    )


async def objective_row(conn, objective_id: ObjectiveId) -> ObjectiveRow:
    cur = await conn.execute(
        "SELECT id, parent_id, depth, space_id, conversation_id "
        "FROM objectives WHERE id = %s",
        (objective_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise NotLive(f"no objective {objective_id}")
    return ObjectiveRow(
        id=row[0],
        parent_id=row[1],
        depth=row[2],
        space_id=row[3],
        conversation_id=row[4],
    )


async def ledger_rows(conn, node_id: str) -> list[LedgerRow]:
    cur = await conn.execute(
        "SELECT node_id, brief_id, kind, usd_micros, approval_id "
        "FROM budget_ledger WHERE node_id = %s ORDER BY id",
        (node_id,),
    )
    return [
        LedgerRow(
            node_id=r[0], brief_id=r[1], kind=r[2], usd_micros=r[3], approval_id=r[4]
        )
        for r in await cur.fetchall()
    ]


async def child_ids(conn, objective_id: ObjectiveId) -> list[ObjectiveId]:
    cur = await conn.execute(
        "SELECT id FROM objectives WHERE parent_id = %s ORDER BY id", (objective_id,)
    )
    return [r[0] for r in await cur.fetchall()]


async def _load(conn, objective_id: ObjectiveId) -> _Loaded:
    row = await objective_row(conn, objective_id)
    events = await read_for(
        conn, space_id=row.space_id, key="objective_id", value=objective_id
    )
    ledger = await ledger_rows(conn, objective_id)
    children = await child_ids(conn, objective_id)
    return _Loaded(
        row=row,
        events=events,
        ledger=ledger,
        children=children,
        objective=fold(row, events, ledger, children),
    )


async def project(conn, objective_id: ObjectiveId) -> Objective:
    return (await _load(conn, objective_id)).objective


# ---------------------------------------------------------------------------
# Opening, revising, approving


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo is not None else t.replace(tzinfo=UTC)


def _check_contract_against_space(contract: Contract, space: Space) -> None:
    if (
        EFFECT_RANK[contract.ceilings.max_effect_class]
        > EFFECT_RANK[space.max_effect_class]
    ):
        raise Refused(
            f"ceiling {contract.ceilings.max_effect_class!r} is above the space's "
            f"{space.max_effect_class!r} (architecture §9)"
        )
    if contract.root not in space.roots:
        raise Refused(f"root {contract.root!r} is not one of the space's roots")


async def open_objective(
    conn,
    *,
    space: SpaceId,
    conversation_id: ConversationId,
    contract: Contract,
    spaces: Mapping[SpaceId, Space] | None = None,
) -> ObjectiveId:
    """A new root in FRAMED at revision 1. Refuses before any row is written."""
    spaces = _spaces(spaces)
    _bind("check_may_open")(space, spaces)
    manifest = spaces[space]
    _check_contract_against_space(contract, manifest)
    if _aware(contract.ceilings.deadline) <= datetime.now(UTC):
        raise Refused("the contract's deadline is not in the future")
    objective_id = new_id()
    await conn.execute(
        "INSERT INTO objectives (id, parent_id, space_id, conversation_id, depth) "
        "VALUES (%s, NULL, %s, %s, 0)",
        (objective_id, space, conversation_id),
    )
    await _insert_revision(conn, space, objective_id, 1, contract)
    await append(
        conn,
        space_id=space,
        type="objective.opened",
        payload={
            "objective_id": objective_id,
            "parent_id": None,
            "conversation_id": conversation_id,
            "contract": contract.model_dump(mode="json"),
            "revision": 1,
        },
    )
    return objective_id


async def _insert_revision(conn, space, objective_id, revision, contract) -> None:
    await conn.execute(
        "INSERT INTO objective_revisions (space_id, objective_id, revision, contract) "
        "VALUES (%s, %s, %s, %s)",
        (space, objective_id, revision, Jsonb(contract.model_dump(mode="json"))),
    )


async def revise_contract(conn, objective_id: ObjectiveId, contract: Contract) -> int:
    """A new revision in FRAMED or AWAITING_APPROVAL; the fold clears the
    approval because the approved revision is no longer current."""
    async with single_flight(conn, lock_key(objective_id)):
        loaded = await _load(conn, objective_id)
        node = loaded.objective
        if node.state not in REVISABLE:
            raise IllegalTransition(
                f"revise_contract in {node.state}: only FRAMED or AWAITING_APPROVAL"
            )
        _check_contract_against_space(contract, _spaces(None)[node.space])
        floor = node.budget_allocated + node.budget_consumed
        if not floor.fits_within(contract.budget):
            raise BudgetExceeded(
                f"budget {contract.budget.usd_micros} is below what is already "
                f"allocated and consumed ({floor.usd_micros})"
            )
        revision = node.contract_revision + 1
        await _insert_revision(conn, node.space, objective_id, revision, contract)
        await append(
            conn,
            space_id=node.space,
            type="objective.contract_revised",
            payload={
                "objective_id": objective_id,
                "revision": revision,
                "contract": contract.model_dump(mode="json"),
            },
        )
        return revision


async def approve(conn, objective_id: ObjectiveId, approval) -> None:
    """`approval` is an `ApprovalRecord` (seams §1.12). `approvals.check`
    first; then the tree's own refusals; then the APPROVED edge, which this
    function alone writes."""
    async with single_flight(conn, lock_key(objective_id)):
        loaded = await _load(conn, objective_id)
        node = loaded.objective
        _bind("approval_check")(approval, contract_revision=node.contract_revision)
        if approval.objective_id != objective_id:
            raise Refused("the approval names another objective")
        if (
            approval.kind == "self_approved"
            and node.contract.ceilings.max_effect_class == "act"
        ):
            raise Refused("an act contract always waits on the person (seams v3)")
        if node.state == "FAILED" and approval.kind != "approved":
            raise IllegalTransition(
                "a FAILED node is revived only by an approved record, never "
                f"{approval.kind!r}"
            )
        if (node.state, "APPROVED") not in TRANSITIONS:
            raise IllegalTransition(f"approve in {node.state}")
        await append(
            conn,
            space_id=node.space,
            type="objective.approved",
            payload={
                "objective_id": objective_id,
                "revision": node.contract_revision,
                "approval_id": approval.id,
            },
        )
        await _transition(conn, loaded, "APPROVED", "approved", by="approve")


# ---------------------------------------------------------------------------
# Transitions and stops


async def _transition(
    conn,
    loaded: _Loaded,
    state: ObjectiveState,
    reason: StateReason,
    *,
    by: str | None = None,
    token_issuer: TokenIssuer | None = None,
) -> list[BriefId]:
    node = loaded.objective
    pair = (node.state, state)
    reasons = TRANSITIONS.get(pair)
    if reasons is None:
        raise IllegalTransition(f"{node.state} to {state} is not an edge")
    if reason not in reasons:
        raise IllegalTransition(f"{node.state} to {state} admits no reason {reason!r}")
    if pair in ONLY and ONLY[pair] != by:
        raise IllegalTransition(
            f"{node.state} to {state} is written by {ONLY[pair]} only"
        )
    await append(
        conn,
        space_id=node.space,
        type="objective.state_changed",
        payload={
            "objective_id": node.id,
            "from": node.state,
            "to": state,
            "reason": reason,
        },
    )
    if state in STOPPING:
        return await _stop_node(conn, loaded, reason, token_issuer)
    return []


async def transition(
    conn, objective_id: ObjectiveId, state: ObjectiveState, reason: StateReason
) -> list[BriefId]:
    """Only the edges of TRANSITIONS, and none of the pairs another function
    owns. Returns the Briefs fenced and revoked when the target stops the
    node; the caller hands them to `kernel.runs.stop` after commit."""
    async with single_flight(conn, lock_key(objective_id)):
        loaded = await _load(conn, objective_id)
        return await _transition(conn, loaded, state, reason)


async def _stop_node(
    conn, loaded: _Loaded, reason: StateReason, token_issuer: TokenIssuer | None
) -> list[BriefId]:
    stopped: list[BriefId] = []
    for brief_id, _ in _live_briefs(loaded.events):
        await _stop_brief(
            conn,
            brief_id,
            loaded.objective.id,
            loaded.objective.space,
            reason,
            new_generation=loaded.objective.generation + len(stopped) + 1,
            token_issuer=token_issuer,
        )
        stopped.append(brief_id)
    return stopped


async def _stop_brief(
    conn, brief_id, objective_id, space, reason, *, new_generation, token_issuer
) -> None:
    await append(
        conn,
        space_id=space,
        type="brief.stopped",
        payload={
            "objective_id": objective_id,
            "brief_id": brief_id,
            "reason": reason,
            "new_generation": new_generation,
        },
    )
    issuer = token_issuer if token_issuer is not None else _bind("default_token_issuer")
    await issuer.revoke(conn, brief_id)


async def stop(
    conn,
    objective_id: ObjectiveId,
    reason: StateReason,
    *,
    token_issuer: TokenIssuer | None = None,
) -> list[BriefId]:
    """Fence and revoke every live Brief of the node in one transaction. The
    state is the caller's to change; the returned ids go to
    `kernel.runs.stop` after commit."""
    async with single_flight(conn, lock_key(objective_id)):
        loaded = await _load(conn, objective_id)
        return await _stop_node(conn, loaded, reason, token_issuer)


# ---------------------------------------------------------------------------
# The ledger (plan 02, "Ledger arithmetic"; spike 01, lifted)
#
# Remaining is derived by SUM, never stored. Two kinds of node hold a budget
# of their own: an objective (B = the current contract's budget + Σ grant)
# and a root Scribe (B = its one grant row). A Brief carved from an objective
# holds its one allocate row. Every write that could break conservation takes
# the lock of the node it charges, reads remaining, and inserts only if the
# amount fits.


class _Holder(Strict, frozen=True):
    """What a ledger id refers to, resolved from the tables."""

    id: str
    kind: Literal["objective", "brief"]
    space: SpaceId
    budget: int  # B, in usd_micros
    parent: str | None  # the node an allocate row carved the Brief from
    objective_id: ObjectiveId | None  # for the events a Brief's rows raise


async def _holder(conn, x: str) -> _Holder:
    cur = await conn.execute("SELECT space_id FROM objectives WHERE id = %s", (x,))
    row = await cur.fetchone()
    if row is not None:
        loaded = await _load(conn, x)
        cur = await conn.execute(
            "SELECT COALESCE(SUM(usd_micros), 0) FROM budget_ledger "
            "WHERE node_id = %s AND kind = 'grant'",
            (x,),
        )
        grants = int((await cur.fetchone())[0])
        return _Holder(
            id=x,
            kind="objective",
            space=row[0],
            budget=loaded.objective.contract.budget.usd_micros + grants,
            parent=None,
            objective_id=x,
        )
    cur = await conn.execute(
        "SELECT node_id, usd_micros, space_id FROM budget_ledger "
        "WHERE brief_id = %s AND kind = 'allocate'",
        (x,),
    )
    row = await cur.fetchone()
    if row is not None:
        return _Holder(
            id=x,
            kind="brief",
            space=row[2],
            budget=int(row[1]),
            parent=row[0],
            objective_id=row[0],
        )
    cur = await conn.execute(
        "SELECT usd_micros, space_id FROM budget_ledger "
        "WHERE node_id = %s AND brief_id IS NULL AND kind = 'grant'",
        (x,),
    )
    row = await cur.fetchone()
    if row is not None:
        return _Holder(
            id=x,
            kind="brief",
            space=row[1],
            budget=int(row[0]),
            parent=None,
            objective_id=None,
        )
    raise NotLive(f"{x} holds no budget")


async def _sum(conn, column: str, x: str, kind: str) -> int:
    cur = await conn.execute(
        f"SELECT COALESCE(SUM(usd_micros), 0) FROM budget_ledger "
        f"WHERE {column} = %s AND kind = %s",
        (x, kind),
    )
    return int((await cur.fetchone())[0])


async def _remaining_of(conn, h: _Holder) -> int:
    if h.kind == "objective":
        live = await _sum(conn, "node_id", h.id, "allocate") - await _sum(
            conn, "node_id", h.id, "release"
        )
        return h.budget - live
    spent = await _sum(conn, "brief_id", h.id, "consume") + await _sum(
        conn, "brief_id", h.id, "release"
    )
    return max(h.budget - spent, 0)


async def _ledger_write(
    conn, *, space, node_id, brief_id, kind, usd_micros, approval_id=None
) -> None:
    await conn.execute(
        "INSERT INTO budget_ledger "
        "(space_id, node_id, brief_id, kind, usd_micros, approval_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (space, node_id, brief_id, kind, usd_micros, approval_id),
    )


def _charge_lock(h: _Holder) -> str:
    """The lock of the node a Brief's rows charge: its parent, or itself for
    a root Scribe."""
    if h.parent is not None:
        return lock_key(h.parent)
    return lock_key(h.id, "brief" if h.kind == "brief" else "objective")


async def remaining(conn, id: str) -> Budget:
    """B minus live allocations for an objective; B minus consumed and
    released for a Brief, clamped at zero."""
    h = await _holder(conn, id)
    return Budget(usd_micros=await _remaining_of(conn, h))


async def _allocate(conn, node_id: str, child_id: str, amount: Budget) -> Budget:
    """Carve `amount` of `node_id`'s remaining for the Brief `child_id`: one
    allocate row under the node's lock, or `BudgetExceeded`. Returns what the
    node has left."""
    async with single_flight(conn, lock_key(node_id)):
        h = await _holder(conn, node_id)
        if h.kind != "objective":
            raise NotLive(f"{node_id} is not a node a Brief is carved from")
        left = await _remaining_of(conn, h)
        if not amount.fits_within(Budget(usd_micros=left)):
            raise BudgetExceeded(
                f"{node_id}: {amount.usd_micros} does not fit {left} usd_micros"
            )
        await _ledger_write(
            conn,
            space=h.space,
            node_id=node_id,
            brief_id=child_id,
            kind="allocate",
            usd_micros=amount.usd_micros,
        )
        return Budget(usd_micros=left - amount.usd_micros)


async def _grant(conn, *, space, node_id, amount: Budget, approval_id=None) -> None:
    """One grant row: a person's raise of an objective (cites the approval)
    or a root Scribe's budget at delegation."""
    await _ledger_write(
        conn,
        space=space,
        node_id=node_id,
        brief_id=None,
        kind="grant",
        usd_micros=amount.usd_micros,
        approval_id=approval_id,
    )


async def consume(
    conn, brief_id: BriefId, amount: Budget, *, incurred: bool = False
) -> Budget:
    """Record spend against a Brief. `incurred=False` refuses past zero
    before writing. `incurred=True` records the full amount (the provider
    has billed it) and, when it crosses zero, appends `budget.overrun` with
    the shortfall and returns `ZERO`. No generation check: the gateway
    records a cut call's reserve after the stop that cut it."""
    h = await _holder(conn, brief_id)
    if h.kind != "brief":
        raise NotLive(f"{brief_id} is an objective; only a Brief consumes")
    async with single_flight(conn, _charge_lock(h)):
        left = await _remaining_of(conn, h)
        shortfall = amount.usd_micros - left
        if shortfall > 0 and not incurred:
            raise BudgetExceeded(
                f"{brief_id}: {amount.usd_micros} does not fit {left} usd_micros"
            )
        await _ledger_write(
            conn,
            space=h.space,
            node_id=h.parent if h.parent is not None else h.id,
            brief_id=h.id,
            kind="consume",
            usd_micros=amount.usd_micros,
        )
        if shortfall > 0:
            await append(
                conn,
                space_id=h.space,
                type="budget.overrun",
                payload={
                    "objective_id": h.objective_id,
                    "brief_id": h.id,
                    "amount": amount.model_dump(mode="json"),
                    "shortfall": Budget(usd_micros=shortfall).model_dump(mode="json"),
                },
            )
            return ZERO
        return Budget(usd_micros=left - amount.usd_micros)


async def release(conn, brief_id: BriefId) -> Budget:
    """Return a Brief's unspent allocation to its parent: one release row for
    `remaining(brief_id)`, or nothing when that is zero. A root Scribe has no
    parent to return to and writes nothing. Refused for an objective."""
    h = await _holder(conn, brief_id)
    if h.kind != "brief":
        raise Refused(f"{brief_id} is an objective; only a Brief is released")
    if h.parent is None:
        return ZERO
    async with single_flight(conn, lock_key(h.parent)):
        left = await _remaining_of(conn, h)
        if left == 0:
            return ZERO
        await _ledger_write(
            conn,
            space=h.space,
            node_id=h.parent,
            brief_id=h.id,
            kind="release",
            usd_micros=left,
        )
        return Budget(usd_micros=left)


async def _minted_approval(conn, space: SpaceId, approval_id: ApprovalId) -> dict:
    """The record behind an approval id, from the `approval.minted` event the
    surface appended in this space. The tree reads no card."""
    cur = await conn.execute(
        "SELECT payload FROM events WHERE space_id = %s AND type = 'approval.minted' "
        "AND payload->'approval'->>'id' = %s ORDER BY id LIMIT 1",
        (space, approval_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise Refused(f"no approval {approval_id} was minted in {space}")
    return row[0]["approval"]


async def raise_budget(
    conn, objective_id: ObjectiveId, *, by: Budget, approval_id: ApprovalId
) -> None:
    """A person raises the root: one grant row citing the approval, under
    the objective's lock. Only a record of kind `approved` for this objective
    counts (architecture §4, "only a person raises the root"); the card's
    kind is the supervisor's to assert."""
    async with single_flight(conn, lock_key(objective_id)):
        row = await objective_row(conn, objective_id)
        record = await _minted_approval(conn, row.space_id, approval_id)
        if record.get("kind") != "approved":
            raise Refused(
                f"approval {approval_id} is {record.get('kind')!r}, not 'approved'"
            )
        if record.get("objective_id") != objective_id:
            raise Refused(f"approval {approval_id} is for another objective")
        await _grant(
            conn,
            space=row.space_id,
            node_id=objective_id,
            amount=by,
            approval_id=approval_id,
        )


# ---------------------------------------------------------------------------
# Delegation (plan 02, "Delegate, step by step"; CLASS_RULES)


class ClassRule(Strict, frozen=True):
    names: frozenset[CapabilityName]
    ceiling: EffectClass | None  # None: the node's own
    profiles: frozenset[SandboxProfileName]
    max_data_class: DataClass
    seat: str
    states: frozenset[ObjectiveState] | None  # None: any, or no objective


CLASS_RULES: dict[str, ClassRule] = {
    "Executor": ClassRule(
        names=frozenset(
            {"read", "write", "bash", "ask", "push_branch", "connector.read"}
        ),
        ceiling=None,
        profiles=frozenset({"worktree", "scratch"}),
        max_data_class="PROJECT",
        seat="frontier",
        states=frozenset({"APPROVED", "RUNNING"}),
    ),
    "Verifier": ClassRule(
        names=frozenset({"read", "bash"}),
        ceiling="read",
        profiles=frozenset({"verify"}),
        max_data_class="PROJECT",
        seat="verifier",
        states=frozenset({"VERIFYING"}),
    ),
    "Scribe": ClassRule(
        names=frozenset(
            {"read", "ask", "memory.episodic.write", "memory.operator.propose"}
        ),
        ceiling="propose",
        profiles=frozenset({"scratch"}),
        max_data_class="OPERATOR",
        seat="summarizer",
        states=None,
    ),
}
# Planner: refused at M0 (tech stack §10); no row.

ROOT_SCRIBE_DEADLINE = timedelta(hours=1)


async def _is_brief(conn, id: str) -> bool:
    cur = await conn.execute("SELECT 1 FROM briefs WHERE id = %s", (id,))
    return await cur.fetchone() is not None


def _check_slice(slice: ContextSlice, space_id: SpaceId, max_data_class: DataClass):
    if slice.space != space_id:
        raise Refused(f"context slice is for {slice.space!r}, not {space_id!r}")
    if slice.sha256 != ContextSlice.digest(slice.blocks):
        raise Refused("context slice digest does not match its blocks")
    limit = DATA_CLASS_RANK[max_data_class]
    for block in slice.blocks:
        if DATA_CLASS_RANK[block.data_class] > limit:
            raise Refused(
                f"block {block.kind!r} is {block.data_class}, above {max_data_class}"
            )


def _check_capabilities(
    caps: Capabilities, rule: ClassRule, class_ceiling: EffectClass, space_id: SpaceId
) -> None:
    limit = EFFECT_RANK[class_ceiling]
    for cap in caps:
        if cap.name not in rule.names:
            raise Refused(f"{cap.name!r} is not a name this class may hold")
        if EFFECT_RANK[cap.effect_class] > limit:
            raise Refused(f"{cap.name}@{cap.effect_class} is above {class_ceiling}")
        if space_of(cap) != space_id:
            raise Refused(f"{cap.scope!r} is outside {space_id!r}")


async def delegate(
    conn,
    request: DelegateRequest,
    *,
    issuer: Capabilities,
    parent_brief: BriefId | TurnId | None,
    token_issuer: TokenIssuer | None = None,
    spaces: Mapping[SpaceId, Space] | None = None,
) -> Brief:
    """Issue one Brief: a capability set attenuated from the node's, a
    budget carved from the node (or a root Scribe's own grant), the class's
    seat and sandbox profile, the node's generation, and a token from the
    issuer. Refuses rather than clips. The token is returned and never
    stored; its sha256 sits beside the Brief."""
    rule = CLASS_RULES.get(request.agent_class)
    if rule is None:
        raise Refused(f"{request.agent_class} is refused at M0")
    if request.sandbox_profile not in rule.profiles:
        raise Refused(
            f"a {request.agent_class} runs in {sorted(rule.profiles)}, "
            f"not {request.sandbox_profile!r}"
        )
    if DATA_CLASS_RANK[request.max_data_class] > DATA_CLASS_RANK[rule.max_data_class]:
        raise Refused(
            f"a {request.agent_class} sees at most {rule.max_data_class} data"
        )
    brief_id = new_id()
    if request.objective_id is None:
        if parent_brief is None or await _is_brief(conn, parent_brief):
            raise Refused("an objective-less Scribe is delegated by a turn only")
        lock = lock_key(brief_id, "brief")
    else:
        lock = lock_key(request.objective_id)
    async with single_flight(conn, lock):
        return await _delegate(
            conn, request, rule, brief_id, issuer, parent_brief, token_issuer, spaces
        )


async def _delegate(
    conn, request, rule, brief_id, issuer, parent_brief, token_issuer, spaces
) -> Brief:
    now = datetime.now(UTC)
    loaded: _Loaded | None = None
    if request.objective_id is not None:
        loaded = await _load(conn, request.objective_id)
        node = loaded.objective
        if rule.states is not None and node.state not in rule.states:
            raise NotLive(
                f"a {request.agent_class} needs {sorted(rule.states)}, "
                f"the node is {node.state}"
            )
        if _stopped_unconfirmed(loaded.events):
            raise Unconfirmed(
                f"{node.id} has a stopped Brief whose compute is not confirmed dead"
            )
        if _aware(node.contract.ceilings.deadline) <= now:
            raise Refused(f"{node.id} is past its deadline")
        if request.agent_class == "Executor" and any(
            cls == "Executor" for _, cls in _live_briefs(loaded.events)
        ):
            raise AlreadyOwned(f"{node.id} already has a live Executor")
        space_id = node.space
        node_ceiling: EffectClass = node.contract.ceilings.max_effect_class
        deadline = node.contract.ceilings.deadline
        contract_data_class = node.contract.ceilings.max_data_class
        artifact_kind = node.contract.artifact_kind
        root: str | None = node.contract.root
        generation = node.generation
    else:
        space_id = request.context_slice.space
        node_ceiling = "propose"
        deadline = now + ROOT_SCRIBE_DEADLINE
        contract_data_class = rule.max_data_class
        artifact_kind = "document"
        root = None
        generation = 1
    try:
        space = _spaces(spaces)[space_id]
    except KeyError:
        raise Refused(f"no space {space_id!r}") from None

    # step 2: the capability set
    node_caps = ceiling(_bind("root_capabilities")(space), node_ceiling)
    from_worker = parent_brief is not None and await _is_brief(conn, parent_brief)
    effective = issuer if from_worker else node_caps
    caps = issue(effective, request.capabilities)
    class_ceiling = rule.ceiling if rule.ceiling is not None else node_ceiling
    _check_capabilities(caps, rule, class_ceiling, space_id)

    # step 3: the ceilings
    if DATA_CLASS_RANK[request.max_data_class] > DATA_CLASS_RANK[contract_data_class]:
        raise Refused(f"the contract allows at most {contract_data_class} data")
    ceilings = Ceilings(
        max_effect_class=node_ceiling,
        deadline=deadline,
        max_data_class=request.max_data_class,
    )

    # step 4: the slice
    _check_slice(request.context_slice, space_id, request.max_data_class)

    # step 5: the budget
    if request.objective_id is not None:
        await _allocate(conn, request.objective_id, brief_id, request.budget)
    else:
        await _grant(conn, space=space_id, node_id=brief_id, amount=request.budget)

    # steps 6 and 7: seat, profile, token
    model_ref = load_models().model(rule.seat).id
    key = request.objective_id if request.sandbox_profile == "worktree" else brief_id
    profile = await _bind("profile_for")(
        request.sandbox_profile,
        space,
        key,
        artifact_kind=artifact_kind,
        max_data_class=request.max_data_class,
        snapshot=request.snapshot,
        root=root,
    )
    tokens = token_issuer if token_issuer is not None else _bind("default_token_issuer")
    token = await tokens.issue_token(
        conn,
        brief_id=brief_id,
        generation=generation,
        model_ref=model_ref,
        space=space_id,
    )
    brief = Brief(
        id=brief_id,
        objective_id=request.objective_id,
        space=space_id,
        agent_class=request.agent_class,
        harness="pydantic_ai",
        generation=generation,
        context_slice=request.context_slice,
        instruction=request.instruction,
        max_data_class=request.max_data_class,
        budget=request.budget,
        ceilings=ceilings,
        capabilities=caps,
        sandbox_profile=profile,
        model_ref=model_ref,
        gateway_token=token,
        report_schema=request.report_schema,
        issued_at=now,
    )

    # step 8: the writes
    stored = brief.model_dump(mode="json", exclude={"gateway_token"})
    token_sha256 = hashlib.sha256(token.get_secret_value().encode()).hexdigest()
    await conn.execute(
        "INSERT INTO briefs (id, space_id, objective_id, agent_class, generation, "
        "brief, gateway_token_sha256, issued_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            brief_id,
            space_id,
            request.objective_id,
            request.agent_class,
            generation,
            Jsonb(stored),
            token_sha256,
            now,
        ),
    )
    await append(
        conn,
        space_id=space_id,
        type="brief.issued",
        payload={
            "brief": stored,
            "gateway_token_sha256": token_sha256,
            "objective_id": request.objective_id,
        },
    )
    if loaded is not None and request.agent_class == "Executor":
        if loaded.objective.state == "APPROVED":
            await _transition(conn, loaded, "RUNNING", "running", by="delegate")
    return brief


# ---------------------------------------------------------------------------
# Stopping one Brief, confirming, and the generation fence (plan 02, "Stopping")


class BriefRow(Strict, frozen=True):
    id: BriefId
    space_id: SpaceId
    objective_id: ObjectiveId | None
    agent_class: str
    generation: int
    report_schema: ReportSchemaName


async def _brief_row(conn, brief_id: str) -> BriefRow | None:
    cur = await conn.execute(
        "SELECT id, space_id, objective_id, agent_class, generation, "
        "brief->>'report_schema' FROM briefs WHERE id = %s",
        (brief_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return BriefRow(
        id=row[0],
        space_id=row[1],
        objective_id=row[2],
        agent_class=row[3],
        generation=row[4],
        report_schema=row[5],
    )


async def _brief_events(conn, row: BriefRow) -> list[Event]:
    return await read_for(conn, space_id=row.space_id, key="brief_id", value=row.id)


async def stop_brief(
    conn,
    brief_id: BriefId,
    reason: StateReason,
    *,
    token_issuer: TokenIssuer | None = None,
) -> None:
    """The fence and revoke for one Brief: `brief.stopped` with the node's
    next generation, then `revoke`. The supervisor calls it for an
    objective-less Scribe; `stop` calls it per Brief. A turn id names no
    Brief and is refused."""
    row = await _brief_row(conn, brief_id)
    if row is None:
        raise NotLive(f"{brief_id} is not a Brief")
    if row.objective_id is not None:
        async with single_flight(conn, lock_key(row.objective_id)):
            loaded = await _load(conn, row.objective_id)
            if brief_id not in {b for b, _ in _live_briefs(loaded.events)}:
                raise NotLive(f"{brief_id} is not live")
            await _stop_brief(
                conn,
                brief_id,
                row.objective_id,
                row.space_id,
                reason,
                new_generation=loaded.objective.generation + 1,
                token_issuer=token_issuer,
            )
        return
    async with single_flight(conn, lock_key(brief_id, "brief")):
        events = await _brief_events(conn, row)
        if any(
            e.type in ("brief.stopped", "brief.failed", "report.landed") for e in events
        ):
            raise NotLive(f"{brief_id} is not live")
        await _stop_brief(
            conn,
            brief_id,
            None,
            row.space_id,
            reason,
            new_generation=2,
            token_issuer=token_issuer,
        )


async def confirm_stop(conn, brief_id: BriefId, receipt: StopReceipt) -> None:
    """The sandbox's probe-confirmed receipt for a stopped Brief. Until every
    stopped Brief on a node has one, `delegate` refuses on that node."""
    row = await _brief_row(conn, brief_id)
    if row is None:
        raise NotLive(f"{brief_id} is not a Brief")
    events = await _brief_events(conn, row)
    stopped = any(e.type == "brief.stopped" for e in events)
    confirmed = any(e.type == "brief.stop_confirmed" for e in events)
    if not stopped or confirmed:
        raise NotLive(f"{brief_id} has no stop awaiting confirmation")
    await append(
        conn,
        space_id=row.space_id,
        type="brief.stop_confirmed",
        payload={
            "brief_id": brief_id,
            "objective_id": row.objective_id,
            "receipt": receipt.model_dump(mode="json"),
        },
    )


async def check_generation(conn, token: BriefToken) -> None:
    """`StaleGeneration` when the Brief is unknown or the token's generation
    is below the node's (1 + count of brief.stopped). A Brief with no node
    is stale only after its own stop."""
    row = await _brief_row(conn, token.brief_id)
    if row is None:
        raise StaleGeneration(f"{token.brief_id} is not a Brief")
    if row.objective_id is None:
        events = await _brief_events(conn, row)
        current = 1 + sum(1 for e in events if e.type == "brief.stopped")
    else:
        events = await read_for(
            conn, space_id=row.space_id, key="objective_id", value=row.objective_id
        )
        current = 1 + sum(1 for e in events if e.type == "brief.stopped")
    if token.generation < current:
        raise StaleGeneration(
            f"{token.brief_id} carries generation {token.generation}, "
            f"the node is at {current}"
        )


# ---------------------------------------------------------------------------
# Landing a terminal and the deadline sweep (plan 02, "Landing a terminal")

REPORT_TYPES: dict[ReportSchemaName, type] = {
    "Report": Report,
    "Verdict": Verdict,
    "ScribeReport": ScribeReport,
}

VERDICT_EDGES: dict[str, tuple[ObjectiveState, StateReason]] = {
    "pass": ("SUCCEEDED", "verified"),
    "fail": ("FAILED", "verification_failed"),
    "abstain": ("AWAITING_APPROVAL", "awaiting_decision"),
}


def _closed(events: list[Event], brief_id: BriefId) -> set[str]:
    """The closing event types already written for one Brief."""
    return {
        e.type
        for e in events
        if e.type in ("brief.stopped", "brief.failed", "report.landed")
        and e.payload.get("brief_id") == brief_id
    }


def _fence(events: list[Event], token: BriefToken, *, own: bool) -> None:
    stopped = (
        e
        for e in events
        if e.type == "brief.stopped"
        and (not own or e.payload.get("brief_id") == token.brief_id)
    )
    current = 1 + sum(1 for _ in stopped)
    if token.generation < current:
        raise StaleGeneration(
            f"{token.brief_id} carries generation {token.generation}, "
            f"the node is at {current}"
        )


def _report_payload(row: BriefRow, report) -> dict[str, Any]:
    return {
        "brief_id": row.id,
        "objective_id": row.objective_id,
        "report": report.model_dump(mode="json"),
    }


async def _holder_of(conn, loaded: _Loaded, statement: str) -> _Loaded:
    """The nearest node on the path to root whose contract holds the
    statement; `Refused` when none does. At M0 every node is a root."""
    at = loaded
    while at.objective.contract.assumption(statement) is None:
        if at.row.parent_id is None:
            raise Refused(f"no contract on the path holds {statement!r}")
        at = await _load(conn, at.row.parent_id)
    return at


async def land_report(conn, token: BriefToken, terminal: Terminal) -> None:
    """The Brief's last word, under the node's lock. `report.landed` or
    `brief.failed`, then the transition the class and outcome name. Every
    refusal happens before the first write, so a failure after it leaves the
    caller's transaction with nothing of the three."""
    row = await _brief_row(conn, token.brief_id)
    if row is None:
        raise StaleGeneration(f"{token.brief_id} is not a Brief")
    if row.objective_id is None:
        async with single_flight(conn, lock_key(row.id, "brief")):
            events = await _brief_events(conn, row)
            if _closed(events, row.id) == {"brief.stopped"}:
                if terminal.outcome == "aborted":
                    return
            _fence(events, token, own=True)
            await _land(conn, row, None, events, terminal)
        return
    async with single_flight(conn, lock_key(row.objective_id)):
        loaded = await _load(conn, row.objective_id)
        if _closed(loaded.events, row.id) == {"brief.stopped"}:
            if terminal.outcome == "aborted":
                return
        _fence(loaded.events, token, own=False)
        await _land(conn, row, loaded, loaded.events, terminal)


async def _land(
    conn, row: BriefRow, loaded: _Loaded | None, events: list[Event], terminal: Terminal
) -> None:
    if _closed(events, row.id):
        raise NotLive(f"{row.id} is not live")
    oid = row.objective_id
    if terminal.outcome == "aborted":
        raise Refused(f"{row.id} was not stopped; an abort names a stop")
    if terminal.outcome == "failed":
        await append(
            conn,
            space_id=row.space_id,
            type="brief.failed",
            payload={"brief_id": row.id, "objective_id": oid, "error": terminal.error},
        )
        if row.agent_class == "Executor":
            await _transition(
                conn,
                await _load(conn, oid),
                "FAILED",
                "worker_failed",
                by="land_report",
            )
        elif row.agent_class == "Verifier":
            # Round two, supervisor amendments M to P: a Verifier's failed
            # terminal closes the node so the person can retry.
            await _transition(
                conn,
                await _load(conn, oid),
                "FAILED",
                "worker_failed",
                by="land_report",
            )
        return
    report = terminal.report
    if not isinstance(report, REPORT_TYPES[row.report_schema]):
        raise Refused(
            f"{row.id} reports {row.report_schema}, not {type(report).__name__}"
        )
    if row.agent_class == "Executor":
        challenged = [
            d for d in report.assumption_deltas if d.status in ("challenged", "refuted")
        ]
        holders = [await _holder_of(conn, loaded, d.statement) for d in challenged]
        await append(
            conn,
            space_id=row.space_id,
            type="report.landed",
            payload=_report_payload(row, report),
        )
        if not challenged:
            await _transition(
                conn, await _load(conn, oid), "VERIFYING", "verifying", by="land_report"
            )
            return
        for delta, holder in zip(challenged, holders):
            await append(
                conn,
                space_id=row.space_id,
                type="assumption.challenged",
                payload={
                    "objective_id": oid,
                    "ancestor_id": holder.objective.id,
                    "statement": delta.statement,
                    "evidence": [e.model_dump(mode="json") for e in delta.evidence],
                },
            )
        await _transition(
            conn,
            await _load(conn, oid),
            "AWAITING_APPROVAL",
            "challenged_assumption",
            by="land_report",
        )
        for holder_id in {h.objective.id for h in holders} - {oid}:
            async with single_flight(conn, lock_key(holder_id)):
                await _transition(
                    conn,
                    await _load(conn, holder_id),
                    "AWAITING_APPROVAL",
                    "challenged_assumption",
                    by="land_report",
                )
        return
    await append(
        conn,
        space_id=row.space_id,
        type="report.landed",
        payload=_report_payload(row, report),
    )
    if row.agent_class == "Verifier":
        state, reason = VERDICT_EDGES[report.outcome]
        await _transition(conn, await _load(conn, oid), state, reason, by="land_report")


async def expire_due(conn) -> list[ObjectiveId]:
    """Every live node past its contract deadline goes to CANCELLED/deadline,
    which fences and revokes its Briefs through the bound default issuer."""
    # The kernel's clock, not now(): that is the transaction's start time,
    # and the caller's transaction may be older than the deadline.
    now = datetime.now(UTC)
    cur = await conn.execute(
        """
        SELECT DISTINCT o.id
        FROM objectives o
        JOIN events e
          ON e.space_id = o.space_id
         AND e.payload->>'objective_id' = o.id
         AND e.type IN ('objective.opened', 'objective.contract_revised')
        WHERE (e.payload->'contract'->'ceilings'->>'deadline')::timestamptz <= %s
        ORDER BY o.id
        """,
        (now,),
    )
    due = [r[0] for r in await cur.fetchall()]
    expired: list[ObjectiveId] = []
    for oid in due:
        async with single_flight(conn, lock_key(oid)):
            loaded = await _load(conn, oid)
            node = loaded.objective
            if node.state not in LIVE_STATES:
                continue
            if _aware(node.contract.ceilings.deadline) > now:
                continue
            await _transition(conn, loaded, "CANCELLED", "deadline")
            expired.append(oid)
    return expired
