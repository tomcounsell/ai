"""Google Meet's page: Valor in a call as itself (docs/plans/meet.md).

Chrome, through Playwright, on this Mac's call profile
(`settings.meet_profile`, signed in once as Valor's own Google account),
headless and muted, camera off. Attendee's page payloads (`vendor/`, ELv2,
at Attendee 5acfb99, unmodified, with protobuf.js and pako) read the call:
each person's audio apart, the call's chat, and who is in it; they give
the page a virtual microphone and type into the chat. `page.js`, ours,
streams speech into that microphone, stops it at once, and says where the
page stands.

The payloads send what they read over a WebSocket to `127.0.0.1`
(`Socket`), which Chrome's local network checks would refuse from Meet's
page, so the call's Chrome runs with them off. Every frame runs the
payloads; only the top frame's socket is read.

I/O only: what is heard, said and posted is decided in `core/call.py`.
"""

import asyncio
import base64
import contextlib
import json
import os
import signal
import struct
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import psutil
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
# Meet's join buttons, the first shown wins. Never "Switch here", which
# would take the call from another device of Valor's account.
JOIN = ("Join now", "Ask to join", "Ask to join anyway", "Join anyway", "Join the call now", "Join here too")
# Navigations under one `Page.evaluate` waited out before it fails.
NAVIGATIONS = 3
# A shown button whose words end with one of `labels` (an icon's name comes
# first), clicked; else "Other ways to join" opened, for the next try.
CLICK_JOIN = """(labels) => {
    const shown = [...document.querySelectorAll('button')].filter((b) => b.getClientRects().length);
    for (const label of labels) {
        const button = shown.find((b) => b.innerText.trim().endsWith(label));
        if (button) { button.click(); return label; }
    }
    const more = shown.find((b) => b.getAttribute('aria-label') === 'Other ways to join');
    if (more && more.getAttribute('aria-expanded') !== 'true') more.click();
    return null;
}"""


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
    parts = [
        f"window.initialData = {json.dumps(data)};",
        f"window.googleMeetInitialData = {json.dumps(meet)};",
    ]
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
        elif kind == "ValorMedia":
            print(
                f"media connection {said.get('connection')}: {said.get('state')}", file=sys.stderr, flush=True
            )


def holder(profile: str) -> int | None:
    """The live Chrome holding `profile`: the pid its `SingletonLock`
    (`host-pid`) names, when that process was started on this profile. A
    stale lock, or a pid now another process, is None."""
    profile = os.path.expanduser(profile)
    try:
        pid = int(os.readlink(Path(profile, "SingletonLock")).rsplit("-", 1)[1])
        argv = psutil.Process(pid).cmdline()
    except OSError, IndexError, ValueError, psutil.Error:
        return None
    paths = {os.path.abspath(profile).rstrip("/"), os.path.realpath(profile).rstrip("/")}
    return pid if any(a.rstrip("/") in {f"--user-data-dir={p}" for p in paths} for a in argv) else None


def a_call(pid: int) -> bool:
    """Whether the Chrome `pid` is a call's: Playwright starts it with
    `--remote-debugging-pipe`, which Chrome opened by hand never has."""
    try:
        return "--remote-debugging-pipe" in psutil.Process(pid).cmdline()
    except psutil.Error:
        return False


async def quit_holder(profile: str, wait_s: float = 15) -> None:
    """Quit a plain Chrome left open on `profile`, the window Tom signed it
    in from (all three Macs, 2026-11-05): SIGTERM, Chrome's clean quit,
    which saves its cookies, then up to `wait_s` for it to let the profile
    go. Another call's Chrome is refused."""
    pid = holder(profile)
    if pid is None:
        return
    if a_call(pid):
        raise RuntimeError(f"this Mac's call profile is in use by another call's Chrome (pid {pid})")
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    for _ in range(int(wait_s * 10)):
        if holder(profile) is None:
            return
        await asyncio.sleep(0.1)


class Page:
    """The call's Chrome window, one per call."""

    def __init__(self, profile: str, *, headless: bool = True):
        self.profile, self.headless = profile, headless
        self._playwright = self._context = self.page = None

    async def open(self, url: str, port: int) -> None:
        from playwright.async_api import async_playwright

        await quit_holder(self.profile)
        self._playwright = await async_playwright().start()
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                self.profile,
                channel="chrome",
                headless=self.headless,
                ignore_default_args=["--enable-automation", "--use-mock-keychain"],
                args=list(CHROME_ARGS),
                permissions=["microphone", "camera"],
            )
        except Exception:
            # A Chrome that would not quit, or started since.
            pid = holder(self.profile)
            if pid is None:
                raise
            raise RuntimeError(
                f"this Mac's call profile is open in another Chrome (pid {pid}); quit it with ⌘Q and ask again"
            ) from None
        await self._context.add_init_script(script(port))
        self.page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        await self.page.goto(url, wait_until="domcontentloaded")

    async def evaluate(self, expression: str, arg: Any = None) -> Any:
        """`page.evaluate`, run again on the new document when Meet
        navigated under it: a rejoin of a call Valor had left ended its
        call process with "Execution context was destroyed" (2026-11-05).
        The payloads are init scripts, so the new document has them."""
        from playwright.async_api import Error

        for _ in range(NAVIGATIONS):
            try:
                return await self.page.evaluate(expression, arg)
            except Error as exc:
                if "Execution context was destroyed" not in str(exc):
                    raise
                await self.page.wait_for_load_state("domcontentloaded")
        return await self.page.evaluate(expression, arg)

    async def join(self, timeout_s: float = 60) -> str:
        """Camera off, then the join button, whichever Meet shows; the
        button's label."""
        camera = self.page.locator('button[aria-label="Turn off camera"]')
        if await camera.count() and await camera.first.is_visible():
            await camera.first.click()
        for _ in range(int(timeout_s * 2)):
            clicked = await self.evaluate(CLICK_JOIN, list(JOIN))
            if clicked:
                return clicked
            await asyncio.sleep(0.5)
        raise TimeoutError("Meet showed no join button")

    async def ready(self) -> None:
        """Once in the call: the payloads start reading it and open the chat."""
        await self.evaluate("window.ws.enableMediaSending()")

    async def state(self) -> dict[str, Any]:
        return await self.evaluate("window.valorState()")

    async def post(self, text: str) -> bool:
        return bool(await self.evaluate("(t) => window.sendChatMessage(t)", text))

    async def stream(self, pcm: bytes, rate: int, fresh: bool) -> float:
        """Queue `pcm` (16-bit mono at `rate`) after what is playing;
        `fresh` starts a new turn. The seconds still queued."""
        return float(await self.evaluate(
            "([b, r, f]) => window.valorStream(b, r, f)", [base64.b64encode(pcm).decode(), rate, fresh]
        ))  # fmt: skip

    async def stop(self) -> float:
        """Stop the turn at once; the seconds of it that played."""
        return float(await self.evaluate("window.valorStop()") or 0.0)

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
