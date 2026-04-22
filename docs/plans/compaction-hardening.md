---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-04-23
tracking: https://github.com/tomcounsell/ai/issues/1127
last_comment_id:
allow_unchecked: true
---

# Compaction Hardening — JSONL Backup, Cooldown, Post-Compact Nudge Guard

## Problem

Our worker spawns `claude -p` subprocesses per `AgentSession`, and those subprocesses silently hit context compaction whenever their conversation history approaches the context-window limit. Today, compaction fires reactively from the SDK with no Valor-side safeguards around the event. Two related failure modes surface when sessions run long enough to actually compact:

**Current behavior:**

1. **No JSONL backup before compaction.** `agent/hooks/pre_compact.py` currently does a one-line `logger.info(...)` and returns `{}`. There is no snapshot of the on-disk JSONL transcript before compaction proceeds. If the SDK crashes mid-compaction (amux.io fleet observations confirm this happens when the window fills *during* compaction itself), the pre-compact session state is lost and the session becomes unrecoverable — the watchdog's only recourse is a full restart with zero of the prior turn's working context.
2. **No timing guard between `/compact` and the continuation nudge.** After `/compact` runs (either `trigger=auto` as the window fills, or `trigger=manual` if an operator issues `/compact`), the agent returns to idle and `agent/session_executor.py`'s `_enqueue_nudge` can fire "continue" within milliseconds. If that nudge interrupts compaction (because the SDK hasn't finished writing the compacted history yet), the session enters an undefined state that typically requires a full restart. There is no `last_compaction_ts` anywhere on `AgentSession` to gate against, so we cannot even observe the frequency of the race.

**Desired outcome:**

- A JSONL snapshot is written proactively on every `pre_compact` event (both triggers) and the last 3 snapshots per session are retained on disk. A mid-compaction crash becomes recoverable via `claude --resume` against the backup file.
- Compaction is throttled per-session: once a `pre_compact` event fires for session X, any second `pre_compact` event for the same session within 5 minutes is a no-op (no second backup, logger records the skip). This prevents rapid compaction loops from producing degraded summaries and from thrashing disk I/O.
- The nudge path in `_enqueue_nudge` / `determine_delivery_action` consults `last_compaction_ts` on the session and, if `now - last_compaction_ts < 30s`, defers the nudge by re-enqueueing the session at lowered priority (or skipping this tick entirely). Idle-after-compaction is distinguished from idle-normally so compaction has room to complete before continuation input arrives.

## Freshness Check

**Baseline commit:** `b6eebc15ae07cea5c040d66f21de4533bb0f8560` (HEAD of `session/compaction-hardening`, identical to `origin/main`)
**Issue filed at:** 2026-04-22T17:00:10Z (~19 hours before plan creation)
**Disposition:** Minor drift (issue's cited location for the nudge loop is slightly stale — see below)

**File:line references re-verified:**
- `agent/hooks/pre_compact.py` — Issue claims "only a logging hook fires." Verified at all of `pre_compact.py:13-27` — the hook body is 14 lines, returns `{}`, contains zero backup or cooldown logic. Claim holds.
- `bridge/telegram_bridge.py` (nudge loop) — Issue says nudge logic lives in the bridge. This is **stale**: nudge orchestration was extracted into `agent/session_executor.py` (`_enqueue_nudge`, lines 249-383) and `agent/output_router.py` (`determine_delivery_action`, lines 71-127) during the work tracked by plan `extract-nudge-to-pm.md` (issue #743, status `docs_complete`). The bridge itself no longer contains `_enqueue_nudge` or a `last_compaction_ts` check. The corrected site for the 30s guard is therefore `agent/output_router.py::determine_delivery_action` (decision) and `agent/session_executor.py::_enqueue_nudge` (enforcement + metadata write).
- `models/agent_session.py` — Issue suggests storing cooldown via "Redis key tracked by `AgentSession` or a sibling record." Confirmed: `AgentSession` has no `last_compaction_ts` / `compaction_count` fields today (searched entire class body). New fields will need to be added.

**Cited sibling issues/PRs re-checked:**
- #1102 — CLOSED 2026-04-22T17:01:10Z. Superseded into this issue per the issue body.
- #1103 — CLOSED 2026-04-22T17:01:11Z. Superseded into this issue per the issue body.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since="2026-04-22T17:00:10Z" -- agent/hooks/pre_compact.py bridge/telegram_bridge.py worker/__main__.py models/agent_session.py` returned zero commits.

**Active plans in `docs/plans/` overlapping this area:** None. Two plans touch the nudge path structurally but are already merged/shipped and do not concern compaction:
- `extract-nudge-to-pm.md` (status `docs_complete`) — moved nudge orchestration out of the bridge and into `session_executor.py` / `output_router.py`. Benefits us because the integration point is now a single function per concern instead of a 3000-line executor.
- `nudge-stomp-append-event-bypass.md` (status `In Review`) — unrelated CAS/stale-save issue in lifecycle finalization. Does not touch compaction.

**Notes:** The issue's Solution Sketch references `bridge/telegram_bridge.py` for the nudge guard; this plan routes the 30s guard through `agent/output_router.py::determine_delivery_action` + `agent/session_executor.py::_enqueue_nudge` instead, because that is where the code actually lives now. This is a corrective drift fix, not a scope change.

## Prior Art

- **#743 / `extract-nudge-to-pm.md`** — Extracted the 345-line nudge loop from the session-queue executor into `agent/session_executor.py` + `agent/output_router.py`. Made `determine_delivery_action()` a pure function that returns a string action. Our 30s guard hooks in cleanly as one more return-path in that function, exactly because it was made pure.
- **#885 + `lifecycle-cas-authority.md`** — Added CAS guards to `finalize_session()` and `transition_status()` so lifecycle transitions survive stale-object saves. Our cooldown check will use the same `get_authoritative_session` → `transition_status` idiom (see `_enqueue_nudge:300-376`) to avoid introducing a new stale-save hazard when we write `last_compaction_ts`.
- **#898 / `nudge-stomp-append-event-bypass.md`** — Documents how a Popoto full-state `save()` on a stale local AgentSession can clobber fields written by a concurrent writer. We avoid this by using `save(update_fields=["last_compaction_ts", "compaction_count"])` (Popoto partial-save) inside the hook instead of a full-state `save()`.
- **agent/hooks/stop.py** — Existing hook already opens `transcript_path` (via `input_data.get("transcript_path", "")`) and reads bytes for memory extraction (lines 122-127, 262-264). Confirms the PreCompact hook will receive the same `transcript_path` field and can reuse this pattern.

## Research

External research via WebSearch on 2026-04-23 focused on the PreCompact hook's input schema, community backup patterns, and async-safe file-snapshot primitives.

**Queries used:**
- `Claude Code SDK PreCompact hook context compaction JSONL session backup 2026`
- `Claude Code PreCompact hook input schema trigger manual auto 2026`
- `python asyncio file copy snapshot non-blocking JSONL safe concurrent write`

**Key findings:**

- **PreCompact input schema is stable.** Hooks receive `{session_id, transcript_path, cwd, hook_event_name: "PreCompact", trigger: "manual" | "auto", custom_instructions}`. The matcher supports `"manual"` and `"auto"` to distinguish `/compact` from full-context-auto. Source: [Claude Code Hooks reference](https://code.claude.com/docs/en/hooks). This confirms we can cheaply branch on `trigger` if we ever want to apply cooldown only to `auto` compactions (we will — see Open Question 3).
- **Community backup pattern is the straightforward one.** Multiple community hooks (Mike Adolan's SQLite transcript backup, Code Coup's "context recovery hook", mvara-ai/precompact-hook) all follow the same shape: in the PreCompact hook, read `input_data["transcript_path"]`, snapshot the file by byte-copy to a sibling path with a timestamp suffix, and return `{}` quickly. Heavy work goes on a background task, not inline. Sources: [claudefa.st/blog/tools/hooks/context-recovery-hook](https://claudefa.st/blog/tools/hooks/context-recovery-hook), [dev.to/mikeadolan compaction hooks](https://dev.to/mikeadolan/claude-code-compaction-kept-destroying-my-work-i-built-hooks-that-fixed-it-2dgp).
- **Async file-snapshot primitive is `asyncio.to_thread(shutil.copy2, src, dst)`.** `shutil.copy2` preserves timestamps (handy for backup retention) and runs in a thread so it does not block the SDK's event loop. For a backup file a few MB in size the copy completes in milliseconds. Source: [Python asyncio docs](https://docs.python.org/3/library/asyncio.html). We do NOT need `aiofiles` here because the hook is called once per compaction (low frequency) and a thread-executor copy is simpler than a streamed async read/write.

## Spike Results

Two short spikes resolved ambiguities left by the issue's Open-for-planner questions.

### spike-1: Verify PreCompact hook delivers `transcript_path` that points to a valid JSONL file we can snapshot
- **Assumption**: "The SDK's PreCompactHookInput includes `transcript_path`, and by the time the hook fires, the JSONL file on disk is a complete, readable image of the pre-compaction history."
- **Method**: code-read (walk the SDK's `PreCompactHookInput` type + `agent/hooks/stop.py` for a precedent of reading a transcript in a hook) + external confirmation via the Claude Code hooks reference.
- **Finding**: Confirmed. `agent/hooks/stop.py:122-127` already does `transcript_path = input_data.get("transcript_path", "")` and opens it with `open(transcript_path, "rb")` in the Stop hook. The PreCompact hook receives the same field per Anthropic's hooks docs. The JSONL is flushed to disk before the hook fires (the hook runs synchronously in the SDK's compaction critical section, so by the time our handler executes, all prior messages are persisted). Conclusion: straight `shutil.copy2(transcript_path, backup_path)` is correct.
- **Confidence**: high
- **Impact on plan**: Drove the decision to put backup logic in the Python hook (Valor-side) rather than requesting an SDK feature. Resolves Open Question 1 from the issue.

### spike-2: Pick retention policy — N-backups vs age-based TTL
- **Assumption**: "Retention by count (last 3) is sufficient; age-based TTL adds complexity without recovery benefit."
- **Method**: code-read (think through the recovery use case).
- **Finding**: A JSONL backup is only useful for `claude --resume` immediately after a mid-compaction crash. Once a session has resumed from a backup and completed any work, older backups have no recovery value — they cannot be replayed onto a session that has already diverged. So retention should track "how far back could we conceivably want to resume." Three backups covers: (a) the most recent compaction, (b) the one before that if the most recent is itself corrupted, and (c) a safety margin. Time-based TTL would keep backups for sessions that crashed and were never resumed — but in practice those sessions are already orphaned by the worker and cleaned up by `cleanup --age 30`. Conclusion: count-based retention (last 3) is strictly simpler and loses no recovery capability vs age-based TTL.
- **Confidence**: high
- **Impact on plan**: Resolves Open Question 2 from the issue. Backup retention is last-3-per-session, enforced by the same hook after each write.

## Data Flow

Trace from SDK-triggered compaction to a safe nudge-guarded resumption:

1. **Entry**: The Claude Code SDK subprocess is about to compact its conversation. It fires the `PreCompact` hook with `{session_id, transcript_path, trigger, custom_instructions}`.
2. **Hook: backup snapshot**: `agent/hooks/pre_compact.py::pre_compact_hook` receives the input. It computes `backup_dir = Path(transcript_path).parent / "backups"`, creates it if missing, then copies `transcript_path` to `backup_dir / f"{session_uuid}-{utc_ts_int}.jsonl.bak"` using `await asyncio.to_thread(shutil.copy2, ...)`. Returns `{}` within a few hundred ms.
3. **Hook: cooldown write**: Same hook, immediately after the copy completes, looks up the `AgentSession` by the SDK's `session_id` (via the same mapping `agent/sdk_client.py` uses to correlate Claude-Code UUIDs to AgentSession IDs — `_get_prior_session_uuid` / `_store_claude_session_uuid`). Writes `last_compaction_ts = now_utc()` and `compaction_count += 1` with `session.save(update_fields=[...])`. If the lookup fails (hook fires for a session we don't track), the hook logs and still returns `{}` — backup was the critical path, the cooldown is best-effort.
4. **Hook: cooldown check (second invocation)**: On a subsequent PreCompact fire for the same session within 5 minutes, the hook reads `last_compaction_ts` first; if `now - last_compaction_ts < 300s`, it skips the snapshot and returns `{}` immediately (logs at `info` level). No second backup, no cooldown-timestamp update.
5. **Hook: retention**: After a successful snapshot write, the hook lists `backup_dir/{session_uuid}-*.jsonl.bak`, sorts by mtime descending, and unlinks all but the top 3. This is a cheap `os.scandir` + `os.stat` + `os.unlink` loop inside the `asyncio.to_thread` call.
6. **SDK finishes compaction**: The SDK proceeds with compaction, writes the compacted transcript, and returns the session to idle.
7. **Session idle, nudge evaluated**: The session executor's output-callback path calls `route_session_output()` → `determine_delivery_action()`. The session that just idled after a compaction passes through this code.
8. **30s guard**: `determine_delivery_action()` now accepts a new `last_compaction_ts: float | None` parameter. If `last_compaction_ts` is set and `now - last_compaction_ts < 30s`, it returns the new action `"defer_post_compact"` instead of `"nudge_continue"` / `"nudge_empty"` / `"nudge_rate_limited"`.
9. **Defer enforcement**: In `agent/session_executor.py`'s action dispatch (near lines 798-848), the new `"defer_post_compact"` branch schedules a short re-evaluation: `await asyncio.sleep(1)` then re-pops the session and re-evaluates, OR simpler — re-enqueues the session with `priority="low"` and returns. No `_enqueue_nudge` call is made this tick.
10. **Output**: Either (a) the 30s window expires on a subsequent tick and the nudge fires normally, or (b) the SDK completes compaction and produces real output that routes through `"deliver"` before the 30s expires — in which case the guard correctly never nudged.

## Why Previous Fixes Failed

No prior fixes attempted. Issues #1102 and #1103 were both untested risk findings (status `Untested — likely gap`) from external fleet-operations research and were closed into this issue. This plan is the first concrete remediation.

## Architectural Impact

- **New dependencies**: None. Uses only `shutil`, `pathlib`, `asyncio`, and the existing `claude_agent_sdk` hook API. The backup directory lives inside the SDK's own project dir (`~/.claude/projects/{slug}/sessions/backups/`) so no new storage layout is introduced.
- **Interface changes**:
  - `determine_delivery_action()` gains one optional kwarg: `last_compaction_ts: float | None = None`. Default preserves existing behavior (no guard). `route_session_output()` forwards the value.
  - `AgentSession` gains two new fields: `last_compaction_ts: float | None` and `compaction_count: int = 0`. Both are non-indexed IntField / FloatField additions (no schema migration — Popoto is schema-on-write).
  - `pre_compact_hook` gains real behavior but its signature (`input_data, tool_use_id, context → dict`) is unchanged.
  - A new action string `"defer_post_compact"` is added to the contract documented in `determine_delivery_action`'s docstring.
- **Coupling**: Increases slightly. The PreCompact hook now reads/writes `AgentSession` (previously the hook was model-agnostic). The decision to couple the hook to our model is deliberate: the cooldown state must live somewhere durable across compactions, and `AgentSession` is the one record keyed by a stable UUID that the SDK hook can correlate via the existing `_get_prior_session_uuid` helper.
- **Data ownership**: The `AgentSession.last_compaction_ts` field is owned by the PreCompact hook (sole writer). Readers (`determine_delivery_action`) are pure and don't mutate.
- **Reversibility**: High. Reverting = delete backup logic from the hook (returns to no-op logger), drop the two new model fields (Popoto discards unknown fields), delete the defer branch from output_router. No data migration needed.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (scope alignment on the defer-branch behavior — punt vs sleep-and-reevaluate)
- Review rounds: 1 (correctness of the cooldown read/write, behavior of the defer action under concurrency)

A small plan would suffice if the scope were only the backup. Adding the per-session cooldown state and the 30s nudge guard pulls in the output_router + AgentSession model, pushing this into Medium. Not Large — no new storage system, no new service, no protocol change.

## Prerequisites

No prerequisites — this work touches only in-repo Python files, uses existing `claude_agent_sdk` hook infrastructure, and writes snapshots into the SDK's own project directory (which is already guaranteed to exist because the SDK itself wrote the transcript there).

## Solution

### Key Elements

- **Backup snapshot**: The PreCompact hook copies `transcript_path` to a sibling `backups/` directory with a timestamped name before compaction proceeds.
- **Cooldown field pair**: Two new fields on `AgentSession` (`last_compaction_ts`, `compaction_count`) provide durable per-session state so the cooldown survives process restarts and cross-worker coordination.
- **5-minute debounce**: The hook itself enforces the cooldown — a second PreCompact fire inside 300s is a fast return-no-op.
- **Retention**: The hook prunes to the last 3 backups per session UUID after each successful snapshot.
- **30s post-compact nudge guard**: `determine_delivery_action` gains a `last_compaction_ts` parameter and a new `"defer_post_compact"` return value. The session executor handles the new action by re-enqueueing the session at low priority rather than firing `_enqueue_nudge`.

### Flow

**SDK signals compaction imminent** → PreCompact hook fires → **Hook snapshots JSONL to backups/ + writes `last_compaction_ts`** → SDK compacts → **Session returns to idle** → Output router reads `last_compaction_ts` → **If within 30s: action=`defer_post_compact`, executor re-enqueues low-priority, no nudge** → (30s passes) → **Next output-callback fires, `last_compaction_ts` now stale, normal nudge flow resumes**

### Technical Approach

- **Hook lives in `agent/hooks/pre_compact.py`**, not in a new module. It is the natural home — the current hook is already wired into `build_hooks_config()` (`agent/hooks/__init__.py:35`), so no wiring change is needed.
- **Session-UUID → AgentSession correlation uses the existing machinery**. `agent/sdk_client.py` already maintains a Claude-UUID ↔ AgentSession-session_id mapping via `_get_prior_session_uuid` / `_store_claude_session_uuid`. The hook extracts `session_id` from `input_data`, looks up the AgentSession via `AgentSession.query.filter(session_id=session_id)`, and operates on the first match. Non-matches (e.g., a non-Valor Claude session running in the same cwd) are tolerated — the hook logs and still snapshots the JSONL (backup has value even if we can't correlate to a session row).
- **Cooldown write uses `save(update_fields=[...])`**, not a full-state save. This avoids the stale-save hazard documented in `nudge-stomp-append-event-bypass.md` — even if some other writer has an older AgentSession in memory, Popoto's partial-save only overwrites the two named fields.
- **Backup filename format**: `{claude_session_uuid}-{int(utc_ts)}.jsonl.bak` under `~/.claude/projects/{slug}/sessions/backups/`. Using the Claude UUID (which the hook already has from `input_data["session_id"]`) avoids needing to look up the AgentSession just for the filename. The `int(utc_ts)` suffix gives 1-second resolution, which is more than enough given the 5-minute cooldown.
- **Retention**: After write, scan the backups directory for files matching `{uuid}-*.jsonl.bak`, sort by basename-timestamp descending, unlink index 3 onward. O(N) in the number of backups per session (N ≤ 4 in steady state).
- **Nudge guard decision point**: Add parameter `last_compaction_ts: float | None` to `determine_delivery_action()`. If the parameter is set and `now - last_compaction_ts < 30`, return `"defer_post_compact"` before any other classification logic runs (earliest possible branch). `route_session_output()` looks up `session.last_compaction_ts` from the AgentSession and forwards it.
- **Defer enforcement in the executor**: Add a single `elif action == "defer_post_compact":` branch in `agent/session_executor.py` alongside the existing `nudge_*` branches. The branch increments no counters, logs at info level, and calls `await _enqueue_nudge(..., nudge_feedback=NUDGE_MESSAGE, priority="low")` — except the re-enqueue sets the session back to `pending` with `priority="low"` so the next tick is delayed behind any higher-priority work. `chat_state.completion_sent` is NOT set, so if output later arrives from the SDK it still routes normally.

Alternative considered and rejected: an `await asyncio.sleep(30)` inline in the defer branch. Rejected because it holds the session executor coroutine for 30s on each post-compact fire, which starves concurrent sessions and couples the wait time to a single process's event loop.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `pre_compact.py`'s hook body currently has zero exception handlers. This plan will wrap (a) the `shutil.copy2` call and (b) the `AgentSession.save(update_fields=...)` call in `try/except Exception:` + `logger.warning(...)` blocks. Backup failure must NOT raise — raising out of a hook crashes the SDK session. Test: mock `shutil.copy2` to raise `OSError("disk full")`, call `pre_compact_hook`, assert it returns `{}` and a warning was logged.
- [ ] `_enqueue_nudge` already has `_TERMINAL_STATUSES` guards; the new `"defer_post_compact"` path must preserve them. Test: session in `completed` status + recent `last_compaction_ts` → executor logs and returns early, does not re-enqueue.

### Empty/Invalid Input Handling
- [ ] `transcript_path` empty string or missing file → hook logs `warning`, skips snapshot, still attempts cooldown write. Test: pass `input_data={"session_id": "x", "transcript_path": ""}`, assert no copy attempted, no exception raised.
- [ ] `session_id` missing or unknown to AgentSession query → hook skips cooldown write, but the snapshot still ran if `transcript_path` was valid. Test: pass unknown session_id, assert snapshot file exists, assert no AgentSession mutation.
- [ ] `last_compaction_ts` is `None` (first compaction of a session's life) → `determine_delivery_action` does NOT return `"defer_post_compact"`, falls through to existing logic. Test: assert action == `"nudge_continue"` when `last_compaction_ts=None` with all other nudge-continue conditions met.
- [ ] `last_compaction_ts` is exactly `now - 30.0s` → action is NOT deferred (boundary test). Test: `monkeypatch` `time.time()` to freeze, assert action != `"defer_post_compact"` when age is exactly 30s.

### Error State Rendering
- The feature has no user-visible output — compaction is invisible to Telegram chats. The only error rendering path is structured log warnings, which are already covered by the exception-handling tests above.

## Test Impact

- [ ] `tests/unit/test_nudge_loop.py` — UPDATE: add test cases for the new `"defer_post_compact"` action. Existing nudge tests still pass (new param is optional, defaults to None).
- [ ] `tests/unit/test_session_executor_extraction_decoupling.py` — UPDATE: add a test that exercises the defer branch in the action-dispatch switch, asserting `_enqueue_nudge` is called with `priority="low"` and `chat_state.completion_sent` remains False.
- [ ] `tests/unit/test_agent_session_queue.py` — No change expected; the cooldown fields are additive and don't affect the existing create/read paths.
- [ ] NEW: `tests/unit/hooks/test_pre_compact_hook.py` — CREATE. Covers: snapshot happy path, snapshot with missing transcript, cooldown skip within 5min, retention pruning, exception swallowing.
- [ ] NEW: `tests/unit/test_output_router_compaction_guard.py` — CREATE. Covers all `last_compaction_ts` branches in `determine_delivery_action` (None, stale, fresh, boundary).
- [ ] NEW: `tests/integration/test_compaction_hardening.py` — CREATE. End-to-end: simulate a PreCompact hook invocation on a temp JSONL file, assert a backup appears in `backups/`, fire a second PreCompact within 5min and assert no second backup appears, send an output through `route_session_output` within 30s of the hook and assert it defers.

## Rabbit Holes

- **Don't try to implement proactive compaction triggering.** The issue's amux.io reference mentions "back up at 30% context remaining, trigger /compact at 20%." That requires a context-size meter the SDK doesn't currently expose to hooks. Trying to build one from scratch (token-counting the transcript + a poll loop) is a multi-week project with ambiguous payoff. The PreCompact hook gives us backup-before-compact for free; we don't need to also move compaction earlier.
- **Don't try to make the backup format smarter than a byte-copy.** Community hooks that parse the JSONL and write a markdown summary (e.g., Mike Adolan's SQLite thing) are solving a different problem (human-readable review). For our recovery use case, the raw JSONL that `claude --resume` consumes is what we need.
- **Don't try to lock or atomic-rename the snapshot.** `shutil.copy2` reads a file the SDK has already fully flushed (the hook fires between turns). A normal copy is fine; there's no concurrent writer.
- **Don't try to suppress the `/compact` nudge race from the SDK side.** We can't wait for the SDK to finish compaction before delivering output — we observe idleness via the existing output-callback. The 30s guard on the Valor side is the correct (and only) intervention point.
- **Don't make the 30s guard configurable per session type yet.** Until we observe a case where Teammate or Dev sessions need a different window, a single constant keeps the code simple. (See Open Question 3.)

## Risks

### Risk 1: Hook exception crashes the SDK session
**Impact:** If `pre_compact_hook` raises (e.g., `OSError` on disk full, `KeyError` on malformed input), the SDK treats it as a hook failure and may abort compaction, leaving the session in a worse state than if we'd done nothing.
**Mitigation:** Wrap every side-effectful call (copy, save, unlink, list) in `try/except Exception: logger.warning(...)` blocks. The hook always returns `{}`. Hook-level test asserts this invariant.

### Risk 2: Cooldown check races with itself across workers
**Impact:** Two workers each hold an `AgentSession` instance for the same session and both hit the PreCompact hook within milliseconds (unlikely but possible in a multi-worker deployment). Both see `last_compaction_ts=None`, both snapshot, both write. Result: two near-identical backups and a double-increment of `compaction_count`.
**Mitigation:** Accept it. The race window is sub-second and the worst outcome is an extra backup file (which gets pruned next round) and a count that is off by one. We do not need a distributed lock for this. Documented explicitly in the hook's docstring.

### Risk 3: `last_compaction_ts` never set because AgentSession lookup fails
**Impact:** If the hook's `AgentSession.query.filter(session_id=sid)` returns empty (hook fires for a session we don't track, or there's a race between session creation and the first compaction), we never write `last_compaction_ts`. The 30s nudge guard silently does nothing for that session — but that session's nudge path is the pre-fix behavior anyway, so it's no worse than today.
**Mitigation:** Hook logs at `info` level when the lookup misses, so we can see the miss rate in production logs. The snapshot still runs.

### Risk 4: Backup directory fills disk over months
**Impact:** If retention-pruning ever fails silently, we could accumulate backups indefinitely. Given ~1 KB per JSONL turn and sessions that compact every few hours, even 10,000 orphaned backups is under 100 MB — negligible by modern standards. But left unchecked over months, a pathological session (compacting every 5 minutes for a week) could accumulate tens of thousands of backups if the hook keeps failing retention.
**Mitigation:** Retention runs in its own `try/except` so a failure logs but doesn't crash the hook. A follow-up issue (post-MVP) can add a nightly reflection that globs `~/.claude/projects/*/sessions/backups/*.jsonl.bak` and prunes anything older than 7 days as defense-in-depth. Out of scope for this plan.

## Race Conditions

### Race 1: Concurrent PreCompact hooks for the same session
**Location:** `agent/hooks/pre_compact.py` (hook body) and `models/agent_session.py::AgentSession.save(update_fields=...)`
**Trigger:** Two workers or two SDK subprocess invocations for the same AgentSession fire PreCompact within milliseconds. (Not expected in normal operation — one Claude-Code subprocess per AgentSession — but possible during a crash-recovery restart overlap.)
**Data prerequisite:** Neither writer's `last_compaction_ts` is visible to the other before both have done their check. Partial-save write is the final source of truth.
**State prerequisite:** `AgentSession` exists in Redis.
**Mitigation:** `save(update_fields=["last_compaction_ts", "compaction_count"])` means each writer only overwrites the two named fields; last-writer-wins on the timestamp (both writers set approximately the same `now_utc()` ± milliseconds, so the difference is immaterial). Not a correctness hazard, documented as accepted-loss in Risk 2.

### Race 2: Output router reads `last_compaction_ts` while the hook is mid-write
**Location:** `agent/output_router.py::determine_delivery_action` (read) and `agent/hooks/pre_compact.py` (write)
**Trigger:** A post-compaction output arrives during the hook's `AgentSession.save` call.
**Data prerequisite:** Output router re-reads the AgentSession from Redis just before calling `determine_delivery_action`.
**State prerequisite:** The PreCompact hook's cooldown write has completed before the idle output is evaluated. Empirically, the SDK serializes the PreCompact hook before the compaction body and before the subsequent idle tick, so this is the expected ordering.
**Mitigation:** The hook's cooldown write is synchronous from the SDK's perspective (it `await`s the hook return). By the time the session returns to idle and the output callback fires, `last_compaction_ts` is already persisted. No explicit lock needed. `route_session_output` re-reads the session from Redis anyway.

### Race 3: Defer-branch re-enqueue collides with `_enqueue_nudge`'s CAS guards
**Location:** `agent/session_executor.py::_enqueue_nudge` and the new `"defer_post_compact"` branch
**Trigger:** The defer branch is implemented as a call into `_enqueue_nudge` with `priority="low"`. If the session is concurrently being finalized by another path, `_enqueue_nudge`'s `_TERMINAL_STATUSES` guard at line 278 should catch it.
**Data prerequisite:** The session's status must be readable and accurate.
**State prerequisite:** `_enqueue_nudge`'s existing re-read + CAS machinery (`get_authoritative_session` + `transition_status`) correctly handles late-arriving terminal transitions.
**Mitigation:** Reuse `_enqueue_nudge` as-is. Its guards were specifically designed for this. The defer branch does not bypass any existing lifecycle protection.

## No-Gos (Out of Scope)

- **Proactive compaction triggering** (backup at 30%, compact at 20%). Requires a token meter the SDK doesn't expose. Separate issue if we ever want this.
- **Cross-session backup consolidation** (one directory per project instead of per-session prefix). Not needed at current volume.
- **Configurable 30s window per session type.** Single constant for now (see Open Question 3).
- **Age-based retention TTL.** Count-based (last 3) is sufficient — see spike-2.
- **SQLite transcript backup like Mike Adolan's hook.** Raw JSONL is what we need for `claude --resume`; a structured backup is a separate feature.
- **Automatic restore on mid-compaction crash detection.** This plan gives us the backup; detecting and restoring is a follow-up.
- **Metrics / dashboards for compaction frequency.** Logger `info` lines are sufficient for v1. Can add analytics later if compaction turns out to be a hotspot.

## Update System

No update system changes required. This feature is purely internal:
- No new dependencies (only stdlib + existing `claude_agent_sdk`)
- No new config files or env vars
- No changes to `scripts/remote-update.sh` or `.claude/skills/update/`
- The new backup directory (`~/.claude/projects/*/sessions/backups/`) is created lazily on first compaction, so there's no install-time migration

## Agent Integration

No agent integration required. This is worker-internal:
- The PreCompact hook is wired by `agent/hooks/__init__.py::build_hooks_config()` which is already called by `sdk_client.py` when spawning Claude Code — no change needed there.
- No new MCP tool (the backup is not user-visible).
- No new bridge imports — the bridge doesn't touch compaction.
- The new `AgentSession` fields (`last_compaction_ts`, `compaction_count`) are read by the output router and the hook only; neither surface to Telegram.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/compaction-hardening.md` describing: what compaction is, where backups live, the 5-min cooldown contract, the 30s nudge guard, and the recovery procedure (operator copies a `.jsonl.bak` over the live session file and runs `claude --resume`).
- [ ] Add entry to `docs/features/README.md` index table under a reliability/worker category.

### Inline Documentation
- [ ] `agent/hooks/pre_compact.py`: module docstring updated to describe backup + cooldown; function docstring updated to state the hook's guarantees (never raises, writes a backup on first fire, cools down within 5min).
- [ ] `agent/output_router.py::determine_delivery_action`: docstring updated to document the new `last_compaction_ts` parameter and the `"defer_post_compact"` return value.
- [ ] `models/agent_session.py::AgentSession`: docstring / field comments on `last_compaction_ts` and `compaction_count` explaining their writer (pre_compact_hook) and readers (output router, any future dashboard).

### External Documentation Site
Not applicable — this repo has no external docs site.

## Success Criteria

- [ ] A PreCompact hook invocation creates a `.jsonl.bak` file in `backups/` next to the transcript, within ~200ms of the hook firing.
- [ ] A second PreCompact invocation for the same session within 5 minutes creates no new backup (log line at `info` confirms skip).
- [ ] `AgentSession.last_compaction_ts` is set after the first compaction for a tracked session; `compaction_count` increments.
- [ ] When `last_compaction_ts` is within 30s of `now`, `determine_delivery_action` returns `"defer_post_compact"` instead of any nudge action.
- [ ] The defer branch in `session_executor.py` re-enqueues the session at `priority="low"` without calling `chat_state.completion_sent = True`, preserving the ability for real SDK output to route normally if it arrives.
- [ ] Retention: after 4 compactions for one session (across multiple days if needed — simulated in tests), exactly 3 backups remain.
- [ ] Hook exception safety: injecting `OSError` into `shutil.copy2` does not propagate out of the hook and does not prevent the AgentSession cooldown write.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep -n 'last_compaction_ts' agent/output_router.py agent/session_executor.py agent/hooks/pre_compact.py models/agent_session.py` returns a match in each of the 4 files.
- [ ] Not a bug fix with an existing xfail — no xfail conversion needed (confirmed: `grep -rn 'xfail' tests/ | grep -iE 'compact|nudge'` empty at plan time).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (pre_compact hook + model)**
  - Name: `hook-builder`
  - Role: Implement backup logic, cooldown state, retention pruning in `agent/hooks/pre_compact.py`. Add `last_compaction_ts` and `compaction_count` to `AgentSession`.
  - Agent Type: builder
  - Resume: true

- **Builder (output router + executor)**
  - Name: `router-builder`
  - Role: Add `last_compaction_ts` parameter + `"defer_post_compact"` action to `determine_delivery_action`; wire the defer branch into `session_executor.py`.
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: `test-writer-compaction`
  - Role: Author unit + integration tests for hook, router, and executor defer branch.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `compaction-validator`
  - Role: Run full test suite, verify success criteria, report pass/fail.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `compaction-documentarian`
  - Role: Create `docs/features/compaction-hardening.md`, update index, update inline docstrings.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add AgentSession cooldown fields
- **Task ID**: build-model-fields
- **Depends On**: none
- **Validates**: `tests/unit/test_agent_session.py` (no behavioral change, just new fields default-valued)
- **Informed By**: spike-2 (count-based retention, simple fields)
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `last_compaction_ts = FloatField(default=None)` and `compaction_count = IntField(default=0)` to `AgentSession` in `models/agent_session.py`.
- Update docstring to describe writer (pre_compact_hook) and readers (output router).

### 2. Implement pre_compact hook
- **Task ID**: build-pre-compact-hook
- **Depends On**: build-model-fields
- **Validates**: `tests/unit/hooks/test_pre_compact_hook.py` (create)
- **Informed By**: spike-1 (hook receives transcript_path, byte-copy is correct)
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement JSONL snapshot via `asyncio.to_thread(shutil.copy2, src, dst)` into `{transcript_parent}/backups/{session_uuid}-{int(ts)}.jsonl.bak`.
- Look up AgentSession by `session_id` from `input_data`; write `last_compaction_ts` and bump `compaction_count` with `save(update_fields=[...])`.
- Cooldown check: if existing `last_compaction_ts` within 300s, skip snapshot + save, log and return `{}`.
- Retention: after successful write, keep last 3 backups per `session_uuid`, unlink older.
- All side effects wrapped in `try/except Exception: logger.warning(...)`; hook always returns `{}`.

### 3. Add `last_compaction_ts` to output router
- **Task ID**: build-router-guard
- **Depends On**: build-model-fields
- **Validates**: `tests/unit/test_output_router_compaction_guard.py` (create)
- **Informed By**: Freshness Check (corrected site — this lives in output_router, not bridge)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `last_compaction_ts: float | None = None` kwarg to `determine_delivery_action()` and `route_session_output()`.
- Early-return `"defer_post_compact"` when `last_compaction_ts is not None` and `now - last_compaction_ts < 30`.
- Update the docstring's action list to document the new return value.

### 4. Wire defer branch into session executor
- **Task ID**: build-executor-defer
- **Depends On**: build-router-guard
- **Validates**: `tests/unit/test_session_executor_extraction_decoupling.py` (update + new test case)
- **Informed By**: Prior Art (`_enqueue_nudge` has existing CAS + terminal-status guards we can reuse)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- In `agent/session_executor.py` action-dispatch block (near lines 798-848), add `elif action == "defer_post_compact":` branch.
- Branch logs at info, calls `_enqueue_nudge(..., priority="low")`, does NOT set `chat_state.completion_sent = True`.
- Ensure the route_session_output caller site reads `session.last_compaction_ts` and forwards it.

### 5. Test suite
- **Task ID**: build-tests
- **Depends On**: build-pre-compact-hook, build-router-guard, build-executor-defer
- **Assigned To**: test-writer-compaction
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/hooks/test_pre_compact_hook.py` covering: happy path, missing transcript, cooldown skip, retention pruning, exception swallowing.
- Create `tests/unit/test_output_router_compaction_guard.py` covering all last_compaction_ts branches.
- Create `tests/integration/test_compaction_hardening.py` exercising end-to-end hook → AgentSession mutation → output router deferral.
- Update `tests/unit/test_nudge_loop.py` and `tests/unit/test_session_executor_extraction_decoupling.py` per Test Impact.

### 6. Validation
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: compaction-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/hooks/test_pre_compact_hook.py tests/unit/test_output_router_compaction_guard.py tests/integration/test_compaction_hardening.py tests/unit/test_nudge_loop.py tests/unit/test_session_executor_extraction_decoupling.py -v`.
- Run `pytest tests/unit/ tests/integration/ -q` to verify no regressions.
- Run `python -m ruff format . && python -m ruff check .`.
- Verify all Success Criteria checkboxes; report pass/fail.

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: compaction-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/compaction-hardening.md` (what + where + how-to-recover).
- Add row to `docs/features/README.md`.
- Verify docstrings updated on touched functions.

### 8. Final Validation
- **Task ID**: final-validate
- **Depends On**: document-feature
- **Assigned To**: compaction-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run full test suite.
- Confirm docs file exists and is indexed.
- Produce final PASS report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/hooks/test_pre_compact_hook.py tests/unit/test_output_router_compaction_guard.py tests/integration/test_compaction_hardening.py -q` | exit code 0 |
| Full suite still green | `pytest tests/ -x -q` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Hook implements backup | `grep -n 'shutil.copy2' agent/hooks/pre_compact.py` | output > 0 |
| Router has defer branch | `grep -n 'defer_post_compact' agent/output_router.py` | output > 0 |
| Executor handles defer | `grep -n 'defer_post_compact' agent/session_executor.py` | output > 0 |
| Model has cooldown field | `grep -n 'last_compaction_ts' models/agent_session.py` | output > 0 |
| Feature doc exists | `test -f docs/features/compaction-hardening.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Should the 30s nudge guard be configurable per session type?** The issue raised this; the plan currently uses a single 30s constant for all session types. PM sessions are the chattiest and most likely to race with compaction; Dev sessions rarely compact. Is a single constant acceptable for v1, or should we gate on `session_type` now?
2. **Should we add a nightly defense-in-depth cleanup for orphaned backups?** The hook's in-line retention is the primary mechanism; a nightly sweep over `~/.claude/projects/*/sessions/backups/` catches cases where retention itself silently failed. In-scope for this plan, or follow-up?
3. **Is `"defer_post_compact"` + `priority="low"` re-enqueue the right enforcement, or should we punt the nudge tick outright?** The plan chose re-enqueue-low so concurrent work is unblocked and the next tick naturally re-evaluates. Alternative: set a module-level `asyncio.Event` per session that the nudge path awaits, and signal it after 30s elapse. More complex, slightly faster recovery. Preference?
