# Compaction Hardening

JSONL backup, 5-minute cooldown, and 30-second post-compact nudge guard for Claude Code SDK context compaction events.

## Status

Shipped — issue [#1127](https://github.com/tomcounsell/ai/issues/1127).

## Problem

The Claude Code SDK silently compacts a session's conversation history when it approaches the context window limit. Two failure modes surfaced before this work:

1. **No JSONL backup before compaction.** A mid-compaction SDK crash left the session unrecoverable — the prior turn's working context was simply lost. The watchdog could only restart with zero history.
2. **No timing guard between `/compact` and the next nudge.** After compaction, `agent/session_executor.py`'s nudge path could fire "continue" within milliseconds. If that nudge interrupted the SDK's compaction write, the session entered an undefined state requiring a full restart.

Neither failure mode was observable: there was no `last_compaction_ts` anywhere on `AgentSession` to gate against or to count.

## Behavior

### 1. JSONL backup on every PreCompact event

`agent/hooks/pre_compact.py::pre_compact_hook` snapshots the on-disk JSONL transcript before the SDK rewrites it.

- **Trigger**: SDK fires the `PreCompact` hook with `{session_id, transcript_path, trigger, custom_instructions}`. Both `trigger=auto` (window full) and `trigger=manual` (operator `/compact`) are handled.
- **Snapshot**: `shutil.copy2(transcript_path, backup_path)` runs in `asyncio.to_thread` so it never blocks the SDK's event loop.
- **Backup path**: `{transcript_parent}/backups/{claude_session_uuid}-{int(utc_ts)}.jsonl.bak`. The Claude SDK's session UUID (passed in via `input_data["session_id"]`) is used in the filename so backups are immediately correlatable.
- **Recovery**: `claude --resume` can read any backup file directly. The recovery procedure is: copy the most recent `.jsonl.bak` over the live session file, then `claude --resume <session>`.

### 2. 5-minute per-session cooldown

A second `PreCompact` event for the same `claude_session_uuid` within `COMPACTION_COOLDOWN_SECONDS = 300` is a no-op:

- No second backup is written.
- `AgentSession.compaction_skipped_count` is incremented (observability).
- `AgentSession.last_compaction_ts` is NOT bumped — the previous backup's timestamp remains authoritative.
- The hook logs at INFO level with the age of the prior backup.

This prevents rapid compaction loops (which can produce stacked degraded summaries) from thrashing disk I/O.

### 3. Last-3 retention per session

After each successful snapshot, the hook scans `backups/{uuid}-*.jsonl.bak`, sorts by the filename-embedded integer timestamp descending, and unlinks index 3 onward. Three backups covers:

- The most recent compaction.
- The one before that, in case the most recent is itself corrupted.
- One safety margin.

Time-based TTL was rejected (see [spike-2 in the plan](../plans/completed/compaction-hardening.md)): backups for crashed sessions are already cleaned up by `cleanup --age 30`, so count-based retention loses no recovery capability.

### 4. 30-second post-compact nudge guard

`agent/output_router.py::determine_delivery_action` accepts a `last_compaction_ts: float | None` parameter. When set and within `POST_COMPACT_NUDGE_GUARD_SECONDS = 30` of `now`, it returns the new action `"defer_post_compact"` instead of any nudge action.

- The session executor's `"defer_post_compact"` branch (in `agent/session_executor.py`) is a pure no-op: it does NOT call `_enqueue_nudge`, does NOT set `chat_state.completion_sent = True`, and does NOT increment `auto_continue_count`.
- The next SDK idle tick naturally re-invokes the callback. If the 30s window has expired, the normal nudge flow fires; if real SDK output arrived first, it routes via `"deliver"`.
- The branch bumps `AgentSession.nudge_deferred_count` for observability.

The guard runs AFTER the terminal-status and `completion_sent` guards (a terminated session must exit cleanly even mid-compaction), but BEFORE all other classification (deferring is strictly less disruptive than any other action).

### 5. SDK-tick backstop for missed PreCompact hooks

`agent/session_executor.py::_tick_backstop_check_compaction` provides defense-in-depth when the PreCompact hook itself misfires (SDK internal error, hook deregistered by an unrelated path, or PreCompact firing too close to subprocess termination).

- The backstop watches for a *drop* in `ResultMessage.num_turns` across consecutive ticks. A drop is the SDK's observable signature of a compaction that rewrote conversation history.
- On detection, it arms `last_compaction_ts` and bumps `compaction_skipped_count` via a partial save so the 30s nudge guard fires on the next tick.
- The backstop does NOT take a JSONL snapshot — the hook is the only place snapshots are taken. Recovery from a backstop-detected miss is therefore best-effort (the guard fires, but no file is written).
- All failures are swallowed; the backstop must never crash the executor.

Turn counts are tracked in-memory by `agent/sdk_client.py::record_turn_count`, called when each `ResultMessage` arrives.

## State Fields

Four new fields on `AgentSession`:

| Field | Type | Writer | Reader |
|-------|------|--------|--------|
| `last_compaction_ts` | `FloatField` (Unix ts) | `pre_compact_hook` (primary), `_tick_backstop_check_compaction` (defense-in-depth) | `determine_delivery_action` |
| `compaction_count` | `IntField` (default 0) | `pre_compact_hook` | dashboard / post-hoc analysis |
| `compaction_skipped_count` | `IntField` (default 0) | `pre_compact_hook` cooldown path, backstop | dashboard |
| `nudge_deferred_count` | `IntField` (default 0) | `defer_post_compact` action branch | dashboard |

All writes use `save(update_fields=[...])` (Popoto partial save) so they never clobber concurrent writes to other fields. This is the same idiom used by `nudge-stomp-append-event-bypass.md` (issue #898).

## Failure Modes

The hook's top-level contract is **"never raise, always return `{}`"**:

- Every side-effectful call (`shutil.copy2`, `AgentSession.query.filter`, `AgentSession.save`, `os.scandir`, `os.unlink`) is wrapped in `try/except Exception:` + `logger.warning(...)`.
- `FileNotFoundError` on the source transcript is treated as an expected condition (brand-new session, path race, non-Valor session) and logs at DEBUG (not WARNING).
- An empty `transcript_path` or empty `session_id` short-circuits with a WARNING and returns `{}`.
- A failed cooldown write does NOT block the snapshot; the snapshot already landed.
- A failed retention prune does NOT raise; orphaned backups are caught by `cleanup --age 30` defense-in-depth.

## Cross-References

- Plan: [`docs/plans/completed/compaction-hardening.md`](../plans/completed/compaction-hardening.md)
- Issue: [#1127](https://github.com/tomcounsell/ai/issues/1127)
- Hook: `agent/hooks/pre_compact.py`
- Router guard: `agent/output_router.py::determine_delivery_action` (`last_compaction_ts` parameter, `"defer_post_compact"` action)
- Executor branch: `agent/session_executor.py::_tick_backstop_check_compaction` and the `"defer_post_compact"` action handler
- SDK turn-count tracking: `agent/sdk_client.py::record_turn_count` / `get_turn_count` / `clear_turn_count`
- Model: `models/agent_session.py::AgentSession` (search "Compaction hardening fields")
- Tests:
  - `tests/unit/hooks/test_pre_compact_hook.py` — hook backup/cooldown/retention/exception swallowing
  - `tests/unit/test_output_router_compaction_guard.py` — None / fresh / stale / boundary branches
  - `tests/integration/test_compaction_hardening.py` — end-to-end hook → AgentSession → router defer

## Related Features

- **[Bridge Self-Healing](bridge-self-healing.md)** — the watchdog escalation path is the recovery layer above this hook. Compaction hardening reduces the frequency of escalations by preserving session state across SDK crashes.
- **[Stop Hook JSONL Backup](agent-message-delivery.md)** — sibling pattern: the Stop hook also opens `transcript_path` and reads the JSONL. The PreCompact hook borrowed the same approach for snapshots.
- **[Externalized Session Steering](session-steering.md)** — the `output_router.py` extraction (issue #743) made the 30s guard possible by turning `determine_delivery_action` into a pure function.

## Open Items

The plan documented three open questions with deliberate answers shipped here:

1. **Per session-type guard tuning?** A single 30s constant covers PM/Teammate/Dev. Differentiated tuning is deferred until production data shows a real difference.
2. **Nightly defense-in-depth backup cleanup?** Out of scope for this plan. In-line retention (last 3 per session UUID) plus existing `cleanup --age 30` are sufficient for current volume.
3. **Defer enforcement: re-enqueue vs in-place sleep?** Re-enqueue (no nudge call, no `completion_sent` flip, no auto-continue increment). The next SDK tick naturally re-invokes the callback. Inline `asyncio.sleep(30)` was rejected because it would starve concurrent sessions on the same event loop.
