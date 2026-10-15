"""Operator notices: what the kernel tells Tom, as rows a bridge sends.

Tom hears reports of what was done and questions only he can answer
(Tom's rulings of 2026-10-03 and 2026-10-09). Each kernel-written text
speaks to Tom as Valor, the way a deputy and general manager writes to
the CEO: it names the task by the first line of its request (`headline`,
`lead`), says what was done in outcome terms, and links the task's status page
(`report_link`) for the checks and findings behind it. Where a turn wrote
a note to Tom (the text of `done.md` above its first `---` line, or
`stop.md`), the note is the message.

`owe(conn, task_id)` writes the `notice.requested` rows a task's fold owes
and has not requested, to `settings.operator_channel` and
`settings.operator_chat`:

- `question` (`about_key` `question:<id>`): the open question.
- `delivered` (`delivered:<sha>`): a delivery whose merge waits on Tom's
  grant of a governance instance: each named, with its `path:line` and its
  `python -m core grant` command.
- `ended` (`ended:<row>`): the task ended without merging: what was built,
  what did not pass, and why it stopped. It asks for nothing; a reply to
  it is feedback.

`credential(conn, task_id, sha, why)` asks Tom for the push credential a
merge needs (`credential:<sha>`), the one merge failure that reaches him.

`report(conn, task_id, effect, result)` writes the `report` notice
(`report:<effect>`) an `act` effect owes once its outcome is `done`: what
left. A merge's says what shipped; a send's carries its channel,
recipients, and text. A push owes none (a step inside the task), nor does
a send that itself reached Tom (to the operator chat or Tom's Telegram id,
an email only to Tom's addresses, any local send). A reply to a merge's report is feedback.

A notice with no channel or chat (the operator's unset) is written with a
`notice.undeliverable` row beside it, so the ledger shows it was never
sent. `request` writes one notice; binding (`core/intake.py`) uses it for
its own. A notice is requested once
per task and `about_key` (`events_one_notice`): a second request, from
another kernel or a retry, is skipped in a savepoint. A notice whose text
Postgres jsonb refuses is written with kernel text in its place, naming
what it is about and Postgres's reason. Each text carries the
notice's short id, so a bridge's lookup matches the sent message exactly.
The bridge sends notices as they come.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import psycopg

from core import ledger, machine
from core.machine import State
from core.settings import settings


def tag(notice_id: str) -> str:
    """The short id a rendered notice carries."""
    return f"[n:{notice_id}]"


async def request(
    conn,
    task_id: str,
    *,
    kind: str,
    about_key: str,
    text: str,
    reply_to: str | None = None,
    channel: str | None = None,
    chat_id: str | None = None,
) -> str | None:
    """One `notice.requested` row; its id, or None when one with this
    `about_key` already stands for the task."""
    found = await (
        await conn.execute(
            "SELECT 1 FROM events WHERE task_id = %s AND type = 'notice.requested' "
            "AND payload->>'about_key' = %s",
            (task_id, about_key),
        )
    ).fetchone()
    if found is not None:
        return None
    notice_id = ledger.new_id()
    channel = channel or settings.operator_channel
    chat_id = chat_id if chat_id is not None else settings.operator_chat
    try:
        async with conn.transaction():
            notice = {
                "notice_id": notice_id,
                "channel": channel,
                "chat_id": chat_id,
                "kind": kind,
                "about_key": about_key,
                "text": f"{text}\n\n{tag(notice_id)}",
                "reply_to": reply_to,
            }
            written, why = await ledger.try_append(conn, task_id, "notice.requested", notice)
            if written is None:
                await ledger.append(conn, task_id, "notice.requested", {**notice, "text": _unstorable(
                    task_id, kind, about_key, why, notice_id)})  # fmt: skip
            if not channel or not chat_id:
                # No bridge sends a notice with no chat: the ledger says so.
                await ledger.append(
                    conn,
                    task_id,
                    "notice.undeliverable",
                    {
                        "notice_id": notice_id,
                        "reason": "no operator channel or chat is set "
                        "(VALOR_OPERATOR_CHANNEL, VALOR_OPERATOR_CHAT)",
                    },
                )
    except psycopg.errors.UniqueViolation:
        return None
    return notice_id


async def owe(conn, task_id: str) -> list[str]:
    """Request every notice the task's fold owes. Returns the new ids."""
    rows = await ledger.read(conn, task_id)
    f = machine.fold(rows)
    if f.legacy or f.calibration or f.state in (State.STOPPED, State.MERGED):
        return []
    made: list[str | None] = []
    if f.state is State.WAITING and f.open_question:
        asked = next(
            (r["payload"] for r in rows if r["type"] == "question.asked"
             and r["payload"].get("question_id") == f.open_question),
            None,
        )  # fmt: skip
        if asked is not None:
            made.append(
                await request(
                    conn,
                    task_id,
                    kind="question",
                    about_key=f"question:{f.open_question}",
                    text=f"Task {task_id} asks:\n\n{asked.get('text', '')}\n\nReply to this message to answer.",
                )
            )
    if f.delivery and f.state is State.MERGE:
        owed = _delivered_owed(f)
        if owed is not None:
            made.append(
                await request(
                    conn,
                    task_id,
                    kind="delivered",
                    about_key=owed,
                    text=delivered_text(task_id, f.delivery, f.ungranted(), await headline(conn, task_id)),
                )
            )
    if f.state is State.ENDED and f.entry is not None:
        made.append(
            await request(
                conn,
                task_id,
                kind="ended",
                about_key=f"ended:{f.entry.get('id')}",
                text=ended_text(task_id, f, await headline(conn, task_id)),
            )
        )
    return [m for m in made if m]


def _delivered_owed(f: machine.Fold) -> str | None:
    """The `delivered` notice's `about_key` while the current delivery's
    merge waits on Tom's grant of a governance instance, else None (a merge
    that lands has its report; one that cannot ends the task)."""
    sha = ((f.delivery.get("candidate") or {}).get("sha")) or ""
    current = f.candidate is not None and (f.delivery.get("candidate") or {}) == {
        "sha": f.candidate.sha,
        "turn_id": f.candidate.turn_id,
    }
    if current and f.delivery.get("outcome") in ("passed", "gaps", "governance_refused") and f.ungranted():
        return f"delivered:{sha}"
    return None


def report_link(task_id: str) -> str:
    """The task's page on the status page (`ui/`), where its checks,
    findings, and ledger are."""
    return f"http://127.0.0.1:{settings.ui_port}/task/{task_id}"


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
    """The task's request, shortened; None for a task with no Brief."""
    from core import tasks

    try:
        return short((await tasks.brief(conn, task_id)).instruction)
    except KeyError:
        return None


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


def _reason(text: Any, limit: int = 300) -> str:
    line = next((s.strip() for s in str(text or "").splitlines() if s.strip()), "")
    return line if len(line) <= limit else line[: limit - 3] + "..."


def ended_text(task_id: str, f: machine.Fold, head: str | None) -> str:
    """The report owed when a task ends without merging: why it stopped,
    in Valor's words when the working session decided it, and what was
    built, with the status page for the rest. It asks for nothing."""
    entry = f.entry or {}
    p = entry.get("payload") or {}
    built = note((f.delivery or {}).get("summary"))
    kind = p.get("kind") if entry.get("type") == "task.ended" else "rounds"
    parts: list[str | None]
    if kind == "decided":
        parts = [lead(head, "I stopped this work without merging it."), str(p.get("text") or "")]
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
        what = p.get("check") or p.get("state") or "a step"
        parts = [
            lead(
                head,
                f"I stopped this work: its {what} step failed {p.get('tries')} times in a row. "
                f"The last failure: {_reason(p.get('reason'))}",
            ),
            built and f"What was built before it: {built}",
        ]
    elif kind == "provision":
        parts = [
            lead(
                head,
                f"I stopped this work before it began: I could not set up its workspace after "
                f"{p.get('tries')} tries. The last failure: {_reason(p.get('reason'))}",
            ),
        ]
    elif kind == "merge":
        parts = [
            lead(
                head,
                f"I stopped this work: it passed its checks, but I could not merge it: "
                f"{_reason(p.get('reason'))}. The work stays on its branch.",
            ),
            built and f"What was built: {built}",
        ]
    else:
        parts = [lead(head, f"I stopped this work: {_reason(p.get('reason'))}")]
    parts.append(f"The details are on its status page: {report_link(task_id)}")
    return "\n\n".join(x for x in parts if x)


async def credential(conn, task_id: str, sha: str, why: str) -> str | None:
    """Ask Tom for the push credential a merge needs, once per candidate:
    the one merge failure only he can resolve. The merge is tried again on
    its own once the credential is in place."""
    head = await headline(conn, task_id)
    text = "\n\n".join(
        [
            lead(head, "it is ready to merge, and I need a working GitHub push token to merge it."),
            _reason(why),
            "Once the token is in place I merge it on my own.",
        ]
    )
    return await request(conn, task_id, kind="credential", about_key=f"credential:{sha}", text=text)


def _to_tom(effect: dict[str, Any]) -> bool:
    """Whether a send is itself a message to Tom."""
    kind, target, payload = (
        effect.get("action_type"),
        str(effect.get("target") or ""),
        effect.get("payload") or {},
    )
    if kind == "local.send_message":
        return True
    if kind == "telegram.send_message":
        own = {settings.operator_telegram_id}
        if settings.operator_channel == "telegram":
            own.add(settings.operator_chat)
        return target in own - {None}
    if kind == "slack.send_message":
        own = set(settings.operator_slack_ids)
        if settings.operator_channel == "slack":
            own.add(settings.operator_chat)
        return target.upper() in own - {None}
    if kind == "email.send":
        sent = [*(payload.get("to") or []), *(payload.get("cc") or [])]
        mine = {a.casefold() for a in settings.operator_email}
        return bool(sent) and not payload.get("reply_to") and all(str(a).casefold() in mine for a in sent)
    return False


async def report(conn, task_id: str, effect: dict[str, Any], result: dict[str, Any]) -> str | None:
    """The `report` notice a `done` act owes Tom, inside the caller's
    transaction; None when it owes none (see the module docstring)."""

    kind = effect.get("action_type")
    if effect.get("effect_class") != "act" or kind == "push_branch" or _to_tom(effect):
        return None
    payload = effect.get("payload") or {}
    if kind == "merge":
        f = machine.fold(await ledger.read(conn, task_id))
        branch = payload.get("target_branch") or effect.get("target")
        built = note((f.delivery or {}).get("summary"))
        text = "\n\n".join(
            x
            for x in (
                lead(await headline(conn, task_id), f"it is done and merged into {branch}."),
                built,
                f"The details are on its status page: {report_link(task_id)}",
            )
            if x
        )
    else:
        body = payload.get("text") or payload.get("subject") or payload.get("body") or ""
        to = ", ".join([*(payload.get("to") or []), *(payload.get("cc") or [])]) or effect.get("target")
        if kind == "email.send" and payload.get("reply_to"):
            to = f"all of {payload['reply_to']}"
        files = [Path(f.get("path", "")).name for f in payload.get("files") or []]
        text = f"Task {task_id} sent {kind} to {to}:\n\n{body}" + (
            f"\n\nFiles: {', '.join(files)}" if files else ""
        )
    return await request(conn, task_id, kind="report", about_key=f"report:{effect['effect_id']}", text=text)


def _unstorable(task_id: str, kind: str, about_key: str, why: str, notice_id: str) -> str:
    """A notice's text when Postgres jsonb refuses the one rendered: kernel
    words only, naming what it is about, so Tom still hears of it."""
    return (
        f"Task {task_id} has a notice ({kind}, {about_key}) whose text the ledger's JSON (Postgres jsonb) "
        f"cannot store: {why}. The task's ledger holds what it is about.\n\n{tag(notice_id)}"
    )


def delivered_text(
    task_id: str,
    delivery: dict[str, Any],
    waiting: Sequence[machine.Instance] = (),
    head: str | None = None,
) -> str:
    """The `delivered` notice: the merge waits on Tom's grant of each
    governance instance named, where it sits in the diff, and the command
    that grants it. Under autonomous act this is the one question about
    that merge."""
    lines = [
        lead(
            head,
            "it is ready, and it adds a check or approval step, which needs your grant before it can merge.",
        ),
    ]
    built = note(delivery.get("summary"))
    if built:
        lines += ["", built]
    lines.append("")
    for i in waiting:
        where = f"{i.path}:{i.line}" if i.line is not None else i.path
        lines += [
            f"- governance instance {i.id} at {where}" + (f" ({i.summary})" if i.summary else ""),
            f'  grant with: python -m core grant {task_id} {i.id} --note "..."',
        ]
    lines += ["", f"The details are on its status page: {report_link(task_id)}"]
    return "\n".join(lines)
