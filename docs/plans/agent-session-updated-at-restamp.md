---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-08
tracking: https://github.com/tomcounsell/ai/issues/2660
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-08-10T05:20:00Z
---

# AgentSession.save() restamps updated_at on maintenance writes, faking liveness

## Problem

On 2026-08-07 the dashboard reported `last activity` of "just now" for nine Jobs that had settled on 07-16. Their `last_activity_at` all read `08-07 11:34` — one batch, written by a service restart. Earlier that day the same flattening landed on every `running` row on a repeating tick: `11:03:16 → 11:08:14 → 11:13:16`, 59 rows inside a ~16-second window each time.

Meanwhile `/update` kept reporting `Skipped 59 live session(s) (recent heartbeat)`. The stale-session reaper could see 59 rows that had not executed anything in weeks and skipped every one as freshly active.

**Current behavior:**

`AgentSession.save()` stamps `self.updated_at = utc_now()` on every unguarded call (`models/agent_session.py:1023`). There is a carve-out — `save(update_fields=[...])` skips the stamp when `update_fields` omits `updated_at` (`models/agent_session.py:1008`) — and the execution-path writers in `agent/session_health.py` and `agent/session_executor.py` use it correctly.

Three maintenance writers do not, and all three write whole rows:

1. **The corruption probe.** `cleanup_corrupted_agent_sessions()` calls `session.save()` on **every hydrated row**, terminal ones included, purely to see whether the save raises (`agent/session_health.py:5569-5571`, comment: "Try a no-op save to detect other validation failures"). It is not a no-op. It moves the entire table's `updated_at` in one pass. Four callers fire it: worker startup (`worker/__main__.py:763`), the hourly `agent-session-cleanup` reflection (`config/reflections.yaml:37-42`), `/update` Step 5.5 (`scripts/update/run.py:1969`), and the corrupted-pop handler in the queue loop (`agent/agent_session_queue.py:2308`).

2. **The archive restore.** `_rehydrate_row()` rebuilds each archived row as `AgentSession(id=archived_id, **fields).save()` (`agent/session_archive.py:433-443`), called from `restore_if_empty()` at worker startup (`worker/__main__.py:720`). The archived `updated_at` travels in the payload and is then overwritten with restore time.

3. **The sibling `session-ensure` run-lock bind.** `_acquire_run_lock_and_bind()` writes `session.active_run_id` and `owned_run_ids`, then calls a bare `session.save()` (`tools/sdlc_session_ensure.py:486-493`). Its docstring records that it "is called immediately before EVERY return point in `ensure_session()`," so every SDLC stage dispatch — including a dispatch that merely re-reads state and returns — restamps the row. This is the writer that keeps the 59 ledger anchors of the Problem statement permanently fresh, and it is the second half of a sentence this plan's own Prior Art quotes: #2305 defect 1 named "probes **and sibling `session-ensure` renewals**." The write itself is legitimate (two fields genuinely changed); the whole-row stamp attached to it is not.

The `/update` path makes the consequence self-defeating by construction. Step 5.5 runs the probe at `scripts/update/run.py:1969` and the reaper at `:1984`. The probe restamps every row seconds before `_cleanup_stale_sessions` reads `updated_at` recency against a 30-minute `RECENT_ACTIVITY_WINDOW` (`scripts/update/run.py:274-278`). Every fence-less row is guaranteed to land in `skipped_recent`. The reaper cannot reach a fence-less row on the `/update` path, ever.

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

**Active plans in `docs/plans/` overlapping this area:** `durability-room-job-agentrun.md` (#2494, status Ready) and `durability-m1-fence-canary.md` both work the `(exec_pid, pid_create_time)` fence that this plan's Task 4 reads. Neither mentions `updated_at`; no scope collision. This plan consumes the fence as an existing primitive and adds no field to it.

**Live-state check:** the current keyspace holds 2 `AgentSession` rows, both `running` `is_ledger` anchors, `updated_at` 2026-08-07 17:06/17:07 — consistent with the #2042 purge described in the issue's Context.

**Notes:** The bug is still present and reproducible by reading the code path; the 59-row population that surfaced it was purged separately.

## Prior Art

- **PR #1655** (#1645): "AgentSession.updated_at stamped in UTC (remove auto_now, explicit utc_now() in save())" — removed popoto's `auto_now` and moved the stamp into the `save()` override. **This is what created the current shape.** It fixed a timezone bug correctly; it did not ask which callers should be stamping. Relevant: it is the reason index rebuilds cannot restamp (`auto_now` is off), which removes a whole class of suspect.
- **PR #1787**: "downgrade liveness save() log noise to DEBUG" — added `_UPDATED_AT_OMISSION_OK_FIELDS` so the known-good partial-save callers stop logging a WARNING. Establishes the convention this plan extends: the omission is by design for liveness fields, loud for everything else.
- **#1817** (in `agent/session_health.py:_heal_future_updated_at`): a maintenance function that used to persist a clamped `updated_at` was made **read-only detection** because "persisting rewrites the `created_at`-based sorted index on every call, so healing one future-dated straggler reshuffled the index position of every OTHER recently-created record." Same file, same class of defect, same resolution shape. Task 1 is the second application of this lesson.
- **#1676 / PR #1677**: "Fix sdlc-local-\* durability: reap on idle, not creation age" — established the general principle Task 4 obeys: a worker-less session writes no `last_heartbeat_at`, so `last_heartbeat_at` cannot simply *replace* `updated_at` in a liveness ladder. It also scopes Task 3: the run-lock bind is a *different* writer from the stage-state advance that carries the signal for these rows. Note the limit of its reach here — its own `sdlc-local-*` rows now carry `is_ledger=True` (`tools/sdlc_session_ensure.py:810`, #2042), so under the new ladder they are spared at rung 2 and never reach rung 5. The rows that make rung 5 load-bearing are the non-ledger ones enumerated in Technical Approach step 4.
- **#2305 defect 1** (`tools/sdlc_session_ensure.py:882-896`): `updated_at` was demoted from authoritative liveness to idle-window fallback precisely because "it is refreshed by probes and sibling `session-ensure` renewals, which is exactly the mirage that let a hollow tracking session read as live forever." **Both named writers are this bug**, identified a release ago and worked around in one reader instead of fixed at the source: "probes" is `cleanup_corrupted_agent_sessions` (Task 1) and "sibling `session-ensure` renewals" is the bare `session.save()` at `tools/sdlc_session_ensure.py:493` (Task 3). Read the whole sentence, not the first noun.
- **#2098 / #2091 / #2439**: established that out-of-process actuation on session health is unsafe by design, and removed the `session-liveness-check` reflection. Relevant only as the adjacent-drift finding below.
- **#2494 / #2516 / #2538** (Durability M1): built the `(exec_pid, pid_create_time)` fence and closed nine unfenced consumers. The fence is the prior art the issue points at for "a liveness field only execution writes." Task 4 reads it as-is.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|--------------------------------|
| PR #1655 (#1645) | Moved the `updated_at` stamp from popoto `auto_now` into the `save()` override so it lands in UTC | Correct on timezone, silent on authorship. Moving the stamp into `save()` made *every* caller a liveness author, including maintenance sweeps that have nothing to report. |
| PR #2415 (#2305 defect 1) | Demoted `updated_at` to an idle-window fallback in `_iter_orphan_sessions` and documented that it "is refreshed by probes and sibling `session-ensure` renewals" | Fixed one reader, and named both writers in the same breath without touching either. Both kept writing, and every other reader (`/update`'s reaper, `best_timestamp`) kept trusting the forged value. |
| `145967f4b` | Added a `started_at` sort tiebreak so a batch write cannot flatten dashboard ordering | Treats the symptom at the presentation layer, as its own commit message says. The underlying value stays wrong for every non-dashboard reader. |

**Root cause pattern:** each fix moved a *reader* away from `updated_at` instead of stopping the *writer*. Three readers were patched over eleven months while three maintenance writers kept forging the value. The field is fine; the write authorization is missing.

## Research

The work is internal: the ORM is `popoto`, vendored in `.venv`, and the authoritative answer to "what does `save()` validate" is in its source, not in ecosystem documentation. Read directly instead of searched.

**Read popoto second. Read this repo's override first.** Three critique rounds each found a blocker rooted in reasoning about validation from vendored popoto source while `AgentSession` overrides the relevant behavior. Every safety argument in this plan is now derived from the override, then checked against popoto, and each derivation below was executed against the live model rather than read.

**Finding 0 — `AgentSession.__setattr__` normalizes every `_DATETIME_FIELDS` value before validation ever runs (`models/agent_session.py:735-778`).** The set (`:696-709`) contains `updated_at`, `started_at`, `completed_at`, `last_heartbeat_at`, and five more. On assignment — including during `__init__`, which routes through `super().__init__(**kwargs)` at `:733` — an `int`/`float` becomes a UTC datetime, a `str` is parsed by `fromisoformat` or coerced to `None` on `ValueError`, and any other non-`datetime` non-`None` value becomes `None`. Its own docstring states the purpose: guarding "against Popoto's `is_valid()` coercion failure when a `DatetimeField` holds a non-datetime value." Executed:

```
>>> s = AgentSession(id=uuid4().hex, updated_at="TOTALLY-NOT-A-DATE")
>>> s.updated_at, s.is_valid()
(None, True)
```

No `ModelException`. **Consequence: no field in `_DATETIME_FIELDS` can fail `is_valid()` on this model.** This is the single fact Task 1's and Task 2's safety arguments rest on, and it is the fact two prior revisions of this plan asserted the opposite of.

**Finding 0b — the model's constructible failure surface is two fields wide.** Enumerating `AgentSession._meta.fields` at runtime: exactly two `null=False` fields — `id` (`AutoKeyField`) and `created_at` (`SortedField`) — and **zero** `max_length` fields. `created_at` is datetime-typed but deliberately outside `_DATETIME_FIELDS` (it is a `SortedField`, `:696-709` lists only `DatetimeField` names), so it is the one datetime-typed field `__setattr__` does not heal. `id` cannot be made bad post-construction either: `AutoKeyField` rejects a non-32-char value at `__init__` (`AgentSession(id="dbg-verify-1")` raises `ModelException: Could not instantiate class`). So `created_at` is the model's only constructible `is_valid()`-False fixture. Executed: `s.created_at = None` → `is_valid()` returns `False` (`popoto/models/base.py:851-855`, "field ... is null"); `s.created_at = "TOTALLY-NOT-A-DATE"` → `False` via the coercion `TypeError` at `:829-846`.

**Key findings (from `popoto/models/base.py`):**

- `Model.save()` → `pre_save()` → `if not self.is_valid(): raise ModelException("Model instance parameters invalid. ...")` (`base.py:908-913`, `ignore_errors` suppresses the raise). **`is_valid()` is the predicate behind the one raise the current save-probe's string filter actually matches.** It runs type coercion, null checks, `max_length` enforcement, per-field `is_valid()`, and the ttl/expire_at mutual-exclusion check, logs failures rather than raising, and writes nothing to Redis.
- `is_valid()` is read-only **with respect to Redis**, not with respect to the instance: its coercion branch calls `setattr(self, field_name, coerced)` (`base.py:829-839`). Harmless in the sweep — the hydrated instances are discarded — but that is the property the code comment and the feature doc must name, because "read-only" alone is wrong.
- `pre_save` raises `ModelException` on two further conditions the string filter does **not** match — unique-index violations (`base.py:943-951`) and unique-field violations (`:968-978`, and `id` is unique on `AgentSession`). Their messages carry `"violation"`/`"violated"`, never `"invalid"` or `"validation"`. Both the old probe and the new `is_valid()` call are silent on them, which is what makes Task 1 classification-neutral. See Risk 1 for the full table.
- `Model.save(update_fields=[...])` serializes, validates, and indexes only the listed fields; `None` means full save; empty list is a no-op (`base.py:1019-1022`). Confirms the existing carve-out is the sanctioned partial-write path.
- `DatetimeField` supports `auto_now` / `auto_now_add`, both defaulting to `False` (`popoto/fields/datetime_field.py:85-86`). `AgentSession.updated_at = DatetimeField(null=True)` carries neither since #1655, so `rebuild_indexes()` — which runs `field.on_save` per field, never `Model.save()` — cannot restamp. Rules out index repair as a suspect.

No external findings needed beyond the vendored source and the live model.

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
- **Interface changes:** `AgentSession.save()` gains one keyword-only-in-practice parameter, `preserve_updated_at: bool = False`. Default preserves every existing call site's behavior exactly. No signature change is visible to any caller that does not opt in. `AgentSession` also gains `refresh_ttl()` (step 1b), a new method with one call site and no effect on any existing one.
- **Coupling:** decreases. `cleanup_corrupted_agent_sessions` stops writing `AgentSession` field values for healthy rows and becomes a read-mostly auditor. Its only healthy-path command against a session hash is the step 1b `refresh_ttl()` `EXPIRE`, which is key metadata and moves no field and no index; it still writes on the delete path it already owns, plus the index maintenance (`repair_indexes()`/`clean_indexes()`, #1361) it has always run and keeps running. The corruption probe stops coupling "is this row valid" to "this row is now fresh," and `_acquire_run_lock_and_bind` stops coupling "this run owns the lock" to "this row just moved."
- **Data ownership:** clarified, which is the point. After this change, `updated_at` is owned by writers that have something to report, and `last_heartbeat_at` remains exclusively the execution heartbeat's. No new field, so **no Popoto schema change and no migration** — the Popoto Schema Migration Requirement in `docs/sdlc/do-plan.md` does not apply.
- **Reversibility:** partial, and the shape matters more than the grade. There are four code diffs, and they are **not** all independently revertable. Two revert units:

  | Unit | Diffs | Revertable alone? |
  |------|-------|-------------------|
  | A | Task 2 (`preserve_updated_at` + archive restore) | Yes. It touches only the cold-boot restore path and moves no live row's timestamp. |
  | B | Task 1 (`is_valid()` probe plus the `refresh_ttl()` keepalive), Task 3 (`session-ensure` partial save), Task 4 (**both** new reaper rungs) | Tasks 1 and 3 revert alone. **Neither of Task 4's two rungs may be reverted while Task 1 or Task 3 is live** — and they are separate rungs an operator can revert separately. |

  Both of Task 4's rungs are guards for exposure Tasks 1 and 3 create, and each fails differently:

  - **Reverting the `is_ledger` skip (rung 2)** finalizes a live SDLC pipeline's state anchor. Once nothing restamps a ledger anchor, the `/update` process-liveness reaper reaches it through the 120-minute `created_at` floor.
  - **Reverting the `last_heartbeat_at` rung (rung 4)** finalizes an unfenced session mid-turn. With the probe's incidental refresh gone, that row's only remaining `updated_at` writer is the 1500s calendar tick against an 1800s window (Risk 2, row 5).

  These are two single-rung reverts an operator is plausibly reaching for mid-incident, both harmless on today's main and both harmful once Task 1 lands. Task 4's task body states this where the builder sees it, and all four tasks land in the same PR.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1 (confirming the reaper's new ladder does not over-reap — the one place this can cause harm)
- Review rounds: 4 (round 1: 2 blockers — the ladder reading and the third writer both changed the shape of the work. Rounds 2 and 3: blockers rooted in the same mistake, reasoning about validation from vendored popoto source while `AgentSession.__setattr__` overrides it. Round 3's revision re-derives both safety arguments from the override and records the derivations as executed results in Research findings 0 and 0b, so the class of error has a fixed reference point rather than a per-sentence patch.)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable for ORM-backed tests | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | The health/archive tests exercise real Redis, per the repo's no-mocks testing philosophy |
| On-pin interpreter | `python -c "import pathlib,sys; assert pathlib.Path('.python-version').read_text().strip().startswith('.'.join(map(str, sys.version_info[:2])))"` | `scripts/pytest-clean.sh` aborts on an off-pin venv |

## Solution

### Key Elements

- **A corruption probe that writes no field value** — `cleanup_corrupted_agent_sessions` decides "is this row corrupt" without persisting anything about it, then issues one `refresh_ttl()` `EXPIRE` per healthy row to hold the expiry clock exactly where the old probe held it (step 1b, #2698). The probe's field write was incidental to its purpose; removing it removes the batch restamp at its largest source and collapses an O(N) Redis write amplification that ran on every worker start, every `/update`, and every hour. Per healthy row, a whole-row `HSET` plus an `on_save` pass over 89 fields (three `EVAL INDEX_SWAP_LUA` and a `ZADD` among them) becomes a single `EXPIRE`.
- **Explicit timestamp preservation for restore** — the archive restore declares that it owns the `updated_at` it carries, so rehydration reproduces the archived row rather than a row that looks like it was written at restore time.
- **A narrowed run-lock bind write** — `_acquire_run_lock_and_bind` writes exactly the two fields it changed (`active_run_id`, `owned_run_ids`) through the existing `update_fields` carve-out, so a stage dispatch that only re-binds the run lock stops claiming the whole row moved. This is the writer that keeps ledger anchors permanently fresh, so it is the one that has to stop for the motivating population to be fixed at all.
- **An execution-only rung in the reaper's liveness ladder** — the `/update` reaper consults `last_heartbeat_at` (written only by the executor's heartbeat, never by any maintenance path) before falling back to `updated_at`.
- **A ledger guard on the reaper** — every worker loop skips `is_ledger` anchors; the `/update` reaper does not. The restamp has been masking that gap, and removing the restamp exposes it: a live anchor would reach the 120-minute `created_at` floor and be finalized mid-pipeline. The process-liveness reaper is structurally the wrong owner for a row that never had a process, so it skips ledgers. The skip restores today's net outcome for ledger rows (unreachable) by an honest mechanism instead of a forged timestamp — it adds no new accumulation, because the accumulation already exists and has its own owner (see No-Gos, #2677).

### Flow

`/update` runs → corruption cleanup audits every row and writes no field value (one `EXPIRE` per healthy row holds its TTL) → stale reaper skips ledger anchors outright → asks the fence → asks `last_heartbeat_at` → asks `updated_at` → asks `created_at` age → finalizes only rows that survive every rung → summary names which signal drove each skip.

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

The string match on `"invalid"`/`"validation"` matched exactly one of `pre_save`'s three `ModelException` raises: the `is_valid()` one (`base.py:908-913`). The other two — unique-index (`:943-951`) and unique-field (`:968-978`) violations — carry `"violation"`/`"violated"` and were never classified corrupt. Call `is_valid()` directly and the classification is unchanged on all three. `is_valid()` returns a bool and logs its own failure detail; it raises only for the ttl/expire_at mutual-exclusion case, which is treated as corrupt. See Risk 1 for the per-raise table.

**What Check 2 can actually catch, stated small.** Research finding 0 removes every `_DATETIME_FIELDS` shape from the answer, and finding 0b removes `max_length` and `id`. What remains is `created_at`: `null=False`, `SortedField`, outside the normalizer. It is the model's only constructible `is_valid()`-False fixture and therefore the only fixture the parity test can be written against. The plan does **not** claim a Redis hydration path produces that shape — no such path was found, and the parity test does not depend on one; it drives the sweep with an injected instance (patch `AgentSession.query.all` the way `tests/unit/test_update_stale_session_fence.py:99-107` patches `query.filter`). What the test proves is exactly the risk being managed: swapping the predicate does not change which rows get deleted. It does not prove that corrupt rows of this shape occur in production, and should not be written as if it does.

Keep the WARNING log line and its fields so operator-facing output is unchanged in shape.

Note for the inline comment and the feature doc: `is_valid()` is read-only **with respect to Redis**. It calls `setattr` during coercion (`popoto/models/base.py:829-839`), so the in-memory instance can move. Say "writes nothing to Redis," not "read-only."

**1b. Keep the sweep TTL-neutral with an explicitly-named TTL keepalive (#2698).**

The probe was doing a second job nobody declared. `AgentSession` carries `Meta.ttl = 2592000` (30 days, `models/agent_session.py:628-631`), and a full `save()` resets that TTL to the ceiling. Because the sweep full-saved every hydrated row every 5 minutes to an hour, **every row's expiry clock has been pushed back forever and `Meta.ttl` has never once fired.** Measured against the real model on an isolated Redis:

```
ttl right after initial save : 2592000
ttl after 3s idle            : 2591997
ttl after a no-op save probe : 2592000   <- reset to the ceiling
ttl around is_valid()        : unchanged
```

So step 1 alone does not merely stop a bogus write. It activates a 30-day expiry on session rows for the first time in the system's life, as a side effect of a liveness fix. That is a retention-policy change, and this plan says three times over (No-Gos, Risk 2, and the fence-dead rung) that it is a write-authorization fix and retention policy is out of scope. Honoring that boundary means the sweep must leave the TTL exactly where it is today.

**Do not do this by dropping `Meta.ttl`.** It does not work, and the way it fails is invisible. Redis keeps the TTL already stamped on a key; clearing the model attribute only stops future stamps. Measured:

```
ttl=600 -> sleep 5s -> ttl=595
flip Meta.ttl to None, reload (instance _ttl is None), re-save
ttl after re-save: 595   <- RETAINED and still decaying, not cleared
```

Every `AgentSession` hash in Redis right now carries a live 30-day clock from its last write, so that route would leave the existing population expiring while new rows never do, and popoto exposes no ORM-level `persist`/`clear_ttl`/`set_ttl` to fix it up. It would read as neutral and measure as neutral on a fresh key while quietly expiring production.

**Do it with a named model method that refreshes the TTL and writes no field.** Add `AgentSession.refresh_ttl()`:

```python
def refresh_ttl(self) -> bool:
    """Hold this row's ``Meta.ttl`` at the ceiling without writing any field.

    #2698 placeholder. The corruption sweep used to do this incidentally,
    as a side effect of its save() probe, so ``Meta.ttl`` has never fired.
    Deleting this call activates a 30-day expiry on every session row.
    """
    return bool(POPOTO_REDIS_DB.expire(self.db_key.redis_key, self._ttl))
```

Measured against the real model on an isolated Redis:

```
EXPIRE on a hydrated row        :  ttl 2591997 -> 2592000 (refreshed), updated_at UNMOVED
EXPIRE on a non-existent key    :  returns False, key NOT created
instance._ttl (query-loaded)    :  2592000  <- already populated, no reload needed
is_valid()                      :  ttl unchanged,                     updated_at UNMOVED
```

So the healthy path becomes: `is_valid()` for classification, then one `session.refresh_ttl()` whose sole purpose is holding the TTL where it is today. It is the whole reason any Redis command survives on the healthy path at all, so a reviewer who meets it must be able to read why it is there. The method name is the first line of that defense (Risk 4b).

**Use `self.db_key.redis_key`, never `self._redis_key`. This is the one detail a builder will get wrong.** `Model.__init__` leaves `_redis_key = None` unless **every** KeyField is non-`None` (`popoto/models/base.py:605-612`), and `AgentSession` declares four nullable KeyFields (`session_type` `:156`, `chat_id` `:189`, `slug` `:390`, `parent_agent_session_id` `:393`), so a query-hydrated row routinely carries `_redis_key = None`. Measured: `None` on instances from both `query.all()` and `query.filter()`, populated only after a `save()`. `POPOTO_REDIS_DB.expire(None, ...)` then raises `redis.exceptions.DataError: Invalid input of type: 'NoneType'`, which is exactly what happened when this was first written the obvious way. `self.db_key.redis_key` computes the key from current field values, is always populated, and is byte-for-byte the key popoto's own expire targets (`base.py:1117` builds `new_db_key = DB_key(self.db_key)`; it is passed to `expire` at `:1188`, `:1250`, and `:1309`).

**Why a `refresh_ttl()` method rather than a value-preserving `save(update_fields=[<field>])`.** The partial save also holds the TTL, and it was this plan's first answer. Five reasons the named method wins:

- **No field to choose, therefore no index churn.** The partial save's worked example was `status`, which is the worst possible pick: `status = IndexedField(...)` (`models/agent_session.py:158`), so each keepalive becomes an `EVAL INDEX_SWAP_LUA` per row per tick (`popoto/fields/indexed_field_mixin.py:270`, `:287`). `EXPIRE` touches no index at all.
- **No `_UPDATED_AT_OMISSION_OK_FIELDS` edit.** The partial save needs the omitted-stamp allowlist entry to avoid one WARNING per row per tick, and adding `status` to it reddens five live assertions in `tests/unit/test_agent_session_updated_at_utc.py` (`:146-156`, and the parametrized `["status"]` cases at `:251`, `:256`, `:257`) while falsifying the model's own comment at `models/agent_session.py:971-972` ("any other omission (e.g. ``status``) still warns"). `refresh_ttl()` calls no `save()`, so the allowlist is untouched and Task 3's own additions (`active_run_id`, `owned_run_ids`) stay the only edit to that frozenset.
- **It writes no field value at all**, so there is no lost-update hazard on any field. That is what keeps Race 1's "eliminated, not merely narrowed" claim literally true.
- **Its name says TTL**, which is a better guard against a cleanup pass deleting it than any comment (Risk 4b).
- **It is the command popoto itself issues** for this exact purpose, against the exact same key.

**And the decisive reason: the partial save was safe here only by accident.** Popoto's partial-save path detects key drift (`base.py:1126-1128`, `obsolete_key = self._redis_key` when it differs from the newly computed key) and then, at `:1214-1236`, deletes the obsolete hash after `HSET`ting **only the listed fields**, where the full-save path encodes every field first (`:1290`). On a key-drifted row that is a lossy migration: the real hash is deleted and a one-field hash replaces it. The branch is gated on `if obsolete_key and ...`, and because `_redis_key` is `None` for the query-hydrated instances this sweep handles, it cannot fire here. So it was a latent hazard, not a live one. But "safe because an undocumented popoto internal happens to leave an attribute unset on hydration" is not a designed safety property, and this repo already has a popoto bump planned (#2636). `refresh_ttl()` does not depend on that accident in any way.

Key drift under `refresh_ttl()` degrades correctly: `db_key.redis_key` points at a key that does not exist, `EXPIRE` returns `False`, and nothing happens. A drifted row is a corruption case the sweep's delete path already owns (`_delete_with_stale_key_lookup`), and leaving its TTL alone is strictly safer than migrating its hash as a side effect of a liveness probe.

**This deviates from the repo's ORM-only convention, deliberately and narrowly.** CLAUDE.md forbids raw Redis on Popoto-managed keys, and the reason is index integrity and binary-safe field decoding. `EXPIRE` is key metadata: it reads no field, writes no field, and touches no index, so neither rationale reaches it. `.claude/hooks/validators/validate_no_raw_redis_delete.py` will not block it either: it inspects `Bash` tool input only (`:150-153`) and its `_BLOCK_PATTERNS` list (`:29-45`) covers `delete`/`srem`/`sadd`/`zrem` plus the binary-unsafe reads; `expire` is absent. Encapsulating the call in a named model method instead of inlining it at the sweep's call site is the whole point of the deviation: the ORM stays the seam, and `models/agent_session.py` is already where this model's key-level Redis lives (`:2404-2433`, `:2522`).

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

`agent/session_archive.py:_rehydrate_row` passes `preserve_updated_at` **conditionally, guarded on the value's type — never on key presence**:

```python
fields = _deserialize_payload(row["payload"])
archived_id = fields.pop("id")
ts = fields.get("updated_at")
# Preserve only a real datetime. None and an unparsed ISO string must fall
# through to the normal stamp — AgentSession.__setattr__ turns both into
# None, and preserving None restores a row with no liveness stamp at all.
AgentSession(id=archived_id, **fields).save(preserve_updated_at=isinstance(ts, datetime))
```

The key is always present, so a presence check would be dead code: `_serialize_session` (`agent/session_archive.py:181-201`) enumerates `session._meta.fields` and writes **every** field name into the payload, so the deserialized `fields` always carries an `updated_at` key. The two non-`datetime` shapes that actually occur are:

- **`None`** — a row archived before it was ever stamped.
- **A raw `str`** — `_deserialize_payload` (`:214-225`) converts ISO-8601 strings back to `datetime` for every `DatetimeField`/`SortedField` column, but on `ValueError` it logs a warning and **leaves the string in place** deliberately.

**What goes wrong under an unconditional `preserve_updated_at=True`, derived from `__setattr__` rather than from popoto.** Research finding 0: the normalizer intercepts the `updated_at` assignment inside `AgentSession(id=archived_id, **fields)` and turns both shapes into `None` before `save()` is ever called. So the failure is **not** a raise, not `_record_row_failure`, and not quarantine — it is silent: the row restores with `updated_at=None`. That row then has no `best_timestamp` term of its own (`ui/data/sdlc.py:1241` falls past it) and no reaper recency signal (rung 5 cannot read it), which is the same class of wrong value this plan exists to stop writing. Today's unconditional stamp repairs both shapes by accident. `isinstance(ts, datetime)` is the predicate that keeps that repair — `None` and `str` are both not `datetime`, so both fall through to the stamp — while preserving a genuine archived timestamp.

**The guard is narrow, so its test has to fail in two directions.** A single "the row restored and carries a timestamp" assertion is green with the guard present, with `preserve_updated_at=True` hard-coded, and with the whole call deleted. The pair that is not vacuous:

- a real-`datetime` payload round-trips **byte-identically** — red if the guard is hard-coded `False` or dropped;
- a `None`-or-unparsed-string payload restores with `updated_at is not None` **and** a value within a few seconds of restore time — red if the guard is hard-coded `True`.

*(An adjacent shape is out of scope and must not be conflated with it: an unparsed `created_at` string does reach `is_valid()` and returns `False`, because `created_at` is a `SortedField` outside `_DATETIME_FIELDS` — verified. That path raises and quarantines today, unconditionally, and this plan neither causes it nor fixes it. Do not write a Task 2 test against it.)*

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

   **The row this rescues, concretely** (the round-1 critique correctly asked for one): a session executing for longer than `RECENT_ACTIVITY_WINDOW` whose fence is absent or half-recorded. Because the tier-1 heartbeat writes go through `update_fields=["last_heartbeat_at"]`, they *do not* move `updated_at` — that is the carve-out working as designed. So a long-running turn with no full save in 30 minutes has a stale `updated_at` and a 60-second-old heartbeat. Today, if its fence is missing (legacy row, or `pid_create_time` never recorded — the `fence_pid is not None and fence_ct is not None` guard at `:268` fails), it drops straight to the `created_at` gate and is finalized while actively executing.

   **And Task 1 is what makes this rung mandatory rather than opportunistic.** That same row's only other `updated_at` writer is the 25-minute calendar tick (`agent/session_executor.py:612` `CALENDAR_HEARTBEAT_INTERVAL = 1500`, written at `:2246-2247`) against an 1800-second window — a 300-second margin that today is padded by the probe restamping the row on every worker start, every `/update`, and every hourly `agent-session-cleanup`. Remove the probe write and the padding goes with it. Rung 4 replaces the 300-second margin with a 1740-second one. See Risk 2, row 5, and the Reversibility note: this rung and the ledger skip are both unrevertable while Task 1 is live.
5. `updated_at` within `RECENT_ACTIVITY_WINDOW` → skip (retained, and now honest).

   **The row this rung protects, and why it is not the #1676 row.** #1676's `sdlc-local-*` pipelines are the obvious candidate, and they are the wrong one: every one of them is created by the sole production `AgentSession.create_local()` call at `tools/sdlc_session_ensure.py:837`, and `:810` sets `kwargs["is_ledger"] = True` unconditionally. New rung 2 spares all of them first, so an #1676 fixture exercises rung 2 and says nothing about rung 5.

   The real consumer is the **non-ledger local Claude Code CLI session**: `.claude/hooks/user_prompt_submit.py:338` calls `create_local(..., status="running")` and sets no `is_ledger` flag, so the row is non-ledger, worker-less (no `last_heartbeat_at`), and unfenced, while `_cleanup_stale_sessions` iterates exactly `status="running"`. It is kept fresh by the PostToolUse `watchdog_hook` write (`agent/health_check.py:611-625`, `s.updated_at = ...; s.save()` on every tool call). Rung 5 is the only rung standing between a human's live local session and the 120-minute `created_at` floor. Dropping it would reap live local sessions.

   The secondary consumer is the enqueue window described under Race 2: between a session's enqueue-time write and its T+0 `last_heartbeat_at` write (`agent/session_executor.py:1070-1071`), `updated_at` is the only fresh signal the row has.

   Both consumers are non-ledger by construction, which is what makes rung 5 reachable and its test non-vacuous.
6. `created_at` age ≥ threshold → finalize (unchanged)

Extend the return tuple from `tuple[int, int, int]` to `tuple[int, int, int, int, int]` — `(killed, skipped_recent, skipped_fence_live, skipped_ledger, skipped_heartbeat)` — and widen the caller's unpack and summary line (`scripts/update/run.py:1984`) so each skip names the signal that produced it. See Test Impact: the arity is asserted in **source text and in the return annotation**, not only behaviorally.

**5. Leave `best_timestamp()` alone (`ui/data/sdlc.py:1241`).**

Once all three forging writers stop, `updated_at` is the correct "most recent write" signal for dashboard ordering, and it is the *only* progress signal ledger sessions have (`ui/data/sdlc.py:343-348`). Changing the reader would break ledger ordering to fix a writer bug. A regression test pins the intent instead — and because the reported symptom lives at this layer, that test is a first-class Success Criterion rather than a deferral to the existing Job-ordering suite (see Success Criteria).

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `agent/session_health.py` corruption probe: the replacement `is_valid()` call is wrapped so a `ModelException` from the ttl/expire_at check is classified as corrupt rather than escaping the sweep. Test asserts the WARNING fires and the row is deleted, and that one raising row does not abort the loop over the remaining rows.
- [ ] `agent/session_health.py` TTL keepalive: a `refresh_ttl()` that raises (`RedisError`, or `DataError` if `_redis_key` is reintroduced) is logged at WARNING, does not abort the sweep, and (the assertion that matters) **does not delete the row**. Patch `AgentSession.refresh_ttl` to raise for a healthy row and assert `session.delete` was never called and the row still exists. A keepalive folded into the classification `try/except` passes every other test in this plan and fails only this one.
- [ ] `agent/session_archive.py:_rehydrate_row`: three payload shapes, all of which occur, none of which raises. (a) `updated_at` is a real `datetime` → preserved **byte-identically**. (b) `updated_at` is `None` → fresh stamp; assert `updated_at is not None` and within seconds of restore time. (c) `updated_at` is a raw **unparsed ISO string** (the shape `_deserialize_payload` leaves behind after its `ValueError` warning at `:214-225`) → same assertion as (b). Shapes (b) and (c) are indistinguishable at the model boundary because `__setattr__` maps both to `None` (Research finding 0); the failure they guard against is a **silently stamp-less restored row**, not a quarantine. Assertions (a) and (b) must fail in opposite directions — (a) reds if the guard becomes `False`, (b) reds if it becomes `True` — which is what makes the pair non-vacuous. Do not assert on `_record_row_failure`: it is not on this path.
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

- [ ] `tests/unit/test_session_health_phantom_guard.py` — UPDATE: the corruption-probe tests exercise `cleanup_corrupted_agent_sessions` against real records (`:118`, `:169`, `:280`, `:306`). None currently assert on `save()` being called, so they should keep passing unchanged; add assertions that a healthy row's `updated_at` is byte-identical before and after the sweep. **ADD the TTL-neutrality test here.** It is the one guard in this plan protecting against a silent, delayed, per-row data loss (Risk 4b), and this file is its natural home. Two seeded rows (one terminal, one non-terminal), `time.sleep(1.5)` to force a measurable decay, assert the decay, sweep, then assert each key's TTL is back at `AgentSession._meta.ttl` (never the literal `2592000`) with `updated_at` byte-identical. Success Criteria carries the full statement.
- [ ] `tests/unit/test_session_health_orphan_process_reap.py` — UPDATE: asserts the `{"corrupted": int, "orphans": int}` return shape (`:340`, `:352`). Return shape is unchanged; verify no test depends on the probe's write as a side effect.
- [ ] `tests/unit/test_session_health_unconditional_index_repair.py` — UPDATE: exercises the same sweep; confirm the index-repair assertions do not depend on the per-row save.
- [ ] `tests/unit/test_stale_cleanup.py` — UPDATE: covers `_cleanup_stale_sessions`; the return tuple goes from 3 to 5 values, so every unpack site needs widening. Add the ledger-skip and heartbeat-skip cases.
- [ ] `tests/unit/test_update_stale_session_fence.py` — UPDATE, and it is under-scoped by "add an assertion": the arity change reds the **entire file** through a shared helper, and separately breaks a test class whose assertions are on **source text and signature metadata** rather than behavior. All of the following are in scope, in the same commit as the arity change:
  - **`_run_cleanup` (`:89-121`) — FIRST, because it gates every other test in the file.** Its line `:115` unpacks `killed, skipped_recent, skipped_fence_live = _cleanup_stale_sessions(...)`, so the 3→5 widening makes it raise `ValueError: too many values to unpack` and every test in the file goes red mechanically, with no policy meaning whatsoever. Widen the unpack to all five names, return all five plus the `finalize` mock, and update all **12** call sites (`:133, :154, :164, :176, :189, :208, :219, :234, :247, :259, :270, :274`) — most use positional throwaways (`_, skipped_recent, skipped_fence_live, _ = ...`), so each needs an extra slot. **No fixture change is needed:** `_session()` (`:55-86`) returns a `SimpleNamespace` with no `is_ledger` attribute, and `_is_ledger` (`agent/session_health.py:51-62`) is `_truthy(getattr(entry, "is_ledger", False))` → `False`, so new rung 2 does not capture any existing row in this file.
  - `::test_fence_dead_but_recently_updated_is_still_spared` (`:199`) and `::test_fence_dead_but_young_by_created_at_is_still_spared` (`:215`) — **assertions unchanged; unpack widened via `_run_cleanup`.** Their assertions (`killed == 0`, `skipped_recent == 1`, `finalize.assert_not_called()`) pin the "the fence ADDS protection; it never subtracts it" invariant that Technical Approach step 4 preserves. Read the red correctly: a failure on an **assertion** means the build moved retention policy and has left this plan's scope; a failure on the **unpack** is just the arity edit not yet applied to `_run_cleanup`. Do not hunt a policy bug for an unpack error, and do not preserve the 3-tuple to keep these "unchanged" — that would make the `skipped_ledger`/`skipped_heartbeat` assertions this same sub-list requires impossible to write here.
  - `:327` — `assert counts == (1, 1, 1)` — UPDATE to the 5-tuple, with the comment naming all five positions.
  - `class TestCallerUnpacksThreeValues` (`:332`) — RENAME; the class name encodes the old arity.
  - `::test_run_update_unpacks_the_new_arity` (`:333-346`) — UPDATE: asserts the literal source string `"stale_killed, skipped_recent, skipped_fence_live = _cleanup_stale_sessions("` appears in `inspect.getsource(run_module.run_update)`. Rewrite to the 5-name unpack. This fails on string identity, so the new names must match the implementation character for character.
  - `::test_function_signature_declares_a_three_tuple` (`:348-352`) — UPDATE and RENAME: asserts `inspect.signature(_cleanup_stale_sessions).return_annotation in ("tuple[int, int, int]", tuple[int, int, int])`. Both the string and the typing form must move to the 5-tuple.
  - `::test_run_update_logs_the_fence_skip_distinctly` (`:354-361`) — KEEP GREEN: asserts `"fence verified"` survives in `run_update`'s source. Preserve that substring when rewording the summary for the two new counters.
  - ADD: the new heartbeat rung is additive. A fence-dead row with a fresh `last_heartbeat_at` is **spared** (rung 4 skips it), which is the same asymmetry the two existing fence-dead tests assert. Pin it.
- [ ] `tests/unit/test_session_archive.py` — UPDATE: add restore-preserves-`updated_at` coverage alongside the existing rehydrate assertions, and both fall-through shapes (`None` and an unparsed ISO string). Each fall-through case asserts the restored row carries a **non-`None`, near-restore-time** `updated_at` — a stamp-less restored row is the regression this plan could introduce, and it is silent rather than raising.
- [ ] `tests/unit/test_session_archive_cli.py` — UPDATE: verify the `restore --dry-run` path is unaffected (it writes nothing either way).
- [ ] `tests/integration/test_session_archive_cold_boot.py` — UPDATE: the cold-boot round trip is the natural home for the end-to-end assertion that an archived timestamp survives restore.
- [ ] `tests/unit/test_worker_persistent.py` / `tests/unit/test_worker_entry.py` — UPDATE: both patch `cleanup_corrupted_agent_sessions` at the worker-startup call site (`:157`, `:200`); confirm the patches still bind after the internal change.
- [ ] `tests/unit/test_migrate_strip_pid_fields.py` — UPDATE: `scripts/_strip_migration.py:32-40` documents the restamp as a known concurrent-writer property in its safety argument. That paragraph becomes wrong when Task 1 lands. Update the module docstring and any test asserting on it — the atomicity argument (one MULTI/EXEC pipeline) still holds and is the load-bearing half; only the "who else writes terminal rows" example changes.
- [ ] `tests/unit/test_recovery_respawn_safety.py` — NO CHANGE, listed to record that it was checked and excluded. It never imports `_cleanup_stale_sessions`; it drives `agent/session_health.py:985::_recover_interrupted_agent_sessions_startup`, a different reaper with a different guard (`AGENT_SESSION_HEALTH_MIN_RUNNING` against `started_at`). This plan's diff cannot reach it, so asking a builder to "verify the new ladder does not change its outcome" would be a check that cannot fail. The reaper's real legacy-row coverage is `tests/unit/test_stale_cleanup.py`, listed above with the 3-to-5 tuple widening.
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
- **Reordering `/update` Step 5.5.** Tempting, because the probe-before-reaper ordering is what makes the self-defeat total. With Task 1 the probe no longer moves `updated_at` (its only healthy-path command is an `EXPIRE`, which does not touch the field), so the ordering stops mattering. A reorder would encode the old assumption in the call graph and hide a regression if the probe ever stamps again. Pin the invariant with a test instead.

## Risks

### Risk 1: `is_valid()` classifies rows as corrupt that the old save-probe let live, and they get deleted

**Impact:** Data loss. The probe's positive result routes straight to `session.delete()` (`agent/session_health.py:5582+`).

**Mitigation:** the substitution is **classification-neutral**, which is a weaker and true claim rather than the false claim that the two predicates are identical. `pre_save` raises `ModelException` on three distinct conditions, and the old string filter (`"invalid"` or `"validation"` in the message) caught exactly one of them:

| `pre_save` raise | Message | Old probe classified corrupt? | New `is_valid()` classifies corrupt? |
|---|---|---|---|
| `is_valid()` returned False (`popoto/models/base.py:908-913`) | `"Model instance parameters invalid. Failed to save."` | Yes (matches `"invalid"`) | Yes |
| Unique **index** violation (`:943-951`) | `"Unique index violation on (...): ... already exists"` | No — contains neither `"invalid"` nor `"validation"` | No — never reached; `is_valid()` does not run index checks |
| Unique **field** violation (`:968-978`); `id` is unique on `AgentSession` | `"Unique constraint violated: id=... already exists on another instance"` | No — same reason | No — same reason |
| **New in Task 1:** the `refresh_ttl()` keepalive raises: `redis.exceptions.RedisError` on a transient connection fault, or `DataError: Invalid input of type: 'NoneType'` if a builder reintroduces `self._redis_key` (step 1b) | Redis client exceptions, not `ModelException` | n/a (no keepalive existed) | **No, and this is mandatory.** Caught by the keepalive's own `try/except Exception`, logged at WARNING, and it must never touch `is_corrupt`. |

So both the old probe and the new call classify on the `is_valid()` condition alone, and both are silent on the two unique-constraint conditions. No row changes classification.

**The keepalive must be a separate statement after classification, in its own `try/except`.** The branch it lands in routes a positive result straight to `session.delete()` (`agent/session_health.py:5583-5585`). A builder who folds the keepalive into the classification `try/except` converts a transient Redis error during a sweep into a bulk delete of live session rows: every row whose `EXPIRE` failed is classified corrupt and removed. Order and isolation are both load-bearing. Classify first with `is_valid()`, then, only on the healthy branch, call `refresh_ttl()` inside a `try/except Exception: logger.warning(...)` that assigns nothing.

**How wide is the `is_valid()` condition on this model? Two fields.** This is the part two prior revisions of this risk got wrong by reasoning from popoto's generic validator instead of from `AgentSession`. Per Research findings 0 and 0b, executed against the live model:

- `AgentSession.__setattr__` (`models/agent_session.py:735-778`) coerces every `_DATETIME_FIELDS` value to a `datetime` or `None` **before** `is_valid()` ever runs, so **no `DatetimeField` on this model can fail validation** — including `updated_at`.
- `AgentSession` has exactly **two `null=False` fields** (`id`, `created_at`) and **zero `max_length` fields**, so the null and length branches of popoto's per-field check have a two-field surface, not a whole-model one.
- `id` cannot be made bad after construction: `AutoKeyField` rejects a non-32-char value at `__init__`. And a bad `id` is already caught by Check 1 (the length test at `agent/session_health.py:5558-5566`) before Check 2 runs.

That leaves **`created_at`** — datetime-typed, `null=False`, and a `SortedField`, therefore deliberately outside `_DATETIME_FIELDS` (`:696-709`) and unhealed by the normalizer. It is the model's only constructible `is_valid()`-False fixture, verified both ways: `s.created_at = None` → `False` (null check, `popoto/models/base.py:851-855`); `s.created_at = "TOTALLY-NOT-A-DATE"` → `False` (coercion `TypeError`, `:829-846`).

**What this means for the risk, honestly:** Check 2's blast radius is one field, and the risk of the substitution is correspondingly small — which is a better outcome than a wide-but-unverified claim. The parity test is written against that fixture and against the sweep's real branch, with `AgentSession.query.all` patched to yield it. The plan does not assert that any Redis hydration path produces a null `created_at`; none was found, and the test does not need one to be falsifiable.

The ttl/expire_at mutual-exclusion `ModelException` that `is_valid()` can raise before the field loop stays unreachable for this model, but **not** because `ttl` is unset — `Meta.ttl` *is* set from `settings.timeouts.agent_session_retain_ttl_s` (`models/agent_session.py:631`), so an instance carries `_ttl = 2592000`. The raise requires **both** `_ttl` and `_expire_at` to be truthy, and nothing in the repo ever sets `expire_at` on `AgentSession` (grep for `expire_at` in `models/agent_session.py` returns zero hits). The branch is unreachable on the `_expire_at` term. Task 1 still wraps the call so that raise would be classified corrupt if it ever became reachable, and that wrapping is what the "deliberate widening" note refers to.

The build lands a test that asserts a healthy row is never classified corrupt, and the sweep's existing per-row try/except keeps one pathological row from cascading.

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
| no fence, mid-execution, `updated_at` carried only by the 25-min calendar heartbeat | spared with a **300-second margin** — `CALENDAR_HEARTBEAT_INTERVAL = 25 * 60 = 1500s` (`agent/session_executor.py:612`, written at `:2246-2247`) against `RECENT_ACTIVITY_WINDOW = 1800s`, plus the probe's incidental restamp on every worker start, `/update`, and hourly reflection | spared at rung 4 on a **60-second** signal (`HEARTBEAT_WRITE_INTERVAL = 60`, `agent/session_executor.py:2219-2228`) |
| no fence, genuinely idle | finalized at the `created_at` gate | unchanged |

Every added rung is a `continue`. No row moves from spared to finalized.

**Row 5 is why rung 4 is load-bearing rather than opportunistic.** Task 1 removes the probe's whole-row write, and the probe was one of the two things refreshing an unfenced mid-execution row's `updated_at`. What remains is the 1500s calendar tick inside an 1800s window: still spared, but on a 300-second margin that any tick jitter, event-loop stall, or `RECENT_ACTIVITY_WINDOW` retune eats. Rung 4 replaces a 300-second margin with a 1740-second one. The plan previously described rung 4 as "reachable independently of the restamp bug," which is true and reads as optional scope — it is not optional; it is the rung that keeps this table's closing claim true after Task 1 lands. The `updated_at` rung is retained for the non-ledger worker-less rows enumerated in Technical Approach step 4 — the live local Claude Code CLI session and the enqueue window — which are the row shapes whose protection depends on a signal this plan is narrowing.

### Risk 3: the ledger skip does not fix ledger accumulation

**Impact:** Once the `/update` reaper explicitly skips ledger anchors, nothing reaps them. They accumulate — which is the state that produced the 59 rows.

**Mitigation, and the honest framing:** the skip introduces **no regression**, because the accumulation predates it and has a different cause. Today the `/update` reaper already cannot reach a fence-less ledger row: the Problem statement establishes that the probe restamps every row seconds before the reaper reads it, so "every fence-less row is guaranteed to land in `skipped_recent`. The reaper cannot reach a fence-less row on the `/update` path, ever." The skip replaces a forged mechanism with an honest one and holds the net outcome constant.

The real gap is that `tools/sdlc_session_ensure.py --kill-orphans` — the tool that *is* the right authority, deciding on issue-lock ownership rather than process liveness — is a manual CLI that nothing schedules. That is tracked as **#2677** and deliberately excluded here (see No-Gos). Scheduling it turns a human-invoked CLI into an unattended hourly fleet-wide `finalize_session()` actuator on `running` eng sessions, which is the exact class #2439 removed as unsafe by design. It needs its own safety argument, a soak, a kill switch, and a metric, none of which belong bolted onto a timestamp-authorship fix.

**And this plan makes that follow-up strictly harder, which is recorded in #2677:** Task 3 stops the sibling-renewal restamp, so `_iter_orphan_sessions`'s no-payload fallback (`tools/sdlc_session_ensure.py:1013-1018`, reaping purely on `_last_activity_at >= ORPHAN_AGE_SECONDS` where `_last_activity_at` reads `updated_at` first at `:897`) becomes reachable for anchors it previously could not touch. 600 seconds is short for a live BUILD stage. That window has to be re-derived before anything schedules the reaper. The manual CLI is unaffected in practice — the issue-lock payload normally resolves and short-circuits the fallback — but the margin narrows, and an unattended caller must not inherit it silently.

### Risk 4: `_strip_migration.py`'s safety argument silently becomes stale

**Impact:** A future reader trusts a docstring that describes a concurrent writer which no longer exists, and draws the wrong conclusion about what the MULTI/EXEC pipeline is protecting against.

**Mitigation:** `scripts/_strip_migration.py:32-40` is an explicit Test Impact item. The load-bearing half of its argument (atomicity, not quiescence) survives; only the cited example changes. Per the repo's no-legacy-code rule, the paragraph is rewritten to the new status quo rather than annotated with history.

### Risk 4b: the TTL keepalive is deleted as redundant, silently activating a 30-day expiry

**Impact:** The keepalive is the only Redis command left on the healthy path, and a value-preserving write would have looked exactly like the thing this plan exists to remove. A reviewer or a later cleanup pass reads "we removed the pointless write, why is there still a write?" and deletes it. Nothing fails, no test goes red on a fresh fixture, and 30 days later production session rows start disappearing from the authoritative store one at a time. This is the highest-consequence, lowest-visibility failure in the plan: the damage is silent, delayed well past the merge, and irreversible per row.

**Mitigation:** four independent guards, because a comment alone will not survive. (a) **The mechanism's own name.** `session.refresh_ttl()` says what it is at the call site; a reader deleting it knows they are deleting a TTL refresh, which is the difference between a considered decision and a tidy-up. This is why the keepalive is a named model method rather than an inlined `save(update_fields=[...])` or a bare `expire()` (step 1b). (b) The inline comment and the method docstring both name #2698 and state that deleting the call activates an expiry, rather than merely describing what the call does. (c) A Success Criterion asserts TTL neutrality behaviorally, on a key whose TTL has been allowed to decay first, so the assertion can actually fail (asserting on a freshly-written key passes either way, which is the trap that makes the `Meta.ttl = None` route look correct). That criterion is carried into Test Impact and Task 5 so the test engineer writes it, not only into the Success Criteria list. (d) A positive Verification row asserts the `refresh_ttl()` call is present in the sweep's AST body, and the No-Gos record the decision so the next planner sees it without reading the diff. Note that the delayed, per-row nature of the damage is exactly why the criterion cannot be "no row expired during the test."

### Risk 5: restore preserves a timestamp from a row that was archived mid-corruption

**Impact:** A restored row could carry an `updated_at` that is future-dated or nonsensical relative to restore time.

**Mitigation:** `_heal_future_updated_at` already detects (read-only) future-dated rows and logs them for operator visibility, and `agent/session_health.py`'s staleness reads use Redis `TIME` as a single shared clock, which #1817 established makes a skewed `updated_at` harmless to read. Preserving a bad archived value is strictly no worse than the current behavior of overwriting it with a value that is *wrong in a way nothing can detect*.

### Risk 6: narrowing the run-lock bind removes a liveness signal something still depends on

**Impact:** The bare `session.save()` at `tools/sdlc_session_ensure.py:493` runs before every `ensure_session()` return. Any reader that has come to rely on "a stage dispatch happened recently" arriving as `updated_at` loses it. The candidate victim is the `sdlc-local-*` pipeline anchor whose only other liveness signal is the stage-state advance.

**Mitigation:** the #1676 signal is a **different writer**. `tools/sdlc_session_ensure.py:73-79` defines the orphan predicate as "no heartbeat AND no `stage_states` write refreshing `updated_at`" — the stage-state advance, not the run-lock bind. A pipeline that is actually advancing still refreshes the row through that path. A pipeline that only re-dispatches the same stage without advancing stops refreshing it, which is precisely the mirage #2305 defect 1 named and the behavior this plan exists to remove.

The three known consumers are enumerated and each is checked in the build:

- `_iter_orphan_sessions`'s no-payload fallback — the one that genuinely narrows. Covered under Risk 3 and carried into #2677.
- `/update`'s rung 5 — **not a consumer for these rows at all.** The bind writes only to `sdlc-local-*` anchors, which carry `is_ledger=True` (`tools/sdlc_session_ensure.py:810`) and are spared at rung 2 before rung 5 is consulted. Rung 5's own consumers (Technical Approach step 4) are non-ledger rows the bind never touches.
- `best_timestamp` — a ledger anchor's ordering now reflects real stage advances rather than dispatch churn, which is the fix.

Task 3's test set pins each.

## Race Conditions

### Race 1: the corruption sweep reads a row that an executing session writes mid-iteration

**Location:** `agent/session_health.py:5548-5571` (the `for session in all_sessions` loop) versus `agent/session_executor.py:2226-2227` (the 60s heartbeat).

**Trigger:** the sweep loads the full row set, then iterates; an execution heartbeat lands on a row after it was loaded.

**Data prerequisite:** none — after Task 1 the sweep performs no value-moving write on the healthy path, so there is nothing to lose. Its only healthy-path command is the `refresh_ttl()` `EXPIRE` on the hash key, which sets key metadata and cannot overwrite a field, so it cannot lose an update no matter when it lands relative to a heartbeat. This race exists today and is *worsened* by the current code: the sweep's stale in-memory snapshot is written back over the heartbeat's fresh write. Removing the field write removes the race.

**State prerequisite:** none.

**Mitigation:** eliminated by Task 1, not merely narrowed, and the choice of keepalive mechanism is what keeps that word honest. A value-preserving partial save would have re-persisted one field from the stale snapshot and reduced the race rather than closing it; `EXPIRE` writes no field value at all. The delete path (the only remaining field write) is already guarded by `_filter_hydrated_sessions` and by the ORM delete's own key resolution.

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
- [SEPARATE-SLUG #2698] **Activating the `AgentSession` 30-day expiry.** `Meta.ttl = 2592000` has never fired, because the probe this plan removes was resetting it to the ceiling on every sweep tick. Letting it activate is defensible on its own merits (it is the declared intent of the field) but it is a data-retention policy change with no archive backstop: `restore_if_empty()` is a cold-start-only rehydrate, so a row expiring on its own is simply gone from the authoritative store. This plan therefore ships TTL-neutral via the step 1b `AgentSession.refresh_ttl()` keepalive and defers the decision. Recorded as a No-Go so a builder does not "simplify" the keepalive away while implementing Task 1. When #2698 decides the policy, deleting one call from the sweep is the whole change on the "let it expire" side.
- **Making the fence-dead rung terminal in `_cleanup_stale_sessions`.** A dead fence currently selects a reason string and falls through to the recency and age gates (`scripts/update/run.py:272`), an asymmetry two green tests pin as "the fence ADDS protection; it never subtracts it." Making it terminal would finalize rows seconds after spawn, bypassing the 120-minute floor. That is a retention-policy change; this plan is a write-authorization fix. If it is wanted, it belongs in its own issue with its own risk analysis.

## Update System

No `/update` **skill** changes required, but `scripts/update/run.py` is directly modified by Task 4 (the reaper's liveness ladder and its summary counters). No new dependencies and no migration for existing installations: the change adds no Popoto field, so `scripts/update/migrations.py` is untouched and `data/migrations_completed.json` gains no entry.

**No config file changes.** `config/reflections.yaml` is untouched — the `sdlc-ledger-orphan-reap` registration moved to #2677.

One propagation note: the fix changes what `/update` reports. Machines running the old code will keep printing `Skipped N live session(s) (recent heartbeat)` until they pull. That is cosmetic and self-resolving on the next `/update`; no coordinated rollout is needed.

## Agent Integration

No agent integration required. This is a worker/maintenance-internal change. No new CLI entry point in `pyproject.toml [project.scripts]`, no new MCP surface, and no bridge import — the four touched modules (`agent/session_health.py`, `agent/session_archive.py`, `tools/sdlc_session_ensure.py`, `scripts/update/run.py`) are already reachable from the worker, the SDLC CLI, and the update script, and `models/agent_session.py:save()` gains a default-off parameter that no agent-facing path passes. `tools/sdlc_session_ensure.py`'s CLI surface is unchanged: Task 3 narrows an internal write and adds no flag.

## Documentation

### Feature Documentation

- [ ] Create `docs/features/agent-session-liveness-authorship.md` — the durable statement of who is authorized to write which liveness field on `AgentSession`: `updated_at` (any writer with something to report; never a maintenance sweep and never a bind that only re-asserts identity), `last_heartbeat_at` (the executor heartbeat only), `last_turn_at` / `last_tool_use_at` (turn and tool boundaries only), and the `(exec_pid, pid_create_time)` fence (spawn only). Include the reaper's full liveness ladder **with the fence-dead fall-through stated explicitly** (it is the detail an earlier revision of this plan got wrong, and the doc is where a future reader should be able to check it), **which row shape each rung actually protects** (name rung 5's non-ledger consumers explicitly — the live local Claude Code CLI session and the enqueue window — and note that `sdlc-local-*` anchors are spared earlier at the ledger rung, so they are not rung 5's justification), the distinction between a stage-state advance and a run-lock bind as `updated_at` writers, and the split of reaping authority between the `/update` process-liveness reaper and the issue-lock-based orphan reaper (#2677). Include a short section on `Meta.ttl`: it has never fired because the corruption probe was resetting it incidentally, `AgentSession.refresh_ttl()` now holds it there deliberately, and #2698 owns the decision to stop. A reader who finds the keepalive and wants to delete it should land here.
- [ ] Add the entry to the `docs/features/README.md` index table.
- [ ] Update `docs/features/agent-session-health-monitor.md` — it describes stale-session detection and recovery, which is the behavior the reaper's new ladder changes. Add the ladder and cross-link the authorship doc.
- [ ] Cross-link from `docs/features/agent-session-fenced-execution-record.md` — the fence is the top rung of the ladder, and the authorship doc is the natural place a reader goes next.

### External Documentation Site

Not applicable — this repo has no external docs site.

### Inline Documentation

- [ ] Rewrite the `agent/session_health.py:5568` comment: "Try a no-op save to detect other validation failures" is the sentence that made this bug. The replacement says **the classification check** writes nothing to Redis, citing #1817's identical lesson in the same file. Scope it to the check: two lines below, the healthy branch issues the `refresh_ttl()` `EXPIRE`, so a comment claiming the whole path writes nothing would be false the moment a reader looks down. Do **not** write "read-only": `is_valid()` calls `setattr` during type coercion (`popoto/models/base.py:829-839`), so it does mutate the in-memory instance. That is harmless here — the hydrated instances are discarded at the end of the loop — but this comment is the artifact a future reader will trust, so it has to name the property that actually holds.
- [ ] Comment the `refresh_ttl()` call site itself with the #2698 framing from Risk 4b: it holds a 30-day expiry that has never fired, deleting it activates that expiry silently, and the policy call lives in #2698. Explain why it sits outside the classification `try/except` (a keepalive failure must never route a live row to `session.delete()`).
- [ ] Write the `AgentSession.refresh_ttl()` docstring so it carries the #2698 decision, not just the mechanic: what the method does (`EXPIRE` on the hash key, no field written), why `self.db_key.redis_key` rather than `self._redis_key` (the latter is `None` on query-hydrated rows), and that deleting its call site in `cleanup_corrupted_agent_sessions` activates a 30-day expiry that has never fired.
- [ ] Extend the `AgentSession.save()` docstring (`models/agent_session.py:995-1013`) to document `preserve_updated_at` alongside the existing `update_fields` guard paragraph.
- [ ] Rewrite `scripts/_strip_migration.py:32-40` to the new status quo (atomicity is the safety property; the restamping-probe example is gone).
- [ ] Update `_cleanup_stale_sessions`'s docstring (`scripts/update/run.py:189-213`) with the new ladder and the ledger skip. State the fence-dead fall-through explicitly — the current docstring says a fence-dead session "is finalized with a reason that says the fence verified it," which reads as terminal and is the sentence that misled this plan's first revision.
- [ ] Document the narrowed bind at `tools/sdlc_session_ensure.py:486-493`: a comment stating that the partial save is deliberate, that the bind runs before every `ensure_session()` return so a whole-row stamp would be a per-dispatch restamp rather than a report of activity, and that the #1676 liveness signal comes from the stage-state advance instead.

## Success Criteria

- [ ] **`cleanup_corrupted_agent_sessions()` writes no `AgentSession` field value on the healthy path.** This is the `updated_at`-immobility property. Each seeded healthy row's `updated_at` is byte-identical before and after a sweep. **The assertion is on the field, not on a Redis command count**, and the criterion is deliberately *not* "no Redis command touches the hash key": the healthy path issues exactly one command against each session hash, the step 1b `refresh_ttl()` `EXPIRE`, which sets key metadata and writes no hash content. A validator instrumenting commands on the hash key would red a correct build. The sweep also unconditionally calls `AgentSession.repair_indexes()` (`agent/session_health.py:5649`, the "Unconditional `repair_indexes()` (issue #1361)" block whose comment records that the prior gate was removed permanently) and then `clean_indexes()` on `AgentSession` and `Memory`. Those are index writes by design and stay. A command-count spy is guaranteed non-zero on a correct build, so a literal "zero writes" criterion would either fail a correct implementation or push a builder into re-introducing the #1361 gate.
- [ ] Its corruption-detection behavior is unchanged, **against the one fixture this model can construct**: an `AgentSession` whose `created_at` is `None` (the sole `is_valid()`-False shape — see Risk 1) is classified corrupt by both the old save-probe and the new `is_valid()` call, and a healthy row is classified corrupt by neither. Drive the sweep with `AgentSession.query.all` patched to yield the instance. Mutation check: hard-code the new check to `True` and the healthy-row half must go red; hard-code it to `False` and the corrupt-row half must go red. This criterion asserts classification parity, not that such rows occur in Redis — no hydration path producing one was found, and the criterion does not claim one exists.
- [ ] **The sweep is TTL-neutral (#2698).** Seed **two** healthy rows, one terminal and one non-terminal, so the criterion cannot pass while expiry is active for half the population. `time.sleep(1.5)` to let the TTL decay: Redis TTL has one-second granularity, so a shorter sleep makes the assert-decay step flaky. Assert the decay actually happened before proceeding, so the fixture cannot pass vacuously. Then run a full `cleanup_corrupted_agent_sessions()` sweep and assert each key's TTL is back at the ceiling while `updated_at` is byte-identical. **Compare against `AgentSession._meta.ttl`, never the literal `2592000`.** The ceiling is sourced from `settings.timeouts.agent_session_retain_ttl_s` and is env-overridable via `TIMEOUTS__AGENT_SESSION_RETAIN_TTL_S` (`models/agent_session.py:628-631`), so a hard-coded literal is a test that breaks on a config change instead of on a regression. **Both halves are required and they fail in opposite directions:** delete the keepalive and the TTL half goes red; revert `is_valid()` to `save()` and the `updated_at` half goes red. Assert on the TTL value read back from Redis, not on whether `refresh_ttl()` was called. A criterion written against a freshly-written key passes whether or not the keepalive exists, which is precisely how the rejected `Meta.ttl = None` approach measured as correct.
- [ ] Archive restore reproduces the archived `updated_at` **byte-identically** when the payload carries a real `datetime`, and falls back to a fresh stamp for **both** non-`datetime` shapes — `None`, and a raw ISO string that `_deserialize_payload` could not parse. The restored row's `updated_at` must be non-`None` in both fallback cases. The failure being guarded is a silently stamp-less row, not a quarantine: `AgentSession.__setattr__` maps both shapes to `None` before `save()` runs, so nothing raises either way (Research finding 0). The two assertions must fail in opposite directions under a hard-coded flag.
- [ ] A `/sdlc` stage dispatch that only re-binds the run lock leaves `updated_at` unmoved, while still persisting `active_run_id` and `owned_run_ids` (the post-save readback at `tools/sdlc_session_ensure.py:509-515` still passes). A dispatch that advances a stage state still refreshes `updated_at`.
- [ ] `_cleanup_stale_sessions` skips `is_ledger` rows before evaluating any liveness rung, and reports `skipped_ledger` separately.
- [ ] `_cleanup_stale_sessions` consults `last_heartbeat_at` between the fence and `updated_at`, and reports `skipped_heartbeat` separately.
- [ ] **The fence-dead fall-through is preserved.** `tests/unit/test_update_stale_session_fence.py::test_fence_dead_but_recently_updated_is_still_spared` and `::test_fence_dead_but_young_by_created_at_is_still_spared` pass unchanged, and a fence-dead row with a fresh `last_heartbeat_at` is **spared** by the new rung. Every added rung is a `continue`; no row moves from spared to finalized.
- [ ] **The `updated_at` rung survives and is demonstrably reachable.** The fixture is **non-ledger** (`is_ledger` falsy, no fence, no `last_heartbeat_at`, `updated_at` inside `RECENT_ACTIVITY_WINDOW`, `created_at` older than the 120-minute threshold) — the shape of a live local Claude Code CLI session (`.claude/hooks/user_prompt_submit.py:338`). It must be skipped and counted in `skipped_recent`. A ledger fixture proves nothing here: rung 2 returns first, so the criterion would be green with rung 5 deleted. The mutation check is mandatory — delete rung 5, confirm this test goes red.
- [ ] A separate ledger fixture (`is_ledger=True`, same otherwise) is skipped and counted in `skipped_ledger`, **not** `skipped_recent` — pinning that the two rungs are distinguishable and that rung 2 precedes rung 5.
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
  - Role: Task 1 and Task 2 — `is_valid()` corruption probe, `AgentSession.refresh_ttl()` keepalive, `preserve_updated_at` on `AgentSession.save()`, archive restore opt-in
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
  - Role: Task 7 — verify Success Criteria, especially the `updated_at`-immobility property, TTL neutrality, the fence-dead fall-through, and the dashboard end-to-end assertion
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `restamp-docs`
  - Role: Task 6 — the feature doc, the index entry, and the five inline rewrites
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Corruption probe that writes no field value, plus the TTL keepalive

- **Task ID**: build-probe
- **Depends On**: none
- **Validates**: `tests/unit/test_session_health_phantom_guard.py`, `tests/unit/test_session_health_orphan_process_reap.py`, `tests/unit/test_session_health_unconditional_index_repair.py`
- **Informed By**: Research findings 0 and 0b (`AgentSession.__setattr__` heals every `_DATETIME_FIELDS` value, so `created_at` is the model's only `is_valid()`-False fixture); popoto `pre_save` raises iff `is_valid()` is False (`base.py:908-913`); Prior Art #1817 (same file, same no-write resolution)
- **Assigned To**: `probe-builder`
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: true
- Replace the `session.save()` probe at `agent/session_health.py:5568-5580` with a `session.is_valid()` check; classify a raised `ModelException` as corrupt.
- Preserve the WARNING log line's shape and fields.
- Rewrite the "Try a no-op save" comment so that, **scoped to the classification check specifically**, it states the check **writes nothing to Redis**, citing #1817. Not "read-only" — `is_valid()` calls `setattr` during coercion (`popoto/models/base.py:829-839`). That wording describes `is_valid()` and must not be written as a claim about the whole healthy path, which does issue the keepalive `EXPIRE` two lines later.
- The parity fixture is `created_at = None`, and it is the only one. `AgentSession.__setattr__` heals every `_DATETIME_FIELDS` value to a `datetime` or `None` before validation, the model has zero `max_length` fields, and a bad `id` is rejected at construction and already caught by Check 1. Do not spend build time hunting a broader corrupt-row fixture; Risk 1 records why none exists.
- **Add `AgentSession.refresh_ttl()` (Technical Approach step 1b)**: `POPOTO_REDIS_DB.expire(self.db_key.redis_key, self._ttl)`, returning the bool. Its docstring names #2698 and states that deleting the call activates a 30-day expiry. **Use `self.db_key.redis_key`. Never `self._redis_key`.** That attribute is `None` on query-hydrated rows (`popoto/models/base.py:605-612`; `AgentSession` has four nullable KeyFields), and `expire(None, ...)` raises `redis.exceptions.DataError`. Step 1b records the measurement.
- **Call `session.refresh_ttl()` once per healthy row, as a separate statement AFTER classification, in its own `try/except Exception: logger.warning(...)`.** It must never touch `is_corrupt`: the branch routes a positive classification straight to `session.delete()` (`agent/session_health.py:5583-5585`), so a keepalive folded into the classification `try/except` turns a transient Redis fault into a bulk delete of live rows (Risk 1's last table row).
- **Do not truth-test the return value.** `EXPIRE` returns `False` when the key is absent, which is a legitimate no-op (the row was deleted concurrently, or its stored key has drifted from the computed one). `if not session.refresh_ttl(): logger.warning(...)` emits false failures on a correct build.
- Do **not** implement the keepalive as `session.save(preserve_updated_at=True)` or as any partial `save(update_fields=[...])`. Task 2 adds `preserve_updated_at` and you own both tasks, so this substitution is one keystroke away and it passes every behavioral gate in this plan. Step 1b records why the named method wins: no index churn, no `_UPDATED_AT_OMISSION_OK_FIELDS` edit, no field value written at all, and no dependence on popoto's key-drift branch being unreachable by accident. The Verification table carries an anti-check for both `save(` and `preserve_updated_at` inside this function.
- Leave `_UPDATED_AT_OMISSION_OK_FIELDS` (`models/agent_session.py:973-992`) alone. `refresh_ttl()` calls no `save()`, so there is no omitted stamp to downgrade. Task 3's `active_run_id` / `owned_run_ids` additions are the only edit that frozenset receives in this plan.
- Do **not** implement neutrality by clearing `Meta.ttl`. Step 1b records the measurement showing it leaves already-persisted keys decaying, so it would look neutral on a fresh fixture and expire production rows.

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
- In `agent/session_archive.py:_rehydrate_row`, pass `preserve_updated_at=isinstance(fields.get("updated_at"), datetime)`. **Guard on the value's type, never on key presence** — `_serialize_session` writes every field name, so the key is always there. The two non-`datetime` shapes that occur are `None` and a raw unparsed ISO string; `AgentSession.__setattr__` maps both to `None`, so preserving either restores a row with **no liveness stamp at all** (Technical Approach step 2 traces it). Both must fall through to the normal stamp. Nothing raises and nothing is quarantined on this path — do not write a test asserting `_record_row_failure` was skipped, and do not conflate it with the unparsed-`created_at` shape, which is a `SortedField` outside the normalizer and is neither caused nor fixed here.
- Never write the literal `preserve_updated_at=True` in `_rehydrate_row`. The Verification table carries an anti-check for it, because hard-coding the flag is the cheapest way to green a badly written gate and it silently reintroduces the stamp-less restore.

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
- **Validates**: `tests/unit/test_stale_cleanup.py`, `tests/unit/test_update_stale_session_fence.py`
- **Informed By**: Technical Approach step 4 (the rung-5 consumers are non-ledger rows, and the fence-dead rung is **not** terminal); Risk 2 (the ledger guard must land with the write-authorization fixes, not after)
- **Assigned To**: `reaper-builder`
- **Agent Type**: builder
- **Parallel**: true
- **Do not land Tasks 1 or 3 without this task in the same PR.** This skip is the guard for the ledger exposure those tasks create; see Architectural Impact → Reversibility.
- Add the `is_ledger` skip to `_cleanup_stale_sessions` (`scripts/update/run.py:250`), ordered before the fence check, terminal, counted as `skipped_ledger`. Reuse the existing `_is_ledger` helper (defined at `agent/session_health.py:51`) rather than re-deriving the predicate.
- Add the `last_heartbeat_at` recency rung between the fence and the `updated_at` fallback, counted as `skipped_heartbeat`, tolerating naive-datetime / float / unparseable values by falling through.
- **Preserve the fence-dead fall-through.** `fence_verified_dead = True` at `:272` selects a reason string and does not `continue`; leave it that way. Two green tests pin this (`test_fence_dead_but_recently_updated_is_still_spared`, `test_fence_dead_but_young_by_created_at_is_still_spared`) and both must stay green untouched. Making the rung terminal is an explicit No-Go.
- **Keep the `updated_at` rung (rung 5) exactly where it is.** Its consumers are non-ledger: the live local Claude Code CLI session (`.claude/hooks/user_prompt_submit.py:338`, refreshed by `agent/health_check.py:611-625`) and the enqueue window before the T+0 heartbeat. Ledger rows never reach it, so do not justify or test it with an `sdlc-local-*` fixture.
- Widen the return tuple to `tuple[int, int, int, int, int]` and the caller's unpack and summary at `scripts/update/run.py:1984` so each skip names its signal. The arity is asserted in source text and in the return annotation, not only behaviorally — see Test Impact.
- **The arity change reds `tests/unit/test_update_stale_session_fence.py` in full**, through the shared `_run_cleanup` helper's 3-tuple unpack at `:115`. That is a mechanical break, not a policy signal. Widen `_run_cleanup` and its 12 call sites in the same commit; Test Impact lists them.
- **Neither new rung is independently revertable once Task 1 lands.** Reverting the ledger skip finalizes a live pipeline's state anchor; reverting the `last_heartbeat_at` rung finalizes an unfenced session mid-turn, because Task 1 removes the padding that keeps its 1500s calendar tick comfortably inside the 1800s window. See Architectural Impact → Reversibility.
- Update the function docstring (`:189-213`) with the new ladder, stating the fence-dead fall-through explicitly.

### 5. Regression coverage

- **Task ID**: test-restamp
- **Depends On**: build-probe, build-preserve, build-bind, build-reaper
- **Assigned To**: `restamp-tests`
- **Agent Type**: test-engineer
- **Parallel**: false
- Work the Test Impact checklist item by item, including the `scripts/_strip_migration.py` docstring rewrite and the full `test_update_stale_session_fence.py` sub-list. Start with `_run_cleanup` (`:89-121`) — its 3-tuple unpack reds the whole file the moment the arity widens, and the source-text and return-annotation assertions are the ones that will not surface from behavioral reasoning.
- Add the `updated_at`-immobility assertion (byte-identical across a sweep, asserted on the field rather than a command count) and the corruption-parity assertion, whose only fixture is `created_at = None` (Risk 1).
- **Add the TTL-neutrality test** in `tests/unit/test_session_health_phantom_guard.py` (Success Criteria carries the full statement): two seeded rows, one terminal and one non-terminal; `time.sleep(1.5)` for a measurable decay because Redis TTL granularity is one second; assert the decay happened; sweep; assert both keys are back at `AgentSession._meta.ttl` with `updated_at` byte-identical. Compare against `_meta.ttl`, never the literal `2592000` (`TIMEOUTS__AGENT_SESSION_RETAIN_TTL_S` overrides it). This is the only test standing between the merge and a first-ever 30-day expiry on production session rows; do not skip it as redundant with the immobility assertion, which it is not (they fail in opposite directions).
- Add the ledger-skip, heartbeat-skip, fence-dead-fall-through, and bind-does-not-restamp cases.
- Add the rung-5 case with a **non-ledger** fixture (the live local Claude Code CLI shape: `is_ledger` falsy, no fence, no `last_heartbeat_at`, fresh `updated_at`, `created_at` past the threshold) asserting `skipped_recent`, plus a ledger twin asserting `skipped_ledger`. A ledger fixture alone cannot reach rung 5 — rung 2 returns first — so it would be a vacuous guard test.
- Add the three archive-restore payload shapes: real `datetime` preserved **byte-identically**; `None` and unparsed ISO string both restored with a non-`None`, near-restore-time stamp. The `datetime` case must red under a hard-coded `False`; the fallback cases must red under a hard-coded `True`. A single "the row restored" assertion is vacuous.
- Add the dashboard-symptom test in `tests/unit/test_ui_jobs_grouping.py` with at least two non-terminal rows.
- Add the Failure Path Test Strategy cases for each of the seven exception surfaces, including the keepalive-raises case whose assertion is that the row is **not** deleted.
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
- Confirm each Success Criteria item, with particular attention to the `updated_at`-immobility property, TTL neutrality, the preserved fence-dead fall-through, and the dashboard end-to-end assertion. **The property is `updated_at` immobility, not "no Redis command on the hash key."** One `EXPIRE` per healthy row is the correct build; instrumenting hash-key commands and requiring zero would fail it.
- Confirm the two anti-criteria: `config/reflections.yaml` unchanged, `agent/health_check.py` unchanged.
- Report pass/fail.

## Verification

**Presence-greps are not gates here, and the reason is specific.** `is_valid()`, `_is_ledger`/`is_ledger`, `last_heartbeat_at`, and `preserve_updated_at` all read **zero** occurrences in their target files on today's tree (measured). Every one of them is also a word this plan separately mandates be written into a comment or docstring — Task 1 requires a rewritten comment, Task 4 requires a docstring "with the new ladder and the ledger skip." A whole-file `grep -c` for any of them therefore goes green on the prose alone and stays green with the rung deleted: a gate that cannot fire (#2658).

The rows below use a shared idiom instead — parse the target function, drop its docstring, and `ast.unparse` the rest. That strips **both** docstrings and comments, so what is left is code, and the assertions are on counter increments and call sites that prose cannot leave behind. Defined once here, referenced as `<AST-BODY f>` below:

```python
import ast, inspect, textwrap
fn = ast.parse(textwrap.dedent(inspect.getsource(f))).body[0]
if ast.get_docstring(fn):
    fn.body = fn.body[1:]
body = ast.unparse(fn)
```

The original two rows using it were executed against today's tree and correctly read **red** (`_is_ledger(` absent, `.is_valid()` absent, `session.save()` present), which is the demonstrated-red requirement from #2658. The keepalive rows added in round 4 read red today for the same reason (`.refresh_ttl()` absent; `AgentSession.refresh_ttl` does not exist, so its row raises `AttributeError`). Note for the builder: on Python 3.14 the compiler dedents `__doc__`, so the tempting `src.replace(f.__doc__, "")` shortcut strips nothing — use the AST form. The anti-check rows are the exception to the demonstrated-red rule and are green today by construction; each names the specific wrong implementation it exists to catch.

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `./scripts/pytest-clean.sh tests/unit/test_session_health_phantom_guard.py tests/unit/test_stale_cleanup.py tests/unit/test_update_stale_session_fence.py tests/unit/test_session_archive.py tests/unit/test_sdlc_session_ensure.py tests/unit/test_agent_session_updated_at_utc.py tests/unit/test_ui_jobs_grouping.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Corruption probe no longer saves | `grep -n "session.save()" agent/session_health.py` | exit code 1 |
| Probe validates without writing a field value | `python -c "import ast,inspect,textwrap; from agent.session_health import cleanup_corrupted_agent_sessions as f; fn=ast.parse(textwrap.dedent(inspect.getsource(f))).body[0]; fn.body=fn.body[1:] if ast.get_docstring(fn) else fn.body; body=ast.unparse(fn); assert '.is_valid()' in body and 'session.save()' not in body, body"` | exit code 0 (red today: `.is_valid()` absent, `session.save()` present) |
| **The TTL keepalive is present as code** | `python -c "import ast,inspect,textwrap; from agent.session_health import cleanup_corrupted_agent_sessions as f; fn=ast.parse(textwrap.dedent(inspect.getsource(f))).body[0]; fn.body=fn.body[1:] if ast.get_docstring(fn) else fn.body; assert '.refresh_ttl()' in ast.unparse(fn)"` | exit code 0 (red today: absent). **This is the only gate standing between a cleanup pass and a first-ever 30-day expiry on production rows (Risk 4b); every other keepalive row below is an anti-check.** |
| `refresh_ttl()` exists and targets the computed key, not `_redis_key` | `python -c "import ast,inspect,textwrap; from models.agent_session import AgentSession as A; fn=ast.parse(textwrap.dedent(inspect.getsource(A.refresh_ttl))).body[0]; fn.body=fn.body[1:] if ast.get_docstring(fn) else fn.body; body=ast.unparse(fn); assert 'db_key.redis_key' in body and '_redis_key' not in body, body"` | exit code 0 (red today: no such method). The AST idiom is required, not cosmetic: the mandated docstring explains why `self._redis_key` is wrong, so a whole-source `not in` check would fail a correct build. |
| **Anti-check:** no `save()` at all on the sweep's healthy path | `python -c "import ast,inspect,textwrap; from agent.session_health import cleanup_corrupted_agent_sessions as f; fn=ast.parse(textwrap.dedent(inspect.getsource(f))).body[0]; fn.body=fn.body[1:] if ast.get_docstring(fn) else fn.body; body=ast.unparse(fn); assert 'save(' not in body, body"` | exit code 0. Catches the keepalive being written as `save(preserve_updated_at=True)` or `save(update_fields=[...])`, which the `'session.save()' not in body` substring test above does **not** catch: `ast.unparse` normalizes the call and the literal never appears. |
| **Anti-check:** `preserve_updated_at` never reaches the sweep | `grep -c "preserve_updated_at" agent/session_health.py` | match count == 0. Task 2 adds that flag and `probe-builder` owns both tasks, so the substitution is one keystroke away and passes every behavioral Success Criterion (`updated_at` identical, TTL at the ceiling). |
| Restore guards the preserve flag on the value's type | `python -c "import ast,inspect,textwrap; from agent import session_archive as m; fn=ast.parse(textwrap.dedent(inspect.getsource(m._rehydrate_row))).body[0]; fn.body=fn.body[1:] if ast.get_docstring(fn) else fn.body; body=ast.unparse(fn); assert 'preserve_updated_at=' in body and 'isinstance(' in body, body"` | exit code 0 (red today: neither token present) |
| **Anti-check:** restore never hard-codes the flag | `python -c "import inspect; from agent import session_archive as m; assert 'preserve_updated_at=True' not in inspect.getsource(m._rehydrate_row)"` | exit code 0 |
| `save()` honors the preserve flag | `python -c "import inspect; from models.agent_session import AgentSession as A; assert 'preserve_updated_at' in inspect.signature(A.save).parameters"` | exit code 0 |
| Run-lock bind is a partial save | `grep -c 'save(update_fields=\["active_run_id", "owned_run_ids"\])' tools/sdlc_session_ensure.py` | output > 0 |
| No bare `session.save()` left in `_acquire_run_lock_and_bind` | `python -c "import inspect; from tools.sdlc_session_ensure import _acquire_run_lock_and_bind as f; src=inspect.getsource(f); assert 'session.save()' not in src; assert 'save(update_fields=[\"active_run_id\", \"owned_run_ids\"])' in src"` | exit code 0 |
| Bind fields are on the omission allowlist | `python -c "from models.agent_session import AgentSession as A; s=A._UPDATED_AT_OMISSION_OK_FIELDS; assert {'active_run_id','owned_run_ids'} <= s"` | exit code 0 |
| Both new rungs exist as code, not prose | `python -c "import ast,inspect,textwrap; from scripts.update.run import _cleanup_stale_sessions as f; fn=ast.parse(textwrap.dedent(inspect.getsource(f))).body[0]; fn.body=fn.body[1:] if ast.get_docstring(fn) else fn.body; body=ast.unparse(fn); assert '_is_ledger(' in body and 'skipped_ledger += 1' in body and 'skipped_heartbeat += 1' in body and 'last_heartbeat_at' in body, body"` | exit code 0 (red today: `_is_ledger(` absent) |
| `updated_at` rung retained (rung 5 not deleted) | `python -c "import ast,inspect,textwrap; from scripts.update.run import _cleanup_stale_sessions as f; fn=ast.parse(textwrap.dedent(inspect.getsource(f))).body[0]; fn.body=fn.body[1:] if ast.get_docstring(fn) else fn.body; body=ast.unparse(fn); assert 'skipped_recent += 1' in body and 'RECENT_ACTIVITY_WINDOW' in body"` | exit code 0 (green today — presence only; the falsifiable check is the mutation row below) |
| Rung 5 is mutation-checked, not merely present | `./scripts/pytest-clean.sh "tests/unit/test_stale_cleanup.py" -q -k "non_ledger and recent"` | exit code 0, and the test is red when rung 5 is deleted (builder records the mutation result) |
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
| BLOCKER | Risk \& Robustness | The round-2 BLOCKER remediation is written against a failure that cannot occur. `AgentSession.__setattr__` (`models/agent_session.py:735-778`) intercepts every assignment to `_DATETIME_FIELDS`, which includes `updated_at` (`:699`), coercing a `str` via `datetime.fromisoformat` or, on `ValueError`, to `None` (`:750-760`) -- its docstring says it exists to guard "against Popoto's is_valid() coercion failure when a DatetimeField holds a non-datetime value". So `AgentSession(id=archived_id, **fields)` normalizes at construction and `is_valid()` never sees a `str`. Verified: `AgentSession(id=..., updated_at="TOTALLY-NOT-A-DATE").is_valid()` returns `True` with `updated_at` set to `None` -- no `ModelException`, no `_record_row_failure`, no quarantine. The plan asserts the opposite in five places (Technical Approach step 2, Failure Path bullet 2(c), Success Criterion 3, Task 2 bullet 4, Critique Results row 1). | Round 3, fixed at the root: Research finding 0 (new); Technical Approach step 2 (rewritten); Failure Path bullet 2; Success Criterion 3; Task 2 bullets 4-5 | The `isinstance` code is right; the rationale and its test are not. The mandated assertion ("the restored row exists and carries a real timestamp, and `_record_row_failure` was not called") passes with the guard present, with `preserve_updated_at=True` hard-coded, and with the guard deleted -- vacuous, which is the mutation-check failure Task 5's last bullet demands be caught. The real difference is narrow: `_deserialize_payload` (`agent/session_archive.py:214-224`) leaves a raw `str` only when `fromisoformat` raised, and `__setattr__` coerces that same string to `None`, so an unconditional `preserve_updated_at=True` persists `updated_at=None` (a restored row with no liveness stamp) rather than losing the row. Rewrite step 2 to say that, and pin the guard with two assertions failing in opposite directions: (a) a real-`datetime` payload round-trips byte-identically (red if the guard drops to `False`); (b) a `None`/unparsed-string payload restores with `updated_at is not None` (red if hard-coded to `True`). |
| BLOCKER | History \& Consistency | The Verification gate is unsatisfiable by the mandated implementation, and the one edit that turns it green is the round-2 BLOCKER. The row "Restore preserves the archived timestamp" runs `grep -c "preserve_updated_at=True" agent/session_archive.py` requiring `output > 0`, but Task 2 mandates `preserve_updated_at=isinstance(fields.get("updated_at"), datetime)` -- the literal `preserve_updated_at=True` never appears in a correct build, so the count is 0 and the gate fails. Round 2 rewrote Technical Approach step 2 and Task 2 to the `isinstance` form and left this row on the pre-revision shape. | Round 3: Verification rows replaced with an `inspect`-scoped check plus a `preserve_updated_at=True` anti-check; Task 2 gains an explicit never-hard-code bullet | A builder facing a red gate makes it green the cheapest way, and the cheapest way is hard-coding `save(preserve_updated_at=True)` -- exactly what Critique Results row 1 forbids. Replace the row with `python -c "import inspect; from agent import session_archive as m; src=inspect.getsource(m._rehydrate_row); assert 'preserve_updated_at=' in src and 'isinstance(' in src, src"` (exit 0), plus a companion anti-check `python -c "import inspect; from agent import session_archive as m; assert 'preserve_updated_at=True' not in inspect.getsource(m._rehydrate_row)"` (exit 0). The anti-check is the half that survives a builder optimizing for a green table. |
| CONCERN | History \& Consistency | The `tests/unit/test_update_stale_session_fence.py` sub-list -- presented as exhaustive and as the edits "reasoning about the ladder will never surface" -- omits the one edit that gates every other test in the file, and mislabels two tests as unchanged. `:89-121` defines the shared helper `_run_cleanup`, which unpacks a 3-tuple at `:115` and has 13 call sites (`:133, :154, :164, :176, :189, :208, :219, :234, :247, :259, :270, :274`). The 3-to-5 widening makes `:115` raise `ValueError: too many values to unpack` and reds the whole file, yet the plan says `::test_fence_dead_but_recently_updated_is_still_spared` and `::test_fence_dead_but_young_by_created_at_is_still_spared` "KEEP GREEN, unchanged" and that a failure there means the build "changed retention policy and has left this plan's scope." | Round 3: `_run_cleanup` added as the sub-list's FIRST item with all 12 call sites; the two fence-dead tests restated as "assertions unchanged, unpack widened"; Task 4 and Task 5 both name the mechanical red | The instruction is a trap in both directions. Read literally, the builder sees a mechanical red and hunts a retention-policy bug that does not exist. Read defensively, the builder keeps `_run_cleanup` returning three counters to preserve "unchanged", which makes the `skipped_ledger`/`skipped_heartbeat` assertions the same sub-list requires impossible to write here. Fix: add `_run_cleanup` as the sub-list's FIRST item -- widen its unpack to `killed, skipped_recent, skipped_fence_live, skipped_ledger, skipped_heartbeat = _cleanup_stale_sessions(...)`, return all five plus the `finalize` mock, update all 13 call sites. Restate the two fence-dead tests as "assertions unchanged (`killed == 0`, `skipped_recent == 1`, `finalize.assert_not_called()`), unpack widened": a red on an assertion means policy moved, a red on an unpack is the arity edit. No fixture change needed -- `_session()` (`:75`) returns a `SimpleNamespace` with no `is_ledger`, so `_is_ledger` (`agent/session_health.py:51`) returns False and rung 2 does not capture these rows. |
| CONCERN | Risk \& Robustness | Risk 1's evidence is wrong for the third round running, and the Success Criterion it underwrites has no constructible fixture. The rewritten mitigation enumerates popoto's three `pre_save` raises but never accounts for the two facts that decide the question: `__setattr__` (`:747-766`) heals every `_DATETIME_FIELDS` value to a `datetime` or `None` before validation, and `AgentSession` has exactly two `null=False` fields -- `id` (`AutoKeyField`) and `created_at` (`SortedField`) -- and zero `max_length` fields (verified by enumerating `AgentSession._meta.fields`). A hydrated row cannot fail `is_valid()` on any datetime field, so Success Criterion 2 ("a row the old save-probe classified corrupt is still classified corrupt") names no fixture and ships green while Check 2 is a permanent no-op. | Round 3: Research finding 0b (new, executed against the live model); Risk 1 rewritten around the two-field failure surface; Success Criterion 2 names the `created_at = None` fixture and its mutation check; Task 1 tells the builder not to hunt a wider one | `created_at` is a `SortedField`, deliberately outside `_DATETIME_FIELDS` (`models/agent_session.py:696-709`), so it is the one datetime-typed field `__setattr__` does NOT heal, and it is `null=False`. That makes it the only realistic corrupt-row fixture: build the row, then bypass the normalizer with `object.__setattr__(s, "created_at", None)`; `s.is_valid()` is then False via `popoto/models/base.py:851-855`, and both the old probe and the new call classify it corrupt. Add to Risk 1: "`__setattr__` coerces every `_DATETIME_FIELDS` value to a `datetime` or `None` before `is_valid()` runs, so no datetime field can fail validation; `created_at` is a `SortedField` outside that set and one of only two `null=False` fields, so it is the corrupt-row fixture." |
| CONCERN | Risk \& Robustness | Risk 2's row-shape table omits the one row shape Task 1 newly exposes, and Reversibility pins the wrong half of Task 4. Removing the probe's whole-row write removes the last frequent `updated_at` refresh for an unfenced, actively-executing session: the executor's own `updated_at` write is the 25-minute calendar tick (`agent/session_executor.py:2241-2252`, `CALENDAR_HEARTBEAT_INTERVAL = 1500`) against `RECENT_ACTIVITY_WINDOW = 1800` -- a 300-second margin, where today the hourly `agent-session-cleanup` probe and the `/update` probe both refreshed it. Rung 4 is what keeps the table's claim ("No row moves from spared to finalized") true, yet the plan calls rung 4 "reachable independently of the restamp bug" (reads as optional scope) and Reversibility unit B names only the ledger skip as unrevertable while Tasks 1/3 are live. | Round 3: Risk 2 gains the calendar-heartbeat margin row plus a paragraph on why rung 4 is load-bearing; Reversibility unit B names both rungs; Technical Approach step 4 drops "reachable independently"; Task 4 restates it | Add a Risk 2 table row: "no fence, mid-execution, `updated_at` carried only by the 25-min calendar heartbeat \| spared (probe restamp + 1500s tick inside the 1800s window) \| spared at rung 4 (60s `last_heartbeat_at`, `HEARTBEAT_WRITE_INTERVAL = 60`)". Then amend Reversibility unit B from "Task 4 must not be reverted while either Task 1 or Task 3 is live" to name BOTH rungs: reverting the `is_ledger` skip finalizes a live pipeline's state anchor; reverting the `last_heartbeat_at` rung finalizes an unfenced session mid-turn once the probe's incidental refresh is gone. Both are single-rung reverts an operator reaches for mid-incident, and both are harmful only after Task 1 lands. |
| CONCERN | Scope \& Value | Round 2 established that a whole-file presence-grep is not a gate (it replaced the `updated_at` row and the `sed`-window row with `inspect.getsource`-scoped assertions), then left three rows of identical shape. `grep -c "is_valid()" agent/session_health.py`, `grep -c "_is_ledger\|is_ledger" scripts/update/run.py`, and `grep -c "last_heartbeat_at" scripts/update/run.py` all read 0 on today's tree, so each goes green the moment the mandated comment and docstring land -- Task 1 requires a rewritten comment stating the check is read-only, Task 4 bullet 7 requires a docstring "with the new ladder and the ledger skip". Each row is then green with its rung deleted. Same class as #2658 (gates that cannot fire). | Round 3: all three presence-greps replaced with AST-body assertions (docstring AND comments stripped), with a preamble recording the measured-zero counts and the demonstrated-red result | `inspect.getsource(f)` includes the docstring, so scoping to the function is not enough -- strip it and assert on the counter increments, which prose cannot leave behind. Concretely: `python -c "import inspect; from scripts.update.run import _cleanup_stale_sessions as f; src=inspect.getsource(f); body=src.replace(f.__doc__ or '', ''); assert '_is_ledger(' in body and 'skipped_ledger += 1' in body and 'skipped_heartbeat += 1' in body, body"` (exit 0); and `python -c "import inspect; from agent.session_health import cleanup_corrupted_agent_sessions as f; src=inspect.getsource(f); assert '.is_valid()' in src and 'session.save()' not in src"` (exit 0). |
| BLOCKER | Risk \& Robustness | (Round 4, on the TTL amendment.) A builder can satisfy the amendment with `session.save(preserve_updated_at=True)` and pass every gate. Task 2 adds that flag and the SAME agent (`probe-builder`) owns Tasks 1 and 2. Both Success Criteria stay green (`updated_at` identical, TTL at the ceiling, because popoto reapplies the TTL on any save), the `grep -n "session.save()"` row does not match the literal, and the AST row's `'session.save()' not in body` substring test is defeated by `ast.unparse` normalizing the call. Separately, nine Verification rows are positive assertions and **none** covers the one line whose deletion silently expires production. | Round 4: two anti-check Verification rows (no `save(` anywhere in the sweep's AST body; `preserve_updated_at` absent from `agent/session_health.py`) plus a POSITIVE row asserting `.refresh_ttl()` is in that body; Task 1 bullet forbidding the substitution by name | The substring test is the trap: `ast.unparse` re-renders the call, so the literal never appears regardless of what was written. Assert on `'save(' not in body` instead. Verified that the only `save(` in the function today is the probe at `:5571`, and the docstring's two `.save()` mentions are stripped by the AST idiom. |
| CONCERN | Risk \& Robustness | (Round 4, re-graded down from a reported blocker.) The claim that a value-preserving partial save is a lossy migration on a key-drifted row reads correctly against popoto source: `base.py:1126-1128` sets `obsolete_key = self._redis_key` when it differs from the computed key, and `:1214-1236` deletes that hash after `HSET`ting **only** the listed fields, where the full-save path encodes every field first (`:1290`). But the branch is gated on `if obsolete_key and ...`, and `_redis_key` is `None` on the query-hydrated instances this sweep handles, so it cannot fire. Not a live corruption risk. | Round 4: recorded in Technical Approach step 1b as the decisive argument for `refresh_ttl()` over the partial save | This is the strongest reason for the mechanism change, not against it. The partial-save route was safe here **by accident** of an undocumented popoto internal leaving an attribute unset on hydration, not by any designed safety property, and this repo already has a popoto bump planned (#2636). `refresh_ttl()` does not depend on that accident at all. |
| CONCERN | Risk \& Robustness | (Round 4.) The keepalive is being added to a branch that routes a positive classification straight to `session.delete()` (`agent/session_health.py:5583-5585`). Nothing in the amendment forbids a builder from folding it into the classification `try/except`, and if they do, a transient Redis error during a sweep classifies every affected healthy row as corrupt and **deletes live session rows**. | Round 4: Task 1 mandates a separate statement after classification in its own `try/except Exception: logger.warning(...)`; Risk 1's raise table gains a keepalive row; a Failure Path case asserts the row is not deleted when `refresh_ttl()` raises | The failure is loud in production and invisible in a green test suite, because no existing test makes the keepalive raise. The Failure Path case is the only thing that catches it: patch `refresh_ttl` to raise on a healthy row, assert `session.delete` was never called. |
| CONCERN | History \& Consistency | (Round 4.) Eight passages assert the sweep's healthy path "writes nothing to Redis," and the TTL amendment made every one of them wrong or mislabeled: Architectural Impact coupling (`:138`), Reversibility unit B's label (`:145`), Key Elements (`:175`, which also claimed the write amplification is *removed* rather than greatly reduced), Flow (`:183`), Rabbit Holes' reorder argument (`:409`), the `probe-builder` role (`:607`), Task 1's heading (`:637`), and Task 1's mandated comment wording (`:649`, true of `is_valid()` but sitting above the keepalive bullet). Success Criterion 1 (`:584`) plus the two references that retitle it (`:625`, `:739`) compound it: a validator instrumenting Redis commands on the hash key would red a correct build. | Round 4: all rewritten to the new status quo (no-VALUE-write / `updated_at`-immobility), with the `EXPIRE` named as the sole healthy-path command; Task 1's comment mandate scoped to the classification check | Per the repo's no-legacy-code rule these are rewritten, not annotated with history. The amplification claim is now quantified: a whole-row `HSET` plus an `on_save` pass over 89 fields (three `EVAL INDEX_SWAP_LUA` and a `ZADD` among them) collapses to one `EXPIRE` per healthy row. |
| CONCERN | Scope \& Value | (Round 4.) The TTL Success Criterion lived in the Success Criteria section only. Risk 4b leans on it as the one guard that can actually fail, but `restamp-tests` works from Test Impact and Task 5, so the guard would never have been written. | Round 4: threaded into the `tests/unit/test_session_health_phantom_guard.py` Test Impact entry and into Task 5's bullet list | Same class as the gates-that-cannot-fire finding (#2658): a criterion no agent is routed to is not a guard. |
| NIT | Scope \& Value | (Round 4.) The TTL criterion asserted against the literal `2592000`, which is env-overridable via `TIMEOUTS__AGENT_SESSION_RETAIN_TTL_S` (`models/agent_session.py:628-631`); specified no sleep duration, where Redis's one-second TTL granularity makes a short sleep produce no measurable decay and a flaky assert-decay step; and seeded one row, so it could pass with expiry half-active. | Round 4: assert against `AgentSession._meta.ttl`, mandate `time.sleep(1.5)`, seed one terminal and one non-terminal row | n/a (NIT) |
| NIT | Scope \& Value | (Round 4.) A builder is likely to truth-test the keepalive: `if not session.refresh_ttl(): logger.warning(...)`. `EXPIRE` returns `False` for a missing key, which is a legitimate no-op (row deleted concurrently, or stored key drifted from the computed one), so that emits false failures on a correct build. | Round 4: one Task 1 bullet forbidding it | n/a (NIT) |
| NIT | Scope \& Value | "Read-only" is imprecise in the one artifact meant to be durable. `popoto/models/base.py:829-839` performs `setattr(self, field_name, coerced)` during type coercion, so `is_valid()` mutates the in-memory instance; it is read-only with respect to Redis, which is the property that matters and the one the comment should name. Harmless in the sweep (instances are discarded), but the comment is what a future reader will trust. | Round 3: Research bullet 2 states the property as "read-only with respect to Redis"; Technical Approach step 1 and the Documentation inline bullet both forbid the word "read-only" in the comment | n/a (NIT) |

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
