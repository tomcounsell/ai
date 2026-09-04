"""Promise-gate phase-4 measurement report (issue #3027, #3035).

Samples ``logs/classification_audit.jsonl`` and an optional file of sampled
``ask_coverage`` schema objects (the PM-turn schema's per-clause coverage
array — see ``agent/session_runner/router.py::PM_TURN_JSON_SCHEMA`` and
``docs/features/message-drafter.md``), and reports:

* Latency percentiles (p50/p95/p99) of ``elapsed_ms`` grouped by audit
  ``source`` AND ``transport`` — a single blended number is dominated by
  whichever calling surface sends the most traffic (PM-turn/SDLC scenarios
  in practice) and does not characterize general outbound traffic.
* Queue-wait percentiles (p50/p95/p99) of ``queue_wait_ms``, grouped the
  same way, reported alongside the ``elapsed_ms`` bucket for the same
  (source, transport) pair. This separates semaphore-slot contention
  (``queue_wait_ms``) from raw API round-trip time (``elapsed_ms``) — the
  split the #3035 phase-4 decision needs to tell "the gate is slow because
  too many callers are queued" from "the gate is slow because the API is
  slow".
* Contradiction flags: an ``ask_coverage`` disposition contradicted by its
  own ``evidence`` text, or by ground truth (a ``delivered`` entry with
  empty evidence — should already be impossible via
  ``_normalize_ask_coverage``, but the tool double-checks rather than
  trusting the upstream invariant blindly).

Tolerant of the ~40 legacy audit rows written before the ``kind`` field
existed. Rows missing ``elapsed_ms``/``queue_wait_ms`` are excluded from
the respective percentile math, never treated as zero.

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

# Chars scanned immediately around a keyword match to detect an adjacent
# negation, rather than scanning the whole evidence string. Provisional /
# tunable -- picked to catch adjacent phrases like "not done yet" or "did
# not hit any blockers" while NOT reaching across an unrelated clause (e.g.
# "the deploy failed at first but it is now merged and shipped", where
# "failed" must not suppress the later, unrelated "merged"). Re-derive if
# false positive/negative rates on real evidence text drift.
_NEGATION_WINDOW_CHARS = 24

# The subset of _NEGATIVE_EVIDENCE_KEYWORDS that DENY an outcome rather than
# assert a bad one. Only these can be cancelled by a following object noun:
# "did not hit any blockers" is good news, but "blocked on an issue" and
# "failed due to an issue" are the ordinary way a person names a real
# blocker, and cancelling those loses genuine contradictions.
_NEGATION_SHAPED_KEYWORDS = ("could not", "couldn't", "unable", "did not", "didn't")

# Nouns that, immediately following a NEGATION-SHAPED keyword match (e.g.
# "did not hit any ..."), flip the phrase from a genuine failure claim into
# a positive one -- the negation's object is the bad outcome, not the
# claimed disposition itself.
_NEGATION_CANCELING_OBJECTS = ("blocker", "issue", "problem", "delay", "obstacle")


def _iter_keyword_matches(evidence_lower: str, keywords: tuple[str, ...]):
    """Yield (start_index, keyword) for every non-overlapping occurrence of
    any keyword in ``keywords`` within ``evidence_lower``."""
    for kw in keywords:
        start = 0
        while True:
            idx = evidence_lower.find(kw, start)
            if idx == -1:
                break
            yield idx, kw
            start = idx + 1


def _any_positive_keyword_unnegated(evidence_lower: str) -> bool:
    """True when at least one ``_POSITIVE_EVIDENCE_KEYWORDS`` occurrence in
    ``evidence_lower`` is NOT preceded, within ``_NEGATION_WINDOW_CHARS``,
    by "not " or a ``_NEGATIVE_EVIDENCE_KEYWORDS`` phrase.

    Scoped to the window immediately before each match -- an unrelated
    negative clause elsewhere in the string (outside the window) must not
    suppress a genuine, unnegated positive claim later in the same string.
    """
    for idx, _kw in _iter_keyword_matches(evidence_lower, _POSITIVE_EVIDENCE_KEYWORDS):
        window = evidence_lower[max(0, idx - _NEGATION_WINDOW_CHARS) : idx]
        if "not " in window:
            continue
        if any(neg_kw in window for neg_kw in _NEGATIVE_EVIDENCE_KEYWORDS):
            continue
        return True
    return False


def _any_negative_keyword_unnegated(evidence_lower: str) -> bool:
    """True when at least one ``_NEGATIVE_EVIDENCE_KEYWORDS`` occurrence in
    ``evidence_lower`` still reads as a genuine bad outcome.

    A match is cancelled only when it is **negation-shaped**
    (``_NEGATION_SHAPED_KEYWORDS`` -- it denies an outcome) AND is followed,
    within ``_NEGATION_WINDOW_CHARS``, by a ``_NEGATION_CANCELING_OBJECTS``
    noun: "did not hit any blockers" denies the bad outcome, so it reads as
    good news. Keywords that ASSERT a bad outcome ("failed", "failure",
    "blocked on") are never cancelled, because "blocked on an issue with the
    signing key" and "failed due to an issue in prod" are the ordinary way a
    person names a real blocker. Cancelling those would drop genuine
    contradictions, and under-reporting is the worse error for the #3035
    entry criterion this tool feeds.

    Symmetric to :func:`_any_positive_keyword_unnegated` in that both scope
    their check to a window around the match instead of the whole string.
    """
    for idx, kw in _iter_keyword_matches(evidence_lower, _NEGATIVE_EVIDENCE_KEYWORDS):
        if kw in _NEGATION_SHAPED_KEYWORDS:
            window = evidence_lower[idx + len(kw) : idx + len(kw) + _NEGATION_WINDOW_CHARS]
            if any(obj in window for obj in _NEGATION_CANCELING_OBJECTS):
                continue
        return True
    return False


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


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    """Return the interpolated percentile, or None when there is no sample.

    None rather than NaN: an empty bucket means "not measured", and NaN
    serializes to a bare ``NaN`` token that is not valid JSON.
    """
    if not sorted_values:
        return None
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
    queue_wait_values_ms: list[float] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.values_ms)

    @property
    def queue_wait_count(self) -> int:
        return len(self.queue_wait_values_ms)

    def percentiles(self) -> dict[str, float | None]:
        s = sorted(self.values_ms)
        return {
            "p50": _percentile(s, 0.50),
            "p95": _percentile(s, 0.95),
            "p99": _percentile(s, 0.99),
        }

    def queue_wait_percentiles(self) -> dict[str, float | None]:
        s = sorted(self.queue_wait_values_ms)
        return {
            "p50": _percentile(s, 0.50),
            "p95": _percentile(s, 0.95),
            "p99": _percentile(s, 0.99),
        }


def latency_by_source_and_transport(rows: list[dict[str, Any]]) -> list[LatencyBucket]:
    """Group elapsed_ms and queue_wait_ms by (source, transport).

    Each field is excluded independently: a row missing ``elapsed_ms`` but
    carrying ``queue_wait_ms`` still contributes to the queue-wait bucket
    (and vice versa). Neither missing field is ever treated as zero.
    """
    buckets: dict[tuple[str, str], LatencyBucket] = {}
    for row in rows:
        source = row.get("source") or "unknown"
        transport = row.get("transport") or "unknown"
        key = (source, transport)

        elapsed = row.get("elapsed_ms")
        queue_wait = row.get("queue_wait_ms")
        if not isinstance(elapsed, int | float) and not isinstance(queue_wait, int | float):
            continue

        bucket = buckets.setdefault(key, LatencyBucket(source=source, transport=transport))
        if isinstance(elapsed, int | float):
            bucket.values_ms.append(float(elapsed))
        if isinstance(queue_wait, int | float):
            bucket.queue_wait_values_ms.append(float(queue_wait))
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

    NOTE: this classification is plain substring matching against
    ``_NEGATIVE_EVIDENCE_KEYWORDS`` / ``_POSITIVE_EVIDENCE_KEYWORDS`` -- the
    kind of keyword matching CLAUDE.md principle 3 (intelligent systems over
    rigid patterns) discourages for anything decision-bearing. It is
    acceptable *only* because the output is an offline flag a human reads
    before acting, never a gate verdict. Do not promote this function (or
    its keyword lists) into a blocking/gating decision path -- if that need
    arises, replace it with an LLM judgment call the way the drafter's main
    path does, not a bigger keyword list.

    False positives from negation are handled, not just disclaimed, and the
    check is scoped rather than whole-string: a keyword match only counts
    as negated (or canceled) when the negating text sits within
    ``_NEGATION_WINDOW_CHARS`` of that specific match, not anywhere else in
    the string. An unrelated negative clause elsewhere must not suppress a
    genuine, later contradiction in the same evidence string (e.g. "the
    deploy failed at first but it is now merged and shipped" on a
    ``blocked`` disposition still flags on "merged" -- "failed" sits
    outside the window before "merged").

    * A ``blocked``/``declined``/``not_started`` disposition whose evidence
      contains a positive keyword (e.g. "done", "delivered") is flagged
      only when at least one occurrence of that keyword is NOT preceded,
      within the window, by "not " or a negative-evidence keyword (e.g.
      ``"blocked on the key, not done yet"`` and ``"not delivered"`` do not
      flag). See :func:`_any_positive_keyword_unnegated`.
    * A ``delivered`` disposition whose evidence contains a negative
      keyword is flagged unless that occurrence is **negation-shaped**
      (``_NEGATION_SHAPED_KEYWORDS``, which deny an outcome) AND is
      followed within the window by a "negation-canceling" object noun
      such as "blocker"/"issue"/"problem": ``"delivered clean, did not hit
      any blockers"`` does not flag, because the negation's object is the
      bad outcome rather than the disposition. Keywords that ASSERT a bad
      outcome ("failed", "blocked on") are never cancelled this way, so
      ``"delivered; blocked on an issue with the signing key"`` still
      flags. See :func:`_any_negative_keyword_unnegated`.
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

        if disposition == "delivered" and _any_negative_keyword_unnegated(evidence_lower):
            flags.append(
                ContradictionFlag(
                    item=item,
                    disposition=disposition,
                    evidence=evidence,
                    reason="disposition=delivered but evidence text reads as a "
                    "negative/incomplete outcome",
                )
            )

        if disposition in (
            "blocked",
            "declined",
            "not_started",
        ) and _any_positive_keyword_unnegated(evidence_lower):
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
                "queue_wait_count": b.queue_wait_count,
                "queue_wait_ms": b.queue_wait_percentiles(),
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
        head = f"  {bucket['source']:<32} {bucket['transport']:<12} "
        if bucket["count"]:
            print(
                f"{head}n={bucket['count']:<5} "
                f"p50={bucket['p50']:.1f} p95={bucket['p95']:.1f} p99={bucket['p99']:.1f}"
            )
        else:
            print(f"{head}n=0     (no elapsed_ms samples)")
        qw = bucket["queue_wait_ms"]
        if bucket["queue_wait_count"]:
            print(
                f"  {'':<32} {'':<12} "
                f"queue_wait n={bucket['queue_wait_count']:<5} "
                f"p50={qw['p50']:.1f} p95={qw['p95']:.1f} p99={qw['p99']:.1f}"
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
