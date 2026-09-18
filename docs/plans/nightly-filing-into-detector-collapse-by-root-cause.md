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

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (scope alignment — the scope already split once, at #3419; the residual
  judgement call is whether the investigation session survives at all, see `## Open Questions`)
- Review rounds: 1-2 (this touches the module's most safety-critical function and deletes two
  public helpers; `TestDispatchFindings` / `TestDispositionHandoff` rewrites want a real read)

Medium rather than Small because the change deletes a mechanism (the ledger + disposition handoff)
rather than adding beside it, and the test file's four near-identical fake-`gh` harnesses each need
a new seam. Medium rather than Large because the code lives in one module, has no schema, no
migration, and no cross-machine coordination.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `gh` authenticated | `gh auth status` | The detector creates, comments, and reconciles issues through `gh`; the new create path fails closed without it |
| Repo resolves for `gh` | `gh repo view --json nameWithOwner -q .nameWithOwner` | `create_issue` inherits `cwd=PROJECT_DIR` targeting the same way `comment_on_issue` does |
| Test suite runnable | `scripts/pytest-clean.sh tests/unit/test_nightly_regression_tests.py --collect-only -q` | The whole change is gated on this file; a collection failure means the venv is off-pin |

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
| (b) | **Duplicate filing is structurally prevented, then convergently reconciled.** Per-node re-read immediately before create, a deterministic fingerprint in every created body, and a post-filing reconciliation sweep that closes any twin. | A test calls `dispatch_findings()` twice against one in-memory fake GitHub and asserts the second pass creates zero issues and comments once per node. |
| (c) | **The budget is enforced against issues GitHub confirms exist.** `issues_filed` is the count of real numbers returned; `NIGHTLY_MAX_ISSUES_PER_RUN` is decremented per confirmed create and the cap check fails closed on an unreadable verification. | A test with the cap set to a low value and a `create_issue` stub that succeeds asserts exactly that many issues are created and the rest are deferred with a log line; a second test makes the verification read fail and asserts zero further creates. |
| (d) | **The historical duplicates are closed.** #3382-#3397 and the enumerated 09-16 pairs are closed as duplicates pointing at their survivor. | `gh issue view` on each enumerated number reports `CLOSED` / `NOT_PLANNED`. |

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
  humans; the fingerprint cannot drift. It is the key the reconciliation sweep and the cap
  verification both query on, and it is what makes "is this a twin?" an exact-match question rather
  than a string-similarity one.
- **Pre-create re-read**: the open-issue map is refreshed immediately before each create rather
  than once per run, shrinking the check-then-act window from the whole filing loop to one call.
- **Reconciliation sweep**: after the filing loop, one read of issues created during this run's
  window; any fingerprint appearing more than once converges to its lowest number and the rest are
  closed as `NOT_PLANNED` with a pointer comment. This is the substitute for the compare-and-swap
  GitHub does not offer.
- **Comment-only investigation session**: `maybe_dispatch_triage_session()` is dispatched *after*
  filing, with real issue numbers, and its prompts instruct investigation and commenting only. The
  three filing prompts become one investigation prompt shape.
- **Deletions**: `write_triage_ledger()`, `NodeDisposition`, the ledger paragraph in
  `_build_triage_prompt`, and the filing halves of `ISSUE_LOOKUP_INSTRUCTION`. Per this repo's
  no-legacy-code rule these are removed outright, not left behind a flag.

### Flow

Nightly run completes → serial re-confirm → `dispatch_findings()` → collapse (environmental,
setup cascades, body cascades) → **comment branch** (existing, unchanged: open-issue and
closed-not-planned recurrences) → **file branch (new)**: for each survivor → refresh open map →
compute fingerprint → `create_issue()` → record real number → decrement budget →
**reconciliation sweep** → converge twins → verified `issues_filed` → **dispatch investigation
session** with the real numbers and a comment-only instruction → log `Tracker: N issue(s) filed`
where N is verified.

### Technical Approach

- **`create_issue` mirrors `comment_on_issue` deliberately.** Same `subprocess.run` shape, same
  `cwd=PROJECT_DIR`, same `--body-file -` stdin discipline, same "log and return falsy, never
  raise" error posture, same `dry_run` short-circuit. A reviewer should be able to diff the two
  functions and see only the verb change. This is also why the four fake-`gh` harnesses in the test
  file can stub it the same way they already stub `comment_on_issue`.
- **Where the create loop goes.** Replacing the block at `:2941-2970`. The comment at `:2954-2965`
  explaining why `dispositions` is withheld on a degraded read disappears with the mechanism it
  documents; the *reasoning* it encodes does not, and it moves to the budget contract below.
- **Two failure postures, deliberately asymmetric** (this is the answer to the critique's first
  CONCERN, and it must be stated in code comments as well as here):
  - The **dedup reads** (`open_issues`, `closed_issue_dispositions`) keep failing *open*. An
    unreadable GitHub on the night of a real regression must not produce a silent night; a possible
    duplicate is the smaller harm. Unchanged from today.
  - The **budget verification** fails *closed*. If the post-filing read cannot be answered, the
    remaining budget is treated as zero, the unfiled survivors are deferred to the next run with an
    explicit log line, and `issues_filed` reports only creates whose numbers were observed. The
    asymmetry is the point: silently assuming "0 filed so far" on a failed verification is exactly
    how #3382-#3405 happened, and this is the one place where over-filing, not silence, is the
    tracked harm. A deferred node is not added to `recorded`, so tomorrow's run picks it up.
- **Budget accounting is per-confirmed-create.** `issue_budget` decrements only when
  `create_issue()` returns a number. A failed create spends nothing. This alone removes the
  `:2966` class of bug regardless of whether the reconciliation sweep ever fires.
- **`DispatchOutcome`** gains `filed_issues: dict[str, int]` (finding key → real issue number),
  and `issues_filed` becomes a derived length rather than an independently-mutated counter — the
  two can then never disagree. `cascade_issues` stops needing its `None` "pending, the session will
  open it" sentinel, because the number is known at creation time; `carry_cascade_issues()`'s
  upgrade-on-a-later-run path becomes dead and is removed with it.
- **Prompt consolidation.** The per-node, cascade-umbrella, and re-baseline-seed prompts exist as
  three shapes because each had to teach an agent a different filing contract. With filing gone
  they collapse toward one investigation prompt that takes a list of `(number, subject)` pairs.
  `ISSUE_LOOKUP_INSTRUCTION`'s dedup-before-filing framing is removed; if any lookup guidance
  survives at all it keeps the search-index prohibition, so the two existing wording gates stay
  meaningful rather than being deleted along with the text they guard.
- **Tunables carry no new invented numbers.** The reconciliation window and any new threshold are
  named constants with `_DEFAULT` suffixes and `NIGHTLY_*` env overrides, following the module's
  established convention, each with a comment stating the value is provisional and what evidence
  would move it. `MAX_ISSUES_PER_RUN_DEFAULT` keeps its existing value — this plan changes what the
  budget is measured against, not how large it is.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `create_issue()` has one broad `except Exception` (matching `comment_on_issue` and
  `open_issues`, which catch `TimeoutExpired` / `FileNotFoundError` / parse errors alike). It must
  not be a bare `pass`: assert it logs a `WARNING` naming the title and returns `None`, and assert
  the caller leaves the node out of `recorded`. Test: `create_issue` with a subprocess raising
  `FileNotFoundError`, and separately with a non-zero return code, and separately with stdout that
  is not a parseable issue URL.
- [ ] The reconciliation sweep's own read must not raise into the filing path. A failing sweep logs
  a warning, leaves every created issue in place (the issues are real and must not be lost), and
  reports the creates it observed directly — assert this rather than allowing a silent swallow.
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
- [ ] Assert the budget-exhausted and verification-failed paths each emit their own distinct log
  line naming the deferred nodes, so the two are distinguishable in `logs/` the morning after.

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
- [ ] `TestIssueBudgetIsVerified` — NEW: cap set low, assert exactly the cap is created and the
  remainder is deferred and left out of `recorded`; and a second test where the verification read
  fails, asserting the fail-closed posture.
- [ ] `TestReconciliationSweep` — NEW: a fake GitHub seeded with two issues sharing one fingerprint;
  assert the lower number survives, the higher is closed `NOT_PLANNED`, and a pointer comment is
  posted.

**Test-run discipline:** per this repo's rules, run only
`scripts/pytest-clean.sh tests/unit/test_nightly_regression_tests.py`, never the full `tests/unit/`
tree (about 20 minutes, and parallel lanes collide on Redis state).

## Rabbit Holes

- **Making the create genuinely atomic.** It cannot be. GitHub offers no idempotency key and no
  conditional POST (`## Research`). Any design that reaches for a distributed lock, a mutex issue,
  or a "claim" label written before the create is spending real time to buy a smaller window, not a
  closed one. Refresh-then-create plus a convergent sweep is the ceiling; take it and move on.
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
- **Auditing every historical duplicate ever filed.** Task 5 closes the enumerated set from the
  issue body. A general sweep of the tracker's whole duplicate history is a different job.

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
**Mitigation:** This is an explicit `## Open Questions` item for the PM. The plan is structured so
that deleting the dispatch entirely is a strictly smaller change than keeping it: if the answer is
"drop it", Task 4 shrinks rather than growing.

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
handled by Race 3's sweep. Cost is one extra `gh` read per created issue, which is bounded by
`NIGHTLY_MAX_ISSUES_PER_RUN` and therefore small.

### Race 3: Two hosts, or a create whose response was lost — CONVERGED, NOT PREVENTED
**Location:** the new `create_issue()` call site and the reconciliation sweep after the loop.
**Trigger:** Two machines run the nightly against the same repo and both pass the pre-create
re-read before either create lands; or one `gh issue create` succeeds server-side but its response
is lost, so the client cannot tell whether an issue exists.
**Data prerequisite:** a duplicate must be *detectable* after the fact with an exact key. Titles are
not that key — humans edit titles, and the cascade path already deliberately keys on a signature
rather than a title (`resolve_cascade_issue`, line 1165, for exactly this reason).
**State prerequisite:** eventually, exactly one live issue per finding.
**Mitigation — convergence.** This is the standard client-side substitute for the idempotency key
GitHub does not offer:
1. **Deterministic fingerprint.** Every body the detector creates carries
   `<!-- nightly-fingerprint: {sha256 of the finding's stable identity} -->`. Two hosts filing the
   same finding produce byte-identical fingerprints without coordinating.
2. **Reconciliation sweep.** After the filing loop, one read of issues created in this run's
   window. Any fingerprint appearing more than once converges to its lowest-numbered issue; the
   others are closed `NOT_PLANNED` with a comment pointing at the survivor. Closing as
   `NOT_PLANNED` rather than `COMPLETED` is deliberate and interlocks with existing behavior: the
   closed-state dedup at `:2128` treats `NOT_PLANNED` as "already on record, comment don't re-file"
   and `COMPLETED` as "fixed, a recurrence deserves a fresh issue". Closing a twin as completed
   would license tomorrow's run to re-file it.
3. **Never retry a create.** A create whose response was lost is left alone within the run; the
   sweep or the next night's dedup read resolves it. Retrying a non-idempotent POST is how a lost
   response becomes a second issue (`## Research`).
**Residual risk, stated:** between a duplicate create and the sweep, two issues exist — seconds,
not the ten-minute waves of #3382-#3405. If the sweep itself fails, the duplicates persist until
the next night's `open_issues` read sees them, which is a two-issue outcome rather than the 24-issue
outcome this plan exists to end. That degradation is acceptable and is why the sweep is allowed to
fail without failing the run.

### Race 4: Budget verification read races the creates it is counting — FAILS CLOSED
**Location:** the post-filing verification read.
**Trigger:** GitHub's list endpoint is read immediately after creates land; a create that has not
yet propagated is undercounted, or the read times out entirely.
**Data prerequisite:** the verified count must never *under*-report in a way that licenses more
filing.
**State prerequisite:** `issues_filed` must be ≤ the number of issues that exist.
**Mitigation:** the budget is decremented **per confirmed create** during the loop, from the number
`create_issue()` returned — it does not wait on the verification read, so a lagging read cannot
license an extra create. The verification read is a *reconciliation and reporting* step, not the
gate. If it fails or disagrees, the remaining budget is treated as zero, unfiled survivors are
deferred to the next run with a named log line, and they stay out of `recorded`. This is
deliberately the opposite posture from `open_issues`' fail-open (see `## Solution` → Technical
Approach): the dedup reads fail open because silence during a real regression is the larger harm,
and the budget check fails closed because over-filing is the specific harm on record here.

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
- [DESTRUCTIVE] **A general sweep of the tracker's historical duplicates.** Task 5 closes the
  enumerated set from the issue body (#3382-#3397 and the named 09-16 pairs) and nothing else.
  Closing issues is one-shot and review-before-execute is the safety mechanism.

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
- **On rollback, the duplicates closed in Task 5 stay closed.** They are genuine duplicates of
  issues that remain open; their closure is independent of which code path files future issues.

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
- **`tools.valor_session create --role eng` remains the dispatch mechanism** if the investigation
  session survives (`## Open Questions`), unchanged in shape; only its prompt payload changes.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/nightly-triage-dispatch.md` — this is the primary target. It documents
  the dispatch-and-file architecture this plan inverts: rewrite the filing section to describe the
  detector as the sole issue creator, describe the fingerprint and the reconciliation sweep, and
  remove the session-ledger description entirely (no "formerly" paragraph — describe the new status
  quo only, per this repo's no-legacy-code rule).
- [ ] Update `docs/features/nightly-regression-tests.md` — correct the `Tracker: N issue(s) filed`
  semantics (verified count, not self-reported tally) and document the fail-open/fail-closed
  asymmetry between the dedup reads and the budget check, since that asymmetry is surprising and a
  future maintainer will otherwise "fix" it into consistency.
- [ ] Update `docs/features/README.md` index rows for both files if their one-line summaries name
  the session as the filer.

### External Documentation Site
- [ ] Not applicable — this repo publishes no external docs site for internal tooling.

### Inline Documentation
- [ ] `create_issue()` docstring states the no-retry contract and cites the absence of a GitHub
  idempotency key, so the next maintainer does not add retry logic.
- [ ] The fail-closed comment on the budget verification explicitly contrasts itself with
  `open_issues`' fail-open, naming #3418, so the asymmetry reads as deliberate.
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
- [ ] The budget verification fails closed: an unreadable verification read defers the remainder
  and files nothing further.
- [ ] `write_triage_ledger`, `NodeDisposition`, and the ledger prompt paragraph are gone from the
  module — deleted, not flagged off.
- [ ] `NIGHTLY_AUTO_FILE=false` produces a run that comments, logs every would-be filing, and
  creates nothing.
- [ ] #3382-#3397 and the enumerated 09-16 pairs are CLOSED as `NOT_PLANNED` with a pointer to
  their survivor.
- [ ] **Human-readable outcome check** (not a count): after the change, take the next real nightly
  run's issue list and have a reader who is not the filer — Tom, or whoever triages that morning —
  state, for each issue, what is broken, without opening the linked node logs. Every issue must be
  self-describing from its title and first paragraph. An issue that fails this read is a filing-body
  defect even if the counts are perfect; the whole point of the change is that a morning's triage
  is legible, not merely short.
- [ ] Tests pass (`/do-test`, scoped to `tests/unit/test_nightly_regression_tests.py`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No xfail conversions apply — `grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/unit/test_nightly_regression_tests.py` returns nothing.

## Team Orchestration

### Team Members

- **Builder (filing path)**
  - Name: `filing-builder`
  - Role: `create_issue`, the fingerprint, the create loop, budget accounting, the reconciliation
    sweep — all inside `scripts/nightly_regression_tests.py`
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
- **Validates**: `tests/unit/test_nightly_regression_tests.py::TestDispatchFindings`, `::TestDispositionHandoff` (replace), `::TestIssueBudgetIsVerified` (create)
- **Assigned To**: filing-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace the per-node dispatch block at `:2941-2970` and the cascade dispatch at `:2856-2866`
  with calls to `create_issue()`.
- Refresh the open-issue map immediately before each create (Race 2).
- Decrement `issue_budget` and extend `recorded` only on a confirmed number (Race 4).
- Add `DispatchOutcome.filed_issues: dict[str, int]`; make `issues_filed` derived from it.
- Remove the `None` sentinel from `cascade_issues` and the now-dead upgrade path in
  `carry_cascade_issues()`.
- Add the `NIGHTLY_AUTO_FILE` kill switch: when false, log every would-be filing in full and create
  nothing.

### 3. Reconciliation sweep and verified budget
- **Task ID**: build-reconcile
- **Depends On**: build-filing-loop
- **Validates**: `tests/unit/test_nightly_regression_tests.py::TestReconciliationSweep` (create), `::TestIssueBudgetIsVerified`
- **Assigned To**: filing-builder
- **Agent Type**: builder
- **Parallel**: false
- After the filing loop, read back issues created in this run's window and group by fingerprint.
- Converge any duplicated fingerprint to its lowest number; close the others `NOT_PLANNED` with a
  pointer comment.
- The sweep may fail without failing the run — log a warning, keep every created issue, report the
  creates that were observed directly.
- Distinct log lines for budget-exhausted vs. verification-failed deferral, each naming the nodes.

### 4. Retire the ledger, the dispositions, and the filing prompts
- **Task ID**: build-retire-agent-filing
- **Depends On**: build-reconcile
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
- **Gate:** if the PM answers Open Question 1 with "drop the session", this task instead deletes
  `maybe_dispatch_triage_session` and all three prompt builders, and becomes smaller.

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
- Write `TestCreateIssue`, `TestFilingIdempotence`, `TestReconciliationSweep`,
  `TestIssueBudgetIsVerified`.
- Run only this file. Never the full `tests/unit/` tree.

### 6. Close the historical duplicates
- **Task ID**: cleanup-duplicates
- **Depends On**: build-tests
- **Assigned To**: filing-builder
- **Agent Type**: builder
- **Parallel**: false
- Close #3382-#3397 as `NOT_PLANNED`, each with a comment naming its surviving twin
  (#3398-#3405 are the survivors for the 09-17 set; keep the lowest-numbered issue per node and
  close the rest, matching the sweep's own rule).
- Close the 09-16 duplicate pairs listed in the issue body, keeping the lower number in each pair.
- Enumerate the closures in the PR body so the one-shot is reviewable before it runs.

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: nightly-documentarian
- **Agent Type**: documentarian
- **Parallel**: true
- Execute the `## Documentation` checklist.

### 8. Cruft review
- **Task ID**: review-cruft
- **Depends On**: build-tests, document-feature
- **Assigned To**: filing-reviewer
- **Agent Type**: cruft-auditor
- **Parallel**: false
- Scan the diff for dropped test assertions, for any surviving path by which an agent could create
  an issue, and for leftover ledger/disposition references.

### 9. Final validation
- **Task ID**: validate-all
- **Depends On**: build-create-issue, build-filing-loop, build-reconcile, build-retire-agent-filing, build-tests, cleanup-duplicates, document-feature, review-cruft
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
| Budget verification test exists | `grep -c "class TestIssueBudgetIsVerified" tests/unit/test_nightly_regression_tests.py` | output > 0 |
| Kill switch wired | `grep -c "NIGHTLY_AUTO_FILE" scripts/nightly_regression_tests.py` | output > 0 |
| Anti-criterion: no prompt tells an agent to create an issue | `grep -c "gh issue create" scripts/nightly_regression_tests.py` | match count == 0 |
| Anti-criterion: ledger and dispositions are gone | `grep -c "write_triage_ledger\|NodeDisposition" scripts/nightly_regression_tests.py` | match count == 0 |
| Anti-criterion: #3419's collapsing logic untouched | `git diff origin/main -- scripts/nightly_regression_tests.py \| grep -c "group_body_failure_cascades\|BODY_CASCADE_MIN_GROUP_SIZE"` | match count == 0 |
| Anti-criterion: #3243's hostname stamp not landed here | `git diff origin/main -- scripts/nightly_regression_tests.py \| grep -c "gethostname\|hostname"` | match count == 0 |
| No stale xfails | `grep -rn 'xfail' tests/unit/test_nightly_regression_tests.py` | exit code 1 |

## Critique Results

### Round 1 — 2026-09-18, verdict NEEDS REVISION (4 blockers, 3 concerns, 1 nit)

Critiqued against the skeleton at `56a980d8a`, in which every section but this table was an empty
header. All four blockers are addressed in this revision.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|----------------------|
| BLOCKER | Structural Check | Required sections `## Documentation`, `## Update System`, `## Agent Integration`, and `## Test Impact` are all empty placeholders — this repo's plan-critique addendum treats any missing/placeholder required section as a HIGH-severity blocker. | RESOLVED — all four populated. `## Test Impact` carries per-function dispositions for every affected test in `tests/unit/test_nightly_regression_tests.py` with line numbers; `## Documentation` names `docs/features/nightly-triage-dispatch.md` and `nightly-regression-tests.md` as checkbox tasks; `## Update System` records "no update-script change", the dead `data/nightly-triage-ledger/` state, and the `NIGHTLY_AUTO_FILE` break-glass; `## Agent Integration` records that the change is subtractive (the agent loses `gh issue create`, gains nothing). | The `## Test Impact` audit found four near-identical fake-`gh` harnesses (lines 1241, 1406, 2580, 2800), none of which stubs a create function — a real `gh issue create` would escape into a subprocess during unit tests unless all four are updated. Task 5 extracts them into one fixture. |
| BLOCKER | Risk & Robustness | `## Race Conditions` is empty, yet the bug itself is a check-then-act race: concurrent/replayed triage waves each check "does this issue exist" then create it, and two waves can both pass the check before either write lands. Moving filing into the detector does not remove this race unless the detector serializes the check+create into one atomic step. | RESOLVED, with one correction to the finding's premise. `## Race Conditions` now enumerates four races and states for each whether it is eliminated, shrunk, or converged. The critique's prescription — "serialize the check+create into one atomic step" — is **not implementable**: `## Research` establishes that GitHub's REST API offers no idempotency key and no conditional POST, so no compare-and-swap primitive exists at this boundary. The design instead eliminates Race 1 (the replayed-turn race that actually fired: a Python loop under the existing fcntl run lock has no replay semantics), shrinks Race 2 to one `gh` round-trip via a pre-create map refresh, and converges Race 3 via a deterministic body fingerprint plus a post-filing reconciliation sweep. | The residual window is stated rather than claimed away: between a racing create and the sweep, two issues exist for seconds — versus the ten-minute waves that produced #3382-#3405. Duplicates are closed `NOT_PLANNED`, not `COMPLETED`, because the existing closed-state dedup at `:2128` treats `COMPLETED` as a license to re-file. |
| BLOCKER | Scope & Value | The title bundles two independently-scoped fixes — moving issue-filing ownership into the detector (an architecture change), and changing the collapsing key to per-file/root-cause (a dedup-algorithm change) — with no `## Solution` text justifying why they ship together rather than as two separately appetite-sized changes. | RESOLVED by an actual split rather than a justification. On 2026-09-18 the owner narrowed #3418 to filing ownership and filed the collapsing work as **#3419**. This plan covers filing ownership only. `## Solution` → Scope itemizes the four in-scope sub-changes (a)-(d), each with its own acceptance check, and `## No-Gos` carries `[SEPARATE-SLUG #3419]` with a `## Verification` anti-criterion asserting `group_body_failure_cascades` is untouched by the diff. | The plan's H1 was also narrowed to "Nightly triage filing moves into the detector". The filename retains the older slug; per `docs/features/sdlc-lane-identity.md` a plan links to its lane through `tracking:` frontmatter, not filename, so the two are allowed to differ and renaming was not worth the churn. |
| BLOCKER | History & Consistency | `## Why Previous Fixes Failed` is empty even though the tracking issue states this is explicitly a repeat of the #3170 failure class, where "the ledger and lookup-instruction defenses added after #3170 did not hold here." This is the single most important input for judging whether the new detector-side approach actually closes the gap. | RESOLVED — `## Why Previous Fixes Failed` is a five-row table (#2559, #3134, #3075, #3131, #3170) with a root-cause pattern and an explicit three-point argument for why detector-side Python closes the gap, plus an explicit statement of what it does **not** close. | The sharpest evidence: #3170's three defenses are all *prompt inputs*, and the ledger's `filed` array is maintained **by the agent it defends against** — `write_triage_ledger` is called once from `maybe_dispatch_triage_session` and nowhere else, so its "leave a non-empty `filed` alone" guard at `:2450` could not possibly have fired mid-session. That is why waves 1 and 2 left no trace on disk. |
| CONCERN | Risk & Robustness | `## Solution` has no design for the per-file/root-cause collapsing logic, the N=3 umbrella threshold, or the "query GitHub for issues actually created during the run" cap mechanism, and no stated failure-mode contract for that cap query (timeout, stale/paginated result). | ADDRESSED. The collapsing half moved to #3419. The cap mechanism is specified in `## Solution` → Technical Approach and Race 4: the budget decrements **per confirmed create** during the loop (so a lagging verification read can never license an extra create), and the verification read **fails closed** — unreadable means remaining budget is zero, survivors are deferred to the next run with a named log line and stay out of `recorded`. | The failure posture is deliberately asymmetric and both halves are documented as such: the dedup reads (`open_issues`, `closed_issue_dispositions`) keep failing **open** because silence during a real regression is the larger harm; the budget check fails **closed** because over-filing is the specific harm on record. `## Documentation` requires an inline comment naming this contrast so a future maintainer does not "fix" it into consistency. |
| CONCERN | Risk & Robustness | No populated Rollback/Update System content describing how to revert if detector-side filing misbehaves in production (e.g., over-collapses distinct root causes into one umbrella issue). | ADDRESSED, with the suggested mechanism deliberately rejected. `## Update System` specifies `NIGHTLY_AUTO_FILE=false` as a **kill switch**: the detector still comments and logs every would-be filing in full, so an operator can file by hand from the log. The critique's suggestion of a flag reverting to LLM-session filing is rejected as the parallel-migration this repo's no-legacy-code rule forbids — it would leave the duplicate-filing bug one env var away forever. `git revert` of a single commit is the real remedy. | Rollback disposition of the Task 6 closures is stated explicitly: they stay closed. They are genuine duplicates of issues that remain open, and their closure is independent of which code path files future issues. |
| CONCERN | Scope & Value | `## Success Criteria` is empty; the only success measures available anywhere (in the tracking issue, not the plan) are purely technical issue-counts, with no human-facing validation that an engineer actually gets clearer signal. | ADDRESSED. `## Success Criteria` carries a human-readable outcome check: after the change, the next real nightly run's issues must each be self-describing from title and first paragraph to a reader who is not the filer, without opening node logs. An issue failing that read is a filing-body defect even when the counts are perfect. | Deliberately phrased as "the morning's triage is legible, not merely short" — a run that files one unreadable umbrella would pass every count-based criterion and still fail the purpose. |
| CONCERN | History & Consistency | `## Prior Art` is empty despite four directly relevant closed issues (#3170, #3131, #3134, #3075, prior passes this change supersedes) and two related open issues to coordinate with (#3347, #3243) named in the tracking issue. | ADDRESSED. `## Prior Art` covers five closed issues (#2559 as well as the four named) with what each did and why it did not hold, and both open issues with an explicit coordination note. #3243 is cross-referenced in `## Prior Art`, `## Rabbit Holes`, and `## No-Gos`. | #3134 is called out as the half that **works** — it is why #3375-#3380 were correctly commented on the same night 24 twins were filed — because it is the existence proof that Python-side tracker writes are already reliable in this module. |
| NIT | History & Consistency | `## Solution` / `## No-Gos (Out of Scope)` are both empty, so it's unverifiable whether "close the enumerated duplicate issues as part of this work" (a tracking-issue acceptance criterion) is captured as in-scope work or silently dropped. | ADDRESSED. Captured three times: sub-change (d) in `## Solution` → Scope with its own acceptance check, Task 6 `cleanup-duplicates` in `## Step by Step Tasks`, and a `## Success Criteria` row. | Task 6 requires the closures to be enumerated in the PR body before they run, since closing issues is a one-shot `[DESTRUCTIVE]`-class action and review-before-execute is the safety mechanism. |

---

## Open Questions

1. **Does the investigation session survive at all?** With filing gone, a nightly LLM session that
   only comments may be pure cost. The plan keeps it (dispatched after filing, handed real issue
   numbers, comment-only) because that is what the issue's proposed cull describes, and because
   root-cause narrative on a fresh issue has real triage value. But "drop it entirely" is a
   strictly *smaller* change — Task 4 shrinks to a deletion — and would remove the last path by
   which an agent touches the tracker unsupervised. Which way?
2. **Should the reconciliation sweep close duplicates automatically, or only report them?**
   Auto-closing is what makes the convergence guarantee real, but it means the detector closes
   GitHub issues without a human in the loop — a new privilege it has never held. The conservative
   alternative is to log the duplicate fingerprints and let the next morning's triage close them.
   Auto-close is planned; confirm that the new privilege is acceptable.
3. **`NIGHTLY_MAX_ISSUES_PER_RUN` stays at its current default.** The issue's proposed cull asked
   to "lower the per-run issue budget default". This plan deliberately does not: the 09-17 overrun
   was a broken measurement, not a too-generous cap, and lowering the cap while the measurement was
   wrong would only have deferred real regressions. Confirm that leaving the default alone and
   fixing the measurement is the intended reading, or name the value you want.
