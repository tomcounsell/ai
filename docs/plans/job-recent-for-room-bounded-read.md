---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-17
tracking: https://github.com/tomcounsell/ai/issues/2636
last_comment_id: 5215674953
revision_applied: true
revision_applied_at: 2026-08-17T02:57:38Z
---

# Bound Job.recent_for_room via a direct bounded reverse-range read

## Problem

`Job.recent_for_room` (`models/job.py:346-358`) hydrates **every** Job in a Room and only then sorts and slices in Python. It runs on the bind-or-mint hot path (`bridge/job_router.py`) for every routed inbound message, so its cost grows linearly with a Room's lifetime Job count — and popoto's `QueryBuilder` executes its hydration pipeline twice (#2639), so the real "before" cost is `2N` hash reads for a top-5 answer. This is the sole named blocker on the #2494 phase-2 authoritative inbox cutover (`docs/plans/durability-room-job-agentrun.md`, M3 note).

A second, latent defect blocks any bounded fix: the sorted-set **scores are untrustworthy**. popoto 1.8.0 decodes datetimes without tzinfo, so a reloaded Job carries a naive `last_active_at`; any subsequent `save()` (via `mark_at_rest()` or `_write_goal_data()`) recomputes the score as `naive.timestamp()` = **local time**, skewing it by the host's UTC offset. Measured live: after `add_promise`, a `last_active_at__gte=now-1h` filter returned **0 rows** for a Job active seconds ago. A bounded read that trusts the stored score inherits this.

**Current behavior:** top-N answer costs `2N` hash reads; scores skew by UTC offset on every non-`touch()` re-save; skew is invisible only because the current implementation re-sorts uniformly-naive values in Python.

**Desired outcome:** `recent_for_room` costs `O(limit)` hash reads regardless of Room size, ordering is unchanged, scores are trustworthy (new writes correct, existing skew backfilled), and #2494 phase-2 is unblocked without waiting on an upstream popoto release.

## Freshness Check

**Baseline commit:** `4e78975d4abef0a6cab15da264754f637aeb23e4`
**Issue filed at:** 2026-08-07T05:51:23Z
**Disposition:** Minor drift (line numbers + upstream state moved; premise intact). One upstream premise **revised**: the issue's "upstream, then consume" sequencing is superseded by the in-repo bounded read (see Recon Summary on the issue; ratified by supervisor, critique cycle 1).

**File:line references re-verified (2026-08-17):**
- `models/job.py:239` → drifted to `models/job.py:346-358` under PR #2814 (expectations); hydrate-all-then-slice logic byte-identical in shape — still holds.
- `mark_at_rest()` now `:333`, `_write_goal_data()` now `:201` — both still call bare `save()`; **no `Job.save()` override exists**, so the tz-skew write paths are still live.
- popoto 1.8.1/1.8.2 source claims (unbounded `zrangebyscore`, KeyField-only early-limit gate) — verified at issue time against the pinned checkout; unchanged in any shipped release since.

**Cited sibling issues/PRs re-checked:**
- popoto#517 (bounded sorted-range read) + popoto#519 (score purity) — shipped in popoto **1.8.2** (2026-08-07).
- popoto#540 (`get_by_id` returns None; 40 tests red on 1.8.1+) — **CLOSED 2026-08-14** via popoto PR #547, but **UNRELEASED**: latest PyPI/tag is still 1.8.2. popoto main is 17 commits ahead of v1.8.2, including unrelated behavior changes (colon-escape self-escaping popoto#533, datetime-KeyField identity + migration popoto#548). The eventual floor bump is a larger, riskier upgrade than a one-defect wait — and no shippable release exists today.
- #2640 ($SortF orphans) — CLOSED: `rebuild_indexes()` on 1.8.0 does sweep `$SortF:*` partitions (issue's filed mechanism was false), so sorted-set orphans are transient until the daily repair, not permanent.
- #2639 (QueryBuilder double hydration) — OPEN; makes the "before" number `2N`, strengthens the case here; not in scope.
- #2494 — OPEN; phase-2 cutover still gated on this issue per `docs/plans/durability-room-job-agentrun.md:734`.

**Commits on main since issue was filed (touching referenced files):**
- `aa8015ba3` (#2814 expectations) — moved `recent_for_room`, renamed promise fields to expectations; irrelevant to the defect.
- `c8b2136b1` (#2671, closes #2640) — Job registered in the guarded index-repair sweep; establishes the daily `$SortF` reap the bounded read leans on.
- `8d2d445ec` (#2653) — scoped backfill write to avoid clobbering concurrent promise writes; the idiom the skew backfill must mirror.

**Active plans in `docs/plans/` overlapping this area:** `durability-room-job-agentrun.md` (M3 shipped-status note names this issue as the cutover gate — coordination signal only; that plan defers to this one for the fix).

## Prior Art

- **Issue #2634 / plan `completed/job-model-scaling-followups.md`**: parent scaling audit; item 3 split out into #2636 because it appeared to need an upstream release.
- **PR #2671 (closes #2640)**: Job index hygiene — guarded `repair_indexes()` with daily `$SortF` partition reap via `rebuild_indexes()`. The bounded read's orphan tolerance rides on this.
- **PR #2653 (`8d2d445ec`)**: scoped Job backfill write that re-reads immediately before saving to avoid clobbering concurrent writes.
- **`Job.backfill_open_expectations_index` (`models/job.py:629`)**: the structural clobber-proof idiom in the very file this plan edits — `fresh.save(update_fields=[...])` makes concurrent-write clobber impossible by construction. The skew backfill uses this pattern (field-scoped to `last_active_at`), not the timing-based #2653 window-narrowing alone.
- **`agent/memory_retrieval.py:110-122`**: production precedent for the exact idiom — derive the sorted-set key from popoto's own field API, bounded `zrevrange`, decode defensively, fail open.
- **popoto#517/#519 (in 1.8.2), popoto#540/#547 (fixed, unreleased)**: the upstream path exists but has no shippable release; consuming it is deferred (see No-Gos).

## Research

No WebSearch pass — the only external dependency is popoto, which we control and verified directly against its GitHub repo, PyPI release metadata, and the pinned local source (see Freshness Check). No third-party ecosystem patterns are involved.

## Spike Results

All spikes were run during issue recon (issue #2636 comments, 2026-08-07) against pinned popoto 1.8.0 and real Redis; re-confirmed applicable at plan time.

### spike-1: Bounded reverse-range + `get_many` on 1.8.0
- **Assumption**: a direct `zrevrange` + ORM re-hydration matches the current implementation without upstream changes
- **Method**: prototype
- **Finding**: 30 Jobs, limit=5 → hash reads drop 60→5; ordering identical at 7 and 30 Jobs; `Job.query.get_many(keys, skip_none=True)` exists on 1.8.0 (`popoto/models/query.py:1657`), pipelines hydration, preserves input order, drops gone-hash keys; empty room returns `[]` cleanly
- **Confidence**: high
- **Impact on plan**: this is the chosen implementation; no popoto release on the critical path

### spike-2: tz score skew
- **Assumption**: stored sorted-set scores can be trusted as UTC epochs
- **Method**: prototype (UTC+07 host)
- **Finding**: FALSE — reload decodes naive; `add_promise`/`mark_at_rest` re-save skews the score by exactly the host UTC offset (-25200s); `touch()` repairs it; hash field stays correct; skew is one-shot, machine-local, inconsistent across a mixed-offset fleet
- **Confidence**: high
- **Impact on plan**: a `Job.save()` UTC-reattach override plus a one-pass backfill are prerequisites for any bounded read

### spike-3: key layout
- **Assumption**: the sorted-set key can be constructed by hand
- **Method**: prototype
- **Finding**: prefix is `$SortF` (not `$SortedF`); `DB_key.clean()` escapes `:` and `/`, and every real `room_id` contains a colon — the key MUST be derived via `SortedField.get_sortedset_db_key(Job, "last_active_at", room_id).redis_key`; members are `Job:{id}:{clean(room_id)}` bytes; score is a float epoch
- **Confidence**: high
- **Impact on plan**: derivation-only key construction is a hard rule with an anti-criterion row

### spike-4: tie-break divergence
- **Assumption**: old and new implementations return the same set under equal timestamps
- **Method**: prototype
- **Finding**: FALSE for ties straddling the limit boundary — Python stable sort vs Redis reverse-lex pick different subsets; organic ties don't occur (microsecond `_now()`), only constant-stamped migration/test data
- **Confidence**: high
- **Impact on plan**: acceptance tests use distinct timestamps + tie-tolerant assertions; the backfill must never stamp a constant timestamp

## Data Flow

1. **Entry point**: inbound message → `bridge/job_router.py` bind-or-mint → `Job.recent_for_room(room_id, limit=5)`; also `tools/job_tool.py` (MCP surface).
2. **Key derivation**: `SortedField.get_sortedset_db_key(Job, "last_active_at", room_id).redis_key` — the per-Room partition key.
3. **Bounded read**: `zrevrange(key, 0, fetch_n - 1)` where `fetch_n = limit + overfetch` — returns at most `fetch_n` member keys (bytes), newest first.
4. **Hydration**: decode members → `Job.query.get_many(keys, skip_none=True)` — pipelined, order-preserving, drops keys whose hash vanished (transient orphans absorbed by over-fetch).
5. **Output**: truncate to `limit`, return `list[Job]` newest-first; any exception at any step logs a warning and fails open to `[]` (unchanged contract).

Write-side flow (the prerequisite): any `Job.save()` → override re-attaches UTC to a naive `last_active_at` (instant-preserving, idempotent) → popoto computes the sorted-set score from an aware datetime → scores stay pure UTC epochs.

## Architectural Impact

- **New dependencies**: none — popoto floor stays `>=1.8.0`; no new packages.
- **Interface changes**: none — `recent_for_room(room_id, *, limit)` signature, ordering, and fail-open contract unchanged.
- **Coupling**: adds a second in-repo consumer of the derive-key-then-bounded-read idiom (after `agent/memory_retrieval.py`). Accepted: the key is derived from popoto's own field API, never hand-built.
- **Data ownership**: unchanged — popoto still owns the sorted set; we add one read command it doesn't wrap and one write-time normalization it doesn't perform.
- **Reversibility**: high — the read is a drop-in method body swap; the save() override is additive and instant-preserving; the backfill only rewrites rows whose score already disagrees with their hash.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (ratify the in-repo-read decision; review migration safety)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| popoto pinned at 1.8.x (not 1.8.2+ floor) | `grep -c "popoto>=1.8.0" pyproject.toml` | The plan is built for 1.8.0 semantics; a concurrent floor bump would change them |
| Redis reachable | `.venv/bin/python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | Empirical tests and backfill run against real Redis |

## Solution

### Key Elements

- **`Job.save()` tz-normalization override**: re-attach UTC to a naive `last_active_at` before delegating to popoto — one choke point that makes every score write pure UTC. Instant-preserving and idempotent; deliberately NOT a re-stamp (re-stamping `_now()` would resurrect idle Jobs and break rest-by-age).
- **Bounded reverse-range read**: `recent_for_room` becomes derive-key → `zrevrange` top-`fetch_n` → `get_many(skip_none=True)` → truncate to `limit`. Over-fetch absorbs transient gone-hash orphans (permanent orphans don't exist post-#2640: the daily guarded repair reaps them).
- **One-pass skew backfill migration**: for each Job, compare the stored score against `bridge.utc.to_unix_ts(job.last_active_at)`; where they disagree beyond tolerance, re-read fresh and `save(update_fields=["last_active_at"])` (the override's guard fires — the field is named — and rewrites a correct score; the scoped write cannot clobber concurrent expectation writes by construction). Registered in `scripts/update/migrations.py::MIGRATIONS`, idempotent, never stamps a constant timestamp. Single unbounded pass, no resumable cursor — DECIDED on the measured population (92 Jobs / 0.03s full hydrate, shared production Redis, 2026-08-17).
- **Empirical acceptance harness**: hash-read counting by wrapping the pipeline's hash-read method (exact integers, load-stable) — the "measures unchanged while looking fixed" failure mode is structurally detected.

### Flow

Inbound message → job_router bind-or-mint → `recent_for_room` → **one** reverse-range read + **≤ fetch_n** hash reads → top-5 candidates → route/bind as today.

### Technical Approach

- **Read path** (`models/job.py::recent_for_room`): keep the classmethod signature and fail-open contract. Derive the partition key via `SortedField.get_sortedset_db_key(Job, "last_active_at", room_id).redis_key` — never f-string it (`DB_key.clean()` escaping; every real `room_id` contains a colon). `POPOTO_REDIS_DB.zrevrange(key, 0, fetch_n - 1)`, decode bytes members, `Job.query.get_many(keys, skip_none=True)`, truncate to `limit`. `fetch_n = limit + JOB_RECENT_OVERFETCH` with `JOB_RECENT_OVERFETCH` a named, env-overridable constant (provisional/tunable, default 5) per the magic-number convention. Do not re-fetch on under-fill: an under-filled result means the Room genuinely has fewer live Jobs, or a repair is mid-flight (fail-open posture, same exposure as today).
- **Write path** (`models/job.py::Job.save` override): if `self.last_active_at` is a naive `datetime`, `replace(tzinfo=UTC)` before `super().save(...)`. No other field needs it — `last_active_at` is the only SortedField on Job. Preserve `save()`'s signature/kwargs pass-through. **Scoped-save guard (critique):** run the reattach only `if update_fields is None or "last_active_at" in update_fields:` — a scoped save excluding the field (production case: `backfill_open_expectations_index`'s `save(update_fields=["has_open_expectations"])`, `models/job.py:629`, whose docstring guarantees it never writes `last_active_at`) must never touch the SortedField score path; a scoped save *naming* the field MUST still reattach.
- **Backfill** (`scripts/update/migrations.py`): iterate Jobs through the ORM in a **single unbounded pass — DECIDED**, no resumable cursor: the measured population is **92 Jobs total on the shared production Redis, full hydrate 0.03s** (2026-08-17, war-room measurement). Log the count each run. For each Job, read the stored score for its member in its Room partition; if `abs(score - to_unix_ts(job.last_active_at)) > 1.0`, re-read the row fresh and write with the **structural clobber-proof idiom**: `fresh.save(update_fields=["last_active_at"])` — a field-scoped write touching only `last_active_at` and its `$SortF` companion (never `goal`/`status`), which eliminates concurrent-expectation clobber by construction rather than by timing. Mirrors `backfill_open_expectations_index` (`models/job.py:629`); interlocks with the save()-override guard — the scoped save names `last_active_at`, so the tz-reattach runs. Idempotent: second run repairs 0. Per the addendum's Popoto-migration rule: registered in `MIGRATIONS`, recorded once in `data/migrations_completed.json`, no raw Redis writes — the only write is `instance.save()`.
- **Ordering parity**: new path returns the same order as the old for distinct timestamps (verified by spike-1 at N=7 and N=30). Tie behavior legitimately differs (spike-4) — tests use distinct timestamps.
- **Instrumentation for acceptance**: wrap/patch the redis client's hash-read method (as in the spike) in the test to count hash reads before/after; assert the bounded path's count is a function of `limit`, not of Room size. This defeats the named failure mode (a "fix" that measures unchanged).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `recent_for_room`'s fail-open `except` (currently `models/job.py:354`) survives the rewrite — test asserts `logger.warning` fires and `[]` returns when the derived-key read raises (e.g., patched client raising `ConnectionError`)
- [ ] Backfill migration: per-row failure logs and continues (one bad row must not abort the pass); test with one poisoned row

### Empty/Invalid Input Handling
- [ ] Empty Room (no sorted-set key) → `[]`, no exception — spike-1 confirmed at both steps; regression test
- [ ] `limit=0` → `[]` without issuing a hydration call
- [ ] Member whose hash is gone (transient orphan) → dropped by `skip_none`, result still filled from over-fetch window when live rows exist

### Error State Rendering
- [ ] No user-visible surface — callers (`job_router`, `job_tool`) already treat `[]` as "no candidates". Verify `job_tool.py` output for an empty result renders as today (no change expected).

## Test Impact

- [ ] `tests/unit/test_job_model.py` — UPDATE: existing `recent_for_room` tests must pass unchanged against the bounded implementation (ordering/contract parity is the point); audit any test that stamps identical `last_active_at` values across Jobs and give them distinct timestamps (spike-4 tie hazard)
- [ ] `tests/unit/test_job_model.py` — ADD: tz-purity regression (mint → reload → `add_expectation()` → stored score matches `time.time()` within tolerance; `last_active_at__gte=now-1h` finds the row), hash-read-count acceptance test, orphan-tolerance test, fail-open test
- [ ] `tests/unit/test_job_model.py` — ADD: scoped-save guard test — after mutating `last_active_at` to naive, a `save(update_fields=["has_open_expectations"])` leaves the stored `$SortF` score byte-unchanged (the override must not fire when the field is excluded)
- [ ] `tests/unit/test_migrations.py` — UPDATE (file exists): add skewed-row repair + idempotency (second pass repairs 0) + poisoned-row continuation

## Rabbit Holes

- **Upgrading popoto now.** 1.8.2 fails 40 unit tests (popoto#540); the fix is merged upstream but unreleased, and main carries 17 commits of unrelated behavior change (colon-escape rework, datetime-KeyField migration). Do not bump the floor in this plan under any circumstances.
- **Migrating `last_active_at` to `type=float` epoch seconds.** Correct in principle, but touches every reader, the `_EPOCH` bound, and the sort key — spike-2 already ruled it out in favor of the save() choke point.
- **Building a $SortF orphan reaper.** #2640 established the daily guarded repair already reaps them; over-fetch + `skip_none` covers the transient window. Don't build one.
- **Generalizing the bounded-read idiom into a shared helper.** Two call sites (memory_retrieval, job) with different field types (DecayingSortedField vs SortedField) and different failure postures — premature abstraction; revisit if a third caller appears.
- **Fixing #2639 (double hydration) here.** Separate issue, separate fix; the bounded read sidesteps it for this call site anyway.

## Risks

### Risk 1: Backfill clobbers a concurrent write (save() is full-row)
**Impact:** a promise/expectation appended between the backfill's read and its save is lost.
**Mitigation:** structural, not timing-based — the backfill writes `fresh.save(update_fields=["last_active_at"])`, a field-scoped write that cannot touch `goal`/`status` by construction (the `backfill_open_expectations_index` idiom, `models/job.py:629`); a fresh re-read before the save additionally keeps the written value current. Only rows with skewed scores are touched at all. Run order in `/update` puts the migration after the code deploy, so the override is already active when the backfill runs.

### Risk 2: Score-trust assumption is broken by a not-yet-backfilled peer machine
**Impact:** fleet machines share Redis; a peer still running pre-fix code re-skews rows after this machine's backfill, and the bounded read (which trusts scores) demotes a recently-active Job below the top-N.
**Mitigation:** skew is one-shot and bounded by UTC offset (hours, not unbounded); the previous implementation was equally wrong on the *filter* side (gte returned 0 rows) so this is strictly no worse. The migration is idempotent and runs on every machine's `/update`; the fleet converges within one update cycle. Note in the migration docstring that re-running is safe and expected.

### Risk 3: Bounded read during the daily `rebuild_indexes()` window
**Impact:** the repair deletes the partition key before reconstructing it; a read in that window sees an empty/partial set and bind-or-mint may mint a duplicate Job instead of binding.
**Mitigation:** identical exposure exists today (the current filter reads the same sorted set) — no regression. Duplicate-mint is by design tolerable (Jobs converge via recency; nothing is lost). Accept.

### Risk 4: `get_many` semantics drift on a future popoto upgrade
**Impact:** order preservation or `skip_none` behavior changes silently under a floor bump.
**Mitigation:** the ordering-parity and orphan-tolerance unit tests pin the observable contract; any future bump that breaks it turns red in this repo's suite, not in production.

## Race Conditions

### Race 1: backfill read → concurrent expectation write → backfill save
**Location:** new migration in `scripts/update/migrations.py`
**Trigger:** worker appends an expectation to a Job while the backfill iterates
**Data prerequisite:** the backfill must never write fields it did not repair
**State prerequisite:** override active before backfill runs (deploy order)
**Mitigation:** eliminated by construction — `fresh.save(update_fields=["last_active_at"])` writes only the repaired field and its `$SortF` companion, so a concurrent expectation write cannot be clobbered regardless of timing; fresh re-read keeps the written instant current; tolerance gate means untouched rows are never written at all

### Race 2: `zrevrange` vs `save()` on another process (score moves between range-read and hydration)
**Location:** `models/job.py::recent_for_room`
**Trigger:** a Job is touched between the range read and `get_many`
**Data prerequisite:** none — hydration is by primary key, so the row read is current even if its rank moved
**State prerequisite:** none
**Mitigation:** accept momentary rank staleness (identical to the current implementation's read-then-sort window); order among *returned* rows reflects the range-read instant, which is the same contract as today

## No-Gos (Out of Scope)

- [ORDERED] **popoto floor bump + upstream pushdown consumption** — blocked on a popoto release (>1.8.2) containing the popoto#540 fix (PR #547); cutting and validating that release is a human-gated event in the popoto repo. Switch-back disposition is explicit: when that release ships, bump the floor and **RETIRE the in-repo read** in favor of the upstream `.limit()` pushdown (issue #2636's comment thread holds the blocker ledger).
- [SEPARATE-SLUG #2639] **QueryBuilder double-hydration fix** — upstream popoto defect, filed and tracked separately; the bounded read sidesteps it for this call site.
- [SEPARATE-SLUG #2494] **The phase-2 authoritative inbox cutover itself** — this plan only removes its named blocker; the cutover ships under #2494's own plan.

## Update System

- The skew backfill is a **new migration** in `scripts/update/migrations.py`, registered in `MIGRATIONS` — it propagates through the existing `/update` machinery on every machine, runs once per machine, and records completion in `data/migrations_completed.json`. Safe to re-run (idempotent).
- No new dependencies, config files, or env vars beyond the named over-fetch constant (env-overridable with a safe default; no `.env.example` entry required since absence falls back to the default — add one only if we make it a real Settings field).
- No service-restart choreography beyond the standard post-merge `/update`.

## Agent Integration

No agent integration required — this is a model-internal change. Existing surfaces (`tools/job_tool.py` MCP tool and `bridge/job_router.py`) call `recent_for_room` through its unchanged signature and contract.

## Documentation

- [x] Update `docs/features/durability-model.md` — replace the description of `recent_for_room`'s range-scan with the bounded reverse-range read, and document the `save()` tz-normalization invariant (scores are pure UTC epochs) and the backfill migration
- [x] Update `docs/plans/durability-room-job-agentrun.md` (M3 note, line 734) — the "#2636 bounded ZREVRANGE" gate is satisfied; point at this plan
- [x] Docstrings: `recent_for_room` (derivation-only key rule, over-fetch rationale, fail-open contract), `Job.save` override (instant-preserving, why not re-stamp), migration function (idempotency, fleet convergence) — all three already adequate as shipped; no edits needed

## Success Criteria

- [x] Hash-read count for `recent_for_room` is a function of `limit` (≤ `limit + JOB_RECENT_OVERFETCH`), not Room size — asserted by the instrumented test at N≥30 (the "before" is `2N`)
- [x] Mutation check on the bound: the test fails if the range read is issued unbounded (e.g., asserting the counted reads at N=30/limit=5 stay < 2·limit+overfetch, far below 60)
- [x] Ordering parity with the old implementation on distinct timestamps (N=7 and N=30 cases)
- [x] tz regression: reload → expectation write → score matches wall clock within tolerance; `last_active_at__gte` filter finds the row (the spike-2 live failure becomes a green test)
- [x] Backfill migration repairs seeded skewed rows and is idempotent (second run: 0 repairs); its write is field-scoped (`update_fields=["last_active_at"]`)
- [x] Scoped-save guard: a save excluding `last_active_at` leaves the stored score byte-unchanged; a scoped save naming it still reattaches UTC
- [x] `recent_for_room` fail-open contract intact (warning logged, `[]` returned on read failure)
- [x] popoto floor unchanged at `>=1.8.0`
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (job-bounded-read)**
  - Name: job-read-builder
  - Role: `Job.save()` override + bounded `recent_for_room` + unit/acceptance tests
  - Agent Type: builder
  - Domain: Redis/Popoto data
  - Resume: true

- **Builder (skew-backfill)**
  - Name: backfill-builder
  - Role: migration function + registration + migration tests
  - Agent Type: builder
  - Domain: Redis/Popoto data
  - Resume: true

- **Validator (job-bounded-read)**
  - Name: job-read-validator
  - Role: verify success criteria, run verification table, confirm anti-criteria red-state proofs
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: durability-docs
  - Role: Documentation section tasks
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Save-path tz normalization + bounded read
- **Task ID**: build-bounded-read
- **Depends On**: none
- **Validates**: tests/unit/test_job_model.py
- **Informed By**: spike-1 (get_many on 1.8.0 confirmed), spike-2 (override is the choke point), spike-3 (derivation-only keys), spike-4 (distinct-timestamp tests)
- **Assigned To**: job-read-builder
- **Agent Type**: builder
- **Parallel**: true
- Add the `Job.save()` override (UTC re-attach on naive `last_active_at`, instant-preserving, signature-preserving) with the scoped-save guard: reattach only `if update_fields is None or "last_active_at" in update_fields:`
- Rewrite `recent_for_room` per Technical Approach (derived key, `zrevrange` top-`fetch_n`, `get_many(skip_none=True)`, truncate, fail open)
- Name `JOB_RECENT_OVERFETCH` as an env-overridable module constant with a provisional/tunable comment
- Add/adjust unit tests: ordering parity, tz regression, hash-read-count acceptance (instrumented), orphan tolerance, empty room, `limit=0`, fail-open, scoped-save guard (excluded field → score byte-unchanged; named field → reattach fires)

### 2. Skew backfill migration
- **Task ID**: build-backfill
- **Depends On**: build-bounded-read
- **Validates**: migration tests (see Test Impact)
- **Informed By**: spike-2 (skew signature), `backfill_open_expectations_index` idiom (field-scoped `save(update_fields=["last_active_at"])`, structural clobber-proofing), critique concern 3 (92 Jobs / 0.03s → single unbounded pass, no cursor)
- **Assigned To**: backfill-builder
- **Agent Type**: builder
- **Parallel**: false
- Migration function in `scripts/update/migrations.py`, registered in `MIGRATIONS`; ORM-only writes via `fresh.save(update_fields=["last_active_at"])`; tolerance-gated; per-row failure tolerant; never stamps constants; single unbounded pass logging the count
- Tests: seeded skew repaired, idempotent second pass, poisoned-row continuation

### 3. Validation
- **Task ID**: validate-all
- **Depends On**: build-bounded-read, build-backfill
- **Assigned To**: job-read-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; confirm success criteria; confirm anti-criterion red-state proofs are in the PR description

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: durability-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Execute the Documentation section checklist

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Job model tests pass | `scripts/pytest-clean.sh tests/unit/test_job_model.py -q` | exit code 0 |
| Lint clean | `python -m ruff check models/job.py scripts/update/migrations.py` | exit code 0 |
| Format clean | `python -m ruff format --check models/job.py scripts/update/migrations.py` | exit code 0 |
| No hand-built sorted-set key (anti-criterion, spike-3) | `grep -c 'SortF' models/job.py` | match count == 0 |
| Key derived via field API | `grep -c 'get_sortedset_db_key' models/job.py` | output > 0 |
| Floor unchanged (anti-criterion, Rabbit Hole 1) | `grep -c 'popoto>=1.8.0' pyproject.toml` | output > 0 |
| No raw Redis writes in migration (anti-criterion) | `grep -cE 'zadd\|zrem\|hset\|delete\(' scripts/update/migrations.py` | match count == 0 |
| Migration registered | `grep -c 'backfill_job_last_active_scores' scripts/update/migrations.py` | output > 0 |
| Bounded read present | `grep -c 'zrevrange' models/job.py` | output > 0 |

## Critique Results

Round 5 (re-critique of revision 114bf2db8, 2026-08-17). All 3 prior CONCERNs + 1 NIT verified genuinely closed by all three critics against both the revised plan text and the landed Task-1 code. Two new CONCERNs were raised and adjudicated by the war-room driver with direct evidence: one refuted (the pre-fix `recent_for_room` body — `git show 3081c2fa2~1:models/job.py` — filtered `last_active_at__gte=_EPOCH` through the same `$SortF` SortedField partition, so Risk 3's "identical exposure exists today" claim is factually correct), one downgraded to NIT (popoto's partial-save path runs `field.on_save` for every named field — `popoto/models/base.py:1176-1185`, `:1238-1247` — and `SortedFieldMixin.on_save` performs the `zadd` at `sorted_field_mixin.py:528`, so `save(update_fields=["last_active_at"])` does rewrite the `$SortF` score; the planned seeded-skew-repair + idempotency tests empirically detect any silent skip).

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| NIT | Risk & Robustness | No spike explicitly exercises a scoped save that *names* the SortedField; the migration test should assert the raw sorted-set score, not just hash-side repair. (Downgraded from CONCERN: driver verified against pinned popoto source that the scoped-save path zadds the `$SortF` companion — see round-5 note above.) | not required (NIT) — recommended for Task 2 tests | In the migration test, after the scoped save, assert `POPOTO_REDIS_DB.zscore(partition_key, member_key) == pytest.approx(bridge.utc.to_unix_ts(job.last_active_at), abs=1.0)` — checking the raw sorted-set score catches any future popoto scoped-save path that silently skips the SortedField companion write. |

