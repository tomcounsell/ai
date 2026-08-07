---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-08
tracking: https://github.com/tomcounsell/ai/issues/2660
last_comment_id: none
---

# AgentSession.save() restamps updated_at on maintenance writes, faking liveness

## Problem

On 2026-08-07 the dashboard reported `last activity` of "just now" for nine Jobs that had settled on 07-16. Their `last_activity_at` all read `08-07 11:34` — one batch, written by a service restart. Earlier that day the same flattening landed on every `running` row on a repeating tick: `11:03:16 → 11:08:14 → 11:13:16`, 59 rows inside a ~16-second window each time.

Meanwhile `/update` kept reporting `Skipped 59 live session(s) (recent heartbeat)`. The stale-session reaper could see 59 rows that had not executed anything in weeks and skipped every one as freshly active.

**Current behavior:**

`AgentSession.save()` stamps `self.updated_at = utc_now()` on every unguarded call (`models/agent_session.py:1023`). There is a carve-out — `save(update_fields=[...])` skips the stamp when `update_fields` omits `updated_at` (`models/agent_session.py:1008`) — and the execution-path writers in `agent/session_health.py` and `agent/session_executor.py` use it correctly.

Two maintenance writers do not, and both write whole rows:

1. **The corruption probe.** `cleanup_corrupted_agent_sessions()` calls `session.save()` on **every hydrated row**, terminal ones included, purely to see whether the save raises (`agent/session_health.py:5569-5571`, comment: "Try a no-op save to detect other validation failures"). It is not a no-op. It moves the entire table's `updated_at` in one pass. Four callers fire it: worker startup (`worker/__main__.py:763`), the hourly `agent-session-cleanup` reflection (`config/reflections.yaml:37-42`), `/update` Step 5.5 (`scripts/update/run.py:1935`), and the corrupted-pop handler in the queue loop (`agent/agent_session_queue.py:2308`).

2. **The archive restore.** `_rehydrate_row()` rebuilds each archived row as `AgentSession(id=archived_id, **fields).save()` (`agent/session_archive.py:433-443`), called from `restore_if_empty()` at worker startup (`worker/__main__.py:720`). The archived `updated_at` travels in the payload and is then overwritten with restore time.

The `/update` path makes the consequence self-defeating by construction. Step 5.5 runs the probe at `scripts/update/run.py:1935` and the reaper at `:1949`. The probe restamps every row seconds before `_cleanup_stale_sessions` reads `updated_at` recency against a 30-minute `RECENT_ACTIVITY_WINDOW` (`scripts/update/run.py:274-278`). Every fence-less row is guaranteed to land in `skipped_recent`. The reaper cannot reach a fence-less row on the `/update` path, ever.

The dashboard reads the same forged value: `best_timestamp()` prefers `updated_at` (`ui/data/sdlc.py:1240`) and feeds Job ordering. A batch write flattens the sort key across every touched row.

**Desired outcome:**

`updated_at` means "something actually wrote this row." No maintenance sweep can move it. The stale-session reaper decides liveness on evidence a maintenance pass cannot forge, and does not finalize non-executable ledger anchors when the restamp stops masking them from it.

## Freshness Check

**Baseline commit:** `1d1830fc4` (main; recon was performed against `37f3eb5d2`, no relevant delta)
**Issue filed at:** 2026-08-07T11:35:03Z
**Disposition:** Minor drift

**File:line references re-verified:**

- `models/agent_session.py:1023` — `self.updated_at = utc_now()` — still holds, exact line.
- `models/agent_session.py:1008` — the `update_fields` carve-out — still holds, exact line.
- `scripts/update/run.py:274` — `updated_at` recency fallback — still holds, exact line (`# Fallback liveness check: updated_at recency`).
- `ui/data/sdlc.py:1241` — `best_timestamp()` prefers `updated_at` — drifted to `:1240`. Claim holds. Technical Approach below uses the corrected line.
- `agent/session_health.py` liveness saves — all four (`:2055`, `:3517`, `:3539`, `:5280`) still use `update_fields`. The issue's read of this file as the correct-usage exemplar is accurate.

**Cited sibling issues/PRs re-checked:**

- #2042 (`is_ledger` anchors) — the source of the 59 rows. Referenced only as context; behavior unchanged.

**Commits on main since issue was filed (touching referenced files):**

- `145967f4b` "Stop ledger anchors rendering as running Jobs on the dashboard" (2026-08-07 18:35) — **partially addresses, symptom layer only.** Added the `started_at` sort tiebreak in `ui/data/jobs.py` and aged ledgers out of the retention window in `ui/data/sdlc.py`. The issue already names that tiebreak as a symptom-level mitigation. Root cause untouched. This commit is also the source of the **ledger-reaper hazard** documented under Risks: it stopped ledgers rendering as active Jobs, it did not stop the reaper from being able to kill them.

**Active plans in `docs/plans/` overlapping this area:** `durability-room-job-agentrun.md` (#2494, status Ready) and `durability-m1-fence-canary.md` both work the `(exec_pid, pid_create_time)` fence that this plan's Task 3 reads. Neither mentions `updated_at`; no scope collision. This plan consumes the fence as an existing primitive and adds no field to it.

**Live-state check:** the current keyspace holds 2 `AgentSession` rows, both `running` `is_ledger` anchors, `updated_at` 2026-08-07 17:06/17:07 — consistent with the #2042 purge described in the issue's Context.

**Notes:** The bug is still present and reproducible by reading the code path; the 59-row population that surfaced it was purged separately.

## Prior Art

- **PR #1655** (#1645): "AgentSession.updated_at stamped in UTC (remove auto_now, explicit utc_now() in save())" — removed popoto's `auto_now` and moved the stamp into the `save()` override. **This is what created the current shape.** It fixed a timezone bug correctly; it did not ask which callers should be stamping. Relevant: it is the reason index rebuilds cannot restamp (`auto_now` is off), which removes a whole class of suspect.
- **PR #1787**: "downgrade liveness save() log noise to DEBUG" — added `_UPDATED_AT_OMISSION_OK_FIELDS` so the known-good partial-save callers stop logging a WARNING. Establishes the convention this plan extends: the omission is by design for liveness fields, loud for everything else.
- **#1817** (in `agent/session_health.py:_heal_future_updated_at`): a maintenance function that used to persist a clamped `updated_at` was made **read-only detection** because "persisting rewrites the `created_at`-based sorted index on every call, so healing one future-dated straggler reshuffled the index position of every OTHER recently-created record." Same file, same class of defect, same resolution shape. Task 1 is the second application of this lesson.
- **#1676 / PR #1677**: "Fix sdlc-local-\* durability: reap on idle, not creation age" — established that live worker-less CLI pipelines never write `last_heartbeat_at` and are kept alive **by `updated_at` refreshed on every stage advance** (`tools/sdlc_session_ensure.py:73-79`). This is a hard constraint on Task 3: `last_heartbeat_at` cannot simply replace `updated_at` in the reaper's ladder.
- **#2305 defect 1** (`tools/sdlc_session_ensure.py:882-896`): `updated_at` was demoted from authoritative liveness to idle-window fallback precisely because "it is refreshed by probes and sibling `session-ensure` renewals, which is exactly the mirage that let a hollow tracking session read as live forever." **The word "probes" is this bug, named a release ago and worked around locally instead of fixed at the source.**
- **#2098 / #2091 / #2439**: established that out-of-process actuation on session health is unsafe by design, and removed the `session-liveness-check` reflection. Relevant only as the adjacent-drift finding below.
- **#2494 / #2516 / #2538** (Durability M1): built the `(exec_pid, pid_create_time)` fence and closed nine unfenced consumers. The fence is the prior art the issue points at for "a liveness field only execution writes." Task 3 reads it as-is.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|--------------------------------|
| PR #1655 (#1645) | Moved the `updated_at` stamp from popoto `auto_now` into the `save()` override so it lands in UTC | Correct on timezone, silent on authorship. Moving the stamp into `save()` made *every* caller a liveness author, including maintenance sweeps that have nothing to report. |
| PR #2415 (#2305 defect 1) | Demoted `updated_at` to an idle-window fallback in `_iter_orphan_sessions` and documented "it is refreshed by probes" | Fixed one reader. The probe kept writing, and every other reader (`/update`'s reaper, `best_timestamp`) kept trusting the forged value. |
| `145967f4b` | Added a `started_at` sort tiebreak so a batch write cannot flatten dashboard ordering | Treats the symptom at the presentation layer, as its own commit message says. The underlying value stays wrong for every non-dashboard reader. |

**Root cause pattern:** each fix moved a *reader* away from `updated_at` instead of stopping the *writer*. Three readers were patched over eleven months while two maintenance writers kept forging the value. The field is fine; the write authorization is missing.

## Research

The work is internal: the ORM is `popoto`, vendored in `.venv`, and the authoritative answer to "what does `save()` validate" is in its source, not in ecosystem documentation. Read directly instead of searched.

**Key findings (from `popoto/models/base.py`):**

- `Model.save()` → `pre_save()` → `if not self.is_valid(): raise ModelException(...)` (`base.py:908-913`, `ignore_errors` suppresses the raise). **`is_valid()` is exactly the predicate the current save-probe tests indirectly.** It runs type coercion, null checks, `max_length` enforcement, per-field `is_valid()`, and the ttl/expire_at mutual-exclusion check, logs failures rather than raising, and writes nothing to Redis. This makes Task 1 a behavior-preserving substitution, not a weakening.
- `Model.save(update_fields=[...])` serializes, validates, and indexes only the listed fields; `None` means full save; empty list is a no-op (`base.py:1019-1022`). Confirms the existing carve-out is the sanctioned partial-write path.
- `DatetimeField` supports `auto_now` / `auto_now_add`, both defaulting to `False` (`popoto/fields/datetime_field.py:85-86`). `AgentSession.updated_at = DatetimeField(null=True)` carries neither since #1655, so `rebuild_indexes()` — which runs `field.on_save` per field, never `Model.save()` — cannot restamp. Rules out index repair as a suspect.

No external findings needed beyond the vendored source.

## Data Flow

How a forged timestamp reaches a reaper decision today:

1. **Entry point** — `/update` runs, or the worker starts, or the hourly `agent-session-cleanup` reflection fires, or the queue loop hits a corrupted pop.
2. **`cleanup_corrupted_agent_sessions()`** (`agent/session_health.py:5548-5571`) — loads `_filter_hydrated_sessions(AgentSession.query.all())`, iterates every row, calls `session.save()` as a corruption probe.
3. **`AgentSession.save()`** (`models/agent_session.py:1015-1024`) — `update_fields is None`, so the carve-out at `:1008` is skipped and `:1023` stamps `self.updated_at = utc_now()`.
4. **Redis** — every row's hash now carries a timestamp equal to sweep time. Nothing distinguishes it from a real execution write.
5a. **`_cleanup_stale_sessions()`** (`scripts/update/run.py:250-290`, invoked 14 lines after step 2 on the `/update` path) — fence absent → falls to `updated_at` recency → `now - updated_ts < RECENT_ACTIVITY_WINDOW` → `skipped_recent += 1`. Output: `Skipped 59 live session(s) (recent heartbeat)`.
5b. **`best_timestamp()`** (`ui/data/sdlc.py:1240`) → `group_into_jobs` → `Job.last_activity_at` (`ui/data/jobs.py:449`) → dashboard sort (`ui/data/jobs.py:493-494`). Output: "just now" on three-week-old work, and a flat primary sort key.

The parallel restore path: **`restore_if_empty()`** (`worker/__main__.py:720`) → **`_rehydrate_row()`** (`agent/session_archive.py:433-443`) → `AgentSession(id=..., **fields).save()` → same step 3, except the correct value was in `fields` and is discarded rather than merely refreshed.

After this plan, step 3 is unreachable from steps 2 and from `_rehydrate_row`, and step 5a consults `last_heartbeat_at` before falling to `updated_at`.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** `AgentSession.save()` gains one keyword-only-in-practice parameter, `preserve_updated_at: bool = False`. Default preserves every existing call site's behavior exactly. No signature change is visible to any caller that does not opt in.
- **Coupling:** decreases. `cleanup_corrupted_agent_sessions` stops being a Redis writer for healthy rows and becomes a read-mostly auditor — it writes only on the delete path it already owns. The corruption probe stops coupling "is this row valid" to "this row is now fresh."
- **Data ownership:** clarified, which is the point. After this change, `updated_at` is owned by writers that have something to report, and `last_heartbeat_at` remains exclusively the execution heartbeat's. No new field, so **no Popoto schema change and no migration** — the Popoto Schema Migration Requirement in `docs/sdlc/do-plan.md` does not apply.
- **Reversibility:** high. Three small, independent diffs, each revertable alone.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1 (confirming the reaper's new ladder does not over-reap — the one place this can cause harm)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable for ORM-backed tests | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | The health/archive tests exercise real Redis, per the repo's no-mocks testing philosophy |
| On-pin interpreter | `python -c "import pathlib,sys; assert pathlib.Path('.python-version').read_text().strip().startswith('.'.join(map(str, sys.version_info[:2])))"` | `scripts/pytest-clean.sh` aborts on an off-pin venv |

## Solution

### Key Elements

- **Read-only corruption probe** — `cleanup_corrupted_agent_sessions` decides "is this row corrupt" without writing to it. The probe's write was incidental to its purpose; removing it removes the batch restamp at its largest source and also removes an O(N) Redis write amplification that ran on every worker start, every `/update`, and every hour.
- **Explicit timestamp preservation for restore** — the archive restore declares that it owns the `updated_at` it carries, so rehydration reproduces the archived row rather than a row that looks like it was written at restore time.
- **An execution-only rung in the reaper's liveness ladder** — the `/update` reaper consults `last_heartbeat_at` (written only by the executor's heartbeat, never by any maintenance path) before falling back to `updated_at`.
- **A ledger guard on the reaper, plus a successor that can actually reap them** — every worker loop skips `is_ledger` anchors; the `/update` reaper does not. The restamp has been masking that gap, and fixing the restamp exposes it. The process-liveness reaper is the wrong owner for a row that never had a process, so it skips ledgers — but `tools/sdlc_session_ensure.py --kill-orphans`, the tool that *is* the right owner (it decides on issue-lock ownership, not process liveness), is a manual CLI that nothing schedules. That is how 59 anchors accumulated. The skip and the scheduled successor land together; shipping the skip alone would make the accumulation permanent.

### Flow

`/update` runs → corruption cleanup audits every row and writes nothing → stale reaper asks the fence → asks `last_heartbeat_at` → asks `updated_at` → skips ledger anchors outright → finalizes only rows with no live process and no execution evidence → summary names which signal drove each skip.

Worker cold start → archive restore rehydrates rows carrying their archived `updated_at` → dashboard orders three-week-old Jobs by when they actually last moved.

### Technical Approach

**1. Replace the save-probe with `is_valid()` (`agent/session_health.py:5568-5580`).**

The current probe:

```python
# Check 2: Try a no-op save to detect other validation failures
if not is_corrupt:
    try:
        session.save()
    except Exception as e:
        if "invalid" in str(e).lower() or "validation" in str(e).lower():
            ...
            is_corrupt = True
```

`popoto.Model.pre_save` raises `ModelException` exactly when `self.is_valid()` returns False (`base.py:908-913`), so the string match on `"invalid"`/`"validation"` was a proxy for that one predicate. Call it directly. `is_valid()` returns a bool and logs its own failure detail; it raises only for the ttl/expire_at mutual-exclusion case, which is treated as corrupt (the old code's `save()` would have raised there too, though its message would not have matched the old string filter — this is a small, deliberate widening, called out in Risks).

Keep the WARNING log line and its fields so operator-facing output is unchanged in shape.

**2. Add `preserve_updated_at` to `AgentSession.save()` (`models/agent_session.py:995-1024`) and use it in the restore.**

The `update_fields` carve-out cannot serve the restore: `_rehydrate_row` constructs a fresh instance and must write every field, so a partial save would drop the rest of the row. The honest primitive is an explicit opt-out of the stamp:

```python
def save(self, *args, update_fields=None, preserve_updated_at=False, **kwargs):
    ...
    if preserve_updated_at:
        # Caller owns updated_at (archive restore reproduces the archived value).
        logger.debug("save(preserve_updated_at=True): caller-supplied updated_at kept")
        return super().save(*args, update_fields=update_fields, **kwargs)
    if update_fields is not None and "updated_at" not in update_fields:
        ...  # unchanged
    self.updated_at = utc_now()
    return super().save(*args, update_fields=update_fields, **kwargs)
```

`agent/session_archive.py:_rehydrate_row` passes `preserve_updated_at=True`. Guard for the case where the archived payload has no `updated_at` (pre-field rows): when the deserialized `fields` omit it, fall through to the normal stamp rather than persisting `None`, so restore never *removes* a timestamp the row would otherwise have had.

**3. Insert `last_heartbeat_at` into the reaper's ladder and skip ledgers (`scripts/update/run.py:250-300`).**

Current ladder: fence → `updated_at` recency → `created_at` age. New ladder:

1. live worker in `_active_workers` → skip (unchanged)
2. `is_ledger` → skip, counted separately as `skipped_ledger`. Ledger anchors (#2042) carry a non-terminal status for their whole life with no subprocess behind them; every worker loop already excludes them via `_is_ledger` (`agent/agent_session_queue.py:1466`, `:2087` region, `bridge/update.py:_cli_flush_stuck`). The `/update` reaper is the one sweep that does not, and finalizing one kills a live SDLC pipeline's state anchor.
3. fence live → skip / fence dead → finalize (unchanged; still the only path that honestly asserts no live process)
4. **new:** `last_heartbeat_at` within `RECENT_ACTIVITY_WINDOW` → skip, counted as `skipped_heartbeat`. Written only by `agent/session_executor.py:1070-1071` (T+0) and `:2226-2227` (60s tick), both via `save(update_fields=["last_heartbeat_at"])`. No maintenance path sets it.
5. `updated_at` within `RECENT_ACTIVITY_WINDOW` → skip (retained, and now honest). **This rung must stay**: #1676 established that live worker-less local CLI pipelines never write `last_heartbeat_at` and are kept alive precisely by `updated_at` refreshed on every stage advance (`tools/sdlc_session_ensure.py:73-79`). Dropping it would reap live pipelines.
6. `created_at` age ≥ threshold → finalize (unchanged)

Extend the return tuple and the caller's summary line (`scripts/update/run.py:1949`) with the two new skip counters so each skip names the signal that produced it.

**4. Schedule the ledger reaper that already exists (`config/reflections.yaml`, `tools/sdlc_session_ensure.py`).**

`_iter_orphan_sessions` (`tools/sdlc_session_ensure.py:937-1019`) is built for exactly these rows: it selects `session_type="eng"`, `status="running"`, `session_id` starting `sdlc-local-`, `last_heartbeat_at is None` — which is precisely the shape of a ledger anchor (verified against the live row: `sdlc-local-2643`, `is_ledger=True`, `session_type=eng`, `last_heartbeat_at=None`). It decides via `_lock_owner_is_live(payload)` on the per-issue lock, failing toward live on ambiguous evidence, and falls back to a 600s idle window only when no lock payload resolves. That is the honest authority for a row with no process.

It is reachable only as `python -m tools.sdlc_session_ensure --kill-orphans`. Nothing in `config/reflections.yaml`, `scripts/update/run.py`, or launchd invokes it — grep confirms the only references are its own argparse wiring and its tests.

Add a zero-argument public wrapper (`kill_orphans_reflection()`, delegating to `_kill_orphans(dry_run=False)` and returning its dict) and register it:

```yaml
  - name: sdlc-ledger-orphan-reap
    description: "Finalize sdlc-local ledger anchors whose issue lock resolves to a dead owner"
    every: 3600s # 1 hour
    priority: normal
    execution_type: function
    callable: "tools.sdlc_session_ensure.kill_orphans_reflection"
    enabled: true
```

Hourly matches `ORPHAN_AGE_SECONDS` (600s) with generous headroom and matches the cadence of the sibling `agent-session-cleanup` entry. The wrapper must never raise — `_kill_orphans` already returns a structured dict and the CLI layer exits 0 regardless of per-session failures, so the wrapper wraps in try/except and returns a zeroed dict on error, per the reflection contract.

**5. Leave `best_timestamp()` alone (`ui/data/sdlc.py:1240`).**

Once writers 1 and 2 stop forging it, `updated_at` is the correct "most recent write" signal for dashboard ordering, and it is the *only* progress signal ledger sessions have (`ui/data/sdlc.py:343-348`). Changing the reader would break ledger ordering to fix a writer bug. A regression test pins the intent instead.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `agent/session_health.py` corruption probe: the replacement `is_valid()` call is wrapped so a `ModelException` from the ttl/expire_at check is classified as corrupt rather than escaping the sweep. Test asserts the WARNING fires and the row is deleted, and that one raising row does not abort the loop over the remaining rows.
- [ ] `agent/session_archive.py:_rehydrate_row`: a payload missing `updated_at` must not persist `None`. Test asserts the fallback stamp is applied and a DEBUG line is emitted.
- [ ] `models/agent_session.py:save()`: `preserve_updated_at=True` combined with `update_fields=[...]` is a caller smell. Test asserts the Resolved Decision 2 precedence (preserve wins), that a WARNING is emitted, and that no exception escapes into the fail-quiet callers.
- [ ] `scripts/update/run.py:_cleanup_stale_sessions`: `last_heartbeat_at` present but unparseable (string, `None`, naive datetime) must fall through to the next rung rather than raising or being read as fresh. Test covers each shape.

### Empty/Invalid Input Handling

- [ ] `cleanup_corrupted_agent_sessions` over an empty keyspace returns `{"corrupted": 0, "orphans": 0}` and performs zero writes.
- [ ] A row whose `updated_at` is `None` (never saved through the stamping path) is not treated as infinitely stale by the reaper — it falls to the `created_at` rung, which is the existing documented behavior.
- [ ] No agent-output processing in scope; the empty-output silent-loop category does not apply.

### Error State Rendering

- [ ] `/update`'s summary line must distinguish `skipped_recent`, `skipped_heartbeat`, `skipped_fence_live`, and `skipped_ledger`. A skip that names no signal is the failure mode that hid this bug for months; the test asserts each counter appears with its own label.
- [ ] Dashboard rendering is unchanged by this plan; the existing Job-ordering tests cover it.

## Test Impact

- [ ] `tests/unit/test_session_health_phantom_guard.py` — UPDATE: the corruption-probe tests exercise `cleanup_corrupted_agent_sessions` against real records (`:118`, `:169`, `:280`, `:306`). None currently assert on `save()` being called, so they should keep passing unchanged; add assertions that a healthy row's `updated_at` is byte-identical before and after the sweep.
- [ ] `tests/unit/test_session_health_orphan_process_reap.py` — UPDATE: asserts the `{"corrupted": int, "orphans": int}` return shape (`:340`, `:352`). Return shape is unchanged; verify no test depends on the probe's write as a side effect.
- [ ] `tests/unit/test_session_health_unconditional_index_repair.py` — UPDATE: exercises the same sweep; confirm the index-repair assertions do not depend on the per-row save.
- [ ] `tests/unit/test_stale_cleanup.py` — UPDATE: covers `_cleanup_stale_sessions`; the return tuple gains two counters, so every unpack site needs widening. Add the ledger-skip and heartbeat-skip cases.
- [ ] `tests/unit/test_update_stale_session_fence.py` — UPDATE: the fence rung keeps precedence over the new heartbeat rung; add an assertion pinning that order (a fence-dead row with a fresh `last_heartbeat_at` is still finalized, because the fence is the only signal that can assert absence of a process).
- [ ] `tests/unit/test_session_archive.py` — UPDATE: add restore-preserves-`updated_at` coverage alongside the existing rehydrate assertions.
- [ ] `tests/unit/test_session_archive_cli.py` — UPDATE: verify the `restore --dry-run` path is unaffected (it writes nothing either way).
- [ ] `tests/integration/test_session_archive_cold_boot.py` — UPDATE: the cold-boot round trip is the natural home for the end-to-end assertion that an archived timestamp survives restore.
- [ ] `tests/unit/test_worker_persistent.py` / `tests/unit/test_worker_entry.py` — UPDATE: both patch `cleanup_corrupted_agent_sessions` at the worker-startup call site (`:157`, `:200`); confirm the patches still bind after the internal change.
- [ ] `tests/unit/test_migrate_strip_pid_fields.py` — UPDATE: `scripts/_strip_migration.py:32-40` documents the restamp as a known concurrent-writer property in its safety argument. That paragraph becomes wrong when Task 1 lands. Update the module docstring and any test asserting on it — the atomicity argument (one MULTI/EXEC pipeline) still holds and is the load-bearing half; only the "who else writes terminal rows" example changes.
- [ ] `tests/unit/test_recovery_respawn_safety.py::TestStartupRecoveryOwnershipGuard::test_stale_legacy_session_without_stamp_still_recovered` (`:965`) — UPDATE: exercises the reaper's legacy-row age fallback for a session with no ownership stamp. Verify the new ladder does not change its outcome (a legacy row has neither `is_ledger`, a fence, nor `last_heartbeat_at`, so it should reach the same rung it does today) and add the assertion that pins that.
- [ ] `tests/unit/test_sdlc_session_ensure.py` — UPDATE: add coverage for the new `kill_orphans_reflection()` wrapper (returns a dict, never raises, is not a dry run).
- [ ] `tests/unit/test_reflection_scheduler.py` — UPDATE: `test_all_function_reflections_resolve` must resolve `tools.sdlc_session_ensure.kill_orphans_reflection`; `test_no_duplicate_names` and `test_expected_reflections_present` must still pass with the new entry.
- [ ] No new xfail conversions: no `pytest.mark.xfail` or runtime `pytest.xfail()` in the suite references this bug. Searched `tests/` for xfail markers matching `updated_at` / `restamp` / `liveness` / stale-session — zero hits.

## Rabbit Holes

- **Auditing all ~120 unguarded `.save()` call sites in the repo.** The issue asks for the maintenance writers, and recon isolated them to two. Execution-path full saves (`agent/health_check.py`, `bridge/session_transcript.py`, `models/session_lifecycle.py` transitions, `tools/stage_states_helpers.py`) are reporting real activity — restamping is correct there. Converting them wholesale would be a large diff that changes nothing about the reported bug and risks breaking the `#1676` worker-less-pipeline liveness signal.
- **Adding a new execution-only liveness field.** The issue floats this. `last_heartbeat_at` already is one: written only by the executor's heartbeat loop, already in `_UPDATED_AT_OMISSION_OK_FIELDS`, already trusted by `_session_is_alive` (`agent/session_health.py:5846-5853`) and `_iter_orphan_sessions` (`tools/sdlc_session_ensure.py:990`). Minting a second one means a Popoto schema change, a migration, and a backfill for zero new signal.
- **Fixing `agent/health_check.py:625`'s float-into-`DatetimeField` write.** Real, adjacent, and genuinely wrong (`s.updated_at = time.time()` where everyone else stores a `datetime`). It is an execution path, so it is not this bug, and changing the stored type mid-plan risks every reader that does `isinstance` dispatch. See No-Gos.
- **Rewriting `best_timestamp()` or the Job sort.** `145967f4b` already added the tiebreak. Once the writers are honest the reader is correct. Touching it again is churn.
- **Reordering `/update` Step 5.5.** Tempting, because the probe-before-reaper ordering is what makes the self-defeat total. With Task 1 the probe writes nothing and the ordering stops mattering. A reorder would encode the old assumption in the call graph and hide a regression if the probe ever writes again. Pin the invariant with a test instead.

## Risks

### Risk 1: `is_valid()` classifies rows as corrupt that the old save-probe let live, and they get deleted

**Impact:** Data loss. The probe's positive result routes straight to `session.delete()` (`agent/session_health.py:5582+`).

**Mitigation:** `popoto.Model.pre_save` raises `ModelException` **iff** `is_valid()` is False (`base.py:908-913`), so the two predicates agree by construction — the old code's string match on `"invalid"`/`"validation"` was a lossy proxy for exactly this call. The one deliberate widening is the ttl/expire_at mutual-exclusion `ModelException`, which `is_valid()` raises before the field loop; `AgentSession` sets neither `ttl` nor `expire_at`, so that branch is unreachable for this model — verified by grep. The build lands a test that asserts a healthy row is never classified corrupt across the full field surface, and the sweep's existing per-row try/except keeps one pathological row from cascading.

### Risk 2: the reaper, no longer blinded, starts finalizing rows it could never reach before

**Impact:** This is the intended effect, and it is also the sharpest edge in the plan. The 59 rows it was skipping were ledger anchors. Reaching them without a guard would finalize the state anchor of a live SDLC pipeline mid-run.

**Mitigation:** the `is_ledger` skip (Task 3, rung 2) lands in the same change as the fix, never after it, and is ordered *before* the fence check so no ledger can be finalized by any subsequent rung. The `last_heartbeat_at` rung is additive (it can only cause skips, never finalizations), and the `updated_at` rung is retained for the #1676 worker-less-pipeline case. Net effect on any row: it can only move from "skipped for the wrong reason" to "skipped for the right reason" or "finalized on evidence."

### Risk 5: the ledger skip makes anchor accumulation permanent

**Impact:** The inverse of Risk 2, and the reason the skip cannot ship alone. If the `/update` reaper stops being able to reach ledger anchors and nothing else reaps them, they accumulate without bound — which is the state that produced the 59 rows in the first place.

**Mitigation:** Task 4 schedules `tools/sdlc_session_ensure.py`'s existing orphan reaper, which decides on issue-lock ownership rather than process liveness and is therefore the correct authority for a process-less row. The build must not land Task 3's skip without Task 4's reflection; the task graph encodes this by making them a single validated pair, and a Verification row asserts the registry entry exists.

### Risk 3: `_strip_migration.py`'s safety argument silently becomes stale

**Impact:** A future reader trusts a docstring that describes a concurrent writer which no longer exists, and draws the wrong conclusion about what the MULTI/EXEC pipeline is protecting against.

**Mitigation:** `scripts/_strip_migration.py:32-40` is an explicit Test Impact item. The load-bearing half of its argument (atomicity, not quiescence) survives; only the cited example changes. Per the repo's no-legacy-code rule, the paragraph is rewritten to the new status quo rather than annotated with history.

### Risk 4: restore preserves a timestamp from a row that was archived mid-corruption

**Impact:** A restored row could carry an `updated_at` that is future-dated or nonsensical relative to restore time.

**Mitigation:** `_heal_future_updated_at` already detects (read-only) future-dated rows and logs them for operator visibility, and `agent/session_health.py`'s staleness reads use Redis `TIME` as a single shared clock, which #1817 established makes a skewed `updated_at` harmless to read. Preserving a bad archived value is strictly no worse than the current behavior of overwriting it with a value that is *wrong in a way nothing can detect*.

## Race Conditions

### Race 1: the corruption sweep reads a row that an executing session writes mid-iteration

**Location:** `agent/session_health.py:5548-5571` (the `for session in all_sessions` loop) versus `agent/session_executor.py:2226-2227` (the 60s heartbeat).

**Trigger:** the sweep loads the full row set, then iterates; an execution heartbeat lands on a row after it was loaded.

**Data prerequisite:** none — after Task 1 the sweep performs no write on the healthy path, so there is nothing to lose. This race exists today and is *worsened* by the current code: the sweep's stale in-memory snapshot is written back over the heartbeat's fresh write. Removing the write removes the race.

**State prerequisite:** none.

**Mitigation:** eliminated by Task 1, not merely narrowed. The delete path (the only remaining write) is already guarded by `_filter_hydrated_sessions` and by the ORM delete's own key resolution.

### Race 2: the reaper evaluates a row while the session it describes is starting up

**Location:** `scripts/update/run.py:250-300`.

**Trigger:** `/update` runs at the moment a session transitions to `running` but before its T+0 `last_heartbeat_at` write (`agent/session_executor.py:1070-1071`) lands.

**Data prerequisite:** `last_heartbeat_at` must be written before any reaper can consult it. The T+0 write exists precisely for this window and is documented as such (`agent/session_executor.py:1035`: "A T+0 `last_heartbeat_at` write ensures the first health-check tick...").

**State prerequisite:** the row must have a fence or a fresh `updated_at` during the sub-second gap.

**Mitigation:** the enqueue-time `updated_at` write covers the gap via rung 5, and the fence covers it once the subprocess exists. The new rung is additive and cannot shrink the set of skipped rows, so it cannot open this window wider than it is today.

### Race 3: archive restore races a live worker writing the same row

**Location:** `agent/session_archive.py:_rehydrate_row` versus any executor write.

**Trigger:** restore runs while another worker is live.

**Data prerequisite:** none introduced. `restore_if_empty`'s guard already re-computes the empty-Redis decision against the fresh live count on every call, so restore does not run against a populated keyspace.

**Mitigation:** unchanged by this plan. `preserve_updated_at` alters which value is written, never whether the write happens.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2673] `config/reflections.yaml:29-35` still declares `session-liveness-check` (`every: 300s`, `enabled: true`), but `tests/unit/test_reflection_scheduler.py:75-76`, `:699-706`, `:721` all assert it was intentionally removed from the registry by #2439 (spike-3: out-of-process actuation is unsafe by design). The registry and the tests disagree and no test asserts absence, so the drift is invisible to CI. Surfaced by this recon; unrelated to the restamp (that reflection writes nothing).
- [SEPARATE-SLUG #2674] `agent/health_check.py:625` writes `s.updated_at = time.time()` — a float into a `DatetimeField` — on the PostToolUse hook path. Honest activity, wrong type, inconsistent with the `utc_now()` datetime every other writer stores. Changing the stored type touches every reader that dispatches on `isinstance`, which is a different blast radius than this fix.

## Update System

No `/update` **skill** changes required, but `scripts/update/run.py` is directly modified by Task 3 (the reaper's liveness ladder and its summary counters). No new dependencies and no migration for existing installations: the change adds no Popoto field, so `scripts/update/migrations.py` is untouched and `data/migrations_completed.json` gains no entry.

One config file changes: `config/reflections.yaml` gains the `sdlc-ledger-orphan-reap` entry (Task 4). It is already tracked in git and read at scheduler start, so it propagates on the normal `git pull` half of `/update` with no extra step. Machines pick the new reflection up on their next worker restart, which `/update` performs anyway.

One propagation note: the fix changes what `/update` reports. Machines running the old code will keep printing `Skipped N live session(s) (recent heartbeat)` until they pull. That is cosmetic and self-resolving on the next `/update`; no coordinated rollout is needed.

## Agent Integration

No agent integration required. This is a worker/maintenance-internal change. No new CLI entry point in `pyproject.toml [project.scripts]`, no new MCP surface, and no bridge import — the three touched modules (`agent/session_health.py`, `agent/session_archive.py`, `scripts/update/run.py`) are already reachable from the worker and the update script, and `models/agent_session.py:save()` gains a default-off parameter that no agent-facing path passes.

## Documentation

### Feature Documentation

- [ ] Create `docs/features/agent-session-liveness-authorship.md` — the durable statement of who is authorized to write which liveness field on `AgentSession`: `updated_at` (any writer with something to report; never a maintenance sweep), `last_heartbeat_at` (the executor heartbeat only), `last_turn_at` / `last_tool_use_at` (turn and tool boundaries only), and the `(exec_pid, pid_create_time)` fence (spawn only). Include the reaper's full liveness ladder, the #1676 constraint that keeps `updated_at` in it, and the split of reaping authority between the `/update` process-liveness reaper and the issue-lock-based `sdlc-ledger-orphan-reap`.
- [ ] Add the entry to the `docs/features/README.md` index table.
- [ ] Update `docs/features/agent-session-health-monitor.md` — it describes stale-session detection and recovery, which is the behavior the reaper's new ladder changes. Add the ladder and cross-link the authorship doc.
- [ ] Cross-link from `docs/features/agent-session-fenced-execution-record.md` — the fence is the top rung of the ladder, and the authorship doc is the natural place a reader goes next.

### External Documentation Site

Not applicable — this repo has no external docs site.

### Inline Documentation

- [ ] Rewrite the `agent/session_health.py:5568` comment: "Try a no-op save to detect other validation failures" is the sentence that made this bug, and its replacement should say why the check is read-only, citing #1817's identical lesson in the same file.
- [ ] Extend the `AgentSession.save()` docstring (`models/agent_session.py:995-1013`) to document `preserve_updated_at` alongside the existing `update_fields` guard paragraph.
- [ ] Rewrite `scripts/_strip_migration.py:32-40` to the new status quo (atomicity is the safety property; the restamping-probe example is gone).
- [ ] Update `_cleanup_stale_sessions`'s docstring (`scripts/update/run.py:189-213`) with the new ladder and the ledger skip.

## Success Criteria

- [ ] `cleanup_corrupted_agent_sessions()` performs zero Redis writes against a keyspace of healthy rows: a row's `updated_at` is byte-identical before and after a sweep.
- [ ] Its corruption-detection behavior is unchanged: a row that the old save-probe classified corrupt is still classified corrupt, and a healthy row is never classified corrupt.
- [ ] Archive restore reproduces the archived `updated_at` rather than restore time, and falls back to a fresh stamp when the payload carries none.
- [ ] `_cleanup_stale_sessions` skips `is_ledger` rows before evaluating any liveness rung, and reports `skipped_ledger` separately.
- [ ] `_cleanup_stale_sessions` consults `last_heartbeat_at` between the fence and `updated_at`, and reports `skipped_heartbeat` separately. The fence keeps precedence: a fence-dead row with a fresh heartbeat is still finalized.
- [ ] The `updated_at` rung survives, and a live worker-less local pipeline (#1676: no `last_heartbeat_at`, fresh `updated_at` from stage advances) is still skipped.
- [ ] Abandoned ledger anchors have an automatic reaper: `sdlc-ledger-orphan-reap` is registered and enabled in `config/reflections.yaml`, and its callable resolves.
- [ ] Tests pass (`/do-test`, via `scripts/pytest-clean.sh`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No agent integration to grep for — the Agent Integration section declares none.
- [ ] No xfail conversions needed — none exist for this bug.

## Team Orchestration

### Team Members

- **Builder (probe + save primitive)**
  - Name: `probe-builder`
  - Role: Task 1 and Task 2 — read-only corruption probe, `preserve_updated_at` on `AgentSession.save()`, archive restore opt-in
  - Agent Type: builder
  - Resume: true

- **Builder (reaper ladder)**
  - Name: `reaper-builder`
  - Role: Task 3 — `is_ledger` skip, `last_heartbeat_at` rung, summary counters
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `restamp-tests`
  - Role: Task 4 — regression coverage across the existing suites named in Test Impact
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `restamp-validator`
  - Role: verify success criteria, especially the no-write and no-over-reap properties
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `restamp-docs`
  - Role: the feature doc, the index entry, and the four inline rewrites
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Read-only corruption probe

- **Task ID**: build-probe
- **Depends On**: none
- **Validates**: `tests/unit/test_session_health_phantom_guard.py`, `tests/unit/test_session_health_orphan_process_reap.py`, `tests/unit/test_session_health_unconditional_index_repair.py`
- **Informed By**: Research (popoto `pre_save` raises iff `is_valid()` is False, `base.py:908-913`); Prior Art #1817 (same file, same read-only resolution)
- **Assigned To**: `probe-builder`
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: true
- Replace the `session.save()` probe at `agent/session_health.py:5568-5580` with a `session.is_valid()` check; classify a raised `ModelException` as corrupt.
- Preserve the WARNING log line's shape and fields.
- Rewrite the "Try a no-op save" comment to state why the check is read-only, citing #1817.

### 2. `preserve_updated_at` and archive restore

- **Task ID**: build-preserve
- **Depends On**: none
- **Validates**: `tests/unit/test_session_archive.py`, `tests/unit/test_session_archive_cli.py`, `tests/integration/test_session_archive_cold_boot.py`
- **Informed By**: Technical Approach step 2 (the `update_fields` carve-out cannot serve an INSERT)
- **Assigned To**: `probe-builder`
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: true
- Add `preserve_updated_at: bool = False` to `AgentSession.save()` (`models/agent_session.py:995`); when True, skip the stamp and log at DEBUG.
- Document the parameter in the docstring next to the existing `update_fields` guard paragraph.
- Implement the Resolved Decision 2 precedence: `preserve_updated_at=True` wins over `update_fields`, a WARNING names the caller, no exception is raised.
- Pass `preserve_updated_at=True` from `agent/session_archive.py:_rehydrate_row`, falling through to the normal stamp when the payload carries no `updated_at`.

### 3. Reaper liveness ladder

- **Task ID**: build-reaper
- **Depends On**: none
- **Validates**: `tests/unit/test_stale_cleanup.py`, `tests/unit/test_update_stale_session_fence.py`
- **Informed By**: Prior Art #1676 (the `updated_at` rung must survive); Risk 2 (the ledger guard must land with the fix, not after)
- **Assigned To**: `reaper-builder`
- **Agent Type**: builder
- **Parallel**: true
- Add the `is_ledger` skip to `_cleanup_stale_sessions` (`scripts/update/run.py:250`), ordered before the fence check, counted as `skipped_ledger`. Reuse the existing `_is_ledger` helper rather than re-deriving the predicate.
- Add the `last_heartbeat_at` recency rung between the fence and the `updated_at` fallback, counted as `skipped_heartbeat`, tolerating naive-datetime / float / unparseable values by falling through.
- Widen the return tuple and the caller's summary at `scripts/update/run.py:1949` so each skip names its signal.
- Update the function docstring (`:189-213`) with the new ladder.

### 4. Schedule the ledger orphan reaper

- **Task ID**: build-ledger-reaper
- **Depends On**: build-reaper
- **Validates**: `tests/unit/test_sdlc_session_ensure.py`, `tests/unit/test_reflection_scheduler.py`
- **Informed By**: Technical Approach step 4; Risk 5 (the skip cannot ship alone)
- **Assigned To**: `reaper-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `kill_orphans_reflection()` to `tools/sdlc_session_ensure.py` — zero-argument, delegates to `_kill_orphans(dry_run=False)`, never raises, returns a zeroed dict on error.
- Register `sdlc-ledger-orphan-reap` in `config/reflections.yaml` per the Technical Approach snippet.
- Confirm `tests/unit/test_reflection_scheduler.py::test_all_function_reflections_resolve` resolves the new dotted path.

### 5. Regression coverage

- **Task ID**: test-restamp
- **Depends On**: build-probe, build-preserve, build-reaper, build-ledger-reaper
- **Assigned To**: `restamp-tests`
- **Agent Type**: test-engineer
- **Parallel**: false
- Work the Test Impact checklist item by item, including the `scripts/_strip_migration.py` docstring rewrite.
- Add the no-write assertion (`updated_at` byte-identical across a sweep) and the corruption-parity assertion.
- Add the ledger-skip, heartbeat-skip, fence-precedence, and #1676 worker-less-pipeline cases.
- Add the Failure Path Test Strategy cases for each of the four exception surfaces.
- Mutation-check each new guard: disable it and confirm the corresponding test goes red before re-enabling.

### 6. Documentation

- **Task ID**: document-feature
- **Depends On**: test-restamp
- **Assigned To**: `restamp-docs`
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/agent-session-liveness-authorship.md` and add the `docs/features/README.md` index entry.
- Complete the four inline rewrites listed under Documentation → Inline Documentation.

### 7. Final validation

- **Task ID**: validate-all
- **Depends On**: build-probe, build-preserve, build-reaper, build-ledger-reaper, test-restamp, document-feature
- **Assigned To**: `restamp-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row.
- Confirm each Success Criteria item, with particular attention to the no-write and no-over-reap properties.
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `./scripts/pytest-clean.sh tests/unit/test_session_health_phantom_guard.py tests/unit/test_stale_cleanup.py tests/unit/test_update_stale_session_fence.py tests/unit/test_session_archive.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Corruption probe no longer saves | `grep -n "session.save()" agent/session_health.py` | exit code 1 |
| Probe uses read-only validation | `grep -c "is_valid()" agent/session_health.py` | output > 0 |
| Restore preserves the archived timestamp | `grep -c "preserve_updated_at=True" agent/session_archive.py` | output > 0 |
| `save()` honors the preserve flag | `grep -c "preserve_updated_at" models/agent_session.py` | output > 0 |
| Reaper skips ledger anchors | `grep -c "_is_ledger\|is_ledger" scripts/update/run.py` | output > 0 |
| Reaper consults execution-only liveness | `grep -c "last_heartbeat_at" scripts/update/run.py` | output > 0 |
| `updated_at` rung retained (#1676) | `grep -c "updated_at" scripts/update/run.py` | output > 0 |
| Ledger orphan reaper scheduled | `grep -c "sdlc-ledger-orphan-reap" config/reflections.yaml` | output > 0 |
| Its callable resolves | `python -c "from tools.sdlc_session_ensure import kill_orphans_reflection; assert callable(kill_orphans_reflection)"` | exit code 0 |
| Anti-criterion: no new Popoto field added | `git diff main --stat -- scripts/update/migrations.py` | output does not contain `migrations.py` |
| Anti-criterion: float-into-DatetimeField untouched (No-Go #2674) | `git diff main -- agent/health_check.py` | match count == 0 |
| Stale-migration docstring corrected | `grep -c "restamps ..updated_at.., that pass moves every record" scripts/_strip_migration.py` | match count == 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Resolved Decisions

Three questions surfaced while shaping this plan. Each was resolvable from evidence in the codebase, so each is decided here rather than deferred.

1. **Ledger skip vs. ledger reaping — split by authority, and schedule the missing half.** The `/update` reaper decides on *process liveness*; a ledger anchor has no process by construction, so it is outside that reaper's competence and gets skipped. The authority for a process-less row is issue-lock ownership, which `tools/sdlc_session_ensure.py::_iter_orphan_sessions` already implements correctly (`_lock_owner_is_live`, failing toward live on ambiguous evidence, with a 600s idle fallback when no payload resolves). Its selection criteria match a ledger anchor exactly, verified against the live row `sdlc-local-2643`.

   The gap: nothing schedules it. Grep finds `--kill-orphans` only in its own argparse wiring and tests — absent from `config/reflections.yaml`, `scripts/update/run.py`, and launchd. That is how 59 anchors accumulated, and it is why the skip cannot ship alone. Task 4 registers it as an hourly reflection. Recorded as Risk 5.

2. **`preserve_updated_at` + `update_fields` — documented precedence, logged, not an exception.** `AgentSession.save()` is called from fail-quiet paths throughout the codebase (`agent/health_check.py`, `bridge/promise_gate.py`, `agent/output_handler.py`, every archive and health sweep), all of which swallow exceptions to avoid crashing the agent. A hard error would therefore surface as a silently swallowed save, which is worse than the wrong timestamp it was meant to prevent. Both flags mean "do not stamp," so they do not actually conflict: `preserve_updated_at=True` wins, a WARNING names the caller, and the combination is documented as a caller smell rather than enforced.

3. **`last_heartbeat_at` rung scoped to the `/update` reaper only.** The other two readers are already correct and should not be converted. `agent/session_health.py` uses `last_heartbeat_at` as a first-class Tier 1 signal already (`:1664-1680`, `:1751-1775`, `_session_is_alive` at `:5846-5860`). `tools/sdlc_session_ensure.py` deliberately prefers the issue-lock payload over any timestamp, per #2305 defect 1, and demoted `updated_at` to an idle-window fallback for exactly this bug's reason. `scripts/update/run.py` is the one reader still treating `updated_at` as a liveness proxy without a stronger signal ahead of it, and it is the reader the issue names. No third conversion.
