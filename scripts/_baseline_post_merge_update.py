#!/usr/bin/env python3
"""Post-merge baseline update: decay + flake-tracker + quarantine hints.

Invoked by the Full Suite Gate pass-path in ``.claude/commands/do-merge.md``
after :mod:`scripts.baseline_gate` has rendered its verdict. The script:

1. Loads the existing baseline JSON.
2. Calls :func:`scripts.baseline_gate.apply_decay` to increment
   ``recent_pass_count`` for ``real`` entries the PR did not fail, and to
   drop entries whose counter has reached the decay threshold.
3. Calls :func:`scripts.baseline_gate.update_flake_tracker` to update
   consecutive-flake counters using ``new_flaky_occurrences`` from the
   verdict.
4. Calls :func:`scripts.baseline_gate.format_quarantine_hints` to emit any
   QUARANTINE_HINT lines to stderr.
5. Writes the updated baseline back in place.

Usage::

    python3 scripts/_baseline_post_merge_update.py {baseline_file} {gate_verdict_json} {pr_junitxml}

All failures are swallowed (exit 0) — this is advisory post-merge hygiene,
not a gate. Missing files, JSON parse errors, and subprocess hiccups must
not block a merge that already passed the gate.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 4:
        return 0
    baseline_file = sys.argv[1]
    gate_output = sys.argv[2] or "{}"
    pr_junitxml = sys.argv[3]

    try:
        verdict = json.loads(gate_output)
    except json.JSONDecodeError:
        return 0

    try:
        from scripts._baseline_common import failing_node_ids, parse_junitxml
        from scripts.baseline_gate import (
            DEFAULT_DECAY_THRESHOLD,
            DEFAULT_FLAKE_THRESHOLD,
            apply_decay,
            format_quarantine_hints,
            update_flake_tracker,
        )
    except Exception:
        return 0

    try:
        baseline = (
            json.loads(Path(baseline_file).read_text()) if Path(baseline_file).exists() else {}
        )
    except Exception:
        baseline = {}

    decay_thresh = int(baseline.get("_decay_threshold") or DEFAULT_DECAY_THRESHOLD)
    flake_thresh = int(baseline.get("_flake_threshold") or DEFAULT_FLAKE_THRESHOLD)

    try:
        pr_failures = failing_node_ids(parse_junitxml(pr_junitxml))
    except Exception:
        pr_failures = set()

    decayed = apply_decay(baseline, pr_failures, threshold=decay_thresh)
    flaky_now = verdict.get("new_flaky_occurrences") or []
    new_flake_tracker = update_flake_tracker(decayed.get("_flake_tracker"), flaky_now)
    if new_flake_tracker:
        decayed["_flake_tracker"] = new_flake_tracker
    else:
        decayed.pop("_flake_tracker", None)

    for hint in format_quarantine_hints(new_flake_tracker, flaky_now, threshold=flake_thresh):
        sys.stderr.write(hint + "\n")

    try:
        Path(baseline_file).write_text(json.dumps(decayed, indent=2, sort_keys=True) + "\n")
    except Exception:
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
