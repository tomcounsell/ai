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
| Redis reachable | `python -c "from popoto import Redis; Redis().ping()"` | Session ORM persistence |
| AgentSession model importable | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'query')"` | Used by ensure/find functions |

Run all checks: `python scripts/check_prerequisites.py docs/plans/sdlc-session-ensure-dedup.md`

## Solution

### Key Elements

- **Env-var short-circuit** in `ensure_session()`: when `VALOR_SESSION_ID` or `AGENT_SESSION_ID` is set and resolves to a real session, return it and skip create.
- **Message-text fallback** in `find_session_by_issue()`: after the existing `issue_url` match, try `message_text` against a case-insensitive regex `\bissue\s*#?\s*{N}\b`. Catches Telegram-originated PM sessions that have no URL.
- **Orphan killer CLI path** on `sdlc_session_ensure`: new `--kill-orphans` mode lists/transitions zombie sessions (`status="running"`, `session_id.startswith("sdlc-local-")`, `last_heartbeat_at is None`, `created_at` older than 10 minutes). `--dry-run` lists without modifying.
- **SKILL.md correction**: update the Step 1.5 comment in `.claude/skills/sdlc/SKILL.md` so the documentation matches behavior once the short-circuit lands.

### Flow

**Bridge-initiated path:**
Telegram message → Worker creates `tg_...` PM AgentSession → spawns Claude Code with `VALOR_SESSION_ID=tg_...` → `/sdlc` Step 1.5 runs `sdlc_session_ensure` → **env short-circuit** finds the live session → returns it → no zombie created → stage markers write to the same session → dashboard shows one PM session.

**Local-CLI path (unchanged):**
Developer runs `/sdlc` from Claude Code locally (no `VALOR_SESSION_ID`) → Step 1.5 runs `sdlc_session_ensure` → env short-circuit is a no-op → `find_session_by_issue` scans `issue_url` (miss) then `message_text` (miss, because no AgentSession exists yet) → creates `sdlc-local-{N}` → returns it.

**Zombie-cleanup path:**
Operator runs `python -m tools.sdlc_session_ensure --kill-orphans --dry-run` → tool scans AgentSession for zombie pattern → prints JSON list of candidates → operator reviews → reruns without `--dry-run` → tool transitions each via `models.session_lifecycle.transition_status(s, "killed", "zombie sdlc-local session cleanup")` → dashboard count drops.

### Technical Approach

1. **`ensure_session()` env short-circuit** (edit `tools/sdlc_session_ensure.py` near line 48, before the existing `find_session_by_issue` call):
   - Read `env_session_id = os.environ.get("VALOR_SESSION_ID") or os.environ.get("AGENT_SESSION_ID")`.
   - If set, call the existing `find_session(session_id=env_session_id)` (import from `tools._sdlc_utils`) to confirm the session actually exists in Redis.
   - If `find_session` returns a truthy session object, return `{"session_id": env_session_id, "created": False}` immediately — do NOT fall through to issue-number lookup or create.
   - If env var is set but resolves to None (stale env from a killed session), fall through to the existing issue-number path so the local fallback still works in degraded conditions.

2. **`find_session_by_issue()` message_text fallback** (edit `tools/_sdlc_utils.py:20`):
   - After the existing `issue_url.endswith(target_suffix)` loop completes with no match, run a second pass over the same `pm_sessions` list.
   - Compile `pattern = re.compile(rf"\bissue\s*#?\s*{issue_number}\b", re.IGNORECASE)` once.
   - For each session, read `getattr(s, "message_text", None) or ""`. If `pattern.search(message_text)`, return `s`.
   - Import `re` at the top of `_sdlc_utils.py`.

3. **`--kill-orphans` CLI** (extend `tools/sdlc_session_ensure.py:main`):
   - Add mutually-exclusive `--kill-orphans` flag (cannot be combined with `--issue-number`; if user provides only `--kill-orphans`, make `--issue-number` not-required).
   - Implement `_iter_orphan_sessions()` helper that runs `AgentSession.query.filter(session_type="pm", status="running")`, iterates, keeps those whose `session_id.startswith("sdlc-local-")` AND `last_heartbeat_at is None` AND `(now_utc - created_at).total_seconds() >= 600`.
   - With `--dry-run`: print JSON `{"orphans": [{"session_id": ..., "created_at": ..., "issue_url": ...}], "count": N, "killed": false}`.
   - Without `--dry-run`: iterate and call `transition_status(session, "killed", "zombie sdlc-local session cleanup")`. Track failures in a list. Print JSON with `killed: true`, per-session results, and failure list.

4. **SKILL.md comment fix** (edit `.claude/skills/sdlc/SKILL.md` around Step 1.5):
   - Current text: `This is idempotent -- running it multiple times for the same issue reuses the same session.`
   - Append: `Inside a bridge-initiated session (where VALOR_SESSION_ID is set), the call is a true no-op — it returns the already-active session without creating a new record.`

5. **Constants:**
   - `ORPHAN_AGE_SECONDS = 600` defined as a module-level constant in `sdlc_session_ensure.py` so the threshold is easy to tune.
   - Use `datetime.now(timezone.utc)` for the comparison; `AgentSession.created_at` is timezone-aware per model definition.

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] The existing `try/except Exception` in `ensure_session()` (`tools/sdlc_session_ensure.py:96`) wraps the whole function and returns `{}` on error. The env short-circuit sits inside this same try block so Redis failures during the `find_session` call degrade gracefully to the existing path. Test: simulate `find_session` raising `ConnectionError` and assert `ensure_session` still falls through to legacy create.
- [x] The new `--kill-orphans` path must not crash when a session transition fails. Each transition runs inside its own try/except; failures are recorded in the output JSON, never raised. Test: mock `transition_status` to raise, confirm CLI still exits 0 and reports the failure in the payload.

### Empty/Invalid Input Handling
- [x] `VALOR_SESSION_ID=""` (empty string) behaves identically to unset — short-circuit does not activate, falls through. Test: `os.environ["VALOR_SESSION_ID"] = ""` with no real session, assert create path runs.
- [x] `find_session_by_issue(0)` and `(-1)` still return None (existing guard). No new tests needed; existing `test_returns_empty_for_invalid_issue_number` covers.
- [x] `message_text` containing `"issue 1147"` as a substring of a larger word (e.g., `"tissue 1147"`) must NOT match. The `\b` word boundaries in the regex handle this. Test: add regression assertion.

### Error State Rendering
- [x] CLI always prints valid JSON on stdout and exits 0 (documented contract). Add a test that runs `--kill-orphans --dry-run` and asserts `json.loads(stdout)` succeeds.
- [x] If the env short-circuit branch returns a session ID, the caller (SDLC skill) must observe `created: False`. Add a test that patches env, provides a mock PM session, and asserts the result dict.

## Test Impact

- [x] `tests/unit/test_sdlc_session_ensure.py` — UPDATE: keep all six existing tests (they exercise the fallback paths that remain). Add new tests listed in Step 5 below.
- [x] No other test files touch this code path. `grep -rn "sdlc_session_ensure\|find_session_by_issue\|sdlc-local-" tests/` returns only the one file.

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
- [x] Update `docs/features/sdlc-pipeline-state.md`: add a short subsection under "Local SDLC sessions" explaining the env-var short-circuit and what happens when Step 1.5 runs inside a bridge session. Note the `--kill-orphans` operator tool and link to its usage.
- [x] Update `docs/features/sdlc-stage-tracking.md`: the existing paragraph at line 68 describes `sdlc_session_ensure` for local sessions. Append a note that bridge sessions short-circuit and do not create records.

### Inline Documentation
- [x] `ensure_session()` docstring: document the env-var short-circuit behavior and when it falls through.
- [x] `find_session_by_issue()` docstring: document the two-pass match (`issue_url` first, then `message_text` regex) and the known limitation (first match wins on multi-mention).
- [x] `_iter_orphan_sessions()` helper: docstring covering the zombie criteria (pattern, age floor, heartbeat).

### Skill Documentation
- [x] `.claude/skills/sdlc/SKILL.md` Step 1.5: correct the comment so "no-op for bridge-initiated sessions" matches real behavior after fix.

## Success Criteria

- [x] `ensure_session(1140)` called with `VALOR_SESSION_ID=tg_valor_-1003449100931_691` in env AND a live PM session matching that ID returns `{"session_id": "tg_valor_-1003449100931_691", "created": false}`. No new session created.
- [x] `ensure_session(1140)` called with `VALOR_SESSION_ID=stale_id` where no session exists falls through to the legacy path and creates `sdlc-local-1140` (preserves degraded-mode behavior).
- [x] `find_session_by_issue(1140)` returns a Telegram PM session whose `message_text="SDLC issue 1140"` and `issue_url is None`.
- [x] `find_session_by_issue(1147)` does NOT match a session whose `message_text` contains `"tissue 1147"` (word-boundary regression).
- [x] Running SDLC on a bridge-initiated session produces exactly one PM session on the dashboard (curl `/dashboard.json`, count `pm` sessions for this issue). Zero `sdlc-local-{N}` duplicates.
- [x] `python -m tools.sdlc_session_ensure --kill-orphans --dry-run` lists existing zombies without modifying them. Output is valid JSON, exit 0.
- [x] `python -m tools.sdlc_session_ensure --kill-orphans` transitions zombie sessions to `killed` and reports per-session results in JSON.
- [x] Tests in `tests/unit/test_sdlc_session_ensure.py` cover: bridge short-circuit (happy path), stale env fallback, message_text fallback match, message_text word-boundary negative case, orphan listing, orphan killing, transition failure handling in orphan killer.
- [x] `.claude/skills/sdlc/SKILL.md` Step 1.5 comment reads accurately.
- [x] Tests pass (`/do-test`).
- [x] Documentation updated (`/do-docs`).

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
- **Validates**: `tests/unit/test_sdlc_session_ensure.py`
- **Assigned To**: session-ensure-builder
- **Agent Type**: builder
- **Parallel**: true
- In `tools/sdlc_session_ensure.py`, add env-var short-circuit at the top of `ensure_session()` (before the `find_session_by_issue` call). Use `tools._sdlc_utils.find_session` to confirm the env-resolved session exists in Redis before returning it.
- On stale env (env var set, session not found), fall through to the existing path.
- In `tools/_sdlc_utils.py`, extend `find_session_by_issue()` with a second pass over the same `pm_sessions` list matching on `message_text` via a case-insensitive regex with word boundaries. Import `re`.
- Update both docstrings to describe the new behavior.

### 2. Build the `--kill-orphans` CLI
- **Task ID**: build-kill-orphans
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_session_ensure.py::TestKillOrphans`
- **Assigned To**: session-ensure-builder
- **Agent Type**: builder
- **Parallel**: true
- In `tools/sdlc_session_ensure.py`, add module-level `ORPHAN_AGE_SECONDS = 600` and a helper `_iter_orphan_sessions()` that yields zombie sessions.
- Extend `main()` with `--kill-orphans` and `--dry-run` flags. Make `--issue-number` optional when `--kill-orphans` is set; mutually exclusive otherwise.
- On `--dry-run`, print JSON listing orphans. On real run, iterate and call `transition_status(..., "killed", ...)` guarded by per-session try/except. Output JSON with results.

### 3. Extend tests
- **Task ID**: build-tests
- **Depends On**: build-core, build-kill-orphans
- **Validates**: `pytest tests/unit/test_sdlc_session_ensure.py -v`
- **Assigned To**: session-ensure-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `TestBridgeShortCircuit` class with: (a) env var set + live session returns it without create, (b) env var set + no live session falls through to legacy path, (c) empty env var string does not trigger short-circuit.
- Add `TestMessageTextFallback` class with: (a) match on "SDLC issue 1147", (b) match on "issue #1147", (c) no match on "tissue 1147" (word boundary), (d) no match when `message_text` is None.
- Add `TestKillOrphans` class with: (a) `--dry-run` lists orphans without modifying, (b) real run transitions orphans to killed, (c) transition failure does not crash CLI and is reported in output, (d) sessions newer than 10 minutes are NOT listed as orphans, (e) sessions with heartbeats are NOT listed even if old.

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
- Run `pytest tests/unit/test_sdlc_session_ensure.py -v` and verify all tests pass.
- Run `python -m ruff format .` and `python -m ruff check tools/sdlc_session_ensure.py tools/_sdlc_utils.py`.
- Run `python -m tools.sdlc_session_ensure --kill-orphans --dry-run` against the live dev Redis; confirm valid JSON output.
- Verify the SKILL.md comment reads accurately.
- Verify all Success Criteria items are checked.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_sdlc_session_ensure.py -v` | exit code 0 |
| Format clean | `python -m ruff format --check tools/sdlc_session_ensure.py tools/_sdlc_utils.py` | exit code 0 |
| Dry-run CLI works | `python -m tools.sdlc_session_ensure --kill-orphans --dry-run` | exit code 0, stdout parses as JSON |
| SKILL.md updated | `grep -c "no-op" .claude/skills/sdlc/SKILL.md` | output > 0 |
| Feature doc updated | `grep -c "VALOR_SESSION_ID\|bridge-initiated" docs/features/sdlc-pipeline-state.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique. -->

---

## Open Questions

None. The issue body includes a detailed Solution Sketch, Recon Summary, and explicit Acceptance Criteria. All four buckets (Confirmed, Revised, Pre-requisites, Dropped) are resolved. No scope ambiguity or technical unknowns require supervisor input before build.
