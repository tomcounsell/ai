---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-09-18
revision_applied: true
revision_applied_at: 2026-09-18T14:56:53Z
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
create adjacent. The LLM session, if dispatched at all,
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
  the next pass and close one with a pointer to the other. That posture was drafted here as a
  reconciliation sweep and is **deferred** by owner decision (`## Decisions` #2, `## No-Gos`): the
  detector does not gain issue-closing privilege, and the residue after Race 1 and Race 2 is
  unobserved. What ships from this finding is only its *detection* half — the fingerprint, which
  makes a duplicate pair findable with an exact key by a human or by a future follow-up.

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

4. **File branch (Python, deterministic)**: for each survivor, re-read the open-issue map, then
   take one of four branches — (a) the refresh shows the title already open: post a recurrence
   comment via `comment_on_issue()` against the number the refresh returned and record the node;
   (b) the fingerprint was already filed by this same run: skip and log the collision; (c) the
   budget is exhausted: defer the node with its own log line and leave it unrecorded; (d) otherwise
   call `create_issue()` and capture the real number GitHub returns. Only (d) spends budget.
5. **Report collisions**: the run keeps an in-process registry of every fingerprint it filed. A
   second finding resolving to a fingerprint already filed this run is skipped and logged. Nothing
   is read back and nothing is closed.
6. **Output**: `issues_filed` is a derived length over the confirmed numbers; the budget was spent
   only on confirmed creates; the session is handed the real numbers and a comment-only
   instruction.

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
  irreversible artifact is the historical duplicate closures, which are GitHub state rather than
  code, are performed once by a human after merge from the enumerated runbook in `## Update System`,
  and are reopenable by hand. They are deliberately outside the task graph (`## Decisions` #4). The
  detector itself never closes an issue.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2, both spent (scope split to #3419 on 2026-09-18; the three residual judgement
  calls were answered by the owner on 2026-09-18, see `## Decisions`)
- Review rounds: 1-2 (this touches the module's most safety-critical function and deletes two
  public helpers; `TestDispatchFindings` / `TestDispositionHandoff` rewrites want a real read)

Medium rather than Small because the change deletes a mechanism (the ledger + disposition handoff)
rather than adding beside it, and the test file's four near-identical fake-`gh` harnesses each need
a new seam. Medium rather than Large because the code lives in one module, has no schema, no
migration, and no cross-machine coordination.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `gh` authenticated | `gh auth status` | The detector creates and comments through `gh`; the new create path fails closed without it |
| Repo resolves for `gh` | `gh repo view --json nameWithOwner -q .nameWithOwner` | `create_issue` inherits `cwd=PROJECT_DIR` targeting the same way `comment_on_issue` does |
| Repo venv has pytest | `test -x .venv/bin/pytest` | `scripts/pytest-clean.sh` aborts on a worktree `.venv` lacking `bin/pytest`; the whole change is gated on one test file, so a missing runner blocks everything |

## Solution

### Scope

This plan is **one** change, not two. The critique's Scope & Value blocker is resolved by an actual
split rather than a justification: on 2026-09-18 the owner narrowed #3418 to filing ownership and
filed the collapsing-algorithm work as **#3419**. The two are genuinely independent — #3418 changes
*who creates* an issue, #3419 changes *how many* issues a given night's findings should become — and
they touch disjoint functions (`dispatch_findings`'s filing branch vs.
`group_body_failure_cascades`). Nothing in this plan blocks #3419; the collapsing rule operates
upstream and feeds whatever filing path exists.

In scope, each with its own acceptance check:

| # | Sub-change | Acceptance check |
|---|-----------|------------------|
| (a) | **Filing moves into `scripts/nightly_regression_tests.py`.** A `create_issue()` function creates issues via `gh issue create`; `dispatch_findings()` calls it for every survivor and every cascade umbrella; the triage session never creates anything. | `grep -c "gh\", \"issue\", \"create\"` in the script is non-zero, and no prompt builder's output contains a create instruction (asserted by an updated `TestPromptsNeverNameTheSearchIndex` sibling). |
| (b) | **Duplicate filing is structurally prevented; the residue is reported, never converged.** Per-node re-read immediately before create, a deterministic fingerprint in every created body, and an in-process fingerprint registry that skips-and-logs a same-run collision. No read-back, no close. | A test calls `dispatch_findings()` twice against one in-memory fake GitHub and asserts the second pass creates zero issues and comments once per node; a second test asserts a same-run fingerprint collision is skipped with a named log line; a third asserts a mid-loop external file is **commented on**, not silently skipped. |
| (c) | **The budget is spent only on issues GitHub confirms exist.** `issues_filed` is a derived length over the real numbers returned; `NIGHTLY_MAX_ISSUES_PER_RUN` is decremented per confirmed create and keeps its current default. | A test with the cap set to a low value and a succeeding `create_issue` stub asserts exactly that many issues are created and the rest are deferred with a log line; a second test asserts a create returning `None` spends no budget and leaves its node out of `recorded`. |
| (d) | **[POST-MERGE OPERATOR STEP] The historical duplicates are closed.** #3382-#3397 and the enumerated 09-16 pairs are closed as duplicates pointing at their survivor. Not a pipeline task and not a merge gate — see `## Update System` → the post-merge runbook, and `## Decisions` #4. | `gh issue view` on each enumerated number reports `CLOSED` / `NOT_PLANNED`, run by the operator after merge. |

Out of scope and tracked elsewhere: per-file/root-cause collapsing (#3419), hostname/session_id
stamping (#3243), quota-exhaustion misclassification (#3347). See `## No-Gos`.

### Key Elements

- **`create_issue(title, body) -> int | None`**: the sibling `comment_on_issue` never had. Shells
  `gh issue create --title ... --body-file -` (body on stdin for the same reason the comment path
  does: a cascade body carries a collapsed node list that has been 278 entries long), parses the
  issue URL `gh` prints on stdout into an integer, and returns `None` on any failure. **Never
  retries** — a retried non-idempotent create is a second issue (see `## Research`). A `None`
  return leaves the node out of `recorded`, so the next run retries it against fresh GitHub state,
  matching `comment_on_issue`'s existing contract exactly.
- **Filing fingerprint**: every body the detector creates carries a hidden
  `<!-- nightly-fingerprint: {sha256} -->` line derived from the finding's stable identity (the
  node id for a per-node issue, the cascade state key for an umbrella). Titles can be edited by
  humans; the fingerprint cannot drift. It is what makes "is this a twin?" an exact-match question rather
  than a string-similarity one — for the in-run registry today, for a human grepping the tracker
  the morning after, and for the deferred sweep if evidence ever reopens it.
- **Pre-create re-read**: the open-issue map is refreshed immediately before each create rather
  than once per run, shrinking the check-then-act window from the whole filing loop to one call.
  **A hit on that refresh comments, it does not merely skip.** The refresh is not a suppression
  check — it is the same question the run's opening `open_issues()` read asked, asked later, so it
  gets the same answer: route the finding through `comment_on_issue()` against the number the
  refresh just returned, exactly as `partition_already_open()` already does for the opening read.
  Skipping silently here would drop a legitimate recurrence comment, which `## Risks` Risk 1 names
  as strictly worse than a duplicate. This is the one place the refresh differs from the
  fingerprint-collision check, which *is* a pure skip because the issue it collides with was
  created by this very run and has already been counted.
- **Fingerprint-collision report**: the filing loop keeps an in-process `dict[fingerprint, int]` of
  what it created. A second finding resolving to a fingerprint already filed this run is skipped
  and logged by name. That is the whole of it — no GitHub read-back, no `gh issue close`, no
  pointer comment. The detector's privilege set stays exactly what it is today: create and comment.
  The convergent sweep that would close a twin is deferred (`## Decisions` #2, `## No-Gos`).
- **Comment-only investigation session** (kept — owner decision, `## Decisions` #1):
  `maybe_dispatch_triage_session()` is dispatched *after*
  filing, with real issue numbers, and its prompts instruct investigation and commenting only. The
  three filing prompts become one investigation prompt shape.
- **Deletions**: `write_triage_ledger()`, `NodeDisposition`, the ledger paragraph in
  `_build_triage_prompt`, and the filing halves of `ISSUE_LOOKUP_INSTRUCTION`. Per this repo's
  no-legacy-code rule these are removed outright, not left behind a flag.

### Flow

Nightly run completes → serial re-confirm → `dispatch_findings()` → collapse (environmental,
setup cascades, body cascades) → **comment branch** (existing, unchanged: open-issue and
closed-not-planned recurrences) → **file branch (new)**: for each survivor → refresh open map →
**on a hit, comment via `comment_on_issue()` and record; on a miss** → compute fingerprint →
skip-and-log if that fingerprint was already filed this run → skip-and-log if the budget is spent →
otherwise `create_issue()` → record real number → decrement budget → derived `issues_filed` →
**dispatch investigation session** with the real numbers and a comment-only instruction →
log `Tracker: N issue(s) filed` where N is the count of numbers GitHub returned.

### Technical Approach

- **`create_issue` mirrors `comment_on_issue` deliberately.** Same `subprocess.run` shape, same
  `cwd=PROJECT_DIR`, same `--body-file -` stdin discipline, same "log and return falsy, never
  raise" error posture, same `dry_run` short-circuit. A reviewer should be able to diff the two
  functions and see only the verb change. This is also why the four fake-`gh` harnesses in the test
  file can stub it the same way they already stub `comment_on_issue`.
- **Where the create loop goes.** Replacing the block at `:2941-2970`. The comment at `:2954-2965`
  explaining why `dispositions` is withheld on a degraded read disappears with the mechanism it
  documents; the *reasoning* it encodes does not, and it moves to the budget contract below.
- **One failure posture, and no second read to disagree with it** (this must be stated in code
  comments as well as here):
  - The **dedup reads** (`open_issues`, `closed_issue_dispositions`) keep failing *open*. An
    unreadable GitHub on the night of a real regression must not produce a silent night; a possible
    duplicate is the smaller harm. Unchanged from today.
  - There is **no post-filing verification read**. An earlier draft placed one inside the
    reconciliation sweep with a fail-closed posture; with the sweep deferred (`## Decisions` #2) the
    count is already exact at the moment of each create, and a second read could only introduce a
    way for the exact number to be second-guessed by a laggy list endpoint. What replaces the
    fail-closed posture is stronger and simpler: an unconfirmed create is never counted, never
    spends budget, and never enters `recorded`, so tomorrow's run picks the node up. Silently
    assuming "0 filed so far" — exactly how #3382-#3405 happened — is unrepresentable when the
    count is a derived length over observed numbers.
- **Budget accounting is per-confirmed-create.** `issue_budget` decrements only when
  `create_issue()` returns a number. A failed create spends nothing. This alone removes the
  `:2966` class of bug on its own.
- **`DispatchOutcome`** gains `filed_issues: dict[str, int]` (finding key → real issue number),
  and `issues_filed` becomes a derived length rather than an independently-mutated counter — the
  two can then never disagree. This single change is what retires the `:2966` defect; everything
  else in this plan protects it. `cascade_issues` stops needing its `None` "pending, the session will
  open it" sentinel, because the number is known at creation time; `carry_cascade_issues()`'s
  upgrade-on-a-later-run path becomes dead and is removed with it.
- **Prompt consolidation.** The per-node, cascade-umbrella, and re-baseline-seed prompts exist as
  three shapes because each had to teach an agent a different filing contract. With filing gone
  they collapse toward one investigation prompt that takes a list of `(number, subject)` pairs.
  `ISSUE_LOOKUP_INSTRUCTION`'s dedup-before-filing framing is removed; if any lookup guidance
  survives at all it keeps the search-index prohibition, so the two existing wording gates stay
  meaningful rather than being deleted along with the text they guard.
- **Tunables carry no new invented numbers.** With the sweep deferred there is no reconciliation
  window and no new threshold to invent. The only new knob is the `NIGHTLY_AUTO_FILE` boolean kill
  switch, which defaults to on. Any constant that does appear follows the module's established
  convention — `_DEFAULT` suffix, `NIGHTLY_*` env override, and a comment stating the value is
  provisional and what evidence would move it.
- **`NIGHTLY_MAX_ISSUES_PER_RUN` keeps its current default** (owner decision, `## Decisions` #3).
  The issue's proposed cull asked to lower it; the 09-17 overrun was a broken measurement, not a
  too-generous cap, and lowering the cap while the measurement was wrong would only have deferred
  real regressions behind a number that was never being honoured anyway. This plan changes what the
  budget is measured against, not how large it is.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `create_issue()` has one broad `except Exception` (matching `comment_on_issue` and
  `open_issues`, which catch `TimeoutExpired` / `FileNotFoundError` / parse errors alike). It must
  not be a bare `pass`: assert it logs a `WARNING` naming the title and returns `None`, and assert
  the caller leaves the node out of `recorded`. Test: `create_issue` with a subprocess raising
  `FileNotFoundError`, and separately with a non-zero return code, and separately with stdout that
  is not a parseable issue URL.
- [ ] The fingerprint-collision check introduces no new exception surface: it is a dict lookup over
  in-process state, with no I/O and nothing to fail. Assert that a collision produces a skip plus a
  `WARNING`, and that the run continues to the next survivor rather than aborting the loop.
- [ ] No other exception handlers are introduced in scope.

### Empty/Invalid Input Handling
- [ ] `create_issue("", body)` and `create_issue(title, "")` — a create with an empty title must be
  refused before shelling out, not passed to `gh`. Test asserts no subprocess call and a `None`
  return.
- [ ] Empty survivor list: `dispatch_findings()` with nothing to file must still shell out to `gh`
  zero times. `TestDispatchFindings::test_a_clean_night_never_shells_out_to_gh` (line 1377) already
  pins this and must keep passing against the new create path.
- [ ] `gh issue create` stdout that is whitespace-only or a URL with no trailing integer → `None`,
  not a crash and not a bogus issue number.
- [ ] Not applicable: agent output processing. The detector no longer depends on anything the agent
  produces, which is itself part of the fix.

### Error State Rendering
- [ ] The run's log line is the only user-visible surface (this module deliberately has no notifier
  — `TestNothingNotifies`, line 629). Assert that a night where some creates failed logs both the
  verified filed count and the deferred count, so a partial failure is legible rather than reading
  as a quiet success.
- [ ] Assert that each of the four non-filing outcomes emits its own distinct log line naming the
  nodes it concerns — **budget exhausted**, **create failed**, **fingerprint collision**, and
  **pre-create refresh hit (commented instead)** — so they are distinguishable from each other, and
  from a genuinely quiet night, in `logs/` the morning after. A single shared "not filed" line would
  collapse four different operator responses (raise the cap / check `gh` auth / investigate a
  collapsing bug / nothing, this one is working as designed) into one.
- [ ] The refresh-hit branch must not be able to fail silently: if the refresh returns a number but
  the recurrence comment fails to post, the node stays out of `recorded` — the existing
  `comment_on_issue` contract — and the failure is logged as a comment failure, not swallowed into
  the refresh-hit line.

## Test Impact

`tests/unit/test_nightly_regression_tests.py` (3355 lines). Per-function dispositions:

**Shared harness (do these first — everything else depends on them):**
- [ ] `_assert_per_node_dispatch` (line 29) — REPLACE: today it asserts the dispatch call carries
  one `NodeDisposition(disposition="file")` per node. With `NodeDisposition` deleted it becomes an
  assertion over the *created issue numbers* handed to the investigation dispatch. This single
  helper is the highest-leverage edit in the file.
- [ ] `_disposition` (line 818) — DELETE: constructs the removed `NodeDisposition`.
- [ ] `TestDispatchFindings._dispatch` (1241), `TestDispositionHandoff._run` (1406),
  `TestClosedIssueDedup._dispatch` (2580), `TestReviewFindings3142._dispatch` (2800) — UPDATE all
  four: each monkeypatches `open_issues`, `closed_issue_dispositions`, `comment_on_issue`,
  `maybe_dispatch_triage_session` and none patches a create. Each must stub `create_issue`, or a
  real `gh issue create` escapes into a subprocess during unit tests. Prefer extracting the four
  near-identical copies into one shared fake-GitHub fixture while touching them.
- [ ] `TestMainDispatchPersistence._run_main` (1832) / `._dry_run_main` (1952),
  `TestPersistedStateKeyInvariance._run` (2464), `TestFatalPathIntegration._base_patches` (2316) —
  UPDATE: add `create_issue` to each `patch.object` block.
- [ ] `_ledger_dir` (1742 and 3271, duplicated) — DELETE both.

**`TestWriteTriageLedger` (3266) — DELETE the entire class** (`test_happy_path_shape_and_absolute_return` 3276, `test_empty_entries_writes_nothing_and_returns_none` 3289, `test_an_unwritable_target_logs_a_warning_and_returns_none` 3297, `test_a_live_sessions_appends_are_never_re_seeded_away` 3310, `test_an_untouched_ledger_with_no_filings_is_re_seeded` 3331, `test_the_write_is_atomic_and_leaves_no_temp_file` 3341, `test_a_corrupt_existing_ledger_is_overwritten_rather_than_raising` 3347): the ledger exists only to let an agent remember what it filed. No agent files.

**`TestDispositionHandoff` (1388) — REPLACE the entire class.** Its subject (what `dispositions=` reaches the agent) ceases to exist. Its four tests map onto new assertions about what reaches `create_issue`:
- [ ] `test_dispositions_cover_the_survivors_and_nothing_already_tracked` (1427) — REPLACE: assert `create_issue` is called exactly once, for the survivor, with the exact `Nightly regression: {node}` title and a body carrying the fingerprint.
- [ ] `test_no_dispositions_when_open_read_fails` (1459) — REPLACE: assert the degraded-read posture *for creates* — unreadable open map still files (fail-open preserved), and the log says so.
- [ ] `test_no_dispositions_when_closed_read_fails` (1487) — REPLACE: same, for the closed read.
- [ ] `test_cascade_dispatch_is_handed_no_dispositions` (1514) — REPLACE: assert the cascade umbrella is created by `create_issue`, not dispatched.

**`TestDispatchFindings` (1219) — UPDATE every test; the class keeps its subject.**
- [ ] `test_night_one_files_one_issue_and_records_the_signature_as_pending` (1268) — UPDATE + RENAME: `cascade_issues` maps to a real number now, not `None`. "as_pending" leaves the name.
- [ ] `test_night_two_comments_instead_of_filing_a_second_issue` (1279) — UPDATE: assert `create_issue` not called.
- [ ] `test_a_comment_that_failed_to_post_leaves_the_finding_unrecorded` (1297) — UPDATE: unchanged in intent; add the sibling assertion that a failed *create* also leaves the finding unrecorded.
- [ ] `test_per_node_recurrence_is_commented_not_suppressed` (1313) — UPDATE: the "other node" is now created, not dispatched.
- [ ] `test_comments_do_not_spend_the_issue_budget` (1329) — UPDATE: budget now decrements per confirmed create.
- [ ] `test_cascades_only_suppresses_per_node_filing` (1344) — UPDATE: `cascades_only` now suppresses per-node *creates*; the `cascade:` pseudo-node assertion goes away with the dispatch.
- [ ] `test_a_clean_night_never_shells_out_to_gh` (1377) — UPDATE: extend to assert `create_issue` is also never reached.

**`TestMaybeDispatchTriage` (1633) — UPDATE the class, DELETE its ledger tests.**
- [ ] `test_real_per_node_dispatch_seeds_a_ledger_and_names_it_in_the_message` (1747),
  `test_dry_run_writes_no_ledger` (1764), `test_prompt_override_dispatch_writes_no_ledger` (1782),
  `test_a_failed_ledger_write_does_not_stop_the_dispatch` (1801) — DELETE all four.
- [ ] `test_dispatch_once` (1661), `test_literal_titles_in_prompt` (1679),
  `test_prompt_override_replaces_default` (1693), `test_slug_suffix_override` (1703) — UPDATE: the
  dispatch now carries issue numbers; `test_literal_titles_in_prompt` becomes
  `test_literal_issue_numbers_in_prompt`.
- [ ] `test_dry_run_spawns_no_session` (1646), `test_subprocess_failure_safe` (1712),
  `test_session_id_parsed_from_json_stdout` (1718), `test_malformed_stdout_returns_none_not_crash`
  (1724), `test_empty_stdout_returns_none_not_crash` (1730), `test_empty_dispatch_set_no_dispatch`
  (1736) — KEEP unchanged: subprocess plumbing, orthogonal to who files.

**`TestBuildTriagePrompt` (828) — REPLACE as the investigation-prompt class.**
- [ ] `test_literal_titles_present` (831) — REPLACE: assert real issue numbers, and assert the
  prompt contains no create instruction.
- [ ] `test_dispositions_render_the_detectors_own_finding_per_node` (846) — DELETE.
- [ ] `test_empty_disposition_list_degrades_to_the_plain_prompt` (855) — DELETE.
- [ ] `test_wrong_length_disposition_list_raises` (866) — DELETE.
- [ ] `test_ledger_paragraph_is_emitted_only_for_a_non_none_path` (873) — DELETE.

**`TestPromptsNeverNameTheSearchIndex` (3139) — UPDATE, do not delete.**
- [ ] `_prompts` helper (3154) and the five tests (3169, 3176, 3182, 3188, 3191) — UPDATE to the
  surviving prompt shape(s). The search-index prohibition stays meaningful for any lookup the
  investigation session still performs. Add one new sibling assertion: **no surviving prompt
  contains an issue-creation instruction.** That is the anti-criterion that keeps filing from
  drifting back into the agent.

**`TestPreFileDedup` (1024) — UPDATE.**
- [ ] `test_already_open_titles_are_paired_with_their_issue_number` (1031),
  `test_unreadable_open_set_fails_open` (1038),
  `test_open_issues_uses_the_rest_list_not_the_lagging_search` (1043),
  `test_open_issues_returns_none_on_any_failure` (1065) — KEEP; `open_issues` is unchanged. ADD a
  sibling `TestCreateIssue` class covering argv shape, URL parsing, dry-run, empty title, and each
  failure mode — mirroring these tests one-for-one.

**Unchanged classes** (no filing coupling): `TestLoadLastRun`, `TestSaveLastRun`,
`TestExtractFailingNodeIds`, `TestSpawnPytest`, `TestRunTests`, `TestValidateRunIntegrity`,
`TestReconfirmSerial`, `TestNothingNotifies`, `TestFatal`, `TestRunLock`, `TestComputeDispatchSet`,
`TestComputeNewFailures`, `TestCarryDispatchedNodes`, `TestGroupSetupErrorCascades`,
`TestResolveIntKnob`, `TestRecurrenceComments`, `TestResolveCascadeIssue`, `TestRunTtftGate`,
`TestLoadEnvOrDie`, `TestSpawnPytestCwdSeam`, `TestEnvironmentalClassification`,
`TestBuildSeedPrompt` (3197), `TestBodyFailureGrouping` (2495 — this is #3419's territory, untouched
here).

**Classes needing targeted updates:**
- [ ] `TestCarryCascadeIssues` (1199) — UPDATE: the `None`-sentinel upgrade path is removed.
- [ ] `TestHandleIntegrityTrip` (1531) — UPDATE: the integrity trip still files the cascade, now via
  `create_issue`.
- [ ] `TestClosedIssueDedup` (2577) — UPDATE: `issues_filed` assertions at 2618 and 2634 now count
  confirmed creates.
- [ ] `TestReviewFindings3142` (2797) — UPDATE: `test_end_to_end_replay_dispatch_shapes` (3073) and
  the `issues_filed` assertions at 3071/3129 are the closest existing thing to a replay test;
  rewrite against the create path rather than the dispatch path.

**New tests (the ones no prior pass could write):**
- [ ] `TestFilingIdempotence` — NEW: run `dispatch_findings()` twice against one in-memory fake
  GitHub whose create mutates the open-issue map. Assert pass two creates zero issues and comments
  once per node. This is the regression test for #3382-#3405 and for the whole #3170 class.
- [ ] `TestIssueBudgetSpendsOnlyConfirmedCreates` — NEW: cap set low, assert exactly the cap is
  created and the remainder is deferred, logged, and left out of `recorded`; and a second test where
  `create_issue` returns `None`, asserting that create spends no budget and its node stays out of
  `recorded` so the next run retries it.
- [ ] `TestPreCreateRefreshComments` — NEW: a fake GitHub whose `open_issues` returns an empty map
  on the run's opening read and a map containing the survivor's title on the pre-create refresh.
  Assert `create_issue` is not called, `comment_on_issue` **is** called once against the refreshed
  number, the node lands in `recorded`, and the refresh-hit log line fires. A sibling test makes
  that comment fail and asserts the node stays out of `recorded`.
- [ ] `TestFingerprintCollisionIsLogged` — NEW: two survivors in one run resolving to the same
  fingerprint; assert exactly one `create_issue` call, a `WARNING` naming the fingerprint and the
  skipped node, and — the anti-assertion that pins the owner's report-only decision — that no
  `gh issue close` is ever shelled out and no close helper exists to call.

**Test-run discipline:** per this repo's rules, run only
`scripts/pytest-clean.sh tests/unit/test_nightly_regression_tests.py`, never the full `tests/unit/`
tree (about 20 minutes, and parallel lanes collide on Redis state).

## Rabbit Holes

- **Making the create genuinely atomic.** It cannot be. GitHub offers no idempotency key and no
  conditional POST (`## Research`). Any design that reaches for a distributed lock, a mutex issue,
  or a "claim" label written before the create is spending real time to buy a smaller window, not a
  closed one. Refresh-then-create is the ceiling this plan buys; take it and move on. The
  convergent sweep that would clean up behind it is deferred on evidence, not on difficulty
  (`## No-Gos`).
- **Rewriting the cascade/collapsing logic.** `group_body_failure_cascades` and its threshold are
  #3419. Touching them here re-merges the scope the owner just split.
- **Generalizing `create_issue` into a repo-wide GitHub client.** Several modules shell out to `gh`.
  A shared client is a reasonable idea and a different project; here it is one function modelled on
  the `comment_on_issue` two hundred lines above it.
- **Landing #3243's hostname/session_id stamp "while we're in there."** The body builder this plan
  creates is where that stamp belongs, which makes it tempting. It is a separate issue with its own
  acceptance criteria and it will be a five-line change afterwards.
- **Perfecting the investigation prompt.** The session's value after this change is root-cause
  narrative, not correctness. A merely adequate comment-only prompt is fine; iterating on its
  wording is unbounded and unmeasured.
- **Auditing every historical duplicate ever filed.** The post-merge runbook in `## Update System`
  closes the enumerated set from the issue body and nothing else. A general sweep of the tracker's
  whole duplicate history is a different job.

## Risks

### Risk 1: Over-suppression — a real regression is never filed
**Impact:** The failure mode this change could introduce is the mirror of the one it fixes. If the
fingerprint or the pre-create re-read matches too eagerly, a genuinely new finding is silently
treated as already-filed and nothing reaches the tracker. That is strictly worse than a duplicate:
a duplicate is noise, a missed regression is a silent hole.
**Mitigation:** The fingerprint is derived from the finding's exact stable identity (node id /
cascade state key), so it cannot match across distinct findings by construction. The dedup reads
keep their existing fail-open posture — an unreadable GitHub files rather than stays silent. Nodes
whose create fails stay out of `recorded` and are retried by the next run rather than suppressed.
`TestFilingIdempotence`'s second pass asserts comments are still posted, so "suppressed" is always
distinguishable from "silent" in the log.

### Risk 2: `gh issue create` stdout parsing is brittle
**Impact:** The real issue number is now load-bearing for the budget, for `cascade_issues`, and for
the investigation dispatch. If the URL-to-integer parse is wrong or `gh` changes its output, every
create reports `None`, every night reports zero filed, and nothing is ever recorded — an outage
that looks like a quiet success.
**Mitigation:** `gh issue create` accepts `--json number` on current versions; prefer that over
scraping the URL, and fall back to a strict trailing-integer parse of the printed URL. Cover
whitespace-only stdout, a URL with no trailing integer, and a valid URL in `TestCreateIssue`. The
"some creates failed" log line from `## Failure Path Test Strategy` makes a total parse failure
loud on the first night rather than silent.

### Risk 3: The test rewrite is large enough to hide a behavior change
**Impact:** Roughly 25 test functions are deleted or replaced. A rewrite that quietly drops an
assertion — particularly the closed-state dedup or the environmental-exclusion coupling — removes a
guard that five prior issues paid for.
**Mitigation:** Every deletion in `## Test Impact` is justified by the disappearance of its subject
(the ledger, `NodeDisposition`), never by inconvenience. The unchanged-class list is explicit so a
reviewer can check that nothing outside it moved. The `cruft-auditor` agent runs over the diff
during review specifically for dropped assertions.

### Risk 4: The investigation session, stripped of filing, has no remaining value
**Impact:** Dispatching an LLM session per night that only comments may be pure cost — a
possibility worth naming rather than discovering in three months.
**Mitigation:** Raised as an explicit question and **answered**: the owner ruled on 2026-09-18 that
the comment-only session stays, as the issue's own proposed cull describes (`## Decisions` #1). The
value it is kept for is root-cause narrative on a fresh issue, not correctness — correctness is now
entirely the detector's. That makes the session's worth measurable after the fact rather than
argued in advance: if a quarter's worth of nightly investigation comments turn out to be read by
nobody, deleting `maybe_dispatch_triage_session` is a strictly smaller follow-up change than
keeping it, because filing no longer depends on it.

## Race Conditions

The bug this plan fixes **is** a check-then-act race, so this section is the load-bearing one.
`/do-plan-critique` asked for "an atomic check+create". The honest finding from `## Research` is
that no atomic check+create exists at this boundary: GitHub's REST API supports no
`Idempotency-Key`, no conditional POST, and no unique constraint on issue titles. The design below
therefore does three things in order — **eliminate** the race that actually fired, **shrink** the
one that cannot be eliminated, and **converge** whatever survives — and says plainly which is which
rather than claiming atomicity it cannot deliver.

### Race 1: Replayed LLM turn re-runs the whole filing loop — ELIMINATED
**Location:** `scripts/nightly_regression_tests.py:2941-2970` (the dispatch hand-off) and the
prompt constants at `:348-359`.
**Trigger:** One triage session's turn is replayed or continued with a fresh context. The agent
re-reads its prompt from the top, re-runs `gh issue list` *or reuses the answer from its first
lookup*, and re-executes the create loop. Observed three times in eighteen minutes on 2026-09-17,
producing #3382-#3389, #3390-#3397, #3398-#3405.
**Data prerequisite:** The set of issues the *previous wave of the same session* created must be
visible to the current wave before it decides to create. It was not: the session's report shows it
acting on a lookup that saw only up to #3380.
**State prerequisite:** The filing loop must execute at most once per finding per run.
**Mitigation — elimination, not mitigation.** The loop moves into `dispatch_findings()`, inside a
single Python process already holding the module's fcntl run lock (`TestRunLock`, line 672). A
Python loop has no replay semantics: if the process dies mid-loop it exits, and the next night's
run re-reads live GitHub state from scratch and finds the issues the dead run created. There is no
mechanism by which this loop runs twice against one finding set. This race is not made smaller; it
ceases to have a trigger. `TestFilingIdempotence` pins it.

### Race 2: Read-to-create window within one run — SHRUNK AND BOUNDED
**Location:** the new create loop replacing `:2941-2970`.
**Trigger:** `dispatch_findings()` reads the open-issue map once (`:2696` onwards), then files
survivors. Between the read and the Nth create, an external actor — a human, another repo
automation, #3419's future collapsing path — opens an issue with a title this loop is about to
file.
**Data prerequisite:** the open-issue map must reflect GitHub at the moment of each create, not at
the moment the run started.
**State prerequisite:** each finding maps to at most one live issue.
**Mitigation:** the open-issue map is refreshed immediately before each create rather than once per
run. The window shrinks from "duration of the entire filing loop" (minutes, with N `gh` round-trips
inside it) to "one `gh` round-trip". This is a genuine reduction and not a closure; the residue is
reported rather than converged (Race 3). Cost is one extra `gh` read per created issue, which is bounded by
`NIGHTLY_MAX_ISSUES_PER_RUN` and therefore small.
**What a hit does — comment, never a bare skip.** A refresh that finds the title already open means
an external actor filed it during this loop, so the finding is a *recurrence*, not a duplicate of
something this run created. It routes through `comment_on_issue()` against the number the refresh
returned, reusing the shape `partition_already_open()` (`scripts/nightly_regression_tests.py:2397-2425`,
per-node caller `:2881-2889`) already applies to the run's opening read, and the node is recorded on
a successful comment exactly as the existing recurrence path records it. A bare skip would leave the
night with neither an issue nor a comment for a real finding — the silent hole `## Risks` Risk 1
calls worse than a duplicate, merely relocated into the smaller window. This is the fourth non-filing
outcome and it carries its own log line (`## Failure Path Test Strategy`).

### Race 3: Two hosts, or a create whose response was lost — REPORTED, CONVERGENCE DEFERRED
**Location:** the new `create_issue()` call site.
**Trigger:** Two machines run the nightly against the same repo and both pass the pre-create
re-read before either create lands; or one `gh issue create` succeeds server-side but its response
is lost, so the client cannot tell whether an issue exists.
**Data prerequisite:** a duplicate must be *detectable* after the fact with an exact key. Titles are
not that key — humans edit titles, and the cascade path already deliberately keys on a signature
rather than a title (`resolve_cascade_issue`, `scripts/nightly_regression_tests.py:2366`, for
exactly this reason).
**State prerequisite:** eventually, exactly one live issue per finding.
**Disposition — detect and report; do not converge.** The owner ruled on 2026-09-18
(`## Decisions` #2) that the detector does not gain issue-closing privilege and that the convergent
sweep is deferred until concurrent multi-host nightly runs are actually observed. Three things ship:
1. **Deterministic fingerprint.** Every body the detector creates carries
   `<!-- nightly-fingerprint: {sha256 of the finding's stable identity} -->`. Two hosts filing the
   same finding produce byte-identical fingerprints without coordinating. This is the *detection*
   half of the convergence pattern, and it is the half with lasting value: it is what makes a
   duplicate pair findable with an exact key, by a human grepping the tracker or by the deferred
   sweep if it is ever built.
2. **In-run collision report.** The filing loop keeps a `dict[fingerprint, int]` of what it created
   this run. A second finding resolving to an already-filed fingerprint is skipped and logged by
   name. This is a dict lookup over in-process state — no GitHub read-back, no `gh issue close`, no
   pointer comment, and no new failure surface.
3. **Never retry a create.** A create whose response was lost is left alone within the run; the
   *next night's* existing `open_issues` dedup read resolves it into a recurrence comment rather
   than a twin. Retrying a non-idempotent POST is how a lost response becomes a second issue
   (`## Research`).
**Residual risk, stated plainly.** A genuine cross-host duplicate survives as two open issues until
a human closes one; the next night's dedup read will comment on the lower-numbered one rather than
file a third. That is a two-issue outcome against the 24-issue outcome this plan exists to end, for
a trigger — two hosts running this nightly concurrently — that has never been observed in this
system's operational record. Both motivating incidents (09-17's three waves, 09-16's ten pairs) were
single-run LLM replays, which Race 1 eliminates outright. Buying convergence for the unobserved
residue would mean building the detector's first autonomous issue-closing path; the evidence bar
that would justify that is written down in `## No-Gos`.

### Race 4: The budget spends against a count nothing confirmed — CLOSED BY CONSTRUCTION
**Location:** `scripts/nightly_regression_tests.py:2966` and `:2860` today; the new create loop
after this change.
**Trigger:** `issues_filed` is incremented by `len(single_nodes)` the instant the dispatch
subprocess returns a session id, and by 1 per cascade umbrella — before any issue exists. The cap
is then enforced against that hoped-for number. A run that created 24 issues was budgeted as 8.
**Data prerequisite:** the number the budget spends must come from GitHub, not from the caller's
intention.
**State prerequisite:** `issues_filed` ≤ the number of issues that exist.
**Mitigation — the counter stops existing.** `issue_budget` decrements only when `create_issue()`
returns a real number, and `issues_filed` becomes a derived length over
`DispatchOutcome.filed_issues` rather than an independently mutated integer. There is no second
number that could disagree with the first, which is why this is a closure rather than a mitigation.
There is also **no post-filing verification read**: an earlier draft placed one inside the
reconciliation sweep with a fail-closed posture, and with the sweep deferred (Race 3) the count is
already exact at the moment of each create — a laggy list endpoint could only add a way to
second-guess an exact number. A create whose response is lost returns `None`, spends no budget, and
leaves its node out of `recorded`, so the next run retries it against fresh GitHub state. The one
posture that remains asymmetric is the dedup reads' fail-open (see `## Solution` → Technical
Approach): silence during a real regression is the larger harm there.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3419] **Per-file / per-package collapsing by root cause.** Folding the ~22
  `test_improvement_eval_*` nodes that share one arm-corpus-digest `InfraFailure` into a single
  umbrella. Split out by the owner on 2026-09-18; `group_body_failure_cascades()` and
  `BODY_CASCADE_MIN_GROUP_SIZE_DEFAULT` are untouched by this plan.
- [SEPARATE-SLUG #3243] **Stamping dispatching hostname and session_id into filed bodies.** The
  body builder introduced here is where it belongs, and it becomes trivial afterwards, but it has
  its own acceptance criteria.
- [SEPARATE-SLUG #3347] **Quota-exhaustion filed as a code regression.** Upstream of this change,
  in the environmental-classification path, and orthogonal to who creates the issue.
- [EXTERNAL] **Deploying the change to the machines that run the nightly.** Merging moves the ref;
  propagating it to the launchd-scheduled runner is an operator `/update` on each bridge machine.
- [DESTRUCTIVE] **A general sweep of the tracker's historical duplicates.** The post-merge runbook
  in `## Update System` closes the enumerated set from the issue body (#3382-#3397 and the named
  09-16 pairs) and nothing else. Closing issues is one-shot, so review-before-execute is the safety
  mechanism, and `## Decisions` #4 makes that structural by taking the step out of the task graph
  entirely: a human runs it after merge, reading a fixed list. It is not the detector acquiring a
  closing privilege, and no `gh issue close` call enters `scripts/nightly_regression_tests.py`
  (pinned as an anti-criterion in `## Verification`).
- [DEFERRED] **The reconciliation sweep, and any issue-closing privilege for the detector.** The
  drafted design — read back the issues created in the run's window, group by fingerprint, converge
  each duplicate to its lowest number, close the rest `NOT_PLANNED` with a pointer comment — is
  deferred by owner decision on 2026-09-18 (`## Decisions` #2), adopting the Simplifier's finding
  in critique round 2. The reasoning: both motivating incidents (09-17's three waves, 09-16's ten
  pairs) were single-run LLM replays that Race 1's Python-loop fix eliminates outright; nothing in
  the source or this system's operational record establishes that the nightly has ever run on more
  than one host; and the sweep is a net-new autonomous subsystem (GitHub read-back, fingerprint
  grouping, and the module's first-ever `gh issue close` path) carried by a Medium appetite for an
  unobserved failure. What ships instead is the detection half — the fingerprint in every body, and
  an in-run collision log line.
  **Evidence bar that reopens this.** Either of the following is sufficient, and either should be
  filed as a follow-up issue citing this deferral:
  1. Two `scripts/nightly_regression_tests.py` runs observed against the same repo within one night
     from different hosts — directly legible once #3243 stamps hostname and session_id into filed
     bodies, which is why #3243 is the natural predecessor of any sweep work.
  2. A filed issue pair sharing one `<!-- nightly-fingerprint: ... -->` value that the following
     night's `open_issues()` dedup read did *not* collapse into a recurrence comment — i.e. evidence
     that the existing cross-run dedup does not in fact converge the residue on its own.
  Absent either, the residue is two open issues resolved by a human, against the 24-issue morning
  this plan exists to end.

## Update System

- **Update script / skill changes: none.** `scripts/remote-update.sh` and `.claude/skills/update/`
  pull the ref and restart services; this module is invoked by the launchd nightly schedule, not by
  a long-lived service, so the next scheduled run picks up the new code with no restart.
- **New dependencies or config to propagate: none.** `gh` is already required. Any new tunable is a
  `NIGHTLY_*` env var with a documented default that is correct when unset, so no `.env` or
  `.env.example` change is required and no machine needs configuration to get the fixed behavior.
- **Migration steps: one, and it is a deletion.** `data/nightly-triage-ledger/` becomes dead state.
  It is machine-local, gitignored scratch, not a Popoto model and not in Redis, so no
  `scripts/update/migrations.py` entry is warranted. The directory is left on disk to rot rather
  than deleted by automation; state this in the feature doc so an operator reading the directory
  later knows it is inert.
- **Rollback / break-glass.** `NIGHTLY_AUTO_FILE=false` disables new-issue creation for a run: the
  detector still comments on recurrences and still logs, in full, every issue it would have filed,
  so an operator can file by hand from the log. This is a **kill switch, not a second code path** —
  the alternative the critique suggested (a flag reverting to LLM-session filing) is explicitly
  rejected: keeping the retired path alive behind a flag is the parallel-migration this repo's
  no-legacy-code rule forbids, and it would mean the duplicate-filing bug stays one env var away
  forever. If detector-side filing misbehaves, the kill switch stops the bleeding within one night
  and `git revert` of a single commit is the real remedy.
- **On rollback, the historical duplicates stay closed.** They are genuine duplicates of issues
  that remain open; their closure is independent of which code path files future issues.

### Post-merge operator step: close the historical duplicates

Run **after merge**, by a human, once. This is not a pipeline task and not a merge gate
(`## Decisions` #4): it has no code dependency on the PR, and it is one-shot and irreversible, so
review-before-execute is made structural by keeping it out of the task graph rather than by a gate
clause inside it. Carry this runbook into `docs/features/nightly-triage-dispatch.md` so it survives
the plan.

1. **Confirm the list is still accurate before touching anything.** Nothing here is derived at run
   time; the numbers come from issue #3418's body and from this plan. Someone may have closed or
   edited one already:

   ```bash
   for n in $(seq 3382 3405); do
     printf '%s\t' "$n"; gh issue view "$n" --json state,stateReason,title -q '[.state,.stateReason,.title]|@tsv'
   done
   ```

2. **Close the 09-17 duplicates: #3382-#3397** (waves 1 and 2), as `NOT_PLANNED`, each with a
   comment naming its surviving twin. **The survivors are #3398-#3405** (wave 3) — the same 8 node
   titles, filed a third time at 21:08. Note this is *not* the lowest-number-survives rule the
   deferred sweep would apply: wave 3 is the set the session's own ledger
   (`data/nightly-triage-ledger/nightly-triage-4b33f93e.json`) and its final report record, so it is
   the set anything downstream already points at. Pair each closure with its wave-3 twin by matching
   the byte-identical title, not by arithmetic offset.
3. **Close the 09-16 duplicates**, keeping the **lower** number in each pair: #3365, #3366, #3367,
   #3368, #3369, #3370, #3371, #3372, #3373, #3374 are closed; #3355-#3364 survive.
4. **Verify**, with the same loop as step 1: every closed number must report `CLOSED` /
   `NOT_PLANNED`, and every survivor must still be `OPEN`.

A failure or a surprise at any step is a stop-and-ask, not a retry — these are live issues on a
tracker a human reads every morning.

## Agent Integration

- **No new CLI entry point in `pyproject.toml [project.scripts]`.** `scripts/nightly_regression_tests.py`
  is invoked by the launchd nightly schedule, not by the agent's Bash tool, and this plan adds no
  operator-facing verb.
- **No bridge import.** `bridge/telegram_bridge.py` does not import this module and must not start
  to. `TestNothingNotifies` (line 629) pins that this module has zero notifier surface and stays
  passing unchanged.
- **The agent surface this change touches is subtractive.** The triage session reached GitHub via
  its own `gh issue create` in a Bash call; it no longer does. Its permitted surface narrows to
  `gh issue comment`. The `TestPromptsNeverNameTheSearchIndex` sibling assertion added in
  `## Test Impact` — no surviving prompt contains a creation instruction — is the mechanical
  enforcement of that narrowing.
- **`tools.valor_session create --role eng` remains the dispatch mechanism** for the investigation
  session, which is kept (`## Decisions` #1), unchanged in shape; only its prompt payload changes.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/nightly-triage-dispatch.md` — this is the primary target. It documents
  the dispatch-and-file architecture this plan inverts: rewrite the filing section to describe the
  detector as the sole issue creator, describe the fingerprint and the in-run collision report,
  state that the detector creates and comments but never closes, and remove the session-ledger
  description entirely (no "formerly" paragraph — describe the new status
  quo only, per this repo's no-legacy-code rule).
- [ ] Update `docs/features/nightly-regression-tests.md` — correct the `Tracker: N issue(s) filed`
  semantics (a derived count over issue numbers GitHub returned, not a self-reported tally) and
  document that the dedup reads deliberately fail *open* while an unconfirmed create is never
  counted and never recorded, since a future maintainer will otherwise "fix" that into consistency.
- [ ] Carry the post-merge duplicate-closure runbook (`## Update System`) into
  `docs/features/nightly-triage-dispatch.md` as an operator subsection, with the enumerated numbers
  and the `gh issue view` verification loop, so the one-shot survives this plan document.
- [ ] Update `docs/features/README.md` index rows for both files if their one-line summaries name
  the session as the filer.

### External Documentation Site
- [ ] Not applicable — this repo publishes no external docs site for internal tooling.

### Inline Documentation
- [ ] `create_issue()` docstring states the no-retry contract and cites the absence of a GitHub
  idempotency key, so the next maintainer does not add retry logic.
- [ ] The budget comment states that `issues_filed` is derived from confirmed numbers and
  explicitly contrasts itself with `open_issues`' fail-open, naming #3418, so the asymmetry reads as
  deliberate rather than as an oversight.
- [ ] The collision-report comment states that the detector deliberately reports and does not close,
  naming `## Decisions` #2 and the deferral, so the next maintainer does not "finish" it into an
  auto-close.
- [ ] The fingerprint constant's comment states what it is derived from and why a title is not a
  usable key.
- [ ] Any new tunable carries the module's established provisional-value comment form: what the
  default is, that it is provisional, and what evidence would move it.

## Success Criteria

- [ ] `scripts/nightly_regression_tests.py` creates issues itself; no prompt it emits contains a
  creation instruction.
- [ ] Running `dispatch_findings()` twice against one fake GitHub creates zero issues on the second
  pass and comments once per node (`TestFilingIdempotence`).
- [ ] `issues_filed` equals the number of issue numbers actually returned by `create_issue()`; a
  failed create spends no budget and leaves its node out of `recorded`.
- [ ] A second finding in one run resolving to an already-filed fingerprint is skipped and logged,
  not filed (`TestFingerprintCollisionIsLogged`).
- [ ] A pre-create refresh that finds the title already open **comments** on it and records the node;
  it never silently skips (`TestPreCreateRefreshComments`).
- [ ] The detector never closes an issue: `scripts/nightly_regression_tests.py` contains no
  `gh issue close` invocation and no close helper.
- [ ] `write_triage_ledger`, `NodeDisposition`, and the ledger prompt paragraph are gone from the
  module — deleted, not flagged off.
- [ ] `NIGHTLY_AUTO_FILE=false` produces a run that comments, logs every would-be filing, and
  creates nothing.
- [ ] **[POST-MERGE, NOT A MERGE GATE]** #3382-#3397 and the enumerated 09-16 pairs are CLOSED as
  `NOT_PLANNED` with a pointer to their survivor. Owned by the operator running the post-merge
  runbook in `## Update System`; the final-validation task does not confirm this row
  (`## Decisions` #4).
- [ ] **[POST-DEPLOY, NOT A MERGE GATE] Human-readable outcome check** (not a count): every other
  row above is mechanically checkable at merge time; this one is not, because it needs a real
  nightly run to have happened. **Owner:** whoever triages the tracker that morning (Tom by default).
  **Trigger:** the first real nightly run after `/update` carries the merged ref to the machine that
  runs the schedule (`## No-Gos` [EXTERNAL]). **Check:** take that run's issue list and, for each
  issue, state what is broken without opening the linked node logs — every issue must be
  self-describing from its title and first paragraph. An issue that fails this read is a filing-body
  defect even if the counts are perfect; the whole point of the change is that a morning's triage is
  legible, not merely short. A failure here is a follow-up issue against the body builder, not a
  revert: the counts and the duplicate-prevention this plan exists for are already proven by the
  rows above. Task 8 (`validate-all`) does **not** block on this row.
- [ ] Tests pass (`/do-test`, scoped to `tests/unit/test_nightly_regression_tests.py`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No xfail conversions apply — `grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/unit/test_nightly_regression_tests.py` returns nothing.

## Team Orchestration

### Team Members

- **Builder (filing path)**
  - Name: `filing-builder`
  - Role: `create_issue`, the fingerprint, the create loop, budget accounting, the in-run collision
    report — all inside `scripts/nightly_regression_tests.py`
  - Agent Type: builder
  - Resume: true

- **Builder (prompt and deletion path)**
  - Name: `prompt-builder`
  - Role: retire `write_triage_ledger` / `NodeDisposition`, collapse the three filing prompts into
    the comment-only investigation prompt, update `ISSUE_LOOKUP_INSTRUCTION`
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `nightly-test-engineer`
  - Role: the `## Test Impact` dispositions and the three new test classes
  - Agent Type: test-engineer
  - Resume: true

- **Reviewer**
  - Name: `filing-reviewer`
  - Role: review the diff for dropped assertions and for any surviving path by which an agent could
    create an issue
  - Agent Type: cruft-auditor
  - Resume: true

- **Documentarian**
  - Name: `nightly-documentarian`
  - Role: the `## Documentation` tasks
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `filing-validator`
  - Role: verify every `## Success Criteria` row and run the `## Verification` table
  - Agent Type: validator
  - Resume: true

**Sequencing note:** `filing-builder` and `prompt-builder` touch the same file and must not run
concurrently. Run `filing-builder` to completion first — the prompt changes depend on the create
path existing.

## Step by Step Tasks

### 1. Add the create primitive
- **Task ID**: build-create-issue
- **Depends On**: none
- **Validates**: `tests/unit/test_nightly_regression_tests.py::TestCreateIssue` (create)
- **Assigned To**: filing-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `create_issue(title, body, *, dry_run=False) -> int | None` beside `comment_on_issue`, same
  subprocess shape, `--body-file -` on stdin, `cwd=PROJECT_DIR`.
- Prefer `gh issue create --json number` for the return value; fall back to a strict trailing-integer
  parse of the printed URL. Refuse an empty title before shelling out.
- No retries, ever. Docstring states why, citing the absence of a GitHub idempotency key.
- Add the `<!-- nightly-fingerprint: ... -->` body-builder helper and its derivation (node id for
  per-node, cascade state key for umbrellas).

### 2. Move the filing loop into `dispatch_findings()`
- **Task ID**: build-filing-loop
- **Depends On**: build-create-issue
- **Validates**: `tests/unit/test_nightly_regression_tests.py::TestDispatchFindings`, `::TestDispositionHandoff` (replace)
- **Assigned To**: filing-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace the per-node dispatch block at `:2941-2970` and the cascade dispatch at `:2856-2866`
  with calls to `create_issue()`.
- Refresh the open-issue map immediately before each create (Race 2). **On a hit, comment via
  `comment_on_issue()` against the number the refresh returned and record the node on success** —
  reuse `partition_already_open()`'s shape (`:2397-2425`, per-node caller `:2881-2889`) and key the
  fresh dict on the same `f"Nightly regression: {node}"` / `cascade["title"]` strings the opening
  read used, or the refresh silently never matches. A bare skip here is a dropped recurrence.
- Decrement `issue_budget` and extend `recorded` only on a confirmed number (Race 4).
- Add `DispatchOutcome.filed_issues: dict[str, int]`; make `issues_filed` derived from it.
- Remove the `None` sentinel from `cascade_issues` and the now-dead upgrade path in
  `carry_cascade_issues()`.
- Add the `NIGHTLY_AUTO_FILE` kill switch: when false, log every would-be filing in full and create
  nothing.

### 3. Fingerprint-collision reporting (report only, no close)
- **Task ID**: build-collision-report
- **Depends On**: build-filing-loop
- **Validates**: `tests/unit/test_nightly_regression_tests.py::TestFingerprintCollisionIsLogged` (create), `::TestIssueBudgetSpendsOnlyConfirmedCreates` (create)
- **Assigned To**: filing-builder
- **Agent Type**: builder
- **Parallel**: false
- Keep an in-process `dict[fingerprint, int]` of every fingerprint this run filed, populated as
  each `create_issue()` returns a number.
- Before each create, look the computed fingerprint up in that dict. On a hit, skip the create and
  emit one `WARNING` naming the fingerprint, the issue it was already filed as, and the skipped
  node. The node is left out of `recorded` so a genuine distinct finding is not suppressed forever.
- **Report only.** No GitHub read-back, no `gh issue close`, no pointer comment, no close helper.
  The detector's privilege set stays create-and-comment. This is the owner's ruling on Open
  Question 2 (`## Decisions` #2); the convergent sweep and its evidence bar live in `## No-Gos`.
- Emit three distinct, separately-named log lines for the three non-filing outcomes — budget
  exhausted, create failed, fingerprint collision — each naming the nodes it concerns.

### 4. Retire the ledger, the dispositions, and the filing prompts
- **Task ID**: build-retire-agent-filing
- **Depends On**: build-collision-report
- **Validates**: `tests/unit/test_nightly_regression_tests.py::TestBuildTriagePrompt` (replace), `::TestMaybeDispatchTriage`, `::TestPromptsNeverNameTheSearchIndex`
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `write_triage_ledger()` and `NodeDisposition` outright. Delete the ledger paragraph from
  `_build_triage_prompt`.
- Narrow `maybe_dispatch_triage_session()` to take `(number, subject)` pairs and dispatch after
  filing; collapse the three filing prompts into one comment-only investigation prompt.
- Strip the dedup-before-filing framing from `ISSUE_LOOKUP_INSTRUCTION`; keep the search-index
  prohibition for whatever lookup the investigation session still performs.
- Leave no commented-out code and no "formerly" comments.
- **Decided, no gate:** the owner ruled on 2026-09-18 that the comment-only investigation session
  **stays** (`## Decisions` #1). Build it as written above; do not delete
  `maybe_dispatch_triage_session`.

### 5. Test suite
- **Task ID**: build-tests
- **Depends On**: build-retire-agent-filing
- **Validates**: `scripts/pytest-clean.sh tests/unit/test_nightly_regression_tests.py`
- **Assigned To**: nightly-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Execute every disposition in `## Test Impact`, in the order given (shared harness first).
- Extract the four duplicated fake-`gh` harnesses (1241, 1406, 2580, 2800) into one fixture that
  stubs `create_issue` alongside `comment_on_issue`.
- Write `TestCreateIssue`, `TestFilingIdempotence`, `TestPreCreateRefreshComments`,
  `TestFingerprintCollisionIsLogged`, `TestIssueBudgetSpendsOnlyConfirmedCreates`.
- Run only this file. Never the full `tests/unit/` tree.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Validates**: `git diff --name-only origin/main -- docs/` lists every path in the `## Documentation` checklist. A docs task's output *is* its validation — there is no test to run, so the file list is the check.
- **Assigned To**: nightly-documentarian
- **Agent Type**: documentarian
- **Parallel**: true
- Execute the `## Documentation` checklist.

### 7. Cruft review
- **Task ID**: review-cruft
- **Depends On**: build-tests, document-feature
- **Validates**: the auditor's own report, plus the three anti-criterion rows in `## Verification` (no create instruction in any prompt, no ledger/disposition references, no `gh issue close` in the detector). A review step's finding list is its own validation; the anti-criteria make the two claims it is most likely to get wrong mechanically checkable.
- **Assigned To**: filing-reviewer
- **Agent Type**: cruft-auditor
- **Parallel**: false
- Scan the diff for dropped test assertions, for any surviving path by which an agent could create
  an issue, and for leftover ledger/disposition references.

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: build-create-issue, build-filing-loop, build-collision-report, build-retire-agent-filing, build-tests, document-feature, review-cruft
- **Validates**: every row of the `## Verification` table, run in order, plus every *mechanically-checkable* `## Success Criteria` checkbox. This task's validation is the table itself — it exists to run it.
- Two `## Success Criteria` rows Task 8 does **not** confirm: the `[POST-DEPLOY, NOT A MERGE GATE]` human-readable outcome check, which needs a real nightly run and is owned by the morning triager; and the `[POST-MERGE, NOT A MERGE GATE]` historical-duplicate closures, owned by the operator running the runbook in `## Update System` (`## Decisions` #4). Record both as deferred in the validation report rather than passing or failing them — a validator that silently rubber-stamps one, or silently drops it, is the failure mode these carve-outs exist to prevent.
- **Assigned To**: filing-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the `## Verification` table; confirm every `## Success Criteria` row.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Nightly tests pass | `scripts/pytest-clean.sh tests/unit/test_nightly_regression_tests.py -q` | exit code 0 |
| Lint clean | `python -m ruff check scripts/nightly_regression_tests.py tests/unit/test_nightly_regression_tests.py` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/nightly_regression_tests.py tests/unit/test_nightly_regression_tests.py` | exit code 0 |
| Detector creates issues itself | `grep -c '"create"' scripts/nightly_regression_tests.py` | output > 0 |
| Idempotence regression test exists | `grep -c "class TestFilingIdempotence" tests/unit/test_nightly_regression_tests.py` | output > 0 |
| Budget test exists | `grep -c "class TestIssueBudgetSpendsOnlyConfirmedCreates" tests/unit/test_nightly_regression_tests.py` | output > 0 |
| Collision-report test exists | `grep -c "class TestFingerprintCollisionIsLogged" tests/unit/test_nightly_regression_tests.py` | output > 0 |
| Refresh-hit comments test exists | `grep -c "class TestPreCreateRefreshComments" tests/unit/test_nightly_regression_tests.py` | output > 0 |
| Kill switch wired | `grep -c "NIGHTLY_AUTO_FILE" scripts/nightly_regression_tests.py` | output > 0 |
| Anti-criterion: no prompt tells an agent to create an issue | `grep -c "gh issue create" scripts/nightly_regression_tests.py` | match count == 0 |
| Anti-criterion: the detector never closes an issue | `grep -c 'issue", "close\|gh issue close' scripts/nightly_regression_tests.py` | match count == 0 |
| **[POST-MERGE, NOT A MERGE GATE]** historical duplicate closures landed (operator, after merge — `## Update System` runbook, `## Decisions` #4) | `for n in $(seq 3382 3397); do gh issue view "$n" --json state,stateReason -q '[.state,.stateReason]\|@tsv'; done`, then the same loop over #3365-#3374 | every line `CLOSED` `NOT_PLANNED`; #3398-#3405 and #3355-#3364 still `OPEN` |
| Anti-criterion: ledger and dispositions are gone | `grep -c "write_triage_ledger\|NodeDisposition" scripts/nightly_regression_tests.py` | match count == 0 |
| Anti-criterion: #3419's collapsing logic untouched | `git diff origin/main -- scripts/nightly_regression_tests.py \| grep -c "group_body_failure_cascades\|BODY_CASCADE_MIN_GROUP_SIZE"` | match count == 0 |
| Anti-criterion: #3243's hostname stamp not landed here | `git diff origin/main -- scripts/nightly_regression_tests.py \| grep -c "gethostname\|hostname"` | match count == 0 |
| No stale xfails | `grep -rn 'xfail' tests/unit/test_nightly_regression_tests.py` | exit code 1 |

## Critique Results

Round 1 (2026-09-18, against the empty skeleton at `56a980d8a`) returned NEEDS REVISION with 4
blockers, 3 concerns and 1 nit. All eight were resolved in plan revision 1 (`22b28d8ea`); the
resolutions are recorded inline in the sections they landed in (`## Race Conditions`,
`## Why Previous Fixes Failed`, `## Solution` → Scope, `## Test Impact`, `## Update System`).

### Round 2 — 2026-09-18, verdict NEEDS REVISION (2 blockers, 4 concerns, 0 nits)

FULL roster, independent: Risk & Robustness, Scope & Value, History & Consistency, plus automated
structural checks. Roster gate 3/3 complete, 0 ungrounded. **Every finding in this round concerns
the reconciliation sweep** (Race 3 / Task 3 `build-reconcile`) — no critic found a defect in the
filing move itself, the budget verification, the prior-art analysis, or the test-impact audit.
That convergence is itself the round's most useful signal: the sweep is the one piece of this plan
that is a net-new subsystem rather than a relocation of an existing one.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|----------------------|
| BLOCKER | Risk & Robustness (Skeptic) | The reconciliation sweep is specified as "one read of issues created during this run's window", but the fingerprint it must compare on lives only in the issue **body**, and no body-reading primitive is specified anywhere in `## Solution` or Task 3. The module's only precedent, `open_issues()` (`scripts/nightly_regression_tests.py:2060-2135`), fetches `--json number,title` and carries no body; `--search` is explicitly rejected elsewhere in this same plan for index lag. As written, Task 3 cannot be built from the primitives the plan names. | **MOOTED** — revision 2: the sweep is deferred (`## Race Conditions` Race 3, `## No-Gos` [DEFERRED]). No body-reading primitive is introduced, because nothing reads an issue body back. The fingerprint still ships, as a write-only forensic key. | `open_issues()` returns `dict[str, int]` keyed on title with no body field and cannot be reused for fingerprint lookups. Task 3 needs an explicit read spec — either `gh issue list --json number,title,body,createdAt` scoped by a time window with its own timeout/limit constants following the `OPEN_ISSUE_LIST_TIMEOUT_SECONDS` pattern, or N per-issue `gh issue view --json body` calls bounded by `NIGHTLY_MAX_ISSUES_PER_RUN` — plus the cost and timeout contract for whichever is chosen. |
| BLOCKER | Scope & Value (User) + History & Consistency (Consistency Auditor) — elevated on two-critic convergence | Task 3 commits to auto-closing duplicate issues as settled, ungated behavior ("Converge any duplicated fingerprint to its lowest number; close the others `NOT_PLANNED` with a pointer comment"), while `## Open Questions` #2 treats that exact behavior as unresolved and requiring PM sign-off on a privilege the detector has never held. Task 4 carries an explicit `**Gate:**` clause tied to Open Question 1; Task 3 has no equivalent, so unattended review-free issue-closing ships regardless of how the PM answers. | **RESOLVED** — revision 2: the owner answered Open Question 2 "report only" (`## Decisions` #2). Task 3 is now `build-collision-report` and ships no close path at all, so there is no gate to write — the branch the gate would have guarded does not exist. A `## Verification` anti-criterion row and a `## Success Criteria` row both pin that the detector contains no `gh issue close`. | Copy Task 4's gate sentence verbatim in shape: if Open Question 2 is answered "report only", Task 3 builds a fingerprint-collision log plus a pointer comment instead of a `gh issue close` call, and no unattended close ships. `TestReconciliationSweep`'s described assertion ("the lower number survives, the higher is closed `NOT_PLANNED`") must branch on that answer rather than hard-coding auto-close as the only tested behavior. |
| CONCERN | Risk & Robustness (Adversary) | When the sweep converges a duplicate that this run itself created to a lower survivor number, nothing in the plan updates `DispatchOutcome.filed_issues` / `cascade_issues` to the survivor before `main()` persists them. The night's own state would then point at an issue the same run just closed `NOT_PLANNED`, corrupting what the next night's `resolve_cascade_issue` reads. | **MOOTED** — revision 2: no convergence step exists, so no second mutation of `cascade_issues` is possible. The map is written exactly once, at create time, from the number GitHub returned (Task 2). | `cascade_issues: dict[str, int \| None]` is mutated in place during the create loop; the sweep must mutate the same dict a second time for every key it converges, before `issues_filed` is finalized. Add a test asserting the persisted map names the survivor, not the closed twin. |
| CONCERN | Risk & Robustness (Operator) | `## Failure Path Test Strategy` pins distinct log lines for budget-exhausted and verification-failed deferral but not for a **partial** sweep failure — a close that succeeds while its pointer comment fails leaves an issue closed `NOT_PLANNED` with no explanation, which is precisely the illegible-morning-triage outcome `## Success Criteria` exists to prevent, relocated from the filing loop to the sweep. | **MOOTED as stated, narrowed and kept** — revision 2: there is no close-and-comment pair to half-fail. The legibility requirement the finding is really about survives and was strengthened: `## Failure Path Test Strategy` now pins three separately-named log lines for the three non-filing outcomes (budget exhausted, create failed, fingerprint collision), on the argument that one shared "not filed" line would collapse three different operator responses into one. Task 3 carries the same requirement. | Model on `comment_on_issue`'s existing "log and return bool" contract (`scripts/nightly_regression_tests.py:2278-2330`): log each half of the close-and-comment pair independently rather than treating "sweep ran" as one pass/fail unit, and add a named log line for each partial outcome. |
| CONCERN | Scope & Value (Simplifier) | The sweep is a net-new autonomous subsystem — read-back, fingerprint grouping, and a `gh issue close` call path that exists nowhere in the module today — built to converge a race whose trigger is two hosts running the nightly concurrently. Nothing in the plan or the source establishes that this system has ever run on more than one host, and both motivating incidents (09-17's three waves, 09-16's ten pairs) were single-run replays that Race 1's Python-loop fix eliminates on its own. | **ACCEPTED IN FULL** — revision 2: the owner adopted this recommendation verbatim (`## Decisions` #2). Race 1 and Race 2 ship; the sweep is deferred. `## No-Gos` carries the deferral with a two-clause evidence bar that reopens it (an observed same-night multi-host run, or a fingerprint-sharing pair the next night's `open_issues()` dedup fails to collapse). | Consider shipping Race 1 (create loop in Python) and Race 2 (pre-create re-read) alone, which close every observed incident, and deferring the sweep to a follow-up gated on real evidence of concurrent multi-host nightly runs. If the sweep stays, the plan should say what evidence justifies it beyond theoretical completeness — an appetite-Medium change is carrying a second subsystem for an unobserved failure. |
| CONCERN | Structural Check | Four of nine tasks carry no `**Validates**` line: `cleanup-duplicates` (6), `document-feature` (7), `review-cruft` (8), `validate-all` (9). Task 6 is the one that matters — it closes roughly twenty GitHub issues as a one-shot `[DESTRUCTIVE]`-class action and has a `## Success Criteria` row, but no validation command a builder or validator can run to confirm it did what it claimed. | **FIXED** — revision 2: Tasks 6-9 each carry a `**Validates**` line. Task 6 gets the concrete `gh issue view $n --json state,stateReason` loop asserting `CLOSED` / `NOT_PLANNED` over #3382-#3397 and the 09-16 numbers, mirrored as a `## Verification` table row. Tasks 7-9 state explicitly that a docs/review/validation step's own output is its validation, and name the mechanical checks that back them. | Give Task 6 a `**Validates**` line with a concrete command, e.g. a loop over the enumerated numbers asserting `gh issue view N --json state,stateReason` reports `CLOSED` / `NOT_PLANNED`, and add the same command as a `## Verification` table row so it is checked mechanically rather than by assertion. Tasks 7-9 are docs/review/validation steps whose output is their own validation; a one-line note saying so would close the gap. |


**Revision 2 disposition (2026-09-18).** All six rows above are closed: two FIXED/RESOLVED by
edits, three MOOTED by deferring the sweep, one narrowed and kept. The round's own observation —
that every finding landed on the sweep and none on the filing move, the budget accounting, the
prior-art analysis or the test-impact audit — is what the owner acted on: the piece the critics
converged against was the one net-new subsystem, and it is now out of scope with a written evidence
bar for reopening it. What remains is a relocation of an existing mechanism, which is what the
Medium appetite was sized for.

### Round 3 — 2026-09-18, verdict READY TO BUILD (with concerns) (0 blockers, 4 concerns, 0 nits)

FULL roster, independent: Risk & Robustness, Scope & Value, History & Consistency. Roster gate 3/3
complete, 0 ungrounded. Run against revision 2 (`604c82eb1`). **No blockers.** No critic re-raised a
round-2 row, and Scope & Value explicitly re-verified that the sweep deferral is applied
consistently across `## Decisions` #2, Race 3 and `## No-Gos` with no drift. The four concerns are
two behavioral gaps and two stale cross-references left by revision 2's deletions.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|----------------------|
| CONCERN | Risk & Robustness (Adversary) | The pre-create re-read (Race 2 / Key Elements) is specified as a duplicate-prevention check but never says what happens **on a hit**. The initial-read path handles that case by commenting instead of filing (`partition_already_open`, `scripts/nightly_regression_tests.py:2397-2425`, per-node caller `:2881-2889`), preserving the recurrence signal; `## Failure Path Test Strategy` enumerates only three non-filing outcomes and has no fourth for "refresh found a same-title issue mid-loop". If the implementation skips-and-leaves-unrecorded on a refresh hit, a legitimate recurrence comment is silently dropped — the "silent hole" class Risk 1 calls worse than a duplicate, relocated to a narrower window rather than eliminated. | **FIXED** — revision 3: the refresh-hit branch is now specified everywhere it has to be. `## Solution` Key Elements and Race 2 both state that a hit routes through `comment_on_issue()` against the number the refresh returned, reusing `partition_already_open()`'s shape, and record the node on a successful comment. Task 2 carries the same instruction with the re-keying gotcha. `## Failure Path Test Strategy` now names **four** non-filing outcomes and pins that a failed recurrence comment leaves the node out of `recorded`. New `TestPreCreateRefreshComments` plus a `## Verification` row and a `## Success Criteria` row. | Add an explicit fourth branch to the create loop: a refresh hit routes through `comment_on_issue` against the number the refresh just returned, not a skip-and-log like a fingerprint collision. Reuse `partition_already_open` (`:2397`) and its call site (`:2881-2889`) per-create inside the loop replacing `:2941-2970`; the refresh call is `open_issues()` (`:2065-2125`), and the fresh dict must be re-keyed against the same `f"Nightly regression: {node}"` / `cascade["title"]` strings the original read used or it silently never matches. Own log line, own test. |
| CONCERN | Scope & Value (User) | `## Success Criteria` mixes mechanically-verifiable rows with the human-readable outcome check, which cannot be evaluated until a real nightly run happens in production and a human triager reads its output — yet Task 9 (`validate-all`) is defined as confirming **every** `## Success Criteria` row, with no carve-out. As written the plan's own completion gate cannot be closed at merge time. | **FIXED** — revision 3: the row is relabelled `[POST-DEPLOY, NOT A MERGE GATE]` and carries an owner (the morning triager), a trigger (the first real nightly run after `/update` reaches the scheduling machine), and an explicit disposition — a failure there is a follow-up issue against the body builder, not a revert. Task 9's `**Validates**` line is split to *mechanically-checkable* rows and instructs the validator to record this one as deferred-to-post-deploy rather than passing or failing it. | Split Task 9's `**Validates**` line to cover every *mechanically-checkable* row, and move the human-readable read to a named post-deploy observation with an owner and a trigger (the first real nightly run after `/update` reaches the runner machine), so the row is neither silently rubber-stamped by the validator nor silently dropped. |
| CONCERN | History & Consistency (Consistency Auditor) | `## Agent Integration` still reads "remains the dispatch mechanism **if the investigation session survives** (`## Open Questions`)" — a stale cross-reference to a section that now says "None open", and conditional phrasing that re-opens a question `## Decisions` #1 and Task 4 ("Decided, no gate") both close. | **FIXED** — revision 3: `## Agent Integration` now reads "remains the dispatch mechanism for the investigation session, which is kept (`## Decisions` #1)". No conditional clause and no pointer at `## Open Questions`. | Replace the conditional clause with "(kept per `## Decisions` #1)", matching the wording already used in Task 4 and Risk 4's mitigation. |
| CONCERN | History & Consistency (Consistency Auditor) | Race 3's data prerequisite cites `resolve_cascade_issue`, line 1165. That function is defined at `scripts/nightly_regression_tests.py:2366`; line 1165 of that file is `@dataclass(frozen=True)` preceding the unrelated `GateCaps` class. Line 1165 of the **test** file is `class TestResolveCascadeIssue`, so the citation was copied from the wrong file. The claim itself is correct (`:2391-2394`), but every other citation in the plan (`:2280`, `:2450`, `:2518`, `:2696`, `:3265`, test-file `672`) verifies exactly, so the one wrong pointer is the outlier. | **FIXED** — revision 3: the Race 3 data prerequisite now cites `resolve_cascade_issue`, `scripts/nightly_regression_tests.py:2366`. Verified against the source at revision time. | Change `line 1165` to `line 2366` in the Race 3 "Data prerequisite" sentence. |


### Round 4 — 2026-09-18, verdict NEEDS REVISION (1 blocker, 1 concern, 0 nits)

FULL roster, independent: Risk & Robustness, Scope & Value, History & Consistency. Roster gate 3/3
complete, 0 ungrounded. Run against revision 3 (`f2bc1d64c`). Scope & Value returned **no findings**
and explicitly re-verified that revision 3's refresh-hit branch is a relocation of
`partition_already_open()`'s existing pattern rather than new scope. Both remaining findings are
about Task 6, the historical-duplicate cleanup, and about narrative summaries that revision 3 did not
reach — neither touches the filing mechanism itself.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|----------------------|
| BLOCKER | Risk & Robustness (Operator) | Task 6 contradicts the plan's own stated safety design for the one irreversible action in scope. `## Architectural Impact` says the closures are "performed once by an operator with the closure list enumerated in the PR body for review" and `## No-Gos` [DESTRUCTIVE] says "review-before-execute is the safety mechanism" — but Task 6 is assigned to the autonomous `filing-builder`, depends only on `build-tests`, and runs **before** `document-feature` (7), `review-cruft` (8) and `validate-all` (9), i.e. before a PR exists to carry the enumerated list and before any reviewer sees the diff. As scheduled, an agent shells `gh issue close` against roughly 26 live production issues as an unattended mid-pipeline build step, with no gate clause — unlike Task 4, which states "Decided, no gate" precisely so that an ungated action is a deliberate, recorded choice. | **FIXED** — revision 4, via the suggestion's option (2), ruled by the owner: Task 6 is removed from the task graph (7-9 renumber to 6-8, `cleanup-duplicates` drops out of `validate-all`'s `Depends On`) and becomes a post-merge operator runbook under `## Update System`, recorded as `## Decisions` #4. The `## Verification` and `## Success Criteria` rows survive, relabelled `[POST-MERGE, NOT A MERGE GATE]` with the operator as owner. The runbook also corrects a latent error the old Task 6 carried: it said "keep the lowest-numbered issue per node" while naming #3398-#3405 (the *highest*) as survivors — the runbook enumerates wave 3 as the survivor set and says why (it is the set the session ledger and final report recorded). | Two candidate fixes, both mechanical once the policy is set: (1) change Task 6's `Depends On` from `build-tests` to `review-cruft` so the dependency graph enforces review-before-execute, and add a `**Gate:**` clause requiring the enumerated list to be confirmed against the PR body before any `gh issue close` is shelled; or (2) move Task 6 out of the build pipeline entirely into a post-merge operator step. Task 6's existing `**Validates**` loop (`gh issue view "$n" --json state,stateReason`) is the post-hoc half and already works for either. |
| CONCERN | History & Consistency (Consistency Auditor) | Revision 3 added the refresh-hit → comment branch to seven places but left two earlier narrative summaries describing only three outcomes. `## Solution` → `### Flow` reads "refresh open map → compute fingerprint → skip-and-log … → `create_issue()`" with no refresh-hit branch, and `## Data Flow` step 4 reads "re-read the open-issue map, compute the fingerprint, call `create_issue()`" with the same omission. Both sit earlier in the document than Key Elements and Race 2 and read like the canonical step list, so a reader consulting either concludes the refresh only feeds the create call. | **FIXED** — revision 4: `### Flow`'s arrow chain now branches explicitly (refresh hit → `comment_on_issue()` + record; miss → fingerprint → skip-and-log collision → skip-and-log exhausted budget → `create_issue()`), and `## Data Flow` step 4 enumerates the same four outcomes (a)-(d) with the note that only the create branch spends budget. | Insert the fourth branch into both summaries mirroring Task 2's wording — "refresh open map → **on a hit, comment via `comment_on_issue()` and record** → on a miss, compute fingerprint → skip-and-log if already filed this run → `create_issue()`" — and split `## Data Flow`'s numbered item 4 into a hit/miss pair. |


### Round 5 — 2026-09-18, verdict NEEDS REVISION (2 blockers, 10 concerns, 6 nits)

FULL roster, independent: Risk & Robustness, Scope & Value, History & Consistency. Roster gate 3/3
complete, 0 ungrounded. Run against revision 4 (`7ce3d1375`). All three critics independently
confirmed that revision 4's task removal and renumbering are structurally clean — no numbering gaps,
no dangling `Depends On`, and zero stale `Task N` / `cleanup-duplicates` references in live prose
(the only surviving mentions are inside the dated Round 2 and Round 4 tables, which are archival).
The round-4 blocker is closed and Scope & Value explicitly cleared the post-merge relocation as not
weakening the deliverable. Both new blockers are on ground earlier rounds never reached: the
re-baseline seed path, which no round had read, and a verification command that passes on the
known-bad baseline. Both were re-verified against the source before being accepted here.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|----------------------|
| BLOCKER | Risk & Robustness (Skeptic) | The re-baseline seed path is a third agent-filing path the plan declares unchanged while its own success criteria and Task 4 both require it to change. `_build_seed_prompt` (`scripts/nightly_regression_tests.py:2019-2062`) emits "Only if neither exists, open ONE umbrella issue with EXACTLY that title" — a creation instruction, which falsifies `## Success Criteria` row 1 ("no surviving prompt contains a creation instruction"), yet `## Test Impact` lists `TestBuildSeedPrompt` (3197) under **unchanged classes**. Worse, the seed dispatch lives in `main()` at `:3198-3241`, outside `dispatch_findings()`, and calls `maybe_dispatch_triage_session([f"seed:{len(confirmed_failing)}"], …)`; Task 4 narrows that function "to take `(number, subject)` pairs and dispatch after filing", silently breaking the caller. And the seed branch's `_fatal` guard at `:3222-3235` uses a returned session id as its proxy for "an umbrella issue exists" — after this change a session id proves nothing about issue existence, and `seeded_nodes` is sticky, so a false green there suppresses a whole night-one population forever. | **ESCALATED to the PM** — the fix direction is forced but it is a scope call: either the detector creates the seed umbrella itself via `create_issue()` (a fifth code change, the consistent choice) or the seed path is carved out as explicitly untouched, which means accepting a standing exception to Success Criteria row 1. Not resolved in-lane. | **Verified independently.** `sed -n 2019,2062p` confirms the "open ONE umbrella issue" text; `sed -n 3198,3241p` confirms the out-of-`dispatch_findings` caller and the session-id-as-proxy `_fatal`; plan line 543 confirms `TestBuildSeedPrompt` is listed unchanged; plan line 404-406 already says the three prompts "collapse toward one investigation prompt", so the plan's own prose contradicts its Test Impact table. |
| BLOCKER | Risk & Robustness (Operator), Scope & Value (Simplifier) | The `## Verification` row that proves the plan's central deliverable is already green on unmodified `main`. The row reads `` grep -c '"create"' scripts/nightly_regression_tests.py `` / `output > 0`, and line 2602 today is `"create",` — the `tools.valor_session create` argv. `validate-all` could rubber-stamp a build in which `create_issue` was never written. | **PENDING revision 5** — purely mechanical: replace with `grep -c "^def create_issue"` (or the Scope table's already-correct `grep -c "gh\", \"issue\", \"create\""`) and prove it RED against `origin/main` before trusting it. | **Verified independently.** `grep -c '"create"' scripts/nightly_regression_tests.py` returns `1` on unmodified main; the single hit is line 2602. This is the known-bad-baseline guard failure the memory entry *Prove guards red against known-bad* names. |
| CONCERN | Risk & Robustness, Scope & Value | `NIGHTLY_AUTO_FILE` is both likely-inert and untested. `.env` reaches `os.environ` only through `load_env_or_die()` inside `main()`, which is why `resolve_int_knob` (`:462-482`) exists; there is no bool sibling, so a builder copying the nearest neighbour gets an import-time read and the vault setting does nothing. Its `## Verification` row asserts only that the string appears in the file. | **PENDING revision 5** — Task 2 specifies a call-time `resolve_bool_knob` read; Task 5 gains a behavioural test (kill switch on → zero `create_issue` calls, comments still posted, one log line per would-be filing); the Verification row asserts behaviour, not string presence. |  |
| CONCERN | Risk & Robustness | A kill-switched run is not a dry run, so `save_last_run` still fires (`:3332-3346`). If skipped nodes land in `outcome.recorded` they enter `dispatched_nodes` and `compute_dispatch_set` suppresses them permanently — the break-glass reaches the same permanent-suppression hazard the seed branch's comment at `:3222-3229` exists to prevent. | **PENDING revision 5** — Task 2 states the invariant explicitly (kill-switch skip leaves the node out of `recorded`) with a test. |  |
| CONCERN | Risk & Robustness | The four-outcome narrative has no branch for an unreadable pre-create refresh. `open_issues()` returns `None` on any failure (`:2065`); nothing in Race 2, `## Failure Path Test Strategy`, or Task 2 says what a `None` refresh does per-create, and a builder could reasonably read "refresh before create" as skip-on-unknown, which is Risk 1's silent hole at the exact spot Race 2 claims to shrink. | **PENDING revision 5** — name the degraded-refresh case as fail-open (create) with its own log line, as a fifth row in the outcome enumeration. |  |
| CONCERN | Risk & Robustness, Scope & Value | Task 3's in-run fingerprint registry has no reachable trigger. Risk 1's own mitigation says the fingerprint "cannot match across distinct findings by construction"; `single_nodes` comes from a deduplicated sorted set and cascade fingerprints key on the unique `cascade_state_key`, so a same-run collision cannot be produced. Task 3's stated motivation is Race 3, which is cross-host and invisible to an in-process dict. It costs a task, a test class, and one of the four named outcomes. | **PENDING revision 5** — keep the fingerprint in the body (the real detection half) and collapse the registry to a defensive log line with one small test, unless a reachable producer is named (the honest candidate is #3419's future root-cause collapsing, which could map two findings onto one key). |  |
| CONCERN | History & Consistency | Task 3 still says "three distinct, separately-named log lines for the three non-filing outcomes" while `## Failure Path Test Strategy` and Race 2 say four, including the pre-create refresh hit. Revision 4 rewrote the narratives for four outcomes but did not sweep the task bodies — no task owns the refresh-hit log line. | **PENDING revision 5** — Task 3 says four, or Task 2 gains an explicit refresh-hit log-line bullet. |  |
| CONCERN | History & Consistency | Task 8's last bullet ("Run the `## Verification` table; confirm every `## Success Criteria` row") contradicts its own carve-out paragraph one line above, re-opening the round-3 defect. The carve-out also covers only `## Success Criteria` rows, not the `[POST-MERGE]` `## Verification` row, which by construction cannot pass before merge. | **PENDING revision 5** — rewrite the last bullet to except the `[POST-MERGE]` Verification row and to record both deferred rows as deferred. |  |
| CONCERN | History & Consistency, Scope & Value | The "no prompt tells an agent to create an issue" anti-criterion greps the whole module for `gh issue create`, but Task 1 requires a `create_issue()` docstring stating the no-retry contract and Key Elements says the function "Shells `gh issue create --title … --body-file -`". An accurate docstring turns a correct build red, and a builder will "fix" it by degrading the docstring. The module already documents this exact trap at `:338-347`. | **PENDING revision 5** — scope the anti-criterion to rendered prompt output (the `TestPromptsNeverNameTheSearchIndex` sibling assertion already does this properly). |  |
| CONCERN | History & Consistency | Race 2's cost bound disagrees with the ordering `### Flow` and `## Data Flow` now specify. Race 2 says "one extra `gh` read per created issue, which is bounded by `NIGHTLY_MAX_ISSUES_PER_RUN`", but the refresh runs per *survivor*, before the budget check, and revision 4's per-node in-loop deferral replaced the pre-loop truncation at `:2912-2920`. | **PENDING revision 5** — keep the ordering (budget-before-refresh would suppress legitimate recurrence comments) and correct the sentence to "one extra `gh` read per survivor considered". |  |
| CONCERN | Risk & Robustness | Line-anchor drift in the two ranges a builder is told to delete. Task 2 cites `:2941-2970` and `:2856-2866`; the per-node block actually starts at `:2922` and includes `read_shape` at `:2927-2931`, which exists only to feed the deleted disposition path — deleting the literal range leaves it assigned and unused (ruff F841, so the lint row fails). The cascade dispatch call starts at `:2852`. | **PENDING revision 5** — cite `:2922-2970` and `:2852-2865`. |  |
| CONCERN | Risk & Robustness | Two in-module narratives this change falsifies are absent from the `## Documentation` checklist: the module docstring at `:107-109` ("the triage session, not this script, is what actually opens the issue") and `resolve_cascade_issue`'s at `:2381-2384` ("This script does not open issues itself"). `resolve_cascade_issue`'s whole lookup-order rationale is predicated on the number being unknowable at filing time. | **PENDING revision 5** — both added to the Documentation checklist. |  |
| NIT | Risk & Robustness, History & Consistency | The post-merge runbook's pre-check (step 1, `seq 3382 3405`) and verify (step 4, "the same loop as step 1") never touch the ten 09-16 numbers step 3 closes, and the `[POST-MERGE]` `## Verification` row's Expected column asserts state for numbers its command never queries. | **PENDING revision 5** — widen both loops to `seq 3355 3405` and align the Expected column. |  |
| NIT | Risk & Robustness | The post-merge runbook has no named default owner and no carrier outside the plan document, unlike the `[POST-DEPLOY]` row which names "Tom by default" and a trigger. | **PENDING revision 5** — name a default human; the Documentation checklist item already carries the runbook into `docs/features/nightly-triage-dispatch.md`. |  |
| NIT | History & Consistency | Task 6 (`document-feature`) is marked `**Parallel**: true` with nothing to run in parallel with; every other task is `false`. | **PENDING revision 5** — set to false or name the overlap. |  |
| NIT | Risk & Robustness | A stale post-renumber reference survives in the Round 3 "Addressed By" cell ("Task 9's `**Validates**` line is split"). | **PENDING revision 5** — say Task 8. |  |
| NIT | Scope & Value | `### Flow` is now a seven-line single-arrow sentence carrying four branches, while `## Data Flow` step 4 expresses the same thing as a clean (a)-(d) list. | **PENDING revision 5** — mirror the lettered list, or point `### Flow` at Data Flow step 4 as canonical. |  |
| NIT | Risk & Robustness | `create_issue` is a new argv exposure for report-derived strings and the plan says nothing about argv hygiene; the module has precedent for stating it (`:955-961`). | **PENDING revision 5** — one docstring line: list-form argv, never `shell=True`, body on stdin, title always prefixed. |  |

Per-critic verdicts: Risk & Robustness **NEEDS REVISION**; Scope & Value **READY TO BUILD (with
concerns)**; History & Consistency **READY TO BUILD (with concerns)**. Aggregate: **NEEDS REVISION**,
carried by the Risk & Robustness blockers.

---

## Decisions

Answered by the owner on 2026-09-18. These were `## Open Questions` 1-3; they are recorded here
because the plan text above now depends on them.

1. **The comment-only investigation session stays.** With filing moved into the detector, the
   nightly LLM session is dispatched *after* filing, handed real issue numbers, and permitted to
   investigate and comment only. This is what the issue's own proposed cull describes, and
   root-cause narrative on a fresh issue has triage value that the deterministic path cannot
   produce. Task 4 builds it as written and carries no gate. If the comments turn out to go unread,
   deleting `maybe_dispatch_triage_session` afterwards is a strictly smaller change, because
   correctness no longer depends on it.
2. **Report only — the detector does not gain issue-closing privilege, and the reconciliation sweep
   is deferred.** Adopting the Simplifier's round-2 recommendation in full: ship Race 1 (the create
   loop in Python) and Race 2 (the pre-create re-read), which between them close every incident on
   record, and defer the convergent sweep to a follow-up gated on real evidence of concurrent
   multi-host nightly runs. Task 3 becomes a fingerprint-collision log line — no close, no pointer
   comment, no read-back subsystem. The deferral and the evidence bar that reopens it are recorded
   under `## No-Gos`.
3. **`NIGHTLY_MAX_ISSUES_PER_RUN` stays at its current default.** The issue's proposed cull asked to
   lower it. The 09-17 overrun was a broken measurement, not a too-generous cap: a run that created
   24 issues was budgeted as 8, so the cap was never the thing being enforced. Fix the measurement,
   leave the number alone. Lowering it while the count was wrong would only have deferred real
   regressions behind a budget nothing was honouring.
4. **Closing the ~26 historical duplicates leaves the build pipeline and becomes a post-merge
   operator step.** Round 4 surfaced that scheduling it as an autonomous build task contradicted the
   plan's own safety claim for its one irreversible action. Rather than repair the dependency edge
   and bolt on a gate clause, the step comes out of the task graph entirely: it has **no code
   dependency on this PR** — nothing built here reads, writes, or tests those issues' state — and it
   is **one-shot and irreversible against live production issues**, so it belongs to a human running
   it after merge. The enumerated closure list and the `gh issue view --json state,stateReason`
   validation loop are carried into the runbook under `## Update System`; the `## Verification` and
   `## Success Criteria` rows survive, relabelled `[POST-MERGE, NOT A MERGE GATE]` and owned by the
   operator, the same shape already used for the `[POST-DEPLOY]` outcome check. Keeping the rows
   rather than deleting them is deliberate: the work is still owed, it is simply not owed by the
   pipeline.

## Open Questions

None open. All three questions this plan raised were answered by the owner on 2026-09-18 and are
recorded under `## Decisions`; the plan text above reflects those answers rather than deferring to
them.
