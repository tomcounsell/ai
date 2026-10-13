"""The Slack bridge as its own process over real Postgres, against the
emulator: receiving, gap fill, sends and their lookup, files, and Tom's
replies binding.

Each test has a workspace of its own (a fresh team id), so the chats it
names are its own in the session-long test database. The bridge runs as
`tests.slack_child` and is killed by its pid; the test's own process holds
the same settings, so it binds what the bridge records.
"""

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

from bridges.slack.inbound import is_dm
from bridges.slack.send import marker
from core import broker, db, intake, ledger, machine, notices
from core.machine import State
from tests import bridges, scripted
from tests.bridges import of_type, rows
from tests.slack_emulator import Emulator
from tests.telegram_port import key_of, outcome, release
from tests.test_pipeline import drive

pytestmark = pytest.mark.spend(usd=0)

SEND = "slack.send_message"
ME = "U0VALOR"
TOM = "U0TOM"
OTHER = "U0OTHER"


def run(coro):
    return asyncio.run(coro)


def wait_for(cond, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "condition did not hold in time"
        time.sleep(0.05)


def send(target: str, text: str = "", **payload) -> broker.Action:
    return broker.Action(SEND, target, {"text": text, "reply_to": None, "files": [], **payload})


@dataclass
class World:
    """One workspace: the operator chat `ops` (channel C0OPS), a client
    channel `client` a project lists, Tom's DM `tom`, and the bridge's
    state directory."""

    emu: Emulator
    dsn: str
    tmp: Path
    team: str

    @property
    def ops(self) -> str:
        return f"{self.team}/C0OPS"

    @property
    def client(self) -> str:
        return f"{self.team}/C0CLIENT"

    @property
    def tom(self) -> str:
        return f"{self.team}/{TOM}"

    @property
    def state(self) -> Path:
        return self.tmp / "state"

    @property
    def mark(self) -> Path:
        return self.tmp / "mark"

    def env(self) -> dict[str, str]:
        return {
            **os.environ,
            "VALOR_MACHINE": "m-test",
            "VALOR_DEFAULT_MACHINE": "m-test",
            "VALOR_OPERATOR_CHANNEL": "slack",
            "VALOR_OPERATOR_CHAT": self.ops,
            "VALOR_OPERATOR_SLACK_IDS": self.tom,
            "VALOR_PROJECTS": str(self.tmp / "projects"),
            "VALOR_INBOUND": str(self.tmp / "inbound"),
            "VALOR_SERVE_TICK_S": "0.2",
        }

    @contextlib.contextmanager
    def bridge(self, *args: str, filled: bool = True):
        """The bridge in a process of its own, killed by its pid when the
        block ends; with `filled`, entered once a gap-fill pass completed."""
        self.mark.unlink(missing_ok=True)
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.slack_child",
                self.emu.url,
                self.dsn,
                str(self.state),
                "--only",
                str(self.tmp / "effects"),
                "--mark",
                str(self.mark),
                *args,
            ],
            env=self.env(),
        )
        try:
            if filled:
                wait_for(lambda: self.mark.exists() or proc.poll() is not None)
                assert proc.poll() is None, "the bridge exited"
            yield proc
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(10)

    def filled_again(self) -> None:
        """Until one more gap-fill pass completes."""
        self.mark.unlink(missing_ok=True)
        wait_for(self.mark.exists)

    def release(self, action: broker.Action, workspace: str | None = None) -> str:
        effect = run(release(self.dsn, action, workspace))
        with open(self.tmp / "effects", "a") as f:
            f.write(effect + "\n")
        return effect

    def outcome(self, effect: str, timeout: float = 20) -> dict:
        wait_for(lambda: run(outcome(self.dsn, effect)) is not None, timeout)
        return run(outcome(self.dsn, effect))

    def records(self, chat: str) -> list[dict]:
        return run(of_type(self.dsn, "message.received", chat_id=chat))

    def ids(self, chat: str) -> list[str]:
        return [r["message_id"] for r in self.records(chat)]

    def seen(self, chat: str) -> dict | None:
        path = self.state / "slack-seen.json"
        return json.loads(path.read_text()).get(chat) if path.exists() else None

    def say(self, chat: str, text: str, user: str = TOM, **kw) -> str:
        """`user` writes in `chat`'s Slack channel; with `live` false Slack
        sends no event (the bridge was down)."""
        where = chat.partition("/")[2]
        return self.emu.inject("D" + where if is_dm(chat) else where, text, user=user, **kw)

    def recorded(self, chat: str, ts: str) -> dict:
        wait_for(lambda: ts in self.ids(chat))
        (got,) = [r for r in self.records(chat) if r["message_id"] == ts]
        return got

    def bind(self, chat: str, ts: str) -> dict:
        got = self.recorded(chat, ts)

        async def go():
            async with await db.connect(self.dsn) as conn:
                await intake.bind(conn)
            (bound,) = await of_type(self.dsn, "message.bound", received_id=got["received_id"])
            return bound

        return run(go())

    def own(self, chat: str) -> list[dict]:
        where = chat.partition("/")[2]
        return self.emu.own("D" + where if is_dm(chat) else where)

    def notice(self, chat: str, text: str, reply_to: str | None = None, task: str | None = None):
        """A notice for a task (fresh unless named) to `chat`, sent by the
        running bridge; (task, its post's ts)."""

        async def go():
            t = task or await bridges.new_task(self.dsn)
            async with await db.connect(self.dsn) as conn:
                nid = await notices.request(
                    conn,
                    t,
                    kind="test",
                    about_key=f"t:{uuid.uuid4().hex}",
                    text=text,
                    reply_to=reply_to,
                    channel="slack",
                    chat_id=chat,
                )
            return t, nid

        t, nid = run(go())
        wait_for(lambda: run(of_type(self.dsn, "notice.sent", notice_id=nid)))
        (sent,) = run(of_type(self.dsn, "notice.sent", notice_id=nid))
        return t, sent["sent"][0]["message_id"]


@pytest.fixture
def w(dsn, tmp_path):
    team = "T0" + uuid.uuid4().hex[:10].upper()
    emu = Emulator().start(team=team, me=ME, channels=["C0OPS", "C0CLIENT", "C0ELSE"])
    world = World(emu, dsn, tmp_path, team)
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "client.toml").write_text(
        f'name = "client"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\nchats = ["slack:{world.client}"]\n'
    )
    world.state.mkdir()
    with (
        bridges.operator(tmp_path),
        bridges.configure(
            operator_channel="slack",
            operator_chat=world.ops,
            operator_slack_ids=(world.tom,),
            inbound_dir=str(tmp_path / "inbound"),
        ),
    ):
        yield world
    emu.stop()


# -- receiving ---------------------------------------------------------------


def test_a_message_is_recorded_once_and_what_is_not_wanted_is_acked(w):
    with w.bridge():
        ts = w.say(w.ops, "hello from Tom")
        got = w.recorded(w.ops, ts)
        w.say(w.ops, "Valor's own post", user=ME)
        w.say(w.ops, "a bot", user="U0BOT", fields={"bot_id": "B1"})
        w.say(w.ops, "an edit", fields={"subtype": "message_changed"})
        w.say("X/C0ELSE", "a channel no project lists")
        w.filled_again()
        wait_for(lambda: all(e["acked"] for e in w.emu.state()["envelopes"].values()))
        w.filled_again()
    assert (got["sender_id"], got["chat_id"], got["verified"]) == (w.tom, w.ops, True)
    assert got["sender_name"] == f"Person {TOM}" and got["chat_kind"] == "group"
    assert w.ids(w.ops) == [ts]
    assert w.records(f"{w.team}/C0ELSE") == []


@pytest.mark.parametrize("field", ["team", "user_team"])
def test_both_copies_of_a_dm_are_one_person_and_one_record(w, field):
    """The live event names its team, the history copy does not: both
    make Tom's id, so the DM is one chat, its message one record, and Tom's
    DM starts work."""
    with w.bridge():
        live = w.say(w.tom, "a DM", live_extra={field: w.team.lower()})
        assert w.bind(w.tom, live)["as"] == "start"
    (w.state / "slack-seen.json").unlink()  # the restart reads the DM again from its lowest ts
    later = w.say(w.tom, "while the bridge was down", live=False)
    with w.bridge():
        w.recorded(w.tom, later)
        w.filled_again()
    got = w.records(w.tom)
    assert [r["message_id"] for r in got] == [live, later]
    assert {(r["sender_id"], r["chat_kind"]) for r in got} == {(w.tom, "dm")}


def test_the_first_pass_records_nothing_and_gap_fill_takes_what_a_kill_missed(w):
    w.say(w.client, "before the bridge ever ran", user=OTHER, live=False)
    with w.bridge():
        assert w.records(w.client) == []
        first = w.seen(w.client)
        assert first is not None and first["inclusive"] is False
    m1 = w.say(w.client, "one", user=OTHER, live=False)
    m2 = w.say(w.client, "two", user=OTHER, live=False)
    m3 = w.say(w.client, "three", user=OTHER, live=False)
    reply = w.say(w.client, "in one's thread", user=OTHER, live=False, thread_ts=m1)
    with w.bridge():
        wait_for(lambda: len(w.ids(w.client)) == 4)
        w.filled_again()
    assert w.ids(w.client) == [m1, m2, m3, reply]
    (r,) = [r for r in w.records(w.client) if r["message_id"] == reply]
    assert (r["topic_id"], r["reply_to"]) == (m1, m1)
    assert [t["id"] for t in r["thread"]] == [m1]
    assert w.seen(w.client) == {"ts": m3, "inclusive": False}


def _sent_notice(w, chat: str, root: str, task: str) -> None:
    """A notice of `task` already sent as `root` in `chat`."""

    async def go():
        async with await db.connect(w.dsn) as conn:
            nid = await notices.request(
                conn, task, kind="test", about_key=f"t:{root}", text="old", channel="slack", chat_id=chat
            )
            await ledger.append(
                conn,
                task,
                "notice.sent",
                {
                    "notice_id": nid,
                    "sent": [{"channel": "slack", "chat_id": chat, "message_id": root, "thread_ts": None}],
                },
            )

    run(go())


def test_a_reply_missed_in_a_three_day_old_notices_thread_binds(w):
    task = run(bridges.new_task(w.dsn))
    root = w.say(w.ops, "a notice from three days ago", user=ME, live=False, ago=3 * 86400)
    _sent_notice(w, w.ops, root, task)
    with w.bridge():
        pass
    reply = w.say(w.ops, "carry on with the second part", live=False, thread_ts=root)
    with w.bridge():
        bound = w.bind(w.ops, reply)
    assert (bound["as"], bound["task_id"]) == ("steer", task)


def test_the_seen_ts_waits_until_every_listed_thread_is_read(w):
    """The budget runs out between the history read and the last thread's
    replies: the chat's seen ts stays, and the next run reads the rest."""
    task = run(bridges.new_task(w.dsn))
    roots = [w.say(w.ops, f"notice {n}", user=ME, live=False, ago=600 - n) for n in range(3)]
    for root in roots:
        _sent_notice(w, w.ops, root, task)
    with w.bridge():
        pass
    before = w.seen(w.ops)
    replies = [w.say(w.ops, f"reply {n}", user=OTHER, live=False, thread_ts=r) for n, r in enumerate(roots)]
    # Two calls a method: the DM's and the channel's history, two threads.
    with w.bridge("--budget", "2/600", filled=False):
        wait_for(lambda: set(replies[:2]) <= set(w.ids(w.ops)))
        time.sleep(1)
        assert replies[2] not in w.ids(w.ops)
        assert w.seen(w.ops) == before
    with w.bridge():
        w.recorded(w.ops, replies[2])
    assert sorted(w.ids(w.ops)) == sorted(replies)
    assert w.seen(w.ops)["ts"] >= before["ts"]


def test_a_receive_running_through_a_pass_holds_the_seen_ts_and_a_kill_loses_nothing(w):
    with w.bridge("--receive", "hold"):
        ts = w.say(w.ops, "held while a pass runs")
        wait_for(Path(f"{w.mark}.paused").exists)
        w.filled_again()
        assert w.seen(w.ops) == {"ts": ts, "inclusive": True}
        assert w.ids(w.ops) == []
    with w.bridge():
        w.recorded(w.ops, ts)
        w.filled_again()
    assert w.ids(w.ops) == [ts]


@pytest.mark.parametrize("first", ["hold", "fail-first"])
def test_a_redelivered_envelope_waits_for_the_first_receive(w, first):
    """Slack redelivers while the first receive runs: one record, acked
    once that commits, or taken by the redelivery when it fails."""
    w.emu.control(redeliver_s=0.3, redeliver_max=20)
    with w.bridge("--receive", first):
        ts = w.say(
            w.ops, "delivered twice", files=[{"name": "a.txt", "data_b64": base64.b64encode(b"a").decode()}]
        )
        wait_for(Path(f"{w.mark}.paused").exists)
        envelope = lambda: next(iter(w.emu.state()["envelopes"].values()))
        wait_for(lambda: envelope()["sent"] >= 3)
        assert not envelope()["acked"]
        Path(f"{w.mark}.go").touch()
        w.recorded(w.ops, ts)
        wait_for(lambda: envelope()["acked"])
        sent = envelope()["sent"]
        time.sleep(1)
        assert envelope()["sent"] == sent
    assert w.ids(w.ops) == [ts]
    # The file is fetched once while the first receive runs; a failed one
    # is fetched again by the redelivery, to the same sha256 name.
    assert list(w.emu.state()["downloads"].values()) == [1 if first == "hold" else 2]
    assert len(list((w.tmp / "inbound" / "slack").iterdir())) == 1


def test_files_are_named_by_sha_and_an_off_host_redirect_never_gets_the_token(w):
    files = [
        {"name": "notes.txt", "data_b64": base64.b64encode(b"hello").decode(), "mime": "text/plain"},
        {"name": "away.bin", "data_b64": base64.b64encode(b"x").decode(), "mode": "redirect"},
        {"name": "page.bin", "data_b64": base64.b64encode(b"y").decode(), "mode": "html"},
    ]
    with w.bridge():
        ts = w.say(w.ops, "files", files=files)
        got = w.recorded(w.ops, ts)
    ok, away, page = got["attachments"]
    assert Path(ok["path"]).name == hashlib.sha256(b"hello").hexdigest()
    assert Path(ok["path"]).read_bytes() == b"hello" and ok["bytes"] == 5
    assert away["skipped"] == "redirected off Slack's hosts" and "path" not in away
    assert page["skipped"] == "not signed in"
    assert w.emu.state()["offhost"] == []


# -- sending -----------------------------------------------------------------


def test_a_send_carries_its_marker_and_a_reply_goes_under_the_threads_root(w):
    with w.bridge():
        root = w.say(w.ops, "a question")
        w.recorded(w.ops, root)
        reply = w.say(w.ops, "more", thread_ts=root)
        w.recorded(w.ops, reply)
        effect = w.release(send(w.ops, "the answer", reply_to=reply))
        out = w.outcome(effect)
    assert out["kind"] == "done"
    [post] = w.own(w.ops)
    assert post["thread_ts"] == root and post["text"] == "the answer"
    key = run(key_of(w.dsn, effect))
    assert post["metadata"]["event_payload"] == {"key": marker(key), "part": 0, "parts": 1}
    assert out["result"]["sent"] == [
        {"channel": "slack", "chat_id": w.ops, "message_id": post["ts"], "thread_ts": root}
    ]


@pytest.mark.parametrize("keep_metadata", [True, False])
def test_killed_after_slack_accepted_the_post_lookup_settles_it_done(w, keep_metadata):
    """With its marker, or without (Slack dropped the metadata): then the
    post is matched by its text, which Slack stored with the URL wrapped
    and `&`, `<`, `>` as entities."""
    text = "see https://example.com/a?b=1&c=2\nand <this> & &amp; that "
    w.emu.control(keep_metadata=keep_metadata)
    with w.bridge() as proc:
        w.emu.control(faults=[{"method": "chat.postMessage", "kind": "accept_then_pause", "seconds": 600}])
        effect = w.release(send(w.ops, text))
        wait_for(lambda: w.own(w.ops))
        proc.kill()
        proc.wait(10)
    assert run(of_type(w.dsn, "effect.intent", effect_id=effect))
    assert run(outcome(w.dsn, effect)) is None
    [post] = w.own(w.ops)
    assert "<https://example.com/a?b=1&amp;c=2>" in post["text"]
    stored = w.emu.call("api/conversations.history", channel="C0OPS", include_all_metadata=True)
    assert ("metadata" in stored["messages"][0]) is keep_metadata
    with w.bridge():
        out = w.outcome(effect)
    assert out["kind"] == "done"
    assert [s["message_id"] for s in out["result"]["sent"]] == [post["ts"]]
    assert len(w.own(w.ops)) == 1


def _long(n: int) -> str:
    words = " ".join(f"word{i:05d}" for i in range(n // 10 + 1))
    return words[:n]


def test_a_long_send_is_three_posts_and_a_kill_after_the_first_finishes_the_rest(w):
    with w.bridge() as proc:
        w.emu.control(faults=[{"method": "chat.postMessage", "kind": "pause", "nth": 2, "seconds": 600}])
        effect = w.release(send(w.ops, _long(9000)))
        wait_for(lambda: len(w.own(w.ops)) == 1)
        proc.kill()
        proc.wait(10)
    with w.bridge():
        out = w.outcome(effect)
    posts = w.own(w.ops)
    assert out["kind"] == "done" and len(posts) == 3
    assert [s["message_id"] for s in out["result"]["sent"]] == [p["ts"] for p in posts]
    assert " ".join(p["text"] for p in posts).split() == _long(9000).split()


def test_a_rate_limit_on_part_two_is_waited_out(w):
    with w.bridge():
        w.emu.control(faults=[{"method": "chat.postMessage", "kind": "429", "nth": 2, "retry_after": 1}])
        out = w.outcome(w.release(send(w.ops, _long(9000))), timeout=30)
    assert out["kind"] == "done" and len(out["result"]["sent"]) == 3 == len(w.own(w.ops))


def test_part_two_refused_settles_done_with_the_part_sent(w):
    with w.bridge():
        w.emu.control(
            faults=[{"method": "chat.postMessage", "kind": "error", "nth": 2, "error": "is_archived"}]
        )
        out = w.outcome(w.release(send(w.ops, _long(5000))))
    [post] = w.own(w.ops)
    assert out["kind"] == "done" and [s["message_id"] for s in out["result"]["sent"]] == [post["ts"]]


@pytest.mark.parametrize(
    ("fault", "error"),
    [
        ({"kind": "429", "retry_after": 30}, "30"),
        ({"kind": "error", "error": "channel_not_found"}, "channel_not_found"),
    ],
)
def test_the_first_post_refused_or_rate_limited_is_failed(w, fault, error):
    with w.bridge():
        w.emu.control(faults=[{"method": "chat.postMessage", **fault}])
        out = w.outcome(w.release(send(w.ops, "not sent")))
    assert out["kind"] == "failed" and error in out["error"]
    assert w.own(w.ops) == []


def test_a_target_in_another_workspace_is_failed(w):
    with w.bridge():
        out = w.outcome(w.release(send("T0ELSEWHERE/C0OPS", "not sent")))
    assert out["kind"] == "failed" and "T0ELSEWHERE is not a connected workspace" in out["error"]
    assert w.own(w.ops) == []


def test_a_post_cut_after_slack_wrote_it_is_unknown_until_lookup_finds_it(w):
    with w.bridge():
        w.emu.control(faults=[{"method": "chat.postMessage", "kind": "accept_then_drop"}])
        out = w.outcome(w.release(send(w.ops, "written, then the line cut")))
    [post] = w.own(w.ops)
    assert out["kind"] == "done" and out["result"]["sent"][0]["message_id"] == post["ts"]


def _file(w, name: str, data: bytes) -> tuple[str, dict]:
    ws = w.tmp / "ws"
    ws.mkdir(exist_ok=True)
    (ws / name).write_bytes(data)
    return str(ws), {"path": str(ws / name), "sha256": hashlib.sha256(data).hexdigest()}


def test_a_files_post_is_the_share_files_info_shows(w):
    w.emu.control(share_mode="delay", share_delay_s=1)
    ws, f = _file(w, "report.txt", b"the report")
    with w.bridge():
        out = w.outcome(w.release(send(w.ops, "", files=[f]), ws))
    [post] = w.own(w.ops)
    assert post["files"][0]["name"] == "report.txt"
    assert out["kind"] == "done" and out["result"]["sent"][0]["message_id"] == post["ts"]


def test_a_share_files_info_never_shows_is_unknown_until_lookup_finds_the_post(w):
    w.emu.control(share_mode="unlisted")
    ws, f = _file(w, "report.txt", b"the report")
    with w.bridge("--share-reads", "2/0.2"):
        out = w.outcome(w.release(send(w.ops, "with a file", files=[f]), ws))
    text, upload = w.own(w.ops)
    assert out["kind"] == "done"
    assert [s["message_id"] for s in out["result"]["sent"]] == [text["ts"], upload["ts"]]


# -- Tom's replies -----------------------------------------------------------


@pytest.mark.macos
def test_an_answer_by_reply_in_the_question_notices_thread(w, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def waiting():
        task = await scripted.start(w.dsn, ws, judge="thin")
        await drive(w.dsn, task)
        assert machine.fold(await rows(w.dsn, task)).state is State.WAITING
        async with await db.connect(w.dsn) as conn:
            made = await notices.owe(conn, task)
        for nid in made:
            (req,) = await of_type(w.dsn, "notice.requested", notice_id=nid)
            if req["kind"] == "question":
                return task, nid
        raise AssertionError("no question notice")

    task, question = run(waiting())
    with w.bridge():
        wait_for(lambda: run(of_type(w.dsn, "notice.sent", notice_id=question)))
        (sent,) = run(of_type(w.dsn, "notice.sent", notice_id=question))
        root = sent["sent"][0]["message_id"]
        bound = w.bind(w.ops, w.say(w.ops, "a short one", thread_ts=root))
    assert (bound["as"], bound["task_id"]) == ("answer", task)
    [answered] = [r for r in run(rows(w.dsn, task)) if r["type"] == "question.answered"]
    assert answered["payload"]["provenance"]["via"] == "slack"


def test_a_reply_binds_to_the_newer_of_two_notices_in_its_thread(w):
    with w.bridge():
        first, root = w.notice(w.ops, "the first notice")
        second, inner = w.notice(w.ops, "the second, in its thread", reply_to=root)
        bound = w.bind(w.ops, w.say(w.ops, "go ahead", thread_ts=root))
    [post] = [p for p in w.own(w.ops) if p["ts"] == inner]
    assert post["thread_ts"] == root and first != second
    assert (bound["as"], bound["task_id"]) == ("steer", second)


def test_reply_sixty_binds_to_the_notice_at_reply_fifty_eight(w):
    root = w.say(w.ops, "a long thread", user=OTHER, live=False, ago=1000)
    for n in range(1, 58):
        w.say(w.ops, f"reply {n}", user=OTHER, live=False, thread_ts=root, ago=1000 - n)
    with w.bridge():
        task, notice = w.notice(w.ops, "a notice at reply 58", reply_to=root)
        w.recorded(w.ops, w.say(w.ops, "reply 59", user=OTHER, thread_ts=root))
        bound = w.bind(w.ops, w.say(w.ops, "reply 60, Tom's", thread_ts=root))
    assert (bound["as"], bound["task_id"]) == ("steer", task)
    (got,) = [r for r in w.records(w.ops) if r["text"] == "reply 60, Tom's"]
    assert len(got["thread"]) == 50 and got["thread"][0]["id"] == root
    assert notice in [t["id"] for t in got["thread"]]


def test_exactly_stop_stops_and_a_near_miss_steers_with_a_notice_in_its_thread(w):
    with w.bridge():
        task, root = w.notice(w.ops, "about to send")
        near = w.bind(w.ops, w.say(w.ops, "Stop.", thread_ts=root))
        assert (near["as"], near["task_id"]) == ("steer", task)
        wait_for(lambda: len(w.own(w.ops)) == 2)
        stop = w.bind(w.ops, w.say(w.ops, " STOP ", thread_ts=root))
    assert (stop["as"], stop["task_id"]) == ("stop", task)
    assert machine.fold(run(rows(w.dsn, task))).state is State.STOPPED
    answer = w.own(w.ops)[1]
    assert answer["thread_ts"] == root and "stop" in answer["text"]


def test_a_binding_notice_about_a_client_channel_goes_to_the_operator_chat(w):
    with w.bridge():
        task, root = w.notice(w.client, "about the client's work")
        near = w.bind(w.client, w.say(w.client, "Stop.", thread_ts=root))
        assert (near["as"], near["task_id"]) == ("steer", task)
        wait_for(lambda: w.own(w.ops))
    [binding] = w.own(w.ops)
    assert "stop" in binding["text"] and "thread_ts" not in binding
    assert [p["ts"] for p in w.own(w.client)] == [root]
