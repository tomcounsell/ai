"""The email bridge on the kernel: the IMAP watch through intake, the bridge
under `bridge.serve`, the reply-all a turn asks for, the request-time size
refusal, and a kill at every point of a send, on real Postgres and the
local mail servers."""

import asyncio
import contextlib
import dataclasses
import hashlib
import imaplib
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import psycopg
import pytest

from bridges.email import EmailBridge, imap, smtp
from bridges.email.stop import Ends
from core import bridge, broker, db, intake, ledger, mail, session, signals, tasks
from tests import scripted
from tests.bridges import OPERATOR_EMAIL, configure, declared, new_task, of_type, operator, outbox
from tests.ports import listen
from tests.test_email_parse import message

pytestmark = pytest.mark.spend(usd=0)

PASS = "mx.google.com; dkim=pass header.i=@example.com; dmarc=pass (p=NONE) header.from=example.com"
FORGED = "attacker.example; dmarc=pass header.from=example.com"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def op(tmp_path):
    with operator(tmp_path, inbound_dir=str(tmp_path / "inbound")) as s:
        yield s


def mid() -> str:
    return f"<{uuid.uuid4().hex}@example.com>"


def from_tom(auth: str | None = PASS, **more) -> bytes:
    headers = {"Authentication_Results": auth} if auth else {}
    return message(
        sender=f"Tom <{OPERATOR_EMAIL}>", message_id=more.pop("message_id", mid()), **headers, **more
    )


async def poll(dsn, cfg) -> int:
    mailbox = await asyncio.to_thread(imap.connect, cfg)
    try:
        async with await db.connect(dsn) as conn:
            return await imap.poll(cfg, mailbox, conn)
    finally:
        await asyncio.to_thread(imap.logout, mailbox)


async def received(dsn, message_id) -> list[dict]:
    return await of_type(dsn, "message.received", message_id=message_id)


def seen(mailbox) -> dict[str, bool]:
    out = {}
    for raw, flags in mailbox.dovecot.messages("INBOX"):
        head = raw.split(b"\r\n\r\n", 1)[0].decode()
        ident = next(
            line.split(":", 1)[1].strip()
            for line in head.splitlines()
            if line.lower().startswith("message-id:")
        )
        out[ident] = "\\Seen" in flags
    return out


def test_the_poll_receives_owned_unseen_mail_once_and_starts_nothing_unverified(dsn, op, mailbox):
    good, forged, plain, stranger = mid(), mid(), mid(), mid()
    mailbox.dovecot.deliver(from_tom(message_id=good, body="please start"))
    mailbox.dovecot.deliver(from_tom(FORGED, message_id=forged, body="me too"))
    mailbox.dovecot.deliver(from_tom(None, message_id=plain, body="and me"))
    mailbox.dovecot.deliver(
        message(sender="someone@else.example", message_id=stranger, Authentication_Results=PASS)
    )
    cfg = mailbox.config()

    async def go():
        first = await poll(dsn, cfg)
        again = await poll(dsn, cfg)
        async with await db.connect(dsn) as conn:
            await intake.bind(conn)
        rows = {m: await received(dsn, m) for m in (good, forged, plain, stranger)}
        bound = {
            m: (await of_type(dsn, "message.bound", received_id=r[0]["received_id"]))[0]
            for m, r in rows.items()
            if r
        }
        return first, again, rows, bound

    first, again, rows, bound = run(go())
    assert first == 3 and again == 0
    assert rows[stranger] == []
    assert [len(rows[m]) for m in (good, forged, plain)] == [1, 1, 1]
    assert [rows[m][0]["verified"] for m in (good, forged, plain)] == [False, False, False]
    assert rows[good][0]["sender_id"] == OPERATOR_EMAIL and rows[good][0]["chat_id"] == good
    assert rows[good][0]["headers"]["uidvalidity"] and rows[good][0]["headers"]["uid"]
    assert [bound[m]["as"] for m in (good, forged, plain)] == ["none", "none", "none"]
    flags = seen(mailbox)
    assert flags[good] and flags[forged] and flags[plain] and not flags[stranger]


def test_a_message_received_twice_is_one_record_and_both_copies_are_seen(dsn, op, mailbox):
    same = mid()
    raw = from_tom(message_id=same, body="once")
    mailbox.dovecot.deliver(raw)
    mailbox.dovecot.deliver(raw)
    count = run(poll(dsn, mailbox.config()))
    assert count == 1 and len(run(received(dsn, same))) == 1
    assert [("\\Seen" in flags) for _, flags in mailbox.dovecot.messages("INBOX")] == [True, True]


def test_a_message_that_fails_stays_unseen_and_later_mail_is_received(dsn, op, mailbox, tmp_path):
    # The inbound directory for email is a file, so persisting an
    # attachment fails; a message with none needs no directory.
    (tmp_path / "inbound").mkdir()
    (tmp_path / "inbound" / "email").write_text("in the way")
    broken, fine = mid(), mid()
    mailbox.dovecot.deliver(from_tom(message_id=broken, files=[("a.txt", b"attached")]))
    mailbox.dovecot.deliver(from_tom(message_id=fine, body="no files"))
    assert run(poll(dsn, mailbox.config())) == 1
    assert run(received(dsn, broken)) == [] and len(run(received(dsn, fine))) == 1
    flags = seen(mailbox)
    assert flags[fine] and not flags[broken]


def test_attachments_are_written_under_the_inbound_directory_by_sha256(dsn, op, mailbox, tmp_path):
    with_file = mid()
    data = b"the attached notes\n"
    mailbox.dovecot.deliver(from_tom(message_id=with_file, files=[("notes.txt", data)]))
    run(poll(dsn, mailbox.config()))
    (row,) = run(received(dsn, with_file))
    (att,) = row["attachments"]
    assert att["name"] == "notes.txt"
    path = tmp_path / "inbound" / "email"
    assert any(p.read_bytes() == data for p in path.rglob("*") if p.is_file())
    assert hashlib.sha256(data).hexdigest() in att["path"]


def test_the_bridge_under_serve_sends_a_release_once_and_receives_mail(dsn, op, mailbox):
    """Mail delivered before the watch starts is received by its first
    search."""
    _send_and_receive(dsn, mailbox, deliver_after=False)


def test_mail_delivered_while_the_bridge_idles_is_received(dsn, op, mailbox):
    """The watch is in IDLE when the mail lands; the server's EXISTS
    wakes it."""
    _send_and_receive(dsn, mailbox, deliver_after=True)


def _send_and_receive(dsn, mailbox, deliver_after: bool):
    to = "tom@yuda.me"
    payload = {"to": [to], "cc": [], "subject": "Re: plans", "body": "Done.", "in_reply_to": None,
               "references": [], "files": []}  # fmt: skip
    incoming = mid()
    raw = from_tom(message_id=incoming, body="while serving")
    if not deliver_after:
        mailbox.dovecot.deliver(raw)
    cfg = mailbox.config()

    async def go():
        task = await new_task(dsn)
        async with await db.connect(dsn) as conn:
            held = await broker.request(conn, declared(), task, broker.Action("email.send", to, payload))
            assert held.kind == "pending", held
            await broker.approve(conn, held.effect_id, note="approve")
            released = await broker.release(conn, declared(), held.effect_id)
            assert released.kind == "released", released
        served = asyncio.create_task(bridge.serve(EmailBridge(cfg, dsn), dsn))
        try:
            if deliver_after:
                assert await asyncio.to_thread(mailbox.imap.idling.wait, 30)
                await asyncio.to_thread(mailbox.dovecot.deliver, raw)
            for _ in range(300):
                done = await of_type(dsn, "effect.outcome", effect_id=held.effect_id)
                got = await received(dsn, incoming)
                if done and got:
                    break
                assert not served.done(), served
                await asyncio.sleep(0.1)
        finally:
            served.cancel()
            with pytest.raises(asyncio.CancelledError):
                await served
        async with await db.connect(dsn) as conn:
            again = await broker.release(conn, declared(), held.effect_id)
        return held.effect_id, done, got, again

    effect_id, done, got, again = run(go())
    (outcome,) = done
    assert outcome["kind"] == "done"
    sent = outcome["result"]["sent"][0]
    assert sent["channel"] == "email" and sent["message_id"] == outcome["result"]["message_id"]
    assert len(mailbox.smtp.accepted) == 1 and len(got) == 1
    assert again.kind == "done"  # the recorded outcome; nothing is sent again
    assert len(run(of_type(dsn, "effect.outcome", effect_id=effect_id))) == 1


def test_a_watch_whose_database_connection_dropped_gets_a_new_one_on_the_next_tick(dsn, op, mailbox):
    before, after = mid(), mid()

    async def watch_backends() -> list[int]:
        async with await db.connect(dsn) as conn:
            rows = await (
                await conn.execute(
                    "SELECT pid FROM pg_stat_activity WHERE datname = current_database() "
                    "AND application_name = 'valor-email-watch'"
                )
            ).fetchall()
            return [r[0] for r in rows]

    async def go():
        email = EmailBridge(mailbox.config(), dsn)
        watching = asyncio.create_task(email.watches())
        try:
            mailbox.dovecot.deliver(from_tom(message_id=before, body="one"))
            for _ in range(300):
                if await received(dsn, before):
                    break
                await asyncio.sleep(0.1)
            assert await received(dsn, before)
            (pid,) = await watch_backends()
            async with await db.connect(dsn) as conn:
                await conn.execute("SELECT pg_terminate_backend(%s)", (pid,))
            mailbox.dovecot.deliver(from_tom(message_id=after, body="two"))
            await asyncio.sleep(1.0)  # the watch fails on it and waits for a tick
            assert not await received(dsn, after) and not watching.done()
            await email.tick()
            for _ in range(300):
                if await received(dsn, after):
                    break
                await asyncio.sleep(0.1)
            return await received(dsn, after), pid, await watch_backends()
        finally:
            watching.cancel()
            with pytest.raises(asyncio.CancelledError):
                await watching

    got, old, now = run(go())
    assert got and len(now) == 1 and now != [old]


def test_a_watch_that_cannot_connect_connects_again_on_the_next_tick(dsn, op, mailbox):
    incoming = mid()
    mailbox.imap.stop()

    async def go():
        retry = asyncio.Event()
        retry.set()  # a tick before the failure; the failed watch clears it and waits for the next
        async with await db.connect(dsn) as conn:
            watching = asyncio.create_task(imap.watch(mailbox.config(), conn, retry))
            try:
                for _ in range(300):
                    if not retry.is_set():
                        break
                    await asyncio.sleep(0.1)
                assert not retry.is_set() and not watching.done()
                mailbox.imap.start()
                mailbox.dovecot.deliver(from_tom(message_id=incoming, body="after the outage"))
                retry.set()
                for _ in range(300):
                    if await received(dsn, incoming):
                        break
                    await asyncio.sleep(0.1)
                return await received(dsn, incoming)
            finally:
                watching.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await watching

    assert len(run(go())) == 1


def _watch_receives_without_a_tick(dsn, mailbox, *, cause, expect_idle_first=True):
    """A watch idles, `cause` kills its path, mail lands, and no tick is
    given: the watch must find the mail on its own."""
    incoming = mid()

    async def go():
        retry = asyncio.Event()
        changed = asyncio.Event()
        async with await db.connect(dsn) as conn:
            watching = asyncio.create_task(imap.watch(mailbox.config(), conn, retry, changed=changed))
            try:
                assert await asyncio.to_thread(mailbox.imap.idling.wait, 30)
                mailbox.imap.idling.clear()
                cause(mailbox, changed)
                mailbox.dovecot.deliver(from_tom(message_id=incoming, body="in the dead window"))
                for _ in range(300):
                    if await received(dsn, incoming):
                        break
                    await asyncio.sleep(0.1)
                return await received(dsn, incoming)
            finally:
                watching.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await watching

    assert len(run(go())) == 1


def test_a_path_that_dies_silently_is_replaced_when_the_network_changes(dsn, op, mailbox):
    """The stand-in stops forwarding with no reset, so the client sees
    nothing; the interface change ends the connection and the new one finds
    the mail."""

    def cause(mailbox, changed):
        mailbox.imap.stall()
        time.sleep(0.2)
        changed.set()

    _watch_receives_without_a_tick(dsn, mailbox, cause=cause)


def test_a_connection_reset_after_idle_began_is_replaced_without_a_tick(dsn, op, mailbox):
    def cause(mailbox, changed):
        mailbox.imap.cut()

    _watch_receives_without_a_tick(dsn, mailbox, cause=cause)


class _Fake:
    """Stands in for the IMAP connection and its steps, so a failure of one
    kind can be played again and again."""

    def __init__(self, monkeypatch, *, fails, entered):
        self.connects = 0
        self.fails, self.entered = fails, entered

        def connect(cfg, ends=None):
            self.connects += 1
            return object()

        async def poll(cfg, conn, db, ends=None):
            return 0

        def idle(conn, on_enter=None):
            if self.entered and on_enter:
                on_enter()
            raise self.fails

        monkeypatch.setattr(imap, "connect", connect)
        monkeypatch.setattr(imap, "poll", poll)
        monkeypatch.setattr(imap, "idle", idle)
        monkeypatch.setattr(imap, "drop", lambda conn: None)

    async def watch(self, seconds=0.5):
        class Db:
            closed = broken = False

        retry = asyncio.Event()
        task = asyncio.create_task(imap.watch(None, Db(), retry))
        await asyncio.sleep(seconds)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return self.connects


def test_a_reconnect_the_network_started_that_fails_before_idle_waits_for_the_tick(monkeypatch):
    """The first connection idles; the network changes; the new connection
    is slow to fail, and more changes arrive while it does. None of them
    starts a third connection."""
    connects = []
    changed = asyncio.Event()

    def connect(cfg, ends=None):
        connects.append(1)
        if len(connects) > 1:
            time.sleep(0.3)
            raise OSError(60, "Operation timed out")
        return object()

    async def poll(cfg, conn, db, ends=None):
        await changed.wait()
        raise OSError(54, "reset")

    monkeypatch.setattr(imap, "connect", connect)
    monkeypatch.setattr(imap, "poll", poll)
    monkeypatch.setattr(imap, "drop", lambda conn: None)

    async def go():
        class Db:
            closed = broken = False

        watching = asyncio.create_task(imap.watch(None, Db(), asyncio.Event(), changed=changed))
        await asyncio.sleep(0.1)
        changed.set()
        await asyncio.sleep(0.1)  # the second connection is failing slowly now
        for _ in range(3):
            changed.set()
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.6)
        watching.cancel()
        with pytest.raises(asyncio.CancelledError):
            await watching

    run(go())
    assert len(connects) == 2


@pytest.mark.parametrize(
    "fails, entered, again",
    [
        (OSError(60, "Operation timed out"), True, True),
        (imaplib.IMAP4.abort("socket error: EOF"), True, True),
        (OSError(60, "Operation timed out"), False, False),
        (imaplib.IMAP4.error("Server does not support IMAP4 IDLE"), False, False),
        (imaplib.IMAP4.error("idle denied: ['not now']"), False, False),
        (imaplib.IMAP4.abort("unexpected status response: BYE"), False, False),
    ],
)
def test_only_a_transport_failure_after_idle_began_reconnects_before_the_tick(
    monkeypatch, fails, entered, again
):
    fake = _Fake(monkeypatch, fails=fails, entered=entered)
    connects = run(fake.watch())
    assert (connects > 1) is again and (again or connects == 1)


def test_a_reply_all_is_filled_in_from_the_received_email_before_it_is_held(dsn, op, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    tom = mid()
    effects = ws / ".valor" / "effects"
    effects.mkdir(parents=True, exist_ok=True)
    (effects / "reply.json").write_text(
        json.dumps(
            {"action_type": "email.send", "target": "anyone", "payload": {"reply_to": tom, "body": "Done."}}
        )
    )
    (effects / "stray.json").write_text(
        json.dumps(
            {
                "action_type": "email.send",
                "target": "anyone",
                "payload": {"reply_to": "<no@such>", "body": "x"},
            }
        )
    )

    async def go():
        async with await db.connect(dsn) as conn:
            await intake.receive(
                conn,
                intake.Inbound(
                    channel="email", chat_id="<root@valor.test>", chat_kind="email", message_id=tom,
                    sender_id="tom@yuda.me", sender_name="Tom", sent_at="2026-10-03T00:00:00Z", text="Plans",
                    thread=[{"id": "<root@valor.test>"}],
                    headers={"subject": "Plans", "to": ["valor@test.local", "ann@example.com"],
                             "cc": ["bob@example.com", "tom@yuda.me"]},
                ),
            )  # fmt: skip
        task = await scripted.start(dsn, ws)
        turn = f"turn-{uuid.uuid4().hex[:12]}"  # turn ids are unique across the ledger
        found = signals.collect(ws, turn)
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, task, "turn.started", {"turn_id": turn, "state": "plan"})
            await ledger.append(
                conn,
                task,
                "turn.ended",
                {"turn_id": turn, "outcome": "done", "result": {"session_id": "s"}},
            )
            with configure(email_address="valor@test.local"):
                await session.record(
                    conn,
                    task,
                    turn,
                    found,
                    state=tasks.machine.State.PLAN,
                    workspace=str(ws),
                    performers=declared(),
                )
            rows = await ledger.read(conn, task)
        return rows

    rows = run(go())
    (action,) = [r["payload"] for r in rows if r["type"] == "effect.held"]
    assert action["action_type"] == "email.send" and action["target"] == "tom@yuda.me"
    assert action["payload"] == {
        "to": ["tom@yuda.me"],
        "cc": ["ann@example.com", "bob@example.com"],
        "subject": "Re: Plans",
        "in_reply_to": tom,
        "references": ["<root@valor.test>", tom],
        "body": "Done.",
        "files": [],
    }
    (collected,) = [r["payload"] for r in rows if r["type"] == "turn.collected"]
    errors = {e["file"]: e.get("error") for e in collected["effects"]}
    assert "names no received email" in errors["stray.json"]


def test_the_size_refused_at_request_is_the_whole_encoded_message(dsn, op, tmp_path):
    """At exactly 25,000,000 encoded bytes an email is held; one byte more
    is refused before Tom is asked."""
    to = "tom@yuda.me"
    f = tmp_path / "big.bin"
    f.write_bytes(b"\0" * 18_000_000)
    files = [{"path": str(f), "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}]

    def payload(body: str) -> dict:
        return {"to": [to], "cc": [], "subject": "s", "body": body, "in_reply_to": None, "references": [],
                "files": files}  # fmt: skip

    def body_for(size: int) -> str:
        # Lines of 70 characters go 7bit, two bytes of CRLF each; the last
        # line takes up the rest.
        base = mail.email_encoded_bytes(payload("x\n"), [18_000_000])
        need = size - base
        lines, rest = divmod(need, 72)
        body = ("y" * 70 + "\n") * lines + "x" * (1 + rest) + "\n"
        assert mail.email_encoded_bytes(payload(body), [18_000_000]) == size, (size, need)
        return body

    async def go():
        task = await new_task(dsn)
        async with await db.connect(dsn) as conn:
            at = await broker.request(
                conn,
                declared(str(tmp_path)),
                task,
                broker.Action("email.send", to, payload(body_for(25_000_000))),
            )
            over = await broker.request(
                conn,
                declared(str(tmp_path)),
                task,
                broker.Action("email.send", to, payload(body_for(25_000_001))),
            )
        return at, over

    with configure(email_address="valor@test.local"):
        at, over = run(go())
    assert at.kind == "pending"
    assert over.kind == "refused" and "is 25000001 bytes, over email's limit of 25000000 bytes" in over.error


def test_the_kernel_job_knows_valors_address_when_it_fills_a_reply_all(monkeypatch):
    """The kernel runs `reply_all` and the request-time size check, so the
    environment its launchd job carries must hold Valor's address: run in
    exactly that environment, Valor's own address is not copied on a
    reply-all, and the size measured is of a message from that address."""
    import plistlib

    from core import serve

    monkeypatch.setenv("VALOR_EMAIL_ADDRESS", "valor@test.local")
    job = plistlib.loads(serve.plist())
    code = (
        "import json; from core import mail\n"
        "row = {'sender_id': 'tom@yuda.me', 'message_id': '<m@x>', 'headers': "
        "{'to': ['valor@test.local'], 'cc': ['x@y.z']}}\n"
        "print(json.dumps([mail.reply_all(row, mail.settings.email_address)['cc'],"
        " mail.email_encoded_bytes({'to': ['a@b.c'], 'body': ''}, [])]))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        env=job["EnvironmentVariables"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    cc, size = json.loads(out.stdout)
    assert cc == ["x@y.z"]
    own = mail.email_encoded_bytes({"to": ["a@b.c"], "body": ""}, [], "valor@test.local")
    assert size == own != mail.email_encoded_bytes({"to": ["a@b.c"], "body": ""}, [], "")


def test_an_eml_attachment_goes_out_as_wire_valid_mime_that_measures_exactly():
    """`message/rfc822` may not be base64 (RFC 2046 5.2.1) and a bare LF may
    not be sent in DATA (RFC 5321 2.3.8): the file goes as its own bytes in
    an `application/octet-stream` part, every line ending CRLF, and the size
    measured is the size sent."""
    import email

    eml = b"From: a@b.c\nSubject: forwarded\n\nbody\n" * 40
    payload = {"to": ["tom@yuda.me"], "body": "see attached", "files": [{"path": "/x/fwd.eml"}]}
    msg = mail.email_message(
        payload,
        [("fwd.eml", eml)],
        message_id=mail.message_id("", "valor@test.local"),
        sender="valor@test.local",
        date=mail._PLACEHOLDER_DATE,
    )
    wire = mail.serialized(msg)
    assert b"\n" not in wire.replace(b"\r\n", b"")
    part = email.message_from_bytes(wire).get_payload()[1]
    assert part.get_content_type() == "application/octet-stream"
    assert part["Content-Transfer-Encoding"] == "base64" and part.get_payload(decode=True) == eml
    assert mail.email_encoded_bytes(payload, [len(eml)], "valor@test.local") == len(wire)


# -- a kill at every point of a send ----------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent


def bridge_child(dsn, cfg, log) -> subprocess.Popen:
    values = {k: v for k, v in dataclasses.asdict(cfg).items()}
    values["since"] = cfg.since.isoformat()
    env = {**os.environ, "EMAIL_DSN": dsn, "EMAIL_CONFIG": json.dumps(values), "VALOR_SERVE_TICK_S": "0.2"}
    return subprocess.Popen(
        [sys.executable, "-m", "tests.email_child"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT
    )


def killed_mid_send(dsn, mailbox, tmp_path, when: threading.Event) -> tuple[str, str]:
    """A released send performed by a bridge process killed (SIGKILL, by
    its PID) once `when` is set; then the outbox's reconcile. Returns the
    effect id and the outcome's kind."""
    to = "tom@yuda.me"
    payload = {"to": [to], "cc": [], "subject": "Re: plans", "body": "Done.", "in_reply_to": None,
               "references": [], "files": []}  # fmt: skip
    cfg = mailbox.config()

    async def held() -> str:
        task = await new_task(dsn)
        async with await db.connect(dsn) as conn:
            h = await broker.request(conn, declared(), task, broker.Action("email.send", to, payload))
            await broker.approve(conn, h.effect_id, note="approve")
            await broker.release(conn, declared(), h.effect_id)
        return h.effect_id

    effect_id = run(held())
    with open(tmp_path / "child.log", "wb") as log:
        child = bridge_child(dsn, cfg, log)
        try:
            assert when.wait(30), (tmp_path / "child.log").read_text()
            assert run(of_type(dsn, "effect.intent", effect_id=effect_id))
        finally:
            os.kill(child.pid, signal.SIGKILL)
            child.wait()
    assert not run(of_type(dsn, "effect.outcome", effect_id=effect_id))

    async def reconciled():
        async with outbox(dsn, EmailBridge(cfg, dsn)) as box:
            return await box.reconcile()

    return effect_id, run(reconciled())


def test_a_send_killed_after_its_end_of_data_line_is_reconciled_done_from_sent_mail(
    dsn, op, mailbox, tmp_path
):
    accepted = threading.Event()
    mailbox.smtp.behavior.reply_delay_s = 60.0
    mailbox.smtp.behavior.on_data = lambda: threading.Thread(
        target=lambda: (_until(lambda: mailbox.smtp.accepted), accepted.set()), daemon=True
    ).start()
    effect_id, (outcome,) = killed_mid_send(dsn, mailbox, tmp_path, accepted)
    assert outcome.kind == "done"
    assert len(mailbox.smtp.accepted) == 1
    (row,) = run(of_type(dsn, "effect.outcome", effect_id=effect_id))
    assert row["reconciled"] is True and row["result"]["message_id"] == outcome.result["message_id"]


def in_flight(dsn, effect_id) -> bool:
    async def go():
        async with await db.connect(dsn) as conn:
            return effect_id in await broker.dangling(conn, ["email.send"])

    return run(go())


def test_a_send_killed_before_the_server_took_the_message_stays_in_flight(dsn, op, mailbox, tmp_path):
    """The server answered DATA and never read the body; Sent Mail does
    not hold it, which cannot show it failed, so the effect stays in
    flight and is looked up again on each outbox wake."""
    started = threading.Event()
    mailbox.smtp.behavior.read_delay_s = 60.0
    mailbox.smtp.behavior.on_data = started.set
    effect_id, outcomes = killed_mid_send(dsn, mailbox, tmp_path, started)
    assert outcomes == [] and in_flight(dsn, effect_id)
    assert not run(of_type(dsn, "effect.outcome", effect_id=effect_id))
    assert mailbox.smtp.accepted == []


def test_a_send_killed_before_its_greeting_stays_in_flight(dsn, op, mailbox, tmp_path):
    """The SMTP port accepts the connection and never greets."""
    with silent_smtp(mailbox) as connected:
        effect_id, outcomes = killed_mid_send(dsn, mailbox, tmp_path, connected)
    assert outcomes == [] and in_flight(dsn, effect_id)
    assert mailbox.smtp.connections == 0


@contextlib.contextmanager
def silent_smtp(mailbox):
    """The SMTP port is a server that takes the connection and never
    greets."""
    with socket.create_server(("127.0.0.1", listen())) as silent:
        held = []
        connected = threading.Event()

        def accept():
            held.append(silent.accept()[0])
            connected.set()

        threading.Thread(target=accept, daemon=True).start()
        real = mailbox.config
        mailbox.config = lambda **o: real(**{"smtp_port": silent.getsockname()[1], **o})
        try:
            yield connected
        finally:
            mailbox.config = real
            for c in held:
                c.close()


def _until(ready) -> None:
    while not ready():
        time.sleep(0.05)


async def served_until(dsn, cfg, effect_id, ready=None, settled="effect.outcome") -> list[dict]:
    """`bridge.serve` on the email bridge until the effect has a `settled`
    row and `ready()` holds; returns those rows."""
    served = asyncio.create_task(bridge.serve(EmailBridge(cfg, dsn), dsn))
    try:
        for _ in range(300):
            done = await of_type(dsn, settled, effect_id=effect_id)
            if done and (ready is None or await ready()):
                return done
            assert not served.done(), served
            await asyncio.sleep(0.1)
        raise AssertionError(f"no {settled}")
    finally:
        served.cancel()
        with pytest.raises(asyncio.CancelledError):
            await served


async def released(dsn, task, payload, workspace=None) -> str:
    async with await db.connect(dsn) as conn:
        action = broker.Action("email.send", ",".join(sorted(payload["to"])), payload)
        held = await broker.request(conn, declared(workspace), task, action)
        assert held.kind == "pending", held
        await broker.approve(conn, held.effect_id, note="approve")
        assert (await broker.release(conn, declared(workspace), held.effect_id)).kind == "released"
    return held.effect_id


def test_a_file_swapped_after_approval_fails_the_send_and_is_never_sent(dsn, op, mailbox, tmp_path):
    plan = tmp_path / "plan.txt"
    plan.write_bytes(b"approved bytes")
    payload = {"to": ["tom@yuda.me"], "cc": [], "subject": "Re: plans", "body": "Attached.",
               "in_reply_to": None, "references": [],
               "files": [{"path": str(plan), "sha256": hashlib.sha256(b"approved bytes").hexdigest()}]}  # fmt: skip

    async def go():
        task = await new_task(dsn)
        effect_id = await released(dsn, task, payload, str(tmp_path))
        plan.write_bytes(b"swapped bytes")
        return await served_until(dsn, mailbox.config(), effect_id, settled="effect.outcome")

    (failed,) = run(go())
    assert failed["kind"] == "failed" and "sha256 is not the one approved" in failed["error"]
    assert mailbox.smtp.connections == 0 and mailbox.smtp.accepted == []


# -- a server that never answers holds only its own effect -------------------------------

PAYLOAD = {"to": ["tom@yuda.me"], "cc": [], "subject": "Re: plans", "body": "Done.", "in_reply_to": None,
           "references": [], "files": []}  # fmt: skip


def test_a_send_to_a_server_that_never_greets_does_not_hold_the_next_send(dsn, op, mailbox):
    """The first connection is taken and never greeted. The second send is
    released after it and is performed and settled while the first is
    still waiting; the first has no outcome and no notice, since it is
    still being performed."""
    mailbox.smtp.behavior.silent = 1

    async def go():
        task = await new_task(dsn)
        first = await released(dsn, task, {**PAYLOAD, "body": "first"})
        served = asyncio.create_task(bridge.serve(EmailBridge(mailbox.config(), dsn), dsn))
        try:
            for _ in range(100):
                if mailbox.smtp.connections:
                    break
                await asyncio.sleep(0.1)
            second = await released(dsn, task, {**PAYLOAD, "body": "second"})
            for _ in range(300):
                if await of_type(dsn, "effect.outcome", effect_id=second):
                    break
                assert not served.done(), served
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.6)  # a few wakes
            return (
                await of_type(dsn, "effect.outcome", effect_id=first),
                await of_type(dsn, "effect.outcome", effect_id=second),
                await of_type(dsn, "notice.requested", about_key=f"send-in-doubt:{first}"),
            )
        finally:
            served.cancel()
            with pytest.raises(asyncio.CancelledError):
                await served

    first_out, second_out, notices = run(go())
    assert first_out == [] and notices == []
    assert [o["kind"] for o in second_out] == ["done"]
    assert len(mailbox.smtp.accepted) == 1 and b"second" in mailbox.smtp.accepted[0].data


def test_a_lookup_that_never_answers_does_not_hold_the_outbox(dsn, op, mailbox, tmp_path, monkeypatch):
    """A send in flight whose Sent Mail lookup hangs holds only its own
    reconcile; a release is performed and settled beside it."""
    with silent_smtp(mailbox) as connected:
        stuck, outcomes = killed_mid_send(dsn, mailbox, tmp_path, connected)
    assert outcomes == [] and in_flight(dsn, stuck)
    entered, free = threading.Event(), threading.Event()
    lookups = []

    def hung(cfg, action, key, since, ends):
        lookups.append(key)
        entered.set()
        free.wait(60)
        raise broker.Unknown("released at the end of the test")

    monkeypatch.setattr(smtp, "lookup", hung)

    async def go():
        task = await new_task(dsn)
        later = await released(dsn, task, PAYLOAD)
        try:
            (done,) = await served_until(dsn, mailbox.config(), later)
            await asyncio.sleep(0.6)  # a few wakes
            return done
        finally:
            free.set()  # the hung thread ends before the loop closes

    outcome = run(go())
    assert entered.is_set() and outcome["kind"] == "done"
    assert not run(of_type(dsn, "effect.outcome", effect_id=stuck))
    assert len([k for k in lookups if k.endswith(stuck)]) == 1  # not asked again on each wake while it hangs


def test_a_send_in_doubt_tells_tom_once_at_once(dsn, op, mailbox):
    """The server took the body, filed nothing, and answered with a line
    that is neither a 250 nor a refusal: the send is in doubt, a notice is
    requested the moment it is, and later wakes (each asks Sent Mail
    again) request no second one."""
    mailbox.smtp.behavior.file_in_sent = False
    mailbox.smtp.behavior.final_reply = "garbled"

    async def go():
        task = await new_task(dsn)
        effect = await released(dsn, task, PAYLOAD)
        key = f"send-in-doubt:{effect}"
        served = asyncio.create_task(bridge.serve(EmailBridge(mailbox.config(), dsn), dsn))
        try:
            for _ in range(300):
                if await of_type(dsn, "notice.requested", about_key=key):
                    break
                assert not served.done(), served
                await asyncio.sleep(0.1)
            await asyncio.sleep(1.0)  # several wakes
            return (
                effect,
                await of_type(dsn, "notice.requested", about_key=key),
                await of_type(dsn, "effect.outcome", effect_id=effect),
            )
        finally:
            served.cancel()
            with pytest.raises(asyncio.CancelledError):
                await served

    effect, notices, outcomes = run(go())
    assert outcomes == []
    (notice,) = notices
    assert notice["kind"] == "send_in_doubt" and effect in notice["text"]
    assert notice["channel"] == "telegram" and notice["chat_id"]


def test_a_send_cut_off_before_its_greeting_tells_tom_on_the_next_wake(dsn, op, mailbox, tmp_path):
    with silent_smtp(mailbox) as connected:
        effect_id, _ = killed_mid_send(dsn, mailbox, tmp_path, connected)

    async def go():
        served = asyncio.create_task(bridge.serve(EmailBridge(mailbox.config(), dsn), dsn))
        try:
            for _ in range(300):
                found = await of_type(dsn, "notice.requested", about_key=f"send-in-doubt:{effect_id}")
                if found:
                    return found
                assert not served.done(), served
                await asyncio.sleep(0.1)
            raise AssertionError("no notice")
        finally:
            served.cancel()
            with pytest.raises(asyncio.CancelledError):
                await served

    (notice,) = run(go())
    assert notice["kind"] == "send_in_doubt"
    assert in_flight(dsn, effect_id)


# A stop ends a command that waits on a server that never answers. Nothing
# in the code times a read out; the connection is what the stop ends.


def workers() -> list[str]:
    """Live threads with a frame in the email bridge: a thread still in a
    command, or in a read."""
    stuck = []
    for ident, frame in sys._current_frames().items():
        while frame:
            if "/bridges/email/" in frame.f_code.co_filename:
                stuck.append(f"{ident}: {frame.f_code.co_name}")
                break
            frame = frame.f_back
    return stuck


async def stopped(make, ready) -> None:
    """`make()` as a task, once `ready()` holds and a moment has passed;
    then cancelled."""
    task = asyncio.create_task(make())
    try:
        while not ready():
            assert not task.done(), task
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.5)
        assert not task.done(), "it returned before the stop"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def ended(coro) -> None:
    """`asyncio.run` waits for its worker threads when it closes the loop,
    so a thread still blocked on its server holds this until it is ended:
    it returns promptly with no thread left in the bridge."""
    started = time.monotonic()
    run(coro)
    while workers() and time.monotonic() - started < 30:
        time.sleep(0.05)  # a thread already told to end, on its way out
    assert time.monotonic() - started < 30 and workers() == []


BIG = {**PAYLOAD, "body": ("x" * 99 + "\n") * 90_000}  # 9 MB, more than the sockets buffer
LOOKUP_SINCE = "2026-01-01T00:00:00+00:00"


@pytest.mark.parametrize(
    "point", ["EHLO", "STARTTLS", "TLS", "EHLO2", "AUTH", "MAIL", "RCPT", "DATA", "BODY", "FINAL"]
)
def test_a_stop_ends_a_send_whose_server_never_answers_at(mailbox, point):
    mailbox.smtp.behavior.mute_at = point
    cfg = mailbox.config()
    act = broker.Action("email.send", "tom@yuda.me", BIG if point == "BODY" else PAYLOAD)
    ended(stopped(lambda: EmailBridge(cfg).perform(act, act.key("e1")), lambda: mailbox.smtp.connections))


def test_a_stop_ends_a_send_whose_server_never_greets(mailbox):
    act = broker.Action("email.send", "tom@yuda.me", PAYLOAD)
    with silent_smtp(mailbox) as connected:
        cfg = mailbox.config()
        ended(stopped(lambda: EmailBridge(cfg).perform(act, act.key("e1")), connected.is_set))


def test_a_stop_ends_an_imap_server_that_never_greets(mailbox):
    """The lookup's connection and the watch's both: the port takes the
    connection and sends nothing, so the TLS handshake waits."""
    act = broker.Action("email.send", "tom@yuda.me", PAYLOAD)
    with silent_smtp(mailbox) as connected:
        cfg = mailbox.config(imap_port=mailbox.config().smtp_port)
        ended(stopped(lambda: EmailBridge(cfg).lookup(act, act.key("e1"), LOOKUP_SINCE), connected.is_set))
    with silent_smtp(mailbox) as connected:
        cfg = mailbox.config(imap_port=mailbox.config().smtp_port)
        ended(stopped(lambda: imap.watch(cfg, None, asyncio.Event()), connected.is_set))


def test_a_stop_ends_the_watch_idling(dsn, op, mailbox):
    cfg = mailbox.config()

    async def go():
        idling = time.monotonic() + 3  # connected, searched, and waiting in IDLE
        async with await db.connect(dsn) as conn:
            await stopped(lambda: imap.watch(cfg, conn, asyncio.Event()), lambda: time.monotonic() > idling)

    ended(go())


def test_a_send_whose_server_mutes_the_quit_is_done_and_returns(mailbox):
    mailbox.smtp.behavior.mute_at = "QUIT"
    act = broker.Action("email.send", "tom@yuda.me", PAYLOAD)
    ended(in_thread_perform(mailbox.config(), act))
    assert len(mailbox.smtp.accepted) == 1


async def in_thread_perform(cfg, act):
    result = await EmailBridge(cfg).perform(act, act.key("e1"))
    assert result["accepted"] == ["tom@yuda.me"]


def test_every_call_has_a_thread_of_its_own_and_a_queued_one_cannot_exist():
    """More hung calls than the default executor has workers, and one more
    call is still started at once and still ends on a stop."""

    async def go():
        pairs, calls = [], []

        def block(ends: Ends):
            a, b = socket.socketpair()
            pairs.append((a, b))
            ends.register(a)
            a.recv(1)

        async def hung():
            ends = Ends()
            try:
                await ends.call(block, ends)
            finally:
                ends.close()

        for _ in range(40):
            calls.append(asyncio.create_task(hung()))
        while len(pairs) < 40:
            await asyncio.sleep(0.05)
        last = asyncio.create_task(hung())
        while len(pairs) < 41:
            await asyncio.sleep(0.05)
        for c in (last, *calls):
            c.cancel()
        for c in (last, *calls):
            with pytest.raises(asyncio.CancelledError):
                await c
        for a, b in pairs:
            a.close()
            b.close()

    ended(go())


def sending_threads() -> list[int]:
    stuck = []
    for ident, frame in sys._current_frames().items():
        while frame:
            if frame.f_code.co_filename.endswith("/bridges/email/smtp.py"):
                stuck.append(ident)
                break
            frame = frame.f_back
    return stuck


def test_tom_stopping_the_task_ends_a_send_blocked_on_its_server(dsn, op, mailbox):
    """The stop reaches a send that is already performing: its connection is
    shut down and its thread returns. The server was muted before the end
    of data line, so nothing was sent: the outcome is `failed`, with no
    Sent Mail lookup and no `send_in_doubt` notice."""
    mailbox.smtp.behavior.mute_at = "RCPT"

    async def go():
        task = await new_task(dsn)
        effect = await released(dsn, task, PAYLOAD)
        served = asyncio.create_task(bridge.serve(EmailBridge(mailbox.config(), dsn), dsn))
        try:
            for _ in range(100):
                if sending_threads():
                    break
                await asyncio.sleep(0.1)
            assert sending_threads()
            await asyncio.sleep(0.5)
            async with await db.connect(dsn) as conn:
                await tasks.stop(conn, task, reason="test")
            for _ in range(100):
                if not sending_threads():
                    break
                await asyncio.sleep(0.1)
            assert sending_threads() == []
            for _ in range(100):
                if await of_type(dsn, "effect.outcome", effect_id=effect):
                    break
                assert not served.done(), served
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.6)  # more wakes: no lookup, no notice
            return (
                effect,
                await of_type(dsn, "effect.outcome", effect_id=effect),
                await of_type(dsn, "notice.requested", about_key=f"send-in-doubt:{effect}"),
            )
        finally:
            served.cancel()
            with pytest.raises(asyncio.CancelledError):
                await served

    _, outcomes, notices = run(go())
    assert [o["kind"] for o in outcomes] == ["failed"] and notices == []
    assert "before the end of data line" in outcomes[0]["error"]
    assert mailbox.smtp.accepted == []


def test_a_dropped_stop_listener_does_not_end_a_healthy_send(dsn, op, mailbox, monkeypatch):
    """The stop listener's connection fails while a slow send runs. Nobody
    stopped the task, so the send finishes `done`, with no notice."""
    mailbox.smtp.behavior.read_delay_s = 3.0  # held before the end of data

    async def dropped(listener, task_id, check):
        await asyncio.sleep(1.0)
        raise psycopg.OperationalError("the listener connection dropped")

    monkeypatch.setattr(EmailBridge, "stop_heard", staticmethod(dropped))

    async def go():
        task = await new_task(dsn)
        effect = await released(dsn, task, PAYLOAD)
        (outcome,) = await served_until(dsn, mailbox.config(), effect, settled="effect.outcome")
        return outcome, await of_type(dsn, "notice.requested", about_key=f"send-in-doubt:{effect}")

    outcome, notices = run(go())
    assert outcome["kind"] == "done" and notices == []
    assert len(mailbox.smtp.accepted) == 1


async def drop_stop_listeners(dsn) -> int:
    """The stop listeners' backends in the test database, ended by PID."""
    async with await db.connect(dsn) as conn:
        rows = await (
            await conn.execute(
                "SELECT pid FROM pg_stat_activity WHERE datname = current_database() "
                "AND application_name = 'valor-email-stop' AND pid <> pg_backend_pid()"
            )
        ).fetchall()
        for (pid,) in rows:
            await conn.execute("SELECT pg_terminate_backend(%s)", (pid,))
    return len(rows)


async def stop_after_drop(dsn, task, *, pause: float) -> None:
    assert await drop_stop_listeners(dsn) >= 1  # a killed child's may linger
    await asyncio.sleep(pause)
    async with await db.connect(dsn) as conn:
        await tasks.stop(conn, task, reason="test")


@pytest.mark.parametrize("pause", [1.0, 0.0], ids=["after_the_next_wake", "in_the_gap"])
def test_tom_stopping_a_hung_send_after_the_stop_listener_dropped_ends_it(dsn, op, mailbox, pause):
    """The listener's connection drops while the server is muted at RCPT.
    The next wake listens again, and a stop, heard on the new connection or
    found in the durable row for one that landed in the gap, ends the send:
    the effect settles `failed` (the end of data line never went), with
    no `send_in_doubt`."""
    mailbox.smtp.behavior.mute_at = "RCPT"

    async def go():
        task = await new_task(dsn)
        effect = await released(dsn, task, PAYLOAD)
        served = asyncio.create_task(bridge.serve(EmailBridge(mailbox.config(), dsn), dsn))
        try:
            for _ in range(100):
                if sending_threads():
                    break
                await asyncio.sleep(0.1)
            assert sending_threads()
            await asyncio.sleep(0.5)
            await stop_after_drop(dsn, task, pause=pause)
            for _ in range(100):
                if not sending_threads():
                    break
                await asyncio.sleep(0.1)
            assert sending_threads() == []
            key = f"send-in-doubt:{effect}"
            for _ in range(100):
                if await of_type(dsn, "effect.outcome", effect_id=effect):
                    break
                assert not served.done(), served
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.6)  # more wakes
            return (
                effect,
                await of_type(dsn, "effect.outcome", effect_id=effect),
                await of_type(dsn, "notice.requested", about_key=key),
            )
        finally:
            served.cancel()
            with pytest.raises(asyncio.CancelledError):
                await served

    _, outcomes, notices = run(go())
    assert [o["kind"] for o in outcomes] == ["failed"] and notices == []
    assert mailbox.smtp.accepted == []


def test_tom_stopping_a_hung_lookup_after_the_stop_listener_dropped_ends_it(
    dsn, op, mailbox, tmp_path, monkeypatch
):
    """A Sent Mail lookup that never answers, its listener dropped: a stop
    still ends it, with no outcome, and the effect stays in flight."""
    with silent_smtp(mailbox) as connected:
        stuck, outcomes = killed_mid_send(dsn, mailbox, tmp_path, connected)
    assert outcomes == [] and in_flight(dsn, stuck)
    entered, ended = threading.Event(), threading.Event()
    held = []

    def hung(cfg, action, key, since, ends):
        a, b = socket.socketpair()
        held.extend([a, b])
        ends.register(a)
        entered.set()
        try:
            a.recv(1)
        finally:
            ended.set()
        raise broker.Unknown("ended by a stop")

    monkeypatch.setattr(smtp, "lookup", hung)

    async def go():
        task = await _task_of(dsn, stuck)
        served = asyncio.create_task(bridge.serve(EmailBridge(mailbox.config(), dsn), dsn))
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.1)
            assert entered.is_set()
            await asyncio.sleep(0.5)
            await stop_after_drop(dsn, task, pause=1.0)
            for _ in range(100):
                if ended.is_set():
                    break
                await asyncio.sleep(0.1)
            return ended.is_set(), await of_type(dsn, "effect.outcome", effect_id=stuck)
        finally:
            served.cancel()
            with pytest.raises(asyncio.CancelledError):
                await served
            for c in held:
                c.close()

    stopped, outcomes = run(go())
    assert stopped and outcomes == [] and in_flight(dsn, stuck)


async def _task_of(dsn, effect_id) -> str:
    async with await db.connect(dsn) as conn:
        row = await (
            await conn.execute(
                "SELECT task_id FROM events WHERE type = 'effect.held' AND payload->>'effect_id' = %s",
                (effect_id,),
            )
        ).fetchone()
    return row[0]


def test_a_send_the_server_took_after_tom_stopped_the_task_settles_from_sent_mail(dsn, op, mailbox):
    """The server filed the message and never sent its 250; Tom stops the
    task and the call ends with no outcome. The next wakes ask Sent Mail,
    which holds it: `done`, and no notice, though the task is stopped."""
    mailbox.smtp.behavior.mute_at = "FINAL"

    async def go():
        task = await new_task(dsn)
        effect = await released(dsn, task, PAYLOAD)
        served = asyncio.create_task(bridge.serve(EmailBridge(mailbox.config(), dsn), dsn))
        try:
            for _ in range(100):
                if mailbox.smtp.accepted:
                    break
                await asyncio.sleep(0.1)
            assert mailbox.smtp.accepted
            await asyncio.sleep(0.5)
            async with await db.connect(dsn) as conn:
                await tasks.stop(conn, task, reason="test")
            for _ in range(100):
                if not sending_threads():
                    break
                await asyncio.sleep(0.1)
            assert sending_threads() == []
            for _ in range(100):
                if await of_type(dsn, "effect.outcome", effect_id=effect):
                    break
                assert not served.done(), served
                await asyncio.sleep(0.1)
            return (
                await of_type(dsn, "effect.outcome", effect_id=effect),
                await of_type(dsn, "notice.requested", about_key=f"send-in-doubt:{effect}"),
            )
        finally:
            served.cancel()
            with pytest.raises(asyncio.CancelledError):
                await served

    outcomes, notices = run(go())
    assert [o["kind"] for o in outcomes] == ["done"] and notices == []
    assert len(mailbox.smtp.accepted) == 1


def test_a_release_for_a_task_stopped_after_approval_is_recorded_refused(dsn, op, mailbox):
    """Tom approved and released the send, then stopped the task before the
    bridge performed it: the broker's fence refuses it, once, and nothing
    is sent."""

    async def go():
        task = await new_task(dsn)
        effect = await released(dsn, task, PAYLOAD)
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, task, reason="test")
        (refused,) = await served_until(dsn, mailbox.config(), effect, settled="effect.refused")
        await asyncio.sleep(0.6)  # a few wakes
        return task, refused, await of_type(dsn, "effect.refused", effect_id=effect)

    task, refused, all_refused = run(go())
    assert refused["at"] == "release" and refused["reason"] == task and len(all_refused) == 1
    assert mailbox.smtp.accepted == [] and mailbox.smtp.connections == 0


def test_sigterm_ends_a_send_blocked_on_its_server_and_the_bridge_exits(dsn, op, mailbox, tmp_path):
    """launchd's SIGTERM runs `Ends` on every blocked call: the process
    exits at once, where a thread left in a read would hold it."""
    mailbox.smtp.behavior.mute_at = "RCPT"

    async def held() -> str:
        task = await new_task(dsn)
        return await released(dsn, task, PAYLOAD)

    effect = run(held())
    with open(tmp_path / "child.log", "wb") as log:
        child = bridge_child(dsn, mailbox.config(), log)
        try:
            _until(lambda: mailbox.smtp.connections or child.poll() is not None)
            assert child.poll() is None, (tmp_path / "child.log").read_text()
            time.sleep(1.0)
            child.send_signal(signal.SIGTERM)
            assert child.wait(timeout=30) == 1
        finally:
            if child.poll() is None:
                os.kill(child.pid, signal.SIGKILL)
                child.wait()
    assert not run(of_type(dsn, "effect.outcome", effect_id=effect)) and in_flight(dsn, effect)
