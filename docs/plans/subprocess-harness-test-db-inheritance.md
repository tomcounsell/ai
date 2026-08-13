---
status: Ready
type: bug
appetite: Medium
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
latent, and the same shape exists at 113 other call sites across the suite (114
total across 54 files under the corrected predicate; see Appetite for the
convert-vs-allowlist split).

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

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

**Re-derived from the corrected predicate, not from the round-1 recon snapshot.**
Running the argv-gated predicate with one-hop delegator resolution over the real
tree gives **766 files scanned, 114 violations across 54 files**, which splits as:

- **70 sites / 32 files must be converted** — the child launches a repo module
  (`-m tools.…`, `-m worker`, …), the `WRAPPER`, or an inline `-c` script, so it
  can import popoto.
- **44 sites / 25 files are ALLOWLIST candidates** — the child is a standalone
  script (a `.claude/hooks/` validator, a `scripts/` one-file utility) that
  imports no repo package and therefore cannot reach Redis.

(The two file counts sum to 57, not 54: three files contain both a
convert-class and an allowlist-class site and are counted in each bucket.)

**Line-number convention.** Where this plan cites a call site by line, it names
the line of the `env=` keyword, not the line the `subprocess.run(` call opens on.
The two differ by a few lines in multi-line calls and the difference has
re-triggered a spurious "stale line numbers" finding in three consecutive
critique rounds. `_isolated_subprocess_env`'s four driven sites are cited by
call-start line — `:380, :424, :467, :496` — because that is what
`grep -n "_isolated_subprocess_env"` returns for the reader who goes looking.

That is roughly twice the round-1 estimate of "~30 sites / 16 files", which is
why the appetite moves from Small to **Medium**. The work stays one issue rather
than splitting: every conversion is the same mechanical kwarg, the risk is
uniform, and a second lane would duplicate the guard. The genuine thinking is
concentrated in four places — the two helpers that re-derive a db (Group C), the
one call site that must omit `project_root` (line 129), and the allowlist
adjudication rule below.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable on the default port | `redis-cli -p 6379 ping` | The db-claim pool and both parent/child clients need a live server |
| `tests/db_claim.subprocess_env` present on main | `python -c "import ast,sys; t=ast.parse(open('tests/db_claim.py').read()); sys.exit(0 if any(isinstance(n,ast.FunctionDef) and n.name=='subprocess_env' for n in ast.walk(t)) else 1)"` | The fix calls this helper; it must exist before the sweep. Checks for a real function definition, not a substring that a comment could satisfy |

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
  they cannot reach Redis today. Convert them for **uniform file shape and
  defence in depth** — one `env=` idiom per file, and no ambient `REDIS_URL`
  leaking to a child if the wrapper's early-exit path is ever relaxed. (The
  earlier draft justified this as "so the guard needs no per-line exemptions";
  that was false — both sites invoke `WRAPPER`, so the revised argv-gated
  predicate *does* match them, and converting them is what keeps the guard green
  rather than what avoids an exemption. Do not carry the old rationale into a
  code comment.)
- Line **129** (`test_dispatch_from_foreign_cwd_with_own_tools_succeeds`):
  replace with `subprocess_env(AI_REPO_ROOT=str(REPO_ROOT))` — **and do not pass
  `project_root`**. This test plants a decoy `tools/` package in a foreign `cwd`
  and asserts the wrapper still resolves the real one; injecting `REPO_ROOT` on
  `PYTHONPATH` muddies the very resolution order the test exists to pin. Only
  the `REDIS_URL` half of the helper is wanted here. Note this site reaches
  Python against `REPO_ROOT` from `cwd=str(tmp_path)` — it is invisible to any
  `cwd`-spelling-based predicate, which is one reason the guard gates on argv
  (Group D).
- Lines **69**, **75**, **81** invoke `WRAPPER` and so *are* matched by the
  argv-gated predicate. They pass no `env` today and terminate on the bash
  usage/`--help` path without importing Python. Convert them too:
  `env=subprocess_env()`. This is cheaper and more honest than three exemption
  entries, and leaves the whole file with a single shape.

**Group B — the audited sweep.** Same conversion at the remaining call sites.
**This enumeration is an indicative recon snapshot, not the work order.** A
re-run of the predicate found it undercounts (`test_sdlc_stage_query.py` has 4
matching sites, not 1; `test_sdlc_stage_marker.py` 3, not 2;
`test_sdlc_session_ensure.py` 4, not 3). The authoritative work order is the
guard's own red-state output, per the acceptance step on Task 3:

`tests/unit/test_sdlc_meta_set.py`, `test_sdlc_session_ensure.py`,
`test_sdlc_stage_marker.py`, `test_sdlc_stage_query.py`,
`test_memory_search_cli.py`, `test_session_telemetry.py`,
`test_evaluate_build.py`, `test_worker_entry.py`, `test_worker_supervisor.py`,
`test_migrate_strip_pid_fields.py`, `tests/unit/test_sdlc_dispatch.py`,
`tests/integration/test_sdlc_dispatch.py` (2 env-less sites, :321 and :344),
`tests/integration/test_design_system_pipeline.py`,
`tests/integration/test_session_telemetry_e2e.py`.

Several of these pass a locally-built `clean_env` dict that strips
`VALOR_SESSION_ID` / `AGENT_SESSION_ID` — `test_sdlc_stage_query.py:444,603,624`
and `test_sdlc_stage_marker.py:1179`. Only the stage_marker one sets `REDIS_URL`;
**the three stage_query sites set nothing**, so they run `python -m
tools.sdlc_stage_query` against production db0 on every run today. That is a
second live instance of the filed defect, not a stylistic wart. Convert them to
`env = subprocess_env()` followed by the existing pops, exactly as Group C does.

`test_session_telemetry_e2e.py` takes `project_root=str(WORKTREE)`. Note that
despite its name, that module's `WORKTREE` (line 16,
`Path(__file__).parent.parent.parent`) resolves to the repo root, not a separate
worktree; the argument is still correct, only the name is misleading.

**Group C — the hand-rolled helpers that re-derive the db.** Four module-local
env helpers are in scope here (a fifth exists elsewhere in the suite and is
deliberately untouched: it neither re-derives a db nor gates a converted site). Two are already correct thin delegators to
`subprocess_env` (landed with PR #2606; see Group D) and are left alone. The
other two read the
db back out of `POPOTO_REDIS_DB.connection_pool.connection_kwargs`. Both happen
to land on the right db today, but this is the exact anti-pattern the
db-ownership work forbids, and both break the moment the parent's client is
rebound differently. Neither collapses to a bare call, because each *removes*
keys and `subprocess_env(**extra)` only adds them:

- `tests/unit/test_session_lifecycle.py:1602` `_subprocess_env` → body becomes
  `env = subprocess_env()` then the existing `env.pop("SDLC_HOLDER_TOKEN", None)`.
  That pop is load-bearing: the test proves the env seam is gone.
- `tests/unit/test_sdlc_dispatch.py:272` `_isolated_subprocess_env` (drives the
  four sites at :380, :424, :467, :496) → body becomes `env = subprocess_env()`
  then pops of `VALOR_SESSION_ID`, `AGENT_SESSION_ID`, and `active_run_id`. Those
  three removals are the #2144 run-identity seam; a naive one-line replacement
  re-inherits a live run identity from the parent and dissolves the
  healable/unhealable boundary these tests assert.

**Group D — the guard.** New file
`tests/unit/test_subprocess_test_db_isolation.py`. It walks `tests/**/*.py` with
`ast`, and for each `subprocess.run`/`Popen`/`check_output`/`check_call`/`call`:

- **Match on argv only — `cwd=` is not part of the predicate.** Matching on the
  spelling of a `cwd` variable is what let the previous draft go green without
  the sweep: converted files use inline `Path(__file__).parent.parent.parent`
  (`test_worker_entry.py:68,86`, `test_evaluate_build.py:136,155,171`) or
  lowercase `repo_root` (`test_worker_supervisor.py:274`,
  `test_session_telemetry.py:468`), and `test_sdlc_tool_wrapper.py:129` reaches
  Python against `REPO_ROOT` from `cwd=str(tmp_path)` — plausibly the very shape
  that wrote the db0 row. A call matches when its first positional argument
  mentions `sys.executable`, a name containing `PYTHON`, a `"-m"` element, the
  `WRAPPER` constant, or a string containing `scripts/`.
- **Exclusions are explicit and visible in the diff**: a module-level
  `SKIP_ARGV0 = {"git"}` covering the `git`-in-`tmp_path` scratch repos (10 sites
  across 6 files under the argv-gated predicate — no Popoto import, no isolation
  value; note `tests/unit/test_sdlc_next_skill.py:209` has a starred argv0
  (`*env_git`) that `SKIP_ARGV0` cannot resolve statically and so needs its own
  `[standalone-script]` entry), plus a named
  `ALLOWLIST` of `path:line` entries, each with a comment giving its reason. Any
  future exemption is a reviewable line, never a silent predicate loophole.
- **Allowlist adjudication rule** — an entry is permitted for exactly two
  reasons, and the reason is written in the comment:
  1. `[#2628]` — the site lives in a file this plan's No-Gos forbid touching
     because open PR #2683 owns it. There are five such sites:
     `tests/unit/test_redis_flush_guard_prod.py:54,555,579,648` and
     `tests/unit/test_conftest_isolation_guards.py:463`. Allowlisting them in the
     *new* guard file touches no forbidden file, which is what breaks the
     otherwise-unsatisfiable deadlock between "guard green" and "no #2683-owned
     file in the diff". Each entry carries a note to remove it and convert the
     site when #2628 lands.
  2. `[standalone-script]` — the child is a single-file `.claude/hooks/`
     validator or `scripts/` utility that imports no repo package, so it cannot
     reach popoto. The ~44 sites in this class.

  **When reachability is unclear, convert — do not allowlist.** A parametrized
  argv (`[sys.executable, "-m", module, *argv]`, where `module` is a fixture
  parameter) reaches repo code and belongs in the conversion set even though a
  static scan cannot name the module.
- **The assign-mutate-return shape is accepted.** Every fix this plan prescribes
  produces `env = subprocess_env(); env.pop(...); return env` rather than a bare
  call, because the strips are load-bearing (Group B's `clean_env` rebases,
  Group C's two helpers) — and `tests/integration/test_bot_await_reply.py:33` is
  already written that way and is already correct. A predicate that accepts only
  a direct call therefore rejects the plan's own remedy and can never go green.
  The third acceptance rule: an `env=` value that is an `ast.Name` counts as
  clean when that name is bound in the enclosing scope by **exactly one**
  `Assign` from `subprocess_env(...)`, every subsequent statement touching it is
  a `.pop(<literal>, None)` or `.update(...)` on that same name, and it is never
  rebound. The identical rule applies inside a delegator whose body ends
  `return <name>`.
- **Method delegators resolve through the enclosing class.** Group C's own
  remedy for `tests/unit/test_session_lifecycle.py` leaves a `@staticmethod`
  invoked at `:1614` as `env=self._subprocess_env()` — an `ast.Attribute`, so the
  bare-`Name` clauses above do not reach it and the guard would stay red at a
  file Group C claims to fix. A `self.<name>()` or `cls.<name>()` value resolves
  to a method of the enclosing `ClassDef` (unwrapping `@staticmethod` /
  `@classmethod`), and that method's body is then judged by the same two body
  rules. A method that is not found in the enclosing class is a violation, not a
  pass.
- **Module-local delegators resolve one hop.** A function whose entire body is
  `return subprocess_env(...)` is a correct helper, not a violation. Two already
  exist and are already right — `tests/integration/test_agent_session_scheduler.py:24`
  (which alone drives 22 call sites) and `tests/integration/test_bot_await_reply.py`
  — both landed with PR #2606. A guard that rejects them flags ~25 already-correct
  sites and would push a builder to "fix" working code. Resolution is exactly one
  hop, and the delegator's body must satisfy either the single-`return` form or
  the assign-mutate-return rule above. A helper that derives its db from anything
  other than `subprocess_env` — the two Group C helpers as they stand today —
  stays a violation until rebased.
- **The `env=` check resolves the value to an AST node, not to source text.** A
  substring test on "subprocess_env" passes `_isolated_subprocess_env()` and
  `_subprocess_env()` — the two helpers this plan exists to kill. The check
  requires `env=` to be an `ast.Call` whose `func` is exactly the name
  `subprocess_env` (or an attribute access ending in `.subprocess_env`), plus an
  import of `tests.db_claim` in that module. Bare names such as
  `_isolated_subprocess_env`, `_subprocess_env`, and `clean_env` are violations.
- Report every violation as `path:line` in one assertion message, not one
  failure per site.
- **The guard's own fixtures are source strings, never live code.** The guard
  lives at `tests/unit/test_subprocess_test_db_isolation.py`, inside its own scan
  root, so a fixture written as real code would be reported on the first green
  run — and the obvious "fix" would be to allowlist it, quietly weakening the
  predicate's only proof. Each self-test passes a snippet to the scanner as a
  string (`scan_source(textwrap.dedent(...))`), so the scanner's public entry
  point takes source text and the file-walking wrapper sits above it. The guard's
  own path is additionally excluded from the walk.
- **Four self-tests, as a set**: (1) `env=_my_subprocess_env()` where the body
  re-derives the db from `POPOTO_REDIS_DB.connection_pool.connection_kwargs` is
  **reported**; (2) `def _w(**k): return subprocess_env(**k)` used as `env=_w()`
  is **accepted**; (3) `def _w(): e = subprocess_env(); e.pop("X", None); return e`
  used as `env=_w()` is **accepted**; (4) a `@staticmethod` of the same shape
  invoked as `env=self._w()` is **accepted**. Any one alone permits a scanner
  that is uselessly strict or uselessly loose; together they pin the predicate to
  "the db comes from `subprocess_env`, whatever else the helper does to the dict
  and however it is reached".

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

The rows below are the files needing *judgement*. They are not the whole set: the
conversion covers **70 sites across 32 files**, and the guard's red-state output
is the authoritative work order (Task 3). The remaining files not named here take
the plain mechanical conversion with no per-file decision:
`tests/integration/test_bot_await_reply.py`, `test_doc_impact_finder_sdk.py`,
`test_sdlc_cross_repo_resolution.py`, `test_watchdog_recovery.py`,
`tests/unit/session_runner/test_tool_activity_liveness.py`,
`test_critique_resume.py`, `test_features_readme_sort.py`,
`test_memory_timeline.py`, `test_room_resolution.py`,
`test_sdlc_fork_issue_number.py`, `test_session_lifecycle_consolidation.py`,
`test_session_progress.py`, `test_steering_mechanism.py`, `test_stop_detach.py`,
`test_venv_health.py`, `test_worker_guard.py`, `test_youtube_search.py`.

- [ ] `tests/unit/test_sdlc_tool_wrapper.py` (9 call sites: lines 69, 75, 81, 87, 101, 129, 172, 189, 230) — UPDATE: pass `env=subprocess_env(...)`; assertions unchanged.
- [ ] `tests/unit/test_sdlc_meta_set.py` — UPDATE: add `env=subprocess_env(project_root=str(REPO_ROOT))`.
- [ ] `tests/unit/test_sdlc_session_ensure.py` — UPDATE: same.
- [ ] `tests/unit/test_sdlc_stage_marker.py` — UPDATE: same; the `clean_env` at :1179 rebases on `subprocess_env()` keeping its strips.
- [ ] `tests/unit/test_sdlc_stage_query.py` — UPDATE: same; the three `clean_env` sites (:444, :603, :624) set no `REDIS_URL` today and are live db0 writers.
- [ ] `tests/unit/test_sdlc_dispatch.py::_isolated_subprocess_env` (line 272, drives :380, :424, :467, :496) — REPLACE: `subprocess_env()` plus pops of `VALOR_SESSION_ID`, `AGENT_SESSION_ID`, `active_run_id`.
- [ ] `tests/unit/test_memory_search_cli.py` (2 sites) — UPDATE: same.
- [ ] `tests/unit/test_session_telemetry.py` (1 site) — UPDATE: same.
- [ ] `tests/unit/test_evaluate_build.py` (3 sites) — UPDATE: same, via the file's existing `_run` helper.
- [ ] `tests/unit/test_worker_entry.py` (2 sites) — UPDATE: same.
- [ ] `tests/unit/test_worker_supervisor.py` (1 site) — UPDATE: same.
- [ ] `tests/unit/test_migrate_strip_pid_fields.py` (1 site) — UPDATE: same.
- [ ] `tests/unit/test_session_lifecycle.py::_subprocess_env` — REPLACE: helper body becomes `subprocess_env()` + the existing `SDLC_HOLDER_TOKEN` pop.
- [ ] `tests/integration/test_sdlc_dispatch.py` (2 env-less sites, :321 and :344) — UPDATE: add `env=subprocess_env(...)`.
- [ ] `tests/integration/test_design_system_pipeline.py` — UPDATE: same.
- [ ] `tests/integration/test_session_telemetry_e2e.py` — UPDATE: `project_root=str(WORKTREE)`.
- [ ] `tests/unit/test_subprocess_test_db_isolation.py` — CREATE: the AST guard, including its `ALLOWLIST` (5 `[#2628]` entries + the `[standalone-script]` class).
- [ ] `tests/unit/test_redis_flush_guard_prod.py` (:54, :555, :579, :648) and `tests/unit/test_conftest_isolation_guards.py` (:463) — NOT TOUCHED: real violations, allowlisted with a `[#2628]` reason because open PR #2683 owns both files.

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
- **Generalizing the hand-rolled env helpers into one shared utility.** Group C
  rebases the two that re-derive a db number and Group B rebases the `clean_env`
  dicts, each keeping its own strips because the strip sets differ and are
  load-bearing per test. Collapsing them into a single parameterized helper is a
  refactor with its own review surface; not this plan.

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
the guard against unmodified `main` and confirm it reports **on the order of 100+
sites** (measured ground truth on `90c0e81e4`: 113 violations across 54 files,
after `SKIP_ARGV0={'git'}` and one-hop delegator resolution; see Appetite). Read
that as a floor, not a target: a run that reports ~30 means the predicate was
narrowed, not that the suite is nearly clean;
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

- [SEPARATE-SLUG #2628] `tests/unit/test_youtube_search.py:219` is a real
  convert-class violation, but PR #2683 deletes a trailing class starting at
  `:226` in that file — close enough that both edits land in one merge hunk.
  Allowlist it with a `[#2628]` reason and convert it when #2683 lands. (Checked
  against #2683's diff at 2026-08-13T08:30Z; the PR is still OPEN.)
- [SEPARATE-SLUG #2628] `tests/README.md` is in #2683's diff as well, so the
  Documentation fallback below competes with it. If #2683 has not merged, append
  the subprocess subsection as a new trailing section rather than editing an
  existing one, to keep the merge a clean append.
- [SEPARATE-SLUG #2628] #2683 introduces `tests/db_derivation_guard.py`. The
  guard in this plan must not restate what that module enforces — it covers a
  different seam (child process env, not in-process db derivation). Re-read it
  before writing the guard if #2683 has landed by then.
- [SEPARATE-SLUG #2628] Any modification to `tests/conftest.py`,
  `tests/db_claim.py`, `tests/unit/test_conftest_isolation_guards.py`, or
  `tests/unit/test_redis_flush_guard_prod.py`. The last two contain five real
  violations (`:54,555,579,648` and `:463`); they are handled by `[#2628]`
  `ALLOWLIST` entries in the new guard file, which keeps this No-Go and a green
  guard simultaneously satisfiable. All four are in open PR #2683's
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

- [ ] Zero subprocess call sites in `tests/` launch a Python interpreter or repo
  entry point without `env=subprocess_env(...)`, except entries in the guard's
  commented `ALLOWLIST`.
- [ ] `tests/unit/test_subprocess_test_db_isolation.py` exists, its self-test
  proves a `_my_subprocess_env()`-shaped value is rejected, and its red-state
  output against pre-fix `main` is pasted in the PR description.
- [ ] `tests/unit/test_sdlc_tool_wrapper.py` passes in full.
- [ ] The full set of touched test files passes.
- [ ] No file owned by PR #2683 appears in this PR's diff.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] *(post-merge — tracked on #2763, not a merge gate)* The `PipelineLedger` row
  for issue `999999` is deleted from db0 via the ORM and its absence confirmed by
  an ORM read.

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
  only channel), the Appetite figures (766 files scanned, 113 violations across 54 files under the
  corrected argv-gated predicate; the round-1 `cwd=`-based recon is superseded)
- **Assigned To**: isolation-guard-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `tests/unit/test_subprocess_test_db_isolation.py` with an `ast`-based
  scan of `tests/**/*.py`.
- Predicate: `subprocess.{run,Popen,check_output,check_call,call}` whose first
  positional arg mentions a Python interpreter or repo entry point
  (`sys.executable`, a `PYTHON`-containing name, a `"-m"` element, `WRAPPER`, a
  `scripts/` string). **`cwd=` is not part of the predicate.**
- Violation when `env=` is absent, or when its value is not an `ast.Call` to the
  exact name `subprocess_env` / `<mod>.subprocess_env` backed by a
  `tests.db_claim` import in that module.
- Exemptions only via `SKIP_ARGV0 = {"git"}` and a commented `ALLOWLIST` of
  `path:line` entries.
- Assert scanned-file count > 0 before asserting zero violations.
- Failure message lists every `path:line` and states the remedy.
- Include the self-test: a synthetic `env=_my_subprocess_env()` snippet MUST be
  reported as a violation.
- Run it against unmodified `main`, confirm it reports the known sites, and save
  that output verbatim for the PR description.

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
- Lines 69, 75, 81: `env=subprocess_env()` (they invoke `WRAPPER`, so the
  argv-gated predicate matches them; see Group A).
- Run the file: `scripts/pytest-clean.sh tests/unit/test_sdlc_tool_wrapper.py`.

### 3. Sweep the remaining audited call sites
- **Task ID**: build-sweep
- **Depends On**: build-wrapper-file
- **Validates**: the files listed in Test Impact Group B (the guard's red-state
  diff is the real gate, not this enumeration), plus
  tests/unit/test_session_lifecycle.py
- **Assigned To**: subprocess-env-builder
- **Agent Type**: builder
- **Parallel**: false
- **Work from the guard's red-state list, not from the Group B enumeration.** The
  enumeration is a stale snapshot that undercounts. Acceptance step: diff the
  guard's red-state `path:line` output against what was converted; every line is
  either converted or added to the guard's commented `ALLOWLIST`. Task 3 is not
  done until the guard is green *and* that diff is empty.
- Convert each site, threading extra variables through `subprocess_env(**extra)`
  rather than hand-built dicts.
- `test_session_telemetry_e2e.py`: `project_root=str(WORKTREE)`.
- Rebase `tests/unit/test_sdlc_dispatch.py::_isolated_subprocess_env` on
  `subprocess_env()` **followed by pops of `VALOR_SESSION_ID`,
  `AGENT_SESSION_ID`, and `active_run_id`** (the #2144 seam), and
  `test_session_lifecycle.py::_subprocess_env` on `subprocess_env()` preserving
  its `SDLC_HOLDER_TOKEN` pop.
- Rebase the `clean_env` dicts in `test_sdlc_stage_query.py` (:444, :603, :624)
  and `test_sdlc_stage_marker.py` (:1179) the same way, keeping their strips.
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

### 7. POST-MERGE (not a pipeline gate): delete the db0 reproduction row
- **Task ID**: cleanup-prod-residue
- **Depends On**: none — this task runs after the PR merges and is deliberately
  outside the dependency graph, so no validator sees an unsatisfiable edge.
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
| No bare `os.environ.copy()` env in the target file | `! grep -q "os.environ.copy()" tests/unit/test_sdlc_tool_wrapper.py` | exit code 0 (`grep -c` would exit 1 on the *passing* state) |
| Guard self-test rejects the substring shape | `scripts/pytest-clean.sh tests/unit/test_subprocess_test_db_isolation.py -q -k self_test` | exit code 0 |
| Every wrapper-file subprocess with a repo cwd has an env | `python -c "import ast;t=ast.parse(open('tests/unit/test_sdlc_tool_wrapper.py').read());print(sum(1 for n in ast.walk(t) if isinstance(n,ast.Call) and ast.unparse(n.func).startswith('subprocess.') and any(k.arg=='cwd' for k in n.keywords) and not any(k.arg=='env' for k in n.keywords)))"` | output contains 0 |
| Anti-criterion: PR does not touch #2683-owned files | `git diff --name-only main -- tests/conftest.py tests/db_claim.py tests/unit/test_conftest_isolation_guards.py tests/unit/test_redis_flush_guard_prod.py \| wc -l` | output contains 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness, Scope & Value, History & Consistency (3/3) | The No-Gos forbid touching `tests/unit/test_redis_flush_guard_prod.py` and `tests/unit/test_conftest_isolation_guards.py` (owned by open PR #2683), but an empirical run of the plan's own argv-gated predicate reports 5 live violations inside exactly those files: `test_redis_flush_guard_prod.py:54,555,579,648` and `test_conftest_isolation_guards.py:463`. No task allocates ALLOWLIST entries for them, so Task 4's "guard now green" gate, the Verification row "Isolation guard passes / exit 0", and the Success Criterion "no file owned by PR #2683 appears in this PR's diff" are mutually unsatisfiable as written. | Group D allowlist rule 1; Test Impact NOT-TOUCHED row | Add the five `path:line` entries to `ALLOWLIST` in the NEW guard file `tests/unit/test_subprocess_test_db_isolation.py` only — this edits no #2683-owned file, so the anti-criterion still holds. Each entry carries the comment `# owned by open PR #2683 (#2628) — No-Go here, fix there`. Make it a Task 1 acceptance sub-bullet so the conflict is resolved on the guard's first red/green cycle, and have Task 4 assert those specific lines are present in `ALLOWLIST` rather than merely that the guard passes. |
| BLOCKER | Scope & Value, Risk & Robustness (elevated per cross-validation) | Appetite (`Small`, "PM check-ins: 0", "Review rounds: 1"), the 16-row Test Impact table, and Risk 4's "confirm it reports the ~30 known sites" are all sized against the stale recon snapshot. Running the plan's own corrected argv-gated predicate over `tests/` scans 766 files and reports **147 violations across ~60 files**. Whole suites appear nowhere in Group A/B/C or the task list: `tests/integration/test_agent_session_scheduler.py` alone contributes 23 sites, plus `test_youtube_search.py:219`, `test_worker_guard.py:52`, `test_venv_health.py:66`, `test_steering_mechanism.py` (3), `test_validate_no_uv_sync_in_worktree.py` (5), `test_pre_commit_hook.py` (3), `tests/e2e/test_telegram_flow.py:38`, and dozens more. Task 3 defers to the red-state list, but no gate re-derives appetite or team sizing from it. | Appetite re-derived to Medium (70 convert / 44 allowlist); Test Impact preamble; Risk 4 | Re-derive scope from the measured red state before build starts, not mid-sweep. Either (a) split: keep Groups A/B/C in #2763 and open a follow-up issue for the ~44 newly-surfaced files, with the guard shipping ALLOWLIST-suppressed for the deferred set; or (b) re-declare `appetite: Medium`/`Large` with more than one review round. Update Risk 4's mitigation to expect a red-state count of order 100+, not "~30", so a builder does not stop early believing Group B was near-complete. |
| BLOCKER | Structural (cross-reference check) | The plan contradicts itself on `test_sdlc_tool_wrapper.py` lines 69/75/81. Technical Approach Group A says "Convert them too: `env=subprocess_env()`. This is cheaper and more honest than three exemption entries". Task 2's bullet list says "Leave lines 69/75/81 alone." Test Impact lists that file as "6 call sites: lines 87, 101, 129, 172, 189, 230" — excluding them. The ground-truth run confirms all three are `NOENV` violations under the argv predicate, so following Task 2 leaves the guard red in the very file the issue was filed against. | Task 2 bullet; Test Impact row (9 sites) | Task 2's builder follows the task list, not the prose. Delete "Leave lines 69/75/81 alone." from Task 2 and replace it with `Lines 69, 75, 81: env=subprocess_env()`, and change the Test Impact row to "9 call sites: lines 69, 75, 81, 87, 101, 129, 172, 189, 230". These three are `subprocess.run([str(WRAPPER)], ...)` and `[str(WRAPPER), "--help"]` with no `cwd` and no `env` — bare `subprocess_env()` with no `project_root` is correct; do not add `AI_REPO_ROOT`, which would break `test_wrapper_no_args_exits_2`'s usage-path assertion. |
| CONCERN | Structural (cross-reference check) | Group D bullet 3 rejects any `env=` that is not literally a call to `subprocess_env`, naming `_isolated_subprocess_env` / `_subprocess_env` / `clean_env` as violations. But two *correct* thin delegating wrappers already exist whose entire body is `return subprocess_env(...)`: `tests/integration/test_agent_session_scheduler.py:25-37` and `tests/integration/test_bot_await_reply.py:33-43`. The predicate flags ~25 already-correct call sites, and Group C's claim that there are "Two helpers" that re-derive the db is wrong — there are four helpers, two broken and two already fixed by PR #2606. | Group D delegator bullet; Group C four-helper count | The anti-substring hardening from round 1 overshot. Resolve module-local helper names one hop: when `env=` is a `Call` to a bare `Name` defined in the same module, parse that function and accept it if its body's only `return` is a call to `subprocess_env`; otherwise it is a violation. This keeps `_isolated_subprocess_env` (which reads `POPOTO_REDIS_DB.connection_pool.connection_kwargs`) rejected while accepting the two legitimate delegators, and avoids a 25-entry ALLOWLIST that would read as noise. Add a second self-test asserting a synthetic `def _w(**k): return subprocess_env(**k)` delegator is ACCEPTED, paired with the existing one asserting `_my_subprocess_env()` is rejected. Also correct Group C's "Two helpers" count. |
| CONCERN (r2, superseded) | History & Consistency | Round 2 flagged `_isolated_subprocess_env`'s driven call-site line numbers as stale and "corrected" them to the same values it was flagging, which then propagated a wrong set into the plan. | Round 3, verified by `grep -n` | The authoritative numbers are `:380, :424, :467, :496` (helper at `:272`). Round 2's suggested `:375, :409, :462, :491` was itself wrong; do not reintroduce it. |
| BLOCKER (r3) | Risk & Robustness, Scope & Value | The guard predicate accepted only a direct `subprocess_env(...)` call or a single-`return` delegator, but every fix the plan prescribes produces `env = subprocess_env(); env.pop(...)` — and Group B's rebased `clean_env` sites pass an `ast.Name`, not a call at all. The already-correct `test_bot_await_reply.py:33` helper has that same shape. As specified the guard could never go green. | Group D acceptance rule (c); third self-test; delegator bullet reconciled | Accept an `env=` `Name` bound by exactly one `Assign` from `subprocess_env(...)` and thereafter only `.pop(<literal>, None)` / `.update(...)`, never rebound; same rule inside a delegator ending `return <name>`. |
| CONCERN (r3) | History & Consistency | Line numbers for the four driven sites were wrong in all three places (Group C, Test Impact, Task 3). | Corrected to `:380, :424, :467, :496` in all six occurrences | Verified directly with `grep -n "_isolated_subprocess_env" tests/unit/test_sdlc_dispatch.py`, not from a critique report. |
| NIT (r3) | History & Consistency | Delegator call-site count said 23. | Corrected to 22 | `grep -c "env=_subprocess_env" tests/integration/test_agent_session_scheduler.py` → 22. |
| BLOCKER (r4) | Risk & Robustness, Structural (cross-reference check) | Group D's three acceptance rules all resolve either a direct call or a **bare `ast.Name` bound in the enclosing scope**. None covers `env=self._subprocess_env()` — which is exactly the shape Group C's own prescribed remedy produces at `tests/unit/test_session_lifecycle.py:1614`, because that helper is a `@staticmethod` inside a class (`def _subprocess_env` at `:1602`), not a module-level function. `self._subprocess_env` is `ast.Attribute(value=Name('self'), attr='_subprocess_env')`; it fails the direct-call clause (which accepts only an attribute *ending in* `.subprocess_env`, and this ends in `._subprocess_env`) and it is not a bare Name, so one-hop delegator resolution never fires. A builder implementing Group D literally leaves the guard **red at the very file Group C claims to fix**, making Task 3's acceptance gate ("guard green *and* that diff is empty") unsatisfiable. This is the same defect class rounds 1-3 each closed for the module-level cases only. | Group D method-delegator clause; fourth self-test | Add a fourth acceptance clause: when `env=` is an `ast.Call` whose `func` is an `ast.Attribute` whose `.value` is `ast.Name(id="self")` (or `"cls"`), walk up to the nearest enclosing `ast.ClassDef`, find the `FunctionDef` named `func.attr` in its body (unwrapping `@staticmethod`/`@classmethod`), and run the SAME single-`return`/assign-mutate-return body check used for bare-Name delegators. Add a **fourth** self-test to the mandatory set: a class containing `@staticmethod def _subprocess_env(): e = subprocess_env(); e.pop("X", None); return e` invoked as `env=self._subprocess_env()` must be ACCEPTED. Note the resolution is still exactly one hop and must not recurse. |
| BLOCKER (r4) | Scope & Value | Round 3's accepted remedy said verbatim: "Update Risk 4's mitigation to expect a red-state count of order 100+, not '~30', so a builder does not stop early believing Group B was near-complete" — and the r3 row's "Addressed By" column names Risk 4. **It was never applied.** Risk 4's Mitigation still reads "confirm it reports the ~30 known sites". A builder running Task 1's red-state capture against a ground truth of 113-114 violations will read a 4x overshoot as a broken predicate and narrow it, or will stop the Task 3 sweep at ~30 conversions believing Group B is complete. An accepted BLOCKER remedy recorded as landed but absent from the body is the precise regression this table exists to prevent. | Risk 4 mitigation restated as a 100+ floor | In `## Risks` → Risk 4 → **Mitigation**, replace the clause "confirm it reports the ~30 known sites" with "confirm it reports on the order of 100+ sites (measured ground truth on `90c0e81e4`: **113 violations across 54 files** after `SKIP_ARGV0={'git'}` and one-hop delegator resolution; see Appetite)". State it as a floor, not a fixed count, so it cannot go stale again. Do this edit before Task 1 runs — Task 1 is the first task that reads Risk 4. |
| CONCERN (r4) | Scope & Value | Two round-1-vintage recon figures survive and now contradict the plan's own re-derived numbers: Task 1's **Informed By** says "recon AST scan (115 raw hits → ~33 Python-against-checkout hits)", and Group D says `SKIP_ARGV0 = {"git"}` covers "85 of the 115 raw hits". Both describe the superseded `cwd=`-spelling predicate. Under the corrected argv-gated predicate the raw count is 147 across 62 files and only **10** sites have `argv0='git'` (across 6 files), so "85 of 115" is wrong by nearly an order of magnitude in the one bullet that justifies the guard's only blanket exclusion. | Task 1 Informed By; Group D SKIP_ARGV0 bullet (10 sites / 6 files) + next_skill entry | Replace Task 1's Informed By count with a pointer to the Appetite figures rather than an independent number: "recon superseded — see Appetite (766 files scanned, 113 violations / 54 files under the corrected argv-gated predicate)". In Group D, replace "(85 of the 115 raw hits — no Popoto import, no isolation value)" with "(10 sites across 6 files under the argv-gated predicate: `test_pm_briefings_e2e.py`, `test_cross_repo_build.py`, `test_merge_stage_slug_reuse.py`, `test_build_validation.py`, `test_pre_commit_hook.py`, `test_session_isolation_bypass.py` — no Popoto import, no isolation value)". Note `tests/unit/test_sdlc_next_skill.py:209` has a starred argv0 (`*env_git`) that `SKIP_ARGV0` cannot resolve statically, so it needs an explicit `[standalone-script]` ALLOWLIST entry rather than relying on the git skip. |
| CONCERN (r4) | Structural (cross-reference check) | The guard walks `tests/**/*.py` and the guard file itself lives at `tests/unit/test_subprocess_test_db_isolation.py` — inside its own scan root. Its mandatory self-tests are described only as "a synthetic `env=_my_subprocess_env()` snippet" without stating that the fixtures are parsed as **source strings**. If a builder writes them as live module-level code (the natural reading of "self-test"), the guard reports its own fixtures as violations on the first green run, and the obvious "fix" is to allowlist them — which silently weakens the predicate's own proof. | Group D fixtures-as-source-strings bullet; guard path excluded from the walk | State explicitly in Group D that each self-test builds its fixture as a Python **string literal** passed to `ast.parse(...)` and asserts against the scanner's pure `scan_tree(tree, path)` function; the fixtures must never exist as importable code inside the scan root. Alternatively, have the scan skip its own `__file__`, but the string-fixture form is strictly better because it also proves the scanner is callable on an arbitrary tree. Corollary for the *rejected*-shape self-test: `_my_subprocess_env` must not appear as real code anywhere under `tests/`. |
| NIT (r4) | History & Consistency (raised as BLOCKER, refuted on ground truth) | Critic claimed the `_isolated_subprocess_env` driven sites are at `:375, :409, :462, :491`, not `:380, :424, :467, :496`. Both are correct for different referents: `375` etc. are the `subprocess.run(` call-start lines; `380` etc. are the `env=_isolated_subprocess_env(),` kwarg lines (verified by direct read). The plan is not wrong, but it uses **call-start** lines for `test_sdlc_tool_wrapper.py` (`172, 189, 230`) and **kwarg** lines for `test_sdlc_dispatch.py`, which is what keeps re-triggering this flip every round. | Appetite line-number convention note; Group C fifth-helper note; 57-vs-54 overlap explained | Do NOT change the values. Add a one-line convention note at the top of Group C: "Line numbers in this plan name the `subprocess.run(` call-start line, except the four `_isolated_subprocess_env` sites (`:380, :424, :467, :496`) which name the `env=` kwarg line; call-start for those is `:375, :409, :462, :491`." Recording both sets kills the round-over-round flip. |
| NIT (r4) | History & Consistency (raised as BLOCKER, refuted on ground truth) | Critic read the SOURCE_FILES scan header (`violations=147 files=62`) as contradicting Appetite's "114 violations across 54 files". That scan was published with delegator resolution and the `git` skip **disabled**; applying both yields **113 violations / 54 files**, confirming Appetite within one. The genuine defect is smaller: Appetite's own sub-counts do not add up — 32 convert-files + 25 allowlist-files = 57, not the stated 54. | Appetite: 57-vs-54 overlap explained; counts reconciled at 113-114 / 54 | Reconcile the file arithmetic in `## Appetite`: either state the overlap explicitly ("3 files contain both convert and allowlist sites") or restate as "54 files total, of which 32 contain at least one site that must be converted". Also change "114 violations" to "113" to match the measured value, or mark it "~113" — the one-site delta is `test_sdlc_next_skill.py:209`'s unresolvable starred argv0. |
| NIT (r4) | History & Consistency | Group C opens "Four module-local env helpers exist in the suite" — an unqualified suite-wide claim contradicted by the plan's own No-Gos, which name a fifth (`_subprocess_env` in `tests/unit/test_redis_flush_guard_prod.py`, live at `:555`). It is out of scope, which is not the same as not existing. | Group C reworded to 'in scope here' + fifth-helper note | Reword to "Four module-local env helpers exist in the suite *that this plan touches* (a fifth, in `tests/unit/test_redis_flush_guard_prod.py`, is out of scope — see No-Gos and its `[#2628]` ALLOWLIST entry)." Wording only; no task or code change. |
| NIT (r4) | Scope & Value | Task 3's **Validates** says "the 12 files listed in Test Impact Group B"; the enumeration lists 14. | Task 3 Validates de-numbered | Change to "the 14 files listed in Test Impact Group B", or drop the number entirely — Task 3's real gate is the guard's red-state diff, not this count. |
---

## Open Questions

None of these block the build: each carries a stated default position that the
plan already builds against. They are recorded for the critique round and for a
supervisor who wants to overrule a default.

1. **Guard placement.** The guard is proposed as a pytest test. Would you rather
   it be a `PreToolUse` hook validator so the shape is rejected at authoring
   time rather than at test time? (Plan's position: test — see Rabbit Holes.)
2. **Sweep breadth.** The plan converts the 70 sites whose child can import repo
   code, allowlists the 44 standalone-script children and the `git`-in-`tmp_path`
   sites, and keeps it all in one issue at Medium appetite. Is that the right
   line, or do you want the 44 split into a follow-up issue?
3. **Doc destination.** `docs/features/test-db-ownership.md` does not exist on
   main yet — it arrives with PR #2683. Confirm the fallback (write to
   `tests/README.md` and fold in later) rather than creating a second doc.
