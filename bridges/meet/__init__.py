"""Google Meet's page: Valor in a call as itself (docs/plans/meet.md).

Chrome, through Playwright, on this Mac's call profile
(`settings.meet_profile`, signed in once as Valor's own Google account),
headless and muted, camera off. Attendee's page payloads (`vendor/`, ELv2,
at Attendee 5acfb99, unmodified, with protobuf.js and pako) read the call:
each person's audio apart, the call's chat, and who is in it; they give
the page a virtual microphone and type into the chat. `page.js`, ours,
plays speech into that microphone, stops it at once, and says where the
page stands.

The payloads send what they read over a WebSocket to `127.0.0.1`
(`Socket`), which Chrome's local network checks would refuse from Meet's
page, so the call's Chrome runs with them off. Every frame runs the
payloads; only the top frame's socket is read.

I/O only: what is heard, said and posted is decided in `core/call.py`.
"""

import asyncio
import base64
import json
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from aiohttp import WSMsgType, web

HERE = Path(__file__).resolve().parent
PAYLOADS = ("vendor/protobuf.min.js", "vendor/pako.min.js", "vendor/shared_chromedriver_payload.js",
            "vendor/google_meet_chromedriver_payload.js", "page.js")  # fmt: skip
JSON, PER_PARTICIPANT_AUDIO = 1, 5
CHROME_ARGS = (
    "--disable-blink-features=AutomationControlled",
    "--use-fake-ui-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
    "--mute-audio",
    "--disable-features=LocalNetworkAccessChecks,LocalNetworkAccessChecksWebSockets",
)
JOIN = (
    'xpath=//button[.//span[text()="Join now" or text()="Ask to join" or text()="Ask to join anyway" or '
    'text()="Join anyway" or text()="Join here too" or text()="Join the call now"]]'
)


def script(port: int) -> str:
    """What every frame of the page runs before Meet's own code."""
    data = {
        "websocketPort": port,
        "sendPerParticipantAudio": True,
        "collectCaptions": False,
        "recordingView": "speaker_view",
        "perParticipantRealtimeVideoConfiguration": {
            "webcam_configuration": {"enabled": False},
            "screenshare_configuration": {"enabled": False},
        },
    }
    meet = {"modifyDomForVideoRecording": False, "disableIncomingVideo": True}
    parts = [f"window.initialData = {json.dumps(data)};", f"window.googleMeetInitialData = {json.dumps(meet)};"]
    return "\n".join(parts + [(HERE / name).read_text() for name in PAYLOADS])


class Socket:
    """The page's socket: each person's audio (float32, at the rate the
    page last said), the call's chat, and who is in it, handed to `hear`,
    `chat` and `people`. `up` is whether the top frame's socket is open."""

    def __init__(
        self,
        hear: Callable[[str, np.ndarray, int], None],
        chat: Callable[[str, str], None],
        people: Callable[[dict[str, Any]], None],
    ):
        self.hear, self.chat, self.people = hear, chat, people
        self.rate = 48_000
        self.up = False
        self.port = 0
        self._runner: web.AppRunner | None = None

    async def start(self) -> int:
        app = web.Application()
        app.router.add_get("/{tail:.*}", self._socket)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        return self.port

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def _socket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(max_msg_size=0)
        await ws.prepare(request)
        top = False
        try:
            async for msg in ws:
                if msg.type != WSMsgType.BINARY or len(msg.data) < 4:
                    continue
                kind = struct.unpack_from("<i", msg.data)[0]
                if kind == JSON:
                    said = json.loads(msg.data[4:])
                    if said.get("type") == "ValorHello":
                        if not said.get("top"):
                            break
                        top = self.up = True
                    elif top:
                        self.read(said)
                elif kind == PER_PARTICIPANT_AUDIO and top:
                    size = msg.data[4]
                    who = msg.data[5 : 5 + size].decode()
                    self.hear(who, np.frombuffer(msg.data[5 + size :], dtype=np.float32), self.rate)
        finally:
            if top:
                self.up = False
        return ws

    def read(self, said: dict[str, Any]) -> None:
        kind = said.get("type")
        if kind == "AudioFormatUpdate":
            self.rate = int((said.get("format") or {}).get("sampleRate") or self.rate)
        elif kind == "ChatMessage":
            self.chat(str(said.get("participant_uuid") or ""), str(said.get("text") or ""))
        elif kind == "UsersUpdate":
            self.people(said)


class Page:
    """The call's Chrome window, one per call."""

    def __init__(self, profile: str, *, headless: bool = True):
        self.profile, self.headless = profile, headless
        self._playwright = self._context = self.page = None

    async def open(self, url: str, port: int) -> None:
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            self.profile,
            channel="chrome",
            headless=self.headless,
            ignore_default_args=["--enable-automation", "--use-mock-keychain"],
            args=list(CHROME_ARGS),
            permissions=["microphone", "camera"],
        )
        await self._context.add_init_script(script(port))
        self.page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        await self.page.goto(url, wait_until="domcontentloaded")

    async def join(self, timeout_s: float = 60) -> None:
        """Camera off, then the join button, whichever Meet shows."""
        button = self.page.locator('button[aria-label="Turn off camera"]')
        if await button.count():
            await button.first.click()
        await self.page.locator(JOIN).first.click(timeout=timeout_s * 1000)

    async def ready(self) -> None:
        """Once in the call: the payloads start reading it and open the chat."""
        await self.page.evaluate("window.ws.enableMediaSending()")

    async def state(self) -> dict[str, Any]:
        return await self.page.evaluate("window.valorState()")

    async def post(self, text: str) -> bool:
        return bool(await self.page.evaluate("(t) => window.sendChatMessage(t)", text))

    async def play(self, pcm: np.ndarray, rate: int) -> dict[str, Any]:
        """Speak `pcm` (float32 mono at `rate`); returns `played`, `total`
        (seconds) and `cut`, once it played out or `stop` cut it."""
        ints = (np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes()
        return await self.page.evaluate("([b, r]) => window.valorPlay(b, r)", [base64.b64encode(ints).decode(), rate])

    async def stop(self) -> None:
        await self.page.evaluate("window.valorStop()")

    async def leave(self) -> None:
        button = self.page.locator('button[aria-label="Leave call"]')
        if await button.count():
            await button.first.click()
            await asyncio.sleep(1)

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._playwright is not None:
            await self._playwright.stop()
