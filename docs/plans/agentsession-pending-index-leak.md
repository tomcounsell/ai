---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-15
tracking: https://github.com/tomcounsell/ai/issues/2101
last_comment_id:
revision_applied: false
revision_applied_at:
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
not legitimate queryable work and must not be `SADD`ed to any status index. Two candidate
altitudes (build to choose, see Open Questions):
- **A1 (skip on re-add):** a repo-owned rebuild that mirrors popoto's scan loop but skips
  `on_save`/`SADD` for any decoded instance failing the hydration check (`_filter_hydrated_sessions`
  predicate — no `session_id`). Healthy records re-index; identity-less hashes are left
  un-indexed (invisible, correct) and counted/logged.
- **A2 (purge before rebuild):** before calling `rebuild_indexes()`, delete the identity-less
  `AgentSession:*` hashes themselves via the ORM (they decode fine — they are near-empty, not
  msgpack-corrupt — so `instance.delete()` works), so the rebuild never sees them. Riskier
  (deletes hashes); A1 is preferred (non-destructive).

Either way: return/log a `quarantined_identityless` count so the drift is observable, and route
the critical scan callers (`cleanup_corrupted_agent_sessions`, dashboard) through it.

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

### Empty/Invalid Input Handling
- [ ] All-healthy: rebuild re-indexes exactly the real pending set; `quarantined == 0`.
- [ ] Empty keyspace: `(0,0,0)`, no crash.

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
  `rebuild_indexes` / sanctioned index-set ops).

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

## Success Criteria

- [ ] With identity-less `AgentSession:*` hashes present, `repair_indexes()` does NOT re-add them
  to `$IndexF:AgentSession:status:pending`; `scard` tracks the true pending count.
- [ ] Deleting a pending record via the ORM leaves no stranded `:pending` index member.
- [ ] A bloated `:pending` index converges to the true count within one `repair_indexes()` pass
  and stays flat across subsequent passes (no re-inflation).
- [ ] Worker completes startup and holds a stable heartbeat for ≥1h under normal load (verified
  on the affected machine, since this machine cannot reproduce the live leak).
- [ ] Identity-less quarantine is counted + logged; optionally surfaced on the dashboard.
- [ ] No raw-Redis deletion of Popoto-managed keys introduced; `ruff` clean; targeted tests pass.

## Open Questions

1. **Fix altitude for A:** skip-on-re-add (A1, non-destructive) vs purge-before-rebuild (A2,
   deletes identity-less hashes). Recommendation: A1.
2. **Is B (delete ordering) independently required,** or does A's rebuild-clears-and-doesn't-re-add
   converge the index on its own? Resolve with the convergence test.
3. **Where do the near-empty/identity-less hashes originate?** (Partial writes during a crash?
   delete-and-recreate races? #2083's save-side audit may inform this.) Confirming the source
   could enable preventing them at the write side, but the read/rebuild resilience fix stands
   regardless.
4. **Blast radius:** AgentSession-only override vs global popoto monkeypatch. Recommendation:
   AgentSession-only now.
