---
status: Planning
type: chore
appetite: Small
owner: valorengels
created: 2026-05-13
tracking: https://github.com/tomcounsell/ai/issues/1379
last_comment_id: null
---

# Calendar Event Guards: Session Slug Requirement and 12-Minute Blocks

## Problem

Calendar events are being created for sessions that should not be tracked, and the granularity is too coarse to accurately represent short work sessions.

**Current behavior:**

1. **Direct Claude Code CLI sessions** (interactive `claude` sessions opened at the terminal) fire the `UserPromptSubmit` and heartbeat hooks (`scripts/calendar_prompt_hook.sh`, `scripts/calendar_hook.sh`), creating calendar events. These are not billable work units.
2. **Thread-scoped Telegram conversations** (ad-hoc sessions with no plan slug) also trigger calendar events. `CLAUDE_CODE_TASK_LIST_ID` is set to `thread-{chat_id}-{root_msg_id}` for these — not a real work slug.
3. **Worker-side heartbeats** (`agent/session_executor.py:943,1747`) pass `session.project_key` as the calendar event name even when a real `session.slug` (e.g., `auth-refactor`) is available. Planned Dev sessions log events under the project name instead of the task slug.
4. **Calendar blocks are 30 minutes** — too coarse. A 15-minute task becomes a 30-minute block.

**Desired outcome:**

- Calendar events are only created for **planned Dev sessions** — those with a real `AgentSession.slug`.
- Direct CLI sessions and thread-scoped conversations produce no calendar events.
- Worker-side heartbeats use the actual session slug as the event name.
- Minimum calendar block: **12 minutes**, extending in **6-minute increments**.

## Freshness Check

**Baseline commit:** `01a94beee40906ab3da579e5e67455fb5a2593a1`
**Issue filed at:** 2026-05-13T14:41:04Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/session_executor.py:884` — `task_list_id = session.slug` (tier-2) — still holds
- `agent/session_executor.py:943` — `_calendar_heartbeat(session.project_key, project=session.project_key)` — still holds
- `agent/session_executor.py:1747` — same call in `_heartbeat_loop` — still holds
- `agent/session_executor.py:348` — `CALENDAR_HEARTBEAT_INTERVAL = 25 * 60` — still holds
- `tools/valor_calendar.py:53` — `round_down_30` — still holds
- `tools/valor_calendar.py:59` — `round_up_30` — still holds
- `tools/valor_calendar.py:68` — `current_segment` — still holds

**Commits on main since issue was filed (touching referenced files):** None.

**Active plans in `docs/plans/` overlapping this area:** None. Most recent plans are `sdlc-1362.md`, `sdlc-1357.md` — unrelated SDLC work.

## Prior Art

- **PR #865**: Fix Google Workspace OAuth error handling — addressed auth token failures in `tools.google_workspace.auth`; irrelevant to session guards or block sizing.
- **PR #49**: Preserve existing calendar config mappings during `/update` — ensured `calendar_config.json` mappings survive machine updates; irrelevant.
- **PR #525**: Wire Claude Code hooks to subconscious memory system — added `UserPromptSubmit` and `Stop` hooks for memory extraction. Demonstrates the hook injection pattern we're augmenting.

No prior attempts to add session-type guards or change block granularity found.

## Research

No relevant external findings — all changes are internal to shell hook scripts and a local Python tool. No external library changes, API schema updates, or ecosystem patterns involved.

## Data Flow

**Current (broken) flow — direct CLI session:**

1. User opens `claude` at terminal → Claude Code session starts with no `CLAUDE_CODE_TASK_LIST_ID`
2. User types first prompt → `UserPromptSubmit` hook fires → `calendar_prompt_hook.sh` runs
3. Hook derives slug via Haiku, calls `valor-calendar --project ai slug` → event created ❌

**Current (broken) flow — thread-scoped session:**

1. Telegram message arrives → worker spawns Claude Code with `CLAUDE_CODE_TASK_LIST_ID=thread-123-456`
2. `UserPromptSubmit` hook fires → `calendar_prompt_hook.sh` runs
3. No guard on `CLAUDE_CODE_TASK_LIST_ID` → event created ❌

**Target flow — planned Dev session only:**

1. Telegram `/do-plan auth-refactor` → worker spawns Dev session with `CLAUDE_CODE_TASK_LIST_ID=auth-refactor`
2. `UserPromptSubmit` hook fires → `calendar_prompt_hook.sh` checks `CLAUDE_CODE_TASK_LIST_ID`
3. Value is `auth-refactor` (not empty, not `thread-*`) → proceed → event created ✅
4. Worker also fires heartbeat via `_calendar_heartbeat(session.slug, project=session.project_key)` ✅

**Block sizing flow (after fix):**

1. Session starts at 14:07 → `round_down_6(14:07)` = 14:06; event created 14:06–14:18 (12 min)
2. Heartbeat at 14:32 → `round_down_6(14:32)` = 14:30; `seg_end` = 14:42; event ends 14:18 < 14:42 → extend to 14:42
3. Session ends at 14:38 → final event: 14:06–14:42 (36 min of covered time, 31 min actual)

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work modifies local shell scripts and a local Python utility. No external API keys, new services, or environment variables required.

## Solution

### Key Elements

- **Hook session guard**: Both shell hooks exit early if `CLAUDE_CODE_TASK_LIST_ID` is absent or starts with `thread-`.
- **Worker heartbeat guard**: `session_executor.py` only fires calendar heartbeats when `session.slug` is set.
- **Worker slug fix**: Use `session.slug` as the event name argument; keep `session.project_key` for `--project` routing.
- **6-minute rounding**: Replace 30-minute boundary functions with 6-minute equivalents; minimum block = 12 minutes.

### Flow

Prompt submitted → guard checks `CLAUDE_CODE_TASK_LIST_ID` → slug-scoped value? → `valor-calendar --project PROJECT SLUG` → 12-min event created or extended in 6-min steps

### Technical Approach

**1. `scripts/calendar_prompt_hook.sh` and `scripts/calendar_hook.sh`**

Add the following block immediately after the `INPUT=$(cat)` / `SESSION_ID=...` reads, **before** the project allowlist check:

```bash
# Only track planned Dev sessions (tier-2 work with a real AgentSession.slug)
TASK_LIST_ID="${CLAUDE_CODE_TASK_LIST_ID:-}"
if [ -z "$TASK_LIST_ID" ] || echo "$TASK_LIST_ID" | grep -qE '^thread-'; then
    exit 0
fi
```

Logic:
- Empty `CLAUDE_CODE_TASK_LIST_ID` → direct CLI session → skip
- `thread-` prefix → tier-1 ephemeral session → skip
- Any other value (the slug itself, e.g., `auth-refactor`) → tier-2 planned Dev session → proceed

**2. `agent/session_executor.py` — lines 943 and 1747**

Guard both heartbeat calls with `if session.slug:`, and pass the slug as the event name:

```python
# Line ~943 (session start):
if session.slug:
    asyncio.create_task(
        _calendar_heartbeat(session.slug, project=session.project_key)
    )

# Line ~1747 (heartbeat loop):
if session.slug:
    asyncio.create_task(
        _calendar_heartbeat(session.slug, project=session.project_key)
    )
```

`CALENDAR_HEARTBEAT_INTERVAL` (25 min) stays unchanged — extensions cover the gap correctly with 6-min increments.

**3. `tools/valor_calendar.py` — rounding functions**

Replace:

```python
def round_down_30(dt: datetime) -> datetime:
    minute = (dt.minute // 30) * 30
    return dt.replace(minute=minute, second=0, microsecond=0)

def round_up_30(dt: datetime) -> datetime:
    if dt.minute == 0 and dt.second == 0:
        return dt.replace(second=0, microsecond=0)
    if dt.minute <= 30:
        return dt.replace(minute=30, second=0, microsecond=0)
    return dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

def current_segment(now: datetime) -> tuple[datetime, datetime]:
    start = round_down_30(now)
    end = start + timedelta(minutes=30)
    return start, end
```

With:

```python
_SEGMENT_MINUTES = 6
_MIN_BLOCK_MINUTES = 12

def round_down_6(dt: datetime) -> datetime:
    minute = (dt.minute // _SEGMENT_MINUTES) * _SEGMENT_MINUTES
    return dt.replace(minute=minute, second=0, microsecond=0)

def round_up_6(dt: datetime) -> datetime:
    if dt.second == 0 and dt.minute % _SEGMENT_MINUTES == 0:
        return dt.replace(second=0, microsecond=0)
    minute = ((dt.minute // _SEGMENT_MINUTES) + 1) * _SEGMENT_MINUTES
    if minute >= 60:
        return dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return dt.replace(minute=minute, second=0, microsecond=0)

def current_segment(now: datetime) -> tuple[datetime, datetime]:
    """Return segment boundaries: start rounded down to 6-min, end = start + 12 min."""
    start = round_down_6(now)
    end = start + timedelta(minutes=_MIN_BLOCK_MINUTES)
    return start, end
```

No changes needed to `process_calendar_event` — it already works generically against `seg_end`.

Update module docstring to reflect new block sizes.

**4. New unit tests — `tests/unit/test_valor_calendar.py` (create)**

Test `round_down_6`, `round_up_6`, and `current_segment` with representative inputs including boundary cases (hour rollover, minute=0, minute=6, minute=59).

## Failure Path Test Strategy

### Exception Handling Coverage

Both hooks use `set +e` and `|| { ... true; }` patterns — failures are already swallowed and logged. No new exception handlers added.

In `session_executor.py`, `_calendar_heartbeat` already wraps everything in `try/except Exception as e: logger.warning(...)`. The guard `if session.slug:` is additive and cannot raise.

### Empty/Invalid Input Handling

- `round_down_6` / `round_up_6`: only called with `datetime` objects from `utc_now()` — always valid. Unit tests will cover boundary minutes (0, 6, 30, 59).
- `CLAUDE_CODE_TASK_LIST_ID` guard: handles empty string and unset (`:-` expansion) — both map to skip.

### Error State Rendering

No user-visible output surfaces change. Calendar heartbeat failures are already logged to `logs/hooks.log`.

## Test Impact

No existing tests affected — the two test files that reference `tools.valor_calendar` only import `_handle_check` and `_handle_reauth` (auth flag handlers), neither of which calls the rounding functions being replaced. The `test_config_consolidation.py` legacy-path check is a string scan for `claude_code` and is unaffected by renaming `round_down_30` → `round_down_6`. All changes are additive (new rounding functions, new guard logic) with no modification to existing callable interfaces.

New test file to create: `tests/unit/test_valor_calendar_rounding.py` — covers `round_down_6`, `round_up_6`, and `current_segment` with boundary cases.

## Rabbit Holes

- **Reducing `CALENDAR_HEARTBEAT_INTERVAL`**: 25 min works fine with 6-min extensions — extensions will bridge the gap. Changing the interval is unnecessary churn.
- **Detecting session type from AgentSession Redis lookup**: Checking Redis inside the shell hooks would be fragile and slow. The `CLAUDE_CODE_TASK_LIST_ID` pattern check is instant and reliable.
- **Retroactively fixing existing calendar events**: Past events under `project_key` names instead of slugs are already created — no migration needed, just fix going forward.

## Risks

### Risk 1: Slug-format `CLAUDE_CODE_TASK_LIST_ID` for non-Dev sessions

**Impact:** If a future session type sets a non-`thread-` task list ID that isn't a real work slug, it would get calendar events.
**Mitigation:** The issue description defines the contract clearly — slug-based IDs are only set for tier-2 Dev sessions. If that invariant ever breaks, it's a bug in the session spawning code, not here.

### Risk 2: `session.slug` not yet set at line 943

**Impact:** If the slug is populated after line 943 runs, the start-of-session heartbeat would be skipped.
**Mitigation:** Line 884 sets `task_list_id = session.slug` from the already-populated `session` object before line 943. The slug is available at that point.

## Race Conditions

No race conditions identified — the guard checks are reads of pre-populated state (`CLAUDE_CODE_TASK_LIST_ID` is set before the hook process starts; `session.slug` is set before line 943 executes). No shared mutable state is introduced.

## No-Gos (Out of Scope)

- [EXTERNAL] Wiring the Cyndra calendar ID into `calendar_config.json` — requires confirming the correct project key mapping and running the config update on the live machine. Tracked separately via saved memory.

## Update System

No update system changes required — all changes are to files already present on every machine. The shell hooks are global hooks registered in `~/.claude/settings.json` and executed from the repo; they pick up changes immediately on next `git pull`.

## Agent Integration

No agent integration required — `tools/valor_calendar.py` is already exposed as the `valor-calendar` CLI entry point in `pyproject.toml`. No new entry points, MCP tools, or bridge imports needed.

## Documentation

- [ ] Update `tools/valor_calendar.py` module docstring to reflect 6-min/12-min block sizes (inline, not a separate doc file)
- [ ] No separate feature doc needed — the calendar integration is documented by the code and comments

## Success Criteria

- [ ] Direct Claude Code CLI sessions (no `CLAUDE_CODE_TASK_LIST_ID`) produce no calendar events
- [ ] Thread-scoped sessions (`CLAUDE_CODE_TASK_LIST_ID=thread-*`) produce no calendar events
- [ ] Planned Dev sessions (`CLAUDE_CODE_TASK_LIST_ID=auth-refactor` etc.) still produce calendar events named with the slug
- [ ] Worker-side heartbeat events use `session.slug` as the event name (not `session.project_key`)
- [ ] Worker-side heartbeats are skipped entirely when `session.slug` is None
- [ ] `round_down_30` and `round_up_30` are removed from `tools/valor_calendar.py`
- [ ] New minimum block is 12 minutes; events extend in 6-minute increments
- [ ] `pytest tests/unit/test_valor_calendar_rounding.py` passes with new rounding tests
- [ ] `pytest tests/` passes with no regressions

## Team Orchestration

### Team Members

- **Builder (hooks-and-executor)**
  - Name: hooks-builder
  - Role: Implement session guard in both shell hooks and fix slug usage in session_executor.py
  - Agent Type: builder
  - Resume: true

- **Builder (calendar-rounding)**
  - Name: rounding-builder
  - Role: Replace 30-min rounding with 6-min/12-min in tools/valor_calendar.py and write unit tests
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: final-validator
  - Role: Verify all success criteria, run pytest, confirm no calendar noise from CLI sessions
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement session guard in shell hooks
- **Task ID**: build-hooks-guard
- **Depends On**: none
- **Validates**: `bash -n scripts/calendar_prompt_hook.sh && bash -n scripts/calendar_hook.sh`
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: true
- In `scripts/calendar_prompt_hook.sh`, add the `CLAUDE_CODE_TASK_LIST_ID` guard block immediately after the `SESSION_ID=` line (before the slash-command skip check)
- In `scripts/calendar_hook.sh`, add the same guard block immediately after the `SESSION_ID=` line (before the excluded projects check)
- Guard exits 0 (silent skip) when `CLAUDE_CODE_TASK_LIST_ID` is empty or starts with `thread-`

### 2. Fix worker heartbeat slug and guard in session_executor.py
- **Task ID**: build-executor-fix
- **Depends On**: none
- **Validates**: `python -m ruff check agent/session_executor.py`
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: true
- At `session_executor.py:943`: wrap in `if session.slug:`, change first arg from `session.project_key` to `session.slug`
- At `session_executor.py:1747`: same changes
- Update the `CALENDAR_HEARTBEAT_INTERVAL` comment to remove the "fits within 30-min segments" note

### 3. Replace 30-min rounding with 6-min/12-min in valor_calendar.py
- **Task ID**: build-rounding
- **Depends On**: none
- **Validates**: `tests/unit/test_valor_calendar_rounding.py` (create)
- **Assigned To**: rounding-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `round_down_30`, `round_up_30`, `current_segment` with `round_down_6`, `round_up_6`, updated `current_segment` using `_SEGMENT_MINUTES=6`, `_MIN_BLOCK_MINUTES=12`
- Update module docstring to say "12-minute minimum blocks, 6-minute increments"
- Create `tests/unit/test_valor_calendar_rounding.py` with tests for: round_down at minute=0, 6, 7, 30, 59; round_up at same; current_segment start/end; hour-boundary rollover

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: build-hooks-guard, build-executor-fix, build-rounding
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -q` — confirm no regressions, new rounding tests pass
- Run `python -m ruff check . && python -m ruff format --check .`
- Verify `round_down_30` and `round_up_30` are gone: `grep -n "round_down_30\|round_up_30" tools/valor_calendar.py` → must return nothing
- Verify guard is in both hooks: `grep -n "CLAUDE_CODE_TASK_LIST_ID" scripts/calendar_prompt_hook.sh scripts/calendar_hook.sh`
- Verify executor uses `session.slug`: `grep -n "session.slug\|_calendar_heartbeat" agent/session_executor.py | head -10`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Old rounding gone | `grep -n "round_down_30\|round_up_30" tools/valor_calendar.py` | exit code 1 |
| Hook guard present | `grep -c "CLAUDE_CODE_TASK_LIST_ID" scripts/calendar_prompt_hook.sh` | output contains 1 |
| Executor uses slug | `grep -c "session\.slug" agent/session_executor.py` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| — | — | — | — | — |

---

## Open Questions

None — all assumptions verified against the codebase. Ready to build.
