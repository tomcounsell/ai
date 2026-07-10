---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-10
tracking: https://github.com/tomcounsell/ai/issues/1979
last_comment_id:
---

# Delivery Guard Resume Epoch Scoping

## Problem

`agent/session_health.py`'s "Delivery guard" force-finalizes any `running`
session whose `response_delivered_at` field is set — on the assumption that a
delivered response means the session should have finalized. But the guard checks
only whether the field is set *at all*, never whether the delivery belongs to
the *current run*. Nothing clears `response_delivered_at`, so a session resumed
after a prior delivery carries the stale timestamp forever and gets killed on the
next health-check tick while still legitimately working.

**Current behavior:**

Observed 2026-07-09 on session `43b42260899a4f1f81f254fa2b1fc2bb` (project
`psyoptimal`):

1. Session delivered a response at `05:55:12` during a prior (failed) attempt.
2. Resumed via `valor_session resume` at `10:20:26` for a fresh attempt.
3. ~4 min in, the underlying `claude -p --resume` subprocess (PID 78380) was
   confirmed still alive via `ps` — not crashed.
4. The health check fired anyway:
   `[session-health] Session ... already delivered response at 2026-07-09 05:55:12.680293, finalizing stuck running session`
   and force-finalized the session `completed` using the **prior run's** timestamp.
5. The live process kept working, orphaned from its own DB record, and ~7 min
   later opened and merged a real PR.
6. When the live process tried to finalize normally, it hit a
   terminal→terminal `StatusConflictError` — a downstream symptom of step 4, not
   an independent bug.

**Desired outcome:**

The Delivery guard only fires when the delivery happened during the *current*
run. Resuming a session that delivered in a prior attempt does not expose it to
immediate premature finalization. The guard's original purpose — catching a
session that delivered *this run* but got stuck in `running` — is preserved
intact.

## Freshness Check

**Baseline commit:** `2f659c7ff9f331f36d2fe92d0df2d5a9b31b089a`
**Issue filed at:** 2026-07-09T10:42:56Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/session_health.py:3196-3219` (the cited Delivery guard) — **drifted** to
  `agent/session_health.py:3228` (`if getattr(entry, "response_delivered_at", None) is not None:`),
  inside `_agent_session_health_check()` (def at line 3097). Claim still holds.
- **New finding:** a *second*, structurally identical delivery guard exists at
  `agent/session_health.py:2222`, inside `_apply_recovery_transition()` (def at
  line 2113): "Guard: if response was already delivered, finalize instead of
  recovering to pending (#918)." The issue only cited one guard; both share the
  bug and both must be fixed, or the fix is incomplete.
- `tools/valor_session.py::resume_session` (issue cited `680-757`) — confirmed at
  lines 680-757: pushes a steering message then `transition_status(..., "pending",
  ..., reject_from_terminal=False)`, no field clearing. Claim holds.
- `models/agent_session.py:158` `response_delivered_at = DatetimeField(null=True)` — confirmed.
- Setters confirmed at `agent/session_executor.py:1504-1505` and
  `agent/session_completion.py:1079-1080`; grep confirms **zero** clear sites.
- **New finding (shapes the fix):** `started_at` is freshly stamped on *every*
  pickup, including resume pickups (`agent/session_pickup.py:463` and `:611`:
  `chosen.started_at = datetime.now(tz=UTC)`), and is nulled on recovery
  (`agent/session_health.py:2558`: `entry.started_at = None`). This makes
  `started_at` a viable per-run epoch anchor, with `created_at` as the fallback
  when it is None.

**Cited sibling issues/PRs re-checked:**
- #1614 — the sticky-field-not-scoped-to-current-run precedent; gated
  turn_count/log_path/claude_session_uuid behind `NO_OUTPUT_BUDGET_SECONDS`.
  Same class of bug, different field. Directly informs the audit task below.
- #944, #1938 (closed), #1962 — confirmed distinct guard branches, ruled out as
  duplicates in the issue's Prior Context; nothing changed that stance.

**Commits on main since issue was filed (touching referenced files):**
- `1cb4478e fix(#1962): don't recover a fresh-heartbeat running session as orphaned (#1982)`
  — touched `agent/session_health.py` but modified the #944 "orphaned running
  row" branch, **not** the Delivery guard. Irrelevant to this root cause; the two
  delivery guards are unchanged from what the issue described.

**Active plans in `docs/plans/` overlapping this area:** none. (`consolidate_delivery_paths.md`
concerns the delivery pipeline, not the health-check finalization guard — no overlap.)

**Notes:** The only drift is line-number movement (3196→3228) plus the discovery
of the second guard at 2222. The bug's premise is fully intact and reproducible
by inspection of the code path.

## Prior Art

- **#1614**: *ungated sticky own-progress fields let a stale running session evade
  recovery* — same class (a sticky field not scoped to the current run), fixed by
  gating turn_count/log_path/claude_session_uuid behind `NO_OUTPUT_BUDGET_SECONDS`.
  Precedent that this codebase has hit and fixed this pattern before; the audit
  task extends that discipline to `response_delivered_at` and any remaining fields.
- **#918**: introduced the Delivery guard(s) to prevent duplicate delivery /
  stuck-after-delivery sessions. This plan scopes those guards, it does not remove
  them.
- **#944, #1938 (closed), #1962/#1982**: distinct health-check branches, ruled out
  as duplicates.

## Data Flow

1. **Delivery**: session delivers a response → `session_executor.py:1504` (or
   `session_completion.py:1079`) stamps `response_delivered_at = now()`.
2. **Terminal**: session reaches `failed`/`completed`.
3. **Resume**: `resume_session()` pushes steering + `transition_status(..., "pending")`.
   `response_delivered_at` is **not** cleared (the stale timestamp persists).
4. **Pickup**: worker transitions pending→running and stamps a fresh
   `started_at = now()` (`session_pickup.py:463`/`:611`).
5. **Health check tick**: `_agent_session_health_check()` scans `running` rows;
   the Delivery guard at line 3228 sees `response_delivered_at is not None` and
   finalizes `completed` — **using the prior run's timestamp**. The parallel guard
   in `_apply_recovery_transition()` (line 2222) does the same on its path.
6. **Bug surfaces**: the still-alive subprocess is orphaned from its DB record;
   its later self-finalize hits `StatusConflictError`.

The fix inserts an epoch comparison at step 5 (both guard sites): treat the
delivery as "this run" only if `response_delivered_at >= (started_at or created_at)`.

## Why Previous Fixes Failed

No prior fix targeted `response_delivered_at` scoping — this is the first pass at
this specific field. The structural precedent (#1614) fixed the *same class* of
bug for different fields, which is why this plan follows its epoch/gating shape
rather than inventing a new pattern.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: one new module-private helper in `agent/session_health.py`
  (e.g. `_delivery_belongs_to_current_run(entry) -> bool`); no public signature
  changes.
- **Coupling**: unchanged. The helper reads only fields already read in that module.
- **Data ownership**: unchanged. No field is added, removed, or cleared. The fix
  is purely a read-site predicate; `started_at`/`created_at`/`response_delivered_at`
  keep their current owners.
- **Reversibility**: trivial — the change is a guarded condition; reverting restores
  the unconditional check.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a tightly-scoped bug fix: one predicate helper applied at two read sites,
plus regression tests. The risk is in *correctness of the epoch boundary*, not in
volume of code.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Epoch predicate helper** (`_delivery_belongs_to_current_run(entry)`): returns
  `True` only when `response_delivered_at` is set **and** falls at or after the
  current run's start anchor (`started_at`, falling back to `created_at` when
  `started_at` is None). Reuses the existing `_ts()` normalizer for datetime/float
  comparison.
- **Guard site A** (`_apply_recovery_transition`, line ~2222): replace the bare
  `response_delivered_at is not None` check with the helper.
- **Guard site B** (`_agent_session_health_check`, line ~3228): same replacement.
- **Sticky-field audit** (#1614 follow-through): enumerate the other
  cross-resume-persistent fields `session_health.py` reads and confirm each is
  already run-scoped (gated by heartbeat/budget) or file a note if a genuine gap
  remains. Do the audit inline; only escalate to a separate issue if a real
  second gap is found (see No-Gos).

### Flow

Health-check tick → scan `running` rows → for each row, Delivery guard evaluates
`_delivery_belongs_to_current_run(entry)` → **True** (delivered this run, stuck):
finalize `completed` (original behavior preserved) → **False** (no delivery, or
delivery predates this run's start): fall through to the normal
worker_alive/no_progress evaluation → live resumed session is left running.

### Technical Approach

- **Epoch anchor = `started_at or created_at`.** `started_at` is re-stamped on every
  pickup, so after a resume it is strictly later than any prior-run delivery; the
  stale `response_delivered_at` sorts *before* it and the guard correctly declines
  to fire. `created_at` is the fallback for the legacy/None case, which preserves
  the original always-fire behavior for pre-`started_at` rows (safe: those never
  resume through the modern path). Mirrors the existing
  `started_ref = entry.started_at or entry.created_at` idiom already used elsewhere
  in this module.
- **Comparison uses `>=`** (not `>`) so a same-run delivery whose timestamp equals
  the start anchor still counts as "this run."
- **Both guard sites call the one helper** — no divergence between the two paths.
- **Chosen over clearing `response_delivered_at` on resume.** Clearing in
  `resume_session()` would fix only the resume path and leave any other
  terminal→running transition exposed; it also destroys delivery history. A
  read-site epoch predicate is a single source of truth that covers every path and
  keeps the field's history intact — consistent with the issue's constraint
  ("epoch-scoping added, not the guard removed").

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The two guard blocks each wrap `finalize_session` in `try/except
  StatusConflictError` + `except Exception` with logging (not silent). No new
  `except: pass` is introduced. The helper itself must not raise — it returns
  `False` on any unparseable timestamp (defensive), which is the safe direction
  (skip the destructive finalize). A unit test asserts the helper returns `False`
  when `response_delivered_at` is a garbage/None value.

### Empty/Invalid Input Handling
- [ ] Helper handles `response_delivered_at=None` → `False`; `started_at=None`
  → fall back to `created_at`; both `started_at` and `created_at` None → treat as
  legacy and fire the guard (return `True`) to preserve original behavior. Each
  branch gets a unit test.
- [ ] Not agent-output processing; no silent-loop surface.

### Error State Rendering
- [ ] No user-visible surface. The guard's log lines (`already delivered response
  at ... finalizing`) remain; a test asserts the log is emitted only when the
  helper returns `True`.

## Test Impact

- [ ] `tests/unit/test_health_check_recovery_finalization.py` — UPDATE: existing
  cases that construct entries with `response_delivered_at` set must also set
  `started_at`/`created_at` consistent with the intended run so the epoch predicate
  resolves as the test expects. Cases asserting "delivered → finalized" should set
  `response_delivered_at >= started_at`; verify each and adjust fixtures.
- [ ] `tests/unit/test_deliver_pipeline_completion.py` — UPDATE (verify only):
  these test the *stamping* side (`session_completion`), not the health-check
  guard, so they likely need no change — confirm no fixture asserts the old
  unconditional guard behavior.
- [ ] Add a new regression test module (e.g.
  `tests/unit/test_delivery_guard_resume_epoch.py`) — see Success Criteria.

## Rabbit Holes

- **Do not refactor the two guard blocks into one shared function.** They live in
  different functions with different surrounding recovery logic
  (`_apply_recovery_transition` reclaims a slot lease; `_agent_session_health_check`
  increments `recovered`). Extract only the *predicate*, not the whole block.
- **Do not attempt to clear `response_delivered_at` on resume as the primary fix.**
  It is a narrower, history-destroying alternative already considered and rejected
  above.
- **Do not expand the #1614 sticky-field audit into a rewrite of the no-progress
  detector.** The audit is a read-and-confirm pass; any real new gap becomes its
  own scoped issue, not scope creep here.

## Risks

### Risk 1: Legacy rows with no `started_at` and no `created_at`
**Impact:** Epoch anchor is None; predicate could wrongly skip a genuinely
stuck-after-delivery session.
**Mitigation:** When both anchors are None, treat as legacy and return `True`
(fire the guard) — identical to today's behavior for those rows. Covered by an
explicit unit test.

### Risk 2: `started_at` reset timing vs. delivery within the same run
**Impact:** If a same-run delivery could ever be stamped *before* `started_at`,
the guard would wrongly decline to fire.
**Mitigation:** `started_at` is set at pickup (pending→running), strictly before
any turn runs and thus before any delivery in that run; `>=` comparison covers
the equality edge. Data-flow trace confirms ordering. A unit test asserts a
same-run delivery (`response_delivered_at == started_at` and `> started_at`) still
finalizes.

## Race Conditions

### Race 1: resume transition vs. health-check tick
**Location:** `tools/valor_session.py:680-757` (resume) vs.
`agent/session_health.py:3228` / `:2222` (guards).
**Trigger:** Health check ticks between resume (→pending) and pickup (→running).
**Data prerequisite:** `started_at` must be re-stamped by pickup before the guard
reads it for a resumed run.
**State prerequisite:** While the row is `pending`, the running-loop guard does
not evaluate it at all (the loop filters `status="running"`), so the stale
`response_delivered_at` cannot trigger finalization before pickup sets a fresh
`started_at`. By the time the row is `running`, `started_at` is current.
**Mitigation:** No new synchronization needed — the pending→running ordering plus
the fresh `started_at` stamp already close the window. Documented here so the
builder does not "helpfully" clear the field or add a lock.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG] If the #1614 sticky-field audit uncovers a *second* genuinely
  ungated field (beyond `response_delivered_at`) that causes premature death or
  recovery evasion, it gets its own issue rather than being folded in here. This
  is conditional — the audit runs *in* this plan; only a confirmed new gap
  escalates. If the audit finds no new gap (the expected outcome given #1614
  already gated the known fields), nothing is deferred.
- Nothing else deferred — the delivery-guard fix, both guard sites, and the audit
  pass are all in scope for this plan.

## Update System

No update system changes required — this is a pure in-process behavior fix in
`agent/session_health.py`. No new dependencies, config, migrations, or Popoto
schema changes (no field added, removed, or type-changed).

## Agent Integration

No agent integration required — this is a worker-internal health-check change with
no new tool, MCP surface, CLI entry point, or bridge call. The agent never invokes
this code path directly; it runs autonomously inside the worker's health loop.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-recovery-mechanisms.md` — add a subsection on
  the Delivery guard's epoch scoping: what `response_delivered_at >= started_at`
  means, why prior-run deliveries no longer finalize a resumed session, and that
  `started_at` is the per-run anchor.

### External Documentation Site
- [ ] Not applicable — repo does not publish a Sphinx/MkDocs site for this module.

### Inline Documentation
- [ ] Docstring on `_delivery_belongs_to_current_run` stating the epoch rule and
  the legacy-None fallback.
- [ ] Update the inline comments above both guard blocks to reference epoch scoping
  (remove the implication that "field set" alone means "delivered this run").

## Success Criteria

- [x] A session resumed after a prior delivery is **not** force-finalized as
  `completed` by either Delivery guard based on the prior delivery's timestamp
  (regression test: deliver → terminal → resume → pickup stamps fresh `started_at`
  → run health check with a fresh heartbeat while `running` → assert NOT finalized).
- [x] The original guard purpose still works: a session that delivered **this run**
  (`response_delivered_at >= started_at`) but is stuck `running` is still finalized
  `completed` (regression test asserts finalize fires).
- [x] Both guard sites (`_apply_recovery_transition` line ~2222 and
  `_agent_session_health_check` line ~3228) use the shared epoch predicate —
  grep confirms both reference `_delivery_belongs_to_current_run`.
- [x] Legacy-None edge (`started_at` and `created_at` both None) preserves the
  original always-fire behavior (unit test).
- [x] #1614 sticky-field audit completed: a note in the PR description (or a filed
  issue) records which other `session_health.py` fields were checked and their
  run-scoping status.
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (health-guard)**
  - Name: `guard-builder`
  - Role: Add `_delivery_belongs_to_current_run` helper; apply at both guard sites;
    update inline comments and docstring.
  - Agent Type: builder
  - Domain: async/concurrency (session lifecycle, Popoto rows)
  - Resume: true

- **Builder (tests)**
  - Name: `test-builder`
  - Role: Write the regression tests; update affected fixtures in
    `test_health_check_recovery_finalization.py`.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (guard)**
  - Name: `guard-validator`
  - Role: Verify both guard sites use the predicate, epoch boundary is `>=`, legacy
    fallback preserved, and the audit note exists.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add the epoch predicate helper
- **Task ID**: build-helper
- **Depends On**: none
- Add `_delivery_belongs_to_current_run(entry) -> bool` to `agent/session_health.py`
  near `_ts()`. Logic: `rd = _ts(getattr(entry, "response_delivered_at", None))`;
  if `rd is None` → `False`. `anchor = _ts(getattr(entry, "started_at", None)) or
  _ts(getattr(entry, "created_at", None))`; if `anchor is None` → `True` (legacy,
  preserve original behavior). Return `rd >= anchor`. Defensive: any exception →
  `False` is acceptable only for genuinely unparseable input; prefer explicit None
  handling via `_ts`.

### 2. Apply the predicate at both guard sites
- **Task ID**: build-guards
- **Depends On**: build-helper
- Replace `if getattr(entry, "response_delivered_at", None) is not None:` with
  `if _delivery_belongs_to_current_run(entry):` at `_apply_recovery_transition`
  (~line 2222) and `_agent_session_health_check` (~line 3228). Update the
  surrounding comment blocks to describe epoch scoping.

### 3. Regression + edge tests
- **Task ID**: build-tests
- **Depends On**: build-helper
- New module `tests/unit/test_delivery_guard_resume_epoch.py`: (a) resumed prior
  delivery not finalized; (b) same-run delivery still finalized (both `==` and
  `>` boundary); (c) legacy both-None fires; (d) None `response_delivered_at`
  falls through. Update `test_health_check_recovery_finalization.py` fixtures so
  delivered→finalized cases set `response_delivered_at >= started_at`.

### 4. #1614 sticky-field audit
- **Task ID**: audit-sticky-fields
- **Depends On**: none
- Enumerate cross-resume-persistent fields read by `session_health.py`; confirm
  each is run-scoped (heartbeat/budget-gated per #1614) or flag a gap. Record the
  result in the PR description. Only file a new issue if a real second gap surfaces.

### 5. Docs
- **Task ID**: build-docs
- **Depends On**: build-guards
- Update `docs/features/session-recovery-mechanisms.md` per the Documentation
  section.

### 6. Validate
- **Task ID**: validate
- **Depends On**: build-guards, build-tests, build-docs
- Run `/do-test`; grep-confirm both guard sites use the predicate; confirm audit
  note present.

## Verification

| # | Criterion | Check |
|---|-----------|-------|
| 1 | Both guards use the predicate | `grep -c "_delivery_belongs_to_current_run" agent/session_health.py` returns ≥ 3 (def + 2 call sites) |
| 2 | Guard NOT removed | Both guard blocks still call `finalize_session(..., "completed", ...)` on the `True` branch |
| 3 | Epoch boundary inclusive | Helper uses `>=`, not `>` |
| 4 | Legacy preserved | Unit test asserts both-None-anchor → guard fires |
| 5 | Resumed session survives | Regression test asserts prior-run delivery → NOT finalized |
| 6 | No field cleared on resume | `grep -n "response_delivered_at" tools/valor_session.py` still returns no assignment (fix is read-site only) |

## Open Questions

1. **Audit escalation policy** — if the #1614 sticky-field audit finds a second
   ungated field, should the builder fix it inline (if trivial) or always file a
   separate issue? Plan currently says: separate issue unless it is the same
   one-line epoch pattern, in which case fix inline. Confirm this is the desired
   bias.
2. **Helper defensiveness** — is returning `False` (skip finalize) the right safe
   direction on an unparseable `response_delivered_at`, versus `True` (fire)?
   Plan chose `False` (never destructively finalize on ambiguous data). Confirm.
