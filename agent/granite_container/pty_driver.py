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

C1 (submit key): the submit key is `\\r` (CR), never `\\n` (LF), and it
    MUST be sent as a separate keystroke after a short delay
    (`SUBMIT_KEY_DELAY_S`). The TUI's paste-burst heuristic treats a CR
    arriving in the same input burst as the text as a literal newline
    inside the pasted content, NOT as a submit — observed live on TUI
    v2.1.173 (PR #1612 smoke failure): `text+\\r` in one `send()` left
    the command sitting in the input box forever, the model never ran,
    and the startup loop burned its full 600s ceiling with
    `pm_idle=False dev_idle=False` on every cycle. Sending the body,
    sleeping `SUBMIT_KEY_DELAY_S`, then sending `\\r` as its own
    keystroke submits reliably.
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
C5 (idle signal): byte-quiescence + the bottom-bar text + prompt glyph
    + a content floor (default 400 stripped chars for post-reply). The
    heuristic is evaluated against the driver's persistent per-turn
    screen capture (`_turn_text`, reset on every `write()`), NOT only
    against bytes read during the current `read_until_idle` call: a
    fully settled TUI paints NOTHING, so an idle check that only runs
    on fresh chunks can never observe idle on a quiescent PTY (the PR
    #1612 `startup_unresolved` smoke failure — 99 startup cycles, zero
    new bytes, `saw_idle=False` for 10 minutes). Idle is only declared
    after `QUIESCENCE_S` of byte-silence: while a model turn is active
    the TUI repaints the spinner animation at >=1 Hz, but those
    repaints arrive as cursor-positioned cell FRAGMENTS (`✻i…`,
    `✽hg`), not full `✻ Sprouting…` frames, so no regex over the
    capture can reliably tell "running now" from "settled" — sustained
    silence is the physical signal (live-observed on v2.1.173: a
    mid-turn read with only fragments in its tail false-idled at 4.2s
    while the model was still painting). Reads with a content floor
    (> 0) additionally require spinner evidence somewhere in the turn
    capture — proof the model actually ran a turn — so the post-write
    wait cannot declare idle on the command-echo frame alone.

Reuse policy: the regexes (`_UUID_RE`, `_RESUME_HINT_RE`,
`INTERRUPTED_RE`) are imported from the spike / headless harness rather
than duplicated. Modifying `agent/claude_session.py` to add PTY support
would couple the PoC to the headless harness and break the "existing
headless harness is untouched" invariant.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass

import pexpect
import pexpect.exceptions

# The session UUID Claude embeds in stream-json `session_id` fields and
# prints in its on-exit hint line: `claude --resume <uuid>`. Capturing it
# lets a crashed/interrupted session be resumed with full context instead
# of respawned fresh. Inlined from agent.claude_session (deleted in
# plan #1572, Task 5 — PoC deletion).
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_RESUME_HINT_RE = re.compile(r"--resume\s+(" + _UUID_RE.pattern + r")")

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
# Spinner-evidence pattern for content-floor reads: proof that a model
# turn actually RAN at some point this turn (the spinner painted, or
# the "esc to interrupt" processing hint did). Floor reads mean "the
# model responded"; without this, the echo frame of the submitted
# command (bar + glyph + enough bytes) satisfies the heuristic before
# the model has started (live-observed in the PR #1612 smoke runs).
# Observed spinner verbs (sampled across multiple PR #1612 live runs):
# Sprouting, Whirlpooling, Cookin, Sock-hopping, Honking, Thinking,
# Brewing, Crunching, Spinning, Streaming, Drafting, plus a long tail.
# We match the structural shape (`<glyph> <word>+ …`) rather than
# enumerate verbs; the leading glyph CYCLES per frame (·, ✻, ✶, ✳, ✢,
# ✽ on v2.1.173, plus • historically). The full shape paints at least
# once when the spinner line first renders; subsequent animation
# frames are cell-fragments and are deliberately NOT used as a
# "running right now" negative — see QUIESCENCE_S.
SPINNER_EVIDENCE_RE = re.compile(
    r"[·•✻✶✳✢✽]\s*[A-Za-z][A-Za-z\-']{2,30}\s*[…\.]{1,3}|esc to interrupt",
    re.IGNORECASE,
)
# Quiescence gate (C5): idle is only declared after this many seconds
# with zero new PTY bytes. While a model turn is active the TUI
# repaints the spinner animation and the elapsed-seconds counter at
# >=1 Hz, so an active turn can never be silent this long; a settled
# TUI paints nothing. This is the only reliable "running right now"
# negative: the animation repaints arrive as cursor-positioned cell
# FRAGMENTS (`✻i…`, `✽hg`), not full `✻ Sprouting…` frames, so a
# regex over the capture's tail misses an active turn (false idle
# mid-load, live-observed) and a stale full spinner frame near the
# end of a settled capture latches not-idle forever (false hang).
# Must stay below STARTUP_CYCLE_TIMEOUT_S (3s) so the startup loop's
# short polls can still observe idle. Module-level so tests can patch.
QUIESCENCE_S = 2.0

# C4: the `/help` overlay swaps the bottom bar to "esc to cancel".
# Treat the overlay as "not actively responding" so the loop can hold.
# The TUI may render this with whitespace collapsed (`Esctocancel`) or
# with spaces (`Esc to cancel`); \s* matches both forms.
OVERLAY_BAR = re.compile(r"esc\s*to\s*cancel", re.IGNORECASE)

# C1: the submit key. Sent as a SEPARATE keystroke after the text body.
SUBMIT_KEY = b"\r"
# C1: delay between sending the text body and the submit CR. The TUI's
# paste-burst heuristic treats a CR arriving in the same burst as the
# text as a literal newline inside the paste, not a submit (observed
# live on v2.1.173 — the PR #1612 startup_unresolved failure). 0.5s is
# the live-calibrated gap that reliably registers the CR as a
# standalone Enter keystroke. Module-level so tests can patch it to 0.
SUBMIT_KEY_DELAY_S = 0.5

# Cap on the per-turn screen capture retained across read_until_idle
# calls. A long Dev turn repaints spinner frames for minutes; trimming
# from the front keeps memory bounded while preserving the trailing
# frames the idle heuristic and the classifier care about.
TURN_TEXT_MAX_CHARS = 256_000

# ANSI CSI-stripping regex (basic). The TUI paints styled output; for
# downstream classification we strip CSI sequences and the OSC sequences
# that are common in TUI frames. Full SGR parsing is out of scope.
_ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")

# Cursor-advance CSI sequences the TUI uses to position the next glyph
# INSTEAD of emitting literal spaces. The Ink/React renderer paints
# `word1 word2` as `word1<cursor-forward>word2`, so the inter-word gap
# lives entirely in the escape sequence — never as a space character.
# A naive `_ANSI_CSI_RE.sub("", ...)` deletes the gap and collapses the
# words (`word1word2`), which is the issue-#1634 space-stripping bug.
# We translate these to literal spaces BEFORE the blanket CSI strip so
# the visible spacing survives into the classifier payload.
#   - `\x1b[<N>C` (Cursor Forward, CUF): advance N columns -> N spaces.
#     A bare `\x1b[C` (no count) advances 1 column -> 1 space.
#   - `\x1b[<N>G` / `\x1b[G` (Cursor Horizontal Absolute, CHA): jump to
#     an absolute column. We can't reconstruct the absolute target
#     without full terminal emulation, but the only thing downstream
#     matching needs is that the word boundary is preserved, so we emit
#     a single space. (Adjacent runs collapse via the final whitespace
#     normalization the classifier already does with `.strip()`.)
_ANSI_CURSOR_FORWARD_RE = re.compile(r"\x1b\[([0-9]*)C")
_ANSI_CURSOR_ABS_COL_RE = re.compile(r"\x1b\[[0-9]*G")

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

# Fallback substrate aliases when settings can't be read (test envs that
# import the driver without a Settings instance). The PM/Dev TUIs run on
# the Claude subscription, NOT ollama — ollama is the granite *classifier*
# only. Aliases are UNPINNED so the substrate tracks the latest version.
_FALLBACK_SUBSTRATE_MODEL = {"pm": "opus", "dev": "sonnet"}


class PTYDriverError(RuntimeError):
    """Raised for caller-facing errors (empty write, double-spawn, ...)."""


@dataclass
class IdleResult:
    """Result of a `read_until_idle` call.

    `buffer` is the ANSI-stripped text read during THIS call only —
    edge-triggered, suitable for startup-event parsing (an event must
    not be re-detected and re-answered on every poll cycle).
    `turn_buffer` is the ANSI-stripped screen capture since the last
    `write()` — level-triggered, suitable for classification (the PM's
    routed output may have streamed during an earlier read call, e.g.
    the prime's post-write wait, while the classifying read sees a
    quiescent PTY).
    """

    saw_idle: bool
    buffer: str  # ANSI-stripped text read during this call
    idle_marker: str  # short slice of the trailing buffer at the moment of idle
    elapsed_ms: int
    turn_buffer: str = ""  # ANSI-stripped capture since the last write()


def _strip_ansi(text: str) -> str:
    """Strip CSI and OSC sequences from TUI output.

    This is a best-effort basic strip — full SGR attribute parsing is
    out of scope. The classifier layer only needs the visible text.
    Whitespace is preserved so that downstream matching (bypass bar,
    overlay bar, content checks) can use readable substrings.

    Cursor-advance sequences (CUF `\x1b[NC`, CHA `\x1b[NG`) are the
    TUI's way of painting inter-word spacing without literal spaces; we
    translate them to spaces BEFORE the blanket CSI strip so the gaps
    survive into the classifier payload (issue #1634). Order matters:
    the CUF/CHA substitutions MUST run before `_ANSI_CSI_RE`, which
    would otherwise delete the spacing outright.
    """
    text = _ANSI_CURSOR_FORWARD_RE.sub(lambda m: " " * max(1, int(m.group(1) or "1")), text)
    text = _ANSI_CURSOR_ABS_COL_RE.sub(" ", text)
    text = _ANSI_CSI_RE.sub("", text)
    text = _ANSI_OSC_RE.sub("", text)
    # TUI cursor-positioning and similar non-CSI controls. We keep the
    # visible text but drop the escape byte so the strip is idempotent.
    text = re.sub(r"\x1b[=>]", "", text)
    return text


def _default_substrate_model(role: str) -> str:
    """Resolve the Claude model alias for a TUI PTY by role.

    The PM/Dev TUIs are real ``claude`` Code sessions on the Claude
    subscription (OAuth — see ``_build_env``), run exactly like the
    ``claude --permission-mode bypassPermissions`` shortcut but with the
    model chosen at spawn time. ollama is the granite *classifier's*
    substrate, never the PTY's.

    Reads ``settings.granite.pm_model`` / ``dev_model`` (env-overridable as
    ``GRANITE__PM_MODEL`` / ``GRANITE__DEV_MODEL``); falls back to the
    role-default alias if settings can't be loaded (e.g. a bare unit-test
    import). Returns an UNPINNED alias so the substrate tracks the latest
    version; callers pass it straight to ``claude --model <alias>``.
    """
    try:
        from config.settings import settings

        if role == "dev":
            return settings.granite.dev_model
        return settings.granite.pm_model
    except Exception:
        return _FALLBACK_SUBSTRATE_MODEL.get(role, "sonnet")


def _build_env() -> dict[str, str]:
    """Child env: inherit everything except blank the API key + base URL.

    Mirrors `_build_env` in `agent/claude_session.py:90-101`: blanking
    `ANTHROPIC_API_KEY` (rather than removing it) is the documented way
    to force the Max subscription OAuth path. But blanking ONLY the
    key is not enough — if the operator's shell exports
    `ANTHROPIC_BASE_URL=http://localhost:11434` (ollama) and
    `ANTHROPIC_AUTH_TOKEN=ollama` (also from the ollama setup), the
    TUI sees OAuth login but dispatches model calls to ollama, which
    doesn't host Opus / Sonnet and errors with "issue with the
    selected model" (observed live in PR #1612). We must blank all
    three so the TUI uses the real Claude API endpoint that OAuth
    resolves to.

    The ollama substrate is the granite classifier only
    (`granite_classifier.py`); it is intentionally NOT inherited
    by the PTY child.
    """
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = ""
    env["ANTHROPIC_BASE_URL"] = ""
    env["ANTHROPIC_AUTH_TOKEN"] = ""
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
        env: dict[str, str] | None = None,
        append_system_prompt: str | None = None,
    ) -> None:
        self.role = role
        self.cwd = cwd
        self._explicit_model = model
        self.timeout_s = timeout_s
        # Per-session env overlay merged ON TOP of `_build_env()` at spawn
        # time (session identity: AGENT_SESSION_ID, SESSION_TYPE,
        # CLAUDE_CODE_TASK_LIST_ID, VALOR_PARENT_SESSION_ID, ...). The
        # ANTHROPIC_* blanking in `_build_env` is applied first and is not
        # expected to be overridden by callers.
        self._extra_env = dict(env) if env else None
        # Composed persona overlay passed to `claude --append-system-prompt`
        # at spawn time (spawn-on-acquire path, PR #1612 review B2). The
        # interactive TUI supports the flag, so the persona is a real
        # system-prompt append rather than user-visible prime text.
        self._append_system_prompt = append_system_prompt
        self._child: pexpect.spawn | None = None
        self._spawned_at: float | None = None
        # Per-turn screen capture (raw, ANSI-laden). Accumulates every
        # chunk read by `read_until_idle` across calls; reset by
        # `write()` (a new turn starts) and `close()`. The C5 idle
        # heuristic is evaluated against this capture so a quiescent
        # PTY whose idle frame painted during an EARLIER read call is
        # still observable as idle (level-triggered).
        self._turn_text: str = ""

    # -- Lifecycle --------------------------------------------------------

    def spawn(self) -> None:
        """Spawn a fresh interactive `claude` TUI under a PTY.

        Idempotent in the strict sense: calling `spawn()` on an
        already-spawned driver raises PTYDriverError. Callers must
        `close()` first if they want to re-spawn.

        The model is picked automatically if not provided explicitly.
        See `_default_substrate_model` for the model-by-role policy.

        macOS setsid note: pexpect's underlying `pty.fork()` already
        calls `setsid()`; a second `setsid()` from `preexec_fn` is a
        no-op that EPERMs out. We pass a no-op `preexec_fn` so the
        child is still in its own session (the desired outcome) without
        the redundant syscall. Mirrors the spike's finding at
        `scripts/granite_tui_pty_spike_pexpect.py:181-207`.
        """
        if self._child is not None and self._child.isalive():
            raise PTYDriverError(f"PTYDriver({self.role}) already spawned; close() first")

        model = self._explicit_model or _default_substrate_model(self.role)

        args = ["--model", model, "--permission-mode", "bypassPermissions"]
        if self._append_system_prompt:
            args += ["--append-system-prompt", self._append_system_prompt]

        env = _build_env()
        if self._extra_env:
            env.update(self._extra_env)

        self._child = pexpect.spawn(
            "claude",
            args,
            env=env,
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
        self._turn_text = ""

    # -- I/O --------------------------------------------------------------

    def write(self, text: str) -> None:
        """Write text to the PTY, then submit with a separate `\\r` (C1).

        Empty input is rejected. A single trailing `\\r`/`\\n`/`\\r\\n`
        on the input is stripped (it IS the submit intent); literal
        newlines WITHIN the input are preserved (the TUI input box
        treats a rapid burst as a paste, so internal `\\n` become
        newlines inside the field).

        The submit CR is sent as its own keystroke after
        `SUBMIT_KEY_DELAY_S`: the TUI's paste-burst heuristic treats a
        CR arriving in the same burst as the text as a literal newline
        inside the paste, not a submit (live-observed on v2.1.173 —
        the PR #1612 startup_unresolved failure mode, where the prime
        command sat unsubmitted in the input box for the full 600s
        startup ceiling).

        A write starts a new turn: the per-turn screen capture
        (`_turn_text`) is reset BEFORE sending so the C5 idle heuristic
        and content floor measure only frames painted after this
        submit.
        """
        if not text:
            raise PTYDriverError("PTYDriver.write() rejected empty input")
        if self._child is None:
            raise PTYDriverError("PTYDriver.write() called before spawn()")

        if text.endswith("\r\n"):
            body = text[:-2]
        elif text.endswith(("\r", "\n")):
            body = text[:-1]
        else:
            body = text

        self._turn_text = ""
        if body:
            self._child.send(body)
            time.sleep(SUBMIT_KEY_DELAY_S)
        self._child.send("\r")

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
        overlay.

        The heuristic is **level-triggered**: it is evaluated against
        the per-turn screen capture (`_turn_text`, everything painted
        since the last `write()`), on every poll tick — including ticks
        where no new bytes arrived. A settled TUI paints nothing, so an
        edge-triggered check (only on fresh chunks) can never observe
        idle on a quiescent PTY and times out forever (the PR #1612
        startup_unresolved failure mode).

        Idle additionally requires `QUIESCENCE_S` of byte-silence
        observed within this call: an active model turn repaints the
        spinner animation at >=1 Hz (as cell fragments no regex can
        reliably match), so sustained silence is the signal that the
        turn is over and the capture's bar/glyph reflect a settled
        screen rather than a mid-load repaint.

        The `min_content_bytes` floor (default 400) is measured against
        the ANSI-stripped turn capture and prevents false-positives
        from the TUI re-rendering the bar while the model is still
        loading. Floor reads (> 0) additionally require spinner
        evidence somewhere in the turn capture — the echo of the
        submitted command paints the bypass bar and the prompt glyph
        BEFORE the model starts, and ANSI-heavy echo frames can exceed
        a byte floor on their own (live-observed: the prime post-write
        wait returned `saw_idle=True` on the echo frame while the model
        had not run).

        Returns an `IdleResult` with `saw_idle=False` if the timeout
        fires before the idle signal stabilizes. `buffer` is the
        ANSI-stripped text read during this call; `turn_buffer` is the
        ANSI-stripped capture since the last write.
        """
        if self._child is None:
            raise PTYDriverError("PTYDriver.read_until_idle() called before spawn()")

        deadline = time.monotonic() + (timeout_s or self.timeout_s)
        accumulated = ""
        saw_idle = False
        idle_marker = ""
        start = time.monotonic()
        last_chunk_at = start

        while time.monotonic() < deadline:
            try:
                chunk = self._child.read_nonblocking(size=8192, timeout=0.5)
            except pexpect.TIMEOUT:
                chunk = ""
            except pexpect.EOF:
                break
            except pexpect.exceptions.ExceptionPexpect:
                break
            if chunk:
                accumulated += chunk
                self._turn_text += chunk
                if len(self._turn_text) > TURN_TEXT_MAX_CHARS:
                    self._turn_text = self._turn_text[-TURN_TEXT_MAX_CHARS:]
                # The TUI is actively painting — a model turn is in
                # flight (spinner animation / streaming response).
                # Re-read; idle can only be declared after silence.
                last_chunk_at = time.monotonic()
                continue
            # Quiescence gate: require sustained byte-silence observed
            # WITHIN this call before judging the capture. An active
            # turn repaints at >=1 Hz, so it can never pass this gate;
            # a settled PTY passes it QUIESCENCE_S after its last paint
            # (or QUIESCENCE_S into the call if it was already silent).
            if time.monotonic() - last_chunk_at < QUIESCENCE_S:
                continue
            stripped = _strip_ansi(self._turn_text)
            if not stripped:
                continue
            # C4 + C5: idle = (bypass bar OR overlay bar) AND prompt glyph.
            bar_match = IDLE_BAR.search(stripped) or OVERLAY_BAR.search(stripped)
            if bar_match and PROMPT_GLYPH.search(stripped):
                if min_content_bytes > 0:
                    if len(stripped) < min_content_bytes:
                        continue
                    # Spinner evidence: a content-floor read means "the
                    # model responded"; require that a loading verb (or
                    # the esc-to-interrupt hint) was painted at some
                    # point this turn. The trailing window above
                    # already proved the spinner is gone NOW.
                    if not SPINNER_EVIDENCE_RE.search(stripped):
                        continue
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
            turn_buffer=_strip_ansi(self._turn_text),
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
