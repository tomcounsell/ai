"""Non-interactive CLI: snapshot the current memory-corpus telemetry baseline.

Entry point: ``python -m tools.memory_eval.snapshot [--force]``

Writes two artifacts under ``docs/baselines/``:

- ``memory-telemetry-baseline.json``: the raw
  ``ui.data.memories.get_corpus_metrics()`` output plus provenance fields
  (record count, ISO snapshot timestamp, current git SHA). The
  ``act_rate_definition`` string is already present in the metrics dict
  (see ``tools/memory_eval/ingest_quality.py``), so it is not duplicated.
- ``memory-telemetry-baseline.md``: a short human-readable summary of the
  key numbers (record count, aggregate act rate, junk rate, ingest volume
  by source, decay-imminent count, never-injected count, timestamp, git
  SHA).

Read-only with respect to Memory records: this module never calls
``.save(``, ``.delete(``, or ``.transition_status(``. It only calls
``get_corpus_metrics``, itself a read-only ``.no_track()`` corpus scan
(see ``ui/data/memories.py``).

Existence guard: refuses to overwrite either artifact unless ``--force`` is
passed, so re-running by accident never silently clobbers a prior baseline.
On any metrics-computation failure, nothing is written -- both artifacts'
content is fully built in memory before either file touches disk, so a
computation failure never leaves a truncated/partial artifact behind.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_DIR = REPO_ROOT / "docs" / "baselines"
JSON_PATH = BASELINE_DIR / "memory-telemetry-baseline.json"
MD_PATH = BASELINE_DIR / "memory-telemetry-baseline.md"


def _git_sha() -> str:
    """Best-effort current git SHA via `git rev-parse HEAD`; 'unknown' on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return result.stdout.strip()
    except Exception as e:
        logger.warning("[memory_eval.snapshot] git rev-parse failed: %s", e)
        return "unknown"


def build_baseline(project_key: str | None = None, min_evidence: int = 2) -> dict:
    """Compute the corpus-metrics + provenance dict for the baseline artifact.

    Raises on failure -- the caller is responsible for not writing partial
    output when this raises.
    """
    from ui.data.memories import get_corpus_metrics

    metrics = get_corpus_metrics(project_key=project_key, min_evidence=min_evidence)
    metrics["record_count"] = metrics.get("total_records", 0)
    metrics["snapshot_timestamp"] = datetime.now(UTC).isoformat()
    metrics["git_sha"] = _git_sha()
    return metrics


def render_markdown(baseline: dict) -> str:
    """Human-readable summary of the key baseline numbers."""

    def _rate(value, empty_reason: str) -> str:
        return f"{value:.3f}" if value is not None else f"undefined ({empty_reason})"

    act_rate_str = _rate(baseline.get("aggregate_act_rate"), "no qualifying records")
    junk_rate_str = _rate(baseline.get("junk_rate"), "no durable records")

    source_counts = baseline.get("source_counts") or {}
    source_lines = (
        "\n".join(f"- `{source}`: {count}" for source, count in sorted(source_counts.items()))
        or "- (none)"
    )

    return "\n".join(
        [
            "# Memory Telemetry Baseline",
            "",
            f"Snapshot taken: {baseline.get('snapshot_timestamp', 'unknown')}",
            f"Git SHA: {baseline.get('git_sha', 'unknown')}",
            f"Project key: {baseline.get('project_key', 'unknown')}",
            "",
            "## Key numbers",
            "",
            f"- Record count: {baseline.get('record_count', 0)}",
            f"- Superseded count: {baseline.get('superseded_count', 0)}",
            f"- Durable denominator: {baseline.get('durable_denominator', 0)}",
            f"- Aggregate act rate: {act_rate_str}",
            f"- Junk rate: {junk_rate_str}",
            f"- Junk count: {baseline.get('junk_count', 0)}"
            f" (ack-only: {baseline.get('ack_only_count', 0)},"
            f" fragment: {baseline.get('fragment_suspect_count', 0)})",
            f"- Decay-imminent count: {baseline.get('decay_imminent_count', 0)}",
            f"- Never-injected count: {baseline.get('never_injected_count', 0)}",
            "",
            "## Ingest volume by source",
            "",
            source_lines,
            "",
            "## Act-rate definition",
            "",
            baseline.get("act_rate_definition", ""),
            "",
        ]
    )


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
        help="Overwrite existing baseline artifacts. Without this flag, the "
        "CLI refuses to run when either artifact already exists.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    existing = [p for p in (JSON_PATH, MD_PATH) if p.exists()]
    if existing and not args.force:
        names = ", ".join(str(p) for p in existing)
        logger.error(
            "[memory_eval.snapshot] Refusing to overwrite existing baseline "
            "artifact(s): %s. Re-run with --force to overwrite both.",
            names,
        )
        return 1

    try:
        baseline = build_baseline(project_key=args.project_key, min_evidence=args.min_evidence)
        markdown_content = render_markdown(baseline)
        json_content = json.dumps(baseline, indent=2, default=str)
    except Exception as e:
        logger.error("[memory_eval.snapshot] Failed to compute baseline metrics: %s", e)
        return 1

    # Both artifacts' content is fully computed above before either file is
    # touched -- a computation failure can never leave a partial artifact.
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json_content)
    MD_PATH.write_text(markdown_content)

    print(f"Wrote {JSON_PATH}")
    print(f"Wrote {MD_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
