"""A project working as its own Google account, beside Valor's own
(docs/plans/9f4967d9-google-per-project.md): the spec's `google`, the
`Account` every path comes from, mail to that account's mailbox going to
its project, each mailbox watched apart, and a send leaving from the
mailbox it names. Every project without `google` works as before."""

import asyncio
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from bridges.email import EmailBridge, imap, smtp
from bridges.email.config import Config
from bridges.meet.google import Account
from core import broker, credentials, db, intake, tasks, workspace
from core.settings import settings
from tests import bridges

pytestmark = pytest.mark.spend(usd=0)
run = asyncio.run
OWN = "valor@yuda.me"  # persona/identity.toml's
CLIENT = "valor@client.test"
SPEC = 'name = "{name}"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\n'


@pytest.fixture
def projects(tmp_path):
    """`client` and `client-be` name the client's account; `plain` none;
    `broken` does not load."""
    with bridges.operator(tmp_path, email_address=OWN):
        root = Path(settings.projects_dir)
        (root / "client.toml").write_text(
            SPEC.format(name="client") + f'google = "{CLIENT.upper()}"\nchats = ["slack:T0ABC/C0CLIENT"]\n'
        )
        (root / "client-be.toml").write_text(SPEC.format(name="client-be") + f'google = "{CLIENT}"\n')
        (root / "plain.toml").write_text(SPEC.format(name="plain") + 'chats = ["email:@plain.test"]\n')
        (root / "broken.toml").write_text(SPEC.format(name="broken") + f'google = "{CLIENT}"\nnonsense = 1\n')
        yield root


def test_the_spec_names_one_address_lowercased_and_refuses_anything_else():
    base = {"name": "a", "repo": "unused", "kind": "plain", "suite": "true"}
    assert workspace.Spec.from_dict({**base, "google": " Valor@Client.Test "}).google == CLIENT
    assert workspace.Spec.from_dict(base).google is None
    for bad in ("@client.test", "valor@", "valor", "a@b@c", ["valor@client.test"]):
        with pytest.raises(workspace.Refused, match="google"):
            workspace.Spec.from_dict({**base, "google": bad})


def test_own_keeps_todays_folder_profile_and_item_and_another_has_its_own(projects):
    own, other = Account.own(), Account.of("client")
    assert own.email == OWN and own.is_own and own.env == {}
    assert own.config_dir == str(Path.home() / ".config" / "gws") and own.profile == settings.meet_profile
    assert own.item == "Google valor@yuda.me" and own.sign_in.endswith("meet-sign-in")
    assert other.email == CLIENT and not other.is_own
    assert (
        other.config_dir == str(Path.home() / ".config" / f"gws-{CLIENT}")
        and Path(other.config_dir).is_absolute()
    )
    assert other.env == {"GOOGLE_WORKSPACE_CLI_CONFIG_DIR": other.config_dir}
    assert other.profile == f"{settings.meet_profile}-{CLIENT}" and other.item == f"Google {CLIENT}"
    assert other.sign_in.endswith(f"meet-sign-in --account {CLIENT}")
    # No project, no field, a spec that does not load or is gone: own.
    assert Account.of(None) == Account.of("plain") == Account.of("broken") == Account.of("gone") == own
    assert Account.here() == [own, other]


def test_the_login_is_read_from_the_accounts_own_item(monkeypatch):
    from bridges.meet import google

    asked = []
    monkeypatch.undo()  # the autouse fake of `secret`, not the one under test
    monkeypatch.setattr(credentials, "op", lambda args, vault_env: asked.append(args) or "v")
    assert google.secret("otp", Account(CLIENT)) == "v"
    assert asked == [["item", "get", f"Google {CLIENT}", "--vault", "m-valor", "--otp"]]


# -- mail ------------------------------------------------------------------------------


def test_mail_to_the_clients_mailbox_is_taken_from_anyone_but_googles_notices_and_valor(projects):
    root = f"<CAK+{uuid.uuid4().hex}@mail.gmail.com>"
    assert intake.mailbox_project(CLIENT).name == "client"  # of two sharing it, the one with chats
    assert intake.mailbox_project(OWN) is None and intake.mailbox_project(None) is None
    for who in ("stranger@nowhere.test", bridges.OPERATOR_EMAIL, "jo@plain.test"):
        assert intake.takes_email(who, root, CLIENT)
    for who in (*intake.GOOGLE_NOTICES, OWN, CLIENT):
        assert not intake.takes_email(who, root, CLIENT)
    # Own's mailbox: the sender's owner, as before.
    assert intake.takes_email("jo@plain.test", root, OWN) and not intake.takes_email(
        "stranger@nowhere.test", root, OWN
    )


async def _received(dsn, conn, mailbox: str, sender: str) -> dict:
    got = await intake.receive(conn, intake.Inbound(
        channel="email", chat_id=f"<{uuid.uuid4().hex}@mail.test>", chat_kind="email",
        message_id=f"<{uuid.uuid4().hex}@mail.test>", sender_id=sender, sender_name="Jo",
        sent_at="2026-11-24T08:00:00+00:00", text="Hello\n\nA question.", headers={"mailbox": mailbox, "to": [mailbox]},
    ))  # fmt: skip
    await intake.bind(conn)
    (bound,) = await bridges.of_type(dsn, "message.bound", received_id=got.received_id)
    return bound


def test_mail_to_the_clients_mailbox_opens_the_clients_email_session(dsn, projects):
    async def go():
        async with await db.connect(dsn) as conn:
            client = await _received(dsn, conn, CLIENT, "stranger@nowhere.test")
            plain = await _received(dsn, conn, OWN, "jo@plain.test")
            stray = await _received(dsn, conn, OWN, "stranger@nowhere.test")
            return [(await tasks.brief(conn, b["task_id"])).project["name"] if b.get("task_id") else None
                    for b in (client, plain, stray)]  # fmt: skip

    assert run(go()) == ["client", "plain", None]


def test_poll_searches_each_mailbox_from_its_own_day(projects, monkeypatch):
    days = []
    monkeypatch.setattr(imap, "select_inbox", lambda conn: "7")
    monkeypatch.setattr(imap, "search", lambda conn, since: days.append(since) or [])
    monkeypatch.setattr(imap, "headers", lambda conn, uids: {})
    run(imap.poll(SimpleNamespace(since=date(2026, 11, 24), address=CLIENT), None, None))
    assert days == [date(2026, 11, 24)]


def test_another_mailbox_is_read_from_its_own_keys_with_its_own_day(projects, tmp_path):
    keys = tmp_path / "mail-keys"
    with bridges.configure(pg_passfile=str(tmp_path / "pgpass")):
        with pytest.raises(credentials.MissingKey, match=f"IMAP_USER.{CLIENT}"):
            Config.for_account(CLIENT)
        keys.write_text("".join(f"{n}.{CLIENT}={v}\n" for n, v in (
            ("IMAP_USER", CLIENT), ("SMTP_USER", CLIENT), ("IMAP_PASSWORD", "p1"), ("SMTP_PASSWORD", "p2"),
            ("EMAIL_SINCE", "2026-11-24"))))  # fmt: skip
        cfg = Config.for_account(CLIENT)
    assert (cfg.address, cfg.since, cfg.imap_user, cfg.smtp_password) == (
        CLIENT,
        date(2026, 11, 24),
        CLIENT,
        "p2",
    )
    assert "p1" not in repr(cfg)


def mailbox(address: str) -> Config:
    return Config(address=address, since=date(2026, 11, 24), imap_host="", imap_port=0, smtp_host="", smtp_port=0,
                  imap_user=address, smtp_user=address, imap_password="", smtp_password="")  # fmt: skip


def test_a_send_leaves_from_the_mailbox_it_names_and_one_not_here_fails(projects):
    bridge = EmailBridge(mailbox(OWN))
    bridge.cfgs[CLIENT] = mailbox(CLIENT)

    def action(**payload):
        return broker.Action("email.send", "a@b.test", {"to": ["a@b.test"], **payload})

    assert bridge._from(action()).address == OWN
    assert bridge._from(action(**{"from": CLIENT.upper()})).address == CLIENT
    with pytest.raises(smtp.SendRefused, match="no mailbox for valor@gone.test"):
        run(bridge.perform(action(**{"from": "valor@gone.test"}), "k"))
    with pytest.raises(broker.Unknown, match="no mailbox for valor@gone.test"):
        run(bridge.lookup(action(**{"from": "valor@gone.test"}), "k", "2026-11-24T00:00:00+00:00"))


def test_each_mailbox_has_its_own_watch_and_a_tick_wakes_each(projects, monkeypatch):
    bridge = EmailBridge(mailbox(OWN), dsn="unused")
    bridge.cfgs[CLIENT] = mailbox(CLIENT)
    watched = {}

    async def watch(cfg, db_, retry, connect, changed):
        watched[cfg.address] = retry
        await retry.wait()

    async def network_changes(changed):
        await asyncio.Event().wait()

    monkeypatch.setattr(imap, "watch", watch)
    monkeypatch.setattr(imap, "network_changes", network_changes)

    async def go():
        task = asyncio.create_task(bridge.watches())
        while len(watched) < 2:
            await asyncio.sleep(0)
        assert watched[OWN] is not watched[CLIENT]
        await bridge.tick()
        assert all(r.is_set() for r in watched.values())
        await task

    run(go())
    assert set(watched) == {OWN, CLIENT}


def test_without_another_accounts_keys_only_own_is_watched(projects, tmp_path, monkeypatch):
    with bridges.configure(pg_passfile=str(tmp_path / "pgpass")):
        monkeypatch.setattr(Config, "from_settings", classmethod(lambda cls: mailbox(OWN)))
        assert set(EmailBridge().cfgs) == {OWN}


def test_the_turn_env_names_the_projects_gws_folder_and_nothing_for_own(projects):
    from core.__main__ import _client_env
    from core.tasks import Brief

    assert _client_env(Brief(instruction="x", project={"name": "client"})) == {
        "GOOGLE_WORKSPACE_CLI_CONFIG_DIR": str(Path.home() / ".config" / f"gws-{CLIENT}")
    }
    for name in ("plain", "broken", "gone"):
        assert _client_env(Brief(instruction="x", project={"name": name})) == {}


def test_the_brief_says_which_account_gws_is_and_how_to_reach_own(dsn, projects):
    async def go(project):
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="x", project={"name": project}))
            return (await tasks.dispatch(conn, task))["text"]

    client, plain = run(go("client")), run(go("plain"))
    assert f"Google: gws is {CLIENT} here" in client
    assert f"GOOGLE_WORKSPACE_CLI_CONFIG_DIR={Path.home() / '.config' / 'gws'} gws" in client
    assert "every email.send leaves from it" in client
    assert "Google:" not in plain
