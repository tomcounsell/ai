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

_placeholder_

## Data Flow

_placeholder_

## Why Previous Fixes Failed

_placeholder_

## Architectural Impact

_placeholder_

## Appetite

_placeholder_

## Prerequisites

_placeholder_

## Solution

_placeholder_

## Failure Path Test Strategy

_placeholder_

## Test Impact

_placeholder_

## Rabbit Holes

_placeholder_

## Risks

_placeholder_

## Race Conditions

_placeholder_

## No-Gos (Out of Scope)

_placeholder_

## Update System

_placeholder_

## Agent Integration

_placeholder_

## Documentation

_placeholder_

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
