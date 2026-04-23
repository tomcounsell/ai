---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-24
tracking: https://github.com/tomcounsell/ai/issues/1147
last_comment_id:
---

# SDLC Session Ensure — Dedup Against Bridge Sessions

## Problem

When a Telegram-originated PM session runs `/sdlc`, Step 1.5 of the SDLC skill invokes `python -m tools.sdlc_session_ensure`. The tool is *supposed* to be a no-op in this case — the bridge already created an `AgentSession`, and `VALOR_SESSION_ID` is injected into the Claude Code environment. Instead, the tool creates a second, orphaned `sdlc-local-{N}` PM session for the same issue and transitions it to `running`. The dashboard then shows two PM sessions for the same issue; the zombie never receives a Claude turn, has no heartbeats, and is never closed.

**Current behavior:**

1. User sends "SDLC issue 1140" via Telegram. Worker creates `AgentSession` `tg_valor_-1003449100931_691` (`session_type=pm`) and executes it. `VALOR_SESSION_ID=tg_valor_...` is in the child process environment.
2. `/sdlc` Step 1.5 runs `python -m tools.sdlc_session_ensure --issue-number 1140 --issue-url ...`.
3. `ensure_session()` (`tools/sdlc_session_ensure.py:31`) calls `find_session_by_issue(1140)` (`tools/_sdlc_utils.py:20`), which scans PM sessions looking for `issue_url.endswith("/issues/1140")`. The Telegram session has `issue_url=None` (it was built from `initial_telegram_message.message_text`, not a URL), so the scan returns None.
4. `ensure_session()` creates `sdlc-local-1140`, transitions it to `running`, and returns.
5. The real work proceeds through the Telegram session. Stage markers write to the Telegram session (because `sdlc_stage_marker._find_session` prefers `VALOR_SESSION_ID` — `tools/sdlc_stage_marker.py:62`). The Telegram session completes.
6. `sdlc-local-1140` stays `running` forever with `last_heartbeat_at=None`, `turn_count=0`, `tool_call_count=0`. No worker drives it. No code closes it.

**Desired outcome:**

- When `VALOR_SESSION_ID` or `AGENT_SESSION_ID` is set and resolves to a real session, `ensure_session()` returns that session ID immediately — no scan, no create.
- `find_session_by_issue()` also matches by `message_text` (case-insensitive regex `\bissue\s*#?\s*{N}\b`) so bridge-originated PM sessions are findable when `issue_url` is None.
- No zombie `sdlc-local-{N}` sessions accumulate when running SDLC from Telegram.
- Existing zombies are listable and killable via `python -m tools.sdlc_session_ensure --kill-orphans [--dry-run]`.
- The SDLC skill's claim that Step 1.5 "is a no-op for bridge-initiated sessions" becomes true.

## Freshness Check

**Baseline commit:** `ec8dbf85d559cc21b35a4c6fe77c056304f8c401`
**Issue filed at:** `2026-04-23T11:01:10Z` (~25 hours before plan creation)
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/sdlc_session_ensure.py:31` — `ensure_session()` signature — still holds. No env var checks anywhere in the function.
- `tools/sdlc_session_ensure.py:60` — `f"sdlc-local-{issue_number}"` — still holds.
- `tools/_sdlc_utils.py:20` — `find_session_by_issue()` definition — still holds. Matches only on `issue_url.endswith("/issues/{N}")` (line 43).
- `tools/_sdlc_utils.py:51` — `find_session()` (distinct from `find_session_by_issue`) already resolves `VALOR_SESSION_ID`/`AGENT_SESSION_ID` with PM preference — still holds. This is the helper the fix can reuse directly.
- `tools/sdlc_stage_marker.py:51–93` — `_find_session` prefers env session ID, ensuring stage data still flows to the Telegram session — still holds. The zombie has empty stage_states, confirming the Recon "Dropped" bucket.
- `agent/sdk_client.py` — `VALOR_SESSION_ID` injection — verified present in repo (referenced by `_sdlc_utils.find_session` at line 58).
- `models/agent_session.py:672–685` — `message_text` is a property backed by `initial_telegram_message["message_text"]` — still holds. This is the field the extended matcher needs to read.
- `models/agent_session.py:253` — `last_heartbeat_at = DatetimeField(null=True)` — still holds. Basis for zombie detection.

**Cited sibling issues/PRs re-checked:**
- #951 — merged 2026-04-14 ("fix(#941): local SDLC pipeline state tracking"). Established `sdlc-local-{N}` contract; did not address bridge dedup.
- #941 — closed 2026-04-14 via #951. Now superseded context.
- #704 — closed 2026-04-05 ("SDLC router must use PipelineStateMachine"). Established stage_states as exclusive routing signal. Unchanged.
- #1043 — closed 2026-04-18 (oscillation guard). Confirms the fix class (dedup/prevent zombies) but not the same mechanism.

**Commits on main since issue was filed (touching referenced files):** None.
```
$ git log --oneline --since=2026-04-23T11:01:10Z -- tools/sdlc_session_ensure.py tools/_sdlc_utils.py tools/sdlc_stage_marker.py tests/unit/test_sdlc_session_ensure.py
(empty)
```

**Active plans in `docs/plans/` overlapping this area:** None. `grep -r "#1147" docs/plans/` returns zero hits.

**Notes:** Everything cited in the issue is still accurate at baseline commit. No drift, no overlap, no adjacent system change.

## Prior Art

- **PR #951** — `fix(#941): local SDLC pipeline state tracking` (merged 2026-04-14). Introduced `tools/sdlc_session_ensure.py` with the `sdlc-local-{N}` contract. Designed for local-only Claude Code sessions. Did NOT handle the bridge case where `VALOR_SESSION_ID` is already set; the tool always scans by issue_url, misses bridge sessions without URLs, and creates a duplicate. This is the gap #1147 closes.
- **#704** — SDLC router must use PipelineStateMachine (closed 2026-04-05). Set the invariant that stage_states lives on exactly one session. This fix upholds that invariant by preventing state-splitting across duplicate sessions.
- **#1043** — SDLC dispatches /do-pr-review 8 times (closed 2026-04-18). Same failure class (duplicated dispatches/sessions) but unrelated mechanism (oscillation guard at router level, not session creation).
- No closed issues matched search terms `"sdlc_session_ensure zombie"` — confirms this is the first report.

## Data Flow

1. **Entry point:** User sends "SDLC issue 1147" via Telegram → bridge (`bridge/telegram_bridge.py`) → worker (`python -m worker`) creates `AgentSession` via `AgentSession.create_telegram(...)` with `message_text="SDLC issue 1147"`, `issue_url=None`.
2. **SDK spawn:** Worker invokes Claude Code via `agent/sdk_client.py`, which exports `VALOR_SESSION_ID=<telegram_session_id>` into the child process environment.
3. **SDLC router Step 1.5:** `/sdlc` shell block runs `python -m tools.sdlc_session_ensure --issue-number 1147`.
4. **Current (broken) `ensure_session()`:** calls `find_session_by_issue(1147)` → scans PM sessions → no `issue_url` match → creates `sdlc-local-1147`.
5. **Fixed `ensure_session()`:** reads `VALOR_SESSION_ID` → calls `find_session(session_id=env_val)` → confirms live PM session → returns `{"session_id": env_val, "created": False}`. No create. Exit.
6. **Fallback (no env vars, local Claude Code):** same as today — `find_session_by_issue` scans `issue_url` AND `message_text`, creates `sdlc-local-{N}` if nothing found.
7. **Downstream:** `/sdlc` continues. `sdlc_stage_marker._find_session` also prefers `VALOR_SESSION_ID`, so stage markers land on the same session that `ensure_session` now returned. No split.
8. **Output:** Dashboard shows exactly one PM session for the issue; zombie count holds at zero.

## Architectural Impact

- **New dependencies:** None. `os.environ` is already imported at `sdlc_session_ensure.py:25`; `find_session` already exists at `_sdlc_utils.py:51`.
- **Interface changes:**
  - `ensure_session()` gains an env-var short-circuit branch before the existing issue_url scan. Return contract unchanged.
  - `find_session_by_issue()` extended with a secondary match predicate (`message_text` regex). Return contract unchanged.
  - New CLI flag `--kill-orphans` added to `sdlc_session_ensure` with optional `--dry-run`. Net-additive; no existing flag semantics altered.
- **Coupling:** Zero new cross-module coupling. Reuses existing helpers and Popoto queries.
- **Data ownership:** No change. `AgentSession` still owns session state; `stage_states` still lives on whichever PM session `_find_session` resolves.
- **Reversibility:** Fully reversible with a single-file revert of each touched file.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1 (standard PR review)

Three tightly-scoped code changes plus test coverage plus a one-line SKILL.md comment fix. No new modules, no new abstractions, no new dependencies.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | Session ORM persistence (popoto 5.x does not export `Redis` at package level) |
| AgentSession model importable | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'query')"` | Used by ensure/find functions |
| `finalize_session` helper importable | `python -c "from models.session_lifecycle import finalize_session"` | Required by `--kill-orphans` for terminal status transitions |

Run all checks: `python scripts/check_prerequisites.py docs/plans/sdlc-session-ensure-dedup.md`

## Solution

### Key Elements

- **Env-var short-circuit** in `ensure_session()`: when `VALOR_SESSION_ID` or `AGENT_SESSION_ID` is set and resolves to a real session, return it and skip create.
- **Message-text fallback** in `find_session_by_issue()`: after the existing `issue_url` match, try `message_text` against a case-insensitive regex `\bissue\s*#?\s*{N}\b`. Catches Telegram-originated PM sessions that have no URL.
- **Orphan killer CLI path** on `sdlc_session_ensure`: new `--kill-orphans` mode lists/finalizes zombie sessions (`status="running"`, `session_id.startswith("sdlc-local-")`, `last_heartbeat_at is None`, `created_at` older than 10 minutes). `--dry-run` lists without modifying. Uses `finalize_session()` (not `transition_status`) because `"killed"` is a terminal status and `transition_status()` raises `ValueError` on terminal targets.
- **SKILL.md correction**: update the Step 1.5 comment in `.claude/skills/sdlc/SKILL.md` so the documentation matches behavior once the short-circuit lands.

### Flow

**Bridge-initiated path:**
Telegram message → Worker creates `tg_...` PM AgentSession → spawns Claude Code with `VALOR_SESSION_ID=tg_...` → `/sdlc` Step 1.5 runs `sdlc_session_ensure` → **env short-circuit** finds the live session → returns it → no zombie created → stage markers write to the same session → dashboard shows one PM session.

**Local-CLI path (unchanged):**
Developer runs `/sdlc` from Claude Code locally (no `VALOR_SESSION_ID`) → Step 1.5 runs `sdlc_session_ensure` → env short-circuit is a no-op → `find_session_by_issue` scans `issue_url` (miss) then `message_text` (miss, because no AgentSession exists yet) → creates `sdlc-local-{N}` → returns it.

**Zombie-cleanup path:**
Operator runs `python -m tools.sdlc_session_ensure --kill-orphans --dry-run` → tool scans AgentSession for zombie pattern → prints JSON list of candidates → operator reviews → reruns without `--dry-run` → tool finalizes each via `models.session_lifecycle.finalize_session(s, "killed", reason="zombie sdlc-local session cleanup", skip_auto_tag=True, skip_checkpoint=True, skip_parent=True)` → dashboard count drops. `transition_status()` must NOT be used here — it rejects terminal statuses by design (see `models/session_lifecycle.py:264–269`).

### Technical Approach

1. **`ensure_session()` env short-circuit** (edit `tools/sdlc_session_ensure.py` near line 48, before the existing `find_session_by_issue` call):
   - Read `env_session_id = os.environ.get("VALOR_SESSION_ID") or os.environ.get("AGENT_SESSION_ID")`.
   - If set (non-empty string), call the existing `find_session(session_id=env_session_id)` (import from `tools._sdlc_utils`) to confirm the session actually exists in Redis.
   - **Gate on `session_type == "pm"`**: after finding the session, check `getattr(resolved, "session_type", None) == "pm"`. If the env points to a Dev or Teammate session (e.g., during cross-role debugging), DO NOT short-circuit — fall through to the legacy path so PM stage_states do not end up written to a non-PM session.
   - If `find_session` returns a truthy PM session, return `{"session_id": env_session_id, "created": False}` immediately — do NOT fall through to issue-number lookup or create.
   - If env var is unset, empty, resolves to None (stale env from a killed session), or resolves to a non-PM session, fall through to the existing issue-number path so the local fallback still works in degraded conditions.

2. **`find_session_by_issue()` message_text fallback** (edit `tools/_sdlc_utils.py:20`):
   - After the existing `issue_url.endswith(target_suffix)` loop completes with no match, run a second pass over the same `pm_sessions` list.
   - Compile `pattern = re.compile(rf"\bissue\s*#?\s*{issue_number}\b", re.IGNORECASE)` once.
   - For each session, read `getattr(s, "message_text", None) or ""`. If `pattern.search(message_text)`, return `s`.
   - Import `re` at the top of `_sdlc_utils.py`.

3. **`--kill-orphans` CLI** (extend `tools/sdlc_session_ensure.py:main`):
   - Add mutually-exclusive `--kill-orphans` flag (cannot be combined with `--issue-number`; if user provides only `--kill-orphans`, make `--issue-number` not-required).
   - Implement `_iter_orphan_sessions()` helper that runs `AgentSession.query.filter(session_type="pm", status="running")`, iterates, keeps those whose `session_id.startswith("sdlc-local-")` AND `last_heartbeat_at is None` AND `(now_utc - created_at).total_seconds() >= ORPHAN_AGE_SECONDS`.
   - With `--dry-run`: print JSON `{"orphans": [{"session_id": ..., "created_at": ..., "issue_url": ...}], "count": N, "killed": false}` and exit 0.
   - Without `--dry-run`: iterate and call `finalize_session(session, "killed", reason="zombie sdlc-local session cleanup", skip_auto_tag=True, skip_checkpoint=True, skip_parent=True)`. Track per-session results: `{"session_id": ..., "result": "killed"}` on success, `{"session_id": ..., "result": "failed", "error": str(e)}` on exception. Print JSON with `killed: true`, `count`, per-session results list, and `failures` count. Exit code is always 0 — per-session failures are reported in the payload, never raised (matches module docstring contract).
   - **Critical**: do NOT call `transition_status(session, "killed", ...)` — `transition_status()` raises `ValueError` on terminal statuses (see `models/session_lifecycle.py:264–269`). The correct helper is `finalize_session()`. The `skip_auto_tag=True, skip_checkpoint=True, skip_parent=True` flags are required because zombie sessions have no meaningful work to tag, no branch to checkpoint, and no parent to notify.

4. **SKILL.md comment fix** (edit `.claude/skills/sdlc/SKILL.md` around Step 1.5):
   - Current text: `This is idempotent -- running it multiple times for the same issue reuses the same session.`
   - Append: `Inside a bridge-initiated session (where VALOR_SESSION_ID is set), the call is a true no-op — it returns the already-active session without creating a new record.`

5. **Constants:**
   - `ORPHAN_AGE_SECONDS = 600` defined as a module-level constant in `sdlc_session_ensure.py` so the threshold is easy to tune.
   - Use `datetime.now(timezone.utc)` for the comparison; `AgentSession.created_at` is timezone-aware per model definition.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The existing `try/except Exception` in `ensure_session()` (`tools/sdlc_session_ensure.py:96`) wraps the whole function and returns `{}` on error. The env short-circuit sits inside this same try block so Redis failures during the `find_session` call degrade gracefully to the existing path. Test: simulate `find_session` raising `ConnectionError` and assert `ensure_session` still falls through to legacy create.
- [ ] The new `--kill-orphans` path must not crash when a session transition fails. Each transition runs inside its own try/except; failures are recorded in the output JSON, never raised. Test: mock `transition_status` to raise, confirm CLI still exits 0 and reports the failure in the payload.

### Empty/Invalid Input Handling
- [ ] `VALOR_SESSION_ID=""` (empty string) behaves identically to unset — short-circuit does not activate, falls through. Test: `os.environ["VALOR_SESSION_ID"] = ""` with no real session, assert create path runs.
- [ ] `find_session_by_issue(0)` and `(-1)` still return None (existing guard). No new tests needed; existing `test_returns_empty_for_invalid_issue_number` covers.
- [ ] `message_text` containing `"issue 1147"` as a substring of a larger word (e.g., `"tissue 1147"`) must NOT match. The `\b` word boundaries in the regex handle this. Test: add regression assertion.

### Error State Rendering
- [ ] CLI always prints valid JSON on stdout and exits 0 (documented contract). Add a test that runs `--kill-orphans --dry-run` and asserts `json.loads(stdout)` succeeds. Add a second test for the real `--kill-orphans` path (no `--dry-run`) verifying exit 0 even when `finalize_session` is mocked to raise — the failure must appear in the per-session result list, not as a non-zero exit.
- [ ] If the env short-circuit branch returns a session ID, the caller (SDLC skill) must observe `created: False`. Add a test that patches env, provides a mock PM session, and asserts the result dict.
- [ ] Non-PM env-resolved session MUST fall through (C2): patch env to point at a session whose `session_type="dev"` and confirm the result is NOT the env session ID — create path runs instead. Guards against cross-role state contamination.

### Orphan Age Boundary Coverage (C4)
- [ ] Session exactly at `ORPHAN_AGE_SECONDS` boundary is listed as orphan: `created_at = now - 600s` must be `>=` threshold. Test: freeze time, create a mock session at precisely the boundary, assert it appears in orphan list.
- [ ] Session at `ORPHAN_AGE_SECONDS - 1s` is NOT listed. Test: boundary - 1 second, assert absent from list.
- [ ] Session at `ORPHAN_AGE_SECONDS + 1s` IS listed. Test: boundary + 1 second, assert present in list.
- [ ] Session with `last_heartbeat_at` set (any non-None value) is NEVER listed even if older than threshold. Test: boundary + 1 hour but `last_heartbeat_at = now`, assert absent.
- [ ] Session whose `session_id` does not start with `sdlc-local-` is NEVER listed even if all other criteria match. Test: session_id="tg_valor_123", `last_heartbeat_at=None`, created 2h ago — assert absent (bridge sessions are out of scope for this cleanup).

## Test Impact

- [ ] `tests/unit/test_sdlc_session_ensure.py` — UPDATE: keep all existing tests (they exercise the fallback paths that remain). Add `TestBridgeShortCircuit`, `TestMessageTextFallback` (where applicable), and `TestKillOrphans` classes as described in Step by Step Task 3.
- [ ] `tests/unit/test_sdlc_utils.py` — UPDATE: this file owns the `find_session_by_issue` tests. The existing `test_returns_none_when_no_match` test asserts the current `issue_url`-only match behavior. Add `TestMessageTextFallback` class covering: (a) match on `message_text="SDLC issue 1147"`, (b) match on `"issue #1147"`, (c) no match on `"tissue 1147"` (word boundary), (d) no match when `message_text` is None/empty, (e) fallback does not trigger when `issue_url` already matches (preserves priority).
- [ ] `tests/integration/test_sdlc_session_ensure_integration.py` — NEW: integration test that drives the headline dashboard claim. Given a simulated bridge AgentSession (real Popoto Redis write, `session_type="pm"`, `message_text="SDLC issue 1147"`, `issue_url=None`), invoke `ensure_session(1147)` with `VALOR_SESSION_ID=<bridge_session_id>` in env, then assert exactly one PM session exists for that issue in Redis (`len(AgentSession.query.filter(session_type="pm")) == 1` scoped to the test's `project_key`). Clean up via `instance.delete()` in teardown per CLAUDE.md manual testing hygiene.
- [ ] `grep -rn "sdlc_session_ensure\|find_session_by_issue\|sdlc-local-" tests/` returns only these three files after the change. No other test files touch this code path.

## Rabbit Holes

- **Unifying `find_session_by_issue` and `find_session`**: tempting to collapse both helpers into one. Don't. They have different contracts (`find_session` is env-aware; `find_session_by_issue` is a pure-scan helper called from multiple places). Refactor is out of scope.
- **Indexing PM sessions by issue_url**: the current linear scan runs in `ensure_session` and `sdlc_stage_marker`. Adding a secondary index would shave microseconds on each call. Not worth the complexity at <100 PM session scale. Existing NOTE at `_sdlc_utils.py:36–38` already acknowledges this.
- **Generalized zombie detection**: tempting to add a full sweep for all `status="running"` sessions with no heartbeat. Stay narrow — kill only the zombies in `sdlc-local-` pattern. The watchdog already handles stuck bridge sessions (`docs/features/bridge-self-healing.md`).
- **Renaming `sdlc-local-{N}` to something friendlier**: out of scope and would break dashboards/tooling.
- **Backfilling `issue_url` on existing Telegram AgentSessions**: tempting as an alternative to the message_text fallback. Not worth it — the fallback is simpler, one-way-in, and doesn't need a Redis migration.

## Risks

### Risk 1: `VALOR_SESSION_ID` points at a dead session
**Impact:** If the bridge session was killed mid-flight but the env var lingers (Claude Code subprocess outlives the worker's AgentSession lifetime somehow), the short-circuit would return a stale ID and the caller would write stage state to a dead record.
**Mitigation:** The short-circuit calls `find_session(session_id=env_val)` which does a live Redis lookup. Returns None on missing record. We only short-circuit on truthy lookup. On None, fall through to the existing path so the local fallback still creates a usable session.

### Risk 2: `message_text` regex matches unrelated issues in a multi-issue conversation
**Impact:** If a Telegram PM session's `message_text` contains "issue 1140 and issue 1147", a lookup for 1147 could match the wrong session.
**Mitigation:** Match is only used as a fallback when `issue_url` is None. Bridge sessions today carry exactly one originating message; multi-issue mentions are rare. The `\b` word boundary plus `#?` handling covers "issue 1147", "issue #1147", "SDLC issue 1147" — typical formats. We document the known limitation in the function docstring. If two valid matches exist, we return the first (newest if query ordering guarantees it; otherwise arbitrary — acceptable since either session is a legitimate tracker).

### Risk 3: Orphan killer races a worker that resurrects a zombie
**Impact:** If a worker is recovering a `sdlc-local-{N}` session at the exact moment the killer runs, the killer could transition it to `killed` mid-recovery.
**Mitigation:** The 10-minute age floor (`ORPHAN_AGE_SECONDS = 600`) means a session must have existed for >=10 minutes with zero heartbeats. Worker recovery either writes a heartbeat within that window (moving it out of the zombie set) or has given up itself. `--dry-run` is the default-recommended flow in docs. The transition call is idempotent (re-calling on `killed` is a no-op).

## Race Conditions

No new race conditions introduced.

- The env short-circuit is a pure read path (env var + one Redis lookup). No state is mutated.
- The `message_text` fallback is a pure read over the same `pm_sessions` list.
- The orphan killer iterates sequentially; each `transition_status` call is atomic at the Popoto layer. Concurrent invocations of `--kill-orphans` on the same zombie would both attempt `transition_status(..., "killed")`; the second is a no-op.

All operations remain synchronous and Redis-single-call at the Popoto-ORM layer. No new async primitives, locks, or multi-step transactions.

## No-Gos (Out of Scope)

- Backfilling `issue_url` onto existing Telegram AgentSessions.
- Refactoring `find_session` and `find_session_by_issue` into a single helper.
- Indexing PM sessions by issue_url in Redis.
- General-purpose zombie detection for non-`sdlc-local-` sessions (watchdog's job).
- Renaming `sdlc-local-{N}` or changing its lifecycle semantics.
- Changing how the bridge stores `issue_url` on Telegram sessions.
- Changing `sdlc_stage_marker`'s session resolution order (already correct).

## Update System

No update system changes required — this fix modifies two Python files, one skill markdown file, and extends a CLI with a new flag. No new deps, no new services, no migration, no config changes propagated via `/update`.

## Agent Integration

No agent integration required — `sdlc_session_ensure` is not exposed as an MCP tool. It is invoked by the `/sdlc` skill's shell block at Step 1.5 and by operators via CLI. The bridge does not import it. The fix is self-contained within tools/ and the skill definition.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-pipeline-state.md`: add a short subsection under "Local SDLC sessions" explaining the env-var short-circuit and what happens when Step 1.5 runs inside a bridge session. Note the `--kill-orphans` operator tool and link to its usage.
- [ ] Update `docs/features/sdlc-stage-tracking.md`: the existing paragraph at line 68 describes `sdlc_session_ensure` for local sessions. Append a note that bridge sessions short-circuit and do not create records.

### Inline Documentation
- [ ] `ensure_session()` docstring: document the env-var short-circuit behavior and when it falls through.
- [ ] `find_session_by_issue()` docstring: document the two-pass match (`issue_url` first, then `message_text` regex) and the known limitation (first match wins on multi-mention).
- [ ] `_iter_orphan_sessions()` helper: docstring covering the zombie criteria (pattern, age floor, heartbeat).

### Skill Documentation
- [ ] `.claude/skills/sdlc/SKILL.md` Step 1.5: correct the comment so "no-op for bridge-initiated sessions" matches real behavior after fix.

## Success Criteria

- [ ] `ensure_session(1140)` called with `VALOR_SESSION_ID=tg_valor_-1003449100931_691` in env AND a live PM session matching that ID returns `{"session_id": "tg_valor_-1003449100931_691", "created": false}`. No new session created.
- [ ] `ensure_session(1140)` called with `VALOR_SESSION_ID=stale_id` where no session exists falls through to the legacy path and creates `sdlc-local-1140` (preserves degraded-mode behavior).
- [ ] `ensure_session(1140)` called with `VALOR_SESSION_ID` pointing at a Dev session (`session_type="dev"`) falls through to the legacy path — no PM stage_states written to a non-PM session (C2).
- [ ] `find_session_by_issue(1140)` returns a Telegram PM session whose `message_text="SDLC issue 1140"` and `issue_url is None`.
- [ ] `find_session_by_issue(1147)` does NOT match a session whose `message_text` contains `"tissue 1147"` (word-boundary regression).
- [ ] Integration test in `tests/integration/test_sdlc_session_ensure_integration.py` proves that running `ensure_session()` with a real bridge-style PM session in Redis produces exactly ONE PM session for the issue (the original bridge session) — zero `sdlc-local-{N}` duplicates created.
- [ ] Running SDLC on a real bridge-initiated session produces exactly one PM session on the dashboard (curl `/dashboard.json`, count `pm` sessions for this issue). Zero `sdlc-local-{N}` duplicates. This is the headline manual-verification criterion.
- [ ] `python -m tools.sdlc_session_ensure --kill-orphans --dry-run` lists existing zombies without modifying them. Output is valid JSON, exit 0.
- [ ] `python -m tools.sdlc_session_ensure --kill-orphans` finalizes zombie sessions to `killed` via `finalize_session()` (NOT `transition_status()`) and reports per-session results in JSON. Exit code 0 even when some transitions fail.
- [ ] Orphan detection respects the `>=` boundary at `ORPHAN_AGE_SECONDS`: sessions exactly at threshold ARE listed, one second under are NOT.
- [ ] Orphan detection ignores sessions whose `session_id` does not start with `sdlc-local-` (bridge sessions stay out of scope).
- [ ] Tests in `tests/unit/test_sdlc_session_ensure.py`, `tests/unit/test_sdlc_utils.py`, and `tests/integration/test_sdlc_session_ensure_integration.py` cover: bridge short-circuit (happy path), stale env fallback, non-PM env fallback, message_text fallback match, message_text word-boundary negative case, message_text None/empty handling, issue_url priority preserved, orphan listing, orphan killing via `finalize_session`, `finalize_session` failure handling, orphan-age boundary cases (at/under/over), non-`sdlc-local-` skip.
- [ ] `.claude/skills/sdlc/SKILL.md` Step 1.5 comment reads accurately.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (session-ensure)**
  - Name: `session-ensure-builder`
  - Role: Implement env short-circuit, message_text fallback, and `--kill-orphans` CLI. Update docstrings and SKILL.md.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (session-ensure)**
  - Name: `session-ensure-tester`
  - Role: Extend `tests/unit/test_sdlc_session_ensure.py` with the new scenarios listed in Success Criteria. Ensure existing tests still pass.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (session-ensure)**
  - Name: `session-ensure-validator`
  - Role: Run full test file, confirm zero zombie PM sessions after exercising the bridge-simulation test, verify CLI JSON output shape.
  - Agent Type: validator
  - Resume: true

- **Documentarian (session-ensure)**
  - Name: `session-ensure-docs`
  - Role: Update `docs/features/sdlc-pipeline-state.md`, `docs/features/sdlc-stage-tracking.md`, and the SKILL.md Step 1.5 comment.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Tier 1 Core as listed in PLAN_TEMPLATE.md.

## Step by Step Tasks

### 1. Build the env short-circuit and message_text fallback
- **Task ID**: build-core
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_session_ensure.py`, `tests/unit/test_sdlc_utils.py`
- **Assigned To**: session-ensure-builder
- **Agent Type**: builder
- **Parallel**: true
- In `tools/sdlc_session_ensure.py`, add env-var short-circuit at the top of `ensure_session()` (before the `find_session_by_issue` call). Use `tools._sdlc_utils.find_session` to confirm the env-resolved session exists in Redis before returning it.
- **Gate the short-circuit on `session_type == "pm"`**: if the resolved session's `session_type` is anything other than `"pm"` (e.g., `"dev"`, `"teammate"`), fall through to the legacy path. Do NOT write PM stage_states to a non-PM session.
- On stale env (env var set, session not found), fall through to the existing path.
- In `tools/_sdlc_utils.py`, extend `find_session_by_issue()` with a second pass over the same `pm_sessions` list matching on `message_text` via a case-insensitive regex with word boundaries (`re.compile(rf"\bissue\s*#?\s*{issue_number}\b", re.IGNORECASE)`). Import `re`.
- Update both docstrings to describe the new behavior.

### 2. Build the `--kill-orphans` CLI
- **Task ID**: build-kill-orphans
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_session_ensure.py::TestKillOrphans`
- **Assigned To**: session-ensure-builder
- **Agent Type**: builder
- **Parallel**: true
- In `tools/sdlc_session_ensure.py`, add module-level `ORPHAN_AGE_SECONDS = 600` and a helper `_iter_orphan_sessions()` that yields zombie sessions (`session_type="pm"`, `status="running"`, `session_id.startswith("sdlc-local-")`, `last_heartbeat_at is None`, `(now_utc - created_at).total_seconds() >= ORPHAN_AGE_SECONDS`).
- Extend `main()` with `--kill-orphans` and `--dry-run` flags. Make `--issue-number` optional when `--kill-orphans` is set; mutually exclusive otherwise.
- On `--dry-run`, print JSON listing orphans and exit 0. On real run, iterate and call `finalize_session(session, "killed", reason="zombie sdlc-local session cleanup", skip_auto_tag=True, skip_checkpoint=True, skip_parent=True)` (imported from `models.session_lifecycle`) guarded by per-session try/except. **Do NOT use `transition_status()`** — it raises `ValueError` on terminal statuses. Output JSON with per-session result entries (`{"session_id": ..., "result": "killed"}` or `{"session_id": ..., "result": "failed", "error": ...}`), total `count`, `failures` count, and `killed: true` marker. Exit code is always 0 regardless of per-session failures (matches the module docstring's "always exit 0" contract).

### 3. Extend tests
- **Task ID**: build-tests
- **Depends On**: build-core, build-kill-orphans
- **Validates**: `pytest tests/unit/test_sdlc_session_ensure.py tests/unit/test_sdlc_utils.py tests/integration/test_sdlc_session_ensure_integration.py -v`
- **Assigned To**: session-ensure-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- In `tests/unit/test_sdlc_session_ensure.py`, add `TestBridgeShortCircuit` class with: (a) env var set + live PM session returns it without create, (b) env var set + no live session falls through to legacy path, (c) empty env var string does not trigger short-circuit, (d) env var set + resolved session has `session_type="dev"` — short-circuit must NOT activate (C2 regression).
- In `tests/unit/test_sdlc_utils.py`, add `TestMessageTextFallback` class with: (a) match on "SDLC issue 1147", (b) match on "issue #1147", (c) no match on "tissue 1147" (word boundary), (d) no match when `message_text` is None, (e) no match when `message_text=""`, (f) priority test — when both `issue_url` and `message_text` could match, `issue_url` match wins (existing behavior preserved).
- In `tests/unit/test_sdlc_session_ensure.py`, add `TestKillOrphans` class with: (a) `--dry-run` lists orphans without modifying, (b) real run finalizes orphans to killed via `finalize_session`, (c) `finalize_session` failure does not crash CLI (exit code 0) and is reported in output `failures` list, (d) sessions newer than `ORPHAN_AGE_SECONDS` are NOT listed as orphans, (e) sessions with heartbeats are NOT listed even if old, (f) boundary test at exactly `ORPHAN_AGE_SECONDS` (IS listed, `>=` threshold), (g) boundary test at `ORPHAN_AGE_SECONDS - 1s` (NOT listed), (h) boundary test at `ORPHAN_AGE_SECONDS + 1s` (IS listed), (i) session_id not starting with `sdlc-local-` is never listed even when all other criteria match, (j) mock `finalize_session` to assert it was called with `skip_auto_tag=True, skip_checkpoint=True, skip_parent=True, reason="zombie sdlc-local session cleanup"` and NOT called as `transition_status`.
- Create `tests/integration/test_sdlc_session_ensure_integration.py` with one integration test: create a real bridge-style PM `AgentSession` via Popoto (`session_type="pm"`, `message_text="SDLC issue 9999"`, `issue_url=None`, `project_key="test-sdlc-ensure-int"`), set `VALOR_SESSION_ID` env var, invoke `ensure_session(9999)`, assert result equals `{"session_id": <bridge_id>, "created": False}`, then scan `AgentSession.query.filter(session_type="pm")` and assert no `sdlc-local-9999` was created. Teardown deletes all sessions with `project_key="test-sdlc-ensure-int"` via `instance.delete()` per CLAUDE.md hygiene.

### 4. Update documentation
- **Task ID**: build-docs
- **Depends On**: build-core, build-kill-orphans
- **Assigned To**: session-ensure-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/sdlc-pipeline-state.md` with the bridge short-circuit note and `--kill-orphans` usage.
- Update `docs/features/sdlc-stage-tracking.md` paragraph at line 68 with the bridge behavior note.
- Edit `.claude/skills/sdlc/SKILL.md` Step 1.5 comment so it matches post-fix reality.

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: build-tests, build-docs
- **Assigned To**: session-ensure-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_sdlc_session_ensure.py tests/unit/test_sdlc_utils.py tests/integration/test_sdlc_session_ensure_integration.py -v` and verify all tests pass.
- Run `python -m ruff format .` (formatting only per repo convention — no `ruff check`).
- Run `python -m tools.sdlc_session_ensure --kill-orphans --dry-run` against the live dev Redis; confirm valid JSON output and exit code 0.
- Grep the codebase to confirm NO remaining `transition_status(..., "killed"` references in the new code (`grep -rn 'transition_status.*killed' tools/sdlc_session_ensure.py` returns nothing).
- Verify the SKILL.md comment reads accurately.
- Verify all Success Criteria items are checked.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_sdlc_session_ensure.py tests/unit/test_sdlc_utils.py -v` | exit code 0 |
| Integration test passes | `pytest tests/integration/test_sdlc_session_ensure_integration.py -v` | exit code 0 |
| Format clean | `python -m ruff format --check tools/sdlc_session_ensure.py tools/_sdlc_utils.py` | exit code 0 |
| Dry-run CLI works | `python -m tools.sdlc_session_ensure --kill-orphans --dry-run` | exit code 0, stdout parses as JSON |
| Real CLI uses `finalize_session` | `grep -c "finalize_session" tools/sdlc_session_ensure.py` | output > 0 |
| No `transition_status` for killed | `grep -c "transition_status.*killed" tools/sdlc_session_ensure.py` | output = 0 |
| SKILL.md updated | `grep -c "no-op" .claude/skills/sdlc/SKILL.md` | output > 0 |
| Feature doc updated | `grep -c "VALOR_SESSION_ID\|bridge-initiated" docs/features/sdlc-pipeline-state.md` | output > 0 |

## Critique Results

Critique run (2026-04-24): 10 findings (2 blockers, 5 concerns, 3 nits). Verdict: **NEEDS REVISION**. All 2 blockers and 5 concerns addressed in this revision pass:

**Blockers (resolved):**
- **B1** — `transition_status(session, "killed", ...)` would raise `ValueError` because `"killed"` is a terminal status (see `models/session_lifecycle.py:264–269`). **Fix:** Solution, Technical Approach step 3, Step-by-step task 2, Success Criteria, and Verification table all now reference `finalize_session(session, "killed", reason=..., skip_auto_tag=True, skip_checkpoint=True, skip_parent=True)` as the correct helper. Grep verification added to validator task.
- **B2** — Prerequisite check `python -c "from popoto import Redis; Redis().ping()"` fails because popoto 5.x does not export `Redis` at package level. **Fix:** Replaced with `from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()` (verified working against live Redis). Added a third prerequisite row for `finalize_session` importability.

**Concerns (resolved):**
- **C1** — Test Impact wrongly claimed only `test_sdlc_session_ensure.py` was affected. **Fix:** `tests/unit/test_sdlc_utils.py` now listed as UPDATE with the specific `TestMessageTextFallback` scenarios the file owns (including priority preservation for `issue_url` matches).
- **C2** — Env-var short-circuit did not verify `session_type == "pm"`. **Fix:** Technical Approach step 1 now explicitly gates on `session_type == "pm"`; Step-by-step task 1 repeats the gate requirement; Test Impact adds a regression test for the Dev-session-in-env case; Success Criteria adds an explicit acceptance item.
- **C3** — Headline dashboard claim had no integration test. **Fix:** New `tests/integration/test_sdlc_session_ensure_integration.py` described in Test Impact and Step-by-step task 3, exercising real Popoto writes with a simulated bridge session and asserting zero `sdlc-local-{N}` creation.
- **C4** — Orphan-age boundary cases were not covered. **Fix:** "Orphan Age Boundary Coverage" subsection added to Failure Path Test Strategy with five explicit boundary tests (at threshold, -1s, +1s, with heartbeat, non-`sdlc-local-` id). Step-by-step task 3 lists them as required test cases.
- **C5** — `--kill-orphans` exit-code contract conflicted with module-level "always exit 0" docstring. **Fix:** Technical Approach step 3, Step-by-step task 2, and Failure Path Test Strategy now explicitly state exit code 0 is invariant even when per-session `finalize_session` calls fail — failures surface only in the JSON `failures` list.

**Nits:** Not addressed in this revision pass (NITs do not block READY TO BUILD per `do-plan-critique` contract). They will be considered during `/do-build` if the builder has spare cycles.

Revision applied: 2026-04-24. Plan is now expected to pass critique and route to `/do-build`.

---

## Open Questions

None. The issue body includes a detailed Solution Sketch, Recon Summary, and explicit Acceptance Criteria. All four buckets (Confirmed, Revised, Pre-requisites, Dropped) are resolved. No scope ambiguity or technical unknowns require supervisor input before build.
