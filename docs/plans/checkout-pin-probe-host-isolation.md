---
status: Ready
type: bug
appetite: Small
owner: valor
created: 2026-09-07
tracking: https://github.com/tomcounsell/ai/issues/3201
last_comment_id: 5563793309
revision_applied: true
revision_applied_at: 2026-09-07T02:30:34Z
---

# Isolate the checkout-pin probe from the venv that ships the pin

## Problem

`tests/unit/test_checkout_pin.py::TestEndToEnd::test_worktree_script_imports_worktree_package_only_with_the_pin`
is red on `main`, and it is red for a reason that has nothing to do with the pin
being broken. The test proves the pin by running the same worktree script twice
— once with the pin's `.pth` written into a fake site dir, once without — and
asserting the two runs disagree. The "without" run is the whole proof: it
establishes that the worktree's package is *not* what a bare script would import
on its own.

That negative control runs through `sys.executable`, which is the repo venv, and
the repo venv's own `site-packages` now carries `_valor_checkout_pin.pth` —
the production copy of the very mechanism under test, installed fleet-wide by
`scripts/update/redis_flush_guard_pth.py` since #3141 landed in `b1cb7e793`.
The ambient `.pth` fires during the probe subprocess's `site` processing, sees
`sys.argv[0]` inside the fake linked worktree, and prepends the worktree root to
`sys.path` whether or not the test wrote its own. The control is no longer a
control.

The failure is therefore keyed to *install* time, not commit time: it goes red on
every checkout that has run `/update` since the pin shipped, and stays green only
on one that has not. A machine where this test passes is a machine missing the
feature.

**Current behavior:**

```
tests/unit/test_checkout_pin.py:216: in test_worktree_script_imports_worktree_package_only_with_the_pin
    assert _run_probe(site_dir, script, pinned=False) == "primary primary"
E   AssertionError: assert 'worktree worktree' == 'primary primary'
```

Reproduced 2026-09-07 at main `de229ee46`: `1 failed, 18 passed`.

**Desired outcome:**

The probe subprocess runs in an interpreter whose startup is governed only by the
test's own fake site dir. The negative control prints `primary primary` again,
the positive assertion keeps its meaning, and re-contamination from any future
ambient shim fails loudly with a message that names the leak instead of flipping
an expected string.

## Freshness Check

**Baseline commit:** `de229ee46`
**Issue filed at:** 2026-09-06T16:52:18Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `tests/unit/test_checkout_pin.py:216` — the failing negative-control assertion — still at line 216, still fails.
- `tests/unit/test_checkout_pin.py:154-178` — `_run_probe`, the helper that launches `sys.executable` and scrubs only `PYTHONPATH`/`PYTHONSAFEPATH`/`PYTHONNOUSERSITE` — unchanged.
- `tools/checkout_pin.py:95-116` — `pin()` — unchanged; it reads no environment at all.
- `scripts/update/redis_flush_guard_pth.py` — `_PIN_PTH_FILENAME` / `_PIN_PTH_CONTENT` installer — unchanged.

**Cited sibling issues/PRs re-checked:**
- #3141 — closed. Its implementation commit `b1cb7e793` is what introduced the contaminating install.
- #3206 — closed 2026-09-07T01:37:14Z as a duplicate of #3201, after reaching the same diagnosis from the nightly regression run at `25e4df925`. Its analysis is now a comment on #3201.
- #2600 — closed 2026-08-06, unrelated to this failure beyond the `Refs` line in the issue body.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since=2026-09-06T16:52:18Z -- tests/unit/test_checkout_pin.py tools/checkout_pin.py scripts/update/redis_flush_guard_pth.py` is empty across the 15 commits that landed in that window.

**Active plans in `docs/plans/` overlapping this area:** none. No plan document mentions `checkout_pin`, #3141, or #3201.

**Notes:** The bug reproduces at current `main`, so the premise holds in full.

## Prior Art

- **#3141 / `b1cb7e793`**: "Pin bare scripts to the checkout they live in via a startup `.pth` shim" — succeeded at its own goal, and is the direct cause of this test failure. The pin works; installing it fleet-wide removed the only environment in which its own negative control could hold.
- **#3206**: "Nightly regression: `tests/unit/test_checkout_pin.py::TestEndToEnd::...`" — closed as a duplicate of #3201. Its triage independently identified the ambient `.pth` and proposed three fixes. This plan adopts a corrected form of its first proposal and rejects its second.
- **#2603 / #2605 / PR #2606**: "Repair two shared-state leaks that make the suite's failure set unreproducible" — same failure family (a test measuring the host rather than the code), different mechanism (in-process shared state, not interpreter startup). No shared code.
- **#2748 / PR #2882**: "Doctor console-script check: verify the winning script's interpreter" — prior art for the general lesson that on this machine the interpreter a command resolves to is not the interpreter you assumed. No shared code.
- **#3195** (open): "`pytest-clean.sh` exits 0 when zero tests ran (pool exhausted, node down): mutation checks read false green" — `scripts/pytest-clean.sh:311` is a bare `exit "$PYTEST_EXIT"` pass-through, so a run that collected nothing still exits 0. This plan's Verification rows therefore state an expected passed count rather than resting on the exit code; the wrapper does not yet fail closed.

No prior attempt to fix *this* test exists; it has been red since the pin was installed and has never been patched.

## Research

**Queries used:**
- `python sys._base_executable venv stability documented attribute`

**Key findings:**
- `sys._base_executable` is private and undocumented, and CPython has declined to promote it. It can be **absent**, can **equal `sys.executable`** inside a venv (reported on 3.10/Linux), can point at an **invalid path** for venvs built with `--copies` ([python/cpython#99204](https://github.com/python/cpython/issues/99204)), and can point at a **non-Python host binary** under embedded interpreters. The defensive pattern in the wild is `getattr(sys, "_base_executable", None) or sys.executable` *plus* an existence check ([python/cpython#114476](https://github.com/python/cpython/issues/114476), [pypa/pipx#1074](https://github.com/pypa/pipx/issues/1074)).
- The documented way to reason about a venv is the prefix pair: `sys.prefix != sys.base_prefix` ([venv docs](https://docs.python.org/3.12/library/venv.html)).

**How this informs the approach:** it demotes the "just run the probe under the base interpreter" strategy from primary to rejected alternative. That strategy measured correctly here (spike-1), but its correctness rests on a private attribute with four known failure modes — on a machine where it degrades to `sys.executable`, the test silently returns to measuring the host. The bootstrap strategy this plan adopts depends only on documented CPython behavior (`-S`, `-P`, `runpy`) and on `site.addsitedir`, which the test already uses.

Exactly one isolation mechanism ships: **an explicit bootstrap script run under `-S -P`**. `sys._base_executable` is not added alongside it, and no environment kill-switch is added to `pin()`; both live in Rabbit Holes.

## Spike Results

### spike-1: A clean interpreter restores the negative control
- **Assumption**: "The failure is ambient contamination from the venv's `site-packages`, not a defect in `pin()`."
- **Method**: prototype (scratch harness reproducing `_run_probe` verbatim)
- **Finding**: through `sys._base_executable` (uv's `cpython-3.14`, whose `site-packages` holds only pip) the harness prints `primary primary` unpinned and `worktree worktree` pinned. Through `sys.executable` it prints `worktree worktree` both times. Separately, a hand-built fake worktree script run through `.venv/bin/python` with no test `.pth` anywhere lands the lane root at `sys.path[1]`.
- **Confidence**: high
- **Impact on plan**: confirms the diagnosis and fixes the shape of the repair — isolate the interpreter, do not touch `pin()`.

### spike-2: the bootstrap, not `-S`, is what defeats the ambient pin
- **Assumption**: "#3206's `-S` proposal works as written."
- **Method**: prototype
- **Finding**: it does not work as written — `-S` is precisely what stops `site.py` from importing `sitecustomize`, so the test's current `sitecustomize` bootstrap never runs. A bootstrap **script** passed as `argv[1]` does work: `python -S boot.py <script> <site_dir>` where `boot.py` sets `sys.argv = [script]`, calls `site.addsitedir(site_dir)`, and then runs the script. Restoring `sys.argv` before `addsitedir` is load-bearing: leave that line out and `pin()` sees the bootstrap's path, which is outside any checkout, and no-ops — the *positive* assertion fails instead of the negative one.
- **Confidence**: high
- **Impact on plan**: this becomes the chosen mechanism, with the exact ordering pinned in Technical Approach.

### spike-2b: which part of the invocation carries the isolation (re-measured for the critique)
- **Assumption**: "`-S` is what defeats the ambient `_valor_checkout_pin.pth`, so deleting `-S` is a mutation that turns the negative control red."
- **Method**: prototype, on **this** machine (`.venv` carries `_valor_checkout_pin.pth`, CPython 3.14.6), same fixtures as the test
- **Finding**: **false.** Measured unpinned / pinned output per invocation:

  | Invocation | unpinned | pinned |
  |---|---|---|
  | `[python, -S, -P, boot, script, site_dir]` (proposed) | `primary primary False` | `worktree worktree True` |
  | drop `-S` (`[python, -P, boot, ...]`) | `primary primary False` | `worktree worktree True` |
  | drop `-S` and `-P` (`[python, boot, ...]`) | `primary primary False` | `worktree worktree True` |
  | drop `sys.argv = [target]` from the bootstrap | `primary primary False` | **`primary primary False`** |
  | revert to `[python, script]` + `sitecustomize` on `PYTHONPATH` | **`worktree worktree True`** | `worktree worktree True` |

  The ambient `.pth` runs during `site` processing, at which point `sys.argv[0]` is `boot.py` — a path outside any `valor-bridge` checkout — so `checkout_root_of()` returns `None` and `pin()` no-ops. The bootstrap defeats the ambient pin by *existing as the argv[0] the child starts with*; `-S` never gets the chance to matter for this particular shim.
- **Confidence**: high (directly measured, both directions, five invocations)
- **Impact on plan**: `-S`'s rationale is restated as hermeticity against the whole ambient `site-packages` (any future shim that does not read `argv[0]` — an unconditional `sys.path` append, a `sitecustomize` in the venv — would still leak without it). The mutation check becomes the full revert, the one row measured red. A second, complementary mutation is available and also measured red: deleting `sys.argv = [target]` flips the *positive* assertion.

### spike-2c: `-P` removes the bootstrap's own directory from the child's path
- **Assumption**: "The bootstrap's directory on `sys.path` is harmless."
- **Method**: prototype, this machine
- **Finding**: without `-P`, `tmp_path` (which also holds the fixture dirs `primary/` and `site/`) sits on the child's `sys.path`, and the sibling test's `sys.path[1]` becomes that bootstrap directory — a value real CPython startup never produces. With `-P` added, `tmp_path` is gone from `sys.path` entirely; the worktree pair still prints `primary primary False` / `worktree worktree True`, and the sibling test's `sys.path[1]` becomes `<uv python>/lib/python314.zip`, which is what real startup places there. `-P` requires 3.11+; `.python-version` pins 3.14.6.
- **Confidence**: high
- **Impact on plan**: `-P` is passed as a flag on the same invocation (it is the same switch as `PYTHONSAFEPATH`, but a flag cannot be lost by a later env-scrub refactor). This is hardening of the one mechanism, not a second mechanism.

### spike-3: the bootstrap reproduces CPython's own `sys.path[0]`
- **Assumption**: "Dropping real interpreter startup costs the sibling test its meaning."
- **Method**: prototype
- **Finding**: real CPython inserts the script's directory *after* `site` processing — measured directly, the ambient pin's root lands at `sys.path[1]`, behind the script dir. A bootstrap that inserts `dirname(script)` at index 0 between `addsitedir` and the run reproduces that order. Under `-S -P` (spike-2c) the ordering matches real startup for **both** end-to-end tests: the worktree probe prints `primary primary False` / `worktree worktree True`, and the primary probe's `sys.path[1]` is the stdlib zip rather than the bootstrap's directory.
- **Confidence**: high
- **Impact on plan**: with `-P` adopted the "reproduces that order" claim holds for both tests. Without `-P` it holds only for the worktree probe, which is why `-P` is not optional here.

### spike-4: the ambient-pin guard needs resolved paths
- **Assumption**: "The negative control can assert 'no ambient pin fired' by testing `str(worktree) in sys.path`."
- **Method**: prototype
- **Finding**: it reports `False` even when the pin *did* fire, because `pin()` inserts `os.path.realpath(...)` and macOS `tmp_path` is a symlink (`/var/...` vs `/private/var/...`). The guard must compare against `worktree.resolve()`.
- **Confidence**: high
- **Impact on plan**: the guard assertion is specified against resolved paths, so it cannot pass vacuously.

### spike-5: the sibling test can compare the whole path, not one constant slot
- **Assumption**: "`test_primary_script_is_unaffected_by_the_pin` can only assert on `sys.path[1]`."
- **Method**: prototype, this machine
- **Finding**: with the probe changed from `print(agentx.WHICH, sys.path[1])` to `print(agentx.WHICH, sys.path)`, the two runs (fake pin `.pth` present vs. absent) produce byte-identical six-element lists. The comparison is deterministic across runs: same interpreter, same fixtures, sequential subprocesses. Under `-S -P` the list is `[<script dir>, <stdlib zip>, <stdlib>, <lib-dynload>, <fake site dir>, <primary checkout>]`.
- **Confidence**: high
- **Impact on plan**: the sibling test stops asserting that one constant equals itself and starts asserting the thing it is named for — the pin shim ran and added nothing. One changed expression; no new mechanism.

1. **Entry point**: `_run_probe(site_dir, script, pinned=...)` decides whether the fake pin `.pth` exists in the fake site dir, then launches a subprocess.
2. **Interpreter startup**: today `sys.executable` runs `site.py`, which processes the venv's real `site-packages` (`_valor_checkout_pin.pth` → `pin()` → worktree root onto `sys.path`) and then imports `sitecustomize` from `PYTHONPATH`, which calls `site.addsitedir(fake_site_dir)` and processes the test's `.pth` files.
3. **Fake site dir**: `_editable_impl_fake.pth` adds the *primary* checkout (standing in for the venv's editable install); `_valor_checkout_pin.pth` (present only when `pinned`) adds the *script's* checkout; `zzz_early_probe.pth` imports `agentx` at site time to capture what an early importer sees.
4. **Script dir**: CPython inserts `dirname(script)` at `sys.path[0]`.
5. **Output**: the probe prints `agentx.WHICH` and the early importer's `SEEN`.

The repair replaces step 2 alone. Steps 3–5 keep their current shape, and the fake site dir stays the only thing that decides what the probe sees.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| `b1cb7e793` (#3141) | Shipped `tools/checkout_pin.py` and installed it into every repo venv | It was correct and it works. Its test was written against a venv that did not yet carry the shim, so the negative control was only ever satisfiable before the feature reached the machine running it. The gap is that the test never asserted its own isolation. |

**Root cause pattern:** a test that measures a property of the interpreter it happens to be running under, while the feature under test modifies exactly that interpreter. Shipping the feature invalidates the test's baseline. The durable fix is not a different expected string — it is an isolation boundary plus an assertion that the boundary held.

## Architectural Impact

- **New dependencies**: none. Stdlib `site`, `runpy`, `subprocess`.
- **Interface changes**: `_run_probe` (a module-private test helper) gains a bootstrap; no production signature moves.
- **Coupling**: reduces it. The test stops depending on the ambient venv's `site-packages` contents.
- **Data ownership**: unchanged.
- **Reversibility**: trivial — one test file, one commit.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (the diagnosis is settled and evidence-backed)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. The one environmental
fact it relies on, `_valor_checkout_pin.pth` being present in the venv, is what
makes the test meaningful and is asserted as a Verification row rather than
assumed.

## Solution

### Key Elements

- **A bootstrap-driven probe** (the one isolation mechanism): `_run_probe` hands the child a generated bootstrap script that reproduces the three startup steps the test cares about — `sys.argv`, the fake site dir's `.pth` processing, and the script-directory insert. Because the child's `argv[0]` is the bootstrap (a path outside any `valor-bridge` checkout), the ambient `_valor_checkout_pin.pth` no-ops during `site` processing, and the fake site dir becomes the only thing that can pin anything (spike-2b).
- **`-S -P` as hardening on that same invocation**: `-S` skips the venv's `site-packages` wholesale, so a *future* ambient shim that does not consult `argv[0]` (an unconditional `sys.path` append, a venv `sitecustomize`) cannot leak in either. `-P` keeps the bootstrap's own directory off the child's path so the sibling test measures real startup ordering (spike-2c). Neither flag is what fixes today's bug; both are cheap and both are stated for what they do.
- **A restored negative control**: with no ambient shim reachable, `pinned=False` measures only what the fake site dir set up.
- **An isolation guard**: the probe reports whether the script's own checkout root reached `sys.path`, and the negative control asserts it did not. Re-contamination then fails with "an ambient pin fired" rather than an expected-string flip.

### Flow

`_run_probe(pinned=False)` → child starts on the bootstrap under `-S -P`; the ambient `.pth` is not processed at all, and would no-op even if it were → bootstrap sets `sys.argv = [script]` → `site.addsitedir(fake_site_dir)` runs the test's `.pth` files (no pin present) → bootstrap inserts `dirname(script)` at `sys.path[0]` → script imports `agentx` → prints `primary primary False` → assertion passes on all three fields.

### Technical Approach

- Generate the bootstrap into the test's `tmp_path` (not `site_dir`, which is scanned for `.pth` files). Its body, in this exact order:
  1. read `target, site_dir` from `sys.argv[1:3]`
  2. `sys.argv = [target]` — before anything processes a `.pth`, because `pin()` reads `argv[0]`
  3. `site.addsitedir(site_dir)`
  4. `sys.path.insert(0, os.path.dirname(os.path.abspath(target)))` — CPython's own step, which happens after `site`
  5. `runpy.run_path(target, run_name="__main__")`
- Invoke as `[sys.executable, "-S", "-P", str(boot), str(script), str(site_dir)]`. Keeping `sys.executable` keeps the interpreter version identical to the one running the suite. Pass `-P` as a flag rather than restoring `PYTHONSAFEPATH` (the same switch) so an env-scrub refactor cannot silently drop it.
- Scrub the child env of every `PYTHON*` variable and set `PYTHONNOUSERSITE=1`. `PYTHONPATH` is no longer needed at all — the bootstrap replaces the `sitecustomize` hop — so the `sitecustomize` file and its `customize/` directory come out of both end-to-end tests.
- Extend the probe script in `test_worktree_script_imports_worktree_package_only_with_the_pin` to print a third field: whether `str(worktree.resolve())` is in `sys.path`. Assert `"primary primary False"` unpinned and `"worktree worktree True"` pinned. Compare resolved paths (spike-4) so the guard cannot pass vacuously on macOS.
- Change the probe in `test_primary_script_is_unaffected_by_the_pin` from `print(agentx.WHICH, sys.path[1])` to `print(agentx.WHICH, sys.path)` (spike-5), so the equality assertion compares the whole search path instead of one slot that is constant for reasons unrelated to `pin()`.
- Leave `tools/checkout_pin.py` and `scripts/update/redis_flush_guard_pth.py` untouched. The production pin is correct; only its test's environment was wrong.
- Add a comment above `_run_probe` recording *why* the bootstrap is mandatory and what each flag buys: the venv running this suite ships the shim under test, an ordinary child measures the shim twice and the control never, the bootstrap's `argv[0]` is what disarms that shim, and `-S -P` close the same door against shims that do not read `argv[0]`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No exception handlers in scope. `_run_probe` already asserts `proc.returncode == 0` with `proc.stderr` as the message, and the bootstrap deliberately carries no `try`/`except` — a broken bootstrap must surface as a non-zero exit with a traceback, not as a silently degraded probe.

### Empty/Invalid Input Handling
- [ ] A bootstrap that receives a missing `site_dir` or `target` must fail loudly; `site.addsitedir` on a non-existent directory is a silent no-op, so the test asserts on probe *output* rather than on the call, and the ambient-pin field makes a silently empty site dir visible (the unpinned probe would fail to import `agentx` and exit non-zero).
- [ ] Not agent-output processing; no empty-output loop risk.

### Error State Rendering
- [ ] The failure mode this plan cares about is a *wrong pass*, not a wrong render. The guard field is the render: on re-contamination the assertion diff names the ambient pin instead of showing two package labels.
- [ ] **Mutation M1 — negative control (the primary check).** Revert `_run_probe` to the pre-fix invocation `[sys.executable, str(script)]`, restore `PYTHONPATH=str(site_dir / "customize")` on the child env, and temporarily re-create `site_dir/customize/sitecustomize.py` in the test body. Re-run `tests/unit/test_checkout_pin.py::TestEndToEnd::test_worktree_script_imports_worktree_package_only_with_the_pin`. **Measured red on this machine at head `c9b2995ef`:** `AssertionError: assert 'which=worktree early_probe_seen=worktree worktree_in_syspath=True script_dir_precedes_pin_root=True' == 'which=primary early_probe_seen=primary worktree_in_syspath=False script_dir_precedes_pin_root=None'`. Restore, re-run, confirm green. Paste both outputs into the PR. M1 is a three-part hand mutation (invocation, `PYTHONPATH`, `customize/sitecustomize.py`); a partially applied M1 also goes red, but with a different failure (e.g. `KeyError: '_early_probe'` or a non-zero exit through `_run_probe`'s `assert proc.returncode == 0`). Any red whose text is not the exact diff above means M1 was mis-applied, not that the guard bit — re-check the three-part edit before recording the row as proved.
- [ ] **Mutation M2 — positive assertion (complementary check).** Delete the `sys.argv = [target]` line from the generated bootstrap, leaving the invocation otherwise untouched. **Measured red on this machine:** the pinned probe prints `which=primary early_probe_seen=primary worktree_in_syspath=False script_dir_precedes_pin_root=None` where `which=worktree early_probe_seen=worktree worktree_in_syspath=True script_dir_precedes_pin_root=True` is expected, because `pin()` then reads the bootstrap's own path. This proves the bootstrap's argv rewrite is load-bearing rather than decorative. Restore and re-run.
- [ ] **Do not use "delete `-S`" as a mutation.** It was measured on this machine and does **not** bite: the child still prints `primary primary False` unpinned and `worktree worktree True` pinned, because the ambient shim is disarmed by the bootstrap's `argv[0]`, not by `-S` (spike-2b). A validator reporting that mutation green would be recording a false green on this plan's central claim.

## Test Impact

- [ ] `tests/unit/test_checkout_pin.py::TestEndToEnd::test_worktree_script_imports_worktree_package_only_with_the_pin` — UPDATE: probe prints a third field, both assertions gain the ambient-pin expectation, `sitecustomize` scaffolding removed. Round-2 review added a labeled fourth field (`script_dir_precedes_pin_root`) guarding the bootstrap's `site.addsitedir`-before-`sys.path.insert` line order, all four fields now individually labeled.
- [ ] `tests/unit/test_checkout_pin.py::TestEndToEnd::test_primary_script_is_unaffected_by_the_pin` — UPDATE: same `_run_probe` change reaches it; `sitecustomize` scaffolding removed; probe changed from `sys.path[1]` to the whole `sys.path` (spike-5). Note what this test is and is not: it is an **equality-under-no-op** check — it proves the pin shim ran and changed nothing for a script inside the venv's own checkout — not a positive proof that the pin fires. The positive proof lives in the worktree test and in `TestPinDecision`. Under the bootstrap without `-P`, its old `sys.path[1]` was the bootstrap's own directory, a constant identical on both sides for reasons unrelated to `pin()`; `-P` plus the whole-path comparison is what restores its meaning.
- [ ] `tests/unit/test_checkout_pin.py` `_run_probe` helper and its docstring — UPDATE: bootstrap-driven, with the `-S` rationale recorded.
- [ ] `tests/unit/test_checkout_pin.py::TestPinDecision`, `::TestDeclaresProject` — no change. They drive `pin()` with explicit `argv`/`path` lists and never start an interpreter.
- [ ] `tests/unit/test_redis_flush_guard_pth_installer.py` — no change. It asserts installer file contents, never interpreter startup.

## Rabbit Holes

- **Adding an env kill-switch to `pin()`.** #3206's second option. It puts a production foot-gun (a variable that silently disables checkout isolation fleet-wide) into `tools/checkout_pin.py`, which today reads no environment at all, to solve a problem that lives entirely in the test.
- **Building a throwaway venv per probe.** Correct and hermetic, and it turns a 7-second file into a minute-plus of `uv venv` per parametrization for no additional proof.
- **Switching to (or adding) `sys._base_executable`.** Measured working (spike-1), but four documented failure modes mean it can silently degrade to `sys.executable` on some machine and quietly restore the bug this plan is closing. It is not carried alongside the bootstrap either — exactly one isolation mechanism ships, and a second one that can silently degrade would make it harder, not easier, to tell which one is holding.
- **Auditing all 72 test files that spawn `sys.executable`.** The pin only fires for a script whose nearest `.git` ancestor declares `valor-bridge`, and `test_checkout_pin.py` is the only test that builds such a fake checkout. Re-deriving that across the whole suite buys nothing.
- **"Fixing" the assertion by flipping the expected string to `worktree worktree`.** That is what the contaminated environment already produces; it would make the test green and meaningless.

## Risks

### Risk 1: `-S` drops something the probe silently needed
**Impact:** The child cannot import `agentx`, or `runpy` behaves differently from a real script run, and the test fails for a new reason.
**Mitigation:** Measured end-to-end in spike-2 and spike-3 against the real fixtures: both end-to-end scenarios produce the expected strings, and the sibling test still compares equal. The probe imports nothing outside stdlib and the fake site dir.

### Risk 2: The bootstrap drifts from real interpreter startup
**Impact:** The test proves a simulation rather than the mechanism, and a future change to `site` ordering goes unnoticed.
**Mitigation:** The bootstrap reproduces the two ordering facts the pin depends on — `sys.argv` set before `.pth` processing, script dir inserted after it — and both are asserted, not assumed: mutation M2 shows the positive assertion goes red when `argv` is wrong (measured). The `site.addsitedir`-before-`sys.path.insert` ordering is guarded directly by a fourth, labeled field on the worktree probe (`script_dir_precedes_pin_root`), added in review round 2 after measuring that neither end-to-end test's original assertions actually caught the two `_BOOTSTRAP` lines being swapped (`TestEndToEnd` stayed `2 passed`) — the sibling test's whole-`sys.path` comparison does not exercise this, because its `pin()` call is always a no-op (its checkout root is already on `sys.path` via the editable-install `.pth`, so there is never an `insert(0, ...)` for the swap to reorder). With `-P` the ordering matches real startup (verified empirically against a real venv + `.pth`: the script's own directory precedes anything a `.pth`'s `sys.path.insert(0, ...)` adds). The decision table in `TestPinDecision` covers `pin()`'s logic independently.

### Risk 3: The guard field passes vacuously
**Impact:** A future re-contamination goes unnoticed because the guard compares unresolved paths and always reports `False`.
**Mitigation:** spike-4 caught exactly this; the guard compares `worktree.resolve()`. Mutation M1 in Failure Path Test Strategy proves the guard bites before review — measured red at main `78447df87` as `assert 'worktree worktree True' == 'primary primary False'`, which flips the guard field as well as both labels.

## Race Conditions

No race conditions identified. Both end-to-end tests are single-threaded, run their subprocesses sequentially with `subprocess.run`, and write only inside their own `tmp_path`. The two `_run_probe` calls in one test share a fake site dir but are strictly ordered, and the pin `.pth` is written or unlinked before each launch.

## No-Gos (Out of Scope)

No acceptance criterion is deferred — every relevant item is in scope for this
plan. The five rejected alternative approaches (an env kill-switch on `pin()`,
a throwaway venv per probe, `sys._base_executable`, auditing all 72
`sys.executable`-spawning test files, and flipping the expected string) live in
Rabbit Holes with the reason each was rejected; they are considered and
excluded, not deferred to a later plan.

## Update System

No update system changes required. This is a test-only fix; no dependency, config
file, or migration propagates to other machines. `scripts/update/redis_flush_guard_pth.py`
keeps installing the pin exactly as it does today — that install is the correct
production behavior and is what the repaired test now tolerates.

## Agent Integration

No agent integration required. Nothing here is reachable from the bridge, and no
CLI entry point or MCP surface changes.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/worktree-venv-isolation.md` (the `### Bare scripts from a worktree (#3141)` section, around line 238): the sentence describing what `tests/unit/test_checkout_pin.py` proves must also say that the probe runs through an explicit bootstrap script under `-S -P`, because the venv running the suite ships the shim under test and would otherwise contaminate the negative control. State the causal order correctly: the bootstrap is what disarms the ambient pin (its `argv[0]` is outside any checkout); `-S -P` are hermeticity against shims that do not read `argv[0]`.
- [ ] No `docs/features/README.md` index change — the feature already has its entry.

### External Documentation Site
- [ ] Not applicable; this repo publishes no external docs site.

### Inline Documentation
- [ ] Comment above `_run_probe` recording why the bootstrap is mandatory, naming the ambient `_valor_checkout_pin.pth` as the contaminant, and saying separately what `-S` and `-P` each buy — so a later reader does not repeat the mistake of assuming `-S` is what defeats this shim (spike-2b).
- [ ] Update the module docstring's claim that the end-to-end test proves the mechanism "at the moment it matters (`site` processing)" so it stays accurate under the bootstrap.

## Success Criteria

- [ ] `tests/unit/test_checkout_pin.py` is green on a checkout whose venv carries `_valor_checkout_pin.pth` (i.e. any machine that has run `/update` since #3141), with the summary line reading **`19 passed`** — the count, not the exit code, is the proof (#3195)
- [ ] **Mutation M1 is measured red and pasted into the PR**: reverting `_run_probe` to `[sys.executable, str(script)]` plus the `sitecustomize` hop makes the negative control fail with `assert 'which=worktree early_probe_seen=worktree worktree_in_syspath=True script_dir_precedes_pin_root=True' == 'which=primary early_probe_seen=primary worktree_in_syspath=False script_dir_precedes_pin_root=None'`, and restoring it returns the summary line to `19 passed`. This, not the removal of `-S`, is the proof that the isolation is load-bearing
- [ ] **Mutation M2 is measured red and pasted into the PR**: deleting `sys.argv = [target]` from the bootstrap makes the *positive* assertion fail (`which=primary early_probe_seen=primary worktree_in_syspath=False script_dir_precedes_pin_root=None` where `which=worktree early_probe_seen=worktree worktree_in_syspath=True script_dir_precedes_pin_root=True` is expected)
- [ ] `tools/checkout_pin.py` and `scripts/update/redis_flush_guard_pth.py` are byte-identical to `origin/main`
- [ ] Verified on **this** machine — the one that has run `/update` and carries the production `_valor_checkout_pin.pth` in `.venv/lib/python3.14/site-packages/`. A green run in an environment without that file proves nothing and does not satisfy any row above
- [ ] Documentation updated (`/do-docs`)
- [ ] No xfail markers exist for this bug — none were found; nothing to convert

## Team Orchestration

Not applicable at this appetite. This is a Small, single-file test fix whose four
tasks form a strict chain — nothing here is independently schedulable, so a
named multi-agent roster would be ceremony rather than parallelism. One builder
carries all four tasks; the mutation checks are steps in that chain, not a
separate role.

## Step by Step Tasks

### 1. Rewrite the probe helper
- **Task ID**: build-probe-isolation
- **Depends On**: none
- **Validates**: tests/unit/test_checkout_pin.py
- **Informed By**: spike-2 / spike-2b (the bootstrap's `argv[0]` is what disarms the ambient pin; `sys.argv = [target]` must precede `addsitedir`), spike-2c (`-P`), spike-3 (script-dir insert reproduces CPython), spike-4 (guard needs `resolve()`), spike-5 (whole-`sys.path` comparison)
- **Parallel**: false
- Write the bootstrap generator into `tmp_path`, outside `site_dir`
- Change `_run_probe` to launch `[sys.executable, "-S", "-P", boot, script, site_dir]` with every `PYTHON*` var scrubbed and `PYTHONNOUSERSITE=1`
- Drop the `sitecustomize` scaffolding from both end-to-end tests
- Add the third probe field and the ambient-pin assertions, comparing `worktree.resolve()`
- Change the primary probe to print the whole `sys.path`
- Add the rationale comment (bootstrap first, then what `-S` and `-P` each buy) and refresh the module docstring

### 2. Prove the isolation is load-bearing (both mutations)
- **Task ID**: validate-probe-isolation
- **Depends On**: build-probe-isolation
- **Parallel**: false
- Confirm the ambient pin is present first: `ls .venv/lib/python*/site-packages/_valor_checkout_pin.pth` must exit 0. If it is absent, **stop** — every measurement below is meaningless on that machine. Recovery: `python -m scripts.update.redis_flush_guard_pth --venv <path>` installs it, then re-run the `ls` check; the gate stays fail-closed but the stop is recoverable, not a dead end.
- Baseline: `./scripts/pytest-clean.sh tests/unit/test_checkout_pin.py -n 0 -q`, read the summary line, require **`19 passed`**
- **Mutation M1**: revert `_run_probe` to `[sys.executable, str(script)]`, restore `env["PYTHONPATH"] = str(site_dir / "customize")` and the `customize/sitecustomize.py` write in the worktree test, re-run the single node id, require a **failure** whose diff is `assert 'which=worktree early_probe_seen=worktree worktree_in_syspath=True script_dir_precedes_pin_root=True' == 'which=primary early_probe_seen=primary worktree_in_syspath=False script_dir_precedes_pin_root=None'`. Restore; re-run; require `19 passed`
- **Mutation M2**: delete `sys.argv = [target]` from the bootstrap, re-run the single node id, require a **failure** on the *pinned* assertion (`which=primary early_probe_seen=primary worktree_in_syspath=False script_dir_precedes_pin_root=None` where `which=worktree early_probe_seen=worktree worktree_in_syspath=True script_dir_precedes_pin_root=True` is expected). Restore; re-run; require `19 passed`
- Do **not** run "delete `-S`" as a mutation and do not report it as evidence — it is measured green in both directions (spike-2b)
- Confirm `git diff --name-only origin/main -- tools/ scripts/` is empty
- Record every summary line and both red diffs verbatim for the PR body

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-probe-isolation
- **Parallel**: false
- Update the `#3141` section of `docs/features/worktree-venv-isolation.md` per the Documentation section

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Parallel**: false
- Run every Verification row and read the passed count off each summary line
- Confirm all Success Criteria

## Verification

Every pytest row states the **passed count** rather than resting on the exit
code. `scripts/pytest-clean.sh:311` is a bare `exit "$PYTEST_EXIT"` pass-through
(#3195, open), so a run that collected nothing exits 0; a plan whose deliverable
*is* a verification mechanism cannot accept that as proof. Read the count off
pytest's summary line and record it. `tests/unit/test_checkout_pin.py` collects
19 tests, 2 of them in `TestEndToEnd` (verified by `--collect-only` at main
`78447df87`); neither class uses `scratch_test_db`, so the test-DB pool-exhaustion
skip channel is not reachable here and a mistyped node id exits 5.

| Check | Command | Expected |
|-------|---------|----------|
| Contamination source is present (makes every row below meaningful) | `ls .venv/lib/python*/site-packages/_valor_checkout_pin.pth` | exit code 0. **If this fails, stop** — a green suite on a machine without the ambient pin is not evidence |
| Checkout-pin tests pass | `./scripts/pytest-clean.sh tests/unit/test_checkout_pin.py -n 0 -q` | summary line reads `19 passed` |
| End-to-end pair passes under the ambient pin | `./scripts/pytest-clean.sh "tests/unit/test_checkout_pin.py::TestEndToEnd" -n 0 -q` | summary line reads `2 passed` |
| Mutation M1 (negative control bites) | Apply M1 per Failure Path Test Strategy, then `./scripts/pytest-clean.sh "tests/unit/test_checkout_pin.py::TestEndToEnd::test_worktree_script_imports_worktree_package_only_with_the_pin" -n 0` | summary line reads `1 failed`, diff is `assert 'which=worktree early_probe_seen=worktree worktree_in_syspath=True script_dir_precedes_pin_root=True' == 'which=primary early_probe_seen=primary worktree_in_syspath=False script_dir_precedes_pin_root=None'` |
| Mutation M2 (positive assertion bites) | Apply M2 per Failure Path Test Strategy, then re-run the same node id | summary line reads `1 failed`, pinned probe printed `which=primary early_probe_seen=primary worktree_in_syspath=False script_dir_precedes_pin_root=None` |
| Restored after each mutation | `./scripts/pytest-clean.sh tests/unit/test_checkout_pin.py -n 0 -q` | summary line reads `19 passed` |
| Production pin untouched | `git diff --name-only origin/main -- tools/checkout_pin.py scripts/update/redis_flush_guard_pth.py` | no output |
| No env kill-switch added to the pin | `! grep -q 'environ\|getenv' tools/checkout_pin.py` | exit code 0 (`grep -c` would print `0` and exit **1**, which a uniform exit-code reader records as a failure) |
| Lint clean | `python -m ruff check tests/unit/test_checkout_pin.py` | exit code 0 |
| Format clean | `python -m ruff format --check tests/unit/test_checkout_pin.py` | exit code 0 |

## Critique Results

**Round 2** — READY TO BUILD (no concerns). 0 blockers, 0 concerns, 4 nits.

**Round 1 closure, re-measured rather than accepted.** All seven Round 1 rows were
re-verified against the plan body and against this machine (`.venv` carries the
production `_valor_checkout_pin.pth`, CPython 3.14.6), using a harness reproducing
the plan's exact bootstrap and invocation:

| Round 1 row | Independent re-measurement | Closed |
|---|---|---|
| BLOCKER — the specified mutation does not bite | Proposed fix prints `primary primary False` / `worktree worktree True`. **M1** (revert to `[python, script]` + the `sitecustomize` hop) prints `worktree worktree True` unpinned — RED in the negative control. **M2** (drop `sys.argv = [target]`) prints `primary primary False` pinned — RED in the positive assertion. Both replacement mutations measured red, in the directions the plan states. | yes |
| CONCERN — bootstrap dir stays on the child's path | Under `-S -P`, `tmp_path` is absent from the child's `sys.path` and the sibling probe's `sys.path[1]` is `<uv python>/lib/python314.zip`; under `-S` alone `tmp_path` sits at index 1. spike-2c holds. | yes |
| CONCERN — Verification rows rest on exit code | Every pytest row now states a count. `--collect-only` confirms 19 tests, 2 in `TestEndToEnd`. `scripts/pytest-clean.sh` still ends on a bare `exit "$PYTEST_EXIT"`, so the counts are the right proof. | yes |
| NIT — sibling test stops measuring the pin | Whole-`sys.path` comparison measured byte-identical across the pinned/unpinned pair. Test Impact names it an equality-under-no-op check. | yes |
| NIT — #3195 missing from Prior Art | Present with state re-verified OPEN on `tomcounsell/ai`. | yes |
| NIT — `grep -c` exits 1 on the passing case | Row is now `! grep -q ...`; verified exit 0, while `grep -c` prints `0` and exits 1. | yes |
| NIT — Team Orchestration ceremony | No `Assigned To` / `Agent Type` lines survive in any of the four tasks; the section states the reason for the collapse. | yes |

**Round 2 findings.** Nits only; none blocks the build.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| NIT | Risk & Robustness | Task 2's stop condition ("if `_valor_checkout_pin.pth` is absent, stop") names no remedy. A lane building in a worktree whose venv was provisioned before the flush-guard self-heal ran would halt with nothing to do next. | yes | Recovery added inline in Step by Step Tasks: `python -m scripts.update.redis_flush_guard_pth --venv <path>`, then re-run the `ls` check. Fail-closed gate stays; the stop is now recoverable. (#3211 review round 2) |
| NIT | Risk & Robustness | M1 is a three-part hand mutation (invocation, `PYTHONPATH`, `customize/sitecustomize.py`). A partially applied M1 also goes red, but with `KeyError: '_early_probe'` and a non-zero exit through `_run_probe`'s `assert proc.returncode == 0` — a different red that could be recorded as the expected one. | yes | The Failure Path Test Strategy's M1 row now states explicitly: any red whose text is not the exact pinned diff means M1 was mis-applied, not that the guard bit. (#3211 review round 2) |
| NIT | Scope & Value | The production-pin fence (Success Criteria and Verification) compares against the local `main` ref, which drifts from `origin/main` over a lane's life. | yes | Success Criteria, Step by Step Tasks, and Verification all now compare against `origin/main`. (#3211 review round 2) |
| NIT | History & Consistency | No-Gos says "Nothing deferred — every relevant item is in scope," while Rabbit Holes lists five explicitly deferred items. Separately, the baselines `78447df87` and `de229ee46` are commits from the #3183 lane, used as "main's head at measurement time"; a reader who resolves them lands on unrelated ETL plan work. | yes | No-Gos now says the five rejected approaches live in Rabbit Holes with reasons and are excluded, not deferred; the four bare `78447df87`/`de229ee46` citations are now labeled "main `<sha>`". (#3211 review round 2) |

---

## Open Questions

None. The diagnosis is reproduced, the two candidate mechanisms were measured
end-to-end, and the choice between them turns on documented CPython behavior
rather than on a judgment call that needs a human.
