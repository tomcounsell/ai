---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-29
tracking: https://github.com/tomcounsell/ai/issues/2438
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-29T07:54:11Z
---

# Subconscious Memory: Corpus-Collapse Guardrails & Prune Reconciliation

## Problem

The subconscious-memory corpus silently collapsed from **1,991 records to 1** between 2026-07-22 and 2026-07-28. Nothing alerted. The loss was found by accident during an unrelated `/doctor` pass. Every agent session across every project lost months of accumulated corrections, decisions, patterns, and surprises.

**Current behavior:**
- The `memory-decay-prune` reflection runs **daily in apply mode** and its **tier-1 path hard-deletes** records (`memory.delete()`, no tombstone) — consistent with the observed `superseded_count == 0`.
- The tier-1 selection predicate coerces **missing** `importance`/`access_count` to `0.0`/`0` via `or`-defaulting, so a record with absent fields qualifies for hard-delete. **This is a latent hazard we are hardening, not a proven cause of the collapse** — the 50/run cap and the 2026-07-23 activation date mean tier-1 could delete at most a few hundred records across the whole window, far short of the ~1990 lost. The mechanism that removed the larger ~1690-record bulk remains **unproven and open** (the forensic task below narrows it; the code guardrails are defense-in-depth, not a claimed fix for the bulk loss).
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
- `reflections/memory/memory_decay_prune.py:145` (`_resolve_tier_apply`) — still present; env-as-kill-switch fallback to `params.get("apply", False)` confirmed.
- `reflections/memory/memory_decay_prune.py:80` (`MAX_PRUNE_PER_RUN = 50`) and `:254` (`capped = to_prune[:MAX_PRUNE_PER_RUN]`) — cap confirmed applied before the delete loop.
- `reflections/memory/memory_decay_prune.py:207`/`:211` — null-coalescing `importance = memory.importance or 0.0`, `access_count = memory.access_count or 0` confirmed.
- `reflections/memory/memory_decay_prune.py:296` — tier-1 `memory.delete()` hard-delete confirmed.
- `config/reflections.yaml:333-343` (`memory-decay-prune`, `params: {apply: true}`) and `:151-160` (`memory-dedup`, `apply: true`) confirmed.
- `models/memory.py` — no `Meta.ttl`/`expire`; TTL ruled out.

**Cited sibling issues/PRs re-checked:**
- #2203 (activated pruning) — merged as commit `0e888123c` on 2026-07-23, the first day of the loss window. Temporal correlation for hypothesis 1 being *involved*.
- #2435 (stop-hook timeouts) — explains non-refill, **not** deletion. Kept strictly separate.
- #2207 (phantom AgentSession keys), #1814 (AOF disabled), #1231 (memory health audit), #1214 (orphan `.npy` sidecars) — landscape unchanged.

**Commits on main since issue filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** none found touching `reflections/memory/` or the memory corpus.

**Notes (Open Question 3 — RESOLVED):** On this checkout `config/reflections.yaml` is a **regular file** (`ls -la` shows no symlink), **not** the vault symlink the standing `reference_reflections_config` note describes. So on this machine the `apply:true` change is machine-local and does not auto-propagate. The build must make the change **machine-safe**: edit `config/reflections.yaml` here, and because the file's shared-vs-local status differs per machine, the config reconciliation (description + params) and the code-level tier-1 default-off decoupling together ensure the safe posture holds **regardless** of whether a given machine's copy is a symlink or a regular file. The code default (tier-1 off unless `MEMORY_DECAY_PRUNE_APPLY=true`) is the durable guarantee; the yaml `params.apply` only governs the reversible tier-2 tombstone. This caveat is carried into the Update System and Risk 3 sections.

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

- **Two distinct, non-redundant guardrails (reconciled).** These operate at different times on different signals and do not duplicate each other:
  - **(1) Corpus-fraction guardrail (memory_decay_prune, per-run, preventive)**: *Before* executing any hard-delete, compute the durable corpus size; if this run's tier-1 delete set would exceed `MAX_PRUNE_FRACTION` (percent of corpus) **or** an absolute `MAX_PRUNE_ABSOLUTE` floor, **abort the apply path, file a GitHub alert issue, and fall back to dry-run reporting**. This is **forward-looking defense-in-depth** on the *write* side: because the shared `MAX_PRUNE_PER_RUN=50` cap already bounds a single run to ≤50 tier-1 deletes (see Tech-Debt note under Critique Results), the fraction ceiling only becomes reachable on a future larger corpus or if the per-run cap is ever raised.
  - **(2) Corpus-size anomaly detector (memory-quality-audit, cross-run, detective)**: records the durable corpus size each run and alerts on a *drop across runs* regardless of which mechanism caused it (prune, phantom-key sweep, manual, or unknown). This is the *read* side and is the mechanism that would have caught the motivating ~1990→1 collapse; the fraction guardrail cannot, because that collapse did not flow through tier-1's capped delete loop. They are complementary — (1) blocks a runaway prune before it writes, (2) surfaces any collapse after the fact — not two copies of the same check.
- **Predicate hardening (memory_decay_prune)**: A record only qualifies for tier-1 when `importance` and `access_count` are **explicitly present and numeric**. A missing/None field means "unknown", which must be **exempt**, never coerced to a deletable `0.0`/`0`. (Hardening of a latent hazard — see Problem; not a claimed cause of the collapse.)
- **Corpus-size anomaly detection + alert**: Extend `memory-quality-audit` (#1231) so there is one corpus monitor that records the durable corpus size each run and **files a GitHub issue via the established alert channel** (`_find_recent_audit_issue` title-prefix dedup + `_file_anomaly_issue`, `memory_quality_audit.py:549-678`) when the count drops by more than `CORPUS_DROP_ALERT_FRACTION` vs. the **max of recent recorded baselines** (a bounded ring, not a single sample — see Technical Approach and Risk 2). Two additional safety rules make it robust to being deployed *into* an already-collapsed corpus: (a) a **first-run absolute floor** — when no prior baseline exists and the observed count is below `CORPUS_MIN_HEALTHY_FLOOR`, file the alert immediately instead of silently baseline-locking the collapse in; (b) an **alert-channel fallback** — if `gh issue create` fails (auth/network), emit a loud durable `logger.error` (not a swallowed `warning`) so a real collapse is never lost to a single failed subprocess.
- **Config/doc reconciliation**: Make the apply/dry-run posture singular and consistent across `config/reflections.yaml` (description + params), the module docstring, `docs/features/subconscious-memory.md`, and `CLAUDE.md`, for **both** `memory-decay-prune` and `memory-dedup`. The dangerous surface is tier-1 hard-delete, not the tier-2 tombstone. **Decided posture (was Open Question 1): decouple the tiers** — tier-2 tombstoning stays in apply mode (reversible), while tier-1 hard-delete requires an explicit per-machine opt-in (`MEMORY_DECAY_PRUNE_APPLY=true`, default off) and never inherits the shared `params.apply` silently.
- **Forensic conclusion (bounded)**: Establish whether tier-1 prune could even have deleted ~1990 records (it is capped at 50/run and was only active from 2026-07-23), resolve the `Reflection.last_run == None` question, and check `.npy` sidecars / distilled-ingest report for what was lost. Land a written conclusion, not open-ended digging.

### Flow

Prune run starts → count durable corpus → select tier-1 candidates (present-field predicate) → **guardrail: would-delete count vs. MAX_PRUNE_FRACTION/MAX_PRUNE_ABSOLUTE?** → if exceeded: file GitHub alert issue + dry-run report, delete nothing → else: delete (still capped at MAX_PRUNE_PER_RUN) → append corpus size to the bounded baseline ring (Popoto model) → anomaly reflection (`memory-quality-audit`) compares observed count against the **max of the recent baseline ring** (and, on first run with no ring, against `CORPUS_MIN_HEALTHY_FLOOR`) → drop beyond `CORPUS_DROP_ALERT_FRACTION` (or sub-floor first run) → GitHub alert issue via the existing audit alert channel (with a loud `logger.error` fallback if `gh` fails).

### Technical Approach

- **Guardrail placement**: In `run()` after `capped` is computed and before the tier-1 delete loop. Compute `durable_total = len([m for m in all_memories if not m.superseded_by])`. If `decay_apply` and (`len(tier1_pruned) > MAX_PRUNE_ABSOLUTE` or `len(tier1_pruned)/max(durable_total,1) > MAX_PRUNE_FRACTION`), skip deletes, append a loud finding, and **file a GitHub alert issue** through the same `gh issue create` path `memory_quality_audit.py` Layer 2/3 uses (`_file_anomaly_issue` + `_find_recent_audit_issue` title-prefix dedup, `memory_quality_audit.py:549-678`). Do **not** import `bridge_watchdog.human_alert_needed` — that signal lives only in `monitoring/bridge_watchdog.py` (a launchd watchdog process, verified: `grep -rn "human_alert_needed" monitoring/ reflections/` shows it exclusively in `bridge_watchdog.py`) and is not reachable from the `python -m reflections` subprocess. Keep tier-2 tombstoning independent (reversible, lower risk) — the guardrail gates the **hard-delete** tier.
- **Predicate hardening**: Replace `importance = memory.importance or 0.0` (`memory_decay_prune.py:207`) / `access_count = memory.access_count or 0` (`:211`) with explicit `None`-checks: if either is `None`, `continue` (exempt). Preserve current behavior for genuine `0`/`0.0` values. Note Popoto bool/number storage quirks (`reference_popoto_bool_storage`) — verify a stored-but-zero `access_count` still reads as numeric `0`, not a string.
- **Anomaly detection (bounded baseline ring + first-run floor)**: Extend the existing `memory-quality-audit` reflection so there is one corpus monitor and the alert reuses that module's `gh issue create` channel. Persist recent durable counts via a **dedicated Popoto model** `models/memory_corpus_baseline.py` — a **single-row `CorpusSizeBaseline` holding a bounded ring of the last N `(size, recorded_at)` samples**, not a single scalar. A single `last_corpus_size` scalar cannot express the Risk-2 mitigation's promise to "compare against the **max of recent baselines**": one already-collapsed sample would silently become the new normal. Store the ring as a JSON-serialized list field (Popoto stores it as a string; use `json.dumps`/`json.loads`, per `reference_popoto_bool_storage` on Popoto's string-typing of non-str fields) capped at `CORPUS_BASELINE_RING_SIZE` most-recent entries (drop-oldest on append), with arbitrary SET via `instance.save()`. **Do NOT use the gate counter** (`models/memory_gate.py::_increment_gate_counter`): it is a raw-Redis `INCR`/`GET`-only monotonic counter with no SET or decrement API, so it structurally cannot hold a corpus size that must *shrink* after legitimate pruning or be re-baselined. Each run: read the ring, compute `baseline = max(size for size,_ in ring)` (the recent high-water mark, so one collapsed sample cannot suppress the alert), and file the alert when `observed / max(baseline,1) < 1 - CORPUS_DROP_ALERT_FRACTION`, then append the observed sample. **First-run / empty-ring floor**: when the ring is empty (`prior_baseline is None`), do **not** unconditionally suppress the alert — if `observed < CORPUS_MIN_HEALTHY_FLOOR`, file the alert immediately. This is the specific guard against deploying the detector *after* the collapse (corpus currently at 1): without it, the baseline would initialize at 1, the standing collapse would never surface, and any future refill would misread as growth. **Alert-channel fallback**: `_file_anomaly_issue` already returns `False` and logs a `warning` on `gh` failure; for the corpus-collapse signal specifically, on that `False`-return also emit a `logger.error` (durable, higher-severity) so an auth/network failure during a real collapse is not swallowed at `warning` level. Because a new Popoto model is added, register an idempotent migration in `scripts/update/migrations.py` (added to the `MIGRATIONS` dict, confirm-readable pattern à la `_migrate_confirm_run_identity_fields_readable`) per the addendum's Popoto Schema Migration Requirement.
- **Config posture decision (Open Question 1 — RESOLVED: decouple)**: Keep tier-2 tombstoning in apply mode (safe/reversible) but make tier-1 hard-delete require an explicit, documented opt-in via `MEMORY_DECAY_PRUNE_APPLY=true` (default off) and never inherit the shared `params.apply` silently — decouple the two tiers' apply resolution so tier-1 no longer falls back to `params.get("apply", False)`. `_resolve_tier_apply` (`memory_decay_prune.py:145`) currently applies the same params fallback to both tiers; the build must give tier-1 its own resolution that defaults off absent the env opt-in.
- **Constants (Open Question 2 — RESOLVED: conservative defaults)**: `MAX_PRUNE_FRACTION = 0.05`, `MAX_PRUNE_ABSOLUTE = 25`, `CORPUS_DROP_ALERT_FRACTION = 0.10`, `CORPUS_MIN_HEALTHY_FLOOR = 50` (first-run/empty-ring absolute floor below which a collapse is alerted even with no baseline), `CORPUS_BASELINE_RING_SIZE = 14` (≈ two weeks of daily samples for the recent high-water mark) — all named module constants, env-overridable, each carrying a grain-of-salt "provisional/tunable" comment (`feedback_provisional_magic_numbers`). These are deliberately conservative starting values; tune from telemetry once the anomaly detector has run for a few cycles.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `memory_decay_prune.py` wraps each `delete()`/`save()` in `try/except Exception` with `logger.warning`. Add a test asserting the guardrail-abort path emits an observable signal (a filed GitHub alert issue + finding string), not a silent swallow. Assert on the `gh issue create` invocation (mock the subprocess) rather than any watchdog signal.
- [ ] Anomaly reflection: assert that when the alert fires it invokes the `gh issue create` path (title-prefix dedup honored), and that a read failure or `gh` failure falls back without crashing the reflection run.
- [ ] **Alert-channel fallback**: when `_file_anomaly_issue` returns `False` (simulated `gh` failure) for the corpus-collapse signal, assert a `logger.error` is emitted — the collapse signal must not be lost at `warning` level (mock the subprocess to non-zero exit / raise).

### Empty/Invalid Input Handling
- [ ] Test tier-1 predicate with `importance=None` and `access_count=None` — record must be **exempt** (not selected).
- [ ] Test guardrail with `durable_total == 0` (empty corpus) — no divide-by-zero, no deletes.
- [ ] Test anomaly detection with no prior baseline recorded **and observed ≥ `CORPUS_MIN_HEALTHY_FLOOR`** — must initialize quietly, not alert spuriously.
- [ ] Test anomaly detection with **empty ring and observed < `CORPUS_MIN_HEALTHY_FLOOR`** (deploy-into-collapse case, corpus==1) — must file the alert, NOT baseline-lock the collapse.
- [ ] Test baseline ring compares against `max` of recent samples: a ring containing one already-collapsed low sample plus healthy highs must still alert on a drop from the high-water mark (single-scalar model would not).
- [ ] Test ring bounding: appending beyond `CORPUS_BASELINE_RING_SIZE` drops the oldest entry; JSON round-trips through Popoto's string storage without type corruption.

### Error State Rendering
- [ ] Guardrail trip must surface a loud, human-readable finding and a filed GitHub alert issue (user-visible), not merely a debug log.

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

### Risk 2: Anomaly baseline itself drifts, is corrupted, or baseline-locks a collapse
**Impact:** A wrong baseline suppresses a real alert or fires false alarms. **The specific failure the model must not allow:** if the baseline is a single scalar and the detector is installed *after* a collapse (corpus at 1), "no alert on first run" would initialize the baseline at 1, the standing collapse would never surface, and any future refill would misread as growth.
**Mitigation:** Store the baseline through a dedicated Popoto model holding a **bounded ring of recent `(size, ts)` samples** (never a single scalar, never the INCR-only gate counter, never raw Redis). Compare each observed count against `max(size for the recent ring)` — a genuine recent high-water mark that one already-collapsed sample cannot lower. To avoid baseline-locking a collapse present at deploy time, do **not** unconditionally suppress on first run: apply an absolute `CORPUS_MIN_HEALTHY_FLOOR` so an empty-ring run observing a sub-floor corpus files the alert immediately. Normal first runs above the floor still initialize quietly.

### Risk 3: apply:true posture may differ per machine, so other machines could still hard-delete
**Impact:** A config-only fix on one machine doesn't protect others; damage may be wider than observed.
**Mitigation:** The safety fix lands in **code** (tier-1 hard-delete off by default unless `MEMORY_DECAY_PRUNE_APPLY=true`), which propagates to every machine through the normal `/update` git-pull path irrespective of whether that machine's `config/reflections.yaml` is a symlink or a regular file. Machine-locality was resolved (regular file on this checkout, see Freshness Check / Open Question 3); the shared-vs-local caveat is documented so no machine silently keeps hard-delete on via a divergent yaml.

## Race Conditions

No race conditions identified — the prune reflection runs single-threaded on the daily scheduler cadence and reads/writes Memory records sequentially. Corpus-count baseline read-then-write is within one reflection invocation with no concurrent prune (the scheduler does not overlap runs of the same reflection).

## No-Gos (Out of Scope)

- [DESTRUCTIVE] Recovering/rehydrating the 1,990 deleted records from Redis persistence — AOF is disabled (#1814), so no reliable point-in-time source exists; best-effort salvage from `.npy` sidecars / distilled-ingest report is in scope, but a recovery guarantee is not.
- [SEPARATE-SLUG #2435] Fixing stop-hook timeouts / corpus refill — different root cause; explicitly kept separate per the issue.
- [SEPARATE-SLUG #2207] Root-causing the phantom AgentSession key collapse — only its *code-path overlap* with `Memory:*` deletes is in scope here (confirm/rule-out hypothesis 2), not fixing that incident.

## Update System

`config/reflections.yaml` is a **regular file on this checkout** (confirmed via `ls -la` — see Freshness Check), not the vault symlink the `reference_reflections_config` note describes; machine-locality varies. The durable safety guarantee therefore lives in **code**, not config: tier-1 hard-delete defaults **off** unless `MEMORY_DECAY_PRUNE_APPLY=true` is explicitly set, so no machine hard-deletes regardless of its yaml copy. The config reconciliation (description + params) is a consistency fix, not the safety mechanism. Because the fix ships in code (`memory_decay_prune.py` + the new baseline model + migration), the normal `/update` / `scripts/remote-update.sh` git-pull path propagates it to every machine; the idempotent migration in `scripts/update/migrations.py` runs on each machine's `/update`. No new dependencies. **Register `MEMORY_DECAY_PRUNE_APPLY` in `.env.example`** (default off) so operators can see the tier-1 opt-in switch; the new tunables (`MAX_PRUNE_FRACTION`, `MAX_PRUNE_ABSOLUTE`, `CORPUS_DROP_ALERT_FRACTION`, `CORPUS_MIN_HEALTHY_FLOOR`, `CORPUS_BASELINE_RING_SIZE`) are code constants with env overrides and need no `.env.example` entry unless an operator wants to pin one.

## Agent Integration

No new agent/tool surface required — this is a reflection-internal change. The human-alert path already exists as the **`gh issue create` channel** in `memory_quality_audit.py` (`_file_anomaly_issue` + `_find_recent_audit_issue` title-prefix dedup, Layer 2/3); the guardrail and anomaly detector reuse it. This is NOT the `human_alert_needed` watchdog signal (that lives only in `monitoring/bridge_watchdog.py` and is unreachable from the reflections subprocess). No new MCP tool, no `bridge/telegram_bridge.py` import changes. Verify the alert actually files a de-duplicated GitHub issue via the existing audit alert wiring in an integration test.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/subconscious-memory.md` — document the corpus-fraction guardrail, the anomaly alert, and the corrected apply/dry-run posture (remove the stale "dry-run default" claim or make it accurate).
- [ ] Update `CLAUDE.md` memory section to match the reconciled posture (it currently says "Dry-run default").
- [ ] Add/adjust entry in `docs/features/README.md` if a new monitor is introduced.

### Inline Documentation
- [ ] Docstring update in `memory_decay_prune.py` reflecting the guardrail and the present-field predicate.
- [ ] Grain-of-salt comments on the new provisional constants.

## Success Criteria

- [ ] **(Forward-looking defense-in-depth)** `memory_decay_prune` tier-1 aborts (deletes nothing) and files a GitHub alert issue when a run would delete more than `MAX_PRUNE_FRACTION` of the durable corpus or more than `MAX_PRUNE_ABSOLUTE` records. Note: given the shared `MAX_PRUNE_PER_RUN=50` cap, on a ~2000-record corpus the fraction ceiling (~100) is unreachable and only `MAX_PRUNE_ABSOLUTE=25` can trip today; the guardrail is sized to protect a future larger corpus or a raised per-run cap. The mechanism that catches a collapse the size of the motivating incident is the corpus-size anomaly detector below, not this guardrail. Unit test proves the abort + alert-file path fires when the threshold is crossed (with the cap raised in-test so the tier-1 set can exceed it).
- [ ] Records with `importance is None` or `access_count is None` are exempt from tier-1 selection (unit test proves it).
- [ ] A corpus-size drop beyond `CORPUS_DROP_ALERT_FRACTION` **vs. the max of the recent baseline ring** files a de-duplicated GitHub alert issue via the `memory-quality-audit` alert channel (unit/integration test proves it).
- [ ] The baseline is a **bounded ring of recent `(size, ts)` samples** (not a single scalar); comparison uses `max(recent ring)` so one already-collapsed sample cannot suppress a real alert (unit test proves it).
- [ ] **Deploy-into-collapse safety**: with an empty ring and `observed < CORPUS_MIN_HEALTHY_FLOOR`, the detector files the alert immediately rather than baseline-locking the collapse (unit test proves it; guards the current corpus==1 state).
- [ ] When `gh issue create` fails for the corpus-collapse signal, a `logger.error` is emitted so the alert is not silently swallowed at `warning` level (unit test proves it).
- [ ] `config/reflections.yaml`, the module docstring, `docs/features/subconscious-memory.md`, and `CLAUDE.md` state one consistent apply/dry-run posture for `memory-decay-prune` and `memory-dedup`.
- [ ] Written forensic conclusion recorded in the issue: whether tier-1 prune could account for the loss (given the 50/run cap and 2026-07-23 activation), the resolution of `Reflection.last_run == None`, and disposition of hypotheses 2/4/5. **Scoping is explicit**: the code guardrails are defense-in-depth and do NOT close the ~1690-record bulk-deletion mechanism, which remains an open forensic question tracked in this issue.
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
- Replace `or`-defaulting (`:207`/`:211`) with explicit `None`-exemption for `importance`/`access_count`.
- Add `MAX_PRUNE_FRACTION`, `MAX_PRUNE_ABSOLUTE` named env-overridable constants with provisional/tunable comments.
- Insert guardrail before the tier-1 delete loop (`:296`): compute durable total, abort + file GitHub alert issue (reuse `memory_quality_audit._file_anomaly_issue` pattern) + dry-run report when exceeded; leave tier-2 tombstoning independent.
- Decouple tier-1 apply resolution: tier-1 hard-delete defaults **off** unless `MEMORY_DECAY_PRUNE_APPLY=true`; do not inherit `params.apply` (Open Question 1).
- Register `MEMORY_DECAY_PRUNE_APPLY` in `.env.example` (default off / empty) with a comment line above it, per the repo's secret/env-completeness convention — the var is invisible to operators otherwise.

### 2. Corpus-size anomaly detection + alert
- **Task ID**: build-anomaly
- **Depends On**: none
- **Validates**: new tests for baseline init (no false alert), drop alert fires
- **Assigned To**: anomaly-monitor-builder
- **Agent Type**: builder
- **Domain**: redis-popoto
- **Parallel**: true
- Read `reflections/memory/memory_quality_audit.py` (esp. `_find_recent_audit_issue` / `_file_anomaly_issue`, `:549-678`) and extend it to add corpus-count baseline tracking — one monitor, reusing the existing `gh issue create` alert channel.
- Persist recent durable counts via a **dedicated Popoto model holding a bounded ring** (`models/memory_corpus_baseline.py`, single-row `CorpusSizeBaseline` with a JSON-serialized ring of the last `CORPUS_BASELINE_RING_SIZE` `(size, recorded_at)` samples, drop-oldest on append). NOT a single scalar; NOT the INCR-only gate counter. Add an idempotent migration to `scripts/update/migrations.py` and register it in `MIGRATIONS`.
- Compare `observed` against `max(size for the ring)` and file a de-duplicated GitHub alert issue (title-prefix dedup) on a drop beyond `CORPUS_DROP_ALERT_FRACTION`.
- **First-run/empty-ring floor**: when the ring is empty and `observed < CORPUS_MIN_HEALTHY_FLOOR`, file the alert immediately (do not baseline-lock a collapse present at deploy time — corpus is currently 1).
- **Alert fallback**: on `_file_anomaly_issue` returning `False` (gh failure) for the corpus-collapse signal, emit a `logger.error` so the signal is not swallowed at `warning` level. Do NOT reference `human_alert_needed`.
- Add `CORPUS_MIN_HEALTHY_FLOOR` and `CORPUS_BASELINE_RING_SIZE` as named, env-overridable constants with provisional/tunable comments.

### 3. Config + doc reconciliation
- **Task ID**: build-reconcile
- **Depends On**: none
- **Assigned To**: config-doc-builder
- **Agent Type**: builder
- **Parallel**: true
- Apply the decoupled posture (Open Question 1): update `config/reflections.yaml` description + params for `memory-decay-prune` (tier-1 opt-in, default off) and `memory-dedup`, so description and params agree.
- Machine-locality is resolved (regular file on this checkout, Open Question 3): confirm `ls -la config/reflections.yaml` on the build machine, and rely on the code-level default-off guarantee for cross-machine safety; document the shared-vs-local caveat.
- Align `memory_decay_prune.py` docstring, `docs/features/subconscious-memory.md`, and `CLAUDE.md` to the one reconciled posture.

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
| Anomaly alert wired | `grep -rc "CORPUS_DROP_ALERT_FRACTION" reflections/memory/` | output > 0 |
| Alert via gh-issue channel (not watchdog) | `grep -rn "human_alert_needed" reflections/memory/` | exit code 1 (no matches) |
| Baseline model uses SET not gate counter | `grep -rn "_increment_gate_counter" reflections/memory/memory_quality_audit.py` | exit code 1 (no matches) |
| First-run floor present | `grep -rc "CORPUS_MIN_HEALTHY_FLOOR" reflections/memory/ models/memory_corpus_baseline.py` | output > 0 |
| Baseline is a ring, not a scalar | `grep -rc "CORPUS_BASELINE_RING_SIZE" reflections/memory/ models/memory_corpus_baseline.py` | output > 0 |
| Tier-1 opt-in registered for operators | `grep -c "MEMORY_DECAY_PRUNE_APPLY" .env.example` | output > 0 |
| Docs posture consistent | `grep -rn "dry-run default\|dry_run default" config/reflections.yaml docs/features/subconscious-memory.md CLAUDE.md` | output does not contain apply |

## Critique Results

<!-- Populated by /do-plan-critique (war room) 2026-07-29. Verdict: NEEDS REVISION (2 blockers, 1 concern). FULL depth: Risk & Robustness, Scope & Value, History & Consistency. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness + History & Consistency (Scope & Value concurred at CONCERN) | The plan cites the human-alert path as `crash-tracker human_alert_needed (#2396)` and instructs builders to "mirror" it, but `human_alert_needed` lives in `monitoring/bridge_watchdog.py` (a launchd watchdog process), NOT `crash_tracker.py`. No memory reflection emits it; the established memory-reflection alert channel is `gh issue create` (`memory_quality_audit.py` Layer 2/3). Load-bearing for 2 of 4 Key Elements. Also repeats the "two monitors instead of one" anti-pattern the plan warns against. | | Grep-verify first: `grep -rn "human_alert_needed" monitoring/ reflections/`. If it only appears in `bridge_watchdog.py`/tests, route the corpus-collapse alert through the existing `gh issue create` dedup-by-title-prefix channel used for `memory_quality_audit.py` Layer 2/3, NOT an import of `bridge_watchdog.human_alert_needed` from the `python -m reflections` subprocess. Fix every "#2396 reuse" mention in the plan + Success Criteria. |
| BLOCKER | Risk & Robustness + History & Consistency (Scope & Value concurred at CONCERN) | Technical Approach offers persisting the anomaly baseline "via a small Popoto record **or** a gate counter (`models/memory_gate.py` already has `_increment_gate_counter`)". That counter is a raw-Redis INCR-only, monotonic counter with no SET/decrement API — it structurally cannot store a corpus size that must shrink after legitimate pruning or be re-baselined. A builder could wire the one load-bearing new capability onto unusable infrastructure. | | Drop the gate-counter alternative from the plan text; commit to a small dedicated Popoto model field (e.g. `last_corpus_size` + `recorded_at`) supporting arbitrary SET, with the idempotent migration in `scripts/update/migrations.py` (registered in `MIGRATIONS`) per the addendum's Popoto Schema Migration Requirement. `_increment_gate_counter(project_key, reason)` exposes only INCR/GET. |
| CONCERN | Risk & Robustness + Scope & Value + History & Consistency (all three) | The corpus-fraction guardrail is placed on the tier-1 hard-delete path, but `capped = to_prune[:MAX_PRUNE_PER_RUN]` (50) is applied to the combined tier1+tier2 union BEFORE the tier split (memory_decay_prune.py:254), so `len(tier1_pruned) <= 50` always. On a ~2000-record corpus `MAX_PRUNE_FRACTION=0.05` (=100) is mathematically unreachable — only `MAX_PRUNE_ABSOLUTE=25` can ever trip. The plan's own Forensic section concludes tier-1 "cannot alone account for the ~1990-record loss," so this guardrail formally cannot prevent the motivating failure. | | Either lower `MAX_PRUNE_ABSOLUTE` to be meaningfully protective relative to the 50/run cap, OR reframe Success Criterion 1 + the Solution text to state the fraction guardrail is forward-looking defense-in-depth (sized for a future larger corpus) and that the corpus-size **anomaly detector**, not this guardrail, is the mechanism that catches a collapse this large. The forensic task's conclusion (hypotheses 2/4/5), not the guardrail, determines whether hypothesis 1 is closed. |
| NIT | Critique driver (structural) | Plan line-number citations drifted from current source: null-coalescing is at `memory_decay_prune.py:207`/`:211` (plan says ~226-236), tier-1 delete at `:296` (plan says ~299), `_resolve_tier_apply` at `:145` (plan says 129-142). Substance confirmed; only the anchors are stale. | | Re-anchor the file:line references during the revision pass; the code facts they point to are all verified correct. |

### Revision Pass (2026-07-29) — critique findings resolved

- **BLOCKER 1 (wrong alert path)** — RESOLVED. Every "#2396 / crash-tracker `human_alert_needed`" reference is replaced with the established `gh issue create` channel in `memory_quality_audit.py` (`_file_anomaly_issue` + `_find_recent_audit_issue`, `:549-678`). Verified `human_alert_needed` lives only in `monitoring/bridge_watchdog.py`. Fixed across Solution, Technical Approach, Flow, Agent Integration, Failure Path Test Strategy, Success Criteria, Tasks 1–2, and the Verification table (added a `grep` guard asserting no `human_alert_needed` in `reflections/memory/`).
- **BLOCKER 2 (monotonic counter can't hold a shrinking baseline)** — RESOLVED. Gate-counter alternative dropped everywhere; committed to a dedicated Popoto model (`models/memory_corpus_baseline.py`, `last_corpus_size` + `recorded_at`, arbitrary SET) with an idempotent migration registered in `scripts/update/migrations.py::MIGRATIONS`. Verification table asserts no `_increment_gate_counter` use.
- **CONCERN / Tech-Debt (guardrail unreachable under the 50/run cap)** — ADDRESSED. Success Criterion 1 and the Solution text are reframed as forward-looking defense-in-depth; the corpus-size anomaly detector (not the fraction guardrail) is named as the mechanism that catches a collapse the size of the incident. Kept `MAX_PRUNE_ABSOLUTE=25` (conservative) rather than lowering it, and stated the math explicitly.
- **NIT (stale line numbers)** — RESOLVED. Re-anchored to current source: `_resolve_tier_apply` `:145`, null-coalescing `:207`/`:211`, tier-1 delete `:296`.

### Revision Pass 2 (2026-07-29) — second critique findings resolved

- **BLOCKER (baseline model contradicts its own mitigation; baseline-locks at collapse)** — RESOLVED. The single-scalar `CorpusSizeBaseline (last_corpus_size + recorded_at)` is replaced by a **bounded ring** of the last `CORPUS_BASELINE_RING_SIZE` `(size, ts)` samples, so "compare against the max of recent baselines" is now expressible and real (`baseline = max(size for size,_ in ring)`). Added a **first-run absolute floor** (`CORPUS_MIN_HEALTHY_FLOOR`, env-overridable): when the ring is empty and `observed < floor`, the detector files the alert immediately instead of baseline-locking the standing collapse — the specific guard for deploying into the current corpus==1 state. Threaded through Solution, Technical Approach, Flow, Risk 2, Failure Path tests, Success Criteria, Task 2, and the Verification table.
- **CONCERN (a): sole gh-alert channel swallows auth failures** — ADDRESSED. `_file_anomaly_issue` already logs `warning` and returns `False` on `gh` failure; the corpus-collapse signal additionally emits a `logger.error` on that `False`-return so a real collapse isn't lost at `warning` level. Test added.
- **CONCERN (b): unproven `importance=None` root-cause claim** — ADDRESSED. Reworded in Problem and the predicate-hardening element as a **latent hazard being hardened**, not a proven cause; the 50/run cap + 2026-07-23 activation make tier-1 far too small to explain the ~1990-record loss.
- **CONCERN (c): fraction guardrail duplicates the anomaly detector** — ADDRESSED. Reconciled explicitly as two non-redundant guardrails: (1) per-run **preventive** fraction abort on the write side, (2) cross-run **detective** anomaly alert on the read side. Neither is a copy of the other.
- **CONCERN (d): guardrails harden an unproven cause while the bulk mechanism stays open** — ADDRESSED. Problem, Success Criteria, and the forensic task now state plainly that the ~1690-record bulk mechanism is unproven and open, the forensic task remains, and the code guardrails are defense-in-depth, not a claimed fix.
- **CONCERN (e): `MEMORY_DECAY_PRUNE_APPLY` unregistered** — ADDRESSED. Added a Task 1 bullet + Update System note to register it in `.env.example` (default off) with a comment line, and a Verification-table grep.

### Open Questions — RESOLVED (supervisor decisions, 2026-07-29)

1. **Tier-1 apply posture** → **Decouple.** Tier-2 tombstoning stays in apply mode (reversible); tier-1 hard-delete requires explicit per-machine opt-in `MEMORY_DECAY_PRUNE_APPLY=true` (default off), never inheriting `params.apply`.
2. **Guardrail thresholds** → **Conservative defaults:** `MAX_PRUNE_FRACTION=0.05`, `MAX_PRUNE_ABSOLUTE=25`, `CORPUS_DROP_ALERT_FRACTION=0.10` — all named, env-overridable constants with a "provisional/tunable" grain-of-salt comment. Corpus-fraction abort + `gh issue create` human alert.
3. **Machine scope** → On this checkout `config/reflections.yaml` is a **regular file** (not the vault symlink). Docs + yaml description + params reconciled to agree; the durable safety guarantee is the **code-level tier-1 default-off**, which propagates via `/update` git-pull to every machine regardless of its yaml being shared or local. Shared-vs-local caveat documented in Update System, Risk 3, and Freshness Check.

### Re-Critique (2026-07-29) — FULL war room, verdict READY TO BUILD (with concerns)

Re-ran the full war room (Risk & Robustness, Scope & Value, History & Consistency) against current source. **Zero blockers; 4 CONCERNs.** Verdict: **READY TO BUILD (with concerns)**. The `plan_revising` lock was intentionally NOT set because `revision_applied: true` is already in the frontmatter (the plan was revised twice) — these residual concerns are advisory for the builder, not a forced third revision loop. Line anchors and the two prior BLOCKER fixes (gh-issue channel, ring model) all re-verified correct against current source.

| Severity | Critic | Finding | Implementation Note |
|----------|--------|---------|---------------------|
| CONCERN | Risk & Robustness (Adversary) | Race Conditions section declares "No race conditions identified," justified only by single-machine non-overlap, but the new single-row `CorpusSizeBaseline` ring uses read-modify-write (read ring → append → save). If `memory-quality-audit` ever runs on >1 machine against the shared durable corpus, the second `save()` silently clobbers the first's append (lost high-water sample). | Reflection scheduler is worker-role-gated (one machine), so this is latent, not live — but the race-free justification is incomplete. Either state the single-worker-machine invariant explicitly as the reason, or use an atomic Redis structure (`RPUSH`/`LTRIM`) instead of read-then-SET a JSON blob. Contrast `models/memory_gate.py:25-31`, which documents its own INCR atomicity for exactly this reason. |
| CONCERN | Scope & Value (Simplifier) | `MAX_PRUNE_FRACTION=0.05` is structurally dead in production: `capped = to_prune[:MAX_PRUNE_PER_RUN]` (50) caps the union before the tier split (`memory_decay_prune.py:80,254`), so `len(tier1_pruned) <= 50` always and ~100 (5% of ~2000) is unreachable. Plan concedes this yet still ships the constant, its abort branch, and an artificial-cap unit test. (Re-litigates a Revision-Pass-1 supervisor decision to keep it as forward-looking defense-in-depth.) | Optional simplification: ship only `MAX_PRUNE_ABSOLUTE=25` (genuinely reachable) and defer the fraction ceiling to a follow-up. If keeping it (supervisor's prior call), the "forward-looking" framing already in the plan is the mitigation — no code change required. |
| CONCERN | History & Consistency | Tier-1 apply-resolution framing is misleading: plan says tier-1 needs "its own resolution," but both tiers already resolve via distinct env vars (`MEMORY_DECAY_PRUNE_APPLY`/`MEMORY_NOISE_PRUNE_APPLY`, `memory_decay_prune.py:180-181`). Only the `params.get("apply", False)` fallback is shared. | Narrow the fix wording: tier-1 already resolves via `MEMORY_DECAY_PRUNE_APPLY`; the change is to **drop the `params`-fallback branch for tier-1 specifically** (`:157-159`), e.g. an `allow_params_fallback: bool` param or a dedicated `_resolve_tier1_apply` — NOT a wholesale "add its own resolution." |
| CONCERN | History & Consistency | Cross-module reuse of `_file_anomaly_issue` is unresolved: Task 1 says "reuse the pattern" (reimplement) while Technical Approach says "through the same path" (import). `_file_anomaly_issue` is module-private (underscore) in `memory_quality_audit.py`. Silence invites a builder to duplicate ~130 lines, forking the "one channel" the plan claims to consolidate. | Pick one: (a) extract a public helper (e.g. `reflections/memory/alert_channel.py` exporting `file_anomaly_issue`/`find_recent_audit_issue`) imported by both reflections — straightforward since the args are already generic `(signal_name, observed, threshold, sample_ids, evidence)`; or (b) state plainly that `memory_decay_prune.py` imports the private symbols directly and accept the coupling. |
