---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-25
tracking: https://github.com/tomcounsell/ai/issues/1159
last_comment_id:
---

# Progress detector: 4 minor tweaks for accuracy and clarity

## Problem

Recent issues (#1036, #1046) hardened the session-health detector at `agent/session_health.py:461-566`. While monitoring a 15+ minute PM warmup on issue #1155, four small gaps surfaced:

1. **Tweak 1** — A session that emits stream-event partial messages (refreshing `last_stdout_at`) but never produces a `result` event is never flagged. Tier 1's stdout-stale check trusts any stdout activity as progress.
2. **Tweak 2** — The 300s `FIRST_STDOUT_DEADLINE` doesn't scale with prompt size. A 50-char prompt taking 20 minutes is broken; a 4000-char PM prompt taking 20 minutes may be legitimate.
3. **Tweak 3** — *Already fixed by [PR #1166](https://github.com/tomcounsell/ai/pull/1166)* for the duplicate `local-XXX` AgentSession creation. Two follow-up gaps remain: capturing `claude_session_uuid` onto the worker-managed parent record (a precondition for Tweak 1), and cleaning up pre-#1166 orphaned `local-XXX` records.
4. **Tweak 4** — `[session-health]` log lines phrase observations as if the detector knows live state (`alive`, `stuck`, `no progress signal`). The detector only knows past timestamps; phrasing must reflect that.

**Current behavior:**
- `_has_progress()` returns True for any session with `last_stdout_at` younger than 600s, even if `result_text == ""` and `claude_session_uuid is None` for hours.
- `FIRST_STDOUT_DEADLINE = 300s` applies a flat deadline regardless of prompt size — generous for short prompts, brittle for 60K-char system prompts.
- The worker-managed PM `AgentSession` for a worker-spawned subprocess has no `claude_session_uuid` populated by `user_prompt_submit.py` (the hook resolves to it via env vars but never writes the field).
- Operators read log lines like "worker alive but no progress signal" without an explicit staleness window.

**Desired outcome:**
- `_has_progress()` flags `(result_text=="" AND claude_session_uuid is None AND started_at >= NO_RESULT_DEADLINE)` while preserving Tier 2 reprieves.
- First-result deadline scales with prompt size: `min_first_result_secs = max(600, len(prompt_chars) * SCALE_FACTOR)`.
- `user_prompt_submit.py` sets `attached.claude_session_uuid = hook_input.session_id` when attaching the sidecar to a worker-managed `AgentSession`.
- `[session-health]` log lines and dashboard renderings phrase observations as "no X in Ns" / "last Y Ns ago" — never bare-present-tense `alive` / `stuck` / `no progress`.
- Pre-#1166 orphaned `local-XXX` PM records (status `pending`, parent terminated) are cleaned up by a one-time scan.

## Freshness Check

**Baseline commit:** `72f6e2fa` (main HEAD at plan time, 2026-04-25). Two commits ahead of `b822146a` (telegram-chat-resolution work — not in scope).
**Issue filed at:** 2026-04-24T07:31:34Z.
**Disposition:** Major drift on Tweak 3; Tweaks 1, 2, 4 unchanged.

**File:line references re-verified:**
- `agent/session_health.py:140` — `STDOUT_FRESHNESS_WINDOW` — unchanged.
- `agent/session_health.py:156` — `FIRST_STDOUT_DEADLINE` — unchanged.
- `agent/session_health.py:461-566` — `_has_progress()` body — unchanged. Tier 1 stdout-stale check at lines 535-550 is the insertion point for Tweak 1.
- `agent/session_health.py:760` — "worker alive but no progress signal" log line — unchanged. Tweak 4 target.
- `agent/session_health.py:904-911` — "Recovering stuck session" log line — unchanged. Tweak 4 target.
- `.claude/hooks/user_prompt_submit.py:106-149` — **drifted to phantom-twin-prevention** (PR #1166). Tweak 3's primary symptom is fixed; only `claude_session_uuid` capture and orphan cleanup remain.

**Cited sibling issues/PRs re-checked:**
- #1036 — CLOSED 2026-04-18 — added dual-heartbeat OR check; established that heartbeat counts as progress.
- #1046 — CLOSED 2026-04-18 — added stdout-stale Tier 1 extension.
- #944 — CLOSED 2026-04-14 — slugless dev sessions sharing worker_key with PM (must not regress).
- #808 — CLOSED 2026-04-07 — established `parent_agent_session_id` linkage.
- #1157 — CLOSED 2026-04-24 (PR #1166 merged 14:30 UTC, ~7h after #1159 was filed) — phantom PM twin prevention. **Major drift to Tweak 3.**
- #1155 — CLOSED 2026-04-24 — the originating SDLC observed.

**Commits on main since issue was filed (touching referenced files):**
- `b5c9d2f4 fix(#1157): prevent phantom PM twin AgentSession creation (#1166)` — partially addresses Tweak 3 (drops the duplicate-creation symptom).
- No commits to `agent/session_health.py` since issue was filed.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** Tweak 3's primary code change (the gate against duplicate `local-XXX` creation) is **already shipped**. The plan's Tweak 3 work is reduced to (a) adding a `claude_session_uuid` write in the existing phantom-twin-prevention branch at `.claude/hooks/user_prompt_submit.py:144-150` and (b) a one-time cleanup of orphaned `local-XXX` PM records.

## Prior Art

- **#1036** (closed 2026-04-18) — added dual-heartbeat OR check; established the warmup-tolerant invariant that we must preserve.
- **#1046** (closed 2026-04-18) — added the stdout-stale Tier 1 extension and `FIRST_STDOUT_DEADLINE`. Tweaks 1 and 2 are direct extensions.
- **#1099** (closed 2026-04-18) — added compaction reprieve gate `(b) compacting` to Tier 2.
- **#1157 / PR #1166** (closed 2026-04-24) — added phantom-twin-prevention via `AGENT_SESSION_ID` / `VALOR_SESSION_ID` env-var attachment. **Drops Tweak 3's primary scope.**
- **#1113 / PR #1121** (closed earlier) — prevented zombie session revival through `user_prompt_submit.py` terminal-status guard.

No prior art for Tweak 4 (log-line phrasing) — this is the first explicit-staleness pass.

## Research

No external research applicable — this is purely internal session-health detector behavior. All references are project-internal.

**Queries used:** none.

## Data Flow

For Tweak 1 (no-result deadline):

1. **Entry point:** SDK subprocess emits a stream event → `_handle_sdk_message()` updates `last_stdout_at`.
2. **Health-check tick:** `_agent_session_health_check()` iterates running sessions every N seconds.
3. **Tier 1 evaluation:** `_has_progress(entry)` returns True if any heartbeat is fresh AND stdout is fresh.
4. **(NEW)** After the existing stdout-stale / first-stdout-deadline checks, if `entry.result_text == "" AND entry.claude_session_uuid is None AND age(started_at) >= NO_RESULT_DEADLINE`, set `_last_progress_reason = "no_result_deadline"` and return False.
5. **Tier 2 reprieve:** `_tier2_reprieve_signal()` evaluates psutil + compacting + stdout gates. An alive subprocess with children continues to reprieve.
6. **Output:** if Tier 2 fails, `transition_status(entry, "pending", reason="health check: no_result_deadline")` and the recovery counter increments.

For Tweak 3 sub-fix (`claude_session_uuid` capture):

1. **Entry point:** worker-spawned subprocess emits its first prompt → Claude Code hooks fire.
2. **`user_prompt_submit.py:121-150`:** reads `AGENT_SESSION_ID` / `VALOR_SESSION_ID` env vars, resolves `attached` (the worker-managed `AgentSession`).
3. **(NEW)** Before `save_agent_session_sidecar(...)`, set `attached.claude_session_uuid = session_id` (the Claude Code UUID from `hook_input["session_id"]`) if `attached.claude_session_uuid` is None. Persist via `attached.save(update_fields=["claude_session_uuid"])`.
4. **Output:** worker-managed `AgentSession` now has `claude_session_uuid` populated, satisfying Tweak 1's precondition.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** `_has_progress()` adds one Tier 1 check; signature unchanged. `FIRST_STDOUT_DEADLINE` becomes a function call returning a per-session deadline, not a module constant (or stays a constant with a new helper `_first_stdout_deadline_for(prompt_len)` — the plan picks the helper approach).
- **Coupling:** unchanged. All four tweaks live within existing modules.
- **Data ownership:** `user_prompt_submit.py` now writes `claude_session_uuid` onto the worker-managed `AgentSession`; today only the SDK auth path and `post_compact._lookup_session()` reach that field. Adding the hook as a writer is benign because the field is set-once (None → uuid).
- **Reversibility:** all four tweaks are individually revertable. Tweak 1 and 2 are guarded by env vars (`NO_RESULT_DEADLINE_SECS`, `FIRST_RESULT_SCALE_FACTOR_MS_PER_CHAR`) — set to 0 to disable.

## Appetite

**Size:** Small.

**Team:** Solo dev, validator.

**Interactions:**
- PM check-ins: 1 (alignment on `NO_RESULT_DEADLINE` default and `SCALE_FACTOR` value).
- Review rounds: 1.

The four tweaks are short, additive, and isolated. The bottleneck is regression-test coverage, not coding time.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Worktree on `session/progress-detector-tweaks` | `git rev-parse --abbrev-ref HEAD` | Isolated build branch |
| pytest available | `python -c "import pytest"` | Test suite execution |
| Popoto + Redis | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | AgentSession ORM access for tests |

Run all checks: `python scripts/check_prerequisites.py docs/plans/progress-detector-tweaks.md`

## Solution

### Key Elements

- **`NO_RESULT_DEADLINE` constant** — env-tunable via `NO_RESULT_DEADLINE_SECS`, default `3600` (60 min). Conservative default per #1036's intent.
- **`_has_progress()` Tier 1 extension** — new combined check after existing stdout-stale / first-stdout-deadline branches. Sets `_last_progress_reason = "no_result_deadline"` when triggered.
- **`_first_stdout_deadline_for(prompt_len)` helper** — returns `max(600, prompt_len * SCALE_FACTOR_MS_PER_CHAR / 1000)`, env-tunable via `FIRST_RESULT_SCALE_FACTOR_MS_PER_CHAR` (default `15`, ~14ms/char observed empirically).
- **`user_prompt_submit.py` `claude_session_uuid` write** — in the existing phantom-twin-prevention branch (lines 144-150), set `attached.claude_session_uuid = session_id` when None. Persisted via `save(update_fields=["claude_session_uuid"])`.
- **Log-line rewrite pass** — `agent/session_health.py:760` and `:904-911` rewritten to use explicit-staleness phrasing.
- **Orphan cleanup script** — `scripts/cleanup_orphan_local_records.py` scans `AgentSession` records where `session_id` starts with `local-` AND `session_type == "pm"` AND `status == "pending"` AND parent is terminal. Finalizes them as `abandoned`.

### Flow

For Tweak 1 (operator perspective):

A PM session starts → emits stream events but never produces a result → after 60 min, health-check tick observes (no `result_text`, no `claude_session_uuid`, `started_at` > 60 min ago) → Tier 1 flags → Tier 2 evaluates: subprocess `alive` → reprieve fires → counter increments → at next tick (~30s later), same flag, but now psutil shows the process gone or zombie → Tier 2 fails → `transition_status` to `pending` with reason `"health check: no_result_deadline"`.

For Tweak 4 (operator perspective):

Operator tails `logs/worker.log` → sees `[session-health] Flagging session X (no stdout for 612s, last_heartbeat last fired 84s ago, no result event in 720s)` → understands the staleness window of every observation, can compute when the threshold was crossed.

### Technical Approach

**Tweak 1 — `_has_progress()` no-result-deadline check** at `agent/session_health.py:461-566`:

After the existing `lso is None` / `FIRST_STDOUT_DEADLINE` branch (line ~550), add a final Tier 1 check inside the `if any_heartbeat_fresh:` block:

```python
# Tweak 1: no-result deadline. If a session has fresh heartbeats but
# never produced a result event AND has no claude_session_uuid AND has
# been running >= NO_RESULT_DEADLINE, flag it. Tier 2 reprieves still apply.
result_text = getattr(entry, "result_text", None)
claude_uuid = getattr(entry, "claude_session_uuid", None)
started = getattr(entry, "started_at", None)
if (
    not result_text
    and not claude_uuid
    and started is not None
):
    started_aware = started if started.tzinfo else started.replace(tzinfo=UTC)
    if (now_utc - started_aware).total_seconds() >= NO_RESULT_DEADLINE:
        _last_progress_reason = "no_result_deadline"
        return False
```

`NO_RESULT_DEADLINE = int(os.environ.get("NO_RESULT_DEADLINE_SECS", 3600))` added near the existing constants block at `agent/session_health.py:140-156`.

**Tweak 2 — length-scaled first-result deadline:**

Replace the bare `FIRST_STDOUT_DEADLINE` in `_has_progress()` with a per-session calculation:

```python
def _first_stdout_deadline_for(entry: AgentSession) -> int:
    """Per-session first-stdout deadline scaled by prompt length.

    Returns max(FIRST_STDOUT_DEADLINE, len(message_text) * SCALE_FACTOR / 1000).
    """
    prompt_len = len(getattr(entry, "message_text", "") or "")
    scaled = (prompt_len * FIRST_RESULT_SCALE_FACTOR_MS_PER_CHAR) // 1000
    return max(FIRST_STDOUT_DEADLINE, scaled)
```

Used at the existing `lso is None` branch:
```python
if (now_utc - started_aware).total_seconds() >= _first_stdout_deadline_for(entry):
    _last_progress_reason = "first_stdout_deadline"
    return False
```

`FIRST_RESULT_SCALE_FACTOR_MS_PER_CHAR = int(os.environ.get("FIRST_RESULT_SCALE_FACTOR_MS_PER_CHAR", 15))` added with the other constants.

Note: `message_text` is the prompt as delivered to the subprocess (truncated to 500 chars for AgentSession storage), but for non-local sessions it captures the full prompt before truncation. The plan accepts this approximation: a 60K-char system prompt registers as ≥500 chars and gets `max(600, 500*15/1000) = 600s` — same as today's flat 300s clamp doubled. **Open Question 2** asks whether to track `prompt_len` as a separate field for accurate scaling, or accept the 500-char clamp as good-enough.

**Tweak 3 sub-fix — `claude_session_uuid` capture in `user_prompt_submit.py:144-150`:**

```python
if (
    attached is not None
    and getattr(attached, "status", None) not in TERMINAL_STATUSES
):
    sidecar["agent_session_id"] = attached.agent_session_id
    save_agent_session_sidecar(session_id, sidecar)
    # Tweak 3: capture Claude Code session UUID onto worker-managed parent.
    if not getattr(attached, "claude_session_uuid", None):
        try:
            attached.claude_session_uuid = session_id
            attached.save(update_fields=["claude_session_uuid"])
        except Exception:
            pass  # Silent failure — never block prompt submission.
    return
```

**Tweak 3 orphan cleanup — `scripts/cleanup_orphan_local_records.py`:**

One-time scan finalizing `local-XXX` PM/Teammate records that:
- `status == "pending"` AND
- `session_id` starts with `"local-"` AND
- `session_type in ("pm", "teammate")` AND
- parent (`get_parent()`) is None OR has `status` in `_TERMINAL_STATUSES`.

Action: `finalize_session(record, "abandoned", reason="health check: orphaned local-XXX (pre-#1166 cleanup)")`.

Idempotent — running twice is a no-op since the second pass finds no `pending` matches.

**Tweak 4 — log-line rewrites:**

`agent/session_health.py:759-764`:
```python
# Before:
reason = (
    f"worker alive but no progress signal, running for "
    f"{int(running_seconds)}s (>{AGENT_SESSION_HEALTH_MIN_RUNNING}s guard, "
    f"turn_count={entry.turn_count}, log_path={entry.log_path!r}, "
    f"claude_session_uuid={entry.claude_session_uuid!r})"
)
# After:
reason = (
    f"worker process registered, no progress signal in last "
    f"{int(running_seconds)}s (>{AGENT_SESSION_HEALTH_MIN_RUNNING}s guard, "
    f"turn_count={entry.turn_count}, log_path={entry.log_path!r}, "
    f"claude_session_uuid={entry.claude_session_uuid!r})"
)
```

`agent/session_health.py:903-911`:
```python
# Before:
logger.warning(
    "[session-health] Recovering stuck session %s ...",
    ...
)
# After:
logger.warning(
    "[session-health] Recovering session flagged by Tier 1 — %s ...",
    ...
)
```

Plus the `_last_progress_reason` map gains a human-readable suffix for log emission:
- `stdout_stale` → `"no stdout for {age}s"`
- `first_stdout_deadline` → `"no stdout since started_at ({age}s ago, deadline {deadline}s)"`
- `no_result_deadline` → `"no result event in {age}s (deadline {NO_RESULT_DEADLINE}s)"`

Field names and status enum values are NOT changed (constraint from issue).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `except Exception: pass` in `user_prompt_submit.py:154-157` (existing) — covered by Tweak 3 unit test `test_phantom_prevention_silent_on_save_failure`: assert hook returns silently when `attached.save()` raises, and that no `claude_session_uuid` is written.
- [ ] The `except Exception: pass` around the new `attached.save(update_fields=["claude_session_uuid"])` — same test; assert subsequent prompt submissions don't crash.
- [ ] No new exception handlers introduced in `agent/session_health.py` (the new Tier 1 branch reuses existing `_last_progress_reason` machinery).

### Empty/Invalid Input Handling
- [ ] `_has_progress()` with `entry.result_text = ""` (empty string) — Tweak 1's primary trigger; covered by regression test 1.
- [ ] `_has_progress()` with `entry.result_text is None` — falsy via `not result_text`; covered.
- [ ] `_has_progress()` with `entry.message_text = ""` — Tweak 2 helper returns `max(600, 0) = 600`; covered by Tweak 2 unit test.
- [ ] `_has_progress()` with `entry.started_at is None` — already-handled (the existing branch returns True without flagging); preserved by Tweak 1 (the new branch's `if started is not None` guard).
- [ ] `user_prompt_submit.py` with empty `session_id` — existing guard at line 49 returns early; preserved.

### Error State Rendering
- [ ] Log-line rewrites must still render `claude_session_uuid=None` (literal `None`, not `"None"`) — covered by Tweak 4 log-assertion test.
- [ ] Dashboard does not currently render `last_stdout_at` / `last_heartbeat_at` per session card. **No surface to test** — verified during recon. If a future PR adds such surfaces, they must use the same explicit-staleness phrasing (call this out in `docs/features/session-health.md`).

## Test Impact

- [ ] `tests/unit/test_session_health_phantom_guard.py` — UPDATE: add a fixture asserting `_has_progress()` returns False when `result_text == "" AND claude_session_uuid is None AND age(started_at) >= NO_RESULT_DEADLINE`. Existing assertions about phantom-guard behavior remain unchanged.
- [ ] `tests/unit/test_session_health_sibling_phantom_safety.py` — UPDATE: re-confirm slugless dev-session sharing (#944) still passes with the new Tier 1 branch. Existing assertions unchanged; add one assertion that a slugless dev session with fresh heartbeats and no result_text in <60min is NOT flagged.
- [ ] `tests/unit/test_health_check_recovery_finalization.py` — UPDATE: add a case where `_last_progress_reason == "no_result_deadline"` produces the correct counter increment (`tier1_flagged_total` and a new `tier1_flagged_no_result_deadline`).
- [ ] `tests/unit/test_hook_user_prompt_submit.py` — UPDATE: add three regression tests: (a) `claude_session_uuid` capture writes to parent when None, (b) does not overwrite when already set, (c) silent failure on save exception.
- [ ] `tests/integration/test_agent_session_lifecycle.py` — UPDATE: a worker-spawned PM session whose first turn never produces a result is finalized as `failed` after `NO_RESULT_DEADLINE + 60s`. May require time-mocking; if integration setup makes this brittle, REPLACE with a unit-level scenario in `test_session_health_phantom_guard.py`.

No tests deleted. No tests fully replaced.

## Rabbit Holes

- **Slimming the PM `--append-system-prompt`** — the issue notes that the 62858-char system prompt likely contributes to slow first turns. Out of scope for this plan. File a separate investigation issue if pursued.
- **Per-session-type `NO_RESULT_DEADLINE`** — Open Question 1 asks whether PM/Dev/Teammate should differ. The plan defaults to a single global constant; per-type tuning is out of scope.
- **Refactoring `_has_progress()` into smaller predicates** — tempting because the function is now ~110 lines, but the issue explicitly calls for additive checks. Refactoring is out of scope.
- **Replacing the `_last_progress_reason` module-global with a return-tuple** — would change the function signature and ripple into the health-check loop. Out of scope; Tweak 4 only changes log strings, not the reason mechanism.
- **Tracking `prompt_len` as a dedicated AgentSession field** — Open Question 2. The plan accepts the `len(message_text)` approximation (clamped to 500 chars) for v1.

## Risks

### Risk 1: NO_RESULT_DEADLINE default kills legitimate long PM warmups
**Impact:** A PM whose first turn legitimately takes >60 min (rare, but observed once on issue #1155 at 15 min) gets flagged → Tier 2 evaluates → if subprocess is alive (gate c) or has children (gate d), reprieve fires → no kill. **Constraint preserved.** Risk only materializes if Tier 2 also fails, which means the subprocess is already dead/zombie — in which case killing is correct.
**Mitigation:** Default 3600s (60 min) is 4x the worst observed first-turn latency. Env-tunable via `NO_RESULT_DEADLINE_SECS`. Tier 2 reprieves remain unchanged. The plan's regression test 1 explicitly covers a long-warmup PM with active children to verify Tier 2 wins.

### Risk 2: SCALE_FACTOR misfires for unusually short or long prompts
**Impact:** A 60K-char prompt scales to `max(600, 60000*15/1000) = 900s` first-stdout deadline — only 5 min vs. the observed 15 min. Could cause false flags on extreme system-prompt sessions.
**Mitigation:** Set `FIRST_RESULT_SCALE_FACTOR_MS_PER_CHAR=20` if needed (giving 1200s for 60K-char). Open Question 2 asks for the right SCALE_FACTOR. The Tier 2 reprieve still catches actively-working subprocesses.

### Risk 3: `claude_session_uuid` capture races with another writer
**Impact:** SDK auth path also writes `claude_session_uuid` on the same record. Race window: hook fires before SDK auth completes, writes `session_id` → SDK auth completes, writes the same `session_id`. Both writes are identical (both source from the Claude Code UUID); race is benign.
**Mitigation:** None needed. The set-once invariant (`if not getattr(attached, "claude_session_uuid", None)`) prevents stomping a different value. If the SDK auth path ever sets a *different* uuid (it shouldn't), the hook's check would skip the write. See Race Condition section below.

### Risk 4: Orphan cleanup script kills a legitimately-pending session
**Impact:** A `local-XXX` PM record with `status=pending` and a *running* parent gets queued and may eventually be picked up by some path. If the cleanup finalizes it as abandoned, that path breaks.
**Mitigation:** Cleanup script's filter requires the parent to be in `_TERMINAL_STATUSES` OR missing entirely. A running parent is excluded. Pre-#1166 orphans are exclusively the case where the parent already terminated (as observed in the issue's #1155 example: the `local-44ba5a95-...` child sat in pending forever after its parent's `claude_session_uuid` was discarded). **Manual dry-run pass first** — script supports `--dry-run` flag and prints candidates before finalizing.

## Race Conditions

### Race 1: `claude_session_uuid` set-once invariant
**Location:** `.claude/hooks/user_prompt_submit.py:144-150` (the new write) vs. `agent/sdk_client.py` SDK auth path.
**Trigger:** Worker spawns subprocess → SDK auth path schedules a write of `claude_session_uuid` → hook fires before SDK write completes → hook writes `session_id` → SDK write completes, writes the same `session_id`.
**Data prerequisite:** `attached` must already exist (worker created it before subprocess spawn, see `agent/sdk_client.py:1343-1369` per #1157 fix).
**State prerequisite:** `attached.status not in TERMINAL_STATUSES`.
**Mitigation:** Both writers source `claude_session_uuid` from the same Claude Code UUID. Both writes are idempotent; the second writer reads the field, sees it's already set (or sets the same value), and is a no-op. The set-once guard `if not getattr(attached, "claude_session_uuid", None)` makes the hook a no-op if SDK auth already wrote first. Race is benign — no consistency hazard.

### Race 2: Tweak 1's no-result-deadline vs. Tier 2 reprieve
**Location:** `agent/session_health.py:_has_progress()` (Tier 1, new branch) vs. `_tier2_reprieve_signal()` (Tier 2 gates).
**Trigger:** PM session running 60+ min with no `result_text`, no `claude_session_uuid`, but subprocess actively executing tools (gate `(d) children`).
**Data prerequisite:** `_has_progress()` returns False with `_last_progress_reason = "no_result_deadline"`. Tier 2 reads psutil and finds active children.
**State prerequisite:** Health check loop runs Tier 2 only when Tier 1 returned False.
**Mitigation:** Existing `if reprieve is not None: continue` at `agent/session_health.py:864-891` reprieves the kill. Reprieve counter increments, log warning at `reprieve_count >= 3`. Same flow as today's `stdout_stale` + active-subprocess case. No new race introduced.

## No-Gos (Out of Scope)

- **Per-session-type `NO_RESULT_DEADLINE`** — single global constant for v1. Per-type tuning deferred until evidence shows different session types need different deadlines.
- **Slimming the PM `--append-system-prompt`** — 62858-char prompt size is a separate investigation. File issue if pursued.
- **Refactoring `_has_progress()` into smaller predicates** — kept as a single function with additive checks per #1036/#1046 patterns.
- **Renaming any `AgentSession` field or status enum value** — Tweak 4 is purely log-string changes (constraint from issue).
- **Adding new metrics or counters beyond `tier1_flagged_no_result_deadline`** — keep the metric surface minimal.
- **Dashboard staleness rendering** — no current surface in `ui/app.py` renders `last_stdout_at` / `last_heartbeat_at` per session. Future surfaces must follow the same convention; capture as a doc note, not a code change.

## Update System

No update system changes required — this work is purely internal to the worker / hooks. The `/update` skill (`scripts/remote-update.sh`) does not need modification:
- No new dependencies.
- No new config files.
- New env vars (`NO_RESULT_DEADLINE_SECS`, `FIRST_RESULT_SCALE_FACTOR_MS_PER_CHAR`) have defaults; vault `.env` updates are optional.
- Migration via `scripts/cleanup_orphan_local_records.py` is run-once-per-machine; document in PR body that operators should run it manually after deploy.

## Agent Integration

No agent integration required — this is a worker-internal change:
- No new MCP server or tool exposure needed.
- The agent does not directly invoke the session-health detector; it observes its effects via dashboard/logs.
- No `.mcp.json` changes.
- The bridge does not import or call the new code.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-health.md` (or create if missing): document `NO_RESULT_DEADLINE`, the length-scaled first-result deadline, the Tweak 3 `claude_session_uuid` capture invariant, and the explicit-staleness logging convention.
- [ ] Add row to `docs/features/README.md` index table if a new feature doc is created.

### Inline Documentation
- [ ] Docstring update for `_has_progress()` — add a paragraph documenting the new no-result-deadline check, citing #1159.
- [ ] Docstring for new `_first_stdout_deadline_for(entry)` helper.
- [ ] Module-level comment block at `agent/session_health.py:120-160` updated to reflect the new constants.
- [ ] Inline comment on the `attached.claude_session_uuid = session_id` write in `user_prompt_submit.py` citing #1159 Tweak 3.

### External Documentation Site
N/A — this repo does not have a Sphinx/MkDocs site.

## Success Criteria

- [ ] `_has_progress()` flags sessions where `result_text == "" AND claude_session_uuid is None AND age(started_at) >= NO_RESULT_DEADLINE` (default 3600s, env-configurable). Tier 2 reprieves still apply.
- [ ] First-result deadline scales with `len(message_text)` per `_first_stdout_deadline_for(entry)`. Default `SCALE_FACTOR_MS_PER_CHAR=15`.
- [ ] `user_prompt_submit.py` writes `claude_session_uuid = session_id` onto the attached worker-managed `AgentSession` when previously None. Persisted via `save(update_fields=["claude_session_uuid"])`. Silent on save failure.
- [ ] All `[session-health]` log lines phrase observations as "no X in Ns" / "last Y Ns ago" — no bare-present-tense `alive` / `stuck` / `no progress`.
- [ ] Dashboard surfaces in `ui/` that render heartbeat / stdout / subprocess state use the same explicit-staleness wording. **Recon confirmed no current surface; success = doc note in `docs/features/session-health.md` for future authors.**
- [ ] Regression test 1: a session with `last_stdout_at` continually fresh but `result_text == ""` and `claude_session_uuid is None` for `NO_RESULT_DEADLINE + 60s` is finalized as `failed` (or `pending` if reprieve attempts < `MAX_RECOVERY_ATTEMPTS`).
- [ ] Regression test 2: a worker-spawned PM session results in exactly one `running` `AgentSession` at any time, AND the parent record's `claude_session_uuid` is populated by `user_prompt_submit.py` after the first prompt.
- [ ] Regression test 3: log assertions that no `[session-health]` message uses bare present-tense verbs like "alive" / "stuck" / "no progress" without an accompanying staleness window. Implemented as a `caplog`-based unit test scanning `caplog.records` for forbidden substrings without numeric staleness.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] One-time orphan cleanup script `scripts/cleanup_orphan_local_records.py` runs successfully in `--dry-run` mode and identifies pre-#1166 orphans (if any exist on the operator's Redis).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools.

### Team Members

- **Builder (session-health)**
  - Name: health-builder
  - Role: Implement Tweaks 1, 2, 4 in `agent/session_health.py` (constants, `_has_progress()` extension, `_first_stdout_deadline_for()` helper, log-line rewrites).
  - Agent Type: builder
  - Resume: true

- **Builder (hooks)**
  - Name: hook-builder
  - Role: Implement Tweak 3 sub-fix in `.claude/hooks/user_prompt_submit.py` and the orphan cleanup script `scripts/cleanup_orphan_local_records.py`.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (regressions)**
  - Name: test-builder
  - Role: Add regression tests 1, 2, 3 across `test_session_health_phantom_guard.py`, `test_hook_user_prompt_submit.py`, `test_health_check_recovery_finalization.py`. Update existing tests per Test Impact section.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: doc-builder
  - Role: Update `docs/features/session-health.md` and inline docstrings.
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: plan-validator
  - Role: Verify all success criteria, run pytest + ruff, confirm orphan-cleanup `--dry-run` works.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement Tweaks 1, 2, 4 in session_health.py
- **Task ID**: build-health
- **Depends On**: none
- **Validates**: `tests/unit/test_session_health_phantom_guard.py`, `tests/unit/test_session_health_sibling_phantom_safety.py`
- **Informed By**: Recon (file:line refs Confirmed)
- **Assigned To**: health-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `NO_RESULT_DEADLINE` and `FIRST_RESULT_SCALE_FACTOR_MS_PER_CHAR` constants near `agent/session_health.py:140-156`.
- Add `_first_stdout_deadline_for(entry)` helper.
- Extend `_has_progress()` with the no-result-deadline Tier 1 check after the existing stdout-stale / first-stdout-deadline branches.
- Replace bare `FIRST_STDOUT_DEADLINE` reference at line 548 with `_first_stdout_deadline_for(entry)`.
- Rewrite log lines at `agent/session_health.py:759-764` and `:903-911` for explicit-staleness phrasing.
- Add `tier1_flagged_no_result_deadline` counter at the existing tier1 metrics block (`agent/session_health.py:850-861`).

### 2. Implement Tweak 3 sub-fix in user_prompt_submit.py
- **Task ID**: build-hook
- **Depends On**: none
- **Validates**: `tests/unit/test_hook_user_prompt_submit.py`
- **Informed By**: Recon (Major drift — only `claude_session_uuid` capture remains)
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: true
- In `.claude/hooks/user_prompt_submit.py:144-150` (existing phantom-twin-prevention branch), add `attached.claude_session_uuid = session_id` write when None.
- Persist via `save(update_fields=["claude_session_uuid"])`.
- Wrap in try/except — silent on failure (existing pattern in the file).

### 3. Build orphan-cleanup script
- **Task ID**: build-cleanup
- **Depends On**: none
- **Validates**: dry-run output is non-empty if orphans exist; finalizes orphans as `abandoned` on real run.
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/cleanup_orphan_local_records.py`.
- Use `AgentSession.query.filter(status="pending")` then filter by `session_id.startswith("local-")` AND `session_type in ("pm", "teammate")`.
- For each candidate, resolve `get_parent()`. If parent is None or `parent.status in _TERMINAL_STATUSES`, finalize as `abandoned`.
- Support `--dry-run` flag (default off) — print the candidates without finalizing.

### 4. Add regression tests
- **Task ID**: build-tests
- **Depends On**: build-health, build-hook
- **Validates**: `pytest tests/unit/ -k "phantom_guard or hook_user_prompt_submit or health_check_recovery"` passes.
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Regression test 1 (Tweak 1): unit test in `test_session_health_phantom_guard.py` — fixture creates an `AgentSession` with fresh heartbeats but `result_text=""`, `claude_session_uuid=None`, `started_at = now - NO_RESULT_DEADLINE - 60s`. Assert `_has_progress(entry) is False` and `_last_progress_reason == "no_result_deadline"`. Then mock psutil to return alive subprocess + children; assert `_tier2_reprieve_signal` returns "children" (Tier 2 reprieves). Then mock psutil to return dead; assert finalize path.
- Regression test 2 (Tweak 3): unit test in `test_hook_user_prompt_submit.py` — set `AGENT_SESSION_ID` env var to a worker-managed `AgentSession` UUID with `claude_session_uuid=None`. Run hook with `prompt`, `session_id`, `cwd`. Assert: (a) no `local-XXX` record created, (b) `attached.claude_session_uuid == session_id`, (c) running again is idempotent (no overwrite). Plus a save-failure case using `mocker.patch.object(attached, "save", side_effect=Exception)` — assert hook returns silently and `attached.claude_session_uuid` ends up at whatever it was before.
- Regression test 3 (Tweak 4): caplog-based unit test scanning `[session-health]` log records. For each forbidden substring (`"alive"`, `"stuck"`, `"no progress signal"`, `"no progress"`) without an accompanying numeric staleness window (regex `\d+s ago` or `no \w+ in \d+s` or `\d+s guard`), assert no record matches.
- Update existing tests per Test Impact section.

### 5. Validate session-health changes
- **Task ID**: validate-health
- **Depends On**: build-tests
- **Assigned To**: plan-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -k "session_health or hook_user_prompt or health_check_recovery"` — all pass.
- Run `python scripts/cleanup_orphan_local_records.py --dry-run` — exits cleanly.
- Run `grep -nE 'alive|stuck|no progress' agent/session_health.py | grep -vE '\d+s|N seconds|since|in last'` — should return zero non-comment matches in active log strings.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-health
- **Assigned To**: doc-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-health.md` (create if missing): document `NO_RESULT_DEADLINE`, length-scaled first-result deadline, Tweak 3 `claude_session_uuid` invariant, explicit-staleness logging convention.
- Add row to `docs/features/README.md` index table if new feature doc.
- Ensure docstrings updated (covered by `health-builder` and `hook-builder` inline comments).

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-health, document-feature
- **Assigned To**: plan-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` — all pass.
- Run `python -m ruff check . && python -m ruff format --check .` — exit code 0.
- Verify all 8 success-criteria checkboxes met.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Targeted health tests | `pytest tests/unit/ -k "session_health or hook_user_prompt or health_check_recovery" -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No bare-present-tense in active log strings | `grep -nE '"\\\\\[session-health\\\\\]" .*"alive"' agent/session_health.py` | no matches in active log f-strings (comments may reference) |
| Cleanup script runs | `python scripts/cleanup_orphan_local_records.py --dry-run` | exit code 0 |
| New constants present | `grep -nE "NO_RESULT_DEADLINE|FIRST_RESULT_SCALE_FACTOR_MS_PER_CHAR" agent/session_health.py` | output > 0 |
| `claude_session_uuid` write present | `grep -n "attached.claude_session_uuid = session_id" .claude/hooks/user_prompt_submit.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique is run. -->

---

## Open Questions

1. **`NO_RESULT_DEADLINE` default — single global vs. per-session-type?**
   The plan defaults to a single 3600s constant for all session types. PMs can legitimately go long; Dev sessions are typically faster. Per-type tuning would be `NO_RESULT_DEADLINE_PM_SECS=3600`, `NO_RESULT_DEADLINE_DEV_SECS=1800`, etc. Current empirical data (one observation from #1155) is insufficient to justify per-type values. **Recommend single global for v1; revisit if false flags surface in production.**

2. **`FIRST_RESULT_SCALE_FACTOR_MS_PER_CHAR` value — 14ms/char observed; round to 15?**
   The #1155 observation gave ~14ms/char (15 min on 62858 chars). Rounding to 15ms/char gives ~942s (~16 min) for the same prompt — slight headroom. The plan defaults to 15. **Should the SCALE_FACTOR be more conservative (20-25ms/char) given we have only one data point?**

3. **`message_text` truncation to 500 chars vs. accurate `prompt_len` field?**
   `AgentSession.message_text` is truncated to 500 chars at storage time, so a 60K-char system prompt registers as length 500 in `_first_stdout_deadline_for(entry)`. This means scaled deadline = `max(600, 500*15/1000) = 600s` — only 10 min for what actually takes 15 min. **Should we add a `prompt_len_chars` field to `AgentSession` for accurate scaling, or accept the 500-char clamp as good-enough?** Adding a field is a schema change; out of scope for Small appetite, but blocks Tweak 2 from being fully effective on large system prompts.

