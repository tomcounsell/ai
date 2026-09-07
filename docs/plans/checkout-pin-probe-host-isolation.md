---
status: Planning
type: bug
appetite: Small
owner: valor
created: 2026-09-07
tracking: https://github.com/tomcounsell/ai/issues/3201
last_comment_id: 5563793309
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

Reproduced 2026-09-07 at `de229ee46`: `1 failed, 18 passed`.

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

No prior attempt to fix *this* test exists; it has been red since the pin was installed and has never been patched.

## Research

**Queries used:**
- `python sys._base_executable venv stability documented attribute`

**Key findings:**
- `sys._base_executable` is private and undocumented, and CPython has declined to promote it. It can be **absent**, can **equal `sys.executable`** inside a venv (reported on 3.10/Linux), can point at an **invalid path** for venvs built with `--copies` ([python/cpython#99204](https://github.com/python/cpython/issues/99204)), and can point at a **non-Python host binary** under embedded interpreters. The defensive pattern in the wild is `getattr(sys, "_base_executable", None) or sys.executable` *plus* an existence check ([python/cpython#114476](https://github.com/python/cpython/issues/114476), [pypa/pipx#1074](https://github.com/pypa/pipx/issues/1074)).
- The documented way to reason about a venv is the prefix pair: `sys.prefix != sys.base_prefix` ([venv docs](https://docs.python.org/3.12/library/venv.html)).

**How this informs the approach:** it demotes the "just run the probe under the base interpreter" strategy from primary to rejected alternative. That strategy measured correctly here (spike-1), but its correctness rests on a private attribute with four known failure modes — on a machine where it degrades to `sys.executable`, the test silently returns to measuring the host. The `-S` strategy depends only on documented CPython behavior and on `site.addsitedir`, which the test already uses.

## Spike Results

### spike-1: A clean interpreter restores the negative control
- **Assumption**: "The failure is ambient contamination from the venv's `site-packages`, not a defect in `pin()`."
- **Method**: prototype (scratch harness reproducing `_run_probe` verbatim)
- **Finding**: through `sys._base_executable` (uv's `cpython-3.14`, whose `site-packages` holds only pip) the harness prints `primary primary` unpinned and `worktree worktree` pinned. Through `sys.executable` it prints `worktree worktree` both times. Separately, a hand-built fake worktree script run through `.venv/bin/python` with no test `.pth` anywhere lands the lane root at `sys.path[1]`.
- **Confidence**: high
- **Impact on plan**: confirms the diagnosis and fixes the shape of the repair — isolate the interpreter, do not touch `pin()`.

### spike-2: `-S` plus an explicit bootstrap is hermetic
- **Assumption**: "#3206's `-S` proposal works as written."
- **Method**: prototype
- **Finding**: it does not work as written — `-S` is precisely what stops `site.py` from importing `sitecustomize`, so the test's current `sitecustomize` bootstrap never runs. A bootstrap **script** passed as `argv[1]` does work: `python -S boot.py <script> <site_dir>` where `boot.py` sets `sys.argv = [script]`, calls `site.addsitedir(site_dir)`, and then runs the script. Restoring `sys.argv` before `addsitedir` is load-bearing: leave it and `pin()` sees the bootstrap's path, which is outside any checkout, and no-ops — the positive assertion would fail instead of the negative one.
- **Confidence**: high
- **Impact on plan**: this becomes the chosen mechanism, with the exact ordering pinned in Technical Approach.

### spike-3: the bootstrap can reproduce CPython's own `sys.path[0]`
- **Assumption**: "Dropping real interpreter startup costs the sibling test its meaning."
- **Method**: prototype
- **Finding**: real CPython inserts the script's directory *after* `site` processing — measured directly, the ambient pin's root lands at `sys.path[1]`, behind the script dir. A bootstrap that inserts `dirname(script)` at index 0 between `addsitedir` and the run reproduces that order exactly. Under it, `test_primary_script_is_unaffected_by_the_pin` still compares equal pinned vs. unpinned on `sys.path[1]`, and the end-to-end test prints `primary primary` / `worktree worktree`.
- **Confidence**: high
- **Impact on plan**: no fidelity is lost, so nothing has to be split off or weakened.

### spike-4: the ambient-pin guard needs resolved paths
- **Assumption**: "The negative control can assert 'no ambient pin fired' by testing `str(worktree) in sys.path`."
- **Method**: prototype
- **Finding**: it reports `False` even when the pin *did* fire, because `pin()` inserts `os.path.realpath(...)` and macOS `tmp_path` is a symlink (`/var/...` vs `/private/var/...`). The guard must compare against `worktree.resolve()`.
- **Confidence**: high
- **Impact on plan**: the guard assertion is specified against resolved paths, so it cannot pass vacuously.

## Data Flow

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

- **A bootstrap-driven probe**: `_run_probe` launches the child with `-S`, so the ambient `site-packages` is never processed, and hands it a generated bootstrap script that reproduces the three startup steps the test actually cares about — `sys.argv`, the fake site dir's `.pth` processing, and the script-directory insert.
- **A restored negative control**: with no ambient shim reachable, `pinned=False` measures only what the fake site dir set up.
- **An isolation guard**: the probe reports whether the script's own checkout root reached `sys.path`, and the negative control asserts it did not. Re-contamination then fails with "an ambient pin fired" rather than an expected-string flip.

### Flow

`_run_probe(pinned=False)` → child starts with `-S`, no ambient `site-packages` → bootstrap sets `sys.argv = [script]` → `site.addsitedir(fake_site_dir)` runs the test's `.pth` files (no pin present) → bootstrap inserts `dirname(script)` at `sys.path[0]` → script imports `agentx` → prints `primary primary`, `ambient_pin=False` → assertion passes on both fields.

### Technical Approach

- Generate the bootstrap into the test's `tmp_path` (not `site_dir`, which is scanned for `.pth` files). Its body, in this exact order:
  1. read `target, site_dir` from `sys.argv[1:3]`
  2. `sys.argv = [target]` — before anything processes a `.pth`, because `pin()` reads `argv[0]`
  3. `site.addsitedir(site_dir)`
  4. `sys.path.insert(0, os.path.dirname(os.path.abspath(target)))` — CPython's own step, which happens after `site`
  5. `runpy.run_path(target, run_name="__main__")`
- Invoke as `[sys.executable, "-S", str(boot), str(script), str(site_dir)]`. `-S` is what drops the venv's `site-packages`; keeping `sys.executable` keeps the interpreter version identical to the one running the suite.
- Scrub the child env of every `PYTHON*` variable and set `PYTHONNOUSERSITE=1`. `PYTHONPATH` is no longer needed at all — the bootstrap replaces the `sitecustomize` hop — so the `sitecustomize` file and its `customize/` directory come out of both end-to-end tests.
- Extend the probe script in `test_worktree_script_imports_worktree_package_only_with_the_pin` to print a third field: whether `str(worktree.resolve())` is in `sys.path`. Assert `"primary primary False"` unpinned and `"worktree worktree True"` pinned. Compare resolved paths (spike-4) so the guard cannot pass vacuously on macOS.
- Leave `tools/checkout_pin.py` and `scripts/update/redis_flush_guard_pth.py` untouched. The production pin is correct; only its test's environment was wrong.
- Add a short comment above `_run_probe` recording *why* `-S` is mandatory: the venv running this suite ships the shim under test, so an ordinary child would measure the shim twice and the control never.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No exception handlers in scope. `_run_probe` already asserts `proc.returncode == 0` with `proc.stderr` as the message, and the bootstrap deliberately carries no `try`/`except` — a broken bootstrap must surface as a non-zero exit with a traceback, not as a silently degraded probe.

### Empty/Invalid Input Handling
- [ ] A bootstrap that receives a missing `site_dir` or `target` must fail loudly; `site.addsitedir` on a non-existent directory is a silent no-op, so the test asserts on probe *output* rather than on the call, and the ambient-pin field makes a silently empty site dir visible (the unpinned probe would fail to import `agentx` and exit non-zero).
- [ ] Not agent-output processing; no empty-output loop risk.

### Error State Rendering
- [ ] The failure mode this plan cares about is a *wrong pass*, not a wrong render. The guard field is the render: on re-contamination the assertion diff names the ambient pin instead of showing two package labels.
- [ ] Confirm by mutation before review: delete `-S` from the invocation and re-run; the test must fail on the ambient-pin field, not merely on the label. Paste that red output into the PR.

## Test Impact

- [ ] `tests/unit/test_checkout_pin.py::TestEndToEnd::test_worktree_script_imports_worktree_package_only_with_the_pin` — UPDATE: probe prints a third field, both assertions gain the ambient-pin expectation, `sitecustomize` scaffolding removed.
- [ ] `tests/unit/test_checkout_pin.py::TestEndToEnd::test_primary_script_is_unaffected_by_the_pin` — UPDATE: same `_run_probe` change reaches it; `sitecustomize` scaffolding removed. Its `sys.path[1]` assertion holds under the bootstrap (spike-3) and stays as-is.
- [ ] `tests/unit/test_checkout_pin.py` `_run_probe` helper and its docstring — UPDATE: bootstrap-driven, with the `-S` rationale recorded.
- [ ] `tests/unit/test_checkout_pin.py::TestPinDecision`, `::TestDeclaresProject` — no change. They drive `pin()` with explicit `argv`/`path` lists and never start an interpreter.
- [ ] `tests/unit/test_redis_flush_guard_pth_installer.py` — no change. It asserts installer file contents, never interpreter startup.

## Rabbit Holes

- **Adding an env kill-switch to `pin()`.** #3206's second option. It puts a production foot-gun (a variable that silently disables checkout isolation fleet-wide) into `tools/checkout_pin.py`, which today reads no environment at all, to solve a problem that lives entirely in the test.
- **Building a throwaway venv per probe.** Correct and hermetic, and it turns a 7-second file into a minute-plus of `uv venv` per parametrization for no additional proof.
- **Switching to `sys._base_executable`.** Measured working (spike-1), but four documented failure modes mean it can silently degrade to `sys.executable` on some machine and quietly restore the bug this plan is closing.
- **Auditing all 72 test files that spawn `sys.executable`.** The pin only fires for a script whose nearest `.git` ancestor declares `valor-bridge`, and `test_checkout_pin.py` is the only test that builds such a fake checkout. Re-deriving that across the whole suite buys nothing.
- **"Fixing" the assertion by flipping the expected string to `worktree worktree`.** That is what the contaminated environment already produces; it would make the test green and meaningless.

## Risks

### Risk 1: `-S` drops something the probe silently needed
**Impact:** The child cannot import `agentx`, or `runpy` behaves differently from a real script run, and the test fails for a new reason.
**Mitigation:** Measured end-to-end in spike-2 and spike-3 against the real fixtures: both end-to-end scenarios produce the expected strings, and the sibling test still compares equal. The probe imports nothing outside stdlib and the fake site dir.

### Risk 2: The bootstrap drifts from real interpreter startup
**Impact:** The test proves a simulation rather than the mechanism, and a future change to `site` ordering goes unnoticed.
**Mitigation:** The bootstrap reproduces exactly the two ordering facts the pin depends on — `sys.argv` set before `.pth` processing, script dir inserted after it — and both are asserted, not assumed: the positive assertion fails if `argv` is wrong (spike-2), and the sibling test's `sys.path[1]` fails if the insert order is wrong (spike-3). The decision table in `TestPinDecision` covers `pin()`'s logic independently.

### Risk 3: The guard field passes vacuously
**Impact:** A future re-contamination goes unnoticed because the guard compares unresolved paths and always reports `False`.
**Mitigation:** spike-4 caught exactly this; the guard compares `worktree.resolve()`. The mutation check in Failure Path Test Strategy (delete `-S`, expect a red on the guard field) proves the guard bites before review.

## Race Conditions

No race conditions identified. Both end-to-end tests are single-threaded, run their subprocesses sequentially with `subprocess.run`, and write only inside their own `tmp_path`. The two `_run_probe` calls in one test share a fake site dir but are strictly ordered, and the pin `.pth` is written or unlinked before each launch.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan.

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
- [ ] Update `docs/features/worktree-venv-isolation.md` (the `### Bare scripts from a worktree (#3141)` section, around line 238): the sentence describing what `tests/unit/test_checkout_pin.py` proves must also say that the probe runs under `-S` with an explicit bootstrap, because the venv running the suite ships the shim under test and would otherwise contaminate the negative control.
- [ ] No `docs/features/README.md` index change — the feature already has its entry.

### External Documentation Site
- [ ] Not applicable; this repo publishes no external docs site.

### Inline Documentation
- [ ] Comment above `_run_probe` recording why `-S` and the bootstrap are mandatory, naming the ambient `_valor_checkout_pin.pth` as the contaminant.
- [ ] Update the module docstring's claim that the end-to-end test proves the mechanism "at the moment it matters (`site` processing)" so it stays accurate under the bootstrap.

## Success Criteria

- [ ] `tests/unit/test_checkout_pin.py` is fully green on a checkout whose venv carries `_valor_checkout_pin.pth` (i.e. any machine that has run `/update` since #3141)
- [ ] The negative control asserts, and would fail on, an ambient pin firing — proven by deleting `-S` and pasting the red output into the PR
- [ ] `tools/checkout_pin.py` and `scripts/update/redis_flush_guard_pth.py` are byte-identical to `main`
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No xfail markers exist for this bug — none were found; nothing to convert

## Team Orchestration

### Team Members

- **Builder (test isolation)**
  - Name: `pin-probe-builder`
  - Role: rewrite `_run_probe` and the two end-to-end tests
  - Agent Type: test-engineer
  - Resume: true

- **Validator (isolation proof)**
  - Name: `pin-probe-validator`
  - Role: run the mutation check and confirm the guard bites
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Rewrite the probe helper
- **Task ID**: build-probe-isolation
- **Depends On**: none
- **Validates**: tests/unit/test_checkout_pin.py
- **Informed By**: spike-2 (bootstrap ordering: `sys.argv` before `addsitedir`), spike-3 (script-dir insert reproduces CPython), spike-4 (guard needs `resolve()`)
- **Assigned To**: pin-probe-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Write the bootstrap generator into `tmp_path`, outside `site_dir`
- Change `_run_probe` to launch `[sys.executable, "-S", boot, script, site_dir]` with every `PYTHON*` var scrubbed and `PYTHONNOUSERSITE=1`
- Drop the `sitecustomize` scaffolding from both end-to-end tests
- Add the third probe field and the ambient-pin assertions, comparing `worktree.resolve()`
- Add the `-S` rationale comment and refresh the module docstring

### 2. Prove the guard bites
- **Task ID**: validate-probe-isolation
- **Depends On**: build-probe-isolation
- **Assigned To**: pin-probe-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `./scripts/pytest-clean.sh tests/unit/test_checkout_pin.py -n 0`
- Mutate: remove `-S` from the invocation, re-run, confirm the failure names the ambient-pin field; restore
- Confirm `git diff main -- tools/ scripts/` is empty
- Report pass/fail with both outputs

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-probe-isolation
- **Assigned To**: pin-probe-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update the `#3141` section of `docs/features/worktree-venv-isolation.md` per the Documentation section

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: pin-probe-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row
- Confirm all Success Criteria

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Contamination source is present (makes the test meaningful) | `ls .venv/lib/python*/site-packages/_valor_checkout_pin.pth` | exit code 0 |
| Checkout-pin tests pass | `./scripts/pytest-clean.sh tests/unit/test_checkout_pin.py -n 0 -q` | exit code 0 |
| End-to-end pair passes under the ambient pin | `./scripts/pytest-clean.sh "tests/unit/test_checkout_pin.py::TestEndToEnd" -n 0 -q` | exit code 0 |
| Production pin untouched | `git diff --name-only main -- tools/checkout_pin.py scripts/update/redis_flush_guard_pth.py \| wc -l` | output contains 0 |
| No env kill-switch added to the pin | `grep -c 'environ\|getenv' tools/checkout_pin.py` | match count == 0 |
| Lint clean | `python -m ruff check tests/unit/test_checkout_pin.py` | exit code 0 |
| Format clean | `python -m ruff format --check tests/unit/test_checkout_pin.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None. The diagnosis is reproduced, the two candidate mechanisms were measured
end-to-end, and the choice between them turns on documented CPython behavior
rather than on a judgment call that needs a human.
