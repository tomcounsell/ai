---
status: Ready
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-07-14
tracking: https://github.com/tomcounsell/ai/issues/2083
last_comment_id: 4990928521
revision_applied: true
revision_applied_at: 2026-07-14T06:26:56Z
---

# Popoto 1.8.0 Descriptor-Pollution / Index-Race Scar-Tissue Audit

## Problem

`models/agent_session.py` and `models/session_lifecycle.py` carry multiple
defensive/healing code paths that exist to survive Popoto 1.7.1's cross-process
`save()` behavior — custom `__getattribute__`/`__setattr__` overrides, coercion
sets (`_INT_FIELDS_BACKCOMPAT`, `_DATETIME_FIELDS`), a `_saved_field_values["status"]`
backfill, and a defensive `srem`-across-`ALL_STATUSES` loop. PR #2081 (`6c243ebc`)
upgraded Popoto `1.7.1 → 1.8.0`, which introduces atomic Lua secondary-index
maintenance (`INDEX_SWAP_LUA`). PR #2081 explicitly deferred the question of which
of this scar tissue is now redundant.

**Current behavior:** Nobody has verified which defensive code is now dead and
which is still load-bearing. The save path is race-sensitive and multi-process;
deleting the wrong defense reintroduces a live production bug (worker crash loops,
sessions stranded in two status index sets, OOM detector silently misfiring on
legacy rows).

**Desired outcome:** A verified inventory of every descriptor-pollution /
index-race defense with a per-defense verdict (redundant-and-removed vs.
load-bearing-and-kept). Every removal maps to a Sentry issue as a regression
tripwire (removed-defenses-ledger discipline). Every kept defense carries an
explicit comment stating why 1.8.0 does not subsume it. No removal on optimism.

## Freshness Check

**Baseline commit:** `1227da35f608b39129a8f195e099c9ccacb4427d`
**Issue filed at:** 2026-07-14T05:56:15Z
**Disposition:** Unchanged

### Re-check 2026-07-16 (pre-BUILD re-dispatch)

- **#2086 gate CLEARED.** The issue-thread hard gate ("no removal PR may merge
  until #2086 is root-caused and resolved") is satisfied: #2086 was root-caused
  (2026-07-15) as a **mixed-version deploy artifact** (popoto 1.8.0-writer →
  1.7.1-reader choking on the raw `{field}\x00idxset` pointer value) and CLOSED
  2026-07-15T11:04Z. Crucially, #2086 was **not** the index race this audit
  concerns — the audit premise stands. Sibling #2088 also CLOSED 2026-07-15.
- **Drift since plan (2026-07-14):** two commits touched referenced files —
  `1aedc8a4e` (#2101/#2102, A1 rebuild guard in `repair_indexes` + tests) and
  `d105b33e5` (batched stale-index scans in `repair_indexes`). Both are
  **Cluster C only** (`repair_indexes`, out of scope / confirm-and-keep);
  neither touches Cluster A/B defenses. Line numbers in `agent_session.py`
  after ~:2100 have shifted; the build must re-verify file:line refs.
- **Salvaged Task-1 audit (commit `81a7e471`, per operator re-dispatch
  guidance):** the killed run's read-only audit draft
  (`docs/features/popoto-descriptor-pollution-ledger.md`) is adopted as the
  Task-1 seed. It records two evidence-backed revisions to this plan's working
  hypotheses, both within the plan's audit-gated process:
  1. **Cluster A `_INT_FIELDS_BACKCOMPAT` + `__getattribute__` missing-field
     descriptor substitution → REMOVE-CANDIDATE** (contradicts spike-1's
     "lazy-load leak still exists" claim): popoto has default-filled absent
     fields at construction since 1.6.1 (`encoding.py` `_create_lazy_model`);
     the killed run's empirical repro on live Redis showed scalars, never
     descriptors, even bypassing the override. Removal remains gated on the
     plan's regression-test discipline plus a fresh re-verification of the
     repro (the /tmp scripts were discarded). `__setattr__` datetime coercion
     and `_normalize_kwargs` #929 coercion remain KEEP (write-path coercion,
     different mechanism).
  2. **B3 verdict KEEP, but re-justified**: empirically redundant in the
     steady state (pointer-bearing rows); its load-bearing case is the
     compound legacy-no-pointer + no-hint scenario — i.e. the same
     legacy-pointer gate as B1/B2, not an independent value-freshness defense.
     B1/B2/B3 KEEP, gated (unchanged outcome, corrected reasoning).

**File:line references re-verified (all present today):**
- `models/agent_session.py:236`, `:335` — `_heal_descriptor_pollution` appears ONLY in comments — confirmed, no such method exists (grep).
- `models/agent_session.py:610` `_DATETIME_FIELDS`, `:622` `_INT_FIELDS_BACKCOMPAT`, `:643-687` `__getattribute__`, `:689-732` `__setattr__`, `:740-900` `_normalize_kwargs` (`response_delivered_at` coercion, #929) — all present.
- `models/agent_session.py:945` `save()` (updated_at stamp, #1645), `:977` `_heal_future_updated_at`, `:2102` `repair_indexes`, `:2146` `cleanup_expired` — all present.
- `models/session_lifecycle.py:466-475` `_saved_field_values["status"]` backfill, `:488-510` defensive `srem` across `ALL_STATUSES`, `:601-603` second backfill site, `:472-474` pre-marked "if Popoto is upgraded, verify" note — all present.
- `models/agent_session.py:145` `status = IndexedField(...)` is the ONLY `IndexedField`; `created_at:150` is `SortedField` — confirmed.
- Popoto 1.8.0 `INDEX_SWAP_LUA` at `popoto/fields/indexed_field_mixin.py:97`; runtime `popoto.__version__ == "1.8.0"` — confirmed. (Note: `pip show popoto` metadata cache reads a stale `1.7.1`; the installed dist-info and runtime version are `1.8.0`.)

**Cited sibling issues/PRs re-checked:**
- #2080 — CLOSED (popoto 1.8.0 upgrade tracking). #2081 — MERGED 2026-07-14T05:16:56Z (`6c243ebc`). This is the origin.
- #1099 (IntField), #1172 (DatetimeField/Field), #1185 (DatetimeField descriptor pollution) — all CLOSED, all the same lazy-load descriptor-leak bug class per `feedback_field_backcompat_heal` memory.
- #929 (`response_delivered_at`), #1645/#1817 (clock skew), #1720 (class-set delete/re-add window) — referenced in-code, not re-opened.

**Commits on main since issue filed touching referenced files:** none. Two plan commits landed (`8a432c48`, `1227da35`) for issue #2082 (memory hybrid-retrieval eval) — a different popoto-1.8.0 topic; neither touched `models/agent_session.py` or `models/session_lifecycle.py`.

**Active plans overlapping this area:** none. `docs/plans/hybrid-retrieval-eval.md` (#2082) is popoto-1.8.0-adjacent but concerns memory recall, not the index race or descriptor leak. No overlap.

**Notes:** All issue claims hold verbatim. The issue's naming-trap warning is accurate: `_heal_descriptor_pollution` is comment-only shorthand.

## Prior Art

- **#2080 / PR #2081**: Popoto 1.7.1 → 1.8.0 upgrade — merged, the origin of this audit. Introduced `INDEX_SWAP_LUA`.
- **#1099 / PR #1153**: "harness failure hardening" — first per-type IntField descriptor-leak fix. Per `feedback_field_backcompat_heal` memory, the per-type fix did NOT generalize, so #1172 recurred.
- **#1172 / #1185**: DatetimeField/Field descriptor pollution crashed `save()` on pre-existing rows (dropped a PM message, crashed the worker loop). Motivated the generic `__getattribute__`/`__setattr__` heal.
- **#1270 / PR #1279**: per-tool timeout counters — added `tool_timeout_count_*` IntFields after initial model creation, which is why they are in `_INT_FIELDS_BACKCOMPAT`.
- **#1720**: class-set delete→re-add window in `rebuild_indexes()` — motivated `repair_indexes` + read-path bounded retries. This is a `rebuild_indexes` concern, NOT the save race, and is likely a separate concern from what `INDEX_SWAP_LUA` fixes.
- **#950** (CLOSED, "Stale-index regression: pending index entry survives pending→killed transitions"): **the direct motivating trigger for the defensive `srem`-across-`ALL_STATUSES` loop at `session_lifecycle.py:488-510`.** A process holding a *stale* AgentSession object does a FULL `save()` that clobbers `status` back to a superseded value (e.g. re-writes `status="running"` after another process already moved it to `completed`), re-adding the row to the wrong index Set. The defensive `srem` in `finalize_session` scrubs the row out of every non-target status Set to repair this. **Crucially, this is a value-freshness bug, NOT a client-side SREM/SADD interleave bug** — see the correction to the over-claim in Data Flow / Race Conditions below. `INDEX_SWAP_LUA` guarantees the index pointer matches whatever value the save writes; it does NOT prevent a stale full save from atomically writing a stale *value*. So `INDEX_SWAP_LUA` does **not** subsume this `srem`, and the audit must prove otherwise with a red-state regression test before any removal verdict.
- **#1645 / #1817**: clock-skew `updated_at` — motivated `save()` stamp + `_heal_future_updated_at` (now read-only detection). Different bug class; out of scope.

## Research

Investigation was done by reading the installed Popoto 1.8.0 source directly (more authoritative than release notes), captured in memory `3dea2cc0`.

**Key findings:**
- `INDEX_SWAP_LUA` (`popoto/fields/indexed_field_mixin.py:97`) is an atomic server-side check-and-swap for `IndexedFieldMixin` secondary-index Sets **only**. `models/base.py:1131` and `:1292` explicitly *exclude* `IndexedFieldMixin` fields from the plain HSET path because "EVAL (INDEX_SWAP_LUA) owns their" maintenance.
- The Lua script reads a **server-authoritative pointer** (`{field}\x00idxset`) stored in the model hash to know the record's current index Set, then atomically `SREM` old / `SADD` new. This eliminates the client-side check-then-act race the `_saved_field_values["status"]` backfill + defensive `srem` were compensating for.
- **Legacy-row caveat:** rows written before 1.8.0 have no pointer. On the first save after upgrade, the Lua script falls back to a `legacy_old_set` hint (`ARGV[6]`) derived from Popoto's `_saved_field_values`. So the `_saved_field_values["status"]` backfill in `session_lifecycle.py` may remain load-bearing until every live row has been re-saved once under 1.8.0 and has a pointer.
- The lazy-load path that produces the descriptor leak (bug class 2) **still exists** in 1.8.0 (`popoto/models/base.py:728`, `_lazy_fields` / msgpack-decode-on-first-access). `INDEX_SWAP_LUA` does not touch it. The defended fields (`exit_returncode`, `tool_timeout_count_*` = `IntField`; `response_delivered_at`, `last_heartbeat_at` = `DatetimeField`) are all **plain, non-indexed** Fields — outside `INDEX_SWAP_LUA`'s scope entirely.

## Spike Results

### spike-1: Does INDEX_SWAP_LUA cover the plain-Field descriptor-leak defenses?
- **Assumption**: "Popoto 1.8.0's atomic index makes the `__getattribute__`/`__setattr__`/`_INT_FIELDS_BACKCOMPAT`/`_DATETIME_FIELDS` defenses redundant."
- **Method**: code-read (installed popoto 1.8.0 source + AgentSession field declarations)
- **Finding**: **FALSE.** `INDEX_SWAP_LUA` owns `IndexedFieldMixin` fields only. The descriptor-leak defenses guard plain `IntField`/`DatetimeField` reads on legacy rows — a different bug class (lazy-load leak) than the secondary-index race. The lazy-load path still exists in 1.8.0.
- **Confidence**: high
- **Impact on plan**: Cluster A (descriptor-leak defenses) is preliminarily KEEP. Removal candidates concentrate in Cluster B (status-index defenses).

### spike-2: Does 1.8.0's atomic index subsume the status-index defenses?
- **Assumption**: "`_saved_field_values['status']` backfill + defensive `srem` across `ALL_STATUSES` are now dead."
- **Method**: code-read (`INDEX_SWAP_LUA` contract + `session_lifecycle.py`)
- **Finding**: **Partially — gated on legacy rows.** In steady state (rows with a server-authoritative pointer), the Lua swap makes both defenses redundant. But legacy rows without a pointer depend on the `legacy_old_set` hint sourced from `_saved_field_values` on their first post-upgrade save. Safe removal requires either a one-time migration re-saving all live rows (establishing pointers) OR proof the hint path does not need the AgentSession-level backfill.
- **Confidence**: medium (needs the empirical Task-1 audit + a migration decision)
- **Impact on plan**: Cluster B removals are GATED behind a migration that establishes pointers on all live rows first.

## Data Flow

Two distinct failure modes flow through two distinct code paths:

**Bug class 1 has TWO distinct sub-modes that were previously lumped together. `INDEX_SWAP_LUA` covers one and NOT the other — the earlier draft conflated them.**

**Bug class 1a — client-side SREM/SADD interleave (the clean `transition_status` race, Cluster B backfill):**
1. Process P1 loads AgentSession row (status="running"), process P2 loads the same row; both intend the SAME correct next value.
2. Both call `on_save()` → pre-1.8.0 each does a client-side `SREM old / SADD new`. The two-op sequences interleave → member stranded in two Sets.
3. The `_saved_field_values["status"]` backfill (`session_lifecycle.py:474-475` in `finalize_session` AND `:713-714` in `transition_status`) exists so Popoto's `on_save()` guard `if old_value is not None` fires and issues the `srem` at all (lazy-loaded rows start with an empty `_saved_field_values`).
4. 1.8.0: `INDEX_SWAP_LUA` runs SREM-old/SADD-new/write-pointer as a single atomic server-side Lua script keyed on the hash pointer → **this interleave is genuinely eliminated.** The backfill's job (feeding `on_save()` the old value) is what the server-authoritative pointer now supplies — gated only on legacy rows having a pointer (spike-2).

**Bug class 1b — stale-object FULL save clobbering the value (#950, the defensive `srem` at `:488-510`):**
1. Process P2 holds a *stale* AgentSession object (in-memory `status="running"`) while P1 has already moved the row to `status="completed"`.
2. P2 does a FULL `save()`. This writes the STALE value `status="running"` back to the hash and (under 1.8.0) atomically points the index at `running`.
3. The row is now indexed under the WRONG status. `finalize_session`'s defensive `srem`-across-`ALL_STATUSES` scrubs the row out of every non-target Set to repair this after the terminal transition.
4. **`INDEX_SWAP_LUA` does NOT fix this.** The atomic Lua only guarantees the index pointer is consistent with *whatever value the save writes* — and a stale full save writes a stale value. Atomicity of the swap ≠ freshness of the value. **Therefore the defensive `srem`'s preliminary verdict is KEEP, and any removal requires a red-state regression test (a stale full save clobbering status) that stays green without the `srem` under 1.8.0.** This is the correction that resolves the critique BLOCKER.

**Bug class 2 — lazy-load descriptor leak (Cluster A):**
1. A field is added to the model AFTER a row was written to Redis.
2. `_create_lazy_model` / lazy-load builds the instance from the hash; the missing key is absent from `__dict__`.
3. Reading the field falls through to the class-level `IntField`/`DatetimeField` **descriptor object** instead of a scalar.
4. Reader (`session_health.py`'s `exit_returncode == -9`) misbehaves, or `pre_save_format` calls `field.type(<descriptor>)` and crashes `save()`.
5. `__getattribute__` (`:643-687`) substitutes the default and heals `__dict__`; `__setattr__` (`:689-732`) coerces bad types. `INDEX_SWAP_LUA` never enters this path.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1153 (#1099) | Per-type IntField descriptor coercion | Did not generalize to DatetimeField/Field; #1172/#1185 recurred and crashed the worker loop |
| (pre-#1720 index handling) | Relied on client-side `SREM/SADD` for status index | Cross-process interleave stranded members in two Sets; needed the defensive `srem`-across-`ALL_STATUSES` compensator |

**Root cause pattern:** Both clusters are client-side compensations for Popoto internals (lazy-load semantics and client-side index maintenance). 1.8.0 fixes the *index-maintenance* internal (Cluster B's root cause) but NOT the *lazy-load* internal (Cluster A's root cause).

## Architectural Impact

- **New dependencies**: none (Popoto 1.8.0 already installed via #2081).
- **Interface changes**: potential removal of `_saved_field_values["status"]` backfill helper logic in `session_lifecycle.py`; potential removal of the defensive `srem` loop. Public method signatures unchanged.
- **Coupling**: removing Cluster B *decreases* coupling to Popoto internals (`_saved_field_values` is a Popoto private attribute).
- **Data ownership**: unchanged; index ownership moves from client-side compensators to Popoto's server-side Lua (already true as of #2081 — this plan just removes the now-dead client compensators).
- **Reversibility**: high — each removal is a small, isolated deletion; the ledger + Sentry tripwire enables targeted re-fix if a regression surfaces.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm the audit verdicts before any removal; approve the migration-gate decision)
- Review rounds: 1 (race-sensitive save path; a reviewer must sanity-check every removal against the ledger)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Popoto 1.8.0 installed | `.venv/bin/python -c "import popoto; assert popoto.__version__ == '1.8.0'"` | Atomic index present |
| Redis reachable (integration tests) | `.venv/bin/python -c "from popoto.models.query import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | Legacy-row reproduction / migration test |
| Sentry access (org yudame, project 4511091961888768) | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('SENTRY_DSN')"` | Ledger tripwire creation |

## Solution

### Key Elements

- **Audit report (read-only, first and gating)**: the definitive file:line inventory of both clusters with a per-defense verdict grounded in what `INDEX_SWAP_LUA` actually guarantees. Written to `docs/features/popoto-descriptor-pollution-ledger.md`.
- **Cluster A — descriptor-leak defenses (plain Fields)**: `_INT_FIELDS_BACKCOMPAT`, `_DATETIME_FIELDS`, `__getattribute__`, `__setattr__`, `_normalize_kwargs` `response_delivered_at` coercion. Preliminary verdict: KEEP (different bug class, lazy-load path still live).
- **Cluster B — status-index defenses**, which are THREE separate code sites with TWO distinct verdicts (do not treat as one deletion):
  - **B1 — `_saved_field_values["status"]` backfill, site 1 of 2**: `session_lifecycle.py:474-475` in `finalize_session`. Guards bug class 1a. Preliminary verdict: REMOVE-CANDIDATE, gated behind a pointer-establishing migration (spike-2).
  - **B2 — `_saved_field_values["status"]` backfill, site 2 of 2**: `session_lifecycle.py:713-714` in `transition_status` (the second site the critique flagged — a half-removal here would silently strand the non-terminal transition path). Same bug class (1a), same verdict and audit treatment as B1: REMOVE-CANDIDATE, gated on the same migration. B1 and B2 move together — neither is removed without the other, or the surviving site's absence leaves lazy-loaded rows stranded.
  - **B3 — defensive `srem`-across-`ALL_STATUSES`**: `session_lifecycle.py:488-510`. Guards bug class 1b (#950 stale-object FULL save). Preliminary verdict: **KEEP** — `INDEX_SWAP_LUA` does not subsume value-freshness (see Data Flow bug class 1b). Only downgraded to REMOVE if a red-state regression test (stale full save clobbering status) proves it dead under 1.8.0. Ambiguity defaults to KEEP.
- **Cluster C — adjacent / out of scope**: `_heal_future_updated_at`, `save()` `updated_at` stamp, `repair_indexes`, `cleanup_expired`. Verdict: confirm-and-keep (separate bug class).
- **Removed-defenses ledger (PASSIVE tripwire)**: every removal maps to a Sentry issue (org `yudame`, project `4511091961888768`) so a *crashing* regression surfaces loudly.
- **Active index-consistency detector (the critique's CONCERN 2 fix)**: because index drift is SILENT (a row stranded in two status Sets does not throw — passive Sentry never fires), the ledger is paired with an ACTIVE detector. `AgentSession.repair_indexes()` already counts stale/drifted index members before repairing (`models/agent_session.py:2102`, returns `(stale_count, rebuilt_count)`). The plan wires a scheduled **dry-run stale-count probe** (count-only, no mutation) that alarms to Sentry when `stale_count > 0` after any Cluster B removal ships — turning the silent-drift failure mode into an observable signal. This is the load-bearing tripwire for B-cluster removals; the passive Sentry ledger entry alone is insufficient.

### Flow

Audit (read-only) → PM confirms verdicts → for each REMOVE: write a regression test that reproduces the original bug → confirm it passes under 1.8.0 with the defense present → (if Cluster B) add + run the pointer-establishing migration → remove the defense → confirm the regression test still passes → open the Sentry ledger issue → commit. For each KEEP: add an explicit "not subsumed by 1.8.0 because …" comment referencing #2083.

### Technical Approach

- **Task 1 is a read-only audit** and gates everything else. It must not trust any comment, plan doc, or memory referencing `_heal_descriptor_pollution` by name — it inventories real symbols. It confirms (a) `INDEX_SWAP_LUA`'s scope empirically, (b) that the lazy-load leak still reproduces under 1.8.0 (load a hash missing a field, read it, assert descriptor vs. scalar), (c) whether legacy rows need a migration before B1/B2 backfill removal, and (d) **the #950 stale-object FULL-save scenario specifically** for B3: reproduce a stale in-memory object writing a superseded `status` value via a full `save()` under 1.8.0, and observe whether the row is left stranded in the wrong index Set *with the defensive `srem` removed*. This is the empirical test of whether `INDEX_SWAP_LUA` covers value-freshness (the audit's working hypothesis, from Data Flow bug class 1b, is that it does NOT).
- **Removals are gated on a passing regression test that would have caught the original bug** — written first, confirmed RED without the defense, GREEN with it. Two distinct red-state repros are mandatory before ANY verdict:
  - For B1/B2 (backfill): a legacy-shaped lazy-loaded row transitioning status, asserting it is not stranded in two Sets.
  - For B3 (defensive `srem`): the **#950 stale-object full-save red-state test** — a stale object clobbers status via full `save()`; assert the row does NOT survive in the superseded index Set. **No verdict (KEEP or REMOVE) on B3 is permitted until this red-state test exists and its behavior under 1.8.0-without-`srem` is observed.** If the row is still stranded without `srem`, B3 is KEEP, full stop.
- **Cluster B backfill (B1/B2) removal — IF the audit verdicts REMOVE — requires a Popoto migration** (`scripts/update/migrations.py`, registered in `MIGRATIONS`, idempotent) that re-saves every live AgentSession once under 1.8.0 to establish the server-authoritative pointer, run BEFORE the backfill is deleted. Use `instance.save()` / `Model.rebuild_indexes()` — never raw Redis ops. **The migration is CONDITIONAL on the audit verdict, not pre-committed:** given the plan's own KEEP-leaning defaults (B3 KEEP, B1/B2 gated), the expected outcome may well be KEEP-all → **no migration written, no destructive schema work.** The migration task (Task 3) only executes if Task 1 produces a REMOVE verdict for B1/B2.
- **Ambiguity defaults to KEEP** (issue AC #5): if it is unclear whether the atomic index covers a defense, keep it and record an Open Question rather than delete. This governs B3 especially — value-freshness coverage is the ambiguous case, so B3 stays unless proven dead.
- Do NOT rewrite `docs/plans/completed/*.md` history — only update living docs whose claims become inaccurate.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `session_lifecycle.py:510` `except … logger.debug("Defensive srem failed (non-fatal)")` — B3 defaults to KEEP; the existing `test_defensive_srem_failure_is_nonfatal` covers the handler. Only if the #950 red-state test proves B3 dead does the block (and handler) get deleted together. Assert observable behavior either way.
- [ ] **#950 stale-object full-save red-state test (B3 gate):** construct a stale in-memory AgentSession (`status="running"`) after another path advanced the row to terminal; do a full `save()`; assert the row is NOT stranded in the superseded status index Set. Run WITH `srem` (green) and WITHOUT it under 1.8.0 (observe). This test gates any B3 verdict.
- [ ] Cluster A `__getattribute__`/`__setattr__` coercions log at DEBUG on coercion — if kept, add/confirm a test asserting the log fires when a descriptor/bad-type sneaks through.

### Empty/Invalid Input Handling
- [ ] Reproduce the descriptor-leak on a legacy row: a hash missing `exit_returncode` / `response_delivered_at`, then read the attribute — assert scalar (not descriptor) with the defense; capture behavior without it.
- [ ] `response_delivered_at` with a `str` / stale value (#929) — assert coercion to datetime/None if the defense is kept.

### Error State Rendering
- [ ] No user-visible surface. Assert the failure modes surface via `logger` / Sentry rather than silent index corruption or a swallowed `save()` crash.

## Test Impact

- [ ] `tests/unit/test_agent_session_index_corruption.py::test_defensive_srem_code_exists_in_finalize` — UPDATE or DELETE: this test asserts the defensive `srem` (B3) *source string* is present in `finalize_session`. B3 defaults to KEEP → leave as-is. Only DELETE if the #950 red-state test proves B3 dead and it is removed.
- [ ] `tests/unit/test_agent_session_index_corruption.py::test_backfills_saved_field_values_on_lazy_session` (and the `:139`/`:352`/`:382` backfill variants) — UPDATE/DELETE: these pin the `_saved_field_values["status"]` backfill. Disposition follows the Task-1 verdict + conditional migration decision for B1/B2. **Both backfill sites (B1 `finalize_session:474-475` AND B2 `transition_status:713-714`) share disposition — if either is kept these tests stay; a half-removal that deletes one site's test but not the other is a defect.**
- [ ] ADD: a test asserting BOTH backfill sites move together — if B1's backfill is present, B2's must be too (structural guard against the half-removal the critique flagged).
- [ ] `tests/unit/test_agent_session_index_corruption.py` index-clearing tests (`:426`, `:469`, `:500`) — UPDATE: confirm they still pass against `INDEX_SWAP_LUA`'s atomic swap; adjust assertions that assume client-side `srem`.
- [ ] `tests/unit/test_session_health_trusted_clock.py` and `tests/unit/test_agent_session_updated_at_utc.py` `_heal_future_updated_at` tests — no change (Cluster C, out of scope) — listed to confirm they are untouched.
- [ ] New regression tests (ADD): one per REMOVE decision, reproducing the original bug (#1099/#1172 descriptor leak; the 1a cross-process interleave; the **#950 stale-object full-save** for B3) — these are the safety net gating each deletion.
- [ ] ADD (only if any B-cluster removal ships): a test for the active stale-count detector — seed a deliberately drifted index member, assert the count-only `repair_indexes` dry-run reports `stale_count > 0` (proving the detector observes silent drift).

## Rabbit Holes

- **Rewriting Popoto's lazy-load path or filing an upstream popoto fix** — out of scope; this audit only decides which *AgentSession-level* compensators to keep or remove.
- **Auditing every other Popoto model in the repo for the same patterns** — the issue scopes this to AgentSession + its lifecycle helpers; a repo-wide sweep is a grep (do it in Task 1 to confirm no siblings) but fixing other models is a separate slug.
- **Mass-editing `docs/plans/completed/*.md` references to `_heal_descriptor_pollution`** — historical record, leave alone unless a *living* doc is inaccurate.
- **Over-engineering the migration** — re-saving live rows once to establish pointers is a one-shot idempotent loop, not a new subsystem.
- **Chasing `repair_indexes`/#1720** — that is the `rebuild_indexes()` class-set window, a different concern from the save-race; confirm-and-keep, do not entangle.

## Risks

### Risk 1: Removing a Cluster A defense on the false assumption 1.8.0 covers it
**Impact:** Reintroduces #1099/#1172 — worker crash loop / dropped PM messages on legacy rows.
**Mitigation:** spike-1 (high confidence) already shows `INDEX_SWAP_LUA` does not touch plain Fields; Cluster A defaults to KEEP. Any removal requires a green regression test proving the leak no longer reproduces under 1.8.0.

### Risk 2: Removing the Cluster B backfill before all legacy rows have a pointer
**Impact:** Legacy rows lose the `legacy_old_set` hint on first post-upgrade save → stranded in two status index sets → wrong `.filter(status=…)` results (sessions appear in two states).
**Mitigation:** Gate Cluster B removal behind the idempotent pointer-establishing migration; verify via an integration test that a pre-1.8.0-shaped row transitions cleanly with the backfill removed AFTER migration.

### Risk 3: Ledger tripwire never fires because index drift is SILENT (removal regresses without a crash)
**Impact:** Index drift (a row stranded in two status Sets) does NOT raise an exception — passive Sentry crash-capture never fires, and a removed B-cluster defense regresses invisibly (wrong `.filter(status=…)` results, OOM detector misfiring on legacy rows).
**Mitigation (upgraded per critique CONCERN 2):** the passive Sentry ledger entry is paired with an ACTIVE detector — a scheduled dry-run stale-count probe built on `AgentSession.repair_indexes()` (which already counts drifted members before repairing). The probe counts only (no mutation) and alarms to Sentry when `stale_count > 0` after any Cluster B removal ships. Passive Sentry alone is explicitly insufficient for a silent-drift failure mode.

### Risk 4: Removing the B3 defensive `srem` on the false assumption 1.8.0's atomic index covers value-freshness
**Impact:** Reintroduces #950 — a stale-object full save strands the row in a superseded status index Set; `.filter(status=…)` returns sessions in the wrong state, and the reflections stale-index regression signal fires at volume (as it did with 540 warnings post-#898).
**Mitigation:** B3 defaults to KEEP. No removal without the #950 stale-full-save red-state regression test proving the row is NOT stranded without `srem` under 1.8.0. `INDEX_SWAP_LUA`'s pointer atomicity is proven orthogonal to value freshness in Task 1(d).

## Race Conditions

### Race 1a: Concurrent cross-process status transition (client-side SREM/SADD interleave)
**Location:** `models/session_lifecycle.py:474-475` + `:713-714` (backfill, both sites); `popoto/fields/indexed_field_mixin.py:97` (INDEX_SWAP_LUA)
**Trigger:** Worker and bridge (or two sessions) `on_save()` the same AgentSession status transition within the same window.
**Data prerequisite:** The server-authoritative pointer (`status\x00idxset`) must exist in the model hash for INDEX_SWAP_LUA to remove from the correct old Set without the client-side snapshot.
**State prerequisite:** All live rows must have been re-saved at least once under 1.8.0 (migration) before the client-side backfill (B1/B2) is removed.
**Mitigation:** INDEX_SWAP_LUA runs the SREM-old/SADD-new/write-pointer as a single atomic server-side Lua script — the interleave is genuinely eliminated. The (conditional) pointer-establishing migration ensures the prerequisite holds before removal; until then, keep the backfill.

### Race 1b: Stale-object FULL save clobbering the status value (#950)
**Location:** `models/session_lifecycle.py:488-510` (defensive `srem`-across-`ALL_STATUSES`, B3)
**Trigger:** One process holds a stale in-memory AgentSession (`status="running"`) while another has already advanced the row to a terminal status; the stale process does a full `save()`, atomically re-writing the superseded value and pointing the index at the wrong Set.
**Data prerequisite:** None that INDEX_SWAP_LUA can supply — this is a value-freshness hazard, not a pointer-consistency hazard.
**Why INDEX_SWAP_LUA does NOT cover it:** The atomic Lua guarantees the index pointer is consistent with *whatever value the save writes*. A stale full save writes a stale *value*, atomically. Atomicity of the swap is orthogonal to freshness of the value. The `srem` in `finalize_session` scrubs the row out of every non-target Set precisely to repair a stale-value write that lands after the terminal transition.
**Mitigation:** KEEP the defensive `srem` (B3) unless the #950 stale-full-save red-state regression test proves it dead under 1.8.0. This is the resolution of the critique BLOCKER — the earlier draft's "no interleave possible" claim did not cover this sub-mode.

### Race 2: Lazy-load read racing a field-adding deploy
**Location:** `models/agent_session.py:643-732`; `popoto/models/base.py:728`
**Trigger:** A row written before a field existed is lazy-loaded and the field is read.
**Data prerequisite:** N/A — this is a schema-evolution hazard, not a concurrency one; INDEX_SWAP_LUA is irrelevant here.
**Mitigation:** Cluster A `__getattribute__`/`__setattr__` heal (KEEP). This race is unaffected by 1.8.0.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1720] The `rebuild_indexes()` class-set delete→re-add window and its read-path bounded retries (`tools/valor_session.py`, `tools/sdlc_stage_query.py`) are a distinct concern from the save-race; confirm-and-keep only, do not modify in this plan.
- [DESTRUCTIVE — CONDITIONAL] The one-time migration re-saving all live AgentSession rows to establish pointers is written **only if** the audit verdicts B1/B2 backfill removal as REMOVE. The plan does NOT pre-commit to it — the KEEP-all outcome (the likely default) ships zero migration. IF written: it is idempotent, recorded once in `data/migrations_completed.json`, and reviewed before merge as the safety mechanism (it writes to every live session hash). Do not author the destructive migration on optimism ahead of the Task-1 verdict.
- Rewriting Popoto internals or filing upstream popoto changes — this plan is AgentSession-level only.
- Editing `docs/plans/completed/*.md` historical references — left as historical record.

## Update System

- **Migration CONDITIONAL on the audit verdict (not pre-committed):** the pointer-establishing migration is written and registered **only if** Task 1 verdicts B1/B2 backfill removal as REMOVE. Given the plan's KEEP-leaning defaults (B3 KEEP; B1/B2 gated behind proof), the likely outcome is KEEP-all → **no migration, no `scripts/update/migrations.py` change, no destructive schema work.** IF (and only if) removal proceeds: add an idempotent migration to `scripts/update/migrations.py` (registered in the `MIGRATIONS` dict) that re-saves every live AgentSession once under 1.8.0 to establish the server-authoritative index pointer, run BEFORE the backfill is deleted. Uses `instance.save()` / `Model.rebuild_indexes()` — no raw Redis ops.
- **Active detector scheduling (only if any Cluster B removal ships):** the dry-run stale-count probe (CONCERN 2) is wired as a lightweight scheduled check — extend the existing `agent-session-cleanup` hourly reflection (or add a `reflections.yaml` entry) to call the count-only `repair_indexes` dry-run and alarm to Sentry on `stale_count > 0`. No new standalone service. If nothing in Cluster B is removed, no detector wiring is needed.
- No new dependencies to propagate (Popoto 1.8.0 already shipped via #2081).
- No `scripts/update/run.py` changes beyond the conditional migration registration.

## Agent Integration

No agent integration required — this is an internal data-model audit and cleanup. No new MCP surface, no `.mcp.json` change, no `bridge/telegram_bridge.py` change. The audit report and ledger are Markdown docs; the code changes are internal to `models/`.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/popoto-descriptor-pollution-ledger.md` — the audit report + removed-defenses ledger: per-defense inventory (file:line) covering all of B1/B2/B3 separately, verdict (removed/kept), reasoning tied to what `INDEX_SWAP_LUA` actually guarantees (pointer atomicity, NOT value freshness), the Sentry issue link for each removal (passive tripwire), and — if any B-cluster removal ships — the active stale-count detector's location and alarm threshold (CONCERN 2).
- [ ] Add an entry to `docs/features/README.md` index table.

### Inline Documentation
- [ ] For every KEPT defense: add/refresh a comment stating "NOT subsumed by Popoto 1.8.0 `INDEX_SWAP_LUA` because … (see #2083)".
- [ ] Fix the two stale `_heal_descriptor_pollution` comments (`models/agent_session.py:236`, `:335`) to name the real generic-heal behavior instead of the non-existent method.

## Success Criteria

- [x] `docs/features/popoto-descriptor-pollution-ledger.md` exists and inventories every Cluster A/B/C defense with file:line and a per-defense verdict.
- [x] Each defense marked redundant is removed AND has a Sentry ledger issue (org yudame, project 4511091961888768) linked in the ledger doc.
- [x] Each kept defense carries an explicit "not subsumed by 1.8.0 because …" comment referencing #2083.
- [x] For every removal, a regression test reproducing the original bug exists and passes under 1.8.0 without the removed defense.
- [x] The defensive `srem` (B3) has a #950 stale-object full-save red-state test, and NO B3 verdict was recorded before that test's WITH/WITHOUT-`srem` behavior under 1.8.0 was observed. B3 removed only if that test stays green without `srem`; otherwise KEPT with a keep-comment.
- [x] Both `_saved_field_values["status"]` backfill sites (B1 `finalize_session`, B2 `transition_status`) share a single disposition — no half-removal (one site deleted, the other left).
- [ ] If any Cluster B defense removed: an active stale-count detector (dry-run `repair_indexes`, alarm on `stale_count > 0`) is wired and tested — silent index drift is observable, not just passively Sentry-reported.
- [x] If B1/B2 backfill removed: an idempotent pointer-establishing migration is registered in `MIGRATIONS` and runs before the backfill deletion. If KEEP-all (the likely default): NO migration authored.
- [x] The two stale `_heal_descriptor_pollution` comments are corrected.
- [x] No removal made on optimism — every ambiguous case is KEPT with an Open Question recorded.
- [x] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Auditor (scar-tissue inventory)**
  - Name: scar-auditor
  - Role: Read-only inventory of both clusters + empirical verification of INDEX_SWAP_LUA scope and lazy-load reproduction; produces the ledger doc draft. Domain: redis/popoto.
  - Agent Type: Explore
  - Resume: true

- **Builder (removals + migration)**
  - Name: scar-remover
  - Role: Write regression tests, run the pointer migration, remove proven-dead defenses, add keep-comments, open Sentry ledger issues. Domain: redis/popoto, async/concurrency.
  - Agent Type: builder
  - Resume: true

- **Validator (race-safety)**
  - Name: scar-validator
  - Role: Verify every removal has a green regression test + ledger entry; verify kept defenses have keep-comments; verify migration idempotency.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: scar-doc
  - Role: Finalize the ledger doc + README index + inline comments.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Per template. Domain framing (redis/popoto, async/concurrency) pasted into scar-remover's assignment from `DOMAIN_FRAMING.md`.

## Step by Step Tasks

### 1. Read-only audit (GATING)
- **Task ID**: audit-scar-tissue
- **Depends On**: none
- **Validates**: produces `docs/features/popoto-descriptor-pollution-ledger.md` (draft); no code change
- **Informed By**: spike-1 (INDEX_SWAP_LUA covers indexed fields only), spike-2 (Cluster B gated on legacy rows)
- **Assigned To**: scar-auditor
- **Agent Type**: Explore
- **Parallel**: false
- Repo-wide grep for the REAL defensive symbols (never trust the `_heal_descriptor_pollution` name); confirm the Cluster A/B/C inventory with file:line. **Cluster B is THREE sites: B1 backfill `finalize_session:474-475`, B2 backfill `transition_status:713-714`, B3 defensive `srem` `:488-510` — inventory all three separately.**
- Empirically confirm `INDEX_SWAP_LUA` scope and that the lazy-load descriptor leak still reproduces under 1.8.0.
- **Empirically test the #950 stale-object FULL-save scenario against B3:** reproduce a stale in-memory object writing a superseded `status` via full `save()` under 1.8.0, and observe whether the row is stranded in the wrong index Set with `srem` removed. This determines whether `INDEX_SWAP_LUA`'s pointer atomicity covers value-freshness (working hypothesis: it does NOT → B3 KEEP).
- Decide, per defense: redundant (→ remove, gated) vs. load-bearing (→ keep) vs. out-of-scope (Cluster C). B1/B2 share one disposition. B3 defaults KEEP unless the #950 red-state test proves it dead. Record ambiguous cases as Open Questions (default KEEP).
- Determine whether B1/B2 removal (IF verdicted REMOVE) needs the pointer-establishing migration — do NOT pre-commit; KEEP-all ships no migration.

### 2. Regression tests for removal candidates
- **Task ID**: build-regression-tests
- **Depends On**: audit-scar-tissue
- **Validates**: `tests/unit/test_agent_session_index_corruption.py`, `tests/unit/test_agent_session.py` (add legacy-row descriptor + status-race repros)
- **Assigned To**: scar-remover
- **Agent Type**: builder
- **Parallel**: false
- For each REMOVE verdict, write a test reproducing the original bug; confirm it passes under 1.8.0 with the defense present, and characterize behavior without it.
- **Mandatory before ANY B3 verdict:** write the #950 stale-object full-save red-state test (green with `srem`, observed without it under 1.8.0). Also add the structural "both backfill sites move together" guard for B1/B2.

### 3. Pointer-establishing migration (CONDITIONAL — only if Task 1 verdicts B1/B2 REMOVE)
- **Task ID**: build-pointer-migration
- **Depends On**: audit-scar-tissue
- **Condition**: SKIP ENTIRELY if the audit verdict is KEEP-all for B1/B2 (the likely default). Do not author destructive schema work on optimism.
- **Validates**: `scripts/update/migrations.py`, `tests/` migration idempotency test
- **Assigned To**: scar-remover
- **Agent Type**: builder
- **Parallel**: false
- IF removal proceeds: add idempotent migration re-saving every live AgentSession once to establish the `status` index pointer; register in `MIGRATIONS`.

### 3b. Active stale-count detector (CONDITIONAL — only if any Cluster B defense is removed)
- **Task ID**: build-active-detector
- **Depends On**: audit-scar-tissue
- **Condition**: SKIP if no Cluster B removal ships.
- **Validates**: `agent-session-cleanup` reflection (or `reflections.yaml`), detector test
- **Assigned To**: scar-remover
- **Agent Type**: builder
- **Parallel**: false
- Wire a count-only `repair_indexes` dry-run into the hourly reflection; alarm to Sentry on `stale_count > 0`. This is the ACTIVE tripwire for silent index drift (CONCERN 2) — passive Sentry crash-capture is insufficient.

### 4. Remove proven-dead defenses + keep-comments + Sentry ledger
- **Task ID**: build-removals
- **Depends On**: build-regression-tests, build-pointer-migration (if run), build-active-detector (if run)
- **Validates**: regression tests still green without removed defenses; `models/agent_session.py`, `models/session_lifecycle.py`
- **Assigned To**: scar-remover
- **Agent Type**: builder
- **Parallel**: false
- Delete only proven-dead defenses (B3 only if the #950 red-state test proved it dead; B1/B2 only as a pair after the conditional migration). Add "not subsumed by 1.8.0 because … (see #2083)" comments on kept defenses (B3 by default carries the value-freshness rationale). Fix the two stale `_heal_descriptor_pollution` comments; open one Sentry issue per removal and link it in the ledger; confirm the active detector is live for any B-cluster removal.

### 5. Validate race-safety + ledger completeness
- **Task ID**: validate-removals
- **Depends On**: build-removals
- **Assigned To**: scar-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify every removal has a green regression test AND a Sentry ledger entry; every kept defense has a keep-comment; migration is idempotent; no removal made on optimism.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-removals
- **Assigned To**: scar-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Finalize `docs/features/popoto-descriptor-pollution-ledger.md`; add README index entry.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-removals, document-feature
- **Assigned To**: scar-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full verification table; confirm all success criteria (including docs + ledger).

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_agent_session_index_corruption.py tests/unit/test_agent_session.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Ledger doc exists | `test -f docs/features/popoto-descriptor-pollution-ledger.md` | exit code 0 |
| Popoto 1.8.0 active | `.venv/bin/python -c "import popoto; assert popoto.__version__=='1.8.0'"` | exit code 0 |
| No stale `_heal_descriptor_pollution` comment left uncorrected | `grep -rn "_heal_descriptor_pollution" models/agent_session.py \| grep -v "generic\|see #2083"` | match count == 0 |
| Every kept defense references #2083 (if any Cluster A comment mentions 1.8.0, it names the issue) | `grep -rn "1.8.0" models/agent_session.py models/session_lifecycle.py \| grep -i "subsume\|INDEX_SWAP" \| grep -v "2083"` | match count == 0 |

## Critique Results

Critique verdict: NEEDS REVISION (2026-07-14). All findings addressed in this revision pass.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | war-room | Removing the defensive `srem` (:488-510) without bringing its #950 stale-full-save trigger into scope; over-claims `INDEX_SWAP_LUA` covers value-freshness | Prior Art #950 row; Data Flow split into bug class 1a (covered) / 1b (NOT covered); Race Conditions split into 1a/1b; B3 verdict → KEEP by default; Task 1(d) stale-full-save validation; mandatory #950 red-state test before ANY B3 verdict | `INDEX_SWAP_LUA` guarantees pointer atomicity, not value freshness; a stale full save writes a stale value atomically. B3 stays unless red-state test proves it dead. |
| CONCERN 1 | war-room | Second `_saved_field_values["status"]` backfill at `transition_status:706-714` not named (half-removal risk) | Named as B2 throughout (Solution, Data Flow, Race 1a, Test Impact, Success Criteria); B1/B2 share one disposition; structural "both sites move together" test added | Half-removal is now an explicit defect condition. |
| CONCERN 2 | war-room | Sentry ledger is passive; index drift is silent (no throw) | Active stale-count detector (dry-run `repair_indexes`, alarm on `stale_count > 0`) added to Solution, Risk 3, Update System, Documentation, Success Criteria, new Task 3b | Built on `repair_indexes()`'s existing stale-count; wired into hourly reflection; conditional on a B-cluster removal shipping. |
| CONCERN 3 | war-room | Task graph pre-commits to destructive migration despite KEEP-all default | Migration made CONDITIONAL on Task-1 verdict (Technical Approach, Update System, No-Gos, Task 3 SKIP condition); KEEP-all default ships zero migration | Destructive schema work is not authored ahead of the audit verdict. |

---

## Open Questions

1. **Cluster B migration appetite:** Is a one-time pointer-establishing migration (re-save every live AgentSession once) acceptable to unlock B1/B2 backfill removal, or should we keep both backfill sites indefinitely as cheap belt-and-suspenders? Note the critique correction: the defensive `srem` (B3) is a SEPARATE concern (guards the #950 stale-value full save, which `INDEX_SWAP_LUA` does NOT cover) and defaults to KEEP regardless of the migration decision. (Default if unanswered: keep both backfills AND B3, author no migration, remove nothing that depends on legacy-row pointers or value-freshness.)
2. **Sentry ledger granularity:** One Sentry issue per removed defense, or a single umbrella "descriptor-pollution scar-tissue removal (#2083)" issue with per-defense tags? The removed-defenses-ledger convention implies per-defense mapping; confirm.
3. **Cluster A scope confirmation:** spike-1 says KEEP all of Cluster A. Is any Cluster A defense worth a removal attempt anyway (e.g., `response_delivered_at`'s extra #929 coercion, if that specific bug is provably 1.8.0-covered), or do we lock Cluster A as entirely load-bearing and move on?
