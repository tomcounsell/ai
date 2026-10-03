"""The email bridge on the kernel: the poll through intake, the bridge
under `bridge.serve`, the reply-all a turn asks for, the request-time size
refusal, and a kill at every point of a send, on real Postgres and the
local mail servers."""

import asyncio
import dataclasses
import hashlib
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

import pytest

from bridges.email import EmailBridge, imap
from core import bridge, broker, db, intake, ledger, mail, session, signals, tasks
from tests import scripted
from tests.fake_bridges import OPERATOR_EMAIL, configure, declared, new_task, of_type, operator, outbox
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
    async with await db.connect(dsn) as conn:
        return await imap.poll(cfg, conn)


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


def test_the_bridge_under_serve_sends_a_release_once_and_polls(dsn, op, mailbox):
    to = "tom@yuda.me"
    payload = {"to": [to], "cc": [], "subject": "Re: plans", "body": "Done.", "in_reply_to": None,
               "references": [], "files": []}  # fmt: skip
    incoming = mid()
    mailbox.dovecot.deliver(from_tom(message_id=incoming, body="while serving"))
    cfg = mailbox.config(poll_s=0.2)

    async def go():
        task = await new_task(dsn)
        async with await db.connect(dsn) as conn:
            held = await broker.request(
                conn, task, broker.Action("email.send", to, payload), performers=declared()
            )
            assert held.kind == "pending", held
            await broker.approve(conn, held.effect_id, note="approve")
            released = await broker.release(conn, held.effect_id, performers=declared())
            assert released.kind == "released", released
        served = asyncio.create_task(bridge.serve(EmailBridge(cfg, dsn), dsn))
        try:
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
            again = await broker.release(conn, held.effect_id, performers=declared())
        return held.effect_id, done, got, again

    effect_id, done, got, again = run(go())
    (outcome,) = done
    assert outcome["kind"] == "done"
    sent = outcome["result"]["sent"][0]
    assert sent["channel"] == "email" and sent["message_id"] == outcome["result"]["message_id"]
    assert len(mailbox.smtp.accepted) == 1 and len(got) == 1
    assert again.kind == "done"  # the recorded outcome; nothing is sent again
    assert len(run(of_type(dsn, "effect.outcome", effect_id=effect_id))) == 1


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
        broker.CURRENT.set(declared())  # a job's performers, as router.step sets them
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
                    conn, task, turn, found, state=tasks.machine.State.PLAN, workspace=str(ws)
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
        base = mail.email_encoded_bytes(payload("x\n"))
        need = size - base
        lines, rest = divmod(need, 72)
        body = ("y" * 70 + "\n") * lines + "x" * (1 + rest) + "\n"
        assert mail.email_encoded_bytes(payload(body)) == size, (size, need)
        return body

    async def go():
        task = await new_task(dsn)
        async with await db.connect(dsn) as conn:
            at = await broker.request(
                conn,
                task,
                broker.Action("email.send", to, payload(body_for(25_000_000))),
                performers=declared(),
            )
            over = await broker.request(
                conn,
                task,
                broker.Action("email.send", to, payload(body_for(25_000_001))),
                performers=declared(),
            )
        return at, over

    with configure(email_address="valor@test.local"):
        at, over = run(go())
    assert at.kind == "pending"
    assert over.kind == "refused" and "is 25000001 bytes, over email's limit of 25000000 bytes" in over.error


# -- a kill at every point of a send ----------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent


def bridge_child(dsn, cfg, log) -> subprocess.Popen:
    values = {k: v for k, v in dataclasses.asdict(cfg).items() if k != "smtp_timeouts"}
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
            h = await broker.request(
                conn, task, broker.Action("email.send", to, payload), performers=declared()
            )
            await broker.approve(conn, h.effect_id, note="approve")
            await broker.release(conn, h.effect_id, performers=declared())
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

    with configure(reconcile_after_s=0.0):
        (outcome,) = run(reconciled())
    return effect_id, outcome


def test_a_send_killed_after_its_end_of_data_line_is_reconciled_done_from_sent_mail(
    dsn, op, mailbox, tmp_path
):
    accepted = threading.Event()
    mailbox.smtp.behavior.reply_delay_s = 60.0
    mailbox.smtp.behavior.on_data = lambda: threading.Thread(
        target=lambda: (_until(lambda: mailbox.smtp.accepted), accepted.set()), daemon=True
    ).start()
    effect_id, outcome = killed_mid_send(dsn, mailbox, tmp_path, accepted)
    assert outcome.kind == "done"
    assert len(mailbox.smtp.accepted) == 1
    (row,) = run(of_type(dsn, "effect.outcome", effect_id=effect_id))
    assert row["reconciled"] is True and row["result"]["message_id"] == outcome.result["message_id"]


def test_a_send_killed_mid_body_is_reconciled_failed(dsn, op, mailbox, tmp_path):
    started = threading.Event()
    mailbox.smtp.behavior.read_delay_s = 60.0
    mailbox.smtp.behavior.on_data = started.set
    _, outcome = killed_mid_send(dsn, mailbox, tmp_path, started)
    assert outcome.kind == "failed"
    assert mailbox.smtp.accepted == []


def test_a_send_killed_before_its_greeting_is_reconciled_failed(dsn, op, mailbox, tmp_path):
    """The SMTP port accepts the connection and never greets."""
    with socket.create_server(("127.0.0.1", 0)) as silent:
        connected = threading.Event()
        held = []

        def accept():
            held.append(silent.accept()[0])
            connected.set()

        threading.Thread(target=accept, daemon=True).start()
        port = silent.getsockname()[1]
        real = mailbox.config
        mailbox.config = lambda **o: real(**{"smtp_port": port, **o})
        try:
            _, outcome = killed_mid_send(dsn, mailbox, tmp_path, connected)
        finally:
            mailbox.config = real
            for c in held:
                c.close()
    assert outcome.kind == "failed"
    assert mailbox.smtp.connections == 0


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


async def released(dsn, task, payload) -> str:
    async with await db.connect(dsn) as conn:
        action = broker.Action("email.send", ",".join(sorted(payload["to"])), payload)
        held = await broker.request(conn, task, action, performers=declared())
        assert held.kind == "pending", held
        await broker.approve(conn, held.effect_id, note="approve")
        assert (await broker.release(conn, held.effect_id, performers=declared())).kind == "released"
    return held.effect_id


def test_a_file_swapped_after_approval_is_refused_at_release_and_never_sent(dsn, op, mailbox, tmp_path):
    plan = tmp_path / "plan.txt"
    plan.write_bytes(b"approved bytes")
    payload = {"to": ["tom@yuda.me"], "cc": [], "subject": "Re: plans", "body": "Attached.",
               "in_reply_to": None, "references": [],
               "files": [{"path": str(plan), "sha256": hashlib.sha256(b"approved bytes").hexdigest()}]}  # fmt: skip

    async def go():
        task = await new_task(dsn)
        effect_id = await released(dsn, task, payload)
        plan.write_bytes(b"swapped bytes")
        return await served_until(dsn, mailbox.config(poll_s=0.2), effect_id, settled="effect.refused")

    (refused,) = run(go())
    assert refused["at"] == "release" and "sha256 differs" in refused["reason"]
    assert mailbox.smtp.connections == 0 and mailbox.smtp.accepted == []
