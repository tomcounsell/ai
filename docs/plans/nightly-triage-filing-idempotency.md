---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-05
tracking: https://github.com/tomcounsell/ai/issues/3170
last_comment_id:
---

# Nightly Triage Filing Idempotency

## Problem


On 2026-08-24 one nightly triage dispatch filed the *same* failing node three times — #2960 (20:35:17), #2972 (20:40:13), #2990 (20:45:57) — with three different body templates, from the same worktree at the same commit `fd848ed83`. Across the whole wave that was 39 issues (#2960–#2999), later closed wholesale as duplicates of #3001.

Every one of those three filings ran the prompt's instruction to "search ALL issues — open AND closed — for the EXACT title" and every one of them came up empty, because a search-index read lags issue creation by minutes and each wave searched inside the lag window its own predecessor had opened issues in. The turn was replayed (why, is #3161's question); the filing step had no defense of its own.

**Current behavior:**

The detector has genuinely good idempotency for its *own* decisions. `dispatch_findings` reads live REST state via `open_issues()` and `closed_issue_dispositions()`, partitions nodes into already-open / closed-not-planned / to-file, and comments rather than re-files. Then it throws the answer away: `maybe_dispatch_triage_session(single_nodes, dry_run=dry_run)` hands the triage agent a bare `list[str]` of node ids, and `_build_triage_prompt` tells that agent to go re-derive from GitHub what the script already knew — using the one read mechanism this module documents as unreliable in exactly this window.

Nothing else stands between a replayed turn and a duplicate issue. There is no record on disk of what the session already filed, so a fresh-context replay starts from zero every time.

**Desired outcome:**

A replayed triage turn re-files nothing. Three independent defenses, cheapest first:

1. The agent never consults the lagging search index — the prompt hands it the exact `gh issue list --state all` REST command.
2. The agent mostly does not need to look at all — the script hands it the disposition it already resolved, per node, with the issue number when one exists.
3. If both of those are somehow bypassed, a session-local ledger on disk records every issue the session opened, and the agent consults it before opening the next.

## Freshness Check


**Baseline commit:** `feebe32aa` (`origin/main` at plan time; local `main` was 3 behind and was pulled before planning)
**Issue filed at:** 2026-09-05T06:46:08Z
**Disposition:** Minor drift

**File:line references re-verified (all at `feebe32aa`):**

- `scripts/nightly_regression_tests.py` — `_build_triage_prompt` — claimed to say "search ALL issues". **Still holds**, now at line 1458 (`def`), with the offending wording at line 1470: `"been triaged before. For EACH node below, search ALL issues — open AND "`. Verified present on `origin/main`, not just locally.
- `scripts/nightly_regression_tests.py` — `dispatch_findings` — claimed to "already partition nodes into open / closed-not-planned / to-file before dispatching". **Still holds**, at line 2447. The partition helpers are `partition_environmental` (1668), `partition_already_open` (2261), `partition_closed_matches` (2083). The discarding call site is line 2664.
- Commit `8524e765b` ("Quiet the nightly regression detector") — cited as the origin of the REST-not-search principle. **Confirmed present on main**; the principle is now written into the module's own constants at lines 322–340 and guarded by `test_open_issues_uses_the_rest_list_not_the_lagging_search`.
- `.worktrees/nightly-triage-{slug}` (cited in the #2972 body) — **still the live convention**: `agent/worktree_manager.py:20` sets `WORKTREES_DIR = ".worktrees"` and every lane lands at `.worktrees/{slug}/`. The slug is computed at `scripts/nightly_regression_tests.py:2334`.

**Cited sibling issues/PRs re-checked:**

- **#3161** — still OPEN. This issue was split from it; #3161 retains the "why was the turn replayed" investigation.
- **#3075** — **CLOSED 2026-09-05T02:13:35Z**, four hours before #3170 was filed, via merged PR **#3142**. This is the significant one: #3170's fix item 2 describes work partly done by #3075. See Prior Art.
- **#3001** — CLOSED. Its Work Item 3 is the parent scope ("make triage issue-filing idempotent — the same node must not produce a second issue on a re-run"). #3075 was that work item; this issue is its remainder.

**Commits on main since issue was filed (touching referenced files):**

- `35a225c19` "Nightly environmental classification escalates after consecutive nights and widens the closed dedup window" — **irrelevant to this fix.** It raised `CLOSED_ISSUE_LIST_LIMIT` to 4000 and added the consecutive-night environmental escalation. It touches neither `_build_triage_prompt` nor the `maybe_dispatch_triage_session` call site. Its `escalated` nodes flow into the ordinary filing path, so they inherit whatever this plan builds, with no special handling needed.

**Active plans in `docs/plans/` overlapping this area:** none. No plan doc references the nightly detector; the most recent plan touching adjacent ground is `sdlc-control-plane-asserted-facts.md` (2026-09-04), which is unrelated.

**Notes:** The branch `session/nightly-triage-idempotency-3075` exists locally and on origin and is **not** an ancestor of main — but its four commits are on main by content, squash-merged as `97354ce1e` via PR #3142. It is a leftover lane, not pending work. **The build must branch from `main`, never from that branch**, or it will reintroduce a duplicate of already-landed code.

## Prior Art

_placeholder_

## Research

_placeholder_

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
