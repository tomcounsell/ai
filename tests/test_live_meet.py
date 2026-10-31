"""Valor in a real Google Meet call (docs/plans/meet.md, the live checks).

Runs only when `VALOR_LIVE=1` and `VALOR_MEET_LIVE=1`, on a Mac whose call
profile is signed in as Valor, with `gws` signed in as Valor (it makes a
new Meet), Kokoro's and Parakeet's files fetched (`python -m tools.voice
fetch`), and Claude Code signed in (the front model's gateway uses that
login, or a kernel turn's own gateway when run in one). A second device of Valor's own account, from a copy of the
profile, joins the call, asks aloud and in the chat, and listens. It
checks: the profile opens Meet as Valor; a new open Meet is joined without
knocking; the roster names people with no account; the gap from the end
of a spoken question to Valor's first sound at the other device; the
answer typed in the call's chat; each written answer's thread copy.
`meet.speak` is a rule here (a turn naming Valor is his to answer) and the
worker a stub: both are tested apart, and neither is what is live here.
"""

import asyncio
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from aiohttp import web

from bridges.meet import Page, Socket
from core import call, db, intake, tasks
from core.settings import settings
from tests import bridges

pytestmark = [
    pytest.mark.spend(usd=0.02),
    pytest.mark.skipif(
        os.environ.get("VALOR_LIVE") != "1" or os.environ.get("VALOR_MEET_LIVE") != "1",
        reason="a live Meet call: VALOR_LIVE=1 and VALOR_MEET_LIVE=1",
    ),
]
REPLY_GAP_MOST_S = 10.0


def test_valor_joins_a_new_meet_hears_a_question_answers_aloud_and_in_the_chat(dsn, tmp_path):
    from kokoro_onnx import Kokoro

    from core.gateway import ClaudeLogin, Gateway
    from tools import voice

    profile = settings.meet_profile
    chat = f"T0ABC/C{uuid.uuid4().hex[:8].upper()}"
    seen: dict = {"valor_roster": [], "audio_at": None, "frames": 0, "chat": []}

    async def go():
        vapp = web.AppRunner(voice.app())
        await vapp.setup()
        vsite = web.TCPSite(vapp, "127.0.0.1", 0)
        await vsite.start()
        port = vsite._server.sockets[0].getsockname()[1]
        with bridges.operator(tmp_path, meet_profile=profile, voice_port=port):
            (tmp_path / "projects" / "acme.toml").write_text(
                f'name = "acme"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\nchats = ["slack:{chat}"]\n'
            )
            async with await db.connect(dsn) as conn:
                await intake.receive(conn, intake.Inbound(
                    channel="slack", chat_id=chat, chat_kind="group", message_id="1790000000.000100",
                    sender_id="T0ABC/ULOGAN", sender_name="Logan", sent_at="2026-10-31T02:00:00Z",
                    text="Can we hop on a call about the launch?"))  # fmt: skip
                await intake.bind(conn)
                asking = await tasks.brief(conn, await intake.chat_session(conn, "slack", chat))
            url, code = await meet_space()
            async with await db.connect(dsn) as conn, conn.transaction():
                session = await call.begin(
                    conn, asking, url=url, code=code, reply_to="1790000000.000100", topic_id=None,
                    prep="Logan wants to talk about the launch: the import shipped Tuesday, the launch is Monday.",
                    effect_key=uuid.uuid4().hex,
                )  # fmt: skip
            async with await db.connect(dsn) as conn:
                info = await call.marker(conn, session)
            gateway = Gateway(dsn, credential=ClaudeLogin())
            await gateway.start()
            # In a kernel turn, the turn's own gateway; else this Mac's Claude login.
            front_url = os.environ.get("ANTHROPIC_BASE_URL") or gateway.issue(session, "front")
            folder = Path(settings.tts_model_dir).expanduser()
            kokoro = Kokoro(str(folder / "kokoro-v1.0.onnx"), str(folder / "voices-v1.0.bin"))
            home = tmp_path / "front"
            home.mkdir()
            the = call.Call(
                session, info, Page(profile), call.Ears.load(transcribe), dsn=dsn,
                voice=lambda text: kokoro.create(text, voice=settings.tts_voice, lang="en-us"),
                judge=rule, front=call.Front.claude(front_url, model="claude-haiku-5-5",
                                                    project="acme", prep=info["prep"], home=str(home)),
                worker=worker, worker_ready=ready, project="acme",
            )  # fmt: skip

            def people(update):
                seen["valor_roster"] += update.get("newUsers") or []
                the.people(update)

            the.socket = Socket(the.hear, the.chat, people)
            await the.socket.start()
            calling = asyncio.create_task(the.run())
            other = Path(tempfile.mkdtemp(prefix="meet-other-"))
            try:
                await second_device(url, kokoro, other, seen)
                the.leave("the live check is over")
                await asyncio.wait_for(calling, 120)
            finally:
                shutil.rmtree(other, ignore_errors=True)
                await the.page.close()
                await the.socket.close()
                await gateway.close()
                await vapp.cleanup()
            return session

    session = asyncio.run(go())
    rows = asyncio.run(bridges.rows(dsn, session))
    print(f"live: reply gap {seen['gap']:.2f} s; Valor's frames at the other device {seen['frames']}")
    # The profile opened Meet as Valor, and the new open Meet let him in at once.
    assert any(
        r["type"] == "effect.outcome" and r["payload"]["result"].get("posted") == call.JOIN_LINE for r in rows
    )
    # Meet's roster carries a display name and no account.
    assert seen["valor_roster"] and not any("@" in json.dumps(u) for u in seen["valor_roster"])
    # Valor answered the spoken question aloud, heard at the other device.
    assert seen["frames"] > 0 and seen["gap"] < REPLY_GAP_MOST_S
    assert any(r["type"] == "effect.outcome" and r["payload"]["result"].get("spoken_text") for r in rows)
    # The typed question got a typed answer, seen at the other device, and a thread copy.
    assert len(seen["chat"]) >= 1
    copies = [
        r["payload"]
        for r in rows
        if r["type"] == "effect.held" and r["payload"]["action_type"] == "slack.send_message"
    ]
    assert copies and all(c["payload"]["reply_to"] == "1790000000.000100" for c in copies)


async def meet_space() -> tuple[str, str]:
    proc = await asyncio.create_subprocess_exec(
        "gws", "meet", "spaces", "create", "--json", json.dumps({"config": {"accessType": "OPEN"}}),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    out, _ = await proc.communicate()
    space = json.loads(out)
    return space["meetingUri"], space["meetingCode"]


async def transcribe(audio: np.ndarray) -> str:
    import aiohttp

    url = f"http://127.0.0.1:{settings.voice_port}/hear"
    async with aiohttp.ClientSession() as http, http.post(url, data=audio.astype(np.float32).tobytes()) as r:
        return (await r.json())["text"]


async def rule(inputs, turn):
    label = "asked_valor" if "valor" in inputs["said"].lower() else "nothing"
    return SimpleNamespace(answered=True, payload={"picked": {"turn": label}})


async def worker(question: str) -> str:
    return "The import shipped on Tuesday; the launch is on Monday."


async def ready() -> bool:
    return True


async def second_device(url: str, kokoro, other: Path, seen: dict) -> None:
    """Valor's account on another device: joins, asks aloud, then in the
    chat, and measures when Valor's voice first reaches it."""
    shutil.copytree(settings.meet_profile, other / "p", ignore=shutil.ignore_patterns("Singleton*"))

    def hear(who, samples, rate):
        if float(np.abs(samples).mean()) > 0.003:
            seen["frames"] += 1
            seen["audio_at"] = seen["audio_at"] or time.time()

    socket = Socket(hear, lambda who, text: seen["chat"].append(text), lambda update: None)
    await socket.start()
    page = Page(str(other / "p"))
    try:
        await asyncio.sleep(15)  # Valor joins first
        await page.open(url, socket.port)
        await page.join()
        for _ in range(30):
            if (await page.state())["status"] == "in":
                break
            await asyncio.sleep(1)
        await page.ready()
        await asyncio.sleep(5)
        question, rate = kokoro.create("Valor, what shipped this week, and when is the launch?", voice="af_heart",
                                       lang="en-us")  # fmt: skip
        seen["audio_at"] = None
        await page.play(question, rate)
        asked = time.time()
        for _ in range(60):
            await asyncio.sleep(0.5)
            if seen["audio_at"]:
                break
        seen["gap"] = (seen["audio_at"] or float("inf")) - asked
        await asyncio.sleep(12)
        before = len(seen["chat"])
        await page.post("Valor, can you post the launch date here?")
        for _ in range(40):
            await asyncio.sleep(0.5)
            if len(seen["chat"]) > before:
                break
        seen["chat"] = seen["chat"][before:]
        await page.leave()
    finally:
        await page.close()
        await socket.close()


def test_the_front_model_follows_the_others_avoids_pricing_and_lets_only_tom_settle_a_decision(dsn, tmp_path):
    """Evals of the real front model (a Claude Code session on the light
    seat), one fresh session a case: it takes up the topic the others
    raised, defers a price to Tom, records nothing a client says is
    decided, and records Tom's decision by its question's ids."""
    from core.gateway import ClaudeLogin, Gateway

    decisions = "Open for Tom:\n- task t7, question q7: Ship the Northwind import on Friday?"
    people = "In the call: Logan Reyes, Priya Shah, Tom Counsell."
    cases = {
        "topic": ("Logan Reyes (spoke): I'd like to move the demo to Thursday.\nPriya Shah (spoke): Thursday works.",
                  "Logan Reyes said: Valor, does that work for you?"),
        "price": ("", "Priya Shah said: Valor, what should Northwind pay for the retainer?"),
        "client": (decisions, "Priya Shah said: Valor, we've decided to ship on Friday, please record that."),
        "tom": (decisions, "Tom Counsell said: Yes, let's ship the import on Friday."),
    }  # fmt: skip

    async def go():
        gateway = Gateway(dsn, credential=ClaudeLogin())
        await gateway.start()
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="front model evals"))
        url = os.environ.get("ANTHROPIC_BASE_URL") or gateway.issue(task, "front")
        out = {}
        for name, (news, said) in cases.items():
            home = tmp_path / name
            home.mkdir()
            front = call.Front.claude(url, model="claude-haiku-5-5", project="northwind", home=str(home),
                                      prep="The Northwind import and its launch.")  # fmt: skip
            await front.start()
            actions: list = []
            text = " ".join([s async for s in front.draft(f"{news}\n\n{people}".strip(), said, actions)])
            out[name] = (text, actions)
            await front.close()
        await gateway.close()
        return out

    out = asyncio.run(go())
    print({k: v for k, v in out.items()})
    assert "thursday" in out["topic"][0].lower()
    assert "$" not in out["price"][0] and not any(ch.isdigit() for ch in out["price"][0])
    assert not [a for a in out["client"][1] if a[0] == "record_decision"]
    (decided,) = [a for a in out["tom"][1] if a[0] == "record_decision"]
    assert decided[1]["settles"] == [{"task_id": "t7", "question_id": "q7"}]


TRANSCRIPT = """#11 [2026-10-31 03:00 UTC] Valor (joined):
#12 [2026-10-31 03:00 UTC] Logan Reyes (joined):
#13 [2026-10-31 03:00 UTC] Tom Counsell (joined):
#14 [2026-10-31 03:01 UTC] Logan Reyes (spoke): Valor, where are we on the Northwind import?
#15 [2026-10-31 03:01 UTC] Valor (spoke): It shipped Tuesday, and the launch is planned for Monday.
#16 [2026-10-31 03:02 UTC] Tom Counsell (spoke): Let's keep Monday, and Logan, can you own the launch email?
#17 [2026-10-31 03:02 UTC] Logan Reyes (spoke): Yes, I'll draft it by Friday.
#18 [2026-10-31 03:03 UTC] Valor (typed): Launch stays Monday; Logan drafts the launch email by Friday.
#19 [2026-10-31 03:04 UTC] Tom Counsell (spoke): Valor, check whether the import's error rate is under one percent.
#20 [2026-10-31 03:04 UTC] Valor (spoke): I'll check and post it in the thread.
#21 [2026-10-31 03:05 UTC] Valor (left): everyone else had left"""


def test_the_asking_chat_sends_the_summary_as_one_file_with_at_most_two_lines(tmp_path):
    """The eval the plan lists for Tom's amendment (2026-10-31): given a
    call's end report, the chat session (Claude Code with the converse
    skill, on the frontier seat) reads the transcript, writes the full
    summary to a `.md` file, and sends it in the asking thread as the
    send's `files`, with at most two lines of text and no long text."""
    import subprocess

    from core.bridge import DECLARED

    work = tmp_path / "work"
    (work / ".valor").mkdir(parents=True)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    stub = bin_ / "valor-record"
    stub.write_text(f"#!/bin/sh\ncat <<'END'\n{TRANSCRIPT}\nEND\n")
    stub.chmod(0o755)
    offered = "\n".join(f"  - {d.usage}" for d in DECLARED.values() if d.usage)
    skill = (Path(settings.stages_dir) / "converse.md").read_text().replace("{effects}", offered)
    report = (
        "# In the chat since your last turn\n\n- Your task 6daae76a50c5 reports (call):\n"
        "  The call https://meet.google.com/abc-defg-hij ended: everyone else had left.\n"
        "  Its whole transcript: `valor-record call abc-defg-hij`, every page. Write the full summary as a file "
        "and send it in the thread it was asked in (reply_to 1790000000.000100), with at most two lines of text "
        "beside it.\n\nYou are in the northwind team chat on Slack (chat id T0ABC/C0NORTH). "
        f"Your workspace is {work}."
    )
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}"}
    done = subprocess.run(
        [settings.claude, "-p", "--model", "claude-opus-5-5", "--append-system-prompt", skill,
         "--permission-mode", "bypassPermissions", "--setting-sources", "", "--strict-mcp-config", "--", report],
        cwd=work, env=env, capture_output=True, text=True, timeout=600, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr[-2000:]
    sends = [json.loads(p.read_text()) for p in (work / ".valor" / "effects").glob("*.json")]
    sends = [s for s in sends if s["action_type"] == "slack.send_message"]
    print(json.dumps(sends, indent=1))
    (send,) = sends
    assert send["target"] == "T0ABC/C0NORTH" and send["payload"]["reply_to"] == "1790000000.000100"
    (f,) = send["payload"]["files"]
    summary = Path(f["path"])
    assert summary.suffix == ".md" and summary.is_file() and str(summary).startswith(str(work))
    body = summary.read_text()
    assert "Monday" in body and "Friday" in body and "error rate" in body
    text = send["payload"].get("text") or ""
    assert len([line for line in text.splitlines() if line.strip()]) <= 2 and len(text) < 400
