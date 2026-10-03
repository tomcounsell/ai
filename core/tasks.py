"""The task record, its Brief, stop, status, and the consistency audit.

A task is one JSONB document holding its Brief, written once, plus the
events that name it. Its state is `machine.fold` over the events, never a
stored field, so a stop at any instant leaves nothing to reconcile between
the two.
"""

from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from core import corrections, git, ledger, machine
from core.settings import settings

EFFECT_RANK = {"read": 0, "propose": 1, "act": 2}

STOP_CHANNEL = "valor_stop"


@dataclass(frozen=True)
class Brief:
    """What the kernel commits to when a task starts.

    `max_effect_class` is the ceiling no effect under this task may exceed. `governance_grant`
    is Tom's advance word that this task may add a check, gate, hook,
    validator, review round, or approval step, shown in every turn's Brief.
    It grants nothing by itself: the broker computes whether a merge adds
    governance from the review and docs verdicts, and refuses it while any
    instance they name lacks Tom's own tap (`guard.granted`), Brief grant or
    not.

    `workspace` is the directory a turn works in, `model` the model it runs,
    `harness_name` the harness that runs it (`claude_code` or `pi`), and
    `harness` the harness's own settings for the task (its isolation).
    `target_branch` is the branch a merge
    lands on, `origin_url` the absolute push URL of the workspace's origin
    as it was at start (the merge goes there, whatever the workspace's
    config says later), and `base_sha` the workspace's head at start; all
    three come from `resolve_workspace`, or from `core.workspace` for a task
    the kernel provisioned. For such a task `mirror` is the kernel mirror
    (the bare repository only the kernel writes, which the merge predicate
    and the merge read), `push_url` where `push_branch` goes (the task's own
    bare origin), and `project` the project spec as it was at start, with
    the task's service ports.
    """

    instruction: str
    max_effect_class: str = "propose"
    governance_grant: str | None = None
    workspace: str | None = None
    model: str = "haiku"
    harness_name: str = "claude_code"
    harness: dict[str, Any] = field(default_factory=dict)
    target_branch: str | None = None
    origin_url: str | None = None
    base_sha: str | None = None
    mirror: str | None = None
    push_url: str | None = None
    project: dict[str, Any] | None = None
    id: str = field(default_factory=ledger.new_id)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self):
        if self.max_effect_class not in EFFECT_RANK:
            raise ValueError(f"unknown effect class {self.max_effect_class!r}")

    @classmethod
    def load(cls, body: dict[str, Any]) -> Brief:
        """A stored Brief, from the fields this dataclass has. A document
        written when the Brief had a field it no longer has (`mode`, before
        the judge ran, or the money figure tasks once started with) still
        loads; no document is rewritten."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in body.items() if k in known})


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
    try:
        if not git.is_repo(workspace):
            return {}
        found_hostile = git.hostile(workspace)
    except git.GitError as exc:
        raise WorkspaceRefused(str(exc)) from None
    if found_hostile:
        raise WorkspaceRefused(
            "the workspace's git config names what the kernel will not run: " + "; ".join(found_hostile)
        )
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
    """Write the task document and its first event in one transaction. The
    task starts in `judge`; the judge runner decides it."""
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
                "max_effect_class": brief.max_effect_class,
                "governance_grant": brief.governance_grant,
                "target_branch": brief.target_branch,
                "origin_url": brief.origin_url,
                "base_sha": brief.base_sha,
                "provenance": ledger.provenance(by, via, role_played),
            },
        )
    return brief.id


async def start_calibration(conn, site: str, *, by: str = "tom") -> str:
    """A calibration task: a document and a `task.started` carrying the site
    its judgement calls are metered on, and no `sdlc` marker. It folds as
    `calibration`, and every SDLC writer refuses it."""
    b = Brief(instruction=f"calibrate {site}", max_effect_class="read")
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO documents (kind, id, body) VALUES ('task', %s, %s)", (b.id, Jsonb(asdict(b)))
        )
        await ledger.append(
            conn,
            b.id,
            "task.started",
            {
                "calibration": site,
                "instruction": b.instruction,
                "max_effect_class": "read",
                "provenance": ledger.provenance(by, "python -m core calibrate", False),
            },
        )
    return b.id


async def is_calibration(conn, task_id: str) -> bool:
    row = await (
        await conn.execute(
            "SELECT payload->>'calibration' FROM events WHERE task_id = %s AND type = 'task.started' LIMIT 1",
            (task_id,),
        )
    ).fetchone()
    return bool(row and row[0])


class CalibrationTask(LookupError):
    """An SDLC writer was pointed at a calibration task."""


async def brief(conn, task_id: str) -> Brief:
    row = await (
        await conn.execute("SELECT body FROM documents WHERE kind = 'task' AND id = %s", (task_id,))
    ).fetchone()
    if row is None:
        raise KeyError(task_id)
    return Brief.load(row[0])


def stage_text(state: machine.State | str) -> str | None:
    """The stage file for a state (or a check's name), or None for one no
    turn runs in."""
    name = state.value if isinstance(state, machine.State) else str(state)
    path = Path(settings.stages_dir) / f"{name}.md"
    return path.read_text().strip() if path.is_file() else None


def channel_text(offered: list[str]) -> str:
    effects = "\n".join(f"  - {line}" for line in offered) or "  - none"
    return (Path(settings.stages_dir) / "channel.md").read_text().strip().replace("{effects}", effects)


def verdict_text() -> str:
    return (Path(settings.stages_dir) / "verdict.md").read_text().strip()


async def dispatch(
    conn, task_id: str, state: machine.State | None = None, *, fresh: str | None = None
) -> dict[str, Any]:
    """The Brief as a turn receives it: the task's commitments plus every
    correction in force, rendered from the ledger now, never from a copy
    made when the task started, and for a task with a workspace, how the
    turn reaches Tom (`skills/sdlc/channel.md`, listing the effects the
    registered performers offer) and the stage file for the state the task
    is in (`skills/sdlc/<state>.md`). A `fresh` session (critique, review,
    docs: the stage's name) gets the verdict channel
    (`skills/sdlc/verdict.md`) in place of the working session's, offering
    no effect and no question, and its stage's file. Returns the text, the
    correction numbers it carries, and the text's digest."""
    from core import broker

    b = await brief(conn, task_id)
    rows = await ledger.read(conn, task_id)
    f = machine.fold(rows)
    state = state or f.state
    standing = await corrections.in_force(conn)
    head = (
        "# Brief\n\n"
        f"Task: {b.id}\n"
        f"Instruction: {b.instruction}\n"
        f"Effect ceiling: {b.max_effect_class}\n"
        f"Governance grant: {b.governance_grant or 'none'}"
    )
    if b.workspace and not fresh:
        head += f"\nWorkspace: {b.workspace}"
    if f.plan and state in (machine.State.BUILD, machine.State.PATCH, machine.State.PLAN) and not fresh:
        head += f"\nPlan: {f.plan['path']} at {f.plan['commit']}"
    sections = [head, corrections.render(standing)]
    if fresh:
        sections.append(verdict_text())
        stage = stage_text(fresh)
        if stage:
            sections.append(stage)
    elif b.workspace:
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
        if await is_calibration(conn, task_id):
            raise CalibrationTask(f"task {task_id} is a calibration task; it runs no turn to stop")
        if await is_stopped(conn, task_id):
            return False
        await ledger.append(conn, task_id, "task.stopped", {"reason": reason, "by": by})
        await conn.execute("SELECT pg_notify(%s, %s)", (STOP_CHANNEL, task_id))
    return True


# Ledgers written before 2026-10-03 open a call with this type; it folds as `gateway.opened`.
LEGACY_OPENED = "gateway.reserved"


def spending(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """A task's metered spending, over its rows in id order (rows of other
    types are ignored): `spent_usd_micros` is every charge summed, and
    `open_calls` each call opened and not yet charged, with its worst-case
    estimate. Information only: nothing refuses on it."""
    spent = 0
    open_calls: dict[str, int] = {}
    for row in rows:
        kind, p = row["type"], row["payload"]
        if kind in ("gateway.opened", LEGACY_OPENED):
            # A legacy row carries its worst case as `usd_micros`.
            open_calls[p["call_id"]] = p.get("estimate_usd_micros", p.get("usd_micros"))
        elif kind == "gateway.charged":
            open_calls.pop(p["call_id"], None)
            spent += p["usd_micros"]
    return {"spent_usd_micros": spent, "open_calls": open_calls}


# Every kind of attention entry, in the order `attention_counts` lists them.
# A manual verdict is a person playing a stage no runner plays yet; a grant
# is Tom's tap on one governance instance.
ATTENTION_KINDS = ("question", "feedback", "approval", "verdict", "grant")


async def status(conn, task_id: str) -> dict[str, Any]:
    """The task as a fold over its ledger.

    The state machine's fold (`machine.Fold.summary`: `state`, `legacy`,
    `return_to`, `plan`, `loops`, `counts`, `candidate`, `checks`, `join`,
    `governance`, `merge_effect`), its metered spending, every turn and effect, and
    the attention log. `attention` lists every point where Tom acted on the
    task, in ledger order, each labelled by `kind`: a question with his
    answer, feedback on a delivery, an approval of a held effect, a manual
    verdict, and a governance grant, each with the
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
        **spending(rows),
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
    gateway call charged, every turn ended, and no effect caught between
    intent and outcome."""
    problems = []
    for call_id in state["open_calls"]:
        problems.append(f"gateway call {call_id} opened and never charged")
    for turn_id, outcome in state["turns"].items():
        if outcome is None:
            problems.append(f"turn {turn_id} started and never ended")
    for effect_id, effect_state in state["effects"].items():
        if effect_state == "in_flight":
            problems.append(f"effect {effect_id} has an intent and no outcome")
    return problems
