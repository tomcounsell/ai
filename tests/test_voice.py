"""Voice messages both ways: every audio attachment is transcribed by the
voice process before it is recorded, and an `.ogg` a turn sends goes to
Telegram as a voice note, spoken by `valor-tts` with no key."""

import asyncio
import base64
import dataclasses
import hashlib
import imaplib
import json
import os
import shutil
import subprocess
import sys
import threading
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from aiohttp import web

from bridges.email import imap
from bridges.slack.bridge import SlackBridge
from bridges.telegram.send import Expected, _matches
from bridges.telegram.wire import Media, Msg, document, ogg_seconds
from core import bridge, intake, workspace
from core.settings import settings
from tests import bridges
from tests.telegram_port import (
    chat,
    connected,
    emu_chat,
    machine,
    outcome,
    perform,
    received,
    reconcile,
    release,
    send,
    until,
)
from tools import voice

pytestmark = [
    pytest.mark.spend(usd=0),
    pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg"),
]
TOOLS = Path(__file__).resolve().parents[1] / "tools"
run = asyncio.run


def tone(seconds: float = 2.0, rate: int = 24_000) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def codec(data: bytes, tmp_path: Path) -> tuple[str, float]:
    f = tmp_path / "probe"
    f.write_bytes(data)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name", "-of", "json", str(f)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    got = json.loads(out.stdout)
    return got["streams"][0]["codec_name"], float(got["format"]["duration"])


class Voice:
    """The voice process's app with its engines faked, on a free port in a
    thread of its own, with `settings.voice_port` pointed at it."""

    def __init__(self, listen=None, speak=None):
        self.heard: list[str] = []
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        def heard(path):
            self.heard.append(path)
            return f"words of {Path(path).name[:8]}"

        def spoken(text, fmt):
            return voice.encode(tone(), 24_000, fmt)

        async def start():
            self.runner = web.AppRunner(voice.app(listen=listen or heard, speak=speak or spoken))
            await self.runner.setup()
            site = web.TCPSite(self.runner, "127.0.0.1", 0)
            await site.start()
            self.port = self.runner.addresses[0][1]
            ready.set()

        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        asyncio.run_coroutine_threadsafe(start(), self.loop)
        assert ready.wait(10)
        self._configured = bridges.configure(voice_port=self.port)
        self._configured.__enter__()

    def stop(self):
        self._configured.__exit__(None, None, None)
        asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)


@pytest.fixture
def heard():
    v = Voice()
    yield v
    v.stop()


# -- the voice process ----------------------------------------------------------


def test_decode_any_audio_to_16k_mono_and_encode_ogg_and_mp3(tmp_path):
    ogg = tmp_path / "v.ogg"
    ogg.write_bytes(voice.encode(tone(2.0), 24_000, "ogg"))
    samples = voice.decode(ogg)
    assert samples.dtype == np.float32 and abs(len(samples) - 32_000) < 1_600
    assert codec(ogg.read_bytes(), tmp_path)[0] == "opus"
    assert codec(voice.encode(tone(), 24_000, "mp3"), tmp_path)[0] == "mp3"
    with pytest.raises(subprocess.CalledProcessError):
        voice.decode(tmp_path / "missing.ogg")


def test_speak_uses_the_configured_voice_and_listen_joins_segments(tmp_path, monkeypatch):
    used = []

    class Kokoro:
        def create(self, text, voice):
            used.append((text, voice))
            return tone(1.0), 24_000

    class Asr:
        def recognize(self, samples):
            assert samples.dtype == np.float32
            return [
                SimpleNamespace(text=" Valor. "),
                SimpleNamespace(text=" "),
                SimpleNamespace(text="Ship it."),
            ]

    monkeypatch.setattr(voice, "_tts", lambda: Kokoro())
    monkeypatch.setattr(voice, "_asr", lambda: Asr())
    with bridges.configure(tts_voice="bf_alice"):
        data = voice.speak("hello", "ogg")
    assert used == [("hello", "bf_alice")] and codec(data, tmp_path)[0] == "opus"
    (tmp_path / "a.ogg").write_bytes(data)
    assert voice.listen(str(tmp_path / "a.ogg")) == "Valor. Ship it."


def test_the_process_answers_transcribe_and_speech(heard, tmp_path):
    import urllib.error
    import urllib.request

    def post(path, body):
        r = urllib.request.Request(
            f"http://127.0.0.1:{heard.port}{path}",
            json.dumps(body).encode(),
            {"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(r) as answer:
            return answer.headers["Content-Type"], answer.read()

    assert post("/transcribe", {"path": "/x/abcdefgh123"}) == ("application/json; charset=utf-8",
                                                              b'{"text": "words of abcdefgh"}')  # fmt: skip
    kind, data = post("/speech", {"text": "hi", "format": "mp3"})
    assert kind == "audio/mpeg" and codec(data, tmp_path)[0] == "mp3"
    with pytest.raises(urllib.error.HTTPError) as e:
        post("/speech", {"text": "hi", "format": "wav"})
    assert e.value.code == 400


def test_plist_runs_the_process_with_ffmpeg_on_its_path():
    import plistlib

    job = plistlib.loads(voice.plist(python="/py"))
    assert job["Label"] == "com.valor.voice" and job["ProgramArguments"] == [
        "/py",
        "-m",
        "tools.voice",
        "run",
    ]
    assert os.path.dirname(shutil.which("ffmpeg")) in job["EnvironmentVariables"]["PATH"].split(":")


def _cached(*paths: Path) -> bool:
    return all(p.exists() for p in paths)


HF = Path.home() / ".cache" / "huggingface" / "hub" / "models--istupakov--parakeet-tdt-0.6b-v3-onnx"


@pytest.mark.skipif(
    not _cached(HF, Path(settings.tts_model_dir).expanduser() / voice.KOKORO_FILES[0]),
    reason="the models are not fetched (python -m tools.voice fetch)",
)
def test_real_models_round_trip(tmp_path):
    ogg = tmp_path / "said.ogg"
    ogg.write_bytes(voice.speak("Please check the deploy and send me a voice note.", "ogg"))
    heard_text = voice.listen(str(ogg)).lower()
    assert "deploy" in heard_text and "voice note" in heard_text


# -- the attachment as recorded --------------------------------------------------


def test_only_downloaded_audio_is_transcribed(heard):
    audio = {"name": "voice", "mime": "audio/ogg", "bytes": 3, "path": "/in/abcdefgh9"}
    assert bridge.transcribe(audio) == {**audio, "transcript": "words of abcdefgh"}
    for other in (
        {"name": "x.png", "mime": "image/png", "bytes": 3, "path": "/in/1"},
        {"name": "voice", "mime": "audio/ogg", "bytes": 3, "skipped": "download failed: Refused"},
    ):
        assert bridge.transcribe(other) == other
    assert heard.heard == ["/in/abcdefgh9"]


def test_voice_process_down_says_why():
    # tests/conftest.py points the port at 1, where nothing answers.
    got = bridge.transcribe({"name": "voice", "mime": "audio/ogg", "path": "/in/1"})
    assert "Connection refused" in got["transcript_failed"]


def test_a_session_reads_the_transcript_or_why_there_is_none():
    line = intake.chat_line(
        {
            "sent_at": "2026-10-17T10:00:00+00:00", "sender_name": "Tom", "message_id": "9",
            "attachments": [
                {"name": "voice", "mime": "audio/ogg", "bytes": 5, "path": "/in/a", "transcript": "ship it"},
                {"name": "a.m4a", "mime": "audio/mp4", "bytes": 5, "path": "/in/b", "transcript": ""},
                {"name": "c.ogg", "mime": "audio/ogg", "bytes": 5, "path": "/in/c", "transcript_failed": "down"},
                {"name": "d.png", "mime": "image/png", "bytes": 5, "path": "/in/d"},
            ],
        }
    )  # fmt: skip
    assert line.splitlines() == [
        "[2026-10-17 10:00 UTC] Tom (message 9): (no text)",
        "- voice (audio/ogg, 5 bytes): /in/a",
        "  transcript: ship it",
        "- a.m4a (audio/mp4, 5 bytes): /in/b",
        "  transcript: (no speech heard)",
        "- c.ogg (audio/ogg, 5 bytes): /in/c",
        "  not transcribed: down",
        "- d.png (image/png, 5 bytes): /in/d",
    ]


# -- Telegram, over the emulator ---------------------------------------------------


@pytest.fixture
def group(emu, tmp_path):
    g = chat()
    emu.control(chats=[emu_chat(g)])
    with machine(tmp_path, [g]):
        yield g


def inject_file(emu, chat_id, kind, mime, data=b"audio", name="", **kw):
    return emu.inject(
        int(chat_id), kw.pop("text", ""), media={"kind": kind, "name": name, "mime": mime},
        data_b64=base64.b64encode(data).decode(), sender_id=7, sender_name="Tom", **kw,
    )  # fmt: skip


def test_telegram_voice_and_audio_are_transcribed_photo_and_pdf_are_not(emu, dsn, tmp_path, group, heard):
    async def go():
        async with connected(emu.url, dsn, tmp_path):
            inject_file(emu, group, "voice", "audio/ogg", b"v1")
            inject_file(emu, group, "audio", "audio/mpeg", b"a1", name="memo.mp3")
            inject_file(emu, group, "photo", "image/jpeg", b"p1")
            inject_file(emu, group, "document", "application/pdf", b"d1", name="spec.pdf")
            await until(lambda: _count(dsn, group, 4))
            by_mime = {r["attachments"][0]["mime"]: r["attachments"][0] for r in await received(dsn, group)}
            for mime, data in (("audio/ogg", b"v1"), ("audio/mpeg", b"a1")):
                digest = hashlib.sha256(data).hexdigest()
                assert by_mime[mime]["path"].endswith(digest)
                assert by_mime[mime]["transcript"] == f"words of {digest[:8]}"
            assert not {"transcript", "transcript_failed"} & (
                by_mime["image/jpeg"].keys() | by_mime["application/pdf"].keys()
            )

    run(go())


def test_telegram_voice_process_down_still_records_the_voice_note(emu, dsn, tmp_path, group):
    async def go():
        async with connected(emu.url, dsn, tmp_path):
            mid = inject_file(emu, group, "voice", "audio/ogg", b"v2")
            await until(lambda: _count(dsn, group, 1))
            [r] = await received(dsn, group)
            assert r["message_id"] == str(mid) and Path(r["attachments"][0]["path"]).exists()
            assert "Connection refused" in r["attachments"][0]["transcript_failed"]

    run(go())


def test_a_slow_transcription_is_not_cancelled_and_holds_up_nothing(emu, dsn, tmp_path, group):
    go_on = threading.Event()

    calls = []

    def slow(path):
        calls.append(path)
        go_on.wait(20)
        return "a long voice note"

    v = Voice(listen=slow)

    async def go():
        async with connected(emu.url, dsn, tmp_path) as b:
            spoken = inject_file(emu, group, "voice", "audio/ogg", b"v3")
            await until(lambda: (int(group), spoken) in b._inflight)
            typed = emu.inject(int(group), "typed after it", sender_id=7, sender_name="Tom")
            await until(lambda: _count(dsn, group, 1))
            for _ in range(3):  # each tick runs the stall check and a gap fill
                await b.tick()
            assert (int(group), spoken) in b._inflight and not b._inflight[(int(group), spoken)].done()
            assert b._seen.get(group) < spoken
            go_on.set()
            await until(lambda: _count(dsn, group, 2))
            rows = await received(dsn, group)
            assert [r["message_id"] for r in rows] == [str(typed), str(spoken)]
            assert rows[1]["attachments"][0]["transcript"] == "a long voice note"
            assert len(calls) == 1  # never cancelled and taken again

    try:
        run(go())
    finally:
        go_on.set()
        v.stop()


async def _count(dsn, chat_id, n):
    return len(await received(dsn, chat_id)) >= n


# -- Telegram sends: an .ogg is a voice note --------------------------------------


def test_an_ogg_is_sent_as_a_voice_note_with_its_length_and_anything_else_as_a_document():
    data = voice.encode(tone(3.2), 24_000, "ogg")
    assert ogg_seconds(data) == 3
    note = document("up", data, "reply.ogg")
    audio, name = note.attributes
    assert (note.mime_type, audio.voice, audio.duration, name.file_name) == (
        "audio/ogg",
        True,
        3,
        "reply.ogg",
    )
    assert not note.force_file
    doc = document("up", b"%PDF", "report.pdf")
    assert doc.force_file and doc.mime_type == "application/pdf" and len(doc.attributes) == 1


def test_a_sent_voice_note_is_found_by_kind_and_size_without_its_name():
    exp = Expected("file", name="reply.ogg", size=10)
    sent = Msg(1, 5, None, 1, out=True, media=Media("voice", "", "audio/ogg", 10))
    assert _matches(sent, exp)
    assert not _matches(dataclasses.replace(sent, media=Media("voice", "", "audio/ogg", 11)), exp)
    assert not _matches(sent, Expected("file", name="report.pdf", size=10))


def test_a_voice_reply_lost_in_flight_is_found_by_reconcile_not_sent_twice(emu, dsn, tmp_path, group):
    async def go():
        ws = tmp_path / "ws"
        ws.mkdir()
        ogg = ws / "reply.ogg"
        ogg.write_bytes(voice.encode(tone(), 24_000, "ogg"))
        files = [{"path": str(ogg), "sha256": hashlib.sha256(ogg.read_bytes()).hexdigest()}]
        async with connected(emu.url, dsn, tmp_path) as b:
            effect = await release(dsn, send(group, "the same words", files=files), str(ws))
            emu.control(drop_reply_next=True)
            await perform(dsn, b, effect)
            assert (await reconcile(dsn, b, effect)).kind == "done"
            own = emu.own(int(group))
            assert [m["media"]["kind"] if m["media"] else "text" for m in own] == ["text", "voice"]
            sent = (await outcome(dsn, effect))["result"]["sent"]
            assert [s["message_id"] for s in sent] == [str(m["id"]) for m in own]

    run(go())


# -- Slack -----------------------------------------------------------------------


class SlackWire:
    team_id, user_id = "T0ABC", "UVALOR"

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.acked = []

    async def download(self, url, folder):
        folder.mkdir(parents=True, exist_ok=True)
        data = url.encode()
        p = folder / ".part"
        p.write_bytes(data)
        return SimpleNamespace(path=p, sha256=hashlib.sha256(data).hexdigest(), size=len(data))

    async def user_name(self, user):
        return "Tom"

    async def ack(self, envelope_id):
        self.acked.append(envelope_id)


def slack_bridge(tmp_path, transcribe=bridge.transcribe):
    got = []

    class Conn:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    async def receive(conn, inbound):
        got.append(inbound)

    kernel = SimpleNamespace(
        inbound_dir=str(tmp_path / "inbound"), transcribe=transcribe, conn=Conn, receive=receive,
        Inbound=intake.Inbound, owns=lambda channel, chat: True, split_text=None,
    )  # fmt: skip
    wire = SlackWire(tmp_path)
    return SlackBridge(wire, kernel), wire, got


def slack_msg(ts, *files):
    return {
        "type": "message",
        "channel": "C0TEAM",
        "user": "UTOM",
        "ts": ts,
        "text": "",
        "files": list(files),
    }


def test_slack_audio_is_transcribed_an_image_is_not(tmp_path, heard):
    b, _, got = slack_bridge(tmp_path)
    clip = {
        "id": "F1",
        "name": "clip.m4a",
        "mimetype": "audio/mp4",
        "url_private_download": "https://slack.com/a",
    }
    img = {
        "id": "F2",
        "name": "x.png",
        "mimetype": "image/png",
        "url_private_download": "https://slack.com/i",
    }
    run(b._receive("T0ABC/C0TEAM", "C0TEAM", slack_msg("1792000001.000100", clip, img), dm=False))
    [inbound] = got
    a, i = inbound.attachments
    assert a["transcript"] == f"words of {hashlib.sha256(b'https://slack.com/a').hexdigest()[:8]}"
    assert "transcript" not in i and len(heard.heard) == 1


def test_slack_acks_a_slow_voice_note_once_it_is_recorded(tmp_path):
    go_on = threading.Event()

    def slow(a):
        go_on.wait(20)
        return {**a, "transcript": "later"}

    b, wire, got = slack_bridge(tmp_path, transcribe=slow)
    clip = {
        "id": "F1",
        "name": "clip.m4a",
        "mimetype": "audio/mp4",
        "url_private_download": "https://slack.com/a",
    }
    event = {**slack_msg("1792000002.000200", clip), "channel_type": "channel"}

    async def go():
        voice_task = asyncio.create_task(b.handle("E1", event))
        typed = {**slack_msg("1792000003.000300"), "text": "typed", "channel_type": "channel"}
        await b.handle("E2", typed)
        assert wire.acked == ["E2"] and [m.text for m in got] == ["typed"]
        go_on.set()
        await voice_task
        assert wire.acked == ["E2", "E1"] and got[1].attachments[0]["transcript"] == "later"

    try:
        run(go())
    finally:
        go_on.set()


# -- email -----------------------------------------------------------------------


def test_an_email_audio_attachment_is_transcribed(tmp_path, monkeypatch, heard):
    mail = EmailMessage()
    mail["From"], mail["To"], mail["Subject"] = "tom@yuda.me", "valor@yuda.me", "listen"
    mail["Message-ID"] = "<voice-1@yuda.me>"
    mail.set_content("see attached")
    mail.add_attachment(b"memo", maintype="audio", subtype="mpeg", filename="memo.mp3")
    mail.add_attachment(b"%PDF", maintype="application", subtype="pdf", filename="spec.pdf")
    got = []

    async def receive(db, inbound):
        got.append(inbound)

    async def recorded(*a):
        return set()

    for name, fn in {
        "select_inbox": lambda conn: "1", "search": lambda conn, since, owned: [b"7"],
        "headers": lambda conn, uids: {b"7": {"sender": "tom@yuda.me", "message_id": "<voice-1@yuda.me>",
                                              "chat_id": "<voice-1@yuda.me>"}},
        "fetch": lambda conn, u: (mail.as_bytes(), None), "mark_seen": lambda conn, u: None,
    }.items():  # fmt: skip
        monkeypatch.setattr(imap, name, fn)
    monkeypatch.setattr(imap.intake, "owned", lambda channel: ["tom@yuda.me"])
    monkeypatch.setattr(imap.intake, "owns", lambda channel, who: True)
    monkeypatch.setattr(imap.intake, "recorded", recorded)
    monkeypatch.setattr(imap.intake, "receive", receive)
    with bridges.configure(inbound_dir=str(tmp_path / "inbound")):
        assert run(imap.poll(SimpleNamespace(since=None), imaplib.IMAP4, None)) == 1
    memo, pdf = got[0].attachments
    assert memo["transcript"].startswith("words of ") and Path(memo["path"]).read_bytes() == b"memo"
    assert "transcript" not in pdf


# -- valor-tts ---------------------------------------------------------------------


def tts(tmp_path, *args, port):
    """`valor-tts` as a turn runs it: the macOS python, in the environment
    every turn starts from (no OpenAI key), with the turn's voice port."""
    env = {**workspace.environment(), "VALOR_VOICE_PORT": str(port)}
    assert "OPENAI_API_KEY" not in env
    python = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable
    return subprocess.run(
        [python, "-I", str(TOOLS / "valor_tts.py"), *args], env=env, capture_output=True, text=True,
        cwd=tmp_path, check=False,
    )  # fmt: skip


def test_valor_tts_writes_ogg_and_mp3_with_no_key(tmp_path, heard, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-for-turns")
    for name, kind in (("reply.ogg", "opus"), ("reply.mp3", "mp3")):
        out = tts(tmp_path, "Deploy is done.", "-o", str(tmp_path / name), port=heard.port)
        assert out.returncode == 0, out.stderr
        assert codec((tmp_path / name).read_bytes(), tmp_path)[0] == kind
    bad = tts(tmp_path, "x", "-o", str(tmp_path / "reply.wav"), port=heard.port)
    assert bad.returncode == 2 and ".ogg or .mp3" in bad.stderr


def test_valor_tts_says_why_when_the_process_is_down_or_fails(tmp_path):
    down = tts(tmp_path, "x", "-o", str(tmp_path / "r.ogg"), port=1)
    assert down.returncode == 1 and "not answering on 127.0.0.1:1" in down.stderr

    def broken(text, fmt):
        raise RuntimeError("no model")

    v = Voice(speak=broken)
    try:
        failed = tts(tmp_path, "x", "-o", str(tmp_path / "r.ogg"), port=v.port)
    finally:
        v.stop()
    assert failed.returncode == 1 and "answered 500" in failed.stderr
    assert not (tmp_path / "r.ogg").exists()
