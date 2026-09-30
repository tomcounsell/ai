---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-15
tracking: https://github.com/tomcounsell/ai/issues/2101
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-15T10:06:36Z
---

# AgentSession pending-index phantom leak — stop rebuild re-inflation + delete srem asymmetry

## Problem

The Redis SET `$IndexF:AgentSession:status:pending` (Popoto's secondary index for
`AgentSession.query.filter(status="pending")`) leaks phantom members at a sustained
~250/sec on production ("Valor the Captain", 2026-07-15), crash-looping the worker: `scard`
reached 1,377,001 while the ORM ground truth `AgentSession.query.filter(status="pending")`
reported `pending_count: 0`. Worker startup runs `cleanup_corrupted_agent_sessions()` before it
can register a heartbeat, and that cleanup can never finish scanning a multi-hundred-thousand
member index before the watchdog's next tick kills the process → permanent restart loop.

**Confirmed root cause (two independent code traces + direct verification):**

1. **Rebuild re-inflation.** `AgentSession.repair_indexes()` (`models/agent_session.py:2102-2152`)
   → popoto `rebuild_indexes()` (`.venv/.../popoto/models/base.py:2819-2856`) `scan_iter`s
   `AgentSession:*`, `hgetall`s each (skips only if **completely** empty, `base.py:2834-2836`),
   `decode_popoto_model_hashmap`s it (`base.py:2839`), and runs `field.on_save` for `status`
   (`base.py:2850-2856`) → `SADD $IndexF:AgentSession:status:pending`. Because
   `status = IndexedField(default="pending")` (`models/agent_session.py:145`), **any near-empty
   / identity-less hash that decodes without a usable `status` materializes as `pending`** and
   is re-added to `:pending` on **every** rebuild. `query.filter(status="pending")` then
   hydrates and drops these via `_filter_hydrated_sessions` (no `session_id`), so the ORM count
   stays 0 while `scard` climbs. This is why a one-off manual `repair_indexes()` dropped `scard`
   to ~2k and it **refilled to 217k with no further action** — the rebuild half of repair
   re-inflates the index it just cleared.

2. **Phantom manufacture (delete `srem` asymmetry).** Popoto's `Model.delete()` deletes the
   hash **first** (`base.py:1652`), then runs `IndexedFieldMixin.on_delete`
   (`indexed_field_mixin.py:338`), which reads the (now-deleted) hash pointer to find which
   index member to `srem`. With the authoritative pointer unreadable it falls back to the
   legacy `field_value`-derived `srem` (`indexed_field_mixin.py:345-355`); when that disagrees
   with the member the entry physically lives under (delete-and-recreate sites, or an
   `AutoKeyField`-regenerated member key), the `srem` misses and the member is stranded as a
   phantom.

**Accelerant already fixed (commit `40b23937`, this session):** PR #2099's corrupted-pop
handler called the full `cleanup_corrupted_agent_sessions()` (→ unconditional `repair_indexes()`
rebuild) on **every** ~2s stuck-head pop, re-driving a full rebuild every 2s and turning the
latent leak into a runaway crash-loop. That call is now gated behind a per-`worker_key` cooldown
(`CORRUPTED_POP_REAP_COOLDOWN_S`, default 300s). This plan owns the **core** leak the cooldown
does not fix (the rebuild still re-inflates on the 300s sweep and every worker startup).

**Desired outcome:** `$IndexF:AgentSession:status:pending` (and other status indexes) track the
true count of live records with that status, growing only with real traffic. `repair_indexes` /
`rebuild_indexes` never re-index an identity-less (`session_id`-less) hash as default-`pending`,
and record deletion never strands an index member. The worker completes startup and holds a
stable heartbeat.

## Freshness Check

**Baseline:** `main` @ HEAD 2026-07-15 (post #2098/#2089/#2044/#2088 + the `40b23937` accelerant
gate). **Disposition: Unchanged** — root cause verified in real code today.
- `models/agent_session.py:145` (`status = IndexedField(default="pending")`),
  `:2102-2152` (`repair_indexes`) — exact.
- popoto 1.8.0 `base.py:1652` (delete-first), `:2819-2856` (rebuild re-add),
  `indexed_field_mixin.py:338/345-355` (on_delete srem fallback) — exact (external wheel).
- `agent/session_health.py:4677-4685` (unconditional `repair_indexes`) — exact.
- Commit `d105b33e` already batched the SCAN + widened the watchdog to 5min — scan performance
  only, NOT the leak. Do not re-propose.

## Coordination (avoid duplicate scope)

- **#2086** (`agentsession-index-drift-loud-surfacing.md`, Ready): a **detector** for
  hash-count vs queryable-count divergence (ERROR + Sentry + doctor check). Complementary — it
  *surfaces* this leak; this plan *fixes* it. No file collision (detector adds a startup guard +
  `tools/doctor.py` check; this plan changes `repair_indexes` re-add logic + delete ordering).
- **#2083** (`popoto-descriptor-pollution-audit.md`, Ready, chore): removes redundant *save-side*
  defensive scar tissue (`_INT_FIELDS_BACKCOMPAT`, `_saved_field_values["status"]` backfill,
  `srem`-across-`ALL_STATUSES`). Does NOT touch the rebuild re-add or delete-ordering fix. If
  #2083 lands first, re-confirm the `_saved_field_values["status"]` backfill removal does not
  change the identity-less-decode-to-`pending` behavior this plan depends on.

## Solution

### A. Stop rebuild re-inflation (primary)

The repo already owns `AgentSession.repair_indexes()`. Make its rebuild **refuse to re-index an
identity-less record**: a decoded instance whose `session_id` / `agent_session_id` is absent is
not legitimate queryable work and must not be `SADD`ed to any status index. **A1 is the chosen
altitude** (non-destructive); A2 is retained below only as a documented fallback with its own
sequencing constraint.

**A1 (skip the status-index `SADD` for identity-less records) — DO NOT reimplement popoto's
rebuild loop.** The critical seam: popoto's `rebuild_indexes()` re-adds the status index inside a
generic per-field loop — `for field_name, field in cls._meta.fields.items(): field.on_save(...)`
at `.venv/.../popoto/models/base.py:2849-2856`. There is **no** isolated status-only call to
hook; the SADD is one iteration of that loop. So bound the intervention to **that one field's
`on_save`**:

- Provide a repo-owned `on_save` shim/override for the `status` `IndexedField` **only** that
  skips the `SADD` when `_filter_hydrated_sessions([instance])` rejects the record (no
  `session_id`). Every other field (`AutoKeyField`/`KeyField`, `SortedField`, class-set
  membership, and the `status` SADD for **healthy** records) stays routed through an
  **unmodified** `cls.rebuild_indexes()`. The reimplementation is one call — the `status`
  field's `on_save` — not the whole loop.
- Concretely: subclass/wrap popoto's `IndexedField` for `AgentSession.status` (or override the
  field instance's `on_save`) so the hydration guard runs before the SADD; leave popoto's rebuild
  orchestration untouched. This directly honors the "do not reimplement popoto's rebuild" Rabbit
  Hole — the healthy-record field-index rebuild is delegated to popoto verbatim.
- **Scope the guard to the rebuild path — do NOT suppress the SADD at normal live save.** A
  permanent, class-level `on_save` override that gates the SADD on `_filter_hydrated_sessions`
  would suppress indexing of a *legitimate brand-new* session at `AgentSession(...).save()` time
  (a new record can momentarily lack a fully-materialized `session_id`), which is the **inverse**
  of this bug — a healthy session that never appears in `:pending`. Prefer a transient shim active
  only for the duration of `cls.rebuild_indexes()` (save/restore the original `status.on_save`
  around the rebuild call), OR, if a permanent field subclass is used, it MUST be paired with a
  test asserting the normal creation flow still adds the record to
  `$IndexF:AgentSession:status:pending`. The identity-less skip is a *rebuild-time* correction,
  not a live-save gate.

Healthy records re-index normally; identity-less hashes are left un-indexed (invisible to
`query.filter`, which is correct) and counted/logged as `quarantined_identityless`.

**A2 (purge identity-less hashes before rebuild) — FALLBACK ONLY, and B-gated.** Before calling
`rebuild_indexes()`, delete the identity-less `AgentSession:*` hashes via the ORM (they decode
fine — near-empty, not msgpack-corrupt — so `instance.delete()` works) so the rebuild never sees
them. **A2 reuses the exact buggy delete path Solution B fixes** (`instance.delete()` deletes the
hash first, then `on_delete` reads the now-gone pointer — `base.py:1652` /
`indexed_field_mixin.py:338,345-355`), so an A2 purge can itself strand a fresh `:pending`
phantom. Therefore A2 must **not** ship before B: either sequence B first, or have the A2 path
capture the record's status-index membership and route the `srem` through popoto's own
`on_delete` deterministically (never a bare raw-Redis `srem` — see No-Gos). A1 remains the
recommendation precisely because it avoids this hazard entirely (it never deletes a hash).

Either way: return/log a `quarantined_identityless` count so the drift is observable, and route
the critical scan callers (`cleanup_corrupted_agent_sessions`, dashboard) through it. **Note:**
`quarantined_identityless` is a *per-pass event count*, not a cumulative keyspace gauge — it does
not reveal total `AgentSession:*` hashes climbing from an unfixed write source (see the keyspace
gauge in the pre-build classification step below and Risk 4).

### B. Stop phantom manufacture (delete srem asymmetry)

Fix the delete ordering so the index pointer is resolved **before** the hash is deleted. Popoto
is external, so this is a repo-side override of `AgentSession.delete()` (or a pre-delete hook)
that captures the current status-index membership and `srem`s it deterministically after the
hash delete — or a reconciliation pass in `repair_indexes` that removes index members whose
backing hash is gone (the existing `$IndexF` stale-member scan at `agent_session.py:2135-2140`
already deletes the whole index key and rebuilds; combined with A, the rebuild no longer re-adds
junk, so stranded members are cleared each pass). Confirm at build time whether A alone
converges the index (rebuild clears + no re-add) or whether B is independently required to stop
NEW phantoms between rebuilds.

**Sanctioned vs raw-Redis boundary for B (read this before implementing B):** whole-`$IndexF`-key
delete-and-rebuild via `repair_indexes` is sanctioned (the existing scan already does
`smembers`/`delete` on the whole index key). Member-level `SADD`/`SREM` outside popoto's own
`on_save`/`on_delete` is **prohibited** — B's member reconciliation must route through popoto's
`IndexedFieldMixin.on_delete` (`indexed_field_mixin.py:325-356`), never a bare
`POPOTO_REDIS_DB.srem(index_key, member)` (that trips `validate_no_raw_redis_delete.py`). See
No-Gos.

### Pre-build classification (read-only — run on the AFFECTED machine before building)

Open Question #3 (where the identity-less hashes originate) is not confirmed, and this machine
cannot reproduce the live leak. Before/at build start, run a **read-only** classification of a
live sample of `$IndexF:AgentSession:status:pending` members on the affected host ("Valor the
Captain") to confirm A1's seeded repro shape matches production:

- For a bounded sample of members, `EXISTS AgentSession:{member}` / `HGETALL` (read-only; no
  writes) and bucket each into:
  1. **empty-but-existing hash** (backing hash present, no `session_id`) — A1's skip fixes these
     on the next rebuild.
  2. **gone-hash orphan** (backing hash deleted) — popoto's `rebuild_indexes()` `scan_iter` never
     re-adds these, so **A1's skip is a no-op for them**; they are cleared only by the
     whole-`$IndexF`-key delete-and-rebuild (repair's stale-member scan) or by B. If the live
     sample is dominated by gone-hash orphans, A1 alone will NOT converge and B (or repair's
     whole-key rebuild) is load-bearing — decide altitude from this evidence, not assumption.
- This is a diagnostic gate, not a fix. It tells the builder whether A1 suffices or B is required,
  and whether an unfixed *write* path is still manufacturing hashes (residual keyspace growth).

## Failure Path Test Strategy

### Leak Reproduction Coverage
- [ ] Seed (test-scoped `project_key`) N healthy AgentSession hashes + M near-empty/identity-less
  `AgentSession:*` hashes (no `session_id`, so they decode to `status="pending"`). Run
  `repair_indexes()`; assert `scard($IndexF:AgentSession:status:pending)` equals the count of
  **healthy** pending records only (not N+M), and stays there across repeated rebuilds (no
  re-inflation). Assert the M identity-less hashes are counted/logged as quarantined.
- [ ] Delete-srem asymmetry: create a pending record, delete it via the ORM, assert the
  `:pending` index member is removed (no stranded phantom) — reproduces the delete-ordering bug
  and pins the fix.
- [ ] Convergence: starting from a pre-seeded bloated `:pending` index (phantoms), one
  `repair_indexes()` pass brings `scard` to the true pending count and a second pass keeps it
  there.
- [ ] Gone-hash orphan (A1 no-op case): seed a `:pending` member whose backing
  `AgentSession:*` hash does NOT exist; assert A1's skip does not touch it and that the
  whole-`$IndexF`-key rebuild (or B) is what clears it — pins the "A1 alone may not converge"
  boundary the pre-build classification checks for.
- [ ] A2-before-B stranding guard (only if A2 is ever selected): assert **no** `:pending` member
  survives an A2 purge of an identity-less hash performed **before** B is merged — i.e. the buggy
  `on_delete` ordering does not strand a fresh phantom. This test must fail on a naive
  `instance.delete()`-based A2 and pass only when B (or a deterministic captured-membership `srem`
  via popoto's `on_delete`) is in place.

### Empty/Invalid Input Handling
- [ ] All-healthy: rebuild re-indexes exactly the real pending set; `quarantined == 0`.
- [ ] Empty keyspace: `(0,0,0)`, no crash.
- [ ] Inverse-bug guard: a normal `AgentSession(...).save()` (healthy, live) still adds the record
  to `$IndexF:AgentSession:status:pending` — the A1 identity-less guard must NOT suppress
  legitimate live-save indexing (only the rebuild path skips identity-less hashes).

### Error State Rendering
- [ ] Assert the identity-less quarantine logs at `warning`/`error` with counts; no user surface.

## Test Impact

- [ ] `tests/unit/` repair_indexes / session_health index tests — UPDATE (additive) with the
  reproduction + convergence cases above. Audit existing `cleanup_corrupted_agent_sessions`
  tests that assume rebuild re-adds all hashes.
- [ ] No existing test reproduces the re-inflation or delete-srem-asymmetry — net-new coverage
  for a confirmed production leak.

## Documentation

- [ ] Create `docs/features/agentsession-pending-index-leak.md` (or extend
  `docs/features/popoto-index-hygiene.md`) describing the rebuild re-inflation mechanism
  (`status` default + identity-less re-add), the delete-srem asymmetry, and the fixes.
- [ ] Update `docs/features/agent-session-queue.md` (the #2101 accelerant note already added) to
  link the core fix, and cross-link the #2086 detector.

## Update System

No `scripts/update/run.py` change required. **Popoto schema:** no field/schema change — this is
index-rebuild + delete-ordering logic. If fix A2 (purge identity-less hashes) is chosen, that is
a runtime reap via the ORM, not a schema migration, so no `scripts/update/migrations.py` entry.
Confirm at build time.

## Agent Integration

No agent integration required — worker/model-internal index-health fix. No new CLI, no `.mcp.json`
change, no bridge import. `valor-session` / dashboard / `agent_session_scheduler status`
automatically benefit (index tracks reality; worker stops crash-looping). Optionally surface the
`quarantined_identityless` count in `dashboard.json`.

## No-Gos (Out of Scope)

- Editing popoto library files in `.venv` (external wheel).
- The #2086 detector (separate plan) and #2083 save-side scar-tissue removal (separate chore).
- Re-tuning the watchdog interval or the SCAN batching (already done in `d105b33e`).
- Raw-Redis writes/deletes on Popoto-managed keys (prohibited; use ORM `delete()` /
  `rebuild_indexes` / sanctioned index-set ops). **Boundary for Solution B:** whole-`$IndexF`-key
  delete-and-rebuild via `repair_indexes` is *sanctioned*; member-level `SADD`/`SREM` outside
  popoto's own `on_save`/`on_delete` is *prohibited*. If B is built it must route the member
  reconciliation through popoto's `IndexedFieldMixin.on_delete`
  (`indexed_field_mixin.py:325-356`), not a bare `POPOTO_REDIS_DB.srem(index_key, member)` — the
  latter is exactly the pattern `validate_no_raw_redis_delete.py` blocks.

## Rabbit Holes

- **Reimplementing all of popoto's rebuild.** Prefer scoping the skip-identity-less guard to the
  class-set / status-index re-add; delegate healthy-record field-index rebuild to popoto.
- **A global monkeypatch of `Model.delete` / `rebuild_indexes`.** AgentSession-only override
  bounds blast radius; a global fix is a follow-up if other models leak.

## Risks

### Risk 1: A alone may not stop NEW phantoms between rebuilds (B needed)
**Impact:** if the delete-srem asymmetry keeps stranding members, A clears them each rebuild but
they re-accumulate between passes.
**Mitigation:** measure convergence in the reproduction test; implement B (delete ordering) if A
alone doesn't hold the index flat between rebuilds.

### Risk 2: Skipping identity-less hashes hides a legitimate record with a transiently-unreadable id
**Impact:** a healthy record whose `session_id` is momentarily unreadable could be skipped.
**Mitigation:** the hydration predicate (`_filter_hydrated_sessions`) already defines
"identity-less"; reuse it exactly. Log every skip so a false skip is visible. A transient read
resolves on the next rebuild.

### Risk 3: #2083 changes the decode/save path
**Mitigation:** re-confirm identity-less-decode-to-`pending` behavior if #2083 lands first; the
reproduction test pins it and fails loudly on change.

### Risk 4: A1 leaves identity-less hashes undeleted → invisible keyspace growth from an unfixed write source
**Impact:** A1 correctly stops the *index* re-inflation but does not delete the identity-less
`AgentSession:*` hashes themselves. If a live write path is still manufacturing them, the raw hash
keyspace keeps growing invisibly (the index count stays flat, so `scard` looks healthy while
memory climbs). `quarantined_identityless` is a per-pass count, not a cumulative gauge, so it will
not surface this.
**Mitigation:** the pre-build classification step identifies whether the sample is dominated by
gone-hash orphans vs empty-existing hashes and whether a write source is active; add the raw
`AgentSession:*` keyspace gauge (Success Criteria) so growth is visible; hand active-write-source
prevention / invisible-hash reaping to #2086 or a follow-up if the classification proves a live
producer. The read/rebuild resilience fix stands regardless.

## Success Criteria

- [ ] With identity-less `AgentSession:*` hashes present, `repair_indexes()` does NOT re-add them
  to `$IndexF:AgentSession:status:pending`; `scard` tracks the true pending count.
- [ ] Deleting a pending record via the ORM leaves no stranded `:pending` index member. **Scope
  note:** *immediate* post-delete correctness is a property of B (delete-ordering). If only A1
  ships (B deferred per Resolved Decision #2), this criterion is satisfied *after the next
  `repair_indexes()` pass* (eventual convergence), not immediately — build B if the delete path
  needs immediate correctness.
- [ ] Normal live creation still indexes: `AgentSession(...).save()` adds the record to
  `$IndexF:AgentSession:status:pending` (guards against the A1 override suppressing legitimate
  new-session indexing — the inverse-bug check).
- [ ] A bloated `:pending` index converges to the true count within one `repair_indexes()` pass
  and stays flat across subsequent passes (no re-inflation).
- [ ] Worker completes startup and holds a stable heartbeat for ≥1h under normal load (verified
  on the affected machine, since this machine cannot reproduce the live leak).
- [ ] **Production accrual-rate delta (affected machine):** two `scard($IndexF:AgentSession:status:pending)`
  samples taken ≥10 min apart after the fix is deployed show a *flat* (non-growing) count under
  normal traffic — the ~250/sec accrual is gone, not merely reset once. A single post-fix `scard`
  snapshot is NOT sufficient (the pre-fix repair also transiently dropped it before it refilled).
- [ ] **Residual-write visibility:** a raw `AgentSession:*` keyspace count (cumulative gauge, e.g.
  a bounded `scan_iter` count logged alongside `quarantined_identityless`) is stable, OR the
  "cap/reap invisible identity-less hashes" concern is explicitly handed to #2086's scope and
  noted here. `quarantined_identityless` (per-pass event count) does not by itself prove the
  underlying hash keyspace is not still climbing from an unfixed write source.
- [ ] Identity-less quarantine is counted + logged; optionally surfaced on the dashboard.
- [ ] No raw-Redis deletion of Popoto-managed keys introduced; `ruff` clean; targeted tests pass.

## Resolved Decisions (post-critique)

1. **Fix altitude for A → A1 (skip the status-field `SADD` for identity-less records).** A1 is
   non-destructive and, critically, avoids the buggy delete path. It is bounded to the `status`
   `IndexedField`'s `on_save` — NOT a reimplementation of popoto's rebuild loop (see Solution A).
2. **Is B independently required? → decide from evidence, not assumption.** The convergence test +
   the gone-hash-orphan test + the pre-build live classification determine it: if the live
   `:pending` sample is dominated by gone-hash orphans, A1 is a no-op for them and B (or repair's
   whole-`$IndexF`-key rebuild) is load-bearing. Build to the classification result.
3. **Origin of identity-less hashes → confirmed as a pre-build read-only classification gate**
   (see Solution A → Pre-build classification). If a live write source is proven, active-source
   prevention / invisible-hash reaping is handed to #2086 or a follow-up (Risk 4); the
   read/rebuild resilience fix stands regardless.
4. **Blast radius → AgentSession-only override** (subclass/wrap the `status` field, or override
   that field instance's `on_save`); a global popoto monkeypatch is a follow-up only if other
   models leak.

## Critique Resolution (2026-07-15)

War room (Risk & Robustness, Scope & Value, History & Consistency, FULL depth) returned
**NEEDS REVISION** — 1 blocker + 3 concerns. All resolved in this revision:

| Severity | Finding | Resolved by |
|----------|---------|-------------|
| BLOCKER (3-critic converge) | A1 "mirrors popoto's scan loop" IS the forbidden full-loop reimplementation | Solution A rewritten: A1 bounds the intervention to the `status` field's `on_save` (skip SADD when `_filter_hydrated_sessions` rejects); every other field + healthy-record rebuild delegated to unmodified `cls.rebuild_indexes()`. Rabbit-Hole resolution now embedded in Solution A itself. |
| CONCERN | Root-cause origin unconfirmed + unfalsifiable verification + residual keyspace growth | Added read-only pre-build classification (gone-hash vs empty-existing) on the affected host; added accrual-rate delta (two `scard` ≥10 min apart) + raw keyspace gauge to Success Criteria; added Risk 4. |
| CONCERN | A2 purge reuses the buggy delete path B fixes | A2 demoted to explicit B-gated fallback; added A2-before-B stranding repro test; A1 reaffirmed as recommendation. |
| CONCERN | Sanctioned-vs-raw-Redis boundary for B undefined | Boundary defined in No-Gos + inline in Solution B: whole-`$IndexF`-key rebuild sanctioned; member-level SREM must route through popoto `on_delete`, never bare raw `srem`. |
