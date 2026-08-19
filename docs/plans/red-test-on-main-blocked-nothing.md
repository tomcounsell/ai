---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-19
tracking: https://github.com/tomcounsell/ai/issues/2823
last_comment_id: 5324618203
revision_applied: true
revision_applied_at: 2026-08-19T06:02:16Z
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
- **Finding**: **214 passed, 5 skipped, 0 failed, in 110s.** Fully green. `tests/integration` + `tests/e2e` (1,202 tests) **could not be measured** — three attempts were defeated by test-DB slot exhaustion from concurrent lanes, which is spike-3's finding.
- **Confidence**: high for tools/performance (219 tests, measured); **unmeasured for integration/e2e** (1,202 tests). This is a real gap in the evidence and is carried into Risks and Open Questions rather than papered over.
- **Impact on plan**: Supports widening. Combined with the re-baseline task (Task D), a red tier is survivable regardless: the first widened run takes the `is_first_run` path (:630-632, :691-696) and seeds a baseline rather than dispatching.

### spike-3: Can a scheduled full-collection run silently report a clean night?
- **Assumption**: "A pytest run that fails to execute will surface as a non-zero exit or a parse error, so the detector cannot mistake it for green."
- **Method**: prototype — ran the proposed widened collection directly and inspected exit code and JSON summary shape
- **Finding**: **False, and reproducibly so.** `./scripts/pytest-clean.sh tests/integration tests/e2e tests/tools tests/performance` **exited 0 with zero tests executed** — output `5 warnings in 341.54s`, 45 × `node down: Not properly terminated` — because all 15 test-DB slots were held by concurrent lanes and the #2628 guard correctly refused to collide. `run_tests()` logs `returncode` at :200 but never inspects it, and applies no floor to `summary.total` (:211-222). The returned dict would be `{passed: 0, failed: 0, error: 0, skipped: 0, total: 0, failing_parallel: []}` — indistinguishable from a green suite, which then persists as the new baseline via `save_last_run()` (:745).
- **Confidence**: high — **reproduced three times** across the recon window, at different times and with different worker counts (`-n auto` and `-n 6`), whenever sibling lanes held the slots. The base rate is not exotic: this machine had 5-8 concurrent pytest processes for most of the recon session, and the detector is scheduled for 03:00 while the worker and reflection services run 24/7.
- **Impact on plan**: **This became the highest-value task in the plan (Task B).** It is not a hypothetical: the detector is scheduled for 03:00 local, the worker and reflection services run 24/7, and overnight lanes hold DB slots. Without a run-integrity guard, widening the collection increases the number of nights that silently report green. Fixing leg 3 without fixing leg 5 would make the problem worse, not better.

### spike-7: Does `scripts/pytest-clean.sh` add protection a bare `python -m pytest` lacks?
- **Assumption**: "Routing through the wrapper is hygiene, not a material safety difference — it could be deferred." (This was Open Question 4 before the evidence arrived.)
- **Method**: measurement — a fourth attempt at `tests/integration` + `tests/e2e` via the wrapper, `-n 6`
- **Finding**: **The wrapper's stall watchdog fired and killed the run: exit 143 (SIGTERM) at 637s.** The run was wedged at zero executed tests on DB-slot exhaustion. `PYTEST_STALL_LIMIT_S` defaults to 600s (`pytest-clean.sh:178`), and `watch_for_stall()` (:197-236) TERMed the controller and reaped its workers. **A bare `sys.executable -m pytest` — which is what the detector uses today at :182-199 — has no stall watchdog, and would have burned the full `PYTEST_TIMEOUT_SECONDS` before surfacing anything**, then hit the timeout path at :639-641 that returns 1 *before* any Telegram call.
- **Confidence**: high (direct observation)
- **Impact on plan**: **Task E is promoted from optional to load-bearing, and Open Question 4 is answered by evidence rather than judgment.** The wrapper turns a wedged nightly from a 30-minute silent burn into a bounded SIGTERM at 10 minutes — and paired with Task B's guard, exit 143 is exactly the kind of non-zero, non-clean result the integrity check must classify as infrastructure failure rather than green. Task B's trip conditions must therefore include a SIGTERM-shaped exit (negative or >128), not only pytest's own 2/3/4/5.

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
- **Interface changes**: `scripts/nightly_regression_tests.py` gains a module constant `COLLECTION_PATHS` and one new internal function for run-integrity validation. `run_tests()` changes signature to `-> tuple[dict | None, dict | None, int]` returning `(raw_report, summary_or_None, returncode)` — the raw report is what the validator needs, since one of its trip conditions is a report with no `summary` key at all. `scripts/update/service.py::install_nightly_tests` changes return type from `bool` to `Literal["installed", "skipped", "failed"]`. No CLI flags are added; see Round-2 Revision Notes for the `--paths` decision.
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

- **Task A — widen the collection, and size the run to the machine.** Add a module constant `COLLECTION_PATHS = ["tests/"]` and use it in the pytest argv at :187 and as the value of the new `collection` state field (Task D). Update the six unit-tier string sites spike-5 enumerated (:4, :174, :180, :187, :730, :738) plus the prose comment at :636 so remediation instructions match what actually ran. No CLI flag: the plist stays flag-free, the scheduled run is the only production caller, and a flag whose only production posture is "never passed" buys a suppression branch and a seven-method test ripple to defend against a hazard it creates. Recorded under Round-2 Revision Notes.

  **Two argv changes make the unattended run survivable on a shared machine.** `-n auto` on this 10-core box requests ~10-14 of the 15 test-DB slots by itself (`tests/db_claim.py:66`), and `:180-181` names exactly this state: "Too many concurrent pytest runs on this machine." Pass `-n 6` explicitly instead of `auto`, leaving 9 slots for sibling lanes, and give the subprocess a patient claim window with `env={**os.environ, "TEST_DB_CLAIM_WAIT_S": "600"}` — the 30 s default is documented as "Deliberately short" for *interactive* use (`tests/db_claim.py:69-75`), and a 03:00 job has ten minutes to spare. Neither is a concurrency redesign; both are values in the argv Task A already edits. Without them the shipped end state on this machine is a nightly alert saying the run did not happen, which is a loud detector that detects nothing and that operators learn to mute.

  Raise `PYTEST_TIMEOUT_SECONDS` (:72) — the unit tier alone is already estimated at ~21 min against an 1800s ceiling (`pyproject.toml:188`), and the widening adds ~10% more tests that parallelize worse. **The measurement may be unavailable, so the constant has a deterministic fallback and the comment must say which one it is.** Attempt one widened run. If it completes (exit in `{0, 1}` and `summary.total > 0`), size the constant from the observed wall time and comment it as a measurement with the date and total. If it does not complete, set `PYTEST_TIMEOUT_SECONDS = 5400` and comment it as **"bound, not measurement"**, deriving it as `~1260s unit-tier baseline (pyproject.toml:188) x 1.1 collection growth x 3.5 contention headroom`. Never land the constant with a comment asserting a measurement that was not taken — spike-2 and spike-7 record four defeated attempts on this machine, so the fallback is the expected path, not the exceptional one. Raise `PYTEST_RECONFIRM_TIMEOUT_SECONDS` (:76) on the same rule, or cap the re-confirm set, because its timeout fail-safe marks every input node confirmed (:261-263).

- **Task B — run-integrity guard, keyed on report freshness and error rate.** Add `validate_run_integrity(report, returncode, prev) -> tuple[str | None, list[str]]` returning `(trip_reason_or_None, warnings)`. The caller branches on both: a reason is fatal, and the warnings change how state is persisted (see the shrink case below).

  **The load-bearing check is that a fresh report exists, not the returncode.** `PYTEST_JSON_TMP` (:69) is a fixed `/tmp` path that nothing ever unlinks, so a run whose pytest never executed parses *last night's* report and inherits its healthy totals. Two changes make freshness structural:

  1. In `run_tests()`, `Path(PYTEST_JSON_TMP).unlink(missing_ok=True)` immediately **before** the subprocess — and the same for `PYTEST_SERIAL_JSON_TMP` in `reconfirm_serial`.
  2. Change `run_tests()` to return `tuple[dict | None, dict | None, int]` — `(raw_report, summary_or_None, returncode)` — rather than raising on a parse failure, so the missing-report case reaches the guard instead of `main()`'s silent `return 1` arm at :206-209. The **raw** report is required because one trip condition is "a report with no `summary` key at all", which the summary dict cannot express. `summary_or_None` is the existing `{passed, failed, error, skipped, total, failing_parallel, run_at}` dict, built only when `raw_report` parsed, and it is what `main()` keeps consuming at :644-648 and :669-676 as `current`. The call site becomes:

  ```python
  raw, current, rc = run_tests()
  reason, warnings = validate_run_integrity(raw, rc, prev)
  if reason:
      return _fatal(reason, args.dry_run)
  parallel_failing = current.get("failing_parallel", [])
  ```

  placed so `current is None` is never dereferenced — the guard trips on `raw is None` before any read of `current`.

  Trip conditions, in order: `report is None` → `"pytest wrote no JSON report (exit {rc}) — the run did not happen"`; pytest exit code in `{2, 3, 4, 5}` (interrupted / internal error / usage error / no tests collected); an exit code that is negative or `> 128` (signal death — spike-7 measured 143 from the wrapper's stall watchdog); `summary.total == 0`; a report with no `summary` key at all; the report's collectors contain an `error` outcome; **or the error rate exceeds its ceiling (below).** **Exit code 1 is deliberately not a trip condition** — pytest's 1 means "tests failed", a legitimate red night, and trapping it would convert every real regression into an infrastructure alert.

  **The error-rate ceiling is what catches the dominant contention mode, and total collapse is not it.** `claim_test_db()` memoizes per process and records a *sticky* failure (`tests/db_claim.py:198-203, 213`), so a worker that cannot claim one of the 15 slots inside its wait window raises at setup for **every test it touches** — and every later call re-raises immediately off `_CLAIM_FAILURE` without re-polling. Those land as `outcome == "error"`, which `extract_failing_node_ids` (:159-171) counts alongside `failed`. Such a night therefore produces a fresh report, exit **1**, and `total ≈ 14,899` — every absolute check above passes it as a legitimate red night, and thousands of node IDs flow into `reconfirm_serial`, whose 900 s budget expires and whose catch-all at :259-261 returns `ordered, []`, marking **every** input node confirmed. Dispatch and `save_last_run()` follow. That is this plan's own failure mode reproduced at scale: a detector that misreports. Add:

  ```python
  err = summary.get("error", 0)
  total = summary.get("total", 0)
  if err and err > max(50, 0.02 * total):
      return f"{err} of {total} tests errored at setup — infrastructure failure, not a red suite"
  ```

  2% sits far above any genuine collection-error night and far below a starved-worker night. It keys on `error` specifically, never on `failed`, so a genuinely red tier — which produces `failed` outcomes — is never mistaken for infrastructure no matter how red it is. Enrich the reason with the most common error message found in the report (the exhaustion text at `tests/db_claim.py:178-187` is self-describing) so the alert is actionable rather than a bare count.

  **Bound the re-confirmation input too, as defense in depth.** Add `MAX_RECONFIRM_NODES = 200`; when `len(ordered)` exceeds it, log that the failing set is too large to disambiguate and return `ordered, []` **without** running the serial pass. This is the same result the 900 s timeout already produces, reached in a second instead of fifteen minutes, and it deliberately preserves the fail-safe's "treat all as confirmed" semantics. It must **not** return `[], []`: manufacturing an empty confirmed set from a very red night is a false green, the exact bug class this plan exists to close, and it would also deadlock the detector into re-tripping every night with no baseline ever written. The blast radius downstream is bounded by `MAX_DISPATCH_NODES` (Task C), not by `compute_dispatch_set` — that function subtracts `prior_dispatched`, which the night after a re-baseline holds only the seeded set.

  **A shrunken total warns and changes how state is written.** `total < 0.9 * prev_total` is a real signal for a partial worker collapse, but a PR that legitimately deletes a large test file would trip it and suppress a whole night's genuine results, so it belongs in the returned `warnings` list rather than the trip reason. It is not inert, though: `carry_dispatched_nodes` (:427-440) keeps a previously-dispatched node only while it is still in *this* run's confirmed set (`still_failing = prior_dispatched(prev) & set(confirmed_failing)`), so a truncated night that is trusted persists a `dispatched_nodes` set stripped of every already-filed node that did not run, and the next full night re-dispatches them. `prior_dispatched`'s fallback to `failing_tests` cannot rescue that: it fires only when the key is *absent* (:396-407), and here the key is present-but-shrunken. So when the shrink warning is present, `main()` writes `current["dispatched_nodes"] = sorted(prior_dispatched(prev) | set(just_dispatched))` instead of calling `carry_dispatched_nodes`. Retiring a stale node ID one night late is cheap; re-filing a node that already has an open issue is precisely what the dedup set exists to prevent (#2429/#2430/#2462).

  On a trip: **alert loudly naming the reason, do not save state, do not dispatch, return non-zero** — so the next night still diffs against the last trustworthy baseline. The exit-code list alone would not have caught the recon failure, which exited **0** with zero tests executed; under Task E's wrapper it would not have caught the preflight refusal either, which exits **1** having never invoked pytest; and without the error ceiling none of it catches the contention mode that is most likely to actually occur.

- **Task B2 — one fatal path for every pre-alert exit.** `main()` returns 1 from two arms before any Telegram call is reachable: `except subprocess.TimeoutExpired` (:639-641) and `except (FileNotFoundError, json.JSONDecodeError)` (:642-644). Both go to `logs/nightly_tests.log` and nowhere else. Fixing only the timeout arm leaves the exact arm Task E makes more likely still silent, and makes Success Criterion 2 false for half its surface.

  Collapse both into one helper:

  ```python
  def _fatal(reason: str, dry_run: bool) -> int:
      log(f"FATAL: {reason}")
      send_telegram(f"Nightly tests could not run: {reason}", dry_run=dry_run)
      return 1
  ```

  called as `return _fatal(f"pytest timed out after {PYTEST_TIMEOUT_SECONDS}s", args.dry_run)` and `return _fatal(f"could not parse test results: {exc}", args.dry_run)`. Neither arm may reach `save_last_run()`. `send_telegram()` is documented never-fatal (`docs/features/nightly-regression-tests.md:76-78`), so no extra `try/except` wraps it. The integrity-guard trip in Task B routes through the same helper, giving one alert shape for every "the run did not happen" outcome.

- **Task C — role-correct install gate, an observable skip, and a worktree refusal.** Replace `has_bridge_role()` in `scripts/install_nightly_tests.sh:30-74` with `has_worker_role()`, copied from `scripts/install_reflection_worker.sh:37-80` (the diff is one line: drop `if proj.get("telegram"):`). Keep the fail-open contract on unreadable config, missing venv, and `scutil` error — `tests/unit/test_install_scripts_bootstrap.py` depends on it (its harness sets no `PROJECTS_CONFIG_PATH` and a fresh `$HOME`, so all four parametrized nightly cases reach bootstrap only via fail-open).

  **Make the skip observable at the Python boundary *before* removing the gate that currently makes it observable.** `run.py:2045` is the `else:` arm of the very `if has_bridge:` at :2038, so deleting the gate deletes the only place `/update` can see a skip. The skip then happens inside the shell script, which `exit 0`s on the role gate (`install_nightly_tests.sh:73`), and `service.install_nightly_tests` returns `True` on rc 0 — its docstring says so outright, "installed or cleanly skipped" (`service.py:555-575`). Un-gating alone would therefore make `/update` log **"Nightly tests service installed/verified" on a machine where nothing was installed**: a confident false positive replacing a quiet-but-true INFO line, leaving problem leg 4 open. Sequence it:

  1. Change `service.install_nightly_tests(project_dir) -> Literal["installed", "skipped", "failed"]`, classifying from captured stdout. **Match the success marker, not the skip strings**: `install_nightly_tests.sh:107` already prints the stable line `Nightly regression test service installed successfully.`, so `"installed" if "Nightly regression test service installed successfully." in result.stdout else "skipped"` on rc 0 fails *closed* — the worktree refusal, the role-gate skip, and any early exit added later all read as "skipped" rather than falsely as "installed". Matching skip text instead would silently mis-classify every future early exit as a success.
  2. Do **not** signal skip with a distinct non-zero exit code. `service.py`'s `run_cmd` is `check=False` (:108-113), so a non-zero return falls into the existing `rc != 0` branch and would append a spurious "install failed" warning.
  3. Replace the `if has_bridge:` block at :2038 with a three-way branch on the returned literal, pushing the skip into `result.warnings.append("Nightly tests: no regression coverage on this machine (install skipped)")` so it surfaces in `/update`'s warning channel rather than at INFO. Leave the `has_bridge` assignment at :1858 alone — :1859 still uses it.
  4. Pin the marker string on both sides (shell source and Python matcher) in the new `tests/integration/test_install_nightly_tests.py`, so a cosmetic edit to the installer's success line cannot silently turn every machine's status into "skipped".

  **The installer must refuse to run from a linked worktree.** It writes `$HOME/Library/LaunchAgents/com.valor.nightly-tests.plist`, a machine-global path, substituting `__PROJECT_DIR__` with `$PROJECT_DIR` derived from the script's own location (:7-8) and hardcoding it into `ProgramArguments`, `WorkingDirectory`, `PATH` and both log paths. A lane that runs the installer from `.worktrees/{slug}/` therefore points the fleet's 03:00 job at a directory that merge deletes, and the job fails silently every night thereafter. Add this above the role gate:

  ```bash
  if [ -f "$PROJECT_DIR/.git" ] && grep -q '^gitdir:.*/\.git/worktrees/' "$PROJECT_DIR/.git" 2>/dev/null; then
      echo "Refusing to install nightly-tests from a worktree ($PROJECT_DIR)"
      exit 0
  fi
  ```

  In a linked worktree `.git` is a *file* containing `gitdir: …/.git/worktrees/<name>`; in the main checkout it is a directory, so `-f` alone discriminates (verified on this checkout and on `.worktrees/durability-m1-fence-canary` during revision). Pin it with a source-text assertion in the new `tests/integration/test_install_nightly_tests.py`.

  **Fleet-wide dedup, sized to the dispatcher that actually exists.** `dispatched_nodes` is per-machine local state in one checkout's `data/nightly_tests_last_run.json`. Turning the detector on across the fleet means every newly-covered machine dispatches the same confirmed-failing node on night two and files its own issue — #2429/#2430/#2462 reopened along a machine axis rather than a time axis. The fix is a machine-independent key, but it has to fit the dispatcher: `maybe_dispatch_triage_session` (:503-563) spawns **one** session for the whole `dispatch_nodes` list with a prompt saying to file "a … GitHub issue" — singular, for the batch. A batch title cannot be `<nodeid>`, and any batch-derived title differs between machines whose dispatch sets differ by even one node, which they always will. So pin the *prompt*, not the session count:

  > "For EACH node ID below, search open issues for the exact title `Nightly regression: <nodeid>`. Comment there if one exists; otherwise open an issue with exactly that title."

  One session, per-node issues, dedup by exact string match rather than judgment. Cap the fan-out so an error-storm night that slips past the guard cannot flood the tracker: `MAX_DISPATCH_NODES = 10`; when `len(dispatch_nodes)` exceeds it, log and truncate to the first 10. Mark only the truncated slice as dispatched — `carry_dispatched_nodes(prev, confirmed_failing, just_dispatched)` records exactly what went out, so the remainder is picked up on later nights instead of being lost. A shared Redis-backed dispatch set is the thorough fix and is out of appetite; the title convention plus the cap is what ships here, and the docs say so.

- **Task D — collection-aware baseline.** Add a `collection` field to the state dict recording the paths that produced it. When the recorded collection differs from the current one, treat the run as a baseline seed: take the existing `is_first_run` path (:630-632, :691-696), seeding `dispatched_nodes` and dispatching nothing. This is what stops the widening from re-filing every pre-existing non-unit failure in one night and reopening #2429/#2430/#2462.

  Make the re-baseline **visible**, since a silent seed reads exactly like a quiet night: `log(f"WARNING: collection changed {prev.get('collection')!r} -> {current_collection!r} — re-baselining, dispatching nothing (see #2429/#2430/#2462). Any regression landing tonight is absorbed into the seed and will not be filed until it stops failing and re-fails.")` The second sentence is not decoration — the seed genuinely swallows a same-night unit-tier regression for one commit window, and an operator reading the log should know the widening night is a seed rather than a verdict.

  Add `head_commit` (`git rev-parse HEAD`) in the same edit so a red night is attributable to a SHA — coordinating with #2334's plan, which proposes the same field. It is captured once at run start, for attribution only; see Race 3.

- **Task E — route through `scripts/pytest-clean.sh`.** Replace the bare `sys.executable -m pytest` argv at :182-199 (and the serial re-confirm at :242-258) with the wrapper. Unattended at 03:00 this buys orphan reaping via `trap cleanup EXIT INT TERM HUP PIPE`, the `check-interpreter-pin.sh` refusal, and the `PYTEST_STALL_LIMIT_S` watchdog. Spike-7 measured the watchdog doing exactly its job — SIGTERM at 637s on a wedged run that a bare invocation would have let burn for the full timeout. The recon runs also produced 45 `node down` events; without reaping, those workers reparent to PID 1 and accumulate nightly. `--json-report` passes through the wrapper unchanged since it is a pure argv pass-through.

  **Task E is only safe on top of Task B's freshness checks.** The wrapper exits **1** from both preflight refusals — missing venv pytest (`pytest-clean.sh:142`) and the interpreter-pin refusal (:149-151) — *without invoking pytest at all*. With the fixed `/tmp` report path and no unlink, that is a direct route to parsing last night's healthy report under a returncode the guard must not trip on. Sequencing matters: the unlink and the 3-tuple `(raw_report, summary, returncode)` return land with Task B, before the argv is switched.

- **What this plan deliberately does not do**: add a CI workflow, or add a test leg to the merge predicate or any stage gate. See Rabbit Holes.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `run_tests()` no longer raises on a parse failure — it returns `(None, None, returncode)`. Assert the log line is still emitted and that `None` reaches `validate_run_integrity` rather than `main()`'s exception arm, and that `main()` never dereferences the `None` summary.
- [ ] **Stale-report regression test.** Write a healthy report to `PYTEST_JSON_TMP`, then run with a pytest stub that exits 1 without writing anything. Assert the pre-subprocess `unlink` removed it, that `run_tests()` returns `(None, 1)`, that the guard trips with a reason naming "no JSON report", and that no state is written. This is the Task E false-green in test form.
- [ ] Both `_fatal()` arms alert. Assert `TimeoutExpired` and a corrupt-JSON path each call `send_telegram` with a reason string, each return non-zero, and neither reaches `save_last_run()`.
- [ ] `send_telegram()` is documented as never fatal — assert that an integrity-failure alert *and* both `_fatal()` arms still return non-zero and still skip the state write when the send itself fails.
- [ ] `reconfirm_serial`'s catch-all at :259-261 (`TimeoutExpired/FileNotFoundError/JSONDecodeError/OSError`) marks every input node confirmed. Add a test asserting that behavior is preserved under the widened collection, and that the number of nodes actually dispatched is bounded by `MAX_DISPATCH_NODES` — **not** by `compute_dispatch_set`, which subtracts `prior_dispatched` and is therefore no bound at all the night after a re-baseline.
- [ ] `reconfirm_serial` with more than `MAX_RECONFIRM_NODES` inputs → returns `(all_inputs, [])` without spawning a subprocess. Assert it never returns an empty confirmed set: a very red night must not be converted into a green one.
- [ ] No `except Exception: pass` blocks exist in the touched scope of `nightly_regression_tests.py` — confirm during build and state so.

### Empty/Invalid Input Handling
- [ ] `validate_run_integrity` with `summary.total == 0` → trips. **This is the spike-3 regression test and the single most important test in the plan.**
- [ ] `validate_run_integrity` with `report is None` → trips with the "the run did not happen" reason. Second most important: it is the only guard against the fixed-`/tmp`-path staleness.
- [ ] `validate_run_integrity` with `returncode == 1` and a healthy report → **does not trip.** Pytest's 1 is a legitimate red night; a guard that trapped it would convert every real regression into an infrastructure alert and re-hide the failures this plan exists to surface.
- [ ] `validate_run_integrity` with `prev = {}` (no prior run) → the ratio check must not fire; only the absolute floor applies.
- [ ] `validate_run_integrity` with `total` at 50% of `prev_total` and an otherwise healthy report → **does not trip**, but returns the shrink warning, the warning text reaches the alert body, **and `main()` persists `prior_dispatched(prev) | just_dispatched` instead of calling `carry_dispatched_nodes`.** Assert directly that an already-filed node absent from the truncated run survives into the next state file — that is the #2429/#2430/#2462 re-filing this guard prevents.
- [ ] `validate_run_integrity` with a report missing `summary` entirely → trips rather than raising `KeyError`.
- [ ] **Error-storm case (the blocker-1 regression test).** A fresh report, returncode 1, `total = 14899`, `error = 9000`, `failed = 0` → **trips** with a reason naming the error count, writes no state, dispatches nothing. Paired with its inverse: `total = 14899`, `error = 0`, `failed = 9000` → **does not trip**, because a genuinely red suite is the signal this plan exists to deliver.
- [ ] Error-ceiling boundary: `error` just under `max(50, 0.02 * total)` does not trip; just over does. Assert the `max(50, …)` floor holds for a small collection, so a 40-error night in a 500-test run is not classified as infrastructure.
- [ ] `has_worker_role()` against a `projects.json` with zero projects owned by this machine → skips install and removes any stale plist.
- [ ] Installer worktree refusal: with `$PROJECT_DIR/.git` a file containing `gitdir: /x/.git/worktrees/y`, the script exits 0 having written no plist; with `.git` a directory it proceeds to the role gate.

### Error State Rendering
- [ ] The integrity-failure Telegram message must name the reason (zero collection, bad exit code, total dropped below floor) — assert the reason string reaches the message body, so an operator reading the alert can tell "the suite could not run" from "the suite is red".
- [ ] Assert the integrity-failure path writes no state file, by asserting the pre-existing state file is byte-identical after the run.

## Test Impact

- [ ] `tests/unit/test_nightly_regression_tests.py::TestMainDispatchPersistence` — VERIFY, likely no change: `_run_main` patches `sys.argv` to `["nightly_regression_tests.py", "--dry-run"]` at :513, and no new flag is added, so the seven methods inheriting that fixture (:532, :547, :554, :566, :579, :584, :595) should be unaffected by the argparse block.
- [ ] `tests/unit/test_nightly_regression_tests.py::TestMainDispatchPersistence._RUN_RESULT` (:495-503) — UPDATE: the stub becomes a **3-tuple** `(raw_report, summary, returncode)`, not a bare dict, and the summary must carry a non-zero `total` and a zero `error` or every test trips the new integrity guard.
- [ ] `tests/unit/test_nightly_regression_tests.py::TestRunLock::test_main_returns_0_on_collision_without_running_tests` (:260-281) — UPDATE: patches `sys.argv` at :268; re-check under the new argparse block.
- [ ] `tests/unit/test_nightly_regression_tests.py::TestReconfirmSerial` (:162-198) — UPDATE: fixtures use `tests/unit/...` node IDs; add non-unit node IDs to prove path-agnosticism.
- [ ] `tests/unit/test_nightly_regression_tests.py::TestDeltaLogic` (:74-124) — REPLACE: `_compute_alert` (:77-90) reimplements main()'s alert logic inline using a **count delta**, which no longer matches the set-based `compute_new_failures` (:381-393). It is already drifted; rewrite it against the real function or delete it.
- [ ] `tests/unit/test_nightly_regression_tests.py` — ADD: coverage for `run_tests()`'s argv. **No existing test asserts on it** — `run_tests` is always mocked (:273, :520) — so line 187 is currently untested and the widening would otherwise land with zero coverage. Copy the argv-assertion pattern from `TestMaybeDispatchTriage::test_dispatch_once` (:438).
- [ ] `tests/unit/test_nightly_regression_tests.py` — ADD: a `TestValidateRunIntegrity` class covering every trip condition in Task B, including the spike-3 case (exit 0, total 0), the `report is None` case, and the exit-1-with-healthy-report **non**-trip.
- [ ] `tests/unit/test_nightly_regression_tests.py` — ADD: a stale-report test asserting `PYTEST_JSON_TMP` is unlinked before the subprocess, so a pytest that never runs cannot yield last night's totals.
- [ ] `tests/unit/test_nightly_regression_tests.py` — ADD: `Test_Fatal` covering both pre-alert arms — each alerts, each returns non-zero, neither writes state.
- [ ] `tests/unit/test_nightly_regression_tests.py` — ADD: the error-storm trip test and its red-suite inverse, plus the `MAX_RECONFIRM_NODES` and `MAX_DISPATCH_NODES` bounds.
- [ ] `tests/unit/test_nightly_regression_tests.py` — ADD: a shrink-warning state-persistence test asserting `prior_dispatched` survives a truncated night intact.
- [ ] `tests/unit/test_nightly_regression_tests.py` — UPDATE: any test that asserts `run_tests()` raises must move to the `(raw_report, summary, returncode)` 3-tuple contract.
- [ ] `tests/unit/test_nightly_regression_tests.py::TestMaybeDispatchTriage` (:430-470) — UPDATE: the prompt assertion must cover the per-node `Nightly regression: <nodeid>` instruction, and add a case with more than `MAX_DISPATCH_NODES` nodes asserting the prompt lists exactly 10 and the returned dispatched slice matches.
- [ ] `tests/unit/test_nightly_regression_tests.py::TestMaybeDispatchTriage::test_prompt_does_not_call_the_set_newly_confirmed` (:448-458) — UPDATE: it asserts the literal `"Search open issues first"` is in the prompt. The rewritten per-node instruction changes that wording; re-anchor the assertion on the exact-title search text rather than deleting the guarantee.
- [ ] `scripts/update/service.py::install_nightly_tests` — VERIFY: no test asserts on its `bool` return today (`grep -rn install_nightly_tests tests/` hits only `test_install_scripts_bootstrap.py`'s shell-script table), and `run.py:2039` is its only caller. The `Literal["installed","skipped","failed"]` change is therefore a two-site edit; the new coverage lands in `tests/integration/test_install_nightly_tests.py`.
- [ ] `tests/unit/test_install_scripts_bootstrap.py` (:74-77, :59) — VERIFY, likely no change: the harness reaches bootstrap via the **fail-open** branch (no `PROJECTS_CONFIG_PATH`, fresh `$HOME`), so loosening the predicate keeps all four parametrized cases passing. Confirm empirically rather than by inspection; a change that made the gate fail closed would break `assert len(harness.bootstrap_calls()) == len(harness.labels)` at :261.
- [ ] `tests/integration/test_install_nightly_tests.py` — CREATE: source-text assertions pinning the new gate, copying `tests/integration/test_install_reflection_worker.py:47-68` (`assert "has_worker_role" in installer_src`, `assert 'proj.get("telegram")' not in installer_src`, self-skip and stale-plist removal, fail-open on unreadable config). Plus a **worktree-refusal** assertion pinning the `gitdir:.*/\.git/worktrees/` guard, and a behavioral case running the installer against a fixture directory whose `.git` is such a file, asserting exit 0 and no plist written. Plus a **marker-string pin** asserting the literal `Nightly regression test service installed successfully.` appears in both `scripts/install_nightly_tests.sh` and `scripts/update/service.py`, so the two sides of the installed-versus-skipped classification cannot drift apart. No test pins the nightly gate today.
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
**Impact:** A flood of duplicate issues, exactly the #2429/#2430/#2462 failure the `dispatched_nodes` set was built to stop. Worse in reputation terms than the gap being fixed, because it teaches the team to ignore the detector. **The magnitude is unmeasured**: spike-2 confirmed `tests/tools` + `tests/performance` (219 tests) fully green, but `tests/integration` + `tests/e2e` (1,202 tests) resisted three measurement attempts, so the number of pre-existing red non-unit nodes is unknown.
**Mitigation:** Task D is a hard requirement, not an optimization, and it is designed to make the magnitude irrelevant. The state file records its collection identity, and a changed collection forces the existing `is_first_run` baseline path (:630-632, :691-696), which seeds `dispatched_nodes` and dispatches nothing — so night one files zero issues whether the non-unit tiers are clean or 200-red. Verify by running once against a state file recording the old collection and asserting zero dispatches. Build should also measure the integration tier once on a quiet machine and record the number, so night two's dispatch volume is a known quantity rather than a surprise.

### Risk 2: The widened run exceeds its timeout and goes silent
**Impact:** `main()` returns 1 from **both** of its pre-alert exception arms — `TimeoutExpired` at :639-641 and `(FileNotFoundError, json.JSONDecodeError)` at :642-644 — and every Telegram call (:720/:734/:740) is downstream of them. Either arm produces no notification at all, only a line in `logs/nightly_tests.log`, a file that does not currently exist on this machine. A too-tight timeout converts the fix into a differently-silent detector, and Task E makes the parse arm materially more likely by introducing a wrapper that can exit before writing a report.
**Mitigation:** Task B2 collapses both arms into `_fatal()`, so every pre-alert exit alerts and none reaches `save_last_run()`. This covers the whole class, not the timeout half. On the constant itself, Task A carries a deterministic fallback: attempt the measurement once, and on a run that does not complete, land `5400` with a comment that says **"bound, not measurement"** and shows the derivation. The plan's own evidence (spike-2's three defeated attempts, spike-7's SIGTERM at 637s) says the fallback is the likely path on this machine, so the task is executable as written rather than blocking on a measurement that may never be takeable here. Task B2 is a hard prerequisite of the constant change: a wrong ceiling must page someone.

### Risk 6: A lane installs the LaunchAgent from its worktree
**Impact:** `scripts/install_nightly_tests.sh` writes a machine-global plist that hardcodes `$PROJECT_DIR` into `ProgramArguments`, `WorkingDirectory`, `PATH` and both log paths. Run from `.worktrees/{slug}/`, it aims the fleet's only regression detector at a directory that merge deletes — after which the 03:00 job fails silently every night, which is the precise failure mode this plan exists to eliminate. The exposure is not hypothetical: Success Criterion 6 asks a validator to confirm the service is installed, and Task 7 assigns that job to an agent working in the lane worktree.
**Mitigation:** Closed in code, not prose — Task C adds a worktree refusal above the role gate, discriminating on `.git` being a file whose contents start `gitdir: …/.git/worktrees/`. Pinned by source-text assertion in `tests/integration/test_install_nightly_tests.py`. Success Criterion 6 and its Verification row are reworded to state they run from the main checkout after a real `/update`, so a lane validator marks the row N/A rather than satisfying it unsafely.

### Risk 7: Fleet-wide install multiplies duplicate issue filing
**Impact:** `dispatched_nodes` lives in one checkout's `data/nightly_tests_last_run.json`, so it dedups across nights on one machine and not at all across machines. Task C turns the detector on fleet-wide by design; on night two every newly-covered machine dispatches the same confirmed-failing node and files its own issue. That is #2429/#2430/#2462 reopened along a new axis, and the only remaining guard would be the soft prompt instruction to "search open issues first" — exactly the prompt-level dedup whose insufficiency motivated `dispatched_nodes` in #2559.
**Mitigation:** Task C rewrites the dispatch *prompt* so the single triage session files **one issue per node** under the deterministic title `Nightly regression: <nodeid>`, searching for that exact title first. The dispatcher itself stays one session for the batch (`maybe_dispatch_triage_session` :503-563 is unchanged in shape); only the instruction changes, because a batch-derived title would differ between any two machines whose dispatch sets differ by a single node — which they always will, since `dispatched_nodes` is per-machine and flaky sets diverge. `MAX_DISPATCH_NODES = 10` caps the per-night issue volume this implies, and only the truncated slice is recorded as dispatched, so the remainder is picked up on later nights rather than lost. The per-machine scope of `dispatched_nodes` and the load-bearing role of the title convention are recorded in `docs/features/nightly-regression-tests.md`. A shared Redis-backed dispatch set would be the complete fix and is out of appetite. This also revises Risk 5's claim that fleet reach does not affect the plan: it does not affect the plan's *value*, but the duplicate-filing exposure scales with it.

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
**Mitigation, in two parts, because the race has two shapes and only one was measured.**

*Shape 1 — total collapse.* No worker claims a slot, pytest exits 0 having executed nothing. Measured twice in recon: exit 0, zero tests, `5 warnings in 341.54s`. Task B's `summary.total == 0` and `report is None` checks convert this from a silent false green into a loud, named infrastructure failure with no baseline write.

*Shape 2 — partial starvation, which is the more likely one and was not measured.* Some workers claim slots and some do not. `claim_test_db()` memoizes and records a **sticky** failure (`tests/db_claim.py:198-203`), so each starved worker raises at setup for every test it touches, producing thousands of `error` outcomes that `extract_failing_node_ids` (:159-171) counts as failing. The night then has a fresh report, exit 1, and a full `total` — every absolute check passes it as a legitimate red night. Task B's **error-rate ceiling** is the check that catches this shape, and `MAX_RECONFIRM_NODES` / `MAX_DISPATCH_NODES` bound the damage if a variant slips past it. Task A's `-n 6` and `TEST_DB_CLAIM_WAIT_S=600` reduce how often either shape occurs at all: six workers against fifteen slots, with ten minutes of patience, is a very different proposition from `-n auto` demanding ten to fourteen with thirty seconds.

Neither part eliminates the race — the machine is genuinely shared and that is out of appetite (see Rabbit Holes). Both convert it from a wrong answer into a named one.

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
**Mitigation:** **Accepted, not guarded** — and named as accepted so no reader mistakes it for handled. `head_commit` is captured **once** at run start and persisted for attribution only; mid-run drift is tolerated. The reasoning: the confirmed set is re-derived from scratch every night, so a node mis-confirmed against a moved tree self-corrects within 24 hours, and the worst outcome is one spurious issue that Task C's deterministic title dedups. Guarding it properly would mean widening `validate_run_integrity` to take `head_before`/`head_after` and calling it a second time after `reconfirm_serial`, which buys a 24-hour correction the nightly cadence already provides. Task B's signature stays `validate_run_integrity(report, returncode, prev)` and its single call site stays immediately after the parallel parse.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2852] Repairing the tests the widened detector finds red. #2852 is the open umbrella for driving `main`'s unit suite to zero; non-unit failures surfaced by the widening get their own issues filed by the detector itself, which is the mechanism this plan builds.
- [SEPARATE-SLUG #2334] Autonomous-fix-before-alert behavior for the nightly detector. Already planned and tracked; this plan changes what the detector *collects* and *trusts*, not what it does after a confirmed failure.
- [EXTERNAL] Verifying which other machines in the fleet have `com.valor.nightly-tests` installed. Requires shell access to hosts this agent cannot reach; `projects.json` on this machine is known to be stale as a fleet inventory.

## Update System

`/update` changes are **required** — they are Task C, not an afterthought:

- `scripts/update/run.py:2038` currently wraps the nightly-tests install in `if has_bridge:`, an independent second gate. It must stop gating so the installer's own predicate is the single decision point, matching the pattern `run.py:2016-2023` already argues for with the reflection worker. Keep the `has_bridge` assignment at :1858 — :1859 still uses it.
- The skip must stay observable **through** that un-gating, not because of it. `run.py:2045` is the `else:` arm of the very `if` being removed, so the observability has to move to the Python boundary first: `service.install_nightly_tests` returns `Literal["installed", "skipped", "failed"]`, classified by whether the installer's stable success line `Nightly regression test service installed successfully.` (`install_nightly_tests.sh:107`) appears in captured stdout. Then `run.py` branches three ways and appends `"Nightly tests: no regression coverage on this machine (install skipped)"` to `result.warnings`. Without this ordering, un-gating turns a quiet-but-true INFO line into `"Nightly tests service installed/verified"` on a machine where nothing was installed — a confident false positive, and problem leg 4 still open.
- `scripts/install_nightly_tests.sh` gate changes from `has_bridge_role()` to `has_worker_role()`. On the next `/update`, machines that own a project and previously had no nightly service will install it. This is the intended propagation and needs no migration step: the installer is idempotent (boots out any existing label before bootstrapping) and `launchctl_bootstrap_fail_soft` is called 3-arg, without PID verification, which is correct for a scheduled service (`scripts/lib/launchctl.sh:38-44` names nightly-tests explicitly).
- No new dependencies or config files to propagate. `pytest-json-report` is already a prerequisite and already checked at `install_nightly_tests.sh:78`.
- The plist is unchanged. The collection lives in the module constant `COLLECTION_PATHS` inside the script, so the plist stays flag-free and no reinstall is needed to change scope.

## Agent Integration

No agent integration required — this is scheduled-job infrastructure. The detector already reaches the agent through two existing surfaces, and this plan adds no third:

- **Session dispatch**: `maybe_dispatch_triage_session` (:503-563) shells out to `python -m tools.valor_session create --role eng`, an entry point already declared in `pyproject.toml [project.scripts]`. Task A and B change the *content* of the triage prompt (which paths ran, and the integrity verdict) but not the invocation shape.
- **Telegram alerting**: `send_telegram()` (:271-298) invokes the existing `valor-telegram` binary. Unchanged, and explicitly best-effort — `docs/features/nightly-regression-tests.md:76-78` documents that a failed send never crashes the script, which is precisely why the bridge-role gate in Task C is safe to drop.

No new CLI entry point in `pyproject.toml`, no bridge import, no MCP surface.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/nightly-regression-tests.md` — four statements are now false or misleading and must be corrected, not appended to: `:3-7` states as unconditional present-tense fact that "a launchd job runs the suite each night at 03:00"; `:60` and `:83-85` describe the bridge-role gate being replaced; `:89` says "installed automatically by `/update` on bridge machines" while omitting the second gate at `run.py:2038`; `:104-108` gives a verification command without noting that empty output was the expected state. Document the widened collection, the run-integrity guard and its failure disposition, and the worker-role predicate.
- [ ] Correct three further statements this work falsifies, in the same file and one adjacent script:
  - `docs/features/nightly-regression-tests.md:80-81` — "**Per-count delta, not per-test delta** — Tracking individual test names is out of scope." Already false since #2559 introduced `dispatched_nodes`, and it now reads as guidance *against* the per-node title convention Risk 7 depends on. Rewrite it to describe per-node tracking as the mechanism.
  - `docs/features/nightly-regression-tests.md:76-78` — "The test results are still saved." False under Task B, whose whole point is that an integrity trip writes no state. State the new rule: a failed Telegram send never blocks the state write, but an integrity trip never reaches it.
  - `scripts/install_reflection_worker.sh:32` — documents `has_worker_role()` as "`has_bridge_role()` (install_nightly_tests.sh) MINUS the Telegram-block check", pointing at a function Task C deletes. Reword so the comment stands on its own.
- [ ] Update `docs/features/README.md` index entry if the summary line changes.
- [ ] Update `docs/sdlc/do-merge.md:273-274`, which names `scripts/nightly_regression_tests.py` as "the backstop for anything that slips through". That claim was false in two ways — the backstop ran `tests/unit/` only, and was installed nowhere — and becomes true once this ships. State the new scope explicitly.
- [ ] Add a short note to `docs/sdlc/do-test.md` recording the answer to investigation question (b): no code gates on a full-suite result, deliberately (#2376), and the nightly detector is the compensating control. This is where a future reader will look.
- [ ] Update `tests/README.md:417` (`test_nightly_regression_tests.py` test count, already stale).

### External Documentation Site
Not applicable — this repo has no Sphinx/MkDocs site.

- [ ] Record in `docs/features/nightly-regression-tests.md` that `dispatched_nodes` is **per-machine** state and that the `Nightly regression: <nodeid>` title convention is what makes dedup work across a fleet — a future change to the title format silently reopens #2429/#2430/#2462.
- [ ] Record in the same doc that the installer refuses to run from a linked worktree, and why (the plist is machine-global and hardcodes an absolute project dir).
- [ ] Record in the same doc that a collection change produces a **seed night**, not a verdict: any regression landing that night is absorbed into the baseline and will not be filed until it stops failing and re-fails.

### Inline Documentation
- [ ] Docstring for `validate_run_integrity` stating each trip condition and, critically, **why the returncode alone is insufficient in both directions** — cite the measured exit-0-with-zero-tests case, and state that exit 1 is deliberately excluded because pytest's 1 is a legitimate red night.
- [ ] Comment at the `unlink` call explaining that `PYTEST_JSON_TMP` is a fixed path and a stale report reads as a healthy run — the reason the unlink is not optional cleanup.
- [ ] Comment on the raised `PYTEST_TIMEOUT_SECONDS` stating explicitly whether it is a measurement (with date and total) or a **bound** (with the derivation). Never a comment claiming a measurement that was not taken.
- [ ] Comment on the `collection` state field explaining that a mismatch forces a re-baseline, and naming #2429/#2430/#2462 as what that prevents.

## Success Criteria

- [ ] `scripts/nightly_regression_tests.py` collects the full default collection by default — the same set a bare `scripts/pytest-clean.sh` collects (14,899 tests), including `tests/integration/`.
- [ ] A run that executes zero tests, writes no report, exits with pytest code 2/3/4/5, or dies to a signal produces an infrastructure-failure alert naming the reason, writes no state, dispatches nothing, and returns non-zero. A total that merely shrank against the prior run warns without blocking.
- [ ] The spike-3 scenario is a passing regression test: a JSON report with `summary.total == 0` and returncode 0 must not be treated as a clean night.
- [ ] The stale-report scenario is a passing regression test: a healthy report already at `PYTEST_JSON_TMP` plus a pytest that exits without running must trip the guard, not inherit last night's totals.
- [ ] A returncode of 1 with a healthy report does **not** trip the guard — it is a red night and must alert as a regression, not as infrastructure failure.
- [ ] A report with a full `total`, returncode 1, and an `error` count above `max(50, 0.02 * total)` trips the guard as infrastructure failure; the same report with those outcomes as `failed` rather than `error` does not. This is the blocker-1 criterion: partial worker starvation must not read as a red suite.
- [ ] The number of nodes reaching `maybe_dispatch_triage_session` in any single run is bounded by `MAX_DISPATCH_NODES`, and `reconfirm_serial` never returns an empty confirmed set from its bail-out path.
- [ ] A night carrying the shrink warning persists `prior_dispatched(prev) | just_dispatched`, so an already-filed node that did not run is not re-filed on the next full night.
- [ ] Every `main()` path that returns non-zero before the normal alert block sends a Telegram alert naming the reason, proven by `Test_Fatal` covering both arms plus the two delegation greps in the Verification table.
- [ ] **One widened run has completed with `summary.total > 0`.** Attempted with `-n 6` and `TEST_DB_CLAIM_WAIT_S=600`; a lower worker count is an acceptable way to get the completion, since this criterion proves the collection is *executable*, not how fast it is. A build that cannot achieve this on any worker count must stop and report rather than mark itself done — shipping a detector that has never once run is the failure mode this criterion exists to prevent.
- [ ] `PYTEST_TIMEOUT_SECONDS` carries a comment that is either a measurement (with date and observed total) or an explicit bound (with the derivation). Never a comment asserting a measurement that was not taken.
- [ ] `scripts/install_nightly_tests.sh` gates on `has_worker_role()`; `grep 'proj.get("telegram")' scripts/install_nightly_tests.sh` returns nothing.
- [ ] `scripts/install_nightly_tests.sh` refuses to install from a linked worktree, pinned by a source-text assertion and a behavioral test.
- [ ] `scripts/update/run.py` invokes the nightly-tests installer unconditionally, leaving the shell script as the single decision point. `service.install_nightly_tests` returns `"installed" | "skipped" | "failed"`, and a skip appends to `result.warnings` — so a machine with no regression coverage is visible in `/update`'s warning channel and never reports "installed/verified".
- [ ] `com.valor.nightly-tests` is present in `launchctl list` **on the main checkout's machine, after a real `/update` run from the main checkout.** A validator working in a lane worktree marks this row N/A and records why — installing it from a worktree is the failure mode Risk 6 describes, not a way to satisfy this criterion.
- [ ] A run whose recorded state carries a different collection re-baselines, dispatches zero triage sessions, and logs the re-baseline as a warning naming the absorbed-regression caveat.
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
- Add the module constant `COLLECTION_PATHS = ["tests/"]` and use it in the pytest argv at :187 and as the `collection` state value in task 3. No CLI flag — the argparse block at :610-612 is unchanged.
- Update all six unit-tier strings so log lines and Telegram remediation instructions name what actually ran.
- **Size the run to the slot pool, not the CPU.** Replace `-n auto` with `-n 6` in the argv, and pass `env={**os.environ, "TEST_DB_CLAIM_WAIT_S": "600"}` to the subprocess. `-n auto` on this box demands ~10-14 of 15 slots (`tests/db_claim.py:66`, `:180-181`); six leaves nine for sibling lanes, and a 03:00 job can afford ten minutes of patience where the documented 30 s default is tuned for interactive use (`:69-75`). Without both, the shipped end state on this machine is a nightly "the run did not happen" alert.
- **Attempt the widened run and record the outcome.** A completion with `summary.total > 0` is a Success Criterion in its own right. If `-n 6` cannot complete, retry at a lower worker count; the criterion is that the collection is executable at all, not that it is fast.
- **Timeout constant, with a deterministic fallback.** Attempt one widened run. If it completes (exit in `{0,1}` and `summary.total > 0`), size `PYTEST_TIMEOUT_SECONDS` (:72) from the observed wall time and comment it as a measurement with date and total. If it does not complete — the likely outcome on this machine per spike-2 and spike-7 — set it to `5400` and comment it **"bound, not measurement"** with the derivation `~1260s unit baseline (pyproject.toml:188) x 1.1 growth x 3.5 contention headroom`. Do not block on the measurement, and do not write a comment claiming one that was not taken. Same rule for `PYTEST_RECONFIRM_TIMEOUT_SECONDS` (:76), or bound the re-confirm set instead.
- **Hard prerequisite:** task 2b (`_fatal()`) must land before or with the constant change. A wrong ceiling has to page someone, and today the timeout arm pages nobody.

### 2. Add the run-integrity guard
- **Task ID**: build-integrity-guard
- **Depends On**: build-widen-collection
- **Validates**: tests/unit/test_nightly_regression_tests.py (create `TestValidateRunIntegrity`)
- **Informed By**: spike-3 (reproduced twice: exit 0, zero tests executed, `5 warnings in 341.54s`, all 15 test-DB slots held)
- **Assigned To**: detector-builder
- **Agent Type**: builder
- **Parallel**: false
- **Make the report fresh by construction first.** `Path(PYTEST_JSON_TMP).unlink(missing_ok=True)` immediately before the subprocess in `run_tests()`; same for `PYTEST_SERIAL_JSON_TMP` in `reconfirm_serial`. The path is fixed (:69) and nothing unlinks it today, so a pytest that never runs yields last night's healthy report.
- **Change `run_tests()` to return the 3-tuple `(raw_report, summary_or_None, returncode)`** instead of raising at :206-209, so a missing or corrupt report reaches the guard rather than `main()`'s silent `return 1`. The raw report is what the guard needs; `summary_or_None` is the existing dict `main()` consumes as `current` at :644-648 and :669-676, built only when the report parsed.
- Add `validate_run_integrity(report, returncode, prev) -> tuple[str | None, list[str]]`, tripping on: `report is None` → `"pytest wrote no JSON report (exit {rc}) — the run did not happen"`; exit code in `{2,3,4,5}`; **any exit code >128 or negative (signal death — spike-7 measured exit 143 from the wrapper's stall watchdog)**; `summary.total == 0`; a report with no `summary` key; a collector error outcome; **or `error > max(50, 0.02 * total)`**.
- **The error ceiling is the blocker-1 fix and the most consequential line in this task.** Partial test-DB starvation makes each starved worker raise at setup for every test it touches — the claim failure is sticky (`tests/db_claim.py:198-203`) — and `extract_failing_node_ids` (:159-171) counts `error` alongside `failed`. That night has a fresh report, exit 1, and a full `total`, so every other check passes it as a legitimate red night. Key the ceiling on `error` only, never `failed`, so a genuinely red tier is never misread as infrastructure. Enrich the reason with the most common error message in the report so the alert is actionable.
- Add `MAX_RECONFIRM_NODES = 200`: when `reconfirm_serial` receives more, log and return `(ordered, [])` without spawning the serial pass — the same result the 900 s timeout already produces, reached immediately. It must **not** return `([], [])`: an empty confirmed set manufactured from a very red night is a false green, and it would deadlock the detector into re-tripping nightly with no baseline ever written.
- `total < 0.9 * prev_total` goes in the returned `warnings` list, not the trip reason (a legitimate large test-file deletion would otherwise suppress a real night). But it is not inert: when it is present, `main()` persists `sorted(prior_dispatched(prev) | set(just_dispatched))` instead of calling `carry_dispatched_nodes`, because that function's `prior_dispatched(prev) & set(confirmed_failing)` intersection silently drops every already-filed node a truncated run did not reach, and the next full night re-files them (#2429/#2430/#2462).
- **Exit code 1 must not trip.** Pytest's 1 means "tests failed" — a legitimate red night, and the signal this plan exists to deliver. Add an explicit non-trip test.
- Wire it immediately after the parse in `run_tests()`'s caller, before `reconfirm_serial` and before any read of `current`, so a `None` summary is never dereferenced. Signature stays three-arg in; no head-commit parameters (see Race 3).
- On a trip: alert with the reason named via `_fatal()`, skip `save_last_run()`, skip dispatch, return non-zero.
- Docstring must state why the returncode alone is insufficient **in both directions** — cite the measured exit-0-with-zero-tests case, and state that exit 1 is deliberately excluded.

### 2b. Give every pre-alert exit one fatal path
- **Task ID**: build-fatal-path
- **Depends On**: none
- **Validates**: tests/unit/test_nightly_regression_tests.py (create `Test_Fatal`)
- **Informed By**: Risk 2, critique blocker 3 — `main()` returns 1 from two arms and both page nobody
- **Assigned To**: detector-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_fatal(reason: str, dry_run: bool) -> int` that logs `FATAL: {reason}`, sends `"Nightly tests could not run: {reason}"` via `send_telegram`, and returns 1.
- Route **both** pre-alert arms through it: `except subprocess.TimeoutExpired` (:639-641) and `except (FileNotFoundError, json.JSONDecodeError)` (:642-644). Task B's integrity trip uses it too.
- Neither arm may reach `save_last_run()`. No `try/except` around `send_telegram` — it is documented never-fatal (`docs/features/nightly-regression-tests.md:76-78`).
- Fixing only the timeout arm is not acceptable: the parse arm is the one Task E makes more likely.

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
- Log the re-baseline as a **warning**, naming the old and new collection, citing #2429/#2430/#2462, and stating that a regression landing tonight is absorbed into the seed until it stops failing and re-fails. A silent seed is indistinguishable from a quiet night, which is the bug class this plan exists to close.
- Add `head_commit` from `git rev-parse HEAD` in the same edit, captured **once at run start**, for attribution only. Check whether #2334 landed first and treat it as done if so.

### 4. Route execution through the sanctioned wrapper
- **Task ID**: build-wrapper-routing
- **Depends On**: build-integrity-guard
- **Validates**: tests/unit/test_nightly_regression_tests.py
- **Informed By**: spike-3 (45 `node down` events leave reparented workers)
- **Assigned To**: detector-builder
- **Agent Type**: builder
- **Parallel**: false
- **Do not start this before task 2 lands.** The wrapper exits **1** from both preflight refusals — missing venv pytest (`pytest-clean.sh:142`) and the interpreter-pin refusal (:149-151) — without ever invoking pytest. Against the unmodified fixed `/tmp` report path that is a direct route to parsing last night's report under a returncode the guard must not trip on. The unlink and the 3-tuple `(raw_report, summary, returncode)` return are the prerequisite, not a follow-up.
- Replace the bare `sys.executable -m pytest` argv at :182-199 and :242-258 with `scripts/pytest-clean.sh`.
- Confirm `--json-report` passes through unchanged (the wrapper is a pure argv pass-through).
- Verify the preflight-refusal path end to end: force an off-pin interpreter, assert exit 1 with no report written, and assert the guard trips with the "no JSON report" reason rather than logging a clean run.
- If the wrapper's interpreter-pin refusal or its own reaping conflicts with the unattended context, stop and report rather than working around it — that is a signal, not an obstacle.

### 5. Fix the install role gate
- **Task ID**: build-role-gate
- **Depends On**: none
- **Validates**: tests/unit/test_install_scripts_bootstrap.py, tests/integration/test_install_nightly_tests.py (create)
- **Informed By**: spike-6 (the gate is doubled — the shell script AND `scripts/update/run.py:2038`)
- **Assigned To**: install-builder
- **Agent Type**: builder
- **Parallel**: true
- **Add the worktree refusal first**, above the role gate: if `$PROJECT_DIR/.git` is a file matching `^gitdir:.*/\.git/worktrees/`, echo the refusal and `exit 0`. The plist is machine-global and hardcodes an absolute project dir; installing from a lane worktree aims the fleet's detector at a directory merge deletes (Risk 6).
- Replace `has_bridge_role()` in `scripts/install_nightly_tests.sh:30-74` with `has_worker_role()`, copied from `scripts/install_reflection_worker.sh:37-80`.
- Preserve the fail-open contract on unreadable config, missing venv, and `scutil` error — `tests/unit/test_install_scripts_bootstrap.py` reaches bootstrap only through it. The worktree refusal is a separate, earlier gate and does not change that contract.
- **Make the skip observable before un-gating, in this order.** (i) Change `service.install_nightly_tests` to return `Literal["installed", "skipped", "failed"]`, classifying rc 0 by whether the installer's stable success line `Nightly regression test service installed successfully.` (`install_nightly_tests.sh:107`) is in captured stdout — matching the *success* marker rather than a skip string fails closed, so the worktree refusal and any future early exit read as "skipped" instead of falsely as "installed". (ii) Do **not** signal skip with a non-zero exit code: `service.py`'s `run_cmd` is `check=False` (:108-113), so a non-zero return lands in the existing failure branch and appends a spurious "install failed" warning. (iii) Only then replace the `if has_bridge:` block at :2038 with a three-way branch, pushing the skip into `result.warnings.append("Nightly tests: no regression coverage on this machine (install skipped)")`. Leave the `has_bridge` assignment at :1858 in place — :1859 still uses it. Removing the gate first would delete `run.py:2045`, the `else:` arm that is currently the *only* place `/update` can observe a skip, and leave `/update` reporting "installed/verified" on a machine where nothing was installed.
- Rewrite the triage prompt in `maybe_dispatch_triage_session` (:503-563) to be **per-node inside one session**: "For EACH node ID below, search open issues for the exact title `Nightly regression: <nodeid>`; comment there if found, otherwise open an issue with exactly that title." The dispatcher keeps its single-session shape; only the instruction changes, because a batch-derived title differs between machines whose dispatch sets differ by one node (Risk 7).
- Add `MAX_DISPATCH_NODES = 10` and truncate `dispatch_nodes` to it, logging the truncation. Record only the truncated slice as dispatched, so the remainder is retried on later nights rather than lost.

### 6. Test coverage
- **Task ID**: build-tests
- **Depends On**: build-integrity-guard, build-fatal-path, build-state-versioning, build-wrapper-routing, build-role-gate
- **Validates**: tests/unit/test_nightly_regression_tests.py, tests/integration/test_install_nightly_tests.py
- **Informed By**: Test Impact section
- **Assigned To**: detector-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `TestValidateRunIntegrity` covering every trip condition, with the spike-3 case (exit 0, total 0) as the headline test, plus the `report is None` case and the **exit-1-with-healthy-report non-trip**.
- Add the stale-report test: seed `PYTEST_JSON_TMP` with a healthy report, stub a pytest that exits without writing, assert the guard trips and no state is written.
- Add `Test_Fatal` covering both pre-alert arms — each alerts, each returns non-zero, neither writes state, and each still does so when `send_telegram` itself fails.
- Add the **error-storm** test and its inverse: full `total`, rc 1, `error = 9000` trips; the same shape with `failed = 9000` and `error = 0` does not. Plus the `max(50, 0.02 * total)` boundary in both directions.
- Add the shrink-warning persistence test: an already-filed node absent from a truncated run must survive into the next state file.
- Add bound tests for `MAX_RECONFIRM_NODES` (returns all inputs confirmed, never an empty set, and spawns no subprocess) and `MAX_DISPATCH_NODES` (prompt lists exactly 10; only those are recorded as dispatched).
- Pin the installer's worktree refusal by source text and by a behavioral run against a fixture `.git` file, and pin the `Nightly regression test service installed successfully.` marker in both the shell script and `scripts/update/service.py`.
- Add `run_tests()` argv coverage — none exists today; copy the pattern at `TestMaybeDispatchTriage::test_dispatch_once` (:438). Assert `-n 6` and the `TEST_DB_CLAIM_WAIT_S` env override are both present.
- Give `_RUN_RESULT` (:495-503) the 3-tuple shape with a non-zero `total` and a zero `error`, so the existing seven inherited methods do not trip the new guard.
- Re-anchor `test_prompt_does_not_call_the_set_newly_confirmed` (:448-458) on the new exact-title search text instead of the literal `"Search open issues first"`.
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
- **The `launchctl list` row is not runnable from a lane worktree.** Mark it N/A with the reason, and do NOT run `scripts/install_nightly_tests.sh` to satisfy it — the installer's own worktree refusal should make that impossible, and confirming the refusal fires is the substitute check. The row is closed on the main checkout after a real `/update`, post-merge.

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
| Collection widened | `! grep -q '"tests/unit/",' scripts/nightly_regression_tests.py` | exit code 0 |
| Integrity guard exists | `grep -q 'def validate_run_integrity' scripts/nightly_regression_tests.py` | exit code 0 |
| Report is unlinked before the run | `grep -q 'unlink(missing_ok=True)' scripts/nightly_regression_tests.py` | exit code 0 |
| Fatal helper exists | `grep -q 'def _fatal' scripts/nightly_regression_tests.py` | exit code 0 |
| Timeout arm delegates to `_fatal` | `grep -A3 'except subprocess.TimeoutExpired' scripts/nightly_regression_tests.py \| grep -q '_fatal('` | exit code 0 |
| Parse arm delegates to `_fatal` | `grep -A3 'except (FileNotFoundError, json.JSONDecodeError)' scripts/nightly_regression_tests.py \| grep -q '_fatal('` | exit code 0 |
| Error ceiling exists | `grep -q 'errored at setup' scripts/nightly_regression_tests.py` | exit code 0 |
| Re-confirm and dispatch are bounded | `grep -q 'MAX_RECONFIRM_NODES' scripts/nightly_regression_tests.py && grep -q 'MAX_DISPATCH_NODES' scripts/nightly_regression_tests.py` | exit code 0 |
| Nightly run is slot-sized | `grep -q 'TEST_DB_CLAIM_WAIT_S' scripts/nightly_regression_tests.py` | exit code 0 |
| Install skip is a distinct outcome | `grep -q '"skipped"' scripts/update/service.py` | exit code 0 |
| Zero-collection regression test exists | `grep -q 'TestValidateRunIntegrity' tests/unit/test_nightly_regression_tests.py` | exit code 0 |
| Role gate replaced | `grep -q 'has_worker_role' scripts/install_nightly_tests.sh` | exit code 0 |
| Telegram predicate gone from gate | `! grep -q 'proj.get("telegram")' scripts/install_nightly_tests.sh` | exit code 0 |
| Installer refuses a worktree | `grep -q 'gitdir:.*/\.git/worktrees/' scripts/install_nightly_tests.sh` | exit code 0 |
| `/update` gate un-doubled | `grep -n 'if has_bridge:' scripts/update/run.py` | output does not contain `install_nightly_tests` |
| State records collection identity | `grep -q '"collection"' scripts/nightly_regression_tests.py` | exit code 0 |
| State records head commit | `grep -q 'head_commit' scripts/nightly_regression_tests.py` | exit code 0 |
| No merge-time test gate added (anti-criterion) | `! grep -q 'pytest' tools/merge_predicate.py` | exit code 0 |
| No CI test workflow added (anti-criterion) | `[ "$(ls .github/workflows/ \| grep -cv claude.yml \|\| true)" = 0 ]` | exit code 0 |
| Service installed after update | `launchctl list \| grep -q nightly-tests` | exit code 0 — **main checkout only, after a real `/update`.** N/A from a lane worktree; see Risk 6 |

Every row is exit-code-honest: run it, check `$?`, and `0` means pass. `grep -c` is deliberately absent — it exits **1** when the count is zero, so a row expecting "no matches" would report FAIL on the correct end state. Verified on `main` during revision: `! grep -q 'pytest' tools/merge_predicate.py` and the `ls`/`grep -cv` form both exit 0 today.

The two `_fatal` delegation rows are stated **positively** for the same reason. An earlier revision carried `! grep -nE '^\s+return 1\s*$'`, which can never pass once `_fatal()` lands: the helper's own body ends with a four-space-indented `return 1`, which the pattern matches, so grep exits 0 and the negation exits 1 — FAIL on the correct end state. Confirmed by running the pattern against a stub during this revision. Both delegation rows are **smoke checks only**: each `-A3` pattern matches two sites in the file (one inside `run_tests`, one inside `main`), and the pipe succeeds if either contains `_fatal(`. `Test_Fatal` in the unit suite is the real proof that both `main()` arms alert; the greps exist so a validator gets a fast signal without reading the diff.

## Critique Results

Round 3. Findings adopted in rounds 1-2 are recorded in the Round-2 Revision Notes
section below and are not repeated here; this table is the round-3 record.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | Task A's `env={**os.environ, "TEST_DB_CLAIM_WAIT_S": "600"}` is unreachable and makes the failure it targets worse. `pyproject.toml:196` sets `addopts = "... --timeout=420 --timeout-method=thread"`, and that per-item ceiling covers fixture setup, which is where `claim_test_db()` polls. `tests/db_claim.py:73-74` says so in-code about the current value: "30 s ... stays well inside pytest's `--timeout=420` ceiling." A 600s window never elapses: at 420s `--timeout-method=thread` calls `os._exit`, killing the xdist worker outright — the "node down: Not properly terminated" mechanism `scripts/pytest-clean.sh:154-159` documents — and the restarted worker re-polls with a fresh, non-sticky `_CLAIM_FAILURE` and dies again. This converts the benign contention outcome into the malignant one: today a starved worker raises at setup and produces `outcome == "error"` results, which is exactly what Task B's new error ceiling is built to see. Under 600 those tests never report an outcome at all, `summary.total` collapses or the controller wedges, and the run ends at the stall watchdog (exit 143). Race 1 Shape 2 — the shape the plan calls most likely — is bypassed rather than caught, and Success Criterion "one widened run has completed with `summary.total > 0`" is made *less* achievable by the change introduced to achieve it. | pending | Use `"TEST_DB_CLAIM_WAIT_S": "300"`. 300s stays clear of both `pyproject.toml:196`'s `--timeout=420` and `pytest-clean.sh:178`'s `PYTEST_STALL_LIMIT_S=600` low-CPU window, so a starved worker still raises the sticky `RuntimeError` at `tests/db_claim.py:203` and lands as an `error` outcome the ceiling can count. A longer wait is only legal paired with an explicit `--timeout=<n>` appended to the nightly argv (a CLI `--timeout` follows `addopts` and wins), with `n > TEST_DB_CLAIM_WAIT_S + slack`. Add the invariant as a comment at the constant ("must stay below the effective per-item `--timeout`, see `tests/db_claim.py:74`") and assert it in the new `run_tests()` argv test. |
| CONCERN | Risk & Robustness | Every hardening lands on the parallel run; none reaches the serial re-confirmation, which is the step that decides what gets persisted. `reconfirm_serial` spawns its own pytest at `nightly_regression_tests.py:242-258`, receives neither `-n 6` nor the `TEST_DB_CLAIM_WAIT_S` override (Task A edits only `:187`), and Task B pins `validate_run_integrity` to a single call site *before* `reconfirm_serial` runs. If that serial process cannot claim a slot inside the default 30s, every re-run node errors at setup, `extract_failing_node_ids` (`:159-171`) counts errors as failing, `confirmed = ordered` at `:266`, and `main()` writes `current["failing_tests"] = confirmed_failing` and calls `save_last_run()`. Under Task D that poisoned set becomes the widened collection's seed baseline, after which every genuinely-red node inside it is permanently invisible to `compute_new_failures` (`:392-393`) and `compute_dispatch_set` (`:410-424`) — a red test the detector will never report, which is #2823's own failure mode reproduced by the fix. `MAX_RECONFIRM_NODES = 200` does not cover it: that bound only skips the serial pass when the set is large, so any set of 200 or fewer is exposed. | pending | Two edits in `reconfirm_serial`. (1) Pass the same env on the `subprocess.run` at `:242-258`: `env={**os.environ, "TEST_DB_CLAIM_WAIT_S": "300"}`. (2) After the parse at `:260`, count setup errors by outcome rather than by total — `serial_errors = sum(1 for t in report.get("tests", []) if t.get("outcome") == "error")` — and when `serial_errors >= len(ordered)`, treat the serial pass as untrusted. Do NOT fabricate an artifact entry to signal it; widen the return to `tuple[list[str], list[str], bool]` adding `serial_trusted`, and in `main()` route an untrusted pass through `_fatal("serial re-confirmation errored on every node — the run did not happen")`, skipping `save_last_run()`. Keeping "treat all as confirmed" for the alert while refusing the state write avoids both a false green and a poisoned baseline. |
| CONCERN | History & Consistency | The Test Impact entry for `TestMainDispatchPersistence` reads "VERIFY, likely no change", reasoning only about the now-dropped argparse flag — but Task D will break all seven inherited methods. Not one fixture in that class carries a `collection` key: the `prev` dicts at `tests/unit/test_nightly_regression_tests.py:540`, `:549`, `:556-560`, `:568-572`, `:580` and `:597` are all `{"failing_tests": [...], "dispatched_nodes": [...]}`, and `_run_main` (`:505-530`) writes `prev_state` to the real `LAST_RUN_FILE` and calls the real `nrt.main()`, patching only `run_tests`, `reconfirm_serial`, `summarize_failures`, `maybe_dispatch_triage_session`, `send_telegram` and `run_ttft_gate`. So every method at `:532`, `:547`, `:554`, `:566`, `:579`, `:584`, `:595` takes the seed branch at `nightly_regression_tests.py:690-696`, where `dispatch_nodes = []`. `test_standing_failure_is_not_re_dispatched` asserts `mock_dispatch.assert_called_once_with([fresh])` at `:544` and receives `[]`; the alert branch also flips to "baseline established". Telling the test engineer these need no change is what invites relaxing the assertions instead of updating the fixtures. | pending | Reclassify the entry as UPDATE and name the fixture edit: every `prev` fixture (`:540`, `:549`, `:556`, `:568`, `:580`, `:597`) gains `"collection": COLLECTION_PATHS`, or Task D's mismatch check forces all seven onto the seed path. Add one new test that deliberately omits the key and asserts the mismatch behavior — dispatcher called with `[]`, `saved["dispatched_nodes"] == sorted(confirmed)`, and the re-baseline WARNING logged — so the path is proven rather than exercised by accident. In that new test the mocked `maybe_dispatch_triage_session` must return `None`: `main()` sets `just_dispatched = list(confirmed_failing)` on the seed path and then overwrites it with `dispatch_nodes` if a truthy session id comes back, whereas the real function returns `None` early on an empty list (`:519-520`). |
| CONCERN | History & Consistency | The Verification row `grep -n 'if has_bridge:' scripts/update/run.py` with Expected "output does not contain `install_nightly_tests`" passes today, before any change, and cannot fail after it. Run against `main` during this critique it exits 0 printing three lines — `1859`, `2017` (a comment) and `2038` — none containing `install_nightly_tests`, because that string is on the *following* line (`run.py:2039`). The row is also not exit-code-honest, contradicting the note the plan added directly beneath the table. A count-based variant fails too, since the plan deliberately keeps the `has_bridge` assignment at `:1858` and its use at `:1859`. This is the same defect class as the round-2 NIT about `grep -c` and the `! grep -nE 'return 1'` row, and Task 7 assigns a validator to run exactly these rows — so it yields a green row for work never done. | pending | Replace with `! grep -B2 'service.install_nightly_tests' scripts/update/run.py \| grep -q 'if has_bridge:'`. Verified during this critique: it exits **1 (FAIL) today** — `grep -B2` currently emits the comment at `:2037`, `if has_bridge:` at `:2038` and the call at `:2039` — and exits 0 once the wrapper is removed. `-B2` is the correct window precisely because the unrelated `if has_bridge:` at `:1859` stays, so any whole-file pattern is satisfied forever regardless of the change. |
| CONCERN | Scope & Value | Risk 7 states its own disqualifier and then adopts the disqualified mechanism. Its Impact says the residual guard would be "the soft prompt instruction to 'search open issues first' — exactly the prompt-level dedup whose insufficiency motivated `dispatched_nodes` in #2559", and its Mitigation is a rewritten *prompt* telling a triage session to search for an exact title. Nothing enforces it: `maybe_dispatch_triage_session` (`nightly_regression_tests.py:503-563`) hands the string to `tools.valor_session create` and never sees a title, so no code verifies any issue was filed as `Nightly regression: <nodeid>`. The only scheduled coverage asserts the prompt contains the instruction, which proves the prompt rather than the dedup. It also defends an exposure the plan cannot confirm exists — Resolved Question 2 records fleet membership as unverifiable and `projects.json` as stale — at the cost of a prompt rewrite, `MAX_DISPATCH_NODES`, a truncation branch, a dispatched-slice change, two Test Impact entries and a docs obligation. The #2559 lesson is being re-learned in the same file that documents it (`compute_dispatch_set` docstring, `:415-418`). | pending | Make the convention a value the detector emits rather than a derivation the agent must perform: build `titles = [f"Nightly regression: {n}" for n in dispatch_nodes]` and embed that literal list in the prompt, then assert in `TestMaybeDispatchTriage` that each expected title appears verbatim in `argv[argv.index("--message") + 1]`. That is the one change that makes two machines produce byte-identical titles for the same node, and it is assertable. Then reword Risk 7's Mitigation to say plainly that cross-machine dedup rests on a convention with no shared state, that `MAX_DISPATCH_NODES = 10` bounds the blast radius, and that the shared Redis-backed dispatch set remains the real fix — rather than recording the risk as handled. |
| NIT | History & Consistency | `reconfirm_serial`'s fail-safe catch-all is cited at two different ranges: Task B and the Technical Approach say `:259-261`, the Why Previous Fixes Failed table says `:261-263`. The real location is `nightly_regression_tests.py:261-263` (`:259` is the exit-code log, `:260` the report parse). A builder following the Task B citation edits the wrong lines. | pending | n/a (NIT) — normalize both citations to `:261-263`. |
| NIT | Structural check | Step by Step Task 1 carries the prose "**Hard prerequisite:** task 2b (`_fatal()`) must land before or with the constant change", but its machine-readable `Depends On` is `none` and both task 1 and task 2b are `Parallel: true`. The dependency graph a dispatcher reads contradicts the constraint the prose states. | pending | n/a (NIT) — set task 1's `Depends On: build-fatal-path`, or drop the prose claim if same-agent serialization (both are `detector-builder`) is considered sufficient. |

## Round-2 Revision Notes

Every round-2 finding is adopted. Two of them ship with a **different remedy** than the critique proposed, and one NIT was adopted as a deletion rather than an addition. Recorded here so a reviewer can check the substitution rather than discover it in the diff.

**1. Blocker 1's `reconfirm_serial` bound: adopted the finding, changed the remedy.** The critique proposed `if len(ordered) > MAX_RECONFIRM_NODES: log(...); return [], []`. Returning an empty confirmed set would manufacture a green night out of a very red one — the exact bug class this plan exists to close — and it would also deadlock the detector: with no confirmed set there is nothing to seed, so a machine whose non-unit tiers are genuinely 250-red would re-trip every night and never write a baseline. The bound ships as `return ordered, []`, which is what the existing 900 s timeout fail-safe already produces, reached in a second instead of fifteen minutes. The blast radius is bounded downstream by `MAX_DISPATCH_NODES` instead, and the error-rate ceiling stops the storm before either is reached. The critique's own warning that `compute_dispatch_set` is not a bound is adopted verbatim.

**2. Blocker 2's skip detection: adopted the finding, inverted the marker.** The critique proposed classifying a skip by matching the shell script's `"Skipping nightly-tests install"` text. Matching the **success** line instead — `Nightly regression test service installed successfully.` at `install_nightly_tests.sh:107` — fails closed rather than open: the worktree refusal, the role-gate skip, and any early exit added later all read as "skipped" rather than silently as "installed". Under the critique's form, a future early-exit path would report a false success, which is the same defect the finding is about. Everything else in the remedy is adopted as written, including the warning against a distinct non-zero exit code (`service.py`'s `run_cmd` is `check=False`, so it would append a spurious "install failed").

**3. Concern 2's new Success Criterion: adopted, made satisfiable.** "One widened run has completed with `summary.total > 0`, and its wall time is recorded in the timeout comment" couples two things that can fail independently: the collection being executable at all, and a *representative* wall time. On a contended machine the second may never be obtainable. It ships as two criteria — one completed widened run at any worker count, and a timeout comment that honestly labels itself measurement or bound — with an explicit stop-and-report clause if no worker count completes. The `-n 6` and `TEST_DB_CLAIM_WAIT_S=600` moves are adopted verbatim.

**4. NIT 1 (`--paths`) is adopted as a removal.** The flag is gone rather than defended. That deletes the argparse entry, `paths_are_default`, both suppression branches, the override log line, two Failure-Path bullets, two Test Impact entries, one Success Criterion, and the `_run_main` `sys.argv` change that rippled through seven inherited test methods. `COLLECTION_PATHS` remains as a module constant because Task D needs it for the state file's `collection` field regardless.

**Nothing is left unresolved.** No finding was judged wrong and dropped.

---

## Resolved Questions

The plan is settled. Each question the draft raised now carries a decision the builder can act on without a check-in.

1. **`tests/e2e/` stays inside the nightly collection.** It is 129 tests and part of the default collection; carving it out would reopen the exact gap this plan closes, and "the detector collects what a bare `scripts/pytest-clean.sh` collects" is the one property that makes "the default collection is red" and "the detector is red" the same statement. The API-spend and third-party-flakiness cost is real and is absorbed: 129 tests once a night, with xdist artifacts already filtered by the serial re-confirmation gate. Revisit as a separate issue if the spend shows up.

2. **Fleet membership is not a blocker, but it is no longer treated as value-neutral.** Recon can see only `Tom's MacBook Air`, and this machine's `projects.json` is stale as a fleet inventory. Legs 1, 2, 3 and 5 are machine-independent, so the plan's value stands whatever the answer. Risk 7 now carries the part that *does* depend on the answer — duplicate filing scales with the number of covered machines — and Task C's deterministic-title dedup is sized for an unknown fleet rather than a known one.

3. **The ratio check warns; the absolute checks and the error ceiling block.** `total < 0.9 * prev_total` is a genuine signal for a partial worker collapse, but a legitimate PR deleting a large test file would trip it and suppress a real night's results. It ships as a warning carried into the alert text. It is **not inert**, though — round 2 found that a trusted truncated night silently strips already-filed nodes out of `dispatched_nodes` via `carry_dispatched_nodes`, so the shrink warning now also switches `main()` to a union-preserving state write. Blocking is done by `report is None`, `total == 0`, the exit-code list, and the error-rate ceiling, which are the checks with measured or mechanically-demonstrated failure modes behind them.

4. **Build does not schedule an integration-tier failure-count measurement, but it must complete one widened run.** Four recon attempts at counting the tier's red nodes were defeated by test-DB slot exhaustion, and Task D makes night one safe regardless — the collection mismatch forces a baseline seed that dispatches nothing whether the tier is clean or 200-red. That number becomes known for free on night two from the detector's own first dispatch set. Distinct from that: the build **does** have to get the widened collection to execute once with `summary.total > 0`, at whatever worker count achieves it, because a detector that has never run is not a shipped detector. The timeout constant still has its deterministic fallback bound for the case where the completing run is not a representative one.

<!-- The first draft's Open Question 4 asked whether Task E could be deferred.
     spike-7 answered it with evidence: the wrapper's stall watchdog fired at
     637s on a wedged run that a bare invocation would have let burn for the
     full timeout. Task E ships in this plan. -->

