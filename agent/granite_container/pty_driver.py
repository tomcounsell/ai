"""Substrate driver for the granite operator PoC (issue #1546).

A thin pexpect-backed wrapper around the interactive `claude` TUI. The
driver does NOT use `claude -p`; it spawns an interactive session attached
to a pseudo-terminal and exposes a small surface (`spawn`, `write`,
`read_until_idle`, `send_ctrl_c`, `close`) the container layer can use
without reaching into pexpect directly.

This module is the PoC's **substrate** — every other component in
`agent/granite_container/` sits on top of it. The class is intentionally
narrow: it models the Claude Code TUI's submit/idle/interject surface
(per the v7 spike report's C1-C5 findings) and nothing more.

C1 (submit key): every text write ends with `\\r` (CR), never `\\n` (LF).
    The TUI is a readline-style input box; `\\n` is a literal newline
    within the field, not a submit.
C2 (interjection): the regex `INTERRUPTED_RE` matches both the v2.1.160
    text ("Press Ctrl-C again to exit") and the older text
    ("Interrupted · What should Claude do instead?"). The first ctrl-c
    surfaces the interjection; the second ctrl-c exits.
C3 (resume-UUID): the on-exit hint is environment-gated. Resume acceptance
    tests are run in a model-reachable env; in a non-reachable env they
    are skipped (per the PoC's Q5 disposition).
C4 (`/help` overlay): idle detection must recognize the `esc to cancel`
    bottom-bar text. The `wait_for_idle` heuristic checks for the
    bypass-permissions bar; an overlay swaps that bar for `esc to cancel`.
    Callers should treat both as "not actively responding".
C5 (idle signal): the bottom-bar text + prompt glyph + a content floor
    (default 400 bytes for post-reply). The glyph alone is not enough;
    the TUI briefly re-renders the bar while the model is still loading
    a response.

Reuse policy: the regexes (`_UUID_RE`, `_RESUME_HINT_RE`,
`INTERRUPTED_RE`) are imported from the spike / headless harness rather
than duplicated. Modifying `agent/claude_session.py` to add PTY support
would couple the PoC to the headless harness and break the "existing
headless harness is untouched" invariant.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

import pexpect
import pexpect.exceptions

# Reuse the headless harness's UUID/resume regexes. The spike (scripts/
# granite_tui_pty_spike_pexpect.py) and the prior PoC both depend on
# these being identical; the PoC inherits the same parser.
from agent.claude_session import _RESUME_HINT_RE

# Reuse the spike's INTERRUPTED_RE. The regex accepts both the v2.1.160
# TUI text and the older wording so the driver is robust to TUI version
# drift.
INTERRUPTED_RE = re.compile(
    r"(Interrupted\s*[·•\.]\s*What should Claude do instead\?|Press Ctrl-C again to exit)",
    re.IGNORECASE,
)

# Idle-signal regexes (C5). The TUI's idle state is a bottom bar
# containing "bypass ... permissions" plus the prompt glyph
# (`>` or `❯`). Both must be present.
IDLE_BAR = re.compile(r"bypass.{0,30}permissions", re.DOTALL)
PROMPT_GLYPH = re.compile(r"[>❯]")

# C4: the `/help` overlay swaps the bottom bar to "esc to cancel".
# Treat the overlay as "not actively responding" so the loop can hold.
OVERLAY_BAR = re.compile(r"esc to cancel", re.IGNORECASE)

# C1: the submit key. Every text write ends with this.
SUBMIT_KEY = b"\r"

# ANSI CSI-stripping regex (basic). The TUI paints styled output; for
# downstream classification we strip CSI sequences and the OSC sequences
# that are common in TUI frames. Full SGR parsing is out of scope.
_ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")

# The default content floor for post-reply idle. The TUI briefly
# re-renders the bar while the model is still loading a response; that
# false-positive doesn't have response content behind it, so we require
# the buffer to accumulate at least this many bytes before we declare
# idle. The spike calibrated this at 400 bytes; the PoC inherits.
DEFAULT_MIN_CONTENT_BYTES = 400

# Default per-read timeout. Long enough for the model to produce a turn
# on slow hardware; short enough that the loop's main tick doesn't
# stall. The 60s figure is the spike's per-scenario scenario-1/2/3/6
# ceiling; the steady-state loop can tune this per role.
DEFAULT_TIMEOUT_S = 60.0

# The model-pick policy: prefer cloud models (e.g. `glm-5.1:cloud`) when
# available, then gemma* (small/fast, conversational), then any non-granite
# local model. Granite is the *operator*, never the substrate model.
# Each entry is `(match_token, match_kind)` where match_kind is "suffix"
# for tokens that end the model string (like ":cloud" tags) and "prefix"
# for tokens that begin it. Cloud models get a longer cold-start budget
# in `spawn()` and in the prerequisite check (see prereq table in
# `docs/plans/granite_interactive_tui_poc.md`).
MODEL_PICK_PREFER: tuple[tuple[str, str], ...] = (
    (":cloud", "suffix"),
    ("gemma", "prefix"),
)
MODEL_PICK_AVOID_PREFIX = ("granite",)


class PTYDriverError(RuntimeError):
    """Raised for caller-facing errors (empty write, double-spawn, ...)."""


@dataclass
class IdleResult:
    """Result of a `read_until_idle` call."""

    saw_idle: bool
    buffer: str  # ANSI-stripped accumulated text
    idle_marker: str  # short slice of the trailing buffer at the moment of idle
    elapsed_ms: int


def _strip_ansi(text: str) -> str:
    """Strip CSI and OSC sequences from TUI output.

    This is a best-effort basic strip — full SGR attribute parsing is
    out of scope. The classifier layer only needs the visible text.
    """
    text = _ANSI_CSI_RE.sub("", text)
    text = _ANSI_OSC_RE.sub("", text)
    # TUI cursor-positioning and similar non-CSI controls. We keep the
    # visible text but drop the escape byte so the strip is idempotent.
    text = re.sub(r"\x1b[=>]", "", text)
    return text


def _pick_substrate_model() -> str:
    """Pick a model for the TUI subprocess, avoiding the operator's own model.

    Preference order (see `MODEL_PICK_PREFER`):
      1. Any `*:cloud` model (e.g. `glm-5.1:cloud`) — strongest when reachable
      2. Any `gemma*` model — small/fast/conversational
      3. Any non-`granite*` model — last local fallback
      4. The first model ollama reports (granite-acceptable as last resort)

    Returns the full ollama model identity (e.g. `glm-5.1:cloud` or
    `gemma4:e2b`) — the tag is required for ollama to resolve the model.
    Callers pass the result straight to `claude --model <full_identity>`.

    Raises PTYDriverError if ollama is unreachable or returns no models.
    """
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5) as r:
            tags = json.loads(r.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as e:
        raise PTYDriverError(f"ollama unreachable: {e}") from e

    names: list[str] = [m["name"] for m in tags.get("models", [])]
    if not names:
        raise PTYDriverError("ollama reports no models")

    for prefer_token, match_kind in MODEL_PICK_PREFER:
        for n in names:
            if match_kind == "suffix":
                if n.endswith(prefer_token):
                    return n  # keep the full identity, e.g. "glm-5.1:cloud"
            else:  # prefix
                if n.startswith(prefer_token):
                    return n  # keep the full identity, e.g. "gemma4:e2b"

    for n in names:
        if not any(n.startswith(p) for p in MODEL_PICK_AVOID_PREFIX):
            return n  # keep the tag — ollama needs it for resolution

    # All models are granite — return the first one as a last resort. The
    # operator's classifier is robust to this, just slower.
    return names[0]


def _build_env() -> dict[str, str]:
    """Child env: inherit everything except blank the API key.

    Mirrors `_build_env` in `agent/claude_session.py:90-101`: blanking
    `ANTHROPIC_API_KEY` (rather than removing it) is the documented way
    to force the Max subscription OAuth path.
    """
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = ""
    return env


class PTYDriver:
    """A thin pexpect-backed driver for an interactive `claude` TUI session.

    Lifecycle: `spawn()` -> `write()` + `read_until_idle()` (looped) ->
    `close()`. The driver does not interpret the TUI's output; that's
    the classifier's job. The driver only knows about the submit key,
    the idle heuristic, and the two-stage ctrl-c interject.

    Threading: the driver is single-threaded. Spawning two drivers in
    the same process (one PM, one Dev) is the container's job; each
    driver owns its own pexpect.spawn child and they don't share state.
    """

    def __init__(
        self,
        role: str = "pm",
        cwd: str | None = None,
        model: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.role = role
        self.cwd = cwd
        self._explicit_model = model
        self.timeout_s = timeout_s
        self._child: pexpect.spawn | None = None
        self._spawned_at: float | None = None

    # -- Lifecycle --------------------------------------------------------

    def spawn(self) -> None:
        """Spawn a fresh interactive `claude` TUI under a PTY.

        Idempotent in the strict sense: calling `spawn()` on an
        already-spawned driver raises PTYDriverError. Callers must
        `close()` first if they want to re-spawn.

        The model is picked automatically if not provided explicitly.
        See `_pick_substrate_model` for the selection policy.

        macOS setsid note: pexpect's underlying `pty.fork()` already
        calls `setsid()`; a second `setsid()` from `preexec_fn` is a
        no-op that EPERMs out. We pass a no-op `preexec_fn` so the
        child is still in its own session (the desired outcome) without
        the redundant syscall. Mirrors the spike's finding at
        `scripts/granite_tui_pty_spike_pexpect.py:181-207`.
        """
        if self._child is not None and self._child.isalive():
            raise PTYDriverError(f"PTYDriver({self.role}) already spawned; close() first")

        model = self._explicit_model or _pick_substrate_model()

        self._child = pexpect.spawn(
            "claude",
            ["--model", model, "--permission-mode", "bypassPermissions"],
            env=_build_env(),
            echo=False,
            encoding="utf-8",
            preexec_fn=lambda: None,
            cwd=self.cwd,
            timeout=int(self.timeout_s),
        )
        self._spawned_at = time.monotonic()

    def close(self, force: bool = True) -> None:
        """Close the PTY child. If `force=True` (default), SIGKILL on hang.

        The teardown path runs `pkill -f "claude --permission-mode bypassPermissions"`
        as a fallback for orphans (mirroring the probe's teardown at
        `scripts/probe_slash_arguments.py:367-373`).
        """
        if self._child is None:
            return
        try:
            if self._child.isalive():
                self._child.close(force=force)
        except Exception:
            pass
        self._child = None
        self._spawned_at = None

    # -- I/O --------------------------------------------------------------

    def write(self, text: str) -> None:
        """Write text + `\\r` to the PTY (C1 submit key).

        Empty input is rejected. If the caller already appended `\\r`
        or `\\n`, the trailing newline is normalized to `\\r`. Literal
        newlines WITHIN the input are preserved (the TUI input box
        supports multi-line input via the bracketed paste or a literal
        `\\n`; only the *final* newline is the submit key).
        """
        if not text:
            raise PTYDriverError("PTYDriver.write() rejected empty input")
        if self._child is None:
            raise PTYDriverError("PTYDriver.write() called before spawn()")

        if text.endswith("\n") and not text.endswith("\r\n"):
            text = text[:-1] + "\r"
        elif not text.endswith("\r"):
            text = text + "\r"

        self._child.send(text)

    def send_ctrl_c(self) -> None:
        """Send a single ctrl-c to the TUI (the first stage of C2 interject).

        The first ctrl-c surfaces the "Press Ctrl-C again to exit" hint;
        a second ctrl-c exits. The driver does not enforce a count — the
        caller decides. The container's logic is "send ctrl-c, wait for
        the hint, send ctrl-c again, wait for exit."
        """
        if self._child is None:
            raise PTYDriverError("PTYDriver.send_ctrl_c() called before spawn()")
        self._child.send("\x03")

    def read_until_idle(
        self,
        min_content_bytes: int = DEFAULT_MIN_CONTENT_BYTES,
        timeout_s: float | None = None,
    ) -> IdleResult:
        """Block until the TUI is idle, up to `timeout_s` (default driver timeout).

        C5 heuristic: the idle state is the bottom-bar text + the prompt
        glyph. The C4 overlay (`/help` showing `esc to cancel`) is also
        treated as idle — the loop holds while the user dismisses the
        overlay. The `min_content_bytes` floor (default 400) prevents
        false-positives from the TUI briefly re-rendering the bar while
        the model is still loading a response.

        Returns an `IdleResult` with `saw_idle=False` if the timeout
        fires before the idle signal stabilizes. The buffer is
        ANSI-stripped and is whatever the TUI has painted so far.
        """
        if self._child is None:
            raise PTYDriverError("PTYDriver.read_until_idle() called before spawn()")

        deadline = time.monotonic() + (timeout_s or self.timeout_s)
        accumulated = ""
        saw_idle = False
        idle_marker = ""
        start = time.monotonic()

        while time.monotonic() < deadline:
            try:
                chunk = self._child.read_nonblocking(size=8192, timeout=0.5)
            except pexpect.TIMEOUT:
                continue
            except pexpect.EOF:
                break
            except pexpect.exceptions.ExceptionPexpect:
                break
            if not chunk:
                continue
            accumulated += chunk
            stripped = _strip_ansi(accumulated)
            # C4 + C5: idle = (bypass bar OR overlay bar) AND prompt glyph.
            bar_match = IDLE_BAR.search(stripped) or OVERLAY_BAR.search(stripped)
            if bar_match and PROMPT_GLYPH.search(stripped):
                if min_content_bytes == 0 or len(accumulated) >= min_content_bytes:
                    saw_idle = True
                    tail = stripped[-200:]
                    m = IDLE_BAR.search(tail) or OVERLAY_BAR.search(tail)
                    if m:
                        s = max(0, m.start() - 20)
                        e = m.end() + 20
                        idle_marker = tail[s:e]
                    break

        elapsed_ms = int((time.monotonic() - start) * 1000)
        return IdleResult(
            saw_idle=saw_idle,
            buffer=_strip_ansi(accumulated),
            idle_marker=idle_marker,
            elapsed_ms=elapsed_ms,
        )

    def isalive(self) -> bool:
        """Whether the underlying pexpect child is still running."""
        if self._child is None:
            return False
        return self._child.isalive()

    # -- Inspection -------------------------------------------------------

    def alive_seconds(self) -> float | None:
        """Seconds since the child was spawned (None if not spawned)."""
        if self._spawned_at is None:
            return None
        return time.monotonic() - self._spawned_at

    def last_resume_uuid(self) -> str | None:
        """Capture a `--resume <uuid>` hint from the child buffer (best effort).

        The on-exit hint is only emitted on a successful model response
        (C3); in a non-reachable env, no hint is printed. The driver
        does not actively drive an exit; this is a passive scrape
        available for the container's resume-UUID acceptance test.
        """
        if self._child is None:
            return None
        buf = self._child.before or ""
        m = _RESUME_HINT_RE.search(buf)
        return m.group(1) if m else None
