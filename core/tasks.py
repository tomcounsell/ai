"""The task record, its Brief, stop, status, and the consistency audit.

A task is one JSONB document holding its Brief, written once, plus the
events that name it. Its state is `machine.fold` over the events, never a
stored field, so a stop at any instant leaves nothing to reconcile between
the two.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from core import corrections, git, ledger, machine, memory, outcomes, persona
from core.settings import resolve_model, settings

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

    `workspace` is the directory a turn works in, `model` the model it runs
    (by default the light seat's pinned id, never an alias),
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

    `parent_id` names the task this one is a child of (None for a root; a
    document written before the tree loads as one). A Brief is written
    once, so a node's parent and ceiling never change.

    `routine` names the routine a task belongs to (its objective, its runs,
    and what they start); `replay` marks a task the emulator drives. Either
    makes the task, and every task under it, background work
    (`background`): the kernel runs Tom's tasks first.
    """

    instruction: str
    max_effect_class: str = "propose"
    governance_grant: str | None = None
    workspace: str | None = None
    model: str = resolve_model("light")
    harness_name: str = "claude_code"
    harness: dict[str, Any] = field(default_factory=dict)
    target_branch: str | None = None
    origin_url: str | None = None
    base_sha: str | None = None
    mirror: str | None = None
    push_url: str | None = None
    project: dict[str, Any] | None = None
    parent_id: str | None = None
    routine: str | None = None
    replay: bool = False
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
    try:
        target = target_branch or git.remote_head(workspace, url)
    except git.GitError as exc:
        raise WorkspaceRefused(f"cannot read origin's HEAD ({url}): {exc}") from None
    if target is None:
        raise WorkspaceRefused(f"origin ({url}) has no HEAD branch; pass --target-branch")
    return {**found, "origin_url": url, "target_branch": target}


class CeilingRefused(ValueError):
    """A child asked for an effect ceiling above its parent's."""


def child_ceiling(parent_ceiling: str, requested: str | None) -> str:
    """A child's effect ceiling: its parent's when none is asked for, the
    one asked for when it ranks at or below the parent's. One above is
    refused in full, never clipped to one that fits."""
    if requested is None:
        return parent_ceiling
    if requested not in EFFECT_RANK:
        raise ValueError(f"unknown effect class {requested!r}")
    if EFFECT_RANK[requested] > EFFECT_RANK[parent_ceiling]:
        raise CeilingRefused(
            f"a child may not have the ceiling {requested}: its parent's ceiling is {parent_ceiling}"
        )
    return requested


async def start(
    conn,
    brief: Brief,
    *,
    marker: dict[str, Any] | None = None,
    by: str = "tom",
    via: str = "the command line",
    role_played: bool = False,
) -> str:
    """Write the task document and its first event in one transaction. The
    task starts in `judge`; the judge runner decides it. A Brief naming a
    parent starts through `start_child`, its ceiling the one asked for.
    `marker` is laid into `task.started` as `start_child`'s is (`{"sdlc": 1}`
    when None); a root with `{"objective": NAME}` is a routine's objective."""
    if brief.parent_id is not None:
        given = asdict(brief)
        parent_id, ceiling = given.pop("parent_id"), given.pop("max_effect_class")
        return await start_child(
            conn, parent_id, ceiling=ceiling, marker=marker, by=by, via=via, role_played=role_played, **given
        )
    marker = {"sdlc": 1} if marker is None else dict(marker)
    async with conn.transaction():
        await _write(conn, brief, marker, ledger.provenance(by, via, role_played))
    return brief.id


async def _write(conn, brief: Brief, marker: dict[str, Any], provenance: dict[str, Any]) -> None:
    """The task document and its `task.started`: the marker's fields, then
    the kernel's, which a marker never overrides."""
    await conn.execute(
        "INSERT INTO documents (kind, id, body) VALUES ('task', %s, %s)",
        (brief.id, Jsonb(asdict(brief))),
    )
    await ledger.append(
        conn,
        brief.id,
        "task.started",
        {
            **marker,
            **({"parent_id": brief.parent_id} if brief.parent_id is not None else {}),
            "instruction": brief.instruction,
            "max_effect_class": brief.max_effect_class,
            "governance_grant": brief.governance_grant,
            "target_branch": brief.target_branch,
            "origin_url": brief.origin_url,
            "base_sha": brief.base_sha,
            "provenance": provenance,
        },
    )


async def start_child(
    conn,
    parent_id: str,
    *,
    ceiling: str | None = None,
    marker: dict[str, Any] | None = None,
    by: str = "tom",
    via: str = "the command line",
    role_played: bool = False,
    **brief_fields: Any,
) -> str:
    """Start a child of `parent_id`, in one transaction under the tree's
    lock. Its ceiling is `child_ceiling(parent's, ceiling)`; its workspace,
    `governance_grant`, and `harness` are what `brief_fields` give, never
    the parent's. `marker` is laid into `task.started` (`{"sdlc": 1}` when
    None, so a child is an SDLC task by default). Refused, with nothing
    written: an unknown parent (`UnknownParent`), a calibration parent or a
    marker carrying `calibration` (`CalibrationTask`: stop cannot walk
    through one), a parent fenced by a stop (`TaskStopped`), and a ceiling
    above the parent's (`CeilingRefused`)."""
    marker = {"sdlc": 1} if marker is None else dict(marker)
    if "calibration" in marker:
        raise CalibrationTask("a calibration task cannot be a child; stop cannot walk through one")
    async with conn.transaction():
        await lock_tree(conn, parent_id)
        try:
            parent = await brief(conn, parent_id)
        except KeyError:
            raise UnknownParent(f"no task {parent_id}") from None
        if await is_calibration(conn, parent_id):
            raise CalibrationTask(f"task {parent_id} is a calibration task; it cannot be a parent")
        fence = await fenced_by(conn, parent_id)
        if fence is not None:
            raise TaskStopped(
                f"task {parent_id} is stopped"
                if fence == parent_id
                else f"task {parent_id} is under stopped task {fence}"
            )
        child = Brief(
            **brief_fields,
            parent_id=parent_id,
            max_effect_class=child_ceiling(parent.max_effect_class, ceiling),
        )
        await _write(conn, child, marker, ledger.provenance(by, via, role_played))
    return child.id


async def start_calibration(
    conn,
    site: str,
    *,
    by: str = "tom",
    via: str = "python -m core calibrate",
    detail: dict[str, Any] | None = None,
) -> str:
    """A calibration task: a document and a `task.started` carrying the site
    its judgement calls are metered on, and no `sdlc` marker. It folds as
    `calibration`, and every SDLC writer refuses it, so it is a task that
    only meters. `detail` is merged into the `task.started` payload (its
    `instruction`, when given, names the task); `via` is the command its
    provenance records. The emulator meters its stand-in and judge on one."""
    detail = dict(detail or {})
    b = Brief(instruction=detail.pop("instruction", f"calibrate {site}"), max_effect_class="read")
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO documents (kind, id, body) VALUES ('task', %s, %s)", (b.id, Jsonb(asdict(b)))
        )
        await ledger.append(
            conn,
            b.id,
            "task.started",
            {
                **detail,
                "calibration": site,
                "instruction": b.instruction,
                "max_effect_class": "read",
                "provenance": ledger.provenance(by, via, False),
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


class UnknownParent(LookupError):
    """A child's start named a parent with no Brief."""


class CalibrationTask(LookupError):
    """An SDLC writer was pointed at a calibration task."""


async def brief(conn, task_id: str) -> Brief:
    row = await (
        await conn.execute("SELECT body FROM documents WHERE kind = 'task' AND id = %s", (task_id,))
    ).fetchone()
    if row is None:
        raise KeyError(task_id)
    body = row[0]
    # A task started by message is provisioned after it starts, by a kernel
    # job; what that made is a row, laid over the document written at start.
    made = await (
        await conn.execute(
            "SELECT payload->'fields' FROM events WHERE task_id = %s AND type = 'workspace.provisioned' "
            "ORDER BY id DESC LIMIT 1",
            (task_id,),
        )
    ).fetchone()
    if made is not None:
        body = {**body, **made[0]}
    return Brief.load(body)


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
    conn,
    task_id: str,
    state: machine.State | None = None,
    *,
    fresh: str | None = None,
    offered: Sequence[str] = (),
) -> dict[str, Any]:
    """The text a turn receives: the persona first (`persona/`, rendered
    now from the kernel's own checkout, never from the workspace), then the
    Brief: the task's commitments plus every
    correction in force, rendered from the ledger now, then what memory
    recalls for it (`core.memory.recall`; none for a fresh session), never from a copy
    made when the task started, and for a task with a workspace, how the
    turn reaches Tom (`skills/sdlc/channel.md`, listing `offered`, the
    usage lines of the task's own performers) and the stage file for the state the task
    is in (`skills/sdlc/<state>.md`). A `fresh` session (critique, review,
    docs: the stage's name) gets the verdict channel
    (`skills/sdlc/verdict.md`) in place of the working session's, offering
    no effect and no question, and its stage's file; the advisor (`advice`)
    gets only its stage file, since it answers in prose. A working session's
    Brief whose task has children ends with their Children section
    (`children_text`). Returns the text, the
    correction numbers it carries, the text's digest, the usage lines it
    offered, and the persona's digest and size. A persona that cannot be
    read (a missing persona file or identity field, or a `CLAUDE.md` that
    is missing or lacks the governance or tests paragraph) raises
    `persona.PersonaUnreadable`; there is no fallback text."""

    b = await brief(conn, task_id)
    rows = await ledger.read(conn, task_id)
    f = machine.fold(rows)
    state = state or f.state
    standing = await corrections.in_force(conn)
    rendered = persona.render(settings.persona_dir)
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
    sections = [rendered, head, corrections.render(standing)]
    remembered = await memory.recall(conn, task_id, fresh)
    if remembered:
        sections.append(remembered)
    lines: list[str] = []
    if fresh:
        if fresh != "advice":  # the advisor answers in prose, in its own stage file
            sections.append(verdict_text())
        stage = stage_text(fresh)
        if stage:
            sections.append(stage)
    elif b.workspace:
        lines = list(offered)
        sections.append(channel_text(lines))
        stage = stage_text(state)
        if stage:
            sections.append(stage)
    if not fresh:
        found = await reports(conn, task_id)
        if found:
            sections.append(children_text(found))
    text = "\n\n".join(sections)
    return {
        "text": text,
        "corrections": [c["number"] for c in standing],
        "sha256": ledger.digest(text),
        "offered": lines,
        "persona_sha256": persona.digest(rendered),
        "persona_bytes": len(rendered.encode()),
    }


async def has_stop_row(conn, task_id: str) -> bool:
    """Whether the task has its own `task.stopped` row."""
    row = await (
        await conn.execute(
            "SELECT 1 FROM events WHERE task_id = %s AND type = 'task.stopped'",
            (task_id,),
        )
    ).fetchone()
    return row is not None


async def ancestors(conn, task_id: str) -> list[str]:
    """The task's parents up to the root, nearest first, read from each
    Brief's `parent_id`; empty for a root or an unknown task."""
    rows = await (
        await conn.execute(
            "WITH RECURSIVE up (id, depth) AS ("
            "  SELECT body->>'parent_id', 1 FROM documents WHERE kind = 'task' AND id = %s"
            "  UNION ALL"
            "  SELECT d.body->>'parent_id', up.depth + 1 FROM documents d"
            "  JOIN up ON d.kind = 'task' AND d.id = up.id"
            ") SELECT id FROM up WHERE id IS NOT NULL ORDER BY depth",
            (task_id,),
        )
    ).fetchall()
    return [r[0] for r in rows]


async def children(conn, task_id: str) -> list[str]:
    """The task's children, in the order they started."""
    rows = await (
        await conn.execute(
            "SELECT task_id FROM events WHERE type = 'task.started' AND payload @> %s ORDER BY id",
            (Jsonb({"parent_id": task_id}),),
        )
    ).fetchall()
    return [r[0] for r in rows]


async def subtree(conn, task_id: str) -> list[str]:
    """The task's descendants, breadth first, each node's children in start
    order."""
    found: list[str] = []
    queue = [task_id]
    while queue:
        below = await children(conn, queue.pop(0))
        found += below
        queue += below
    return found


async def background(conn, task_id: str) -> bool:
    """Whether the task is background work: it or an ancestor carries
    `Brief.routine` or `Brief.replay`. An unknown task is foreground."""
    row = await (
        await conn.execute(
            "WITH RECURSIVE up (id) AS ("
            "  SELECT %s::text"
            "  UNION ALL"
            "  SELECT d.body->>'parent_id' FROM documents d JOIN up ON d.kind = 'task' AND d.id = up.id"
            "  WHERE d.body->>'parent_id' IS NOT NULL"
            ") SELECT 1 FROM documents d JOIN up ON d.kind = 'task' AND d.id = up.id"
            " WHERE d.body->>'routine' IS NOT NULL OR (d.body->>'replay')::boolean IS TRUE LIMIT 1",
            (task_id,),
        )
    ).fetchone()
    return row is not None


async def lock_tree(conn, task_id: str) -> str:
    """The tree's advisory lock, `tree:<root id>`, taken by the writers that
    change what a tree holds or whether a node may reopen (`start_child`,
    `stop_tree`, `session.feedback`), before any task lock. Returns the
    root's id."""
    root = ([task_id, *await ancestors(conn, task_id)])[-1]
    await ledger.lock(conn, f"tree:{root}")
    return root


async def fenced_by(conn, task_id: str) -> str | None:
    """The nearest of the task and its ancestors with a `task.stopped` row,
    or None: a stop fences the whole subtree under it."""
    path = [task_id, *await ancestors(conn, task_id)]
    rows = await (
        await conn.execute(
            "SELECT task_id FROM events WHERE type = 'task.stopped' AND task_id = ANY(%s)",
            (path,),
        )
    ).fetchall()
    stopped = {r[0] for r in rows}
    return next((node for node in path if node in stopped), None)


async def is_stopped(conn, task_id: str) -> bool:
    """Whether the task is fenced: it or an ancestor is stopped."""
    return await fenced_by(conn, task_id) is not None


async def stop(
    conn,
    task_id: str,
    *,
    reason: str,
    by: str = "tom",
    via: str = "the command line",
    role_played: bool = False,
) -> bool:
    """Stop the task and its subtree (`stop_tree`). Returns False when the
    task already had its own stop row."""
    return await stop_tree(conn, task_id, reason=reason, by=by, via=via, role_played=role_played) > 0


async def stop_tree(
    conn,
    task_id: str,
    *,
    reason: str,
    by: str = "tom",
    via: str = "the command line",
    role_played: bool = False,
) -> int:
    """Fence the task and every descendant, and wake whichever process is
    running each one's turn. Returns how many `task.stopped` rows it wrote:
    0 when the task already has its own.

    The `task.stopped` row is the fence: the gateway refuses every later
    call and the broker every later effect by reading it (on the node or an
    ancestor, `fenced_by`), whether or not the running process ever hears
    the notification. In one transaction, under the tree's lock and then
    the named task's: the named task gets its row whatever its state, then
    the walk goes down in start order. A descendant with its own row is
    passed by with its subtree (walked when it stopped); a merged one keeps
    `merged`, fenced by the ancestor read, and its children are walked; any
    other gets a row carrying `by_stop_of` and its own notification."""
    async with conn.transaction():
        await lock_tree(conn, task_id)
        await ledger.lock(conn, f"task:{task_id}")
        if await is_calibration(conn, task_id):
            raise CalibrationTask(f"task {task_id} is a calibration task; it runs no turn to stop")
        if await has_stop_row(conn, task_id):
            return 0
        provenance = ledger.provenance(by, via, role_played)
        row = {"reason": reason, "by": by, "provenance": provenance}
        await _stop_row(conn, task_id, row)
        written = 1
        queue = await children(conn, task_id)
        while queue:
            node = queue.pop(0)
            if await has_stop_row(conn, node):
                continue
            if machine.fold(await ledger.read(conn, node)).state is not machine.State.MERGED:
                await _stop_row(conn, node, {**row, "by_stop_of": task_id})
                written += 1
            queue += await children(conn, node)
    return written


async def _stop_row(conn, task_id: str, payload: dict[str, Any]) -> None:
    await ledger.append(conn, task_id, "task.stopped", payload)
    await conn.execute("SELECT pg_notify(%s, %s)", (STOP_CHANNEL, task_id))


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


async def tree_spending(conn, task_id: str) -> dict[str, Any]:
    """The metered spending of the task's subtree, itself included, as
    `spending` folds each node: `tree_spent_usd_micros`, every charge
    summed; `tree_open_calls`, each call opened and not yet charged, by
    call id, with its task id and estimate; and `charges`, every
    `gateway.charged` row as `{task_id, call_id, usd_micros, at}` in event
    id order. A report: nothing reads it to decide anything, and a stopped
    node's spending counts like any other."""
    nodes = [task_id, *await subtree(conn, task_id)]
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT id, task_id, type, payload, at FROM events WHERE task_id = ANY(%s) "
            "AND type IN ('gateway.opened', 'gateway.reserved', 'gateway.charged') ORDER BY id",
            (nodes,),
        )
        rows = await cur.fetchall()
    spent = 0
    open_calls: dict[str, dict[str, Any]] = {}
    for node in nodes:
        own = spending([r for r in rows if r["task_id"] == node])
        spent += own["spent_usd_micros"]
        for call_id, estimate in own["open_calls"].items():
            open_calls[call_id] = {"task_id": node, "estimate_usd_micros": estimate}
    charges = [
        {
            "task_id": r["task_id"],
            "call_id": r["payload"]["call_id"],
            "usd_micros": r["payload"]["usd_micros"],
            "at": r["at"].isoformat(),
        }
        for r in rows
        if r["type"] == "gateway.charged"
    ]
    return {"tree_spent_usd_micros": spent, "tree_open_calls": open_calls, "charges": charges}


async def child_report(conn, child_id: str) -> dict[str, Any]:
    """A child as its parent sees it: id, instruction, state, its subtree's
    metered spending, and its latest delivery (`summary`, `outcome`) or
    None."""
    f = machine.fold(await ledger.read(conn, child_id))
    delivery = f.delivery
    return {
        "task_id": child_id,
        "instruction": (await brief(conn, child_id)).instruction,
        "state": f.state.value,
        "tree_spent_usd_micros": (await tree_spending(conn, child_id))["tree_spent_usd_micros"],
        "delivery": {"summary": delivery.get("summary"), "outcome": delivery.get("outcome")}
        if delivery
        else None,
    }


async def reports(conn, task_id: str) -> list[dict[str, Any]]:
    """`child_report` for each direct child, in start order."""
    return [await child_report(conn, c) for c in await children(conn, task_id)]


def usd(micros: int) -> str:
    """Micro-dollars as dollars to the micro-dollar, from integers only."""
    return f"${micros // 1_000_000}.{micros % 1_000_000:06d}"


def quoted(text: str | None, indent: str = "  ") -> list[str]:
    """Each line of `text` quoted under a bullet."""
    return [f"{indent}> {line}".rstrip() for line in (text or "").splitlines() or [""]]


def children_text(found: list[dict[str, Any]]) -> str:
    """The Brief's Children section: kernel facts only (id, state, subtree
    spending, the instruction Tom or kernel code gave), never a child's own
    words."""
    lines = ["# Children", ""]
    for c in found:
        lines.append(f"- {c['task_id']} ({c['state']}, metered spending {usd(c['tree_spent_usd_micros'])})")
        lines += quoted(c["instruction"])
    return "\n".join(lines)


# Every kind of attention entry, in the order `attention_counts` lists them.
# A verdict is a `leg: manual` row already in a ledger, a person's; a
# grant is Tom's tap on one governance instance.
ATTENTION_KINDS = ("question", "feedback", "approval", "verdict", "grant")


async def status(conn, task_id: str) -> dict[str, Any]:
    """The task as a fold over its ledger.

    The state machine's fold (`machine.Fold.summary`: `state`, `legacy`,
    `return_to`, `plan`, `loops`, `counts`, `candidate`, `checks`, `join`,
    `governance`, `merge_effect`), its metered spending, every turn and effect, and
    the attention log. `attention` lists every point where Tom acted on the
    task, in ledger order, each labelled by `kind`: a question with his
    answer, feedback on a delivery, an approval of a held effect, a manual
    verdict (an older row), and a governance grant, each with the
    provenance it was recorded with (see `provenance`). `attention_counts`
    counts each kind, with how many were role-played and how many are
    unknown (rows that recorded no `role_played`). A question not yet
    answered is listed and not counted. `failed_step` is the `step.failed`
    the task waits on, or None. `delivered` is the latest delivery's
    summary.

    The tree: `parent_id`, `fenced_by` (the nearest stopped node on the
    path to the root, or None), `children` (`reports`), and the subtree's
    `tree_spent_usd_micros` and `tree_open_calls` beside the task's own."""
    rows = await ledger.read(conn, task_id)
    f = machine.fold(rows)
    turns, effects, attention = _digest(rows)
    return {
        "task_id": task_id,
        **f.summary(),
        **spending(rows),
        "turns": turns,
        "effects": effects,
        "attention": attention,
        "attention_counts": _counts(attention),
        "failed_step": failed_step(rows),
        "delivered": (f.delivery or {}).get("summary"),
        "delivery": f.delivery,
        "parent_id": next(iter(await ancestors(conn, task_id)), None),
        "fenced_by": await fenced_by(conn, task_id),
        "children": await reports(conn, task_id),
        **{k: v for k, v in (await tree_spending(conn, task_id)).items() if k != "charges"},
    }


def failed_step(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The `step.failed` the task waits on: its payload, row id and time,
    while it is the latest row but for notices and gateway rows; else None."""
    last = next((r for r in reversed(rows) if r["type"] not in ledger.QUIET), None)
    if last is None or last["type"] != "step.failed":
        return None
    return {**last["payload"], "row": last["id"], "at": str(last.get("at"))}


def _digest(rows: list[dict[str, Any]]) -> tuple[dict[str, str | None], dict[str, str], list[dict[str, Any]]]:
    """The turns (each with its outcome, None while open), the effects (each
    with its state), and the attention log of one task's rows."""
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
            effects[p["effect_id"]] = "released"
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
    return turns, effects, attention


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


async def index(conn) -> list[dict[str, Any]]:
    """Every task, newest first: its id, instruction, state, parent, metered
    spending, attention counts, what came after its merges
    (`outcomes.summary`: `merges`, `feedback_after`, `used`, `reworked`),
    and the time of its last row. The status page's task list; it runs no
    git."""
    done = await outcomes.done_merges(conn)
    ids = await (
        await conn.execute(
            "SELECT id, body->>'instruction', body->>'parent_id' FROM documents WHERE kind = 'task'"
        )
    ).fetchall()
    found = []
    for task_id, instruction, parent_id in ids:
        rows = await ledger.read(conn, task_id)
        f = machine.fold(rows)
        _, _, attention = _digest(rows)
        state = (
            "calibration"
            if f.calibration
            else "stopped"
            if _stopped(rows)
            else "node"
            if f.legacy
            else f.state.value
        )
        found.append(
            {
                "task_id": task_id,
                "instruction": instruction,
                "parent_id": parent_id,
                "state": state,
                "spent_usd_micros": spending(rows)["spent_usd_micros"],
                "attention_counts": _counts(attention),
                **outcomes.summary(rows, done),
                "last_at": max((r["at"] for r in rows if r.get("at")), default=None),
            }
        )
    return sorted(found, key=lambda t: t["last_at"] or datetime.min.replace(tzinfo=UTC), reverse=True)


def _stopped(rows: list[dict[str, Any]]) -> bool:
    return any(r["type"] == "task.stopped" for r in rows)


async def attention_log(conn) -> list[dict[str, Any]]:
    """Every task's attention entries in time order, each with its task id
    and the row time it was recorded at (an unanswered question carries the
    time it was asked)."""
    entries = []
    for (task_id,) in await (await conn.execute("SELECT id FROM documents WHERE kind = 'task'")).fetchall():
        rows = await ledger.read(conn, task_id)
        _, _, attention = _digest(rows)
        asked = {r["payload"].get("question_id"): r["at"] for r in rows if r["type"] == "question.asked"}
        for entry in attention:
            at = (entry.get("provenance") or {}).get("at") or asked.get(entry.get("question_id"))
            entries.append(
                {"task_id": task_id, "at": at.isoformat() if hasattr(at, "isoformat") else at, **entry}
            )
    return sorted(entries, key=lambda e: e["at"] or "")
