"""Non-interactive CLI: the distilled-ingest lift report (issue #2202, Task 3).

Entry point: ``python -m tools.memory_eval.distilled_ingest_report [--force]``

Writes two artifacts under ``docs/baselines/``:

- ``memory-distilled-ingest-report.json``: per-source-segmented
  ``compute_corpus_metrics`` output (aggregate + one block per normalized
  ``source`` value) plus a comparison against the committed Phase 1 baseline
  (``memory-telemetry-baseline.json``) and a pinned-model/prompt +
  provenance header.
- ``memory-distilled-ingest-report.md``: a human-readable rendering of the
  same data, following the style of ``tools/memory_eval/snapshot.py``'s
  ``render_markdown`` but extended with the source-segmented breakdown and
  the baseline-comparison section.

Per-source segmentation reuses ``tools.memory_eval.ingest_quality
.compute_corpus_metrics`` UNCHANGED -- that aggregator takes no ``source``
parameter and is not modified by this module. Segmentation is achieved by
filtering the decorated-record list by ``source`` BEFORE calling
``compute_corpus_metrics`` once per subset (see
``segment_records_by_source`` / ``compute_segmented_metrics`` below), plus
once more, unfiltered, for the pooled aggregate.

**This is a merge-time importance-DISTRIBUTION snapshot (plan Success
Criterion 5a), not an act-rate LIFT claim.** Act-rate needs post-deploy
outcome accrual (>=2 acted/dismissed events per record) over an N-day
window and is an explicitly separate, deferred follow-up (5b) -- see
``docs/plans/memory-distilled-ingest.md``, Risk 3 and Open Question 2. This
snapshot is also taken BEFORE the distillation reflection has processed any
live traffic (the feature has not yet merged/deployed at snapshot time), so
the corpus may show little or no ``distilled``/``provisional`` status spread
yet. The artifact's job is to establish the measurement METHODOLOGY and a
same-shape starting point for the later comparison, not to claim lift
prematurely -- ``render_markdown`` states this explicitly in the rendered
report.

Read-only with respect to Memory records: this module performs no writes,
deletes, or status transitions on any Memory record (mirrors
``tools/memory_eval/snapshot.py``'s read-only guarantee).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from tools.memory_eval.ingest_quality import compute_corpus_metrics
from tools.memory_eval.snapshot import _git_sha

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_DIR = REPO_ROOT / "docs" / "baselines"
PHASE1_BASELINE_JSON_PATH = BASELINE_DIR / "memory-telemetry-baseline.json"
REPORT_JSON_PATH = BASELINE_DIR / "memory-distilled-ingest-report.json"
REPORT_MD_PATH = BASELINE_DIR / "memory-distilled-ingest-report.md"

MEASUREMENT_NOTE = (
    "This is a MERGE-TIME IMPORTANCE-DISTRIBUTION SNAPSHOT (plan Success "
    "Criterion 5a), not an act-rate lift claim. Act-rate needs post-deploy "
    "outcome accrual (>=2 acted/dismissed events per record) over an N-day "
    "window; that comparison is a separately-tracked follow-up (5b), not a "
    "merge gate -- see Risk 3 / Open Question 2 in "
    "docs/plans/memory-distilled-ingest.md. This snapshot is also taken "
    "before the distillation reflection has processed any live traffic (the "
    "feature has not yet been deployed at snapshot time), so the corpus may "
    "show little or no distilled/provisional status spread yet. The "
    "artifact establishes the measurement METHODOLOGY and a same-shape "
    "starting point for the later comparison, not a lift claim."
)


def _normalize_source(record: dict) -> str:
    """Mirror `compute_corpus_metrics`'s own source normalization exactly,
    so segment membership matches the pooled aggregate's `source_counts`."""
    source = record.get("source") if isinstance(record, dict) else None
    if not isinstance(source, str) or not source:
        return "unknown"
    return source


def segment_records_by_source(records: list[dict]) -> dict[str, list[dict]]:
    """Group decorated records by normalized `source` (human/agent/unknown/...).

    Pure function, no Redis. Grouping only -- callers run
    `compute_corpus_metrics` on each subset (and once, unfiltered, for the
    pooled aggregate); this function does no aggregation itself.
    """
    segments: dict[str, list[dict]] = {}
    for record in records:
        source = _normalize_source(record if isinstance(record, dict) else {})
        segments.setdefault(source, []).append(record)
    return segments


def compute_segmented_metrics(records: list[dict], min_evidence: int = 2) -> dict:
    """Pooled aggregate + per-source `compute_corpus_metrics` blocks.

    Returns:
        {"aggregate": <pooled metrics dict>,
         "by_source": {source: <metrics dict>, ...}}
    """
    aggregate = compute_corpus_metrics(records, min_evidence=min_evidence)
    segments = segment_records_by_source(records)
    by_source = {
        source: compute_corpus_metrics(subset, min_evidence=min_evidence)
        for source, subset in sorted(segments.items())
    }
    return {"aggregate": aggregate, "by_source": by_source}


def load_phase1_baseline(path: Path = PHASE1_BASELINE_JSON_PATH) -> dict | None:
    """Best-effort load of the committed Phase 1 baseline artifact.

    Returns None (never raises) if the file is missing or malformed --
    the report is still generated, with `baseline_comparison.baseline_present
    == False`, rather than failing the whole run over a comparison feature.
    """
    try:
        return json.loads(path.read_text())
    except Exception as e:
        logger.warning("[distilled_ingest_report] Failed to load Phase 1 baseline %s: %s", path, e)
        return None


def _rate_delta(current: float | None, baseline: float | None) -> float | None:
    if current is None or baseline is None:
        return None
    return current - baseline


def compare_to_baseline(current_aggregate: dict, baseline: dict | None) -> dict:
    """Side-by-side + delta comparison of headline numbers and the
    importance histogram, current pooled aggregate vs. the Phase 1 baseline.
    """
    if baseline is None:
        return {"baseline_present": False}

    baseline_hist = baseline.get("importance_histogram") or {}
    current_hist = current_aggregate.get("importance_histogram") or {}
    hist_buckets = sorted(set(baseline_hist) | set(current_hist))
    importance_histogram_comparison = {
        bucket: {
            "baseline": baseline_hist.get(bucket, 0),
            "current": current_hist.get(bucket, 0),
            "delta": current_hist.get(bucket, 0) - baseline_hist.get(bucket, 0),
        }
        for bucket in hist_buckets
    }

    return {
        "baseline_present": True,
        "baseline_snapshot_timestamp": baseline.get("snapshot_timestamp"),
        "baseline_git_sha": baseline.get("git_sha"),
        "record_count": {
            "baseline": baseline.get("total_records", 0),
            "current": current_aggregate.get("total_records", 0),
            "delta": current_aggregate.get("total_records", 0) - baseline.get("total_records", 0),
        },
        "junk_rate": {
            "baseline": baseline.get("junk_rate"),
            "current": current_aggregate.get("junk_rate"),
            "delta": _rate_delta(current_aggregate.get("junk_rate"), baseline.get("junk_rate")),
        },
        "aggregate_act_rate": {
            "baseline": baseline.get("aggregate_act_rate"),
            "current": current_aggregate.get("aggregate_act_rate"),
            "delta": _rate_delta(
                current_aggregate.get("aggregate_act_rate"), baseline.get("aggregate_act_rate")
            ),
        },
        "source_counts": {
            "baseline": baseline.get("source_counts") or {},
            "current": current_aggregate.get("source_counts") or {},
        },
        "importance_histogram": importance_histogram_comparison,
    }


def build_report(project_key: str | None = None, min_evidence: int = 2) -> dict:
    """Compute the full segmented report dict.

    Raises on failure -- the caller is responsible for not writing partial
    output when this raises (mirrors `snapshot.py::build_baseline`).
    """
    from agent.memory_extraction import DISTILL_MODEL, DISTILL_PROMPT_VERSION
    from ui.data.memories import get_corpus_metrics, get_corpus_records

    records, pks = get_corpus_records(project_key=project_key)
    segmented = compute_segmented_metrics(records, min_evidence=min_evidence)

    # Distillation-status LIVE gauges (provisional/distilled/abandoned counts)
    # are not part of the decorated-record shape `get_corpus_records` returns
    # (distill_status rides raw Memory.metadata), so pull them from
    # `get_corpus_metrics`'s own separate corpus scan rather than threading a
    # second return shape through `get_corpus_records`.
    gauge_metrics = get_corpus_metrics(project_key=project_key, min_evidence=min_evidence)
    distillation_coverage = {
        "provisional_count": gauge_metrics.get("provisional_count", 0),
        "distilled_count": gauge_metrics.get("distilled_count", 0),
        "abandoned_count": gauge_metrics.get("abandoned_count", 0),
    }

    # Pass the module-level path explicitly (rather than relying on
    # `load_phase1_baseline`'s default parameter) so a caller/test that
    # monkeypatches `PHASE1_BASELINE_JSON_PATH` on this module is honored --
    # a default arg is bound once at function-definition time and would
    # otherwise silently ignore the monkeypatch.
    baseline = load_phase1_baseline(PHASE1_BASELINE_JSON_PATH)
    comparison = compare_to_baseline(segmented["aggregate"], baseline)

    return {
        "header": {
            "distill_model": DISTILL_MODEL,
            "distill_prompt_version": DISTILL_PROMPT_VERSION,
            "git_sha": _git_sha(),
            "snapshot_timestamp": datetime.now(UTC).isoformat(),
            "project_key": ", ".join(pks),
            "min_evidence": min_evidence,
            "measurement_note": MEASUREMENT_NOTE,
        },
        "aggregate": segmented["aggregate"],
        "by_source": segmented["by_source"],
        "distillation_coverage": distillation_coverage,
        "baseline_comparison": comparison,
    }


def _rate_str(value) -> str:
    return f"{value:.3f}" if isinstance(value, int | float) else "undefined"


def _render_metrics_block(title: str, metrics: dict) -> list[str]:
    source_counts = metrics.get("source_counts") or {}
    source_lines = (
        "\n".join(f"  - `{s}`: {c}" for s, c in sorted(source_counts.items())) or "  - (none)"
    )
    return [
        f"### {title}",
        "",
        f"- Record count: {metrics.get('total_records', 0)}",
        f"- Superseded count: {metrics.get('superseded_count', 0)}",
        f"- Durable denominator: {metrics.get('durable_denominator', 0)}",
        f"- Aggregate act rate: {_rate_str(metrics.get('aggregate_act_rate'))}",
        f"- Junk rate: {_rate_str(metrics.get('junk_rate'))}",
        f"- Junk count: {metrics.get('junk_count', 0)}"
        f" (ack-only: {metrics.get('ack_only_count', 0)},"
        f" fragment: {metrics.get('fragment_suspect_count', 0)})",
        "- Importance histogram:",
        "\n".join(
            f"  - `{bucket}`: {count}"
            for bucket, count in (metrics.get("importance_histogram") or {}).items()
        )
        or "  - (none)",
        "- Source counts (within this block):",
        source_lines,
        "",
    ]


def render_markdown(report: dict) -> str:
    """Human-readable rendering: header, aggregate, per-source breakdown,
    distillation coverage, and the Phase 1 baseline comparison."""
    header = report.get("header") or {}
    aggregate = report.get("aggregate") or {}
    by_source = report.get("by_source") or {}
    coverage = report.get("distillation_coverage") or {}
    comparison = report.get("baseline_comparison") or {}

    lines = [
        "# Memory Distilled-Ingest Report",
        "",
        f"Snapshot taken: {header.get('snapshot_timestamp', 'unknown')}",
        f"Git SHA: {header.get('git_sha', 'unknown')}",
        f"Project key: {header.get('project_key', 'unknown')}",
        f"Distill model: `{header.get('distill_model', 'unknown')}`",
        f"Distill prompt version: `{header.get('distill_prompt_version', 'unknown')}`",
        f"Min evidence: {header.get('min_evidence', 2)}",
        "",
        "## Methodology note",
        "",
        header.get("measurement_note", MEASUREMENT_NOTE),
        "",
        "## Aggregate (pooled, all sources)",
        "",
        *_render_metrics_block("Aggregate", aggregate)[2:],  # skip duplicate title
        "## Segmented by source",
        "",
    ]

    for source, metrics in sorted(by_source.items()):
        lines.extend(_render_metrics_block(f"Source: `{source}`", metrics))

    lines.extend(
        [
            "## Distillation coverage",
            "",
            f"- Provisional (awaiting distillation): {coverage.get('provisional_count', 0)}",
            f"- Distilled (settled): {coverage.get('distilled_count', 0)}",
            f"- Abandoned (terminal, attempt-cap or write-filter drop): "
            f"{coverage.get('abandoned_count', 0)}",
            "",
            (
                "As of merge time, before the backfill reflection has processed "
                "the live corpus, distilled/provisional counts above may "
                "legitimately read 0 (or small) -- the reflection runs at a "
                "300s cadence in the standing `com.valor.reflection-worker` "
                "subprocess and only starts distilling provisional records "
                "written by `ingest()` after this branch is deployed and live "
                "traffic arrives. Legacy pre-Phase-3 records carry no "
                "`distill_status` at all and are counted in none of the three "
                "buckets above."
            ),
            "",
            "## Comparison to Phase 1 baseline",
            "",
        ]
    )

    if not comparison.get("baseline_present"):
        try:
            baseline_path_display = PHASE1_BASELINE_JSON_PATH.relative_to(REPO_ROOT)
        except ValueError:
            # Path isn't under REPO_ROOT (e.g. monkeypatched to a tmp dir in
            # tests) -- fall back to the absolute path rather than raising.
            baseline_path_display = PHASE1_BASELINE_JSON_PATH
        lines.append(
            f"No Phase 1 baseline artifact found at `{baseline_path_display}` -- "
            "comparison skipped."
        )
    else:
        rc = comparison.get("record_count", {})
        jr = comparison.get("junk_rate", {})
        ar = comparison.get("aggregate_act_rate", {})
        lines.extend(
            [
                f"Baseline snapshot: {comparison.get('baseline_snapshot_timestamp', 'unknown')}"
                f" (git {comparison.get('baseline_git_sha', 'unknown')})",
                "",
                "| Metric | Baseline | Current | Delta |",
                "|--------|----------|---------|-------|",
                f"| Record count | {rc.get('baseline', 0)} | {rc.get('current', 0)}"
                f" | {rc.get('delta', 0):+d} |",
                f"| Junk rate | {_rate_str(jr.get('baseline'))} | {_rate_str(jr.get('current'))}"
                f" | {_rate_str(jr.get('delta'))} |",
                f"| Aggregate act rate | {_rate_str(ar.get('baseline'))}"
                f" | {_rate_str(ar.get('current'))} | {_rate_str(ar.get('delta'))} |",
                "",
                "### Importance histogram: baseline vs. current",
                "",
                "| Bucket | Baseline | Current | Delta |",
                "|--------|----------|---------|-------|",
            ]
        )
        for bucket, vals in comparison.get("importance_histogram", {}).items():
            lines.append(
                f"| `{bucket}` | {vals.get('baseline', 0)} | {vals.get('current', 0)}"
                f" | {vals.get('delta', 0):+d} |"
            )
        lines.append("")

    lines.extend(
        [
            "## Act-rate definition",
            "",
            aggregate.get("act_rate_definition", ""),
            "",
        ]
    )

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--project-key",
        default=None,
        help="Project partition key. Defaults to every project this machine owns.",
    )
    parser.add_argument(
        "--min-evidence",
        type=int,
        default=2,
        help="Minimum acted+dismissed outcome count for a record to count "
        "toward the aggregate act rate. Default 2.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing report artifacts. Without this flag, the "
        "CLI refuses to run when either artifact already exists.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    existing = [p for p in (REPORT_JSON_PATH, REPORT_MD_PATH) if p.exists()]
    if existing and not args.force:
        names = ", ".join(str(p) for p in existing)
        logger.error(
            "[distilled_ingest_report] Refusing to overwrite existing report "
            "artifact(s): %s. Re-run with --force to overwrite both.",
            names,
        )
        return 1

    try:
        report = build_report(project_key=args.project_key, min_evidence=args.min_evidence)
        markdown_content = render_markdown(report)
        json_content = json.dumps(report, indent=2, default=str)
    except Exception as e:
        logger.error("[distilled_ingest_report] Failed to compute report: %s", e)
        return 1

    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON_PATH.write_text(json_content)
    REPORT_MD_PATH.write_text(markdown_content)

    print(f"Wrote {REPORT_JSON_PATH}")
    print(f"Wrote {REPORT_MD_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
