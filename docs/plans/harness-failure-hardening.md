---
status: docs_complete
type: feature
appetite: Small
owner: Tom Counsell
created: 2026-04-24
tracking: https://github.com/tomcounsell/ai/issues/1099
last_comment_id:
revision_applied: true
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
3. In the health-check recovery branch (`session_health.py:872-947` where
   `entry.recovery_attempts` is bumped and `transition_status(entry, "pending", ...)`
   is called), BEFORE the recovery_attempts bump at line 873, capture the pre-bump
   value (`pre_bump_attempts = entry.recovery_attempts or 0`) and evaluate:
   if `entry.exit_returncode == -9` AND `pre_bump_attempts == 0`
   AND `_is_memory_tight()`:
     - Set `entry.scheduled_at = datetime.now(UTC) + timedelta(seconds=120)`
     - Save the session (partial-save of `scheduled_at` + `recovery_attempts`)
     - STILL bump recovery_attempts (or skip the bump — see ordering note below)
     - Still transition to `pending` (or skip the transition this tick — see below)
   The pending-scan in `agent/session_pickup.py:231-235` and `:396-400`
   already honors `scheduled_at > now` by skipping the session. No new field
   is needed; `scheduled_at` already serves as the "not before" timestamp.
```

### Spike Results

#### spike-1: Which existing field, if any, does the pending-scan honor as a "not before" timestamp?

- **Assumption**: We need a way to defer a re-queued session's eligibility for pickup by 120s.
- **Method**: code-read — `agent/session_pickup.py` (`_pop_agent_session` and `_pop_agent_session_with_fallback`), `agent/agent_session_queue.py`, `models/agent_session.py`.
- **Finding (revised post-critique)**: Two fields were inspected.
  1. `started_at` is set to `None` on recovery-to-pending (`session_health.py:926`) and written at execution start. It is NOT polled as a "not before" timestamp. Repurposing it would be a silent no-op / correctness bug.
  2. `scheduled_at = DatetimeField(null=True)` (models/agent_session.py:139) IS honored by the pending-scan. Both `_pop_agent_session` (session_pickup.py:231-235) and `_pop_agent_session_with_fallback` (session_pickup.py:396-400) filter out sessions whose `scheduled_at` is in the future (`if sa and sa > now: skip`). The model docstring on line 139 literally says: "`_pop_job()` skips if > now()". The field supports both `datetime` and `float` (float is auto-converted via `_DATETIME_FIELDS` coercion at line 355).
- **Confidence**: high
- **Impact on plan (REVISED)**: Do NOT introduce a new `retry_after_ts` field. Use the existing `scheduled_at` field — set `entry.scheduled_at = datetime.now(UTC) + timedelta(seconds=120)` on OOM-deferred re-queues. This eliminates one new field + two new read-site edits from the scope. The pending-scan paths at `session_pickup.py:231-235` and `:396-400` already honor it; no additional guards needed in `_ensure_worker` or the worker pending-drain loop.

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
- **Data ownership**: `pre_compact_hook` owns writes to `last_compaction_ts` (already wired). `_run_harness_subprocess` / `get_response_via_harness` owns writes to `exit_returncode` (new, best-effort). Health check owns writes to `scheduled_at` (existing field, new writer) + reads of `last_compaction_ts` + `exit_returncode` + `recovery_attempts` (pre-bump). No new field introduced for Mode 4 — only `exit_returncode`.
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
- **Mode 4 — OOM backoff**: One new `AgentSession` field: `exit_returncode: IntField(null=True, default=None)`. Harness writes it best-effort after subprocess exit. Health check's recovery branch checks the OOM condition (using a pre-bump-captured `recovery_attempts` value, before the line 873 increment) and defers the next eligibility by setting the existing `scheduled_at` field to `now + 120s`. The pending-scan (`agent/session_pickup.py:231-235` and `:396-400`) already skips sessions whose `scheduled_at` is in the future — no new read sites needed.

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

**Implementation Note (concern #1 — sentinel confidence)**: The `"redacted_thinking"` string is derived from the amux blog post ("Every way Claude Code crashes") and has not been confirmed against Anthropic's published API error taxonomy. To detect false positives in production and provide an escape hatch:

1. On every sentinel match, emit a structured log BEFORE raising: `logger.warning("[harness] THINKING_BLOCK_SENTINEL matched: session_id=%s returncode=%d stderr_prefix=%r", session_id, returncode, stderr_snippet[:200])`. This gives operators a grep-able signal (`grep "THINKING_BLOCK_SENTINEL matched"`) to audit for false positives during the first weeks of deployment.
2. Add a feature flag: read `os.environ.get("DISABLE_THINKING_SENTINEL", "")` at module load. If truthy (`"1"`, `"true"`, etc.), the sentinel check becomes a no-op and `get_response_via_harness` returns its normal fallback result. The env var lets operators disable the check at runtime without a code rollback if it misfires.
3. Document the flag in the feature doc update (`docs/features/session-recovery-mechanisms.md`) so operators know how to opt out.

#### Mode 2

- In `config/models.py`, `MODELS[name]["context_window"]` already exists as a dict key (grep confirmed: lines 161, 178, 196, 214 all have `"context_window": 200_000` or `128_000`). The data layout is `MODELS[model_name]` -> dict with `"context_window"` key (integer number of tokens). There is no `ModelConfig` dataclass or `context_window_tokens` attribute — just a nested dict.
- Add a public accessor in `config/models.py` to keep `sdk_client.py` decoupled from the dict layout:
  ```python
  def get_model_context_window(model_name: str) -> int | None:
      """Return the context window (in tokens) for a registered model, or None if unknown."""
      entry = MODELS.get(model_name)
      if entry is None:
          return None
      return entry.get("context_window")
  ```
- In `agent/sdk_client.py`, add a module-level helper:
  ```python
  def _log_context_usage_if_risky(session_id, model, usage) -> None:
      # Defensive: usage may be None or missing input_tokens
      try:
          input_tokens = int((usage or {}).get("input_tokens", 0) or 0)
          if input_tokens <= 0: return
          from config.models import get_model_context_window
          window = get_model_context_window(model)
          if not window:
              logger.warning(
                  "[harness] context_usage: unknown model=%r, skipping pct calc (session_id=%s)",
                  model, session_id,
              )
              return
          pct = input_tokens / window
          if pct > 0.75:
              logger.warning("context_usage pct=%.2f session_id=%s model=%s input_tokens=%d",
                             pct, session_id, model, input_tokens)
      except Exception:
          return  # observability must never crash the turn
  ```
- Call it from `get_response_via_harness` after `accumulate_session_tokens` (existing line 1990), using the same `usage` dict and the `model` argument already in scope.
- Note: the emitted log record is `logger.warning` at the string level, NOT a raw JSON dict. This matches the rest of the codebase's logging conventions (grep-friendly structured-prefix style). Dashboard/grep can pick it up via `grep "context_usage"`.

**Implementation Note (concern #2 — model context window lookup)**: The lookup path was verified by grep. `config/models.py` stores context windows in `MODELS[name]["context_window"]` (int, in tokens). No dataclass exists — the storage is plain dicts. The new `get_model_context_window(model_name)` accessor (added in this plan) takes a string model name and returns `int | None`. If the model name is not in `MODELS` (e.g., a new model added to the project without being registered, or a caller passing an alias), the helper returns `None` and `_log_context_usage_if_risky` logs a WARNING with the unknown-model signal but does not raise and does not emit the `context_usage` log. Test case `test_harness_context_usage_log.py::test_unknown_model_no_crash` covers this.

#### Mode 3

- In `agent/session_health.py`, add a module-level constant (distinct from `STDOUT_FRESHNESS_WINDOW`, even though both currently default to 600s):
  ```python
  # Post-compaction grace period: after a successful compaction, the session
  # often returns to idle briefly before the next turn. During this window
  # the Tier 2 gate reprieves rather than killing. Separate from
  # STDOUT_FRESHNESS_WINDOW so the two can evolve independently.
  # Env-tunable via COMPACT_REPRIEVE_WINDOW_SECS. (Issue #1099.)
  COMPACT_REPRIEVE_WINDOW_SEC = int(os.environ.get("COMPACT_REPRIEVE_WINDOW_SECS", 600))
  ```
- In `agent/session_health.py::_tier2_reprieve_signal`, PREPEND a `compacting` gate before the existing (c)/(d)/(e) gates:
  ```python
  # (b) compacting — reprieve if a compaction completed in the last
  # COMPACT_REPRIEVE_WINDOW_SEC seconds. Companion writer: pre_compact_hook
  # updates AgentSession.last_compaction_ts on every successful backup.
  # See issue #1099 Mode 3.
  lct = getattr(entry, "last_compaction_ts", None)
  if lct is not None:
      try:
          if (time.time() - float(lct)) < COMPACT_REPRIEVE_WINDOW_SEC:
              return "compacting"
      except (TypeError, ValueError):
          pass
  ```
- Order: `compacting` → `children` → `alive` → `stdout`. Telemetry counter string extends: `tier2_reprieve_total:compacting`.
- Add an `import time` if not already present at the top of `session_health.py`. (It is — confirmed via grep.)

**Implementation Note (concern #3 — `last_compaction_ts` writer race with subprocess context)**: `pre_compact_hook` fires from the Claude Code hooks subsystem, which is a subprocess spawned by the Claude harness (not the worker process). The hook has access to `AGENT_SESSION_ID` via its environment (set by the SDK client when spawning `claude -p`). The existing `_update_session_cooldown()` in `agent/hooks/pre_compact.py:131-175` already looks up the `AgentSession` by `claude_session_uuid` (which is in scope for the hook via the hook payload). That function uses the Popoto ORM (`AgentSession.query.filter(...)` + `s.save(update_fields=["last_compaction_ts", ...])`) — no raw Redis. This infrastructure is already in place from PR #1135. For Mode 3, we do NOT add a new writer; we only add the reader in `session_health.py`. The race between hook-write and health-check-read is addressed in the Race Conditions section (Race 2 — field write is atomic via single Redis HSET). Test `test_session_health_compacting_reprieve.py` simulates the read side by creating an `AgentSession` with `last_compaction_ts` already populated (the hook is not actually run); a separate test (`test_pre_compact_hook_updates_session_cooldown.py`, which already exists from PR #1135) covers the writer.

**Implementation Note (concern #4 — distinct constant, no window collision)**: `STDOUT_FRESHNESS_WINDOW` governs how long a silent subprocess is considered "still alive" before Tier 1 flags it for kill. `COMPACT_REPRIEVE_WINDOW_SEC` governs how long after a compaction the Tier 2 gate reprieves a kill candidate. Both happen to default to 600s today, but they answer different questions and may drift apart in the future (e.g., if compaction legitimately takes longer than 10 minutes in some configurations, or if the stdout-freshness threshold is tightened). Having two named constants makes the relationship explicit and self-documenting. Environment overrides: `STDOUT_FRESHNESS_WINDOW_SECS` (existing) and `COMPACT_REPRIEVE_WINDOW_SECS` (new, introduced by this plan).

#### Mode 4

- `models/agent_session.py`: add **one** field (we reuse the existing `scheduled_at` for the deferred-eligibility timestamp):
  ```python
  exit_returncode = IntField(null=True, default=None)   # last subprocess exit code, for OOM detection (issue #1099)
  ```
  Add it to the safe-to-save list at line 361 if that list is the update-fields allowlist. (Confirmed — see lines 361-365 in recon. Add `exit_returncode`.)

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

- **Ordering fix (resolves critique B2)**: In `agent/session_health.py`, the recovery-to-pending branch at line 872-947 currently bumps `entry.recovery_attempts` at line 873 BEFORE any re-queue logic runs. If the OOM-defer check reads `entry.recovery_attempts` after that bump, the condition `== 0` can never be true for a first-time OS kill, and the defer would never fire. The fix: capture the pre-bump value into a local variable BEFORE the increment on line 873, and use the local variable in the OOM check. Concretely, replace lines 872-873:

  ```python
  # BEFORE (current):
  # Bump recovery_attempts counter only on actual kill (#1036).
  entry.recovery_attempts = (entry.recovery_attempts or 0) + 1
  ```

  ```python
  # AFTER (issue #1099):
  # Capture pre-bump recovery_attempts for Mode 4 OOM-defer check below.
  # The increment must happen AFTER the OOM check so we can distinguish
  # first-time OS kills (pre_bump_attempts == 0) from health-check kills
  # (pre_bump_attempts >= 1).
  pre_bump_attempts = entry.recovery_attempts or 0
  entry.recovery_attempts = pre_bump_attempts + 1
  ```

  Then, in the recovery branch body (around line 921-947) where `transition_status(entry, "pending", ...)` is called, insert the OOM-defer check BEFORE the `transition_status` call:

  ```python
  # Mode 4: OOM defer — if the OS killed the subprocess (not the health check)
  # AND this is the first recovery attempt AND memory is currently tight,
  # defer the next eligibility by 120s via the existing scheduled_at field.
  # The pending-scan at session_pickup.py:231-235 honors scheduled_at.
  if (entry.exit_returncode == -9
          and pre_bump_attempts == 0
          and _is_memory_tight()):
      entry.scheduled_at = datetime.now(tz=UTC) + timedelta(seconds=120)
      try:
          entry.save(update_fields=["scheduled_at", "recovery_attempts"])
      except Exception as _sa_err:
          logger.debug("[session-health] scheduled_at save failed: %s", _sa_err)
      logger.warning(
          "[session-health] OOM backoff: deferring %s for 120s "
          "(exit_returncode=-9, recovery_attempts now=%d, memory<400MB)",
          entry.agent_session_id,
          entry.recovery_attempts,
      )
  ```

  Note: the deferred session is STILL transitioned to `pending` by the subsequent `transition_status()` call. `scheduled_at > now` then keeps it dormant in the queue until the 120s elapses — the pending-scan skips it (`session_pickup.py:_is_eligible`). This avoids introducing a new "queued but not transitioned" intermediate state.

- `_is_memory_tight()` helper (new, in `agent/session_health.py`):
  ```python
  _MEMORY_CACHE: tuple[float, bool] | None = None  # (checked_at_monotonic, result)
  _MEMORY_CACHE_TTL_SEC = 5.0

  def _is_memory_tight() -> bool:
      """Return True if available memory is below the OOM-backoff threshold.

      Wraps psutil.virtual_memory() in try/except (fail-open: returns False on
      any error). Caches the result for 5 seconds to avoid repeated
      psutil syscalls on hot paths (e.g., when many sessions recover in the
      same tick). psutil is already a project dependency (monitoring/orphan_cleanup.py,
      agent/session_health.py imports).
      """
      global _MEMORY_CACHE
      now_mono = time.monotonic()
      if _MEMORY_CACHE is not None and (now_mono - _MEMORY_CACHE[0]) < _MEMORY_CACHE_TTL_SEC:
          return _MEMORY_CACHE[1]
      try:
          import psutil
          available_bytes = psutil.virtual_memory().available
          result = available_bytes < 400 * 1024 * 1024  # 400 MB
      except Exception:
          result = False  # fail-open
      _MEMORY_CACHE = (now_mono, result)
      return result
  ```

  This addresses critique concern #5 (psutil cost on hot path) with a 5-second in-process cache. The cache is module-global; no cross-process coordination needed because each health-check tick runs in one process.

### Thread-safety note (Mode 4)

The OOM backoff logic is a single-write, single-read sequence entirely inside the health check's own coroutine. The only write to `scheduled_at` from the health check comes from the Mode 4 OOM-defer branch; external writes to `scheduled_at` happen only at session-creation time (see `agent/agent_session_queue.py:216-250`) and do not race with the recovery path (a session being recovered is already past creation). No locking needed beyond Popoto's default `save(update_fields=...)` partial-save, which writes only the specified fields atomically via Redis `HSET`.

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

- [x] `tests/unit/test_sdk_client_image_sentinel.py` — **UPDATE**: tuple unpacking tests must shift from 5-tuple to 6-tuple (`stderr_snippet` added). The existing tests mock `_run_harness_subprocess` return — update all mocks to return 6-tuples.
- [x] `tests/unit/test_session_watchdog.py` — **no change expected**: this file tests the bridge-hosted watchdog. Mode 3 touches worker-hosted `session_health.py`, which has its own test file.
- [x] `tests/unit/test_session_health_phantom_guard.py` — **no change expected**: we add a new reprieve gate; existing phantom-guard tests exercise an unrelated branch.
- [x] `tests/unit/test_health_check_recovery_finalization.py` — **UPDATE**: if it constructs an `AgentSession` via the finalize path, the new field (`exit_returncode`) has `default=None` so no test break is expected. Verify at build time that adding the new field doesn't break `to_dict()` / `from_dict()` assumptions. Also verify this test does not assert the exact ordering of the `recovery_attempts` bump relative to `transition_status()` — if it does, update to match the revised ordering (pre-bump capture).
- [x] `tests/unit/test_message_drafter.py` — **UPDATE (regression guard)**: no behavioral change expected from any of the four modes. The drafter pathway is independent of the harness subprocess / health-check changes. This entry is a guard: if the drafter test regresses after this build, a harness change has leaked into drafting logic and must be reverted. Run as part of the Verification step. (Related: `tests/unit/test_drafter_validators.py`, `tests/unit/test_message_drafter_linkify.py`, `tests/integration/test_message_drafter_integration.py` — same regression-guard expectation.)

Greenfield tests to add (4 new test files, one per mode — each uses the `test-resilience-{mode}` `project_key` prefix as required by the acceptance criteria):

| # | File | Cases | `project_key` |
|---|---|---|---|
| 1 | `tests/unit/test_harness_thinking_block_sentinel.py` (Mode 1) | 4 | `test-resilience-mode-1` |
| 2 | `tests/unit/test_harness_context_usage_log.py` (Mode 2) | 3 + 1 (unknown-model case, concern #2) | `test-resilience-mode-2` |
| 3 | `tests/unit/test_session_health_compacting_reprieve.py` (Mode 3) | 3 + 1 (distinct-window constant, concern #4) | `test-resilience-mode-3` |
| 4 | `tests/unit/test_harness_oom_backoff.py` (Mode 4) | 4 + 1 (ordering case, blocker B2) | `test-resilience-mode-4` |

All new test files MUST:
1. Use a `project_key` with the `test-resilience-` prefix (enforced by manual testing hygiene — see CLAUDE.md).
2. Use pytest fixture teardown with Popoto ORM cleanup: `AgentSession.query.filter(project_key=k).delete()`. **No raw Redis** — the `.claude/hooks/validators/validate_no_raw_redis_delete.py` hook blocks that.
3. Clean up in a `finally` block or `tearDown` method so crashed tests still clean up.

The cleanup pattern (copied from `tests/unit/test_session_watchdog.py`):
```python
@pytest.fixture
def clean_sessions():
    project_key = "test-resilience-mode-1"
    yield project_key
    try:
        for s in AgentSession.query.filter(project_key=project_key):
            s.delete()
    except Exception:
        pass  # best-effort cleanup
```

## Rabbit Holes

- **Replay after thinking-block corruption** (extract last message from transcript, re-spawn session). The issue explicitly defers this. Keep deferred — the detection work is the whole scope.
- **Proactive `/compact` injection at 20% context**. Also explicitly deferred. Observability log (Mode 2) is the full scope.
- **Rewriting the Tier 2 gate into a registry pattern** to make adding new reprieves easier. Tempting but over-engineering for one new gate. Keep the imperative if-chain.
- **Adding a full OOM dashboard (metrics, alerts, auto-scaling hooks)**. Deferred — single `exit_returncode` field + 120s backoff is the entire scope.
- **Introducing a new `retry_after_ts` field or a new scheduler loop to "wake up" pending sessions**. We piggyback on the existing `scheduled_at` field and the existing pending-scan — both already support deferred execution. Adding a new field would be redundant with `scheduled_at`. No new field, no new scheduler.
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
**Mitigation:** The defer is conditioned on `pre_bump_attempts == 0` (first attempt only). A second recovery attempt has `pre_bump_attempts == 1` and bypasses the OOM defer, proceeding to normal recovery. At MAX_RECOVERY_ATTEMPTS, session finalizes as `failed` — same backstop as today. Test `test_harness_oom_backoff.py::test_second_attempt_no_defer` asserts the ordering (pre_bump value captured before increment, second recovery attempt does NOT defer).

### Risk 4: `scheduled_at` not consulted by some re-queue path (Mode 4)
**Impact:** If a code path promotes a session to `running` without going through the pending-scan (which already honors `scheduled_at`), the defer is silently lost.
**Mitigation:** The pending-scan paths (`agent/session_pickup.py:_pop_agent_session` at line 231-235 and `_pop_agent_session_with_fallback` at line 396-400) are the ONLY paths that move sessions from `pending` to `running` — both already honor `scheduled_at`. This is the whole point of using the existing field instead of adding a new one. Grep-verify during build: `grep -n 'status="running"\|status = "running"' agent/ worker/` should show only the session_pickup paths writing `running` via the pop flow. No new read sites to add.

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
- [x] Update `docs/features/agent-session-health-monitor.md` — add the `compacting` gate to the Tier 2 reprieve list (Mode 3) and add an "OOM backoff" subsection (Mode 4).
- [x] Update `docs/features/session-recovery-mechanisms.md` — add a "Thinking-block corruption" subsection (Mode 1) and a "Context-usage observability" subsection (Mode 2).
- [x] No new feature doc required — these four fixes are defenses on existing features (harness execution + session health), not a new feature in their own right. An entry in the index table would misrepresent the scope.

### External Documentation Site
- N/A — this repo has no external docs site.

### Inline Documentation
- [x] Docstrings on `_log_context_usage_if_risky`, `HarnessThinkingBlockCorruption`, `_is_memory_tight` explaining trigger conditions and failure handling.
- [x] Docstring on the new `compacting` gate in `_tier2_reprieve_signal` citing the issue number (#1099) and the `pre_compact_hook` as the companion writer.
- [x] Code comments on the three `_run_harness_subprocess` call sites noting the 6-tuple return.
- [x] `models/agent_session.py` field comment explaining `exit_returncode` = last subprocess exit code (for OOM detection, issue #1099). No other new fields — deferred eligibility uses existing `scheduled_at`.

## Success Criteria

- [ ] Mode 1: `THINKING_BLOCK_SENTINEL` in stderr + non-zero returncode → `HarnessThinkingBlockCorruption` raised → session finalized as `failed` with user-visible error. Healthy run (returncode=0, no sentinel) returns a non-empty result and status `completed`. (Test: `test_harness_thinking_block_sentinel.py`)
- [ ] Mode 2: `usage.input_tokens / context_window > 0.75` → a single `logger.warning("context_usage ...")` record emitted per turn. Result text, session status, returncode, and all other behavior unchanged. (Test: `test_harness_context_usage_log.py`)
- [ ] Mode 3: `last_compaction_ts` within 600s → Tier 2 returns `"compacting"` → kill skipped. `last_compaction_ts` stale (>600s) or None → existing gate chain runs unchanged. (Test: `test_session_health_compacting_reprieve.py`)
- [ ] Mode 4: `exit_returncode == -9` AND `pre_bump_attempts == 0` AND `_is_memory_tight()` → `scheduled_at = now + 120s`. Any condition unmet → normal recovery. `pre_bump_attempts` is captured BEFORE line 873's `recovery_attempts` increment, ensuring first-time OS kills actually trigger the defer. (Test: `test_harness_oom_backoff.py`)
- [ ] The one new field (`exit_returncode`) defaults to `None`. Existing sessions loaded from Redis deserialize correctly without migration. No other new AgentSession fields are added — Mode 4 reuses the existing `scheduled_at`.
- [ ] Drafter tests (`test_message_drafter.py`, `test_drafter_validators.py`, `test_message_drafter_linkify.py`) remain green — no behavioral regression. (Regression guard per Test Impact section.)
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
  - Role: Read-only verification — all call sites of `_run_harness_subprocess` updated (now 6-tuple), `pre_bump_attempts` capture present before line 873 increment, `scheduled_at` used for OOM defer, `COMPACT_REPRIEVE_WINDOW_SEC` is a distinct constant, `DISABLE_THINKING_SENTINEL` env var gate present, no raw Redis, documentation updated.
  - Agent Type: validator
  - Resume: true

- **Documentarian (harness hardening)**
  - Name: `harness-documentarian`
  - Role: Surgical updates to `docs/features/agent-session-health-monitor.md` and `docs/features/session-recovery-mechanisms.md`.
  - Agent Type: documentarian
  - Resume: true

### Step by Step Tasks

### 1. Add exit_returncode field to AgentSession
- **Task ID**: build-agentsession-fields
- **Depends On**: none
- **Validates**: `tests/unit/test_harness_oom_backoff.py` (create), existing unit tests still green
- **Assigned To**: `harness-builder`
- **Agent Type**: builder
- **Parallel**: true
- Add `exit_returncode = IntField(null=True, default=None)` to `models/agent_session.py`. (Only one new field — Mode 4 reuses the existing `scheduled_at` for deferred eligibility.)
- Add `exit_returncode` to any update-fields allowlist (reference: lines 361-365, currently whitelists `last_heartbeat_at` etc.). Confirm `scheduled_at` is already in the allowlist (it is — line 155 of agent_session_queue.py `ALLOWED_CREATE_FIELDS` and `_DATETIME_FIELDS` in models/agent_session.py:355).
- Add explanatory inline comment citing issue #1099 and the Mode 4 OOM-backoff design.

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

### 5. Mode 4 — OOM backoff via scheduled_at
- **Task ID**: build-mode-4
- **Depends On**: build-agentsession-fields
- **Validates**: `tests/unit/test_harness_oom_backoff.py` (create)
- **Assigned To**: `harness-builder`
- **Agent Type**: builder
- **Parallel**: false (needs fields first)
- In `agent/sdk_client.py::get_response_via_harness` (right after the stale-UUID fallback path finishes), add the best-effort `exit_returncode` write as described in Technical Approach (Mode 4 section).
- In `agent/session_health.py`:
  - Add `_is_memory_tight()` helper at module scope with a 5s in-process cache (wraps `psutil.virtual_memory()` inside try/except; threshold 400MB; fail-open). See Technical Approach for the full implementation. Addresses concern #5.
  - Ensure `from datetime import datetime, timedelta; from datetime import UTC` imports are present at top of file.
- **Ordering fix (B2, critical)**: In `agent/session_health.py:872-873`, capture the pre-bump value BEFORE incrementing `recovery_attempts`:
  ```python
  # OLD: entry.recovery_attempts = (entry.recovery_attempts or 0) + 1
  # NEW:
  pre_bump_attempts = entry.recovery_attempts or 0
  entry.recovery_attempts = pre_bump_attempts + 1
  ```
- In the recovery-to-pending branch (the `else` branch around current line 921-947), BEFORE `transition_status(entry, "pending", ...)` is called, add the OOM-defer block:
  ```python
  if (entry.exit_returncode == -9
          and pre_bump_attempts == 0
          and _is_memory_tight()):
      entry.scheduled_at = datetime.now(tz=UTC) + timedelta(seconds=120)
      try:
          entry.save(update_fields=["scheduled_at", "recovery_attempts"])
      except Exception as _sa_err:
          logger.debug("[session-health] scheduled_at save failed: %s", _sa_err)
      logger.warning(
          "[session-health] OOM backoff: deferring %s for 120s",
          entry.agent_session_id,
      )
  ```
  The session STILL transitions to `pending` via the subsequent `transition_status()` call — the deferral works by making the session ineligible for pickup until `scheduled_at > now` becomes false (after 120s).
- **No new read sites needed.** The pending-scan in `agent/session_pickup.py:_pop_agent_session` (line 231-235) and `_pop_agent_session_with_fallback` (line 396-400) already filter out sessions with `scheduled_at > now`. Grep-verify during validation that these are the only paths that move sessions from `pending` → `running`. (If a new bypass path has been introduced since the recon, it would need an explicit `scheduled_at` guard added.)

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
- Create `tests/unit/test_harness_oom_backoff.py` — 5 cases: (a) **ordering test** `test_first_attempt_defers` — create an AgentSession with `recovery_attempts=0`, `exit_returncode=-9`, mock `_is_memory_tight` returning True; run the health-check recovery branch; assert `scheduled_at` is set to approximately `now+120s` (tolerance ±10s); assert `recovery_attempts` was bumped to 1 (the capture-before-bump ordering works). (b) **ordering test** `test_second_attempt_no_defer` — create session with `recovery_attempts=1` (already bumped by a prior health-check kill), `exit_returncode=-9`, memory tight; run recovery branch; assert `scheduled_at` is NOT updated (defer condition fails because `pre_bump_attempts == 1`). (c) `test_memory_ok_no_defer` — `exit_returncode=-9`, `recovery_attempts=0`, but `_is_memory_tight` returns False; assert no deferral. (d) `test_non_oom_returncode_no_defer` — `exit_returncode=0`, `recovery_attempts=0`, memory tight; assert no deferral. (e) `test_pending_scan_skips_deferred` — create an AgentSession with `status="pending"` and `scheduled_at = now + 60s`; call `_pop_agent_session`; assert the session is skipped (returns None or picks a different session). This is the end-to-end verification that the deferral actually prevents pickup.
- Update `tests/unit/test_sdk_client_image_sentinel.py` — replace all 5-tuple mocks with 6-tuple mocks (add `None` as the stderr_snippet element).
- All new tests use `project_key="test-resilience-mode-{N}"` (see Test Impact table above) and clean up AgentSession records via Popoto ORM in a fixture teardown (pattern: `AgentSession.query.filter(project_key=k).delete()`). NO raw Redis.

### 7. Documentation updates
- **Task ID**: document-feature
- **Depends On**: build-mode-1, build-mode-2, build-mode-3, build-mode-4, build-tests
- **Validates**: both doc files contain the new sections; links remain valid
- **Assigned To**: `harness-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/agent-session-health-monitor.md` with the `compacting` Tier 2 gate (Mode 3) and a brief OOM-backoff subsection (Mode 4) noting `exit_returncode` (new field), the `pre_bump_attempts` capture pattern, and the use of `scheduled_at` for deferred re-queue.
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
- Grep-verify the only `pending` → `running` paths are `_pop_agent_session` and `_pop_agent_session_with_fallback` in `agent/session_pickup.py` — both already honor `scheduled_at`. No new guards required.
- Grep-verify `pre_bump_attempts` is captured BEFORE `entry.recovery_attempts` is incremented in `agent/session_health.py`.
- Confirm no new raw Redis operations (`r.hget`, `r.hset`, `r.delete` on AgentSession keys) introduced.
- Confirm `logger.warning("context_usage ...")` format is grep-friendly.
- Confirm the `compacting` gate is the first branch of `_tier2_reprieve_signal`.
- Confirm `COMPACT_REPRIEVE_WINDOW_SEC` constant is a distinct module-level symbol (not an alias of `STDOUT_FRESHNESS_WINDOW`).
- Confirm `DISABLE_THINKING_SENTINEL` env var gate is read at module load in `sdk_client.py`.
- Confirm `_is_memory_tight()` has a 5s in-process cache.

## Verification

| Check | Command | Expected |
|---|---|---|
| Format clean | `python -m ruff format --check .` | exit code 0 |
| New tests pass | `pytest tests/unit/test_harness_thinking_block_sentinel.py tests/unit/test_harness_context_usage_log.py tests/unit/test_session_health_compacting_reprieve.py tests/unit/test_harness_oom_backoff.py -q` | exit code 0 |
| Updated image-sentinel test passes | `pytest tests/unit/test_sdk_client_image_sentinel.py -q` | exit code 0 |
| Drafter regression guard | `pytest tests/unit/test_message_drafter.py tests/unit/test_drafter_validators.py tests/unit/test_message_drafter_linkify.py -q` | exit code 0 |
| Full unit suite green | `pytest tests/unit/ -n auto -q` | exit code 0 |
| 6-tuple unpacking complete | `grep -n '_run_harness_subprocess' agent/sdk_client.py` | output contains 3+ call sites all unpacking 6 values |
| `scheduled_at` used for OOM defer | `grep -n 'scheduled_at' agent/session_health.py` | output contains OOM-defer assignment |
| `pre_bump_attempts` capture present | `grep -n 'pre_bump_attempts' agent/session_health.py` | output contains `pre_bump_attempts = entry.recovery_attempts or 0` |
| Only session_pickup promotes pending → running | `grep -rn 'status\s*=\s*.running.\|status=.running.' agent/ worker/` | only session_pickup.py writes `running` via pop flow |
| `compacting` gate first in reprieve | `grep -n 'compacting' agent/session_health.py` | output contains `return "compacting"` |
| Distinct reprieve window constant | `grep -n 'COMPACT_REPRIEVE_WINDOW_SEC' agent/session_health.py` | defines constant and uses it in the compacting gate |
| Thinking sentinel gate | `grep -n 'DISABLE_THINKING_SENTINEL\|THINKING_BLOCK_SENTINEL' agent/sdk_client.py` | both symbols present; env var read at module load |
| Memory cache TTL | `grep -n '_MEMORY_CACHE_TTL_SEC' agent/session_health.py` | 5.0 |
| No raw Redis on AgentSession | `python .claude/hooks/validators/validate_no_raw_redis_delete.py agent/ models/ worker/` | exit code 0 |
| Docs updated | `git diff --name-only main -- docs/features/` | output contains `agent-session-health-monitor.md` and `session-recovery-mechanisms.md` |

## Critique Results

Verdict: NEEDS REVISION (2 blockers, 6 concerns). Revision applied 2026-04-24 — see `revision_applied: true` in frontmatter.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Operator | B1: `retry_after_ts` never honored by worker — no consumer reads the proposed field. | Technical Approach § Mode 4; Spike spike-1 (revised) | Dropped `retry_after_ts` entirely. Reuse existing `scheduled_at` field which IS honored by `agent/session_pickup.py:231-235` and `:396-400` (`_is_eligible` filters sessions with `scheduled_at > now`). |
| BLOCKER | Archaeologist | B2: OOM-defer condition `recovery_attempts == 0` never fires because line 873 increments BEFORE the re-queue logic. | Technical Approach § Mode 4 ordering; Step 5 task instructions | Capture `pre_bump_attempts = entry.recovery_attempts or 0` BEFORE the line 873 increment; use the local in the OOM check. Added test `test_second_attempt_no_defer` to lock in the ordering. |
| CONCERN | Skeptic | 1. Sentinel `"redacted_thinking"` unconfirmed from Anthropic docs — false-positive risk. | Technical Approach § Mode 1 Implementation Note | Emit `logger.warning("THINKING_BLOCK_SENTINEL matched: ...")` on every match for production audit; add `DISABLE_THINKING_SENTINEL=1` env-var escape hatch. |
| CONCERN | Adversary | 2. `config/models.py` context window lookup path unverified. | Technical Approach § Mode 2; `get_model_context_window` helper | Verified via grep: `MODELS[name]["context_window"]` (dict key, not dataclass attribute). Added public accessor. Unknown-model fallback logs WARNING and skips. Test case covers. |
| CONCERN | Archaeologist | 3. `pre_compact_hook` runs in subprocess — confirm ORM access. | Technical Approach § Mode 3 Implementation Note | Verified: hook uses `AGENT_SESSION_ID`/`claude_session_uuid` from env + hook payload; `_update_session_cooldown` at `agent/hooks/pre_compact.py:131-175` already uses Popoto ORM. No new writer needed — reader-only work in this plan. |
| CONCERN | Simplifier | 4. Reprieve window shares `STDOUT_FRESHNESS_WINDOW` coincidentally. | Technical Approach § Mode 3 Implementation Note | Introduced `COMPACT_REPRIEVE_WINDOW_SEC = 600` as a distinct module-level constant with its own env override (`COMPACT_REPRIEVE_WINDOW_SECS`). Prevents future drift from coupling. |
| CONCERN | Operator | 5. `psutil.virtual_memory()` on hot path. | `_is_memory_tight()` helper (Mode 4 Technical Approach) | Added 5s in-process cache. Module-global `_MEMORY_CACHE` + `_MEMORY_CACHE_TTL_SEC = 5.0`. |
| CONCERN | User | 6. Test file paths and `project_key` convention not enumerated. | Test Impact table | Enumerated 4 test files with exact paths, `test-resilience-mode-{N}` project_key prefix, and ORM teardown fixture. |


---

## Open Questions

None. The issue is self-contained, the scope is well-bounded (four independent fixes, each <60 LOC + one test file), and all infrastructure needed (Popoto ORM, `psutil`, existing Tier 2 gate, `pre_compact_hook` writer, `accumulate_session_tokens` helper, existing `scheduled_at` deferral field) is in place.

Design choices resolved inline in the Technical Approach without needing Tom's input:

1. **OOM defer mechanism** — initially spike-1 assumed we would need a new `retry_after_ts` field. Post-critique re-inspection confirmed the existing `scheduled_at` field IS polled by the pending-scan (`agent/session_pickup.py:231-235` and `:396-400`) as a "not before" timestamp. Decision: reuse `scheduled_at`, no new field. (Resolves critique blocker B1.)
2. **Recovery-attempts ordering** — the health check at `agent/session_health.py:873` bumps `recovery_attempts` BEFORE any re-queue logic runs. Naively reading `entry.recovery_attempts == 0` after that point would never succeed. Decision: capture the pre-bump value into a local variable `pre_bump_attempts` before line 873's increment; use the local for the OOM check. (Resolves critique blocker B2.)
3. **`last_compaction_ts` vs. a new field** — PR #1135 already added `last_compaction_ts` and wired `pre_compact_hook`. Reuse it.
4. **Sentinel false-positive mitigation** — add a grep-able WARNING log on every sentinel match (for production audit) + `DISABLE_THINKING_SENTINEL` env var escape hatch. (Resolves critique concern #1.)
5. **Context window lookup** — verified by grep: `MODELS[name]["context_window"]` dict key. Added a dedicated accessor `get_model_context_window` for decoupling. (Resolves critique concern #2.)
6. **Compaction reprieve window constant** — distinct symbol `COMPACT_REPRIEVE_WINDOW_SEC` (not an alias of `STDOUT_FRESHNESS_WINDOW`) so the two can evolve independently. (Resolves critique concern #4.)
7. **psutil cost** — cached for 5s in-process in `_is_memory_tight()`. (Resolves critique concern #5.)
8. **Test file enumeration and cleanup** — 4 new test files listed in Test Impact table with `test-resilience-mode-{N}` project_key and Popoto ORM teardown fixture. (Resolves critique concern #6.)
