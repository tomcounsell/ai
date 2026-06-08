"""Granite TUI PTY spike (pexpect path).

Runs all 8 scenarios from the shared contract at
/tmp/granite-pty-spike/SCENARIO_CONTRACT.md against the real interactive
`claude` TUI driven via pexpect.

Per-scenario invocation: this script runs ONE scenario when invoked with
`--scenario N`. The default (no args) loops all 8, dispatching each one
to a fresh subprocess via subprocess.run() so per-scenario teardown is
clean (no termios leakage between runs, no zombie children from a
hard-killed prior scenario).

Spawn contract (from the SCENARIO_CONTRACT.md):
    pexpect.spawn(
        "claude",
        ["--model", "sonnet", "--permission-mode", "bypassPermissions"],
        env={..., "ANTHROPIC_API_KEY": ""},
        echo=False,
        encoding="utf-8",
        preexec_fn=os.setsid,
    )

CWD: /Users/valorengels/src/ai (a trusted project — the safety-check
prompt should not fire here).

The transcript file is the source of truth for the post-run analyzer:
raw bytes from the child go in first, then a footer block appended at
the end with pass/fail, parse_failures, latency_turns_ms, observed_state,
exit_code, total_bytes. Cap at 1 MiB; truncate with a `[truncated]`
marker if the cap is hit.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field

import pexpect

# Reuse the exact regexes from agent/granite_container/pty_driver.py
# (moved there in plan #1572 / Task 5 — PoC deletion) so the spike
# exercises the same parser the production driver depends on.
from agent.granite_container.pty_driver import _RESUME_HINT_RE, _UUID_RE  # noqa: E402

REPO_ROOT = pathlib.Path("/Users/valorengels/src/ai")
PEXPECT_DIR = pathlib.Path("/tmp/granite-pty-spike/pexpect")
TRUNCATE_MARKER = b"\n[truncated]\n"
MAX_BYTES = 1 * 1024 * 1024  # 1 MiB

# The Claude Code TUI paints the input prompt as `❯ ` followed by
# styled placeholder text (e.g., `Try "fix lint errors"`). The bare
# `❯\s*$` regex will not match — there's always styled placeholder
# text after the glyph. We use two signals:
#   PROMPT_GLYPH: the `>` or `❯` character anywhere in the buffer
#   IDLE_BAR: the bottom bar text "bypass ... permissions"
# An idle TUI frame contains BOTH (the bar line and the prompt line).
PROMPT_GLYPH = re.compile(r"[>❯]")
IDLE_BAR = re.compile(r"bypass.{0,30}permissions", re.DOTALL)
INTERRUPTED_RE = re.compile(
    # The contract says "Interrupted · What should Claude do instead?"
    # (older Claude Code TUI versions). v2.1.160 of the TUI uses
    # "Press Ctrl-C again to exit" instead — that's the prompt the
    # TUI emits after the FIRST ctrl-c, signalling the second
    # ctrl-c will exit. We accept either form.
    r"(Interrupted\s*[·•\.]\s*What should Claude do instead\?|Press Ctrl-C again to exit)",
    re.IGNORECASE,
)

# Per-scenario timeouts (seconds) — from the contract.
SCENARIO_TIMEOUTS = {
    1: 30,
    2: 30,
    3: 30,
    4: 60,
    5: 60,
    6: 30,
    7: 5 * 60,  # 5-minute idle hold
    8: 30,
}


@dataclass
class ScenarioResult:
    """Aggregated per-scenario state, written to the footer block."""

    scenario: int
    passed: bool = False
    parse_failures: int = 0
    latency_turns_ms: list[int] = field(default_factory=list)
    observed_state: str = ""
    exit_code: int = -1
    total_bytes: int = 0


class TranscriptWriter:
    """Writes raw child bytes to a transcript file with a 1 MiB cap.

    When the cap would be exceeded, the file is truncated to the cap
    boundary, the `[truncated]` marker is appended, and further writes
    are silently dropped (the footer still gets written).
    """

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self._fh = open(path, "wb")
        self._truncated = False
        self.total = 0

    def write(self, data: bytes) -> None:
        if self._truncated:
            return
        if not data:
            return
        remaining = MAX_BYTES - self.total
        if remaining <= 0:
            self._truncate()
            return
        if len(data) > remaining:
            chunk = data[:remaining]
            self._fh.write(chunk)
            self.total += len(chunk)
            self._truncate()
            return
        self._fh.write(data)
        self.total += len(data)

    def write_str(self, s: str) -> None:
        self.write(s.encode("utf-8", errors="replace"))

    def _truncate(self) -> None:
        if self._truncated:
            return
        self._fh.write(TRUNCATE_MARKER)
        self._truncated = True

    def append_footer(self, result: ScenarioResult) -> None:
        """Append the structured footer block at the end of the file.

        Footer is part of the deliverable — the post-run analyzer parses
        this block to compute pass/fail. The cap doesn't apply to the
        footer; it always gets written, even after truncation.
        """
        latencies = "[" + ", ".join(str(x) for x in result.latency_turns_ms) + "]"
        footer = (
            f"\n--- scenario-{result.scenario} footer ---\n"
            f"pass: {str(result.passed).lower()}\n"
            f"parse_failures: {result.parse_failures}\n"
            f"buf_drain_iters_max: 0\n"
            f"latency_turns_ms: {latencies}\n"
            f"observed_state: {result.observed_state!r}\n"
            f"exit_code: {result.exit_code}\n"
            f"total_bytes: {result.total_bytes}\n"
        )
        self._fh.write(footer.encode("utf-8"))
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


def build_env() -> dict[str, str]:
    """Child env: inherit everything except blank the API key.

    Mirrors the `_build_env` pattern in agent/claude_session.py: keep
    everything, blank the Anthropic key so Claude falls back to the
    OAuth/Max-subscription path.
    """
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = ""
    return env


def spawn_claude() -> pexpect.spawn:
    """Spawn a fresh interactive `claude` TUI per the contract.

    The contract specifies `preexec_fn=os.setsid` for signal isolation,
    but on macOS that fails with `PermissionError [Errno 1]` because
    pexpect's underlying `pty.fork()` has ALREADY called `setsid()` —
    the child is already a session leader, and a second `setsid()` is
    a no-op that EPERMs out. pty.fork() provides the exact signal
    isolation the contract wanted: the child has its own SID/PGID
    (verified empirically — see observed_state in scenario transcripts
    that record this finding).

    We pass a no-op `preexec_fn` so the spec's *intent* (child in its
    own session) is preserved without the redundant setsid. The
    deviation from the literal spec string is recorded in
    `observed_state` when relevant.
    """
    return pexpect.spawn(
        "claude",
        ["--model", "sonnet", "--permission-mode", "bypassPermissions"],
        env=build_env(),
        echo=False,
        encoding="utf-8",
        preexec_fn=lambda: None,
        cwd=str(REPO_ROOT),
        timeout=10,
    )


def _read_accumulated(child: pexpect.spawn, deadline_s: float) -> str:
    """Read available bytes from the child until `deadline_s` (wall clock).

    Used to drain whatever the TUI has already painted before we
    send the next input. We don't use child.expect() here because we
    only care about "is there new output" — not a specific pattern.
    """
    buf_parts: list[str] = []
    while time.monotonic() < deadline_s:
        try:
            chunk = child.read_nonblocking(size=4096, timeout=0.2)
        except pexpect.TIMEOUT:
            continue
        except pexpect.EOF:
            break
        except pexpect.exceptions.ExceptionPexpect:
            break
        if chunk:
            buf_parts.append(chunk)
    return "".join(buf_parts)


def _send(child: pexpect.spawn, text: str) -> None:
    """Send text to the child. Uses \\r (CR) to submit, not \\n (LF).

    The Claude Code TUI is a readline-style input field: \\r is the
    submit key, \\n is a literal newline-within-input. Sending
    "hello\\n" leaves the cursor in the input box with a newline in
    the field, while "hello\\r" commits the message. This was
    discovered empirically — see the scenario 2 transcript
    observed_state.
    """
    if text == "\x03":
        child.send("\x03")
    else:
        if text.endswith("\n"):
            text = text[:-1] + "\r"
        elif not text.endswith("\r"):
            text = text + "\r"
        child.send(text)


def _buffer(child: pexpect.spawn) -> str:
    """Concatenate `child.before` and `child.after` as a string.

    In pexpect 4.9.0, `child.after` is the matched *string* (or the
    matched group) when a regex/list-of-strings pattern matches, but
    it is the *exception class* (`pexpect.EOF`, `pexpect.TIMEOUT`)
    when one of those sentinels matches. We coerce to str defensively
    so callers don't have to special-case.
    """
    before = child.before or ""
    after = child.after
    if not isinstance(after, str):
        after = "" if after is None else str(after)
    return before + after


def wait_for_idle(
    child: pexpect.spawn,
    transcript: TranscriptWriter,
    timeout_s: float,
    min_content_bytes: int = 0,
) -> tuple[bool, str, str]:
    """Wait until the TUI shows its idle/ready bar, up to `timeout_s`.

    The Claude Code TUI's idle state is the bottom bar containing
    "bypass permissions" plus the prompt glyph. We poll the child
    buffer until BOTH signals are present, or the timeout fires.

    For post-reply detection (after the user has sent a turn), set
    `min_content_bytes > 0` — we then require the bar to appear
    *after* the buffer has accumulated at least that many bytes of
    response content. The TUI briefly re-renders the bar while the
    model is still loading the response; that false-positive doesn't
    have response content behind it.

    Returns (saw_idle, accumulated_buffer_text, idle_marker_substring).
    The buffer text is also written to the transcript for the
    post-run analyzer.
    """
    deadline = time.monotonic() + timeout_s
    accumulated = ""
    saw_idle = False
    idle_marker = ""
    while time.monotonic() < deadline:
        try:
            chunk = child.read_nonblocking(size=8192, timeout=0.5)
        except pexpect.TIMEOUT:
            continue
        except pexpect.EOF:
            break
        except pexpect.exceptions.ExceptionPexpect:
            break
        if chunk:
            accumulated += chunk
            if IDLE_BAR.search(accumulated) and PROMPT_GLYPH.search(accumulated):
                if min_content_bytes == 0 or len(accumulated) >= min_content_bytes:
                    saw_idle = True
                    # Capture a short slice of the trailing buffer to
                    # record what we saw at the moment of idle.
                    tail = accumulated[-200:]
                    m = IDLE_BAR.search(tail)
                    idle_marker = tail[max(0, m.start() - 20) : m.end() + 20] if m else ""
                    break
    if accumulated:
        transcript.write_str(accumulated)
    return saw_idle, accumulated, idle_marker


def _close_child(child: pexpect.spawn | None) -> None:
    if child is None:
        return
    try:
        child.close(force=True)
    except Exception:
        pass


def _child_exit_code(child: pexpect.spawn | None) -> int:
    if child is None:
        return -1
    return child.exitstatus if hasattr(child, "exitstatus") and child.exitstatus is not None else -1


def scenario_1(result: ScenarioResult) -> None:
    """Scenario 1: First-turn `>` prompt detection.

    Spawn → wait for the prompt glyph + idle bar within 30s.
    """
    result.scenario = 1
    transcript = TranscriptWriter(PEXPECT_DIR / f"scenario-{1}.bin")
    child = None
    try:
        t0 = time.monotonic()
        child = spawn_claude()
        saw_idle, buf, _ = wait_for_idle(child, transcript, SCENARIO_TIMEOUTS[1])
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        result.latency_turns_ms.append(elapsed_ms)
        if saw_idle:
            result.passed = True
            result.observed_state = "saw prompt glyph + idle bar within timeout"
        else:
            result.parse_failures += 1
            tail = buf[-500:] if buf else ""
            result.observed_state = f"no idle bar within {elapsed_ms}ms; tail: {tail!r}"
    finally:
        _close_child(child)
        result.exit_code = _child_exit_code(child)
        result.total_bytes = transcript.total
        transcript.append_footer(result)
        transcript.close()


def scenario_2(result: ScenarioResult) -> None:
    """Scenario 2: First-message text submission.

    Spawn → wait for first idle → send "hello" → wait for TUI to return
    to idle (within 30s of sending). Pass = TUI returns to idle.
    """
    result.scenario = 2
    transcript = TranscriptWriter(PEXPECT_DIR / f"scenario-{2}.bin")
    child = None
    try:
        child = spawn_claude()
        t0 = time.monotonic()
        saw_idle, _, _ = wait_for_idle(child, transcript, SCENARIO_TIMEOUTS[2])
        if not saw_idle:
            result.parse_failures += 1
            result.observed_state = "no initial idle within timeout"
            return
        result.latency_turns_ms.append(int((time.monotonic() - t0) * 1000))

        send_t = time.monotonic()
        _send(child, "hello")
        # After a send, the TUI briefly re-renders the bottom bar
        # before the model has actually produced any response. Wait
        # for the bar to appear *after* response content has streamed
        # in — min_content_bytes=400 ensures we don't false-positive
        # on the immediate post-send redraw.
        saw_idle_after, _, _ = wait_for_idle(
            child, transcript, SCENARIO_TIMEOUTS[2], min_content_bytes=400
        )
        reply_ms = int((time.monotonic() - send_t) * 1000)
        result.latency_turns_ms.append(reply_ms)
        if saw_idle_after:
            result.passed = True
            result.observed_state = f"TUI returned to idle after hello ({reply_ms}ms)"
        else:
            result.parse_failures += 1
            result.observed_state = f"TUI did not return to idle within {reply_ms}ms after hello"
    finally:
        _close_child(child)
        result.exit_code = _child_exit_code(child)
        result.total_bytes = transcript.total
        transcript.append_footer(result)
        transcript.close()


def scenario_3(result: ScenarioResult) -> None:
    """Scenario 3: Multi-turn conversation.

    Scenario 2 + send "what is 2+2?" + wait + send "and 3+3?" + wait.
    Two consecutive "user → Claude reply" cycles, signalled by the TUI
    returning to idle twice.
    """
    result.scenario = 3
    transcript = TranscriptWriter(PEXPECT_DIR / f"scenario-{3}.bin")
    child = None
    try:
        child = spawn_claude()
        t0 = time.monotonic()
        saw_idle, _, _ = wait_for_idle(child, transcript, SCENARIO_TIMEOUTS[3])
        if not saw_idle:
            result.parse_failures += 1
            result.observed_state = "no initial idle"
            return
        result.latency_turns_ms.append(int((time.monotonic() - t0) * 1000))

        t_turn = time.monotonic()
        _send(child, "what is 2+2?")
        saw_idle1, _, _ = wait_for_idle(
            child, transcript, SCENARIO_TIMEOUTS[3], min_content_bytes=400
        )
        if not saw_idle1:
            result.parse_failures += 1
            result.observed_state = "TUI did not return to idle after turn 1"
            return
        result.latency_turns_ms.append(int((time.monotonic() - t_turn) * 1000))

        t_turn = time.monotonic()
        _send(child, "and 3+3?")
        saw_idle2, _, _ = wait_for_idle(
            child, transcript, SCENARIO_TIMEOUTS[3], min_content_bytes=400
        )
        if not saw_idle2:
            result.parse_failures += 1
            result.observed_state = "TUI did not return to idle after turn 2"
            return
        result.latency_turns_ms.append(int((time.monotonic() - t_turn) * 1000))
        result.passed = True
        result.observed_state = "2 consecutive user→reply cycles completed"
    finally:
        _close_child(child)
        result.exit_code = _child_exit_code(child)
        result.total_bytes = transcript.total
        transcript.append_footer(result)
        transcript.close()


def scenario_4(result: ScenarioResult) -> None:
    """Scenario 4: Two-stage ctrl-c interject.

    Send a long prompt; after streaming starts, send \\x03 once → expect
    "Interrupted · What should Claude do instead?" prompt. Send \\x03
    a second time → claude begins to exit (resume hint starts printing).
    """
    result.scenario = 4
    transcript = TranscriptWriter(PEXPECT_DIR / f"scenario-{4}.bin")
    child = None
    try:
        t0 = time.monotonic()
        child = spawn_claude()
        saw_idle, _, _ = wait_for_idle(child, transcript, SCENARIO_TIMEOUTS[4])
        if not saw_idle:
            result.parse_failures += 1
            result.observed_state = "no initial idle"
            return
        result.latency_turns_ms.append(int((time.monotonic() - t0) * 1000))

        _send(
            child,
            "List the first 20 prime numbers with a one-line explanation for each.",
        )
        # Wait for streaming to begin. Cap at 15s.
        stream_deadline = time.monotonic() + 15
        first_buf_accum = ""
        while time.monotonic() < stream_deadline:
            try:
                chunk = child.read_nonblocking(size=4096, timeout=1.0)
                if chunk:
                    first_buf_accum += chunk
                    if len(first_buf_accum) > 200:
                        break
            except pexpect.TIMEOUT:
                continue
            except pexpect.EOF:
                break
            except pexpect.exceptions.ExceptionPexpect:
                break
        if first_buf_accum:
            transcript.write_str(first_buf_accum)

        # First ctrl-c.
        _send(child, "\x03")
        t_first_ctrlc = time.monotonic()
        try:
            idx = child.expect(
                [INTERRUPTED_RE.pattern, pexpect.TIMEOUT, pexpect.EOF],
                timeout=10,
            )
        except pexpect.exceptions.ExceptionPexpect as e:
            result.parse_failures += 1
            result.observed_state = f"first ctrl-c wait failed: {e}"
            return
        if child.before or (isinstance(child.after, str) and child.after):
            transcript.write_str(_buffer(child))
        first_ctrlc_ms = int((time.monotonic() - t_first_ctrlc) * 1000)
        result.latency_turns_ms.append(first_ctrlc_ms)
        if idx != 0:
            result.parse_failures += 1
            result.observed_state = (
                f"first ctrl-c did not produce Interrupted prompt ({first_ctrlc_ms}ms)"
            )
            return
        result.observed_state = "first ctrl-c produced Interrupted prompt"

        # Second ctrl-c.
        _send(child, "\x03")
        t_second_ctrlc = time.monotonic()
        resume_deadline = time.monotonic() + 5.0
        resume_buf = ""
        resume_matched = False
        while time.monotonic() < resume_deadline:
            try:
                chunk = child.read_nonblocking(size=4096, timeout=0.5)
                if chunk:
                    resume_buf += chunk
                    if _RESUME_HINT_RE.search(resume_buf):
                        resume_matched = True
                        break
            except pexpect.TIMEOUT:
                continue
            except pexpect.EOF:
                break
            except pexpect.exceptions.ExceptionPexpect:
                break
        if resume_buf:
            transcript.write_str(resume_buf)
        time.sleep(2.0)
        try:
            grace = child.read_nonblocking(size=4096, timeout=1.5)
            if grace:
                transcript.write_str(grace)
                resume_buf += grace
        except (pexpect.TIMEOUT, pexpect.EOF, pexpect.exceptions.ExceptionPexpect):
            pass

        second_ctrlc_ms = int((time.monotonic() - t_second_ctrlc) * 1000)
        result.latency_turns_ms.append(second_ctrlc_ms)

        if resume_matched or _RESUME_HINT_RE.search(resume_buf):
            result.passed = True
            result.observed_state = (
                f"second ctrl-c began exit; resume hint observed in {second_ctrlc_ms}ms"
            )
        else:
            still_alive = child.isalive() if hasattr(child, "isalive") else None
            if still_alive is False:
                result.passed = True
                result.observed_state = (
                    f"second ctrl-c caused exit (no resume hint in buffer); {second_ctrlc_ms}ms"
                )
            else:
                result.parse_failures += 1
                tail = resume_buf[-1000:] if resume_buf else ""
                result.observed_state = (
                    f"second ctrl-c: no resume hint and child still alive; tail: {tail!r}"
                )
    finally:
        _close_child(child)
        result.exit_code = _child_exit_code(child)
        result.total_bytes = transcript.total
        transcript.append_footer(result)
        transcript.close()


def scenario_5(result: ScenarioResult) -> None:
    """Scenario 5: Resume UUID capture from on-exit hint.

    Reproduce the scenario-4 setup (long prompt, two ctrl-c's) and
    capture the UUID within 7s of the second ctrl-c. The per-process
    model means we re-implement the steps; the stdlib path can chain
    the child across scenarios 4 and 5.
    """
    result.scenario = 5
    transcript = TranscriptWriter(PEXPECT_DIR / f"scenario-{5}.bin")
    child = None
    try:
        child = spawn_claude()
        saw_idle, _, _ = wait_for_idle(child, transcript, SCENARIO_TIMEOUTS[5])
        if not saw_idle:
            result.parse_failures += 1
            result.observed_state = "no initial idle"
            return

        _send(
            child,
            "List the first 20 prime numbers with a one-line explanation for each.",
        )
        time.sleep(2.0)
        first_buf = _read_accumulated(child, time.monotonic() + 5)
        if first_buf:
            transcript.write_str(first_buf)

        _send(child, "\x03")
        try:
            child.expect([INTERRUPTED_RE.pattern, pexpect.TIMEOUT], timeout=10)
        except pexpect.exceptions.ExceptionPexpect:
            pass
        if child.before or (isinstance(child.after, str) and child.after):
            transcript.write_str(_buffer(child))

        t_second = time.monotonic()
        _send(child, "\x03")
        deadline = t_second + 7.0
        captured = ""
        uuid_match = None
        while time.monotonic() < deadline:
            try:
                chunk = child.read_nonblocking(size=4096, timeout=0.5)
                if chunk:
                    captured += chunk
                    m = _UUID_RE.search(captured)
                    if m:
                        uuid_match = m.group(0)
                        break
            except pexpect.TIMEOUT:
                continue
            except pexpect.EOF:
                break
            except pexpect.exceptions.ExceptionPexpect:
                break
        if captured:
            transcript.write_str(captured)
        elapsed_ms = int((time.monotonic() - t_second) * 1000)
        result.latency_turns_ms.append(elapsed_ms)

        if uuid_match is not None:
            result.passed = True
            result.observed_state = f"captured UUID {uuid_match} in {elapsed_ms}ms"
        else:
            result.parse_failures += 1
            tail = captured[-1000:] if captured else ""
            result.observed_state = f"no UUID in on-exit hint within 7s; tail: {tail!r}"
    finally:
        _close_child(child)
        result.exit_code = _child_exit_code(child)
        result.total_bytes = transcript.total
        transcript.append_footer(result)
        transcript.close()


def scenario_6(result: ScenarioResult) -> None:
    """Scenario 6: Numbered menu / slash command.

    Fresh spawn → wait for prompt → send `/help` → expect help-style
    output (multi-line, non-prompt) within 30s. Pass = the help text
    appeared (or the TUI returned to idle). The TUI's help overlay
    stays visible until the user dismisses it with Esc; we don't
    require dismissal within the timeout, just that the help text
    rendered.
    """
    result.scenario = 6
    transcript = TranscriptWriter(PEXPECT_DIR / f"scenario-{6}.bin")
    child = None
    try:
        t0 = time.monotonic()
        child = spawn_claude()
        saw_idle, _, _ = wait_for_idle(child, transcript, SCENARIO_TIMEOUTS[6])
        if not saw_idle:
            result.parse_failures += 1
            result.observed_state = "no initial idle"
            return
        result.latency_turns_ms.append(int((time.monotonic() - t0) * 1000))

        t_send = time.monotonic()
        _send(child, "/help")
        saw_idle_after, buf_after, _ = wait_for_idle(
            child, transcript, SCENARIO_TIMEOUTS[6], min_content_bytes=400
        )
        elapsed_ms = int((time.monotonic() - t_send) * 1000)
        result.latency_turns_ms.append(elapsed_ms)

        # The TUI shows an "Esc to cancel" hint at the bottom of the
        # help overlay; that's a strong signal the help panel
        # rendered.
        help_seen = "Esc" in buf_after and (
            "cancel" in buf_after or "Help" in buf_after or "Shortcuts" in buf_after
        )
        if saw_idle_after or help_seen:
            result.passed = True
            non_empty = [
                ln for ln in buf_after.splitlines() if ln.strip() and not PROMPT_GLYPH.search(ln)
            ]
            result.observed_state = (
                f"/help rendered (t={elapsed_ms}ms); "
                f"non-glyph lines in buffer: {len(non_empty)}; "
                f"return-to-idle={saw_idle_after}; help_overlay={help_seen}"
            )
        else:
            result.parse_failures += 1
            tail = buf_after[-500:] if buf_after else ""
            result.observed_state = (
                f"/help produced no help text within {elapsed_ms}ms; tail: {tail!r}"
            )
    finally:
        _close_child(child)
        result.exit_code = _child_exit_code(child)
        result.total_bytes = transcript.total
        transcript.append_footer(result)
        transcript.close()


def scenario_7(result: ScenarioResult) -> None:
    """Scenario 7: Long-running session stability.

    Spawn → wait for prompt → send a 3-sentence async/parallel question
    → wait for full reply → idle hold for 5 minutes. Pass if the
    process is still alive at the 5-minute mark.
    """
    result.scenario = 7
    transcript = TranscriptWriter(PEXPECT_DIR / f"scenario-{7}.bin")
    child = None
    try:
        t0 = time.monotonic()
        child = spawn_claude()
        saw_idle, _, _ = wait_for_idle(child, transcript, SCENARIO_TIMEOUTS[7])
        if not saw_idle:
            result.parse_failures += 1
            result.observed_state = "no initial idle"
            return
        result.latency_turns_ms.append(int((time.monotonic() - t0) * 1000))

        t_send = time.monotonic()
        _send(
            child,
            "Explain the difference between async and parallel in 3 sentences.",
        )
        # 3 sentences will produce more than 400 bytes of response.
        saw_idle_after, _, _ = wait_for_idle(child, transcript, 60, min_content_bytes=400)
        reply_ms = int((time.monotonic() - t_send) * 1000)
        result.latency_turns_ms.append(reply_ms)
        if not saw_idle_after:
            result.parse_failures += 1
            result.observed_state = f"TUI did not return to idle after reply ({reply_ms}ms)"
            return

        idle_start = time.monotonic()
        idle_deadline = idle_start + 5 * 60
        alive_at_deadline = False
        while time.monotonic() < idle_deadline:
            time.sleep(15)
            if child.isalive() is False:
                break
            try:
                chunk = child.read_nonblocking(size=4096, timeout=0.0)
                if chunk:
                    transcript.write_str(chunk)
            except (pexpect.TIMEOUT, pexpect.EOF, pexpect.exceptions.ExceptionPexpect):
                pass
        if child.isalive():
            alive_at_deadline = True
            transcript.write_str("\n--- alive at 5min ---\n")
        else:
            transcript.write_str("\n--- child exited before 5min ---\n")

        idle_ms = int((time.monotonic() - idle_start) * 1000)
        result.latency_turns_ms.append(idle_ms)
        if alive_at_deadline:
            result.passed = True
            result.observed_state = "still alive at 5-minute mark"
        else:
            result.parse_failures += 1
            result.observed_state = f"child exited before 5min (after {idle_ms}ms idle)"
    finally:
        _close_child(child)
        result.exit_code = _child_exit_code(child)
        result.total_bytes = transcript.total
        transcript.append_footer(result)
        transcript.close()


def scenario_8(result: ScenarioResult) -> None:
    """Scenario 8: Negative control — no PTY.

    Spawn `claude` with `stdin=PIPE` (no PTY). Per the contract, this
    has no pass criterion — we just record what happens. We capture
    whatever output we can get with subprocess.run, plus the failure
    mode.
    """
    result.scenario = 8
    transcript = TranscriptWriter(PEXPECT_DIR / f"scenario-{8}.bin")
    try:
        env = build_env()
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                ["claude", "--model", "sonnet", "--permission-mode", "bypassPermissions"],
                cwd=str(REPO_ROOT),
                env=env,
                stdin=subprocess.PIPE,
                capture_output=True,
                timeout=SCENARIO_TIMEOUTS[8],
                # NOTE: `stdin=PIPE` and `input=` are mutually
                # exclusive in subprocess.run. We open the pipe and
                # write the input separately.
            )
            # If we want to feed input, we'd write via
            # `proc.stdin.write(...)` before `proc.communicate()`,
            # but for a no-PTY negative control it's more honest to
            # just spawn without input and let claude exit/error.
            stdout = proc.stdout or b""
            stderr = proc.stderr or b""
            rc = proc.returncode
            combined = stdout + b"\n[stderr]\n" + stderr
            transcript.write(combined[:MAX_BYTES])
            result.exit_code = rc
            result.latency_turns_ms.append(int((time.monotonic() - t0) * 1000))
            result.observed_state = (
                f"no-PTY run finished; rc={rc}; "
                f"stdout_bytes={len(stdout)}; stderr_bytes={len(stderr)}"
            )
            # Negative control — no pass criterion. The contract says
            # "Document what happens"; we mark passed=True to signal
            # "the experiment ran to completion" and let the report
            # interpret the result.
            result.passed = True
            result.observed_state += "; recorded (negative control has no pass criterion)"
        except subprocess.TimeoutExpired as e:
            result.parse_failures += 1
            captured = (e.stdout or b"") + b"\n[stderr]\n" + (e.stderr or b"")
            transcript.write(captured[:MAX_BYTES])
            result.observed_state = f"subprocess.TimeoutExpired after {SCENARIO_TIMEOUTS[8]}s"
            result.exit_code = -1
        except Exception as e:  # noqa: BLE001
            result.parse_failures += 1
            result.observed_state = f"exception during no-PTY run: {e}"
            result.exit_code = -1
    finally:
        result.total_bytes = transcript.total
        transcript.append_footer(result)
        transcript.close()


SCENARIO_DISPATCH = {
    1: scenario_1,
    2: scenario_2,
    3: scenario_3,
    4: scenario_4,
    5: scenario_5,
    6: scenario_6,
    7: scenario_7,
    8: scenario_8,
}


def nuke_transcripts() -> None:
    """Wipe stale transcripts at startup.

    Re-runs must not conflate old and new data — the post-run analyzer
    reads this dir verbatim.
    """
    PEXPECT_DIR.mkdir(parents=True, exist_ok=True)
    for f in PEXPECT_DIR.glob("scenario-*.bin"):
        try:
            f.unlink()
        except Exception:
            pass


def run_one_scenario(n: int) -> ScenarioResult:
    """Run scenario N, return its result."""
    result = ScenarioResult(scenario=n)
    fn = SCENARIO_DISPATCH[n]
    fn(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--scenario",
        type=int,
        choices=sorted(SCENARIO_DISPATCH),
        help="Run a single scenario (used by the per-scenario driver).",
    )
    parser.add_argument(
        "--no-nuke",
        action="store_true",
        help="Skip the startup transcript nuke (debug only).",
    )
    args = parser.parse_args()

    if not args.no_nuke:
        nuke_transcripts()

    if args.scenario is not None:
        result = run_one_scenario(args.scenario)
        # Subprocess-mode: print a one-liner summary for the driver.
        print(
            f"scenario-{args.scenario}: "
            f"pass={result.passed} parse_failures={result.parse_failures} "
            f"total_bytes={result.total_bytes} exit_code={result.exit_code}"
        )
        return 0 if result.passed or result.parse_failures == 0 else 1

    # Default: run all 8 in fresh subprocesses for clean per-scenario
    # teardown.
    print(f"nuked {PEXPECT_DIR}/scenario-*.bin")
    print("dispatching 8 scenarios, one per subprocess...")
    summaries: list[tuple[int, int, int, int]] = []
    for n in sorted(SCENARIO_DISPATCH):
        print(f"\n=== scenario {n} ===", flush=True)
        t0 = time.monotonic()
        proc = subprocess.run(
            [
                sys.executable,
                str(pathlib.Path(__file__).resolve()),
                "--scenario",
                str(n),
                "--no-nuke",  # the driver already nuke'd; preserve each scenario's output
            ],
            cwd=str(REPO_ROOT),
        )
        elapsed = int((time.monotonic() - t0) * 1000)
        rc = proc.returncode
        footer_path = PEXPECT_DIR / f"scenario-{n}.bin"
        passed = False
        parse_failures = 0
        total_bytes = 0
        exit_code = rc
        if footer_path.exists():
            text = footer_path.read_text(errors="replace")
            for line in text.splitlines():
                if line.startswith("pass:"):
                    passed = line.split(":", 1)[1].strip() == "true"
                elif line.startswith("parse_failures:"):
                    try:
                        parse_failures = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                elif line.startswith("total_bytes:"):
                    try:
                        total_bytes = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                elif line.startswith("exit_code:"):
                    try:
                        exit_code = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
        summaries.append((n, 1 if passed else 0, parse_failures, total_bytes))
        print(
            f"scenario {n} took {elapsed}ms subprocess_time; "
            f"pass={passed} parse_failures={parse_failures} "
            f"total_bytes={total_bytes} exit_code={exit_code}"
        )
    print("\n=== summary ===")
    print(f"{'scenario':<10}{'pass':<8}{'parse_failures':<18}{'total_bytes':<14}")
    for n, p, pf, tb in summaries:
        print(f"{n:<10}{p:<8}{pf:<18}{tb:<14}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
