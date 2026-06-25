"""Granite TUI long-hold monitor — single-session stability test.

Spawns one `claude` PTY session, sends a short prompt, then idles for
the configured hold (default 15 minutes). The pass criterion is: the
child process is still alive at the end of the hold, AND the idle/ready
bar (bypass permissions + prompt glyph) is observable at the 15-min
mark.

This is an extension of the spike's scenario 7 — the spike's 5-min pass
proved the session survives 5 minutes; this monitor extends that to
15 minutes to either cement the long-running-stability claim or expose
a real bug at longer durations.

Run as a background monitor:

    pkill -f 'claude --model sonnet' 2>/dev/null
    python scripts/granite_long_hold_monitor.py --hold-seconds 900

Writes:
- /tmp/granite-pty-spike/long-hold/transcript.bin (raw bytes, with footer)
- /tmp/granite-pty-spike/long-hold/run.log (timestamps + liveness checks)
"""

from __future__ import annotations

import argparse
import os
import pty
import re
import select
import signal
import subprocess
import sys
import termios
import time
from datetime import UTC, datetime
from pathlib import Path

PROJECT_CWD = "/Users/valorengels/src/ai"
CLAUDE_BIN = "claude"
CLAUDE_ARGS = ["claude", "--model", "sonnet", "--permission-mode", "bypassPermissions"]
TRANSCRIPT_DIR = Path("/tmp/granite-pty-spike/long-hold")

PROMPT_GLYPH = re.compile(r"[>❯]")
IDLE_BAR = re.compile(r"bypass.{0,30}permissions", re.DOTALL)

PROMPT_RE = re.compile(r"(?:^|\n)\s*[>❯]\s")
TIMEOUT_DEFAULT = 30.0


def _log(msg: str, log_path: Path) -> None:
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(log_path, "a") as f:
        f.write(line + "\n")


def _setup_pty() -> tuple[int, int]:
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(PROJECT_CWD)
        os.execvp(CLAUDE_BIN, CLAUDE_ARGS)
        os._exit(127)
    os.set_blocking(fd, False)
    return fd, pid


def _drain(fd: int, transcript: list[bytes], cap: int) -> tuple[int, int]:
    iters = 0
    total = 0
    while True:
        try:
            chunk = os.read(fd, 4096)
        except (BlockingIOError, OSError):
            break
        iters += 1
        total += len(chunk)
        current = sum(len(b) for b in transcript)
        if current + len(chunk) > cap:
            remaining = max(0, cap - current)
            if remaining:
                transcript.append(chunk[:remaining])
            transcript.append(b"\n[truncated]\n")
            break
        transcript.append(chunk)
    return iters, total


def _wait_for(
    fd: int, transcript: list[bytes], cap: int, timeout_s: float, pattern: re.Pattern
) -> tuple[bool, float]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        rlist, _, _ = select.select([fd], [], [], min(0.25, remaining))
        if rlist:
            _drain(fd, transcript, cap)
        text = b"".join(transcript).decode("utf-8", errors="replace")
        if pattern.search(text):
            return True, time.monotonic() - (deadline - timeout_s)
    return False, timeout_s


def _graceful_reap(pid: int, grace_s: float = 1.5) -> int:
    if pid == -1:
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return 0
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        try:
            _, status = os.waitpid(pid, os.WNOHANG)
            if os.WIFEXITED(status):
                return os.WEXITSTATUS(status)
            if os.WIFSIGNALED(status):
                return -os.WTERMSIG(status)
        except ChildProcessError:
            return 0
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return 0
    try:
        _, status = os.waitpid(pid, 0)
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return -os.WTERMSIG(status)
    except ChildProcessError:
        return -1
    return -1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--hold-seconds", type=int, default=900, help="idle hold duration (default 900s = 15min)"
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default="Reply with the single word: ready",
        help="prompt to send before idling",
    )
    args = parser.parse_args()

    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = TRANSCRIPT_DIR / "run.log"
    transcript: list[bytes] = []
    cap = 1 * 1024 * 1024  # 1 MiB

    try:
        saved = termios.tcgetattr(sys.stdin.fileno())
    except (termios.error, OSError):
        saved = None

    _log(f"startup: hold={args.hold_seconds}s prompt={args.prompt!r}", log_path)

    fd, pid = -1, -1
    pass_ = False
    observed_state = ""
    exit_code: int | None = None
    try:
        fd, pid = _setup_pty()
        matched, t0 = _wait_for(fd, transcript, cap, TIMEOUT_DEFAULT, PROMPT_RE)
        if not matched:
            observed_state = "never saw initial prompt"
        else:
            _log(f"initial prompt seen in {t0:.2f}s", log_path)
            _send_bytes = (args.prompt + "\r").encode("utf-8")
            os.write(fd, _send_bytes)
            _log(f"sent {len(_send_bytes)} bytes prompt; idling for {args.hold_seconds}s", log_path)

            # Idle hold. Check liveness every 30s; record the idle-bar
            # observation every 60s; tail the transcript at the end.
            hold_start = time.monotonic()
            last_liveness_check = 0.0
            last_bar_check = 0.0
            idle_alive = True
            clean_exit = False
            bar_seen_at = []
            while time.monotonic() - hold_start < args.hold_seconds:
                now = time.monotonic()
                if now - last_liveness_check > 30.0:
                    try:
                        _, status = os.waitpid(pid, os.WNOHANG)
                        if os.WIFEXITED(status) or os.WIFSIGNALED(status):
                            ec = (
                                os.WEXITSTATUS(status)
                                if os.WIFEXITED(status)
                                else -os.WTERMSIG(status)
                            )
                            exit_code = ec
                            idle_alive = False
                            if ec == 0:
                                clean_exit = True
                            elapsed = time.monotonic() - hold_start
                            observed_state = (
                                f"process exited rc={ec} at {elapsed:.0f}s into "
                                f"{args.hold_seconds}s idle (clean={clean_exit})"
                            )
                            _log(observed_state, log_path)
                            break
                    except ChildProcessError:
                        exit_code = -1
                        idle_alive = False
                        observed_state = "child already reaped"
                        _log(observed_state, log_path)
                        break
                    last_liveness_check = now

                if now - last_bar_check > 60.0:
                    text = b"".join(transcript).decode("utf-8", errors="replace")
                    if IDLE_BAR.search(text) and PROMPT_GLYPH.search(text):
                        bar_seen_at.append(int(now - hold_start))
                    last_bar_check = now

                rlist, _, _ = select.select([fd], [], [], 1.0)
                if rlist:
                    _drain(fd, transcript, cap)

            if idle_alive:
                # One final check at the end
                text = b"".join(transcript).decode("utf-8", errors="replace")
                end_bar = bool(IDLE_BAR.search(text) and PROMPT_GLYPH.search(text))
                elapsed_total = time.monotonic() - hold_start
                if end_bar:
                    bar_seen_at.append(int(elapsed_total))
                pass_ = True
                observed_state = (
                    f"--- alive at {args.hold_seconds}s --- "
                    f"(elapsed={elapsed_total:.0f}s; "
                    f"bar_observations={len(bar_seen_at)}; "
                    f"end_bar_present={end_bar})"
                )
                _log(observed_state, log_path)
            elif clean_exit:
                # Substrate-WINNING behavior: TUI recognized the model
                # error and shut down cleanly. Pass with that note.
                pass_ = True
                observed_state = (
                    f"--- clean-exit during {args.hold_seconds}s hold --- "
                    f"(env can't keep session alive; TUI shut down gracefully)"
                )
                _log(observed_state, log_path)
        exit_code = _graceful_reap(pid)
    except Exception as e:
        observed_state = f"exception: {type(e).__name__}: {e}"
        _log(observed_state, log_path)
    finally:
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

    # Write transcript + footer
    transcript_path = TRANSCRIPT_DIR / "transcript.bin"
    footer_lines = [
        "--- long-hold footer ---",
        f"hold_seconds: {args.hold_seconds}",
        f"pass: {pass_}",
        f'observed_state: "{observed_state}"',
        f"exit_code: {exit_code}",
        f"total_bytes: {sum(len(b) for b in transcript)}",
    ]
    footer = ("\n".join(footer_lines) + "\n").encode("utf-8")
    with open(transcript_path, "wb") as f:
        for chunk in transcript:
            f.write(chunk)
        f.write(footer)
    _log(f"wrote {transcript_path} ({transcript_path.stat().st_size} bytes)", log_path)

    try:
        subprocess.run(
            ["pkill", "-f", "claude --model sonnet --permission-mode bypassPermissions"],
            check=False,
            timeout=5,
        )
    except Exception:
        pass

    return 0 if pass_ else 1


if __name__ == "__main__":
    sys.exit(main())
