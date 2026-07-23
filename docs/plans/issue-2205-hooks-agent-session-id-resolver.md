---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-23
tracking: https://github.com/tomcounsell/ai/issues/2205
last_comment_id:
---

# Hooks AGENT_SESSION_ID resolver misses bridge sessions

## Problem

The SDK/headless hook surface resolves the in-flight `AgentSession` from the
wrong identifier namespace, so its writes silently no-op for every bridge PM
session (the largest live session population).

`AGENT_SESSION_ID` is set to `session.agent_session_id` — the per-run Popoto
AutoKey hex (`id`). Two hook modules read that hex and then look it up with
`AgentSession.query.filter(session_id=…)`, which filters on the *other*
identifier (`tg_valor_…`, `sdlc-local-…`). For a bridge PM session
`agent_session_id != session_id`, so the filter returns zero rows and the
lookup silently misses.

**Current behavior:**
- `agent/hooks/pre_tool_use.py::_resolve_sdk_session()` (env read `:391`, filter
  `:396`) returns `None` for bridge sessions → the per-tool budget backstop
  (`_enforce_tool_budget_sdk`) treats every bridge session as "no session" and
  never enforces a budget for it.
- `agent/hooks/liveness_writers.py` (filter at `:86`, `:193`, `:229`; env read
  at `:127`, `:175`, `:213`) returns `False` for bridge sessions → the
  `current_tool_name` / `current_tool_timeout_s` / `last_turn_at` /
  `recent_thinking_excerpt` liveness stamps are never written, degrading the
  dashboard's in-flight visibility and the per-tool timeout sub-loop in
  `agent/session_health.py` for that population.

Both consumers are best-effort and fail silently, so this has been invisible.
It is pre-existing status-quo behavior explicitly deferred out of #2190 (see
`docs/plans/completed/issue-2190-agent-session-id-resolver-mismatch.md`,
No-Gos `[SEPARATE-SLUG #2205]`).

**Desired outcome:**
The hooks resolve the live `AgentSession` for bridge sessions. Budget backstop
and liveness stamps work for the full session population, not just the accidental
cases where `agent_session_id == session_id`.

## Freshness Check

**Baseline commit:** 3c0fc7ee1
**Issue filed at:** 2026-07-22T07:04:55Z
**Disposition:** Minor drift (a referenced prerequisite PR merged after filing — it *enables* this fix)

**File:line references re-verified:**
- `agent/hooks/pre_tool_use.py:391/396` — env read + `filter(session_id=…)` — still holds (`_resolve_sdk_session` at `:380`).
- `agent/hooks/liveness_writers.py:86,193,229` (filters) and `:127,175,213` (env reads) — still hold across `record_tool_boundary`'s inner persist, `record_turn_boundary`, `record_thinking_excerpt`.
- `agent/session_executor.py:1954` — `VALOR_SESSION_ID = session.session_id` now injected into `_harness_env` (alongside `AGENT_SESSION_ID = session.agent_session_id` at `:1940`).
- `models/agent_session.py:1025` — `get_by_id(x)` resolves the AutoKey via `filter(id=x)`.
- `tools/valor_session.py:656` — `_find_session` session_id-first / `get_by_id`-fallback precedent (with a bounded class-set retry).

**Cited sibling issues/PRs re-checked:**
- #2190 — closed; plan migrated to `docs/plans/completed/`. Its No-Gos filed this issue as `[SEPARATE-SLUG #2205]`.
- PR #2206 (commit `ed47cccb3`) — merged **after** this issue was filed. It injects `VALOR_SESSION_ID` in `session_executor.py`. This is the prerequisite that makes direction (1) viable: the env var the hooks need is now present.

**Commits on main since issue was filed (touching referenced files):**
- `ed47cccb3` Fix WS-F AGENT_SESSION_ID resolver identifier-type mismatch (#2190) (#2206) — **enables this fix** (adds `VALOR_SESSION_ID`); does not touch the two hook modules.

**Active plans in `docs/plans/` overlapping this area:** none (the #2190 plan is completed/migrated).

**Notes:** No premise drifted. The one relevant commit is the prerequisite the
issue already anticipated ("now that `agent/session_executor.py` injects it").

## Prior Art

- **Issue #2190 / PR #2206 (`ed47cccb3`)**: Fixed the *same identifier-type
  mismatch* in `tools/sdlc_session_ensure.py`'s env short-circuit by injecting
  `VALOR_SESSION_ID` and reading it first (Seam B2). Succeeded. This issue is
  its explicitly-deferred sibling — the hooks were left out of #2190's minimal
  scope on purpose.
- **`tools/valor_session.py::_find_session` (`:656`)**: Working precedent for
  the session_id-first / `get_by_id`-fallback resolution shape. Not reused
  directly (see Rabbit Holes) but its logic is the template.

## Research

No relevant external findings — this is purely internal (Popoto/env resolution).
No WebSearch performed.

## Data Flow

1. **Session spawn**: `agent/session_executor.py` builds `_harness_env` with
   `AGENT_SESSION_ID = session.agent_session_id` (hex) **and**
   `VALOR_SESSION_ID = session.session_id` (`:1940`, `:1954`), passed to the
   `claude -p` harness subprocess.
2. **Tool call (budget)**: harness fires the SDK PreToolUse hook →
   `_enforce_tool_budget_sdk()` → `_resolve_sdk_session()` reads
   `AGENT_SESSION_ID` and `filter(session_id=<hex>)` → **0 rows for bridge** →
   budget backstop no-ops.
3. **Tool / turn / thinking boundary (liveness)**: SDK client / hook calls
   `record_tool_boundary` / `record_turn_boundary` / `record_thinking_excerpt`
   → each reads `AGENT_SESSION_ID` and `filter(session_id=<hex>)` → **0 rows for
   bridge** → stamp write silently skipped.
4. **Output (today)**: dashboard shows stale/empty `current_tool_name`,
   `last_turn_at`, etc. for bridge sessions; `session_health.py` per-tool
   timeout loop has nothing to read.
5. **Output (after fix)**: both hooks resolve via `VALOR_SESSION_ID`
   (`filter(session_id=…)`) → 1 row → stamps and budget attribution land.

## Appetite

**Size:** Small

**Team:** Solo dev, validator

**Interactions:**
- PM check-ins: 0 (scope fully determined by the sibling plan's recorded decisions)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable (Popoto backend) | `python -c "from models.agent_session import AgentSession; list(AgentSession.query.filter(session_id='__none__'))"` | Resolver + tests need Popoto query/get_by_id |

## Solution

### Key Elements

- **Shared hooks resolver**: one helper that reads `VALOR_SESSION_ID` first
  (resolve via `filter(session_id=…)`), falling back to `AGENT_SESSION_ID`
  resolved via `AgentSession.get_by_id()` (the AutoKey `id`). Returns the
  `AgentSession` or `None`; lets Popoto/Redis exceptions propagate so each
  caller keeps its own error posture.
- **`pre_tool_use._resolve_sdk_session`**: delegates to the shared resolver.
  Keeps its RAISES-on-infra-error contract (the loud "backstop BLIND" path in
  `_enforce_tool_budget_sdk` catches separately).
- **`liveness_writers` (3 call sites)**: each reads the raw env identifier for
  its cooldown bucket key, then delegates resolution to the shared helper. Keeps
  fail-silent (`try/except` → DEBUG → return `False`).

### Flow

Bridge session runs a tool → SDK hook fires → resolver reads `VALOR_SESSION_ID`
→ `filter(session_id=…)` → 1 row → budget attributed / liveness stamped → visible
on dashboard.

### Technical Approach

- **New module `agent/hooks/session_resolver.py`** exposing
  `resolve_inflight_session() -> AgentSession | None`:
  1. `sid = os.environ.get("VALOR_SESSION_ID")`; if truthy, return the first
     `AgentSession.query.filter(session_id=sid)` match (or `None`).
  2. Else `aid = os.environ.get("AGENT_SESSION_ID")`; if truthy, return
     `AgentSession.get_by_id(aid)`.
  3. Else `None`. Do **not** swallow exceptions — resolution/infra errors
     propagate to the caller.
- **`_resolve_sdk_session()`** becomes a thin wrapper over
  `resolve_inflight_session()` (preserving its docstring contract: `None` for a
  genuine no-session, RAISES on infra error).
- **`liveness_writers`**: replace the three `os.environ.get("AGENT_SESSION_ID")`
  + `filter(session_id=…)` blocks with a call to `resolve_inflight_session()`.
  The cooldown bucket key must stay stable per session: key it on the raw env
  value actually used (`VALOR_SESSION_ID` when present, else `AGENT_SESSION_ID`)
  so a session keeps ONE cooldown bucket regardless of which identifier resolved
  it. The `record_tool_boundary` inner persist (`:78`) currently takes a
  `session_id` param and re-queries — refactor it to accept the resolved
  `AgentSession` (or call the shared resolver) so it does not re-issue the wrong
  filter.
- **Why `get_by_id` for `AGENT_SESSION_ID`, not a second `filter(session_id)`
  fallback**: `AGENT_SESSION_ID` holds the AutoKey hex; `get_by_id` is the
  correct primary-key lookup. Adding a `filter(session_id=<hex>)` fallback would
  be defensive scar tissue that can never match. The clean two-path resolver
  (session_id-first, id-fallback) is the whole fix.
- **No retry**: unlike `_find_session`, the resolver does not add a bounded
  class-set retry. These are best-effort hooks and the current code never
  retried; adding it now would be unwarranted complexity (see Rabbit Holes).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `liveness_writers` keeps its `except Exception → logger.debug → return
  False` blocks; add/keep a test asserting a Popoto failure returns `False`
  without raising.
- [ ] `_enforce_tool_budget_sdk` keeps its infra-error split (loud WARNING +
  `record_resolution_error`, fail-open `None`); the resolver propagating an
  exception must still route through that path — assert it.

### Empty/Invalid Input Handling
- [ ] Resolver with both env vars unset → returns `None` (genuine no-session).
- [ ] Resolver with `VALOR_SESSION_ID` set to a non-matching id → `None`.
- [ ] Resolver with `AGENT_SESSION_ID` set to a non-existent hex → `get_by_id`
  returns `None`.

### Error State Rendering
- [ ] No user-visible rendering in scope; the observable output is the persisted
  `AgentSession` field / budget decision, asserted directly in tests.

## Test Impact

The existing hook tests set `AGENT_SESSION_ID = s.session_id` (e.g.
`tests/unit/test_pre_tool_use_liveness_writes.py:61`). That value is a
`session_id`, not the production `agent_session_id` hex — which is *why the bug
was never caught*: the tests fed the resolver an identifier that happens to match
`filter(session_id=…)`. These fixtures must be updated to production-accurate env
values, and new cases must cover the real bridge shape.

- [ ] `tests/unit/test_pre_tool_use_liveness_writes.py` (fixture `:51-61` + cases
  using it) — UPDATE: set `VALOR_SESSION_ID = s.session_id` and
  `AGENT_SESSION_ID = s.agent_session_id` (the hex) so the fixture reflects
  production; add a case asserting resolution succeeds when only
  `AGENT_SESSION_ID` (hex) is set (get_by_id path), and a bridge-shape case where
  `agent_session_id != session_id`.
- [ ] `tests/unit/test_liveness_writers_turn_boundary.py` — UPDATE: same env-var
  correction for its fixture; add the bridge-shape assertion.
- [ ] `tests/unit/test_pre_tool_use_start_stage.py` — UPDATE if it sets
  `AGENT_SESSION_ID = session_id` for `_resolve_sdk_session`; keep the
  no-env-var → no-op case.
- [ ] `test_hook_silently_no_ops_without_agent_session_id`
  (`test_pre_tool_use_liveness_writes.py:152`) — UPDATE: also unset
  `VALOR_SESSION_ID` so "no env → no-op" still holds under the new resolver.
- [ ] `test_cli_hook_writes_current_tool_name_and_datetime`
  (`:215`) — VERIFY unaffected: this exercises the **CLI** hook
  (`.claude/hooks/pre_tool_use.py`) via the on-disk sidecar, a separate
  resolution surface that is out of scope. Confirm it still passes untouched.

New coverage to add (in the existing files):
- [ ] `agent/hooks/session_resolver.py` — new unit test covering all four
  resolver branches (VALOR match, VALOR miss, AGENT get_by_id hit, both unset).

## Rabbit Holes

- **Refactoring the shared `_find_session` resolver in `tools/valor_session.py`
  to serve the hooks.** That is Seam A, explicitly rejected in #2190 for higher
  blast radius (it changes resolution semantics for recovery/heal callers and
  couples hooks to a CLI module). Keep the hooks resolver local.
- **Adding a bounded class-set retry** like `_find_session`. These are
  best-effort fail-silent hooks; the status quo never retried. Do not import
  retry complexity into the hot path.
- **Touching the CLI hook / sidecar resolution** (`.claude/hooks/pre_tool_use.py`,
  `data/sessions/<id>` sidecar). Different surface, already resolves the hex
  correctly, out of scope.
- **Backfilling stamps for already-running sessions.** The writers already
  document "no backfill" — next boundary fires and the field populates. Leave it.

## Risks

### Risk 1: Cooldown bucket key changes split a session's cooldown
**Impact:** If the cooldown key silently switches from `AGENT_SESSION_ID` to
`VALOR_SESSION_ID` mid-refactor, a session could double-write during the window.
**Mitigation:** Key the cooldown on the raw env value actually used for
resolution (VALOR first, else AGENT), chosen once per call. Within one running
harness the same env var is always present, so the bucket is stable. Covered by
keeping the existing cooldown tests green.

### Risk 2: Existing tests were green on the wrong identifier
**Impact:** Updating fixtures to production-accurate env values could mask a
regression if done carelessly (e.g. setting both to `session_id`).
**Mitigation:** Fixtures must set `AGENT_SESSION_ID = s.agent_session_id` (hex)
and `VALOR_SESSION_ID = s.session_id` distinctly, and at least one case must
assert the bridge shape where the two differ. This is the anti-criterion that
proves the fix.

## Race Conditions

No new race conditions identified. The resolver adds no new query path beyond a
single `filter(session_id=…)` or `get_by_id()` per hook call — the same
single-shot lookups the code does today, just against the correct identifier.
`get_by_id` is a direct primary-key lookup (`filter(id=…)`) and does not touch
the class-set membership index, so it carries none of the class-set-lag concern
that motivated `_find_session`'s retry.

## No-Gos (Out of Scope)

- `[SEPARATE-SLUG #2190]` The `session_executor.py` `VALOR_SESSION_ID` injection
  and `sdlc_session_ensure.py` env short-circuit — already shipped in PR #2206;
  this plan consumes that env var, it does not re-touch it.
- Nothing else deferred — the CLI-hook/sidecar surface is a genuinely different
  resolution path (correct already) and is not part of this bug.

## Update System

No update system changes required — this is a purely internal resolver fix. No
new dependencies, config, or propagation steps.

## Agent Integration

No agent integration required — this is a bridge/worker-internal hook change. No
new CLI entry point, MCP surface, or bridge import. The hooks already run inside
the harness subprocess; the fix only corrects which identifier they resolve.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/subconscious-memory.md` or the relevant liveness/
  session-health feature doc only if it documents the resolver identifier; a
  short note that hooks resolve via `VALOR_SESSION_ID` first, `AGENT_SESSION_ID`
  (`get_by_id`) fallback.
- [ ] If `docs/features/session-lifecycle.md` (or the dashboard-liveness doc)
  describes why bridge sessions showed no in-flight stamps, correct it.

### Inline Documentation
- [ ] Docstring on `resolve_inflight_session()` stating the two-identifier
  contract and the exception-propagation posture.
- [ ] Update `_resolve_sdk_session()` and the `liveness_writers` module docstring
  (`:1-24`) which currently say resolution is via `AGENT_SESSION_ID` only.

[No external docs site.]

## Success Criteria

- [ ] `agent/hooks/session_resolver.py` exists with `resolve_inflight_session()`
  reading `VALOR_SESSION_ID` first, `AGENT_SESSION_ID` via `get_by_id` second.
- [ ] `_resolve_sdk_session()` and all three `liveness_writers` call sites use it.
- [ ] A unit test with the bridge shape (`agent_session_id != session_id`,
  `VALOR_SESSION_ID` set) proves the budget resolver and each liveness writer now
  resolve the session and persist their field.
- [ ] `grep -n 'filter(session_id' agent/hooks/liveness_writers.py agent/hooks/pre_tool_use.py`
  returns no direct `AGENT_SESSION_ID`-fed filter (resolution goes through the
  helper).
- [ ] Existing hook tests updated to production-accurate env vars and passing.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (hooks-resolver)**
  - Name: hooks-resolver-builder
  - Role: Create `session_resolver.py`, rewire both hook modules, update/extend tests
  - Agent Type: builder
  - Domain: async/data (Popoto/env resolution)
  - Resume: true

- **Validator (hooks-resolver)**
  - Name: hooks-resolver-validator
  - Role: Verify resolver branches, bridge-shape resolution, fail-silent posture, cooldown stability
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build shared resolver + rewire hooks
- **Task ID**: build-resolver
- **Depends On**: none
- **Validates**: tests/unit/test_pre_tool_use_liveness_writes.py, tests/unit/test_liveness_writers_turn_boundary.py, tests/unit/test_pre_tool_use_start_stage.py, tests/unit/test_session_resolver.py (create)
- **Assigned To**: hooks-resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/hooks/session_resolver.py::resolve_inflight_session()` (VALOR_SESSION_ID → filter(session_id); else AGENT_SESSION_ID → get_by_id; else None; exceptions propagate).
- Rewrite `agent/hooks/pre_tool_use.py::_resolve_sdk_session()` to delegate to it (preserve RAISES-on-infra contract).
- Rewrite the three `agent/hooks/liveness_writers.py` resolution blocks to delegate, keying cooldown on the raw env value used; refactor the `record_tool_boundary` inner persist to take the resolved session.
- Update module/function docstrings.

### 2. Update + extend tests
- **Task ID**: build-tests
- **Depends On**: build-resolver
- **Assigned To**: hooks-resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- Update fixtures to set `AGENT_SESSION_ID = agent_session_id` (hex) and `VALOR_SESSION_ID = session_id` distinctly.
- Add a bridge-shape case (`agent_session_id != session_id`) proving each writer + the budget resolver now land.
- Add `tests/unit/test_session_resolver.py` covering all four branches.
- Keep the no-env → no-op case (unset BOTH env vars).

### 3. Validate
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: hooks-resolver-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the four unit test files; confirm the bridge-shape assertion and the `grep` anti-criterion.
- Confirm the CLI-sidecar test (`test_cli_hook_writes_current_tool_name_and_datetime`) still passes untouched.
- Run ruff check/format.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: hooks-resolver-validator
- **Agent Type**: documentarian
- **Parallel**: false
- Apply the Documentation-section doc edits.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Resolver tests pass | `pytest tests/unit/test_session_resolver.py tests/unit/test_pre_tool_use_liveness_writes.py tests/unit/test_liveness_writers_turn_boundary.py -q` | exit code 0 |
| No AGENT_SESSION_ID-fed session_id filter left in hooks | `grep -n 'AGENT_SESSION_ID' agent/hooks/liveness_writers.py agent/hooks/pre_tool_use.py \| grep -c 'filter(session_id'` | match count == 0 |
| Resolver reads VALOR_SESSION_ID | `grep -c 'VALOR_SESSION_ID' agent/hooks/session_resolver.py` | output > 0 |
| Resolver uses get_by_id | `grep -c 'get_by_id' agent/hooks/session_resolver.py` | output > 0 |
| Lint clean | `python -m ruff check agent/hooks/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/hooks/` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. Resolver location: standalone `agent/hooks/session_resolver.py` (proposed) vs.
   a private helper inside `liveness_writers.py` imported by `pre_tool_use.py`.
   Proposed keeps the dependency direction clean; confirm you agree.
2. Confirm no second `filter(session_id=<hex>)` fallback is wanted on the
   `AGENT_SESSION_ID` path — the plan treats it as scar tissue and uses only
   `get_by_id`. Any known surface that sets `AGENT_SESSION_ID` to a `session_id`
   value in production would change this.
