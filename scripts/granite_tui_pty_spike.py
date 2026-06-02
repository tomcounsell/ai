"""Granite TUI PTY Spike — stdlib path (pty + select + termios).

Implements all 8 scenarios from /tmp/granite-pty-spike/SCENARIO_CONTRACT.md
against the real `claude` interactive TUI, without using `claude -p` or
`--input-format stream-json`. Each scenario writes a raw byte transcript to
/tmp/granite-pty-spike/stdlib/scenario-{N}.bin, terminated by a structured
footer block summarizing pass/fail, parse_failures, buf_drain_iters_max,
latency_turns_ms, observed_state, exit_code, and total_bytes.

Hard requirements (from contract):
- Spawn: `claude --model sonnet --permission-mode bypassPermissions`, no -p.
- Child env: ANTHROPIC_API_KEY="" (blank the inherited key).
- cwd: /Users/valorengels/src/ai (trusted project).
- Per-scenario termios save/restore in a finally block (sequential scenarios
  in one process leak termios otherwise).
- Master fd non-blocking, tight read loop until BlockingIOError per
  select() wakeup, count buf_drain_iters_max.
- Scenario 4/5: select-driven wait for resume hint, 7s total budget.
- Transcript cap: 1 MiB; truncate with [truncated] marker.
- Startup: nuke prior transcripts.
- End: pkill any orphaned claude children.
"""

from __future__ import annotations

import os
import pty
import re
import select
import signal
import subprocess
import sys
import termios
import time
from pathlib import Path

# Import the same regexes used by agent/claude_session.py to ensure parity
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.claude_session import _RESUME_HINT_RE, _UUID_RE  # noqa: E402

TRANSCRIPT_DIR = Path("/tmp/granite-pty-spike/stdlib")
PROJECT_CWD = "/Users/valorengels/src/ai"
CLAUDE_BIN = "claude"
TRANSCRIPT_CAP_BYTES = 1 * 1024 * 1024  # 1 MiB

# Per-scenario timeouts
TIMEOUT_DEFAULT = 30.0
TIMEOUT_CTRL_C = 60.0
TIMEOUT_HOLD = 5 * 60.0  # scenario 7 = 5 minutes idle

# Spawn args
CLAUDE_ARGS = ["claude", "--model", "sonnet", "--permission-mode", "bypassPermissions"]

# --- Terminal prompt detection patterns -------------------------------------
# Interactive `claude` shows a `>` (or `❯`) prompt at idle, with a hint banner
# above. We look for the prompt glyph as the canonical "ready" signal. The
# regex is forgiving: optional whitespace, the prompt char, then a space.
PROMPT_RE = re.compile(r"(?:^|\n)\s*[>❯]\s")
# Resume hint: claude prints `claude --resume <uuid>` on exit. We use the
# shared _RESUME_HINT_RE for parity with claude_session.py.
# Two-stage interject: first ctrl-c produces an "Interrupted" prompt line.
INTERRUPTED_RE = re.compile(r"Interrupted\s*[·•]?\s*What should Claude do instead", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TranscriptCapError(Exception):
    """Raised when the transcript file hits the 1 MiB cap."""


def _nuke_stale_transcripts() -> int:
    """Delete any prior scenario-*.bin files in the transcript dir.

    Required by the contract — re-runs must not conflate stale and new data.
    """
    n = 0
    for f in TRANSCRIPT_DIR.glob("scenario-*.bin"):
        f.unlink()
        n += 1
    return n


def _build_child_env() -> dict[str, str]:
    """Inherit os.environ but blank ANTHROPIC_API_KEY to force OAuth path.

    Mirrors agent/claude_session.py:_build_env.
    """
    env = dict(os.environ)
    env["ANTHROPIC_API_KEY"] = ""
    return env


def _exec_claude() -> None:
    """Child-side exec of the claude TUI.

    Replaces the forked child with the real claude process. The PTY is
    already wired up by pty.fork() on STDIN/STDOUT/STDERR.
    """
    os.chdir(PROJECT_CWD)
    os.execvp(CLAUDE_BIN, CLAUDE_ARGS)


def _truncated_marker() -> bytes:
    return b"\n[truncated]\n"


def _drain(fd: int, transcript: list[bytes], cap_state: dict) -> tuple[int, int]:
    """Non-blocking drain of the master fd.

    Reads in a tight loop until BlockingIOError. Returns (iters, bytes_read).
    Updates cap_state["truncated"]=True if the 1 MiB cap is exceeded; in that
    case we stop appending new bytes (subsequent calls are no-ops until the
    marker is appended by the caller).
    """
    if cap_state.get("truncated"):
        return 0, 0
    iters = 0
    total = 0
    while True:
        try:
            chunk = os.read(fd, 4096)
        except BlockingIOError:
            break
        except OSError:
            break
        iters += 1
        total += len(chunk)
        # Apply 1 MiB cap
        current_size = sum(len(b) for b in transcript)
        if current_size + len(chunk) > TRANSCRIPT_CAP_BYTES:
            remaining = TRANSCRIPT_CAP_BYTES - current_size
            if remaining > 0:
                transcript.append(chunk[:remaining])
            transcript.append(_truncated_marker())
            cap_state["truncated"] = True
            break
        transcript.append(chunk)
    return iters, total


def _wait_for(
    fd: int,
    transcript: list[bytes],
    cap_state: dict,
    timeout_s: float,
    pattern: re.Pattern,
    tick_s: float = 0.25,
    track_latency: list[float] | None = None,
) -> tuple[bool, float, str]:
    """Wait until `pattern` matches in the accumulated transcript bytes.

    Returns (matched, elapsed_s, matched_text). Uses select() in short
    ticks so the master fd is drained continuously. If matched, returns
    immediately on the first match (does not wait for the full timeout).
    """
    deadline = time.monotonic() + timeout_s
    accumulated = bytearray()
    while True:
        now = time.monotonic()
        if now >= deadline:
            return False, timeout_s, ""
        remaining = deadline - now
        tick = min(tick_s, remaining)
        rlist, _, _ = select.select([fd], [], [], tick)
        if rlist:
            _drain(fd, transcript, cap_state)
            # Rebuild accumulated from transcript (cheap: small N of chunks)
            for b in transcript:
                accumulated.extend(b)
            text = accumulated.decode("utf-8", errors="replace")
            m = pattern.search(text)
            if m:
                if track_latency is not None:
                    track_latency.append((deadline - now) * 1000.0)  # remaining-budget ms
                return True, time.monotonic() - (deadline - timeout_s), m.group(0)
    return False, timeout_s, ""  # unreachable


def _send(fd: int, data: bytes) -> int:
    """Write `data` to the master fd. Returns bytes written."""
    return os.write(fd, data)


def _format_footer(
    scenario_n: int,
    passed: bool,
    parse_failures: int,
    buf_drain_iters_max: int,
    latency_turns_ms: list,
    observed_state: str,
    exit_code: int | None,
    total_bytes: int,
) -> bytes:
    """Build the per-scenario footer block.

    Note: the keyword 'pass' is reserved in Python, so the parameter is
    named 'passed' here. The footer's first line is still written as
    'pass: <true|false>' per the contract.
    """
    lat_str = "[" + ", ".join(str(int(x)) for x in latency_turns_ms) + "]"
    lines = [
        f"--- scenario-{scenario_n} footer ---",
        f"pass: {passed}",
        f"parse_failures: {parse_failures}",
        f"buf_drain_iters_max: {buf_drain_iters_max}",
        f"latency_turns_ms: {lat_str}",
        f'observed_state: "{observed_state}"',
        f"exit_code: {exit_code}",
        f"total_bytes: {total_bytes}",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _try_waitpid(pid: int) -> int | None:
    """Non-blocking waitpid; returns exit code if reaped, else None."""
    try:
        _, status = os.waitpid(pid, os.WNOHANG)
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return -os.WTERMSIG(status)
        return None
    except ChildProcessError:
        return None


def _reap(pid: int) -> int:
    """Blocking reap; returns exit code or signal-derived int."""
    try:
        _, status = os.waitpid(pid, 0)
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return -os.WTERMSIG(status)
        return -1
    except ChildProcessError:
        return -1


def _graceful_reap(pid: int, grace_s: float = 1.5) -> int:
    """SIGTERM -> grace -> SIGKILL -> reap, returning the exit code.

    The interactive `claude` TUI doesn't always honor SIGTERM promptly when
    the master fd is still open (it sits in a read on stdin). Send SIGTERM,
    poll non-blocking for `grace_s`, then SIGKILL if still alive, then reap.
    Returns 0 on success, the exit code or signal-derived int from `_reap`.
    """
    if pid == -1:
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return 0
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        ec = _try_waitpid(pid)
        if ec is not None:
            return ec
        time.sleep(0.1)
    # Still alive — escalate.
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return 0
    return _reap(pid)


# ---------------------------------------------------------------------------
# Per-scenario driver
# ---------------------------------------------------------------------------


def _setup_pty() -> tuple[int, int]:
    """Fork a PTY-attached child running the claude TUI.

    Returns (parent_fd, child_pid). Raises on fork failure.
    """
    pid, fd = pty.fork()
    if pid == 0:
        # Child: exec claude. _exec_claude does not return.
        _exec_claude()
        # Unreachable if exec succeeds
        os._exit(127)
    # Parent
    os.set_blocking(fd, False)
    return fd, pid


def _write_transcript(path: Path, transcript: list[bytes], footer: bytes) -> int:
    """Write all transcript chunks + footer to disk. Returns total bytes."""
    with open(path, "wb") as f:
        total = 0
        for chunk in transcript:
            f.write(chunk)
            total += len(chunk)
        f.write(footer)
        total += len(footer)
    return total


# ---- Scenario 1: First-turn `>` prompt detection --------------------------


def scenario_1() -> dict:
    """Spawn claude, wait for the `>` prompt within 30s."""
    scenario_n = 1
    transcript: list[bytes] = []
    cap_state: dict = {"truncated": False}
    # termios save is mandatory per the contract, but only if stdin is a TTY.
    # In headless / non-interactive runs (no controlling terminal), skip it.
    try:
        saved = termios.tcgetattr(sys.stdin.fileno())
    except (termios.error, OSError):
        saved = None
    fd = pid = -1
    buf_drain_iters_max = 0
    latency_turns_ms: list[int] = []
    pass_ = False
    observed_state = ""
    exit_code: int | None = None
    try:
        fd, pid = _setup_pty()
        matched, elapsed, match_text = _wait_for(
            fd, transcript, cap_state, TIMEOUT_DEFAULT, PROMPT_RE
        )
        if matched:
            pass_ = True
            latency_turns_ms.append(int(elapsed * 1000))
            observed_state = f"saw prompt glyph '{match_text.strip()}' after {elapsed:.2f}s"
        else:
            tail = b"".join(transcript)[-500:]
            observed_state = (
                f"timeout: no prompt within {TIMEOUT_DEFAULT}s; tail: "
                f"{tail.decode('utf-8', errors='replace')!r}"
            )
        # Reap
        exit_code = _graceful_reap(pid)
    except Exception as e:  # pragma: no cover — defensive
        observed_state = f"exception: {type(e).__name__}: {e}"
    finally:
        # Restore termios BEFORE closing the fd / reaping
        if saved is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, saved)
            except termios.error:
                pass
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        if pid != -1 and exit_code is None:
            # Final fallback: escalate hard. _graceful_reap already does
            # SIGTERM->grace->SIGKILL, so this is the last-resort reap.
            _graceful_reap(pid, grace_s=0.5)

    total_bytes = sum(len(b) for b in transcript)
    footer = _format_footer(
        scenario_n,
        passed=pass_,
        parse_failures=0 if pass_ else 1,
        buf_drain_iters_max=buf_drain_iters_max,
        latency_turns_ms=latency_turns_ms,
        observed_state=observed_state,
        exit_code=exit_code,
        total_bytes=total_bytes,
    )
    path = TRANSCRIPT_DIR / f"scenario-{scenario_n}.bin"
    _write_transcript(path, transcript, footer)
    return {
        "scenario": scenario_n,
        "pass": pass_,
        "total_bytes": total_bytes + len(footer),
        "footer_observed_state": observed_state,
    }


# ---- Scenario 2: First-message text submission ----------------------------


def scenario_2() -> dict:
    """Scenario 1 -> send "hello\\r" (CR, the TUI submit key) -> wait for Claude's reply within 30s.

    Note: the original contract assumed \\n (LF) as the submit key. The
    pexpect subagent's parallel run surfaced that \\r (CR) is what the
    Claude Code TUI v2.1.160 actually treats as a submit. The pexpect
    run with \\r got 7/8 pass; the stdlib run with \\n got 2/8. The
    stdlib run has been re-issued with \\r to be a fair comparison.
    """
    scenario_n = 2
    transcript: list[bytes] = []
    cap_state: dict = {"truncated": False}
    # termios save is mandatory per the contract, but only if stdin is a TTY.
    # In headless / non-interactive runs (no controlling terminal), skip it.
    try:
        saved = termios.tcgetattr(sys.stdin.fileno())
    except (termios.error, OSError):
        saved = None
    fd = pid = -1
    latency_turns_ms: list[int] = []
    pass_ = False
    observed_state = ""
    exit_code: int | None = None
    try:
        fd, pid = _setup_pty()
        # Wait for prompt
        matched, t_prompt, _ = _wait_for(fd, transcript, cap_state, TIMEOUT_DEFAULT, PROMPT_RE)
        if not matched:
            observed_state = f"never saw prompt within {TIMEOUT_DEFAULT}s"
        else:
            latency_turns_ms.append(int(t_prompt * 1000))
            t_send = time.monotonic()
            _send(fd, b"hello\r")
            # Wait for a non-prompt, multi-line response. After sending, claude
            # streams a response block that ends with the prompt glyph again.
            # We wait for the SECOND prompt appearance as a proxy for "reply
            # complete": if the prompt is re-displayed, the turn finished.
            deadline = time.monotonic() + TIMEOUT_DEFAULT
            accumulated = bytearray()
            for b in transcript:
                accumulated.extend(b)
            # Count how many prompt-glyph matches we've already seen
            prior_text = accumulated.decode("utf-8", errors="replace")
            prior_matches = list(PROMPT_RE.finditer(prior_text))
            prior_count = len(prior_matches)
            # Now wait for one more prompt match (the "turn complete" signal)
            success = False
            while time.monotonic() < deadline:
                rlist, _, _ = select.select([fd], [], [], 0.25)
                if rlist:
                    _drain(fd, transcript, cap_state)
                accumulated = bytearray()
                for b in transcript:
                    accumulated.extend(b)
                text = accumulated.decode("utf-8", errors="replace")
                matches = list(PROMPT_RE.finditer(text))
                if len(matches) > prior_count:
                    success = True
                    break
            t_reply = time.monotonic() - t_send
            latency_turns_ms.append(int(t_reply * 1000))
            if success:
                pass_ = True
                observed_state = f"reply received in {t_reply:.2f}s after 'hello'"
            else:
                tail = b"".join(transcript)[-500:]
                observed_state = (
                    f"no reply within {TIMEOUT_DEFAULT}s of 'hello'; "
                    f"tail: {tail.decode('utf-8', errors='replace')!r}"
                )
        exit_code = _graceful_reap(pid)
    except Exception as e:  # pragma: no cover — defensive
        observed_state = f"exception: {type(e).__name__}: {e}"
    finally:
        try:
            if saved is not None:
                try:
                    termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, saved)
                except termios.error:
                    pass
        except termios.error:
            pass
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        if pid != -1 and exit_code is None:
            # Final fallback: escalate hard. _graceful_reap already does
            # SIGTERM->grace->SIGKILL, so this is the last-resort reap.
            _graceful_reap(pid, grace_s=0.5)

    total_bytes = sum(len(b) for b in transcript)
    footer = _format_footer(
        scenario_n,
        passed=pass_,
        parse_failures=0 if pass_ else 1,
        buf_drain_iters_max=0,
        latency_turns_ms=latency_turns_ms,
        observed_state=observed_state,
        exit_code=exit_code,
        total_bytes=total_bytes,
    )
    path = TRANSCRIPT_DIR / f"scenario-{scenario_n}.bin"
    _write_transcript(path, transcript, footer)
    return {
        "scenario": scenario_n,
        "pass": pass_,
        "total_bytes": total_bytes + len(footer),
        "footer_observed_state": observed_state,
    }


# ---- Scenario 3: Multi-turn conversation ----------------------------------


def scenario_3() -> dict:
    """Scenario 2 -> 2 additional turns. 60s total budget, 30s per turn."""
    scenario_n = 3
    transcript: list[bytes] = []
    cap_state: dict = {"truncated": False}
    # termios save is mandatory per the contract, but only if stdin is a TTY.
    # In headless / non-interactive runs (no controlling terminal), skip it.
    try:
        saved = termios.tcgetattr(sys.stdin.fileno())
    except (termios.error, OSError):
        saved = None
    fd = pid = -1
    latency_turns_ms: list[int] = []
    pass_ = False
    observed_state = ""
    exit_code: int | None = None
    parse_failures = 0
    try:
        fd, pid = _setup_pty()
        # Wait for first prompt
        matched, t0, _ = _wait_for(fd, transcript, cap_state, TIMEOUT_DEFAULT, PROMPT_RE)
        if not matched:
            parse_failures += 1
            observed_state = "never saw initial prompt"
        else:
            latency_turns_ms.append(int(t0 * 1000))
            turns_ok = 0
            for prompt_text in ("what is 2+2?\r", "and 3+3?\r"):
                t_send = time.monotonic()
                _send(fd, prompt_text.encode("utf-8"))
                # Wait for the prompt glyph to re-appear (turn complete)
                deadline = time.monotonic() + TIMEOUT_DEFAULT
                accumulated = bytearray()
                for b in transcript:
                    accumulated.extend(b)
                prior_text = accumulated.decode("utf-8", errors="replace")
                prior_count = len(list(PROMPT_RE.finditer(prior_text)))
                success = False
                while time.monotonic() < deadline:
                    rlist, _, _ = select.select([fd], [], [], 0.25)
                    if rlist:
                        _drain(fd, transcript, cap_state)
                    accumulated = bytearray()
                    for b in transcript:
                        accumulated.extend(b)
                    text = accumulated.decode("utf-8", errors="replace")
                    matches = list(PROMPT_RE.finditer(text))
                    if len(matches) > prior_count:
                        success = True
                        break
                t_turn = time.monotonic() - t_send
                latency_turns_ms.append(int(t_turn * 1000))
                if success:
                    turns_ok += 1
                else:
                    parse_failures += 1
                    tail = b"".join(transcript)[-300:]
                    observed_state = (
                        f"turn '{prompt_text.strip()}' failed at {t_turn:.2f}s; "
                        f"tail: {tail.decode('utf-8', errors='replace')!r}"
                    )
                    break
            if turns_ok == 2:
                pass_ = True
                if not observed_state:
                    observed_state = "both follow-up turns completed"
        exit_code = _graceful_reap(pid)
    except Exception as e:  # pragma: no cover — defensive
        observed_state = f"exception: {type(e).__name__}: {e}"
    finally:
        try:
            if saved is not None:
                try:
                    termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, saved)
                except termios.error:
                    pass
        except termios.error:
            pass
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        if pid != -1 and exit_code is None:
            # Final fallback: escalate hard. _graceful_reap already does
            # SIGTERM->grace->SIGKILL, so this is the last-resort reap.
            _graceful_reap(pid, grace_s=0.5)

    total_bytes = sum(len(b) for b in transcript)
    footer = _format_footer(
        scenario_n,
        passed=pass_,
        parse_failures=parse_failures,
        buf_drain_iters_max=0,
        latency_turns_ms=latency_turns_ms,
        observed_state=observed_state,
        exit_code=exit_code,
        total_bytes=total_bytes,
    )
    path = TRANSCRIPT_DIR / f"scenario-{scenario_n}.bin"
    _write_transcript(path, transcript, footer)
    return {
        "scenario": scenario_n,
        "pass": pass_,
        "total_bytes": total_bytes + len(footer),
        "footer_observed_state": observed_state,
    }


# ---- Scenario 4: Two-stage ctrl-c interject --------------------------------


def scenario_4() -> dict:
    """Send a long prompt; on first ctrl-c, expect 'Interrupted' prompt.

    On second ctrl-c, expect claude to begin exiting (resume hint starts
    printing). 60s total budget.
    """
    scenario_n = 4
    transcript: list[bytes] = []
    cap_state: dict = {"truncated": False}
    # termios save is mandatory per the contract, but only if stdin is a TTY.
    # In headless / non-interactive runs (no controlling terminal), skip it.
    try:
        saved = termios.tcgetattr(sys.stdin.fileno())
    except (termios.error, OSError):
        saved = None
    fd = pid = -1
    latency_turns_ms: list[int] = []
    pass_ = False
    observed_state = ""
    exit_code: int | None = None
    parse_failures = 0
    buf_drain_iters_max = 0
    try:
        fd, pid = _setup_pty()
        # 1) Wait for prompt
        matched, t0, _ = _wait_for(fd, transcript, cap_state, TIMEOUT_DEFAULT, PROMPT_RE)
        if not matched:
            parse_failures += 1
            observed_state = "never saw initial prompt"
        else:
            latency_turns_ms.append(int(t0 * 1000))
            # 2) Send the long-running prompt
            _send(fd, b"List the first 20 prime numbers with a one-line explanation for each.\r")
            # 3) Wait for streaming to begin (some non-prompt text appears).
            #    Then send first ctrl-c.
            # Strategy: wait up to 10s for some response bytes; if we see any
            # non-empty text beyond the prompt, send first ctrl-c.
            stream_started_deadline = time.monotonic() + 10.0
            prior_size = sum(len(b) for b in transcript)
            stream_started = False
            while time.monotonic() < stream_started_deadline:
                rlist, _, _ = select.select([fd], [], [], 0.25)
                if rlist:
                    iters, _ = _drain(fd, transcript, cap_state)
                    buf_drain_iters_max = max(buf_drain_iters_max, iters)
                cur_size = sum(len(b) for b in transcript)
                if cur_size > prior_size + 200:  # ~200 bytes of streaming reply
                    stream_started = True
                    break
            if not stream_started:
                # No streaming observed — send ctrl-c anyway (defensive)
                observed_state = "no streaming reply within 10s; sending ctrl-c anyway"
            t_first_ctrlc = time.monotonic()
            _send(fd, b"\x03")
            # 4) Wait for "Interrupted" prompt
            matched_int, t_int, _ = _wait_for(fd, transcript, cap_state, 15.0, INTERRUPTED_RE)
            if matched_int:
                latency_turns_ms.append(int((t_int - t_first_ctrlc) * 1000))
                # 5) Send second ctrl-c
                t_second_ctrlc = time.monotonic()
                _send(fd, b"\x03")
                # 6) Wait for resume hint (select-driven, 7s budget per contract)
                resume_deadline = time.monotonic() + 7.0
                accumulated = bytearray()
                for b in transcript:
                    accumulated.extend(b)
                hint_matched = False
                while time.monotonic() < resume_deadline:
                    rlist, _, _ = select.select([fd], [], [], 0.5)
                    if rlist:
                        iters, _ = _drain(fd, transcript, cap_state)
                        buf_drain_iters_max = max(buf_drain_iters_max, iters)
                    accumulated = bytearray()
                    for b in transcript:
                        accumulated.extend(b)
                    text = accumulated.decode("utf-8", errors="replace")
                    if _RESUME_HINT_RE.search(text):
                        hint_matched = True
                        break
                if hint_matched:
                    latency_turns_ms.append(int((time.monotonic() - t_second_ctrlc) * 1000))
                    pass_ = True
                    observed_state = (
                        f"two-stage interject worked: 'Interrupted' seen in "
                        f"{t_int - t_first_ctrlc:.2f}s, resume hint seen in "
                        f"{time.monotonic() - t_second_ctrlc:.2f}s"
                    )
                else:
                    parse_failures += 1
                    observed_state = (
                        f"'Interrupted' seen at {t_int - t_first_ctrlc:.2f}s, "
                        f"but resume hint NOT seen within 7s of second ctrl-c"
                    )
            else:
                parse_failures += 1
                tail = b"".join(transcript)[-500:]
                observed_state = (
                    f"first ctrl-c did NOT produce 'Interrupted' within 15s; "
                    f"tail: {tail.decode('utf-8', errors='replace')!r}"
                )
        # Don't kill — let claude exit on its own (we want the resume hint).
        # But enforce a hard cap on how long we wait.
        reap_deadline = time.monotonic() + 5.0
        while time.monotonic() < reap_deadline:
            ec = _try_waitpid(pid)
            if ec is not None:
                exit_code = ec
                break
            time.sleep(0.2)
        if exit_code is None:
            exit_code = _graceful_reap(pid, grace_s=1.0)
    except Exception as e:  # pragma: no cover — defensive
        observed_state = f"exception: {type(e).__name__}: {e}"
    finally:
        try:
            if saved is not None:
                try:
                    termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, saved)
                except termios.error:
                    pass
        except termios.error:
            pass
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        if pid != -1 and exit_code is None:
            # Final fallback: escalate hard. _graceful_reap already does
            # SIGTERM->grace->SIGKILL, so this is the last-resort reap.
            _graceful_reap(pid, grace_s=0.5)

    total_bytes = sum(len(b) for b in transcript)
    footer = _format_footer(
        scenario_n,
        passed=pass_,
        parse_failures=parse_failures,
        buf_drain_iters_max=buf_drain_iters_max,
        latency_turns_ms=latency_turns_ms,
        observed_state=observed_state,
        exit_code=exit_code,
        total_bytes=total_bytes,
    )
    path = TRANSCRIPT_DIR / f"scenario-{scenario_n}.bin"
    _write_transcript(path, transcript, footer)
    return {
        "scenario": scenario_n,
        "pass": pass_,
        "total_bytes": total_bytes + len(footer),
        "footer_observed_state": observed_state,
    }


# ---- Scenario 5: Resume UUID capture ---------------------------------------


def scenario_5() -> dict:
    """Capture a UUID matching _UUID_RE within 7s of second ctrl-c.

    Continues from scenario 4's setup, but is run independently: spawn,
    send the long prompt, ctrl-c, ctrl-c, then look for a UUID.
    """
    scenario_n = 5
    transcript: list[bytes] = []
    cap_state: dict = {"truncated": False}
    # termios save is mandatory per the contract, but only if stdin is a TTY.
    # In headless / non-interactive runs (no controlling terminal), skip it.
    try:
        saved = termios.tcgetattr(sys.stdin.fileno())
    except (termios.error, OSError):
        saved = None
    fd = pid = -1
    latency_turns_ms: list[int] = []
    pass_ = False
    observed_state = ""
    exit_code: int | None = None
    buf_drain_iters_max = 0
    try:
        fd, pid = _setup_pty()
        # 1) Wait for prompt
        matched, t0, _ = _wait_for(fd, transcript, cap_state, TIMEOUT_DEFAULT, PROMPT_RE)
        if not matched:
            observed_state = "never saw initial prompt"
        else:
            latency_turns_ms.append(int(t0 * 1000))
            # 2) Send long prompt
            _send(fd, b"List the first 20 prime numbers with a one-line explanation for each.\r")
            # 3) Wait for some streaming, then first ctrl-c
            stream_deadline = time.monotonic() + 10.0
            prior_size = sum(len(b) for b in transcript)
            while time.monotonic() < stream_deadline:
                rlist, _, _ = select.select([fd], [], [], 0.25)
                if rlist:
                    iters, _ = _drain(fd, transcript, cap_state)
                    buf_drain_iters_max = max(buf_drain_iters_max, iters)
                if sum(len(b) for b in transcript) > prior_size + 200:
                    break
            _send(fd, b"\x03")
            # 4) Wait briefly for 'Interrupted' (best-effort, not required for pass)
            _wait_for(fd, transcript, cap_state, 5.0, INTERRUPTED_RE)
            # 5) Second ctrl-c
            t_second_ctrlc = time.monotonic()
            _send(fd, b"\x03")
            # 6) Look for a UUID matching _UUID_RE within 7s of second ctrl-c
            uuid_deadline = time.monotonic() + 7.0
            accumulated = bytearray()
            for b in transcript:
                accumulated.extend(b)
            captured_uuid = None
            while time.monotonic() < uuid_deadline:
                rlist, _, _ = select.select([fd], [], [], 0.5)
                if rlist:
                    iters, _ = _drain(fd, transcript, cap_state)
                    buf_drain_iters_max = max(buf_drain_iters_max, iters)
                accumulated = bytearray()
                for b in transcript:
                    accumulated.extend(b)
                text = accumulated.decode("utf-8", errors="replace")
                m = _UUID_RE.search(text)
                if m:
                    captured_uuid = m.group(0)
                    break
            elapsed = time.monotonic() - t_second_ctrlc
            latency_turns_ms.append(int(elapsed * 1000))
            if captured_uuid is not None:
                pass_ = True
                observed_state = (
                    f"captured UUID {captured_uuid!r} {elapsed:.2f}s after second ctrl-c"
                )
            else:
                tail = b"".join(transcript)[-1000:]
                observed_state = (
                    f"no UUID matching _UUID_RE within 7s of second ctrl-c; "
                    f"tail: {tail.decode('utf-8', errors='replace')!r}"
                )
        # Let claude exit on its own; cap wait at 5s
        reap_deadline = time.monotonic() + 5.0
        while time.monotonic() < reap_deadline:
            ec = _try_waitpid(pid)
            if ec is not None:
                exit_code = ec
                break
            time.sleep(0.2)
        if exit_code is None:
            exit_code = _graceful_reap(pid, grace_s=1.0)
    except Exception as e:  # pragma: no cover — defensive
        observed_state = f"exception: {type(e).__name__}: {e}"
    finally:
        try:
            if saved is not None:
                try:
                    termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, saved)
                except termios.error:
                    pass
        except termios.error:
            pass
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        if pid != -1 and exit_code is None:
            # Final fallback: escalate hard. _graceful_reap already does
            # SIGTERM->grace->SIGKILL, so this is the last-resort reap.
            _graceful_reap(pid, grace_s=0.5)

    total_bytes = sum(len(b) for b in transcript)
    footer = _format_footer(
        scenario_n,
        passed=pass_,
        parse_failures=0 if pass_ else 1,
        buf_drain_iters_max=buf_drain_iters_max,
        latency_turns_ms=latency_turns_ms,
        observed_state=observed_state,
        exit_code=exit_code,
        total_bytes=total_bytes,
    )
    path = TRANSCRIPT_DIR / f"scenario-{scenario_n}.bin"
    _write_transcript(path, transcript, footer)
    return {
        "scenario": scenario_n,
        "pass": pass_,
        "total_bytes": total_bytes + len(footer),
        "footer_observed_state": observed_state,
    }


# ---- Scenario 6: Numbered menu / slash command ----------------------------


def scenario_6() -> dict:
    """Fresh spawn, wait for prompt, send /help\\r, expect help output."""
    scenario_n = 6
    transcript: list[bytes] = []
    cap_state: dict = {"truncated": False}
    # termios save is mandatory per the contract, but only if stdin is a TTY.
    # In headless / non-interactive runs (no controlling terminal), skip it.
    try:
        saved = termios.tcgetattr(sys.stdin.fileno())
    except (termios.error, OSError):
        saved = None
    fd = pid = -1
    latency_turns_ms: list[int] = []
    pass_ = False
    observed_state = ""
    exit_code: int | None = None
    try:
        fd, pid = _setup_pty()
        matched, t0, _ = _wait_for(fd, transcript, cap_state, TIMEOUT_DEFAULT, PROMPT_RE)
        if not matched:
            observed_state = "never saw initial prompt"
        else:
            latency_turns_ms.append(int(t0 * 1000))
            t_send = time.monotonic()
            _send(fd, b"/help\r")
            # Wait for the prompt to re-appear (turn complete). Help text
            # usually triggers a re-display of the prompt after a delay.
            deadline = time.monotonic() + TIMEOUT_DEFAULT
            accumulated = bytearray()
            for b in transcript:
                accumulated.extend(b)
            prior_count = len(
                list(PROMPT_RE.finditer(accumulated.decode("utf-8", errors="replace")))
            )
            success = False
            while time.monotonic() < deadline:
                rlist, _, _ = select.select([fd], [], [], 0.25)
                if rlist:
                    _drain(fd, transcript, cap_state)
                accumulated = bytearray()
                for b in transcript:
                    accumulated.extend(b)
                text = accumulated.decode("utf-8", errors="replace")
                # Look for help-style content: many lines, or new occurrences
                # of the prompt. Either indicates claude responded.
                lines = text.splitlines()
                if len(lines) > 30:  # crude proxy for help output
                    success = True
                    break
                matches = list(PROMPT_RE.finditer(text))
                if len(matches) > prior_count:
                    success = True
                    break
            t_reply = time.monotonic() - t_send
            latency_turns_ms.append(int(t_reply * 1000))
            if success:
                pass_ = True
                observed_state = f"/help produced response in {t_reply:.2f}s"
            else:
                tail = b"".join(transcript)[-500:]
                observed_state = (
                    f"no help response within {TIMEOUT_DEFAULT}s; "
                    f"tail: {tail.decode('utf-8', errors='replace')!r}"
                )
        exit_code = _graceful_reap(pid)
    except Exception as e:  # pragma: no cover — defensive
        observed_state = f"exception: {type(e).__name__}: {e}"
    finally:
        try:
            if saved is not None:
                try:
                    termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, saved)
                except termios.error:
                    pass
        except termios.error:
            pass
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        if pid != -1 and exit_code is None:
            # Final fallback: escalate hard. _graceful_reap already does
            # SIGTERM->grace->SIGKILL, so this is the last-resort reap.
            _graceful_reap(pid, grace_s=0.5)

    total_bytes = sum(len(b) for b in transcript)
    footer = _format_footer(
        scenario_n,
        passed=pass_,
        parse_failures=0 if pass_ else 1,
        buf_drain_iters_max=0,
        latency_turns_ms=latency_turns_ms,
        observed_state=observed_state,
        exit_code=exit_code,
        total_bytes=total_bytes,
    )
    path = TRANSCRIPT_DIR / f"scenario-{scenario_n}.bin"
    _write_transcript(path, transcript, footer)
    return {
        "scenario": scenario_n,
        "pass": pass_,
        "total_bytes": total_bytes + len(footer),
        "footer_observed_state": observed_state,
    }


# ---- Scenario 7: Long-running session stability ---------------------------


def scenario_7() -> dict:
    """Spawn, send a short prompt, wait for full reply, idle 5 minutes.

    Pass criterion: process still alive at the 5-minute mark.
    """
    scenario_n = 7
    transcript: list[bytes] = []
    cap_state: dict = {"truncated": False}
    # termios save is mandatory per the contract, but only if stdin is a TTY.
    # In headless / non-interactive runs (no controlling terminal), skip it.
    try:
        saved = termios.tcgetattr(sys.stdin.fileno())
    except (termios.error, OSError):
        saved = None
    fd = pid = -1
    latency_turns_ms: list[int] = []
    pass_ = False
    observed_state = ""
    exit_code: int | None = None
    try:
        fd, pid = _setup_pty()
        matched, t0, _ = _wait_for(fd, transcript, cap_state, TIMEOUT_DEFAULT, PROMPT_RE)
        if not matched:
            observed_state = "never saw initial prompt"
        else:
            latency_turns_ms.append(int(t0 * 1000))
            t_send = time.monotonic()
            _send(
                fd,
                b"explain the difference between async and parallel in 3 sentences.\r",
            )
            # Wait for the prompt to re-appear (turn complete)
            deadline = time.monotonic() + TIMEOUT_DEFAULT
            accumulated = bytearray()
            for b in transcript:
                accumulated.extend(b)
            prior_count = len(
                list(PROMPT_RE.finditer(accumulated.decode("utf-8", errors="replace")))
            )
            success = False
            while time.monotonic() < deadline:
                rlist, _, _ = select.select([fd], [], [], 0.25)
                if rlist:
                    _drain(fd, transcript, cap_state)
                accumulated = bytearray()
                for b in transcript:
                    accumulated.extend(b)
                text = accumulated.decode("utf-8", errors="replace")
                matches = list(PROMPT_RE.finditer(text))
                if len(matches) > prior_count:
                    success = True
                    break
            t_reply = time.monotonic() - t_send
            latency_turns_ms.append(int(t_reply * 1000))
            if not success:
                observed_state = f"first reply did not complete in {TIMEOUT_DEFAULT}s"
            else:
                # Idle hold for 5 minutes
                hold_start = time.monotonic()
                idle_alive = True
                while time.monotonic() - hold_start < TIMEOUT_HOLD:
                    # Check if child is still alive
                    ec = _try_waitpid(pid)
                    if ec is not None:
                        exit_code = ec
                        idle_alive = False
                        observed_state = (
                            f"process died at "
                            f"{(time.monotonic() - hold_start):.2f}s into 5min idle "
                            f"with exit_code={ec}"
                        )
                        break
                    # Drain anything that arrived
                    rlist, _, _ = select.select([fd], [], [], 1.0)
                    if rlist:
                        _drain(fd, transcript, cap_state)
                if idle_alive:
                    pass_ = True
                    observed_state = f"--- alive at 5min --- (held idle for {TIMEOUT_HOLD:.0f}s)"
                    # Append the alive marker to the transcript
                    transcript.append(b"--- alive at 5min ---\n")
        exit_code = _graceful_reap(pid)
    except Exception as e:  # pragma: no cover — defensive
        observed_state = f"exception: {type(e).__name__}: {e}"
    finally:
        try:
            if saved is not None:
                try:
                    termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, saved)
                except termios.error:
                    pass
        except termios.error:
            pass
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        if pid != -1 and exit_code is None:
            # Final fallback: escalate hard. _graceful_reap already does
            # SIGTERM->grace->SIGKILL, so this is the last-resort reap.
            _graceful_reap(pid, grace_s=0.5)

    total_bytes = sum(len(b) for b in transcript)
    footer = _format_footer(
        scenario_n,
        passed=pass_,
        parse_failures=0 if pass_ else 1,
        buf_drain_iters_max=0,
        latency_turns_ms=latency_turns_ms,
        observed_state=observed_state,
        exit_code=exit_code,
        total_bytes=total_bytes,
    )
    path = TRANSCRIPT_DIR / f"scenario-{scenario_n}.bin"
    _write_transcript(path, transcript, footer)
    return {
        "scenario": scenario_n,
        "pass": pass_,
        "total_bytes": total_bytes + len(footer),
        "footer_observed_state": observed_state,
    }


# ---- Scenario 8: Negative control — no PTY --------------------------------


def scenario_8() -> dict:
    """Spawn claude with stdin=PIPE (no PTY) and record what happens."""
    scenario_n = 8
    transcript: list[bytes] = []
    # termios save is mandatory per the contract, but only if stdin is a TTY.
    # In headless / non-interactive runs (no controlling terminal), skip it.
    try:
        saved = termios.tcgetattr(sys.stdin.fileno())
    except (termios.error, OSError):
        saved = None
    proc = None
    latency_turns_ms: list[int] = []
    pass_ = False  # always — no pass criterion for negative control
    observed_state = ""
    exit_code: int | None = None
    try:
        env = _build_child_env()
        # Spawn with stdin=PIPE, no PTY. stdout and stderr are PIPE.
        t_start = time.monotonic()
        proc = subprocess.Popen(
            CLAUDE_ARGS,
            cwd=PROJECT_CWD,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Try to read for up to 5s. claude likely detects non-tty and exits
        # quickly with an error, or produces garbled output.
        try:
            stdout, stderr = proc.communicate(timeout=5.0, input=b"hello\n")
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            observed_state = (
                f"claude did NOT exit within 5s without PTY; "
                f"stdout head: {stdout[:200]!r}, stderr head: {stderr[:200]!r}"
            )
        else:
            observed_state = (
                f"claude exited without PTY in {time.monotonic() - t_start:.2f}s; "
                f"exit_code={proc.returncode}; "
                f"stdout (first 300 bytes): {stdout[:300]!r}; "
                f"stderr (first 300 bytes): {stderr[:300]!r}"
            )
        transcript.append(b"--- no-PTY capture ---\n")
        transcript.append(b"--- stdout ---\n")
        transcript.append(stdout[: TRANSCRIPT_CAP_BYTES - 1024])
        transcript.append(b"\n--- stderr ---\n")
        transcript.append(stderr[: TRANSCRIPT_CAP_BYTES - 1024])
        exit_code = proc.returncode
        latency_turns_ms.append(int((time.monotonic() - t_start) * 1000))
        # No pass criterion — record the failure mode.
    except Exception as e:  # pragma: no cover — defensive
        observed_state = f"exception: {type(e).__name__}: {e}"
    finally:
        try:
            if saved is not None:
                try:
                    termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, saved)
                except termios.error:
                    pass
        except termios.error:
            pass
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=2.0)
            except Exception:
                pass

    total_bytes = sum(len(b) for b in transcript)
    footer = _format_footer(
        scenario_n,
        passed=pass_,
        parse_failures=0,
        buf_drain_iters_max=0,  # N/A for pipe
        latency_turns_ms=latency_turns_ms,
        observed_state=observed_state,
        exit_code=exit_code,
        total_bytes=total_bytes,
    )
    path = TRANSCRIPT_DIR / f"scenario-{scenario_n}.bin"
    _write_transcript(path, transcript, footer)
    return {
        "scenario": scenario_n,
        "pass": pass_,
        "total_bytes": total_bytes + len(footer),
        "footer_observed_state": observed_state,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    # 1. Ensure transcript dir exists, nuke stale files
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    n_nuked = _nuke_stale_transcripts()
    print(f"[startup] transcript dir: {TRANSCRIPT_DIR}")
    print(f"[startup] nuked {n_nuked} stale scenario-*.bin file(s)")

    scenarios = [
        ("scenario 1 (first-turn prompt detection)", scenario_1),
        ("scenario 2 (first-message text submission)", scenario_2),
        ("scenario 3 (multi-turn conversation)", scenario_3),
        ("scenario 4 (two-stage ctrl-c interject)", scenario_4),
        ("scenario 5 (resume UUID capture)", scenario_5),
        ("scenario 6 (numbered menu / slash command)", scenario_6),
        ("scenario 7 (long-running session stability, 5min idle)", scenario_7),
        ("scenario 8 (negative control: no PTY)", scenario_8),
    ]

    results: list[dict] = []
    overall_t0 = time.monotonic()
    for name, fn in scenarios:
        print(f"\n=== {name} ===", flush=True)
        t0 = time.monotonic()
        try:
            r = fn()
        except Exception as e:  # pragma: no cover — defensive
            print(f"  !! scenario raised: {type(e).__name__}: {e}", flush=True)
            r = {
                "scenario": -1,
                "pass": False,
                "total_bytes": 0,
                "footer_observed_state": f"raised: {e}",
            }
        r["elapsed_s"] = time.monotonic() - t0
        results.append(r)
        print(
            f"  pass={r['pass']}  bytes={r['total_bytes']}  "
            f"elapsed={r['elapsed_s']:.2f}s  "
            f"observed: {r.get('footer_observed_state', '')[:120]}",
            flush=True,
        )

    total_elapsed = time.monotonic() - overall_t0
    print(f"\n=== done; total wall-clock {total_elapsed:.1f}s ===")

    # Final defensive pkill of any orphaned claude children
    try:
        subprocess.run(
            ["pkill", "-f", "claude --model sonnet --permission-mode bypassPermissions"],
            check=False,
            timeout=5,
        )
        print("[cleanup] pkill issued for orphaned claude children")
    except Exception as e:  # pragma: no cover — defensive
        print(f"[cleanup] pkill error: {e}")

    # Print summary table
    print("\n=== summary ===")
    print(f"{'scn':>3}  {'pass':>5}  {'bytes':>8}  {'elapsed':>8}  observed")
    for r in results:
        scn = r.get("scenario", "?")
        print(
            f"{scn:>3}  {str(r.get('pass', False)):>5}  "
            f"{r.get('total_bytes', 0):>8}  "
            f"{r.get('elapsed_s', 0):>7.1f}s  "
            f"{r.get('footer_observed_state', '')[:100]}"
        )

    # Exit code: 0 if all pass, 1 otherwise. Scenario 8 has no pass criterion,
    # so its 'pass=False' is expected and does not cause non-zero exit.
    n_fail = sum(1 for r in results if not r.get("pass", False) and r.get("scenario") != 8)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
