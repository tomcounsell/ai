---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-08
tracking: https://github.com/tomcounsell/ai/issues/2660
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-08-07T18:15:37Z
---

# AgentSession.save() restamps updated_at on maintenance writes, faking liveness

## Problem

On 2026-08-07 the dashboard reported `last activity` of "just now" for nine Jobs that had settled on 07-16. Their `last_activity_at` all read `08-07 11:34` — one batch, written by a service restart. Earlier that day the same flattening landed on every `running` row on a repeating tick: `11:03:16 → 11:08:14 → 11:13:16`, 59 rows inside a ~16-second window each time.

Meanwhile `/update` kept reporting `Skipped 59 live session(s) (recent heartbeat)`. The stale-session reaper could see 59 rows that had not executed anything in weeks and skipped every one as freshly active.

**Current behavior:**

`AgentSession.save()` stamps `self.updated_at = utc_now()` on every unguarded call (`models/agent_session.py:1023`). There is a carve-out — `save(update_fields=[...])` skips the stamp when `update_fields` omits `updated_at` (`models/agent_session.py:1008`) — and the execution-path writers in `agent/session_health.py` and `agent/session_executor.py` use it correctly.

Three maintenance writers do not, and all three write whole rows:

1. **The corruption probe.** `cleanup_corrupted_agent_sessions()` calls `session.save()` on **every hydrated row**, terminal ones included, purely to see whether the save raises (`agent/session_health.py:5569-5571`, comment: "Try a no-op save to detect other validation failures"). It is not a no-op. It moves the entire table's `updated_at` in one pass. Four callers fire it: worker startup (`worker/__main__.py:763`), the hourly `agent-session-cleanup` reflection (`config/reflections.yaml:37-42`), `/update` Step 5.5 (`scripts/update/run.py:1935`), and the corrupted-pop handler in the queue loop (`agent/agent_session_queue.py:2308`).

2. **The archive restore.** `_rehydrate_row()` rebuilds each archived row as `AgentSession(id=archived_id, **fields).save()` (`agent/session_archive.py:433-443`), called from `restore_if_empty()` at worker startup (`worker/__main__.py:720`). The archived `updated_at` travels in the payload and is then overwritten with restore time.

3. **The sibling `session-ensure` run-lock bind.** `_acquire_run_lock_and_bind()` writes `session.active_run_id` and `owned_run_ids`, then calls a bare `session.save()` (`tools/sdlc_session_ensure.py:486-493`). Its docstring records that it "is called immediately before EVERY return point in `ensure_session()`," so every SDLC stage dispatch — including a dispatch that merely re-reads state and returns — restamps the row. This is the writer that keeps the 59 ledger anchors of the Problem statement permanently fresh, and it is the second half of a sentence this plan's own Prior Art quotes: #2305 defect 1 named "probes **and sibling `session-ensure` renewals**." The write itself is legitimate (two fields genuinely changed); the whole-row stamp attached to it is not.

The `/update` path makes the consequence self-defeating by construction. Step 5.5 runs the probe at `scripts/update/run.py:1935` and the reaper at `:1949`. The probe restamps every row seconds before `_cleanup_stale_sessions` reads `updated_at` recency against a 30-minute `RECENT_ACTIVITY_WINDOW` (`scripts/update/run.py:274-278`). Every fence-less row is guaranteed to land in `skipped_recent`. The reaper cannot reach a fence-less row on the `/update` path, ever.

The dashboard reads the same forged value: `best_timestamp()` prefers `updated_at` (`ui/data/sdlc.py:1241`) and feeds Job ordering. A batch write flattens the sort key across every touched row.

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
- `ui/data/sdlc.py:1241` — `best_timestamp()` prefers `updated_at` — still holds, exact line (`:1239` is the `def`, `:1241` is the `return p.completed_at or p.updated_at or ...`). An earlier revision of this plan mis-cited `:1240`; corrected throughout.
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
- **#2305 defect 1** (`tools/sdlc_session_ensure.py:882-896`): `updated_at` was demoted from authoritative liveness to idle-window fallback precisely because "it is refreshed by probes and sibling `session-ensure` renewals, which is exactly the mirage that let a hollow tracking session read as live forever." **Both named writers are this bug**, identified a release ago and worked around in one reader instead of fixed at the source: "probes" is `cleanup_corrupted_agent_sessions` (Task 1) and "sibling `session-ensure` renewals" is the bare `session.save()` at `tools/sdlc_session_ensure.py:493` (Task 3). Read the whole sentence, not the first noun.
- **#2098 / #2091 / #2439**: established that out-of-process actuation on session health is unsafe by design, and removed the `session-liveness-check` reflection. Relevant only as the adjacent-drift finding below.
- **#2494 / #2516 / #2538** (Durability M1): built the `(exec_pid, pid_create_time)` fence and closed nine unfenced consumers. The fence is the prior art the issue points at for "a liveness field only execution writes." Task 3 reads it as-is.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|--------------------------------|
| PR #1655 (#1645) | Moved the `updated_at` stamp from popoto `auto_now` into the `save()` override so it lands in UTC | Correct on timezone, silent on authorship. Moving the stamp into `save()` made *every* caller a liveness author, including maintenance sweeps that have nothing to report. |
| PR #2415 (#2305 defect 1) | Demoted `updated_at` to an idle-window fallback in `_iter_orphan_sessions` and documented that it "is refreshed by probes and sibling `session-ensure` renewals" | Fixed one reader, and named both writers in the same breath without touching either. Both kept writing, and every other reader (`/update`'s reaper, `best_timestamp`) kept trusting the forged value. |
| `145967f4b` | Added a `started_at` sort tiebreak so a batch write cannot flatten dashboard ordering | Treats the symptom at the presentation layer, as its own commit message says. The underlying value stays wrong for every non-dashboard reader. |

**Root cause pattern:** each fix moved a *reader* away from `updated_at` instead of stopping the *writer*. Three readers were patched over eleven months while three maintenance writers kept forging the value. The field is fine; the write authorization is missing.

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
5b. **`best_timestamp()`** (`ui/data/sdlc.py:1241`) → `group_into_jobs` → `Job.last_activity_at` (`ui/data/jobs.py:449`) → dashboard sort (`ui/data/jobs.py:493-494`). Output: "just now" on three-week-old work, and a flat primary sort key.

The parallel restore path: **`restore_if_empty()`** (`worker/__main__.py:720`) → **`_rehydrate_row()`** (`agent/session_archive.py:433-443`) → `AgentSession(id=..., **fields).save()` → same step 3, except the correct value was in `fields` and is discarded rather than merely refreshed.

The parallel SDLC path (the one that produced the 59 ledger anchors): any `/sdlc` stage dispatch → **`ensure_session()`** → **`_acquire_run_lock_and_bind()`** (`tools/sdlc_session_ensure.py:486-493`) → bare `session.save()` → same step 3. Because the bind runs before *every* return point, a stage that changes nothing still refreshes the row, and the anchor reads live to step 5a forever.

After this plan, step 3 is unreachable from step 2, from `_rehydrate_row`, and from `_acquire_run_lock_and_bind`; step 5a skips ledger anchors outright and consults `last_heartbeat_at` before falling to `updated_at`.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** `AgentSession.save()` gains one keyword-only-in-practice parameter, `preserve_updated_at: bool = False`. Default preserves every existing call site's behavior exactly. No signature change is visible to any caller that does not opt in.
- **Coupling:** decreases. `cleanup_corrupted_agent_sessions` stops being an `AgentSession`-hash writer for healthy rows and becomes a read-mostly auditor — it writes only on the delete path it already owns, plus the index maintenance (`repair_indexes()`/`clean_indexes()`, #1361) it has always run and keeps running. The corruption probe stops coupling "is this row valid" to "this row is now fresh," and `_acquire_run_lock_and_bind` stops coupling "this run owns the lock" to "this row just moved."
- **Data ownership:** clarified, which is the point. After this change, `updated_at` is owned by writers that have something to report, and `last_heartbeat_at` remains exclusively the execution heartbeat's. No new field, so **no Popoto schema change and no migration** — the Popoto Schema Migration Requirement in `docs/sdlc/do-plan.md` does not apply.
- **Reversibility:** partial, and the shape matters more than the grade. There are four code diffs, and they are **not** all independently revertable. Two revert units:

  | Unit | Diffs | Revertable alone? |
  |------|-------|-------------------|
  | A | Task 2 (`preserve_updated_at` + archive restore) | Yes. It touches only the cold-boot restore path and moves no live row's timestamp. |
  | B | Task 1 (read-only probe), Task 3 (`session-ensure` partial save), Task 4 (reaper `is_ledger` skip) | Tasks 1 and 3 revert alone. **Task 4 must not be reverted while either Task 1 or Task 3 is live.** |

  Task 4's skip is the guard for the exposure Tasks 1 and 3 create: once nothing restamps a ledger anchor, the `/update` process-liveness reaper reaches it through the 120-minute `created_at` floor and finalizes a live pipeline's state anchor. Reverting Task 4 alone is therefore the single revert an operator is most likely to reach for mid-incident and the one that does harm. Task 4's task body states this where the builder sees it, and all four land in the same PR.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1 (confirming the reaper's new ladder does not over-reap — the one place this can cause harm)
- Review rounds: 2 (round 1 landed 2 blockers; the ladder reading and the third writer both changed the shape of the work)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable for ORM-backed tests | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | The health/archive tests exercise real Redis, per the repo's no-mocks testing philosophy |
| On-pin interpreter | `python -c "import pathlib,sys; assert pathlib.Path('.python-version').read_text().strip().startswith('.'.join(map(str, sys.version_info[:2])))"` | `scripts/pytest-clean.sh` aborts on an off-pin venv |

## Solution

### Key Elements

- **Read-only corruption probe** — `cleanup_corrupted_agent_sessions` decides "is this row corrupt" without writing to it. The probe's write was incidental to its purpose; removing it removes the batch restamp at its largest source and also removes an O(N) Redis write amplification that ran on every worker start, every `/update`, and every hour.
- **Explicit timestamp preservation for restore** — the archive restore declares that it owns the `updated_at` it carries, so rehydration reproduces the archived row rather than a row that looks like it was written at restore time.
- **A narrowed run-lock bind write** — `_acquire_run_lock_and_bind` writes exactly the two fields it changed (`active_run_id`, `owned_run_ids`) through the existing `update_fields` carve-out, so a stage dispatch that only re-binds the run lock stops claiming the whole row moved. This is the writer that keeps ledger anchors permanently fresh, so it is the one that has to stop for the motivating population to be fixed at all.
- **An execution-only rung in the reaper's liveness ladder** — the `/update` reaper consults `last_heartbeat_at` (written only by the executor's heartbeat, never by any maintenance path) before falling back to `updated_at`.
- **A ledger guard on the reaper** — every worker loop skips `is_ledger` anchors; the `/update` reaper does not. The restamp has been masking that gap, and removing the restamp exposes it: a live anchor would reach the 120-minute `created_at` floor and be finalized mid-pipeline. The process-liveness reaper is structurally the wrong owner for a row that never had a process, so it skips ledgers. The skip restores today's net outcome for ledger rows (unreachable) by an honest mechanism instead of a forged timestamp — it adds no new accumulation, because the accumulation already exists and has its own owner (see No-Gos, #2677).

### Flow

`/update` runs → corruption cleanup audits every row and writes nothing → stale reaper skips ledger anchors outright → asks the fence → asks `last_heartbeat_at` → asks `updated_at` → asks `created_at` age → finalizes only rows that survive every rung → summary names which signal drove each skip.

`/sdlc` dispatches a stage → `ensure_session()` binds the run lock → the bind persists `active_run_id` and `owned_run_ids` and nothing else → the anchor's `updated_at` still reflects the last real stage advance.

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

**3. Narrow the `session-ensure` run-lock bind to a partial save (`tools/sdlc_session_ensure.py:486-493`, `models/agent_session.py:973-993`).**

The bind currently reads:

```python
session.active_run_id = candidate
_append_owned_run_id(session, candidate)   # sets session.owned_run_ids (JSON string), does not save
session.save()
```

Only those two fields changed, so the existing `update_fields` carve-out is the right primitive:

```python
session.save(update_fields=["active_run_id", "owned_run_ids"])
```

Two things pin the field list:

- **`preserve_updated_at` is the wrong tool here.** The post-save readback immediately below (`:509-515`) re-queries the row and asserts `active_run_id` round-tripped; a preserve-flagged full save would satisfy that but would still be a whole-row write with all the index churn. More importantly the carve-out already exists and already means exactly this. Use the primitive that is already sanctioned.
- **Both fields must appear.** `_append_owned_run_id` (`:112-140`) sets `session.owned_run_ids = json.dumps(owned)` in memory and deliberately does not save, because "the caller batches this with the `active_run_id` write in a single `session.save()`". Omitting `owned_run_ids` from `update_fields` would drop the #2446/#2451 self-recognition set on every bind. Field name verified in source: `owned_run_ids`.

`_append_owned_run_id` is best-effort and returns early when the id is already in the set, leaving `owned_run_ids` untouched. Listing it in `update_fields` in that case re-persists the value it already holds, which is a no-op in effect.

Add `active_run_id` and `owned_run_ids` to `AgentSession._UPDATED_AT_OMISSION_OK_FIELDS` (`models/agent_session.py:973`) under a new comment group ("SDLC run-identity bookkeeping — the bind runs before every `ensure_session()` return, so the stamp would be a per-dispatch restamp, not a report of activity"). Without this the omission logs a WARNING on every stage dispatch (`:1017-1021`), which is the log noise PR #1787 introduced the allowlist to prevent.

**What this does not touch:** the #1676 liveness path. Live worker-less CLI pipelines are kept alive by `updated_at` refreshed on **stage-state advances**, a different writer from the run-lock bind (`tools/sdlc_session_ensure.py:73-79` documents the distinction: "no heartbeat AND no `stage_states` write refreshing `updated_at`"). A pipeline that is actually advancing still refreshes the row; a pipeline that only re-dispatches without advancing no longer does, which is the whole point.

**4. Insert `last_heartbeat_at` into the reaper's ladder and skip ledgers (`scripts/update/run.py:250-300`).**

Read the current ladder precisely, because an earlier revision of this plan mislabeled it. `scripts/update/run.py:264-303`:

- fence live → `skipped_fence_live += 1; continue` — terminal.
- fence dead → `fence_verified_dead = True` and **no `continue`** (`:272`). The flag is a *reason-string selector* consumed at `:299-303`; control falls through to the `updated_at` recency gate at `:274-280` and then the `created_at >= threshold` gate at `:288-289`.

That fall-through is deliberate and pinned by two green tests: `tests/unit/test_update_stale_session_fence.py::test_fence_dead_but_recently_updated_is_still_spared` (`:199`) and `::test_fence_dead_but_young_by_created_at_is_still_spared` (`:215`), whose docstring states the invariant — **"The fence ADDS protection; it never subtracts it."** Making the fence-dead rung terminal would let `/update` finalize a row seconds after spawn, bypassing the 120-minute floor. That is a separate, more aggressive retention policy and it is **out of scope here**; this plan is a write-authorization fix and preserves the invariant exactly.

New ladder (every added rung is *additive* — it can only produce a skip, never a finalization):

1. live worker in `_active_workers` → skip (unchanged, terminal)
2. **new:** `is_ledger` → skip, terminal, counted as `skipped_ledger`. Placed first among the liveness rungs so no later rung can reach a ledger row. Ledger anchors (#2042) carry a non-terminal status for their whole life with no subprocess behind them; every worker loop already excludes them via `_is_ledger` (defined at `agent/session_health.py:51`, imported at `agent/agent_session_queue.py:88`, used at `agent/agent_session_queue.py:1466`, `:2754`, and in `_cli_flush_stuck` at `agent/agent_session_queue.py:3135` with the skip at `:3144`). The `/update` reaper is the one sweep that does not, and finalizing one kills a live SDLC pipeline's state anchor.
3. fence live → skip (terminal, unchanged). fence dead → set the reason selector and **fall through** (unchanged).
4. **new:** `last_heartbeat_at` within `RECENT_ACTIVITY_WINDOW` → skip, terminal, counted as `skipped_heartbeat`. Written only by `agent/session_executor.py:1070-1071` (T+0) and `:2226-2227` (60s tick), both via `save(update_fields=["last_heartbeat_at"])`. No maintenance path sets it.

   **The row this rescues, concretely** (the round-1 critique correctly asked for one): a session executing for longer than `RECENT_ACTIVITY_WINDOW` whose fence is absent or half-recorded. Because the heartbeat writes go through `update_fields=["last_heartbeat_at"]`, they *do not* move `updated_at` — that is the carve-out working as designed. So a long-running turn with no full save in 30 minutes has a stale `updated_at` and a 60-second-old heartbeat. Today, if its fence is missing (legacy row, or `pid_create_time` never recorded — the `fence_pid is not None and fence_ct is not None` guard at `:268` fails), it drops straight to the `created_at` gate and is finalized while actively executing. Rung 4 is the fix for that row, and it is reachable independently of the restamp bug.
5. `updated_at` within `RECENT_ACTIVITY_WINDOW` → skip (retained, and now honest). **This rung must stay**: #1676 established that live worker-less local CLI pipelines never write `last_heartbeat_at` and are kept alive precisely by `updated_at` refreshed on every stage-state advance (`tools/sdlc_session_ensure.py:73-79`). Dropping it would reap live pipelines.
6. `created_at` age ≥ threshold → finalize (unchanged)

Extend the return tuple from `tuple[int, int, int]` to `tuple[int, int, int, int, int]` — `(killed, skipped_recent, skipped_fence_live, skipped_ledger, skipped_heartbeat)` — and widen the caller's unpack and summary line (`scripts/update/run.py:1949`) so each skip names the signal that produced it. See Test Impact: the arity is asserted in **source text and in the return annotation**, not only behaviorally.

**5. Leave `best_timestamp()` alone (`ui/data/sdlc.py:1241`).**

Once all three forging writers stop, `updated_at` is the correct "most recent write" signal for dashboard ordering, and it is the *only* progress signal ledger sessions have (`ui/data/sdlc.py:343-348`). Changing the reader would break ledger ordering to fix a writer bug. A regression test pins the intent instead — and because the reported symptom lives at this layer, that test is a first-class Success Criterion rather than a deferral to the existing Job-ordering suite (see Success Criteria).

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `agent/session_health.py` corruption probe: the replacement `is_valid()` call is wrapped so a `ModelException` from the ttl/expire_at check is classified as corrupt rather than escaping the sweep. Test asserts the WARNING fires and the row is deleted, and that one raising row does not abort the loop over the remaining rows.
- [ ] `agent/session_archive.py:_rehydrate_row`: a payload missing `updated_at` must not persist `None`. Test asserts the fallback stamp is applied and a DEBUG line is emitted.
- [ ] `models/agent_session.py:save()`: `preserve_updated_at=True` combined with `update_fields=[...]` is a caller smell. Test asserts the Resolved Decision 2 precedence (preserve wins), that a WARNING is emitted, and that no exception escapes into the fail-quiet callers.
- [ ] `scripts/update/run.py:_cleanup_stale_sessions`: `last_heartbeat_at` present but unparseable (string, `None`, naive datetime) must fall through to the next rung rather than raising or being read as fresh. Test covers each shape.
- [ ] `tools/sdlc_session_ensure.py:_acquire_run_lock_and_bind`: the narrowed `save(update_fields=[...])` sits inside the existing `try/except` whose failure path calls `release_issue_lock` and returns `RUN_BIND_FAILED` (`:494-507`). Test asserts that path is unchanged when the partial save raises, and that the post-save readback at `:509-515` still sees `active_run_id`.
- [ ] `tools/sdlc_session_ensure.py:_append_owned_run_id` swallows its own failures and leaves `owned_run_ids` untouched (`:133-140`). Test asserts a bind whose append failed still persists `active_run_id` and does not write a `None` into `owned_run_ids`.

### Empty/Invalid Input Handling

- [ ] `cleanup_corrupted_agent_sessions` over an empty keyspace returns `{"corrupted": 0, "orphans": 0}` and writes no `AgentSession` hash. It still runs `repair_indexes()`/`clean_indexes()`, which are index writes by design (#1361) — see Success Criteria for why the assertion is on the field, not on a command count.
- [ ] A row whose `updated_at` is `None` (never saved through the stamping path) is not treated as infinitely stale by the reaper — it falls to the `created_at` rung, which is the existing documented behavior.
- [ ] No agent-output processing in scope; the empty-output silent-loop category does not apply.

### Error State Rendering

- [ ] `/update`'s summary line must distinguish `skipped_recent`, `skipped_heartbeat`, `skipped_fence_live`, and `skipped_ledger`. A skip that names no signal is the failure mode that hid this bug for months; the test asserts each counter appears with its own label, and the existing `"fence verified"` source-text assertion (`tests/unit/test_update_stale_session_fence.py:354-361`) must still find its substring after the reword.
- [ ] Dashboard rendering: a sweep over a populated keyspace must leave Job ordering and `last_activity_at` unchanged. Fixture seeds at least two **non-terminal** rows (`completed_at is None`) so `updated_at` is the operative term in `best_timestamp` (`ui/data/sdlc.py:1241`) rather than a vacuous pass through `completed_at`.

## Test Impact

- [ ] `tests/unit/test_session_health_phantom_guard.py` — UPDATE: the corruption-probe tests exercise `cleanup_corrupted_agent_sessions` against real records (`:118`, `:169`, `:280`, `:306`). None currently assert on `save()` being called, so they should keep passing unchanged; add assertions that a healthy row's `updated_at` is byte-identical before and after the sweep.
- [ ] `tests/unit/test_session_health_orphan_process_reap.py` — UPDATE: asserts the `{"corrupted": int, "orphans": int}` return shape (`:340`, `:352`). Return shape is unchanged; verify no test depends on the probe's write as a side effect.
- [ ] `tests/unit/test_session_health_unconditional_index_repair.py` — UPDATE: exercises the same sweep; confirm the index-repair assertions do not depend on the per-row save.
- [ ] `tests/unit/test_stale_cleanup.py` — UPDATE: covers `_cleanup_stale_sessions`; the return tuple goes from 3 to 5 values, so every unpack site needs widening. Add the ledger-skip and heartbeat-skip cases.
- [ ] `tests/unit/test_update_stale_session_fence.py` — UPDATE, and it is under-scoped by "add an assertion": the arity change breaks an entire test class that reasoning about the ladder will never surface, because its assertions are on **source text and signature metadata**, not behavior. All of the following are in scope, in the same commit as the arity change:
  - `::test_fence_dead_but_recently_updated_is_still_spared` (`:199`) and `::test_fence_dead_but_young_by_created_at_is_still_spared` (`:215`) — **KEEP GREEN, unchanged.** These pin the "the fence ADDS protection; it never subtracts it" invariant that Technical Approach step 4 preserves. If a build makes either of them fail, the build changed retention policy and has left this plan's scope.
  - `:327` — `assert counts == (1, 1, 1)` — UPDATE to the 5-tuple, with the comment naming all five positions.
  - `class TestCallerUnpacksThreeValues` (`:332`) — RENAME; the class name encodes the old arity.
  - `::test_run_update_unpacks_the_new_arity` (`:333-346`) — UPDATE: asserts the literal source string `"stale_killed, skipped_recent, skipped_fence_live = _cleanup_stale_sessions("` appears in `inspect.getsource(run_module.run_update)`. Rewrite to the 5-name unpack. This fails on string identity, so the new names must match the implementation character for character.
  - `::test_function_signature_declares_a_three_tuple` (`:348-352`) — UPDATE and RENAME: asserts `inspect.signature(_cleanup_stale_sessions).return_annotation in ("tuple[int, int, int]", tuple[int, int, int])`. Both the string and the typing form must move to the 5-tuple.
  - `::test_run_update_logs_the_fence_skip_distinctly` (`:354-361`) — KEEP GREEN: asserts `"fence verified"` survives in `run_update`'s source. Preserve that substring when rewording the summary for the two new counters.
  - ADD: the new heartbeat rung is additive. A fence-dead row with a fresh `last_heartbeat_at` is **spared** (rung 4 skips it), which is the same asymmetry the two existing fence-dead tests assert. Pin it.
- [ ] `tests/unit/test_session_archive.py` — UPDATE: add restore-preserves-`updated_at` coverage alongside the existing rehydrate assertions.
- [ ] `tests/unit/test_session_archive_cli.py` — UPDATE: verify the `restore --dry-run` path is unaffected (it writes nothing either way).
- [ ] `tests/integration/test_session_archive_cold_boot.py` — UPDATE: the cold-boot round trip is the natural home for the end-to-end assertion that an archived timestamp survives restore.
- [ ] `tests/unit/test_worker_persistent.py` / `tests/unit/test_worker_entry.py` — UPDATE: both patch `cleanup_corrupted_agent_sessions` at the worker-startup call site (`:157`, `:200`); confirm the patches still bind after the internal change.
- [ ] `tests/unit/test_migrate_strip_pid_fields.py` — UPDATE: `scripts/_strip_migration.py:32-40` documents the restamp as a known concurrent-writer property in its safety argument. That paragraph becomes wrong when Task 1 lands. Update the module docstring and any test asserting on it — the atomicity argument (one MULTI/EXEC pipeline) still holds and is the load-bearing half; only the "who else writes terminal rows" example changes.
- [ ] `tests/unit/test_recovery_respawn_safety.py::TestStartupRecoveryOwnershipGuard::test_stale_legacy_session_without_stamp_still_recovered` (`:965`) — UPDATE: exercises the reaper's legacy-row age fallback for a session with no ownership stamp. Verify the new ladder does not change its outcome (a legacy row has neither `is_ledger`, a fence, nor `last_heartbeat_at`, so it should reach the same rung it does today) and add the assertion that pins that.
- [ ] `tests/unit/test_sdlc_session_ensure.py` — UPDATE: the run-lock bind is heavily covered here (`_acquire_run_lock_and_bind`, the post-save readback, `RUN_BIND_FAILED`, the #2446/#2451 `owned_run_ids` set). Audit every test that asserts on a bind and confirm none depends on the whole-row write. ADD: a bind does not move `updated_at`; a bind still persists both `active_run_id` and `owned_run_ids`; a bind whose `_append_owned_run_id` failed still persists `active_run_id`.
- [ ] `tests/unit/test_agent_session_updated_at_utc.py` — UPDATE: `class TestSaveUpdatedAtOmissionAllowlist` (`:190`) is the sole suite asserting on `_UPDATED_AT_OMISSION_OK_FIELDS`. Its parametrized `test_allowlisted_liveness_fields_log_debug_not_warning` (`:236`) gains an `["active_run_id", "owned_run_ids"]` case, and `test_non_allowlisted_omission_still_warns` (`:260`) must be checked for a parameter that the two additions would now silently move to DEBUG. Also add a `preserve_updated_at` case to `class TestSaveUpdateFieldsGuard` (`:129`).
- [ ] `tests/unit/test_ui_jobs_grouping.py` — ADD: the dashboard-symptom regression, which has **no existing coverage** (grep finds `last_activity_at` asserted only in `tests/unit/test_session_modal_liveness_render.py` and `tests/unit/test_sdlc_session_ensure.py`, neither of which touches Job ordering). Seed at least two **non-terminal** `AgentSession` rows with distinct `updated_at` values, run `cleanup_corrupted_agent_sessions()`, and assert `Job.last_activity_at` (`ui/data/jobs.py:449`) and the sort order (`ui/data/jobs.py:493-494`) are unchanged. Non-terminal is load-bearing: `best_timestamp` (`ui/data/sdlc.py:1241`) returns `p.completed_at or p.updated_at or ...`, so a terminal fixture passes vacuously through `completed_at` and proves nothing about the field this plan fixes. `class TestJobRollup` (`:230`) is the natural home.
- [ ] `tests/unit/test_reflection_scheduler.py` — NO CHANGE. The `sdlc-ledger-orphan-reap` registration moved out of this plan to #2677; `config/reflections.yaml` is untouched here.
- [ ] No new xfail conversions: no `pytest.mark.xfail` or runtime `pytest.xfail()` in the suite references this bug. Searched `tests/` for xfail markers matching `updated_at` / `restamp` / `liveness` / stale-session — zero hits.

## Rabbit Holes

- **Auditing all ~120 unguarded `.save()` call sites in the repo.** The issue asks for the maintenance writers, and recon plus critique round 1 isolated them to three. Execution-path full saves (`agent/health_check.py`, `bridge/session_transcript.py`, `models/session_lifecycle.py` transitions, `tools/stage_states_helpers.py`) are reporting real activity — restamping is correct there. Converting them wholesale would be a large diff that changes nothing about the reported bug and risks breaking the `#1676` worker-less-pipeline liveness signal.
- **Adding a new execution-only liveness field.** The issue floats this. `last_heartbeat_at` already is one: written only by the executor's heartbeat loop, already in `_UPDATED_AT_OMISSION_OK_FIELDS`, already trusted by `_session_is_alive` (`agent/session_health.py:5846-5853`) and `_iter_orphan_sessions` (`tools/sdlc_session_ensure.py:990`). Minting a second one means a Popoto schema change, a migration, and a backfill for zero new signal.
- **Fixing `agent/health_check.py:625`'s float-into-`DatetimeField` write.** Real, adjacent, and genuinely wrong (`s.updated_at = time.time()` where everyone else stores a `datetime`). It is an execution path, so it is not this bug, and changing the stored type mid-plan risks every reader that does `isinstance` dispatch. See No-Gos.
- **Rewriting `best_timestamp()` or the Job sort.** `145967f4b` already added the tiebreak. Once the writers are honest the reader is correct. Touching it again is churn.
- **Reordering `/update` Step 5.5.** Tempting, because the probe-before-reaper ordering is what makes the self-defeat total. With Task 1 the probe writes nothing and the ordering stops mattering. A reorder would encode the old assumption in the call graph and hide a regression if the probe ever writes again. Pin the invariant with a test instead.

## Risks

### Risk 1: `is_valid()` classifies rows as corrupt that the old save-probe let live, and they get deleted

**Impact:** Data loss. The probe's positive result routes straight to `session.delete()` (`agent/session_health.py:5582+`).

**Mitigation:** `popoto.Model.pre_save` raises `ModelException` **iff** `is_valid()` is False (`base.py:908-913`), so the two predicates agree by construction — the old code's string match on `"invalid"`/`"validation"` was a lossy proxy for exactly this call. The one deliberate widening is the ttl/expire_at mutual-exclusion `ModelException`, which `is_valid()` raises before the field loop; `AgentSession` sets neither `ttl` nor `expire_at`, so that branch is unreachable for this model — verified by grep. The build lands a test that asserts a healthy row is never classified corrupt across the full field surface, and the sweep's existing per-row try/except keeps one pathological row from cascading.

### Risk 2: the reaper, no longer blinded, starts finalizing rows it could never reach before

**Impact:** This is the sharpest edge in the plan. The 59 rows it was skipping were ledger anchors. Reaching them without a guard would finalize the state anchor of a live SDLC pipeline mid-run, at the 120-minute `created_at` floor.

**Mitigation:** the `is_ledger` skip (Task 4, rung 2) lands in the same PR as the write-authorization fixes, never after them, and is ordered *before* the fence check so no ledger can be finalized by any subsequent rung.

**Net effect on any row, stated precisely** (an earlier revision of this claim rested on a mislabeled ladder — see Technical Approach step 4 for the corrected reading):

| Row shape | Before | After |
|---|---|---|
| `is_ledger` | skipped via forged `updated_at` | skipped via `skipped_ledger`, terminal |
| fence live | `skipped_fence_live` | unchanged |
| fence dead, fresh `updated_at` | spared at the recency gate | unchanged (the fence-dead rung is **not** terminal, and this plan does not make it terminal) |
| fence dead, fresh `last_heartbeat_at`, stale `updated_at` | finalized at the `created_at` gate | **spared** at the new rung 4 |
| no fence, mid-execution, stale `updated_at` | finalized at the `created_at` gate | **spared** at the new rung 4 |
| no fence, genuinely idle | finalized at the `created_at` gate | unchanged |

Every added rung is a `continue`. No row moves from spared to finalized. The `updated_at` rung is retained for the #1676 worker-less-pipeline case, which is the one row shape whose protection depends on a signal this plan is narrowing.

### Risk 3: the ledger skip does not fix ledger accumulation

**Impact:** Once the `/update` reaper explicitly skips ledger anchors, nothing reaps them. They accumulate — which is the state that produced the 59 rows.

**Mitigation, and the honest framing:** the skip introduces **no regression**, because the accumulation predates it and has a different cause. Today the `/update` reaper already cannot reach a fence-less ledger row: the Problem statement establishes that the probe restamps every row seconds before the reaper reads it, so "every fence-less row is guaranteed to land in `skipped_recent`. The reaper cannot reach a fence-less row on the `/update` path, ever." The skip replaces a forged mechanism with an honest one and holds the net outcome constant.

The real gap is that `tools/sdlc_session_ensure.py --kill-orphans` — the tool that *is* the right authority, deciding on issue-lock ownership rather than process liveness — is a manual CLI that nothing schedules. That is tracked as **#2677** and deliberately excluded here (see No-Gos). Scheduling it turns a human-invoked CLI into an unattended hourly fleet-wide `finalize_session()` actuator on `running` eng sessions, which is the exact class #2439 removed as unsafe by design. It needs its own safety argument, a soak, a kill switch, and a metric, none of which belong bolted onto a timestamp-authorship fix.

**And this plan makes that follow-up strictly harder, which is recorded in #2677:** Task 3 stops the sibling-renewal restamp, so `_iter_orphan_sessions`'s no-payload fallback (`tools/sdlc_session_ensure.py:1013-1018`, reaping purely on `_last_activity_at >= ORPHAN_AGE_SECONDS` where `_last_activity_at` reads `updated_at` first at `:897`) becomes reachable for anchors it previously could not touch. 600 seconds is short for a live BUILD stage. That window has to be re-derived before anything schedules the reaper. The manual CLI is unaffected in practice — the issue-lock payload normally resolves and short-circuits the fallback — but the margin narrows, and an unattended caller must not inherit it silently.

### Risk 4: `_strip_migration.py`'s safety argument silently becomes stale

**Impact:** A future reader trusts a docstring that describes a concurrent writer which no longer exists, and draws the wrong conclusion about what the MULTI/EXEC pipeline is protecting against.

**Mitigation:** `scripts/_strip_migration.py:32-40` is an explicit Test Impact item. The load-bearing half of its argument (atomicity, not quiescence) survives; only the cited example changes. Per the repo's no-legacy-code rule, the paragraph is rewritten to the new status quo rather than annotated with history.

### Risk 5: restore preserves a timestamp from a row that was archived mid-corruption

**Impact:** A restored row could carry an `updated_at` that is future-dated or nonsensical relative to restore time.

**Mitigation:** `_heal_future_updated_at` already detects (read-only) future-dated rows and logs them for operator visibility, and `agent/session_health.py`'s staleness reads use Redis `TIME` as a single shared clock, which #1817 established makes a skewed `updated_at` harmless to read. Preserving a bad archived value is strictly no worse than the current behavior of overwriting it with a value that is *wrong in a way nothing can detect*.

### Risk 6: narrowing the run-lock bind removes a liveness signal something still depends on

**Impact:** The bare `session.save()` at `tools/sdlc_session_ensure.py:493` runs before every `ensure_session()` return. Any reader that has come to rely on "a stage dispatch happened recently" arriving as `updated_at` loses it. The candidate victim is #1676's worker-less local CLI pipeline, kept alive by `updated_at` and nothing else.

**Mitigation:** the #1676 signal is a **different writer**. `tools/sdlc_session_ensure.py:73-79` defines the orphan predicate as "no heartbeat AND no `stage_states` write refreshing `updated_at`" — the stage-state advance, not the run-lock bind. A pipeline that is actually advancing still refreshes the row through that path. A pipeline that only re-dispatches the same stage without advancing stops refreshing it, which is precisely the mirage #2305 defect 1 named and the behavior this plan exists to remove.

The three known consumers are enumerated and each is checked in the build: `_iter_orphan_sessions`'s no-payload fallback (covered under Risk 3 and #2677), `/update`'s rung 5 (a genuinely advancing pipeline still refreshes it), and `best_timestamp` (a ledger anchor's ordering now reflects real stage advances rather than dispatch churn, which is the fix). Task 3's test set pins each.

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
- [SEPARATE-SLUG #2677] **Scheduling the ledger orphan reaper.** `tools/sdlc_session_ensure.py --kill-orphans` is the correct authority for a process-less ledger anchor and nothing invokes it. Carved out of this plan during critique round 1 for three reasons: (a) it converts a human-invoked CLI into an unattended hourly fleet-wide `finalize_session()` actuator on `running` eng sessions, the class #2439 removed as unsafe by design, and that needs its own safety argument rather than a bundled task; (b) it needs a soak, an env kill switch, and a metric, none of which are timestamp-authorship work; (c) Task 3 here narrows the `updated_at` signal its no-payload fallback depends on, so `ORPHAN_AGE_SECONDS` has to be re-derived first. See Risk 3. Landing the `is_ledger` skip without it is safe: the skip holds today's net outcome for ledger rows constant rather than regressing it.
- **Making the fence-dead rung terminal in `_cleanup_stale_sessions`.** A dead fence currently selects a reason string and falls through to the recency and age gates (`scripts/update/run.py:272`), an asymmetry two green tests pin as "the fence ADDS protection; it never subtracts it." Making it terminal would finalize rows seconds after spawn, bypassing the 120-minute floor. That is a retention-policy change; this plan is a write-authorization fix. If it is wanted, it belongs in its own issue with its own risk analysis.

## Update System

No `/update` **skill** changes required, but `scripts/update/run.py` is directly modified by Task 4 (the reaper's liveness ladder and its summary counters). No new dependencies and no migration for existing installations: the change adds no Popoto field, so `scripts/update/migrations.py` is untouched and `data/migrations_completed.json` gains no entry.

**No config file changes.** `config/reflections.yaml` is untouched — the `sdlc-ledger-orphan-reap` registration moved to #2677.

One propagation note: the fix changes what `/update` reports. Machines running the old code will keep printing `Skipped N live session(s) (recent heartbeat)` until they pull. That is cosmetic and self-resolving on the next `/update`; no coordinated rollout is needed.

## Agent Integration

No agent integration required. This is a worker/maintenance-internal change. No new CLI entry point in `pyproject.toml [project.scripts]`, no new MCP surface, and no bridge import — the four touched modules (`agent/session_health.py`, `agent/session_archive.py`, `tools/sdlc_session_ensure.py`, `scripts/update/run.py`) are already reachable from the worker, the SDLC CLI, and the update script, and `models/agent_session.py:save()` gains a default-off parameter that no agent-facing path passes. `tools/sdlc_session_ensure.py`'s CLI surface is unchanged: Task 3 narrows an internal write and adds no flag.

## Documentation

### Feature Documentation

- [ ] Create `docs/features/agent-session-liveness-authorship.md` — the durable statement of who is authorized to write which liveness field on `AgentSession`: `updated_at` (any writer with something to report; never a maintenance sweep and never a bind that only re-asserts identity), `last_heartbeat_at` (the executor heartbeat only), `last_turn_at` / `last_tool_use_at` (turn and tool boundaries only), and the `(exec_pid, pid_create_time)` fence (spawn only). Include the reaper's full liveness ladder **with the fence-dead fall-through stated explicitly** (it is the detail an earlier revision of this plan got wrong, and the doc is where a future reader should be able to check it), the #1676 constraint that keeps `updated_at` in the ladder, the distinction between a stage-state advance and a run-lock bind as `updated_at` writers, and the split of reaping authority between the `/update` process-liveness reaper and the issue-lock-based orphan reaper (#2677).
- [ ] Add the entry to the `docs/features/README.md` index table.
- [ ] Update `docs/features/agent-session-health-monitor.md` — it describes stale-session detection and recovery, which is the behavior the reaper's new ladder changes. Add the ladder and cross-link the authorship doc.
- [ ] Cross-link from `docs/features/agent-session-fenced-execution-record.md` — the fence is the top rung of the ladder, and the authorship doc is the natural place a reader goes next.

### External Documentation Site

Not applicable — this repo has no external docs site.

### Inline Documentation

- [ ] Rewrite the `agent/session_health.py:5568` comment: "Try a no-op save to detect other validation failures" is the sentence that made this bug, and its replacement should say why the check is read-only, citing #1817's identical lesson in the same file.
- [ ] Extend the `AgentSession.save()` docstring (`models/agent_session.py:995-1013`) to document `preserve_updated_at` alongside the existing `update_fields` guard paragraph.
- [ ] Rewrite `scripts/_strip_migration.py:32-40` to the new status quo (atomicity is the safety property; the restamping-probe example is gone).
- [ ] Update `_cleanup_stale_sessions`'s docstring (`scripts/update/run.py:189-213`) with the new ladder and the ledger skip. State the fence-dead fall-through explicitly — the current docstring says a fence-dead session "is finalized with a reason that says the fence verified it," which reads as terminal and is the sentence that misled this plan's first revision.
- [ ] Document the narrowed bind at `tools/sdlc_session_ensure.py:486-493`: a comment stating that the partial save is deliberate, that the bind runs before every `ensure_session()` return so a whole-row stamp would be a per-dispatch restamp rather than a report of activity, and that the #1676 liveness signal comes from the stage-state advance instead.

## Success Criteria

- [ ] `cleanup_corrupted_agent_sessions()` writes no `AgentSession` hash on the healthy path: each seeded healthy row's `updated_at` is byte-identical before and after a sweep. **The assertion is on the field, not on a Redis command count.** The sweep unconditionally calls `AgentSession.repair_indexes()` (`agent/session_health.py:5649`, the "Unconditional `repair_indexes()` (issue #1361)" block whose comment records that the prior gate was removed permanently) and then `clean_indexes()` on `AgentSession` and `Memory`. Those are index writes by design and stay. A command-count spy is guaranteed non-zero on a correct build, so a literal "zero writes" criterion would either fail a correct implementation or push a builder into re-introducing the #1361 gate.
- [ ] Its corruption-detection behavior is unchanged: a row that the old save-probe classified corrupt is still classified corrupt, and a healthy row is never classified corrupt.
- [ ] Archive restore reproduces the archived `updated_at` rather than restore time, and falls back to a fresh stamp when the payload carries none.
- [ ] A `/sdlc` stage dispatch that only re-binds the run lock leaves `updated_at` unmoved, while still persisting `active_run_id` and `owned_run_ids` (the post-save readback at `tools/sdlc_session_ensure.py:509-515` still passes). A dispatch that advances a stage state still refreshes `updated_at`.
- [ ] `_cleanup_stale_sessions` skips `is_ledger` rows before evaluating any liveness rung, and reports `skipped_ledger` separately.
- [ ] `_cleanup_stale_sessions` consults `last_heartbeat_at` between the fence and `updated_at`, and reports `skipped_heartbeat` separately.
- [ ] **The fence-dead fall-through is preserved.** `tests/unit/test_update_stale_session_fence.py::test_fence_dead_but_recently_updated_is_still_spared` and `::test_fence_dead_but_young_by_created_at_is_still_spared` pass unchanged, and a fence-dead row with a fresh `last_heartbeat_at` is **spared** by the new rung. Every added rung is a `continue`; no row moves from spared to finalized.
- [ ] The `updated_at` rung survives, and a live worker-less local pipeline (#1676: no `last_heartbeat_at`, fresh `updated_at` from a stage-state advance) is still skipped.
- [ ] **The reported dashboard symptom is closed end to end.** With at least two **non-terminal** `AgentSession` rows seeded at distinct `updated_at` values, a full `cleanup_corrupted_agent_sessions()` sweep leaves `Job.last_activity_at` (`ui/data/jobs.py:449`) and the Job sort order (`:493-494`) unchanged. Non-terminal is required: `best_timestamp` (`ui/data/sdlc.py:1241`) short-circuits on `completed_at`, so a terminal fixture passes vacuously.
- [ ] Tests pass (`/do-test`, via `scripts/pytest-clean.sh`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No agent integration to grep for — the Agent Integration section declares none.
- [ ] No xfail conversions needed — none exist for this bug.
- [ ] Anti-criterion: `config/reflections.yaml` is unchanged. The ledger orphan reaper's registration is #2677.

## Team Orchestration

### Team Members

- **Builder (probe + save primitive)**
  - Name: `probe-builder`
  - Role: Task 1 and Task 2 — read-only corruption probe, `preserve_updated_at` on `AgentSession.save()`, archive restore opt-in
  - Agent Type: builder
  - Resume: true

- **Builder (SDLC bind + reaper ladder)**
  - Name: `reaper-builder`
  - Role: Task 3 (narrowed run-lock bind, allowlist additions) and Task 4 (`is_ledger` skip, `last_heartbeat_at` rung, 5-tuple arity, summary counters)
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `restamp-tests`
  - Role: Task 5 — regression coverage across the suites named in Test Impact, plus the new dashboard-symptom test
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `restamp-validator`
  - Role: Task 7 — verify Success Criteria, especially the no-write property, the fence-dead fall-through, and the dashboard end-to-end assertion
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `restamp-docs`
  - Role: Task 6 — the feature doc, the index entry, and the five inline rewrites
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

### 3. Narrow the `session-ensure` run-lock bind

- **Task ID**: build-bind
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_session_ensure.py`, `tests/unit/test_agent_session_updated_at_utc.py`
- **Informed By**: Technical Approach step 3; Prior Art #2305 defect 1 (this is the "sibling `session-ensure` renewals" half of the sentence); Risk 6 (the #1676 signal comes from a different writer)
- **Assigned To**: `reaper-builder`
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: true
- Change the bare `session.save()` at `tools/sdlc_session_ensure.py:493` to `session.save(update_fields=["active_run_id", "owned_run_ids"])`. Both fields are required: `_append_owned_run_id` (`:112-140`) sets `session.owned_run_ids` in memory and deliberately does not save, and the post-save readback (`:509-515`) asserts `active_run_id` round-tripped. Do **not** use `preserve_updated_at` here — the `update_fields` carve-out already exists and already means exactly this.
- Add `active_run_id` and `owned_run_ids` to `AgentSession._UPDATED_AT_OMISSION_OK_FIELDS` (`models/agent_session.py:973`) under a new comment group for SDLC run-identity bookkeeping. Without this every stage dispatch logs the `:1018` WARNING, which is the noise PR #1787's allowlist exists to prevent.
- Add the inline comment explaining why the write is partial (the bind runs before every `ensure_session()` return).
- Leave the `try/except` → `release_issue_lock` → `RUN_BIND_FAILED` path (`:494-507`) structurally unchanged.

### 4. Reaper liveness ladder

- **Task ID**: build-reaper
- **Depends On**: none
- **Validates**: `tests/unit/test_stale_cleanup.py`, `tests/unit/test_update_stale_session_fence.py`, `tests/unit/test_recovery_respawn_safety.py`
- **Informed By**: Prior Art #1676 (the `updated_at` rung must survive); Risk 2 (the ledger guard must land with the write-authorization fixes, not after); Technical Approach step 4 (the fence-dead rung is **not** terminal)
- **Assigned To**: `reaper-builder`
- **Agent Type**: builder
- **Parallel**: true
- **Do not land Tasks 1 or 3 without this task in the same PR.** This skip is the guard for the ledger exposure those tasks create; see Architectural Impact → Reversibility.
- Add the `is_ledger` skip to `_cleanup_stale_sessions` (`scripts/update/run.py:250`), ordered before the fence check, terminal, counted as `skipped_ledger`. Reuse the existing `_is_ledger` helper (defined at `agent/session_health.py:51`) rather than re-deriving the predicate.
- Add the `last_heartbeat_at` recency rung between the fence and the `updated_at` fallback, counted as `skipped_heartbeat`, tolerating naive-datetime / float / unparseable values by falling through.
- **Preserve the fence-dead fall-through.** `fence_verified_dead = True` at `:272` selects a reason string and does not `continue`; leave it that way. Two green tests pin this (`test_fence_dead_but_recently_updated_is_still_spared`, `test_fence_dead_but_young_by_created_at_is_still_spared`) and both must stay green untouched. Making the rung terminal is an explicit No-Go.
- Widen the return tuple to `tuple[int, int, int, int, int]` and the caller's unpack and summary at `scripts/update/run.py:1949` so each skip names its signal. The arity is asserted in source text and in the return annotation, not only behaviorally — see Test Impact.
- Update the function docstring (`:189-213`) with the new ladder, stating the fence-dead fall-through explicitly.

### 5. Regression coverage

- **Task ID**: test-restamp
- **Depends On**: build-probe, build-preserve, build-bind, build-reaper
- **Assigned To**: `restamp-tests`
- **Agent Type**: test-engineer
- **Parallel**: false
- Work the Test Impact checklist item by item, including the `scripts/_strip_migration.py` docstring rewrite and the full `test_update_stale_session_fence.py` sub-list (the source-text and return-annotation assertions are the ones that will not surface from behavioral reasoning).
- Add the no-write assertion (`updated_at` byte-identical across a sweep, asserted on the field rather than a command count) and the corruption-parity assertion.
- Add the ledger-skip, heartbeat-skip, fence-dead-fall-through, bind-does-not-restamp, and #1676 worker-less-pipeline cases.
- Add the dashboard-symptom test in `tests/unit/test_ui_jobs_grouping.py` with at least two non-terminal rows.
- Add the Failure Path Test Strategy cases for each of the six exception surfaces.
- Mutation-check each new guard: disable it and confirm the corresponding test goes red before re-enabling. A guard whose test stays green when the guard is removed is not covered.

### 6. Documentation

- **Task ID**: document-feature
- **Depends On**: test-restamp
- **Assigned To**: `restamp-docs`
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/agent-session-liveness-authorship.md` and add the `docs/features/README.md` index entry.
- Complete the five inline rewrites listed under Documentation → Inline Documentation.

### 7. Final validation

- **Task ID**: validate-all
- **Depends On**: build-probe, build-preserve, build-bind, build-reaper, test-restamp, document-feature
- **Assigned To**: `restamp-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row.
- Confirm each Success Criteria item, with particular attention to the no-write property, the preserved fence-dead fall-through, and the dashboard end-to-end assertion.
- Confirm the two anti-criteria: `config/reflections.yaml` unchanged, `agent/health_check.py` unchanged.
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `./scripts/pytest-clean.sh tests/unit/test_session_health_phantom_guard.py tests/unit/test_stale_cleanup.py tests/unit/test_update_stale_session_fence.py tests/unit/test_session_archive.py tests/unit/test_sdlc_session_ensure.py tests/unit/test_agent_session_updated_at_utc.py tests/unit/test_ui_jobs_grouping.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Corruption probe no longer saves | `grep -n "session.save()" agent/session_health.py` | exit code 1 |
| Probe uses read-only validation | `grep -c "is_valid()" agent/session_health.py` | output > 0 |
| Restore preserves the archived timestamp | `grep -c "preserve_updated_at=True" agent/session_archive.py` | output > 0 |
| `save()` honors the preserve flag | `grep -c "preserve_updated_at" models/agent_session.py` | output > 0 |
| Run-lock bind is a partial save | `grep -c 'save(update_fields=\["active_run_id", "owned_run_ids"\])' tools/sdlc_session_ensure.py` | output > 0 |
| No bare `session.save()` left in `_acquire_run_lock_and_bind` | `sed -n '483,495p' tools/sdlc_session_ensure.py \| grep -c 'session.save()'` | output == 0 |
| Bind fields are on the omission allowlist | `python -c "from models.agent_session import AgentSession as A; s=A._UPDATED_AT_OMISSION_OK_FIELDS; assert {'active_run_id','owned_run_ids'} <= s"` | exit code 0 |
| Reaper skips ledger anchors | `grep -c "_is_ledger\|is_ledger" scripts/update/run.py` | output > 0 |
| Reaper consults execution-only liveness | `grep -c "last_heartbeat_at" scripts/update/run.py` | output > 0 |
| `updated_at` rung retained (#1676) | `grep -c "updated_at" scripts/update/run.py` | output > 0 |
| Reaper arity widened to 5 | `python -c "import inspect; from scripts.update.run import _cleanup_stale_sessions as f; a=inspect.signature(f).return_annotation; assert a in ('tuple[int, int, int, int, int]', tuple[int,int,int,int,int]), a"` | exit code 0 |
| Fence-dead fall-through preserved | `./scripts/pytest-clean.sh "tests/unit/test_update_stale_session_fence.py::TestFenceDeadSession" -q` | exit code 0 |
| Anti-criterion: no new Popoto field added | `git diff main --stat -- scripts/update/migrations.py` | output does not contain `migrations.py` |
| Anti-criterion: float-into-DatetimeField untouched (No-Go #2674) | `git diff main --stat -- agent/health_check.py` | empty output |
| Anti-criterion: reflection registry untouched (No-Go #2677) | `git diff main --stat -- config/reflections.yaml` | empty output |
| Stale-migration docstring corrected | `grep -c "restamps ..updated_at.., that pass moves every record" scripts/_strip_migration.py` | match count == 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | Technical Approach step 3 rung 3 labels "fence dead → finalize" as *(unchanged)*, but it is not the current behavior and it inverts two green tests. In `scripts/update/run.py:271-278` a dead fence only sets `fence_verified_dead = True` (a reason-string selector) and falls THROUGH to the `updated_at` recency gate at `:277` and the `created_at >= threshold` gate at `:290`. Making the rung terminal lets the reaper finalize a row seconds after spawn, bypassing the 120-minute age floor. Risk 2's claim that a row "can only move from skipped for the wrong reason to skipped for the right reason or finalized on evidence" rests on the false label. | **RESOLVED — kept non-terminal.** Technical Approach step 4 now reads the ladder correctly and states the fall-through explicitly; making the rung terminal is an explicit No-Go (retention policy, not write authorization). Risk 2's net-effect claim is replaced by a per-row-shape table. Both tests stay green untouched and are listed under Test Impact as KEEP GREEN. Task 4's body forbids the change. | The change is the difference between the current `fence_verified_dead = True` with no `continue` at `scripts/update/run.py:277` and an added `if fence_verified_dead: finalize_session(...); killed_count += 1; continue` placed after the new `is_ledger` skip. Pick one explicitly. If terminal is chosen, two currently-green tests invert and MUST be listed for replacement: `tests/unit/test_update_stale_session_fence.py::test_fence_dead_but_recently_updated_is_still_spared` (`:199`, asserts `skipped_recent == 1` and `finalize.assert_not_called()`) and `::test_fence_dead_but_young_by_created_at_is_still_spared` (`:215`). |
| BLOCKER | History & Consistency | A third unguarded `updated_at` forger is missed, and the plan's own Prior Art quotes the sentence naming it. #2305 defect 1 says `updated_at` is refreshed by probes **and sibling `session-ensure` renewals**; the plan extracts only "probes". `tools/sdlc_session_ensure.py:493` calls a bare `session.save()` inside `_acquire_run_lock_and_bind`, whose docstring says it is "Called immediately before EVERY return point in ensure_session()". Those restamped rows are exactly the 59 ledger anchors in the Problem statement, so after Tasks 1 and 2 the Desired Outcome is still false for the motivating population, and Task 4's no-payload fallback (`_last_activity_at`, `updated_at`-first, 600s) stays permanently fresh on any supervised anchor. | **RESOLVED — new Task 3.** Problem statement now names three maintenance writers; Prior Art and Why-Previous-Fixes-Failed corrected to quote the whole #2305 sentence; Data Flow gains the SDLC path. Task 3 uses `save(update_fields=["active_run_id", "owned_run_ids"])` and adds both fields to `_UPDATED_AT_OMISSION_OK_FIELDS`. Field name verified in source (`_append_owned_run_id` sets `session.owned_run_ids`). Risk 6 covers the removed signal; Risk 3 records the knock-on for #2677. | Use Task 2's own primitive, not `preserve_updated_at`. At `tools/sdlc_session_ensure.py:493` the write is `session.active_run_id = candidate` plus `_append_owned_run_id(session, candidate)`, so `session.save(update_fields=["active_run_id", "owned_run_ids"])` persists exactly what changed and the existing carve-out at `models/agent_session.py:1008` suppresses the stamp. `preserve_updated_at=True` will NOT work here: the post-save readback immediately below asserts `active_run_id` round-tripped, so both fields must appear in `update_fields`. Confirm the real field name for the owned-set before writing the list. |
| CONCERN | Risk & Robustness | Task 4 converts a human-invoked CLI into an unattended hourly actuator calling `finalize_session()` on `running` eng sessions across the whole fleet. That is the out-of-process session-health actuation class the plan's own Prior Art says #2439 established as unsafe by design and removed. The plan dismisses that prior art as "adjacent drift" and never argues why this actuator is safe when the removed one was not. No dry-run soak, no `enabled: false` first landing, no metric, no kill switch short of editing YAML. | **RESOLVED — split out to #2677.** The reflection registration leaves this plan entirely; `config/reflections.yaml` is now an anti-criterion. #2677 carries the #2439 safety argument, the soak, the kill switch, the metric, and the `ORPHAN_AGE_SECONDS` re-derivation that this plan's Task 3 makes necessary. Risk 3 is rewritten to show the `is_ledger` skip holds today's net outcome constant rather than regressing it, so it ships safely alone. | `_kill_orphans(dry_run=False)` (`tools/sdlc_session_ensure.py:1021`) finalizes every row `_iter_orphan_sessions()` yields. The fail-safe is `_lock_owner_is_live()` returning True on cross-host/malformed evidence, so the dangerous path is the no-payload fallback at `:1013-1018`, which reaps purely on `_last_activity_at(s) >= ORPHAN_AGE_SECONDS` (600s) where `_last_activity_at` reads `updated_at` first (`:897`). `kill_orphans_reflection()` must honor an env kill switch, e.g. `if os.environ.get("SDLC_LEDGER_REAP_DISABLED"): return {"count": 0, "failures": 0, "killed": False}`. |
| CONCERN | History & Consistency | Success Criterion 1 ("performs zero Redis writes against a keyspace of healthy rows") is unachievable and contradicts a test the same plan lists for preservation. `cleanup_corrupted_agent_sessions` unconditionally calls `AgentSession.repair_indexes()` (`agent/session_health.py:5649`, the "Unconditional repair_indexes() (issue #1361)" block whose comment records the prior gate was removed permanently) and then `clean_indexes()` on both `AgentSession` and `Memory`. Both write. A builder taking the criterion literally either fails validation on a correct build or re-introduces the #1361 gate. | **RESOLVED.** Success Criterion 1 rewritten to assert on the field, names the `repair_indexes()`/`clean_indexes()` index writes as by-design and retained, and states explicitly that a command-count spy is guaranteed non-zero on a correct build. The Empty/Invalid-Input bullet is corrected to match. | Assert on the field, not on write count: read `updated_at` for each seeded healthy row before and after the sweep and assert equality. Any Redis command-count spy will be non-zero because of the index pass, so a literal "zero writes" test is guaranteed to fail on a correct implementation. Restate the criterion as "zero writes to any AgentSession hash on the healthy path, `updated_at` byte-identical, with the unconditional `repair_indexes()`/`clean_indexes()` maintenance (#1361) retained". |
| CONCERN | History & Consistency | Test Impact under-scopes `tests/unit/test_update_stale_session_fence.py`. Widening the return tuple breaks an entire unmentioned test class, `TestCallerUnpacksThreeValues`: `test_run_update_unpacks_the_new_arity` (`:344-346`) asserts the literal source string `stale_killed, skipped_recent, skipped_fence_live = _cleanup_stale_sessions(`; `test_function_signature_declares_a_three_tuple` (`:348-352`) asserts the return annotation is exactly `tuple[int, int, int]`; and `:327` asserts `counts == (1, 1, 1)`. The plan scopes "every unpack site needs widening" only to `test_stale_cleanup.py`. | **RESOLVED.** Test Impact now carries a seven-item sub-list for that file naming each assertion by line, its disposition, and why it fails at the source-text/annotation layer rather than behaviorally. Line numbers re-verified against the file: class `:332`, unpack test `:333-346`, annotation test `:348-352`, `"fence verified"` test `:354-361`, `counts ==` `:327`. | These are source-text and annotation assertions, so they fail on `inspect.getsource(run_module.run_update)` and `inspect.signature(...).return_annotation`, not at the behavioral layer, and no amount of reasoning about the ladder will surface them. Update all three plus the class name (which encodes the old arity) in the same commit as the arity change, and keep the "fence verified" summary-string assertion at `:355-358` intact when rewording the summary line for the two new counters. |
| CONCERN | Risk & Robustness | Architectural Impact's Reversibility ("high. Three small, independent diffs, each revertable alone") contradicts Risk 5 ("the skip cannot ship alone") and is stale on count (four code tasks, not three). Reverting Task 4 alone leaves the reaper permanently unable to reach ledger anchors with nothing else reaping them, the unbounded-accumulation state that produced the 59 rows. Reverting Task 3 alone leaves an hourly fleet-wide auto-finalizer with its coupled guard removed. An operator reading "each revertable alone" mid-incident picks exactly the wrong single revert. | **RESOLVED.** Reversibility replaced with a two-unit table (A: Task 2 alone; B: Tasks 1, 3, 4, where Task 4 must not be reverted while 1 or 3 is live), naming the harm of the wrong single revert. Task 4's body carries the constraint as its first bullet. The `sdlc-ledger-orphan-reap` verification row is gone; the registry is now an anti-criterion (#2677). | Name two revert units: `{Task 1, Task 2}` independently revertable, `{Task 3, Task 4}` revertable only as a pair. Encode the reverse constraint where the builder sees it: Task 4 already carries `Depends On: build-reaper`, so add to Task 3's body "do not land without build-ledger-reaper in the same PR". Make the Verification row for `sdlc-ledger-orphan-reap` assert the `enabled:` state alongside presence rather than a bare `grep -c` for the name. |
| CONCERN | Scope & Value | The reported user pain is a dashboard symptom (nine Jobs reading "just now" on 07-16 work) and not one of the 11 Success Criteria validates it. The plan defers the user-facing half to "the existing Job-ordering tests cover it", but those were written against `145967f4b`'s `started_at` tiebreak, which the plan itself classifies as a presentation-layer mitigation for a different failure. Nothing demonstrates end to end that a swept keyspace still orders and labels Jobs by real activity. | **RESOLVED.** New Success Criterion, a Test Impact entry in `tests/unit/test_ui_jobs_grouping.py` (`class TestJobRollup`, `:230`), and an Error-State-Rendering bullet — all requiring at least two non-terminal rows and stating why. Confirmed there is no existing coverage: `last_activity_at` is asserted only in `test_session_modal_liveness_render.py` and `test_sdlc_session_ensure.py`, neither of which touches Job ordering. | Put the assertion on the `ui/data/jobs.py` layer where the symptom lives: `best_timestamp` (`ui/data/sdlc.py:1241`, `p.completed_at or p.updated_at or p.started_at or p.created_at or 0`) feeds `Job.last_activity_at` (`ui/data/jobs.py:449`) and the sort at `:493-494`. Seed at least two NON-terminal rows so `completed_at` is None and `updated_at` is actually the operative term. A terminal-row fixture passes vacuously via `completed_at` and proves nothing. |
| CONCERN | Scope & Value | Tasks 1 and 2 close the issue as filed. Tasks 3 and 4 are a second, larger change (a liveness-ladder rewrite plus a new fleet-wide scheduled finalizer) that exists only because Task 3's own defensive `is_ledger` skip creates the gap Task 4 fills. Bundling a session-retention policy decision with a timestamp-authorship fix puts the plan's two self-described most dangerous risks on the same PR as a change that is provably safe, and maximizes the blast radius of a single revert. | **RESOLVED — partially accepted.** The scheduled fleet-wide finalizer (the actual retention-policy change) is out, to #2677. The `is_ledger` skip stays and is now Task 4, because Tasks 1 and 3 remove the only thing currently masking live ledger anchors from the 120-minute `created_at` floor: shipping them without the guard would finalize a live pipeline's state anchor. The skip is a guard, not a policy — it holds the current net outcome for ledger rows constant. | The split is safe in this order and only this order. Tasks 1+2 alone cannot over-reap because they only remove writes; they stop masking rows from the reaper, whose existing `created_at >= 120min` floor plus the retained `updated_at` rung already spare live worker-less pipelines (#1676). Shipping Tasks 3+4 first, or the ledger skip without the reflection, is the combination Risk 5 correctly forbids. |
| NIT | Scope & Value | Technical Approach rung 4 (`last_heartbeat_at` recency) never names a row it actually rescues. `last_heartbeat_at` is written only by the executor (`agent/session_executor.py:1070-1071` and `:2226-2227`), and a session that reached the executor also carries a spawn fence, which rung 3 resolves first. A fenceless row with a fresh heartbeat is not shown to exist. | **RESOLVED.** Technical Approach rung 4 now names the row: heartbeat writes use `save(update_fields=["last_heartbeat_at"])` and therefore do NOT move `updated_at`, so a turn running longer than the 30-minute window with a half-recorded fence (the `fence_pid is not None and fence_ct is not None` guard at `:268` fails) drops to the `created_at` gate and is finalized mid-execution today. Reachable independently of the restamp bug, which is why the rung earns its place. | n/a (NIT) |
| NIT | Structural | Three file:line misattributions. `bridge/update.py:_cli_flush_stuck` does not exist. `_cli_flush_stuck` is `agent/agent_session_queue.py:3135` and its ledger skip is at `:3144`; `bridge/update.py` contains no `is_ledger` reference at all. `_is_ledger` is DEFINED at `agent/session_health.py:51`, not `agent/agent_session_queue.py:1466` (that is a usage site; the import is at `:88`). `best_timestamp`'s `updated_at` term is at `ui/data/sdlc.py:1241`, not `:1240` (`:1239` is the `def`). | **RESOLVED.** All three corrected and re-verified by grep: `_cli_flush_stuck` at `agent/agent_session_queue.py:3135` with its ledger skip at `:3144`; `_is_ledger` defined at `agent/session_health.py:51`, imported at `agent/agent_session_queue.py:88`, used at `:1466`, `:2754`, `:3144`; `best_timestamp`'s `updated_at` term at `ui/data/sdlc.py:1241`. The `:1240` reference is gone from every section and the Freshness Check records the correction. | n/a (NIT) |
| NIT | Structural | Team Orchestration assigns the test engineer "Role: Task 4 — regression coverage across the existing suites named in Test Impact", but Task 4 is "Schedule the ledger orphan reaper" and regression coverage is Task 5 (`test-restamp`). Stale numbering from a prior revision. | **RESOLVED.** Every Team Orchestration role now cites its correct task number against the revised seven-task list, and the `reaper-builder` role covers Tasks 3 and 4. | n/a (NIT) |
| NIT | Structural | Risks are numbered out of order: Risk 1, Risk 2, Risk 5, Risk 3, Risk 4. Risk 5 was inserted mid-list. | **RESOLVED.** Risks renumbered 1-6 in order; the former Risk 5 is now Risk 3 (rewritten), and Risk 6 is new (the liveness signal Task 3 removes). | n/a (NIT) |

---

## Resolved Decisions

Five questions surfaced while shaping this plan and during critique round 1. Each was resolvable from evidence in the codebase, so each is decided here rather than deferred.

1. **Ledger skip vs. ledger reaping — split by authority; the skip ships, the reaper does not.** The `/update` reaper decides on *process liveness*; a ledger anchor has no process by construction, so it is outside that reaper's competence and gets skipped. The authority for a process-less row is issue-lock ownership, which `tools/sdlc_session_ensure.py::_iter_orphan_sessions` already implements correctly (`_lock_owner_is_live`, failing toward live on ambiguous evidence, with a 600s idle fallback when no payload resolves). Its selection criteria match a ledger anchor exactly, verified against the live row `sdlc-local-2643`.

   The gap: nothing schedules it. Grep finds `--kill-orphans` only in its own argparse wiring and tests — absent from `config/reflections.yaml`, `scripts/update/run.py`, and launchd. That is how 59 anchors accumulated.

   **Revised in round 1:** scheduling it is out of scope and tracked as **#2677**. It converts a human-invoked CLI into an unattended fleet-wide `finalize_session()` actuator, the class #2439 removed as unsafe, and this plan's Task 3 narrows the `updated_at` signal its no-payload fallback depends on — so `ORPHAN_AGE_SECONDS` has to be re-derived before anything schedules it. The `is_ledger` skip still ships here, because it is a guard rather than a policy: today the reaper cannot reach a fence-less ledger row anyway (the probe restamps it first), so the skip holds the net outcome constant while Tasks 1 and 3 remove the forgery. Recorded as Risk 3.

2. **`preserve_updated_at` + `update_fields` — documented precedence, logged, not an exception.** `AgentSession.save()` is called from fail-quiet paths throughout the codebase (`agent/health_check.py`, `bridge/promise_gate.py`, `agent/output_handler.py`, every archive and health sweep), all of which swallow exceptions to avoid crashing the agent. A hard error would therefore surface as a silently swallowed save, which is worse than the wrong timestamp it was meant to prevent. Both flags mean "do not stamp," so they do not actually conflict: `preserve_updated_at=True` wins, a WARNING names the caller, and the combination is documented as a caller smell rather than enforced.

3. **`last_heartbeat_at` rung scoped to the `/update` reaper only.** The other two readers are already correct and should not be converted. `agent/session_health.py` uses `last_heartbeat_at` as a first-class Tier 1 signal already (`:1664-1680`, `:1751-1775`, `_session_is_alive` at `:5846-5860`). `tools/sdlc_session_ensure.py` deliberately prefers the issue-lock payload over any timestamp, per #2305 defect 1, and demoted `updated_at` to an idle-window fallback for exactly this bug's reason. `scripts/update/run.py` is the one reader still treating `updated_at` as a liveness proxy without a stronger signal ahead of it, and it is the reader the issue names. No third conversion.

4. **The run-lock bind uses `update_fields`, not `preserve_updated_at`.** (Round 1.) Two primitives could suppress the stamp at `tools/sdlc_session_ensure.py:493`. The `update_fields` carve-out wins because the write genuinely is partial — exactly `active_run_id` and `owned_run_ids` changed — and the carve-out is the repo's sanctioned expression of that, established by PR #1787's allowlist. `preserve_updated_at` exists for the one case `update_fields` cannot serve: an INSERT that must write every field while owning the timestamp it carries (the archive restore). Using it for a two-field update would persist a whole row for no reason and churn every index. The two primitives are not interchangeable and the plan should not blur them.

5. **The fence-dead rung stays non-terminal.** (Round 1.) Making it terminal is defensible on its own merits — a dead fence is the strongest evidence the reaper has — but it converts `/update` from "finalize old dead things" to "finalize dead things immediately," bypassing the 120-minute `created_at` floor, and it inverts two green tests that state the opposite invariant as a deliberate design choice ("the fence ADDS protection; it never subtracts it"). That is a retention-policy decision with its own blast radius. This plan is a write-authorization fix. Recorded as a No-Go so a builder does not quietly make the change while implementing the two new rungs.
