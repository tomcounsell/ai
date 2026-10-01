"""The task record, its Brief, stop, and the consistency audit.

A task is one JSONB document holding its Brief, written once, plus the
events that name it. Status is a fold over the events, never a stored field,
so a stop at any instant leaves nothing to reconcile between the two.
"""

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from psycopg.types.json import Jsonb

from core import corrections, ledger, signals

EFFECT_RANK = {"read": 0, "propose": 1, "act": 2}

# How the Brief asks Valor to open the task. `bare` gives the instruction
# alone; `clarify` is an experimental arm whose Brief tells Valor to spend
# its first turn inspecting and asking (`signals.CLARIFY`). The mode is data
# in the Brief, never something the kernel enforces.
MODES = ("bare", "clarify")

STOP_CHANNEL = "valor_stop"


@dataclass(frozen=True)
class Brief:
    """What the kernel commits to when a task starts.

    `budget_usd_micros` is money, the only budget unit. `max_effect_class`
    is the ceiling no effect under this task may exceed. `governance_grant`
    is Tom's grant for this task to add a check, gate, hook, validator,
    review round, or approval step; with none, the broker refuses any action
    that adds governance, and with one, each such action is still `act` and
    waits for his tap.

    `workspace` is the directory a turn works in, `model` the model it runs,
    and `harness` the harness's own settings for the task (its isolation).
    `mode` is one of `MODES`.
    """

    instruction: str
    budget_usd_micros: int
    max_effect_class: str = "propose"
    governance_grant: str | None = None
    workspace: str | None = None
    model: str = "haiku"
    harness: dict[str, Any] = field(default_factory=dict)
    mode: str = "bare"
    id: str = field(default_factory=ledger.new_id)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self):
        if self.budget_usd_micros < 0:
            raise ValueError("a budget is never negative")
        if self.max_effect_class not in EFFECT_RANK:
            raise ValueError(f"unknown effect class {self.max_effect_class!r}")
        if self.mode not in MODES:
            raise ValueError(f"unknown mode {self.mode!r}")


class TaskStopped(RuntimeError):
    pass


async def start(conn, brief: Brief) -> str:
    """Write the task document and its first event in one transaction."""
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO documents (kind, id, body) VALUES ('task', %s, %s)",
            (brief.id, Jsonb(asdict(brief))),
        )
        await ledger.append(
            conn,
            brief.id,
            "task.started",
            {
                "instruction": brief.instruction,
                "budget_usd_micros": brief.budget_usd_micros,
                "max_effect_class": brief.max_effect_class,
                "governance_grant": brief.governance_grant,
                "mode": brief.mode,
            },
        )
    return brief.id


async def brief(conn, task_id: str) -> Brief:
    row = await (
        await conn.execute("SELECT body FROM documents WHERE kind = 'task' AND id = %s", (task_id,))
    ).fetchone()
    if row is None:
        raise KeyError(task_id)
    return Brief(**row[0])


async def dispatch(conn, task_id: str) -> dict[str, Any]:
    """The Brief as a turn receives it: the task's commitments plus every
    correction in force, rendered from the ledger now, never from a copy
    made when the task started, and for a task with a workspace, how the
    turn reaches Tom. Returns the text, the correction numbers it carries,
    and the text's digest."""
    b = await brief(conn, task_id)
    standing = await corrections.in_force(conn)
    committed = money(await ledger.read(conn, task_id))["committed_usd_micros"]
    head = (
        "# Brief\n\n"
        f"Task: {b.id}\n"
        f"Instruction: {b.instruction}\n"
        f"Budget: ${committed / 1_000_000:.4f}\n"
        f"Effect ceiling: {b.max_effect_class}\n"
        f"Governance grant: {b.governance_grant or 'none'}"
    )
    if b.workspace:
        head += f"\nWorkspace: {b.workspace}"
    sections = [head, corrections.render(standing)]
    if b.workspace:
        sections.append(signals.PROTOCOL)
        if b.mode == "clarify":
            sections.append(signals.CLARIFY)
    text = "\n\n".join(sections)
    return {
        "text": text,
        "corrections": [c["number"] for c in standing],
        "sha256": ledger.digest(text),
    }


async def is_stopped(conn, task_id: str) -> bool:
    row = await (
        await conn.execute(
            "SELECT 1 FROM events WHERE task_id = %s AND type = 'task.stopped'",
            (task_id,),
        )
    ).fetchone()
    return row is not None


async def stop(conn, task_id: str, *, reason: str, by: str = "tom") -> bool:
    """Fence the task and wake whichever process is running its turn.

    The `task.stopped` row is the fence: the gateway refuses every later
    call and the broker every later effect by reading it, whether or not the
    running process ever hears the notification. Returns False when the task
    was already stopped.
    """
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        if await is_stopped(conn, task_id):
            return False
        await ledger.append(conn, task_id, "task.stopped", {"reason": reason, "by": by})
        await conn.execute("SELECT pg_notify(%s, %s)", (STOP_CHANNEL, task_id))
    return True


# The rows remaining money is folded from.
MONEY_EVENTS = ("task.started", "budget.raised", "gateway.reserved", "gateway.charged")


def money(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The one computation of a task's money, over its rows in id order
    (rows of other types are ignored): committed is the Brief's budget plus
    every raise Tom gave; remaining is committed minus every charge minus
    every open reservation. `budget.reserve` and `status` both call this."""
    committed = 0
    charged = 0
    reserved: dict[str, int] = {}
    for row in rows:
        kind, p = row["type"], row["payload"]
        if kind == "task.started":
            committed += p["budget_usd_micros"]
        elif kind == "budget.raised":
            committed += p["usd_micros"]
        elif kind == "gateway.reserved":
            reserved[p["call_id"]] = p["usd_micros"]
        elif kind == "gateway.charged":
            reserved.pop(p["call_id"], None)
            charged += p["usd_micros"]
    return {
        "committed_usd_micros": committed,
        "charged_usd_micros": charged,
        "open_reservations": reserved,
        "remaining_usd_micros": committed - charged - sum(reserved.values()),
    }


# Every kind of attention entry, in the order `attention_counts` lists them.
ATTENTION_KINDS = ("question", "feedback", "approval", "budget_raise")


async def status(conn, task_id: str) -> dict[str, Any]:
    """The task as a fold over its ledger.

    `attention` lists every point where Tom acted on the task, in ledger
    order, each labelled by `kind`: a question (`question`) with his
    answer, feedback on a delivery (`feedback`), an approval of a held
    effect (`approval`), and a raise of the budget (`budget_raise`), each
    with the provenance it was recorded with (see `provenance`).
    `attention_counts` counts each kind, with how many were role-played and
    how many are unknown (rows that recorded no `role_played`), so
    approvals are counted apart from questions and feedback. A question not
    yet answered is listed and not counted. `delivered` is the latest
    delivery's summary; feedback after it puts the task back to `live`
    until the next `task.delivered`."""
    rows = await ledger.read(conn, task_id)
    turns: dict[str, str | None] = {}
    effects: dict[str, str] = {}
    attention: list[dict[str, Any]] = []
    delivered = None
    reopened = False
    stopped = False
    for row in rows:
        kind, p = row["type"], row["payload"]
        if kind == "turn.started":
            turns[p["turn_id"]] = None
        elif kind == "turn.ended":
            turns[p["turn_id"]] = p["outcome"]
        elif kind == "effect.held":
            effects[p["effect_id"]] = "pending"
        elif kind == "effect.intent":
            effects[p["effect_id"]] = "in_flight"
        elif kind == "effect.outcome":
            effects[p["effect_id"]] = p["kind"]
        elif kind == "effect.refused":
            effects[p["effect_id"]] = "refused"
        elif kind == "question.asked":
            attention.append(
                {"kind": "question", "question_id": p["question_id"], "question": p["text"], "answer": None}
            )
        elif kind == "question.answered":
            for q in attention:
                if q.get("question_id") == p["question_id"]:
                    q["answer"] = p["text"]
                    q["provenance"] = provenance(row)
        elif kind == "feedback.given":
            attention.append(
                {
                    "kind": "feedback",
                    "feedback_id": p["feedback_id"],
                    "on_delivery": p["on_delivery"],
                    "feedback": p["text"],
                    "provenance": provenance(row),
                }
            )
            reopened = True
        elif kind == "approval.granted":
            attention.append(
                {
                    "kind": "approval",
                    "approval_id": p["approval_id"],
                    "effect_id": p["effect_id"],
                    "note": p.get("note"),
                    "provenance": provenance(row),
                }
            )
        elif kind == "budget.raised":
            attention.append(
                {
                    "kind": "budget_raise",
                    "raise_id": p["raise_id"],
                    "usd_micros": p["usd_micros"],
                    "note": p.get("note"),
                    "provenance": provenance(row),
                }
            )
        elif kind == "task.delivered":
            delivered = p["summary"]
            reopened = False
        elif kind == "task.stopped":
            stopped = True
    if stopped:
        state = "stopped"
    elif delivered is not None and not reopened:
        state = "delivered"
    elif any(q["kind"] == "question" and q["answer"] is None for q in attention):
        state = "waiting for Tom"
    else:
        state = "live"
    return {
        "task_id": task_id,
        "state": state,
        **money(rows),
        "turns": turns,
        "effects": effects,
        "attention": attention,
        "attention_counts": _counts(attention),
        "delivered": delivered,
    }


def provenance(row: dict[str, Any]) -> dict[str, Any]:
    """A row's provenance (`by`, `via`, `at`, `role_played`) as recorded.
    A field the row does not carry reads as None, never as a default:
    answers and feedback written before `role_played` existed say nothing
    about it, and approvals written before approvals carried provenance hold
    only a top-level `by` (unreliable: the replay driver's approvals say
    `tom`), so their `at` is the row's own time. No row is rewritten."""
    p = row["payload"]
    recorded = p.get("provenance") or {"by": p.get("by")}
    at = recorded.get("at")
    if at is None and row.get("at") is not None:
        at = row["at"].isoformat()
    return {
        "by": recorded.get("by"),
        "via": recorded.get("via"),
        "at": at,
        "role_played": recorded.get("role_played"),
    }


def _counts(attention: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts = {k: {"total": 0, "role_played": 0, "unknown": 0} for k in ATTENTION_KINDS}
    for entry in attention:
        if "provenance" not in entry:
            continue  # a question asked and not yet answered
        c = counts[entry["kind"]]
        c["total"] += 1
        played = entry["provenance"]["role_played"]
        if played is None:
            c["unknown"] += 1
        elif played:
            c["role_played"] += 1
    return counts


def audit(state: dict[str, Any]) -> list[str]:
    """What a lossless stop must leave true. Empty means consistent: every
    gateway call charged, every turn ended, no effect caught between intent
    and outcome, and nothing spent past the committed budget."""
    problems = []
    for call_id in state["open_reservations"]:
        problems.append(f"gateway call {call_id} reserved and never charged")
    for turn_id, outcome in state["turns"].items():
        if outcome is None:
            problems.append(f"turn {turn_id} started and never ended")
    for effect_id, effect_state in state["effects"].items():
        if effect_state == "in_flight":
            problems.append(f"effect {effect_id} has an intent and no outcome")
    if state["charged_usd_micros"] > state["committed_usd_micros"]:
        problems.append("charged more than the committed budget")
    return problems
