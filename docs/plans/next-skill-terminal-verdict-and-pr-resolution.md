---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-19
tracking: https://github.com/tomcounsell/ai/issues/2817
last_comment_id: 5324622874
revision_applied: true
revision_applied_at: 2026-08-19T05:45:10Z
---

# Terminal Verdict and PR Resolution: the three stage-marker residuals left outside #2826's fence

Covers **#2817**, **#2824**, and **#2825** as one lane in **one PR**. They share four
files — `tools/sdlc_stage_query.py`, `tools/sdlc_stage_marker.py`,
`tools/sdlc_next_skill.py`, `agent/sdlc_router.py` — and, more importantly, they share a
single root cause that none of them names on its own (see **Why Previous Fixes Failed**).

## Lane Size and the One-PR Decision

The critique asked whether four workstreams across three issues is too much for one PR.
It was, and the answer was to **cut a workstream, not to split the PR**.

**WS-D (deterministic candidate ordering) is deferred to [#2868](https://github.com/tomcounsell/ai/issues/2868)**
— its headline rule was falsified by the critique (detail in *Resolved Questions* Q3).
That removes the largest and least-grounded diff, the WS-B ≤ WS-D sequencing constraint,
and the whole of Risk 3.

What remains is three workstreams landing as **one PR**:

| | Files touched | Rough size |
|---|---|---|
| **WS-A** (#2817) | `agent/sdlc_router.py`, `tools/sdlc_next_skill.py` | one guard + one JSON shape |
| **WS-B** (#2824) | `tools/sdlc_stage_query.py` | ~10 lines in `_lookup_pr` |
| **WS-C** (#2825) | `tools/sdlc_stage_marker.py` | one argument |

The two halves are **file-disjoint** — WS-A touches nothing WS-B/C touch, and vice versa
— so a reviewer reads two independent diffs rather than one entangled one, and each
workstream reverts alone (the mutation table in Risk 5 enforces that each has its own
coverage). Splitting file-disjoint halves into two PRs would buy the reviewer nothing and
cost a second lane's worth of pipeline machinery. One PR, closing all three issues.

## Problem

Three residuals were deliberately left outside the fence of PR #2826 ("G8 must not
rebuild shipped work", merged 2026-08-17). Each was filed with an explicit
"measure before changing anything" instruction. All three measurements are now
done, and all three shapes **reproduce live on this repo today**.

**Current behavior:**

1. **#2817 — a finished pipeline has no way to say so.** `agent/sdlc_router.py`
   never reads `stage_states["MERGE"]`. Not one rule, not one guard. `grep -n MERGE
   agent/sdlc_router.py` returns only the `SKILL_DO_MERGE` constant and its three
   dispatch uses. So "this pipeline is finished" is not a state the dispatch table
   can express, and a terminal ledger falls into whichever row happens to match its
   residual fields.

2. **#2824 — a reopened issue inherits its previous life's PR.** `_lookup_pr`'s
   fuzzy `#N` body search finds the *prior* lifecycle's merged PR, whose body still
   reads `Closes #N`. That truthy `pr_number` disarms `_rule_branch_exists_no_pr`
   (row 5) — a falsy→truthy flip of a router input. Live on #2494 and #2518 right now.

3. **#2825 — a review gate reads its evidence off an abandoned PR.**
   `tools/sdlc_stage_marker.py:263` passes `state="all"`, so `_review_artifact_posted`
   can select a closed-unmerged PR and return `True` from a review that belongs to
   work nobody merged. Live on #1785, #2073, and #2104.

**Desired outcome:**

A terminal pipeline reports itself terminal instead of being rebuilt, re-reviewed,
or escalated. PR resolution answers with the PR belonging to *this* lifecycle, or
answers `None` — never with a confident wrong number.

## Freshness Check

**Baseline commit:** `f491306c5`
**Issues filed at:** #2817 2026-08-14T03:54:01Z · #2824 2026-08-16T05:15:05Z · #2825 2026-08-16T05:15:36Z
**Disposition:** **Minor drift** — one issue's premise changed shape (expected and predicted), two cited line refs moved, all claims still hold at corrected locations.

**File:line references re-verified:**

| Cited | Claim | Status |
|---|---|---|
| `agent/sdlc_router.py:922-927` (#2824) | `_rule_branch_exists_no_pr` fires only when `pr_number` is falsy | **Drifted** → now `1051-1056`. Claim holds verbatim at the new location. |
| `agent/sdlc_router.py:1470-1471` (#2824) | `_rule_ready_to_merge` reads the same value | **Drifted** → now `1653-1669`. Claim holds. |
| `tools/sdlc_stage_marker.py:263` (#2825) | `pr_number = pr or _lookup_pr(issue_number, repo=target_repo, state="all")` | **Byte-exact.** Unchanged. |
| `tools/sdlc_stage_query.py:284-335` (#2825) | `_gh_pr_search_issue_ref` returns the first body-validating candidate | Holds. |
| `agent/sdlc_router.py` row 10 / G6 gated on `pr_number` (#2817) | Both return False/None without it | Holds (`1653`, `769`). |

Both drifts are attributable to `839d70a5f` (#2830, rows 4b/4c), which inserted rules above them.

**Cited sibling issues/PRs re-checked:**

- **#2757** — CLOSED, by PR **#2826** merged 2026-08-17T05:03:08Z (`838182c1`). Read in full; its "Follow-ups filed" section filed #2824 and #2825, and its closing paragraph predicted #2817 would change shape. It did — see below.
- **#2539** — the precedent that made `state="all"` deliberate at the `_review_artifact_posted` site. Its property (a merged PR must still resolve) is an acceptance bar for this work, not an obstacle.
- **#2823** — sibling follow-up from the same PR (CI/collection gap). Not in this lane's scope, no code overlap.

**Commits on main since the issues were filed (touching referenced files):**

| Commit | Effect on this lane |
|---|---|
| `838182c16` #2826 | **Changed the root cause of #2817.** The two-pass `_lookup_pr` restores `pr_number` for merged PRs, moving most terminal ledgers off `NO_RULE`. Re-measured below. |
| `f491306c5` | Added artifact leg 2b (GraphQL fallback) to `_review_artifact_posted`. **Did not touch** the `state="all"` argument, so #2825 is untouched — but the fail-open now has one more way to return True. |
| `839d70a5f` #2830 | Rows 4b/4c; caused the two line-ref drifts above. No behavioral overlap. |
| `e2d3cf209` #2818 | `next-skill` run identity. Touches `decide()`'s preamble, not its router call. |
| `4f778447f` #2802 | Router upsert slot for dispatch records. Adjacent, not overlapping. |

**Bug reproduction against current main:** all three reproduce. Full evidence in **Spike Results**; each issue's `## Recon Summary` carries the same measurements.

**Active plans in `docs/plans/` overlapping this area:** none blocking.
`gates-that-cannot-fire.md` (status Planning, #2658) and
`pipeline-graph-single-source-of-truth.md` (status Planning, #2491) both name
`sdlc_router.py` but neither is in flight and neither touches these call sites.
`router-docs-skip-trivial.md` is status Ready against a different row. Coordination
signal only.

**Notes:** #2817's shape change was predicted by #2826 and is the reason this lane
was told to re-measure before designing. The prediction turned out to be **half
right**, and the half it missed is the more serious one. Detail in Spike Results.

## Prior Art

- **#2757 / PR #2826** — "G8 must not rebuild shipped work." Fixed the reported
  bug by making `_compute_meta` retry `_lookup_pr` with `state="merged"`, and by
  splitting artifact verification into verified / falsified / **unverifiable**.
  Succeeded. Deliberately left all three of this lane's issues unfixed, with
  written rationale for each. This lane is its declared continuation.
- **#2539** — established `state="all"` at the `_review_artifact_posted` site so
  that a merged PR's review artifact stays findable after the merge. Succeeded at
  its goal; the `all` scope is the part that has since proven too wide.
- **#1058 / `agent/pipeline_complete.py`** — introduced `is_pipeline_complete`, a
  pure terminal predicate keyed on `psm.states`. Succeeded, and is used today by
  `agent/session_runner/completion_guard.py`. **The router has never consulted it.**
  This is the single most important prior-art finding in this plan.
- **#2062 (WS3a/WS3d)** — made row 10 require a recorded APPROVED verdict and a
  non-stale head. Succeeded; it is why the terminal matrix below splits on verdict.
- **#1267 / G8** — artifact verification guard, positioned deliberately after G4 so
  G4 bounds its re-dispatch loop. Its positioning rationale is the template for
  where the new terminal guard goes.
- **#2036 (PR)** — "Fix merge-predicate tracked-issue resolution: key on
  `PipelineLedger.pr_number`." Prior instance of the same class of bug: a PR
  identifier resolved by inference rather than by record.
- **#1642** — `pr_merge_state` resolving UNKNOWN without `GH_REPO`, silently
  disabling G6. Same failure signature: a lookup that fails soft and disarms a gate.

## Research

**Queries used:**
- `gh pr list --search results ordering guarantee best-match GitHub CLI sort`

**Key findings:**

- **`gh pr list --search` returns results ranked by best-match relevance, with no
  ordering guarantee.** Passing `--search` routes the request through GitHub's
  search API rather than the plain list endpoint. `gh pr list` has no `--sort` flag
  at all — that is still an open feature request against the CLI
  ([cli/cli#10244](https://github.com/cli/cli/issues/10244)), whose reporter
  describes exactly this pain: results are not ordered by the field you filtered on,
  which makes pagination and any "take the first one" logic unreliable.
  ([gh pr list manual](https://cli.github.com/manual/gh_pr_list))
- **The deterministic alternatives are explicit.** Embed a `sort:` qualifier inside
  the search string, switch to `gh search prs` (which does expose `--sort` with a
  documented `best-match` default and `--order`), or take `--json` output and sort
  client-side. Only the client-side sort is a hard guarantee.
  ([gh search prs manual](https://cli.github.com/manual/gh_search_prs))

**How this informs the technical approach:** it converts a suspicion into a fact.
`_gh_pr_search_issue_ref` documents its own behavior as "returns the FIRST
body-validating candidate with no MERGED-over-CLOSED preference" — and that "first"
is drawn from an ordering GitHub does not guarantee and the CLI cannot pin. The
measurement in Spike Results found 6 abandoned PRs carrying review artifacts of
which only 3 are currently selected; the other 3 are masked **by ordering luck that
no code controls and no test pins**. Any fix that only narrows `state` leaves that
non-determinism in place.

**Amended at critique.** This finding was the original justification for WS-D, and it does
not survive scrutiny: the "6 abandoned PRs" population is entirely **closed-unmerged**, and
WS-C's `state="merged"` excludes that population by construction rather than by ordering.
`state` threads into `gh pr list --state {state}`, so candidates in any single call share
one state and a MERGED-first comparator can never separate them. The research finding
remains true and remains useful — it just supports a **recency** tiebreak among same-state
candidates, which is now [#2868](https://github.com/tomcounsell/ai/issues/2868) rather than
WS-D of this lane. See *Resolved Questions* Q3.

Sources: [gh pr list](https://cli.github.com/manual/gh_pr_list) · [cli/cli#10244](https://github.com/cli/cli/issues/10244) · [gh search prs](https://cli.github.com/manual/gh_search_prs)

## Spike Results

All four spikes are **code-read + live-measurement** against `main` at `f491306c5`.
No prototypes, no worktrees, no committed code.

### spike-1: Does #2817's residual still exist post-#2826, and in what shape?
- **Assumption**: "#2826 converted `Blocked(NO_RULE)` into an un-terminated `/do-merge` loop."
- **Method**: code-read + direct calls to `agent.sdlc_router.decide_next_dispatch` with `{ISSUE..DOCS, PATCH, MERGE} = completed`.
- **Finding**: **Half right, and the missing half is worse.** Full matrix:

  | `pr_number` | `branch_exists` | APPROVED verdict | result |
  |---|---|---|---|
  | absent | False | yes | `Blocked(NO_RULE)` — **the originally filed shape, still live** |
  | absent | False | no | `Blocked(NO_RULE)` |
  | absent | True | yes | **`/do-build` row 5 — a false rebuild of shipped work** |
  | absent | True | no | **`/do-build` row 5** |
  | 555 | False | yes | `/do-merge` row 10 |
  | 555 | False | no | `/do-pr-review` row 8e |
  | 555 | True | yes | `/do-merge` row 10 |
  | 555 | True | no | `/do-pr-review` row 8e |

  **Ninth cell — an unsettled PATCH under a completed MERGE** (added at critique; the
  eight cells above all hold `PATCH = completed` and so never exercised it). `MERGE`
  completion does not force PATCH to settle: `_backfill_predecessors` settles on-spine
  predecessors but **explicitly exempts PATCH** as off-spine, so this ledger is reachable.
  Measured on `f491306c5`:

  | ledger | result |
  |---|---|
  | all completed except `PATCH='pending'`, `pr_number=555`, APPROVED verdict | `/do-merge` row 10 |
  | all completed except `PATCH='pending'`, `pr_number=555`, no verdict | `/do-pr-review` row 8e |
  | all completed except `PATCH='failed'`, `pr_number=555`, no verdict | `/do-pr-review` row 8e |

  The guard **deliberately terminates all three.** `is_pipeline_complete` keys on
  `MERGE == "completed"` alone — measured: `is_pipeline_complete({...PATCH:'pending'},
  "success", pr_open=None)` → `(True, "merge_success")`. That is the correct reading: once
  the merge landed, a pending off-spine PATCH is a residual field, not outstanding work.
  This is a deliberate settling decision, not an oversight, and Risk 1 carries the
  assertion that pins it.

  Three corrections to the record: (a) `Blocked(NO_RULE)` **survives #2826 unchanged**
  whenever `pr_number` is genuinely unrecoverable, so #2817 is not obsolete;
  (b) #2826's "row 10 under both `branch_exists` values" holds **only when an APPROVED
  REVIEW verdict is recorded** — without one, row 8e re-dispatches `/do-pr-review`
  against an already-merged PR, a cell #2826 did not name; and (c) with `branch_exists=True`
  and no `pr_number`, **row 5 dispatches `/do-build` on shipped work.** #2826 fenced out
  G8's false rebuild and row 5's identical false rebuild is still live.
- **Confidence**: high.
- **Impact on plan**: #2817 is not a cosmetic reason-string fix. It has an active
  failure mode in two of four `pr_number`-absent cells. It also cannot be fixed by a
  dispatch row — rows 5 and 8e are registered earlier and evaluated first, so the
  terminal state must pre-empt the whole table. It must be a **guard**.

### spike-2: Does G4 actually cap the terminal `/do-merge` loop?
- **Assumption**: "the lane re-dispatches until `guard_g4_oscillation` caps it."
- **Method**: code-read + parametrized `decide_next_dispatch` calls.
- **Finding**: **Yes, but only through the meta counter.** `MAX_SAME_STAGE_DISPATCHES = 3`.
  G4 reads `meta["same_stage_dispatch_count"]`, **not** `stage_states["_sdlc_dispatches"]`:
  a ledger carrying ten `/do-merge` entries in `_sdlc_dispatches` still returns row 10
  on every tick, while `same_stage_dispatch_count >= 3` returns `Blocked(G4)`.
- **Confidence**: high.
- **Impact on plan**: the escalation does arrive, so the terminal residual costs
  three wasted `/do-merge` ticks and then a human escalation whose suggested remedy
  (`sdlc-tool dispatch reset`) is the wrong advice for a finished pipeline. Confirms
  the severity is "wasted cycles + a misleading escalation", not "infinite loop".
- **Corroboration from the issue thread** (#2817 comment `5311386590`, 2026-08-17):
  each of those three ticks is a **no-op** rather than a destructive re-merge, because
  `/do-merge` fails closed on `state != "OPEN"`
  (`.claude/skills-global/do-merge/SKILL.md:99`). That comment also records the live
  confirmation `--issue-number 2755` → `{"skill": "/do-merge", "row_id": "10",
  "dispatched": true}`. Independent of this plan's synthetic matrix and in agreement
  with it — which is why the severity here is wasted cycles and a misleading
  escalation rather than data loss.

### spike-3: Is there already a terminal predicate in this codebase?
- **Assumption**: "#2817 needs a new terminal predicate designed from scratch."
- **Method**: code-read (`grep -rn "def is_pipeline_complete"`).
- **Finding**: **Falsified — one already exists and is battle-tested.**
  `agent/pipeline_complete.py::is_pipeline_complete(psm_states, outcome, pr_open)`
  (issue #1058) returns `(True, "merge_success")` on `MERGE == completed`, is pure,
  performs no I/O, has a dedicated unit-test module
  (`tests/unit/test_pipeline_complete_predicate.py`), and is consumed today by
  `agent/session_runner/completion_guard.py:155`. Its own docstring explains it is
  keyed on `states` rather than `current_stage()` **precisely because** a predicate
  keyed on `current_stage` would return False exactly when the pipeline just finished.
  The router has simply never consulted it.

  It also already handles a second terminal path the router knows nothing about:
  `(True, "docs_success_no_pr")` for DOCS-completed lanes with no open PR — docs-only
  changes and plan PRs.
- **Confidence**: high.
- **Impact on plan**: **this reshapes WS-A entirely.** The work is not "invent a
  terminal verdict", it is "route the router through the terminal predicate the repo
  already agreed on." That removes the risk of two divergent definitions of "terminal"
  and shrinks the change substantially. Import direction is legal:
  `tests/unit/test_architectural_constraints.py` forbids `agent/sdlc_router.py` from
  importing **`tools/`**, and `agent/pipeline_complete.py` is in `agent/`.

### spike-4: Do #2824 and #2825 reproduce, and does each issue's proposed fix work?
- **Assumption**: "both reproduce; both proposed fixes are correct as written."
- **Method**: GraphQL sweep of all 1753 issues for `REOPENED_EVENT`; live calls to the real `_lookup_pr`, `_gh_pr_list`, and `_review_artifact_posted`; a monkeypatched-scope counterfactual against live GitHub.
- **Finding**:

  **#2824 reproduces; its proposed fix as literally worded does not work.**
  17 issues carry a reopen event (0.97% of 1753); 8 have a body-validating merged PR
  that merged *before* the reopen; **2 are OPEN today** — #2494 (`_lookup_pr(merged)` →
  **2516**, merged 19 seconds before the reopen) and #2518 (→ **2538**, the *first* of
  two lifecycles, not even the most recent). Row 5 is confirmed disarmed. But the
  predicted `Blocked(NO_RULE)` is wrong: measured with `{ISSUE,PLAN,CRITIQUE: completed,
  BUILD: in_progress}` and `branch_exists=True`, the stale number routes to
  **`/do-pr-review` row 7** — the lane is sent to review a merged, finished PR.
  And a plain reorder does not help: `_gh_pr_list(["--head","session/sdlc-2494","--state","merged"])`
  → `None`, same for #2518, so the fuzzy leg still runs and still returns the stale PR.
  The fix only works with the issue's *second* clause — suppress the fuzzy leg when a
  lane branch is recorded — and **even then both live candidates stay unfixed**, because
  `sdlc-tool stage-query` reports `slug: null, slug_source: "unresolved"` for both.

  **#2825 reproduces, and `state="merged"` is a sufficient and safe fix.**
  `_review_artifact_posted(1785, "tomcounsell/ai") = True` and
  `_review_artifact_posted(2073, "tomcounsell/ai") = True` — **neither issue has a
  merged PR at all**; both artifacts are read off abandoned closed PRs 1793 and 2075.
  #2104 returns `True` off abandoned 2110 while merged 2109 exists. Population: 22
  closed-unmerged PRs, 15 with a closing ref, **6 carrying a real artifact**, 3 currently
  selected. Counterfactual under `merged`: #1785 → `False`, #2073 → `False`, #2104 →
  **still `True`** but now via merged PR 2109's own two `## Review:` comments.
  **#2539 regression control 5/5 green** (#2860/2861, #2831/2838, #2716/2835,
  #2734/2844, #2741/2842 all still resolve and still return `True`).

  **Blast radius of `_lookup_pr` is two production call sites**, not the wide surface
  #2824 feared: `tools/sdlc_stage_query.py:596,598` and `tools/sdlc_stage_marker.py:263`.
  Everything else in the tree is a test patch point.

  **Incidental:** PR 2542 body-validates for #2518 via a *retrospective prose* sentence
  ("...which is what happened when PR #2538 closed #2518") while actually declaring
  `Closes #2547`. `_body_references_issue` cannot tell a closing directive from a
  narrative mention.
- **Confidence**: high on every number above; all are live calls, not reasoning.
- **Impact on plan**: #2825 shrinks to a one-argument change with a measured control.
  #2824 grows a scope caveat that must be stated rather than papered over. And the
  6-carrying-artifact / 3-selected gap motivated WS-D, which the critique then falsified
  as a same-state comparator — it is now [#2868](https://github.com/tomcounsell/ai/issues/2868).

## Data Flow

The path a `pr_number` takes from GitHub to a routing decision, with the three
defects marked:

1. **Entry point**: `sdlc-tool next-skill --issue-number N` → `tools/sdlc_next_skill.py::decide()`.
2. **Context assembly**: `decide()` runs the issue-lock peek, then resolves enriched
   context, which calls `tools/sdlc_stage_query.py::_compute_meta`.
3. **PR resolution** (`_compute_meta`, `:596-598`): `_lookup_pr(issue, slug, repo)` →
   `_gh_pr_search_issue_ref` (fuzzy `#N`, body-validated) → falls back to `_gh_pr_list --head <lane branch>`.
   On `None`, a second pass repeats with `state="merged"` (#2826).
   → **DEFECT #2824**: on a reopened issue the fuzzy leg returns the prior lifecycle's PR.
   → **DEFECT (root)**: "first body-validating candidate" is drawn from GitHub best-match order, which is not a guarantee.
4. **Artifact verification**: `_verify_stage_artifacts_live` reads the resolved
   `pr_number`, sets `context["stage_artifacts_verified"]` / `unverified_stage`.
5. **Routing**: `agent/sdlc_router.py::decide_next_dispatch(stage_states, meta, context)`
   runs `GUARDS` in order, then walks `DISPATCH_RULES` top to bottom, first match wins.
   → **DEFECT #2817**: nothing in either list reads `stage_states["MERGE"]`, so a
   finished pipeline is adjudicated by rows 5 / 7 / 8e / 10 on its residual fields.
6. **Output**: a `Dispatch` (skill + reason + row_id) or a `Blocked` (reason + guard_id),
   serialized to JSON by `decide()`.

A parallel, independent path reaches the same lookup:

1. **Entry point**: `sdlc-tool stage-marker --stage REVIEW --status completed`.
2. **Gate**: `tools/sdlc_stage_marker.py:729` → `_review_artifact_posted(issue, repo, pr=pr)`.
3. **PR resolution** (`:263`): `pr or _lookup_pr(issue, repo, state="all")`.
   → **DEFECT #2825**: `all` admits closed-unmerged candidates.
4. **Artifact probe**: three legs OR'd, first True wins — formal reviews (`:271-281`,
   any non-empty `state`), REST issue-comments `startswith("## Review:")` (`:293-308`),
   GraphQL comments fallback (`:318-328`, added today by `f491306c5`).
5. **Output**: `True` admits the REVIEW completion marker; `False` refuses it and the
   WS3b recovery row re-dispatches `/do-pr-review`.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|---|---|---|
| PR #2826 (#2757) | Two-pass `_lookup_pr` (`open` then `merged`); three-state artifact verification | Restored the *identifier* without giving the router any way to recognize that the pipeline the identifier belongs to is **finished**. It moved the terminal ledger from one wrong row (G8 `/do-build`) to another (row 10 `/do-merge`, or row 8e `/do-pr-review`, or — when `pr_number` stays unrecoverable and a branch exists — row 5 `/do-build` again). Its own body predicted the residual and asked for it to be recorded. |
| #2539 | Widened the review-artifact probe to `state="all"` so a merged PR stays findable | Correctly identified that `open` was too narrow; overshot to `all`, which admits closed-unmerged candidates. The right scope, `merged`, satisfies the original goal exactly (measured 5/5 above). |
| PR #2036 | Keyed merge-predicate resolution on `PipelineLedger.pr_number` | Same class, different call site: fixed one consumer of an inferred PR identity rather than the inference itself. |
| #1642 | Repaired `pr_merge_state` resolving UNKNOWN without `GH_REPO` | Same signature: a soft-failing lookup silently disarmed a gate (G6). Fixed the lookup, not the "a lookup that fails soft disarms a gate" pattern. |
| #1058 | Built `is_pipeline_complete`, a correct terminal predicate | Wired it into the *session-completion* path only. The **router** — the component that most needs to know a pipeline is terminal — was never connected to it, so #2817 is a wiring gap, not a missing concept. |

**Root cause pattern:** *this system repeatedly infers a PR's identity, and its
pipeline's finishedness, from fuzzy external search rather than reading what it
recorded.* Every fix so far has widened, narrowed, or retried the inference. None
has made the inference deterministic, and none has given the router the recorded
terminal fact it already computes elsewhere. That is why the same bug keeps
arriving through a different row.

## Architectural Impact

- **New dependencies**: none external. One new *internal* import,
  `agent/sdlc_router.py` → `agent/pipeline_complete.py`. Legal:
  `tests/unit/test_architectural_constraints.py` forbids only `agent/sdlc_router.py`
  importing from **`tools/`**. A verification row pins this so the constraint is not
  quietly widened.
- **Interface changes**: `_lookup_pr`'s resolution *order* changes when a lane branch
  is recorded (WS-B). Its signature does not. `_gh_pr_search_issue_ref` is **untouched**
  by this lane (WS-D deferred to #2868). `decide()`'s JSON gains one additive `complete`
  key on the terminal shape (WS-A); existing keys keep their meaning. The one external
  contract change is `.claude/skills-global/do-sdlc/SKILL.md`'s reading of that key.
- **Coupling**: **decreases.** The router stops carrying an implicit, unstated
  definition of "not finished" (the absence of any MERGE-aware row) and starts
  consuming the explicit shared predicate. Two definitions of terminal collapse into one.
- **Data ownership**: unchanged. No new persisted state, no ledger fields, no Popoto
  models, therefore **no migration**.
- **Reversibility**: high. Each workstream is a small, independently revertible diff,
  and the demonstrated-red table below names exactly which tests re-red on each revert.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 remaining. Both former alignment points are settled in *Resolved Questions*: WS-D is deferred to #2868, and the WS-B coverage caveat is now a Success Criterion.
- Review rounds: 2

The measurement work that usually dominates an appetite like this is **already
done** — it is in Spike Results and on all three issues. What remains is four
bounded diffs plus their tests.

## Prerequisites

| Requirement | Check Command | Purpose |
|---|---|---|
| `gh` authenticated | `gh auth status` | Every measurement and verification row shells out to `gh` |
| Repo resolvable | `gh repo view --json nameWithOwner -q .nameWithOwner` | `_lookup_pr` / `_review_artifact_posted` need a repo slug |
| Venv on the pinned interpreter | `./.venv/bin/python -c "import sys,pathlib; pin=pathlib.Path('.python-version').read_text().strip(); assert '.'.join(map(str,sys.version_info[:2])) in pin, (sys.version, pin)"` | `scripts/pytest-clean.sh` aborts on an off-pin venv, so every test row depends on this |
| Live fixture issues still in shape | `./.venv/bin/python -c "from tools.sdlc_stage_query import _lookup_pr; assert _lookup_pr(2494, repo='tomcounsell/ai', state='merged') == 2516"` | The #2824 live reproduction must still reproduce at build time |

## Solution

### Key Elements

- **WS-A — Terminal guard (#2817).** Give the router the terminal fact it already
  computes elsewhere, by consulting `agent/pipeline_complete.py::is_pipeline_complete`
  rather than inventing a second definition of "finished".
- **WS-B — Lane-branch authority (#2824).** When a lane branch is recorded, the exact
  head-ref match is authoritative: it answers, or the lookup answers `None`. The fuzzy
  body search runs only when there is no recorded branch to disambiguate with.
- **WS-C — Review-probe scope (#2825).** `state="all"` → `state="merged"` at the one
  call site, preserving #2539.
- **WS-D — Deterministic candidate selection.** **Deferred to
  [#2868](https://github.com/tomcounsell/ai/issues/2868).** Its headline rule (MERGED
  before anything else) is unreachable once WS-C lands, and the surviving recency half has
  no measured live failure. See *Resolved Questions* Q3.

### Flow

Terminal-pipeline tick, today versus after:

**Today:** `next-skill` → context assembly resolves `pr_number` → router walks guards
(no MERGE awareness) → walks rows → **row 5 `/do-build`** (rebuild shipped work) *or*
**row 8e `/do-pr-review`** (review a merged PR) *or* **row 10 `/do-merge`** ×3 →
**`Blocked(G4)`** telling a human to run `dispatch reset` on a pipeline that is simply done.

**After:** `next-skill` → context assembly → router evaluates the terminal guard
**first** → `MERGE == completed` → **terminal verdict: "pipeline complete, nothing to
dispatch"** → supervisor stops. No rebuild, no re-review, no oscillation, no misleading
escalation.

Reopened-issue tick:

**Today:** `_lookup_pr(2494)` → fuzzy `#2494` → prior lifecycle's merged PR **2516** →
row 5 disarmed → **row 7 `/do-pr-review`** on a finished PR.

**After (with a recorded slug):** `_lookup_pr(2494, slug=...)` → head-ref leg on
`session/{slug}` is authoritative → no PR on this lane's branch → **`None`** → row 5
fires → `/do-build` creates the PR this lifecycle actually needs.

### Technical Approach

**WS-A — where the terminal verdict lives, and why.**

*It must be a guard, not a dispatch row.* Rows 5, 7, and 8e are registered before
row 10 in `DISPATCH_RULES` and the table is first-match-wins, so a terminal row
appended anywhere below them would never be reached on the exact ledgers that
matter (spike-1's matrix proves rows 5 and 8e win today). Guards run before the
table and can pre-empt all of it.

*It must not duplicate the terminal definition.* `agent/pipeline_complete.py::is_pipeline_complete`
already owns it, already handles the `docs_success_no_pr` path the router does not
know about, and already has its own test module. The guard delegates to it. Note
its `outcome` parameter: the router has no per-transition outcome, so pass
`"success"` — the router is asking a state question, not judging a transition, and
`completion_guard.py:155` already calls it exactly this way. The `pr_open` argument
stays `None` from the router (pure, no I/O), which makes the `docs_success_no_pr`
path return `(False, "pr_state_unavailable")` and fall through to the table
unchanged. That is deliberately conservative: **WS-A ships only the
`merge_success` leg**, and the DOCS-terminal leg is a No-Go.

*It must type-check its input before delegating.* `evaluate_guards` (`:830-839`) walks
`GUARDS` with a bare `result = guard(stage_states, meta, ctx)` and **no** try/except —
only *rule* predicates are wrapped, and that wrapping lives inside `decide_next_dispatch`,
not here. Placing this guard first makes it the most exposed callable in the router.
`is_pipeline_complete` opens with `psm_states.get("MERGE")` (`agent/pipeline_complete.py:69`),
which raises on a non-dict — measured: `None`, `"x"`, `42`, and `[]` each raise
`AttributeError: ... has no attribute 'get'`. A raise there escapes
`decide_next_dispatch` entirely and surfaces as `decide()`'s `{"error": ...}` shape,
which is strictly worse than today's `Blocked(NO_RULE)`.

So the guard body **opens with a type check and returns `None`**:

```python
if not isinstance(stage_states, dict):
    return None
```

`None`, never `Blocked` — an unreadable ledger must fall through to the dispatch table,
not terminate a lane. This resolves the contradiction the critique found between task 3's
bare-delegation spec and the Failure Path section's "guard cannot raise" test.

*Guard ordering — resolved.* **First in `GUARDS`, ahead of G1.** A completed pipeline has
nothing to gain from any other guard's opinion, and G4 in particular gives actively wrong
advice on it ("clear it with `sdlc-tool dispatch reset`"). The competing argument was
#1267's precedent that escalation guards take priority; it does not reach this case,
because G4 exists to bound a *loop* and terminating before the loop begins is strictly
better than capping it at three. See *Resolved Questions* Q1.

*Return type — resolved.* Return `Blocked(reason=..., guard_id="TERMINAL")` and have
`tools/sdlc_next_skill.py::decide()` map that `guard_id` to a JSON shape carrying an
explicit `complete: true` key alongside the existing `blocked` key. No consumer of
`decide_next_dispatch` breaks, and a supervisor can distinguish "finished" from
"escalate" on one key. The third-`Complete`-dataclass alternative stays a Rabbit Hole.
See *Resolved Questions* Q2.

*The supervisor must learn the new shape in the same PR.* Today
`.claude/skills-global/do-sdlc/SKILL.md:136` reads: "`{"blocked": true, ...}` (other
reasons) → **STOP the loop.** Report the `reason` and `guard_id` to the human." Until
that line learns `complete: true`, WS-A converts "three wasted `/do-merge` ticks then a
misleading escalation" into "an immediate escalation" — a regression in human-noise terms
even though the routing is correct. `do-sdlc` is a **global** skill hardlinked to every
machine by `scripts/update/hardlinks.py`, and its body already documents the `blocked` /
`guard_id` contract generically, so the terminal shape belongs in the **global body**
too — no `.claude/skill-context/do-sdlc.md` is needed, and none exists today. Carried as
a Documentation checkbox and a build task, not left to review.

*The false rebuild is fixed as a consequence.* With the guard first, the
`pr_number`-absent / `branch_exists=True` cells never reach row 5. Both `/do-build`
cells in spike-1's matrix become terminal. No change to row 5 itself is needed —
and none should be made, because row 5 is correct for every non-terminal ledger.

**WS-B — lane-branch authority.**

In `_lookup_pr`, when `lane_branch_name(slug)` yields a branch, run the head-ref leg
first and **return its result directly, including `None`**, without falling through
to the fuzzy search. When there is no recorded branch, behavior is byte-identical to
today. The suppression clause is the load-bearing half; a bare reorder is measurably
a no-op (spike-4).

The guard condition must key on **the slug being recorded**, not on the head-ref
lookup returning `None`. `lane_branch_name(None)` returns `None`
(`tools/lane_identity.py:95-103`), so "no slug" and "slug present, no PR on that
branch" are otherwise indistinguishable at the call site, and conflating them would
suppress the fuzzy leg for every slugless lane and regress resolution across the board.

**State honestly what this does not cover.** Both live candidates (#2494, #2518)
carry `slug: null / slug_source: "unresolved"`, so WS-B does not fix them. It fixes
every *future* lane, because current lanes record their slug at lane start
(`docs/features/sdlc-lane-identity.md`). Claiming otherwise would be false.

**WS-C — review-probe scope.**

Change `state="all"` → `state="merged"` at `tools/sdlc_stage_marker.py:263`. One
argument. Measured: closes both live fail-opens, keeps #2104 correct via the right
PR, and holds #2539's property 5/5. Leave the `pr or ...` short-circuit alone so
`verdict finalize --pr` callers are untouched.

**WS-D — deterministic candidate selection. Deferred to
[#2868](https://github.com/tomcounsell/ai/issues/2868); do not build it here.**

The critique falsified its headline rule, and the falsification is worth recording so
nobody re-derives it. WS-D was specified as "**MERGED before anything else**, then
most-recent first." That comparator **can never separate two candidates at any production
call site.** `state` threads straight into `gh pr list --state {state}`
(`tools/sdlc_stage_query.py:310`), so every candidate returned by a single call already
shares one state. The three production `_lookup_pr` call sites are exhaustive and
verified: `sdlc_stage_query.py:596` (default `open`), `:598` (`merged`), and
`sdlc_stage_marker.py:263` — the one **WS-C changes to `merged`**. After WS-C there is no
`all` caller left, and under `open` every candidate is open while under `merged` every
candidate is merged.

Its urgency argument dies with it. "6 abandoned PRs carry review artifacts and only 3 are
selected; a GitHub-side ranking change could silently re-open #2825" is a statement about
**closed-unmerged** candidates — precisely the population `state="merged"` excludes by
construction. WS-C is not defense-in-depth behind WS-D; WS-C is the fix, and it closes
that hole without WS-D's help.

The surviving half is real but not urgent: a **most-recent-first tiebreak among
same-state candidates**, grounded in spike-4's finding that `_lookup_pr(2518,
state="merged")` returns 2538, the *first* of two lifecycles rather than the most recent.
That is a latent determinism gap with no measured live fail-open, and #2868 carries it,
including the interaction that makes it non-trivial: PR 2542 body-validates for #2518 via
a retrospective prose mention while actually declaring `Closes #2547`, so recency-first
could select a **worse** match there. That needs its own measurement pass, which is
exactly the wrong thing to bolt onto this lane.

Consequences of the deferral, all of which simplify this plan: **Risk 3 is retired**, the
WS-B ≤ WS-D sequencing constraint disappears, and the verification row that claimed to
enforce it (and could not — it only ever read `_lookup_pr`) is removed rather than
patched.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/sdlc_stage_query.py::_gh_pr_search_issue_ref` — **not touched by this lane** (WS-D deferred to #2868). Its `except Exception` at `:338-339` keeps its current contract; no new test owed here.
- [ ] `tools/sdlc_stage_query.py::_lookup_pr` — documented "never raises". WS-B adds a branch; add a test that a raising `lane_branch_name` still yields `None`.
- [ ] `tools/sdlc_stage_marker.py::_review_artifact_posted` — all three artifact legs fail closed to `False` (`:330-335`). WS-C does not touch them; add one regression test that a `subprocess.run` raising on every call still returns `False` under the new `merged` scope.
- [ ] `agent/sdlc_router.py` terminal guard — `decide_next_dispatch` wraps rule predicates in try/except (`:1944`) but guards are **not** protected at all: `evaluate_guards` (`:830-839`) calls each guard bare. Assert **the guard itself** returns `None` on a malformed `stage_states` (missing key, non-dict, `None` value) — scoped to the guard, because `decide_next_dispatch` as a whole already raises on a non-dict at G1 today and this lane does not fix that (see the note under the Verification table).

### Empty/Invalid Input Handling
- [ ] Terminal guard against `{}`, `{"MERGE": None}`, `{"MERGE": ""}`, `{"MERGE": "in_progress"}`, `{"MERGE": "failed"}` — only the exact `"completed"` string terminates. Parametrize; a substring or truthiness check here would terminate a live lane.
- [ ] `_lookup_pr` with `slug=""` and `slug="   "` — `_nonempty`/`lane_branch_name` treat both as absent, so the fuzzy leg must still run. Assert by **call count**, not by result equality, or the test passes vacuously.
- [ ] `_gh_pr_search_issue_ref` with an empty candidate list, a list of non-dicts, and candidates missing the new sort key — no raise, `None` returned.

### Error State Rendering
- [ ] The terminal JSON from `decide()` must be distinguishable from an escalation `Blocked` by a machine consumer. Assert the exact key set of both shapes, not just that the reason string differs.
- [ ] The terminal reason string must not tell a human to run `sdlc-tool dispatch reset` — that is G4's remedy and it is wrong for a finished pipeline. Assert its absence explicitly.

## Test Impact

- [ ] `tests/unit/test_sdlc_router_oscillation.py` — **UPDATE**: any fixture whose ledger has `MERGE: "completed"` and asserts a non-terminal `row_id` now hits the terminal guard first. `test_terminal_merged_pipeline_routes_to_merge_not_build` is the one #2826 repaired; it deliberately does **not** set `MERGE: "completed"` (setting it would have made the fixture dead code), so it should survive untouched — **verify this rather than assume it**, and if it does change, that is a signal the guard is placed wrong.
- [ ] `tests/unit/test_sdlc_router_oscillation.py` — **UPDATE**: G4 tests that drive `same_stage_dispatch_count` on a `MERGE`-completed ledger would now terminate before G4 fires. Re-key those fixtures onto a non-terminal ledger so they keep testing G4.
- [ ] `tests/unit/test_sdlc_stage_query.py:270-400` — **UPDATE**: the D4 resolution-order tests (`_lookup_pr` issue-search primary + branch-head fallback) encode the old ladder order directly, several by `assert_called_once` / call-count on the search leg. WS-B inverts the order when a slug is present; these must be rewritten to assert the new contract, and a new case added for "slug present → fuzzy leg never called".
- [ ] `tests/unit/test_sdlc_stage_query.py:364-399` — **NO CHANGE** (was UPDATE before WS-D was deferred). `_gh_pr_search_issue_ref`'s "first body-validating candidate" contract is unchanged by this lane; these tests keep asserting it. [#2868](https://github.com/tomcounsell/ai/issues/2868) owns rewriting them.
- [ ] `tests/unit/test_sdlc_stage_query.py:1436-1520` — **REVIEW**: the #2757 two-pass block. WS-B changes the ladder *above* it, so re-run and confirm the two-pass cases still hold; the comment at `:1517` documenting "returns the FIRST body-validating candidate" stays accurate under this lane and needs only the #2868 pointer added by the Documentation task.
- [ ] `tests/unit/test_sdlc_stage_marker.py:1086-1210` — **UPDATE**: several cases patch `_lookup_pr` and assert its call kwargs; `:1113` already pins `return_value=2538`. The `state="all"` kwarg assertion becomes `state="merged"`. **Add** a case pinning the scope by kwarg so a future widening re-reds.
- [ ] `tests/unit/test_pipeline_complete_predicate.py` — **UPDATE (additive)**: add the router's exact call shape (`outcome="success"`, `pr_open=None`) as a pinned case, so a future change to the predicate's default behavior surfaces as a router-facing failure rather than a silent routing change.
- [ ] `tests/unit/test_architectural_constraints.py` — **UPDATE**: extend the router import-boundary class with an explicit positive assertion that importing `agent.pipeline_complete` is allowed, so the new import is documented as intentional and a future blanket tightening does not silently forbid it.
- [ ] `tests/unit/test_sdlc_next_skill.py:1631` — **REVIEW**: patches `_lookup_pr` to `None`; confirm the terminal shape does not change its expectation, and update if it does.
- [ ] `tests/integration/test_sdlc_session_ensure_integration.py` — **REVIEW**: 11 tests, green today. Re-run; no expected change, but they exercise the marker path WS-C touches.

No xfail or runtime `pytest.xfail()` markers exist anywhere in `tests/` (`grep` count: **0**), so there are none to convert.

## Rabbit Holes

- **Introducing a third `Complete` return type alongside `Dispatch` and `Blocked`.**
  Architecturally the right model, and it ripples into every consumer of
  `decide_next_dispatch`, the parity tests, and `decide()`'s documented JSON contract.
  **Settled (Resolved Question 2): the additive-key approach ships.** Revisit only if a
  second terminal reason is ever added, at which point the key-count argument changes.
- **Making `_body_references_issue` distinguish a declarative closing directive from a
  retrospective prose mention** (the PR 2542 finding). Genuinely interesting, genuinely
  a natural-language problem, and WS-B neutralizes the case that surfaced it because a
  prose mention lives in a PR whose head branch belongs to a different lane. Do not
  start writing a parser.
- **Backfilling `slug` onto #2494 and #2518 so WS-B covers them.** Mutating two live
  ledgers to make a fix look complete. The honest move is the scope statement in WS-B.
- **Extending the terminal guard to the `docs_success_no_pr` path.** It needs `pr_open`,
  which needs I/O, which breaks the router's purity and its no-`tools/` import
  constraint. Out of scope by design, not by omission.
- **Touching `_gh_pr_search_issue_ref` at all.** Client-side candidate ordering is
  [#2868](https://github.com/tomcounsell/ai/issues/2868), not this lane. Two temptations
  in particular: adding `sort:` to the search string (moves the guarantee back onto a
  remote service whose behavior we just established is not contractual) and "while I'm
  here" widening the `--json` field list (a diff on a shared function with no test in this
  lane that needs it). A verification task checks this function is byte-unchanged.
- **Rewriting `_compute_meta`'s two-pass structure now that WS-B changes the ladder.**
  The two-pass is #2826's, it is measured, and it is orthogonal. Leave it.

## Risks

### Risk 1: The terminal guard fires on a pipeline that is not actually finished
**Impact:** A live lane stops dispatching and looks silently dead — the worst possible
failure for this change, and strictly worse than the bug being fixed.
**Mitigation:** `MERGE == "completed"` is written in exactly one place,
`tools/sdlc_stage_marker.py`'s `sm.complete_stage(stage)` path, which is reached only
by `sdlc-tool stage-marker --stage MERGE --status completed` — documented in
`docs/sdlc/do-merge.md:100` as the post-merge step. The same transition already
triggers the run-lease release (`_release_run_best_effort`, `:823-836`) on the
understanding that nothing downstream still needs the lease, and
`_pipeline_is_terminal` already makes `reestablish_run_id` decline to re-mint after it.
The system already treats this marker as terminal in two other places; the router is
the outlier.

**That argument is about the write path, and the write path is only half the risk.** The
guard's *predicate* is what fires, and `is_pipeline_complete` consults `MERGE` alone —
DOCS, TEST, and PATCH are never read. So "who writes the key" does not bound "what the
rest of the ledger looks like when they do." The reachable gap is PATCH:
`_backfill_predecessors` settles on-spine predecessors but **explicitly exempts PATCH**
("PATCH, being off-spine, is excluded here and never force-completed"), so
`MERGE=completed + PATCH=pending` is a real ledger. It is now measured and carried as the
ninth matrix cell in spike-1, and the guard **deliberately settles it**.

**Verification tasks (both required):**
1. Confirm by grep that no code path other than `sm.complete_stage("MERGE")` writes
   `MERGE = completed`.
2. Assert **positively** that when `sm.complete_stage("MERGE")` returns, every
   `DISPLAY_STAGES` entry that is *not* PATCH is in `SETTLED_STATUSES`
   (`agent/pipeline_state.py:66`, `frozenset({"completed", "skipped"})`), and that PATCH
   is documented as the intentional exemption the guard settles. This is the assertion
   that bounds the ledger shape; task 1's grep only bounds the writer.
3. Add the exact-string parametrized test from the Failure Path section so
   `"in_progress"` / `"failed"` / `None` / `""` provably do not terminate.

### Risk 2: WS-B suppresses the fuzzy leg too aggressively and regresses PR resolution
**Impact:** Lanes whose PR was opened from a branch not matching `session/{slug}` stop
resolving their PR, disarming the PR-gated rows — the same class of failure as #2824
itself, pointed the other way.
**Mitigation:** Gate strictly on *slug recorded*, never on *head-ref returned None*.
Then measure before merging.

**The corpus is pinned and the bar is split, because a zero-tolerance bar here is
unfalsifiable.** WS-B's entire mechanism is *suppressing* the fuzzy leg when a slug is
recorded, so every lane whose PR came off a branch other than `session/{slug}` is a
guaranteed counterexample. Stated as "any counterexample blocks the change", the gate
either blocks WS-B outright or is settled by whoever picks the corpus. Neither is a test.

Corpus (runnable, not described):

```bash
gh issue list --state closed --limit 50 --json number -q '.[].number'
```

For each issue, resolve the PR under both ladder orders and classify every difference:

| Difference | Meaning | Disposition |
|---|---|---|
| Resolves old, not new; resolved PR's head ref is **not** `session/{slug}` | The fuzzy leg was doing work lane-branch authority deliberately gives up | **Expected cost.** Count it, report the count and the issue numbers in the PR body. Does not block. |
| Resolves old, not new; resolved PR's head ref **is** `session/{slug}` | The head-ref leg should have found this and did not — the mechanism is broken | **Blocks the change.** This is the only invalidating shape. |
| Resolves new, not old | Lane-branch authority recovered something fuzzy search missed | Report as a gain. |

Without that split the gate is not falsifiable, which is the same as not having one.

### Risk 3: *(retired)*
Previously covered WS-D's MERGED-first ordering sharpening #2824 if it landed alone.
**WS-D is deferred to [#2868](https://github.com/tomcounsell/ai/issues/2868)**, so
neither the risk nor its sequencing mitigation applies to this lane. #2868 inherits the
constraint: it must not land before WS-B.

### Risk 4: The `all` → `merged` change makes some review artifact unreachable
**Impact:** REVIEW completion markers refused, `/do-pr-review` re-dispatched in a loop.
**Mitigation:** Already measured — the #2539 control is 5/5 green and #2104 stays
`True` via the correct PR. Carry those five issues into the verification table as live
regression rows so the control is re-run at build time rather than trusted from this
document. Direction of failure is fail-closed, which the WS3b recovery row already handles.

### Risk 5: Test fixtures encoding the old ladder order pass vacuously after the change
**Impact:** A green suite that proves nothing — the #2091 stale-fixture problem that
`docs/sdlc/do-test.md` was widened to catch after #2826 found a router fixture rotting
on `main`.
**Mitigation:** Mutation-check each guard: revert each of WS-A/B/C individually and
record which tests re-red, in a demonstrated-red table in the PR body. A workstream
with zero re-reds has no real coverage. Assert lookup ordering by **call count on the
legs**, never by result equality, since both orders can return the same number for the
common case.

## Race Conditions

**No new race conditions identified.** All four workstreams are synchronous,
single-threaded, side-effect-free reads. The terminal guard is a pure dict read;
`_lookup_pr` and `_gh_pr_search_issue_ref` are blocking `subprocess.run` calls with
5-second timeouts that return a value and write nothing.

One **pre-existing** timing hazard is worth recording because this lane's design
depends on its direction, not on fixing it:

### Race 1: MERGE marker write versus a concurrent `next-skill` tick
**Location:** `tools/sdlc_stage_marker.py:820-836` (the `complete_stage("MERGE")` transition and its lease release) versus `tools/sdlc_next_skill.py::decide()`.
**Trigger:** A supervisor tick reads `stage_states` in the window between `/do-merge`
completing the GitHub merge and the `MERGE = completed` marker landing.
**Data prerequisite:** The marker must be persisted before a tick can observe terminality.
**State prerequisite:** The issue lease is held by the merging run until
`_release_run_best_effort` runs *inside* the same transition.
**Mitigation:** None needed, and none added. The window fails in the **safe** direction:
a tick landing inside it sees a non-terminal ledger and behaves exactly as it does today
(the pre-existing behavior this plan fixes for the post-marker case), and the next tick
sees the marker. The terminal guard makes this window strictly shorter-lived in
consequence, never longer. Adding locking here would be scope creep on a hazard that
resolves itself on the following tick.

## No-Gos (Out of Scope)

- **[SEPARATE-SLUG #2823]** The CI/collection gap that let a red integration test sit
  on `main` blocking nothing. Filed from the same PR #2826, genuinely independent of
  these four files.
- **[SEPARATE-SLUG #2491]** Unifying the router's dispatch table with a single-source
  pipeline graph (`docs/plans/pipeline-graph-single-source-of-truth.md`, status
  Planning). The terminal guard is a small consumer of that future model; building the
  model here would swallow the lane.
- **[SEPARATE-SLUG #2658]** Two-pole proofs for every verification row and guard
  (`docs/plans/gates-that-cannot-fire.md`, status Planning). This plan applies the
  mutation-check discipline to its own four guards (Risk 5) without adopting the
  program repo-wide.
- The `docs_success_no_pr` terminal leg of `is_pipeline_complete` — **not deferred for
  convenience**: it requires `pr_open`, which requires I/O in a router that must stay
  pure and `tools/`-import-free. WS-A ships the `merge_success` leg only, and the
  Verification table carries an anti-criterion asserting no I/O entered the router.

Everything else the three issues describe is in scope for this plan.

## Update System

No update-system changes required. All four workstreams are edits to existing Python
modules already shipped by the repo checkout; `/update`'s `git pull` + `uv sync` picks
them up with no new dependency, config file, or migration step.

One propagation note for the deploy step rather than the update script: `next-skill`
is invoked by the running worker, so machines running a worker need the standard
`worker-restart` after this lands for the terminal verdict to take effect. That is the
existing documented restart obligation, not a new one.

## Agent Integration

No new agent integration required. All four changes are internal to code paths the
agent already reaches through the existing `sdlc-tool` entry point declared in
`pyproject.toml [project.scripts]` — `sdlc-tool next-skill`, `sdlc-tool stage-marker`,
`sdlc-tool stage-query`. No new CLI entry point, no new MCP surface, and no direct
bridge import.

The one agent-visible change is the **shape of `next-skill`'s JSON on a terminal
pipeline**. The `/sdlc` router skill and `/do-sdlc` supervisor read that output to
decide whether to continue, so both must learn the terminal shape or they will keep
treating a finished pipeline as an unexplained block. Covered as a build task and a
documentation task, and asserted by an integration test that runs the real
`sdlc-tool next-skill` binary against a terminal ledger rather than calling `decide()`
in-process.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-router-oscillation-guard.md` — the G-guard doc of
      record. Add the terminal guard: its position in `GUARDS`, why it precedes G4
      (and what that changes about G4's reachability on terminal ledgers), and the
      exact predicate it delegates to.
- [ ] Update `docs/features/sdlc-pipeline.md` — the terminal state is now expressible;
      describe the verdict and what a supervisor should do on receiving it.
- [ ] Update `docs/features/sdlc-lane-identity.md` — record that a lane's recorded
      slug is now load-bearing for **PR resolution**, not only for worktree/branch/task-list
      naming, and that a slugless lane falls back to fuzzy resolution with the
      reopened-issue caveat.
- [ ] Update `docs/features/README.md` index if any section title changes.

### Inline Documentation
- [ ] Annotate the `_gh_pr_search_issue_ref` docstring (`tools/sdlc_stage_query.py:284-306`).
      It documents "returns the FIRST body-validating candidate with no MERGED-over-CLOSED
      preference" as settled behavior. **WS-D is deferred, so that behavior does not change
      here** — but the docstring must stop reading as a deliberate design choice and start
      reading as a known gap: add a line noting the ordering is GitHub best-match with no
      guarantee ([cli/cli#10244](https://github.com/cli/cli/issues/10244)) and pointing at
      [#2868](https://github.com/tomcounsell/ai/issues/2868). A stale comment presenting a
      gap as intent is what let these bugs survive this long.
- [ ] Rewrite `_lookup_pr`'s "Resolution order (D4)" docstring (`:348-366`) for the
      lane-branch-authoritative order, including the explicit statement that a recorded
      slug suppresses the fuzzy leg and why.
- [ ] Update the `state="all"` rationale comment at **`tools/sdlc_stage_marker.py:259-262`**
      to the `merged` rationale, preserving the #2539 reference and adding the measured
      control. **Extend, do not parallel** — #2826's review flagged paralleling as the
      failure mode here. (Citation corrected at critique: the previously cited `:238-250`
      is the `Args: pr:` docstring, not this comment. The comment is the second of the two
      `state="all"` matches in the file, which is why the anti-criterion grep is scoped to
      the call site.)
- [ ] Docstring on the new guard naming its `is_pipeline_complete` delegation and the
      `outcome="success"` / `pr_open=None` call shape, so nobody re-derives a second
      terminal definition.

### SDLC Stage Addenda
- [ ] **Update `.claude/skills-global/do-sdlc/SKILL.md:136` — the supervisor's `blocked`
      contract.** It currently routes every non-`ISSUE_LOCKED` block to "**STOP the loop.**
      Report the `reason` and `guard_id` to the human." Until it learns the terminal shape,
      WS-A trades three wasted ticks for an immediate false escalation. Add the
      `{"blocked": true, "complete": true, "guard_id": "TERMINAL", ...}` case → *exit the
      loop reporting **success**, not escalation*. Keep the wording **generic** (it is a
      global skill hardlinked to every machine by `scripts/update/hardlinks.py`, and the
      body already documents `blocked`/`guard_id` generically); no
      `.claude/skill-context/do-sdlc.md` is needed and none exists today.
      **This is a blocking dependency of WS-A, not a follow-up.**
- [ ] Update `docs/sdlc/do-merge.md` if the terminal verdict changes what a supervisor
      does after the MERGE marker lands.
- [ ] Update `.claude/skills/sdlc/SKILL.md`'s row/guard table if the parity test
      requires the new guard to appear there (the parity test cross-checks rule
      docstrings against SKILL.md state cells — **check whether guards are in scope for
      it before assuming either way**).

## Success Criteria

- [ ] A terminal ledger (`MERGE == completed`) returns an explicit terminal verdict
      from `decide_next_dispatch` in **all eight** cells of spike-1's matrix **and in the
      ninth `PATCH`-unsettled cell** — no `/do-build`, no `/do-pr-review`, no `/do-merge`,
      no `NO_RULE`.
- [ ] The terminal guard returns `None` (falls through), never `Blocked`, on a non-dict
      `stage_states` — asserted against the guard directly.
- [ ] The four non-terminal `/do-merge` routes measured in spike-1 (`MERGE` not
      completed) are **unchanged** — the negative control.
- [ ] `sdlc-tool next-skill` on a terminal pipeline emits a machine-distinguishable
      terminal shape, verified by invoking the real binary, not `decide()` in-process.
- [ ] `_lookup_pr` with a recorded slug never calls the fuzzy search leg (asserted by
      call count).
- [ ] `_lookup_pr` with no recorded slug behaves byte-identically to today (asserted by
      call count and result).
- [ ] The old-versus-new ladder diff over a corpus of recent closed issues produces
      **zero** issues that resolve under the old order and fail under the new one
      (Risk 2's counterexample check).
- [ ] `_review_artifact_posted(1785)` and `_review_artifact_posted(2073)` return
      **False**; `_review_artifact_posted(2104)` returns **True** via merged PR 2109.
- [ ] The #2539 control holds 5/5 live: #2860, #2831, #2716, #2734, #2741.
- [ ] **KNOWN-UNCOVERED — the two live #2824 candidates stay broken, by design.**
      #2494 and #2518 are the only OPEN reopened issues carrying a stale prior-lifecycle
      PR, and `sdlc-tool stage-query` reports `slug: null, slug_source: "unresolved"` for
      both. WS-B keys on a *recorded* slug, so **it does not fix either of them.** It fixes
      every future lane, because lanes now record their slug at lane start
      (`docs/features/sdlc-lane-identity.md`). This lane closes #2824 as "the mechanism is
      correct going forward", not as "the two live instances are repaired" — backfilling
      their ledgers to make the fix look complete is an explicit Rabbit Hole. Stating this
      here, at the completion bar, rather than only in task 8's issue comment.
- [ ] The terminal shape is consumed correctly end to end: `.claude/skills-global/do-sdlc/SKILL.md`
      routes `complete: true` to loop-exit-success, not to human escalation.
- [ ] `agent/sdlc_router.py` imports nothing from `tools/` (existing constraint test
      still green) and performs no I/O.
- [ ] Demonstrated-red table in the PR body: each of WS-A/B/C reverted individually
      re-reds at least one named test.
- [ ] All three issues carry their measured post-fix shape as a comment before closing
      — #2826's explicit request for #2817, applied to all three.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (router terminal verdict)**
  - Name: `router-builder`
  - Role: WS-A only — the terminal guard, its wiring into `GUARDS`, and `decide()`'s terminal JSON shape
  - Agent Type: builder
  - Resume: true

- **Builder (PR resolution)**
  - Name: `resolution-builder`
  - Role: WS-B and WS-C — the `_lookup_pr` ladder and the marker scope. **Not** `_gh_pr_search_issue_ref`, which this lane leaves alone (#2868).
  - Agent Type: builder
  - Resume: true

- **Test engineer (mutation proofs)**
  - Name: `mutation-tester`
  - Role: the demonstrated-red table — revert each workstream in isolation and record which tests re-red
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `lane-validator`
  - Role: independently re-run every live measurement and the whole Verification table; confirm `_gh_pr_search_issue_ref` is unmodified in the merged diff (WS-D scope leak check)
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `lane-documentarian`
  - Role: the Documentation section, including the two docstring rewrites that currently document the old behavior as intentional
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Re-measure all three shapes against build-time main
- **Task ID**: measure-baseline
- **Depends On**: none
- **Validates**: no test files — this produces the red-state record
- **Informed By**: spike-1, spike-2, spike-4 (all measurements to reproduce)
- **Assigned To**: `lane-validator`
- **Agent Type**: validator
- **Parallel**: false
- Re-run spike-1's eight-cell matrix and confirm it still matches this plan. If it has moved, **stop and report** — a drifted baseline invalidates the design, not just the numbers.
- Re-run `_lookup_pr(2494/2518, state="merged")` → 2516 / 2538, and `_review_artifact_posted(1785/2073/2104)` → True/True/True.
- Confirm no code path other than `sm.complete_stage("MERGE")` writes `MERGE = completed` (Risk 1's verification task).
- Record everything as the red-state paper trail for the PR body.

### 2. Write the failing tests first (all four workstreams)
- **Task ID**: red-tests
- **Depends On**: measure-baseline
- **Validates**: tests/unit/test_sdlc_router_oscillation.py, tests/unit/test_sdlc_stage_query.py, tests/unit/test_sdlc_stage_marker.py, tests/unit/test_pipeline_complete_predicate.py, tests/integration/test_sdlc_next_skill_terminal.py (create)
- **Informed By**: spike-1 (the eight-cell matrix is the test matrix), spike-4 (the live issue numbers are the fixtures)
- **Assigned To**: `mutation-tester`
- **Agent Type**: test-engineer
- **Parallel**: false
- Write every assertion from Success Criteria against **unmodified** source and watch each one fail. Paste the real failure output into the PR body.
- **Exempt from "watch it fail": every Verification row labelled GREEN TODAY** (the two router anti-criterion greps, the xfail row, the two lint/format rows, and WS-B's slugless negative control). They are anti-criteria and controls; they start green and must stay green. Do not record them as red-state evidence, and do not count them in the mutation table.
- Every negative assertion must be paired with a positive one (`"error" not in result`) so a crash cannot go green — the #2826 discipline.
- Assert lookup ordering by **call count on the legs**, never result equality.

### 3. WS-A — terminal guard
- **Task ID**: build-terminal-guard
- **Depends On**: red-tests
- **Validates**: tests/unit/test_sdlc_router_oscillation.py, tests/unit/test_pipeline_complete_predicate.py, tests/unit/test_architectural_constraints.py
- **Informed By**: spike-1 (must be a guard, not a row), spike-3 (delegate to `is_pipeline_complete`; do not define terminal twice)
- **Assigned To**: `router-builder`
- **Agent Type**: builder
- **Parallel**: true
- **Open the guard body with `if not isinstance(stage_states, dict): return None`** before delegating. `evaluate_guards` has no try/except and `is_pipeline_complete` calls `.get` on its first argument. Return `None`, never `Blocked` — an unreadable ledger falls through to the table.
- Then delegate to `agent.pipeline_complete.is_pipeline_complete(stage_states, "success", pr_open=None)`; ship the `merge_success` leg only.
- Insert it **first in `GUARDS`, ahead of G1** (Resolved Question 1), with the rationale in the docstring.
- Map the terminal `guard_id` to its JSON shape in `tools/sdlc_next_skill.py::decide()` — an additive `complete: true` key alongside `blocked` (Resolved Question 2).
- **Update `.claude/skills-global/do-sdlc/SKILL.md:136` in this same commit** so the supervisor exits the loop on `complete: true` instead of escalating. Shipping the router change without it is a regression in human noise.
- Do **not** modify row 5, row 8e, or row 10 — the guard pre-empts them and they stay correct for non-terminal ledgers.
- Do **not** harden G1 against non-dict ledgers here; that fragility predates this lane and is out of scope.

### 4. WS-B — lane-branch authority
- **Task ID**: build-resolution-ladder
- **Depends On**: red-tests
- **Validates**: tests/unit/test_sdlc_stage_query.py
- **Informed By**: spike-4 (a bare reorder is a measured no-op; the suppression clause is the fix)
- **Assigned To**: `resolution-builder`
- **Agent Type**: builder
- **Parallel**: true
- Gate the fuzzy-leg suppression on *slug recorded*, never on *head-ref returned None*. `lane_branch_name(None)` returns `None`, so conflating the two would suppress the fuzzy leg for every slugless lane.
- **Do not build WS-D here.** Deterministic candidate ordering is deferred to [#2868](https://github.com/tomcounsell/ai/issues/2868); leave `_gh_pr_search_issue_ref`'s selection logic alone. Widening the `--json` field list is part of that deferred work, not this task.
- Run Risk 2's counterexample check over the pinned corpus (`gh issue list --state closed --limit 50`) and attach the classified diff. Only a counterexample whose resolved PR head **is** `session/{slug}` blocks the change; head refs outside that shape are the expected, reported cost.

### 5. WS-C — review-probe scope
- **Task ID**: build-marker-scope
- **Depends On**: red-tests
- **Validates**: tests/unit/test_sdlc_stage_marker.py
- **Informed By**: spike-4 (`merged` closes both fail-opens; #2539 control 5/5)
- **Assigned To**: `resolution-builder`
- **Agent Type**: builder
- **Parallel**: true
- `state="all"` → `state="merged"` at `tools/sdlc_stage_marker.py:263`. Leave the `pr or ...` short-circuit untouched.
- Extend (do not parallel) the rationale comment above it.

### 6. Mutation proofs
- **Task ID**: mutation-check
- **Depends On**: build-terminal-guard, build-resolution-ladder, build-marker-scope
- **Assigned To**: `mutation-tester`
- **Agent Type**: test-engineer
- **Parallel**: false
- Revert each of WS-A/B/C **individually** and record which tests re-red. A workstream with zero re-reds has no coverage — go back and write the missing test.
- Revert all three together and confirm the three original reported shapes return.
- Produce the demonstrated-red table for the PR body.

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: mutation-check
- **Assigned To**: `lane-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Every checkbox in the Documentation section.
- The two docstring rewrites are the priority: both currently document the old behavior as deliberate, which is exactly what made these bugs look like features.

### 8. Post measured outcomes to all three issues
- **Task ID**: record-outcomes
- **Depends On**: document-feature
- **Assigned To**: `lane-validator`
- **Agent Type**: validator
- **Parallel**: false
- Comment the measured post-fix shape on #2817, #2824, and #2825 before any of them closes — #2826's explicit request, applied to all three.
- For #2824, state plainly that #2494 and #2518 remain unfixed because they carry no recorded slug, and that the fix covers lanes that record one.

### 9. Final validation
- **Task ID**: validate-all
- **Depends On**: record-outcomes
- **Assigned To**: `lane-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run the entire Verification table.
- Confirm `_gh_pr_search_issue_ref` is byte-unchanged in the merged diff — WS-D belongs to #2868 and must not have leaked in.
- Confirm no `tools/` import and no I/O entered `agent/sdlc_router.py`.

## Verification

| Check | Command | Expected |
|---|---|---|
| Router unit suite | `./scripts/pytest-clean.sh tests/unit/test_sdlc_router_oscillation.py -q` | exit code 0 |
| Stage-query unit suite | `./scripts/pytest-clean.sh tests/unit/test_sdlc_stage_query.py -q` | exit code 0 |
| Stage-marker unit suite | `./scripts/pytest-clean.sh tests/unit/test_sdlc_stage_marker.py -q` | exit code 0 |
| next-skill unit suite | `./scripts/pytest-clean.sh tests/unit/test_sdlc_next_skill.py -q` | exit code 0 |
| Terminal predicate suite | `./scripts/pytest-clean.sh tests/unit/test_pipeline_complete_predicate.py -q` | exit code 0 |
| Architectural constraints | `./scripts/pytest-clean.sh tests/unit/test_architectural_constraints.py -q` | exit code 0 |
| Terminal verdict, no `pr_number`, no branch | `./.venv/bin/python -c "from agent.sdlc_router import decide_next_dispatch as d; s={k:'completed' for k in ['ISSUE','PLAN','CRITIQUE','BUILD','TEST','REVIEW','DOCS','PATCH','MERGE']}; print(d(s,{},{'branch_exists':False}))"` | output contains `TERMINAL` |
| Terminal verdict, no `pr_number`, branch exists (the false rebuild) | `./.venv/bin/python -c "from agent.sdlc_router import decide_next_dispatch as d; s={k:'completed' for k in ['ISSUE','PLAN','CRITIQUE','BUILD','TEST','REVIEW','DOCS','PATCH','MERGE']}; print(d(s,{},{'branch_exists':True}))"` | output does not contain `/do-build` |
| Terminal verdict, `pr_number` present | `./.venv/bin/python -c "from agent.sdlc_router import decide_next_dispatch as d; s={k:'completed' for k in ['ISSUE','PLAN','CRITIQUE','BUILD','TEST','REVIEW','DOCS','PATCH','MERGE']}; s['_verdicts']={'REVIEW':{'verdict':'APPROVED','at':'2026-08-19T00:00:00Z'}}; print(d(s,{'pr_number':555},{'branch_exists':True}))"` | output does not contain `/do-merge` |
| Negative control — non-terminal still routes to merge | `./.venv/bin/python -c "from agent.sdlc_router import decide_next_dispatch as d; s={k:'completed' for k in ['ISSUE','PLAN','CRITIQUE','BUILD','TEST','REVIEW','DOCS','PATCH']}; s['_verdicts']={'REVIEW':{'verdict':'APPROVED','at':'2026-08-19T00:00:00Z'}}; print(d(s,{'pr_number':555},{'branch_exists':False}))"` | output contains `/do-merge` |
| Terminal verdict, `PATCH` unsettled (ninth cell) | `./.venv/bin/python -c "from agent.sdlc_router import decide_next_dispatch as d; s={k:'completed' for k in ['ISSUE','PLAN','CRITIQUE','BUILD','TEST','REVIEW','DOCS','MERGE']}; s['PATCH']='pending'; s['_verdicts']={'REVIEW':{'verdict':'APPROVED','at':'2026-08-19T00:00:00Z'}}; print(d(s,{'pr_number':555},{}))"` | output contains `TERMINAL` (red today: returns `/do-merge` row 10) |
| Terminal guard survives a non-dict ledger | `./.venv/bin/python -c "from agent.sdlc_router import guard_terminal_pipeline as g; print(all(g(b,{},{}) is None for b in [None,'x',42,[]]))"` | output contains `True`. Asserts **the new guard alone**, not `evaluate_guards` — see the note under the table. |
| Terminal reason does not give G4's wrong remedy | `./.venv/bin/python -c "from agent.sdlc_router import decide_next_dispatch as d; s={k:'completed' for k in ['ISSUE','PLAN','CRITIQUE','BUILD','TEST','REVIEW','DOCS','PATCH','MERGE']}; r=str(d(s,{},{})); print('TERMINAL' in r and 'dispatch reset' not in r)"` | output contains `True` — **both legs**: `TERMINAL` present AND `dispatch reset` absent. Asserting only the absence passes vacuously today (`Blocked(NO_RULE)` contains neither), which is what the critique caught. |
| #2825 fail-open closed (1785) | `./.venv/bin/python -c "import tools.sdlc_stage_marker as m; print(m._review_artifact_posted(1785,'tomcounsell/ai'))"` | output contains `False` |
| #2825 fail-open closed (2073) | `./.venv/bin/python -c "import tools.sdlc_stage_marker as m; print(m._review_artifact_posted(2073,'tomcounsell/ai'))"` | output contains `False` |
| #2539 control preserved (5 live issues) | `./.venv/bin/python -c "import tools.sdlc_stage_marker as m; print(all(m._review_artifact_posted(n,'tomcounsell/ai') for n in [2860,2831,2716,2734,2741]))"` | output contains `True` |
| Anti-criterion — no `tools/` import in the router | `grep -c '^from tools\|^import tools\|from tools\.' agent/sdlc_router.py` | match count == 0. **GREEN TODAY (regression anti-criterion).** Proves nothing about any workstream; counts as coverage for none of them in Risk 5's mutation table. |
| Anti-criterion — no I/O in the router (No-Go: DOCS-terminal leg) | `grep -cE 'subprocess\.\|requests\.\|urllib' agent/sdlc_router.py` | match count == 0. **GREEN TODAY (regression anti-criterion).** Same disposition. |
| WS-B — recorded slug never calls the fuzzy leg | `./.venv/bin/python -c "from unittest.mock import patch; import tools.sdlc_stage_query as q; f=patch.object(q,'_gh_pr_search_issue_ref',return_value=None).start(); l=patch.object(q,'_gh_pr_list',return_value=None).start(); q._lookup_pr(2494, slug='sdlc-2494', repo='tomcounsell/ai'); print(f'fuzzy_calls={f.call_count} head_calls={l.call_count}')"` | output contains `fuzzy_calls=0 head_calls=1`. **Verified red today**: returns `fuzzy_calls=1 head_calls=1`. |
| WS-B — no recorded slug still calls the fuzzy leg | `./.venv/bin/python -c "from unittest.mock import patch; import tools.sdlc_stage_query as q; f=patch.object(q,'_gh_pr_search_issue_ref',return_value=None).start(); q._lookup_pr(2494, slug=None, repo='tomcounsell/ai'); print(f'fuzzy_calls={f.call_count}')"` | output contains `fuzzy_calls=1`. **GREEN TODAY by design** (verified): WS-B's negative control, whose job is to *stay* green. Counts as coverage for no workstream in the mutation table. |
| Anti-criterion — `state="all"` gone from the **call site** | `grep -c 'state="all")' tools/sdlc_stage_marker.py` | match count == 0. Scoped to the call so it cannot match prose: today this returns **1** (the call at `:263`), while the unscoped `grep -c 'state="all"'` returns **2** because the rationale comment at `:259` literally opens `# state="all": ...`. The unscoped form is unsatisfiable alongside the Documentation task that keeps that comment. |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1. **GREEN TODAY (regression anti-criterion).** Counts as coverage for no workstream. |
| Lint clean | `python -m ruff check .` | exit code 0. **GREEN TODAY (hygiene row).** Not workstream coverage. |
| Format clean | `python -m ruff format --check .` | exit code 0. **GREEN TODAY (hygiene row).** Not workstream coverage. |

### Which rows can go red, and which cannot

Task 2 says "watch each one fail." That instruction applies **only to rows not labelled
GREEN TODAY**. The labelled rows are anti-criteria, negative controls, and hygiene checks:
they start green, their job is to *stay* green, and Risk 5's mutation table must not count
any of them as coverage for any workstream. Recording them in the red-state paper trail as
if they had failed would manufacture four rows of false evidence — which is precisely the
#2091 stale-fixture problem this plan is trying not to repeat.

### Note: the non-dict ledger is a pre-existing router-wide fragility, not one this lane creates

The critique's concern said a raising terminal guard would surface as `decide()`'s
`{"error": ...}` shape "instead of today's `Blocked(NO_RULE)`". **Measured, that premise is
wrong in the plan's favour and worth stating precisely:** `decide_next_dispatch` *already*
raises on a non-dict `stage_states` today, at **G1** — `_latest_critique_verdict`
(`agent/sdlc_router.py:276`) calls `stage_states.get("_verdicts")` with no type check.
Verified on `f491306c5`: `None`, `42`, and `[]` each raise `AttributeError` before any
guard this lane touches runs.

So the `isinstance` check in WS-A's guard is still **required** — placed first, the guard
must not become a *new* raise site, and it must fall through rather than terminate a lane
it cannot read. But it does **not** make the router non-dict-safe, and this plan must not
claim it does. Hardening G1 is a separate concern on a rule this lane has no reason to
touch. The verification row above therefore asserts the new guard in isolation, which is
exactly the scope of what WS-A controls.

## Critique Results

War room run 2026-08-19 against `main` @ `f491306c5`. Depth **FULL** (force-FULL: the plan
touches the doctrine paths `agent/sdlc_router.py` and `.claude/skills/`). Roster 3/3 complete,
0 ungrounded. **0 blockers, 8 concerns, 1 nit.**

**Revision applied 2026-08-19T05:45:10Z.** All 9 findings are resolved below (the NIT too). Every critique claim was independently re-measured during the revision and all reproduce, including the two the critique itself had not measured: `MERGE=completed + PATCH=pending` → `/do-merge` row 10, and `AttributeError` on a non-dict ledger. One critique premise was found **understated** and is corrected in place: `decide_next_dispatch` already raises on a non-dict today at G1, so it does not currently return `Blocked(NO_RULE)` for that input — see the note under the Verification table. The material scope change is that **WS-D is cut from the lane and filed as #2868**.

Every live claim in Spike Results was independently re-measured by the critique driver and
**all reproduce**: the eight-cell matrix, `_lookup_pr(2494/2518, state="merged")` → 2516 / 2538,
and `_review_artifact_posted(1785/2073/2104)` → True/True/True.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | Task 3 specifies the guard as a bare delegation to `is_pipeline_complete(stage_states, "success", pr_open=None)`, but the Failure Path section demands a test proving the guard cannot raise on a non-dict `stage_states`. The delegation calls `psm_states.get("MERGE")` (`agent/pipeline_complete.py:69`) and raises `AttributeError` on a non-dict, so the requested test and the specified implementation contradict. | **RESOLVED** — Technical Approach WS-A now mandates `if not isinstance(stage_states, dict): return None` before delegating; task 3 carries it as its first bullet; Failure Path + Verification assert the guard in isolation. | `evaluate_guards` walks `GUARDS` with a bare `result = guard(stage_states, meta, ctx)` and **no** try/except — only rule predicates are wrapped, inside `decide_next_dispatch`. Placing the guard first in `GUARDS` makes it the most exposed callable in the router: a raise escapes `decide_next_dispatch` and surfaces as `decide()`'s `{"error": ...}` shape instead of today's `Blocked(NO_RULE)`. Open the guard body with `if not isinstance(stage_states, dict): return None` before delegating. Return `None`, never `Blocked` — an unreadable ledger must fall through to the table, not terminate a lane. |
| CONCERN | Risk & Robustness | Risk 1's mitigation argues only from the write path and never from the guard's predicate. `is_pipeline_complete` returns `(True, "merge_success")` on `MERGE == "completed"` alone; DOCS/TEST/PATCH are not consulted. `_backfill_predecessors` settles on-spine predecessors but **explicitly exempts PATCH** ("PATCH, being off-spine, is excluded here and never force-completed"), so a terminal ledger with an unsettled PATCH is reachable and appears in neither the eight-cell matrix, Success Criterion 1, nor the negative control. | **RESOLVED** — spike-1 gains a ninth `PATCH`-unsettled matrix cell (measured), the guard is stated to settle it deliberately, Risk 1 gains a positive `SETTLED_STATUSES` assertion, and Verification gains the cell as a red-today row. | Measured on `f491306c5`: `decide_next_dispatch({...all completed, PATCH:'pending'}, {'pr_number':555}, {})` → `Dispatch(skill='/do-merge', row_id='10')`. The repo already owns the vocabulary: `SETTLED_STATUSES = frozenset({"completed", "skipped"})` (`agent/pipeline_state.py:66`). Either add a matrix cell for `PATCH ∈ {pending, failed}` under `MERGE == completed` and state the guard deliberately settles it, or make Risk 1's verification task assert positively that `sm.complete_stage("MERGE")` leaves every `DISPLAY_STAGES` entry in `SETTLED_STATUSES`. Task 1's grep proves who writes the key, not what the rest of the ledger looks like when they do. |
| CONCERN | Risk & Robustness | The terminal verdict ships as a `Blocked`, which today's supervisor treats as a human escalation, so until that consumer is updated WS-A converts "three wasted ticks then a misleading escalation" into "an immediate escalation". Agent Integration names the obligation but no Documentation checkbox and no task names the supervisor skill file. | **RESOLVED** — new Documentation checkbox for `.claude/skills-global/do-sdlc/SKILL.md:136` (global body, generic wording, no skill-context file), added to task 3 as a same-commit obligation and to Success Criteria. | `decide()` serializes `Blocked` to exactly `{"blocked": True, "reason": ..., "guard_id": ...}`. `.claude/skills-global/do-sdlc/SKILL.md:136`: "`{"blocked": true, ...}` (other reasons) → **STOP the loop.** Report the `reason` and `guard_id` to the human." The Documentation section lists only `.claude/skills/sdlc/SKILL.md` and `docs/sdlc/do-merge.md`, leaving `do-sdlc` an orphan. It is a **global** skill hardlinked to every machine by `scripts/update/hardlinks.py`; per CLAUDE.md global bodies stay generic with repo specifics in `.claude/skill-context/`. The new checkbox must also decide whether `guard_id == "TERMINAL"` is a generic contract or repo-specific. |
| CONCERN | Scope & Value | WS-D's headline rule "MERGED before anything else, then most-recent first" becomes unreachable at every live call site the moment WS-C lands, so Open Question 3 is decided on a premise WS-C removes. The only non-test caller passing `state="all"` is `tools/sdlc_stage_marker.py:263` (which WS-C changes to `merged`); `tools/sdlc_stage_query.py:596/598` pass `open` then `merged`. Under `open` all candidates are open; under `merged` all are merged. | **RESOLVED (scope cut)** — WS-D removed from the lane and filed as #2868 with the recency-only framing. Risk 3 retired; sequencing constraint moved to #2868. See Resolved Questions Q3. | WS-D's justification ("6 abandoned PRs carry artifacts, only 3 selected"; "a GitHub-side ranking change could silently re-open #2825") is specifically about closed-unmerged candidates, which `state="merged"` excludes by construction. The surviving half is the recency tiebreak, which is real and is exactly spike-4's #2518 finding (fuzzy picks 2538, "the *first* of two lifecycles, not even the most recent"). Re-anchor WS-D on merged-vs-merged ambiguity. If MERGED-first is retained as defence against a future `all` caller, note the plan already carries an anti-criterion forbidding that caller — two mechanisms, one hole, only one testable. |
| CONCERN | Scope & Value | Risk 2's counterexample check is the stated gate on WS-B, but its corpus is unspecified and its bar is zero-tolerance. WS-B's mechanism is *suppressing* the fuzzy leg when a slug is recorded, so any lane whose PR came off a branch other than `session/{slug}` is a guaranteed counterexample. As written the check either blocks the change outright or is settled by whoever picks the corpus. | **RESOLVED** — Risk 2 now pins a runnable corpus and splits the bar: only a counterexample whose PR head **is** `session/{slug}` invalidates the design; other head refs are a counted, reported cost. | Pin the corpus as a runnable command (e.g. `gh issue list --state closed --limit 50 --json number`) and split the outcome: a counterexample whose resolved PR head is **not** `session/{slug}` is the expected, accepted cost of lane-branch authority — count and report it; only a counterexample whose PR head **is** `session/{slug}` (the head-ref leg should have found it and did not) invalidates the design. Without that split, Risk 2's gate is unfalsifiable. |
| NIT | Scope & Value | WS-B has zero measurable effect on any currently-observable case and the plan says so honestly in prose, but no Success Criterion records the scope limit, so a reader of Success Criteria alone would conclude this lane closes #2824. Verified live: `stage-query` returns `slug: None, slug_source: unresolved` for both #2494 and #2518, and both issues are still OPEN. | **RESOLVED anyway** — promoted to an explicit KNOWN-UNCOVERED Success Criterion naming #2494 and #2518. | (NIT — exempt.) Promote the scope statement into Success Criteria as an explicit "known-uncovered" line so it is visible where the completion bar is read, rather than only in task 8's issue comment. |
| CONCERN | History & Consistency | Two sections give instructions that cannot both be satisfied. Documentation says to extend the `state="all"` rationale comment "preserving the #2539 reference"; Verification requires `grep -c 'state="all"' tools/sdlc_stage_marker.py` == 0. That grep returns **2** today: `:263` is the call and `:259` is the rationale comment, which literally opens `# state="all": the artifact question is historical`. The cited range is also wrong — `:238-250` is the `Args: pr:` docstring; the comment is at `:259-262`. | **RESOLVED** — anti-criterion scoped to `grep -c 'state="all")'` (returns 1 today, 0 after; the prose comment cannot match). Documentation citation corrected to `:259-262`. | Scope the anti-criterion to the call site so it cannot match prose: `grep -c '_lookup_pr(.*state="all"' tools/sdlc_stage_marker.py` == 0, or `grep -c 'state="all")' tools/sdlc_stage_marker.py` == 0. Both are red today (the call at `:263` matches) and both stay green when the rationale comment names the old scope. Correct the Documentation citation to `tools/sdlc_stage_marker.py:259-262`. |
| CONCERN | History & Consistency | The row named for the WS-B ≤ WS-D sequencing constraint cannot detect WS-D. It reads only `_lookup_pr` and asserts WS-B's head-ref leg precedes the fuzzy leg; WS-D lives inside `_gh_pr_search_issue_ref`, which the row never opens. If Open Question 3 is answered "defer WS-D", the row still passes — so it cannot enforce what the plan calls "a build-sequencing requirement, not advice." | **RESOLVED (row removed)** — the row is deleted with WS-D. WS-B is instead asserted **behaviourally** by two call-count rows (fuzzy_calls=0 with a slug, =1 without), verified red/green today, replacing the source-text shape the critique warned was spuriously fragile. | The assertion is genuinely red today (verified: `lane_branch_name` currently appears **after** `_gh_pr_search_issue_ref` in `_lookup_pr`), so it is a valid WS-B check under the wrong name. Express the implication — WS-D present implies WS-B present — e.g. assert `('sorted' in inspect.getsource(q._gh_pr_search_issue_ref)) <= ('lane_branch_name' in src.split('_gh_pr_search_issue_ref')[0])`, and rename the row. Better: drop the source-text shape (a WS-B helper wrapping `lane_branch_name` would fail it spuriously) and assert behaviour — with a slug recorded, patch `_gh_pr_search_issue_ref` and assert call count 0. |
| CONCERN | History & Consistency | Four of the twenty Verification rows already pass on unmodified `main`, contradicting task 2's "watch each one fail" and Risk 5's own mutation discipline. Measured on `f491306c5`: the "dispatch reset" row passes because today's output is `Blocked(reason='no matching dispatch rule', guard_id='NO_RULE')`; both router anti-criterion greps already return 0; the xfail row already exits 1. Anti-criteria legitimately start green, but the plan does not distinguish them, so the red-state paper trail will contain four rows that prove nothing. | **RESOLVED** — the `dispatch reset` row now asserts both legs (`TERMINAL` present AND `dispatch reset` absent) so it cannot pass vacuously; all green-today rows are labelled **GREEN TODAY** in the Expected column, and task 2 plus a new subsection exempt them from "watch it fail" and from mutation-table credit. | The first Verification row already models the correct shape ("output contains `TERMINAL`" — red today, green after). Give the `dispatch reset` row the same treatment: assert `output contains 'TERMINAL'` **and** `output does not contain 'dispatch reset'`, so a guard returning the wrong reason string cannot pass vacuously. Label the three green-today rows explicitly as regression anti-criteria in the Expected column so Risk 5's mutation table does not count them as coverage for any workstream. |

---

## Resolved Questions

All three are settled. Nothing is deferred into build.

**Q1 — Guard ordering: should the terminal guard precede G1, or sit after G4?**
**Resolved: first in `GUARDS`, ahead of G1.** A completed pipeline has nothing to gain
from any other guard's opinion, and G4 specifically gives actively wrong advice on one
(`dispatch reset` is not the remedy for "this is done"). The counter-argument was #1267's
precedent that escalation guards take priority; it does not reach this case, because G4
exists to bound a *loop* and terminating before the loop begins is strictly better than
capping it at three. The departure from convention is deliberate and is recorded in the
guard docstring and in `docs/features/sdlc-router-oscillation-guard.md`, so the next
reader meets the reasoning rather than re-litigating it.

*One consequence to build against:* placing the guard first makes it the router's most
exposed callable, since `evaluate_guards` wraps nothing. That is why the `isinstance`
check is mandatory rather than defensive styling.

**Q2 — Terminal return type: additive key on `Blocked`, or a third `Complete` dataclass?**
**Resolved: additive key.** `Blocked(guard_id="TERMINAL")` plus a `complete: true` key in
`decide()`'s JSON. A third dataclass is the more honest model — a finished pipeline is not
blocked — but it ripples into every consumer of `decide_next_dispatch`, the parity tests,
and `decide()`'s documented contract, and it buys nothing this lane needs: exactly one
consumer (`do-sdlc`) has to change behavior, and it has to change either way. Ship the key,
change the one consumer, keep the dataclass refactor available if a second terminal reason
ever arrives. Recorded as a Rabbit Hole so the option stays visible.

**Q3 — WS-D scope: is deterministic candidate ordering in this lane or the next?**
**Resolved: the next. Filed as [#2868](https://github.com/tomcounsell/ai/issues/2868) and
removed from this lane.**

The critique falsified WS-D's headline rule and the falsification is decisive. WS-D was
"**MERGED before anything else**, then most-recent first" — but `state` threads straight
into `gh pr list --state {state}`, so every candidate in one call shares a state. The three
production `_lookup_pr` call sites are exhaustive: `sdlc_stage_query.py:596` (`open`),
`:598` (`merged`), and `sdlc_stage_marker.py:263` — the one **WS-C changes to `merged`**.
After WS-C no caller passes `all`, so a MERGED-over-CLOSED comparator can never separate
two candidates at any live call site.

Its urgency argument goes with it. "6 abandoned PRs carry artifacts, only 3 selected; a
ranking change could silently re-open #2825" describes **closed-unmerged** candidates,
which `state="merged"` excludes by construction. WS-C is the fix, not a stopgap behind
WS-D.

The surviving half — a most-recent-first tiebreak among same-state candidates — is real
(spike-4: `_lookup_pr(2518, "merged")` → 2538, the *first* of two lifecycles) but has no
measured live fail-open, and it is not simply smaller: PR 2542 body-validates for #2518
through a retrospective prose mention while declaring `Closes #2547`, so recency-first
could select a **worse** match. That deserves its own measurement pass, which is exactly
what should not be bolted onto this lane.

Keeping a workstream whose stated justification had been disproved would have been the
wrong call, and shrinking it in place would have left a diff on a shared function with no
test in this lane needing it. #2868 carries the full reasoning, the sequencing constraint
(it must not land before WS-B), and the 2542 caveat.
