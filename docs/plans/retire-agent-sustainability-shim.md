---
status: Planning
type: chore
appetite: Small
owner: Dev (sdlc-2875)
created: 2026-08-25
tracking: https://github.com/tomcounsell/ai/issues/2875
last_comment_id:
---

# Retire the `agent/sustainability.py` shim (PR 2 of 2)

## Problem

`agent/sustainability.py` is a pure compatibility surface. Its five reflection
callables moved to `reflections/agents/*.py` in #1028; the file survived only so
the reflections registry's historical dotted paths (`agent.sustainability.*`)
kept resolving. Its own docstring already says "new code should import the
reflection directly."

PR #2944 (commit `c9a91bdad`) shipped the registry half: a tracked migration
script rewrites both `config/reflections.yaml` and the vault copy onto
`reflections.agents.*`, wired into `/update` at Step 1.659. The shim was
deliberately left in place so it would outlive the rewrite on machines that had
not yet run `/update`.

**Current behavior:**

Three production import sites still reach through the shim:

- `agent/agent_session_queue.py:2899` — `from agent.sustainability import send_hibernation_notification`
- `reflections/stall_advisory.py:65-78` — two wrapper functions that do nothing
  but delegate to `agent.sustainability._get_redis` / `_get_project_key`

The shim also holds one of **seven** byte-identical copies of the
`_get_project_key()` / `_get_redis()` pair (the other six live in
`reflections/agents/{circuit_health_gate,failure_loop_detector,session_count_throttle,session_recovery_drip,system_health_digest}.py`
and `reflections/stall_advisory.py`). `tests/unit/test_default_project_key_consistency.py`
exists specifically because these copies can drift — it asserts the writer-side
`DEFAULT_PROJECT_KEY` still agrees with the reader-side fallback. That test is a
symptom of the duplication, not a fix for it.

**Desired outcome:**

`agent/sustainability.py` is deleted. Every production caller imports from the
real module. The `_get_project_key` / `_get_redis` pair has exactly one
definition inside `reflections/`.

## Freshness Check

**Baseline commit:** `483c7cd14ae712a04c21fa6f908c10069577253e`
**Issue filed at:** 2026-08-19T07:45:10Z
**Disposition:** Minor drift — the yaml half of the issue is already done; the
remaining scope is exactly what PR #2944's body designates as "PR 2".

**File:line references re-verified:**

- `agent/sustainability.py` — still exists, 79 lines, still a pure re-export
  shim plus the two helpers. **Still holds.**
- `agent/agent_session_queue.py:2899` — `from agent.sustainability import
  send_hibernation_notification`. **Still holds** at that exact line.
  (`agent/agent_session_queue.py:948` also names the shim in a docstring.)
- `reflections/stall_advisory.py:67` / `:74` — the two delegating wrappers.
  **Still holds.**
- Issue claim "`config/reflections.yaml` still has 5 `callable:` entries pointing
  at `agent.sustainability.*`" — **NO LONGER TRUE.** `grep -n sustain` returns
  zero callable hits in both `config/reflections.yaml` and
  `~/Desktop/Valor/reflections.yaml`. PR #2944 migrated both copies.
- Issue claim that `config/reflections.yaml` is "a gitignored symlink to the
  vault copy" — **FALSE as stated.** It is gitignored (`.gitignore:8`) but it is
  a *real file copy*, not a symlink, maintained by `/update` Step 1.66
  (`env_sync.sync_reflections_yaml`), which copies vault→config only when the
  config copy is older. Verified with `ls -la` and `git check-ignore -v`. The
  distinction matters: an edit to the repo copy does not propagate to the vault,
  which is exactly why PR #2944 shipped a script that rewrites *both*.

**Cited sibling issues/PRs re-checked:**

- **PR #2944** — MERGED 2026-08-24. Shipped `scripts/migrate_reflections_callables.py`,
  `scripts/update/reflections_callables.py`, and the Step 1.659 wiring. Its body
  explicitly scopes PR 2 as: delete the shim, repoint `agent_session_queue.py`
  and `stall_advisory.py`, update the live-import tests.
- **#2876** — OPEN. De-hubs `agent/agent_session_queue.py`; phase 1 already
  landed as `2239473f9`. Coordination constraint: keep this plan's edit to that
  file to the single import line.
- **#2879** — OPEN, running in parallel on `tests/unit/` file splitting. Its file
  set (`test_sdlc_session_ensure.py`, `test_sdlc_router_decision.py`,
  `test_valor_telegram.py`, `test_worktree_manager.py`) does not intersect this
  plan's test set.
- **#1028** — the original relocation that created the shim.
- **#2439** — deduplicated `send_hibernation_notification` into
  `reflections/agents/circuit_health_gate.py`; the shim re-exports it.

**Commits on main since issue was filed (touching referenced files):**

- `c9a91bdad` PR #2944 (PR 1 of 2) — **partially addresses**: completes the
  registry half, leaves the Python half.
- `2239473f9` de-hub phase 1 of #2876 — dropped 16 dead re-exports from
  `agent_session_queue.py`; did not touch the sustainability import.
- `b8333d115`, `2183222d1`, `511f7e936`, `12d815ccd`, `616a53007` — irrelevant
  to this surface.

**Active plans in `docs/plans/` overlapping this area:** none.

## Prior Art

- **PR #2944** — *Ship reflections.yaml migration off the agent.sustainability
  shim (PR 1 of 2)* — MERGED. This plan is its stated successor. It did not fail;
  it deliberately stopped short.
- **#1028** — *reflections-modular* (`docs/archive/plans-completed/reflections-modular.md`)
  — moved the five callables into `reflections/agents/` and, at the war room's
  insistence, kept the shim rather than hard-cutting over. That decision is the
  direct cause of this issue. The stated reason (registry still names the old
  paths) no longer applies after #2944.
- **#2439** — collapsed the two `send_hibernation_notification` definitions to
  one in `circuit_health_gate.py`, re-exported through the shim. Succeeded; this
  plan removes the remaining re-export hop.
- **#1171** — established the `VALOR_PROJECT_KEY` empty-string fallback and
  `tests/unit/test_default_project_key_consistency.py`. Succeeded, but as a
  cross-copy consistency *assertion* rather than a de-duplication.
- **#2872 / PR #2943** — deleted `bridge/session_logs.py` and
  `models/reflections.py` re-export shims. Same class of work, merged cleanly one
  day before this plan. Confirms the pattern is routine here.

No prior attempt to delete this shim failed — none was made.

## Research

No relevant external findings — this is a purely internal refactor of first-party
modules with no external libraries, APIs, or ecosystem patterns involved.
Phase 0.7 skipped per the skill's stated skip condition.

## Data Flow

The reflection scheduler resolves a `callable:` dotted path from the registry:

1. **Entry point**: `agent/reflection_scheduler.py` reads `config/reflections.yaml`
   (a real file copy of `~/Desktop/Valor/reflections.yaml`, refreshed by `/update`
   Step 1.66).
2. **Resolution**: `_resolve_callable()` does an `importlib` import of the dotted
   path. **Post-#2944 this path is `reflections.agents.<module>.run` — the shim is
   no longer on this route at all.**
3. **Execution**: the reflection body calls `_get_redis()` / `_get_project_key()`
   to build its project-scoped Redis keys.

The two remaining routes *through* the shim are separate from the scheduler:

- **Hibernation**: `agent/agent_session_queue.py` → `agent.sustainability` →
  `reflections.agents.circuit_health_gate.send_hibernation_notification` →
  enqueues a Telegram AgentSession. One pointless hop.
- **Stall advisory**: `reflections/stall_advisory.py` → local wrapper →
  `agent.sustainability._get_redis` → `popoto.redis_db.POPOTO_REDIS_DB`. Two
  pointless hops.

After this change both go direct, and the registry route is unchanged.

## Architectural Impact

- **New dependencies**: one new first-party module, `reflections/redis_access.py`
  (~25 lines, stdlib `os` plus a lazy `popoto` import). No third-party additions.
- **Interface changes**: `agent.sustainability` ceases to exist as an importable
  module. The names it re-exported remain importable at their real locations.
  Within `reflections/`, the module-private `_get_redis` / `_get_project_key` are
  replaced by shared `get_redis` / `get_project_key`.
- **Coupling**: net decrease. Deletes an `agent/` → `reflections/` dependency edge
  that existed only to serve `reflections/` consumers, and collapses six
  duplicate helper definitions into one.
- **Data ownership**: unchanged. Same Redis connection, same key prefix, same
  fallback semantics (`VALOR_PROJECT_KEY` stripped, empty → `"valor"`).
- **Reversibility**: high. Pure deletion plus mechanical import repointing; a
  revert restores the shim verbatim.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Registry already migrated off the shim | `grep -c "agent.sustainability" ~/Desktop/Valor/reflections.yaml` | The vault registry must already name `reflections.agents.*`, else deleting the shim kills five reflections on this machine |
| Migration ships in tracked code | `test -f scripts/migrate_reflections_callables.py` | Every other machine self-heals its registry at `/update` Step 1.659 |

## Solution

### Key Elements

- **`reflections/redis_access.py`** (new): the single canonical definition of
  `get_project_key()` and `get_redis()` for everything under `reflections/`.
- **`reflections/agents/*.py` (5 modules)**: drop their private copies, import
  the canonical pair.
- **`reflections/stall_advisory.py`**: drop the two shim-delegating wrappers,
  import the canonical pair.
- **`agent/agent_session_queue.py`**: import `send_hibernation_notification`
  from `reflections.agents.circuit_health_gate` directly. One line plus one
  docstring mention. Nothing else in that file is touched (#2876 coordination).
- **`agent/sustainability.py`**: deleted.

### Flow

Not a user-facing feature. The operator-visible flow is the `/update` cycle:

`git pull` (Step 1, shim now absent) → **Step 1.659 migration rewrites the
registry onto `reflections.agents.*`** → Step 1.66 vault→config sync → Step 5
service restart (worker loads the new registry).

The migration runs *before* the service restart on the same cycle, so a machine
that has not yet migrated its registry heals itself in the same `/update` that
removes the shim.

### Technical Approach

- **Public names, not private ones.** The helpers become `get_redis` /
  `get_project_key` in the new module. They are now a deliberate shared API
  across six call sites; keeping the leading underscore would be a lie about
  their scope. Each consumer does `from reflections.redis_access import
  get_project_key, get_redis`, which binds the names into the consumer's module
  namespace — so existing per-module `unittest.mock.patch` targets keep working
  after a mechanical `_get_redis` → `get_redis` rename of the patch string.
- **Lazy `popoto` import stays inside `get_redis()`.** All seven current copies
  import `popoto.redis_db` inside the function body. Hoisting it to module scope
  would make importing any reflection module open a Redis connection at import
  time. Preserve the lazy form verbatim.
- **`send_hibernation_notification` moves nowhere.** Its canonical definition is
  already in `reflections/agents/circuit_health_gate.py` (#2439). This plan only
  removes the re-export hop.
- **Scope boundary on `agent_session_queue.py`.** Exactly two lines change: the
  import at 2899 and the docstring reference at 948. #2876 owns everything else
  in that file.
- **The migration script keeps its `agent.sustainability.*` strings.** They are
  the *source* side of a rename table (`CALLABLE_MIGRATIONS`); the script cannot
  do its job without naming what it migrates *from*. Deleting them would silently
  disarm the self-heal for every machine that has not yet run `/update`. See
  Verification for how the acceptance-criterion grep is scoped around this.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new exception handlers are introduced. `get_redis()` and
      `get_project_key()` are the same non-raising bodies as today (the lazy
      `popoto` import can raise, exactly as it can today; callers already wrap).
- [ ] `send_hibernation_notification` retains its existing internal
      `try/except` — untouched, and `tests/unit/test_sustainability.py` already
      covers its never-raises contract.

### Empty/Invalid Input Handling
- [ ] `get_project_key()` must preserve the #1171 semantics exactly: strip the
      env value, fall back to `"valor"` on empty/whitespace-only. The existing
      cases in `tests/unit/test_default_project_key_consistency.py` (unset,
      empty, whitespace, set) are repointed at the canonical function and must
      all still pass.
- [ ] No new functions take user input; there is no new empty-input surface.

### Error State Rendering
- [ ] No user-visible output changes. The one user-visible path
      (`send_hibernation_notification` → Telegram) is byte-identical after the
      re-export hop is removed.

## Test Impact

- [ ] `tests/unit/test_sustainability.py` — UPDATE: rename ~20
      `patch("reflections.agents.X._get_redis", ...)` targets to
      `...X.get_redis`. Any `from agent.sustainability import ...` becomes the
      real module. Consider renaming the file to `test_reflection_agents.py`
      only if it is free; do not fight #2879 over it.
- [ ] `tests/unit/test_sustainability_namespace.py` — REPLACE: this file asserts
      the *shape of the shim namespace*. With the shim gone, its subject is gone.
      Replace with an assertion that `agent.sustainability` is no longer
      importable and that each of the five callables plus
      `send_hibernation_notification` resolves at its real
      `reflections.agents.*` location.
- [ ] `tests/unit/test_default_project_key_consistency.py` — UPDATE: repoint the
      seven `from agent.sustainability import _get_project_key` sites at
      `reflections.redis_access.get_project_key`; update the module docstring.
      The assertions themselves are unchanged and still meaningful (they guard
      the writer↔reader agreement, which survives de-duplication).
- [ ] `tests/unit/test_session_health_sibling_phantom_safety.py` — UPDATE: three
      `from agent.sustainability import <callable>` imports become
      `from reflections.agents.<module> import run`; fix the module docstring's
      "(sustainability.py)" annotations.
- [ ] `tests/integration/test_stall_advisory_e2e.py:362` — UPDATE: import the
      canonical `get_project_key` / `get_redis` from `reflections.redis_access`.
- [ ] `tests/unit/test_reflection_scheduler.py:761` — UPDATE: the comment
      describing entries that "resolve through re-export shims" is now false.
- [ ] `tests/e2e/test_session_continuity.py:68` — UPDATE: comment-only reference
      to `agent.sustainability._get_project_key`.
- [ ] `tests/unit/test_update_install_worker.py:186` — UPDATE: comment-only
      reference.
- [ ] `tests/unit/test_migrate_reflections_callables.py` — NO CHANGE. Its
      `agent.sustainability.*` strings are migration-source fixtures and must
      survive. Same for `tests/unit/test_update_reflections_callables.py`.
- [ ] `tests/unit/test_agent_session_queue.py` — NO CHANGE expected; its
      "sustainability" references are prose about throttle guards, not imports.
      Verify by running it.

## Rabbit Holes

- **Rewriting `reflections/utilities.py` into the canonical home.** It already
  has a `_get_redis` at line 267, which makes it look like the natural host. It
  is not: it is a heavy module (`subprocess`, `config.settings`, LLM helpers) and
  making five tiny reflection modules import it at module scope drags that weight
  into every reflection import. A dedicated ~25-line module is the right size.
  De-duplicating `utilities.py`'s own copy is explicitly out of scope.
- **De-hubbing `agent/agent_session_queue.py`.** #2876 owns it. Change the one
  import line and stop.
- **Deleting `scripts/migrate_reflections_callables.py` "because the migration is
  done."** It is done *on this machine*. It is the self-heal for the other three.
  Retiring it is a separate decision that needs every machine confirmed
  migrated.
- **Chasing the `agent.sustainability` strings in `docs/archive/plans-completed/`.**
  Those are historical records of completed plans. They are supposed to name what
  existed at the time.
- **Renaming `tests/unit/test_sustainability.py`.** Tempting for tidiness,
  collides with a parallel lane's working set. Not worth the coordination cost.

## Risks

### Risk 1: A machine runs the new code against an unmigrated registry
**Impact:** Five self-healing reflections (`circuit_health_gate`,
`session_recovery_drip`, `session_count_throttle`, `failure_loop_detector`,
`sustainability_digest`) fail to import. `run_reflection` swallows the failure in
a broad `except`, records `last_error`, and keeps ticking — so the loss is
**silent**. Circuit-breaker recovery and session-count throttling stop working
with no alert.
**Mitigation:** This is the exact hazard PR #2944 was ordered to prevent. `/update`
Step 1 (git pull) → Step 1.659 (migration) → Step 5 (service restart) means any
machine that receives this code via the sanctioned path migrates its registry
before the worker reloads. The migration is idempotent and runs every cycle.
Verified locally: both the vault and config registries on this machine are
already migrated, so this machine is safe regardless. The residual exposure is a
machine that takes the code by a bare `git pull` without `/update` — which is
already unsupported (CLAUDE.md: `/update` after every merge).

### Risk 2: Step 1.656 prunes the five reflections before Step 1.659 migrates them
**Impact:** Would be severe — reflections deleted from the vault registry rather
than repointed.
**Mitigation:** Checked and **does not apply**. Step 1.656 iterates an explicit
name list (`reflection_register.REMOVED_REFLECTIONS`), not a dynamic
"does this callable import?" probe. None of the five names is on it. No change
needed; recorded here so the next reader does not have to re-derive it.

### Risk 3: The acceptance-criterion grep fails on its own necessary strings
**Impact:** `git grep -n "agent.sustainability"` cannot return zero — the
migration script's rename table, its two test files, and the archived plan docs
all legitimately contain the string. A naive AC check reads as failure.
**Mitigation:** Scope the Verification greps to `agent/ reflections/ config/` and
to *import statements* rather than any occurrence. Documented explicitly in
Verification so the reviewer is not surprised.

### Risk 4: A missed `patch()` target silently no-ops
**Impact:** `unittest.mock.patch("reflections.agents.X._get_redis")` against a
name that no longer exists raises `AttributeError` at patch time — loud, not
silent. The genuinely silent failure mode is the reverse: a test that patches the
canonical module while the consumer holds a from-import binding.
**Mitigation:** Keep every consumer on `from reflections.redis_access import
get_redis` (name bound into the consumer namespace) and keep every patch target
module-local. Do not introduce `import reflections.redis_access` +
`redis_access.get_redis()` call style, which would change patch semantics.

## Race Conditions

No race conditions identified. Every change is a compile-time import
redirection; no new concurrency, no new shared mutable state, no ordering
dependency between the touched modules at runtime. The one ordering concern in
this work is a *deployment* ordering (registry migration vs. shim deletion),
handled under Risk 1 and already solved by PR #2944's `/update` step placement —
it is a sequencing question, not a data race.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2876] De-hubbing `agent/agent_session_queue.py`. This plan
  changes exactly one import line and one docstring line in that file.
- [SEPARATE-SLUG #2879] Splitting or renaming files under `tests/unit/` that the
  parallel lane owns.
- [ORDERED] Deleting `scripts/migrate_reflections_callables.py` and its Step
  1.659 wiring. Blocked on a human-gated event: confirmation that all four
  machines in `projects.json` have run `/update` past `c9a91bdad`. Until then the
  script is the only thing standing between an unmigrated machine and five
  silently-dead reflections.
- De-duplicating `reflections/utilities.py:267`'s own `_get_redis`. It serves a
  different consumer set (`sdlc_progress`, `sdlc_upvote_lanes`) and touching it
  widens the blast radius into unrelated reflection tests for no gain against
  this issue's acceptance criteria. Filed as a note in the Rabbit Holes section
  rather than deferred work — it is a deliberate boundary, not a promise.

## Update System

No new update-system work is required. The mechanism this change depends on —
`/update` Step 1.659 (`scripts/update/reflections_callables.py` →
`scripts/migrate_reflections_callables.py`) — already shipped in PR #2944 and
runs on every cycle, before the Step 5 service restart. This plan deliberately
adds nothing to `scripts/update/` and removes nothing from it.

One doc-level task only: the Step 1.659 comment at `scripts/update/run.py:1048`
says the migration repoints callables "off the `agent.sustainability.*` shim."
After this plan the shim does not exist, so the comment should say the registry
must not *reacquire* those paths. Wording change, no behavior change.

## Agent Integration

No agent integration required. This is an internal import refactor. No new CLI
entry point in `pyproject.toml [project.scripts]`, no new MCP tool, no change to
what the bridge imports. `send_hibernation_notification` remains reachable on the
same worker code path (`agent/agent_session_queue.py`), only via a shorter import.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/adding-reflection-tasks.md` if it names
      `agent.sustainability` as an example callable path — the canonical example
      must be `reflections.agents.<module>.run`.
- [ ] Grep `docs/features/` and `CLAUDE.md` for live (non-archival) references to
      `agent/sustainability.py` and repoint or remove them. Archived plans under
      `docs/archive/plans-completed/` are historical records and are NOT edited.
- [ ] No new `docs/features/*.md` page — this deletes a compatibility layer, it
      does not add a feature. No `docs/features/README.md` index entry.

### Inline Documentation
- [ ] `reflections/redis_access.py` module docstring states why the pair is
      centralized: seven copies existed and `tests/unit/test_default_project_key_consistency.py`
      was the guardrail against their drift.
- [ ] `agent/agent_session_queue.py:948` docstring stops naming the deleted file.
- [ ] `scripts/update/run.py:1048` Step 1.659 comment reworded (see Update System).

## Success Criteria

- [ ] `agent/sustainability.py` no longer exists.
- [ ] No import statement anywhere under `agent/`, `reflections/`, `bridge/`,
      `worker/`, `tools/`, or `tests/` names `agent.sustainability`.
- [ ] Exactly one definition of `get_project_key` / `get_redis` under
      `reflections/agents/` and `reflections/stall_advisory.py` — namely the
      import from `reflections.redis_access`.
- [ ] Every registry callable resolves with `agent.sustainability` banned from
      `sys.modules` (the issue's AC #2, re-run against the live registry).
- [ ] `agent/agent_session_queue.py` diff is exactly two lines.
- [ ] Targeted tests pass (see Verification).
- [ ] `ruff check` and `ruff format --check` clean.
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (shim removal)**
  - Name: `shim-builder`
  - Role: Create the canonical helper module, repoint all production callers,
    delete the shim.
  - Agent Type: builder
  - Resume: true

- **Builder (test repointing)**
  - Name: `test-builder`
  - Role: Update the eight affected test files.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `shim-validator`
  - Role: Run the Verification table, confirm the registry resolves with the
    shim banned.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create the canonical helper module
- **Task ID**: build-redis-access
- **Depends On**: none
- **Validates**: `tests/unit/test_default_project_key_consistency.py`
- **Assigned To**: shim-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `reflections/redis_access.py` with `get_project_key()` and
  `get_redis()`, bodies copied verbatim from `agent/sustainability.py:58-74`
  (keep the lazy `popoto.redis_db` import inside `get_redis`).
- Module docstring records the de-duplication rationale and the #1171 fallback
  semantics.

### 2. Repoint the reflections consumers
- **Task ID**: build-reflections-consumers
- **Depends On**: build-redis-access
- **Validates**: `tests/unit/test_sustainability.py`, `tests/integration/test_stall_advisory_e2e.py`
- **Assigned To**: shim-builder
- **Agent Type**: builder
- **Parallel**: false
- In each of `reflections/agents/{circuit_health_gate,failure_loop_detector,session_count_throttle,session_recovery_drip,system_health_digest}.py`:
  delete the local `_get_project_key` / `_get_redis`, add
  `from reflections.redis_access import get_project_key, get_redis`, rename the
  call sites.
- In `reflections/stall_advisory.py`: delete the two shim-delegating wrappers at
  lines 65-78 and the now-dead section banner, import the canonical pair, rename
  its six call sites.

### 3. Repoint the queue caller and delete the shim
- **Task ID**: build-delete-shim
- **Depends On**: build-reflections-consumers
- **Validates**: `tests/unit/test_agent_session_queue.py`
- **Assigned To**: shim-builder
- **Agent Type**: builder
- **Parallel**: false
- `agent/agent_session_queue.py:2899` → `from reflections.agents.circuit_health_gate import send_hibernation_notification`.
- `agent/agent_session_queue.py:948` docstring: drop the `agent/sustainability.py`
  mention. **These two lines are the entire diff for this file.**
- `git rm agent/sustainability.py`.
- Reword the `scripts/update/run.py:1048` Step 1.659 comment.

### 4. Repoint the tests
- **Task ID**: build-tests
- **Depends On**: build-delete-shim
- **Validates**: all files listed in Test Impact
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Work the Test Impact checklist top to bottom.
- `test_sustainability_namespace.py` gets the REPLACE treatment: assert the shim
  is gone and each name resolves at its real home.
- Do NOT touch `test_migrate_reflections_callables.py` or
  `test_update_reflections_callables.py`.

### 5. Documentation
- **Task ID**: document-removal
- **Depends On**: build-tests
- **Assigned To**: shim-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Work the Documentation checklist. Leave `docs/archive/` alone.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-removal
- **Assigned To**: shim-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the Verification table and report pass/fail per row.

## Verification

Scoping note for the two grep rows: `agent.sustainability` legitimately survives
in `scripts/migrate_reflections_callables.py` (its rename table's *source* keys),
in that script's two test files, and in `docs/archive/plans-completed/`. The rows
below are therefore scoped to production packages and to import statements, which
is the acceptance criterion's actual intent ("no hits in production code").

| Check | Command | Expected |
|-------|---------|----------|
| Shim deleted | `test -e agent/sustainability.py` | exit code != 0 |
| Shim unimportable | `.venv/bin/python -c "import importlib.util as u; raise SystemExit(0 if u.find_spec('agent.sustainability') is None else 1)"` | exit code 0 |
| No shim imports in production packages | `git grep -c "agent\.sustainability" -- agent/ reflections/ bridge/ worker/ tools/ config/` | match count == 0 |
| No shim imports in tests | `git grep -n "^\s*from agent\.sustainability\|^\s*import agent\.sustainability" -- tests/` | exit code 1 |
| Helper defined exactly once under reflections/agents + stall_advisory | `git grep -c "^def get_redis\|^def _get_redis" -- reflections/agents/ reflections/stall_advisory.py` | match count == 0 |
| Canonical module exists | `git grep -c "^def get_project_key" -- reflections/redis_access.py` | output contains 1 |
| Registry resolves with shim banned | `.venv/bin/python scripts/verify_registry_without_shim.py` | exit code 0 |
| Queue file diff is two lines | `git diff origin/main --numstat -- agent/agent_session_queue.py \| awk '{print $1+$2}'` | output contains 2 |
| Migration self-heal still armed | `git grep -c "agent.sustainability.circuit_health_gate" -- scripts/migrate_reflections_callables.py` | output > 0 |
| Targeted tests pass | `scripts/pytest-clean.sh tests/unit/test_sustainability.py tests/unit/test_sustainability_namespace.py tests/unit/test_default_project_key_consistency.py tests/unit/test_session_health_sibling_phantom_safety.py tests/unit/test_reflection_scheduler.py tests/unit/test_migrate_reflections_callables.py tests/unit/test_update_reflections_callables.py tests/unit/test_agent_session_queue.py -q` | exit code 0 |
| Stall advisory e2e passes | `scripts/pytest-clean.sh tests/integration/test_stall_advisory_e2e.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

The `verify_registry_without_shim.py` helper referenced above is the AC #2 probe:
it inserts a `sys.meta_path` finder that raises on `agent.sustainability`, loads
the live registry, and imports every `callable:` entry. It is a build artifact of
this plan (a small script under `scripts/`), not a pre-existing file — the
builder creates it as part of task 3. It is worth committing rather than running
ad hoc, because `tests/unit/test_reflection_scheduler.py::test_all_callables_resolve`
resolves the registry vault-first and therefore validates whatever file happens to
be on the running machine (flagged in PR #2944's body as not a real CI gate).

## Critique Results

<!-- Populated by /do-plan-critique. -->

---

## Open Questions

None blocking. Two judgment calls made explicitly rather than escalated:

1. **Scope of de-duplication.** The issue asks only that `stall_advisory` stop
   importing the shim's helpers. This plan additionally folds the five
   `reflections/agents/*.py` copies onto the canonical module, because those five
   are the same drift class that `tests/unit/test_default_project_key_consistency.py`
   was written to police, and the edit is mechanical. If the critique judges this
   scope creep, tasks 1-2 shrink to `stall_advisory` alone with no effect on the
   acceptance criteria.
2. **The migration script stays.** Deleting it is tagged `[ORDERED]` in No-Gos
   pending confirmation that all four machines have run `/update` past
   `c9a91bdad`. That confirmation needs a human with access to the other three
   machines.
