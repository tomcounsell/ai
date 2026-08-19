---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-19
tracking: https://github.com/tomcounsell/ai/issues/2817
last_comment_id: 5324622874
revision_applied: true
revision_applied_at: 2026-08-19T06:20:00Z
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
| **WS-A** (#2817) | `agent/sdlc_router.py`, `tools/sdlc_next_skill.py`, `tools/sdlc_stage_query.py` (`issue_state` key only) | one guard + one meta key + one JSON shape |
| **WS-B** (#2824) | `tools/sdlc_stage_query.py`, `tools/lane_identity.py` | ~10 lines in `_lookup_pr` + one helper |
| **WS-C** (#2825) | `tools/sdlc_stage_marker.py` | one argument |

WS-A's `issue_state` key (round-2 blocker fix) puts one small WS-A edit into
`tools/sdlc_stage_query.py`, so the halves are no longer strictly file-disjoint. They remain
**function-disjoint**: WS-A adds a key inside `_compute_meta`, WS-B edits `_lookup_pr`, and
neither reads the other's code. Each still reverts alone.

The two halves are **near-file-disjoint** — WS-A touches nothing WS-B/C touch, and vice versa
— so a reviewer reads two independent diffs rather than one entangled one, and each
workstream reverts alone (the mutation table in Risk 5 enforces that each has its own
coverage). Splitting these halves into two PRs would buy the reviewer nothing and
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
   → **WS-B inserts a branch-existence probe here**, ahead of the fuzzy leg: a recorded slug
   naming a live branch takes authority; a recorded slug naming nothing falls through unchanged.
3b. **Issue state** (`_compute_meta`, WS-A): when and only when `stage_states["MERGE"] ==
   "completed"`, resolve `gh issue view N --json state` into `meta["issue_state"]`, degrading
   to `None` on failure. This is the input that keeps the terminal guard from latching a
   re-entered issue (Risk 6).
4. **Artifact verification**: `_verify_stage_artifacts_live` reads the resolved
   `pr_number`, sets `context["stage_artifacts_verified"]` / `unverified_stage`.
5. **Routing**: `agent/sdlc_router.py::decide_next_dispatch(stage_states, meta, context)`
   runs `GUARDS` in order, then walks `DISPATCH_RULES` top to bottom, first match wins.
   → **DEFECT #2817**: nothing in either list reads `stage_states["MERGE"]`, so a
   finished pipeline is adjudicated by rows 5 / 7 / 8e / 10 on its residual fields.
   → **WS-A inserts the terminal guard first**, reading `stage_states["MERGE"]` and
   `meta["issue_state"]` — both already resolved upstream, so the router still does no I/O.
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

- **New dependencies**: none external. Two new *internal* imports:
  `agent/sdlc_router.py` → `agent/pipeline_complete.py`, and
  `tools/sdlc_stage_query.py` → `tools/lane_identity.py` (the latter edge already exists at
  `:60-61`, so WS-B adds a symbol, not an edge). The router import is legal:
  `tests/unit/test_architectural_constraints.py` forbids only `agent/sdlc_router.py`
  importing from **`tools/`**. A verification row pins this so the constraint is not
  quietly widened. **The guard still performs no I/O** — `issue_state` is resolved in
  `_compute_meta` and arrives as a plain string in `meta`, so router purity is preserved by
  construction rather than by discipline.
- **Interface changes**: `_lookup_pr`'s resolution *order* changes when a lane branch
  is recorded **and exists on origin** (WS-B). Its signature does not. `_compute_meta`'s
  returned `_meta` gains an `issue_state` key (additive; `None` on non-terminal ledgers). `_gh_pr_search_issue_ref` is **untouched**
  by this lane (WS-D deferred to #2868). `decide()`'s JSON gains one additive `complete`
  key on the terminal shape (WS-A); existing keys keep their meaning. The one external
  contract change is `.claude/skills-global/do-sdlc/SKILL.md`'s reading of that key.
- **Coupling**: **decreases.** The router stops carrying an implicit, unstated
  definition of "not finished" (the absence of any MERGE-aware row) and starts
  consuming the explicit shared predicate. Two definitions of terminal collapse into one.
- **Data ownership**: unchanged. No new persisted state, no ledger fields, no Popoto
  models, therefore **no migration**. `issue_state` is a *computed* meta key resolved fresh
  each tick, never stored — deliberately, since a persisted copy would go stale on reopen
  and reintroduce Risk 6 through the cache instead of the ledger.
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
| WS-B's regression fixture still in shape | `./.venv/bin/python -c "from tools.sdlc_stage_query import _lookup_pr; assert _lookup_pr(2694, slug='sdlc-2694', repo='tomcounsell/ai', state='merged') == 2695"` | #2694 is the single measured loss the existence check exists to prevent. If it has healed (via [#2869](https://github.com/tomcounsell/ai/issues/2869) or a slug adoption), WS-B has **no live regression fixture left** — say so in the PR body rather than quietly dropping the row, and fall back to a synthetic fixture patching `lane_branch_exists_on_remote`. |
| #2694's lane branch still absent from origin | `git ls-remote --heads origin refs/heads/session/sdlc-2694` | Empty output. The other half of the same fixture: the loss only occurs because this branch does not exist. |
| Terminal-ledger issue states still CLOSED | `./.venv/bin/python -c "from agent.pipeline_ledger import PipelineLedger; import json,subprocess; ns=[int(r.issue_number) for r in PipelineLedger.query.all() if json.loads(r.stage_states_json or '{}').get('MERGE')=='completed']; c=sum(subprocess.run(['gh','issue','view',str(n),'--json','state','-q','.state'],capture_output=True,text=True).stdout.strip()=='CLOSED' for n in ns); assert c==len(ns), (c, len(ns))"` | 16/16 at plan time. A drift means the `!= \"CLOSED\"` polarity now costs real coverage and must be re-argued before build. |

## Solution

### Key Elements

- **WS-A — Terminal guard (#2817).** Give the router the terminal fact it already
  computes elsewhere, by consulting `agent/pipeline_complete.py::is_pipeline_complete`
  rather than inventing a second definition of "finished" — **gated on the tracking issue
  being CLOSED**, so a re-entered issue is not latched shut forever.
- **WS-B — Lane-branch authority (#2824).** When a lane branch is recorded **and exists on
  origin**, the exact head-ref match is authoritative: it answers, or the lookup answers
  `None`. The fuzzy body search runs when there is no recorded branch, or when the recorded
  slug names a branch that does not exist.
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

**After:** `next-skill` → context assembly (resolves `issue_state` because `MERGE` is
completed) → router evaluates the terminal guard **first** → `MERGE == completed` **and
`issue_state == "CLOSED"`** → **terminal verdict: "pipeline complete, nothing to
dispatch"** → supervisor stops. No rebuild, no re-review, no oscillation, no misleading
escalation.

Reopened-issue tick — the case both WS-A and WS-B are shaped around:

**Today:** `_lookup_pr(2494)` → fuzzy `#2494` → prior lifecycle's merged PR **2516** →
row 5 disarmed → **row 7 `/do-pr-review`** on a finished PR.

**After (with a recorded slug whose branch exists):** the terminal guard sees
`issue_state == "OPEN"` and **stands down** — it must, or the ledger latches shut forever
(Risk 6) — so resolution is what decides the tick. `_lookup_pr(2494, slug=...)` →
`session/{slug}` exists on origin → head-ref leg is authoritative → no PR on this lane's
branch → **`None`** → row 5 fires → `/do-build` creates the PR this lifecycle actually needs.

**After (with a slug whose branch does not exist, e.g. a minted `sdlc-N` on a
human-named lane — [#2869](https://github.com/tomcounsell/ai/issues/2869)):** the existence
check fails, the fuzzy leg runs, and behavior is exactly today's. WS-B degrades to a no-op
rather than to a wrong answer, which is the property the round-1 shape lacked.

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

*It must not latch a re-entered issue shut. (Round-2 blocker.)*

`MERGE == "completed"` is **durable ledger state with no un-write path**, and
`PipelineLedger.get_or_create(target_repo, issue_number)` returns the *same* ledger for a
second lifecycle. A guard keyed on that key alone therefore terminates a reopened issue on
every tick, forever, and **nothing downstream can clear it**:

| Candidate remedy | Why it does not reach |
|---|---|
| `sdlc-tool stage-marker --status ...` | `choices=["in_progress", "completed", "skipped"]` (`tools/sdlc_stage_marker.py:957`, verified). No status moves a stage backwards. |
| `sdlc-tool dispatch reset` | Clears `_sdlc_dispatches` only (`tools/sdlc_dispatch.py:296`, `_cli_reset`). Never touches `stages`. |
| Hand-zeroing `stage_states_json` | Documented as a clobber: `docs/features/sdlc-issue-keyed-stage-ledger.md:324` calls that exact reset "silently wiping a live, populated" ledger. |

That is **strictly worse than the bug being fixed**. Today the same reopened ledger
recovers on its own — measured on `f491306c5` with `{ISSUE,PLAN,CRITIQUE: completed,
BUILD: in_progress}`: `branch_exists=True, pr_number=None` → `/do-build` row 5, and a
resolved `pr_number` → row 7 / row 10. A permanent latch replaces a self-healing wrong
answer with an unrecoverable dead lane. Reachability is not theoretical: spike-4 counted
**17 reopened issues, 8 carrying a merged PR that merged before the reopen**, and **16 of
36 live `PipelineLedger` rows carry `MERGE == "completed"` today**.

**Resolution: the guard requires positive evidence that the issue is CLOSED.**
`_compute_meta` (`tools/sdlc_stage_query.py:515`) already shells out to `gh` — it calls
`_lookup_pr` and `_fetch_pr_merge_state` on every enriched query — so it gains one
`issue_state` key, and the guard opens with:

```python
if not isinstance(stage_states, dict):
    return None
if (meta or {}).get("issue_state") != "CLOSED":
    return None
```

**The polarity is `!= "CLOSED"`, not the critique's suggested `== "OPEN"`, and the
difference is the whole safety argument.** Under `== "OPEN"`, an *unresolvable* issue
state (`None` — a `gh` outage, a rate limit, an unset `GH_REPO`) is not `"OPEN"`, so the
guard fires and terminates. A transient network failure would then permanently latch a
live lane, which is the exact failure this fix exists to prevent, re-entered through the
fix itself. Under `!= "CLOSED"`, an unresolvable state falls through to the dispatch table
— i.e. to **today's behavior**, the well-understood baseline this plan is improving on.
This is the same fail-closed discipline #1642 established after a soft-failing lookup
silently disarmed G6, pointed at the guard's own input.

**Coverage is not sacrificed by the stricter polarity — measured, not assumed.** All
**16 of 16** terminal ledgers on this machine resolve `gh issue view N --json state` to
`CLOSED` (issues 2475, 2566, 2628, 2637, 2638, 2640, 2644, 2645, 2655, 2659, 2663, 2679,
2682, 2694, 2697, 2711; zero OPEN, zero unresolved). The guard fires on the entire measured
terminal population and stands down on exactly the re-entered case.

It is also the semantically right reading, not merely the safe one. A merged pipeline whose
issue is still open is a pipeline someone deliberately did not finish — a `Refs #N` PR, or
one issue of a multi-issue lane. "Terminal" should mean the tracker agrees, and requiring
`CLOSED` makes the router's terminal fact and the tracker's fact the same fact.

**Cost containment for the extra `gh` call.** Resolve `issue_state` **only when
`stage_states["MERGE"] == "completed"`**. Every non-terminal ledger — the overwhelming
majority of ticks — pays nothing, and a terminal ledger pays one `gh issue view N --json
state` on a path that already makes two or more `gh` calls. Resolve it once per
`_compute_meta` invocation, inside the existing `cached_target_repo_resolution()` scope, and
degrade to `None` on any failure rather than raising — `_compute_meta`'s own contract
(`:505-512`) is that "a corrupt value must degrade rather than raise", since "an exception
here would take the entire `stage-query` projection down and blind the router."

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

**WS-B — lane-branch authority, gated on the branch actually existing.**

**The round-1 shape of WS-B was falsified on live data and has been replaced.** The
original spec was: when `lane_branch_name(slug)` yields a branch, run the head-ref leg and
return its result directly, *including `None`*, suppressing the fuzzy search. Measured over
every `PipelineLedger` on this machine with the real `_body_references_issue` validator,
that shape scores **0 gains and 1 loss** — it never once recovered a PR the fuzzy leg
missed, and it destroyed a currently-correct resolution.

**The measurement (2026-08-19, 36 ledgers, 11 with a recorded slug):**

| Shape | Gains | Losses | Unchanged |
|---|---|---|---|
| Round-1 (unconditional head-ref authority) | **0** | **1** (#2694) | 10 |
| Round-2 (authority only if the branch exists on origin) | **0** | **0** | 11 |

Critique round 2 measured 0 gains / **2** losses. One of the two has healed since: #2738's
slug was `sdlc-2738` when the critique ran and is `hook-validator-target-resolution` today
(`slug_source: recorded`), which matches PR 2746's actual head ref, so it moved from LOST to
correctly-covered. The remaining loss is **#2694** — slug `sdlc-2694`, PR 2695's real head
is `session/dev-41a59eee`, and `git ls-remote --heads origin refs/heads/session/sdlc-2694`
returns empty. The critique's direction was right and its count was right at the time; the
live number today is 1.

**Root cause: two slug vocabularies, and adoption is partial rather than absent.**
`tools/lane_identity.py` records a lane's slug two ways. `adopt_lane_slug` takes a branch
name the caller **already knows** and records it verbatim; `resolve_lane_slug`'s discovery
ladder probes only the issue-derived name and, on a miss, mints `sdlc-{N}`. `adopt_lane_slug`'s
own docstring names the resulting divergence as "the defect this module closes."
#2738 shows adoption working end to end; #2694 shows a site that knew the branch and did not
record it. **That coverage gap is a real, separate bug and is now
[#2869](https://github.com/tomcounsell/ai/issues/2869)** — it is not fixed here, and WS-B is
correct whether or not it is ever fixed. (If #2869 lands, more recorded slugs name real
branches, which makes WS-B resolve *more* answers authoritatively, never fewer.)

**The revised mechanism.** Assert head-ref authority only when the branch is real:

```python
branch = lane_branch_name(slug)
if branch and lane_branch_exists_on_remote(branch):
    return _gh_pr_list(["--head", branch, "--state", state], repo=repo)
# otherwise fall through to the existing fuzzy ladder, unchanged
```

**This keeps the round-1 rule that must not be broken.** The gate is still never *"the
head-ref lookup returned `None`"* — conflating "no PR on this branch" with "no branch" would
suppress the fuzzy leg for every slugless lane and regress resolution across the board.
Branch **existence** is a categorically different signal from lookup **result**: it is
evidence about whether the slug names anything real, which is precisely the failure mode
#2869 describes. A slug that names a nonexistent branch has no authority to assert, so the
lookup falls back to fuzzy search and loses nothing.

**Why the existence check costs nothing in the scenario WS-B exists to fix.** #2824's harm
is: reopened issue, this lifecycle's lane branch pushed, no PR on it yet, fuzzy search
returns the *prior* lifecycle's merged PR. The premise of that scenario is **that the branch
exists** — so the existence check is satisfied exactly when the fix needs to fire. Measured
on `f491306c5` with `{ISSUE,PLAN,CRITIQUE: completed, BUILD: in_progress}`,
`branch_exists=True`:

| `pr_number` | Route |
|---|---|
| `2516` (stale, prior lifecycle) | `Dispatch(/do-pr-review, row 7)` — review a finished PR |
| `None` (what WS-B returns) | `Dispatch(/do-build, row 5)` — **create the PR this lifecycle needs** |

**Why this workstream stays in the lane rather than being cut.** Its measured effect on live
data is **zero, not negative** — 0 gains, 0 losses, 11 identical resolutions — so the "do not
ship a net-negative workstream" bar is cleared. But zero measured gain is a weak reason to
ship on its own, and the real reason is an interaction with BLOCKER 1's fix: **the terminal
guard now deliberately stands down on an OPEN issue**, which hands every reopened ledger
straight back to the fuzzy-resolution path. WS-A no longer fences the reopened case (by
design — that is what stops the permanent latch), so WS-B is the **only** remaining
protection for it. Cutting WS-B would leave the reopened-issue harm unguarded on both sides
of the lane at once.

**Implementation note — where the helper lives.** `_check_branch_pushed`
(`tools/sdlc_next_skill.py:157`) is exactly this check and is already battle-tested, but
`sdlc_next_skill` imports `sdlc_stage_query`, so importing it back would be circular. Add
`lane_branch_exists_on_remote(branch)` to `tools/lane_identity.py` instead — it is already
the sole owner of the `session/` prefix, it already runs `git ls-remote --heads origin`
(`:211`), and `sdlc_stage_query` already imports from it (`:60-61`) with no reverse edge
(verified). Give it the same disposition as `_check_branch_pushed`: return `False` on
non-zero exit or timeout, never raise. Memoize per `_lookup_pr` call chain so the two-pass
lookup (`open` then `merged`) pays **one** `ls-remote`, not two.

**State honestly what this does not cover.** Both live #2824 candidates (#2494, #2518)
carry `slug: null / slug_source: "unresolved"`, so WS-B does not fix them, and the existence
check does not change that — a lane with no slug has no branch to check. Claiming otherwise
would be false.

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
- [ ] Terminal guard against `issue_state` in `{"OPEN", None, "", "closed", "CLOSED_STALE", 42}` on an otherwise fully terminal ledger — all must return `None`; only the exact `"CLOSED"` terminates (Risk 6). Lowercase `"closed"` is in the set deliberately: `gh issue view --json state` emits uppercase, and a case-insensitive match would be a second, silently divergent definition of the same fact.
- [ ] Terminal guard with `meta=None` and `meta={}` — must return `None` without raising, since the guard reads `meta` before delegating and `evaluate_guards` wraps nothing.
- [ ] `_compute_meta` with a `gh issue view` that exits non-zero, times out, or emits unparseable JSON — `issue_state` must be `None` and the whole projection must still return, per `_compute_meta`'s stated "degrade rather than raise" contract.
- [ ] `lane_branch_exists_on_remote` with a raising / timing-out / non-zero `subprocess.run` — returns `False`, never raises, so `_lookup_pr`'s documented "never raises" contract holds through the new leg.
- [ ] `_lookup_pr` with `slug=""` and `slug="   "` — `_nonempty`/`lane_branch_name` treat both as absent, so the fuzzy leg must still run. Assert by **call count**, not by result equality, or the test passes vacuously.
- [ ] `_gh_pr_search_issue_ref` with an empty candidate list, a list of non-dicts, and candidates missing the new sort key — no raise, `None` returned.

### Error State Rendering
- [ ] The terminal JSON from `decide()` must be distinguishable from an escalation `Blocked` by a machine consumer. Assert the exact key set of both shapes, not just that the reason string differs.
- [ ] The terminal reason string must not tell a human to run `sdlc-tool dispatch reset` — that is G4's remedy and it is wrong for a finished pipeline. Assert its absence explicitly.

## Test Impact

- [ ] **`tests/unit/test_sdlc_router_decision.py::TestNoRuleBlockIsDistinguishable` (`:1691`) — UPDATE, and this is the one router test file WS-A provably reds.** Round 1 omitted this file entirely; it is the repo's primary router-decision suite (**97** `decide_next_dispatch` call sites, verified) and holds the **only** `"MERGE": "completed"` fixture in any router test (verified: `grep -c` returns **1** here and **0** in `test_sdlc_router_oscillation.py`). Its `_unowned_state()` helper builds PLAN/CRITIQUE/BUILD/REVIEW/DOCS/MERGE all completed plus an APPROVED verdict, with `meta = {"pr_number": 4242, "pr_merge_state": "BLOCKED"}`. `test_no_rule_block_uses_no_rule_sentinel` asserts `result.guard_id == "NO_RULE"` and fails outright once a first-position guard returns `TERMINAL`; `test_no_rule_block_renders_distinguishably_from_a_guard_block` consumes the same helper. **Disposition: re-key `_unowned_state()` onto a non-terminal unowned ledger — drop the `"MERGE": "completed"` entry, keep everything else including `pr_merge_state: "BLOCKED"`.** Do **not** flip the assertion to `"TERMINAL"`: these two tests are the only proof of the `NO_RULE` sentinel's distinguishability (#2767b), and flipping them retires that guarantee instead of preserving it. **The re-key is verified to work** — measured on `f491306c5`, the helper's ledger returns `Blocked(reason='no matching dispatch rule', guard_id='NO_RULE')` with `MERGE` completed, with `MERGE` absent, **and** with `MERGE: "pending"`, so dropping the key leaves both assertions passing for the same reason they pass today. (These tests will additionally be insulated by the `issue_state` gate, since the fixture's `meta` carries no `issue_state` and the guard requires `"CLOSED"` — but the re-key is still the correct disposition, because a fixture should not depend on a gate to stay meaningful.)
- [ ] `tests/unit/test_sdlc_router_oscillation.py` — **NO CHANGE EXPECTED, verify rather than assume.** Round 1 listed two UPDATE dispositions against this file; both were **vacuous** — it contains zero `"MERGE": "completed"` fixtures (verified), so no fixture in it can reach the terminal guard. Re-run it and confirm green. If anything in it does move, that is a signal the guard is placed wrong, not a fixture to patch.
- [ ] `tests/unit/test_sdlc_router_decision.py` — **REVIEW (the other 96 call sites)**: sweep for any other fixture that sets MERGE completed indirectly (via a helper or a `dict` update) rather than by the literal the grep counts. The grep bounds literals, not construction.
- [ ] `tests/unit/test_sdlc_stage_query.py:270-400` — **UPDATE**: the D4 resolution-order tests (`_lookup_pr` issue-search primary + branch-head fallback) encode the old ladder order directly, several by `assert_called_once` / call-count on the search leg. WS-B inverts the order when a slug is present; these must be rewritten to assert the new contract, and a new case added for "slug present → fuzzy leg never called".
- [ ] `tests/unit/test_sdlc_stage_query.py:364-399` — **NO CHANGE** (was UPDATE before WS-D was deferred). `_gh_pr_search_issue_ref`'s "first body-validating candidate" contract is unchanged by this lane; these tests keep asserting it. [#2868](https://github.com/tomcounsell/ai/issues/2868) owns rewriting them.
- [ ] `tests/unit/test_sdlc_stage_query.py:1436-1520` — **REVIEW**: the #2757 two-pass block. WS-B changes the ladder *above* it, so re-run and confirm the two-pass cases still hold; the comment at `:1517` documenting "returns the FIRST body-validating candidate" stays accurate under this lane and needs only the #2868 pointer added by the Documentation task.
- [ ] `tests/unit/test_sdlc_stage_marker.py:1086-1210` — **UPDATE**: several cases patch `_lookup_pr` and assert its call kwargs; `:1113` already pins `return_value=2538`. The `state="all"` kwarg assertion becomes `state="merged"`. **Add** a case pinning the scope by kwarg so a future widening re-reds.
- [ ] `tests/unit/test_pipeline_complete_predicate.py` — **UPDATE (additive)**: add the router's exact call shape (`outcome="success"`, `pr_open=None`) as a pinned case, so a future change to the predicate's default behavior surfaces as a router-facing failure rather than a silent routing change.
- [ ] `tests/unit/test_architectural_constraints.py` — **UPDATE**: extend the router import-boundary class with an explicit positive assertion that importing `agent.pipeline_complete` is allowed, so the new import is documented as intentional and a future blanket tightening does not silently forbid it.
- [ ] `tests/unit/test_sdlc_next_skill.py:1631` — **REVIEW**: patches `_lookup_pr` to `None`; confirm the terminal shape does not change its expectation, and update if it does.
- [ ] `tests/integration/test_sdlc_session_ensure_integration.py` — **REVIEW**: **10** tests (round-1 said 11), green today. Re-run; no expected change, but they exercise the marker path WS-C touches. **This file holds `test_terminal_merged_pipeline_routes_to_merge_not_build` at `:498`** — round 1 attributed it to `tests/unit/test_sdlc_router_oscillation.py`, which is wrong (citation corrected at round-2 critique). The plan's claim *about* the fixture is correct: its docstring reads "No `MERGE` marker is set, deliberately", so it should survive WS-A untouched. **Verify that rather than assume it** — if it changes, the guard is placed wrong.
- [ ] `tests/unit/test_sdlc_stage_query.py` — **UPDATE (new in round 2)**: add cases for the branch-existence precondition — recorded slug + branch exists → head-ref authoritative, fuzzy leg never called; recorded slug + branch absent → fuzzy leg called exactly once. Assert by call count on both legs. Patch the existence helper rather than shelling to `git` so the test is hermetic.
- [ ] `tests/unit/test_lane_identity.py` — **UPDATE (new in round 2)**: cover `lane_branch_exists_on_remote` — `True` on a matching `ls-remote` line, `False` on empty stdout, `False` on non-zero exit, `False` on timeout, and never raises.
- [ ] `tests/unit/test_sdlc_stage_query.py` — **UPDATE (new in round 2)**: cover the `issue_state` meta key — present and resolved only when `MERGE == "completed"`, absent/`None` otherwise, and `None` rather than a raise when the `gh` call fails.

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
- **Fixing the lane-slug adoption gap while WS-B is open.** #2694's slug is `sdlc-2694`
  while its PR's head is `session/dev-41a59eee`, and the temptation is to chase the write
  site that failed to call `adopt_lane_slug`. That is a real bug and it is
  [#2869](https://github.com/tomcounsell/ai/issues/2869). It is **not** a prerequisite:
  WS-B's existence check makes a minted-but-wrong slug degrade to today's behavior rather
  than to a wrong answer, so the two are independent in both directions. Fixing #2869 later
  strictly increases how often WS-B resolves authoritatively; it can never invalidate it.
- **Making the terminal guard "smarter" about re-entry than one `issue_state` string.**
  Reopen timestamps, comparing the merge time against the reopen time, per-lifecycle ledger
  keys — all plausible, all strictly more machinery than the failure needs. `CLOSED` versus
  not-`CLOSED` fails in the safe direction and costs zero measured coverage (16/16). If a
  second terminal reason ever needs finer state, revisit then.
- **Un-writing `MERGE = completed` so a reopened lane starts clean.** The obvious "real"
  fix for Risk 6, and the reason it is a rabbit hole is that every route to it is worse than
  the gate: a new backwards `--status` value widens a CLI contract three other tools read,
  and zeroing `stage_states_json` is documented as a clobber
  (`docs/features/sdlc-issue-keyed-stage-ledger.md:324`). Per-lifecycle ledger identity is
  the honest long-term answer and it belongs to #2491's pipeline-graph work, not here.
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

**And the write path plus the write-time ledger shape are still only two thirds of the
risk. The third is *time*: what happens to this ledger on a tick that arrives weeks later.**
That is Risk 6, added at round-2 critique, and it is the reason the guard carries an
issue-state gate rather than keying on `MERGE` alone.

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
**Mitigation:** Gate on *slug recorded* **and** *branch exists on origin*, never on
*head-ref returned `None`*. Then measure before merging.

**Round 2 measured this risk as real and the existence check as the thing that retires
it.** Under the round-1 shape the risk was not hypothetical: it scored 0 gains / 1 loss on
live data, i.e. it fired only in the harmful direction. Under the round-2 shape it scores
0 / 0. The mechanism is why: the round-1 shape asserted authority on a branch name that
might not exist, and a nonexistent branch trivially has no PR, so `None` was returned with
false confidence. Existence is exactly the missing precondition.

**The acceptance bar, stated once and only here.** Success Criteria references this
section rather than restating a number, because round 1 left two different bars in two
places and the plan was red under one of them.

Corpus (runnable, not described) — the live ledger set, which is the population the
mechanism actually acts on:

```bash
./.venv/bin/python -c "
from agent.pipeline_ledger import PipelineLedger
print([int(r.issue_number) for r in PipelineLedger.query.all() if getattr(r,'slug',None)])
"
```

For each such issue, resolve the PR under both ladder orders and classify every difference:

| Difference | Meaning | Disposition |
|---|---|---|
| Resolves old, not new; resolved PR's head ref **is** `session/{slug}` | The head-ref leg should have found this and did not — the mechanism is broken | **Blocks the change.** |
| Resolves old, not new; resolved PR's head ref is **not** `session/{slug}`, and `session/{slug}` **does not exist** on origin | The existence check should have fallen through to fuzzy and did not — the mechanism is broken | **Blocks the change.** New in round 2: under the existence-checked shape this is no longer an expected cost, it is a defect. |
| Resolves old, not new; resolved PR's head ref is **not** `session/{slug}`, and `session/{slug}` **does** exist on origin | Two branches for one lane; authority went to the real recorded one | **Expected cost, ceiling 1.** Report the issue numbers in the PR body. Measured today: **0**. More than one means the recorded-slug vocabulary is less trustworthy than #2869 assumes — stop and reassess rather than absorbing it. |
| Resolves new, not old | Lane-branch authority recovered something fuzzy search missed | Report as a gain. Measured today: **0**. |

The stated ceiling matters: round 1's "expected cost, does not block" bucket had no bound,
so an arbitrary regression could have been absorbed into it. Under the existence-checked
shape the expected-cost bucket should be empty, and it measures empty, so a ceiling of 1
is a real constraint rather than a formality.

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

### Risk 6: The terminal guard latches a re-entered issue shut, permanently
**Impact:** The worst outcome in this plan. `MERGE == "completed"` is durable and
un-writable-backwards, and `PipelineLedger.get_or_create(target_repo, issue_number)` returns
the same ledger for a second lifecycle, so a reopened issue would terminate on every tick
forever with **no sanctioned remedy** — `stage-marker --status` offers no backwards
transition (`tools/sdlc_stage_marker.py:957`), `dispatch reset` clears `_sdlc_dispatches`
only (`tools/sdlc_dispatch.py:296`), and hand-zeroing `stage_states_json` is documented as a
clobber (`docs/features/sdlc-issue-keyed-stage-ledger.md:324`). That is worse than G4's wrong
remedy, which this plan is careful to forbid: G4 at least *has* a remedy.

**Reachability (measured, not argued):** 17 reopened issues in the repo, 8 with a merged PR
that merged before the reopen (spike-4). **16 of 36 live `PipelineLedger` rows carry
`MERGE == "completed"` today.** No live wedge exists yet only because all 16 of those issues
are currently CLOSED — one reopen would create one.

**Mitigation:** The guard requires **positive `CLOSED` evidence**, not merely the absence of
`OPEN`. `_compute_meta` resolves an `issue_state` key (only when `MERGE == "completed"`, so
non-terminal ticks pay nothing), and the guard returns `None` on anything that is not the
exact string `"CLOSED"` — including `None`, which is what a `gh` failure yields. A reopened
issue is `OPEN`, so the guard stands down and the ledger routes exactly as it does today.
Full reasoning, including why the `== "OPEN"` polarity would reintroduce the latch through a
transient network failure, is in *Technical Approach → WS-A*.

**Direction of failure:** fail-open, into today's well-understood behavior. The guard can
only ever *decline* to terminate when it is unsure.

**Verification:** a parametrized test asserting the guard returns `None` for
`issue_state` in `{"OPEN", None, "", "closed", "CLOSED_STALE", 42}` on an otherwise fully
terminal ledger, and `Blocked(guard_id="TERMINAL")` for exactly `"CLOSED"`. Lowercase
`"closed"` is in the set deliberately: `gh issue view --json state` emits uppercase, and a
case-insensitive match here would be a second, silently divergent definition of the same
fact.

**Interaction to build against:** this mitigation is what makes WS-B load-bearing. By
standing down on an OPEN issue, WS-A hands every reopened ledger back to the fuzzy PR
resolution that WS-B fixes. The two workstreams cover the reopened case together and neither
covers it alone.

## Race Conditions

**No new race conditions identified.** All three shipped workstreams are synchronous,
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

No update-system changes required. All three shipped workstreams are edits to existing Python
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
- [ ] **Update `.claude/skills/sdlc/SKILL.md:261` — the router skill's `blocked` contract.
      Unconditional, same commit as WS-A. Not a checkbox gated on a parity test.**
      That line today reads: "If `blocked` is `true`: surface the `reason` to the human and
      wait. Do NOT loop or guess an alternative skill." (verified verbatim). It is the
      *identical* instruction to `.claude/skills-global/do-sdlc/SKILL.md:136`, so leaving it
      unfixed turns WS-A into an immediate false escalation for every `/sdlc`-routed lane —
      exactly the regression the `do-sdlc` edit exists to prevent, arriving through the other
      door. Add the `{"blocked": true, "complete": true, "guard_id": "TERMINAL", ...}` case →
      *exit reporting **success**, not escalation*.

      Round 1 deferred this to "if the parity test requires it". **That question is now
      answered and the answer is that the checkbox could never have fired**, so the deferral
      was a guaranteed no-op dressed as a conditional:
      `tests/unit/test_sdlc_skill_md_parity.py::test_guard_row_ids_in_python` runs one
      direction only — it parses guard rows *out of* SKILL.md and asserts each has a matching
      callable in `GUARDS`, so adding a callable with no SKILL.md row cannot fail it. And its
      row regex is `_GUARD_ROW_RE = re.compile(r"^G\d+")` (`:176`, verified), which cannot
      parse a `TERMINAL` id as a row at all. Meanwhile the same module already declares
      `ROUTER_CONSUMER_SKILLS = (SKILL_MD, DO_SDLC_MD)` (`:33`), so the repo has **already
      decided both bodies are router consumers**. The parity test is not the authority on
      this edit; that declaration is.

      Note this is a **project-only** skill (`.claude/skills/`, never synced), unlike the
      `do-sdlc` global body — so the two edits have different wording constraints even though
      they carry the same instruction. Keep `do-sdlc`'s generic; this one may name repo
      specifics.
- [ ] Consider adding a `TERMINAL` row to `.claude/skills/sdlc/SKILL.md`'s guard table for
      human readers. **Optional and explicitly not load-bearing** — `_GUARD_ROW_RE` cannot
      parse the id, so such a row is documentation only and no test will hold it accurate.
      If added, it must not be described as parity-enforced.
- [ ] Update `docs/sdlc/do-merge.md` if the terminal verdict changes what a supervisor
      does after the MERGE marker lands.

## Success Criteria

- [ ] A terminal ledger (`MERGE == completed` **and `issue_state == "CLOSED"`**) returns an
      explicit terminal verdict from `decide_next_dispatch` in **all eight** cells of
      spike-1's matrix **and in the ninth `PATCH`-unsettled cell** — no `/do-build`, no
      `/do-pr-review`, no `/do-merge`, no `NO_RULE`.
- [ ] **The terminal guard never latches a re-entered issue.** On an otherwise fully
      terminal ledger it returns `None` for every `issue_state` other than the exact string
      `"CLOSED"` — parametrized over `{"OPEN", None, "", "closed", "CLOSED_STALE", 42}` —
      and `Blocked(guard_id="TERMINAL")` for `"CLOSED"` alone. This is the Risk 6 bar; a
      guard that fires on an unresolvable issue state fails it.
- [ ] `_compute_meta` resolves `issue_state` **only** when `MERGE == "completed"`, and
      degrades to `None` rather than raising when the `gh` call fails.
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
- [ ] **WS-B's ladder diff meets Risk 2's bar — which is stated in Risk 2 and nowhere
      else.** Round 1 stated two different bars in two places: this criterion demanded
      "**zero** issues that resolve under the old order and fail under the new one" while
      Risk 2's revised table classified exactly that shape as "expected cost… does not
      block." The plan was **red today under the stricter reading** — the round-1 mechanism
      measured 0 gains / 1 loss. Both are now superseded: the existence-checked mechanism
      measures **0 gains / 0 losses / 11 unchanged**, and Risk 2 carries the single
      authoritative bar (two blocking shapes, one expected-cost bucket with a stated ceiling
      of 1). Do not restate a number here.
- [ ] `_lookup_pr` asserts head-ref authority **only** when the recorded slug names a branch
      that exists on origin; a recorded slug naming a nonexistent branch falls through to the
      fuzzy leg (asserted by call count on both legs, both directions).
- [ ] The two-pass lookup (`open` then `merged`) performs **one** branch-existence check,
      not two.
- [ ] `_review_artifact_posted(1785)` and `_review_artifact_posted(2073)` return
      **False**; `_review_artifact_posted(2104)` returns **True** via merged PR 2109.
- [ ] The #2539 control holds 5/5 live: #2860, #2831, #2716, #2734, #2741.
- [ ] **KNOWN-UNCOVERED — the two live #2824 candidates stay broken, by design.**
      #2494 and #2518 are the only OPEN reopened issues carrying a stale prior-lifecycle
      PR, and `sdlc-tool stage-query` reports `slug: null, slug_source: "unresolved"` for
      both. WS-B keys on a *recorded* slug **naming a branch that exists**, so **it does not
      fix either of them** — a lane with no slug has no branch to check. This lane closes
      #2824 as "the mechanism is correct going forward", not as "the two live instances are
      repaired"; backfilling their ledgers to make the fix look complete is an explicit
      Rabbit Hole.
      **Round-2 addendum — the forward-looking claim is narrower than round 1 stated.**
      "It fixes every future lane" was too strong: measured over every slugged ledger on this
      machine, the existence-checked mechanism changes **zero** answers today (0 gains,
      0 losses, 11 unchanged). It fixes every future lane *whose recorded slug names a branch
      that exists*, and [#2869](https://github.com/tomcounsell/ai/issues/2869) is how that
      population grows. WS-B's value in this PR is that it makes the reopened-issue path
      correct while costing nothing measurable — not that it repairs something observable
      today. Say exactly that in task 8's issue comment.
- [ ] The terminal shape is consumed correctly end to end by **both** router consumers:
      `.claude/skills-global/do-sdlc/SKILL.md` **and** `.claude/skills/sdlc/SKILL.md` route
      `complete: true` to loop-exit-success, not to human escalation. Both edits land in the
      same commit as WS-A; neither is conditional.
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

### 2. Write the failing tests first (all three workstreams)
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
- **Validates**: tests/unit/test_sdlc_router_decision.py, tests/unit/test_sdlc_router_oscillation.py, tests/unit/test_pipeline_complete_predicate.py, tests/unit/test_architectural_constraints.py, tests/unit/test_sdlc_stage_query.py (the `issue_state` key)
- **Informed By**: spike-1 (must be a guard, not a row), spike-3 (delegate to `is_pipeline_complete`; do not define terminal twice), Risk 6 (the issue-state gate is not optional)
- **Assigned To**: `router-builder`
- **Agent Type**: builder
- **Parallel**: true
- **Open the guard body with `if not isinstance(stage_states, dict): return None`** before delegating. `evaluate_guards` has no try/except and `is_pipeline_complete` calls `.get` on its first argument. Return `None`, never `Blocked` — an unreadable ledger falls through to the table.
- **Then gate on `(meta or {}).get("issue_state") != "CLOSED": return None`** (Risk 6). Exact-string, case-sensitive, positive evidence only. **Do not write the `== "OPEN"` form** — it terminates on an unresolvable state and reintroduces the permanent latch through a transient `gh` failure.
- Add the `issue_state` key to `_compute_meta` (`tools/sdlc_stage_query.py:515`), resolved **only when `MERGE == "completed"`** so non-terminal ticks cost nothing, memoized once per invocation inside the existing `cached_target_repo_resolution()` scope, degrading to `None` on any failure rather than raising.
- Then delegate to `agent.pipeline_complete.is_pipeline_complete(stage_states, "success", pr_open=None)`; ship the `merge_success` leg only.
- Insert it **first in `GUARDS`, ahead of G1** (Resolved Question 1), with the rationale in the docstring.
- Map the terminal `guard_id` to its JSON shape in `tools/sdlc_next_skill.py::decide()` — an additive `complete: true` key alongside `blocked` (Resolved Question 2).
- **Update BOTH router-consumer skill bodies in this same commit** — `.claude/skills-global/do-sdlc/SKILL.md:136` **and** `.claude/skills/sdlc/SKILL.md:261` — so each exits reporting success on `complete: true` instead of escalating. They carry the identical `blocked` instruction; fixing one and not the other leaves the regression live on the other path. Neither is conditional on the parity test, which provably cannot hold either of them (`_GUARD_ROW_RE` is `^G\d+`).
- **Re-key `tests/unit/test_sdlc_router_decision.py::TestNoRuleBlockIsDistinguishable._unowned_state()`** — drop `"MERGE": "completed"`, change nothing else. Verified to keep both assertions passing. Do **not** flip either assertion to `"TERMINAL"`; that retires the #2767b sentinel guarantee.
- Do **not** modify row 5, row 8e, or row 10 — the guard pre-empts them and they stay correct for non-terminal ledgers.
- Do **not** harden G1 against non-dict ledgers here; that fragility predates this lane and is out of scope.

### 4. WS-B — lane-branch authority
- **Task ID**: build-resolution-ladder
- **Depends On**: red-tests
- **Validates**: tests/unit/test_sdlc_stage_query.py, tests/unit/test_lane_identity.py
- **Informed By**: spike-4 (a bare reorder is a measured no-op; the suppression clause is the fix), Risk 2 as revised in round 2 (the unconditional form scores 0 gains / 1 loss on live data — do not build it)
- **Assigned To**: `resolution-builder`
- **Agent Type**: builder
- **Parallel**: true
- Add `lane_branch_exists_on_remote(branch)` to `tools/lane_identity.py` — **not** to `tools/sdlc_stage_query.py`, and **do not** import `_check_branch_pushed` from `tools/sdlc_next_skill.py` (that module imports `sdlc_stage_query`, so the edge would be circular; verified there is no reverse edge from `lane_identity` today). Mirror `_check_branch_pushed`'s disposition: `git ls-remote --heads origin <branch>`, `False` on non-zero exit or timeout, never raises.
- Gate head-ref authority on *slug recorded* **and** *branch exists on origin*. Never on *head-ref returned `None`* — `lane_branch_name(None)` returns `None`, so conflating "no slug" with "no PR on this branch" would suppress the fuzzy leg for every slugless lane.
- Memoize the existence check so the two-pass lookup (`open` then `merged`) pays **one** `ls-remote`, not two.
- **Do not build WS-D here.** Deterministic candidate ordering is deferred to [#2868](https://github.com/tomcounsell/ai/issues/2868); leave `_gh_pr_search_issue_ref`'s selection logic alone. Widening the `--json` field list is part of that deferred work, not this task.
- **Do not fix the slug-adoption gap here** either — that is [#2869](https://github.com/tomcounsell/ai/issues/2869). WS-B is deliberately correct *despite* a minted-but-wrong slug, which is the whole point of the existence check.
- Run Risk 2's counterexample check over the live slugged-ledger corpus and attach the classified diff. Two shapes block: a lost resolution whose PR head **is** `session/{slug}`, and a lost resolution where `session/{slug}` **does not exist** on origin. The expected-cost bucket has a **ceiling of 1** and measures **0** today.

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
| Terminal verdict, no `pr_number`, no branch | `./.venv/bin/python -c "from agent.sdlc_router import decide_next_dispatch as d; s={k:'completed' for k in ['ISSUE','PLAN','CRITIQUE','BUILD','TEST','REVIEW','DOCS','PATCH','MERGE']}; print(d(s,{'issue_state':'CLOSED'},{'branch_exists':False}))"` | output contains `TERMINAL` |
| Terminal verdict, no `pr_number`, branch exists (the false rebuild) | `./.venv/bin/python -c "from agent.sdlc_router import decide_next_dispatch as d; s={k:'completed' for k in ['ISSUE','PLAN','CRITIQUE','BUILD','TEST','REVIEW','DOCS','PATCH','MERGE']}; print(d(s,{'issue_state':'CLOSED'},{'branch_exists':True}))"` | output contains `TERMINAL` and does not contain `/do-build`. **Both legs** — asserting only the absence passes vacuously today (`/do-build` is what it returns, but a crash would also omit the string). |
| Terminal verdict, `pr_number` present | `./.venv/bin/python -c "from agent.sdlc_router import decide_next_dispatch as d; s={k:'completed' for k in ['ISSUE','PLAN','CRITIQUE','BUILD','TEST','REVIEW','DOCS','PATCH','MERGE']}; s['_verdicts']={'REVIEW':{'verdict':'APPROVED','at':'2026-08-19T00:00:00Z'}}; print(d(s,{'pr_number':555,'issue_state':'CLOSED'},{'branch_exists':True}))"` | output contains `TERMINAL` and does not contain `/do-merge` |
| **Risk 6 — a reopened (OPEN) issue is NOT latched** | `./.venv/bin/python -c "from agent.sdlc_router import decide_next_dispatch as d; s={k:'completed' for k in ['ISSUE','PLAN','CRITIQUE','BUILD','TEST','REVIEW','DOCS','PATCH','MERGE']}; print(d(s,{'issue_state':'OPEN'},{'branch_exists':True}))"` | output does **not** contain `TERMINAL`. The single most important row in this table: a terminal ledger whose issue was reopened must route as it does today, not terminate forever. |
| **Risk 6 — an unresolvable issue state fails open** | `./.venv/bin/python -c "from agent.sdlc_router import decide_next_dispatch as d; s={k:'completed' for k in ['ISSUE','PLAN','CRITIQUE','BUILD','TEST','REVIEW','DOCS','PATCH','MERGE']}; print(all('TERMINAL' not in str(d(dict(s),m,{'branch_exists':True})) for m in [{},{'issue_state':None},{'issue_state':''},{'issue_state':'closed'},{'issue_state':42}]))"` | output contains `True`. Pins the `!= "CLOSED"` polarity: the `== "OPEN"` form would fail every case here. |
| **Risk 6 — all live terminal ledgers are still CLOSED (guard coverage unchanged)** | `./.venv/bin/python -c "from agent.pipeline_ledger import PipelineLedger; import json,subprocess; ns=[int(r.issue_number) for r in PipelineLedger.query.all() if json.loads(r.stage_states_json or '{}').get('MERGE')=='completed']; print(len(ns), sum(subprocess.run(['gh','issue','view',str(n),'--json','state','-q','.state'],capture_output=True,text=True).stdout.strip()=='CLOSED' for n in ns))"` | both numbers equal. Measured 2026-08-19: **16 16**. A drop means the gate is now costing real coverage and the polarity needs re-argument. |
| Negative control — non-terminal still routes to merge | `./.venv/bin/python -c "from agent.sdlc_router import decide_next_dispatch as d; s={k:'completed' for k in ['ISSUE','PLAN','CRITIQUE','BUILD','TEST','REVIEW','DOCS','PATCH']}; s['_verdicts']={'REVIEW':{'verdict':'APPROVED','at':'2026-08-19T00:00:00Z'}}; print(d(s,{'pr_number':555},{'branch_exists':False}))"` | output contains `/do-merge` |
| Terminal verdict, `PATCH` unsettled (ninth cell) | `./.venv/bin/python -c "from agent.sdlc_router import decide_next_dispatch as d; s={k:'completed' for k in ['ISSUE','PLAN','CRITIQUE','BUILD','TEST','REVIEW','DOCS','MERGE']}; s['PATCH']='pending'; s['_verdicts']={'REVIEW':{'verdict':'APPROVED','at':'2026-08-19T00:00:00Z'}}; print(d(s,{'pr_number':555,'issue_state':'CLOSED'},{}))"` | output contains `TERMINAL` (red today: returns `/do-merge` row 10) |
| Terminal guard survives a non-dict ledger | `./.venv/bin/python -c "from agent.sdlc_router import guard_terminal_pipeline as g; print(all(g(b,{},{}) is None for b in [None,'x',42,[]]))"` | output contains `True`. Asserts **the new guard alone**, not `evaluate_guards` — see the note under the table. |
| Terminal reason does not give G4's wrong remedy | `./.venv/bin/python -c "from agent.sdlc_router import decide_next_dispatch as d; s={k:'completed' for k in ['ISSUE','PLAN','CRITIQUE','BUILD','TEST','REVIEW','DOCS','PATCH','MERGE']}; r=str(d(s,{'issue_state':'CLOSED'},{})); print('TERMINAL' in r and 'dispatch reset' not in r)"` | output contains `True` — **both legs**: `TERMINAL` present AND `dispatch reset` absent. Asserting only the absence passes vacuously today (`Blocked(NO_RULE)` contains neither), which is what the critique caught. |
| **WS-B — authority asserted when the branch exists** | `./.venv/bin/python -c "from unittest.mock import patch; import tools.sdlc_stage_query as q; f=patch.object(q,'_gh_pr_search_issue_ref',return_value=None).start(); l=patch.object(q,'_gh_pr_list',return_value=None).start(); patch.object(q,'lane_branch_exists_on_remote',return_value=True).start(); q._lookup_pr(2739, slug='sdlc-2739', repo='tomcounsell/ai'); print(f'fuzzy={f.call_count} head={l.call_count}')"` | output contains `fuzzy=0 head=1` |
| **WS-B — authority declined when the branch does NOT exist (round-2 blocker fix)** | `./.venv/bin/python -c "from unittest.mock import patch; import tools.sdlc_stage_query as q; f=patch.object(q,'_gh_pr_search_issue_ref',return_value=None).start(); patch.object(q,'lane_branch_exists_on_remote',return_value=False).start(); q._lookup_pr(2694, slug='sdlc-2694', repo='tomcounsell/ai'); print(f'fuzzy={f.call_count}')"` | output contains `fuzzy=1`. The row that pins the fix: without it, `sdlc-2694` (a slug naming a branch that does not exist) suppresses the fuzzy leg and loses PR 2695. |
| **WS-B — the measured live regression is gone** | `./.venv/bin/python -c "import tools.sdlc_stage_query as q; print(q._lookup_pr(2694, slug='sdlc-2694', repo='tomcounsell/ai', state='merged'))"` | output contains `2695`. **Red today under the round-1 shape** (returns `None`); green under the existence-checked shape and green on unmodified `main`. |
| **WS-B — one existence check per two-pass lookup** | `./.venv/bin/python -c "from unittest.mock import patch; import tools.sdlc_stage_query as q; e=patch.object(q,'lane_branch_exists_on_remote',return_value=True).start(); patch.object(q,'_gh_pr_list',return_value=None).start(); q._compute_meta({'MERGE':'pending'}, None, 2739); print(f'ls_remote_calls={e.call_count}')"` | output contains `ls_remote_calls=1` (not 2) |
| **WS-B — no live resolution is lost across the slugged corpus** | Re-run Risk 2's classified ladder diff over every `PipelineLedger` with a recorded slug | `GAINS=0 LOSSES=0 SAME=11` (measured 2026-08-19). Any LOSS blocks; see Risk 2 for the two blocking shapes and the ceiling-1 expected-cost bucket. |
| `issue_state` resolved only on a terminal ledger | `./.venv/bin/python -c "from unittest.mock import patch; import tools.sdlc_stage_query as q; import subprocess; c=patch.object(subprocess,'run',wraps=subprocess.run).start(); m=q._compute_meta({'MERGE':'pending'}, None, 2739); print('issue_state' in m and m.get('issue_state') is not None)"` | output contains `False` — a non-terminal ledger pays no `gh issue view`. |
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

### Note: `stage_states_json` is flat — the `stages` wrapper belongs to the CLI projection only

Caught while writing the Risk 6 rows above, and recorded because it silently manufactures a
vacuous gate. `PipelineLedger.stage_states_json` stores stage names as **top-level** keys
(`{"ISSUE": ..., "MERGE": "pending", "_patch_cycle_count": 0, ...}`). The `{"stages": {...}}`
envelope that `sdlc-tool stage-query` emits is a *projection*, built by the CLI — it is not
what is on disk.

A row written as `json.loads(r.stage_states_json).get("stages", {}).get("MERGE")` therefore
matches **nothing**, returns an empty population, and any `assert count == len(population)`
over it passes as `0 == 0`. Both Risk 6 rows were briefly written that way and measured
`0 0` before being corrected to read the flat shape, at which point they measured the real
`16 16`. Anyone extending these rows must read the flat shape, and must sanity-check that
the population is **non-empty** before trusting a comparison over it — an all-pass over an
empty set is the #2091 stale-fixture problem wearing a different hat.

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

War room round 3, run 2026-08-19 against `main` @ `ceb49fa9c`. Depth **FULL** (force-FULL:
the plan touches the doctrine paths `agent/sdlc_router.py`, `.claude/skills/`, and
`.claude/skills-global/`). Roster 3/3 complete, 0 ungrounded. **2 blockers, 3 concerns, 2 nits.**

**Execution note, recorded because it bears on how much independence this round carries.**
No agent-spawn tool was available in the round-3 driver's session, so the three lenses were
executed by the driver itself rather than by three independently dispatched critics. The
membership + grounding gate still ran and passed 3/3, and every finding below carries a
command that was run against `ceb49fa9c` rather than a reasoned claim — but a reader should
weight this round as one agent applying three lenses, not three agents disagreeing.

**Baseline re-verified before critiquing.** Zero commits between the plan's stated baseline
`f491306c5` and `ceb49fa9c` touch `agent/sdlc_router.py`, `tools/sdlc_stage_query.py`,
`tools/sdlc_stage_marker.py`, `tools/sdlc_next_skill.py`, `tools/lane_identity.py`,
`agent/pipeline_complete.py`, or either router-consumer SKILL.md. Every "red today" claim in
the Verification table that was re-run reproduces: the eight-cell matrix, the ninth
PATCH-unsettled cell (`/do-merge` row 10), the non-dict `AttributeError` at G1, both WS-B
call-count rows, and all three live `_lookup_pr` fixtures (2494 to 2516, 2518 to 2538,
2694 to 2695). Rounds 1 and 2 remain resolved; nothing below re-litigates them.

**Where round 3 converges.** All three lenses independently landed on **WS-B**, from three
directions: its acceptance contract is stated two contradictory ways (blocker 2), its
memoization scope is unspecified and incompatible with its own verification row (concern 1),
and its measured value is zero while its cost is unmeasured (concern 2). None of the three
falsifies the mechanism. Together they say WS-B is the least-finished third of the lane, and
the revision should either specify it completely or defer it to #2869 and ship WS-A + WS-C.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | The `issue_state` gate — the mitigation built for Risk 6 — is specified as a bare `gh issue view N --json state` with no `--repo` scoping, inside a function where every other GitHub call threads the resolved repo (`:192`, `:242`, `:316`, `:601`). A bare `gh issue view` resolves through gh's own ladder (`GH_REPO` env before cwd), so under `GH_REPO`/`SDLC_TARGET_REPO` it can return a different repository's issue #N, exit 0, and hand the guard a confident wrong answer. A foreign CLOSED issue then terminates a live lane — the outcome Risk 1 calls "the worst possible failure for this change" — reached through the gate built to prevent it. The plan cites #1642 ("a lookup that fails soft and disarms a gate") as prior art and then repeats the signature in the arming direction. The fail-soft-to-`None` contract does not cover this: a wrong-repo lookup succeeds. | pending | Build the argv exactly like `_fetch_pr_merge_state` at `tools/sdlc_stage_query.py:192`: `cmd = ["gh","issue","view",str(issue_number), *(["--repo",resolved_repo] if resolved_repo else []), "--json","state","-q",".state"]`. Then whitelist the parse: `state = out.strip(); issue_state = state if state in ("OPEN","CLOSED") else None` — the `!= "CLOSED"` polarity is only fail-open if a garbage value cannot be the literal `"CLOSED"`. Fix the two rows that shell out unscoped too (Verification row "Risk 6 — all live terminal ledgers are still CLOSED" and the matching Prerequisites row), or the 16/16 coverage measurement is not reproducible under a set `GH_REPO`. |
| BLOCKER | History & Consistency | Two Success Criteria thirteen lines apart state mutually exclusive contracts for `_lookup_pr`. One: "with a recorded slug never calls the fuzzy search leg (asserted by call count)." The other: head-ref authority applies "**only** when the recorded slug names a branch that exists on origin; a recorded slug naming a nonexistent branch falls through to the fuzzy leg." The first is the round-1 mechanism measured at 0 gains / 1 loss, which task 4 explicitly forbids building; the second is the shipped mechanism. A builder satisfying the first rebuilds the falsified shape and re-breaks #2694. This is round-2 concern 4's defect ("state the bar in exactly one place") swept for the ladder-diff bar and left un-swept for the call-count bar. | pending | The invalidated round-1 rule survives in three places needing the same edit: (1) the Success Criterion "recorded slug never calls the fuzzy search leg" — delete it, the existence-checked criterion below already states both directions; (2) Verification row "WS-B — recorded slug never calls the fuzzy leg" — its title restates the invalid rule and its command stays green only by fixture luck, because `session/sdlc-2494` really does exist on origin (`git ls-remote` returns `28b0bf8b216aad9a096a5209a56132a56461c9a2`); (3) Test Impact's "a new case added for 'slug present → fuzzy leg never called'". The correct universal statement is the pair already in the two existence rows (branch exists → `fuzzy=0 head=1`; branch absent → `fuzzy=1`); every other phrasing should reference those rows rather than restate a rule. |
| CONCERN | Risk & Robustness | The branch-existence probe must be memoized so the two-pass lookup "pays **one** `ls-remote`, not two", but no scope is named, and the two candidates are mutually exclusive with the plan's own verification row. `_compute_meta` calls `_lookup_pr` as two separate top-level calls (`:596`, `:598`), so a memo scoped "per `_lookup_pr` call chain" yields 2 round trips. The in-repo idiom that yields 1 — the thread-local `cached_target_repo_resolution()` scope task 3 already prescribes for `issue_state` — is inert outside that scope, and the verification row calls `_compute_meta` bare. So the row measures 2 and fails against a correct request-scoped implementation, while the implementation that passes it (a process-level `lru_cache`) latches a "branch does not exist" answer across the build-push-PR sequence that makes it change. | pending | Mirror `_resolve_memo` in `tools/_sdlc_utils.py:106-146`: a `threading.local()` dict keyed on branch name, populated only when `getattr(_resolve_memo,"active",False)` is true and inert otherwise, so uncached behavior stays byte-identical outside a request. Then wrap the verification row: `with cached_target_repo_resolution(): q._compute_meta({'MERGE':'pending'}, None, 2739)`. Explicitly forbid `functools.lru_cache` in the task text — it is process-lifetime, and a lane whose branch is absent at tick N routinely has one at tick N+1 (the row-5 path this lane protects), so a cache outliving the request is the Risk 6 latch reached through the cache instead of the ledger. Measured cost this memo governs: 0.797s for a single-ref `ls-remote`, 0.847s for the full 545-head listing. |
| CONCERN | Scope & Value | WS-B ships with a measured value of zero and an unmeasured recurring cost. Its own numbers are 0 gains / 0 losses / 11 unchanged, and both live instances of the bug it names carry no slug — re-verified: `stage-query` reports `slug: None, slug_source: "unresolved"` for #2494 and #2518. The surviving justification is that WS-A now stands down on an OPEN issue "so WS-B is the **only** remaining protection" for the reopened case — but the reopened population *is* the slugless population, and a lane with no slug has no branch to check, so that protection is empty on the same measurement. What WS-B does add unconditionally is a ~0.8s round trip on the pre-PR hot path (`tools/sdlc_stage_query.py:570` routes to the lookup exactly when no PR exists yet), and the plan's cost analysis covers only WS-A's `gh` call. | pending | Either state the cost next to the zero measured value (one `ls-remote` per enriched tick of every slugged lane, ~0.8s, doubling without the memo fix above), or defer WS-B to #2869 — the slug-adoption fix that is the actual precondition for WS-B having a non-empty population. The deferral has no code coupling: WS-B's only edits are `lane_branch_exists_on_remote` in `tools/lane_identity.py` plus a ~5-line branch in `_lookup_pr`; WS-A touches the router and `_compute_meta`'s `issue_state` key, WS-C touches `tools/sdlc_stage_marker.py:263`, and neither reads the ladder order. If it is kept, write "correct-going-forward on a population that is currently empty and grows only via #2869" rather than "the only remaining protection", which reads as though it protects something today. |
| CONCERN | History & Consistency | The No-Go for the `docs_success_no_pr` terminal leg states a reason WS-A's own design falsifies: "it requires `pr_open`, which requires I/O in a router that must stay pure and `tools/`-import-free." WS-A demonstrates the opposite — a router-consumed fact requiring I/O is resolved upstream in `_compute_meta` and arrives as a plain value in `meta`, which the plan itself calls preserving purity "by construction rather than by discipline". `pr_open` is available on identical terms. Presenting a scope decision as an architectural impossibility means the next reader will not revisit it when they should. | pending | Restate the No-Go on its true grounds — the DOCS-terminal leg needs its own measurement pass (how many live ledgers are DOCS-completed with no open PR, and what they route to today) that this lane has not done — and drop the purity argument. The mechanically-equivalent shape, for the record so nobody re-derives it: `_compute_meta` sets `meta["pr_open"] = _check_pr_open(issue_number)` under the mirror-image gate (`DOCS == "completed" and MERGE != "completed"`), and the guard passes it through. `agent/pipeline_complete.py:88` already returns `None` on subprocess error, timeout, and malformed output, landing on the predicate's existing conservative `pr_state_unavailable` branch, so the fail-open direction is identical to the `issue_state` gate's. |
| NIT | Risk & Robustness | Task 4 tells the builder to give `lane_branch_exists_on_remote` "the same disposition as `_check_branch_pushed`: return `False` on non-zero exit **or timeout**, never raise." `_check_branch_pushed` (`tools/sdlc_next_skill.py:157-174`) has no `try`/`except` — a `TimeoutExpired` or `FileNotFoundError` propagates. Meanwhile the target module already contains `_ls_remote_heads()` (`tools/lane_identity.py:201-224`), which does have that disposition, and `_adopt_pushed_lane_branch` (`:288-293`) already implements the exact membership probe WS-B wants. | pending | (NIT — exempt.) Point the instruction at `_ls_remote_heads()` and `_adopt_pushed_lane_branch` instead; the helper is then three lines over an existing never-raising primitive. |
| NIT | History & Consistency | Two counts drifted. Test Impact says `tests/unit/test_sdlc_router_decision.py` has "**97** `decide_next_dispatch` call sites, verified" — the count today is **95** — and cites `TestNoRuleBlockIsDistinguishable` at `:1691` where the class opens at `:1692`. Everything else in that bullet holds: the `"MERGE": "completed"` literal count really is 1 in the decision file and 0 in the oscillation file, and no non-literal construction sets MERGE completed in either. | pending | (NIT — exempt.) Verified in the plan's favour while checking: the parity module's two other `ROUTER_CONSUMER_SKILLS` assertions (`:319`, `:339`) cannot be tripped by the planned SKILL.md edits — `MULTI_DISPATCH_KEYS = ("multi", "dispatches")` does not collide with a `complete` key and neither edit introduces `pthread`. |

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
