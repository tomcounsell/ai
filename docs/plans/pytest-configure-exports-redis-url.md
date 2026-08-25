---
status: Ready
type: bug
appetite: Small
owner: valor
created: 2026-08-24
tracking: https://github.com/tomcounsell/ai/issues/2805
last_comment_id: 5392635119
revision_applied: true
revision_applied_at: 2026-08-24T10:25:13Z
---

# Pytest exports its own REDIS_URL; the line-keyed ALLOWLIST guard is deleted

## Problem

A pytest process on this machine claims a private Redis test db from the pool
`[1..15]`, then forgets to tell its own environment. `pytest_configure`
(`tests/conftest.py:274-294`) exports `POPOTO_TEST_DB` at line 289 and stops
there. `REDIS_URL` keeps whatever the ambient shell had — on every machine in
this fleet, `redis://localhost:6379/0`, which is **live production**.

So every child process a test forks re-resolves `REDIS_URL` at popoto import
time and lands on production db0, unless the author remembered to pass
`env=subprocess_env(...)`. Roughly forty call sites have to remember. Authors
forget, so `tests/unit/test_subprocess_test_db_isolation.py` exists: 688 lines
of AST scanner over all of `tests/`. The scanner has unavoidable false
positives, so it carries a 33-entry `ALLOWLIST`. The allowlist is keyed by
**`path:line`**, so any unrelated merge that shifts a line silently un-exempts
a site — and, worse, silently exempts whatever call happens to occupy that line
afterward. It is fail-open and fail-closed at the same time.

**Current behavior:**

The guard is red on `main` at `4ff2338dc` with four violations, reproduced
during planning:

```
tests/unit/test_conftest_isolation_guards.py:488 — no env= (child inherits ambient REDIS_URL)
tests/unit/test_sdlc_next_skill.py:215 — no env= (child inherits ambient REDIS_URL)
tests/unit/test_watchdog_log_isolation.py:48 — env=_subprocess_env() does not resolve to subprocess_env()
tests/unit/test_watchdog_log_isolation.py:606 — env=_subprocess_env() does not resolve to subprocess_env()
```

Two are pure line drift from merges that never touched the call sites. Two are
new, introduced by `f7a1081b1` (#2827) — someone hand-built
`{**os.environ, "PYTHONPATH": REPO_ROOT}` because they did not know the
convention. That is the failure mode restating itself.

**Desired outcome:**

The pytest process's environment is correct, so a child inherits the claimed db
**by construction**. `subprocess_env` survives as the opt-in `PYTHONPATH`
pinner it also is. The AST scanner, its allowlist, its two reason classes, its
`SKIP_ARGV0` set, its `-m` heuristic, and the entire line-drift failure mode are
deleted. No call site in the repo needs to change for the four current
violations to become correct.

## Freshness Check

**Baseline commit:** `4ff2338dc`
**Issue filed at:** 2026-08-13T20:21:07Z (reframed 2026-08-24T08:29Z)
**Disposition:** Minor drift

**File:line references re-verified:**

- `tests/conftest.py:289` — `os.environ["POPOTO_TEST_DB"] = str(db)`, no `REDIS_URL` sibling — **still holds**, verified by read of lines 274-294.
- `tests/db_claim.py:312` — module-level `redis_test_url(host="127.0.0.1")` — **still holds**.
- `tests/db_claim.py:353` — `subprocess_env` calls `redis_test_url()` — **still holds**.
- `tests/db_claim.py:342-348` — the `POPOTO_TEST_DB`-is-not-inherited docstring (#2628) — **still holds**.
- The four violation lines (488 / 215 / 48 / 606) — **still exact**; the guard reproduces the identical four-line failure at `4ff2338dc`.
- `tests/unit/test_subprocess_test_db_isolation.py` — 688 lines, `ALLOWLIST` at 116-176 with 33 entries — **still holds**.

**Cited sibling issues/PRs re-checked:**

- #2763 — CLOSED 2026-08-13 via PR #2786. Introduced `subprocess_env`. This plan does not undo it; it removes the *enforcement* layer and keeps the helper.
- #2628 — CLOSED 2026-08-13 via PR #2683. Introduced the per-process claim and the `POPOTO_TEST_DB`-not-inherited rule. The rule survives unchanged and must be demonstrated, not assumed.
- #2683 — MERGED. Six `ALLOWLIST` entries name it as their expiry condition; it landed on 2026-08-13 and the sites were never converted. Deleting the allowlist retires the debt.
- #2792 — MERGED. One of the two line-shifting commits. Irrelevant to the root cause.
- #2605 / #2606 / #2645 — CLOSED. Cross-process db sharing, shared-state leaks, and the ambient flush guard respectively. All remain in force; none is weakened here.
- #2904 — CLOSED 2026-08-24. Its plan explicitly scopes this row out and delegates it here.

**Commits on main since issue was filed (touching referenced files):**

- `f7a1081b1` "Watchdog logging configures at the entry point, not at import (#2643) (#2827)" — **changed the symptom set, not the root cause.** It added the two `test_watchdog_log_isolation.py` violations. It is the single strongest argument for this plan: the convention was reintroduced-as-violation by an unrelated, well-executed change.

No other commit has touched `tests/conftest.py`, `tests/db_claim.py`,
`tests/unit/test_subprocess_test_db_isolation.py`, or
`tests/unit/test_conftest_isolation_guards.py` since the filing.

**Active plans in `docs/plans/` overlapping this area:**

1. `docs/plans/fix-red-main-unit-tests.md` (issue #2904, `status: docs_complete`,
   CLOSED 2026-08-24). It lists this exact test as row #3 of a red-main umbrella
   and scopes it **out**: "owned by #2805; do not re-diagnose or touch it here."
   Coordination is clean; no contention, no shared files.

2. **`docs/plans/overclaim-guard-greps-whole-worktree.md` (issue #2807,
   `status: Ready`, `revision_applied: true`) — an ACTIVE plan the round-1 sweep
   missed, which is why its conclusion "no contention" was overstated.** Its
   Test Impact carries, at line 813:

   > `tests/unit/test_subprocess_test_db_isolation.py` — **NO CHANGE, verified**:
   > its scanner only flags subprocess calls where `_argv_reaches_python(argv)`
   > holds. `["git", "grep", …]` does not reach Python, so the new helper needs
   > no `ALLOWLIST` entry.

   That row reasons about a scanner **this plan deletes in full**, so whichever
   lane lands second carries a stale row. The files themselves do not collide:
   #2807 asserts *no change* to the file, so there is no merge conflict in either
   order — the cost is a builder wasting time re-verifying `_argv0_is_skipped`
   and `_argv_reaches_python` in a file that no longer exists.

   **Resolution: #2805 lands first, and #2807's line-813 row becomes moot rather
   than wrong.** If #2807 lands first, nothing about this plan changes — its row
   was a no-op assertion. Task 7 posts a comment on issue #2807 recording this so
   its builder skips the re-verification; this lane does **not** edit #2807's plan
   document.

**Notes:** Two of the issue body's four stated Consequences are factually wrong
and are corrected in Technical Approach below. Two breakages the body does not
mention are the plan's hard blockers: the direct-`pytest_configure()` poisoning
(Risk 1) and the in-process migration of twenty production `REDIS_URL`
consumers off db0 (Risk 7, found during the critique revision — see spike-3).

## Prior Art

- **PR #2786 / Issue #2763** — "Make test subprocesses inherit the parent's claimed test DB". Built `subprocess_env` and converted ~40 call sites. **Partially succeeded**: the helper is correct, but the enforcement is a convention, and a convention needs a policeman. Directly upstream of this plan; `subprocess_env` is kept.
- **PR #2683 / Issue #2628** — "Enforce test-DB ownership so the unit suite stops rotating". Built the flock claim, the `pytest_configure` hook, and the runtime flush guard permitting `flushdb()` only against `claimed_test_dbs()`. **Succeeded and stays.** This plan adds one line to the hook it created.
- **PR #2680 / Issue #2645** — "Harden production Redis against accidental flush (four layers)". Built `tools/redis_flush_guard.py`, the ambient interpreter-scope guard that raises on any db0 flush in any venv process. **Succeeded and stays.** It is the runtime backstop the AST guard was a static proxy for. Verified during recon: it contains no `REDIS_URL` reference at all — it reads `_db_of(client)` off an already-constructed client, so it is orthogonal to this change.
- **Issue #1897** — "Test-isolation flakes under xdist" (umbrella). The ancestor of the whole line. Its cross-process family is what the claim machinery closed.
- **Issue #2904 / `fix-red-main-unit-tests.md`** — red-main umbrella that delegated this row here.

## Research

**Queries used:**

- `pytest_configure os.environ mutation xdist worker inherits environment subprocess`

**Key findings:**

- Each pytest-xdist **worker** is a separate process and runs `pytest_configure`
  itself, so a mutation there is per-worker, not shared. This is exactly the
  property the design needs: worker `gw3` exports *its own* claimed db, not the
  controller's. Source:
  [pytest-xdist how-to](https://pytest-xdist.readthedocs.io/en/stable/how-to.html).
  Confirms that the single line is correct under `-n N` without any worker-id
  special-casing, and that the controller's early return at `conftest.py:282`
  (it runs no tests) is the right behavior rather than a gap.
- `monkeypatch` is **function-scoped and unavailable inside `pytest_configure`**;
  session-wide env setup in that hook is necessarily a raw, process-lifetime
  `os.environ` assignment. Source:
  [monkeypatch env restoration](https://qaskills.sh/blog/pytest-monkeypatch-environment-variable-restoration).
  This is fine for the real hook (it runs once, before collection, before any
  test can spawn a child) and is precisely why tests that **invoke the hook
  directly** are the blast radius — see Risk 1.
- Children inherit the parent's `os.environ` **at spawn time**, so mutating in
  `pytest_configure` (before collection) rather than mid-test is the ordering
  that makes inheritance deterministic. Confirms the placement at line 289+1.

Sources:
- [pytest-xdist how-to documentation](https://pytest-xdist.readthedocs.io/en/stable/how-to.html)
- [Restore Environment Variables with pytest monkeypatch](https://qaskills.sh/blog/pytest-monkeypatch-environment-variable-restoration)

## Spike Results

### spike-1: A process-wide `REDIS_URL` export makes an unguarded child land on the claimed db

- **Assumption**: "Adding `os.environ["REDIS_URL"] = redis_test_url()` to `pytest_configure` makes a plain `subprocess.run([sys.executable, "-c", ...])` with no `env=` resolve to this process's claimed db, under both serial and `-n N` runs; a nested pytest child still claims its own slot; and `tests/unit/test_conftest_isolation_guards.py`'s direct `pytest_configure()` calls poison the live session env."
- **Method**: prototype (isolated worktree, reverted clean)
- **Finding**: **Confirmed on every axis, with two additions the design must absorb.**
  - **A. Baseline red, measured.** Parent claimed db1; unguarded child printed `redis://localhost:6379/0`. Production.
  - **B. Green with the fix.** Same probe: child printed `redis://127.0.0.1:6379/1` — the parent's claimed db.
  - **C. popoto undisturbed.** `POPOTO_REDIS_DB.connection_pool.connection_kwargs` is byte-identical with and without the fix (`{'db': 1, 'host': 'localhost', ...}`). It is already on the claimed db via the existing `POPOTO_TEST_DB` export. The change is purely additive for subprocesses, exactly as spike-2's ordering analysis predicted.
  - **D. xdist per-worker isolation holds.** Under `-n 2 --dist=each` with claims `gw0=db1 gw1=db2`, each worker's child landed on that worker's own db. The **controller** (measured via a `pytest_sessionfinish` plugin) reported `REDIS_URL=redis://localhost:6379/0 POPOTO_TEST_DB=None` — the early return at `conftest.py:282` precedes both exports, so it writes no bogus URL. The controller runs no tests, so this is correct.
  - **E. Nested pytest holds (#2628).** Parent on db1 exported `.../1`; a nested `python -m pytest ... -n0` spawned with **no `env=`** reported `REDIS_URL=redis://127.0.0.1:6379/2 POPOTO_TEST_DB=2 claim=2`. The child's own `pytest_configure` overwrites the inherited value. No leak.
  - **F. Risk 1 is real and worse than static analysis showed.** `tests/unit/test_conftest_isolation_guards.py` **passes** (44 passed) — the leak is invisible from inside that file, because under `-n0` the tmp-registry stub claim also picks db1 and masks the divergence. Forcing divergence by running the guards file followed by the probe under `-n 2 --dist=each` (92 passed) exposes it: `gw1 parent_claim=2` but `parent_env=redis://127.0.0.1:6379/1`, **and its subprocess landed on db1 — a slot owned by gw0.** A green file is not evidence of safety here.
  - **G.** Worktree left clean; `git status --porcelain` and `git diff --stat` both empty.
- **Confidence**: high (measured, not reasoned)
- **Impact on plan**: Three concrete changes. (1) The import spelling hazard **dissolves**: `tests/conftest.py:18` already carries `from tests import db_claim`, so the line is `db_claim.redis_test_url()` with no new import and no fixture shadowing — Risk 5 is downgraded accordingly. (2) The `_reset_claim_state` mitigation is promoted from "recommended" to **required**, and its verification must run under `-n 2 --dist=each`, because `-n0` masks the bug. (3) A new decision surfaced: the fix flips the hostname from `localhost` to `127.0.0.1` — see Risk 6 and Open Question 4.

### spike-1 operational notes for the builder

- `scripts/pytest-clean.sh` defaults to xdist, which **swallows `-s`**. Pass `-n0` (or write probe output to a file) to see anything a probe prints.
- A nested-pytest target must live **under the repo root**. Put it in `/tmp` and pytest picks a different rootdir, never loads `tests/conftest.py`, and the child silently inherits the parent's db — a false green for assertion 3.
- Reproducing the Risk 1 leak requires `-n 2 --dist=each` **with the guards file and the probe in the same run**. Under `-n0` both the stub claim and the real claim land on db1 and the bug is invisible.

### spike-2: Blast radius of a globally-correct `REDIS_URL` across `tests/`

- **Assumption**: "The issue body's four Consequences are the complete set of things that break."
- **Method**: code-read (exhaustive sweep of every `REDIS_URL` mention in `tests/` and every subprocess-spawning test body in the named files)
- **Finding**: **The assumption is false in both directions.** Two stated consequences are non-existent, and one unstated breakage is the real blocker. Detail:
  1. `tests/unit/test_redis_flush_guard_prod.py` **has no db0 dependency.** All eight of its subprocess-spawning tests were read in full; every child body does import-time or attribute introspection only (`assert "redis" not in sys.modules`, reading `redis.Redis.flushdb._prod_flush_guarded`, `import tools`, `-X importtime -c pass`). None constructs a Redis client, imports popoto, or opens a socket. The file's own module docstring (lines 4-12) declares the *opposite* ground rule — it must never construct a db-0 client. The issue's proposed `env={**subprocess_env(), "REDIS_URL": ".../0"}` edit to five sites is unnecessary and would be actively misleading.
  2. **`tests/unit/test_conftest_isolation_guards.py` breaks.** Three tests call `_conftest.pytest_configure()` directly inside the live pytest process, after `_reset_claim_state(monkeypatch, tmp_path)` (lines 496-540) repoints the claim registry at an empty `tmp_path`. Against an empty registry the claim returns db 1, not this process's real claim. The new line is a bare `os.environ[...] =` assignment, so `monkeypatch` cannot undo it. See Risk 1.
  3. **`tests/unit/test_migrate_strip_pid_fields.py:411` becomes a tautology.** Its child's db is already pinned twice over; the new export is a third. See Test Impact.
  4. **`config/settings.py::RedisSettings` is not an env seam for `REDIS_URL`** — `env_nested_delimiter="__"` means it would need `REDIS__URL`. `tests/unit/test_redis_bootstrap.py` and `test_redis_bootstrap_username.py` are unaffected.
  5. **popoto's `pytest11` plugin imports `popoto.redis_db` before `tests/conftest.py` is imported**, so `POPOTO_REDIS_DB` is built off the ambient value and is unchanged by this fix. The export benefits **children only**; it is not defense-in-depth for the parent process. That is honest and sufficient, but must be stated rather than claimed otherwise.
  6. No test anywhere reads ambient `REDIS_URL` or does `monkeypatch.delenv("REDIS_URL", ...)`. All nine `monkeypatch.setenv("REDIS_URL", ...)` fixture sites already name the claimed db; none names a different one.
  7. No `xfail` markers exist anywhere in `tests/` — nothing to convert.
  8. `tests/unit/test_subprocess_test_db_isolation.py` is imported by **no other module**. Deleting it is self-contained.
- **Confidence**: high **within `tests/`; its conclusions do not extend past that
  boundary.** The sweep was scoped to `tests/` by construction, and the plan
  previously generalized finding 5 ("the export benefits children only") into a
  claim about the whole repo. That generalization was wrong. spike-3 re-runs the
  sweep over non-test code and supersedes it.
- **Impact on plan**: Removed Consequence 1 from scope entirely and added an
  anti-criterion asserting no db0 override is introduced. Promoted the
  `test_conftest_isolation_guards.py` poisoning to a hard blocker. Made the
  tautology an explicit disposition rather than a silent survivor.

### spike-3: Blast radius of a globally-correct `REDIS_URL` across **non-test** code

- **Assumption**: "The change is test-harness-internal; no production module's
  behavior changes under test." (This was the plan's implicit claim in
  Architectural Impact and Update System. It is **false**.)
- **Method**: code-read + one measurement, run during the revision pass
- **Finding**: **The assumption is false. This is the plan's largest blocker.**
  1. **Twenty lazy `REDIS_URL` call sites read the variable inside a function
     body** and therefore resolve it *after* `pytest_configure` runs, switching
     in-process from production db0 to the claimed test db.

     **Corrected at critique round 2 — the first sweep of this spike omitted the
     `agent/` package and reported fifteen.** The authoritative inventory,
     re-run at `2d10a7e92` over every non-test package:

     ```
     grep -rn 'os.environ.get("REDIS_URL"' --include='*.py' \
       bridge/ tools/ reflections/ ui/ agent/ config/
     ```

     → **20 hits**: `agent/output_handler.py:438`,
     `agent/session_completion.py:424`, `agent/session_completion.py:609`,
     `bridge/dedup.py:131`, `bridge/routing.py:1449`, `bridge/liveness.py:66`,
     `bridge/email_bridge.py:147` and `:830`, `bridge/telegram_relay.py:115`,
     `bridge/email_relay.py:71`, `bridge/email_dead_letter.py:35`,
     `tools/send_message.py:86`, `tools/react_with_emoji.py:65`,
     `tools/valor_email.py:65`, `tools/valor_telegram.py:725`,
     `tools/email_history/__init__.py:41`,
     `reflections/pm_briefings/delivery.py:78`, and `ui/app.py:410`, `:456`,
     `:652`. (`config/` contributes none — `RedisSettings` is a pydantic field,
     not an `os.environ` read; see finding 4 of spike-2.)

     **The three omitted `agent/` sites are the highest-consequence ones in the
     set**, which is why this was a BLOCKER and not a counting nit.
     `agent/session_completion.py:609` **writes**: `r.rpush` plus `r.expire` on
     `telegram:outbox:{session_id}`. Under test today those rows land in live
     production db0. `agent/session_completion.py:424` reads (`r.llen` on the
     same key) and `agent/output_handler.py:438` caches the URL into
     `self._redis_url` at `__init__` for a lazily-constructed client. Every one
     is the same shape:
     `os.environ.get("REDIS_URL", "redis://localhost:6379/0")` evaluated per
     call. Unlike popoto's `pytest11` plugin — which resolves at import, before
     `tests/conftest.py` loads, and is genuinely immune — these are read at call
     time and are **fully exposed** to the export.
  2. **Measured, not reasoned.** `redis-cli -n 0 dbsize` immediately before and
     after `./scripts/pytest-clean.sh tests/unit/test_dedup.py -p no:randomly -q -n0`
     on `main` at the revision baseline: **61505 → 61509**. Sixteen passing
     tests wrote **four net new keys into live production db0**. That is the
     status quo this export corrects, and it is the single best piece of
     evidence for the change. **This one `dbsize` reading is historical
     motivating evidence only, not the plan's verification method** — `dbsize`
     is signed and machine-global (see the Success Criteria's attributed
     key-set snapshot for why), so no later Verification row or measurement
     repeats this shape. The follow-on task 2.5 measurement and every
     Verification row use the attributed key-set snapshot instead.
  3. **Static import-reachability gives a loose upper bound, not the answer.**
     Seventy-three files under `tests/unit/` and `tests/integration/` import at
     least one of those modules and contain **no** `REDIS_URL` mention at all
     (so they pin nothing); 37 of them reference `session_completion` or
     `output_handler` specifically. That set is a superset: importing a module
     is not calling its lazy `_get_redis()`. **Do not enumerate the affected set
     statically — measure it.** The db0-delta probe in item 2 is the instrument;
     an import grep is not.

     **But the measurement cluster must span every consumer group, or the
     headline criterion is vacuous.** The round-1 cluster was drawn from the
     incomplete fifteen-site inventory and contained no `agent/`-facing test at
     all, so a zero db0 delta over it would have been recorded as "zero net
     production writes" while the writing path at
     `agent/session_completion.py:609` went unmeasured. The cluster is therefore
     extended with `tests/unit/output_handler/` and
     `tests/unit/test_deliver_pipeline_completion.py` — chosen because they
     exercise the outbox **write** paths, not because they merely import the
     module. Do **not** expand to all 37 referencing files; that repeats exactly
     the import-graph mistake this finding warns against. Pick the files that
     call the writing paths, and measure.
  4. The direction of the change is **correct in every case**: a test writing to
     production is a defect, and this export fixes it repo-wide as a side
     effect. The regression surface is not "tests now write to the wrong place"
     but "tests that silently depended on db0's *persistence* now run against a
     db the autouse `redis_test_db` fixture flushes per test." Expect
     key-visibility failures from cross-test key reuse, **not** import errors.
- **Confidence**: high, and now earned rather than asserted. The round-1
  spelling of this line claimed the module list was "exhaustive over tracked
  non-test Python" while the sweep had in fact omitted `agent/`. The corrected
  inventory in finding 1 is the output of a single grep whose package list is
  written out verbatim so a reader can re-run it and count, rather than a claim
  about a sweep nobody can reproduce. The db0 delta is measured.
- **Impact on plan**: Corrects Architectural Impact ("Data ownership: unchanged"
  → changed) and Update System ("test-harness-internal" → the code change is,
  the *blast radius* is not). Adds Risk 7, a Success Criterion stated as a db0
  delta rather than a proxy, two Verification rows, and a build task that runs
  the affected-suite sweep before and after the export.

## Data Flow

1. **Entry point**: `pytest` starts. The `pytest11` entry point loads
   `popoto.pytest_plugin`, which imports `popoto.redis_db`; that module reads
   ambient `REDIS_URL` and builds `POPOTO_REDIS_DB`. *(Unchanged by this plan.)*
2. **`tests/conftest.py` import**: stdlib + `pytest` + `tests.db_claim` only. No
   popoto, no `config.settings`.
3. **`pytest_configure`**: xdist controller early-returns (line 282, runs no
   tests). Every other process — serial master or xdist worker — calls
   `claim_test_db()`, exports `POPOTO_TEST_DB`, **and now exports `REDIS_URL`**.
   This is the one changed step.
4. **Collection and fixtures**: the autouse `redis_test_db` fixture swaps
   popoto's in-process client onto the claimed db, as today.
4b. **A test calls into a production module that resolves `REDIS_URL` lazily**
   (e.g. `bridge.dedup._get_redis()` at `dedup.py:131`, the outbox write at
   `agent/session_completion.py:609`, and eighteen further siblings) →
   the `os.environ.get("REDIS_URL", "redis://localhost:6379/0")` inside the
   function body now returns the **claimed db** instead of falling through to
   production db0. This is a **new** step in the flow, missed by the first draft.
   These reads happen at call time, after `pytest_configure`, so they are not
   protected by popoto's import-time immunity. See Risk 7.
5. **A test spawns a child**:
   - *with* `env=subprocess_env(...)` → `REDIS_URL` re-pinned to the same claimed
     db (unchanged), `POPOTO_TEST_DB` stripped, `PYTHONPATH` optionally pinned.
   - *without* `env=` → child inherits `os.environ`, which **now names the
     claimed db**. This is the fix.
6. **The child imports popoto** → resolves `REDIS_URL` → claimed db.
7. **A nested pytest child** → inherits the claimed `REDIS_URL`, then runs its
   *own* `pytest_configure`, claims its own free slot, and **overwrites both**
   `POPOTO_TEST_DB` and `REDIS_URL`. The #2628 invariant is self-correcting by
   the same mechanism that creates it.
8. **Output**: no child of a pytest process can reach db0 by omission. Reaching
   db0 becomes an act of deliberate spelling.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2786 (#2763) | Added `tests/db_claim.py::subprocess_env` and converted ~40 call sites to pass `env=subprocess_env(...)` | Correct helper, wrong enforcement altitude. It made isolation **opt-in per call site**, which means every future call site is unisolated until someone notices. |
| PR #2786 (#2763), same PR | Added the 688-line AST guard `test_subprocess_test_db_isolation.py` to police the convention | A static scan cannot see a child spawned any other way, and cannot see in-process code that builds its own client from `REDIS_URL`. It also needs exemptions, and exemptions need keys — the line-number key is unstable under any merge. |
| PR #2683 (#2628) | Added `pytest_configure`'s claim + `POPOTO_TEST_DB` export | Fixed the *plugin's* view of the db and stopped there. `REDIS_URL` — the variable every other consumer actually reads — was left pointing at production. This is the omission. |

**Root cause pattern:** every prior fix corrected a **consumer** of the wrong
environment instead of correcting the **environment**. `subprocess_env` fixes
children that remember to ask. The `redis_test_db` fixture fixes popoto's
in-process client. `POPOTO_TEST_DB` fixes popoto's plugin. Nobody fixed
`os.environ["REDIS_URL"]` itself, so each new consumer arrives unprotected and
needs its own hand-rolled correction — and a policeman to remember it.

## Architectural Impact

- **New dependencies**: none. `redis_test_url` already exists at
  `tests/db_claim.py:312` and is already imported into the same module graph.
- **Interface changes**: none. `subprocess_env`'s signature and behavior are
  unchanged; its `REDIS_URL` pin becomes redundant-but-harmless and its
  `project_root=` pin remains the reason to call it.
- **Coupling**: **decreases**. Removes a repo-wide static coupling between every
  `subprocess.*` call site in `tests/` and one 688-line meta-test.
- **Data ownership**: **changed, and this is the point.** `tests/db_claim.py`
  remains the sole source of the claimed db number, but the set of consumers
  that read it widens from "popoto's plugin plus children that opt in" to
  "every consumer in this process that resolves `REDIS_URL` lazily." That
  includes twenty call sites across sixteen **non-test** modules that call
  `os.environ.get("REDIS_URL", "redis://localhost:6379/0")` *inside a function
  body* — so they re-read the variable per call, after `pytest_configure` has
  run, and are **not** immune the way popoto's `pytest11` plugin is. Under test
  today those consumers write to production db0; after this change they write to
  the claimed test db. See Risk 7 — that migration is a **correction**, but it is
  a behavior change with a real regression surface, and the plan owns it rather
  than claiming it does not exist.
- **Reversibility**: trivial for the one-line export (revert one line). The
  guard deletion is a `git revert` away but should not be reverted piecemeal —
  the guard and the export are one decision.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirming the two dispositions in Open Questions)
- Review rounds: 1

The code change is one line, one helper edit, and three deletions. The cost is
almost entirely in *proving* the removal is safe — the demonstrated-red bar, the
nested-pytest invariant, and (added at revision) the db0-delta measurement that
bounds Risk 7's blast radius.

**Appetite is at risk from exactly one thing:** the size of the red set task 2.5
turns up when twenty production call sites stop writing to production db0. That
is a measurement, not a guess.

**Operator decision (2026-08-24): absorb whatever it takes, and simplify by
deleting. There is no split and no escalation gate.** Every test the export
turns red is dispositioned in this PR, however many there are, and the PR grows
past Small if it must. Splitting them out would leave known-broken isolation on
`main` under a follow-up issue while the thing that revealed it merges — the
same defer-the-consumer pattern this plan exists to retire.

**The disposition is delete-first, not repair-first.** Every red test here was
passing by reading and writing **live production Redis**; its green was an
artifact of shared production state, so the coverage it appeared to provide was
never real. When in doubt, delete it. Repair only where the test covers
behavior that is genuinely worth asserting and the repair is obvious — a
fixture pointed at the claimed db, a key the test should have been creating
itself. Do not invent scaffolding to rescue a test: a test that needs new
machinery to survive correct isolation is telling you it was testing the
machinery, not the behavior. Record each deletion with one line saying what it
claimed to cover and why that claim was hollow.

This supersedes the earlier split contract wherever it still appears below. The
"Affected suites still pass" Verification row holds at exit 0 at merge time,
unconditionally; nothing merges red.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Local Redis reachable | `redis-cli -h 127.0.0.1 -p 6379 ping` | The claim pool and every probe need a live server |
| Free test-db slots | `.venv/bin/python -c "from tests.db_claim import claim_test_db; print(claim_test_db())"` | The nested-pytest spike needs at least two free slots |
| On-pin venv | `.venv/bin/python -c "import sys; print(sys.version)"` | `scripts/pytest-clean.sh` aborts on an off-pin venv |

## Solution

### Key Elements

- **The export**: `pytest_configure` publishes the claimed db to `REDIS_URL`, in
  the same breath as `POPOTO_TEST_DB`. One line. Every child of the process
  inherits a correct database instead of a production one — **and so does every
  in-process consumer that resolves `REDIS_URL` lazily**, which is twenty call
  sites across sixteen production modules — including the outbox **write** at
  `agent/session_completion.py:609` (spike-3). That second effect is a bonus fix and a real
  regression surface at the same time; Risk 7 and task 2.5 own it.
- **The unreachable-write seam**: the export lives in its own module-level
  function `tests/conftest.py::_export_claimed_redis_url()`, which
  `pytest_configure` calls. A test invoking the hook directly against a synthetic
  claim registry stubs that one function out, so the poisoning write **never
  happens** rather than being undone at teardown. The stub is installed inside
  `_reset_claim_state`, the helper all twenty-three callers already go through, so
  no call site has to remember. This is the plan's own thesis applied one level
  down: fix the producer, not the consumer. See Risk 1.
- **The deletion**: `tests/unit/test_subprocess_test_db_isolation.py` goes
  entirely — scanner, `ALLOWLIST`, `SKIP_ARGV0`, the two reason classes, the
  `-m` heuristic, the rejected-shape source fixtures, and the line-drift
  failure mode.
- **The replacement proof**: three behavioral assertions added to
  `TestPerProcessDbClaim` that test the *outcome* (a child lands on the claimed
  db) rather than the *convention* (a call site is spelled a certain way).
- **The docs cascade**: `tests/README.md`,
  `docs/features/test-isolation-hardening.md`, and
  `docs/features/test-db-ownership.md` describe the new status quo — the
  environment is correct, `subprocess_env` is the `PYTHONPATH` pinner — with no
  historical residue of the retired convention.

### Flow

**A test author writes `subprocess.run([sys.executable, ...])` with no `env=`**
→ child inherits `os.environ` → **`REDIS_URL` names this process's claimed db**
→ child imports popoto → **claimed db**. No review comment, no scanner, no
allowlist entry, nothing to remember.

**A test author genuinely needs db0** (to prove a production guard fires)
→ writes `env={**subprocess_env(), "REDIS_URL": "redis://localhost:6379/0"}`
→ **the intent is stated at the call site**, where a reader can see it.

**A test author needs the child to resolve repo modules from this checkout**
→ calls `subprocess_env(project_root=...)` → unchanged from today.

### Technical Approach

- **Add the export to `tests/conftest.py::pytest_configure`,** immediately after
  line 289 and before anything can spawn a child — routed through a module-level
  seam so a synthetic hook invocation can suppress it (Risk 1):

  ```python
  def _export_claimed_redis_url() -> None:
      """Publish THIS process's claimed db as the process-wide ``REDIS_URL``.

      A module-level seam rather than an inline assignment so that tests which
      call ``pytest_configure()`` directly against a synthetic claim registry can
      stub it out and never write a foreign db into the live session env.
      """
      os.environ["REDIS_URL"] = db_claim.redis_test_url()


  # ... inside pytest_configure, after the claim-failure guard:
      os.environ["POPOTO_TEST_DB"] = str(db)
      _export_claimed_redis_url()
  ```

  **Zero-arg by design.** It takes no `db` parameter: `redis_test_url()` already
  composes host, port and the memoized claim in one place, and threading `db`
  through would fork the URL spelling into two sites. The call is a plain
  module-global lookup at call time, which is precisely what makes
  `monkeypatch.setattr` on it effective.

  **Spelling is already available and collision-free.** `tests/conftest.py:18`
  carries `from tests import db_claim`, so the module-qualified call needs no
  new import and cannot shadow the *fixture* also named `redis_test_url` at
  conftest.py:937 — which nine test modules request by name and which must not
  be renamed. Do **not** add `from tests.db_claim import redis_test_url`.

- **Do not attempt to fix the parent process's popoto client.** popoto's
  `pytest11` plugin resolves `REDIS_URL` before `tests/conftest.py` is even
  imported (spike-2 finding 5). The existing `redis_test_db` autouse swap
  already handles the parent's popoto client.

  **But do not repeat the earlier draft's overcorrection that "the export is for
  children."** That was spike-2's finding generalized past the `tests/` boundary
  it was measured in. Popoto's plugin is immune because it resolves at *import*;
  the twenty call sites in spike-3 resolve inside a *function body*, per call,
  and are fully affected in-process. The honest code comment states both: popoto's
  in-process client is untouched by this line, and every lazy `REDIS_URL` reader
  in the process — test or production module — now resolves to the claimed db.

- **Make the direct-invocation write unreachable — this is required, not
  optional.** Extend
  `tests/unit/test_conftest_isolation_guards.py::_reset_claim_state` (lines
  497-540) immediately after the existing
  `monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)` with:

  ```python
  # The synthetic registry hands back a db this process does not own, so the
  # real export would poison the live session env for every later test in this
  # worker. Suppress the write instead of undoing it: there is then no window
  # in which os.environ names a foreign db.
  monkeypatch.setattr(_conftest, "_export_claimed_redis_url", lambda: None)
  monkeypatch.delenv("POPOTO_TEST_DB", raising=False)
  ```

  `_conftest` is already imported at line 61 (`import tests.conftest as
  _conftest`), so no new import is needed. Then delete the now-redundant
  per-call-site `POPOTO_TEST_DB` delenvs at 1096, 1108 and 1126. Risk 1 carries
  why the seam beats both `delenv` and `setenv`, why all twenty-three callers are
  safe, and the pre-existing gap at 1220 that this closes. **Note the helper
  contains no `POPOTO_TEST_DB` handling today** — an earlier draft of this plan
  said it did; it does not.

  **`REDIS_URL` itself is deliberately NOT touched by the helper.** Neither
  `delenv` nor `setenv` appears. With the write suppressed at the source there is
  nothing to restore, and any `delenv` here would make `REDIS_URL` *absent* for
  the duration of all twenty-three tests that use this helper — at which point
  every one of the twenty lazy consumers falls back to its hardcoded
  `"redis://localhost:6379/0"` default and the plan manufactures the exact db0
  exposure it exists to remove. An anti-criterion in Verification asserts no
  `delenv("REDIS_URL"` is ever introduced.

  **The guards file passes either way; that is not evidence.** spike-1 measured
  44 passed with the fix and no mitigation, because under `-n0` the tmp-registry
  stub claim also lands on db1 and matches the real claim. The divergence only
  appears under `-n 2 --dist=each`, where `gw1`'s real claim is db2 but its
  leaked `REDIS_URL` names db1 and its subprocess writes into `gw0`'s database.
  **Verify the mitigation under `-n 2 --dist=each` with the guards file and a
  probe in the same run.** A green single-file `-n0` run proves nothing here.

- **Delete `tests/unit/test_subprocess_test_db_isolation.py`.** No module
  imports it (spike-2 finding 8). Delete the file, not its contents.

- **Add three behavioral assertions to `TestPerProcessDbClaim`** in
  `tests/unit/test_conftest_isolation_guards.py`, which already owns the claim
  contract:
  1. the live process's `os.environ["REDIS_URL"]` ends with `/{claim_test_db()}`;
  2. a deliberately unguarded child (`subprocess.run([sys.executable, "-c",
     "import os; print(os.environ['REDIS_URL'])"])`, **no `env=`**) resolves to
     a URL **exactly equal** to the parent's `os.environ["REDIS_URL"]` — this is
     the permanent regression test for the whole issue, and it is *supposed* to
     look like a guard violation;
  3. a nested pytest child spawned **without** `env=` claims a **different** db
     than its parent (the #2628 invariant), proving the inherited `REDIS_URL` is
     overwritten by the child's own claim rather than leaked;
  4. **the documented coverage gap** (Risk 2): a child spawned with a
     **non-splatting** `env={"PATH": os.environ["PATH"]}` reports `REDIS_URL`
     **absent**. This asserts the shape the deleted scanner used to flag and the
     export cannot rescue, so the gap is pinned in code with a comment naming the
     retired guard, rather than living only in this plan. It is a documentation
     test by intent; label it as one in its docstring so a future reader does not
     mistake it for a behavior the system provides.

  **Assertions 1 and 2 must be dist-mode-independent — this is load-bearing.**
  After the AST guard is deleted these are the only regression detectors, and a
  detector that only fires under a flag nobody passes is not a detector.
  `scripts/pytest-clean.sh` defaults to xdist `--dist=load`, so a routine
  full-suite run never issues `--dist=each` and nothing schedules it. Phrase both
  assertions against **this process's own claim** rather than against a sibling
  worker: assert
  `os.environ["REDIS_URL"].endswith(f"/{_db_claim.claim_test_db()}")` and that
  the child's resolved URL equals `os.environ["REDIS_URL"]` byte-for-byte. Under
  `--dist=load` each worker executes both in its own process, so a cross-worker
  leak fails assertion 1 with no special invocation. Keep the `--dist=each` run
  as an *additional* check, never as the only one.
- **Leave the four currently-violating call sites completely untouched.** Not
  touching them is the demonstration that the design works. An anti-criterion in
  Verification asserts the diff does not include them.

- **Do not add explicit db0 overrides to `tests/unit/test_redis_flush_guard_prod.py`.**
  The issue body asks for this; spike-2 proves it is unnecessary (no child in
  that file connects to Redis) and it would misrepresent what those tests do. An
  anti-criterion asserts no `6379/0` literal appears there.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `pytest_configure` already handles `claim_test_db()`'s `RuntimeError` via `pytest.exit(...)` at lines 285-288. The new line runs **after** that guard, so a claim failure still exits before the export — verify no `REDIS_URL` is written on the exhaustion path.
- [ ] `redis_test_url()` calls `claim_test_db()`, which is memoized; in `pytest_configure` the claim has just succeeded, so it cannot raise on the second call. Assert this ordering holds rather than assuming it.
- [ ] No `except Exception: pass` blocks exist in the touched scope of `tests/conftest.py` or `tests/db_claim.py`.

### Empty/Invalid Input Handling
- [ ] `REDIS_PORT` unset → `redis_test_url` falls back to `"6379"` (`db_claim.py:314`). Confirm the composed URL is well-formed with `REDIS_PORT` **unset** and **set**. **The empty-string case is explicitly OUT OF SCOPE**: `os.environ.get("REDIS_PORT", "6379")` returns `""` for `REDIS_PORT=""` and composes `redis://127.0.0.1:/N`, which is malformed — but that is pre-existing behavior of `redis_test_url`, already reached today through `subprocess_env`, and identical before and after this change. Fixing it (`os.environ.get("REDIS_PORT") or "6379"`) is a correct one-line change that belongs to the follow-up issue task 7 files, not to a Small-appetite isolation fix. Do not "confirm" a property that is false; do not fix it here either.
- [ ] The xdist controller path returns at line 282 without claiming; confirm it writes **no** `REDIS_URL` at all (rather than an empty or malformed one).
- [ ] Confirm the exported URL is never `.../0` under any code path — a claim of db 0 is impossible by pool definition `[1..15]`, but assert it, since a silent 0 is the entire incident class.

### Error State Rendering
- [ ] If a child does land on db0 despite the fix, `tools/redis_flush_guard.py` raises `RuntimeError` at the flush — verify that path is still reachable and its message still names the cause, since it is now the only remaining enforcement layer for that failure.
- [ ] Verify the conftest flush guard's "not a claimed db" error (`tests/conftest.py:217-226`) still fires with its diagnostic intact.

## Test Impact

- [ ] `tests/unit/test_subprocess_test_db_isolation.py` (entire file, 688 lines, 9 tests) — **DELETE**: the convention it enforces no longer exists. No module imports it.
- [ ] `tests/conftest.py::pytest_configure` — **UPDATE**: the export goes through a new module-level `_export_claimed_redis_url()` seam (Risk 1), not an inline `os.environ[...] =`.
- [ ] `tests/unit/test_conftest_isolation_guards.py::TestPerProcessDbClaim::_reset_claim_state` (lines 497-540) — **UPDATE**: add `monkeypatch.setattr(_conftest, "_export_claimed_redis_url", lambda: None)` and `monkeypatch.delenv("POPOTO_TEST_DB", raising=False)` so the four direct `pytest_configure()` callers (1098, 1111, 1128, 1220) cannot poison the live session. **No `REDIS_URL` delenv/setenv** — see Risk 1 for why that would open twenty-three db0-fallback windows across this helper's twenty-three callers.
- [ ] `tests/unit/test_conftest_isolation_guards.py` lines 1096, 1108, 1126 — **DELETE**: the per-call-site `monkeypatch.delenv("POPOTO_TEST_DB", ...)` calls become redundant once the helper owns them. The assertions at 1101, 1114 and 1130 are unaffected: they read `os.environ` after the synthetic hook call writes it.
- [x] **Measured (task 2.5, 2026-08-24) — `bridge/` group genuine, `agent/` group NOT measured (see below):** the eight-file cluster (`test_dedup.py`, `test_reconciler.py`, `test_catchup_claim.py`, `test_duplicate_delivery.py`, `test_last_processed.py`, `test_bridge_dispatch_contract.py`, `test_output_handler.py`, `test_deliver_pipeline_completion.py`; 222 tests) was run once against `origin/main` (`0b8f40ffe`) and once against this branch. **Disposition: NO CHANGE to any of the eight files** — all 222 tests pass on both `main` and the branch (222 passed / 222 passed); nothing in the cluster went red. The measured difference is entirely in the attributed key-set snapshot (see Success Criteria): on `main` the cluster wrote two `bridge:msgclaim:*` keys into production db0 via `test_dedup.py`; on the branch it wrote none. No repair or deletion was needed because no test's assertions depended on the db0 write landing anywhere observable — the leak was silent production pollution, not a load-bearing fixture. **This zero-delta result is genuine only for the `bridge/` half of the cluster** (`bridge.dedup._get_redis()` is unmocked in `test_dedup.py`): the `agent/` half (`test_output_handler.py`/`tests/unit/output_handler/`, `test_deliver_pipeline_completion.py`) is mock-isolated at every call site that reaches `agent/session_completion.py:424`/`:609` and `agent/output_handler.py:438` (five `patch()` sites in `test_deliver_pipeline_completion.py`, the `_patch_redis_no_drain_wait` helper patching `redis.Redis.from_url`, and `handler._redis = MagicMock()` throughout `tests/unit/output_handler/`), so `main` reads zero there too and the branch's matching zero proves nothing about the `agent/` write path. See the corrected Success Criteria row for the full accounting and why this is discharged by argument, not by adding a new unmocked test purely to generate a number. This measurement is the empirical answer to Risk 7 for the `bridge/`-reachable portion of the constructed sample; the `agent/` sites and all residual non-cluster call sites are the follow-up issue (task 7). **Post-rebase note:** #2941 (landed on `main` after this measurement) split `tests/unit/test_output_handler.py` into `tests/unit/output_handler/` (5 files, byte-for-byte moved test bodies). After rebasing onto the new `origin/main`, the cluster (now naming the directory) was re-run on the branch: still 222 passed, still zero new attributed keys — the split changed no behavior, only file layout. All cluster path references below were updated to `tests/unit/output_handler/`.
- [x] `tests/unit/output_handler/`, `tests/unit/test_deliver_pipeline_completion.py` — **IN THE CLUSTER, NOT A MEASUREMENT OF THE `agent/` WRITE PATH**: both files pass unchanged on `main` and the branch (part of the 222/222), but every call site in them that could reach `agent/session_completion.py:609`/`:424` or `agent/output_handler.py:438` is mocked out (five `patch()` sites over `_queue_completion_suppress_reaction`, `_patch_redis_no_drain_wait` patching `redis.Redis.from_url`, and `handler._redis = MagicMock()` throughout). The pass/fail count is uninformative for db0 exposure here; it says only that the mocks did not regress.
- [ ] `tests/unit/test_conftest_isolation_guards.py::TestPerProcessDbClaim` — **UPDATE (add)**: four new assertions (live-process export, unguarded-child inheritance, nested-pytest own-claim, and the non-splatting-`env=` coverage-gap documentation test from Risk 2).
- [ ] `tests/unit/test_migrate_strip_pid_fields.py::TestSubprocessCapture::test_the_subprocess_ran_against_the_test_db_not_production` (line 411) — **DELETE**: three independent mechanisms would now have to be removed for it to go red, so it can no longer fail. Its purpose is subsumed by the new unguarded-child assertion, which tests the same property at the layer that owns it. Its docstring at line 416 ("`REDIS_URL` is unset on this machine by default") is already false today.
- [ ] `tests/unit/test_migrate_strip_pid_fields.py` module docstring (lines 29-32) — **UPDATE**: it explains why every test in the file sets `REDIS_URL`; that reasoning changes.
- [ ] `tests/unit/test_redis_bootstrap.py` module docstring (line 7) — **DEFERRED to a follow-up issue**: "Empty/missing `REDIS_URL`: falls back to 127.0.0.1:6379/db=0" describes a pydantic `Field(default=...)`, not an env read (`REDIS__URL` is the actual seam). Pre-existing drift, wrong identically before and after this change; not adjacent enough to justify riding a Small-appetite fix. The file itself needs no code change (spike-2 finding 4).
- [ ] `tests/unit/test_redis_flush_guard_prod.py` — **NO CHANGE**, deliberately. Its children never connect to Redis; the issue's proposed override is dropped. Asserted as an anti-criterion.
- [ ] `tests/unit/test_watchdog_log_isolation.py`, `tests/unit/test_sdlc_next_skill.py` — **NO CHANGE**, deliberately. These are two of the four current violations; leaving them untouched *is* the proof. Asserted as an anti-criterion.

## Rabbit Holes

- **Converting the four current violations "while we're here."** Tempting and
  actively harmful — it destroys the evidence that the design works without
  call-site churn. Leave them.
- **Trying to make popoto's in-process client resolve from the new export.**
  It cannot: the plugin imports before conftest. Chasing plugin load order to
  win a redundant fix would burn the whole appetite for zero behavior change.
- **Generalizing `subprocess_env` into an env-builder framework**, or splitting
  it into `redis_env()` / `pythonpath_env()`. The `REDIS_URL` half becoming
  redundant is not a reason to refactor a working helper; redundant and correct
  is fine.
- **Auditing all ~40 existing `subprocess_env(...)` call sites** to see which
  could now drop the argument. None of them need to. Removing correct code to
  celebrate a fix is churn.
- **Re-litigating the `-m` heuristic or `SKIP_ARGV0`.** They are being deleted.
  Do not improve them on the way out.
- **Extending the export to other environment variables** (`AI_REPO_ROOT`,
  `POPOTO_TEST_DB` inheritance, etc.). Scope is `REDIS_URL`; `POPOTO_TEST_DB`
  must specifically keep *not* being inherited.

## Risks

### Risk 1: `pytest_configure` is called directly by tests, and the new write is unrestorable
**Impact:** Three tests in `tests/unit/test_conftest_isolation_guards.py`
(lines 1111, 1128, 1220) invoke the hook against a `tmp_path` claim registry
that returns db 1, not this process's claim. A bare `os.environ[...] =` write
survives the test. Every subsequent test in that worker — and every child it
spawns — then reads and writes a db this process does not own, quite possibly a
slot a concurrent pytest process holds and flushes each test. That is the #2605
cross-process class the claim machinery was built to eliminate, reintroduced by
the fix meant to strengthen it. (An earlier draft called this the plan's *only*
hard blocker. It is not — Risk 7 is the larger one.)
**Measured, not theorised.** spike-1 reproduced it: under `-n 2 --dist=each`,
`gw1`'s real claim was db2 but its `os.environ["REDIS_URL"]` read db1 for the
rest of the session, and its subprocess wrote into `gw0`'s database. The guards
file itself reported **44 passed** — the leak is invisible from inside it,
because under `-n0` the stub claim also picks db1 and masks the divergence.
**Mitigation — suppress the write at its source, do not undo it afterwards.**
`pytest_configure` calls a module-level seam
`tests/conftest.py::_export_claimed_redis_url()`; `_reset_claim_state` stubs
that seam to a no-op via `monkeypatch.setattr`, so a synthetic hook invocation
performs no `REDIS_URL` write at all.

```python
monkeypatch.setattr(_conftest, "_export_claimed_redis_url", lambda: None)
monkeypatch.delenv("POPOTO_TEST_DB", raising=False)
```

**Why the seam and not `monkeypatch.delenv("REDIS_URL", ...)`.** Two independent
reasons, and the second is the decisive one.

1. *It is the plan's own thesis, applied one level down.* This plan deletes 688
   lines on the argument that every prior fix patched a **consumer** of a wrong
   environment instead of correcting the **producer**. A `delenv` in the test
   helper is that same anti-pattern: it teaches a consumer to tolerate a write
   the hook should never have made in a synthetic context. The seam makes the
   write unreachable, which is a property of the code rather than a convention
   somebody has to remember when they add a twenty-fifth caller or a sibling
   helper.
2. *`delenv` would manufacture the exact hazard this plan exists to remove.*
   `_reset_claim_state` has **twenty-three** call sites in this file (558, 581,
   598, 610, 631, 655, 683, 709, 727, 738, 756, 772, 781, 807, 829, 956, 1004,
   1052, 1095, 1107, 1125, 1217, 1245; the definition at 498 is not one), not the four
   direct `pytest_configure()` callers the round-1 draft reasoned about.
   `delenv` makes `REDIS_URL` **absent** for the whole body of each of those
   twenty-three tests, and all twenty lazy consumers (spike-3) fall back to their
   hardcoded `"redis://localhost:6379/0"` when it is missing. That is twenty-three
   fresh windows of production-db0 exposure, opened by the mitigation, directly
   contradicting the zero-delta Success Criterion.

**And not the guarded `setenv` either, though it is defensible.** The round-1
draft rejected only the strawman
`monkeypatch.setenv("REDIS_URL", os.environ.get("REDIS_URL", ""))` — correctly,
since its empty-string fallback exports a malformed URL on the
xdist-controller path where nothing was ever set. The guarded form (read the
live value; `setenv` it back if present, `delenv` only if it was genuinely
absent) does avoid the db0 window and does restore at teardown. It loses on
timing: between the synthetic `pytest_configure()` call and the teardown,
`os.environ["REDIS_URL"]` still names the tmp-registry's db, so any child spawned
or any lazy consumer called inside that window still hits a foreign db. The seam
has no such window. Same edit size, strictly stronger property.

**All twenty-three callers are safe under the seam.** Suppressing the export
changes nothing any of them assert: no test in the file reads `REDIS_URL` after
a synthetic hook call, and the class's only subprocess spawn,
`_spawn_flock_holder` at line 470, runs `import fcntl, os, sys, time` and never
touches Redis. The three new behavioral assertions read the **live** process's
`REDIS_URL`, written by the real `pytest_configure` at session start, and do not
go through `_reset_claim_state` at all.

**Correcting a stale anchor in this plan's own earlier draft:** the instruction
used to say "alongside the `POPOTO_TEST_DB` handling that already lives there."
It does not live there. `_reset_claim_state` (lines 497-540) only rebinds
claim-module globals and clears `PYTEST_XDIST_WORKER`; the `POPOTO_TEST_DB`
protection lives at three of the four direct call sites. Moving the
`POPOTO_TEST_DB` delenv into the helper lets the now-redundant ones at 1096,
1108 and 1126 be **deleted** — the assertions at 1101, 1114 and 1130 keep
working, because they read `os.environ` *after* the synthetic
`pytest_configure()` call writes it, and `POPOTO_TEST_DB` is still written by
that call (only the `REDIS_URL` seam is stubbed). It also closes a pre-existing
gap: the fourth direct caller at 1220 has no `POPOTO_TEST_DB` delenv at all
today. `POPOTO_TEST_DB` keeps `delenv` rather than a seam because **absence is
the correct state** for it (#2628: it must not be inherited), so no fallback
hazard exists.

**Verification must be able to go red.** A `grep -c 'REDIS_URL'` over the whole
guards file cannot: the file already contains exactly three occurrences on
unmodified `main` (lines 587, 1194, 1205), so any `> 2` threshold passes without
the fix. And running the guards file alone under `-n 2 --dist=each` cannot
either — spike-1 finding F measured that this file reports green whether or not
the leak exists. Both rows are replaced in Verification: one scoped to the
**helper body** (asserting the `setattr` stub is present there, not merely
somewhere in the file), one that runs the guards file **and a db-reading probe
in the same invocation**. A third row is an anti-criterion against
`delenv("REDIS_URL"` ever appearing. See Verification.

### Risk 2: Removing the only static enforcement leaves a coverage gap for a case the new tests miss
**Impact:** If the export is ever reverted, narrowed, or shadowed, nothing
scans for unguarded call sites any more, and the failure is silent until a
production row is written.
**Mitigation:** The new unguarded-child assertion is a *behavioral* regression
test: it spawns a bare subprocess with no `env=` and asserts the claimed db, so
it goes red the instant the export stops working — regardless of which call
site or which spawn mechanism is involved.

**The coverage trade is broader on the inheriting path and narrower on the
non-inheriting one, and the narrowing is real.** The round-1 draft claimed
"strictly broader coverage", which is false and is exactly the sentence a
reviewer would rest on when approving a 688-line deletion. The correction:

- *Broader.* The behavioral assertion catches any spawn mechanism — `os.posix_spawn`,
  a vendored helper, a future API — where the AST scan only ever saw five named
  `subprocess.*` functions with a literal `env=` keyword it could parse.
- *Narrower, precisely here.* The deleted scanner's `_env_value_is_clean`
  (`tests/unit/test_subprocess_test_db_isolation.py:422-449`) returns `False` for
  any `env=` value that is not an `ast.Call` or an `ast.Name` — so a **dict
  literal** `env={...}` was always flagged. That is how both current
  `test_watchdog_log_isolation.py` violations were caught. Post-fix, a dict
  literal that splats (`env={**os.environ, ...}`) is genuinely fine and the scan
  would have been a false positive. But one that does **not** splat —
  `env={"PYTHONPATH": str(REPO_ROOT)}` — drops `REDIS_URL` entirely, the child
  falls back to the hardcoded db0 default, and **the export cannot rescue it**:
  all three inheritance assertions exercise only the inheriting path. This shape
  loses its only detector.

That loss is accepted, not hidden. It is bounded (it requires someone to write a
non-splatting `env=` dict, which is rarer than writing no `env=` at all — the
shape the export now handles by construction), and the runtime backstops
(`tools/redis_flush_guard.py` on db0 flush; conftest's claimed-db flush guard)
still fail closed underneath it. It is also *documented in code* rather than only
in this plan: a fourth assertion in `TestPerProcessDbClaim` spawns
`env={"PATH": os.environ["PATH"]}` and asserts the child reports `REDIS_URL`
**absent**, with a comment naming the retired scanner and stating that this shape
is unprotected by design. A future reader who writes that shape and greps for why
finds a test that says so.

### Risk 3: The nested-pytest `POPOTO_TEST_DB` invariant (#2628) is assumed rather than proved
**Impact:** If a nested pytest child somehow inherits the parent's db, its
per-test `flushdb()` wipes the parent's data mid-run — reproducing the exact
rotating-failure-set symptom of #2628, but harder to diagnose because the
plausible cause has just been "fixed".
**Mitigation:** Explicit test (assertion 3): a nested pytest child spawned
**without** `env=` must claim a *different* db than its parent. The issue body
flags this as something the build "must confirm rather than assume", and it is
a named Verification row, not a note.

### Risk 4: Reviewers read "delete 688 lines of a guard" as removing enforcement
**Impact:** Review stalls, or the deletion is rejected on reflex.
**Mitigation:** The PR body carries the demonstrated-red evidence: the same
probe red (child on db0) with the export reverted and green (child on the
claimed db) with it applied, plus an explicit statement of which enforcement
layers remain and which was only ever a static proxy. Risk 2's argument belongs
in the PR description, not just the plan.

### Risk 5: The import spelling collides with the same-named fixture (LOW — resolved by spike-1)
**Impact:** `tests/conftest.py` defines a fixture `redis_test_url` at line 937;
nine modules request it by name. A careless module-scope
`from tests.db_claim import redis_test_url` shadows it and breaks all nine, or
is itself shadowed and the export silently writes something wrong.
**Mitigation:** No new import is needed at all. `tests/conftest.py:18` already
has `from tests import db_claim`, so `db_claim.redis_test_url()` is
collision-free by construction. The plan forbids the `from ... import` spelling
outright, and a Verification row asserts the fixture is still requestable.

### Risk 6: The fix silently flips the Redis hostname from `localhost` to `127.0.0.1`
**Impact:** The ambient production `REDIS_URL` spells the host `localhost`;
`db_claim.redis_test_url()` defaults to `127.0.0.1` (`db_claim.py:312`). So the
export changes not just the db number but the hostname of every inheriting
child. Same server, same socket — but any test or tool that string-matches
`redis://localhost` on an inherited value would break, and a reader diffing two
URLs may misread the host change as the significant one.
**Mitigation:** Measured as harmless in spike-1 (children connected fine, and
recon found no test that reads ambient `REDIS_URL` at all). Make it a
**deliberate** choice rather than an accident: either accept `127.0.0.1` — which
is what `subprocess_env` has always passed to children, so this makes the two
paths agree — or pass `redis_test_url("localhost")` to preserve the ambient
spelling. The plan's default is to accept `127.0.0.1` for consistency with
`subprocess_env`, and to say so in the code comment. See Open Question 4.

### Risk 7: Twenty non-test call sites silently move off production db0 in-process
**Impact:** This is the plan's **largest** blast radius and it was missed
entirely by the first draft, which called the change "test-harness-internal."
**Twenty** call sites across sixteen production modules resolve `REDIS_URL`
**lazily, inside a function body** — see spike-3 finding 1 for the verbatim grep
and the full list. They re-read the variable per call, after `pytest_configure`,
so unlike popoto's `pytest11` plugin they are **not** immune.

The three `agent/` sites were absent from the round-1 inventory and are the ones
that matter most: `agent/session_completion.py:609` **writes**
(`r.rpush` + `r.expire` on `telegram:outbox:{session_id}`) and therefore puts
rows into live production db0 under test today; `:424` polls the same key with
`r.llen`; `agent/output_handler.py:438` caches the URL for a lazily-constructed
client.

Tests exercising these paths write to production db0 today — measured:
`tests/unit/test_dedup.py` alone leaves four net keys in db0 (61505 → 61509).
After the export they write to the claimed test db, which the autouse
`redis_test_db` fixture **flushes per test**. Any test that implicitly relied on
a key surviving from a previous test now sees it gone.
**Expect key-visibility failures, not import errors.**

The *direction* is unambiguously right — a unit test writing to live production
is the defect, and this export fixes it repo-wide. The hazard is that the
correction lands as a batch of unexplained red in files nobody expected this
plan to touch, and gets misdiagnosed as the export being broken.

**Mitigation:** Do not try to enumerate the affected set from the import graph.
Seventy-three test files import one of those modules without pinning
`REDIS_URL` (37 of them reference `session_completion`/`output_handler`), but
importing is not calling — that number is a loose upper bound
and treating it as a work list would blow the appetite on files that never
change behavior. **Measure instead.** Task 2.5 runs the highest-signal cluster
before and after the export and diffs the results. **The cluster must contain at
least one test per consumer group**, or a zero delta measures only the groups it
covers: the round-1 cluster was drawn from the incomplete fifteen-site inventory
and covered no `agent/` path, so it would have reported the headline criterion
met while the `session_completion.py:609` outbox write went unmeasured.
`tests/unit/output_handler/` and
`tests/unit/test_deliver_pipeline_completion.py` are added for exactly that
reason — they call the write paths. Any file that goes red is
triaged as either (a) a genuine cross-test key dependency to fix, or (b) an
assertion that was only ever passing because production db0 is never flushed —
which is itself a finding worth writing down. If the red set exceeds what a
Small appetite absorbs, **stop and split it**: the export plus the guard
deletion ship, and the follow-on test repairs get their own issue rather than
silently inflating this one.

**Anti-mitigation, explicitly rejected:** do not paper over this by pinning
`REDIS_URL` back to db0 in the affected tests. That would preserve the defect
to protect the assertions that depend on it.

## Race Conditions

### Race 1: A child is spawned before `pytest_configure` has exported the URL
**Location:** `tests/conftest.py:274-294`
**Trigger:** Any code that forks a subprocess during plugin load or conftest
import — i.e. before the hook runs.
**Data prerequisite:** `claim_test_db()` must have returned before any child
inherits the environment.
**State prerequisite:** `pytest_configure` runs before collection and therefore
before every fixture, autouse or installed-plugin (documented at
`tests/conftest.py:267-273`). No test body can execute earlier.
**Mitigation:** Placement. The export goes immediately after the claim, in the
earliest hook that has a db to publish. Nothing in `tests/conftest.py`'s
module-scope imports (stdlib + `pytest` + `tests.db_claim`) spawns a process.

### Race 2: Two concurrent pytest processes both write `REDIS_URL`
**Location:** `tests/conftest.py:289+1`
**Trigger:** A single-test run and a background full-suite run on the same
machine, plus xdist workers within each.
**Data prerequisite:** Each process's `claim_test_db()` must have returned a
slot no live process holds.
**State prerequisite:** `os.environ` is per-process; there is no shared
environment to contend for.
**Mitigation:** None needed — this is not actually a race. The `fcntl.flock`
claim (`db_claim.py:140-177`) already guarantees distinct slots across live
processes, and each process writes only its own environment. Recorded here to
document why it is a non-hazard.

### Race 3: A test mutates `REDIS_URL` mid-session and a later child inherits the mutation
**Location:** `tests/unit/test_conftest_isolation_guards.py:1111, 1128, 1220`
**Trigger:** A direct `pytest_configure()` call writing an unrestorable value;
any subsequent child in the same worker inherits it.
**Data prerequisite:** The value in `os.environ` at spawn time must name a db
this process claimed.
**State prerequisite:** The session's claim must be the only db the session's
children ever touch.
**Mitigation:** the `_export_claimed_redis_url` seam, stubbed in
`_reset_claim_state` (Risk 1). Note this closes the race rather than shortening
it: a restore-at-teardown mitigation (`delenv`/`setenv`) would leave the window
between the synthetic hook call and teardown open, and a child spawned inside
that window would still inherit the foreign db. Suppressing the write means the
window has zero width. This is the one genuine ordering hazard the change
introduces.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2645] Changing, weakening, or re-scoping `tools/redis_flush_guard.py` or the server-side Redis ACL. They are the runtime backstops this plan explicitly relies on remaining intact; they are shipped under #2645 and are not touched here.
- [SEPARATE-SLUG #2628] Altering the `POPOTO_TEST_DB`-is-not-inherited rule in `subprocess_env`, or the flock claim pool itself. This plan proves the invariant still holds; it does not modify the machinery that provides it.
- [SEPARATE-SLUG #2763] Removing `subprocess_env` or its `REDIS_URL` pin. The pin becomes redundant, not wrong, and the helper's `PYTHONPATH` half is a live, separate concern with existing `project_root=` callers.
- [SEPARATE-SLUG #2904] The two other red-main failures (`test_sdlc_dispatch.py`, `test_sdlc_meta_set.py`). Owned and shipped by #2904's plan; not touched here.

## Update System

No update system changes required — but **not** for the reason the first draft
gave. That draft called this "test-harness-internal," which is false: spike-3
found twenty lazy `REDIS_URL` call sites in non-test modules whose in-process
Redis target changes under test (Risk 7).

The accurate reason is narrower and still holds. Every edited file lives under
`tests/` or `docs/`; no production module is modified. The behavior change to
those twenty call sites is confined to **processes that ran
`tests/conftest.py::pytest_configure`** — i.e. pytest, and nothing else. The
bridge, the worker (including `agent/output_handler.py` and
`agent/session_completion.py`), the reflections scheduler, and `ui/app.py` in
production never load `tests/conftest.py`, so their resolved `REDIS_URL` is byte-identical
before and after. Consequently the change adds no dependency, no config file, no
secret, no entry point, and no migration; `scripts/update/` and the `/update`
skill are untouched; and nothing propagates to other machines beyond the
ordinary `git pull` that `/update` already performs.

A Verification row asserts the production-module claim rather than resting on
it: no file outside `tests/` and `docs/` appears in the diff.

## Agent Integration

No agent integration required. Nothing in `tests/` is reachable from the
Telegram bridge, the worker, or any MCP surface; `pyproject.toml
[project.scripts]` gains no entry and `bridge/telegram_bridge.py` imports
nothing from this change. The only agent-adjacent consideration is that
`.claude/hooks/validators/validate_no_raw_redis_delete.py` and the ambient flush
guard continue to behave identically — neither reads `REDIS_URL`, and both are
asserted unchanged.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/test-isolation-hardening.md` (lines ~197-233): the subprocess-inheritance section currently describes `subprocess_env` as the channel to a child. Rewrite to the new status quo — the process environment carries the claimed db, and `subprocess_env` is the opt-in `PYTHONPATH` pinner plus an explicit-intent helper. Remove the reference to the retired AST guard in the `TestPerProcessDbClaim` bullet and replace it with the three new behavioral assertions.
- [ ] Update `docs/features/test-db-ownership.md` (line 37 table): the "a subprocess that must see the same data" row no longer requires `subprocess_env()` for the db; correct the row and state what `subprocess_env` is still for.
- [ ] Update `docs/features/README.md` (lines 250, 253): refresh the Test Isolation Hardening and Test-DB Ownership summaries so neither implies a static call-site convention is enforced.
- [ ] Update `tests/README.md`: rewrite the "Subprocess Test-DB Inheritance (issue #2763)" section (lines ~502-545) — in particular the sentence "`os.environ` is never mutated", which becomes false, and the closing paragraph naming the enforcing guard, which must go entirely. Also correct the guard reference in line 33 and the #2605 corollary in line 48. **Describe only the new status quo**; leave no "formerly enforced by" residue.
- [ ] Review `CLAUDE.md`'s "Manual Testing Hygiene" paragraph: its claim that "this shell always carries a production `REDIS_URL`" stays true for shells but must not read as true inside pytest. Its `os.environ.setdefault` warning and the "assign explicitly, assert the db number" rule remain correct for standalone debug scripts and are unaffected — draw the shell/pytest line clearly rather than deleting the guidance. **In scope:** this paragraph is now actively wrong inside pytest *because of this change*, so correcting it is status-quo work, not drift cleanup.

### Folded in — two stale references, corrected here (operator decision, 2026-08-24)

The first draft deferred these to a follow-up issue on appetite grounds. That is
overturned: both are factual corrections to text naming a variable **this repo
does not have**, so there is no wording to argue about and nothing for a
reviewer to block on. Filing an issue to track a two-line correction costs more
than making it.

- [ ] `docs/sdlc/do-build.md:215` ("## Test Isolation") tells builders to "use `REDIS_TEST_DB` or a separate prefix". `REDIS_TEST_DB` does not exist; the mechanism is the flock claim plus `POPOTO_TEST_DB`, and after this change the environment is correct with no builder action at all. This file is loaded at runtime by every `/do-build`, which is an argument for fixing it now, not later.
- [ ] `tests/unit/test_redis_bootstrap.py`'s module docstring (line 7). Same character, same fix.

Task 7 makes both edits instead of filing an issue.

### External Documentation Site
- [ ] Not applicable — this repo has no external docs site.

### Inline Documentation
- [ ] Comment the new `pytest_configure` line with *why* (children inherit by construction) and its one honest limitation (popoto's plugin has already resolved; this is not defense-in-depth for the parent).
- [ ] Update `tests/db_claim.py::subprocess_env`'s docstring (lines 318-349): its `REDIS_URL` paragraph now describes a redundant belt over a correct environment, and the `POPOTO_TEST_DB` paragraph's "mirror image" framing needs restating.
- [ ] Update `tests/unit/test_migrate_strip_pid_fields.py`'s module docstring (lines 29-32).
- [ ] Docstring `tests/conftest.py::_export_claimed_redis_url` with *why it is a separate function at all* — so a synthetic `pytest_configure()` call can suppress the write rather than undo it. A future reader who inlines it back into the hook to "simplify" reintroduces Risk 1, so the reason must be at the definition site.
- [ ] Comment the two new lines in `_reset_claim_state` (the `setattr` stub and the `POPOTO_TEST_DB` delenv) with *why the helper — not the call site — owns them*: the next direct `pytest_configure()` caller is protected without anyone remembering, which is the same failure mode this whole issue is about. Add a second sentence on **why `REDIS_URL` is deliberately not `delenv`'d here** — absence makes all twenty lazy consumers fall back to hardcoded db0 across this helper's twenty-three callers — because a future reader will otherwise "fix" the apparent asymmetry with `POPOTO_TEST_DB`.
- [ ] (`tests/unit/test_redis_bootstrap.py`'s module docstring is **deferred** — see the Deferred subsection above.)

## Success Criteria

- [ ] `tests/conftest.py::pytest_configure` exports `REDIS_URL` naming this process's claimed db, immediately after the `POPOTO_TEST_DB` export and after the claim-failure guard.
- [ ] A plain `subprocess.run([sys.executable, "-c", ...])` with **no `env=`**, launched from a test, resolves `REDIS_URL` to a value **byte-identical** to the parent's — asserted by a permanent test in `TestPerProcessDbClaim` that fires under the default `--dist=load`, not only under `--dist=each`.
- [ ] **Demonstrated red:** the same probe, run with the export reverted, is shown landing on db0, and that output is pasted into the PR description. A green-only run does not discharge this.
- [x] **The affected-suite cluster performs ZERO net writes against production db0.** Measured as an **attributed key-set snapshot**, not a `dbsize` delta — `dbsize` is signed and machine-global and cannot attribute a change to pytest specifically. The 2026-08-24 measurement below was taken with `redis-cli -n 0 --scan --pattern '<prefix>'` over `telegram:outbox:*`, `bridge:msgclaim:*`, and `LastProcessedRecord:*`, which is how these numbers were actually obtained; task 2.5's recipe (above) now routes the two Popoto-managed prefixes (`LastProcessedRecord:*` and the dedup prefix) through the ORM instead for any re-run, per CLAUDE.md's Manual Testing Hygiene rule — `telegram:outbox:*` and `bridge:msgclaim:*` have no Popoto model and stay raw-scan-only (tracked in #3003). The recipe changed; the recorded outcome below did not. Measured at build time (2026-08-24) by running the eight-file cluster once against `origin/main` (`0b8f40ffe`, detached worktree) and once against this branch:
  - **`main`**: key set grew by two — `bridge:msgclaim:-1003449100931:999` and `bridge:msgclaim:-5051653062:5920` — both created by `tests/unit/test_dedup.py` resolving `bridge.dedup._get_redis()`'s lazy `REDIS_URL` fallback to production db0. Both keys were TTL'd (15-16s) and deleted immediately after measurement.
  - **branch**: key set unchanged — zero new keys, zero removed keys, `diff` of before/after snapshots empty.
  - Both key lists are in the PR body beside the demonstrated-red evidence. It is measurable at all only because the export also moves the in-process consumers off db0 (Risk 7).

  **Scope resolved deliberately (critique round 3): the claim is stated over the
  cluster, not over `tests/unit/`.** The earlier wording branded itself "the
  outcome, not a proxy: a unit-suite run" while being discharged by an eight-file
  sweep — an inflated claim in the section a reviewer reads to approve a 688-line
  deletion. The alternative was a full-`tests/unit/` db0-delta row with an
  ambient-drift floor. It was rejected on two grounds, and neither is appetite:
  (a) this machine's Redis db0 serves a live bridge and worker that write to it
  continuously and independently of pytest, so a full-run delta is a signal buried
  in unbounded ambient noise and a criterion phrased as "delta equals the ambient
  floor" is a coin-flip that will be waved through the first time it disagrees;
  (b) the cluster is a *deliberately constructed* sample, and the `bridge/` half
  of it is a genuine, reproducible measurement (see the criterion below for the
  `agent/` half, which is NOT measured and is discharged by argument instead). A
  small accurate claim beats a large unfalsifiable one. Any residual db0 writes
  from paths outside the cluster are the follow-up issue in task 7, not a silent
  hole: this criterion no longer implies they were measured.
- [x] **The db0-delta cluster's `bridge/` group is genuinely measured; its `agent/` group is NOT, and is discharged by argument rather than measurement — stated as a limitation, not dressed as a result.** `bridge/dedup.py`'s lazy `_get_redis()` is unmocked in `tests/unit/test_dedup.py`, so the two `bridge:msgclaim:*` keys that appeared on `main` and vanished on the branch are a real, load-bearing measurement of that consumer group. The `agent/` group is different in kind, not just in outcome: every call site the cluster was extended to reach is mock-isolated at every existing call site in the repo, so the cluster's zero-delta result there is uninformative rather than confirming — `main` reads zero too, for the same reason. Specifically: `agent/session_completion.py:609` (`_queue_completion_suppress_reaction`) is `patch`-ed out at all five call sites in `tests/unit/test_deliver_pipeline_completion.py` (lines 442, 555, 631, 751, 851); `agent/session_completion.py:424` (`_await_outbox_drained`) is patched at line 818 and neutralized elsewhere by `_patch_redis_no_drain_wait`, which patches `redis.Redis.from_url` directly (its own docstring names the reason); `agent/output_handler.py:438`'s `__init__` URL capture never runs a real client because every handler test in `tests/unit/output_handler/` assigns `handler._redis = MagicMock()` before use, two of them with a hardcoded db0 URL literal that the process-wide export cannot override. The only other repo-wide caller of `_queue_completion_suppress_reaction`, `tests/unit/test_stall_detection.py:815`, is outside the cluster and also injects a fake Redis via `patch.dict("sys.modules", ...)`. **This plan deliberately does not add a new unmocked `agent/` test purely to produce a number for this row** — inventing scaffolding whose only purpose is satisfying this document is exactly the failure mode the simplify directive rejects, and it would not even prove anything about production behavior beyond what the argument above already establishes (that these paths are mock-isolated, not that they are safe unmeasured). The criterion is discharged for `agent/` by that argument: the paths neither wrote to db0 before this change nor can go red now, because no path from a real socket to db0 exists in any test that exercises them.
- [ ] **The lazy-consumer inventory in the plan equals the tree: 20 call sites over `bridge/ tools/ reflections/ ui/ agent/ config/`** — asserted by a Verification grep, not by a claim of exhaustiveness.
- [ ] The affected-suite sweep (task 2.5) is run before and after the export, and every file it turns red has an explicit disposition recorded in Test Impact — **deleted (with its one-line hollow-coverage note) or repaired**. Nothing is split to a follow-up; nothing merges red.
- [ ] A nested pytest child spawned without `env=` claims a **different** db than its parent (#2628 invariant), asserted by a permanent test.
- [ ] Under `-n N`, each xdist worker's unguarded child lands on **that worker's own** claimed db; the controller exports nothing.
- [ ] `tests/unit/test_subprocess_test_db_isolation.py` no longer exists, and no reference to it survives in `tests/` or `docs/features/`.
- [ ] `tests/unit/test_conftest_isolation_guards.py` passes, and — verified under `-n 2 --dist=each`, not `-n0` — each worker's `REDIS_URL` still names *its own* claimed db after the file has run.
- [ ] The hostname decision (`127.0.0.1` vs `localhost`) is made deliberately and recorded in the code comment, not left as a side effect.
- [ ] `tests/unit/test_watchdog_log_isolation.py` and `tests/unit/test_sdlc_next_skill.py` are **unchanged in the diff**, and their children land on the claimed db.
- [ ] `tests/unit/test_redis_flush_guard_prod.py` is unchanged and still proves the db0 flush guard fires.
- [ ] The now-tautological `test_the_subprocess_ran_against_the_test_db_not_production` is deleted, not left green-and-unfalsifiable — asserted by a name grep, not inferred from the file still being green.
- [ ] No file outside `tests/` and `docs/` (plus `CLAUDE.md`) appears in the diff — the twenty lazy `REDIS_URL` call sites change behavior under test without being edited (Risk 7 / Update System).
- [ ] `_reset_claim_state` stubs `_conftest._export_claimed_redis_url` and owns `POPOTO_TEST_DB` via `monkeypatch.delenv`; the redundant per-call-site delenvs at 1096/1108/1126 are gone; and **no `monkeypatch.delenv("REDIS_URL"` or `setenv("REDIS_URL"` exists anywhere in the guards file** (it would open a hardcoded-db0 fallback window across the helper's 23 callers).
- [ ] Risk 2's coverage loss is stated accurately ("broader on the inheriting path, narrower on the non-inheriting `env=` path") and pinned in code by the fourth assertion, rather than claimed as "strictly broader".
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No `xfail`/`xpass` conversions needed — none exist in `tests/`.

## Team Orchestration

### Team Members

**On the roster size (critique NIT):** every task is `Parallel: false` and that is
correct — the ordering is genuinely dependent. The demonstrated-red measurement
must run *before* the export exists; the blast-radius sweep *after* the export
and *before* the guard deletion; the deletion *after* the replacement assertions
exist to take over from it. There is no concurrency to buy here, and the roster
is not padding.

- **Builder (conftest export + guard deletion)**
  - Name: `env-export-builder`
  - Role: The one-line export, the `_reset_claim_state` restorability fix, and the deletion of the AST guard file
  - Agent Type: builder
  - Resume: true

- **Test engineer (demonstrated-red proof + replacement assertions)**
  - Name: `isolation-test-engineer`
  - Role: The three behavioral assertions, the nested-pytest invariant, the xdist per-worker check, and the red/green paper trail
  - Agent Type: test-engineer
  - Resume: true

- **Validator (isolation contract)**
  - Name: `isolation-validator`
  - Role: Verifies the demonstrated-red evidence is real, the untouched-call-site anti-criteria hold, and no enforcement was silently lost
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `isolation-documentarian`
  - Role: The four-file docs cascade with no historical residue
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Export `REDIS_URL` from `pytest_configure`
- **Task ID**: build-env-export
- **Depends On**: none
- **Validates**: tests/unit/test_conftest_isolation_guards.py
- **Informed By**: spike-2 (popoto's plugin resolves before conftest imports, so this is children-only; `redis_test_url` collides with a same-named fixture at conftest.py:937)
- **Assigned To**: env-export-builder
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: false
- Add a module-level `def _export_claimed_redis_url() -> None:` to `tests/conftest.py` that assigns `os.environ["REDIS_URL"] = db_claim.redis_test_url()`, and call it from `pytest_configure` immediately after `tests/conftest.py:289`. The seam is required (Risk 1); do not inline the assignment. Use the module-qualified `db_claim.redis_test_url()` spelling — a `from tests.db_claim import redis_test_url` would shadow the fixture at conftest.py:937
- Confirm it sits after the `pytest.exit` claim-failure guard, so an exhausted pool writes nothing
- Confirm the xdist-controller early return at line 282 still writes nothing
- Comment the *why* and the honest limitation (not defense-in-depth for the parent)

### 2. Make the direct-invocation `REDIS_URL` write unreachable
- **Task ID**: build-unreachable-write
- **Depends On**: build-env-export
- **Validates**: tests/unit/test_conftest_isolation_guards.py
- **Informed By**: spike-2 (direct callers at 1098, 1111, 1128, 1220 against a tmp_path registry); spike-1 (the leak is real and `-n0` masks it — the file reports 44 passed either way)
- **Assigned To**: env-export-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `monkeypatch.setattr(_conftest, "_export_claimed_redis_url", lambda: None)` and `monkeypatch.delenv("POPOTO_TEST_DB", raising=False)` to `_reset_claim_state` (lines 497-540), immediately after the existing `PYTEST_XDIST_WORKER` delenv. `_conftest` is already imported at line 61. **The helper has no `POPOTO_TEST_DB` handling today** — do not go looking for it
- Delete the now-redundant delenvs at 1096, 1108, 1126; the assertions at 1101, 1114, 1130 keep working because they read `os.environ` after the synthetic hook call, and `POPOTO_TEST_DB` is still written by it (only the `REDIS_URL` seam is stubbed)
- **Do NOT touch `REDIS_URL` in this helper — no `delenv`, no `setenv`.** The helper has **23** call sites, not 4; `delenv` would make `REDIS_URL` absent for all 23 tests and every one of the 20 lazy consumers would fall back to hardcoded db0, manufacturing the exact exposure this plan removes. The guarded `setenv` form avoids that but still leaves the hook-call-to-teardown window open. Read Risk 1 before deviating
- **Verify under `-n 2 --dist=each` with the guards file AND a db-reading probe in the same invocation**; the guards file alone reports green with or without the leak (spike-1 finding F)
- Do not accept a green `-n0` single-file run as evidence

### 2.5. Measure the non-test blast radius (Risk 7)
- **Task ID**: build-blast-radius-sweep
- **Depends On**: build-env-export
- **Validates**: tests/unit/ (the lazy-`REDIS_URL` consumer cluster)
- **Informed By**: spike-3 — **twenty** call sites across sixteen production modules read `REDIS_URL` inside function bodies and switch dbs in-process; `test_dedup.py` alone writes 4 net keys to production db0 today; `agent/session_completion.py:609` writes to `telegram:outbox:*` and was missing from the round-1 inventory
- **Assigned To**: isolation-test-engineer
- **Agent Type**: test-engineer
- **Domain**: Redis/Popoto data
- **Parallel**: false
- **Measure attributed keys, not `dbsize`.** `dbsize` is signed and machine-global: TTL expiry drives it negative and live bridge/worker traffic moves it independently of pytest, so a "delta of 0" is neither achievable nor evidence. Snapshot the **key set** the cluster's consumers actually write instead, before and after the affected-suite command. Two of the three prefixes are Popoto-managed models — read them through the ORM, per CLAUDE.md's Manual Testing Hygiene rule, not a raw scan: snapshot `{r.chat_id for r in DedupRecord.query.all()}` (`models/dedup.py`) and `{r.chat_id for r in LastProcessedRecord.query.all()}` (`models/last_processed.py`). `telegram:outbox:*` and `bridge:msgclaim:*` have **no Popoto model** — they are hand-built raw-client keys (see the `_MSG_CLAIM_KEY_PREFIX` comment at `bridge/dedup.py:137`) and cannot be expressed through the ORM today; for those two, `redis-cli -n 0 --scan --pattern '<prefix>'` remains the only available read. That gap — twenty production call sites building raw Redis clients from `os.environ["REDIS_URL"]`, bypassing Popoto — is tracked separately in issue #3003; this plan does not close it
- On **`main`**: expect the snapshot to grow. Record which keys appeared; those are pytest's writes to production, and they are the defect
- On the **branch**: expect the snapshot **unchanged** — no key that was absent before the run is present after it. That is the headline criterion, and unlike a `dbsize` delta it is attributable to pytest rather than to whatever else the machine was doing
- Put both key lists in the PR body. Do **not** use `MONITOR`: it is machine-global and this Redis serves live production traffic
- Diff the pass/fail set between the two runs. Triage each newly-red file as (a) a genuine cross-test key dependency to fix, or (b) an assertion that only ever passed because production db0 is never flushed — and write the disposition into Test Impact
- **The cluster must cover every consumer group.** The round-1 cluster (`test_dedup`, `test_reconciler`, `test_catchup_claim`, `test_duplicate_delivery`, `test_last_processed`, `test_bridge_dispatch_contract`) contained no `agent/`-facing test, so a zero delta over it would have certified the headline criterion with the outbox-write path unmeasured. `tests/unit/output_handler/` and `tests/unit/test_deliver_pipeline_completion.py` are now in the cluster and in the Verification row — do not drop them
- **Do not work from the import graph.** 73 files import one of those modules without pinning `REDIS_URL` (37 reference `session_completion`/`output_handler`); that is a loose upper bound, not a work list. Pick files that *call* the writing paths and measure
- **Do not pin `REDIS_URL` back to db0** anywhere to make a red test green — that preserves the defect to protect the assertion depending on it
- **No gate. Disposition every red file in this PR (operator decision, 2026-08-24 — see Appetite).** There is no size trigger, no escalation, and no split. Work the red set until the affected-suite command exits 0
- **Delete-first.** Each red file was green only because production db0 is never flushed, so its apparent coverage was an artifact of shared production state. Default to **deleting** the test. Repair only where the behavior is genuinely worth asserting and the fix is obvious (point a fixture at the claimed db; have the test create the key it reads). If a test needs new scaffolding to survive correct isolation, it was testing the scaffolding — delete it
- Record each deletion in Test Impact with one line: what it claimed to cover, and why that claim was hollow. A deletion with no such line is not an accepted disposition
- **Do not pin `REDIS_URL` back to db0** to rescue anything — that preserves the defect to protect the assertion depending on it (unchanged; still the one forbidden escape)

### 3. Prove red, then green
- **Task ID**: test-demonstrated-red
- **Depends On**: build-env-export
- **Validates**: tests/unit/test_conftest_isolation_guards.py
- **Informed By**: spike-1
- **Assigned To**: isolation-test-engineer
- **Agent Type**: test-engineer
- **Domain**: Redis/Popoto data
- **Parallel**: false
- With the export reverted, run a probe test whose unguarded child prints its resolved `REDIS_URL`; capture the output showing **db0**
- Re-apply the export, re-run, capture the output showing the **claimed db**
- Paste both into the PR description as the red/green paper trail

### 4. Add the permanent behavioral assertions

- **Placement is load-bearing (round-4 blocker).** The leak-detection probe goes in its own class at the **end of the file** — e.g. `class TestExportedRedisUrlSurvivesSyntheticHookCalls` after `TestReloadedRegistryIdentity` — asserting `os.environ["REDIS_URL"].endswith(f"/{_db_claim.claim_test_db()}")`. A probe in `TestPerProcessDbClaim` (line 452) runs before the synthetic hook calls at 1098+ and is structurally incapable of failing. The other assertions stay in `TestPerProcessDbClaim`; they check a different property
- **Demonstrate this row red before accepting it**: remove the `setattr` stub, run under `-n 2 --dist=each`, watch the tail probe fail, restore. A verification row never observed red is the exact failure this plan was written to stop reproducing
- **Task ID**: test-replacement-assertions
- **Depends On**: build-unreachable-write
- **Validates**: tests/unit/test_conftest_isolation_guards.py
- **Informed By**: spike-2 (this file already owns the claim contract; no new test file needed)
- **Assigned To**: isolation-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- **Deliberately does NOT depend on `build-blast-radius-sweep`.** Task 5 does (see its Why-the-2.5-edge note). These assertions prove the export works and are independent of how the blast-radius red set is dispositioned
- Assert the live process's `os.environ["REDIS_URL"]` ends with `/{claim_test_db()}`
- Assert a deliberately unguarded child's resolved URL is **byte-identical** to the parent's `os.environ["REDIS_URL"]`
- **Both of the above must fire under the default `--dist=load`**, phrased against this process's own claim rather than a sibling worker. After the guard deletion these are the only regression detectors, and `scripts/pytest-clean.sh` never issues `--dist=each` on its own
- Assert a nested pytest child spawned without `env=` claims a **different** db (#2628) — the nested target **must live under the repo root**, or pytest picks a different rootdir, never loads `tests/conftest.py`, and the assertion falsely passes (spike-1 hit this)
- Add the fourth, **documentation** assertion (Risk 2), named **exactly** `test_non_splatting_env_drops_redis_url` (a Verification row greps for that name): a child spawned with a non-splatting `env={"PATH": os.environ["PATH"]}` reports `REDIS_URL` **absent**. Comment it with the retired scanner's name and state that this shape is unprotected by design; label it a documentation test in its docstring
- Verify each under `-n 2 --dist=each` so per-worker independence is covered
- Remember `scripts/pytest-clean.sh` defaults to xdist and swallows `-s`; pass `-n0` or write probe output to a file when you need to read it
- Cover the `REDIS_PORT` **unset** and **set** cases from Failure Path Test Strategy. The empty-string case is **out of scope** (pre-existing, unchanged) — do not add a test that asserts the malformed URL, and do not fix it here

### 5. Delete the AST guard and retire the tautology
- **Task ID**: build-delete-guard
- **Depends On**: test-replacement-assertions, build-blast-radius-sweep
- **Why the 2.5 edge**: this task is the plan's one irreversible step, and 2.5 is what turns the blast radius from a guess into a measured list. Deleting 688 lines before that measurement exists would discard the guard while still blind to what the export exposed. The dependency is on 2.5 having **run and been dispositioned to exit 0** — with no split available, there is no longer a 'came back dirty' branch to reason about
- **Validates**: tests/unit/
- **Informed By**: spike-2 (no module imports the guard; the migrate test can no longer go red)
- **Assigned To**: env-export-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `tests/unit/test_subprocess_test_db_isolation.py` in full
- Delete `tests/unit/test_migrate_strip_pid_fields.py::test_the_subprocess_ran_against_the_test_db_not_production` and correct that file's module docstring
- Leave the four previously-violating call sites and `test_redis_flush_guard_prod.py` untouched
- `tests/unit/test_redis_bootstrap.py`'s docstring is **deferred** — do not touch it here

### 6. Validate the isolation contract
- **Task ID**: validate-isolation
- **Depends On**: build-delete-guard, test-demonstrated-red, build-blast-radius-sweep
- **Assigned To**: isolation-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm the red output in the PR body is a genuine db0 landing, not a fabricated string
- Confirm the four call sites and `test_redis_flush_guard_prod.py` are absent from the diff
- Confirm no `6379/0` literal was introduced anywhere in `tests/`
- Confirm the runtime backstops are untouched and still fire
- Confirm the db0 dbsize delta is **0** on the branch and **nonzero** on `main`, and that both numbers are in the PR body
- Confirm no file outside `tests/`, `docs/` and `CLAUDE.md` appears in the diff
- Confirm the Risk 1 mitigation is the `_export_claimed_redis_url` **seam stub** installed in `_reset_claim_state` itself, not at the call sites, and that **no `delenv("REDIS_URL"` or `setenv("REDIS_URL"` was introduced anywhere in the guards file**
- Confirm the db0-delta cluster includes the two `agent/`-facing files, and confirm the PR body and Success Criteria state plainly that their zero-delta result is NOT a measurement of the outbox write path (every call site that reaches it is mocked) — the `agent/` group is discharged by argument, not by this cluster
- Run all Verification rows

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-isolation
- **Assigned To**: isolation-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Rewrite `tests/README.md`'s subprocess-inheritance section and correct lines 33 and 48
- Update `docs/features/test-isolation-hardening.md` and `docs/features/test-db-ownership.md`
- Refresh the two `docs/features/README.md` index rows
- Draw the shell-vs-pytest line in `CLAUDE.md`'s Manual Testing Hygiene paragraph without deleting its still-correct debug-script guidance
- Update `subprocess_env`'s docstring
- **File the deferred-drift follow-up issue** covering `docs/sdlc/do-build.md:215`'s phantom `REDIS_TEST_DB`, `tests/unit/test_redis_bootstrap.py`'s module docstring, and the empty-`REDIS_PORT` malformed-URL case in `db_claim.redis_test_url` (`os.environ.get("REDIS_PORT", "6379")` → `or "6379"`); put its number in the PR body. Do **not** edit any of those files in this PR
- **Post a comment on issue #2807** noting that #2805 deletes `tests/unit/test_subprocess_test_db_isolation.py`, so its plan's line-813 "NO CHANGE, verified" row is moot and its builder need not re-verify `_argv0_is_skipped`/`_argv_reaches_python`. Do **not** edit `docs/plans/overclaim-guard-greps-whole-worktree.md` from this lane
- Leave no historical residue of the retired convention

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: isolation-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row
- Confirm every Success Criterion
- Generate the final report

## Verification

**Two conventions this table's rows must obey, both learned the hard way at
critique round 3.** (1) Any row whose command uses grep alternation writes
`grep -cE` — a `\|` inside a markdown table de-escapes to a literal pipe, and
under BASIC grep the pattern then matches nothing and exits 1, so the row reads
green-adjacent while verifying nothing. (2) One row, one decidable outcome. A
row whose Expected cell conjoins two assertions ("exit 0, **and** X is present")
cannot be recorded as pass or fail; split it into two rows.

| Check | Command | Expected |
|-------|---------|----------|
| Export present in pytest_configure | `grep -c 'os.environ\["REDIS_URL"\]' tests/conftest.py` | output contains 1 |
| AST guard file is gone | `ls tests/unit/test_subprocess_test_db_isolation.py` | exit code != 0 |
| No surviving reference to the retired guard | `grep -rn 'test_subprocess_test_db_isolation' tests/ docs/features/ CLAUDE.md` | exit code 1 |
| Claim-contract tests pass | `./scripts/pytest-clean.sh tests/unit/test_conftest_isolation_guards.py -p no:randomly -q` | exit code 0 |
| Export routed through the seam, not inlined (Risk 1 precondition) | `grep -c 'def _export_claimed_redis_url' tests/conftest.py` | output contains 1 |
| **Risk 1 mitigation is present in the helper, not just somewhere in the file** (the file-wide `grep -c 'REDIS_URL' > 2` form passes on unmodified `main` — it already has exactly 3 — so it is not usable; currently 0 on unmodified `main`, i.e. this row is red-able) | `sed -n '497,548p' tests/unit/test_conftest_isolation_guards.py \| grep -c '_export_claimed_redis_url'` | output contains 1 |
| **Anti-criterion A: no `monkeypatch.delenv("REDIS_URL"` in the guards file** — it would open a hardcoded-db0 fallback window across this helper's 23 callers (Risk 1) | `grep -c 'monkeypatch.delenv("REDIS_URL"' tests/unit/test_conftest_isolation_guards.py` | match count == 0 |
| **Anti-criterion B: no `monkeypatch.setenv("REDIS_URL"` in the guards file** — it avoids the db0 window but leaves the hook-call-to-teardown window open (Risk 1) | `grep -c 'monkeypatch.setenv("REDIS_URL"' tests/unit/test_conftest_isolation_guards.py` | match count == 0 |
| Redundant per-call-site delenvs removed once the helper owns them (currently 3 on unmodified `main`, i.e. this row is red-able) | `grep -c 'monkeypatch.delenv("POPOTO_TEST_DB"' tests/unit/test_conftest_isolation_guards.py` | output contains 1 |
| **Claim-contract tests do not leak REDIS_URL across workers.** The leak probe must live in a class placed at the **END** of `tests/unit/test_conftest_isolation_guards.py` (after `TestReloadedRegistryIdentity`) — round 4 measured that `TestPerProcessDbClaim` is at line 452 while every synthetic `pytest_configure()` call is at 1098+, so under `-p no:randomly` a probe inside it collects first and the row can never go red. Passing a node ID from the same file does not reorder collection. | `./scripts/pytest-clean.sh tests/unit/test_conftest_isolation_guards.py -p no:randomly -n 2 --dist=each -q` | exit code 0, AND demonstrated red: with the `_export_claimed_redis_url` stub removed, the tail probe must fail |
| The unguarded-child detector fires under the DEFAULT dist mode, not only `--dist=each` (Risk 2 / Concern: nothing schedules `--dist=each`) | `./scripts/pytest-clean.sh tests/unit/test_conftest_isolation_guards.py -p no:randomly -n 2 -q` | exit code 0 |
| **Nested-pytest #2628 invariant holds** (Risk 3's entire mitigation — previously verified nowhere) | `./scripts/pytest-clean.sh "tests/unit/test_conftest_isolation_guards.py::TestPerProcessDbClaim::test_nested_pytest_child_claims_its_own_db" -p no:randomly -q` | exit code 0 |
| Per-worker independence: each worker's child lands on that worker's own db | `./scripts/pytest-clean.sh "tests/unit/test_conftest_isolation_guards.py::TestPerProcessDbClaim::test_live_process_redis_url_names_its_own_claim" -p no:randomly -n 2 --dist=each -q` | exit code 0 |
| **Zero net production-db0 writes from the affected suites** (the outcome criterion; run on `main` AND on the branch). Measured as an **attributed key-set snapshot**, not `dbsize` — `dbsize` is signed and machine-global and cannot attribute a change to pytest specifically (see Success Criteria). Do NOT use `MONITOR` — it is machine-global and this Redis serves live production traffic. `telegram:outbox:*`/`bridge:msgclaim:*` have no Popoto model (tracked in #3003) and stay raw-scan; `LastProcessedRecord`/`DedupRecord` are Popoto-managed and go through the ORM, per CLAUDE.md's Manual Testing Hygiene rule. | Snapshot `redis-cli -n 0 --scan --pattern 'telegram:outbox:*'` and `bridge:msgclaim:*`, plus `{r.chat_id for r in LastProcessedRecord.query.all()}` and `{r.chat_id for r in DedupRecord.query.all()}`, before and after `./scripts/pytest-clean.sh tests/unit/test_dedup.py tests/unit/test_reconciler.py tests/unit/test_catchup_claim.py tests/unit/test_duplicate_delivery.py tests/unit/test_last_processed.py tests/unit/test_bridge_dispatch_contract.py tests/unit/output_handler/ tests/unit/test_deliver_pipeline_completion.py -p no:randomly -q`, on `main` AND on the branch | `main`: key set grows (attributed keys appear); branch: **key set unchanged** — no key appears or disappears |
| Affected suites still pass after moving off db0 (Risk 7 regression surface) | `./scripts/pytest-clean.sh tests/unit/test_dedup.py tests/unit/test_reconciler.py tests/unit/test_catchup_claim.py tests/unit/test_duplicate_delivery.py tests/unit/test_last_processed.py tests/unit/test_bridge_dispatch_contract.py tests/unit/output_handler/ tests/unit/test_deliver_pipeline_completion.py -p no:randomly -q` | exit code 0 |
| No production module was edited (Update System claim asserted, not assumed) | `git diff --name-only origin/main...HEAD \| grep -v '^tests/' \| grep -v '^docs/' \| grep -v '^CLAUDE.md$' \| wc -l` | output contains 0 |
| The tautological migration test is gone (the adjacent "migration tests green" row passes either way) | `grep -c 'test_the_subprocess_ran_against_the_test_db_not_production' tests/unit/test_migrate_strip_pid_fields.py` | match count == 0 |
| The hostname decision is recorded at the export site (= the seam body, where Documentation bullet 4 puts the rationale — **not** a line window inside `pytest_configure`) | `sed -n '/def _export_claimed_redis_url/,/^def \|^class /p' tests/conftest.py \| grep -c '127.0.0.1'` | output > 0 |
| Export uses the non-shadowing spelling | `grep -c 'db_claim.redis_test_url()' tests/conftest.py` | output contains 1 |
| **The lazy-consumer inventory in the plan matches the tree** (round-2 BLOCKER: the round-1 sweep omitted `agent/` and undercounted 20 as 15) | `grep -rn 'os.environ.get("REDIS_URL"' --include='*.py' bridge/ tools/ reflections/ ui/ agent/ config/ \| wc -l` | output contains 20 |
| **The coverage-gap documentation test exists by name** (Risk 2 — the non-splatting `env=` shape the deleted scanner used to flag; this is the sole compensating control for the enforcement lost with the 688-line deletion, so it must actually run). The test MUST be named exactly `test_non_splatting_env_drops_redis_url`. (Currently 0 on unmodified `main`, i.e. this row is red-able.) | `grep -c 'def test_non_splatting_env_drops_redis_url' tests/unit/test_conftest_isolation_guards.py` | output contains 1 |
| **The coverage-gap documentation test passes** (companion to the row above; split out so each row has one decidable outcome) | `./scripts/pytest-clean.sh "tests/unit/test_conftest_isolation_guards.py::TestPerProcessDbClaim" -p no:randomly -q` | exit code 0 |
| No shadowing import of the fixture name was added | `grep -c 'from tests.db_claim import redis_test_url' tests/conftest.py` | match count == 0 |
| Previously-violating watchdog sites untouched | `git diff --name-only main -- tests/unit/test_watchdog_log_isolation.py \| wc -l` | output contains 0 |
| Previously-violating sdlc-next-skill site untouched | `git diff --name-only main -- tests/unit/test_sdlc_next_skill.py \| wc -l` | output contains 0 |
| No db0 override introduced in the flush-guard tests (anti-criterion, drops issue Consequence 1) | `grep -c '6379/0' tests/unit/test_redis_flush_guard_prod.py` | match count == 0 |
| Flush-guard prod tests unchanged and green | `./scripts/pytest-clean.sh tests/unit/test_redis_flush_guard_prod.py -p no:randomly -q` | exit code 0 |
| Runtime backstop untouched (anti-criterion for No-Go #2645) | `git diff --name-only main -- tools/redis_flush_guard.py \| wc -l` | output contains 0 |
| `subprocess_env` still strips POPOTO_TEST_DB (#2628 invariant, anti-criterion for No-Go #2628) | `grep -c 'env.pop("POPOTO_TEST_DB", None)' tests/db_claim.py` | output contains 1 |
| `subprocess_env` not removed (anti-criterion for No-Go #2763) | `grep -c 'def subprocess_env' tests/db_claim.py` | output contains 1 |
| The `redis_test_url` fixture is still requestable by name | `grep -c 'def redis_test_url(request)' tests/conftest.py` | output contains 1 |
| Migration tests still green after the deletion | `./scripts/pytest-clean.sh tests/unit/test_migrate_strip_pid_fields.py -p no:randomly -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

Round 4 (re-critique of the round-3 revision), 2026-08-24. FULL depth, roster: Risk &
Robustness, Scope & Value, History & Consistency. Rounds 1-3 were addressed by their revisions
and are superseded by this table.

**Verdict: NEEDS REVISION.** One blocker, and it is not a wording problem: the Verification row
that is the *only* check on the REQUIRED Risk 1 mitigation is order-inverted and cannot go red.
Every direct `pytest_configure()` caller lives in a class defined *after* the class the plan
puts the probe assertions in, so under `-p no:randomly` the probe executes before any poisoning
can happen. Five concerns follow, all in instrumentation rather than design: the db0-delta
criterion conflates pytest's writes with db0's total mutation, the stop-and-escalate gate
pre-authorizes landing a red Verification row, round 3's replacement hostname row still fails
against the plan's own seam snippet, an anti-criterion contradicts a mandatory in-code comment,
and three of the four new test names are unmandated while the fourth is.

**No design decision is challenged.** The one-line export, the zero-arg
`_export_claimed_redis_url` seam, the `_reset_claim_state` stub, the 688-line guard deletion,
the accepted Risk 2 coverage loss, and all four RESOLVED Open Questions stand exactly as
written. The round-2 blocker (the `agent/` inventory) remains fixed — re-measured this round,
the inventory grep returns exactly 20.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | The Verification row "Claim-contract tests do not leak REDIS_URL across workers" is the sole verification of the REQUIRED Risk 1 mitigation, and it cannot go red. All four direct `_conftest.pytest_configure()` callers live in `TestSessionClaimHook` (class at line 1064; calls at 1098/1111/1128) and `TestRunProvenance` (class at 1212; call at 1220), while the plan puts the probe assertions in `TestPerProcessDbClaim` (class at 452, ends 858). Under `-p no:randomly` pytest collects in file order, so the probe runs *before* any synthetic hook call can poison `os.environ["REDIS_URL"]`. Passing the file plus a node ID from that same file does not change ordering — the node ID dedupes into the file's collection. This is precisely the property spike-1 finding F identified and this row was authored to eliminate. | pending | Measured this round: `grep -n '^class ' tests/unit/test_conftest_isolation_guards.py` → `452:TestPerProcessDbClaim`, `1064:TestSessionClaimHook`, `1212:TestRunProvenance`; `grep -n '_conftest.pytest_configure'` → 1098, 1111, 1128, 1220, all greater than 858. Cheapest correct fix: define the leak-detection probe in its own class placed at the END of the file (after `TestReloadedRegistryIdentity`, line 1265), e.g. `class TestExportedRedisUrlSurvivesSyntheticHookCalls`, asserting `os.environ["REDIS_URL"].endswith(f"/{_db_claim.claim_test_db()}")`. Keep the live-process assertions in `TestPerProcessDbClaim` — they check a different property. The revision must also require a demonstrated-red for this row itself (remove the `setattr` stub, run under `-n 2 --dist=each`, show the tail probe failing); a row never observed red is the exact failure mode this finding names. |
| CONCERN | Risk & Robustness | The headline db0 criterion demands an exact `delta of 0` in `redis-cli -n 0 dbsize` across a multi-minute eight-file run, against a database the plan itself describes as carrying "continuous live bridge/worker traffic" — the very property it cites to reject the full-`tests/unit/` version of the same measurement. Both cannot hold: if the traffic claim is true the equality is a coin flip; if it is false, round 3's grounds for narrowing the criterion do not hold. `dbsize` is also signed (TTL expiry makes the delta negative), so "delta of 0" is not the right shape for "pytest wrote nothing". No round has separated *pytest's writes to db0* from *db0's total mutation* — that conflation is why this finding keeps relocating instead of resolving. | pending | Two workable forms. (a) Attributed count: snapshot the key set the cluster's consumers write rather than total size — `redis-cli -n 0 --scan --pattern 'telegram:outbox:*'` piped to `wc -l`, plus the dedup and last-processed prefixes the six bridge-facing files touch — before and after, requiring an unchanged set on the branch. (b) Matched control: bracket an identical wall-clock window with no pytest running (`sleep <observed_run_seconds>`) by `dbsize`, call that delta `C`, and state the criterion as branch delta less than or equal to `C`, with `C` in the PR body beside the `main` delta. Measured while critiquing: `redis-cli -n 0 dbsize` returned 61843 twice ten seconds apart at idle, so the ambient floor is plausibly zero at idle and the "continuous traffic" phrasing needs correcting either way. Task 6's validator bullet "Confirm the db0 dbsize delta is 0 on the branch" must be reworded to match, or it becomes a flaky merge gate. |
| CONCERN | Scope & Value | The split contract pre-authorizes an outcome the plan elsewhere forbids. Task 2.5's gate says the split is "pre-authorized, only the trigger needed naming" and that "the export and the guard deletion still ship", while Verification requires the eight-file cluster to exit 0 — and the plan rejects both escapes, naming a db0 re-pin as an "anti-mitigation, explicitly rejected" and stating there are no `xfail` markers in `tests/` to convert. If the gate fires, the sanctioned path lands a merge with a knowingly-red Verification row and a red unit suite on `main`, in a repo that just spent issue #2904 on a red-main umbrella. "Stop and report" and "pre-authorized" point in opposite directions and leave the builder to choose. | pending | One edit to the task-2.5 gate bullet makes it decidable: replace "the split is pre-authorized, only the trigger needed naming. The export and the guard deletion still ship" with an explicit two-branch rule — (a) gate does not fire, repairs land in this PR; (b) gate fires, **stop, report to the PM, do not merge** until the PM chooses between (b1) absorbing the repairs anyway or (b2) splitting, in which case the follow-up issue is filed and its number is recorded in the deferred tests' disposition before merge. Either branch keeps the "Affected suites still pass" row at exit 0 at merge time — it is the only row that would catch a red-main landing, and Appetite currently overrides it in prose. Mirror the same wording into Appetite ("Appetite is at risk from exactly one thing") and Risk 7's Mitigation, so all three sites agree; a rule stated in one section and contradicted in another is what survived round 3. |
| CONCERN | History & Consistency | Round 3's replacement hostname row fails against the plan's own reference implementation — the same defect class as the `sed -n '288,298p'` row it replaced. The row runs `awk '/def _export_claimed_redis_url/,/^$/' tests/conftest.py` piped to `grep -c '127.0.0.1'` expecting greater than 0, but the seam snippet printed in Technical Approach has a **blank line inside its docstring**, and awk's range terminates there — never reaching the body or any comment after the docstring. A builder implementing the seam exactly as specified fails this row. | pending | Reproduced this round against a file carrying the plan's own snippet shape with the hostname rationale in the docstring body: the awk range emitted two lines and `grep -c '127.0.0.1'` returned 0, exit 1. Use a range anchored on the next top-level definition instead of on a blank line: `sed -n '/def _export_claimed_redis_url/,/^def \|^class /p' tests/conftest.py` piped to `grep -c '127.0.0.1'`, expecting greater than 0 — deterministic terminator, blank lines inside the docstring irrelevant. Confirm the replacement still goes red on the current tree (`grep -c 'def _export_claimed_redis_url' tests/conftest.py` is 0 today, so any correctly-anchored form returns 0). This row has now been rewritten twice and still cannot discharge its claim; the structural cause is that it verifies *prose placement* with a line-range grep. Consider asserting only that the seam exists and that `127.0.0.1` appears somewhere in `tests/conftest.py`, and moving the placement judgement to task 6's validator checklist where a human reads it. |
| CONCERN | History & Consistency | Two mandatory items contradict each other. Task 4 requires the fourth assertion to be commented "with the retired scanner's name" and Risk 2 requires "a comment naming the retired scanner" as the in-code record of the accepted coverage loss — while the Verification row greps `test_subprocess_test_db_isolation` across `tests/ docs/features/ CLAUDE.md` expecting **exit code 1**, i.e. zero matches under `tests/`. Writing the mandated comment turns the anti-criterion red. The builder must drop the comment (losing Risk 2's sole compensating documentation, the stated basis for accepting the 688-line deletion) or leave a red row. | pending | Verified on the tree: the grep currently returns exactly one hit, `tests/README.md:538`, which task 7 removes — so after task 7 the row is green only if no such comment is written. Fix by narrowing the anti-criterion to exclude the one deliberate historical citation and pairing it with a positive row that *requires* the comment: exclude `tests/unit/test_conftest_isolation_guards.py` from the recursive grep (expect exit 1) and add `grep -c 'test_subprocess_test_db_isolation' tests/unit/test_conftest_isolation_guards.py` expecting 1. Alternative: have task 4's comment name the retired *check* (`_env_value_is_clean`, verified at line 422) rather than the file, and say so in both task 4 and Risk 2. Do not resolve this by silently softening task 4 — Risk 2's argument is that the coverage loss is "documented in code rather than only in this plan". |
| CONCERN | History & Consistency | Round 3 closed the name-drift finding by mandating one exact test name (`test_non_splatting_env_drops_redis_url`) and left its three siblings unmandated. Three Verification rows invoke node IDs — `test_unguarded_child_inherits_claimed_db`, `test_nested_pytest_child_claims_its_own_db`, `test_live_process_redis_url_names_its_own_claim` — that no task requires be named that way, so a builder who names them otherwise gets pytest exit 4 or 5 on the rows that prove the plan's only remaining regression detectors work. Fail-closed rather than fail-open, but these four assertions replace a 688-line guard and the fix is one sentence. Same "fixed one occurrence, left the siblings" shape round 3 flagged as its own NIT. | pending | Add to task 4, beside the existing exact-name mandate: "the first three assertions must be named exactly `test_live_process_redis_url_names_its_own_claim`, `test_unguarded_child_inherits_claimed_db`, and `test_nested_pytest_child_claims_its_own_db` — Verification rows grep for these node IDs." Sequence this **after** the BLOCKER's class-placement decision: if the leak-detection probe moves to a new class at the end of the file, the node ID in the "does not leak REDIS_URL across workers" row moves with it, so fix the placement first and then pin all four names against the final layout. |

**Round-3 findings, confirmed still fixed against the tree at `4361e78f5`:** the lazy-consumer
inventory grep returns exactly 20 over `bridge/ tools/ reflections/ ui/ agent/ config/`;
`tests/unit/output_handler/` and `tests/unit/test_deliver_pipeline_completion.py` are in
both the task-2.5 cluster and the db0-delta Verification row; task 5 and task 6 both carry the
`build-blast-radius-sweep` edge and task 4 deliberately does not; the coverage-gap row is split
into two decidable rows with no BASIC-grep alternation; only the zero-arg seam arity appears
anywhere in the document; `_reset_claim_state` is described as 23 callers (re-measured:
`grep -c '_reset_claim_state'` = 25 = 23 calls + the definition at 498 + one docstring mention);
and `_env_value_is_clean` is cited at 422, which is exact.

**Structural checks performed this round (no finding):** all 24 referenced file paths exist;
the task graph has no numbering gap, no invalid `Depends On`, and no cycle; `redis-cli ping`
returns PONG; the guard file is 688 lines; `def redis_test_url(request)` is at conftest.py:937
and `from tests import db_claim` at conftest.py:18; `env.pop("POPOTO_TEST_DB", None)` appears
once in `tests/db_claim.py`; `monkeypatch.delenv("POPOTO_TEST_DB"` appears at 1096/1108/1126
(3, matching the row's "currently 3"); and `REDIS_URL` appears exactly 3 times in the guards
file (587, 1194, 1205), matching Risk 1's argument for why the file-wide count row was
unusable.

---

## Critique Disposition (operator decision, 2026-08-24)

**Planning is capped at round 4. The plan goes to build; no round 5.**

Rounds 1-2 earned their cost: they found the twenty lazy `REDIS_URL` consumers,
the `agent/session_completion.py:609` write into production db0 under test, and
the `delenv` mitigation that would have manufactured twenty-three fresh db0
windows. Rounds 3-4 moved to auditing the verification scaffolding this plan
invented, and round 4's own text records a finding that "keeps relocating
instead of resolving" — the signal that more rounds stop converging.

Disposition of round 4:

- **The BLOCKER is accepted and fixed in-plan** (probe-class placement; see task 4
  and the corresponding Verification row). It was a real catch: a row that cannot
  go red.
- **The five CONCERNs are accepted on the record and closed.** The db0-measurement
  concern is resolved by switching to an attributed key-set snapshot (task 2.5) —
  `dbsize` was the wrong instrument. The split-contract concern is resolved by
  removing the split entirely (see Appetite). The remainder are measurement
  precision, and they settle against real numbers from a build rather than
  another round of argument in a document.

Anything the build surfaces that contradicts this plan is a build-time finding,
recorded in the PR, not a reason to re-enter planning.

## Open Questions

**All four resolved 2026-08-24 — every proposed default is CONFIRMED. No PM
check-in outstanding; the builder proceeds on the defaults as written below.**

1. **CONFIRMED: DELETE.** A test that cannot go red is not a test. Its purpose
   is subsumed by the new unguarded-child assertion, which can.
2. **CONFIRMED: drop Consequence 1.** The measurement beats the issue body, and
   the body has been corrected on the issue
   ([comment 5393099353](https://github.com/tomcounsell/ai/issues/2805#issuecomment-5393099353)).
   Adding a db0 override to eight tests whose own docstring forbids constructing
   a db0 client would have made them lie. Carry the anti-criterion.
3. **CONFIRMED: `TestPerProcessDbClaim`.** It owns the claim contract; a new
   file would split it. The prominent comment on assertion 2 is required, not
   optional — a future reader must be told that the bare `subprocess.run` with
   no `env=` is the deliberate subject of the test and not an oversight, or
   someone will "fix" it.
4. **CONFIRMED: accept `127.0.0.1`.** Making the guarded and unguarded paths
   produce byte-identical URLs is worth more than preserving the ambient
   spelling, and a divergence in host between the two paths is exactly the kind
   of detail that costs an hour during a future incident. State it in the code
   comment as a deliberate choice.

### Original question text (retained for the reviewer)

1. **Deleting `test_the_subprocess_ran_against_the_test_db_not_production`** (`tests/unit/test_migrate_strip_pid_fields.py:411`): it can no longer go red once the export lands — three independent mechanisms would have to be removed. The plan proposes DELETE, with its purpose subsumed by the new unguarded-child assertion. The alternative is to keep it and accept a permanently-green test, which the repo's demonstrated-red principle argues against. Confirm DELETE.
2. **The issue's Consequence 1 is dropped.** Recon established that no subprocess in `tests/unit/test_redis_flush_guard_prod.py` connects to Redis — all eight spawning tests do import/attribute introspection only — so the proposed `env={**subprocess_env(), "REDIS_URL": ".../0"}` edit to five call sites is unnecessary and would misdescribe the tests. Confirm the drop, since the issue body asks for it explicitly.
3. **Home for the replacement assertions**: the plan puts all three in `tests/unit/test_conftest_isolation_guards.py::TestPerProcessDbClaim`, which already owns the claim contract, rather than a new file. The second assertion deliberately looks like the exact pattern the deleted guard used to reject (a bare `subprocess.run` with no `env=`), so it needs a prominent comment saying so. Confirm the placement.
4. **Hostname: accept `127.0.0.1` or preserve `localhost`?** `db_claim.redis_test_url()` defaults to `127.0.0.1` while the ambient production URL spells it `localhost`, so the export flips the host as a side effect (spike-1, measured harmless). The plan's default is to **accept `127.0.0.1`**, because that is already what `subprocess_env` hands children — making the guarded and unguarded paths produce identical URLs. The alternative, `db_claim.redis_test_url("localhost")`, changes only the db number and leaves the host untouched. Confirm the default.
