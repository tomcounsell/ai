# Memory Distilled-Ingest Report

Snapshot taken: 2026-07-23T04:21:41.809062+00:00
Git SHA: 58b728c83b7b301dd6544d163d85a93be80f2c04
Project key: valor
Distill model: `claude-haiku-4-5-20251001`
Distill prompt version: `v1`
Min evidence: 2

## Methodology note

This is a MERGE-TIME IMPORTANCE-DISTRIBUTION SNAPSHOT (plan Success Criterion 5a), not an act-rate lift claim. Act-rate needs post-deploy outcome accrual (>=2 acted/dismissed events per record) over an N-day window; that comparison is a separately-tracked follow-up (5b), not a merge gate -- see Risk 3 / Open Question 2 in docs/plans/memory-distilled-ingest.md. This snapshot is also taken before the distillation reflection has processed any live traffic (the feature has not yet been deployed at snapshot time), so the corpus may show little or no distilled/provisional status spread yet. The artifact establishes the measurement METHODOLOGY and a same-shape starting point for the later comparison, not a lift claim.

## Aggregate (pooled, all sources)

- Record count: 1991
- Superseded count: 14
- Durable denominator: 1977
- Aggregate act rate: 0.990
- Junk rate: 0.030
- Junk count: 59 (ack-only: 0, fragment: 59)
- Importance histogram:
  - `0.0-0.2`: 0
  - `0.2-0.4`: 0
  - `0.4-0.6`: 0
  - `0.6-0.8`: 0
  - `0.8-1.0`: 1407
  - `<0.0`: 0
  - `>1.0`: 584
- Source counts (within this block):
  - `agent`: 1963
  - `human`: 28

## Segmented by source

### Source: `agent`

- Record count: 1963
- Superseded count: 14
- Durable denominator: 1949
- Aggregate act rate: 0.990
- Junk rate: 0.030
- Junk count: 59 (ack-only: 0, fragment: 59)
- Importance histogram:
  - `0.0-0.2`: 0
  - `0.2-0.4`: 0
  - `0.4-0.6`: 0
  - `0.6-0.8`: 0
  - `0.8-1.0`: 1384
  - `<0.0`: 0
  - `>1.0`: 579
- Source counts (within this block):
  - `agent`: 1963

### Source: `human`

- Record count: 28
- Superseded count: 0
- Durable denominator: 28
- Aggregate act rate: 1.000
- Junk rate: 0.000
- Junk count: 0 (ack-only: 0, fragment: 0)
- Importance histogram:
  - `0.0-0.2`: 0
  - `0.2-0.4`: 0
  - `0.4-0.6`: 0
  - `0.6-0.8`: 0
  - `0.8-1.0`: 23
  - `<0.0`: 0
  - `>1.0`: 5
- Source counts (within this block):
  - `human`: 28

## Distillation coverage

- Provisional (awaiting distillation): 0
- Distilled (settled): 0
- Abandoned (terminal, attempt-cap or write-filter drop): 0

As of merge time, before the backfill reflection has processed the live corpus, distilled/provisional counts above may legitimately read 0 (or small) -- the reflection runs at a 300s cadence in the standing `com.valor.reflection-worker` subprocess and only starts distilling provisional records written by `ingest()` after this branch is deployed and live traffic arrives. Legacy pre-Phase-3 records carry no `distill_status` at all and are counted in none of the three buckets above.

## Comparison to Phase 1 baseline

Baseline snapshot: 2026-07-22T08:26:23.822196+00:00 (git b55cc3b8d70ea852c87302724e1d67b3c22c456a)

| Metric | Baseline | Current | Delta |
|--------|----------|---------|-------|
| Record count | 1991 | 1991 | +0 |
| Junk rate | 0.030 | 0.030 | 0.000 |
| Aggregate act rate | 0.990 | 0.990 | 0.000 |

### Importance histogram: baseline vs. current

| Bucket | Baseline | Current | Delta |
|--------|----------|---------|-------|
| `0.0-0.2` | 0 | 0 | +0 |
| `0.2-0.4` | 0 | 0 | +0 |
| `0.4-0.6` | 0 | 0 | +0 |
| `0.6-0.8` | 0 | 0 | +0 |
| `0.8-1.0` | 1407 | 1407 | +0 |
| `<0.0` | 0 | 0 | +0 |
| `>1.0` | 584 | 584 | +0 |

## Act-rate definition

Micro-average of per-record acted/dismissed outcome counts computed directly from metadata.outcome_history: sum(acted_i) / sum(evidence_i) across records where evidence_i = acted_i + dismissed_i >= min_evidence. "used" outcomes are excluded from both numerator and denominator. Records below the min_evidence floor are excluded from the aggregate and counted in excluded_thin_evidence_count. This is a pooled-count micro-average, not an average of per-record ratios, and it deliberately diverges from compute_act_rate (acted count divided by the total number of outcome_history entries, which includes 'used').
