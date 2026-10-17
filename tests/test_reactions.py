"""Reactions (docs/plans/reactions-activity.md): which messages get one,
the activity that replaces 👀, each platform's emoji, and what a session
reads of the reaction on a message."""

import asyncio
import uuid
from types import SimpleNamespace

from core import bridge, db, intake, judgement, ledger, reactions, session, tasks
from core.judgement_tasks import SHAPE
from tests import bridges, test_kernel
from tests.test_kernel import QUIET_GROUP, at, posted, said, tom_says, turn

# The kernel tests' fixtures: a project's team chats, and Tom's Slack DM.
team, dm = test_kernel.team, test_kernel.dm

run = asyncio.run
EYES = "\U0001f440"


async def requested(dsn, received_id) -> list[dict]:
    return await bridges.of_type(dsn, "reaction.requested", received_id=received_id)


class Port:
    """A judgement port whose one leg picks `label` (None: abstain)."""

    def __init__(self, label):
        self.label = label
        self.asked = []

    async def judge(self, task, inputs, *, task_id, ref, dsn=None):
        self.asked.append(ref["received_id"])
        jid = ledger.new_id()
        payload = {"judgement_id": jid, "site": task.site, "ref": ref}
        if self.label is not None:
            payload["picked"] = {SHAPE.questions[0].id: self.label}
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, task_id, "judgement.answered", payload)
        return judgement.Judgement(jid, task.site, True, {}, {}, (), "primary", "m", 0, (), 0, payload)


async def shape_all(dsn, port, *bound) -> list:
    """The serve job's part for these bindings' messages: each pending one judged."""
    rids = {b["received_id"] for b in bound}
    async with await db.connect(dsn) as conn:
        pending = [seen for seen in await reactions.pending(conn) if seen["received_id"] in rids]
        return [await reactions.shape(port, conn, seen, dsn) for seen in pending]


async def place_all(dsn, channel) -> None:
    """The bridge's part: every due reaction on `channel` placed."""
    async with bridges.outbox(dsn, bridges.FakeBridge(channel)) as box:
        for due in await box._reactions():
            await box.reacted(due)


def test_every_label_has_an_emoji_on_each_platform_and_nothing_else():
    """Each activity, plus `seen` and `stop`, on Telegram and Slack."""
    labels = set(SHAPE.questions[0].labels) - {reactions.REPLY} | {"seen", "stop"}
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
    task's post is marked read and never judged, so no activity follows."""

    async def go():
        top = await tom_says(dsn, dm, "what's on today?", at(10))
        async with await db.connect(dsn) as conn:
            done = await tasks.start(conn, tasks.Brief(instruction="old work", max_effect_class="act"))
        await posted(dsn, done, dm, at(20), "Done.")
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, done, reason="test")
        late = await tom_says(dsn, dm, "one more thing", at(30), reply_to=at(20))
        port = Port("web_search")
        shaped = await shape_all(dsn, port, top, late)
        return (
            top,
            late,
            await requested(dsn, top["received_id"]),
            await requested(dsn, late["received_id"]),
            port,
            shaped,
        )

    top, late, top_rows, late_rows, port, shaped = run(go())
    assert top["as"] == "steer" and [(r["emoji"], r["label"]) for r in top_rows] == [
        ("eyes", "seen"),
        ("mag", "web_search"),
    ]
    assert late["as"] == "none" and [(r["emoji"], r["judged"]) for r in late_rows] == [("eyes", False)]
    assert late["received_id"] not in port.asked and top["received_id"] in port.asked
    assert shaped == ["mag"]


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


def test_the_activity_replaces_eyes_reply_keeps_them_and_stop_is_ok_hand(dsn, team):
    """A pick writes the shape with Telegram's glyph; `reply` writes none
    and is not asked again; an abstain writes none; a stop gets 👌 unjudged."""

    async def go():
        work = await said(dsn, "search for florists open sunday")
        searched = await shape_all(dsn, Port("web_search"), work)
        plain = await said(dsn, "thanks!")
        replied = await shape_all(dsn, Port("reply"), plain)
        again = Port("code")
        await shape_all(dsn, again, plain)
        unsure = await said(dsn, "hmm")
        abstained = await shape_all(dsn, Port(None), unsure)
        return work, searched, plain, replied, again, unsure, abstained

    work, searched, plain, replied, again, unsure, abstained = run(go())
    assert searched == ["\U0001f50d"]
    shapes = [
        r
        for b in (work, plain, unsure)
        for r in run(requested(dsn, b["received_id"]))
        if r["stage"] == "shape"
    ]
    assert [(r["received_id"], r["label"], r["judged"]) for r in shapes] == [
        (work["received_id"], "web_search", True)
    ]
    assert replied == [None] and plain["received_id"] not in again.asked
    assert abstained == [None]


def test_stop_in_toms_dm_gets_ok_hand_at_once(dsn, dm):
    async def go():
        await tom_says(dsn, dm, "hello", at(10))
        stop = await tom_says(dsn, dm, "stop", at(20))
        return stop, await requested(dsn, stop["received_id"])

    stop, rows = run(go())
    assert stop["as"] == "stop"
    assert [(r["emoji"], r["label"], r["stage"], r["judged"]) for r in rows] == [
        ("ok_hand", "stop", "shape", False)
    ]


def test_the_outbox_closes_a_superseded_eyes_and_carries_the_label(dsn, team):
    async def go():
        work = await said(dsn, "file an issue for the 500")
        await shape_all(dsn, Port("issue"), work)
        async with bridges.outbox(dsn, bridges.FakeBridge("telegram")) as box:
            due = [d for d in await box._reactions() if d.received_id == work["received_id"]]
        return work, due, await bridges.of_type(dsn, "reaction.placed", received_id=work["received_id"])

    _, due, placed = run(go())
    assert [(d.emoji, d.label, d.stage) for d in due] == [("\U0001f3ab", "issue", "shape")]
    assert [p.get("superseded") for p in placed] == [True]


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


def test_a_session_reads_the_classifiers_reaction_on_each_message_and_one_set_later(dsn, team):
    """The line names the reaction and that the classifier set it; a reply
    keeps 👀 and says so; an activity set after a turn read its message
    reaches the next turn; a message with no reaction says nothing."""

    async def go():
        first = await said(dsn, "what time is it in Bangkok?")
        task = first["task_id"]
        await place_all(dsn, "telegram")
        await shape_all(dsn, Port("reply"), first)
        read = session.chat_prompt(await bridges.rows(dsn, task))
        await turn(dsn, task)
        second = await said(dsn, "find me the florist's opening hours")
        await place_all(dsn, "telegram")
        await turn(dsn, task)
        await shape_all(dsn, Port("web_search"), second)
        third = await said(dsn, "and book a table")
        return first, second, third, read, session.chat_prompt(await bridges.rows(dsn, task))

    first, second, third, read, later = run(go())
    assert (
        f"(message {first['message_id']}; reacted {EYES} (read); the reaction classifier read it as a plain reply)"
        in read
    )
    assert f"- The reaction classifier set \U0001f50d (web_search) on message {second['message_id']}" in later
    assert f"(message {third['message_id']}; reacted {EYES} (read))" in later
    assert "what time" not in later


def test_chat_line_names_an_activity_and_says_nothing_without_a_reaction():
    m = {"sent_at": "2026-10-17T13:30:00+00:00", "sender_name": "Tom", "message_id": "9", "text": "go"}
    shown = reactions.shown([
        {"type": "reaction.requested", "payload": {"reaction_id": "a", "received_id": "r", "channel": "slack",
                                                   "emoji": "eyes", "label": "seen", "stage": "seen"}},
        {"type": "reaction.requested", "payload": {"reaction_id": "b", "received_id": "r", "channel": "slack",
                                                   "emoji": "ticket", "label": "issue", "stage": "shape",
                                                   "judged": True}},
        {"type": "reaction.requested", "payload": {"reaction_id": "c", "received_id": "f", "channel": "slack",
                                                   "emoji": "eyes", "label": "seen", "stage": "seen"}},
        {"type": "reaction.requested", "payload": {"reaction_id": "d", "received_id": "f", "channel": "slack",
                                                   "emoji": "mag", "label": "web_search", "stage": "shape",
                                                   "judged": True}},
        {"type": "reaction.failed", "payload": {"reaction_id": "d", "received_id": "f", "error": "x"}},
    ])  # fmt: skip
    assert intake.chat_line(m, shown["r"]) == (
        "[2026-10-17 13:30 UTC] Tom (message 9; reacted :ticket:: the reaction classifier read it as issue): go"
    )
    assert shown["f"] == "reacted :eyes: (read)"
    assert intake.chat_line(m) == "[2026-10-17 13:30 UTC] Tom (message 9): go"


def test_a_new_sessions_history_shows_the_reactions_on_earlier_messages(dsn, team):
    async def go():
        first = await said(dsn, "deploy the menu page")
        await shape_all(dsn, Port("code"), first)
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, first["task_id"], reason="a new session")
        second = await said(dsn, "valor?")
        async with await db.connect(dsn) as conn:
            return first, (await tasks.brief(conn, second["task_id"])).instruction

    first, instruction = run(go())
    assert (
        f"(message {first['message_id']}; reacted \U0001f468‍\U0001f4bb: the reaction classifier read it as code)"
        in instruction
    )


def test_a_working_turn_reads_the_reaction_on_toms_steer():
    steer = {"received_id": "r", "text": "use the blue one", "provenance": {"by": "tom"}}
    shown = {"r": "reacted 👀 (read)"}
    assert (
        session._steering([steer], shown)
        == "Tom wrote while you worked:\n- use the blue one (reacted 👀 (read))"
    )
