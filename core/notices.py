"""Operator notices: what the kernel tells Tom, as rows a bridge sends.

Tom hears reports of what was done and questions only he can answer
(Tom's rulings of 2026-10-03 and 2026-10-09). Each kernel-written text
speaks to Tom as Valor, the way a deputy and general manager writes to
the CEO: it names the task by the first line of its request (`headline`,
`lead`), says what was done and why in outcome terms, and asks at most one
plain thing. No task, notice, or effect id, commit, path, command, link, stage
name, or raw error is in the text; those stay in the ledger. Where a turn wrote
a note to Tom (the text of `done.md` above its first `---` line, or
`stop.md`), the note is the message.

`owe(conn, task_id)` writes the `notice.requested` rows a task's fold owes
and has not requested, to `settings.operator_channel` and
`settings.operator_chat`:

- `question` (`about_key` `question:<id>`): the open question.
- `ended` (`ended:<row>`): the task ended without merging: what was built,
  and why it stopped, in plain words. A reply to it is feedback.

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
what it is about. The bridge sends notices as they come.
"""

from pathlib import Path
from typing import Any

import psycopg

from core import ledger, machine
from core.machine import State
from core.settings import settings


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
                "text": text,
                "reply_to": reply_to,
            }
            written, why = await ledger.try_append(conn, task_id, "notice.requested", notice)
            if written is None:
                await ledger.append(conn, task_id, "notice.requested", {**notice, "text": _unstorable(
                    await headline(conn, task_id), kind), "unstorable": why})  # fmt: skip
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
                    text="\n\n".join(
                        [
                            lead(await headline(conn, task_id), "I have a question for you."),
                            str(asked.get("text", "")),
                            "Reply to this message to answer.",
                        ]
                    ),
                )
            )
    if f.state is State.ENDED and f.entry is not None:
        made.append(
            await request(
                conn,
                task_id,
                kind="ended",
                about_key=f"ended:{f.entry.get('id')}",
                text=ended_text(f, await headline(conn, task_id)),
            )
        )
    return [m for m in made if m]


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
    to)."""
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


# A check or state, as a notice names the step that kept failing.
STEPS = {"test": "its tests", "review": "its review", "docs": "its documentation check"}
# Why a merge that ended the task did not land (`verdicts.merge_failure`;
# a moved target rebases, it never ends one), in Tom's words.
MERGE_WHY = {
    "refused": "the merge was refused, and trying again would get the same answer",
    "transient": "the merge kept failing on errors from GitHub or the network",
}
AGAIN = "Reply to this message if you want me to pick it up again or change course."


def ended_text(f: machine.Fold, head: str | None) -> str:
    """The report owed when a task ends without merging: why it stopped,
    in Valor's words when the working session decided it, in plain words
    for the kernel's own reasons (the raw error stays on `task.ended`), and
    what was built. A reply to it is feedback."""
    entry = f.entry or {}
    p = entry.get("payload") or {}
    built = note((f.delivery or {}).get("summary"))
    kind = p.get("kind") if entry.get("type") == "task.ended" else "rounds"
    parts: list[str | None]
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
    parts.append(AGAIN)
    return "\n\n".join(x for x in parts if x)


async def credential(conn, task_id: str, sha: str, *, missing: bool) -> str | None:
    """Ask Tom for the push credential a merge needs, once per candidate:
    the one merge failure only he can resolve. `missing` tells a token not
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
            )
            if x
        )
    else:
        body = payload.get("text") or payload.get("subject") or payload.get("body") or ""
        files = [Path(f.get("path", "")).name for f in payload.get("files") or []]
        text = "\n\n".join(
            x
            for x in (
                lead(await headline(conn, task_id), f"I sent {_sent_to(effect)}:"),
                body,
                files and f"Files: {', '.join(files)}",
            )
            if x
        )
    return await request(conn, task_id, kind="report", about_key=f"report:{effect['effect_id']}", text=text)


def _sent_to(effect: dict[str, Any]) -> str:
    """What a send was and who it went to, as Tom knows them: email
    addresses, else the project whose chat it is (`Spec.chats`), never a
    chat id."""
    kind, target, payload = (
        effect.get("action_type"),
        str(effect.get("target") or ""),
        effect.get("payload") or {},
    )
    if kind == "email.send":
        if payload.get("reply_to"):
            return "a reply to everyone on an email thread"
        return "an email to " + ", ".join([*(payload.get("to") or []), *(payload.get("cc") or [])])
    if kind not in ("telegram.send_message", "slack.send_message"):
        return "a message"
    channel = kind.split(".")[0]
    place = "Telegram" if channel == "telegram" else "Slack"
    from core import workspace

    for spec in workspace.Spec.all():
        if f"{channel}:{target}".casefold() in {c.casefold() for c in spec.chats}:
            return f"a message to the {spec.name} chat on {place}"
    return f"a message to a {place} chat" if channel == "telegram" else "a message to a Slack channel"


def _unstorable(head: str | None, kind: str) -> str:
    """A notice's text when Postgres jsonb refuses the one rendered: kernel
    words only, so Tom still hears of it; the row's `unstorable` holds
    Postgres's reason."""
    what = kind.replace("_", " ")
    return lead(
        head,
        f"I had a {what} message for you about this work that I could not store, so this note "
        "stands in for it. The record of the work holds what it is about.",
    )
