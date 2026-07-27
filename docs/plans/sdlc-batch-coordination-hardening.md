---
title: SDLC multi-pipeline coordination hardening
slug: sdlc-batch-coordination-hardening
type: bug
appetite: Medium
status: Planning
tracking: https://github.com/yudame/ai/issues/2305
last_comment_id:
---

# SDLC multi-pipeline coordination hardening

## Problem

A 2026-07-23 batch of seven concurrent local `/do-sdlc` runs on one machine surfaced
four coordination defects that each independently stalled pipelines and required manual
orchestrator surgery. This plan closes **three** of the four. Defect 2 (xdist
finalization deadlock on full-suite merge gates) is **explicitly out of scope** — see
No-Gos — because it has been resolved on main since the issue was filed and must not be
re-fixed here.

The three in-scope survivors, re-validated against current `main`:

1. **Liveness mirage (defect 1).** `_run_id_has_live_session()` reports any non-terminal
   eng session carrying the run_id as live, and the `--kill-orphans` reaper keys idle
   detection on `updated_at` — a field refreshed by monitoring probes and sibling
   `session-ensure` renewals. A hollow `sdlc-local-{N}` tracking session (no pid, no
   heartbeat, no turns, worktree frozen for hours) therefore reads as live forever, so a
   dead run's merge lease never flips `orphaned_lock=True` and approved PRs cannot merge.

3. **Cleanup races a live sibling (defect 3).** `worktree_busy_check` only inspects
   `AgentSession` rows whose `working_dir` is inside the worktree. It does **not** detect
   an arbitrary OS process (a sibling fork's pytest, cwd rooted in the worktree). So
   `cleanup_after_merge` → `remove_worktree` → rmtree can delete a worktree with a live
   foreign process inside it, wedging the sibling's gate (the macOS cwd-vanished failure,
   #1246/#1357, but triggered by a live peer rather than a dead session).

4. **Verdict-less REVIEW marker (defect 4).** `write_marker` closes the *direct*
   REVIEW-completed write with verdict/trailer/artifact guards (#2062/#2124/#2193). But
   `PipelineStateMachine._backfill_predecessors()` — reached whenever ANY downstream
   stage is marked `in_progress` via `write_marker` → `start_stage(stage,
   backfill_predecessors=True)` — force-sets `self.states["REVIEW"]="completed"` (and
   CRITIQUE) with **zero** verdict checks. This is the open write path suspected in the
   issue's open question #4: a REVIEW `completed` marker minted with a null verdict /
   missing head_sha trailer. #2193 is only the downstream selfcheck gate, not a write-path
   closure.

**Desired outcome:** an unattended multi-pipeline batch completes without orchestrator
surgery — dead runs are detected from authoritative liveness evidence, cleanups cannot
destroy in-flight sibling state, and REVIEW markers cannot exist without verdicts.

## Freshness Check

Baseline commit: `61042fea7` (main at plan time). Issue filed 2026-07-23; supervisor
re-validated the four defects against `66a433bd9`. All in-scope file:line references
re-read against `61042fea7` and confirmed still present:

| Reference | Cited location | Status at `61042fea7` | Disposition |
|---|---|---|---|
| `_run_id_has_live_session()` | `models/session_lifecycle.py:895` | Present, unchanged; still returns True for any non-terminal eng session with matching run_id | **Unchanged** |
| `touch_issue_lock(peek)` orphaned flag | `models/session_lifecycle.py:1029` | `orphaned_lock=not _run_id_has_live_session(owner_run_id)`; lock payload already records `pid` + `hostname` | **Unchanged** |
| orphan reaper idle logic | `tools/sdlc_session_ensure.py:689` `_last_activity_at`, `:713` `_iter_orphan_sessions` | Present; keys idle on `updated_at` → `started_at` → `created_at` | **Unchanged** |
| heartbeat-authoritative check | `agent/session_health.py:5250` `_session_is_alive` | Present; heartbeat-only, returns False for worker-less local runs (never heartbeat) | **Unchanged** |
| `worktree_busy_check` | `agent/worktree_manager.py:433` | Present; AgentSession-row-only, no OS-process scan | **Unchanged** |
| `remove_worktree` busy guard | `agent/worktree_manager.py:1292` | Present; returns `("blocked", session_id)` on live-session hit | **Unchanged** |
| `cleanup_after_merge` | `agent/worktree_manager.py:1632` | Present; calls `remove_worktree` then rmtree | **Unchanged** |
| `_backfill_predecessors` | `agent/pipeline_state.py:576` | Present; force-completes on-spine predecessors, no verdict check | **Unchanged** |
| backfill trigger | `tools/sdlc_stage_marker.py:412` | `sm.start_stage(stage, backfill_predecessors=True)` on the `in_progress` path — verdict guards (`:427`,`:448`) only run on the `completed` path for the named stage | **Unchanged** |
| verdict-readable helpers | `tools/sdlc_stage_marker.py:119` `_review_verdict_readable`, `:152` `_review_trailer_present`, `:197` `_critique_verdict_readable` | Present; delegate to `tools.sdlc_verdict.get_verdict`; fail-closed | **Unchanged** |

Defect 2's fix path was confirmed resolved on main by the supervisor; no defect-2 code is
touched here. Disposition overall: **Unchanged** — all three in-scope defects are still
live and the plan proceeds on verified premises.

## Research

Purely-internal work (no external libraries, APIs, or ecosystem patterns) — Phase 0.7
external research skipped per the skill. No relevant external findings; proceeding with
codebase context.

## Prior Art

- **PR #2172 — "Add PID liveness check to update lock guard (#2169)".** Establishes the
  repo pattern for judging a Redis lock's owner liveness by pid-alive rather than a
  timestamp. Defect 1's fix follows this exact shape: the issue lock payload already
  records the owner `pid` + `hostname`, so the authoritative liveness signal is a
  same-host `psutil` pid-alive check on that recorded pid. Reuse `agent/session_health.py`
  `_pid_is_alive` / `_psutil_process_for_pid` rather than re-implementing.
- **PR #1943 — "reap claude -p process group before requeue/worktree cleanup (#1938)".**
  Confirms the codebase already reaps *owned* subprocesses before worktree cleanup; the
  gap defect 3 closes is *foreign* processes (a sibling fork's pytest) that this run does
  not own and cannot reap — those must instead *block* cleanup.
- **#1357 / #1246** — the existing `worktree_busy_check` + refuse-busy guard that defect 3
  extends. The AgentSession-row check is correct as far as it goes; defect 3 adds an
  OS-process-rooted-in-worktree scan alongside it.
- **#2062 / #2124 / #2193** — the fail-closed verdict/trailer guards in `write_marker`
  that defect 4 must reuse (not duplicate) so the backfill path enforces the identical
  invariant.

## Why Previous Fixes Failed

- **Liveness (defect 1):** prior liveness work (`_session_is_alive`, #1271) is
  heartbeat-authoritative, which is correct for worker-executed sessions but returns
  False for worker-less local `/do-sdlc` runs that never heartbeat. Using it directly
  would falsely kill a *live* local run. The orphan reaper (#1676) then over-corrected by
  keying on `updated_at`, which is refreshed by probes and siblings — reintroducing the
  mirage. Neither signal is authoritative for a local supervisor; the pid the lock
  *already records* is.
- **REVIEW verdict (defect 4):** #2062/#2124/#2193 closed the *direct* completed-write and
  the *downstream selfcheck*, but left the backfill write path (`start_stage(...,
  backfill_predecessors=True)`) untouched — it mutates `self.states` directly, never
  passing through the `write_marker` completed-path guards.

## Data Flow

**Defect 1 (liveness):** local `/do-sdlc` supervisor → `sdlc-tool session-ensure` mints
`sdlc-local-{N}` and `touch_issue_lock` writes lock payload `{run_id, session_id, pid,
hostname, target_repo}` → later `touch_issue_lock(peek=True)` computes
`orphaned_lock = not _run_id_has_live_session(run_id)`. Fix inserts an authoritative
liveness decision (pid-alive of the recorded owner pid, worktree-mtime fallback) at the
peek computation and reconciles `_iter_orphan_sessions` to the same signal.

**Defect 4 (verdict):** any stage marker write for a downstream stage → `write_marker(status="in_progress")`
→ `PipelineStateMachine.for_issue(target_repo, issue_number)` → `sm.start_stage(stage,
backfill_predecessors=True)` → `_backfill_predecessors` force-completes REVIEW/CRITIQUE.
Fix inserts the verdict invariant into `_backfill_predecessors` (issue_number recovered
from `self._ledger.issue_number`).

## Appetite

**Medium.** Three surgical, disjoint fixes in three subsystems, each with a focused
regression test. No schema changes, no cross-machine propagation, no new dependencies
beyond `psutil` (already a dependency).

## Solution

### Defect 1 — one authoritative liveness signal (resolves open questions #1 and #5)

**Decision:** liveness of a `sdlc-local-{N}` run is judged by the **pid the issue lock
already records**, not by `updated_at` and not by non-terminal status. This honors the
repo's single-authoritative-liveness principle (the lock module owns the run-ownership
lifecycle and already stamps the owning pid + hostname) and PR #2172's precedent.

Authoritative liveness helper (new, in `models/session_lifecycle.py` — the module that
owns the lock payload):

```
_lock_owner_is_live(payload) -> bool:
    # same-host: pid-alive of payload["pid"] via psutil (reuse session_health._pid_is_alive)
    # cross-host (payload["hostname"] != this host): worktree-mtime freshness within
    #   SDLC_RUN_WORKTREE_FRESH_SECONDS (provisional/tunable, env-overridable)
    # neither determinable: fail toward True (never mislabel a live cross-host run),
    #   with the lock TTL as the ultimate backstop
```

- `touch_issue_lock` peek path (line ~1029) computes
  `orphaned_lock = not _lock_owner_is_live(payload)` using the pid/hostname already in the
  payload. `_run_id_has_live_session` is either replaced by, or reduced to delegate to,
  this payload-based check — no more "any non-terminal session ⇒ live" inference.
- **Reconcile the orphan reaper, do not fork it.** `tools/sdlc_session_ensure.py`
  `_last_activity_at` / `_iter_orphan_sessions` stop treating `updated_at` as a liveness
  proxy. Idle detection uses the SAME authoritative signal: a `sdlc-local-{N}` session is
  reapable when its recorded owner pid is dead (same-host) or its worktree mtime is stale
  beyond `SDLC_RUN_WORKTREE_FRESH_SECONDS` (cross-host) AND it never heartbeated. The
  recorded owner pid is resolved from the issue-lock payload (or persisted on the session
  at `session-ensure`, see next bullet). This eliminates the mirage at both read sites
  through one shared predicate rather than two divergent inferences.
- **Persist the owner pid where the reaper can read it.** The lock payload holds
  `pid`+`hostname`, but the reaper walks `AgentSession` rows, not lock payloads. To keep
  the reaper's query cheap and avoid a lock lookup per row, record the supervisor pid +
  hostname on the `sdlc-local-{N}` session at `session-ensure` time (new nullable fields
  — heals generically per `_heal_descriptor_pollution`, no backcompat code). The single
  authoritative predicate reads these fields; the lock peek path reads the payload; both
  call the same `_pid_is_alive` core.

**Open question #5 (aggressive supervisor dedupe):** resolved-by-design, no separate
mechanism. Once a dead supervisor's lock is flagged `orphaned_lock=True` within one TTL
via authoritative liveness, the existing issue-lock takeover path lets a single new
supervisor reclaim the issue. Redundant supervisors piled up *only because* the ghost
lock never released; fixing liveness removes the pile-up cause. Noted in No-Gos so no one
adds a redundant dedupe layer.

Named, env-overridable constant with a provisional/tunable comment:
`SDLC_RUN_WORKTREE_FRESH_SECONDS` (worktree-mtime freshness window; grain-of-salt: tuned
against observed batch cadence, adjust if long single-stage runs false-positive).

### Defect 3 — process-rooted-in-worktree guard (resolves open question #3)

Add a real OS-process guard so cleanup cannot delete a worktree with a live process
inside it. **Decision:** a per-worktree process scan (not the machine-global suite lock —
that lock is defect 2's territory and out of scope, and coupling cleanup to it would
serialize unrelated cleanups).

- New helper `_worktree_has_live_process(worktree_dir) -> int | None` in
  `agent/worktree_manager.py`: iterate `psutil.process_iter`, for each live process
  attempt `proc.cwd()`, and return the first pid whose cwd is under `worktree_dir`
  (segment-aware containment, mirroring `worktree_busy_check`'s `.parts`-prefix match so
  `sdlc-1218` does not match `sdlc-1218-other`). Fail-open **per process** (skip
  processes we cannot inspect — permission/zombie), but a positive hit is authoritative.
- `remove_worktree` calls it alongside the existing `worktree_busy_check` (before the
  rmtree/CWD-death block). A live foreign pid returns `("blocked", f"pid:{pid}")` exactly
  like the live-session case, so `cleanup_after_merge` already surfaces it into
  `blocked_by_session` / `result["errors"]` and `post_merge_cleanup.py` exits non-zero.
- `force=True` still overrides (logged WARNING), preserving the verified-dead escape hatch.

macOS note: `proc.cwd()` is available for same-user processes (sibling forks are same
user); cross-user/denied processes are skipped fail-open. No new dependency — `psutil` is
already used across `session_health.py`.

### Defect 4 — close the backfill verdict bypass (resolves open question #4)

**Decision:** `_backfill_predecessors` must enforce the SAME verdict invariant as the
`write_marker` completed-path, reusing one fail-closed predicate (no duplicated logic,
no drift).

- Extract the invariant into a single reusable predicate,
  `verdict_invariant_satisfied(stage, issue_number) -> bool`, living in
  `tools/sdlc_verdict.py` (the verdict source of truth; `pipeline_state` already lazily
  imports `tools.sdlc_verdict`, so no import cycle). For REVIEW it ANDs the existing
  `_review_verdict_readable` + `_review_trailer_present` logic; for CRITIQUE it wraps
  `_critique_verdict_readable`. Fail-CLOSED on any error. Refactor the three
  `sdlc_stage_marker.py` helpers to delegate to it so the write path and backfill path
  share ONE implementation.
- In `_backfill_predecessors`, during the **scan** phase (before any mutation, preserving
  the scan-then-mutate no-partial-state property): for each to-promote member that is
  REVIEW or CRITIQUE, resolve `issue_number` from `self._ledger.issue_number` and call
  the predicate. If the invariant is not satisfied → `raise ValueError` (symmetric with
  the existing failed-predecessor raise at line 600), leaving REVIEW/CRITIQUE at their
  real state and surfacing loudly. This closes the write path: a downstream stage cannot
  be started-with-backfill if doing so would mint a verdict-less REVIEW/CRITIQUE
  completion.
- **issue_number-unresolvable edge:** if REVIEW/CRITIQUE is a to-promote member but
  `issue_number` cannot be resolved (session-keyed construction with no `_ledger`), fail
  CLOSED (raise) — an unverifiable verdict must never be promoted. The primary trigger
  path constructs via `for_issue(target_repo, issue_number)` (sdlc_stage_marker.py:401),
  so `self._ledger.issue_number` is populated in the real backfill path. (See Open
  Questions for whether any legitimate session-keyed backfill of REVIEW/CRITIQUE exists.)

## Step by Step Tasks

### Defect 1 — authoritative liveness
- [ ] Add `SDLC_RUN_WORKTREE_FRESH_SECONDS` to `config/settings.py` `TimeoutSettings`
      (env-overridable `TIMEOUTS__*`), with a provisional/tunable grain-of-salt comment.
- [ ] Add nullable `supervisor_pid` + `supervisor_hostname` fields to the AgentSession
      model; populate them for `sdlc-local-{N}` at `session-ensure` time.
- [ ] Add `_lock_owner_is_live(payload)` to `models/session_lifecycle.py` (same-host
      pid-alive via reuse of `session_health._pid_is_alive`; cross-host worktree-mtime
      freshness; fail-toward-True only when undeterminable).
- [ ] Rewrite `_run_id_has_live_session` to delegate to the payload/field-based
      authoritative check (no non-terminal-status inference); wire it into the
      `touch_issue_lock` peek `orphaned_lock` computation.
- [ ] Reconcile `tools/sdlc_session_ensure.py` `_last_activity_at` / `_iter_orphan_sessions`
      to the same authoritative predicate (pid-alive + worktree mtime), removing
      `updated_at` as a liveness proxy.

### Defect 3 — process-rooted-in-worktree guard
- [ ] Add `_worktree_has_live_process(worktree_dir)` to `agent/worktree_manager.py`
      (psutil process scan, per-process fail-open, segment-aware cwd containment).
- [ ] Call it in `remove_worktree` alongside `worktree_busy_check`; return
      `("blocked", "pid:{pid}")` on a live foreign process; keep `force=True` override.

### Defect 4 — close backfill verdict bypass
- [ ] Add `verdict_invariant_satisfied(stage, issue_number)` to `tools/sdlc_verdict.py`
      (fail-closed; REVIEW = verdict-readable AND trailer-present; CRITIQUE =
      verdict-readable). Refactor `sdlc_stage_marker.py` helpers to delegate to it.
- [ ] In `_backfill_predecessors`, gate promotion of REVIEW/CRITIQUE on the predicate
      during the scan phase; raise `ValueError` before mutating if unsatisfied or
      issue_number unresolvable.

### Regression tests (one per closed path — acceptance criterion)
- [ ] Defect 1: dead-owner lock flips `orphaned_lock=True` within one TTL; a live-pid /
      fresh-worktree owner stays non-orphaned; a hollow session with a dead recorded pid
      is reapable by `_iter_orphan_sessions` while a live one is exempt.
- [ ] Defect 3: `remove_worktree` returns `("blocked", ...)` when a live process has cwd
      rooted in the worktree; is clear when none does; `force=True` overrides.
- [ ] Defect 4: `start_stage("DOCS", backfill_predecessors=True)` raises (no REVIEW
      mint) when REVIEW has no verdict; succeeds when a finalized APPROVED+trailer verdict
      is present; symmetric CRITIQUE case.

## Documentation
- [ ] Update `docs/features/sdlc-verdict-fail-closed-persistence.md` to note the backfill
      write path is now closed (not just the downstream selfcheck).
- [ ] Add a short section to `docs/features/single-machine-ownership.md` (or the relevant
      SDLC-liveness feature doc) describing the authoritative pid/worktree-mtime liveness
      signal for `sdlc-local-{N}` runs and that `updated_at` is no longer a liveness proxy.
- [ ] Document the process-rooted-in-worktree cleanup guard in the worktree/cleanup
      feature doc referenced by #1357.
- [ ] Add `SDLC_RUN_WORKTREE_FRESH_SECONDS` to `docs/features/config-timeout-catalog.md`.

## No-Gos

- **Defect 2 (xdist finalization deadlock on full-suite merge gates) is OUT OF SCOPE.**
  It was resolved on main after the issue was filed. Do NOT add a suite-lock watchdog,
  full-suite serialization, or load-ceiling logic under this plan — that path is deleted
  and re-fixing it here would resurrect stale code.
- Do NOT couple worktree cleanup to the machine-global full-suite advisory lock (that
  lock is defect 2's domain); use a per-worktree process scan instead.
- Do NOT add a separate "aggressive supervisor dedupe by issue" mechanism (open question
  #5) — authoritative liveness releasing the ghost lock within one TTL is the dedupe.
- Do NOT touch `tools/sdlc_next_skill.py` `_fetch_pr_state` / `_fetch_pr_head_sha` — that
  is sibling issue #2404 (gh response cache staleness); keep this work disjoint.
- Do NOT use heartbeat-only liveness (`_session_is_alive` as-is) for local runs — it
  false-negatives worker-less supervisors that never heartbeat.

## Update System

No update-system changes required — all three fixes are internal to the SDLC pipeline
subsystem. The new nullable AgentSession fields heal generically via
`_heal_descriptor_pollution` (no migration needed for nullable adds; #1099/#1172), and
the new `SDLC_RUN_WORKTREE_FRESH_SECONDS` setting has a default so no config propagation
is needed.

## Agent Integration

No agent integration required — this hardens internal SDLC coordination paths reached via
existing `sdlc-tool` subcommands and the worktree/pipeline modules. No new CLI entry
point in `pyproject.toml`, and the bridge imports nothing new. Existing `sdlc-tool`
surfaces (`session-ensure`, `stage-marker`, `verdict`) keep their contracts; behavior
changes are confined to internal liveness/guard decisions.

## Test Impact

- [ ] `tests/unit/test_session_lifecycle.py` — UPDATE: existing `_run_id_has_live_session`
      / `touch_issue_lock` peek cases must be updated for the payload-pid-based orphaned
      decision (a non-terminal session with a dead recorded pid is now orphaned).
- [ ] `tests/unit/test_worktree_manager.py` — UPDATE: `remove_worktree` /
      `worktree_busy_check` cases gain the process-scan path; ensure existing
      no-live-process cases still return clear.
- [ ] `tests/unit/test_pipeline_state_machine.py` and `tests/unit/test_pipeline_state.py`
      — UPDATE: any existing `start_stage(..., backfill_predecessors=True)` case that
      backfills across REVIEW/CRITIQUE must now seed a finalized verdict or expect a
      `ValueError`.
- [ ] `tests/unit/test_sdlc_stage_marker.py` — UPDATE: helpers now delegate to the shared
      `verdict_invariant_satisfied`; assert no behavior change on the direct completed
      path.
- [ ] Orphan-reaper tests for `sdlc_session_ensure` (search suite; if
      `_iter_orphan_sessions` has coverage) — UPDATE for the pid/worktree-mtime signal
      replacing `updated_at`.

No expected-failure (`xfail`) markers found related to these defects.

## Failure Path Test Strategy

Each defect's failure path is exercised directly:
- **Defect 1:** simulate a dead owner (recorded pid not alive) and assert the lock peek
  reports `orphaned_lock=True` within TTL; simulate a live owner (own pid) and assert it
  stays live — proving no `updated_at` dependence.
- **Defect 3:** spawn a short-lived child process with cwd set inside a temp worktree and
  assert `remove_worktree` returns `("blocked", ...)`; without such a process, assert it
  proceeds.
- **Defect 4:** construct a `PipelineStateMachine` with REVIEW un-completed and no
  verdict, call `start_stage("DOCS", backfill_predecessors=True)`, assert `ValueError`
  and that REVIEW state is unchanged (no partial mutation); then record a finalized
  APPROVED verdict with a valid head_sha trailer and assert backfill succeeds.

## Rabbit Holes

- **Cross-host liveness.** Local batches run on one machine, so the same-host pid check is
  the hot path. Do not over-engineer distributed liveness; worktree-mtime is a sufficient
  cross-host fallback and the lock TTL is the backstop.
- **psutil `proc.cwd()` cost.** Scanning all processes on every removal could be slow
  under load. Keep the scan bounded and per-process fail-open; do not add caching or a
  watchdog thread.
- **Refactoring the whole verdict-readability stack.** Only extract the shared predicate;
  do not restructure `write_marker`'s tri-state contract or the ledger-lease resolution.

## Success Criteria

Every acceptance-criteria checkbox from issue #2305 maps to a task above:

- [ ] **A dead run's lease is detected as orphaned within one lock TTL using authoritative
      liveness evidence (no `updated_at` inference)** — Defect 1 tasks: `_lock_owner_is_live`
      pid/worktree-mtime signal wired into the `touch_issue_lock` peek `orphaned_lock`
      computation; reaper reconciled off `updated_at`. Verified by the defect-1 regression
      test.
- [ ] **A wedged full-suite gate self-recovers or fails loudly within a bounded window** —
      OUT OF SCOPE (defect 2, resolved on main). Explicitly recorded in No-Gos so this
      criterion is not re-opened here.
- [ ] **Post-merge worktree cleanup cannot delete a worktree with a live process rooted in
      it** — Defect 3 tasks: `_worktree_has_live_process` guard in `remove_worktree`.
      Verified by the defect-3 regression test.
- [ ] **REVIEW `completed` marker cannot be written without a finalized verdict (write path
      closed, not just gated downstream)** — Defect 4 tasks: shared
      `verdict_invariant_satisfied` predicate enforced inside `_backfill_predecessors`.
      Verified by the defect-4 regression test.
- [ ] **Regression tests cover each closed path** — one test per closed path under
      Regression tests above.

## Open Questions

1. **Defect 4 session-keyed backfill.** Is there any legitimate path that constructs
   `PipelineStateMachine` session-keyed (no `_ledger`) and backfills across
   REVIEW/CRITIQUE? If so, fail-closed-raise on unresolvable issue_number would break it.
   The real trigger (`write_marker` → `for_issue`) always has `_ledger`, so this is
   believed safe — critique should confirm no other caller relies on session-keyed
   backfill of those two stages.
2. **Defect 1 worktree-mtime resolution for the reaper.** Confirm the `sdlc-local-{N}`
   session record exposes (or can cheaply derive) its worktree path so
   `_iter_orphan_sessions` can stat its mtime for the cross-host fallback, without a
   per-row lock lookup.
