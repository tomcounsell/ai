"""The task record, its Brief, stop, status, and the consistency audit.

A task is one JSONB document holding its Brief, written once, plus the
events that name it. Its state is `machine.fold` over the events, never a
stored field, so a stop at any instant leaves nothing to reconcile between
the two.
"""

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from core import corrections, git, ledger, machine
from core.settings import settings

EFFECT_RANK = {"read": 0, "propose": 1, "act": 2}

# The starter's manual judge verdict until the judgement port (1.3) runs the
# judge: `start --mode bare` records `precise`, `--mode clarify` records
# `thin`, each `leg: manual` with the starter's provenance. No mode leaves
# the task in `judge`.
MODES = ("bare", "clarify")
JUDGE_FOR_MODE = {"bare": "precise", "clarify": "thin"}

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
    `mode` is one of `MODES` or None. `target_branch` is the branch a merge
    lands on, `origin_url` the absolute push URL of the workspace's origin
    as it was at start (the merge goes there, whatever the workspace's
    config says later), and `base_sha` the workspace's head at start; all
    three come from `resolve_workspace`.
    """

    instruction: str
    budget_usd_micros: int
    max_effect_class: str = "propose"
    governance_grant: str | None = None
    workspace: str | None = None
    model: str = "haiku"
    harness: dict[str, Any] = field(default_factory=dict)
    mode: str | None = None
    target_branch: str | None = None
    origin_url: str | None = None
    base_sha: str | None = None
    id: str = field(default_factory=ledger.new_id)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self):
        if self.budget_usd_micros < 0:
            raise ValueError("a budget is never negative")
        if self.max_effect_class not in EFFECT_RANK:
            raise ValueError(f"unknown effect class {self.max_effect_class!r}")
        if self.mode is not None and self.mode not in MODES:
            raise ValueError(f"unknown mode {self.mode!r}")


class TaskStopped(RuntimeError):
    pass


class WorkspaceRefused(ValueError):
    pass


def resolve_workspace(workspace: str | None, target_branch: str | None = None) -> dict[str, str | None]:
    """Where a task's merge goes, read once at start, before any turn can
    touch the workspace's config: origin's push URL (absolute), the target
    branch (given, or the branch origin's HEAD names), and the workspace's
    head. A workspace on a detached HEAD is refused; so is an origin whose
    HEAD names no branch when none is given. A directory that is not a git
    repository, or has no origin, gives what it can."""
    if not git.is_repo(workspace):
        return {}
    if git.branch(workspace) is None:
        raise WorkspaceRefused(f"{workspace} is on a detached HEAD; check out a branch first")
    found: dict[str, str | None] = {"base_sha": git.head(workspace)}
    try:
        url = git.push_url(workspace)
    except git.GitError:
        return found
    target = target_branch or git.remote_head(workspace, url)
    if target is None:
        raise WorkspaceRefused(f"origin ({url}) has no HEAD branch; pass --target-branch")
    return {**found, "origin_url": url, "target_branch": target}


async def start(
    conn, brief: Brief, *, by: str = "tom", via: str = "the command line", role_played: bool = False
) -> str:
    """Write the task document and its first event in one transaction, and
    with a mode, the manual judge verdict beside them."""
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
                "sdlc": 1,
                "instruction": brief.instruction,
                "budget_usd_micros": brief.budget_usd_micros,
                "max_effect_class": brief.max_effect_class,
                "governance_grant": brief.governance_grant,
                "mode": brief.mode,
                "target_branch": brief.target_branch,
                "origin_url": brief.origin_url,
                "base_sha": brief.base_sha,
            },
        )
        if brief.mode is not None:
            verdict = JUDGE_FOR_MODE[brief.mode]
            await ledger.append(
                conn,
                brief.id,
                "judge.decided",
                {
                    "verdict": verdict,
                    "leg": "manual",
                    "judgement_id": None,
                    "guard_id": machine.GUARD_JUDGE if verdict == "thin" else None,
                    "provenance": ledger.provenance(by, via, role_played),
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


def stage_text(state: machine.State) -> str | None:
    """The stage file for a state, or None for a state no turn runs in."""
    path = Path(settings.stages_dir) / f"{state.value}.md"
    return path.read_text().strip() if path.is_file() else None


def channel_text(offered: list[str]) -> str:
    effects = "\n".join(f"  - {line}" for line in offered) or "  - none"
    return (Path(settings.stages_dir) / "channel.md").read_text().strip().replace("{effects}", effects)


async def dispatch(conn, task_id: str, state: machine.State | None = None) -> dict[str, Any]:
    """The Brief as a turn receives it: the task's commitments plus every
    correction in force, rendered from the ledger now, never from a copy
    made when the task started, and for a task with a workspace, how the
    turn reaches Tom (`skills/sdlc/channel.md`, listing the effects the
    registered performers offer) and the stage file for the state the task
    is in (`skills/sdlc/<state>.md`). Returns the text, the correction
    numbers it carries, and the text's digest."""
    from core import broker

    b = await brief(conn, task_id)
    rows = await ledger.read(conn, task_id)
    f = machine.fold(rows)
    state = state or f.state
    standing = await corrections.in_force(conn)
    committed = money(rows)["committed_usd_micros"]
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
    if f.plan and state in (machine.State.BUILD, machine.State.PATCH, machine.State.PLAN):
        head += f"\nPlan: {f.plan['path']} at {f.plan['commit']}"
    sections = [head, corrections.render(standing)]
    if b.workspace:
        sections.append(channel_text(broker.offered()))
        stage = stage_text(state)
        if stage:
            sections.append(stage)
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
    budget = 0
    raised = 0
    charged = 0
    reserved: dict[str, int] = {}
    for row in rows:
        kind, p = row["type"], row["payload"]
        if kind == "task.started":
            # Set, not added: a task has one `task.started`, which `start`
            # writes with the task's document, whose primary key refuses a
            # second start of the same id.
            budget = p["budget_usd_micros"]
        elif kind == "budget.raised":
            raised += p["usd_micros"]
        elif kind == "gateway.reserved":
            reserved[p["call_id"]] = p["usd_micros"]
        elif kind == "gateway.charged":
            reserved.pop(p["call_id"], None)
            charged += p["usd_micros"]
    committed = budget + raised
    return {
        "committed_usd_micros": committed,
        "charged_usd_micros": charged,
        "open_reservations": reserved,
        "remaining_usd_micros": committed - charged - sum(reserved.values()),
    }


# Every kind of attention entry, in the order `attention_counts` lists them.
# A manual verdict is a person playing a stage no runner plays yet; a grant
# is Tom's tap on one governance instance.
ATTENTION_KINDS = ("question", "feedback", "approval", "budget_raise", "verdict", "grant")


async def status(conn, task_id: str) -> dict[str, Any]:
    """The task as a fold over its ledger.

    The state machine's fold (`machine.Fold.summary`: `state`, `legacy`,
    `return_to`, `plan`, `loops`, `counts`, `candidate`, `checks`, `join`,
    `governance`, `merge_effect`), the money, every turn and effect, and
    the attention log. `attention` lists every point where Tom acted on the
    task, in ledger order, each labelled by `kind`: a question with his
    answer, feedback on a delivery, an approval of a held effect, a raise of
    the budget, a manual verdict, and a governance grant, each with the
    provenance it was recorded with (see `provenance`). `attention_counts`
    counts each kind, with how many were role-played and how many are
    unknown (rows that recorded no `role_played`). A question not yet
    answered is listed and not counted. `delivered` is the latest
    delivery's summary."""
    rows = await ledger.read(conn, task_id)
    f = machine.fold(rows)
    turns: dict[str, str | None] = {}
    effects: dict[str, str] = {}
    attention: list[dict[str, Any]] = []
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
        elif kind in machine.VERDICT_ROWS and p.get("leg") == "manual":
            attention.append(
                {
                    "kind": "verdict",
                    "stage": machine.VERDICT_ROWS[kind].value,
                    "verdict": p.get("verdict"),
                    "provenance": provenance(row),
                }
            )
        elif kind == "guard.granted" and p.get("instance_id"):
            attention.append(
                {
                    "kind": "grant",
                    "guard_id": p["guard_id"],
                    "instance_id": p["instance_id"],
                    "note": p.get("note"),
                    "provenance": provenance(row),
                }
            )
    return {
        "task_id": task_id,
        **f.summary(),
        **money(rows),
        "turns": turns,
        "effects": effects,
        "attention": attention,
        "attention_counts": _counts(attention),
        "delivered": (f.delivery or {}).get("summary"),
        "delivery": f.delivery,
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
