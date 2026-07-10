---
status: Ready
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-07-10
tracking: https://github.com/tomcounsell/ai/issues/2003
last_comment_id: 4932944448
revision_applied: true
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

**Issue comment 4932944448 (2026-07-10T07:18Z):** live confirmation of defect 3 — the
sdlc-local-1834 dev agent satisfied `validate_merge_guard.py` by manually touching
`data/merge_authorized_2005` (no `/do-merge` invocation) and merged PR #2005. Confirms
the exact hole this plan's merge-predicate hook closes; no scope change.

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
- **Interface changes:** `AgentSession` gains `active_run_id` and `pr_number` (the latter
  does not exist today — only `pr_url`); `_process_holder_token()` and `SDLC_HOLDER_TOKEN`
  are deleted; state-mutating `sdlc-tool` subcommands gain a required `--run-id`; the
  merge-guard hook's contract changes from "auth file exists" to "predicate passes".
- **Coupling:** decreases at the enforcement layer — router terminal rows stop duplicating
  the merge predicate. Stated precisely (cycle-2 CONCERN 4): `--run-id` is still
  prompt-carried within a supervision run, but with fail-loud semantics (a missing flag is
  a named error, never a silent new identity) and a bounded, documented recovery path
  (re-`ensure_session`, ≤300s TTL wait) — versus the env seam's silent split-identity and
  the run file's unbounded staleness.
- **Data ownership:** run identity is minted by the lock contest in `ensure_session`
  (single minting site) and mirrored to the record for inspection; PR number lives on the
  session record (single writer: `/do-build`).
- **Reversibility:** moderate — hard cutover on the lock payload (deploy runbook pairs the
  merge with a worker restart); hook change is a single-file revert.

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

- **`run_id` (T2.1)**: one logical pipeline-run identity, minted **only** at
  `ensure_session` by contesting the issue lock (fresh candidate per top-level call,
  `SET NX` carries it), stored as `AgentSession.active_run_id` for inspection. Mid-run
  `sdlc-tool` subprocesses receive the run_id **explicitly** (`--run-id` flag, emitted in
  `session-ensure`'s JSON output) — never by ambient adoption off the shared record.
  State-mutating calls without `--run-id` fail loudly instead of minting or adopting.
  `SDLC_HOLDER_TOKEN`, the `data/.sdlc_run/` file, and the skill-body re-export blocks are
  deleted; the worker's in-process path passes the same run_id programmatically.
- **Merge predicate in the hook (T2.2)**: `validate_merge_guard.py` evaluates the real
  terminal predicate (PR OPEN/MERGEABLE/CLEAN + CI green + **SHA-fresh** APPROVED review
  verdict + DOCS marker completed-or-legitimately-skipped per #1799 + issue link) instead
  of checking `data/merge_authorized_{PR}` existence. Freshness means the verdict is
  compared against the PR's latest commit (head-SHA trailer or commit date) — the
  Structured Review Comment Check (`docs/sdlc/do-merge.md:231-283`) is extracted into the
  shared helper alongside Step 2/2b, not left behind. Router row 10b's weakening is
  deleted; rows 9/10/G6 stay as *scheduling* (when to dispatch `/do-merge`), enforcement
  lives in the hook alone.
- **PR resolution & dead signal (T1.7, rescoped post-#1998)**: add
  `pr_number = IntField(null=True)` to `AgentSession` (the field does not exist today —
  only `pr_url`; the "primary rung" at `tools/sdlc_stage_query.py:425` is dead code);
  `/do-build` writes it at PR creation (single writer); the ladder becomes session field →
  validated-search (#1998) → head fallback, documented as read-only recovery.
  `branch_exists` checks the canonical `session/{slug}` (slug already resolvable in
  `next-skill` context) or is deleted along with row 5's dependence if slug resolution
  isn't available at that call site.

### Flow

`/do-sdlc N` → `ensure_session` contests the lock with a fresh run_id and emits it →
every state-mutating `sdlc-tool` call carries `--run-id` explicitly (missing = loud error;
foreign = ISSUE_LOCKED) → stages dispatch → `/do-build` writes `pr_number` at PR creation →
router schedules `/do-merge` → merge-guard hook evaluates the terminal predicate live
(including verdict-vs-head-SHA freshness) → merge or a named, actionable refusal.

### Technical Approach

- `run_id` is a uuid hex. **Minting is exclusive to `ensure_session` and is decided by the
  lock, never by session status**: each top-level call generates a fresh candidate and
  attempts `SET NX` with it. Acquired → this run owns the issue; `active_run_id` is saved
  to the session record (inspection surface). Not acquired → the lock's live holder is a
  foreign run → `ISSUE_LOCKED` with the owning run_id/session, regardless of what
  `active_run_id` the record carries. There is no adopt-from-record branch (the critique's
  BLOCKER 1: status-keyed adoption lets a second supervisor impersonate the incumbent).
- Mid-run identity is explicit, not ambient: `session-ensure` emits `run_id` in its JSON
  output; the skill passes `--run-id` to every state-mutating `sdlc-tool` call; the worker
  passes it in-process. A state-mutating call without `--run-id` exits non-zero with a
  named error (structural fail-loud, vs the env seam's silent new-token minting).
  `touch_issue_lock()` compares the supplied run_id against the lock holder on every
  mutation (fresh live check, per the #1954 design preference).
- **All `touch_issue_lock()` call sites flip together — including the two non-CLI renewal
  paths** (cycle-2 BLOCKER): `tools/_sdlc_utils.py:399` (`renew_issue_lock_for_session`,
  wired into `sdlc_stage_marker.write_marker()`) and `agent/session_executor.py:232`
  (`_tick_issue_lock_renewal`, the worker's 60s heartbeat renewal) currently pass
  `session_id` positionally and rely on the process-global token for comparison. Under the
  run_id contract each must source the identity from `agent_session.active_run_id` —
  renewal is reading back the identity this same process's `ensure_session` established,
  not adopting a foreign one, so it does not violate the no-adopt rule. Each call site
  gets a regression test asserting a lock acquired with run_id X is still renewable past
  the 300s TTL by the same session object; `_tick_issue_lock_renewal` additionally logs a
  warning when renewal returns not-owner instead of staying fire-and-forget.
- **run_id loss recovery (supervisor side):** if a local supervisor loses its run_id
  (context compaction, crash of the driving session), there is deliberately no
  adopt-from-record shortcut. The documented recovery is: re-run `session-ensure`; while
  the old lock is live it returns `ISSUE_LOCKED` (bounded by the 300s TTL since nothing
  renews the orphaned run's lock), after which a fresh contest mints a new run_id. Bounded
  and loud, versus the env seam's silent split-identity behavior.
- Lock/record consistency (cycle-1 CONCERN 1, refined by cycle-2 CONCERN 2): after
  `session.save()` in the acquire path, `ensure_session` re-reads the record and asserts
  `active_run_id` matches the lock payload; on mismatch or save failure it releases the
  lock via **compare-and-delete** (delete only if the lock value still equals our run_id —
  the standard Lua release pattern), never a raw `DEL`, so a delayed cleanup can never
  delete a successor's freshly acquired lock. Scope claim stated precisely: the readback
  covers the save-failure branch; a true process death inside the window is bounded by the
  300s TTL and surfaced by the peek path flagging a lock whose run_id matches no live
  session (`orphaned_lock: true`) instead of reporting a healthy foreign owner.
- Stale-owner takeover keeps the existing lock TTL semantics: an expired lock is claimable
  by the next fresh candidate; no takeover reads `active_run_id` as authority.
- One idempotent migration registers both new fields (`active_run_id`, `pr_number`) —
  Popoto rule, `scripts/update/migrations.py`; fix the stale "primary rung" comment at
  `tools/sdlc_stage_query.py:422-424`.
- The hook predicate extracts **all three** deterministic check groups into one shared
  helper (`tools/merge_predicate.py`): (a) Step 2 PR state
  (`gh pr view --json mergeable,mergeStateStatus,statusCheckRollup,reviewDecision`),
  (b) Step 2b DOCS stage-query, and (c) the Structured Review Comment Check's
  **verdict freshness**: fetch the PR's latest commit
  (`gh api repos/{repo}/pulls/{pr}/commits --jq '.[-1]'`) and fail when the APPROVED
  verdict predates it (prefer the `REVIEW_CONTEXT head_sha=` trailer comparison; commit
  date as fallback). A bare `"APPROVED" in verdict_text` check is explicitly insufficient
  (critique BLOCKER 2). Skill and hook both consume the helper so they cannot drift.
- Hook posture with an explicit discriminator (cycle-2 CONCERN 3 — "foreign-repo skip" and
  "fail-closed on errors" are different observable conditions and must be told apart
  BEFORE evaluating): (1) **substrate absent** — no `docs/sdlc/do-merge.md` addendum in
  the target repo, or `sdlc-tool` unresolvable — is detected up front as a repo property;
  groups b/c skip with a logged notice, group a still enforces. (2) **substrate present
  but a predicate call raises / exits non-zero / returns malformed output** — fail closed.
  The detection is ordered (probe substrate first, then evaluate), so an evaluation error
  in a substrate-present repo can never be misread as "foreign repo". The auth file
  survives only as an explicit break-glass override — must contain `override: <reason>`;
  empty/legacy files block. Every accepted override emits
  `record_metric("merge_guard.override_used", {"pr_number", "reason"})` so uses surface on
  the dashboard, not just in grep-able logs. Both branches are test-covered (see Failure
  Path Test Strategy).
- Lock fail-open on *Redis* errors is preserved (advisory lock, per #1954 design), but each
  fail-open site logs the swallowed error class explicitly (cf. #1868's not-found vs
  transient distinction).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `touch_issue_lock` / run-id checks: Redis-error path logs a warning naming the
      error class and fails open (test asserts the log record + open behavior)
- [ ] Merge-guard hook: predicate-evaluation exception → hook blocks (fail-closed) with an
      actionable message naming the failed check (test asserts block + message)
- [ ] `ensure_session` crash window: save failure (or readback mismatch) after lock
      acquire → lock released, error surfaced; next caller acquires immediately, no 300s
      wedge (test simulates save raise)

### Empty/Invalid Input Handling
- [ ] State-mutating `sdlc-tool` call with no `--run-id` → non-zero exit with a named
      error, no mint, no adopt (test per mutating subcommand)
- [ ] `run_id` absent on a legacy session record → next `ensure_session` contests the lock
      normally; reads never crash on the missing field
- [ ] Auth file empty or legacy-format (no `override:` line) → hook blocks (test)
- [ ] Auth file with `override: <reason>` → hook allows, logs at warning, and emits
      `merge_guard.override_used` metric (test asserts all three)
- [ ] Hook invoked on a repo/PR with no sdlc substrate (foreign repo) → PR-state checks
      (group a) still enforced; substrate checks (groups b/c) skip with a logged notice —
      documented generic posture, never an unhandled traceback

### Error State Rendering
- [ ] `ISSUE_LOCKED` refusal includes the owning run_id + session id (inspectable via
      `valor-session inspect`); orphaned locks flagged as `orphaned_lock: true`
- [ ] Hook refusal output names the exact failed predicate leg (e.g. "DOCS stage
      in_progress", "REVIEW verdict predates head commit"), not a generic denial

## Test Impact

- [ ] `tests/unit/test_session_lifecycle.py` — UPDATE: holder-token tests (`SDLC_HOLDER_TOKEN`
      env cases from #1971) become run-id resolution tests; keep the two-subprocess
      same-owner scenario, drop the env-var mechanism
- [ ] `tests/unit/test_sdlc_session_ensure.py` — UPDATE: `ensure_session` mints/adopts
      `run_id`
- [ ] `tests/unit/test_sdlc_dispatch.py`, `test_sdlc_next_skill.py` — UPDATE: dispatch
      records carry `run_id`; `branch_exists` canonical-shape fix (or removal)
- [ ] `tests/unit/test_sdlc_utils.py` — UPDATE: `renew_issue_lock_for_session` sources
      `active_run_id`; new renewal-past-TTL regression test
- [ ] `tests/unit/test_session_executor_tick_backstop.py` (or sibling) — UPDATE:
      `_tick_issue_lock_renewal` sources `active_run_id`, warns on not-owner; renewal
      regression test
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
**Mitigation:** break-glass override: `data/merge_authorized_{PR}` containing
`override: <reason>`; empty/legacy-format files block. Every accepted override is
test-covered, logged at warning, and emits a `merge_guard.override_used` metric so uses
are dashboard-visible, not grep-only.

### Risk 2: Mixed-version window during rollout (old skill bodies still exporting SDLC_HOLDER_TOKEN)
**Impact:** an in-flight supervision run at deploy time could see ISSUE_LOCKED.
**Mitigation:** hard cutover, no legacy alias (cycle-1 CONCERN 3 — an alias with a
permanent Verification exclusion is an unremovable bridge). Deploy runbook sequences
"merge → `./scripts/valor-service.sh worker-restart`"; the worker is a single process, so
no session survives the restart boundary to present a stale token. An in-flight local
supervision run fails loudly at its next state-mutating call (missing/foreign run
identity) and restarts its run via `session-ensure` — a named error and a bounded
re-entry, not a silent continue; the 300s lock TTL bounds any residual overlap.

### Risk 3: Hook predicate drifts from `/do-merge` skill checks
**Impact:** the #1944 class recurs with the roles reversed (hook stricter/looser than gate).
**Mitigation:** both consume one shared helper; a parity test imports the helper and
asserts the skill body references it (same pattern as `test_sdlc_skill_md_parity.py`).

## Race Conditions

### Race 1: Two `ensure_session` calls contest the same issue concurrently
**Location:** `tools/sdlc_session_ensure.py` (lock-contest path)
**Trigger:** two supervisors start on the same issue within one lock-TTL window — at
cold start OR while the incumbent is live (the second case is the critique's BLOCKER 1)
**Data prerequisite:** the issue lock must be acquired before any run identity is trusted
**State prerequisite:** lock acquisition (Redis `SET NX EX`) precedes the run-id record write
**Mitigation:** every top-level call mints a fresh candidate *into* the `SET NX` value
atomically; the loser reads the winner's run_id from the lock and reports `ISSUE_LOCKED`.
There is no adopt-from-record path, so a live incumbent can never be impersonated — the
existing `claim_pending_run` SETNX pattern is the precedent.

### Race 3: Crash between lock acquire and `active_run_id` save
**Location:** `tools/sdlc_session_ensure.py` (acquire → save window)
**Trigger:** process death after `SET NX` succeeds, before `session.save()` lands
**Data prerequisite:** lock payload run_id must have a matching session record to be
considered healthy
**State prerequisite:** none (recovery path)
**Mitigation:** post-save readback assertion releases the lock (`DEL`) on mismatch/failure;
the peek path reports `orphaned_lock: true` for a lock run_id with no matching live
session, so callers and operators distinguish "held by a live run" from "held by a ghost".

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
- [SEPARATE-SLUG #1927] AgentSession field renames beyond adding `active_run_id` and
  `pr_number` — the schema diet owns naming; this plan adds two fields with precision
  names and otherwise leaves the schema alone.

## Update System

- `scripts/update/migrations.py`: one idempotent migration registering both new fields
  (`active_run_id`, `pr_number`) — Popoto rule, no raw Redis ops.
- Skill bodies (`.claude/skills/sdlc/SKILL.md`, `.claude/skills-global/do-sdlc/SKILL.md`,
  `.claude/skills-global/do-merge/SKILL.md`) change in the same PR; `skills-global` copies
  propagate via the existing `/update` hardlink sync — no new wiring, no `RENAMED_REMOVALS`.
- Hard-cutover deploy sequencing (Risk 2): the PR description instructs "merge →
  `./scripts/valor-service.sh worker-restart`" on the worker machine. No compatibility
  alias ships. No other update-system changes required.

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
- [ ] The merge-guard hook blocks `gh pr merge` when DOCS is `in_progress`, the REVIEW
      verdict is missing, **or the APPROVED verdict predates the PR's latest commit** —
      even with a legacy `data/merge_authorized_{PR}` file present
- [ ] The hook allows a merge when the predicate passes with no auth file at all
- [ ] An `override: <reason>` auth file allows the merge AND emits the
      `merge_guard.override_used` metric (asserted); an empty/legacy file blocks
- [ ] A state-mutating `sdlc-tool` call without `--run-id` exits non-zero with a named
      error (no silent mint/adopt)
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
- Add `active_run_id` + `pr_number` fields + one idempotent migration; mint fresh
  candidate into the `SET NX` lock value atomically (no adopt-from-record branch)
- Delete `_process_holder_token` and the env seam entirely (hard cutover); add `--run-id`
  to state-mutating `sdlc-tool` subcommands (missing = named non-zero error);
  `session-ensure` emits run_id in JSON output
- Post-save readback assertion + compare-and-delete lock release on mismatch; peek path
  reports `orphaned_lock`; `touch_issue_lock` comparison; dispatch records carry run_id
- **Flip the two non-CLI renewal call sites** to source identity from
  `agent_session.active_run_id`: `tools/_sdlc_utils.py:399` (`renew_issue_lock_for_session`)
  and `agent/session_executor.py:232` (`_tick_issue_lock_renewal`, add not-owner warning
  log); regression test per site: lock acquired with run_id X renewable past 300s TTL by
  the same session object

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
- Extract Step 2 + Step 2b + the Structured Review Comment Check's verdict-freshness
  (verdict vs PR head SHA/latest-commit date) into `tools/merge_predicate.py`; hook
  evaluates it fail-closed
- Auth file becomes explicit-override-with-reason only (empty/legacy blocks); override
  emits `merge_guard.override_used` metric; both branches test-covered; delete router
  row 10b
- Update `/do-merge` skill body to consume the helper

### 4. PR resolution single-writer + branch_exists fix
- **Task ID**: build-pr-resolution
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_next_skill.py, build-path pr_number test
- **Assigned To**: gate-builder
- **Agent Type**: builder
- **Parallel**: true
- `/do-build` writes the new `pr_number` field at creation; ladder documented read-only
  recovery; fix the stale "primary rung" comment at `tools/sdlc_stage_query.py:422-424`
- Fix `branch_exists` to `session/{slug}` (or remove with row 5 dependence)

### 5. Validation
- **Task ID**: validate-workstream
- **Depends On**: build-run-id, build-skill-bodies, build-merge-gate, build-pr-resolution
- **Validates**: every row of the ## Verification table (run each Command, compare Expected)
- **Assigned To**: substrate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification rows; confirm success criteria; report pass/fail

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-workstream
- **Validates**: `python -m tools.doc_impact_finder` clean on the diff (or manual doc-index
  check); every ## Documentation checkbox checked
- **Assigned To**: substrate-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Execute the Documentation section checklist

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Validates**: `scripts/pytest-clean.sh tests/ -q` (full suite) + ## Verification table
  re-run + all ## Success Criteria checked
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
| No env-token residue in code/skills | `grep -rn "SDLC_HOLDER_TOKEN" tools/ models/ agent/ .claude/skills/ .claude/skills-global/ \| wc -l` | match count == 0 |
| No run-file convention residue | `grep -rn "\.sdlc_run" tools/ models/ agent/ .claude/skills/ .claude/skills-global/ \| wc -l` | match count == 0 |
| Dead branch shape gone | `grep -n "session/sdlc-" tools/sdlc_next_skill.py \| wc -l` | match count == 0 |
| Row 10b deleted | `grep -n "_rule_stage_states_unavailable_pr_open\|row 10b" agent/sdlc_router.py \| wc -l` | match count == 0 |
| Two-subprocess ownership | `pytest tests/unit/test_session_lifecycle.py -k run_identity -q` | exit code 0 |
| Renewal call sites flipped | `pytest tests/unit/ -k "lock_renewal" -q` | exit code 0 |
| No session_id passed to lock renewal | `grep -n "touch_issue_lock(issue_number, session_id" tools/ agent/ -r \| wc -l` | match count == 0 |
| Stale approval blocked | `pytest tests/unit/test_do_merge_docs_gate.py -k stale_verdict -q` | exit code 0 |
| Override path covered | `pytest tests/unit/ -k merge_guard_override -q` | exit code 0 |

## Critique Results

War room run 2026-07-10 (FULL depth — doctrine paths: `.claude/hooks/`, `agent/sdlc_router.py`, `.claude/skills/`, `.claude/skills-global/`). Critics: Risk & Robustness, Scope & Value, History & Consistency (roster 3/3 complete). Verdict: **NEEDS REVISION** (2 blockers).

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency | Lazy run_id adoption reads `active_run_id` off the shared, deterministically-keyed session record: a second live supervisor calling `ensure_session` while the incumbent's session is non-terminal silently adopts the incumbent's run_id and is renewed by `touch_issue_lock` as if it were the owner — repeating the session_id-identity mistake (`models/session_lifecycle.py:780-783`) one layer up. Race 1's mitigation only covers cold-start contention. | REVISED: adopt-from-record branch deleted. Minting is lock-decided only (fresh candidate per top-level call, `SET NX`); mid-run identity travels explicitly via `--run-id` emitted by `session-ensure`; missing `--run-id` on a mutating call is a named non-zero error. See Key Elements, Technical Approach, Race 1. | Key the mint-vs-adopt branch in `ensure_session` off the issue lock's live holder comparison (`touch_issue_lock(..., peek=True)`), never off session.status ∈ {absent, terminal, stale}. Generate a fresh candidate run_id per top-level call; reuse the stored `active_run_id` only when the caller can prove it is the same top-level invocation (parent-supplied run_id down the subprocess context), never merely because the record already has one. |
| BLOCKER | Risk & Robustness | The shared merge predicate is specced as "the exact checks `/do-merge` Step 2/2b already runs", which drops the issue's "**fresh** APPROVED review verdict" requirement: Step 2/2b has no freshness check — the stale-approval protection (#1932/#1941 class) lives in the un-extracted "Structured Review Comment Check" (`docs/sdlc/do-merge.md:231-283`). Built as written, the hardened hook reopens the stale-approval bypass. | REVISED: `tools/merge_predicate.py` extracts all three check groups including the Structured Review Comment Check's verdict-freshness (verdict vs head-SHA trailer / latest-commit date). New Success Criterion + Verification row "Stale approval blocked". | In `tools/merge_predicate.py`, alongside `sdlc-tool verdict get --stage REVIEW`, fetch the PR's latest commit date (`gh api repos/{repo}/pulls/{pr}/commits --jq '.[-1].commit.committer.date'`) and fail the predicate when the APPROVED verdict predates the latest commit (or compare via the `REVIEW_CONTEXT head_sha=` trailer), not a bare `"APPROVED" in verdict_text` check. |
| CONCERN | Risk & Robustness | New crash window the current design lacks: process dies between the lock `SET NX` (carrying run_id) and the separate `session.save()` of `active_run_id` — the lock then holds a run_id no session record carries, wedging the issue for the full 300s TTL. Today holder_token is per-process and never persisted to a second store, so no equivalent window exists. | REVISED: post-save readback assertion releases the lock on mismatch/failure; peek path flags `orphaned_lock: true`. New Race 3 + failure-path test row. | After `session.save()` in `ensure_session`'s create/adopt path, re-read the session and assert `active_run_id` matches the lock payload's run_id; on mismatch/exception, delete `session:issuelock:{N}` so the next caller need not wait out the TTL. Give the peek path a way to flag a lock whose run_id has no matching live session. |
| CONCERN | Risk & Robustness + Scope & Value | The `override: <reason>` auth-file escape hatch is new operator-facing scope absent from the issue's acceptance criteria, with zero coverage in Failure Path Test Strategy / Test Impact / Success Criteria, and its use is only "logged loudly" — invisible unless someone greps hook logs. An unwatched, untested bypass is the #1944 gap re-armed. | REVISED: both branches test-covered (Failure Path rows: empty/legacy blocks; `override:` allows + warning log + `merge_guard.override_used` metric asserted); new Success Criterion + Verification row "Override path covered". | Add failure-path rows: empty/legacy-format file → hook still blocks (assert block); `override: <reason>` file → hook allows + logs at warning (assert allow + log record). Parse the override at the same call site as today's `_is_authorized` (one code path decides format validity). In the override-accepted branch, `record_metric("merge_guard.override_used", 1, {"pr_number": ..., "reason": ...})` so the dashboard/analytics surface every use. |
| CONCERN | Scope & Value | The one-release `SDLC_HOLDER_TOKEN` legacy alias (Risk 2 / Open Question 3 default) has no mechanical removal guard: the Verification grep permanently excludes `legacy_alias`, so the "temporary bridge" can persist indefinitely with verification green — and Success Criterion 2's grep over `models/` contradicts the alias living in `models/session_lifecycle.py` during the window. | REVISED: hard cutover adopted (critics' recommendation, resolving former Open Question 3). No alias ships; deploy runbook pairs merge with `worker-restart`; Verification grep no longer excludes anything. | Prefer hard cutover: sequence "merge → `./scripts/valor-service.sh worker-restart`" in the deploy runbook — the worker is a single process, no session survives the restart boundary to present a stale token — and drop the alias entirely. If a window is kept, file the dated removal issue now and make the Verification grep fail after the sunset instead of excluding `legacy_alias` forever. |
| CONCERN | History & Consistency | "PR number lives on the session record (single writer: /do-build)" assumes a field that does not exist: `AgentSession` has `pr_url` but no `pr_number` (the rung at `tools/sdlc_stage_query.py:425` is dead — `getattr(session, "pr_number", None)` always None). A second schema field + migration is required but Architectural Impact / Update System account only for `active_run_id`. | REVISED: `pr_number = IntField(null=True)` added to the plan; single migration registers both fields; stale "primary rung" comment fix scoped in Task 4; Architectural Impact + Update System updated. | Either add `pr_number = IntField(null=True)` to AgentSession with its own idempotent migration (mirror the `active_run_id` pattern in `scripts/update/migrations.py`) and fix the stale "primary rung" comment at `tools/sdlc_stage_query.py:422-424`, or rewrite the plan language to target the existing `_pr_number` meta-key mechanism (the de-facto working path). |
| NIT | Structural checks | Verification rows using `grep -c ... == 0` are exit-code traps: `grep -c` prints 0 but exits 1 on zero matches, so a harness checking exit codes reads success as failure. | REVISED: all zero-match rows converted to `grep … \| wc -l` shapes. | — |
| NIT | Structural checks | Tasks 5-7 (validate-workstream, document-feature, validate-all) carry no `Validates` line / validation command; they lean on the Verification table implicitly. | REVISED: Validates lines added to tasks 5-7. | — |

### Cycle 2 (2026-07-10, against revision 1 — verdict NEEDS REVISION, 1 blocker; both cycle-1 blockers verified FIXED)

| Severity | Critic | Finding | Addressed By |
|----------|--------|---------|--------------|
| BLOCKER | Risk & Robustness + History & Consistency | Two non-CLI `touch_issue_lock()` renewal call sites (`tools/_sdlc_utils.py:399` `renew_issue_lock_for_session`; `agent/session_executor.py:232` `_tick_issue_lock_renewal`, fire-and-forget) pass `session_id` positionally and are unnamed in the plan — under the run_id contract they silently mismatch, the 300s TTL lapses mid-session, and a second supervisor can win the lock out from under a running eng session (#1915 class, reintroduced by this plan's own change). | REVISED (rev 3): both call sites named in Technical Approach + Task 1; each sources identity from `agent_session.active_run_id` (read-back of own established identity, not foreign adoption); `_tick_issue_lock_renewal` warns on not-owner; per-site renewal-past-TTL regression tests; new Verification rows "Renewal call sites flipped" + "No session_id passed to lock renewal"; Test Impact rows added. |
| CONCERN | Risk & Robustness | Crash-window release specs raw `DEL` (can delete a successor's fresh lock); post-save readback cannot cover true process death — "no 300s wedge" overreaches. | REVISED (rev 3): compare-and-delete (Lua release pattern) replaces `DEL`; scope claim corrected — readback covers save-failure; process death is TTL-bounded + `orphaned_lock`-flagged. |
| CONCERN | Risk & Robustness | "Fail-closed on predicate errors" vs "foreign-repo skip" had no discriminator for the same observable signal. | REVISED (rev 3): ordered detection — substrate probed first as a repo property (addendum present / sdlc-tool resolvable); only substrate-present evaluation failures fail closed. |
| CONCERN | Scope & Value | "Skill bodies stop carrying ownership state" overstated; a supervisor that loses its run_id has no self-recovery once adopt-from-record is removed. | REVISED (rev 3): claim restated precisely (prompt-carried within a run, fail-loud); documented bounded recovery — re-`ensure_session`, ≤300s TTL wait, fresh mint. |
| NIT | History & Consistency | No-Gos #1927 bullet said "one field" after `pr_number` was added. | REVISED (rev 3): "two fields". |
| NIT | Scope & Value | Risk 2 claimed in-flight forks "acquire normally" post-cutover. | REVISED (rev 3): reworded — fails loudly at next mutating call, bounded re-entry via `session-ensure`. |

---

## Resolved Questions (settled at revision, 2026-07-10)

1. **Hook posture:** fail-closed on predicate-evaluation errors. In foreign repos with no
   substrate, PR-state checks (group a) still enforce; substrate checks (groups b/c) skip
   with a logged notice. Break-glass override requires `override: <reason>` and is
   test-covered + metriced (`merge_guard.override_used`).
2. **Validated search rung:** stays as read-only recovery below the session field (#1998
   made it safe; deleting it would lose out-of-band PR recovery — critics raised no
   objection).
3. **Migration window:** hard cutover, no legacy alias — per critique CONCERN 3. Deploy
   runbook pairs the merge with `worker-restart`; the 300s lock TTL bounds any residual
   overlap.
