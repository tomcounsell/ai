"""Anyone's Slack DM, by workspace (Tom, 2026-11-03): a spec's
`slack:<team>/*` takes every DM in that workspace that no spec lists by
id, on the Mac the spec is placed on. Made-up teams and people."""

from __future__ import annotations

import asyncio
import pathlib
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from bridges.slack import inbound as slack_inbound
from bridges.slack.bridge import SlackBridge
from core import db, intake, ledger, tasks, workspace
from tests import bridges
from tests.test_slack_workspaces import Wire, connect_all
from tests.test_slack_workspaces import kernel as fake_kernel

TA, TB = "T0AAA", "T0BBB"
TOM_A, TOM_B = f"{TA}/U0TOM", f"{TB}/U0TOMB"
KIM_A, LEE_B = f"{TA}/U0KIM", f"{TB}/W0LEE"
KEPT = f"{TA}/U0KEPT"  # listed by id in a spec on another Mac
BASE = 'repo = "unused"\nkind = "plain"\nsuite = "true"\n'
YU_ROOM = "A 1:1 direct message with a teammate in the A workspace; answer every message."
MA_ROOM = "A 1:1 direct message with a teammate in the B workspace."


def run(coro):
    return asyncio.run(coro)


def specs(root: pathlib.Path) -> None:
    root.mkdir(exist_ok=True)
    (root / "valor.toml").write_text(
        f'name = "valor"\n{BASE}machine = "Cowboy"\nchats = ["slack:{TOM_A}", "slack:{TA}/C0GENERAL"]\n'
    )
    (root / "yu.toml").write_text(
        f'name = "yu"\n{BASE}machine = "Pirate"\nchats = ["slack:{TA}/C0YUDEV", "slack:{TA.lower()}/*"]\n'
        f'rooms."slack:{TA}/*" = "{YU_ROOM}"\nchat_roles."slack:{TA}/*" = "teammate"\n'
    )
    (root / "ma.toml").write_text(
        f'name = "ma"\n{BASE}machine = "Captain"\nchats = ["slack:{TB}/C0CRM", "slack:{TB}/*"]\n'
        f'rooms."slack:{TB}/*" = "{MA_ROOM}"\nchat_roles."slack:{TB}/*" = "teammate"\n'
    )
    (root / "other.toml").write_text(f'name = "other"\n{BASE}machine = "Bald"\nchats = ["slack:{KEPT}"]\n')
    (root / "paused.toml.paused").write_text(
        f'name = "paused"\n{BASE}machine = "Bald"\nchats = ["slack:{TA}/U0PAUSED"]\n'
    )
    (root / "broken.toml").write_text(
        f'name = "broken"\n{BASE}machine = "Bald"\nchats = ["slack:{TA}/U0BROKE"]\nnope = 1\n'
    )


@contextmanager
def on(mac: str, root: pathlib.Path, tmp_path: pathlib.Path):
    with bridges.configure(
        projects_dir=str(root), work_dir=str(tmp_path / "work"), machine=mac, default_machine="Cowboy",
        operator_slack_ids=(TOM_A, TOM_B), operator_channel="telegram", operator_chat="1000",
    ):  # fmt: skip
        yield


@pytest.fixture
def root(tmp_path):
    specs(tmp_path / "projects")
    return tmp_path / "projects"


def test_a_workspace_entry_loads_and_takes_only_that_workspaces_dms(root):
    yu = workspace.Spec.load(str(root / "yu.toml"))
    assert f"slack:{TA}/*" in yu.chats and yu.rooms[f"slack:{TA}/*"] == YU_ROOM
    assert yu.takes(f"slack:{KIM_A}") == f"slack:{TA}/*" and yu.takes(f"slack:{TA}/W0X") == f"slack:{TA}/*"
    assert yu.takes(f"slack:{TA}/C0YUDEV") == f"slack:{TA}/C0YUDEV"
    for not_a_dm in (f"slack:{TA}/C0ELSE", f"slack:{TA}/G0MPIM", f"slack:{TB}/U0KIM", "telegram:U0KIM"):
        assert yu.takes(not_a_dm) is None
    with pytest.raises(workspace.Refused):
        workspace.Spec.from_dict({"name": "x", **_base(), "chats": [f"slack:{TA}/*X"]})


def _base():
    return {"repo": "unused", "kind": "plain", "suite": "true"}


def test_each_dm_is_owned_by_one_mac_and_no_listed_dm_is_caught(root, tmp_path):
    owns = {}
    for mac in ("Cowboy", "Pirate", "Captain", "Bald"):
        with on(mac, root, tmp_path):
            owns[mac] = {c for c in (TOM_A, KIM_A, f"{TA}/W0X", KEPT, TOM_B, LEE_B, f"{TA}/C0ELSE", f"{TA}/G0X")
                         if intake.owns("slack", c)}  # fmt: skip
            assert not any(c.endswith("*") for c in intake.owned("slack"))
    assert owns["Cowboy"] == {TOM_A}
    assert owns["Pirate"] == {KIM_A, f"{TA}/W0X"}
    assert owns["Captain"] == {TOM_B, LEE_B}
    assert owns["Bald"] == {KEPT}


def test_a_dm_listed_only_in_a_spec_that_does_not_load_is_caught(root, tmp_path):
    with on("Pirate", root, tmp_path):
        assert intake.owns("slack", f"{TA}/U0PAUSED") and intake.owns("slack", f"{TA}/U0BROKE")


def test_a_caught_dm_has_the_workspace_entrys_room_and_role_and_a_listed_one_keeps_its_own(root, tmp_path):
    with on("Pirate", root, tmp_path):
        assert intake.role("slack", KIM_A) == "teammate" and intake.room("slack", KIM_A) == YU_ROOM
        assert (
            intake.team_spec({"channel": "slack", "chat_id": KIM_A, "chat_kind": "dm", "verified": True}).name
            == "yu"
        )
    with on("Cowboy", root, tmp_path):
        assert intake.role("slack", TOM_A) is None and intake.room("slack", TOM_A) == intake.TOMS_OWN_ROOM


async def dm(dsn, chat, text, ts, *, sender=None, name="Kim", **kw) -> dict:
    inbound = intake.Inbound(
        channel="slack", chat_id=chat, chat_kind="dm", message_id=ts, sender_id=sender or chat,
        sender_name=name, sent_at="2026-11-03T09:30:00Z", text=text, **kw,
    )  # fmt: skip
    async with await db.connect(dsn) as conn:
        got = await intake.receive(conn, inbound)
        await intake.bind(conn)
    (bound,) = await bridges.of_type(dsn, "message.bound", received_id=got.received_id)
    return bound


async def brief(dsn, task_id):
    async with await db.connect(dsn) as conn:
        return await tasks.brief(conn, task_id), await ledger.read(conn, task_id)


def test_every_message_in_a_caught_dm_wakes_its_teammate_session(dsn, root, tmp_path):
    with on("Pirate", root, tmp_path):

        async def go():
            first = await dm(dsn, KIM_A, "hi there", "1793700000.000100")
            second = await dm(dsn, KIM_A, "no mention here either", "1793700000.000200")
            return first, second, await brief(dsn, first["task_id"])

        first, second, (b, _) = run(go())
    assert (first["as"], second["as"]) == ("steer", "steer") and first["task_id"] == second["task_id"]
    assert b.project == {"name": "yu"} and b.chat == {"channel": "slack", "chat_id": KIM_A}
    assert b.instruction.startswith(
        f"You are in a direct message with Kim on Slack (chat id {KIM_A}), not Tom;"
    )


def test_toms_dm_in_a_caught_workspace_is_a_teammate_dm_by_tom_and_his_listed_dm_stays_his_own(
    dsn, root, tmp_path
):
    async def go():
        with on("Captain", root, tmp_path):
            caught = await dm(dsn, TOM_B, "hi", "1793700001.000100", name="Tom")
            b, rows = await brief(dsn, caught["task_id"])
        with on("Cowboy", root, tmp_path):
            own = await dm(dsn, TOM_A, "hi", "1793700001.000200", name="Tom")
            ob, _ = await brief(dsn, own["task_id"])
        return caught, b, rows, own, ob

    caught, b, rows, own, ob = run(go())
    assert caught["as"] == "steer" and b.project == {"name": "ma"}
    assert b.instruction.startswith(f"You are in a direct message with Tom on Slack (chat id {TOM_B});")
    (steered,) = bridges.typed(rows, "message.steered")
    assert steered["provenance"]["by"] == "tom"
    assert own["as"] == "steer" and ob.project == {"name": "valor"} and "Tom's own chat" in ob.instruction


def test_toms_reaction_in_his_caught_dm_steers_its_session_and_starts_nothing(dsn, root, tmp_path):
    with on("Captain", root, tmp_path):

        async def go():
            first = await dm(dsn, TOM_B, "hi", "1793700002.000100", name="Tom")
            react = await dm(dsn, TOM_B, ":+1:", "1793700002.000500/+1", name="Tom",
                             kind="reaction", reply_to="1793700002.000500", topic_id="1793700002.000500")  # fmt: skip
            async with await db.connect(dsn) as conn:
                n = (await (await conn.execute(
                    "SELECT count(*) FROM documents WHERE kind = 'task' AND body->>'instruction' LIKE '%:+1:%'"
                )).fetchone())[0]  # fmt: skip
            return first, react, n

        first, react, n = run(go())
    assert react["task_id"] == first["task_id"] and react["as"] in ("steer", "heard") and n == 0


def test_a_report_to_a_caught_dm_opens_its_session_under_the_workspace_entrys_spec(dsn, root, tmp_path):
    with on("Captain", root, tmp_path):

        async def go():
            async with await db.connect(dsn) as conn:
                task_id = await intake.session_for(conn, "slack", LEE_B)
                return await tasks.brief(conn, task_id)

        b = run(go())
    assert b.project == {"name": "ma"} and b.chat == {"channel": "slack", "chat_id": LEE_B}


def test_slackbot_is_no_person_and_its_dm_is_neither_caught_nor_recorded(root, tmp_path):
    assert not slack_inbound.wanted({"user": "USLACKBOT", "text": "Reminder: stand-up"}, "UVALOR")
    assert slack_inbound.wanted({"user": "U0KIM", "text": "hi"}, "UVALOR")


def test_the_bridge_receives_a_caught_dm_live_and_never_fills_the_entry(root, tmp_path):
    got = []
    with on("Captain", root, tmp_path):
        k = SimpleNamespace(**{**vars(fake_kernel(got=got)), "owns": intake.owns, "owned": intake.owned})
        wa, wb = Wire(TA, "UVALORA"), Wire(TB, "UVALORB")
        b = SlackBridge([wa, wb], k, seen=tmp_path / "seen.json")
        connect_all(b)
        im = {
            "type": "message",
            "channel_type": "im",
            "channel": "D0X",
            "ts": "1793700003.000100",
            "text": "hey",
        }
        run(wb.on_message("E1", {**im, "user": "W0LEE"}))
        run(wa.on_message("E2", {**im, "user": "U0KIM"}))  # no A entry on this Mac
        run(wb.on_message("E3", {**im, "user": "USLACKBOT", "ts": "1793700003.000200"}))
        run(b.tick())
    assert [m.chat_id for m in got] == [LEE_B]
    assert (wb.acked, wa.acked) == (["E1", "E3"], ["E2"])
    assert ("history", "D0X") not in wb.calls and all(c == "connect" or c[1] != "*" for c in wb.calls)
