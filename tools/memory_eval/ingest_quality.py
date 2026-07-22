"""Corpus-level ingest-quality metrics for the subconscious memory system.

Pure functions over a list of *decorated* Memory record dicts (the shape
produced by `ui/data/memories.py::_decorate_record`) -- no Redis, no popoto,
no network. This module folds per-record outcome telemetry and heuristic
junk classification (`agent.memory_quality.classify_content`) into a single
corpus-metrics dict, matching the existing `tools/memory_eval/metrics.py`
style (pure aggregation, synthetic-fixture unit tests).

Aggregate act-rate -- pinned formula (do not deviate; see
docs/plans/memory-telemetry-baseline.md, Technical Approach):

    For each record, compute directly from `outcome_history`:
        acted_i     = count(outcome == "acted")
        dismissed_i = count(outcome == "dismissed")
        evidence_i  = acted_i + dismissed_i
    "used" outcomes are excluded entirely -- neither numerator nor
    denominator. A record only contributes to the aggregate when
    `evidence_i >= min_evidence` (default 2); records below the floor are
    excluded and counted separately. The corpus aggregate is the
    MICRO-average across qualifying records: `sum(acted_i) / sum(evidence_i)`
    (pooled counts), NOT a macro-average of per-record ratios.

    This deliberately diverges from `agent.memory_extraction.compute_act_rate`
    / `_decorate_record["act_rate"]`, which divides the acted count by the
    total number of entries in `outcome_history` -- a different denominator
    that includes "used". This module always recomputes counts directly from
    `outcome_history` and never consumes the pre-computed `act_rate` field
    for that reason.
"""

from __future__ import annotations

from agent.memory_quality import classify_content

# Fixed histogram buckets for importance/confidence/act-rate distributions,
# so the emitted JSON is stable and diffable across snapshots. Confidence and
# per-record act rate are naturally bounded to [0, 1]; importance is not
# (human-authored memories can score well above 1.0), so out-of-range values
# fall into explicit overflow buckets rather than being silently dropped.
_RATE_BUCKETS: tuple[tuple[float, float], ...] = (
    (0.0, 0.2),
    (0.2, 0.4),
    (0.4, 0.6),
    (0.6, 0.8),
    (0.8, 1.0),
)
_UNDER_RANGE_LABEL = "<0.0"
_OVER_RANGE_LABEL = ">1.0"

ACT_RATE_DEFINITION = (
    "Micro-average of per-record acted/dismissed outcome counts computed "
    "directly from metadata.outcome_history: sum(acted_i) / sum(evidence_i) "
    "across records where evidence_i = acted_i + dismissed_i >= min_evidence. "
    '"used" outcomes are excluded from both numerator and denominator. '
    "Records below the min_evidence floor are excluded from the aggregate "
    "and counted in excluded_thin_evidence_count. This is a pooled-count "
    "micro-average, not an average of per-record ratios, and it deliberately "
    "diverges from compute_act_rate (acted count divided by the total number "
    "of outcome_history entries, which includes 'used')."
)


def _empty_histogram() -> dict[str, int]:
    """A zero-filled histogram dict with every fixed bucket label present."""
    hist = {f"{lo:.1f}-{hi:.1f}": 0 for lo, hi in _RATE_BUCKETS}
    hist[_UNDER_RANGE_LABEL] = 0
    hist[_OVER_RANGE_LABEL] = 0
    return hist


def _bucket_label(value: float) -> str:
    """Map a numeric value to its fixed histogram bucket label."""
    if value < _RATE_BUCKETS[0][0]:
        return _UNDER_RANGE_LABEL
    for lo, hi in _RATE_BUCKETS[:-1]:
        if lo <= value < hi:
            return f"{lo:.1f}-{hi:.1f}"
    lo, hi = _RATE_BUCKETS[-1]
    if lo <= value <= hi:
        return f"{lo:.1f}-{hi:.1f}"
    return _OVER_RANGE_LABEL


def _add_to_histogram(hist: dict[str, int], value: float) -> None:
    hist[_bucket_label(value)] += 1


def _coerce_float(value: object, default: float = 0.0) -> float:
    """Best-effort float coercion for a decorated-record field."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _record_outcome_counts(record: dict) -> tuple[int, int, int]:
    """Return (acted_i, dismissed_i, evidence_i) computed directly from
    ``outcome_history``, per the pinned formula ("used" excluded entirely).

    Defensive against malformed records: a missing/non-list
    ``outcome_history`` (whether at the top level, as `_decorate_record`
    produces, or nested under a non-dict/missing ``metadata``) yields
    ``(0, 0, 0)`` rather than raising.
    """
    outcome_history = record.get("outcome_history") if isinstance(record, dict) else None
    if not isinstance(outcome_history, list):
        metadata = record.get("metadata") if isinstance(record, dict) else None
        outcome_history = metadata.get("outcome_history") if isinstance(metadata, dict) else None
    if not isinstance(outcome_history, list):
        return 0, 0, 0

    acted = 0
    dismissed = 0
    for entry in outcome_history:
        if not isinstance(entry, dict):
            continue
        outcome = entry.get("outcome")
        if outcome == "acted":
            acted += 1
        elif outcome == "dismissed":
            dismissed += 1
    return acted, dismissed, acted + dismissed


def _record_content(record: dict) -> str:
    content = record.get("content", "") if isinstance(record, dict) else ""
    return content if isinstance(content, str) else ""


def compute_corpus_metrics(records: list[dict], min_evidence: int = 2) -> dict:
    """Aggregate a corpus-metrics dict from decorated Memory record dicts.

    Args:
        records: decorated record dicts, as produced by
            `ui/data/memories.py::_decorate_record`. Malformed entries
            (non-dict, missing fields, non-dict metadata) are handled
            defensively and contribute zero evidence / default values
            rather than raising.
        min_evidence: minimum `acted_i + dismissed_i` for a record to
            contribute to the aggregate act rate / dismissal rate / act-rate
            distribution. Default 2 (excludes single-sample records, which
            would otherwise contribute a spurious 0.0 or 1.0).

    Returns:
        A metrics dict with every key present, even for an empty
        ``records`` list (zero-filled, no exceptions). Rate fields are
        ``None`` when their denominator is zero (undefined), never a
        ``ZeroDivisionError``.
    """
    total_records = len(records)

    superseded_count = 0
    junk_count = 0
    ack_only_count = 0
    fragment_suspect_count = 0
    durable_denominator = 0

    source_counts: dict[str, int] = {}
    importance_histogram = _empty_histogram()
    confidence_histogram = _empty_histogram()
    act_rate_distribution = _empty_histogram()

    decay_imminent_count = 0
    never_injected_count = 0

    acted_total = 0
    dismissed_total = 0
    evidence_total = 0
    qualifying_record_count = 0
    excluded_thin_evidence_count = 0

    for record in records:
        if not isinstance(record, dict):
            record = {}

        is_superseded = bool(record.get("superseded"))
        if is_superseded:
            superseded_count += 1
        else:
            durable_denominator += 1
            classification = classify_content(_record_content(record))
            if classification == "ack_only":
                ack_only_count += 1
                junk_count += 1
            elif classification == "fragment":
                fragment_suspect_count += 1
                junk_count += 1

        source = record.get("source") or "unknown"
        if not isinstance(source, str):
            source = "unknown"
        source_counts[source] = source_counts.get(source, 0) + 1

        _add_to_histogram(importance_histogram, _coerce_float(record.get("importance"), 0.0))
        _add_to_histogram(confidence_histogram, _coerce_float(record.get("confidence"), 0.0))

        if record.get("decay_imminent"):
            decay_imminent_count += 1
        access_count = record.get("access_count", 0)
        if not isinstance(access_count, int) or access_count == 0:
            never_injected_count += 1

        acted_i, dismissed_i, evidence_i = _record_outcome_counts(record)
        if evidence_i >= min_evidence:
            qualifying_record_count += 1
            acted_total += acted_i
            dismissed_total += dismissed_i
            evidence_total += evidence_i
            _add_to_histogram(act_rate_distribution, acted_i / evidence_i)
        else:
            excluded_thin_evidence_count += 1

    aggregate_act_rate = acted_total / evidence_total if evidence_total > 0 else None
    aggregate_dismissal_rate = dismissed_total / evidence_total if evidence_total > 0 else None
    junk_rate = junk_count / durable_denominator if durable_denominator > 0 else None

    return {
        "total_records": total_records,
        "superseded_count": superseded_count,
        "durable_denominator": durable_denominator,
        "min_evidence": min_evidence,
        "act_rate_definition": ACT_RATE_DEFINITION,
        "aggregate_act_rate": aggregate_act_rate,
        "aggregate_dismissal_rate": aggregate_dismissal_rate,
        "acted_total": acted_total,
        "dismissed_total": dismissed_total,
        "evidence_total": evidence_total,
        "qualifying_record_count": qualifying_record_count,
        "excluded_thin_evidence_count": excluded_thin_evidence_count,
        "act_rate_distribution": act_rate_distribution,
        "junk_count": junk_count,
        "junk_rate": junk_rate,
        "ack_only_count": ack_only_count,
        "fragment_suspect_count": fragment_suspect_count,
        "source_counts": source_counts,
        "importance_histogram": importance_histogram,
        "confidence_histogram": confidence_histogram,
        "decay_imminent_count": decay_imminent_count,
        "never_injected_count": never_injected_count,
    }
