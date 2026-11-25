"""A chat session's hands on work: `task.start`, `task.answer`,
`task.steer`, `task.stop`, which the session asks for in its turn with
`valor-record act` (the gateway's `act` route), reading the outcome before
it writes the reply that reports it; they offer no effect-file usage.

A chat session (`core/intake.py`) does no work itself. It starts a task
for the work, as a child of itself, with the request as written and who
asked and a few lines of context it writes (where the work came from, what
its room allows and forbids), under its own project or the one it names (its Brief's `Repos:`
line lists its spec's `projects`, the repository the request is about), carrying the session's chat so the
child reads it (`project.chat`), or with `in` another chat of its project
as that chat's session's child, reading that chat (a product room
approving a build in its sibling dev chat, `skills/sdlc/roles/product.md`); answers a task's open question with what the chat said; steers a
task with what the chat added, or hands a message of its record to another
chat's session, any project's on any Mac (`in` and `from`, `TaskSteer.hand_on`:
who said it, where and when, verbatim, recorded on the Mac owning the chat,
over ssh, `core/remote.py`, when that is another; nothing is posted); and stops a task. A steer of a task waiting
on its question answers it, since a waiting task reads nothing else. A steer of a task that has
delivered (`merged` or `ended`) is feedback (`session.feedback`): the task reopens in
`patch`, in its own workspace, resuming its own harness session, and
reports again, so each deliverable's task is the worker for its revision
rounds. Its tasks report to it
(`core/notices.py`), never to a chat, and so does every root task that is
no session when the session is the operator chat's, and any task under
whose post Tom replied in its chat (`tasks.reporting`). Each is `propose`:
the work a task does is bounded by its own ceiling, its parent's, and a
stop undoes a start.

The performers run in the kernel's process with a connection of their
own; each refuses a task that does not report to its session
(`tasks.reporting`: its children, and the tasks whose report is on it),
but a project's board chat session answers any of the project's tasks
waiting on a question (`board.answers`), since every decision for Tom on
that project is on its board, however he answers it; it stops any of
them (`board.covers`), which is how a stale one is closed; and the chat
session a call reported to answers a question Tom settled in it (`call.settles`).
A call's session (`core/call.py`) takes a steer while the call is open,
posted in the call's chat, and refuses one once the call has ended.
`lookup` reads the ledger, which is how a dangling intent is reconciled:
a start by its idempotency key on the child's `task.started`, an answer
by the question it answered, a steer by its key on the `message.steered`,
`feedback.given` or `question.answered` row, a stop by the task's stop.
"""

import json
from dataclasses import asdict

from core import (
    board,
    broker,
    call,
    channels,
    db,
    intake,
    ledger,
    machine,
    record,
    remote,
    session,
    tasks,
    workspace,
)
from core.machine import State
from core.settings import EFFORT, resolve_seat, settings


class _ChatTask:
    effect_class = "propose"

    def __init__(self, brief: tasks.Brief, dsn: str | None = None):
        self.session = brief.id
        self.project = brief.project
        self.chat = f"{brief.chat['channel']}:{brief.chat['chat_id']}" if brief.chat else None
        self.dsn = dsn

    async def _child(self, conn, task_id: str) -> tasks.Brief:
        try:
            b = await tasks.brief(conn, task_id)
        except KeyError:
            raise ValueError(f"no task {task_id}") from None
        if task_id not in await tasks.reporting(conn, self.session):
            raise ValueError(f"task {task_id} does not report to this chat")
        return b

    async def refuse(self, conn, action) -> str | None:
        if self.action_type == "task.start":
            return (
                None if str(action.payload.get("instruction") or "").strip() else "the request has no words"
            )
        if self.action_type == "task.steer" and action.payload.get("in"):
            return None  # a hand-on into a chat (`TaskSteer.hand_on`), no task of this session's
        try:
            child = await self._child(conn, action.target)
        except ValueError as exc:
            return str(exc)
        if self.action_type == "task.steer" and call.is_call(child) and await _ended(conn, action.target):
            return "the call has ended"
        return None


class TaskStart(_ChatTask):
    action_type = "task.start"

    async def perform(self, action, key: str) -> dict:
        seat, model = resolve_seat("frontier")
        async with await db.connect(self.dsn) as conn, conn.transaction():
            parent, chat = self.session, self.chat
            if action.payload.get("in"):
                parent, chat = await self._sibling(conn, str(action.payload["in"]))
            child = await tasks.start_child(
                conn,
                parent,
                marker={"sdlc": 1, "effect_key": key},
                by=str(action.payload.get("by") or "valor"),
                via="chat",
                instruction=str(action.payload["instruction"]).strip(),
                context=str(action.payload.get("context") or "").strip() or None,
                model=model,
                harness_name=seat,
                effort=EFFORT.get("frontier"),
                project=self._child_project(action.payload.get("project"), chat),
            )
        return {"task_id": child}

    async def _sibling(self, conn, into: str) -> tuple[str, str]:
        """The session of `into`, another chat of this session's project
        (so one this Mac owns), opened under the chat's lock and started in
        this transaction when it has none, as `intake.operator_session`
        does; and the chat as a child's `project.chat` names it."""
        channel, _, ident = into.partition(":")
        row = channels.get(channel)
        found = f"{channel}:{row.normal(ident)}" if row is not None and ident else into
        spec = workspace.Spec.load(self.project["name"]) if self.project else None
        if spec is None or found not in spec.chats:
            raise ValueError(f"{into} is not one of this project's chats")
        chat_id = found.partition(":")[2]
        own = intake._toms_own({"channel": channel, "chat_id": chat_id})
        session_id, brief = await intake.open_session(conn, channel, chat_id, spec, own=own)
        if brief is not None:
            await tasks.start(conn, brief, by="valor", via="kernel")
        return session_id, found

    def _child_project(self, name, chat: str | None) -> dict | None:
        """The child's project: the named one, else the session's own, with
        the chat it was started in, which the child reads (`_reading_env`)."""
        if self.project is None and name is None:
            return None
        found = {"name": name or self.project["name"]}
        return {**found, "chat": chat} if chat else found

    async def lookup(self, action, key: str) -> dict | None:
        async with await db.connect(self.dsn) as conn:
            row = await (
                await conn.execute(
                    "SELECT task_id FROM events WHERE type = 'task.started' AND payload->>'effect_key' = %s",
                    (key,),
                )
            ).fetchone()
        return {"task_id": row[0]} if row else None


class TaskAnswer(_ChatTask):
    action_type = "task.answer"

    async def _child(self, conn, task_id: str) -> tasks.Brief:
        try:
            return await super()._child(conn, task_id)
        except ValueError:
            if not await board.answers(conn, self.session, task_id) and not await call.settles(
                conn, self.session, task_id
            ):
                raise
            return await tasks.brief(conn, task_id)

    async def perform(self, action, key: str) -> dict:
        async with await db.connect(self.dsn) as conn:
            await self._child(conn, action.target)
            question = await session.answer(
                conn, action.target, str(action.payload.get("text") or ""),
                by=str(action.payload.get("by") or "valor"), via=str(action.payload.get("via") or "chat"),
                question_id=action.payload.get("question_id") or None,
            )  # fmt: skip
        return {"task_id": action.target, "question_id": question}

    async def lookup(self, action, key: str) -> dict | None:
        async with await db.connect(self.dsn) as conn:
            rows = await ledger.read(conn, action.target)
        text = str(action.payload.get("text") or "").strip()
        for r in rows:
            if r["type"] == "question.answered" and r["payload"].get("text") == text:
                return {"task_id": action.target, "question_id": r["payload"].get("question_id")}
        return None


class TaskSteer(_ChatTask):
    action_type = "task.steer"

    async def perform(self, action, key: str) -> dict:
        if action.payload.get("in"):
            return await self.hand_on(action, key)
        async with await db.connect(self.dsn) as conn, conn.transaction():
            await self._child(conn, action.target)
            await tasks.lock_tree(conn, action.target)
            await ledger.lock(conn, f"task:{action.target}")
            by, text = str(action.payload.get("by") or "valor"), str(action.payload.get("text") or "")
            state = machine.fold(await ledger.read(conn, action.target)).state
            if state is State.WAITING:
                question = await session.answer(conn, action.target, text, by=by, via="chat", effect_key=key)
                return {"task_id": action.target, "question_id": question}
            if state in (State.MERGED, State.ENDED):
                feedback = await session.feedback(
                    conn, action.target, text, by=by, via="chat", effect_key=key
                )
                return {"task_id": action.target, "feedback_id": feedback}
            await ledger.append(conn, action.target, "message.steered", {
                "text": text, "effect_key": key, "provenance": ledger.provenance(by, "chat", False),
            })  # fmt: skip
        return {"task_id": action.target}

    async def hand_on(self, action, key: str) -> dict:
        """The record `from`, a message in this project's reach, handed to
        the session of chat `in` as said by whoever said it, where and when
        (Tom, 2026-11-24): recorded in the ledger of the Mac owning the chat,
        whose kernel binds it (`intake._handed_on`). Nothing is posted. The
        record is read and closed before the other Mac is reached."""
        into, inbound = await self._handed(action, key)
        owner = intake._spec_machine(into)
        if owner == settings.machine:
            async with await db.connect(self.dsn) as conn:
                got = await intake.receive(conn, intake.Inbound(**inbound))
            return {"chat": action.payload["in"], "machine": owner, "received_id": got.received_id}
        status, out = await remote.ssh(
            remote.host(owner), f"{remote.KERNEL} receive", json.dumps(inbound).encode()
        )
        if status == remote.NO_ROUTE:
            raise broker.Failed(f"no route from {settings.machine} to {owner}: {out.strip()}")
        return {"chat": action.payload["in"], "machine": owner, "received_id": _received(owner, status, out)}

    async def _handed(self, action, key: str) -> tuple[workspace.Spec, dict]:
        """The spec owning chat `in`, and the `intake.Inbound` fields of the
        record `from` handed into it: who said it, when, and its words as
        received; the thread, reply and posts of its own chat left out, its
        files named and kept where they are. A record handed on before keeps
        its first chat and who said it there."""
        into = str(action.payload["in"])
        channel, _, ident = into.partition(":")
        row = channels.get(channel)
        chat_id = row.normal(ident) if row is not None else ident
        taken = intake._taking(channel, chat_id, anywhere=True)
        if taken is None:
            raise broker.Failed(f"no project on any Mac takes {into}")
        frm = str(action.payload.get("from") or "").lstrip("#")
        if not frm:
            raise broker.Failed("a hand-on names the message it hands on: --from RECORD")
        async with await db.connect(self.dsn) as conn:
            spec = await record.spec_of(conn, self.session)
            found = await record.messages(conn, spec, await record.reach(conn, spec), {"record": frm})
        if not found:
            raise broker.Failed(f"no record #{frm} in project {spec.name}'s messages")
        m = found[0]["message"]
        first = m.get("handed_on") or {
            "chat": found[0]["chat"], "channel": m["channel"], "message_id": m["message_id"],
            "verified": bool(m.get("verified")),
            "by": "tom" if intake._from_operator(m) else (m.get("sender_name") or m["sender_id"]),
        }  # fmt: skip
        keep = ("chat", "channel", "message_id", "verified", "by")
        handed_on = {
            **{k: first.get(k) for k in keep},
            "project": spec.name, "session": self.session, "machine": settings.machine,
            "asked_by": action.payload.get("by"), "note": str(action.payload.get("text") or "").strip() or None,
        }  # fmt: skip
        files = [
            {**{k: a[k] for k in ("name", "mime", "bytes", "transcript", "transcript_failed") if k in a},
             "kept_on": a.get("kept_on") or settings.machine}
            for a in m.get("attachments") or []
        ]  # fmt: skip
        inbound = intake.Inbound(
            channel=channel, chat_id=chat_id, chat_kind="group" if channels.group(channel, chat_id) else "dm",
            message_id=f"handon:{key}", sender_id=m["sender_id"], sender_name=m.get("sender_name") or "",
            sent_at=m["sent_at"], kind=m.get("kind") or "message", text=m.get("text") or "",
            attachments=files, handed_on=handed_on,
        )  # fmt: skip
        return taken[1], asdict(inbound)

    async def lookup(self, action, key: str) -> dict | None:
        if action.payload.get("in"):
            return await self._handed_lookup(action, key)
        async with await db.connect(self.dsn) as conn:
            rows = await ledger.read(conn, action.target)
        for r in rows:
            if r["type"] in ("message.steered", "feedback.given", "question.answered") and (
                r["payload"].get("effect_key") == key
            ):
                found = {k: r["payload"][k] for k in ("feedback_id", "question_id") if r["payload"].get(k)}
                return {"task_id": action.target, **found}
        return None

    async def _handed_lookup(self, action, key: str) -> dict | None:
        into, inbound = await self._handed(action, key)
        owner = intake._spec_machine(into)
        named = {k: inbound[k] for k in ("channel", "chat_id", "message_id")}
        if owner == settings.machine:
            async with await db.connect(self.dsn) as conn:
                got = await intake.handed(conn, **named)
        else:
            status, out = await remote.ssh(
                remote.host(owner), f"{remote.KERNEL} receive --lookup", json.dumps(named).encode()
            )
            if status == remote.NO_ROUTE:
                raise broker.Unknown(f"no route from {settings.machine} to {owner}: {out.strip()}")
            got = _received(owner, status, out)
        return {"chat": action.payload["in"], "machine": owner, "received_id": got} if got else None


def _received(owner: str, status: int, out: str) -> str | None:
    """The received id `core receive` printed on `owner`; its own error
    when it failed (a Mac whose update has not reached this code names
    the command)."""
    lines = [line for line in out.splitlines() if line.startswith("{")]
    if status != 0 or not lines:
        raise RuntimeError(f"{owner} did not record it: {out.strip()[-300:]}")
    return json.loads(lines[-1])["received_id"]


class TaskStop(_ChatTask):
    action_type = "task.stop"

    async def _child(self, conn, task_id: str) -> tasks.Brief:
        try:
            return await super()._child(conn, task_id)
        except ValueError:
            if not await board.covers(conn, self.session, task_id):
                raise
            return await tasks.brief(conn, task_id)

    async def perform(self, action, key: str) -> dict:
        async with await db.connect(self.dsn) as conn:
            await self._child(conn, action.target)
            await tasks.stop(
                conn, action.target, reason=str(action.payload.get("why") or "stopped from the chat"),
                by=str(action.payload.get("by") or "valor"), via=str(action.payload.get("via") or "chat"),
            )  # fmt: skip
        return {"task_id": action.target}

    async def lookup(self, action, key: str) -> dict | None:
        async with await db.connect(self.dsn) as conn:
            f = machine.fold(await ledger.read(conn, action.target))
        return {"task_id": action.target} if f.state is State.STOPPED else None


async def _ended(conn, task_id: str) -> bool:
    row = await (
        await conn.execute("SELECT 1 FROM events WHERE task_id = %s AND type = 'call.ended'", (task_id,))
    ).fetchone()
    return row is not None


def performers(brief: tasks.Brief, dsn: str | None = None) -> list:
    """A chat session's: none for any other task."""
    return [cls(brief, dsn) for cls in (TaskStart, TaskAnswer, TaskSteer, TaskStop)] if brief.chat else []
