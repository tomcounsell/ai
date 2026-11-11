"""A project's `email_cc` (projects/README.md): every `email.send` under the
project copies its contacts, deduplicated and never Valor itself, the
reply to all too; the send's line says who was added; the Brief names
them; a project without one sends what was asked."""

import asyncio

import pytest

from core import broker, db, ledger, mail, session, signals, tasks, workspace
from core.machine import State
from tests import bridges

pytestmark = pytest.mark.spend(usd=0)

OWN = "valor@valor.test"
SPEC = 'name = "{name}"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\n'


class Sends:
    action_type = "email.send"
    effect_class = "act"

    def __init__(self):
        self.sent: list[dict] = []

    async def perform(self, action, key):
        self.sent.append(action.payload)
        return {"message_id": key}

    async def lookup(self, action, key):
        return None


@pytest.fixture
def projects(tmp_path):
    with bridges.operator(tmp_path, email_address=OWN):
        root = tmp_path / "projects"
        (root / "cc.toml").write_text(
            SPEC.format(name="cc") + 'email_cc = ["Lead@Client.test", "boss@home.test", "VALOR@valor.test"]\n'
        )
        (root / "plain.toml").write_text(SPEC.format(name="plain"))
        (root / "broken.toml").write_text(SPEC.format(name="broken") + "nonsense = 1\n")
        yield root


def send(dsn, project, *payloads, received=None, action_type="email.send"):
    """Each payload as one `email.send` request of one turn of a task under
    `project`: what the performer was given and the turn's effect lines."""
    performer = Sends()

    async def go():
        async with await db.connect(dsn) as conn:
            if received:
                await ledger.append(conn, "email", "message.received", received)
            brief = tasks.Brief(instruction="x", project={"name": project} if project else None)
            task = await tasks.start(conn, brief)
            turn = ledger.new_id()
            await ledger.append(conn, task, "turn.started", {"turn_id": turn, "state": "clarify"})
            found = signals.Signals(
                effects=[
                    {
                        "file": f"e{i}.json",
                        "request": {"action_type": action_type, "target": "t", "payload": p},
                    }
                    for i, p in enumerate(payloads)
                ]
            )
            await session.record(
                conn, task, turn, found, state=State.CLARIFY, workspace=None,
                brief=await tasks.brief(conn, task), performers=broker.Performers(performer),
            )  # fmt: skip
            (row,) = await bridges.of_type(dsn, "turn.collected", turn_id=turn)
        return performer.sent, session._effects_report(row, {})

    return asyncio.run(go())


def test_a_send_under_a_project_copies_its_contacts_deduplicated(dsn, projects):
    sent, said = send(
        dsn, "cc",
        {"to": ["someone@client.test"], "cc": ["other@client.test"], "subject": "s", "body": "b"},
        {"to": ["LEAD@client.test"], "cc": ["Boss@Home.test"], "subject": "s", "body": "b"},
        {"to": ["someone@client.test"], "subject": "no cc key", "body": "b"},
    )  # fmt: skip
    assert [p.get("cc") for p in sent] == [
        ["other@client.test", "lead@client.test", "boss@home.test"],
        ["Boss@Home.test"],
        ["lead@client.test", "boss@home.test"],
    ]
    lines = said.splitlines()[1:]
    assert lines[0].endswith("; the project's email_cc added to its cc: lead@client.test, boss@home.test")
    assert "email_cc" not in lines[1]


def test_a_reply_to_all_under_the_project_copies_them_after_the_thread(dsn, projects):
    received = {
        "received_id": "r1", "channel": "email", "chat_id": "<root@client.test>", "message_id": "<m1@client.test>",
        "sender_id": "someone@client.test", "text": "hi", "sent_at": "2026-11-10T08:00:00+00:00",
        "headers": {"to": [OWN], "cc": ["lead@client.test", "third@client.test"], "subject": "Plan"},
    }  # fmt: skip
    (sent,), _ = send(dsn, "cc", {"reply_to": "r1", "body": "yes"}, received=received)
    assert sent["to"] == ["someone@client.test"]
    assert sent["cc"] == ["lead@client.test", "third@client.test", "boss@home.test"]


def test_without_a_list_or_a_project_the_send_is_what_was_asked(dsn, projects):
    asked = {"to": ["someone@client.test"], "cc": [], "subject": "s", "body": "b"}
    for project in ("plain", None):
        sent, said = send(dsn, project, asked)
        assert sent == [asked] and "email_cc" not in said
    _, said = send(dsn, "cc", {"text": "hi"}, action_type="telegram.send_message")
    assert "email_cc" not in said


def test_a_spec_that_does_not_load_sends_unchanged_and_says_why(dsn, projects):
    asked = {"to": ["someone@client.test"], "subject": "s", "body": "b"}
    sent, said = send(dsn, "broken", asked)
    assert sent == [asked]
    assert "; the project's email_cc not added: unknown project spec keys: ['nonsense']" in said


def test_a_malformed_payload_is_left_for_the_bridge_to_refuse():
    for payload in ({"to": "someone@client.test"}, {"to": ["a@b.test"], "cc": "c@d.test"}, {"to": [1]}):
        assert mail.copied(payload, ("lead@client.test",), OWN) == (payload, [])


def test_the_spec_loads_the_list_lowercased_and_refuses_a_non_address(projects):
    assert workspace.Spec.load("cc").email_cc == ("lead@client.test", "boss@home.test", "valor@valor.test")
    assert workspace.Spec.load("plain").email_cc == ()
    for bad in ('["not an address"]', '"lead@client.test"', "[1]"):
        with pytest.raises(workspace.Refused, match="email_cc"):
            workspace.Spec.from_dict({"name": "x", "repo": "r", "kind": "plain", "suite": "true",
                                      "email_cc": __import__("tomllib").loads(f"v = {bad}")["v"]})  # fmt: skip


def test_the_brief_names_the_list_for_a_project_with_one(dsn, projects):
    async def go(project):
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="x", project={"name": project}))
            return (await tasks.dispatch(conn, task))["text"]

    assert "Email CC: lead@client.test, boss@home.test (" in asyncio.run(go("cc"))
    assert "Email CC:" not in asyncio.run(go("plain"))


def test_the_brief_says_how_to_reach_the_clients_own_vaults(dsn, projects, tmp_path):
    """A project whose client has `op-token.<client>` is told in its Brief
    which variable signs `op` in to the client's vaults; others are not."""
    (projects / "own.toml").write_text(SPEC.format(name="own") + 'client = "acme"\n')
    (tmp_path / "keys").mkdir()
    (tmp_path / "keys" / "op-token.acme").write_text("acme-op\n")

    async def go(project):
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="x", project={"name": project}))
            return (await tasks.dispatch(conn, task))["text"]

    with bridges.configure(pg_passfile=str(tmp_path / "keys" / "pgpass")):
        own, plain = asyncio.run(go("own")), asyncio.run(go("plain"))
    assert 'run it with OP_SERVICE_ACCOUNT_TOKEN="$OP_SERVICE_ACCOUNT_TOKEN_ACME"' in own
    assert "acme-op" not in own and "1Password:" not in plain
