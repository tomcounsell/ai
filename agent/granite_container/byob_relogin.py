"""Deterministic granite ``/login`` re-auth recovery via a pure-Python BYOB driver.

When a granite ``claude`` TUI's OAuth subscription token expires or rotates
mid-run, the PTY paints a login frame. ``startup_parser`` detects it and emits
``StartupEvent.LOGIN_PROMPT``; today the container passively waits out the 600s
ceiling and alerts a human. This module makes recovery **autonomous**: it drives
the already-logged-in real Chrome (via BYOB's MCP server) through the Claude
Code OAuth consent — a fixed, deterministic recipe with NO LLM in the loop.

The driver speaks BYOB's MCP server directly over stdio JSON-RPC (no ``claude``
session, no shared-credential bootstrap deadlock). The ``claude`` PTY itself
owns the token exchange / Keychain write; this module only completes the
browser side and either presses Enter (localhost callback) or pastes the
captured ``{code}#{state}`` into the PTY (paste fallback).

SPIKE-4 GOTCHAS — encoded throughout, do NOT re-litigate (see plan):

  (a) ``browser_eval`` of a BARE expression returns ``null``. ALWAYS wrap the
      JS in an IIFE: ``(() => { ... return ...; })()``. ``_iife`` enforces this.

  (b) ``browser_eval`` on a tab AFTER it navigates fails — the CDP execution
      context detaches. To read a post-navigation callback URL, POLL
      ``browser_list_tabs`` for the new URL; never eval the navigated tab.

  (c) Clicking Authorize BEFORE the React consent page hydrates (~1.5s) is a
      silent no-op. Wait for the button to exist (IIFE eval), then RETRY the
      click until ``browser_list_tabs`` shows the tab left the authorize URL.

  (d) Prefer ``browser_click`` (CDP trusted dispatch) over an eval'd
      ``element.click()`` — synthetic eval clicks can be trusted-event gated.

The driver is SYNCHRONOUS to match ``pty_driver``'s pexpect style; the
container startup loop is synchronous and bridges to async via
``asyncio.to_thread`` upstream. Do NOT make this async.

Account guard (FAIL-CLOSED, Risk 1): before clicking Authorize in EITHER flow,
the consent page's "Logged in as <user>" identity is read and compared against
the expected identity (``config/identity.json`` email). A missing/empty page
identity is ALWAYS a mismatch → abort to failure. We never authorize unless
the identity is positively confirmed to match.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.granite_container.pty_driver import PTYDriver

logger = logging.getLogger(__name__)

# --- Bounding -----------------------------------------------------------------

# Single overall hard deadline for a recovery attempt. Well under the existing
# STARTUP_HARD_CEILING_S (600s) so a failed recovery still degrades to the
# alert path with budget to spare. Grain of salt: provisional, tunable.
RECOVERY_HARD_DEADLINE_S = float(os.environ.get("GRANITE_RELOGIN_DEADLINE_S", "120"))

# How long to wait for claude's auto-opened authorize tab (flow 1) before
# falling back to the printed paste URL (flow 2). Grain of salt: tunable.
AUTO_OPEN_TAB_BUDGET_S = float(os.environ.get("GRANITE_RELOGIN_AUTOTAB_BUDGET_S", "15"))

# Per-step poll budgets / intervals (provisional, tunable).
HYDRATION_BUDGET_S = float(os.environ.get("GRANITE_RELOGIN_HYDRATION_S", "10"))
CLICK_REDIRECT_BUDGET_S = float(os.environ.get("GRANITE_RELOGIN_CLICK_REDIRECT_S", "15"))
CALLBACK_POLL_BUDGET_S = float(os.environ.get("GRANITE_RELOGIN_CALLBACK_S", "20"))
PASTE_SENTINEL_BUDGET_S = float(os.environ.get("GRANITE_RELOGIN_PASTE_SENTINEL_S", "20"))
HANDSHAKE_TIMEOUT_S = float(os.environ.get("GRANITE_RELOGIN_HANDSHAKE_S", "15"))
RPC_TIMEOUT_S = float(os.environ.get("GRANITE_RELOGIN_RPC_S", "30"))
POLL_INTERVAL_S = 0.5
CLICK_RETRY_LIMIT = 6

# --- OAuth consent recipe constants (spike-2 / spike-3) -----------------------

# The authorize URL path the OAuth flow lands on. The auto-opened tab uses a
# localhost callback; the paste fallback uses the platform host. Both carry a
# `/authorize` path and an OAuth `state` query param.
_AUTHORIZE_URL_RE = re.compile(r"/authorize", re.IGNORECASE)
# The paste-flow callback URL where code+state appear as query params.
_CALLBACK_URL_FRAGMENT = "oauth/code/callback"
# Buffer sentinels (spike-3). The PTY prints a `platform.claude.com` URL and a
# "Paste code here" prompt. URL extraction slices from the first `https://claude`
# to the "Paste code" sentinel and strips ALL whitespace (de-wraps the terminal
# line-wrapping).
_PASTE_URL_START = "https://claude"
_PASTE_CODE_SENTINEL_RE = re.compile(r"Paste\s*code", re.IGNORECASE)

# BYOB MCP server fallback invocation (used only if `~/.claude.json` lacks the
# registration). The canonical command/args/env is read at runtime from
# `mcpServers.byob` — see `_resolve_byob_invocation`.
_BYOB_HOME = Path.home() / ".byob"
_FALLBACK_TSX = _BYOB_HOME / "node_modules" / ".bin" / "tsx"
_FALLBACK_MCP_TS = _BYOB_HOME / "packages" / "mcp-server" / "bin" / "byob-mcp.ts"
_CLAUDE_CONFIG_PATH = Path.home() / ".claude.json"


# ==============================================================================
# ReloginOutcome
# ==============================================================================


@dataclass(frozen=True)
class ReloginOutcome:
    """Immutable result of a ``recover_login`` attempt.

    Thread-safety requirement C1: the container builds this object IN FULL
    locally, then publishes it as its final statement. ``frozen=True`` makes it
    impossible to mutate after construction, so a half-built outcome can never
    be observed by another thread.

    Fields:
        succeeded: whether the login was completed.
        flow: which flow completed it — 1 (localhost auto-complete), 2 (paste
            fallback), or None (no flow ran / degraded to alert).
        reason: human-readable explanation, always populated (success or
            failure) for observability / session_events logging.
    """

    succeeded: bool
    flow: int | None
    reason: str


def _success(flow: int, reason: str) -> ReloginOutcome:
    return ReloginOutcome(succeeded=True, flow=flow, reason=reason)


def _failure(reason: str) -> ReloginOutcome:
    return ReloginOutcome(succeeded=False, flow=None, reason=reason)


# ==============================================================================
# BYOBClient — pure-Python MCP stdio client (no LLM, no claude session)
# ==============================================================================


class BYOBClientError(RuntimeError):
    """Raised internally for unrecoverable client faults; always caught and
    converted to a ReloginOutcome by ``recover_login`` (never escapes)."""


def _iife(js_body: str) -> str:
    """Wrap a JS expression/body in an IIFE (spike-4a).

    ``browser_eval`` of a BARE expression returns ``null``; only an IIFE that
    explicitly ``return``s a value yields a usable result. Callers pass a body
    that already contains a ``return`` statement.
    """
    return f"(() => {{ {js_body} }})()"


def _resolve_byob_invocation() -> tuple[str, list[str], dict[str, str]]:
    """Resolve the canonical BYOB MCP invocation from ``~/.claude.json``.

    Reads ``mcpServers.byob`` for the exact ``command``/``args``/``env`` rather
    than hardcoding the path. Falls back to the well-known ``~/.byob/...`` path
    with ``BYOB_ALLOW_EVAL=1`` if the registration is absent or unreadable.

    Returns (command, args, env-overlay). The env overlay is merged on top of
    the inherited process env at spawn time.
    """
    try:
        data = json.loads(_CLAUDE_CONFIG_PATH.read_text())
        entry = data.get("mcpServers", {}).get("byob")
        if entry and entry.get("command"):
            command = str(entry["command"])
            args = [str(a) for a in entry.get("args", [])]
            env = {str(k): str(v) for k, v in (entry.get("env") or {}).items()}
            env.setdefault("BYOB_ALLOW_EVAL", "1")
            return command, args, env
        logger.warning(
            "byob_relogin: ~/.claude.json has no mcpServers.byob entry; using fallback path"
        )
    except Exception as exc:  # noqa: BLE001 — observable fallback, never fatal
        logger.warning("byob_relogin: failed reading ~/.claude.json (%s); using fallback path", exc)
    return str(_FALLBACK_TSX), [str(_FALLBACK_MCP_TS)], {"BYOB_ALLOW_EVAL": "1"}


class BYOBClient:
    """Synchronous, pure-Python MCP stdio client for BYOB's browser tools.

    Spawns ``tsx byob-mcp.ts`` (env ``BYOB_ALLOW_EVAL=1``) and speaks
    newline-delimited JSON-RPC over stdin/stdout. No LLM, no ``claude`` session.

    Lifecycle: construct → ``start()`` (spawn + handshake) → tool helpers →
    ``close()``. ``close()`` is idempotent and safe to call in a ``finally``.

    Spawn / handshake failure does NOT raise out of normal use: ``start()``
    returns ``False`` and logs a warning so the caller degrades to the alert
    path. Every ``except`` logs (``logger.warning``) and yields an observable
    outcome — no bare ``except: pass``.

    Testability: the subprocess boundary is isolated to ``start()``/``close()``
    and ``_rpc()``; tests inject a fake process via ``proc`` or subclass and
    override ``_rpc``/``_call_tool``.
    """

    def __init__(self, proc: subprocess.Popen[str] | None = None) -> None:
        self._proc = proc
        self._next_id = 0
        self._started = proc is not None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> bool:
        """Spawn the MCP server and complete the initialize handshake.

        Returns True on success, False on any spawn/handshake failure (logged).
        Never raises — the caller treats False as "degrade to alert".
        """
        if self._proc is not None and self._started:
            return True
        command, args, env_overlay = _resolve_byob_invocation()
        spawn_env = os.environ.copy()
        spawn_env.update(env_overlay)
        try:
            self._proc = subprocess.Popen(
                [command, *args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=spawn_env,
                text=True,
                bufsize=1,  # line-buffered for newline-delimited JSON-RPC
            )
        except (OSError, ValueError) as exc:
            logger.warning(
                "byob_relogin: failed to spawn BYOB MCP server (%s %s): %s",
                command,
                args,
                exc,
            )
            self._proc = None
            return False

        try:
            self._handshake()
        except Exception as exc:  # noqa: BLE001 — handshake failure degrades, never fatal
            logger.warning("byob_relogin: MCP handshake failed: %s", exc)
            self.close()
            return False
        self._started = True
        return True

    def _handshake(self) -> None:
        """Send ``initialize`` then the ``notifications/initialized`` notice."""
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "granite-byob-relogin", "version": "1.0.0"},
            },
            timeout=HANDSHAKE_TIMEOUT_S,
        )
        self._notify("notifications/initialized", {})

    def close(self) -> None:
        """Kill the MCP subprocess. Idempotent and ``finally``-safe."""
        proc = self._proc
        self._proc = None
        self._started = False
        if proc is None:
            return
        try:
            proc.kill()
        except Exception as exc:  # noqa: BLE001 — best-effort teardown
            logger.warning("byob_relogin: error killing BYOB MCP subprocess: %s", exc)
        try:
            proc.wait(timeout=5)
        except Exception as exc:  # noqa: BLE001 — best-effort reap
            logger.warning("byob_relogin: error reaping BYOB MCP subprocess: %s", exc)

    # -- JSON-RPC transport --------------------------------------------------

    def _write_message(self, message: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise BYOBClientError("BYOB MCP subprocess not available for write")
        self._proc.stdin.write(json.dumps(message) + "\n")
        self._proc.stdin.flush()

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        self._write_message({"jsonrpc": "2.0", "method": method, "params": params})

    def _rpc(
        self, method: str, params: dict[str, Any], timeout: float = RPC_TIMEOUT_S
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and read until the matching response.

        Skips notifications / mismatched-id messages on the wire. Raises
        ``BYOBClientError`` on transport failure, timeout, or a JSON-RPC error
        object — always caught upstream and converted to a failure outcome.
        """
        if self._proc is None or self._proc.stdout is None:
            raise BYOBClientError("BYOB MCP subprocess not available for rpc")
        self._next_id += 1
        request_id = self._next_id
        self._write_message(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self._proc.stdout.readline()
            if line == "":  # EOF — server died
                raise BYOBClientError(f"BYOB MCP server closed stdout during {method}")
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                # BYOB may emit non-JSON diagnostics on stdout; skip them.
                continue
            if message.get("id") != request_id:
                continue  # notification or stale response — keep reading
            if "error" in message:
                raise BYOBClientError(f"JSON-RPC error on {method}: {message['error']}")
            return message.get("result", {})
        raise BYOBClientError(f"timed out waiting for {method} response")

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a BYOB tool via ``tools/call`` and return the decoded payload.

        MCP returns ``{"content": [{"type": "text", "text": "..."}], ...}``.
        We extract the first text block and JSON-decode it when possible (BYOB
        tools return JSON text); otherwise the raw string is returned.
        """
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        return _decode_tool_result(result)

    # -- tool helpers --------------------------------------------------------

    def navigate(self, url: str) -> Any:
        """Navigate the active tab to ``url`` (browser_navigate)."""
        return self._call_tool("browser_navigate", {"url": url})

    def click(self, **kwargs: Any) -> Any:
        """Click via CDP trusted dispatch (browser_click — spike-4d)."""
        return self._call_tool("browser_click", kwargs)

    def list_tabs(self) -> Any:
        """List open browser tabs (browser_list_tabs)."""
        return self._call_tool("browser_list_tabs", {})

    def eval(self, js_body: str) -> Any:
        """Evaluate JS in the active tab (browser_eval).

        ``js_body`` is wrapped in an IIFE (spike-4a) so a returned value is not
        swallowed. Pass a body containing an explicit ``return``.
        """
        return self._call_tool("browser_eval", {"expression": _iife(js_body)})

    def read(self, **kwargs: Any) -> Any:
        """Read page content (browser_read)."""
        return self._call_tool("browser_read", kwargs)


def _decode_tool_result(result: dict[str, Any]) -> Any:
    """Extract and best-effort JSON-decode an MCP tool-call result payload.

    Returns the decoded object/string from the first text content block, or the
    raw result dict if no text block is present.
    """
    content = result.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and "text" in first:
            text = first["text"]
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return text
    return result


# ==============================================================================
# Flow helpers
# ==============================================================================


def _tab_url(tab: Any) -> str:
    """Best-effort extract a URL string from a tab record of unknown shape."""
    if isinstance(tab, dict):
        for key in ("url", "href", "location"):
            value = tab.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _iter_tabs(tabs_payload: Any) -> list[Any]:
    """Normalize a browser_list_tabs payload to a list of tab records."""
    if isinstance(tabs_payload, list):
        return tabs_payload
    if isinstance(tabs_payload, dict):
        for key in ("tabs", "result", "data"):
            value = tabs_payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _find_authorize_tab(client: BYOBClient) -> Any | None:
    """Return claude's auto-opened authorize tab, if present (flow 1 / race 2).

    Prefers the auto-opened tab over self-navigation; the tab is identified by
    an ``/authorize`` path in its URL.
    """
    try:
        tabs = _iter_tabs(client.list_tabs())
    except BYOBClientError as exc:
        logger.warning("byob_relogin: list_tabs failed while finding authorize tab: %s", exc)
        return None
    for tab in tabs:
        if _AUTHORIZE_URL_RE.search(_tab_url(tab)):
            return tab
    return None


def _await_left_authorize(client: BYOBClient, deadline: float) -> bool:
    """Poll list_tabs until NO tab remains on an ``/authorize`` URL (race 1).

    Returns True if the authorize tab navigated away (consent accepted), False
    if the budget expired.
    """
    while time.monotonic() < deadline:
        if _find_authorize_tab(client) is None:
            return True
        time.sleep(POLL_INTERVAL_S)
    return False


def _await_callback_url(client: BYOBClient, deadline: float) -> str | None:
    """Poll list_tabs for the ``oauth/code/callback`` URL (spike-4b, flow 2).

    Reads the post-navigation URL via list_tabs polling — NEVER by eval'ing the
    navigated tab (the CDP context detaches). Returns the callback URL or None.
    """
    while time.monotonic() < deadline:
        try:
            tabs = _iter_tabs(client.list_tabs())
        except BYOBClientError as exc:
            logger.warning("byob_relogin: list_tabs failed while awaiting callback: %s", exc)
            return None
        for tab in tabs:
            url = _tab_url(tab)
            if _CALLBACK_URL_FRAGMENT in url:
                return url
        time.sleep(POLL_INTERVAL_S)
    return None


def _parse_callback(url: str) -> tuple[str, str] | None:
    """Parse ``code`` and ``state`` from a callback URL. None if either absent."""
    try:
        query = urllib.parse.urlparse(url).query
        params = urllib.parse.parse_qs(query)
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
    except Exception as exc:  # noqa: BLE001 — malformed URL → observable failure
        logger.warning("byob_relogin: failed parsing callback URL: %s", exc)
        return None
    if not code or not state:
        logger.warning("byob_relogin: callback URL missing code/state: %r", url)
        return None
    return code, state


def _extract_paste_url(buffer: str) -> str | None:
    """Reconstruct the wrapped authorize URL from the PTY buffer (flow 2).

    Slices from the first ``https://claude`` to the ``Paste code`` sentinel and
    strips ALL whitespace to de-wrap terminal line-wrapping (spike-3). Returns
    None if no extractable URL is present.
    """
    start = buffer.find(_PASTE_URL_START)
    if start == -1:
        return None
    tail = buffer[start:]
    sentinel = _PASTE_CODE_SENTINEL_RE.search(tail)
    raw = tail[: sentinel.start()] if sentinel else tail
    url = "".join(raw.split())  # strip ALL whitespace (de-wrap)
    if not url or "/authorize" not in url.lower():
        return None
    return url


def _read_page_identity(client: BYOBClient) -> str | None:
    """Read the consent page's "Logged in as <user>" identity (account guard).

    Uses an IIFE-wrapped eval (spike-4a). Returns the matched identity string,
    or None if it could not be read / is absent.
    """
    js = (
        "const m = document.body.innerText.match"
        "(/Logged in as\\s+([^\\n]+)/i); return m ? m[1].trim() : null;"
    )
    try:
        result = client.eval(js)
    except BYOBClientError as exc:
        logger.warning("byob_relogin: account-guard eval failed: %s", exc)
        return None
    if isinstance(result, str) and result.strip():
        return result.strip()
    return None


def _account_guard_ok(client: BYOBClient, expected_identity: str | None) -> bool:
    """FAIL-CLOSED account guard (Risk 1).

    Reads the consent page's logged-in identity and compares it to
    ``expected_identity``. A MISSING/EMPTY page identity is ALWAYS treated as a
    mismatch (we never use ``if page_identity and ...`` which would silently
    skip the guard). Never authorize unless identity is positively confirmed.

    When ``expected_identity`` is None we cannot positively confirm a match, so
    the guard fails closed.
    """
    page_identity = _read_page_identity(client)
    if not expected_identity:
        logger.warning("byob_relogin: no expected identity configured; account guard fails closed")
        return False
    # Fail-closed: a missing/empty page_identity must be a mismatch. The naive
    # `if page_identity and page_identity != expected` would SKIP the guard
    # when page_identity is falsy — never do that.
    if page_identity != expected_identity:
        logger.warning(
            "byob_relogin: account guard mismatch (page=%r expected=%r); aborting to alert",
            page_identity,
            expected_identity,
        )
        return False
    return True


def _hydrate_and_authorize(client: BYOBClient, deadline: float) -> bool:
    """Wait for the Authorize button to hydrate, then click until redirect.

    Encodes race 1 + spike-4c/d: poll for the button's existence (IIFE eval),
    then retry ``browser_click`` (CDP trusted dispatch) until list_tabs shows
    the tab left the authorize URL. Returns True on redirect, False otherwise.
    """
    # Wait for hydration: the Authorize button must exist in the DOM.
    hydration_deadline = min(deadline, time.monotonic() + HYDRATION_BUDGET_S)
    button_present = False
    button_js = (
        "const b = Array.from(document.querySelectorAll('button'))"
        ".find(el => el.innerText.trim() === 'Authorize'); return !!b;"
    )
    while time.monotonic() < hydration_deadline:
        try:
            if client.eval(button_js) is True:
                button_present = True
                break
        except BYOBClientError as exc:
            logger.warning("byob_relogin: hydration eval failed: %s", exc)
            return False
        time.sleep(POLL_INTERVAL_S)
    if not button_present:
        logger.warning("byob_relogin: Authorize button never hydrated within budget")
        return False

    # Retry the trusted click until the tab leaves the authorize URL (race 1).
    redirect_deadline = min(deadline, time.monotonic() + CLICK_REDIRECT_BUDGET_S)
    for _ in range(CLICK_RETRY_LIMIT):
        if time.monotonic() >= redirect_deadline:
            break
        try:
            client.click(text="Authorize")
        except BYOBClientError as exc:
            logger.warning("byob_relogin: Authorize click failed: %s", exc)
            return False
        # Give the click a short window to take effect, then check redirect.
        per_click_deadline = min(redirect_deadline, time.monotonic() + 3.0)
        if _await_left_authorize(client, per_click_deadline):
            return True
    logger.warning("byob_relogin: Authorize click never produced a redirect")
    return False


def _buffer_has_paste_sentinel(login_pty: PTYDriver, deadline: float) -> bool:
    """Wait for the ``Paste code here`` sentinel in the PTY buffer (race 3).

    Reads the PTY until the paste prompt is painted before writing the code, so
    the code is never written before the TUI is ready to accept it.
    """
    sentinel_deadline = min(deadline, time.monotonic() + PASTE_SENTINEL_BUDGET_S)
    pattern = re.compile(r"Paste\s*code\s*here", re.IGNORECASE)
    while time.monotonic() < sentinel_deadline:
        remaining = max(0.5, sentinel_deadline - time.monotonic())
        try:
            result = login_pty.read_until_idle(min_content_bytes=0, timeout_s=min(remaining, 2.0))
        except Exception as exc:  # noqa: BLE001 — PTY read failure → observable
            logger.warning("byob_relogin: PTY read failed awaiting paste sentinel: %s", exc)
            return False
        buf = getattr(result, "turn_buffer", "") or getattr(result, "buffer", "")
        if pattern.search(buf):
            return True
    return False


# ==============================================================================
# Orchestration
# ==============================================================================


def recover_login(
    login_pty: PTYDriver,
    login_pty_buffer: str,
    deadline: float,
    expected_identity: str | None = None,
) -> ReloginOutcome:
    """Autonomously recover a granite ``/login`` re-auth via BYOB.

    ``login_pty`` is the PTYDriver whose buffer showed the login frame — it may
    be PM **or** Dev; never assume PM. ``login_pty_buffer`` is that SAME PTY's
    buffer (paste-URL / sentinel slicing reads ONLY from this buffer).
    ``deadline`` is a ``time.monotonic()`` deadline; the routine additionally
    caps itself at ``RECOVERY_HARD_DEADLINE_S``. ``expected_identity`` is the
    email from ``config/identity.json`` (the container passes it) used by the
    fail-closed account guard.

    Flow classifier — THREE branches, TWO implemented flows:

      Flow 1 (localhost auto-complete, PRIMARY): find claude's auto-opened
        authorize tab → wait for hydration → account guard → click Authorize
        (retry until the tab leaves the authorize URL) → localhost callback
        auto-completes → press Enter into the PTY → success(flow=1).

      Flow 2 (paste fallback): if NO auto-opened tab appears within
        ``AUTO_OPEN_TAB_BUDGET_S`` but a printed ``platform.claude.com`` paste
        URL is in the buffer → reconstruct the wrapped authorize URL →
        navigate → account guard → click Authorize → poll list_tabs for the
        ``oauth/code/callback`` URL → parse ``code``/``state`` → wait for the
        ``Paste code here`` sentinel → write ``{code}#{state}`` + Enter →
        success(flow=2).

      Branch 3 (logged-out browser, NOT implemented): if the consent page is
        logged-out, or neither an auto-opened tab nor a recoverable paste URL
        appears within budget → return failure (degrade to alert). NO
        Google-unlock handler — explicit No-Go in the plan.

    Returns a ``ReloginOutcome``. NEVER raises — every failure path logs and
    returns a failure outcome so the container can fall through to the existing
    ``startup_unresolved`` ceiling + Telegram alert.
    """
    hard_deadline = min(deadline, time.monotonic() + RECOVERY_HARD_DEADLINE_S)
    client = BYOBClient()
    try:
        if not client.start():
            return _failure("BYOB MCP client failed to start; degrading to alert")

        # --- Flow 1: claude's auto-opened localhost authorize tab ----------
        auto_tab_deadline = min(hard_deadline, time.monotonic() + AUTO_OPEN_TAB_BUDGET_S)
        authorize_tab = None
        while time.monotonic() < auto_tab_deadline:
            authorize_tab = _find_authorize_tab(client)
            if authorize_tab is not None:
                break
            time.sleep(POLL_INTERVAL_S)

        if authorize_tab is not None:
            if not _account_guard_ok(client, expected_identity):
                return _failure("account guard failed in flow 1; degrading to alert")
            if not _hydrate_and_authorize(client, hard_deadline):
                return _failure("flow 1 Authorize click did not complete; degrading to alert")
            # Localhost callback auto-completes; press Enter into the PTY.
            try:
                login_pty.write("\r")
            except Exception as exc:  # noqa: BLE001 — PTY write failure → observable
                logger.warning("byob_relogin: PTY Enter write failed (flow 1): %s", exc)
                return _failure("flow 1 PTY Enter write failed; degrading to alert")
            return _success(1, "localhost auto-complete login recovered")

        # --- Flow 2: printed platform.claude.com paste URL -----------------
        paste_url = _extract_paste_url(login_pty_buffer)
        if paste_url is None:
            return _failure(
                "no auto-opened authorize tab and no recoverable paste URL; degrading to alert"
            )
        try:
            client.navigate(paste_url)
        except BYOBClientError as exc:
            logger.warning("byob_relogin: navigate to paste URL failed: %s", exc)
            return _failure("flow 2 navigate failed; degrading to alert")

        if not _account_guard_ok(client, expected_identity):
            return _failure("account guard failed in flow 2; degrading to alert")
        if not _hydrate_and_authorize(client, hard_deadline):
            return _failure("flow 2 Authorize click did not complete; degrading to alert")

        callback_deadline = min(hard_deadline, time.monotonic() + CALLBACK_POLL_BUDGET_S)
        callback_url = _await_callback_url(client, callback_deadline)
        if callback_url is None:
            return _failure("flow 2 callback URL never appeared; degrading to alert")
        parsed = _parse_callback(callback_url)
        if parsed is None:
            return _failure("flow 2 callback URL missing code/state; degrading to alert")
        code, state = parsed

        # Race 3: wait for the paste prompt before writing the code.
        if not _buffer_has_paste_sentinel(login_pty, hard_deadline):
            return _failure("flow 2 paste sentinel never appeared; degrading to alert")
        try:
            login_pty.write(f"{code}#{state}")
        except Exception as exc:  # noqa: BLE001 — PTY write failure → observable
            logger.warning("byob_relogin: PTY paste write failed (flow 2): %s", exc)
            return _failure("flow 2 PTY paste write failed; degrading to alert")
        return _success(2, "paste fallback login recovered")
    except Exception as exc:  # noqa: BLE001 — recover_login must NEVER raise
        logger.warning("byob_relogin: unexpected error during recovery: %s", exc)
        return _failure(f"unexpected recovery error: {exc}")
    finally:
        client.close()
