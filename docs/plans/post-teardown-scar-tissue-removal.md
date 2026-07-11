---
status: Ready
type: chore
appetite: Small
owner: Valor Engels
created: 2026-07-11
tracking: https://github.com/tomcounsell/ai/issues/1926
last_comment_id: none
revision_applied: true
---

# Post-teardown scar-tissue removal: happy-path liveness + Sentry reporting + removed-defenses ledger

## Problem

The granite PTY teardown (#1924, merged as PR #1930 on 2026-07-07) deleted the
PTY substrate and cut every session over to headless `claude -p` execution. What
it did NOT delete is the accreted failure-handling machinery built to defend that
fragile PTY substrate: a stall-classifier taxonomy, a crash-signature library,
three watchdogs, escalation ladders, and the bridge nudge auto-continue counter.
Much of that machinery classifies and guards failure modes that **cannot occur
under protocol-driven headless execution** — the harness now emits a structured
`result` event per turn, so "did the agent stop producing output" is answered by
the protocol, not by scraping a PTY master fd.

**Current behavior:**
The reporting-layer taxonomies (`session_stall_classifier`, `crash_signature`)
still enumerate classes that no longer occur under headless execution. There is no
single written record of *what each removed defense guarded against*, so a future
targeted fix has no map back to the gotcha.

**Scope correction from critique (verified against `main`):** the earlier premise
that `monitoring/worker_watchdog.py` carries a PTY-specific "kill the PTY master
fds" narrative is FALSE. `grep -ni "pty|master fd" monitoring/worker_watchdog.py`
returns nothing. The file's only U-state text (docstring lines ~10-21, 231-236,
and the W4 log at ~287) is the generic issue-#1767 rationale for the W1-W5
verified-kill ladder — a hung-worker mechanism that is **substrate-agnostic and
explicitly KEPT** by this plan. A `claude -p` subprocess can still wedge in
uninterruptible sleep on a blocking syscall exactly as any process can, so that
rationale stays. The watchdog therefore needs **no deletion**, only a confirmation
that no PTY-specific text exists.

**Desired outcome:**
Keep the happy path plus Sentry error reporting. Trim PTY-era rationale and
pare the reporting taxonomies to classes actually observed post-cutover. For
every deletion, add one entry to a **removed-defenses ledger**
(`docs/removed-defenses.md`) naming the gotcha the machinery guarded against, so
that when a matching Sentry issue reappears we re-apply a *targeted* fix — never
the old blanket machinery. Make explicit, evidence-grounded keep-vs-cut calls on
the three "review, don't blindly delete" surfaces.

Two folded-in scope items resolve open holds on the same recovery surface:
- **Closes #1855** — delete the `FEATURES__STALL_RECOVERY_ENABLED` flag
  (`config/settings.py:310`, sole read site `reflections/stall_advisory.py:316`).
  Per operator decision (Tom, 2026-07-02), stall recovery is THE behavior, not a
  dry-run-gated feature: the flag and its dry-run branch are removed so actuation
  is unconditional. The budgets/guards (consec-observation threshold, run +
  per-session kill budgets, Race-1 re-read) remain as the real safety mechanism.
- **Closes #1868** — the AUTONOMOUS slot-lease reap Phase 2 in
  `agent/session_health.py::_reap_slot_leases` (~lines 2867-2895; the reclaim
  decision is at 2874-2877) treats `AgentSession.get_by_id(owner) is None` as
  "owner terminal → reclaim" via `if fresh is None or getattr(fresh, "status",
  None) in _TERMINAL_STATUSES: registry.reclaim(...)`. But `get_by_id`
  (`models/agent_session.py:1068`) swallows transient Redis lookup exceptions into
  `return None` (its own `except Exception: logger.warning(...); return None` at
  lines 1090-1096) AND returns `None` for a genuine not-found (lines 1097-1098), so
  `None` alone cannot distinguish a read blip from a deleted record — a lookup blip
  during a reap tick can spuriously reclaim a live session's semaphore permit
  (over-admission).
  **Why a call-site try/except cannot fix this (round-3 blocker):** because
  `get_by_id` catches its OWN lookup exception internally and returns a plain
  `None`, no exception ever escapes to a call-site `try/except`. Wrapping the
  Phase-2 `get_by_id` call to store an `_ABSENT` sentinel on exception can NEVER
  populate `_ABSENT` — every `None` (transient blip OR genuine not-found) still
  reaches the `if fresh is None ... reclaim` branch, so the code would LOOK fixed
  and behave identically to the bug. The reap-local stale-check map just above
  (lines 2852-2865) inherits this exact defect: it wraps `get_by_id` too, so its
  `_ABSENT` branch is ALREADY unreachable dead code — it is NOT a usable precedent.
  **Fix (required):** relocate the error-vs-absent distinction INTO the lookup. Add
  a raising sibling on the model — `AgentSession.get_by_id_strict(id)` — that lets
  the `cls.query.filter` lookup exception PROPAGATE (returns the record or `None`
  for a clean not-found; raises on a lookup error). Call THAT from
  `_reap_slot_leases` Phase 2 and reclaim only on a confirmed-absent (`None` from a
  clean lookup) or terminal owner; on a raised lookup error, SKIP the reclaim (leave
  the live permit alone). Preserve the deliberate "genuinely-deleted record is
  reclaimable" behavior.
  **Not the same code as `_drain_reclaim_requests`** (the request-driven drain
  defined ~line 3025): that function ALREADY implements the correct behavior (its
  docstring carries the `#1868 trap` note and it skips reclaim on both `None` and
  lookup-error). A fix aimed at ~3046-3073 lands in already-correct code — the
  bug lives in the autonomous reaper, not the drain.

## Freshness Check

**Baseline commit:** `d7753e02` (worktree `session/dev-a1552d0a`); `main` at `11ad27fc`.
**Issue filed at:** 2026-07-06T08:07:09Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/session_runner/liveness.py::derive_sdk_ever_output` — the "one
  authoritative liveness signal" (owner directive 2026-07-07) already exists and
  is imported by `session_health` at its recovery-path derivation sites. The
  liveness *collapse* the issue lists as a scope candidate is therefore **largely
  already done** (#1843 Gap B, #2004 hygiene sweep). Remaining work is trimming
  residual duplicate inference, not rebuilding.
- `agent/session_health.py::_confirm_subprocess_dead` (line 1551) — the
  `TypeError: '<=' not supported between 'str' and 'int'` (Sentry VALOR-E1/D0)
  was **already fixed** by commit `b92d9a44` (2026-07-08, "Fix TypeError in
  _confirm_subprocess_dead on string claude_pid"): the defensive `int(pid)` cast
  now sits at lines 1597-1601 ahead of the `pid <= 0` comparison. VALOR-E1's
  events 9h ago are **deploy lag**, not a live defect on `main` (corroborated by
  VALOR-CT: "worker running 2f659c7f but HEAD is bcda4b1c"). **This bug is NOT in
  scope** — re-fixing an already-fixed defect would be building for a stale
  problem. (Correction to the intake instruction, which asked to fold it in.)
- `agent/output_router.py:39` `MAX_NUDGE_COUNT = 50` — still present and still
  wired live via `session_executor.send_to_chat -> route_session_output`.
- `monitoring/worker_watchdog.py::recover()` (lines 219-306) — kill ladder
  (W1-W5) present as described. Re-verified during this revision: the docstring
  (lines ~10-21, 231-236) and W4 log (~287) carry only the generic issue-#1767
  U-state rationale, **not** any PTY-master-fd narrative (`grep -ni "pty|master fd"`
  returns 0). This rationale is KEPT — it justifies a substrate-agnostic mechanism.
- `monitoring/bridge_watchdog.py::execute_recovery` (line 675), `revert_last_commit`
  (line 636) — 5-level ladder + revert present as described.

**Cited sibling issues/PRs re-checked:**
- #1924 — closed 2026-07-07; delivered by PR #1930 (merged 2026-07-07). This is
  the enabling prerequisite; landed.

**Commits on main since issue was filed (touching referenced files):**
- `b92d9a44` (2026-07-08) — fixed the `_confirm_subprocess_dead` TypeError.
  **Already fixes** the VALOR-E1/D0 item; removed from scope.
- `ffed9ba0` (#2004 "Resilience hygiene sweep") — unified session evidence,
  enforced artifact freshness. **Partially addresses** the liveness-collapse
  candidate (introduced `has_demonstrable_activity` as the shared leaf). Reduces
  this plan's liveness scope to residual trimming.
- `39bf0fc7` (#2002 epoch-scope tool-timeout guard) — touched session_health;
  irrelevant to this scope.

**Active plans in `docs/plans/` overlapping this area:**
- `resilience-simplification-three-tier.md` (status: draft, tracking: none). A
  broader "resilience program" synthesized 2026-07-10 from 21 closed bugs. It
  overlaps thematically (liveness/recovery, degradation contract) but is a
  *program* plan whose items each ship as their own issue/PR. **Coordination, not
  a blocker:** #1926 is the narrower PTY-teardown scar-tissue slice. Where the two
  touch the same file (`session_health.py`), #1926 confines itself to removing
  PTY-dead rationale and does not implement the three-tier program's event-sourced
  stage log (T3.4) or unified evidence model (that's #2004's territory, already
  landed). Note the overlap in the PR body so the resilience program can subtract
  what #1926 ships.

**Notes:** The "few weeks of headless telemetry" prerequisite is only ~4 days
satisfied, but the PTY-vs-headless signal is already clean and decisive (see
Research / Recon), so the deletion decisions are evidence-grounded rather than
predicted.

## Prior Art

- **#1924 / PR #1930** — Granite PTY teardown: deleted the PTY substrate, cut all
  sessions to headless `claude -p`. This is the direct predecessor; #1926 removes
  the failure machinery #1930 left behind.
- **#1843** — Headless-runner zombie liveness: introduced the headless
  `last_stdout_at` liveness stamp replacing the PTY-era `last_pty_read_loop_at`
  (see `agent/session_runner/liveness.py` docstring). Established `derive_sdk_ever_output`.
- **PR #2004 / #2011 (`ffed9ba0`)** — Resilience hygiene sweep: unified session
  evidence via `has_demonstrable_activity`, the shared leaf both
  `session_stall_classifier` and `crash_signature` already import.
- **`b92d9a44`** — Fixed the `_confirm_subprocess_dead` string-pid TypeError
  (the VALOR-E1/D0 defect). Already on main.
- **#1768 / PR #1773** — Shipped the stall-recovery gate ladder (consec-observation
  counter, kill budgets, Race-1 re-read, kill + valor-catchup re-enqueue) gated by
  `stall_recovery_enabled` (default False / dry-run). #1855 removes that gate.
- **#1855** (OPEN, folded in) — Operator decision (Tom, 2026-07-02) to remove the
  `FEATURES__STALL_RECOVERY_ENABLED` flag so recovery is one always-on path.
- **#1868** (OPEN, folded in) — REVIEW follow-up of PR #1867 (#1820 slot-lease
  ownership): the reap Phase-2 must distinguish `get_by_id` not-found from a
  transient lookup error before reclaiming a permit.
- No prior attempt has written a removed-defenses ledger; this is greenfield for
  that artifact.

## Research

**Queries used (Sentry, yudame org / project 4511091961888768):**
- `is:unresolved` sorted by frequency, 14d window
- `is:unresolved firstSeen:-7d` sorted by frequency, 7d window

**Key findings (headless-failure telemetry, 2026-07-11):**
- **PTY-era failure classes stopped firing at the #1930 cutover boundary (~5 days
  ago).** Every one of these has `lastSeen ~5d ago` and no headless-era recurrence:
  `Watchdog W4/W5 U-state / "kill the PTY master fds"` (VALOR-B7/B8, 210 events
  each), `[pty-pool] slot stuck/spawn failed` (VALOR-BF/A4), `[granite-container]
  startup plateau` (VALOR-AX), `[granite-exit-anomaly]` (VALOR-A3), `[deadman]
  loop beacon stale` (VALOR-BE/BG), `[executor-guard] refusing empty container
  message` (VALOR-B5). → These are the machinery to trim/ledger.
- **Headless-era real failures (last 2-3 days) are a small, different set:**
  `pm turn subprocess exited 143 without a result event` (VALOR-E0 — SIGTERM
  during a turn, i.e. steering preempt), `Harness exited without a result event`
  (VALOR-2M, still active), `StatusConflictError` session-lifecycle race
  (VALOR-DZ), and Redis MISCONF/disk-I/O infra noise (VALOR-DB family — a Redis
  RDB-persistence outage 2 days ago, NOT scar tissue). → These are the classes the
  pruned taxonomy must retain.
- The surviving real-failure surface maps cleanly onto the target liveness model:
  subprocess-alive (exit 143 / exit-without-result) + turn-timeout + last-turn-age.
  No headless failure observed requires a stall *classifier taxonomy* richer than
  "started vs never-started" and "clean vs non-clean exit."

## Data Flow

Liveness / failure-handling data flow, post-headless:

1. **Turn boundary (entry):** `agent/session_runner/runner.py` spawns `claude -p`,
   stamps `last_stdout_at` on first output and `last_turn_at` on the harness
   `result` event (`agent.hooks.liveness_writers`). The runner OWNS subprocess
   spawn/kill and turn-timeout (`turn_timeout_for`, `role_driver.turn_timeout_s`).
2. **Authoritative signal:** `agent/session_runner/liveness.py::derive_sdk_ever_output`
   folds `{last_tool_use_at, last_turn_at, last_stdout_at}` into the single
   "has the SDK ever produced output" predicate. `has_demonstrable_activity`
   folds `{turn_count, last_tool_use_at}` into the progress predicate.
3. **Worker recovery (consumer):** `agent/session_health.py` reads those leaves at
   its recovery-path derivation sites; `_confirm_subprocess_dead` reaps orphans.
4. **Reporting (read-only, no writes):** `session_stall_classifier.classify_session_stall`
   and `crash_signature.extract_signature` consume the same leaves to produce
   advisory verdicts / signature keys for reflections + Sentry. Zero mutations.
5. **Watchdogs (out-of-process):** `monitoring/{bridge,worker,session}_watchdog.py`
   supervise the bridge/worker processes and pending-session stalls.
6. **Bridge output routing:** `agent/output_router.py::route_session_output` decides
   deliver-vs-nudge; `session_executor.send_to_chat` executes it, incrementing
   `AgentSession.auto_continue_count` against `MAX_NUDGE_COUNT`.

The fix layer for THIS plan is (4) and (5) — the reporting taxonomies and the
watchdog PTY rationale — plus documentation of the keep decisions on (5)/(6).

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** no existing signatures change. `derive_sdk_ever_output` /
  `has_demonstrable_activity` remain the authoritative leaves. Removing enum
  members / verdict reasons from the reporting modules is an internal-taxonomy
  change; callers already branch only on `level in {healthy, suspect, stalled}`
  and `resumable`. The #1868 fix adds ONE additive classmethod,
  `AgentSession.get_by_id_strict` (a raising sibling of `get_by_id`) — behavior-only,
  no field/schema change.
- **Coupling:** decreases. Trimming dead PTY rationale and unobserved taxonomy
  classes reduces the surface that a future change must reason about. The single
  authoritative liveness signal (already owned by `session_runner`) is
  reaffirmed, not spread wider.
- **Data ownership:** unchanged — liveness stays owned by `session_runner`.
- **Reversibility:** high. Every deletion is recorded in the ledger with the
  exact gotcha and the Sentry class that would signal its return, so a targeted
  re-apply is a small, well-scoped change.

## Appetite

**Size:** Small

**Right-sizing (critique #4):** the actual diff surface is small. After the
watchdog scope correction (no PTY narrative to delete — confirmation only), the
real edits are: prune two pure reporting modules (`session_stall_classifier.py`,
`crash_signature.py`) to observed classes, a possible subtraction-only residual
trim in `session_health.py`, update their two test files, author one new doc
(`docs/removed-defenses.md`), and touch three existing docs. That is a
single-developer change, not a four-builder fan-out. Team is a solo dev with one
reviewer; no worktree fan-out is warranted.

**Team:** Solo dev, PM check-in, 1 code reviewer.

**Interactions:**
- PM check-ins: 1-2 (keep-vs-cut sign-off on the nudge counter; overlap
  coordination with the resilience program plan)
- Review rounds: 1 (code review + cruft audit on a deletion diff)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| #1924 landed | `gh pr view 1930 --json state -q .state` | Headless cutover must be merged (returns `MERGED`) |
| Sentry read access | `python -c "import os; assert os.environ.get('SENTRY_DSN') or True"` | Telemetry grounding (advisory; MCP used at plan time) |

## Solution

### Key Elements

- **Removed-defenses ledger (`docs/removed-defenses.md`)**: the durable artifact.
  One entry per defense removed **in THIS PR** (plus a clearly-labeled reference
  block for the #1930-era classes that motivated the ledger): what it guarded
  against, why it's dead under headless, and the Sentry signature that would
  justify a targeted re-apply. See critique #5 note under Technical Approach.
- **Worker watchdog: confirmation, no deletion**: `monitoring/worker_watchdog.py`
  carries no PTY-specific narrative (verified: `grep -ni "pty|master fd"` == 0).
  Its U-state text is the generic issue-#1767 hung-worker rationale for the
  W1-W5 verified-kill ladder, which is substrate-agnostic and **KEPT unchanged**.
  This is a no-op/confirmation surface, not a trim. No ledger entry originates here.
- **Stall-classifier taxonomy prune**: reduce `session_stall_classifier.py` to the
  classes actually observed post-cutover (never-started, idle/turn-timeout);
  delete unobserved corroboration branches. Module stays pure read-only reporting.
- **Crash-signature library prune**: reduce `crash_signature.py` normalization to
  the event shapes that occur under headless (`status_transition`,
  `idle_gap` bucketing, never-started); drop PTY-only signature classes.
  **KEEP carve-out (critique #3, corrected round-3):** the `ceiling` /
  `ceiling_timeout` signature class (`crash_signature.py` lines ~296-297, 329-330)
  is explicitly retained — but the rationale is corrected. It is NOT a "live
  headless" prefix: per the extractor's own docstring (`crash_signature.py:184-185`),
  nothing produces `startup_failure_kind` after the PTY teardown (#1924); the value
  survives only in pre-cutover rows. The class is kept for BACKWARD-COMPATIBLE
  classification of those pre-cutover records, not because headless execution still
  emits it. Do not prune it — historical rows still classify through it.
- **Residual liveness inference trim**: in `session_health.py`, remove any
  remaining duplicated multi-field liveness inference that `derive_sdk_ever_output`
  already centralizes (no new signal, no wider spread — a subtraction only).
- **Stall-recovery flag deletion (closes #1855)**: remove
  `stall_recovery_enabled` from `config/settings.py`, delete the sole read site's
  dry-run branch (`reflections/stall_advisory.py:312-331`, step 6) so kill+catchup
  actuation is unconditional, drop `FEATURES__STALL_RECOVERY_ENABLED=false` from
  `.env.example`. Budgets/guards untouched and hold conservative production values
  (consec=3, run_budget=1, per_session=2). **This makes stall-recovery kill+catchup
  actuate LIVE for the first time** (PR #1773 shipped it default-False/dry-run) — see
  Risk 4 for the monitoring signal. To keep a no-deploy kill-switch, also relax
  `stall_recovery_run_budget` to `ge=0` so `FEATURES__STALL_RECOVERY_RUN_BUDGET=0`
  disables actuation (break-glass; Risk 4). Ledger entry: the dry-run gate was
  PTY-era caution scaffolding; the guard that matters is the budget ladder, kept.
- **Slot-lease reap transient-error fix (closes #1868)**: in
  `agent/session_health.py::_reap_slot_leases` **Phase 2 (~lines 2867-2895; the
  reclaim at 2874-2877)** — the AUTONOMOUS reaper, NOT `_drain_reclaim_requests`
  (which already handles this) — reclaim a lease only when the owner is confirmed
  absent/terminal, never on a transient lookup error. Because the existing
  `get_by_id` (`models/agent_session.py:1068`) CATCHES its own lookup exception and
  returns a plain `None`, a call-site `try/except` around it is inert — no exception
  escapes, so an `_ABSENT` sentinel can never be set (the stale-check map at lines
  2852-2865 has this exact dead branch today). The fix must move the error-vs-absent
  distinction INTO the lookup: add a raising sibling
  `AgentSession.get_by_id_strict(id)` in `models/agent_session.py` (same body as
  `get_by_id` — the input guard and the `len(results) > 1` warning — but WITHOUT the
  `except Exception: return None` swallow, so a `cls.query.filter` lookup error
  propagates; a clean not-found still returns `None`). Call `get_by_id_strict` from
  Phase 2 and reclaim only on a confirmed-`None`/terminal owner; on a raised lookup
  error, SKIP the reclaim so a live session's permit is never stripped by a read
  blip. Preserve the deliberate "genuinely-deleted record is reclaimable" path. Not
  a deletion — a targeted correctness fix on the recovery surface, so no ledger entry.
- **Keep decisions, documented**: `worker_watchdog.py` W1-W5 kill ladder + its
  U-state rationale KEPT (substrate-agnostic hung-worker recovery, issue #1767);
  `bridge_watchdog.py` 5-level ladder + revert-commit KEPT (bridge-process
  resilience, orthogonal to PTY); the 50-cap nudge counter KEPT as a runaway
  backstop (recommendation below); the `ceiling`/`ceiling_timeout` crash-signature
  class KEPT (backward-compatible classification of pre-cutover rows; nothing
  produces `startup_failure_kind` post-#1924 — see `crash_signature.py:184-185`).

### Flow

Deletion PR journey:
`main (headless, post-#1930)` → confirm watchdog clean + prune taxonomies →
delete stall-recovery flag (always-on) + fix slot-lease reap → write ledger entry
per deletion → keep-decisions documented → narrow tests green → cruft audit
confirms no half-migrations → merge.

**Separate commits within the one PR (concern #3).** The three folded-in concerns
have DIFFERENT rollback profiles, so land each as its own commit inside this single
PR (all three are `Depends On: none`, so this ordering is free — no rebase cost):
1. **Taxonomy/ledger prune** (pure reporting-layer subtraction + new doc) — lowest
   risk; a bad prune re-classifies, never crashes.
2. **#1855 flag flip** (`FEATURES__STALL_RECOVERY_ENABLED` deletion + `ge=0`
   break-glass) — highest risk (first live actuation of kill+catchup); a targeted
   `git revert` of THIS commit restores dry-run without touching the other two.
3. **#1868 slot-lease reap fix** (autonomous-reaper correctness — the new raising
   `get_by_id_strict` lookup + its Phase-2 call site) — a targeted revert restores
   the prior reclaim logic independently. **This correctness fix is bundled into
   the scar-tissue-removal PR only for delivery efficiency (concern #1); it is fully
   splittable at ZERO sequencing cost (`Depends On: none`) and lands as its own
   commit — a reviewer can cherry-pick, revert, or split it out to a standalone PR
   without touching the taxonomy prune or the #1855 flag flip.**
Committing them separately means an operator can revert any ONE concern post-merge
without dragging the other two back.

**Implementation PR body must carry** `Closes #1926`, `Closes #1855`, and
`Closes #1868`, and note the overlap coordination with
`resilience-simplification-three-tier.md` so the program plan can subtract what
this PR ships.

### Technical Approach

- **Ledger-first discipline:** no deletion lands without its ledger entry in the
  same commit. The ledger is the compensating control for aggressive cutting.
- **Ledger seeds from THIS PR's diff (critique #5):** the ledger's primary entries
  are the taxonomy/signature classes THIS PR actually removes, sourced from this
  PR's own diff — one entry per pruned class. The #1930-era classes (pty-pool,
  granite-container, deadman, executor-guard empty-container-message) were deleted
  by an already-shipped PR; they appear in the ledger only as a clearly-labeled
  "Baseline: removed by #1930 teardown" reference block for historical map-back,
  NOT counted as deletions this PR performs. Do not present already-shipped work
  as this PR's deletions.
- **Trim, don't gut, the reporting modules:** `session_stall_classifier` and
  `crash_signature` remain — they are already pure, side-effect-free, and import
  the authoritative liveness leaf. The change removes *enum members / verdict
  reasons / signature classes* that map to PTY-era events with zero post-cutover
  occurrences, plus their now-dead threshold constants. The `ceiling` /
  `ceiling_timeout` prefix is retained for backward-compatible classification
  of pre-cutover rows (critique #3, round-3 correction) — not because headless
  execution still emits it.
- **Watchdog: confirmation only, no edit.** `worker_watchdog.py` has no PTY-fd
  narrative to remove (verified 0 matches). Its U-state text is generic #1767
  hung-worker rationale for a KEPT mechanism; a headless `claude -p` subprocess can
  still block in uninterruptible sleep, so the rationale is accurate and stays.
  The watchdog surface is a no-op — verify no PTY-specific text exists and move on.
- **No parallel-run artifacts:** fully delete pruned classes; describe only the
  new status quo.

### Keep-vs-Cut Recommendations (issue-mandated, evidence-grounded)

**(1) 50-cap bridge nudge auto-continue counter (`MAX_NUDGE_COUNT`, `agent/output_router.py:39`) — RECOMMENDATION: KEEP (do not delete in this PR).**
Rationale: it is a happy-path *runaway backstop* (bounds infinite nudging so a
misbehaving session eventually delivers instead of looping), still wired live via
`session_executor.send_to_chat -> route_session_output`, and **orthogonal to PTY** —
it never read a PTY fd. Telemetry shows zero runaway-nudge Sentry issues in the
headless era, so there is no evidence justifying its removal, and deleting live
control-flow without evidence violates the "prune against evidence, not
predictions" mandate. The issue's phrase "the runner owns progression by
construction" is *aspirationally* true, but under the current headless
architecture the bridge nudge loop is still the mechanism that re-drives an idle
PM/eng session between turns — establishing that it is fully vestigial requires a
control-flow trace that is out of scope here. **Open decision flagged for the PM**
(see Open Questions): whether to schedule a *separate* investigation into
retiring the whole bridge nudge loop in favor of the steering list as the single
inbound channel. This PR keeps the counter and documents the reasoning.

**(2) worker_watchdog U-state kill ladder — RECOMMENDATION: KEEP unchanged (no narrative to trim).**
Corrected against `main` during this revision (critique #1): the premise that
`worker_watchdog.py` carries a "kill the PTY master fds" narrative is FALSE —
`grep -ni "pty|master fd" monitoring/worker_watchdog.py` returns 0. The only
U-state text (docstring lines ~10-21, 231-236; W4 log ~287) is the generic
issue-#1767 rationale for the W1-W5 verified-kill ladder: it explains why, against
a process wedged in uninterruptible sleep on a blocking syscall, SIGKILL queues and
the ladder escalates to bootout + a loud operator signal. That mechanism is
**substrate-agnostic** — a headless `claude -p` subprocess can wedge in U-state on
a hung fd/device exactly as any process can — so both the ladder AND its rationale
are KEPT verbatim. There is nothing PTY-specific to delete here. The VALOR-B7/B8
"kill the PTY master fds" telemetry that stopped at cutover was the *former PTY
pool's* fd-close path (deleted by #1930), not text in this watchdog. This surface
is a confirmation grep, not an edit.

**(3) stall classifier as pure reporting — RECOMMENDATION: KEEP as pure reporting, PRUNE taxonomy.**
Rationale: it is already zero-write and import-isolated from the kill/recovery
machinery (enforced by tests). It stays as the advisory/Sentry reporting layer.
Only its *taxonomy* is pruned to observed classes. Not ambiguous.

**Kept without change: `bridge_watchdog.py` 5-level escalation ladder + revert-commit.**
This supervises the *bridge process* (Telethon connectivity, hibernation,
auto-revert of a bad commit) — orthogonal to PTY and to session execution. No
change beyond a ledger note explaining why it is explicitly retained.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `session_stall_classifier.classify_session_stall` retains its fail-soft
  `except -> "healthy"` contract after the prune; test asserts a malformed event
  window still returns `healthy` (observable, not swallowed).
- [ ] `crash_signature.extract_signature` retains its `-> unclassifiable` fallback
  after the prune; test asserts an empty/garbage trace yields the sentinel.
- [ ] `worker_watchdog` kill-ladder branches are unchanged by this PR (no edit);
  their existing tests remain green as a regression guard, asserting the SIGKILL
  rung logs when SIGTERM fails to reap (no silent swallow).

### Empty/Invalid Input Handling
- [ ] Classifier + signature extractor tested with empty `events=[]`, `None`
  session, and whitespace/None fields — must not raise, must return the safe default.
- [ ] Confirm the nudge path's empty-output branch (`nudge_empty` /
  `deliver_fallback`) is unchanged by this PR (regression guard only — the counter
  is kept).

### Error State Rendering
- [ ] No new user-visible surface. The watchdog Sentry-report path is untouched
  and still fires on the kept kill-ladder rungs; its existing test asserting a
  Sentry capture (or the log the reporter reads) at the terminal rung stays green
  unedited.

## Test Impact

- [ ] `tests/**/test_session_stall_classifier*.py` — UPDATE: remove assertions on
  pruned verdict reasons / threshold constants; keep never-started + idle-timeout
  + fail-soft cases. (Exact file located at build time via
  `grep -rl session_stall_classifier tests/`.)
- [ ] `tests/**/test_crash_signature*.py` (e.g. `tests/unit/test_crash_signature_library.py`)
  — UPDATE: drop cases asserting PTY-only signature classes; keep
  `status_transition` / `idle_gap` bucketing / never-started / unclassifiable
  cases AND the `ceiling` / `ceiling_timeout` prefix cases (that class is kept —
  critique #3).
- [ ] `tests/**/test_worker_watchdog*.py` — VERIFY UNCHANGED (regression guard):
  the watchdog is not edited (no PTY narrative exists to remove), so these must
  continue to pass without edits, preserving the kill-ladder
  (SIGTERM->SIGKILL->bootout->respawn) assertions. If any edit is needed here, the
  scope correction is wrong — stop and escalate.
- [ ] `tests/**/test_output_router*.py` — VERIFY UNCHANGED (regression guard): the
  50-cap counter is kept, so these must continue to pass without edits. If any
  edit is needed, the keep-decision is wrong — stop and escalate.
- [ ] `tests/**/test_session_health*.py` — UPDATE only if residual-inference
  trimming touches a covered path; otherwise assert no behavior change. Do NOT
  touch `_confirm_subprocess_dead` tests (that bug is already fixed on main).
- [ ] Import-isolation guard tests (classifier/signature must not import
  `session_health`) — VERIFY UNCHANGED; the prune must not add the forbidden import.
- [ ] `tests/**/*stall_advisory*.py` — UPDATE (closes #1855): flip every dry-run
  assertion (`WOULD kill`, `dry_run` outcome, `stall_recovery_enabled=False`
  branch) to unconditional-actuation assertions. Any test that patches or sets
  `stall_recovery_enabled` must have that removed — the flag no longer exists.
- [ ] `tests/**/*stall_advisory*.py` — ADD (closes #1855, break-glass): a case
  asserting `stall_recovery_run_budget=0` short-circuits every candidate to
  `skipped_run_budget` and kills nothing (the remaining no-deploy kill-switch).
- [ ] `tests/**/*session_health*` reap tests — UPDATE/ADD (closes #1868): target
  the AUTONOMOUS `_reap_slot_leases` Phase-2 path (~2874-2877), NOT
  `_drain_reclaim_requests` (already correct). Add a case that mocks the UNDERLYING
  `AgentSession.query.filter` (`models/agent_session.py:~1089`) to RAISE — NOT
  `get_by_id`/`get_by_id_strict` themselves (mocking the helper would pass while the
  real path stays broken) — and asserts the raised lookup error does NOT reclaim the
  lease, while a confirmed-absent (clean `None`) or terminal-status owner still does.
  Existing reclaim-on-terminal cases stay.
- [ ] `tests/**/test_agent_session*.py` (the model's test module) — ADD (closes
  #1868): a unit test for the new `AgentSession.get_by_id_strict` — mock
  `cls.query.filter` to raise and assert it PROPAGATES (does not swallow to `None`),
  and assert a clean not-found still returns `None`.

Exact test files and cases are enumerated by the builder from `grep` at build
start; the dispositions above are binding.

## Rabbit Holes

- **Rewriting the liveness model from scratch.** It already exists
  (`derive_sdk_ever_output`, owner-directed, #1843/#2004). This plan *trims residual
  duplication*, it does not re-architect liveness. Do not spread inference wider.
- **Deleting the whole bridge nudge loop.** Tempting given "the runner owns
  progression," but establishing vestigiality needs a control-flow trace and
  risks removing live progression control with no telemetry backing. Out of scope;
  flagged as an open decision for a separate investigation.
- **Merging into the resilience-simplification three-tier program.** That is a
  broader program plan; #1926 is a focused slice. Coordinate, don't absorb.
- **"Consolidate toward one supervisor per process" as a rewrite.** The issue
  says *toward*; the watchdogs already map one-per-process (bridge/worker/session
  cover distinct processes). Do not collapse them into a single mega-watchdog —
  that is a re-architecture, not scar-tissue removal.
- **Re-fixing VALOR-E1/D0.** Already fixed by `b92d9a44`. Touching it is building
  for a stale problem.

## Risks

### Risk 1: Pruning a taxonomy class that DOES occur under headless (just rarely).
**Impact:** A real (rare) failure becomes unclassified/unreported.
**Mitigation:** Prune only classes with zero post-cutover Sentry occurrences. Note
(concern #4) the honest evidence window is the post-#1930-cutover period, which is
only ~4-5 days of headless telemetry (the #1930 teardown merged 2026-07-07; plan
authored 2026-07-11), NOT a full 14 days — the 14-day Sentry query spans the
cutover, but the headless-clean signal within it is ~4-5 days. That window is short
but decisive: every PTY-era class stopped firing sharply at the cutover boundary
and none has recurred since. Each pruned class gets a ledger entry naming the exact
Sentry signature to watch, and the reporting modules keep their
`unclassifiable`/`healthy` fallbacks, so an unforeseen shape observed AFTER the
window degrades to a safe default plus a generic Sentry event rather than a crash —
the residual risk of the short window is a re-classify, not a crash.

### Risk 4: Deleting the #1855 flag fires stall-recovery kill+catchup live for the FIRST time.
**Impact:** PR #1773 shipped stall recovery default-False / dry-run: kill +
valor-catchup re-enqueue has NEVER actuated in production. Removing the flag makes
the FIRST live actuation happen at merge — a wrongly-classified live session could
be killed and its work re-enqueued when it should have been left alone.
**Mitigation:** The real safety mechanism is the budget ladder, which stays and
holds CONSERVATIVE (not test-only) values, verified against `config/settings.py`:
`stall_recovery_consecutive_observations=3` (≈15 min of sustained stall at the 300s
cadence before any kill), `stall_recovery_run_budget=1` (at most ONE kill per
reflection run), `stall_recovery_per_session_budget=2` (anti-thrash cap), plus the
step-7 Race-1 status re-read that skips a session that finalized between
classification and kill. **Post-merge monitoring signal:** watch the
`session_events` recovery-audit stream and the `[stall-recovery]` logs for the
FIRST real (non-dry-run) `killed=True` / `catchup_invoked=True` event; the
dashboard's recovery panel surfaces `_emit_recovery_event` records. If the first
live actuation kills a session that was actually healthy, pull the break-glass
below. Also watch Sentry for a post-merge spike in `StatusConflictError`
(VALOR-DZ) or exit-143 events correlated with recovery actuation.
**Post-merge validation rule (concern #2 — signal / window / owner):** within 48
hours of merge, the shipping engineer (Valor Engels, else the on-call bridge
operator) inspects the `session_events` recovery-audit stream and the
`[stall-recovery]` worker logs for the FIRST `killed=True` actuation. VALIDATION
PASSES when, for each such event, the killed session's last classification carried
`consecutive_observations >= 3` AND a `catchup_invoked=True` re-enqueue landed (work
preserved). VALIDATION FAILS — pull `FEATURES__STALL_RECOVERY_RUN_BUDGET=0`
immediately — if any killed session had a fresh heartbeat (`last_stdout_at` within
its turn-timeout window) at kill time, i.e. a healthy session was reaped. If NO
actuation fires within 48h, extend the watch through the first weekly reflection
cycle; sustained non-actuation is a PASS (dry-run parity). Record the outcome in the
#1855 tracking-issue thread so the first-actuation result is auditable.
**Break-glass (concern #5 NIT):** removing the flag deletes the only no-deploy
kill-switch. To preserve one, this PR relaxes `stall_recovery_run_budget` from
`ge=1` to `ge=0` (mirroring `crash_autoresume_*`'s "Set to 0 to disable" precedent,
`config/settings.py:289-300`) and documents that `FEATURES__STALL_RECOVERY_RUN_BUDGET=0`
is the remaining break-glass: the existing run-budget check
(`reflections/stall_advisory.py:289`, `run_state["killed"] >= budget`) already
short-circuits every candidate to `skipped_run_budget` when the budget is 0, since
`killed` starts at 0 and `0 >= 0` is true. That lever is a per-machine `.env` edit,
no deploy required.

### Risk 2: Wrongly editing worker_watchdog under the old (false) premise.
**Impact:** Deleting a live kill-ladder rung or its accurate U-state rationale,
weakening hung-worker recovery.
**Mitigation:** The watchdog is confirmed to hold no PTY-specific text, so this PR
makes ZERO watchdog edits. `test_worker_watchdog*` must pass unedited as a
regression guard; any required edit there means the scope correction is wrong and
the builder stops and escalates.

### Risk 3: The kept 50-cap counter turns out to be genuinely vestigial.
**Impact:** Dead code remains (violates NO_LEGACY_CODE if truly unreachable).
**Mitigation:** It is demonstrably reachable today (`send_to_chat` call path).
"Kept and documented with rationale" is a deliberate scope decision, not
legacy tolerance; the open-decision flag routes the vestigiality question to a
separate, evidence-driven follow-up rather than a blind delete now.

## Race Conditions

No new race conditions introduced. The taxonomy/rationale work is subtractive
(removing taxonomy members and dead branches) plus one additive doc. The kept kill
ladder's existing PID-reuse caveat and `run_in_executor` offload (already
documented in `_confirm_subprocess_dead`) are unchanged, and the reporting modules
are already zero-write and race-free by construction.

The #1868 slot-lease reap fix *closes* an existing race window rather than opening
one: today, in the AUTONOMOUS `_reap_slot_leases` Phase 2 (~2874-2877), a transient
read blip during a 300s reap tick makes the exception-swallowing `get_by_id` return
`None`, which is treated as terminal, reclaiming a live session's permit (semaphore
over-admission). The fix routes Phase 2 through the new raising `get_by_id_strict`,
reclaims only on a confirmed-absent (`None` from a clean lookup) or terminal owner,
and skips reclaim when the lookup RAISES, tightening the guard. (The request-driven
`_drain_reclaim_requests` path already had this guard — the fix is confined to the
autonomous reaper.) The #1855 flag deletion changes no timing — it
removes a boolean branch; the consec-observation counter, kill budgets, and Race-1
re-read guard that order the actuation are all preserved.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1926] Retiring the entire bridge nudge loop / making the
  steering list the single inbound channel — flagged as an open decision requiring
  a control-flow trace; belongs to its own investigation, not this scar-tissue PR.
  (Tracked as an Open Question against this issue; if the PM greenlights, it gets
  its own issue before any code moves.)
- [SEPARATE-SLUG #1926] The `resilience-simplification-three-tier` program's Tier
  1-3 items (event-sourced stage log, degradation contract, unified evidence
  model) — separate program plan.
- Re-fixing `_confirm_subprocess_dead` VALOR-E1/D0 — already fixed on `main`
  (`b92d9a44`); nothing to do.

## Update System

No migration or `scripts/update/run.py` changes required — no new dependencies,
config files, or Popoto model field/schema changes (the kept `auto_continue_count`
field is untouched). The #1868 fix adds a behavior-only classmethod
(`AgentSession.get_by_id_strict`) — no new field, no index change — so no
`migrations.py` entry is required. The `/update` skill propagates the code changes
automatically on the next run; the new `docs/removed-defenses.md` ships as an
ordinary tracked file.

One env-var cleanup note (closes #1855): `FEATURES__STALL_RECOVERY_ENABLED` is
removed from `.env.example` and from `config/settings.py`. Machines that set it in
their per-machine `~/Desktop/Valor/.env` should delete the line. Leaving it set is
harmless (pydantic-settings ignores unknown `FEATURES__*` keys), but per the
no-legacy policy the docs and example are updated so no machine re-adds it. No
automated migration is warranted for a harmless stale env line.

**Operator note — first live actuation + break-glass (Risk 4):** removing the flag
makes stall-recovery kill+catchup actuate live for the first time (it was
dry-run-only under PR #1773). The `/update` rollout should call this out to bridge
operators. The remaining no-deploy kill-switch is
`FEATURES__STALL_RECOVERY_RUN_BUDGET=0` (this PR relaxes the field to `ge=0`); set
it in a machine's `~/Desktop/Valor/.env` to disable actuation without a deploy.

## Agent Integration

No agent integration required — this is a bridge/worker-internal change. No new
CLI entry point in `pyproject.toml [project.scripts]`, no new MCP surface in
`mcp_servers/` or `.mcp.json`, and the bridge (`bridge/telegram_bridge.py`) needs
no new import. The kept nudge counter and watchdog kill ladder are already wired;
this plan only trims dead rationale around them. Existing integration tests that
exercise the session lifecycle and the nudge/deliver path serve as the regression
surface.

## Documentation

### Feature Documentation
- [ ] Create `docs/removed-defenses.md` — the removed-defenses ledger. One entry
  per deletion: (a) the defense, (b) the gotcha it guarded against, (c) why it is
  dead under headless, (d) the Sentry signature that would justify a targeted
  re-apply. This is the load-bearing deliverable.
- [ ] Update `docs/features/bridge-worker-architecture.md` — reflect the headless
  liveness status quo and the pruned reporting taxonomies (describe only the new
  status quo; no PTY archaeology in the doc body — that lives in the ledger). Note
  the worker_watchdog kill ladder is unchanged.
- [ ] Update `docs/features/bridge-self-healing.md` — re-state the watchdog fleet
  section: bridge 5-level ladder KEPT, worker W1-W5 kill ladder KEPT (unchanged,
  substrate-agnostic). No watchdog narrative is removed by this PR.
- [ ] Add a `docs/removed-defenses.md` entry to `docs/features/README.md` index
  table (or the appropriate index) so the ledger is discoverable.
- [ ] Update the stall-advisory feature doc (`docs/features/` coverage of
  `reflections/stall_advisory.py`, e.g. the resilience/stall-recovery doc) to
  describe recovery as always-on: remove the `FEATURES__STALL_RECOVERY_ENABLED`
  dry-run description; the budgets/guards are the safety mechanism (closes #1855).
  Document that recovery now actuates live (first time — Risk 4) and that
  `FEATURES__STALL_RECOVERY_RUN_BUDGET=0` is the no-deploy break-glass kill-switch.

### External Documentation Site
- No external docs site changes (internal system docs only).

### Inline Documentation
- [ ] `monitoring/worker_watchdog.py` — no change (its U-state text is generic
  #1767 rationale for a KEPT mechanism, not PTY archaeology). Confirm only.
- [ ] Prune stale threshold-constant comments in `session_stall_classifier.py` /
  `crash_signature.py` for any removed class; leave the `ceiling` prefix comments.

## Success Criteria

- [ ] `docs/removed-defenses.md` exists with one entry per deletion, each naming
  the guarded gotcha + the Sentry signature to watch for re-apply.
- [ ] `monitoring/worker_watchdog.py` contains no PTY-master-fd narrative
  (`grep -ni "pty|master fd"` == 0, already true); the W1-W5 kill ladder and its
  generic #1767 U-state rationale remain unchanged and its tests pass unedited.
- [ ] `session_stall_classifier.py` and `crash_signature.py` enumerate only
  post-cutover-observed classes (retaining the kept `ceiling`/`ceiling_timeout`
  prefix); both remain pure (zero writes) and neither has a real
  `from agent.session_health` / `import session_health` statement (isolation guard
  still green — anchored grep, see Verification).
- [ ] `agent/output_router.py` `MAX_NUDGE_COUNT` and `AgentSession.auto_continue_count`
  are unchanged (kept-with-rationale); `test_output_router*` passes without edits.
- [ ] `FEATURES__STALL_RECOVERY_ENABLED` / `stall_recovery_enabled` removed from
  `config/settings.py`, `reflections/stall_advisory.py`, and `.env.example`; stall
  recovery actuates unconditionally; stall-advisory tests green (closes #1855).
- [ ] Break-glass preserved: `stall_recovery_run_budget` relaxed to `ge=0` and
  `FEATURES__STALL_RECOVERY_RUN_BUDGET=0` disables actuation (test asserts budget=0
  kills nothing); budgets otherwise hold conservative values (consec=3,
  run_budget=1, per_session=2).
- [ ] `AgentSession.get_by_id_strict` exists in `models/agent_session.py` and lets a
  `cls.query.filter` lookup error PROPAGATE (a clean not-found still returns `None`);
  `_reap_slot_leases` Phase 2 (the AUTONOMOUS reaper at ~2874-2877, NOT
  `_drain_reclaim_requests`) calls it and reclaims only on a confirmed-absent/terminal
  owner, never on a raised lookup error; a regression test that mocks the underlying
  `cls.query.filter` to raise proves the live path is not reclaimed (closes #1868).
- [ ] No commented-out code, no "previously PTY" archaeology in code bodies, no
  parallel-run artifacts (cruft audit clean).
- [ ] Narrow-scope tests pass (`/do-test` on the touched files).
- [ ] Documentation updated (`/do-docs`).
- [ ] `grep -rniE "pty master|master fd" monitoring/ agent/` returns no matches
  (no PTY-fd archaeology in code bodies). Note: `uninterruptible sleep` is
  deliberately NOT in this grep — it is kept #1767 rationale for the retained
  worker_watchdog ladder.

## Team Orchestration

Right-sized for a small diff (critique #4): a single dev does the code prune and
doc work in-place on the base branch — no worktree fan-out, no watchdog builder
(that surface is a confirmation grep, not an edit). One reviewer closes the loop.

### Team Members

- **Builder (taxonomy-prune + ledger + docs)**
  - Name: `scar-builder`
  - Role: Prune `session_stall_classifier.py` + `crash_signature.py` to observed classes (KEEP the `ceiling`/`ceiling_timeout` prefix); subtraction-only residual liveness-inference trim in `session_health.py` if any remains; update the two affected test files. Delete the `FEATURES__STALL_RECOVERY_ENABLED` flag making stall recovery always-on and relax `stall_recovery_run_budget` to `ge=0` as the break-glass (closes #1855). Fix the slot-lease reap transient-error reclaim in the AUTONOMOUS `_reap_slot_leases` Phase 2 at ~2874-2877 — NOT `_drain_reclaim_requests`, which is already correct — by adding a raising `AgentSession.get_by_id_strict` lookup and calling it from Phase 2 (a call-site try/except around the exception-swallowing `get_by_id` is inert), reclaiming only on confirmed-absent/terminal and skipping on a raised lookup error (closes #1868). Author `docs/removed-defenses.md` and update `bridge-worker-architecture.md`, `bridge-self-healing.md`, feature index. Confirm (grep) the watchdog carries no PTY narrative — no watchdog edit. Land the three concerns as separate commits within the one PR.
  - Agent Type: builder
  - Resume: true

- **Reviewer**
  - Name: `scar-reviewer`
  - Role: Cruft audit (no half-migrations / commented code / PTY archaeology) + correctness on the deletion diff; confirm `test_output_router*` and `test_worker_watchdog*` pass UNEDITED.
  - Agent Type: code-reviewer
  - Resume: true

### Available Agent Types

Tier 1 `builder` + `code-reviewer` (+ `cruft-auditor` for the deletion diff).

## Step by Step Tasks

### 1. Confirm watchdog carries no PTY narrative (no edit)
- **Task ID**: confirm-watchdog-clean
- **Depends On**: none
- **Validates**: `grep -niE "pty master|master fd" monitoring/worker_watchdog.py` returns nothing (already true)
- **Assigned To**: scar-builder
- **Agent Type**: builder
- **Parallel**: false
- Confirmation only. `monitoring/worker_watchdog.py` has no PTY-fd narrative; its
  U-state text is generic #1767 rationale for the KEPT W1-W5 kill ladder. Make NO
  edit to the watchdog and NO ledger entry from it. If a PTY-specific string is
  found, stop and escalate — the scope correction would be wrong.

### 2. Prune reporting taxonomies + residual liveness inference
- **Task ID**: build-taxonomy-prune
- **Depends On**: none
- **Validates**: `tests/**/test_session_stall_classifier*.py`, `tests/unit/test_crash_signature_library.py`, anchored isolation grep (Verification table)
- **Assigned To**: scar-builder
- **Agent Type**: builder
- **Parallel**: false
- Prune `session_stall_classifier.py` + `crash_signature.py` to observed classes;
  remove dead threshold constants; **KEEP the `ceiling`/`ceiling_timeout` prefix
  (lines ~296-297, 329-330) — retained for backward-compatible classification
  of pre-cutover rows (round-3 correction), not scar tissue.** Trim residual
  duplicate liveness inference in `session_health.py` only if any remains
  (subtraction only, no wider spread). Update the two affected test files.

### 3. Author the removed-defenses ledger + docs
- **Task ID**: build-ledger-docs
- **Depends On**: build-taxonomy-prune
- **Validates**: `test -f docs/removed-defenses.md`
- **Assigned To**: scar-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `docs/removed-defenses.md` with the entry template (defense / gotcha /
  why-dead-under-headless / Sentry-signature-to-watch). Primary entries = the
  taxonomy/signature classes THIS PR removed (seed from this PR's own diff —
  critique #5). Add a clearly-labeled "Baseline: removed by #1930 teardown"
  reference block for pty-pool / granite-container / deadman / executor-guard
  empty-container-message — historical map-back, NOT this PR's deletions. Update
  `bridge-worker-architecture.md`, `bridge-self-healing.md`, and the feature index.

### 4. Delete FEATURES__STALL_RECOVERY_ENABLED flag (closes #1855)
- **Task ID**: build-stall-flag-delete
- **Depends On**: none
- **Validates**: `grep -rn "stall_recovery_enabled\|STALL_RECOVERY_ENABLED" config/ reflections/ .env.example` returns nothing; `tests/**/*stall_advisory*` green
- **Assigned To**: scar-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove the `stall_recovery_enabled` Field from `config/settings.py` (lines
  ~310-319); delete the step-6 dry-run branch at `reflections/stall_advisory.py:312-331`
  (the `if not feat.stall_recovery_enabled:` block) so actuation is unconditional;
  update the `_maybe_recover` docstring (drop the "flag off (default) -> dry_run"
  rung and the module-header flag mention); remove
  `FEATURES__STALL_RECOVERY_ENABLED=false` from `.env.example`. Keep all budgets/
  guards. Flip dry-run test assertions to actuation assertions.
- **Break-glass (concern #5):** relax `stall_recovery_run_budget` from `ge=1` to
  `ge=0` in `config/settings.py:331-340` and update its description to note "Set to
  0 to disable actuation entirely" (mirroring `crash_autoresume_*` at
  `config/settings.py:289-300`). No read-site change needed: the existing run-budget
  gate at `reflections/stall_advisory.py:289` (`run_state["killed"] >= budget`)
  already short-circuits all candidates to `skipped_run_budget` at budget 0. This
  makes `FEATURES__STALL_RECOVERY_RUN_BUDGET=0` the remaining no-deploy kill-switch
  now that the enabled flag is gone. Add a test asserting budget=0 kills nothing.
- Land this whole #1855 change as its OWN commit within the PR (flag flip has a
  distinct rollback profile — see Flow). Add a ledger entry.

### 5. Fix slot-lease reap transient-error reclaim (closes #1868)
- **Task ID**: build-slot-lease-fix
- **Depends On**: none
- **Validates**: `tests/**/*session_health*` / reap tests green; a new test asserts a lookup-error owner is NOT reclaimed
- **Assigned To**: scar-builder
- **Agent Type**: builder
- **Parallel**: false
- In `agent/session_health.py::_reap_slot_leases` **Phase 2 (~lines 2867-2895; the
  reclaim decision at 2874-2877** — `if fresh is None or getattr(fresh, "status",
  None) in _TERMINAL_STATUSES: registry.reclaim(...)`), reclaim only on a
  confirmed-absent/terminal owner; never on a transient lookup error.
  **Do NOT edit `_drain_reclaim_requests` (~line 3025): it already carries the
  `#1868 trap` fix — the buggy autonomous reaper is the one at ~2874-2877.**
  **A call-site try/except around `get_by_id` will NOT work** — `get_by_id`
  (`models/agent_session.py:1068`) catches its own lookup exception at lines
  1090-1096 and returns a plain `None`, so nothing escapes to a wrapper and an
  `_ABSENT` sentinel can never be set (the stale-check map at 2852-2865 carries this
  dead branch today; do not copy it).
  **Required fix — raising lookup:** add `AgentSession.get_by_id_strict(id)` to
  `models/agent_session.py` — identical to `get_by_id` (keep the input guard and the
  `len(results) > 1` warning) but WITHOUT the `except Exception: return None` swallow,
  so a `cls.query.filter` lookup error propagates while a clean not-found still
  returns `None`. Call `get_by_id_strict` from the Phase-2 loop; reclaim only on a
  confirmed-`None`/terminal owner and let a raised lookup error SKIP the reclaim
  (the surrounding try/except logs-and-continues, it must NOT reclaim). Preserve the
  deliberate "genuinely-deleted record is reclaimable" path. Land this as its OWN
  commit within the PR (separate rollback profile — see Flow). Add a regression test
  that mocks the UNDERLYING `AgentSession.query.filter` (`models/agent_session.py:~1089`)
  to raise — NOT `get_by_id`/`get_by_id_strict` themselves (mocking the helper would
  pass while the real path stays broken) — asserting a raised lookup error does NOT
  reclaim, while a clean `None` (not-found) or terminal-status owner still does.

### 6. Cruft audit + final review
- **Task ID**: validate-all
- **Depends On**: build-ledger-docs, build-stall-flag-delete, build-slot-lease-fix
- **Assigned To**: scar-reviewer
- **Agent Type**: code-reviewer
- **Parallel**: false
- Run only the touched test files. Confirm `test_output_router*` AND
  `test_worker_watchdog*` pass UNEDITED (keep-decision guards). Confirm anchored
  isolation grep green, `ceiling_timeout` retained, and no residual
  `stall_recovery_enabled` reference. No commented-out code, no PTY archaeology in
  code bodies, no half-migrations. Verify all success criteria + the anti-criteria
  greps.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Ledger exists | `test -f docs/removed-defenses.md && echo ok` | output contains ok |
| No PTY-fd archaeology in code | PTY-archaeology grep — see code block below (V1) | match count == 0 |
| Nudge counter kept | `grep -c "MAX_NUDGE_COUNT = 50" agent/output_router.py` | output contains 1 |
| `ceiling` signature class kept | `grep -c "ceiling_timeout" agent/crash_signature.py` | output >= 1 |
| Classifier stays pure (no real session_health import) | isolation-guard grep — see code block below (V2) | match count == 0 |
| Stall flag deleted (#1855) | `grep -rn "stall_recovery_enabled\|STALL_RECOVERY_ENABLED" config/ reflections/ .env.example` | match count == 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Narrow tests pass | `pytest tests/ -k "worker_watchdog or session_stall_classifier or crash_signature or output_router or stall_advisory or session_health" -q` | exit code 0 |
| Stall break-glass lever intact (#1855) | `grep -n "ge=0" config/settings.py` shows `stall_recovery_run_budget`; a run with `FEATURES__STALL_RECOVERY_RUN_BUDGET=0` set logs `run budget exhausted` and kills nothing | budget=0 disables actuation |

**Pipe-bearing grep commands (V1, V2) — run verbatim.** These use `grep -E`
(extended regex), where the alternation operator is a BARE `|`. An escaped `\|`
under `-E` is a LITERAL pipe character and would make the check a silent no-op that
always passes (concern #1). They live in this code block rather than the table
because a bare `|` inside a markdown table cell breaks the table:

```bash
# V1 — No PTY-fd archaeology in code bodies (expect: match count == 0)
grep -rniE "pty master|master fd" monitoring/ agent/

# V2 — Classifier/signature stay pure: no REAL session_health import (expect: 0).
# Anchored to real import statements so the plain substring does not self-match
# the classifier's isolation docstring. The pipe is a real alternation (no backslash).
grep -cE "^[[:space:]]*(from agent\.session_health|import session_health)" \
  agent/session_stall_classifier.py agent/crash_signature.py
```

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | war-room | Watchdog PTY-narrative trim targets nonexistent code; success-criterion grep for "uninterruptible sleep" strips rationale for a KEPT mechanism | Re-scoped watchdog to a confirmation no-op | Verified `grep -ni "pty\|master fd" monitoring/worker_watchdog.py` == 0; the only U-state text is generic #1767 rationale for the kept W1-W5 ladder. Greps now check only `pty master\|master fd`; "uninterruptible sleep" dropped. Task 1, Solution, Keep-vs-Cut (2), Success Criteria, Verification, Test Impact, Risk 2, Docs all updated. |
| BLOCKER | war-room | Isolation-guard grep self-matches classifier docstring → false merge-gate failure | Anchored the grep to real import statements | Verification now uses `grep -cE "^[[:space:]]*(from agent\.session_health\|import session_health)"`, verified == 0 against current code. |
| CONCERN | war-room | `ceiling`/`ceiling_timeout` crash-signature class is deliberately kept | Explicit KEEP carve-out | Called out in Solution, Task 2, Test Impact, Success Criteria, and a new Verification row `grep -c "ceiling_timeout"` >= 1. |
| CONCERN | war-room | Large appetite + four-builder team over-sizes a small deletion surface | Right-sized to Small, solo builder + reviewer | Appetite Small; Team Orchestration collapsed to one `scar-builder` + one reviewer; no worktree fan-out. |
| CONCERN | war-room | Ledger should seed from THIS PR's diff, not #1930's shipped work | Ledger-seed discipline documented | Technical Approach + Task 3: primary entries from this PR's own diff; #1930 classes only as a labeled "Baseline: removed by #1930" reference block. |

### Re-critique round 2 (2026-07-11)

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | war-room | #1868 fix misdirected: plan cited `_reap_slot_leases` "Phase 2 (~3046-3073)", but those lines are inside `_drain_reclaim_requests` (~3025) which ALREADY implements the fix (`#1868 trap` docstring). The REAL bug is the autonomous reap at ~2874-2877. | Corrected the citation to the autonomous reaper | Verified against `main`: the buggy reclaim is `_reap_slot_leases` Phase 2, `if fresh is None or getattr(fresh, "status", None) in _TERMINAL_STATUSES: registry.reclaim(...)` at lines 2874-2877; `_drain_reclaim_requests` (3025+) already skips on None/lookup-error. `get_by_id` (`models/agent_session.py:1068`) collapses swallowed-exception and genuine-not-found both to `None`, so the fix adds an `_ABSENT` sentinel (mirroring the reap-local stale-check map at 2852-2865) and reclaims only on confirmed-`None`/terminal. Fixed in Problem, Solution, Task 5, Test Impact, Success Criteria. |
| CONCERN | war-room | Isolation-guard grep is a silent no-op: escaped `\|` under `grep -E` is a LITERAL pipe, so the gate always passes | Moved V1/V2 greps to a fenced code block with a REAL bare-pipe alternation | Verification table now points to a code block below it (bare `|` breaks a markdown table cell); the isolation grep is `grep -cE "^[[:space:]]*(from agent\.session_health\|import session_health)"` with a REAL pipe (the raw markdown shows a bare `\|`-free alternation in the code fence). Added a grep-syntax note. |
| CONCERN | war-room | Removing the #1855 flag fires stall-recovery kill+catchup live for the FIRST time (was default-False/dry-run) | Added Risk 4 + post-merge monitoring signal + confirmed conservative budgets | Risk 4 names the `session_events` recovery-audit stream / `[stall-recovery]` logs / dashboard recovery panel to watch, plus Sentry VALOR-DZ + exit-143 correlation. Confirmed budgets hold conservative production values (consec=3, run_budget=1, per_session=2), not test-only. |
| CONCERN | war-room | Three folded concerns have different rollback profiles; land as separate commits | Separate-commits guidance added to Flow + Tasks 4/5 | Flow now lists the 3-commit ordering (prune / #1855 / #1868), all `Depends On: none` so free; Tasks 4 and 5 each say "land as its OWN commit". |
| CONCERN | war-room | Risk 1 mitigation overstates the evidence window ("zero over 14 days" is ~4-5 days post-#1930) | Restated the window honestly | Risk 1 now says the headless-clean signal is ~4-5 days (cutover 2026-07-07, plan 2026-07-11), short but decisive; the 14-day Sentry query spans the cutover. |
| NIT | war-room | Removing the flag deletes the only no-deploy break-glass | Documented `FEATURES__STALL_RECOVERY_RUN_BUDGET=0` lever + made it real | This PR relaxes `stall_recovery_run_budget` from `ge=1` to `ge=0` (mirroring `crash_autoresume_*` "Set to 0 to disable" at `config/settings.py:289-300`); the existing run-budget gate (`stall_advisory.py:289`, `run_state["killed"] >= budget`) short-circuits to `skipped_run_budget` at 0. Documented in Risk 4, Solution, Task 4, Update System operator note, Success Criteria, new Verification row + test. |

### Re-critique round 3 (2026-07-11)

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | war-room | The prescribed #1868 fix is INERT: wrapping the Phase-2 `get_by_id` call in try/except to store `_ABSENT` can never fire because `get_by_id` (`models/agent_session.py:1090-1096`) catches its OWN lookup exception and returns a plain `None`. No exception escapes to a call-site wrapper, so every `None` (blip or not-found) still hits the reclaim branch — the code would look fixed but behave identically to the bug. The cited precedent (stale-check map at 2852-2865) inherits the same defect: its `_ABSENT` branch is already unreachable. | Relocated the error-vs-absent distinction INTO the lookup | Verified against `main`: `get_by_id` swallows at 1090-1096 and returns `None`; the reap Phase-2 call at 2874 sits inside a try/except that only ever catches `registry.reclaim`. Fix now prescribes a raising sibling `AgentSession.get_by_id_strict(id)` (same body as `get_by_id` minus the `except Exception: return None` swallow — lookup error propagates, clean not-found still `None`), called from Phase 2; reclaim only on confirmed-`None`/terminal, SKIP on a raised error. Regression test must mock the UNDERLYING `cls.query.filter` (~1089) to raise, NOT `get_by_id`/`get_by_id_strict` (mocking the helper passes while the real path stays broken). Fixed in Problem, Solution, Task 5, Test Impact, Success Criteria, Race Conditions, Team Orchestration, Architectural Impact, Update System. |
| CONCERN | war-room | The #1868 correctness fix is bundled into a scar-tissue-removal PR | Reaffirmed separate-commits guidance | Flow item 3 now states the fix is bundled only for delivery efficiency, is fully splittable at zero sequencing cost (`Depends On: none`), lands as its own commit, and can be cherry-picked/reverted/split to a standalone PR without touching the prune or the #1855 flip. |
| CONCERN | war-room | First live actuation of stall-recovery lacks a concrete post-merge validation RULE/owner/window | Added a specific validation step | Risk 4 now carries a "Post-merge validation rule": signal = `session_events` recovery-audit stream + `[stall-recovery]` logs; window = 48h post-merge, extended through the first weekly reflection cycle if no actuation; owner = shipping engineer (else on-call bridge operator); PASS/FAIL rule keyed on `consecutive_observations >= 3` + `catchup_invoked=True` vs. a fresh-heartbeat kill; outcome recorded in the #1855 issue thread. |
| CONCERN | war-room | `ceiling`/`ceiling_timeout` KEEP justification ("live headless classification prefix") contradicts `crash_signature.py:184-185`, which says nothing produces `startup_failure_kind` post-teardown | Reclassified the KEEP rationale | Verified `crash_signature.py:184-185` ("nothing produces `startup_failure_kind` after the PTY teardown, plan #1924; the value stays valid in old rows"). KEEP rationale now reads "kept for backward-compatible classification of pre-cutover rows," corrected in the Solution carve-out and the Keep-vs-Cut summary. |

---

## Open Questions

1. **Bridge nudge loop retirement (control-channel consolidation).** The issue
   lists the 50-cap counter as a cut candidate and wants the steering list as the
   single inbound channel. My evidence-grounded recommendation is to KEEP the
   counter as a runaway backstop in this PR and spin the "is the whole nudge loop
   vestigial under headless?" question into a separate, control-flow-traced
   investigation. **Do you approve keeping it here and filing the vestigiality
   question separately, or do you want the nudge-loop retirement folded into this
   PR?** (If folded in, appetite grows and this becomes a live-control-flow change,
   not just scar-tissue removal.)
2. **Overlap coordination with `resilience-simplification-three-tier.md`.** That
   draft program touches `session_health.py` too. Confirm #1926 should stay the
   narrow PTY-scar slice and leave the three-tier items to the program plan.
