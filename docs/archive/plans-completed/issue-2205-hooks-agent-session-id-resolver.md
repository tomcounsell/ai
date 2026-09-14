---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-23
tracking: https://github.com/tomcounsell/ai/issues/2205
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-23T02:57:08Z
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

- **Shared hooks resolver (standalone module)**: `agent/hooks/session_resolver.py`
  exposes two functions — `resolve_inflight_session()` (reads `VALOR_SESSION_ID`
  first via `filter(session_id=…)`; on a **miss** falls through to
  `AGENT_SESSION_ID` via `AgentSession.get_by_id()`; else `None`; lets
  Popoto/Redis exceptions propagate) and `inflight_cooldown_key()` (pure env read
  returning the stable per-session cooldown bucket base, no Redis).
- **`pre_tool_use._resolve_sdk_session`**: delegates to `resolve_inflight_session()`.
  Keeps its RAISES-on-infra-error contract (the loud "backstop BLIND" path in
  `_enforce_tool_budget_sdk` catches separately).
- **`liveness_writers` — env-only call sites (`record_tool_boundary`,
  `record_thinking_excerpt`)**: take the cooldown bucket from
  `inflight_cooldown_key()`, check the cooldown FIRST, then resolve via
  `resolve_inflight_session()` and write. Keep fail-silent (`try/except` → DEBUG
  → return `False`).
- **`liveness_writers.record_turn_boundary` — dual path**: the explicit
  `session_id` worker call path (from `sdk_client.py`) is preserved unchanged
  (direct `filter(session_id=…)` on the passed value); only the `session_id is
  None` in-subprocess path routes through `inflight_cooldown_key()` +
  `resolve_inflight_session()`.

### Flow

Bridge session runs a tool → SDK hook fires → resolver reads `VALOR_SESSION_ID`
→ `filter(session_id=…)` → 1 row → budget attributed / liveness stamped → visible
on dashboard.

### Technical Approach

**Resolver location (Open Question 1 — RESOLVED): standalone module.** The
resolver lives in a new `agent/hooks/session_resolver.py`, not as a private
helper inside `liveness_writers.py`. Both `pre_tool_use.py` and
`liveness_writers.py` import from it, so a standalone module keeps the
dependency direction clean (a shared leaf both hook modules depend on) and
avoids a `liveness_writers ↔ pre_tool_use` import cycle. This is the critique's
resolved decision, not a lingering open question.

**New module `agent/hooks/session_resolver.py`** exposing **two** functions.
The split exists deliberately: the cheap cooldown key must be derivable from env
*without* a Redis round-trip so the liveness writers can keep their
cooldown-first ordering (see the cooldown-ordering note below).

1. `inflight_cooldown_key() -> str | None` — returns
   `os.environ.get("VALOR_SESSION_ID") or os.environ.get("AGENT_SESSION_ID")`.
   Pure env read, no Popoto/Redis touch. This is the **stable per-session
   cooldown bucket base**: within one running harness the same env var is always
   present, so the chosen key is stable across the whole session regardless of
   which identifier ultimately resolves the row. **This closes the cooldown-key
   interface gap the critique flagged** — callers no longer infer "which env var
   resolved" from the resolver's return value (which is now ambiguous because of
   the VALOR-miss → AGENT fallthrough below); they take the bucket base straight
   from this helper. Callers still append their per-metric suffix (`:turn`,
   `:thinking`, or none for the tool-boundary bucket).

2. `resolve_inflight_session() -> AgentSession | None`:
   1. `sid = os.environ.get("VALOR_SESSION_ID")`; if truthy, take the first
      `AgentSession.query.filter(session_id=sid)` match. **If it matches, return
      it.**
   2. **VALOR miss-fallthrough (critique concern):** if `VALOR_SESSION_ID` was
      set but produced **zero** rows, do **not** return `None` yet — fall
      through to step 3. (A stale/mismatched `VALOR_SESSION_ID` must not shadow a
      resolvable `AGENT_SESSION_ID`.)
   3. `aid = os.environ.get("AGENT_SESSION_ID")`; if truthy, return
      `AgentSession.get_by_id(aid)` (may be `None`).
   4. Else `None`. Do **not** swallow exceptions — resolution/infra errors
      propagate to the caller.

- **`_resolve_sdk_session()`** becomes a thin wrapper over
  `resolve_inflight_session()` (preserving its docstring contract: `None` for a
  genuine no-session, RAISES on infra error). It has no cooldown, so it does not
  use `inflight_cooldown_key()`.
- **`record_tool_boundary` / `record_thinking_excerpt`** (env-only call sites):
  replace the `os.environ.get("AGENT_SESSION_ID")` + `filter(session_id=…)`
  blocks with (a) `bucket = inflight_cooldown_key()` for the cooldown check and
  (b) `resolve_inflight_session()` for the write. The `record_tool_boundary`
  inner persist (`_save_tool_boundary`, `:69-96`) currently takes a `session_id`
  param and re-queries — refactor it to accept the resolved `AgentSession` so it
  does not re-issue the wrong filter.
- **`record_turn_boundary` — PRESERVE the explicit-`session_id` worker path
  (critique concern).** This function is called two ways:
  - *Worker-process path* (`agent/sdk_client.py` `result` handler, plumbed from
    `agent/session_runner/runner.py`): passes an **explicit** `session_id` (the
    true `AgentSession.session_id`) because `AGENT_SESSION_ID` is unset in that
    process. When `session_id is not None`, resolve it **directly** via
    `filter(session_id=session_id)` and key the cooldown on that same explicit
    value. Do **NOT** route this path through the env-only resolver — the
    resolver reads env vars that are absent in the worker process, so collapsing
    it would silently break the worker call site.
  - *In-subprocess path* (`session_id is None`): use `inflight_cooldown_key()`
    for the bucket and `resolve_inflight_session()` for the write, exactly like
    the other two writers.
- **Cooldown ordering (critique nit — preserve).** Each liveness writer must
  check its cooldown bucket **before** resolving/writing, identical to today: a
  cooldown-dropped write must never issue a Popoto query. Because
  `inflight_cooldown_key()` is a pure env read, the sequence stays
  cooldown-check → (only if not coalesced) resolve → write. No new Redis hit is
  introduced on the coalesced path.
- **Why `get_by_id` for `AGENT_SESSION_ID`, not a second `filter(session_id)`
  fallback**: `AGENT_SESSION_ID` holds the AutoKey hex; `get_by_id` is the
  correct primary-key lookup. Adding a `filter(session_id=<hex>)` fallback would
  be defensive scar tissue that can never match. The two-path resolver
  (session_id-first with miss-fallthrough, id-fallback) is the whole fix.
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
- [ ] Resolver with `VALOR_SESSION_ID` set to a non-matching id AND
  `AGENT_SESSION_ID` unset → `None` (VALOR miss, no fallback available).
- [ ] **VALOR miss-fallthrough:** `VALOR_SESSION_ID` set to a non-matching id but
  `AGENT_SESSION_ID` set to a valid hex → resolver returns the session via the
  `get_by_id` fallthrough (NOT `None`). This is the critique-concern branch and
  MUST be asserted.
- [ ] Resolver with `AGENT_SESSION_ID` set to a non-existent hex (and no VALOR
  match) → `get_by_id` returns `None`.
- [ ] `record_turn_boundary` explicit-`session_id` worker path (no env vars set)
  → resolves directly and writes; asserts the env-only resolver is NOT invoked
  for this path (worker-process regression guard).

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
- [ ] `agent/hooks/session_resolver.py` — new unit test covering all resolver
  branches: VALOR match; VALOR miss → AGENT get_by_id hit (fallthrough); VALOR
  miss + AGENT unset → None; AGENT-only get_by_id hit; AGENT get_by_id miss →
  None; both unset → None. Plus `inflight_cooldown_key()` returns VALOR when set,
  else AGENT, else None.

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
**Mitigation:** Key the cooldown on `inflight_cooldown_key()` (VALOR if set,
else AGENT), computed by env *presence* — NOT by which identifier ultimately
resolved the row. This matters now that the resolver can resolve via the AGENT
fallthrough even when VALOR is present: the bucket base stays pinned to VALOR
(the present env var) regardless. Within one running harness the same env var is
always present, so the bucket is stable. Covered by keeping the existing cooldown
tests green.

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
- [ ] **Anti-criterion (non-vacuous):** all env-identifier handling is
  centralized in the resolver module — the two hook modules no longer name the
  env vars directly:
  `grep -c 'AGENT_SESSION_ID\|VALOR_SESSION_ID' agent/hooks/liveness_writers.py agent/hooks/pre_tool_use.py`
  reports `0` for BOTH files. (Supersedes the prior vacuous
  `AGENT_SESSION_ID | filter(session_id` pipe, which could never match two
  strings on one line.) NOTE: `record_turn_boundary`'s preserved explicit-param
  worker path legitimately keeps one `filter(session_id=session_id)` on its
  passed argument — so a blanket "no `filter(session_id` in liveness_writers"
  check is intentionally NOT used; the env-name check above is the correct
  anti-criterion.
- [ ] **Dashboard (operator-facing):** a live bridge PM session
  (`agent_session_id != session_id`) shows a populated `current_tool_name` /
  `last_turn_at` on the localhost:8500 dashboard while running a tool, where it
  previously rendered empty. Spot-check via
  `curl -s localhost:8500/dashboard.json` (or the dashboard UI) against a live
  bridge session, or assert the equivalent persisted fields in the bridge-shape
  unit test if no live session is available.
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
- Create `agent/hooks/session_resolver.py` with BOTH `resolve_inflight_session()` (VALOR_SESSION_ID → filter(session_id); on **miss** fall through to AGENT_SESSION_ID → get_by_id; else None; exceptions propagate) and `inflight_cooldown_key()` (pure env read: VALOR else AGENT; no Redis).
- Rewrite `agent/hooks/pre_tool_use.py::_resolve_sdk_session()` to delegate to `resolve_inflight_session()` (preserve RAISES-on-infra contract).
- Rewrite `record_tool_boundary` and `record_thinking_excerpt`: cooldown bucket from `inflight_cooldown_key()`, check cooldown FIRST, then resolve + write; refactor `_save_tool_boundary` to take the resolved session.
- `record_turn_boundary`: PRESERVE the explicit-`session_id` worker path (direct `filter(session_id=…)` on the passed value); only the `session_id is None` in-subprocess path routes through the helper + resolver.
- Verify no `AGENT_SESSION_ID`/`VALOR_SESSION_ID` string remains in either hook module (all env handling in `session_resolver.py`).
- Update module/function docstrings (both hook modules currently claim `AGENT_SESSION_ID`-only resolution).

### 2. Update + extend tests
- **Task ID**: build-tests
- **Depends On**: build-resolver
- **Assigned To**: hooks-resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- Update fixtures to set `AGENT_SESSION_ID = agent_session_id` (hex) and `VALOR_SESSION_ID = session_id` distinctly.
- Add a bridge-shape case (`agent_session_id != session_id`) proving each writer + the budget resolver now land.
- Add `tests/unit/test_session_resolver.py` covering all resolver branches (incl. the VALOR-miss → AGENT get_by_id fallthrough) and `inflight_cooldown_key()`.
- Add a `record_turn_boundary` explicit-`session_id` worker-path case (no env vars) proving that path still resolves and writes.
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
| Env-identifier handling centralized in resolver (hooks don't name env vars) | `grep -c 'AGENT_SESSION_ID\|VALOR_SESSION_ID' agent/hooks/liveness_writers.py agent/hooks/pre_tool_use.py` | `0` for both files |
| Resolver reads VALOR_SESSION_ID | `grep -c 'VALOR_SESSION_ID' agent/hooks/session_resolver.py` | output > 0 |
| Resolver uses get_by_id | `grep -c 'get_by_id' agent/hooks/session_resolver.py` | output > 0 |
| Lint clean | `python -m ruff check agent/hooks/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/hooks/` | exit code 0 |

## Critique Results

Critique verdict: **READY TO BUILD (WITH CONCERNS)** — 0 blockers. All concerns
folded into this revision pass.

| Severity | Finding | Addressed By | Implementation Note |
|----------|---------|--------------|---------------------|
| Concern | `record_turn_boundary`'s explicit-`session_id` worker path (from `sdk_client.py`, where `AGENT_SESSION_ID` is unset) would break if collapsed into the env-only resolver | Technical Approach (`record_turn_boundary` dual path); Step 1; Failure Path Test Strategy | Explicit-param path resolves directly via `filter(session_id=…)`; only the `session_id is None` path uses the resolver. Regression-guarded by a no-env explicit-path test. |
| Concern | On `VALOR_SESSION_ID` filter-miss the resolver returned `None`, shadowing a resolvable `AGENT_SESSION_ID` | `resolve_inflight_session()` step 2 (miss-fallthrough); Empty/Invalid test list | VALOR miss now falls through to `get_by_id(AGENT_SESSION_ID)` before returning `None`. Dedicated fallthrough test. |
| Concern | Cooldown-key interface gap — resolver return value doesn't expose which env var resolved, so callers can't key the cooldown stably (worse under the fallthrough) | New `inflight_cooldown_key()` helper; Risk 1 mitigation | Cooldown bucket keyed by env *presence* (VALOR else AGENT), independent of which identifier resolved the row. |
| Concern | Vacuous grep verification (`grep AGENT_SESSION_ID … \| grep filter(session_id` can't match two strings on one line) | Success Criteria anti-criterion; Verification table | Replaced with `grep -c 'AGENT_SESSION_ID\|VALOR_SESSION_ID'` == 0 in both hook modules (env handling centralized in resolver). |
| Concern | Open Question 1 (standalone module vs private helper) unresolved | Technical Approach (resolver location); Open Questions | RESOLVED: standalone `agent/hooks/session_resolver.py` — clean dependency direction, no import cycle. |
| Nit | Cooldown ordering must stay cooldown-check-before-Redis | Technical Approach cooldown-ordering note; Step 1 | `inflight_cooldown_key()` is a pure env read; coalesced writes issue no Popoto query. |
| Nit | Add a user-facing dashboard success criterion | Success Criteria (dashboard bullet) | Live bridge session shows populated `current_tool_name`/`last_turn_at` on localhost:8500 where it was empty. |

---

## Resolved Questions

1. **Resolver location — RESOLVED: standalone module.**
   `agent/hooks/session_resolver.py`, imported by both `pre_tool_use.py` and
   `liveness_writers.py`. A private helper inside `liveness_writers.py` was
   rejected because it would force `pre_tool_use.py` to import from
   `liveness_writers.py`, coupling the two hook modules; a shared leaf keeps the
   dependency direction clean and cycle-free.
2. **Second `filter(session_id=<hex>)` fallback — RESOLVED: not added.** The
   `AGENT_SESSION_ID` path uses only `get_by_id` (primary-key lookup for the
   AutoKey hex). A `filter(session_id=<hex>)` fallback can never match and would
   be scar tissue. NOTE: this is distinct from the VALOR-miss → `get_by_id`
   fallthrough added in this revision (critique concern), which routes to the
   *correct* `get_by_id` lookup, not a second `filter`.
