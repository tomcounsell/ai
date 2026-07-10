---
status: Planning
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-07-10
tracking: https://github.com/tomcounsell/ai/issues/2003
last_comment_id: none
---

# SDLC Pipeline Substrate: Single Run Ownership, Live-Ref PR Resolution, Real Merge-Gate Enforcement

## Problem

Workstream A of the resilience-simplification program
([`docs/plans/resilience-simplification-three-tier.md`](resilience-simplification-three-tier.md),
items T1.7 + T2.1 + T2.2). Three defects on one file surface, each observed in production:

**Current behavior:**
1. **Run ownership is keyed to OS process identity.** The #1954 issue lock decides ownership
   by a per-process `holder_token` (`models/session_lifecycle.py:792-816`). Because
   `/do-sdlc` fans out to short-lived `sdlc-tool` subprocesses, the lock self-collided the
   day it shipped (#1971) and was patched with an `SDLC_HOLDER_TOKEN` env seam + a
   gitignored run file + three "re-export before every state call" prose blocks in the
   skill bodies. The logical unit of ownership — a pipeline run — is modeled nowhere.
2. **Merge enforcement is duplicated in the router and hollow at the choke point.** The
   merge-guard hook (`.claude/hooks/validators/validate_merge_guard.py`) passes iff
   `data/merge_authorized_{PR}` *exists* — proving someone created a file, not that the
   gate ran. Router row 10b dispatches merge when stage states are unavailable and a PR is
   open, actively weakening the "never merge unfinished work" invariant that rows 9/10/G6
   and the `/do-merge` skill each partially enforce (#1944 incident class).
3. **PR resolution has a dead signal and a split-brain writer.** `sdlc_next_skill.py:136`
   computes `branch_exists` against branch shape `session/sdlc-{N}` — a shape this repo
   never creates (canonical is `session/{slug}`; `sdlc_stage_query.py`'s own docstring calls
   the `sdlc-{N}` form "fabricated") — so row 5's context signal is permanently False.
   `pr_number` is resolved through a ladder (session field → meta → gh search → head) with
   no single writer.

**Desired outcome:** one logical `run_id` that every subprocess, lock, and dispatch record
keys off with zero env threading; one merge predicate evaluated at the one choke point
every merge path crosses; one PR-resolution path anchored on the session record and live
refs; no permanently-dead router signals.

## Freshness Check

**Baseline commit:** `7a7f9c3d`
**Issue filed at:** 2026-07-10 (same day)
**Disposition:** Minor drift — one of the three workstream items partially landed since recon.

**File:line references re-verified:**
- `models/session_lifecycle.py:792-816` — per-process holder token + `SDLC_HOLDER_TOKEN`
  env seam — **still holds**.
- `.claude/hooks/validators/validate_merge_guard.py` — existence-only auth-file check —
  **still holds**.
- `tools/sdlc_next_skill.py:122-136` — `branch_exists` checks `session/sdlc-{N}` —
  **still holds**, now with a comment ("the canonical branch name for SDLC work") that
  directly contradicts `sdlc_stage_query.py`'s docstring and both SKILL.md bodies
  (canonical is `session/{slug}`).
- `tools/sdlc_stage_query.py:348-360` `--search`-first untrusted ladder — **DRIFTED, partially
  fixed**: PR #1998 (merged 2026-07-10 06:23, closing #1987) added
  `_gh_pr_search_issue_ref` + `_body_references_issue` — fuzzy search results are now
  trusted only with a word-boundary `Closes/Fixes/Resolves #{N}` body reference, and the
  head fallback now uses the canonical `session/{slug}` shape.

**Cited sibling issues/PRs re-checked:**
- #1987 — **CLOSED 2026-07-10T06:23Z** by PR #1998. The false-match defect and the #1950
  pipeline blockage are resolved. This plan's T1.7 scope shrinks accordingly (see Solution).
- #1971, #1954, #1944 — closed with the fixes this plan consolidates (unchanged).
- #1979 — open, build in flight, touches `session_health.py`/`valor_session.py` — disjoint
  surface, no coordination needed.

**Commits on main since issue was filed (touching referenced files):**
- `268d5500` "Validate PR body issue-reference before trusting fuzzy search match (#1987)
  (#1998)" — partially addresses item 3; root cause of the false match is fixed; the
  single-writer consolidation and dead `branch_exists` signal remain.

**Active plans in `docs/plans/` overlapping this area:** the program plan
(`resilience-simplification-three-tier.md`) — this plan *is* its Workstream A;
`sdlc_issue_ownership_lock.md` (shipped, #1954) — superseded by T2.1 here, not concurrent.

## Prior Art

- **#1954 / PR #1956**: added the issue-ownership lock (per-process holder token). Worked
  for the worker, self-collided for `/do-sdlc` subprocess fan-out.
- **#1971 / PR #1972**: `SDLC_HOLDER_TOKEN` env seam. Unblocked `/do-sdlc` but moved the
  invariant into prompt convention (re-export blocks).
- **#1944 / PR #1990**: `/do-merge` skill Step 2b now reads the DOCS stage marker — the
  *skill*-side gate is fixed; the *hook* still checks file existence only.
- **#1987 / PR #1998**: body-reference validation on fuzzy PR search (see Freshness Check).
- **#1915 / PR #1946**: slug-wins branch ownership — established `session/{slug}` as the
  canonical shape and one-branch-per-issue as the structural duplicate-PR guard.

## Research

No relevant external findings — purely internal substrate work; proceeding with codebase
context. (WebSearch skipped per skill rule: no external libraries or APIs involved.)

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1956 (#1954 lock) | Advisory issue lock keyed by per-process uuid | Keyed ownership to OS process identity; `/do-sdlc`'s subprocess fan-out made every `sdlc-tool` call a different "owner" |
| PR #1972 (#1971 seam) | `SDLC_HOLDER_TOKEN` env override | Patched the symptom: ownership became a prompt-maintained env choreography instead of a modeled fact |
| PR #1990 (#1944 gate) | DOCS check in the `/do-merge` skill body | Enforced in the skill, not the hook — a raw `gh pr merge` with a stale auth file still bypasses it |

**Root cause pattern:** invariants attached to the wrong identity (process ≠ run) or the
wrong layer (skill prose ≠ enforcement choke point), each patch layering on the previous.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** `AgentSession` gains `active_run_id`; `_process_holder_token()`
  and `SDLC_HOLDER_TOKEN` are deleted; the merge-guard hook's contract changes from
  "auth file exists" to "predicate passes".
- **Coupling:** decreases — skill bodies stop carrying ownership state; router terminal
  rows stop duplicating the merge predicate.
- **Data ownership:** run identity lives on the AgentSession record (single owner:
  `ensure_session`); PR number lives on the session record (single writer: `/do-build`).
- **Reversibility:** moderate — the lock payload change ships with a fallback window
  (accept either token form for one release); hook change is a single-file revert.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (open-questions resolution, hook-posture decision)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. (Redis and `gh` availability are
ambient repo requirements, already covered by `python -m tools.doctor`.)

## Solution

### Key Elements

- **`run_id` (T2.1)**: one logical pipeline-run identity, minted at `ensure_session`
  create-or-adopt, stored as `AgentSession.active_run_id`. The issue-lock payload and
  dispatch records carry it; every `sdlc-tool` subprocess resolves it from the session
  record it already fetches. `SDLC_HOLDER_TOKEN`, the `data/.sdlc_run/` file, and the
  skill-body re-export blocks are deleted.
- **Merge predicate in the hook (T2.2)**: `validate_merge_guard.py` evaluates the real
  terminal predicate (PR OPEN/MERGEABLE/CLEAN + CI green + APPROVED review verdict + DOCS
  marker completed-or-legitimately-skipped per #1799 + issue link) instead of checking
  `data/merge_authorized_{PR}` existence. Router row 10b's weakening is deleted; rows
  9/10/G6 stay as *scheduling* (when to dispatch `/do-merge`), enforcement lives in the
  hook alone.
- **PR resolution & dead signal (T1.7, rescoped post-#1998)**: `/do-build` records
  `pr_number` on the session record at PR creation (single writer); the ladder becomes
  session field → validated-search (#1998) → head fallback, documented as read-only
  recovery. `branch_exists` checks the canonical `session/{slug}` (slug already resolvable
  in `next-skill` context) or is deleted along with row 5's dependence if slug resolution
  isn't available at that call site.

### Flow

`/do-sdlc N` → `ensure_session` mints/adopts `run_id` on the session record →
every `sdlc-tool` call resolves ownership from the record (no env) → stages dispatch →
`/do-build` writes `pr_number` at PR creation → router schedules `/do-merge` →
merge-guard hook evaluates the terminal predicate live → merge or a named, actionable
refusal.

### Technical Approach

- `run_id` is a uuid hex stored in a new `AgentSession.active_run_id` field; the issue-lock
  value becomes `{run_id}` (or a JSON payload carrying it). `touch_issue_lock()` compares
  the caller's resolved `run_id` against the stored one. The worker's in-process path and
  the CLI subprocess path converge on the same resolution (read the session record).
- Adoption is lazy: `ensure_session` mints `run_id` when absent or when adopting a
  terminal/stale session; no bulk migration of historical rows. A one-line idempotent
  migration registers the field (Popoto rule, `scripts/update/migrations.py`).
- Stale-owner takeover keeps the existing lock TTL semantics: an expired lock is claimable
  regardless of `active_run_id`; a live lock with a foreign `run_id` blocks with
  `ISSUE_LOCKED` + the owning `run_id`/session for inspection.
- The hook predicate reuses the exact checks `/do-merge` Step 2/2b already runs
  (`gh pr view --json mergeable,mergeStateStatus,statusCheckRollup,reviewDecision` +
  `sdlc-tool verdict get --stage REVIEW` + DOCS stage-query) — extracted into one shared
  helper (`tools/_sdlc_utils.py` or a new `tools/merge_predicate.py`) so skill and hook
  cannot drift. Redis/substrate errors in the hook follow the posture decided in Open
  Question 2 (proposed: fail-closed for predicate-evaluation errors, with the auth-file
  path retained as a narrow, logged manual override for genuine substrate-down emergencies).
- Lock fail-open on *Redis* errors is preserved (advisory lock, per #1954 design), but each
  fail-open site logs the swallowed error class explicitly (cf. #1868's not-found vs
  transient distinction).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `touch_issue_lock` / run-id resolution: Redis-error path logs a warning naming the
      error class and fails open (test asserts the log record + open behavior)
- [ ] Merge-guard hook: predicate-evaluation exception → hook blocks (fail-closed) with an
      actionable message naming the failed check (test asserts block + message)

### Empty/Invalid Input Handling
- [ ] `run_id` absent on a legacy session record → lazily minted, never crashes
- [ ] Hook invoked on a repo/PR with no sdlc substrate (foreign repo) → documented generic
      behavior per Open Question 1, never an unhandled traceback

### Error State Rendering
- [ ] `ISSUE_LOCKED` refusal includes the owning run_id + session id (inspectable via
      `valor-session inspect`)
- [ ] Hook refusal output names the exact failed predicate leg (e.g. "DOCS stage
      in_progress"), not a generic denial

## Test Impact

- [ ] `tests/unit/test_session_lifecycle.py` — UPDATE: holder-token tests (`SDLC_HOLDER_TOKEN`
      env cases from #1971) become run-id resolution tests; keep the two-subprocess
      same-owner scenario, drop the env-var mechanism
- [ ] `tests/unit/test_sdlc_session_ensure.py` — UPDATE: `ensure_session` mints/adopts
      `run_id`
- [ ] `tests/unit/test_sdlc_dispatch.py`, `test_sdlc_next_skill.py` — UPDATE: dispatch
      records carry `run_id`; `branch_exists` canonical-shape fix (or removal)
- [ ] `tests/unit/test_sdlc_router.py`, `test_sdlc_router_decision.py`,
      `test_sdlc_skill_md_parity.py` — UPDATE: row 10b deletion
- [ ] `tests/unit/test_do_merge_docs_gate.py` — UPDATE: gate logic moves into the shared
      predicate helper; hook tests added alongside
- [ ] Merge-guard hook tests (location per existing hook-test convention under
      `tests/unit/`) — REPLACE: existence-check tests become predicate tests

## Rabbit Holes

- Distributed-lock general correctness (fencing tokens, clock skew): keep the advisory,
  TTL-based semantics; this plan changes *what identity* the lock carries, not its
  consistency model.
- Rewriting router scheduling rows 9/10/G6: they stay as-is; only the *enforcement*
  duplication and row 10b's weakening are touched.
- Reordering the whole PR-resolution ladder beyond single-writer + documented fallbacks
  (#1998 already made search safe; deleting it entirely buys little and risks losing
  out-of-band PR recovery).
- Foreign-repo substrate design: the hook's generic path gets a *documented posture*, not a
  new substrate.

## Risks

### Risk 1: Hook fail-closed strands emergency merges when the substrate is down
**Impact:** an operator cannot merge a genuinely-ready PR while Redis/stage markers are
unavailable.
**Mitigation:** retain the auth file as a narrow manual override that requires an explicit
reason string in the file (`data/merge_authorized_{PR}` containing `override: <reason>`),
logged loudly; empty/legacy-format files no longer pass.

### Risk 2: Mixed-version window during rollout (old skills exporting SDLC_HOLDER_TOKEN, new lock ignoring it)
**Impact:** in-flight supervision runs at deploy time could see ISSUE_LOCKED.
**Mitigation:** one-release compatibility: `_resolve_run_identity()` honors a present
`SDLC_HOLDER_TOKEN` as a legacy alias for lock comparison, warns, and the alias is removed
in the next release. Lock TTL (300s) bounds any residual wedge.

### Risk 3: Hook predicate drifts from `/do-merge` skill checks
**Impact:** the #1944 class recurs with the roles reversed (hook stricter/looser than gate).
**Mitigation:** both consume one shared helper; a parity test imports the helper and
asserts the skill body references it (same pattern as `test_sdlc_skill_md_parity.py`).

## Race Conditions

### Race 1: Two `ensure_session` calls mint run_ids concurrently
**Location:** `tools/sdlc_session_ensure.py` (create-or-adopt path)
**Trigger:** two supervisors start on the same issue within one lock-TTL window
**Data prerequisite:** the issue lock must be acquired before `active_run_id` is trusted
**State prerequisite:** lock acquisition (Redis `SET NX EX`) precedes run-id write
**Mitigation:** mint the run_id *into* the `SET NX` lock value atomically; the loser reads
the winner's run_id from the lock and reports `ISSUE_LOCKED` — the existing
`claim_pending_run` SETNX pattern is the precedent.

### Race 2: PR created but process dies before `pr_number` saved
**Location:** `/do-build` PR-creation step
**Trigger:** crash between `gh pr create` and the session-record save
**Data prerequisite:** none (recovery path)
**State prerequisite:** branch `session/{slug}` exists with the PR as head
**Mitigation:** the head-fallback rung in `_lookup_pr_number` recovers exactly this case —
which is why the ladder keeps its read-only recovery rungs (single *writer*, not single
*reader*).

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2004] The signals/gates hygiene sweep (Workstream B) — disjoint file
  surface, concurrent pipeline.
- [SEPARATE-SLUG #1760] Verdict staleness / artifact-hash routing (program item T3.1) —
  sequenced later in the program plan; this plan does not touch verdict freshness logic.
- [SEPARATE-SLUG #1629] Durable stage-state artifact / event-sourced markers (T3.4) —
  explicitly deferred; `run_id` lands on the existing record shape.
- [SEPARATE-SLUG #1927] AgentSession field renames beyond adding `active_run_id` — the
  schema diet owns naming; this plan adds one field with a precision name and otherwise
  leaves the schema alone.

## Update System

- `scripts/update/migrations.py`: one idempotent migration registering the
  `active_run_id` field (Popoto rule). No raw Redis ops.
- Skill bodies (`.claude/skills/sdlc/SKILL.md`, `.claude/skills-global/do-sdlc/SKILL.md`,
  `.claude/skills-global/do-merge/SKILL.md`) change in the same PR; `skills-global` copies
  propagate via the existing `/update` hardlink sync — no new wiring, no `RENAMED_REMOVALS`.
- The mixed-version compatibility alias (Risk 2) is called out in the PR description for
  deploy sequencing. No other update-system changes required.

## Agent Integration

No new agent surface required — all changes live behind existing entry points (`sdlc-tool`
CLI, the merge-guard hook, skill bodies) that agents already invoke. Integration tests
verify `sdlc-tool session-ensure` → `next-skill` → `dispatch record` succeed as separate
subprocesses with no `SDLC_HOLDER_TOKEN` in the environment (the #1971 scenario, inverted).

## Documentation

- [ ] Update `docs/features/sdlc-issue-ownership-lock.md` — run_id ownership model replaces
      holder-token/env-seam sections
- [ ] Update `docs/sdlc/do-merge.md` + `docs/features/enforce-review-docs-stages.md` — hook
      predicate enforcement, manual-override format
- [ ] Update `docs/features/sdlc-pipeline-state.md` — pr_number single-writer convention
- [ ] Add entries/corrections to `docs/features/README.md` index as needed

## Success Criteria

- [ ] Two separate `sdlc-tool` subprocesses in one supervision run acquire+peek the issue
      lock as the same owner with no `SDLC_HOLDER_TOKEN` in the environment (integration test)
- [ ] `grep -r "SDLC_HOLDER_TOKEN" --include="*.py" --include="*.md" .claude/ tools/ models/`
      returns only historical plan docs (grep-clean in code and live skill bodies)
- [ ] The merge-guard hook blocks `gh pr merge` when DOCS is `in_progress` or the REVIEW
      verdict is missing, even with a legacy `data/merge_authorized_{PR}` file present
- [ ] The hook allows a merge when the predicate passes with no auth file at all
- [ ] Router row 10b deleted; skill-md parity test updated and green
- [ ] `branch_exists` reflects the canonical `session/{slug}` branch or is removed with row
      5's dependence
- [ ] `/do-build` writes `pr_number` to the session record at PR creation (asserted by a
      build-path test)
- [ ] Existing #1971 and #1944 regression scenarios still pass, rewritten against the new
      mechanism
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (substrate)**
  - Name: substrate-builder
  - Role: run_id model + lock + sdlc-tool resolution + skill-body edits
  - Agent Type: builder
  - Resume: true
- **Builder (merge-gate)**
  - Name: gate-builder
  - Role: shared predicate helper + hook rewrite + router row 10b removal
  - Agent Type: builder
  - Resume: true
- **Validator (workstream)**
  - Name: substrate-validator
  - Role: verify success criteria, run integration scenarios
  - Agent Type: validator
  - Resume: true
- **Documentarian**
  - Name: substrate-docs
  - Role: feature-doc updates per Documentation section
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Run identity model + lock payload
- **Task ID**: build-run-id
- **Depends On**: none
- **Validates**: tests/unit/test_session_lifecycle.py, tests/unit/test_sdlc_session_ensure.py
- **Assigned To**: substrate-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `active_run_id` field + migration; mint into `SET NX` lock value atomically
- Rewrite `_process_holder_token` → `_resolve_run_identity` (session-record read, legacy
  env alias with warning)
- Update `ensure_session` create-or-adopt; `touch_issue_lock` comparison; dispatch records
  carry run_id

### 2. Skill-body ownership cleanup
- **Task ID**: build-skill-bodies
- **Depends On**: build-run-id
- **Validates**: tests/unit/test_sdlc_skill_md_parity.py
- **Assigned To**: substrate-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `SDLC_HOLDER_TOKEN` mint/re-export blocks and `data/.sdlc_run/` convention from
  `/do-sdlc` and `/sdlc` bodies

### 3. Shared merge predicate + hook enforcement
- **Task ID**: build-merge-gate
- **Depends On**: none
- **Validates**: tests/unit/test_do_merge_docs_gate.py, hook tests
- **Assigned To**: gate-builder
- **Agent Type**: builder
- **Parallel**: true
- Extract the Step 2/2b checks into one shared helper; hook evaluates it fail-closed
- Auth file becomes explicit-override-with-reason only; delete router row 10b
- Update `/do-merge` skill body to consume the helper

### 4. PR resolution single-writer + branch_exists fix
- **Task ID**: build-pr-resolution
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_next_skill.py, build-path pr_number test
- **Assigned To**: gate-builder
- **Agent Type**: builder
- **Parallel**: true
- `/do-build` records pr_number at creation; ladder documented read-only recovery
- Fix `branch_exists` to `session/{slug}` (or remove with row 5 dependence)

### 5. Validation
- **Task ID**: validate-workstream
- **Depends On**: build-run-id, build-skill-bodies, build-merge-gate, build-pr-resolution
- **Assigned To**: substrate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification rows; confirm success criteria; report pass/fail

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-workstream
- **Assigned To**: substrate-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Execute the Documentation section checklist

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: substrate-validator
- **Agent Type**: validator
- **Parallel**: false
- Full suite + criteria re-check + final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No env-token residue in code/skills | `grep -rn "SDLC_HOLDER_TOKEN" tools/ models/ agent/ .claude/skills/ .claude/skills-global/ \| grep -v legacy_alias` | match count == 0 |
| No run-file convention residue | `grep -rn "\.sdlc_run" tools/ models/ agent/ .claude/skills/ .claude/skills-global/` | match count == 0 |
| Dead branch shape gone | `grep -c "session/sdlc-" tools/sdlc_next_skill.py` | match count == 0 |
| Row 10b deleted | `grep -c "_rule_stage_states_unavailable_pr_open\|row 10b" agent/sdlc_router.py` | match count == 0 |
| Two-subprocess ownership | `pytest tests/unit/test_session_lifecycle.py -k run_identity -q` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Hook posture without substrate (foreign repos / degraded Redis):** proposed
   fail-closed on predicate-evaluation errors with an explicit-override auth file
   (`override: <reason>`). Alternative: generic repos keep today's existence-check.
   Which posture for the generic path?
2. **Does the validated search rung stay?** #1998 made `--search` safe (body-reference
   validated). Proposed: keep it as read-only recovery below the session field; delete
   nothing further. Confirm, or direct full deletion (head-fallback only)?
3. **Migration window:** proposed lazy adoption (mint on next `ensure_session`) + one
   idempotent field migration + one-release `SDLC_HOLDER_TOKEN` legacy alias. Acceptable,
   or require a hard cutover?
