# Nightly Triage Dispatch

Two additions to the nightly regression detector (`scripts/nightly_regression_tests.py`)
that decide when it runs and what it does with a finding: an advisory run lock, and the
filing/investigation split — the detector files every issue itself and comments on every
recurrence, then hands the issues it just created to an Eng session for root-cause
investigation and comment only.

The detector notifies nothing — the GitHub issue tracker is its only output surface
(#3134). A third addition shipped here originally, a best-effort LLM summarizer for
the Telegram alert text, and was deleted with the alert it existed to compose.

## Status

Shipped — Scope 1 of issue #2192 ("Nightly Regression Detector & Sentry Triage
Reflection — Dedupe, Readable Alerts, Auto-Triage"). Filing moved from the
triage session into the detector itself, with a per-body fingerprint and an
in-run collision report, in issue #3418.

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
two behaviors layered around that base run:

1. **Run lock** — prevents two overlapping launchd invocations from both running the
   suite and both filing against the same window.
2. **Filing, then investigation** — the detector is the sole creator of every nightly
   issue: per-node issues, cascade umbrellas, and the re-baseline seed umbrella all go
   through one function, `create_issue()`, called from a single Python loop inside the
   run lock. Once filing for the night is done, an Eng session is dispatched with the
   real issue numbers GitHub returned and a comment-only mandate: investigate and write
   a root-cause comment on each, add nothing else to the tracker.

### Filing (issue #3418)

`create_issue(title, body, *, dry_run=False)` shells `gh issue create --title ...
--body-file -` (body on stdin, for the same reason `comment_on_issue` takes its body
that way — a cascade body can carry a collapsed list hundreds of nodes long), parses
the issue number out of the URL `gh` prints on stdout, and returns `None` on any
failure — a non-zero exit, an unparseable or empty stdout, a timeout, a missing
binary. **It never retries.** GitHub's REST API has no `Idempotency-Key` header and no
conditional request on an unsafe method, so a retried create is not a retry, it is a
second issue. A `None` return spends no budget and leaves the finding out of
`recorded`, so the next run re-reads live GitHub state and files it then — the same
contract `comment_on_issue()` already has.

`dispatch_findings()` calls `create_issue()` from one loop, under the run's `fcntl`
lock (`TestRunLock`), for every survivor and every cascade umbrella. There is no
second actor and no replay window: a crashed run does not re-enter the loop, it exits,
and the next night's run starts over against fresh GitHub state.

For each finding, `_file_finding()` takes exactly one of five branches, each with its
own log line:

- **Refresh hit (comment)** — immediately before creating, the open-issue map is
  re-read. If the title is already open — an external actor filed it during this very
  loop — the finding is a genuine recurrence: `comment_on_issue()` posts against the
  refreshed number. This is a comment, never a silent skip, because dropping a real
  recurrence is worse than a duplicate (see below).
- **Degraded refresh (create anyway)** — the refresh read failed. Unknown is treated
  as "not open," matching the run's opening dedup posture: fail open, create, and log
  that tonight's dedup ran blind.
- **Fingerprint collision (skip)** — this run already filed the same fingerprint. Pure
  skip: the issue that fingerprint maps to was created by this run and is already
  counted.
- **Budget exhausted (defer)** — no budget left; the finding is deferred, unrecorded,
  to a later run.
- **Created** — `create_issue()` returns a real number, which is recorded and spends
  one unit of `NIGHTLY_MAX_ISSUES_PER_RUN`.

Only the created and refresh-hit-commented branches license the caller to mark the
finding `recorded`; only a create spends budget.

**Fingerprint.** Every body the detector creates ends with a hidden
`<!-- nightly-fingerprint: {sha256} -->` line, keyed on the finding's stable identity
— the node id for a per-node issue, the cascade's state key for an umbrella, and
`seed:{head_commit}` for a re-baseline seed — never on the rendered title, because a
title can be edited by a human or reworded by a future version of this script while
the identity underneath does not change. `finding_fingerprint()` / `fingerprint_marker()`
compute it; a re-baseline retry at the same commit collapses to the same fingerprint,
and a retry at a different commit does not.

**In-run collision report.** `dispatch_findings()` keeps an in-process
`dict[fingerprint, issue_number]` of everything it has created so far this run. A
second finding resolving to a fingerprint already in that dict is skipped and logged
by name — nothing more. **The detector creates and comments; it never closes an
issue.** There is no read-back after a collision, no `gh issue close`, no pointer
comment linking the twin to its survivor. That convergent sweep is deliberately
deferred pending real evidence of concurrent multi-host nightly runs (see the plan's
`## Decisions` #2 and `## No-Gos`) — do not "finish" the collision report into an
auto-close.

### Comment-only investigation session

`maybe_dispatch_triage_session(issues, *, slug_suffix=None, dry_run=False)` is called
once per `dispatch_findings()` pass, **after** every create for the night has already
happened, with `issues` a list of `(number, subject)` pairs — every number one
`create_issue()` call returned and GitHub confirmed. The dispatched Eng session's
prompt (`_build_investigation_prompt`) is unconditional: read each issue, investigate,
add a root-cause comment, and nothing else. No prompt this module renders contains an
issue-creation instruction; the module's search-index-avoidance wording
(`ISSUE_LOOKUP_INSTRUCTION`) survives only for whatever lookup the investigation
session still performs, never for filing.

Because the numbers are already real, nothing about this dispatch's outcome can add a
duplicate issue: a replayed or continued session turn here costs an extra comment at
worst, never a second issue. `--slug`, `--json`, and the 30-second subprocess timeout
work exactly as before — see the "Invocation contract" details that still apply,
below.

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
  - No caller may treat a returned session id as evidence that an issue exists: the
    issues were filed, and their numbers confirmed, before this function was ever
    called.
- **Dedup semantics for filing itself** are per node, not per set. `dispatched_nodes`
  in `data/nightly_tests_last_run.json` holds every node ID a previous run recorded as
  filed or commented; `compute_dispatch_set` subtracts it from the confirmed-failing
  set, so a node with an issue already open against it cannot reach a second create.
  - The delta and the filing decision answer **different questions**. The delta asks
    "is this a regression since last night" (`compute_new_failures`); filing asks
    "does this node already have an issue". Conflating them is what made a standing
    failure re-triage on every run that had any new failure, so #2429, #2430 and
    #2462 each opened an issue over the same dead watchdog node (issue #2559).
  - `carry_dispatched_nodes` persists the union of (previously dispatched ∩ still
    failing) and whatever this run recorded. A node that stops failing drops out, so
    a genuine re-regression is filable again later, and a **renamed** node retires
    itself with no special case: `df6097fe6` renamed the watchdog node the churn kept
    citing, and the old ID simply stops appearing in the confirmed set.
  - Only what actually reached the tracker is recorded. A failed create or a failed
    comment leaves its node unrecorded, so the next run retries it instead of
    silently swallowing the failure.
  - A **first (baseline) run** seeds `dispatched_nodes` with the confirmed set and
    files nothing per-node — see "Baseline seed" under
    `docs/features/nightly-regression-tests.md`. The baseline declares the known-failing
    state rather than reporting a finding, so without the seed the *next* run would
    file the entire standing set as fresh discoveries.
  - `dispatched_session_id` records the most recent successful investigation dispatch
    and is carried forward on runs that dispatch nothing.
- **Mandate**: investigate and comment only — never file, never close, never
  auto-hotfix. Auto-hotfixing nightly regressions is out of scope and explicitly
  called out as a No-Go in the originating plan
  (`docs/plans/nightly-regression-triage.md`).

## Post-merge operator step: close the historical duplicates (issue #3418)

Filing moved into the detector after five prior passes at replay-driven duplicate
filing failed; the last incident before the move produced 26 duplicate issues across
two nights (#3355–#3405). Their closure is a one-shot, irreversible operator action
against live tracker state, has no code dependency on this module, and is deliberately
kept outside the pipeline — a human runs it once, after merge, and stops-and-asks on
any surprise rather than retrying.

1. **Confirm the list is still accurate before touching anything.** Someone may have
   already closed or edited one of these issues by hand:

   ```bash
   for n in $(seq 3355 3405); do
     printf '%s\t' "$n"; gh issue view "$n" --json state,stateReason,title -q '[.state,.stateReason,.title]|@tsv'
   done
   ```

2. **Close the 09-17 duplicates: #3382-#3397** (waves 1 and 2), as `NOT_PLANNED`, each
   with a comment naming its surviving twin. **The survivors are #3398-#3405** (wave
   3) — the same 8 node titles, filed a third time. Pair each closure with its wave-3
   twin by matching the byte-identical title, not by arithmetic offset.
3. **Close the 09-16 duplicates**, keeping the **lower** number in each pair:
   #3365-#3374 are closed; #3355-#3364 survive.
4. **Verify**, with the same `seq 3355 3405` loop as step 1: #3365-#3374 and
   #3382-#3397 must report `CLOSED` / `NOT_PLANNED`, and #3355-#3364 and #3398-#3405
   must still be `OPEN`.

If this step is never run, the duplicates stay open and a morning's triage list stays
noisy, but nothing in the shipped filing path depends on their state:
`open_issues()` collapses same-title rows regardless.

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

### Lane reaping

Every investigation dispatch mints a `session/nightly-triage-*` branch and a
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
| `scripts/nightly_regression_tests.py` | Adds `_acquire_run_lock`, `create_issue`, and `maybe_dispatch_triage_session` around the existing detector; see `docs/features/nightly-regression-tests.md` for the base run mechanics |
| `data/nightly_tests.lock` | Advisory lock file for `_acquire_run_lock` (gitignored, empty — existence and the flock state are all that matter) |
| `data/nightly_tests_last_run.json` | Now also carries `dispatched_nodes` and `dispatched_session_id` alongside the existing delta-state fields |

`data/nightly-triage-ledger/` is inert: no code path reads or writes it. If the
directory exists on a machine, it is gitignored scratch that nothing consults, and no
automation deletes it. An operator who finds it can ignore or remove it freely; there
is no state in it to migrate.

## Design Decisions

**Advisory `flock`, not a Redis lock** — the nightly job already writes local JSON
state files rather than depending on Redis (see the base doc's "Local JSON state, not
Redis" decision). A lock file in the same `data/` directory keeps that pattern
consistent and needs no external dependency.

**The detector files; the session investigates** (issue #3418) — filing correctness
cannot depend on an actor whose turn can be replayed. `create_issue()` runs once per
finding, in one Python loop, under the run lock, and returns the number GitHub
actually assigned; the investigation session is dispatched only afterward, with those
confirmed numbers, and is permitted to comment and nothing else. See "Filing" above
for the fingerprint and collision-report mechanics that make a duplicate the same run
creates itself impossible, and "Post-merge operator step" for the one-shot cleanup
this change left behind.

**Investigation dispatch is fire-and-forget, not awaited** — the nightly script's job
is to detect, file, and record — not to babysit an investigation. The dispatch
subprocess call has a short timeout and any failure degrades to "no root-cause comment
for this finding," never a blocked or failed nightly run.

**Hash-based dedup over a run-count or time-based dedup** — the confirmed-failing set
is the signal that actually matters: two different failing sets should each get their
own investigation dispatch, but the same unresolved set showing up night after night
should not re-dispatch. A content hash captures that directly.

## Manual Testing

```bash
# Preview a full run including the filing and dispatch paths. --dry-run files
# nothing, posts no comment, and writes no state, but the filing and dispatch
# decision logic still executes if there are newly-confirmed failures.
python scripts/nightly_regression_tests.py --dry-run
```

`maybe_dispatch_triage_session()` can also be exercised directly against fake
`(number, subject)` pairs for a quick sanity check without running the full suite.

## See Also

- `docs/features/nightly-regression-tests.md` — the base detector this feature
  extends (run cadence, serial re-confirmation gate, delta computation, cascade
  collapsing, comment-over-create, and what each outcome produces)
- `docs/plans/nightly-regression-triage.md` — originating plan, including the No-Gos
  that keep the investigation session comment-only
- `docs/plans/nightly-filing-into-detector-collapse-by-root-cause.md` — the #3418
  plan that moved filing into the detector
- `docs/features/eng-session-architecture.md` — Eng session semantics for the
  dispatched investigation session
