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
| `scripts/install_nightly_tests.sh` | Install script: refuses to install from a lane worktree, fails closed when the projects config is unreadable or the host role is undeterminable, worker-role gated (`has_worker_role()` — any machine owning a project, Telegram-independent), substitutes placeholders, calls `launchctl_bootstrap_fail_soft` (fail-soft errno-5 recovery via `scripts/lib/launchctl.sh`, see bridge-self-healing.md Component 21); skips + removes stale plist on a machine owning no project |
| `data/nightly_tests.lock` | Advisory `flock` lock file preventing overlapping runs (gitignored) — see `docs/features/nightly-alert-triage.md` |
| `data/nightly_tests_last_run.json` | Delta state: `passed`, `failed`, `error`, `skipped`, `total`, `run_at`, `collection`, `head_commit`, `dispatched_nodes`, `dispatched_session_id`, `seeded_nodes` (carried forward on every run), and — on a re-baseline night only — `seed_collection`, `seed_size`, `min_expected_collected` (gitignored) |
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
protects against by construction. *Repairing* the seeded population is a
separate lane (the #2852 model, "get `main`'s unit suite to zero"); this
detector's job is only ever to *book and escalate* it once, not to fix it.

**Seed suppression is sticky, because a flapping node would otherwise be
re-filed** — The seed files exactly one umbrella issue (`Nightly regression
baseline: ...`), while per-node dedup keys on a different title (`Nightly
regression: <nodeid>`). The two are separate title namespaces, so a seeded
node has no per-node issue to find. Since `carry_dispatched_nodes()` drops a
node from `dispatched_nodes` as soon as it passes, a seeded node that *flaps*
— passes one night, fails the next — would look unfiled, dispatch against a
title that never existed, and open a fresh issue on every flap. That is
#2429/#2430/#2462 rebuilt through a different door.

So `seeded_nodes` is persisted on **every** run (not just the seed night) and
`compute_dispatch_set()` subtracts it permanently.

Be precise about the cost, because the short version overstates the safety. A
seeded node that regresses is **alerted once, then never filed**.
`compute_new_failures()` keys on `failing_tests` rather than dispatch state,
so the night it re-fails does produce a Telegram alert — but from the next
night it is no longer "new", so no further alert fires and no issue is ever
opened. For a non-seeded node the durable record is a GitHub issue; for a
seeded node it is one line in a chat log, and the umbrella may since have been
closed. `seeded_nodes` also never retires entries, unlike `dispatched_nodes`,
so a renamed or deleted test stays in it indefinitely. Both costs are accepted
deliberately: the alternative is a duplicate issue on every flap of a
population known to flap, and closing the repair lane is what makes this
suppression stop mattering. The population this matters most
for is the `.pyc`-sensitive working-tree guards (#2807, #2808, #2809), which
are red or green depending on which interpreter last touched `__pycache__`
and are therefore both the likeliest members of night one's seed and the
likeliest to flap. Fixing those three also defuses this interaction.

**A failed seed dispatch writes no baseline** — If the umbrella dispatch
fails, `main()` routes through `_fatal()` and skips `save_last_run()`
entirely. Recording the seed anyway would mark every absorbed node as filed
while no umbrella issue existed, so `compute_dispatch_set()` would suppress
the whole night-one population forever — behind a Telegram message that reads
like a successful baseline. Refusing to persist means the next run sees no
prior state, re-seeds, and retries.

**Starvation presents as missing tests, never as errors** — Since #2628,
`claim_test_db()` polls inside `tests/conftest.py::pytest_configure`, before
collection and before any test item exists; a worker that cannot claim a slot
aborts its whole session there and contributes zero test items and zero
`error` outcomes — it dies as "node down: Not properly terminated" rather
than failing individual tests. That is why the coverage floor, not the error
ceiling, is what catches it: an error-keyed check would read a starved run as
"nothing errored" and trust a report that silently ran a fraction of the
suite.

**`dispatched_nodes` is per-machine state, and cross-machine dedup is a
convention, not an enforced invariant** — Each machine's `dispatched_nodes`
lives in its own `data/nightly_tests_last_run.json`; two machines with the
same red node both dispatch unless something else prevents it. The only thing
that does is the literal issue title (`Nightly regression: <nodeid>`), which
the detector emits verbatim and a triage session is instructed to search for
before filing — nothing verifies that an issue was actually filed under that
exact title, so a future change to the title format silently reopens
#2429/#2430/#2462 across the fleet. `MAX_DISPATCH_NODES` (10) bounds the
blast radius of any single run's dispatch, not the cross-machine duplication
risk; a shared, Redis-backed dispatch set is the real fix and is deliberately
deferred.

**`NIGHTLY_XDIST_WORKERS` is env-overridable, not a pinned literal** —
Default `6`, derived from this machine's 15 test-DB slots
(`tests/db_claim.py`) with headroom left for sibling lanes. `-n 4` is the
practical floor: below it the run cannot complete inside
`PYTEST_TIMEOUT_SECONDS` (scaling the ~1260s unit-tier baseline by the ~1.1x
collection-widening growth and inversely by worker count puts `-n 2` at
~6900s and `-n 1` at ~13,900s, both well past the ceiling). A machine with a
different core count or slot pressure overrides the env var; the code and its
tests never pin the literal `"6"`.

## Update-Time Staleness Warning

`/update` warns — only on the leg where the service is confirmed
`"installed"` — when `now - max(plist_mtime, run_at) >= 2 days`. `plist_mtime`
is the mtime of `~/Library/LaunchAgents/com.valor.nightly-tests.plist`;
`run_at` is read from `data/nightly_tests_last_run.json`, and an absent or
malformed value falls back to the plist mtime rather than "unknown, warn" —
so the very `/update` that installs the detector never warns about a run that
hasn't happened yet. This is the only check in the system that observes the
*absence* of a run: a booted-out plist, a machine asleep at 03:00, and the
run lock's silent collision-exit are all otherwise indistinguishable from a
clean night.

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
# Dry-run: runs the suite and prints what would be sent, changing nothing.
# No Telegram send, no Eng session spawned, no GitHub issue filed, and NO
# state written. The state write is suppressed deliberately: dispatch
# short-circuits to a truthy sentinel so the success path runs realistically,
# and on a seed night that path records `seeded_nodes` — which is sticky, so
# persisting it would permanently suppress the whole absorbed population
# against an umbrella issue that was never filed.
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
