# Omnigent `claude_native_*` Reference Map

This document is a durable reference map of nine engineering practices extracted from the Omnigent codebase's `claude_native_*` module family. It exists so that engineers working on our granite PTY / hook integration can quickly find the Omnigent precedent for each practice without re-auditing the Omnigent source from scratch.

## What Is Omnigent?

Omnigent is a prior-art codebase that solved the same problem we are solving: driving an interactive Claude Code TUI session over a PTY with reliable turn-end detection, injection, and crash recovery. Their `claude_native_forwarder.py`, `claude_native_bridge.py`, `claude_native_hook.py`, and `claude_native_executor.py` modules implement a hook-first completion authority pattern — Stop/StopFailure hooks, not PTY quiescence, are the authoritative signal that a turn has ended.

We are not forking Omnigent. We are learning from it. The table below maps each practice to its Omnigent file and line range and to our current equivalent (or gap).

## Definitions

- **Stop hook**: A Claude Code hook that fires exactly once when Claude finishes a turn cleanly. Maps to `idle` state in Omnigent.
- **StopFailure hook**: A Claude Code hook that fires exactly once when Claude errors or aborts a turn. Maps to `failed` state in Omnigent.
- **UserPromptSubmit hook**: Fires when the user message is accepted by the TUI. Used to drive the `running` badge, not completion.
- **PTY quiescence (C5 idle)**: A ~1 s heuristic — no PTY writes for one second. Oscillates on mid-turn lulls; unreliable as a completion signal.
- **Edge file**: A file (or append-only log) written by hooks to signal state changes to the operator process.
- **Hook cursor**: A durable byte-offset + fingerprint that lets the operator replay only unseen hook events after a restart.
- **SubAgent**: A child Claude Code session spawned by the parent session (e.g. builder, code-reviewer). Its Stop hooks share the parent's hook settings unless explicitly isolated.
- **Compaction**: Context-window compaction that produces a `PreCompact` hook event followed by a `SessionStart source=compact` hook.
- **Fork-on-resume**: A session resume that surfaces a new Claude session ID, implying the session forked rather than truly resumed.

## Practice Reference Table

| # | Practice | Omnigent file:line | Our-side equivalent | Home |
|---|----------|--------------------|---------------------|------|
| 1 | **Stop/StopFailure as authoritative turn-end edge** | `claude_native_forwarder.py:100-103` maps `{"Stop": "idle", "StopFailure": "failed"}`; forward loop at `claude_native_forwarder.py:2161-2237` | Replace C5 idle in `read_until_idle` (`pty_driver.py:474-585`) as completion authority | **#1688** |
| 2 | **PTY reduced to two jobs: inject + running/idle badge** | `claude_native_forwarder.py:96-99` — pane does only (a) inject input via tmux send-keys/paste-buffer and (b) drive running/idle badge from UserPromptSubmit + pane activity | `read_until_idle` conflates both; refactor keeps PTY for liveness/badge, strips completion authority from it | **#1688** |
| 3 | **Hook writes edge to bridge file; operator reads the edge** | `claude_native_hook.py:69-122` (`main()`) calls `record_hook_event` at `claude_native_bridge.py:1319-1335` — appends one JSON envelope per line; atomic `fsync` + `os.replace` state update at `claude_native_bridge.py:809-844` | No edge file today; container uses in-process `on_turn()` callback (`container.py:563/583`, invoked at `container.py:1085-1089`, `container.py:1162-1169`) | **NEW** |
| 4 | **Durable, idempotent hook cursor** | `HookForwardState(event_cursor, byte_offset, cursor_fingerprint)` at `claude_native_forwarder.py:108-126`; fingerprint detects truncation/replacement before seeking stale offset at `claude_native_forwarder.py:2201-2218` | No durable cursor; worker-restart can double-deliver without it | **NEW** |
| 5 | **Subagent-hook filtering: child Stop must not end parent turn** (FIRST-CLASS LOAD-BEARING) | `claude_native_forwarder.py:2220-2237` — subagent lifecycle hooks land in same file because subagents inherit parent hook settings; subagent StopFailure explicitly skipped | `pty_driver.py:382-384` spawns claude with only `--model`, `--permission-mode bypassPermissions`, conditional `--session-id` — NO `--settings` isolation; child Stop hooks share parent stream. Confirmed: Dev persona fans out to Sonnet subagents (builder/code-reviewer) constantly; naive Stop-hook wiring WILL end Dev turn early | **NEW** |
| 6 | **Verified-submit injection: poll-until-committed, re-send** | `inject_user_message` at `claude_native_bridge.py:2288-2410` — `tmux load-buffer` + `paste-buffer -p` (bracketed paste, survives >16 KB), polls `capture-pane` until draft visible, sends Enter, verifies draft left box, re-sends if not | Fixed `SUBMIT_KEY_DELAY_S=0.5` then bare `\r` at `pty_driver.py:422-461` — can race TUI's paste-coalescing and silently drop | **NEW** |
| 7 | **Completion decoupled from injection** | `run_turn` at `claude_native_executor.py:99-146` yields `TurnComplete(response=None)` immediately after injection; actual completion arrives asynchronously via Stop-hook; `_inject_lock` at `claude_native_executor.py:48-62` | Synchronous `write()` → `read_until_idle()` block in `pty_driver.py:422-585` | **#1688** |
| 8 | **Compaction boundaries forwarded, not mistaken for completion** | `claude_native_forwarder.py:2238-2244` maps `PreCompact` / `SessionStart source=compact` to compaction-status event | No compaction awareness; mid-turn compaction looks like quiescence to C5 | **NEW** |
| 9 | **Sticky-failed against trailing PTY idle** | `_publish_status` keeps `failed` sticky so `StopFailure` isn't overwritten by trailing PTY idle at `claude_native_forwarder.py:97-99`, loop `claude_native_forwarder.py:2161-2237` | No failure-vs-success distinction at the edge | **#1719** |

## Fork-on-Resume Guard + Dead-vs-Stalled Disambiguation

Home: **#1721**

These two practices belong together because both protect against incorrect state inference after a session boundary event (resume or apparent stall).

### Fork-on-Resume Guard

`SessionStart source=resume` is annotated and de-duplicated against `seen_claude_session_ids` at `claude_native_hook.py:123-167` and `claude_native_bridge.py:1346-1351`. A resume surfacing a new Claude session ID is flagged: it can fork the session rather than silently rebinding to the old one. Without this guard, a forked resume looks like a clean continuation.

### Dead-vs-Stalled Disambiguation Rationale

Three cases, three outcomes:

- **Dead** (`pexpect.EOF`, `isalive() == False`): process gone, no Stop will ever arrive — resume path.
- **Stalled-but-alive** (process alive, quiet, no Stop within watchdog window): with hook as completion authority, "alive + quiet + no Stop" means unambiguously still running. The PTY quiescence heuristic cannot distinguish this from a finished turn; the hook-based model can.
- **Never-started / startup-plateau**: `NON_RESUMABLE_DETERMINISTIC` — escalate-only. Our side: `agent/crash_signature.py:207-228`, `reflections/crash_recovery.py:280-293`.

## Context: The Money Quote

From `omnigent/claude_native_forwarder.py:89-99`:

> `Stop` -> idle and `StopFailure` -> failed are the authoritative turn-end edges (each fires once when Claude finishes / errors a turn); they drive sub-agent terminal delivery ... The PTY-activity `idle` cannot: it is a ~1s-quiescence heuristic that oscillates on every mid-turn lull, so delivering on it fired a premature completion and **idempotently locked out the real one**. `UserPromptSubmit` -> running stays PTY-derived — the pane watcher drives the UI running/idle badge and catches what `Stop` misses (interrupts, compaction failures, TUI edits).

This quote captures the core insight. PTY quiescence is a badge signal, not a completion signal. Conflating the two is the root cause of premature completion and subsequent idempotent lockout.

## Home Tag Index

| Home tag | Practices |
|----------|-----------|
| **#1688** | 1, 2, 7 |
| **#1719** | 9 |
| **#1721** | Fork-on-resume guard, dead-vs-stalled disambiguation |
| **NEW** | 3, 4, 5, 6, 8 |

## Caveats

Omnigent citations are pinned to their HEAD at 2026-06-18. Re-verify against Omnigent HEAD when revisiting. Their `claude_native_*` modules carry a Phase A → Phase B migration comment and are actively evolving. Line numbers may shift; the module names and logical practices will be stable for longer.

Practice 5 (subagent-hook filtering) is marked FIRST-CLASS, LOAD-BEARING. It is a hard acceptance criterion on issue #1688. Any implementation of Stop-hook wiring that does not account for child Stop events ending the parent turn prematurely is incomplete.
