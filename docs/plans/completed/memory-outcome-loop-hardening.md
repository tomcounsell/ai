---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-23
tracking: https://github.com/tomcounsell/ai/issues/2203
last_comment_id: 5048924712
revision_applied: true
revision_applied_at: 2026-07-23T08:02:26Z
---

# Outcome-Loop Hardening: Durable Attribution, Honest Fallback, Activate Pruning

## Problem

Valor's subconscious memory system is supposed to **validate (weight/downweight) and prune memories naturally during regular use** — a hard project constraint. The machinery exists: injection outcomes are LLM-judged and fed into popoto's `ObservationProtocol.on_context_used` (Bayesian confidence updates), and pruning reflections are written. But the loop is degraded in four specific ways, so junk never leaves the corpus and confidence updates are being corrupted.

**Current behavior:**
1. **Outcome attribution dies with the session sidecar.** Injections are tracked in a per-session file sidecar (`injected[]`). Outcomes are judged only at *clean* session stop, then the sidecar is deleted. Crashed/abandoned/killed sessions never reach the stop handler, so their injections receive **no outcome signal at all** — silently lost.
2. **The fallback judge lies optimistically.** When the Haiku outcome judge fails, the bigram-overlap fallback marks *any* keyword overlap as `acted`. False "acted" signals corroborate confidence on memories that were never actually used — poisoning the exact signal the system learns from.
3. **Pruning is dark.** All three maintenance reflections are scheduled but never actually mutate the corpus: `memory-decay-prune` is dry-run unless `MEMORY_DECAY_PRUNE_APPLY` is set; `memory-dedup` defaults to `dry_run=True`; and `memory-embedding-backfill` — though it **does** have an entry in `reflections.yaml` (config `:305`, vault `:305`, `enabled: true`, daily) — is likewise dry-run unless `MEMORY_EMBEDDING_BACKFILL_APPLY` is set, so vectorless records are never re-embedded. Zero records have ever been auto-pruned or re-embedded in apply mode.
4. **Dismissal downweighting is slow relative to noise.** Importance decays only after 3 *consecutive* dismissals, and a single interleaved false "acted" (defect 2) resets the counter. Example: "Ahhh" (dismissed ×2) still sits at importance 6.0 in production.

**Desired outcome:** every injection eventually gets an honest outcome (or an explicit neutral `deferred`), and low-value memories demonstrably leave the active corpus with zero human action. Signal integrity outranks signal volume — when in doubt, resolve neutral.

## Freshness Check

**Baseline commit:** `dee0e1e2b77ec61d1f3d838bc7d59685c20e5a2f` (re-verified at HEAD during the 3rd revision pass; earlier baseline `3c0fc7ee`)
**Issue filed at:** 2026-07-22T04:32:06Z
**Disposition:** Minor drift (line numbers moved after #2215; both prerequisites landed — favorable). **Correction:** the `memory-embedding-backfill` "missing entry" premise was FALSE — the entry exists but runs dry-run (see below).

**File:line references re-verified against baseline:**
- `.claude/hooks/hook_utils/memory_bridge.py` injected-tracking — issue cited `:604-613`; now at ~`:611-613` (`injected.extend(new_entries)` + `_save_sidecar`). Claim holds.
- `memory_bridge.py` outcome-at-stop + sidecar cleanup — issue cited `:839`/`:886-907`; now `detect_outcomes_async` invoked at `:897`, `cleanup_sidecar` defined at `:907` and called in the stop handler's `finally` at `:904`. Claim holds: cleanup only runs on the clean-stop path.
- `agent/memory_extraction.py` optimistic fallback — issue cited `:1402-1410`; now the bigram fallback at ~`:1390-1400` sets `outcome_map[memory_key] = "acted"` on any `overlap`. Claim holds.
- `agent/memory_extraction.py` dismissal decay — issue cited `:1321-1339`; now `DISMISSAL_DECAY_THRESHOLD` check + reset-on-acted at ~`:1305-1330`. Claim holds. Constants live in `config/memory_defaults.py:106-111`.
- `reflections/memory/memory_decay_prune.py` apply gate — `MEMORY_DECAY_PRUNE_APPLY` env, dry-run default for both tiers (`:106-117`). Claim holds.
- `config/reflections.yaml:140` dedup dry-run — `memory-dedup` at `:140`, callable `scripts.memory_consolidation.run_consolidation` whose `dry_run: bool = True` default (`scripts/memory_consolidation.py:445`). Claim holds.
- `memory-embedding-backfill` **already scheduled but dry-run** — re-verified at HEAD (`dee0e1e2`): `grep -c "embedding-backfill" config/reflections.yaml` == **1** (entry at `config/reflections.yaml:305` and vault `~/Desktop/Valor/reflections.yaml:305`, `enabled: true`, `every: 86400s`, callable `reflections.memory_management.run_memory_embedding_backfill`). The reflection's `run()` (`reflections/memory/memory_embedding_backfill.py:52,63`) reads `MEMORY_EMBEDDING_BACKFILL_APPLY` from env and defaults to dry-run, so vectorless records are never actually re-embedded. **The earlier "== 0 / never scheduled" claim was stale**: `config/reflections.yaml` is gitignored/vault-synced and absent from the repo tree at the older baseline commit, so a repo-only grep false-returned 0. The real defect is dry-run-by-default (an unset apply env var), identical to decay-prune/dedup — so backfill is now the **third apply-mode activation target** (params-driven, env-as-kill-switch), not a missing-entry add.

**Cited sibling issues/PRs re-checked:**
- #2200 (Phase 1 telemetry) — **CLOSED 2026-07-22T14:12:09Z** via PR #2210. This is the prerequisite for apply-mode pruning (deletions are now observable in `/memories/metrics.json`). Unblocked.
- #2201 (Phase 2 write-gate) — **MERGED 2026-07-22T19:11:53Z** via PR #2215. Established the `models/memory_gate.py::_increment_gate_counter` + `{project_key}:memory-gate:{reason}` Redis-counter pattern and `_sum_gate_counter` in `ui/data/memories.py`. This issue's activated pruning/dedup should **reuse that counter pattern** for `prune_count`/`dedup_merge_count` rather than inventing a new telemetry mechanism (per the upstream comment on this issue).

**Commits on main since issue was filed (touching referenced files):**
- `e563efd19` Unify memory write-path quality gates (#2215) — touched `models/memory.py`, `agent/memory_extraction.py`, `ui/data/memories.py`. Changed root cause? No — it prevents *new* junk at write time; it explicitly deferred pruning of the 59 pre-existing fragment records to **this** issue. Complementary, not overlapping.

**Active plans in `docs/plans/` overlapping this area:** none (memory-write-gate-unification and memory-telemetry-baseline already migrated/shipped).

**Notes:** #2215's write-gate is upstream and complementary. The write-gate stops the inflow; this issue drains the standing pool and hardens the learn-from-use loop. No premise changed.

## Prior Art

- **#2200 / PR #2210**: Memory telemetry baseline (Phase 1) — shipped the corpus-metrics JSON export. **Prerequisite met**: apply-mode pruning is now observable.
- **#2201 / PR #2215**: Write-path quality gates (Phase 2) — content-gates all five writer paths, deletes the newline-splitting fallback. Established the reusable Redis gate-counter telemetry pattern. Complementary to this issue.
- **#1822 / PR #1831**: Closed three systematic extraction noise sources + a GC tier. Relevant: prior work on noise reduction at extraction time; this issue closes the *outcome-loop* leaks that #1822 didn't touch.
- **#795 / memory-dedup**: LLM-based semantic dedup with drift safety rails — the dedup reflection this issue flips to apply mode. Design intent was "dry-run 14 days, then apply after ≥95% human agreement." That review window has long passed.
- **#1231**: Memory health audit reflection — sibling reflection infrastructure; no conflict.

## Spike Results

### spike-1: `deferred` is a first-class ObservationProtocol outcome
- **Assumption**: "The honest-fallback fix can emit `deferred` (neutral) and popoto will handle it without effects."
- **Method**: code-read (`popoto/fields/observation.py`)
- **Finding**: `VALID_OUTCOMES = {"acted", "dismissed", "deferred", "contradicted", "used"}`. `on_context_used` defaults unmapped instances to `"deferred"`. `_apply_deferred` = "No effects, pressure builds" (docstring `:18`). Confirmed at `observation.py:56,171,179,220`.
- **Confidence**: high
- **Impact on plan**: The fallback fix is a one-line semantic change (`"acted"`→`"deferred"`) plus a `deferred` branch in `_persist_outcome_metadata` (which currently only handles acted/used/dismissed). No popoto changes needed.

### spike-2: reflection scheduler forwards a `params` dict to callables that accept it
- **Assumption**: "Apply-mode can be activated from `reflections.yaml` config rather than requiring an operator to set an env var (subconscious constraint: no load-bearing operator step)."
- **Method**: code-read (`agent/reflection_scheduler.py`)
- **Finding**: Registry entries carry an optional `params: dict` (`:127`), forwarded as `func(params=entry.params)` **only if** the callable's signature contains a `params` parameter (`:441-448`). Callables without `params` are called zero-arg. `run_memory_decay_prune` (= `memory_decay_prune.run`) and `run_consolidation` do **not** currently accept `params` — decay-prune reads `MEMORY_DECAY_PRUNE_APPLY` from env; consolidation takes `dry_run=True` positionally.
- **Confidence**: high
- **Impact on plan**: To make activation config-driven (not env/operator-driven), the two run-callables must grow a `params` kwarg that maps `params={"apply": True}` → apply mode. Env vars stay as emergency-brake overrides only.

### spike-3: the crashed-session sidecar survives as the durable record
- **Assumption**: "Crashed sessions leave a recoverable artifact we can resolve late, without building new storage."
- **Method**: code-read (`memory_bridge.py` stop handler)
- **Finding**: `cleanup_sidecar(session_id)` runs **only** in the `finally` of the Stop-hook handler (`:904-907`). A crashed/killed session never reaches the Stop hook, so its sidecar file (containing full `injected[]`) is left on disk under the session dir. The stale sidecar **is** the durable journal — no new Popoto model or Redis namespace is required to recover lost injections.
- **Confidence**: high
- **Impact on plan**: Durable attribution = a sweep reflection over stale/orphaned sidecars that resolves unresolved injections to `deferred` and then cleans them up. Avoids a schema migration entirely (see Rabbit Holes for the rejected Popoto-model alternative).

## Data Flow

1. **Injection**: `memory_bridge` recall injects `<thought>` stubs → appends `{memory_id, content}` to sidecar `injected[]` → `_save_sidecar` (file per session).
2. **Session runs**: agent produces `response_text`.
3. **Clean stop (today)**: Stop hook → `detect_outcomes_async(injected_tuples, response_text)` → Haiku judge (or bigram fallback) → `outcome_map` → `_persist_outcome_metadata` (dismissal_count / importance) + `ObservationProtocol.on_context_used` (confidence/decay) → `cleanup_sidecar`.
4. **Crash/kill (today — BROKEN)**: process dies before step 3 → sidecar orphaned → injections never judged → **signal lost**.
5. **After fix**: step 4's orphaned sidecar is picked up by the new `memory-outcome-resolve` reflection → unresolved injections resolved to `deferred` (neutral) → cleanup. Step 3's fallback path now emits `deferred` instead of false `acted`.
6. **Pruning (after fix)**: `memory-decay-prune` (apply — **tier-1 hard-deletes** below-write-floor records that can't be tombstoned, **tier-2 tombstones** via `superseded_by`) + `memory-dedup` (apply) + `memory-embedding-backfill` (apply — re-embeds vectorless records) run on the reflection tick → low-confidence/zero-access records leave the corpus and vectorless records regain embeddings → counts surface in `/memories/metrics.json` via the reused gate-counter pattern (incremented only on a persisted removal — never a phantom).
7. **Dismissal-dominated exit (after fix)**: a *previously-accessed* record (access_count > 0, so excluded from both prune tiers) that is repeatedly `dismissed` decays toward `MIN_IMPORTANCE_FLOOR` and, once floored with a 0% act rate, is superseded directly in the dismissal-decay path (`_persist_outcome_metadata`) — the corpus exit for the flagship "Ahhh"-class record that the access_count==0 prune gates can never reach.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|-----------------------|
| #1822 / PR #1831 | Closed three extraction-time noise sources + GC tier | Addressed *inflow* at extraction; never touched the outcome→confidence loop or activated pruning. |
| memory-dedup (#795) | Built LLM dedup with a dry-run safety period | The "flip to apply after review" step was never taken — the reflection has run dark for months. |
| memory-decay-prune | Built two-tier decay/noise pruning gated on env | Env gate (`MEMORY_DECAY_PRUNE_APPLY`) was never set in any worker environment, so apply mode never engaged — and env-gating violates the "no load-bearing operator step" constraint. |

**Root cause pattern:** the loop was *built* but its activation switches were left off — every maintenance reflection (decay-prune, dedup, embedding-backfill) is scheduled and `enabled: true` yet defaults to dry-run behind an unset apply env var — and its degraded paths (crash-loss, optimistic fallback) manufacture or drop signal. This is a **hardening + activation** issue, not a construction one.

## Architectural Impact

- **New dependencies**: none (all machinery exists: sidecars, ObservationProtocol, reflection scheduler, gate-counter module).
- **Interface changes**: `memory_decay_prune.run`, `run_consolidation`, **and `memory_embedding_backfill.run`** each gain an optional `params: dict | None = None` kwarg (backward-compatible; env stays as kill-switch override). `run_consolidation` also gains a **new optional `MEMORY_DEDUP_APPLY` env kill-switch** (it has none today — see CONCERN 1 below) so the env-as-kill-switch precedence is genuinely uniform across all three targets. `detect_outcomes_async` fallback branch changes emitted outcome. `_persist_outcome_metadata` gains a `deferred` branch **and a dismissal-dominated supersede exit**. `_GATE_COUNTER_FIELDS` (`ui/data/memories.py`) gains two tuples so prune/merge counts surface in metrics.
- **Coupling**: *decreases* — outcome resolution is decoupled from the sidecar-delete lifecycle. Activation moves from scattered env vars to the single `reflections.yaml` registry.
- **Data ownership**: unchanged. Sidecars remain the injection journal; the new reflection is a late resolver, not a new owner.
- **Reversibility**: mixed-but-bounded. Tier-2 prune, dedup, and the dismissal-dominated exit all **tombstone** (supersede, reversible). **Tier-1 prune hard-deletes** — irreversible — but only records whose importance is *below the 0.15 write floor*, i.e. records the current write gate would refuse to admit at all; a tombstone-via-`save()` is mechanically impossible for them (see BLOCKER below), so hard-delete is the only persistable removal. Blast radius stays bounded by `MAX_PRUNE_PER_RUN` and observable via #2200 metrics. The fallback change is one line; the sweep reflection can be disabled in `reflections.yaml`.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm tombstone-first semantics and apply-mode activation are safe given #2200 baseline)
- Review rounds: 1 (this touches the confidence-learning signal — precision-sensitive)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Phase 1 metrics baseline (#2200) | `test -f ui/data/memories.py` | Metrics export (deletions must be observable before apply-mode pruning) shipped via #2210 |
| popoto ObservationProtocol with deferred | `.venv/bin/python -c "from popoto.fields.observation import VALID_OUTCOMES; assert 'deferred' in VALID_OUTCOMES"` | Honest-fallback fix depends on deferred |
| Redis reachable (gate-counter reuse) | `.venv/bin/python -c "from popoto.redis_db import POPOTO_REDIS_DB as r; r.ping()"` | prune/dedup counters use the gate-counter namespace |

Run all checks via `python scripts/check_prerequisites.py docs/plans/memory-outcome-loop-hardening.md`.

## Solution

### Key Elements

- **Durable attribution (`memory-outcome-resolve` reflection)**: A scheduled sweep that finds stale session sidecars (mtime older than `INJECTION_RESOLVE_TTL` — TTL-only gating, no liveness dependency; see Technical Approach), resolves their unresolved `injected[]` entries to `deferred` (neutral — pressure builds, never a false positive), feeds them through `ObservationProtocol.on_context_used`, and cleans up. Reuses the existing sidecar as the journal (spike-3); no new storage.
- **Honest fallback**: In `detect_outcomes_async`, the bigram-overlap fallback emits `deferred` for **every** injection (never `acted`, never `dismissed`). A cheap heuristic must not manufacture positive or negative corroboration. Add a `deferred` branch to `_persist_outcome_metadata`.
- **Activate pruning (config-driven, safety-railed)** — three apply-mode targets, all dry-run-by-default today:
  - Give `memory_decay_prune.run`, `run_consolidation`, **and `memory_embedding_backfill.run`** a `params` kwarg so `params={"apply": true}` in `reflections.yaml` engages apply mode (env vars are emergency-brake overrides under the env-as-kill-switch rule; **`run_consolidation` has no env var today, so this pass adds an optional `MEMORY_DEDUP_APPLY` kill-switch** — see CONCERN 1).
  - The `memory-embedding-backfill` entry **already exists** (config/vault `:305`, `enabled: true`, daily) — do NOT add a duplicate. Just set `params: {apply: true}` on the existing line-305 entry and grow its callable a `params` kwarg (mapping `params["apply"]` → the same `apply_mode` boolean it currently derives from `MEMORY_EMBEDDING_BACKFILL_APPLY`, env-as-kill-switch). **Scope note (CONCERN 5):** backfill *adds* recall signal (the inverse of pruning), yet it belongs here because the issue's Acceptance Criteria explicitly require "memory-embedding-backfill runs on the reflection schedule," and all three maintenance reflections share the *identical* activation defect (dry-run behind an unset apply gate) and the *identical* fix seam (a `params` kwarg on the run-callable, env-as-kill-switch). Activating the three together is one coherent "flip the dark maintenance reflections to apply" workstream, not orthogonal scope-creep — the boundary is intentional.
  - **BLOCKER FIX — tier-split removal, mechanically-correct per tier.** The decay-prune reflection has ONE shared `.delete()` call site (`memory_decay_prune.py:219`) looping over the union of tier-1 (decay) and tier-2 (#1822 noise) candidates. **Tier-1 candidates have importance < `WF_MIN_THRESHOLD` (0.15)** — and popoto's `WriteFilterMixin._check_write_filter()` raises `SkipSaveException` for *any* save (INSERT **or** UPDATE) whose `compute_filter_score()` (= importance) is below 0.15 (`popoto/models/base.py:1093-1097`; verified empirically: `Memory(importance=0.10).save()` returns `False`, record absent from Redis). So a `superseded_by=…; save()` tombstone on a tier-1 record **silently no-ops** — the tombstone never persists while a naively-incremented `prune_count` reports a phantom prune. Therefore:
    - **Tier-1 → hard-delete** (`memory.delete()`). These records sit below the write floor — the write gate would refuse to admit them at all, and a tombstone is impossible for them — so hard-delete is the only persistable removal. Increment `prune_count` **only after `delete()` returns without raising** (no phantom count).
    - **Tier-2 → tombstone** (`superseded_by` sentinel + `save()`). Tier-2 importance is ≥ 0.15 (at/above the write floor), so `save()` persists. This is strictly safer/reversible than tier-2's old hard-delete (#1822). Increment `prune_count` **only when `save()` returns a truthy result** (guard against a `False`/filtered return, so a filtered save never phantom-counts).
    - Split the shared loop so each tier runs its own removal + gated count. Respect the importance floor and `MAX_PRUNE_PER_RUN` cap already in place. This *supersedes* the prior revision's "both tiers tombstone" resolution, which was mechanically impossible for tier-1.
  - Emit `prune_count` / `dedup_merge_count` counters via the existing `_increment_gate_counter` pattern, keyed by each record's own `project_key`, **only on a persisted removal** (per the per-tier gating above); **append both `(reason, field)` tuples to `_GATE_COUNTER_FIELDS` (`ui/data/memories.py:240`)** so they surface in `/memories/metrics.json` (without that append the summing loops at `:388`/`:414` never emit them).
- **Dismissal decay (verified, not just asserted)**: leave the decay constants in `config/memory_defaults.py` as-is (already named/env-overridable). The *fix* for defect 4 is indirect — the honest fallback (above) stops manufacturing false `acted` resets, so the existing "decay after `DISMISSAL_DECAY_THRESHOLD` consecutive dismissals" path (`agent/memory_extraction.py:1425-1441`) becomes trustworthy. Because there's no direct code change to the decay math, the plan **proves** the path with a regression test: drive **3 sequential `dismissed` outcomes** for one record through `_persist_outcome_metadata` (with `DISMISSAL_DECAY_THRESHOLD == 3`) and assert (a) `dismissal_count` increments 1→2→3, (b) importance decays by `DISMISSAL_IMPORTANCE_DECAY` (floored at `MIN_IMPORTANCE_FLOOR`) on the 3rd, and (c) `dismissal_count` resets to 0 after decay — and a companion assertion that an interleaved `deferred` (the new fallback outcome) does **not** reset the counter mid-run (only `acted` resets it). Add a comment noting the reset-on-`acted` rule is now trustworthy.
- **Dismissal-dominated corpus exit (CONCERN 3 — the "Ahhh"-class record)**: the flagship dismissed record is importance 6.0, dismissed ×2, and **has been recalled** (`access_count > 0`). Both prune tiers require `access_count == 0`, and `MIN_IMPORTANCE_FLOOR == 0.2` sits **above** `WF_MIN_THRESHOLD == 0.15`, so dismissal decay floors it at 0.2 and it *never* enters tier-1's `< 0.15` band — it has **no prune exit at all** through the reflection. Acceptance Criterion 5 (a junky record must leave the corpus with zero human action) depends on closing this gap. The exit lives in the dismissal-decay path itself, cleanly divided from the prune reflection: the reflection prunes *never-accessed* junk (`access_count == 0`); the decay path retires *previously-accessed, repeatedly-dismissed* junk. In `_persist_outcome_metadata`, when a `dismissed` outcome decays a record that is **already at `MIN_IMPORTANCE_FLOOR`** (further decay can't lower it) **and** whose outcome history shows a **0% act rate** (`compute_act_rate(...) == 0.0`) over at least `DISMISSAL_DECAY_THRESHOLD` recorded outcomes, **supersede it** (`superseded_by` sentinel + `save()`) and emit `prune_count`. Because a floored record sits at 0.2 (≥ 0.15, above the write floor), this tombstone `save()` persists (unlike a tier-1 record) — mechanically sound. Recall already filters superseded records, and the prune reflection skips them, so no double-handling. This is the corpus exit for high-importance, previously-accessed, dismissal-dominated records.

### Flow

Session crashes → sidecar orphaned on disk → `memory-outcome-resolve` tick finds it (mtime past TTL) → unresolved injections → `deferred` → confidence pressure builds (no false signal) → cleanup.

Reflection tick → `memory-decay-prune` (apply, tombstone) + `memory-dedup` (apply) + `memory-embedding-backfill` → low-value records superseded/re-embedded → counts in metrics.json.

### Technical Approach

- **Sweep reflection** (`reflections/memory/memory_outcome_resolve.py`, wired through `reflections/memory_management.py`): iterate the sidecar directory (`_get_sidecar_dir` root), select sidecars whose mtime exceeds `INJECTION_RESOLVE_TTL` (new named constant in `config/memory_defaults.py`, provisional/tunable per the magic-numbers convention). For each, load the `Memory` instances named by the sidecar's unresolved `injected[]` `memory_id`s (`Memory.query.filter(...)` / `Memory.get(...)`), then call `ObservationProtocol.on_context_used(instances, outcome_map)`. **`on_context_used`'s contract keys `outcome_map` by each instance's Redis key, NOT by `memory_id`** (popoto `observation.py:141,149-151,177-180` — `_get_instance_key(instance)` returns `instance._redis_key` or `instance.db_key.redis_key`; unmatched instances default to `"deferred"`). Reuse the **exact pattern the clean-stop path already uses** at `agent/memory_extraction.py:1542-1552`: build `redis_outcome_map[m.db_key.redis_key] = "deferred"` and pass `(instances, redis_outcome_map)`. Passing a `memory_id`-keyed map would silently fall through to the `"deferred"` default for every instance — masking the mis-key while the *test* (Success Criterion 1) still passes for the wrong reason — so the plan mandates redis-key keying and a test that asserts the resolved outcome via the record's observation state, not merely "no crash." Idempotent, fail-silent, per-run cap.
  - **Compare-and-delete cleanup (CONCERN 2 — don't clobber a resuming session).** The existing `cleanup_sidecar` (`memory_bridge.py:930`) unlinks **blindly** (`filepath.unlink()` at `:947`). A crashed session can be *resumed*: on resume, `recall()` rewrites the sidecar (`:615`) with fresh `injected[]`. If the sweep read the stale sidecar, resolved its injections, then blindly unlinked, it would **destroy the resumed session's new injections** (they never get an outcome). So the sweep must NOT call the blind `cleanup_sidecar`; it does its own **compare-and-delete**: capture `mtime_at_read = filepath.stat().st_mtime` when it loads the sidecar, and immediately before unlinking, re-`stat()` the file and unlink **only if `current_mtime == mtime_at_read`**. If the mtime changed (a resume rewrote it), leave the sidecar in place — a later sweep or the resumed session's own Stop hook will handle it. This keeps the sweep safe against the resume race without any liveness primitive.
- **Liveness gating — TTL-only**: there is **no** session-liveness helper importable from `memory_bridge.py` (grep confirms: no `is_session_live`/`session_live`/`is_live` symbol; only an unrelated `AgentSession` reference in a comment at `:920`). Rather than build a new cross-process liveness primitive mid-task (which would contradict the "hardening, not construction" / "no new dependencies" framing), the sweep gates **solely on `mtime > INJECTION_RESOLVE_TTL`**.
  - **Verified mtime-refresh assumption:** the TTL gate is only safe if a live session keeps its sidecar's mtime fresh. Confirmed in code: `recall()` (`memory_bridge.py:450`) rewrites the sidecar via `_save_sidecar` (`:615`) every time it appends injected `<thought>` entries, and `recall` runs per-turn through the PostToolUse hook. So each recall-injection touches the file and pushes mtime forward. **Caveat:** mtime is refreshed on *injection*, not merely on "any turn" — a live session that runs a long stretch with no new recall injection does not refresh it. Therefore `INJECTION_RESOLVE_TTL` must exceed the **maximum plausible gap between recall injections** in a live session (not just a single turn); the value is set with that headroom and marked provisional/tunable. Even if the bound is mis-estimated, `deferred` is a no-op outcome (spike-1), so a premature resolve on a just-past-TTL live sidecar causes no confidence corruption — the TTL protects against churn, not correctness. The "session-not-live" conjunct is dropped.
- **Fallback fix**: `agent/memory_extraction.py` `detect_outcomes_async` — replace the `if overlap: "acted" else: "dismissed"` block with `outcome_map[memory_key] = "deferred"` for all fallback injections. Add `elif outcome == "deferred":` (no-op on dismissal_count) in `_persist_outcome_metadata`.
- **Apply activation (env-as-kill-switch precedence)**: all **three** run-callables — `memory_decay_prune.run`, `run_consolidation`, and `memory_embedding_backfill.run` — gain a `params` kwarg. The single, canonical precedence rule — stated identically in Risk 1 and Success Criteria — is **env-as-kill-switch**: an explicitly-set env var always wins (it can force apply OR force dry-run); when the env var is unset (the normal production posture), `params` governs. In code: `apply = env_value if env_explicitly_set else params.get("apply", False)`. This is why activation is config-driven by default yet an operator retains an emergency brake. **For the invariant "env-as-kill-switch for all three targets" to be *true*, each of the three needs an env var** — decay-prune and backfill already have one; **`run_consolidation` has none today**, so this pass adds one (CONCERN 1, below).
  - **Consolidation / dedup (`run_consolidation`) — CONCERN 1.** Today `run_consolidation(dry_run: bool = True)` (`scripts/memory_consolidation.py:443-445`) has **no env kill-switch at all** — its only apply toggle is the CLI's `args.apply` (`:569-572`). Leaving it out would make the plan's "env-as-kill-switch for all three targets" invariant false. Fix: add an optional **`MEMORY_DEDUP_APPLY`** env kill-switch and a `params` kwarg, wired to the same rule: `apply = <MEMORY_DEDUP_APPLY if set> else params.get("apply", False)`, then `dry_run = not apply`. The CLI `--apply` path stays (it maps to `params={"apply": true}`). This makes the emergency-brake symmetry real across all three targets rather than asserted for a target that lacks the mechanism.
  - **Backfill (`memory_embedding_backfill.run`).** Today it derives `apply_mode = os.environ.get("MEMORY_EMBEDDING_BACKFILL_APPLY", "false") in (...)` (`:63`). Rewrite to the same kill-switch rule against `MEMORY_EMBEDDING_BACKFILL_APPLY`, falling back to `params.get("apply", False)` when the env var is unset. The `memory-embedding-backfill` `reflections.yaml` entry already exists and is `enabled: true`; the ONLY yaml change is adding `params: {apply: true}` to the existing line-305 entry (no new entry). Note `run()` is currently zero-arg (`async def run() -> dict`) — the scheduler only forwards `params=` when the signature contains a `params` parameter (spike-2), so adding `params: dict | None = None` is what makes the yaml `params` reach it.
  - **A single `params={"apply": true}` engages BOTH decay-prune tiers.** `memory_decay_prune.run` computes two independent booleans today — `decay_apply` (tier-1, env `MEMORY_DECAY_PRUNE_APPLY`) and `noise_apply` (tier-2, env `MEMORY_NOISE_PRUNE_APPLY`). Each is rewritten to the same kill-switch rule against its OWN env var, falling back to the shared `params.get("apply", False)` when its env var is unset: `decay_apply = <MEMORY_DECAY_PRUNE_APPLY if set> else params.get("apply", False)` and `noise_apply = <MEMORY_NOISE_PRUNE_APPLY if set> else params.get("apply", False)`. So `params={"apply": true}` from `reflections.yaml` turns on both tiers at once, while either env var can independently veto (force dry-run) or force-enable its tier.
  - **Removal mechanism per tier (BLOCKER).** Split the shared prune loop (`memory_decay_prune.py:183-226`) so each tier is iterated separately:
    - **Tier-1 (importance < 0.15) → hard-delete** (`memory.delete()`). A tombstone-via-`save()` is mechanically impossible: `WriteFilterMixin._check_write_filter()` (`popoto/models/base.py:1093-1097`) raises `SkipSaveException` on *any* save whose importance is below 0.15, so `superseded_by=…; save()` would return `False` and persist nothing while a naive counter reports a phantom prune. Empirically confirmed: `Memory(importance=0.10).save()` returns `False`, record absent from Redis. These records are below the write-admission floor anyway (the write gate would refuse them), so hard-delete is the correct — and only persistable — removal. **Increment `prune_count` only after `delete()` returns without raising.**
    - **Tier-2 (0.15 ≤ importance ≤ 1.0) → tombstone** (`superseded_by` sentinel + `save()`). Tier-2 importance is at/above the write floor, so `save()` persists. Strictly safer/reversible than tier-2's old hard-delete (#1822). **Increment `prune_count` only when `save()` returns a truthy result** (a `False`/filtered return must not phantom-count).
    - `superseded_by` is the tombstone sentinel; recall already filters superseded records (`memory_decay_prune.py:143`), and the shared `MAX_PRUNE_PER_RUN` cap and importance floor stay in force for both tiers. Update the module docstring (`:22-24`, `:29-31`) to state tier-1 hard-deletes below-floor records and tier-2 tombstones. **This supersedes the prior revision's "both tiers tombstone" resolution, which the write filter makes impossible for tier-1.**
- **Counters**: `prune_count`, `dedup_merge_count` via `models/memory_gate.py::_increment_gate_counter(project_key, reason)`. Each pruned/merged record is a `Memory` instance carrying its own `memory.project_key` (records are queried by `Memory.query.filter(project_key=pk)` and `_sum_gate_counter` sums `{project_key}:memory-gate:{reason}` over resolved keys). Increment **per-record with that record's `project_key`** — `_increment_gate_counter(memory.project_key or DEFAULT_PROJECT_KEY, "prune_count")` — so the `{project_key}:memory-gate:{reason}` layout stays intact and `ui/data/memories.py::_sum_gate_counter(reason, pks)` aggregates correctly per project. Do **not** thread a single ambient `project_key`: that would silently misattribute counts. **Null/empty `project_key` handling:** a record with a null or empty `project_key` would write to a `:memory-gate:{reason}` key that `_sum_gate_counter` never sums (its pk list is the set of resolved project keys), so those increments would silently vanish from `/memories/metrics.json`. Coalesce null/empty to the corpus default (`config.memory_defaults.DEFAULT_PROJECT_KEY == "default"`, already imported into `ui/data/memories.py`) at both the increment site and in `_sum_gate_counter`'s pk list, so no prune/merge is under-counted. **Surfacing step (required):** `get_corpus_metrics` emits gate counters by iterating `_GATE_COUNTER_FIELDS` (`ui/data/memories.py:240`, currently a 4-tuple of `(reason, output_field)` consumed by the summing loops at `:388` and `:414`). Append `("prune_count", "prune_count")` and `("dedup_merge_count", "dedup_merge_count")` to that tuple — **without this append the counters are incremented in Redis but never read into `/memories/metrics.json`.** The `_increment_gate_counter(pk, reason)` writes `{pk}:memory-gate:{reason}` on the same namespace `_sum_gate_counter` reads, so the append is the sole wiring needed on the read side.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The memory loop is fail-silent by design (`except Exception` in `detect_outcomes_async`, `_persist_outcome_metadata`, `memory_bridge`). Each new code path (sweep reflection, `deferred` branch) must assert *observable* behavior on the happy path (a `logger.debug`/counter increment or a state change on the Memory record), not just "didn't crash".
- [ ] Sweep reflection: assert it logs a resolved count and does NOT raise when a sidecar is malformed/partial (crashed mid-write).

### Empty/Invalid Input Handling
- [ ] `detect_outcomes_async` with empty `injected_thoughts` or empty `response_text` → returns `{}` (already guarded; add regression test).
- [ ] Sweep reflection with an empty sidecar dir → no-op, returns zero count.
- [ ] Sweep reflection with a sidecar containing `injected: []` → no-op cleanup.

### Error State Rendering
- [ ] `/memories/metrics.json` renders `prune_count`/`dedup_merge_count` as `0` (not missing/errored) when no pruning has occurred yet.

## Test Impact

- [ ] `tests/unit/test_memory_extraction.py` (outcome-detection cases) — UPDATE: any test asserting the bigram fallback yields `acted` on overlap must be changed to assert `deferred`. Search for `"acted"` assertions in fallback-path tests.
- [ ] `tests/**` outcome/persist tests — UPDATE: add a `deferred` case to `_persist_outcome_metadata` coverage (dismissal_count unchanged).
- [ ] `tests/**` reflections registry tests (e.g. `test_reflections_yaml_*`) — UPDATE: the new `params` blocks (on the existing `memory-embedding-backfill`/`memory-decay-prune`/`memory-dedup` entries) and the new `memory-outcome-resolve` entry must still parse/validate; confirm the schema-validation migration accepts the `params` field shape.
- [ ] New tests (REPLACE/ADD): `test_memory_outcome_resolve.py` (orphaned-sidecar sweep → deferred keyed by redis_key not memory_id; **compare-and-delete leaves a resumed/mtime-bumped sidecar intact** — CONCERN 2), `test_decay_prune_apply_params.py` (params→apply; **tier-1 hard-deletes → record absent from `Memory.query`, `prune_count` == actual removals with no phantom** — BLOCKER; tier-2 tombstones via `superseded_by`; cap respected), `test_dedup_apply_params.py` (**includes `MEMORY_DEDUP_APPLY` env-kill-switch precedence** — CONCERN 1), `test_backfill_apply_params.py` (params→apply re-embeds a seeded vectorless record; env-as-kill-switch precedence).
- [ ] `tests/unit/test_memory_extraction.py` — ADD dismissal-decay regression (3 sequential `dismissed` → decay at threshold + reset; interleaved `deferred` does not reset) — CONCERN 3a. ADD dismissal-dominated exit (`dismissal_prune` marker): a previously-accessed (`access_count > 0`), floored, 0%-act-rate record is superseded on the next `dismissed` and `prune_count` increments — CONCERN 3b.

If a grep shows no existing test asserts `acted` in the fallback path, state so in the build and add fresh coverage — the fallback path is currently under-tested, which is how the optimistic bug survived.

## Rabbit Holes

- **Building a new Popoto `MemoryInjection` model + migration for the journal.** Rejected: the crashed-session sidecar already IS the durable record (spike-3). A schema migration is disproportionate; the sweep reflection is lighter and reversible.
- **Re-embedding provider/model selection for backfill.** `memory_embedding_backfill.run` already probes the provider and is already scheduled+`enabled`; don't re-litigate embedding infra here — just flip it to apply mode via `params` (env-as-kill-switch), same as the other two reflections.
- **Tuning the dismissal-decay constants** (threshold, decay factor, floor). Tempting now that the fallback is honest, but changing learning constants deserves its own observation window. Leave as-is with a comment; revisit after the honest signal has run.
- **Hard-deleting junk immediately.** Resist. Tombstone-first (supersede) is the safe activation; hard-delete can follow once apply-mode tombstoning is trusted against the #2200 metrics.
- **Building a cross-process session-liveness primitive for the sweep.** Rejected: no liveness helper exists in `memory_bridge.py`, and TTL-only gating is sufficient and dependency-free (see Technical Approach). Constructing one would contradict the "hardening not construction" framing.
- **Judging crashed-session injections against a partial response with the LLM.** Overkill and expensive; there may be no coherent response to judge. `deferred` is the correct neutral resolution.

## Risks

### Risk 1: Apply-mode pruning removes a record that was actually valuable
**Impact:** A useful memory disappears from recall.
**Mitigation:** Removal is **tombstone-first wherever the write filter permits a persisting `save()`** — tier-2 noise, dedup, and the dismissal-dominated exit all supersede (reversible). **Tier-1 decay records (importance < 0.15) hard-delete** because a tombstone `save()` cannot persist below the 0.15 write floor (BLOCKER, Technical Approach); the loss is bounded — these records sit below the write-admission floor (the gate would refuse to re-admit them) and every run is capped by `MAX_PRUNE_PER_RUN` and made observable by #2200 metrics. `prune_count` increments **only on a persisted removal** (delete succeeded / tombstone `save()` returned truthy), so a filtered save never inflates the metric. Emergency brake follows the **env-as-kill-switch** precedence: an explicitly-set `MEMORY_DECAY_PRUNE_APPLY`/`MEMORY_NOISE_PRUNE_APPLY`/`MEMORY_DEDUP_APPLY` always wins over `params` — setting any to `false` force-disables its target back to dry-run, independent of the `params={"apply": true}` in `reflections.yaml`.

### Risk 2: Sweep reflection resolves a still-live session's sidecar as `deferred`
**Impact:** A live session's injections get a premature neutral outcome; the clean-stop path would then double-resolve.
**Mitigation:** Gate on a generous `INJECTION_RESOLVE_TTL` (longer than the max plausible gap between recall injections, verified per Technical Approach — `recall()` rewrites the sidecar on every injection at `memory_bridge.py:615`) — TTL-only, no liveness dependency. A live session's sidecar mtime is pushed forward by each recall injection, so it stays under TTL and is never eligible. `deferred` is idempotent-safe (no effects), and cleanup removes the sidecar so the stop handler finds nothing to double-count even in the rare TTL-boundary overlap.

### Risk 3: `reflections.yaml` change doesn't propagate (it's the iCloud vault file, gitignored)
**Impact:** Code ships but the new schedule/apply-params never take effect on any machine.
**Mitigation:** See Update System — the vault `reflections.yaml` edit is a real, required step and must be called out explicitly; the code changes (params support) are inert without it.

## Race Conditions

### Race 1: sweep reflection vs. a session's own Stop-hook resolution
**Location:** `reflections/memory/memory_outcome_resolve.py` (new) vs. `memory_bridge.py:897-907`
**Trigger:** A session finishes cleanly at nearly the same tick the sweep runs.
**Data prerequisite:** The sidecar must still exist, be unresolved, and have mtime past TTL for the sweep to act.
**State prerequisite:** None beyond the sidecar's filesystem mtime (TTL-only gating).
**Mitigation:** Sweep only touches sidecars whose mtime exceeds `INJECTION_RESOLVE_TTL`; a cleanly-finishing session either refreshed its sidecar via a recent recall injection (mtime under TTL, ineligible) or has already deleted it in the Stop-hook `finally`. `deferred` is a no-op outcome, so even a rare TTL-boundary double-resolve causes no confidence corruption.

### Race 2: concurrent prune + dedup mutating the same record
**Location:** `memory-decay-prune` and `memory-dedup` reflections
**Trigger:** Both scheduled daily; could overlap.
**Data prerequisite:** Both read/write `superseded_by`.
**State prerequisite:** A record already superseded by dedup must be skipped by prune.
**Mitigation:** Decay-prune already skips `memory.superseded_by` records (`memory_decay_prune.py:142-143`), so a dedup-superseded record is never re-touched by either prune tier. Tier-2 and dedup are both supersede-based and idempotent. The one hard-delete path (tier-1, importance < 0.15) is disjoint from dedup's target band (dedup exempts importance ≥ 7.0 and operates on semantic duplicates), and its `delete()` is wrapped in try/except so a delete of an already-removed record fails silently. Per-run caps bound blast radius.

## No-Gos (Out of Scope)

- [DESTRUCTIVE] Hard-deleting the ~59 pre-existing fragment records (from #2215's deferred cleanup) *as a bulk sweep* in this pass. Apply-mode removal here is **tombstone-first wherever `save()` can persist it** (tier-2 noise, dedup, and the dismissal-dominated exit all supersede). The lone exception is **tier-1 decay records (importance < 0.15)**, which the write filter forbids re-saving, so they hard-delete — but they are below the write-admission floor and bounded by `MAX_PRUNE_PER_RUN`, not an unbounded bulk purge of the standing pool. A bulk irreversible purge waits until tombstoning is trusted against #2200 metrics.
- [SEPARATE-SLUG] Per-instance confidence-modulated decay-rate (plan Phase 5) — substrate work, filed separately in popoto; explicitly dropped in this issue's recon.
- Retuning the dismissal-decay constants (threshold/factor/floor) — deliberately deferred to its own observation window (see Rabbit Holes); this is a *value-choice* deferral, not an operator/world action, so it carries no anti-criterion.

## Update System

**This feature requires a manual vault-config step and normal code propagation.**

- **`reflections.yaml` is the iCloud vault file** (`~/Desktop/Valor/reflections.yaml`), gitignored in-repo. Three edits MUST be made in the vault file so they iCloud-sync to every machine: (1) `params: {apply: true}` added to the **existing** `memory-embedding-backfill` entry at `:305` (activate, do NOT duplicate), (2) `params: {apply: true}` added to `memory-decay-prune` and `memory-dedup`, and (3) a **new** `memory-outcome-resolve` entry. This propagation is config-only (no git commit for the yaml itself).
- **Code changes** (sweep reflection module, `params` kwargs, fallback fix, counters) propagate normally via git/PR + `/update` (`uv sync`, no new dependency).
- **No new dependency** and **no new env var required for activation** (activation is config-driven via `params`). This pass *does* add one **optional** env kill-switch, `MEMORY_DEDUP_APPLY`, so `run_consolidation` gains the emergency brake the other two targets already have (CONCERN 1) — optional, never required for normal apply-mode operation.
- **Reconciling "no load-bearing operator step" with the vault edit:** the subconscious constraint forbids a *runtime / per-run* operator action (e.g., an operator having to set an env var before each sweep for pruning to engage). The one-time `reflections.yaml` vault edit is **config propagation**, not a runtime step — it is authored once, iCloud-syncs to every machine, and thereafter every scheduled run activates automatically with zero human involvement. This is the same class of action as editing any versioned config; it does not make an operator load-bearing on the recurring path. The two are therefore consistent, not contradictory.
- Confirm the `/update` Step 3.65 `reflections.yaml` migration (`scripts/update/reflections_yaml.py`) still passes with the new entry shape (it only rewrites `every:`/`interval:` lines, so it should be inert — verify).

## Agent Integration

No new agent-facing tool or MCP surface. This is entirely internal to the memory subsystem:
- The sweep reflection runs via the existing reflection scheduler (`agent/reflection_scheduler.py`) — no bridge wiring.
- Outcome resolution and pruning are background processes the agent never invokes directly.
- Existing MCP memory tools (`mcp__memory__memory_search`/`memory_get`) automatically benefit from the cleaner corpus (superseded records already filtered from recall).
- Integration test: assert the new reflection is discoverable/loadable by the scheduler registry (`python -m reflections --dry-run` exits 0 with the new entry present).

## Documentation

### Feature Documentation
- [ ] Update `docs/features/subconscious-memory.md` — document durable outcome attribution (crash-safe resolution), the honest-fallback change (fallback emits `deferred`), and activated pruning (apply-mode, tombstone-first, new schedule).
- [ ] Update the memory-consolidation / decay-prune subsections to reflect apply-mode activation and the `params`-driven switch.
- [ ] Add `memory-outcome-resolve` to any reflection index/table in `docs/features/` referencing scheduled memory jobs.

### Inline Documentation
- [ ] Docstring on `memory_outcome_resolve.run` explaining orphaned-sidecar semantics, the TTL gate, and the compare-and-delete cleanup (why it doesn't reuse blind `cleanup_sidecar`).
- [ ] Comment at the fallback branch in `detect_outcomes_async` explaining why it emits `deferred` (precision over recall).
- [ ] Comment at the dismissal-reset branch noting the reset-on-`acted` rule is now trustworthy post-fix.
- [ ] Comment at the dismissal-dominated supersede branch explaining it is the corpus exit for previously-accessed records the prune tiers' `access_count == 0` gate can't reach.
- [ ] `memory_decay_prune.py` docstring updated: tier-1 hard-deletes below-write-floor records (tombstone `save()` impossible under `WriteFilterMixin`), tier-2 tombstones.
- [ ] Grain-of-salt comment on the new `INJECTION_RESOLVE_TTL` constant (provisional/tunable).

## Success Criteria

- [ ] Injections from a killed/crashed session receive outcome resolution (test: simulate session death, leave an orphaned sidecar, run the sweep, assert injections resolved to `deferred`); none silently lost.
- [ ] The non-LLM fallback path never emits `acted` — its outcomes are `deferred` (unit test on `detect_outcomes_async` fallback branch).
- [ ] `memory-embedding-backfill` runs in **apply mode** via `params={"apply": true}` (its existing line-305 entry activated, no duplicate) and demonstrably re-embeds a seeded vectorless record (test: save a `Memory` with no embedding, run the reflection with `params={"apply": true}` and a healthy provider, assert the record gains a vector).
- [ ] `memory-decay-prune` and `memory-dedup` run in apply mode via `params`, with per-run caps respected; `prune_count`/`dedup_merge_count` appear in `/memories/metrics.json` (the `_GATE_COUNTER_FIELDS` append is in place).
- [ ] **Tier-1 genuinely leaves the corpus, no phantom count (BLOCKER):** a test seeds a tier-1 record (importance 0.10, `access_count == 0`, age > 30d), runs the reflection with `params={"apply": true}`, and asserts (a) the record is **absent** from `Memory.query` afterward (it was hard-deleted, since a tombstone `save()` cannot persist below 0.15), and (b) `prune_count` equals the number actually removed — not inflated by a phantom tombstone. A companion test seeds a tier-2 record (importance 0.5, confidence ≈ 0.5, age > 14d) and asserts it gains `superseded_by` and increments `prune_count`.
- [ ] **Dismissal decay verified (CONCERN 3a):** a regression test drives 3 sequential `dismissed` outcomes for one record and asserts importance decays at `DISMISSAL_DECAY_THRESHOLD` with `dismissal_count` reset; an interleaved `deferred` does not reset the counter.
- [ ] **Dismissal-dominated exit for previously-accessed records (CONCERN 3b):** a test seeds a record with `access_count > 0`, importance already at `MIN_IMPORTANCE_FLOOR`, and a 0%-act-rate all-`dismissed` outcome history; drives one more `dismissed` through `_persist_outcome_metadata`; and asserts the record is **superseded** (`superseded_by` set) and `prune_count` incremented — proving the "Ahhh"-class record (importance 6.0, dismissed, recalled) has a corpus exit the access_count==0 prune tiers can never provide.
- [ ] A demonstrably junky record (0% act rate) is observed to leave the active corpus (tier-1 hard-deleted, or tier-2 / dismissal-dominated tombstoned) with zero human action.
- [ ] No **runtime** operator step is load-bearing: with no env var set, `params={"apply": true}` in `reflections.yaml` engages both prune tiers automatically (env-as-kill-switch precedence — env vars are emergency brakes only, never required for normal activation). The one-time `reflections.yaml` vault edit is config propagation (iCloud-synced to every machine), not a per-run operator action — see Update System.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (attribution)**
  - Name: attribution-builder
  - Role: durable outcome attribution — sweep reflection + honest fallback + `deferred` persist branch
  - Agent Type: builder
  - Resume: true

- **Builder (pruning)**
  - Name: pruning-builder
  - Role: activate pruning — `params` kwargs, apply-mode tombstone-first, backfill schedule, counters
  - Agent Type: builder
  - Resume: true

- **Validator (loop)**
  - Name: loop-validator
  - Role: verify crash-resolution, fallback honesty, apply-mode safety rails, metrics counters
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: memory-doc
  - Role: update subconscious-memory feature docs + inline docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Honest fallback + deferred persistence
- **Task ID**: build-honest-fallback
- **Depends On**: none
- **Validates**: tests/unit/test_memory_extraction.py (fallback→deferred, persist deferred branch)
- **Informed By**: spike-1 (deferred is first-class)
- **Assigned To**: attribution-builder
- **Agent Type**: builder
- **Parallel**: true
- **Domain**: async/concurrency, Redis/Popoto data
- In `agent/memory_extraction.py` `detect_outcomes_async`, change the bigram fallback to emit `deferred` for all injections.
- Add a `deferred` branch to `_persist_outcome_metadata` (no change to dismissal_count).
- Add/adjust unit tests; add comment explaining precision-over-recall.
- **Dismissal-decay regression test (CONCERN 3a):** add a test driving **3 sequential `dismissed` outcomes** for one record through `_persist_outcome_metadata` (`DISMISSAL_DECAY_THRESHOLD == 3`): assert `dismissal_count` 1→2→3, importance decays by `DISMISSAL_IMPORTANCE_DECAY` (floored at `MIN_IMPORTANCE_FLOOR`) on the 3rd, and resets to 0 after decay; plus an interleaved-`deferred` case asserting `deferred` does NOT reset the counter (only `acted` does). Add the trustworthy-reset comment at the reset branch.
- **Dismissal-dominated corpus exit (CONCERN 3b):** in the `dismissed` branch of `_persist_outcome_metadata`, after the decay step, add a supersede exit for previously-accessed records that the prune reflection's `access_count == 0` gates can never reach. When the record is **already at `MIN_IMPORTANCE_FLOOR`** (decay can't lower it further) **and** `compute_act_rate(outcome_history) == 0.0` over at least `DISMISSAL_DECAY_THRESHOLD` recorded outcomes, set `superseded_by` (tombstone sentinel) + `save()` (persists — the floored record sits at 0.2 ≥ 0.15 write floor) and emit `prune_count` via `_increment_gate_counter(m.project_key or DEFAULT_PROJECT_KEY, "prune_count")`. Add a test seeding an `access_count > 0`, floored, 0%-act-rate record and asserting it is superseded on the next `dismissed`. Name the test with a `dismissal_prune` marker so the Verification grep matches.

### 2. Durable attribution sweep reflection
- **Task ID**: build-outcome-resolve
- **Depends On**: none
- **Validates**: tests/unit/test_memory_outcome_resolve.py (create)
- **Informed By**: spike-3 (sidecar is the durable record)
- **Assigned To**: attribution-builder
- **Agent Type**: builder
- **Parallel**: true
- **Domain**: async/concurrency
- Create `reflections/memory/memory_outcome_resolve.py::run`; wire through `reflections/memory_management.py`.
- Add `INJECTION_RESOLVE_TTL` to `config/memory_defaults.py` (grain-of-salt comment).
- Gate on mtime > TTL only (no liveness dependency — no such helper exists in `memory_bridge.py`); resolve to `deferred` **keyed by `db_key.redis_key`** (clean-stop pattern at `agent/memory_extraction.py:1542-1552`, NOT `memory_id`); per-run cap; fail-silent.
- **Compare-and-delete cleanup (CONCERN 2):** do NOT call the blind `cleanup_sidecar` (`memory_bridge.py:930`, unconditional `unlink()` at `:947`). Capture `mtime_at_read` when the sweep loads each sidecar; before unlinking, re-`stat()` and unlink **only if the mtime is unchanged**. If a resume rewrote the sidecar (mtime changed), leave it in place so the resumed session's injections are not destroyed. Add a test simulating a mtime bump between read and cleanup and asserting the sidecar survives.

### 3. Activate pruning (params + apply + counters)
- **Task ID**: build-activate-pruning
- **Depends On**: none
- **Validates**: tests/unit/test_decay_prune_apply_params.py, test_dedup_apply_params.py, test_backfill_apply_params.py (create)
- **Informed By**: spike-2 (params forwarding), #2201 counter pattern
- **Assigned To**: pruning-builder
- **Agent Type**: builder
- **Parallel**: true
- **Domain**: Redis/Popoto data
- Add `params` kwarg to **all three** callables — `memory_decay_prune.run`, `run_consolidation`, and `memory_embedding_backfill.run` (currently zero-arg `async def run()`). Precedence is **env-as-kill-switch**: `apply = env_value if env_explicitly_set else params.get("apply", False)`. In `memory_decay_prune.run`, apply this rule to BOTH `decay_apply` (env `MEMORY_DECAY_PRUNE_APPLY`) and `noise_apply` (env `MEMORY_NOISE_PRUNE_APPLY`) so one `params={"apply": true}` engages both tiers, each env var still able to veto its own tier. In `memory_embedding_backfill.run`, apply it to the existing `apply_mode` derivation against `MEMORY_EMBEDDING_BACKFILL_APPLY` (`:63`).
- **Consolidation env kill-switch (CONCERN 1):** `run_consolidation` (`scripts/memory_consolidation.py:443`) has **no env var today** — add an optional `MEMORY_DEDUP_APPLY` kill-switch plus the `params` kwarg, wired to the same rule (`apply = <MEMORY_DEDUP_APPLY if set> else params.get("apply", False)`, then `dry_run = not apply`) so the env-as-kill-switch invariant is genuinely uniform across all three targets. Preserve the CLI `--apply` path.
- **Split the shared prune loop (`memory_decay_prune.py:183-226`) by tier with per-tier removal (BLOCKER):** **tier-1 (importance < 0.15) hard-deletes** (`memory.delete()`) — a tombstone `save()` cannot persist below the 0.15 write floor (`WriteFilterMixin._check_write_filter`, `popoto/models/base.py:1093-1097`), so this is the only persistable removal; **tier-2 (0.15 ≤ importance ≤ 1.0) tombstones** (`superseded_by` + `save()`). Keep floor + `MAX_PRUNE_PER_RUN` cap for both. Update the module docstring (`:22-24`, `:29-31`) to state tier-1 hard-deletes below-floor records / tier-2 tombstones.
- Emit `prune_count`/`dedup_merge_count` via `_increment_gate_counter(memory.project_key or DEFAULT_PROJECT_KEY, reason)` **per-record, only on a persisted removal** — tier-1 after `delete()` returns without raising; tier-2 only when `save()` returns truthy (a `False`/filtered save must not phantom-count). Coalesce null/empty `project_key` to the default so no counter is lost.
- **Surface the counters (CONCERN 2):** append `("prune_count", "prune_count")` and `("dedup_merge_count", "dedup_merge_count")` to `_GATE_COUNTER_FIELDS` (`ui/data/memories.py:240`) so `get_corpus_metrics`'s summing loops (`:388`/`:414`) read them into `/memories/metrics.json`; ensure `_sum_gate_counter`'s pk list includes `DEFAULT_PROJECT_KEY`. Without this append the increments are invisible in metrics.

### 4. reflections.yaml activation (vault config)
- **Task ID**: build-reflections-config
- **Depends On**: build-activate-pruning, build-outcome-resolve
- **Assigned To**: pruning-builder
- **Agent Type**: builder
- **Parallel**: false
- **Activate the EXISTING `memory-embedding-backfill` entry** (config/vault `:305`, `enabled: true`) by adding `params: {apply: true}` to it — do NOT add a duplicate entry.
- Add `params: {apply: true}` to `memory-decay-prune` and `memory-dedup`; add the **new** `memory-outcome-resolve` entry (references the callable created in Task 2 `build-outcome-resolve` — hence the dependency edge on Task 2, whose module must exist before the yaml references it).
- **Verify the vault edits actually landed (CONCERN 4)** — the vault `~/Desktop/Valor/reflections.yaml` is separate from the repo copy, so grep the vault file directly for each `apply: true` and the new entry (the four "Vault:" rows in the Verification table). A build that edits only the repo copy silently no-ops on every machine.
- Verify `python -m reflections --dry-run` exits 0 (loads the `memory-outcome-resolve` callable and parses the new `params` blocks) and the update-step migration stays inert.

### 5. Validation
- **Task ID**: validate-loop
- **Depends On**: build-honest-fallback, build-outcome-resolve, build-activate-pruning, build-reflections-config
- **Assigned To**: loop-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all Success Criteria; run Verification table commands; confirm metrics counters render.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-loop
- **Assigned To**: memory-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/subconscious-memory.md` and inline docs.

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: loop-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full Verification table; confirm docs updated; final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Fallback never emits acted | `grep -n '"acted"' agent/memory_extraction.py \| grep -i fallback` | match count == 0 |
| deferred is first-class | `.venv/bin/python -c "from popoto.fields.observation import VALID_OUTCOMES; print('deferred' in VALID_OUTCOMES)"` | output contains True |
| Sweep reflection exists | `test -f reflections/memory/memory_outcome_resolve.py && echo ok` | output contains ok |
| Backfill takes params (apply-mode wired) | `grep -n 'def run' reflections/memory/memory_embedding_backfill.py \| grep params` | match count >= 1 |
| Backfill apply-mode re-embeds a seeded vectorless record | `pytest tests/unit/test_backfill_apply_params.py -x -q` | exit code 0 |
| Backfill entry not duplicated in vault | `grep -c '^  - name: memory-embedding-backfill' ~/Desktop/Valor/reflections.yaml` | output == 1 |
| Prune/merge counters surfaced in metrics | `grep -c 'prune_count\|dedup_merge_count' ui/data/memories.py` | output >= 2 (present in `_GATE_COUNTER_FIELDS`) |
| Dismissal decay after 3 dismissals verified | `pytest tests/unit/test_memory_extraction.py -x -q -k dismissal` | exit code 0 |
| Registry loads | `python -m reflections --dry-run` | exit code 0 |
| Tier-2 tombstones (apply path sets `superseded_by`) | `grep -n 'superseded_by =' reflections/memory/memory_decay_prune.py` | match count >= 1 |
| Tier-1 hard-deletes below-floor records (delete retained for tier-1) | `grep -n '\.delete()' reflections/memory/memory_decay_prune.py` | match count >= 1 |
| Tier-1 genuinely leaves corpus + no phantom count (BLOCKER) | `pytest tests/unit/test_decay_prune_apply_params.py -x -q` | exit code 0 |
| Dedup has an env kill-switch (CONCERN 1) | `grep -n 'MEMORY_DEDUP_APPLY' scripts/memory_consolidation.py` | match count >= 1 |
| Dismissal-dominated exit supersedes accessed record (CONCERN 3b) | `pytest tests/unit/test_memory_extraction.py -x -q -k dismissal_prune` | exit code 0 |
| Vault: decay-prune apply landed (CONCERN 4) | `grep -A6 'name: memory-decay-prune' ~/Desktop/Valor/reflections.yaml \| grep -c 'apply: true'` | output >= 1 |
| Vault: dedup apply landed (CONCERN 4) | `grep -A6 'name: memory-dedup' ~/Desktop/Valor/reflections.yaml \| grep -c 'apply: true'` | output >= 1 |
| Vault: backfill apply landed on existing entry (CONCERN 4) | `grep -A6 'name: memory-embedding-backfill' ~/Desktop/Valor/reflections.yaml \| grep -c 'apply: true'` | output >= 1 |
| Vault: outcome-resolve entry present (CONCERN 4) | `grep -c 'name: memory-outcome-resolve' ~/Desktop/Valor/reflections.yaml` | output == 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

### Revision applied (2026-07-23) — response to NEEDS REVISION (2 blockers, 2 concerns)

- **BLOCKER 1 (wrong symbol `persist_outcome_data`)** — RESOLVED. Renamed every occurrence to the real symbol `_persist_outcome_metadata` (`agent/memory_extraction.py:1243`, called at `:1443`; existing branches are `dismissed`/`used`/`acted`, and the new `deferred` no-op branch goes alongside them).
- **BLOCKER 2 (tombstone/delete contradiction — one shared `.delete()` at `:219` for both tiers)** — RESOLVED. The plan now splits the shared loop (`memory_decay_prune.py:183-226`) and makes **both** tier-1 (decay) and tier-2 (#1822 noise) tombstone-first via `superseded_by` + `save()`; no `.delete()` remains on the apply path. Verification updated: added a positive `superseded_by =` check and scoped the `.delete()`-absence check to cover code + docstring (line `:24` docstring mention must also be updated). No-Gos already state apply-mode is tombstone-first only, now fully consistent.
- **CONCERN 3 (phantom liveness reuse)** — RESOLVED. Confirmed no liveness helper exists in `memory_bridge.py` (no `is_session_live`/`session_live`/`is_live`; only an unrelated `AgentSession` comment at `:920`). Committed to **TTL-only gating** (`mtime > INJECTION_RESOLVE_TTL`); dropped the "session-not-live" conjunct across Technical Approach, Key Elements, Risk 2, Race 1, and the task. Preserves "no new dependencies".
- **CONCERN 4 (counter-namespace forced fit)** — RESOLVED. Each pruned/merged record is a `Memory` carrying its own `memory.project_key`; the plan now increments **per-record** via `_increment_gate_counter(memory.project_key, reason)`, preserving the `{project_key}:memory-gate:{reason}` layout so `_sum_gate_counter(reason, pks)` aggregates correctly. No placeholder/corpus-wide key.

### Revision applied (2026-07-23, 2nd pass) — response to RE-CRITIQUE (1 blocker, 3 concerns, 1 nit)

- **BLOCKER (apply-activation switch underspecified/self-contradictory)** — RESOLVED, both facets:
  - *(a) Precedence contradiction.* Adopted the recommended **env-as-kill-switch** rule — `apply = env_value if env_explicitly_set else params.get("apply", False)` — and stated it **identically** in Technical Approach ("Apply activation"), Risk 1, Success Criteria, and Task 3. The old "params > env > dry-run" phrasing (Technical Approach) and "env override still forces dry-run" phrasing (Risk 1) are gone; both now read as one rule: explicit env wins as an emergency brake, `params` governs when env is unset.
  - *(b) Tier-mapping ambiguity.* Made explicit that `memory_decay_prune.run` holds TWO booleans — `decay_apply` (tier-1, `MEMORY_DECAY_PRUNE_APPLY`) and `noise_apply` (tier-2, `MEMORY_NOISE_PRUNE_APPLY`) — and that a single `params={"apply": true}` engages **both** tiers via the kill-switch rule applied per-env-var. Documented in Technical Approach and Task 3.
- **CONCERN (TTL-only gate relies on unverified per-turn mtime refresh)** — RESOLVED. Verified in code: `recall()` (`memory_bridge.py:450`) rewrites the sidecar via `_save_sidecar` (`:615`) on every injection, per-turn via PostToolUse. Stated the assumption AND its caveat (mtime refreshes on *injection*, not any turn), so `INJECTION_RESOLVE_TTL` must exceed the max gap between injections; `deferred` no-op keeps a mis-estimate harmless. Threaded through Technical Approach, Risk 2, Race 1.
- **CONCERN (null/empty `project_key` counters silently vanish)** — RESOLVED. Coalesce null/empty `project_key` to `DEFAULT_PROJECT_KEY` (`config/memory_defaults.py`, already imported in `ui/data/memories.py`) at both the `_increment_gate_counter` site and in `_sum_gate_counter`'s pk list, so no prune/merge is under-counted. Documented in Technical Approach counters bullet and Task 3.
- **CONCERN ("no load-bearing operator step" vs. required vault edit)** — RESOLVED. Reconciled in Update System: the constraint bars a *runtime/per-run* operator action; the one-time `reflections.yaml` vault edit is config propagation (iCloud-synced, authored once), consistent not contradictory. Success Criteria reworded to "no **runtime** operator step is load-bearing".
- **NIT (over-specified prune-loop refactor)** — ADDRESSED. Trimmed the mechanical line-by-line ("keep candidate lists distinct, deduped by memory_id, bounded by MAX_PRUNE_PER_RUN") to the load-bearing invariant (both tiers tombstone-first, cap+floor stay, no `.delete()`).

### Revision applied (2026-07-23, 3rd pass) — response to RE-CRITIQUE (1 blocker, 4 concerns)

- **BLOCKER (stale "backfill missing from reflections.yaml" premise — the entry already exists)** — RESOLVED. Re-verified at HEAD (`dee0e1e2`): `grep -c "embedding-backfill" config/reflections.yaml` == **1**; the entry lives at `config/reflections.yaml:305` and vault `:305`, `enabled: true`, daily, dry-run behind `MEMORY_EMBEDDING_BACKFILL_APPLY`. The earlier "== 0" claim was stale (the vault-synced/gitignored yaml is absent from the repo tree at the older baseline). Recast throughout: Problem defect 3, Freshness Check, Data Flow, Why-Previous-Fixes root cause. Backfill is now the **third apply-mode target** — its `run()` grows a `params` kwarg (env-as-kill-switch on `MEMORY_EMBEDDING_BACKFILL_APPLY`) in **Task 3**, and **Task 4** only adds `params: {apply: true}` to the existing line-305 entry (no duplicate). The existence-grep verification row was replaced with (a) a "backfill takes params" grep, (b) a `test_backfill_apply_params.py` re-embed test, and (c) a "not duplicated" `grep -c == 1` row. Success Criterion recast from "appears in yaml + loads" to "runs in apply mode and re-embeds a seeded vectorless record."
- **CONCERN 1 (sweep `on_context_used` keyed by `memory_id`, contract wants redis_key)** — RESOLVED. `on_context_used(instances, outcome_map)` keys `outcome_map` by each instance's Redis key (`_get_instance_key` = `instance._redis_key`/`db_key.redis_key`); a `memory_id`-keyed map silently falls through to the `deferred` default, masking the mis-key while the test passes for the wrong reason. Technical Approach now mandates the **exact clean-stop pattern at `agent/memory_extraction.py:1542-1552`** (`redis_outcome_map[m.db_key.redis_key] = "deferred"`) and a test asserting the record's observation state, not just "no crash."
- **CONCERN 2 (`prune_count`/`dedup_merge_count` never surface without `_GATE_COUNTER_FIELDS` append)** — RESOLVED. Added the explicit "append `("prune_count", "prune_count")` and `("dedup_merge_count", "dedup_merge_count")` to `_GATE_COUNTER_FIELDS` (`ui/data/memories.py:240`)" step to Key Elements, Technical Approach counters bullet, Task 3, and a Verification row. Without the append the summing loops (`:388`/`:414`) never emit them.
- **CONCERN 3 (defect 4 dismissal decay asserted, not verified)** — RESOLVED. No direct code change (the honest fallback makes the existing threshold-decay path trustworthy), but the plan now **proves** it: a regression test driving 3 sequential `dismissed` outcomes through `_persist_outcome_metadata` (decay at threshold + reset; interleaved `deferred` does not reset), wired into Task 1, Test Impact, a Success Criterion, and a Verification row.
- **CONCERN 4 (Task 4 `Depends On` omits Task 2, whose callable the yaml references; both 2 & 3 are `Parallel: true`)** — RESOLVED. Task 4 `Depends On` is now `build-activate-pruning, build-outcome-resolve` — the `memory-outcome-resolve` yaml entry references Task 2's module, which must exist first.

### Revision applied (2026-07-23, 4th pass) — response to RE-CRITIQUE (1 blocker, 5 concerns)

- **BLOCKER (tier-1 tombstone-via-`save()` mechanically impossible)** — RESOLVED. Verified in popoto: `WriteFilterMixin._check_write_filter()` (`popoto/models/base.py:1093-1097`) raises `SkipSaveException` on *every* save (INSERT or UPDATE) whose importance < 0.15, so `superseded_by=…; save()` on a tier-1 record (importance < 0.15) silently no-ops while an unconditional `prune_count` reports a phantom. Fix, made explicit throughout (Key Elements, Technical Approach, Task 3, No-Gos, Risk 1, Race 2, Decisions, docstring): **tier-1 → hard-delete** (`delete()`, the only persistable removal for below-write-floor records), **tier-2 → tombstone** (`superseded_by` + `save()`, importance ≥ 0.15 persists). `prune_count` increments **only on a persisted removal** (delete succeeded / tombstone `save()` truthy) — no phantom. Added Success Criterion + Verification test that a seeded tier-1 record is **absent from `Memory.query`** after apply and the count is not inflated. This supersedes the prior "both tiers tombstone" resolution (mechanically impossible for tier-1).
- **CONCERN 1 (dedup has no env kill-switch → invariant false)** — RESOLVED. Confirmed `run_consolidation` (`scripts/memory_consolidation.py:443`) has only a `dry_run` param, no env var. Added an optional **`MEMORY_DEDUP_APPLY`** kill-switch so the "env-as-kill-switch for all three targets" invariant is genuinely true, symmetric with `MEMORY_DECAY_PRUNE_APPLY`/`MEMORY_NOISE_PRUNE_APPLY`/`MEMORY_EMBEDDING_BACKFILL_APPLY`. Threaded through Technical Approach, Task 3, Update System, Verification.
- **CONCERN 2 (blind `cleanup_sidecar` clobbers a resuming session)** — RESOLVED. The sweep no longer calls blind `cleanup_sidecar` (`memory_bridge.py:947` unconditional `unlink()`); it captures `mtime_at_read` and does a **compare-and-delete**, unlinking only if the mtime is unchanged. A resume that rewrites the sidecar (mtime bump) is preserved. Specified in Technical Approach, Task 2, Test Impact.
- **CONCERN 3 (flagship "Ahhh" record has no prune exit — both tiers require `access_count == 0`)** — RESOLVED. Confirmed `MIN_IMPORTANCE_FLOOR == 0.2 > WF_MIN_THRESHOLD == 0.15`, so a dismissed-decayed *accessed* record floors at 0.2 and never enters tier-1's `< 0.15` band, and `access_count > 0` excludes both tiers. Added a **dismissal-dominated corpus exit** in `_persist_outcome_metadata`: when a record is already at the floor with a 0% act rate, supersede it directly (persists — 0.2 ≥ 0.15). Cleanly divided from the prune reflection (never-accessed junk) vs decay path (previously-accessed dismissed junk). New Success Criterion + `dismissal_prune` test.
- **CONCERN 4 (no grep verifies the vault `apply: true` edits landed)** — RESOLVED. Added four "Vault:" Verification rows grepping `~/Desktop/Valor/reflections.yaml` directly for `apply: true` on decay-prune/dedup/backfill and for the new `memory-outcome-resolve` entry, plus a Task-4 note that a repo-only edit silently no-ops on every machine.
- **CONCERN 5 (backfill scope-creep — adds recall signal, inverse of pruning)** — RESOLVED (in-scope, justified). The issue's Acceptance Criteria explicitly require "memory-embedding-backfill runs on the reflection schedule," and all three maintenance reflections share the identical activation defect (dry-run behind an unset apply gate) and identical fix seam (a `params` kwarg, env-as-kill-switch). Added an explicit scope note to the Solution making the boundary intentional, not accidental.

---

## Decisions (resolved)

Finalized with the plan's proposed defaults; each is reversible/tunable and will be stress-tested at critique:

1. **Removal semantics** — reuse the existing `superseded_by` path as the tombstone marker wherever a `save()` can persist it (tier-2 prune, dedup, and the dismissal-dominated exit), distinguished from dedup by counter name (`prune_count` vs `dedup_merge_count`). **Tier-1 decay records (importance < 0.15) hard-delete** because `WriteFilterMixin` forbids re-saving a below-floor record, making a tombstone mechanically impossible (BLOCKER); they are below the write-admission floor and bounded by the per-run cap. No new `tombstoned_at`/status field this pass; recall already filters superseded records. Revisit only if metrics need to disaggregate prune-vs-merge beyond the counters.
2. **Sweep cadence & TTL** — `INJECTION_RESOLVE_TTL` set comfortably longer than the max plausible gap between recall injections in a live session (per the verified mtime-refresh analysis in Technical Approach), swept daily-or-faster. Value is provisional/tunable; a mis-estimate is harmless because `deferred` is a no-op outcome (spike-1).
3. **Apply-mode blast-radius** — enable apply-mode (tombstone-first, reversible) immediately on merge. The #2200 baseline makes every deletion observable and tombstoning is reversible, satisfying the "gated on observability" constraint without a separate dry-run cycle. Emergency env kill-switches remain if a machine needs to force dry-run.
