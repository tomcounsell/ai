"""A chat session sees what Valor posted in its chat, whichever session or
Mac sent it (Tom, 2026-11-10: a channel's session sent a file into a
teammate's DM; the DM's session, on another Mac, said it never had). The
Slack bridge carries Valor's own posts before each message (`posted`), the
session's own sends are dropped from it, and `chat_line` shows the rest.
Made-up teams and people."""

from __future__ import annotations

import asyncio

from bridges.slack import inbound
from bridges.slack.wire import RateLimited
from core import db, intake, ledger, session
from tests.test_workspace_dms import KIM_A, TA, dm, on, root  # noqa: F401 - the fixture

ME = "UVALOR"


def m(ts, text, user=ME, **kw):
    return {"type": "message", "ts": ts, "user": user, "text": text, **kw}


class Wire:
    team_id, user_id = TA, ME

    def __init__(self, history=(), thread=(), fails=None):
        self._history, self._thread, self.fails, self.calls = list(history), list(thread), fails, []

    async def history(self, channel, *, latest=None, limit=None, **kw):
        self.calls.append(("history", channel, latest, limit))
        if self.fails:
            raise self.fails
        page = sorted((x for x in self._history if x["ts"] < latest), key=lambda x: x["ts"], reverse=True)
        return page[:limit], None

    async def replies(self, channel, ts, cursor=None, **kw):
        return list(self._thread), None

    async def user_name(self, user):
        return "Kim"


def build(wire, msg):
    return asyncio.run(inbound.build(wire, KIM_A, "D0KIM", msg, "/nonexistent", dm=True))


def test_a_dm_message_carries_valors_own_posts_since_the_persons_last_message_oldest_first():
    wire = Wire(
        history=[
            m("1.0", "an older post"),
            m("1.1", "can I see it?", "U0KIM"),
            m("1.2", "here it is", files=[{"name": "report.pdf"}]),
            m("1.3", "and the notes"),
        ]
    )
    got = build(wire, m("1.4", "thanks, one question", "U0KIM"))
    assert [(p["id"], p["text"], p["files"]) for p in got["posted"]] == [
        ("1.2", "here it is", ["report.pdf"]),
        ("1.3", "and the notes", []),
    ]
    assert got["posted_failed"] is None and wire.calls == [("history", "D0KIM", "1.4", inbound.POSTED)]
    assert intake.Inbound(**got).posted == got["posted"]  # the kernel's Inbound takes the field


def test_a_thread_reply_takes_valors_posts_from_its_thread_and_reads_no_history():
    thread = [m("2.0", "the plan", "U0KIM"), m("2.1", "a first answer"), m("2.2", "ok", "U0KIM"),
              m("2.3", "the file", files=[{"name": "a.pdf"}]), m("2.4", "after", "U0KIM")]  # fmt: skip
    wire = Wire(thread=thread)
    got = build(wire, m("2.4", "after", "U0KIM", thread_ts="2.0"))
    assert [p["id"] for p in got["posted"]] == ["2.3"] and not wire.calls
    assert [t["id"] for t in got["thread"]] == ["2.0", "2.1", "2.2", "2.3"]


def test_no_post_before_carries_none_and_many_are_cut_to_the_newest():
    assert build(Wire(history=[m("3.0", "hi", "U0KIM")]), m("3.1", "hello", "U0KIM"))["posted"] == []
    many = [m(f"4.{i:02d}", f"post {i}") for i in range(15)]
    got = build(Wire(history=many), m("4.99", "?", "U0KIM"))["posted"]
    assert [p["text"] for p in got] == [f"post {i}" for i in range(15 - inbound.POSTED, 15)]


def test_a_failed_history_read_records_the_message_and_says_why():
    got = build(Wire(fails=RateLimited(30)), m("5.0", "hi", "U0KIM"))
    assert got["posted"] == [] and got["text"] == "hi"
    assert got["posted_failed"] == "RateLimited: rate limited; retry after 30 s"
    assert "could not be read: RateLimited" in intake.chat_line(got)


def test_a_dm_session_reads_a_post_another_session_sent_and_not_its_own_reply_again(dsn, root, tmp_path):  # noqa: F811
    other = {"id": "1793700000.000120", "sent_at": "2026-11-03T09:31:00Z", "text": "the report, new layout",
             "files": ["report.pdf"]}  # fmt: skip
    own = {"id": "1793700000.000110", "sent_at": "2026-11-03T09:30:30Z", "text": "hello Kim", "files": []}
    with on("Pirate", root, tmp_path):

        async def go():
            first = await dm(dsn, KIM_A, "hi there", "1793700000.000100")
            task = first["task_id"]
            async with await db.connect(dsn) as conn:
                sent = [{"channel": "slack", "chat_id": KIM_A, "thread_ts": None, "message_id": own["id"]}]
                await ledger.append(conn, task, "effect.outcome",
                                    {"effect_id": "e1", "idempotency_key": "k", "kind": "done",
                                     "result": {"sent": sent}, "error": None})  # fmt: skip
            second = await dm(dsn, KIM_A, "what is this?", "1793700000.000200", posted=[own, other])
            async with await db.connect(dsn) as conn:
                rows = await ledger.read(conn, task)
            # A session that opens after both posts reads them in its history.
            async with await db.connect(dsn) as conn:
                start = await intake._chat_start(conn, "slack", KIM_A, intake.workspace.Spec.load("yu"),
                                                 False, None)  # fmt: skip
            return second, task, session.chat_prompt(rows), start

        second, task, prompt, start = run(go())
    assert second["task_id"] == task
    assert "before it, you posted here (from this session, another, or another Mac):" in prompt
    assert (
        "[09:31 UTC] message 1793700000.000120:\n  > the report, new layout\n  > (file: report.pdf)" in prompt
    )
    assert "hello Kim" not in prompt
    assert "the report, new layout" in start and "hello Kim" in start


def test_a_message_with_no_posts_reads_as_before():
    msg = {"channel": "slack", "message_id": "6.0", "sender_name": "Kim", "sent_at": "2026-11-03T09:30:00Z",
           "text": "hi", "posted": [], "posted_failed": None}  # fmt: skip
    assert intake.chat_line(msg) == "[2026-11-03 09:30 UTC] Kim (message 6.0, top-level): hi"


def test_the_persona_keeps_a_rooms_work_and_files_in_the_room():
    from core import persona
    from tests.test_kernel import REPO

    flat = " ".join(persona.render(REPO / "persona").split())
    assert "Work that starts in a room stays in that room (Tom, 2026-11-10)." in flat
    assert (
        "A file for the people in a room is sent in that room's thread, since the room is who may see it."
        in flat
    )
    assert "A DM is only for something meant for one person alone." in flat


def run(coro):
    return asyncio.run(coro)
