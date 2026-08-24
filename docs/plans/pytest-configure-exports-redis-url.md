---
status: Planning
type: bug
appetite: Small
owner: valor
created: 2026-08-24
tracking: https://github.com/tomcounsell/ai/issues/2805
last_comment_id: 5392635119
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

`docs/plans/fix-red-main-unit-tests.md` (issue #2904, `status: docs_complete`,
CLOSED 2026-08-24). It lists this exact test as row #3 of a red-main umbrella
and scopes it **out**: "owned by #2805; do not re-diagnose or touch it here."
Coordination is clean; no contention, no shared files.

**Notes:** Two of the issue body's four stated Consequences are factually wrong
and are corrected in Technical Approach below. One breakage the body does not
mention is the plan's hard blocker.

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
- **Confidence**: high
- **Impact on plan**: Removed Consequence 1 from scope entirely and added an
  anti-criterion asserting no db0 override is introduced. Promoted the
  `test_conftest_isolation_guards.py` poisoning to the plan's only hard blocker.
  Made the tautology an explicit disposition rather than a silent survivor.

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
- **Data ownership**: unchanged. `tests/db_claim.py` remains the sole source of
  the claimed db number; this plan adds one more consumer of it, in the hook
  that already claims.
- **Reversibility**: trivial for the one-line export (revert one line). The
  guard deletion is a `git revert` away but should not be reverted piecemeal —
  the guard and the export are one decision.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirming the two dispositions in Open Questions)
- Review rounds: 1

The code change is one line plus three deletions. The cost is entirely in
proving the removal is safe — the demonstrated-red bar and the nested-pytest
invariant.

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
  inherits a correct database instead of a production one.
- **The restorable-write fix**: `tests/unit/test_conftest_isolation_guards.py`'s
  `_reset_claim_state` helper takes ownership of `REDIS_URL` via `monkeypatch`
  so that direct `pytest_configure()` invocations against a synthetic claim
  registry cannot poison the live session — one edit in one helper, covering
  every present and future caller.
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

- **Add one line to `tests/conftest.py::pytest_configure`,** immediately after
  line 289 and before anything can spawn a child:

  ```python
  os.environ["POPOTO_TEST_DB"] = str(db)
  os.environ["REDIS_URL"] = db_claim.redis_test_url()
  ```

  **Spelling is already available and collision-free.** `tests/conftest.py:18`
  carries `from tests import db_claim`, so the module-qualified call needs no
  new import and cannot shadow the *fixture* also named `redis_test_url` at
  conftest.py:938 — which nine test modules request by name and which must not
  be renamed. Do **not** add `from tests.db_claim import redis_test_url`.

- **Do not attempt to fix the parent process's popoto client.** popoto's
  `pytest11` plugin resolves `REDIS_URL` before `tests/conftest.py` is even
  imported (spike-2 finding 5). The existing `redis_test_db` autouse swap
  already handles the parent. The export is for children. Say so in the code
  comment; do not claim defense-in-depth the change does not provide.

- **Make the direct-invocation write restorable — this is required, not
  optional.** Extend
  `tests/unit/test_conftest_isolation_guards.py::_reset_claim_state` (lines
  496-540) to take `monkeypatch` ownership of `REDIS_URL` before returning,
  alongside the `POPOTO_TEST_DB` handling that already lives there. That way
  whatever the synthetic `pytest_configure()` call writes is restored at
  teardown. Doing it in the helper — rather than at each of lines 1098, 1111,
  1128, 1220 — means the next test that calls the hook directly is protected
  without anyone remembering.

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
     the claimed db — this is the permanent regression test for the whole issue,
     and it is *supposed* to look like a guard violation;
  3. a nested pytest child spawned **without** `env=` claims a **different** db
     than its parent (the #2628 invariant), proving the inherited `REDIS_URL` is
     overwritten by the child's own claim rather than leaked.
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
- [ ] `REDIS_PORT` unset → `redis_test_url` falls back to `"6379"` (`db_claim.py:314`). Confirm the composed URL is well-formed with `REDIS_PORT` unset, empty, and set.
- [ ] The xdist controller path returns at line 282 without claiming; confirm it writes **no** `REDIS_URL` at all (rather than an empty or malformed one).
- [ ] Confirm the exported URL is never `.../0` under any code path — a claim of db 0 is impossible by pool definition `[1..15]`, but assert it, since a silent 0 is the entire incident class.

### Error State Rendering
- [ ] If a child does land on db0 despite the fix, `tools/redis_flush_guard.py` raises `RuntimeError` at the flush — verify that path is still reachable and its message still names the cause, since it is now the only remaining enforcement layer for that failure.
- [ ] Verify the conftest flush guard's "not a claimed db" error (`tests/conftest.py:217-226`) still fires with its diagnostic intact.

## Test Impact

- [ ] `tests/unit/test_subprocess_test_db_isolation.py` (entire file, 688 lines, 9 tests) — **DELETE**: the convention it enforces no longer exists. No module imports it.
- [ ] `tests/unit/test_conftest_isolation_guards.py::TestPerProcessDbClaim::_reset_claim_state` (lines 496-540) — **UPDATE**: take `monkeypatch` ownership of `REDIS_URL` so the three direct `pytest_configure()` callers (lines 1111, 1128, 1220) cannot poison the live session.
- [ ] `tests/unit/test_conftest_isolation_guards.py::TestPerProcessDbClaim` — **UPDATE (add)**: three new assertions (live-process export, unguarded-child inheritance, nested-pytest own-claim).
- [ ] `tests/unit/test_migrate_strip_pid_fields.py::TestSubprocessCapture::test_the_subprocess_ran_against_the_test_db_not_production` (line 411) — **DELETE**: three independent mechanisms would now have to be removed for it to go red, so it can no longer fail. Its purpose is subsumed by the new unguarded-child assertion, which tests the same property at the layer that owns it. Its docstring at line 416 ("`REDIS_URL` is unset on this machine by default") is already false today.
- [ ] `tests/unit/test_migrate_strip_pid_fields.py` module docstring (lines 29-32) — **UPDATE**: it explains why every test in the file sets `REDIS_URL`; that reasoning changes.
- [ ] `tests/unit/test_redis_bootstrap.py` module docstring (line 7) — **UPDATE**: "Empty/missing `REDIS_URL`: falls back to 127.0.0.1:6379/db=0" describes a pydantic `Field(default=...)`, not an env read (`REDIS__URL` is the actual seam). Pre-existing drift, corrected while adjacent.
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
the fix meant to strengthen it. This is the plan's only hard blocker.
**Measured, not theorised.** spike-1 reproduced it: under `-n 2 --dist=each`,
`gw1`'s real claim was db2 but its `os.environ["REDIS_URL"]` read db1 for the
rest of the session, and its subprocess wrote into `gw0`'s database. The guards
file itself reported **44 passed** — the leak is invisible from inside it,
because under `-n0` the stub claim also picks db1 and masks the divergence.
**Mitigation:** `_reset_claim_state` takes `monkeypatch` ownership of
`REDIS_URL` before returning, so any write by the synthetic hook call is
restored at teardown. Fixing it in the shared helper rather than at the four
call sites (1098, 1111, 1128, 1220) means a fifth caller is protected
automatically. **Verification must run under `-n 2 --dist=each` with the guards
file and a db-reading probe in the same run**; a green `-n0` single-file run is
not evidence.

### Risk 2: Removing the only static enforcement leaves a coverage gap for a case the new tests miss
**Impact:** If the export is ever reverted, narrowed, or shadowed, nothing
scans for unguarded call sites any more, and the failure is silent until a
production row is written.
**Mitigation:** The new unguarded-child assertion is a *behavioral* regression
test: it spawns a bare subprocess with no `env=` and asserts the claimed db, so
it goes red the instant the export stops working — regardless of which call
site or which spawn mechanism is involved. That is strictly broader coverage
than an AST scan of five named `subprocess.*` functions. The runtime backstops
(`tools/redis_flush_guard.py` on db0 flush; conftest's claimed-db flush guard)
remain and are the layers that actually fail closed.

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
**Impact:** `tests/conftest.py` defines a fixture `redis_test_url` at line 938;
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
**Mitigation:** `monkeypatch` ownership in `_reset_claim_state` (Risk 1). This
is the one genuine ordering hazard the change introduces.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2645] Changing, weakening, or re-scoping `tools/redis_flush_guard.py` or the server-side Redis ACL. They are the runtime backstops this plan explicitly relies on remaining intact; they are shipped under #2645 and are not touched here.
- [SEPARATE-SLUG #2628] Altering the `POPOTO_TEST_DB`-is-not-inherited rule in `subprocess_env`, or the flock claim pool itself. This plan proves the invariant still holds; it does not modify the machinery that provides it.
- [SEPARATE-SLUG #2763] Removing `subprocess_env` or its `REDIS_URL` pin. The pin becomes redundant, not wrong, and the helper's `PYTHONPATH` half is a live, separate concern with existing `project_root=` callers.
- [SEPARATE-SLUG #2904] The two other red-main failures (`test_sdlc_dispatch.py`, `test_sdlc_meta_set.py`). Owned and shipped by #2904's plan; not touched here.

## Update System

No update system changes required. This is a test-harness-internal change: one
line in `tests/conftest.py`, one file deletion under `tests/unit/`, and
documentation. It adds no dependency, no config file, no secret, no entry point,
and no migration. `scripts/update/` and the `/update` skill are untouched, and
nothing needs to propagate to other machines beyond the ordinary `git pull` that
`/update` already performs.

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
- [ ] Review `CLAUDE.md`'s "Manual Testing Hygiene" paragraph: its claim that "this shell always carries a production `REDIS_URL`" stays true for shells but must not read as true inside pytest. Its `os.environ.setdefault` warning and the "assign explicitly, assert the db number" rule remain correct for standalone debug scripts and are unaffected — draw the shell/pytest line clearly rather than deleting the guidance.
- [ ] Correct `docs/sdlc/do-build.md:215` ("## Test Isolation"): it tells builders to "use `REDIS_TEST_DB` or a separate prefix". `REDIS_TEST_DB` is not a variable this repo has — the mechanism is the flock claim plus `POPOTO_TEST_DB`, and after this change the environment is correct with no builder action at all. Pre-existing drift, surfaced by the blast-radius scan, and directly misleading for every future build.

### External Documentation Site
- [ ] Not applicable — this repo has no external docs site.

### Inline Documentation
- [ ] Comment the new `pytest_configure` line with *why* (children inherit by construction) and its one honest limitation (popoto's plugin has already resolved; this is not defense-in-depth for the parent).
- [ ] Update `tests/db_claim.py::subprocess_env`'s docstring (lines 318-349): its `REDIS_URL` paragraph now describes a redundant belt over a correct environment, and the `POPOTO_TEST_DB` paragraph's "mirror image" framing needs restating.
- [ ] Update `tests/unit/test_migrate_strip_pid_fields.py`'s module docstring (lines 29-32).
- [ ] Correct `tests/unit/test_redis_bootstrap.py`'s module docstring (line 7).

## Success Criteria

- [ ] `tests/conftest.py::pytest_configure` exports `REDIS_URL` naming this process's claimed db, immediately after the `POPOTO_TEST_DB` export and after the claim-failure guard.
- [ ] A plain `subprocess.run([sys.executable, "-c", ...])` with **no `env=`**, launched from a test, resolves `REDIS_URL` to the claimed db — asserted by a permanent test in `TestPerProcessDbClaim`.
- [ ] **Demonstrated red:** the same probe, run with the export reverted, is shown landing on db0, and that output is pasted into the PR description. A green-only run does not discharge this.
- [ ] A nested pytest child spawned without `env=` claims a **different** db than its parent (#2628 invariant), asserted by a permanent test.
- [ ] Under `-n N`, each xdist worker's unguarded child lands on **that worker's own** claimed db; the controller exports nothing.
- [ ] `tests/unit/test_subprocess_test_db_isolation.py` no longer exists, and no reference to it survives in `tests/` or `docs/features/`.
- [ ] `tests/unit/test_conftest_isolation_guards.py` passes, and — verified under `-n 2 --dist=each`, not `-n0` — each worker's `REDIS_URL` still names *its own* claimed db after the file has run.
- [ ] The hostname decision (`127.0.0.1` vs `localhost`) is made deliberately and recorded in the code comment, not left as a side effect.
- [ ] `tests/unit/test_watchdog_log_isolation.py` and `tests/unit/test_sdlc_next_skill.py` are **unchanged in the diff**, and their children land on the claimed db.
- [ ] `tests/unit/test_redis_flush_guard_prod.py` is unchanged and still proves the db0 flush guard fires.
- [ ] The now-tautological `test_the_subprocess_ran_against_the_test_db_not_production` is deleted, not left green-and-unfalsifiable.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No `xfail`/`xpass` conversions needed — none exist in `tests/`.

## Team Orchestration

### Team Members

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
- Add `os.environ["REDIS_URL"] = <db_claim's redis_test_url()>` immediately after `tests/conftest.py:289`, using a non-colliding import spelling
- Confirm it sits after the `pytest.exit` claim-failure guard, so an exhausted pool writes nothing
- Confirm the xdist-controller early return at line 282 still writes nothing
- Comment the *why* and the honest limitation (not defense-in-depth for the parent)

### 2. Make direct `pytest_configure()` invocations restorable
- **Task ID**: build-restorable-write
- **Depends On**: build-env-export
- **Validates**: tests/unit/test_conftest_isolation_guards.py
- **Informed By**: spike-2 (direct callers at 1098, 1111, 1128, 1220 against a tmp_path registry); spike-1 (the leak is real and `-n0` masks it — the file reports 44 passed either way)
- **Assigned To**: env-export-builder
- **Agent Type**: builder
- **Parallel**: false
- Extend `_reset_claim_state` (lines 496-540) to take `monkeypatch` ownership of `REDIS_URL`
- **Verify under `-n 2 --dist=each`** with the guards file and a db-reading probe in the same run; confirm each worker's `os.environ["REDIS_URL"]` still names *its own* claim afterwards
- Do not accept a green `-n0` single-file run as evidence

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
- **Task ID**: test-replacement-assertions
- **Depends On**: build-restorable-write
- **Validates**: tests/unit/test_conftest_isolation_guards.py
- **Informed By**: spike-2 (this file already owns the claim contract; no new test file needed)
- **Assigned To**: isolation-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Assert the live process's `os.environ["REDIS_URL"]` ends with `/{claim_test_db()}`
- Assert a deliberately unguarded child resolves to the claimed db
- Assert a nested pytest child spawned without `env=` claims a **different** db (#2628) — the nested target **must live under the repo root**, or pytest picks a different rootdir, never loads `tests/conftest.py`, and the assertion falsely passes (spike-1 hit this)
- Verify each under `-n 2 --dist=each` so per-worker independence is covered
- Remember `scripts/pytest-clean.sh` defaults to xdist and swallows `-s`; pass `-n0` or write probe output to a file when you need to read it
- Cover the `REDIS_PORT` unset/empty/set cases from Failure Path Test Strategy

### 5. Delete the AST guard and retire the tautology
- **Task ID**: build-delete-guard
- **Depends On**: test-replacement-assertions
- **Validates**: tests/unit/
- **Informed By**: spike-2 (no module imports the guard; the migrate test can no longer go red)
- **Assigned To**: env-export-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `tests/unit/test_subprocess_test_db_isolation.py` in full
- Delete `tests/unit/test_migrate_strip_pid_fields.py::test_the_subprocess_ran_against_the_test_db_not_production` and correct that file's module docstring
- Correct `tests/unit/test_redis_bootstrap.py`'s module docstring
- Leave the four previously-violating call sites and `test_redis_flush_guard_prod.py` untouched

### 6. Validate the isolation contract
- **Task ID**: validate-isolation
- **Depends On**: build-delete-guard, test-demonstrated-red
- **Assigned To**: isolation-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm the red output in the PR body is a genuine db0 landing, not a fabricated string
- Confirm the four call sites and `test_redis_flush_guard_prod.py` are absent from the diff
- Confirm no `6379/0` literal was introduced anywhere in `tests/`
- Confirm the runtime backstops are untouched and still fire
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
- Correct `docs/sdlc/do-build.md:215`'s stale `REDIS_TEST_DB` reference
- Draw the shell-vs-pytest line in `CLAUDE.md`'s Manual Testing Hygiene paragraph without deleting its still-correct debug-script guidance
- Update `subprocess_env`'s docstring
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

| Check | Command | Expected |
|-------|---------|----------|
| Export present in pytest_configure | `grep -c 'os.environ\["REDIS_URL"\]' tests/conftest.py` | output contains 1 |
| AST guard file is gone | `ls tests/unit/test_subprocess_test_db_isolation.py` | exit code != 0 |
| No surviving reference to the retired guard | `grep -rn 'test_subprocess_test_db_isolation' tests/ docs/features/ CLAUDE.md` | exit code 1 |
| Claim-contract tests pass | `./scripts/pytest-clean.sh tests/unit/test_conftest_isolation_guards.py -p no:randomly -q` | exit code 0 |
| Claim-contract tests do not leak REDIS_URL across workers (the `-n0`-masked hazard) | `./scripts/pytest-clean.sh tests/unit/test_conftest_isolation_guards.py -p no:randomly -n 2 --dist=each -q` | exit code 0 |
| `_reset_claim_state` owns REDIS_URL (Risk 1 mitigation present) | `grep -c 'REDIS_URL' tests/unit/test_conftest_isolation_guards.py` | output > 2 |
| Export uses the non-shadowing spelling | `grep -c 'db_claim.redis_test_url()' tests/conftest.py` | output contains 1 |
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

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

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
