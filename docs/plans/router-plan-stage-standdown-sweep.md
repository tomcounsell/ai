---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-09
tracking: https://github.com/tomcounsell/ai/issues/3249
last_comment_id:
---

# Router plan-stage stand-down sweep, and G3's merge leg verdict gate

## Problem

Two defects in `agent/sdlc_router.py`, planned and shipped as ONE lane because they live
in the same file and splitting them guarantees a rebase conflict.

**#3249 — plan-stage rows keep answering for lanes that have left the plan stage.**
`_rule_plan_not_critiqued` (row 2, `:1153`) is the only plan-stage row with **no** PR
step-aside at all, so it dispatches `/do-plan-critique` on a lane with an open PR. Three
siblings — row 1 `_rule_no_plan` (`:1121`), row 2c `_rule_critique_in_progress_no_verdict`
(`:1650`), row 3 `_rule_critique_needs_revision` (`:1180`) — each carry the `pr_number`
step-aside but are missing the `BUILD` one, so they answer with a plan-stage skill while
BUILD is `in_progress`. This is one defect class, not four symptoms: #3237 and #3227 were
two earlier instances of it, and each cost a supervisor a manual override on a live lane
(#3195, #3181). The issue is explicit that the fix "should close on a **sweep** ... with
the stand-down condition expressed once rather than hand-copied into each rule predicate."

**#3260 — G3 leg 1 routes to `/do-merge` on stage markers alone.** At `:518`:

```python
if review_status == STATUS_COMPLETED and docs_status == STATUS_COMPLETED:
    target = SKILL_DO_MERGE
    suffix = "review clean and docs complete"
```

`review_verdict_norm` is computed immediately above (`:516`) and simply not consulted. The
two legs below it both check the verdict and one checks head freshness, so the
**most-taken** leg is the only one that skips both. Two states route to MERGE that should
not: (1) REVIEW marker `completed` with a `CHANGES REQUESTED` verdict — the `elif` that
catches it is unreachable because leg 1 already matched; (2) an APPROVED verdict recorded
against an older head, which occurs on every PR whose DOCS stage produces a commit.

The realized impact today is a wasted dispatch, not an unauthorized merge:
`tools/merge_predicate.py` independently refuses both states and demonstrably did so across
the #3246/#3245/#3244 batch. The concern is defense-in-depth and drift — the router telling
lanes to attempt merges the gate will reject is exactly the disagreement that later gets
"fixed" by relaxing the predicate.

**#3260 widened to G6 (ratified).** `guard_g6_terminal_merge_ready` (`:976`) has the
identical absent-key fail-open on the OTHER terminal `/do-merge` fast-path. Its own comment
at `:971-975` already asserts the behavior it does not have — *"never fast-path a
head_sha-stale APPROVED verdict ... (or the live-head lookup failed, which fails closed
toward stale)"* — while the code returns "not stale" on an absent signal. Same lie, one
guard over, same diff.

## Freshness Check

Baseline: `main` @ `a15c5eab7` (2026-09-10). Both issues recorded their recon against
`fd0847a50` (2026-09-09).

**Disposition: Unchanged.**

- `git log origin/main --since=2026-09-08 -- agent/sdlc_router.py tests/unit/sdlc_router_decision/`
  returns exactly one commit, `2fb7df519` (#3246), which predates both recons. The file the
  fix modifies has not moved since either issue was written.
- Every cited `file:line` re-read verbatim at the current HEAD and confirmed at the **same
  line number**, no drift:
  - `:518` — G3 leg 1, quoted text identical.
  - `:527` — leg 3's `_review_verdict_head_is_stale` call.
  - `:936` `guard_g6_terminal_merge_ready`; `:971-975` the WS3d comment; `:976` the stale call.
  - `:1121` `_rule_no_plan`; `:1153` `_rule_plan_not_critiqued`; `:1180`
    `_rule_critique_needs_revision`; `:1650` `_rule_critique_in_progress_no_verdict`.
  - `:1385` `_review_verdict_head_is_stale`, absent-key contract unchanged in its docstring.
  - `:1594` `_rule_critique_verdict_stale` (row 2b), canonical stand-down pair at `:1641-1644`.
  - `:1998` row 8f, `:2029` row 10 — both still call the existing predicate; untouched here.
- Producer re-verified: `tools/sdlc_next_skill.py:512-531` sets `context["pr_head_sha"]`
  **unconditionally** whenever `pr_number` is set and a REVIEW verdict is recorded — a real
  SHA, or `""` plus `pr_head_sha_lookup_failed` on lookup failure. The key is omitted only
  when there is no PR or no recorded verdict.
- Rows 4b (`~:1214`) and 4c (`~:1249`) each still check `meta.get("pr_number")` **twice**
  within the same predicate.
- Sibling issues re-checked: #3237 CLOSED, #3227 CLOSED, #2062 CLOSED, PR #3246 MERGED
  (2026-09-08). None changed the root cause; #3246 is the prerequisite and it landed.
- Both recon validators pass: `validate_issue_recon.py 3249` and `3260` both `continue`.

**Overlap check.** `grep -l sdlc_router docs/plans/*.md` names only
`resilience-simplification-three-tier.md` and `sdlc-control-plane-asserted-facts.md`,
neither of which touches these rows or guards. Two lanes run in parallel on this machine —
`pgrep-sweep-finish` (`scripts/`, `tools/process_lookup.py`, `monitoring/`) and a lane on
`agent/agent_session_queue.py` — and neither touches any file this plan modifies. No overlap.

**Bug reproduction.** Both defects were reproduced at recon time by direct
`decide_next_dispatch` probes (recorded verbatim in each issue's Recon Summary), and the
code paths those probes exercise are byte-identical at this baseline. Reproduction is
re-run as the RED step of this plan's tests rather than as a throwaway probe.

## Prior Art

- **PR #3246** (merged 2026-09-08, `2fb7df519`) — "Router: stand row 2b down past the plan
  stage, add G3's missing DOCS leg". Closed #3237 and #3227. It established both the
  canonical stand-down shape (`agent/sdlc_router.py:1641-1644`) and the test file this plan
  extends. It deliberately left row 2 and the three siblings alone to keep the diff
  conservative on load-bearing routing, and filed #3249 for the sweep. It also surfaced
  #3260 via its round-2 `code-quality` judge and correctly left it out of scope.
- **#3237** (CLOSED) — row 2b outranked row 5, leaving a lane with BUILD `in_progress` and
  an armed concern gate no exit from the plan loop. Symptom one of the class.
- **#3227** (CLOSED) — G3's ladder had no DOCS leg, so an approved PR with docs pending
  re-dispatched `/do-pr-review` forever. Symptom two.
- **#2062** (CLOSED, WS3d) — introduced `_review_verdict_head_is_stale` and the
  router↔`tools/merge_predicate` freshness convergence. G6's `:971-975` comment is its
  artifact; this plan finishes what that comment promised, hence `Refs #2062`.
- **PR #2797** (merged 2026-08-19) — "G9 owns the BLOCKED_ON_CONFLICT verdict". Same
  file, unrelated guard; noted only to confirm no live overlap.

No prior attempt tried and failed at either of these two fixes, so there is no failed-fix
pattern to avoid — only the *scope* pattern: fixing this class one reported symptom at a
time is how it survived this long.

## Research

No external research performed — and none is warranted. This work is entirely internal:
one Python module (`agent/sdlc_router.py`), its unit tests, and one docs file. No external
library, API, service, or ecosystem pattern is involved, so Phase 0.7's skip condition
applies verbatim. All evidence comes from the codebase itself and from the two issues'
recon probes, re-verified above.

## Spike Results

Small appetite caps spikes at two. Both load-bearing assumptions were resolvable by direct
inspection at plan time and were resolved inline rather than dispatched — a spike agent
would have read the same two files.

### spike-1: Requiring a *present* `pr_head_sha` costs live lanes nothing

- **Assumption**: "On every path that can reach G3 leg 1 or G6, `context['pr_head_sha']` is
  already present, so a predicate that returns False on an absent key never strands a real lane."
- **Method**: code-read (`tools/sdlc_next_skill.py:505-533`, resolved inline).
- **Result**: **CONFIRMED.** `_build_context` sets the key unconditionally when
  `meta['pr_number']` is truthy AND a REVIEW verdict is recorded — a real SHA on success, or
  `""` plus `pr_head_sha_lookup_failed` on failure. The comment at `:507-511` states the
  fail-closed contract explicitly: it "never silently omits the key." The key is omitted
  only when there is no PR or no recorded verdict. G3 leg 1 requires `review_status ==
  completed`, and G6 gates on `pr_number` (`:953-955`) and `REVIEW_APPROVED` (`:966-969`),
  so both producer conditions hold on every reachable path.
- **Confidence**: high.
- **Impact if false**: live lanes would fall through to `/do-pr-review` instead of merging —
  noisy but not unsafe. This is why the design accepts the closed-by-default posture: the
  failure mode of requiring presence is a re-review, and the failure mode of not requiring
  it is a merge dispatch on unverified evidence.

### spike-2: Nothing is stranded once rows 1/2/2c/3 stand down on BUILD

- **Assumption**: "Rows that stand down on `BUILD in (in_progress, completed)` have a
  correct landing row, without widening row 5."
- **Method**: code-read plus the recon probes recorded in #3249.
- **Result**: **CONFIRMED.** Row 5 `_rule_branch_exists_no_pr` fires on
  `BUILD == in_progress OR context['branch_exists'] is True`; the branch half answers
  regardless of BUILD status, and a BUILD cannot reach `completed` without pushing its lane
  branch. #3249's second probe pass re-ran all five states with the shared stand-down applied
  (monkeypatched) and every one routed correctly: rows 1/2/2c/3 with `BUILD=in_progress` and
  no PR → `Dispatch(/do-build, row_id='5')`; row 2 with an open PR and `REVIEW=pending` →
  `Dispatch(/do-pr-review, row_id='7')`. None reached `Blocked`.
- **Confidence**: high.
- **Impact if false**: a lane would dead-end at `Blocked('no matching dispatch rule')`. The
  residual `Blocked` subcase (BUILD settled AND no live branch) is inherited from #3246
  unchanged — there is nothing left to resume there, so it is correct, not a regression.

## Data Flow

The change sits at one point in a three-hop chain and reads a signal produced upstream.

1. **Producer — `tools/sdlc_next_skill.py::_build_context` (`:505-533`).** Given
   `stage_states` and `meta`, when `meta['pr_number']` is set and a REVIEW verdict exists in
   `_verdicts` or `meta['latest_review_verdict']`, it calls `_fetch_pr_head_sha` (which
   resolves through `tools/pr_head_resolver.py::resolve_pr_head_sha`, never a bare `gh`
   read) and writes `context['pr_head_sha']` — the SHA on success, `""` plus
   `context['pr_head_sha_lookup_failed'] = True` on failure. **The key is never omitted when
   both conditions hold.**

2. **Consumer — `agent/sdlc_router.py::decide_next_dispatch` (`:2306`).** Guards run first
   (G1–G6, G9), then `DISPATCH_RULES` in row order. The router itself makes no `gh` calls;
   it reads only what `context` carries. Three freshness readings coexist after this change:
   - `_review_verdict_head_is_stale` — *"is there positive evidence of staleness?"*, inert
     on an absent key. Keeps its exact current contract and its four current call sites
     (`:527` G3 leg 3, `:976` G6 → moves, `:1998` row 8f, `:2029` row 10).
   - `_review_verdict_head_is_verified_fresh` (**new**) — *"is there positive evidence of
     freshness?"*, False on an absent key. Used **only** on terminal `/do-merge` dispatch:
     G3 leg 1 (`:518`) and G6 (`:976`).
   - `_plan_stage_stood_down` (**new, shared**) — *"has this lane left the plan stage?"*,
     True when `meta['pr_number']` is set or `BUILD in (in_progress, completed)`.

3. **Downstream authorization — `tools/merge_predicate.py`.** Untouched. It keeps its
   independent verdict and freshness checks and remains the authorization gate. This change
   converges the *router* onto the predicate; it does not move authorization into the router.

The stand-down flows the other way: rows 1/2/2c/3 and 4b/4c call `_plan_stage_stood_down`
and return False, letting evaluation fall through to row 5 (pre-PR) or rows 7–10 (post-PR).

## Why Previous Fixes Failed

No prior fix attempted either of these two defects, so nothing was applied at the wrong
layer or aimed at a symptom. What failed was **scope**, twice:

- **#3237 and #3227 were each fixed as a single row/guard.** PR #3246 fixed row 2b and G3's
  DOCS leg and consciously left the four siblings alone. That was the right call for that
  diff's risk posture, but it is the third consecutive round of fixing one instance of a
  class whose other instances were already visible. #3249 exists specifically to break that
  pattern, which is why narrowing this plan to row 2 would reproduce the failure it was
  filed against.
- **#2062 shipped the freshness predicate and a comment describing stronger behavior than
  the code has.** G6's `:971-975` comment claims a lookup failure "fails closed toward
  stale" — true for the EMPTY sentinel, false for an absent key. A correct mechanism plus a
  comment that overstates it is how the gap stayed invisible for a release cycle.

The lesson carried into this plan: express the condition **once**, apply it to **every**
site of the class in the same diff, and prove each site reachable by probe rather than by
reading the predicate.

## Architectural Impact

Contained. One module changes (`agent/sdlc_router.py`), plus its unit tests and one docs
page. No schema, no Popoto model, no config, no new dependency, no CLI surface.

Three architectural notes worth recording:

- **Two freshness predicates is the design, not duplication.** They answer genuinely
  different questions and the difference is load-bearing at exactly one place: an absent
  signal. Non-terminal consumers (rows 8f/10, G3 leg 3) legitimately want the inert reading;
  terminal merge dispatch does not. Each gets its own named predicate with the absent-key
  behavior stated in its docstring, so neither call site has to remember an implicit rule.
- **The stand-down becomes a named concept.** Today "this lane has left the plan stage" is
  an idiom hand-copied into six predicates (and twice within two of them). After this change
  it is one function with one docstring, which is what makes the *next* plan-stage row
  correct by default.
- **Router/predicate convergence continues.** This is the fourth step of the #2062 program:
  the router's routing opinion now matches `tools/merge_predicate`'s authorization opinion on
  both terminal merge paths. Divergence between them is the drift this closes.

## Appetite

**Small.** Roughly a day. The bounding facts: two new small predicates, six predicate call
sites edited, two one-line guard changes, and one test file extended. Every state to be
tested has already been probed and recorded in the two issues' Recon Summaries, so the
expensive part — establishing what the router actually does — is already paid for.

If the work threatens to exceed the appetite, the thing to cut is **not** the sweep (that is
the issue) and **not** the G6 widening (ratified). Cut the rows 4b/4c de-duplication, which
is hygiene rather than a behavior fix.

## Prerequisites

- **PR #3246 merged** (`2fb7df519`, 2026-09-08) — establishes the canonical stand-down shape
  and creates `tests/unit/sdlc_router_decision/test_sdlc_router_decision_plan_rule_standdown.py`,
  the file this plan extends. ✅ satisfied.
- **Lane identity recorded** — slug `sdlc-3249`, worktree
  `/Users/valorengels/src/ai/.worktrees/sdlc-3249`, branch `session/sdlc-3249`, both already
  at `origin/main`. ✅ satisfied. Do not re-derive.
- No new dependency, service, credential, or migration. Nothing else blocks build.

## Solution

All code changes are in `agent/sdlc_router.py`. All test changes are in
`tests/unit/sdlc_router_decision/test_sdlc_router_decision_plan_rule_standdown.py`.

### 1. New predicate `_review_verdict_head_is_verified_fresh` (added next to `:1385`)

A narrow sibling of `_review_verdict_head_is_stale`, **not** a modification of it. The
existing predicate's absent-key contract (*"key ABSENT from context → signal not supplied →
False (inert; other rules own routing)"*) is legitimate for its non-terminal consumers and
**must not change**.

```python
def _review_verdict_head_is_verified_fresh(stage_states: dict, meta: dict, context: dict) -> bool:
    """Return True only on POSITIVE evidence that the REVIEW verdict judged the live head.

    Narrow sibling of :func:`_review_verdict_head_is_stale`, for TERMINAL
    ``/do-merge`` dispatch only (G3 leg 1, G6). The two differ on exactly two
    inputs, both deliberate:

    - ``pr_head_sha`` ABSENT from context → **False** here (no evidence, no
      merge), where the stale predicate returns False meaning "inert".
    - no recorded REVIEW verdict → **False** here, where the stale predicate
      returns False meaning "the no-verdict recovery rows own this".

    Requiring presence is free in production: ``tools/sdlc_next_skill._build_context``
    sets ``pr_head_sha`` unconditionally whenever ``pr_number`` is set and a
    REVIEW verdict is recorded — a real SHA, or ``""`` plus
    ``pr_head_sha_lookup_failed`` on lookup failure. Both conditions hold on
    every path that reaches either call site, so the key is never absent for a
    live lane; requiring it closes the hole against non-CLI and future callers.
    """
    if "pr_head_sha" not in context:
        return False
    head_sha = context.get("pr_head_sha") or ""
    if not head_sha:
        return False  # fail-closed lookup-failure sentinel
    if not _latest_review_verdict(stage_states, meta).strip():
        return False
    recorded_head = _latest_review_head_sha(stage_states, meta)
    if not recorded_head:
        return False  # unattributable verdict is never "verified fresh"
    return recorded_head.lower() == head_sha.lower()
```

### 2. G3 leg 1 requires an APPROVED verdict AND verified-fresh head (`:518`)

```python
if (
    review_status == STATUS_COMPLETED
    and docs_status == STATUS_COMPLETED
    and REVIEW_APPROVED in review_verdict_norm
    and _review_verdict_head_is_verified_fresh(stage_states, meta, context or {})
):
    target = SKILL_DO_MERGE
    suffix = "review clean and docs complete"
```

Fall-through is already correct and needs no other edit: `CHANGES REQUESTED` now reaches
leg 2 (`/do-patch`), and a stale or unverifiable APPROVED reaches leg 4 (`/do-pr-review`).

### 3. G6 uses the same predicate (`:976`) — RATIFIED WIDENING, strictly one line

```python
if not _review_verdict_head_is_verified_fresh(stage_states, meta, context):
    return None
```

replacing `if _review_verdict_head_is_stale(stage_states, meta, context):`. The WS3d comment
directly above (`:971-975`) is updated in the same hunk to describe the absent-key case as
well, so the comment stops overstating the code — that overstatement is the whole reason
this hunk is in scope.

**Conditions on this change, non-negotiable:**
- Strictly the predicate swap plus its comment. Do **not** touch G6's other gates
  (`pr_number` `:953-955`, `pr_merge_state`, `ci_all_passing`, the DOCS gate, the
  `REVIEW_APPROVED` gate `:966-969`) and do **not** touch row 8f.
- **The PR body must name the deliberate widening** and cite the WS3d comment at
  `agent/sdlc_router.py:971-975`, so a reviewer seeing an out-of-scope hunk understands why
  it is there.
- Fall-through is safe and verified: absent key → not verified fresh → `return None` →
  dispatch table → row 8f → `/do-pr-review`.
- Add `Refs #2062` alongside the closers.

### 4. New shared predicate `_plan_stage_stood_down` (added above row 1, near `:1121`)

The #3249 condition, expressed **once**:

```python
def _plan_stage_stood_down(stage_states: dict, meta: dict) -> bool:
    """Return True when the lane has moved past the plan stage (#3249).

    One definition for the step-aside that rows 1, 2, 2c and 3 were each
    supposed to carry and hand-copied inconsistently. Two signals:

    - ``pr_number`` set — a PR-stage lane has no plan-stage question left to
      answer; rows 7-10 own that state.
    - ``BUILD`` at ``in_progress`` or ``completed`` — the plan was accepted when
      the build was dispatched. Row 5 (``_rule_branch_exists_no_pr``) owns the
      pre-PR resume: its predicate is ``BUILD == in_progress OR
      context['branch_exists'] is True``, so the branch half answers regardless
      of BUILD status. Nothing is stranded.
    """
    if meta.get("pr_number"):
        return True
    return stage_states.get("BUILD") in (STATUS_IN_PROGRESS, STATUS_COMPLETED)
```

### 5. Route the four plan-stage rows through it

| Row | Function | Line | Today | Change |
|-----|----------|------|-------|--------|
| 1 | `_rule_no_plan` | `:1121` | `pr_number` only | replace with `_plan_stage_stood_down` |
| 2 | `_rule_plan_not_critiqued` | `:1153` | **neither** | add `_plan_stage_stood_down` as the first check |
| 2c | `_rule_critique_in_progress_no_verdict` | `:1650` | `pr_number` only | replace with `_plan_stage_stood_down` |
| 3 | `_rule_critique_needs_revision` | `:1180` | `pr_number` only | replace with `_plan_stage_stood_down` |

Each becomes, as the first statement in the predicate body:

```python
if _plan_stage_stood_down(stage_states, meta):
    return False
```

Row 2's docstring gains a sentence recording that it had no step-aside at all and why
(#3249); rows 1/2c/3's existing step-aside comments are rewritten to name the shared helper
rather than restating the condition.

### 6. Fold rows 4b/4c's duplicated `pr_number` checks into the helper

Rows 4b (`~:1214`) and 4c (`~:1249`) each check `meta.get("pr_number")` **twice** in the
same predicate — the hand-copied duplication #3249 exists to eliminate. Replace the leading
`if meta.get("pr_number") or stage_states.get("BUILD") == STATUS_COMPLETED: return False`
with `if _plan_stage_stood_down(stage_states, meta): return False`, and delete the redundant
second `if meta.get("pr_number"): return False` further down.

**Behavior-identical, and this must be argued explicitly, not assumed.** The helper is
broader than the check it replaces (it also catches `BUILD == in_progress`), but both rows
end with `return build_status in (None, "pending", "ready")`, which already excludes
`in_progress`. So no input changes answer.

**Keep rows 4a/4c's `build_status in (None, "pending", "ready")` gates.** They are strictly
NARROWER than the shared stand-down — they also exclude `BUILD == failed`. The helper is
added *alongside* them, **never substituted for them**; substituting would be a behavior
change. Row 4a is otherwise untouched.

### 7. Docs

Update `docs/features/gh-stale-state-verdict-gate.md` to describe the two-predicate split
and the terminal-dispatch rule (see **Documentation**).

### What this plan does NOT change

- `_review_verdict_head_is_stale` itself, or its remaining call sites at `:527` (G3 leg 3),
  `:1998` (row 8f) and `:2029` (row 10).
- `tools/merge_predicate.py` — authorization stays where it is, and is not relaxed.
- Row 5. See **No-Gos**.

## Failure Path Test Strategy

All new tests go in
`tests/unit/sdlc_router_decision/test_sdlc_router_decision_plan_rule_standdown.py`, using the
existing helper style (`_build_in_progress_states`, `_plan_context`, `_approved_pr_states`,
`_approved_pr_meta`). Run with `scripts/pytest-clean.sh`, targeted node IDs only, never bare
`pytest`, never `pkill -f pytest`.

**Two hard rules, both from the ratified design:**

1. **Every new assertion is proven RED before it is made green**, and the RED output is
   captured as evidence in the PR body. An assertion that has never failed proves nothing.
2. **Reachability is proven by direct `decide_next_dispatch` probes, not by inference** —
   one probe per row and per guard leg touched. Reading a predicate and concluding it fires
   is exactly the reasoning that let this class survive three rounds.

### Coverage matrix

| # | Path | Input state | Expected after fix |
|---|------|-------------|--------------------|
| T1 | row 1 | `PLAN=in_progress`, no plan doc, `BUILD=in_progress`, no PR, branch exists | `Dispatch(/do-build, row_id='5')` |
| T2 | row 2 | `PLAN=completed`, `CRITIQUE=pending`, open PR #999, `last_dispatched_skill=/do-build`, `REVIEW=pending` | `Dispatch(/do-pr-review, row_id='7')` |
| T3 | row 2 | same, no PR, `BUILD=in_progress`, branch exists | `Dispatch(/do-build, row_id='5')` |
| T4 | row 2c | `CRITIQUE=in_progress`, no verdict, `BUILD=in_progress`, no PR, branch exists | `Dispatch(/do-build, row_id='5')` |
| T5 | row 3 | `NEEDS REVISION` verdict, `BUILD=in_progress`, no PR, branch exists | `Dispatch(/do-build, row_id='5')` |
| T6 | rows 1/2/2c/3 negative control | `BUILD=pending`, no PR | each row still fires its own skill — the sweep must not disable the rows |
| T7 | rows 4b/4c | the `pr_number` and `BUILD=completed` states each row already refuses | unchanged answers (refactor is behavior-identical) |
| T8 | row 4a/4c narrowing guard | `BUILD=failed`, with-concerns verdict, no PR | rows 4a/4c still decline — proves the narrower gate was not replaced |
| T9 | G3 leg 1 | `REVIEW=completed`, `DOCS=completed`, verdict `CHANGES REQUESTED` at live head | `Dispatch(/do-patch, row_id='G3')` (leg 2) |
| T10 | G3 leg 1 | markers completed, `APPROVED` recorded against an OLDER head | `Dispatch(/do-pr-review, row_id='G3')` (leg 4) |
| T11 | G3 leg 1 | markers completed, `APPROVED`, **`pr_head_sha` ABSENT from context** | `/do-pr-review`, NOT `/do-merge` |
| T12 | G3 leg 1 | markers completed, `APPROVED`, `pr_head_sha == ""` + `pr_head_sha_lookup_failed` | `/do-pr-review`, NOT `/do-merge` |
| T13 | G3 leg 1 positive control | markers completed, `APPROVED` at the live head | still `Dispatch(/do-merge, row_id='G3')` |
| T14 | **G6, absent key** | `pr_number`, `pr_merge_state=CLEAN`, `ci_all_passing=True`, `DOCS=completed`, `APPROVED`, **`pr_head_sha` ABSENT** | `guard_g6_terminal_merge_ready` returns `None`; `decide_next_dispatch` → `/do-pr-review` via row 8f |
| T15 | G6 positive control | same but `pr_head_sha` matches the verdict's head | still `Dispatch(/do-merge, row_id='G6')` |
| T16 | G6 stale control | same but `pr_head_sha` differs | still `None` → row 8f (pre-existing behavior, pinned) |
| T17 | `_review_verdict_head_is_stale` unchanged | absent key, with a recorded verdict | still returns `False` — pins that the existing contract was not modified |

**T14 is the mandatory RED for the G6 widening.** The RED proof must be on the **ABSENT-key**
case specifically; T16's stale-key path already passes today and proves nothing about this
change. If T14 is green before the code change, the test is wrong — fix the test, do not
proceed.

### Guard-ordering hygiene for the G3 probes

G3 only engages when `last_dispatched_skill` / `proposed_skill` puts it in scope, and G6 can
answer first. Follow the recon's shape: set `last_dispatched_skill=/do-plan-critique` and
`pr_merge_state="DIRTY"` on the G3 probes so G6 cannot pre-empt them, and use
`pr_merge_state="CLEAN"` only on the G6 probes.

## Test Impact

- [ ] `tests/unit/sdlc_router_decision/test_sdlc_router_decision_plan_rule_standdown.py` —
      UPDATE: extend with T1–T17 above. The existing `TestRow2bStandsDownOnceBuildStarted`
      and `TestG3DocsLeg` classes stay as-is; new classes are added alongside them, and the
      module docstring's line "whose missing `pr_number` step-aside is tracked as #3249" is
      updated to record that #3249 has landed.
- [ ] `tests/unit/test_sdlc_router.py:1484-1488` — UPDATE (verify only): this is the direct
      unit test of `_review_verdict_head_is_stale`. That function is deliberately unchanged,
      so these cases must stay green **unmodified**. If any of them needs editing, the
      existing predicate was touched and the change is wrong. T17 pins the same contract from
      the router-decision side.
- [ ] `tests/unit/sdlc_router_decision/test_sdlc_router_decision_dispatch_rows.py` — AUDIT:
      the row-by-row table tests. Any case asserting rows 1/2/2c/3/4b/4c fire while
      `BUILD in (in_progress, completed)` or a PR is open is asserting the defect and must be
      UPDATED to the new expected routing, with the change called out in the PR body.
- [ ] `tests/unit/sdlc_router_decision/test_sdlc_router_decision_terminal.py` and
      `test_sdlc_router_decision_convergence.py` — AUDIT: these exercise G6 and the merge
      fast-path. Any case that reaches `/do-merge` without a `pr_head_sha` in context now
      routes to `/do-pr-review` and must be UPDATED to supply the key (the shape production
      actually produces) rather than by relaxing the assertion.
- [ ] `tests/unit/sdlc_router_decision/test_sdlc_router_decision_with_concerns.py` — AUDIT:
      rows 4b/4c live here. The refactor is behavior-identical, so every case must stay green
      unmodified; a failure means step 6 changed behavior and must be reverted to the literal
      duplicated checks.

No expected-failure markers exist for these defects — `grep -rn 'pytest.mark.xfail\|pytest.xfail('
tests/unit/sdlc_router_decision/` returns nothing, so there are no xfails (decorator or
runtime) to convert.

## Rabbit Holes

- **Unifying the two freshness predicates.** They look like near-duplicates and the pull to
  collapse them into one parameterized function is strong. Resist it: the difference is one
  boolean on one input, and the parameter would immediately be got wrong at a call site. Two
  named functions with two docstrings is the deliverable.
- **"While I'm here" guard reordering.** `GUARDS` evaluation order
  (`[T, G1, G2, G3, G4, G9, G8, G7, G5, G6]`) is pinned and load-bearing. Nothing in this
  plan requires touching it, and a reorder would invalidate every probe in the coverage
  matrix.
- **Auditing all ~25 dispatch rows for stand-downs.** The sweep is over the **plan-stage**
  rows the issue names (1, 2, 2c, 3, plus the 4b/4c duplication). PR-stage and patch-stage
  rows have different correct step-asides and are a separate class.
- **Rewriting the recon probes as a reusable harness.** Tempting, and out of scope. Use the
  existing helper functions in the test file.
- **Chasing `tools/merge_predicate.py` into agreement.** It already agrees. This change moves
  the router toward the predicate; touching the predicate reverses the direction of the fix.
- **Fixing the "Known gap — stale REVIEW verdict after PATCH" note** in
  `.claude/skills-global/do-sdlc/SKILL.md:278`. That describes a *different* staleness axis
  (`/do-patch`-relative, `_review_verdict_is_stale`) and is not touched here.

## Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| The G6 widening reads as an out-of-scope hunk and gets bounced at review | Medium | PR body names it explicitly as a ratified widening and cites the WS3d comment at `agent/sdlc_router.py:971-975`. Non-negotiable, not optional prose. |
| A live lane stalls because `pr_head_sha` is genuinely absent on some path not surveyed | Low | spike-1 traced the sole producer and both call sites' gates. Worst case is a `/do-pr-review` dispatch, not a bad merge — the failure mode is noise, not damage. |
| The 4b/4c refactor silently changes behavior | Medium | Argued explicitly in Solution step 6 (the trailing `build_status in (None, pending, ready)` already excludes `in_progress`), and pinned by T7/T8 plus the unmodified `test_..._with_concerns.py` suite. Any red there means revert step 6. |
| Existing tests encode the defect and get "fixed" by relaxing assertions | Medium | Test Impact lists the audit targets by file with explicit dispositions and requires supplying the real context key rather than weakening an assertion. Every changed existing assertion is called out in the PR body. |
| Standing four rows down strands a lane at `Blocked` | Low | spike-2 plus #3249's recorded second probe pass: every state lands on row 5 or row 7. The residual `Blocked` subcase (BUILD settled AND no live branch) is inherited from #3246 unchanged. |
| Rebase conflict with a parallel lane | Low | The two live lanes touch `scripts/`, `tools/process_lookup.py`, `monitoring/` and `agent/agent_session_queue.py` — disjoint from every file here. #3260 and #3249 are one lane precisely to avoid conflicting with each other. |
| Someone widens row 5 to catch the stood-down lanes | **High** | See No-Gos. This is the known trap and the failure is silent. |

## Race Conditions

The router is a pure function: `decide_next_dispatch(stage_states, meta, context)` performs
no I/O, makes no `gh` calls, and mutates no shared state. Nothing in this change introduces
async work, shared mutable state, or a cross-process handoff, so there is no new timing
hazard to design against.

One **pre-existing** ordering fact this change interacts with, worth stating so it is not
mistaken for a race: `context['pr_head_sha']` is a point-in-time read taken by
`tools/sdlc_next_skill._build_context` before the router runs. A commit landing between that
read and the merge dispatch would leave the router acting on a head that is one commit
behind. That window exists today and is unchanged here; it is closed downstream by
`tools/merge_predicate.py`, which re-reads at merge time. The change strictly narrows the
window (the router now refuses more, never fewer, states) and must not be relied on to close
it — that remains the merge predicate's job.

## No-Gos (Out of Scope)

- **DO NOT widen row 5 under any circumstances.** This is *the* trap, carried from #3246 and
  restated in #3249: *"do not widen row 5 to absorb these states. Row 5 precedes row 6, so
  widening it diverts `TEST == failed` lanes away from `/do-patch`."* Once rows 1/2/2c/3
  stand down on BUILD, those lanes need somewhere to land and row 5 is the tempting
  catch-all. It is the wrong place and **the failure is silent** — a lane with failing tests
  gets sent to `/do-build` instead of `/do-patch` and nothing reports it. The correct landing
  is row 5's **existing, unmodified** predicate: `build_status == in_progress OR
  context['branch_exists'] is True`. The branch half already answers regardless of BUILD
  status. **No widening is required, and none is permitted.**
- **DO NOT modify `_review_verdict_head_is_stale`.** Its absent-key → `False` contract is
  correct for its non-terminal consumers and is documented in its own docstring. The new
  behavior arrives as a sibling.
- **DO NOT change rows 8f or 10.** They already call the existing predicate and need no
  change; row 8f is the landing row this plan depends on.
- **DO NOT replace rows 4a/4c's `build_status in (None, "pending", "ready")` gates** with the
  shared helper. Those gates are strictly narrower (they also exclude `BUILD == failed`);
  substituting would be a behavior change. Add alongside, never substitute.
- **DO NOT touch G6's other gates** (`pr_merge_state`, `ci_all_passing`, the DOCS gate) or
  the `REVIEW_APPROVED` gate. One line plus its comment.
- **DO NOT relax or modify `tools/merge_predicate.py`.**
- **DO NOT touch the parallel lanes' files**: `scripts/`, `tools/process_lookup.py`,
  `monitoring/`, `agent/agent_session_queue.py`.
- **DO NOT edit the shared checkout root** at `/Users/valorengels/src/ai` for code. All code
  work happens in `/Users/valorengels/src/ai/.worktrees/sdlc-3249` on branch
  `session/sdlc-3249`. Plan and `.md` docs are the exception: they commit directly on `main`.

## Update System

No update-system changes required. This is a pure change to an in-repo Python module and its
tests: no new dependency, no new config file, no new env key, no new launchd plist, no
migration. `scripts/remote-update.sh` and the `/update` skill propagate it as an ordinary
code change on `main`.

One propagation note that is **not** an update-script change: the dispatch/guard tables in
`.claude/skills-global/do-sdlc/SKILL.md` are hardlinked to `~/.claude/skills/` by `/update`.
Editing that file in-place (never replace-and-rename, which breaks the hardlink) means a
routine `/update` after merge carries the corrected G3/G6 descriptions fleet-wide. Per
project convention, run `/update` after this merges so running services pick up the new ref.

## Agent Integration

No agent integration required — this is entirely internal to the router. No new CLI entry
point in `pyproject.toml [project.scripts]`, and the bridge imports nothing new.

The router already reaches the agent through the existing surface: `sdlc-tool next-skill`
(`tools/sdlc_next_skill.py`) calls `decide_next_dispatch` and is the only caller that
assembles a real `context`. That path is unchanged in shape — the same `pr_head_sha` key the
producer already writes is simply read by one more predicate. The `/sdlc` router skill and
`/do-sdlc` supervisor consume `next-skill`'s output and need no change beyond the doc updates
below.

## Documentation

- [ ] Update `docs/features/gh-stale-state-verdict-gate.md` — document the two-predicate
      split: `_review_verdict_head_is_stale` (inert on an absent signal, for non-terminal
      consumers) vs `_review_verdict_head_is_verified_fresh` (positive evidence required, for
      terminal `/do-merge` dispatch only), and state the rule that terminal merge dispatch
      uses the latter.
- [ ] Update `.claude/skills-global/do-sdlc/SKILL.md:248` — G3's ladder description: leg 1 now
      reads "`/do-merge` (REVIEW and DOCS complete, verdict `APPROVED`, head verified fresh)".
- [ ] Update `.claude/skills-global/do-sdlc/SKILL.md:254` — G6's condition row: add "AND the
      REVIEW verdict's head is verified fresh against `context['pr_head_sha']`".
      Edit in place; do not replace-and-rename (it is hardlinked to `~/.claude/skills/`).
- [ ] Update the plan-stage rows in the same SKILL.md dispatch table so rows 1, 2, 2c and 3
      record the plan-stage stand-down, matching what row 2b already documents.
- [ ] Update the "Open-PR step-asides" note (`.claude/skills-global/do-sdlc/SKILL.md:263`) to
      name the shared `_plan_stage_stood_down` condition instead of listing rows individually.
- [ ] Update the module docstring of
      `tests/unit/sdlc_router_decision/test_sdlc_router_decision_plan_rule_standdown.py`,
      which currently says row 2's missing step-aside "is tracked as #3249" — describe the
      new status quo, no historical artifact.
- [ ] No new feature doc and no `docs/features/README.md` entry: this modifies behavior
      already documented by `gh-stale-state-verdict-gate.md` and the do-sdlc dispatch tables
      rather than adding a capability.
- [ ] No `docs/infra/` doc: no new dependency, service, external API call, quota, or
      deployment change.

## Success Criteria

_placeholder_

## Team Orchestration

_placeholder_

## Step by Step Tasks

_placeholder_

## Verification

_placeholder_

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

_placeholder_
