---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-20
tracking: https://github.com/tomcounsell/ai/issues/1069
last_comment_id:
---

# agent-session-cleanup phantom-record guard

## Problem

The `agent-session-cleanup` reflection, scheduled hourly in `config/reflections.yaml` and implemented at `agent/session_health.py:1052-1145` (`cleanup_corrupted_agent_sessions`), is destroying legitimate `AgentSession` records rather than corrupt ones.

**Current behavior:**

On every run, it logs:

```
[agent-session-cleanup] Corrupted session detected: id=<popoto.fields.short (length 60, expected 32), session_id=<popoto.fields.field.Field object at 0x109cd39d0>
[agent-session-cleanup] Rebuilt AgentSession indexes after cleaning 2 corrupted session(s)
```

The `id=<popoto.fields.short ...>` is a Popoto **Field class descriptor repr**, not a 32-character uuid4. This means `AgentSession.query.all()` is yielding **phantom rows** — list items where attribute access falls through to the class-level field descriptor instead of a real hydrated value. Phantoms are triggered by orphan `$IndexF:AgentSession:...` set members that point to Redis hashes which no longer exist. The cleanup misreads the descriptor repr (~60 chars) as the `id` value, the length check (32 ≠ 60) flags it as corrupt, and `session.delete()` then damages **real records whose indexed-field values happen to match**, because ORM delete uses indexed fields to identify the target.

Observed impact (2026-04-20): three PM `AgentSession` records for issues #1060, #1064, #1061 were destroyed within minutes of being enqueued. PR #1068 (for #1060) only shipped because its first dev turn completed before the next cleanup tick fired. Sessions for #1064 and #1061 never executed across two retries.

**Desired outcome:**

1. `cleanup_corrupted_agent_sessions` never deletes a hydrated session — only records that are both (a) real instances and (b) actually unsaveable under the existing ID-length or `.save()` validation checks.
2. Orphan `$IndexF:AgentSession:*` members pointing to deleted hashes are removed at the source, so subsequent `query.all()` / `query.filter(...)` calls stop yielding phantoms.
3. Sibling functions that iterate `AgentSession.query.*` without phantom-filtering are hardened against the same class of failure so the bug cannot reappear one call site over.

## Freshness Check

**Baseline commit:** `8fd4554c66419d8a3f4293503f2ec7ae41d27eb9`
**Issue filed at:** 2026-04-20T05:15:18Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/session_health.py:1052-1145` (`cleanup_corrupted_agent_sessions`) — still holds. Exact lines match the issue, including the buggy `str(getattr(session, "id", "") or "")`, the 32-vs-60 length check, the `session.delete()` call, and the raw-Redis `r.scan_iter` / `r.delete` fallback at 1114-1122.
- `agent/session_health.py:900-902` (phantom-filter reference pattern, `isinstance(s.agent_session_id, str)`) — still holds.
- `agent/session_health.py:105` (`_recover_interrupted_agent_sessions_startup`) — still holds, uses `AgentSession.query.filter(status="running")`.
- `agent/session_health.py:475` (`_agent_session_health_check`) — still holds, same filter.
- `agent/sustainability.py:143-146` (`session_recovery_drip`) — still holds.
- `agent/sustainability.py:278` (`session_count_throttle`) — still holds.
- `agent/sustainability.py:334` (`failure_loop_detector`) — still holds.
- `models/agent_session.py:1474-1500` (`repair_indexes`) — still holds. The method explicitly clears `$IndexF:AgentSession:*` sets before calling `rebuild_indexes()`.

**Cited sibling issues/PRs re-checked:**
- #738 — CLOSED 2026-04-06; precedent for cleanup-kills-live-sessions pattern.
- #822 / PR #826 — MERGED; another cleanup-destroys-work incident (interrupted sessions marked completed).
- #950 / PR #954 — MERGED 2026-04-14; prevents stale-save index orphans on killed transitions. Same class of bug (index diverges from hashes), different trigger.
- #1038 — CLOSED 2026-04-18; Popoto binary-field crash on raw Redis reads. Motivates the ORM-only policy enforced by `.claude/hooks/validators/validate_no_raw_redis_delete.py`.
- PR #650 — MERGED 2026-04-03; "Popoto ORM hygiene: refactor raw Redis ops + orphaned index cleanup." Did not cover the `IndexedField` orphan case, which is exactly what this plan fixes.
- PR #751 — MERGED 2026-04-06; introduced `cleanup_corrupted_agent_sessions` with the empirical 32-vs-60 workaround for an observed ID-corruption incident.
- PR #1051 — MERGED 2026-04-19; moved `cleanup_corrupted_agent_sessions` from `agent/agent_session_queue.py` into `agent/session_health.py` as part of a broader file split. `agent_session_queue.py` re-exports it (backward-compat shim), so the reflection callable `agent.agent_session_queue.cleanup_corrupted_agent_sessions` still resolves correctly.

**Commits on main since issue was filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** none.

**Drift note (MINOR):** The issue says `enabled: false` is currently set on `agent-session-cleanup` in `config/reflections.yaml` as a temp mitigation. Current main has `enabled: true` — the disable was never committed. The reflection is therefore **actively running destructively in production until this fix lands**. The plan keeps `enabled: true` (which is already the state) after the fix, so the re-enable acceptance criterion is effectively a no-op assertion that the value remains `true`.

## Prior Art

Searched closed issues and merged PRs for related work:

- **Issue #738** (CLOSED 2026-04-06): "fix: stale session cleanup kills live sessions and corrupts state on forced termination" — precedent for destructive cleanup logic; shows this failure mode category recurs and the fix lives at the iteration layer, not in the mutation.
- **PR #826** (MERGED, for #822): "fix: worker restart preserves pending sessions and re-queues interrupted running" — another cleanup-destroys-work incident; fixed by tightening the status-transition matrix, not by filtering phantoms. Different root cause.
- **PR #954** (MERGED, for #950): "fix(lifecycle): prevent stale-save index orphans on killed transitions" — closest prior art. Addressed `IndexedField` orphans *at write time* (stale-save on killed transitions). The current issue is the read-side counterpart: orphans that already exist (from historical stale saves, crashed writes, or TTL expiry) must be filtered out on iteration *and* cleaned at the source via `repair_indexes()`. Together #954 + this plan close the ring.
- **PR #650** (MERGED 2026-04-03): "Popoto ORM hygiene: refactor raw Redis ops + orphaned index cleanup" — introduced the ORM-only policy and some orphan-cleanup scaffolding. Did not cover `IndexedField` orphans, which is what `repair_indexes()` (added later in `models/agent_session.py:1474-1500`) fixes.
- **PR #751** (MERGED 2026-04-06): "Enforce bridge/worker separation" — introduced `cleanup_corrupted_agent_sessions`. The 32-vs-60 length check was empirical, designed to catch an ID-generation bug at the time, not a principled invariant. The plan preserves this branch (per issue constraint) but shields it behind the phantom filter.
- **PR #1051** (MERGED 2026-04-19): "refactor: split agent_session_queue.py" — moved the function to `session_health.py`. Logic unchanged. This plan edits the new location.

## Why Previous Fixes Failed

The issue surfaces a recurring pattern: Redis indexes that diverge from their backing hashes cause misclassification and collateral damage at iteration time. Each prior attempt fixed a different symptom:

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|-----------------------|
| PR #650 | Refactored raw Redis ops, added some orphan cleanup scaffolding | Did not cover `IndexedField` (`$IndexF:`) orphans — only `KeyField` / `SortedField` via Popoto's built-in `rebuild_indexes()` |
| PR #751 | Added `cleanup_corrupted_agent_sessions` with empirical 32-vs-60 check | Iterated `query.all()` without guarding against phantom rows; the length check then matched descriptor reprs |
| PR #954 | Backfilled `_saved_field_values` on killed transitions to prevent new stale saves | Did not clean pre-existing orphans; iteration-time defenses were still missing |

**Root cause pattern:** Each PR addressed the symptom at one layer — write-time invariants (#954), cleanup scaffolding (#650), or an empirical corruption signature (#751) — without asserting the universal invariant at the iteration boundary: **every element returned by `AgentSession.query.*` must be hydrated before it is read for mutation decisions.** This plan makes that invariant explicit via a shared filter and a companion `repair_indexes()` call that removes orphan members at the source.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** none to public APIs. Adds one internal helper `_filter_hydrated_sessions(sessions)` in `agent/session_health.py`.
- **Coupling:** decreases. The helper centralizes the phantom-guard idiom that is currently duplicated-by-copy at `session_health.py:900`. Five sibling call sites gain the same guard for near-zero additional code.
- **Data ownership:** unchanged. Orphan cleanup continues to flow through Popoto (`repair_indexes()`); no call site introduces raw Redis writes.
- **Reversibility:** trivial. The filter is additive (it never converts a hydrated record into a phantom); revert by deleting the helper and its call sites.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope alignment on sibling-function hardening — whether all 5 call sites require the filter or whether some are verifiably phantom-safe)
- Review rounds: 1 (PR review after tests pass)

Coding time is small (roughly 40–80 LOC changed across 2 files, plus a regression-test file). The bottleneck is correctness review of the phantom-filter semantics and verification that the regression tests actually seed an orphan index entry in the way the production bug manifests.

## Prerequisites

No prerequisites — this work has no external dependencies. Redis is already required for the test suite (`tests/conftest.py` bootstraps a test Redis), and Popoto is already a first-class dependency.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Local Redis available for tests | `python -c "import redis; redis.Redis().ping()"` | Popoto ORM + regression tests need Redis |

## Solution

### Key Elements

- **`_filter_hydrated_sessions(sessions)` helper** — a module-level function in `agent/session_health.py` that returns only session instances whose key identity fields are hydrated strings. Canonical check: `isinstance(s.agent_session_id, str)`. This matches the existing pattern at `session_health.py:900-902` and the issue's acceptance criterion verbatim.
- **Phantom guard at `cleanup_corrupted_agent_sessions` iteration** — apply the filter immediately after `AgentSession.query.all()`, before any `session_id_str` / `getattr(session, "id", "")` / length check runs. Phantoms never reach the mutation path.
- **Source-level orphan cleanup via `repair_indexes()`** — replace the terminal `AgentSession.rebuild_indexes()` call with `AgentSession.repair_indexes()`. `repair_indexes()` (already exists at `models/agent_session.py:1474-1500`) explicitly clears `$IndexF:AgentSession:*` sets *before* `rebuild_indexes()` runs, which is the only way to remove orphan members that point to deleted hashes (rebuild alone only reconstructs from surviving hashes).
- **Raw-Redis fallback removal** — delete the dead `r.scan_iter(match=pattern)` / `r.delete(key)` branch at `session_health.py:1114-1122`. It is unreachable under the phantom filter (phantoms don't get to delete) and violates the ORM-only policy enforced by `.claude/hooks/validators/validate_no_raw_redis_delete.py`.
- **Sibling-function hardening** — apply `_filter_hydrated_sessions` to the five other `AgentSession.query.*` iterators the issue calls out. For the three read-only call sites in `sustainability.py`, the filter is cheap insurance; for the two destructive call sites in `session_health.py` (`_recover_interrupted_agent_sessions_startup`, `_agent_session_health_check`), the filter is load-bearing — phantoms reaching those code paths would cause the same class of damage as the current bug.
- **`enabled: true` invariant** — the reflection is already `enabled: true` on main (see Freshness Check drift note); the plan asserts it remains so after the fix. No yaml change needed; a test asserts the current value.

### Flow

Reflection scheduler tick → `cleanup_corrupted_agent_sessions()` → `AgentSession.query.all()` → `_filter_hydrated_sessions(...)` (phantoms dropped) → per-session length + `.save()` validation → `session.delete()` on real corrupt records only → `AgentSession.repair_indexes()` clears orphan `$IndexF` members and rebuilds all indexes → subsequent `query.all()` returns only hydrated records.

### Technical Approach

- **Placement of the helper:** `_filter_hydrated_sessions(sessions: Iterable) -> list[AgentSession]` goes in `agent/session_health.py` near the top of the module (module-level private helper). It is imported by `agent/sustainability.py` call sites. Both modules already import from each other's neighborhood (shared `AgentSession` model, shared `_TERMINAL_STATUSES`), so this adds no new circular-import risk.
- **Canonical hydration check:** `isinstance(s.agent_session_id, str)`. `agent_session_id` is a `KeyField` and is the first attribute Popoto populates on hydration; if it's still a `Field` descriptor, the instance is a phantom. This matches `session_health.py:900` exactly and is what the issue's acceptance criterion specifies.
- **Fail-closed semantics:** The filter DROPS phantoms silently (with a structured `logger.debug(...)` for traceability). It does NOT attempt to heal them by calling `.delete()` — that's what caused the bug. Phantoms are cleaned at the source by `repair_indexes()` at the end of the function.
- **`repair_indexes()` vs `rebuild_indexes()`:** Only call `repair_indexes()` when `cleaned > 0` OR when phantoms were observed in the filter. The second condition is the load-bearing one: even if no "corrupt" records were deleted, phantoms being present means orphan `$IndexF` members exist and must be cleared. The function returns `(stale_count, rebuilt_count)` so the log line can distinguish "deleted 0 corrupt records but cleared N orphan index members."
- **Counter semantics:** Keep returning the count of deleted real corrupt records. Add a second counter `phantoms_filtered` that is logged but NOT returned (callers that inspect the return value are counting destructive work, not phantom filtering).
- **Raw-Redis fallback deletion:** Remove lines 1114-1122 entirely. The outer `session.delete()` at line 1105 already wraps errors in a `try/except` — if it fails, log and move on rather than escalate to raw Redis. This aligns with the ORM-only policy.
- **Sibling call-site filtering:** At each of the 5 listed call sites, wrap the `list(AgentSession.query.filter(...))` result with `_filter_hydrated_sessions(...)`. For `_agent_session_health_check` specifically — which already has a terminal-status guard at lines 483-491 — the hydration filter must run *before* the terminal-status check, because `getattr(entry, "status", None)` on a phantom returns a `Field` descriptor, which would slip past `actual_status in _TERMINAL_STATUSES` (descriptors are not in `_TERMINAL_STATUSES`).

## Data Flow

1. **Entry point:** reflection scheduler (launchd/worker loop) invokes `agent.agent_session_queue.cleanup_corrupted_agent_sessions` once per hour (configured in `config/reflections.yaml:37-43`). The callable path resolves to the re-exported binding in `agent/agent_session_queue.py:84` → the real implementation in `agent/session_health.py:1052`.
2. **Query layer:** `AgentSession.query.all()` iterates the Popoto class-level Redis set of `AgentSession` member IDs, fetches each hash, and constructs a `Model` instance per entry. If a member ID points to a non-existent hash (orphan), Popoto logs `POPOTO.Query ERROR one or more redis keys points to missing objects` and emits a **phantom instance** — an `AgentSession` with attribute access falling through to class-level `Field` descriptors.
3. **Filter layer (new):** `_filter_hydrated_sessions(...)` reads `s.agent_session_id` on each session. Hydrated sessions return a 32-char uuid string; phantoms return a `popoto.fields.KeyField` descriptor. The filter keeps only the first group.
4. **Validation layer:** for each hydrated session, run the 32-vs-60 length check on `str(getattr(session, "id", ""))` (now guaranteed to be a real string) and the no-op `.save()` validation. Both are preserved per the issue's constraint.
5. **Mutation layer:** `session.delete()` on confirmed corrupt records. Because the input is now guaranteed hydrated, ORM delete operates on the correct instance and does not collateral-damage siblings.
6. **Orphan cleanup layer (new):** `AgentSession.repair_indexes()` scans `$IndexF:AgentSession:*` sets, removes members pointing to deleted hashes, then rebuilds all indexes. Returns `(stale_count, rebuilt_count)`.
7. **Output:** returns the count of deleted real corrupt records to the reflection scheduler. Phantom count and orphan-cleanup stats are logged at INFO but not returned.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `session_health.py:1107-1128` (the broad `except Exception: ... Fallback: direct Redis key deletion` block) is being DELETED — no test needed; the deletion removes the swallowed-exception branch.
- [ ] `session_health.py:1139-1140` (`except Exception as idx_err: logger.warning(...)` on index rebuild failure) — preserve current behavior; no new test required because the existing semantic (log-and-continue on index failure) is correct.
- [ ] `_filter_hydrated_sessions` itself MUST NOT have a bare `except Exception: pass`. If `s.agent_session_id` access raises an unexpected exception (not a descriptor return), the record is treated as a phantom (filtered out) with a `logger.warning(...)` — observable via caplog.

### Empty/Invalid Input Handling

- [ ] `_filter_hydrated_sessions([])` returns `[]` — explicit test.
- [ ] `_filter_hydrated_sessions([phantom_only])` returns `[]` and emits a debug log per phantom — explicit test.
- [ ] `cleanup_corrupted_agent_sessions()` with zero sessions returns 0 and does NOT call `repair_indexes()` — existing behavior, confirmed.
- [ ] `cleanup_corrupted_agent_sessions()` with zero corrupt records but phantoms present DOES call `repair_indexes()` — new test, load-bearing.

### Error State Rendering

Not user-visible. Logs are operator-facing. Verify log messages at INFO/WARNING are informative (phantom count, orphan-cleanup count, per-corrupt-record detail) via caplog assertions.

## Test Impact

- [ ] `tests/unit/test_worker_entry.py::test_worker_calls_cleanup_corrupted` — **UPDATE (no-op)**: still passes as-is. Tests only that the string `cleanup_corrupted_agent_sessions` appears in `worker/__main__.py`. The rename or relocation is not in scope.
- [ ] `tests/unit/test_worker_entry.py` (line 378 onward, the startup-order test) — **UPDATE (no-op)**: still passes. Tests the relative call order of `run_cleanup`, `cleanup_corrupted_agent_sessions`, `_recover_interrupted_agent_sessions_startup`, `_cleanup_orphaned_claude_processes`, `_ensure_worker`. This plan does not change call order.
- [ ] `tests/unit/test_agent_session_index_corruption.py` — **UPDATE (no-op)**: existing test suite for index corruption under lazy loads. This plan adds a separate layer (iteration-time phantom filter), not a change to lazy-load behavior. Existing tests should continue to pass.
- [ ] `tests/unit/test_agent_session.py`, `tests/unit/test_agent_session_hierarchy.py`, etc. — **UPDATE (no-op)**: no existing test targets `cleanup_corrupted_agent_sessions` destructive behavior directly.
- [ ] `tests/unit/test_session_health_phantom_guard.py` — **CREATE (new test file)**. Contains the regression tests specified by the issue:
  1. Seed an orphan `$IndexF:AgentSession:status:pending` member pointing to a deleted hash, run `cleanup_corrupted_agent_sessions`, assert: (a) no real sessions deleted, (b) orphan cleaned after the run, (c) `AgentSession.query.all()` returns only hydrated records.
  2. After seeding orphan, create a live `AgentSession` with matching indexed field values (e.g., `status="pending"`), run cleanup, assert the live record survives.
  3. Phantom-only `query.all()` result → `_filter_hydrated_sessions` returns empty list, caplog shows debug entries.
  4. Mixed hydrated + phantom → filter keeps hydrated only.
  5. `cleanup_corrupted_agent_sessions` on zero sessions → does not call `repair_indexes` (preserves existing behavior).
  6. `cleanup_corrupted_agent_sessions` with phantoms present but no deletable corrupt records → DOES call `repair_indexes` and logs phantom count.
- [ ] `tests/unit/test_session_health_sibling_phantom_safety.py` — **CREATE (new test file)**. For each of the 5 sibling call sites, assert that the function completes without raising `TypeError` / `AttributeError` when `AgentSession.query.*` yields a mix of hydrated records and phantoms. Uses the same orphan-seeding helper as the first new test file.
- [ ] `tests/unit/test_reflections_config.py` (if it exists — **CREATE if absent**): assert `agent-session-cleanup` appears in `config/reflections.yaml` with `enabled: true`. Guards against an accidental disable lingering after hotfix deployment.

No existing behavioral tests of `cleanup_corrupted_agent_sessions` exist (verified via grep; only `test_worker_entry.py` mentions the symbol at all, and only as a string-existence check). This is greenfield test coverage for the destructive path, created specifically to prevent regression.

## Rabbit Holes

- **Re-evaluating the 60-vs-32 length check.** The issue explicitly defers this ("Dropped" list). Tempting because the empirical check looks suspicious now that the phantom vector is understood, but the `.save()`-validation branch (Check 2) still has value for real ID-generation bugs. Defer to a separate investigation.
- **Adding a dry-run mode.** The issue explicitly drops this. Once the phantom filter lands, the function is safe by construction. A dry-run mode adds a new code path to maintain for no lasting value.
- **Refactoring `repair_indexes()` upstream into Popoto.** The gap between `rebuild_indexes()` and `repair_indexes()` is real and worth fixing in Popoto itself, but this plan uses the existing method and does not modify the library. Out of scope.
- **Touching `_create_lazy_model` / `_saved_field_values` semantics.** That's the neighboring-but-separate bug class addressed by PR #954. Iteration-time filtering does not require modifying Popoto internals.
- **Generalizing the hydration filter to all Popoto models.** Tempting because other models (e.g., `Memory`) might have the same vulnerability, but the call sites in scope are all `AgentSession`-specific. Generalization is its own project.
- **Migrating the yaml callable path from `agent.agent_session_queue` to `agent.session_health`.** PR #1051 intentionally left the re-export in place for backward compatibility. Changing the callable would require coordinated machine deployments and is unrelated to this bug.
- **Rewriting `cleanup_corrupted_agent_sessions` to use `query.keys(clean=True)`.** Popoto's `clean=True` only touches class set + KeyField/Relationship indexes, NOT `$SortF` or `$IndexF`. The issue specifically documents this limitation. `repair_indexes()` is the right tool.

## Risks

### Risk 1: Phantom filter over-rejects valid records

**Impact:** A hydrated session that, for some reason not yet seen in production, returns a non-`str` `agent_session_id` would be silently dropped from cleanup iteration. Cleanup becomes a no-op for it, and the record could linger.

**Mitigation:** The existing sibling `_agent_session_hierarchy_health_check` at line 900 uses the exact same check and has been in production without false-rejection reports. Additionally, the filter logs at WARNING (not DEBUG) whenever it drops a record that also has any non-descriptor attribute present — operators will see false positives within one reflection cycle if the check is too tight.

### Risk 2: `repair_indexes()` is slower than `rebuild_indexes()`

**Impact:** `repair_indexes()` does an extra pass over `$IndexF:*` keys before rebuilding. On a Redis instance with many sessions, this adds latency to the cleanup reflection tick (hourly cadence, so a ~few-seconds increase is acceptable).

**Mitigation:** `repair_indexes()` only runs when `cleaned > 0 OR phantoms_filtered > 0`. Empty-pass runs remain cheap (just `query.all()` + filter + no-op return). Latency is bounded by the number of `$IndexF` keys for `AgentSession`, which is small (status, project_key, worker_key, session_type, etc.).

### Risk 3: Sibling-function hardening changes read-only call sites' return behavior

**Impact:** `session_count_throttle` and `failure_loop_detector` currently iterate `query.filter(project_key=...)` and read fields. Phantoms slipping through would raise `AttributeError` (bad) but not corrupt data. Adding the filter changes the returned list. A downstream assumption about list length could break.

**Mitigation:** Both functions use the list only for length-comparison and per-element reads (`s.status`, `s.started_at`, `s.completed_at`). Dropping phantoms can only REDUCE the list (never grow it) and can only remove non-readable entries. Any length-based assertion that previously counted phantoms was already incorrect. Validate via new test file `test_session_health_sibling_phantom_safety.py`.

### Risk 4: Orphan-seeding test setup doesn't reproduce the production failure mode

**Impact:** If the test seeds an orphan via a path that diverges from how production orphans actually form (e.g., crashed `save()` vs. TTL expiry vs. manual `DEL` on a hash), the regression test passes but the real bug remains.

**Mitigation:** Seed orphans via the Popoto-internal Redis connection using the exact key format the production logs show (`$IndexF:AgentSession:status:pending` set with a member pointing to a hash key that doesn't exist). Verify the seeded state reproduces the `POPOTO.Query ERROR one or more redis keys points to missing objects` log line that appears in production. If the log line doesn't fire in the test, the seeding is wrong.

## Race Conditions

No race conditions identified. The reflection scheduler runs `cleanup_corrupted_agent_sessions` as a single synchronous call per tick (hourly). Within the call, `AgentSession.query.all()` returns a materialized list; iteration does not re-read from Redis per element. `repair_indexes()` is atomic at the per-key level (Popoto internally uses `DEL` on the full index key then rebuilds), so there is no window where a concurrent `save()` from another process could race with a partially-rebuilt index — if it tries to, the worst case is a missing member that the next reflection tick will rebuild.

## No-Gos (Out of Scope)

- Adding a dry-run mode to `cleanup_corrupted_agent_sessions` — issue explicitly drops this.
- Re-evaluating the 60-vs-32 length check — issue explicitly defers this.
- Generalizing `_filter_hydrated_sessions` to other Popoto models (e.g., `Memory`, `SessionLog`) — scoped to `AgentSession` only.
- Refactoring `repair_indexes()` into Popoto upstream — use the existing method.
- Migrating the reflections.yaml callable path from `agent_session_queue` to `session_health` — maintain backward-compat shim.
- Touching the bridge's nudge loop or output-router code paths — unrelated to this bug.
- Modifying the `.save()` validation check (`is_corrupt` Check 2) — preserved verbatim per issue constraint.
- Adding new raw-Redis reads or writes anywhere in the fix — explicitly forbidden by ORM-only policy.

## Update System

No update system changes required — this is a purely internal code fix. The `/update` skill pulls latest code and restarts services; nothing in the update flow needs modification. The fix takes effect on the next worker restart after deployment.

## Agent Integration

No agent integration required — this is a background reflection running on the worker process, invoked by the reflection scheduler. The Telegram agent does not call `cleanup_corrupted_agent_sessions` directly and no new MCP tool is introduced. The bridge / worker / agent split remains unchanged.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/reflections.md` (if it exists and documents the agent-session-cleanup reflection) to note the phantom-filter invariant and the `repair_indexes()` cleanup step. Skip if the doc doesn't mention this reflection by name.
- [ ] Update `docs/features/bridge-self-healing.md` if it references `cleanup_corrupted_agent_sessions` — add a one-sentence note that cleanup filters phantoms before mutation.
- [ ] Check `docs/features/README.md` index — no new entry needed; this is a bug fix, not a new feature.

### Inline Documentation
- [ ] Docstring on `_filter_hydrated_sessions` explaining: what a phantom is, why the filter is needed, which attribute is the canonical hydration marker, and a cross-reference to `session_health.py:900` as the established pattern.
- [ ] Update docstring on `cleanup_corrupted_agent_sessions` (lines 1053-1067) to mention the phantom-filter step and the switch from `rebuild_indexes()` to `repair_indexes()`.
- [ ] Inline comment at each sibling call site referencing the shared helper.

### External Documentation Site
No external docs site in this repo.

## Success Criteria

- [ ] `cleanup_corrupted_agent_sessions` applies `_filter_hydrated_sessions` before any iteration over session attributes.
- [ ] `_filter_hydrated_sessions` exists in `agent/session_health.py` with `isinstance(s.agent_session_id, str)` as the check.
- [ ] `cleanup_corrupted_agent_sessions` calls `AgentSession.repair_indexes()` (NOT `rebuild_indexes()`) at the terminal cleanup step.
- [ ] Raw-Redis fallback at `session_health.py:1114-1122` is removed entirely.
- [ ] The 5 sibling functions (`_recover_interrupted_agent_sessions_startup`, `_agent_session_health_check`, `session_recovery_drip`, `session_count_throttle`, `failure_loop_detector`) apply `_filter_hydrated_sessions` to their `AgentSession.query.*` results.
- [ ] `config/reflections.yaml` has `enabled: true` for `agent-session-cleanup` (already true on main; verify unchanged).
- [ ] New test file `tests/unit/test_session_health_phantom_guard.py` contains regression tests 1–6 from the Test Impact section.
- [ ] New test file `tests/unit/test_session_health_sibling_phantom_safety.py` verifies sibling-function phantom safety.
- [ ] `pytest tests/unit/test_session_health_phantom_guard.py tests/unit/test_session_health_sibling_phantom_safety.py -v` passes.
- [ ] Full test suite passes (`/do-test`).
- [ ] `python -m ruff check .` returns exit code 0.
- [ ] `python -m ruff format --check .` returns exit code 0.
- [ ] `grep -rn 'r.scan_iter\|r.delete' agent/session_health.py` returns no matches (policy check).
- [ ] Documentation updated (`/do-docs`).
- [ ] After a deployment to a worker instance, `tail -n 200 logs/worker.log | grep "popoto.fields"` returns zero matches across three consecutive reflection ticks (smoke test post-deploy, not CI-enforced).

## Team Orchestration

### Team Members

- **Builder (session-health-phantom-guard)**
  - Name: phantom-guard-builder
  - Role: Add `_filter_hydrated_sessions`, apply it to `cleanup_corrupted_agent_sessions`, swap `rebuild_indexes()` → `repair_indexes()`, delete raw-Redis fallback, apply filter to 5 sibling call sites.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (phantom-guard-tests)**
  - Name: phantom-guard-tests
  - Role: Author the two new regression test files. Seed orphan `$IndexF` entries via Popoto's internal Redis connection; verify both the happy path and the sibling-function phantom-safety path.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (phantom-guard-validator)**
  - Name: phantom-guard-validator
  - Role: Verify all success criteria, run full test suite, run ruff, verify no raw Redis operations remain in the touched files.
  - Agent Type: validator
  - Resume: true

- **Documentarian (phantom-guard-docs)**
  - Name: phantom-guard-docs
  - Role: Update reflections feature doc (if present), update self-healing doc, update inline docstrings on `_filter_hydrated_sessions` and `cleanup_corrupted_agent_sessions`.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add `_filter_hydrated_sessions` helper and apply to `cleanup_corrupted_agent_sessions`
- **Task ID**: build-phantom-filter
- **Depends On**: none
- **Validates**: tests/unit/test_session_health_phantom_guard.py (create), tests/unit/test_worker_entry.py
- **Informed By**: Freshness Check confirmed `session_health.py:900-902` still uses the canonical pattern `isinstance(s.agent_session_id, str)`.
- **Assigned To**: phantom-guard-builder
- **Agent Type**: builder
- **Parallel**: true
- Add module-level helper `_filter_hydrated_sessions(sessions: Iterable) -> list[AgentSession]` to `agent/session_health.py`, placed near the top of the module after imports.
- In `cleanup_corrupted_agent_sessions`, replace `all_sessions = list(AgentSession.query.all())` with a two-line sequence that queries, then filters. Log phantom count at INFO when non-zero.
- Replace terminal `AgentSession.rebuild_indexes()` call (line 1133) with `stale, rebuilt = AgentSession.repair_indexes()`; update the log line to report both counts.
- Trigger `repair_indexes()` when either `cleaned > 0` OR `phantoms_filtered > 0`, not just the former.
- Delete lines 1114-1122 (raw-Redis fallback) entirely. The outer `try/except` at 1107-1112 remains and still logs on ORM delete failure.

### 2. Harden 5 sibling functions with `_filter_hydrated_sessions`
- **Task ID**: build-sibling-hardening
- **Depends On**: build-phantom-filter
- **Validates**: tests/unit/test_session_health_sibling_phantom_safety.py (create)
- **Assigned To**: phantom-guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Apply `_filter_hydrated_sessions` to `_recover_interrupted_agent_sessions_startup` (session_health.py:105) — filter `running_sessions` immediately after the query.
- Apply `_filter_hydrated_sessions` to `_agent_session_health_check` (session_health.py:475) — filter BEFORE the terminal-status guard (lines 483-491), because `actual_status = getattr(entry, "status", None)` on a phantom returns a `Field` descriptor, which sneaks past `_TERMINAL_STATUSES`.
- Apply `_filter_hydrated_sessions` to both queries in `session_recovery_drip` (sustainability.py:143-146): `paused_circuit` and `paused`.
- Apply `_filter_hydrated_sessions` to `session_count_throttle` (sustainability.py:278) on `all_sessions`.
- Apply `_filter_hydrated_sessions` to `failure_loop_detector` (sustainability.py:334) on `all_sessions`.
- Add an import of `_filter_hydrated_sessions` from `agent.session_health` in `agent/sustainability.py`.
- Add a one-line inline comment at each call site: `# Phantom guard: drop records whose fields are still Popoto Field descriptors (orphan $IndexF members).`

### 3. Author regression tests
- **Task ID**: test-phantom-guard
- **Depends On**: build-phantom-filter, build-sibling-hardening
- **Validates**: `pytest tests/unit/test_session_health_phantom_guard.py tests/unit/test_session_health_sibling_phantom_safety.py -v` passes
- **Assigned To**: phantom-guard-tests
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_session_health_phantom_guard.py` with tests 1–6 from Test Impact. Seed orphans via Popoto's internal Redis connection (`from popoto.models.query import POPOTO_REDIS_DB; POPOTO_REDIS_DB.sadd("$IndexF:AgentSession:status:pending", "AgentSession:nonexistent-hash-id")`).
- Create `tests/unit/test_session_health_sibling_phantom_safety.py` with one test per sibling call site. Each test: seed a matching orphan, create one live hydrated record in the same index bucket, invoke the function, assert no exception and the live record is observable in the function's side effects.
- Do NOT mock `AgentSession.query.all()` — use real Popoto ORM against the test Redis (`tests/conftest.py` already provides the fixture). Mocking would miss the exact failure mode.
- Assert the log line patterns via `caplog.records` to confirm the phantom-filter debug log fires and the orphan-cleanup INFO log fires with non-zero counts.

### 4. Validation pass
- **Task ID**: validate-phantom-guard
- **Depends On**: test-phantom-guard
- **Assigned To**: phantom-guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full unit test suite: `pytest tests/unit/ -x -q`.
- Run `python -m ruff check .` and `python -m ruff format --check .`; both must return exit 0.
- Verify no raw Redis calls remain in `agent/session_health.py`: `grep -n 'r\.scan_iter\|r\.delete\|import redis' agent/session_health.py` returns nothing relevant (the `import redis` at the deleted fallback must be gone; other imports of `redis as _redis` were only in the fallback block).
- Verify all 5 sibling functions have the filter applied: `grep -c '_filter_hydrated_sessions' agent/session_health.py agent/sustainability.py` totals at least 6 (1 definition + 1 in cleanup + 2 in session_health sibling callers + 3 in sustainability callers — plus the import line in sustainability.py).
- Verify `config/reflections.yaml` still has `enabled: true` for `agent-session-cleanup`.
- Confirm `tests/unit/test_worker_entry.py` still passes (callable-presence and startup-order tests).

### 5. Documentation pass
- **Task ID**: document-phantom-guard
- **Depends On**: validate-phantom-guard
- **Assigned To**: phantom-guard-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/reflections.md` (if present) — add a note under the agent-session-cleanup subsection about the phantom-filter invariant.
- Update `docs/features/bridge-self-healing.md` if it references `cleanup_corrupted_agent_sessions`.
- Verify docstrings added in step 1 are complete (helper docstring, updated cleanup docstring).

### 6. Final validation and PR
- **Task ID**: validate-all
- **Depends On**: document-phantom-guard
- **Assigned To**: phantom-guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run all verification commands from the Verification table.
- Confirm all Success Criteria checkboxes are checkable (via grep, test runs, and file reads).
- Generate final report for PR description.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Phantom-guard tests pass | `pytest tests/unit/test_session_health_phantom_guard.py -v` | exit code 0 |
| Sibling phantom-safety tests pass | `pytest tests/unit/test_session_health_sibling_phantom_safety.py -v` | exit code 0 |
| Full unit suite passes | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Helper defined | `grep -c "^def _filter_hydrated_sessions" agent/session_health.py` | output contains 1 |
| Cleanup uses repair_indexes | `grep -c "AgentSession.repair_indexes" agent/session_health.py` | output contains 1 |
| Cleanup no longer uses rebuild_indexes directly | `grep -c "AgentSession.rebuild_indexes()" agent/session_health.py` | output contains 0 |
| Raw-Redis fallback removed | `grep -En "r\.scan_iter\|^\s+r\.delete\(key\)" agent/session_health.py` | exit code 1 |
| Reflection still enabled | `grep -A 5 "name: agent-session-cleanup" config/reflections.yaml \| grep "enabled: true"` | output contains 1 |
| Sibling functions filtered (session_health.py) | `grep -c "_filter_hydrated_sessions" agent/session_health.py` | output > 3 |
| Sibling functions filtered (sustainability.py) | `grep -c "_filter_hydrated_sessions" agent/sustainability.py` | output > 3 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Sibling-filter scope — all 5 or just the destructive 2?** The issue lists 5 sibling functions but three of them (`session_recovery_drip`, `session_count_throttle`, `failure_loop_detector`) are read-only or only transition status via `transition_status()`. The plan applies the filter to all 5 for defense-in-depth. Should we narrow to just the 2 destructive call sites (`_recover_interrupted_agent_sessions_startup`, `_agent_session_health_check`) to minimize diff size, accepting that the 3 read-only call sites could still hit `AttributeError` on a phantom — which would at worst log a traceback and skip the tick? Recommendation: apply to all 5 (issue asks for it, diff is small).

2. **Canonical hydration attribute — `agent_session_id` or `id`?** The existing pattern at `session_health.py:900` uses `agent_session_id`. The cleanup's current buggy code reads `session.id`. `agent_session_id` is a `KeyField` and is hydrated first on load; `id` may be a computed property. Recommendation: use `agent_session_id` (matches established pattern, tracks what Popoto actually populates). Confirming.

3. **Should `repair_indexes()` ALWAYS run, or only when phantoms/corrupt records were observed?** Always-run would make the reflection self-healing even for orphans introduced since the last tick, but costs a full index rebuild per hour even when clean. Conditional run saves that work when there's nothing to do. The plan goes with conditional (`cleaned > 0 OR phantoms_filtered > 0`). Recommendation: conditional — orphan rate is low, and the scheduled `redis-index-cleanup` reflection handles the unconditional-rebuild case separately.
