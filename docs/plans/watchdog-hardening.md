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
- **Finding**: Confirmed. `ResultMessage` fields include `usage`, `model_usage`, `total_cost_usd`, `duration_ms`, `num_turns`. `usage` is a dict-like with `input_tokens`, `output_tokens`, and typically `cache_read_input_tokens`, `cache_creation_input_tokens`. `total_cost_usd` is already captured at `agent/sdk_client.py:1233`.
- **Confidence**: high
- **Impact on plan**: Token aggregation hooks into the existing `ResultMessage` handler at `sdk_client.py:1216`. Adds 4 integer fields to `AgentSession`: `total_input_tokens`, `total_output_tokens`, `total_cache_read_tokens`, `total_cost_usd`. No new SDK integration required.

### spike-2: Idle-probe mechanism — API call vs passive-check
- **Assumption**: "Proactively probing a dormant SDK connection requires an actual lightweight API call to detect silent death."
- **Method**: code-read + doc-read
- **Finding**: No built-in SDK heartbeat/ping exists. The SDK's persistent connection is managed inside the `ClaudeSDKClient` context manager. For a dormant session (status = `dormant` / `paused`), the subprocess may be torn down entirely (no persistent connection held) OR held open across turns. The 48h silent-death is a concern only when a connection IS held open. The safer, cheaper approach is to **tear down the SDK connection entirely when the session enters `dormant` state** — and rebuild on resume. This sidesteps the 48h problem without per-session probe overhead.
- **Confidence**: high
- **Impact on plan**: Replace "proactive probe" with "teardown-on-dormant + fresh rebuild on resume." The watchdog tracks `last_activity_ts` on `AgentSession` and, if a session has been in `dormant` / `paused` for >24h and a live SDK connection still exists, it forcibly closes the connection. Resume creates a fresh client. This is strictly safer than probing and matches how #1036 treats stale heartbeats.

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

## Data Flow

### Token tracking
1. **Entry point**: Claude Agent SDK `ResultMessage` arrives in `agent/sdk_client.py:1216`.
2. **Capture**: Existing handler at line 1233 reads `msg.total_cost_usd`, `msg.num_turns`. We add reads for `msg.usage.input_tokens`, `msg.usage.output_tokens`, `msg.usage.cache_read_input_tokens`.
3. **Accumulate**: Call new helper `accumulate_session_tokens(session_id, input_tokens, output_tokens, cache_read_tokens, cost_usd)` which performs a single `AgentSession` update: `total_input_tokens += …`, `total_output_tokens += …`, `total_cache_read_tokens += …`, `total_cost_usd += …`.
4. **Redis write**: Popoto `save(update_fields=[…])` with the four fields. Fail-quiet on ModelException (same pattern as existing heartbeat writes).
5. **Read path — dashboard**: `ui/data/sdlc.py` loads `AgentSession`, populates `PipelineProgress.total_input_tokens` etc., `_session_to_json` includes in response. `/dashboard.json` response includes four new fields per session.
6. **Threshold check**: In the watchdog's per-session loop, if `total_input_tokens + total_output_tokens >= TOKEN_ALERT_THRESHOLD` (default 5_000_000) AND session status is `running` AND no alert has fired in the last 3600s (cooldown), push a steering message `"Token budget exceeded: $N spent this session. Stop and summarize what you've done."`.

### Idle teardown
1. **Trigger**: `session_watchdog._check_session_health` iterates sessions.
2. **Filter**: Session status in `{dormant, paused}` AND `last_activity_ts` (or `updated_at`) older than `IDLE_TEARDOWN_THRESHOLD` (default 86400s = 24h).
3. **Action**: Check whether a live SDK connection is held for this session_id in `agent/sdk_client.py` process memory (via `_active_clients` registry — new). If yes, close it. If no (subprocess not held across dormant states), no-op.
4. **Record**: Set `AgentSession.sdk_connection_torn_down_at = now()` so resumes know to build fresh. The resume path in `sdk_client.py` already builds a fresh client per query, so this is a soft no-op on resume but gives us an observable signal.

### Loop-break steering
1. **Trigger**: `detect_repetition` returns `(True, tool_name, count)` OR `detect_error_cascade` returns `(True, error_count)`.
2. **Cooldown check**: Redis `GET watchdog:steer_cooldown:{session_id}`. If present, skip. If absent, `SETEX watchdog:steer_cooldown:{session_id} 900 "1"`.
3. **Steering push**: Compose a targeted message:
   - Repetition: `"Stop and re-check the task — you appear to be repeating the same tool call ({tool_name}) {count} times. Summarize what you've tried, then try a different approach."`
   - Error cascade: `"Stop — you've hit {error_count} errors in the last 20 operations. Summarize the failure pattern and pause for human input rather than continuing blind."`
4. **Delivery**: `push_steering_message(session_id, text, sender="watchdog")`. The existing PostToolUse hook drains the queue on the next tool call.
5. **Logging**: `logger.warning("[watchdog] Loop-break steer injected for %s: %s", session_id, reason)` — visible in `logs/worker.log`.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1104 (investigation-only) | Flagged 48h idle SDK death from fleet-operations research | Never produced a fix — closed as "superseded by #1128." |
| #1105 (investigation-only) | Flagged stuck-agent loops + suggested steering-based intervention | Never wired up — detections remained passive, closed as "superseded by #1128." |

**Root cause pattern:** The system ships detections faster than it ships actuators. `detect_repetition` and `detect_error_cascade` landed with #440; the steering queue landed with #743; but the two were never connected. This plan closes the loop: detection → actuator, with cooldown to prevent amplification.

## Architectural Impact

- **New dependencies**: None. All building blocks exist (Popoto, Redis, SDK, steering queue).
- **Interface changes**:
  - `AgentSession` gains 5 fields: `total_input_tokens`, `total_output_tokens`, `total_cache_read_tokens`, `total_cost_usd`, `sdk_connection_torn_down_at`.
  - `agent/sdk_client.py` gains `accumulate_session_tokens(...)` helper (private).
  - `monitoring/session_watchdog.py` gains `_inject_loop_break_steer(session_id, reason)` helper (private).
  - `_session_to_json` (dashboard) gains 4 token fields in output.
- **Coupling**: Increases by one arrow — `monitoring/session_watchdog.py` now calls into `agent/steering.py`. Not a layering violation: `monitoring/` already imports `models/`, and `agent/steering.py` is a Redis-only module. No circular-import risk.
- **Data ownership**: Token counts are owned by `AgentSession` (same owner as existing heartbeat timestamps). Dashboard reads; worker/sdk_client writes. Single-writer per session; no contention.
- **Reversibility**: High. Token fields are additive and default to 0. Loop-break steering can be gated behind `WATCHDOG_AUTO_STEER_ENABLED` env var (default on, flipping off disables without rollback). Idle teardown only affects dormant sessions; disabling it loses no data.

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

- **Token accumulator**: On every `ResultMessage` in `sdk_client.py`, call `accumulate_session_tokens(session_id, …)` which persists input/output/cache/cost onto `AgentSession` via `save(update_fields=[…])`.
- **Dashboard surfacing**: Extend `PipelineProgress` + `_session_to_json` to include token fields so operators can see per-session spend on `/dashboard.json`.
- **Token-threshold alert**: Watchdog tick checks cumulative tokens against `TOKEN_ALERT_THRESHOLD` (default 5M). If exceeded AND session is `running` AND no alert fired in last hour, steer the session with a budget warning.
- **Loop-break steering**: In `_check_session_health`, when `detect_repetition` or `detect_error_cascade` fires, call `_inject_loop_break_steer(session_id, reason)` which (a) checks Redis cooldown, (b) composes a targeted message, (c) `push_steering_message(…)`, (d) SETEX the cooldown.
- **Idle teardown**: New `_teardown_dormant_sdk_connections` pass in the watchdog. For sessions in `dormant` / `paused` status with activity older than 24h AND a tracked live client in `sdk_client._active_clients`, close the client. Record `sdk_connection_torn_down_at` on the session.

### Flow

Session turn completes → SDK emits `ResultMessage` → sdk_client captures tokens + cost → `accumulate_session_tokens` writes to AgentSession → dashboard serializer picks up on next render.

Session enters stuck loop → watchdog tick (every 300s) calls `detect_repetition` → returns True → `_inject_loop_break_steer` checks cooldown → absent → push steering msg + SETEX cooldown 900s → PostToolUse hook drains queue on next tool call → session receives correction → the loop is broken.

Session goes dormant → watchdog tick → `_teardown_dormant_sdk_connections` finds stale client → closes it → session record gets `sdk_connection_torn_down_at` → operator resumes → `sdk_client.query` builds fresh client → no 48h silent death.

### Technical Approach

- Keep all three subsystems in their existing files: `agent/sdk_client.py` for token capture, `monitoring/session_watchdog.py` for loop-break + idle-teardown, `ui/app.py` + `ui/data/sdlc.py` for dashboard. No new modules unless LOC crosses 200 (then split the steering helper into `monitoring/loop_break.py`).
- Token accumulation is synchronous, fail-quiet. On `ModelException` (Popoto duplicate-key during concurrent save), log at WARNING and continue. Do NOT block the SDK query path on Redis latency — wrap the save in a try/except.
- Cooldown keys use Redis `SETEX` with an integer TTL. Key pattern: `watchdog:steer_cooldown:{session_id}` (for loop-break) and `watchdog:token_alert_cooldown:{session_id}` (for token threshold). Separate cooldowns so they don't squelch each other.
- Idle teardown only affects sessions with `status in {dormant, paused, paused_circuit}`. Never tear down `running` or `pending`. The `_active_clients` registry is a new `dict[str, ClaudeSDKClient]` at module scope in `sdk_client.py`; populated in the `async with` context-enter, removed in context-exit.
- Token threshold defaults: `TOKEN_ALERT_THRESHOLD = 5_000_000` (5M combined input+output), `TOKEN_ALERT_COOLDOWN = 3600` (one alert per hour per session). Both env-overridable (`WATCHDOG_TOKEN_ALERT_THRESHOLD`, `WATCHDOG_TOKEN_ALERT_COOLDOWN`).
- Idle teardown threshold: `IDLE_TEARDOWN_THRESHOLD = 86400` (24h). Env-overridable (`WATCHDOG_IDLE_TEARDOWN_THRESHOLD_SECONDS`). Chosen to sit well inside the 48h silent-death window with a full safety margin.
- All three features are individually gated by env vars so they can be disabled without a rollback: `WATCHDOG_AUTO_STEER_ENABLED` (default `true`), `WATCHDOG_TOKEN_TRACKING_ENABLED` (default `true`), `WATCHDOG_IDLE_TEARDOWN_ENABLED` (default `true`).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `accumulate_session_tokens` wraps save in try/except ModelException — test: simulate concurrent save conflict, assert logger.warning fires AND SDK query path continues without raising.
- [ ] `_inject_loop_break_steer` wraps `push_steering_message` in try/except — test: simulate Redis unavailable, assert watchdog tick continues and logs WARNING, does not crash the watchdog loop.
- [ ] `_teardown_dormant_sdk_connections` wraps client.close() in try/except — test: simulate client already closed, assert no exception propagates.
- [ ] No new `except Exception: pass` — every handler must log at WARNING or ERROR with session_id.

### Empty/Invalid Input Handling
- [ ] `accumulate_session_tokens` called with `usage=None` (older SDK version or error message) → no-op, no crash.
- [ ] `detect_repetition` returns `(True, None, 5)` edge case — skip steer (nothing to report on).
- [ ] Dashboard serializer when `total_input_tokens` is None (session predates migration) → return 0, not None.

### Error State Rendering
- [ ] Loop-break steering message is visible in session steering history (`valor-session status --id ...`) — test asserts the message appears with `sender="watchdog"`.
- [ ] Dashboard `/dashboard.json` returns token fields for every session, even brand-new ones (values = 0), never omitted.

## Test Impact

- [ ] `tests/unit/test_session_watchdog.py` — UPDATE: existing tests assert detections fire; add new assertions that `push_steering_message` is called exactly once per detection (mock the steering module) and twice if cooldown expires.
- [ ] `tests/unit/test_session_watchdog.py::test_repetition_detection_passive` — REPLACE: rename to `test_repetition_detection_triggers_steer` and update assertions (old test just checked the boolean return; new test checks side effect).
- [ ] `tests/unit/test_transcript_liveness.py` — no change; orthogonal.
- [ ] `tests/unit/test_stall_detection.py` — no change; idle teardown is a different code path, handled in new tests.
- [ ] `tests/unit/test_recovery_respawn_safety.py` — no change; recovery is unrelated.
- [ ] `tests/unit/test_bridge_watchdog.py` — no change; bridge watchdog does not own these detections.

**New tests to add:**
- `tests/unit/test_watchdog_loop_break_steer.py` — repetition triggers one steer, cooldown suppresses duplicates, cooldown expiry re-enables, error cascade triggers one steer, env var disable suppresses.
- `tests/unit/test_session_token_accumulator.py` — ResultMessage with usage accumulates; None usage is no-op; concurrent save logs warning but doesn't raise; dashboard field populated.
- `tests/unit/test_watchdog_idle_teardown.py` — dormant+24h tears down; dormant+12h does not; running+48h does not; `sdk_connection_torn_down_at` recorded; missing client no-ops cleanly.

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

### Risk 3: Idle teardown closes a connection the worker was about to use
**Impact:** A dormant session transitioning to running could race: watchdog tears down the client while the worker is just starting a new query on it.
**Mitigation:** Gate teardown on `status in {dormant, paused, paused_circuit}` AND `last_activity_ts > 24h old`. The worker transitions `dormant → running` BEFORE querying, so a teardown targeting `dormant` will never race with an active query. Plus the teardown is idempotent — if the worker has already rebuilt, the registry no longer points at the old client.

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

### Race 3: SDK client teardown vs. resume
**Location:** `agent/sdk_client.py::_active_clients` registry + `monitoring/session_watchdog.py::_teardown_dormant_sdk_connections`.
**Trigger:** Watchdog iterates `_active_clients` while the worker is adding/removing entries.
**Data prerequisite:** Registry dict.
**State prerequisite:** Client context enter/exit is mid-flight.
**Mitigation:** Protect `_active_clients` with a `threading.Lock` (or asyncio.Lock if accessed from async code). Iterate a snapshot (`list(_active_clients.items())`), not the live dict. Closing a client that was just removed is a no-op (close() is idempotent via try/except).

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
- New env vars have safe defaults, so no `.env` template change is required. Document them in `docs/features/session-watchdog.md` for operators who want to tune.
- No new dependencies; `scripts/remote-update.sh` pull + restart is sufficient.
- After deploy, `./scripts/valor-service.sh restart` cycles bridge, watchdog, and worker to pick up the new code. This is the standard restart flow per CLAUDE.md (development principle #10).

## Agent Integration

No MCP / agent-tool changes. This is a watchdog/bridge-internal reliability feature. The agent does NOT receive new tools, and the PM/Dev/Teammate personas are unaffected.

The only user-visible change is that operators (Valor, Tom) see token columns on `/dashboard.json` and may observe watchdog-authored steering messages in session history (`valor-session status --id ...`). Those are passive observations — no new persona behavior to integrate.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-watchdog.md` to cover: loop-break auto-steering, cooldown semantics, env-var tuning.
- [ ] Update `docs/features/session-watchdog-reliability.md` to cross-reference the new loop-break actuator.
- [ ] Update `docs/features/session-steering.md` to note that the watchdog is now a steering-message sender (alongside human operators).
- [ ] Update `docs/features/bridge-self-healing.md` to add a section on "Idle SDK teardown" and reference `IDLE_TEARDOWN_THRESHOLD`.
- [ ] Add entry to `docs/features/README.md` index if no entry exists, OR note that the three updated docs cover this plan.

### Inline Documentation
- [ ] Docstring on new `accumulate_session_tokens` in `sdk_client.py`.
- [ ] Docstring on new `_inject_loop_break_steer` in `session_watchdog.py`.
- [ ] Docstring on new `_teardown_dormant_sdk_connections` in `session_watchdog.py`.
- [ ] Module-level comment block in `session_watchdog.py` updated to list: "When detections fire, they AUTOMATICALLY steer (or teardown). See `_inject_loop_break_steer`." — removing the old "logged only" wording.

### External Documentation Site
- No Sphinx / Read the Docs site in this repo. Skip.

## Success Criteria

- [ ] `AgentSession.total_input_tokens`, `total_output_tokens`, `total_cache_read_tokens`, `total_cost_usd` exist and update monotonically across a session's turns.
- [ ] `/dashboard.json` exposes all four token fields per session, including for newly-created sessions (as 0).
- [ ] `monitoring/session_watchdog.py`: simulated `detect_repetition=True` results in exactly ONE `push_steering_message` call per cooldown window; second detection within 900s is suppressed; detection after 900s+ fires again.
- [ ] Simulated `detect_error_cascade=True` triggers exactly one steer via its own cooldown key.
- [ ] A session whose cumulative tokens exceed `TOKEN_ALERT_THRESHOLD` receives exactly one steer per `TOKEN_ALERT_COOLDOWN` window.
- [ ] A session in `dormant` status with `updated_at` >24h old gets its SDK client torn down; `sdk_connection_torn_down_at` is set; subsequent resume builds a fresh client and succeeds end-to-end.
- [ ] `WATCHDOG_AUTO_STEER_ENABLED=false` disables loop-break steering without disabling detection (still logged).
- [ ] `WATCHDOG_TOKEN_TRACKING_ENABLED=false` skips the accumulate call without crashing.
- [ ] `WATCHDOG_IDLE_TEARDOWN_ENABLED=false` skips teardown without crashing.
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

- **Builder (idle-teardown)**
  - Name: `teardown-builder`
  - Role: Add `_active_clients` registry + `_teardown_dormant_sdk_connections` pass + `sdk_connection_torn_down_at` field.
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

### 1. Add AgentSession token fields + sdk_client accumulator
- **Task ID**: build-token-accumulator
- **Depends On**: none
- **Validates**: `tests/unit/test_session_token_accumulator.py` (create), `tests/unit/test_agent_session_fields.py` (update)
- **Informed By**: spike-1 (confirmed ResultMessage.usage structure)
- **Assigned To**: token-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `total_input_tokens`, `total_output_tokens`, `total_cache_read_tokens`, `total_cost_usd` (all `IntField(default=0)` or `FloatField(default=0.0)`) to `models/agent_session.py`.
- Add `accumulate_session_tokens(session_id, input_tokens, output_tokens, cache_read_tokens, cost_usd)` function near `record_session_activity` in `sdk_client.py`. Use `save(update_fields=[...])`. Wrap in try/except ModelException with warning-level log.
- Hook into existing `ResultMessage` handler at `sdk_client.py:1216` — after the existing `total_cost_usd` capture, call `accumulate_session_tokens(...)` with values from `msg.usage`.
- Gate with `WATCHDOG_TOKEN_TRACKING_ENABLED` env var (default `true`).

### 2. Expose token fields on dashboard
- **Task ID**: build-dashboard-tokens
- **Depends On**: build-token-accumulator
- **Validates**: `tests/unit/test_dashboard_json.py` (create or extend)
- **Assigned To**: token-builder
- **Agent Type**: builder
- **Parallel**: false
- Add token fields to `PipelineProgress` dataclass in `ui/data/sdlc.py`.
- Populate them from `AgentSession` in the `PipelineProgress` reader.
- Add to `_session_to_json` in `ui/app.py:244`. Default to 0 (never None) for backward compat with existing consumers.

### 3. Wire loop-break steering
- **Task ID**: build-loop-break-steer
- **Depends On**: none
- **Validates**: `tests/unit/test_watchdog_loop_break_steer.py` (create), `tests/unit/test_session_watchdog.py` (update)
- **Informed By**: spike-3 (confirmed watchdog→steering cost is ~15 LoC per detection)
- **Assigned To**: steer-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_inject_loop_break_steer(session_id, reason, message_template)` in `session_watchdog.py`. Use Redis `SET NX EX 900` for atomic cooldown.
- Call it from the positive branches of `detect_repetition` (line ~403) and `detect_error_cascade` (line ~408).
- Compose distinct messages per reason (see Data Flow section above).
- Gate with `WATCHDOG_AUTO_STEER_ENABLED` env var (default `true`). When disabled: still log, don't push.
- Use separate cooldown keys: `watchdog:steer_cooldown:repetition:{session_id}` and `watchdog:steer_cooldown:error_cascade:{session_id}` so the two detections don't squelch each other.

### 4. Add token-threshold alert
- **Task ID**: build-token-threshold-alert
- **Depends On**: build-token-accumulator, build-loop-break-steer
- **Validates**: `tests/unit/test_watchdog_token_alert.py` (create)
- **Assigned To**: steer-builder
- **Agent Type**: builder
- **Parallel**: false
- In `_check_session_health`, after reading `AgentSession.total_input_tokens + total_output_tokens`, compare to `TOKEN_ALERT_THRESHOLD`.
- If exceeded AND status=running AND no cooldown, push steer `"Token budget exceeded: ${cost_usd:.2f} spent this session. Stop and summarize what you've done."` with cooldown key `watchdog:token_alert_cooldown:{session_id}` TTL 3600s.
- Uses the same `_inject_loop_break_steer` helper with a custom cooldown key.

### 5. Add idle-teardown pass
- **Task ID**: build-idle-teardown
- **Depends On**: none
- **Validates**: `tests/unit/test_watchdog_idle_teardown.py` (create)
- **Informed By**: spike-2 (teardown-on-dormant preferred over API probe)
- **Assigned To**: teardown-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `sdk_connection_torn_down_at = DatetimeField(null=True)` to `AgentSession`.
- Add `_active_clients: dict[str, ClaudeSDKClient]` registry at module scope in `sdk_client.py`. Populate on context-enter, remove on context-exit. Protect with a lock.
- Add `_teardown_dormant_sdk_connections(sessions)` pass in `session_watchdog.py`. Iterate `list(_active_clients.items())` snapshot. Filter to sessions in `{dormant, paused, paused_circuit}` with activity >24h old. Close the client. Set `sdk_connection_torn_down_at`.
- Gate with `WATCHDOG_IDLE_TEARDOWN_ENABLED` env var (default `true`).

### 6. Write test suite
- **Task ID**: test-watchdog-hardening
- **Depends On**: build-token-accumulator, build-dashboard-tokens, build-loop-break-steer, build-token-threshold-alert, build-idle-teardown
- **Assigned To**: watchdog-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Create/update the test files listed in Test Impact.
- Cover positive path, cooldown suppression, env-var disable, concurrent-save race.
- All tests use in-process Popoto + fakeredis — no network.

### 7. Validate integration end-to-end
- **Task ID**: validate-watchdog-hardening
- **Depends On**: test-watchdog-hardening
- **Assigned To**: watchdog-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full `pytest tests/unit/test_watchdog*.py tests/unit/test_session_*` suite.
- Hit `/dashboard.json` on a live worker, assert token fields present.
- Flip each env var off, restart worker, verify gating works.
- Simulate a repetition loop against a real (throwaway) session and observe the steering message in `valor-session status`.

### 8. Documentation
- **Task ID**: document-watchdog-hardening
- **Depends On**: validate-watchdog-hardening
- **Assigned To**: watchdog-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update the four feature docs listed in Documentation.
- Add env-var table to `docs/features/session-watchdog.md`.

### 9. Final validation
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
| Unit tests pass | `pytest tests/unit/test_watchdog_loop_break_steer.py tests/unit/test_session_token_accumulator.py tests/unit/test_watchdog_idle_teardown.py tests/unit/test_watchdog_token_alert.py tests/unit/test_session_watchdog.py -x -q` | exit code 0 |
| All unit tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| New AgentSession fields exist | `python -c "from models.agent_session import AgentSession; assert all(hasattr(AgentSession, f) for f in ['total_input_tokens', 'total_output_tokens', 'total_cache_read_tokens', 'total_cost_usd', 'sdk_connection_torn_down_at'])"` | exit code 0 |
| Token tracking wired into sdk_client | `grep -q 'accumulate_session_tokens' agent/sdk_client.py` | exit code 0 |
| Loop-break steer wired into watchdog | `grep -q '_inject_loop_break_steer' monitoring/session_watchdog.py` | exit code 0 |
| Idle teardown pass exists | `grep -q '_teardown_dormant_sdk_connections' monitoring/session_watchdog.py` | exit code 0 |
| Dashboard exposes tokens | `python -c "import json; from ui.app import _session_to_json" && grep -q 'total_input_tokens' ui/app.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Token threshold default (5M / $75 Sonnet).** Is 5M the right soft threshold, or should it be lower (1M / $15) to catch runaway loops earlier? Depends on observed p90 token spend — do we have that data from analytics? If not, start at 5M and tune down based on how often it fires.
2. **Idle teardown threshold (24h).** The 48h silent-death window gives us a 24h safety margin. Should we be more aggressive (12h) to reduce the window of risk, or less (36h) to reduce unnecessary teardowns for near-dormant sessions? Leaning 24h as the balance.
3. **Loop-break message phrasing.** The messages in Data Flow are my best guess. Should they be shorter / more authoritative / framed as the PM/operator? (e.g., "Stop. Summarize and try a different approach." vs. the longer templates I drafted.)
4. **Should Row 4 (token-threshold alert) escalate to Telegram after N steers in a session?** Current plan: steer only. If a session has been steered 3 times for token spend in a single day, that's probably a human-intervention moment. Do we want that escalation in v1 or follow-up?
