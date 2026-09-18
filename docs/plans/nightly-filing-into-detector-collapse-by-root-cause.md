---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-09-18
tracking: https://github.com/tomcounsell/ai/issues/3418
last_comment_id: 5729821863
---

# Nightly triage filing moves into the detector

## Problem

Tom opens the tracker the morning after a nightly regression run and finds the same test node filed
three times, ten minutes apart, under byte-identical titles. On the 2026-09-17 run
`scripts/nightly_regression_tests.py` reported "8 issue(s) filed" and dispatched exactly one triage
session. GitHub received 24 issues — #3382-#3389 at 20:54, #3390-#3397 at 21:04, #3398-#3405 at
21:08 — for those same 8 nodes. The 2026-09-16 run did the same thing at 2x
(#3355/#3365, #3356/#3366, #3357/#3367, #3358/#3368, #3359/#3369, #3360/#3370, #3361/#3371,
#3362/#3372, #3363/#3373, #3364/#3374).

**Current behavior:**

Filing and commenting are split across two actors with different guarantees. The detector
*comments* in Python: `dispatch_findings()` calls `comment_on_issue()` directly, and on the very
same 09-17 run it correctly routed #3375-#3380 to recurrence comments instead of twins. But the
detector *files* by proxy: for anything new it calls `maybe_dispatch_triage_session()`, which shells
out to `tools.valor_session create --role eng` and hands an LLM session a prompt asking it to run
`gh issue create`. There is no `gh issue create` call anywhere in the script.

That hand-off is where the guarantee is lost. An LLM session's turn can be replayed or continued
with a fresh context; each replay re-enters at the top of its instructions and re-runs the filing
loop. Every defense the detector had built against this — a live REST lookup instruction, a set of
pre-resolved `NodeDisposition` records, a seeded on-disk ledger — is an *input to a prompt*, and a
replayed turn that re-reads its prompt reads all of them as first-time instructions.

The tally compounds it. `DispatchOutcome.issues_filed` is incremented by `len(single_nodes)`
(`scripts/nightly_regression_tests.py:2966`) the instant the dispatch subprocess returns a session
id, and by 1 per cascade umbrella (`:2860`) — before any issue exists. The
`NIGHTLY_MAX_ISSUES_PER_RUN` budget is enforced against that number. A session that created 24
issues for 8 nodes was measured, budgeted, and logged as 8.

**Desired outcome:**

The deterministic detector creates the issues itself, at a Python call site, with the check and the
create adjacent and a convergent reconciliation behind them. The LLM session, if dispatched at all,
receives issue numbers that already exist and is permitted to comment and nothing else. The
per-run budget is enforced against issues GitHub confirms exist, not against a number the script
hoped was true. A run that finds K genuinely new root causes leaves exactly K issues behind, and a
re-run the same night leaves zero more.

## Freshness Check

**Baseline commit:** `3eea88a111b8df808f3c4a450c92500dd120a36a` (origin/main at plan time)
**Issue filed at:** 2026-09-18 (same day as this plan)
**Disposition:** Unchanged

**File:line references re-verified** against `scripts/nightly_regression_tests.py` at the baseline
commit — every pointer in the issue's Recon Summary still lands on the code it describes:

- `:305` `BODY_CASCADE_MIN_GROUP_SIZE_DEFAULT` — still 5, still env-overridable via
  `NIGHTLY_BODY_CASCADE_MIN_GROUP_SIZE`. Holds. (Now #3419's concern, not this plan's.)
- `:348-359` `ISSUE_LOOKUP_INSTRUCTION` — still carries "run exactly this ONCE for the whole list
  below" and the `--limit 200` cap. Holds, and is the wording this plan retires.
- `:1838` `group_body_failure_cascades()` — still keyed on `body_failure_signature()` alone. Holds.
- `:2065` `open_issues()` — still `gh issue list --state open --limit 1000`, still returns `None`
  on any failure and the callers still fail open. Holds.
- `:2128` `closed_issue_dispositions()` — still the `--limit 4000` closed read. Holds.
- `:2280` `comment_on_issue()` — still `gh issue comment --body-file -`. Holds; this is the
  sibling the new create function is modelled on.
- `:2450` `write_triage_ledger()` — still has the "leave a non-empty `filed` alone" guard, and is
  still only ever called once per dispatch. Holds; this confirms the recon's finding that the guard
  could never have fired for waves 1 and 2.
- `:2518` `maybe_dispatch_triage_session()` — still the only path from a new finding to an issue.
  Holds.
- `:2696` `dispatch_findings()` — still the single decision point; `:2860` and `:2966` still
  increment `issues_filed` pre-emptively. Holds.
- `:3265` — still logs `Tracker: {outcome.issues_filed} issue(s) filed`. Holds.

**Cited sibling issues/PRs re-checked:**

- #3170 — CLOSED. "Nightly triage filing must be idempotent against the session's own minutes-old
  issues (replayed turns re-file)." Its three defenses are the ones that did not hold here.
- #3131, #3134, #3075, #2559 — all CLOSED as completed. Prior passes, analyzed below.
- #3347 — still OPEN (quota exhaustion filed as regression). Link/keep target, not a dependency.
- #3243 — still OPEN (stamp hostname/session_id into filed bodies). Touches the same filing path
  this plan moves; see Prior Art for the coordination note.
- #3419 — OPEN, filed 2026-09-18 as the scope split for per-file collapsing. Not a blocker.

**Commits on main since the issue was filed (touching referenced files):** none.
`git log origin/main --since=2026-09-18 -- scripts/nightly_regression_tests.py
tests/unit/test_nightly_regression_tests.py` is empty, and `eae095035` (the recon baseline) is
still an ancestor of `origin/main`.

**Active plans in `docs/plans/` overlapping this area:** none. No other plan references
`nightly_regression_tests.py`.

**Bug still present:** confirmed by code read rather than reproduction — reproducing requires a
real red nightly suite plus a live GitHub repo to file into. The defect is structural and visible
statically: `grep -n "gh.*issue.*create" scripts/nightly_regression_tests.py` returns nothing, so
every new issue this system has ever opened was opened by an LLM session.

## Prior Art

Five closed issues attacked this exact failure and the detector still filed 24 issues for 8 nodes.

- **#2559** (CLOSED): "Nightly detector re-dispatches already-filed confirmed_failing nodes under a
  'Newly-confirmed' header, causing duplicate-issue churn." Added per-node dispatch suppression via
  the persisted `dispatched_nodes` set, so a node handed to triage once is never handed over again.
  Worked for the cross-*run* case; does nothing within a single run's session.
- **#3134** (CLOSED): "Nightly detector: comment on the open issue instead of filing a twin, and
  stop notifying." Added `open_issues()` returning `title -> number` and `partition_already_open()`,
  so a node with a live issue gets a recurrence comment. This is the half that *works* — and it
  works precisely because commenting is done in Python.
- **#3075** (CLOSED): "nightly triage re-files closed issues and files one issue per node."
  Added `closed_issue_dispositions()` / `partition_closed_matches()` (closed-state dedup),
  `group_body_failure_cascades()` (root-cause collapsing), and environmental classification.
- **#3131** (CLOSED): "detector floods the tracker: 26 issues from one poisoned xdist worker."
  Added setup-error cascade collapsing and the absolute `MAX_SETUP_ERRORS_DEFAULT` ceiling.
- **#3170** (CLOSED): "Nightly triage filing must be idempotent against the session's own
  minutes-old issues (replayed turns re-file)." The direct predecessor. Added the three defenses
  named in `write_triage_ledger`'s own docstring: (1) the `ISSUE_LOOKUP_INSTRUCTION` constant
  pointing the agent at the REST list endpoint instead of the lagging search index, (2) the
  pre-resolved `NodeDisposition` handoff so the agent confirms rather than re-derives, and (3) the
  seeded `data/nightly-triage-ledger/{slug}.json` with a `filed` array the agent appends to.

Two open issues to coordinate with:

- **#3243** (OPEN): "Nightly triage should stamp dispatching hostname and session_id into every
  issue body it files." Today that stamp would have to be an instruction in a prompt. Once filing
  is a Python function, the stamp is two f-string fields in one body builder. This plan does not
  implement #3243, but it creates the single call site where it becomes trivial, and the body
  builder introduced here is the natural home. Noted in the No-Gos.
- **#3347** (OPEN): "Nightly regression detector files quota exhaustion as code regressions."
  Orthogonal — it is about *which* findings deserve an issue, not about *who* creates it. The
  environmental-classification path it targets runs upstream of everything this plan touches and is
  left exactly as it is.

## Research

**Queries used:**

- GitHub REST API create issue idempotency key duplicate prevention

**Key findings:**

- GitHub's REST API supports no `Idempotency-Key` header, on the issues endpoint or anywhere else;
  GitHub's own best-practices documentation states that conditional requests on unsafe methods
  (POST/PUT/PATCH/DELETE) are not supported, and the standing community request
  (<https://github.com/orgs/community/discussions/192764>) has not moved in years. **This is the
  load-bearing constraint for `## Race Conditions`:** there is no compare-and-swap primitive
  available at the API boundary, so "make the check+create atomic" is not implementable as stated
  in the critique. The honest design is a *convergent* one, specified below.
- The recommended client-side substitute is a caller-generated deterministic token embedded in the
  created resource itself — for issues, an HTML comment in the body — so that "did I already file
  this?" becomes a content query with an exact, collision-free key rather than a title match. This
  plan adopts that pattern as `NIGHTLY_FINGERPRINT`.
- The same sources warn explicitly against adding retry logic to a non-idempotent create before the
  dedup layer exists: a retried POST is a second issue. This informs the error contract on the new
  create function — it reports failure and leaves the node unrecorded for the next *run*, and never
  retries within a run.
- Where duplicates cannot be prevented, the recommended posture is post-hoc convergence: detect on
  the next pass and close one with a pointer to the other. That is the reconciliation sweep in
  Task 3, and it is also what makes the historical #3382-#3397 cleanup a natural first exercise of
  the same code path.

Sources: [GitHub community discussion #192764](https://github.com/orgs/community/discussions/192764),
[Implementing Idempotency Keys in REST APIs (Zuplo)](https://zuplo.com/learning-center/implementing-idempotency-keys-in-rest-apis-a-complete-guide).

## Data Flow

Today, tracing one newly-confirmed failing node from pytest to GitHub:

1. **Entry point**: `main()` runs the suite, `reconfirm_serial()` strips xdist-only flakes,
   `compute_new_failures()` / `compute_dispatch_set()` reduce to nodes never handed to triage.
2. **`dispatch_findings()`** (`:2696`): environmental exclusion, then setup-error cascades, then
   body-failure cascades. One `open_issues()` read and one `closed_issue_dispositions()` read.
3. **Comment branch (Python, deterministic)**: `partition_already_open()` and
   `partition_closed_matches()` peel off nodes with an existing issue; `comment_on_issue()` posts
   the recurrence. `comments_posted` counts what actually posted. **This branch is correct.**
4. **File branch (LLM, non-deterministic)**: survivors become `NodeDisposition` records, are
   written to `data/nightly-triage-ledger/{slug}.json`, are rendered into a prompt, and the prompt
   is handed to `tools.valor_session create --role eng`. `issues_filed += len(single_nodes)`
   **at this point** — before the session has started, let alone created anything.
5. **Inside the session (unobserved)**: the agent runs its own `gh issue list --limit 200`,
   compares titles, runs `gh issue create` per survivor, and is asked to append each number to the
   ledger's `filed` array. On 09-17 the loop ran three times and the ledger ended up holding only
   wave 3's numbers, so waves 1 and 2 left no trace on disk at all.
6. **Output**: `main()` logs `Tracker: {issues_filed} issue(s) filed` and persists `recorded` into
   `dispatched_nodes`, suppressing those nodes on future runs. The number logged and persisted is
   step 4's guess.

After this plan, steps 4-6 become:

4. **File branch (Python, deterministic)**: for each survivor, re-read the open-issue map, compute
   the fingerprint, call `create_issue()`, capture the real number GitHub returns.
5. **Reconcile**: a single post-filing sweep reads back the issues created during this run's
   window, converges any duplicate fingerprint to its lowest-numbered issue, and yields the
   verified count.
6. **Output**: `issues_filed` is the verified count; the budget was enforced against it; the
   session, if dispatched, is handed the real numbers and a comment-only instruction.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #2559 | Persisted `dispatched_nodes` so a node handed to triage once is never handed over again. | Scoped to the gap *between* runs. The 09-17 waves were all inside one run, behind one dispatch, so the suppression set was never consulted a second time. |
| #3134 | `open_issues()` + `partition_already_open()`: comment on the live issue instead of filing a twin. | Worked, and still works — it is why #3375-#3380 were commented on the same night 24 twins were filed. It only ever covers findings that *already have* an issue; the first filing of a node is out of its reach by construction. |
| #3075 | Closed-state dedup, body-cascade collapsing, environmental classification. | Reduced how many findings reach the filing boundary. Changed the input to the broken step, never the step. |
| #3131 | Setup-cascade collapsing and an absolute setup-error ceiling. | Same shape: fewer things crossing the boundary, boundary unchanged. |
| #3170 | Three replay defenses: the `ISSUE_LOOKUP_INSTRUCTION` REST-list constant, the pre-resolved `NodeDisposition` handoff, and the seeded session ledger with its `filed` array. | **All three are prompt inputs.** A replayed or continued turn re-reads its prompt from the top and reads every one of them as a first-time instruction. The lookup instruction says "run exactly this ONCE for the whole list below", which binds a turn, not a session — so wave 2 at 21:04 acted on a lookup taken at 20:52 and could not see #3382-#3389, exactly as the session's own report describes. The dispositions say "the detector already decided: file" — which a replay obeys, again. And the ledger's `filed` array is maintained *by the agent*: the detector seeds it once at dispatch (`write_triage_ledger` is called from `maybe_dispatch_triage_session` and nowhere else), so its "leave a non-empty `filed` alone" guard at `:2450` could not possibly have fired mid-session, and waves 1 and 2 left no trace in it. |

**Root cause pattern:** every pass widened what the detector *knew* and left the *act of creating*
with an actor that cannot be bound by knowledge. An instruction to look before you file cannot
constrain a process that may re-enter at the top with no memory of having looked. The ledger is the
sharpest illustration: a defense whose upkeep is delegated to the actor it is defending against is
not a defense, it is a request. Five passes is enough evidence that the boundary, not the
instructions crossing it, is the defect.

**Why detector-side Python closes the gap.** The three properties the LLM path cannot offer are
exactly the three this change buys:

1. **Single-entry control flow.** `create_issue()` is reached by one call from one loop in one
   process holding the existing fcntl run lock (`TestRunLock`). There is no "replayed turn" — a
   crashed run does not re-enter its loop, it exits and the next night's run re-reads live GitHub
   state from scratch. The ten-minute wave window that produced #3382-#3405 has no analogue.
2. **Observed results instead of asserted ones.** The create call returns the issue number GitHub
   assigned. Every count downstream — the budget, the log line, `recorded` — is computed from
   numbers that exist, replacing a tally incremented on hope at `:2966`.
3. **Testability.** The 09-17 duplication happened inside a subprocess with no assertable surface;
   `tests/unit/test_nightly_regression_tests.py` could only ever assert that the *dispatch* was
   shaped correctly. Once the create is a module function, a test can call
   `dispatch_findings()` twice against one fake GitHub and assert the second pass creates nothing.
   That regression test is the thing none of the five prior passes could write.

Detector-side Python does **not** close the cross-machine or the pathological-retry window — no
GitHub primitive exists that would (see `## Research`). `## Race Conditions` specifies what is done
about the residue instead of claiming it away.

## Architectural Impact

- **New dependencies**: none. `gh` is already a hard dependency of this module, invoked by
  `comment_on_issue`, `open_issues`, and `closed_issue_dispositions`.
- **Interface changes**: one new module function `create_issue(title, body) -> int | None`
  (sibling of `comment_on_issue`). `DispatchOutcome` gains a field carrying the real issue numbers
  created this run. `maybe_dispatch_triage_session()`'s contract narrows from "file these" to
  "investigate these already-filed numbers"; `_build_triage_prompt` and the cascade/seed prompt
  builders change with it. `write_triage_ledger()` and `NodeDisposition` lose their reason to
  exist and are deleted, not deprecated.
- **Coupling**: net decrease. Today the correctness of the tracker depends on an LLM session
  faithfully executing a three-part instruction set; afterwards it depends on a Python function and
  the `gh` binary. The prompt-wording gates (`TestPromptsNeverNameTheSearchIndex`) exist only
  because prompt text was load-bearing; they shrink to covering the residual investigation prompt.
- **Data ownership**: issue creation moves from the triage session to the detector. The detector
  becomes the sole writer of new nightly issues; the session becomes a commenter, the same
  privilege level the detector's recurrence path already has.
- **Reversibility**: high for the mechanism, via a kill switch rather than a second code path — see
  `## Update System`. Reverting the *commit* is a clean revert; nothing persists a schema. The one
  irreversible artifact is the historical duplicate closures in Task 5, which are GitHub state, not
  code, and are reopenable by hand.
