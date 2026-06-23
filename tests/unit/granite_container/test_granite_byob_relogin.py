"""Unit tests for the BYOB ``/login`` re-auth recovery driver (issue #1750).

Everything here is browser-free and deterministic:
  * ``BYOBClient`` is exercised with an INJECTED fake subprocess whose
    stdin/stdout are in-memory buffers — no ``tsx``, no Chrome, no Keychain.
  * The ``recover_login`` flow state machine is driven with a fake BYOBClient
    (a stand-in that records calls and returns scripted tab/eval payloads) and
    fixture PTY buffers.

NO real OAuth: every fixture URL uses a clearly-fake host
(``claude.example`` / ``example.test``) so a real authorize endpoint never
appears in the suite (the plan verifies this with a grep over ``tests/``).
"""

from __future__ import annotations

import json
import logging
import time
import unittest
from typing import Any
from unittest.mock import patch

from agent.granite_container import byob_relogin
from agent.granite_container.byob_relogin import (
    BYOBClient,
    BYOBClientError,
    ReloginOutcome,
    _account_guard_ok,
    _extract_paste_url,
    _parse_callback,
    recover_login,
)

# --- Fake-fake URLs (NEVER the real authorize endpoint) -----------------------
FAKE_AUTHORIZE_URL = "https://claude.example/oauth/authorize?state=abc123&code_challenge=xyz"
FAKE_CALLBACK_URL = "https://platform.example.test/oauth/code/callback?code=THECODE&state=THESTATE"
EXPECTED_IDENTITY = "valor@example.test"


# ==============================================================================
# Fake subprocess for BYOBClient transport tests
# ==============================================================================


class _FakeStdin:
    """Captures bytes the client writes, exposing them as parsed JSON lines."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.closed = False

    def write(self, data: str) -> int:
        self.lines.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    def messages(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for chunk in self.lines:
            for ln in chunk.splitlines():
                ln = ln.strip()
                if ln:
                    out.append(json.loads(ln))
        return out


class _FakeProc:
    """Minimal subprocess.Popen stand-in with scriptable stdout responses.

    ``responses`` maps a request ``method`` to a JSON-RPC ``result`` payload.
    The fake echoes the matching ``id`` back so ``_rpc`` resolves it. Requests
    with no scripted response get an empty result.
    """

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.stdin = _FakeStdin()
        self._out_lines: list[str] = []
        self.stdout = self  # readline() lives here
        self._responses = responses or {}
        self.killed = False
        self.waited = False
        self._read_idx = 0

    # -- BYOBClient writes call into stdin, which we intercept to enqueue a
    #    matching response line that readline() will later return.
    def _enqueue_response_for(self, message: dict[str, Any]) -> None:
        if "id" not in message:
            return  # notification — no response
        method = message.get("method", "")
        result = self._responses.get(method, {})
        self._out_lines.append(
            json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}) + "\n"
        )

    def readline(self) -> str:
        if self._read_idx < len(self._out_lines):
            line = self._out_lines[self._read_idx]
            self._read_idx += 1
            return line
        return ""  # EOF

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return 0


def _wire_proc(proc: _FakeProc) -> _FakeProc:
    """Make the fake proc's stdin.write also enqueue a scripted response.

    Real ``_rpc`` writes then reads; with the fake we synchronously enqueue the
    response at write time so the subsequent ``readline()`` finds it.
    """
    original_write = proc.stdin.write

    def _write_and_enqueue(data: str) -> int:
        n = original_write(data)
        for ln in data.splitlines():
            ln = ln.strip()
            if ln:
                proc._enqueue_response_for(json.loads(ln))
        return n

    proc.stdin.write = _write_and_enqueue  # type: ignore[method-assign]
    return proc


# ==============================================================================
# Fake BYOBClient for recover_login flow tests
# ==============================================================================


class FakeBYOBClient:
    """Records driver calls and returns scripted browser state.

    Substituted for the real ``BYOBClient`` inside ``recover_login`` so no
    subprocess / browser is touched. Mirrors the public surface
    ``recover_login`` uses: ``start``, ``close``, ``list_tabs``, ``navigate``,
    ``click``, ``eval``.
    """

    def __init__(
        self,
        *,
        start_ok: bool = True,
        authorize_tab_present: bool = False,
        callback_url: str | None = None,
        eval_results: dict[str, Any] | None = None,
        raise_on: set[str] | None = None,
    ) -> None:
        self.start_ok = start_ok
        # Phase-driven browser state (no fragile call-count scripting):
        #   * before the Authorize click, list_tabs() shows the authorize tab
        #     iff ``authorize_tab_present`` (or navigate() landed us there).
        #   * after the click, the tab leaves the authorize URL (settles) and,
        #     if ``callback_url`` is set, the callback tab appears.
        self._authorize_tab_present = authorize_tab_present
        self._callback_url = callback_url
        self._navigated = False
        self._clicked = False
        self._eval_results = eval_results or {}
        self._raise_on = raise_on or set()
        self.closed = False
        self.calls: list[str] = []
        self.navigated: list[str] = []
        self.clicks: list[dict[str, Any]] = []

    def start(self) -> bool:
        self.calls.append("start")
        return self.start_ok

    def close(self) -> None:
        self.calls.append("close")
        self.closed = True

    def list_tabs(self) -> Any:
        self.calls.append("list_tabs")
        if "list_tabs" in self._raise_on:
            raise BYOBClientError("scripted list_tabs failure")
        on_authorize_page = self._authorize_tab_present or self._navigated
        if not self._clicked:
            return [_authorize_tab()] if on_authorize_page else [_settled_tab()]
        # Post-click: left the authorize URL; callback tab may have appeared.
        tabs = [_settled_tab()]
        if self._callback_url is not None:
            tabs.append({"url": self._callback_url})
        return tabs

    def navigate(self, url: str) -> Any:
        self.calls.append("navigate")
        self.navigated.append(url)
        self._navigated = True
        if "navigate" in self._raise_on:
            raise BYOBClientError("scripted navigate failure")
        return {}

    def click(self, **kwargs: Any) -> Any:
        self.calls.append("click")
        self.clicks.append(kwargs)
        self._clicked = True
        if "click" in self._raise_on:
            raise BYOBClientError("scripted click failure")
        return {}

    def eval(self, js_body: str) -> Any:
        self.calls.append("eval")
        if "eval" in self._raise_on:
            raise BYOBClientError("scripted eval failure")
        # Account-guard eval: "Logged in as ..." → identity string.
        if "Logged in as" in js_body:
            return self._eval_results.get("identity")
        # Hydration eval: "Authorize" button present → bool.
        if "Authorize" in js_body:
            return self._eval_results.get("authorize_present", True)
        return None


class FakePTY:
    """Records writes; serves scripted read_until_idle buffers (paste sentinel)."""

    def __init__(self, idle_buffers: list[str] | None = None) -> None:
        self.writes: list[str] = []
        self._idle_buffers = idle_buffers or ["Paste code here if prompted >"]
        self._idx = 0

    def write(self, text: str) -> None:
        self.writes.append(text)

    def read_until_idle(self, *, min_content_bytes: int = 0, timeout_s: float = 1.0) -> Any:
        if self._idx < len(self._idle_buffers):
            buf = self._idle_buffers[self._idx]
            self._idx += 1
        else:
            buf = self._idle_buffers[-1] if self._idle_buffers else ""

        class _R:
            turn_buffer = buf
            buffer = buf

        return _R()


def _authorize_tab() -> dict[str, Any]:
    return {"url": FAKE_AUTHORIZE_URL, "title": "Authorize"}


def _settled_tab() -> dict[str, Any]:
    return {"url": "https://claude.example/new", "title": "Claude"}


def _far_deadline() -> float:
    return time.monotonic() + 600.0


# ==============================================================================
# BYOBClient transport + lifecycle
# ==============================================================================


class TestBYOBClientHandshakeAndRoundTrip(unittest.TestCase):
    def test_start_handshake_and_tool_call(self) -> None:
        """Injected proc: start() does the handshake, _call_tool round-trips."""
        proc = _wire_proc(
            _FakeProc(
                responses={
                    "initialize": {"protocolVersion": "2024-11-05"},
                    "tools/call": {
                        "content": [{"type": "text", "text": json.dumps([_authorize_tab()])}]
                    },
                }
            )
        )
        client = BYOBClient(proc=proc)  # type: ignore[arg-type]
        # An injected proc is treated as already-spawned; drive the handshake
        # directly to exercise the initialize → notifications/initialized
        # round-trip against the in-memory transport (start() would re-spawn).
        client._handshake()

        # Handshake: initialize request + notifications/initialized notice.
        sent = proc.stdin.messages()
        methods = [m.get("method") for m in sent]
        self.assertIn("initialize", methods)
        self.assertIn("notifications/initialized", methods)
        # The notification must carry no id.
        notif = next(m for m in sent if m.get("method") == "notifications/initialized")
        self.assertNotIn("id", notif)

        # A tools/call round-trip decodes the JSON text payload.
        tabs = client.list_tabs()
        self.assertEqual(tabs, [_authorize_tab()])

    def test_start_idempotent_when_proc_injected(self) -> None:
        proc = _wire_proc(_FakeProc(responses={"initialize": {}}))
        client = BYOBClient(proc=proc)  # type: ignore[arg-type]
        # Injected + already-started: start() returns True without re-handshake.
        self.assertTrue(client.start())
        self.assertTrue(client.start())

    def test_close_is_idempotent_and_finally_safe(self) -> None:
        proc = _wire_proc(_FakeProc(responses={"initialize": {}}))
        client = BYOBClient(proc=proc)  # type: ignore[arg-type]
        client.start()
        client.close()
        self.assertTrue(proc.killed)
        self.assertTrue(proc.waited)
        # Second close is a no-op (does not raise).
        client.close()

    def test_rpc_error_object_raises_byob_client_error(self) -> None:
        """A JSON-RPC error object surfaces as BYOBClientError (caught upstream)."""
        proc = _FakeProc()
        # Inject a stdin that enqueues an *error* response for tools/call.
        original_write = proc.stdin.write

        def _write_enqueue_error(data: str) -> int:
            n = original_write(data)
            for ln in data.splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                msg = json.loads(ln)
                if "id" not in msg:
                    continue
                if msg.get("method") == "tools/call":
                    err = {"code": -1, "message": "boom"}
                    proc._out_lines.append(
                        json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": err}) + "\n"
                    )
                else:
                    proc._out_lines.append(
                        json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}) + "\n"
                    )
            return n

        proc.stdin.write = _write_enqueue_error  # type: ignore[method-assign]
        client = BYOBClient(proc=proc)  # type: ignore[arg-type]
        client.start()
        with self.assertRaises(BYOBClientError):
            client.list_tabs()

    def test_rpc_eof_raises_byob_client_error(self) -> None:
        """Server closing stdout (EOF) during a call raises BYOBClientError."""
        proc = _FakeProc()  # no responses scripted, readline() returns "" → EOF
        client = BYOBClient(proc=proc)  # type: ignore[arg-type]
        # Mark started so start()'s handshake is skipped (proc injected).
        with self.assertRaises(BYOBClientError):
            client._rpc("tools/call", {"name": "browser_list_tabs", "arguments": {}}, timeout=2.0)


class TestBYOBClientSpawnFailure(unittest.TestCase):
    def test_spawn_failure_returns_false_and_logs_warning(self) -> None:
        """Missing tsx/BYOB: start() logs a warning and returns False (no raise)."""
        client = BYOBClient()  # no injected proc → real spawn path
        with patch(
            "agent.granite_container.byob_relogin.subprocess.Popen",
            side_effect=FileNotFoundError("tsx not found"),
        ):
            with self.assertLogs("agent.granite_container.byob_relogin", level="WARNING") as cm:
                ok = client.start()
        self.assertFalse(ok)
        self.assertTrue(any("failed to spawn" in m for m in cm.output))

    def test_recover_login_degrades_when_client_start_fails(self) -> None:
        """recover_login with a client that fails to start → failure outcome, no raise."""
        fake = FakeBYOBClient(start_ok=False)
        with patch.object(byob_relogin, "BYOBClient", lambda: fake):
            outcome = recover_login(
                FakePTY(), "", deadline=_far_deadline(), expected_identity=EXPECTED_IDENTITY
            )
        self.assertIsInstance(outcome, ReloginOutcome)
        self.assertFalse(outcome.succeeded)
        self.assertIsNone(outcome.flow)
        # The client is always closed in finally even when start failed.
        self.assertTrue(fake.closed)


# ==============================================================================
# recover_login flow state machine
# ==============================================================================


class TestRecoverLoginFlow1(unittest.TestCase):
    def test_flow1_success_clicks_and_presses_enter(self) -> None:
        """Flow 1: auto-opened authorize tab → click Authorize → Enter → success(1)."""
        fake = FakeBYOBClient(
            authorize_tab_present=True,
            eval_results={"identity": EXPECTED_IDENTITY, "authorize_present": True},
        )
        pty = FakePTY()
        with (
            patch.object(byob_relogin, "BYOBClient", lambda: fake),
            patch.object(byob_relogin, "POLL_INTERVAL_S", 0.01),
        ):
            outcome = recover_login(
                pty, "", deadline=_far_deadline(), expected_identity=EXPECTED_IDENTITY
            )
        self.assertTrue(outcome.succeeded, outcome.reason)
        self.assertEqual(outcome.flow, 1)
        self.assertIn({"text": "Authorize"}, fake.clicks)
        self.assertIn("\r", pty.writes)
        self.assertTrue(fake.closed)

    def test_flow1_account_guard_mismatch_aborts(self) -> None:
        """Flow 1: page identity != expected → abort to failure (no click)."""
        fake = FakeBYOBClient(
            authorize_tab_present=True,
            eval_results={"identity": "intruder@example.test", "authorize_present": True},
        )
        pty = FakePTY()
        with (
            patch.object(byob_relogin, "BYOBClient", lambda: fake),
            patch.object(byob_relogin, "POLL_INTERVAL_S", 0.01),
        ):
            outcome = recover_login(
                pty, "", deadline=_far_deadline(), expected_identity=EXPECTED_IDENTITY
            )
        self.assertFalse(outcome.succeeded)
        self.assertIn("account guard", outcome.reason)
        self.assertEqual(fake.clicks, [])
        self.assertNotIn("\r", pty.writes)


class TestRecoverLoginFlow2(unittest.TestCase):
    def test_flow2_paste_fallback_success(self) -> None:
        """Flow 2: no auto tab, paste URL in buffer → reconstruct → callback →
        parse {code}#{state} → write to PTY → success(2)."""
        buffer = (
            "Browser didn't open? Use the url below (c to copy)\n"
            "https://claude.exam\n"
            "ple/oauth/authorize?sta\n"
            "te=THESTATE&code_challenge=xyz\n"
            "Paste code here if prompted >\n"
        )
        # No auto-opened authorize tab (flow 1 fails), so recovery reconstructs
        # the paste URL, navigates (→ on authorize page), clicks Authorize, then
        # the callback URL appears.
        fake = FakeBYOBClient(
            authorize_tab_present=False,
            callback_url=FAKE_CALLBACK_URL,
            eval_results={"identity": EXPECTED_IDENTITY, "authorize_present": True},
        )
        pty = FakePTY(idle_buffers=["Paste code here if prompted >"])
        # Shrink the flow-1 auto-tab budget so the test does not spin 15s.
        with (
            patch.object(byob_relogin, "BYOBClient", lambda: fake),
            patch.object(byob_relogin, "AUTO_OPEN_TAB_BUDGET_S", 0.2),
            patch.object(byob_relogin, "POLL_INTERVAL_S", 0.01),
        ):
            outcome = recover_login(
                pty, buffer, deadline=_far_deadline(), expected_identity=EXPECTED_IDENTITY
            )
        self.assertTrue(outcome.succeeded, outcome.reason)
        self.assertEqual(outcome.flow, 2)
        self.assertEqual(fake.navigated[0].count("authorize"), 1)
        # The pasted payload is {code}#{state}.
        self.assertIn("THECODE#THESTATE", pty.writes)
        self.assertTrue(fake.closed)

    def test_flow2_garbled_buffer_no_url_fails(self) -> None:
        """No auto tab and no extractable URL → failure with logged reason (no crash)."""
        fake = FakeBYOBClient(authorize_tab_present=False)
        with (
            patch.object(byob_relogin, "BYOBClient", lambda: fake),
            patch.object(byob_relogin, "AUTO_OPEN_TAB_BUDGET_S", 0.2),
            patch.object(byob_relogin, "POLL_INTERVAL_S", 0.01),
        ):
            outcome = recover_login(
                FakePTY(),
                "garbled buffer with no url at all",
                deadline=_far_deadline(),
                expected_identity=EXPECTED_IDENTITY,
            )
        self.assertFalse(outcome.succeeded)
        self.assertIn("paste URL", outcome.reason)
        self.assertTrue(fake.closed)

    def test_flow2_malformed_callback_missing_code_fails(self) -> None:
        """Callback URL missing code/state → failure, not an exception."""
        buffer = (
            "https://claude.example/oauth/authorize?state=S&code_challenge=x\nPaste code here >\n"
        )
        fake = FakeBYOBClient(
            authorize_tab_present=False,
            callback_url="https://platform.example.test/oauth/code/callback?state=ONLYSTATE",
            eval_results={"identity": EXPECTED_IDENTITY, "authorize_present": True},
        )
        with (
            patch.object(byob_relogin, "BYOBClient", lambda: fake),
            patch.object(byob_relogin, "AUTO_OPEN_TAB_BUDGET_S", 0.2),
            patch.object(byob_relogin, "POLL_INTERVAL_S", 0.01),
        ):
            outcome = recover_login(
                FakePTY(), buffer, deadline=_far_deadline(), expected_identity=EXPECTED_IDENTITY
            )
        self.assertFalse(outcome.succeeded)
        self.assertIn("code/state", outcome.reason)


class TestRecoverLoginLoggedOut(unittest.TestCase):
    def test_zero_authorize_tabs_no_paste_url_fails(self) -> None:
        """Flow 1 finds zero authorize tabs within deadline → falls through to
        flow 2; with no paste URL it degrades to failure (no crash)."""
        fake = FakeBYOBClient(authorize_tab_present=False)  # never an /authorize tab
        with (
            patch.object(byob_relogin, "BYOBClient", lambda: fake),
            patch.object(byob_relogin, "AUTO_OPEN_TAB_BUDGET_S", 0.2),
            patch.object(byob_relogin, "POLL_INTERVAL_S", 0.01),
        ):
            outcome = recover_login(
                FakePTY(), "", deadline=_far_deadline(), expected_identity=EXPECTED_IDENTITY
            )
        self.assertFalse(outcome.succeeded)
        self.assertIsNone(outcome.flow)
        self.assertTrue(fake.closed)


class TestRecoverLoginNeverRaises(unittest.TestCase):
    def test_handshake_or_rpc_error_does_not_escape(self) -> None:
        """A JSON-RPC / list_tabs error inside recovery → failure, not exception."""
        fake = FakeBYOBClient(authorize_tab_present=False, raise_on={"list_tabs"})
        with (
            patch.object(byob_relogin, "BYOBClient", lambda: fake),
            patch.object(byob_relogin, "AUTO_OPEN_TAB_BUDGET_S", 0.2),
            patch.object(byob_relogin, "POLL_INTERVAL_S", 0.01),
        ):
            outcome = recover_login(
                FakePTY(), "", deadline=_far_deadline(), expected_identity=EXPECTED_IDENTITY
            )
        self.assertIsInstance(outcome, ReloginOutcome)
        self.assertFalse(outcome.succeeded)
        self.assertTrue(fake.closed)


# ==============================================================================
# Account guard (fail-closed, Risk 1)
# ==============================================================================


class TestAccountGuard(unittest.TestCase):
    def test_match_passes(self) -> None:
        fake = FakeBYOBClient(eval_results={"identity": EXPECTED_IDENTITY})
        self.assertTrue(_account_guard_ok(fake, EXPECTED_IDENTITY))  # type: ignore[arg-type]

    def test_mismatch_fails(self) -> None:
        fake = FakeBYOBClient(eval_results={"identity": "other@example.test"})
        self.assertFalse(_account_guard_ok(fake, EXPECTED_IDENTITY))  # type: ignore[arg-type]

    def test_missing_identity_treated_as_mismatch(self) -> None:
        """MISSING/EMPTY page identity → mismatch → fail (NOT a skip)."""
        fake = FakeBYOBClient(eval_results={"identity": None})
        self.assertFalse(_account_guard_ok(fake, EXPECTED_IDENTITY))  # type: ignore[arg-type]

    def test_empty_string_identity_treated_as_mismatch(self) -> None:
        fake = FakeBYOBClient(eval_results={"identity": "   "})
        self.assertFalse(_account_guard_ok(fake, EXPECTED_IDENTITY))  # type: ignore[arg-type]

    def test_expected_identity_none_fails_closed(self) -> None:
        """expected_identity=None → cannot confirm → fail closed (even if page
        renders a valid identity)."""
        fake = FakeBYOBClient(eval_results={"identity": "anyone@example.test"})
        self.assertFalse(_account_guard_ok(fake, None))  # type: ignore[arg-type]


# ==============================================================================
# URL extraction + callback parse helpers
# ==============================================================================


class TestUrlExtraction(unittest.TestCase):
    def test_dewrap_line_wrapped_url(self) -> None:
        buffer = (
            "Browser didn't open? Use the url below\n"
            "https://claude.exam\n"
            "ple/oauth/authorize?sta\n"
            "te=THESTATE\n"
            "Paste code here >\n"
        )
        url = _extract_paste_url(buffer)
        self.assertIsNotNone(url)
        assert url is not None
        self.assertEqual(url, "https://claude.example/oauth/authorize?state=THESTATE")

    def test_no_url_returns_none(self) -> None:
        self.assertIsNone(_extract_paste_url("nothing useful here"))

    def test_url_without_authorize_returns_none(self) -> None:
        # A claude URL that is not an authorize URL is not a valid paste URL.
        buffer = "https://claude.example/settings\nPaste code here >\n"
        self.assertIsNone(_extract_paste_url(buffer))


class TestCallbackParse(unittest.TestCase):
    def test_parse_code_and_state(self) -> None:
        parsed = _parse_callback(FAKE_CALLBACK_URL)
        self.assertEqual(parsed, ("THECODE", "THESTATE"))

    def test_missing_code_returns_none(self) -> None:
        self.assertIsNone(
            _parse_callback("https://platform.example.test/oauth/code/callback?state=S")
        )

    def test_missing_state_returns_none(self) -> None:
        self.assertIsNone(
            _parse_callback("https://platform.example.test/oauth/code/callback?code=C")
        )

    def test_garbage_url_returns_none(self) -> None:
        self.assertIsNone(_parse_callback("not a url at all"))


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    unittest.main(verbosity=2)
