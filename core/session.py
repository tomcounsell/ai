"""The working session: the task's one harness session, in which clarify,
plan, build, and patch run, each turn resuming the one before.

Serves Mission item 1 (one working context from inspection to delivery) and
Mission item 6 (Tom's answer lands in the context that asked). `run` runs
one state's turns until the task leaves that state, or until there is
something for Tom: a failed turn, a stop. A turn that finishes without its
stage's signal ends the run (`idle`); the next step resumes it, prompted
`Continue.`. When it left only `.valor/advice.md`, the run goes on: the
advisor (`fresh.advise`) answers first and the next turn opens with that
answer, quoted. `.valor/stop.md` ends the work from any working state.
The router (`core/router.py`) decides what runs next.

A turn's prompt is data from the row that moved the task into its state
(`Fold.entry`): the instruction, Tom's answer, a critique's findings, every
finding of the join, or Tom's feedback, under a short label; `Continue.`
once a turn in the state has finished. What the turn should do with it is
the stage file its Brief carries (`skills/sdlc/<state>.md`). An answer,
findings, or feedback is spent only by a turn that finishes: after a turn
that fails or is stopped, the next one opens with it again. Tom's steering
written while a turn runs reaches it after its current step (`mid_turn`);
what the turn did not take opens the next one. Each prompt is
followed by what became of the effects the previous turn requested, and
then by the latest delivery of each child that has delivered.

What a turn left under `.valor/` (see `core.signals`) is one
`turn.collected` row carrying the state it ran in and its verdict, with a
`question.asked` or `plan.written` beside it when the verdict calls for
one. A signal that means nothing in the state, a plan not committed, or a
candidate on a tree with uncommitted changes goes to `errors`, and the next
prompt says so. The merge is the kernel's to request: a turn's request for
one never reaches the broker. An `email.send` naming `reply_to` is made
the reply to all of that received email before it is requested, so the
request names its final recipients.

Every question is a `question.asked` row and its answer a
`question.answered` row with Tom's provenance, and his feedback a
`feedback.given` row, so the attention a task cost is a fold over its
ledger (`tasks.status`).

A chat session (`Brief.chat`, `core/intake.py`) is one long-lived
working session in `clarify`, resumed once per wake. Its prompt is what
reached it since its last finished turn, in order (`chat_prompt`): the
chat's messages, woken or heard, each with when, who and its id, and its
tasks' reports (`child.reported`, `core/notices.py`); its first opens
with its instruction. A message that wakes it while a turn runs reaches
that turn after its current step (`mid_turn`, `core/runs.py`), and its
next prompt leaves out what the turn took. It answers by requesting sends; `stop.md` ends it,
and no other signal counts. A turn ends the run `listening`: it is
stepped again only when a message wakes it, a task reports
(`chat_waiting`), or a wake it asked for is due (`wake_due`), never on its
own effects' outcomes. A turn's `wake.md` asks to be woken after a delay,
watching a post (`wakes`): a reply to the post, or a message in its
thread, drops the wake; anything else leaves it pending, and the turn
after it is due opens with `# Your wake-up` (`_wake_report`). Before each turn
its checkout is brought up to the project's branch (`refresh`). It
controls its own compaction: its prompt gives its context size as it
passes each reminder (`context_report`), and a turn that leaves
`compact.md` is followed by a `/compact` turn in the same session
(`turn.started` `compaction`), which acts on no message and is never
collected. Claude Code compacts it only past the forced compaction
(`core.settings.compaction`). Either way the summary follows
`skills/sdlc/compact.md`, through Valor's PreCompact hook.
"""

import asyncio
import dataclasses
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from core import (
    broker,
    db,
    git,
    ledger,
    machine,
    mail,
    outcomes,
    persona,
    reactions,
    runs,
    signals,
    tasks,
    workspace,
)
from core.gateway import Gateway
from core.machine import State
from core.settings import compaction, settings

# Builds one turn's command: (prompt, session to resume or None, Brief).
TurnFor = Callable[[str, str | None, tasks.Brief], Callable[[str, str], runs.TurnCommand]]
Alive = Callable[[], Awaitable[bool]]
# Puts a turn's question to the advisor: (gateway, task id, dsn, alive, the
# `turn.collected` payload that asked) -> a status (`fresh.advise`).
Advise = Callable[[Gateway, str, str, Alive, dict[str, Any]], Awaitable[dict[str, Any]]]
ADVICE_GIVEN = "advice.given"
ADVICE_BESIDE = "advice.md beside a signal that ends the stage; not asked"
ADVICE_UNFINISHED = "advice.md from a turn that did not finish; not asked"
STOP_BESIDE = "stop.md beside another signal that ends the stage; the other signal counts"


async def _always() -> bool:
    return True


async def run(
    gateway: Gateway,
    task_id: str,
    turn_for: TurnFor,
    dsn: str | None = None,
    alive: Alive = _always,
    performers: broker.Performers | None = None,
    advise: Advise | None = None,
) -> dict[str, Any]:
    """Run turns in the task's current working state until it leaves it.
    Returns `status`: `moved` (the fold left the state), `idle` (a turn
    finished with no signal that ends the stage), `failed`, `stopped`, or
    `lock lost` (the router's run lock died), with the task's `state` from
    `tasks.status`. An `idle` turn ends the run, so the kernel can give the
    turn slot to other ready work before the task's next `Continue.` turn;
    one that asked the advisor goes on to its answer. Before each turn, a question for the
    advisor that is still pending (`pending_advice`) is put to `advise`
    (`fresh.advise`); its `lock lost` or `stopped` ends the
    run as a turn's does, so the question is asked on the next run. Before
    a `patch` turn of a task with a done merge, the merged head is brought
    into the work branch (`merged_into_work`), and before the rebase a
    moved target asks for, its new tip is put on the task's origin
    (`target_into_origin`)."""
    dsn = dsn or gateway.dsn
    performers = performers or broker.Performers()
    async with await db.connect(dsn) as conn:
        state = machine.fold(await ledger.read(conn, task_id)).state
    if state not in machine.WORKING:
        return {"status": "moved"}
    while True:
        if not await alive():
            return {"status": "lock lost"}
        async with await db.connect(dsn) as conn:
            now = await tasks.status(conn, task_id)
            if now["state"] != state.value:
                return {"status": "moved", "state": now}
            b = await tasks.brief(conn, task_id)
            asked = pending_advice(await ledger.read(conn, task_id)) if advise else None
            stale = None
            if b.chat and asked is None:
                stale = await asyncio.to_thread(refresh, b)
            woke: list[str] = []
            if asked is None:
                now_at = datetime.now(UTC)
                prompt, resume, read_upto = await next_prompt(conn, task_id, now_at)
                if b.chat:
                    woke = [w["turn_id"] for w in owed(await ledger.read(conn, task_id), now_at)]
            rows = await ledger.read(conn, task_id) if state is State.PATCH and asked is None else []
        if asked is not None:
            advised = await advise(gateway, task_id, dsn, alive, asked)
            if advised.get("status") in ("lock lost", "stopped"):
                return advised
            continue
        merged = await asyncio.to_thread(merged_into_work, b, rows) if rows else None
        moved = await asyncio.to_thread(target_into_origin, b, rows) if rows else None
        merged = "\n\n".join(x for x in (merged, moved, stale) if x)
        if merged:
            prompt = f"{prompt}\n\n{merged}"
        try:
            ended = await runs.run_turn(
                gateway,
                task_id,
                turn_for(prompt, resume, b),
                dsn=dsn,
                state=state,
                offered=performers.offered(b.max_effect_class),
                read_upto=read_upto,
                steering=lambda rows, upto=read_upto, chat=bool(b.chat): mid_turn(rows, upto, chat),
            )
        except tasks.TaskStopped:
            return {"status": "stopped"}
        found = (
            await asyncio.to_thread(signals.collect, b.workspace, ended["turn_id"])
            if b.workspace
            else signals.Signals()
        )
        if not await alive():
            return {"status": "lock lost", "turn": ended}
        ok = ended["outcome"] == "done" and not ended["result"].get("is_error")
        async with await db.connect(dsn) as conn:
            await record(
                conn,
                task_id,
                ended["turn_id"],
                found,
                state=state,
                workspace=b.workspace,
                finished=ok,
                brief=b,
                performers=performers,
                woke=woke,
            )
            now = await tasks.status(conn, task_id)
            asking = advise is not None and pending_advice(await ledger.read(conn, task_id)) is not None
        if now["state"] == State.STOPPED.value:
            return {"status": "stopped", "state": now, "turn": ended}
        if now["state"] != state.value:
            return {"status": "moved", "state": now, "turn": ended}
        if not ok:
            return {"status": "failed", "state": now, "turn": ended}
        if b.chat:
            if found.compact is not None:
                # The session asked to compact: it is, in place, before its next turn.
                try:
                    await runs.run_turn(
                        gateway,
                        task_id,
                        turn_for(
                            compact_prompt(found.compact), ended["result"].get("session_id") or resume, b
                        ),
                        dsn=dsn,
                        state=state,
                        offered=performers.offered(b.max_effect_class),
                        compaction=True,
                    )
                except tasks.TaskStopped:
                    return {"status": "stopped"}
            return {"status": "listening", "state": now, "turn": ended}
        if not asking:
            return {"status": "idle", "state": now, "turn": ended}


def _plan(workspace: str | None, raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """A `plan.written` payload from a turn's `plan.json`, or why not. Why
    not names the field and what is wrong with it (its JSON type, or git's
    answer about the path), never the field's value: the turn wrote it, and
    the reason is kernel text."""
    try:
        path = raw["path"]
        counts = {k: raw[k] for k in ("critique_rounds", "review_rounds")}
    except KeyError as exc:
        return None, f"plan.json lacks {exc.args[0]!r}"
    if not isinstance(path, str):
        return None, f"plan.json's path is {signals.kind(path)}, not a string"
    for k, v in counts.items():
        if not isinstance(v, int) or isinstance(v, bool):
            return None, f"plan.json's {k} is {signals.kind(v)}; each count is 0, 1, or 2"
        if v not in machine.ROUNDS:
            return None, f"plan.json's {k} is out of range; each count is 0, 1, or 2"
    stakes, scope = raw.get("stakes"), raw.get("scope")
    if stakes is not None and not isinstance(stakes, str):
        return None, f"plan.json's stakes is {signals.kind(stakes)}, not a string"
    if scope is not None and not isinstance(scope, list):
        return None, f"plan.json's scope is {signals.kind(scope)}, not a list"
    try:
        if not git.is_repo(workspace):
            return None, "the workspace is not a git repository, so the plan cannot be committed"
        head = git.head(workspace)
        try:
            body = git.show(workspace, "HEAD", path) if head else None
            left = git.dirty(workspace, path) if body is not None else []
        except (OSError, ValueError) as exc:  # the path cannot be an argument: too long, or a NUL
            return None, f"plan.json's path cannot be passed to git: {getattr(exc, 'strerror', None) or exc}"
    except git.GitError as exc:
        return None, str(exc)
    if body is None:
        return None, "plan.json's path is not committed at HEAD"
    if left:
        return None, "plan.json's path has changes not committed"
    return {
        "path": path,
        "commit": head,
        "sha256": hashlib.sha256(body).hexdigest(),
        "stakes": stakes or "",
        **counts,
        "scope": scope or [],
    }, None


def _candidate(workspace: str | None, turn_id: str) -> tuple[dict[str, str] | None, str | None]:
    try:
        if not git.is_repo(workspace):
            return None, "the workspace is not a git repository, so there is no commit to check"
        left = git.dirty(workspace)
        head = git.head(workspace)
    except git.GitError as exc:
        return None, str(exc)
    if left:
        return None, "done.md with uncommitted changes; commit everything first:\n" + "\n".join(left)
    if head is None:
        return None, "the workspace has no commit"
    return {"sha": head, "turn_id": turn_id}, None


def refresh(brief: tasks.Brief) -> str | None:
    """Bring a chat session's checkout up to its project's branch: fetch
    the repository into the kernel's cache, then fast-forward the work
    branch to its tip (`workspace.bring_merged`). When it cannot, a note
    for the turn's prompt."""
    if not brief.mirror or not brief.workspace or not brief.push_url or not brief.project:
        return None
    try:
        tip = workspace.fetch_tip(workspace.Spec.load(brief.project["name"]), brief.mirror)
        workspace.bring_merged(brief.workspace, brief.mirror, brief.push_url, tip, brief.target_branch,
                               brief.harness["sandbox_profile"], f"refresh-{brief.id}")  # fmt: skip
    except (workspace.Refused, git.GitError) as exc:
        return f"# Your checkout\n\nThe kernel could not bring it up to the project's branch: {exc}"
    return None


CHAT_WAKES = ("message.steered", "child.reported")
CHAT_ROWS = ("message.steered", "message.heard", "child.reported")


def chat_waiting(rows: list[dict]) -> bool:
    """Whether a chat session has something to answer: a message that
    wakes it or a task's report, since the prompt of its last finished
    turn, that the turn did not take mid-turn."""
    f = machine.fold(rows)
    return any(
        r["type"] in CHAT_WAKES and int(r.get("id") or 0) > f.steer_since and r["id"] not in f.spent
        for r in rows
    )


def mid_turn(rows: list[dict], read_upto: int, chat: bool) -> list[tuple[int, str]]:
    """What a running turn whose prompt read the rows up to `read_upto` is
    owed: each `message.steered` row after it, by id, rendered as the next
    prompt would render it (`_steering`; for a chat, `intake.chat_line`,
    with the chat's heard messages since the one before it)."""
    from core import intake

    shown = reactions.shown(rows)
    owed, heard = [], []
    for r in rows:
        if int(r.get("id") or 0) <= read_upto:
            continue
        p = r["payload"]
        if chat and r["type"] in ("message.heard", "message.steered"):
            heard.append(f"- {intake.chat_line(p, shown.get(p.get('received_id')))}")
        if r["type"] != "message.steered":
            continue
        if chat:
            owed.append((r["id"], "\n".join(["In the chat since your last step:", *heard])))
            heard = []
        else:
            owed.append((r["id"], _steering([p], shown)))
    return owed


def chat_prompt(rows: list[dict]) -> str:
    """What reached a chat session since its last finished turn began,
    oldest first: each message as `intake.chat_line` renders it, with the
    reaction on it; the reaction now on a message an earlier turn read,
    when it was set since, such as the done the last turn earned (quiet, it
    wakes nothing); and each task's report under its task id."""
    from core import intake

    f = machine.fold(rows)
    since = f.steer_since
    # A message the last finished turn took mid-turn came with the chat's
    # heard messages before it (`mid_turn`): the session has read them.
    upto = max((i for i in f.spent if i > since), default=since)
    shown = reactions.shown(rows)
    now = reactions.current(rows)
    read = {
        r["payload"].get("received_id"): r["payload"].get("message_id")
        for r in rows
        if r["type"] in CHAT_ROWS and (int(r.get("id") or 0) <= since or r["id"] in f.spent)
    }
    lines = []
    for r in rows:
        if int(r.get("id") or 0) <= since or r["id"] in f.spent:
            continue
        p = r["payload"]
        if r["type"] == "message.heard" and r["id"] < upto:
            continue
        if (
            r["type"] == "reaction.requested"
            and p.get("stage") != "seen"
            and p.get("received_id") in read
            and now.get(p["received_id"]) is p
        ):
            lines.append(
                f"- Your reaction on message {read[p['received_id']]} is now {reactions.emoji_of(p)}: "
                f"{reactions.why(p)}"
            )
        elif r["type"] not in CHAT_ROWS:
            continue
        elif r["type"] == "child.reported" and p.get("kind") == "board":
            lines.append("- The decision board changed:")
            lines += ["  " + line if line else "" for line in str(p.get("text") or "").splitlines()]
        elif r["type"] == "child.reported":
            lines.append(f"- Your task {p.get('child_id')} reports ({p.get('kind')}):")
            lines += ["  " + line if line else "" for line in str(p.get("text") or "").splitlines()]
        else:
            lines.append(f"- {intake.chat_line(p, shown.get(p.get('received_id')))}")
    return "\n".join(["# In the chat since your last turn", "", *lines]) if lines else ""


def compact_prompt(text: str) -> str:
    """The `/compact` turn's prompt: Claude Code's own command, with the
    text of the session's `compact.md` as extra instructions."""
    return "/compact" if text == "(empty compact.md)" else f"/compact {text}"


WAKE = re.compile(r"(\d+)\s*([smh]?)(?:\s+post:\s*(\S+))?")
UNITS = {"s": 1, "": 60, "m": 60, "h": 3600}


def _wake(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """A `wake.md`'s request, or why it is not one: a first line that is a
    delay (`90s`, `20m`, `2h`, or minutes) and optionally `post: <the
    turn's effect file, or an earlier post's message id>`, then a note."""
    first, _, note = text.partition("\n")
    m = WAKE.fullmatch(first.strip())
    if m is None:
        return None, "wake.md's first line is not a delay such as `20m` (then `post: <file or id>`); not set"
    return {"after_s": int(m[1]) * UNITS[m[2]], "post": m[3], "note": note.strip() or None}, None


def wakes(rows: list[dict]) -> list[dict[str, Any]]:
    """A chat session's pending wakes, oldest first. Each finished turn's
    `wake` adds one, replacing the wake on the same post (with no post, the
    one with no post). Its post is the effect file of that turn it names,
    by the `effect_id` its collected entry carries, whose `effect.outcome`
    lists the parts sent; or an earlier post's message id. A
    `message.steered` or `message.heard` replying to one of the post's
    parts or its thread drops it, and a finished turn whose prompt gave it
    (`woke`) spends it. `status` is `sent`, `held` (no outcome yet), or
    `gone`, with `why`; `due` is when it is owed."""
    sent: dict[str, list[dict]] = {}
    gone: dict[str, str] = {}
    for r in rows:
        p = r["payload"]
        if r["type"] == "effect.outcome" and isinstance((p.get("result") or {}).get("sent"), list):
            sent[p.get("effect_id")] = [s for s in p["result"]["sent"] if isinstance(s, dict)]
        if r["type"] == "effect.outcome" and p.get("kind") in ("failed", "refused"):
            gone[p.get("effect_id")] = p.get("error") or p["kind"]
        elif r["type"] == "effect.refused":
            gone[p.get("effect_id")] = p.get("reason") or "refused"
    parts = [s for got in sent.values() for s in got]
    pending: dict[str | None, dict[str, Any]] = {}
    for r in rows:
        p = r["payload"]
        if r["type"] in ("message.steered", "message.heard") and p.get("reply_to") is not None:
            for key in [k for k, w in pending.items() if str(p["reply_to"]) in w["ids"]]:
                del pending[key]
        if r["type"] != "turn.collected":
            continue
        if p.get("verdict") != "failed":
            for key in [k for k, w in pending.items() if w["turn_id"] in (p.get("woke") or ())]:
                del pending[key]
        asked = p.get("wake")
        if not isinstance(asked, dict):
            continue
        w = {**asked, "turn_id": p.get("turn_id"), "set_at": r["at"], "ids": set(), "status": "sent",
             "why": None, "due": r["at"] + timedelta(seconds=asked["after_s"])}  # fmt: skip
        ref, key = asked.get("post"), None
        entry = next((e for e in p.get("effects") or [] if ref and e.get("file") == ref), None)
        if entry is not None:
            got = sent.get(entry.get("effect_id"), [])
            w["ids"] = {str(x) for s in got for x in (s.get("message_id"), s.get("thread_ts")) if x}
            w["why"] = entry.get("error") or gone.get(entry.get("effect_id"))
            w["status"] = "gone" if w["why"] else "sent" if w["ids"] else "held"
            key = str(got[0].get("message_id")) if got else f"{w['turn_id']}/{ref}"
        elif ref and ref.endswith(".json"):
            w["status"], w["why"], key = "gone", f"the turn requested no {ref}", f"{w['turn_id']}/{ref}"
        elif ref:
            same = [s for s in parts if str(s.get("message_id")) == ref]
            w["ids"] = {ref, *(str(s["thread_ts"]) for s in same if s.get("thread_ts"))}
            key = ref
        w["key"] = key
        pending.pop(key, None)
        pending[key] = w
    return list(pending.values())


def owed(rows: list[dict], now: datetime) -> list[dict[str, Any]]:
    """The pending wakes whose delay has passed at `now`: the next turn's
    prompt gives them (`_wake_report`)."""
    return [w for w in wakes(rows) if now >= w["due"]]


def wake_due(rows: list[dict], now: datetime | None = None) -> bool:
    """Whether a chat session should be stepped for a wake: one is owed
    and no turn has started since it fell due. A wake turn that fails
    leaves it owed, to the retry `router.retry_due` times."""
    started = [r["at"] for r in rows if r["type"] == "turn.started" and not r["payload"].get("compaction")]
    return any(not any(at >= w["due"] for at in started) for w in owed(rows, now or datetime.now(UTC)))


def _delay(seconds: int) -> str:
    for unit, size in (("hour", 3600), ("minute", 60)):
        if seconds >= size and seconds % size == 0:
            n = seconds // size
            return f"{n} {unit}{'s' if n != 1 else ''}"
    return f"{seconds} second{'s' if seconds != 1 else ''}"


def _when(at: datetime) -> str:
    """`at` in the operator's time and in UTC: the zone his identity names
    in parentheses (`UTC+7 (Asia/Bangkok)`)."""
    who = persona.identity(settings.persona_dir)
    zone = re.search(r"\(([^)]+)\)", who["timezone"])
    local = at.astimezone(ZoneInfo(zone[1]) if zone else UTC)
    return f"{local:%H:%M} {who['operator'].split()[0]}'s time ({at.astimezone(UTC):%H:%M} UTC)"


def _wake_report(owed_wakes: list[dict[str, Any]]) -> str:
    """`# Your wake-up`: each owed wake, what became of its post, and its note."""
    lines = []
    for w in owed_wakes:
        line = f"- You asked to be woken {_delay(w['after_s'])} after your turn that ended at {_when(w['set_at'])}"
        if w.get("post"):
            line += {
                "sent": f", watching your post {w['key']}: nothing has replied to it or come in its thread since.",
                "held": f", watching your post ({w['post']}): it has not gone out yet.",
                "gone": f", watching your post ({w['post']}): it never went out ({w['why']}).",
            }[w["status"]]
        else:
            line += "."
        lines.append(line + (f" Your note: {w['note']}" if w.get("note") else ""))
    return "\n".join(["# Your wake-up", "", *lines]) if lines else ""


def context_size(rows: list[dict], turn_id: str, model: str) -> int | None:
    """A turn's context when it ended: its last complete, sent model call on
    the session's model (`gateway.charged`), all of whose input and output
    the next call carries. Calls are in the order they closed; a turn's
    subagents run in the foreground, so its last call on its model is the
    main agent's answer."""
    size = None
    for r in rows:
        p = r["payload"]
        if r["type"] != "gateway.charged" or p.get("turn_id") != turn_id or p.get("model") != model:
            continue
        if p.get("complete") and not p.get("unsent"):
            u = p.get("usage") or {}
            size = sum(int(u.get(k) or 0) for k in CONTEXT_USAGE)
    return size


CONTEXT_USAGE = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")


def context_report(rows: list[dict], model: str) -> str:
    """For a chat session's next prompt: its context size, when its last
    turn passed a reminder (`core.settings.compaction`) that the turn before
    had not (taken as 0 when the context shrank, as after a compaction), so
    each reminder is given once; or what became of a compaction it asked
    for, when one ran since."""
    marks = compaction(model)
    working: list[str] = []
    compacted = None
    for r in rows:
        p = r["payload"]
        if r["type"] == "turn.started" and not p.get("fresh"):
            if p.get("compaction"):
                compacted = p["turn_id"]
            else:
                working.append(p["turn_id"])
                compacted = None
    if compacted is not None:
        ended = next(
            (
                r["payload"]
                for r in rows
                if r["type"] == "turn.ended" and r["payload"].get("turn_id") == compacted
            ),
            {},
        )
        result = ended.get("result") if isinstance(ended.get("result"), dict) else {}
        if ended.get("outcome") == "done" and not result.get("is_error"):
            return ""
        return (
            f"# Your compaction\n\nThe compaction you asked for did not finish (it ended "
            f"{ended.get('outcome') or 'without a result'}); your conversation is as it was. "
            "Ask again with `.valor/compact.md` when you choose."
        )
    sizes = [n for n in (context_size(rows, t, model) for t in working) if n is not None]
    if marks is None or not sizes:
        return ""
    now = sizes[-1]
    before = sizes[-2] if len(sizes) > 1 and sizes[-2] <= now else 0
    if not any(before < m <= now for m in marks[0]):
        return ""
    return (
        f"# Your context\n\nYour conversation is about {now:,} tokens. You may compact it "
        f"(`.valor/compact.md`); past {marks[1]:,} it is compacted for you."
    )


def merged_into_work(brief: tasks.Brief, rows: list[dict]) -> str | None:
    """Bring the task's last done merge's head into its work branch
    (`workspace.bring_merged`), so a feedback round builds on what the
    merge landed and its own merge is a fast-forward of it. A task without
    a mirror or a done merge has nothing to bring. When it cannot, a note
    for the turn's prompt naming the head, how to bring it in, and why."""
    done = outcomes.merges(rows)
    if not done or not brief.mirror or not brief.workspace or not brief.push_url:
        return None
    head, target = done[-1]["head_sha"], done[-1]["target_branch"]
    try:
        workspace.bring_merged(
            brief.workspace,
            brief.mirror,
            brief.push_url,
            head,
            target,
            brief.harness["sandbox_profile"],
            f"merged-{brief.id}",
        )
    except git.GitError as exc:
        return (
            f"# The merged head\n\nThe merge landed {head} on {target}; this round builds on it. "
            f"The kernel could not bring it into the work branch: {exc}\n"
            f"Bring it in before you commit: git fetch origin {target}, then git merge --ff-only {head}, "
            f"or, when the branch has diverged from it, rebase the branch onto {head}"
        )
    return None


def target_into_origin(brief: tasks.Brief, rows: list[dict]) -> str | None:
    """Before the rebase a moved target asks for (`rebase.requested`), put
    the target's new tip, which the merge's lookup fetched into the kernel
    mirror, on the task's bare origin, so the working session fetches it
    from there. Nothing to do for a task without a mirror, or whose origin
    is the target itself. When it cannot, a note for the turn's prompt."""
    f = machine.fold(rows)
    entry = f.entry or {}
    if entry.get("type") != "rebase.requested" or f.entry_finished:
        return None
    p = entry["payload"]
    tip, target = p.get("tip"), p.get("target_branch")
    if not tip or not target or not brief.mirror or not brief.push_url or brief.push_url == brief.origin_url:
        return None
    try:
        git.trusted(brief.mirror, "push", "-q", "--no-verify", "--force", brief.push_url,
                    f"{tip}:refs/heads/{target}")  # fmt: skip
    except git.GitError as exc:
        return f"The kernel could not put {target}'s new tip {tip} on the task's origin: {exc}"
    return None


def _keep(brief: tasks.Brief | None, sha: str, ref: str, turn_id: str) -> str | None:
    """Fetch a plan commit or a candidate into the kernel mirror of a task
    the kernel provisioned; why not, or None. A task without a mirror keeps
    nothing."""
    if brief is None or not brief.mirror:
        return None
    try:
        workspace.fetch_into_mirror(
            brief.mirror, brief.workspace, sha, ref, brief.harness["sandbox_profile"], f"mirror-{turn_id}"
        )
    except (workspace.FetchRefused, git.GitError) as exc:
        return str(exc)
    return None


def _verdict(
    state: State,
    found: signals.Signals,
    workspace: str | None,
    turn_id: str,
    finished: bool,
    brief: tasks.Brief | None = None,
) -> tuple[str, dict[str, Any], list[str]]:
    """The turn's verdict in its state, what goes with it, and the signals
    that did not count. `advice.md` counts only on a turn that finished
    and wrote no signal that ends a stage (its verdict is `idle`); then it
    goes with the verdict as `advice`."""
    verdict, extra, errors = _stage_verdict(state, found, workspace, turn_id, finished, brief)
    if found.advice is not None:
        ended = any(
            getattr(found, name) is not None for name in ("question", "no_question", "plan", "done", "stop")
        )
        if ended:
            errors.append(ADVICE_BESIDE)
        elif verdict != "idle":
            errors.append(ADVICE_UNFINISHED)
        else:
            extra["advice"] = found.advice
    return verdict, extra, errors


def _stage_verdict(
    state: State,
    found: signals.Signals,
    workspace: str | None,
    turn_id: str,
    finished: bool,
    brief: tasks.Brief | None = None,
) -> tuple[str, dict[str, Any], list[str]]:
    """The turn's verdict from the signals that end a stage."""
    errors: list[str] = list(found.unreadable)
    extra: dict[str, Any] = {}
    if brief is not None and brief.chat:
        # A chat session answers by sends; only stop.md ends it.
        for name in ("question", "no_question", "plan", "done"):
            if getattr(found, name) is not None:
                errors.append(f"{name} means nothing in a chat session; ignored")
        if found.stop is not None:
            return "ended", extra, errors
        if found.wake is not None and not finished:
            errors.append("wake.md from a turn that did not finish; not set")
        elif found.wake is not None:
            wake, why = _wake(found.wake)
            if wake is None:
                errors.append(why)
            else:
                extra["wake"] = wake
        return ("idle" if finished else "failed"), extra, errors
    meant = {
        State.CLARIFY: ("question", "no_question"),
        State.PLAN: ("question", "plan"),
        State.BUILD: ("question", "done"),
        State.PATCH: ("question", "done"),
    }[state]
    for name in ("no_question", "plan", "done"):
        if name not in meant and getattr(found, name) is not None:
            errors.append(f"{name} means nothing in {state}; ignored")
    for name in ("compact", "wake"):
        if getattr(found, name) is not None:
            errors.append(f"{name} means nothing outside a chat session; ignored")
    # `stop.md` ends the work from any working state, unless another of the
    # state's signals is beside it.
    beside = any(getattr(found, name) is not None for name in meant)
    if found.stop is not None and beside:
        errors.append(STOP_BESIDE)
    if found.plan_error:
        errors.append(found.plan_error)
    if found.question is not None:
        return "asked", extra, errors
    if state is State.CLARIFY and found.no_question is not None:
        return "no_material_question", extra, errors
    if state is State.PLAN and found.plan is not None:
        plan, why = _plan(workspace, found.plan)
        if plan is not None:
            why = _keep(brief, plan["commit"], f"refs/valor/plans/{turn_id}", turn_id)
            plan = None if why else plan
        if plan is None:
            errors.append(why)
        else:
            extra["plan"] = plan
            return "planned", extra, errors
    if found.stop is not None and not beside:
        return "ended", extra, errors
    if state in (State.BUILD, State.PATCH) and found.done is not None:
        candidate, why = _candidate(workspace, turn_id)
        if candidate is not None:
            why = _keep(brief, candidate["sha"], f"refs/valor/candidates/{turn_id}", turn_id)
            candidate = None if why else candidate
        if candidate is None:
            errors.append(why)
        else:
            extra["candidate"] = candidate
            return "candidate", extra, errors
    return ("idle" if finished else "failed"), extra, errors


UNSTORABLE = ledger.UNSTORABLE


def _collected(
    turn_id: str,
    state: State,
    found: signals.Signals,
    verdict: str | None,
    candidate: dict[str, str] | None,
    errors: list[str],
    effects: list[dict[str, Any]],
    advice: str | None = None,
    wake: Any = None,
    woke: list[str] | None = None,
) -> dict[str, Any]:
    """The `turn.collected` payload. `advice` is the question for the
    advisor when it counted (`_verdict`), never `found.advice` as written;
    `wake` the wake it asked for, read (`_wake`); `woke` the turns whose
    wakes its prompt gave it (`owed`)."""
    return {
        "turn_id": turn_id,
        "state": state.value,
        "verdict": verdict,
        "question": found.question,
        "no_question": found.no_question,
        "done": found.done,
        "advice": advice,
        "stop": found.stop,
        "plan": found.plan,
        "restated": found.stakes,
        "candidate": candidate,
        "errors": errors,
        "effects": effects,
        "screens": found.screens,
        "wake": wake,
        "woke": woke or [],
    }


def _size(value: Any) -> float:
    """The length of `value` as JSON text, to order parts by; a value too
    deep to serialize sorts first."""
    try:
        return len(json.dumps(value, ensure_ascii=False))
    except RecursionError, ValueError, TypeError:
        return float("inf")


def _parts(found: signals.Signals) -> list[tuple[float, tuple]]:
    """Each part of `found` the turn wrote, with its size."""
    parts = [
        (_size(getattr(found, name)), ("text", name))
        for name in signals.TEXT_SIGNALS
        if getattr(found, name) is not None
    ]
    if found.plan is not None:
        parts.append((_size(found.plan), ("plan",)))
    if found.stakes is not None:
        parts.append((_size(found.stakes), ("stakes",)))
    if found.screens:
        parts.append((_size(found.screens), ("screens",)))
    parts += [(_size(e), ("effect", i)) for i, e in enumerate(found.effects) if "request" in e]
    return parts


def _drop(found: signals.Signals, part: tuple, reason: str) -> None:
    """Answer one part of `found` as unreadable with `reason`."""
    if part[0] == "text":
        setattr(found, part[1], None)
        found.unreadable.append(f"{part[1]}.md is unreadable: {reason}")
    elif part[0] == "plan":
        found.plan, found.plan_error = None, f"plan.json is unreadable: {reason}"
    elif part[0] == "stakes":
        found.stakes = None
        found.unreadable.append(f"stakes.json is unreadable: {reason}")
    elif part[0] == "screens":
        found.screens = []
        found.unreadable.append(f"the screens are unrecorded: {reason}")
    else:
        entry = found.effects[part[1]]
        found.effects[part[1]] = {"file": entry["file"], "error": f"unreadable request: {reason}"}


async def _storable(conn, turn_id: str, state: State, found: signals.Signals) -> signals.Signals:
    """`found` as the `turn.collected` row can hold it, asked of Postgres
    (`ledger.unstorable`) of the row as it will be written. Postgres's
    refusal (a NUL character, a NaN or infinite number, nesting past the
    server's stack depth, a value past jsonb's size) is the answer, never a
    number of ours. When the row is refused, each part is asked alone,
    nested as the row nests it: a text signal, the plan, the screens, each
    request; a part refused alone is answered as unreadable with
    Postgres's reason. If the row is still refused, the largest part left
    is answered so and the row asked again, until it is stored; if no part
    is left and it is still refused, every part is, and the row keeps only
    what the kernel wrote and the file names of the requests."""
    errors = [*found.unreadable, *([found.plan_error] if found.plan_error else [])]
    why = await ledger.unstorable(
        conn, _collected(turn_id, state, found, None, None, errors, found.effects, found.advice, found.wake)
    )
    if why is None:
        return found
    kept = dataclasses.replace(found, effects=list(found.effects), unreadable=list(found.unreadable))
    for name in signals.TEXT_SIGNALS:
        value = getattr(kept, name)
        if value is not None and (refused := await ledger.unstorable(conn, {name: value})):
            setattr(kept, name, None)
            kept.unreadable.append(f"{name}.md is unreadable: {UNSTORABLE}: {refused}")
    if kept.plan is not None and (refused := await ledger.unstorable(conn, {"plan": kept.plan})):
        kept.plan, kept.plan_error = None, f"plan.json is unreadable: {UNSTORABLE}: {refused}"
    if kept.screens and (refused := await ledger.unstorable(conn, {"screens": kept.screens})):
        kept.screens = []
        kept.unreadable.append(f"the screens are unrecorded: {UNSTORABLE}: {refused}")
    for i, entry in enumerate(kept.effects):
        if "request" in entry and (refused := await ledger.unstorable(conn, {"effects": [entry]})):
            kept.effects[i] = {"file": entry["file"], "error": f"unreadable request: {UNSTORABLE}: {refused}"}
    errors = [*kept.unreadable, *([kept.plan_error] if kept.plan_error else [])]
    why = await ledger.unstorable(
        conn, _collected(turn_id, state, kept, None, None, errors, kept.effects, kept.advice, kept.wake)
    )
    if why is None:
        return kept
    # Refused only together: drop the largest part left, answered with
    # Postgres's reason, and ask again, so the parts that fit are kept.
    for part in sorted(_parts(kept), key=lambda part: part[0], reverse=True):
        _drop(kept, part[1], f"{UNSTORABLE} beside the turn's other parts: {why}")
        errors = [*kept.unreadable, *([kept.plan_error] if kept.plan_error else [])]
        why = await ledger.unstorable(
            conn, _collected(turn_id, state, kept, None, None, errors, kept.effects, kept.advice, kept.wake)
        )
        if why is None:
            return kept
    reason = f"{UNSTORABLE} with all it holds: {why}"
    return signals.Signals(
        plan_error=f"plan.json is unreadable: {reason}" if found.plan is not None else kept.plan_error,
        effects=[
            {"file": e["file"], "error": f"unreadable request: {reason}"} if "request" in e else e
            for e in found.effects
        ],
        unreadable=[
            *found.unreadable,
            *(
                f"{name}.md is unreadable: {reason}"
                for name in signals.TEXT_SIGNALS
                if getattr(found, name) is not None
            ),
            *([f"the screens are unrecorded: {reason}"] if found.screens else []),
        ],
    )


async def _reply_all(conn, action: broker.Action) -> tuple[broker.Action, str | None]:
    """An `email.send` naming `reply_to` (a received email's `received_id`
    or `message_id`) as the reply to all of it: recipients, subject, and
    threading from `mail.reply_all`, the turn's `body` and `files` kept.
    Returns the action, or an error when no email was received under that
    id."""
    ref = str(action.payload["reply_to"])
    row = await (
        await conn.execute(
            "SELECT payload FROM events WHERE type = 'message.received' AND task_id = 'email' "
            "AND (payload->>'received_id' = %s OR payload->>'message_id' = %s) ORDER BY id LIMIT 1",
            (ref, ref),
        )
    ).fetchone()
    if row is None:
        return action, f"reply_to {ref} names no received email"
    payload = {
        **mail.reply_all(row[0], settings.email_address),
        "body": action.payload.get("body") or "",
        "files": action.payload.get("files") or [],
    }
    return broker.Action("email.send", ",".join(sorted(payload["to"])), payload), None


async def record(
    conn,
    task_id: str,
    turn_id: str,
    found: signals.Signals,
    *,
    state: State,
    workspace: str | None,
    finished: bool = True,
    brief: tasks.Brief | None = None,
    performers: broker.Performers | None = None,
    woke: list[str] | None = None,
) -> str:
    """Ledger what a turn left, sending each effect request but a merge to
    the broker. Postgres judges the signals before the verdict and the
    broker (`_storable`): what jsonb refuses is answered as unreadable with
    Postgres's reason, so the verdict reads only what the ledger holds and
    a request so answered never reaches the broker. The broker judges its
    own rows where it writes them. The `turn.collected` row, and the
    `question.asked` or `plan.written` beside it, are judged where they are
    written (`_written`): when Postgres refuses them, each error and effect
    entry it refuses alone is answered (`_answered`) and the rows are
    written again; when it still refuses, the largest part left is
    answered and the row written again, alone, until it is stored
    (`_reduce`), and failing that, one `turn.collected` holding nothing the
    turn wrote is (`_bare`); in the same transaction, the reactions owed
    the messages the turn read (`reactions.owe`). Returns the verdict recorded. For a task with a kernel
    mirror, a plan commit or a candidate counts only once it is fetched
    into the mirror.
    The verdict, with its git calls and that fetch, is read in a worker
    thread (`git.threaded`), so the kernel's loop runs meanwhile and a stop
    kills the fetch."""
    found = await _storable(conn, turn_id, state, found)
    verdict, extra, errors = await git.threaded(_verdict, state, found, workspace, turn_id, finished, brief)
    effects = []
    for entry in found.effects:
        if "request" in entry and entry["request"]["action_type"] == "merge":
            entry = {**entry, "error": "the merge is the kernel's to request"}
        elif "request" in entry:
            r = entry["request"]
            action = broker.Action(r["action_type"], r["target"], r["payload"])
            if action.action_type == "email.send" and "reply_to" in action.payload:
                action, said = await _reply_all(conn, action)
                if said:
                    effects.append({**entry, "error": said})
                    continue
            outcome = await broker.request(
                conn,
                performers or broker.Performers(),
                task_id,
                action,
                request_id=f"{turn_id}/{entry['file']}",
            )
            entry = {**entry, "effect_id": outcome.effect_id, "kind": outcome.kind, "error": outcome.error}
        effects.append(entry)
    did = await reactions.did(conn, task_id, turn_id, effects)
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        current = machine.fold(await ledger.read(conn, task_id)).state
        collected = _collected(
            turn_id,
            state,
            found,
            verdict,
            extra.get("candidate"),
            errors,
            effects,
            extra.get("advice"),
            extra.get("wake"),
            woke,
        )
        follow = None
        if current is state and verdict == "asked":
            follow = (
                "question.asked",
                {
                    "question_id": ledger.new_id(),
                    "turn_id": turn_id,
                    "text": found.question,
                    "state": state.value,
                },
            )
        elif current is state and verdict == "planned":
            follow = "plan.written", {"turn_id": turn_id, **extra["plan"]}
        elif current is state and verdict == "ended":
            follow = "task.ended", {"kind": "decided", "by": "valor", "turn_id": turn_id, "text": found.stop}
        why = await _written(conn, task_id, collected, follow)
        if why is not None:
            collected = await _answered(conn, collected, why)
            why = await _written(conn, task_id, collected, follow)
        if why is not None:
            reduced = await _reduce(conn, task_id, collected, finished, why)
            if reduced is None:
                collected = _bare(turn_id, state, finished, why)
                await ledger.append(conn, task_id, "turn.collected", collected)
            else:
                collected = reduced
        await reactions.owe(conn, task_id, turn_id, effects, did)
    return collected["verdict"]


async def _written(
    conn, task_id: str, collected: dict[str, Any], follow: tuple[str, dict] | None
) -> str | None:
    """Append `turn.collected` and the row that goes with it, judged where
    they are written: in a savepoint, so when Postgres jsonb refuses either
    the two are rolled back together and the caller writes what it can
    store instead. Why it refused, or None once both are written."""
    try:
        async with conn.transaction():
            _, why = await ledger.try_append(conn, task_id, "turn.collected", collected)
            if why is None and follow is not None:
                _, why = await ledger.try_append(conn, task_id, *follow)
            if why is not None:
                raise _Refused(why)
    except _Refused as refused:
        return str(refused)
    return None


class _Refused(Exception):
    """Rolls a savepoint back; never leaves `_written`."""


async def _answered(conn, collected: dict[str, Any], why: str) -> dict[str, Any]:
    """`collected` with each part Postgres refuses alone answered as
    unreadable with its reason: each error and each effect entry, the parts
    added after `_storable` judged the signals (the verdict's errors, and
    the broker's answer on each request)."""
    errors = []
    for e in collected["errors"]:
        refused = await ledger.unstorable(conn, {"errors": [e]})
        errors.append(f"an error is unrecorded: {UNSTORABLE}: {refused}" if refused else e)
    effects = []
    for entry in collected["effects"]:
        refused = await ledger.unstorable(conn, {"effects": [entry]})
        if refused:
            entry = {
                **{k: entry[k] for k in ("effect_id", "kind") if k in entry},
                "file": entry["file"],
                "error": f"unrecorded request: {UNSTORABLE}: {refused}",
            }
        effects.append(entry)
    return {**collected, "errors": errors, "effects": effects}


async def _reduce(
    conn, task_id: str, collected: dict[str, Any], finished: bool, why: str
) -> dict[str, Any] | None:
    """Write `turn.collected` with the parts Postgres stores together: the
    largest part left is answered with Postgres's reason, and the row is
    written again (`_written`), until it is stored. The parts are each
    error, each effect entry, the text signals, the plan, and the screens.
    An effect entry keeps its `effect_id`, `kind`, and `file`, so the
    broker's rows still match it. The row counts as finished with nothing
    (or failed), with no candidate and no row beside it, and one error
    names what was dropped. The row written, or None when no reduction is
    stored."""
    kept = {
        **collected,
        "verdict": "idle" if finished else "failed",
        "candidate": None,
        "errors": list(collected["errors"]),
        "effects": list(collected["effects"]),
    }
    parts = [(_size(e), "error", i) for i, e in enumerate(kept["errors"])]
    parts += [(_size(e), "effect", i) for i, e in enumerate(kept["effects"])]
    parts += [
        (_size(kept[name]), name, None)
        for name in (*signals.TEXT_SIGNALS, "plan")
        if kept.get(name) is not None
    ]
    if kept["screens"]:
        parts.append((_size(kept["screens"]), "screens", None))
    dropped = []
    for _, name, i in sorted(parts, key=lambda part: part[0], reverse=True):
        reason = f"{UNSTORABLE} beside the turn's other parts: {why}"
        if name == "error":
            kept["errors"][i] = f"an error is unrecorded: {reason}"
            dropped.append("an error")
        elif name == "effect":
            entry = kept["effects"][i]
            kept["effects"][i] = {
                **{k: entry[k] for k in ("effect_id", "kind") if k in entry},
                "file": entry["file"],
                "error": f"unrecorded request: {reason}",
            }
            dropped.append(entry["file"])
        else:
            kept[name] = [] if name == "screens" else None
            dropped.append({"plan": "plan.json", "screens": "the screens"}.get(name, f"{name}.md"))
        row = {
            **kept,
            "errors": [
                *kept["errors"],
                f"the turn's signals: {', '.join(dropped)} dropped to record the rest: {reason}",
            ],
        }
        why = await _written(conn, task_id, row, None)
        if why is None:
            return row
    return None


def _bare(turn_id: str, state: State, finished: bool, why: str) -> dict[str, Any]:
    """The `turn.collected` row that holds nothing the turn wrote, for when
    Postgres jsonb refuses every row that does: the turn counts as
    finished with nothing (or failed), and the next prompt says why."""
    verdict = "idle" if finished else "failed"
    found = signals.Signals()
    return _collected(
        turn_id,
        state,
        found,
        verdict,
        None,
        [f"the turn's signals: {UNSTORABLE} with all it holds: {why}"],
        [],
    )


async def answer(
    conn,
    task_id: str,
    text: str,
    *,
    by: str = "tom",
    via: str = "the command line",
    role_played: bool = False,
    effect_key: str | None = None,
    question_id: str | None = None,
) -> str:
    """Record the answer to the task's open question. `by` names who wrote
    it and `role_played` says whether they stood in for Tom; `effect_key`
    names the chat session's `task.steer` that gave it. With `question_id`
    (a call's read-back, `core/call.py`) it answers only that question:
    a task asking another one now raises `LookupError`. Returns the
    question's id."""
    text = text.strip()
    if not text:
        raise ValueError("an answer has text")
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        f = machine.fold(await ledger.read(conn, task_id))
        if f.legacy:
            raise LookupError(f"task {task_id} predates the state machine")
        if f.calibration:
            raise LookupError(f"task {task_id} is a calibration task")
        if f.state is not State.WAITING:
            raise LookupError(f"task {task_id} has no open question (it is {f.state})")
        if question_id is not None and question_id != f.open_question:
            raise LookupError(f"task {task_id} now asks another question, not {question_id}")
        await ledger.append(
            conn,
            task_id,
            "question.answered",
            {
                "question_id": f.open_question,
                "text": text,
                "provenance": ledger.provenance(by, via, role_played),
                **({"effect_key": effect_key} if effect_key else {}),
            },
        )
    return f.open_question


async def shape(
    conn,
    task_id: str,
    *,
    confirm: bool = False,
    amend: str | None = None,
    before: str | None = None,
    after: str | None = None,
    by: str = "tom",
    via: str = "the command line",
    role_played: bool = False,
) -> list[str]:
    """The writer's word on the reconciler's decision and the ask's place in
    the queue (docs/plans/ask-reconciliation.md): `confirm` or `amend`
    writes `shape.confirmed`, the writer's label on `reconcile.decided`, and
    an amend's text also steers the task (`message.steered`); `before` or
    `after` writes `task.placed` beside another open task. Returns the kinds
    written. Refused (LookupError, nothing written): a task that is not an
    open SDLC task, a label with no decision to label, and an anchor that is
    the task itself or not open."""
    amend = amend.strip() if amend is not None else None
    if (confirm and amend is not None) or (before and after):
        raise ValueError("one of confirm and amend, and one of before and after")
    if amend == "":
        raise ValueError("an amend has text")
    if not (confirm or amend or before or after):
        raise ValueError("nothing to record: confirm, amend, before, or after")
    anchor = before or after
    written: list[str] = []
    provenance = ledger.provenance(by, via, role_played)
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        rows = await ledger.read(conn, task_id)
        if not rows:
            raise LookupError(f"no task {task_id}")
        f = machine.fold(rows)
        if f.legacy or f.calibration or f.state in tasks.TERMINAL:
            raise LookupError(f"task {task_id} is not open (it is {f.state})")
        if anchor is not None and (
            anchor == task_id or anchor not in {t for t, _, _ in await tasks.open_ids(conn)}
        ):
            raise LookupError(f"task {anchor} is not another open task")
        if confirm or amend:
            decided = next((r for r in reversed(rows) if r["type"] == "reconcile.decided"), None)
            if decided is None:
                raise LookupError(f"task {task_id} has no reconciler decision to confirm or amend")
            d = decided["payload"]
            await ledger.append(
                conn,
                task_id,
                "shape.confirmed",
                {
                    "judgement_id": d.get("judgement_id"),
                    "decision": d.get("decision"),
                    "named": d.get("named"),
                    "confirmed": bool(confirm),
                    "amend": amend,
                    "provenance": provenance,
                },
            )
            written.append("shape.confirmed")
            if amend:
                await ledger.append(
                    conn, task_id, "message.steered", {"text": amend, "provenance": provenance}
                )
                written.append("message.steered")
        if anchor is not None:
            where = "before" if before else "after"
            await ledger.append(
                conn, task_id, "task.placed", {"anchor": anchor, "where": where, "provenance": provenance}
            )
            written.append("task.placed")
    return written


async def feedback(
    conn,
    task_id: str,
    text: str,
    *,
    by: str = "tom",
    via: str = "the command line",
    role_played: bool = False,
    effect_key: str | None = None,
) -> str:
    """Record feedback on the task's delivery, in `merge`, `merged`, or
    `ended`, which sends the work to `patch` and opens a new loop window;
    `effect_key` names the chat session's `task.steer` that gave it. Refused
    while the merge's intent has no outcome, and on a task stopped or under
    a stopped ancestor, under the tree's lock so a stop's walk sees the
    reopened task or the feedback sees the stop. Returns the feedback's
    id."""
    text = text.strip()
    if not text:
        raise ValueError("feedback has text")
    async with conn.transaction():
        await tasks.lock_tree(conn, task_id)
        await ledger.lock(conn, f"task:{task_id}")
        f = machine.fold(await ledger.read(conn, task_id))
        if f.legacy:
            raise LookupError(f"task {task_id} predates the state machine")
        if f.calibration:
            raise LookupError(f"task {task_id} is a calibration task")
        if f.state is State.STOPPED:
            raise LookupError(f"task {task_id} is stopped; a stopped task takes no feedback")
        fence = await tasks.fenced_by(conn, task_id)
        if fence is not None:
            raise LookupError(
                f"task {task_id} is under stopped task {fence}; a stopped task takes no feedback"
            )
        if f.state is State.WAITING:
            raise LookupError(f"task {task_id} has an open question; answer it with `answer`")
        if f.state not in (State.MERGE, State.MERGED, State.ENDED):
            raise LookupError(f"task {task_id} is in {f.state}; feedback is on a delivery")
        if f.merge_effect and f.merge_effect["state"] == "in_flight":
            raise LookupError(f"task {task_id}'s merge is in flight; wait for its outcome")
        feedback_id = ledger.new_id()
        await ledger.append(
            conn,
            task_id,
            "feedback.given",
            {
                "feedback_id": feedback_id,
                "on_delivery": (f.delivery or {}).get("summary"),
                "candidate": {"sha": f.candidate.sha, "turn_id": f.candidate.turn_id}
                if f.candidate
                else None,
                "text": text,
                "provenance": ledger.provenance(by, via, role_played),
                **({"effect_key": effect_key} if effect_key else {}),
            },
        )
    return feedback_id


async def open_question(conn, task_id: str) -> dict[str, Any] | None:
    state = await tasks.status(conn, task_id)
    unanswered = [q for q in state["attention"] if q["kind"] == "question" and q["answer"] is None]
    return unanswered[-1] if unanswered else None


def _entry_prompt(entry: dict[str, Any] | None, rows: list[dict]) -> str | None:
    if not entry:
        return None
    kind, p = entry["type"], entry["payload"]
    if kind == "question.answered":
        return f"# Tom's answer\n\n{p['text']}"
    if kind == "feedback.given":
        who = p.get("provenance") or {}
        said = f"from {who.get('by', 'tom')}, via {who.get('via', 'the command line')}"
        return f"# Feedback on the delivery {said}\n\n{p['text']}"
    if kind == "critique.decided":
        lines = [f"- [{x.get('kind', 'finding')}] {x.get('text', '')}" for x in p.get("findings") or []]
        return f"# Critique: {p['verdict']}\n\n" + ("\n".join(lines) or "No findings.")
    if kind == "join":
        found = machine.fold(rows).join
        lines = [f"- [{x.source}, {x.kind}] {x.text}" for x in (found.findings if found else ())]
        head = "# Findings from the checks\n\n"
        if found is not None and found.row == 2:
            head = (
                "# The checks passed; Tom wrote while you built\n\n"
                "What he wrote is below and came after your last turn began. Act on it, then write `done.md`.\n\n"
            )
        elif found is not None and found.spends == "last":
            head = (
                "# Findings from the checks, with the plan's rounds spent\n\n"
                "This is the last round: patch once more (`done.md`), or stop (`stop.md`).\n\n"
            )
        return head + ("\n".join(lines) or "No findings were written.")
    if kind == "rebase.requested":
        tip = f" to {p['tip']}" if p.get("tip") else ""
        return (
            f"# The target branch moved\n\nThe merge into {p.get('target_branch')} was refused: "
            f"{p.get('target_branch')} moved on{tip} since this work was based on it. Rebase the work "
            f"onto it (git fetch origin {p.get('target_branch')}, then git rebase "
            f"origin/{p.get('target_branch')}), resolve any conflicts, run the tests, commit, and write "
            f"done.md.\n\nWhy the kernel could not carry it across: {p.get('not_carried') or 'unknown'}"
            f"\n\nWhat git said: {p.get('reason') or ''}".strip()
        )
    if kind == "turn.collected" and p.get("verdict") == "no_material_question":
        return f"# No material question; on to the plan\n\n{p.get('no_question') or ''}".strip()
    return None


async def next_prompt(conn, task_id: str, now: datetime | None = None) -> tuple[str, str | None, int]:
    """The next turn's prompt, the harness session it resumes, and the last
    row it read (`turn.started` `read_upto`): a steer after it is owed to
    the turn mid-turn (`mid_turn`) or to the next. A chat session's wakes
    owed at `now` (`owed`) are in it."""
    rows = await ledger.read(conn, task_id)
    read_upto = max((int(r["id"]) for r in rows), default=0)
    f = machine.fold(rows)
    b = await tasks.brief(conn, task_id)
    if b.chat:
        first = b.instruction if f.session is None else None
        places = await _send_places(conn, rows, b.chat, f.last_collected)
        report = _effects_report(f.last_collected, (await tasks.status(conn, task_id))["effects"], places)
        woken = _wake_report(owed(rows, now or datetime.now(UTC)))
        parts = (first, chat_prompt(rows), woken, _failure_report(rows), _advice_report(rows),
                 _errors_report(f.last_collected), report, context_report(rows, b.model))  # fmt: skip
        return "\n\n".join(x for x in parts if x) or "Continue.", f.session, read_upto
    if f.session is None:
        prompt = b.instruction
    else:
        prompt = (None if f.entry_finished else _entry_prompt(f.entry, rows)) or "Continue."
    advice = _advice_report(rows)
    steering = _steering(f.steering, reactions.shown(rows))
    notes = _errors_report(f.last_collected)
    failed = _failure_report(rows)
    report = _effects_report(f.last_collected, (await tasks.status(conn, task_id))["effects"])
    children = _children_report(await tasks.reports(conn, task_id))
    parts = (prompt, failed, advice, steering, notes, report, children)
    return "\n\n".join(x for x in parts if x), f.session, read_upto


def _working_turn_finished(rows: list[dict], turn_states: dict[str, str]) -> bool:
    """Whether a working-session turn ended done and without an error in
    `rows` (a fresh session's turn, the advisor's included, is no working
    turn)."""
    for r in rows:
        if r["type"] != "turn.ended":
            continue
        p = r["payload"]
        result = p.get("result") if isinstance(p.get("result"), dict) else {}
        state = turn_states.get(str(p.get("turn_id")))
        if (
            p.get("outcome") == "done"
            and not result.get("is_error")
            and state not in (None, machine.FRESH, machine.COMPACTION)
        ):
            return True
    return False


def pending_advice(rows: list[dict]) -> dict[str, Any] | None:
    """The `turn.collected` payload whose question the advisor has yet to
    answer, or None. Only the latest collected turn can name one: its
    verdict is `idle`, it carries `advice`, it ran in the task's current
    state, no `advice.given` names it, and no working turn has finished
    after it. So a run ended between the question and the answer asks it
    on its next run, and an older question is never asked."""
    f = machine.fold(rows)
    last = next((i for i in range(len(rows) - 1, -1, -1) if rows[i]["type"] == "turn.collected"), None)
    if last is None:
        return None
    p = rows[last]["payload"]
    if p.get("verdict") != "idle" or not p.get("advice") or p.get("state") != f.state.value:
        return None
    after = rows[last + 1 :]
    if any(r["type"] == ADVICE_GIVEN and r["payload"].get("asked_turn_id") == p["turn_id"] for r in after):
        return None
    return None if _working_turn_finished(after, f.turn_states) else p


def _advice_report(rows: list[dict]) -> str:
    """The advisor's latest answer, or why it gave none, until a working
    turn finishes after it: its words quoted as prompt data under a label,
    never an instruction."""
    i = next((i for i in range(len(rows) - 1, -1, -1) if rows[i]["type"] == ADVICE_GIVEN), None)
    if i is None or _working_turn_finished(rows[i + 1 :], machine.fold(rows).turn_states):
        return ""
    p = rows[i]["payload"]
    if p.get("answer") is not None:
        head = (
            f"# The advisor's answer\n\nFrom {p.get('seat')} ({p.get('model')}), "
            f"reading your commit {str(p.get('head') or '')[:12]}:"
        )
        return "\n".join([head, *tasks.quoted(p["answer"])])
    return "\n".join(["# The advisor did not answer", "", *tasks.quoted(p.get("error"))])


def _children_report(found: list[dict[str, Any]]) -> str:
    """Each child's latest delivery, in start order: the child's own words,
    as prompt data under a label, never in the Brief. Every prompt carries
    them, so a report is never lost to a turn that failed or was stopped."""
    lines = []
    for c in found:
        if c["delivery"] is not None:
            lines.append(f"- {c['task_id']} (delivered, {c['delivery']['outcome']}):")
            lines += tasks.quoted(c["delivery"]["summary"])
    return "\n".join(["# Reports from your children", "", *lines]) if lines else ""


def _steering(steered: list[dict[str, Any]], shown: dict[str, str]) -> str:
    """What Tom wrote while the last working turn ran (or since it
    finished), for the next working turn, each with the reaction on it
    (`reactions.shown`)."""
    if not steered:
        return ""
    by = {(p.get("provenance") or {}).get("by") or "tom" for p in steered}
    lines = ["Tom wrote while you worked:" if by == {"tom"} else "Written to you while you worked:"]
    for p in steered:
        paths = [a["path"] for a in p.get("attachments") or [] if a.get("path")]
        writer = (p.get("provenance") or {}).get("by") or "tom"
        text = p.get("text", "") if by == {"tom"} else f"{writer}: {p.get('text', '')}"
        mark = shown.get(p.get("received_id"))
        lines.append(
            f"- {text}"
            + (f" (attachments: {', '.join(paths)})" if paths else "")
            + (f" ({mark})" if mark else "")
        )
    return "\n".join(lines)


def _failure_report(rows: list[dict]) -> str:
    """Why the last try failed, when a step failed after the last turn the
    kernel collected: the kernel retries a failed step itself
    (`router.retry_due`), and the next turn opens with the reason."""
    for r in reversed(rows):
        if r["type"] == "turn.collected":
            return ""
        if r["type"] == "step.failed":
            return "# The last try failed\n\n" + str(r["payload"].get("reason") or "no reason was recorded")
    return ""


def _errors_report(collected: dict[str, Any] | None) -> str:
    if not collected or not collected.get("errors"):
        return ""
    return "What did not count from your last turn:\n" + "\n".join(f"- {e}" for e in collected["errors"])


async def _send_places(
    conn, rows: list[dict], chat: dict[str, Any], collected: dict[str, Any] | None
) -> dict[str, str]:
    """Where each of a chat session's last-turn Slack or Telegram sends
    went, by request file, in the tags its chat lines carry
    (`intake.thread_tag`): `into #tag` for a reply, the tag of the message
    it answers, or of that message's thread's first message
    (`intake.root_of`), as this chat's rows have them, else `into the
    thread of <first message>`; `into #topic-<id>` for a Telegram topic;
    `top-level, starts #tag` for a post, slugged from its text. A send to
    another chat, or an entry without its request, has none."""
    from core import channels, intake

    tags = {
        r["payload"].get("message_id"): r["payload"].get("thread_tag") for r in rows if r["type"] in CHAT_ROWS
    }
    places = {}
    for e in (collected or {}).get("effects") or []:
        r = e.get("request") or {}
        channel = chat.get("channel")
        if channel not in ("slack", "telegram") or r.get("action_type") != f"{channel}.send_message":
            continue
        normal = channels.get(channel).normal
        if normal(str(r.get("target"))) != normal(str(chat.get("chat_id"))):
            continue
        p = r.get("payload") or {}
        if not p.get("reply_to"):
            tag = intake.slug(str(p.get("text") or ""))
            places[e["file"]] = (
                f"into #topic-{p['topic_id']}" if p.get("topic_id")
                else f"top-level, starts #{tag}" if tag else "top-level"
            )  # fmt: skip
            continue
        reply_to = str(p["reply_to"])  # a Telegram id may be written as a number
        where = tags.get(reply_to)
        root = None
        if not where:
            root = await intake.root_of(conn, channel, chat["chat_id"], reply_to)
            where = tags.get(root)
        tag = re.search(r"#[a-z0-9-]+", where or "")
        places[e["file"]] = f"into {tag[0]}" if tag else f"into the thread of {root or reply_to}"
    return places


def _effects_report(
    collected: dict[str, Any] | None, now: dict[str, str], places: dict[str, str] | None = None
) -> str:
    """What became of the last turn's effect requests, as the ledger has
    them now (a send released to its bridge reads as done once it went), each named
    by its request file and effect id, and a chat send by where it went
    (`_send_places`): the request itself is the turn's and is not repeated
    into its next prompt."""
    if not collected or not collected.get("effects"):
        return ""
    lines = ["What became of the effects you requested last turn:"]
    for e in collected["effects"]:
        if "error" in e and "effect_id" not in e:
            lines.append(f"- {e['file']}: {e['error']}")
            continue
        kind = now.get(e["effect_id"], e["kind"])
        said = {
            "released": "released to its bridge",
            "done": "done",
            "refused": f"refused: {e['error']}",
            "failed": f"failed: {e['error']}",
        }.get(kind, kind)
        place = (places or {}).get(e["file"])
        lines.append(f"- {e['file']} (effect {e['effect_id']}): {said}" + (f", {place}" if place else ""))
    return "\n".join(lines)
