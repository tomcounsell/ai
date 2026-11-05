"""Valor in a real Google Meet call on Gemini Live (docs/plans/gemini-live.md,
the live checks).

Runs only when `VALOR_LIVE=1` and `VALOR_MEET_LIVE=1`, on a Mac whose call
profile is signed in as Valor, with `gws` signed in as Valor (it makes a
new Meet), Kokoro's files fetched (the other devices' voices), and the vault
`.env` holding the Gemini key (`VALOR_LIVE_GOOGLE_KEY`, default
`GOOGLE_API_KEY`). The call runs as the kernel runs it (`python -m core
call`'s `_call`). Two other devices of Valor's own account, from copies of
the profile, join it: one asks aloud and in the chat, the other talks over
the call as a third person would. The gap from the end of each spoken
question to Valor's first sound at the asking device is measured, the way
the cascaded pipeline's was (`page.play` resolving to the first frame), so
the two compare. The worker is a stub turn.
"""

import asyncio
import json
import os
import shutil
import statistics
import sys
import time
import uuid
from pathlib import Path

import numpy as np
import pytest

from bridges.meet import Page, Socket
from core import call, credentials, db, intake, ledger, runs, tasks
from core.settings import settings
from tests import bridges

pytestmark = [
    pytest.mark.spend(usd=0.50),
    pytest.mark.skipif(
        os.environ.get("VALOR_LIVE") != "1" or os.environ.get("VALOR_MEET_LIVE") != "1",
        reason="a live Meet call: VALOR_LIVE=1 and VALOR_MEET_LIVE=1",
    ),
]
GAP_MEDIAN_MOST_S = 2.0  # against the cascaded pipeline's 4 to 10 s (2026-11-05)
LONG_S = 12 * 60  # past a Live connection's life, so the session is resumed
LOOKED_UP = "The import's error rate is 0.4 percent."


def test_valor_on_gemini_live_answers_when_addressed_keeps_out_of_side_talk_and_outlasts_a_reconnect(
    dsn, tmp_path, monkeypatch
):
    from kokoro_onnx import Kokoro

    from core import __main__ as kernel

    def turn_for(prompt, resume, b):
        def build(url, brief, turn_id):
            return runs.TurnCommand(argv=[sys.executable, "-c", "pass"], env={}, cwd=b.workspace, harness="stub",
                                    parse=lambda out: {"text": LOOKED_UP})  # fmt: skip

        return build

    monkeypatch.setattr(kernel, "_turn_for", turn_for)
    keyfile = tmp_path / "keys" / "google-key"
    name = os.environ.get("VALOR_LIVE_GOOGLE_KEY", "GOOGLE_API_KEY")
    assert credentials.copy_keys(settings.vault_env, keyfile, [call.GOOGLE_KEY], {call.GOOGLE_KEY: name})[
        call.GOOGLE_KEY
    ] in ("written", "kept")
    monkeypatch.setattr(type(settings), "google_keyfile", property(lambda _: str(keyfile)))
    chat = f"T0ABC/C{uuid.uuid4().hex[:8].upper()}"
    folder = Path(settings.tts_model_dir).expanduser()
    kokoro = Kokoro(str(folder / "kokoro-v1.0.onnx"), str(folder / "voices-v1.0.bin"))
    seen: dict = {"gaps": {}, "chat": [], "frames": []}

    async def go():
        # `_call` connects by `settings.dsn()`: the test database here. The asking
        # device's display name is Valor's, so it stands in for Tom's.
        with bridges.operator(tmp_path, meet_profile=settings.meet_profile, database=settings.test_database,
                              operator_name="Valor Engels", meetings_store=str(tmp_path / "store" / "Meetings"),
                              summary_model=os.environ.get("VALOR_LIVE_SUMMARY_MODEL", settings.summary_model)):  # fmt: skip
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
            work = tmp_path / "worker-copy"
            work.mkdir()
            async with await db.connect(dsn) as conn:
                await ledger.append(
                    conn, session, "workspace.provisioned", {"fields": {"workspace": str(work)}}
                )
            started = time.time()
            calling = asyncio.create_task(kernel._call(session))
            a, b = Device(tmp_path / "a", seen), Device(tmp_path / "b", seen)
            try:
                await asyncio.sleep(15)  # Valor joins first
                await a.join(url)
                await asyncio.sleep(15)
                seen["gaps"]["hello"] = await a.ask(kokoro, "Hello.")
                await asyncio.sleep(6)
                seen["gaps"]["question"] = await a.ask(
                    kokoro, "Valor, what shipped this week, and when is the launch?"
                )
                await asyncio.sleep(8)
                seen["typed"] = await a.typed("Valor, can you post the launch date here?")
                seen["gaps"]["lookup"] = await a.ask(
                    kokoro, "Valor, please have your worker look up the import's error rate in the code."
                )
                for _ in range(90):
                    if any(LOOKED_UP in t for t in seen["chat"]):
                        break
                    await asyncio.sleep(1)
                seen["looked_up"] = any(LOOKED_UP in t for t in seen["chat"])
                await asyncio.sleep(4)
                seen["barge_at"] = await a.cut_in(kokoro, "Valor, tell me in five or six sentences everything you "
                                                  "know about this launch.", "Valor, stop. Wait a moment.")  # fmt: skip
                await asyncio.sleep(6)
                await b.join(url)
                await asyncio.sleep(8)
                seen["side_talk_frames"] = await b.side(kokoro, "Hey Sam, did you get the invoice from the printer "
                                                        "yesterday? I need it for the books.", a)  # fmt: skip
                await asyncio.sleep(3)
                seen["over_at"] = await a.cut_in(
                    kokoro,
                    "Valor, tell me a story of five or six sentences about a rocket launch.",
                    "Sam, could you grab me a coffee after this?",
                    by=b,
                )
                await asyncio.sleep(10)
                await b.leave()
                while time.time() - started < LONG_S:
                    await asyncio.sleep(30)
                seen["gaps"]["after_reconnect"] = await a.ask(kokoro, "Valor, are you still there?")
                await asyncio.sleep(8)
                await a.ask(kokoro, "Valor, please leave the call now.", wait=False)
                reason = await asyncio.wait_for(calling, 300)
            finally:
                await a.close()
                await b.close()
            return session, asking.id, reason

    session, asking_id, reason = asyncio.run(go())
    rows = asyncio.run(bridges.rows(dsn, session))
    report = next(r["payload"]["text"] for r in asyncio.run(bridges.rows(dsn, asking_id))
                  if r["type"] == "child.reported" and r["payload"]["about_key"].endswith(":ended"))  # fmt: skip
    says = [
        {**r["payload"]["result"], "at": r["at"].timestamp()}
        for r in rows
        if r["type"] == "effect.outcome" and "spoken_text" in (r["payload"]["result"] or {})
    ]
    for step in ("barge_at", "over_at"):  # the say that ended as the other voice spoke
        w = seen[step]
        w["cut"] = any(x["cut"] for x in says if w["over"] <= x["at"] <= w["over"] + 6)
    resumed = [
        r for r in rows if r["type"] == "gateway.charged" and r["payload"].get("model") == settings.call_model
    ]
    gaps = seen["gaps"]
    print(json.dumps({"gaps": gaps, "typed": seen["typed"], "looked_up": seen["looked_up"],
                      "side_talk_frames": seen["side_talk_frames"], "barge": seen["barge_at"], "over": seen["over_at"],
                      "says": says, "charged_turns": len(resumed), "reason": reason, "report": report}, indent=1))  # fmt: skip
    # Answered when addressed, and to "Hello" with one other person there.
    assert all(g < 30 for g in gaps.values())
    assert statistics.median(gaps.values()) <= GAP_MEDIAN_MOST_S
    # The typed question answered in the chat, not aloud; the lookup posted.
    assert seen["typed"]["chat"] and not seen["typed"]["frames"]
    assert seen["looked_up"]
    # Silence in side talk; cut off by the person asking, not by side talk.
    assert seen["side_talk_frames"] == 0
    assert seen["barge_at"]["cut"] and not seen["over_at"]["cut"]
    # "Valor, leave" from the operator, past a resumption, and Gemini's summary file.
    assert reason in (
        "Tom asked Valor to leave",
        "Valor was asked to leave",
    )  # Live's tool when it heard "Valer"
    assert "Gemini's summary of the call is" in report


class Device:
    """Another device of Valor's account in the call, from a copy of the
    profile: it speaks clips, types, and hears who else speaks."""

    def __init__(self, home: Path, seen: dict):
        self.home, self.seen = home, seen
        self.page = self.socket = None
        self.me: str | None = None
        self.voice: list[float] = []  # when Valor's audio reached this device
        self.others: set[str] = set()  # the other devices', not Valor's

    async def join(self, url: str) -> None:
        # Links as links: Chrome 156 leaves `RunningChromeVersion` dangling (the Captain, 2026-11-03).
        shutil.copytree(
            settings.meet_profile, self.home, symlinks=True, ignore=shutil.ignore_patterns("Singleton*")
        )

        def hear(who, samples, rate):
            if who not in self.others and float(np.abs(samples).mean()) > 0.003:
                self.voice.append(time.time())

        def people(update):
            for p in [*(update.get("newUsers") or []), *(update.get("updatedUsers") or [])]:
                if p.get("isCurrentUser"):
                    self.me = p.get("deviceId")

        self.socket = Socket(hear, lambda who, text: self.seen["chat"].append(text), people)
        await self.socket.start()
        self.page = Page(str(self.home))
        await self.page.open(url, self.socket.port)
        await self.page.join()
        for _ in range(30):
            if (await self.page.state())["status"] == "in":
                break
            await asyncio.sleep(1)
        await self.page.ready()

    async def say(self, kokoro, text: str) -> float:
        """Speak `text` into the call; when it ends playing."""
        pcm, rate = kokoro.create(text, voice="af_heart", lang="en-us")
        ints = (np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes()
        ahead = await self.page.stream(ints, rate, True)
        return time.time() + ahead

    async def ask(self, kokoro, text: str, *, wait: bool = True) -> float:
        """The seconds from the end of `text` to Valor's first sound here."""
        await self.quiet()
        end = await self.say(kokoro, text)
        if not wait:
            return 0.0
        for _ in range(120):
            await asyncio.sleep(0.25)
            if self.voice and self.voice[-1] > end:
                break
        later = [t for t in self.voice if t > end]
        await self.quiet()
        return (later[0] - end) if later else float("inf")

    async def quiet(self, s: float = 2.0) -> None:
        """Until Valor has been silent `s` seconds."""
        while self.voice and time.time() - self.voice[-1] < s:
            await asyncio.sleep(0.25)

    async def typed(self, text: str) -> dict:
        before, heard = len(self.seen["chat"]), len(self.voice)
        await self.page.post(text)
        for _ in range(60):
            await asyncio.sleep(0.5)
            if len(self.seen["chat"]) > before:
                break
        await asyncio.sleep(3)
        return {"chat": self.seen["chat"][before:], "frames": len(self.voice) - heard}

    async def cut_in(self, kokoro, ask: str, over: str, *, by=None) -> dict:
        """Ask for a long answer, and 1.5 s into it say `over` (from `by`);
        when `over` began, for the say it cut, if any."""
        await self.quiet()
        end = await self.say(kokoro, ask)
        for _ in range(120):
            await asyncio.sleep(0.25)
            if self.voice and self.voice[-1] > end:
                break
        await asyncio.sleep(1.5)
        start = time.time()
        spoke = await (by or self).say(kokoro, over)
        await asyncio.sleep(max(0.0, spoke - time.time()) + 2.5)
        cut = not self.voice or time.time() - self.voice[-1] > 1.5
        await self.quiet()
        return {"over": start, "heard_cut": cut}

    async def side(self, kokoro, text: str, listener) -> int:
        """Say `text`, not to Valor; how many of Valor's frames `listener` heard in the next 10 s."""
        listener.others.add(self.me)
        heard = len(listener.voice)
        end = await self.say(kokoro, text)
        await asyncio.sleep(max(0.0, end - time.time()) + 10)
        return len(listener.voice) - heard

    async def leave(self) -> None:
        if self.page is not None:
            await self.page.leave()

    async def close(self) -> None:
        if self.page is not None:
            await self.page.close()
        if self.socket is not None:
            await self.socket.close()
        shutil.rmtree(self.home, ignore_errors=True)


async def meet_space() -> tuple[str, str]:
    proc = await asyncio.create_subprocess_exec(
        "gws", "meet", "spaces", "create", "--json", json.dumps({"config": {"accessType": "OPEN"}}),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    out, _ = await proc.communicate()
    space = json.loads(out)
    return space["meetingUri"], space["meetingCode"]


SUMMARY = """# Call on the Northwind import, 2026-10-31

**Who joined:** Valor, Logan Reyes, Tom Counsell.

## Discussed
- The import shipped Tuesday; the launch stays Monday.

## Settled
- Logan drafts the launch email by Friday.

## Open
- Valor checks whether the import's error rate is under one percent.
"""


def test_the_asking_chat_sends_geminis_summary_unchanged_as_one_file_with_at_most_two_lines(tmp_path):
    """Given a call's end report naming Gemini's summary, the chat session
    (Claude Code with the converse skill, on the frontier seat) copies the
    file into its workspace and sends it unchanged in the asking thread as
    the send's `files`, with at most two lines of text and no summary of
    its own (docs/plans/gemini-live.md)."""
    import subprocess

    from core.bridge import DECLARED

    work = tmp_path / "work"
    (work / ".valor").mkdir(parents=True)
    summary_file = tmp_path / "store" / "2026-10-31 1000 Valor call abc-defg-hij" / "call-abc-defg-hij.md"
    summary_file.parent.mkdir(parents=True)
    summary_file.write_text(SUMMARY)
    offered = "\n".join(f"  - {d.usage}" for d in DECLARED.values() if d.usage)
    skill = (Path(settings.stages_dir) / "converse.md").read_text().replace("{effects}", offered)
    report = (
        "# In the chat since your last turn\n\n- Your task 6daae76a50c5 reports (call):\n"
        "  The call https://meet.google.com/abc-defg-hij ended: everyone else had left.\n"
        f"  Gemini's summary of the call is {summary_file}. Copy it into your workspace as call-abc-defg-hij.md "
        "and send it, unchanged, in the thread it was asked in (reply_to 1790000000.000100) as the send's "
        "`files`, with at most two lines of text beside it.\n\nYou are in the northwind team chat on Slack "
        f"(chat id T0ABC/C0NORTH). Your workspace is {work}."
    )
    done = subprocess.run(
        [settings.claude, "-p", "--model", "claude-opus-5-5", "--append-system-prompt", skill,
         "--permission-mode", "bypassPermissions", "--setting-sources", "", "--strict-mcp-config", "--", report],
        cwd=work, capture_output=True, text=True, timeout=600, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr[-2000:]
    sends = [json.loads(p.read_text()) for p in (work / ".valor" / "effects").glob("*.json")]
    sends = [s for s in sends if s["action_type"] == "slack.send_message"]
    print(json.dumps(sends, indent=1))
    (send,) = sends
    assert send["target"] == "T0ABC/C0NORTH" and send["payload"]["reply_to"] == "1790000000.000100"
    (f,) = send["payload"]["files"]
    sent = Path(f["path"])
    assert sent.name == "call-abc-defg-hij.md" and str(sent).startswith(str(work))
    assert sent.read_text() == SUMMARY
    text = send["payload"].get("text") or ""
    assert len([line for line in text.splitlines() if line.strip()]) <= 2 and len(text) < 400
