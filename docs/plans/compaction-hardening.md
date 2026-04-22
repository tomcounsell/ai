---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-04-23
tracking: https://github.com/tomcounsell/ai/issues/1127
last_comment_id:
revision_applied: true
critique_blockers_resolved:
  - B1 (2026-04-23): session-id correlation corrected — hook receives the SDK's UUID in `input_data["session_id"]`, which maps to `AgentSession.claude_session_uuid` (NOT `session_id`). Lookup now uses `AgentSession.query.filter(claude_session_uuid=<hook_input.session_id>)`. Cooldown state keyed by `claude_session_uuid`.
  - B2 (2026-04-23): `_enqueue_nudge` has no `priority` kwarg (hardcodes `priority="high"` on lines 338, 370). Plan redesigned to use an early-return in `determine_delivery_action` that short-circuits BEFORE `_enqueue_nudge` is reached. No signature change to `_enqueue_nudge`; the `"defer_post_compact"` branch in the executor simply logs + returns without re-enqueue.
  - B3 (2026-04-23): Spike-1 upgraded from code-read to empirical verification. Added spike-1a (prototype spike, worktree-isolated) that triggers a real compaction, captures the backup, and validates byte-for-byte that the pre-compact JSONL state is fully on disk at PreCompact-hook-fire time. Fallback (explicit fsync + line-count check) added to the plan if the spike reveals partial-write behavior.
critique_concerns_applied:
  - C1 (2026-04-23): SDK-tick backstop added — tick-based compaction detection in the executor's output-callback path as defense-in-depth if the PreCompact hook fails to fire or is skipped by the SDK. See Technical Approach "SDK-tick backstop" section and task 2b.
  - C2 (2026-04-23): Rabbit Holes section rewritten with sharper out-of-scope language to prevent builder misreading an item as in-scope.
  - C3 (2026-04-23): FileNotFoundError handling documented explicitly — the JSONL snapshot path wraps `shutil.copy2` in `try/except FileNotFoundError: logger.debug(...)` so a missing transcript (brand-new session, path race) is a silent no-op rather than a hook crash.
  - C4 (2026-04-23): Observability counters consolidated into a single coherent scheme on the AgentSession record (`compaction_count`, `compaction_skipped_count`, `nudge_deferred_count`) + one optional Redis hash (`metrics:compaction:daily:{yyyy-mm-dd}`) for aggregate dashboards. No ad-hoc per-event Redis keys. See Technical Approach "Observability counters".
  - C5 (2026-04-23): Rollback order documented in Reversibility section — (1) disable the executor's defer branch, (2) disable the hook's backup + cooldown writes, (3) drop the two new AgentSession fields, (4) delete the router parameter. This order is mandatory because rolling back the model fields before the readers crashes the readers.
  - C6 (2026-04-23): Stale Open Question 3 (about `priority="low"` re-enqueue) removed. The B2 fix in critique_blockers_resolved already settled this — the defer branch is a pure no-op, no re-enqueue, no priority kwarg. The question no longer applies.
  - C7 (2026-04-23): FloatField import confirmed — `popoto.FloatField` already exists (used in `models/memory.py:87`, `models/knowledge_document.py:48`, `models/task_type_profile.py:74-75`). Task 1 now explicitly adds `FloatField` to the `from popoto import (...)` block in `models/agent_session.py` alongside the existing imports.
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
- **agent/sdk_client.py `_get_prior_session_uuid` / `_store_claude_session_uuid` (lines 152-241)** — Canonical mapping code between our bridge session_id and Claude Code's session UUID. `_store_claude_session_uuid(session_id, claude_uuid)` writes `session.claude_session_uuid = claude_uuid` (line 234). The hook does the inverse lookup: given the UUID from `input_data["session_id"]`, find the AgentSession by filtering on `claude_session_uuid`. This is the key correction versus the pre-critique draft, which incorrectly filtered on `session_id`.

## Research

External research via WebSearch on 2026-04-23 focused on the PreCompact hook's input schema, community backup patterns, and async-safe file-snapshot primitives.

**Queries used:**
- `Claude Code SDK PreCompact hook context compaction JSONL session backup 2026`
- `Claude Code PreCompact hook input schema trigger manual auto 2026`
- `python asyncio file copy snapshot non-blocking JSONL safe concurrent write`

**Key findings:**

- **PreCompact input schema is stable.** Hooks receive `{session_id, transcript_path, cwd, hook_event_name: "PreCompact", trigger: "manual" | "auto", custom_instructions}`. The matcher supports `"manual"` and `"auto"` to distinguish `/compact` from full-context-auto. Source: [Claude Code Hooks reference](https://code.claude.com/docs/en/hooks). This confirms we can cheaply branch on `trigger` in a follow-up if we want to apply cooldown only to `auto` compactions; for v1, both triggers share the same 5-minute cooldown.
- **IMPORTANT — the hook's `input_data["session_id"]` is the Claude Code SDK's internal session UUID, NOT our bridge/Telegram `AgentSession.session_id`.** The Claude Code SDK exposes its own UUID (e.g. a uuid4 it generates per subprocess) through hook payloads and `ResultMessage.session_id`. Our bridge uses a DIFFERENT `session_id` (the Telegram-thread-scoped string keyed by chat_id + root_message_id or an SDLC slug). These two namespaces must NOT be conflated. The mapping is written at `agent/sdk_client.py:1735` by `_store_claude_session_uuid(bridge_session_id, claude_uuid)` which sets `AgentSession.claude_session_uuid = claude_uuid` (models/agent_session.py:179). Therefore, the PreCompact hook must query by `claude_session_uuid`, not `session_id`.
- **Community backup pattern is the straightforward one.** Multiple community hooks (Mike Adolan's SQLite transcript backup, Code Coup's "context recovery hook", mvara-ai/precompact-hook) all follow the same shape: in the PreCompact hook, read `input_data["transcript_path"]`, snapshot the file by byte-copy to a sibling path with a timestamp suffix, and return `{}` quickly. Heavy work goes on a background task, not inline. Sources: [claudefa.st/blog/tools/hooks/context-recovery-hook](https://claudefa.st/blog/tools/hooks/context-recovery-hook), [dev.to/mikeadolan compaction hooks](https://dev.to/mikeadolan/claude-code-compaction-kept-destroying-my-work-i-built-hooks-that-fixed-it-2dgp).
- **Async file-snapshot primitive is `asyncio.to_thread(shutil.copy2, src, dst)`.** `shutil.copy2` preserves timestamps (handy for backup retention) and runs in a thread so it does not block the SDK's event loop. For a backup file a few MB in size the copy completes in milliseconds. Source: [Python asyncio docs](https://docs.python.org/3/library/asyncio.html). We do NOT need `aiofiles` here because the hook is called once per compaction (low frequency) and a thread-executor copy is simpler than a streamed async read/write.

## Spike Results

Three spikes resolved ambiguities left by the issue's Open-for-planner questions. Spike-1a is a **prerequisite-gating empirical verification** — the plan does NOT ship without a passing spike-1a result.

### spike-1: Verify PreCompact hook delivers `transcript_path` that points to a valid JSONL file
- **Assumption**: "The SDK's PreCompactHookInput includes `transcript_path` and points at a real file."
- **Method**: code-read.
- **Finding**: Confirmed. `agent/hooks/stop.py:122-127` already does `transcript_path = input_data.get("transcript_path", "")` and opens it with `open(transcript_path, "rb")` in the Stop hook. The PreCompact hook receives the same field per Anthropic's hooks docs.
- **Confidence**: high
- **Impact on plan**: Drove the decision to put backup logic in the Python hook (Valor-side) rather than requesting an SDK feature. Resolves Open Question 1 from the issue.

### spike-1a: **(prerequisite to implementation)** Empirically verify JSONL is fully flushed to disk when PreCompact fires
- **Assumption**: "By the time the PreCompact hook's Python handler executes, the on-disk JSONL at `transcript_path` is a byte-complete image of the pre-compaction history. Meaning: every message that was in the SDK's in-memory conversation before compaction has been persisted to disk before our handler runs."
- **Why this needs empirical verification, not code-read**: The Claude Code SDK is a closed-source binary from our perspective. We cannot walk its write-buffering policy by reading our own code. Community hooks (mvara-ai/precompact-hook, Mike Adolan's tool) assume full-flush is true, but none publish a test that proves it. If the SDK flushes asynchronously — even by a single buffered chunk — our backup would capture a torn state and `claude --resume` against the backup would fail with a JSONL parse error on the last line. This is a backup-integrity risk we cannot accept on faith.
- **Method**: prototype in worktree isolation. Spawn a real `claude -p` subprocess with a prompt that forces a long conversation (e.g. a loop of 50 "echo N" turns to push history past the SDK's compaction threshold). Register a PreCompact hook that, when it fires, does THREE things: (a) records `len(Path(transcript_path).read_bytes())` and the final line of the JSONL, (b) calls `os.fsync()` on the file and re-reads — reports any diff, (c) writes a marker JSON to a side-channel file. After compaction completes and the session exits, walk the POST-compact transcript's `parent_uuid` chain and count how many messages preceded compaction. Compare that count against the line count captured in step (a). Any mismatch means the hook fired before a flush completed.
- **Time cap**: 5 minutes agent time.
- **Agent Type**: builder in worktree (prototype isolation — no committed code, report returns yes/no/finding only).
- **Finding**: `_TO BE FILLED BY SPIKE-1A EXECUTION_`. If the finding is "flush is complete," the backup uses a straight `shutil.copy2`. If the finding is "partial flush possible," the fallback path below activates.
- **Confidence**: `_TO BE FILLED_` after spike.
- **Fallback if spike-1a fails (empirical partial-flush observed)**: The hook (a) opens the file with `O_DIRECT`-semantics read (via `os.open(path, os.O_RDONLY)` + explicit `os.fsync` on the source fd is a no-op for a read-only fd, so instead we poll line-count stability: read line count, `time.sleep(0.05)`, read again, repeat until two consecutive reads match OR 500ms elapses), (b) after stability, performs `shutil.copy2`. The 500ms ceiling bounds the PreCompact hook's worst-case latency. Added to the hook implementation as a conditional branch — activated only if spike-1a proves partial flushes can occur.
- **Impact on plan**: This spike is a **gate on build**. If spike-1a reports partial-flush behavior AND the fallback stability-polling proves fragile in its own prototype, the plan is revised (not shipped as-is) to take a different backup approach (e.g., snapshot the SDK's in-memory state via an SDK API if one exists, or consume the transcript via a tail-the-log side channel). Builder MUST NOT start task `build-pre-compact-hook` before spike-1a passes.

### spike-2: Pick retention policy — N-backups vs age-based TTL
- **Assumption**: "Retention by count (last 3) is sufficient; age-based TTL adds complexity without recovery benefit."
- **Method**: code-read (think through the recovery use case).
- **Finding**: A JSONL backup is only useful for `claude --resume` immediately after a mid-compaction crash. Once a session has resumed from a backup and completed any work, older backups have no recovery value — they cannot be replayed onto a session that has already diverged. So retention should track "how far back could we conceivably want to resume." Three backups covers: (a) the most recent compaction, (b) the one before that if the most recent is itself corrupted, and (c) a safety margin. Time-based TTL would keep backups for sessions that crashed and were never resumed — but in practice those sessions are already orphaned by the worker and cleaned up by `cleanup --age 30`. Conclusion: count-based retention (last 3) is strictly simpler and loses no recovery capability vs age-based TTL.
- **Confidence**: high
- **Impact on plan**: Resolves Open Question 2 from the issue. Backup retention is last-3-per-session, enforced by the same hook after each write.

## Data Flow

Trace from SDK-triggered compaction to a safe nudge-guarded resumption.

**Terminology note (B1 fix):** The SDK hook's `input_data["session_id"]` is the Claude Code SDK's internal UUID. Our `AgentSession.session_id` is the bridge/Telegram thread identifier. They are NOT the same namespace. The mapping is stored on `AgentSession.claude_session_uuid` (written by `_store_claude_session_uuid` at `agent/sdk_client.py:1735`). Throughout the flow below, `claude_session_uuid` = the SDK's UUID, `bridge_session_id` = our AgentSession.session_id.

1. **Entry**: The Claude Code SDK subprocess is about to compact its conversation. It fires the `PreCompact` hook with `{session_id, transcript_path, trigger, custom_instructions}` — where `input_data["session_id"]` is the **Claude SDK UUID** (=our `claude_session_uuid`).
2. **Hook: backup snapshot**: `agent/hooks/pre_compact.py::pre_compact_hook` receives the input. It extracts `claude_session_uuid = input_data["session_id"]`. Computes `backup_dir = Path(transcript_path).parent / "backups"`, creates it if missing, then copies `transcript_path` to `backup_dir / f"{claude_session_uuid}-{utc_ts_int}.jsonl.bak"` using `await asyncio.to_thread(shutil.copy2, ...)`. Returns `{}` within a few hundred ms.
3. **Hook: cooldown write (AgentSession lookup via `claude_session_uuid`)**: After the copy completes, the hook looks up the `AgentSession` by the Claude UUID via `AgentSession.query.filter(claude_session_uuid=claude_session_uuid)`. If found: writes `last_compaction_ts = now_utc()` and `compaction_count += 1` with `session.save(update_fields=["last_compaction_ts", "compaction_count"])`. If NOT found (hook fires before `_store_claude_session_uuid` persisted the mapping, or for a session we don't track): the hook logs at `info` level and still returns `{}` — backup was the critical path, the cooldown write is best-effort. **Redis key format: cooldown data lives on the AgentSession record itself; no separate Redis key is needed. The logical correlation key is `claude_session_uuid`, not `session_id`.**
4. **Hook: cooldown check (second invocation)**: On a subsequent PreCompact fire for the same Claude UUID within 5 minutes, the hook re-looks up the AgentSession by `claude_session_uuid` and reads `last_compaction_ts`; if `now - last_compaction_ts < 300s`, it skips the snapshot and returns `{}` immediately (logs at `info` level). No second backup, no cooldown-timestamp update.
5. **Hook: retention**: After a successful snapshot write, the hook lists `backup_dir/{claude_session_uuid}-*.jsonl.bak`, sorts by basename-embedded timestamp descending, and unlinks all but the top 3. This is a cheap `os.scandir` + `os.stat` + `os.unlink` loop inside the `asyncio.to_thread` call.
6. **SDK finishes compaction**: The SDK proceeds with compaction, writes the compacted transcript, and returns the session to idle.
7. **Session idle, nudge evaluated**: The session executor's output-callback path calls `route_session_output()` → `determine_delivery_action()`. The session that just idled after a compaction passes through this code. The executor has access to the `AgentSession` (keyed by `bridge_session_id`) and therefore to its `last_compaction_ts` field directly — no UUID-to-bridge-ID translation is needed here because the executor is already on the bridge-session side of the mapping.
8. **30s guard (B2 fix)**: `determine_delivery_action()` now accepts a new `last_compaction_ts: float | None` parameter. If `last_compaction_ts` is set and `now - last_compaction_ts < 30s`, it returns the new action `"defer_post_compact"` instead of `"nudge_continue"` / `"nudge_empty"` / `"nudge_rate_limited"`. **This early-return in the pure decision function is what suppresses the nudge** — no changes to `_enqueue_nudge`'s signature are required, because when the decision function returns `"defer_post_compact"`, the executor's action-dispatch branch for that action does NOT call `_enqueue_nudge` at all.
9. **Defer enforcement (B2 fix)**: In `agent/session_executor.py`'s action dispatch (near lines 798-848), the new `"defer_post_compact"` branch does three things: (a) logs at `info` level with the session's `last_compaction_ts` age, (b) does NOT call `_enqueue_nudge` — the session is simply left in its current state, (c) does NOT set `chat_state.completion_sent = True`. Because the output-callback loop is driven by the SDK's own idle ticks, another tick will arrive within seconds; on that subsequent tick, `determine_delivery_action` is re-evaluated. If 30s has now passed, the normal nudge path fires; if real SDK output has arrived in the meantime, it routes through `"deliver"`.
10. **Output**: Either (a) the 30s window expires on a subsequent tick and the nudge fires normally, or (b) the SDK completes compaction and produces real output that routes through `"deliver"` before the 30s expires — in which case the guard correctly never nudged.

**Why this design avoids the `_enqueue_nudge` signature change:** The original draft wanted `_enqueue_nudge(..., priority="low")`, but `_enqueue_nudge` has no `priority` kwarg (it hardcodes `session.priority = "high"` on lines 338 and 370 of `agent/session_executor.py`). Adding a `priority` kwarg would require changing every existing call site and reasoning through the interaction with `transition_status(... "pending")`, which is out of scope. The simpler alternative — return early from `determine_delivery_action`, and skip the nudge call entirely in the executor's dispatch branch — achieves the same suppression with zero signature churn on a hot-path function.

## Why Previous Fixes Failed

No prior fixes attempted. Issues #1102 and #1103 were both untested risk findings (status `Untested — likely gap`) from external fleet-operations research and were closed into this issue. This plan is the first concrete remediation.

## Architectural Impact

- **New dependencies**: None. Uses only `shutil`, `pathlib`, `asyncio`, and the existing `claude_agent_sdk` hook API. The backup directory lives inside the SDK's own project dir (`~/.claude/projects/{slug}/sessions/backups/`) so no new storage layout is introduced.
- **Interface changes**:
  - `determine_delivery_action()` gains one optional kwarg: `last_compaction_ts: float | None = None`. Default preserves existing behavior (no guard). `route_session_output()` forwards the value.
  - `AgentSession` gains four new fields per the C4 consolidated counter scheme: `last_compaction_ts: float | None` (FloatField), `compaction_count: int = 0` (IntField), `compaction_skipped_count: int = 0` (IntField), and `nudge_deferred_count: int = 0` (IntField). None are indexed (no schema migration — Popoto is schema-on-write). `FloatField` is added to the `from popoto import` block per C7.
  - `pre_compact_hook` gains real behavior but its signature (`input_data, tool_use_id, context → dict`) is unchanged.
  - A new action string `"defer_post_compact"` is added to the contract documented in `determine_delivery_action`'s docstring.
- **Coupling**: Increases slightly. The PreCompact hook now reads/writes `AgentSession` (previously the hook was model-agnostic). The decision to couple the hook to our model is deliberate: the cooldown state must live somewhere durable across compactions, and `AgentSession` is the one record where the correlation key `claude_session_uuid` is already stored (written by `_store_claude_session_uuid` at `sdk_client.py:1735`). The hook does the inverse lookup — given the Claude UUID from `input_data["session_id"]`, `AgentSession.query.filter(claude_session_uuid=<uuid>)` returns the row.
- **Data ownership**: The `AgentSession.last_compaction_ts` field is owned by the PreCompact hook (sole writer). Readers (`determine_delivery_action`) are pure and don't mutate.
- **Reversibility**: High. Reverting is a four-step sequence and the ORDER MATTERS (C5) — rolling back in the wrong order bricks live sessions by leaving readers pointed at deleted fields or leaving the executor's defer branch pointed at a stale cooldown timestamp.

  **Mandatory rollback order (top-down):**

  1. **Disable the executor's `"defer_post_compact"` branch FIRST.** Either comment out the branch in `agent/session_executor.py` or change `determine_delivery_action` to never return `"defer_post_compact"`. This stops the executor from reading `last_compaction_ts`. Deploy this change first, verify no sessions are stuck in the defer state, then proceed.
  2. **Disable the `pre_compact_hook`'s backup + cooldown writes** by reverting `agent/hooks/pre_compact.py` to the no-op logger. At this point no new writes to `last_compaction_ts`, `compaction_count`, `compaction_skipped_count` are happening.
  3. **Disable the SDK-tick backstop** in `agent/session_executor.py` (C1) by deleting the per-tick backstop block. No new writes to `last_compaction_ts` / `compaction_skipped_count` from this path either.
  4. **Remove the `last_compaction_ts` parameter from `determine_delivery_action` and `route_session_output`.** At this point the four AgentSession fields are unread and unwritten.
  5. **Drop the four new AgentSession fields** (`last_compaction_ts`, `compaction_count`, `compaction_skipped_count`, `nudge_deferred_count`) from `models/agent_session.py`. Popoto discards unknown fields on existing records automatically, so no data migration is needed — but this step MUST come last because earlier steps still reference the fields.

  **What must NOT happen:** Do NOT delete the AgentSession fields before step 4 completes. A reader still pointed at a deleted field raises `AttributeError` on every idle tick, which takes every running session down. If rollback is urgent, steps 1 + 2 + 3 (disabling the writers and readers) are sufficient to neutralize the feature — the model fields can be dropped later during a calm window.

  **Recovery order (if a partial rollback went wrong):** re-apply steps in reverse — restore the fields first, then the readers, then the writers, then the defer branch. The system tolerates "fields present but unread" indefinitely, which is what makes the field-removal step safely the last one.

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

- **Backup snapshot**: The PreCompact hook copies `transcript_path` to a sibling `backups/` directory with a timestamped name before compaction proceeds. Backup filenames are keyed by `claude_session_uuid` (the hook's `input_data["session_id"]`), which is unambiguous even for non-Valor sessions.
- **Cooldown field pair**: Two new fields on `AgentSession` (`last_compaction_ts`, `compaction_count`) provide durable per-session state. **These are keyed via `claude_session_uuid`, not `session_id`** — the hook looks up the AgentSession row with `AgentSession.query.filter(claude_session_uuid=<hook_input.session_id>)` (B1 fix).
- **5-minute debounce**: The hook itself enforces the cooldown — a second PreCompact fire inside 300s for the same `claude_session_uuid` is a fast return-no-op.
- **Retention**: The hook prunes to the last 3 backups per `claude_session_uuid` after each successful snapshot.
- **30s post-compact nudge guard (B2 fix)**: `determine_delivery_action` gains a `last_compaction_ts` parameter and a new `"defer_post_compact"` return value. The session executor handles the new action by **skipping the nudge call entirely for that tick** — no re-enqueue, no priority change, no `_enqueue_nudge` call. The next SDK idle tick re-evaluates the decision function and either defers again (still in the window) or nudges normally (window expired). This avoids any change to `_enqueue_nudge`'s signature.

### Flow

**SDK signals compaction imminent** → PreCompact hook fires (receives `claude_session_uuid` in `input_data["session_id"]`) → **Hook snapshots JSONL to `backups/{claude_session_uuid}-{ts}.jsonl.bak` + looks up AgentSession via `filter(claude_session_uuid=...)` + writes `last_compaction_ts` on that row** → SDK compacts → **Session returns to idle** → Output router reads `session.last_compaction_ts` (from the AgentSession, already keyed by `bridge_session_id`) → **If within 30s: action=`defer_post_compact`, executor SKIPS nudge call entirely for this tick (no _enqueue_nudge, no re-enqueue)** → (SDK fires next idle tick within seconds) → **Guard re-evaluates; if 30s expired, normal nudge flow resumes; if not, defers again**

### Technical Approach

- **Hook lives in `agent/hooks/pre_compact.py`**, not in a new module. It is the natural home — the current hook is already wired into `build_hooks_config()` (`agent/hooks/__init__.py:35`), so no wiring change is needed.
- **Session-UUID → AgentSession correlation uses `claude_session_uuid`, NOT `session_id` (B1 fix)**. `agent/sdk_client.py` maintains the mapping via `_store_claude_session_uuid(bridge_session_id, claude_uuid)` which writes `AgentSession.claude_session_uuid = claude_uuid` (line 234). The SDK's hook input gives us the claude_uuid side; the hook does the inverse lookup via `AgentSession.query.filter(claude_session_uuid=input_data["session_id"])`. The first match (sorted by `created_at` desc, for defense against hypothetical duplicate-uuid rows that shouldn't exist) is the target AgentSession. Non-matches (e.g., a non-Valor Claude session, or a session where `_store_claude_session_uuid` hasn't run yet because the first ResultMessage hasn't been processed) are tolerated — the hook logs at `info` and still snapshots the JSONL (backup has value even if we can't correlate to a session row). Cooldown state lives on the AgentSession record itself under `last_compaction_ts`; there is no separate `compaction:cooldown:*` Redis key — Popoto persists it as part of the AgentSession hash.
- **Cooldown write uses `save(update_fields=[...])`**, not a full-state save. This avoids the stale-save hazard documented in `nudge-stomp-append-event-bypass.md` — even if some other writer has an older AgentSession in memory, Popoto's partial-save only overwrites the two named fields.
- **Backup filename format**: `{claude_session_uuid}-{int(utc_ts)}.jsonl.bak` under `~/.claude/projects/{slug}/sessions/backups/`. Using the Claude UUID (which the hook receives directly from `input_data["session_id"]`) avoids needing any AgentSession lookup to construct the filename. The `int(utc_ts)` suffix gives 1-second resolution, which is more than enough given the 5-minute cooldown. This intentionally keeps backup filenames decoupled from our bridge namespace — a non-Valor session will still get a usefully-named backup.
- **FileNotFoundError handling on the snapshot source (C3)**: The `shutil.copy2(src, dst)` call is wrapped in a dedicated `try/except FileNotFoundError:` handler SEPARATE from the general `try/except Exception:` handler. Rationale: `FileNotFoundError` on the source is an *expected* condition (brand-new session whose first turn hasn't flushed yet; path race where the SDK rotates the transcript between hook-fire and our read; a non-Valor session that never produced a transcript). It must be a silent `logger.debug(...)` no-op, NOT a `logger.warning(...)`. The hook returns `{}` and does NOT attempt a cooldown write (cooldown is meaningful only after a real backup). The `backup_dir.mkdir(parents=True, exist_ok=True)` call is inside the same `try/except FileNotFoundError:` so a missing parent directory on the source side (which implies no transcript exists) is also a silent no-op. All OTHER exceptions (OSError on disk-full, PermissionError, etc.) flow into the outer `try/except Exception: logger.warning(...)` handler. Concrete code shape:
  ```python
  try:
      await asyncio.to_thread(backup_dir.mkdir, parents=True, exist_ok=True)
      await asyncio.to_thread(shutil.copy2, transcript_path, dst)
  except FileNotFoundError:
      logger.debug("pre_compact: transcript missing for %s, skipping snapshot", claude_session_uuid)
      return {}
  except Exception as exc:  # noqa: BLE001 - hook must never raise
      logger.warning("pre_compact: snapshot failed for %s: %s", claude_session_uuid, exc)
      return {}
  ```
- **Retention**: After write, scan the backups directory for files matching `{claude_session_uuid}-*.jsonl.bak`, sort by basename-timestamp descending, unlink index 3 onward. O(N) in the number of backups per session (N ≤ 4 in steady state).
- **Nudge guard decision point (B2 fix)**: Add parameter `last_compaction_ts: float | None` to `determine_delivery_action()`. If the parameter is set and `now - last_compaction_ts < 30`, return `"defer_post_compact"` before any other classification logic runs (earliest possible branch). `route_session_output()` looks up `session.last_compaction_ts` from the AgentSession (keyed by `bridge_session_id`, which is what the executor naturally has in hand) and forwards it.
- **Defer enforcement in the executor (B2 fix)**: Add a single `elif action == "defer_post_compact":` branch in `agent/session_executor.py` alongside the existing `nudge_*` branches (near lines 798-848). The branch:
  - Logs at `info` level the session's `last_compaction_ts` age
  - Does NOT call `_enqueue_nudge` (that's the whole point — the nudge is suppressed, not re-enqueued)
  - Does NOT set `chat_state.completion_sent = True`
  - Increments no counters
  - Returns from the output-callback invocation
  The next SDK idle tick naturally re-invokes `route_session_output` a few seconds later; at that point `determine_delivery_action` is re-evaluated and either (a) returns `"defer_post_compact"` again if still inside the 30s window, or (b) falls through to normal nudge classification if the window has expired. No `_enqueue_nudge` signature change is needed. No new `priority` kwarg is introduced.
- **Why not add a `priority` kwarg to `_enqueue_nudge`**: `_enqueue_nudge` hardcodes `session.priority = "high"` on lines 338 and 370 unconditionally. Adding a kwarg would mean (a) refactoring that hardcode, (b) propagating the kwarg through the existing 5 call sites, (c) reasoning through the interaction with `transition_status(..., "pending")` for the low-priority case. That's a separate refactor, out of scope for this plan. The early-return approach in `determine_delivery_action` achieves the same suppression — the nudge is simply not called at all for that tick — with zero signature churn.

Alternative considered and rejected: an `await asyncio.sleep(30)` inline in the defer branch. Rejected because it holds the session executor coroutine for 30s on each post-compact fire, which starves concurrent sessions and couples the wait time to a single process's event loop.

Alternative considered and rejected: a `priority="low"` re-enqueue via a newly-added kwarg on `_enqueue_nudge`. Rejected because it requires a hot-path refactor (see above). The simple early-return achieves correct suppression without touching `_enqueue_nudge`.

### SDK-tick backstop (C1)

The PreCompact hook is the primary backup + cooldown mechanism. But hooks can fail: the SDK may skip a hook under internal error conditions, a hook may be deregistered by an unrelated code path, or a PreCompact event may fire so close to subprocess termination that the hook's async task never completes. If any of these happen, compaction proceeds without a backup AND without a `last_compaction_ts` write, leaving the nudge path unprotected for that session.

A tick-based backstop in the executor's output-callback path defends against this failure mode. The backstop is defense-in-depth — it does NOT replace the hook, it catches misses.

**Mechanism:**

1. On each invocation of the executor's output-callback (which runs on every SDK idle tick and on every output-message arrival), BEFORE consulting `determine_delivery_action`, compute a lightweight "compaction occurred" heuristic: compare the session's currently-observed message count (from the SDK's `ResultMessage` stream state the executor already tracks) against the session's last-observed-count from the prior tick. If the count has *dropped* since the last tick — a count drop is the SDK's observable signature of a compaction that rewrote the history — treat it as a backstop-detected compaction event.
2. On a backstop-detected compaction, the executor does two things:
   - Writes `last_compaction_ts = now_utc()` and `compaction_skipped_count += 1` (NOT `compaction_count`, because we did not capture a backup — we only observed the event) via `save(update_fields=[...])`. This arms the 30s nudge guard even though the hook missed.
   - Logs at `warning` level: `"pre_compact hook appears to have missed a compaction for %s — backstop armed nudge guard"`. The warning level surfaces this in production log monitoring so we can investigate hook-miss rates.
3. The backstop does NOT attempt a recovery-path JSONL snapshot. Recovery-path snapshots from the executor would require the executor to know the SDK's `transcript_path`, which it does not have directly on hand, AND would require it to run `shutil.copy2` synchronously from inside a hot output-callback path — both costs worse than accepting the missed-backup for this rare case.

**Where the backstop lives:** `agent/session_executor.py` inside the output-callback's existing per-tick bookkeeping block, near the top of `route_session_output` invocation. Implementation is ~15 lines: read the tracked-count from the executor's in-memory state (already present for other purposes), compare to `getattr(session, "_last_observed_message_count", None)`, branch, write the AgentSession partial-save, log.

**Tradeoffs documented for the builder:**
- False positives: If the SDK legitimately rewrites history WITHOUT a compaction (e.g., a tool-use turn is edited in-place — not known to happen but theoretically possible), the backstop arms the nudge guard for 30s. This is a harmless false-deferral; the session simply pauses a nudge for one tick. Acceptable.
- False negatives: If the SDK compacts without reducing the observed message count (e.g., if summaries are counted as messages), the backstop fails to detect. Acceptable — the hook is the primary mechanism and the backstop is opportunistic coverage.
- No coupling to hook state: The backstop does NOT check whether the hook ran. It just checks whether a count drop was observed. This is intentional — the hook and backstop are independent signals, combined only through their shared write target (`last_compaction_ts`).

### Observability counters (C4)

To avoid scattered ad-hoc Redis keys and half-baked dashboard queries, the plan commits to a single coherent counter scheme up front. All observability for this feature lives on TWO surfaces and nowhere else:

**Surface 1 — Per-session counters on `AgentSession` (primary):**

| Field | Type | Writer | Semantics |
|-------|------|--------|-----------|
| `compaction_count` | `IntField(default=0)` | `pre_compact_hook` (on successful snapshot) | Total number of compactions observed for this session. Increments when a backup is successfully written AND the cooldown-timestamp update succeeds. |
| `compaction_skipped_count` | `IntField(default=0)` | `pre_compact_hook` (on cooldown hit) | Number of PreCompact events suppressed by the 5-minute cooldown. Increments inside the cooldown-check branch. |
| `nudge_deferred_count` | `IntField(default=0)` | `session_executor.py` (in the `"defer_post_compact"` branch) | Number of nudge ticks suppressed by the 30s post-compact guard. Increments in the executor's defer-branch, written via `session.save(update_fields=["nudge_deferred_count"])`. |
| `last_compaction_ts` | `FloatField(default=None)` | `pre_compact_hook` | Unix timestamp of the most recent backup-written compaction. Read by the output router. |

All three counter fields use `save(update_fields=[...])` partial-save to avoid the stale-save hazard documented in `nudge-stomp-append-event-bypass.md`.

**Surface 2 — Optional aggregate rollup in Redis (`metrics:compaction:daily:{yyyy-mm-dd}`):**

One hash key per UTC day, with fields `total_compactions`, `total_skipped`, `total_deferred_nudges`. The hook and executor each do a single `HINCRBY` under the current day's key after the per-session counter update succeeds. TTL is 30 days (set on key creation). This surface is **optional** for v1 — if the `HINCRBY` call fails, log `warning` and continue; the per-session counters on AgentSession are the primary source of truth.

**Explicit non-goals:**
- No per-event Redis keys (e.g. `compaction:event:{uuid}:{ts}`). Use the daily-rollup hash or per-session fields instead.
- No per-session Redis keys parallel to AgentSession (e.g. `compaction:cooldown:{uuid}`). Cooldown state lives on the AgentSession record itself; do not create a sibling key.
- No Prometheus, Grafana, or statsd exporter in v1. If aggregate dashboards are wanted later, build them on top of the daily-rollup hash — do not invent new key patterns.
- No changes to `/dashboard.json` beyond what the existing session-serializer already exposes. The new AgentSession fields surface automatically via the existing serialization path; no new dashboard section is added in v1.

**Redis key pattern (single canonical form):**

```
metrics:compaction:daily:{yyyy-mm-dd}     # Hash, TTL 30d, fields: total_compactions, total_skipped, total_deferred_nudges
```

That is the ONLY Redis key this feature introduces. Any builder who finds themselves writing a different `compaction:*` key pattern is off-plan.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `pre_compact.py`'s hook body currently has zero exception handlers. This plan will wrap (a) the `shutil.copy2` call in a **two-tier** handler: `try/except FileNotFoundError: logger.debug(...)` for the expected source-missing case (C3), plus `try/except Exception: logger.warning(...)` for everything else (disk-full, permission-denied, etc.), (b) the `AgentSession.query.filter(claude_session_uuid=...)` call and (c) the `AgentSession.save(update_fields=...)` call in single-tier `try/except Exception:` + `logger.warning(...)` blocks. Backup failure must NOT raise — raising out of a hook crashes the SDK session. Tests: (1) mock `shutil.copy2` to raise `OSError("disk full")`, call `pre_compact_hook`, assert it returns `{}` and a `warning` was logged; (2) separately mock `shutil.copy2` to raise `FileNotFoundError`, assert it returns `{}` and a `debug` log (not warning) was recorded.
- [ ] AgentSession lookup failure (Redis down, model unavailable) must not propagate. Test: mock `AgentSession.query.filter` to raise `ConnectionError`, assert the hook still completes the snapshot (copy already succeeded) and returns `{}`.

### Empty/Invalid Input Handling
- [ ] `transcript_path` empty string → hook logs `warning`, skips snapshot, does NOT attempt cooldown write (cooldown is only meaningful after a successful backup). Test: pass `input_data={"session_id": "x", "transcript_path": ""}`, assert no copy attempted, no AgentSession mutation, no exception raised.
- [ ] `transcript_path` points to a non-existent file (brand-new session or path race) → hook logs at `debug` level (NOT `warning` — FileNotFoundError on the source is an expected condition per C3), skips snapshot, does NOT attempt cooldown write, returns `{}`. Test: pass a valid-looking but non-existent path (e.g., `/tmp/does-not-exist-{uuid}.jsonl`), assert `shutil.copy2` raised `FileNotFoundError`, assert no exception propagated out of the hook, assert no `warning` log line was emitted, assert a `debug` log line was recorded.
- [ ] `session_id` in hook input (the Claude UUID) is missing or unknown to AgentSession query → hook skips cooldown write, but the snapshot still ran if `transcript_path` was valid. Test: pass `input_data["session_id"]` that matches no AgentSession row, assert snapshot file exists, assert no AgentSession mutation, assert info-level log line recorded the miss.
- [ ] `last_compaction_ts` is `None` (first compaction of a session's life) → `determine_delivery_action` does NOT return `"defer_post_compact"`, falls through to existing logic. Test: assert action == `"nudge_continue"` when `last_compaction_ts=None` with all other nudge-continue conditions met.
- [ ] `last_compaction_ts` is exactly `now - 30.0s` → action is NOT deferred (boundary test). Test: `monkeypatch` `time.time()` to freeze, assert action != `"defer_post_compact"` when age is exactly 30s.

### Error State Rendering
- The feature has no user-visible output — compaction is invisible to Telegram chats. The only error rendering path is structured log warnings, which are already covered by the exception-handling tests above.

## Test Impact

- [ ] `tests/unit/test_nudge_loop.py` — UPDATE: add test cases for the new `"defer_post_compact"` action. Existing nudge tests still pass (new param is optional, defaults to None).
- [ ] ~~`tests/unit/test_session_executor_extraction_decoupling.py` — UPDATE: add a test that exercises the defer branch in the action-dispatch switch, asserting that `_enqueue_nudge` is NOT called~~ — **Dropped post-review (#1127 PR #1135 review).** This file is scoped to hotfix #1055 extraction-decoupling semantics, not the broader nudge-flow dispatch. The "defer branch skips `_enqueue_nudge`" invariant is covered by `tests/unit/test_output_router_compaction_guard.py` (returns `"defer_post_compact"` action) and `tests/unit/test_session_executor_tick_backstop.py` (backstop arms the guard); adding a duplicate test here would only re-assert existing coverage in the wrong file.
- [ ] `tests/unit/test_agent_session_queue.py` — No change expected; the cooldown fields are additive and don't affect the existing create/read paths.
- [ ] NEW: `tests/unit/hooks/test_pre_compact_hook.py` — CREATE. Covers: snapshot happy path (keyed by `claude_session_uuid` in filename), snapshot with missing transcript, AgentSession lookup via `claude_session_uuid` (B1 fix — NOT via `session_id`), cooldown skip within 5min, retention pruning keyed by `claude_session_uuid`, exception swallowing on copy/query/save failure, graceful no-op when `claude_session_uuid` matches no AgentSession.
- [ ] NEW: `tests/unit/test_output_router_compaction_guard.py` — CREATE. Covers all `last_compaction_ts` branches in `determine_delivery_action` (None, stale, fresh, boundary).
- [ ] NEW: `tests/unit/test_session_executor_tick_backstop.py` — CREATE (C1). Covers the SDK-tick backstop: (a) no backstop fires when count is steady or increasing, (b) backstop arms `last_compaction_ts` + increments `compaction_skipped_count` when count drops tick-over-tick, (c) backstop swallows exceptions from `save()` without crashing the executor, (d) `_last_observed_message_count` is updated every tick regardless.
- [ ] NEW: `tests/integration/test_compaction_hardening.py` — CREATE. End-to-end: simulate a PreCompact hook invocation on a temp JSONL file with a known `claude_session_uuid`, assert a backup appears in `backups/` with that UUID in the filename, assert the AgentSession row (pre-populated with the same `claude_session_uuid`) has `last_compaction_ts` written, fire a second PreCompact within 5min and assert no second backup appears, send an output through `route_session_output` within 30s of the hook and assert it returns `"defer_post_compact"` AND that `_enqueue_nudge` is never called (B2).
- [ ] NEW: `tests/integration/test_compaction_spike1a.py` — CREATE (run as prerequisite to implementation tasks). Empirically validates spike-1a: spawns a real `claude -p` subprocess with a long conversation, triggers compaction, and asserts the PreCompact-hook's captured backup is byte-complete relative to the pre-compact conversation state. This test is marked `@pytest.mark.slow` and `@pytest.mark.integration`. If it fails, the build blocks until the fallback flush-stability-polling path is added to the hook.

## Rabbit Holes

Each bullet below is **explicitly out of scope for this plan**. A builder who finds themselves implementing any of these is off-plan and should stop. Follow-up issues are fine; inline expansion during this build is not.

- **OUT OF SCOPE: Proactive compaction triggering (context-meter + early `/compact`).** The amux.io reference mentions "back up at 30% context remaining, trigger /compact at 20%." Do not build this. It requires a context-size meter the SDK does not expose to hooks; building one from the transcript (token-counting + a poll loop) is a multi-week feature with ambiguous payoff. Ship the reactive PreCompact-hook backup this plan describes and nothing more. If proactive triggering is wanted, file a separate issue.
- **OUT OF SCOPE: Structured / parsed backup formats (SQLite, Markdown summaries, per-turn indexing).** The only consumer of the backup is `claude --resume`, which reads raw JSONL. Do not parse, transform, filter, or summarize the JSONL during backup. A byte-for-byte `shutil.copy2` is the entire scope of the snapshot path. Community hooks that parse to SQLite (e.g., Mike Adolan's tool) are solving a different problem (human review) — that is not this problem.
- **OUT OF SCOPE: File locking, atomic-rename, or tempfile-move semantics on the snapshot.** The PreCompact hook fires between turns when the SDK has already flushed the transcript. A naked `shutil.copy2` is correct. Do not introduce `fcntl`, `os.link`, `os.rename(tmp, dst)`, or any locking primitive. (The only exception is the spike-1a fallback's stability-polling on line count — that is NOT locking, it is a read-side consistency check.)
- **OUT OF SCOPE: SDK-side compaction suppression or signaling.** We cannot block the SDK's compaction path, and we cannot ask the SDK to wait for us before returning to idle. Do not try. The 30s guard lives on the Valor side (in `determine_delivery_action`) and intervenes only at nudge time. Do not add any IPC or signal to the SDK subprocess.
- **OUT OF SCOPE: Per-session-type configuration for the 30s guard.** Use a single constant (`POST_COMPACT_NUDGE_GUARD_SECONDS = 30`) defined at module scope in `agent/output_router.py`. Do not wire `session_type` into the decision. Do not add an env var, a settings field, or a per-session override. If Teammate or Dev sessions later need a different window, file a follow-up.
- **OUT OF SCOPE: Automatic restore on mid-compaction crash.** This plan writes the backup. Detecting a mid-compaction crash and automatically restoring from the backup is a separate feature. Do not add crash detection, restore logic, or any watchdog integration that consumes the backup. Operators restore manually via `cp backups/{uuid}-{ts}.jsonl.bak transcript_path && claude --resume {uuid}` per the feature doc.
- **OUT OF SCOPE: Compaction metrics dashboards or analytics rollups.** The `compaction_count` / `compaction_skipped_count` / `nudge_deferred_count` fields on AgentSession and the optional daily-rollup Redis hash are as far as observability goes in this plan. Do not add a Grafana dashboard, a new `/dashboard.json` section beyond what the existing session-list already surfaces, or a dedicated analytics tool. Logger `info` lines remain the primary observability surface for v1.

## Risks

### Risk 1: Hook exception crashes the SDK session
**Impact:** If `pre_compact_hook` raises (e.g., `OSError` on disk full, `KeyError` on malformed input), the SDK treats it as a hook failure and may abort compaction, leaving the session in a worse state than if we'd done nothing.
**Mitigation:** Wrap every side-effectful call (copy, save, unlink, list) in `try/except Exception: logger.warning(...)` blocks. The hook always returns `{}`. Hook-level test asserts this invariant.

### Risk 2: Cooldown check races with itself across workers
**Impact:** Two workers each hold an `AgentSession` instance for the same session and both hit the PreCompact hook within milliseconds (unlikely but possible in a multi-worker deployment). Both see `last_compaction_ts=None`, both snapshot, both write. Result: two near-identical backups and a double-increment of `compaction_count`.
**Mitigation:** Accept it. The race window is sub-second and the worst outcome is an extra backup file (which gets pruned next round) and a count that is off by one. We do not need a distributed lock for this. Documented explicitly in the hook's docstring.

### Risk 3: `last_compaction_ts` never set because AgentSession lookup fails
**Impact:** If the hook's `AgentSession.query.filter(claude_session_uuid=<uuid>)` returns empty (possible causes: hook fires for a non-Valor Claude session, OR hook fires for a Valor session BEFORE `_store_claude_session_uuid` has persisted the mapping — the first `ResultMessage.session_id` write at `sdk_client.py:1735` must land before the hook can find the row), we never write `last_compaction_ts`. The 30s nudge guard silently does nothing for that session — but that session's nudge path is the pre-fix behavior anyway, so it's no worse than today.
**Mitigation:** Hook logs at `info` level when the lookup misses, so we can see the miss rate in production logs. The snapshot still runs. If the miss rate is materially > 0% in production (e.g. compaction happens before the first `ResultMessage` for long-running first turns), a follow-up can either (a) write `claude_session_uuid` earlier in the flow (from the SDK's `SystemMessage`, for example) or (b) route the hook to accept a `bridge_session_id` via env var, via the `cwd` field, or via the SDK's custom hook-input extension mechanism if one exists. Out of scope for v1.

### Risk 5: Spike-1a (empirical JSONL flush verification) fails
**Impact:** If the SDK does not fully flush the JSONL before firing PreCompact, our backup would capture a torn state and `claude --resume` against the backup would fail at parse time.
**Mitigation:** Spike-1a is run as a prerequisite to the `build-pre-compact-hook` task. If it fails, the plan's fallback path (stability-polling on line count before copy, up to a 500ms ceiling) activates. If the fallback also proves fragile in its own prototype, the plan is revised — we do not ship a backup that could be silently torn.

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

### Race 3: Defer branch vs session finalization
**Location:** `agent/session_executor.py` — the new `"defer_post_compact"` dispatch branch
**Trigger:** The defer branch fires for a session that another code path has just finalized (terminal status). Because the defer branch is now a pure no-op (B2 fix — no `_enqueue_nudge` call, no state mutation), there is no collision with lifecycle transitions at all.
**Data prerequisite:** None — the defer branch does not read or write any shared state.
**State prerequisite:** None.
**Mitigation:** By design. The pure-no-op defer branch (B2 fix) is strictly simpler than the original re-enqueue approach and cannot collide with finalization, because it performs no mutations. `chat_state.completion_sent` is not touched, `session.status` is not touched, `priority` is not touched. If the session happens to transition to terminal in the window between the PreCompact hook firing and the next idle tick, the next tick's normal terminal-status handling takes over with zero interference from us.

## No-Gos (Out of Scope)

- **Proactive compaction triggering** (backup at 30%, compact at 20%). Requires a token meter the SDK doesn't expose. Separate issue if we ever want this.
- **Cross-session backup consolidation** (one directory per project instead of per-session prefix). Not needed at current volume.
- **Configurable 30s window per session type.** Single constant for now (see Open Question 1).
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

- [ ] Spike-1a passes (empirical flush verification) OR the stability-polling fallback is implemented and its own prototype passes.
- [ ] A PreCompact hook invocation creates a `.jsonl.bak` file in `backups/{claude_session_uuid}-{ts}.jsonl.bak` next to the transcript, within ~200ms of the hook firing (or within ~700ms if the stability-polling fallback is active).
- [ ] A second PreCompact invocation for the same `claude_session_uuid` within 5 minutes creates no new backup (log line at `info` confirms skip).
- [ ] `AgentSession.last_compaction_ts` is set after the first compaction for a tracked session (looked up via `claude_session_uuid`, not `session_id`); `compaction_count` increments.
- [ ] When `session.last_compaction_ts` is within 30s of `now`, `determine_delivery_action` returns `"defer_post_compact"` instead of any nudge action.
- [ ] The defer branch in `session_executor.py` does NOT call `_enqueue_nudge` and does NOT set `chat_state.completion_sent = True`, preserving the ability for real SDK output to route normally if it arrives. `_enqueue_nudge`'s signature is unchanged by this plan (no `priority` kwarg added).
- [ ] Retention: after 4 compactions for one session (across multiple days if needed — simulated in tests), exactly 3 backups remain, all keyed by the same `claude_session_uuid`.
- [ ] Hook exception safety: injecting `OSError` into `shutil.copy2` does not propagate out of the hook and does not prevent the AgentSession cooldown write. Injecting a `ConnectionError` into the `AgentSession.query.filter` call does not propagate either — the snapshot still lands.
- [ ] AgentSession lookup key: `grep -n 'claude_session_uuid' agent/hooks/pre_compact.py` returns a match on the `AgentSession.query.filter(...)` line. `grep -n 'filter(session_id=' agent/hooks/pre_compact.py` returns zero matches (B1 regression guard).
- [ ] SDK-tick backstop (C1): `grep -n '_last_observed_message_count' agent/session_executor.py` returns a match. Simulated message-count drop in the executor's output-callback path increments `compaction_skipped_count` and writes `last_compaction_ts`.
- [ ] FileNotFoundError handling (C3): `grep -n 'FileNotFoundError' agent/hooks/pre_compact.py` returns a match. Unit test confirms missing transcript → `debug` log + `{}` return, not a `warning` log.
- [ ] Observability counter scheme (C4): exactly one Redis key pattern is introduced (`metrics:compaction:daily:{yyyy-mm-dd}`) — `grep -rn 'compaction:' agent/ models/ worker/ bridge/ | grep -v 'metrics:compaction:daily'` returns zero matches on any ad-hoc Redis key.
- [ ] FloatField import (C7): `grep -n 'FloatField' models/agent_session.py` returns exactly two matches — one in the `from popoto import` block, one in the `last_compaction_ts` field declaration.
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
  - Role: Add `last_compaction_ts` parameter + `"defer_post_compact"` action to `determine_delivery_action`; wire the defer branch into `session_executor.py`; implement the SDK-tick backstop (C1) in the executor's output-callback per-tick bookkeeping.
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

### 0. Run spike-1a (prerequisite to implementation)
- **Task ID**: spike-1a-empirical-flush
- **Depends On**: none
- **Validates**: `tests/integration/test_compaction_spike1a.py` (create)
- **Informed By**: B3 (unverified flush timing)
- **Assigned To**: hook-builder
- **Agent Type**: builder (worktree isolation — prototype only)
- **Parallel**: true
- Implement the empirical-flush integration test in a temporary worktree.
- Run the test: spawn real `claude -p`, force compaction via a long conversation, register a PreCompact hook that captures line count + final line of the transcript at hook-fire time.
- Compare captured count to the POST-compact transcript's `parent_uuid`-chain length.
- Report: PASS if flush is complete, FAIL with a diff if not.
- Move the test into the main codebase (marked `@pytest.mark.slow`) on PASS.
- If FAIL: STOP, write findings to `docs/plans/compaction-hardening.md` Spike Results section, and add the stability-polling fallback to the hook plan before proceeding to task 2.

### 1. Add AgentSession cooldown + observability fields
- **Task ID**: build-model-fields
- **Depends On**: none
- **Validates**: `tests/unit/test_agent_session.py` (no behavioral change, just new fields default-valued)
- **Informed By**: spike-2 (count-based retention, simple fields), C4 (consolidated counter scheme), C7 (FloatField import verified — already used in `models/memory.py:87`, `models/knowledge_document.py:48`, `models/task_type_profile.py:74-75`).
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: true
- **Add `FloatField` to the existing `from popoto import (...)` block in `models/agent_session.py`** (the current import block includes `AutoKeyField, DatetimeField, DictField, Field, IndexedField, IntField, KeyField, ListField, Model, SortedField` but NOT `FloatField` — it must be added alphabetically between `Field` and `IndexedField`).
- Add the following fields to `AgentSession`:
  - `last_compaction_ts = FloatField(default=None)` — unix timestamp of most recent compaction
  - `compaction_count = IntField(default=0)` — total successful backups for this session
  - `compaction_skipped_count = IntField(default=0)` — total cooldown-suppressed PreCompact events
  - `nudge_deferred_count = IntField(default=0)` — total nudge ticks suppressed by the 30s guard
- Update the AgentSession docstring to describe each field's writer and reader (hook writes first three, executor writes `nudge_deferred_count`, output router reads `last_compaction_ts`).
- Verify with `grep -n 'FloatField' models/agent_session.py` — expected output: one import line + one field declaration line.

### 2. Implement pre_compact hook
- **Task ID**: build-pre-compact-hook
- **Depends On**: build-model-fields, spike-1a-empirical-flush (**MUST pass**)
- **Validates**: `tests/unit/hooks/test_pre_compact_hook.py` (create)
- **Informed By**: spike-1 (hook receives transcript_path), spike-1a (flush is complete OR fallback activated), B1 (session correlation via `claude_session_uuid`)
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- Extract `claude_session_uuid = input_data["session_id"]` at the top of the hook.
- Implement JSONL snapshot via `asyncio.to_thread(shutil.copy2, src, dst)` into `{transcript_parent}/backups/{claude_session_uuid}-{int(ts)}.jsonl.bak`.
- **If spike-1a reported partial-flush**: add stability-polling before the copy (re-read line count every 50ms until two consecutive reads match OR 500ms elapsed, whichever comes first).
- Look up AgentSession via `AgentSession.query.filter(claude_session_uuid=claude_session_uuid)` (B1 fix — NOT `filter(session_id=...)`); sort by `created_at` desc, take the first match. Write `last_compaction_ts` and bump `compaction_count` with `save(update_fields=["last_compaction_ts", "compaction_count"])`.
- Cooldown check: if the looked-up AgentSession's `last_compaction_ts` is within 300s, skip snapshot + save, log at info, and return `{}`.
- Retention: after successful write, keep last 3 backups per `claude_session_uuid`, unlink older.
- All side effects wrapped in `try/except Exception: logger.warning(...)`; hook always returns `{}`.

### 2b. Implement SDK-tick backstop in the executor
- **Task ID**: build-tick-backstop
- **Depends On**: build-model-fields
- **Validates**: `tests/unit/test_session_executor_tick_backstop.py` (create)
- **Informed By**: C1 (defense-in-depth for a missed PreCompact hook)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: true (with task 3)
- In `agent/session_executor.py`, inside the output-callback's per-tick bookkeeping block (at the top of each output-callback invocation, BEFORE `route_session_output` is called), add:
  - Read the currently-observed message count from the executor's existing in-memory state.
  - Read `session._last_observed_message_count` (default `None`).
  - If the prior count exists AND current count < prior count: backstop-detected compaction. Write `session.last_compaction_ts = time.time()` and `session.compaction_skipped_count += 1`, then `session.save(update_fields=["last_compaction_ts", "compaction_skipped_count"])`. Log at `warning`.
  - Always update `session._last_observed_message_count = current_count` for the next tick.
- The backstop does NOT attempt a recovery-path snapshot (per C1 — snapshot is hook-only).
- All side effects wrapped in `try/except Exception: logger.warning(...)`; backstop must never crash the executor.

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
- In `route_session_output()`, read `session.last_compaction_ts` from the AgentSession (which the route function already has in scope, keyed by `bridge_session_id`) and forward it to `determine_delivery_action`.
- Update the docstring's action list to document the new return value.

### 4. Wire defer branch into session executor
- **Task ID**: build-executor-defer
- **Depends On**: build-router-guard
- **Validates**: `tests/unit/test_session_executor_extraction_decoupling.py` (update + new test case)
- **Informed By**: B2 (the defer branch is a pure no-op, no `_enqueue_nudge` signature change)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- In `agent/session_executor.py` action-dispatch block (near lines 798-848), add `elif action == "defer_post_compact":` branch.
- Branch logs at info level the `last_compaction_ts` age, then returns from the output-callback invocation.
- **Branch does NOT call `_enqueue_nudge`** (B2 fix — the whole point is to suppress the nudge for this tick).
- **Branch does NOT set `chat_state.completion_sent = True`** (preserves the ability for real SDK output to route normally).
- Branch increments no counters.
- Do NOT add any `priority` kwarg to `_enqueue_nudge` — it has none and this plan does not add one.

### 5. Test suite
- **Task ID**: build-tests
- **Depends On**: build-pre-compact-hook, build-tick-backstop, build-router-guard, build-executor-defer
- **Assigned To**: test-writer-compaction
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/hooks/test_pre_compact_hook.py` covering: happy path, missing transcript (FileNotFoundError → debug log, not warning — C3), cooldown skip, retention pruning, exception swallowing.
- Create `tests/unit/test_output_router_compaction_guard.py` covering all last_compaction_ts branches.
- Create `tests/unit/test_session_executor_tick_backstop.py` covering C1 backstop branches.
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
| Model has consolidated counters (C4) | `grep -cE 'compaction_count\|compaction_skipped_count\|nudge_deferred_count' models/agent_session.py` | output >= 3 |
| FloatField imported (C7) | `grep -n 'FloatField' models/agent_session.py` | output >= 2 (one import, one field) |
| Tick backstop wired (C1) | `grep -n '_last_observed_message_count' agent/session_executor.py` | output > 0 |
| FileNotFoundError handled (C3) | `grep -n 'FileNotFoundError' agent/hooks/pre_compact.py` | output > 0 |
| No ad-hoc compaction Redis keys (C4) | `grep -rn "'compaction:" agent/ models/ worker/ bridge/ \| grep -v 'metrics:compaction:daily'` | output == 0 |
| B1 regression guard — hook uses claude_session_uuid | `grep -n 'claude_session_uuid' agent/hooks/pre_compact.py` | output > 0 |
| B1 regression guard — hook does NOT use session_id for lookup | `grep -n 'filter(session_id=' agent/hooks/pre_compact.py` | output == 0 |
| B2 regression guard — no priority kwarg added to _enqueue_nudge | `grep -n 'priority=' agent/session_executor.py \| grep -i 'defer\|nudge'` | no new `priority=` on `_enqueue_nudge` call sites beyond the existing 2 hardcodes |
| Spike-1a test exists | `test -f tests/integration/test_compaction_spike1a.py` | exit code 0 |
| Feature doc exists | `test -f docs/features/compaction-hardening.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Should the 30s nudge guard be configurable per session type?** The issue raised this; the plan currently uses a single 30s constant for all session types. PM sessions are the chattiest and most likely to race with compaction; Dev sessions rarely compact. Is a single constant acceptable for v1, or should we gate on `session_type` now?
2. **Should we add a nightly defense-in-depth cleanup for orphaned backups?** The hook's in-line retention is the primary mechanism; a nightly sweep over `~/.claude/projects/*/sessions/backups/` catches cases where retention itself silently failed. In-scope for this plan, or follow-up?
