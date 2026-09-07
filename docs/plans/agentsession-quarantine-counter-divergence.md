---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-07
tracking: https://github.com/tomcounsell/ai/issues/3199
last_comment_id:
---

# AgentSession quarantine counter goes blind under popoto 1.9.0's divergence guard

## Problem

`AgentSession.repair_indexes()` runs on every worker startup and every reflection tick. Its job is
to stop identity-less `AgentSession:*` hashes (rows with no `session_id`) from being re-SADDed into
the `$IndexF` sets on each rebuild — the phantom re-inflation leak from #2101, generalized to every
`IndexedField` in #2207. Alongside the fix it publishes a count of what it quarantined, both as
`AgentSession._last_quarantined_identityless` and as the Redis key
`agentsession:repair_indexes:last_quarantined_identityless`, which `python -m tools.doctor` reads
to append a drift note to its `agentsession-index-drift` check.

Since the popoto floor moved to 1.9.0 (`8c1a36ad1`, 2026-09-05) that count reads 0 no matter how
many phantom hashes are in the keyspace.

**Current behavior:**

popoto 1.9.0 added a divergence guard to `Model.rebuild_indexes()`. For every scanned row it
compares the stored Redis key against the key re-derived from the decoded values, and on a mismatch
it appends to `diverged_keys` and `continue`s — *before* the `field.on_save` loop
(`popoto/models/base.py:3272-3290`). An identity-less hash can never derive back to its stored key,
so it is skipped at the pre-check and our `_make_identityless_skip_shim`
(`models/agent_session.py:2476`) is never invoked. `quarantined[0]` stays 0 and
`cls._last_quarantined_identityless = quarantined[0]` (`:2528`) publishes a zero.

The leak itself is still prevented — popoto's guard now does what our shim used to do, and every
index-population assertion in the affected tests passes. What broke is purely the reporting: three
test nodes on `main` are red because they assert the counter is non-zero, and the doctor's drift
suffix has gone permanently silent. A keyspace filling up with phantom hashes now looks identical
to a clean one on every observability surface.

Reproduced at `de229ee46`:

```
./scripts/pytest-clean.sh tests/unit/test_agentsession_pending_index_leak.py \
    tests/unit/test_agentsession_index_guard_generalized.py -n0 -q
→ 3 failed, 10 passed
```

with popoto logging `AgentSession.rebuild_indexes() skipped 4 row(s) …` / `3 row(s)` / `5 row(s)`
against the three nodes' assertions of `>= 4`, `>= 3`, and `>= 15`.

**Desired outcome:**

`repair_indexes()` counts the rows popoto's divergence guard quarantined on its behalf, in addition
to the ones its own shim still catches, and reports a single de-duplicated per-pass count of
identity-less rows. The three red nodes go green, the doctor's drift suffix reports again, and the
three healthy-keyspace `== 0` assertions still bite.

## Freshness Check

**Baseline commit:** `de229ee46`
**Issue filed at:** 2026-09-06
**Disposition:** Unchanged

**File:line references re-verified:**

- `models/agent_session.py:2528` — issue claimed `cls._last_quarantined_identityless = quarantined[0]`
  is where the counter is set — still holds, exact line.
- `models/agent_session.py:2476` / `:2517` — shim factory and install loop — still hold.
- `tests/unit/test_agentsession_pending_index_leak.py:217,248,265` — the three `== 0`
  healthy-keyspace assertions — still hold, exact lines.
- `tools/doctor.py:1575-1601` (`_recent_quarantine_suffix`) and `:1604-1657`
  (`_check_agentsession_index_drift`) — reads the Redis key, informational only, never gates
  pass/fail — confirmed.
- `popoto/models/base.py:101-137` / `:3272-3280` / `:3327` — `RebuildIndexesResult`, the divergence
  pre-check, and the return — confirmed against the installed 1.9.0 in `.venv`.

**Cited sibling issues/PRs re-checked:**

- #3173 / PR #3180 — the aware-UTC datetime decode contract 1.9.0 introduced. Relevant to the
  *archive* half of #3199, not to this half; the quarantine trio fails on key divergence, which is
  a different 1.9.0 change on the same method.
- `8c1a36ad1` — the floor bump. Touched only `tests/conftest.py`,
  `test_conftest_isolation_guards.py`, and `test_job_model.py`, so the four nodes were never re-run
  against 1.9.0 before the floor moved. Confirmed.

**Commits on main since issue was filed (touching referenced files):**

- `3c77e1eab` AgentSession: one newest-wins resolver for every session_id read — irrelevant; does
  not touch `repair_indexes()`.
- `f65e05dfc` AgentSession `Meta.ttl` keepalive is the retention policy — irrelevant; retention
  only.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** The bug reproduces at the current HEAD, so the premise is live. The issue body's stated
hypothesis (popoto 1.9.0's "single hydration per `list()`") is wrong and is corrected in the issue's
Recon Summary — `rebuild_indexes()` never calls `list()`.

## Prior Art

- **PR #2102** (issue #2101): "Fix AgentSession pending-index phantom leak — A1 rebuild guard".
  Introduced `_make_identityless_skip_shim` on the `status` field's `on_save` for the duration of
  `rebuild_indexes()`, plus the `_last_quarantined_identityless` counter and
  `tests/unit/test_agentsession_pending_index_leak.py`. Succeeded — the leak stopped.
- **Issue #2207** (redis-phantom-agentsession-flood): generalized the shim from `status` alone to
  every `IndexedField` enumerated at runtime, and added
  `tests/unit/test_agentsession_index_guard_generalized.py`. Succeeded.
- **Durability plan #2494**: deleted the `claude_pid` `IndexedField`, leaving exactly three
  (`status`, `task_type`, `claude_session_uuid`). Relevant because
  `test_quarantine_count_sums_across_all_indexed_fields` hardcodes that `3`.
- **Issue #2536**: established the ordering constraint that `assert_popoto_floor()` must precede the
  `$IndexF` scan-and-delete. Unchanged by this work, but it is the reason the floor assertion sits
  where it does.

Neither prior fix failed. This is not a repeat attempt at the same bug — the leak fix still works;
an upstream change moved where the skip happens and left the counter reading the wrong seam.

## Research

No external research needed beyond the vendored dependency itself. The relevant "documentation" is
the installed popoto 1.9.0 source in `.venv/lib/python3.14/site-packages/popoto/models/base.py`,
read directly during recon:

- `RebuildIndexesResult(int)` at `base.py:101-137` — an `int` subclass equal to the indexed-row
  count, carrying `.diverged_keys` (list of stored key strings) and `.diverged_count`. Because it
  subclasses `int`, the existing `rebuilt_count = cls.rebuild_indexes()` assignment and the
  `(stale_count, rebuilt_count)` return tuple keep working with no change at any call site.
- The divergence guard at `base.py:3272-3280`, added for popoto #537/#538, with the rationale that
  indexing a row whose derived key disagrees with its stored key would point the index at a
  nonexistent hash — "strictly worse than leaving the row unindexed".
- `decode_popoto_model_hashmap(cls, redis_hash, source_redis_key=...)` — the decode helper
  `rebuild_indexes()` itself uses, available for re-deriving an instance from a diverged key.

No relevant external findings beyond the dependency source — proceeding with codebase context.

## Data Flow

1. **Entry point**: worker startup or a reflection tick calls `AgentSession.repair_indexes()`.
2. **`$IndexF` scan** (`models/agent_session.py`): counts stale members, deletes each whole index
   key. Produces `stale_count`. Unchanged by this work.
3. **Shim install**: a guarded `on_save` is installed on each of the three `IndexedField`s.
4. **`cls.rebuild_indexes()`** (popoto): `scan_iter` → `hgetall` → `decode_popoto_model_hashmap` →
   **divergence pre-check**. Identity-less rows exit here into `diverged_keys`; healthy rows fall
   through to the `on_save` loop, where the shim delegates to the original.
5. **Return**: `RebuildIndexesResult(count, diverged_keys)`.
6. **Counting (the seam this plan fixes)**: today only `quarantined[0]` (shim hits, now always 0) is
   read. After this change the diverged keys are re-decoded, identity-tested, and unioned with the
   shim's hits into one de-duplicated row set.
7. **Publication**: `cls._last_quarantined_identityless = <count>`, a WARNING log, and a `SET` on
   `agentsession:repair_indexes:last_quarantined_identityless` with a 7-day TTL.
8. **Output**: `python -m tools.doctor` reads that Redis key in a fresh process and appends
   "(most recent repair_indexes() quarantined N identity-less hash re-add(s))" to the
   `agentsession-index-drift` check message.

## Why Previous Fixes Failed

Not applicable in the usual sense — no prior fix for *this* symptom exists. The relevant history is
that #2102 and #2207 both coupled the observability counter to a specific internal seam of a
third-party rebuild loop (`field.on_save` being reached). That coupling was invisible until popoto
inserted a `continue` upstream of it.

| Prior Fix | What It Did | Why It Is Fragile Here |
|-----------|-------------|------------------------|
| PR #2102 | Shim on `status.on_save`, counting invocations | Counts a popoto-internal call site, not an outcome |
| #2207 | Generalized the shim to every `IndexedField` | Multiplied the same coupling by the field count, baking "3" into a test assertion |

**Root cause pattern:** the counter measured *how our workaround fired* rather than *what happened
to the rows*. The fix re-bases it on the row outcome, which both seams can report.

## Architectural Impact

- **New dependencies**: none. `RebuildIndexesResult` already ships in the pinned popoto 1.9.0 and
  the floor assertion already guarantees it.
- **Interface changes**: none externally. `repair_indexes()` keeps its `(stale_count,
  rebuilt_count)` 2-tuple arity; `_last_quarantined_identityless` keeps its name and `int` type.
  Its **unit** changes from "shim invocations across all indexed fields" to "identity-less rows
  quarantined", which is a semantic change one existing test asserts on directly.
- **Coupling**: net decrease. The counter stops depending on popoto reaching `field.on_save` and
  starts depending on a documented return value.
- **Data ownership**: unchanged.
- **Reversibility**: trivial — the change is confined to the counting block of one method plus its
  tests.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (scope is fully specified by the issue's Recon Summary)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| popoto >= 1.9.0 installed | `python -c "import popoto; from popoto.models.base import RebuildIndexesResult"` | The `.diverged_keys` signal this fix reads |
| Redis reachable for scoped tests | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | The affected tests are Redis-backed |

## Solution

### Key Elements

- **A row-scoped quarantine set** — `repair_indexes()` accumulates the *Redis keys* of identity-less
  rows rather than a bare invocation counter, so the same row seen through two different seams is
  counted once.
- **The divergence seam** — the keys popoto skipped are read off `RebuildIndexesResult.diverged_keys`
  and folded into that set. This is the seam that carries all the traffic under 1.9.0.
- **An identity filter on the diverged keys** — `diverged_keys` is wider than "identity-less"; it
  also collects healthy rows whose stored key disagrees with a re-derived one. Each diverged key is
  re-decoded and passed through `_filter_hydrated_sessions`, the same canonical identity test the
  shim uses, so only genuinely identity-less rows bump the counter.
- **The retained shim** — `_make_identityless_skip_shim` stays installed on every `IndexedField`.
  It is the second half of a two-path defence: a hypothetical identity-less row whose derived key
  *does* match its stored key would sail past popoto's pre-check and still needs skipping.
- **A counter unit stated in the docstring** — "identity-less rows quarantined this pass",
  de-duplicated, so the next reader is not left guessing whether 15 means five rows or fifteen.

### Flow

`repair_indexes()` → `$IndexF` scan-and-delete (`stale_count`) → install shims → popoto
`rebuild_indexes()` → **row diverged?** → yes → `diverged_keys` → re-decode → identity-less? → add
key to quarantine set; **no** → `on_save` → shim → identity-less? → add key to quarantine set →
restore shims → `_last_quarantined_identityless = len(quarantine set)` → WARNING log → Redis `SET`
→ doctor reads it in a later process.

### Technical Approach

- Replace the `quarantined = [0]` accumulator with `quarantined_keys: set[str]`. The shim adds the
  instance's Redis key (`getattr(model_instance, "_redis_key", None)`, falling back to
  `model_instance.db_key.redis_key`) instead of incrementing; if neither resolves, fall back to a
  synthetic unique token so an unkeyable row is still counted once.
- Capture the rebuild result as `result = cls.rebuild_indexes()`; keep `rebuilt_count = int(result)`
  so the returned 2-tuple stays exactly as it is today.
- Read `getattr(result, "diverged_keys", ()) or ()` — a `getattr` guard, not an `isinstance` check,
  so a future popoto that returns a plain `int` degrades to the shim-only path instead of raising.
- For each diverged key: `POPOTO_REDIS_DB.hgetall(key)`, `decode_popoto_model_hashmap(cls, h,
  source_redis_key=key)`, then `_filter_hydrated_sessions([instance])`. Empty result → identity-less
  → add the key. A decode that returns `None` or raises → treat as identity-less (it is certainly
  not a hydrated session) and add the key, with a `logger.debug`. This loop costs one round trip per
  diverged key and diverged keys are 0 in a healthy keyspace.
- Publish `len(quarantined_keys)` to `cls._last_quarantined_identityless`, to the WARNING log, and
  to the Redis key. The WARNING text changes from "N identity-less … re-add(s) across M
  IndexedField(s)" to a row-scoped phrasing naming both seams.
- Rewrite the docstring's "Returns" note and the A1 rebuild-guard paragraph to describe the
  two-seam arrangement and name popoto's divergence pre-check as the primary path under 1.9.0.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] The existing `try: … except Exception:` around the Redis `SET` (agent_session.py:2543-2550)
      stays non-fatal and stays covered — add a test that a raising `POPOTO_REDIS_DB.set` does not
      fail `repair_indexes()` and that `_last_quarantined_identityless` is still populated in memory.
- [ ] The new diverged-key re-decode loop must not be able to fail the repair. Wrap the per-key
      decode in its own `try`, count the key as identity-less on failure, and add a test that
      monkeypatches `decode_popoto_model_hashmap` to raise and asserts `repair_indexes()` still
      returns its 2-tuple with the diverged keys counted.

### Empty/Invalid Input Handling

- [ ] Empty keyspace: `test_empty_keyspace_returns_tuple_no_crash` already covers `diverged_keys`
      being empty and the counter being 0. Keep it, and confirm by mutation that it bites.
- [ ] A `RebuildIndexesResult` with no `.diverged_keys` attribute (hypothetical popoto downgrade):
      covered by the `getattr` fallback; add a test that monkeypatches `rebuild_indexes` to return a
      plain `int` and asserts no crash.
- [ ] A diverged key whose backing hash vanished between the scan and the re-decode: `hgetall`
      returns `{}` → counted as identity-less, no exception.

### Error State Rendering

- [ ] The user-visible surface is the doctor message. Add a test asserting that after a repair over
      an identity-less keyspace, `agentsession:repair_indexes:last_quarantined_identityless` holds a
      non-zero value and `tools.doctor._recent_quarantine_suffix()` renders a non-empty string.
      There is no existing test for either today.

## Test Impact

- [ ] `tests/unit/test_agentsession_index_guard_generalized.py::test_quarantine_count_sums_across_all_indexed_fields`
      — REPLACE. It asserts `>= n_ghosts * 3` on the premise that the counter accumulates per-field
      shim invocations. Under row-scoped counting five ghosts yield five, not fifteen. Rewrite it as
      a row-semantics test: assert the counter equals the seeded ghost count *and* that all three
      `$IndexF` sets (`status`, `task_type`, `claude_session_uuid`) stay clean — which is a stronger
      statement of the #2207 generalization than counter arithmetic ever was. Rename to
      `test_quarantine_counts_each_identityless_row_once_across_all_indexed_fields` and rewrite the
      docstring, which currently documents the old unit.
- [ ] `tests/unit/test_agentsession_pending_index_leak.py::test_repair_does_not_reinflate_from_identityless_hashes`
      — UPDATE (comment only). The `>= m_identityless` assertions at :95 and :101 are satisfied by
      row counting (4 diverged rows vs. `>= 4`). The inline comment explaining the lower bound as a
      "per-pass event count" describes the old unit and must be reworded.
- [ ] `tests/unit/test_agentsession_index_guard_generalized.py::test_task_type_index_does_not_reinflate_from_identityless_hashes`
      — no change. `>= 3` against 3 diverged rows.
- [ ] `tests/unit/test_agentsession_pending_index_leak.py::test_gone_hash_orphan_cleared_by_wholekey_rebuild` (:217),
      `::test_all_healthy_rebuild_no_quarantine` (:248),
      `::test_empty_keyspace_returns_tuple_no_crash` (:265)
      — no change, and each must be individually mutation-checked (see Step 4) to prove the new
      counting rule still drives them red.
- [ ] `tests/unit/test_agentsession_index_guard_generalized.py::test_shims_restored_after_repair_no_leak`,
      `::test_shims_restored_after_rebuild_indexes_raises`,
      `::test_reentrant_call_from_another_thread_is_a_noop`,
      `::test_all_indexed_fields_enumerated_at_runtime`
      — no change. The shim, the lock, and the restore invariant are all retained.
- [ ] New: `tests/unit/test_agentsession_index_guard_generalized.py` gains coverage for the Redis
      persistence and the doctor suffix, for a decode failure in the diverged-key loop, and for a
      `rebuild_indexes()` that returns a plain `int`.
- [ ] `tests/unit/test_session_archive.py` — NOT TOUCHED. See No-Gos.

## Rabbit Holes

- **Reimplementing popoto's rebuild loop.** Tempting once you see that the divergence guard skips
  our shim. Do not. The whole point of the shim design was to avoid owning that loop, and the
  return value already gives us everything we need.
- **"Fixing" the divergence so identity-less rows reach `on_save` again.** The divergence guard is
  correct upstream behaviour — indexing a row to a key with no hash behind it is worse than leaving
  it unindexed. Restoring the old path would trade a reporting bug for a data bug.
- **Cleaning up the identity-less hashes themselves.** `repair_indexes()` has never deleted the raw
  phantom hashes (Risk 4 in #2101's notes) and this plan does not start. Quarantine means "not
  indexed", not "removed".
- **Chasing `migrate_datetime_keys()` / `audit_datetime_keys()`.** popoto's warning recommends them.
  They are the remedy for the *archive* half's key-canonicalization drift, not for phantom hashes,
  and running them here would blur the two halves.
- **Preserving the `× 3` number to avoid editing a test.** Multiplying the row count by the indexed
  field count would keep every assertion green while reporting a quantity that never happened.

## Risks

### Risk 1: `diverged_keys` picks up healthy rows and inflates the drift signal
**Impact:** Once the sibling archive half lands, real production rows may diverge on datetime-key
canonicalization. Counting them as "identity-less" would make the doctor report phantom-hash drift
that does not exist, and would send an operator to the wrong remediation.
**Mitigation:** Every diverged key is re-decoded and identity-tested with `_filter_hydrated_sessions`
before it counts. A diverged-but-hydrated row is logged at `debug` and ignored by the counter. The
`test_all_healthy_rebuild_no_quarantine` node is the standing guard for this.

### Risk 2: Re-decoding diverged keys adds a round trip per key on a huge broken keyspace
**Impact:** `repair_indexes()` runs on every worker startup; a keyspace with hundreds of thousands
of diverged rows would add that many `HGETALL`s to a hot path.
**Mitigation:** Diverged keys are 0 in a healthy keyspace, and the `$IndexF` scan above already
pipelines at `batch_size=5000` precisely because this method must survive a bloated keyspace. If the
diverged list is large the same treatment applies — pipeline the `hgetall`s in batches of 5000
rather than issuing them one at a time.

### Risk 3: The counter's unit change confuses a future reader
**Impact:** Someone reads `_last_quarantined_identityless == 5` and, remembering the old docs,
divides by three.
**Mitigation:** The docstring, the WARNING message, the doctor suffix wording, and the renamed test
all state "rows" explicitly. `docs/features/agentsession-pending-index-leak.md` gets the same
correction.

### Risk 4: A future popoto drops or renames `.diverged_keys`
**Impact:** An `AttributeError` on a hot startup path.
**Mitigation:** `getattr(result, "diverged_keys", ()) or ()`. The floor assertion
(`assert_popoto_floor()`) already guards the lower bound; the `getattr` guards the upper.

## Race Conditions

### Race 1: Concurrent `repair_indexes()` calls clobbering each other's shim captures
**Location:** `models/agent_session.py`, the `install → rebuild → restore` block.
**Trigger:** Two threads calling `repair_indexes()` on the same class.
**Data prerequisite:** none new.
**State prerequisite:** exactly one live shim per `IndexedField`.
**Mitigation:** Pre-existing and unchanged — a per-class non-blocking `threading.Lock` guards the
whole sequence, plus a `RuntimeError` backstop if a shim is already installed. The new
`quarantined_keys` set is created inside that lock, so it inherits the same protection. Covered by
`test_reentrant_call_from_another_thread_is_a_noop`.

### Race 2: A diverged key's hash is deleted between popoto's scan and our re-decode
**Location:** the new diverged-key identity-filter loop.
**Trigger:** a TTL expiry or a concurrent `delete()` landing in that window.
**Data prerequisite:** none — the loop must tolerate a missing hash.
**State prerequisite:** none.
**Mitigation:** `hgetall` returning `{}` is treated as identity-less (a vanished row was certainly
not a hydrated session) and counted once. No exception, no retry.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3199] The archive half — `tests/unit/test_session_archive.py::test_restore_preserves_a_real_datetime_byte_identically`
  and any change to `agent/session_archive.py`. It is the aware-vs-naive datetime decode contract
  from #3173/PR #3180, a different 1.9.0 change, and it is already in flight as a separate
  direct-to-main hotfix on the same tracking issue. Touching it here would collide with that lane.
- [SEPARATE-SLUG #3199] Verifying that the nightly host runs popoto 1.9.0 and that the 2026-09-05
  nightly executed. That is a fleet-state question about a machine this lane cannot reach, and it
  stays on the tracking issue.
- [ORDERED] Closing #3199. The issue closes only when both halves have landed, so this PR says
  `Refs #3199`, never `Closes`.

## Update System

No update system changes required. The fix is a behaviour change inside an existing method on an
already-deployed model; no new dependency, config file, or migration is introduced. The popoto floor
that makes `RebuildIndexesResult` available is already asserted by `config/popoto_floor.py` and was
propagated by the `8c1a36ad1` bump.

## Agent Integration

No agent integration required. `repair_indexes()` is called internally by the worker startup path
and the reflection tick; its reporting surface is `python -m tools.doctor`, which is already a
registered CLI entry point the agent can invoke via Bash. No new `pyproject.toml` script, no MCP
wrapper, no bridge import.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/agentsession-pending-index-leak.md` — describe the two-seam quarantine
      (popoto's divergence pre-check plus the retained `on_save` shim) and state the counter's unit
      as de-duplicated identity-less rows per pass.
- [ ] Update `docs/features/agentsession-index-drift-detection.md` — correct the description of what
      the quarantine suffix counts.
- [ ] Check `docs/features/popoto-index-hygiene.md` for the same stale claim and correct it if
      present.
- [ ] `docs/features/README.md` index — verify the three entries above still describe their pages
      accurately; add no new page (this is a correction, not a new feature).

### External Documentation Site
Not applicable — this repo has no external documentation site.

### Inline Documentation
- [ ] Rewrite the "A1 rebuild guard" paragraph and the "Returns" note in the `repair_indexes()`
      docstring to name popoto 1.9.0's divergence pre-check as the primary quarantine seam.
- [ ] Update the module-level comment at `models/agent_session.py:68-78` describing the counter and
      its Redis key.

## Success Criteria

- [ ] `test_repair_does_not_reinflate_from_identityless_hashes`,
      `test_task_type_index_does_not_reinflate_from_identityless_hashes`, and the rewritten
      sum test all pass.
- [ ] The scoped run reports 0 failed and at least 13 passed — a "0 passed" summary line is a failed
      verification, not a pass.
- [ ] Each of the three `== 0` assertions at `test_agentsession_pending_index_leak.py:217`, `:248`,
      and `:265` is individually mutation-checked red and the evidence is pasted into the PR body.
- [ ] `agentsession:repair_indexes:last_quarantined_identityless` holds a non-zero value after a
      repair over a seeded identity-less keyspace, and `tools.doctor._recent_quarantine_suffix()`
      returns a non-empty string — asserted by a new test, not only by hand.
- [ ] `_make_identityless_skip_shim` is still installed on every `IndexedField` and still restored in
      the `finally`.
- [ ] `tests/unit/test_session_archive.py` and `agent/session_archive.py` are untouched by this PR.
- [ ] PR body says `Refs #3199`.
- [ ] Tests pass (`/do-test`, scoped)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (quarantine-counter)**
  - Name: `quarantine-counter-builder`
  - Role: Implement the row-scoped quarantine counting in `repair_indexes()` and update the affected
    tests and docstrings.
  - Agent Type: builder
  - Domain: Redis/Popoto data
  - Resume: true

- **Validator (mutation-check)**
  - Name: `quarantine-counter-validator`
  - Role: Run the scoped suite, perform the three individual mutation checks, and verify the doctor
    surface. Read-only on source.
  - Agent Type: validator
  - Resume: true

### Domain framing for the builder

Never write raw Redis ops against Popoto-managed keys. The `$IndexF` scan and the plain
`agentsession:repair_indexes:last_quarantined_identityless` key are the two sanctioned exceptions
already present in this method — the first because popoto's `rebuild_indexes()` does not enumerate
`$IndexF`, the second because it is not a Popoto-managed key. Add no others. Scoped test runs go
through `scripts/pytest-clean.sh`, never bare `pytest`, and never the full suite.

## Step by Step Tasks

### 1. Row-scoped quarantine counting

- **Task ID**: build-counter
- **Depends On**: none
- **Validates**: tests/unit/test_agentsession_pending_index_leak.py, tests/unit/test_agentsession_index_guard_generalized.py
- **Informed By**: the issue's Recon Summary (divergence pre-check at `popoto/models/base.py:3272-3280`)
- **Assigned To**: quarantine-counter-builder
- **Agent Type**: builder
- **Parallel**: false
- In `models/agent_session.py::repair_indexes()`, replace `quarantined = [0]` with
  `quarantined_keys: set[str] = set()`.
- Change `_make_identityless_skip_shim` to add the instance's Redis key to that set rather than
  increment, with a synthetic-token fallback when no key resolves. Keep every other property of the
  shim: the install-inside-try placement, the `finally` restore over the full field list, the
  re-entrancy `RuntimeError` backstop, and delegation to the original `on_save` for healthy rows.
- Capture `result = cls.rebuild_indexes()` and set `rebuilt_count = int(result)` so the returned
  2-tuple is byte-identical in shape.
- Fold in the divergence seam: `for key in getattr(result, "diverged_keys", ()) or ()`, re-decode via
  `decode_popoto_model_hashmap(cls, POPOTO_REDIS_DB.hgetall(key), source_redis_key=key)`, and add the
  key to `quarantined_keys` when `_filter_hydrated_sessions([instance])` is empty, when the decode
  returns `None`, when the hash is gone, or when the decode raises. Batch the `hgetall`s through a
  pipeline in chunks of 5000, matching the `$IndexF` scan above.
- Publish `len(quarantined_keys)` to `cls._last_quarantined_identityless`, to the WARNING log (row
  phrasing, naming both seams), and to the Redis key. Leave the Redis `SET` non-fatal.
- Rewrite the docstring's A1 paragraph, its "Returns" note, and the module comment at lines 68-78.

### 2. Test updates

- **Task ID**: build-tests
- **Depends On**: build-counter
- **Validates**: tests/unit/test_agentsession_index_guard_generalized.py
- **Assigned To**: quarantine-counter-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace `test_quarantine_count_sums_across_all_indexed_fields` per the Test Impact section: rename
  to `test_quarantine_counts_each_identityless_row_once_across_all_indexed_fields`, assert the
  counter equals `n_ghosts`, and assert all three `$IndexF` sets stay clean. Rewrite the docstring so
  it states the row unit.
- Reword the stale unit comment at `test_agentsession_pending_index_leak.py:88-93`.
- Add `test_quarantine_count_persisted_to_redis_key_for_doctor`: seed ghosts, repair, assert the
  Redis key is non-zero and that `tools.doctor._recent_quarantine_suffix()` returns a non-empty
  string containing the count.
- Add `test_diverged_key_decode_failure_is_counted_not_raised`: monkeypatch the decode helper to
  raise, assert `repair_indexes()` returns its 2-tuple and the diverged keys still counted.
- Add `test_plain_int_rebuild_result_degrades_to_shim_only`: monkeypatch `rebuild_indexes` to return
  a bare `int`, assert no `AttributeError`.
- Add `test_redis_persistence_failure_is_non_fatal`: monkeypatch the Redis `set` to raise, assert
  `repair_indexes()` still returns and the in-memory counter is populated.

### 3. Scoped test run

- **Task ID**: validate-suite
- **Depends On**: build-tests
- **Assigned To**: quarantine-counter-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `./scripts/pytest-clean.sh tests/unit/test_agentsession_pending_index_leak.py tests/unit/test_agentsession_index_guard_generalized.py -n0 -q`.
- Read the passed count off the summary line. A "0 passed" line is a failed verification. Record the
  exact summary line for the PR body.

### 4. Individual mutation checks on the three `== 0` assertions

- **Task ID**: validate-mutations
- **Depends On**: build-tests
- **Assigned To**: quarantine-counter-validator
- **Agent Type**: validator
- **Parallel**: false
- **Mutation A (all three).** In the working tree, add an unconditional
  `quarantined_keys.add("__mutation_probe__")` immediately before the counter is published. Run each
  of the three nodes as its own invocation:
  `test_agentsession_pending_index_leak.py::test_gone_hash_orphan_cleared_by_wholekey_rebuild`,
  `::test_all_healthy_rebuild_no_quarantine`, `::test_empty_keyspace_returns_tuple_no_crash`.
  Each must report `1 failed` on its own. Record all three summary lines. Revert the mutation.
- **Mutation B (:217 specifically).** Fold `stale_count` into the published counter. Only the
  gone-hash-orphan node seeds a stale `$IndexF` member, so this must turn `:217` red while `:248` and
  `:265` stay green — proving `:217` guards the "gone-hash orphans are not quarantine" boundary
  rather than merely "the counter happens to be 0". Record all three results. Revert.
- **Mutation C (:248 specifically).** Drop the `_filter_hydrated_sessions` identity filter from the
  diverged-key loop and force healthy rows to diverge by monkeypatching the derived key in a scratch
  run. `:248` must go red. If this mutation cannot be staged cleanly, substitute: remove the identity
  filter and seed a diverged-but-hydrated row, asserting `:248` catches it. Record the result.
  Revert.
- Confirm `git status --porcelain` is clean of mutation residue before finishing.

### 5. Doctor surface verification

- **Task ID**: validate-doctor
- **Depends On**: build-tests
- **Assigned To**: quarantine-counter-validator
- **Agent Type**: validator
- **Parallel**: false
- Beyond the new test, confirm by hand in a claimed test DB (never db 0, follow
  `tests/db_claim.py::redis_test_url()`): seed identity-less hashes, call `repair_indexes()`, read
  `agentsession:repair_indexes:last_quarantined_identityless`, and call
  `tools.doctor._recent_quarantine_suffix()`. Paste the rendered suffix into the PR body.
- Delete the seeded rows through the ORM afterwards, scoped by their test `project_key`.

### 6. Documentation

- **Task ID**: document-feature
- **Depends On**: validate-suite, validate-mutations, validate-doctor
- **Assigned To**: quarantine-counter-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Apply every item in the Documentation section.

### 7. Final validation

- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: quarantine-counter-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the Verification table and confirm all Success Criteria.
- Confirm the PR body carries `Refs #3199` and the three mutation-check summary lines.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Scoped suite green | `./scripts/pytest-clean.sh tests/unit/test_agentsession_pending_index_leak.py tests/unit/test_agentsession_index_guard_generalized.py -n0 -q` | exit code 0 |
| Run was not empty | `./scripts/pytest-clean.sh tests/unit/test_agentsession_pending_index_leak.py tests/unit/test_agentsession_index_guard_generalized.py -n0 -q 2>&1 \| grep -oE '[0-9]+ passed' \| grep -oE '^[0-9]+'` | output > 12 |
| Divergence seam wired in | `grep -c 'diverged_keys' models/agent_session.py` | output > 0 |
| on_save shim retained | `grep -c '_make_identityless_skip_shim' models/agent_session.py` | output > 1 |
| Identity filter applied to diverged keys | `grep -c '_filter_hydrated_sessions' models/agent_session.py` | output > 1 |
| Counter no longer a bare int accumulator | `grep -c 'quarantined = \[0\]' models/agent_session.py` | match count == 0 |
| Archive half untouched | `git diff --name-only origin/main...HEAD \| grep -c 'test_session_archive\|session_archive.py'` | match count == 0 |
| Lint clean | `python -m ruff check models/agent_session.py tests/unit/test_agentsession_pending_index_leak.py tests/unit/test_agentsession_index_guard_generalized.py` | exit code 0 |
| Format clean | `python -m ruff format --check models/agent_session.py tests/unit/test_agentsession_pending_index_leak.py tests/unit/test_agentsession_index_guard_generalized.py` | exit code 0 |
| No stale xfails introduced | `grep -rn 'xfail' tests/unit/test_agentsession_pending_index_leak.py tests/unit/test_agentsession_index_guard_generalized.py` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. The counter's unit changes from "shim invocations across all indexed fields" to "identity-less
   rows quarantined". Nothing outside the doctor suffix consumes it, and the doctor text is being
   updated with it — but if any dashboard or alert threshold is keyed to the old
   three-times-larger number, say so now.
2. `test_quarantine_count_sums_across_all_indexed_fields` is being rewritten rather than deleted,
   trading counter arithmetic for a direct assertion that all three `$IndexF` sets stay clean.
   That is a stronger guard on the #2207 generalization, but it does drop the only test that ever
   asserted the counter aggregates across fields. Acceptable?
