---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-13
tracking: https://github.com/tomcounsell/ai/issues/2763
last_comment_id: none
---

# Subprocess harnesses must inherit the parent's claimed test DB

## Problem

`tests/unit/test_sdlc_tool_wrapper.py` shells out to a Python child with
`cwd=str(REPO_ROOT)` and no `env=`. Popoto resolves `REDIS_URL` at import time
and falls back to `redis://localhost:6379` — **db0, production** — when the
variable is unset. The parent pytest process isolates itself correctly by
claiming a db from the `[1..15]` pool (`tests/db_claim.py`, `fcntl.flock`), but
that claim lives in the parent's *in-process Popoto client objects*, not in the
environment, so the child inherits nothing and lands on production.

**Current behavior:** three subprocess call sites in that file (and three more
that pass a bare `os.environ.copy()`, which is the same hole with extra steps)
launch children that read and write production Redis db0. A stray
`PipelineLedger` row for the fake issue `999999` is sitting in db0 today as
evidence, key `PipelineLedger:tomcounsell//ai{:}999999`.

Under the code as shipped the *write* in the reviewed test never fires — the
injected `boom` raises before the ledger write — so this is latent, not actively
corrupting. It is one refactor of `_cli_record`'s ordering away from not being
latent, and the same shape exists at ~30 other call sites across the suite.

**Desired outcome:** every test subprocess that can reach Popoto runs against
the parent's claimed test db, by construction, via the existing
`tests.db_claim.subprocess_env` helper. A mechanical guard fails the suite if a
new call site reintroduces the shape.

## Freshness Check

**Baseline commit:** `90c0e81e4`
**Issue filed at:** 2026-08-13T06:10:34Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `tests/unit/test_sdlc_tool_wrapper.py:232` — issue claims a `subprocess.run`
  with `cwd=REPO_ROOT` and no `env=` — **drifted to line 230**. PR #2706 merged
  at 06:28Z (18 minutes after filing) and shifted the file by two lines. Same
  code, same defect.
- `tests/db_claim.py:187` — `subprocess_env(*, project_root=None, **extra)` —
  still holds, unchanged since PR #2606.
- `tests/conftest.py:571` — `redis_test_db` autouse fixture — still holds, and
  confirms the mechanism: it swaps the Popoto client objects in-process and
  never writes `REDIS_URL` into `os.environ`.

**Cited sibling issues/PRs re-checked:**
- #2637 / PR #2706 — merged 2026-08-13T06:28:56Z (`171c1871a`). It is the review
  that surfaced this issue. It rebound the lease helpers through the module
  object; it did not touch the subprocess env.
- #2628 / PR #2683 (`session/suite-failure-rotation-db-ownership`) — **still
  open**. Touches `tests/conftest.py`, `tests/db_claim.py`,
  `tests/unit/test_conftest_isolation_guards.py`,
  `tests/unit/test_redis_flush_guard_prod.py`, and others. This is a
  coordination constraint, see No-Gos.

**Commits on main since issue was filed (touching referenced files):**
- `171c1871a` Reach lease helpers through the module, closing the #2469 freeze
  class (#2637) (#2706) — irrelevant to the root cause; only shifted line
  numbers in the target file.

**Active plans in `docs/plans/` overlapping this area:**
`suite-failure-rotation-db-ownership` (issue #2628, PR #2683) owns the db-claim
*machinery*. This plan owns the *call sites*. No file overlap once the No-Gos
below are respected.

**Notes:** Corrected line numbers are used throughout the Technical Approach.

## Prior Art

- **PR #2606**: "Repair two shared-state leaks that make the suite's failure set
  unreproducible (#2603, #2605)" — created `tests/db_claim.py` and the
  `subprocess_env` helper precisely for this hazard, and converted the call
  sites that were flaking at the time (`TestKillCommandIntegration`). It did not
  sweep the suite. **This plan is the sweep it deferred.**
- **PR #2117 / issue #2060**: "Fix cross-process Redis test-db collision" —
  introduced the flock-based per-process claim replacing the
  `PYTEST_XDIST_WORKER`-derived db number. Establishes that deriving a db number
  by hand is always wrong; `claim_test_db()` is the only source.
- **PR #2061 / issue #1897**: xdist isolation umbrella — fixed popoto db-cache
  split-brain in-process. Same family, in-process scope only.
- **PR #2683 / issue #2628** (open): makes a `flushdb()` on an unclaimed db raise
  at its own line and exports `POPOTO_TEST_DB`. Strengthens the parent side;
  does not close the subprocess hole.

**Why the earlier fixes left this open:** every prior fix hardened the *parent*
process — client objects, claim registry, flush guard. None of them can reach a
child process, because the only channel to a child is the environment and no
fixture writes `REDIS_URL` there. `subprocess_env` is the bridge; it just was
never applied suite-wide.

## Research

No relevant external findings — proceeding with codebase context. This is
purely internal test hygiene: no new libraries, APIs, or ecosystem patterns.
Popoto's `REDIS_URL` semantics were verified by reading the installed package's
`popoto/redis_db.py` docstring directly rather than from training data.

## Spike Results

### spike-1: Does setting `REDIS_URL` in the child env actually redirect Popoto?
- **Assumption**: "Popoto in the child reads `REDIS_URL` from the environment at
  import time, so `subprocess_env()` alone is sufficient."
- **Method**: code-read (installed package source)
- **Finding**: Confirmed. `popoto/redis_db.py`: "Connects to Redis or Valkey
  using the `REDIS_URL` environment variable, or falls back to
  `localhost:6379`… The connection is established at module import time using
  environment variables." The fallback is db0.
- **Confidence**: high
- **Impact on plan**: The fix is exactly `env=subprocess_env(...)`. No child-side
  code changes, no conftest changes.

### spike-2: Can anything in the child clobber the injected `REDIS_URL`?
- **Assumption**: "Repo code loaded by the child does not override `REDIS_URL`
  after the env is set."
- **Method**: code-read (`grep` for `REDIS_URL` and `load_dotenv` across
  `agent/ tools/ config/ worker/ bridge/`)
- **Finding**: Every consumer uses `os.environ.get("REDIS_URL", <db0 default>)`
  — a read, never a write. `load_dotenv` is called in `tools/valor_session.py`,
  `tools/valor_email.py`, `tools/valor_telegram.py`, `tools/doctor.py`, always
  with the default `override=False`, so an explicitly-set `REDIS_URL` wins over
  `.env`. There is no `load_dotenv(..., override=True)` anywhere in the repo.
- **Confidence**: high
- **Impact on plan**: No defensive ordering needed. Confirms the No-Go against
  an `os.environ.setdefault` "fix" — that would be a silent no-op whenever
  `.env` has already populated the variable, and would still leave the child on
  whatever the parent's ambient value is rather than the claimed db.

## Data Flow

1. **Entry point**: `pytest` starts. `tests/db_claim.claim_test_db()` takes an
   exclusive `flock` on `/tmp/valor-pytest-db-claims-6379/{n}.lock` and memoizes
   `n` for the process lifetime.
2. **Parent isolation**: the autouse `redis_test_db` fixture
   (`tests/conftest.py:571`) builds `redis.Redis(db=n)` and rebinds
   `POPOTO_REDIS_DB` on `popoto.redis_db` and every popoto submodule that
   captured it at import. **This state is in-process only.** `os.environ` is
   never mutated.
3. **The gap**: `subprocess.run([sys.executable, ...], cwd=REPO_ROOT)` forks. The
   child gets the parent's `os.environ`, in which `REDIS_URL` is unset (or holds
   the production value from a shell/launchd context).
4. **Child**: imports `tools.sdlc_verdict` → imports popoto → `popoto.redis_db`
   resolves `REDIS_URL` → falls back to `redis://localhost:6379` → **db0**.
5. **Output**: any `PipelineLedger` / `AgentSession` read or write in the child
   hits production. The parent's teardown `flushdb()` targets db `n` and never
   cleans it up, so the residue persists after the run.

**After the fix**, step 3 becomes: `subprocess_env()` calls `redis_test_url()` →
`claim_test_db()` (memoized, same `n`) → writes `REDIS_URL=redis://127.0.0.1:6379/n`
into the child's env, and step 4 resolves to db `n`.

## Architectural Impact

- **New dependencies**: none. `tests/db_claim.py` is already imported by
  `tests/conftest.py` and several test modules.
- **Interface changes**: none. `subprocess_env` is called, not modified.
- **Coupling**: slightly increases the number of test modules importing
  `tests.db_claim` — which is the intended direction: one shared definition of
  "which db do I own" instead of per-file reinventions.
- **Data ownership**: unchanged in production code. In tests, it moves the
  child's db choice from "ambient environment" to "the parent's claim".
- **Reversibility**: trivial. Every change is a per-call-site kwarg.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (scope is fully specified by the recon audit)
- Review rounds: 1

The work is mechanical and the audit surface is already enumerated. The only
genuine thinking is the two call sites where a naive conversion would weaken an
existing assertion (see Rabbit Holes).

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable on the default port | `redis-cli -p 6379 ping` | The db-claim pool and both parent/child clients need a live server |
| `tests/db_claim.subprocess_env` present on main | `python -c "import ast,sys; src=open('tests/db_claim.py').read(); sys.exit(0 if 'def subprocess_env' in src else 1)"` | The fix calls this helper; it must exist before the sweep |

Run via `python scripts/check_prerequisites.py docs/plans/subprocess-harness-test-db-inheritance.md`.

## Solution

### Key Elements

- **Call-site conversion**: every `subprocess.run`/`Popen` in `tests/` that
  launches a Python interpreter or repo script against a repo checkout passes
  `env=subprocess_env(...)`, so the child's `REDIS_URL` names the parent's
  claimed db.
- **Extras pass-through**: call sites that need additional variables
  (`AI_REPO_ROOT`, `VIRTUAL_ENV`, …) supply them as `subprocess_env(**extra)`
  keyword arguments instead of hand-building a dict, so nobody re-derives a db
  number.
- **Mechanical guard**: a new test performs an AST scan of `tests/**/*.py` and
  fails on any subprocess call that launches a Python interpreter with a
  repo-rooted `cwd` and an `env=` expression that does not derive from
  `subprocess_env`. This is what keeps the fix from rotting.
- **Post-merge residue cleanup**: the stray `PipelineLedger` row for issue
  `999999` in db0 is deleted through the ORM once the fix lands.

### Flow

Test process claims db `n` → test calls `subprocess.run(..., env=subprocess_env(...))`
→ child inherits `REDIS_URL=.../n` → child's Popoto import binds to db `n` →
child's reads/writes land in the same db the parent flushes at teardown →
production db0 is never touched.

### Technical Approach

**Group A — `tests/unit/test_sdlc_tool_wrapper.py` (the filed defect).**

- Lines **172**, **189**, **230**: add
  `env=subprocess_env(project_root=str(REPO_ROOT))`. These run
  `sys.executable -c <harness>` / `python -m tools.sdlc_verdict get` with
  `cwd=str(REPO_ROOT)`; pinning `project_root` additionally guarantees the
  child resolves repo modules from *this* checkout rather than whatever the
  shared venv's `.pth` names — the worktree hazard documented in
  `tests/db_claim.py`'s docstring.
- Lines **87**, **101**: replace `env = os.environ.copy(); env["AI_REPO_ROOT"] = …`
  with `env = subprocess_env(AI_REPO_ROOT=…)`. These deliberately point at a
  bad `AI_REPO_ROOT` and exit 2 inside the bash wrapper before Python starts, so
  they cannot reach Redis today — convert them anyway so the file has one shape
  and the guard needs no per-line exemptions.
- Line **129** (`test_dispatch_from_foreign_cwd_with_own_tools_succeeds`):
  replace with `subprocess_env(AI_REPO_ROOT=str(REPO_ROOT))` — **and do not pass
  `project_root`**. This test plants a decoy `tools/` package in a foreign `cwd`
  and asserts the wrapper still resolves the real one; injecting `REPO_ROOT` on
  `PYTHONPATH` muddies the very resolution order the test exists to pin. Only
  the `REDIS_URL` half of the helper is wanted here.
- Lines **69**, **75**, **81** need no change: they pass neither `cwd` nor `env`
  and terminate on the bash usage/`--help` path without importing Python. The
  guard's predicate (Python interpreter + repo-rooted `cwd`) does not match
  them, so no exemption comment is required.

**Group B — the audited sweep.** Same conversion at the remaining call sites the
AST scan surfaced (~30 sites, 12 files):

`tests/unit/test_sdlc_meta_set.py` (6), `test_sdlc_session_ensure.py` (3),
`test_sdlc_stage_marker.py` (2), `test_sdlc_stage_query.py` (1),
`test_memory_search_cli.py` (2), `test_session_telemetry.py` (1),
`test_evaluate_build.py` (3), `test_worker_entry.py` (2),
`test_worker_supervisor.py` (1), `test_migrate_strip_pid_fields.py` (1),
`tests/integration/test_sdlc_dispatch.py` (2),
`tests/integration/test_design_system_pipeline.py` (4),
`tests/integration/test_session_telemetry_e2e.py` (1).

`test_session_telemetry_e2e.py` runs against a `WORKTREE` rather than the main
checkout, so it takes `project_root=str(WORKTREE)`.

**Group C — one hand-rolled helper that re-derives the db.**
`tests/unit/test_session_lifecycle.py:1602`'s `_subprocess_env` reads the db back
out of `POPOTO_REDIS_DB.connection_pool.connection_kwargs`. It happens to land on
the right db today, but it is the exact anti-pattern the db-ownership work
forbids, and it breaks the moment the parent's client is rebound differently.
Replace its body with `env = subprocess_env()` followed by the existing
`env.pop("SDLC_HOLDER_TOKEN", None)` — that pop is load-bearing (the test proves
the env seam is gone) and `subprocess_env` has no removal argument, so it stays a
two-line helper rather than becoming a direct call.

**Group D — the guard.** New file
`tests/unit/test_subprocess_test_db_isolation.py`. It walks `tests/**/*.py` with
`ast`, and for each `subprocess.run`/`Popen`/`check_output`/`check_call`/`call`:

- match only calls whose first positional argument mentions a Python interpreter
  or a repo entry point (`sys.executable`, `PYTHON`, `"-m"`, `scripts/`, the
  `WRAPPER` constant) **and** whose `cwd=` expression names a repo checkout
  (`REPO_ROOT`, `PROJECT_DIR`, `WORKTREE`, `parents[2]`, …);
- assert the call has an `env=` keyword whose source text mentions
  `subprocess_env`;
- report every violation as `path:line` in one assertion message, not one
  failure per site.

The guard deliberately ignores `git` invocations in `tmp_path` scratch repos
(85 of the 115 raw `cwd=`-without-`env=` hits) — they have no Popoto import and
sweeping them would be noise with no isolation value.

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope. The changes are keyword arguments on existing
  `subprocess.run` calls plus a new read-only AST scan; no `try`/`except` is
  added or modified. `tests/db_claim.py` (which does contain fallback handlers)
  is not touched.

### Empty/Invalid Input Handling
- The guard must not silently pass when it finds nothing to scan. It asserts the
  scanned-file count is greater than zero before asserting zero violations —
  otherwise a future refactor of the `tests/` layout turns the guard into a
  no-op that always passes. This is the "empty output must not read as success"
  rule applied to a static check.
- `subprocess_env()` with no arguments is the documented common case and needs
  no new handling.

### Error State Rendering
- The guard's failure message lists every offending `path:line` plus the
  one-line remedy (`pass env=subprocess_env(...)`), so a developer who trips it
  does not have to reverse-engineer the predicate. A guard that fails with a
  bare `assert not violations` is a swallowed error in practice.

## Test Impact

- [ ] `tests/unit/test_sdlc_tool_wrapper.py` (6 call sites: lines 87, 101, 129, 172, 189, 230) — UPDATE: pass `env=subprocess_env(...)`; assertions unchanged.
- [ ] `tests/unit/test_sdlc_meta_set.py` (6 sites) — UPDATE: add `env=subprocess_env(project_root=str(REPO_ROOT))`.
- [ ] `tests/unit/test_sdlc_session_ensure.py` (3 sites) — UPDATE: same.
- [ ] `tests/unit/test_sdlc_stage_marker.py` (2 sites) — UPDATE: same.
- [ ] `tests/unit/test_sdlc_stage_query.py` (1 site) — UPDATE: same.
- [ ] `tests/unit/test_memory_search_cli.py` (2 sites) — UPDATE: same.
- [ ] `tests/unit/test_session_telemetry.py` (1 site) — UPDATE: same.
- [ ] `tests/unit/test_evaluate_build.py` (3 sites) — UPDATE: same, via the file's existing `_run` helper.
- [ ] `tests/unit/test_worker_entry.py` (2 sites) — UPDATE: same.
- [ ] `tests/unit/test_worker_supervisor.py` (1 site) — UPDATE: same.
- [ ] `tests/unit/test_migrate_strip_pid_fields.py` (1 site) — UPDATE: same.
- [ ] `tests/unit/test_session_lifecycle.py::_subprocess_env` — REPLACE: helper body becomes `subprocess_env()` + the existing `SDLC_HOLDER_TOKEN` pop.
- [ ] `tests/integration/test_sdlc_dispatch.py` (2 sites, plus `_isolated_subprocess_env` at line 272) — UPDATE: base the local helper on `subprocess_env()` so the guard's predicate is satisfied and the db is claimed rather than assumed.
- [ ] `tests/integration/test_design_system_pipeline.py` (4 sites) — UPDATE: same.
- [ ] `tests/integration/test_session_telemetry_e2e.py` (1 site) — UPDATE: `project_root=str(WORKTREE)`.
- [ ] `tests/unit/test_subprocess_test_db_isolation.py` — CREATE: the AST guard.

No assertions change meaning anywhere. Every edit is additive to the subprocess
invocation; the tests' subjects and expectations are untouched.

## Rabbit Holes

- **Sweeping all 115 `cwd=`-without-`env=` call sites.** The large majority are
  `git` commands inside `tmp_path` scratch repos that never import popoto.
  Converting them adds imports and churn to ~40 files for zero isolation gain,
  and would force the guard's predicate to be so broad it generates false
  positives forever. Stop at "launches Python against a repo checkout".
- **Passing `project_root` to the foreign-cwd test.** It looks like the
  consistent thing to do and it quietly degrades the assertion. Called out
  explicitly in the Technical Approach because a mechanical sweep will get this
  wrong by default.
- **Making the guard a hook instead of a test.** A `PreToolUse` validator would
  catch it at authoring time, which is tempting — but hook registration is
  generated from `.claude/hooks/manifest.toml`, the validator would need its own
  test coverage, and the shape is only detectable with a full AST parse of the
  edited file. A test in the suite is where this belongs.
- **Fixing `os.environ` globally in `conftest.py`** (setting `REDIS_URL` once for
  the whole session so every child inherits it for free). It is the more elegant
  fix and it is **off limits**: `conftest.py`'s db-claim machinery is owned by
  open PR #2683, and mutating global process env from a fixture has its own
  cross-test hazards. Revisit only after #2683 lands, as a separate issue.
- **Consolidating the three hand-rolled `_subprocess_env` helpers.** Group C
  handles the one that actively re-derives a db number. The other two are a
  refactor, not a bug.

## Risks

### Risk 1: A converted call site depended on inheriting an ambient variable that `subprocess_env` reorders
**Impact:** A test starts failing for an unrelated reason and the failure looks
like a flake.
**Mitigation:** `subprocess_env` starts from `{**os.environ}` and only *adds*
`REDIS_URL` (and optionally prepends `PYTHONPATH`). Nothing is removed. The only
behavioral delta for a site that previously passed no `env` at all is the two
variables the helper sets. Each touched file is run individually after
conversion.

### Risk 2: `PYTHONPATH` pinning changes module resolution in a test that cares
**Impact:** A test that asserts something about import order (the foreign-cwd
test is exactly this) silently stops testing what it claims.
**Mitigation:** `project_root` is opt-in per call site, not a default. The one
known import-order-sensitive site (line 129) is explicitly documented to omit
it. Reviewers check that every `project_root=` argument names the checkout the
test is actually exercising.

### Risk 3: Merge conflict with PR #2683
**Impact:** Rework at merge time.
**Mitigation:** The No-Gos below forbid touching the three files #2683 owns
(`tests/conftest.py`, `tests/db_claim.py`,
`tests/unit/test_conftest_isolation_guards.py`) plus
`tests/unit/test_redis_flush_guard_prod.py`, which is also in that PR's diff.
This plan only *calls* `subprocess_env`; #2683 keeps the helper and extends it
(adding a `POPOTO_TEST_DB` export), so call sites are forward-compatible either
way it lands.

### Risk 4: The guard is written to pass rather than to catch
**Impact:** A green check that proves nothing — the worst outcome here, since
the whole point is durability.
**Mitigation:** Red-state proof is mandatory. Before the sweep is applied, run
the guard against unmodified `main` and confirm it reports the ~30 known sites;
paste that FAIL output into the PR description. A guard that has never been seen
red is not evidence.

## Race Conditions

No race conditions identified in the change itself: `subprocess_env()` is a pure
function of the already-memoized process claim, and the AST guard performs no
I/O beyond reading files.

The *bug being fixed* is, however, a race in the broader sense worth recording:

### Race 1: Child on db0 versus a concurrent production writer
**Location:** `tests/unit/test_sdlc_tool_wrapper.py:172,189,230` (pre-fix)
**Trigger:** Any test subprocess that reaches a Popoto write while the live
bridge/worker is serving traffic against db0.
**Data prerequisite:** The child must resolve `REDIS_URL` before its first
Popoto import — which it cannot do unless the parent put it in the environment.
**State prerequisite:** The parent's claimed db number must be stable for the
process lifetime, which `claim_test_db()`'s memoization plus the held `flock`
already guarantees.
**Mitigation:** Passing `env=subprocess_env(...)` closes the window entirely;
there is no ordering to get right because the value is fixed before `fork`.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2628] Any modification to `tests/conftest.py`,
  `tests/db_claim.py`, `tests/unit/test_conftest_isolation_guards.py`, or
  `tests/unit/test_redis_flush_guard_prod.py`. All four are in open PR #2683's
  diff. That PR also owns the `_subprocess_env` helper in
  `test_redis_flush_guard_prod.py`, which sets `PYTHONPATH` but **not**
  `REDIS_URL` — a genuine instance of this bug that must be fixed there, not
  here, to avoid a conflicting double-fix.
- [SEPARATE-SLUG #2628] Setting `REDIS_URL` in `os.environ` from a session-scoped
  fixture so children inherit it automatically. Architecturally the better fix;
  it belongs to whoever owns the conftest db-claim machinery.
- [DESTRUCTIVE] Deleting the stray `PipelineLedger` row for issue `999999` from
  production db0 before this PR merges. It is the live reproduction evidence for
  the issue; removing it early destroys the ability to demonstrate the bug. It is
  scheduled as the final post-merge task, via the ORM, scoped to that single key.

## Update System

No update system changes required — this is test-suite-internal. No new
dependencies, no config files to propagate, no migration for existing
installations. `/update` is unaffected.

## Agent Integration

No agent integration required — nothing here is reachable from a Telegram
message or a CLI entry point. No `pyproject.toml [project.scripts]` entry, no
bridge import. The only consumer is `pytest`.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/test-db-ownership.md` (created by PR #2683) with a
  "subprocesses" subsection stating the rule: any test that launches Python
  against a repo checkout passes `env=subprocess_env(...)`, and naming
  `tests/unit/test_subprocess_test_db_isolation.py` as the enforcing guard. **If
  #2683 has not merged when this ships**, put the same subsection in
  `tests/README.md` instead and leave a task on #2628 to fold it in — do not
  create a second competing feature doc.
- [ ] `docs/features/README.md` index: no new row needed if the content lands in
  the existing `test-db-ownership.md`; add one only if a new doc is created.

### Inline Documentation
- [ ] Module docstring on `tests/unit/test_subprocess_test_db_isolation.py`
  explaining the predicate, why `git`-in-`tmp_path` calls are excluded, and how
  to satisfy the guard.
- [ ] A one-line comment at
  `test_sdlc_tool_wrapper.py:129` recording *why* that call site omits
  `project_root` — otherwise a future consistency sweep re-adds it.

## Success Criteria

- [ ] Zero subprocess call sites in `tests/` launch a Python interpreter against
  a repo checkout without `env=subprocess_env(...)`.
- [ ] `tests/unit/test_subprocess_test_db_isolation.py` exists, and its red-state
  output against pre-fix `main` (listing the ~30 violations) is pasted in the PR
  description.
- [ ] `tests/unit/test_sdlc_tool_wrapper.py` passes in full.
- [ ] The full set of touched test files passes.
- [ ] No file owned by PR #2683 appears in this PR's diff.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] Post-merge: the `PipelineLedger` row for issue `999999` is deleted from db0
  via the ORM and its absence confirmed by an ORM read.

## Team Orchestration

### Team Members

- **Builder (call-site sweep)**
  - Name: `subprocess-env-builder`
  - Role: Convert every audited call site to `env=subprocess_env(...)`
  - Agent Type: builder
  - Domain: Redis/Popoto data
  - Resume: true

- **Builder (guard)**
  - Name: `isolation-guard-builder`
  - Role: Write the AST guard test and produce its red-state proof
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `isolation-validator`
  - Role: Verify no #2683-owned file is touched, no assertion semantics changed,
    guard is red before / green after
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `isolation-documentarian`
  - Role: Land the subprocess subsection in the correct doc given #2683's state
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Write the guard and capture its red state
- **Task ID**: build-guard
- **Depends On**: none
- **Validates**: tests/unit/test_subprocess_test_db_isolation.py (create)
- **Informed By**: spike-1 (REDIS_URL is read at import time, so the env is the
  only channel), recon AST scan (115 raw hits → ~33 Python-against-checkout hits)
- **Assigned To**: isolation-guard-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `tests/unit/test_subprocess_test_db_isolation.py` with an `ast`-based
  scan of `tests/**/*.py`.
- Predicate: `subprocess.{run,Popen,check_output,check_call,call}` whose first
  positional arg mentions a Python interpreter or repo entry point AND whose
  `cwd=` names a repo checkout; violation when `env=` is absent or its source
  text does not mention `subprocess_env`.
- Assert scanned-file count > 0 before asserting zero violations.
- Failure message lists every `path:line` and states the remedy.
- Run it against unmodified `main`, confirm it reports the ~30 known sites, and
  save that output verbatim for the PR description.

### 2. Fix the filed defect in test_sdlc_tool_wrapper.py
- **Task ID**: build-wrapper-file
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_tool_wrapper.py
- **Informed By**: spike-2 (nothing clobbers an explicit REDIS_URL)
- **Assigned To**: subprocess-env-builder
- **Agent Type**: builder
- **Parallel**: true
- Import `subprocess_env` from `tests.db_claim`.
- Lines 172, 189, 230: `env=subprocess_env(project_root=str(REPO_ROOT))`.
- Lines 87, 101: `env=subprocess_env(AI_REPO_ROOT=<bad path>)`.
- Line 129: `env=subprocess_env(AI_REPO_ROOT=str(REPO_ROOT))` with **no**
  `project_root`, plus the explanatory comment.
- Leave lines 69/75/81 alone.
- Run the file: `scripts/pytest-clean.sh tests/unit/test_sdlc_tool_wrapper.py`.

### 3. Sweep the remaining audited call sites
- **Task ID**: build-sweep
- **Depends On**: build-wrapper-file
- **Validates**: the 12 files listed in Test Impact Group B, plus
  tests/unit/test_session_lifecycle.py
- **Assigned To**: subprocess-env-builder
- **Agent Type**: builder
- **Parallel**: false
- Convert each site, threading extra variables through `subprocess_env(**extra)`
  rather than hand-built dicts.
- `test_session_telemetry_e2e.py`: `project_root=str(WORKTREE)`.
- Rebase `test_sdlc_dispatch.py::_isolated_subprocess_env` and
  `test_session_lifecycle.py::_subprocess_env` on `subprocess_env()`, preserving
  the latter's `SDLC_HOLDER_TOKEN` pop.
- Touch none of the four files named in the No-Gos.
- Run each touched file individually via `scripts/pytest-clean.sh`.

### 4. Validate
- **Task ID**: validate-all-sites
- **Depends On**: build-guard, build-sweep
- **Assigned To**: isolation-validator
- **Agent Type**: validator
- **Parallel**: false
- Guard now green; red-state output preserved in the PR body.
- `git diff --name-only main` contains none of `tests/conftest.py`,
  `tests/db_claim.py`, `tests/unit/test_conftest_isolation_guards.py`,
  `tests/unit/test_redis_flush_guard_prod.py`.
- No `assert` statement anywhere in the diff changed.
- Every `project_root=` argument names the checkout that test exercises.
- `test_sdlc_tool_wrapper.py:129` still omits `project_root`.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all-sites
- **Assigned To**: isolation-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Check whether PR #2683 has merged; route the subprocess subsection to
  `docs/features/test-db-ownership.md` if so, `tests/README.md` if not.
- Add the module docstring and the line-129 comment if the builders did not.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: isolation-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row.
- Confirm all Success Criteria except the post-merge cleanup.

### 7. Post-merge: delete the db0 reproduction row
- **Task ID**: cleanup-prod-residue
- **Depends On**: merge of this PR
- **Assigned To**: subprocess-env-builder
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: false
- **Only after the PR merges.** Against production db0, via the ORM only:
  `PipelineLedger.query.filter(...)` for `issue_number=999999`, confirm it is the
  single expected row, then `instance.delete()`.
- Never raw Redis (`delete`/`hgetall`/`scan_iter`) — blocked by
  `.claude/hooks/validators/validate_no_raw_redis_delete.py` and forbidden by
  CLAUDE.md regardless.
- Confirm absence with an ORM read afterwards and report the result on #2763.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Isolation guard passes | `scripts/pytest-clean.sh tests/unit/test_subprocess_test_db_isolation.py -q` | exit code 0 |
| Filed defect's file passes | `scripts/pytest-clean.sh tests/unit/test_sdlc_tool_wrapper.py -q` | exit code 0 |
| No bare `os.environ.copy()` env in the target file | `grep -c "os.environ.copy()" tests/unit/test_sdlc_tool_wrapper.py` | match count == 0 |
| Every wrapper-file subprocess with a repo cwd has an env | `python -c "import ast;t=ast.parse(open('tests/unit/test_sdlc_tool_wrapper.py').read());print(sum(1 for n in ast.walk(t) if isinstance(n,ast.Call) and ast.unparse(n.func).startswith('subprocess.') and any(k.arg=='cwd' for k in n.keywords) and not any(k.arg=='env' for k in n.keywords)))"` | output contains 0 |
| Anti-criterion: PR does not touch #2683-owned files | `git diff --name-only main -- tests/conftest.py tests/db_claim.py tests/unit/test_conftest_isolation_guards.py tests/unit/test_redis_flush_guard_prod.py \| wc -l` | output contains 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | The guard accepts any `env=` whose source text *mentions* `subprocess_env`. The two hand-rolled helpers this plan exists to kill are named `_isolated_subprocess_env` (tests/unit/test_sdlc_dispatch.py:272) and `_subprocess_env` (tests/unit/test_session_lifecycle.py:1602) — both contain that substring, so they pass the guard while re-deriving the db from `POPOTO_REDIS_DB.connection_pool.connection_kwargs`. A scan implementing the stated predicate silently accepted tests/unit/test_sdlc_dispatch.py:380,424,467,496. | pending | Resolve the `env=` value to a `Call` node and require `node.func.id == "subprocess_env"` (or `node.func.attr == "subprocess_env"`) plus an import of `tests.db_claim` in that module. Reject bare-name matches (`_isolated_subprocess_env`, `_subprocess_env`, `clean_env`). Add a self-test asserting a synthetic `env=_my_subprocess_env()` IS reported. |
| BLOCKER | History & Consistency | `_isolated_subprocess_env` "at line 272" is attributed to `tests/integration/test_sdlc_dispatch.py`; it actually lives at **tests/unit/test_sdlc_dispatch.py:272** and drives 4 sites (:380, :424, :467, :496). That file appears nowhere in Test Impact / Group B / the task list, so 4 db-re-deriving sites fall outside the sweep. The integration file's real exposure is 2 env-less sites (:321, :344). | pending | Add `tests/unit/test_sdlc_dispatch.py` as its own Test Impact row and Step 3 bullet. The helper also *removes* `VALOR_SESSION_ID` / `AGENT_SESSION_ID` / `active_run_id` (the #2144 seam) and `subprocess_env(**extra)` only adds keys, so the rebase must be `env = subprocess_env()` followed by popping those three keys — a naive one-line replacement re-inherits a live run identity and breaks the healable/unhealable boundary the tests assert. |
| CONCERN | Risk & Robustness | The guard's `cwd=` half matches on variable *spelling* (`REPO_ROOT`, `PROJECT_DIR`, `WORKTREE`, `parents[2]`). Files the plan converts do not use those spellings: test_worker_entry.py:68,86 and test_evaluate_build.py:136,155,171 use inline `Path(__file__).parent.parent.parent`; test_worker_supervisor.py:274 and test_session_telemetry.py:468 use lowercase `repo_root`. The guard goes green whether or not those files were swept. It also cannot see test_sdlc_tool_wrapper.py:129, which reaches Python against REPO_ROOT from `cwd=str(tmp_path)` — plausibly the shape that wrote the db0 row. | pending | Drop `cwd` from the match condition; gate on argv instead (`sys.executable`, a name containing `PYTHON`, a `"-m"` element, `WRAPPER`, a string containing `scripts/`). Carve out `SKIP_ARGV0 = {"git"}` plus a named, commented `path:line` allowlist so every exemption is a visible diff. |
| CONCERN | History & Consistency | The Group B enumeration is a stale recon snapshot and undercounts: re-running the plan's own predicate finds test_sdlc_stage_query.py with 4 matching sites (plan says 1), test_sdlc_stage_marker.py with 3 (plan says 2), test_sdlc_session_ensure.py with 4 (plan says 3). Several extras pass a local `clean_env` (stage_query :445/:604/:625, stage_marker :1187) which the guard accepts under no reading, so the sweep as scoped finishes with the guard still red. | pending | Sequence Task 1 before Task 3 (already the case) and add a Task 3 acceptance step: diff the guard's red-state `path:line` list against Group B; every line is either converted or added to the documented exemption list. Task 3 is not done off the Group B enumeration alone. |
| CONCERN | Scope & Value | The stated reason for converting lines 87/101 ("so the guard needs no per-line exemptions") is false under the plan's own predicate — those calls pass no `cwd`, so the predicate never matched them. The conversion is harmless, but the false premise will be propagated into a code comment. | pending | Restate as "uniform file shape / defence in depth". Lines 87/101 belong in the same category the plan already describes correctly for 69/75/81 ("pass neither `cwd` nor `env` … no exemption comment is required"). Fix the sentence, not the code. |
| CONCERN | Scope & Value | Task 7 declares `Depends On: merge of this PR` — not a task ID — and its matching Success Criterion (post-merge db0 row deletion) is unachievable at every gate that reads the checklist. Task 6 already exempts it in prose but the criterion itself is unmarked, so a reviewer finds a permanently unchecked box. | pending | Mark the criterion inline `(post-merge — tracked on #2763, not a merge gate)`; give task 7 no `Depends On` and a bold POST-MERGE header, or move it entirely to a comment on #2763 so no validator sees an unsatisfiable dependency edge. |
| NIT | History & Consistency | `tests/integration/test_session_telemetry_e2e.py`'s `WORKTREE = Path(__file__).parent.parent.parent` (line 16) resolves to the repo root, not a separate worktree; the plan's rationale ("runs against a `WORKTREE` rather than the main checkout") sends a reviewer hunting for something that is not there. The prescribed `project_root=str(WORKTREE)` is still correct. | pending | n/a (NIT) |
| NIT | Scope & Value | The second Prerequisites check command imports `ast` and never uses it; the check is a substring test that also passes if `def subprocess_env` appears in a comment or docstring. | pending | n/a (NIT) |
| NIT | Risk & Robustness | Verification row "No bare `os.environ.copy()` env in the target file" uses `grep -c`, which exits 1 when the count is 0 — the passing state carries a failing exit status. | pending | n/a (NIT) |

---

## Open Questions

None of these block the build: each carries a stated default position that the
plan already builds against. They are recorded for the critique round and for a
supervisor who wants to overrule a default.

1. **Guard placement.** The guard is proposed as a pytest test. Would you rather
   it be a `PreToolUse` hook validator so the shape is rejected at authoring
   time rather than at test time? (Plan's position: test — see Rabbit Holes.)
2. **Sweep breadth.** The plan converts the ~33 sites that launch Python against
   a repo checkout and deliberately leaves the ~85 `git`-in-`tmp_path` sites
   alone. Is that the right line, or do you want uniformity across all 115?
3. **Doc destination.** `docs/features/test-db-ownership.md` does not exist on
   main yet — it arrives with PR #2683. Confirm the fallback (write to
   `tests/README.md` and fold in later) rather than creating a second doc.
