#!/usr/bin/env python3
"""TTFT regression gate (issue #1227).

Reads ``logs/cold_start_metrics.jsonl`` (produced by
:mod:`agent.cold_start_metrics`), filters entries by ``--session-type``,
takes the last ``--last N`` matching entries, computes the median
``ttft_seconds``, and exits 0 if the median is below the supplied
``--threshold`` or 1 otherwise.

This is the executable gate referenced by ``docs/plans/sdlc-1227.md``
(Verification table line 537, Success Criteria line 337) and is wired into
``scripts/nightly_regression_tests.py`` as a post-run check.

Usage::

    python scripts/check_ttft.py --session-type pm --last 10 --threshold 90
    python scripts/check_ttft.py --session-type pm --last 10 --threshold 120 \
        --log-file /custom/path.jsonl

Exit codes:

* ``0`` — median < threshold (PASS)
* ``1`` — median >= threshold, log file missing, or no matching entries (FAIL)

Output (single line)::

    median=XX.Xs N=N threshold=Ts [PASS|FAIL]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

DEFAULT_LOG_FILE = Path("logs/cold_start_metrics.jsonl")


def load_entries(log_file: Path, *, session_type: str) -> list[dict]:
    """Load JSONL entries from ``log_file`` filtered by ``session_type``.

    Skips malformed lines and entries missing ``ttft_seconds`` silently —
    the gate is best-effort and a single corrupt line must never block
    the regression check.

    Args:
        log_file: Path to the JSONL log produced by ``record_ttft()``.
        session_type: e.g. ``"pm"``, ``"dev"``, ``"teammate"``.

    Returns:
        List of parsed entries (dicts) matching ``session_type`` and
        carrying a numeric ``ttft_seconds`` field, in file order.
    """
    entries: list[dict] = []
    with log_file.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("session_type") != session_type:
                continue
            ttft = entry.get("ttft_seconds")
            if not isinstance(ttft, (int, float)):
                continue
            entries.append(entry)
    return entries


def compute_median(values: list[float]) -> float:
    """Return the median of a non-empty list of floats.

    Raises ``ValueError`` if ``values`` is empty — the caller decides how
    to surface a "no data" condition.
    """
    if not values:
        raise ValueError("compute_median: empty input")
    return statistics.median(values)


def _format_result(median: float, n: int, threshold: float, passed: bool) -> str:
    verdict = "PASS" if passed else "FAIL"
    return f"median={median:.1f}s N={n} threshold={threshold:g}s [{verdict}]"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="TTFT regression gate — reads logs/cold_start_metrics.jsonl "
        "and exits non-zero on regression."
    )
    parser.add_argument(
        "--session-type",
        required=True,
        help='Session type to filter on (e.g. "pm", "dev", "teammate").',
    )
    parser.add_argument(
        "--last",
        type=int,
        required=True,
        help="Number of most-recent matching entries to consider.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        required=True,
        help="Threshold in seconds. Median must be strictly less than this to pass.",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=DEFAULT_LOG_FILE,
        help=f"Path to the JSONL metrics file (default: {DEFAULT_LOG_FILE}).",
    )
    args = parser.parse_args(argv)

    log_file: Path = args.log_file
    if not log_file.exists():
        print(
            f"check_ttft: log file not found: {log_file} "
            f"(no TTFT data yet; run a few PM sessions first)"
        )
        return 1

    entries = load_entries(log_file, session_type=args.session_type)
    last = entries[-args.last :] if args.last > 0 else []
    n = len(last)

    if n == 0:
        print(
            f"median=n/a N=0 threshold={args.threshold:g}s [FAIL] "
            f"(no entries matching session_type={args.session_type!r} in {log_file})"
        )
        return 1

    values = [float(e["ttft_seconds"]) for e in last]
    median = compute_median(values)
    passed = median < args.threshold
    print(_format_result(median, n, args.threshold, passed))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
