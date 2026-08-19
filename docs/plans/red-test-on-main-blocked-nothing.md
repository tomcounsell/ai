---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-19
tracking: https://github.com/tomcounsell/ai/issues/2823
last_comment_id: 5324618203
revision_applied: true
revision_applied_at: 2026-08-19T07:56:28Z
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

A test that **becomes** red on `main` under default collection, after the baseline is seeded, produces a filed issue within 24 hours, on whatever machine owns a checkout. The population already red when the widening lands is absorbed into the seed and escalated **once**, as a single umbrella issue, not per node — see Task D. A detector that could not actually run says so loudly instead of reporting a clean night.

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

**The concrete seed set, measured on `main` during the round-7 revision.** `main` is red right now with exactly **three** confirmed nodes, and these are what the re-baseline will absorb:

- `tests/unit/test_sdlc_dispatch.py::TestDispatchRecordLease::test_lease_lost_between_peek_and_write_refuses`
- `tests/unit/test_sdlc_meta_set.py::TestMetaSetWriteMeta::test_pr_number_writes_ledger_field_not_meta_key`
- `tests/unit/test_subprocess_test_db_isolation.py::test_every_test_subprocess_inherits_the_claimed_test_db`

Three facts follow, and the plan's re-baseline narrative is written against them rather than against a hypothetical flood. (i) **#2823's own test now passes** — PR #2826 repaired it — so this lane is not fixing the instance, only the class. (ii) These three are the expected pre-existing baseline; **repairing them is not this lane's job** (No-Gos, #2852). (iii) `seed_size` is small today, so the umbrella escalation in Task D is cheap on the realistic path and the `MAX_DISPATCH_NODES = 10` fan-out cap is slack rather than binding. That does **not** make the seed-wipe hazard of blocker 1 academic: with three seeded nodes, a wiped seed hands all three to `compute_dispatch_set` on night two, well under the cap, so every one of them is filed as a fresh per-node issue — the #2429/#2430/#2462 churn at full strength. The population is also live, not static: `test_every_test_subprocess_inherits_the_claimed_test_db` is brand-new drift that no earlier `prev` ever recorded, which is exactly the node a `prior_dispatched(prev) & confirmed_failing` intersection drops.

**Notes:** No drift invalidated any premise. Every leg of the current behavior was re-verified against `57c8986e4` during recon rather than inherited from the issue text. Seven other lanes were merging into `main` concurrently during this revision; unrelated churn from them is not treated as a premise change.

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
- **#2658** — `docs/plans/gates-that-cannot-fire.md` (`status: Planning`). The closest prior art there is: its Problem is "three independent gates in this repo each reported success while being structurally incapable of detecting the failure they exist to catch", which is legs 1 and 2 of this plan stated generically. It is also the source of the convention this plan's own Verification table follows — **two-pole mutation checking**: every row is run against `main` before it is written down, and a row that cannot exit non-zero today is not a check. Rounds 2, 3 and 4 each produced a finding against this table for exactly that defect; rounds 5 and 6 re-ran every non-anti-criterion Verification row against `main` and every one exits non-zero, so the table is falsifiable end to end.

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
- **Interface changes**: `scripts/nightly_regression_tests.py` gains three module constants — `COLLECTION_PATHS`, the env-overridable `NIGHTLY_XDIST_WORKERS`, and the env-overridable `MIN_EXPECTED_COLLECTED` (the pre-baseline coverage floor) — plus `MAX_RECONFIRM_NODES` and `MAX_DISPATCH_NODES`, and one new internal function for run-integrity validation. `load_env_or_die()` changes from raising `SystemExit(1)` to returning `tuple[int, str | None]`, so its two refusal paths reach `_fatal()` instead of dying silently. `run_tests()` changes signature to `-> tuple[dict | None, dict | None, int]` returning `(raw_report, summary_or_None, returncode)` — the raw report is what the validator needs, since one of its trip conditions is a report with no `summary` key at all. `reconfirm_serial()` changes from `-> tuple[list[str], list[str]]` to `-> tuple[list[str], list[str], bool]`, adding `serial_trusted`. `maybe_dispatch_triage_session()` changes from `(dispatch_nodes: list[str]) -> str | None` to `(dispatch_nodes: list[str], *, prompt: str | None = None, slug_suffix: str | None = None) -> str | None`: the function builds its prompt internally today (`:503-563`) and takes no prompt argument, so Task D's umbrella escalation cannot be expressed through it without this. Both new parameters are keyword-only with defaults, so every existing call site is unchanged. `scripts/update/service.py::install_nightly_tests` changes return type from `bool` to `Literal["installed", "skipped", "failed"]`. No CLI flags are added; see Round-2 Revision Notes for the `--paths` decision.
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

  **Two argv changes make the unattended run survivable on a shared machine.** `-n auto` on this 10-core box requests ~10-14 of the 15 test-DB slots by itself (`tests/db_claim.py:66`), and `:180-181` names exactly this state: "Too many concurrent pytest runs on this machine." Pass an explicit worker count instead of `auto`, and give the subprocess a patient claim window with `env={**os.environ, "TEST_DB_CLAIM_WAIT_S": "300"}` — the 30 s default is documented as "Deliberately short" for *interactive* use (`tests/db_claim.py:69-75`), and a 03:00 job has five minutes to spare. Both are values in the argv Task A already edits.  Without them the shipped end state on this machine is a nightly alert saying the run did not happen, which is a loud detector that detects nothing and that operators learn to mute.

  **The worker count is an env-overridable constant, not a pinned literal.** `6` is derived entirely from this machine — 15 slots, ~10 cores, headroom for sibling lanes — and Task C's whole purpose is to run the detector on machines whose core count and slot pressure are unknown and, per Resolved Question 2, unknowable from here. A hardcoded 6 pinned by a unit assertion oversubscribes a 4-core machine, wastes most of a 24-core one, and makes adaptation a code-plus-test change rather than a config change. The file already establishes the convention twice — `MIN_ENV_KEYS = int(os.environ.get("NIGHTLY_MIN_ENV_KEYS", "10"))` at `:96` and `PYTEST_RECONFIRM_TIMEOUT_SECONDS` at `:76`, both carrying an explicit "provisional / tunable" comment, the same convention as `tests/db_claim.py:66`'s `TEST_DB_POOL_MAX`. So add:

  ```python
  # Provisional/tunable. 6 is derived from this machine: 15 test-DB slots
  # (tests/db_claim.py:66) with nine left for sibling lanes. `-n 4` is the
  # practical floor — see the derivation at the timeout constant below.
  NIGHTLY_XDIST_WORKERS = os.environ.get("NIGHTLY_XDIST_WORKERS", "6")
  ```

  and build the argv as `"-n", NIGHTLY_XDIST_WORKERS`. The argv test asserts the **constant** (`argv[argv.index("-n") + 1] == nrt.NIGHTLY_XDIST_WORKERS`), never the string `"6"`, so a machine-specific override does not turn the suite red. This is a different case from `TEST_DB_CLAIM_WAIT_S = "300"`, which stays pinned as a literal: that value has a real invariant behind it (`pyproject.toml:196`'s `--timeout=420`) and its test asserts the invariant, not the number. The worker count has no such invariant.

  **`-n 4` is the practical floor, and the ladder below it does not exist.** "Retry at a lower worker count" is only a real fallback if a slower run may still finish, and it may not: `PYTEST_TIMEOUT_SECONDS` lands at `5400` (below) and Task B routes `TimeoutExpired` through `_fatal()`, so a run slower than the ceiling terminates as infrastructure failure rather than the completion the Success Criterion demands. Scaling the plan's own baseline — `pyproject.toml:188`, verbatim "a full run is ~21 min across ~10 workers" (~1260 s) — by the ~1.1 collection growth and inversely by worker count gives ~2300 s at `-n 6`, ~3500 s at `-n 4`, **~6900 s at `-n 2`** and ~13,900 s at `-n 1`. Only `-n 4..6` fits inside 5400 s, and every one of those still needs 4-6 of the 15 slots. State the floor in the task; do not offer a ladder whose lower rungs cannot pass.

  **The claim window is bounded by the stall watchdog, not by `--timeout=420`, and 300 is chosen against the bound that governs.** `tests/db_claim.py:74` states its invariant about the current 30 s default ("stays well inside pytest's `--timeout=420` ceiling"), and an earlier revision of this plan reasoned from it. That reasoning is stale: since #2628 `claim_test_db()` polls inside `tests/conftest.py::pytest_configure` (`:274-288`), which runs before collection and therefore before any test item exists. pytest-timeout arms its timer in the runtest protocol, so **no per-item timer is armed while the claim polls**, and the fixture's own call at `:883` never re-enters the poll. Under Task E the governing bound is `scripts/pytest-clean.sh:178`'s `PYTEST_STALL_LIMIT_S` — a 600 s low-CPU window measured on the controller, which a fleet of workers sitting in a poll loop will trip. **300 s sits at half that window**, leaving room for the sample cadence (`STALL_SAMPLE_S=30`) and for the run to make progress once slots free. Write that as the comment at the constant: *300 s. Bounded by `scripts/pytest-clean.sh:178`'s `PYTEST_STALL_LIMIT_S` (600 s low-CPU window on the controller) under Task E, not by `pyproject.toml:196`'s `--timeout=420` — since #2628 the claim runs in `tests/conftest.py::pytest_configure`, before any test item, so no per-item timer is armed.* There is deliberately **no** "legal only with an explicit `--timeout=<n>`" escape hatch: appending a CLI `--timeout` would change nothing about a claim that no timer covers, and offering it would send the next reader to tune the wrong dial.

  Raise `PYTEST_TIMEOUT_SECONDS` (:72) — the unit tier alone is already estimated at ~21 min against an 1800s ceiling (`pyproject.toml:188`), and the widening adds ~10% more tests that parallelize worse. **The measurement may be unavailable, so the constant has a deterministic fallback and the comment must say which one it is.** Attempt one widened run. If it completes (exit in `{0, 1}` and `summary.total > 0`), size the constant from the observed wall time and comment it as a measurement with the date and total. If it does not complete, set `PYTEST_TIMEOUT_SECONDS = 5400` and comment it as **"bound, not measurement"**, deriving it as `~1260s unit-tier baseline (pyproject.toml:188) x 1.1 collection growth x 3.5 contention headroom`. Never land the constant with a comment asserting a measurement that was not taken — spike-2 and spike-7 record four defeated attempts on this machine, so the fallback is the expected path, not the exceptional one. Raise `PYTEST_RECONFIRM_TIMEOUT_SECONDS` (:76) on the same rule, or cap the re-confirm set, because its timeout fail-safe marks every input node confirmed (:261-263).

  **Run the completion attempt as a probe outside the constant, and take night one as the fallback.** The attempt must not be bound by a ceiling the same task is still choosing, so do not run it through `run_tests()`. Invoke the collection directly:

  ```bash
  TEST_DB_CLAIM_WAIT_S=300 ./scripts/pytest-clean.sh tests/ -n 4 \
      --json-report --json-report-file=/tmp/widen_probe.json
  ```

  Same collection, same wrapper, no `PYTEST_TIMEOUT_SECONDS`; read `summary.total` out of that report. If the probe cannot complete because slots are exhausted, the criterion is instead satisfied **post-merge by night one's own baseline alert**: the seed path already sends "baseline established: N tests, M confirmed failures" carrying `current['total']` (Resolved Question 4), so a night-one alert with `total > 0` is direct evidence the collection executed. Stop-and-report is reserved for a probe that fails for any reason *other* than slot exhaustion — a collection error, an import failure, a wrapper refusal — because those say the collection is not executable, which is what the criterion is actually about.

- **Task B — run-integrity guard, keyed on report freshness and error rate.** Add `validate_run_integrity(report, returncode, prev) -> tuple[str | None, list[str]]` returning `(trip_reason_or_None, warnings)`. The caller branches on both: a reason is fatal, and the warnings change how state is persisted (see the shrink case below).

  **The load-bearing check is that a fresh report exists, not the returncode.** `PYTEST_JSON_TMP` (:69) is a fixed `/tmp` path that nothing ever unlinks, so a run whose pytest never executed parses *last night's* report and inherits its healthy totals. Two changes make freshness structural:

  1. In `run_tests()`, `Path(PYTEST_JSON_TMP).unlink(missing_ok=True)` immediately **before** the subprocess — and the same for `PYTEST_SERIAL_JSON_TMP` in `reconfirm_serial`.
  2. Change `run_tests()` to return `tuple[dict | None, dict | None, int]` — `(raw_report, summary_or_None, returncode)` — rather than raising on a parse failure, so the missing-report case reaches the guard instead of `main()`'s silent `return 1` arm at :206-209. The **raw** report is required because one trip condition is "a report with no `summary` key at all", which the summary dict cannot express. `summary_or_None` is the existing `{passed, failed, error, skipped, total, failing_parallel, run_at}` dict, built only when `raw_report` parsed, and it is what `main()` keeps consuming at :646-650 and :669-676 as `current`. The call site becomes:

  ```python
  raw, current, rc = run_tests()
  reason, warnings = validate_run_integrity(raw, rc, prev)
  if reason:
      return _fatal(reason, args.dry_run)
  parallel_failing = current.get("failing_parallel", [])
  ```

  placed so `current is None` is never dereferenced — the guard trips on `raw is None` before any read of `current`.

  **Own the process group — Task E makes `subprocess.run(..., timeout=)` unsafe.** Today the direct child is the pytest controller, so CPython's `process.kill()` on `TimeoutExpired` SIGKILLs pytest itself. Under Task E the direct child is bash, and SIGKILL runs no bash trap, so `scripts/pytest-clean.sh:109`'s `trap cleanup EXIT INT TERM HUP PIPE` never fires and the controller launched at `:241` (`"$PYTEST_BIN" "$@" &`, deliberately not `exec` — see the in-code comment at `:238-240`) survives with its xdist workers, unowned and still holding test-DB slots. `cleanup()` at `:101-103` calls only `reap_workers`, which pgreps the xdist-worker regex at `:42` and never targets the controller, so even a graceful SIGTERM to the wrapper leaves it. Task E's advertised purchase is orphan reaping; on the timeout path it buys none, and Task A raises the ceiling to 5400s on exactly that path while Task B2 makes it louder without making it bounded. Close it inside the function Task B is already rewriting, at both `run_tests()` (:182-199) and `reconfirm_serial` (:242-258):

  ```python
  proc = subprocess.Popen(argv, cwd=PROJECT_DIR, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, env=env,
                          start_new_session=True)
  try:
      out, err = proc.communicate(timeout=PYTEST_TIMEOUT_SECONDS)
  except subprocess.TimeoutExpired:
      os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
      time.sleep(10)
      os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
      proc.wait()
      raise
  ```

  `start_new_session=True` is what makes `killpg` safe: the wrapper, pytest and every xdist worker land in a process group this script owns, so the kill cannot reach a sibling lane's run and needs no pattern kill (forbidden by `.claude/hooks/validators/validate_no_broad_process_kill.py`). This is three lines inside one function, not a redesign, and it must land **with** Task B rather than with Task E — otherwise both get rewritten later.

  Trip conditions, in order: `report is None` → `"pytest wrote no JSON report (exit {rc}) — the run did not happen"`; pytest exit code in `{2, 3, 4, 5}` (interrupted / internal error / usage error / no tests collected); an exit code that is negative or `> 128` (signal death — spike-7 measured 143 from the wrapper's stall watchdog); `summary.total == 0`; a report with no `summary` key at all; **`total` below its coverage floor (below) — the check that actually catches test-DB starvation**; or the error rate exceeds its fixture-error ceiling (below). **Exit code 1 is deliberately not a trip condition** — pytest's 1 means "tests failed", a legitimate red night, and trapping it would convert every real regression into an infrastructure alert.

  **There is deliberately no collectors leg, and the docstring must say why.** An earlier revision listed "the report's collectors contain an `error` outcome". That condition can never fire: `pytest_jsonreport/serialize.py:17-29` builds each collector entry from `report.outcome` of a `CollectReport`, whose value is only ever `passed`, `failed` or `skipped`. Widening it to `outcome != "passed"` is worse than inert — one broken import in one test file is a genuine red-on-main regression of exactly the class this plan exists to surface, and routing it through `_fatal` would write no state, dispatch nothing, file no issue, and recur every night with no baseline ever written, leaving the detector permanently dark. It would also contradict `main()`'s collection-error alert branch at `:735-740`, which this plan keeps. A collection failure contributes no test item at all — `summary` is `Counter([t["outcome"] for t in tests])` (`serialize.py:104-108`) — so it is invisible to every summary-keyed check by construction. If a signal is wanted, it belongs in `warnings` as `len([c for c in report.get("collectors", []) if c.get("outcome") == "failed"])`, with `:735-740` remaining the reporting path. Never a `_fatal` trip.

  **Starvation is visible as MISSING TESTS, never as `error` outcomes — so the blocking check is a coverage floor.** Since #2628 the test-DB claim happens in `tests/conftest.py::pytest_configure` (`:274-288`), which runs *before collection* and, on `RuntimeError`, calls `pytest.exit(str(exc), returncode=3)`. The in-code comment at `:286` states the intent verbatim: **"One line of output instead of a setup error on every collected test."** The `redis_test_db` fixture's own `claim_test_db()` call (`tests/conftest.py:883`) is never reached, because `pytest_configure` has already either memoized a slot or aborted the session. A starved xdist worker therefore contributes **zero `error` outcomes and zero test items**; it dies as "node down: Not properly terminated" — the 45 events spike-3 measured. Total collapse gives `total == 0` and exit **0**. Partial starvation gives a **reduced** `total`, `error == 0`, and the same exit **0**: a fresh report, a legal exit code, a non-zero `total`, a present `summary`. Every absolute check passes it. A night in which thousands of tests never ran would be persisted as the baseline, and every red test inside a starved shard would be invisible — #2823's own failure mode rebuilt inside its fix.

  The check that sees it is a **blocking coverage floor on `total`**, in two forms because the first widened night has no comparable predecessor:

  ```python
  floor = None
  if prev.get("collection") == COLLECTION_PATHS and prev.get("total"):
      floor = 0.9 * prev["total"]
  elif MIN_EXPECTED_COLLECTED:
      floor = 0.9 * MIN_EXPECTED_COLLECTED
  if floor is not None and total < floor:
      return f"only {total} tests ran against a floor of {floor:.0f} — the run was truncated, not green"
  ```

  `MIN_EXPECTED_COLLECTED` is a provisional/tunable module constant in the `MIN_ENV_KEYS` (`:96`) convention — `int(os.environ.get("NIGHTLY_MIN_EXPECTED_COLLECTED", "<probe total>"))` — seeded from the `summary.total` Task A's probe already reads out of `/tmp/widen_probe.json`. When the probe is defeated by slot exhaustion and the night-one fallback is taken instead, it is seeded from night one's own `total` on the re-baseline path in Task D, in the same edit that writes `seed_size`. Until it is seeded it is `0`, and the same-collection ratio is the only floor — which is correct, because an unseeded constant is an unmeasured one and a fabricated floor is a gate that lies in the other direction.

  **This reverses Resolved Question 3's disposition for the deep-shrink case, and the reasoning changed with the mechanism.** That question ruled the ratio a warning because a PR legitimately deleting a large test file would otherwise suppress a real night. It would — for one night, with a one-command remedy, and a loud named reason an operator can read. A silently truncated night has no remedy because nobody knows it happened. The two failure directions are not symmetric, and only the second is the bug class this plan exists to close.

  **The error ceiling stays, but it is no longer the starvation guard.** A genuine fixture-level error storm — a broken conftest import, a missing service, a Redis that is down rather than full — still produces real `error` outcomes worth trapping, and `extract_failing_node_ids` (:159-171) counts `error` alongside `failed`, so an unbounded storm would flood `reconfirm_serial` and then dispatch. Keep:

  ```python
  err = summary.get("error", 0)
  total = summary.get("total", 0)
  if err and err > max(50, 0.02 * total):
      return f"{err} of {total} tests errored at setup — infrastructure failure, not a red suite"
  ```

  and describe it in the docstring as the **fixture-error** ceiling. It keys on `error` specifically, never on `failed`, so a genuinely red tier — which produces `failed` outcomes — is never mistaken for infrastructure no matter how red it is. Enrich the reason with the most common error message found in the report so the alert is actionable rather than a bare count. What it must **not** claim, in code comment, docstring or test name, is that it catches test-DB starvation: that claim is what round 6 falsified, and re-asserting it would leave the next reader trusting a gate that cannot fire for the cause named beside it.

  **Bound the re-confirmation input too, as defense in depth.** Add `MAX_RECONFIRM_NODES = 200`; when `len(ordered)` exceeds it, log that the failing set is too large to disambiguate and return `ordered, []` **without** running the serial pass. This is the same result the 900 s timeout already produces, reached in a second instead of fifteen minutes, and it deliberately preserves the fail-safe's "treat all as confirmed" semantics. It must **not** return `[], []`: manufacturing an empty confirmed set from a very red night is a false green, the exact bug class this plan exists to close, and it would also deadlock the detector into re-tripping every night with no baseline ever written. The blast radius downstream is bounded by `MAX_DISPATCH_NODES` (Task C), not by `compute_dispatch_set` — that function subtracts `prior_dispatched`, which the night after a re-baseline holds only the seeded set.

  **The serial re-confirmation needs the same hardening, because it is the step whose output becomes the baseline.** Everything above lands on the parallel run. `reconfirm_serial` spawns its own pytest at `:242-258`, inherits neither `-n 6` nor the claim-wait override (Task A edits only `:187`), and `validate_run_integrity` has a single call site that runs *before* it. If that serial process cannot claim a slot inside the default 30 s, every re-run node errors at setup, `extract_failing_node_ids` (`:159-171`) counts errors as failing, `confirmed = ordered` falls out at `:266`, and `main()` writes `current["failing_tests"] = confirmed_failing` and calls `save_last_run()`. Under Task D that poisoned set becomes the widened collection's **seed baseline**, after which every genuinely-red node inside it is permanently invisible to `compute_new_failures` (`:392-393`) and `compute_dispatch_set` (`:410-424`) — a red test the detector will never report, which is #2823's own failure mode reproduced by its fix. `MAX_RECONFIRM_NODES = 200` does not cover this: it only skips the serial pass when the set is *large*, so any set of 200 or fewer is fully exposed. Two edits close it:

  1. Pass the same env on the serial `subprocess.run` at `:242-258`: `env={**os.environ, "TEST_DB_CLAIM_WAIT_S": "300"}`. Same ceiling reasoning as Task A.
  2. After the parse at `:260`, key trust on **result coverage**, not on the `error` outcome: `seen = {t.get("nodeid") for t in report.get("tests", [])}`, then `serial_trusted = all(n in seen for n in ordered)`. The pass is trusted exactly when it produced a result for every node it was asked about. Do **not** signal untrust by fabricating an artifact entry. Widen the return to `tuple[list[str], list[str], bool]` — `(confirmed, artifacts, serial_trusted)` — and in `main()` route `serial_trusted is False` through `_fatal("serial re-confirmation returned no result for every node — the run did not happen")`, skipping `save_last_run()` and dispatch.

  **Coverage, not an `error` count, because the serial pass fails the same way the parallel one does — and here the failure direction is a false green.** An earlier revision computed `serial_errors >= len(ordered)` over `outcome == "error"` entries. Under the mechanism that actually exists, a serial `-n0` process that cannot claim a slot inside its window hits `tests/conftest.py:287`'s `pytest.exit(returncode=3)`; `pytest.exit` raises `Exit`, which `wrap_session` handles by still running `pytest_sessionfinish` — the hook `pytest-json-report` writes from — so a report with **zero test entries** is produced. `serial_errors` is then `0`, `0 >= len(ordered)` is False for any non-empty input, and the pass is marked trusted. `serial_failing` is empty, so `:266-267` yields `confirmed = []` and `artifacts = ordered`: every genuinely-red node reclassified as an xdist artifact, `current["failed"] = 0`, the "Clean run" branch at `:741-742`, and an empty `failing_tests` persisted. That is strictly worse than the over-broad baseline the check exists to prevent, and neither `MAX_RECONFIRM_NODES` (which only skips *large* sets) nor the parallel integrity guard (which runs before `reconfirm_serial` and never sees the serial report) covers it. The coverage form subsumes the all-errored case and additionally catches the zero-test and partially-executed reports a configure-time `pytest.exit` produces.

  Keeping "treat all as confirmed" for the alert text while refusing the state write is deliberate: it avoids a false green *and* a poisoned baseline in the same move.

  **`serial_trusted = False` is scoped to a parsed report with incomplete coverage, and to nothing else.** The existing catch-all at `:261-263` returns `ordered, [], **True**`, and so does the `MAX_RECONFIRM_NODES` bail. Extending untrust to those two would re-create the deadlock round 2 rejected: a machine whose 200+ node re-confirm reliably exhausts the 900 s budget would take the fatal path every night and never write a baseline, so the detector would never reach a state from which it could report anything. A parsed report is different in kind — it is *positive evidence* about which nodes ran, so a node absent from it demonstrably produced no result — where a timeout or a skipped pass is merely an absence of information. That is why the coverage check can fail closed without wedging. The residual is named rather than hidden: a serial pass that times out still seeds an over-broad baseline, and the correction is the next night's re-derivation, same as Race 3.

  **A shallow shrink warns and changes how state is written — the deep shrink now trips, and both are scoped to one collection.** Both comparisons read a `prev_total` that may have come from a *different* collection, so compute either only when `prev.get("collection") == COLLECTION_PATHS`, alongside the `prev = {}` guard already specified. On the widening night `prev` records `tests/unit/` (13,454) against a run of 14,899, so neither fires; any later *narrowing* of `COLLECTION_PATHS` would otherwise fire both on the very night whose purpose is to seed a fresh baseline — killing the seed outright, or switching `main()` onto the union-preserving state write and dragging the old collection's `dispatched_nodes` into it. The split is by depth: `total < 0.9 * prev_total` is the **coverage floor above** and is fatal, because at that magnitude a truncated run is the far likelier explanation than a deletion. Any shallower shrink — `prev_total > total >= 0.9 * prev_total` — goes in the returned `warnings` list, because a 1-5% drop is routine test churn and blocking on it would page someone weekly. The warning is not inert: `carry_dispatched_nodes` (:427-440) drops an already-filed node on **any** shrink, not only a deep one, so the union-preserving write is exactly what the shallow band needs. It keeps a previously-dispatched node only while it is still in *this* run's confirmed set (`still_failing = prior_dispatched(prev) & set(confirmed_failing)`), so a truncated night that is trusted persists a `dispatched_nodes` set stripped of every already-filed node that did not run, and the next full night re-dispatches them. `prior_dispatched`'s fallback to `failing_tests` cannot rescue that: it fires only when the key is *absent* (:396-407), and here the key is present-but-shrunken. So when the shrink warning is present, `main()` writes `current["dispatched_nodes"] = sorted(prior_dispatched(prev) | set(just_dispatched))` instead of calling `carry_dispatched_nodes`. Retiring a stale node ID one night late is cheap; re-filing a node that already has an open issue is precisely what the dedup set exists to prevent (#2429/#2430/#2462).

  On a trip: **alert loudly naming the reason, do not save state, do not dispatch, return non-zero** — so the next night still diffs against the last trustworthy baseline. The exit-code list alone would not have caught the recon failure, which exited **0** with zero tests executed; under Task E's wrapper it would not have caught the preflight refusal either, which exits **1** having never invoked pytest; and without the error ceiling none of it catches the contention mode that is most likely to actually occur.

- **Task B2 — one fatal path for every pre-alert exit.** `main()` exits non-zero from **four** places before any Telegram call is reachable, not two. Two are exception arms: `except subprocess.TimeoutExpired` (:639-641) and `except (FileNotFoundError, json.JSONDecodeError)` (:642-644). The other two are earlier and easier to miss: `main()` calls `load_env_or_die()` at `:619`, which raises `SystemExit(1)` at `:588` (the `.env` read raised `OSError` — the macOS Desktop-folder TCC denial of #2327) and again at `:603` (`applied < MIN_ENV_KEYS`). All four write a `FATAL:` line to `logs/nightly_tests.log` and nothing else: no Telegram, no state, no signal. Fixing only the timeout arm leaves the exact arm Task E makes more likely still silent; fixing only the two exception arms leaves the env-load refusal silent, and Task C is what makes that decisive rather than academic.

  **The env-load refusal is the one Task C turns into a fleet-wide hazard.** Un-gating the installer turns the detector on across machines that have never run it, and a per-machine TCC grant on `~/Desktop/Valor/.env` is exactly the thing that differs between machines. A newly-covered machine without the grant exits 1 at 03:00 every night forever while `/update` reports `installed` — problem leg 4 rebuilt by the fix for problem leg 4, and this plan's own bug class shipped inside its own remedy.

  `args = parser.parse_args()` is at `:612`, seven lines *before* the `load_env_or_die()` call at `:619`, so `args.dry_run` is already in scope and `main()` needs no restructuring. Change the signature to `load_env_or_die() -> tuple[int, str | None]` — returning a reason instead of raising — and call it as:

  ```python
  applied, env_err = load_env_or_die()
  if env_err:
      return _fatal(env_err, args.dry_run)
  ```

  Keep both existing `FATAL:` log strings verbatim as the reason text: they already name the TCC cause and cite #2327, so the alert an operator receives is the diagnosis. Two gotchas. (1) This path runs **before** `_acquire_run_lock()` at `:624`, so it must never be mistaken for a lock collision (which correctly returns 0 silently) and must never write state. (2) Under a full TCC denial `valor-telegram` may itself fail to read the vault; that is acceptable rather than a design flaw — `send_telegram()` is documented never-fatal (`docs/features/nightly-regression-tests.md:76-78`), and the `MIN_ENV_KEYS` partial-read case at `:603` still has enough environment to send.

  Collapse all four into one helper:

  ```python
  def _fatal(reason: str, dry_run: bool) -> int:
      log(f"FATAL: {reason}")
      send_telegram(f"Nightly tests could not run: {reason}", dry_run=dry_run)
      return 1
  ```

  called as `return _fatal(f"pytest timed out after {PYTEST_TIMEOUT_SECONDS}s", args.dry_run)`, `return _fatal(f"could not parse test results: {exc}", args.dry_run)`, and `return _fatal(env_err, args.dry_run)` for the env-load refusal. No arm may reach `save_last_run()`. `send_telegram()` is documented never-fatal (`docs/features/nightly-regression-tests.md:76-78`), so no extra `try/except` wraps it. The integrity-guard trip in Task B routes through the same helper, giving one alert shape for every "the run did not happen" outcome.

  **The `(FileNotFoundError, json.JSONDecodeError)` arm is the missing-executable path, not the parse path — say so, and do not double-count it.** Task B changes `run_tests()` to return `(None, None, rc)` instead of raising on a parse failure, so `json.JSONDecodeError` no longer has a route into that arm at all; what still reaches it is `FileNotFoundError` from the `Popen` of a missing `./scripts/pytest-clean.sh` under Task E. Keep the arm, keep it routed through `_fatal`, and describe it as the missing-executable path. The real parse-failure route is the guard's `report is None` trip, which is covered by the stale-report test in the Failure Path section — a `Test_Fatal` case labelled "corrupt JSON" would let a validator tick three boxes while the route that actually exists is proven only somewhere else, which is the "count paths, not arms" failure this very task warns about.

  **The property to prove is "no non-zero exit from `main()` is silent", not "both arms alert".** Counting arms is what let two of the four hide. `Test_Fatal` therefore covers three cases — the timeout arm; the env-load refusal, patching `dotenv_values` to raise `OSError`; and `run_tests()` returning `(None, None, 1)` so `validate_run_integrity` trips with the "no JSON report" reason and routes to `_fatal` — each asserting `send_telegram` was called and `save_last_run` was not. The Verification table carries `! grep -q 'raise SystemExit(1)' scripts/nightly_regression_tests.py`, which exits **1 (FAIL) on current `main`** and 0 once both `load_env_or_die` sites route through `_fatal`.

- **Task C — role-correct install gate, an observable skip, and a worktree refusal.** Replace `has_bridge_role()` in `scripts/install_nightly_tests.sh:30-74` with `has_worker_role()`, copied from `scripts/install_reflection_worker.sh:37-80` (the diff is one line: drop `if proj.get("telegram"):`). Keep the fail-open contract on unreadable config, missing venv, and `scutil` error — `tests/unit/test_install_scripts_bootstrap.py` depends on it (its harness sets no `PROJECTS_CONFIG_PATH` and a fresh `$HOME`, so all four parametrized nightly cases reach bootstrap only via fail-open).

  **Make the skip observable at the Python boundary *before* removing the gate that currently makes it observable.** `run.py:2045` is the `else:` arm of the very `if has_bridge:` at :2038, so deleting the gate deletes the only place `/update` can see a skip. The skip then happens inside the shell script, which `exit 0`s on the role gate (`install_nightly_tests.sh:73`), and `service.install_nightly_tests` returns `True` on rc 0 — its docstring says so outright, "installed or cleanly skipped" (`service.py:555-575`). Un-gating alone would therefore make `/update` log **"Nightly tests service installed/verified" on a machine where nothing was installed**: a confident false positive replacing a quiet-but-true INFO line, leaving problem leg 4 open. Sequence it:

  1. Change `service.install_nightly_tests(project_dir) -> Literal["installed", "skipped", "failed"]`, classifying from captured stdout. **Match the success marker, not the skip strings**: `install_nightly_tests.sh:107` already prints the stable line `Nightly regression test service installed successfully.`, so `"installed" if "Nightly regression test service installed successfully." in result.stdout else "skipped"` on rc 0 fails *closed* — the worktree refusal, the role-gate skip, and any early exit added later all read as "skipped" rather than falsely as "installed". Matching skip text instead would silently mis-classify every future early exit as a success.
  2. Do **not** signal skip with a distinct non-zero exit code. `service.py`'s `run_cmd` is `check=False` (:108-113), so a non-zero return falls into the existing `rc != 0` branch and would append a spurious "install failed" warning.
  3. Replace the `if has_bridge:` block at :2038 with a three-way branch on the returned literal, pushing the skip into `result.warnings.append("Nightly tests: no regression coverage on this machine (install skipped)")` so it surfaces in `/update`'s warning channel rather than at INFO. Leave the `has_bridge` assignment at :1858 alone — :1859 still uses it.
  4. Pin the marker string on both sides (shell source and Python matcher) in the new `tests/integration/test_install_nightly_tests.py`, so a cosmetic edit to the installer's success line cannot silently turn every machine's status into "skipped".

  **Something must observe the *absence* of a run, or leg 4 survives its own fix.** Every other signal this plan adds is emitted from inside a run that happened: `_fatal()` alerts, the integrity trip alerts, the re-baseline warning logs. Nothing at all reports that the detector stopped running. `tools/doctor.py` contains zero references to `nightly`, `launchctl` or the state file (verified by grep, zero hits). A launchd bootstrap that silently fails, a plist booted out later, a machine asleep at 03:00, and the `_acquire_run_lock()` collision path (`main()` `:624-626`, `return 0` with no alert) all produce the same observable as a green suite: silence. The Desired Outcome's "within 24 hours" is otherwise verifiable by nothing this plan builds.

  Close it with one staleness check on artifacts the plan already has — no new state, no new service. **The clock is `now - max(plist_mtime, run_at) >= 2 days`, not file-absence.** Keyed on absence it would fire on the very `/update` that installs the detector, on every machine, and keep firing until a 03:00 run landed — the installer is idempotent and takes the `"installed"` leg every run. The plist's mtime is the install time, so it is the anchor that says "the detector has not had a night yet". Gate the whole check on the `"installed"` leg of the new three-way branch so it cannot fire where no detector belongs. In `scripts/update/run.py`:

  ```python
  plist = Path.home() / "Library/LaunchAgents/com.valor.nightly-tests.plist"
  anchor = datetime.fromtimestamp(plist.stat().st_mtime, UTC) if plist.exists() else None
  run_at = None
  try:
      run_at = datetime.fromisoformat(json.loads(state_path.read_text())["run_at"])
  except (OSError, ValueError, KeyError, json.JSONDecodeError):
      run_at = None
  newest = max([d for d in (anchor, run_at) if d is not None], default=None)
  if newest is None or (datetime.now(UTC) - newest) >= timedelta(days=2):
      result.warnings.append("Nightly tests: service installed but last run is 2+ days old (or never ran)")
  ```

  An absent, unreadable or malformed `run_at` therefore **falls back to the plist mtime** rather than warning outright. `newest is None` — no plist *and* no readable `run_at` — is the only surviving "unknown, warn" case. It goes in `/update` rather than a reflection because spike-4 already ruled reflections out as a carrier here, and `/update` runs on exactly the machine population Task C widens to.

  **The installer must refuse to run from a linked worktree.** It writes `$HOME/Library/LaunchAgents/com.valor.nightly-tests.plist`, a machine-global path, substituting `__PROJECT_DIR__` with `$PROJECT_DIR` derived from the script's own location (:7-8) and hardcoding it into `ProgramArguments`, `WorkingDirectory`, `PATH` and both log paths. A lane that runs the installer from `.worktrees/{slug}/` therefore points the fleet's 03:00 job at a directory that merge deletes, and the job fails silently every night thereafter. Add this above the role gate:

  ```bash
  if [ -f "$PROJECT_DIR/.git" ] && grep -q '^gitdir:.*/\.git/worktrees/' "$PROJECT_DIR/.git" 2>/dev/null; then
      echo "Refusing to install nightly-tests from a worktree ($PROJECT_DIR)"
      exit 0
  fi
  ```

  In a linked worktree `.git` is a *file* containing `gitdir: …/.git/worktrees/<name>`; in the main checkout it is a directory, so `-f` alone discriminates (verified on this checkout and on `.worktrees/durability-m1-fence-canary` during revision). Pin it with a source-text assertion in the new `tests/integration/test_install_nightly_tests.py`.

  **Fleet-wide dedup, sized to the dispatcher that actually exists.** `dispatched_nodes` is per-machine local state in one checkout's `data/nightly_tests_last_run.json`. Turning the detector on across the fleet means every newly-covered machine dispatches the same confirmed-failing node on night two and files its own issue — #2429/#2430/#2462 reopened along a machine axis rather than a time axis. The fix is a machine-independent key, but it has to fit the dispatcher: `maybe_dispatch_triage_session` (:503-563) spawns **one** session for the whole `dispatch_nodes` list with a prompt saying to file "a … GitHub issue" — singular, for the batch. A batch title cannot be `<nodeid>`, and any batch-derived title differs between machines whose dispatch sets differ by even one node, which they always will. So make the convention a **value the detector emits**, not a derivation the agent is asked to perform. A prompt that says "construct the title `Nightly regression: <nodeid>`" is prompt-level dedup, and the insufficiency of prompt-level dedup is the whole lesson of #2559 — recorded in this very file at `compute_dispatch_set`'s docstring (`:415-418`). Build the strings in Python and embed them literally:

  ```python
  titles = [f"Nightly regression: {n}" for n in dispatch_nodes]
  ```

  and list them verbatim in the prompt: "Each failing node below has exactly one issue title. Search open issues for that exact title; comment there if it exists, otherwise open an issue with exactly that title." Two machines then produce byte-identical titles for the same node no matter how their dispatch sets differ, and the property is **assertable**: `TestMaybeDispatchTriage` checks each expected title appears verbatim in `argv[argv.index("--message") + 1]`. That is a test of the mechanism, where a test that the prompt contains an instruction is only a test of the prompt.

  Cap the fan-out so an error-storm night that slips past the guard cannot flood the tracker: `MAX_DISPATCH_NODES = 10`; when `len(dispatch_nodes)` exceeds it, log and truncate to the first 10.

  **Truncate in `main()`, never inside `maybe_dispatch_triage_session` — the site is what makes the remainder recoverable.** Truncating inside the dispatcher leaves `main()`'s local `dispatch_nodes` untouched, so `just_dispatched = dispatch_nodes` records the full untruncated set, `carry_dispatched_nodes` persists it, and `compute_dispatch_set` subtracts it forever: nodes 11..N would never be filed by any later night, a permanent silent suppression and this plan's own bug class. Do it at the `dispatch_nodes = compute_dispatch_set(prev, confirmed_failing)` site instead — `if len(dispatch_nodes) > MAX_DISPATCH_NODES: log(...); dispatch_nodes = dispatch_nodes[:MAX_DISPATCH_NODES]`, placed before the `maybe_dispatch_triage_session` call so only the slice is recorded. Fix the `suppressed` log in the same edit: it computes `len(confirmed_failing) - len(dispatch_nodes) - len(just_dispatched)` and would otherwise count truncated nodes as "already filed". Assert it directly — with 25 confirmed unfiled nodes, `saved["dispatched_nodes"]` has exactly 10 entries and a second run dispatches the next 10.

  **Be honest about what this is.** `maybe_dispatch_triage_session` (:503-563) hands a string to `tools.valor_session create` and never sees a title, so no code verifies any issue was actually filed under it. Emitting the exact titles removes the *derivation* risk, not the *compliance* risk. Cross-machine dedup rests on a convention with no shared state; `MAX_DISPATCH_NODES = 10` bounds the blast radius when the convention is not honored; a shared Redis-backed dispatch set remains the real fix and is out of appetite. The docs record it that way rather than as handled.

- **Task D — collection-aware baseline.** Add a `collection` field to the state dict recording the paths that produced it. When the recorded collection differs from the current one, treat the run as a baseline seed: take the existing `is_first_run` path (:630-632, :691-696), seeding `dispatched_nodes` and dispatching nothing. This is what stops the widening from re-filing every pre-existing non-unit failure in one night and reopening #2429/#2430/#2462.

  Make the re-baseline **visible**, since a silent seed reads exactly like a quiet night: `log(f"WARNING: collection changed {prev.get('collection')!r} -> {current_collection!r} — re-baselining, dispatching nothing (see #2429/#2430/#2462). Any regression landing tonight is absorbed into the seed and will not be filed until it stops failing and re-fails.")` The second sentence is not decoration — the seed genuinely swallows a same-night unit-tier regression for one commit window, and an operator reading the log should know the widening night is a seed rather than a verdict.

  **Make the seed auditable, because it is permanent.** Every node red when the widening lands is absorbed into `dispatched_nodes` and will never be filed by any later night unless it stops failing and re-fails. That population is precisely the one #2823 is about, so it must be recorded rather than swallowed: on the re-baseline path persist `seed_collection`, `seed_size = len(confirmed_failing)` and the seeded node list under a distinct key, and extend the baseline Telegram text with the seeded count plus the absorbed-regression sentence the re-baseline log already carries. The seeded set is then a readable artifact someone can open one umbrella issue against, on the #2852 model. Doing that repair is out of scope (see No-Gos and Rabbit Holes); leaving it invisible is not an option, since silence is the failure this plan exists to close.

  Add `head_commit` (`git rev-parse HEAD`) in the same edit so a red night is attributable to a SHA — coordinating with #2334's plan, which proposes the same field. It is captured once at run start, for attribution only; see Race 3.

- **Task E — route through `scripts/pytest-clean.sh`.** Replace the bare `sys.executable -m pytest` argv at :182-199 (and the serial re-confirm at :242-258) with the wrapper. Unattended at 03:00 this buys orphan reaping via `trap cleanup EXIT INT TERM HUP PIPE`, the `check-interpreter-pin.sh` refusal, and the `PYTEST_STALL_LIMIT_S` watchdog. Spike-7 measured the watchdog doing exactly its job — SIGTERM at 637s on a wedged run that a bare invocation would have let burn for the full timeout. The recon runs also produced 45 `node down` events; without reaping, those workers reparent to PID 1 and accumulate nightly. `--json-report` passes through the wrapper unchanged since it is a pure argv pass-through.

  **Task E is only safe on top of Task B's freshness checks.** The wrapper exits **1** from both preflight refusals — missing venv pytest (`pytest-clean.sh:142`) and the interpreter-pin refusal (:149-151) — *without invoking pytest at all*. With the fixed `/tmp` report path and no unlink, that is a direct route to parsing last night's healthy report under a returncode the guard must not trip on. Sequencing matters: the unlink and the 3-tuple `(raw_report, summary, returncode)` return land with Task B, before the argv is switched.

- **What this plan deliberately does not do**: add a CI workflow, or add a test leg to the merge predicate or any stage gate. See Rabbit Holes.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `run_tests()` no longer raises on a parse failure — it returns `(None, None, returncode)`. Assert the log line is still emitted and that `None` reaches `validate_run_integrity` rather than `main()`'s exception arm, and that `main()` never dereferences the `None` summary.
- [ ] **Stale-report regression test.** Write a healthy report to `PYTEST_JSON_TMP`, then run with a pytest stub that exits 1 without writing anything. Assert the pre-subprocess `unlink` removed it, that `run_tests()` returns `(None, 1)`, that the guard trips with a reason naming "no JSON report", and that no state is written. This is the Task E false-green in test form.
- [ ] **Every `_fatal()` path alerts — all three, not both arms.** Assert that `TimeoutExpired`, an env-load refusal (patch `dotenv_values` to raise `OSError`), and `run_tests()` returning `(None, None, 1)` (guard trips with the "no JSON report" reason) each call `send_telegram` with a reason string, each return non-zero, and none reaches `save_last_run()`. The env-load case is the one that runs *before* `_acquire_run_lock()` (`:624`), so also assert it is not mistaken for a lock collision, which correctly returns 0. There is deliberately **no** "corrupt JSON" case: Task B removes `json.JSONDecodeError`'s route into that arm, leaving it the missing-executable path (`FileNotFoundError` from the `Popen` of `./scripts/pytest-clean.sh`), and labelling a case for a route that no longer exists is the box-ticking this task warns against.
- [ ] The `MIN_ENV_KEYS` refusal at `:603` alerts on the same path as the `OSError` refusal at `:588` — a partial vault read still has enough environment for `send_telegram` to work, so this arm has no excuse for silence.
- [ ] `send_telegram()` is documented as never fatal — assert that an integrity-failure alert *and* all three `_fatal()` paths still return non-zero and still skip the state write when the send itself fails.
- [ ] `reconfirm_serial`'s catch-all at :261-263 (`TimeoutExpired/FileNotFoundError/JSONDecodeError/OSError`) marks every input node confirmed. Add a test asserting that behavior is preserved under the widened collection, and that the number of nodes actually dispatched is bounded by `MAX_DISPATCH_NODES` — **not** by `compute_dispatch_set`, which subtracts `prior_dispatched` and is therefore no bound at all the night after a re-baseline.
- [ ] `reconfirm_serial` with more than `MAX_RECONFIRM_NODES` inputs → returns `(all_inputs, [], True)` without spawning a subprocess. Assert it never returns an empty confirmed set: a very red night must not be converted into a green one.
- [ ] `reconfirm_serial` whose serial report contains **no result for at least one input node** → returns `serial_trusted=False`, and `main()` routes it through `_fatal`, writes no state, and dispatches nothing. Assert the pre-existing state file is byte-identical afterwards — a poisoned seed baseline is the failure this closes.
- [ ] **The configure-abort shape (the concern-1 regression test).** A serial report with an **empty `tests` list** and a non-empty `ordered` → `serial_trusted=False`. Assert explicitly that `confirmed` is **not** silently emptied and the nodes are not reclassified into `artifacts`: the `:266-267` arithmetic turning a red night into "all xdist artifacts" and a persisted empty `failing_tests` is the false green being closed, and it is strictly worse than the over-broad baseline.
- [ ] `reconfirm_serial` whose serial report carries a result for **every** input node, mixing `error` and `failed` outcomes → `serial_trusted=True`. Trust is about coverage, not about outcome; a pass that answered every question carries real information however red the answers.
- [ ] `reconfirm_serial`'s serial subprocess carries `TEST_DB_CLAIM_WAIT_S=300` in its env, asserted the same way as `run_tests()`'s argv.
- [ ] **Timeout path leaves no orphans.** Both spawns pass `start_new_session=True`; on `TimeoutExpired` the arm calls `os.killpg(os.getpgid(proc.pid), SIGTERM)` then `SIGKILL` before routing to `_fatal`. Assert with a patched `os.killpg` that it is called with the child's own group id, and assert no `subprocess.run(..., timeout=)` remains in either function — under Task E the direct child is bash, whose `trap cleanup EXIT INT TERM HUP PIPE` never runs under SIGKILL.
- [ ] No `except Exception: pass` blocks exist in the touched scope of `nightly_regression_tests.py` — confirm during build and state so.

### Empty/Invalid Input Handling
- [ ] `validate_run_integrity` with `summary.total == 0` → trips. **This is the spike-3 regression test and the single most important test in the plan.**
- [ ] `validate_run_integrity` with `report is None` → trips with the "the run did not happen" reason. Second most important: it is the only guard against the fixed-`/tmp`-path staleness.
- [ ] `validate_run_integrity` with `returncode == 1` and a healthy report → **does not trip.** Pytest's 1 is a legitimate red night; a guard that trapped it would convert every real regression into an infrastructure alert and re-hide the failures this plan exists to surface.
- [ ] `validate_run_integrity` with `prev = {}` (no prior run) → the ratio check must not fire; only the absolute floor applies.
- [ ] `validate_run_integrity` with a `prev` whose `collection` differs from `COLLECTION_PATHS` → neither the coverage floor nor the shrink warning may fire, however far the total dropped. A narrowed collection's seed night must not be killed outright, nor inherit the old collection's `dispatched_nodes` through the union-preserving write.
- [ ] **Partial-starvation case (the round-6 blocker regression test).** A fresh report, returncode **0**, `error = 0`, `failed = 0`, `total = 9000` against a same-collection `prev_total = 14899` → **trips** on the coverage floor, writes no state, dispatches nothing. This is the shape a configure-time `pytest.exit` actually produces, and every other check in the guard passes it.
- [ ] `validate_run_integrity` with `total` at 95% of `prev_total` and an otherwise healthy report → **does not trip**, but returns the shrink warning, the warning text reaches the alert body, **and `main()` persists `prior_dispatched(prev) | just_dispatched` instead of calling `carry_dispatched_nodes`.** Assert directly that an already-filed node absent from the truncated run survives into the next state file — that is the #2429/#2430/#2462 re-filing this guard prevents.
- [ ] Coverage-floor boundary in both directions: `total` just above `0.9 * prev_total` warns and does not trip; just below trips. Plus the pre-baseline form: with no same-collection `prev` and `MIN_EXPECTED_COLLECTED` seeded, `total < 0.9 * MIN_EXPECTED_COLLECTED` trips; with `MIN_EXPECTED_COLLECTED` unseeded (`0`) no floor applies at all, so an unmeasured constant never fabricates a verdict.
- [ ] `validate_run_integrity` with a report missing `summary` entirely → trips rather than raising `KeyError`.
- [ ] **Fixture-error-storm case.** A fresh report, returncode 1, `total = 14899`, `error = 9000`, `failed = 0` → **trips** with a reason naming the error count, writes no state, dispatches nothing. This tests the fixture-error ceiling, **not** test-DB starvation — starvation produces no `error` outcomes at all (`tests/conftest.py:274-288`), which is why it needs the coverage floor instead. Paired with its inverse: `total = 14899`, `error = 0`, `failed = 9000` → **does not trip**, because a genuinely red suite is the signal this plan exists to deliver.
- [ ] Error-ceiling boundary: `error` just under `max(50, 0.02 * total)` does not trip; just over does. Assert the `max(50, …)` floor holds for a small collection, so a 40-error night in a 500-test run is not classified as infrastructure.
- [ ] `has_worker_role()` against a `projects.json` with zero projects owned by this machine → skips install and removes any stale plist.
- [ ] Installer worktree refusal: with `$PROJECT_DIR/.git` a file containing `gitdir: /x/.git/worktrees/y`, the script exits 0 having written no plist; with `.git` a directory it proceeds to the role gate.

### Error State Rendering
- [ ] The integrity-failure Telegram message must name the reason (zero collection, bad exit code, total dropped below floor) — assert the reason string reaches the message body, so an operator reading the alert can tell "the suite could not run" from "the suite is red".
- [ ] Assert the integrity-failure path writes no state file, by asserting the pre-existing state file is byte-identical after the run.

## Test Impact

- [ ] `tests/unit/test_nightly_regression_tests.py::TestMainDispatchPersistence` — **UPDATE, all seven methods.** The argparse block is a non-issue (`_run_main` patches `sys.argv` to `["nightly_regression_tests.py", "--dry-run"]` at :513 and no flag is added), but **Task D breaks every one of them**. Not one fixture carries a `collection` key: the `prev` dicts at :540, :549, :556, :568, :580 and :597 are all `{"failing_tests": [...], "dispatched_nodes": [...]}`, and `_run_main` (:505-530) writes `prev_state` to the real `LAST_RUN_FILE` and calls the real `nrt.main()`, patching only `run_tests`, `reconfirm_serial`, `summarize_failures`, `maybe_dispatch_triage_session`, `send_telegram` and `run_ttft_gate`. So Task D's mismatch check routes all seven onto the seed branch (`nightly_regression_tests.py:690-696`) where `dispatch_nodes = []`; `test_standing_failure_is_not_re_dispatched` asserts `mock_dispatch.assert_called_once_with([fresh])` at :544 and would receive `[]`, and the alert branch flips to "baseline established". **Fix the fixtures, do not relax the assertions**: every `prev` dict gains `"collection": COLLECTION_PATHS`.
- [ ] `tests/unit/test_nightly_regression_tests.py::TestMainDispatchPersistence` — ADD: one new method that deliberately **omits** `collection` and asserts the mismatch behavior directly — dispatcher called with `[]`, `saved["dispatched_nodes"] == sorted(confirmed)`, and the re-baseline WARNING logged — so Task D's path is proven rather than exercised by accident across seven tests that are asserting something else. In that test the mocked `maybe_dispatch_triage_session` **must return `None`**: `main()` sets `just_dispatched = list(confirmed_failing)` on the seed path and then overwrites it with `dispatch_nodes` if a truthy session id comes back, whereas the real function returns `None` early on an empty list (:519-520). A truthy mock would wipe the seed and make the test assert the opposite of the intent.
- [ ] `tests/unit/test_nightly_regression_tests.py::TestMainDispatchPersistence._run_main` (:521) — UPDATE: `patch.object(nrt, "reconfirm_serial", return_value=(list(confirmed), []))` becomes the 3-tuple `(list(confirmed), [], True)`. Every one of the seven methods flows through it.
- [ ] `tests/unit/test_nightly_regression_tests.py::TestMainDispatchPersistence._RUN_RESULT` (:495-503) — UPDATE: the stub becomes a **3-tuple** `(raw_report, summary, returncode)`, not a bare dict, and the summary must carry a non-zero `total` and a zero `error` or every test trips the new integrity guard.
- [ ] `tests/unit/test_nightly_regression_tests.py::TestRunLock::test_main_returns_0_on_collision_without_running_tests` (:260-281) — UPDATE: patches `sys.argv` at :268; re-check under the new argparse block.
- [ ] `tests/unit/test_nightly_regression_tests.py::TestReconfirmSerial` (:162-198) — UPDATE: **all three methods unpack two values** — `confirmed, artifacts = nrt.reconfirm_serial(...)` at `:165`, `:186` and `:196` — and each hard-fails against the widened `(confirmed, artifacts, serial_trusted)` return. Update all three to unpack three. Separately, the fixtures use `tests/unit/...` node IDs; add non-unit node IDs to prove path-agnosticism.
- [ ] `tests/unit/test_nightly_regression_tests.py::TestDeltaLogic` (:74-124) — REPLACE: `_compute_alert` (:77-90) reimplements main()'s alert logic inline using a **count delta**, which no longer matches the set-based `compute_new_failures` (:381-393). It is already drifted; rewrite it against the real function or delete it.
- [ ] `tests/unit/test_nightly_regression_tests.py` — ADD: coverage for `run_tests()`'s argv. **No existing test asserts on it** — `run_tests` is always mocked (:273, :520) — so line 187 is currently untested and the widening would otherwise land with zero coverage. Copy the argv-assertion pattern from `TestMaybeDispatchTriage::test_dispatch_once` (:438).
- [ ] `tests/unit/test_nightly_regression_tests.py` — ADD: a `TestValidateRunIntegrity` class covering every trip condition in Task B, including the spike-3 case (exit 0, total 0), the `report is None` case, and the exit-1-with-healthy-report **non**-trip.
- [ ] `tests/unit/test_nightly_regression_tests.py` — ADD: a stale-report test asserting `PYTEST_JSON_TMP` is unlinked before the subprocess, so a pytest that never runs cannot yield last night's totals.
- [ ] `tests/unit/test_nightly_regression_tests.py` — ADD: `Test_Fatal` covering **all three** pre-alert paths — the timeout arm, the env-load refusal (patch `dotenv_values` to raise `OSError`), and the integrity trip reached via `run_tests()` returning `(None, None, 1)` — each alerts, each returns non-zero, none writes state. Three, not two: counting arms is what let two of `main()`'s four silent exits hide. No "corrupt JSON" case — Task B removes that route, leaving the arm as the missing-executable path.
- [ ] `tests/unit/test_nightly_regression_tests.py` — ADD: `load_env_or_die` returns `(applied, None)` on a healthy vault and `(applied, reason)` on both refusal paths, and raises `SystemExit` from neither. The existing `FATAL:` strings become the reason text verbatim.
- [ ] `scripts/update/run.py` — ADD coverage for the nightly staleness warning, keyed on `max(plist_mtime, run_at)`: on the `"installed"` leg, a missing state file with a plist mtime of **today** → **no warning**; a missing state file with a plist mtime 3 days old → warning; a state file with no `run_at` or a malformed `run_at` falls back to the plist mtime rather than warning outright; a `run_at` 3 days old with an old plist → warning; a `run_at` from today → no warning; and the `"skipped"` leg never warns about staleness regardless. The two missing-state-file cases are the ones that stop the warning firing on the `/update` that installs the service — without them, "a missing state file appends the warning" pins the false positive as the specification. Place it beside the existing `/update` step tests.
- [ ] `tests/unit/test_nightly_regression_tests.py` — ADD: the fixture-error-storm trip test and its red-suite inverse, plus the `MAX_RECONFIRM_NODES` and `MAX_DISPATCH_NODES` bounds.
- [ ] `tests/unit/test_nightly_regression_tests.py` — ADD: the coverage-floor tests — the partial-starvation shape (exit 0, `error = 0`, reduced `total`) trips; both boundary directions; the `MIN_EXPECTED_COLLECTED` pre-baseline form, seeded and unseeded.
- [ ] `tests/unit/test_nightly_regression_tests.py` — ADD: a shrink-warning state-persistence test in the shallow band (95% of `prev_total`), asserting `prior_dispatched` survives a truncated night intact.
- [ ] `tests/unit/test_nightly_regression_tests.py` — UPDATE: any test that asserts `run_tests()` raises must move to the `(raw_report, summary, returncode)` 3-tuple contract.
- [ ] `tests/unit/test_nightly_regression_tests.py::TestMaybeDispatchTriage` (:430-470) — UPDATE: assert each expected title appears **verbatim** in the dispatched prompt — `msg = argv[argv.index("--message") + 1]`, then `assert f"Nightly regression: {node}" in msg` for every node. Asserting only that the prompt carries an *instruction* to build the title tests the prompt, not the dedup. Add a case with more than `MAX_DISPATCH_NODES` nodes asserting the prompt lists exactly 10 titles and the returned dispatched slice matches.
- [ ] `tests/unit/test_nightly_regression_tests.py::TestMaybeDispatchTriage::test_prompt_does_not_call_the_set_newly_confirmed` (:448-458) — UPDATE: it asserts the literal `"Search open issues first"` is in the prompt. The rewritten per-node instruction changes that wording; re-anchor the assertion on the exact-title search text rather than deleting the guarantee.
- [ ] `scripts/update/service.py::install_nightly_tests` — VERIFY: no test asserts on its `bool` return today (`grep -rn install_nightly_tests tests/` hits only `test_install_scripts_bootstrap.py`'s shell-script table), and `run.py:2039` is its only caller. The `Literal["installed","skipped","failed"]` change is therefore a two-site edit; the new coverage lands in `tests/integration/test_install_nightly_tests.py`.
- [ ] `tests/unit/test_install_scripts_bootstrap.py` (:74-77, :59) — VERIFY, likely no change: the harness reaches bootstrap via the **fail-open** branch (no `PROJECTS_CONFIG_PATH`, fresh `$HOME`), so loosening the predicate keeps all four parametrized cases passing. Confirm empirically rather than by inspection; a change that made the gate fail closed would break `assert len(harness.bootstrap_calls()) == len(harness.labels)` at :261.
- [ ] `tests/integration/test_install_nightly_tests.py` — CREATE: source-text assertions pinning the new gate, copying `tests/integration/test_install_reflection_worker.py:47-68` (`assert "has_worker_role" in installer_src`, `assert 'proj.get("telegram")' not in installer_src`, self-skip and stale-plist removal, fail-open on unreadable config). Plus a **worktree-refusal** assertion pinning the `gitdir:.*/\.git/worktrees/` guard, and a behavioral case running the installer against a fixture directory whose `.git` is such a file, asserting exit 0 and no plist written. Plus a **marker-string pin** asserting the literal `Nightly regression test service installed successfully.` appears in both `scripts/install_nightly_tests.sh` and `scripts/update/service.py`, so the two sides of the installed-versus-skipped classification cannot drift apart. No test pins the nightly gate today.
- [ ] `tests/README.md:417` — UPDATE: the row recording `test_nightly_regression_tests.py | 28` is already stale and will move further.

## Rabbit Holes

- **Adding a GitHub Actions workflow that runs the suite.** Tempting because leg 1 reads like "we forgot CI". It is out of appetite and against the grain of a system whose Redis, launchd services, test-DB claims and `projects.json` are all machine-local — a hosted runner reproduces none of that. The issue rules out "run everything on every push" directly, and `CLAUDE.md` puts a `tests/unit/` run at ~20 minutes.
- **Adding a test leg to the merge predicate or a TEST-stage gate.** This is the single most attractive wrong turn here, and it is already a settled question: #2376 removed exactly that after #2064, #2066, #1965, #2118 and #2145 documented it wedging. `docs/sdlc/do-merge.md:276-279` carries the standing instruction not to. Question (b)'s answer is a **finding to record**, not a gate to build.
- **Making the TEST stage marker record its collection scope.** Genuinely appealing — it is the honest fix for "nothing can tell a full run from `-k one_test`" — but it touches `tools/sdlc_stage_marker.py`, the OUTCOME contract, and every consumer, and it only pays off if something later gates on it, which the bullet above rules out for now.
- **Fixing the red non-unit tiers found by the widened run.** The detector's job is to file issues; those issues are separate lanes. #2852 is the existing umbrella for the unit tier and the model to follow. Be explicit about the consequence: every node already red when the widening lands is absorbed into the seed baseline and is **never** filed by the detector unless it stops failing and re-fails. The plan's "a red test produces a filed issue within 24 hours" outcome is true for regressions landing after the seed, not for the pre-existing population. Task D persists `seed_size` and the seeded node list so that population is a readable artifact rather than a silence; opening the umbrella issue against it is a separate lane.
- **Building a general "machine roles" abstraction.** Recon found five hand-rolled role gates and no canonical helper, plus a latent apostrophe-normalization bug (`config/machine.py` has `normalize_machine_name`; none of the five shell gates use it, and this machine is literally `Tom's MacBook Air` with U+2019). Real, and a different plan. Task C copies the existing `has_worker_role()` rather than abstracting.
- **A concurrency redesign against the 15 test-DB slots** — a shared scheduler, a machine-wide slot broker, or dynamic worker sizing. Out of scope. Picking a fixed worker count and claim wait for the unattended run **is** in scope and is Task A (`NIGHTLY_XDIST_WORKERS` defaulting to `6`, `TEST_DB_CLAIM_WAIT_S=300`); Task B's guard is what reports the collision when those values are not enough. The recon detail is the evidence for both halves: the recon run spawned 50 xdist workers against 15 slots, which is why a fixed count is worth choosing and why choosing one does not solve contention.

## Risks

### Risk 1: The widened collection re-files every pre-existing non-unit failure on night one
**Impact:** A flood of duplicate issues, exactly the #2429/#2430/#2462 failure the `dispatched_nodes` set was built to stop. Worse in reputation terms than the gap being fixed, because it teaches the team to ignore the detector. **The magnitude is unmeasured**: spike-2 confirmed `tests/tools` + `tests/performance` (219 tests) fully green, but `tests/integration` + `tests/e2e` (1,202 tests) resisted three measurement attempts, so the number of pre-existing red non-unit nodes is unknown.
**Mitigation:** Task D is a hard requirement, not an optimization, and it is designed to make the magnitude irrelevant. The state file records its collection identity, and a changed collection forces the existing `is_first_run` baseline path (:630-632, :691-696), which seeds `dispatched_nodes` and dispatches nothing — so night one files zero issues whether the non-unit tiers are clean or 200-red. Verify by running once against a state file recording the old collection and asserting zero dispatches. Build should also measure the integration tier once on a quiet machine and record the number, so night two's dispatch volume is a known quantity rather than a surprise.

### Risk 2: The widened run exceeds its timeout and goes silent
**Impact:** `main()` returns 1 from **both** of its pre-alert exception arms — `TimeoutExpired` at :639-641 and `(FileNotFoundError, json.JSONDecodeError)` at :642-644 — and every Telegram call (:720/:734/:740) is downstream of them. Either arm produces no notification at all, only a line in `logs/nightly_tests.log`, a file that does not currently exist on this machine. A too-tight timeout converts the fix into a differently-silent detector, and Task E makes the parse arm materially more likely by introducing a wrapper that can exit before writing a report.
**Mitigation:** Task B2 collapses **all four** pre-alert exits into `_fatal()` — the two exception arms plus `load_env_or_die()`'s `SystemExit(1)` sites at `:588` and `:603` — so every pre-alert exit alerts and none reaches `save_last_run()`. This covers the whole class, not the timeout half. The env-load pair is what Task C makes fleet-wide: a machine lacking the `~/Desktop/Valor/.env` TCC grant would otherwise exit 1 at 03:00 forever while `/update` reports `installed`. On the constant itself, Task A carries a deterministic fallback: attempt the measurement once, and on a run that does not complete, land `5400` with a comment that says **"bound, not measurement"** and shows the derivation. The plan's own evidence (spike-2's three defeated attempts, spike-7's SIGTERM at 637s) says the fallback is the likely path on this machine, so the task is executable as written rather than blocking on a measurement that may never be takeable here. Task B2 is a hard prerequisite of the constant change: a wrong ceiling must page someone.

### Risk 8: The detector stops running and nothing notices
**Impact:** Every signal this plan adds is emitted from inside a run that happened — `_fatal()` alerts, the integrity trip alerts, the re-baseline warning logs. A launchd bootstrap that fails quietly, a plist booted out later, a machine asleep at 03:00, and the `_acquire_run_lock()` collision path (`main()` `:624-626`, `return 0` with no alert) all produce the same observable as a green suite: silence. `tools/doctor.py` has zero references to `nightly`, `launchctl` or the state file. Without a check on the absence, the Desired Outcome's "within 24 hours" is verifiable by nothing this plan builds, and problem leg 4 survives its own fix.
**Mitigation:** Task C adds one staleness check to `/update`, on the `"installed"` leg of the new three-way branch, over artifacts the plan already has: the LaunchAgent plist's mtime and `run_at` in `data/nightly_tests_last_run.json`. No new state and no new service. The clock is `now - max(plist_mtime, run_at) >= 2 days`; an absent, unreadable or malformed `run_at` falls back to the plist mtime, and only "no plist *and* no readable `run_at`" warns as unknown. Keying it on file-absence instead would have fired on the very `/update` that installs the detector and kept firing until a 03:00 run landed. It is gated on `"installed"` so it never fires on a machine where no detector belongs. This bounds the blind window to the interval between `/update` runs rather than closing it; a machine that never runs `/update` is still unobserved, and that residual is named rather than hidden.

### Risk 6: A lane installs the LaunchAgent from its worktree
**Impact:** `scripts/install_nightly_tests.sh` writes a machine-global plist that hardcodes `$PROJECT_DIR` into `ProgramArguments`, `WorkingDirectory`, `PATH` and both log paths. Run from `.worktrees/{slug}/`, it aims the fleet's only regression detector at a directory that merge deletes — after which the 03:00 job fails silently every night, which is the precise failure mode this plan exists to eliminate. The exposure is not hypothetical: Success Criterion 6 asks a validator to confirm the service is installed, and Task 7 assigns that job to an agent working in the lane worktree.
**Mitigation:** Closed in code, not prose — Task C adds a worktree refusal above the role gate, discriminating on `.git` being a file whose contents start `gitdir: …/.git/worktrees/`. Pinned by source-text assertion in `tests/integration/test_install_nightly_tests.py`. Success Criterion 6 and its Verification row are reworded to state they run from the main checkout after a real `/update`, so a lane validator marks the row N/A rather than satisfying it unsafely.

### Risk 7: Fleet-wide install multiplies duplicate issue filing
**Impact:** `dispatched_nodes` lives in one checkout's `data/nightly_tests_last_run.json`, so it dedups across nights on one machine and not at all across machines. Task C turns the detector on fleet-wide by design; on night two every newly-covered machine dispatches the same confirmed-failing node and files its own issue. That is #2429/#2430/#2462 reopened along a new axis, and the only remaining guard would be the soft prompt instruction to "search open issues first" — exactly the prompt-level dedup whose insufficiency motivated `dispatched_nodes` in #2559.
**Mitigation — partial, and recorded as partial.** Task C has the detector **compute** the per-node titles in Python (`titles = [f"Nightly regression: {n}" for n in dispatch_nodes]`) and embed them verbatim in the single triage session's prompt, so two machines emit byte-identical titles for the same node however their dispatch sets differ. That much is assertable and is asserted in `TestMaybeDispatchTriage`. What it is **not** is enforcement: `maybe_dispatch_triage_session` (:503-563) hands the string to `tools.valor_session create` and never sees a title, so no code verifies an issue was filed under it. Cross-machine dedup therefore rests on a convention with no shared state — the same class of guarantee, though not the same weakness, as the prompt-level dedup #2559 replaced. Emitting the literal titles removes the derivation risk; the compliance risk stands, bounded by `MAX_DISPATCH_NODES = 10`, with only the truncated slice recorded as dispatched so the remainder is retried rather than lost. **A shared Redis-backed dispatch set is the real fix and is out of appetite for this plan.** `docs/features/nightly-regression-tests.md` records it in exactly those terms rather than as handled. This also revises Risk 5's claim that fleet reach does not affect the plan: it does not affect the plan's *value*, but the duplicate-filing exposure scales with it — and per Resolved Question 2 the fleet size is itself unverifiable from here, so the residual is unquantified.

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

*Shape 2 — partial starvation, which is the more likely one and was not measured. It appears as MISSING TESTS, not as errors.* Some workers claim slots and some do not. A starved worker does not raise per test: since #2628 the claim runs in `tests/conftest.py::pytest_configure` (`:274-288`), before collection, and on `RuntimeError` calls `pytest.exit(str(exc), returncode=3)` — the in-code comment at `:286` says why, verbatim: "One line of output instead of a setup error on every collected test." The worker contributes **zero `error` outcomes and zero test items** and dies as "node down: Not properly terminated", the event class spike-3 measured 45 of. The night therefore has a fresh report, `error == 0`, exit **0**, and a **reduced** `total` — every absolute check and the fixture-error ceiling pass it as a clean night. Task B's **blocking coverage floor on `total`** is the check that catches this shape: `total < 0.9 * prev_total` within the same collection, or `total < 0.9 * MIN_EXPECTED_COLLECTED` before a same-collection baseline exists. The fixture-error ceiling does **not** catch it and must not be described as though it does. `MAX_RECONFIRM_NODES` / `MAX_DISPATCH_NODES` bound the damage if a variant slips past. Task A's explicit worker count (`NIGHTLY_XDIST_WORKERS`, default `6`) and `TEST_DB_CLAIM_WAIT_S=300` reduce how often either shape occurs at all: six workers against fifteen slots, with five minutes of patience, is a very different proposition from `-n auto` demanding ten to fourteen with thirty seconds. **The wait is capped at 300 because of the stall watchdog**, `scripts/pytest-clean.sh:178`'s `PYTEST_STALL_LIMIT_S=600` — not because of `pyproject.toml:196`'s `--timeout=420`, which arms no timer over a configure-time poll. See Task A.

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
- [SEPARATE-SLUG #2334] Autonomous-fix-before-alert behavior for the nightly detector. This plan changes what the detector *collects* and *trusts*, not what it does after a confirmed failure. Tracked, but **not** ready to build as written: per Risk 4, landing this plan invalidates #2334's Technical Approach at `run_tests()`, `reconfirm_serial()`, both pytest spawns and `load_env_or_die()`, so it needs re-planning rather than a rebase.
- [EXTERNAL] Verifying which other machines in the fleet have `com.valor.nightly-tests` installed. Requires shell access to hosts this agent cannot reach; `projects.json` on this machine is known to be stale as a fleet inventory.

## Update System

`/update` changes are **required** — they are Task C, not an afterthought:

- `scripts/update/run.py:2038` currently wraps the nightly-tests install in `if has_bridge:`, an independent second gate. It must stop gating so the installer's own predicate is the single decision point, matching the pattern `run.py:2016-2023` already argues for with the reflection worker. Keep the `has_bridge` assignment at :1858 — :1859 still uses it.
- The skip must stay observable **through** that un-gating, not because of it. `run.py:2045` is the `else:` arm of the very `if` being removed, so the observability has to move to the Python boundary first: `service.install_nightly_tests` returns `Literal["installed", "skipped", "failed"]`, classified by whether the installer's stable success line `Nightly regression test service installed successfully.` (`install_nightly_tests.sh:107`) appears in captured stdout. Then `run.py` branches three ways and appends `"Nightly tests: no regression coverage on this machine (install skipped)"` to `result.warnings`. Without this ordering, un-gating turns a quiet-but-true INFO line into `"Nightly tests service installed/verified"` on a machine where nothing was installed — a confident false positive, and problem leg 4 still open.
- `scripts/install_nightly_tests.sh` gate changes from `has_bridge_role()` to `has_worker_role()`. On the next `/update`, machines that own a project and previously had no nightly service will install it. This is the intended propagation and needs no migration step: the installer is idempotent (boots out any existing label before bootstrapping) and `launchctl_bootstrap_fail_soft` is called 3-arg, without PID verification, which is correct for a scheduled service (`scripts/lib/launchctl.sh:38-44` names nightly-tests explicitly).
- The `"installed"` leg gains a **staleness warning**: `/update` warns when `now - max(plist_mtime, run_at) >= 2 days`, reading the LaunchAgent plist's mtime and `run_at` from `data/nightly_tests_last_run.json`. An absent or malformed `run_at` falls back to the plist mtime; only "no plist and no readable `run_at`" warns as unknown. Not keyed on file-absence — that form fires on the very `/update` that installs the service. This is the plan's only observer of a run that did not happen — every other signal it adds is emitted from inside a run that did. `/update` is the right carrier because spike-4 ruled out reflections and because `/update` runs on exactly the machine population Task C widens to.
- One new operator-facing env var, `NIGHTLY_XDIST_WORKERS` (default `6`), for machines whose core count or slot pressure differs from this one. Nothing to propagate: an unset var takes the default, and the plist stays flag-free.
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
- [ ] Record in `docs/features/nightly-regression-tests.md` that `/update` warns when the service is installed and `now - max(plist_mtime, run_at) >= 2 days`, that an absent or malformed `run_at` falls back to the plist mtime so the `/update` that *installs* the service never warns, and that this is the only check that observes a run's *absence* — a booted-out plist, a machine asleep at 03:00, and a lock collision are all otherwise indistinguishable from a clean night.
- [ ] Record `NIGHTLY_XDIST_WORKERS` in the same doc: default `6`, derived from 15 test-DB slots with headroom for sibling lanes, floor `-n 4` because below that the run cannot finish inside `PYTEST_TIMEOUT_SECONDS`.
- [ ] Update `docs/features/README.md` index entry if the summary line changes.
- [ ] Update `docs/sdlc/do-merge.md:273-274`, which names `scripts/nightly_regression_tests.py` as "the backstop for anything that slips through". That claim was false in two ways — the backstop ran `tests/unit/` only, and was installed nowhere — and becomes true once this ships. State the new scope explicitly.
- [ ] Add a short note to `docs/sdlc/do-test.md` recording the answer to investigation question (b): no code gates on a full-suite result, deliberately (#2376), and the nightly detector is the compensating control. This is where a future reader will look.
- [ ] Update `tests/README.md:417` (`test_nightly_regression_tests.py` test count, already stale).

### External Documentation Site
Not applicable — this repo has no Sphinx/MkDocs site.

- [ ] Record in `docs/features/nightly-regression-tests.md` that `dispatched_nodes` is **per-machine** state and that cross-machine dedup rests on the `Nightly regression: <nodeid>` title convention — emitted verbatim by the detector, but **unenforced**, since nothing verifies an issue was filed under it. Name `MAX_DISPATCH_NODES = 10` as the blast-radius bound and a shared Redis-backed dispatch set as the real fix, deferred. A future change to the title format silently reopens #2429/#2430/#2462.
- [ ] Record in the same doc **how test-DB starvation actually presents**, because the intuitive model is wrong and this plan spent a critique round on it: since #2628 the claim runs in `tests/conftest.py::pytest_configure` (`:274-288`) and aborts the worker's session via `pytest.exit(returncode=3)`, so a starved worker produces **no `error` outcomes and no test items** and dies as "node down". Starvation is therefore visible only as *missing tests*, which is why the coverage floor blocks and why the error ceiling is documented as a fixture-error guard that cannot fire for this cause.
- [ ] Record in the same doc that the installer refuses to run from a linked worktree, and why (the plist is machine-global and hardcodes an absolute project dir).
- [ ] Record in the same doc that a collection change produces a **seed night**, not a verdict: any regression landing that night is absorbed into the baseline and will not be filed until it stops failing and re-fails. State plainly that every node already red when the widening lands is absorbed permanently and is out of scope for this work, that `seed_size` and the seeded node list are persisted so the population is readable, and that repairing it is a separate lane on the #2852 model.

### Inline Documentation
- [ ] Docstring for `validate_run_integrity` stating each trip condition and, critically, **why the returncode alone is insufficient in both directions** — cite the measured exit-0-with-zero-tests case, and state that exit 1 is deliberately excluded because pytest's 1 is a legitimate red night. It must also state which check catches which cause: the **coverage floor** catches test-DB starvation, because since #2628 a starved worker aborts its session in `tests/conftest.py::pytest_configure` (`:274-288`) and reports no outcome at all; the **error ceiling** catches fixture-level error storms and cannot fire for starvation. Attributing the ceiling to starvation is the exact defect round 6 found.
- [ ] Comment at `MIN_EXPECTED_COLLECTED` recording that it is the coverage floor for nights with no same-collection baseline, where its value came from (the widening probe, or night one's own `total`), that `0` means unseeded and disables the floor, and that a fabricated value would be a gate that lies in the other direction.
- [ ] Comment at the `Popen(..., start_new_session=True)` spawn stating that the direct child is a shell wrapper, that SIGKILL runs no bash trap, and that `killpg` on an owned session is what keeps a timed-out run from orphaning the controller and its workers onto a shared machine.
- [ ] Comment at the `unlink` call explaining that `PYTEST_JSON_TMP` is a fixed path and a stale report reads as a healthy run — the reason the unlink is not optional cleanup.
- [ ] Comment on the raised `PYTEST_TIMEOUT_SECONDS` stating explicitly whether it is a measurement (with date and total) or a **bound** (with the derivation). Never a comment claiming a measurement that was not taken.
- [ ] Comment on the `collection` state field explaining that a mismatch forces a re-baseline, and naming #2429/#2430/#2462 as what that prevents.
- [ ] Comment at `load_env_or_die`'s new return contract stating that it returns a reason rather than raising because a `SystemExit` here pages nobody, and that this path runs before the run lock so it must not be read as a collision.
- [ ] Comment at `NIGHTLY_XDIST_WORKERS` recording the derivation (15 slots, nine left for sibling lanes), the `-n 4` floor and its arithmetic, and that it is env-overridable because the detector now ships to machines of unknown size.

## Success Criteria

- [ ] `scripts/nightly_regression_tests.py` collects the full default collection by default — the same set a bare `scripts/pytest-clean.sh` collects (14,899 tests), including `tests/integration/`.
- [ ] A run that executes zero tests, writes no report, exits with pytest code 2/3/4/5, or dies to a signal produces an infrastructure-failure alert naming the reason, writes no state, dispatches nothing, and returns non-zero. A total that merely shrank against the prior run warns without blocking.
- [ ] The spike-3 scenario is a passing regression test: a JSON report with `summary.total == 0` and returncode 0 must not be treated as a clean night.
- [ ] The stale-report scenario is a passing regression test: a healthy report already at `PYTEST_JSON_TMP` plus a pytest that exits without running must trip the guard, not inherit last night's totals.
- [ ] A returncode of 1 with a healthy report does **not** trip the guard — it is a red night and must alert as a regression, not as infrastructure failure.
- [ ] **A fresh report with returncode 0, `error = 0`, `failed = 0` and a `total` below `0.9 × prev_total` (same collection) trips the guard as infrastructure failure.** This is the round-6 criterion: test-DB starvation aborts each worker's session in `pytest_configure` (`tests/conftest.py:274-288`) and produces **no `error` outcomes and no test items**, so it is visible only as missing tests. Before a same-collection baseline exists, the floor is `0.9 × MIN_EXPECTED_COLLECTED`, seeded from the widening probe or from night one's own `total`; unseeded, no floor applies.
- [ ] A report with a full `total`, returncode 1, and an `error` count above `max(50, 0.02 * total)` trips the guard as a **fixture-error** storm; the same report with those outcomes as `failed` rather than `error` does not. No comment, docstring or test name attributes this ceiling to test-DB starvation — it cannot fire for that cause.
- [ ] The number of nodes reaching `maybe_dispatch_triage_session` in any single run is bounded by `MAX_DISPATCH_NODES`, and `reconfirm_serial` never returns an empty confirmed set from its bail-out path.
- [ ] `reconfirm_serial`'s own subprocess carries `TEST_DB_CLAIM_WAIT_S=300`, and `serial_trusted` is `all(n in seen for n in ordered)` over the report's node IDs — **not** a count of `error` outcomes. A serial report with an empty `tests` list, which a configure-time `pytest.exit` produces, returns `serial_trusted=False` and routes `main()` through `_fatal` with no state written. Under an `error`-keyed check that same report reads as trusted and converts every red node into an xdist artifact, persisting an empty `failing_tests` — a false green worse than the poisoned baseline the check exists to prevent.
- [ ] The `:261-263` catch-all and the `MAX_RECONFIRM_NODES` bail both return `serial_trusted=True`, so a re-confirm that merely times out still writes a baseline rather than wedging the detector into nightly fatals with no state ever persisted.
- [ ] Both pytest spawns use `subprocess.Popen(..., start_new_session=True)` with `communicate(timeout=)`, and the `TimeoutExpired` arm kills the owned process group (SIGTERM, then SIGKILL) before `_fatal`. A timed-out night must not leave a live pytest controller and its xdist workers holding test-DB slots into the working day — under Task E the direct child is bash, and SIGKILL runs no trap.
- [ ] `TEST_DB_CLAIM_WAIT_S` is `300` at both call sites, and a test asserts it against the bound that governs — `int(env["TEST_DB_CLAIM_WAIT_S"]) * 2 <= 600`, `scripts/pytest-clean.sh:178`'s `PYTEST_STALL_LIMIT_S`. Not against `pyproject.toml:196`'s `--timeout=420`: since #2628 the claim polls in `pytest_configure`, before any test item, so no per-item timer is armed over it.
- [ ] The dispatch prompt contains each node's issue title **verbatim** (`Nightly regression: <nodeid>`), computed in Python, asserted by exact substring match against the `--message` argv value.
- [ ] A night carrying the shrink warning persists `prior_dispatched(prev) | just_dispatched`, so an already-filed node that did not run is not re-filed on the next full night.
- [ ] **Every non-zero exit from `main()`, including the env-load refusal**, sends a Telegram alert naming the reason. Count *paths*, not arms: there are four before the normal alert block — the two exception arms and the two `SystemExit(1)` sites inside `load_env_or_die()` (`:588`, `:603`). Proven by `Test_Fatal` covering three cases (timeout, parse, `dotenv_values` raising `OSError`), by `! grep -q 'raise SystemExit(1)' scripts/nightly_regression_tests.py`, and by the two delegation greps in the Verification table.
- [ ] The nightly worker count is `NIGHTLY_XDIST_WORKERS`, env-overridable with a default of `6` and a comment carrying its derivation and the `-n 4` floor. The argv test asserts the constant, not the literal `"6"`.
- [ ] `/update` warns, on the `"installed"` leg only, when `now - max(plist_mtime, run_at) >= 2 days` — the plist mtime being `~/Library/LaunchAgents/com.valor.nightly-tests.plist` and `run_at` coming from `data/nightly_tests_last_run.json`, with an absent or malformed `run_at` falling back to the plist mtime and only "neither readable" warning as unknown. **A missing state file with a plist mtime of today produces no warning**; keying the check on file-absence would fire on the very `/update` that installs the service, on every machine. Absence of a run is otherwise indistinguishable from a clean night, which is problem leg 4 surviving its own fix.
- [ ] **One widened run has completed with `summary.total > 0`** — satisfied either by a completed local probe at `-n 4..6` run outside `run_tests()`'s ceiling (`TEST_DB_CLAIM_WAIT_S=300 ./scripts/pytest-clean.sh tests/ -n 4 --json-report --json-report-file=/tmp/widen_probe.json`), **or** by night one's baseline alert reporting `total > 0` post-merge. The criterion proves the collection is *executable*, not how fast it is. Below `-n 4` the run cannot finish inside `PYTEST_TIMEOUT_SECONDS = 5400`, so a lower count is not a fallback. A probe that fails for any reason other than slot exhaustion must stop and report.
- [ ] `PYTEST_TIMEOUT_SECONDS` carries a comment that is either a measurement (with date and observed total) or an explicit bound (with the derivation). Never a comment asserting a measurement that was not taken.
- [ ] `scripts/install_nightly_tests.sh` gates on `has_worker_role()`; `grep 'proj.get("telegram")' scripts/install_nightly_tests.sh` returns nothing.
- [ ] `scripts/install_nightly_tests.sh` refuses to install from a linked worktree, pinned by a source-text assertion and a behavioral test.
- [ ] `scripts/update/run.py` invokes the nightly-tests installer unconditionally, leaving the shell script as the single decision point. `service.install_nightly_tests` returns `"installed" | "skipped" | "failed"`, and a skip appends to `result.warnings` — so a machine with no regression coverage is visible in `/update`'s warning channel and never reports "installed/verified".
- [ ] `com.valor.nightly-tests` is present in `launchctl list` **on the main checkout's machine, after a real `/update` run from the main checkout.** A validator working in a lane worktree marks this row N/A and records why — installing it from a worktree is the failure mode Risk 6 describes, not a way to satisfy this criterion.
- [ ] A run whose recorded state carries a different collection re-baselines, dispatches zero **per-node** triage sessions, and logs the re-baseline as a warning naming the absorbed-regression caveat.
- [ ] The re-baseline path persists `seed_collection`, `seed_size` and the seeded node list, seeds `MIN_EXPECTED_COLLECTED` from its own `total`, and dispatches **one** umbrella triage session whose prompt carries the exact title `Nightly regression baseline: {seed_size} nodes absorbed on {head_commit}`. The absorbed population is the one #2823 is about; persisting it without escalating it is the same silence relocated.
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
  - Role: `scripts/install_nightly_tests.sh` role gate, the `scripts/update/service.py` return contract, and the `scripts/update/run.py` call site (three-way branch plus the staleness warning)
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
- **Depends On**: build-fatal-path
- **Validates**: tests/unit/test_nightly_regression_tests.py
- **Informed By**: spike-1 (delta is only 1,445 tests / ~10%), spike-5 (exactly six unit-tier string sites: :4, :174, :180, :187, :730, :738, plus prose at :636)
- **Assigned To**: detector-builder
- **Agent Type**: builder
- **Parallel**: false
- Add the module constant `COLLECTION_PATHS = ["tests/"]` and use it in the pytest argv at :187 and as the `collection` state value in task 3. No CLI flag — the argparse block at :610-612 is unchanged.
- Update all six unit-tier strings so log lines and Telegram remediation instructions name what actually ran.
- **Size the run to the slot pool, not the CPU — through an env-overridable constant.** Add `NIGHTLY_XDIST_WORKERS = os.environ.get("NIGHTLY_XDIST_WORKERS", "6")` with a comment recording the derivation (15 slots, nine left for sibling lanes) and the `-n 4` floor below, following the convention `MIN_ENV_KEYS` (`:96`) and `PYTEST_RECONFIRM_TIMEOUT_SECONDS` (`:76`) already set in this file. Build the argv as `"-n", NIGHTLY_XDIST_WORKERS` in place of `-n auto`, and pass `env={**os.environ, "TEST_DB_CLAIM_WAIT_S": "300"}` to the subprocess. `-n auto` on this box demands ~10-14 of 15 slots (`tests/db_claim.py:66`, `:180-181`); six leaves nine for sibling lanes, and a 03:00 job can afford five minutes of patience where the documented 30 s default is tuned for interactive use (`:69-75`). Without both, the shipped end state on this machine is a nightly "the run did not happen" alert. **Do not pin the literal `"6"` in a test** — Task C ships this to machines whose core count and slot pressure are unknown (Resolved Question 2), so the argv test asserts `argv[argv.index("-n") + 1] == nrt.NIGHTLY_XDIST_WORKERS` and an operator override never turns the suite red. `TEST_DB_CLAIM_WAIT_S = "300"` stays pinned as a literal for the opposite reason: it has a real invariant behind it and the test asserts that invariant.
- **`-n 4` is the floor; there is no ladder below it.** Scaling `pyproject.toml:188`'s ~1260 s at ~10 workers by ~1.1 collection growth and inversely by worker count: ~2300 s at `-n 6`, ~3500 s at `-n 4`, ~6900 s at `-n 2`, ~13,900 s at `-n 1`. Only `-n 4..6` fits inside `PYTEST_TIMEOUT_SECONDS = 5400`, past which Task B routes `TimeoutExpired` through `_fatal()` — so "retry lower until it finishes" is not a real fallback. State the floor and this derivation in the constant's comment.
- **300, not more — and the bound is the stall watchdog, not `--timeout=420`.** Since #2628 `claim_test_db()` polls inside `tests/conftest.py::pytest_configure` (`:274-288`), before collection and therefore before any test item; pytest-timeout arms per-item in the runtest protocol, so no timer is armed while the claim waits, and the fixture's call at `:883` never re-enters the poll. `tests/db_claim.py:74`'s in-code "`--timeout=420` ceiling" note is stale for this reason — do not reason from it. The bound that can actually kill the run is `scripts/pytest-clean.sh:178`'s `PYTEST_STALL_LIMIT_S` (600 s low-CPU window on the controller) under Task E; 300 sits at half of it. Add that derivation as the comment at the constant, assert the literal `"300"` in the new `run_tests()` argv test, and assert the bound as `int(env["TEST_DB_CLAIM_WAIT_S"]) * 2 <= 600` so the test pins the quantity that governs. Do **not** carry an "unless you also pass `--timeout=<n>`" escape clause: a CLI `--timeout` does not cover a configure-time poll, so it would change nothing.
- **Probe the widened run outside the constant, and record the outcome.** A completion with `summary.total > 0` is a Success Criterion in its own right, and it must not be run through `run_tests()` — a ceiling this same task is still choosing would decide the result. Invoke `TEST_DB_CLAIM_WAIT_S=300 ./scripts/pytest-clean.sh tests/ -n 4 --json-report --json-report-file=/tmp/widen_probe.json` directly and read `summary.total` from that report. **That number is also the seed for `MIN_EXPECTED_COLLECTED`** (task 2's pre-baseline coverage floor) — land it as the constant's default in the same edit. Retry between `-n 4` and `-n 6` only. If the probe is defeated by slot exhaustion, the criterion is satisfied instead by night one's baseline alert reporting `total > 0` post-merge (Resolved Question 4). Stop and report only if the probe fails for a reason other than slot exhaustion — a collection error, an import failure, a wrapper refusal — since those are what "the collection is not executable" actually looks like.
- **Timeout constant, with a deterministic fallback.** Attempt one widened run. If it completes (exit in `{0,1}` and `summary.total > 0`), size `PYTEST_TIMEOUT_SECONDS` (:72) from the observed wall time and comment it as a measurement with date and total. If it does not complete — the likely outcome on this machine per spike-2 and spike-7 — set it to `5400` and comment it **"bound, not measurement"** with the derivation `~1260s unit baseline (pyproject.toml:188) x 1.1 growth x 3.5 contention headroom`. Do not block on the measurement, and do not write a comment claiming one that was not taken. Same rule for `PYTEST_RECONFIRM_TIMEOUT_SECONDS` (:76), or bound the re-confirm set instead.
- **Hard prerequisite, now expressed in the graph:** `Depends On: build-fatal-path` and `Parallel: false`. A wrong ceiling has to page someone, and today the timeout arm pages nobody. Same-agent serialization is not a substitute — a dispatcher reads `Depends On`, not prose.

### 2. Add the run-integrity guard
- **Task ID**: build-integrity-guard
- **Depends On**: build-widen-collection
- **Validates**: tests/unit/test_nightly_regression_tests.py (create `TestValidateRunIntegrity`)
- **Informed By**: spike-3 (reproduced twice: exit 0, zero tests executed, `5 warnings in 341.54s`, all 15 test-DB slots held)
- **Assigned To**: detector-builder
- **Agent Type**: builder
- **Parallel**: false
- **Make the report fresh by construction first.** `Path(PYTEST_JSON_TMP).unlink(missing_ok=True)` immediately before the subprocess in `run_tests()`; same for `PYTEST_SERIAL_JSON_TMP` in `reconfirm_serial`. The path is fixed (:69) and nothing unlinks it today, so a pytest that never runs yields last night's healthy report.
- **Own the process group in the same edit — this is the prerequisite that makes Task E safe.** `subprocess.run(..., timeout=)` SIGKILLs its direct child, which under Task E is bash; SIGKILL runs no trap, so `pytest-clean.sh:109`'s `trap cleanup EXIT INT TERM HUP PIPE` never fires and the controller at `:241` (deliberately not `exec`, `:238-240`) survives with its xdist workers, still holding test-DB slots. `cleanup()` (`:101-103`) reaps only the worker regex at `:42` and never the controller, so a graceful SIGTERM leaves it too. In both `run_tests()` (:182-199) and `reconfirm_serial` (:242-258), switch to `subprocess.Popen(argv, …, start_new_session=True)` + `proc.communicate(timeout=…)`, and in `except subprocess.TimeoutExpired:` do `os.killpg(os.getpgid(proc.pid), signal.SIGTERM)`, `time.sleep(10)`, `os.killpg(…, signal.SIGKILL)`, `proc.wait()` before re-raising to `_fatal`. `start_new_session=True` is what bounds the kill to this script's own group, so it can never touch a sibling lane's run and needs no pattern kill (forbidden by `.claude/hooks/validators/validate_no_broad_process_kill.py`). Assert both: that `start_new_session=True` is passed, and that the timeout arm kills the group before `_fatal`.
- **Change `run_tests()` to return the 3-tuple `(raw_report, summary_or_None, returncode)`** instead of raising at :206-209, so a missing or corrupt report reaches the guard rather than `main()`'s silent `return 1`. The raw report is what the guard needs; `summary_or_None` is the existing dict `main()` consumes as `current` at :646-650 and :669-676, built only when the report parsed.
- Add `validate_run_integrity(report, returncode, prev) -> tuple[str | None, list[str]]`, tripping on: `report is None` → `"pytest wrote no JSON report (exit {rc}) — the run did not happen"`; exit code in `{2,3,4,5}`; **any exit code >128 or negative (signal death — spike-7 measured exit 143 from the wrapper's stall watchdog)**; `summary.total == 0`; a report with no `summary` key; **or `error > max(50, 0.02 * total)`**. **There is no collectors leg**: `pytest_jsonreport/serialize.py:17-29` only ever writes `passed`/`failed`/`skipped` for a collector, so the condition cannot fire, and widening it to `!= "passed"` would route one broken import — a real red-on-main regression — through `_fatal` every night with no baseline ever written. Record the reasoning in the docstring; `main()`'s `:735-740` collection-error alert stays as the reporting path.
- **The coverage floor is the round-6 fix and the most consequential line in this task.** Test-DB starvation does **not** produce per-test setup errors: since #2628 the claim runs in `tests/conftest.py::pytest_configure` (`:274-288`), before collection, and aborts the session with `pytest.exit(str(exc), returncode=3)` — the comment at `:286` states the intent verbatim. A starved worker reports **no outcome at all**; it dies as "node down". Partial starvation therefore yields `error == 0`, exit **0**, and a **reduced** `total`, which every absolute check and the error ceiling pass as a clean night. The floor is what sees it. Seed `MIN_EXPECTED_COLLECTED` (provisional/tunable via `NIGHTLY_MIN_EXPECTED_COLLECTED`, the `MIN_ENV_KEYS` convention at `:96`) from the `summary.total` of task 1's probe, or from night one's own `total` on Task D's re-baseline path when the probe was defeated by slot exhaustion. Leave it `0` until seeded, so an unmeasured floor never fabricates a verdict.
- **Keep the error ceiling, and stop calling it the starvation guard.** `error > max(50, 0.02 * total)` still traps a genuine fixture-level error storm — a broken conftest import, a service that is down — which `extract_failing_node_ids` (:159-171) would otherwise flood into `reconfirm_serial` and then into dispatch. Key it on `error` only, never `failed`, so a genuinely red tier is never misread as infrastructure. Enrich the reason with the most common error message in the report so the alert is actionable. **No comment, docstring or test name may claim it catches test-DB starvation** — that claim is what round 6 falsified, and restating it leaves the next reader trusting a gate that cannot fire for the cause named beside it.
- Add `MAX_RECONFIRM_NODES = 200`: when `reconfirm_serial` receives more, log and return `(ordered, [], True)` without spawning the serial pass — the same result the 900 s timeout already produces, reached immediately. It must **not** return an empty confirmed set: manufactured from a very red night that is a false green, and it would deadlock the detector into re-tripping nightly with no baseline ever written.
- **Harden `reconfirm_serial` itself — it is the step whose output becomes the baseline.** Every other change here lands on the parallel run; the serial pass spawns its own pytest at `:242-258`, inherits neither `-n 6` nor the claim-wait override, and runs *after* the single `validate_run_integrity` call site. A serial process that cannot claim a slot errors every node at setup, `extract_failing_node_ids` (`:159-171`) counts those as failing, `confirmed = ordered` at `:266`, and `save_last_run()` persists it — which Task D then makes the widened collection's seed, rendering every genuinely-red node in it permanently invisible to `compute_new_failures` (`:392-393`) and `compute_dispatch_set` (`:410-424`). That is #2823's failure mode rebuilt by its own fix. `MAX_RECONFIRM_NODES` does not cover it: it only skips the pass when the set is large, so any set ≤ 200 is exposed. Do both:
  1. `env={**os.environ, "TEST_DB_CLAIM_WAIT_S": "300"}` on the serial `subprocess.run` at `:242-258`, same ceiling reasoning as task 1.
  2. After the parse at `:260`, key trust on **result coverage**: `seen = {t.get("nodeid") for t in report.get("tests", [])}`, then `serial_trusted = all(n in seen for n in ordered)`. Widen the return to `tuple[list[str], list[str], bool]` — `(confirmed, artifacts, serial_trusted)` — rather than fabricating an artifact entry to signal it, and in `main()` route `serial_trusted is False` through `_fatal("serial re-confirmation returned no result for every node — the run did not happen")`, skipping both dispatch and `save_last_run()`.
- **Coverage, not an `error` count — the `error` form is a false green here.** A serial process that cannot claim a slot hits `tests/conftest.py:287`'s `pytest.exit(returncode=3)`; `pytest.exit` raises `Exit`, `wrap_session` still runs `pytest_sessionfinish`, and `pytest-json-report` writes a report with **zero test entries**. An `error`-keyed check computes `0 >= len(ordered)` → False → trusted, `serial_failing` is empty, and `:266-267` yields `confirmed = []` with `artifacts = ordered`: every red node reclassified as an xdist artifact, `current["failed"] = 0`, the "Clean run" branch at `:741-742`, an empty `failing_tests` persisted. Strictly worse than the over-broad baseline the check exists to prevent. The coverage form subsumes the all-errored case and also catches the zero-test and partially-executed reports a configure-time abort produces.
- **Scope `serial_trusted = False` to a parsed report with incomplete coverage, and to nothing else.** The `:261-263` catch-all and the `MAX_RECONFIRM_NODES` bail both return `True`. Untrusting them would deadlock a machine whose large re-confirm reliably exhausts its 900 s budget: fatal every night, no baseline ever written, nothing ever reported. A parsed report is positive evidence about which nodes ran; a timeout or a skipped pass is merely an absence of information.
- **The deep shrink trips; only the shallow band warns.** `total < 0.9 * prev_total` is the coverage floor above and is **fatal** — round 6 established that a truncated run, not a test-file deletion, is the far likelier explanation at that magnitude, and a deletion costs one loud night with a one-command remedy while a silent truncation has no remedy at all. `prev_total > total >= 0.9 * prev_total` stays a `warnings` entry, because a few percent of test churn is routine and blocking on it would page someone weekly. **Compute either only when `prev.get("collection") == COLLECTION_PATHS`**, alongside the `prev = {}` guard — an unconditioned comparison fires on any later *narrowing* of the collection, on the very night whose purpose is to seed a fresh baseline, either killing the seed outright or dragging the old collection's `dispatched_nodes` into it through the union-preserving write below. The warning is not inert: `carry_dispatched_nodes` drops an already-filed node on *any* shrink, so when the warning is present `main()` persists `sorted(prior_dispatched(prev) | set(just_dispatched))` instead of calling `carry_dispatched_nodes`, because that function's `prior_dispatched(prev) & set(confirmed_failing)` intersection silently drops every already-filed node a truncated run did not reach, and the next full night re-files them (#2429/#2430/#2462).
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
- Route **all four** pre-alert exits through it, not two. Two are exception arms: `except subprocess.TimeoutExpired` (:639-641) and `except (FileNotFoundError, json.JSONDecodeError)` (:642-644). Two are inside `load_env_or_die()`, called at `:619`: the `OSError` refusal at `:588` (the #2327 TCC denial) and the `applied < MIN_ENV_KEYS` refusal at `:603`. Task B's integrity trip uses the same helper.
- **Convert `load_env_or_die` from raising to returning.** Signature becomes `-> tuple[int, str | None]`; `main()` calls `applied, env_err = load_env_or_die()` then `if env_err: return _fatal(env_err, args.dry_run)`. `args = parser.parse_args()` is at `:612`, seven lines before the call, so `args.dry_run` is already in scope and `main()` needs no restructuring. Keep both existing `FATAL:` log strings verbatim as the reason text — they already name the TCC cause and cite #2327.
- **Two gotchas on the env path.** It runs *before* `_acquire_run_lock()` at `:624`, so it must not be confused with a lock collision (which correctly returns 0 silently) and must not write state. And under a full TCC denial `valor-telegram` may itself fail to read the vault; that is acceptable, since `send_telegram()` is documented never-fatal and the `MIN_ENV_KEYS` partial-read case still has enough environment to send.
- No arm may reach `save_last_run()`. No `try/except` around `send_telegram` — it is documented never-fatal (`docs/features/nightly-regression-tests.md:76-78`).
- Fixing only the timeout arm is not acceptable: the parse arm is the one Task E makes more likely, and the env arms are the ones Task C turns on across machines whose TCC grant is exactly what differs. A newly-covered machine without the grant would exit 1 nightly forever while `/update` reports `installed`.
- `Test_Fatal` covers three cases, and the property under test is "no non-zero exit from `main()` is silent" — not "both arms alert". Counting arms is what let two of the four hide.

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
- **Persist the seed so it is auditable.** On the re-baseline path record `seed_collection`, `seed_size = len(confirmed_failing)` and the seeded node list under a distinct key, and extend the baseline Telegram text with the seeded count and the absorbed-regression sentence. Everything red when the widening lands is absorbed permanently; that population is the one #2823 is about.
- **Seed `MIN_EXPECTED_COLLECTED` from this run's `total` in the same edit**, for the case where task 1's probe was defeated by slot exhaustion and never produced a number. Task 2's coverage floor has no same-collection `prev` to compare against on night one, so without this the first widened night has no floor at all.
- **Escalate the seed in the same branch — a readable artifact nobody is tasked with reading is the silence this plan exists to close, moved from the log to the state file.** Call `maybe_dispatch_triage_session` once on the seed path with a single synthetic entry (so `MAX_DISPATCH_NODES` is irrelevant) and a distinct prompt: "The nightly detector re-baselined onto collection `{COLLECTION_PATHS}` and absorbed `{seed_size}` already-failing nodes, listed below. Open ONE umbrella issue titled exactly `Nightly regression baseline: {seed_size} nodes absorbed on {head_commit}`, linking #2852, and do **not** file per-node issues." Guard it with the same `if triage_session_id is not None` bookkeeping `main()` uses at `:707-711`, so a failed dispatch leaves the seed unchanged and the next run retries. *Repairing* the absorbed tests stays a separate lane (#2852 model); *booking* them is this bullet.
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
- **On the `"installed"` leg, add the staleness warning — the only thing in this plan that observes the *absence* of a run.** The clock is `now - max(plist_mtime, run_at) >= 2 days`, **not** file-absence: the installer is idempotent and takes the `"installed"` leg on every `/update`, so an absence-keyed check warns on the very run that installs the detector and keeps warning until a 03:00 night lands. Resolve `Path.home() / "Library/LaunchAgents/com.valor.nightly-tests.plist"` and take `datetime.fromtimestamp(plist.stat().st_mtime, UTC)` when it exists; parse `run_at` from `data/nightly_tests_last_run.json` inside `except (OSError, ValueError, KeyError, json.JSONDecodeError)` so an absent or malformed value becomes `None` and **falls back to the plist mtime** rather than warning outright. Then `newest = max([d for d in (anchor, run_at) if d is not None], default=None)` and `result.warnings.append("Nightly tests: service installed but last run is 2+ days old (or never ran)")` when `newest is None or (datetime.now(UTC) - newest) >= timedelta(days=2)`. `newest is None` — no plist *and* no readable `run_at` — is the only surviving "unknown, warn" case. Gate the whole check on `"installed"` so it never fires where no detector belongs. Every other signal this plan adds is emitted from inside a run that happened; a plist booted out, a bootstrap that failed quietly, a machine asleep at 03:00, and the `_acquire_run_lock()` collision (`main()` `:624-626`, `return 0`) all look exactly like a green suite otherwise. `tools/doctor.py` has zero references to `nightly` or `launchctl`, so nothing else covers this.
- Step 5 touches `scripts/install_nightly_tests.sh` and `scripts/update/run.py`/`service.py` **only**. The detector-side dispatch work is step 5b, on a different agent, so this step's `Parallel: true` is actually safe alongside step 2b.

### 5b. Deterministic dispatch titles and the fan-out cap
- **Task ID**: build-dispatch-titles
- **Depends On**: build-integrity-guard
- **Validates**: tests/unit/test_nightly_regression_tests.py::TestMaybeDispatchTriage
- **Informed By**: Risk 7, #2559, round-4 concern on concurrent edits to `scripts/nightly_regression_tests.py`
- **Assigned To**: detector-builder
- **Agent Type**: builder
- **Parallel**: false
- This step exists because both bullets below edit `scripts/nightly_regression_tests.py`, the plan's most heavily edited file. Leaving them in step 5 (`install-builder`, `Depends On: none`, `Parallel: true`) scheduled two builders to write that file at once, alongside step 2b which is also `Depends On: none, Parallel: true`. A dispatcher reads the graph, not the prose.
- Rewrite the triage prompt in `maybe_dispatch_triage_session` (:503-563) to be **per-node inside one session**, with the titles computed in Python rather than derived by the agent: build `titles = [f"Nightly regression: {n}" for n in dispatch_nodes]` and list them verbatim, instructing the session to search open issues for each exact title, comment if found, otherwise open an issue with exactly that title. The dispatcher keeps its single-session shape. Emitting literal titles is what makes two machines byte-identical for the same node, and it is what `TestMaybeDispatchTriage` can assert — a prompt that only *instructs* the agent to build the title proves nothing (Risk 7, #2559).
- Add `MAX_DISPATCH_NODES = 10` and truncate **in `main()`**, at the `dispatch_nodes = compute_dispatch_set(prev, confirmed_failing)` site and before the dispatcher call — never inside `maybe_dispatch_triage_session`, where `main()`'s local list would keep the full set, `just_dispatched` would record it, and `compute_dispatch_set` would subtract nodes 11..N forever. Log the truncation, and correct the `suppressed` count in the same edit so truncated nodes are not reported as "already filed". Only the slice is recorded as dispatched, so the remainder is retried on later nights.

### 6. Test coverage
- **Task ID**: build-tests
- **Depends On**: build-integrity-guard, build-fatal-path, build-state-versioning, build-wrapper-routing, build-role-gate, build-dispatch-titles
- **Validates**: tests/unit/test_nightly_regression_tests.py, tests/integration/test_install_nightly_tests.py
- **Informed By**: Test Impact section
- **Assigned To**: detector-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `TestValidateRunIntegrity` covering every trip condition, with the spike-3 case (exit 0, total 0) as the headline test, plus the `report is None` case and the **exit-1-with-healthy-report non-trip**.
- Add the stale-report test: seed `PYTEST_JSON_TMP` with a healthy report, stub a pytest that exits without writing, assert the guard trips and no state is written.
- Add `Test_Fatal` covering **three** pre-alert paths — the timeout arm, an env-load refusal with `dotenv_values` patched to raise `OSError`, and the integrity trip reached via `run_tests()` returning `(None, None, 1)` — each alerts, each returns non-zero, none writes state, and each still does so when `send_telegram` itself fails. No "corrupt JSON" case: Task B removes `json.JSONDecodeError`'s route into the `(FileNotFoundError, json.JSONDecodeError)` arm, leaving it the missing-executable path. Also assert `load_env_or_die` returns `(applied, None)` on a healthy vault, returns a reason on both refusal paths, and raises `SystemExit` from neither; and that the env refusal is not mistaken for the lock collision, which correctly returns 0.
- Add the `/update` staleness-warning tests beside the existing update-step tests, all on the `"installed"` leg and all keyed on `max(plist_mtime, run_at)`: **a missing state file with a plist mtime of today → no warning**; a missing state file with a plist mtime 3 days old → warning; an absent or malformed `run_at` falls back to the plist mtime; a 3-day-old `run_at` warns; a same-day `run_at` does not; the `"skipped"` leg never warns about staleness. The first case is the one that keeps the warning off the `/update` that installs the service.
- Add the **coverage-floor** tests — the partial-starvation shape (fresh report, rc **0**, `error = 0`, `failed = 0`, `total = 9000` against same-collection `prev_total = 14899`) **trips**; both boundary directions around `0.9 * prev_total`; and the pre-baseline form using `MIN_EXPECTED_COLLECTED`, seeded (trips below `0.9 ×`) and unseeded (no floor applies).
- Add the **fixture-error-storm** test and its inverse: full `total`, rc 1, `error = 9000` trips; the same shape with `failed = 9000` and `error = 0` does not. Plus the `max(50, 0.02 * total)` boundary in both directions. Name it for fixture errors, not for starvation.
- Add the shrink-warning persistence test in the shallow band (95% of `prev_total`): an already-filed node absent from a truncated run must survive into the next state file. Add its companion: a `prev` whose `collection` differs from `COLLECTION_PATHS` must fire neither the floor nor the warning, however far the total dropped.
- Add bound tests for `MAX_RECONFIRM_NODES` (returns `(all_inputs, [], True)`, never an empty confirmed set, and spawns no subprocess) and `MAX_DISPATCH_NODES` (prompt lists exactly 10 titles; only those are recorded as dispatched).
- Add the **serial-trust** tests, keyed on coverage: a serial report with an **empty `tests` list** and non-empty `ordered` → `serial_trusted=False`, `main()` takes `_fatal`, no dispatch, and the pre-existing state file is byte-identical afterwards — assert explicitly that `confirmed` is not emptied into `artifacts`, which is the false green being closed; a report missing any one input node → `False`; a report covering every input node with mixed `error`/`failed` outcomes → `True`; the `:261-263` catch-all and the `MAX_RECONFIRM_NODES` bail both → `True`, so a timing-out re-confirm can still write a baseline instead of wedging the detector forever.
- Assert `reconfirm_serial`'s subprocess carries `TEST_DB_CLAIM_WAIT_S=300` in its env, the same way as `run_tests()`'s argv.
- Update `TestReconfirmSerial`'s three direct call sites (`:165`, `:186`, `:196`) to unpack `(confirmed, artifacts, serial_trusted)` — each currently unpacks two values and hard-fails on the widened return.
- Update `_run_main`'s `reconfirm_serial` patch (:521) to the 3-tuple `(list(confirmed), [], True)`, and give every `prev` fixture (:540, :549, :556, :568, :580, :597) a `"collection": COLLECTION_PATHS` key — without it Task D routes all seven inherited methods onto the seed branch and `test_standing_failure_is_not_re_dispatched` fails. Fix the fixtures; do not relax the assertions.
- Add the deliberate **collection-mismatch** test: a `prev` with no `collection` key asserts dispatcher called with `[]`, `saved["dispatched_nodes"] == sorted(confirmed)`, and the re-baseline WARNING logged. Mock `maybe_dispatch_triage_session` to return `None` — a truthy session id overwrites `just_dispatched` with `dispatch_nodes` (`[]`) and wipes the seed, inverting what the test claims to prove.
- In `TestMaybeDispatchTriage`, assert the literal titles: `msg = argv[argv.index("--message") + 1]`, then `f"Nightly regression: {node}" in msg` for each node.
- Pin the installer's worktree refusal by source text and by a behavioral run against a fixture `.git` file, and pin the `Nightly regression test service installed successfully.` marker in both the shell script and `scripts/update/service.py`.
- Add `run_tests()` argv coverage — none exists today; copy the pattern at `TestMaybeDispatchTriage::test_dispatch_once` (:438). Assert the worker count **against the constant** — `argv[argv.index("-n") + 1] == nrt.NIGHTLY_XDIST_WORKERS`, never the string `"6"`, so an operator override on a differently-sized machine does not turn the suite red. Assert the literal `TEST_DB_CLAIM_WAIT_S=300` env override is present, and assert the bound that actually governs — `int(env["TEST_DB_CLAIM_WAIT_S"]) * 2 <= 600`, `scripts/pytest-clean.sh:178`'s `PYTEST_STALL_LIMIT_S` — so a later "be more patient" edit fails the suite instead of silently walking into the stall watchdog. **Not** `< 420`: since #2628 the claim polls in `pytest_configure`, before any test item, so `pyproject.toml:196`'s per-item `--timeout` arms no timer over it and an assertion against it would pin a ceiling that does not govern.
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
| Env-load refusal is no longer a silent `SystemExit` | `! grep -q 'raise SystemExit(1)' scripts/nightly_regression_tests.py` | exit code 0 — mutation-checked: exits **1 (FAIL)** on current `main`, where `:588` and `:603` both carry it |
| Env-load refusal routes to `_fatal` | `grep -q 'env_err' scripts/nightly_regression_tests.py` | exit code 0 — mutation-checked: exits **1 (FAIL)** on current `main` |
| Worker count is env-overridable | `grep -q 'NIGHTLY_XDIST_WORKERS' scripts/nightly_regression_tests.py` | exit code 0 — mutation-checked: exits **1 (FAIL)** on current `main` |
| `/update` observes the absence of a run | `grep -q 'nightly_tests_last_run' scripts/update/run.py` | exit code 0 — mutation-checked: exits **1 (FAIL)** on current `main` |
| Timeout arm delegates to `_fatal` | `grep -A3 'except subprocess.TimeoutExpired' scripts/nightly_regression_tests.py \| grep -q '_fatal('` | exit code 0 |
| Parse arm delegates to `_fatal` | `grep -A3 'except (FileNotFoundError, json.JSONDecodeError)' scripts/nightly_regression_tests.py \| grep -q '_fatal('` | exit code 0 |
| Fixture-error ceiling exists | `grep -q 'errored at setup' scripts/nightly_regression_tests.py` | exit code 0 |
| Coverage floor exists (the starvation guard) | `grep -q 'the run was truncated' scripts/nightly_regression_tests.py` | exit code 0 — mutation-checked: exits **1 (FAIL)** on current `main` |
| Pre-baseline floor is a tunable constant | `grep -q 'MIN_EXPECTED_COLLECTED' scripts/nightly_regression_tests.py` | exit code 0 — mutation-checked: exits **1 (FAIL)** on current `main` |
| Seed is persisted and escalated | `grep -q 'seed_size' scripts/nightly_regression_tests.py` | exit code 0 — mutation-checked: exits **1 (FAIL)** on current `main` |
| Staleness clock reads the plist mtime | `grep -q 'com.valor.nightly-tests.plist' scripts/update/run.py` | exit code 0 — mutation-checked: exits **1 (FAIL)** on current `main` |
| Re-confirm and dispatch are bounded | `grep -q 'MAX_RECONFIRM_NODES' scripts/nightly_regression_tests.py && grep -q 'MAX_DISPATCH_NODES' scripts/nightly_regression_tests.py` | exit code 0 |
| Timeout path owns its process group | `grep -q 'start_new_session=True' scripts/nightly_regression_tests.py && grep -q 'os.killpg' scripts/nightly_regression_tests.py` | exit code 0 |
| No bare timed-out `subprocess.run` remains | `! grep -q 'timeout=PYTEST_TIMEOUT_SECONDS,' scripts/nightly_regression_tests.py` | exit code 0 — the timeout moves to `communicate(timeout=…)`; verified to exit **1 (FAIL)** on current `main`, where `:198` carries it |
| Nightly run is slot-sized | `grep -q '"TEST_DB_CLAIM_WAIT_S": "300"' scripts/nightly_regression_tests.py` | exit code 0 |
| Both subprocesses carry the claim wait | `[ "$(grep -c '"TEST_DB_CLAIM_WAIT_S": "300"' scripts/nightly_regression_tests.py \|\| true)" = 2 ]` | exit code 0 — the parallel argv **and** `reconfirm_serial` |
| Install skip is a distinct outcome | `grep -q '"skipped"' scripts/update/service.py` | exit code 0 |
| Zero-collection regression test exists | `grep -q 'TestValidateRunIntegrity' tests/unit/test_nightly_regression_tests.py` | exit code 0 |
| Role gate replaced | `grep -q 'has_worker_role' scripts/install_nightly_tests.sh` | exit code 0 |
| Telegram predicate gone from gate | `! grep -q 'proj.get("telegram")' scripts/install_nightly_tests.sh` | exit code 0 |
| Installer refuses a worktree | `grep -q 'gitdir:.*/\.git/worktrees/' scripts/install_nightly_tests.sh` | exit code 0 |
| `/update` gate un-doubled | `! grep -B2 'service.install_nightly_tests' scripts/update/run.py \| grep -q 'if has_bridge:'` | exit code 0 |
| Serial pass can be untrusted | `grep -q 'serial_trusted' scripts/nightly_regression_tests.py` | exit code 0 |
| State records collection identity | `grep -q '"collection"' scripts/nightly_regression_tests.py` | exit code 0 |
| State records head commit | `grep -q 'head_commit' scripts/nightly_regression_tests.py` | exit code 0 |
| No merge-time test gate added (anti-criterion) | `! grep -q 'pytest' tools/merge_predicate.py` | exit code 0 |
| No CI test workflow added (anti-criterion) | `[ "$(ls .github/workflows/ \| grep -cv claude.yml \|\| true)" = 0 ]` | exit code 0 |
| Service installed after update | `launchctl list \| grep -q nightly-tests` | exit code 0 — **main checkout only, after a real `/update`.** N/A from a lane worktree; see Risk 6 |

Every row is exit-code-honest: run it, check `$?`, and `0` means pass. A bare `grep -c` is deliberately absent — it exits **1** when the count is zero, so a row expecting "no matches" would report FAIL on the correct end state. Where a count is genuinely the assertion, it is wrapped as `[ "$(grep -c … || true)" = N ]` so the row's own exit code carries the verdict.

The `/update` gate row is `-B2`-windowed on purpose. An earlier revision carried `grep -n 'if has_bridge:' scripts/update/run.py` with Expected "output does not contain `install_nightly_tests`" — a row that **passes today, before any change, and cannot fail after it**: run on `main` it exits 0 printing `1859`, `2017` (a comment) and `2038`, none of which contain the string, because the call is on the *following* line (`run.py:2039`). A whole-file pattern can never work here, since the plan deliberately keeps the unrelated `has_bridge` use at `:1859`. The replacement was mutation-checked during this revision: `! grep -B2 'service.install_nightly_tests' scripts/update/run.py | grep -q 'if has_bridge:'` exits **1 (FAIL) on current `main`** — `-B2` emits the comment at `:2037`, `if has_bridge:` at `:2038` and the call at `:2039` — and exits 0 once the wrapper is gone. Verified on `main` during revision: `! grep -q 'pytest' tools/merge_predicate.py` and the `ls`/`grep -cv` form both exit 0 today.

The four rows added in round 6 were mutation-checked the same way, against the working tree on `main` at revision time: `grep -q 'the run was truncated' …`, `grep -q 'MIN_EXPECTED_COLLECTED' …`, `grep -q 'seed_size' …` and `grep -q 'com.valor.nightly-tests.plist' scripts/update/run.py` each exit **1 (FAIL)** today and 0 once their change lands. The count of rows is deliberately not stated anywhere in this section — round 6 found a stale "21" in the one paragraph whose subject is the completeness of the mutation check, so the claim is now phrased over *every* non-anti-criterion row and stays true as rows are added.

The four rows added in round 5 were mutation-checked the same way, against the working tree on `main` at revision time: `! grep -q 'raise SystemExit(1)' …`, `grep -q 'env_err' …`, `grep -q 'NIGHTLY_XDIST_WORKERS' …` and `grep -q 'nightly_tests_last_run' scripts/update/run.py` each exit **1 (FAIL)** today and 0 once their change lands. The `SystemExit` row is stated as a negation on purpose: the property is that *no* raise-and-exit path remains, which is what makes it count paths rather than arms.

The two `_fatal` delegation rows are stated **positively** for the same reason. An earlier revision carried `! grep -nE '^\s+return 1\s*$'`, which can never pass once `_fatal()` lands: the helper's own body ends with a four-space-indented `return 1`, which the pattern matches, so grep exits 0 and the negation exits 1 — FAIL on the correct end state. Confirmed by running the pattern against a stub during this revision. Both delegation rows are **smoke checks only**: each `-A3` pattern matches two sites in the file (one inside `run_tests`, one inside `main`), and the pipe succeeds if either contains `_fatal(`. `Test_Fatal` in the unit suite is the real proof that both `main()` arms alert; the greps exist so a validator gets a fast signal without reading the diff.

## Critique Results

Round 7: two blockers, four concerns, one nit. Every citation below was read out of the
working tree on `main` during the critique, and the two replacement Verification rows proposed
here were mutation-checked against `main` before being written down. Both blockers are round-6
remedies that landed in only some of the sections they govern: the seed escalation adopted the
`main()` bookkeeping that wipes the seed it books, and the staleness-warning clock was rewritten
in the test bullets while all five build-side statements kept the falsified spec. Findings from
rounds 1-6 are recorded in the Round-2 through Round-6 Revision Notes and are not repeated here;
this table is the round-7 record.

*Depth note: FULL parallel dispatch was unavailable in this execution context (no agent-spawn
tool), so the three lenses were applied by the driver in a single source-verified pass rather
than by three concurrent critics.*

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Consolidated Critic | The round-6 umbrella escalation destroys the baseline seed on its success path. Step 3's bullet at :741 says to call `maybe_dispatch_triage_session` on the re-baseline branch guarded by "the same `if triage_session_id is not None` bookkeeping `main()` uses at `:707-711`" -- and that block does `just_dispatched = dispatch_nodes` (`scripts/nightly_regression_tests.py:706-710`). On the seed branch `dispatch_nodes` is `[]` while `just_dispatched` was set to `list(confirmed_failing)` (`:691-694`), so a *successful* umbrella dispatch replaces the seed with `[]`; `carry_dispatched_nodes(prev, confirmed_failing, [])` (`:426-440`) then persists only `prior_dispatched(prev) & set(confirmed_failing)`, dropping every non-unit red node and every newly-red unit node. Night two hands the whole pre-existing red population to `compute_dispatch_set` and files 10 issues a night (`MAX_DISPATCH_NODES`) until exhausted -- Risk 1 and #2429/#2430/#2462 reopened by the edit meant to book the seed. Test Impact :445 already documents this arithmetic ("A truthy mock would wipe the seed") without noticing it now describes production, and the test it mandates mocks the dispatcher to `None`, so the bug is invisible to the specified suite. | pending | Split the branch instead of reusing the block: on the re-baseline path call the umbrella dispatch and set only `current["dispatched_session_id"] = sid` when `sid is not None`, leaving `just_dispatched = list(confirmed_failing)` untouched; keep the existing `:706-710` reassignment on the `else` (non-seed) branch. Add the regression test as the *inverse* of Test Impact :445 -- mock the umbrella dispatch to return a truthy session id and assert `saved["dispatched_nodes"] == sorted(confirmed_failing)`. A `None` mock proves nothing: `maybe_dispatch_triage_session` already returns `None` on an empty list (`:519-520`), so that test passes before and after the change. |
| BLOCKER | Consolidated Critic | The round-6 staleness-warning remedy landed on the test-side bullets only; every build-side statement still carries the falsified specification. The adopted remedy (Critique Results row and Round-6 Revision Note 5) is "warn only when `now - max(plist_mtime, run_at) >= 2 days`", with an absent or malformed `run_at` falling back to the plist mtime rather than to "unknown, warn". That text appears in exactly three places -- Test Impact :456, Step 6 :796 and Verification row :866. All five statements a builder implements from (Task C :365, Risk 8 :489, Update System :553, Success Criterion :618, Step 5 :770) still say "when the file is missing or `run_at` is 2 or more days old" and "treat both as 'unknown, warn'". A builder following :770 ships exactly the false positive concern 5 identified, and Step 6's first mandated test ("missing state file with a plist mtime of today -> no warning") fails against the code the same plan told them to write. Verified this round: both `~/Library/LaunchAgents/com.valor.nightly-tests.plist` and `data/nightly_tests_last_run.json` are absent on this machine, so the `/update` that installs the detector takes the `"installed"` leg and warns. | pending | Rewrite :365, :489, :553, :618 and :770 to the plist-mtime clock so builder and tester get one specification. In `scripts/update/run.py` on the `"installed"` leg: resolve `Path.home() / "Library/LaunchAgents/com.valor.nightly-tests.plist"`, take `datetime.fromtimestamp(plist.stat().st_mtime, UTC)` when it exists, parse `run_at` inside `except (OSError, ValueError, KeyError, json.JSONDecodeError)`, then `newest = max([d for d in (anchor, run_at) if d is not None], default=None)` and warn when `newest is None or (datetime.now(UTC) - newest) >= timedelta(days=2)`. `newest is None` (no plist AND no readable `run_at`) is the only surviving "unknown, warn" case. |
| CONCERN | Consolidated Critic | The umbrella dispatch cannot be expressed through the function it is told to call, and it collides with Step 5b's rewrite of that same function with no dependency edge. `maybe_dispatch_triage_session(dispatch_nodes: list[str]) -> str \| None` (`scripts/nightly_regression_tests.py:503`) builds its prompt internally and takes no prompt argument, so "call `maybe_dispatch_triage_session` once with a distinct prompt ... Pass a single synthetic entry" needs a signature change that Architectural Impact's interface list (:165) does not carry. Step 5b rewrites that internal prompt to emit `titles = [f"Nightly regression: {n}" for n in dispatch_nodes]` verbatim, so the synthetic seed entry would emit a per-node title inside the very prompt that says "do **not** file per-node issues", and the slug becomes `nightly-triage-<sha of a synthetic id>` (`:521-522`). Step 3 (`Depends On: build-widen-collection`) and Step 5b (`Depends On: build-integrity-guard`) carry no ordering edge although both rewrite this function -- the defect Step 5b was created to fix, stated at :781: "A dispatcher reads the graph, not the prose." | pending | Give the function an explicit override -- `def maybe_dispatch_triage_session(dispatch_nodes: list[str], *, prompt: str \| None = None, slug_suffix: str \| None = None) -> str \| None`, defaulting to the Step-5b per-node prompt built from `titles`; the seed path passes its umbrella prompt and `slug_suffix="baseline"` so a retried seed reuses one slug instead of hashing a synthetic node id. List it in Architectural Impact and add `build-dispatch-titles` to Step 3's `Depends On`. Cheaper alternative, explicitly offered by the round-6 critic and which also dissolves the seed-wipe blocker: soften the Desired Outcome and add `[SEPARATE-SLUG #2852] Escalating the seeded baseline population` to No-Gos, deleting Step 3 :741 and SC :626 -- one edit, and it keeps the plan inside its Medium appetite. |
| CONCERN | Consolidated Critic | `MIN_EXPECTED_COLLECTED` cannot be seeded the way three sections say it is, and the consequence is that the widening night runs with no coverage floor at all. The floor reads a module constant -- `int(os.environ.get("NIGHTLY_MIN_EXPECTED_COLLECTED", "<probe total>"))`, a source literal -- while Step 3 :740 instructs "Seed `MIN_EXPECTED_COLLECTED` from this run's `total` in the same edit" that writes `seed_size`, which is a runtime state write. Even granting it, the value is unreachable afterwards: `run_tests()` returns `total` (`scripts/nightly_regression_tests.py:218`) and `save_last_run` persists `current` whole, so from night two on `prev["collection"] == COLLECTION_PATHS` and `prev["total"]` both hold and the ratio branch always wins. The unstated consequence is the sharp one: on the widening night `prev` records `tests/unit/` so the ratio branch is skipped, and the probe is the *expected* casualty of slot exhaustion (spike-2 and spike-7 record four defeated attempts), leaving the constant at `0` -- no floor on the single night whose result becomes the permanent seed. A starved widening night seeds a truncated baseline and sets night two's floor to `0.9 x` that truncated number. | pending | Persist the floor rather than pretending to seed a constant: on the re-baseline branch write `current["min_expected_collected"] = current["total"]`, and in `validate_run_integrity` use `floor_base = prev.get("min_expected_collected") or MIN_EXPECTED_COLLECTED` when `prev.get("collection") != COLLECTION_PATHS`. For the widening night itself either land the probe's `summary.total` as the constant's default (the plan's stated first choice) or say plainly in Task A/Task B that the seed night is deliberately floorless and that an implausibly small `seed_size` in the baseline Telegram text is the operator's only signal. |
| CONCERN | Consolidated Critic | The Success Criterion "Both pytest spawns use `subprocess.Popen(..., start_new_session=True)`" (:612) has no Verification row that can fail for it -- inside the one section whose stated discipline (Prior Art :82, #2658) is that a row which cannot exit non-zero is not a check. Row :868 is `grep -q 'start_new_session=True'`, satisfied by a single occurrence. Row :869 is `! grep -q 'timeout=PYTEST_TIMEOUT_SECONDS,'`, and `reconfirm_serial`'s spawn carries `timeout=PYTEST_RECONFIRM_TIMEOUT_SECONDS` (`scripts/nightly_regression_tests.py:257`, verified in the working tree) -- so converting only `run_tests()` and leaving the serial spawn a bare `subprocess.run(..., timeout=PYTEST_RECONFIRM_TIMEOUT_SECONDS)` passes both rows green. Step 2's own prose invites the miss: :695 says convert both spawns to `Popen`, then :702 still refers to "the serial `subprocess.run` at `:242-258`". | pending | Replace :868 with `[ "$(grep -c 'start_new_session=True' scripts/nightly_regression_tests.py \|\| true)" = 2 ]` and :869 with `! grep -qE 'subprocess\.run\([^)]*timeout=PYTEST_(RECONFIRM_)?TIMEOUT_SECONDS' scripts/nightly_regression_tests.py`. Mutation-checked in this round: the count row exits 1 on `main` today (`start_new_session` absent, `grep -c` yields 0) and the negated row exits 1 on `main` today (`:257` matches), so both are falsifiable in the required direction. Fix the stale `subprocess.run` wording at :702 in the same pass. |
| CONCERN | Consolidated Critic | Round 6 added the umbrella escalation to the Step-by-Step task and the Success Criteria and left the canonical Technical Approach contradicting both. Task D still reads "seeding `dispatched_nodes` and dispatching nothing" (:392) and "re-baselining, dispatching nothing" (:394), and Rabbit Holes :473 still says "opening the umbrella issue against it is a separate lane" -- a builder working from the Technical Approach implements no escalation at all. Separately, Round-6 Revision Note 4 asserts "The Desired Outcome is also corrected to say 'a test that **becomes** red ... after the baseline is seeded'"; :40 still reads "A test that is red on `main` under default collection produces a filed issue within 24 hours", and :473 states in the same document that this outcome is not true for the pre-existing population. A revision note attesting to an edit that was not made is the audit defect the plan's own #2658 convention exists to eliminate. | pending | Pick one story about the seed night and propagate it to all four sites in one pass. If the escalation stays: edit :394-396 to state that the seed path dispatches exactly one umbrella session, edit :473 so the umbrella issue is filed by the detector while *repairing* the tests stays the separate lane, and apply the :40 softening the round-6 notes already claim. If it is dropped per the concern above: delete SC :626 and Step 3 :741 and add the No-Go entry instead. Either way the "revision applied" claim at :986-988 must match the file. |
| NIT | Consolidated Critic | Four `docs/features/nightly-regression-tests.md` bullets (per-machine `dispatched_nodes`, how starvation actually presents, the worktree refusal, the seed night) sit under `### External Documentation Site` at :582-588, whose body is "Not applicable -- this repo has no Sphinx/MkDocs site", so a documentarian scanning subsection headers can reasonably skip them. Separately, the `tests/README.md` row for `test_nightly_regression_tests.py` is at line 421, not the `:417` cited at both :466 and :580 (`:417` is the `### Other` heading). | pending | n/a (NIT) -- move the four bullets under `### Feature Documentation` and correct both `:417` citations to `:421`. |

## Round-6 Revision Notes

Every round-6 finding is adopted. Citations were re-verified against the working tree on `main`
before adoption, as in every prior round. One remedy ships **wider** than proposed and the
widening is argued below rather than left for a reviewer to discover.

**Citations verified before adoption.**

| Claim | Check | Result |
|---|---|---|
| The claim runs in `pytest_configure`, before collection | read `tests/conftest.py:274-288` | confirmed — the hook returns early for the xdist *controller* (`numprocesses` set), else calls `claim_test_db()` |
| A failed claim aborts the session rather than erroring per test | read `tests/conftest.py:285-287` | confirmed — `except RuntimeError as exc: pytest.exit(str(exc), returncode=3)` |
| The in-code comment states the intent verbatim | read `tests/conftest.py:286` | confirmed — "One line of output instead of a setup error on every collected test." |
| The fixture's own claim is downstream and unreachable when configure aborted | read `tests/conftest.py:883` | confirmed — `test_db = claim_test_db()` inside the fixture body |
| `reconfirm_serial` returns a 2-tuple and derives `confirmed` by set membership | read `scripts/nightly_regression_tests.py:240-267` | confirmed — `serial_failing` at `:265`, `confirmed`/`artifacts` at `:266-267`, catch-all `return ordered, []` at `:262` |
| The file already uses env-overridable "provisional/tunable" constants | read `:92-96` | confirmed — `MIN_ENV_KEYS = int(os.environ.get("NIGHTLY_MIN_ENV_KEYS", "10"))` |
| `main()` guards dispatch bookkeeping on a non-`None` session id | read `:706-712` | confirmed — the `if triage_session_id is not None:` block the seed escalation reuses |
| The installer is idempotent and prints its success line every run | read `scripts/install_nightly_tests.sh:69, 95, 104, 107` | confirmed — `bootout` then `launchctl_bootstrap_fail_soft`, then the success echo |
| `PYTEST_STALL_LIMIT_S` defaults to 600 | read `scripts/pytest-clean.sh:178` | confirmed; `STALL_SAMPLE_S=30` at `:179` |
| All four new Verification rows are falsifiable | ran them on `main` | all four exit **1 (FAIL)** today, 0 once their change lands |

**1. Blocker: the starvation failure model was wrong, and the guard built on it could not fire.**
The plan modelled a starved worker as raising at setup for every test it touches, producing
`error` outcomes the ceiling would count. Since #2628 the claim aborts the worker's *session* in
`pytest_configure`, so a starved worker produces **zero `error` outcomes and zero test items** and
dies as "node down". Partial starvation therefore yields exit **0**, `error = 0` and a *reduced*
`total` — a shape that passed every trip condition the plan had. The remedy is a **blocking
coverage floor on `total`**: `0.9 × prev_total` within the same collection, or
`0.9 × MIN_EXPECTED_COLLECTED` (a provisional/tunable constant seeded from Task A's probe, or from
night one's own total) before a same-collection baseline exists. The error ceiling is kept and
re-scoped to fixture-error storms, with an explicit prohibition on any comment, docstring or test
name attributing it to starvation. Race 1 Shape 2, the Step 2 bullet, the Success Criterion, the
Failure-Path tests and the docs obligation all now describe starvation as **missing tests**.
The irony is worth naming: this is issue #2823's own defect — a gate that cannot fire for its
stated cause — reproduced inside the plan meant to close it, and caught only because the #2658
mutation-check convention makes "can this check ever fire?" a required question.

**Where this ships wider than proposed.** The critique asked for `total < 0.9 * prev_total` to
trip *instead of* warning. Taken literally that would delete the round-2 union-preserving state
write, which exists because `carry_dispatched_nodes` drops an already-filed node on **any**
shrink, not only a deep one. So the band is split rather than flipped: `< 0.9 ×` trips, and
`0.9 × … < 1.0 ×` keeps the warning and keeps the union-preserving write. Both are computed only
when `prev.get("collection") == COLLECTION_PATHS`. Resolved Question 3 records the reversal and
why the reasoning changed with the mechanism, rather than reading as a decision reopened on taste.

**2. Concern: `serial_trusted` is keyed on result coverage, not on an `error` count.**
The same falsified premise reappeared in the serial pass, where its failure direction is a false
green: a configure-time `pytest.exit` still runs `pytest_sessionfinish`, so a report with zero test
entries is written, `serial_errors` is `0`, the pass reads as trusted, and `:266-267` reclassifies
every red node as an xdist artifact and persists an empty `failing_tests`. `serial_trusted` becomes
`all(n in seen for n in ordered)` over the report's node IDs. The round-3 scoping is preserved
verbatim: the `:261-263` catch-all and the `MAX_RECONFIRM_NODES` bail still return `True`, so a
timing-out re-confirm seeds a baseline instead of wedging the detector into nightly fatals.

**3. Concern: the `TEST_DB_CLAIM_WAIT_S` invariant is re-derived against the bound that governs.**
The value stays `300`; what was wrong was the reason. `pyproject.toml:196`'s `--timeout=420` is
armed per test item in the runtest protocol, and the claim polls in `pytest_configure` before any
item exists, so no timer covers it. The comment, the unit assertion
(`int(env["TEST_DB_CLAIM_WAIT_S"]) * 2 <= 600`) and the Success Criterion now cite
`scripts/pytest-clean.sh:178`'s `PYTEST_STALL_LIMIT_S`. The "legal only with an explicit
`--timeout=<n>`" escape clause is deleted rather than reworded — it would have changed nothing and
would have sent the next reader to tune the wrong dial. This is the third round in which a
correct value carried an incorrect derivation; the lesson recorded is that an assertion pinning
the wrong quantity is worse than no assertion, because it looks like coverage.

**4. Concern: the seeded population is escalated, not merely persisted.** Task D's re-baseline
absorbs every currently-red node permanently, which is the population #2823 is about. Persisting
`seed_size` and the node list made it readable; nothing made anyone read it. The seed path now
dispatches **one** umbrella triage session with a distinct prompt and an exact title
(`Nightly regression baseline: {seed_size} nodes absorbed on {head_commit}`), guarded by the same
`if triage_session_id is not None` bookkeeping `main()` already uses at `:706-712`. The Desired
Outcome is also corrected to say "a test that **becomes** red … after the baseline is seeded",
because the headline previously claimed per-node coverage the mechanism does not provide.

**5. Concern: the staleness warning is anchored to the plist mtime.** Keyed on file-absence it
would have fired on the very `/update` that installs the detector, on every machine, and kept
firing until a 03:00 run landed — the installer is idempotent and takes the `"installed"` leg every
run. It now warns only when `now - max(plist_mtime, run_at) >= 2 days`, and an absent or malformed
`run_at` falls back to the plist mtime rather than to "unknown, warn". Two boundary tests are added
so the false positive cannot be pinned as the specification.

**6. Concern: Risk 4's #2334 disposition is corrected from "conflict" to "needs re-planning".**
This plan changes `run_tests()`'s and `reconfirm_serial()`'s return contracts, both spawn shapes
and `load_env_or_die()`'s signature, so #2334's Technical Approach is stale at its call sites
rather than conflicting at one line. Step 8 gains an obligation to post that as a comment on
#2334 at merge, because coordination living only in a plan doc #2334's builder will not read is
not coordination. The No-Go entry keeps its slug routing and loses its "already planned and
tracked" framing.

**7. Both NITs adopted.** The `(FileNotFoundError, json.JSONDecodeError)` arm is redescribed as
the missing-executable path — Task B removes `json.JSONDecodeError`'s route into it — and
`Test_Fatal`'s third case becomes the integrity trip via `run_tests()` returning `(None, None, 1)`
rather than a "corrupt JSON" case for a route that no longer exists. The stale row count is
replaced with "every non-anti-criterion Verification row" in both places, so no future round has
to recount.

**Nothing is left unresolved.** No finding was judged wrong and dropped.

**One process note for the next reader.** Part of this revision was lost mid-pass when a
concurrent lane's `git stash` / `git checkout main` cycle reverted this file in the shared main
checkout, and the first commit captured only three lines of it. The edits were re-applied from a
verified diff and re-audited by grepping for both the new text and the superseded text. This is
the hazard the do-plan skill's Phase 4 note describes; the mitigation that worked was committing
in small batches and auditing by marker string rather than trusting edit-tool success.

## Round-5 Revision Notes

Every round-5 finding is adopted, each with the remedy the critique itself specified. This
round was deliberately mechanical: the critic wrote the complete remedy into the findings
table, so this pass splices that text into the plan body rather than re-deriving anything.
No settled decision was reopened and no task was added beyond what the findings require.
Citations were re-verified against the working tree on `main` before adoption.

| Claim | Check | Result |
|---|---|---|
| `load_env_or_die` raises `SystemExit(1)` at two sites | read `scripts/nightly_regression_tests.py:580-607` | confirmed — `:588` on `OSError` from `dotenv_values`, `:603` on `applied < MIN_ENV_KEYS`; both log `FATAL:` and nothing else |
| `main()` calls it before the run lock | read `:609-626` | confirmed — `parse_args()` at `:612`, `load_env_or_die()` at `:619`, `_acquire_run_lock()` at `:624` |
| `args.dry_run` is already in scope at the call | read `:612` | confirmed — seven lines earlier; no restructuring needed |
| `tools/doctor.py` has no nightly/launchctl coverage | `grep -cin 'nightly\|launchctl' tools/doctor.py` | confirmed — `0` |
| The file already uses env-overridable constants | read `:76`, `:96` | confirmed — `PYTEST_RECONFIRM_TIMEOUT_SECONDS` and `MIN_ENV_KEYS = int(os.environ.get("NIGHTLY_MIN_ENV_KEYS", "10"))`, both with "provisional / tunable" comments |
| `docs/plans/gates-that-cannot-fire.md` exists | `ls docs/plans/gates-that-cannot-fire.md` | confirmed |
| All four new Verification rows are falsifiable | ran them on `main` | all four exit **1 (FAIL)** today, 0 once their change lands |

**1. Blocker: `main()` has four silent pre-alert exits, and the criterion now counts paths.**
`load_env_or_die` changes from raising to returning `tuple[int, str | None]`, both refusals
route through `_fatal()`, and the existing `FATAL:` strings carry over verbatim as the reason
text so the alert an operator gets is the diagnosis. Two gotchas are recorded in the task: the
path runs before `_acquire_run_lock()` and must not be read as a collision, and a
TCC-denied machine may fail to send at all, which is acceptable because `send_telegram()` is
never fatal and the `MIN_ENV_KEYS` partial-read case still has enough environment. `Test_Fatal`
grows a third case; the Success Criterion is restated as "every non-zero exit from `main()`,
including the env-load refusal", so a validator counts paths rather than arms. Task C is what
made this decisive: un-gating the installer ships the detector to machines whose TCC grant is
exactly what differs, and a machine without it would exit 1 nightly forever while `/update`
reported `installed`.

**2. Concern: something now observes the absence of a run.** One staleness check in
`/update`, on the `"installed"` leg, over `run_at` in a state file the plan already persists.
No new state, no new service, and gated so it cannot fire where no detector belongs. Recorded
as Risk 8 with its residual named: it bounds the blind window to the `/update` interval rather
than closing it, and a machine that never runs `/update` is still unobserved.

**3. Concern: the fallback ladder is replaced by a floor and a second way to satisfy the
criterion.** `-n 4` is stated as the practical floor with its arithmetic, the completion
attempt moves out of `run_tests()` into a direct probe not bound by a constant the same task is
still choosing, and night one's baseline alert reporting `total > 0` is accepted as post-merge
evidence. Stop-and-report is kept, narrowed to a probe that fails for a reason other than slot
exhaustion — the criterion is about the collection being executable, not about contention.

**4. Concern: the worker count becomes an env-overridable constant.** `NIGHTLY_XDIST_WORKERS`
defaults to `6` and carries its derivation, following the convention `MIN_ENV_KEYS` and
`PYTEST_RECONFIRM_TIMEOUT_SECONDS` already set in this file. The argv test asserts the
constant, never the literal. `TEST_DB_CLAIM_WAIT_S = "300"` stays pinned exactly as round 3
specified — its literal assertion is load-bearing because a real invariant sits behind it.

**5. Concern: the Rabbit Hole is redrawn where the plan actually draws the line.** It now rules
out a concurrency redesign (shared scheduler, slot broker, dynamic sizing) and says plainly that
choosing a fixed worker count and claim wait for the unattended run *is* Task A. The recon
detail stays as evidence for both halves. This is the third round in which prose in one section
contradicted the instruction a builder executes; the correction this time is to the section
title's claim, not only its last clause.

**6. All three NITs adopted.** The shrink ratio is computed only when
`prev.get("collection") == COLLECTION_PATHS`, with a test in the same class as the existing
`prev = {}` case. #2658 joins Prior Art as the source of the two-pole mutation-check convention
this Verification table follows. `install-builder`'s Role line now names all three files it
touches.

**Nothing is left unresolved.** No finding was judged wrong and dropped.

## Round-4 Revision Notes

Every round-4 finding is adopted. Citations were re-verified against the working tree
before adoption, as in prior rounds; the checks are recorded so a later reader need not
repeat them.

| Claim | Check | Result |
|---|---|---|
| `run_tests()` uses `subprocess.run(..., timeout=)` | read `scripts/nightly_regression_tests.py:182-199` | confirmed, `timeout=PYTEST_TIMEOUT_SECONDS` at `:198`; `reconfirm_serial` the same at `:257` |
| The wrapper traps but does not `exec` | read `scripts/pytest-clean.sh` | `trap cleanup EXIT INT TERM HUP PIPE` at `:109`; `"$PYTEST_BIN" "$@" &` at `:241` under the "intentionally do NOT use `exec`" comment at `:238-240` |
| `cleanup()` never targets the controller | read `pytest-clean.sh:101-103`, `:42` | confirmed — `cleanup()` calls only `reap_workers`, which pgreps `XDIST_WORKER_RE` (worker cmdline only) |
| A collector outcome is never `error` | read `pytest_jsonreport/serialize.py:17-29` | confirmed — the entry copies `CollectReport.outcome`, which is `passed`/`failed`/`skipped` |
| `summary` is keyed on test items only | read `serialize.py:104-108` | confirmed — `Counter([t['outcome'] for t in tests.values()])` |
| Night two's dispatch set is empty after a seed | read `nightly_regression_tests.py` seed path and `:698` | confirmed — the seed persists the whole confirmed set as `dispatched_nodes`, which `compute_dispatch_set` subtracts |
| The truncation site matters | read `main()` `dispatch_nodes` / `just_dispatched` / `carry_dispatched_nodes` flow | confirmed — truncating inside the dispatcher leaves `main()`'s list whole and records the untruncated set |
| The two nit citations | read `tests/unit/test_nightly_regression_tests.py:513`, `scripts/nightly_regression_tests.py:644-650` | confirmed — `sys.argv` patch is at `:513`; `:644` is the parse arm's bare `return 1`, consumption starts `:646` |
| `TestReconfirmSerial` unpacks two values | `grep -n 'reconfirm_serial(' tests/unit/test_nightly_regression_tests.py` | confirmed at `:165`, `:186`, `:196` |
| Both new Verification rows are falsifiable | ran them on `main` | both exit **1 (FAIL)** today, 0 once the change lands |

**1. Blocker: the timeout path owns its process group, and it lands with Task B.** The remedy ships as proposed and is deliberately not expanded: `Popen(..., start_new_session=True)`, `communicate(timeout=)`, and a `killpg` SIGTERM/SIGKILL pair on `TimeoutExpired`, at both spawn sites. It sits in Task B rather than Task E because Task B is already rewriting `run_tests()`'s return contract, and splitting them would mean rewriting the same function twice. Two Verification rows and a failure-path test cover it; both rows were mutation-checked against `main`.

**2. Concern: the collectors leg is deleted rather than repaired.** It could never fire, and the obvious widening to `!= "passed"` would send one broken import through `_fatal` nightly with no baseline ever written — the detector going permanently dark on exactly the regression class this plan exists to surface. The docstring now has to record why the leg is absent, so the next reader does not re-add it. `main()`'s existing collection-error alert branch stays as the reporting path.

**3. Concern: `MAX_DISPATCH_NODES` truncates in `main()`, at a named site.** Inside `maybe_dispatch_triage_session` the cap would have recorded the full set as dispatched and suppressed nodes 11..N forever. The plan now names the line, the ordering relative to the dispatcher call, and the `suppressed` log correction, with a two-run assertion.

**4. Concern: Resolved Question 4 was false and is corrected.** The seed empties night two's dispatch set by construction, so the red count arrives in night one's own baseline alert, not from a later dispatch. Beyond the correction, the seed is now *auditable*: `seed_collection`, `seed_size` and the seeded node list are persisted, and the baseline Telegram carries the count. Rabbit Holes and the docs obligation state plainly that the pre-existing red population is absorbed permanently and is a separate lane. Repairing it stays out of scope; leaving it invisible does not.

**5. Concern: the dispatch work moves to its own detector-owned step.** Step 5's last two bullets edited `scripts/nightly_regression_tests.py` while the step was assigned to `install-builder` with `Depends On: none, Parallel: true`, scheduling a concurrent write against step 2b on the same file. They are now Step 5b `build-dispatch-titles` (`detector-builder`, `Depends On: build-integrity-guard`, `Parallel: false`), Step 6 depends on it, and Step 5 is installer plus `/update` only. Same defect class round 3 fixed for Task 1: a dispatcher reads the graph, not the prose.

**6. Both NITs adopted.** `TestReconfirmSerial`'s three two-value unpack sites are named in Test Impact and Step 6; the `:511` and `:644-648` citations are normalized to `:513` and `:646-650`.

**Nothing is left unresolved.** No finding was judged wrong and dropped.

## Round-3 Revision Notes

Every round-3 finding is adopted. One remedy ships **narrower** than proposed and the
narrowing is argued below rather than left for a reviewer to discover. Citations were
re-verified against the working tree first; the checks are recorded so a later reader
need not repeat them.

**Citations verified before adoption.**

| Claim | Check | Result |
|---|---|---|
| `--timeout=420 --timeout-method=thread` is in `addopts` | `grep -n addopts pyproject.toml` | `:196`, verbatim |
| `tests/db_claim.py` states the ceiling invariant in-code | `grep -n 'timeout=420' tests/db_claim.py` | `:74` ("stays well inside pytest's `--timeout=420` ceiling"), constant at `:75` |
| `PYTEST_STALL_LIMIT_S` defaults to 600 | `grep -n PYTEST_STALL_LIMIT_S scripts/pytest-clean.sh` | `:178` |
| `reconfirm_serial`'s catch-all range | `grep -n 'except (subprocess.TimeoutExpired' scripts/nightly_regression_tests.py` | `:261-263`; `:259` is the exit-code log, `:260` the parse — the round-3 NIT is correct |
| `reconfirm_serial` spawns its own pytest with no env | read `:242-258` | confirmed, bare `sys.executable -m pytest`, no `env=` |
| `TestMainDispatchPersistence` fixtures carry no `collection` key | read `tests/unit/test_nightly_regression_tests.py:490-600` | confirmed, all six `prev` dicts; `_run_main` calls the real `nrt.main()` |
| The old `/update` Verification row is unfalsifiable | ran it on `main` | exits 0 printing `1859`, `2017`, `2038`; `install_nightly_tests` is on `:2039`, never in the output |
| The proposed replacement is falsifiable | ran `! grep -B2 'service.install_nightly_tests' … \| grep -q 'if has_bridge:'` | exits **1** on `main` today, as the critic reported |

**1. Blocker: `TEST_DB_CLAIM_WAIT_S` ships as `300`.** The 600 value came from this plan's own round-2 revision, and it is unreachable: `pyproject.toml:196`'s per-item `--timeout=420` covers fixture setup, where `claim_test_db()` polls. Beyond adopting `300`, the plan now carries the *invariant* rather than only the value — a source comment at the constant, a unit assertion that the env value is below 420, and a Verification row — so the next "let it be more patient" edit fails the suite instead of silently re-creating the same unreachable window. The failure-mode inversion is the reason it matters: a worker killed by the timeout thread reports **no outcome**, while a worker that raises reports `error`, and `error` is the only thing Task B's ceiling can count.

**2. Concern: serial re-confirmation is hardened, but `serial_trusted=False` is scoped narrower than proposed.** The env override and the all-errored trust check are adopted verbatim, including the widened `tuple[list[str], list[str], bool]` return and the `_fatal` route. The narrowing: the `:261-263` catch-all and the `MAX_RECONFIRM_NODES` bail return `True`, not `False`. Untrusting them would resurrect exactly the deadlock round 2 rejected — a machine whose 200+ node re-confirm reliably exhausts its 900 s budget would take the fatal path every night and never write a baseline, leaving the detector unable to report anything at all. The critique's remedy in fact scopes the trip to `serial_errors >= len(ordered)` and asks for nothing more; the distinction is that an all-errored report is *positive evidence* of setup failure, where a timeout is an absence of information. The residual is named in the Technical Approach: a timing-out serial pass still seeds an over-broad baseline, corrected by the next night's re-derivation, same disposition as Race 3.

**3. Concern: `TestMainDispatchPersistence` reclassified VERIFY → UPDATE.** The entry had reasoned only about the dropped argparse flag. Task D breaks all seven inherited methods, and the plan now names the six fixture line numbers, the `_run_main` 3-tuple patch, and the trap in the new mismatch test (a truthy `maybe_dispatch_triage_session` mock overwrites `just_dispatched` and inverts the assertion). "Fix the fixtures, do not relax the assertions" is stated explicitly, because a test engineer told "likely no change" who then sees red is exactly who relaxes an assertion.

**4. Concern: the `/update` Verification row was unfalsifiable and is replaced.** Same defect class as the round-2 `grep -c` and `! grep -nE 'return 1'` findings — three in a row on this table — so the note beneath it now records the mutation check itself, not just the corrected command.

**5. Concern: Risk 7 downgraded from "mitigated" to "partially mitigated, bounded."** The detector now computes `titles = [f"Nightly regression: {n}" …]` in Python and embeds them verbatim, which removes the derivation risk and, unlike an instruction, is assertable. What it does not remove is the compliance risk: nothing verifies an issue was filed under the title. Risk 7's Mitigation and the docs obligation both say so plainly and name the shared Redis-backed dispatch set as the real fix, deferred. The critique's observation that the exposure defends an unverifiable fleet is accepted; the cost stays because `MAX_DISPATCH_NODES` is cheap and doubles as the error-storm bound.

**6. Both NITs adopted.** Citations normalized to `:261-263`; task 1 now declares `Depends On: build-fatal-path` and `Parallel: false` rather than asserting the constraint in prose a dispatcher does not read.

**Nothing is left unresolved.** No finding was judged wrong and dropped.

## Round-2 Revision Notes

Every round-2 finding is adopted. Two of them ship with a **different remedy** than the critique proposed, and one NIT was adopted as a deletion rather than an addition. Recorded here so a reviewer can check the substitution rather than discover it in the diff.

**1. Blocker 1's `reconfirm_serial` bound: adopted the finding, changed the remedy.** The critique proposed `if len(ordered) > MAX_RECONFIRM_NODES: log(...); return [], []`. Returning an empty confirmed set would manufacture a green night out of a very red one — the exact bug class this plan exists to close — and it would also deadlock the detector: with no confirmed set there is nothing to seed, so a machine whose non-unit tiers are genuinely 250-red would re-trip every night and never write a baseline. The bound ships as `return ordered, []` — since round 3 widened the signature, that is `return ordered, [], True` — which is what the existing 900 s timeout fail-safe already produces, reached in a second instead of fifteen minutes. The blast radius is bounded downstream by `MAX_DISPATCH_NODES` instead, and the error-rate ceiling stops the storm before either is reached. The critique's own warning that `compute_dispatch_set` is not a bound is adopted verbatim.

**2. Blocker 2's skip detection: adopted the finding, inverted the marker.** The critique proposed classifying a skip by matching the shell script's `"Skipping nightly-tests install"` text. Matching the **success** line instead — `Nightly regression test service installed successfully.` at `install_nightly_tests.sh:107` — fails closed rather than open: the worktree refusal, the role-gate skip, and any early exit added later all read as "skipped" rather than silently as "installed". Under the critique's form, a future early-exit path would report a false success, which is the same defect the finding is about. Everything else in the remedy is adopted as written, including the warning against a distinct non-zero exit code (`service.py`'s `run_cmd` is `check=False`, so it would append a spurious "install failed").

**3. Concern 2's new Success Criterion: adopted, made satisfiable.** "One widened run has completed with `summary.total > 0`, and its wall time is recorded in the timeout comment" couples two things that can fail independently: the collection being executable at all, and a *representative* wall time. On a contended machine the second may never be obtainable. It ships as two criteria — one completed widened run at any worker count, and a timeout comment that honestly labels itself measurement or bound — with an explicit stop-and-report clause if no worker count completes. The `-n 6` move is adopted verbatim. **`TEST_DB_CLAIM_WAIT_S=600` did not survive round 3** — it sits above `pyproject.toml:196`'s per-item `--timeout=420`, so it can never elapse; it ships as `300`. See the round-3 blocker.

**4. NIT 1 (`--paths`) is adopted as a removal.** The flag is gone rather than defended. That deletes the argparse entry, `paths_are_default`, both suppression branches, the override log line, two Failure-Path bullets, two Test Impact entries, one Success Criterion, and the `_run_main` `sys.argv` change that rippled through seven inherited test methods. `COLLECTION_PATHS` remains as a module constant because Task D needs it for the state file's `collection` field regardless.

**Nothing is left unresolved.** No finding was judged wrong and dropped.

---

## Resolved Questions

The plan is settled. Each question the draft raised now carries a decision the builder can act on without a check-in.

1. **`tests/e2e/` stays inside the nightly collection.** It is 129 tests and part of the default collection; carving it out would reopen the exact gap this plan closes, and "the detector collects what a bare `scripts/pytest-clean.sh` collects" is the one property that makes "the default collection is red" and "the detector is red" the same statement. The API-spend and third-party-flakiness cost is real and is absorbed: 129 tests once a night, with xdist artifacts already filtered by the serial re-confirmation gate. Revisit as a separate issue if the spend shows up.

2. **Fleet membership is not a blocker, but it is no longer treated as value-neutral.** Recon can see only `Tom's MacBook Air`, and this machine's `projects.json` is stale as a fleet inventory. Legs 1, 2, 3 and 5 are machine-independent, so the plan's value stands whatever the answer. Risk 7 now carries the part that *does* depend on the answer — duplicate filing scales with the number of covered machines — and Task C's deterministic-title dedup is sized for an unknown fleet rather than a known one.

3. **The deep shrink blocks; only the shallow band warns. Round 6 reversed this, and the reversal is the point.** This question originally ruled `total < 0.9 * prev_total` a warning, because a legitimate PR deleting a large test file would trip it and suppress a real night. Round 6 established that the disposition rested on a wrong failure model: test-DB starvation was believed to produce per-test `error` outcomes catchable by the error ceiling, and since #2628 it produces **no outcomes at all** (`tests/conftest.py::pytest_configure` aborts the session at `:287`). The shrink comparison is therefore not a redundant nicety — it is the *only* check that can see partial starvation. So `total < 0.9 * prev_total` within the same collection now **blocks**, as does `total < 0.9 * MIN_EXPECTED_COLLECTED` before a same-collection baseline exists. The two failure directions are not symmetric: a deletion costs one loud night with a one-command remedy, while a silently truncated night has no remedy because nobody knows it happened. The shallower band `prev_total > total >= 0.9 * prev_total` keeps the warning, and keeps its round-2 job — a trusted truncated night silently strips already-filed nodes out of `dispatched_nodes` via `carry_dispatched_nodes`, so the warning switches `main()` to a union-preserving state write. Blocking is done by `report is None`, `total == 0`, the exit-code list, the coverage floor, and the fixture-error ceiling.

4. **Build does not schedule an integration-tier failure-count measurement, but it must complete one widened run.** Four recon attempts at counting the tier's red nodes were defeated by test-DB slot exhaustion, and Task D makes night one safe regardless — the collection mismatch forces a baseline seed that dispatches nothing whether the tier is clean or 200-red. **The count arrives on night ONE, not night two.** An earlier revision claimed night two's first dispatch set would reveal it; that is false by construction. The seed path sets `just_dispatched = list(confirmed_failing)` and persists the whole confirmed set as both `dispatched_nodes` and `failing_tests`, so night two has `compute_dispatch_set` subtract the entire seed and `compute_new_failures` subtract it again — the dispatch set is empty. The number is visible in night one's own baseline Telegram (`main()` already sends "baseline established: N tests, M confirmed failures") and in the `Confirmed serial failures:` log line. Distinct from that: the build **does** have to get the widened collection to execute once with `summary.total > 0`, at whatever worker count achieves it, because a detector that has never run is not a shipped detector. The timeout constant still has its deterministic fallback bound for the case where the completing run is not a representative one.

<!-- The first draft's Open Question 4 asked whether Task E could be deferred.
     spike-7 answered it with evidence: the wrapper's stall watchdog fired at
     637s on a wedged run that a bare invocation would have let burn for the
     full timeout. Task E ships in this plan. -->

