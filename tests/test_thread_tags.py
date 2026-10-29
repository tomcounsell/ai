"""Thread tags: where each Slack or Telegram message a chat session reads
sits in its chat, and where each of its sends went (docs/plans/thread-tags.md)."""

import asyncio
import uuid

from core import db, intake, ledger, session
from tests import bridges
from tests.test_kernel import GROUP, team  # noqa: F401 - the fixture

CHAT = "T1/C1"
LINK = "<https://github.com/example/app/issues/3654>"


def slack(ts, text, *, root=None, root_text=None, chat=CHAT):
    m = {"channel": "slack", "chat_id": chat, "message_id": ts, "sender_name": "Robin",
         "sent_at": "2026-10-29T07:41:00+00:00", "text": text, "reply_to": root, "topic_id": root,
         "thread": [{"id": root, "text": root_text}] if root else []}  # fmt: skip
    return m


def tag(m):
    return intake.thread_tag(m)


def test_a_slack_threads_first_message_and_its_replies_share_one_tag():
    first = slack("1.0", "<@U1|valor> what about the #3654 self-wake?")
    replies = [
        slack(ts, words, root="1.0", root_text=first["text"]) for ts, words in (("1.1", "yes"), ("1.2", "go"))
    ]
    assert tag(first) == "top-level, starts #self-wake-3654"
    assert {tag(r) for r in replies} == {"in #self-wake-3654"}
    assert intake.chat_line(replies[0]).endswith("(message 1.1, replying to 1.0, in #self-wake-3654): yes")


def test_a_top_level_message_and_a_dm_message_read_top_level():
    assert tag(slack("2.0", "ok")) == "top-level"
    dm = {**slack("2.1", "you're double asking me here and in the thread"), "chat_id": "T1/U9"}
    assert tag(dm) == "top-level, starts #double-asking-thread"


def test_two_threads_in_one_channel_are_told_apart_even_with_similar_first_messages():
    plan, asks = "the self-wake plan", "the self-wake questions"
    a, b = slack("3.1", "ok", root="3.0", root_text=plan), slack("4.1", "ok", root="4.0", root_text=asks)
    assert (tag(a), tag(b)) == ("in #self-wake-plan", "in #self-wake-questions")


def test_first_messages_alike_in_their_first_words_share_a_tag_and_differ_by_their_first_message():
    a = slack("5.1", "ok", root="5.0", root_text="deploy the menu page today")
    b = slack("6.1", "ok", root="6.0", root_text="deploy the menu page tomorrow")
    assert tag(a) == tag(b) == "in #deploy-menu-page"
    assert "replying to 5.0" in intake.chat_line(a) and "replying to 6.0" in intake.chat_line(b)
    chain = {
        "channel": "telegram",
        "reply_to": "12",
        "thread": [{"id": "10", "text": "deploy the menu page"}],
    }
    assert tag(chain) == "in #deploy-menu-page, from 10"


def test_a_link_only_first_message_reads_as_the_issue_and_words_come_before_a_link():
    assert tag(slack("7.0", LINK)) == "top-level, starts #issue-3654"
    assert tag(slack("7.1", "https://github.com/example/app/pull/12")) == "top-level, starts #pr-12"
    assert intake.slug(f"see <https://example.com/x|the menu draft> {LINK}") == "see-menu-draft-3654"


def test_a_reply_whose_first_message_text_is_not_recorded_keeps_only_replying_to():
    m = {**slack("8.1", "yes", root="8.0", root_text=""), "thread": []}
    assert tag(m) is None
    assert intake.chat_line(m).endswith("(message 8.1, replying to 8.0): yes")


def test_telegram_chains_topics_and_plain_messages():
    first = {
        "channel": "telegram",
        "message_id": "10",
        "text": "is the florist open on sunday",
        "reply_to": None,
    }
    reply = {
        "channel": "telegram",
        "reply_to": "11",
        "thread": [{"id": "10", "text": first["text"]}, {"id": "11"}],
    }
    assert tag(first) == "top-level, starts #florist-open-sunday"
    assert tag(reply) == "in #florist-open-sunday, from 10"
    plain = {"channel": "telegram", "topic_id": "40", "reply_to": None, "text": "a new idea"}
    inside = {**plain, "reply_to": "41", "thread": [{"id": "41", "text": "a new idea"}]}
    assert tag(plain) == tag(inside) == "in #topic-40"


def test_a_telegram_chain_whose_first_message_was_deleted_starts_at_the_oldest_left():
    # The chain walk stops at a deleted message, so the tag is the oldest
    # message the chain still reaches.
    m = {"channel": "telegram", "reply_to": "22", "thread": [{"id": "22", "text": "menu photos"}]}
    assert tag(m) == "in #menu-photos, from 22"


def test_email_local_and_reaction_lines_are_unchanged():
    email = {
        "channel": "email",
        "message_id": "<a@x>",
        "text": "hello there",
        "sent_at": "2026-10-29T07:41:00+00:00",
    }
    assert tag(email) is None and tag({"channel": "local", "text": "hi"}) is None
    assert intake.chat_line(email).endswith("(message <a@x>): hello there")
    reaction = {**slack("9.0", ":+1:"), "kind": "reaction", "reply_to": "8.0"}
    assert intake.chat_line(reaction).endswith("Robin reacted :+1: to your post 8.0")


def received(text, *, reply_to=None, thread=(), topic_id=None):
    mid = str(uuid.uuid4().int % 10**9)
    return mid, intake.Inbound(
        channel="telegram", chat_id=GROUP, chat_kind="group", message_id=mid, sender_id="77",
        sender_name="Robin", sent_at="2026-10-29T07:41:00+00:00", text=text, reply_to=reply_to,
        thread=list(thread), topic_id=topic_id,
    )  # fmt: skip


def test_a_session_reads_two_live_threads_by_their_tags_and_each_send_by_where_it_went(dsn, team):  # noqa: F811
    async def go():
        a, first_a = received("the self-wake plan")
        b, first_b = received("the self-wake questions")
        r, reply_a = received("sounds good", reply_to=a, thread=[{"id": a, "text": "the self-wake plan"}])
        async with await db.connect(dsn) as conn:
            got = [await intake.receive(conn, m) for m in (first_a, first_b, reply_a)]
            await intake.bind(conn)
            task = (await bridges.of_type(dsn, "message.bound", received_id=got[0].received_id))[0]["task_id"]
            rows = await ledger.read(conn, task)
            sends = [("into-a.json", r), ("into-b.json", b), ("old.json", "999"), ("post.json", None)]
            effects = [
                {"file": f, "effect_id": f"e{i}", "kind": "done", "error": None,
                 "request": {"action_type": "telegram.send_message", "target": GROUP,
                             "payload": {"text": "the self-wake timer", "reply_to": to}}}
                for i, (f, to) in enumerate(sends)
            ]  # fmt: skip
            effects += [
                {"file": "elsewhere.json", "effect_id": "e8", "kind": "done", "error": None,
                 "request": {"action_type": "telegram.send_message", "target": "-1009",
                             "payload": {"text": "x", "reply_to": r}}},
                {"file": "push.json", "effect_id": "e9", "kind": "done", "error": None,
                 "request": {"action_type": "push_branch", "target": "b", "payload": {"head_sha": "0"}}},
                {"file": "reduced.json", "effect_id": "e10", "kind": "done", "error": "unrecorded request"},
            ]  # fmt: skip
            collected = {"effects": effects}
            places = await session._send_places(
                conn, rows, {"channel": "telegram", "chat_id": GROUP}, collected
            )
            return session.chat_prompt(rows), session._effects_report(collected, {}, places), a, b

    prompt, report, a, b = asyncio.run(go())
    assert f"(message {a}, top-level, starts #self-wake-plan" in prompt
    assert f"(message {b}, top-level, starts #self-wake-questions" in prompt
    assert f"replying to {a}, in #self-wake-plan, from {a}" in prompt
    assert report.splitlines()[1:] == [
        "- into-a.json (effect e0): done, into #self-wake-plan",
        "- into-b.json (effect e1): done, into #self-wake-questions",
        "- old.json (effect e2): done, into the thread of 999",
        "- post.json (effect e3): done, top-level, starts #self-wake-timer",
        "- elsewhere.json (effect e8): done",
        "- push.json (effect e9): done",
        "- reduced.json (effect e10): done",
    ]


def test_a_slack_send_into_a_thread_found_by_its_root_in_the_ledger(dsn):
    async def go():
        async with await db.connect(dsn) as conn:
            await ledger.append(
                conn, "slack", "message.received", {"chat_id": CHAT, "message_id": "1.5", "topic_id": "1.0"}
            )
            rows = [
                {
                    "type": "message.heard",
                    "payload": {"message_id": "1.0", "thread_tag": "top-level, starts #menu-photos"},
                }
            ]
            send = {"file": "s.json", "effect_id": "e", "kind": "done", "error": None,
                    "request": {"action_type": "slack.send_message", "target": CHAT.lower(),
                                "payload": {"text": "here", "reply_to": "1.5"}}}  # fmt: skip
            return await session._send_places(
                conn, rows, {"channel": "slack", "chat_id": CHAT}, {"effects": [send]}
            )

    assert asyncio.run(go()) == {"s.json": "into #menu-photos"}
