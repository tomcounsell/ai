"""What reaches Tom about effects that left, on real Postgres: one `report`
notice for each `act` effect that is done, none for what is itself a step
or itself a message to Tom, and none for a replay task.

No model call. Live spend: none.
"""

import asyncio
import subprocess
import uuid

import pytest

from core import broker, db, notices, tasks
from tests import bridges, scripted
from tests.bridges import OPERATOR, OPERATOR_CHAT, OPERATOR_EMAIL, declared, of_type
from tests.performers import OutboxAppend
from tests.scripted import git
from tools.push_branch import PushBranch

pytestmark = pytest.mark.spend(usd=0)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def op(tmp_path):
    with bridges.operator(tmp_path) as s:
        yield s


class Refusing(OutboxAppend):
    """The target says no: the effect fails."""

    async def perform(self, action, key: str) -> dict:
        raise broker.Failed("the target said no")


class Lost(OutboxAppend):
    """The send leaves but its answer is lost: the effect is in flight
    until `lookup` finds it."""

    async def perform(self, action, key: str) -> dict:
        await super().perform(action, key)
        raise broker.Unknown("the answer was lost")


async def reports(dsn, task) -> list[dict]:
    return [
        n for n in await of_type(dsn, "notice.requested") if n["task_id"] == task and n["kind"] == "report"
    ]


def test_a_done_send_reports_once_and_a_failed_one_does_not(dsn, op, tmp_path):
    outbox = tmp_path / "outbox.jsonl"

    async def go():
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="t", max_effect_class="act"))
            sent = await broker.request(
                conn,
                broker.Performers(OutboxAppend(outbox)),
                task,
                broker.Action("outbox_send", "ann", {"text": "hi"}),
            )
            failed = await broker.request(
                conn,
                broker.Performers(Refusing(outbox)),
                task,
                broker.Action("outbox_send", "bob", {"text": "no"}),
            )
            lost = await broker.request(
                conn,
                broker.Performers(Lost(outbox)),
                task,
                broker.Action("outbox_send", "cat", {"text": "late"}),
            )
            unknown_reports = len(await reports(dsn, task))
            settled = await broker.reconcile(conn, broker.Performers(OutboxAppend(outbox)), lost.effect_id)
        return task, sent, failed, lost, unknown_reports, settled

    task, sent, failed, lost, unknown_reports, settled = run(go())
    assert (sent.kind, failed.kind, lost.kind, settled.kind) == ("done", "failed", "unknown", "done")
    assert unknown_reports == 1  # the done send's, not the failed or the unknown one's
    made = run(reports(dsn, task))
    assert sorted(n["about_key"] for n in made) == sorted(f"report:{e.effect_id}" for e in (sent, lost))
    first = next(n for n in made if n["about_key"] == f"report:{sent.effect_id}")
    assert (
        first["chat_id"] == OPERATOR_CHAT
        and "sent outbox_send to ann" in first["text"]
        and "hi" in first["text"]
    )


@pytest.mark.parametrize(
    ("effect", "owed"),
    [
        ({"action_type": "push_branch", "effect_class": "act", "target": "origin", "payload": {}}, False),
        ({"action_type": "workspace_write", "effect_class": "propose", "target": "f", "payload": {}}, False),
        ({"action_type": "local.send_message", "effect_class": "act", "target": "local", "payload": {}}, False),
        ({"action_type": "telegram.send_message", "effect_class": "act", "target": OPERATOR_CHAT,
          "payload": {"text": "x"}}, False),
        ({"action_type": "telegram.send_message", "effect_class": "act", "target": OPERATOR,
          "payload": {"text": "x"}}, False),
        ({"action_type": "telegram.send_message", "effect_class": "act", "target": "-1009",
          "payload": {"text": "x"}}, True),
        ({"action_type": "email.send", "effect_class": "act", "target": OPERATOR_EMAIL,
          "payload": {"to": [OPERATOR_EMAIL.upper()], "cc": [], "subject": "s"}}, False),
        ({"action_type": "email.send", "effect_class": "act", "target": OPERATOR_EMAIL,
          "payload": {"to": [OPERATOR_EMAIL], "cc": ["ann@example.com"], "subject": "s"}}, True),
    ],
)  # fmt: skip
def test_who_gets_a_report(dsn, op, effect, owed):
    async def go():
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="t", max_effect_class="act"))
            made = await notices.report(conn, task, {**effect, "effect_id": uuid.uuid4().hex[:12]}, {})
        return made

    assert (run(go()) is not None) == owed


def test_a_merge_reports_with_a_feedback_line_and_a_replay_merge_does_not(dsn, op):
    merge = {
        "action_type": "merge",
        "effect_class": "act",
        "target": "origin",
        "payload": {"head_sha": "a" * 40, "target_branch": "main", "url": "/repo.git"},
    }

    async def go():
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="t", max_effect_class="act"))
            replay = await tasks.start(
                conn, tasks.Brief(instruction="t", max_effect_class="act", replay=True)
            )
            made = await notices.report(conn, task, {**merge, "effect_id": uuid.uuid4().hex[:12]}, {})
            none = await notices.report(conn, replay, {**merge, "effect_id": uuid.uuid4().hex[:12]}, {})
        (notice,) = await reports(dsn, task)
        return made, none, notice

    made, none, notice = run(go())
    assert made is not None and none is None
    assert f"merged {'a' * 12} into main" in notice["text"]
    assert notice["text"].endswith("Reply to this message to give feedback.\n\n" + notices.tag(made))


def test_an_email_report_names_who_was_on_cc(dsn, op):
    email = {
        "action_type": "email.send",
        "effect_class": "act",
        "target": "ann@example.com",
        "payload": {"to": ["ann@example.com"], "cc": ["bob@example.com"], "subject": "s", "body": "b"},
    }

    async def go():
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="t", max_effect_class="act"))
            await notices.report(conn, task, {**email, "effect_id": uuid.uuid4().hex[:12]}, {})
        (notice,) = await reports(dsn, task)
        return notice

    assert "sent email.send to ann@example.com, bob@example.com:" in run(go())["text"]


def test_an_act_above_the_ceiling_is_refused_with_nothing_performed(dsn, op, tmp_path):
    """A `propose` task's push and its child's send (the child inherits the
    ceiling) are refused at request: no intent, no release, no report, and
    the branch never reaches origin."""
    ws, origin = scripted.workspace(tmp_path)
    head = git(ws, "rev-parse", "HEAD")

    async def go():
        async with await db.connect(dsn) as conn:
            propose = await tasks.start(conn, tasks.Brief(instruction="p", max_effect_class="propose"))
            child = await tasks.start_child(conn, propose, instruction="c")
            pushed = await broker.request(
                conn,
                broker.Performers(PushBranch(ws)),
                propose,
                broker.Action("push_branch", "valor/x", {"head_sha": head}),
            )
            sent = await broker.request(
                conn, declared(), child, broker.Action("telegram.send_message", "-1009", {"text": "x"})
            )
            return propose, child, pushed, sent, await tasks.brief(conn, child)

    propose, child, pushed, sent, brief = run(go())
    assert brief.max_effect_class == "propose"
    for task, outcome in ((propose, pushed), (child, sent)):
        assert outcome.kind == "refused" and outcome.error == "act is above the task's ceiling propose"
        assert not run(of_type(dsn, "effect.intent", effect_id=outcome.effect_id))
        assert not run(of_type(dsn, "release.requested", effect_id=outcome.effect_id))
        assert run(reports(dsn, task)) == []
    unpushed = subprocess.run(
        ["git", "-C", str(origin), "rev-parse", "--verify", "-q", "valor/x"], capture_output=True, check=False
    )
    assert unpushed.returncode != 0
