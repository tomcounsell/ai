"""Every lane plan declares the issue it owns.

`tracking:` frontmatter is the only rung that resolves an issue to its plan
document. There is no filename-stem fallback and no bare-mention search, so a
plan authored without it is invisible to the pipeline: the lane's plan-existence
gate reads as "no plan", and a CRITIQUE verdict that needs the document raises.

This test is the invariant's durable owner. A hook validator would be a new
control surface and belongs in its own change; a test fails at the same moment
with the same information and costs nothing to keep.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tools.plan_doc_scope import NON_LANE_PLANS

PLANS_DIR = Path(__file__).resolve().parents[2] / "docs" / "plans"

# A real issue reference: a `#N` token or a `.../issues/N` URL. The looser
# `^tracking:\s*\S` this replaced accepted the string `none yet` as a
# declaration, which is how a placeholder passed as a tracking issue.
_TRACKING_RE = re.compile(r"^tracking:.*(?:#\d+|issues/\d+)", re.MULTILINE)

# Frontmatter only. A `tracking:` line further down the body is prose.
_FRONTMATTER_LINES = 12


def _plan_docs() -> list[Path]:
    return sorted(p for p in PLANS_DIR.glob("*.md") if p.name not in NON_LANE_PLANS)


def test_plans_dir_is_present_and_populated():
    """Guards the sweep itself: an empty glob would pass every case below."""
    assert PLANS_DIR.is_dir(), f"{PLANS_DIR} is missing"
    assert len(_plan_docs()) > 0, "no plan documents found -- the sweep is vacuous"


@pytest.mark.parametrize("plan_path", _plan_docs(), ids=lambda p: p.name)
def test_plan_declares_a_tracking_issue(plan_path: Path):
    head = "\n".join(plan_path.read_text().splitlines()[:_FRONTMATTER_LINES])
    assert _TRACKING_RE.search(head), (
        f"{plan_path.name} has no resolvable `tracking:` frontmatter. Add a "
        f"`tracking:` line naming the issue this plan owns (a `#N` token or an "
        f"issue URL) within the first {_FRONTMATTER_LINES} lines, or add the "
        f"file to NON_LANE_PLANS in tools/plan_doc_scope.py if it is not a lane "
        f"plan."
    )


@pytest.mark.parametrize("excluded", sorted(NON_LANE_PLANS))
def test_excluded_plans_still_exist(excluded: str):
    """A stale exclusion silently shrinks the invariant's scope."""
    assert (PLANS_DIR / excluded).exists(), (
        f"NON_LANE_PLANS names {excluded}, which no longer exists. Remove the "
        f"entry from tools/plan_doc_scope.py."
    )
