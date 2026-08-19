---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-19
tracking: https://github.com/tomcounsell/ai/issues/2823
last_comment_id: 5324618203
---

# A Red Test on Main Should Block Something

## Problem

`tests/integration/test_sdlc_session_ensure_integration.py::TestStageArtifactVerificationGate::test_terminal_merged_pipeline_routes_to_merge_not_build` was red on `main` and blocked nothing. It is not an opt-in test: `pyproject.toml:155` sets `testpaths = ["tests"]`, so a bare `scripts/pytest-clean.sh` collects it with no marker, no path argument, and no flag. It still sat there long enough for its cause (#2062 WS3a) to become archaeology, and was found only because #2757's plan happened to run that file.

The failure itself is repaired (PR #2826, merged 2026-08-17). This plan is about the gap that let it sit.

That gap is a class, not an instance. The same batch produced #2822, #2846, #2847 and #2852 — roughly 30 more failing nodes on `main`, none of which blocked anything either.

**Current behavior:**

Four independent legs, each verified during recon, combine so that no red test on `main` reaches anyone:

1. **No CI runs any test.** `.github/workflows/` holds exactly one file, `claude.yml`, and it fires only on an `@claude` mention (`claude.yml:3-19`). The merge predicate's "CI green" leg reads `statusCheckRollup`; an empty rollup is treated as a pass. The gate is structurally incapable of failing.
2. **No code anywhere gates on a full-suite result.** The requirement is prose addressed to the model (`docs/sdlc/do-test.md:12-14`), and the TEST stage records only `{passed, failed}` — **no record of what was collected** — so nothing downstream can tell a full run from `-k one_test`. `tools/sdlc_stage_marker.py` has verification branches for `REVIEW` and `CRITIQUE` only. `tools/merge_predicate.py::evaluate_merge_predicate` (:683-764) evaluates four legs and none is a test leg.
3. **The nightly detector that exists runs `tests/unit/` only.** `scripts/nightly_regression_tests.py:187` hardcodes the collection argument. #2823's test lives in `tests/integration/`, outside that collection entirely. The uncovered remainder is 1,445 tests — `tests/` collects 14,899, `tests/unit/` collects 13,454.
4. **That detector is not installed here, and `/update` will not install it.** A doubled bridge-role gate (`scripts/install_nightly_tests.sh:64` plus an independent `if has_bridge:` at `scripts/update/run.py:2038`) requires an owned project with a truthy `telegram` key. Zero of 20 projects have one. `/update` logs the skip at INFO with no warning (`run.py:2045`), so the absence is silent.

Recon surfaced a fifth leg the issue did not anticipate, and it is the sharpest one:

5. **A run that executes zero tests reads as a perfectly clean night.** `run_tests()` logs `result.returncode` (`nightly_regression_tests.py:200`) but never checks it, and applies no floor on `summary.total` (:211-222). Measured twice during recon: `./scripts/pytest-clean.sh tests/integration tests/e2e tests/tools tests/performance` **exited 0 having run zero tests** (`5 warnings in 341.54s`, 45 × `node down: Not properly terminated`) because all 15 test-DB slots were held by concurrent lanes. Handed to the nightly that is `{passed: 0, failed: 0, total: 0, failing_parallel: []}` — arithmetically identical to a green suite.

Leg 5 is the same bug class as the issue itself: a green signal that reached no code.

**Desired outcome:**

A test that is red on `main` under default collection produces a filed issue within 24 hours, on whatever machine owns a checkout. A detector that could not actually run says so loudly instead of reporting a clean night.

## Freshness Check

**Baseline commit:** `57c8986e4`
**Issue filed at:** 2026-08-16T05:14:40Z
**Disposition:** Minor drift (one coordination overlap, noted below)

**File:line references re-verified:**
- `pyproject.toml:155` — issue claimed `testpaths = ["tests"]` — **still holds**, verified verbatim.
- `tests/integration/test_sdlc_session_ensure_integration.py` — issue named `test_terminal_merged_pipeline_routes_to_merge_not_build` — **still present**, now at line 498.
- `.github/workflows/` — issue asked whether a CI job exists — **confirmed absent**; only `claude.yml`, `@claude`-triggered.

**Cited sibling issues/PRs re-checked:**
- #2757 — CLOSED 2026-08-17. The G8 rebuild defect; its fix is explicitly out of scope here.
- #2826 — MERGED 2026-08-17. The fix for #2757. Landed; nothing to re-litigate.
- #2822 — CLOSED 2026-08-18. 11 red nodes from #2814 fallout.
- #2846 / #2847 — CLOSED 2026-08-17/18. 15 red nodes each from #2642 and #2835 fallout.
- #2852 — **OPEN**. Umbrella to get `main`'s unit suite to zero. Context, not a blocker: the widened detector re-baselines on first run rather than mass-filing.
- #2334 — **OPEN**. See overlap below.

**Commits on main since issue was filed (touching referenced files):**
- `838182c16` "G8 must not rebuild shipped work" (#2826) — **irrelevant to this plan's premise.** It repairs the test; it changes nothing about detection.

No commits since 2026-08-16 touched `scripts/nightly_regression_tests.py`, `scripts/install_nightly_tests.sh`, `.github/`, `docs/sdlc/do-test.md`, `docs/sdlc/do-merge.md`, `config/reflections.yaml`, or `tools/merge_predicate.py`.

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/nightly-autonomous-fix-before-alert.md` (#2334, `status: Ready`, `revision_applied: true`, created 2026-07-27, **never built**). It modifies the same file, `scripts/nightly_regression_tests.py`, to attempt an autonomous fix before paging a human. Its concern is *what happens after detection*; this plan's concern is *whether detection happens at all*. They are complementary, not conflicting. One concrete collision: #2334's plan proposes persisting `head_commit = git rev-parse HEAD` in the state file (:160, :396) and this plan proposes the same. Whichever lands first carries it; the second treats it as already done. See Risks.

**Notes:** No drift invalidated any premise. Every leg of the current behavior was re-verified against `57c8986e4` during recon rather than inherited from the issue text.

## Prior Art

- **#2376** (CLOSED): *Simplify the merge gate: remove all test execution at merge time*. Removed the merge-time full-suite gate wholesale. This is the single most important precedent here: it makes "add a test leg to the merge predicate" a rejected design, not an unexplored one. `docs/sdlc/do-merge.md:276-279` records the standing instruction: "Do not add a pytest invocation to this gate stack."
- **#2064** (CLOSED): full-suite pytest lock — concurrent lane suites in separate worktrees bypassed serialization and cross-reaped xdist workers, wedging merge gates.
- **#2066 / #1965** (CLOSED): the merge-gate test baseline went 253 commits stale with no refresh cadence; refresh attempts died to timeouts and contention.
- **#2118** (CLOSED): `run_email_bridge` async-teardown hang wedged full-suite pytest gates at ~99%.
- **#2145** (CLOSED): a per-tool wedge timeout killed a 600s-budgeted full-suite run at 300s, failing a pipeline one stage from merge.
- **#2429 / #2430 / #2462** (CLOSED): the nightly detector filed a fresh issue over the same standing failure on every run. Fixed by the `dispatched_nodes` set (#2559); `compute_dispatch_set`'s docstring at `nightly_regression_tests.py:415-418` names those three issues explicitly. **This is exactly the trap a widened collection could reopen** and is why re-baselining is a task here, not an afterthought.
- **#2488** (CLOSED): the detector mass-confirmed flaky xdist tests — 1 real regression out of 20. Fixed by the serial re-confirmation gate (`reconfirm_serial`, :224-268).
- **#1379**: established `has_worker_role()` in `scripts/install_reflection_worker.sh:37-80` after the same over-narrow bridge-gating mistake. The template for this plan's role-gate fix already exists in-repo.
- **#2628**: the test-DB claim guard that refuses to fall back to a colliding database. Its refusal is what produced the zero-collection run measured in recon.

## Research

No relevant external findings — this is entirely internal infrastructure (launchd, pytest, an in-repo detector script). No new libraries, APIs, or ecosystem patterns are involved, so Phase 0.7's external search was skipped per the skill's own criterion.

## Spike Results

### spike-1: Does a full default-collection run cost meaningfully more than the unit tier?
- **Assumption**: "Widening `tests/unit/` → `tests/` is a large enough cost increase to need a different design."
- **Method**: code-read + measurement (`pytest --collect-only -q` per tier)
- **Finding**: **False — the delta is ~10%.** `tests/` collects **14,899**; `tests/unit/` collects **13,454**. The uncovered remainder is 1,445 tests: integration 1,073, tools 203, e2e 129, performance 16.
- **Confidence**: high
- **Impact on plan**: Widening is cheap. No tiering, sampling, or alternating-night scheme is needed — Task A just changes the collection argument. The existing `PYTEST_TIMEOUT_SECONDS = 1800` still needs raising, because `pyproject.toml:188` already estimates ~21 min (~1260s) for the unit tier alone, leaving only ~1.4x headroom before the widening.

### spike-2: Are the non-unit tiers healthy enough to widen into?
- **Assumption**: "Non-unit tiers are so red that widening turns the detector into a noise cannon on night one."
- **Method**: measurement — `./scripts/pytest-clean.sh tests/tools tests/performance -q --tb=no -p no:randomly -n 4` on `main` @ `57c8986e4`
- **Finding**: **214 passed, 5 skipped, 0 failed, in 110s.** Fully green. `tests/integration` + `tests/e2e` could not be measured cleanly — see spike-3, which is why.
- **Confidence**: high for tools/performance; **unmeasured for integration/e2e** (see Open Questions).
- **Impact on plan**: Supports widening. Combined with the re-baseline task (Task D), a red tier is survivable regardless: the first widened run takes the `is_first_run` path (:630-632, :691-696) and seeds a baseline rather than dispatching.

### spike-3: Can a scheduled full-collection run silently report a clean night?
- **Assumption**: "A pytest run that fails to execute will surface as a non-zero exit or a parse error, so the detector cannot mistake it for green."
- **Method**: prototype — ran the proposed widened collection directly and inspected exit code and JSON summary shape
- **Finding**: **False, and reproducibly so.** Two separate runs of `./scripts/pytest-clean.sh tests/integration tests/e2e tests/tools tests/performance` **exited 0 with zero tests executed** — output `5 warnings in 341.54s`, 45 × `node down: Not properly terminated` — because all 15 test-DB slots were held by concurrent lanes and the #2628 guard correctly refused to collide. `run_tests()` logs `returncode` at :200 but never inspects it, and applies no floor to `summary.total` (:211-222). The returned dict would be `{passed: 0, failed: 0, error: 0, skipped: 0, total: 0, failing_parallel: []}` — indistinguishable from a green suite, which then persists as the new baseline via `save_last_run()` (:745).
- **Confidence**: high (reproduced twice)
- **Impact on plan**: **This became the highest-value task in the plan (Task B).** It is not a hypothetical: the detector is scheduled for 03:00 local, the worker and reflection services run 24/7, and overnight lanes hold DB slots. Without a run-integrity guard, widening the collection increases the number of nights that silently report green. Fixing leg 3 without fixing leg 5 would make the problem worse, not better.

### spike-4: Is the reflections framework a viable carrier for a nightly suite run?
- **Assumption**: "The existing reflections infrastructure is the cheap place to host this (issue question d)."
- **Method**: code-read — `agent/reflection_scheduler.py`, `config/reflections.yaml`, `docs/features/adding-reflection-tasks.md`
- **Finding**: **False.** `agent/reflection_scheduler.py:620-622` states in-code that a *sync* callable dispatched via `run_in_executor` "cannot cancel the thread (detection-only)" on timeout, and the pool has only `REFLECTION_POOL_WORKERS = 2` slots (:60). A 20-40 minute uncancellable pytest subprocess would occupy half the reflection pool and leak the thread on any hang. Separately, no reflection invokes pytest today (`grep -rn pytest reflections/ config/reflections.yaml` → zero hits), and three registry entries are already disabled specifically because they shell out to `gh` and block on auth.
- **Confidence**: high
- **Impact on plan**: Question (d) is answered **no**. The launchd job is the correct carrier and it already exists. Reflections are dropped from the design.

### spike-5: How much of the existing detector assumes the unit tier?
- **Assumption**: "Widening requires threading a path parameter through most of the script."
- **Method**: code-read — full read of all 767 lines
- **Finding**: **Far less than expected.** `grep -n "tests/unit"` returns exactly six sites: the module docstring (:4), the `run_tests` docstring (:174), a log line (:180), **the argv element (:187)**, and two Telegram remediation strings (:730, :738), plus prose at :636. Everything else is path-agnostic: `reconfirm_serial` passes only node IDs (:245), node-ID parsing is a bare `nodeid.split("::", 1)[0]` (:472), and the four dedup functions (`compute_new_failures` :381, `prior_dispatched` :396, `compute_dispatch_set` :410, `carry_dispatched_nodes` :427) are pure set math on opaque strings. **State-file keys encode no tier either** — which is the hazard: a widened run silently diffs against the unit-tier baseline unless the state is versioned.
- **Confidence**: high
- **Impact on plan**: Task A is small. Task D (re-baseline) is mandatory and would have been easy to miss.

### spike-6: What is the minimal correct change to get the job installed?
- **Assumption**: "Loosening `has_bridge_role()` in the installer is sufficient."
- **Method**: code-read — `scripts/install_nightly_tests.sh`, `scripts/update/run.py`, `scripts/update/service.py`, `scripts/install_reflection_worker.sh`
- **Finding**: **False — the gate is doubled.** `scripts/update/run.py:2038` carries an independent `if has_bridge:` check (from `has_bridge = bool(machine_check.get("bridge_projects"))` at :1858) that decides whether to invoke the installer *at all*. Loosening the shell gate alone leaves the installer unreached under `/update`. Both sites must change. The correct target predicate already exists: `has_worker_role()` in `scripts/install_reflection_worker.sh:37-80` differs from `has_bridge_role()` by exactly one line — it drops the `proj.get("telegram")` requirement — and `run.py:2016-2023` argues in-comment that letting the self-gating script decide is the right pattern.
- **Confidence**: high
- **Impact on plan**: Task C touches two files, not one. It also gets a source-text regression test, copying `tests/integration/test_install_reflection_worker.py:47-52`, which pins its own gate the same way.

## Data Flow

1. **Trigger**: launchd fires `com.valor.nightly-tests` at 03:00 local (`StartCalendarInterval`, plist:27-33), executing `.venv/bin/python scripts/nightly_regression_tests.py` with `WorkingDirectory` = the repo root.
2. **Run lock**: `_acquire_run_lock` takes `fcntl.flock(LOCK_EX|LOCK_NB)` on `data/nightly_tests.lock` (:130-156); a collision exits 0 without running.
3. **Collection** *(changed by Task A)*: a subprocess runs the configured collection with `--json-report` into `/tmp/nightly_pytest_report.json`.
4. **Integrity check** *(new, Task B)*: the exit code and `summary.total` are validated against a floor before anything downstream trusts the result. Failure short-circuits to a loud alert with no state write and no dispatch.
5. **Serial re-confirmation**: nodes that failed under `-n auto` are re-run with `-n0` (:224-268) to split real regressions from xdist artifacts.
6. **Baseline diff**: `compute_new_failures` (:381) drives the alert; `compute_dispatch_set` (:410) drives issue filing, subtracting `prior_dispatched` so a standing failure is not re-filed nightly.
7. **Dispatch**: `maybe_dispatch_triage_session` (:503-563) spawns `python -m tools.valor_session create --role eng` with a prompt instructing the agent to search open issues first and file or comment.
8. **Alert**: `send_telegram()` to `Eng: Valor` — best-effort, never fatal (`docs/features/nightly-regression-tests.md:76-78`).
9. **Persist**: `save_last_run()` writes `data/nightly_tests_last_run.json`, carrying `dispatched_nodes` forward *(gains a collection identity and `head_commit` under Task D)*.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| Merge-time full-suite gate (pre-#2376) | Ran the suite as a merge precondition, with a shape classifier, per-SHA verdict cache, and categorised baseline comparison | Wedged routinely — xdist bringup deadlocks (#2064), worker crashes, Redis DB pollution from concurrent suites, stale baselines (#1965, #2066), and a per-tool timeout that killed a budgeted run mid-gate (#2145). Removed wholesale by #2376. **Not a fix to retry.** |
| Nightly detector, initial form | Ran `tests/unit/` nightly and filed an issue on new failures | Two independent shortfalls. (i) Scope: it never collected `tests/integration/`, so #2823's test was invisible to it by construction. (ii) Reach: it was bridge-role gated on `telegram`, a predicate unrelated to running tests, so it installed on zero machines in the current fleet. |
| `dispatched_nodes` dedup (#2559) | Stopped the detector re-filing the same standing failure every night | Correct and still correct — but the state file encodes no collection identity, so widening the collection would make every pre-existing non-unit failure read as brand-new and reopen #2429/#2430/#2462 in one night. |
| Serial re-confirmation (#2488) | Re-ran failures with `-n0` to strip xdist artifacts | Correct, but its 900s budget was sized for unit-tier failure sets, and its fail-safe on timeout marks **every** input node confirmed (:261-263) — which under a widened collection could dispatch a very large set. |

**Root cause pattern:** every prior fix hardened *what the detector does with a result it trusts*. None questioned whether the detector **ran the right tests**, **ran at all on this machine**, or **actually executed anything**. Leg 5 is the purest expression of the pattern: the most carefully-built dedup machinery in the repo sits downstream of a summary dict that is never sanity-checked.

## Architectural Impact

- **New dependencies**: none. No new libraries, services, or config files.
- **Interface changes**: `scripts/nightly_regression_tests.py` gains a `--paths` CLI flag (default: the full default collection) and one new internal function for run-integrity validation. `run_tests()`'s return dict gains no key; a separate validator consumes the raw report and returncode.
- **Coupling**: **decreases.** Task C removes a false dependency between "runs regression tests" and "hosts a Telegram bridge". The alert path is already non-fatal, so the coupling was never load-bearing.
- **Data ownership**: `data/nightly_tests_last_run.json` gains a collection-identity field and `head_commit`. It remains owned solely by this script.
- **Reversibility**: high. Every change is a small edit to one script, one installer, one `/update` call site, and one plist-adjacent doc. Reverting the role gate re-uninstalls the service on next `/update`.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (the e2e-inclusion question, and confirming fleet reach)
- Review rounds: 1

The work is bounded: one script, one installer, one `/update` call site, plus tests and docs. The thinking was front-loaded into recon; the build is mostly mechanical. What earns Medium rather than Small is Task B, which needs a real design (what counts as "the run did not happen") and careful tests, and Task D, whose failure mode is a one-night flood of duplicate issues.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `pytest-json-report` installed | `.venv/bin/python -m pytest --json-report --help > /dev/null` | The detector's report format; also gated by `install_nightly_tests.sh:78` |
| `projects.json` readable | `test -f "$HOME/Desktop/Valor/projects.json"` | The role gate reads it; fail-open otherwise |
| Repo venv on the pinned interpreter | `./scripts/check-interpreter-pin.sh .` | Task E routes the run through `pytest-clean.sh`, which refuses an off-pin interpreter |

## Solution

### Key Elements

- **Widened collection**: the nightly detector collects what a bare `scripts/pytest-clean.sh` collects, so "the default collection is red" and "the detector is red" mean the same thing.
- **Run-integrity guard**: a run that did not actually execute tests is reported as an infrastructure failure, never as a clean night, and never persisted as a baseline.
- **Role-correct install gate**: the detector installs on machines that have a checkout and a worker, which is what running tests requires, rather than on machines that host a Telegram bridge.
- **Collection-aware baseline**: the state file records which collection produced it, so widening triggers a deliberate re-baseline instead of a mass dispatch.
- **Wrapper-routed execution**: the unattended 03:00 run gets the orphan reaping, interpreter-pin check, and stall watchdog that `scripts/pytest-clean.sh` provides and a bare `python -m pytest` does not.

### Flow

**03:00 launchd fire** → acquire run lock → **run the default collection** → validate run integrity → *(integrity failed)* → **loud alert, no state write, exit non-zero**

**03:00 launchd fire** → acquire run lock → **run the default collection** → validate run integrity → re-confirm failures serially → diff against a same-collection baseline → *(new failures)* → **dispatch one triage session + alert** → persist state with `head_commit`

**`/update` on any machine owning a project** → `install_nightly_tests.sh` → worker-role gate passes → **service installed**

### Technical Approach

- **Task A — widen the collection.** Add `--paths` (nargs='+', default `["tests/"]`) to the argparse block at :610-612 and thread it into the argv at :187. Update the six unit-tier string sites spike-5 enumerated (:4, :174, :180, :187, :730, :738) plus the prose comment at :636 so remediation instructions match what actually ran. Raise `PYTEST_TIMEOUT_SECONDS` (:72) — the unit tier alone is already estimated at ~21 min against an 1800s ceiling (`pyproject.toml:188`), and the widening adds ~10% more tests that parallelize worse. Size it from the measured widened runtime rather than guessing, and leave a comment recording the measurement. Raise `PYTEST_RECONFIRM_TIMEOUT_SECONDS` (:76) too, or cap the re-confirm set, because its timeout fail-safe marks every input node confirmed (:261-263).

- **Task B — run-integrity guard.** Add `validate_run_integrity(report, returncode, prev) -> str | None` returning a human-readable reason when the run cannot be trusted, `None` otherwise. Trip on any of: pytest exit code in `{2, 3, 4, 5}` (interrupted / internal error / usage error / no tests collected); `summary.total == 0`; a previous run exists and `total < 0.9 * prev_total`; or the report's collectors contain an `error` outcome. On a trip, take the same disposition the existing timeout path already takes at :639-641 — **alert loudly, do not save state, do not dispatch, return non-zero** — so the next night still diffs against the last trustworthy baseline. Note that exit code alone is insufficient: the recon run exited **0** with zero tests executed, so the `total` floor is the load-bearing check, not the returncode.

- **Task C — role-correct install gate.** Replace `has_bridge_role()` in `scripts/install_nightly_tests.sh:30-74` with `has_worker_role()`, copied from `scripts/install_reflection_worker.sh:37-80` (the diff is one line: drop `if proj.get("telegram"):`). Independently, change `scripts/update/run.py:2038` so the installer is always invoked and the script's own gate is the single decision point, matching the pattern `run.py:2016-2023` argues for. Keep the fail-open contract on unreadable config, missing venv, and `scutil` error — `tests/unit/test_install_scripts_bootstrap.py` depends on it (its harness sets no `PROJECTS_CONFIG_PATH` and a fresh `$HOME`, so all four parametrized nightly cases reach bootstrap only via fail-open). Also make `/update` log the skip as a **warning** rather than INFO at `run.py:2045`, so a machine with no regression coverage is visible.

- **Task D — collection-aware baseline.** Add a `collection` field to the state dict recording the paths that produced it. When the recorded collection differs from the current one, treat the run as a baseline seed: take the existing `is_first_run` path (:630-632, :691-696), seeding `dispatched_nodes` and dispatching nothing. This is what stops the widening from re-filing every pre-existing non-unit failure in one night and reopening #2429/#2430/#2462. Add `head_commit` (`git rev-parse HEAD`) in the same edit so a red night is attributable to a SHA — coordinating with #2334's plan, which proposes the same field.

- **Task E — route through `scripts/pytest-clean.sh`.** Replace the bare `sys.executable -m pytest` argv at :182-199 (and the serial re-confirm at :242-258) with the wrapper. Unattended at 03:00 this buys orphan reaping via `trap cleanup EXIT INT TERM HUP PIPE`, the `check-interpreter-pin.sh` refusal, and the `PYTEST_STALL_LIMIT_S` watchdog. The recon run produced 45 `node down` events; without reaping, those workers reparent to PID 1 and accumulate nightly. `--json-report` passes through the wrapper unchanged since it is a pure argv pass-through.

- **What this plan deliberately does not do**: add a CI workflow, or add a test leg to the merge predicate or any stage gate. See Rabbit Holes.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `run_tests()`'s `except (FileNotFoundError, json.JSONDecodeError)` at :206-209 re-raises after logging — verify the new integrity guard does not swallow it, and assert the log line is emitted.
- [ ] `reconfirm_serial`'s catch-all at :261-263 (`TimeoutExpired/FileNotFoundError/JSONDecodeError/OSError`) marks every input node confirmed. Add a test asserting that behavior is preserved under the widened collection, and that the resulting dispatch set is still bounded by `prior_dispatched`.
- [ ] `send_telegram()` is documented as never fatal — assert that an integrity-failure alert whose Telegram send fails still returns non-zero and still skips the state write.
- [ ] No `except Exception: pass` blocks exist in the touched scope of `nightly_regression_tests.py` — confirm during build and state so.

### Empty/Invalid Input Handling
- [ ] `validate_run_integrity` with `summary.total == 0` → trips. **This is the spike-3 regression test and the single most important test in the plan.**
- [ ] `validate_run_integrity` with `prev = {}` (no prior run) → the ratio check must not fire; only the absolute floor applies.
- [ ] `validate_run_integrity` with a report missing `summary` entirely → trips rather than raising `KeyError`.
- [ ] `--paths` with an empty list, and with a path that collects nothing → the collection-error path, not a clean night.
- [ ] `has_worker_role()` against a `projects.json` with zero projects owned by this machine → skips install and removes any stale plist.

### Error State Rendering
- [ ] The integrity-failure Telegram message must name the reason (zero collection, bad exit code, total dropped below floor) — assert the reason string reaches the message body, so an operator reading the alert can tell "the suite could not run" from "the suite is red".
- [ ] Assert the integrity-failure path writes no state file, by asserting the pre-existing state file is byte-identical after the run.

## Test Impact

- [ ] `tests/unit/test_nightly_regression_tests.py::TestMainDispatchPersistence` — UPDATE: `_run_main` patches `sys.argv` to `["nightly_regression_tests.py", "--dry-run"]` at :513; the new `--paths` flag must be threaded there. All seven methods below it inherit that fixture (:532, :547, :554, :566, :579, :584, :595).
- [ ] `tests/unit/test_nightly_regression_tests.py::TestMainDispatchPersistence._RUN_RESULT` (:495-503) — UPDATE: the stub summary dict must carry a non-zero `total` or every test trips the new integrity guard.
- [ ] `tests/unit/test_nightly_regression_tests.py::TestRunLock::test_main_returns_0_on_collision_without_running_tests` (:260-281) — UPDATE: patches `sys.argv` at :268; re-check under the new argparse block.
- [ ] `tests/unit/test_nightly_regression_tests.py::TestReconfirmSerial` (:162-198) — UPDATE: fixtures use `tests/unit/...` node IDs; add non-unit node IDs to prove path-agnosticism.
- [ ] `tests/unit/test_nightly_regression_tests.py::TestDeltaLogic` (:74-124) — REPLACE: `_compute_alert` (:77-90) reimplements main()'s alert logic inline using a **count delta**, which no longer matches the set-based `compute_new_failures` (:381-393). It is already drifted; rewrite it against the real function or delete it.
- [ ] `tests/unit/test_nightly_regression_tests.py` — ADD: coverage for `run_tests()`'s argv. **No existing test asserts on it** — `run_tests` is always mocked (:273, :520) — so line 187 is currently untested and the widening would otherwise land with zero coverage. Copy the argv-assertion pattern from `TestMaybeDispatchTriage::test_dispatch_once` (:438).
- [ ] `tests/unit/test_nightly_regression_tests.py` — ADD: a `TestValidateRunIntegrity` class covering every trip condition in Task B, including the spike-3 case (exit 0, total 0).
- [ ] `tests/unit/test_install_scripts_bootstrap.py` (:74-77, :59) — VERIFY, likely no change: the harness reaches bootstrap via the **fail-open** branch (no `PROJECTS_CONFIG_PATH`, fresh `$HOME`), so loosening the predicate keeps all four parametrized cases passing. Confirm empirically rather than by inspection; a change that made the gate fail closed would break `assert len(harness.bootstrap_calls()) == len(harness.labels)` at :261.
- [ ] `tests/integration/test_install_nightly_tests.py` — CREATE: source-text assertions pinning the new gate, copying `tests/integration/test_install_reflection_worker.py:47-68` (`assert "has_worker_role" in installer_src`, `assert 'proj.get("telegram")' not in installer_src`, self-skip and stale-plist removal, fail-open on unreadable config). No test pins the nightly gate today.
- [ ] `tests/README.md:417` — UPDATE: the row recording `test_nightly_regression_tests.py | 28` is already stale and will move further.

## Rabbit Holes

- **Adding a GitHub Actions workflow that runs the suite.** Tempting because leg 1 reads like "we forgot CI". It is out of appetite and against the grain of a system whose Redis, launchd services, test-DB claims and `projects.json` are all machine-local — a hosted runner reproduces none of that. The issue rules out "run everything on every push" directly, and `CLAUDE.md` puts a `tests/unit/` run at ~20 minutes.
- **Adding a test leg to the merge predicate or a TEST-stage gate.** This is the single most attractive wrong turn here, and it is already a settled question: #2376 removed exactly that after #2064, #2066, #1965, #2118 and #2145 documented it wedging. `docs/sdlc/do-merge.md:276-279` carries the standing instruction not to. Question (b)'s answer is a **finding to record**, not a gate to build.
- **Making the TEST stage marker record its collection scope.** Genuinely appealing — it is the honest fix for "nothing can tell a full run from `-k one_test`" — but it touches `tools/sdlc_stage_marker.py`, the OUTCOME contract, and every consumer, and it only pays off if something later gates on it, which the bullet above rules out for now.
- **Fixing the red non-unit tiers found by the widened run.** The detector's job is to file issues; those issues are separate lanes. #2852 is the existing umbrella for the unit tier and the model to follow.
- **Building a general "machine roles" abstraction.** Recon found five hand-rolled role gates and no canonical helper, plus a latent apostrophe-normalization bug (`config/machine.py` has `normalize_machine_name`; none of the five shell gates use it, and this machine is literally `Tom's MacBook Air` with U+2019). Real, and a different plan. Task C copies the existing `has_worker_role()` rather than abstracting.
- **Tuning `-n auto` against the 15 test-DB slots.** The recon run spawned 50 xdist workers against 15 slots. Worth understanding, but the correct response inside this plan's scope is Task B's guard — report the collision loudly — not a concurrency redesign.

## Risks

### Risk 1: The widened collection re-files every pre-existing non-unit failure on night one
**Impact:** A flood of duplicate issues, exactly the #2429/#2430/#2462 failure the `dispatched_nodes` set was built to stop. Worse in reputation terms than the gap being fixed, because it teaches the team to ignore the detector.
**Mitigation:** Task D is a hard requirement, not an optimization. The state file records its collection identity, and a changed collection forces the existing `is_first_run` baseline path (:630-632, :691-696), which seeds `dispatched_nodes` and dispatches nothing. Verify by running once against a state file recording the old collection and asserting zero dispatches.

### Risk 2: The widened run exceeds its timeout and goes silent
**Impact:** On `TimeoutExpired` the script returns 1 at :640 **before** any Telegram call (the alerts at :720/:734/:740 are all downstream), so a timeout produces no notification at all — only a line in `logs/nightly_tests.log`, a file that does not currently exist on this machine. A too-tight timeout converts the fix into a differently-silent detector.
**Mitigation:** Measure the widened runtime before choosing the constant, and record the measurement in a comment. Separately, move the timeout path's alert **above** the early return so a timeout pages someone — it is the same class as Task B and belongs with it.

### Risk 3: Un-gating the install turns on a detector on machines that cannot run the suite
**Impact:** A machine that owns projects but has a broken venv or no test DBs would alert nightly.
**Mitigation:** The installer's existing `pytest --json-report --help` preflight (`install_nightly_tests.sh:78`) already fails the install on a broken venv. Task B's guard covers the runtime case: such a machine reports "the run did not happen", which is the correct and actionable message.

### Risk 4: Collision with #2334's Ready plan on the same file
**Impact:** Both plans modify `scripts/nightly_regression_tests.py` and both propose persisting `head_commit`. Building both concurrently produces a merge conflict in the state-file writer.
**Mitigation:** #2334 has been `Ready` and unbuilt since 2026-07-27, so the likelihood is low. Whichever lands first carries `head_commit`; the second treats it as done. Named here so a reviewer checks rather than discovers it in a conflict.

### Risk 5: Fleet reach is unverified beyond this machine
**Impact:** The recon evidence for leg 4 is from `Tom's MacBook Air`, where `projects.json` lists 20 projects all owned locally and none Telegram-configured. If another machine in the fleet does have the nightly installed, leg 4 is narrower than stated.
**Mitigation:** Legs 1, 2, 3 and 5 are machine-independent and are the ones that actually explain #2823 — a `tests/unit/`-only detector could never have caught an integration test regardless of where it ran. The plan's value does not depend on the answer. Raised as an Open Question.

## Race Conditions

### Race 1: Nightly run collides with an overnight SDLC lane over test-DB slots
**Location:** `scripts/nightly_regression_tests.py:182-199` (the pytest subprocess), against `tests/db_claim.py` / `tests/conftest.py`'s `_redis_test_db_num()`
**Trigger:** launchd fires at 03:00 while one or more lane worktrees hold test-DB slots. There are 15 slots; `-n auto` requests far more workers than that on this machine.
**Data prerequisite:** a free test-DB slot per worker that touches Redis, before any such test can execute.
**State prerequisite:** the #2628 guard must refuse to fall back to a colliding database — and it correctly does.
**Mitigation:** This race cannot be eliminated from inside this script; the machine is genuinely shared. Task B converts its outcome from a **silent false green** into a loud, named infrastructure failure with no baseline write. Measured twice in recon: exit 0, zero tests, `5 warnings in 341.54s`.

### Race 2: Two nightly runs overlap
**Location:** `_acquire_run_lock`, :130-156
**Trigger:** a manual run during a scheduled one, or a launchd re-fire while a widened run still executes.
**Data prerequisite:** exclusive ownership of `data/nightly_tests_last_run.json` before read-diff-write.
**State prerequisite:** one writer at a time.
**Mitigation:** already handled — `fcntl.flock(LOCK_EX|LOCK_NB)` exits 0 on collision without running. **Widening lengthens the run and therefore widens this window**, so re-verify the lock still holds under the longer runtime; `TestRunLock` (:260-281) covers it.

### Race 3: Serial re-confirmation runs against a moved `HEAD`
**Location:** `reconfirm_serial` (:224-268) relative to the parallel run at :182-199
**Trigger:** a merge lands between the parallel run and the serial re-confirm — a wider window under the widened collection.
**Data prerequisite:** both runs must describe the same tree for "confirmed" to mean anything.
**State prerequisite:** `HEAD` stable across the run.
**Mitigation:** Task D's `head_commit` makes drift **detectable** — capture it before the parallel run and re-check after the serial one, treating a change as an integrity failure under Task B's guard. The script performs no git operations today and does not check its branch at all, so this is currently invisible.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2852] Repairing the tests the widened detector finds red. #2852 is the open umbrella for driving `main`'s unit suite to zero; non-unit failures surfaced by the widening get their own issues filed by the detector itself, which is the mechanism this plan builds.
- [SEPARATE-SLUG #2334] Autonomous-fix-before-alert behavior for the nightly detector. Already planned and tracked; this plan changes what the detector *collects* and *trusts*, not what it does after a confirmed failure.
- [EXTERNAL] Verifying which other machines in the fleet have `com.valor.nightly-tests` installed. Requires shell access to hosts this agent cannot reach; `projects.json` on this machine is known to be stale as a fleet inventory.

## Update System

`/update` changes are **required** — they are Task C, not an afterthought:

- `scripts/update/run.py:2038` currently wraps the nightly-tests install in `if has_bridge:`, an independent second gate. It must stop gating so the installer's own predicate is the single decision point, matching the pattern `run.py:2016-2023` already argues for with the reflection worker.
- `scripts/update/run.py:2045` logs the skip at INFO with no warning. It should emit a warning so a machine running with no regression coverage is visible in `/update` output rather than silent.
- `scripts/install_nightly_tests.sh` gate changes from `has_bridge_role()` to `has_worker_role()`. On the next `/update`, machines that own a project and previously had no nightly service will install it. This is the intended propagation and needs no migration step: the installer is idempotent (boots out any existing label before bootstrapping) and `launchctl_bootstrap_fail_soft` is called 3-arg, without PID verification, which is correct for a scheduled service (`scripts/lib/launchctl.sh:38-44` names nightly-tests explicitly).
- No new dependencies or config files to propagate. `pytest-json-report` is already a prerequisite and already checked at `install_nightly_tests.sh:78`.
- The plist is unchanged unless Task A's `--paths` is threaded through `ProgramArguments`. Prefer defaulting `--paths` to the full collection **in the script** so the plist stays flag-free and no reinstall is needed to change scope.

## Agent Integration

No agent integration required — this is scheduled-job infrastructure. The detector already reaches the agent through two existing surfaces, and this plan adds no third:

- **Session dispatch**: `maybe_dispatch_triage_session` (:503-563) shells out to `python -m tools.valor_session create --role eng`, an entry point already declared in `pyproject.toml [project.scripts]`. Task A and B change the *content* of the triage prompt (which paths ran, and the integrity verdict) but not the invocation shape.
- **Telegram alerting**: `send_telegram()` (:271-298) invokes the existing `valor-telegram` binary. Unchanged, and explicitly best-effort — `docs/features/nightly-regression-tests.md:76-78` documents that a failed send never crashes the script, which is precisely why the bridge-role gate in Task C is safe to drop.

No new CLI entry point in `pyproject.toml`, no bridge import, no MCP surface.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/nightly-regression-tests.md` — four statements are now false or misleading and must be corrected, not appended to: `:3-7` states as unconditional present-tense fact that "a launchd job runs the suite each night at 03:00"; `:60` and `:83-85` describe the bridge-role gate being replaced; `:89` says "installed automatically by `/update` on bridge machines" while omitting the second gate at `run.py:2038`; `:104-108` gives a verification command without noting that empty output was the expected state. Document the widened collection, the run-integrity guard and its failure disposition, and the worker-role predicate.
- [ ] Update `docs/features/README.md` index entry if the summary line changes.
- [ ] Update `docs/sdlc/do-merge.md:273-274`, which names `scripts/nightly_regression_tests.py` as "the backstop for anything that slips through". That claim was false in two ways — the backstop ran `tests/unit/` only, and was installed nowhere — and becomes true once this ships. State the new scope explicitly.
- [ ] Add a short note to `docs/sdlc/do-test.md` recording the answer to investigation question (b): no code gates on a full-suite result, deliberately (#2376), and the nightly detector is the compensating control. This is where a future reader will look.
- [ ] Update `tests/README.md:417` (`test_nightly_regression_tests.py` test count, already stale).

### External Documentation Site
Not applicable — this repo has no Sphinx/MkDocs site.

### Inline Documentation
- [ ] Docstring for `validate_run_integrity` stating each trip condition and, critically, **why exit code alone is insufficient** — cite the measured exit-0-with-zero-tests case so the next reader does not "simplify" it away.
- [ ] Comment on the raised `PYTEST_TIMEOUT_SECONDS` recording the measured widened runtime it was derived from.
- [ ] Comment on the `collection` state field explaining that a mismatch forces a re-baseline, and naming #2429/#2430/#2462 as what that prevents.

## Success Criteria

- [ ] `scripts/nightly_regression_tests.py` collects the full default collection by default — the same set a bare `scripts/pytest-clean.sh` collects (14,899 tests), including `tests/integration/`.
- [ ] A run that executes zero tests, or exits with pytest code 2/3/4/5, or whose total falls below the floor, produces an infrastructure-failure alert naming the reason, writes no state, dispatches nothing, and returns non-zero.
- [ ] The spike-3 scenario is a passing regression test: a JSON report with `summary.total == 0` and returncode 0 must not be treated as a clean night.
- [ ] `scripts/install_nightly_tests.sh` gates on `has_worker_role()`; `grep 'proj.get("telegram")' scripts/install_nightly_tests.sh` returns nothing.
- [ ] `scripts/update/run.py` invokes the nightly-tests installer unconditionally, leaving the shell script as the single decision point, and warns rather than silently informs when the install is skipped.
- [ ] `com.valor.nightly-tests` is present in `launchctl list` on this machine after `/update`.
- [ ] A run whose recorded state carries a different collection re-baselines and dispatches zero triage sessions.
- [ ] The state file records `head_commit`.
- [ ] `tests/integration/test_install_nightly_tests.py` exists and pins the gate by source-text assertion.
- [ ] `run_tests()`'s argv has direct test coverage (it has none today).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (detector)**
  - Name: `detector-builder`
  - Role: All changes to `scripts/nightly_regression_tests.py` — collection widening, integrity guard, state versioning, wrapper routing
  - Agent Type: builder
  - Resume: true

- **Builder (install)**
  - Name: `install-builder`
  - Role: `scripts/install_nightly_tests.sh` role gate and the `scripts/update/run.py` call site
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `detector-tester`
  - Role: Update the existing suite per Test Impact; add `TestValidateRunIntegrity`, `run_tests()` argv coverage, and the installer-gate integration test
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `detector-documentarian`
  - Role: Correct the false claims in `docs/features/nightly-regression-tests.md` and the SDLC addenda
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `detector-validator`
  - Role: Verify every Success Criterion and Verification row
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Widen the collection and size the timeouts
- **Task ID**: build-widen-collection
- **Depends On**: none
- **Validates**: tests/unit/test_nightly_regression_tests.py
- **Informed By**: spike-1 (delta is only 1,445 tests / ~10%), spike-5 (exactly six unit-tier string sites: :4, :174, :180, :187, :730, :738, plus prose at :636)
- **Assigned To**: detector-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `--paths` (nargs='+', default `["tests/"]`) to the argparse block at :610-612; thread it into the pytest argv at :187.
- Update all six unit-tier strings so log lines and Telegram remediation instructions name what actually ran.
- Measure a full widened run, then raise `PYTEST_TIMEOUT_SECONDS` (:72) from the measurement, with a comment recording it. Raise `PYTEST_RECONFIRM_TIMEOUT_SECONDS` (:76) or bound the re-confirm set.
- Move the `TimeoutExpired` alert **above** the early `return 1` at :640 so a timeout notifies rather than going silent (Risk 2).

### 2. Add the run-integrity guard
- **Task ID**: build-integrity-guard
- **Depends On**: build-widen-collection
- **Validates**: tests/unit/test_nightly_regression_tests.py (create `TestValidateRunIntegrity`)
- **Informed By**: spike-3 (reproduced twice: exit 0, zero tests executed, `5 warnings in 341.54s`, all 15 test-DB slots held)
- **Assigned To**: detector-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `validate_run_integrity(report, returncode, prev) -> str | None`, tripping on exit code in `{2,3,4,5}`, `summary.total == 0`, `total < 0.9 * prev_total` when a prior run exists, or a collector error outcome.
- Wire it immediately after the JSON parse in `run_tests()`'s caller, before `reconfirm_serial`.
- On a trip: alert with the reason named, skip `save_last_run()`, skip dispatch, return non-zero — the same disposition as the existing timeout path.
- Docstring must state why the returncode alone is insufficient and cite the measured exit-0 case.

### 3. Make the baseline collection-aware and record the commit
- **Task ID**: build-state-versioning
- **Depends On**: build-widen-collection
- **Validates**: tests/unit/test_nightly_regression_tests.py::TestMainDispatchPersistence
- **Informed By**: spike-5 (state keys encode no tier), Prior Art (#2429/#2430/#2462 duplicate-filing)
- **Assigned To**: detector-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a `collection` field to the persisted state recording the paths that produced it.
- When the recorded collection differs from the current one, route through the existing `is_first_run` baseline path (:630-632, :691-696): seed `dispatched_nodes`, dispatch nothing.
- Add `head_commit` from `git rev-parse HEAD` in the same edit. Check whether #2334 landed first and treat it as done if so.

### 4. Route execution through the sanctioned wrapper
- **Task ID**: build-wrapper-routing
- **Depends On**: build-integrity-guard
- **Validates**: tests/unit/test_nightly_regression_tests.py
- **Informed By**: spike-3 (45 `node down` events leave reparented workers)
- **Assigned To**: detector-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace the bare `sys.executable -m pytest` argv at :182-199 and :242-258 with `scripts/pytest-clean.sh`.
- Confirm `--json-report` passes through unchanged (the wrapper is a pure argv pass-through).
- If the wrapper's interpreter-pin refusal or its own reaping conflicts with the unattended context, stop and report rather than working around it — that is a signal, not an obstacle.

### 5. Fix the install role gate
- **Task ID**: build-role-gate
- **Depends On**: none
- **Validates**: tests/unit/test_install_scripts_bootstrap.py, tests/integration/test_install_nightly_tests.py (create)
- **Informed By**: spike-6 (the gate is doubled — the shell script AND `scripts/update/run.py:2038`)
- **Assigned To**: install-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `has_bridge_role()` in `scripts/install_nightly_tests.sh:30-74` with `has_worker_role()`, copied from `scripts/install_reflection_worker.sh:37-80`.
- Preserve the fail-open contract on unreadable config, missing venv, and `scutil` error — `tests/unit/test_install_scripts_bootstrap.py` reaches bootstrap only through it.
- Change `scripts/update/run.py:2038` so the installer is always invoked; make the skip at :2045 a warning.

### 6. Test coverage
- **Task ID**: build-tests
- **Depends On**: build-integrity-guard, build-state-versioning, build-wrapper-routing, build-role-gate
- **Validates**: tests/unit/test_nightly_regression_tests.py, tests/integration/test_install_nightly_tests.py
- **Informed By**: Test Impact section
- **Assigned To**: detector-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `TestValidateRunIntegrity` covering every trip condition, with the spike-3 case (exit 0, total 0) as the headline test.
- Add `run_tests()` argv coverage — none exists today; copy the pattern at `TestMaybeDispatchTriage::test_dispatch_once` (:438).
- Thread `--paths` through `_run_main`'s `sys.argv` patch (:513) and give `_RUN_RESULT` (:495-503) a non-zero total.
- Rewrite or delete `TestDeltaLogic` (:74-124), whose inline `_compute_alert` has drifted from `compute_new_failures`.
- Create `tests/integration/test_install_nightly_tests.py` pinning the gate by source text, copying `test_install_reflection_worker.py:47-68`.

### 7. Validation
- **Task ID**: validate-detector
- **Depends On**: build-tests
- **Assigned To**: detector-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every command in the Verification table and report pass/fail per row.
- Confirm a run against a state file recording the old collection dispatches zero triage sessions.

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-detector
- **Assigned To**: detector-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Correct the four false or misleading statements in `docs/features/nightly-regression-tests.md` (:3-7, :60, :83-85, :89, :104-108). Describe the new status quo only — no migration narrative.
- Correct `docs/sdlc/do-merge.md:273-274`'s backstop claim.
- Record investigation question (b)'s answer in `docs/sdlc/do-test.md`.
- Update `docs/features/README.md` and `tests/README.md:417`.

### 9. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: detector-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all Success Criteria including documentation.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Detector tests pass | `./scripts/pytest-clean.sh tests/unit/test_nightly_regression_tests.py -q` | exit code 0 |
| Installer tests pass | `./scripts/pytest-clean.sh tests/unit/test_install_scripts_bootstrap.py tests/integration/test_install_nightly_tests.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Collection widened | `grep -c '"tests/unit/",' scripts/nightly_regression_tests.py` | match count == 0 |
| Integrity guard exists | `grep -c 'def validate_run_integrity' scripts/nightly_regression_tests.py` | output > 0 |
| Zero-collection regression test exists | `grep -rn 'TestValidateRunIntegrity' tests/unit/test_nightly_regression_tests.py` | exit code 0 |
| Role gate replaced | `grep -c 'has_worker_role' scripts/install_nightly_tests.sh` | output > 0 |
| Telegram predicate gone from gate | `grep -c 'proj.get("telegram")' scripts/install_nightly_tests.sh` | match count == 0 |
| `/update` gate un-doubled | `grep -n 'if has_bridge:' scripts/update/run.py` | output does not contain `install_nightly_tests` |
| State records collection identity | `grep -c '"collection"' scripts/nightly_regression_tests.py` | output > 0 |
| State records head commit | `grep -c 'head_commit' scripts/nightly_regression_tests.py` | output > 0 |
| No merge-time test gate added (anti-criterion) | `grep -c 'pytest' tools/merge_predicate.py` | match count == 0 |
| No CI test workflow added (anti-criterion) | `ls .github/workflows/ \| grep -c -v claude.yml` | match count == 0 |
| Service installed after update | `launchctl list \| grep -c nightly-tests` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Should `tests/e2e/` be inside the nightly collection?** It is 129 tests and part of the default collection, so excluding it would reopen exactly the gap this plan closes — but `CLAUDE.md`'s testing philosophy is "no mocks, use actual APIs", so a nightly run carries real API spend and real third-party flakiness. The plan currently includes it (that is what "default collection" means). Confirm, or approve carving it out with the tradeoff stated in the docs.

2. **Does any other machine in the fleet currently run the nightly detector?** Recon can only see `Tom's MacBook Air`, where the service is absent and all 20 projects lack a `telegram` block — but this machine's `projects.json` is known to be stale as a fleet inventory. The answer does not change the plan (legs 1, 2, 3 and 5 are machine-independent, and a `tests/unit/`-only detector could never have caught an integration test wherever it ran), but it changes how urgent leg 4 is.

3. **What is the right floor for the run-integrity guard?** The plan proposes `total < 0.9 * prev_total` alongside the absolute `total == 0` check. The ratio catches a partial worker collapse that the absolute check misses, but risks a false trip when a legitimate PR deletes a large test file. Is 10% the right tolerance, or should the ratio check only warn while `total == 0` blocks?

4. **Should Task E (routing through `scripts/pytest-clean.sh`) ship in this plan or separately?** It is the right thing for an unattended 03:00 run — orphan reaping, interpreter-pin refusal, stall watchdog — and the recon run produced 45 `node down` events that would otherwise accumulate nightly. But it is the one task that changes *how* the suite is invoked rather than *what* is collected, and it could be deferred without weakening the core fix.
