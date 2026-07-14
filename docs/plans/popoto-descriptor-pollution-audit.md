---
status: Planning
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-07-14
tracking: https://github.com/tomcounsell/ai/issues/2083
last_comment_id:
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

**Bug class 1 — cross-process index race (Cluster B):**
1. Process P1 loads AgentSession row (status="running"), process P2 loads the same row.
2. P1 `transition_status("completed")` → save; P2 does a stale full `save()` → both mutate the `status` secondary-index Sets.
3. Pre-1.8.0: client-side `SREM old / SADD new` interleave → member stranded in two Sets. `session_lifecycle.py:488-510` defensive `srem` and `:466-475` backfill compensate.
4. 1.8.0: `INDEX_SWAP_LUA` runs the swap atomically server-side using the hash pointer → no interleave possible.

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
- **Cluster B — status-index defenses**: `_saved_field_values["status"]` backfill + defensive `srem`-across-`ALL_STATUSES` in `session_lifecycle.py`. Preliminary verdict: REMOVE, gated behind a pointer-establishing migration.
- **Cluster C — adjacent / out of scope**: `_heal_future_updated_at`, `save()` `updated_at` stamp, `repair_indexes`, `cleanup_expired`. Verdict: confirm-and-keep (separate bug class).
- **Removed-defenses ledger**: every removal maps to a Sentry issue (org `yudame`, project `4511091961888768`) so a regression surfaces loudly.

### Flow

Audit (read-only) → PM confirms verdicts → for each REMOVE: write a regression test that reproduces the original bug → confirm it passes under 1.8.0 with the defense present → (if Cluster B) add + run the pointer-establishing migration → remove the defense → confirm the regression test still passes → open the Sentry ledger issue → commit. For each KEEP: add an explicit "not subsumed by 1.8.0 because …" comment referencing #2083.

### Technical Approach

- **Task 1 is a read-only audit** and gates everything else. It must not trust any comment, plan doc, or memory referencing `_heal_descriptor_pollution` by name — it inventories real symbols. It confirms (a) `INDEX_SWAP_LUA`'s scope empirically, (b) that the lazy-load leak still reproduces under 1.8.0 (load a hash missing a field, read it, assert descriptor vs. scalar), and (c) whether legacy rows need a migration before Cluster B removal.
- **Removals are gated on a passing regression test that would have caught the original bug** — written first, confirmed red without the defense on a legacy-shaped row (if it stays green without the defense under 1.8.0, the defense is proven dead).
- **Cluster B removal requires a Popoto migration** (`scripts/update/migrations.py`, registered in `MIGRATIONS`, idempotent) that re-saves every live AgentSession once under 1.8.0 to establish the server-authoritative pointer, run BEFORE the backfill is deleted. Use `instance.save()` / `Model.rebuild_indexes()` — never raw Redis ops.
- **Ambiguity defaults to KEEP** (issue AC #5): if it is unclear whether the atomic index covers a defense, keep it and record an Open Question rather than delete.
- Do NOT rewrite `docs/plans/completed/*.md` history — only update living docs whose claims become inaccurate.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `session_lifecycle.py:510` `except … logger.debug("Defensive srem failed (non-fatal)")` — if the defensive `srem` block is removed, delete the handler with it; if kept, the existing `test_defensive_srem_failure_is_nonfatal` covers it. Assert observable behavior either way.
- [ ] Cluster A `__getattribute__`/`__setattr__` coercions log at DEBUG on coercion — if kept, add/confirm a test asserting the log fires when a descriptor/bad-type sneaks through.

### Empty/Invalid Input Handling
- [ ] Reproduce the descriptor-leak on a legacy row: a hash missing `exit_returncode` / `response_delivered_at`, then read the attribute — assert scalar (not descriptor) with the defense; capture behavior without it.
- [ ] `response_delivered_at` with a `str` / stale value (#929) — assert coercion to datetime/None if the defense is kept.

### Error State Rendering
- [ ] No user-visible surface. Assert the failure modes surface via `logger` / Sentry rather than silent index corruption or a swallowed `save()` crash.

## Test Impact

- [ ] `tests/unit/test_agent_session_index_corruption.py::test_defensive_srem_code_exists_in_finalize` — UPDATE or DELETE: this test asserts the defensive `srem` *source string* is present in `finalize_session`. If Cluster B's defensive `srem` is removed, this structural test must be DELETED (it would otherwise pin the dead code in place); if kept, leave as-is.
- [ ] `tests/unit/test_agent_session_index_corruption.py::test_backfills_saved_field_values_on_lazy_session` (and the `:139`/`:352`/`:382` backfill variants) — UPDATE/DELETE: these pin the `_saved_field_values["status"]` backfill. Disposition follows the Task-1 verdict + migration decision for Cluster B.
- [ ] `tests/unit/test_agent_session_index_corruption.py` index-clearing tests (`:426`, `:469`, `:500`) — UPDATE: confirm they still pass against `INDEX_SWAP_LUA`'s atomic swap; adjust assertions that assume client-side `srem`.
- [ ] `tests/unit/test_session_health_trusted_clock.py` and `tests/unit/test_agent_session_updated_at_utc.py` `_heal_future_updated_at` tests — no change (Cluster C, out of scope) — listed to confirm they are untouched.
- [ ] New regression tests (ADD): one per REMOVE decision, reproducing the original bug (#1099/#1172 descriptor leak; the cross-process status-index race) — these are the safety net gating each deletion.

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

### Risk 3: Ledger tripwire never fires (removal regresses silently)
**Impact:** A removed defense's bug recurs in production without detection.
**Mitigation:** Each removal opens a real Sentry issue (org yudame, project 4511091961888768) tagged to the specific failure signature; the happy-path save reports the crash/anomaly to Sentry so it maps to the ledger entry.

## Race Conditions

### Race 1: Concurrent cross-process status transition
**Location:** `models/session_lifecycle.py:466-510`; `popoto/fields/indexed_field_mixin.py:97` (INDEX_SWAP_LUA)
**Trigger:** Worker and bridge (or two sessions) `save()` the same AgentSession with different `status` values within the same window.
**Data prerequisite:** The server-authoritative pointer (`status\x00idxset`) must exist in the model hash for INDEX_SWAP_LUA to remove from the correct old Set without the client-side snapshot.
**State prerequisite:** All live rows must have been re-saved at least once under 1.8.0 (migration) before the client-side backfill compensator is removed.
**Mitigation:** INDEX_SWAP_LUA runs the SREM-old/SADD-new/write-pointer as a single atomic server-side Lua script — no client interleave possible. The pointer-establishing migration ensures the prerequisite holds before removal; until then, keep the backfill.

### Race 2: Lazy-load read racing a field-adding deploy
**Location:** `models/agent_session.py:643-732`; `popoto/models/base.py:728`
**Trigger:** A row written before a field existed is lazy-loaded and the field is read.
**Data prerequisite:** N/A — this is a schema-evolution hazard, not a concurrency one; INDEX_SWAP_LUA is irrelevant here.
**Mitigation:** Cluster A `__getattribute__`/`__setattr__` heal (KEEP). This race is unaffected by 1.8.0.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1720] The `rebuild_indexes()` class-set delete→re-add window and its read-path bounded retries (`tools/valor_session.py`, `tools/sdlc_stage_query.py`) are a distinct concern from the save-race; confirm-and-keep only, do not modify in this plan.
- [DESTRUCTIVE] The one-time migration re-saving all live AgentSession rows to establish pointers is idempotent and recorded once in `data/migrations_completed.json`; it is reviewed before merge as the safety mechanism (it writes to every live session hash).
- Rewriting Popoto internals or filing upstream popoto changes — this plan is AgentSession-level only.
- Editing `docs/plans/completed/*.md` historical references — left as historical record.

## Update System

- **Migration required (Popoto model):** if Cluster B removal proceeds, add an idempotent migration to `scripts/update/migrations.py` (registered in the `MIGRATIONS` dict) that re-saves every live AgentSession once under 1.8.0 to establish the server-authoritative index pointer, run BEFORE the `_saved_field_values["status"]` backfill is deleted. Uses `instance.save()` / `Model.rebuild_indexes()` — no raw Redis ops.
- No new dependencies to propagate (Popoto 1.8.0 already shipped via #2081).
- No `scripts/update/run.py` changes beyond migration registration.

## Agent Integration

No agent integration required — this is an internal data-model audit and cleanup. No new MCP surface, no `.mcp.json` change, no `bridge/telegram_bridge.py` change. The audit report and ledger are Markdown docs; the code changes are internal to `models/`.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/popoto-descriptor-pollution-ledger.md` — the audit report + removed-defenses ledger: per-defense inventory (file:line), verdict (removed/kept), reasoning tied to `INDEX_SWAP_LUA`'s guarantees, and the Sentry issue link for each removal.
- [ ] Add an entry to `docs/features/README.md` index table.

### Inline Documentation
- [ ] For every KEPT defense: add/refresh a comment stating "NOT subsumed by Popoto 1.8.0 `INDEX_SWAP_LUA` because … (see #2083)".
- [ ] Fix the two stale `_heal_descriptor_pollution` comments (`models/agent_session.py:236`, `:335`) to name the real generic-heal behavior instead of the non-existent method.

## Success Criteria

- [ ] `docs/features/popoto-descriptor-pollution-ledger.md` exists and inventories every Cluster A/B/C defense with file:line and a per-defense verdict.
- [ ] Each defense marked redundant is removed AND has a Sentry ledger issue (org yudame, project 4511091961888768) linked in the ledger doc.
- [ ] Each kept defense carries an explicit "not subsumed by 1.8.0 because …" comment referencing #2083.
- [ ] For every removal, a regression test reproducing the original bug exists and passes under 1.8.0 without the removed defense.
- [ ] If Cluster B removed: an idempotent pointer-establishing migration is registered in `MIGRATIONS` and runs before the backfill deletion.
- [ ] The two stale `_heal_descriptor_pollution` comments are corrected.
- [ ] No removal made on optimism — every ambiguous case is KEPT with an Open Question recorded.
- [ ] Tests pass (`/do-test`)
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
- Repo-wide grep for the REAL defensive symbols (never trust the `_heal_descriptor_pollution` name); confirm the Cluster A/B/C inventory with file:line.
- Empirically confirm `INDEX_SWAP_LUA` scope and that the lazy-load descriptor leak still reproduces under 1.8.0.
- Decide, per defense: redundant (→ remove, gated) vs. load-bearing (→ keep) vs. out-of-scope (Cluster C). Record ambiguous cases as Open Questions (default KEEP).
- Determine whether Cluster B removal needs the pointer-establishing migration.

### 2. Regression tests for removal candidates
- **Task ID**: build-regression-tests
- **Depends On**: audit-scar-tissue
- **Validates**: `tests/unit/test_agent_session_index_corruption.py`, `tests/unit/test_agent_session.py` (add legacy-row descriptor + status-race repros)
- **Assigned To**: scar-remover
- **Agent Type**: builder
- **Parallel**: false
- For each REMOVE verdict, write a test reproducing the original bug; confirm it passes under 1.8.0 with the defense present, and characterize behavior without it.

### 3. Pointer-establishing migration (only if Cluster B removed)
- **Task ID**: build-pointer-migration
- **Depends On**: audit-scar-tissue
- **Validates**: `scripts/update/migrations.py`, `tests/` migration idempotency test
- **Assigned To**: scar-remover
- **Agent Type**: builder
- **Parallel**: false
- Add idempotent migration re-saving every live AgentSession once to establish the `status` index pointer; register in `MIGRATIONS`.

### 4. Remove proven-dead defenses + keep-comments + Sentry ledger
- **Task ID**: build-removals
- **Depends On**: build-regression-tests, build-pointer-migration
- **Validates**: regression tests still green without removed defenses; `models/agent_session.py`, `models/session_lifecycle.py`
- **Assigned To**: scar-remover
- **Agent Type**: builder
- **Parallel**: false
- Delete each proven-dead defense; add "not subsumed by 1.8.0 (see #2083)" comments on kept defenses; fix the two stale `_heal_descriptor_pollution` comments; open one Sentry issue per removal and link it in the ledger.

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

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Cluster B migration appetite:** Is a one-time pointer-establishing migration (re-save every live AgentSession once) acceptable, or should we keep the `_saved_field_values["status"]` backfill indefinitely as cheap belt-and-suspenders and only remove the defensive `srem` loop? (Default if unanswered: keep the backfill, remove nothing that depends on legacy-row pointers.)
2. **Sentry ledger granularity:** One Sentry issue per removed defense, or a single umbrella "descriptor-pollution scar-tissue removal (#2083)" issue with per-defense tags? The removed-defenses-ledger convention implies per-defense mapping; confirm.
3. **Cluster A scope confirmation:** spike-1 says KEEP all of Cluster A. Is any Cluster A defense worth a removal attempt anyway (e.g., `response_delivered_at`'s extra #929 coercion, if that specific bug is provably 1.8.0-covered), or do we lock Cluster A as entirely load-bearing and move on?
