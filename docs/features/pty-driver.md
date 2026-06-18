# Substrate Driver (PTY Driver)

**Status:** Production. The substrate driver for the granite
interactive-TUI session runner. Module: `agent/granite_container/pty_driver.py`.

## What it is

A thin pexpect-backed wrapper around the interactive `claude` TUI. The
driver does NOT use `claude -p`; it spawns an interactive session
attached to a pseudo-terminal and exposes a small surface
(`spawn`, `write`, `read_until_idle`, `send_ctrl_c`, `close`) the
container layer can use without reaching into pexpect directly.

This is the **substrate for the claude builder** (`PtyClaudeBuilder` in
`agent/granite_container/builder.py`). As of plan #1725, `PTYDriver` is not
the only builder substrate in the granite system — `PiSubprocessBuilder` is a
subprocess-based alternative that bypasses PTY entirely (no pexpect, no idle
heuristic, no startup parser). Each `[/dev:pi]` turn spawns a one-shot `pi`
subprocess; `PTYDriver` is not involved. See
[Pluggable Builder Harness](pluggable-builder-harness.md) for the seam design.

## C1-C5 substrate facts (the load-bearing invariants)

The driver models the Claude Code TUI's submit/idle/interject surface
(per the v7 spike report's findings at
`docs/research/spikes/granite-tui-pty-spike.md`):

### C1 — submit key is `\r` (CR, 0x0D), not `\n` (LF, 0x0A)

The TUI is a readline-style input field. `\r` is the submit key; `\n`
is a literal newline within the field. Sending `hello\n` leaves the
cursor in the input box; `hello\r` commits the message.

The submit CR must additionally arrive in its **own input burst**: the
TUI's paste-burst heuristic (observed on claude v2.1.173) treats a CR
that lands in the same burst as the message body as a literal newline
inside a paste, so `send("hello\r")` parks the text in the input box
unsubmitted. The driver's `write()` therefore sends the body, sleeps
`SUBMIT_KEY_DELAY_S` (0.5s), then sends `\r` as a separate send.
Internal newlines are preserved (the TUI input box supports multi-line
input via a literal `\n`; only the separately-sent final CR submits).

### C2 — first-ctrl-c interjection text

In TUI v2.1.160, the first ctrl-c surfaces the hint "Press Ctrl-C
again to exit". Older TUI versions used "Interrupted · What should
Claude do instead?" (the historical granite-agent-loop PoC docs
referenced the older text; those docs were deleted in PR #1664).

The driver's `INTERRUPTED_RE` regex matches both forms, so the driver
is robust to TUI version drift:

```python
INTERRUPTED_RE = re.compile(
    r"(Interrupted\s*[·•\.]\s*What should Claude do instead\?|Press Ctrl-C again to exit)",
    re.IGNORECASE,
)
```

### C3 — resume-UUID is environment-gated

The on-exit hint (`claude --resume <uuid>`) is only emitted on a
successful model response. In a model-unreachable env, no session is
opened, no hint is printed.

The driver exposes `last_resume_uuid()` as a passive scrape from the
child buffer; it does not actively drive an exit. Resume acceptance
tests run in a model-reachable env; in a non-reachable env they are
skipped.

**As of issue #1648**, the driver also accepts a `session_id: str | None`
constructor argument. When set, `spawn()` appends `--session-id <uuid>` to
the `claude` args so Claude Code names its transcript
`~/.claude/projects/{cwd-slug}/{session_id}.jsonl`. This makes the transcript
path deterministically known at spawn time — no post-hoc `last_resume_uuid()`
scraping required. The `pid` property (`self._child.pid`, or `None` before
spawn / after exit) exposes the OS PID for dashboard identity display.

### C4 — `/help` overlay

The `/help` slash command renders as a non-dismissing overlay. The
bottom-bar text changes from "bypass permissions" to "esc to cancel"
while the overlay is active. Idle detection must recognize the
"esc to cancel" state; the loop holds while the user dismisses the
overlay (typically with Esc).

### C5 — idle signal is byte-quiescence + bottom-bar text + glyph + content-floor

The TUI's idle state is the bottom bar containing "bypass permissions"
plus the prompt glyph (`>` or `❯`), gated on **byte-quiescence**: idle
is only declared after `QUIESCENCE_S` (2.0s) with zero new PTY bytes.
An active model turn repaints the spinner animation at ≥1 Hz, so it
can never be silent that long; a settled TUI paints nothing. Silence
is the only reliable "running right now" negative — the spinner
animates via cursor-positioned cell fragments (`✻i…`, `✽hg`) that no
regex over the capture can reliably match.

The heuristic is **level-triggered**: it is evaluated against a
persistent per-turn screen capture (`_turn_text`, reset on every
`write()`), on every poll tick — including ticks with no new bytes. A
settled TUI paints nothing, so an edge-triggered check (fresh chunks
only) can never observe idle on a quiescent PTY.

The `min_content_bytes` floor (default 400, measured on the
ANSI-stripped capture) plus the `SPINNER_EVIDENCE_RE` gate prevent
false-positives from the command-echo frame: floor reads require proof
that a model turn actually ran (a spinner frame or the "esc to
interrupt" hint painted at some point this turn).

The driver's `read_until_idle` returns an `IdleResult` with
`saw_idle`, `buffer` (ANSI-stripped bytes read this call),
`turn_buffer` (ANSI-stripped per-turn capture), `idle_marker`, and
`elapsed_ms`.

Constraint: `QUIESCENCE_S` must stay strictly below the container's
`STARTUP_CYCLE_TIMEOUT_S` (3.0s) or startup polls can never observe
idle; the floor is documented at both constants.

## Why pexpect over stdlib (subprocess + pty)

The v7 spike report compared pexpect with the stdlib `pty` module and
found pexpect's API to be a cleaner surface for the loop's
`read_nonblocking` / `before` / `after` semantics. The stdlib path is
competitive (the spike validated both); the driver ships pexpect because
the v7 spike's reference implementation is pexpect-backed, and reusing
the spike's `wait_for_idle` and `_send` helpers is cheaper than
re-deriving the same logic on top of `pty`.

If a future refactor needs zero-dep, the stdlib transcripts are
preserved as evidence; switching back is a one-day effort.

## macOS `preexec_fn` note

pexpect's underlying `pty.fork()` already calls `setsid()`; a second
`setsid()` from `preexec_fn` is a no-op that EPERMs out. The driver
passes a no-op `preexec_fn` so the child is still in its own session
(the desired outcome) without the redundant syscall. Mirrors the
spike's finding at `scripts/granite_tui_pty_spike_pexpect.py:181-207`.

## Env blanking for Max OAuth

The driver's `_build_env()` blanks `ANTHROPIC_API_KEY` (rather than
removing it) on the subprocess env. This is the documented way to
force the Max subscription OAuth path even when a key happens to be
present in the inherited environment. Mirrors `_build_env` in
`agent/claude_session.py:90-101`.

## Model-by-role policy (Claude subscription substrate)

The PM/Dev TUIs are real `claude` Code sessions on the **Claude
subscription** — spawned exactly like the
`claude --permission-mode bypassPermissions` shortcut (`_build_env`
blanks `ANTHROPIC_API_KEY` to force the OAuth path), with the model
chosen at spawn time. The driver resolves the alias by role via
`_default_substrate_model(role)`:

- PM → `settings.granite.pm_model` (default `opus`, env `GRANITE__PM_MODEL`)
- Dev → `settings.granite.dev_model` (default `sonnet`, env `GRANITE__DEV_MODEL`)

Aliases are **unpinned** (`opus`, `sonnet`, `haiku`) so the substrate
tracks the latest version. ollama belongs to the granite *classifier*
(`granite_classifier.py`, model `granite4.1:3b`) — never the PTY
substrate. Running the PTY on ollama/glm does not drive the real TUI
and is what the production cutover fixed.

## Cross-references

- Spike report: `docs/research/spikes/granite-tui-pty-spike.md` (v7).
- Container: `agent/granite_container/container.py` (uses the driver).
- Architecture doc: [`granite-interactive-tui.md`](granite-interactive-tui.md).
- Originating results doc: `docs/plans/completed/granite-interactive-tui-poc-results.md`.
- [Omnigent `claude_native_*` Reference Map](omnigent-hook-edge-reference.md) — maps
  the 9 Omnigent production practices for replacing the C5 heuristic with Stop/StopFailure
  hook edges; Practice 6 (verified-submit injection) directly targets `SUBMIT_KEY_DELAY_S`
  and the bare `\r` send in this driver.
