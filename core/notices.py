"""Reports: what the kernel and the tasks under it tell Valor's sessions.

Only a chat session speaks as Valor, and only by its own sends (Tom,
2026-11-01: "No routine or system should be impersonating you."; plan
`docs/plans/one-voice.md`). The kernel never posts: everything it has to
say is a report, one `child.reported` row on the session that hears it,
which tells its chat in its own words, or nothing.

`request(conn, task_id, kind=, about_key=, text=)` writes the report,
once per reporter (`from`, a task or a stream such as `updates`) and
`about_key` (the `events_one_report` index; a reporter's `notice.requested`
of the same key, from before 2026-11-01, counts as made). The session that
hears it (`hears`): the reporter's nearest ancestor that is a chat session;
else the operator chat's session (`intake.operator_session`). A chat
session hears its own reports (a send in doubt, a message it could not
bind), but its end goes to the next session of its chat only when it
ended with a message unanswered (`unanswered`); a session that ends holding
only reports says nothing further. A `session` the caller names (a
`/update` reply, to its room's) is taken as given. With no session to hear
it, the row is written on the reporter itself, quiet, so the record holds it.

`child_id` names the reporter for the kinds a session acts on (`ACTED`: a
question, an end, a merge's report), so `tasks.reporting` and an exact
`stop` reach what they reached before; the kernel's own kinds (credential,
binding, rollout, update, disk, send in doubt) carry none.

A routine's result (`RESULTS` from background work, `tasks.routine_of`) is
`quiet`: it wakes no turn and is shown at the session's next one as a
routine result held in the record. A routine's
question wakes the session, as everything else does.

`owe(conn, task_id)` writes the reports a task's fold owes: its open
question (`question:<id>`; none to its project's board session, whose board
row carries it, `core/board.py`), once it is open `board.STALE_DAYS` the
follow-up asking Tom whether it is still worth doing (`stale:<id>`, to the
same session: the chat it was asked in, Tom 2026-11-06), and its end without a merge
(`ended:<row>`). `credential` is the push credential a merge needs, and
`report` the merge that landed (`report:<effect>`). A report whose text
Postgres jsonb refuses is written with kernel text in its place.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from core import ledger, machine
from core.machine import State

# The kinds whose reporter a session answers, steers and stops.
ACTED = ("question", "ended", "report")
# A routine's results: quiet.
RESULTS = ("ended", "report")
# The follow-up to a question open `board.STALE_DAYS`, in the chat it was asked in.
STALE = (
    "My question below has gone {days}+ days unanswered. Ask Tom here whether it is still worth doing, "
    "with the question's full context and your recommendation, keep or close, and the reason. "
    "His 👍 or a reply to keep leaves it waiting; his 👎 or a reply to close is `task.stop`. "
    "Neither is an answer to the question."
)


async def request(
    conn, task_id: str, *, kind: str, about_key: str, text: str, session: str | None = None
) -> str | None:
    """One report, on the session that hears it; its `about_key`, or None
    when the reporter already made it."""
    if await _made(conn, task_id, about_key):
        return None
    hearer = session or await hears(conn, task_id, kind)
    from core import tasks

    try:
        b = await tasks.brief(conn, task_id)
    except KeyError:
        b = None
    payload: dict[str, Any] = {
        "from": task_id,
        "child_id": task_id if b is not None and kind in ACTED else None,
        "kind": kind,
        "about_key": about_key,
        "text": text,
    }
    if b is None:
        payload["stream"] = True
    if hearer is None or (b is not None and kind in RESULTS and await _routine_result(conn, task_id)):
        payload["quiet"] = True
    try:
        async with conn.transaction():
            written, why = await ledger.try_append(conn, hearer or task_id, "child.reported", payload)
            if written is None:
                await ledger.append(conn, hearer or task_id, "child.reported", {
                    **payload, "text": _unstorable(await headline(conn, task_id), kind), "unstorable": why})  # fmt: skip
    except psycopg.errors.UniqueViolation:
        return None
    return about_key


async def _made(conn, task_id: str, about_key: str) -> bool:
    """Whether the reporter made this report already: a `child.reported`
    naming it (by `from`, or by `child_id` before reports named their
    reporter), or a `notice.requested` of the kernel's notices before
    2026-11-01, which a bridge sent."""
    found = await (
        await conn.execute(
            "SELECT 1 FROM events WHERE (type = 'child.reported' AND (payload @> %s OR payload @> %s)) "
            "OR (type = 'notice.requested' AND task_id = %s AND payload->>'about_key' = %s) LIMIT 1",
            (
                Jsonb({"from": task_id, "about_key": about_key}),
                Jsonb({"child_id": task_id, "about_key": about_key}),
                task_id,
                about_key,
            ),
        )
    ).fetchone()
    return found is not None


async def _routine_result(conn, task_id: str) -> bool:
    """Whether the task is a routine's work."""
    from core import tasks

    return await tasks.routine_of(conn, task_id) is not None


async def hears(conn, task_id: str, kind: str) -> str | None:
    """The session that hears the reporter's report (module docstring)."""
    from core import intake, tasks

    try:
        b = await tasks.brief(conn, task_id)
    except KeyError:
        return await intake.operator_session(conn)
    if b.chat:
        if kind != "ended":
            return b.id
        if await unanswered(conn, task_id, await ledger.read(conn, task_id)) is None:
            return None
        from core import workspace

        try:
            spec = workspace.Spec.load(b.project["name"]) if b.project else None
        except workspace.Refused, FileNotFoundError:
            spec = None
        return await intake.session_for(conn, b.chat["channel"], b.chat["chat_id"], spec)
    while b.parent_id is not None:
        b = await tasks.brief(conn, b.parent_id)
        if b.chat:
            return b.id
    return await intake.operator_session(conn)


async def owe(conn, task_id: str) -> list[str]:
    """Request every report the task's fold owes. Returns the new keys. A
    Meet call's session owes none: its call reports to the chat that asked
    for it (`core/call.py`)."""
    rows = await ledger.read(conn, task_id)
    f = machine.fold(rows)
    if f.legacy or f.calibration or f.state in (State.STOPPED, State.MERGED):
        return []
    if any(r["type"] == "task.started" and "call" in r["payload"] for r in rows[:1]):
        return []
    made: list[str | None] = []
    from core import board

    if f.state is State.WAITING and f.open_question and not await board.here(conn, task_id):
        asked = next(
            (r for r in rows if r["type"] == "question.asked"
             and r["payload"].get("question_id") == f.open_question),
            None,
        )  # fmt: skip
        if asked is not None:
            head, question = await headline(conn, task_id), str(asked["payload"].get("text", ""))
            made.append(
                await request(
                    conn,
                    task_id,
                    kind="question",
                    about_key=f"question:{f.open_question}",
                    text="\n\n".join([lead(head, "I have a question for you."), question]),
                )
            )
            if asked["at"] < datetime.now(UTC) - timedelta(days=board.STALE_DAYS):
                made.append(
                    await request(
                        conn,
                        task_id,
                        kind="question",
                        about_key=f"stale:{f.open_question}",
                        text="\n\n".join([lead(head, STALE.format(days=board.STALE_DAYS)), question]),
                    )
                )
    if f.state is State.ENDED and f.entry is not None:
        said = await unanswered(conn, task_id, rows)
        made.append(
            await request(
                conn,
                task_id,
                kind="ended",
                about_key=f"ended:{f.entry.get('id')}",
                text=ended_text(f, await headline(conn, task_id), said),
            )
        )
    return [m for m in made if m]


async def unanswered(conn, task_id: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The message a chat session ended without answering: its latest
    `message.steered`, when no turn collected after it went through. None
    for a task that is no chat session."""
    from core import tasks

    if not (await tasks.brief(conn, task_id)).chat:
        return None
    for r in reversed(rows):
        if r["type"] == "turn.collected" and r["payload"].get("verdict") != "failed":
            return None
        if r["type"] == "message.steered":
            return r["payload"]
    return None


def short(instruction: str | None, limit: int = 80) -> str | None:
    """A task's request as a notice names it: the first line with words on
    it, cut on a word at `limit` characters with `...`."""
    line = next((s.strip() for s in (instruction or "").splitlines() if s.strip()), "")
    if not line:
        return None
    if len(line) <= limit:
        return line
    cut = line[: limit - 3].rsplit(" ", 1)[0].rstrip(" ,;:.") or line[: limit - 3]
    return cut + "..."


async def headline(conn, task_id: str) -> str | None:
    """The task's request, shortened; None for a task with no Brief (or an
    id that names no task, such as the channel a failed binding falls back
    to), and for a chat session, whose instruction is its opening, not a
    request, and whose notices go to the chat it serves (Wingtip Flowers'
    ended notice, 2026-10-19, quoted "You are in the wingtip team
    chat...")."""
    from core import tasks

    try:
        b = await tasks.brief(conn, task_id)
    except KeyError:
        return None
    return None if b.chat else short(b.instruction)


def lead(head: str | None, sentence: str) -> str:
    """`sentence` opened with the task's request, so Tom knows which work it
    is about without an id; capitalised when there is no request to name."""
    if head:
        return f'On "{head}": {sentence}'
    return sentence[:1].upper() + sentence[1:]


def note(summary: str | None) -> str | None:
    """The note to Tom a turn wrote at the top of `done.md`: its text above
    its first line that is exactly `---`. None when there is no such line or
    nothing above it; the report below is never the note."""
    lines = str(summary or "").splitlines()
    at = next((i for i, line in enumerate(lines) if line.rstrip() == "---"), None)
    if at is None:
        return None
    return "\n".join(lines[:at]).strip() or None


# A check or state, as a report names the step that kept failing.
STEPS = {"test": "its tests", "review": "its review", "docs": "its documentation check"}
# Why a merge that ended the task did not land (`verdicts.merge_failure`;
# a moved target rebases, it never ends one).
MERGE_WHY = {
    "refused": "the merge was refused, and trying again would get the same answer",
    "transient": "the merge kept failing on errors from GitHub or the network",
}


def ended_text(f: machine.Fold, head: str | None, said: dict[str, Any] | None = None) -> str:
    """The report owed when a task ends without merging: why it stopped,
    in Valor's words when the working session decided it, in plain words
    for the kernel's own reasons (the raw error stays on `task.ended`), and
    what was built. A chat session whose steps kept failing on a message
    (`said`, `unanswered`) tells its chat's next session which message went
    unanswered, so that session answers it."""
    entry = f.entry or {}
    p = entry.get("payload") or {}
    built = note((f.delivery or {}).get("summary"))
    kind = p.get("kind") if entry.get("type") == "task.ended" else "rounds"
    parts: list[str | None]
    if kind == "step" and said is not None:
        quoted = short(said.get("text"), 200)
        return (
            f"The session before you in this chat failed {p.get('tries') or 'several'} times in a row on "
            f"message {said.get('message_id')}"
            + (f' ("{quoted}")' if quoted else "")
            + " and sent nothing. It is unanswered: answer it."
        )
    if kind == "decided":
        text = str(p.get("text") or "")
        done = text.lstrip().lower().startswith("done:")
        why = "This is done; nothing needed merging." if done else "I stopped this work without merging it."
        parts = [lead(head, why), text]
    elif kind == "rounds":
        parts = [
            lead(
                head,
                "I stopped this work without merging it: after its last round it still did not "
                "pass its checks.",
            ),
            built and f"What was built: {built}",
        ]
    elif kind == "step":
        what = STEPS.get(p.get("check") or "", "one of its steps")
        parts = [
            lead(head, f"I stopped this work: {what} failed {p.get('tries') or 'several'} times in a row."),
            built and f"What was built before it: {built}",
        ]
    elif kind == "provision":
        parts = [
            lead(
                head,
                "I stopped this work before it began: I could not set up a copy of the project for it "
                f"to work in, after {p.get('tries') or 'several'} tries.",
            ),
        ]
    elif kind == "merge":
        why = MERGE_WHY.get(p.get("why") or "")
        parts = [
            lead(
                head,
                "I stopped this work: it passed its checks, but I could not merge it"
                + (f": {why}." if why else ".")
                + " The work stays on its branch.",
            ),
            built and f"What was built: {built}",
        ]
    else:
        parts = [lead(head, "I stopped this work without merging it."), built and f"What was built: {built}"]
    return "\n\n".join(x for x in parts if x)


async def credential(conn, task_id: str, sha: str, *, missing: bool) -> str | None:
    """Report the push credential a merge needs, once per candidate: the
    one merge failure only Tom can resolve, which wakes the session. `missing` tells a token not
    set up from one GitHub refused, since the fix differs. The merge is
    tried again on its own once the credential is in place."""
    why = (
        "no GitHub push token is set up on this machine."
        if missing
        else "GitHub refused the push token on this machine; it may have expired or lost access to "
        "this repository."
    )
    text = "\n\n".join(
        [
            lead(await headline(conn, task_id), f"it is ready to merge, but I cannot push it: {why}"),
            "Once a working token is in place I merge it on my own.",
        ]
    )
    return await request(conn, task_id, kind="credential", about_key=f"credential:{sha}", text=text)


async def report(conn, task_id: str, effect: dict[str, Any]) -> str | None:
    """The `report` a merge that landed owes, inside the caller's
    transaction; None for any other effect. Only a chat session sends, and
    it knows what it sent."""
    if effect.get("action_type") != "merge":
        return None
    payload = effect.get("payload") or {}
    f = machine.fold(await ledger.read(conn, task_id))
    branch = payload.get("target_branch") or effect.get("target")
    built = note((f.delivery or {}).get("summary"))
    text = "\n\n".join(
        x for x in (lead(await headline(conn, task_id), f"it is done and merged into {branch}."), built) if x
    )
    return await request(conn, task_id, kind="report", about_key=f"report:{effect['effect_id']}", text=text)


def _unstorable(head: str | None, kind: str) -> str:
    """A report's text when Postgres jsonb refuses the one rendered: kernel
    words only, so the session still hears of it; the row's `unstorable`
    holds Postgres's reason."""
    what = kind.replace("_", " ")
    return lead(
        head,
        f"I had a {what} report about this work that I could not store, so this note "
        "stands in for it. The record of the work holds what it is about.",
    )
