# Merge-Gate Baseline

The merge-gate baseline (`data/main_test_baseline.json`) tracks the pre-existing
failing tests on `main` so that `/do-merge`'s **Full Suite Gate** can distinguish
a genuine new regression from a test that was already failing. Schema v2 adds
categorisation (`real`, `flaky`, `hung`, `import_error`) and a reproducible
refresh tool. Issue: [#1084](https://github.com/tomcounsell/ai/issues/1084).

## Why

Before schema v2, the gate stored a flat sorted list of failing node IDs and
compared it to the PR's failing list using `comm -23`. When a PR happened to
introduce a regression that produced the *same number* of failures as a flaky
pre-existing test, the diff showed "0 new regressions" and the gate waved the
regression through (observed on PRs #1054 and #1070 in April 2026). The flat
list also did not distinguish deterministic failures (which SHOULD block future
PRs) from known-flaky tests (which should not).

## How it differs from the PR-branch flaky filter (#476)

PR #484 ([feature doc](test-reliability-flaky-filter.md), issue
[#476](https://github.com/tomcounsell/ai/issues/476)) introduced a retry-based
flaky filter at the `/do-test` layer. That filter runs on the **PR branch**
before `/do-merge` is invoked, retrying individual failures and dropping the
ones that pass on retry. The merge-gate baseline runs on **`main`** and
records which tests are *expected to fail* there. The two layers are
independent:

- `/do-test` filter: "re-run PR failures; ignore the ones that pass on retry".
- Merge-gate baseline: "which main failures are known and which are new?".

A test that is flaky on `main` will be in the baseline as `flaky` and will
not block merges when the PR happens to trip it; a test that is flaky only on
the PR branch will be handled by `/do-test`'s retry before the merge gate
sees it.

## Architecture

```
Developer on main                         /do-merge on PR branch
       |                                          |
       v                                          v
 refresh_test_baseline.py               pytest tests/ --junitxml=/tmp/pr_run.xml
  -> N pytest runs with                        |
     pytest-timeout enabled                    v
  -> aggregate outcomes              scripts/baseline_gate.py
  -> classify (precedence):           --pr-junitxml ... --baseline ...
     import_error > hung > real              |
     > flaky                                 v
  -> write schema v2              JSON verdict + staleness warning
     data/main_test_baseline.json             |
                                              v
                                      exit 0 -> PASS
                                      exit 1 -> GATES_FAILED
```

## Schema v2

`data/main_test_baseline.json` is keyed by test node ID:

```json
{
  "schema_version": 2,
  "generated_at": "2026-04-24T12:00:00+00:00",
  "generated_by": "python scripts/refresh_test_baseline.py --runs 3",
  "runs": 3,
  "commit": "bc23403c",
  "tests": {
    "tests/unit/test_intake_classifier.py::TestRealHaikuClassification::test_foo": {
      "category": "flaky",
      "fail_rate": 0.33,
      "hung_count": 0,
      "note": "LLM-as-judge — see issue #1084"
    },
    "tests/unit/test_reflection.py::test_async_return": {
      "category": "real",
      "fail_rate": 1.0,
      "hung_count": 0
    }
  }
}
```

### Top-level fields

| Field | Consumer |
|-------|----------|
| `schema_version` | loader format detection (`2` = current) |
| `generated_at` | 14-day staleness warning |
| `generated_by` | human debugging of drift |
| `runs` | classifier's `fail_rate` denominator |
| `commit` | reader confirmation; `-dirty` suffix marks an irreproducible baseline (always warns) |
| `bootstrap` *(optional)* | `true` when the file came from `/do-merge`'s fallback path; always warns |
| `tests` | map of node ID to per-test record |

### Per-test record

| Field | Required | Purpose |
|-------|----------|---------|
| `category` | yes | one of `real`, `flaky`, `hung`, `import_error` |
| `fail_rate` | yes | `(fail_count + hung_count) / runs` across the N runs |
| `hung_count` | yes | number of timeouts observed (informational) |
| `note` | no | free-text annotation preserved across `--merge` refreshes |

## Categories

| Category | Meaning | Gate behaviour |
|----------|---------|----------------|
| `real` | Deterministic failure on main (100% fail rate, no timeouts, no collection error) | PR re-occurrence allowed; PR NEW is blocked |
| `flaky` | 1-99% fail rate across N baseline runs | PR re-occurrence allowed; PR NEW is blocked |
| `hung` | pytest-timeout fired at least once for this test | PR re-occurrence allowed; PR NEW is blocked |
| `import_error` | pytest collection error (module won't load) | PR re-occurrence allowed; PR NEW is blocked |

Re-occurrences in **any** baseline category are allowed; only PR failures whose
node ID is NOT in the baseline block. The `flaky` bucket is called out
separately in the gate's reporting (`new_flaky_occurrences`) to make the
difference between "pre-existing flake flipped again" and "genuinely broken
again on main" visible in the PR comment.

### Classifier precedence

The classifier applies these rules in order; **first match wins**:

1. Any collection error across N runs → `import_error`
2. Any `pytest-timeout` failure across N runs → `hung`
3. 100% non-pass across N runs (no timeouts, no collection errors) → `real`
4. 1-99% non-pass (no timeouts, no collection errors) → `flaky`

A test with 2 fails + 1 timeout classifies as `hung`, not `flaky`, because a
hang is a different failure mode with a different fix surface.

## `pytest-timeout` scoping rule

The refresh tool relies on the `pytest-timeout` plugin to produce a per-test
`<failure message="Failed: Timeout >60.0s">` entry in junitxml — the
deterministic signature the classifier keys off of. Two critical rules:

1. `pytest-timeout` is added as a **dev dep** (`pyproject.toml` under the `dev`
   extra) and locked in `uv.lock`. It is installed everywhere pytest is.
2. `pytest-timeout` is **NOT** registered in `[tool.pytest.ini_options].addopts`
   and is **NOT** in the default pytest addopts. The plugin only activates when
   `refresh_test_baseline.py` invokes pytest with
   `-p pytest_timeout --timeout=60`. Regular pytest runs (`/do-test`, CI, local
   dev, the merge gate's PR-branch run) are unaffected.

The classifier matches on the exact prefix `"Failed: Timeout >"`, not the loose
substring `"Timeout"`. A regular assertion failure whose message happens to
contain the word "Timeout" (e.g. `assert response != "Timeout"`) still
classifies as `real`/`flaky`, not `hung`.

## Refresh tool

`scripts/refresh_test_baseline.py` regenerates the baseline from N pytest runs
on the current checkout (intended for a clean `main` checkout).

```
python scripts/refresh_test_baseline.py                # 3 runs, 60s per test
python scripts/refresh_test_baseline.py --runs 5       # broader sample
python scripts/refresh_test_baseline.py --dry-run      # print to stdout
python scripts/refresh_test_baseline.py --merge        # preserve note fields
python scripts/refresh_test_baseline.py --test-timeout 120
```

### Arguments

| Flag | Default | Effect |
|------|---------|--------|
| `--runs N` | 3 | Number of pytest invocations to aggregate. Minimum needed to distinguish 1-of-3 flakiness from 3-of-3 determinism. |
| `--output PATH` | `data/main_test_baseline.json` (in dry-run: `-` for stdout) | Where to write the baseline. In `--dry-run` mode the default is `-` so accidentally dropping the flag does NOT silently overwrite the live baseline. |
| `--test-timeout N` | 60 | Per-test timeout in seconds, passed to `pytest-timeout`. |
| `--global-timeout N` | `test_timeout × 3 × estimated_test_count`, capped at 7200s | Outer wall-clock safety net per pytest invocation, catches tests that wedge in a C extension and ignore `pytest-timeout`'s thread signal (Risk R5 below). |
| `--merge` | off | Preserve the `note` field of every existing record when writing. |
| `--dry-run` | off | Print classification summary; do not write the file. |
| `--verbose` | off | Log each pytest invocation's command line. |

### Per-run isolation

Each pytest invocation writes junitxml into a fresh per-run
`tempfile.TemporaryDirectory()`. This guarantees that a truncated junitxml
from an interrupted previous run (SIGTERM, `Ctrl-C`, OOM kill) cannot leak
into the next run's parse step. The aggregator wraps `ET.parse()` in
`try/except xml.etree.ElementTree.ParseError` (never a bare `except`); on
`ParseError` that run is discarded with a warning and the remaining runs
proceed. If all N runs produce `ParseError` or outer-timeout, the tool exits
non-zero without writing.

### Nameless-`<testcase>` resilience (issue #1853)

xdist/execnet worker crashes can occasionally emit a `<testcase>` element
with no `name` attribute. Before this fix, `parse_junitxml` raised
`JunitxmlParseError` on that single element and the caller discarded the
**entire run** — on 2026-07-02 this silently degraded a 3-run refresh to a
1-run baseline (`runs: 1`), which misclassifies every transient flake as
`real` (there's no majority to compare against with only one observation).

`parse_junitxml` now handles a nameless `<testcase>` per-element instead of
per-run:

- If it has an `<error>` child, it's a genuine collection error — classify
  it as `collection_error` under a best-effort node id (`classname` if
  present, else a synthetic `<unknown>::<index>` placeholder).
- Otherwise, skip just that one element and keep parsing the rest of the
  run normally.

The whole run is only discarded for a true `ParseError` (truncated/malformed
XML) or a `FileNotFoundError`, never for one structurally-odd testcase.

### Loud failure below 2 usable runs

Flaky classification requires a majority across runs — with 0 or 1 usable
runs there's no way to distinguish a flake from a deterministic failure.
`refresh_test_baseline.py` now refuses to let a degraded run count pass
silently: when the count of usable/surviving runs is below
`MIN_USABLE_RUNS_FOR_FLAKY_DETECTION` (2), it appends a
`WARNING: only N usable run(s) -- flaky classification unavailable` line to
the summary output (stdout in normal mode, stderr in `--dry-run`) **and**
exits non-zero, even though it still writes the (degraded) baseline it was
able to build. A CI/cron job that checks the exit code will no longer
mistake a flaky-blind single-run baseline for a healthy multi-run one.

### Dirty-tree capture

The refresh tool captures `git rev-parse --short HEAD` plus a `-dirty` suffix
if either `git diff --quiet` OR `git diff --cached --quiet` exits non-zero.
A dirty-tree baseline is inherently irreproducible — two machines checked out
at the same SHA with different uncommitted changes would produce different
classifications. The tool does not refuse to write, but the `-dirty` marker
flags the baseline as non-authoritative. The merge gate's staleness warning
treats a `-dirty` commit the same way it treats `bootstrap: true`: always
warn.

## Merge-gate comparison

`scripts/baseline_gate.py` implements the comparison logic as pure functions
importable from unit tests (`load_baseline`, `parse_pr_failures`,
`compute_gate_verdict`, `format_staleness_warning`). `.claude/commands/do-merge.md`
invokes it by shelling out to `python -m scripts.baseline_gate`; the markdown
does not embed any Python as a heredoc (extracted to the script to keep
every line of gate logic reachable from
`tests/unit/test_do_merge_baseline.py`).

On each merge attempt the script emits a JSON verdict to stdout:

```json
{
  "new_blocking_regressions": ["tests/unit/test_foo.py::test_new"],
  "new_flaky_occurrences": ["tests/unit/test_flaky.py::test_a"],
  "preexisting_failures_present": 42,
  "preexisting_failures": ["..."],
  "baseline_keys_no_longer_failing": ["..."]
}
```

Exit code is `0` when `new_blocking_regressions` is empty and `1` otherwise.

### Staleness warning

The gate prints a non-blocking `WARNING` line to stderr when any of these is
true:

- `generated_at` is more than 14 days old
- `bootstrap: true` is set at the top level
- `commit` ends with `-dirty`

The warning suggests running `python scripts/refresh_test_baseline.py`.

### Staleness decision (issue #1933)

The baseline drifted to ~60 days stale before PR #1930, producing 40 false
regression flags at that PR's merge gate. The resolution keeps the existing
soft-warn and adds a lightweight detector, but deliberately does **not**
change how staleness is enforced:

- **Soft-warn kept, no hard-block.** `data/main_test_baseline.json` is
  per-machine (see Data ownership below); hard-blocking every merge on a
  machine because a local file is old would be strictly worse than a
  warning — it would halt all merges until someone runs a multi-minute
  3× full-suite refresh. The existing 14-day soft-warn already fired and
  was actionable at PR #1930 (a manual re-classification pass, not a missed
  regression).
- **No scheduled full-suite auto-regeneration.** A weekly 3×-full-suite
  reflection running on a live worker machine would reintroduce the
  Redis-collision / memory-thrash hazard that parallel full-suite runs are
  already known to cause on this project. Rejected as a fix.
- **New: a cheap age-only detector reflection.** `reflections/housekeeping/test_baseline_refresh_check.py`
  reads `data/main_test_baseline.json`'s `generated_at` and compares it
  against `STALENESS_THRESHOLD` (imported from `scripts/baseline_gate.py` —
  single source of truth). It runs **no tests** — only reads a small JSON
  file — so it carries none of the full-suite hazard. It turns silent
  60-day drift into a visible weekly nudge; the operator then runs the
  (now #1853-corrected) `refresh_test_baseline.py` manually once the
  machine is quiescent.
- **`_baseline_post_merge_update.py` intentionally does not refresh
  `generated_at`.** Its decay logic ages out stale `real` entries after
  repeated non-occurrence on merged PRs, but decaying entries is not the
  same as re-observing `main` — so `generated_at` staying untouched is
  correct, and periodic operator-run regeneration remains necessary. The
  new detector reflection is what surfaces that need instead of relying on
  someone to notice.

#### Deploying the detector reflection

The reflection module ships in this repo, but only runs once registered in
the per-machine, gitignored, iCloud-synced `~/Desktop/Valor/reflections.yaml`
(see `docs/features/reflections.md` for the registry format). Add an entry
like:

```yaml
- name: test-baseline-refresh-check
  description: "Warn when data/main_test_baseline.json is older than the merge-gate's staleness threshold"
  every: 604800s   # weekly
  priority: low
  execution_type: function
  callable: "reflections.housekeeping.test_baseline_refresh_check.run"
  enabled: true
  output_sink: log_only
```

No `scripts/update/run.py` change is required — the YAML scheduler picks up
the callable via `importlib` once the entry exists. Since the reflection
scheduler subprocess (`python -m reflections`, `com.valor.reflection-worker`)
is itself only installed on worker/bridge machines, no separate role gate is
needed on the entry.

### Bootstrap path

If `data/main_test_baseline.json` is missing on a PR with failures, the gate
writes a schema-v2 baseline from the PR's failing set with every entry marked
`category: real` and `bootstrap: true` at the top level. The merge proceeds
(the first post-red-main merge must not be blocked purely on "no baseline
exists"). The `bootstrap: true` flag makes every subsequent gate invocation
warn until `refresh_test_baseline.py` writes a properly categorised baseline.

### Post-merge reset

After a clean merge (all tests passing), `/do-merge` writes:

```json
{
  "schema_version": 2,
  "generated_at": "<iso-utc>",
  "generated_by": "do-merge.md post-merge reset",
  "runs": 0,
  "commit": "<sha>",
  "tests": {}
}
```

…so future PRs are held to a fully green standard.

### Backwards-compat migration

The gate auto-promotes the schema-v1 flat shape
(`{"failing_tests": [...]}`) in memory: every entry becomes
`{"category": "real", "fail_rate": 1.0, "hung_count": 0}`. No on-disk write
happens from the gate. Only `refresh_test_baseline.py` upgrades the file
format. A developer who never runs the refresh tool still has a working gate;
it just cannot distinguish `real` from `flaky`.

## Data ownership

`data/main_test_baseline.json` is git-ignored (`.gitignore:181`) and
**per-machine**. Each developer/CI worker maintains their own copy. This is
deliberate: test failures on main depend on the environment (Python version,
installed packages, OS, network) and a shared baseline would mask legitimate
per-machine differences. Centralising the baseline in git is explicitly
out-of-scope.

## Risks

### R1: Low-rate flakes misclassified as `real`

A test that fails 30% of the time could fail all 3 baseline runs (~2.7%
probability) and classify as `real`. A PR that causes it to pass would be
released unblocked.

Mitigation: ship with `--runs 3` default; expose `--runs` as configurable.
Misclassification fails *closed* (block merges that shouldn't block) rather
than open (allow regressions) — the safer direction.

### R3: Genuinely regressed test that is also baseline-flaky

A test that is legitimately flaky AND the PR genuinely regresses will be
allowed because the baseline has it as `flaky`. This is a known limitation of
any fail-rate-based classifier. Recommended practice: quarantine recurrently
confusing tests with `@pytest.mark.flaky` or explicit `skip` rather than
leaving them in the baseline.

### R5: `pytest-timeout` thread method fails on C-extension wedges

`pytest-timeout`'s thread method raises an async exception in the test
thread. Tests blocked in a C extension ignoring the signal can wedge
indefinitely, and no `<failure>` entry is emitted for that test — it appears
as "did not run".

Mitigation: the outer `--global-timeout` wall-clock wrapper catches the whole
pytest invocation. When it fires, the refresh tool discards that run's
junitxml and logs a warning. If all N runs hit the outer timeout, the tool
exits non-zero without writing — inconclusive but safe.

### R6: `pytest-timeout` side effects on regular pytest runs

`pytest-timeout` has no default import-time registration; it hooks in only
via `-p pytest_timeout` or explicit addopts. Verified by running
`pytest tests/unit/` before and after adding the dep; runtimes are within
the 5% noise threshold.

## Tests

- `tests/unit/test_refresh_test_baseline.py` — junitxml parsing (including
  truncated-file safety, and the #1853 nameless-`<testcase>` classify-or-skip
  paths), classifier precedence, exact-prefix timeout match vs. loose
  substring, dirty-tree commit capture, `--merge` note preservation,
  `--dry-run` defaults, and the `main()` degraded-run (<2 usable runs) loud
  WARNING + non-zero exit path.
- `tests/unit/test_do_merge_baseline.py` — schema-v1 load (backwards compat), schema-v2 load,
  new-regression detection (including the PR #1054/#1070 count-coincident
  scenario), flaky pass-through, `hung`/`import_error` pass-through,
  staleness warnings for all three triggers.
- `tests/unit/reflections/test_test_baseline_refresh_check.py` — the
  staleness-detector reflection: stale → `warning`, fresh → `ok`, and
  missing/malformed/directory-shaped baseline → benign `ok` (never raises).

## See also

- `scripts/refresh_test_baseline.py` — refresh tool
- `scripts/baseline_gate.py` — merge-gate comparison logic
- `scripts/_baseline_common.py` — shared junitxml parsing helpers
- `reflections/housekeeping/test_baseline_refresh_check.py` — weekly age-only
  staleness detector (issue #1933; requires a per-machine
  `~/Desktop/Valor/reflections.yaml` entry to actually run)
- `.claude/commands/do-merge.md` — orchestration
- [Test Reliability Flaky Filter](test-reliability-flaky-filter.md) — PR #484,
  the PR-branch retry filter (different layer)
- [PR-Shape-Aware Merge Gates](pr-shape-aware-merge-gates.md) — PR #1285,
  the per-SHA verdict cache layered on top of this baseline (the cache key
  hashes `data/main_test_baseline.json` so any baseline change silently
  invalidates cached verdicts)
- `docs/plans/merge-gate-baseline-refresh.md` — design plan
