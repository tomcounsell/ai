---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-09-18
tracking: https://github.com/tomcounsell/ai/issues/3418
last_comment_id: none
---

# Nightly triage filing moves into the detector, and collapses by root cause

## Problem
## Freshness Check
## Prior Art
## Research
## Data Flow
## Why Previous Fixes Failed
## Architectural Impact
## Appetite
## Prerequisites
## Solution
## Failure Path Test Strategy
## Test Impact
## Rabbit Holes
## Risks
## Race Conditions
## No-Gos (Out of Scope)
## Update System
## Agent Integration
## Documentation
## Success Criteria
## Team Orchestration
## Step by Step Tasks
## Verification
## Critique Results

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|----------------------|
| BLOCKER | Structural Check | Required sections `## Documentation`, `## Update System`, `## Agent Integration`, and `## Test Impact` are all empty placeholders — this repo's plan-critique addendum treats any missing/placeholder required section as a HIGH-severity blocker. | pending | Populate `## Test Impact` with `tests/unit/test_nightly_regression_tests.py` dispositions (UPDATE/DELETE/REPLACE per changed function), `## Documentation` with a `docs/features/` checkbox task, `## Update System` with any config/env changes, and `## Agent Integration` with whether the filing path exposes any new tool surface. |
| BLOCKER | Risk & Robustness | `## Race Conditions` is empty, yet the bug itself is a check-then-act race: concurrent/replayed triage waves each check "does this issue exist" then create it, and two waves can both pass the check before either write lands. Moving filing into the detector does not remove this race unless the detector serializes the check+create into one atomic step. | pending | A check-then-act pattern ("search GitHub issues for fingerprint X, if none found then create") is not atomic against a second concurrent detector invocation or an overlapping nightly run; without a compare-and-swap primitive (an idempotency key GitHub dedupes on, or a lock held for the full check+create span) the same duplication bug can reappear one layer down. |
| BLOCKER | Scope & Value | The title bundles two independently-scoped fixes — moving issue-filing ownership into the detector (an architecture change), and changing the collapsing key to per-file/root-cause (a dedup-algorithm change) — with no `## Solution` text justifying why they ship together rather than as two separately appetite-sized changes. | pending | Add a "Scope" subsection under `## Solution` that separately itemizes (a) moving issue-filing from the LLM triage session into `scripts/nightly_regression_tests.py`, and (b) changing the collapsing key from "identical normalized first error line" to per-file/root-cause grouping, each with its own acceptance check. |
| BLOCKER | History & Consistency | `## Why Previous Fixes Failed` is empty even though the tracking issue states this is explicitly a repeat of the #3170 failure class, where "the ledger and lookup-instruction defenses added after #3170 did not hold here." This is the single most important input for judging whether the new detector-side approach actually closes the gap. | pending | Add a paragraph citing the #3170 ledger/lookup-instruction design and stating explicitly why a Python-side, single-process detector closes the gap (it doesn't rely on an LLM session re-deriving lookup state across replayed turns) — this is the load-bearing justification for the architecture move. |
| CONCERN | Risk & Robustness | `## Solution` has no design for the per-file/root-cause collapsing logic, the N=3 umbrella threshold, or the "query GitHub for issues actually created during the run" cap mechanism, and no stated failure-mode contract for that cap query (timeout, stale/paginated result). | pending | If the cap check is "GitHub API call returns count of issues created this run, compare to K", a network/API failure on that call has no stated fallback; silently defaulting to "assume 0 filed so far" on error would reintroduce the exact over-filing bug (#3382-#3405) this plan exists to fix. |
| CONCERN | Risk & Robustness | No populated Rollback/Update System content describing how to revert if detector-side filing misbehaves in production (e.g., over-collapses distinct root causes into one umbrella issue). | pending | Add a break-glass note: a feature flag or env var that reverts to LLM-session filing (or disables auto-filing entirely) if the new collapsing logic is found to be under- or over-aggressive after deployment; state whether duplicate issues closed as part of this work get reopened or stay closed on rollback. |
| CONCERN | Scope & Value | `## Success Criteria` is empty; the only success measures available anywhere (in the tracking issue, not the plan) are purely technical issue-counts, with no human-facing validation that an engineer actually gets clearer signal. | pending | Add a manual-read success criterion: sample N umbrella issues created by a re-run and confirm a human (not the filer) can state the root cause without opening individual node logs. |
| CONCERN | History & Consistency | `## Prior Art` is empty despite four directly relevant closed issues (#3170, #3131, #3134, #3075, prior passes this change supersedes) and two related open issues to coordinate with (#3347, #3243) named in the tracking issue. | pending | Populate `## Prior Art` with the #3170/#3131/#3134/#3075 lineage, and cross-reference #3243 (hostname/session_id stamping) in both `## Prior Art` and `## Solution` since it touches the same filing code path this plan is about to move. |
| NIT | History & Consistency | `## Solution` / `## No-Gos (Out of Scope)` are both empty, so it's unverifiable whether "close the enumerated duplicate issues as part of this work" (a tracking-issue acceptance criterion) is captured as in-scope work or silently dropped. | pending | When drafting, explicitly list "close duplicate #3382-#3405 copies" under Solution/Success Criteria so it isn't lost between the issue body and the plan. |

## Open Questions
