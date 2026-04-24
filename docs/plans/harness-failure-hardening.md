---
status: Planning
type: feature
appetite: Small
owner: Tom Counsell
created: 2026-04-24
tracking: https://github.com/tomcounsell/ai/issues/1099
last_comment_id:
---

# Harness Failure Hardening (four known modes)

## Problem

The `claude -p stream-json` subprocess — the execution engine for every Dev / PM / Teammate session — has six known failure modes in the wider Claude Code ecosystem. Valor already defends against two (safety-prompt blocking, ANSI-vs-stream-json state detection). Four remain, and in each the session silently degrades or misreports its status rather than failing cleanly:

1. **Thinking block corruption** — extended-thinking + compaction corrupts state. Both the primary harness call and the stale-UUID fallback exit non-zero. Caller sees `""` and the session is marked `completed`. The user gets silence.
2. **Context fills silently** — `message_delta`-style per-turn token counts ARE now accumulated into `AgentSession.total_input_tokens` (PR #1138) but no signal is emitted when the *current turn's* context usage crosses the danger zone. Operators learn about context pressure only after a session degrades.
3. **Post-compaction stall** — a `/compact` completes, the session goes idle, and the Tier 2 reprieve gate kills it as "no activity." The gate cannot see that a compaction just happened. `AgentSession.last_compaction_ts` IS now populated by `pre_compact_hook` (PR #1135) but `session_health.py` does not read it.
4. **OOM / SIGKILL** — `returncode == -9` is logged once in `_run_harness_subprocess` (line 2132) but never persisted. The re-queue path cannot tell "OS killed under memory pressure" from "health check intentionally killed," so memory-pressure re-queues thrash the system.

**Current behavior:**
- Mode 1: empty reply, status `completed`, no error surfaced.
- Mode 2: no `context_usage` log at 75%+ — operators have no early warning.
- Mode 3: Tier 2 reprieve gate ignores `last_compaction_ts` → false kill of a session that is legitimately post-compaction idle.
- Mode 4: `exit_returncode` is not stored on `AgentSession`; re-queue path has no signal to defer on memory pressure.

**Desired outcome:**
Each mode has a minimal, targeted, independently-shippable fix, each with a behavioral test that proves the recovery fires on the failure and does NOT trigger on healthy runs.

## Freshness Check

**Baseline commit:** `a8c3843f` (HEAD of `session/harness-failure-hardening` at plan time)
**Issue filed at:** 2026-04-21T10:13:51Z
**Disposition:** **Minor drift** — Mode 2 scope narrows; Modes 1/3/4 unchanged in intent, some infrastructure already landed.

**File:line references re-verified:**

| Issue said | Reality (verified 2026-04-24) | Disposition |
|---|---|---|
| `agent/sdk_client.py:1701` = stale-UUID fallback | Drifted — stale-UUID fallback now lives at `agent/sdk_client.py:1941-1977`; unchanged in logic | Minor drift — update file:line in Technical Approach |
| `agent/sdk_client.py:1742` = `claude -p stream-json` invocation | Drifted — subprocess launch now at `agent/sdk_client.py:2003` (`_run_harness_subprocess`); extended with 5-tuple return (`usage`, `cost_usd`) per PR #1138 | Minor drift |
| `agent/sdk_client.py:1820` = "`message_delta` parser loop" | Drifted — stream loop now at `agent/sdk_client.py:2074-2126`. The `result` event DOES now extract `usage` + `total_cost_usd` (lines 2106-2111) per PR #1138. `stream_event.message_delta` events remain unhandled | Minor drift — the accumulation half already exists, only the warning log is missing |
| `agent/session_health.py` Tier 2 reprieve gate | Present at `agent/session_health.py:517-579` (`_tier2_reprieve_signal`). Gates: `children`, `alive`, `stdout`. No `compacting` gate yet | Unchanged — this is the integration point for Mode 3 |
| `agent/hooks/pre_compact.py` | Rewrote as of PR #1135 (commit `a13b7470`). Now handles backup + cooldown + retention AND updates `AgentSession.last_compaction_ts` via `_update_session_cooldown` (`agent/hooks/pre_compact.py:131-175`) | **Major infrastructure already landed** — no new write from the hook is needed for Mode 3 |

**Cited sibling issues/PRs re-checked:**

- **PR #1039** (two-tier no-progress detector) — merged long ago. Fields `last_heartbeat_at`, `last_sdk_heartbeat_at`, `last_stdout_at` present on `AgentSession`. Foundation for Tier 2. Unchanged.
- **PR #1128 / #1138** (watchdog hardening) — merged 2026-04-23. Added `total_input_tokens`, `total_output_tokens`, `total_cache_read_tokens`, `total_cost_usd`, `sdk_connection_torn_down_at` to `AgentSession`. Added `accumulate_session_tokens` helper called from both SDK and harness paths. **This is the key overlap for Mode 2** — cumulative token accounting already exists; only the per-turn >75% warning log is new work.
- **PR #1135** (compaction hardening) — merged 2026-04-22. Added `last_compaction_ts`, `compaction_count`, `compaction_skipped_count` to `AgentSession`. Wired `pre_compact_hook` to update `last_compaction_ts` on every backup. **Key overlap for Mode 3** — the writer half is done; only the session_health Tier 2 read/reprieve is new work.

**Commits on main since issue was filed (touching referenced files):**
- `84d5fe7f` (PR #1138) — `agent/sdk_client.py` + `models/agent_session.py`: **partially addresses** Mode 2 (accumulation done; warning log not done)
- `a13b7470` (PR #1135) — `agent/hooks/pre_compact.py` + `models/agent_session.py`: **partially addresses** Mode 3 (hook writes `last_compaction_ts`; Tier 2 reprieve still missing)
- `f811eb2b`, `9935778d`, `b40a2b73`, `0ecac18a` — touched adjacent files, did not address the four modes.

**Active plans in `docs/plans/` overlapping this area:**
- `agentsession-harness-abstraction.md` — planning-stage refactor, not currently shipping.
- `cli_harness_full_migration.md` — older migration plan, not currently shipping.
- No active plan overlaps with any of the four concrete modes in this issue.

**Notes:** The reference field name `last_compact_at` in the issue's Mode 3 solution has already been implemented under the name `last_compaction_ts` (float, not `DatetimeField`). We use the existing field rather than adding a second one. The issue's Mode 4 solution called for `exit_returncode = IntField(null=True)` — this is **still new** and will be added.

## Prior Art

- **PR #1138** (merged 2026-04-23, closes #1128): Per-session token accumulation via `accumulate_session_tokens` in `agent/sdk_client.py`, plus idle teardown and loop-break steering. **Successful** — the cumulative accumulator works and is the foundation we layer the >75% warning on top of.
- **PR #1135** (merged 2026-04-22, compaction hardening): JSONL backup + 5-minute cooldown + `AgentSession.last_compaction_ts`. **Successful** — gives us the timestamp Mode 3 needs.
- **PR #1039** (two-tier no-progress detector): Tier 2 reprieve gate structure (`children`, `alive`, `stdout`). **Successful** — we extend this pattern with a `compacting` gate.
- **PR #957** (dead send_cb API removal), **PR #985** (harness FileNotFoundError retry), **PR #981** (session continuity via --resume): earlier harness hardening. Did not attempt any of the four modes in this issue.

No prior fix for any of the four modes has landed or been attempted — Modes 1, 2 (warning half), 3 (reprieve half), and 4 are greenfield additions.

## Research

External research was not performed — this is a purely internal change to the harness subprocess layer and the session health check. The amux blog post ("Every way Claude Code crashes") is already cited in the issue and no further external context is required. `psutil.virtual_memory()` semantics are well-documented in the psutil docs and already used in `monitoring/orphan_cleanup.py`.

## Data Flow

The four fixes touch three distinct paths. Each fix is independent — no ordering constraint between them beyond "tests for each fix live in separate files."

### Mode 1 — Thinking block corruption (read path)

```
1. `get_response_via_harness()` is called by session_executor / worker.
2. It calls `_run_harness_subprocess(cmd, working_dir, proc_env)`.
3. Inside `_run_harness_subprocess`, when the subprocess exits non-zero,
   `stderr_data` is decoded into `stderr_text` and logged — but NOT returned.
4. Today, the caller only sees `(result_text, session_id_from_harness, returncode, usage, cost_usd)`.
5. New: we widen the return tuple with `stderr_snippet` (first 2000 chars,
   truncated) so the caller can sentinel-match. Sentinel found → raise a
   typed exception (`HarnessThinkingBlockCorruption`) from `get_response_via_harness`.
6. The worker catches the exception and sets `status="failed"` via `finalize_session`,
   delivering the user-facing message "Session context corrupted — please start a new thread."
```

### Mode 2 — Context usage warning (streaming-loop observability)

```
1. `_run_harness_subprocess` already extracts `usage.input_tokens` from the
   `result` event (line 2106-2108).
2. New: after the result event is captured and the subprocess exits,
   `get_response_via_harness` computes `context_pct = input_tokens / context_window`
   using the model's context window from `config/models.py::MODELS[model]["context_window"]`.
3. If `context_pct > 0.75`, emit a single structured log record:
   `{"event": "context_usage", "pct": X, "session_id": Y, "model": M, "input_tokens": N}`.
4. No state change. No behavior change. Observability only.
```

### Mode 3 — Post-compaction reprieve (Tier 2 gate extension)

```
1. `pre_compact_hook` already writes `AgentSession.last_compaction_ts` (float
   epoch seconds) on every successful backup.
2. Health check loop (`_agent_session_health_check`) invokes Tier 1 → Tier 2
   reprieve gate (`_tier2_reprieve_signal`) on candidate kills.
3. Current Tier 2 gates: `children` → `alive` → `stdout`. No compaction gate.
4. New: prepend a `compacting` gate. If `entry.last_compaction_ts` is non-None
   AND `now - last_compaction_ts < STDOUT_FRESHNESS_WINDOW (600s)`, return
   "compacting" — reprieve the kill.
5. The `compacting` gate is evaluated BEFORE `children`/`alive`/`stdout` so
   the telemetry counter (`tier2_reprieve_total:compacting`) captures it
   distinctly for dashboards.
```

### Mode 4 — OOM backoff (re-queue path)

```
1. Subprocess returns `returncode == -9` (SIGKILL) from
   `_run_harness_subprocess`. `get_response_via_harness` already receives it
   via the return tuple.
2. New: non-blocking best-effort write of `AgentSession.exit_returncode = -9`
   via `save(update_fields=["exit_returncode"])` inside try/except.
3. In the health-check recovery branch (`session_health.py:921-947` where
   `transition_status(entry, "pending", ...)` is called), before the
   transition: if `entry.exit_returncode == -9` AND `entry.recovery_attempts == 0`
   AND `psutil.virtual_memory().available < 400MB`, skip the immediate re-queue
   and instead set `entry.started_at = time.time() + 120` as a delayed retry.
   (The worker's pending-scan logic already honors future `started_at` on some
   paths; if not, we add a `retry_after_ts` field — see Spike spike-1 below.)
```

### Spike Results

#### spike-1: Does the existing pending-scan honor a future `started_at`?

- **Assumption**: We can implement the 120s OOM backoff by setting `started_at` to a future epoch.
- **Method**: code-read — `agent/agent_session_queue.py` + `worker/__main__.py` scan of pending sessions.
- **Finding**: `started_at` is set to `None` on recovery-to-pending (`session_health.py:926`) and written at execution start. It is NOT polled as a "not before" timestamp in the pending scan. **Setting it to a future time would be a silent no-op at best and a correctness bug at worst** (health check could re-trigger re-queue on a "legacy session" branch because `started_ts is None` is its "no progress" signal for legacy sessions).
- **Confidence**: high
- **Impact on plan**: Add a new `retry_after_ts = FloatField(default=None)` on `AgentSession`. Pending-scan + recovery path checks it before promoting to `running`. This is a minimal (1 field, 2 read sites) addition — simpler than repurposing `started_at`.

#### spike-2: Is `stderr_text` truncation at 2000 chars safe for sentinel matching?

- **Assumption**: The `THINKING_BLOCK_SENTINEL` ("redacted_thinking … cannot be modified" per amux blog) will appear within the first 2000 chars of stderr.
- **Method**: code-read — amux blog excerpt, anthropic API docs (unconfirmed). The issue explicitly notes the sentinel is unconfirmed.
- **Finding**: The amux report shows the sentinel near the top of stderr in their reproduction. Claude CLI error messages are typically short (<1 KB). A 2000-char window is generous. We already log only `stderr_text[:500]` (line 2132), which is tighter. Using 2000 for the sentinel check gives us 4× safety margin without introducing memory bloat.
- **Confidence**: medium (sentinel string unconfirmed by Anthropic docs; accepting amux's observation as authoritative, as the issue does).
- **Impact on plan**: Truncate stderr to 2000 chars before sentinel check. Use `in` operator (substring match), not regex, to minimize false-positive surface. Require sentinel in stderr **AND** returncode non-zero (not 0) — a healthy session never produces the sentinel on stderr.

## Architectural Impact

- **New dependencies**: none. `psutil` is already a project dependency (used in `monitoring/orphan_cleanup.py` and `agent/session_health.py`).
- **Interface changes**:
  - `_run_harness_subprocess` return tuple widens from 5 to 6 elements: adds `stderr_snippet: str | None`. All three call sites in `get_response_via_harness` must unpack accordingly.
  - `get_response_via_harness` public signature unchanged (still `-> str`). On thinking-block corruption it raises `HarnessThinkingBlockCorruption` (new typed exception); the caller is responsible for catching and finalizing to `failed`.
- **Coupling**: Mode 3 couples `session_health.py` to `last_compaction_ts` (new read). Already coupled to `last_stdout_at`, `last_heartbeat_at`, etc. — low additional coupling.
- **Data ownership**: `pre_compact_hook` owns writes to `last_compaction_ts` (already wired). `_run_harness_subprocess` owns writes to `exit_returncode` (new). Health check owns writes to `retry_after_ts` (new) + reads of `last_compaction_ts` + `exit_returncode`.
- **Reversibility**: All four fixes are independently reversible. Each behind an env-flag gate would be trivial; we instead use unconditional code with behavioral tests asserting both fire-and-don't-fire paths.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (well-scoped, no open questions)
- Review rounds: 1 (standard /do-pr-review)

Rationale: each mode is a 20–60 line change to one file plus one AgentSession field plus one test file. Total estimated diff: ~300 LOC production + ~400 LOC tests. Small by any measure.

## Prerequisites

No prerequisites — all required infrastructure (Popoto ORM, `psutil`, `asyncio`, `subprocess`, `stream-json` parser) is already in place.

## Solution

### Key Elements

- **Mode 1 — Thinking block sentinel**: A new `THINKING_BLOCK_SENTINEL` constant + `HarnessThinkingBlockCorruption` exception in `agent/sdk_client.py`. `_run_harness_subprocess` returns `stderr_snippet`; `get_response_via_harness` raises when it matches.
- **Mode 2 — Context usage warning log**: A new `_log_context_usage_if_risky(session_id, model, usage_input_tokens)` helper in `agent/sdk_client.py`, called once per turn from `get_response_via_harness` after the subprocess exits. No state change.
- **Mode 3 — Tier 2 `compacting` reprieve gate**: Add a `compacting` branch to `_tier2_reprieve_signal` in `agent/session_health.py`. Reads `entry.last_compaction_ts`.
- **Mode 4 — OOM backoff**: New `AgentSession` fields `exit_returncode: IntField(null=True, default=None)` and `retry_after_ts: FloatField(default=None)`. Harness writes `exit_returncode`. Health check's recovery branch checks the OOM condition and defers by setting `retry_after_ts = now + 120`. Pending-scan and recovery-to-pending logic respect `retry_after_ts`.

### Flow

```
Operator opens Telegram → Dev session → Claude session hits
  [Mode 1: thinking corruption] → stderr sentinel → user sees explicit error (not silence)
  [Mode 2: 80% context]        → WARNING log for dashboard + grep
  [Mode 3: /compact fires]      → compaction → pre_compact_hook sets last_compaction_ts
                                                → session goes idle briefly
                                                → Tier 2 reprieve says "compacting" — no false kill
  [Mode 4: OOM kill]            → returncode=-9 stored + 120s backoff if memory tight
                                                (but normal re-queue if health-check-initiated)
```

### Technical Approach

#### Mode 1

- Add module-level constant `THINKING_BLOCK_SENTINEL = "redacted_thinking"` (substring — amux's fuller phrase is `"redacted_thinking … cannot be modified"` but the shorter prefix is strictly more conservative and still unlikely to appear in healthy stderr).
- Add `class HarnessThinkingBlockCorruption(Exception)` at module scope in `agent/sdk_client.py`.
- Widen `_run_harness_subprocess` return tuple from 5-tuple to 6-tuple by appending `stderr_snippet: str | None = stderr_text[:2000] if returncode != 0 else None`.
- Update the three call sites in `get_response_via_harness` (lines 1892-1904, 1918-1930, 1959-1971) to unpack the 6-tuple.
- After the stale-UUID fallback completes, before returning: if the final `stderr_snippet` contains `THINKING_BLOCK_SENTINEL` AND `returncode != 0`, raise `HarnessThinkingBlockCorruption("Session context corrupted — please start a new thread")`.
- In `agent/session_executor.py` (wherever `get_response_via_harness` is awaited in the main execute path — identified during build by grep), catch `HarnessThinkingBlockCorruption` and call `finalize_session(session, "failed", reason=str(exc))`.

#### Mode 2

- In `config/models.py`, `MODELS[name]["context_window"]` already exists (lines 161, 178, 196, 214). Confirm the public reader signature by grep — likely `get_model_context_window(model_name)` or direct dict access.
- In `agent/sdk_client.py`, add a module-level helper:
  ```python
  def _log_context_usage_if_risky(session_id, model, usage) -> None:
      # Defensive: usage may be None or missing input_tokens
      try:
          input_tokens = int((usage or {}).get("input_tokens", 0) or 0)
          if input_tokens <= 0: return
          window = _get_model_context_window(model)  # from config/models.py
          if not window: return
          pct = input_tokens / window
          if pct > 0.75:
              logger.warning("context_usage pct=%.2f session_id=%s model=%s input_tokens=%d",
                             pct, session_id, model, input_tokens)
      except Exception:
          return  # observability must never crash the turn
  ```
- Call it from `get_response_via_harness` after `accumulate_session_tokens` (existing line 1990), using the same `usage` dict and the `model` argument already in scope.
- Note: the emitted log record is `logger.warning` at the string level, NOT a raw JSON dict. This matches the rest of the codebase's logging conventions (grep-friendly structured-prefix style). Dashboard/grep can pick it up via `grep "context_usage"`.

#### Mode 3

- In `agent/session_health.py::_tier2_reprieve_signal`, PREPEND a `compacting` gate before the existing (c)/(d)/(e) gates:
  ```python
  # (b) compacting — reprieve if a compaction completed in the last 600s.
  lct = getattr(entry, "last_compaction_ts", None)
  if lct is not None:
      try:
          if (time.time() - float(lct)) < STDOUT_FRESHNESS_WINDOW:
              return "compacting"
      except (TypeError, ValueError):
          pass
  ```
- Order: `compacting` → `children` → `alive` → `stdout`. Telemetry counter string extends: `tier2_reprieve_total:compacting`.
- Add an `import time` if not already present at the top of `session_health.py`. (It is — confirmed via grep.)

#### Mode 4

- `models/agent_session.py`: add two fields
  ```python
  exit_returncode = IntField(null=True, default=None)   # last subprocess exit code, for OOM detection
  retry_after_ts = FloatField(default=None)             # epoch second at which a deferred re-queue becomes eligible
  ```
  Add them to the safe-to-save list at line 361 if that list is the update-fields allowlist. (Confirmed — see lines 361-365 in recon. Add `exit_returncode`, `retry_after_ts`.)
- In `agent/sdk_client.py::get_response_via_harness`, right after the final `_run_harness_subprocess` call completes (after the stale-UUID fallback), if `session_id` is non-None and `returncode is not None`:
  ```python
  try:
      from models.agent_session import AgentSession
      for s in AgentSession.query.filter(session_id=session_id):
          s.exit_returncode = int(returncode)
          s.save(update_fields=["exit_returncode"])
          break
  except Exception as _e:
      logger.debug("exit_returncode store failed for session_id=%s: %s", session_id, _e)
  ```
  This follows the same best-effort pattern as `_store_claude_session_uuid` and `accumulate_session_tokens`.
- In `agent/session_health.py`, in the recovery-to-pending branch (around line 921-947), before calling `transition_status(entry, "pending", ...)`:
  ```python
  # Mode 4: OOM defer — if the OS killed the subprocess AND this is not a
  # health-check kill AND memory is tight, defer the re-queue by 120s.
  if (entry.exit_returncode == -9
          and entry.recovery_attempts == 0
          and _is_memory_tight()):
      entry.retry_after_ts = time.time() + 120.0
      entry.save(update_fields=["retry_after_ts"])
      logger.warning(
          "[session-health] OOM backoff: deferring %s for 120s "
          "(exit_returncode=-9, memory<400MB)",
          entry.agent_session_id,
      )
  ```
  where `_is_memory_tight()` wraps `psutil.virtual_memory().available < 400 * 1024 * 1024` inside try/except (returning False on any error — fail-open so the backoff is skipped if psutil misbehaves).

  **Guard**: the increment `entry.recovery_attempts = ...` at line 873 happens BEFORE this block. This means by the time we read `entry.recovery_attempts`, it is always `>= 1` (never 0) — making the condition `recovery_attempts == 0` impossible to hit by the health check itself. The correct reading: the issue's "recovery_attempts == 0" means "no prior recovery has run." So we must consult `entry.recovery_attempts` BEFORE it is incremented (or compare against 1 since this is the first increment). Resolution: move the OOM-defer check to BEFORE the `entry.recovery_attempts = (entry.recovery_attempts or 0) + 1` bump, or explicitly use `(entry.recovery_attempts or 0) - 1 == 0` which is equivalent to "this is the first recovery attempt." We'll use the former — move the check before the bump — because it is clearer.
- In the pending-scan (`_agent_session_health_check` RUNNING + PENDING branches) and in the recovery-to-pending branch, respect `retry_after_ts`:
  - If `entry.retry_after_ts` is set and `time.time() < entry.retry_after_ts`, skip the session this tick. Log at `debug`.

### Thread-safety note (Mode 4)

The OOM backoff logic is a single-write, single-read sequence entirely inside the health check's own coroutine. No concurrent writes from other processes touch `retry_after_ts`. No locking needed beyond Popoto's default `save(update_fields=...)` partial-save.

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] Mode 1: The `except Exception: pass`-equivalent in `_run_harness_subprocess` (line 2132 logs and continues). We assert the sentinel path raises `HarnessThinkingBlockCorruption` explicitly — behavioral observable.
- [x] Mode 2: The helper wraps its body in `try/except: return`. We assert healthy runs and malformed-usage runs do NOT crash by constructing a mock run with `usage=None`.
- [x] Mode 3: `_tier2_reprieve_signal` already has fail-soft error handling. New gate adds `(TypeError, ValueError)` catches on the `float(lct)` coercion.
- [x] Mode 4: `exit_returncode` write is wrapped; OOM check is wrapped in `_is_memory_tight()` which catches everything and returns False.

### Empty/Invalid Input Handling
- [x] Mode 1: stderr empty → `stderr_snippet is None` → sentinel check is a no-op.
- [x] Mode 2: `usage=None`, `input_tokens=0`, `window=None` → all branches return early, no log, no raise.
- [x] Mode 3: `last_compaction_ts=None` → gate returns None, falls through to existing gates.
- [x] Mode 4: `exit_returncode=None`, `recovery_attempts=None`, memory check raises → no backoff, normal recovery.

### Error State Rendering
- [x] Mode 1: User-visible error "Session context corrupted — please start a new thread" rendered via `task.error` → telegram message drafter. Assert the message is delivered (not silenced).

## Test Impact

- [ ] `tests/unit/test_sdk_client_image_sentinel.py` — **UPDATE**: tuple unpacking tests must shift from 5-tuple to 6-tuple (`stderr_snippet` added). The existing tests mock `_run_harness_subprocess` return — update all mocks to return 6-tuples.
- [ ] `tests/unit/test_session_watchdog.py` — **no change expected**: this file tests the bridge-hosted watchdog. Mode 3 touches worker-hosted `session_health.py`, which has its own test file.
- [ ] `tests/unit/test_session_health_phantom_guard.py` — **no change expected**: we add a new reprieve gate; existing phantom-guard tests exercise an unrelated branch.
- [ ] `tests/unit/test_health_check_recovery_finalization.py` — **UPDATE**: if it constructs an `AgentSession` via the finalize path, the new fields (`exit_returncode`, `retry_after_ts`) have `default=None` so no test break is expected. Verify at build time that adding new fields doesn't break `to_dict()` / `from_dict()` assumptions.

Greenfield tests to add (4 new test files):
- `tests/unit/test_harness_thinking_block_sentinel.py` (Mode 1) — 4 cases
- `tests/unit/test_harness_context_usage_log.py` (Mode 2) — 3 cases
- `tests/unit/test_session_health_compacting_reprieve.py` (Mode 3) — 3 cases
- `tests/unit/test_harness_oom_backoff.py` (Mode 4) — 4 cases

All new tests use `project_key="test-harness-hardening-{mode}"` and clean up via `AgentSession.query.filter(project_key=...).delete()` in a pytest fixture teardown (pattern from existing `test_session_watchdog.py`).

## Rabbit Holes

- **Replay after thinking-block corruption** (extract last message from transcript, re-spawn session). The issue explicitly defers this. Keep deferred — the detection work is the whole scope.
- **Proactive `/compact` injection at 20% context**. Also explicitly deferred. Observability log (Mode 2) is the full scope.
- **Rewriting the Tier 2 gate into a registry pattern** to make adding new reprieves easier. Tempting but over-engineering for one new gate. Keep the imperative if-chain.
- **Adding a full OOM dashboard (metrics, alerts, auto-scaling hooks)**. Deferred — single `exit_returncode` field + 120s backoff is the entire scope.
- **Introducing a new `retry_after_ts` scheduler loop to "wake up" pending sessions at exactly the right time**. We piggyback on the existing health check tick — it re-evaluates pending sessions every cycle, and deferral naturally times out when `time.time() >= retry_after_ts`. No new scheduler.
- **Measuring `total_input_tokens / context_window` (cumulative) in Mode 2**. Wrong denominator — cumulative tokens exceed context window after many turns. Use per-turn `usage.input_tokens` only.

## Risks

### Risk 1: Sentinel false-positive (Mode 1)
**Impact:** A healthy session's stderr could, in principle, contain the string `"redacted_thinking"` (e.g., a tool that dumps diagnostic output). We would incorrectly raise `HarnessThinkingBlockCorruption` and fail a working session.
**Mitigation:** Require BOTH conditions: (a) stderr contains `THINKING_BLOCK_SENTINEL` AND (b) returncode != 0. A healthy session exits 0 and never triggers. Test asserts the conjunction explicitly (healthy-run-with-sentinel-in-stderr → no raise — but this is a synthetic scenario; real sessions don't emit the sentinel + returncode=0).

### Risk 2: Mode 3 reprieve outlives legitimate kill (false negative)
**Impact:** A session that had `/compact` 500s ago but is genuinely hung for other reasons gets reprieved for ~100 more seconds.
**Mitigation:** The 600s window is tight (10 min). Worst case is a 600s delay on kill of a genuinely stuck session. This is acceptable — the existing timeout path (recovery from `exceeded timeout`) will still fire at the session-level timeout boundary. Test asserts kill proceeds normally when `last_compaction_ts` is stale (> 600s).

### Risk 3: Mode 4 OOM defer loops (persistent memory pressure)
**Impact:** If the machine stays under 400MB of available memory, every re-queue gets deferred 120s. Session could be delayed indefinitely.
**Mitigation:** The defer is conditioned on `recovery_attempts == 0` (first attempt only). A second recovery attempt bypasses the OOM defer and proceeds normally. At MAX_RECOVERY_ATTEMPTS, session finalizes as `failed` — same backstop as today. Test asserts `recovery_attempts >= 1` → no defer.

### Risk 4: `retry_after_ts` is not consulted by all re-queue paths
**Impact:** If a code path promotes a session to `running` without consulting `retry_after_ts`, the defer is silently lost.
**Mitigation:** Grep during build for every call site that moves a session from `pending` → `running`. Confirmed paths from recon: `_ensure_worker` + the worker's pending drain loop. Build task explicitly enumerates them. A validator subagent confirms the field is consulted at each site.

## Race Conditions

### Race 1: Concurrent `exit_returncode` write + health-check read (Mode 4)
**Location:** `agent/sdk_client.py` write vs. `agent/session_health.py` read
**Trigger:** Subprocess exits and writes `exit_returncode` while the health check is already mid-evaluation of the same session.
**Data prerequisite:** The health check tick interval is 30s; a single subprocess exit completes in milliseconds. The write completes before the health check's next tick almost certainly.
**State prerequisite:** `exit_returncode` must be set BEFORE `recovery_attempts` is bumped for the OOM-defer branch to fire.
**Mitigation:** The health check re-reads `entry` fresh from Redis at the start of each iteration. Worst case: the subprocess-exit write races the health-check tick; the tick sees `exit_returncode=None` and skips the defer branch. The session reaches the normal recovery-to-pending path — no correctness bug, just a missed defer opportunity. Next tick will have `exit_returncode=-9` but `recovery_attempts=1` → no defer (correctly). Acceptable.

### Race 2: `pre_compact_hook` writes `last_compaction_ts` while health check reads it (Mode 3)
**Location:** `agent/hooks/pre_compact.py` write vs. `agent/session_health.py` read
**Trigger:** Compaction starts and the hook runs; health check tick fires mid-write.
**Mitigation:** Float field write is atomic (single Redis `HSET`). Read returns either the old value (None or stale timestamp) or the fresh value. Either produces correct behavior: stale/None → reprieve skipped → session may be killed (but only if genuinely stuck for other reasons); fresh → reprieve fires correctly.

### Race 3: 6-tuple unpack migration (Mode 1)
**Location:** `agent/sdk_client.py:1892`, `:1918`, `:1959` call sites
**Trigger:** Partial rollout where the function returns a 6-tuple but a call site still unpacks a 5-tuple.
**Mitigation:** This is a deploy-time risk, not a runtime race. Build task updates all three call sites in the same commit as the function signature change. Test (`test_sdk_client_image_sentinel.py`) exercises all three call sites with 6-tuple mocks; CI catches any missed site.

## No-Gos (Out of Scope)

- Thinking-block replay (extract+re-spawn). Explicitly deferred per the issue.
- Proactive `/compact` injection. Explicitly deferred per the issue.
- Reorganizing Tier 2 gate into a registry pattern.
- Adding a dedicated OOM metrics dashboard.
- Hardening the two failure modes already handled (safety-prompt blocking, state detection).
- Touching the SDK path (`agent/sdk_client.py::query_with_client` / `ClaudeSDKClient`). All four modes are harness-path-only — PM/Dev/Teammate sessions use the harness exclusively.

## Update System

No update system changes required — these are purely internal changes to the agent execution layer and health check. No new dependencies, no new config files, no env vars. The `/update` skill pulls the latest `main` and restarts services; this feature is picked up automatically.

## Agent Integration

No agent integration required — these are bridge-internal / worker-internal changes. The agent (Claude) does not need to know about thinking-block corruption recovery, context-usage warnings, compaction reprieves, or OOM backoff. They all operate beneath the agent at the subprocess + health-check layer.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/agent-session-health-monitor.md` — add the `compacting` gate to the Tier 2 reprieve list (Mode 3) and add an "OOM backoff" subsection (Mode 4).
- [ ] Update `docs/features/session-recovery-mechanisms.md` — add a "Thinking-block corruption" subsection (Mode 1) and a "Context-usage observability" subsection (Mode 2).
- [ ] No new feature doc required — these four fixes are defenses on existing features (harness execution + session health), not a new feature in their own right. An entry in the index table would misrepresent the scope.

### External Documentation Site
- N/A — this repo has no external docs site.

### Inline Documentation
- [ ] Docstrings on `_log_context_usage_if_risky`, `HarnessThinkingBlockCorruption`, `_is_memory_tight` explaining trigger conditions and failure handling.
- [ ] Docstring on the new `compacting` gate in `_tier2_reprieve_signal` citing the issue number (#1099) and the `pre_compact_hook` as the companion writer.
- [ ] Code comments on the three `_run_harness_subprocess` call sites noting the 6-tuple return.
- [ ] `models/agent_session.py` field comments explaining `exit_returncode` = last subprocess exit code (for OOM detection) and `retry_after_ts` = epoch second for deferred re-queue eligibility.

## Success Criteria

- [ ] Mode 1: `THINKING_BLOCK_SENTINEL` in stderr + non-zero returncode → `HarnessThinkingBlockCorruption` raised → session finalized as `failed` with user-visible error. Healthy run (returncode=0, no sentinel) returns a non-empty result and status `completed`. (Test: `test_harness_thinking_block_sentinel.py`)
- [ ] Mode 2: `usage.input_tokens / context_window > 0.75` → a single `logger.warning("context_usage ...")` record emitted per turn. Result text, session status, returncode, and all other behavior unchanged. (Test: `test_harness_context_usage_log.py`)
- [ ] Mode 3: `last_compaction_ts` within 600s → Tier 2 returns `"compacting"` → kill skipped. `last_compaction_ts` stale (>600s) or None → existing gate chain runs unchanged. (Test: `test_session_health_compacting_reprieve.py`)
- [ ] Mode 4: `exit_returncode == -9` AND `recovery_attempts == 0` AND `psutil.virtual_memory().available < 400MB` → `retry_after_ts = now + 120`. Any condition unmet → normal recovery. (Test: `test_harness_oom_backoff.py`)
- [ ] All new fields (`exit_returncode`, `retry_after_ts`) default to sensible values (`None`). Existing sessions loaded from Redis deserialize correctly without migration.
- [ ] No raw Redis operations introduced. All AgentSession writes use `save(update_fields=[...])` pattern. (Enforced by `validate_no_raw_redis_delete.py` hook.)
- [ ] `python -m ruff format --check .` passes.
- [ ] Affected test files green: `pytest tests/unit/test_sdk_client_image_sentinel.py tests/unit/test_harness_thinking_block_sentinel.py tests/unit/test_harness_context_usage_log.py tests/unit/test_session_health_compacting_reprieve.py tests/unit/test_harness_oom_backoff.py` all pass.
- [ ] Full unit suite (`pytest tests/unit/ -n auto`) remains green.
- [ ] Documentation updates (`docs/features/agent-session-health-monitor.md`, `docs/features/session-recovery-mechanisms.md`) landed in the same PR.

## Team Orchestration

### Team Members

- **Builder (harness hardening)**
  - Name: `harness-builder`
  - Role: Implement all four modes' production code changes + inline docstrings.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (harness hardening)**
  - Name: `harness-test-engineer`
  - Role: Write the four new test files + update `test_sdk_client_image_sentinel.py` for 6-tuple.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (harness hardening)**
  - Name: `harness-validator`
  - Role: Read-only verification — all call sites of `_run_harness_subprocess` updated, all `retry_after_ts` read sites present, documentation updated, no raw Redis introduced.
  - Agent Type: validator
  - Resume: true

- **Documentarian (harness hardening)**
  - Name: `harness-documentarian`
  - Role: Surgical updates to `docs/features/agent-session-health-monitor.md` and `docs/features/session-recovery-mechanisms.md`.
  - Agent Type: documentarian
  - Resume: true

### Step by Step Tasks

### 1. Add two fields to AgentSession
- **Task ID**: build-agentsession-fields
- **Depends On**: none
- **Validates**: `tests/unit/test_harness_oom_backoff.py` (create), existing unit tests still green
- **Assigned To**: `harness-builder`
- **Agent Type**: builder
- **Parallel**: true
- Add `exit_returncode = IntField(null=True, default=None)` to `models/agent_session.py`.
- Add `retry_after_ts = FloatField(default=None)` to `models/agent_session.py`.
- Add both to any update-fields allowlist (reference: lines 361-365, currently whitelists `last_heartbeat_at` etc.).
- Add explanatory inline comments citing issue #1099 and the OOM-backoff design.

### 2. Mode 1 — Thinking block sentinel in sdk_client
- **Task ID**: build-mode-1
- **Depends On**: none
- **Validates**: `tests/unit/test_harness_thinking_block_sentinel.py` (create), `tests/unit/test_sdk_client_image_sentinel.py` (update for 6-tuple)
- **Assigned To**: `harness-builder`
- **Agent Type**: builder
- **Parallel**: true
- Add `THINKING_BLOCK_SENTINEL = "redacted_thinking"` module-level constant in `agent/sdk_client.py`.
- Add `class HarnessThinkingBlockCorruption(Exception)` in `agent/sdk_client.py`.
- Widen `_run_harness_subprocess` return type to 6-tuple: `(result_text, session_id_from_harness, returncode, usage, cost_usd, stderr_snippet)`.
- Populate `stderr_snippet = stderr_text[:2000] if returncode != 0 else None`.
- Update all three call sites in `get_response_via_harness` (current lines 1892-1904, 1918-1930, 1959-1971) to unpack the 6-tuple.
- After the stale-UUID fallback, if sentinel in final `stderr_snippet` AND `returncode != 0`, raise `HarnessThinkingBlockCorruption("Session context corrupted — please start a new thread")`.
- In `agent/session_executor.py` main execute path (identify via `grep 'get_response_via_harness' agent/session_executor.py`), wrap in try/except `HarnessThinkingBlockCorruption` and finalize the session as `failed` with the exception message as the reason.

### 3. Mode 2 — Context usage warning log
- **Task ID**: build-mode-2
- **Depends On**: none
- **Validates**: `tests/unit/test_harness_context_usage_log.py` (create)
- **Assigned To**: `harness-builder`
- **Agent Type**: builder
- **Parallel**: true
- Add `_log_context_usage_if_risky(session_id, model, usage)` helper in `agent/sdk_client.py` (see Technical Approach for signature + body).
- In `config/models.py`, confirm a public accessor for `context_window` or fall back to direct dict access. Prefer adding `def get_model_context_window(model_name: str) -> int | None` if not present — keeps `sdk_client.py` decoupled from the dict layout.
- Call the helper from `get_response_via_harness` right after `accumulate_session_tokens` (current line 1990). No exception may escape — wrapped in try/except already.

### 4. Mode 3 — Compacting reprieve gate
- **Task ID**: build-mode-3
- **Depends On**: none
- **Validates**: `tests/unit/test_session_health_compacting_reprieve.py` (create)
- **Assigned To**: `harness-builder`
- **Agent Type**: builder
- **Parallel**: true
- In `agent/session_health.py::_tier2_reprieve_signal`, prepend the `compacting` gate as described in Technical Approach.
- Ensure `import time` is present at top of file (already is — verify).
- Extend the telemetry docstring for `_tier2_reprieve_signal` to include `compacting` in the gate list + reference issue #1099 + companion writer `pre_compact_hook`.

### 5. Mode 4 — OOM backoff + retry_after_ts reads
- **Task ID**: build-mode-4
- **Depends On**: build-agentsession-fields
- **Validates**: `tests/unit/test_harness_oom_backoff.py` (create)
- **Assigned To**: `harness-builder`
- **Agent Type**: builder
- **Parallel**: false (needs fields first)
- In `agent/sdk_client.py::get_response_via_harness` (right after the stale-UUID fallback path finishes), add the best-effort `exit_returncode` write as described in Technical Approach.
- In `agent/session_health.py`, add `_is_memory_tight()` helper (top-level function; wraps psutil.virtual_memory inside try/except; threshold 400MB).
- In the recovery-to-pending branch (around current line 920), BEFORE `entry.recovery_attempts = (entry.recovery_attempts or 0) + 1`, check: if `entry.exit_returncode == -9` AND `entry.recovery_attempts == 0` AND `_is_memory_tight()`: set `entry.retry_after_ts = time.time() + 120.0`, save, log warning, skip to next session (do NOT bump recovery_attempts, do NOT transition).
- Identify every path that moves a session from `pending` → `running`:
  - `_ensure_worker` in `agent/agent_session_queue.py`
  - worker pending-drain loop in `worker/__main__.py` (grep for `status="pending"`)
  - Any recovery-to-pending write in `session_health.py` (just added)
- At each such path, add a guard: if `entry.retry_after_ts is not None and time.time() < entry.retry_after_ts`: skip this session this tick (logger.debug).

### 6. Tests (4 new files + 1 update)
- **Task ID**: build-tests
- **Depends On**: build-agentsession-fields, build-mode-1, build-mode-2, build-mode-3, build-mode-4
- **Validates**: `pytest tests/unit/test_harness_thinking_block_sentinel.py tests/unit/test_harness_context_usage_log.py tests/unit/test_session_health_compacting_reprieve.py tests/unit/test_harness_oom_backoff.py tests/unit/test_sdk_client_image_sentinel.py -q`
- **Assigned To**: `harness-test-engineer`
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_harness_thinking_block_sentinel.py` — 4 cases: (a) sentinel in stderr + returncode=1 → HarnessThinkingBlockCorruption raised; (b) caller sees non-empty error message (not `""`); (c) AgentSession.status == "failed" after caller handling; (d) healthy run (returncode=0, no sentinel) → no raise, normal return.
- Create `tests/unit/test_harness_context_usage_log.py` — 3 cases: (a) mock usage with input_tokens=160000 and context_window=200000 → logger.warning emitted with "context_usage"; (b) input_tokens=50000 → no warning; (c) usage=None → no warning, no raise.
- Create `tests/unit/test_session_health_compacting_reprieve.py` — 3 cases: (a) last_compaction_ts = now - 60s, last_stdout_at = now - 700s → reprieve returns "compacting"; (b) last_compaction_ts = now - 700s → no compacting reprieve, falls through; (c) last_compaction_ts = None → no compacting reprieve, falls through.
- Create `tests/unit/test_harness_oom_backoff.py` — 4 cases: (a) exit_returncode=-9, recovery_attempts=0, memory<400MB → retry_after_ts set to ~now+120; (b) exit_returncode=-9, recovery_attempts=1, memory<400MB → no defer, normal recovery; (c) exit_returncode=-9, recovery_attempts=0, memory>1GB → no defer; (d) exit_returncode=0, recovery_attempts=0, memory<400MB → no defer.
- Update `tests/unit/test_sdk_client_image_sentinel.py` — replace all 5-tuple mocks with 6-tuple mocks (add `None` as the stderr_snippet element).
- All new tests use `project_key="test-harness-hardening-{mode}"` and clean up AgentSession records via Popoto ORM in a fixture teardown (pattern: `AgentSession.query.filter(project_key=k).delete()`). NO raw Redis.

### 7. Documentation updates
- **Task ID**: document-feature
- **Depends On**: build-mode-1, build-mode-2, build-mode-3, build-mode-4, build-tests
- **Validates**: both doc files contain the new sections; links remain valid
- **Assigned To**: `harness-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/agent-session-health-monitor.md` with the `compacting` Tier 2 gate (Mode 3) and a brief OOM-backoff subsection (Mode 4) noting `exit_returncode`/`retry_after_ts`.
- Update `docs/features/session-recovery-mechanisms.md` with a "Thinking-block corruption" subsection (Mode 1) and a "Context-usage observability" subsection (Mode 2).
- Add a one-line entry to `docs/features/README.md` under the harness or session-health row linking back to the updated feature docs.

### 8. Validation
- **Task ID**: validate-all
- **Depends On**: build-agentsession-fields, build-mode-1, build-mode-2, build-mode-3, build-mode-4, build-tests, document-feature
- **Assigned To**: `harness-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table below.
- Grep-verify all three `_run_harness_subprocess` call sites unpack a 6-tuple.
- Grep-verify every `pending` → `running` path checks `retry_after_ts`.
- Confirm no new raw Redis operations (`r.hget`, `r.hset`, `r.delete` on AgentSession keys) introduced.
- Confirm `logger.warning("context_usage ...")` format is grep-friendly.
- Confirm the `compacting` gate is the first branch of `_tier2_reprieve_signal`.

## Verification

| Check | Command | Expected |
|---|---|---|
| Format clean | `python -m ruff format --check .` | exit code 0 |
| New tests pass | `pytest tests/unit/test_harness_thinking_block_sentinel.py tests/unit/test_harness_context_usage_log.py tests/unit/test_session_health_compacting_reprieve.py tests/unit/test_harness_oom_backoff.py -q` | exit code 0 |
| Updated image-sentinel test passes | `pytest tests/unit/test_sdk_client_image_sentinel.py -q` | exit code 0 |
| Full unit suite green | `pytest tests/unit/ -n auto -q` | exit code 0 |
| 6-tuple unpacking complete | `grep -n '_run_harness_subprocess' agent/sdk_client.py` | output contains 3+ call sites all unpacking 6 values |
| `retry_after_ts` consulted in recovery | `grep -n 'retry_after_ts' agent/session_health.py agent/agent_session_queue.py worker/__main__.py` | output > 2 |
| `compacting` gate first in reprieve | `grep -n 'compacting' agent/session_health.py` | output contains `return "compacting"` |
| No raw Redis on AgentSession | `python .claude/hooks/validators/validate_no_raw_redis_delete.py agent/ models/ worker/` | exit code 0 |
| Docs updated | `git diff --name-only main -- docs/features/` | output contains `agent-session-health-monitor.md` and `session-recovery-mechanisms.md` |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None. The issue is self-contained, the scope is well-bounded (four independent fixes, each <60 LOC + one test file), and all infrastructure needed (Popoto ORM, `psutil`, existing Tier 2 gate, `pre_compact_hook` writer, `accumulate_session_tokens` helper) is in place.

Two minor design choices were resolved inline in the Technical Approach without needing Tom's input:

1. Should the OOM defer use a new `retry_after_ts` field or repurpose `started_at`? — spike-1 confirmed `started_at` is NOT polled as a "not before" signal by the pending-scan, so repurposing would be a silent no-op / correctness bug. Use a new field. (Decision: new field `retry_after_ts`.)
2. Should Mode 3 use a new `last_compact_at` `DatetimeField` or the existing `last_compaction_ts` `FloatField` set by `pre_compact_hook`? — PR #1135 already added `last_compaction_ts` and wired the hook. Reuse it. (Decision: reuse existing field.)
