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
   been triaged before and file a GitHub issue, deduped per node against BOTH the open
   and closed sets: an open issue gets a recurrence comment instead of a second issue,
   and a title closed `NOT_PLANNED` (by its most recent closure) is commented on and
   never re-filed, while one closed `COMPLETED` re-files because failing again after a
   fix is new information. The prompts state the same open-and-closed rule so the
   pre-flight and the agent's instructions cannot drift (see the base doc's
   "Comment-over-create" decision and issue #3075). Three defences then keep a
   *replayed* turn from filing a second issue for a node the same session already
   opened, which is what produced the #2960–#2999 wave (issue #3170):
   - **All three prompts hand over the lookup command.** `ISSUE_LOOKUP_INSTRUCTION`
     carries the literal `gh issue list --state all …` REST read, so the agent sees an
     issue the instant it exists rather than waiting on an index that lags creation by
     minutes.
   - **The per-node dispatch carries the detector's own resolved dispositions**, so the
     agent confirms a decision instead of re-deriving one.
   - **The per-node dispatch seeds a session ledger** recording what it has already
     filed. See "Replay Idempotency" below.

   Fixes two and three stop at the per-node path deliberately, and the gap is a
   decision rather than an oversight: the cascade umbrella and the re-baseline seed
   pre-render their own prompt at the call site and their builders take no disposition
   or ledger parameter, so a disposition built for them would have no reader. Every
   observed duplicate-filing incident came out of the per-node path. Both override
   paths still get the lookup command, which is the defence that addresses the read
   failure itself. Widening the other two is the first thing to do if a cascade or seed
   duplicate is ever seen.

### Three prompts, one lookup instruction

There are **three** issue-filing prompts, not one, and each dispatches a session that
opens real GitHub issues:

| Builder | Dispatched from | Files |
|---------|-----------------|-------|
| `_build_triage_prompt` | `dispatch_findings`, per surviving node | One issue per node |
| `_build_cascade_prompt` | `dispatch_findings`, per collapsed cascade | One umbrella issue |
| `_build_seed_prompt` | `main()`, on a collection re-baseline | One seed umbrella issue |

All three interpolate the same `ISSUE_LOOKUP_INSTRUCTION` constant. That matters
historically: the doc used to imply a single prompt, which is how the cascade and seed
prompts went four passes at this bug without ever being hardened — each carried its own
copy of the sentence, and each pass fixed the copy it happened to be looking at. The
seed prompt lived inline inside `main()` until #3170 and could not be rendered by a
test or scanned by a gate at all; extracting it into a named builder is what made its
copy of the defect visible.

### Replay Idempotency

`data/nightly-triage-ledger/{slug}.json` is the third and last-resort defence: a record
on disk of what one triage session has already filed, so a turn replayed with a fresh
context reads its predecessor's work instead of starting from zero.

```json
{
  "slug": "nightly-triage-a1b2c3d4",
  "created_at": "2026-09-05T06:00:00Z",
  "entries": [
    {"node": "tests/unit/test_a.py::test_1",
     "title": "Nightly regression: tests/unit/test_a.py::test_1",
     "disposition": "file",
     "resolved_against": "gh issue list --state all (open+closed REST read)",
     "resolved_at": "2026-09-05T06:00:00Z"}
  ],
  "filed": []
}
```

- **Who writes what.** `write_triage_ledger` seeds `slug`, `created_at`, `entries` and
  an empty `filed` before the session subprocess starts; the triage agent appends to
  `filed` after each `gh issue create`, before moving to the next entry.
- **Per-node dispatch only.** `entries` derives from the `dispositions` argument and
  from nothing else, so the two `prompt=`-override dispatches produce an empty entry
  list, no file is created for them, and `nightly-triage-baseline.json` never exists.
  One gate does the whole narrowing; there is no separate branch.
- **Advisory and fail-open.** Any write failure logs a `WARNING` naming the slug and
  returns `None`; the dispatch proceeds without a ledger paragraph in its prompt. A
  ledger that cannot be written must not stop the night from filing, the same posture
  `open_issues()` takes when it cannot read. The prompt also tells the agent to treat a
  missing or unparseable ledger as an empty `filed` list.
- **Degraded read, same no-file outcome, different cause.** A degraded read (either the
  open-issues or closed-issues REST read fails) also produces no ledger file:
  `dispatch_findings` passes `dispositions=None` in that case, `maybe_dispatch_triage_session`
  calls `write_triage_ledger` with an empty entries list, and `write_triage_ledger` returns
  `None`, the same no-file outcome as the write failure above but from withheld dispositions
  rather than a failed write, with the live REST-read instruction in every prompt remaining
  undemoted as the defense that actually covers a degraded read.
- **Written after the `--dry-run` short-circuit.** A preview writes no state file.
- **Deliberately unlocked.** Two sessions share a ledger only when dispatched for an
  identical node set, which the run lock and `compute_dispatch_set` make
  near-impossible within a machine, and the file is machine-local so two hosts never
  share one. The one case defended is a same-slug retry landing on a ledger a live
  session is appending to: an existing file whose `filed` array is non-empty is left
  exactly as it is. The write itself goes through a temp file and `os.replace`, because
  truncated JSON is worse for the agent than stale-but-valid JSON.
- **Why `data/` and not the lane worktree.** The issue asked for a session-local file
  under `.worktrees/{slug}/`. That worktree is a git checkout, so a file there shows up
  in the agent's own `git status` and dies with the lane on teardown — and the
  stale-branch sweep described under "Lane reaping" keeps any lane whose tree is dirty,
  so a ledger inside the lane would make every triage worktree permanently unreapable,
  reintroducing the accumulation #3162 just fixed. `data/` is already this script's
  state home, is gitignored, survives teardown, and is reachable by absolute path from
  inside a worktree. The slug-keyed filename preserves the session-local property.
- **Growth is bounded by distinct failure sets, not by nights.** The per-node dispatch
  never passes `slug_suffix`, so its slug is always the sha256 of the sorted node set
  and a recurring failure set overwrites its own file. No pruning job.

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

### Lane reaping

Every dispatch mints a `session/nightly-triage-*` branch and a
`.worktrees/nightly-triage-*` worktree, and the triage session leaves both behind
when it ends. The 2026-08-24 incident accumulated 13 branches and 14 worktrees this
way (issue #3162). The rule that keeps the count at zero: **a nightly-triage lane is
disposable the moment its session ends, and the bridge's boot-time stale-branch sweep
owns its teardown.** `cleanup_stale_branches` (`agent/session_revival.py`, run at
every bridge boot and by the `stale-branch-cleanup` reflection) selects
`session/nightly-triage-*` branches whose tip commit is older than the sweep's
72-hour window (`agent/worktree_manager.py::stale_nightly_triage_slug`), reaps the
matching worktree through the fail-closed `reap_idle_worktree`, then deletes the
branch through the usual unmerged-branch guard. The sweep reads its candidates from
`git branch --list 'session/*'`, which marks a branch checked out in another worktree
with a leading `+ `; the parser strips that marker along with `* `, so a lane whose
branch is still checked out is a candidate like any other (the sweep once stripped
only `* `, which silently excluded every lane it was meant to reap). The window is the sweep's existing
one, three nightly cycles, and it is only the coarse filter: a live triage session is
protected by `worktree_busy_probe` and the live-process scan, both of which keep the
lane on any answer other than "clear". A lane with a dirty tree is kept, never
auto-preserved, so a half-deleted worktree (issue #3167) stays on disk for a human
rather than being committed. Every other `session/*` namespace keeps its worktree;
only this namespace is reaped by the sweep, because only this namespace is minted
by a script with nothing to come back for. `tools/disk_reclaim.py` remains the
14-day backstop when armed.

## Files

| File | Purpose |
|------|---------|
| `scripts/nightly_regression_tests.py` | Adds `_acquire_run_lock` and `maybe_dispatch_triage_session` around the existing detector; see `docs/features/nightly-regression-tests.md` for the base run mechanics |
| `data/nightly_tests.lock` | Advisory lock file for `_acquire_run_lock` (gitignored, empty — existence and the flock state are all that matter) |
| `data/nightly_tests_last_run.json` | Now also carries `dispatched_nodes` and `dispatched_session_id` alongside the existing delta-state fields |
| `data/nightly-triage-ledger/{slug}.json` | Per-node dispatch replay ledger (gitignored, machine-local, advisory) — see "Replay Idempotency" |

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
