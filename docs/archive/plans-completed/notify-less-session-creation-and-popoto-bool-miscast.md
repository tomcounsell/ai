---
status: docs_complete
type: bug
appetite: Medium
owner: Valor
created: 2026-07-29
tracking: https://github.com/tomcounsell/ai/issues/2439
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-29T07:32:22Z
---

# Notify-less AgentSession creation + Popoto boolean string mis-cast

## Problem

Investigating why 48 bridge-watchdog crash-storm alert sessions sat `status="pending"`
for up to ~48h (never delivered to Telegram) surfaced two distinct, independent bug
classes.

**Current behavior:**

1. **Bug B — notify-less session creation.** `monitoring/bridge_watchdog.py::_alert_human_of_crash_storm()`
   constructs an `AgentSession` and calls `.save()` directly, bypassing the enqueue path
   (`_push_agent_session()`) that publishes a `valor:sessions:new` pubsub notify. Without
   the notify, the worker only picks the session up on its next periodic scan — and when
   that backstop is not ticking, the session strands indefinitely. Two other construct-and-save
   sites share the pattern (`circuit_health_gate.py`, `sustainability.py`).
2. **Bug A — Popoto string-boolean mis-cast.** Untyped Popoto `Field(default=False)` round-trips
   through Redis as the *string* `'False'`/`'True'`. `bool('False')` is `True` in Python
   (non-empty string), so naive `bool(getattr(obj, "field", False))` reads are wrong. Confirmed
   live: `requires_real_chrome`, `user_facing_routed`, `retain_for_resume` all store as `str`.
   The dashboard displayed `requires_real_chrome: true` for all 48 sessions (real value `'False'`),
   and `tools/valor_session.py:1514` has a genuine **logic-inversion** bug on `retain_for_resume`.

**Desired outcome:** (a) newly created alert/notification sessions are reliably picked up;
(b) the dead `session-liveness-check` reflection no longer implies false periodic coverage;
(c) every naive `bool()` read of an *untyped* Popoto string-boolean field is replaced with the
canonical `_truthy()` helper (drifted copies consolidated), including the logic-inversion bug;
(d) the live root cause of the 48h stall is identified and documented.

## Freshness Check

**Baseline commit:** 060e2f791113d1fd28f6f78c8a4080a03d0f9790
**Issue filed at:** 2026-07-29T06:01:52Z
**Disposition:** Unchanged

**File:line references re-verified (all still hold on current main):**
- `monitoring/bridge_watchdog.py:763-800` — `_alert_human_of_crash_storm()` constructs `AgentSession(session_type="teammate", ...)` + bare `.save()`, no notify — CONFIRMED at :789.
- `ui/data/sdlc.py:1164` — `requires_real_chrome=bool(getattr(session, "requires_real_chrome", False))` — CONFIRMED.
- `ui/data/sdlc.py:1177` — `user_facing_routed=bool(getattr(session, "user_facing_routed", False))` — CONFIRMED.
- `tools/valor_session.py:1514` — `retain = getattr(s, "retain_for_resume", False); if not retain: continue` — CONFIRMED (logic-inversion).
- `agent/session_pickup.py:76-90` — canonical `_truthy()` — CONFIRMED.
- `tools/valor_session.py:762-812` — `_publish_resume_notify()` (PR #2170) DOES publish create-path notify — CONFIRMED; the stale memory note claiming resume doesn't publish is already false.
- `agent/session_health.py:3670-3677` — out-of-process actuation guard (`VALOR_REFLECTION_WORKER==1` early-return) — CONFIRMED; `reflections/__main__.py:167` sets the env var.

**Cited sibling issues/PRs re-checked:** #1804, #1811, #2147/#2163, #2170, #2418 — all referenced for context; none change the current code paths. No new commits landed against any cited file since the issue was filed (a few hours ago). Commit `5d7845c3c "Fix human-alert notification sessions saving with no message body"` predates the issue and is adjacent (same watchdog alert path) — it fixed empty-body saves, not the notify gap.

**Commits on main since issue filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** none found for the watchdog / notify / Popoto-bool surface.

**Notes:** No drift. All line numbers accurate against baseline.

## Prior Art

- **#1804**: Standalone worker in bridge mode strands sessions on notify-listener miss — same symptom class (missed notify → stranded), different root cause. Established that a missed notify with no live backstop strands a session.
- **#1811**: Notify-listener NUMSUB self-check livelock (bytes-vs-str key) — a prior notify-pipeline defect, since fixed.
- **#2147 / PR #2163**: Test-suite notify isolation — introduced `notify_channel_for()` db-scoping that both the publisher and Bug B's fix must respect.
- **PR #2170 (#2143, #2165)**: Worker-hang forensics + resume-notify pickup — added `_publish_resume_notify()` in `tools/valor_session.py`, the exact reusable sync notify-publish helper Bug B needs.
- **#2418**: Bridge watchdog wedge-restart livelock (open) — same file as Bug B's stall source, different mechanism (restart-suppression vs alert-delivery). Not merged in here; noted for a shared watchdog review.

## Research

No relevant external findings — this is entirely internal (Popoto/Redis pubsub, reflection scheduler, dashboard). Proceeding with codebase context and live-system spikes.

## Spike Results

### spike-1: Does a Popoto `Field(type=bool)` field round-trip as a real bool, or as a string like untyped fields?
- **Assumption**: "Only untyped `Field(default=False)` mis-casts; typed `Field(type=bool)` may or may not escape the Redis string round-trip (recon flagged this as unverified)."
- **Method**: code-read + live prototype (`TelegramMessage.has_media` sampled from Redis).
- **Finding**: Typed `Field(type=bool)` round-trips as a **real Python `bool`** (`type(v).__name__ == 'bool'`, values `True`/`False`). Untyped `Field(default=False)` round-trips as **`str`** (`'True'`/`'False'`). Confirmed live on 8 `TelegramMessage` and 13 `AgentSession` records.
- **Confidence**: high (direct observation of stored values).
- **Impact on plan**: **Scope revision.** The `has_media` sites (`bridge/enrichment.py:69`, `agent/session_executor.py:1684`, `scripts/migrate_model_relationships.py`) read a *typed* bool → NOT buggy → **dropped from Bug A**. The `models/reflection.py` fields `auto_delete_after_run` (:93) and `dead_letter_escalated` (:117) are also **typed** (`Field(type=bool, default=False)`) → their reads (`models/reflection.py:287`, `ui/data/reflections.py:134-139`) are NOT correctness bugs → dropped from the mandatory fix, optionally left as-is. Bug A's real correctness fixes are the three untyped fields only: `requires_real_chrome`, `user_facing_routed`, `retain_for_resume`.

### spike-2: Is `_publish_resume_notify()` reusable as the shared notify-publish helper for Bug B's sites?
- **Assumption**: "Bug B sites can reuse an existing sync notify-publish helper rather than routing through the async `_push_agent_session()`."
- **Method**: code-read (`tools/valor_session.py:762-812`).
- **Finding**: `_publish_resume_notify(session)` is a plain synchronous, fail-quiet helper that computes `worker_key` from `session.worker_key` and publishes the identical create-path payload/channel via `POPOTO_REDIS_DB.publish` + `notify_channel_for()`. It is notify-only (does NOT spawn a worker loop locally — the worker's `_session_notify_listener` is the sole spawn owner), which makes it ownership-safe from any process (CLI, watchdog, reflection subprocess).
- **Confidence**: high.
- **Impact on plan**: Promote/rename it to a shared `publish_session_notify(session)` helper (single home) and call it after `.save()` at the Bug B construct-and-save sites. This leaves `_push_agent_session()`'s inline instant-wake publish (agent_session_queue.py:453-478) completely untouched — satisfying the "do not change instant-wake behavior" constraint.

### spike-3: Can the `session-liveness-check` reflection be safely "revived" to actuate out-of-process?
- **Assumption**: "The dead reflection could be fixed to provide real independent periodic coverage."
- **Method**: code-read (`agent/session_health.py:3660-3685`, `reflections/__main__.py:160-170`).
- **Finding**: The out-of-process no-op is **intentional and correct** per #2098/#2091. The health-check actuation branches key off process-local registries (`_active_workers`/`_active_sessions`) that are empty in any non-owning process; actuating there false-recovers live sessions and spawns competing workers (the confirmed #2091 double-owner race). Reviving actuation is therefore *unsafe by design*.
- **Confidence**: high.
- **Impact on plan**: The reflection cannot be "fixed to actuate." Correct resolution: **remove** the `session-liveness-check` reflection (structural no-op that advertises false coverage). A genuinely independent backstop, if wanted, must be a *read-only detector that ALERTS* (never actuates) — that is a separate design, not part of this bug fix. Bug B's notify-publish fix removes the actual root cause; the worker's in-process 300s loop remains the real backstop.

## Data Flow

**Bug B (stranded pending session):**
1. **Entry point**: `bridge_watchdog.py::_alert_human_of_crash_storm()` (out-of-process, launchd watchdog) detects a crash storm.
2. **Construct**: builds `AgentSession(session_type="teammate", project_key="valor", message_text=<alert>)`.
3. **Save**: `notification_session.save()` writes to Redis — record now `status="pending"`.
4. **Gap**: no `valor:sessions:new` notify is published → the worker's `_session_notify_listener` never wakes → pickup depends solely on the worker's in-process 300s health scan.
5. **Stall**: if that scan is not ticking for this `worker_key` (root cause to confirm live), the record sits `pending` indefinitely. **Fix**: publish the create-path notify right after `.save()`.

**Bug A (wrong dashboard display / logic inversion):**
1. **Storage**: untyped `Field(default=False)` stored as string `'False'`/`'True'`.
2. **Read**: `bool(getattr(session, "requires_real_chrome", False))` → `bool('False')` → `True` (wrong).
3. **Output**: `ui/data/sdlc.py` → `SessionView` → `ui/templates/_partials/session_modal_content.html:31,163` renders a wrong badge; `ui/app.py:756,776` leaks the raw string into `/dashboard.json`. `tools/valor_session.py:1514` inverts the retain fast-path. **Fix**: route each read through `_truthy()`.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|-----------------------|
| PR #2170 | Added `_publish_resume_notify()` for the resume→pending path | Fixed only the resume path; the construct-and-save alert/notification sites were never migrated to publish a notify. |
| `agent/session_pickup.py` `_truthy()` | Introduced the canonical string-bool canonicalizer for the BYOB gate | Applied only at the pickup site; the helper was then copy-drifted into two more files and never applied to the dashboard/valor_session read sites. |

**Root cause pattern:** correct primitives (`_publish_resume_notify`, `_truthy`) exist but were never applied uniformly across every site that needs them — each was fixed reactively at one call site, leaving siblings unpatched.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: one new shared helper `publish_session_notify(session)` (promoted from `tools/valor_session.py::_publish_resume_notify`); one consolidated `_truthy` import surface. `send_hibernation_notification` deduplicated from two copies to one.
- **Coupling**: *decreases* — the two drifted `_truthy` copies (`crash_signature.py`, `crash_recovery.py`) collapse to one import (only `crash_signature.py` is a gating duplicate; `crash_recovery.py` already imports the canonical with a no-op fallback — see C4); two `send_hibernation_notification` copies collapse to one.
- **Data ownership**: unchanged. No schema/field changes → **no Popoto migration** (read-path fixes only; the notify fix adds no field).
- **Reversibility**: high — all changes are localized read-path substitutions, one helper promotion, one reflection removal (a vault config edit).

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm scope revision from spike-1: typed-bool sites dropped; confirm reflection removal vs re-describe)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable (live root-cause + spikes) | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | Query stuck sessions and verify bool round-trip |
| Vault reflections.yaml present | `test -f ~/Desktop/Valor/reflections.yaml` | Re-enable circuit-health-gate; remove session-liveness-check |

## Solution

### Key Elements

- **`publish_session_notify(session)` shared helper**: promoted from `_publish_resume_notify`; the single ownership-safe, sync, fail-quiet way any process can wake the worker after a construct-and-save. `_publish_resume_notify` becomes a thin alias/call-through (no second copy).
- **Bug B site fixes**: call `publish_session_notify(session)` immediately after `.save()` at `bridge_watchdog.py:789`, `circuit_health_gate.py:103-108`, `sustainability.py:114-119`.
- **`send_hibernation_notification` dedup**: collapse the two copies (`reflections/agents/circuit_health_gate.py` and `agent/sustainability.py`) to one canonical definition + import.
- **Consolidated `_truthy`**: one canonical home (`agent/session_pickup.py::_truthy` stays canonical). **Gating:** `models/crash_signature.py` — a true inline duplicate — must import the canonical. **Best-effort (C4):** `reflections/crash_recovery.py` already imports the canonical with an `ImportError` fallback that is a functional no-op; tidying it is optional cleanup and NOT a gating requirement.
- **Bug A read-path fixes (untyped fields only)**: `_truthy()` at `ui/data/sdlc.py:1164,1177`, `ui/app.py:756,776`, and the logic-inversion fix at `tools/valor_session.py:1514`.
- **Remove the dead `session-liveness-check` reflection** (vault + `config/reflections.yaml`), since out-of-process actuation is unsafe by design (spike-3).
- **Re-enable `circuit-health-gate`** in the vault reflections.yaml once its notify-less path is fixed.
- **Live root-cause investigation task**: identify why the 48 sessions stalled (worker health-loop liveness during the window for `worker_key="valor"` teammate sessions); document findings in the PR.

### Flow

Crash storm detected → watchdog constructs alert AgentSession → `.save()` → **`publish_session_notify()`** → worker `_session_notify_listener` wakes → session runs → Telegram alert delivered.

### Technical Approach

- Promote `_publish_resume_notify` body to `publish_session_notify(session)` in a shared home (recommend `agent/session_notify.py` or an exported function in `agent/agent_session_queue.py` alongside `notify_channel_for`). Keep `_publish_resume_notify` as a call-through to avoid a second copy (NO LEGACY duplication).
- Do **not** touch `_push_agent_session()`'s inline publish (agent_session_queue.py:453-478) — the instant-wake path for Telegram-originated sessions is unchanged. Bug B's fix only adds notify to the *construct-and-save* sites that lack it.
- `session_archive.py:443` (`_rehydrate_row()`): verify — it runs once at cold-start before the startup pending-scan, so it is likely self-healing. Add a notify only if live inspection shows a gap; otherwise document why it's safe and leave it (avoid scope creep).
- Typed-bool sites (`has_media`, reflection `auto_delete_after_run`/`dead_letter_escalated`): spike-1 proved safe — do NOT modify (documented in Spike Results). This is a deliberate scope revision of the issue's initial site list.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `publish_session_notify` is fail-quiet (`except Exception: logger.warning`) by design (a publish failure must never fail the save/resume; the health scan is the net). Test asserts it logs a warning and does not raise when Redis publish fails.
- [ ] `_alert_human_of_crash_storm` wraps everything in `try/except` and logs — test asserts the notify is published on the success path and that a publish failure is swallowed (alert save still succeeds).

### Empty/Invalid Input Handling
- [ ] `_truthy(None)`, `_truthy('')`, `_truthy('  ')`, `_truthy('False')`, `_truthy('True')`, `_truthy(True/False)`, `_truthy(0/1)` — table test asserting exact bool outputs (canonical helper already has this behavior; assert it holds after consolidation).
- [ ] `publish_session_notify` with a session missing `chat_id`/`project_key` (the teammate alert case) — asserts worker_key derivation still produces a valid payload.

### End-to-End Channel Coverage (C5)
- [ ] One end-to-end pubsub test: subscribe to `notify_channel_for(session)` on `POPOTO_REDIS_DB`, call `publish_session_notify(session)`, and assert a message is received on that exact channel. This proves the promoted publisher's channel derivation stays in lockstep with the channel the worker's `_session_notify_listener` subscribes to — a regression here (e.g. a db-scoping mismatch, cf. #2147/#2163) would silently re-strand sessions even with the notify "fixed".

### Error State Rendering
- [ ] Dashboard: a session with stored `requires_real_chrome='False'` renders the badge as *off* (not on) in `/dashboard.json` and the modal partial.

## Test Impact

- [ ] `tests/unit/test_session_pickup.py` (or wherever `_truthy` is tested) — UPDATE: point consolidated imports; assert `crash_signature`/`crash_recovery` now use the shared helper.
- [ ] `tests/unit/` dashboard/session-view tests reading `requires_real_chrome`/`user_facing_routed` — UPDATE: assert string `'False'` renders as boolean `False`.
- [ ] `tools/valor_session.py` release-by-PR tests — UPDATE/ADD: assert a `retain_for_resume='False'` record is correctly skipped by the fast-path `continue` (guards the logic-inversion fix).
- [ ] `tests/` reflection tests for `circuit-health-gate` / `sustainability` hibernation notify — ADD: assert `publish_session_notify` is called after save.
- [ ] `tests/` notify-channel end-to-end test — ADD (C5): assert `publish_session_notify(session)` publishes on `notify_channel_for(session)` (publisher/listener channel parity).
- [ ] Any test asserting `session-liveness-check` reflection is registered — UPDATE/DELETE: reflection removed.
- [ ] Grep for tests referencing the two `send_hibernation_notification` copies — UPDATE: single definition.

## Rabbit Holes

- **Converting untyped `Field(default=False)` → typed `Field(type=bool)` at the model layer.** Tempting (it would fix the root of Bug A), but existing stored records already hold string values, so it needs a data migration to re-cast them and unverified read-time coercion behavior. The contained read-path `_truthy()` fix is the issue's prescribed approach. Defer the field-type migration (see No-Gos).
- **Rewriting `_push_agent_session()` to be the single enqueue path for all construct-and-save sites.** The watchdog runs out-of-process, synchronously, with no event loop and none of the Telegram-shaped args `_push_agent_session` expects. The sync `publish_session_notify` helper is the right shape; do not async-ify the watchdog.
- **Building a new independent out-of-process periodic backstop.** Reviving actuation is unsafe (#2091); a read-only alerter is a separate design. Don't build it here.
- **Chasing #2418 (watchdog wedge-restart livelock).** Same file, different mechanism — leave it to its own issue.

## Risks

### Risk 1: The live 48h-stall root cause is something other than the missing notify (e.g., worker was down / recovery paused).
**Impact:** Bug B's notify fix would be necessary-but-insufficient; sessions could still strand if the worker loop is dead.
**Mitigation:** The live root-cause investigation task is a gating deliverable (AC #1). If the worker/backstop was the real gap, document it and confirm the in-process 300s loop is the sole real backstop; the notify fix still removes the primary dependency on that backstop for these sites.

### Risk 2: Consolidating `_truthy`/`send_hibernation_notification` introduces an import cycle.
**Impact:** Import error at startup.
**Mitigation:** Keep the shared helpers in low-dependency modules (`agent/session_pickup.py` already houses `_truthy`; put `publish_session_notify` next to `notify_channel_for`). Run the full import smoke (`python -c "import ..."`) and unit suite.

### Risk 3: Removing `session-liveness-check` leaves a dashboard/reflection-registry reference dangling.
**Impact:** Dashboard shows a missing reflection or errors.
**Mitigation:** Remove from both the vault `reflections.yaml` and `config/reflections.yaml`, and grep for the callable name across `ui/` before committing.

## Race Conditions

### Race 1: notify published before the record is durably saved.
**Location:** Bug B sites, ordering of `.save()` vs `publish_session_notify()`.
**Trigger:** Worker receives notify and scans before the Redis write is visible.
**Data prerequisite:** The `AgentSession` record must be fully written to Redis before the notify fires.
**State prerequisite:** `save()` completes (synchronous Redis write) before publish.
**Mitigation:** Call `publish_session_notify(session)` strictly *after* `session.save()` returns — mirrors the create path (`_push_agent_session` publishes after the record write). The worker's scan is also idempotent (re-scans `status="pending"`), so a spurious early notify only triggers a harmless empty scan.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2418] Bridge-watchdog wedge-restart livelock — same file, distinct mechanism (restart suppression vs alert delivery); tracked separately.
- [DESTRUCTIVE] Converting untyped `Field(default=False)` boolean fields to typed `Field(type=bool)` at the model layer + the data migration to re-cast existing string values in Redis. Out of scope — this plan uses the contained read-path `_truthy()` fix. A field-type migration touches hot Redis records and needs review-before-execute.
- [DESTRUCTIVE] Bulk-clearing or reprocessing the existing ~48 stranded `pending` alert sessions in production Redis. The fix prevents new strands; draining the historical backlog is a separate, review-gated operational step.

## Update System

No `/update` script changes required for the code fixes. Two vault config edits (not code) are operational steps performed on the bridge/worker machine: remove `session-liveness-check` and re-enable `circuit-health-gate` in `~/Desktop/Valor/reflections.yaml` (synced to `config/reflections.yaml`). `install_reflection_worker.sh` already handles the sync; no new update wiring needed.

## Agent Integration

No agent integration required — this is bridge/worker/dashboard-internal. No new CLI entry point, no new MCP tool, no new bridge import. The watchdog and reflections already run in-process; the fix only changes what they do after `.save()`.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-self-healing.md` — note that watchdog alert sessions now publish a session-notify on creation (reliable pickup).
- [ ] Update `docs/features/subconscious-memory.md` or the reflections feature doc — remove `session-liveness-check` and explain why out-of-process actuation is unsafe (#2098/#2091), pointing to the worker's in-process 300s loop as the sole backstop.
- [ ] Add/point to a note on the typed-vs-untyped Popoto bool distinction (spike-1 finding) — reconcile the existing `reference_popoto_bool_storage` memory to record that *typed* `Field(type=bool)` round-trips as a real bool.

### External Documentation Site
- [ ] N/A — no external docs site pages affected.

### Inline Documentation
- [ ] Docstring on `publish_session_notify` explaining it is the shared, ownership-safe, notify-only wake helper for construct-and-save sites.
- [ ] Comment at each consolidated `_truthy` import site.

## Success Criteria

- [ ] Live root cause of the 48h stall identified and documented in the PR (AC #1).
- [ ] `_alert_human_of_crash_storm`, `circuit_health_gate.send_hibernation_notification`, and `sustainability.send_hibernation_notification` publish a session-notify after `.save()` via the shared helper (AC #2).
- [ ] `session-liveness-check` reflection removed (vault + config), dashboard no longer implies false coverage (AC #3).
- [ ] Untyped string-bool reads (`requires_real_chrome`, `user_facing_routed`, `retain_for_resume`) use `_truthy()`; the 2 drifted copies consolidated to one import (gating: `crash_signature.py`; best-effort: `crash_recovery.py`, which already has a no-op fallback — C4); the `tools/valor_session.py:1514` logic-inversion fixed (AC #4).
- [ ] `circuit-health-gate` re-enabled in vault reflections.yaml (AC #5).
- [ ] Stale resume-notify memory note confirmed against current code (resume already publishes) and corrected/removed (AC #6).
- [ ] `send_hibernation_notification` exists in exactly one place; grep confirms no second definition.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (bug-b-notify)**
  - Name: notify-builder
  - Role: Promote `publish_session_notify`, wire it into the 3 construct-and-save sites, dedup `send_hibernation_notification`.
  - Agent Type: builder
  - Domain: async/concurrency, Redis/Popoto data
  - Resume: true

- **Builder (bug-a-truthy)**
  - Name: truthy-builder
  - Role: Consolidate `_truthy` to one import; fix untyped-field reads + the logic-inversion; leave typed-bool sites per spike-1.
  - Agent Type: builder
  - Domain: Redis/Popoto data
  - Resume: true

- **Builder (reflection-cleanup)**
  - Name: reflection-builder
  - Role: Remove `session-liveness-check`; re-enable `circuit-health-gate`; reconcile the resume-notify memory note.
  - Agent Type: builder
  - Resume: true

- **Investigator (live-root-cause)**
  - Name: rootcause-investigator
  - Role: Query the stranded sessions live, determine why the backstop didn't drain them, document.
  - Agent Type: general-purpose
  - Resume: true

- **Validator**
  - Name: plan-validator
  - Role: Verify all success criteria + Verification table.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Live root-cause investigation
- **Task ID**: investigate-stall
- **Depends On**: none
- **Assigned To**: rootcause-investigator
- **Agent Type**: general-purpose
- **Parallel**: true
- Query stranded `status="pending"` teammate/alert sessions (`worker_key="valor"`), inspect `created_at`/`updated_at` spread.
- Determine whether the worker's in-process 300s health loop was ticking during the stall window; check `_ensure_worker`/per-worker_key queue-loop registry gaps.
- Document findings in the PR body (AC #1).

### 2. Promote shared notify helper + wire Bug B sites
- **Task ID**: build-bug-b
- **Depends On**: none
- **Validates**: tests for hibernation notify + watchdog alert notify (create)
- **Informed By**: spike-2 (`_publish_resume_notify` is reusable), spike-1
- **Assigned To**: notify-builder
- **Agent Type**: builder
- **Parallel**: true
- Promote `_publish_resume_notify` → `publish_session_notify(session)` in a shared home; make `_publish_resume_notify` a call-through.
- Call `publish_session_notify(session)` after `.save()` at `bridge_watchdog.py:789`, `circuit_health_gate.py:103-108`, `sustainability.py:114-119`.
- Deduplicate `send_hibernation_notification` to one definition + import.
- Verify (do not modify unless a gap is proven) `session_archive.py:443`.
- Do NOT touch `_push_agent_session()`'s inline publish.

### 3. Consolidate `_truthy` + fix untyped-bool reads
- **Task ID**: build-bug-a
- **Depends On**: none
- **Validates**: dashboard/session-view bool tests, valor_session release fast-path test
- **Informed By**: spike-1 (typed sites are safe → dropped)
- **Assigned To**: truthy-builder
- **Agent Type**: builder
- **Parallel**: true
- Consolidate `models/crash_signature.py`'s inline `_truthy` copy to import the canonical `agent/session_pickup.py::_truthy` (gating). `reflections/crash_recovery.py` already imports the canonical with a no-op `ImportError` fallback — tidy it only as best-effort cleanup, not required (C4).
- Apply `_truthy()` at `ui/data/sdlc.py:1164,1177`, `ui/app.py:756,776`.
- Fix the `tools/valor_session.py:1514` logic-inversion (`if not _truthy(retain): continue`).
- Leave typed-bool sites (`has_media`, reflection fields) unchanged; add a one-line comment citing spike-1.

### 4. Reflection cleanup + memory reconciliation
- **Task ID**: build-reflection
- **Depends On**: build-bug-b
- **Assigned To**: reflection-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove `session-liveness-check` from vault `reflections.yaml` + `config/reflections.yaml`; grep `ui/` for dangling references.
- Re-enable `circuit-health-gate` (now that its notify path is fixed).
- **Live reload/verify (C3):** after editing the yaml, run `./scripts/install_reflection_worker.sh` to reload the live scheduler subprocess, then `python -m reflections --dry-run` and confirm `session-liveness-check` is absent and `circuit-health-gate` is present in the printed registry. Config edits do not take effect until the subprocess reloads — the dry-run is the durable proof.
- Confirm the resume-notify memory note against current code; correct/remove it.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-bug-a, build-bug-b, build-reflection, investigate-stall
- **Assigned To**: reflection-builder (or documentarian)
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-self-healing.md` and the reflections doc; record the typed-vs-untyped Popoto bool distinction.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: all previous
- **Assigned To**: plan-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; confirm all success criteria.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Watchdog alert publishes notify | `grep -n "publish_session_notify" monitoring/bridge_watchdog.py` | output contains publish_session_notify |
| Hibernation notify wired | `grep -rn "publish_session_notify" agent/sustainability.py reflections/agents/circuit_health_gate.py` | output contains publish_session_notify |
| `_truthy` consolidated (gating: crash_signature) | `grep -n "def _truthy" models/crash_signature.py` | match count == 0 |
| Untyped-bool sites fixed (data layer) | `grep -n "bool(getattr(session, \"requires_real_chrome\"" ui/data/sdlc.py` | match count == 0 |
| Untyped-bool sites fixed (app.py, C2) | `grep -n "bool(getattr(.*requires_real_chrome\|bool(getattr(.*user_facing_routed" ui/app.py` | match count == 0 |
| Logic-inversion fixed | `grep -n "_truthy(getattr(s, \"retain_for_resume\"\|_truthy(retain)" tools/valor_session.py` | output contains _truthy |
| session-liveness-check removed | `grep -rn "session-liveness-check" config/reflections.yaml` | exit code 1 |
| send_hibernation_notification single definition | `grep -rn "def send_hibernation_notification" agent/ reflections/ \| wc -l` | output contains 1 |
| Instant-wake path untouched | `grep -n "Publish notification so the standalone worker picks up immediately" agent/agent_session_queue.py` | output contains Publish notification |

## Critique Results

**Verdict:** READY TO BUILD (WITH CONCERNS) — 0 blockers, 6 non-gating concerns. All six are embedded below as Implementation Notes and folded into the Solution / Test / Verification sections.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| Concern | Correctness | C1: The modal template `ui/templates/_partials/session_modal_content.html` reads `requires_real_chrome`/`user_facing_routed`. | Task 3 (no template edit) | The template consumes a pre-`_truthy()`'d `SessionView` (the coercion happens upstream in `ui/data/sdlc.py:1164,1177`), so the badge renders correctly once the data-layer reads are fixed. **No template change is needed** — do NOT add a fix task for the partial. This is a clarifying note, not new scope. |
| Concern | Verification | C2: The `ui/app.py:756,776` untyped-bool fix had no grep verification row. | Verification table | Added a Verification row asserting the raw `bool(getattr(...))` reads at `ui/app.py:756,776` are gone (routed through `_truthy()`). |
| Concern | Operational | C3: Task 4 removes/re-enables reflections but never reloads the live scheduler, so config edits wouldn't take effect until the next restart. | Task 4 | Added a live reload/verify step: run `./scripts/install_reflection_worker.sh` (reloads the subprocess) then `python -m reflections --dry-run` to confirm `session-liveness-check` is gone and `circuit-health-gate` is registered. |
| Concern | Scope | C4: `_truthy` consolidation over-scoped `reflections/crash_recovery.py`. | Solution / Task 3 / Verification | `crash_recovery.py` already imports the canonical `_truthy` with an `ImportError` fallback that is a functional no-op — consolidating it is optional cleanup, NOT gating. Only `models/crash_signature.py` is a true inline duplicate that must be consolidated. Verification gates on `crash_signature.py` only; `crash_recovery.py` cleanup is best-effort. |
| Concern | Test coverage | C5: No end-to-end test proved the promoted `publish_session_notify()` publishes on the channel the worker listens on. | Failure Path Test Strategy / Test Impact | Added one end-to-end pubsub test: subscribe to `notify_channel_for(session)`, call `publish_session_notify(session)`, assert a message arrives on that exact channel (guards against a channel-derivation regression between publisher and listener). |
| Concern | Prose accuracy | C6: Plan prose said "3 drifted copies" of `_truthy`. | Solution / Success Criteria / Architectural Impact | Corrected to **2** drifted copies (`crash_signature.py` + `crash_recovery.py`); the canonical `agent/session_pickup.py::_truthy` is the source, not a drifted copy. |

---

## Resolved Questions (settled at critique)

1. **Reflection resolution**: Remove `session-liveness-check` entirely (spike-3: out-of-process actuation is unsafe by design). The worker's in-process 300s loop is the sole backstop; a read-only alerter is deferred to a separate design, not required to fix either bug.
2. **`session_archive.py:443` (`_rehydrate_row`)**: Kept out of the notify fix (self-healing at cold start); add a notify only if the live investigation proves a gap.
3. **Typed-bool sites scope revision**: Confirmed — `has_media` and the reflection typed fields are NOT buggy (spike-1) and are dropped from Bug A. Fix real bugs only; no defensive pass on typed sites.
