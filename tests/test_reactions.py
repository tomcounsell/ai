"""Reactions (docs/plans/reactions-activity.md): which messages get one,
👀 placed at once, the activity a turn's actual work sets, each platform's
emoji, and what a session reads of the reaction on a message."""

import asyncio
import base64
import json
import uuid
from types import SimpleNamespace

from core import bridge, db, intake, ledger, reactions, session, tasks, transcripts
from tests import bridges, test_kernel
from tests.test_kernel import QUIET_GROUP, at, posted, said, tom_says, turn

# The kernel tests' fixtures: a project's team chats, and Tom's Slack DM.
team, dm = test_kernel.team, test_kernel.dm

run = asyncio.run
EYES = "\U0001f440"
LONG_AGO, LATER = "2000-01-01T00:00:00Z", "2999-01-01T00:00:00Z"


async def requested(dsn, received_id) -> list[dict]:
    return await bridges.of_type(dsn, "reaction.requested", received_id=received_id)


def send(target, text="ok", reply_to=None, files=()):
    """An effect entry as `session.record` makes it: a chat send."""
    payload = {"text": text, "reply_to": reply_to, "files": [{"path": f, "sha256": "x"} for f in files]}
    return {
        "request": {"action_type": "telegram.send_message", "target": target, "payload": payload},
        "kind": "held",
    }


def effect(action_type, target="t", **payload):
    return {"request": {"action_type": action_type, "target": target, "payload": payload}, "kind": "held"}


async def worked(dsn, task, effects=(), tools=(), before=()) -> str | None:
    """A collected turn of `task`: its transcript holds `tools` (name,
    input) called during it, and `before`, calls of an earlier turn the same
    file still holds. The label `on_turn` writes."""
    turn_id = ledger.new_id()
    lines = [
        json.dumps({"timestamp": when, "message": {"content": [{"type": "tool_use", "name": n, "input": i}]}})
        for when, calls in ((LONG_AGO, before), (LATER, tools))
        for n, i in calls
    ]
    body = {"turn_id": turn_id, "name": "s.jsonl", "offset": 0, "chunk": 0,
            "base64": base64.b64encode("\n".join(lines).encode()).decode()}  # fmt: skip
    async with await db.connect(dsn) as conn:
        await ledger.append(conn, task, "turn.started", {"turn_id": turn_id, "state": "clarify"})
        await conn.execute(
            "INSERT INTO documents (kind, id, body) VALUES (%s, %s, %s::jsonb)",
            (transcripts.KIND, f"{turn_id}/s.jsonl/0", json.dumps(body)),
        )
        transcript = {"files": [{"name": "s.jsonl"}]}
        await ledger.append(conn, task, "turn.ended", {"turn_id": turn_id, "outcome": "done", "result": {},
                                                       "transcript": transcript})  # fmt: skip
        return await reactions.on_turn(conn, task, turn_id, list(effects))


async def place_all(dsn, channel) -> None:
    """The bridge's part: every due reaction on `channel` placed."""
    async with bridges.outbox(dsn, bridges.FakeBridge(channel)) as box:
        for due in await box._reactions():
            await box.reacted(due)


def test_every_label_has_an_emoji_on_each_platform_and_nothing_else():
    """Each activity, plus `seen` and `stop`, on Telegram and Slack."""
    labels = set(reactions.ORDER) | {"seen", "stop"}
    assert set(reactions.REACTIONS) == {"telegram", "slack"}
    for channel, table in reactions.REACTIONS.items():
        assert set(table) == labels, channel
        assert all("️" not in e for e in table.values()), channel
    assert reactions.REACTIONS["slack"]["seen"] == "eyes"


def test_in_team_chats_only_a_message_that_wakes_the_session_is_marked(dsn, team):
    """An Eng group's every message, and in a mentions-only group one naming
    Valor or replying to his post, gets 👀; an unaddressed one gets none."""

    async def go():
        eng = await said(dsn, "the build is red")
        named = await said(dsn, "valor, is staging up?", chat=QUIET_GROUP)
        mid = "5550"
        entries = [{"channel": "telegram", "chat_id": QUIET_GROUP, "message_id": mid}]
        async with await db.connect(dsn) as conn:
            await ledger.append(
                conn, named["task_id"], "notice.sent", {"notice_id": ledger.new_id(), "sent": entries}
            )
        replied = await said(dsn, "yes that one", chat=QUIET_GROUP, reply_to=mid)
        quiet = await said(dsn, "lunch at noon?", chat=QUIET_GROUP)
        return [(b["as"], await requested(dsn, b["received_id"])) for b in (eng, named, replied, quiet)]

    eng, named, replied, quiet = run(go())
    for as_, rows in (eng, named):
        assert as_ == "steer" and [(r["emoji"], r["label"], r["stage"]) for r in rows] == [
            (EYES, "seen", "seen")
        ]
    assert quiet == ("heard", [])
    assert replied[0] == "steer" and [r["emoji"] for r in replied[1]] == [EYES]


def test_toms_dm_is_marked_and_a_reply_to_finished_work_keeps_its_eyes(dsn, dm):
    """Every message of Tom's in his DM gets `eyes`; his reply to a stopped
    task's post is marked read for good, so no turn's work changes it."""

    async def go():
        top = await tom_says(dsn, dm, "what's on today?", at(10))
        async with await db.connect(dsn) as conn:
            done = await tasks.start(conn, tasks.Brief(instruction="old work", max_effect_class="act"))
        await posted(dsn, done, dm, at(20), "Done.")
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, done, reason="test")
        late = await tom_says(dsn, dm, "one more thing", at(30), reply_to=at(20))
        later = await worked(dsn, done, tools=[("WebSearch", {"query": "x"})])
        return (
            top,
            late,
            await requested(dsn, top["received_id"]),
            await requested(dsn, late["received_id"]),
            later,
        )

    top, late, top_rows, late_rows, later = run(go())
    assert top["as"] == "steer" and [(r["emoji"], r["label"]) for r in top_rows] == [("eyes", "seen")]
    assert late["as"] == "none" and [(r["emoji"], r["final"]) for r in late_rows] == [("eyes", True)]
    assert later is None


def test_a_local_message_is_not_marked(dsn, dm):
    async def go():
        inbound = intake.Inbound(
            channel="local", chat_id="local", chat_kind="dm", message_id=str(uuid.uuid4()), sender_id="tom",
            sender_name="Tom", sent_at="2026-10-17T00:00:00Z", text="hello",
        )  # fmt: skip
        async with await db.connect(dsn) as conn:
            got = await intake.receive(conn, inbound)
            await intake.bind(conn)
        return await requested(dsn, got.received_id)

    assert run(go()) == []


def test_nothing_is_guessed_on_arrival_and_a_plain_reply_keeps_eyes(dsn, team):
    """A message gets 👀 alone; a turn that only replies in the chat, even
    reading a file and writing its own `.valor/` files, keeps it."""

    async def go():
        q = await said(dsn, "what time is it in Bangkok?")
        before = await requested(dsn, q["received_id"])
        tools = [("Read", {"file_path": "/w/notes.md"}), ("Write", {"file_path": "/w/repo/.valor/done.md"}),
                 ("Bash", {"command": "date"})]  # fmt: skip
        label = await worked(dsn, q["task_id"], [send(test_kernel.GROUP, reply_to=q["message_id"])], tools,
                             before=[("Edit", {"file_path": "/w/app.py"})])  # fmt: skip
        return before, label, await requested(dsn, q["received_id"])

    before, label, after = run(go())
    assert [r["label"] for r in before] == ["seen"] and label is None and after == before


def test_what_a_turn_did_sets_the_activity_on_the_message_it_answered(dsn, team):
    """The most specific activity wins; it goes on the message the turn's
    send replies to, else the newest still showing 👀, once."""

    async def go():
        a = await said(dsn, "how's the shop?")
        b = await said(dsn, "and look up the florist's hours")
        task = a["task_id"]
        searched = await worked(dsn, task, [send(test_kernel.GROUP, reply_to=a["message_id"])],
                                [("Agent", {"subagent_type": "valor:web", "prompt": "hours"}),
                                 ("Grep", {"pattern": "x"})])  # fmt: skip
        c = await said(dsn, "now fix the menu page")
        fixed = await worked(dsn, task, [effect("task.start", instruction="fix it")],
                             [("Bash", {"command": "git log -3"})])  # fmt: skip
        again = await worked(dsn, task, [effect("task.start")])
        return a, b, c, searched, fixed, again

    a, b, c, searched, fixed, again = run(go())
    assert (searched, fixed, again) == ("web_search", "task", None)
    shapes = {
        x["message_id"]: [(r["emoji"], r["label"], r["did"]) for r in run(requested(dsn, x["received_id"]))
                          if r["stage"] == "shape"]
        for x in (a, b, c)
    }  # fmt: skip
    assert shapes == {
        a["message_id"]: [("\U0001f50d", "web_search", ["web_search", "lookup"])],
        b["message_id"]: [],
        c["message_id"]: [("\U0001fae1", "task", ["task", "lookup"])],
    }


def test_each_activity_from_effects_and_tools():
    here = {"-1001"}
    by_effect = {
        "task": effect("task.start"), "code": effect("push_branch"), "send": effect("email.send"),
        "present": send("-1001", files=["/w/deck.pptx", "/w/reply.ogg"]),
        "document": send("-1001", files=["/w/brief.pdf"]),
    }  # fmt: skip
    for label, e in by_effect.items():
        assert reactions.effect_did(e["request"], here) == label, label
    assert reactions.effect_did(send("-1001", files=["/w/reply.ogg"])["request"], here) is None
    assert reactions.effect_did(send("-1009")["request"], here) == "send"
    assert reactions.effect_did(send("-1001", files=["/w/photo.png"])["request"], here) == "send"
    by_tool = {
        ("WebFetch", "{}"): "web_search", ("mcp__byob__browser_read", "{}"): "web_search",
        ("Agent", '{"subagent_type": "Explore"}'): "lookup", ("Glob", "{}"): "lookup",
        ("Skill", '{"skill": "yudame:present"}'): "present", ("Skill", '{"skill": "yudame:calendar"}'): "schedule",
        ("Skill", '{"skill": "yudame-dev:do-issue"}'): "issue", ("CronCreate", "{}"): "schedule",
        ("Edit", '{"file_path": "/w/app.py"}'): "code", ("Write", '{"file_path": "/w/plan.md"}'): "document",
        ("Bash", '{"command": "gh issue create -t x"}'): "issue", ("Bash", '{"command": "git commit -m x"}'): "code",
        ("Bash", '{"command": "valor-telegram read --search x"}'): "lookup",
        ("Bash", '{"command": "gws calendar events insert --json {}"}'): "schedule",
        ("Bash", '{"command": "cat x | grep y"}'): "lookup",
        ("Bash", '{"command": "ls"}'): None, ("Read", '{"file_path": "/w/a.py"}'): None,
        ("Write", '{"file_path": "/w/repo/.valor/effects/a.json"}'): None,
    }  # fmt: skip
    for (name, given), label in by_tool.items():
        assert reactions.tool_did(name, json.loads(given)) == label, (name, given)


def test_stop_in_toms_dm_gets_ok_hand_at_once(dsn, dm):
    async def go():
        await tom_says(dsn, dm, "hello", at(10))
        stop = await tom_says(dsn, dm, "stop", at(20))
        return stop, await requested(dsn, stop["received_id"])

    stop, rows = run(go())
    assert stop["as"] == "stop"
    assert [(r["emoji"], r["label"], r["stage"], r["reason"]) for r in rows] == [
        ("ok_hand", "stop", "shape", "stop")
    ]


def test_eyes_go_out_first_even_when_the_activity_is_already_requested(dsn, team):
    """Both due: 👀 is yielded before its activity, never skipped; only a 👀
    whose activity is already placed is closed `superseded` (a flood wait's
    retry), so it never lands over it."""

    async def go():
        work = await said(dsn, "file an issue for the 500")
        await worked(dsn, work["task_id"], tools=[("Bash", {"command": "gh issue create -t 500"})])
        async with bridges.outbox(dsn, bridges.FakeBridge("telegram")) as box:
            due = [d for d in await box._reactions() if d.received_id == work["received_id"]]
            await box.reacted(due[1])
            again = [d for d in await box._reactions() if d.received_id == work["received_id"]]
        return (
            work,
            due,
            again,
            await bridges.of_type(dsn, "reaction.placed", received_id=work["received_id"]),
        )

    _, due, again, placed = run(go())
    assert [(d.emoji, d.label, d.stage) for d in due] == [
        (EYES, "seen", "seen"),
        ("\U0001f3ab", "issue", "shape"),
    ]
    assert again == [] and [p.get("superseded") for p in placed] == [None, True]


def test_a_rows_wake_yields_again_before_the_bridges_tick(dsn):
    """On a row's wake the outbox yields what is due before it reconciles
    and runs the bridge's tick (the gap fill), so a 👀 never waits on them;
    on the tick's wake it does not."""

    async def go(woke):
        fake = bridges.FakeBridge("telegram")
        async with bridges.outbox(dsn, fake) as box:
            calls = []

            async def due():
                calls.append(fake.ticks)
                return [] if len(calls) == 1 else [f"due after {fake.ticks} ticks"]

            async def wait():
                return woke

            async def reconcile():
                return []

            box.due, box.wait, box.reconcile = due, wait, reconcile
            return await anext(box.__aiter__())

    assert run(go(True)) == "due after 0 ticks"
    assert run(go(False)) == "due after 1 ticks"


# -- the senders ----------------------------------------------------------------


def item(emoji, label, stage="seen", chat="T0ABC/C0DEF"):
    return bridge.ReactionDue("r1", "t1", "m1", chat, "1.2", emoji, stage, "now", label)


class Box:
    def __init__(self):
        self.results = []

    async def reacted(self, item, error=None):
        self.results.append((item.reaction_id, error))


class SlackWire:
    team_id = "T0ABC"

    def __init__(self):
        self.calls = []

    async def react(self, channel, ts, name):
        self.calls.append(("add", name))

    async def unreact(self, channel, ts, name):
        self.calls.append(("remove", name))


def slack_sender():
    from bridges.slack.send import Sender

    kernel = SimpleNamespace(split_text=lambda t: [t], Failed=RuntimeError)
    return Sender(SlackWire(), kernel)


def test_slack_adds_the_rows_name_removes_eyes_and_places_a_label_less_row_as_eyes():
    async def go():
        s, box = slack_sender(), Box()
        await s.react(item("mag", "web_search", "shape"), box)
        await s.react(item(EYES, None), box)  # a row written before rows named their emoji
        await s.react(item("⚡", None, "shape"), box)
        return s.wire.calls, box.results

    calls, results = run(go())
    assert calls == [("add", "mag"), ("remove", "eyes"), ("add", "eyes"), ("add", "eyes")]
    assert [e for _, e in results] == [None, None, None]


class Client:
    """Telethon's client, as far as `TelethonWire.react` uses it."""

    def __init__(self):
        self.sent = []

    def is_connected(self):
        return True

    async def get_input_entity(self, chat):
        return chat

    async def __call__(self, request):
        from telethon.tl.functions.messages import (
            GetAvailableReactionsRequest,
            SearchCustomEmojiRequest,
            SendReactionRequest,
        )

        if isinstance(request, GetAvailableReactionsRequest):
            listed = [
                SimpleNamespace(reaction=EYES, inactive=None),
                SimpleNamespace(reaction="\U0001f4a9", inactive=True),
            ]
            return SimpleNamespace(reactions=listed)
        if isinstance(request, SearchCustomEmojiRequest):
            self.sent.append(("search", request.emoticon))
            return SimpleNamespace(
                document_id=[5368324170671202286] if request.emoticon == "\U0001f50d" else []
            )
        assert isinstance(request, SendReactionRequest)
        self.sent.append(("react", request.reaction[0]))


def test_telegram_sends_a_listed_emoji_as_itself_and_another_as_premium_custom_emoji(tmp_path):
    from telethon.tl.types import ReactionCustomEmoji, ReactionEmoji

    from bridges.telegram.wire import TelethonWire

    async def go():
        wire = TelethonWire(tmp_path / "s", 1, "h")
        wire.client = Client()
        for emoji in (EYES, "\U0001f50d", "\U0001f50d", "\U0001f4a9"):
            await wire.react(1, 2, emoji)
        return wire.client.sent

    sent = run(go())
    assert sent == [
        ("react", ReactionEmoji(EYES)),
        ("search", "\U0001f50d"),
        ("react", ReactionCustomEmoji(5368324170671202286)),
        ("react", ReactionCustomEmoji(5368324170671202286)),
        ("search", "\U0001f4a9"),
        ("react", ReactionEmoji("\U0001f4a9")),
    ]


# -- what the session reads --------------------------------------------------------


def test_a_session_reads_the_reaction_on_each_message_and_one_its_work_set(dsn, team):
    """The line names the reaction; the activity a turn's work set reaches
    the next turn on the message an earlier turn read, saying what set it;
    a message with no reaction says nothing."""

    async def go():
        first = await said(dsn, "what time is it in Bangkok?")
        task = first["task_id"]
        await place_all(dsn, "telegram")
        read = session.chat_prompt(await bridges.rows(dsn, task))
        await turn(dsn, task)
        second = await said(dsn, "find me the florist's opening hours")
        await worked(dsn, task, [send(test_kernel.GROUP, reply_to=second["message_id"])],
                     [("WebSearch", {"query": "florist"})])  # fmt: skip
        third = await said(dsn, "and book a table")
        return first, second, third, read, session.chat_prompt(await bridges.rows(dsn, task))

    first, second, third, read, later = run(go())
    assert f"(message {first['message_id']}; reacted {EYES} (read))" in read
    assert (
        f"- Your reaction on message {second['message_id']} is now \U0001f50d: your turn did web_search"
        in later
    )
    assert f"(message {third['message_id']}; reacted {EYES} (read))" in later
    assert "what time" not in later


def test_chat_line_names_an_activity_and_says_nothing_without_a_reaction():
    m = {"sent_at": "2026-10-17T13:30:00+00:00", "sender_name": "Tom", "message_id": "9", "text": "go"}
    shown = reactions.shown([
        {"type": "reaction.requested", "payload": {"reaction_id": "a", "received_id": "r", "channel": "slack",
                                                   "emoji": "eyes", "label": "seen", "stage": "seen"}},
        {"type": "reaction.requested", "payload": {"reaction_id": "b", "received_id": "r", "channel": "slack",
                                                   "emoji": "ticket", "label": "issue", "stage": "shape",
                                                   "turn_id": "t1"}},
        {"type": "reaction.requested", "payload": {"reaction_id": "c", "received_id": "f", "channel": "slack",
                                                   "emoji": "eyes", "label": "seen", "stage": "seen"}},
        {"type": "reaction.requested", "payload": {"reaction_id": "d", "received_id": "f", "channel": "slack",
                                                   "emoji": "mag", "label": "web_search", "stage": "shape",
                                                   "turn_id": "t2"}},
        {"type": "reaction.failed", "payload": {"reaction_id": "d", "received_id": "f", "error": "x"}},
    ])  # fmt: skip
    assert intake.chat_line(m, shown["r"]) == (
        "[2026-10-17 13:30 UTC] Tom (message 9; reacted :ticket:: your turn did issue): go"
    )
    assert shown["f"] == "reacted :eyes: (read)"
    assert intake.chat_line(m) == "[2026-10-17 13:30 UTC] Tom (message 9): go"


def test_a_new_sessions_history_shows_the_reactions_on_earlier_messages(dsn, team):
    async def go():
        first = await said(dsn, "deploy the menu page")
        await worked(dsn, first["task_id"], tools=[("Edit", {"file_path": "/w/menu.py"})])
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, first["task_id"], reason="a new session")
        second = await said(dsn, "valor?")
        async with await db.connect(dsn) as conn:
            return first, (await tasks.brief(conn, second["task_id"])).instruction

    first, instruction = run(go())
    assert f"(message {first['message_id']}; reacted \U0001f468‍\U0001f4bb: your turn did code)" in instruction


def test_a_working_turn_reads_the_reaction_on_toms_steer():
    steer = {"received_id": "r", "text": "use the blue one", "provenance": {"by": "tom"}}
    shown = {"r": "reacted 👀 (read)"}
    assert (
        session._steering([steer], shown)
        == "Tom wrote while you worked:\n- use the blue one (reacted 👀 (read))"
    )


def test_a_message_that_starts_a_task_gets_eyes_then_the_salute(dsn, tmp_path):
    """With no project to hold a chat session, Tom's message starts a task:
    work is done at once, so 🫡 follows 👀 in the same binding."""

    async def go():
        with bridges.operator(tmp_path):
            inbound = intake.Inbound(
                channel="telegram", chat_id=bridges.OPERATOR, chat_kind="dm", message_id="77",
                sender_id=bridges.OPERATOR, sender_name="Tom", sent_at="2026-10-17T15:00:00Z", text="ship it",
            )  # fmt: skip
            async with await db.connect(dsn) as conn:
                got = await intake.receive(conn, inbound)
                await intake.bind(conn)
            (bound,) = await bridges.of_type(dsn, "message.bound", received_id=got.received_id)
            return bound, await requested(dsn, got.received_id)

    bound, rows = run(go())
    assert bound["as"] == "start"
    assert [(r["emoji"], r["stage"], r.get("reason")) for r in rows] == [
        (EYES, "seen", None),
        ("\U0001fae1", "shape", "a task started"),
    ]


def test_collecting_a_turn_sets_the_activity_its_transcript_shows(dsn, team):
    from core import signals
    from core.machine import State

    async def go():
        m = await said(dsn, "valor, search for a vase supplier")
        task = m["task_id"]
        turn_id = ledger.new_id()
        line = json.dumps({"timestamp": LATER, "message": {"content": [
            {"type": "tool_use", "name": "WebSearch", "input": {"query": "vase"}}]}})  # fmt: skip
        body = {"turn_id": turn_id, "name": "s.jsonl", "offset": 0, "chunk": 0,
                "base64": base64.b64encode(line.encode()).decode()}  # fmt: skip
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, task, "turn.started", {"turn_id": turn_id, "state": "clarify"})
            await conn.execute(
                "INSERT INTO documents (kind, id, body) VALUES (%s, %s, %s::jsonb)",
                (transcripts.KIND, f"{turn_id}/s.jsonl/0", json.dumps(body)),
            )
            await ledger.append(conn, task, "turn.ended", {"turn_id": turn_id, "outcome": "done", "result": {},
                                                           "transcript": {"files": [{"name": "s.jsonl"}]}})  # fmt: skip
            b = await tasks.brief(conn, task)
            await session.record(
                conn, task, turn_id, signals.Signals(), state=State.CLARIFY, workspace=None, brief=b
            )
        return await requested(dsn, m["received_id"])

    assert [r["label"] for r in run(go())] == ["seen", "web_search"]
