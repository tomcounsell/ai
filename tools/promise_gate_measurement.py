"""Promise-gate phase-4 measurement report (issue #3027, #3035).

Samples ``logs/classification_audit.jsonl`` and an optional file of sampled
``ask_coverage`` schema objects (the PM-turn schema's per-clause coverage
array — see ``agent/session_runner/router.py::PM_TURN_JSON_SCHEMA`` and
``docs/features/message-drafter.md``), and reports:

* Latency percentiles (p50/p95/p99) of ``elapsed_ms`` grouped by audit
  ``source`` AND ``transport`` — a single blended number is dominated by
  whichever calling surface sends the most traffic (PM-turn/SDLC scenarios
  in practice) and does not characterize general outbound traffic.
* Contradiction flags: an ``ask_coverage`` disposition contradicted by its
  own ``evidence`` text, or by ground truth (a ``delivered`` entry with
  empty evidence — should already be impossible via
  ``_normalize_ask_coverage``, but the tool double-checks rather than
  trusting the upstream invariant blindly).

Tolerant of the ~40 legacy audit rows written before the ``kind`` field
existed. Rows missing ``elapsed_ms``/``queue_wait_ms`` are excluded from
percentile math, never treated as zero.

This tool's report is the **recorded entry criterion** for the deferred
phase-4 decision tracked in issue #3035 (embargoed until 2026-09-10). See
``docs/features/promise-gate.md`` §Phase-4 measurement tool.

Usage:
    python -m tools.promise_gate_measurement
    python -m tools.promise_gate_measurement \\
        --audit-log logs/classification_audit.jsonl \\
        --ask-coverage-file /path/to/sampled_ask_coverage.jsonl
    python -m tools.promise_gate_measurement --json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_AUDIT_LOG = Path(__file__).parent.parent / "logs" / "classification_audit.jsonl"

# Recognizes both the CLI-path and drafter-path source namespaces plus the
# terminal-flush route. A legacy row with no "kind" field is still counted
# as a promise-gate row as long as its "source" matches one of these
# prefixes/values -- this is the tolerance the ~40 no-kind legacy rows need.
_PROMISE_GATE_SOURCE_PREFIXES = ("promise_gate_",)
_PROMISE_GATE_EXACT_SOURCES = ("terminal_flush",)

_NEGATIVE_EVIDENCE_KEYWORDS = (
    "failed",
    "failure",
    "could not",
    "couldn't",
    "unable",
    "did not",
    "didn't",
    "blocked on",
    "no pr yet",
    "not done",
    "never shipped",
)

_POSITIVE_EVIDENCE_KEYWORDS = (
    "done",
    "shipped",
    "merged",
    "completed",
    "committed",
    "delivered",
)

_VALID_DISPOSITIONS = ("delivered", "blocked", "declined", "not_started")


def _is_promise_gate_row(row: dict[str, Any]) -> bool:
    kind = row.get("kind")
    if kind == "promise_gate":
        return True
    if kind is not None:
        return False  # a different classifier's row (e.g. read_the_room)
    source = row.get("source")
    if not isinstance(source, str):
        return False
    return source.startswith(_PROMISE_GATE_SOURCE_PREFIXES) or source in _PROMISE_GATE_EXACT_SOURCES


def load_audit_rows(path: Path) -> list[dict[str, Any]]:
    """Load and filter promise-gate rows from the audit JSONL.

    Malformed lines are skipped rather than raising -- an audit log is
    append-only best-effort telemetry, not a validated data store, and a
    single truncated tail line (e.g. from a crash mid-write) must not sink
    the whole report.
    """
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            if _is_promise_gate_row(row):
                rows.append(row)
    return rows


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return float("nan")
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = k - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


@dataclass
class LatencyBucket:
    source: str
    transport: str
    values_ms: list[float] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.values_ms)

    def percentiles(self) -> dict[str, float]:
        s = sorted(self.values_ms)
        return {
            "p50": _percentile(s, 0.50),
            "p95": _percentile(s, 0.95),
            "p99": _percentile(s, 0.99),
        }


def latency_by_source_and_transport(rows: list[dict[str, Any]]) -> list[LatencyBucket]:
    """Group elapsed_ms by (source, transport). Rows without elapsed_ms are excluded."""
    buckets: dict[tuple[str, str], LatencyBucket] = {}
    for row in rows:
        elapsed = row.get("elapsed_ms")
        if not isinstance(elapsed, int | float):
            continue
        source = row.get("source") or "unknown"
        transport = row.get("transport") or "unknown"
        key = (source, transport)
        bucket = buckets.setdefault(key, LatencyBucket(source=source, transport=transport))
        bucket.values_ms.append(float(elapsed))
    return sorted(buckets.values(), key=lambda b: (b.source, b.transport))


def load_ask_coverage_samples(path: Path | None) -> list[dict[str, Any]]:
    """Load sampled ask_coverage entries from a JSONL file.

    Each line may be either a single ``{item, disposition, evidence}``
    object or a JSON array of such objects (matching how a PM turn's whole
    ``ask_coverage`` array might be sampled per-turn). Malformed lines and
    non-object entries are skipped.
    """
    if path is None or not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            candidates = parsed if isinstance(parsed, list) else [parsed]
            for c in candidates:
                if isinstance(c, dict):
                    entries.append(c)
    return entries


@dataclass
class ContradictionFlag:
    item: str
    disposition: str
    evidence: str
    reason: str


def find_contradictions(entries: list[dict[str, Any]]) -> list[ContradictionFlag]:
    """Flag ask_coverage entries whose disposition contradicts their own evidence.

    Deliberately keyword-based and conservative -- this is a sampling aid
    for a human to review, not a mechanical verdict. False negatives are
    expected and fine; the goal is surfacing candidates worth a look, not
    exhaustive detection.
    """
    flags: list[ContradictionFlag] = []
    for entry in entries:
        item = str(entry.get("item") or "")
        disposition = str(entry.get("disposition") or "")
        evidence = str(entry.get("evidence") or "")
        evidence_lower = evidence.lower()

        if disposition not in _VALID_DISPOSITIONS:
            continue

        if disposition == "delivered" and not evidence.strip():
            flags.append(
                ContradictionFlag(
                    item=item,
                    disposition=disposition,
                    evidence=evidence,
                    reason="delivered with empty evidence (should be structurally "
                    "impossible via _normalize_ask_coverage -- flag anyway)",
                )
            )
            continue

        if disposition == "delivered" and any(
            kw in evidence_lower for kw in _NEGATIVE_EVIDENCE_KEYWORDS
        ):
            flags.append(
                ContradictionFlag(
                    item=item,
                    disposition=disposition,
                    evidence=evidence,
                    reason="disposition=delivered but evidence text reads as a "
                    "negative/incomplete outcome",
                )
            )

        if disposition in ("blocked", "declined", "not_started") and any(
            kw in evidence_lower for kw in _POSITIVE_EVIDENCE_KEYWORDS
        ):
            flags.append(
                ContradictionFlag(
                    item=item,
                    disposition=disposition,
                    evidence=evidence,
                    reason=f"disposition={disposition} but evidence text reads as "
                    "a completed outcome",
                )
            )

    return flags


def build_report(
    audit_log: Path,
    ask_coverage_file: Path | None,
) -> dict[str, Any]:
    rows = load_audit_rows(audit_log)
    buckets = latency_by_source_and_transport(rows)
    ask_coverage_entries = load_ask_coverage_samples(ask_coverage_file)
    contradictions = find_contradictions(ask_coverage_entries)

    legacy_no_kind = sum(1 for r in rows if "kind" not in r)

    return {
        "audit_log": str(audit_log),
        "total_promise_gate_rows": len(rows),
        "legacy_rows_without_kind": legacy_no_kind,
        "latency_by_source_and_transport": [
            {
                "source": b.source,
                "transport": b.transport,
                "count": b.count,
                **b.percentiles(),
            }
            for b in buckets
        ],
        "ask_coverage_file": str(ask_coverage_file) if ask_coverage_file else None,
        "ask_coverage_samples": len(ask_coverage_entries),
        "contradictions": [
            {
                "item": c.item,
                "disposition": c.disposition,
                "evidence": c.evidence,
                "reason": c.reason,
            }
            for c in contradictions
        ],
    }


def _print_human_report(report: dict[str, Any]) -> None:
    print(f"Audit log: {report['audit_log']}")
    print(
        f"Promise-gate rows: {report['total_promise_gate_rows']} "
        f"({report['legacy_rows_without_kind']} legacy rows with no 'kind' field)"
    )
    print()
    print("Latency (ms) by source x transport:")
    if not report["latency_by_source_and_transport"]:
        print("  (no rows with elapsed_ms found)")
    for bucket in report["latency_by_source_and_transport"]:
        print(
            f"  {bucket['source']:<32} {bucket['transport']:<12} "
            f"n={bucket['count']:<5} "
            f"p50={bucket['p50']:.1f} p95={bucket['p95']:.1f} p99={bucket['p99']:.1f}"
        )
    print()
    if report["ask_coverage_file"]:
        print(
            f"ask_coverage samples: {report['ask_coverage_samples']} "
            f"(from {report['ask_coverage_file']})"
        )
    else:
        print("ask_coverage samples: none (--ask-coverage-file not provided)")
    print(f"Contradiction flags: {len(report['contradictions'])}")
    for c in report["contradictions"]:
        print(f"  - [{c['disposition']}] {c['item']!r}: {c['reason']}")
        print(f"      evidence: {c['evidence']!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Promise-gate phase-4 measurement report: latency percentiles "
            "grouped by audit source and calling surface, plus ask_coverage "
            "contradiction flags. Recorded entry criterion for the deferred "
            "phase-4 decision in issue #3035 (embargoed until 2026-09-10)."
        )
    )
    parser.add_argument(
        "--audit-log",
        type=Path,
        default=DEFAULT_AUDIT_LOG,
        help=f"Path to the classification audit JSONL (default: {DEFAULT_AUDIT_LOG})",
    )
    parser.add_argument(
        "--ask-coverage-file",
        type=Path,
        default=None,
        help=(
            "Optional path to a JSONL file of sampled ask_coverage entries "
            "(one object or one array of objects per line: "
            "{item, disposition, evidence})"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the report as JSON instead of the human-readable summary",
    )
    args = parser.parse_args(argv)

    report = build_report(args.audit_log, args.ask_coverage_file)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_human_report(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
