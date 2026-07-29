---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-29
tracking: https://github.com/tomcounsell/ai/issues/2438
last_comment_id:
---

# Subconscious Memory: Corpus-Collapse Guardrails & Prune Reconciliation

## Problem

The subconscious-memory corpus silently collapsed from **1,991 records to 1** between 2026-07-22 and 2026-07-28. Nothing alerted. The loss was found by accident during an unrelated `/doctor` pass. Every agent session across every project lost months of accumulated corrections, decisions, patterns, and surprises.

**Current behavior:**
- The `memory-decay-prune` reflection runs **daily in apply mode** and its **tier-1 path hard-deletes** records (`memory.delete()`, no tombstone) — consistent with the observed `superseded_count == 0`.
- The tier-1 selection predicate coerces **missing** `importance`/`access_count` to `0.0`/`0` via `or`-defaulting, so a record with absent fields qualifies for hard-delete.
- The config says `params: {apply: true}` while the yaml `description`, module docstring, `docs/features/subconscious-memory.md`, and `CLAUDE.md` all describe the prune as "dry-run default". Same divergence for `memory-dedup`.
- There is **no corpus-level anomaly detection**: a drop from ~2000 records to 1 produced no alert, no log, no user-visible signal.
- `Reflection.last_run` is `None` for both memory reflections, so we cannot even confirm from run-tracking whether/when the prune executed.

**Desired outcome:**
- A single prune run can never delete an unbounded fraction of the corpus. A large drop is **surfaced automatically** (human alert) rather than discovered by accident weeks later.
- The prune's actual apply/dry-run posture is intentional, singular, and documented consistently everywhere.
- The tier-1 predicate cannot select records merely because a field is absent.
- The bulk-deletion mechanism is investigated to a defensible conclusion (or explicitly declared unrecoverable), so we know whether hypothesis 1 was even the cause.

## Freshness Check

**Baseline commit:** `060e2f791`
**Issue filed at:** 2026-07-29T05:50:24Z (planned same day)
**Disposition:** **Unchanged**

**File:line references re-verified:**
- `reflections/memory/memory_decay_prune.py:129-142` (`_resolve_tier_apply`) — still present; env-as-kill-switch fallback to `params.get("apply", False)` confirmed.
- `reflections/memory/memory_decay_prune.py:80` (`MAX_PRUNE_PER_RUN = 50`) and `:254` (`capped = to_prune[:MAX_PRUNE_PER_RUN]`) — cap confirmed applied before the delete loop.
- `reflections/memory/memory_decay_prune.py:~226-236` — null-coalescing `importance = memory.importance or 0.0`, `access_count = memory.access_count or 0` confirmed.
- `reflections/memory/memory_decay_prune.py:~299` — tier-1 `memory.delete()` hard-delete confirmed.
- `config/reflections.yaml:333-343` (`memory-decay-prune`, `params: {apply: true}`) and `:151-160` (`memory-dedup`, `apply: true`) confirmed.
- `models/memory.py` — no `Meta.ttl`/`expire`; TTL ruled out.

**Cited sibling issues/PRs re-checked:**
- #2203 (activated pruning) — merged as commit `0e888123c` on 2026-07-23, the first day of the loss window. Temporal correlation for hypothesis 1 being *involved*.
- #2435 (stop-hook timeouts) — explains non-refill, **not** deletion. Kept strictly separate.
- #2207 (phantom AgentSession keys), #1814 (AOF disabled), #1231 (memory health audit), #1214 (orphan `.npy` sidecars) — landscape unchanged.

**Commits on main since issue filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** none found touching `reflections/memory/` or the memory corpus.

**Notes:** On this checkout `config/reflections.yaml` is a **regular file**, not the vault symlink the standing note describes — flagged as a build-time verification (machine-local vs shared `apply:true`).

## Prior Art

- **#2203** — "Outcome-loop hardening: … activate pruning reflections" (merged `0e888123c`, 2026-07-23). This is the change that flipped `apply: true` and introduced the tier-1 hard-delete rationale. It shipped the 50/run cap but **no corpus-fraction guardrail and no anomaly alert** — the gap this plan closes.
- **#1822** — "Memory extraction: three systematic noise sources" (merged). Introduced tier-2 (noise) pruning. Established the `MAX_PRUNE_PER_RUN` cap and the never-reinforced confidence filter.
- **#1231** — "Memory health audit: 3-layer always-apply reflection (cleanup + anomaly detection + gemma classification)". Shipped `memory-quality-audit` with "anomaly detection", yet it did **not** fire on a ~2000→1 collapse. This plan must determine whether its anomaly layer covers **corpus-count deltas** at all (recon suggests it flags per-record quality, not corpus-level collapse).

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR for #2203 | Activated apply-mode pruning; capped tier-1 at 50/run | Capped a single run but added **no corpus-fraction ceiling and no drop alert**; left the null-coalescing predicate and the doc/config "dry-run default" contradiction in place |
| PR for #1231 | Added a "memory health audit … anomaly detection" reflection | Anomaly layer evidently operates at the **per-record quality** granularity, not **corpus-size delta** — so a total collapse went unsurfaced |

**Root cause pattern:** Every prior guardrail bounded *per-record* or *per-run* behavior. None watched the **aggregate corpus size over time**, and none treated a large deletion as an event requiring human confirmation. Defense was applied one record at a time; the corpus as a whole had no monitor.

## Solution

### Key Elements

- **Corpus-fraction guardrail (memory_decay_prune)**: Before executing any hard-delete, compute the durable corpus size; if this run's tier-1 delete set would exceed `MAX_PRUNE_FRACTION` (percent of corpus) **or** an absolute `MAX_PRUNE_ABSOLUTE` floor, **abort the apply path, emit a human alert, and fall back to dry-run reporting**. Named, env-overridable constants with grain-of-salt comments.
- **Predicate hardening (memory_decay_prune)**: A record only qualifies for tier-1 when `importance` and `access_count` are **explicitly present and numeric**. A missing/None field means "unknown", which must be **exempt**, never coerced to a deletable `0.0`/`0`.
- **Corpus-size anomaly detection + alert**: A reflection that records the durable corpus size each run and fires `human_alert_needed` when the count drops by more than a threshold vs. the last recorded baseline. Either extend `memory-quality-audit` (#1231) or add a small dedicated check — decided after confirming what #1231 actually covers.
- **Config/doc reconciliation**: Make the apply/dry-run posture singular and consistent across `config/reflections.yaml` (description + params), the module docstring, `docs/features/subconscious-memory.md`, and `CLAUDE.md`, for **both** `memory-decay-prune` and `memory-dedup`. The dangerous surface is tier-1 hard-delete, not the tier-2 tombstone.
- **Forensic conclusion (bounded)**: Establish whether tier-1 prune could even have deleted ~1990 records (it is capped at 50/run and was only active from 2026-07-23), resolve the `Reflection.last_run == None` question, and check `.npy` sidecars / distilled-ingest report for what was lost. Land a written conclusion, not open-ended digging.

### Flow

Prune run starts → count durable corpus → select tier-1 candidates (present-field predicate) → **guardrail: would-delete count vs. MAX_PRUNE_FRACTION/MAX_PRUNE_ABSOLUTE?** → if exceeded: emit human alert + dry-run report, delete nothing → else: delete (still capped at MAX_PRUNE_PER_RUN) → record corpus size for anomaly baseline → anomaly reflection compares against prior baseline → large drop → human alert.

### Technical Approach

- **Guardrail placement**: In `run()` after `capped` is computed and before the tier-1 delete loop. Compute `durable_total = len([m for m in all_memories if not m.superseded_by])`. If `decay_apply` and (`len(tier1_pruned) > MAX_PRUNE_ABSOLUTE` or `len(tier1_pruned)/max(durable_total,1) > MAX_PRUNE_FRACTION`), skip deletes, append a loud finding, and route a human alert through the existing alert path (mirror the crash-tracker `human_alert_needed` signal, #2396). Keep tier-2 tombstoning independent (reversible, lower risk) — the guardrail gates the **hard-delete** tier.
- **Predicate hardening**: Replace `importance = memory.importance or 0.0` / `access_count = memory.access_count or 0` with explicit `None`-checks: if either is `None`, `continue` (exempt). Preserve current behavior for genuine `0`/`0.0` values. Note Popoto bool/number storage quirks (`reference_popoto_bool_storage`) — verify a stored-but-zero `access_count` still reads as numeric `0`, not a string.
- **Anomaly detection**: Prefer extending the existing `memory-quality-audit` reflection so there is one corpus monitor, not two. Persist the last-seen durable count via a small Popoto record or a gate counter (`models/memory_gate.py` already has `_increment_gate_counter`), compare each run, and alert on a drop beyond `CORPUS_DROP_ALERT_FRACTION`. If a Popoto model changes/adds, add an idempotent migration per the addendum's Popoto Schema Migration Requirement.
- **Config posture decision**: The intended posture is an **Open Question** for the supervisor. Default recommendation: keep tier-2 tombstoning in apply mode (safe/reversible) but make tier-1 hard-delete require an explicit, documented opt-in and never inherit `apply` silently — i.e. decouple the two tiers' apply resolution at the config layer, matching the code's already-separate env vars.
- **Constants**: `MAX_PRUNE_FRACTION` (e.g. 0.05), `MAX_PRUNE_ABSOLUTE` (e.g. 25), `CORPUS_DROP_ALERT_FRACTION` (e.g. 0.10) — all named module constants, env-overridable, with grain-of-salt "provisional/tunable" comments (`feedback_provisional_magic_numbers`).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `memory_decay_prune.py` wraps each `delete()`/`save()` in `try/except Exception` with `logger.warning`. Add a test asserting the guardrail-abort path emits an observable signal (human alert + finding string), not a silent swallow.
- [ ] Anomaly reflection: assert that when the alert fires it produces an observable side effect (alert record / log), and that a read failure falls back without crashing the reflection run.

### Empty/Invalid Input Handling
- [ ] Test tier-1 predicate with `importance=None` and `access_count=None` — record must be **exempt** (not selected).
- [ ] Test guardrail with `durable_total == 0` (empty corpus) — no divide-by-zero, no deletes.
- [ ] Test anomaly detection with no prior baseline recorded — must initialize, not alert spuriously.

### Error State Rendering
- [ ] Guardrail trip must surface a loud, human-readable finding and a human alert (user-visible), not merely a debug log.

## Test Impact

- [ ] `tests/` — search for existing `memory_decay_prune` / `run_memory_decay_prune` tests — UPDATE: any test asserting tier-1 selects `importance or 0.0`-style records must change to the present-field predicate.
- [ ] `tests/` — existing `memory-quality-audit` tests — UPDATE if the anomaly monitor is folded into it (new baseline-tracking behavior).
- [ ] New unit tests (REPLACE/ADD): guardrail abort, predicate exemption for None fields, anomaly alert on large drop, empty-corpus safety.

(Build must run `grep -rl "memory_decay_prune\|run_memory_decay_prune" tests/` to enumerate the exact affected files before editing; the plan cannot assert file paths that may not yet exist.)

## Rabbit Holes

- **Full forensic recovery of the 1,990 lost records.** AOF is disabled (#1814); point-in-time Redis recovery is almost certainly impossible. Bound the forensic effort to a written conclusion + a best-effort `.npy`/distilled-report salvage; do not build a recovery pipeline.
- **Rewriting memory extraction / stop-hook (#2435).** That is the non-refill cause and a separate issue. Do not touch it here.
- **Chasing hypothesis 2 into the AgentSession cleanup code.** Confirm-or-rule-out by a scan-pattern read only; do not refactor the phantom-key cleanup.
- **Redesigning the decay/importance model.** The scoring model is out of scope; this plan hardens the *deletion* path and adds monitoring.

## Risks

### Risk 1: Guardrail blocks legitimate large cleanups
**Impact:** A genuine one-time large purge (e.g. a real noise flood) gets blocked and requires manual override.
**Mitigation:** Provide an explicit env override to raise the ceiling for a single intentional run; the default posture must fail safe (block + alert), since silent over-deletion is the failure we are fixing.

### Risk 2: Anomaly baseline itself drifts or is corrupted
**Impact:** A wrong baseline suppresses a real alert or fires false alarms.
**Mitigation:** Store baseline through Popoto (never raw Redis), initialize conservatively (no alert on first run), and compare against the max of recent baselines, not a single possibly-already-collapsed sample.

### Risk 3: apply:true is machine-shared, so other machines already lost their corpora
**Impact:** Fix on one machine doesn't protect others; damage may be wider than observed.
**Mitigation:** Build task explicitly resolves whether `config/reflections.yaml` is a shared vault symlink or machine-local, and the config change propagates via `/update` if shared.

## Race Conditions

No race conditions identified — the prune reflection runs single-threaded on the daily scheduler cadence and reads/writes Memory records sequentially. Corpus-count baseline read-then-write is within one reflection invocation with no concurrent prune (the scheduler does not overlap runs of the same reflection).

## No-Gos (Out of Scope)

- [DESTRUCTIVE] Recovering/rehydrating the 1,990 deleted records from Redis persistence — AOF is disabled (#1814), so no reliable point-in-time source exists; best-effort salvage from `.npy` sidecars / distilled-ingest report is in scope, but a recovery guarantee is not.
- [SEPARATE-SLUG #2435] Fixing stop-hook timeouts / corpus refill — different root cause; explicitly kept separate per the issue.
- [SEPARATE-SLUG #2207] Root-causing the phantom AgentSession key collapse — only its *code-path overlap* with `Memory:*` deletes is in scope here (confirm/rule-out hypothesis 2), not fixing that incident.

## Update System

`config/reflections.yaml` is propagated per-machine (vault symlink per the standing note, though a regular file on this checkout — to be confirmed). If the apply-posture change is to a shared file, the `/update` skill / `scripts/remote-update.sh` must propagate the corrected config so no machine keeps `apply:true` on the hard-delete tier. Build task must verify machine-locality and note propagation requirements. No new dependencies.

## Agent Integration

No new agent/tool surface required — this is a reflection-internal change. The human-alert path already exists (crash-tracker `human_alert_needed`, #2396); the guardrail and anomaly detector reuse it. No new MCP tool, no `bridge/telegram_bridge.py` import changes. Verify the alert actually reaches a human channel via the existing reflection→alert wiring in an integration test.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/subconscious-memory.md` — document the corpus-fraction guardrail, the anomaly alert, and the corrected apply/dry-run posture (remove the stale "dry-run default" claim or make it accurate).
- [ ] Update `CLAUDE.md` memory section to match the reconciled posture (it currently says "Dry-run default").
- [ ] Add/adjust entry in `docs/features/README.md` if a new monitor is introduced.

### Inline Documentation
- [ ] Docstring update in `memory_decay_prune.py` reflecting the guardrail and the present-field predicate.
- [ ] Grain-of-salt comments on the new provisional constants.

## Success Criteria

- [ ] `memory_decay_prune` tier-1 aborts (deletes nothing) and emits a human alert when a run would delete more than `MAX_PRUNE_FRACTION` of the durable corpus or more than `MAX_PRUNE_ABSOLUTE` records.
- [ ] Records with `importance is None` or `access_count is None` are exempt from tier-1 selection (unit test proves it).
- [ ] A corpus-size drop beyond `CORPUS_DROP_ALERT_FRACTION` produces a human alert (unit/integration test proves it).
- [ ] `config/reflections.yaml`, the module docstring, `docs/features/subconscious-memory.md`, and `CLAUDE.md` state one consistent apply/dry-run posture for `memory-decay-prune` and `memory-dedup`.
- [ ] Written forensic conclusion recorded in the issue: whether tier-1 prune could account for the loss (given the 50/run cap and 2026-07-23 activation), the resolution of `Reflection.last_run == None`, and disposition of hypotheses 2/4/5.
- [ ] Machine-locality of `apply:true` resolved and propagation handled if shared.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (prune-guardrail)**
  - Name: prune-guardrail-builder
  - Role: Predicate hardening + corpus-fraction guardrail in `memory_decay_prune.py`
  - Agent Type: builder
  - Resume: true

- **Builder (anomaly-monitor)**
  - Name: anomaly-monitor-builder
  - Role: Corpus-size baseline tracking + drop alert (extend `memory-quality-audit`)
  - Agent Type: builder
  - Resume: true

- **Builder (config-doc-reconcile)**
  - Name: config-doc-builder
  - Role: Reconcile apply/dry-run posture across config + docs + docstrings
  - Agent Type: builder
  - Resume: true

- **Forensic (root-cause)**
  - Name: forensic-analyst
  - Role: Bounded forensic conclusion (window, last_run, hypotheses 2/4/5, salvage)
  - Agent Type: general-purpose
  - Resume: true

- **Validator**
  - Name: memory-guardrail-validator
  - Role: Verify all success criteria and failure-path tests
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Predicate hardening + corpus-fraction guardrail
- **Task ID**: build-guardrail
- **Depends On**: none
- **Validates**: new tests under `tests/` for `memory_decay_prune` (predicate exemption, guardrail abort, empty-corpus safety)
- **Assigned To**: prune-guardrail-builder
- **Agent Type**: builder
- **Domain**: redis-popoto
- **Parallel**: true
- Replace `or`-defaulting with explicit `None`-exemption for `importance`/`access_count`.
- Add `MAX_PRUNE_FRACTION`, `MAX_PRUNE_ABSOLUTE` named env-overridable constants.
- Insert guardrail before the tier-1 delete loop: compute durable total, abort + human-alert + dry-run report when exceeded; leave tier-2 tombstoning independent.

### 2. Corpus-size anomaly detection + alert
- **Task ID**: build-anomaly
- **Depends On**: none
- **Validates**: new tests for baseline init (no false alert), drop alert fires
- **Assigned To**: anomaly-monitor-builder
- **Agent Type**: builder
- **Domain**: redis-popoto
- **Parallel**: true
- First read `reflections/memory/memory_quality_audit.py` to confirm whether #1231's anomaly layer already covers corpus-count; extend it if so, else add a minimal monitor.
- Persist last-seen durable count via Popoto / gate counter (no raw Redis). Add an idempotent migration if a model changes.
- Fire `human_alert_needed` on a drop beyond `CORPUS_DROP_ALERT_FRACTION`.

### 3. Config + doc reconciliation
- **Task ID**: build-reconcile
- **Depends On**: none
- **Assigned To**: config-doc-builder
- **Agent Type**: builder
- **Parallel**: true
- Decide posture per Open Question answer; update `config/reflections.yaml` description + params for `memory-decay-prune` and `memory-dedup`.
- Verify whether `config/reflections.yaml` is a shared vault symlink or machine-local; note propagation.
- Align `memory_decay_prune.py` docstring, `docs/features/subconscious-memory.md`, and `CLAUDE.md`.

### 4. Bounded forensic conclusion
- **Task ID**: forensic-conclusion
- **Depends On**: none
- **Assigned To**: forensic-analyst
- **Agent Type**: general-purpose
- **Parallel**: true
- Given the 50/run cap + 2026-07-23 activation, quantify the max tier-1 deletions possible in the window and state whether hypothesis 1 can be the sole cause.
- Resolve `Reflection.last_run == None`: did the prune ever run, or is run-tracking broken?
- Confirm/rule-out hypothesis 2 by reading the #2207 AgentSession cleanup for any scan pattern that could match `Memory:*`.
- Best-effort salvage check: `.npy` sidecars (#1214), `docs/baselines/memory-distilled-ingest-report.json`.
- Post the written conclusion to issue #2438.

### 5. Validation
- **Task ID**: validate-all
- **Depends On**: build-guardrail, build-anomaly, build-reconcile, forensic-conclusion
- **Assigned To**: memory-guardrail-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all tests + lint/format; verify each success criterion and failure-path test.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: config-doc-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Finalize `docs/features/subconscious-memory.md`, `CLAUDE.md`, and `docs/features/README.md` updates.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q -k memory_decay_prune or memory_quality` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No `or 0.0` predicate remains | `grep -n "importance or 0.0\|access_count or 0" reflections/memory/memory_decay_prune.py` | exit code 1 |
| Guardrail constant present | `grep -c "MAX_PRUNE_FRACTION" reflections/memory/memory_decay_prune.py` | output > 0 |
| Anomaly alert wired | `grep -rc "CORPUS_DROP_ALERT_FRACTION\|human_alert" reflections/memory/` | output > 0 |
| Docs posture consistent | `grep -rn "dry-run default\|dry_run default" config/reflections.yaml docs/features/subconscious-memory.md CLAUDE.md` | output does not contain apply |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Intended apply posture for tier-1 hard-delete.** Should `memory-decay-prune` tier-1 hard-delete run in apply mode at all by default, or should it require an explicit per-machine opt-in (env var) while tier-2 tombstoning stays on? Recommendation: decouple — tier-2 apply on (reversible), tier-1 apply off unless explicitly enabled.
2. **Guardrail thresholds.** Are `MAX_PRUNE_FRACTION=0.05`, `MAX_PRUNE_ABSOLUTE=25`, `CORPUS_DROP_ALERT_FRACTION=0.10` acceptable starting values? (All env-overridable and provisional.)
3. **Machine scope.** Is `config/reflections.yaml`'s `apply:true` shared across all machines (vault symlink) or local to this one? This determines whether other machines' corpora are also at risk and whether the config fix must propagate via `/update`.
