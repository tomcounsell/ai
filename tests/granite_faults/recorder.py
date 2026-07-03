"""Golden-recorder for the granite failure-simulation harness (plan Task 4).

Runs ONE real ollama-backed ``claude`` TUI session and captures three
artifacts into ``tests/granite_faults/fixtures/``:

  * ``recorded_session.frames`` — the raw PTY frame stream the TUI paints
    (ANSI-light, matching the hand-authored seed fixtures), the input the
    Substrate A replay-and-mutate injectors consume.
  * ``recorded_transcript.jsonl`` — the Claude Code JSONL transcript Claude
    Code writes for the session (``~/.claude/projects/{slug}/{id}.jsonl``).
  * ``recorded_meta.json`` — model tag, session id, elapsed, whether the idle
    bar was ever observed, and any transcript hook-event rows.

TEST-ONLY. This is test-support tooling, not production. It touches nothing
under ``agent/granite_container/`` — it only reuses the read-only
``IDLE_BAR`` / ``_strip_ansi`` seams.

The env is built by ``ollama_env.build_ollama_child_env`` (the three ollama
vars + the ``CLAUDE_CODE_OAUTH_TOKEN`` pop) and ``assert_no_oauth_leak`` runs
on the assembled child env immediately before the spawn — the same blocker-fix
contract Substrate B holds.

Usage::

    python -m tests.granite_faults.recorder                 # auto-pick model
    python -m tests.granite_faults.recorder --model qwen... # explicit tag
    python -m tests.granite_faults.recorder --prompt "..."  # custom prompt

The session runs in a scratch cwd so a large project ``CLAUDE.md`` cannot blow
the ollama prefill budget (Task 0 finding).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field

import pexpect

from agent.granite_container.pty_driver import IDLE_BAR, _strip_ansi
from tests.granite_faults.ollama_env import (
    assert_no_oauth_leak,
    build_ollama_child_env,
    pick_ollama_model,
)

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"

DEFAULT_PROMPT = "Reply with exactly the word PONG and nothing else."
# Generous ceilings: a warm tool-capable ollama model answers a scratch-cwd
# claude --print in ~70s (Task 0); the interactive TUI adds startup paint.
SPAWN_STARTUP_S = 45.0
IDLE_WAIT_S = 240.0
SILENCE_S = 3.0


@dataclass
class RecordingMeta:
    """Metadata captured alongside the frame + transcript fixtures."""

    model: str
    session_id: str
    prompt: str
    elapsed_s: float
    saw_idle_bar: bool
    reply_landed: bool
    frame_bytes: int
    transcript_found: bool
    transcript_rows: int
    hook_event_rows: int
    hook_events: list[str] = field(default_factory=list)


def _drain(child: pexpect.spawn, capture: list[str], *, max_s: float, silence_s: float) -> None:
    """Read from the child until ``silence_s`` of quiet or ``max_s`` elapses."""
    deadline = time.monotonic() + max_s
    last_data = time.monotonic()
    while time.monotonic() < deadline:
        try:
            chunk = child.read_nonblocking(size=4096, timeout=0.5)
        except pexpect.TIMEOUT:
            if time.monotonic() - last_data >= silence_s:
                return
            continue
        except pexpect.EOF:
            return
        if chunk:
            capture.append(chunk)
            last_data = time.monotonic()


# The claude TUI paints an assistant turn under the ``⏺`` bullet glyph. It is
# the stable, model-agnostic "the reply landed" signal in the frame stream —
# the interactive TUI does not reliably persist a per-``--session-id`` JSONL we
# can locate mid-run (and SIGINT teardown drops it), so the frame glyph, not
# the transcript, is the completion signal the recorder keys on.
_REPLY_GLYPH = "⏺"


def _drain_until_reply(
    child: pexpect.spawn,
    capture: list[str],
    *,
    max_s: float,
    settle_s: float,
) -> bool:
    """Capture the model turn until the assistant reply glyph paints, then settle.

    ollama prefill on a scratch-cwd claude session can stay silent for tens of
    seconds before the first token (Task 0), so a naive silence gate bails
    early. Poll the accumulated frames for the ``⏺`` assistant-turn glyph; once
    seen, keep draining for ``settle_s`` so the settled screen repaints into the
    capture. Returns True if a reply landed, False on timeout.
    """
    deadline = time.monotonic() + max_s
    reply_at: float | None = None
    while time.monotonic() < deadline:
        try:
            chunk = child.read_nonblocking(size=4096, timeout=0.5)
            if chunk:
                capture.append(chunk)
        except pexpect.TIMEOUT:
            pass
        except pexpect.EOF:
            return reply_at is not None
        if reply_at is None:
            if _REPLY_GLYPH in "".join(capture):
                reply_at = time.monotonic()
        elif time.monotonic() - reply_at >= settle_s:
            return True
    return reply_at is not None


def record_session(
    *,
    model: str | None = None,
    prompt: str = DEFAULT_PROMPT,
    write_fixtures: bool = True,
) -> RecordingMeta:
    """Run one real ollama-backed ``claude`` session and capture its artifacts.

    Returns the :class:`RecordingMeta`. Raises ``RuntimeError`` if no usable
    ollama model is served.
    """
    pick = model or pick_ollama_model()
    if not pick:
        raise RuntimeError(
            "No tool-capable ollama model is served — pull one (e.g. a qwen "
            "coding tag) before recording."
        )

    # Blocker fix: assemble the child env with the OAuth token popped, then
    # assert no leak BEFORE spawn — identical contract to Substrate B.
    env = build_ollama_child_env()
    assert_no_oauth_leak(env)

    session_id = str(uuid.uuid4())
    capture: list[str] = []
    started = time.monotonic()
    reply_landed = False

    with tempfile.TemporaryDirectory(prefix="granite-recorder-") as scratch:
        child = pexpect.spawn(
            "claude",
            [
                "--model",
                pick,
                "--permission-mode",
                "bypassPermissions",
                "--session-id",
                session_id,
            ],
            env=env,
            cwd=scratch,
            echo=False,
            encoding="utf-8",
            timeout=int(IDLE_WAIT_S),
        )
        try:
            # 1) Capture startup frames (trust/permission/prompt paint).
            _drain(child, capture, max_s=SPAWN_STARTUP_S, silence_s=SILENCE_S)
            # 1b) A fresh scratch cwd triggers the "trust this folder" dialog
            #     (client-rendered by the claude binary, identical under
            #     ollama). Confirm the default "Yes" with Enter, then wait for
            #     the main TUI, so the prompt lands in the input box rather than
            #     being swallowed by the dialog.
            if "trust this folder" in _strip_ansi("".join(capture)).lower():
                child.send("\r")
                _drain(child, capture, max_s=SPAWN_STARTUP_S, silence_s=SILENCE_S)
            # 2) Submit the prompt (body, brief pause, then CR — mirrors the
            #    driver's paste-burst-safe submit).
            child.send(prompt)
            time.sleep(0.5)
            child.send("\r")
            # 3) Capture the model turn until the reply glyph paints
            #    (tolerates long ollama prefill), then let the screen settle.
            reply_landed = _drain_until_reply(child, capture, max_s=IDLE_WAIT_S, settle_s=SILENCE_S)
        finally:
            try:
                if child.isalive():
                    child.sendcontrol("c")
                    time.sleep(0.3)
                    child.sendcontrol("c")
                    time.sleep(0.3)
                    child.close(force=True)
            except Exception:
                pass

    elapsed = time.monotonic() - started
    raw = "".join(capture)
    frames = _strip_ansi(raw)
    saw_idle_bar = IDLE_BAR.search(frames) is not None

    # Hook events: the interactive TUI paints "running <name> hooks" (e.g.
    # "running stop hooks… 0/3") while it fires session hooks, so the frame
    # stream is the reliable hook-event witness. The JSONL transcript is a
    # bonus when Claude Code happens to persist a findable per-session-id file.
    hook_events = sorted(set(re.findall(r"running ([a-z]+) hooks", frames, flags=re.IGNORECASE)))

    transcript_rows = 0
    transcript_text = ""
    # The scratch cwd is torn down by now, so the slug is unknown; locate the
    # transcript by its globally-unique session UUID instead.
    transcript_path = _find_transcript(session_id)
    if transcript_path is not None and transcript_path.exists():
        transcript_text = transcript_path.read_text()
        transcript_rows = sum(1 for line in transcript_text.splitlines() if line.strip())

    meta = RecordingMeta(
        model=pick,
        session_id=session_id,
        prompt=prompt,
        elapsed_s=round(elapsed, 1),
        saw_idle_bar=saw_idle_bar,
        reply_landed=reply_landed,
        frame_bytes=len(frames),
        transcript_found=bool(transcript_text),
        transcript_rows=transcript_rows,
        hook_event_rows=len(hook_events),
        hook_events=sorted(set(hook_events)),
    )

    if write_fixtures:
        FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
        (FIXTURES_DIR / "recorded_session.frames").write_text(frames)
        (FIXTURES_DIR / "recorded_meta.json").write_text(json.dumps(asdict(meta), indent=2) + "\n")
        # Only persist a transcript fixture when Claude Code actually wrote one
        # (the interactive TUI + SIGINT teardown often leaves none — the frame
        # stream is the golden artifact). Never commit an empty placeholder.
        transcript_fixture = FIXTURES_DIR / "recorded_transcript.jsonl"
        if transcript_text:
            transcript_fixture.write_text(transcript_text)
        elif transcript_fixture.exists():
            transcript_fixture.unlink()

    return meta


def _find_transcript(session_id: str) -> pathlib.Path | None:
    """Locate the JSONL transcript Claude Code wrote for ``session_id``.

    The scratch cwd is torn down before this runs, so the slug is unknown;
    glob every project dir for ``{session_id}.jsonl`` (Claude Code names the
    file by the session UUID, which is globally unique).
    """
    projects = pathlib.Path.home() / ".claude" / "projects"
    if not projects.exists():
        return None
    matches = list(projects.glob(f"*/{session_id}.jsonl"))
    return matches[0] if matches else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Granite golden-recorder (ollama-backed).")
    parser.add_argument("--model", default=None, help="ollama model tag (auto-picked if omitted)")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="prompt to submit to the TUI")
    parser.add_argument(
        "--no-write", action="store_true", help="run the session but do not write fixtures"
    )
    args = parser.parse_args(argv)

    try:
        meta = record_session(
            model=args.model, prompt=args.prompt, write_fixtures=not args.no_write
        )
    except RuntimeError as exc:
        print(f"recorder: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(asdict(meta), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
