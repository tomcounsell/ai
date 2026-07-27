---
title: SDLC multi-pipeline coordination hardening
slug: sdlc-batch-coordination-hardening
type: bug
appetite: Medium
status: Ready
tracking: https://github.com/tomcounsell/ai/issues/2305
last_comment_id: 5057971623
revision_applied: true
revision_applied_at: 2026-07-27T03:58:03Z
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

## Implementation Notes (critique revision — build the CORRECTED design)

Critique verdict: **READY TO BUILD (WITH CONCERNS)**, 0 blockers, 4 concerns + 1 nit.
All accepted. These notes are authoritative — where they conflict with any earlier
prose below, the notes win. The builder must implement the corrected design, not the
original.

**Resolution 1 — single source of truth for the owner pid (concern 3; collapses
concerns 2+3+4).** Do **NOT** add `supervisor_pid` / `supervisor_hostname` fields to the
`AgentSession` model. The owner pid already lives in exactly one place: the Redis
issue-lock JSON payload (`session:issuelock:{N}`), which `touch_issue_lock`
(`models/session_lifecycle.py:1050`) already stamps with `pid` + `hostname`. Keep it
there. Both liveness read sites — `_run_id_has_live_session` (via the `touch_issue_lock`
peek `orphaned_lock` computation) and the `_iter_orphan_sessions` / `_last_activity_at`
reaper in `tools/sdlc_session_ensure.py` — resolve liveness by reading that **same lock
payload** through **one shared predicate**, named `_lock_owner_is_live(payload)`, living
in `models/session_lifecycle.py` (the module that owns the payload). No dual storage, no
per-`session-ensure` re-stamp (moots concern 2's re-stamp race), no reconciliation gap
between two divergent inferences. The reaper resolves the payload for a
`sdlc-local-{N}` row by reading the issue-lock key for that issue (a plain non-Popoto
Redis key — direct `_R.get` reads are allowed here; this is NOT a Popoto-managed key, so
the raw-Redis prohibition does not apply). If the payload is absent/malformed, fail
toward reapable-only-when-also-idle (never reap a row whose lock still exists and reads
live).

**Resolution 2 — pid-recycling guard via create_time (concern 1).** A bare
`os.kill(pid, 0)` / `_pid_is_alive(pid)` can match a **recycled** pid now belonging to an
unrelated live process, reconstituting the mirage with no TTL self-correction. Fix: record
`create_time` in the lock payload alongside `pid` at **acquire** time
(`touch_issue_lock` line 1050 write — add `"create_time": <psutil create_time of
os.getpid()>`). The self-heal renewal path (line 1089+) already spreads the existing
payload verbatim, so `create_time` survives TTL renewals with no extra code.
`_lock_owner_is_live` then treats the process as the owner **only if pid is alive AND its
current `psutil` create_time matches the recorded one** — reuse
`session_health._psutil_process_for_pid(pid)` then compare `proc.create_time()` (the
same `abs(a-b) > 1e-3` tolerance the drain path at `session_health.py:5398` uses).
Missing `create_time` in a legacy payload → treat as indeterminate on the same-host path
and fall through to the fail-toward-True backstop (do not hard-kill on an unverifiable
match).

**Resolution 3 — drop the cross-host worktree-mtime path (concern 4).** Local batches are
single-host by construction, so the speculative cross-host worktree-mtime freshness path
and the `SDLC_RUN_WORKTREE_FRESH_SECONDS` constant are **removed entirely** — do NOT add
them. Replace with a simple fail-toward-True fallback: in `_lock_owner_is_live`, when
`payload["hostname"] != socket.gethostname()`, a foreign-host owner pid cannot be checked
locally, so **assume live** (return True); the lock TTL remains the ultimate backstop for
a genuinely dead foreign owner. This removes the only cross-host machinery and moots the
reaper's worktree-path-resolution question — **Open Question #2 is removed**. Same-host is
the only path that ever inspects a pid.

**Resolution 4 — Defect 4 unchanged, session-keyed edge resolved (from original scope).**
Keep Defect 4 as planned: extract fail-closed `verdict_invariant_satisfied(stage,
issue_number)` into `tools/sdlc_verdict.py`, enforce it in `_backfill_predecessors`' scan
phase (raise before mutating REVIEW/CRITIQUE to `completed` without a verdict), recovering
`issue_number` from `self._ledger.issue_number`. Session-keyed-backfill edge: the real
trigger path constructs via `for_issue(target_repo, issue_number)`
(`sdlc_stage_marker.py:401`), so `self._ledger.issue_number` is populated whenever
REVIEW/CRITIQUE backfill can actually fire. If a `PipelineStateMachine` is ever
constructed session-keyed (no `_ledger`, or `_ledger.issue_number` is None) AND
REVIEW/CRITIQUE is a to-promote member, the invariant **fails CLOSED (raises)** — an
unverifiable verdict must never be promoted to `completed`.

**Resolution 5 — TOCTOU nit, document only (nit).** The `psutil` scan added to Defect 3's
`remove_worktree` is a TOCTOU window: a foreign process could `chdir` into the worktree in
the gap between the scan returning clear and `rmtree` running. Do **NOT** close it by
coupling cleanup to the machine-global suite lock (the No-Go stands — that lock is defect
2's domain). This residual window is accepted and documented; it is strictly narrower than
today's no-check-at-all behavior. No code change for this nit beyond the note.

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
  records the owner `pid` + `hostname` (the fix adds `create_time`), so the authoritative
  liveness signal is a same-host `psutil` pid-alive-AND-create_time-match check on that
  recorded pid. Reuse `agent/session_health.py` `_psutil_process_for_pid` and the
  create_time-tolerance pattern at `session_health.py:5398` rather than re-implementing.
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
hostname, create_time, target_repo}` (the fix adds `create_time`) → later
`touch_issue_lock(peek=True)` computes `orphaned_lock = not _lock_owner_is_live(payload)`.
The single shared predicate decides liveness from the payload alone (same-host pid +
create_time match; cross-host fail-toward-True), and `_iter_orphan_sessions` reads the
same payload and calls the same predicate. No AgentSession fields, no dual storage.

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
owns the lock payload). **Single source of truth = the issue-lock payload** (no new
AgentSession fields — see Implementation Notes resolution 1):

```
_lock_owner_is_live(payload) -> bool:
    # cross-host (payload["hostname"] != socket.gethostname()): fail TOWARD True
    #   (a foreign-host pid can't be checked locally); lock TTL is the backstop.
    # same-host: owner only if pid AND create_time match:
    #   proc = session_health._psutil_process_for_pid(payload["pid"])
    #   live iff proc is not None AND proc.create_time() ≈ payload["create_time"]
    #   (abs diff <= 1e-3, matching session_health drain-path tolerance).
    #   pid-recycling guard (resolution 2): a recycled pid without a matching
    #   create_time is NOT the owner.
    # payload lacks create_time (legacy) or is malformed/absent: indeterminate →
    #   fail toward True; the lock TTL remains the ultimate backstop.
```

- **Record `create_time` at acquire time.** `touch_issue_lock`'s payload write
  (`models/session_lifecycle.py:1050`) adds `"create_time"` (the psutil create_time of
  `os.getpid()`) alongside the existing `pid`+`hostname`. The self-heal renewal path
  (line 1089+) already re-spreads the existing payload verbatim, so `create_time` survives
  TTL renewals with no extra code.
- `touch_issue_lock` peek path (line ~1029) computes
  `orphaned_lock = not _lock_owner_is_live(payload)` using the pid/hostname/create_time in
  the payload. `_run_id_has_live_session` is replaced by (or reduced to delegate to) this
  payload-based check — no more "any non-terminal session ⇒ live" inference.
- **Reconcile the orphan reaper, do not fork it.** `tools/sdlc_session_ensure.py`
  `_last_activity_at` / `_iter_orphan_sessions` stop treating `updated_at` as a liveness
  proxy. Idle detection uses the SAME shared predicate: a `sdlc-local-{N}` session is
  reapable when `_lock_owner_is_live(payload)` is False (dead/recycled owner pid on
  same-host) AND it never heartbeated. The reaper resolves the payload by reading the
  issue-lock Redis key for that issue directly (`session:issuelock:{N}` is a plain
  non-Popoto key — a direct `_R.get` is allowed; the raw-Redis prohibition covers only
  Popoto-managed keys). If the payload is absent/malformed, do not treat that alone as
  proof of death. One shared predicate at both read sites — no divergent inferences, no
  new AgentSession fields, no per-`session-ensure` re-stamp race.

**Open question #5 (aggressive supervisor dedupe):** resolved-by-design, no separate
mechanism. Once a dead supervisor's lock is flagged `orphaned_lock=True` within one TTL
via authoritative liveness, the existing issue-lock takeover path lets a single new
supervisor reclaim the issue. Redundant supervisors piled up *only because* the ghost
lock never released; fixing liveness removes the pile-up cause. Noted in No-Gos so no one
adds a redundant dedupe layer.

No new env-overridable constant is introduced (the original `SDLC_RUN_WORKTREE_FRESH_SECONDS`
worktree-mtime window is dropped per resolution 3 — cross-host is fail-toward-True with the
lock TTL as the backstop).

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

**Residual TOCTOU window (nit, documented not closed — resolution 5):** the scan → rmtree
sequence has a race: a foreign process could `chdir` into the worktree in the gap between
the scan returning clear and `rmtree` running. This window is **accepted** — it is
strictly narrower than today's no-check-at-all behavior. Do NOT close it by coupling
cleanup to the machine-global full-suite advisory lock (that lock is defect 2's domain;
the No-Go stands).

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

### Defect 1 — authoritative liveness (single source of truth = lock payload)
- [ ] Add `"create_time"` (psutil create_time of `os.getpid()`) to the `touch_issue_lock`
      payload write (`models/session_lifecycle.py:1050`), alongside the existing
      `pid`+`hostname`. Verify the self-heal renewal spread (line 1089+) carries it.
- [ ] Add `_lock_owner_is_live(payload)` to `models/session_lifecycle.py`: same-host →
      owner only if pid alive AND create_time matches (reuse
      `session_health._psutil_process_for_pid`, `abs diff <= 1e-3`); cross-host
      (`hostname != socket.gethostname()`) → fail toward True; legacy/absent create_time or
      malformed payload → fail toward True (lock TTL is the backstop).
- [ ] Rewrite `_run_id_has_live_session` to delegate to `_lock_owner_is_live` (no
      non-terminal-status inference); wire it into the `touch_issue_lock` peek
      `orphaned_lock` computation. **No new AgentSession fields.**
- [ ] Reconcile `tools/sdlc_session_ensure.py` `_last_activity_at` / `_iter_orphan_sessions`
      to the SAME shared predicate: read the issue-lock payload (direct `_R.get` on the
      non-Popoto `session:issuelock:{N}` key) and call `_lock_owner_is_live`; remove
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
- [ ] Defect 1: dead-owner lock flips `orphaned_lock=True` within one TTL; a live-pid
      owner (matching create_time) stays non-orphaned; a **recycled pid** (alive pid but
      create_time mismatch) is treated as dead (recycling-guard test); a foreign-host
      payload stays non-orphaned (fail-toward-True); a hollow session with a dead recorded
      pid is reapable by `_iter_orphan_sessions` while a live one is exempt.
- [ ] Defect 3: `remove_worktree` returns `("blocked", ...)` when a live process has cwd
      rooted in the worktree; is clear when none does; `force=True` overrides.
- [ ] Defect 4: `start_stage("DOCS", backfill_predecessors=True)` raises (no REVIEW
      mint) when REVIEW has no verdict; succeeds when a finalized APPROVED+trailer verdict
      is present; symmetric CRITIQUE case.

## Documentation
- [ ] Update `docs/features/sdlc-verdict-fail-closed-persistence.md` to note the backfill
      write path is now closed (not just the downstream selfcheck).
- [ ] Add a short section to `docs/features/single-machine-ownership.md` (or the relevant
      SDLC-liveness feature doc) describing the authoritative liveness signal for
      `sdlc-local-{N}` runs — the issue-lock payload's `pid`+`create_time` (same-host,
      recycling-guarded), cross-host fail-toward-True with the lock TTL as backstop — and
      that `updated_at` is no longer a liveness proxy.
- [ ] Document the process-rooted-in-worktree cleanup guard in the worktree/cleanup
      feature doc referenced by #1357, including the accepted residual scan→rmtree TOCTOU
      window.

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
- Do NOT add `supervisor_pid` / `supervisor_hostname` (or any new) AgentSession fields for
  liveness (critique concern 3). The owner pid/hostname/create_time live in the ONE
  issue-lock payload; both read sites share `_lock_owner_is_live`. Dual storage
  reintroduces the re-stamp race (concern 2) and a reconciliation gap.
- Do NOT add a cross-host worktree-mtime freshness path or `SDLC_RUN_WORKTREE_FRESH_SECONDS`
  (critique concern 4). Cross-host is fail-toward-True; the lock TTL is the backstop.
- Do NOT rely on a bare pid-alive check without the create_time comparison (critique
  concern 1) — a recycled pid would reconstitute the mirage with no TTL self-correction.

## Update System

No update-system changes required — all three fixes are internal to the SDLC pipeline
subsystem. No new AgentSession fields (the owner pid lives solely in the issue-lock
payload) and no new config settings, so there is nothing to migrate or propagate. The
added `create_time` payload key is written on the next lock acquire and is optional on
read (legacy payloads without it fail toward True), so no backfill is needed.

## Agent Integration

No agent integration required — this hardens internal SDLC coordination paths reached via
existing `sdlc-tool` subcommands and the worktree/pipeline modules. No new CLI entry
point in `pyproject.toml`, and the bridge imports nothing new. Existing `sdlc-tool`
surfaces (`session-ensure`, `stage-marker`, `verdict`) keep their contracts; behavior
changes are confined to internal liveness/guard decisions.

## Test Impact

- [ ] `tests/unit/test_session_lifecycle.py` — UPDATE: existing `_run_id_has_live_session`
      / `touch_issue_lock` peek cases must be updated for the payload-pid+create_time-based
      orphaned decision (a non-terminal session with a dead or recycled recorded pid is now
      orphaned); add the new `create_time` payload key to acquire-write assertions.
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
      `_iter_orphan_sessions` has coverage) — UPDATE for the shared `_lock_owner_is_live`
      payload signal replacing `updated_at`.

No expected-failure (`xfail`) markers found related to these defects.

## Failure Path Test Strategy

Each defect's failure path is exercised directly:
- **Defect 1:** simulate a dead owner (recorded pid not alive) and assert the lock peek
  reports `orphaned_lock=True` within TTL; simulate a live owner (own pid + matching
  create_time) and assert it stays live — proving no `updated_at` dependence; simulate a
  recycled pid (alive pid, mismatched create_time) and assert it reads dead; simulate a
  foreign-host payload and assert it reads live (fail-toward-True).
- **Defect 3:** spawn a short-lived child process with cwd set inside a temp worktree and
  assert `remove_worktree` returns `("blocked", ...)`; without such a process, assert it
  proceeds.
- **Defect 4:** construct a `PipelineStateMachine` with REVIEW un-completed and no
  verdict, call `start_stage("DOCS", backfill_predecessors=True)`, assert `ValueError`
  and that REVIEW state is unchanged (no partial mutation); then record a finalized
  APPROVED verdict with a valid head_sha trailer and assert backfill succeeds.

## Rabbit Holes

- **Cross-host liveness.** Local batches run on one machine, so the same-host pid +
  create_time check is the only path that inspects a process. Do not over-engineer
  distributed liveness: a foreign-host owner is assumed live (fail-toward-True) and the
  lock TTL is the sole backstop. No worktree-mtime freshness path, no new constant.
- **psutil `proc.cwd()` cost.** Scanning all processes on every removal could be slow
  under load. Keep the scan bounded and per-process fail-open; do not add caching or a
  watchdog thread.
- **Refactoring the whole verdict-readability stack.** Only extract the shared predicate;
  do not restructure `write_marker`'s tri-state contract or the ledger-lease resolution.

## Success Criteria

Every acceptance-criteria checkbox from issue #2305 maps to a task above:

- [ ] **A dead run's lease is detected as orphaned within one lock TTL using authoritative
      liveness evidence (no `updated_at` inference)** — Defect 1 tasks: `_lock_owner_is_live`
      pid+create_time signal (recycling-guarded) wired into the `touch_issue_lock` peek
      `orphaned_lock` computation; reaper reconciled off `updated_at` onto the same shared
      predicate. Verified by the defect-1 regression test.
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

## Open Questions (resolved at critique)

1. **Defect 4 session-keyed backfill** — RESOLVED. Fail CLOSED (raise) on an unresolvable
   `issue_number`: an unverifiable verdict must never be promoted to `completed`. The real
   trigger path (`write_marker` → `for_issue`, `sdlc_stage_marker.py:401`) always populates
   `self._ledger.issue_number`, so the only paths that would raise are ones that genuinely
   cannot verify a verdict — which is the desired fail-closed behavior. See Implementation
   Notes resolution 4.
2. ~~Defect 1 worktree-mtime resolution for the reaper~~ — REMOVED. The cross-host
   worktree-mtime freshness path is dropped entirely (Implementation Notes resolution 3);
   cross-host is fail-toward-True with the lock TTL as the backstop, so the reaper never
   needs to resolve a worktree path or stat its mtime.
