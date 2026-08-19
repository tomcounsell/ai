# Nightly Regression Tests

Automated nightly safety net for the default test collection (`tests/` — the
same set a bare `scripts/pytest-clean.sh` collects). A launchd job runs the
collection each night at 03:00, verifies the run actually executed before
trusting its result, compares failure counts against the prior run, runs a
post-run TTFT (time-to-first-token) regression gate, and sends Telegram
alerts only when new failures, collection errors, or a cold-start latency
regression appear.

## Status

Shipped (issue #972); TTFT regression gate added (issue #1227); run lock,
best-effort failure summarizer, and triage-session dispatch added (issue #2192
Scope 1) — see `docs/features/nightly-alert-triage.md` for those three
additions; collection widened from `tests/unit/` to the default collection,
run-integrity guard, collection-aware baseline, and worker-role install gate
added (issue #2823).

## What It Does

- Acquires an advisory run lock (`data/nightly_tests.lock`) before doing anything
  else; a second overlapping invocation logs the collision and exits 0 with no test
  run and no alert — see `docs/features/nightly-alert-triage.md#run-lock-race-1`
- Runs the default collection (`COLLECTION_PATHS = ["tests/"]`) through
  `scripts/pytest-clean.sh -n {NIGHTLY_XDIST_WORKERS}` (default 6, env-overridable)
  nightly at 03:00 local time, with a 300s test-DB claim window sized for an
  unattended run
- Validates run integrity before trusting the result (`validate_run_integrity`):
  a missing/corrupt report, a signal-death or usage-error exit code, a
  fixture-error storm, or — the case that matters most — a **coverage floor**
  on `total` catches test-DB starvation, which produces zero `error` outcomes
  and a legal exit code and would otherwise read as a clean night. A tripped
  guard alerts loudly through `_fatal()`, writes no state, and dispatches
  nothing
- Compares the confirmed-failing set against the previous run's
  `data/nightly_tests_last_run.json`, but only when both runs share the same
  `collection`. A run whose recorded collection differs (the widening night,
  or any future change of scope) re-baselines instead: it seeds
  `dispatched_nodes` with the whole currently-failing population and
  dispatches **one** umbrella triage session, rather than re-opening the
  #2429/#2430/#2462 per-node duplicate-filing churn
- Runs the TTFT regression gate as a post-test check (see below)
- Sends Telegram alerts to "Eng: Valor" when:
  - A newly-confirmed failure appears or collection errors occur — the
    new-failures alert text is a best-effort LLM summary with a raw node-ID
    fallback (see `docs/features/nightly-alert-triage.md`)
  - The TTFT gate detects a cold-start latency regression
- On newly-confirmed failures, fires a deduped, fire-and-forget Eng-session dispatch
  with literal, Python-computed issue titles (`Nightly regression: {node}`) to
  investigate and file a GitHub issue — see
  `docs/features/nightly-alert-triage.md#triage-session-dispatch`. Capped at
  `MAX_DISPATCH_NODES` (10) per run; the remainder retries on a later run
- Clean runs produce no noise

## Alert Conditions

| Condition | Message |
|-----------|---------|
| First run or a collection change (re-baseline) | `Nightly regression baseline established[ (re-baseline: prior population absorbed)]: {total} tests, {failed} confirmed failures.` |
| Run-integrity guard tripped | `Nightly tests could not run: {reason}` via `_fatal()` — no state written, no dispatch |
| Newly-confirmed failure | Best-effort LLM summary of the confirmed failures (falls back to a raw node-ID preview on any summarizer failure), plus a `[triage session: <id>]` suffix when a triage session was dispatched — see `docs/features/nightly-alert-triage.md` |
| Collection errors, no newly-confirmed failures | `Nightly tests: collection error ({new_errors} errors). Run: pytest tests/ -n {NIGHTLY_XDIST_WORKERS}` |
| TTFT regression | `TTFT regression (issue #1227): {detail}` |
| Lock collision (overlapping run) | Silent — no Telegram message, no test run |
| Clean run (no newly-confirmed failures, no errors, no TTFT regression) | Silent — no Telegram message |

## TTFT Regression Gate (issue #1227)

After the unit suite runs, a post-run gate reads `logs/cold_start_metrics.jsonl`
and compares the last `TTFT_LAST_N` (10) PM-session cold starts against
`TTFT_THRESHOLD_SECONDS` (120s — production target is 90s; the nightly
threshold allows slack for run-to-run noise). A regression is reported as a
Telegram alert without changing the script's exit code. The gate never
crashes the run: a missing log file, a parse failure, or any other exception
is swallowed and logged as non-fatal.

## Files

| File | Purpose |
|------|---------|
| `scripts/nightly_regression_tests.py` | Main script: acquires the run lock, runs the default collection through `scripts/pytest-clean.sh`, validates run integrity, computes the failure delta or re-baselines on a collection change, summarizes/dispatches on new failures, runs the TTFT gate, sends Telegram alerts, saves state |
| `com.valor.nightly-tests.plist` | launchd plist template with `__PROJECT_DIR__`, `__HOME_DIR__`, `__SERVICE_LABEL__` placeholders |
| `scripts/install_nightly_tests.sh` | Install script: refuses to install from a lane worktree, worker-role gated (`has_worker_role()` — any machine owning a project, Telegram-independent), substitutes placeholders, calls `launchctl_bootstrap_fail_soft` (fail-soft errno-5 recovery via `scripts/lib/launchctl.sh`, see bridge-self-healing.md Component 21); skips + removes stale plist on a machine owning no project |
| `data/nightly_tests.lock` | Advisory `flock` lock file preventing overlapping runs (gitignored) — see `docs/features/nightly-alert-triage.md` |
| `data/nightly_tests_last_run.json` | Delta state: `passed`, `failed`, `error`, `skipped`, `total`, `run_at`, `collection`, `head_commit`, `dispatched_nodes`, `dispatched_session_id`, and — on a re-baseline night only — `seed_collection`, `seed_size`, `seeded_nodes`, `min_expected_collected` (gitignored) |
| `logs/nightly_tests.log` | Per-run log with timestamps and counts |
| `logs/nightly_tests_error.log` | Startup crash log (captured by launchd before `log()` fires) |
| `logs/cold_start_metrics.jsonl` | TTFT samples consumed by the gate |

## Design Decisions

**JSON report over text parsing** — `--json-report` gives structured summary data without
fragile regex against pytest's output format.

**Local JSON state, not Redis** — Two fields (`failed`, `run_at`) don't justify a Redis
dependency. Matches the `sdlc_reflection_last_run.json` and `autoexperiment_last_run.json`
patterns.

**Best-effort Telegram** — `send_telegram()` never crashes the script. If `valor-telegram`
is missing or the send fails, it logs a warning and continues. The test results are still
saved.

**Confirmed-failing-set delta, not a scalar delta** — The state file persists the
confirmed failing node-ID **set**, not just a count, so a shifting flaky set
(same count, different tests) never reads as a regression and a genuinely new
failure does, even when the total count is flat.

**Worker-role gating, not bridge-role** — Running the test suite requires a
checkout and a worker, not a Telegram bridge. `install_nightly_tests.sh`
includes a `has_worker_role()` function (the same fix issue #1379 applied to
`install_reflection_worker.sh`) that qualifies any machine owning at least one
project, regardless of whether that project has Telegram configured, and
removes any stale plist on a machine owning none. A worktree refusal runs
first: the plist is machine-global and hardcodes an absolute `PROJECT_DIR`, so
installing from a lane worktree would aim the fleet's detector at a directory
merge cleanup deletes.

**Run-integrity guard, not returncode-only trust** — A run that could not
execute (test-DB slot exhaustion, a wedged xdist controller) was measured,
reproducibly, to exit 0 with zero tests collected. `validate_run_integrity()`
classifies a completed run before anything downstream (dispatch, state
persistence) trusts it; a tripped guard alerts through `_fatal()` and neither
persists state nor dispatches.

**Collection-aware baseline, not a bare first-run flag** — The persisted state
records which `collection` produced it. Widening the collection (or any future
change of scope) is treated as a fresh baseline: the whole currently-failing
population is seeded into `dispatched_nodes` and escalated as one umbrella
triage session, so it is never re-filed node-by-node the next time the
detector runs — the #2429/#2430/#2462 duplicate-filing trap this design
protects against by construction.

## Installation

Nightly tests are installed automatically by `/update` on any machine that
owns at least one project (worker-role, not bridge-role):

```bash
/update  # or: python scripts/update/run.py --full
```

For manual install:

```bash
./scripts/install_nightly_tests.sh
```

Prerequisite: `pytest-json-report>=1.5` must be installed (`uv sync --extra dev` or
`uv pip install pytest-json-report`). The install script performs a hard preflight check.

Verify installation:

```bash
launchctl list | grep nightly-tests
```

## Manual Testing

```bash
# Dry-run: runs tests, prints what Telegram message would be sent, saves state
python scripts/nightly_regression_tests.py --dry-run

# Stream live output
tail -f logs/nightly_tests.log
```

## Uninstall

```bash
launchctl bootout gui/$(id -u)/com.valor.nightly-tests
rm ~/Library/LaunchAgents/com.valor.nightly-tests.plist
```

## Dependencies

- `pytest-json-report>=1.5` (declared in `pyproject.toml` `[project.optional-dependencies].dev`)
- `pytest-xdist` (already present — used for `-n auto` parallelism in the unit suite)
- `valor-telegram` on PATH (best-effort — not required)

## See Also

- `docs/features/nightly-alert-triage.md` — the run lock, best-effort LLM summarizer,
  and triage-session dispatch layered around this base detector (issue #2192 Scope 1)
