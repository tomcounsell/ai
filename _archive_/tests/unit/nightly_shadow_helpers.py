"""Shared helpers for the nightly shadow-tier test files (issue #2334).

`make_run_flags` is the single source of the default `RunFlags` shape used by
both `test_nightly_classifier.py` and `test_nightly_decision_gate.py`, so the
two files cannot drift on what a "default run" looks like.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

import nightly_regression_tests as nrt  # noqa: E402


def make_run_flags(**kwargs) -> nrt.RunFlags:
    base = {
        "is_seed_run": False,
        "integrity_warnings": [],
        "dry_run": False,
        "baseline_sha": "cafef00d",
    }
    base.update(kwargs)
    return nrt.RunFlags(**base)
