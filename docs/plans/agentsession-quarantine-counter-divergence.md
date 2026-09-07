---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-07
tracking: https://github.com/tomcounsell/ai/issues/3199
last_comment_id: 5564168421
revision_applied: true
revision_applied_at: 2026-09-07T02:31:18Z
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
  `tests/unit/test_conftest_isolation_guards.py`, and `tests/unit/test_job_model.py`, so the four
  nodes were never re-run against 1.9.0 before the floor moved. Confirmed.

**Commits on main since issue was filed (touching referenced files):**

- `3c77e1eab` AgentSession: one newest-wins resolver for every session_id read — irrelevant; does
  not touch `repair_indexes()`.
- `f65e05dfc` AgentSession `Meta.ttl` keepalive is the retention policy — irrelevant; retention
  only.

**Active plans in `docs/plans/` overlapping this area:** none.

**Issue comments incorporated:** comment `5563793165` (2026-09-07, from the nightly triage that
closed #3203/#3204/#3205/#3207 into this issue). It independently reaches the same mechanism this
plan's recon found — the divergence pre-check at `.venv/lib/python3.14/site-packages/popoto/models/base.py` ~:3270 `continue`s before
`field.on_save`, so the shim never runs and the persisted doctor key is written as 0 on every pass
while the phantom condition is live. It also settles two open threads: the nightly host **does**
carry popoto 1.9.0 and **did** run on 2026-09-06 at `25e4df925` (the 09-05 gap was a one-night miss,
not a stale install), and the repo-wide audit of naive `updated_at` comparisons belongs to #3181,
not here.

Comment `5564168421` (2026-09-07, the CRITIQUE escalation notice) adds no new technical premise. It
restates round 2's blocker and names the subtractive resolution this revision applies — drop
`config/popoto_floor.py`, wrap the import in `try/except ImportError`, degrade to the unfiltered sum,
report loudly via the `agent/index_drift.py::_report_loud` pattern, `rm -f` the shared log before the
row that writes it, and close the gaps the floor module opened. It also independently confirms the
`test_quarantine_count_sums_across_all_indexed_fields` rewrite this plan already scheduled. Its one
piece of process news is that reaching BUILD needs a cycle-cap override or a fresh run id, which is
the supervisor's call, not the plan's.

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

**Scope containment:** the diff is one method body in `models/agent_session.py`, two test files, and
four doc pages. `config/popoto_floor.py` was pulled in by the round-1 revision and has been struck
back out: the `try/except ImportError` degrade costs three lines at the call site and removes that
module — an incident-response interlock with its own failure policy, its own test file, and its own
popoto-coupling register — from the diff, from the Verification table, and from the Test Impact and
Documentation obligations it would have opened. That is what keeps this plan inside a Small appetite
that also funds five new tests, one rewritten test, and three mutation checks.

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
  The filter is built now rather than deferred because the counter's only consumer is
  `tools/doctor.py::_recent_quarantine_suffix`, whose surrounding remediation text sends an operator
  to `valor-session inspect` and `repair_indexes()` — the remedy for phantom hashes. A
  datetime-key-diverged healthy row needs `migrate_datetime_keys()` instead, so an unfiltered count
  would route an operator to the wrong remedy from the day #3181's aware-datetime work lands. That
  is why this goes past the letter of the directed fix in issue comment `5563793165`.
- **A fail-open import with a loud report** — the identity filter needs `decode_popoto_model_hashmap`,
  a popoto internal. The import lives at the call site under `try/except ImportError`; if it ever
  fails, the counter degrades to the unfiltered `len(diverged_keys)` sum (the exact rule issue comment
  `5563793165` directs) and the degradation is announced with `logger.error` plus a Sentry capture.
  `config/popoto_floor.py` is not touched — this plan changes no file outside
  `models/agent_session.py`, its two test files, and the docs.
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
- **Degrade the one internal import, loudly.** `decode_popoto_model_hashmap` is not exported from
  `popoto/__init__.py`; it lives at `popoto/models/encoding.py:390`, so an upstream move breaks the
  identity filter. Import it at the call site inside `repair_indexes()`, wrapped in
  `try/except ImportError`, and on failure degrade to the unfiltered `len(diverged_keys)` sum — which
  is exactly the counting rule issue comment `5563793165` directs, so the degraded number is correct,
  just coarser. Report the degradation the way `config/popoto_floor.py` reports its own unresolvable
  branch: `logger.error` plus a `sentry_sdk.capture_message` at `error` level, mirroring
  `agent/index_drift.py::_report_loud` (`agent/index_drift.py:211-231`), with the Sentry call itself
  wrapped so it can never crash the caller. `config/popoto_floor.py::assert_popoto_floor()` is
  deliberately NOT extended with a symbol probe: it raises only on an unambiguous `violated` verdict
  and fails open on `unresolvable`, because `repair_indexes()` runs on worker startup and an hourly
  reflection and a false positive there would block index repair fleet-wide. A missing internal symbol
  is neither `violated` nor `unresolvable`, and making it raise would import a hard-fail policy into a
  module whose entire failure policy refuses one. This fix keeps that module's asymmetry rather than
  breaking it: **runtime fails open, observability fails loud.**
- For each diverged key: read the raw hash, `decode_popoto_model_hashmap(cls, h,
  source_redis_key=key)`, then `_filter_hydrated_sessions([instance])`. An empty hash means the row
  is **gone**, not identity-less — `continue` without counting, matching the rule
  `test_gone_hash_orphan_cleared_by_wholekey_rebuild` (`:217`) already asserts. A decode that returns
  `None` or raises → treat as identity-less (it is certainly not a hydrated session) and add the key,
  with a `logger.debug`. One round trip per diverged key, no batching: diverged keys are 0 in a
  healthy keyspace.
- **The raw hash read is a third sanctioned raw-Redis exception in this method**, alongside the
  `$IndexF` scan and the plain counter key. It is necessary rather than convenient: a diverged row is
  unindexed and identity-less by construction, so no `AgentSession.query.filter(...)` can reach it —
  the raw read is the only way to see it at all. It is a non-mutating read and bypasses no
  `on_save`/`on_delete` hook. **Mechanics the builder needs:**
  `.claude/hooks/validators/validate_no_raw_redis_delete.py` runs only through the PreToolUse **Bash**
  dispatcher (`.claude/hooks/dispatch/pre_tool_use_bash.py`), so writing this call into
  `models/agent_session.py` with Edit/Write is not blocked, while pasting the same call shape into a
  Bash one-liner or an interpreter heredoc is. Verify the change by running the scoped tests, never
  by a `.venv/bin/python -c` probe containing `POPOTO_REDIS_DB.hgetall(`.
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
- [ ] The `decode_popoto_model_hashmap` import must not be able to fail the repair. Wrap it in
      `try/except ImportError` at the call site and add a test that forces the `ImportError` and
      asserts `repair_indexes()` still returns its 2-tuple, that the counter equals the unfiltered
      `len(diverged_keys)`, and that `logger.error` fired — a silently-degraded filter must never
      look healthy.
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
- [ ] New: `tests/unit/test_agentsession_index_guard_generalized.py` gains
      `test_decode_import_failure_degrades_to_unfiltered_count_and_reports_loud`, covering the
      `ImportError` degrade path and its loud report.
- [ ] `tests/unit/test_popoto_floor.py` — NOT TOUCHED, and neither is `config/popoto_floor.py`. The
      degrade path lives entirely at the call site in `models/agent_session.py`, so the floor
      interlock, its failure policy, its test file, and its "POPOTO COUPLING POINT" register are all
      outside this diff.
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
**Mitigation:** Diverged keys are 0 in a healthy keyspace, and a non-empty list is itself a loud
WARNING from popoto naming the count. The `$IndexF` scan above needs its `batch_size=5000` pipeline
because it walks every member of every index key on every startup; this loop's input is bounded by
rows popoto refused to index, which is a broken-deploy signal, not steady state. So: no batching now.
If a real keyspace ever produces a large diverged list, that WARNING is the trigger to add the same
pipelined treatment, and this line is the record of that decision.

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

### Risk 5: The degraded import path reports a coarser number without anyone noticing
**Impact:** If `decode_popoto_model_hashmap` moves upstream, the identity filter stops running and
the doctor's count silently starts including diverged-but-healthy rows — the exact wrong-remedy
routing Risk 1 exists to prevent, arriving quietly instead of loudly.
**Mitigation:** The degradation is not silent. It emits `logger.error` plus a Sentry capture at
`error` level on every pass it occurs, mirroring `agent/index_drift.py::_report_loud` and matching
the "observability fails loud" half of `config/popoto_floor.py`'s stated policy. The degraded number
is still the counting rule issue comment `5563793165` directs, so the runtime behaviour remains
correct while the alert names what was lost. Covered by
`test_decode_import_failure_degrades_to_unfiltered_count_and_reports_loud`.

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
**Mitigation:** An empty hash read means the row is gone, and a gone row cannot be re-inflated into
any index — nothing SADDs a key with no hash behind it. So it is skipped with a `logger.debug` and
**not counted**, which is the same rule `:217` already asserts for gone-hash orphans. Counting it
would inflate the doctor's phantom-drift number during ordinary `Meta.ttl` churn. No exception, no
retry.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3199] The archive half — `tests/unit/test_session_archive.py::test_restore_preserves_a_real_datetime_byte_identically`
  and any change to `agent/session_archive.py`. It is the aware-vs-naive datetime decode contract
  from #3173/PR #3180, a different 1.9.0 change, and it is already in flight as a separate
  direct-to-main hotfix on the same tracking issue. Touching it here would collide with that lane.
- [EXTERNAL] The nightly-host question the issue body raised is already answered in comment
  `5563793165`: the host carries popoto 1.9.0 and ran on 2026-09-06 at `25e4df925`, so the 09-05
  gap was a one-night miss. No fleet action is taken from this lane.
- [SEPARATE-SLUG #3181] The repo-wide audit of naive `updated_at` datetime comparisons. Comment
  `5563793165` assigns it to #3181 explicitly; duplicating it here would fork that audit.
- [ORDERED] Closing #3199. The issue closes only when both halves have landed, so this PR says
  `Refs #3199`, never `Closes`.

## Update System

No update system changes required, and specifically **no Popoto migration**. The migration rule in
`docs/sdlc/do-plan.md` keys on *schema* change — a field added, removed, renamed, or re-typed, which
is what makes stored hashes stale. This plan changes only the body of the `repair_indexes()`
classmethod and the unit of the `_last_quarantined_identityless` class attribute; no `Field` on
`AgentSession` is touched, so no stored hash changes shape and `MIGRATIONS` in
`scripts/update/migrations.py` stays untouched. No new dependency or config file is introduced
either. The popoto floor
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
- [ ] Comment the `try/except ImportError` at the call site with why the degrade is correct (issue
      comment `5563793165`'s counting rule, just unfiltered) and why `config/popoto_floor.py` is
      deliberately not the place for this check (it fails open by policy).

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
- [ ] A forced `ImportError` on `decode_popoto_model_hashmap` degrades the counter to the unfiltered
      `len(diverged_keys)` sum, keeps `repair_indexes()` returning its 2-tuple, and emits the loud
      `logger.error` + Sentry report — asserted by a test, not only by inspection.
- [ ] `config/popoto_floor.py` and `tests/unit/test_popoto_floor.py` are untouched by this PR. The
      files this PR may change are `models/agent_session.py`, the two named test files, and docs.
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

Never write raw Redis ops against Popoto-managed keys. This method carries **three** sanctioned
exceptions, and this plan adds the third deliberately:

1. The `$IndexF` scan — popoto's `rebuild_indexes()` does not enumerate `$IndexF` keys.
2. The plain `agentsession:repair_indexes:last_quarantined_identityless` key — not Popoto-managed.
3. **New:** the raw hash read on each diverged key. A diverged row is unindexed and identity-less by
   construction, so `AgentSession.query.filter(...)` cannot reach it; the raw read is the only way to
   observe it. It is non-mutating and bypasses no ORM hook.

Add no fourth. `validate_no_raw_redis_delete.py` fires only on **Bash** commands in an executable
context, so writing exception 3 into `models/agent_session.py` via Edit/Write is not blocked — but a
`.venv/bin/python -c` or heredoc probe containing `POPOTO_REDIS_DB.hgetall(` is, including a
single-quoted heredoc that feeds an interpreter. Verify through the scoped tests instead.

### Testing discipline for the builder and the validator

- Every test run goes through `./scripts/pytest-clean.sh` with **scoped node ids or the two named
  test files only**. Never bare `pytest`, and never the full suite: six lanes share a 15-slot Redis
  test-DB pool on this machine, and a full run starves the others for the ~20 minutes it takes.
- **Read the passed count off the pytest summary line.** The wrapper currently exits 0 when zero
  tests ran, so exit code 0 on its own proves nothing, and a `0 passed` summary is a FAILED
  verification, not a pass. Record the exact summary line as evidence.
- The two files in scope are `tests/unit/test_agentsession_pending_index_leak.py` and
  `tests/unit/test_agentsession_index_guard_generalized.py`.

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
- Fold in the divergence seam: `for key in getattr(result, "diverged_keys", ()) or ()`, read the raw
  hash, and `continue` without counting if it is empty (the row is gone, not identity-less — `:217`'s
  rule). Otherwise decode via `decode_popoto_model_hashmap(cls, h, source_redis_key=key)` and add the
  key to `quarantined_keys` when `_filter_hydrated_sessions([instance])` is empty, when the decode
  returns `None`, or when the decode raises. One read per key, no pipeline batching — see Risk 2.
- Import `decode_popoto_model_hashmap` at the call site inside `repair_indexes()` under
  `try/except ImportError`. On `ImportError`, skip the per-key read/decode/filter loop entirely,
  `quarantined_keys.update(<diverged keys>)` for the unfiltered sum, and emit the loud degradation
  report (`logger.error` naming the missing symbol and the installed popoto version, plus a
  `sentry_sdk.capture_message` at `error` level inside its own `try/except` so Sentry can never crash
  the caller — the shape of `agent/index_drift.py::_report_loud`, `agent/index_drift.py:211-231`).
  Do **not** touch `config/popoto_floor.py`: it fails open by policy and a symbol probe there would
  block index repair fleet-wide on a false positive.
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
- Add `test_decode_import_failure_degrades_to_unfiltered_count_and_reports_loud`: force the
  `decode_popoto_model_hashmap` import to raise `ImportError`, seed diverged rows, and assert the
  counter equals the unfiltered diverged-key count, that `repair_indexes()` still returns its
  2-tuple, and that the degradation was reported at `error` level.
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
- **Mutation C (:248 specifically).** Delete the `if not _filter_hydrated_sessions([model_instance]):`
  guard inside `_make_identityless_skip_shim` so the shim records **every** row that reaches
  `on_save`. At `:248` the three healthy pending sessions are the only rows popoto does not divert
  into `diverged_keys`, so they reach `on_save`, the counter becomes 3, and the assertion goes red;
  `:217` (popoto's scan never sees a hash for the orphan) and `:265` (empty keyspace) stay green.
  Record all three results. Revert. There is no escape hatch on this mutation — it is a source edit
  in the working tree like A and B.
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

Every row below was measured against unmodified `main` at `ed1820fcc` before the build starts, and
re-confirmed at `8f0b04a70` — no commit between the two touched `models/agent_session.py`,
`tests/unit/test_agentsession_pending_index_leak.py`, or
`tests/unit/test_agentsession_index_guard_generalized.py`, so the baseline still holds. A row
that is already green on `main` proves nothing about this work, so each row records its pre-change
result; the three rows that are deliberately green on `main` are labelled anti-regression, because
what they assert is that something existing was **not removed** — the `on_save` shim, the archive
half, and the `config/popoto_floor.py` interlock.

**The scoped-suite log is named per RUN, not per issue.** `/tmp/quarantine-3199-d8a7e64660f541e2be5d9baa9657759c.log` embeds this lane's
`run_id` (the one `sdlc-tool session-ensure` issued for #3199), so a concurrent lane on this machine
cannot write a file the rows below then read as their own evidence. If the build executes under a
different `run_id`, substitute it in both rows and record the substitution in the PR body.

| Check | Command | Expected | On `main` |
|-------|---------|----------|-----------|
| Scoped suite green, non-empty | `rm -f /tmp/quarantine-3199-d8a7e64660f541e2be5d9baa9657759c.log && ./scripts/pytest-clean.sh tests/unit/test_agentsession_pending_index_leak.py tests/unit/test_agentsession_index_guard_generalized.py -n0 -q 2>&1 \| tee /tmp/quarantine-3199-d8a7e64660f541e2be5d9baa9657759c.log \| grep -oE '[0-9]+ passed' \| grep -oE '^[0-9]+'` | output > 12 | RED (10) |
| No failing nodes | `grep -cE '^FAILED\|[0-9]+ failed' /tmp/quarantine-3199-d8a7e64660f541e2be5d9baa9657759c.log` | match count == 0 | RED (4) |
| Divergence seam wired in | `grep -c 'diverged_keys' models/agent_session.py` | output > 0 | RED (0) |
| Old bare-int accumulator gone | `grep -c 'quarantined = \[0\]' models/agent_session.py` | match count == 0 | RED (1) |
| Identity filter is inside the new loop | `.venv/bin/python -c "import inspect, models.agent_session as m; s=inspect.getsource(m.AgentSession.repair_indexes); print('_filter_hydrated_sessions' in s.split('diverged',1)[1] if 'diverged' in s else False)"` | output contains True | RED (False) |
| Import degrade path present | `grep -c 'ImportError' models/agent_session.py` | output > 0 | RED (0) |
| Degradation reported loudly | `grep -c 'capture_message' models/agent_session.py` | output > 0 | RED (0) |
| Floor interlock untouched (anti-regression) | `git diff --name-only origin/main...HEAD \| grep -c 'config/popoto_floor[.]py'` | match count == 0 | green (0) |
| on_save shim retained (anti-regression) | `grep -c '_make_identityless_skip_shim' models/agent_session.py` | output > 1 | green (2) |
| Archive half untouched (anti-regression) | `git diff --name-only origin/main...HEAD \| grep -cE 'test_session_archive\|session_archive[.]py'` | match count == 0 | green (0) |
| Lint clean | `python -m ruff check models/agent_session.py tests/unit/test_agentsession_pending_index_leak.py tests/unit/test_agentsession_index_guard_generalized.py` | exit code 0 | green |
| Format clean | `python -m ruff format --check models/agent_session.py tests/unit/test_agentsession_pending_index_leak.py tests/unit/test_agentsession_index_guard_generalized.py` | exit code 0 | green |
| No stale xfails introduced | `grep -rn 'xfail' tests/unit/test_agentsession_pending_index_leak.py tests/unit/test_agentsession_index_guard_generalized.py` | exit code 1 | green |

The second row reads the log the first row wrote, so the scoped suite runs once, not twice. The
`rm -f` in the first row is what makes that safe: without it a log left by an earlier run satisfies
row 2 even if the suite crashed before writing a byte. With the truncation in place an absent log
**fails closed** — `grep -c` on a missing file exits 2 and prints nothing, and the `match count == 0`
expectation requires non-empty stdout, so "no log" is a failed row rather than a passing one. That
depends on the empty-stdout gate, so it is stated here rather than left implicit.

Both `/tmp/quarantine-3199-d8a7e64660f541e2be5d9baa9657759c.log` reads assume the scoped run in row 1 actually
executed. `scripts/pytest-clean.sh` currently exits 0 when zero tests ran, so **exit code 0 alone
proves nothing** — the passed count on the pytest summary line is the evidence, and a `0 passed`
summary is a FAILED verification.

## Critique Results

**Depth:** FULL (3 critics) · **Mode:** sequential lenses (Agent tool unavailable: not in tool list) · **Round 1 verdict:** NEEDS REVISION (2 blockers) — all rows closed · **Round 2 verdict:** NEEDS REVISION (1 blocker, 3 concerns, 1 nit) — all rows closed · **Round 3 verdict:** READY TO BUILD (with concerns) (0 blockers, 3 concerns, 1 nit). Round 2's revision landed 2026-09-07 and was the final authorized revision round (owner ruling: exactly one round past the G2 cap); its resolution was subtractive — `config/popoto_floor.py` left the plan rather than gaining a probe, and round 3 verified that removal is complete. The round-3 rows below are `pending` and are accepted on the record for the builder to fold in during BUILD; no further revision round is authorized.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | Verification row "Identity filter applied to diverged keys" is vacuous: `grep -c '_filter_hydrated_sessions' models/agent_session.py` already returns 7 on unmodified main, so `output > 1` is green before any work is done and cannot detect a build that skipped the filter. | Verification table re-baselined with an `On main` column; the filter row is now an inside-the-loop AST-scoped check that reads False on main | Measured on main at `ed1820fcc`: the count is 7 (import plus six existing call sites). Set the expectation to `output > 7` and pair it with a row proving the filter sits inside the new diverged-key loop rather than anywhere in the file. Every row in this table should be red on main before the build starts — re-baseline the others against that standard too. |
| BLOCKER | History & Consistency | The plan forbids and mandates the same operation. "Domain framing for the builder" says the `$IndexF` scan and the plain counter key are the only sanctioned raw-Redis exceptions and to "Add no others"; task 1 then instructs a raw `hgetall` on `AgentSession:*` hashes, a third one. This is not only a documentation contradiction: the repo's raw-Redis validator hook fires on that call shape, so a builder following task 1 is blocked outright. | Domain framing now names the raw hash read as sanctioned exception 3 with its necessity argument, and records that the validator is Bash-only so Edit/Write is the route | The exception is genuinely necessary: a diverged row is unindexed and identity-less by construction, so no `AgentSession.query.filter(...)` can reach it and the raw hash read is the only way to see it. Name it as a third sanctioned exception in the Domain framing paragraph with that reason, repeat the reason as a comment at the call site, and confirm how the guard is satisfied before the builder starts — the hook blocked this very string during critique, so "it is only a read" is not sufficient on its own. |
| CONCERN | Risk & Robustness | Mutation C for `:248` ships with its own escape hatch and its primary form (monkeypatching a derived key in a scratch run) is not a working-tree source mutation, so a validator will take the substitute every time. A gate the validator can opt out of does not establish that `:248` bites. | Mutation C replaced with the shim-guard deletion; escape hatch removed | A deterministic mutation exists: delete the `if not _filter_hydrated_sessions([model_instance]):` guard inside `_make_identityless_skip_shim` so it records every row reaching `on_save`. At `:248` the three healthy sessions are the only rows popoto does not divert into `diverged_keys`, so they reach `on_save`, the counter becomes 3, and the assertion goes red; `:217` (no scanned hash) and `:265` (empty keyspace) stay green. Replace Mutation C with this and delete the escape hatch. |
| CONCERN | Risk & Robustness | Counting a diverged key whose raw hash read comes back empty as identity-less contradicts the boundary `:217` exists to assert — that a gone-hash orphan is NOT quarantine. With `AgentSession`'s `Meta.ttl` keepalive, a row expiring mid-repair is ordinary churn, so this silently inflates the doctor's drift number. | An empty hash read is now skipped, not counted, matching `:217` | A vanished row cannot be re-inflated into an index, so it is not quarantine by the counter's own definition. Skip an empty hash with a `logger.debug` and `continue` before decoding, rather than counting it. Keep counting decode-returns-None and decode-raises, which are genuine "could not establish identity" outcomes. |
| CONCERN | Scope & Value | Task 1 mandates a pipelined 5000-chunk batch path for the diverged-key loop, which the plan's own Risk 2 and Solution both say is empty on a healthy keyspace. That is optimization for an already-broken deploy, funded out of a Small appetite that also has to pay for six new tests and three mutation checks. | Pipelining mandate dropped from task 1; Risk 2 records the WARNING as the trigger to revisit | The `$IndexF` scan needs its `batch_size = 5000` pipeline because it walks every member of every index key on every worker startup. The diverged-key loop has no comparable exposure — its input is 0 on a healthy keyspace and is itself a loud WARNING when it is not. Drop the pipelining mandate, issue one hash read per diverged key, and leave Risk 2 as a documented follow-up trigger. |
| CONCERN | Scope & Value | The identity filter goes beyond the directed fix in issue comment 5563793165 ("sum `len(diverged_keys)` into `quarantined[0]`") and carries most of the plan's new surface, while the condition it guards cannot occur until the archive half — an explicit No-Go here — lands. | Solution's fourth bullet now carries the wrong-remedy justification for building the filter now | Add the justification to the Solution's fourth bullet: the counter's only consumer is `tools/doctor.py::_recent_quarantine_suffix`, whose remediation text sends an operator to `valor-session inspect` and `repair_indexes()` for phantom hashes, whereas a datetime-key-diverged healthy row needs `migrate_datetime_keys()`. An unfiltered count routes an operator to the wrong remedy the day #3181's work lands. Without that sentence the filter reads as gold-plating against the issue's own instruction. |
| CONCERN | History & Consistency | `decode_popoto_model_hashmap` is not public — it lives at `popoto/models/encoding.py:390` and is absent from the package `__init__.py`. Binding the fix to a third-party internal repeats the exact wager that caused this bug, which the plan's own "Why Previous Fixes Failed" table names. | Shape (b): the import is wrapped in `try/except ImportError` at the call site and degrades to the unfiltered sum. Round 1 chose shape (a); round 2's blocker showed (a) is incompatible with `assert_popoto_floor()`'s fail-open policy, so (b) is the settled answer and the degradation is reported at `error` with a Sentry capture rather than at WARNING. | Two workable shapes. (a) Add the import to `config/popoto_floor.py`'s `assert_popoto_floor()` so an upstream move fails at worker startup with a named error instead of at the first reflection tick. (b) Wrap the diverged-key filter in `try/except ImportError` and degrade to the unfiltered `len(diverged_keys)` sum, logging the degradation at WARNING. (a) is preferable — one failure mode instead of two. The plan must say which. |
| CONCERN | History & Consistency | The Update System section asserts no migration is needed without the reason, while `docs/sdlc/do-plan.md` requires any plan touching a Popoto model to address `scripts/update/migrations.py` explicitly. | Update System now states the schema-change rule and why `MIGRATIONS` stays untouched | The migration rule keys on schema change — a field added, removed, renamed, or re-typed, which is what makes stored hashes stale. This plan changes only the body of `repair_indexes()` and the unit of the `_last_quarantined_identityless` class attribute; no `Field` on `AgentSession` is touched, so no stored hash changes shape and `MIGRATIONS` stays untouched. One sentence naming that closes it. |
| NIT | Scope & Value | The Verification rows "Scoped suite green" and "Run was not empty" execute the same pytest invocation, running the suite twice to answer one question. | Folded into one run: row 2 reads the log row 1 writes | — |
| NIT | History & Consistency | Three code references resolve to nothing from the repo root: `popoto/models/base.py` (written elsewhere in the plan with its full `.venv/lib/python3.14/site-packages/` prefix) and the bare basenames `test_conftest_isolation_guards.py` and `test_job_model.py`. | All three references corrected to their full paths | — |
| BLOCKER | History & Consistency | Round 2. The revision directs "Add `decode_popoto_model_hashmap` to `config/popoto_floor.py::assert_popoto_floor()`. An upstream move then fails at worker startup with a named error". That function raises ONLY on an unambiguous `violated` verdict and deliberately fails open on `unresolvable`, because — per its module docstring — `repair_indexes()` runs on worker startup and an hourly reflection, so a false positive there would block index repair fleet-wide, a worse incident than the one being prevented. A missing internal symbol is neither `violated` nor `unresolvable`; making it raise imports a hard-fail policy into a module that refuses one. | Resolved as recommended. `config/popoto_floor.py` is no longer extended and leaves the diff entirely. The Solution's "Degrade the one internal import, loudly" bullet and task 1 now import `decode_popoto_model_hashmap` at the call site under `try/except ImportError`, degrading to the unfiltered `len(diverged_keys)` sum on failure and reporting it with `logger.error` plus a Sentry capture mirroring `agent/index_drift.py::_report_loud` (`:211-231`). New Key Element "A fail-open import with a loud report", new Risk 5, new failure-path row, new test `test_decode_import_failure_degrades_to_unfiltered_count_and_reports_loud` (task 2), and a new Success Criterion. Verified independently: `assert_popoto_floor()` (`config/popoto_floor.py:248-259`) raises only on `VIOLATED`, and the module docstring states the runtime-fails-open / observability-fails-loud asymmetry the critic cites — the finding is correct as written. | Do not extend `assert_popoto_floor()`. At the call site in `repair_indexes()`, wrap the import in `try/except ImportError` and degrade to the unfiltered `len(diverged_keys)` sum — which is exactly what issue comment 5563793165 directs, so the degraded path is correct, just coarser. Report the degradation on the surface that module already uses for its own unresolvable branch (`logger.error` plus a Sentry capture, mirroring `agent/index_drift.py::_report_loud`, which `config/popoto_floor.py` names as its model), so a silently-degraded filter never looks healthy. This keeps the module's own asymmetry: runtime fails open, observability fails loud. |
| CONCERN | History & Consistency | Round 2. The revision pulled `config/popoto_floor.py` into scope (Solution, task 1, two Verification rows) without updating the three sections that govern completeness: `tests/unit/test_popoto_floor.py` exists and is absent from Test Impact, the Documentation section names no floor page, and no Success Criterion covers the import-hardening decision. | Closed by deletion, per the recommendation's first branch. `config/popoto_floor.py` is struck from the Solution, from task 1, from the Verification table (the "Internal import pinned to the floor" row is gone), and from the Lint and Format rows' file lists. Test Impact now carries an explicit `tests/unit/test_popoto_floor.py` — NOT TOUCHED disposition naming the interlock, its failure policy, its test file, and its POPOTO COUPLING POINT register as outside this diff, and a Success Criterion asserts both files are untouched. A new anti-regression Verification row proves it from the diff. | If the blocker resolves as recommended, `config/popoto_floor.py` leaves the diff entirely and all three gaps close by deletion — also strike it from the Lint and Format rows' file lists. If any change to that module survives, give `tests/unit/test_popoto_floor.py` an explicit disposition in Test Impact and add the new symbol to that module's "POPOTO COUPLING POINT -- re-verify on any popoto upgrade" note, which is the repo's standing register of popoto internals it depends on. |
| CONCERN | Scope & Value | Round 2. The round-1 revision answered a CONCERN about one internal import by adding work in a second module to a Small-appetite plan. `config/popoto_floor.py` is an incident-response interlock with its own failure policy, test file, and coupling register; editing it is not a one-line hardening step, and it guards a symbol the fix can degrade without. | Accepted. The Appetite section gains a "Scope containment" paragraph stating exactly this: the degrade costs three lines at the call site, removes the interlock from the diff, the Verification table, and the Test Impact / Documentation obligations it opened, and that is what keeps the plan inside its Small appetite. The diff is now one method body in `models/agent_session.py`, two test files, and four doc pages. | The degrade path costs three lines at the call site and removes `config/popoto_floor.py` from the diff, from two Verification rows, and from the Test Impact and Documentation gaps it opened. That keeps the plan inside its Small appetite and leaves the fleet-wide interlock untouched — the conservative choice for a module whose docstring opens by describing the incident it exists to prevent. |
| CONCERN | Risk & Robustness | Round 2. Verification row "No failing nodes" reads `/tmp/quarantine-3199.log`, which the row above writes, and nothing truncates it first. A log left by any earlier run on this machine satisfies the row even if the suite crashed before writing a byte. `/tmp` is shared across lanes, so the stale file is not hypothetical — the revision traded a duplicated suite run for a check that can pass on stale evidence. | Both remedies applied, the stronger one first. The log is now named per RUN — `/tmp/quarantine-3199-d8a7e64660f541e2be5d9baa9657759c.log`, embedding this lane's `run_id` — so two lanes cannot read each other's evidence, with a note on substituting a different `run_id`. The writing row is prefixed `rm -f <log> &&`, and the prose under the table states that an absent log then fails closed because `grep -c` on a missing file exits 2 with empty stdout while the `match count == 0` expectation requires non-empty stdout. | Prefix the first row's command with `rm -f /tmp/quarantine-3199.log &&`. `grep -c` on a missing file exits 2 with empty stdout and the `match count == 0` expectation requires non-empty stdout, so an absent log fails closed once the truncation is in place — say that in the row, because it depends on the parser's empty-stdout gate and is not obvious. Better still, name the log per run rather than per issue so two lanes cannot read each other's evidence. |
| NIT | Risk & Robustness | Round 2. All ten round-1 findings verify as genuinely closed on spot-check: the Verification table carries an `On main` column with six rows measured red, the Domain framing names the raw hash read as sanctioned exception 3 with the Bash-only validator mechanics, Mutation C is a source edit with no escape hatch, the empty-hash case skips rather than counts, the pipelining mandate is gone, the wrong-remedy justification is in the Solution, the Update System states the schema-change rule, and the three path references are corrected. | Acknowledged, no action needed. The spot-check confirms all ten round-1 findings stayed closed through this round; nothing in round 3's revision reopens any of them — the retained shim, the empty-hash skip, Mutation C's source edit, the single scoped run, and the `On main` column all survive the `config/popoto_floor.py` removal untouched. | — |
| CONCERN | Risk & Robustness | Round 3. The `ImportError` degrade reports with a `sentry_sdk.capture_message` at error level "on every pass it occurs", but `repair_indexes()` runs on every worker startup, on the hourly agent-session-cleanup reflection (`agent/session_health.py:6101`), and opportunistically from session pickup (`agent/session_pickup.py:469`). A moved upstream symbol is a permanent condition, so the loud report becomes an unthrottled error-level Sentry stream across the fleet for as long as it lasts. | pending | Add a class attribute beside `_last_quarantined_identityless` (`models/agent_session.py:695`), e.g. `_decode_degrade_reported: bool = False`, and gate only the Sentry call on it: `logger.error(...)` unconditionally, then `if not cls._decode_degrade_reported: cls._decode_degrade_reported = True; <sentry capture in its own try/except>`. Keep the capture inside its own `try/except Exception` exactly as `agent/index_drift.py::_report_loud` does at `agent/index_drift.py:221-231`. `test_decode_import_failure_degrades_to_unfiltered_count_and_reports_loud` must reset the latch (`monkeypatch.setattr(AgentSession, "_decode_degrade_reported", False)`) or it passes or fails on test order. |
| CONCERN | Risk & Robustness | Round 3. The recipe for `test_decode_import_failure_degrades_to_unfiltered_count_and_reports_loud` ("force the `decode_popoto_model_hashmap` import to raise `ImportError`, seed diverged rows") cannot work as written: popoto's own `rebuild_indexes()` imports the same symbol from the same module at `.venv/lib/python3.14/site-packages/popoto/models/base.py:3194`, inside the call `repair_indexes()` makes. Any technique that makes the symbol unimportable raises `ImportError` out of `cls.rebuild_indexes()` first, which sits under a bare `try/finally` with no `except`, so it escapes `repair_indexes()` and the degrade branch is never reached. | pending | In the test, first monkeypatch `AgentSession.rebuild_indexes` to a callable returning a canned `RebuildIndexesResult(0, [<seeded keys>])` (from `popoto.models.base`) — the same technique task 2 already uses for `test_plain_int_rebuild_result_degrades_to_shim_only` — and only then `monkeypatch.delattr(popoto.models.encoding, "decode_popoto_model_hashmap", raising=False)`. With the real rebuild stubbed out, `base.py:3194` never runs and the only remaining import of that symbol is the one inside `repair_indexes()`. Do NOT widen the production `except ImportError` to `except Exception` to make the naive version pass. |
| CONCERN | History & Consistency | Round 3. Risk 3's mitigation states "the docstring, the WARNING message, the doctor suffix wording, and the renamed test all state 'rows' explicitly", but the doctor suffix is a literal at `tools/doctor.py:1599` reading "identity-less hash re-add(s)", and `tools/doctor.py` appears in no Documentation checkbox and is excluded by both the Appetite's file list and the Success Criterion "The files this PR may change are `models/agent_session.py`, the two named test files, and docs." Built as written, the plan's only user-visible surface reports a de-duplicated row count labelled with the old event unit — the exact misreading Risk 3 exists to prevent. | pending | Either add `tools/doctor.py` to the allowed-file list with a Documentation checkbox, or amend Risk 3 to stop claiming the doctor wording changes. The edit is one f-string at `tools/doctor.py:1599`: replace "identity-less hash re-add(s)" with a row phrasing such as "identity-less row(s)". Two docstrings on the same surface carry the same stale unit and should move with it: `_recent_quarantine_suffix` at `tools/doctor.py:1580-1581` and `_check_agentsession_index_drift` at `tools/doctor.py:1607-1611`. If the file list widens, add `tools/doctor.py` to the Verification table's Lint and Format rows; no doctor test asserts on the suffix text. Task 5 (`validate-doctor`, "Paste the rendered suffix into the PR body") is the step that surfaces this at build time. |
| NIT | History & Consistency | Round 3. The Inline Documentation bullet names only "the module-level comment at `models/agent_session.py:68-78`" (that block actually spans `:66-74`; `:75-78` are the constant definitions). The comment stating the counter's unit most directly is a different block — `models/agent_session.py:689-694`, immediately above `_last_quarantined_identityless: int = 0` at `:695` — and it says "refused to re-add to the status index" and "This is a per-pass event count", both stale under row-scoped counting across all three IndexedFields. | pending | — |

---

## Open Questions

Both questions this plan raised are answered by issue comment `5563793165`, which is the
supervisor's own direction, so neither blocks the critique stage.

1. **Counter unit.** Resolved: the comment directs "sum `len(diverged_keys)` into `quarantined[0]`",
   which is row-scoped counting. This plan adopts it, adding de-duplication (so a row seen through
   both seams counts once) and an identity filter on the diverged keys (so a healthy row that
   diverges on key canonicalization — the sibling archive half's failure mode — cannot masquerade as
   phantom drift on the doctor surface). Nothing outside `tools/doctor.py::_recent_quarantine_suffix`
   consumes the number, and that text is updated with it.
2. **Rewriting `test_quarantine_count_sums_across_all_indexed_fields`.** Resolved by consequence: the
   comment's counting rule yields five for five ghosts, and that test asserts fifteen. The comment
   names only the three `== 0` assertions as things to re-verify; this `>= n_ghosts * 3` assertion is
   the fourth consequence it does not mention, and it cannot survive the directed fix. The rewrite
   keeps the node and strengthens it — asserting all three `$IndexF` sets stay clean is a more direct
   statement of the #2207 generalization than counter arithmetic ever was.
