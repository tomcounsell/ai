# Nightly Regression Tests

Automated nightly safety net for the default test collection (`tests/` — the
same set a bare `scripts/pytest-clean.sh` collects). A launchd job runs the
collection each night at 03:00, verifies the run actually executed before
trusting its result, compares failure counts against the prior run, runs a
post-run TTFT (time-to-first-token) regression gate, and reports what it
found **only to the GitHub issue tracker**. It sends no Telegram, no mail, and
no notification of any kind: a finding becomes either a new issue, a comment
on the issue that already represents it, or a logged deferral. A quiet night
is a silent night.

## Status

Shipped (issue #972); TTFT regression gate added (issue #1227); run lock,
best-effort failure summarizer, and triage-session dispatch added (issue #2192
Scope 1) — see `docs/features/nightly-triage-dispatch.md` for those
additions; collection widened from `tests/unit/` to the default collection,
run-integrity guard, collection-aware baseline, and worker-role install gate
added (issue #2823); baseline classification stage and shadow decision gate
added (issue #2334) — see "Baseline Classification (Shadow Mode)" below;
cascade collapsing, pre-file dedup, and a per-run issue budget added (issue
#3131); comment-over-create, signature-keyed cascade identity, and removal of
all notification added (issue #3134); closed-issue-aware dedup, body-failure
cascade collapsing, and environmental-failure exclusion added (issue #3075).
Autonomous-fix action on the gate's verdict is **not shipped**; it is deferred
to #3076.

## What It Does

- Acquires an advisory run lock (`data/nightly_tests.lock`) before doing anything
  else; a second overlapping invocation logs the collision and exits 0 with no test
  run and nothing filed — see `docs/features/nightly-triage-dispatch.md#run-lock-race-1`
- Runs the default collection (`COLLECTION_PATHS = ["tests/"]`) through
  `scripts/pytest-clean.sh -n {NIGHTLY_XDIST_WORKERS}` (default 6, env-overridable)
  nightly at 03:00 local time, with a 300s test-DB claim window sized for an
  unattended run
- Validates run integrity before trusting the result (`validate_run_integrity`):
  a missing/corrupt report, a signal-death or usage-error exit code, a
  fixture-error storm, or — the case that matters most — a **coverage floor**
  on `total` catches test-DB starvation, which produces zero `error` outcomes
  and a legal exit code and would otherwise read as a clean night. A tripped
  guard logs loudly, files or comments on the **cascade only**, and writes no
  baseline — see "The integrity trip still reports" below
- Compares the confirmed-failing set against the previous run's
  `data/nightly_tests_last_run.json`, but only when both runs share the same
  `collection`. A run whose recorded collection differs (the widening night,
  or any future change of scope) re-baselines instead: it seeds
  `dispatched_nodes` with the whole currently-failing population and
  dispatches **one** umbrella triage session, rather than re-opening the
  #2429/#2430/#2462 per-node duplicate-filing churn
- Runs the TTFT regression gate as a post-test check (see below)
- Notifies nothing, ever. The outcome of the night — baseline established,
  N newly-confirmed failures, collection error, clean run, TTFT regression —
  is a log line and, where there is a finding, a tracker write
- On newly-confirmed failures, fires a deduped, fire-and-forget Eng-session dispatch
  with literal, Python-computed issue titles (`Nightly regression: {node}`) to
  investigate and file a GitHub issue — see
  `docs/features/nightly-triage-dispatch.md#triage-session-dispatch`
- Collapses cascades before filing: nodes sharing one normalized setup-error
  message on one xdist worker (at least `CASCADE_MIN_GROUP_SIZE`, default 3)
  become ONE umbrella issue titled
  `Nightly regression cascade [{digest}]: {message}`, with the node list in a
  collapsed section. Detection groups by (worker, message); filing merges by
  message, so four identically-poisoned workers produce one issue
- Collapses body-failure cascades the same way (issue #3075): nodes whose
  failure normalizes to one identical first error line group into one umbrella
  once the group reaches `BODY_CASCADE_MIN_GROUP_SIZE` (default 5, env
  `NIGHTLY_BODY_CASCADE_MIN_GROUP_SIZE`), under a distinct `body::` state-key
  namespace so setup and body umbrellas never collide. A node that fails alone
  in its own body stays its own finding
- Excludes environmental failures before any grouping or filing (issue #3075):
  a node classifies environmental only when EVERY failing phase's normalized
  first error line looks network-shaped (DNS, TLS, refused/reset, connect
  errors) — an assertion failure in any phase disqualifies it, so a regression
  whose assert-diff merely mentions a network string still files. Environmental
  nodes file nothing new and are not recorded as dispatched, so they re-evaluate
  every night. Two escalation paths (issue #3163) keep that from being terminal
  silence: the state file carries `environmental_streaks`, a per-node count of
  consecutive environmental nights, and a node that reaches
  `NIGHTLY_ENVIRONMENTAL_ESCALATE_NIGHTS` (default 3; `0` disables) is handed
  back to the ordinary filing path, where open/closed dedup, cascade grouping
  and the issue budget apply to it like any other node; and an environmental
  node whose exact title is already open gets one recurrence comment naming
  the classification and the streak length, and is recorded so it is
  suppressed afterwards like any other commented node. The streak map is
  rebuilt each night from tonight's classifications, so a node that passes,
  fails for a code-level reason, or gets filed drops out and its count resets
- The closed-issue dedup read pulls `CLOSED_ISSUE_LIST_LIMIT = 4000` rows under
  its own `CLOSED_ISSUE_LIST_TIMEOUT_SECONDS`, sized past the repo's whole
  closed set so every closure is visible to dedup; the read stays on the REST
  path rather than a title-scoped `--search`, whose index lag is the exact
  window dedup has to be right in. Saturating the window now logs a WARNING,
  because it means the repo has outgrown the limit
- **Comments instead of filing a twin.** Open AND closed issues are read once
  via `gh issue list` (the REST path, not the index-lagged `--search`)
  immediately before dispatch. A finding with an open issue gets a recurrence
  comment carrying the run timestamp, HEAD, blast radius, and xdist worker
  ids — not silence. A finding whose exact title exists only CLOSED resolves
  by the closure's `stateReason`, taken from the title's most recent closure
  (max `closedAt`, since creation order diverges from closure order):
  `NOT_PLANNED` means a human consolidated or declined it — comment on the
  closed issue, never re-file; `COMPLETED` means it was fixed — failing again
  is new information, so it re-files; an unknown reason comments. Either
  unreadable list fails open (it files). This is the only dedup that spans
  machines
- Caps a single run at `MAX_ISSUES_PER_RUN` (10) issues, umbrellas and per-node
  alike drawing from one budget; the remainder is logged and retries on a later
  run. **Comments do not spend budget** — the cap bounds how much *new* tracker
  surface one night creates, and a comment creates none
- Clean runs produce no noise

## What Each Outcome Produces (issue #3134)

Nothing in this table notifies anyone. The "Tracker" column is the only thing
a human is expected to notice; the log is the full record.

| Condition | Tracker | Log |
|-----------|---------|-----|
| First run or a collection change (re-baseline) | One umbrella triage session for the absorbed population | `Baseline established[ (re-baseline: prior population absorbed)]: {total} tests, {failed} confirmed failures` |
| Run-integrity guard tripped | Cascade only — commented if already open, filed otherwise; per-node filing suppressed | `FATAL: {reason}` then `Integrity trip recorded: N issue(s) filed, M comment(s) posted` |
| Newly-confirmed failure, no open issue | New issue via triage session | `Tracker: N issue(s) filed, M recurrence comment(s) posted` |
| Newly-confirmed failure, issue already open | Recurrence comment on the existing issue | same line, with `M > 0` |
| Newly-confirmed failure, exact title closed `NOT_PLANNED` (newest closure) | Recurrence comment on the closed issue; never re-filed | same line, with `M > 0` |
| Newly-confirmed failure, exact title closed `COMPLETED` (newest closure) | New issue — failing again after a fix is new information | `Tracker: N issue(s) filed ...` |
| Environmental failure (every failing phase network-shaped) | Nothing new; unrecorded, re-evaluates nightly. Exact title already open: one recurrence comment, then recorded | environmental exclusion logged with the night count, surfaced in the run summary |
| Environmental failure for `NIGHTLY_ENVIRONMENTAL_ESCALATE_NIGHTS` consecutive nights | Ordinary filing path (issue #3163) | `... escalated to ordinary filing (#3163)` |
| Issue budget exhausted | Nothing; the finding stays unrecorded and retries next run | `Issue budget reached: deferring ...` |
| Collection errors, no newly-confirmed failures | Nothing | `Collection error ({new_errors} errors)` |
| TTFT regression | Nothing | `TTFT regression: {detail}` |
| Lock collision (overlapping run) | Nothing | collision logged, no test run |
| Clean run | Nothing — `gh` is not even invoked | `Clean run (no newly-confirmed failures)` |

## TTFT Regression Gate (issue #1227)

After the unit suite runs, a post-run gate reads `logs/cold_start_metrics.jsonl`
and compares the last `TTFT_LAST_N` (10) PM-session cold starts against
`TTFT_THRESHOLD_SECONDS` (120s — production target is 90s; the nightly
threshold allows slack for run-to-run noise). A regression is **logged**
without changing the script's exit code (it used to page; #3134 removed every
notification path). The gate never
crashes the run: a missing log file, a parse failure, or any other exception
is swallowed and logged as non-fatal.

## Baseline Classification (Shadow Mode)

Every non-`off` run additionally asks, for each newly-confirmed failure, "did
this test already fail at the prior run's HEAD SHA, or did HEAD just break
it?" — and logs the answer alongside a verdict on whether the gate *would*
have attempted an autonomous fix. **Nothing acts on that verdict.** Acting on
it (a fixer, a watchdog, a hand-back to a human, a notify tier, a `--silent`
flag) is entirely out of scope here and tracked as #3076.

### `classify_against_baseline`

`classify_against_baseline(node_ids, baseline_sha, *, repo_root=PROJECT_DIR,
worktree_path=BASELINE_WORKTREE, wrapper=PYTEST_CLEAN_SH,
report_path=PYTEST_BASELINE_JSON_TMP)` buckets each node ID by whether it was
already failing at `baseline_sha`. It is synchronous and runs in-process — no
subagent, no spawned session, no Task tool. It provisions the baseline
worktree, re-runs exactly `node_ids` there through `scripts/pytest-clean.sh`
(`-n0`, `--json-report`), and reads the resulting report.

The four parameters after `node_ids` and `baseline_sha` are keyword-only and
exist as the injection seam a non-stubbed fixture test drives without
monkeypatching module globals:

- `repo_root` — the checkout `git worktree add` runs from.
- `worktree_path` — the provisioned baseline worktree pytest runs in.
- `wrapper` — the `pytest-clean.sh` the run routes **through**, never around;
  its `.venv`, interpreter-pin, and rootdir guards are load-bearing here.
- `report_path` — the classifier's own JSON report target.

Their production defaults keep `main()`'s call site a two-argument call.

### The baseline ref: the prior run's HEAD SHA, not "last-green"

`baseline_sha` is **the prior run's HEAD SHA** (`prev["head_commit"]`), never
bare `main` and never called "last-green" or "last-known-good": that key is
written on every non-fatal run (`main()` writes `head_commit` on every
`save_last_run()` call, not only on a clean one), and nothing in the detector
records greenness. The soundness argument is per-node, not global: a
*newly-confirmed* failure was by definition absent from the prior run's
confirmed-failing set, so at that SHA the node specifically was not failing —
that says nothing about whether the rest of the suite was green at that SHA.

### The seven classification preconditions

`classify_precondition_reason` and `gate_reason` share one ordered list —
this is the single canonical enumeration, evaluated in this order, and the
`reason=` token in the shadow-verdict log is the name of whichever one fails
first:

1. `NIGHTLY_FIX_MODE` is not `off` (checked by the caller before
   `log_shadow_verdict` runs at all — not itself a `reason=` token).
2. `new_failures` is non-empty (also checked by the caller).
3. `seed_run` — the run is a first-run/re-baseline seed, not an ordinary
   delta. Unreachable from today's only production call site (the shadow
   block lives in the `elif new_failures:` arm of `if is_seed_run:`, so
   `is_seed_run` is provably false there); retained as cheap
   defense-in-depth so a future caller outside that `elif` cannot regress
   into gating a re-baseline night, and so this list and `gate_reason` keep
   sharing one clause implementation.
4. `integrity_warnings` — `validate_run_integrity` produced a warning (e.g.
   the shallow-shrink case).
5. `dry_run` — the run was invoked with `--dry-run`.
6. `no_baseline_sha` — `prev["head_commit"]` is missing or empty.
7. `over_max_failures` — `len(new_failures) > NIGHTLY_FIX_MAX_FAILURES`.

`classify_precondition_reason` covers preconditions 3-7 (the first two are
the call site's business, since they gate whether classification runs at
all). `gate_reason` runs the same five checks first, then adds three more
post-classification clauses (`pre_existing`, `inconclusive`,
`not_all_newly_broken`) before returning `"none"`.

### The classifier's own JSON report path

The classifier writes to `PYTEST_BASELINE_JSON_TMP`
(`/tmp/nightly_pytest_baseline_report.json`), never
`PYTEST_SERIAL_JSON_TMP` or `PYTEST_JSON_TMP`. Keeping the three paths
disjoint is what makes the run's own report survive the classifier: if the
classifier overwrote `PYTEST_SERIAL_JSON_TMP` with the baseline commit's
results, everything downstream that re-reads it would be reading a run in
which every newly-broken node appears to have passed.

### The provisioned baseline worktree needs its own `.venv`

`provision_baseline_worktree` points the persistent worktree at
`.worktrees/nightly-baseline/` (`BASELINE_WORKTREE`) at `baseline_sha`,
creating it with `git worktree add --detach` plus a full `uv sync` when
absent, or re-pointing it with `git checkout --detach` and re-running
`uv sync` only when `uv.lock` changed since the last provision (tracked by a
`.nightly-baseline-provisioned` marker file holding the lockfile's SHA-256
digest). This keeps the amortized nightly cost near zero.

The `.venv` is mandatory, not an optimization: `scripts/pytest-clean.sh`
refuses to run in a linked worktree with no `.venv` (#3033) and refuses an
off-pin interpreter (#2617); the committed `.python-version` is what makes a
bare `uv sync` land on the pinned interpreter. Provisioning failure — of any
kind, at any step — buckets every node `inconclusive` and **never** falls
back to running against `PROJECT_DIR`: a fallback would classify against
HEAD's own source, marking every node `pre_existing`, which looks exactly
like a working classifier while being silently wrong. See
`docs/features/scheduled-disk-reclaim.md` for why this worktree is never
swept away.

### The three buckets, and the fail-toward-escalate rule

`classify_against_baseline` returns `{"newly_broken": [...], "pre_existing":
[...], "inconclusive": [...]}`:

- **`newly_broken`** — passed at `baseline_sha`, fails at HEAD.
- **`pre_existing`** — failed at `baseline_sha` too.
- **`inconclusive`** — every failure path: a missing SHA, worktree
  provisioning failure, collection error, timeout, an unparseable or missing
  report, a node absent from the report, or any raised exception. Never
  guessed, never assumed-passed.

The gate (`gate_reason` / `decide_fix_or_escalate`) fails toward
`"escalate"`: any `pre_existing` node, any `inconclusive` node, or a
`newly_broken` set that does not exactly match `new_failures` all route to
`"escalate"`. Only a run where every single newly-confirmed node classifies
cleanly `newly_broken` reaches `"none"`.

### What the classifier discriminates that `compute_new_failures` does not

`compute_new_failures` (the existing delta function) proves a node was
**absent from the prior run's confirmed-failing set** — a "first time we've
seen this fail" signal. `classify_against_baseline` proves
something narrower and stronger: that the node **actually passed** at the
prior run's HEAD SHA, by running it there. They differ exactly when the node
was not collected at the prior SHA, was a filtered artifact of that run, or
the prior run itself was untrusted (an integrity-guard warning) — cases where
`compute_new_failures` would still call the node "new" but the classifier
correctly reports `inconclusive` rather than asserting it passed.

### The pure decision gate

`decide_fix_or_escalate(classification, new_failures, caps, run_flags)`
returns `"autonomous-fix"` iff `gate_reason(...) == "none"`, else
`"escalate"`. It is pure — no I/O, no subprocess, no state — so it is tested
directly against constructed `classification`/`new_failures`/`caps`/
`run_flags` values. In this tier, `"autonomous-fix"` means only "the gate
would have attempted a fix"; it triggers nothing further (#3076).

### `off` / `shadow` modes

`NIGHTLY_FIX_MODE` (env-overridable, default `"shadow"`) resolves through
`resolve_fix_mode` to exactly `"off"` or `"shadow"`; any other value is
treated as `"off"` (fail toward the detector's pre-feature behavior) and
warned about once per process. Both `NIGHTLY_FIX_*` knobs are read from
`os.environ` **at call time**, never at module import: the vault `.env` only
reaches the environment via `load_env_or_die()` inside `main()`, and the
launchd job supplies just `PATH` and `HOME`, so an import-time read would
freeze the in-code defaults and make the off switch inert on the only
surface that matters. A malformed `NIGHTLY_FIX_MAX_FAILURES` degrades to the
in-code default with a warning.

- **`off`** — classification, the gate, and the verdict log are skipped
  entirely. The detector behaves exactly as it did before this feature.
- **`shadow`** — classifies, gates, and **logs** the verdict that would have
  been acted on. The night's outcome line is byte-identical to `off` mode.
  Nothing is fixed. The tier runs after the tracker writes and is non-fatal by
  construction: any exception inside it is logged and swallowed, and the
  outcome is still recorded — see `docs/features/nightly-triage-dispatch.md`.

### The `nightly-fix shadow-verdict:` log contract

`log_shadow_verdict(new_failures, caps, run_flags)` emits, on **every**
non-`off` call with a non-empty `new_failures`:

```
nightly-fix shadow-verdict: {verdict} reason={reason} nodes={len(new_failures)}
```

`verdict` is `autonomous-fix` or `escalate`; `reason` is the `reason=` token
from `gate_reason` (see the seven-precondition list above — `none` when the
gate would have fired). This line is emitted even on a night a precondition
skipped classification outright, in which case `reason=` names that
precondition and no pytest ran in the baseline worktree.

The sibling line, `nightly-fix shadow-buckets:`, is emitted only when
classification actually ran (i.e. no precondition skipped it):

```
nightly-fix shadow-buckets: newly_broken={n} pre_existing={n} inconclusive={n} not_newly_broken={comma-joined node IDs}
```

`not_newly_broken` is the union of `pre_existing` and `inconclusive` node
IDs. The verdict line alone answers "would the gate have fired?"; the buckets
line is what lets a human later judge "would it have been *right*?" from the
log history.

### Bounds

`PYTEST_BASELINE_TIMEOUT_SECONDS` (1800s / 30 minutes, a plain module
constant, not an env knob — matching `PYTEST_RECONFIRM_TIMEOUT_SECONDS`'s
convention) bounds the classifier's pytest subprocess, since it only re-runs
the (already capped) newly-confirmed set serially in the baseline worktree.

Two further constants guard baseline **provisioning** specifically, so a
`git worktree add` or `uv sync` can never hang the nightly run:
`BASELINE_GIT_TIMEOUT_SECONDS` (300s) bounds each git subprocess, and
`BASELINE_UV_SYNC_TIMEOUT_SECONDS` (900s) bounds `uv sync`. A
`TimeoutExpired` on either is reported as failure exactly like a non-zero
exit, bucketing every node `inconclusive`.

## Files

| File | Purpose |
|------|---------|
| `scripts/nightly_regression_tests.py` | Main script: acquires the run lock, runs the default collection through `scripts/pytest-clean.sh`, validates run integrity, computes the failure delta or re-baselines on a collection change, files or comments on findings via the tracker, runs the TTFT gate, saves state. Notifies nothing |
| `com.valor.nightly-tests.plist` | launchd plist template with `__PROJECT_DIR__`, `__HOME_DIR__`, `__SERVICE_LABEL__` placeholders |
| `scripts/install_nightly_tests.sh` | Install script, **install-only**: refuses to install from a lane worktree, does nothing when the projects config is unreadable or the host role is undeterminable, worker-role gated (`owns_a_project()` — any machine owning a project, Telegram-independent, verdict carried by a printed `ROLE:OWNS` token), substitutes placeholders, calls `launchctl_bootstrap_fail_soft` (fail-soft errno-5 recovery via `scripts/lib/launchctl.sh`, see bridge-self-healing.md Component 21). It never removes a plist; the uninstall line it prints on success is the removal path (#2905) |
| `data/nightly_tests.lock` | Advisory `flock` lock file preventing overlapping runs (gitignored) — see `docs/features/nightly-triage-dispatch.md` |
| `data/nightly_tests_last_run.json` | Delta state: `passed`, `failed`, `error`, `skipped`, `total`, `run_at`, `collection`, `head_commit`, `dispatched_nodes`, `dispatched_session_id`, `seeded_nodes` (carried forward on every run), `cascade_issues` (signature → issue number, see below), and — on a re-baseline night only — `seed_collection`, `seed_size`, `min_expected_collected` (gitignored) |
| `logs/nightly_tests.log` | Per-run log with timestamps and counts |
| `logs/nightly_tests_error.log` | Startup crash log (captured by launchd before `log()` fires) |
| `logs/cold_start_metrics.jsonl` | TTFT samples consumed by the gate |
| `.worktrees/nightly-baseline/` | Persistent, provisioned baseline worktree the classifier re-points at the prior run's HEAD SHA and runs pytest in; carries its own `.venv` (gitignored, protected from `tools/disk_reclaim.py` — see `docs/features/scheduled-disk-reclaim.md`) |
| `.worktrees/nightly-baseline/.nightly-baseline-provisioned` | Marker recording the `uv.lock` SHA-256 digest the worktree's `.venv` was last synced against, so re-provisioning only re-runs `uv sync` when the lockfile actually moved. Ignored via a root `.gitignore` entry (effective inside the lane once the baseline SHA carries it); either way the lane's `protected` guard in `sweep_worktrees` pre-empts any dirty-tree signal the untracked file could raise |
| `/tmp/nightly_pytest_baseline_report.json` | The classifier's own `--json-report` output (`PYTEST_BASELINE_JSON_TMP`); never shares a path with `PYTEST_JSON_TMP` or `PYTEST_SERIAL_JSON_TMP` |

## Design Decisions

**JSON report over text parsing** — `--json-report` gives structured summary data without
fragile regex against pytest's output format.

**Local JSON state, not Redis** — Two fields (`failed`, `run_at`) don't justify a Redis
dependency. Matches the `sdlc_reflection_last_run.json` and `autoexperiment_last_run.json`
patterns.

**The tracker is the only output surface (#3134)** — The detector notifies
nothing: no Telegram, no mail, no page. Owner ruling, verbatim: *"i don't want
alerts either. i just want a single legit issue if and only if it's distinct
from issues already created. and better to comment on an existing issue than
create a new one."* The LLM failure summarizer existed only to compose alert
text and was deleted with it. The log is unchanged and remains the full record
of every night.

**Comment-over-create is the default posture, not a fallback** — A finding
whose issue is already open produces a recurrence comment carrying the run
timestamp, HEAD SHA, node count, file count, and xdist worker ids. Silence was
the #3131 behavior and it lost the signal that a defect is still happening,
and whether it is getting worse. A comment that fails to post leaves the
finding **unrecorded**, so the next run retries it: a recurrence that could
not be written down has not been reported, and marking it filed would lose it
permanently.

**Cascade identity is the normalized signature, not the rendered title** — A
cascade's key is the normalized setup-error message (digits collapsed to `#`,
pytest's `E ` marker and the `[gwN]` banner stripped). `cascade_issues` in the
state file maps that signature to the issue number, so a human retitling the
umbrella issue does not make the same defect look new. The title match remains
as the *bootstrap*: this script never opens issues itself — a triage session
does — so on the night of filing the number is unknowable and the entry is
recorded as `None` (pending). `carry_cascade_issues()` upgrades it the first
night the title appears in the open set. A pending entry that cannot be
resolved is dropped, because that means either no issue was ever opened or it
has since been closed, and in both cases the correct response to a recurrence
is a fresh issue rather than silence against a record of nothing. An
unreadable open-issue list keeps the map verbatim: `None` is "could not tell",
never evidence that anything closed.

`cascade_issues` is **per-machine**, exactly like `dispatched_nodes` — the
cross-machine gap documented below applies to it unchanged. Per-node findings
need no such map: the node id *is* the identity and it appears verbatim in the
title, so the title is a faithful key there in a way it is not for a cascade.

**The integrity trip still reports** — A tripped run-integrity guard says the
*measurement* cannot be trusted, not that nothing happened. The 2026-09-03
storm was both: an untrustworthy run and a genuine defect (one poisoned xdist
worker, 278 setup errors). With nothing left to alert, a guard that dropped
its finding would make a storm completely invisible. So `_handle_integrity_trip()`
files or comments on the **cascade only** — on a night classified as
infrastructure the individual nodes are collateral by definition — and then
fails the run. It writes only `dispatched_nodes` and `cascade_issues`, merged
onto the prior state: the totals and the confirmed-failing set are precisely
the numbers just declared untrustworthy, and overwriting a good baseline with
them is what the no-state-write invariant exists to prevent.

**Confirmed-failing-set delta, not a scalar delta** — The state file persists the
confirmed failing node-ID **set**, not just a count, so a shifting flaky set
(same count, different tests) never reads as a regression and a genuinely new
failure does, even when the total count is flat.

**Worker-role gating, not bridge-role** — Running the test suite requires a
checkout and a worker, not a Telegram bridge. `install_nightly_tests.sh`
gates on `owns_a_project()` (the same fix issue #1379 applied to
`install_reflection_worker.sh`), which qualifies any machine owning at least
one project, regardless of whether that project has Telegram configured. The
gate is **install-only**: on any other outcome (owns nothing, unreadable
config, undeterminable identity, interpreter failure) it does nothing and
leaves any existing plist alone. Removal stays a human step because it must
be certain of a negative from inputs that can be indeterminate, and
`projects.json` carries no fleet registry that could prove a non-match is
meaningful (#2905). A worktree refusal runs first: the plist is
machine-global and hardcodes an absolute `PROJECT_DIR`, so installing from a
lane worktree would aim the fleet's detector at a directory merge cleanup
deletes.

**Run-integrity guard, not returncode-only trust** — A run that could not
execute (test-DB slot exhaustion, a wedged xdist controller) was measured,
reproducibly, to exit 0 with zero tests collected. `validate_run_integrity()`
classifies a completed run before anything downstream (dispatch, state
persistence) trusts it; a tripped guard logs loudly, records the cascade, and
writes no baseline (see "The integrity trip still reports" above).

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
seeded node that regresses is **logged once, then never filed**.
`compute_new_failures()` keys on `failing_tests` rather than dispatch state,
so the night it re-fails is counted as newly-confirmed in the log — but from
the next night it is no longer "new", and no issue is ever opened. For a
non-seeded node the durable record is a GitHub issue; for a seeded node it is
one line in a log file, and the umbrella may since have been closed. `seeded_nodes` also never retires entries, unlike `dispatched_nodes`,
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
the whole night-one population forever — behind a log line that reads like a
successful baseline. Refusing to persist means the next run sees no
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
same red node both dispatch unless something else prevents it. Since #3131 the
detector reads the open issue set itself (`open_issues()`) immediately before
dispatch and, since #3134, comments on rather than re-files any finding whose
issue is already open — which closes the common cross-machine case, but it is
a read-then-act check with no lock, so two machines dispatching inside the
same window still both file. The literal title remains the contract: the detector
emits it verbatim, the triage session is instructed to search for it, and
nothing verifies an issue was actually filed under it, so a future change to
the title format silently reopens #2429/#2430/#2462 across the fleet.
`MAX_ISSUES_PER_RUN` (10) bounds the blast radius of any single run;
a shared, Redis-backed dispatch set is the real fix and is deliberately
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
owns at least one project (worker-role, not bridge-role).

### Opting one machine out

Create `data/nightly-tests-disabled` in that machine's checkout:

```bash
touch data/nightly-tests-disabled   # this machine stops installing
rm    data/nightly-tests-disabled   # re-enable; next /update installs
```

`data/` is gitignored, so the marker is **per-machine by construction** — the
opted-out host stays down and every other machine sees no marker and installs
on its next `/update`. Same mechanism as `data/auto-revert-enabled`
(bridge-self-healing.md).

The check runs before every other consideration, including the worktree
refusal, so an opted-out machine short-circuits immediately.

**The marker skips installation; it does not uninstall.** This installer
installs or does nothing (see the install gate). To remove a detector that is
already running, use the uninstall line the installer prints on success:

```bash
launchctl bootout gui/$(id -u)/com.valor.nightly-tests \
  && rm ~/Library/LaunchAgents/com.valor.nightly-tests.plist
```

Wiring removal to the marker would reintroduce the branch #2905 exists to
reimplement carefully, for a case the operator is already standing in front of.

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
# Dry-run: runs the suite and prints what it would do, changing nothing.
# No Eng session spawned, no GitHub issue filed, no comment posted, and NO
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
- `gh` on PATH — required to read open issues and post recurrence comments. An
  unreadable open-issue list fails open (the run files); a failed comment
  leaves the finding unrecorded so the next run retries it

## See Also

- `docs/features/nightly-triage-dispatch.md` — the run lock and triage-session
  dispatch layered around this base detector (issue #2192 Scope 1); autonomous-fix
  action is deferred to #3076
- `docs/features/scheduled-disk-reclaim.md` — why `.worktrees/nightly-baseline/` is
  protected from the disk-reclaim sweeper
