# Granite Hook-Driven Turn Returns

**Status:** Shipped · **Issue:** [#1688](https://github.com/tomcounsell/ai/issues/1688) · **Plan:** `docs/plans/granite_hook_driven_turn_returns.md`

## What this is

The granite container drives interactive `claude` TUI sessions (PM and Dev) over PTYs. Two of its load-bearing decisions used to be **heuristics**:

1. **Turn boundaries** were guessed by PTY idle-polling (`read_until_idle`, the C5 byte-quiescence heuristic). This oscillated on mid-turn lulls and created a flush-timing race: after the PTY *looked* idle, the container read "the last assistant entry" from the JSONL transcript with no guarantee the entry was the just-completed turn.
2. **"Needs human input"** (the `[/user]` route) was *inferred* from a self-reported token, with no first-class signal that a session was blocked on a question or a permission prompt.

This feature makes Claude Code's own **hook event stream** the deterministic source of both edges, through **one transport-agnostic consumer seam** (`HookEdgeConsumer`) that works whether the container drives two PTYs, one PTY, or a headless role. The PTY is demoted to what it is good at: injection, a running/idle badge, and crash/liveness detection.

This shrinks the scraper; it does **not** delete it (startup dialogs and liveness still need the PTY), and it does **not** retire the `[/dev]`/`[/user]`/`[/complete]` token protocol or the classifier regex (`classify_pm_prefix`). Hooks replace the *heuristics*, not the classifier.

## Architecture

Two layers: a hook happy-path and a PTY crash-path.

```
Bridge enqueues AgentSession
   │
   ▼
BridgeAdapter.run()
   ├─ generate_hook_settings(pm) → pm_settings.json + pm_edge.ndjson   (per PTY, keyed by session_id)
   ├─ generate_hook_settings(dev) → dev_settings.json + dev_edge.ndjson
   └─ spawns  claude --session-id <uuid> --settings <gen.json> --permission-mode bypassPermissions
        │
        ▼
   claude TUI runs a turn ── Stop hook fires ──► hook_forwarder.py appends one NDJSON envelope
        │                                             to the per-session edge file (fail-silent, atomic)
        ▼
   Container._await_turn_end(pty, HookEdgeConsumer, session_id)
        ├─ poll edge file (level-triggered, durable cursor)
        ├─ parent Stop for this session_id → turn_end  → read final message from payload transcript_path
        ├─ SubagentStop                    → subagent_end (NEVER ends the parent turn — Practice 5)
        ├─ Notification/PermissionRequest/AskUserQuestion → needs_human → [/user] route (deterministic)
        ├─ PreCompact / SessionStart(source=compact)      → compaction (never mistaken for completion)
        └─ PTY EOF / !isalive with no Stop → crash-resume (bounded) → escalate on cap
```

### The pieces

| Piece | Module | Role |
|-------|--------|------|
| Per-session settings generator | `hook_edge.generate_hook_settings` / `generate_pair_hook_settings` | Writes the `--settings` JSON registering every target hook to the forwarder; reserves the edge file path (Race 1); adds the pre-auth `permissions` block (Task 3). |
| Fail-silent forwarder | `hook_forwarder.py` | Reads the hook payload on stdin, appends one NDJSON envelope to the per-session edge file. Stdlib only, atomic `O_APPEND` write, always `exit 0`. |
| Transport-agnostic consumer | `hook_edge.HookEdgeConsumer` | Tails the edge file from a durable cursor, classifies `Stop`/`SubagentStop`/needs-human/compaction edges. Never touches the PTY. |
| Durable cursor | `hook_edge.HookCursor` | `(event_cursor, byte_offset, fingerprint)` — restart-safe, no double-delivery, truncation-safe. |
| Turn authority | `Container._cycle_turn` / `_await_turn_end` | Waits on the `turn_end` edge, racing a crash/timeout watchdog; reads the final message from the payload `transcript_path`. |
| Crash-resume | `Container._resume_crashed_pty` + `PTYDriver(resume_uuid=...)` | Resumes the crashed session via `--resume <uuid>` + a verified `continue` nudge; bounded by the crash-resume cap → operator escalation. |

### Edge transport: append-only file + durable cursor

The edge channel is a per-PTY append-only NDJSON file, **not** a Redis list. This honors the repo's Popoto-only Redis rule (no raw `r.lpush`/`r.rpush`/`r.lpop`), needs no new Popoto model, and is restart-safe. The forwarder writes a file path; no Redis client ever runs inside a hook subprocess.

Envelope shape (one JSON object per line):

```json
{"ts": 1719900000.5, "event": "Stop", "payload": {"hook_event_name": "Stop", "session_id": "...", "transcript_path": "/....jsonl", ...}}
```

`event` is lifted from `payload.hook_event_name` so the consumer can classify without re-parsing; `payload` is the verbatim hook JSON.

## Subagent disambiguation is native (Practice 5)

`Stop` auto-converts to `SubagentStop` for Task-tool subagents. The event **type** distinguishes a parent PM/Dev turn-end from a subagent-end — no filtering heuristic. Turn-end keys strictly on `hook_event_name == "Stop"` **and** a matching `session_id`; `SubagentStop` is a distinct edge kind that never ends the parent turn. This is the exact load-bearing failure the issue flagged (a Dev turn ending the instant a builder subagent finished).

Verified live under Substrate B (see Task 0 below): the `SubagentStop` payload carries `agent_id` / `agent_type`; the parent `Stop` payload does not.

## The three race conditions

1. **Stop edge lands before the wait arms.** The consumer is level-triggered against the append-only file (reads from the cursor, not edge-triggered), and the edge path is reserved at spawn before the first PTY write — so a Stop written before the wait begins is still read.
2. **Crash detected concurrently with a late Stop.** On a watchdog wake (PTY EOF/!isalive), the consumer drains the edge file **first**; a `turn_end` present takes precedence and the following EOF is treated as a normal post-turn exit, not a crash.
3. **Subagent edge interleaved with the parent Stop.** The forwarder does a single atomic `O_APPEND` write per envelope (no torn lines); the consumer classifies by `hook_event_name`, so a `SubagentStop` never advances the turn-boundary state regardless of interleave position.

## Feature flag & fallback

`settings.granite.hook_driven_turn_end` (env `GRANITE__HOOK_DRIVEN_TURN_END`) defaults **on**. When off, or when no edge file is provisioned, `_cycle_turn` falls back to the pre-#1688 idle-completion path (`read_until_idle`'s `saw_idle`) — the documented safety valve if a `claude` version regresses the hook contract.

- `hook_turn_end_wait_s` (default 600s) — the outer budget the container waits for a `Stop` edge before the watchdog trips. This only fires when the PTY is alive but no Stop arrives (the silent-hook mode); it always races PTY EOF.
- `hook_crash_resume_cap` (default 3) — max crash-resume attempts on one turn before escalating with an operator-terminal message (no infinite loop).

**Silent-hook signal:** when the container waits out the budget with the PTY alive and no Stop, it records a process-local fallback via `hook_edge.record_hook_fallback`. A rising count is the observable trigger to investigate before the idle fallback is ever removed (plan No-Gos `[ORDERED]`).

## Startup pre-authorization (Task 3, companion)

The generated per-session settings carry a `permissions` block (`defaultMode: "bypassPermissions"`) that pre-answers the steady-state permission bar via the settings source, reinforcing the `--permission-mode bypassPermissions` spawn flag and shrinking the `startup_parser` scrape surface. Honest limit: the trust-folder dialog and the auto-update notice are governed by `~/.claude.json` project-trust state and the auto-updater, not the `--settings` file, so `startup_parser` retains those dismissals (shrink, not delete). Gated by the `pre_authorize` flag on `generate_hook_settings` (default on).

## Task 0 fidelity gate (hard gate, PASSED)

Before any consumer wiring landed, Task 0 verified the hooks actually fire under the ollama-backed `claude` binary (Substrate B). Result (2026-07-02, `claude 2.1.198`, backend `qwen3.6:35b-a3b-coding-nvfp4`):

- **Parent `Stop`** fires on a simple turn, carrying `transcript_path`, `session_id`, and `last_assistant_message`; no `agent_id`.
- **`SubagentStop`** fires on a Task-bearing fan-out turn, carrying `agent_id`, `agent_type` (`general-purpose`), `transcript_path`, and `agent_transcript_path`; distinct from the parent Stop.

The gate is kept durable as `TestStopHookFidelityGate` in `tests/integration/test_granite_ollama_e2e.py` (probe helper `tests/granite_faults/hook_fidelity.py`) so every new pinned `claude` release can be re-verified with `GRANITE_OLLAMA_SMOKE=1 pytest`.

## Tests

- `tests/unit/granite_container/test_hook_edge.py` — settings generation, fail-silent forwarder, consumer classification (subagent filter / compaction / needs-human / corrupt line), durable-cursor idempotency + truncation reset.
- `tests/unit/granite_container/test_container_hook_turn.py` — `_await_turn_end`: parent Stop completes, SubagentStop never ends the parent turn, needs-human → `[/user]`, compaction ignored, crash-resume + cap escalation, Race 2, and the `turn_detection_wedge` green-swap.
- `tests/unit/granite_container/test_hook_preauth.py` — startup pre-authorization block, pair settings, the retained trust-folder dismissal, and the fallback signal.
- `tests/unit/granite_container/test_fault_injection.py::TestClass1TurnDetectionWedge` — the failure-class-1 wedge (stripped idle bar) is resolved by the hook edge.
- `tests/integration/test_granite_ollama_e2e.py::TestStopHookFidelityGate` — the durable Substrate B gate.

## See also

- [Granite PTY Container: Production Path](granite-pty-production.md) — the production path this turn-authority swap sits inside.
- [Granite Failure-Simulation Test Harness](granite-failure-simulation-harness.md) — the `turn_detection_wedge` red test this fix turns green, and Substrate B.
- [Omnigent Hook-Edge Reference](omnigent-hook-edge-reference.md) — the practice map this feature consumes.
