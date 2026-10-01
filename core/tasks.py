"""The task record, its Brief, stop, and the consistency audit.

A task is one JSONB document holding its Brief, written once, plus the
events that name it. Status is a fold over the events, never a stored field,
so a stop at any instant leaves nothing to reconcile between the two.
"""

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from psycopg.types.json import Jsonb

from core import corrections, ledger

EFFECT_RANK = {"read": 0, "propose": 1, "act": 2}

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
    """

    instruction: str
    budget_usd_micros: int
    max_effect_class: str = "propose"
    governance_grant: str | None = None
    id: str = field(default_factory=ledger.new_id)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self):
        if self.budget_usd_micros < 0:
            raise ValueError("a budget is never negative")
        if self.max_effect_class not in EFFECT_RANK:
            raise ValueError(f"unknown effect class {self.max_effect_class!r}")


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
    made when the task started. Returns the text, the correction numbers it
    carries, and the text's digest."""
    b = await brief(conn, task_id)
    standing = await corrections.in_force(conn)
    text = "\n\n".join(
        [
            (
                "# Brief\n\n"
                f"Task: {b.id}\n"
                f"Instruction: {b.instruction}\n"
                f"Budget: ${b.budget_usd_micros / 1_000_000:.4f}\n"
                f"Effect ceiling: {b.max_effect_class}\n"
                f"Governance grant: {b.governance_grant or 'none'}"
            ),
            corrections.render(standing),
        ]
    )
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


async def status(conn, task_id: str) -> dict[str, Any]:
    """The task as a fold over its ledger."""
    rows = await ledger.read(conn, task_id)
    committed = 0
    charged = 0
    reserved: dict[str, int] = {}
    turns: dict[str, str | None] = {}
    effects: dict[str, str] = {}
    stopped = False
    for row in rows:
        kind, p = row["type"], row["payload"]
        if kind == "task.started":
            committed = p["budget_usd_micros"]
        elif kind == "gateway.reserved":
            reserved[p["call_id"]] = p["usd_micros"]
        elif kind == "gateway.charged":
            reserved.pop(p["call_id"], None)
            charged += p["usd_micros"]
        elif kind == "turn.started":
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
        elif kind == "task.stopped":
            stopped = True
    return {
        "task_id": task_id,
        "state": "stopped" if stopped else "live",
        "committed_usd_micros": committed,
        "charged_usd_micros": charged,
        "open_reservations": reserved,
        "remaining_usd_micros": committed - charged - sum(reserved.values()),
        "turns": turns,
        "effects": effects,
    }


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
