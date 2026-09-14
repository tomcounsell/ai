---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-07
tracking: https://github.com/tomcounsell/ai/issues/809
last_comment_id:
---

# Fix Local Session Type Registration

## Problem

When a PM AgentSession spawns a Teammate subprocess (e.g., for answering a DM), the resulting
`local-*` AgentSession record shows `session_type: Developer` instead of `Teammate`. The
dashboard misrepresents these sessions, and any logic that gates behavior on `session_type`
(permission checks, routing) gets the wrong answer.

**Current behavior:**
`user_prompt_submit.py` calls `AgentSession.create_local()` with no `session_type` argument.
The factory hardcodes `session_type=SESSION_TYPE_DEV` regardless of the actual persona. PM and
Teammate sessions both appear as `Developer` on the dashboard.

**Desired outcome:**
The `local-*` record reflects the actual session type — `Teammate`, `PM`, or `Dev` — matching
the `SESSION_TYPE` env var already injected by `sdk_client.py` when spawning subprocesses.

## Prior Art

- **Issue #634**: Generalize AgentSession parent-child model and add role field — closed 2026-04-03.
  Added the `role` field and `session_type` discriminator. Did not address the hook registration
  gap — `user_prompt_submit.py` was not updated to pass `session_type` through.

## Data Flow

1. **PM session** calls `sdk_client.py` to spawn a Teammate subprocess
2. `sdk_client.py` (line 924) injects `SESSION_TYPE=teammate` into the subprocess environment
3. Claude Code starts; `.claude/hooks/user_prompt_submit.py` fires on the first prompt
4. Hook calls `AgentSession.create_local(...)` — **no `session_type` arg passed**
5. `create_local()` (line 955) hardcodes `session_type=SESSION_TYPE_DEV`
6. Redis record for the session shows `session_type: dev` — wrong for Teammate/PM sessions
7. Dashboard reads Redis and displays incorrect type

**Fix:** In step 4, read `os.environ.get("SESSION_TYPE")` and pass it to `create_local()`.

## Architectural Impact

- **Interface changes**: `create_local()` signature will be updated to accept `session_type` as an explicit parameter with a default value
- **New dependencies**: None — but `import os` must be added to the hook (it is NOT currently imported)
- **Coupling**: No change in coupling; hook already depends on `AgentSession`
- **Reversibility**: Trivial — revert two lines in the hook and the signature default in `create_local()`

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **`user_prompt_submit.py`**: Add `import os`, read `SESSION_TYPE` env var, and pass it to `create_local()` when present
- **`create_local()` signature**: Update to accept `session_type` as an explicit keyword parameter with default `SESSION_TYPE_DEV`

### Flow

subprocess spawned with `SESSION_TYPE=teammate` → hook fires → reads `os.environ.get("SESSION_TYPE")` → passes `session_type="teammate"` to `create_local()` → Redis record shows `Teammate`

### Technical Approach

**Step 1 — Update `models/agent_session.py` `create_local()` signature** (around line 955):

The current signature hardcodes `session_type=SESSION_TYPE_DEV` inside the constructor call. Change the method to accept `session_type` as an explicit parameter:

```python
@classmethod
def create_local(cls, session_id, ..., session_type: str = SESSION_TYPE_DEV, **kwargs):
    ...
    return cls(
        ...,
        session_type=session_type,
        **kwargs,
    )
```

This avoids the `TypeError: duplicate keyword argument` that would occur if `session_type` were passed via `**kwargs` while also being present as a hardcoded positional argument in the constructor call.

**Step 2 — Update `.claude/hooks/user_prompt_submit.py`**:

Add `import os` at the top of the file (it is NOT currently imported), then before the `create_local()` call (around line 94):

```python
import os  # add at top of file

session_type_override = os.environ.get("SESSION_TYPE")  # "teammate", "pm", "dev", or None

agent_session = AgentSession.create_local(
    session_id=local_session_id,
    project_key=project_key,
    working_dir=cwd,
    status="running",
    message_text=prompt[:500] if prompt else "",
    **({"session_type": session_type_override} if session_type_override else {}),
)
```

## Failure Path Test Strategy

### Exception Handling Coverage

- No exception handlers are added or modified by this change. The existing hook error handling
  remains unchanged.

### Empty/Invalid Input Handling

- `os.environ.get("SESSION_TYPE")` returns `None` when the var is absent — the conditional
  `**({"session_type": ...} if session_type_override else {})` safely omits the kwarg, preserving
  the existing default.
- Invalid values (e.g., `SESSION_TYPE=garbage`) would be passed to `create_local()` which would
  store them as-is; this is acceptable since `sdk_client.py` only sets valid values.

### Error State Rendering

- No user-visible output change. This affects only Redis record contents and dashboard display.

## Test Impact

- [x] `tests/unit/test_dev_session_registration.py::TestCreateLocal::test_creates_session_with_correct_fields` — UPDATE: assert `session_type` defaults to `"dev"` when `SESSION_TYPE` env var is absent (behaviour unchanged, but add explicit env-var-absent case)
- [x] `tests/unit/test_dev_session_registration.py::TestCreateLocal::test_accepts_kwargs` — UPDATE: extend to cover `session_type` passed as explicit kwarg to `create_local()`

New tests to add:
- [x] `tests/unit/test_dev_session_registration.py::TestCreateLocal::test_session_type_from_env_var` — ADD: when `SESSION_TYPE=teammate` env var is set, `create_local()` stores `session_type="teammate"`
- [x] `tests/unit/test_hook_user_prompt_submit.py::TestSessionTypeHook::test_hook_reads_session_type_env_var` — ADD: patch `os.environ` with `SESSION_TYPE=teammate`, call the hook's `main()`, assert `create_local()` was called with `session_type="teammate"`. This is the critical path test for the hook layer, not just the model layer.

## Rabbit Holes

- **Validating SESSION_TYPE values**: Don't add an allowlist check in the hook — `sdk_client.py`
  is the sole caller and already ensures valid values. Validation belongs in the model, not the hook.
- **Batching with #808** (`parent_agent_session_id`): Issue #808 is a related missing env-var read.
  Keep the fixes separate — each is one line and their test coverage is independent.
- **Backfilling existing sessions**: No migration needed; only future sessions are affected.

## Risks

### Risk 1: Unrecognized SESSION_TYPE value stored in Redis
**Impact:** Dashboard shows an unrecognized type string if someone manually sets `SESSION_TYPE` to an unexpected value.
**Mitigation:** `sdk_client.py` is the only writer and uses constants (`SESSION_TYPE_PM`, `SESSION_TYPE_TEAMMATE`, `SESSION_TYPE_DEV`). Risk is theoretical; no mitigation needed.

## Race Conditions

No race conditions identified — the hook runs synchronously in the subprocess before any concurrent
work begins, and `SESSION_TYPE` is set in the environment before the subprocess starts.

## No-Gos (Out of Scope)

- Fixing `parent_agent_session_id` null in hook (tracked separately as #808)
- Adding `SESSION_TYPE` validation in `create_local()` or the hook
- Backfilling existing `local-*` session records
- Fixing `create_child()` (line 1005 in `models/agent_session.py`) — it has the same hardcoded `SESSION_TYPE_DEV` issue as `create_local()`, but `create_child()` is called by a different code path (worker spawning child sessions) and needs its own analysis. Track as a follow-up issue after this fix ships.

## Update System

No update system changes required — this is a hook-level fix with no new dependencies, config
files, or deployment steps. The `.claude/hooks/` directory is part of the repo and propagates
via `git pull`.

## Agent Integration

No agent integration required — this is a hook-internal change. The fix only affects how local
Claude Code sessions register themselves in Redis; it does not expose new tools or MCP endpoints.

## Documentation

- [x] Update `docs/features/subconscious-memory.md` to note that `session_type` in the `local-*`
  record now reflects the spawning process's `SESSION_TYPE` env var (not always `dev`)
- [x] Add a brief note in `docs/features/pm-dev-session-architecture.md` explaining that
  `user_prompt_submit.py` reads `SESSION_TYPE` to register the correct persona on session start

## Success Criteria

- [x] Teammate sessions spawned by a PM show `session_type: Teammate` on the dashboard
- [x] PM sessions spawned by a worker show `session_type: PM` on the dashboard
- [x] Dev sessions (no `SESSION_TYPE` env var) continue to default to `Developer`
- [x] `create_local()` accepts `session_type` as an explicit keyword parameter with default `SESSION_TYPE_DEV`
- [x] Unit test covers `create_local()` with `session_type` kwarg
- [x] Hook-layer test verifies that `user_prompt_submit.py` reads `SESSION_TYPE` env var and passes it through to `create_local()`
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (hook-fix)**
  - Name: hook-builder
  - Role: Apply one-line fix to `user_prompt_submit.py` and add unit test
  - Agent Type: builder
  - Resume: true

- **Validator (hook-fix)**
  - Name: hook-validator
  - Role: Verify fix, run tests, confirm dashboard behavior
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update feature docs for session type registration
  - Agent Type: documentarian
  - Resume: true

### Step by Step Tasks

### 1. Apply Model + Hook Fix and Add Tests
- **Task ID**: build-hook-fix
- **Depends On**: none
- **Validates**: tests/unit/test_dev_session_registration.py, tests/unit/test_hook_user_prompt_submit.py
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: true
- In `models/agent_session.py`, update `create_local()` to accept `session_type: str = SESSION_TYPE_DEV` as an explicit parameter instead of hardcoding it in the constructor call
- In `.claude/hooks/user_prompt_submit.py`, add `import os` at the top, then read `os.environ.get("SESSION_TYPE")` and pass it to `create_local()` when present
- Add `test_session_type_from_env_var` unit test to `tests/unit/test_dev_session_registration.py`
- Add `test_hook_reads_session_type_env_var` test to `tests/unit/test_hook_user_prompt_submit.py` (patch `os.environ`, call hook's `main()`, assert `create_local()` called with correct `session_type`)
- Update existing `test_creates_session_with_correct_fields` to explicitly assert the no-env-var default

### 2. Validate Fix
- **Task ID**: validate-hook-fix
- **Depends On**: build-hook-fix
- **Assigned To**: hook-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_dev_session_registration.py -v`
- Verify all three criteria (Teammate, PM, Dev-default) pass
- Report pass/fail status

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-hook-fix
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/subconscious-memory.md` and `docs/features/pm-dev-session-architecture.md`

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: hook-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full unit test suite
- Verify all success criteria met including documentation
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_dev_session_registration.py -v` | exit code 0 |
| Full tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Format clean | `python -m black --check .claude/hooks/user_prompt_submit.py` | exit code 0 |
| Env var read present | `grep -n "SESSION_TYPE" .claude/hooks/user_prompt_submit.py` | output contains SESSION_TYPE |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None — solution is straightforward and fully specified.
