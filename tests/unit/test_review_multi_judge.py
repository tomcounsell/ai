"""Unit tests for multi-judge consensus at the Review gate.

Covers:
- ``agent.sdlc_review_consensus.compute_consensus`` — pure consensus rule fn.
- ``tools.sdlc_verdict.record_verdict`` extended with ``judges`` / ``consensus``
  side-fields (single-writer invariant preserved).
- PR-comment ordering regression — aggregate ``## Review:`` heading must be
  the LAST ``## Review*:`` heading in the simulated comment sequence.

Reference: docs/plans/multi-judge-consensus-gates.md (rev1).
"""

from __future__ import annotations

import json
import re
from unittest.mock import patch

import pytest

from agent.sdlc_review_consensus import compute_consensus
from tools.sdlc_verdict import record_verdict

# ---------------------------------------------------------------------------
# Fakes (mirrors test_sdlc_verdict.py)
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self, session_id="fake-mj-1", stage_states=None):
        self.session_id = session_id
        self.session_type = "eng"
        if stage_states is None:
            self.stage_states = "{}"
        elif isinstance(stage_states, dict):
            self.stage_states = json.dumps(stage_states)
        else:
            self.stage_states = stage_states

    def save(self):
        pass


@pytest.fixture
def fake_session_reload_patched():
    with patch("tools.stage_states_helpers._reload_session") as mock_reload:
        session = _FakeSession()
        mock_reload.return_value = session
        yield session


# ---------------------------------------------------------------------------
# compute_consensus — any-blocker-wins (default)
# ---------------------------------------------------------------------------


class TestComputeConsensusAnyBlockerWins:
    def test_both_approve_returns_approved(self):
        judges = [
            {
                "judge_id": "code-quality",
                "verdict": "APPROVED",
                "blockers": 0,
                "tech_debt": 0,
                "confidence": 0.9,
            },
            {
                "judge_id": "risk",
                "verdict": "APPROVED",
                "blockers": 0,
                "tech_debt": 1,
                "confidence": 0.8,
            },
        ]
        result = compute_consensus(judges, rule="any-blocker-wins")
        assert result["verdict"] == "APPROVED"
        assert result["blockers"] == 0
        assert result["tech_debt"] == 1  # max across judges
        meta = result["consensus"]
        assert meta["rule"] == "any-blocker-wins"
        assert meta["k"] == 2
        assert meta["n"] == 2
        assert meta["tied"] is False
        assert meta["mean_confidence"] == pytest.approx(0.85)

    def test_split_one_blocker_returns_changes_requested(self):
        judges = [
            {
                "judge_id": "code-quality",
                "verdict": "APPROVED",
                "blockers": 0,
                "tech_debt": 0,
                "confidence": 0.7,
            },
            {
                "judge_id": "risk",
                "verdict": "CHANGES REQUESTED",
                "blockers": 1,
                "tech_debt": 0,
                "confidence": 0.95,
            },
        ]
        result = compute_consensus(judges, rule="any-blocker-wins")
        assert result["verdict"] == "CHANGES REQUESTED"
        assert result["blockers"] == 1
        assert result["consensus"]["tied"] is True  # disagreement = tied flag

    def test_both_block_max_aggregates_blockers(self):
        judges = [
            {
                "judge_id": "code-quality",
                "verdict": "CHANGES REQUESTED",
                "blockers": 2,
                "tech_debt": 0,
                "confidence": 0.7,
            },
            {
                "judge_id": "risk",
                "verdict": "CHANGES REQUESTED",
                "blockers": 5,
                "tech_debt": 0,
                "confidence": 0.7,
            },
        ]
        result = compute_consensus(judges, rule="any-blocker-wins")
        assert result["verdict"] == "CHANGES REQUESTED"
        assert result["blockers"] == 5  # max
        assert result["consensus"]["tied"] is False  # both agree to block

    def test_blocker_count_above_zero_overrides_approved_string(self):
        # Defensive: even if a judge returned APPROVED string with blockers>0,
        # the blocker count wins.
        judges = [
            {
                "judge_id": "a",
                "verdict": "APPROVED",
                "blockers": 3,
                "tech_debt": 0,
                "confidence": 0.5,
            },
        ]
        result = compute_consensus(judges, rule="any-blocker-wins")
        assert result["verdict"] == "CHANGES REQUESTED"
        assert result["blockers"] == 3


# ---------------------------------------------------------------------------
# compute_consensus — unanimous-approved (opt-in)
# ---------------------------------------------------------------------------


class TestComputeConsensusUnanimousApproved:
    def test_both_approve_returns_approved(self):
        judges = [
            {
                "judge_id": "a",
                "verdict": "APPROVED",
                "blockers": 0,
                "tech_debt": 0,
                "confidence": 0.9,
            },
            {
                "judge_id": "b",
                "verdict": "APPROVED",
                "blockers": 0,
                "tech_debt": 0,
                "confidence": 0.9,
            },
        ]
        result = compute_consensus(judges, rule="unanimous-approved")
        assert result["verdict"] == "APPROVED"

    def test_split_returns_changes_requested(self):
        judges = [
            {
                "judge_id": "a",
                "verdict": "APPROVED",
                "blockers": 0,
                "tech_debt": 0,
                "confidence": 0.9,
            },
            {
                "judge_id": "b",
                "verdict": "CHANGES REQUESTED",
                "blockers": 1,
                "tech_debt": 0,
                "confidence": 0.9,
            },
        ]
        result = compute_consensus(judges, rule="unanimous-approved")
        assert result["verdict"] == "CHANGES REQUESTED"


# ---------------------------------------------------------------------------
# compute_consensus — empty / dedup / ordering
# ---------------------------------------------------------------------------


class TestComputeConsensusEdgeCases:
    def test_empty_judges_returns_conservative(self):
        result = compute_consensus([], rule="any-blocker-wins")
        assert result["verdict"] == "CHANGES REQUESTED"
        assert result["blockers"] >= 1
        assert result["consensus"]["n"] == 0

    def test_duplicate_judge_id_last_wins(self):
        judges = [
            {
                "judge_id": "code-quality",
                "verdict": "APPROVED",
                "blockers": 0,
                "tech_debt": 0,
                "confidence": 0.5,
            },
            {
                "judge_id": "code-quality",
                "verdict": "CHANGES REQUESTED",
                "blockers": 2,
                "tech_debt": 0,
                "confidence": 0.9,
            },
        ]
        result = compute_consensus(judges, rule="any-blocker-wins")
        # Dedup: only the LAST entry for code-quality counts.
        assert result["verdict"] == "CHANGES REQUESTED"
        assert result["consensus"]["n"] == 1

    def test_deterministic_sort_by_judge_id(self):
        # Reverse order in input should not change output (sorted alphabetically).
        judges_a = [
            {
                "judge_id": "risk",
                "verdict": "APPROVED",
                "blockers": 0,
                "tech_debt": 0,
                "confidence": 0.8,
            },
            {
                "judge_id": "code-quality",
                "verdict": "APPROVED",
                "blockers": 0,
                "tech_debt": 0,
                "confidence": 0.9,
            },
        ]
        judges_b = list(reversed(judges_a))
        result_a = compute_consensus(judges_a, rule="any-blocker-wins")
        result_b = compute_consensus(judges_b, rule="any-blocker-wins")
        assert result_a["verdict"] == result_b["verdict"]
        assert result_a["blockers"] == result_b["blockers"]
        assert result_a["consensus"]["mean_confidence"] == result_b["consensus"]["mean_confidence"]

    def test_malformed_judge_dict_raises_value_error(self):
        # Missing required key 'judge_id'.
        bad = [{"verdict": "APPROVED", "blockers": 0}]
        with pytest.raises(ValueError):
            compute_consensus(bad, rule="any-blocker-wins")

    def test_unknown_rule_raises_value_error(self):
        judges = [
            {
                "judge_id": "a",
                "verdict": "APPROVED",
                "blockers": 0,
                "tech_debt": 0,
                "confidence": 0.9,
            },
        ]
        with pytest.raises(ValueError):
            compute_consensus(judges, rule="bogus-rule")


# ---------------------------------------------------------------------------
# record_verdict extension — judges / consensus side-fields
# ---------------------------------------------------------------------------


class TestRecordVerdictWithJudges:
    def test_persists_judges_and_consensus_side_fields(self, fake_session_reload_patched):
        session = fake_session_reload_patched
        judges = [
            {
                "judge_id": "code-quality",
                "verdict": "APPROVED",
                "blockers": 0,
                "tech_debt": 0,
                "confidence": 0.9,
            },
            {
                "judge_id": "risk",
                "verdict": "APPROVED",
                "blockers": 0,
                "tech_debt": 1,
                "confidence": 0.8,
            },
        ]
        consensus_meta = {
            "rule": "any-blocker-wins",
            "k": 2,
            "n": 2,
            "mean_confidence": 0.85,
            "blocker_aggregation": "max",
            "tied": False,
            "decided_at": "2026-05-08T12:00:00+00:00",
        }
        record = record_verdict(
            session,
            "REVIEW",
            "APPROVED",
            blockers=0,
            tech_debt=1,
            judges=judges,
            consensus=consensus_meta,
        )
        assert record["verdict"] == "APPROVED"
        # Round-trip through stage_states
        data = json.loads(session.stage_states)
        review = data["_verdicts"]["REVIEW"]
        assert review["verdict"] == "APPROVED"
        assert review["blockers"] == 0
        assert review["tech_debt"] == 1
        assert review["_judges"] == judges
        assert review["_consensus"] == consensus_meta

    def test_no_judges_kwarg_writes_no_side_fields(self, fake_session_reload_patched):
        """Back-compat: single-judge callers (no judges kwarg) write today's shape."""
        session = fake_session_reload_patched
        record = record_verdict(
            session,
            "REVIEW",
            "APPROVED",
            blockers=0,
            tech_debt=0,
        )
        assert record["verdict"] == "APPROVED"
        data = json.loads(session.stage_states)
        review = data["_verdicts"]["REVIEW"]
        assert "_judges" not in review
        assert "_consensus" not in review

    def test_malformed_judge_dict_returns_empty_no_partial_write(self, fake_session_reload_patched):
        session = fake_session_reload_patched
        bad_judges = [{"judge_id": "a"}]  # missing 'verdict', 'blockers'
        result = record_verdict(
            session,
            "REVIEW",
            "APPROVED",
            blockers=0,
            judges=bad_judges,
            consensus={"rule": "any-blocker-wins"},
        )
        assert result == {}
        # Stage_states untouched (no partial write).
        data = json.loads(session.stage_states)
        assert "_verdicts" not in data or "REVIEW" not in data.get("_verdicts", {})

    def test_critique_with_judges_kwarg_is_rejected(self, fake_session_reload_patched):
        """CRITIQUE never uses _judges (it aggregates internally already)."""
        session = fake_session_reload_patched
        result = record_verdict(
            session,
            "CRITIQUE",
            "READY TO BUILD (no concerns)",
            judges=[{"judge_id": "a", "verdict": "APPROVED", "blockers": 0}],
            consensus={"rule": "any-blocker-wins"},
        )
        # Either reject outright OR write scalar without side-fields.
        # Spec: reject (return {}) so callers can't accidentally fork CRITIQUE shape.
        assert result == {}


# ---------------------------------------------------------------------------
# PR-comment ordering regression
# ---------------------------------------------------------------------------


class TestPRCommentOrderingRegression:
    """Asserts the aggregate `## Review:` heading is the LAST `## Review*:`
    heading in the simulated PR-comment sequence.

    do-merge.md picks the latest `## Review:` (no judge suffix). The parent
    must post per-judge comments (`## Review (Judge X):`) FIRST, then the
    aggregate (`## Review: Approved` / `## Review: Changes Requested`) LAST.
    """

    # Regex matching do-merge.md's review-comment check.
    AGGREGATE_RE = re.compile(r"^## Review: (Approved|Changes Requested)", re.MULTILINE)

    def _simulate_parent_posting(self, judges: list[dict], aggregate_verdict: str) -> list[str]:
        """Mirror the orchestration the do-pr-review SKILL must follow:

        1. Sequentially post each per-judge comment.
        2. Post the aggregate LAST.

        Returns the ordered list of comment bodies as they would appear on the PR.
        """
        comments: list[str] = []
        for j in judges:
            comments.append(f"## Review (Judge {j['judge_id']}): {j['verdict']}\n\nDetails …")
        comments.append(f"## Review: {aggregate_verdict}\n\nAggregate of {len(judges)} judges.")
        return comments

    def test_aggregate_is_last_review_heading(self):
        judges = [
            {"judge_id": "code-quality", "verdict": "APPROVED"},
            {"judge_id": "risk", "verdict": "APPROVED"},
        ]
        comments = self._simulate_parent_posting(judges, "Approved")

        # Find indices of every `## Review*:` heading in the comment sequence.
        review_indices: list[int] = []
        for i, c in enumerate(comments):
            if c.lstrip().startswith("## Review"):
                review_indices.append(i)
        assert len(review_indices) == 3  # 2 per-judge + 1 aggregate

        # Aggregate (the one matching do-merge's regex) must be at the LAST index.
        aggregate_idx = next(i for i, c in enumerate(comments) if self.AGGREGATE_RE.search(c))
        assert aggregate_idx == review_indices[-1]
        assert aggregate_idx == len(comments) - 1

    def test_do_merge_regex_matches_only_aggregate(self):
        judges = [
            {"judge_id": "code-quality", "verdict": "APPROVED"},
            {"judge_id": "risk", "verdict": "CHANGES REQUESTED"},
        ]
        comments = self._simulate_parent_posting(judges, "Changes Requested")
        # Per-judge headings must NOT match do-merge's regex.
        per_judge = [c for c in comments if "(Judge" in c]
        for c in per_judge:
            assert not self.AGGREGATE_RE.search(c), (
                f"Per-judge comment matched aggregate regex: {c!r}"
            )
        # Exactly one aggregate matches.
        matches = [c for c in comments if self.AGGREGATE_RE.search(c)]
        assert len(matches) == 1
        assert "Changes Requested" in matches[0]

    def test_do_merge_picks_latest_review_when_aggregate_is_last(self):
        """If we walk comments in reverse looking for `## Review:`, we hit the
        aggregate first — confirming the ordering invariant."""
        judges = [
            {"judge_id": "code-quality", "verdict": "APPROVED"},
            {"judge_id": "risk", "verdict": "APPROVED"},
        ]
        comments = self._simulate_parent_posting(judges, "Approved")
        latest_aggregate = None
        for c in reversed(comments):
            if self.AGGREGATE_RE.search(c):
                latest_aggregate = c
                break
        assert latest_aggregate is not None
        assert "Approved" in latest_aggregate


# ---------------------------------------------------------------------------
# CLI shape — record subcommand accepts --judges-json / --consensus-json
# ---------------------------------------------------------------------------


class TestRecordVerdictCLIShape:
    def test_cli_record_help_advertises_judges_json(self, capsys):
        """Smoke test: `python -m tools.sdlc_verdict record --help` lists the
        new flags so operators discover them."""
        from tools.sdlc_verdict import main

        with pytest.raises(SystemExit):
            with patch("sys.argv", ["tools.sdlc_verdict", "record", "--help"]):
                main()
        out = capsys.readouterr().out
        assert "--judges-json" in out
        assert "--consensus-json" in out
