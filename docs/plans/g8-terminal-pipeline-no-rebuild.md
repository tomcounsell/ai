---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-14
tracking: https://github.com/tomcounsell/ai/issues/2757
last_comment_id:
revision_applied: true
revision_applied_at: 2026-08-16T04:19:27Z
---

# G8 must not rebuild shipped work: an absent PR number is unverifiable, not falsified

## Problem

After a PR merges and its issue closes, `sdlc-tool next-skill --issue-number N`
tells the pipeline to build the work it just shipped:

```
stage-artifact-verify: issue #N BUILD claims completed but PR None is not open or merged (state=None)
{"skill": "/do-build", "reason": "G8: BUILD claims completed but its artifact failed live
 verification — re-dispatching /do-build rather than advancing", "row_id": "G8", "dispatched": true}
```

Observed on 2026-08-13 on all three issues merged by that night's PR fan-out —
#2638 (PR #2686), #2566 (PR #2665), #2640 (PR #2671) — identical output for each,
minutes after their merges.

**Current behavior:**

`tools/sdlc_next_skill.py::_verify_stage_artifacts_live` (`:251-278`) reads:

```python
pr_number = meta.get("pr_number")
...
pr_state: str | None = None
if pr_number and (build_claimed or patch_claimed):
    pr_state = _fetch_pr_state(pr_number, repo=repo)

if build_claimed:
    if not pr_number or pr_state not in ("OPEN", "MERGED"):
        ... return {"stage_artifacts_verified": False, "unverified_stage": "BUILD"}
```

The issue title says the verifier "resolves PR None once the PR is closed." It does
not. The live read is itself guarded by `if pr_number` (`:269`), so when `pr_number`
is absent **`_fetch_pr_state` is never called**. The `state=None` in the log line is
the initialized default at `:268`, not an answer from GitHub. The single failing
term is `not pr_number` (`:273`): the verifier treats *nothing to look up* as
*the lookup came back empty*, and reports a fabricated BUILD claim.

The merged-PR path added by #1267 is already correct — `pr_state not in ("OPEN",
"MERGED")` passes a MERGED PR. The hole is one level up, and it is compounded by a
second omission: **the verifier never consults the MERGE marker**, which is sitting
in the `stage_states` dict it was handed. A pipeline that has demonstrably finished
gets its BUILD claim adjudicated as if it were mid-flight.

`_fetch_pr_state`'s own docstring already states the principle this violates
(`:100-118`): *"callers must treat `None` as 'could not determine', never as
evidence of a false claim."* The `not pr_number` term breaks that contract before
the function is even reached. The sibling PATCH check three lines below gets it
right — `patch_claimed = stage_states.get("PATCH") == "completed" and
bool(lane_branch)` (`:257`), with a debug log explaining that a missing artifact
identifier means *skip the probe*, not *fail the claim* (`:258-263`, added by
#2718). BUILD is the same shape missing the same guard.

**Why `pr_number` is absent in the first place — the proximate cause, one layer up.**

The paragraphs above are a correct description of the verifier, but they stop one
layer short of the cause, and the critique's war room was right that this changes
which fix is correct.

`_meta["pr_number"]` is resolved by `tools/sdlc_stage_query.py::_compute_meta`, which
calls `_lookup_pr(issue_number, slug=slug, repo=resolved_repo)` (`:548`) — without a
`state` argument. `_lookup_pr`'s signature defaults to **`state: str = "open"`**
(`:338-342`). A merged PR is not open, so it is invisible to every resolution path in
that function. That is precisely why the symptom appears "minutes after their merges"
and not before: the merge is the event that makes the PR unresolvable.

Measured on the three reported issues (2026-08-16, live `gh`):

| Issue | `_lookup_pr(state="open")` | `_lookup_pr(state="merged")` |
|---|---|---|
| #2638 | `None` | **2686** |
| #2566 | `None` | **2665** |
| #2640 | `None` | **2671** |

Those are exactly the PR numbers this plan's own Problem statement names. The
`pr_number` was never unrecoverable; it was looked up under a filter that excludes
the only state it could be in.

`tools/sdlc_stage_marker.py:246-250` already fixed this same defect at a sibling call
site under **#2539**, with a comment that says the quiet part directly: *"the artifact
question is historical, not in-flight. Under the default `open` a merged PR resolves
to None and this probe returned False before ever looking for the artifact."* The
verifier's call site never got the same treatment.

**Why silencing G8 is not sufficient — the rebuild relocates to row 5.**

Making G8 no-op does not stop the rebuild. `agent/sdlc_router.py::_rule_branch_exists_no_pr`
(`:922-927`) returns `True` on `context["branch_exists"] is True` alone whenever
`pr_number` is falsy, regardless of `BUILD == "completed"`, and row 5 precedes every
PR-gated row. Measured through the real router:

| `pr_number` | `branch_exists` | dispatch |
|---|---|---|
| absent | `True` | **`/do-build`, row 5** |
| absent | `False` | `Blocked(NO_RULE)` |
| **2686** | `True` | **`/do-merge`, row 10** |
| **2686** | `False` | **`/do-merge`, row 10** |

This repo reports `deleteBranchOnMerge: false` and merged lane branches persist on
origin, so the `branch_exists: True` row is the **normal** post-merge case here, not
an exotic one. An earlier draft of this plan asserted only the absence of a *G8*
`/do-build` — an assertion that would have gone green while the bug continued
unabated at row 5.

The same table shows the converse: restoring `pr_number` reaches the terminal row
under **both** branch states, which simultaneously stops the row-5 rebuild, lets G8
pass on its own merits rather than by being silenced, and deletes the
`Blocked(NO_RULE)` residual that motivated #2817.

**Desired outcome:**

A terminal pipeline is never told to rebuild — **at any row**, not merely at G8. This
is achieved primarily by *resolving* the artifact identifier rather than by
suppressing the check that misses it. Secondarily, the artifact verifier
distinguishes three states rather than two —

| State | Meaning | Verdict |
|---|---|---|
| verified | the artifact identifier resolves and the world confirms it | no-op |
| falsified | the identifier resolves and the world contradicts it | `stage_artifacts_verified: False` |
| **unverifiable** | there is no identifier to resolve | **no-op, with a debug log** |

Today the third collapses into the second. After this change it collapses into the
first, matching `_fetch_pr_state`'s stated contract and the general rule that a gate
must not manufacture evidence it never gathered.

Two honest caveats on that secondary change, which an earlier draft glossed:

- **It is a real reduction in coverage, not a free win.** #2718's PATCH guard skipped
  a *guessed* identifier, so skipping lost nothing. An absent `pr_number` is
  different: a lane that genuinely never opened a PR stops tripping G8. Risk 2 states
  this plainly and names the surviving owners. The PATCH↔BUILD analogy is a guide to
  the *code shape*, not a claim that the two cases are equivalent in what they give
  up.
- **After the primary fix it is rarely reached.** Once `pr_number` resolves for
  merged PRs, the unverifiable branch covers only a genuinely PR-less lane or a
  degraded ledger. It ships as defense in depth against the residual, not as the
  mechanism that fixes #2757.

## Freshness Check

**Baseline commit:** `1c48b97f2` (`main`, clean except an unrelated in-flight plan
edit by a peer lane; `main` moved twice during plan authoring — the referenced
line numbers were re-read at this SHA)
**Issue filed at:** 2026-08-13T05:22:10Z
**Disposition:** Minor drift

**File:line references re-verified:**

- `tools/sdlc_next_skill.py:251-278` — the BUILD/PATCH artifact block the issue
  describes — **holds verbatim**, including the `if pr_number and (build_claimed or
  patch_claimed)` guard at `:269` that the issue's root-cause paragraph does not
  account for.
- `tools/sdlc_next_skill.py:100-127` — `_fetch_pr_state` and its
  "never as evidence of a false claim" contract — **holds**.
- `tools/sdlc_next_skill.py:257-263` — the PATCH check's `bool(lane_branch)` guard
  and its no-guessing debug log — **holds**; this is the in-file precedent the fix
  copies.
- `agent/sdlc_router.py:739-741` — `guard_g6_terminal_merge_ready` returns `None`
  when `pr_number` is absent — **holds**, and is what makes guard reordering a
  non-fix (see Spike Results).
- `agent/sdlc_router.py:771-780` — `GUARDS` order `[G1, G2, G3, G4, G8, G7, G5, G6]`
  — **holds**.
- `tools/_sdlc_run_identity.py:114-132` — `_pipeline_is_terminal` is exactly
  `states.get("MERGE") == "completed"`, fail-open to `False` — **holds**.

**Cited sibling issues/PRs re-checked:**

- **#2735** — the issue names this as the same G8-verifier family. Closed; shipped
  as PR **#2792** (`e50eba258`), which introduced `tools/lane_identity.py` and
  reshaped the PLAN/PATCH halves of this exact function. Distinct trigger, shared
  function — its line numbers, not its logic, are the drift source.
- **#1267** — the issue that built this gate, including the merged-PR fix the issue
  body assumes is missing. Closed and present.
- **#2062 / PR #2790** (`706fc4da0`, "Honest finalize refusals, a clean verdict
  field, ... a router row that owns the stale-verdict state") — merged 2026-08-13,
  **after** #2757 was filed and touching `agent/sdlc_router.py`. It did not change
  the verifier, but it is the reason the adjacent test is red (see Spike Results,
  spike-5).

**Foreign-PR reconciliation: PR #2794 / issue #2793 (checked 2026-08-16).**

A closed-unmerged PR edited the same function this plan edits, so its disposition was
reconciled before any code was written.

- **#2793 is closed `NOT_PLANNED`** (2026-08-13T13:14:04Z, by the repo owner):
  "Duplicate of #2735 + #2718." Both mechanisms it reported were already tracked and
  were closed by `docs/plans/sdlc-lane-recorded-slug.md`.
- **PR #2794 is closed unmerged** (2026-08-14T03:45:20Z), superseded. It was also
  measured to regress the suite (33 failed / 77 passed) because it removed
  `find_plan_path` from `tools/_sdlc_utils.py` rather than extending it.
- **What actually landed instead: PR #2792 (`e50eba258`).** Verified on main today:
  `find_plan_path` now lives at `tools/lane_identity.py:123` with a single-rung
  `tracking:`-frontmatter contract, and the bare-`#N` fallback was **deleted as a
  behavior**, not made suppressible. #2794's proposed `tracking_only=True` kwarg has
  nothing left to suppress. The `session/{slug}` reconstruction is gated on a
  *recorded* lane slug, so an unrecorded lane no-ops instead of probing a guess.
- **Consequence for this plan's scope:** nothing in #2794 is live work, and no part of
  it needs re-doing here. Its one durable observation — that
  `test_terminal_merged_pipeline_routes_to_merge_not_build` is red on baseline `main`
  at row 8e — is independently reproduced by this plan's spike-5 and is picked up
  inside this fence. The `headRefName` enhancement #2794 also proposed is **not**
  adopted: it is an enhancement on top of a shipped fix, and this plan's revised
  approach removes the need for it (an absent `pr_number` is now *resolved*, not
  worked around).

**Commits on main since issue was filed (touching referenced files):**

- `e50eba258` (#2735 → PR #2792) — **irrelevant to the root cause**; reshaped the
  PLAN/PATCH artifact resolution in the same function, left the BUILD branch alone.
- `706fc4da0` (#2740/#2767/#2769/#2790) — **partially explains an adjacent
  symptom**: it added the `head_sha` verdict field and row 8f. It is why the
  integration test named in this plan fails at row 8e rather than at G8.
- `ec585ceb9` (#2803, merged 2026-08-14) — did not touch the verifier, but see
  Risk 1: the three cited issues' ledgers now read entirely empty.

**Active plans in `docs/plans/` overlapping this area:**

- `next-skill-self-lock-run-identity.md` (#2766, `status: Ready`) — same **file**,
  different function (the issue-lock peek block, `:513-549`). **Explicitly outside
  this plan's fence**; do not touch that block.
- `router-docs-skip-trivial.md` (#1799, `status: Ready`) — extends
  `_build_context` in the same file. Adjacent, non-conflicting: this plan changes
  `_verify_stage_artifacts_live`, which `_build_context` calls, not `_build_context`
  itself.
- `pipeline-graph-single-source-of-truth.md` (#2491, `status: Planning`, Large) —
  would eventually restructure the router/next-skill boundary wholesale. Not a
  blocker; this fix is a ten-line change inside a function that plan would inherit.
- `ledger-integrity.md` (#2730, `status: Ready`) — the ledger-durability lane. Its
  subject matter is the *cause* of the empty ledgers this plan must tolerate, not
  the fix for this bug. No file overlap.

**Notes:** No drift changed the premise. The one substantive correction is to the
issue's own root-cause paragraph — recorded in the Recon Summary now on the issue,
and load-bearing enough that it changes which fix is correct (see Key Elements).

## Prior Art

- **#1267 (the gate itself)** — introduced `_verify_stage_artifacts_live`, guard G8,
  and the three-artifact table. **Succeeded**, and later self-corrected: its own
  docstring (`:199-209`) records a "merged-pipeline misfire" where an already-MERGED
  PR read as an unverified BUILD artifact and re-dispatched `/do-build` forever.
  **#2757 is the same failure mode reached through the other door** — not a PR whose
  state reads wrong, but a PR number that is not there to read. The fix belongs in
  the same paragraph of the same docstring.
- **#2718 → the PATCH `bool(lane_branch)` guard (`:254-263`)** — the closest prior
  fix, and a direct template. A PATCH claim with no recorded lane slug used to probe
  a *guessed* branch name, force-dispatching `/do-patch` against a clean worktree
  until G4's oscillation cap hard-blocked the lane. The fix was to no-op the check
  when the artifact identifier is absent, and log at debug why. This plan applies
  that exact shape to BUILD. **Succeeded.**
- **#2078 → the `_target_repo_cwd()` threading (`:71-83`)** — a third instance of
  the same family: the verifier inspected the wrong repo, a genuinely-committed plan
  read as unverified, and G8 re-dispatched `/do-plan` forever. **Succeeded.** The
  pattern across all three is worth naming because it is the real subject of this
  plan: *every G8 regression to date has been the verifier reporting "falsified" for
  a check it was not actually in a position to perform.*
- **#2735 and #2718 → the same commit, PR #2792 (`e50eba258`)** — reshaped PLAN/PATCH
  resolution in this function and supplies `find_plan_path` / `resolve_lane_slug` /
  `lane_branch_name`. **Reconciling the two descriptions elsewhere in this plan:**
  Freshness calls it "irrelevant to the root cause" and this section originally
  called #2718's guard "the closest prior fix — Succeeded". Both are half-right and
  the combination was misleading. Precisely: it is **irrelevant to the cause** (it did
  not touch the BUILD branch or the PR lookup) and **relevant as a code template**
  (its `bool(lane_branch)` guard is the shape this plan mirrors). The "Succeeded"
  confidence is **downgraded**: that template is one day old and carries two unit
  tests of coverage, so it is a design precedent, not a battle-tested one.
- **#2539 → `tools/sdlc_stage_marker.py:246-250`** — the direct precedent for this
  plan's primary fix: the identical `state="open"` defect at a sibling `_lookup_pr`
  call site, fixed by passing `state="all"` with a comment explaining that the
  artifact question is historical rather than in-flight. **Succeeded.** This plan
  applies the same correction at the call site #2539 did not reach.
- **#2091 → `docs/plans/completed/fix-sdlc-router-merge-termination.md`** — already
  adjudicated the stale-router-fixture class this plan's red integration test belongs
  to, and produced a guardrail note at `docs/sdlc/do-test.md:156-166`. **Succeeded,
  but under-scoped**: the note covers `tests/unit/test_sdlc_router*.py` only, which is
  exactly how the integration fixture repaired here escaped it. Widening that scope
  is folded into this plan's Documentation section.
- **PR #2033 (#2029) "dispatch precedence, convergence latch, outcome-verified
  advance"** and **PR #2076 (umbrella #2026)** — the two merged PRs a prior-art
  search surfaces for "artifact verification G8". Neither touched the BUILD branch's
  `not pr_number` term.

No closed issue matches "G8 rebuilds merged work with no pr_number" — #2757 is the
first report of this specific trigger.

## Research

Purely internal. No external library, API, or ecosystem pattern is involved: the
change is a predicate correction inside one private function, plus a fixture repair
in two test files. `gh` and `git` usage is unchanged — in fact the fix *removes* a
call path rather than adding one.

No relevant external findings — proceeding with codebase context and training data.

## Spike Results

All spikes were run read-only against the live system on 2026-08-14 at `1c48b97f2`.
Router spikes call `agent.sdlc_router.decide_next_dispatch` directly (a pure
function over three dicts — no Redis, no `gh`).

> **Spike status after the 2026-08-14 war room and the 2026-08-16 re-measurement.**
> spike-1 is **partially invalidated** (it built its context by hand and so never saw
> `branch_exists`); spike-3 and spike-4 are **superseded** by spike-8/9/10, which
> locate the cause one layer up. They are retained below with correction notes
> because their measurements are still true of the code they probed — the error was
> in scope, not in arithmetic. spike-2, spike-5, spike-6 and spike-7 stand unamended.

### spike-1: After the verifier is fixed, where does a terminal no-`pr_number` pipeline actually route?
- **Assumption**: "Stopping G8 from firing lets the terminal rows (G6 / row 10) own the outcome."
- **Method**: prototype (direct `decide_next_dispatch` calls)
- **CORRECTION (2026-08-16)**: this spike passed a **hand-built context** to
  `decide_next_dispatch`, bypassing `_build_context` — the only producer of
  `branch_exists`. With that key absent, row 5 cannot fire, and the spike concluded
  `Blocked(NO_RULE)` was the only residual. Supplying `branch_exists: True` (the
  normal case in this repo) routes to `/do-build` at **row 5**. See spike-8. The
  conclusion drawn from this spike — "the deliverable is precisely to stop the false
  rebuild" — survives; the belief that stopping G8 *achieves* that does not.
- **Finding (as measured, context hand-built)**: With
  the G8 flags unset and `pr_number` absent:
  - all stages completed including `MERGE` and `PATCH`, no verdict →
    `Blocked(reason='no matching dispatch rule', guard_id='NO_RULE')`
  - same, plus an APPROVED verdict → still `Blocked(NO_RULE)`
  Every terminal route is gated on `pr_number`: `_rule_ready_to_merge` returns
  `False` without it (`agent/sdlc_router.py:1470-1471`) and
  `guard_g6_terminal_merge_ready` returns `None` without it (`:739-741`). A terminal
  pipeline that lost its `pr_number` has **no owner in the dispatch table at all**.
- **Confidence**: high
- **Impact on plan (superseded)**: this originally justified asserting only the
  absence of a **G8** `/do-build`. That assertion is now known to be satisfiable
  while the bug persists, and is replaced throughout by `result.get("skill") !=
  "/do-build"` at **any** row, with an explicit `branch_exists: True` variant.
  `Blocked(NO_RULE)` is also **not** the benign no-op this spike assumed — the router
  documents it as a genuine hole in the table and the SDLC skill surfaces it and
  waits, i.e. it is a human-escalation wedge. Under the primary fix the residual
  disappears entirely and #2817 becomes moot (spike-10).

### spike-2: Would reordering `GUARDS` (G6 before G8) fix the reported case?
- **Assumption**: "Letting the terminal verdict preempt the artifact check is the invasive-but-viable alternative."
- **Method**: code-read + the spike-1 calls
- **Finding**: **It cannot fix this bug at all.** `guard_g6_terminal_merge_ready`
  short-circuits to `None` when `pr_number` is falsy — which is the reported shape.
  Reordering would change behavior only for pipelines that *have* a `pr_number`,
  i.e. exactly the pipelines that do not reproduce #2757. It would also weaken the
  #1267 positioning contract (G8 sits after G4 so the oscillation cap bounds a
  persistently-false claim) for every lane, in exchange for nothing.
- **Confidence**: high
- **Impact on plan**: guard reordering is rejected on evidence, not on taste. It is
  a No-Go with a stated reason, and `GUARDS` is untouched — which is also what keeps
  the fix inside its fence.

### spike-3: Does a MERGE-marker short-circuit alone fix the reported case?
- **Assumption**: "`MERGE == completed` → skip verification" is the whole fix.
- **Method**: code-read against the live ledger state of the three cited issues
- **Finding**: **It fixes the reported case and misses the dangerous one.** The
  three cited issues (#2638, #2566, #2640) read *entirely* empty today — `stages:
  {}`, `pr_number: null`, `slug: null`. The ledger that drops `pr_number` is the
  same ledger that drops `MERGE`; there is no mechanism making one field more
  durable than the other. In the partial-loss shape — `BUILD == completed` survives,
  `MERGE` and `pr_number` are gone — a MERGE-marker check sees nothing terminal, the
  `not pr_number` term still fires, and G8 still re-dispatches `/do-build` on
  shipped work. Measured routing for that shape with the G8 flags unset:
  `{ISSUE, PLAN, CRITIQUE, BUILD} completed`, no `pr_number` → `Blocked(NO_RULE)`;
  `{BUILD}` alone → `Dispatch(/do-plan, row 1)`. Neither rebuilds.
- **Confidence**: high
- **Impact on plan (superseded by spike-9)**: this correctly established that a
  MERGE-marker short-circuit is inert for the reported shape — which is now one of
  the four reasons the short-circuit is **cut entirely** rather than demoted to an
  "outer belt". What this spike did not ask is *why* `pr_number` was absent; spike-9
  answers that, and the answer makes the identifiability guard defense-in-depth
  rather than the load-bearing fix. One claim here is also **retracted**: "a wholly
  empty ledger is already safe" is false at `decide()`, which recovers `BUILD` from
  durable signals before the verifier runs. The over-reading of a now-empty ledger as
  testimony about its state a day earlier is likewise noted and not relied on.

### spike-4: Is the MERGE short-circuit redundant once the `not pr_number` term is fixed?
- **Assumption**: "Fixing BUILD is sufficient; a terminal short-circuit adds nothing."
- **Method**: code-read + live probes (`_check_branch_pushed`, `gh repo view --json deleteBranchOnMerge`)
- **Finding**: **Not redundant — it covers a hole in the sibling check.** With
  `pr_number` absent, `pr_state` stays `None`, so the PATCH check's merged-skip
  (`pr_state != "MERGED"`, `:281`) **cannot engage**, and the entire PATCH verdict
  rests on `git ls-remote` still finding the lane branch. This repo reports
  `deleteBranchOnMerge: false`, and `session/sdlc-2755` is still on origin after
  merging — so the hole is dormant *here*. But `SDLC_TARGET_REPO` can point at a
  repo with branch deletion on, and #1267's docstring designed that skip *for*
  exactly that policy. Without the short-circuit, the BUILD fix would convert #2757
  from a `/do-build` re-dispatch into a `/do-patch` re-dispatch on the same shipped
  work, in any such repo, whenever a lane slug is recorded.
- **Confidence**: high for the mechanism; high for the local `deleteBranchOnMerge`
  reading; the "other repo" case is structural, not observed.
- **Impact on plan (superseded)**: the hole this spike found is real, but the
  short-circuit was the wrong instrument for it — it closes the hole only when a
  `MERGE` marker survives, which is precisely the case the reported shape does not
  have (spike-3). The hole is now closed **directly**: the PATCH branch probe gets
  the same `pr_number` identifiability guard as BUILD. And after spike-9's primary
  fix, `pr_number` is normally present post-merge, so the PATCH merged-skip engages
  on its own and the hole is largely closed at the source as well.

### spike-5: Why is `test_terminal_merged_pipeline_routes_to_merge_not_build` red, and does this plan's fix turn it green?
- **Assumption**: "The test defends #2757's behavior, so fixing G8 makes it pass."
- **Method**: prototype (ran the test; then replayed its exact ledger shape through `decide_next_dispatch`)
- **Finding**: **Both halves false, and the test is repairable inside this fence.**
  The run fails verbatim with:
  ```
  AssertionError: {'dispatched': True, 'reason': 'REVIEW completed without a recorded
   verdict — re-run review', 'row_id': '8e', 'skill': '/do-pr-review'}
  ```
  The test's fixture sets `pr_number = 918274`, so **G8 never fires on it** — it does
  not exercise #2757's shape at all. Its red is entirely row 8e. Replaying its
  stage_states through the router:
  - as written (thru DOCS, `pr_number`, no verdict) → row 8e
  - plus `MERGE: completed` → **still** row 8e (a terminal marker does not help)
  - plus `latest_review_verdict: "APPROVED"` → **row 10**, the assertion it makes
  Since #2062 WS3a, `_rule_ready_to_merge` requires a *recorded APPROVED verdict*
  (`:1476-1477`); row 10 has been unreachable for this fixture since that shipped,
  independent of anything G8 does. Two further measurements pin the repair: with
  `context["pr_head_sha"]` empty (the fail-closed sentinel `_build_context` sets when
  the head lookup fails) the same state routes to **row 8f**, not row 10 — so the
  test's `subprocess.run` fake must also answer `headRefOid`, and the recorded
  verdict must carry a matching `head_sha`.
- **Confidence**: high (measured, not reasoned)
- **Impact on plan**: **no router change and no fence extension.** The red is a
  stale test fixture that #2062 invalidated, not a second hole. The test is repaired
  in `tests/` — the only files this plan touches besides the verifier — and nothing
  is left red or `xfail`. See Key Elements for why making row 8e defer on a terminal
  pipeline is rejected outright rather than deferred.

### spike-6: Is the `not pr_number` term covered by any existing test?
- **Assumption**: "Changing this predicate will show up as a test failure if it is wrong."
- **Method**: code-read of `tests/unit/test_sdlc_next_skill.py::TestStageArtifactVerification` and `tests/unit/test_lane_identity.py`
- **Finding**: **No test covers it.** Every BUILD-claim test in the suite supplies
  `meta = {"pr_number": 555}` and varies only the live state (`OPEN`, `CLOSED`,
  `MERGED`). The two tests that pass `{"pr_number": None}`
  (`test_lane_identity.py:342,373`) claim `PLAN`/`PATCH` and never `BUILD`, so they
  never reach the term. The only currently-reachable branch of the BUILD check that
  fires in production is the one branch with zero coverage.
- **Confidence**: high
- **Impact on plan**: the fix cannot regress an existing assertion, and the
  red-test-first step is mandatory rather than ceremonial — there is no existing red
  to inherit.

### spike-7: Negative control — does a complete ledger still route correctly?
- **Assumption**: "The bug needs a degraded ledger; a healthy terminal pipeline is fine."
- **Method**: prototype (live `sdlc-tool next-skill` on #2755, read-only)
- **Finding**: **True.** #2755 (merged today, `pr_number = 2815`, APPROVED verdict
  with `head_sha = a0cd7dc68`, `MERGE = completed`) returns `{"skill": "/do-merge",
  "row_id": "10", "dispatched": true}`. The bug is conditional on the ledger having
  lost `pr_number`, not on merge itself.
- **Confidence**: high
- **Impact on plan**: #2755 is named in Verification as the live negative control,
  and the fix must leave its routing unchanged.

### spike-8: Does silencing G8 actually stop the rebuild?
- **Assumption**: "G8 is the only row that re-dispatches `/do-build` on this shape." (spike-1's implicit premise.)
- **Method**: prototype — `decide_next_dispatch` with a full terminal `stage_states`, varying `pr_number` and `branch_exists`
- **Finding**: **No. The rebuild relocates to row 5.** `_rule_branch_exists_no_pr`
  (`agent/sdlc_router.py:922-927`) returns `True` on `context["branch_exists"] is
  True` alone when `pr_number` is falsy — it does not consult `BUILD` at all in that
  branch — and row 5 precedes every PR-gated row. Measured:
  `pr_number` absent + `branch_exists: True` → **`/do-build`, row 5**;
  `pr_number` absent + `branch_exists: False` → `Blocked(NO_RULE)`.
  `deleteBranchOnMerge` is `false` on this repo and merged lane branches persist, so
  the row-5 column is the normal post-merge state here.
- **Confidence**: high (measured through the real router)
- **Impact on plan**: this is the finding that forced the restructure. A
  verifier-only fix cannot deliver the plan's own stated outcome, and the original
  Success Criterion 1 would have certified it anyway. Every acceptance assertion is
  now row-agnostic.

### spike-9: Is `pr_number` actually unrecoverable after merge, or just looked up wrong?
- **Assumption**: "The ledger lost `pr_number`; recovering it means inventing a second identity resolver on a hot path." (The original Rabbit Hole.)
- **Method**: prototype — live `_lookup_pr` calls against the three reported issues, both `state` values
- **Finding**: **It is looked up wrong, and the fix is one keyword argument.**
  `tools/sdlc_stage_query.py::_compute_meta` calls `_lookup_pr(...)` at `:548`
  without `state`; the signature defaults to `state="open"` (`:338-342`), which
  excludes merged PRs by construction. Measured live: #2638 → `None` under `open`,
  **2686** under `merged`; #2566 → `None` / **2665**; #2640 → `None` / **2671**. Those
  are the exact PRs this plan's Problem section names.
  `tools/sdlc_stage_marker.py:246-250` already carries the identical fix under
  **#2539**, and `agent/pipeline_state.py:1564` independently uses a
  `for state in ("open", "all")` two-pass fallback with the rationale spelled out in
  its docstring.
- **Confidence**: high (measured live against `gh`)
- **Impact on plan**: the Rabbit Hole "Rebuilding `pr_number` from the world" was
  attacking a strawman — this is the *existing* resolver with the filter corrected,
  not a new one. Restoring `pr_number` becomes the plan's load-bearing fix.

### spike-10: Does restoring `pr_number` produce the right outcome, not merely a different one?
- **Assumption**: "Even with `pr_number` restored, a terminal pipeline still needs a new router row to land somewhere sane." (The premise behind #2817.)
- **Method**: prototype — same terminal `stage_states`, `pr_number = 2686`, recorded APPROVED verdict with a `head_sha` matching `context["pr_head_sha"]`
- **Finding**: **It reaches the terminal row under both branch states.**
  `pr_number: 2686` + `branch_exists: True` → **`/do-merge`, row 10**;
  `branch_exists: False` → **`/do-merge`, row 10**. One dependency is worth naming
  because it caused a false start: with the verdict's `head_sha` absent or mismatched
  against `pr_head_sha`, the same state routes to **row 8f** (`/do-pr-review`), not
  row 10 — the #2062 WS3d stale-verdict row. The head-SHA attribution is load-bearing
  for reaching row 10, which is the same dependency spike-5 identified in the
  integration fixture.
- **Confidence**: high (measured)
- **Impact on plan**: the primary fix produces a *correct* terminal outcome rather
  than a merely silent one, and it deletes the `Blocked(NO_RULE)` residual. **#2817
  is moot** and moves from a No-Go to a close-with-evidence action.

## Data Flow

1. **Entry point**: `sdlc-tool next-skill --issue-number N` → `tools/sdlc_next_skill.py::main` → `decide()`.
2. **Lock peek**: the issue-ownership peek (`:513-549`) short-circuits on foreign
   ownership. **Untouched by this plan** (#2766's lane owns that block).
3. **State read**: `_resolve_enriched` → `tools/sdlc_stage_query.query_enriched`
   returns `{"stages": {...}, "_meta": {...}}`. `_meta.pr_number` and
   `stages["MERGE"]` both originate here, from the same `PipelineLedger` record —
   which is why they are lost together (Risk 1).
4. **Durability recovery**: when `stage_states == {}` *exactly*,
   `_recover_stage_states_from_durable_signals` (#2395) attempts reconstruction. A
   *partially* populated ledger is deliberately left alone — so the partial-loss
   shape spike-3 identifies flows through untouched.
5. **Context assembly**: `_build_context(...)` → `_verify_stage_artifacts(...)` →
   **`_verify_stage_artifacts_live(stage_states, meta, issue_number)`** — the sole
   site this plan modifies. It returns either `{}` or
   `{"stage_artifacts_verified": False, "unverified_stage": <STAGE>}`.
6. **Routing**: `decide_next_dispatch(stage_states, meta, context)` walks
   `GUARDS = [G1, G2, G3, G4, G8, G7, G5, G6]`, then the 18-row dispatch table.
   `guard_g8_artifact_verification` reads only the two flags from step 5.
7. **Output**: JSON on stdout — `{"skill", "reason", "row_id", "dispatched"}` or
   `{"blocked", "reason", "guard_id"}`.

The change is confined to step 5, and it only ever moves an outcome from
"flags set" to "flags unset". It cannot introduce a new dispatch, only remove one.

## Architectural Impact

- **New dependencies**: none. No new import, no new model access. One conditional
  second call to a function already imported and already called at the same site.
- **Interface changes**: none externally. `_verify_stage_artifacts_live` and
  `_compute_meta` both keep their signatures and return contracts, and `_lookup_pr`'s
  default is untouched. **No new helper is introduced** — the earlier draft's
  `_pipeline_is_terminal_from_states` is cut, avoiding a third spelling of a
  predicate that already has two.
- **Coupling**: unchanged. Nothing gains an import of `agent/pipeline_state.py`.
- **Cost**: one additional `gh` call whenever the `open` pass returns `None`.
  **Stated honestly, that is the majority of invocations, not a degraded minority.**
  `_compute_meta`'s own comment (`tools/sdlc_stage_query.py:529-530`) notes it "runs
  for any issue number the router, the dashboard, or an operator names — most of which
  are not lanes", and a non-lane issue has no open PR, so it already takes the `None`
  path today and will now pay the retry. Measured on this machine: **~0.89s** added on
  the no-match path (bounded by `_lookup_pr`'s existing 5s subprocess timeout), against
  a function that already makes several `gh` round-trips including
  `_fetch_pr_merge_state`. Accepted as the price of correctness. Deliberately **not**
  mitigated with a cache — that would add persistent state and break this plan's
  "no new dependencies, no new architectural surface" property for a sub-second cost.
  Lanes with an open PR remain entirely unaffected.
- **Data ownership**: unchanged. The verifier remains read-only.
- **Reversibility**: very high. Three small edits across two functions; reverting
  restores the present behavior exactly.
- **Behavioral blast radius**: two directions, both narrow. `_compute_meta` resolves
  a `pr_number` in strictly more cases and never a different one in a case it already
  resolves — which *arms* G8 and row 10 where they were previously inert. The
  verifier fires strictly less often, and every state in which it fired *and* had a
  `pr_number` is unchanged (spike-6 confirms the existing tests pin those). No state
  that previously advanced now blocks.

## Appetite

**Size:** Medium

**Team:** Solo dev + validator (two agents — see Team Orchestration)

**Interactions:**
- PM check-ins: 1 — **already spent**, on the fence extension below
- Review rounds: 1

**FENCE EXTENSION (operator-visible).** The earlier draft fenced this work to
`tools/sdlc_next_skill.py` plus tests. The war room established that the fix which
makes the outcome *correct* rather than merely silent lives one layer up, so the
fence now also includes **`tools/sdlc_stage_query.py::_compute_meta`** — a two-pass
`_lookup_pr` call, following #2539's precedent at a sibling call site. This is a
deliberate, evidence-backed widening, not scope creep: without it the reported
rebuild simply relocates from G8 to router row 5 (spike-8). `agent/sdlc_router.py`,
`agent/pipeline_state.py`, and `tools/sdlc_session_ensure.py` remain outside.

The code is roughly fifteen lines across two modules. The Medium sizing is bought by
the test-fixture reconstruction (spike-5's two-part repair) and by three documentation
surfaces that currently describe the BUILD check in terms this change falsifies.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable for the integration test's real ledger | `.venv/bin/python -c "from agent.pipeline_ledger import PipelineLedger; PipelineLedger.query.filter(ledger_key='nonexistent')"` | `TestStageArtifactVerificationGate` uses a real `PipelineLedger` |
| `gh` authenticated for the live negative control | `gh auth status` | spike-7's #2755 re-check in Verification |
| The live negative control is still terminal | `.venv/bin/python -m tools.sdlc_next_skill --issue-number 2755` | Must return row 10 before and after the change |

## Solution

### Key Elements

- **PRIMARY — `pr_number` is resolved for merged PRs, not worked around.**
  `tools/sdlc_stage_query.py::_compute_meta` (`:548`) calls `_lookup_pr` without a
  `state`, inheriting the `"open"` default that makes every merged PR invisible
  (spike-9). The fix is a **two-pass fallback**: try `"open"` first, and only if that
  returns `None`, retry with `"all"`. This shape is chosen over an unconditional
  `state="all"` deliberately, and the second pass is scoped to **`state="merged"`**
  rather than `"all"` — see the round-2 correction below. It is **strictly additive**:
  it resolves a `pr_number` only where the current code resolves `None`, so it cannot
  change any lookup that succeeds today, and because neither pass can return a
  closed-unmerged PR it cannot surface one at all.
  It is also not a new idiom: `agent/pipeline_state.py:1564` already reads
  `for state in ("open", "all")` for the same reason, and
  `tools/sdlc_stage_marker.py:246-250` already corrected the identical defect at a
  sibling call site under #2539. This is the change that makes the outcome *right*
  rather than merely quiet: it stops the row-5 rebuild, lets G8 pass on its own
  merits, reaches `/do-merge` row 10, and deletes the `Blocked(NO_RULE)` residual
  (spike-10). **It requires a fence extension to `tools/sdlc_stage_query.py`.**
- **SECONDARY — the BUILD check stops treating an absent `pr_number` as a falsified claim.**
  The predicate becomes "claimed *and* identifiable": the check runs only when
  `build_claimed and pr_number`, exactly mirroring `patch_claimed`'s
  `and bool(lane_branch)` two lines above it. When the identifier is absent, log at
  **debug** (matching the PATCH skip's log level and wording at `:258-263`) and
  no-op. It is **defense in depth**, not the load-bearing fix: after the primary
  change it is reached only by a genuinely PR-less lane or a still-degraded ledger.
  Risk 2 states the coverage it gives up.
- **The same guard is applied to PATCH's branch probe.** With `pr_number` absent the
  PATCH merged-skip (`pr_state != "MERGED"`, `:281`) cannot engage, so the PATCH
  verdict rests entirely on `git ls-remote` finding the lane branch — and "branch
  gone" is then indistinguishable from "deleted on merge." Skip the branch probe when
  `pr_number` is absent, for the same reason and with the same debug log. This is
  what actually closes the hole spike-4 identified; the terminal short-circuit only
  appeared to.
- **The MERGE short-circuit is CUT.** An earlier draft returned `{}` from
  `_verify_stage_artifacts_live` whenever `stage_states["MERGE"] == "completed"`.
  It is removed from the plan for four independent reasons, any one of which is
  sufficient:
  1. **It is inert for the actual reproduction.** The three reported issues carry no
     `MERGE` marker at all — `decide()` recovers `BUILD = completed` from durable
     signals (`tools/sdlc_next_skill.py:576-579` → `agent/pipeline_state.py:1564`,
     which reads `("open", "all")` and therefore *does* see the merged PR) while
     `_compute_meta` leaves `pr_number` as `None` and MERGE is never written. A
     MERGE-marker check is blind to the exact shape it was introduced to fix.
  2. **It does not close the hazard it was justified by.** spike-4's PATCH hole is
     now closed directly, above.
  3. **It buys coverage by trusting the self-attested channel G8 exists to police**,
     and nothing ever resets a stage — once MERGE is completed it is permanent, so a
     reopened issue would run its entire second pipeline with all three artifact
     checks disarmed.
  4. **It makes the repaired integration test vacuous** — see the test note below.
- **No third spelling of the terminal predicate.** The cut above removes the need for
  one, which is the cleanest resolution: two spellings already exist
  (`tools/_sdlc_run_identity.py:129`, `agent/pipeline_state.py:1155`) and Development
  Principle 1 forbids adding a third. The earlier draft's stated reason for not
  calling the existing helper was also **factually wrong** and is retracted here so
  it is not repeated: `_pipeline_is_terminal`'s import of `agent/pipeline_state.py`
  is **function-local** (`tools/_sdlc_run_identity.py:122`), so calling it never
  couples this fix to that module's module-level surface. Should a future change
  genuinely need a terminal predicate over a `states` dict in hand, the correct move
  is to add one pure `pipeline_is_terminal(states: dict) -> bool` to
  `tools/_sdlc_run_identity.py` and have the existing issue-number helper delegate to
  it — not to introduce a near-homonym.
- **`GUARDS` is not reordered, and `agent/sdlc_router.py` is not touched.** Guard
  reordering was the obvious alternative and spike-2 rules it out on evidence: G6
  cannot fire without a `pr_number`, which is the reported shape. The revised plan
  makes this doubly moot: restoring `pr_number` is what lets G6 and row 10 fire at
  all, so the ordering question never arises. `_rule_branch_exists_no_pr` is
  likewise **not** modified — row 5's behavior is correct given a falsy `pr_number`
  ("build must create the PR"); the defect was that `pr_number` was falsy when a
  merged PR existed. Fixing the input is the right layer; rewriting the row would
  paper over it.
- **Row 8e is left exactly as it is.** spike-5 establishes that the red integration
  test is a stale fixture, not a second hole: it sets `pr_number`, so it never
  reaches G8; its `row_id == "10"` assertion has been unreachable since #2062 WS3a
  made row 10 require a recorded APPROVED verdict; and supplying that verdict routes
  it to row 10 as written. Making 8e defer on `MERGE == completed` is rejected on
  its merits, not merely fenced out: 8e exists because a `REVIEW: completed` marker
  with no recorded verdict is **unearned** (the #1897 no-owner state). A ledger that
  additionally claims `MERGE: completed` without a verdict is *less* trustworthy,
  not more — it would be asking the router to accept a merge marker from the same
  self-attestation channel whose unverified claim it is currently refusing. So the
  test is repaired rather than the router, nothing ships red, and nothing is
  `xfail`-ed.
- **The repaired test keeps its fixture live.** `test_terminal_merged_pipeline_routes_to_merge_not_build`
  is repaired with the recorded APPROVED verdict and the `headRefOid` answer **only**
  — `MERGE: "completed"` is deliberately **not** added. Adding it (as an earlier
  draft specified) would, in combination with the now-cut short-circuit, mean neither
  `gh pr view` nor `git ls-remote` is ever reached: the fixture becomes dead code and
  the repo's only end-to-end proof that a MERGED PR is an acceptable BUILD artifact
  stops proving anything. If the name is the concern, rename the test; do not hollow
  it out to match the name.
- **The regression test is pinned positively, not only negatively.** `decide()`
  catches every exception and returns `{"error": ..., "dispatched": False}`
  (`tools/sdlc_next_skill.py:605-610`), which satisfies any assertion of the form
  "the result is not `/do-build`". A negative-only regression test therefore goes
  **green when `decide()` crashes** — which would defeat the mandatory red-test-first
  step. Every new assertion must additionally require **no `error` key** and pin the
  measured expected dispatch.
- **Acceptance is row-agnostic.** Assertions are written as `result.get("skill") !=
  "/do-build"` at any row, never "not a *G8* `/do-build`", and each carries a
  `branch_exists: True` variant — because spike-8 measured that the rebuild relocates
  to row 5 exactly when `branch_exists` is `True`, which is this repo's normal
  post-merge state.

### Flow

**`sdlc-tool next-skill --issue-number N`** → lock peek → ledger read
(`stages` + `_meta`) → context assembly → **`_verify_stage_artifacts_live`**:

**Stage 1 — `_compute_meta` resolves the artifact identifier (the primary fix):**

```
_lookup_pr(issue, state="open")  ──found──► pr_number          (unchanged path)
        │ None
        ▼
_lookup_pr(issue, state="merged") ─found──► pr_number   ◄── THE FIX (merged-scoped)
        │ None
        ▼
pr_number = None                                        (genuinely PR-less lane)
```

**Stage 2 — `_verify_stage_artifacts_live` (defense in depth):**

```
PLAN claimed + plan path resolves?  ──► git show main:<path>  ──fail──► unverified PLAN
        │
        ▼
BUILD claimed?
   ├─ no pr_number  ──► debug log, skip                (unverifiable, NOT falsified)
   └─ pr_number     ──► gh pr view --json state
                          ├─ OPEN | MERGED ──► verified
                          └─ otherwise     ──► unverified BUILD
        │
        ▼
PATCH claimed + lane branch resolves?
   ├─ no pr_number  ──► debug log, skip                ("branch gone" is unreadable)
   └─ pr_number     ──► merged? skip : git ls-remote  ──fail──► unverified PATCH
        │
        ▼
return {}
```

In the reported state stage 1 now supplies the `pr_number`, so stage 2 **verifies
clean on the merits** (`gh pr view` returns `MERGED`, an accepted state since #1267)
rather than being skipped. The router then walks
`[G1, G2, G3, G4, G8, G7, G5, G6]`: G8 no-ops, row 5 no longer fires because
`pr_number` is truthy, and the pipeline reaches `/do-merge` row 10 (spike-10).

### Technical Approach

Edits span **two** modules — `tools/sdlc_stage_query.py` (the primary fix) and
`tools/sdlc_next_skill.py` (defense in depth) — plus tests. Line references are at
`1c48b97f2` unless noted.

- **`tools/sdlc_stage_query.py::_compute_meta` (`:548`) — the primary fix.** Replace
  the single `_lookup_pr(issue_number, slug=slug, repo=resolved_repo)` with a
  two-pass fallback: the existing call, then — **only if it returns `None`** — a
  retry with **`state="merged"`**. Do not change `_lookup_pr`'s own default; other
  callers depend on it and `sdlc_stage_marker.py` passes `state` explicitly. Carry a comment
  naming #2539 as precedent and `agent/pipeline_state.py:1564` as the in-repo idiom,
  and stating why the ordering matters (an open PR must always win over a historical
  one, so the second pass runs only on `None`).
- **No new terminal predicate, and no short-circuit in the verifier.** The earlier
  draft's `_pipeline_is_terminal_from_states` helper is cut entirely — see Key
  Elements for the four reasons. Nothing is added above the
  `find_plan_path` / `resolve_lane_slug` block.
- **Gate the BUILD check on an identifiable artifact.** Fold `pr_number` into the
  claim the way PATCH already does — the cleanest form keeps the `pr_state` fetch
  guard (`:269`) unchanged and rewrites `:272-278` so the mismatch branch is reached
  only when a `pr_number` exists. When `build_claimed` is true and `pr_number` is
  absent, emit a **debug** log ("BUILD claims completed but no PR number is
  recorded; skipping the live PR check rather than reporting a claim it cannot
  verify") and fall through. Do **not** leave the old term behind as an `elif` — the
  behavioral test, not the grep, is what pins this, but a demoted copy would defeat
  both.
- **Apply the same guard to the PATCH branch probe.** When `pr_number` is absent,
  skip the `git ls-remote` check with a debug log noting that a missing branch is
  indistinguishable from deletion-on-merge without a PR state to consult. This
  replaces the cut MERGE short-circuit as the fix for spike-4's hole.
- **Update the function docstring (`:189-210`).** Its `#1267 merged-pipeline
  misfire` paragraph is the natural home: extend it with the #2757 case, stating the
  three-state distinction (verified / falsified / unverifiable) and that the real fix
  for the reported case is upstream, in how `_compute_meta` resolves the PR number —
  this guard is defense in depth. **Paraphrase the removed condition; do not quote
  it** — the advisory grep row in Verification covers the whole file, comments
  included, and quoting the deleted term in prose explaining its deletion is a
  self-inflicted red.
- **Do not touch** `_fetch_pr_state`, `_check_branch_pushed`,
  `_check_plan_committed_on_main`, `_build_context`, the lock-peek block, or
  `_verify_stage_artifacts`'s narrow fail-open catch (`:316-330`). The catch's
  scoping is #1267 Concern 4 and is not in question here.
- **Test-side (`tests/integration/test_sdlc_session_ensure_integration.py`)**:
  extend `_fake_gh_pr_view_merged` to answer `headRefOid` for the `gh pr view` call
  and to answer `git ls-remote origin refs/pull/N/head` (the git-first resolver
  `tools/pr_head_resolver.resolve_pr_head_sha` tries first), so the head lookup
  yields a real SHA instead of the fail-closed empty sentinel. Add a `_verdicts`
  entry to the repaired test's ledger: `REVIEW` = APPROVED with `head_sha` equal to
  that SHA. Add `MERGE: "completed"` to its stage_states so the test's name is
  honest. Then add the new no-`pr_number` sibling.
- **Test-side (`tests/unit/test_sdlc_next_skill.py`)**: add to
  `TestStageArtifactVerification` — a claimed BUILD with no `pr_number` runs **zero**
  subprocesses and leaves the flags unset; a terminal `stage_states` runs zero
  subprocesses even with a falsifiable BUILD claim; and the existing CLOSED-PR
  behavior is unchanged. Assert on a `MagicMock` for `subprocess.run` with
  `assert_not_called()`, following `test_no_claimed_artifact_is_a_noop` (`:537-560`).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new exception handler is introduced. `_verify_stage_artifacts`'s existing
  narrowed catch (`subprocess.TimeoutExpired` / `SubprocessError` / `OSError` →
  fail open; anything else → re-raise) is unchanged, and its four existing tests
  (`test_fails_open_on_infra_error`, `test_fails_open_on_os_error`,
  `test_non_infra_exception_does_not_silently_advance`,
  `test_missing_stage_states_or_meta_skips_verification`) must still pass untouched.
- [ ] The identifiability guards run **before** their respective subprocesses, so a
  `pr_number`-less claim cannot raise an infra error at all. Assert this positively:
  a BUILD-claimed, `pr_number`-less `meta` with a `subprocess.run` mock that raises on
  any call must still return `{}`.
- [ ] The `_compute_meta` second pass inherits `_lookup_pr`'s existing internal error
  handling; it adds no new handler. If the `open` pass raises, behavior is unchanged
  (the exception propagates as today) — the fallback is reached only on a clean
  `None`.

### Empty/Invalid Input Handling
- [ ] `meta` missing `pr_number` entirely, `pr_number = None`, and `pr_number = 0`
  all take the unverifiable path. Parametrize into one test — three tests for one
  falsiness check is over-testing.
- [ ] `stage_states = {}` (the wholly-empty ledger) — **this is not "already safe",
  and the earlier draft's claim that it was is retracted.** At the
  `_verify_stage_artifacts_live` level `build_claimed` is indeed `False`, but the
  function is not the entry point: `decide()` recovers `stage_states` from durable
  signals first (`:576-579`), which sets `BUILD = completed` for a merged PR. The
  correct coverage is therefore the **integration** test on the recovery path, not a
  unit assertion about the empty dict.
- [ ] The `MERGE`-value matrix from the earlier draft is dropped — with the
  short-circuit cut, `stage_states["MERGE"]` is no longer read by this function.

### Error State Rendering
- [ ] The unverifiable-BUILD skip logs at **debug**, not warning: it is a normal,
  expected state, and a warning per tick on every ledger-degraded issue is noise
  that trains operators to ignore the channel. The existing falsified-BUILD warning
  (`:274-277`) stays at warning and keeps its wording. Both are asserted by
  `caplog` level in tests — the level distinction is the user-visible contract.
- [ ] Both new debug logs (unverifiable BUILD, unverifiable PATCH) name the issue
  number and the reason, so an operator debugging "why did next-skill skip that
  check" finds it in the log.
- [ ] Log-level assertions require `caplog.set_level(logging.DEBUG)`; without it they
  pass vacuously regardless of the level actually used. Note also that re-asserting
  the **existing** warning's level pins a log level as an interface — do it only for
  the new debug logs, where the level is the contract being introduced.

## Test Impact

- [ ] `tests/integration/test_sdlc_session_ensure_integration.py::TestStageArtifactVerificationGate::test_terminal_merged_pipeline_routes_to_merge_not_build`
  — **UPDATE (currently RED on main)**: add a recorded APPROVED `REVIEW` verdict
  with a `head_sha`, and extend the `gh`/`git` fake to answer the head-SHA lookup.
  **Do NOT add `MERGE: "completed"`** — an earlier draft specified it, which would
  leave neither `gh pr view` nor `git ls-remote` reached and turn the fixture into
  dead code, destroying the repo's only end-to-end proof that a MERGED PR is an
  acceptable BUILD artifact. Keeps its `row_id == "10"` assertion, which spike-5
  measured as reachable once the verdict exists. Its docstring must be rewritten:
  the current text explains a #1267 failure mode that is not why it is red.
- [ ] `tests/integration/test_sdlc_session_ensure_integration.py::TestStageArtifactVerificationGate::_fake_gh_pr_view_merged`
  — **UPDATE**: answer `headRefOid` on `gh pr view` and a `refs/pull/N/head` line on
  `git ls-remote`. Its existing "answer branch-gone to prove the merged skip is load
  bearing" behavior for `git ls-remote --heads` is preserved — do not collapse the
  two `git ls-remote` shapes into one response.
- [ ] `tests/integration/test_sdlc_session_ensure_integration.py::TestStageArtifactVerificationGate::test_g8_redispatches_build_on_synthesized_false_pr_claim`
  — **UNCHANGED, and must stay green**: `pr_number` present + live state CLOSED must
  still produce `row_id == "G8"` and `/do-build`. This is the control proving the
  fix narrowed the gate rather than disarming it.
- [ ] `tests/integration/test_sdlc_session_ensure_integration.py` — **ADD**
  `test_no_pr_number_recorded_does_not_redispatch_build`: `BUILD` completed, **no**
  `MERGE` marker, **no** `pr_number` resolvable in any state, real ledger. **The
  assertion differs by `branch_exists`** (round-2 BLOCKER 2 — a blanket "never
  `/do-build`" is unsatisfiable here):
  `False` → `result.get("skill") != "/do-build"`;
  `True` → `result["row_id"] == "5"`, i.e. `/do-build` **from row 5, never from G8**,
  which is the correct owner of "a branch exists with no PR" (Risk 2).
  Both cases additionally assert **no `error` key**. The #2757 regression test proper;
  it does not exist today in any form. Three corrections to the earlier draft are
  load-bearing here: a G8-specific negative would pass while the rebuild ran at row 5
  (spike-8); the `MERGE` marker would have let the now-cut short-circuit mask a
  reverted guard; and a negative-only assertion is satisfied by `decide()`'s
  catch-all.
- [ ] `tests/integration/test_sdlc_session_ensure_integration.py` — **ADD** the
  recovery-path test: an **empty** ledger + a merged PR + an existing branch → no
  `/do-build` at any row. This is the true reproduction — `decide()` recovers
  `BUILD = completed` from durable signals (`:576-579` →
  `agent/pipeline_state.py:1564`, which reads `("open", "all")` and so *does* see the
  merged PR) while `_compute_meta` leaves `pr_number` as `None`. It replaces the
  earlier draft's unit assertion that a wholly-empty `stage_states` is "already safe",
  which is **false at `decide()`**.
- [ ] `tests/unit/test_sdlc_stage_query.py` — **ADD** two `_compute_meta` cases: the
  `open` pass returning nothing and the `merged` pass returning a PR resolves
  `pr_number`; and the `open` pass returning a PR does **not** run the `merged` pass
  (pinning the strictly-additive ordering).
- [ ] `tests/integration/test_sdlc_session_ensure_integration.py:19-26` — **UPDATE**:
  the module docstring describes the class as driving "a synthesized false
  BUILD-completion claim"; it gains a second, complementary shape.
- [ ] `tests/unit/test_sdlc_next_skill.py::TestStageArtifactVerification` — **ADD**
  three cases: BUILD claimed with no `pr_number` → zero subprocess calls, flags unset
  (parametrized over the falsy forms — missing key, `None`, `0` — in **one** test;
  three tests for one falsiness check is over-testing); PATCH claimed with no
  `pr_number` → the branch probe is not run; and the debug-vs-warning log-level
  distinction, asserted with `caplog.set_level(logging.DEBUG)` — without it the
  assertion passes vacuously. The terminal-`stage_states` case from the earlier draft
  is dropped along with the short-circuit.
- [ ] `tests/unit/test_sdlc_next_skill.py::TestStageArtifactVerification::test_false_build_claim_sets_unverified_stage`
  and the two `test_true_build_claim_*` cases — **UNCHANGED**: all supply
  `pr_number = 555` (spike-6), so none of them touch the modified term. Their
  continued passing is itself an assertion.
- [ ] `tests/unit/test_lane_identity.py:342,373` — **UNCHANGED**: both pass
  `{"pr_number": None}` but claim only `PLAN`/`PATCH`, and neither sets `MERGE`, so
  neither is affected by either change. Re-run them to confirm.

**No `xfail` conversions.** A repo-wide grep for `pytest.mark.xfail` /
`pytest.xfail(` across `tests/` returns nothing at all, so there is no
expected-failure marker to convert and none is introduced by this plan.

**Red-test-first (mandatory).** Before any edit to `tools/`, write
`test_no_pr_number_recorded_does_not_redispatch_build` and watch it fail against the
current module, recording the failure text. spike-6 establishes that no existing
test covers the term being changed — so a suite that passes after the fix proves
nothing on its own.

## Rabbit Holes

- **~~Rebuilding `pr_number` from the world.~~ RETRACTED — this was the fix.** The
  earlier draft listed this as a rabbit hole on the grounds that it would mean "a
  second identity resolver on a hot path, with the cross-repo and multi-PR ambiguity
  hazards `tools/merge_predicate.py`'s docstring catalogs." That was a strawman:
  spike-9 measured that the *existing* resolver already finds the PR when its `state`
  filter is not artificially restricted to `"open"`. No second resolver is written,
  no new ambiguity surface is created, and the two-pass ordering (open wins,
  `merged` only on `None`) means the multi-PR hazard is strictly no worse than
  today's. The
  genuine rabbit hole nearby — writing a *bespoke* PR search inside the verifier
  rather than fixing the shared resolver's call site — remains out of scope.
  Separately, ledger durability is still #2730's lane; this plan fixes a lookup
  filter, not the ledger.
- **Making the verifier fail closed on a missing `pr_number`** ("if we cannot verify,
  refuse to advance"). It is superficially the safe posture and it is what the
  current code accidentally does. It is wrong here: the observed consequence is
  re-dispatching `/do-build` on merged work, which is a *duplicate-PR* risk, not a
  safety property. #1267 already chose narrow fail-open for this gate and named the
  #2003 merge gate as the hard backstop.
- **Fixing the ledger-loss mechanism.** Real, live (Risk 1), and someone else's
  lane. This plan's contract is to behave sanely on a degraded ledger, not to
  prevent degradation.
- **Adding a terminal dispatch row or verdict.** Filed as #2817. It is a router
  change, it is not needed to stop the false rebuild, and doing it here would
  require the fence extension this plan exists to avoid.
- **Restructuring all three artifact checks into a table-driven loop.** PLAN has its
  identifiability guard (`plan_path is not None`, `:221`), BUILD gains one, and PATCH
  gains a second one for the `pr_number`-absent case (its existing `bool(lane_branch)`
  guard covers a different absence). That is three similar-looking branches, which
  invites a refactor into a loop — resist it. Each check consults a different service
  with different failure semantics, and #1267's positioning contract is easier to
  audit as straight-line code. An earlier draft said "PATCH already has the guard,
  confirm by reading, then stop", which contradicted spike-4; the correction is that
  PATCH needed a *second* guard, not that the audit should be skipped.

## Risks

### Risk 1: The ledger loses state that was written — and it is losing it now
**Impact:** #2638, #2566 and #2640 read *entirely* empty at plan time (`stages: {}`,
`pr_number: null`, `slug: null`), and a full ledger wipe on a live lane was observed
mid-run on 2026-08-14 around the merge of #2803 (`ec585ceb9`). Causation is not
established — the correlation is recorded, not asserted. What matters here is that
"the ledger lost fields that were written" is a live mechanism on this machine, so
the verifier must be correct on partial state, not just on the happy path. The
dangerous case is **partial** loss: `BUILD == completed` survives while `MERGE` and
`pr_number` are gone. A MERGE-marker short-circuit is blind to it by construction.
**Mitigation:** the primary fix is what actually addresses this risk — a lost
`pr_number` is now *re-resolved* from the merged PR rather than worked around, so the
degraded ledger stops mattering for the reported shape. The identifiability guard
covers the residual where no PR exists to find.

**A wholly-empty ledger is NOT "already safe"** — that claim appeared here in an
earlier draft and is **retracted** (round-1 BLOCKER 3). It is true only of
`_verify_stage_artifacts_live` in isolation, where `build_claimed` is `False`. It is
false at `decide()`, which first recovers `stage_states` from durable signals
(`tools/sdlc_next_skill.py:576-579` → `agent/pipeline_state.py:1564`, which reads
`("open", "all")` and therefore *does* see the merged PR) and so sets
`BUILD = completed` while `_compute_meta` leaves `pr_number` as `None`. That is the
reported reproduction end to end. The corresponding coverage is an **integration**
test on the recovery path, not the unit assertion this paragraph used to promise —
see Test Impact and task build-red.

### Risk 2: The gate is narrowed on the one branch nobody tests, so a real false claim slips through
**Impact:** G8's purpose is to catch a self-attested BUILD marker with no PR behind
it. After this change, a lane that genuinely never opened a PR *and* never recorded
a `pr_number` no longer trips G8. That is a real reduction in coverage, and it is
deliberate: the verifier cannot distinguish "never built" from "ledger forgot," and
guessing was the bug.
**Mitigation:** the state is not unowned. The dispatch table still routes on the
surviving stage markers — measured: `{BUILD: completed}` alone routes to
`/do-plan` (row 1), and a branch-with-no-PR routes to `/do-build` via row 5
(`_rule_branch_exists_no_pr`), which is the *correct* owner of "build must create
the PR" and reaches it through evidence rather than through a failed verification.
The #2003 merge gate remains the hard backstop that a PR-less lane cannot merge. And
the control test (`test_g8_redispatches_build_on_synthesized_false_pr_claim`) pins
that a recorded-but-dead PR still fires G8.

### Risk 3: ~~The terminal short-circuit trusts a self-attested marker~~ — RESOLVED BY CUTTING IT
**Status:** the MERGE short-circuit is no longer in the plan, so this risk is
retired rather than mitigated. It is kept on the record because two of its original
mitigations were wrong and should not be revived with the idea.

**What was wrong.** The mitigation claimed a falsely-terminal lane "still has no
`pr_number`-bearing route to `/do-merge`" — true only in the branch where `pr_number`
is absent, and silent about the branch where it is present. It also cited a
MERGE-specific write-path property that **does not exist**: the real protection is
generic, namely `_backfill_predecessors` plus the #2554 verdict invariant
(`tools/sdlc_stage_marker.py:744-766`), reinforced by MERGE being non-skippable
(`:328-336`). And nothing ever resets a stage, so once `MERGE` is completed it is
permanent — a reopened issue would have run its entire second pipeline with all three
artifact checks disarmed. That last point alone is disqualifying for a
short-circuit whose only benefit was avoiding a few subprocesses.

### Risk 3b: The primary fix resolves the wrong PR
**Impact:** widening the `state` filter enlarges the candidate set, so a lookup that
previously returned `None` could now return a PR that is not the lane's — an
incorrect `pr_number` is worse than a missing one, because G8 would then verify
against a stranger's artifact.

**Round-2 correction — this risk was real and the mitigation was insufficient.** An
earlier revision used `state="all"` for the second pass and claimed the two-pass
ordering made it safe. That covers ordering *between* passes but not candidate
selection *within* a pass: `_gh_pr_search_issue_ref` (`tools/sdlc_stage_query.py:284-335`)
returns the **first** body-validating candidate (`:325-332`) with no MERGED-over-CLOSED
preference. Demonstrated live on this repo:

| Issue | `open` | `merged` | `all` |
|---|---|---|---|
| #2793 | `None` | `None` | **2794 — closed UNMERGED** |

Under the `all` shape, #2793 would resolve `pr_number = 2794`, `_fetch_pr_state` would
read `CLOSED`, and G8 would fire — converting today's silent no-op into an **active
false rebuild**. The second pass is therefore scoped to **`state="merged"`**, which
returns `None` for that shape while still resolving all three reported issues
(#2638→2686, #2566→2665, #2640→2671, identical to the `all` result). This is not
merely safer, it is more correct: a closed-unmerged PR is not evidence that BUILD
produced an artifact.
**Mitigation:** three layers, none of them new. The two-pass ordering means the
second pass runs **only** when the first returns `None`, so no lookup that succeeds
today can change. `_lookup_pr`'s own validation is unchanged and is already strict:
the issue-number search trusts a match only when the PR body carries a literal
word-boundary closing-keyword reference to the exact issue (`_gh_pr_search_issue_ref`),
and the branch-head fallback requires an exact head-ref match on the lane's
**recorded** slug. Fuzzy digit matches are rejected on both paths. Finally, #2539
shipped this same widening at a sibling call site and the measured results here are
exact: the three reported issues resolve to precisely the PRs that closed them.

**Accepted residual — a reopened issue.** If an issue is reopened and a second
lifecycle begins, the `merged` pass still finds the *prior* lifecycle's merged PR
(its body still reads "Closes #N"). `pr_state` reads `MERGED`, so an unfinished
second-lifecycle BUILD would be reported verified. This is named rather than fixed,
for three reasons: it is strictly narrower than the bug being fixed (it over-verifies
one stage rather than re-dispatching work on every tick); the #2003 merge predicate
remains the hard backstop against actually merging an unbuilt lane; and the clean fix
— trying `_lookup_pr`'s exact `--head <lane branch>` leg before the fuzzy `#N` search,
since a prior lifecycle's branch cannot match the current lane's — reorders the
resolution ladder for **every** caller, which is a larger change than this plan's
fence should absorb on a case nobody has reported. **Filed as its own issue** during
task build-tests-docs, cross-referencing this paragraph.

### Risk 4: The repaired integration test is repaired to the wrong expectation
**Impact:** the test currently asserts `row_id == "10"` on a fixture that cannot
reach row 10. If the repair guesses at the missing pieces, it will either stay red
or be quietly weakened to assert something trivially true — which is how it became
useless the first time.
**Mitigation:** the repair is spelled out from measurement, not inference (spike-5):
the verdict must be APPROVED *and recorded*, its `head_sha` must match what the
head-SHA resolver returns, and the fake must answer both the git-first and `gh`
fallback paths or the fail-closed sentinel routes to row 8f. The Verification table
runs the whole class, and the new #2757 test asserts a *negative* precisely because
spike-1 showed the positive is not what happens.

### Risk 5: `main` moves under the fix
**Impact:** `main` advanced twice during plan authoring, and two active plans
(#2766, #1799) name the same file. A build that starts from a stale checkout will
produce a conflicting patch in a function with four recent contributors.
**Mitigation:** the fence is one function and one helper, and neither active plan
touches `_verify_stage_artifacts_live` (#2766 owns the lock-peek block at `:513-549`;
#1799 owns `_build_context`). Re-read the function at branch time; if either lane
has landed inside it, stop and report rather than rebasing by hand.

## Race Conditions

### Race 1: The ledger changes between the stage read and the routing decision
**Location:** `tools/sdlc_next_skill.py::decide` — `_resolve_enriched` (`:552`)
through `decide_next_dispatch`
**Trigger:** a concurrent stage-marker write (another lane, a supervisor, a
reflection) lands between the ledger read and the guard walk. In particular, `MERGE`
can flip to `completed` in that window.
**Data prerequisite:** none new — `stage_states` and `meta` are read once, together,
from one `PipelineLedger` record, and this plan reads only from that snapshot.
**State prerequisite:** none. With the terminal short-circuit cut, this function no
longer reads `MERGE` at all, so a mid-flight flip of that marker cannot split its
view.
**Mitigation:** structural, and narrowed by this plan rather than merely mitigated.
The earlier draft needed an argument here about keeping the terminal decision and the
artifact decision on one consistent snapshot; cutting the short-circuit removes the
second decision entirely. `stage_states` and `meta` are still read once, together,
from one `PipelineLedger` record. Both
outcomes of a stale read are benign: a just-turned-terminal pipeline read as
non-terminal now falls through to a BUILD check that no-ops anyway (no `pr_number`
after merge is the whole premise), and the next tick sees the marker.

### Race 2: The PR merges between the ledger read and the `gh pr view` call
**Location:** `_verify_stage_artifacts_live:269-278`
**Trigger:** a PR merges in the milliseconds between the ledger snapshot and the
live state read.
**Data prerequisite:** a recorded `pr_number` (otherwise no call is made at all).
**State prerequisite:** none.
**Mitigation:** pre-existing and unchanged — `MERGED` is an accepted state (#1267),
so a mid-flight merge verifies clean either way. This plan adds no new live call and
removes one call path.

## No-Gos (Out of Scope)

- [**MOOT — close #2817 with evidence**] **A named terminal verdict for next-skill.**
  #2817 was filed on spike-1's belief that a terminal pipeline is left at
  `Blocked(NO_RULE)` once G8 stops firing. spike-10 measures that restoring
  `pr_number` routes the same ledger to `/do-merge` **row 10** under both branch
  states, so the residual this issue exists to name does not survive the primary fix.
  Post-merge, comment the spike-10 measurement on #2817 and close it rather than
  leaving a phantom follow-up open. `Blocked(NO_RULE)` is also not the benign state
  spike-1 assumed — the router documents it as a genuine hole in the table and the
  SDLC skill surfaces it and waits, i.e. it is a human-escalation wedge that would
  have been *routine* post-merge under the earlier draft.
- [SEPARATE-SLUG #2730] **Fixing the ledger loss itself.** The empty ledgers on
  #2638/#2566/#2640 are the precondition for this bug, not the bug. The
  ledger-durability lane owns them. This plan's obligation is to behave correctly on
  a degraded ledger.
- **Reordering `GUARDS` so G6 precedes G8.** Not deferred — **rejected on
  evidence.** G6 returns `None` without a `pr_number` (spike-2), which is the exact
  reported shape, so the reorder cannot fix this bug while weakening #1267's
  G4-bounds-G8 contract for every lane.
- **Making row 8e defer on a terminal pipeline.** Not deferred — **rejected on the
  merits** (spike-5 + Key Elements). 8e owns the unearned-`REVIEW`-marker state; a
  ledger that also claims `MERGE` without a verdict is less trustworthy, not more.
  The red test it produces is a stale fixture and is repaired inside this fence.
- **Any edit to `agent/pipeline_state.py`, dispatch records, or
  `tools/sdlc_session_ensure.py`.** Active lanes (#2802, #2766). Not touched, not
  imported by anything this plan adds.

## Update System

No update system changes required. This is a predicate correction inside one
existing module: no new dependency, no config file, no secret, no env var, no
Popoto model change and therefore no entry in `scripts/update/migrations.py`.

Standard post-merge propagation applies — `/update` on each machine, then a worker
restart so long-lived sessions pick up the new module. Note that `sdlc-tool
next-skill` is invoked as a fresh subprocess per call, so the fix takes effect on
the next invocation without any restart; the restart is for consistency, not
correctness.

## Agent Integration

No agent integration required. `sdlc-tool next-skill` is already a registered CLI
entry point in `pyproject.toml [project.scripts]` and is already the routing surface
every SDLC skill calls. This plan changes what that command returns in one state; it
adds no command, no flag, no MCP tool, and no bridge import.

The one agent-visible surface is the JSON on stdout, and the change to it is
subtractive: in the reported state it stops returning a `/do-build` dispatch. Skills
consuming that JSON already handle both the `dispatched` and `blocked` shapes
(`.claude/skills/sdlc/SKILL.md:261`), and `NO_RULE` is an existing `guard_id`, not a
new one.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-router-oscillation-guard.md` — the G8 doc of
  record. Its **"Verified artifact set (top 3, deterministic)"** table (`:127-134`)
  says BUILD is "verified when state is `OPEN` or `MERGED`", which omits the case
  that matters: no recorded PR number means no check at all. Add the unverifiable
  column/qualifier to the BUILD row, and add a short paragraph after the table
  stating the three-state distinction (verified / falsified / unverifiable), and note
  that the identifiability guard is defense in depth behind the upstream PR-number
  resolution — a reader who sees only the guard will conclude the gate was weakened.
  The existing note at `:135-136` ("A stage with no claimed artifact ... is a no-op —
  verification never invents a check") is the sentence this fix finally makes true
  for BUILD; extend it rather than writing a parallel one.
- [ ] Update `docs/features/sdlc-pipeline.md` — the **"Stage-Advance Verification
  Gate (G8, issue #1267)"** section (`:50-80`). Its bullet list describes where
  verification runs, positioning, firing condition, and contract; note in the
  firing-condition bullet that an unresolvable artifact identifier is a no-op, not a
  mismatch, and that `pr_number` now resolves for merged PRs.
- [ ] Update `.claude/skills/sdlc/SKILL.md:188` — "**G8 makes no live calls**"
  paragraph. It describes the verifier's live checks; add one clause that a stage
  whose artifact identifier is absent is skipped rather than reported as a mismatch.
  Keep it to one clause — this file is read into every SDLC session's context.
- [ ] Update `docs/sdlc/do-test.md:156-166` — the stale-fixture guardrail produced by
  **#2091** (`docs/plans/completed/fix-sdlc-router-merge-termination.md`), which
  already adjudicated this exact class of defect: a test fixture that encodes a
  routing expectation invalidated by a later router change. Its scope is currently
  `tests/unit/test_sdlc_router*.py`, which is precisely why the **integration**
  fixture repaired by this plan escaped it. Widen the note to cover integration
  fixtures that assert `row_id`.
- [ ] Also fix `docs/features/sdlc-router-oscillation-guard.md:133` while in that
  table — it still describes the PLAN check by slug, which #2792 made false. It is
  one line inside a table the builder is already editing.
- [ ] No new feature doc, and therefore no `docs/features/README.md` index entry.
  Creating `docs/features/g8-terminal-*.md` would be a parallel artifact for a
  behavior already documented in two places.

### Inline Documentation
- [ ] `_compute_meta`'s two-pass lookup: a comment naming #2539 as the precedent,
  `agent/pipeline_state.py:1564` as the in-repo idiom, why the second pass runs only
  on `None` (an open PR must always win over a historical one), and why it is scoped
  to `merged` rather than `all` (a closed-unmerged PR is not a build artifact, and
  admitting one would make G8 fire — demonstrated on #2793).
- [ ] No new helper docstring — the earlier draft's `_pipeline_is_terminal_from_states`
  is cut, so there is no third spelling of the terminal predicate to document.
- [ ] `_verify_stage_artifacts_live` docstring (`:189-210`): extend the
  merged-pipeline-misfire paragraph with the #2757 case and the three-state
  distinction. **Paraphrase the removed condition** — naming it verbatim trips the
  plan's own anti-criterion grep.
- [ ] The two new debug logs carry their reasons inline (unverifiable BUILD;
  unverifiable PATCH), in the register of the existing PATCH skip at `:258-263`.

## Success Criteria

- [ ] **The primary fix resolves the reported PRs.** `_compute_meta` returns
  `pr_number` = 2686 / 2665 / 2671 for issues #2638 / #2566 / #2640 respectively,
  where it returns `None` today (spike-9). Proven by a test that stubs `gh` for both
  passes, plus a live re-measurement recorded in the PR description.
- [ ] **No `/do-build` at ANY row** for a terminal pipeline whose PR is merged —
  asserted as `result.get("skill") != "/do-build"`, never as "not row G8". Tested with
  `branch_exists: True` **and** `branch_exists: False`, because spike-8 measured the
  rebuild relocating to row 5 exactly in the `True` case, which is this repo's normal
  post-merge state.
- [ ] **Every new assertion also requires no `error` key** and pins the measured
  expected dispatch. A negative-only assertion is satisfied by `decide()`'s catch-all
  (`:605-610`), so it would go green on a crash.
- [ ] **The recovery path is covered end to end**: an empty ledger + a merged PR + an
  existing branch produces no `/do-build` at any row. This is the true reproduction
  (`decide()` recovers `BUILD = completed` from durable signals while `pr_number`
  stays `None` and MERGE is never written), and it replaces the earlier draft's claim
  that a wholly-empty `stage_states` is "already safe" — which is false at `decide()`.
- [ ] **A `BUILD == "completed"` claim with a genuinely unresolvable `pr_number`**
  (no PR exists in any state), **no** `MERGE` marker — the residual shape that pins
  the identifiability guard. This criterion is **split by `branch_exists`, because the
  two cases have different correct answers**, and an earlier revision's blanket "no
  `/do-build` at any row" was **unsatisfiable** here (round-2 BLOCKER 2):
  - `branch_exists: False` → **no `/do-build` at any row**. G8 must not fire, and no
    other row owns the state.
  - `branch_exists: True` → **`/do-build` at row 5, and specifically NOT at G8.**
    This is correct and must not be "fixed": `_rule_branch_exists_no_pr` owns
    "a branch exists with no PR, so build must create the PR", which is exactly right
    for a lane that genuinely never opened one (Risk 2). Assert
    `result["row_id"] == "5"` — the assertion proves the guard stopped G8 from
    manufacturing a verdict *without* disturbing a row the plan deliberately leaves
    out of fence.

  With a `MERGE` marker present, the earlier draft's short-circuit would have masked a
  reverted guard, which is why this criterion requires its absence.
- [ ] A recorded `pr_number` whose live state is `CLOSED` **still** sets
  `stage_artifacts_verified: False` and still re-dispatches `/do-build` via G8
  (`test_g8_redispatches_build_on_synthesized_false_pr_claim`, unchanged and green).
  The gate is narrowed, not disarmed.
- [ ] `test_terminal_merged_pipeline_routes_to_merge_not_build` is **green**, with no
  `xfail`, no skip, and no weakened assertion — and its fixture stays **live**: the
  `gh pr view` / `git ls-remote` calls are still reached, so it still proves a MERGED
  PR is an acceptable BUILD artifact.
- [ ] `agent/sdlc_router.py`, `agent/pipeline_state.py`, and
  `tools/sdlc_session_ensure.py` are **byte-identical to the merge base** in the PR
  diff. (`tools/sdlc_stage_query.py` is now **inside** the fence — see Appetite.)
- [ ] The unverifiable-BUILD skip logs at debug and the falsified-BUILD mismatch
  still logs at warning, asserted by level.
- [ ] `sdlc-tool next-skill --issue-number 2755` still returns row 10 (the live
  negative control from spike-7).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

**Two agents, three tasks.** The earlier draft specified three agents and six tasks
for roughly fifteen lines plus tests, with tasks 3 and 5 being the same validation
work run twice. That is ceremony, not rigor. The documentarian role folds into the
builder — three prose edits to existing files, made by the person who just changed
the behavior they describe — and validation runs once, at the end, when there is
something complete to validate. A separate test-engineer would cost a full context
handoff for test files the builder is already editing line by line, and the
red-test-first step makes the builder hold both halves anyway.

### Team Members

- **Builder (verifier)**
  - Name: `g8-verifier-builder`
  - Role: write the red regression tests first; then the `_compute_meta` two-pass
    lookup, the BUILD and PATCH identifiability guards, the full unit + integration
    test surface, and the three documentation edits
  - Agent Type: builder
  - Domain: Redis/Popoto data (the integration test drives a real `PipelineLedger`)
  - Resume: true

- **Validator (verifier)**
  - Name: `g8-verifier-validator`
  - Role: verify every acceptance criterion, especially the negatives — the gate
    still fires on a dead recorded PR, no `/do-build` at any row under both
    `branch_exists` values, no file outside the (extended) fence changed, nothing
    left red or `xfail`-ed
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Per the template's Tier 1 list. The builder carries a `Domain: Redis/Popoto data`
tag — paste the matching rules from `DOMAIN_FRAMING.md` into its assignment,
particularly the never-raw-Redis rule: the integration test's ledger setup and
teardown go through `PipelineLedger.get_or_create` / `.save()` / `.delete()`, never
raw Redis, and the existing `cleanup_ledger` fixture (`:416-429`) is the pattern.

### Step by Step Tasks

### 0. Red-test-first
- **Task ID**: build-red
- **Depends On**: none
- **Validates**: `tests/integration/test_sdlc_session_ensure_integration.py`
- **Informed By**: spike-6 (no existing test covers the term being changed), spike-8 (the rebuild relocates to row 5, so a G8-specific assertion is not a regression test), spike-5 (the one test that looks like a regression test does not exercise this shape)
- **Assigned To**: `g8-verifier-builder`
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: false
- **First, confirm the inherited red reproduces in a clean checkout**:
  `test_terminal_merged_pipeline_routes_to_merge_not_build` must fail at row **8e**
  before anything is touched. spike-5 measured this, but it has been red on `main`
  under default collection for some time — verify rather than assume, and record the
  output.
- Write `test_no_pr_number_recorded_does_not_redispatch_build` against a real
  `PipelineLedger`: `BUILD` completed, **no** `MERGE` marker, and **no** `pr_number`
  resolvable in any state. Parametrize over `branch_exists`, asserting the two
  different correct outcomes per Success Criteria: `False` → no `/do-build` at any
  row; `True` → `/do-build` at **row 5** and **not** at G8. Both assert **no `error`
  key**. The absent `MERGE` marker is deliberate: it is what makes this test pin the
  identifiability guard rather than a short-circuit. Do **not** "fix" the row-5
  dispatch — it is the designed behavior for a lane that genuinely never opened a PR,
  and `_rule_branch_exists_no_pr` is deliberately out of fence.
- Write the recovery-path sibling: an **empty** ledger + a merged PR + an existing
  branch → no `/do-build` at any row, no `error` key. This is the reproduction end to
  end (`decide()` recovers `BUILD` from durable signals while `pr_number` stays
  `None`), and it replaces the earlier draft's false "an empty ledger is already
  safe" assertion.
- Run all of them against the current modules and **watch them fail**. Record the
  failure text in the PR description. A test that passes here is not yet a
  regression test — check that `decide()` did not simply return an `error` dict.
- Do not edit `tools/` in this task.

### 1. Fix the lookup and the guards
- **Task ID**: build-fix
- **Depends On**: build-red
- **Validates**: `tests/unit/test_sdlc_stage_query.py`, `tests/unit/test_sdlc_next_skill.py`, `tests/integration/test_sdlc_session_ensure_integration.py`
- **Informed By**: spike-9 (the primary fix: `_lookup_pr` defaults to `state="open"`, so merged PRs are invisible; #2539 is the precedent), spike-10 (restoring `pr_number` reaches `/do-merge` row 10 under both branch states), spike-8 (silencing G8 alone relocates the rebuild to row 5), spike-2 (guard reordering cannot fix this)
- **Assigned To**: `g8-verifier-builder`
- **Agent Type**: builder
- **Parallel**: false
- **`tools/sdlc_stage_query.py::_compute_meta` (`:548`) — do this first.** Two-pass
  `_lookup_pr`: existing call, then a **`state="merged"`** retry **only** when it
  returns `None`. Do not change `_lookup_pr`'s own default. Comment naming #2539 and
  `agent/pipeline_state.py:1564`, and why open must win over historical.
- **`tools/sdlc_next_skill.py`** — gate the BUILD check on a recorded `pr_number`,
  mirroring `patch_claimed`'s `bool(lane_branch)` guard; debug-log the skip. Remove
  the old condition outright, no demoted `elif`.
- Apply the same guard to the **PATCH** branch probe when `pr_number` is absent
  (a missing branch is unreadable without a PR state to consult).
- **Add no terminal predicate and no short-circuit.** If a terminal check ever seems
  needed, add a pure `pipeline_is_terminal(states)` to `tools/_sdlc_run_identity.py`
  and delegate — never a third near-homonym.
- Extend the function docstring's merged-pipeline-misfire paragraph. **Paraphrase**
  the removed condition; never quote it.
- Touch nothing else. `_build_context` and the lock-peek block belong to other
  active lanes.

### 2. Test surface, docs, and the follow-up issue
- **Task ID**: build-tests-docs
- **Depends On**: build-fix
- **Validates**: `tests/unit/test_sdlc_next_skill.py`, `tests/unit/test_sdlc_stage_query.py`, `tests/integration/test_sdlc_session_ensure_integration.py`
- **Informed By**: spike-5 (the exact fixture repair: recorded APPROVED verdict + matching `head_sha` + a fake answering both the git-first and `gh` head-SHA paths, or the fail-closed sentinel routes to row 8f), spike-6 (existing BUILD tests all set `pr_number = 555` and must stay untouched)
- **Assigned To**: `g8-verifier-builder`
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: false
- Repair `test_terminal_merged_pipeline_routes_to_merge_not_build`: add the
  `_verdicts["REVIEW"]` APPROVED record with `head_sha`, extend
  `_fake_gh_pr_view_merged` to answer `headRefOid` and
  `git ls-remote origin refs/pull/N/head` while preserving its branch-gone answer for
  `git ls-remote --heads`. **Do not add `MERGE: "completed"`** — that would make the
  fixture dead code. Rewrite its docstring to say what it actually defends; rename it
  if the name is the concern.
- Add a `_compute_meta` unit test: `gh` stubbed so the `open` pass returns nothing
  and the `merged` pass returns a PR → `pr_number` resolves. Plus two negatives: when
  the `open` pass returns a PR the second pass is **not** run; and an issue whose only
  body-validating PR is **closed-unmerged** resolves to `None`, not to that PR (the
  #2793 shape — the regression test for round-2 BLOCKER 1).
- Add the unit cases from Test Impact: the parametrized falsy-`pr_number` case, the
  `assert_not_called()` subprocess assertions, and the debug-vs-warning log levels
  (with `caplog.set_level(logging.DEBUG)`, or they pass vacuously).
- Leave `test_g8_redispatches_build_on_synthesized_false_pr_claim` and the three
  `pr_number = 555` unit tests untouched — their passing is an assertion.
- Apply every checkbox in the Documentation section, including widening the
  `docs/sdlc/do-test.md:156-166` stale-fixture guardrail to integration fixtures.
- **File the gate-gap issue**: a red integration test sat on `main` under default
  collection (`pyproject.toml:155` `testpaths = ["tests"]`) without blocking anything.
  That is a CI/collection gap independent of this fix and needs its own issue.

### 3. Validate
- **Task ID**: validate-all
- **Depends On**: build-tests-docs
- **Assigned To**: `g8-verifier-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row and confirm every Success Criterion. This is the only
  validation pass; there is no second identical one.
- Confirm each negative independently: the control test still produces `G8`; no
  `/do-build` at **any** row under **both** `branch_exists` values; no file outside
  the extended fence differs from the merge base; no `xfail`, skip, or weakened
  assertion.
- **Demonstrate demonstrated-red for the guards.** Revert each guard in turn and
  confirm the corresponding test goes red, then restore. A gate never shown to fail
  has not been verified. Explicitly include the `_compute_meta` two-pass fix.
- Verify the new tests do not pass via `decide()`'s catch-all: assert no `error` key
  is present in any asserted result.
- Confirm no raw Redis access was introduced in the test setup or teardown
  (`PipelineLedger.get_or_create` / `.save()` / `.delete()`; `cleanup_ledger` at
  `:416-429` is the pattern).

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_next_skill.py -q` | exit code 0 |
| Lane-identity G8 tests pass | `scripts/pytest-clean.sh tests/unit/test_lane_identity.py -q` | exit code 0 |
| Artifact-gate integration tests pass | `scripts/pytest-clean.sh tests/integration/test_sdlc_session_ensure_integration.py -q` | exit code 0 |
| Router tests unaffected | `scripts/pytest-clean.sh tests/unit/test_sdlc_router_oscillation.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Stage-query tests pass (fence extended) | `scripts/pytest-clean.sh tests/unit/test_sdlc_stage_query.py -q` | exit code 0 |
| **The absent-PR branch is behaviorally gone** (load-bearing) | `scripts/pytest-clean.sh tests/unit/test_sdlc_next_skill.py -k unverifiable -q` | exit code 0 |
| The old term is not textually present (advisory only) | `! grep -q 'not pr_number' tools/sdlc_next_skill.py` | exit code 0 |
| Fence held — router and off-limits modules untouched | `git fetch origin main && git diff --quiet "$(git merge-base origin/main HEAD)" -- agent/sdlc_router.py agent/pipeline_state.py tools/sdlc_session_ensure.py` | exit code 0 |
| No expected-failure marker introduced | `! grep -rqE 'pytest\.mark\.xfail\|pytest\.xfail\(' tests/unit/test_sdlc_next_skill.py tests/integration/test_sdlc_session_ensure_integration.py` | exit code 0 |
| Live negative control still routes terminal | `.venv/bin/python -m tools.sdlc_next_skill --issue-number 2755` | output contains `"row_id": "10"` |

**Four notes on these rows.**

**The string greps are advisory; the behavioral tests are the gate.** `! grep -q 'not
pr_number'` is trivially evaded by writing `if pr_number is None or ...`, so it can
pass while the defect is fully present — it is not sound as an acceptance criterion.
It is retained only as a cheap smoke check for a literal revert, explicitly demoted,
and the `assert_not_called()` / no-`/do-build`-at-any-row tests are what actually pin
the behavior. The same grep also still trips on its own documentation: the mandated
docstring rewrite must **paraphrase** the removed condition ("the old
absent-PR-number branch"), never quote it.

**The `xfail` row is anchored on the marker forms**, not the bare substring. The
earlier `! grep -rq 'xfail'` matched this plan's own mandated docstring sentence
stating that nothing was left xfail-ed — an anti-criterion that fails on prose
describing its own satisfaction.

**The fence row diffs against the merge base, not `origin/main`.** Diffing a moving
ref fails spuriously whenever main advances and touches those files (#2802 was
actively merging one of them) and passes vacuously against a stale ref. The explicit
`git fetch` plus `git merge-base` pins it to the lane's actual divergence point.

The "must not appear" rows use `! grep -q` rather than a count compared to zero.
`grep -c` exits 1 on zero matches, which a `set -e` runner scores as a failure, and
`grep -rc` prints per-file count lines rather than a single number. The `!`-form
exits 0 on the clean case, matching the positive rows.

The live-negative-control row depends on `gh` auth and on #2755's ledger still being
intact; it is evidence, not a hermetic gate. If that ledger has been wiped by the
time the row runs (Risk 1 makes this plausible), record the wipe and rely on the
integration tests — do not "fix" the row by weakening it.

## Critique Results

### Round 3 — war room 2026-08-16

**Verdict: READY TO BUILD (with concerns)** — FULL roster (Risk & Robustness, Scope
& Value, History & Consistency). 4 findings: **0 BLOCKERs**, 3 CONCERNs, 1 NIT.

All three round-2 BLOCKERs are independently re-verified as genuinely fixed, not
papered over:

**The `merged`-scoped second pass is sound.** Re-measured live by the war-room
driver against the installed `gh`: #2793 → `None` under both `open` and `merged`
but **2794 (closed, unmerged)** under `all`; #2638/#2566/#2640 → 2686/2665/2671
under `merged`, identical to `all`. `_lookup_pr` threads `state` into **both**
resolution legs (the `#N` body search and the `--head <branch>` fallback), so
`merged` narrows both identically rather than selectively breaking one; `--state`
does not filter by base branch, so a PR merged into a non-default branch is still
found; and a draft PR cannot reach `MERGED`, so drafts are moot. The one residual
(a reopened issue resolving its prior lifecycle's merged PR) is already named and
accepted in Risk 3b.

**The split Success Criterion 5 is satisfiable AND still catches a reverted
guard.** `GUARDS = [G1, G2, G3, G4, G8, G7, G5, G6]` runs before the dispatch
table, so reverting the BUILD identifiability guard makes G8 fire first and return
`row_id: "G8"` — row 5 is never reached. The `branch_exists: True` cell's
`result["row_id"] == "5"` assertion therefore flips on a reversion, and the
`branch_exists: False` cell's "no `/do-build` at any row" holds independently.
The criterion is no longer self-contradictory with Risk 2 and is not weakened into
something trivially true.

**Risk 1's retraction is in place**, with the `decide()`-level recovery mechanism
named by file:line and the promised coverage relocated to the recovery-path
integration test.

Three CONCERNs remain. None blocks; each has an Implementation Note the revision
pass should embed.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| CONCERN | Scope | Round-2 BLOCKER 1's fix is not pinned by any Success Criterion. SC 1 asserts only the positive resolution (2686/2665/2671); the negative that makes the fix correct — the #2793 shape resolving to `None` rather than to closed-unmerged PR 2794 — and the strictly-additive ordering property (the second pass runs only when the first returns `None`) exist only as a Test Impact bullet and a task-2 bullet. A builder could satisfy every Success Criterion while silently reverting the second pass to `state="all"`. | pending | Promote the two negatives already written under Test Impact's "Add a `_compute_meta` unit test" bullet into Success Criteria items, anchored on `tools/sdlc_stage_query.py:548`: (a) an issue whose only body-validating PR is closed-unmerged resolves `pr_number` to `None`, not to that PR; (b) when the `open` pass returns a PR, the second pass is not invoked at all. |
| CONCERN | Scope | Risk 3b's round-2 disposition says the reopened-issue residual is "**Filed as its own issue** during task build-tests-docs", i.e. a second follow-up issue distinct from the gate-gap one. Task 2's bullet list contains only one file-an-issue action (the gate-gap issue). A builder executing the task list literally files one issue, and the round-2 CONCERN this disposition claims to address goes unfulfilled. | pending | Add a second bullet to task `build-tests-docs` mirroring the existing gate-gap bullet: "File the reopened-issue residual as its own GitHub issue, cross-referencing Risk 3b." Nothing in the task list or Success Criteria currently checks for it, which is exactly how a promised follow-up drops during build. |
| CONCERN | History | The "### Revision status (2026-08-16)" subsection's item 1 still reads "`_compute_meta` gets a two-pass `_lookup_pr` (open, then `all` only on `None`)". Every current-design section (Key Elements, Technical Approach, the Flow diagram, task `build-fix`) was corrected in round 2 to `state="merged"`. The subsection carries the same date as the Round 2 table directly above it and is not labelled round-1-specific, so it reads as a live design summary and a builder landing there first would implement the unscoped `all` pass that round 2 blocked on. | pending | Either rewrite item 1 to say `state="merged"` (noting round 2 corrected it from `all`), or re-title the heading "Revision status after Round 1 (2026-08-16)" so it cannot be mistaken for the post-round-2 design. The corrected phrasing already exists verbatim in Key Elements: "the second pass is scoped to `state="merged"` rather than `"all"` — see the round-2 correction below." |
| NIT | Structural | The Freshness Check cites `docs/plans/sdlc-lane-recorded-slug.md`, which no longer exists at that path — the plan has since been migrated to `docs/plans/completed/sdlc-lane-recorded-slug.md`. Every other referenced path in the plan resolves. | pending | One-word path fix in the "Foreign-PR reconciliation" bullet under Freshness Check: insert `completed/`. |

**Structural checks:** required sections present and substantive (Documentation
carries a `docs/features/` checkbox; Update System addresses `migrations.py` as
not-applicable with a reason; Agent Integration addresses MCP/CLI exposure; Test
Impact lists UPDATE/ADD/UNCHANGED dispositions per test). Task numbering and
dependencies valid (`build-red` → `build-fix` → `build-tests-docs` →
`validate-all`, no cycles, every task carries a validation target). No Popoto model
change, so no migration is required. All referenced file paths resolve except the
one NIT above. All three Prerequisites pass live, and spike-7's negative control
re-measured today still returns `{"skill": "/do-merge", "row_id": "10"}` for #2755.
**Cross-reference consistency now PASSES** — round-2's two failures (Success
Criterion 5 vs. Risk 2, and Risk 1's retracted claim) are both resolved.

### Round 2 — war room 2026-08-16

**Verdict: NEEDS REVISION** — FULL roster (Risk & Robustness, Scope & Value, History & Consistency). 6 findings: 3 BLOCKERs, 3 CONCERNs, 0 NITs.

The restructure is a genuine improvement and the round-1 dispositions hold up under
re-measurement: #2539's `state="all"` at `tools/sdlc_stage_marker.py:246-250`,
`agent/pipeline_state.py:1564`'s `for state in ("open", "all")`, PR #2792's scope, and
#2817's still-open status were all independently re-verified, and no reverted prior
attempt at widening this call site exists in `git log`. All three plan prerequisites pass
live, and spike-7's negative control still returns `row_id: "10"` for #2755.

Three findings block. Two are defects in the **new** design, not leftovers:

**The "strictly additive" guarantee does not cover candidate selection inside the `all`
pass.** The two-pass ordering makes the second pass run only on `None`, which is sound.
But `_gh_pr_search_issue_ref` (`tools/sdlc_stage_query.py:284-335`) returns the *first*
body-validating candidate and has no preference for `MERGED` over `CLOSED` (`:325-332`).
An issue with an abandoned closed-unmerged attempt alongside the PR that shipped — the
exact shape the plan's own Freshness Check documents for PR #2794 — can resolve to the
closed one. `pr_state` is then `CLOSED`, `pr_state not in ("OPEN", "MERGED")` fires, and
G8 re-dispatches `/do-build`. The primary fix would convert a silent no-op into an
*active* false rebuild in that shape.

**Success Criterion 5 is unsatisfiable as written.** It requires no `/do-build` at any row
for a BUILD-completed, unresolvable-`pr_number` ledger under both `branch_exists` values,
while the plan deliberately leaves `_rule_branch_exists_no_pr` (`agent/sdlc_router.py:922-927`)
outside the fence — and that row fires on `branch_exists is True` alone whenever
`pr_number` is falsy, without consulting `BUILD`. Risk 2's own mitigation calls that
routing *correct*. The criterion and the plan's stated-correct router behavior are mutually
exclusive for the identical input.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk | The two-pass fallback's "cannot newly surface a closed-unmerged PR ahead of an open one" guarantee covers only the ordering *between* passes, not candidate selection *within* the `all` pass. `_gh_pr_search_issue_ref` (`tools/sdlc_stage_query.py:284-335`) returns the first body-validating candidate (`:325-332`) with no MERGED-over-CLOSED preference, so an issue with a superseded closed-unmerged PR plus the shipped one can resolve to the closed one — `pr_state` reads `CLOSED`, G8 fires, and `/do-build` re-dispatches on shipped work. The primary fix would turn a silent no-op into an active false rebuild in that shape. | **ADDRESSED** — reproduced live and the fix changed: the second pass is now scoped to **`state="merged"`**, not `"all"`. Demonstrated on #2793, which resolves to closed-unmerged PR **2794** under `all` but to `None` under `merged`, while all three reported issues resolve identically under both. A closed-unmerged PR is not a build artifact, so excluding it is more correct, not merely safer. Regression test specified in Test Impact. See Risk 3b's round-2 correction. | Prefer MERGED explicitly: either make the second pass `state="merged"` (verify the value against the installed `gh` before adopting) or re-check the `all`-pass result with `_fetch_pr_state` and accept a non-MERGED candidate only as a last fallback. Cover it with a unit test whose `gh pr list --state all` stub returns two body-validating candidates CLOSED-first and asserts the MERGED one is chosen. |
| BLOCKER | Scope | Success Criterion 5 and `test_no_pr_number_recorded_does_not_redispatch_build` require `result.get("skill") != "/do-build"` at any row for a BUILD-completed, no-MERGE, unresolvable-`pr_number` ledger under **both** `branch_exists` values — but `_rule_branch_exists_no_pr` (`agent/sdlc_router.py:922-927`, deliberately out of fence) dispatches `/do-build` at row 5 whenever `pr_number` is falsy and `branch_exists is True`, regardless of `BUILD`. Key Elements and Risk 2 both call that routing *correct*. The criterion is unsatisfiable without a router change the plan forbids. | **ADDRESSED** — the criterion is split by `branch_exists`: `False` → no `/do-build` at any row; `True` → `/do-build` at **row 5 specifically, never G8**, which the plan states is correct and must not be "fixed". The red-test spec in Test Impact and task build-red now match. This turns the contradiction into the assertion that proves the guard narrowed G8 without disturbing an out-of-fence row. | `_rule_branch_exists_no_pr` returns `True` on `meta.get("pr_number")` falsy and `context.get("branch_exists") is True` without reading `BUILD`. Either drop the `branch_exists: True` cell from this specific test (the recovery-path test covers it with a genuinely-resolvable merged PR) or restate SC 5 to carve out the branch-exists-True / genuinely-unresolvable-PR cell as accepted `/do-build`, matching Risk 2's own concession. Do not let the builder "fix" this by weakening the assertion silently. |
| BLOCKER | History | Risk 1's Mitigation still asserts "A wholly-empty ledger is already safe (`build_claimed` is `False`, so G8 cannot fire) and is asserted as a test case" — the exact claim round-1 BLOCKER 3 forced retracted, and which the plan retracts in three other places (Empty/Invalid Input Handling, Success Criteria, the round-1 table). A builder working from Risk 1 writes the wrong test and skips the recovery-path coverage that is the true reproduction. | **ADDRESSED** — Risk 1's Mitigation paragraph is rewritten: the "already safe" claim is explicitly retracted in place, the `decide()`-level recovery mechanism is named with file:line, and the promised unit assertion is replaced by the integration test the plan actually specifies. | The stale sentence is in Risk 1's Mitigation; the corrected framing already exists verbatim under Empty/Invalid Input Handling ("this is not 'already safe' ... is retracted"). Copy it, and point at the recovery-path integration test rather than claiming a unit-level assertion that no longer exists. Also drop "and is asserted as a test case" — no such unit test is planned. |
| CONCERN | Risk | A reopened issue is not covered by either fix. With a new BUILD in flight and no new PR, the `open` pass returns `None` and the `all` pass finds the *prior lifecycle's* merged PR (its body still says "Closes #N"). `pr_state` reads `MERGED`, so an unfinished second-lifecycle BUILD is reported **verified**, and the lane can advance toward `/do-merge` on an artifact that was never checked. Same class as the bug being fixed: trusting an identifier never validated as belonging to the current attempt. | **ADDRESSED as a named residual** — recorded in Risk 3b with the reasoning: it over-verifies one stage rather than re-dispatching work, #2003's merge predicate is the hard backstop, and the clean fix (branch-head leg before the fuzzy `#N` search) reorders the resolution ladder for every caller, which is larger than this fence. **Filed as its own issue** in task build-tests-docs. | `_lookup_pr` (`tools/sdlc_stage_query.py:338-381`) tries the fuzzy `#N` body search before the exact `--head <lane branch>` leg. Trying the branch-head leg first when a slug is recorded closes this case without touching the `state` ladder, since a prior lifecycle's branch cannot match the current lane's. At minimum name it as an accepted residual in Risk 3b rather than leaving it unstated. |
| CONCERN | Risk | Architectural Impact frames the extra `gh` call as paid "only on the path that currently resolves nothing," implying the degraded-ledger minority. `_compute_meta`'s own comment (`tools/sdlc_stage_query.py:529-530`) says it "runs for any issue number the router, the dashboard, or an operator names — most of which are not lanes," and those have no PR at all, so the `open` pass already returns `None` today. The majority case pays a second full `gh pr list --search --state all` round-trip per invocation. | **ADDRESSED** — Architectural Impact now states plainly that the majority of invocations are non-lane and therefore pay the retry, cites `_compute_meta`'s own comment, and records the **measured** cost (~0.89s, bounded by the existing 5s timeout). A cache is explicitly rejected as breaking the plan's no-new-surface property. | No code change is strictly required — restate the cost honestly for the non-lane majority in Architectural Impact. If a cheaper shape is wanted, skip the `all` retry when the `open` pass returned zero raw candidates before body-validation; do not add a persistent cache, which would break the plan's "no new dependencies / no new architectural surface" claim. |
| CONCERN | History | The Inline Documentation checklist names the two new debug logs as "unverifiable BUILD; terminal pipeline". "Terminal pipeline" is leftover text describing the cut MERGE short-circuit's log, which exists nowhere else in the revised plan; Error State Rendering and Technical Approach both correctly say "unverifiable BUILD" and "unverifiable PATCH". | **ADDRESSED** — the Inline Documentation bullet now reads "unverifiable PATCH" instead of the cut short-circuit's "terminal pipeline" log. | One-word fix: change "terminal pipeline" to "unverifiable PATCH" so all three enumerations of the two new debug logs agree. The correct phrasing already exists under Error State Rendering ("Both new debug logs (unverifiable BUILD, unverifiable PATCH)") — reuse it verbatim. |

**Structural checks:** required sections present and substantive; task numbering and
dependencies valid (build-red → build-fix → build-tests-docs → validate-all, no cycles,
every task carries a validation target); all 21 referenced file paths exist; all three
Prerequisites pass live. Cross-reference consistency **FAILS** on Success Criterion 5
(BLOCKER 2 above) and on Risk 1's retracted claim (BLOCKER 3).

### Revision status (2026-08-16)

**All 7 BLOCKERs and all 11 CONCERNs addressed; plan restructured, ready for
re-critique.** The two decisive findings were independently re-measured against live
code before the revision was written, not taken on the critique's word:

| Re-measured claim | Result |
|---|---|
| `_lookup_pr` defaults to `state="open"` (`tools/sdlc_stage_query.py:338-342`), call site at `:548` passes no `state` | **confirmed** |
| `_lookup_pr(state="all")` recovers the reported PRs | **confirmed** — #2638→2686, #2566→2665, #2640→2671 (all `None` under `open`) |
| `_rule_branch_exists_no_pr` fires on `branch_exists` alone when `pr_number` is falsy (`agent/sdlc_router.py:922-927`) | **confirmed** — measured `/do-build` at row 5 |
| Restoring `pr_number` reaches the terminal row | **confirmed** — `/do-merge` row 10 under **both** `branch_exists` values |
| `_pipeline_is_terminal`'s `agent/pipeline_state.py` import is function-local (`tools/_sdlc_run_identity.py:122`) | **confirmed** — the earlier draft's stated reason was false |
| Two terminal-predicate spellings already exist | **confirmed** — `_sdlc_run_identity.py:129`, `pipeline_state.py:1155` |
| `decide()` catch-all returns `{"error": ...}` (`tools/sdlc_next_skill.py:605-610`) | **confirmed** — a negative-only test would pass on a crash |
| `decide()` recovers empty `stage_states` from durable signals, which read `("open","all")` (`:576-579` → `pipeline_state.py:1564`) | **confirmed** — "empty ledger is already safe" is false |

**The three structural changes this produced:**

1. **The primary fix moved one layer up.** `_compute_meta` gets a two-pass
   `_lookup_pr` (open, then `all` only on `None`). This makes the outcome *correct*
   rather than merely silent, and required a **fence extension** to
   `tools/sdlc_stage_query.py` (recorded in Appetite).
2. **The MERGE short-circuit and its new helper are cut entirely** — inert for the
   real shape, redundant against the direct PATCH guard, trust-extending, and it
   would have hollowed out the repaired fixture.
3. **Acceptance is row-agnostic and positively pinned.** `result.get("skill") !=
   "/do-build"` at any row, both `branch_exists` values, plus a no-`error`-key
   assertion; string greps demoted to advisory with behavioral tests as the gate.

Also: **#2817 is moot** (spike-10) and should be closed with evidence rather than
left open, and a new gate-gap issue is filed for the red integration test that sat on
`main` under default collection.

### Round 1 — war room 2026-08-14

**Verdict: NEEDS REVISION** — FULL roster (Risk & Robustness, Scope & Value, History & Consistency).

The diagnosis at the verifier is correct and the row-8e call is honest and independently confirmed. But two findings are decisive and I verified both against the live router before recording them: **the planned fix does not fix the reported bug**, and **a cheaper fix one layer up removes the whole residual**. The plan needs restructuring around those, not patching.

### The two measurements that change the plan

**Silencing G8 relocates the rebuild to row 5.** `_rule_branch_exists_no_pr` (`agent/sdlc_router.py:922-927`) returns True on `context["branch_exists"] is True` alone whenever `pr_number` is falsy, regardless of `BUILD == "completed"`, and it sits before every PR-gated row. Measured through the router with the reported shape:

| context | dispatch |
|---|---|
| `branch_exists: True` | `/do-build`, row **5** |
| `branch_exists: False` | `no matching dispatch rule` (NO_RULE) |

This repo has `deleteBranchOnMerge: false` and `session/sdlc-2755` is still on origin after merging an hour ago, so the branch-present column is the normal case here, not the exotic one. spike-1 missed it by calling `decide_next_dispatch` with a hand-built context, bypassing `_build_context`, the only producer of `branch_exists`. Worst of all, Success Criterion 1 asserts only the absence of a **G8** `/do-build`, so the plan as written would go green while the bug persisted.

**The proximate cause is one layer up, and #2539 already solved it.** `_meta["pr_number"]` is resolved via `_lookup_pr` (`tools/sdlc_stage_query.py:548`), whose signature defaults to `state: str = "open"` (`:338-342`). A merged PR is invisible under `open` — that is why the symptom appears "minutes after their merges". `tools/sdlc_stage_marker.py:246-250` already passes `state="all"` for exactly this reason, with the comment that the artifact question is historical, not in-flight. Measured: with `pr_number` present, the same terminal ledger routes to `/do-merge` row 10 under **both** branch states. Restoring `pr_number` makes G8 pass on its own merits, stops row 5 firing, reaches the terminal row, and deletes the `Blocked(NO_RULE)` residual and #2817 entirely.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk | Silencing G8 converts the reported G8 `/do-build` into a row-5 `/do-build` on shipped work. `_rule_branch_exists_no_pr` fires on `branch_exists` alone when `pr_number` is falsy. Verified by direct measurement through the router; this repo keeps merged branches, so the hazard is live. | **ADDRESSED** — re-measured independently (row 5 confirmed at `/do-build`). Every acceptance assertion is now row-agnostic (`skill != "/do-build"`) with both `branch_exists` values; see spike-8 and Success Criteria. | Restructure around restoring `pr_number` (below). Strengthen the acceptance assertion from "not G8 `/do-build`" to `result.get("skill") != "/do-build"` at any row, and add a `branch_exists: True` variant. |
| BLOCKER | Risk | Root cause misdiagnosed one layer up. `_lookup_pr` defaults to `state="open"` so a merged PR is invisible to the `pr_number` fallback. The Rabbit Hole "Rebuilding `pr_number` from the world" mischaracterizes this as a second identity resolver on a hot path; it is the existing resolver one keyword argument away, with #2539 as direct precedent. | **ADDRESSED** — adopted as the plan's PRIMARY fix, shaped as a two-pass fallback (open, then `all` only on `None`) so it is strictly additive. Fence extension recorded in Appetite; the Rabbit Hole is retracted. See spike-9. | Adopt a terminal-aware `state="all"` resolution in `_compute_meta`. This is the fix that makes the outcome *right* rather than merely silent. Requires a fence extension to `tools/sdlc_stage_query.py` — flagged to the operator. |
| BLOCKER | Risk | "A wholly-empty ledger is already safe" is false at `decide()`. `tools/sdlc_next_skill.py:576-579` recovers an empty `stage_states` via durable signals, and `agent/pipeline_state.py:1503-1510` iterates `("open","all")`, so a merged PR sets `BUILD = completed` while `pr_number` stays None and MERGE is never written. That is the reproduction end to end, and the MERGE short-circuit is provably inert for it. | **ADDRESSED** — the "already safe" claim is explicitly retracted (Empty/Invalid Input Handling and spike-3's correction note); the recovery-path integration test is added to Test Impact and task build-red. | Replace the planned empty-`stage_states` unit assertion with an integration assertion exercising the recovery path: empty ledger + merged PR + existing branch → no `/do-build` at any row. |
| BLOCKER | History | The regression test passes when `decide()` crashes. `tools/sdlc_next_skill.py:605-610` catches every exception and returns `{"error": ..., "dispatched": False}`, which satisfies a negative-only assertion — so the mandatory red-test-first step could produce a green test before the fix. | **ADDRESSED** — a no-`error`-key requirement is now a Success Criterion, a Key Element, a Test Impact clause, and a validator check. | Pin the measured shape positively: no `error` key, plus the exact expected dispatch. |
| BLOCKER | History | The duplication rationale's first reason is factually false. `_pipeline_is_terminal`'s import of `agent/pipeline_state.py` is **function-local** (`tools/_sdlc_run_identity.py:122`); module-level imports there are stdlib-only. Importing that module never touches the off-limits one. Two spellings of the predicate already exist (`_sdlc_run_identity.py:129`, `pipeline_state.py:1101`); this plan would make three, which Development Principle 1 forbids. | **ADDRESSED, more simply** — the short-circuit is cut, so no predicate is needed and no third spelling is created. The false function-local-import rationale is explicitly retracted in Key Elements, with the delegate-don't-duplicate instruction recorded for any future need. | Add a pure `pipeline_is_terminal(states: dict) -> bool` to `tools/_sdlc_run_identity.py` and have the existing helper delegate, rather than adding a third near-homonym. |
| BLOCKER | Scope | Task `build-red` cannot pin the load-bearing term. The red test is specified with BUILD **and** MERGE completed, but after the short-circuit that state returns `{}` before reaching the BUILD branch — so the test stays green if the `not pr_number` term is reverted. | **ADDRESSED** — task build-red now specifies BUILD completed with **no** MERGE marker, and states why. The terminal shape is moot with the short-circuit cut. | The red test must set BUILD completed and **no** MERGE. Add the terminal shape as a separate case. |
| BLOCKER | Scope | The MERGE short-circuit does not close the hazard it is justified by. Its own justification (spike-4's PATCH hole) requires `pr_number` absent, but Risk 1 and spike-3 prioritize *partial* loss where MERGE is also gone — and there the short-circuit is blind. It buys partial coverage by trusting the self-attested channel G8 exists to police. | **ADDRESSED** — PATCH gets the same identifiability guard (Key Elements, Technical Approach, task build-fix), and the short-circuit is **cut** rather than demoted, for four stated reasons. | Fix PATCH's unverifiability the same way BUILD's is fixed (skip the branch probe when `pr_number` is absent, since "branch gone" is then indistinguishable from deleted-on-merge). Demote the short-circuit to an optional subprocess-avoidance nicety or cut it. |
| CONCERN | Scope | Adding `MERGE: completed` to `test_terminal_merged_pipeline_routes_to_merge_not_build` makes it vacuous: neither `gh pr view` nor `git ls-remote` is reached, the fixture becomes dead code, and the repo's only end-to-end proof that a MERGED PR is an acceptable BUILD artifact stops proving anything. | **ADDRESSED** — adding `MERGE: "completed"` to that fixture is now explicitly forbidden in both Key Elements and Test Impact, with the rename offered as the alternative. | Repair it with the recorded verdict and `headRefOid` only. Rename it if the name is the concern. |
| CONCERN | Risk | Risk 3's mitigation is false in one branch ("no `pr_number`-bearing route to `/do-merge`" holds only when `pr_number` is absent), and nothing ever resets a stage — once MERGE is completed it is permanent, so a reopened issue would run its entire second pipeline with artifact checks disarmed. | **ADDRESSED** — Risk 3 is retired (the short-circuit is cut). The reopened-issue permanence problem is recorded there as one of the disqualifying reasons rather than being accepted. | Restate as "bounded by #2003's merge predicate" and address, or explicitly accept, the reopened-issue case. |
| CONCERN | Risk, History | The MERGE-marker trust argument cites a MERGE-specific property that does not exist. The real gate is generic: `_backfill_predecessors` plus the #2554 verdict invariant (`tools/sdlc_stage_marker.py:744-766`), reinforced by MERGE being non-skippable (`:328-336`). | **ADDRESSED** — Risk 3 now cites `_backfill_predecessors` + the #2554 verdict invariant (`tools/sdlc_stage_marker.py:744-766`) and MERGE non-skippability (`:328-336`), and marks the earlier MERGE-specific claim as nonexistent. | Cite the actual mechanism by file:line. |
| CONCERN | Risk, History | `Blocked(NO_RULE)` is a human-escalation wedge, not a quiet no-op: the router documents it as "a genuine hole in the table" and the SDLC skill requires surfacing it and waiting. Under the `_lookup_pr` finding this state is routine post-merge, not rare. | **ADDRESSED — moot.** spike-10 measured `/do-merge` row 10 under both branch states once `pr_number` resolves. The escalation-wedge nature of `Blocked(NO_RULE)` is recorded in spike-1's correction and the No-Go entry; #2817 moves to close-with-evidence. | Moot if `pr_number` is restored. If any residual remains, state it as shipped user-visible behavior in Success Criteria. |
| CONCERN | History | #2718 and #2735 are the same commit (`e50eba258`), called "irrelevant to the root cause" in Freshness and "the closest prior fix — Succeeded" in Prior Art. The mirrored template is one day old with two unit tests of coverage. | **ADDRESSED** — Prior Art now states both are the same commit, separates "irrelevant to the cause" from "relevant as a template", and downgrades the confidence claim (one day old, two unit tests). | Reconcile the two paragraphs and downgrade the confidence claim. |
| CONCERN | History, Scope | The PATCH↔BUILD analogy is not the same shape: #2718 skipped a *guessed* identifier (skipping lost nothing), while an absent `pr_number` is a real coverage loss. Risk 2 concedes this; the Problem section papers over it. Relatedly, the Rabbit Hole "confirm by reading, then stop" contradicts spike-4. | **ADDRESSED** — the Problem section carries two explicit caveats naming the real coverage loss and the #2718 disanalogy; the Rabbit Hole is rewritten to say PATCH needed a *second* guard. | Make the Problem section say what Risk 2 says, and reconcile the Rabbit Hole. |
| CONCERN | History | Missing prior art: #2091 (`docs/plans/completed/fix-sdlc-router-merge-termination.md`) already adjudicated this exact stale-fixture class and produced a guardrail at `docs/sdlc/do-test.md:156-166` scoped to `tests/unit/test_sdlc_router*.py` — which is why the integration fixture escaped it. | **ADDRESSED** — #2091 added to Prior Art as "succeeded but under-scoped"; widening `docs/sdlc/do-test.md:156-166` to integration fixtures is now a Documentation checkbox and a task bullet. | Cite it, and widen that note's scope to integration fixtures as part of Documentation. |
| CONCERN | History | A red integration test has been sitting on `main` under default collection (`pyproject.toml:155` `testpaths = ["tests"]`). Worth its own issue, and a sanity check on spike-5. | **ADDRESSED** — filing the gate-gap issue is a task build-tests-docs bullet; confirming the clean-checkout red is the first bullet of task build-red. | File the gate-gap issue; confirm the red reproduces in a clean checkout before Task 0. |
| CONCERN | Risk, History | The fence-verification row diffs against `origin/main`, so it fails whenever main moves and touches those files (#2802 is actively merging one of them) or passes vacuously against a stale ref. | **ADDRESSED** — the Verification row now fetches explicitly then diffs against `git merge-base`, with the rationale in the notes below the table. | Use `git diff --quiet "$(git merge-base origin/main HEAD)" -- ...` after an explicit fetch. |
| CONCERN | Risk, History | Both anti-criterion grep rows are unsound. `! grep -q 'not pr_number'` is evaded by `if pr_number is None or ...`; `! grep -rq 'xfail'` trips on the plan's own mandated docstring sentence about nothing being left xfail-ed. | **ADDRESSED** — the xfail row is anchored on `pytest.mark.xfail`/`pytest.xfail(`; the `not pr_number` grep is labelled advisory with a behavioral test promoted above it. Both rationales are in the notes below the table. | Anchor on `pytest.mark.xfail`/`pytest.xfail(`, and promote the behavioral `assert_not_called()` test to the load-bearing gate with the string grep demoted to advisory. |
| CONCERN | Scope | Six tasks and three agents for roughly ten lines plus tests; tasks 3 and 5 are the same work run twice. | **ADDRESSED** — exactly that: builder + validator, with validation collapsed to a single pass (build-red / build-fix / build-tests-docs / validate-all). The documentarian folds into the builder. | Collapse to two agents and three tasks. |
| NIT | Risk, Scope, History | Log-level assertions need `caplog.set_level(logging.DEBUG)` or they pass vacuously. Re-asserting the existing warning's level pins a log level as an interface. `_pipeline_is_terminal_from_states` contains `_pipeline_is_terminal` as a substring, making cross-reference greps ambiguous. `docs/features/sdlc-router-oscillation-guard.md:133` still describes the PLAN check by slug, false post-#2792, in the same table the documentarian edits. spike-3 over-reads a now-empty ledger as testimony about its state a day earlier. | **ADDRESSED** — `caplog.set_level(logging.DEBUG)` mandated and the existing-warning re-assertion dropped (Error State Rendering); the near-homonym is gone with the helper; `sdlc-router-oscillation-guard.md:133`'s stale slug wording added to Documentation; spike-3's over-read of the empty ledger flagged in its correction note. | Address inline during revision. |

