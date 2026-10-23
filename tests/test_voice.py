"""Voice messages both ways: every audio attachment is transcribed by the
voice process before it is recorded, and an `.ogg` a turn sends goes to
Telegram as a voice note (spoken by the present skill's `tts.py`, from the
voice every turn's env names)."""

import asyncio
import base64
import dataclasses
import hashlib
import imaplib
import json
import os
import shutil
import subprocess
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
from core import bridge, intake
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
run = asyncio.run


def tone(seconds: float = 2.0, rate: int = 24_000) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def opus(samples: np.ndarray, rate: int = 24_000, fmt: str = "ogg") -> bytes:
    """Samples as an Ogg/Opus file, as the present skill's `tts.py` writes one (or MP3)."""
    codec_args = ["-c:a", "libopus", "-f", "ogg"] if fmt == "ogg" else ["-c:a", "libmp3lame", "-f", "mp3"]
    return subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "f32le", "-ar", str(rate), "-ac", "1", "-i", "-", *codec_args, "-"],
        input=samples.tobytes(), capture_output=True, check=True,
    ).stdout  # fmt: skip


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

    def __init__(self, listen=None):
        self.heard: list[str] = []
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        def heard(path):
            self.heard.append(path)
            return f"words of {Path(path).name[:8]}"

        async def start():
            self.runner = web.AppRunner(voice.app(listen=listen or heard))
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


def test_decode_any_audio_to_16k_mono(tmp_path):
    for name, fmt in (("v.ogg", "ogg"), ("v.mp3", "mp3")):
        f = tmp_path / name
        f.write_bytes(opus(tone(2.0), fmt=fmt))
        samples = voice.decode(f)
        assert samples.dtype == np.float32 and abs(len(samples) - 32_000) < 1_600
    with pytest.raises(subprocess.CalledProcessError):
        voice.decode(tmp_path / "missing.ogg")


def test_listen_joins_the_segments_heard(tmp_path, monkeypatch):
    class Asr:
        def recognize(self, samples):
            assert samples.dtype == np.float32
            return [
                SimpleNamespace(text=" Valor. "),
                SimpleNamespace(text=" "),
                SimpleNamespace(text="Ship it."),
            ]

    monkeypatch.setattr(voice, "_asr", lambda: Asr())
    (tmp_path / "a.ogg").write_bytes(opus(tone(1.0)))
    assert voice.listen(str(tmp_path / "a.ogg")) == "Valor. Ship it."


def test_the_process_answers_transcribe(heard):
    import urllib.request

    r = urllib.request.Request(
        f"http://127.0.0.1:{heard.port}/transcribe", json.dumps({"path": "/x/abcdefgh123"}).encode(),
        {"Content-Type": "application/json"},
    )  # fmt: skip
    with urllib.request.urlopen(r) as answer:
        assert json.load(answer) == {"text": "words of abcdefgh"}


def test_plist_runs_the_process_with_ffmpeg_on_its_path():
    import plistlib

    job = plistlib.loads(voice.plist(python="/py"))
    assert job["Label"] == "com.valor.voice" and job["ProgramArguments"] == [
        settings.launcher,
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
    not (HF.exists() and shutil.which("say")), reason="Parakeet is not fetched (python -m tools.voice fetch)"
)
def test_real_parakeet_hears_speech(tmp_path):
    aiff, ogg = tmp_path / "said.aiff", tmp_path / "said.ogg"
    subprocess.run(["say", "-o", str(aiff), "Please check the deploy and send me a voice note."], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(aiff), "-c:a", "libopus", str(ogg)], check=True)
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


def test_a_hung_voice_process_times_out_and_says_so(monkeypatch):
    import socket

    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen()  # accepts the connection and never answers
    monkeypatch.setattr(bridge, "TRANSCRIBE_TIMEOUT_S", 0.5)
    try:
        with bridges.configure(voice_port=held.getsockname()[1]):
            got = bridge.transcribe({"name": "voice", "mime": "audio/ogg", "path": "/in/1"})
    finally:
        held.close()
    assert "timed out" in got["transcript_failed"]


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
    data = opus(tone(3.2))
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
        ogg.write_bytes(opus(tone()))
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

    async def replies(self, channel, ts, cursor=None):
        return [{"ts": ts, "text": "<@UVALOR> look"}], None

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


def test_slack_labels_valors_own_tag_in_a_message_and_its_thread(tmp_path):
    """So the kernel's mention rule sees a Slack tag of Valor; anyone
    else's tag, and a message with none, is kept as Slack stored it."""
    b, _, got = slack_bridge(tmp_path)
    texts = ["<@UVALOR> hi, ask <@UTOM>", "no tag here", "<@UVALOR|valor> already"]
    for n, text in enumerate(texts):
        run(
            b._receive(
                "T0ABC/C0TEAM", "C0TEAM", {**slack_msg(f"179200001{n}.000100"), "text": text}, dm=False
            )
        )
    reply = {**slack_msg("1792000020.000100"), "text": "and?", "thread_ts": "1792000019.000100"}
    run(b._receive("T0ABC/C0TEAM", "C0TEAM", reply, dm=False))
    assert [m.text for m in got] == [
        "<@UVALOR|valor> hi, ask <@UTOM>",
        "no tag here",
        "<@UVALOR|valor> already",
        "and?",
    ]
    assert got[3].thread == [{"id": "1792000019.000100", "text": "<@UVALOR|valor> look", "attachments": []}]


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
