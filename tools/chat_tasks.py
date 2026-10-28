"""A chat session's hands on work: `task.start`, `task.answer`,
`task.steer`, `task.stop`.

A chat session (`core/intake.py`) does no work itself. It starts a task
for the work, as a child of itself, with the request as written and who
asked, under its own project or one its spec lists in `projects` (the
repository the request is about), carrying the session's chat so the
child reads it (`project.chat`); answers a task's open question with what the chat said; steers a
task with what the chat added; and stops a task. A steer of a task waiting
on its question answers it, since a waiting task reads nothing else. A steer of a task that has
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
(`tasks.reporting`: its children, and the tasks whose report is on it),
but a project's board chat session answers any of the project's tasks
waiting on a question (`board.answers`), since every decision for Tom on
that project is on its board, however he answers it.
`lookup` reads the ledger, which is how a dangling intent is reconciled:
a start by its idempotency key on the child's `task.started`, an answer
by the question it answered, a steer by its key on the `message.steered`,
`feedback.given` or `question.answered` row, a stop by the task's stop.
"""

from core import board, db, ledger, machine, session, tasks, workspace
from core.machine import State
from core.settings import resolve_seat


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
            if not str(action.payload.get("instruction") or "").strip():
                return "the request has no words"
            return self._project_refused(action.payload.get("project"))
        try:
            await self._child(conn, action.target)
        except ValueError as exc:
            return str(exc)
        return None


class TaskStart(_ChatTask):
    action_type = "task.start"
    usage = (
        '`task.start`: target "work", payload `{"instruction": "<the request as written, with what the chat '
        'said it needs>", "by": "<who asked>", "project": null}`; starts a task that does the work and '
        "reports to you, under this project, or under one of the projects your Repos line names when "
        '`"project"` names it. Your next prompt names it.'
    )

    def _own(self) -> str | None:
        return (self.project or {}).get("name")

    def _project_refused(self, name) -> str | None:
        """Why the child cannot start under `name`: none given or the
        session's own project is fine, as is one its spec lists."""
        if name is None or name == self._own():
            return None
        try:
            listed = workspace.Spec.load(self._own()).projects if self._own() else ()
        except workspace.Refused:
            listed = ()
        if name not in listed:
            return f"project {name!r} is not one of this chat's repositories ({', '.join([self._own() or '', *listed])})"
        try:
            workspace.Spec.load(name)
        except workspace.Refused as exc:
            return f"project {name!r} has no spec that loads: {exc}"
        return None

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
                project=self._child_project(action.payload.get("project")),
            )
        return {"task_id": child}

    def _child_project(self, name) -> dict | None:
        """The child's project: the named one, else the session's own, with
        the session's chat, which the child reads (`_reading_env`)."""
        if self.project is None and name is None:
            return None
        found = {"name": name or self._own()}
        return {**found, "chat": self.chat} if self.chat else found

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
        '`task.answer`: target the task id, payload `{"text": "...", "by": "<who answered>", "via": "chat"}`; '
        "answers that task's open question; `via` is how it came (`chat`, `slack reaction :+1:`, "
        "`meet call`). The board chat's session also answers any task of its project waiting on a question."
    )

    async def _child(self, conn, task_id: str) -> tasks.Brief:
        try:
            return await super()._child(conn, task_id)
        except ValueError:
            if not await board.answers(conn, self.session, task_id):
                raise
            return await tasks.brief(conn, task_id)

    async def perform(self, action, key: str) -> dict:
        async with await db.connect(self.dsn) as conn:
            await self._child(conn, action.target)
            question = await session.answer(
                conn, action.target, str(action.payload.get("text") or ""),
                by=str(action.payload.get("by") or "valor"), via=str(action.payload.get("via") or "chat"),
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
        "turn. To a task waiting on its question it is the answer. To a task that has delivered "
        "(merged or ended) it is feedback: the task reopens in its own workspace and conversation, "
        "revises, and reports to you again. A stopped task stays stopped."
    )

    async def perform(self, action, key: str) -> dict:
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

    async def lookup(self, action, key: str) -> dict | None:
        async with await db.connect(self.dsn) as conn:
            rows = await ledger.read(conn, action.target)
        for r in rows:
            if r["type"] in ("message.steered", "feedback.given", "question.answered") and (
                r["payload"].get("effect_key") == key
            ):
                found = {k: r["payload"][k] for k in ("feedback_id", "question_id") if r["payload"].get(k)}
                return {"task_id": action.target, **found}
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
