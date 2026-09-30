# Memory Telemetry Baseline

Snapshot taken: 2026-07-22T08:26:23.822196+00:00
Git SHA: b55cc3b8d70ea852c87302724e1d67b3c22c456a
Project key: valor

## Key numbers

- Record count: 1991
- Superseded count: 14
- Durable denominator: 1977
- Aggregate act rate: 0.990
- Junk rate: 0.030
- Junk count: 59 (ack-only: 0, fragment: 59)
- Decay-imminent count: 0
- Never-injected count: 138

## Ingest volume by source

- `agent`: 1963
- `human`: 28

## Act-rate definition

Micro-average of per-record acted/dismissed outcome counts computed directly from metadata.outcome_history: sum(acted_i) / sum(evidence_i) across records where evidence_i = acted_i + dismissed_i >= min_evidence. "used" outcomes are excluded from both numerator and denominator. Records below the min_evidence floor are excluded from the aggregate and counted in excluded_thin_evidence_count. This is a pooled-count micro-average, not an average of per-record ratios, and it deliberately diverges from compute_act_rate (acted count divided by the total number of outcome_history entries, which includes 'used').
