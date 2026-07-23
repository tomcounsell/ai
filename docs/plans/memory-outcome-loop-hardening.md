---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-23
tracking: https://github.com/tomcounsell/ai/issues/2203
last_comment_id: 5048924712
---

# Outcome-Loop Hardening: Durable Attribution, Honest Fallback, Activate Pruning

## Problem

Valor's subconscious memory system is supposed to **validate (weight/downweight) and prune memories naturally during regular use** — a hard project constraint. The machinery exists: injection outcomes are LLM-judged and fed into popoto's `ObservationProtocol.on_context_used` (Bayesian confidence updates), and pruning reflections are written. But the loop is degraded in four specific ways, so junk never leaves the corpus and confidence updates are being corrupted.

**Current behavior:**
1. **Outcome attribution dies with the session sidecar.** Injections are tracked in a per-session file sidecar (`injected[]`). Outcomes are judged only at *clean* session stop, then the sidecar is deleted. Crashed/abandoned/killed sessions never reach the stop handler, so their injections receive **no outcome signal at all** — silently lost.
2. **The fallback judge lies optimistically.** When the Haiku outcome judge fails, the bigram-overlap fallback marks *any* keyword overlap as `acted`. False "acted" signals corroborate confidence on memories that were never actually used — poisoning the exact signal the system learns from.
3. **Pruning is dark.** `memory-decay-prune` is dry-run unless an env var is set; `memory-dedup` defaults to dry-run; and `memory-embedding-backfill` has **no entry in `reflections.yaml` at all** — vectorless records are never re-embedded. Zero records have ever been auto-pruned in apply mode.
4. **Dismissal downweighting is slow relative to noise.** Importance decays only after 3 *consecutive* dismissals, and a single interleaved false "acted" (defect 2) resets the counter. Example: "Ahhh" (dismissed ×2) still sits at importance 6.0 in production.

**Desired outcome:** every injection eventually gets an honest outcome (or an explicit neutral `deferred`), and low-value memories demonstrably leave the active corpus with zero human action. Signal integrity outranks signal volume — when in doubt, resolve neutral.

## Freshness Check

**Baseline commit:** `3c0fc7ee103b955201f026af01852b41b57dc361`
**Issue filed at:** 2026-07-22T04:32:06Z
**Disposition:** Minor drift (line numbers moved after #2215; both prerequisites landed — favorable)

**File:line references re-verified against baseline:**
- `.claude/hooks/hook_utils/memory_bridge.py` injected-tracking — issue cited `:604-613`; now at ~`:611-613` (`injected.extend(new_entries)` + `_save_sidecar`). Claim holds.
- `memory_bridge.py` outcome-at-stop + sidecar cleanup — issue cited `:839`/`:886-907`; now `detect_outcomes_async` invoked at `:897`, `cleanup_sidecar` defined at `:907` and called in the stop handler's `finally` at `:904`. Claim holds: cleanup only runs on the clean-stop path.
- `agent/memory_extraction.py` optimistic fallback — issue cited `:1402-1410`; now the bigram fallback at ~`:1390-1400` sets `outcome_map[memory_key] = "acted"` on any `overlap`. Claim holds.
- `agent/memory_extraction.py` dismissal decay — issue cited `:1321-1339`; now `DISMISSAL_DECAY_THRESHOLD` check + reset-on-acted at ~`:1305-1330`. Claim holds. Constants live in `config/memory_defaults.py:106-111`.
- `reflections/memory/memory_decay_prune.py` apply gate — `MEMORY_DECAY_PRUNE_APPLY` env, dry-run default for both tiers (`:106-117`). Claim holds.
- `config/reflections.yaml:140` dedup dry-run — `memory-dedup` at `:140`, callable `scripts.memory_consolidation.run_consolidation` whose `dry_run: bool = True` default (`scripts/memory_consolidation.py:445`). Claim holds.
- `memory-embedding-backfill` missing from `reflections.yaml` — confirmed: `grep -c "embedding-backfill" config/reflections.yaml` == 0. Callable `run_memory_embedding_backfill` is imported/exported in `reflections/memory_management.py:15,22` but never scheduled.

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
- **Impact on plan**: The fallback fix is a one-line semantic change (`"acted"`→`"deferred"`) plus a `deferred` branch in `persist_outcome_data` (which currently only handles acted/used/dismissed). No popoto changes needed.

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
3. **Clean stop (today)**: Stop hook → `detect_outcomes_async(injected_tuples, response_text)` → Haiku judge (or bigram fallback) → `outcome_map` → `persist_outcome_data` (dismissal_count / importance) + `ObservationProtocol.on_context_used` (confidence/decay) → `cleanup_sidecar`.
4. **Crash/kill (today — BROKEN)**: process dies before step 3 → sidecar orphaned → injections never judged → **signal lost**.
5. **After fix**: step 4's orphaned sidecar is picked up by the new `memory-outcome-resolve` reflection → unresolved injections resolved to `deferred` (neutral) → cleanup. Step 3's fallback path now emits `deferred` instead of false `acted`.
6. **Pruning (after fix)**: `memory-decay-prune` (apply, tombstone-first) + `memory-dedup` (apply) + `memory-embedding-backfill` (scheduled) run on the reflection tick → low-confidence/zero-access records are superseded/tombstoned → counts surface in `/memories/metrics.json` via the reused gate-counter pattern.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|-----------------------|
| #1822 / PR #1831 | Closed three extraction-time noise sources + GC tier | Addressed *inflow* at extraction; never touched the outcome→confidence loop or activated pruning. |
| memory-dedup (#795) | Built LLM dedup with a dry-run safety period | The "flip to apply after review" step was never taken — the reflection has run dark for months. |
| memory-decay-prune | Built two-tier decay/noise pruning gated on env | Env gate (`MEMORY_DECAY_PRUNE_APPLY`) was never set in any worker environment, so apply mode never engaged — and env-gating violates the "no load-bearing operator step" constraint. |

**Root cause pattern:** the loop was *built* but its activation switches were left off (dry-run defaults, unset env vars, an unscheduled reflection) and its degraded paths (crash-loss, optimistic fallback) manufacture or drop signal. This is a **hardening + activation** issue, not a construction one.

## Architectural Impact

- **New dependencies**: none (all machinery exists: sidecars, ObservationProtocol, reflection scheduler, gate-counter module).
- **Interface changes**: `memory_decay_prune.run` and `run_consolidation` gain an optional `params: dict | None = None` kwarg (backward-compatible; env stays as override). `detect_outcomes_async` fallback branch changes emitted outcome. `persist_outcome_data` gains a `deferred` branch.
- **Coupling**: *decreases* — outcome resolution is decoupled from the sidecar-delete lifecycle. Activation moves from scattered env vars to the single `reflections.yaml` registry.
- **Data ownership**: unchanged. Sidecars remain the injection journal; the new reflection is a late resolver, not a new owner.
- **Reversibility**: high. Tombstone-first pruning (supersede, not hard-delete) is reversible; the fallback change is one line; the sweep reflection can be disabled in `reflections.yaml`.

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

- **Durable attribution (`memory-outcome-resolve` reflection)**: A scheduled sweep that finds orphaned/stale session sidecars (sessions no longer live, or sidecars older than an `INJECTION_RESOLVE_TTL`), resolves their unresolved `injected[]` entries to `deferred` (neutral — pressure builds, never a false positive), feeds them through `ObservationProtocol.on_context_used`, and cleans up. Reuses the existing sidecar as the journal (spike-3); no new storage.
- **Honest fallback**: In `detect_outcomes_async`, the bigram-overlap fallback emits `deferred` for **every** injection (never `acted`, never `dismissed`). A cheap heuristic must not manufacture positive or negative corroboration. Add a `deferred` branch to `persist_outcome_data`.
- **Activate pruning (config-driven, safety-railed)**:
  - Add the `memory-embedding-backfill` entry to `reflections.yaml`.
  - Give `memory_decay_prune.run` and `run_consolidation` a `params` kwarg so `params={"apply": true}` in `reflections.yaml` engages apply mode (env vars downgraded to emergency-brake overrides).
  - Flip `memory-decay-prune` and `memory-dedup` to apply, **tombstone-first** (supersede via `superseded_by` rather than hard-delete for tier-1 decay), respecting the importance floor and per-run caps already in place.
  - Emit `prune_count` / `dedup_merge_count` counters via the existing `_increment_gate_counter` pattern; surface them in `/memories/metrics.json`.
- **Dismissal decay**: leave constants in `config/memory_defaults.py` as-is (already named/env-overridable). Add a comment noting the reset-on-`acted` rule is now trustworthy (the fallback no longer injects false `acted` resets). No constant change this pass.

### Flow

Session crashes → sidecar orphaned on disk → `memory-outcome-resolve` tick finds it → unresolved injections → `deferred` → confidence pressure builds (no false signal) → cleanup.

Reflection tick → `memory-decay-prune` (apply, tombstone) + `memory-dedup` (apply) + `memory-embedding-backfill` → low-value records superseded/re-embedded → counts in metrics.json.

### Technical Approach

- **Sweep reflection** (`reflections/memory/memory_outcome_resolve.py`, wired through `reflections/memory_management.py`): iterate the sidecar directory (`_get_sidecar_dir` root), select sidecars whose owning session is not live AND whose mtime exceeds `INJECTION_RESOLVE_TTL` (new named constant in `config/memory_defaults.py`, provisional/tunable per the magic-numbers convention). For each, build `{memory_id: "deferred"}` and call `ObservationProtocol.on_context_used`; then `cleanup_sidecar`. Idempotent, fail-silent, per-run cap.
- **Liveness check**: reuse the existing session-liveness signal (worker owns it). Do NOT resolve sidecars for still-running sessions — only orphans. If liveness is ambiguous, gate purely on the TTL (long enough that a live session can't plausibly still be mid-turn).
- **Fallback fix**: `agent/memory_extraction.py` `detect_outcomes_async` — replace the `if overlap: "acted" else: "dismissed"` block with `outcome_map[memory_key] = "deferred"` for all fallback injections. Add `elif outcome == "deferred":` (no-op on dismissal_count) in `persist_outcome_data`.
- **Apply activation**: `params` kwarg on both run-callables; precedence: explicit `params["apply"]` > env override > dry-run default. Tombstone-first for decay tier-1 (set `superseded_by`/tombstone instead of `delete()`).
- **Counters**: `prune_count`, `dedup_merge_count` via `models/memory_gate.py::_increment_gate_counter` (or a sibling counter helper in the same namespace); add fields to `ui/data/memories.py::get_corpus_metrics` via `_sum_gate_counter`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The memory loop is fail-silent by design (`except Exception` in `detect_outcomes_async`, `persist_outcome_data`, `memory_bridge`). Each new code path (sweep reflection, `deferred` branch) must assert *observable* behavior on the happy path (a `logger.debug`/counter increment or a state change on the Memory record), not just "didn't crash".
- [ ] Sweep reflection: assert it logs a resolved count and does NOT raise when a sidecar is malformed/partial (crashed mid-write).

### Empty/Invalid Input Handling
- [ ] `detect_outcomes_async` with empty `injected_thoughts` or empty `response_text` → returns `{}` (already guarded; add regression test).
- [ ] Sweep reflection with an empty sidecar dir → no-op, returns zero count.
- [ ] Sweep reflection with a sidecar containing `injected: []` → no-op cleanup.

### Error State Rendering
- [ ] `/memories/metrics.json` renders `prune_count`/`dedup_merge_count` as `0` (not missing/errored) when no pruning has occurred yet.

## Test Impact

- [ ] `tests/unit/test_memory_extraction.py` (outcome-detection cases) — UPDATE: any test asserting the bigram fallback yields `acted` on overlap must be changed to assert `deferred`. Search for `"acted"` assertions in fallback-path tests.
- [ ] `tests/**` outcome/persist tests — UPDATE: add a `deferred` case to `persist_outcome_data` coverage (dismissal_count unchanged).
- [ ] `tests/**` reflections registry tests (e.g. `test_reflections_yaml_*`) — UPDATE: adding `memory-embedding-backfill` + `params` fields must still parse/validate; confirm the schema-validation migration accepts the new entry.
- [ ] New tests (REPLACE/ADD): `test_memory_outcome_resolve.py` (orphaned-sidecar sweep → deferred), `test_decay_prune_apply_params.py` (params→apply, tombstone-first, cap respected), `test_dedup_apply_params.py`.

If a grep shows no existing test asserts `acted` in the fallback path, state so in the build and add fresh coverage — the fallback path is currently under-tested, which is how the optimistic bug survived.

## Rabbit Holes

- **Building a new Popoto `MemoryInjection` model + migration for the journal.** Rejected: the crashed-session sidecar already IS the durable record (spike-3). A schema migration is disproportionate; the sweep reflection is lighter and reversible.
- **Re-embedding provider/model selection for backfill.** `memory_embedding_backfill.run` already probes the provider; don't re-litigate embedding infra here — just schedule it.
- **Tuning the dismissal-decay constants** (threshold, decay factor, floor). Tempting now that the fallback is honest, but changing learning constants deserves its own observation window. Leave as-is with a comment; revisit after the honest signal has run.
- **Hard-deleting junk immediately.** Resist. Tombstone-first (supersede) is the safe activation; hard-delete can follow once apply-mode tombstoning is trusted against the #2200 metrics.
- **Judging crashed-session injections against a partial response with the LLM.** Overkill and expensive; there may be no coherent response to judge. `deferred` is the correct neutral resolution.

## Risks

### Risk 1: Apply-mode pruning removes a record that was actually valuable
**Impact:** A useful memory disappears from recall.
**Mitigation:** Tombstone-first (supersede via `superseded_by`, not hard-delete) — reversible. Importance floor + per-run caps stay. #2200 metrics make every apply-mode deletion observable. Emergency brake: env override still forces dry-run.

### Risk 2: Sweep reflection resolves a still-live session's sidecar as `deferred`
**Impact:** A live session's injections get a premature neutral outcome; the clean-stop path would then double-resolve.
**Mitigation:** Gate on session-not-live AND a generous `INJECTION_RESOLVE_TTL` (longer than any plausible single turn). `deferred` is idempotent-safe (no effects), and cleanup removes the sidecar so the stop handler finds nothing to double-count.

### Risk 3: `reflections.yaml` change doesn't propagate (it's the iCloud vault file, gitignored)
**Impact:** Code ships but the new schedule/apply-params never take effect on any machine.
**Mitigation:** See Update System — the vault `reflections.yaml` edit is a real, required step and must be called out explicitly; the code changes (params support) are inert without it.

## Race Conditions

### Race 1: sweep reflection vs. a session's own Stop-hook resolution
**Location:** `reflections/memory/memory_outcome_resolve.py` (new) vs. `memory_bridge.py:897-907`
**Trigger:** A session finishes cleanly at nearly the same tick the sweep runs.
**Data prerequisite:** The sidecar must still exist and be unresolved for the sweep to act.
**State prerequisite:** Session liveness must be readable.
**Mitigation:** Sweep only touches sidecars for **non-live** sessions past TTL; a cleanly-finishing session is either still live or has already deleted its sidecar. `deferred` is a no-op outcome, so even a rare double-resolve causes no confidence corruption.

### Race 2: concurrent prune + dedup mutating the same record
**Location:** `memory-decay-prune` and `memory-dedup` reflections
**Trigger:** Both scheduled daily; could overlap.
**Data prerequisite:** Both read/write `superseded_by`.
**State prerequisite:** A record already superseded by dedup must be skipped by prune.
**Mitigation:** Decay-prune already skips `memory.superseded_by` records (`memory_decay_prune.py:142-143`). Tombstone-first keeps both operations supersede-based and idempotent. Per-run caps bound blast radius.

## No-Gos (Out of Scope)

- [DESTRUCTIVE] Hard-deleting the ~59 pre-existing fragment records (from #2215's deferred cleanup) in this pass. Apply-mode here is **tombstone-first only**; irreversible hard-delete of the standing pool waits until tombstoning is trusted against #2200 metrics.
- [SEPARATE-SLUG] Per-instance confidence-modulated decay-rate (plan Phase 5) — substrate work, filed separately in popoto; explicitly dropped in this issue's recon.
- Retuning the dismissal-decay constants (threshold/factor/floor) — deliberately deferred to its own observation window (see Rabbit Holes); this is a *value-choice* deferral, not an operator/world action, so it carries no anti-criterion.

## Update System

**This feature requires a manual vault-config step and normal code propagation.**

- **`reflections.yaml` is the iCloud vault file** (`~/Desktop/Valor/reflections.yaml`), gitignored in-repo. The new `memory-embedding-backfill` entry and the `params: {apply: true}` additions to `memory-decay-prune` / `memory-dedup` MUST be made in the vault file so they iCloud-sync to every machine. This propagation is config-only (no git commit for the yaml itself).
- **Code changes** (sweep reflection module, `params` kwargs, fallback fix, counters) propagate normally via git/PR + `/update` (`uv sync`, no new dependency).
- **No new dependency** and **no new env var required** (env vars become optional emergency brakes; activation is config-driven per the subconscious "no load-bearing operator step" constraint).
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
- [ ] Docstring on `memory_outcome_resolve.run` explaining orphaned-sidecar semantics and the TTL gate.
- [ ] Comment at the fallback branch in `detect_outcomes_async` explaining why it emits `deferred` (precision over recall).
- [ ] Comment at the dismissal-reset branch noting the reset-on-`acted` rule is now trustworthy post-fix.
- [ ] Grain-of-salt comment on the new `INJECTION_RESOLVE_TTL` constant (provisional/tunable).

## Success Criteria

- [ ] Injections from a killed/crashed session receive outcome resolution (test: simulate session death, leave an orphaned sidecar, run the sweep, assert injections resolved to `deferred`); none silently lost.
- [ ] The non-LLM fallback path never emits `acted` — its outcomes are `deferred` (unit test on `detect_outcomes_async` fallback branch).
- [ ] `memory-embedding-backfill` appears in `reflections.yaml` and loads via `python -m reflections --dry-run` (exit 0).
- [ ] `memory-decay-prune` and `memory-dedup` run in apply mode via `params`, tombstone-first, with per-run caps respected; `prune_count`/`dedup_merge_count` appear in `/memories/metrics.json`.
- [ ] A demonstrably junky record (0% act rate, low confidence) is observed to leave the active corpus (superseded/tombstoned) with zero human action.
- [ ] All activation is automatic — no operator env step is load-bearing (env vars are emergency brakes only).
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
- Add a `deferred` branch to `persist_outcome_data` (no change to dismissal_count).
- Add/adjust unit tests; add comment explaining precision-over-recall.

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
- Gate on session-not-live AND mtime > TTL; resolve to `deferred`; cleanup; per-run cap; fail-silent.

### 3. Activate pruning (params + apply + counters)
- **Task ID**: build-activate-pruning
- **Depends On**: none
- **Validates**: tests/unit/test_decay_prune_apply_params.py, test_dedup_apply_params.py (create)
- **Informed By**: spike-2 (params forwarding), #2201 counter pattern
- **Assigned To**: pruning-builder
- **Agent Type**: builder
- **Parallel**: true
- **Domain**: Redis/Popoto data
- Add `params` kwarg to `memory_decay_prune.run` and `run_consolidation` (precedence: params > env > dry-run default).
- Make decay tier-1 tombstone-first (supersede, not delete); keep floor + caps.
- Emit `prune_count`/`dedup_merge_count` via the gate-counter pattern; surface in `ui/data/memories.py::get_corpus_metrics`.

### 4. reflections.yaml activation (vault config)
- **Task ID**: build-reflections-config
- **Depends On**: build-activate-pruning
- **Assigned To**: pruning-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `memory-embedding-backfill` entry to `~/Desktop/Valor/reflections.yaml`.
- Add `params: {apply: true}` to `memory-decay-prune` and `memory-dedup`; add the `memory-outcome-resolve` entry.
- Verify `python -m reflections --dry-run` exits 0 and the update-step migration stays inert.

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
| Backfill scheduled | `grep -c 'memory-embedding-backfill' ~/Desktop/Valor/reflections.yaml` | output > 0 |
| Registry loads | `python -m reflections --dry-run` | exit code 0 |
| No hard-delete in decay tier-1 | `grep -n '\.delete()' reflections/memory/memory_decay_prune.py` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Tombstone semantics for tier-1 decay-prune**: confirm reusing `superseded_by` (currently a dedup concept) as the tombstone marker is acceptable, or whether a distinct `tombstoned_at`/status field is preferred to keep "pruned by decay" distinguishable from "merged by dedup" in metrics. (Plan assumes reuse of the supersede path, distinguished by counter name.)
2. **Sweep cadence & TTL**: what `INJECTION_RESOLVE_TTL` and reflection interval balance "resolve crashed sessions promptly" against "never touch a live session"? (Plan proposes a TTL comfortably longer than any single turn + daily-or-faster sweep; exact value provisional.)
3. **Apply-mode blast-radius comfort**: given #2200 baseline now exists, is it acceptable to enable apply-mode (tombstone-first) immediately on merge, or should it ride one dry-run confirmation cycle against live metrics first?
