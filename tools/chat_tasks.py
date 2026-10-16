"""A chat session's hands on work: `task.start`, `task.answer`, `task.stop`.

A chat session (`core/intake.py`) does no work itself. It starts a task
for the work, as a child of itself, with the request as written and who
asked; answers a task's open question with what the chat said; and stops
a task. Its tasks report to it (`core/notices.py`), never to a chat. Each
is `propose`: the work a task does is bounded by its own ceiling, its
parent's, and a stop undoes a start.

The performers run in the kernel's process with a connection of their
own; each refuses a task that is not a child of its session. `lookup`
reads the ledger, which is how a dangling intent is reconciled: a start
by its idempotency key on the child's `task.started`, an answer by the
question it answered, a stop by the child's stop.
"""

from core import db, ledger, machine, session, tasks
from core.machine import State
from core.settings import resolve_seat


class _ChatTask:
    effect_class = "propose"

    def __init__(self, brief: tasks.Brief, dsn: str | None = None):
        self.session = brief.id
        self.project = brief.project
        self.dsn = dsn

    async def _child(self, conn, task_id: str) -> tasks.Brief:
        try:
            b = await tasks.brief(conn, task_id)
        except KeyError:
            raise ValueError(f"no task {task_id}") from None
        if b.parent_id != self.session:
            raise ValueError(f"task {task_id} is not one this chat started")
        return b

    async def refuse(self, conn, action) -> str | None:
        if self.action_type == "task.start":
            return (
                None if str(action.payload.get("instruction") or "").strip() else "the request has no words"
            )
        try:
            await self._child(conn, action.target)
        except ValueError as exc:
            return str(exc)
        return None


class TaskStart(_ChatTask):
    action_type = "task.start"
    usage = (
        '`task.start`: target "work", payload `{"instruction": "<the request as written, with what the chat '
        'said it needs>", "by": "<who asked>"}`; starts a task on this project that does the work and '
        "reports to you. Your next prompt names it."
    )

    async def perform(self, action, key: str) -> dict:
        seat, model = resolve_seat("frontier")
        async with await db.connect(self.dsn) as conn:
            child = await tasks.start_child(
                conn,
                self.session,
                marker={"sdlc": 1, "effect_key": key},
                by=str(action.payload.get("by") or "valor"),
                via="chat",
                instruction=str(action.payload["instruction"]).strip(),
                model=model,
                harness_name=seat,
                project=self.project,
            )
        return {"task_id": child}

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
    usage = (
        '`task.answer`: target the task id, payload `{"text": "...", "by": "<who answered>"}`; '
        "answers that task's open question."
    )

    async def perform(self, action, key: str) -> dict:
        async with await db.connect(self.dsn) as conn:
            await self._child(conn, action.target)
            question = await session.answer(
                conn, action.target, str(action.payload.get("text") or ""),
                by=str(action.payload.get("by") or "valor"), via="chat",
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


class TaskStop(_ChatTask):
    action_type = "task.stop"
    usage = '`task.stop`: target the task id, payload `{"why": "...", "by": "<who asked>"}`; stops that task.'

    async def perform(self, action, key: str) -> dict:
        async with await db.connect(self.dsn) as conn:
            await self._child(conn, action.target)
            await tasks.stop(
                conn, action.target, reason=str(action.payload.get("why") or "stopped from the chat"),
                by=str(action.payload.get("by") or "valor"), via="chat",
            )  # fmt: skip
        return {"task_id": action.target}

    async def lookup(self, action, key: str) -> dict | None:
        async with await db.connect(self.dsn) as conn:
            f = machine.fold(await ledger.read(conn, action.target))
        return {"task_id": action.target} if f.state is State.STOPPED else None


def performers(brief: tasks.Brief, dsn: str | None = None) -> list:
    """A chat session's: none for any other task."""
    return [cls(brief, dsn) for cls in (TaskStart, TaskAnswer, TaskStop)] if brief.chat else []
