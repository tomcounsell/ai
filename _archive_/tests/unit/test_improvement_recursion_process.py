"""Research process digest (lane 6, #3218): canonical bytes and validation.

The digest is the one hashing routine both lane 5 and the comparison use, so
the byte form is a cross-lane contract pinned here rather than a private
choice. No Redis needed: the spec is a plain dataclass.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

import pytest

from models.improvement_investigation import INVESTIGATION_KINDS
from tools.improvement_recursion.process import ResearchProcessSpec, research_process_digest


def _spec(**overrides) -> ResearchProcessSpec:
    fields = dict(
        selection_rule="rank",
        investigation_budget_split={"probe": 0.5, "web_research": 0.5},
        revision_cadence_seconds=3600,
        planner_prompt_digest="sha256:0",
        skill_digest="sha256:0",
    )
    fields.update(overrides)
    return ResearchProcessSpec(**fields)


def test_digest_is_sha256_prefixed_hex():
    digest = research_process_digest(_spec())
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64
    int(digest[len("sha256:") :], 16)


def test_digest_independent_of_split_key_order():
    a = _spec(investigation_budget_split={"probe": 0.5, "web_research": 0.5})
    b = _spec(investigation_budget_split={"web_research": 0.5, "probe": 0.5})
    assert research_process_digest(a) == research_process_digest(b)


def test_digest_independent_of_extra_key_order():
    a = _spec(extra={"x": 1, "y": 2})
    b = _spec(extra={"y": 2, "x": 1})
    assert research_process_digest(a) == research_process_digest(b)


def test_digest_changes_when_any_field_changes():
    base = research_process_digest(_spec())
    assert research_process_digest(_spec(selection_rule="other")) != base
    assert research_process_digest(_spec(revision_cadence_seconds=7200)) != base
    assert research_process_digest(_spec(extra={"k": "v"})) != base


def test_digest_hashes_exact_canonical_bytes():
    """The byte form is the contract: sorted keys, compact separators, UTF-8."""
    spec = _spec(extra={"note": "café"})
    canonical = json.dumps(asdict(spec), sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert research_process_digest(spec) == "sha256:" + hashlib.sha256(canonical).hexdigest()


def test_unknown_investigation_kind_refused():
    spec = _spec(investigation_budget_split={"probe": 0.5, "telepathy": 0.5})
    with pytest.raises(ValueError, match="telepathy"):
        research_process_digest(spec)


@pytest.mark.parametrize(
    "split",
    [
        {"probe": 0.5, "web_research": 0.3},
        {"probe": 0.7, "web_research": 0.5},
        {"probe": 0.0},
    ],
    ids=["under", "over", "zero"],
)
def test_non_empty_split_summing_outside_range_refused(split):
    with pytest.raises(ValueError, match="sum"):
        research_process_digest(_spec(investigation_budget_split=split))


@pytest.mark.parametrize("total", [0.99, 1.0, 1.01])
def test_split_summing_inside_range_accepted(total):
    kinds = list(INVESTIGATION_KINDS)[:2]
    split = {kinds[0]: total / 2, kinds[1]: total / 2}
    assert research_process_digest(_spec(investigation_budget_split=split)).startswith("sha256:")


def test_empty_split_digests():
    """Lane 5 writes every revision with an empty split; the digest must accept it."""
    digest = research_process_digest(_spec(investigation_budget_split={}))
    assert digest.startswith("sha256:")


def test_digest_matches_lane5_canonical_bytes():
    ranking = pytest.importorskip("tools.improvement_ranking", reason="lane 5 (#3217) not landed")
    spec = ResearchProcessSpec(
        selection_rule="ordinal-lexicographic-v1",
        investigation_budget_split={},
        revision_cadence_seconds=3600,
        planner_prompt_digest="sha256:" + "0" * 64,
        skill_digest="sha256:" + "0" * 64,
        extra={"ranking_module_digest": "sha256:" + "0" * 64},
    )
    expected = (
        "sha256:" + hashlib.sha256(ranking.process_spec_json(spec).encode("utf-8")).hexdigest()
    )
    assert research_process_digest(spec) == expected
