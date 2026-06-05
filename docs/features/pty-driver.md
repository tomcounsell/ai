# Substrate Driver (PTY Driver)

**Status:** Shipped in the granite operator interactive TUI PoC (issue
#1546). Module: `agent/granite_container/pty_driver.py`.

## What it is

A thin pexpect-backed wrapper around the interactive `claude` TUI. The
driver does NOT use `claude -p`; it spawns an interactive session
attached to a pseudo-terminal and exposes a small surface
(`spawn`, `write`, `read_until_idle`, `send_ctrl_c`, `close`) the
container layer can use without reaching into pexpect directly.

This is the PoC's **substrate** — every other component in
`agent/granite_container/` sits on top of it.

## C1-C5 substrate facts (the load-bearing invariants)

The driver models the Claude Code TUI's submit/idle/interject surface
(per the v7 spike report's findings at
`docs/research/spikes/granite-tui-pty-spike.md`):

### C1 — submit key is `\r` (CR, 0x0D), not `\n` (LF, 0x0A)

The TUI is a readline-style input field. `\r` is the submit key; `\n`
is a literal newline within the field. Sending `hello\n` leaves the
cursor in the input box; `hello\r` commits the message.

The driver's `write()` method normalizes the trailing newline to `\r`
automatically. Internal newlines are preserved (the TUI input box
supports multi-line input via a literal `\n`; only the *final* newline
is the submit key).

### C2 — first-ctrl-c interjection text

In TUI v2.1.160, the first ctrl-c surfaces the hint "Press Ctrl-C
again to exit". Older TUI versions used "Interrupted · What should
Claude do instead?" (the prior PoC's docs at
`docs/features/granite-agent-loop.md:294-296` referenced the older
text and are now historical).

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
skipped (per the PoC's Q5 disposition).

### C4 — `/help` overlay

The `/help` slash command renders as a non-dismissing overlay. The
bottom-bar text changes from "bypass permissions" to "esc to cancel"
while the overlay is active. Idle detection must recognize the
"esc to cancel" state; the loop holds while the user dismisses the
overlay (typically with Esc).

### C5 — idle signal is the bottom-bar text + glyph + content-floor

The TUI's idle state is the bottom bar containing "bypass permissions"
plus the prompt glyph (`>` or `❯`). Both must be present. The
`min_content_bytes` floor (default 400) prevents false-positives from
the TUI briefly re-rendering the bar while the model is still loading
a response.

The driver's `read_until_idle` returns an `IdleResult` with
`saw_idle`, `buffer` (ANSI-stripped), `idle_marker`, and `elapsed_ms`.

## Why pexpect over stdlib (subprocess + pty)

The v7 spike report compared pexpect with the stdlib `pty` module and
found pexpect's API to be a cleaner surface for the loop's
`read_nonblocking` / `before` / `after` semantics. The stdlib path is
competitive (the spike validated both); the PoC ships pexpect because
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

## Module-pick policy (avoid the operator's own model)

The driver picks a substrate model automatically. It prefers `gemma*`
(small/fast, conversational), falls back to any non-`granite*` model,
and uses the first ollama model as a last resort. Granite is the
*operator*, never the substrate model — running the substrate on
granite would be self-referential and degrade routing accuracy.

The pick policy is exposed as a constant (`MODEL_PICK_PREFER`,
`MODEL_PICK_AVOID_PREFIX`) for the prerequisite check to mirror.

## Cross-references

- Spike report: `docs/research/spikes/granite-tui-pty-spike.md` (v7).
- Container: `agent/granite_container/container.py` (uses the driver).
- Architecture doc: [`granite-interactive-tui.md`](granite-interactive-tui.md).
- Plan: `docs/plans/granite_interactive_tui_poc.md`.
