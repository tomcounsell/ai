# Nightly Triage Dispatch

Two additions to the nightly regression detector (`scripts/nightly_regression_tests.py`)
that decide when it runs and what it does with a finding: an advisory run lock, and a
fire-and-forget triage session dispatch that hands newly-confirmed failures to an Eng
session for investigation and issue-filing.

The detector notifies nothing — the GitHub issue tracker is its only output surface
(#3134). A third addition shipped here originally, a best-effort LLM summarizer for
the Telegram alert text, and was deleted with the alert it existed to compose.

## Status

Shipped — Scope 1 of issue #2192 ("Nightly Regression Detector & Sentry Triage
Reflection — Dedupe, Readable Alerts, Auto-Triage").

**Scope 2 (Sentry triage reflection, `reflections/sentry_triage.py`) is a separate,
later PR and has not shipped yet.** This document covers Scope 1 only — the nightly
test detector side. Do not treat Sentry-side auto-triage as implemented until that
PR lands.

**The night's reported outcome is unchanged by the baseline classification
stage (issue #2334).** The outcome line for a newly-confirmed failure is
byte-identical in both shipped `NIGHTLY_FIX_MODE` values (`off` and `shadow`)
— see
`docs/features/nightly-regression-tests.md#baseline-classification-shadow-mode`.
`shadow` mode adds a `nightly-fix shadow-verdict:` log line and changes no
dispatch behavior and no exit code; an exception inside the tier is logged
non-fatally. **No fixer,
watchdog, hand-back, notify tier, or `--silent` flag exists in this repo.**
Autonomous action on the shadow verdict is deferred to #3076, which is
blocked on two seams that do not exist yet: a per-session env seam for a
fixer's own `gh` identity (today's dispatched sessions act under the
operator's `gh` auth), and `main` branch protection (nothing currently stops
a direct push to `main`, which an autonomous fixer would need guarded against
before it could safely act).

## What It Does

Builds on the existing nightly detector (see `docs/features/nightly-regression-tests.md`
for the base run: pytest, serial re-confirmation, delta computation). This feature adds
three behaviors layered around that base run:

1. **Run lock** — prevents two overlapping launchd invocations from both running the
   suite and both filing against the same window.
2. **Triage dispatch** — spins up an Eng session to investigate failures that have not
   been triaged before and file a GitHub issue, deduped per node so a failure that
   already has an issue open against it gets a recurrence comment instead of a second
   issue (see the base doc's "Comment-over-create" decision).

## Run Lock (Race 1)

`_acquire_run_lock()` takes an exclusive, non-blocking `fcntl.flock` on
`data/nightly_tests.lock`, acquired as the very first thing `main()` does — before
loading prior state or running any tests.

- **Problem it solves**: if two launchd invocations of the nightly job overlap (e.g. a
  slow prior run still finishing when the next scheduled run fires), both processes
  would otherwise run the suite independently and could both file against what's
  really the same underlying test run — duplicate, confusing noise.
- **Mechanism**: the sidecar-lock-file idiom — open/create the lock file, then
  `fcntl.flock(fd, LOCK_EX | LOCK_NB)`. On success, the caller must keep the returned
  file handle alive for the process lifetime (letting it get garbage-collected closes
  the fd and releases the lock early). The OS releases the lock automatically on
  process exit.
- **On collision**: `_acquire_run_lock()` returns `None`. `main()` logs the collision
  and returns `0` immediately — no test run, no tracker write, no state write. The
  losing invocation is a no-op, not a failure.

## Triage Session Dispatch

`maybe_dispatch_triage_session(dispatch_nodes)` fires off an Eng-role `AgentSession` to
investigate failing tests that have never been triaged before. The set comes from
`compute_dispatch_set(prev, confirmed_failing)`.

- **Invocation contract**: shells out to
  `python -m tools.valor_session create --role eng --slug nightly-triage-<hash8>
  --json --message <prompt>`.
  - `--slug` is **mandatory** on this call. A slugless `valor_session create` call
    for a non-teammate role tries to auto-derive a slug from an `issue #N` pattern
    in the message and exits 1 silently if none is found — nightly prompts have no
    such pattern, so omitting `--slug` would make every dispatch fail quietly.
  - `--json` is required so the dispatched session ID can be parsed back out of
    stdout (`json.loads(stdout)["session_id"]`, wrapped in try/except — a parse
    failure just means the session ID doesn't make it into the persisted state,
    it doesn't fail the dispatch or the run).
  - The subprocess call has a 30s timeout; any exception (timeout, missing binary,
    non-zero exit) is caught, logged as a warning, and treated as "no dispatch" —
    this is fire-and-forget, not a blocking dependency of the nightly run.
- **Dedup semantics**: dedup is **per node**, not per set. `dispatched_nodes` in
  `data/nightly_tests_last_run.json` holds every node ID a previous run handed to
  triage; `compute_dispatch_set` subtracts it from the confirmed-failing set, so a
  node with an issue already open against it cannot reach a second dispatch.
  - The delta and the dispatch answer **different questions**. The delta asks "is this
    a regression since last night" (`compute_new_failures`); the dispatch asks "does
    this node already have an issue". Conflating them is what made a standing failure
    re-triage on every run that had any new failure, so #2429, #2430 and #2462 each
    opened an issue over the same dead watchdog node (issue #2559).
  - `carry_dispatched_nodes` persists the union of (previously dispatched ∩ still
    failing) and whatever this run dispatched. A node that stops failing drops out, so
    a genuine re-regression is dispatchable again later, and a **renamed** node retires
    itself with no special case: `df6097fe6` renamed the watchdog node the churn kept
    citing, and the old ID simply stops appearing in the confirmed set.
  - Only what actually went out is recorded. A failed dispatch leaves its nodes unfiled,
    so the next run retries them instead of silently swallowing the failure. This is
    also why the dispatch set is not `compute_new_failures`: a node whose dispatch
    failed is no longer "new" but is still unfiled.
  - A **first (baseline) run** seeds `dispatched_nodes` with the confirmed set and
    dispatches nothing. The baseline declares the known-failing state rather than
    reporting a finding, so without the seed the *next* run would file the entire
    standing set as fresh discoveries.
  - `dispatched_session_id` records the most recent successful dispatch and is carried
    forward on runs that dispatch nothing.
- **Mandate**: the dispatched session's prompt is explicit that the task is
  investigate-and-file-a-`/do-issue`-quality GitHub issue describing the failure, its
  likely cause, and suggested next steps — **not** an auto-hotfix. Auto-hotfixing
  nightly regressions is out of scope and explicitly called out as a No-Go in the
  originating plan (`docs/plans/nightly-regression-triage.md`).

## Files

| File | Purpose |
|------|---------|
| `scripts/nightly_regression_tests.py` | Adds `_acquire_run_lock` and `maybe_dispatch_triage_session` around the existing detector; see `docs/features/nightly-regression-tests.md` for the base run mechanics |
| `data/nightly_tests.lock` | Advisory lock file for `_acquire_run_lock` (gitignored, empty — existence and the flock state are all that matter) |
| `data/nightly_tests_last_run.json` | Now also carries `dispatched_nodes` and `dispatched_session_id` alongside the existing delta-state fields |

## Design Decisions

**Advisory `flock`, not a Redis lock** — the nightly job already writes local JSON
state files rather than depending on Redis (see the base doc's "Local JSON state, not
Redis" decision). A lock file in the same `data/` directory keeps that pattern
consistent and needs no external dependency.

**Dispatch is fire-and-forget, not awaited** — the nightly script's job is to detect
and record, not to babysit a triage investigation. The dispatch subprocess call has a
short timeout and any failure degrades to "no triage session for this finding," never a
blocked or failed nightly run.

**Hash-based dedup over a run-count or time-based dedup** — the confirmed-failing set
is the signal that actually matters: two different failing sets should each get their
own triage session, but the same unresolved set showing up night after night should
not re-dispatch. A content hash captures that directly.

## Manual Testing

```bash
# Preview a full run including the dispatch path. --dry-run files nothing, posts no
# comment, and writes no state, but the dispatch decision logic still executes if
# there are newly-confirmed failures.
python scripts/nightly_regression_tests.py --dry-run
```

`maybe_dispatch_triage_session()` can also be exercised directly against fake node IDs
for a quick sanity check without running the full suite.

## See Also

- `docs/features/nightly-regression-tests.md` — the base detector this feature
  extends (run cadence, serial re-confirmation gate, delta computation, cascade
  collapsing, comment-over-create, and what each outcome produces)
- `docs/plans/nightly-regression-triage.md` — originating plan, including the No-Gos
  that keep triage dispatch investigate-only
- `docs/features/eng-session-architecture.md` — Eng session semantics for the
  dispatched triage session
