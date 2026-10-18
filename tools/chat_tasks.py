"""A chat session's hands on work: `task.start`, `task.answer`,
`task.steer`, `task.stop`.

A chat session (`core/intake.py`) does no work itself. It starts a task
for the work, as a child of itself, with the request as written and who
asked; answers a task's open question with what the chat said; steers a
task with what the chat added; and stops a task. A steer of a task that has
delivered (`merged` or `ended`) is feedback (`session.feedback`), as a
direct reply to its report is (`core/intake.py`): the task reopens in
`patch`, in its own workspace, resuming its own harness session, and
reports again, so each deliverable's task is the worker for its revision
rounds. Its tasks report to it
(`core/notices.py`), never to a chat, and so does every root task that is
no session when the session is the operator chat's. Each is `propose`:
the work a task does is bounded by its own ceiling, its parent's, and a
stop undoes a start.

The performers run in the kernel's process with a connection of their
own; each refuses a task that does not report to its session
(`tasks.reporting`: its children, and the tasks whose report is on it).
`lookup` reads the ledger, which is how a dangling intent is reconciled:
a start by its idempotency key on the child's `task.started`, an answer
by the question it answered, a steer by its key on the `message.steered`
or `feedback.given` row, a stop by the task's stop.
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
        if task_id not in await tasks.reporting(conn, self.session):
            raise ValueError(f"task {task_id} does not report to this chat")
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


class TaskSteer(_ChatTask):
    action_type = "task.steer"
    usage = (
        '`task.steer`: target the task id, payload `{"text": "...", "by": "<who said it>"}`; '
        "gives that task what the chat added: after its current step when it is working, else at its next "
        "turn. To a task that has delivered "
        "(merged or ended) it is feedback: the task reopens in its own workspace and conversation, "
        "revises, and reports to you again. A stopped task stays stopped."
    )

    async def perform(self, action, key: str) -> dict:
        async with await db.connect(self.dsn) as conn, conn.transaction():
            await self._child(conn, action.target)
            await tasks.lock_tree(conn, action.target)
            await ledger.lock(conn, f"task:{action.target}")
            by, text = str(action.payload.get("by") or "valor"), str(action.payload.get("text") or "")
            if machine.fold(await ledger.read(conn, action.target)).state in (State.MERGED, State.ENDED):
                feedback = await session.feedback(
                    conn, action.target, text, by=by, via="chat", effect_key=key
                )
                return {"task_id": action.target, "feedback_id": feedback}
            await ledger.append(conn, action.target, "message.steered", {
                "text": text, "effect_key": key, "provenance": ledger.provenance(by, "chat", False),
            })  # fmt: skip
        return {"task_id": action.target}

    async def lookup(self, action, key: str) -> dict | None:
        async with await db.connect(self.dsn) as conn:
            rows = await ledger.read(conn, action.target)
        for r in rows:
            if r["type"] in ("message.steered", "feedback.given") and r["payload"].get("effect_key") == key:
                found = r["payload"].get("feedback_id")
                return {"task_id": action.target, **({"feedback_id": found} if found else {})}
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
    return [cls(brief, dsn) for cls in (TaskStart, TaskAnswer, TaskSteer, TaskStop)] if brief.chat else []
