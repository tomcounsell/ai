---
status: Ready
type: bug
appetite: Small-Medium
owner: Valor Engels
created: 2026-08-19
tracking: https://github.com/tomcounsell/ai/issues/2817
last_comment_id: 5324622874
revision_applied: true
revision_applied_at: 2026-08-19T07:38:25Z
---

# Terminal Verdict: the router and review-probe residuals left outside #2826's fence

The filename still says `next-skill-terminal-verdict-and-pr-resolution` because it predates the
WS-B and WS-D deferrals; PR resolution is now entirely #2869 and #2868. The file is not renamed
on purpose — `find_plan_path` resolves a plan by its `tracking:` frontmatter, not by name, and
[`docs/features/sdlc-lane-identity.md`](../features/sdlc-lane-identity.md) records that a plan
doc may legitimately carry a different name from its lane slug.

Ships **#2817** and **#2825** as one lane in **one PR**, and **refs #2824** without closing
it. All three were filed from PR #2826's follow-up list and share a single root cause that
none of them names on its own (see **Why Previous Fixes Failed**) — but only two of them
have a measured fix that pays for itself today.

## Lane Size and the Two Deferrals

Two rounds of critique each cut one workstream. Both cuts were made on measurement, not on
size anxiety, and neither required splitting the PR.

**WS-D (deterministic candidate ordering) → [#2868](https://github.com/tomcounsell/ai/issues/2868)**
(round 2). Its headline rule was falsified: a MERGED-first comparator can never separate two
candidates at any production call site. Detail in *Resolved Questions* Q3.

**WS-B (lane-branch-first PR resolution, #2824) → [#2869](https://github.com/tomcounsell/ai/issues/2869)**
(round 3). Its mechanism is sound and its measured effect on live data is **zero**; the
argument that kept it in the lane was falsified. Detail in *Resolved Questions* Q4, which is
the decision record for this cut.

What remains is two workstreams landing as **one PR**:

| | Files touched | Rough size |
|---|---|---|
| **WS-A** (#2817) | `agent/sdlc_router.py`, `tools/sdlc_next_skill.py`, `tools/sdlc_stage_query.py` (`issue_state` key only) | one guard + one meta key + one JSON shape |
| **WS-C** (#2825) | `tools/sdlc_stage_marker.py` | one argument |

The two are **file-disjoint**: WS-A touches `agent/sdlc_router.py`,
`tools/sdlc_next_skill.py`, and `_compute_meta` in `tools/sdlc_stage_query.py`; WS-C touches
one argument in `tools/sdlc_stage_marker.py`. Neither reads the other's code and each
reverts alone (the mutation table in Risk 5 enforces that each has its own coverage).
Splitting them into two PRs would buy the reviewer nothing and cost a second lane's worth of
pipeline machinery. One PR, closing **#2817** and **#2825**.

**#2824 stays open** and is handed to #2869. The lane's closing refs are therefore
`Closes #2817`, `Closes #2825`, `Refs #2824` — task 8 posts the honest disposition on
#2824 itself so the issue explains what is and is not being done rather than going quiet.

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
   **Real, reproduced, and deferred to [#2869](https://github.com/tomcounsell/ai/issues/2869).**
   The fix keys on a recorded lane slug; both live instances carry none, and the slugged
   population and the reopened population are measured **disjoint** today. #2869 is the
   precondition, not the follow-up. See *Resolved Questions* Q4.

3. **#2825 — a review gate reads its evidence off an abandoned PR.**
   `tools/sdlc_stage_marker.py:263` passes `state="all"`, so `_review_artifact_posted`
   can select a closed-unmerged PR and return `True` from a review that belongs to
   work nobody merged. Live on #1785, #2073, and #2104.

**Desired outcome:**

A terminal pipeline reports itself terminal instead of being rebuilt, re-reviewed,
or escalated (WS-A), and a review gate reads its evidence off a PR somebody actually
merged (WS-C).

The third outcome — PR resolution answering with the PR belonging to *this* lifecycle
or answering `None` rather than a confident wrong number — is #2824's, and it moves to
#2869 with its measurement intact.

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
   → **This lane changes nothing here.** #2824's fix belongs to #2869 and #2868 owns the
   ordering. Both defects stay live and stay documented.
3b. **Issue state** (`_compute_meta`, WS-A): when and only when `stage_states["MERGE"] ==
   "completed"`, resolve `gh issue view N --repo <resolved_repo> --json state -q .state` into
   `meta["issue_state"]`, whitelisted to `{"OPEN", "CLOSED"}` and degrading to `None` on
   anything else or on failure. The `--repo` scoping is mandatory — see *Technical Approach
   → WS-A*, where an unscoped call is shown to arm the guard against a foreign issue. This is
   the input that keeps the terminal guard from latching a re-entered issue (Risk 6).
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

- **New dependencies**: none external. One new *internal* import:
  `agent/sdlc_router.py` → `agent/pipeline_complete.py`. It is legal:
  `tests/unit/test_architectural_constraints.py` forbids only `agent/sdlc_router.py`
  importing from **`tools/`**. A verification row pins this so the constraint is not
  quietly widened. **The guard still performs no I/O** — `issue_state` is resolved in
  `_compute_meta` and arrives as a plain string in `meta`, so router purity is preserved by
  construction rather than by discipline.
- **Interface changes**: `_compute_meta`'s returned `_meta` gains an `issue_state` key
  (additive; `None` on non-terminal ledgers, and whitelisted to `{"OPEN", "CLOSED"}`).
  `_lookup_pr` and `_gh_pr_search_issue_ref` are **untouched** by this lane (WS-B deferred
  to #2869, WS-D to #2868), so PR resolution order is byte-identical to today. `decide()`'s
  JSON gains one additive `complete` key on the terminal shape (WS-A); existing keys keep
  their meaning. The one external contract change is the two router-consumer SKILL.md
  bodies' reading of that key.
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

**Size:** Small–Medium (was Medium; two of the four original workstreams are now deferred)

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 remaining. Every alignment point is settled in *Resolved Questions*: WS-D is deferred to #2868 (Q3) and WS-B to #2869 (Q4).
- Review rounds: 2

The measurement work that usually dominates an appetite like this is **already
done** — it is in Spike Results and on all three issues. What remains is two
bounded diffs plus their tests: one guard with a meta key feeding it, and one argument.

## Prerequisites

| Requirement | Check Command | Purpose |
|---|---|---|
| `gh` authenticated | `gh auth status` | Every measurement and verification row shells out to `gh` |
| Repo resolvable | `gh repo view --json nameWithOwner -q .nameWithOwner` | `_lookup_pr` / `_review_artifact_posted` need a repo slug |
| Venv on the pinned interpreter | `./.venv/bin/python -c "import sys,pathlib; pin=pathlib.Path('.python-version').read_text().strip(); assert '.'.join(map(str,sys.version_info[:2])) in pin, (sys.version, pin)"` | `scripts/pytest-clean.sh` aborts on an off-pin venv, so every test row depends on this |
| Terminal-ledger issue states still CLOSED | `./.venv/bin/python -c "from agent.pipeline_ledger import PipelineLedger; import json,subprocess; ns=[int(r.issue_number) for r in PipelineLedger.query.all() if json.loads(r.stage_states_json or '{}').get('MERGE')=='completed']; c=sum(subprocess.run(['gh','issue','view',str(n),'--repo','tomcounsell/ai','--json','state','-q','.state'],capture_output=True,text=True).stdout.strip()=='CLOSED' for n in ns); assert c==len(ns), (c, len(ns))"` | 16/16, re-measured 2026-08-19 **under explicit `--repo` scoping**. A drift means the `!= \"CLOSED\"` polarity now costs real coverage and must be re-argued before build. |

**The `--repo` flag in that last row is load-bearing, not decoration.** A bare `gh issue
view N` resolves through gh's own ladder — `GH_REPO` from the environment *before* cwd — so
under a set `GH_REPO` or a `SDLC_TARGET_REPO` pointing elsewhere it answers about a
*different repository's* issue #N and exits 0. Measured unscoped and scoped on 2026-08-19,
both give `16 16` in this checkout, but that agreement is an artifact of this shell's
environment and is not a property of the command. Any reproduction of the 16/16 number must
pass `--repo` explicitly or it is measuring an unknown repository. This is the same
constraint the guard's own `gh` call carries — see *Technical Approach → WS-A*.

## Solution

### Key Elements

- **WS-A — Terminal guard (#2817).** Give the router the terminal fact it already
  computes elsewhere, by consulting `agent/pipeline_complete.py::is_pipeline_complete`
  rather than inventing a second definition of "finished" — **gated on the tracking issue
  being CLOSED**, so a re-entered issue is not latched shut forever.
- **WS-C — Review-probe scope (#2825).** `state="all"` → `state="merged"` at the one
  call site, preserving #2539.
- **WS-B — Lane-branch authority (#2824).** **Deferred to
  [#2869](https://github.com/tomcounsell/ai/issues/2869).** The mechanism (head-ref
  authority gated on the branch existing on origin) is sound and measures 0 gains / 0
  losses / 11 unchanged — it changes nothing on live data, and the population it would act
  on is disjoint from the population it was kept in the lane to protect. See *Resolved
  Questions* Q4.
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

Reopened-issue tick — the case WS-A is deliberately shaped **not** to touch:

**Today:** `_lookup_pr(2494)` → fuzzy `#2494` → prior lifecycle's merged PR **2516** →
row 5 disarmed → **row 7 `/do-pr-review`** on a finished PR.

**After:** unchanged, by design. The terminal guard sees `issue_state == "OPEN"` and
**stands down** — it must, or the ledger latches shut forever (Risk 6) — so the reopened
tick routes exactly as it does today. That is a self-healing wrong answer, which is the
well-understood baseline this lane improves on elsewhere and deliberately does not disturb
here. Repairing it is #2824's job and needs a recorded lane slug to key on, which is
[#2869](https://github.com/tomcounsell/ai/issues/2869). See *Resolved Questions* Q4 for why
that ordering is the honest one rather than a punt.

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

**The `gh` call must be repo-scoped, and this is a safety property rather than tidiness.
(Round-3 blocker.)**

Every other GitHub call in `_compute_meta`'s reach threads the resolved repo —
`_fetch_pr_merge_state` (`tools/sdlc_stage_query.py:192`), `_gh_pr_list` (`:242`),
`_gh_pr_search_issue_ref` (`:316`), and the `_lookup_pr` calls themselves (`:601`). A bare
`gh issue view N --json state` would not, and `gh` resolves `GH_REPO` **from the environment
before cwd**. Under a set `GH_REPO`, or a `SDLC_TARGET_REPO` pointing at another checkout,
that call answers about a *different repository's* issue #N, **exits 0**, and hands the
guard a confident wrong answer. If that foreign issue happens to be CLOSED, the guard
terminates a live lane — the outcome Risk 1 names "the worst possible failure for this
change", reached through the gate built to prevent it.

The fail-soft-to-`None` contract does not cover this, and that is the whole point: a
wrong-repo lookup **succeeds**. Degrading on failure protects against silence, not against
a confident answer to the wrong question. This is #1642's signature — a lookup that
silently resolves against the wrong scope and moves a gate — pointed in the *arming*
direction rather than the disarming one, which is strictly worse because #1642 at least
failed safe.

Build the argv exactly like its sibling at `:192`:

```python
cmd = [
    "gh", "issue", "view", str(issue_number),
    *(["--repo", resolved_repo] if resolved_repo else []),
    "--json", "state", "-q", ".state",
]
```

**And whitelist the parse.** The `!= "CLOSED"` polarity is only fail-open if a garbage value
cannot *be* the literal `"CLOSED"`:

```python
state = (proc.stdout or "").strip()
issue_state = state if state in ("OPEN", "CLOSED") else None
```

Anything gh emits that is neither — an error string, an empty body, a future state name —
becomes `None`, which the guard already treats as "do not terminate".

**Coverage is not sacrificed by the stricter polarity — measured, not assumed.** All
**16 of 16** terminal ledgers on this machine resolve to `CLOSED` (issues 2475, 2566, 2628,
2637, 2638, 2640, 2644, 2645, 2655, 2659, 2663, 2679, 2682, 2694, 2697, 2711; zero OPEN,
zero unresolved). **That measurement is reproducible only under repo-scoped invocation** —
`gh issue view N --repo tomcounsell/ai --json state -q .state`. Re-measured scoped on
2026-08-19: `16 16`. The unscoped form happens to agree in this checkout, but it agrees by
environment rather than by construction, so the scoped form is the one the Prerequisites and
Verification rows carry. The guard fires on the entire measured terminal population and
stands down on exactly the re-entered case.

It is also the semantically right reading, not merely the safe one. A merged pipeline whose
issue is still open is a pipeline someone deliberately did not finish — a `Refs #N` PR, or
one issue of a multi-issue lane. "Terminal" should mean the tracker agrees, and requiring
`CLOSED` makes the router's terminal fact and the tracker's fact the same fact.

**Cost containment for the extra `gh` call.** Resolve `issue_state` **only when
`stage_states["MERGE"] == "completed"`**. Every non-terminal ledger — the overwhelming
majority of ticks — pays nothing, and a terminal ledger pays one repo-scoped `gh issue view`
on a path that already makes two or more `gh` calls. Resolve it once per
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

**WS-B — lane-branch authority. Deferred to
[#2869](https://github.com/tomcounsell/ai/issues/2869); do not build it here.**

The mechanism was never wrong. Two rounds of measurement made it *correct*, and the third
round showed that correct and worth-shipping are different questions. The reasoning is
recorded here so #2869 inherits a finished design rather than a restart, and so nobody
re-derives the cut.

**Where the design landed.** Round 1 specified unconditional head-ref authority: when
`lane_branch_name(slug)` yields a branch, run the head-ref leg and return its result
directly, *including `None`*, suppressing the fuzzy search. Measured over every
`PipelineLedger` on this machine with the real `_body_references_issue` validator, that
shape scores **0 gains and 1 loss** — it never once recovered a PR the fuzzy leg missed, and
it destroyed a currently-correct resolution (#2694, whose slug `sdlc-2694` names a branch
that does not exist on origin while PR 2695's real head is `session/dev-41a59eee`).

Round 2 added the missing precondition — assert authority only when the branch is **real**:

```python
branch = lane_branch_name(slug)
if branch and lane_branch_exists_on_remote(branch):
    return _gh_pr_list(["--head", branch, "--state", state], repo=repo)
# otherwise fall through to the existing fuzzy ladder, unchanged
```

| Shape | Gains | Losses | Unchanged |
|---|---|---|---|
| Round-1 (unconditional head-ref authority) | **0** | **1** (#2694) | 10 |
| Round-2 (authority only if the branch exists on origin) | **0** | **0** | 11 |

The gate is never *"the head-ref lookup returned `None`"* — conflating "no PR on this
branch" with "no branch" would suppress the fuzzy leg for every slugless lane and regress
resolution across the board. Branch **existence** is a categorically different signal from
lookup **result**. That distinction is the design's one real insight and #2869 should keep it.

**Why it does not ship in this lane.** Its measured effect is **zero, not negative**, and
the argument that kept it here was falsified at round 3.

That argument was: WS-A's terminal guard now deliberately stands down on an OPEN issue
(Risk 6), which hands every reopened ledger back to fuzzy resolution, so WS-B is the only
remaining protection for the reopened case. **The reopened population is the slugless
population, so that protection is empty.** Both live #2824 instances carry
`slug: None, slug_source: "unresolved"` (re-verified 2026-08-19), and a lane with no slug
has no branch to check. Measured the same day, the eleven ledgers carrying a recorded slug
are all *in-flight* lanes (#713, #2694, #2738, #2739, #2748, #2817, #2823, #2836, #2845,
#2853, #2867) — the slugged set and the reopened set are **disjoint**. WS-B protects nothing
today, and the thing that would give it a population is #2869 itself.

Against that zero it carries a real recurring cost: one `git ls-remote` per enriched tick of
every slugged lane, **measured at ~0.8s** (0.797s single-ref, 0.847s for the full 545-head
listing), on the pre-PR hot path — `tools/sdlc_stage_query.py:570` routes to the lookup
exactly when no PR exists yet, which is the tick a lane takes most often before it has one.
Memoizing that away is itself unfinished: a memo scoped "per `_lookup_pr` call chain" still
pays twice because `_compute_meta` makes two top-level calls (`:596`, `:598`), and the
process-lifetime `functools.lru_cache` that would pay once latches a "branch does not exist"
answer across the build-push-PR sequence that makes it change. The correct shape is a
request-scoped `threading.local()` memo mirroring `_resolve_memo`
(`tools/_sdlc_utils.py:106-146`) — designed, not built, and it belongs with the workstream.

**#2869 is WS-B's precondition, not its follow-up.** `tools/lane_identity.py` records a
lane's slug two ways: `adopt_lane_slug` takes a branch name the caller **already knows** and
records it verbatim, while `resolve_lane_slug`'s discovery ladder probes only the
issue-derived name and, on a miss, mints `sdlc-{N}`. `adopt_lane_slug`'s own docstring names
the resulting divergence as "the defect this module closes." Adoption is partial rather than
absent — #2738's slug is `hook-validator-target-resolution` with `slug_source: recorded`,
and `refs/heads/session/hook-validator-target-resolution` exists on origin — but #2694 shows
a site that knew the branch and did not record it. Every slug that starts naming a real
branch is one more answer WS-B can resolve authoritatively. Shipping WS-B first buys a
mechanism with no population; shipping #2869 first creates the population that makes the
mechanism measurable. The two were filed in the wrong order and this cut corrects it.

**What is inherited by #2869, ready to build:** the existence-checked mechanism above, the
`lane_branch_exists_on_remote` helper (three lines over `_ls_remote_heads()` at
`tools/lane_identity.py:201-224`, which already never raises — **not** over
`_check_branch_pushed`, which has no `try`/`except` and propagates `TimeoutExpired`), the
request-scoped memo shape, the two call-count verification rows (branch exists →
`fuzzy=0 head=1`; branch absent → `fuzzy=1`), and Risk 2's classified ladder-diff bar with
its ceiling-1 expected-cost bucket. None of it is lost.

**Zero coupling to what ships.** WS-A touches `agent/sdlc_router.py`,
`tools/sdlc_next_skill.py`, and `_compute_meta`'s `issue_state` key; WS-C touches one
argument at `tools/sdlc_stage_marker.py:263`. **Neither reads `_lookup_pr`'s ladder order**,
so the cut is a clean removal rather than an unpicking.

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
- [ ] `tools/sdlc_stage_query.py::_lookup_pr` — **not touched by this lane** (WS-B deferred to #2869). Its documented "never raises" contract is unchanged; no new test owed here.
- [ ] `tools/sdlc_stage_marker.py::_review_artifact_posted` — all three artifact legs fail closed to `False` (`:330-335`). WS-C does not touch them; add one regression test that a `subprocess.run` raising on every call still returns `False` under the new `merged` scope.
- [ ] `agent/sdlc_router.py` terminal guard — `decide_next_dispatch` wraps rule predicates in try/except (`:1944`) but guards are **not** protected at all: `evaluate_guards` (`:830-839`) calls each guard bare. Assert **the guard itself** returns `None` on a malformed `stage_states` (missing key, non-dict, `None` value) — scoped to the guard, because `decide_next_dispatch` as a whole already raises on a non-dict at G1 today and this lane does not fix that (see the note under the Verification table).

### Empty/Invalid Input Handling
- [ ] Terminal guard against `{}`, `{"MERGE": None}`, `{"MERGE": ""}`, `{"MERGE": "in_progress"}`, `{"MERGE": "failed"}` — only the exact `"completed"` string terminates. Parametrize; a substring or truthiness check here would terminate a live lane.
- [ ] Terminal guard against `issue_state` in `{"OPEN", None, "", "closed", "CLOSED_STALE", 42}` on an otherwise fully terminal ledger — all must return `None`; only the exact `"CLOSED"` terminates (Risk 6). Lowercase `"closed"` is in the set deliberately: `gh issue view --json state` emits uppercase, and a case-insensitive match would be a second, silently divergent definition of the same fact.
- [ ] Terminal guard with `meta=None` and `meta={}` — must return `None` without raising, since the guard reads `meta` before delegating and `evaluate_guards` wraps nothing.
- [ ] `_compute_meta` with a `gh issue view` that exits non-zero, times out, or emits unparseable JSON — `issue_state` must be `None` and the whole projection must still return, per `_compute_meta`'s stated "degrade rather than raise" contract.
- [ ] **`_compute_meta` with a `gh issue view` that succeeds but emits something outside `{"OPEN", "CLOSED"}`** — an error string, an empty body, a future state name — must land on `issue_state = None`, not pass the raw value through. The `!= "CLOSED"` polarity is fail-open only if a garbage value cannot be the literal `"CLOSED"`; the whitelist is what makes that true.
- [ ] **`_compute_meta` builds the `gh issue view` argv with `--repo <resolved_repo>` whenever a repo resolves.** Assert on the argv, not on the result: a wrong-repo lookup *succeeds*, so no result assertion can catch it. This is the round-3 blocker's regression test.

### Error State Rendering
- [ ] The terminal JSON from `decide()` must be distinguishable from an escalation `Blocked` by a machine consumer. Assert the exact key set of both shapes, not just that the reason string differs.
- [ ] The terminal reason string must not tell a human to run `sdlc-tool dispatch reset` — that is G4's remedy and it is wrong for a finished pipeline. Assert its absence explicitly.

## Test Impact

- [ ] **`tests/unit/test_sdlc_router_decision.py::TestNoRuleBlockIsDistinguishable` (`:1692`) — UPDATE, and this is the one router test file WS-A provably reds.** Round 1 omitted this file entirely; it is the repo's primary router-decision suite (**95** `decide_next_dispatch(` call sites, re-counted 2026-08-19; the bare `grep -c decide_next_dispatch` figure of 97 counts import and reference lines too) and holds the **only** `"MERGE": "completed"` fixture in any router test (verified: `grep -c` returns **1** here and **0** in `test_sdlc_router_oscillation.py`). Its `_unowned_state()` helper builds PLAN/CRITIQUE/BUILD/REVIEW/DOCS/MERGE all completed plus an APPROVED verdict, with `meta = {"pr_number": 4242, "pr_merge_state": "BLOCKED"}`. `test_no_rule_block_uses_no_rule_sentinel` asserts `result.guard_id == "NO_RULE"` and fails outright once a first-position guard returns `TERMINAL`; `test_no_rule_block_renders_distinguishably_from_a_guard_block` consumes the same helper. **Disposition: re-key `_unowned_state()` onto a non-terminal unowned ledger — drop the `"MERGE": "completed"` entry, keep everything else including `pr_merge_state: "BLOCKED"`.** Do **not** flip the assertion to `"TERMINAL"`: these two tests are the only proof of the `NO_RULE` sentinel's distinguishability (#2767b), and flipping them retires that guarantee instead of preserving it. **The re-key is verified to work** — measured on `f491306c5`, the helper's ledger returns `Blocked(reason='no matching dispatch rule', guard_id='NO_RULE')` with `MERGE` completed, with `MERGE` absent, **and** with `MERGE: "pending"`, so dropping the key leaves both assertions passing for the same reason they pass today. (These tests will additionally be insulated by the `issue_state` gate, since the fixture's `meta` carries no `issue_state` and the guard requires `"CLOSED"` — but the re-key is still the correct disposition, because a fixture should not depend on a gate to stay meaningful.)
- [ ] `tests/unit/test_sdlc_router_oscillation.py` — **NO CHANGE EXPECTED, verify rather than assume.** Round 1 listed two UPDATE dispositions against this file; both were **vacuous** — it contains zero `"MERGE": "completed"` fixtures (verified), so no fixture in it can reach the terminal guard. Re-run it and confirm green. If anything in it does move, that is a signal the guard is placed wrong, not a fixture to patch.
- [ ] `tests/unit/test_sdlc_router_decision.py` — **REVIEW (the other 94 call sites)**: sweep for any other fixture that sets MERGE completed indirectly (via a helper or a `dict` update) rather than by the literal the grep counts. The grep bounds literals, not construction.
- [ ] `tests/unit/test_sdlc_stage_query.py:270-400` — **NO CHANGE** (was UPDATE before WS-B was deferred). The D4 resolution-order tests encode the current ladder order, which this lane leaves byte-identical. [#2869](https://github.com/tomcounsell/ai/issues/2869) owns rewriting them.
- [ ] `tests/unit/test_sdlc_stage_query.py:364-399` — **NO CHANGE** (was UPDATE before WS-D was deferred). `_gh_pr_search_issue_ref`'s "first body-validating candidate" contract is unchanged by this lane; these tests keep asserting it. [#2868](https://github.com/tomcounsell/ai/issues/2868) owns rewriting them.
- [ ] `tests/unit/test_sdlc_stage_query.py:1436-1520` — **NO CHANGE**: the #2757 two-pass block. Nothing in this lane touches the ladder; the comment at `:1517` documenting "returns the FIRST body-validating candidate" stays accurate and needs only the #2868 pointer added by the Documentation task.
- [ ] `tests/unit/test_sdlc_stage_marker.py:1086-1210` — **UPDATE**: several cases patch `_lookup_pr` and assert its call kwargs; `:1113` already pins `return_value=2538`. The `state="all"` kwarg assertion becomes `state="merged"`. **Add** a case pinning the scope by kwarg so a future widening re-reds.
- [ ] `tests/unit/test_pipeline_complete_predicate.py` — **UPDATE (additive)**: add the router's exact call shape (`outcome="success"`, `pr_open=None`) as a pinned case, so a future change to the predicate's default behavior surfaces as a router-facing failure rather than a silent routing change.
- [ ] `tests/unit/test_architectural_constraints.py` — **UPDATE**: extend the router import-boundary class with an explicit positive assertion that importing `agent.pipeline_complete` is allowed, so the new import is documented as intentional and a future blanket tightening does not silently forbid it.
- [ ] `tests/unit/test_sdlc_next_skill.py:1631` — **REVIEW**: patches `_lookup_pr` to `None`; confirm the terminal shape does not change its expectation, and update if it does.
- [ ] `tests/integration/test_sdlc_session_ensure_integration.py` — **REVIEW**: **10** tests (round-1 said 11), green today. Re-run; no expected change, but they exercise the marker path WS-C touches. **This file holds `test_terminal_merged_pipeline_routes_to_merge_not_build` at `:498`** — round 1 attributed it to `tests/unit/test_sdlc_router_oscillation.py`, which is wrong (citation corrected at round-2 critique). The plan's claim *about* the fixture is correct: its docstring reads "No `MERGE` marker is set, deliberately", so it should survive WS-A untouched. **Verify that rather than assume it** — if it changes, the guard is placed wrong.
- [ ] `tests/unit/test_sdlc_stage_query.py` — **UPDATE**: cover the `issue_state` meta key — resolved **only** when `MERGE == "completed"`, absent/`None` otherwise, `None` rather than a raise when the `gh` call fails, `None` on any value outside `{"OPEN", "CLOSED"}`, and **`--repo` present in the argv** whenever a repo resolves (round-3 blocker).
- [ ] `tests/unit/test_lane_identity.py` — **NO CHANGE**: WS-B added `lane_branch_exists_on_remote` here and is deferred to #2869, which owns that coverage.

No xfail or runtime `pytest.xfail()` markers exist anywhere in `tests/` (`grep` count: **0**), so there are none to convert.

## Rabbit Holes

- **Introducing a third `Complete` return type alongside `Dispatch` and `Blocked`.**
  Architecturally the right model, and it ripples into every consumer of
  `decide_next_dispatch`, the parity tests, and `decide()`'s documented JSON contract.
  **Settled (Resolved Question 2): the additive-key approach ships.** Revisit only if a
  second terminal reason is ever added, at which point the key-count argument changes.
- **Making `_body_references_issue` distinguish a declarative closing directive from a
  retrospective prose mention** (the PR 2542 finding). Genuinely interesting, genuinely
  a natural-language problem, and entirely outside this lane — nothing here reads
  `_body_references_issue`. It belongs with #2868 and #2869.
- **Reviving WS-B "because it is only ten lines."** Size was never the objection. Its
  measured effect on live data is zero and the population it acts on is disjoint from the
  population it was meant to protect, so ten correct lines still buy nothing and still cost
  ~0.8s per enriched tick of every slugged lane. The finished design is preserved verbatim
  under *Technical Approach → WS-B* for #2869 to pick up. See *Resolved Questions* Q4.
- **Backfilling `slug` onto #2494 and #2518 so the #2824 fix looks complete.** Mutating two
  live ledgers to manufacture a population. Wrong under WS-B and still wrong under #2869.
- **Fixing the lane-slug adoption gap inside this lane.** #2694's slug is `sdlc-2694`
  while its PR's head is `session/dev-41a59eee`, and the temptation is to chase the write
  site that failed to call `adopt_lane_slug`. That is a real bug, it is
  [#2869](https://github.com/tomcounsell/ai/issues/2869), and it is now the *lead* of that
  issue rather than a neighbour of it. Nothing shipping here reads a slug at all.
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
- **Extending the terminal guard to the `docs_success_no_pr` path.** Deferred for a
  measurement reason, not an architectural one — see the No-Gos entry, which states the
  real grounds and records the mechanically-equivalent shape so nobody re-derives it.
- **Touching `_gh_pr_search_issue_ref` at all.** Client-side candidate ordering is
  [#2868](https://github.com/tomcounsell/ai/issues/2868), not this lane. Two temptations
  in particular: adding `sort:` to the search string (moves the guarantee back onto a
  remote service whose behavior we just established is not contractual) and "while I'm
  here" widening the `--json` field list (a diff on a shared function with no test in this
  lane that needs it). A verification task checks this function is byte-unchanged.
- **Rewriting `_compute_meta`'s two-pass structure while adding the `issue_state` key.**
  The two-pass is #2826's, it is measured, and it is orthogonal. The `issue_state` key sits
  beside it and reads none of it. Leave it.

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

### Risk 2: *(retired — moved to #2869 with WS-B)*
Previously covered WS-B suppressing the fuzzy leg too aggressively and regressing PR
resolution. **WS-B is deferred to [#2869](https://github.com/tomcounsell/ai/issues/2869)**
(*Resolved Questions* Q4), and nothing in this lane touches `_lookup_pr`, so the risk has no
surface here.

The risk analysis is not discarded, because #2869 needs it intact. It transfers whole: the
acceptance bar is a classified ladder diff over every `PipelineLedger` carrying a recorded
slug, with **two blocking shapes** — a lost resolution whose PR head **is** `session/{slug}`
(the head-ref leg should have found it), and a lost resolution where `session/{slug}` **does
not exist** on origin (the existence check should have fallen through) — and **one
expected-cost bucket with a ceiling of 1**, for a lane carrying two branches where authority
goes to the recorded one. Measured 2026-08-19: 0 blocking, 0 expected-cost, 0 gains, 11
unchanged. The ceiling is the part worth carrying forward: round 1's equivalent bucket had
no bound, so an arbitrary regression could have been absorbed into it.

### Risk 3: *(retired)*
Previously covered WS-D's MERGED-first ordering sharpening #2824 if it landed alone.
**WS-D is deferred to [#2868](https://github.com/tomcounsell/ai/issues/2868)**, so
neither the risk nor its sequencing mitigation applies to this lane. The sequencing
constraint transfers with both deferrals and is now internal to them: #2868 must not land
before #2869's WS-B mechanism.

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
**Mitigation:** Mutation-check each guard: revert WS-A and WS-C individually and record
which tests re-red, in a demonstrated-red table in the PR body. A workstream with zero
re-reds has no real coverage. **Mutate WS-A's two halves separately** — the guard and the
`issue_state` key — because a suite that reds only on the guard revert leaves the `--repo`
scoping and the `{"OPEN","CLOSED"}` whitelist unpinned, and those are exactly where the
round-3 blocker lived. Deleting `--repo` from the argv must red a named test on its own.

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

**What this mitigation deliberately leaves uncovered.** By standing down on an OPEN issue,
WS-A hands every reopened ledger back to today's fuzzy PR resolution — including the #2824
harm on #2494 and #2518. That is accepted, not overlooked. Round 3 tested the alternative
reading, that WS-B could pick the case up, and falsified it: WS-B keys on a recorded lane
slug, both live instances carry none, and the slugged and reopened populations measure
**disjoint**. So the reopened case routes exactly as it does on `main` today — a
self-healing wrong answer rather than a latch — and [#2869](https://github.com/tomcounsell/ai/issues/2869)
owns actually fixing it. The important property here is the direction: WS-A can only ever
*decline* to terminate, so it never makes the reopened case worse than it already is.

## Race Conditions

**No new race conditions identified.** Both shipped workstreams are synchronous,
single-threaded, side-effect-free reads. The terminal guard is a pure dict read; the new
`gh issue view` in `_compute_meta` is a blocking `subprocess.run` with a timeout that
returns a value and writes nothing, on a path that already makes two or more such calls.

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
  the files this lane touches.
- **[SEPARATE-SLUG #2491]** Unifying the router's dispatch table with a single-source
  pipeline graph (`docs/plans/pipeline-graph-single-source-of-truth.md`, status
  Planning). The terminal guard is a small consumer of that future model; building the
  model here would swallow the lane.
- **[SEPARATE-SLUG #2658]** Two-pole proofs for every verification row and guard
  (`docs/plans/gates-that-cannot-fire.md`, status Planning). This plan applies the
  mutation-check discipline to its own two guards (Risk 5) without adopting the
  program repo-wide.
- **[SEPARATE-SLUG #2869]** Lane-branch-first PR resolution (#2824) and the lane-slug
  adoption gap that is its precondition. Cut from this lane at round-3 critique with its
  design finished and its measurement intact — see *Resolved Questions* Q4.
- **[SEPARATE-SLUG #2868]** Deterministic candidate ordering. Cut at round-2 critique
  (*Resolved Questions* Q3).
- **The `docs_success_no_pr` terminal leg of `is_pipeline_complete` — deferred because it
  is unmeasured, not because it is impossible.**

  Earlier rounds of this plan justified the deferral architecturally: the leg needs
  `pr_open`, `pr_open` needs I/O, and the router must stay pure and `tools/`-import-free.
  **WS-A's own design falsifies that argument.** `issue_state` also needs I/O, and the whole
  point of resolving it in `_compute_meta` is that a router-consumed fact requiring I/O
  arrives as a plain value in `meta` — what this plan calls preserving purity "by
  construction rather than by discipline". `pr_open` is available on identical terms, and
  presenting a scope call as an impossibility means the next reader will not revisit it when
  they should.

  **The real reason is that nobody has measured it.** Nothing in this plan establishes how
  many live ledgers are DOCS-completed with no open PR, or what those ledgers route to
  today, and the whole discipline of this lane is that a workstream earns its place with a
  measurement. WS-A ships the `merge_success` leg only, and the DOCS leg gets its own pass.

  The mechanically-equivalent shape, recorded so nobody re-derives it: `_compute_meta` sets
  `meta["pr_open"] = _check_pr_open(issue_number)` under the mirror-image gate
  (`DOCS == "completed" and MERGE != "completed"`) and the guard passes it through.
  `agent/pipeline_complete.py:88` already returns `None` on subprocess error, timeout, and
  malformed output, landing on the predicate's existing conservative `pr_state_unavailable`
  branch — the fail-open direction is identical to the `issue_state` gate's, and the same
  `--repo` scoping obligation applies.

  The Verification table's "no I/O in the router" anti-criterion still stands, because the
  I/O belongs in `_compute_meta` either way. It pins where the I/O lives, not whether the
  leg can exist.

  The DOCS leg's tripwire is the guard's `reason != "merge_success"` check plus the
  Verification row "No-Go tripwire — the guard never fires on the `docs_success_no_pr` leg".
  Wiring the recipe above without doing the measurement turns that row red, which is the
  point: this No-Go is now enforced by a test rather than by an argument default.

- **A third, unscoped `gh issue view --json state` reader stays as it is:
  `scripts/migrate_completed_plan.py:362::_gh_issue_state`.** Found in round 4, live on
  `main`, and cited here so the next reader does not think this plan swept the surface.
  It carries **both** divergences this lane fixes elsewhere: the argv has no `--repo`
  (`["gh", "issue", "view", str(issue_number), "--json", "state"]` at `:366`), and it
  `.lower()`s the result and returns `"unknown"` on failure, so its vocabulary is
  `{"open", "closed", "unknown"}` against `_compute_meta`'s `{"OPEN", "CLOSED", None}`.
  Its two call sites are `:392` and `:421`.

  **Disposition: out of scope for this lane, named rather than fixed.** It is a
  human-invoked one-shot migration script, not a router input — nothing it returns reaches
  `decide_next_dispatch`, so it cannot terminate a live lane, which is the failure the
  round-3 `--repo` blocker was about. Touching it would put a fourth file in a two-file PR
  and dilute the mutation table with a workstream that has no measured failure behind it.
  **Follow-up obligation:** task 8 files a `bug`-labelled issue naming the file, both
  divergences, and the two call sites, and links it from this bullet. This lane does not
  add `--repo` there, and a build that does has exceeded scope.

Everything else #2817 and #2825 describe is in scope for this plan. #2824 is scoped out in
full and handed to #2869.

## Update System

No update-system changes required. Both shipped workstreams are edits to existing Python
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
- [ ] `docs/features/sdlc-lane-identity.md` — **no change**. The slug does **not** become
      load-bearing for PR resolution in this lane; that was WS-B and it is
      [#2869](https://github.com/tomcounsell/ai/issues/2869), which owns the doc edit along
      with the mechanism.
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
- [ ] Annotate `_lookup_pr`'s "Resolution order (D4)" docstring (`:348-366`) with a pointer
      to [#2869](https://github.com/tomcounsell/ai/issues/2869). **Do not rewrite the order**
      — it is unchanged here. The note records that a recorded lane slug *could* take
      authority over the fuzzy leg once slug adoption is reliable, and that the measured
      reason it does not yet is a slugged population disjoint from the reopened one. Same
      discipline as the `_gh_pr_search_issue_ref` annotation above: a known gap should read
      as a known gap, not as settled intent.
- [ ] Docstring on the new `issue_state` resolution in `_compute_meta` stating why the
      `gh` call is repo-scoped — that an unscoped call answers about a foreign repository
      and **exits 0**, so this is a correctness constraint rather than a style one, and why
      the value is whitelisted to `{"OPEN", "CLOSED"}`.
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

Every criterion below is about WS-A or WS-C. **There is no `_lookup_pr` criterion in this
lane** — WS-B is deferred to [#2869](https://github.com/tomcounsell/ai/issues/2869) and PR
resolution is byte-identical to `main`. A build that changes `_lookup_pr`'s ladder has
exceeded scope, and the anti-criterion row in the Verification table catches it.

**WS-A — the terminal guard**

- [ ] A terminal ledger (`MERGE == completed` **and `issue_state == "CLOSED"`**) returns an
      explicit terminal verdict from `decide_next_dispatch` in **all eight** cells of
      spike-1's matrix **and in the ninth `PATCH`-unsettled cell** — no `/do-build`, no
      `/do-pr-review`, no `/do-merge`, no `NO_RULE`.
- [ ] **The terminal guard never latches a re-entered issue.** On an otherwise fully
      terminal ledger it returns `None` for every `issue_state` other than the exact string
      `"CLOSED"` — parametrized over `{"OPEN", None, "", "closed", "CLOSED_STALE", 42}` —
      and `Blocked(guard_id="TERMINAL")` for `"CLOSED"` alone. This is the Risk 6 bar; a
      guard that fires on an unresolvable issue state fails it.
- [ ] The terminal guard returns `None` (falls through), never `Blocked`, on a non-dict
      `stage_states` — asserted against the guard directly.
- [ ] **The guard gates on the reason, not on truthiness.** It reads
      `is_complete, reason = is_pipeline_complete(...)` and returns `None` unless
      `reason == "merge_success"`. Tripwire: a ledger with `DOCS = "completed"`, `MERGE`
      absent, `issue_state = "CLOSED"`, and `pr_open = False` threaded through `meta` still
      returns `None` from the guard. `agent/pipeline_complete.py:78-84` returns
      `(True, "docs_success_no_pr")` the instant `pr_open is False`, and the No-Gos section
      publishes the exact two-line recipe for supplying that argument — so without this
      criterion the DOCS terminal leg switches on with nothing going red. This test is the
      tripwire the No-Go leans on.
- [ ] **Every `MERGE`-not-completed ledger that routes to `/do-merge` today still does** —
      the negative control. Measured on `96a44a505` as **8 routes** over `MERGE` in
      `{absent, pending, in_progress, failed}` × `pr_number` in `{None, 555}` ×
      `branch_exists` in `{False, True}` × verdict in `{APPROVED, none}` (32 cells, 8 hits),
      **all row 10**, all requiring `pr_number` **and** an APPROVED REVIEW verdict — the
      other 24 cells route elsewhere. Expected **8 before and after WS-A**. The Verification
      row "Negative control — non-terminal still routes to merge" is its single-cell form;
      the row "Negative control — the full 8-route non-terminal `/do-merge` sweep" is the
      full form. Spike-1's matrix is defined entirely over `MERGE = completed` and therefore
      contains no cell of this population; this criterion is measured independently of it.
- [ ] `sdlc-tool next-skill` on a terminal pipeline emits a machine-distinguishable
      terminal shape, verified by invoking the real binary, not `decide()` in-process.
- [ ] The terminal shape is consumed correctly end to end by **both** router consumers:
      `.claude/skills-global/do-sdlc/SKILL.md` **and** `.claude/skills/sdlc/SKILL.md` route
      `complete: true` to loop-exit-success, not to human escalation. Both edits land in the
      same commit as WS-A; neither is conditional.
- [ ] `agent/sdlc_router.py` imports nothing from `tools/` (existing constraint test
      still green) and performs no I/O.

**WS-A — the `issue_state` gate that feeds the guard**

- [ ] `_compute_meta` resolves `issue_state` **only** when `MERGE == "completed"`, and
      degrades to `None` rather than raising when the `gh` call fails.
- [ ] **The `gh issue view` argv carries `--repo <resolved_repo>` whenever a repo resolves**,
      built exactly like its sibling at `tools/sdlc_stage_query.py:192`. Asserted **on the
      argv**, because a wrong-repo lookup *succeeds* — no result assertion can catch it, and
      the fail-soft-to-`None` contract does not cover a confident answer to the wrong
      question. This is the round-3 blocker; the failure it prevents is a foreign CLOSED
      issue terminating a live lane.
- [ ] **`issue_state` is whitelisted to `{"OPEN", "CLOSED"}`**, with everything else — error
      strings, empty output, future state names — becoming `None`. The `!= "CLOSED"`
      polarity is fail-open only if a garbage value cannot be the literal `"CLOSED"`.

**WS-C — the review-probe scope**

- [ ] `_review_artifact_posted(1785)` and `_review_artifact_posted(2073)` return
      **False**; `_review_artifact_posted(2104)` returns **True** via merged PR 2109.
- [ ] The #2539 control holds 5/5 live: #2860, #2831, #2716, #2734, #2741.

**Scope, coverage, and honesty**

- [ ] **`_lookup_pr` and `_gh_pr_search_issue_ref` are byte-unchanged in the merged diff.**
      Both WS-B (#2869) and WS-D (#2868) live in that code and neither ships here. This is
      the scope-leak check, and it replaces every ladder-order criterion earlier rounds
      carried.
- [ ] **KNOWN-UNCOVERED — #2824 is not fixed by this lane, and #2494 / #2518 stay broken.**
      They are the only OPEN reopened issues carrying a stale prior-lifecycle PR, and
      `sdlc-tool stage-query` reports `slug: None, slug_source: "unresolved"` for both
      (re-verified 2026-08-19). The mechanism that would fix them keys on a recorded lane
      slug; they have none, and the eleven ledgers that do have one are all in-flight lanes,
      so the slugged and reopened populations are **disjoint**. This lane therefore **does
      not close #2824** — it refs it, and #2869 carries both the mechanism and the
      slug-adoption gap that gives the mechanism a population. Task 8 says exactly this on
      the issue rather than letting it go quiet. Backfilling those two ledgers to manufacture
      a population is an explicit Rabbit Hole.
- [ ] Demonstrated-red table in the PR body: each of WS-A and WS-C reverted individually
      re-reds at least one named test.
- [ ] **#2817 and #2825 carry their measured post-fix shape as a comment before closing,
      and #2824 carries its deferral disposition** — #2826's explicit request, applied to
      every issue this lane touched.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (router terminal verdict)**
  - Name: `router-builder`
  - Role: WS-A only — the terminal guard, its wiring into `GUARDS`, and `decide()`'s terminal JSON shape
  - Agent Type: builder
  - Resume: true

- **Builder (marker scope)**
  - Name: `resolution-builder`
  - Role: WS-C only — the `state="all"` → `state="merged"` argument and its rationale comment. **Nothing in `_lookup_pr` or `_gh_pr_search_issue_ref`**, which this lane leaves alone (#2869, #2868).
  - Agent Type: builder
  - Resume: true

- **Test engineer (mutation proofs)**
  - Name: `mutation-tester`
  - Role: the demonstrated-red table — revert each workstream in isolation and record which tests re-red
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `lane-validator`
  - Role: independently re-run every live measurement and the whole Verification table; confirm **both** `_lookup_pr` and `_gh_pr_search_issue_ref` are unmodified in the merged diff (the WS-B / WS-D scope-leak check)
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `lane-documentarian`
  - Role: the Documentation section, including the docstring annotations that currently document known gaps as intentional design
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Re-measure both shipped shapes against build-time main
- **Task ID**: measure-baseline
- **Depends On**: none
- **Validates**: no test files — this produces the red-state record
- **Informed By**: spike-1, spike-2, spike-4 (all measurements to reproduce)
- **Assigned To**: `lane-validator`
- **Agent Type**: validator
- **Parallel**: false
- Re-run spike-1's eight-cell matrix and confirm it still matches this plan. If it has moved, **stop and report** — a drifted baseline invalidates the design, not just the numbers.
- Re-run `_review_artifact_posted(1785/2073/2104)` → True/True/True.
- Re-run the terminal-ledger CLOSED sweep **with `--repo`** and confirm both numbers are **equal** and the population is **non-empty**. It was `16 16` on 2026-08-19 and grows by one on every merge, so a builder will legitimately see a larger number. An **inequality** — not a change in the count — means the `!= "CLOSED"` polarity now costs real coverage and the argument needs restating before any code is written.
- Confirm no code path other than `sm.complete_stage("MERGE")` writes `MERGE = completed` (Risk 1's verification task).
- Record everything as the red-state paper trail for the PR body.
- **Do not measure `_lookup_pr`.** It is unchanged by this lane; its fixtures belong to #2869.

### 2. Write the failing tests first (both workstreams)
- **Task ID**: red-tests
- **Depends On**: measure-baseline
- **Validates**: tests/unit/test_sdlc_router_decision.py, tests/unit/test_sdlc_router_oscillation.py, tests/unit/test_sdlc_stage_query.py, tests/unit/test_sdlc_stage_marker.py, tests/unit/test_pipeline_complete_predicate.py, tests/integration/test_sdlc_next_skill_terminal.py (create)
- **Informed By**: spike-1 (the eight-cell matrix is the test matrix), spike-4 (the live issue numbers are the fixtures)
- **Assigned To**: `mutation-tester`
- **Agent Type**: test-engineer
- **Parallel**: false
- Write every assertion from Success Criteria against **unmodified** source and watch each one fail. Paste the real failure output into the PR body.
- **Exempt from "watch it fail": every Verification row labelled GREEN TODAY** (the two router anti-criterion greps, the `_lookup_pr` byte-unchanged row, the 8-route negative-control sweep, the xfail row, and the two lint/format rows). They are anti-criteria, controls, and hygiene checks; they start green and must stay green. Do not record them as red-state evidence, and do not count them in the mutation table. **A GREEN TODAY row still owes a mutation proof** — task 6 owns the two anti-criterion greps, and round 4 found both passing vacuously precisely because nobody ever watched them move.
- Every negative assertion must be paired with a positive one (`"error" not in result`) so a crash cannot go green — the #2826 discipline.
- **Assert the `gh issue view` argv, not just its result.** The round-3 blocker is a lookup that returns a confident wrong answer, so a result-shaped test cannot see it. Patch `subprocess.run`, capture `call_args`, and assert `--repo` is present.
- Write **no** `_lookup_pr` tests. That surface is unchanged; its tests belong to #2869.

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
- Then delegate to `agent.pipeline_complete.is_pipeline_complete(stage_states, "success", pr_open=None)` and **gate on the reason, not on truthiness**:
  `is_complete, reason = is_pipeline_complete(stage_states, "success", pr_open=None)` then
  `if not is_complete or reason != "merge_success": return None`. The `pr_open=None` argument alone is not a scope boundary — `agent/pipeline_complete.py:78-84` returns `(True, "docs_success_no_pr")` the moment `pr_open is False`, and the No-Gos section publishes the two-line recipe for supplying it. The explicit `reason` check plus the Failure-Path tripwire case (DOCS completed, MERGE absent, `pr_open=False` threaded through `meta`, guard must still return `None`) is what makes that No-Go go red instead of go quiet.
- Insert it **first in `GUARDS`, ahead of G1** (Resolved Question 1), with the rationale in the docstring.
- Map the terminal `guard_id` to its JSON shape in `tools/sdlc_next_skill.py::decide()` — an additive `complete: true` key alongside `blocked` (Resolved Question 2).
- **Update BOTH router-consumer skill bodies in this same commit** — `.claude/skills-global/do-sdlc/SKILL.md:136` **and** `.claude/skills/sdlc/SKILL.md:261` — so each exits reporting success on `complete: true` instead of escalating. They carry the identical `blocked` instruction; fixing one and not the other leaves the regression live on the other path. Neither is conditional on the parity test, which provably cannot hold either of them (`_GUARD_ROW_RE` is `^G\d+`).
- **Re-key `tests/unit/test_sdlc_router_decision.py::TestNoRuleBlockIsDistinguishable._unowned_state()`** (`:1692`) — drop `"MERGE": "completed"`, change nothing else. Verified to keep both assertions passing. Do **not** flip either assertion to `"TERMINAL"`; that retires the #2767b sentinel guarantee.
- Do **not** modify row 5, row 8e, or row 10 — the guard pre-empts them and they stay correct for non-terminal ledgers.
- Do **not** harden G1 against non-dict ledgers here; that fragility predates this lane and is out of scope.

### 4. WS-A — the `issue_state` meta key
- **Task ID**: build-issue-state-key
- **Depends On**: red-tests
- **Validates**: tests/unit/test_sdlc_stage_query.py
- **Informed By**: Risk 6 (the gate the guard reads), round-3 blocker 1 (the scoping is a safety property)
- **Assigned To**: `router-builder`
- **Agent Type**: builder
- **Parallel**: true
- Add the `issue_state` key to `_compute_meta` (`tools/sdlc_stage_query.py:515`), resolved **only when `MERGE == "completed"`** so non-terminal ticks cost nothing, once per invocation inside the existing `cached_target_repo_resolution()` scope, degrading to `None` on any failure rather than raising.
- **Thread `--repo` into the argv**, built exactly like `_fetch_pr_merge_state` at `:192`:
  `["gh","issue","view",str(issue_number), *(["--repo",resolved_repo] if resolved_repo else []), "--json","state","-q",".state"]`.
  This is not tidiness. `gh` resolves `GH_REPO` from the environment **before** cwd, so a bare call can answer about a foreign repository's issue #N and **exit 0**; a foreign CLOSED issue then terminates a live lane through the very gate built to prevent that. The fail-soft-to-`None` contract does not cover it, because a wrong-repo lookup succeeds.
- **Whitelist the parse**: `state = (proc.stdout or "").strip()`, then `issue_state = state if state in ("OPEN","CLOSED") else None`. The `!= "CLOSED"` polarity is fail-open only if a garbage value cannot be the literal `"CLOSED"`.
- Do **not** persist it. A stored copy goes stale on reopen and reintroduces Risk 6 through the cache instead of the ledger.
- Do **not** touch `_lookup_pr` or the two-pass block that sits beside this code. WS-B is #2869.

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
- **Depends On**: build-terminal-guard, build-issue-state-key, build-marker-scope
- **Assigned To**: `mutation-tester`
- **Agent Type**: test-engineer
- **Parallel**: false
- Revert each of WS-A and WS-C **individually** and record which tests re-red. A workstream with zero re-reds has no coverage — go back and write the missing test.
- Mutate the two halves of WS-A **separately**: the guard and the `issue_state` key. A test suite that reds only on the guard revert leaves the `--repo` scoping and the `{"OPEN","CLOSED"}` whitelist unpinned, which is exactly the round-3 blocker going unnoticed. Deleting `--repo` from the argv must red a named test on its own.
- Revert both workstreams together and confirm the two original reported shapes return.
- **Mutation-prove the two GREEN TODAY anti-criterion greps, which cannot be proved by reverting a workstream.** They count as coverage for nothing, but a gate that cannot fire is worse than no gate, and round 4 found both of them in exactly that state. Inject, measure, revert, re-measure — in the same step, leaving the tree clean:
  - **No-I/O row:** append `subprocess.run(["true"])` to `agent/sdlc_router.py`; `grep -c -e 'subprocess\.' -e 'requests\.' -e 'urllib' agent/sdlc_router.py` must go `0` → non-zero, and back to `0` on revert. (Proved at plan time on `96a44a505`: `0` → `1` → `0`.)
  - **Scope-leak row:** add a `lane_branch: str \| None = None` parameter to `_lookup_pr`'s signature in `tools/sdlc_stage_query.py`; the row's grep must emit the leaked line, and emit nothing again on revert. Then run the **control**: with only WS-A's `issue_state` change present in that file, the row must stay silent. Both legs are required — the round-4 form failed the second one.
- Produce the demonstrated-red table for the PR body.

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: mutation-check
- **Assigned To**: `lane-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Every checkbox in the Documentation section.
- The docstring annotations are the priority: they currently document known gaps as deliberate design, which is exactly what made these bugs look like features. Both now point at the issue that owns them (#2868, #2869) rather than at a change in this diff.

### 8. Record the disposition on all three issues
- **Task ID**: record-outcomes
- **Depends On**: document-feature
- **Assigned To**: `lane-validator`
- **Agent Type**: validator
- **Parallel**: false
- Comment the measured post-fix shape on **#2817** and **#2825** before either closes — #2826's explicit request.
- **On #2824, post an honest deferral disposition, and do not close it.** The PR refs it rather than closing it, so the issue must explain itself or it goes quiet under a merged PR that mentions it. State: the mechanism (head-ref authority gated on the branch existing on origin) is designed and measured at 0 gains / 0 losses / 11 unchanged; it keys on a recorded lane slug; #2494 and #2518 carry none; the eleven slugged ledgers are all in-flight lanes, so the slugged and reopened populations are **disjoint**; and [#2869](https://github.com/tomcounsell/ai/issues/2869) carries both the mechanism and the slug-adoption gap that gives it a population. Link the plan's *Technical Approach → WS-B* section, which is the finished design #2869 inherits.
- Confirm the PR body's closing refs read `Closes #2817`, `Closes #2825`, `Refs #2824` — a bare `#2824` mention closes nothing and records nothing.
- **File the follow-up issue for `scripts/migrate_completed_plan.py:362::_gh_issue_state`** (label `bug`): the unscoped `gh issue view` argv at `:366`, the `.lower()` + `"unknown"` vocabulary divergence, and the two call sites at `:392` and `:421`. Link it back from the No-Gos bullet that names it. This lane does not fix that file; the issue is what keeps the named-not-fixed disposition honest.

### 9. Final validation
- **Task ID**: validate-all
- **Depends On**: record-outcomes
- **Assigned To**: `lane-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run the entire Verification table.
- Confirm **both** `_lookup_pr` and `_gh_pr_search_issue_ref` are byte-unchanged in the merged diff — WS-B belongs to #2869 and WS-D to #2868, and neither may have leaked in.
- Confirm no `tools/` import and no I/O entered `agent/sdlc_router.py`.
- Confirm the `gh issue view` argv in the merged diff carries `--repo`.

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
| **Risk 6 — all live terminal ledgers are still CLOSED (guard coverage unchanged)** | `./.venv/bin/python -c "from agent.pipeline_ledger import PipelineLedger; import json,subprocess; ns=[int(r.issue_number) for r in PipelineLedger.query.all() if json.loads(r.stage_states_json or '{}').get('MERGE')=='completed']; print(len(ns), sum(subprocess.run(['gh','issue','view',str(n),'--repo','tomcounsell/ai','--json','state','-q','.state'],capture_output=True,text=True).stdout.strip()=='CLOSED' for n in ns))"` | both numbers equal. Re-measured **repo-scoped** 2026-08-19: **16 16**. A drop means the gate is now costing real coverage and the polarity needs re-argument. **The `--repo` flag is required**, not optional: without it `gh` resolves `GH_REPO` before cwd and the number describes an unknown repository. |
| **Round-3 blocker — the `gh issue view` argv is repo-scoped** | `./.venv/bin/python -c "from unittest.mock import patch; import tools.sdlc_stage_query as q, subprocess; r=patch.object(q.subprocess,'run',wraps=subprocess.run).start(); q._compute_meta({k:'completed' for k in ['ISSUE','PLAN','CRITIQUE','BUILD','TEST','REVIEW','DOCS','PATCH','MERGE']}, None, 2711); argvs=[c.args[0] for c in r.call_args_list if isinstance(c.args[0],list) and c.args[0][:3]==['gh','issue','view']]; print(bool(argvs) and all('--repo' in a for a in argvs))"` | output contains `True`. **Red today** (the key does not exist yet). Asserts the **argv**, because a wrong-repo lookup *succeeds* and no result-shaped assertion can see it. |
| **Round-3 blocker — `issue_state` is whitelisted to `{"OPEN","CLOSED"}`** | `./.venv/bin/python -c "from unittest.mock import patch,MagicMock; import tools.sdlc_stage_query as q; get=lambda o: [patch.object(q.subprocess,'run',return_value=MagicMock(returncode=0,stdout=o)).start(), q._compute_meta({'ISSUE':'completed','MERGE':'completed'},None,2711).get('issue_state'), patch.stopall()][1]; print(get('CLOSED')=='CLOSED' and all(get(o) is None for o in ['','error: not found','MERGED','closed','Closed']))"` | output contains `True`. **Genuinely red today — measured `False` on `96a44a505`.** The **positive pole is what makes it red**: `get('CLOSED') == 'CLOSED'` fails while the key is absent. The earlier absence-only form of this row printed `True` today (also measured) because `.get('issue_state')` is `None` for every input when the key does not exist — it could not distinguish "the whitelist is correct" from "the whitelist was never written". Prefer a real parametrized unit test over this one-liner; the row states the bar and the Test Impact entry owns the implementation. |
| Negative control — non-terminal still routes to merge | `./.venv/bin/python -c "from agent.sdlc_router import decide_next_dispatch as d; s={k:'completed' for k in ['ISSUE','PLAN','CRITIQUE','BUILD','TEST','REVIEW','DOCS','PATCH']}; s['_verdicts']={'REVIEW':{'verdict':'APPROVED','at':'2026-08-19T00:00:00Z'}}; print(d(s,{'pr_number':555},{'branch_exists':False}))"` | output contains `/do-merge`. The single-cell form of the row below. |
| **Negative control — the full 8-route non-terminal `/do-merge` sweep** | `./.venv/bin/python -c "from agent.sdlc_router import decide_next_dispatch as d; V={'REVIEW':{'verdict':'APPROVED','at':'2026-08-19T00:00:00Z'}}; B=['ISSUE','PLAN','CRITIQUE','BUILD','TEST','REVIEW','DOCS','PATCH']; mk=lambda m,v: dict({k:'completed' for k in B}, **({} if m=='absent' else {'MERGE':m}), **({'_verdicts':V} if v else {})); print(sum('/do-merge' in str(d(mk(m,v), {} if p is None else {'pr_number':p}, {'branch_exists':b})) for m in ['absent','pending','in_progress','failed'] for p in [None,555] for b in [False,True] for v in [True,False]))"` | output is `8`. **GREEN TODAY (negative control).** Measured `8` on `96a44a505` over 32 cells; all 8 hits are row 10 and all require `pr_number` **and** an APPROVED REVIEW verdict. Must still be `8` after WS-A — the terminal guard pre-empts only `MERGE == completed`, so this whole population is untouched. Exempt from task 2's "watch it fail" and excluded from Risk 5's mutation table (it is a control, not workstream coverage). |
| **No-Go tripwire — the guard never fires on the `docs_success_no_pr` leg** | `./.venv/bin/python -c "from agent.sdlc_router import guard_terminal_pipeline as g; docs=g({'DOCS':'completed'},{'issue_state':'CLOSED','pr_open':False},{}); term=g({k:'completed' for k in ['ISSUE','PLAN','CRITIQUE','BUILD','TEST','REVIEW','DOCS','PATCH','MERGE']},{'issue_state':'CLOSED'},{}); print(docs is None and 'TERMINAL' in str(term))"` | output contains `True`. **Red today** (`ImportError` — the guard does not exist). **Both poles**: the DOCS-shaped ledger must return `None` even with `pr_open=False` in `meta`, and the merge-shaped ledger must still fire, so a guard that returns `None` unconditionally cannot pass. This is the row that goes red the instant someone wires the No-Gos section's `meta["pr_open"] = _check_pr_open(issue_number)` recipe without doing the DOCS-population measurement that No-Go demands. |
| Terminal verdict, `PATCH` unsettled (ninth cell) | `./.venv/bin/python -c "from agent.sdlc_router import decide_next_dispatch as d; s={k:'completed' for k in ['ISSUE','PLAN','CRITIQUE','BUILD','TEST','REVIEW','DOCS','MERGE']}; s['PATCH']='pending'; s['_verdicts']={'REVIEW':{'verdict':'APPROVED','at':'2026-08-19T00:00:00Z'}}; print(d(s,{'pr_number':555,'issue_state':'CLOSED'},{}))"` | output contains `TERMINAL` (red today: returns `/do-merge` row 10) |
| Terminal guard survives a non-dict ledger | `./.venv/bin/python -c "from agent.sdlc_router import guard_terminal_pipeline as g; print(all(g(b,{},{}) is None for b in [None,'x',42,[]]))"` | output contains `True`. Asserts **the new guard alone**, not `evaluate_guards` — see the note under the table. |
| Terminal reason does not give G4's wrong remedy | `./.venv/bin/python -c "from agent.sdlc_router import decide_next_dispatch as d; s={k:'completed' for k in ['ISSUE','PLAN','CRITIQUE','BUILD','TEST','REVIEW','DOCS','PATCH','MERGE']}; r=str(d(s,{'issue_state':'CLOSED'},{})); print('TERMINAL' in r and 'dispatch reset' not in r)"` | output contains `True` — **both legs**: `TERMINAL` present AND `dispatch reset` absent. Asserting only the absence passes vacuously today (`Blocked(NO_RULE)` contains neither), which is what the critique caught. |
| `issue_state` resolved only on a terminal ledger | `./.venv/bin/python -c "from unittest.mock import patch,MagicMock; import tools.sdlc_stage_query as q; patch.object(q.subprocess,'run',return_value=MagicMock(returncode=0,stdout='CLOSED')).start(); neg=q._compute_meta({'MERGE':'pending'},None,2739).get('issue_state'); pos=q._compute_meta({'ISSUE':'completed','MERGE':'completed'},None,2711).get('issue_state'); print(neg is None and pos=='CLOSED')"` | output contains `True`. **Genuinely red today — measured `False` on `96a44a505`**, because the positive leg (`pos == 'CLOSED'`) fails while the key is absent. Both poles run under the same patched `gh` that always answers `CLOSED`, so after the key ships a non-terminal ledger can only satisfy `neg is None` by not calling `gh issue view` at all. The earlier absence-only form asserted nothing a missing key would not also satisfy. |
| #2825 fail-open closed (1785) | `./.venv/bin/python -c "import tools.sdlc_stage_marker as m; print(m._review_artifact_posted(1785,'tomcounsell/ai'))"` | output contains `False` |
| #2825 fail-open closed (2073) | `./.venv/bin/python -c "import tools.sdlc_stage_marker as m; print(m._review_artifact_posted(2073,'tomcounsell/ai'))"` | output contains `False` |
| #2539 control preserved (5 live issues) | `./.venv/bin/python -c "import tools.sdlc_stage_marker as m; print(all(m._review_artifact_posted(n,'tomcounsell/ai') for n in [2860,2831,2716,2734,2741]))"` | output contains `True` |
| **Anti-criterion — PR resolution is byte-unchanged** | `git diff main -- tools/sdlc_stage_query.py \| grep -E '^[-+]' \| grep -n -e '_lookup_pr' -e '_gh_pr_search_issue_ref' -e 'lane_branch'` | no output (exit 1). **GREEN TODAY (scope-leak check).** WS-B is #2869 and WS-D is #2868; the only permitted change in this file is the `issue_state` key inside `_compute_meta`. Counts as coverage for no workstream. **Repeated `-e` patterns, never `\|` alternation** — see the escaping note under this table. Mutation-proved on `96a44a505`: silent at baseline and silent under an `issue_state`-shaped insertion into `_compute_meta` (the sanctioned change), emits `3:+    lane_branch: str \| None = None,` under a WS-B-shaped edit to `_lookup_pr`'s signature. |
| Anti-criterion — no `tools/` import in the router | `grep -c '^from tools\|^import tools\|from tools\.' agent/sdlc_router.py` | match count == 0. **GREEN TODAY (regression anti-criterion).** Proves nothing about any workstream; counts as coverage for none of them in Risk 5's mutation table. |
| Anti-criterion — no I/O in the router (No-Go: DOCS-terminal leg) | `grep -c -e 'subprocess\.' -e 'requests\.' -e 'urllib' agent/sdlc_router.py` | match count == 0. **GREEN TODAY (regression anti-criterion).** Same disposition. **Repeated `-e` patterns, never `\|` alternation** — see the escaping note under this table. Mutation-proved on `96a44a505`: appending `subprocess.run(["true"])` to `agent/sdlc_router.py` takes the count 0 → 1; reverting returns it to 0. |
| Anti-criterion — `state="all"` gone from the **call site** | `grep -c 'state="all")' tools/sdlc_stage_marker.py` | match count == 0. Scoped to the call so it cannot match prose: today this returns **1** (the call at `:263`), while the unscoped `grep -c 'state="all"'` returns **2** because the rationale comment at `:259` literally opens `# state="all": ...`. The unscoped form is unsatisfiable alongside the Documentation task that keeps that comment. |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1. **GREEN TODAY (regression anti-criterion).** Counts as coverage for no workstream. |
| Lint clean | `python -m ruff check .` | exit code 0. **GREEN TODAY (hygiene row).** Not workstream coverage. |
| Format clean | `python -m ruff format --check .` | exit code 0. **GREEN TODAY (hygiene row).** Not workstream coverage. |

### How to read `\|` in a command cell

Every `\|` in the Command column above is the **markdown escape for a shell pipe** and nothing
else. GFM splits a table row on unescaped `|` even inside a code span, so a shell pipe has to be
written `\|` for the row to survive; strip the backslash before running the command.

**No pattern in this table uses `\|` for regex alternation.** Round 4 found two rows that did,
and both were gates that could not fire: in `grep -E`, `\|` is an escaped *literal* pipe, so
`grep -cE 'subprocess\.\|requests\.\|urllib'` collapses to one concatenated literal that matches
nothing and returns `0` — the row's pass value — no matter what is in the file. Reproduced:
`printf 'subprocess.run(x)\n' \| grep -cE 'subprocess\.\|requests\.\|urllib'` prints `0`.
Both rows now use **repeated `-e` patterns** instead, which need no pipe character at all, so the
rendered form and the raw form are the same string and each row is mutation-proved above.

The scope-leak row was broken in a second way the reproduction exposed: written with `\|` it was
not merely vacuous but *unsatisfiable in the other direction* — `git diff main -- <file> '\|'
grep ...` treats every later token as a pathspec, so the command printed the file's entire diff
whenever the file changed at all, including under the `issue_state` change WS-A is required to
make in that same file. Measured both ways on `96a44a505`.

One row keeps its backslashes on purpose: `grep -c '^from tools\|^import tools\|from tools\.'`
is **BRE** (no `-E`), where `\|` genuinely *is* alternation. It mutation-passes today. Check a
row's regex dialect before touching its escapes.

### Which rows can go red, and which cannot

Task 2 says "watch each one fail." That instruction applies **only to rows not labelled
GREEN TODAY**. The labelled rows are anti-criteria, scope-leak checks, and hygiene checks:
they start green, their job is to *stay* green, and Risk 5's mutation table must not count
any of them as coverage for any workstream. Recording them in the red-state paper trail as
if they had failed would manufacture rows of false evidence — which is precisely the
#2091 stale-fixture problem this plan is trying not to repeat.

Round 3 caught a live instance of the inverse hazard and it is worth keeping in view: a row
titled "recorded slug never calls the fuzzy leg" measured green against `#2494` **only
because `session/sdlc-2494` happens to exist on origin** (`28b0bf8b2`). It would have passed
while asserting a rule the plan had already falsified. That row is gone with WS-B, but the
lesson generalizes — before trusting a green row, ask which property of the fixture is
making it green, and whether that property is the one under test.

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

War room round 5, run 2026-08-19 against `main` @ `054f0f0fa`. Depth **FULL**
(force-FULL: the plan touches the doctrine paths `agent/sdlc_router.py`,
`.claude/skills/`, and `.claude/skills-global/`). Roster 3/3 complete, 0
ungrounded. **3 blockers, 2 concerns, 2 nits.**

**Execution note.** No agent-spawn tool was available in this driver's session, so
the three lenses were executed by the driver rather than by three independently
dispatched critics. The membership + grounding gate ran and passed 3/3. Weight this
round as one agent applying three lenses.

**Baseline re-verified before critiquing.** `git log 96a44a505..HEAD` restricted to
`agent/sdlc_router.py`, `agent/pipeline_complete.py`, `tools/sdlc_stage_query.py`,
`tools/sdlc_stage_marker.py`, `tools/sdlc_next_skill.py`, `tools/lane_identity.py`,
`scripts/migrate_completed_plan.py`, and both router-consumer SKILL.md files returns
**zero commits** across the 6 commits on `main`, so every round-4 measurement still
holds. Re-reproduced live: spike-1's eight-cell matrix cell for cell, the 8-route
non-terminal negative control (`8`), the terminal-ledger repo-scoped CLOSED sweep
(now **17 17**, up one from round 4 exactly as task 1's invariant predicts), WS-C's
three fail-opens (1785/2073/2104 -> True/True/True), the #2539 control 5/5, the
`_unowned_state()` re-key (`NO_RULE` with `MERGE` completed, absent, and `"pending"`),
and every cited file:line. Both round-4 structural blockers are genuinely fixed: the
Verification table parses as one contiguous run of 33 pipe-prefixed lines with a
uniform cell count and no orphaned Python, and both anti-criteria now mutation-move
under the `-e` form.

**Where round 5 converges.** The design is settled and nothing this round disturbs it.
All three lenses landed on the **enforcement machinery again** — but on rows and
criteria that round 4 wrote or rewrote, not on the ones it fixed. Two of the three
blockers are gates that cannot fire, in rows added or relied on by round 4's own
resolutions; the third is a one-line edit scoped against a four-site contract. The
carried Scope & Value concern is now measured rather than argued: 17/17 live terminal
ledgers route to `/do-merge` row 10, so WS-A is correct-going-forward on a population
that is currently empty.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | The No-Go tripwire row added at round 4 is a gate that cannot fire for the purpose the plan assigns it. The plan states the row "goes red the instant someone wires the No-Gos section's `meta["pr_open"] = _check_pr_open(issue_number)` recipe" and that "this No-Go is now enforced by a test rather than by an argument default". Mutation-proved on `054f0f0fa` against four guard variants built from the plan's own specified body: (A) as-specified -> row **True**; (B) the No-Go recipe wired, `pr_open` threaded from `meta`, reason gate kept -> row **True**; (C) reason gate deleted, `pr_open=None` -> row **True**; (D) both mutations together -> row False. The row detects neither single-edit regression it exists to catch. Its `docs is None` leg is shielded twice — by `is_pipeline_complete` returning `(False, "pr_state_unavailable")` when `pr_open` is `None`, and by the reason gate — so removing either leaves it green. Same defect in Success Criteria WS-A bullet 4, which calls the identical case "the tripwire the No-Go leans on". Round 4's BLOCKER class reproduced inside the row round 4 added to close a CONCERN, which means that CONCERN is not resolved. | pending | Replace the `docs` leg with one that isolates the reason gate: patch `agent.sdlc_router.is_pipeline_complete` to return `(True, "docs_success_no_pr")` and assert the guard still returns `None` on an otherwise-terminal ledger; add a second leg asserting the call shape (`pr_open` is passed as `None`) off the patched callable's `call_args`, the argv-style discipline the `--repo` row already uses. Patch target must be `agent.sdlc_router.is_pipeline_complete` — the name bound in the router's namespace — because a `from ... import` rebinds it and patching the source module would not take. Keep the merge-shaped positive leg so an unconditionally-`None` guard still cannot pass. Do NOT keep the current `g({'DOCS':'completed'}, {'issue_state':'CLOSED','pr_open':False}, {})` form: measured, it returns `None` whether or not the reason gate exists. |
| BLOCKER | History & Consistency | Two contracts are mutually exclusive and one of them is a Success Criterion. "**`_lookup_pr` and `_gh_pr_search_issue_ref` are byte-unchanged in the merged diff**" is asserted in Success Criteria, repeated by task 9, and is the `lane-validator`'s defining duty — while Documentation -> Inline Documentation mandates annotating **both** functions' docstrings, and verified in source both docstrings sit **inside** their function bodies. A builder cannot do both. The gate meant to enforce it cannot see the violation: mutation-proved by applying exactly the two sanctioned annotations, `git diff --stat` reports **7 insertions** into `tools/sdlc_stage_query.py` inside both function bodies while the anti-criterion row stays **silent** (exit 1, row passes). Task 9's two discharge paths give opposite answers on the same tree — a validator reading the diff must block the PR, a validator running the table row must pass it. Round 3's mutually-exclusive-contracts class and round 4's gate-that-cannot-see-the-leak class arriving together, in the row round 4 rewrote. | pending | Restate the criterion as what it means — "`_lookup_pr` and `_gh_pr_search_issue_ref` change in **docstring text only**: no signature, no control flow, no argv" — and gate it on executable lines. Runnable form that discriminates: `inspect.getsource` each function, strip the docstring via `ast.get_docstring`, SHA-256 the remainder, compare against the value recorded at plan time. Mutation-check both poles: adding a docstring line must leave the hash unchanged; adding `lane_branch: str \\| None = None` to `_lookup_pr`'s signature must change it. Update task 9 and the `lane-validator` role in the same edit or a validator keeps discharging the literal reading and blocks a compliant PR. Do NOT resolve this by dropping the annotations — "a stale comment presenting a gap as intent is what let these bugs survive this long" is the strongest argument in the Documentation section. |
| BLOCKER | Risk & Robustness | The `blocked` contract in `.claude/skills-global/do-sdlc/SKILL.md` lives at four load-bearing sites and the plan scopes the edit to one line. Verified verbatim on `main`: **:23** Hard Rule 3 — "**NEVER continue past a `blocked` decision** — surface the reason to the human and stop. Guards block for a reason."; **:136** — the line the plan names; **:295** Step 3e — "Router returned `blocked` -> already stopped in 3a", where the only success exit is the `/do-merge` + `gh pr view ... MERGED` path a terminal ledger can no longer reach once the guard pre-empts row 10; **:303** Step 4 Final Report — "**Outcome**: merged / blocked (with guard + reason) / cap reached", a taxonomy with no terminal member. Because Q2 settles on the additive key, the payload keeps `blocked: true`, so every one of those sites still classifies the terminal shape. A one-line edit leaves a numbered Hard Rule instructing surface-and-stop-to-the-human on the exact payload the Success Criterion requires be routed to "loop-exit-success, not to human escalation" — the regression the plan calls "a blocking dependency of WS-A, not a follow-up", surviving its own remedy. Nothing holds this: the plan itself establishes `_GUARD_ROW_RE = re.compile(r"^G\d+")` cannot parse a `TERMINAL` id and the parity test runs one direction only. | pending | Enumerate the sites in the checkbox and in task 3 instead of naming a single line; anchor on text, not offsets, since the numbers drift as the body is edited (`grep -n 'blocked' .claude/skills-global/do-sdlc/SKILL.md` returns them all). Hard Rule 3 needs the narrowest carve-out that keeps it a rule: "NEVER continue past a `blocked` decision **unless it carries `complete: true`** — that shape is a finished pipeline, not a guard escalation; exit the loop reporting success." Add a terminal exit condition to Step 3e beside the merged one and a fourth outcome value to Step 4's taxonomy. Step 5's lease-release list needs **no** change and must not get one: on a `MERGE == completed` ledger the lease was already released by `complete_stage("MERGE")`'s `_release_run_best_effort`, which Risk 1 depends on. `.claude/skills/sdlc/SKILL.md` genuinely has only the one operative site (`:261`, verified) — its other `blocked` mentions at `:169`/`:202`/`:251`/`:256` are the guard table and the ISSUE_LOCKED contract — so the asymmetry is real and the plan should say so rather than treating the two files as symmetric. |
| CONCERN | Scope & Value | WS-A's headline severity claim is measured empty on live data — round 4's Scope & Value concern, carried forward `pending` by the revision pass's own instruction and now re-measured rather than re-argued. Through the real `query_enriched` -> `tools.sdlc_next_skill._build_context` -> `decide_next_dispatch` path over every `PipelineLedger` row with `MERGE == "completed"`: **17/17 route to `/do-merge` row 10**, every one with `pr_number` resolved by #2826's two-pass lookup and `branch_exists=False`. Zero live ledgers land in either `pr_number`-absent cell. Yet the Problem section still says all three shapes "**reproduce live on this repo today**", spike-1 still reports "the originally filed shape, **still live**" and "**row 5's identical false rebuild is still live**", and its Impact bullet still reads "an active failure mode in two of four `pr_number`-absent cells". Reachable is not occurring. This plan cut WS-B for exactly this shape ("its measured effect on live data is **zero**", "a mechanism with no population"), so the same standard is owed to WS-A's own justification, which will be read straight into the PR body. The design does not change; only the words do. | pending | Add the live route sweep to task 1 (which already walks this population for the CLOSED check) and to the Verification table so the claim is re-measured at build time. It must go through the real context builder, never a hand-built `{"branch_exists": ...}` dict, which omits `stage_artifacts_verified` and cannot see a G8 interaction: for each ledger whose **flat** `stage_states_json` has `MERGE == "completed"`, inside `cached_target_repo_resolution()`, call `query_enriched(issue_number=n)`, then `tools.sdlc_next_skill._build_context(None, n, res["stages"], res["_meta"])`, then `decide_next_dispatch`. Expected today 17/17 `/do-merge` row 10 and **17/17 TERMINAL after WS-A** — which makes it positive coverage, NOT a control, so it is not exempt from task 2's watch-it-fail and it does belong in Risk 5's mutation table. Keep the synthetic eight-cell matrix exactly as is; it bounds reachability, which is the right argument for building the guard. Change only the three occurrence claims — "still live" -> "reachable and unowned; zero live instances today" — and restore the accurate severity sentence the record lists as Dropped. |
| CONCERN | Risk & Robustness | The Verification table's only router-suite row runs `tests/unit/test_sdlc_router_oscillation.py` — the file Test Impact declares "**NO CHANGE EXPECTED**" with zero `"MERGE": "completed"` fixtures — while `tests/unit/test_sdlc_router_decision.py`, which the same section calls "the one router test file WS-A provably reds" and which task 3 requires re-keying, has **no row at all**. Task 9 ("Run the entire Verification table") and the `lane-validator` role therefore both discharge without ever running the suite the change is known to break. A third router suite the plan never names anywhere, `tests/unit/test_sdlc_router.py`, carries **35** further `decide_next_dispatch(` call sites and an `_ALL_COMPLETED` helper at `:1158`; measured, its `MERGE` is `"pending"` (`:1166`) so nothing in it is terminal today, but the exhaustiveness claim ("the **only** `"MERGE": "completed"` fixture in any router test") rests on a grep scoped to two files, and this is the third. | pending | Add a Verification row for `tests/unit/test_sdlc_router_decision.py` and one for `tests/unit/test_sdlc_router.py`, and widen Test Impact's REVIEW sweep from "the other 94 call sites" of one file to all three router suites. The exhaustiveness claim is actually TRUE repo-wide — `grep -rn '"MERGE": "completed"' tests/ --include='*.py'` returns exactly one hit, `tests/unit/test_sdlc_router_decision.py:1706` — so no fixture rework is owed; the fix is evidentiary, stating the claim over `tests/` rather than over two named files. Add `test_sdlc_router.py` to task 6's mutation runs as well: with 35 router call sites and no `issue_state` in any of its metas, a guard that ignored the issue-state gate would red there, making it a free second witness for the Risk 6 mutation. |
| NIT | History & Consistency | Two Verification rows import the guard by name — `from agent.sdlc_router import guard_terminal_pipeline as g` — but no task, Success Criterion, or Documentation checkbox ever names the function. Task 3 says only "Insert it **first in `GUARDS`**, ahead of G1". A builder who names it `guard_terminal`, `guard_g9_terminal`, or `guard_pipeline_terminal` leaves two rows failing with `ImportError`, which reads as a real regression rather than a naming mismatch — on the two rows carrying the Risk 1 and No-Go bars. | pending | (NIT — exempt.) Name the function `guard_terminal_pipeline` explicitly in task 3. |
| NIT | Scope & Value | Tasks 1-5 each carry a **Validates** field naming the test files they move (task 1 explicitly declares "no test files — this produces the red-state record"). Tasks 6 through 9 carry none, and 6-9 also drop the **Informed By** field that 1-5 all have. Task 6 is the mutation-proof step and task 9 is final validation — the two tasks whose output the reviewer most needs to locate. | pending | (NIT — exempt.) Add **Validates** to tasks 6-9, or state explicitly that they validate against the Verification table rather than a test file, matching task 1's honest "no test files" form. |

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
(it must not land before #2869's mechanism), and the 2542 caveat.

**Q4 — WS-B scope: specify it completely and keep it, or cut it to #2869 and ship WS-A + WS-C?**
**Resolved: cut it. Deferred to [#2869](https://github.com/tomcounsell/ai/issues/2869); this
lane ships WS-A + WS-C and refs #2824 without closing it.**

The mechanism is not in doubt. Two rounds made it correct, and the finished design is
preserved verbatim under *Technical Approach → WS-B* for #2869 to pick up. What round 3
falsified was the **argument for keeping it here**.

That argument was: WS-A's terminal guard now stands down on an OPEN issue (Risk 6), so every
reopened ledger goes back to fuzzy PR resolution, so WS-B is the only remaining protection
for the reopened case. Measured 2026-08-19, that protection is **empty**. WS-B keys on a
recorded lane slug. Both live #2824 instances carry `slug: None, slug_source: "unresolved"`,
and the eleven ledgers that *do* carry a slug (#713, #2694, #2738, #2739, #2748, #2817,
#2823, #2836, #2845, #2853, #2867) are all in-flight lanes rather than reopened ones. **The
slugged population and the reopened population are disjoint**, so WS-B and the reopened case
do not touch. All three critique lenses reached this independently.

On its own numbers WS-B is **0 gains / 0 losses / 11 unchanged** — it changes no answer on
any live ledger — against a real recurring cost of ~0.8s per enriched tick of every slugged
lane, on the pre-PR hot path, with a memoization design that was still unspecified.

Three things made the cut clean rather than merely defensible:

1. **No code coupling.** WS-A touches `agent/sdlc_router.py`, `tools/sdlc_next_skill.py`,
   and `_compute_meta`'s `issue_state` key; WS-C touches one argument in
   `tools/sdlc_stage_marker.py`. Neither reads `_lookup_pr`'s ladder order, so removing WS-B
   is a deletion rather than an unpicking.
2. **The dependency runs the other way from how it was filed.** #2869 is WS-B's
   **precondition**, not its follow-up: every slug that starts naming a real branch is one
   more answer WS-B can resolve authoritatively, and today's slug vocabulary is why the
   population is empty. Shipping WS-B first buys a mechanism with no population; shipping
   #2869 first creates the population that makes the mechanism measurable.
3. **It dissolves five of the seven round-3 findings** — blocker 2's contradictory
   `_lookup_pr` contract, the memoization concern, the value-versus-cost concern, and the
   `_check_branch_pushed` nit — rather than patching each symptom of a workstream that was
   not earning its place.

**What this costs, stated plainly.** #2824 stays open and #2494 / #2518 stay broken. The
lane's closing refs are `Closes #2817`, `Closes #2825`, `Refs #2824`, and task 8 posts the
disposition on #2824 itself rather than letting the issue go quiet under a merged PR that
mentions it. Nothing regresses: the reopened case routes exactly as it does on `main` today,
which is a self-healing wrong answer rather than a latch.

**What would reverse this.** A measurement showing the slugged and reopened populations
overlapping — the first reopened issue that carries a recorded slug naming a real branch.
That is the number #2869 should re-take before building WS-B, and it is the honest trigger
for reviving it.
