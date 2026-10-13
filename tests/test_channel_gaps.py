"""Behaviors of the channel table and the Slack binding that the Slack
suite leaves open: local notices, Telegram topics, thread isolation."""

import dataclasses
import uuid

import pytest

from core import db, ledger, tasks
from tests import bridges
from tests.bridges import OPERATOR, OPERATOR_CHAT, new_task
from tests.test_intake import (
    TEAM,
    TOM_SLACK,
    binding_notices,
    msg,
    run,
    say,
    slack,
    slack_op,  # noqa: F401
    slack_sent,
)

pytestmark = pytest.mark.spend(usd=0)


@pytest.fixture
def op(tmp_path):
    with bridges.operator(tmp_path) as s:
        yield s


def test_a_binding_notice_about_a_local_message_goes_in_reply_in_the_local_chat(dsn, op):
    async def go():
        task = await new_task(dsn)
        target = uuid.uuid4().hex
        entry = {"channel": "local", "chat_id": "local", "message_id": target}
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, task, "notice.sent", {"notice_id": uuid.uuid4().hex, "sent": [entry]})
            await tasks.stop(conn, task, reason="test")
        bound = await say(dsn, msg("more", reply_to=target, channel="local", sender="local", chat="local"))
        return bound, await binding_notices(dsn, bound["received_id"])

    with bridges.configure(operator_channel="local", operator_chat=None):
        bound, (n,) = run(go())
    assert bound["as"] == "none"
    assert (n["channel"], n["chat_id"]) == ("local", "local") and n["reply_to"]


def test_a_telegram_topic_reply_binds_the_post_it_names_not_the_newest_in_the_topic(dsn, op):
    async def go():
        a, b = await new_task(dsn), await new_task(dsn)
        async with await db.connect(dsn) as conn:
            for task, mid in ((a, "100"), (b, "200")):
                entry = {"channel": "telegram", "chat_id": OPERATOR_CHAT, "message_id": mid}
                await ledger.append(
                    conn, task, "notice.sent", {"notice_id": uuid.uuid4().hex, "sent": [entry]}
                )
        inbound = dataclasses.replace(msg("hm", reply_to="100"), message_id="300", topic_id="100")
        return a, b, await say(dsn, inbound)

    a, b, bound = run(go())
    assert bound["task_id"] == a


def test_a_slack_thread_binds_only_posts_in_its_own_chat(dsn, slack_op):  # noqa: F811
    async def go():
        mine, other = await new_task(dsn), await new_task(dsn)
        root = "1760000100.000001"
        await slack_sent(dsn, mine, TOM_SLACK, "1760000100.000010", root)
        await slack_sent(dsn, other, f"{TEAM}/C0ELSE", "1760000100.000020", root)
        return mine, await say(dsn, slack("again", "1760000100.000030", root=root))

    mine, bound = run(go())
    assert (bound["task_id"], bound["as"]) == (mine, "steer")


def test_a_slack_reply_with_no_thread_post_falls_back_to_the_post_it_names(dsn, slack_op):  # noqa: F811
    async def go():
        task = await new_task(dsn)
        await slack_sent(dsn, task, TOM_SLACK, "1760000200.000010")
        # the reply names the post, with a topic whose thread has no post of Valor's
        inbound = dataclasses.replace(
            slack("ok", "1760000200.000020", root="1760000200.000010"), topic_id="1760000200.000001"
        )
        return task, await say(dsn, inbound)

    task, bound = run(go())
    assert bound["task_id"] == task


def test_a_slack_message_before_every_post_in_its_thread_binds_nothing_from_them(dsn, slack_op):  # noqa: F811
    async def go():
        task = await new_task(dsn)
        root = "1760000300.000001"
        await slack_sent(dsn, task, TOM_SLACK, "1760000300.000090", root)
        return await say(dsn, slack("early", "1760000300.000050", root=root))

    bound = run(go())
    assert bound["task_id"] is None or bound["as"] == "start"


_ = (OPERATOR,)
