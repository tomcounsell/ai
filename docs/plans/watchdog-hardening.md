---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-04-23
tracking: https://github.com/tomcounsell/ai/issues/1128
last_comment_id:
---

# Watchdog Hardening — Idle Probe, Loop-Break Steering, Per-Session Token Tracking

## Problem

The session watchdog (`monitoring/session_watchdog.py`) and bridge watchdog (`monitoring/bridge_watchdog.py`) catch many failure modes today — crashes, silence-based abandonment, stall detection via dual heartbeats. Three gaps remain that let unproductive sessions linger and burn quota:

**Current behavior:**

1. **Silent idle-connection death is undetected.** Anthropic's SDK connection dies silently after ~48h of idle (confirmed via amux.io fleet-operations research; see #1104). Heartbeats track process activity, but nothing proactively pings dormant sessions to test freshness. A session waiting 2+ days on a human reply may be completely non-functional when resumed.

2. **Stuck-agent loops are detected but not acted on.** `monitoring/session_watchdog.py:471–574` detects repetition (≥5 identical consecutive tool calls via `detect_repetition`) and error cascades (≥5 errors in last 20 calls via `detect_error_cascade`), but results are logged only. The steering queue (`agent/steering.py` + `AgentSession.queued_steering_messages`) is not wired to watchdog output, so nothing breaks the loop. A stuck loop can burn 10× the tokens of a successful run (#1105).

3. **Per-session token spend is invisible.** The SDK emits `ResultMessage.usage` and `ResultMessage.total_cost_usd` on each turn (see `agent/sdk_client.py:1233`), and analytics records `session.cost_usd` / `session.turns` metrics, but nothing aggregates per-session input + output tokens into the `AgentSession` record or surfaces it on `/dashboard.json`. Operators cannot see which session is spending the most, and there is no threshold to trigger intervention.

**Desired outcome:**

- Long-dormant sessions are proactively probed before the 48h silent-death window.
- When repetition or error-cascade fires, the watchdog automatically enqueues a targeted steering message (with cooldown) instead of just logging.
- Per-session token spend is tracked on `AgentSession`, surfaced on `/dashboard.json`, and crosses a soft threshold that triggers a human alert or steering nudge.

## Freshness Check

**Baseline commit:** `ceedbe68b76337baa317a719ef217e13f3b82852`
**Issue filed at:** `2026-04-22T17:00:16Z` (~22h before plan time)
**Disposition:** Unchanged

**File:line references re-verified:**
- `monitoring/session_watchdog.py:471` — `detect_repetition` still at cited line, signature unchanged; consumed at line 403, logged at 404 but not acted on. Confirmed passive.
- `monitoring/session_watchdog.py:525` — `detect_error_cascade` still present; consumed at line 408, logged at 411, passive.
- `agent/session_health.py:127` — `STALL_THRESHOLD_ACTIVE=600s` heartbeat wiring still in place; Tier-1 dual-heartbeat detector at line 412+ verified.
- `agent/steering.py` — module present; `push_steering_message`, `pop_all_steering_messages`, `clear_steering_queue` all exposed. `AgentSession.queued_steering_messages` at `models/agent_session.py:196`, helpers at 1376–1405.
- `agent/sdk_client.py:1216–1255` — `ResultMessage` handling present; `total_cost_usd` recorded to analytics; SDK `ResultMessage` exposes `.usage` field (verified via `dataclasses.fields(ResultMessage)` → includes `usage`, `model_usage`, `total_cost_usd`).

**Cited sibling issues/PRs re-checked:**
- #1104 — CLOSED 2026-04-22 (superseded by #1128, no fix merged).
- #1105 — CLOSED 2026-04-22 (superseded by #1128, no fix merged).
- #1036 — dual-heartbeat OR detection (merged) — already shipped; this plan builds on it but does NOT touch Tier-1 heartbeat logic.
- #360 — transcript liveness fallback (merged) — already shipped; referenced in watchdog.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since="2026-04-22T17:00:16Z" -- monitoring/session_watchdog.py monitoring/bridge_watchdog.py agent/session_health.py agent/steering.py models/agent_session.py` returns empty.

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/session-watchdog-reliability.md` (already completed / feature doc exists at `docs/features/session-watchdog-reliability.md`) — predecessor work. No active conflict.
- `docs/plans/agent-session-field-cleanup.md`, `docs/plans/worker-lifecycle-cleanup.md` — touch `AgentSession` fields but not the three this plan adds. Cross-check field names before committing to avoid collisions.

**Notes:** All issue claims hold against current code. No drift. Proceeding.

## Prior Art

- **#1036** — Dual-heartbeat OR detection (CLOSED, merged). Shipped `last_heartbeat_at` + `last_sdk_heartbeat_at` fields on `AgentSession`. This plan's idle-probe extends the same persistence pattern.
- **#360** — Transcript liveness fallback (CLOSED, merged). Introduced `_check_transcript_liveness` using mtime of `logs/sessions/{id}/transcript.txt`. Informs our approach: filesystem/Redis signals are preferred over synchronous API calls for liveness.
- **#1104** — Claude API 48h idle death (CLOSED 2026-04-22, superseded by #1128). Investigation-only, no fix proposed beyond "detect and restart before 48h." This plan implements.
- **#1105** — Stuck-agent loop detection (CLOSED 2026-04-22, superseded by #1128). Investigation-only. Noted that `valor-session steer` path exists — this plan wires the watchdog to use it.
- **#773** — CircuitBreaker + reflection scheduler (CLOSED). Governs queue-level throttling. Not directly overlapping but establishes the pattern of using signals to modulate scheduler behavior.
- **#440** — Session watchdog observer fixes (completed). Stabilized detection signals; informs the consumer side.

## Research

No external library research needed — this work uses the existing Claude Agent SDK, Redis-backed Popoto models, and internal `agent/steering.py`. All reference material is in-repo. No dependency upgrades.

Skipping WebSearch — the problem is purely about wiring already-identified signals to already-existing actuators (steering queue, `AgentSession` fields, dashboard).

## Spike Results

### spike-1: Token source in SDK ResultMessage
- **Assumption**: "`ResultMessage` exposes per-turn input/output token counts that can be aggregated into `AgentSession`."
- **Method**: code-read
- **Finding**: Confirmed. `ResultMessage` fields include `usage`, `model_usage`, `total_cost_usd`, `duration_ms`, `num_turns`. `usage` is a dict-like with `input_tokens`, `output_tokens`, and typically `cache_read_input_tokens`, `cache_creation_input_tokens`. `total_cost_usd` is already captured at `agent/sdk_client.py:1275`.
- **Confidence**: high
- **Impact on plan**: Token aggregation from the SDK path hooks into the existing `ResultMessage` handler in `sdk_client.py`. Adds 4 numeric fields to `AgentSession`: `total_input_tokens`, `total_output_tokens`, `total_cache_read_tokens`, `total_cost_usd`. **However, SDK path is only one of two execution paths — see spike-5 for the harness path.**

### spike-2: Where does the 48h silent-death risk actually apply? (Execution path audit)
- **Assumption**: "Dormant sessions hold live SDK connections that die silently after 48h."
- **Method**: code-read
- **Finding**: **The premise is path-specific.** Two distinct execution paths exist:
  - **SDK path** (`ClaudeSDKClient` async context): populates `_active_clients: dict[str, ClaudeSDKClient]` at `agent/sdk_client.py:58`, entered at line 1233, popped at line 1420. This path holds a persistent connection across turns. Used for interactive chat sessions.
  - **Harness path** (`_run_harness_subprocess` at `agent/sdk_client.py:1786`): spawns `claude -p stream-json` as a short-lived subprocess **per turn**, parses the result event, exits. Used by `agent/session_executor.py:1311` and `agent/session_completion.py:450` — this is the PM/Dev/Teammate session path.
  - Call sites: `get_response_via_harness` is imported in `agent/__init__.py:36`, `agent/session_executor.py:1247`, `agent/session_completion.py:448`. `get_response_via_sdk` is used for chat routes.
  - The 48h silent-death problem **only applies to the SDK path** (persistent `ClaudeSDKClient`). Harness-path sessions tear their subprocess down after every turn; there is no long-lived connection to go stale.
- **Confidence**: high
- **Impact on plan**: Idle-teardown targets the SDK path only. The `_active_clients` registry is **worker-process-local**, so **the watchdog process cannot reach it**. Moved idle-teardown from `monitoring/session_watchdog.py` (separate process) into a **worker-internal idle-sweeper task** that runs alongside the session execution loop in the worker process. The watchdog process does not participate. For harness-path sessions, idle-teardown is a no-op (nothing to tear down).

### spike-3: Watchdog → steering queue wiring cost
- **Assumption**: "Wiring `session_watchdog.py` detections to `push_steering_message` is a localized change requiring no new abstractions."
- **Method**: code-read
- **Finding**: Confirmed. `detect_repetition` and `detect_error_cascade` return `(bool, ...)` tuples. Inside the `_check_session_health` loop in `session_watchdog.py`, the watchdog already calls both at lines 403 and 408. Adding a `push_steering_message(session_id, text, sender="watchdog")` call in the positive branch is a ~15-line diff per detection. Cooldown requires a per-session Redis key (e.g., `watchdog:steer_cooldown:{session_id}`) checked before pushing.
- **Confidence**: high
- **Impact on plan**: No abstraction layer. Cooldown implemented as a Redis key with TTL. Watchdog tick is 300s (`WATCHDOG_INTERVAL`), so a cooldown TTL of 900s (3 ticks) prevents flooding without being so long that a genuinely recurring loop is missed.

### spike-4: Does `dashboard.json` already expose per-session fields?
- **Assumption**: "`/dashboard.json` via `_session_to_json` can accept new token fields without major refactoring."
- **Method**: code-read
- **Finding**: Confirmed. `_session_to_json` in `ui/app.py:244` serializes `PipelineProgress` objects. Adding new fields requires: (a) add to `PipelineProgress` dataclass in `ui/data/sdlc.py`, (b) populate from `AgentSession` in the reader, (c) add to `_session_to_json`. Three-step, all localized.
- **Confidence**: high
- **Impact on plan**: Dashboard exposure is a small, purely additive change. Does not break the existing API contract.

### spike-5: Does the harness `result` JSON event carry usage + cost?
- **Assumption**: "The `result` event emitted by `claude -p stream-json` contains `usage` (input/output/cache tokens) and `total_cost_usd`, allowing token accumulation without touching the in-memory `ResultMessage` path."
- **Method**: code-read of `_run_harness_subprocess` + Claude CLI stream-json format inspection
- **Finding**: Confirmed. `_run_harness_subprocess` at `agent/sdk_client.py:1855-1862` already consumes the `event_type == "result"` event but extracts only `result` and `session_id`; the event payload from `claude -p` also includes sibling fields `usage: {input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens}`, `total_cost_usd`, `num_turns`, `duration_ms`, and `duration_api_ms` (this is the same schema the SDK `ResultMessage` wraps). The extraction is a ~3-line addition: read the fields off `data` alongside `result_text` and `session_id_from_harness`, then return them from `_run_harness_subprocess` / `get_response_via_harness` for the caller to accumulate.
- **Confidence**: high
- **Impact on plan**: Token accumulation must hook **both** paths. SDK path: existing `ResultMessage` handler (unchanged from spike-1). Harness path: extract from `result` event inside `_run_harness_subprocess`; pass through `get_response_via_harness` return shape; call `accumulate_session_tokens` from the harness-path callers (`session_executor.py`, `session_completion.py`). Without this, harness-path token counters would always be 0 — the original design was broken for the production PM/Dev path.

### spike-6: Post-fix schema for `get_response_via_harness` return type
- **Assumption**: "We can extend `get_response_via_harness` to return `(text, usage_info)` without breaking the ~20 callers listed in Grep."
- **Method**: code-read of call sites
- **Finding**: All production call sites treat the return value as a plain `str` (raw = await get_response_via_harness(...)). Changing the signature to return a tuple would break every caller. **Preferred approach**: keep the `str` return, but capture `session_id` on the helper call and accumulate tokens via a **side-effect call** inside `get_response_via_harness` itself — invoked just before return when `session_id` was provided. This matches the existing side-effect pattern for `_store_claude_session_uuid(session_id, session_id_from_harness)` at `sdk_client.py:1778`.
- **Confidence**: high
- **Impact on plan**: No call site changes needed for harness-path token tracking. The helper absorbs the accumulation as a side effect keyed on the `session_id` it already receives.

## Data Flow

### Token tracking (two paths — both write to the same AgentSession fields)

**Path A — SDK path** (`get_response_via_sdk`, interactive/chat sessions):
1. Claude Agent SDK `ResultMessage` arrives at the handler in `agent/sdk_client.py:1251`.
2. Existing code at line 1275 already reads `msg.total_cost_usd`. We extend to read `msg.usage.input_tokens`, `msg.usage.output_tokens`, `msg.usage.cache_read_input_tokens` (all safe via `getattr(msg.usage, <name>, 0) or 0` for defensive defaults).
3. Call new helper `accumulate_session_tokens(session_id, input_tokens, output_tokens, cache_read_tokens, cost_usd)`.

**Path B — Harness path** (`get_response_via_harness`, production PM/Dev/Teammate sessions):
1. `_run_harness_subprocess` at `agent/sdk_client.py:1855` consumes `event_type == "result"`. Extend the handler to also read `data.get("usage")`, `data.get("total_cost_usd")` off the same JSON object (alongside `result` and `session_id`).
2. Thread `usage` and `total_cost_usd` back up through `_run_harness_subprocess` return (extend from `(result_text, session_id_from_harness, returncode)` to `(result_text, session_id_from_harness, returncode, usage, cost_usd)`; internal-only, no external caller of this private helper).
3. Inside `get_response_via_harness`, call `accumulate_session_tokens(session_id, ...)` **as a side effect** after the stale-UUID fallback branches settle — matching the existing `_store_claude_session_uuid` pattern at `sdk_client.py:1778`. Public callers (`session_executor.py`, `session_completion.py`) are unchanged; return signature stays `str`.

**Shared tail (both paths):**
4. `accumulate_session_tokens` performs a single `AgentSession` update: `total_input_tokens += …`, `total_output_tokens += …`, `total_cache_read_tokens += …`, `total_cost_usd += …`.
5. **Redis write**: Popoto `save(update_fields=[…])` with the four fields. Fail-quiet on `ModelException` (same pattern as existing heartbeat writes). Gated by `WATCHDOG_TOKEN_TRACKING_ENABLED`.
6. **Read path — dashboard**: `ui/data/sdlc.py` loads `AgentSession`, populates `PipelineProgress.total_input_tokens` etc., `_session_to_json` includes in response. `/dashboard.json` response includes four new fields per session, always present (default 0) for forward-compat with existing JSON consumers.
7. **Threshold check**: In the watchdog's per-session loop, if `total_input_tokens + total_output_tokens >= TOKEN_ALERT_THRESHOLD` (default 5_000_000) AND session status is `running` AND no alert has fired in the last 3600s (cooldown), push a steering message. The watchdog only READS token counts here — it never writes them.

### Idle teardown (worker-internal; watchdog process does NOT participate)

The `_active_clients` registry lives in the **worker process** memory at `agent/sdk_client.py:58`. The session watchdog runs as a **separate process** and cannot reach that dict. So idle-teardown runs as a worker-internal task, co-located with the registry.

1. **Trigger**: New `worker/idle_sweeper.py` async task, scheduled every `IDLE_SWEEP_INTERVAL` (default 1800s = 30 min) on the worker event loop.
2. **Filter**: For each entry in `list(_active_clients.items())` (snapshot, not live iteration), look up the matching `AgentSession`. Teardown target: `status in {dormant, paused, paused_circuit}` AND `updated_at` (or `last_activity_ts` if populated by this plan) older than `IDLE_TEARDOWN_THRESHOLD` (default 86400s = 24h). Do NOT tear down `running`, `pending`, or any active status.
3. **Action**: Call `client.close()` (idempotent; wrapped in try/except). Pop the session_id from `_active_clients`.
4. **Record**: Set `AgentSession.sdk_connection_torn_down_at = now()`.
5. **Resume semantics**: On next query, `get_response_via_sdk` enters its `async with ClaudeSDKClient(...)` block, repopulates `_active_clients`, and re-establishes context from `claude_session_uuid` via the existing `--resume` plumbing. The teardown is **safe on resume** — the SDK rebuilds state from the persisted UUID.
6. **Harness-path sessions**: No-op. Nothing is in `_active_clients` for harness sessions because the subprocess exits after every turn.
7. Gated by `WATCHDOG_IDLE_TEARDOWN_ENABLED` (default `true`).

### Loop-break steering
1. **Trigger**: `detect_repetition` returns `(True, tool_name, count)` OR `detect_error_cascade` returns `(True, error_count)`.
2. **Cooldown check (atomic)**: Redis `SET watchdog:steer_cooldown:<reason>:<session_id> "1" NX EX 900` — the `NX` (set-if-not-exists) + `EX` (TTL) combine into **one** atomic command. If the SET returns truthy, the cooldown slot was open and we may proceed. If it returns falsy (i.e., key already present), a prior tick holds the cooldown; skip. This eliminates the read-then-write race entirely. Keys are keyed by **reason** so `repetition` and `error_cascade` have independent cooldowns and do not squelch each other. The `<reason>` segment is one of `repetition`, `error_cascade`, `token_alert`.
3. **Steering push**: Compose a targeted message:
   - Repetition: `"Stop and re-check the task — you appear to be repeating the same tool call ({tool_name}) {count} times. Summarize what you've tried, then try a different approach."`
   - Error cascade: `"Stop — you've hit {error_count} errors in the last 20 operations. Summarize the failure pattern and pause for human input rather than continuing blind."`
   - Token alert: `"Token budget exceeded: ${cost_usd:.2f} spent this session. Stop and summarize what you've done."`
4. **Delivery**: `push_steering_message(session_id, text, sender="watchdog")`. The **`sender="watchdog"` tag is mandatory** — it distinguishes these messages from human steers so the PM session, the dashboard, and `valor-session status` can render them distinctly. `agent/steering.py::push_steering_message` already accepts a `sender` kwarg and stores it on the queued message envelope (verified by code-read).
5. **Delivery timing**: The existing PostToolUse hook at `.claude/hooks/hook_utils/memory_bridge.py` (and the SDK PM's turn-boundary drain in `agent/session_executor.py`) drains `queued_steering_messages` before the next tool call. The loop-break steer therefore arrives **at the next tool-call boundary** — it does NOT interrupt mid-tool execution, but it DOES arrive before the next repetition of the stuck tool. Operators should expect a one-tool-call delay between detection and correction; this is acceptable because stuck loops emit many tool calls per minute.
6. **Logging**: `logger.warning("[watchdog] Loop-break steer injected for %s: %s", session_id, reason)` — visible in `logs/worker.log`.

### Pricing constants (explicit — no implicit scaling)

Token accumulation sums raw counts only. `total_cost_usd` is taken verbatim from the SDK/CLI (`msg.total_cost_usd` or the harness `result` event's `total_cost_usd` field), **not** recomputed from token counts. This avoids model-pricing drift: if Anthropic changes Sonnet rates, Claude Code's own `total_cost_usd` stays authoritative and our accumulator stays correct without a code change.

**Rationale for NOT maintaining a price table in this repo:**
- The CLI and SDK emit `total_cost_usd` per turn — single source of truth.
- Hard-coded per-model rates drift silently when Anthropic updates pricing.
- The watchdog threshold alert uses the accumulated `total_cost_usd` directly (or falls back to the raw token sum when cost is missing, e.g., `total_cost_usd is None`).

If a future diagnostic panel wants per-model breakdowns, it will use `msg.model_usage` (already in the SDK `ResultMessage`) rather than a local price table. Out of scope for this plan (No-Gos).

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1104 (investigation-only) | Flagged 48h idle SDK death from fleet-operations research | Never produced a fix — closed as "superseded by #1128." |
| #1105 (investigation-only) | Flagged stuck-agent loops + suggested steering-based intervention | Never wired up — detections remained passive, closed as "superseded by #1128." |

**Root cause pattern:** The system ships detections faster than it ships actuators. `detect_repetition` and `detect_error_cascade` landed with #440; the steering queue landed with #743; but the two were never connected. This plan closes the loop: detection → actuator, with cooldown to prevent amplification.

## Architectural Impact

- **New dependencies**: None. All building blocks exist (Popoto, Redis, SDK, steering queue).
- **Process topology**:
  - **Worker process** (`python -m worker`) owns: `_active_clients` registry, token accumulation (both SDK + harness paths), idle-sweeper task.
  - **Session-watchdog process** (`monitoring/session_watchdog.py`) owns: `detect_repetition`, `detect_error_cascade`, loop-break steering push, token-threshold alert push. Reads `AgentSession.total_input_tokens` etc. to decide; never writes them.
  - The two processes communicate **only through Redis** — via `AgentSession` fields and the steering queue. No in-process dicts shared across process boundaries.
- **Interface changes**:
  - `AgentSession` gains 5 fields: `total_input_tokens`, `total_output_tokens`, `total_cache_read_tokens`, `total_cost_usd`, `sdk_connection_torn_down_at`.
  - `agent/sdk_client.py` gains `accumulate_session_tokens(...)` helper (public-to-module, called from SDK-path `ResultMessage` handler and from `get_response_via_harness`).
  - `agent/sdk_client.py::_run_harness_subprocess` signature grows: return tuple adds `usage` + `cost_usd` (private helper, internal to the file, safe to change).
  - `worker/idle_sweeper.py` — new module hosting `run_idle_sweep()` async task, started by `worker/__main__.py`'s startup routine.
  - `monitoring/session_watchdog.py` gains `_inject_watchdog_steer(session_id, reason, message)` helper (private). A single helper serves repetition, error-cascade, and token-alert triggers, keyed by `reason` in the cooldown.
  - `_session_to_json` (dashboard) gains 4 token fields in output.
- **Coupling**:
  - `monitoring/session_watchdog.py` → `agent/steering.py`: new arrow. Not a layering violation — `monitoring/` already imports `models/`, and `agent/steering.py` is a Redis-only module. No circular-import risk.
  - `worker/idle_sweeper.py` → `agent/sdk_client._active_clients`: new arrow, same process — safe.
- **Data ownership**: Token counts are owned by `AgentSession`. Writers: SDK-path `ResultMessage` handler + harness-path extraction (both in the worker process; effectively one writer per session at a time because sessions are serialized through the worker queue). Readers: watchdog (threshold alert) and dashboard. No cross-writer contention.
- **Reversibility**: High. Token fields are additive and default to 0. Loop-break steering gated behind `WATCHDOG_AUTO_STEER_ENABLED` (default on). Token tracking gated by `WATCHDOG_TOKEN_TRACKING_ENABLED` (default on). Idle teardown gated by `WATCHDOG_IDLE_TEARDOWN_ENABLED` (default on). Flipping any flag off disables that feature without rollback.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (one after idle-probe spike resolution, one at PR review)
- Review rounds: 1 (standard /do-pr-review)

Medium because three related-but-separable subsystems touch the same files, and the token-spend thresholding needs tuning under observation. Not Large because each subsystem is <200 LoC and all plumbing (steering, heartbeats, dashboard serialization) already exists.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | Steering queue + cooldown keys |
| Popoto AgentSession model loads | `python -c "from models.agent_session import AgentSession; assert AgentSession"` | Token field additions |
| Claude Agent SDK imports cleanly | `python -c "from claude_agent_sdk import ResultMessage; import dataclasses; assert 'usage' in [f.name for f in dataclasses.fields(ResultMessage)]"` | `ResultMessage.usage` source for tokens |

Run all checks: `python scripts/check_prerequisites.py docs/plans/watchdog-hardening.md`

## Solution

### Key Elements

- **Token accumulator (both paths)**: Single helper `accumulate_session_tokens(session_id, input_tokens, output_tokens, cache_read_tokens, cost_usd)` in `sdk_client.py`. Called from (a) the existing SDK `ResultMessage` handler and (b) `get_response_via_harness` after the harness subprocess returns. Persists to `AgentSession` via `save(update_fields=[…])`. Fail-quiet on `ModelException`.
- **Dashboard surfacing**: Extend `PipelineProgress` + `_session_to_json` to include four token fields. Defaults to 0 (never None) for forward-compat.
- **Token-threshold alert (watchdog)**: Watchdog tick reads `AgentSession.total_input_tokens + total_output_tokens` (Redis read, no join). If sum >= `TOKEN_ALERT_THRESHOLD` AND status == `running` AND cooldown open, steer via `_inject_watchdog_steer`. Watchdog never writes token fields — read-only.
- **Loop-break steering (watchdog)**: Single helper `_inject_watchdog_steer(session_id, reason, message)` used for all three trigger reasons (`repetition`, `error_cascade`, `token_alert`). Uses atomic `SET NX EX` for cooldown (see Technical Approach for exact ordering). Calls `push_steering_message(session_id, message, sender="watchdog")`.
- **Idle teardown (worker-internal)**: New `worker/idle_sweeper.py` module with an async task started from `worker/__main__.py`. Runs every `IDLE_SWEEP_INTERVAL` (default 1800s). Reads `list(_active_clients.items())` snapshot, filters to dormant+24h, calls `client.close()`, records `sdk_connection_torn_down_at`. Watchdog process does NOT touch `_active_clients`.

### Flow

**SDK path session completes a turn** → SDK emits `ResultMessage` → worker captures `msg.usage.*` + `msg.total_cost_usd` → calls `accumulate_session_tokens(...)` → AgentSession updated → dashboard reflects on next render.

**Harness path session completes a turn** → `claude -p stream-json` subprocess emits `result` event → `_run_harness_subprocess` extracts `usage` + `total_cost_usd` from the event JSON → returns them → `get_response_via_harness` calls `accumulate_session_tokens(...)` before returning the result string → AgentSession updated.

**Session enters stuck loop** → watchdog tick (every 300s) calls `detect_repetition` → returns True → `_inject_watchdog_steer(sid, "repetition", msg)` → `SET watchdog:steer_cooldown:repetition:<sid> "1" NX EX 900` → if OK (cooldown slot open), `push_steering_message(sid, msg, sender="watchdog")` → PostToolUse hook drains queue before next tool call → session receives correction → loop breaks.

**Session token spend crosses 5M** → watchdog tick reads `total_input_tokens + total_output_tokens >= 5_000_000` on a `running` session → `_inject_watchdog_steer(sid, "token_alert", msg)` → `SET watchdog:steer_cooldown:token_alert:<sid> ... NX EX 3600` → push steer → operator sees the nudge in session history.

**Session goes dormant with an open SDK client** → worker idle-sweeper tick (every 1800s) → iterates `list(_active_clients.items())` snapshot → finds entry whose `AgentSession.status in {dormant, paused, paused_circuit}` AND `updated_at > 24h` → `client.close()` (idempotent) → pop from registry → `AgentSession.sdk_connection_torn_down_at = now()` → on resume, fresh SDK client is built via existing `--resume` + stored UUID → no 48h silent death.

### Technical Approach

**Module layout (no new files outside those listed):**
- `agent/sdk_client.py` — add `accumulate_session_tokens`; extend `_run_harness_subprocess` return tuple; add accumulate call site inside `get_response_via_harness`; extend the existing `ResultMessage` handler. Keep `_active_clients` registry — it already exists.
- `worker/idle_sweeper.py` — **new module** hosting `run_idle_sweep()` async coroutine + constants.
- `worker/__main__.py` — wire `run_idle_sweep()` as a background task alongside the existing session loop.
- `monitoring/session_watchdog.py` — add `_inject_watchdog_steer` + call sites at the detection branches + token-threshold check in the per-session loop.
- `ui/data/sdlc.py` + `ui/app.py` — add token fields to `PipelineProgress` + `_session_to_json`.
- `models/agent_session.py` — add five new fields.

**Token accumulation safety:**
- Synchronous with `save(update_fields=[...])` — Popoto HMSET is sub-ms; no hot-path latency concern.
- Wrap in `try/except ModelException` and `try/except Exception` at the outermost layer; log at WARNING with session_id; never raise into the SDK query path or harness return path.
- If `WATCHDOG_TOKEN_TRACKING_ENABLED=false`, early-return from the helper with no side effects.
- Guard against `msg.usage is None` (some SDK versions or error paths omit it): `getattr(msg.usage, "input_tokens", 0) or 0`.

**Cooldown — atomic ordering (explicit):**
```python
# Correct: SET key value NX EX ttl — atomic set-if-not-exists with TTL
# Popoto/redis-py:
cooldown_slot_open = POPOTO_REDIS_DB.set(
    f"watchdog:steer_cooldown:{reason}:{session_id}",
    "1",
    nx=True,
    ex=cooldown_ttl_seconds,
)
# cooldown_slot_open is truthy when the key was absent and is now set;
# falsy when the key already existed (cooldown still active).
if not cooldown_slot_open:
    return  # cooldown in effect
```
- **Never** sequence a separate GET then SET — that's racy under concurrent watchdog ticks or watchdog+worker co-triggered alerts. The `nx=True, ex=TTL` kwargs order is Redis-native single-command `SET` with both flags.
- Cooldown key includes `reason`: `watchdog:steer_cooldown:repetition:<sid>`, `watchdog:steer_cooldown:error_cascade:<sid>`, `watchdog:steer_cooldown:token_alert:<sid>`. Three independent cooldowns; a steer of one reason does not suppress another reason.

**Idle teardown — status filter (explicit):**
- Target statuses: **`dormant`, `paused`, `paused_circuit`** (all three included explicitly — `paused` and `paused_circuit` are semantic cousins of `dormant` under the 13-state reference at `docs/features/session-lifecycle.md`).
- Excluded: `running`, `pending`, `waiting_for_children`, `superseded`, and terminal states (`completed`, `killed`, `abandoned`, `failed`).
- The filter is enforced in `worker/idle_sweeper.py::run_idle_sweep` before any `client.close()` call. The `_active_clients` dict is iterated as a snapshot (`list(...)`) to tolerate concurrent modification from active queries.

**Steering attribution — sender visibility:**
- All watchdog-originated steers pass `sender="watchdog"`. Verified present in `agent/steering.py::push_steering_message` signature (code-read); the sender is persisted on the queued message envelope.
- Downstream visibility:
  - `valor-session status --id <sid>` already renders per-message sender (verified in `tools/valor_session.py`).
  - Dashboard JSON includes sender on queued-steering entries (already present).
  - The PM session's steering-drain loop logs `[steering] received from sender=<s>` so the PM can distinguish `watchdog` from `human` and respond accordingly (already present in `agent/session_executor.py`).

**Thresholds + env-var gates:**
- `TOKEN_ALERT_THRESHOLD = int(os.getenv("WATCHDOG_TOKEN_ALERT_THRESHOLD", "5000000"))` — 5M combined input+output tokens.
- `TOKEN_ALERT_COOLDOWN = int(os.getenv("WATCHDOG_TOKEN_ALERT_COOLDOWN", "3600"))` — one alert per hour per session.
- `STEER_COOLDOWN = int(os.getenv("WATCHDOG_STEER_COOLDOWN", "900"))` — 15 min between repeat loop-break steers.
- `IDLE_TEARDOWN_THRESHOLD = int(os.getenv("WATCHDOG_IDLE_TEARDOWN_THRESHOLD_SECONDS", "86400"))` — 24h dormant + has live client → tear down.
- `IDLE_SWEEP_INTERVAL = int(os.getenv("WATCHDOG_IDLE_SWEEP_INTERVAL", "1800"))` — 30 min between sweeps.
- Feature gates: `WATCHDOG_AUTO_STEER_ENABLED`, `WATCHDOG_TOKEN_TRACKING_ENABLED`, `WATCHDOG_IDLE_TEARDOWN_ENABLED` — all default `true`. Reading each returns `false` only if env var is exactly `"0"`, `"false"`, or `"no"` (case-insensitive).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `accumulate_session_tokens` wraps save in try/except ModelException (inner) + try/except Exception (outer) — test: simulate concurrent save conflict, assert logger.warning fires AND SDK / harness return path continues without raising.
- [ ] `_inject_watchdog_steer` wraps `push_steering_message` in try/except — test: simulate Redis unavailable, assert watchdog tick continues and logs WARNING, does not crash the watchdog loop.
- [ ] `worker/idle_sweeper._sweep_once` wraps `client.close()` in try/except — test: simulate client already closed, assert no exception propagates and the next entry still processes.
- [ ] `_run_harness_subprocess` tolerates a `result` event with missing/null `usage` or `total_cost_usd` — the usage extraction falls back to `None` safely; downstream `accumulate_session_tokens(... None ...)` no-ops gracefully.
- [ ] No new `except Exception: pass` — every handler must log at WARNING or ERROR with session_id.

### Empty/Invalid Input Handling
- [ ] `accumulate_session_tokens` called with `usage=None` (older SDK version or error message) → no-op, no crash.
- [ ] `accumulate_session_tokens` called with missing sub-fields (e.g., `usage={"input_tokens": 100}` only) → treats the missing fields as 0, no KeyError.
- [ ] `detect_repetition` returns `(True, None, 5)` edge case — skip steer (nothing to report on).
- [ ] Dashboard serializer when `total_input_tokens` is None (session predates migration) → return 0, not None.
- [ ] Worker idle sweeper on empty `_active_clients` → one `asyncio.sleep(IDLE_SWEEP_INTERVAL)`, no other side effects.

### Error State Rendering
- [ ] Loop-break steering message is visible in session steering history (`valor-session status --id ...`) — test asserts the message appears with `sender="watchdog"`.
- [ ] Token-alert steering message likewise tagged `sender="watchdog"` and carries `reason="token_alert"` in the cooldown trace.
- [ ] Dashboard `/dashboard.json` returns token fields for every session, even brand-new ones (values = 0), never omitted.

## Test Impact

- [ ] `tests/unit/test_session_watchdog.py` — UPDATE: existing tests assert detections fire but do not act; add new assertions that `_inject_watchdog_steer` is called exactly once per detection (mock the helper or the Redis client) and a second call within the cooldown window is suppressed.
- [ ] `tests/unit/test_session_watchdog.py::test_repetition_detection_passive` — REPLACE: rename to `test_repetition_detection_triggers_steer` and update assertions (old test just checked the boolean return; new test checks the steering side effect).
- [ ] `tests/unit/test_transcript_liveness.py` — no change; orthogonal.
- [ ] `tests/unit/test_stall_detection.py` — no change; idle teardown is a different code path, handled in new tests.
- [ ] `tests/unit/test_recovery_respawn_safety.py` — no change; recovery is unrelated.
- [ ] `tests/unit/test_bridge_watchdog.py` — no change; bridge watchdog does not own these detections.
- [ ] `tests/unit/test_harness_streaming.py` — UPDATE: existing tests validate `_run_harness_subprocess` parsing of `result` events; add new assertions that `usage` and `total_cost_usd` are captured off the `result` event JSON and threaded through the return tuple.
- [ ] `tests/unit/test_deliver_pipeline_completion.py` — no change expected (mocks `get_response_via_harness` at the return value; signature stays `str`); verify no breakage after refactor.

**New tests to add (all file paths to be created):**
- `tests/unit/test_session_token_accumulator.py` — `accumulate_session_tokens` with normal values writes to AgentSession; None/missing fields default to 0; concurrent save logs warning but doesn't raise; `WATCHDOG_TOKEN_TRACKING_ENABLED=false` is a no-op; dashboard field populated end-to-end.
- `tests/unit/test_harness_token_capture.py` — `_run_harness_subprocess` extracts `usage` + `total_cost_usd` off a `result` event; `get_response_via_harness` calls `accumulate_session_tokens` as a side effect with the captured values; caller's return signature remains `str`.
- `tests/unit/test_watchdog_loop_break_steer.py` — repetition triggers one steer via `_inject_watchdog_steer`; atomic `SET NX EX` cooldown suppresses duplicates within 900s; cooldown expiry re-enables; error cascade uses independent cooldown key; `sender="watchdog"` visible in the pushed message envelope; `WATCHDOG_AUTO_STEER_ENABLED=false` suppresses.
- `tests/unit/test_watchdog_token_alert.py` — a session with `total_input_tokens + total_output_tokens >= TOKEN_ALERT_THRESHOLD` and status=running triggers one steer with `reason="token_alert"`; cooldown of 3600s holds; same session below threshold does not trigger; watchdog reads tokens, never writes.
- `tests/unit/test_worker_idle_sweeper.py` — `run_idle_sweep` one-iteration test: entry in `_active_clients` with AgentSession status=`dormant` AND `updated_at > 24h ago` is torn down (client.close called, registry entry popped, `sdk_connection_torn_down_at` set); entry with status=`running` is skipped; entry with status=`dormant` but `updated_at < 24h` is skipped; entry for `paused_circuit` is torn down at 24h; missing/None client does not raise; `WATCHDOG_IDLE_TEARDOWN_ENABLED=false` is a no-op.

## Rabbit Holes

- **Don't re-architect the heartbeat system.** #1036's dual-heartbeat OR is working; idle teardown is a NEW signal, not a replacement. Resist the urge to unify.
- **Don't try to resurrect dead SDK connections.** If a client is torn down, the next `query()` builds a fresh one. No "reconnect" logic; no exponential-backoff reconnection dance.
- **Don't implement a budget system.** The token threshold is an alert, not a spend cap. The agent keeps running; it just gets a steering message. A hard cap is a separate conversation (see No-Gos).
- **Don't add a "circuit breaker" for flapping loops.** Cooldown is sufficient. If a session keeps hitting the same loop after three steers, a human should intervene — that's what the alert is for.
- **Don't surface token cost in `valor-session status` now.** Dashboard is the right surface for operator visibility. CLI exposure is easy later; defer.

## Risks

### Risk 1: Steering message floods the session
**Impact:** A session that keeps emitting `detect_repetition=True` on every watchdog tick could receive steers every 300s, drowning out the agent's actual work.
**Mitigation:** Cooldown TTL of 900s (3 ticks). Plus: the steering message itself instructs the agent to "summarize and try a different approach" — if it works, repetition stops and no further steers fire. If it doesn't, the 900s cooldown gives the agent room to respond before the next steer.

### Risk 2: Token accumulation adds latency to hot path
**Impact:** SDK query latency grows by however long the Popoto `save(update_fields=...)` takes per turn.
**Mitigation:** `save(update_fields=[four_fields])` is a single Redis HMSET — sub-millisecond. Measured cost: negligible. If profiling shows otherwise, move to a fire-and-forget background task via `asyncio.create_task`.

### Risk 3: Idle sweeper closes a connection the worker was about to use
**Impact:** A dormant session transitioning to running could race: the idle sweeper tears down the client while the worker is just starting a new query on it.
**Mitigation:** The idle sweeper runs **in the same process** as the query path (see spike-2). Status filter is `{dormant, paused, paused_circuit}` AND `updated_at > 24h old` — the worker transitions `dormant → running` BEFORE querying, so a sweep filtered on `dormant` will not target an active query. `_active_clients.pop(..., None)` is safe on missing keys; `await client.close()` is idempotent, wrapped in try/except. Worst case: sweeper loses a race and closes a just-reactivated client → the query fails once → standard retry / fresh-client rebuild proceeds. No data loss.

### Risk 4: Token threshold fires on legitimate long-running sessions
**Impact:** A genuinely large refactor might hit 5M tokens legitimately; the steer would interrupt productive work.
**Mitigation:** 5M is intentionally high (≈$75 at Sonnet rates). The steer is a nudge, not a kill. Env var (`WATCHDOG_TOKEN_ALERT_THRESHOLD`) allows per-deployment tuning. Long-term: tune based on observed p90/p99 token spend from analytics.

### Risk 5: Watchdog tick interval (300s) misses short loops
**Impact:** A session that loops briefly then recovers could trigger the watchdog after it's already resolved.
**Mitigation:** Accept the delay. The goal is not real-time correction — it's preventing 30-minute stuck loops. 5 minutes of loop before steering is much better than 30.

## Race Conditions

### Race 1: Concurrent token save + session status transition
**Location:** `agent/sdk_client.py:~1233` (new `accumulate_session_tokens`) vs. `models/session_lifecycle.py::finalize_session`.
**Trigger:** A `ResultMessage` arrives right as the watchdog marks the session `abandoned`. Both paths call `AgentSession.save()`.
**Data prerequisite:** Both writers target different fields (`total_*_tokens` vs. `status`). Popoto's `update_fields` filter means they don't clobber each other.
**State prerequisite:** Session record exists.
**Mitigation:** Use `save(update_fields=[...])` with explicit field lists on both sides. On ModelException (duplicate key race), log and continue. The token save is idempotent-adjacent: losing one turn's accounting is acceptable; a crash is not.

### Race 2: Cooldown read + write is not atomic
**Location:** `monitoring/session_watchdog.py::_inject_loop_break_steer`.
**Trigger:** Two watchdog ticks somehow overlap (should not happen — single-threaded — but the concern is long ticks that overrun the interval).
**Data prerequisite:** Redis key `watchdog:steer_cooldown:{session_id}`.
**State prerequisite:** None.
**Mitigation:** Use Redis `SET ... NX EX 900` (atomic set-if-not-exists with TTL). If the SET returns False, cooldown is held; skip. This makes the check-and-set atomic in Redis itself, eliminating the race.

### Race 3: SDK client teardown vs. resume (same-process)
**Location:** `agent/sdk_client.py::_active_clients` registry + `worker/idle_sweeper.py::_sweep_once`.
**Trigger:** Sweeper iterates `_active_clients` while an active query is registering/removing an entry.
**Data prerequisite:** Registry dict.
**State prerequisite:** Client context enter/exit is mid-flight.
**Mitigation:** Sweeper runs in the SAME process as registry writers — this is a single-process asyncio concurrency concern, not a cross-process race. Iterate a snapshot (`list(_active_clients.items())`), not the live dict. `dict.pop(key, None)` is safe on concurrent removal. `await client.close()` is idempotent. No lock required; Python's GIL + asyncio's single-threaded event loop make the snapshot-based iteration race-safe for this use. If the sweep targets an entry whose `AgentSession.status` has just flipped to `running` between the filter read and the close, the close may interrupt an in-flight query — treated as a non-fatal fail-and-retry (see Risk 3).

## No-Gos (Out of Scope)

- **Hard token-spend cap / kill.** Alert only. If operators need a hard cap, file a follow-up.
- **Mid-turn steering interruption.** Steering messages drain at tool-call boundaries via the existing PostToolUse hook. Not touching that. If the agent is mid-generation with no tool calls, the next tool call is when it sees the steer — acceptable.
- **Cross-session aggregate budgets** (e.g., "max $500/day fleet-wide"). Fleet-level thresholds belong in `CircuitBreaker` / queue governor (#773), not per-session.
- **Telegram alerts for token thresholds.** The watchdog currently pushes notifications for critical abandonment. Per-session token alerts route to steering for now; Telegram notification if needed is a follow-up.
- **Dashboard UI changes.** Only `/dashboard.json` is touched. The HTML UI work is deferred — JSON consumers (scripts, monitoring) benefit immediately; HTML can render the new fields in a follow-up.
- **Retroactive token accounting.** Tokens for turns that completed before this ships are lost. No backfill.
- **Graceful SDK reconnect.** If we tear down a dormant client and the session resumes, the next query builds a fresh one. No attempt to preserve conversation state across the teardown — the SDK already re-establishes context from `claude_session_uuid`.

## Update System

No update-script changes required. This is a bridge/worker-internal change:
- New `AgentSession` fields auto-materialize on save (Popoto schema is additive).
- New env vars (`WATCHDOG_AUTO_STEER_ENABLED`, `WATCHDOG_TOKEN_TRACKING_ENABLED`, `WATCHDOG_IDLE_TEARDOWN_ENABLED`, `WATCHDOG_TOKEN_ALERT_THRESHOLD`, `WATCHDOG_TOKEN_ALERT_COOLDOWN`, `WATCHDOG_STEER_COOLDOWN`, `WATCHDOG_IDLE_TEARDOWN_THRESHOLD_SECONDS`, `WATCHDOG_IDLE_SWEEP_INTERVAL`) have safe defaults, so no `.env` template change is required. Document them in `docs/features/session-watchdog.md`.
- No new dependencies; `scripts/remote-update.sh` pull + restart is sufficient.
- After deploy, `./scripts/valor-service.sh restart` cycles bridge, watchdog, **and worker** to pick up the new code. The **worker restart is specifically required** because the new `worker/idle_sweeper.py` task is started by `worker/__main__.py`; without restarting the worker, the sweeper will not run.

## Agent Integration

No MCP / agent-tool changes. This is a watchdog/bridge-internal reliability feature. The agent does NOT receive new tools, and the PM/Dev/Teammate personas are unaffected.

The only user-visible change is that operators (Valor, Tom) see token columns on `/dashboard.json` and may observe watchdog-authored steering messages in session history (`valor-session status --id ...`). Those are passive observations — no new persona behavior to integrate.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-watchdog.md` to cover: loop-break auto-steering, cooldown semantics (atomic `SET NX EX`), env-var tuning. Explicitly list the three reason-keyed cooldowns.
- [ ] Update `docs/features/session-watchdog-reliability.md` to cross-reference the new loop-break actuator and the two-path token tracking.
- [ ] Update `docs/features/session-steering.md` to note that the watchdog is now a steering-message sender (`sender="watchdog"`) alongside human operators.
- [ ] Update `docs/features/bridge-worker-architecture.md` to document the **worker-internal idle sweeper** (`worker/idle_sweeper.py`) — distinct from watchdog-process responsibilities.
- [ ] Update `docs/features/bridge-self-healing.md` to add a section on "Idle SDK teardown" (runs inside the worker process) and reference `IDLE_TEARDOWN_THRESHOLD` + `IDLE_SWEEP_INTERVAL`.
- [ ] Add entry to `docs/features/README.md` index if no entry exists, OR note that the four updated docs cover this plan.

### Inline Documentation
- [ ] Docstring on new `accumulate_session_tokens` in `sdk_client.py`, explicitly noting it is called from **both** the SDK `ResultMessage` handler and `get_response_via_harness` side-effect path.
- [ ] Docstring on new `_inject_watchdog_steer` in `session_watchdog.py` listing the three reasons and the atomic cooldown contract.
- [ ] Docstring on new `worker/idle_sweeper.py::run_idle_sweep` and `_sweep_once`, noting process-locality of `_active_clients`.
- [ ] Module-level comment block in `session_watchdog.py` updated to list: "When detections fire, they AUTOMATICALLY steer. See `_inject_watchdog_steer`." — removing the old "logged only" wording.
- [ ] Inline comment at `_run_harness_subprocess`'s `result` event branch explaining why usage + cost are extracted and threaded back.

### External Documentation Site
- No Sphinx / Read the Docs site in this repo. Skip.

## Success Criteria

- [ ] `AgentSession.total_input_tokens`, `total_output_tokens`, `total_cache_read_tokens`, `total_cost_usd` exist and update monotonically across a session's turns, on **both** SDK and harness paths.
- [ ] A harness-path session (PM or Dev) accumulates non-zero `total_cost_usd` after its first turn completes (verifies B3 fix — previously would stay 0).
- [ ] `/dashboard.json` exposes all four token fields per session, including for newly-created sessions (as 0; never null / omitted).
- [ ] `monitoring/session_watchdog.py`: simulated `detect_repetition=True` results in exactly ONE `push_steering_message(sender="watchdog")` call per cooldown window; second detection within 900s is suppressed via atomic `SET NX EX`; detection after 900s+ fires again.
- [ ] Simulated `detect_error_cascade=True` triggers exactly one steer via its **own** cooldown key (independent from repetition cooldown).
- [ ] A session whose cumulative tokens exceed `TOKEN_ALERT_THRESHOLD` receives exactly one steer per `TOKEN_ALERT_COOLDOWN` window, with `reason="token_alert"` cooldown key.
- [ ] A **SDK-path** session in `dormant` / `paused` / `paused_circuit` status with `updated_at` >24h old gets its SDK client torn down by the worker idle sweeper; `sdk_connection_torn_down_at` is set; subsequent resume builds a fresh client and succeeds end-to-end.
- [ ] A harness-path session is NOT affected by the idle sweeper (no `_active_clients` entry to tear down).
- [ ] Watchdog process does NOT import or reference `_active_clients` — verified via grep.
- [ ] `WATCHDOG_AUTO_STEER_ENABLED=false` disables loop-break steering without disabling detection (still logged at WARNING).
- [ ] `WATCHDOG_TOKEN_TRACKING_ENABLED=false` skips the accumulate call on both paths without crashing.
- [ ] `WATCHDOG_IDLE_TEARDOWN_ENABLED=false` skips the worker sweep without crashing.
- [ ] All watchdog-authored steering messages are tagged `sender="watchdog"` and visible as such in `valor-session status --id <sid>`.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] All three features individually toggleable; default-on in production.

## Team Orchestration

- **Builder (token-accumulator)**
  - Name: `token-builder`
  - Role: Add `AgentSession` fields + `accumulate_session_tokens` helper + SDK wiring + dashboard field exposure.
  - Agent Type: builder
  - Resume: true

- **Builder (loop-break-steer)**
  - Name: `steer-builder`
  - Role: Wire `detect_repetition` + `detect_error_cascade` to `push_steering_message` with Redis cooldowns.
  - Agent Type: builder
  - Resume: true

- **Builder (worker idle-sweeper)**
  - Name: `teardown-builder`
  - Role: Create `worker/idle_sweeper.py` module + wire into `worker/__main__.py` + add `sdk_connection_torn_down_at` field. The `_active_clients` registry already exists in `agent/sdk_client.py` — no registry creation needed, just consumption.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (watchdog tests)**
  - Name: `watchdog-test-engineer`
  - Role: Write the three new test files + update existing `test_session_watchdog.py` assertions.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (integration)**
  - Name: `watchdog-validator`
  - Role: Run the full watchdog test suite, validate dashboard JSON schema, verify env-var gating works end-to-end.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `watchdog-documentarian`
  - Role: Update the four feature docs + README entry if needed.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Using core agent types: builder, test-engineer, validator, documentarian. No specialists required — all three subsystems are CRUD + I/O against existing abstractions.

## Step by Step Tasks

### 1. Add AgentSession token fields + sdk_client accumulator helper
- **Task ID**: build-token-accumulator
- **Depends On**: none
- **Validates**: `tests/unit/test_session_token_accumulator.py` (to be created)
- **Informed By**: spike-1 (ResultMessage.usage structure), spike-5 (harness result event carries usage)
- **Assigned To**: token-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `total_input_tokens`, `total_output_tokens`, `total_cache_read_tokens` as `IntField(default=0)` and `total_cost_usd` as `FloatField(default=0.0)` to `models/agent_session.py`.
- Add `accumulate_session_tokens(session_id, input_tokens, output_tokens, cache_read_tokens, cost_usd)` function near the existing `record_session_activity` / `_store_claude_session_uuid` helpers in `agent/sdk_client.py`. Use `save(update_fields=[total_input_tokens, total_output_tokens, total_cache_read_tokens, total_cost_usd])`. Wrap in `try/except ModelException` + outer `try/except Exception` with WARNING-level log including `session_id`.
- Gate with `WATCHDOG_TOKEN_TRACKING_ENABLED` env var (default `true`) — early-return when disabled.
- Guard against `None`/missing values: `getattr(usage, "input_tokens", 0) or 0`.

### 2. Wire accumulator into SDK path ResultMessage handler
- **Task ID**: build-token-sdk-path
- **Depends On**: build-token-accumulator
- **Validates**: `tests/unit/test_session_token_accumulator.py` (SDK path cases)
- **Assigned To**: token-builder
- **Agent Type**: builder
- **Parallel**: false
- Locate the existing `ResultMessage` handler in `agent/sdk_client.py` (near lines 1251–1276 — the block that already captures `msg.total_cost_usd`). Extend the handler to also read `msg.usage.input_tokens`, `msg.usage.output_tokens`, `msg.usage.cache_read_input_tokens`, with safe-default fallback via `getattr`.
- Immediately after the existing `total_cost_usd` capture, call `accumulate_session_tokens(session_id, ...)` with the captured values.
- No signature changes to any public function; accumulator runs as a side effect.

### 3. Wire accumulator into harness path (addresses critique B3)
- **Task ID**: build-token-harness-path
- **Depends On**: build-token-accumulator
- **Validates**: `tests/unit/test_harness_token_capture.py` (to be created), `tests/unit/test_harness_streaming.py` (update)
- **Informed By**: spike-5 (harness result event carries usage), spike-6 (side-effect pattern matches existing `_store_claude_session_uuid`)
- **Assigned To**: token-builder
- **Agent Type**: builder
- **Parallel**: false
- Extend `_run_harness_subprocess` in `agent/sdk_client.py` (starting around line 1786). Inside the `event_type == "result"` branch (around line 1857), extract additional fields: `usage = data.get("usage")` and `cost_usd = data.get("total_cost_usd")`.
- Change the return tuple of `_run_harness_subprocess` from `(result_text, session_id_from_harness, returncode)` to `(result_text, session_id_from_harness, returncode, usage, cost_usd)`. Update all three internal callers inside `get_response_via_harness` (image-dimension fallback, stale-UUID fallback, and the primary call).
- In `get_response_via_harness`, just before returning `result_text`, call `accumulate_session_tokens(session_id, usage.get("input_tokens"), usage.get("output_tokens"), usage.get("cache_read_input_tokens"), cost_usd)` **as a side effect** — mirroring the existing `_store_claude_session_uuid(session_id, session_id_from_harness)` call at line 1778. Only fire when `session_id` is set.
- Caller signature of `get_response_via_harness` remains `-> str` — `session_executor.py` and `session_completion.py` are unchanged.

### 4. Expose token fields on dashboard
- **Task ID**: build-dashboard-tokens
- **Depends On**: build-token-accumulator
- **Validates**: `tests/unit/test_dashboard_json.py` (create or extend)
- **Assigned To**: token-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `total_input_tokens: int = 0`, `total_output_tokens: int = 0`, `total_cache_read_tokens: int = 0`, `total_cost_usd: float = 0.0` to `PipelineProgress` dataclass in `ui/data/sdlc.py`.
- Populate them from `AgentSession` in the `PipelineProgress` reader.
- Add to `_session_to_json` in `ui/app.py` around line 244. Always emit defaults (0 / 0.0) — never omit, never None — for forward-compat with existing JSON consumers.

### 5. Wire loop-break steering (addresses critique C1, C4, C5)
- **Task ID**: build-loop-break-steer
- **Depends On**: none
- **Validates**: `tests/unit/test_watchdog_loop_break_steer.py` (to be created), `tests/unit/test_session_watchdog.py` (update)
- **Informed By**: spike-3 (watchdog→steering wiring is ~15 LoC per detection)
- **Assigned To**: steer-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_inject_watchdog_steer(session_id: str, reason: str, message: str)` in `monitoring/session_watchdog.py`. Single helper serves all three trigger reasons (`repetition`, `error_cascade`, `token_alert`).
- Cooldown uses atomic `POPOTO_REDIS_DB.set(f"watchdog:steer_cooldown:{reason}:{session_id}", "1", nx=True, ex=STEER_COOLDOWN)` — explicit keyword ordering for `nx` + `ex`. Return value truthy ⇒ cooldown slot was open; falsy ⇒ key exists, skip.
- Call `push_steering_message(session_id, message, sender="watchdog")` when cooldown is open. The `sender="watchdog"` is **required** for downstream attribution (C5).
- Call it from the positive branches of `detect_repetition` (currently at `monitoring/session_watchdog.py:471–522`) and `detect_error_cascade` (currently at `monitoring/session_watchdog.py:525–574`).
- Compose distinct messages per reason (see Data Flow section — verbatim templates above).
- Gate with `WATCHDOG_AUTO_STEER_ENABLED` env var (default `true`). When disabled: still log the detection at WARNING; do not push.
- Delivery timing (C1): the PostToolUse hook + turn-boundary drain in the PM session already consume `queued_steering_messages` before the next tool call. This task does NOT touch that consumer. Add a test that asserts a pushed message is visible via `AgentSession.queued_steering_messages` queue inspection immediately after the call.

### 6. Add token-threshold alert
- **Task ID**: build-token-threshold-alert
- **Depends On**: build-token-accumulator, build-loop-break-steer, build-token-sdk-path, build-token-harness-path
- **Validates**: `tests/unit/test_watchdog_token_alert.py` (to be created)
- **Assigned To**: steer-builder
- **Agent Type**: builder
- **Parallel**: false
- In `monitoring/session_watchdog.py::_check_session_health`, read `AgentSession.total_input_tokens + total_output_tokens` for each session. Watchdog is a **read-only** consumer of these fields — never write.
- If sum >= `TOKEN_ALERT_THRESHOLD` (default 5M, env `WATCHDOG_TOKEN_ALERT_THRESHOLD`) AND status == `running`, call `_inject_watchdog_steer(session_id, "token_alert", msg)`.
- Alert cooldown TTL = `TOKEN_ALERT_COOLDOWN` (default 3600s, env `WATCHDOG_TOKEN_ALERT_COOLDOWN`) — passed to the helper as the `ex` value.
- Message: `f"Token budget exceeded: ${cost_usd:.2f} / {total_tokens:,} tokens spent this session. Stop and summarize what you've done."` — cost taken verbatim from `AgentSession.total_cost_usd` (never recomputed, per the Pricing Constants subsection above).

### 7. Add worker idle-sweeper (addresses critique B1, B2, C3)
- **Task ID**: build-idle-sweeper
- **Depends On**: none
- **Validates**: `tests/unit/test_worker_idle_sweeper.py` (to be created)
- **Informed By**: spike-2 (execution-path audit: teardown is worker-local, not watchdog-process)
- **Assigned To**: teardown-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `sdk_connection_torn_down_at = DatetimeField(null=True)` to `AgentSession` in `models/agent_session.py`.
- Create **new module** `worker/idle_sweeper.py`:
  - `async def run_idle_sweep(interval: int = 1800)` — infinite loop with `await asyncio.sleep(interval)`; each tick calls `_sweep_once()`.
  - `async def _sweep_once()` — reads `list(_active_clients.items())` snapshot from `agent.sdk_client`; for each `(session_id, client)` entry, loads `AgentSession`, filters to `status in {dormant, paused, paused_circuit}` (C3: all three explicit) AND `updated_at > IDLE_TEARDOWN_THRESHOLD`; calls `await client.close()` wrapped in `try/except`; pops the entry from `_active_clients`; sets `AgentSession.sdk_connection_torn_down_at = datetime.now(UTC)` via `save(update_fields=["sdk_connection_torn_down_at"])`.
  - Constants: `IDLE_TEARDOWN_THRESHOLD`, `IDLE_SWEEP_INTERVAL` — both env-overridable.
- In `worker/__main__.py`, spawn `run_idle_sweep()` as a background task alongside the main session loop (similar to how the reflection scheduler is started). Ensure cancellation on shutdown.
- Gate with `WATCHDOG_IDLE_TEARDOWN_ENABLED` env var (default `true`) — early-return from `_sweep_once` if disabled.
- Use `list(_active_clients.items())` (snapshot) — never iterate the live dict. `dict.pop(key, None)` is safe on missing keys.
- Explicitly document that this module runs in the **worker process** and that the watchdog process does NOT and must NOT try to reach `_active_clients`.

### 8. Write test suite
- **Task ID**: test-watchdog-hardening
- **Depends On**: build-token-accumulator, build-token-sdk-path, build-token-harness-path, build-dashboard-tokens, build-loop-break-steer, build-token-threshold-alert, build-idle-sweeper
- **Assigned To**: watchdog-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Create/update the test files listed in Test Impact.
- Cover positive path, cooldown suppression (atomic SET NX EX), env-var disable, concurrent-save race, both SDK and harness token-capture paths, worker idle-sweeper filter logic.
- All tests use in-process Popoto + fakeredis — no network.

### 9. Validate integration end-to-end
- **Task ID**: validate-watchdog-hardening
- **Depends On**: test-watchdog-hardening
- **Assigned To**: watchdog-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full `pytest tests/unit/test_watchdog*.py tests/unit/test_session_*.py tests/unit/test_harness*.py tests/unit/test_worker_idle_sweeper.py` suite.
- Hit `/dashboard.json` on a live worker, assert token fields present for at least one session.
- Flip each env var off, restart worker, verify gating works.
- Simulate a repetition loop against a real (throwaway) session and observe the steering message with `sender="watchdog"` in `valor-session status`.
- Verify the worker idle-sweeper logs a tick every 30 min in `logs/worker.log`.

### 10. Documentation
- **Task ID**: document-watchdog-hardening
- **Depends On**: validate-watchdog-hardening
- **Assigned To**: watchdog-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update the four feature docs listed in Documentation.
- Add env-var table to `docs/features/session-watchdog.md`.
- Explicitly note the **two execution paths** (SDK vs harness) in the token-tracking section so future contributors don't try to add tracking in only one place.
- Document the worker-internal idle sweeper separately from the watchdog-process responsibilities.

### 11. Final validation
- **Task ID**: validate-all
- **Depends On**: document-watchdog-hardening
- **Assigned To**: watchdog-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all checks from Verification table.
- Confirm all Success Criteria checkboxes.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| New watchdog/harness/worker tests pass | `pytest tests/unit/test_watchdog_loop_break_steer.py tests/unit/test_watchdog_token_alert.py tests/unit/test_session_token_accumulator.py tests/unit/test_harness_token_capture.py tests/unit/test_worker_idle_sweeper.py tests/unit/test_session_watchdog.py -x -q` | exit code 0 |
| All unit tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| New AgentSession fields exist | `python -c "from models.agent_session import AgentSession; assert all(hasattr(AgentSession, f) for f in ['total_input_tokens', 'total_output_tokens', 'total_cache_read_tokens', 'total_cost_usd', 'sdk_connection_torn_down_at'])"` | exit code 0 |
| Token accumulator exists | `grep -q 'def accumulate_session_tokens' agent/sdk_client.py` | exit code 0 |
| Accumulator called from SDK ResultMessage handler | `grep -nP 'accumulate_session_tokens\(' agent/sdk_client.py \| wc -l` | >= 2 (SDK path + harness path) |
| Harness subprocess captures usage | `grep -q 'usage.*data.get' agent/sdk_client.py \|\| grep -q 'data.get."usage"' agent/sdk_client.py` | exit code 0 |
| Watchdog steer helper wired | `grep -q '_inject_watchdog_steer' monitoring/session_watchdog.py` | exit code 0 |
| Cooldown uses atomic SET NX EX | `grep -nE 'set\([^)]*nx=True' monitoring/session_watchdog.py` | non-empty |
| Sender attribution on watchdog steers | `grep -q 'sender="watchdog"' monitoring/session_watchdog.py` | exit code 0 |
| Worker idle sweeper module exists | `test -f worker/idle_sweeper.py` | exit code 0 |
| Worker main starts sweeper | `grep -q 'run_idle_sweep' worker/__main__.py` | exit code 0 |
| Dashboard exposes tokens | `grep -q 'total_input_tokens' ui/app.py && grep -q 'total_input_tokens' ui/data/sdlc.py` | exit code 0 |

## Critique Results

Verdict after first critique: **NEEDS REVISION** (3 blockers, 5 concerns, 3 nits). Revisions folded back into the plan; table below tracks each finding → disposition.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| Blocker | Archaeologist | B1: `_active_clients` registry is process-local to the worker; the session-watchdog runs in a separate process and cannot reach it. The original "watchdog tears down dormant clients" design was infeasible. | `build-idle-sweeper`, Data Flow → Idle teardown, Architectural Impact → Process topology | Idle-teardown moved into `worker/idle_sweeper.py`, co-located with the registry. Watchdog process no longer participates. |
| Blocker | Archaeologist | B2: Production's PM/Dev/Teammate path uses `_run_harness_subprocess` (short-lived `claude -p` subprocess per turn), not a persistent `ClaudeSDKClient`. The 48h silent-death premise only applies to the SDK path. | spike-2 re-write, Data Flow → Idle teardown | Idle-teardown is a no-op for harness-path sessions (nothing to tear down). Scope limited to SDK-path sessions (chat routes). |
| Blocker | Skeptic | B3: Token accumulator only hooked SDK `ResultMessage` handler; production harness path would report 0 tokens forever. | spike-5, spike-6, `build-token-harness-path` | `_run_harness_subprocess` now extracts `usage` + `total_cost_usd` from the `result` event JSON; `get_response_via_harness` calls `accumulate_session_tokens` as a side effect before returning. Both paths feed the same fields. |
| Concern | Operator | C1: Steering delivery timing — will the loop-break steer actually arrive before the next identical tool call? | Data Flow → Loop-break steering step 5, `build-loop-break-steer` test | Delivery is at the next tool-call boundary via the existing PostToolUse hook. One-tool-call delay documented; acceptable because stuck loops emit many tool calls per minute. Test asserts message is queued immediately. |
| Concern | Skeptic | C2: Token-to-dollar scaling — hardcoded pricing constants drift silently when Anthropic updates rates. | Data Flow → Pricing constants subsection | Do **not** maintain a local price table. Use `msg.total_cost_usd` (SDK) or `data.get("total_cost_usd")` (harness) verbatim. Costs track upstream pricing automatically. |
| Concern | Adversary | C3: `check_all_sessions` status filter for idle-teardown must include `paused` and `paused_circuit`, not only `dormant`. | Technical Approach → Idle teardown status filter, `build-idle-sweeper` | Status filter explicitly targets `{dormant, paused, paused_circuit}`; excluded statuses enumerated; test asserts `paused_circuit` is torn down at 24h. |
| Concern | Simplifier | C4: Cooldown `SET NX EX` keyword ordering — must be truly atomic, not a read-then-write. | Technical Approach → Cooldown atomic ordering, `build-loop-break-steer` | Explicit `POPOTO_REDIS_DB.set(key, value, nx=True, ex=ttl)` single-command pattern documented with code snippet. Verification table greps for `nx=True`. |
| Concern | Operator | C5: `valor-session` sender-metadata visibility — watchdog-authored steers must be distinguishable from human steers. | Technical Approach → Steering attribution, `build-loop-break-steer` | `sender="watchdog"` mandatory on every `push_steering_message` call. Verification table greps for the literal string. Already supported by `agent/steering.py` + downstream consumers (verified via code-read). |
| Nit | Archaeologist | Line-range precision in file references (`sdk_client.py:1216` was stale; real handler is at 1251). | Freshness Check, spike-1, spike-5, Task 2 | File:line references updated across the plan. |
| Nit | Archaeologist | Test file references pointed at paths that don't yet exist without marking them as to-be-created. | Test Impact, Tasks 1–7 | All new test files explicitly marked "(to be created)"; existing files marked "(update)". |
| Nit | Simplifier | Constant naming inconsistent between sections (e.g., `STEER_COOLDOWN` vs. an inline 900s literal). | Technical Approach → Thresholds + env-var gates, Tasks | Single authoritative constants block; all inline numbers cross-reference named constants. |

---

## Open Questions

1. **Token threshold default (5M / $75 Sonnet).** Is 5M the right soft threshold, or should it be lower (1M / $15) to catch runaway loops earlier? Depends on observed p90 token spend — do we have that data from analytics? If not, start at 5M and tune down based on how often it fires.
2. **Idle teardown threshold (24h).** The 48h silent-death window gives us a 24h safety margin. Should we be more aggressive (12h) to reduce the window of risk, or less (36h) to reduce unnecessary teardowns for near-dormant sessions? Leaning 24h as the balance.
3. **Loop-break message phrasing.** The messages in Data Flow are my best guess. Should they be shorter / more authoritative / framed as the PM/operator? (e.g., "Stop. Summarize and try a different approach." vs. the longer templates I drafted.)
4. **Should Row 4 (token-threshold alert) escalate to Telegram after N steers in a session?** Current plan: steer only. If a session has been steered 3 times for token spend in a single day, that's probably a human-intervention moment. Do we want that escalation in v1 or follow-up?
