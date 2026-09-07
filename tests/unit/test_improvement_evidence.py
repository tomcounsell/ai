"""Tests for the improvement evidence writers (#3177).

The plan's user-visible criterion for lane 2 is that the correction detector
runs against real sessions *because something ticks it*. These tests cover both
halves: the durable row (``ImprovementEvidence.record_once`` and its dedup), and
the three observer adapters the reflection tick calls.

They are deliberately unkind to the classifier. A regex cannot tell an
architectural rescue from a preference most of the time, and the honest answer
is ``unknown`` — a test that demanded confident labels would be pushing the
detector toward exactly the overclaiming the plan warns about.

Uses the autouse ``redis_test_db`` fixture (tests/conftest.py); production Redis
is never touched, and every row is written under a test-scoped ``project_key``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from models.improvement_evidence import (
    EVIDENCE_CLASSIFICATIONS,
    EVIDENCE_KINDS,
    ImprovementEvidence,
)
from reflections import improvement_collect

PK = "test-3177-evidence"


class TestRecordOnce:
    def test_writes_a_row(self):
        row = ImprovementEvidence.record_once(
            PK, "correction", source_session_id="s-1", text="no, i meant the other file"
        )
        assert row is not None
        assert row.kind == "correction"
        assert list(ImprovementEvidence.query.filter(project_key=PK, kind="correction"))

    def test_second_identical_observation_is_dropped(self):
        first = ImprovementEvidence.record_once(PK, "correction", source_session_id="s-dup")
        second = ImprovementEvidence.record_once(PK, "correction", source_session_id="s-dup")
        assert first is not None
        assert second is None

    def test_dedup_prefers_source_ref_when_present(self):
        assert (
            ImprovementEvidence.record_once(PK, "inspiration", source_ref="memory:abc") is not None
        )
        # Same ref, different session: still the same idea, still one row.
        assert (
            ImprovementEvidence.record_once(
                PK, "inspiration", source_ref="memory:abc", source_session_id="other"
            )
            is None
        )

    def test_different_kinds_do_not_collide(self):
        assert ImprovementEvidence.record_once(PK, "correction", source_ref="r-1") is not None
        assert ImprovementEvidence.record_once(PK, "shipped_work", source_ref="r-1") is not None

    def test_an_observation_with_no_identity_is_never_deduped(self):
        """Two anonymous observations are two observations, not one."""
        assert ImprovementEvidence.record_once(PK, "other", text="a") is not None
        assert ImprovementEvidence.record_once(PK, "other", text="b") is not None

    def test_unknown_kind_degrades_to_other_rather_than_raising(self):
        row = ImprovementEvidence.record_once(PK, "not-a-kind", source_ref="k-1")
        assert row is not None
        assert row.kind == "other"

    def test_unknown_classification_degrades_to_unknown(self):
        row = ImprovementEvidence.record_once(
            PK, "correction", classification="wildly-confident", source_ref="c-1"
        )
        assert row is not None
        assert row.classification == "unknown"

    def test_recent_returns_newest_first_and_respects_limit(self):
        for i in range(5):
            ImprovementEvidence.record_once(PK, "other", source_ref=f"n-{i}")
        rows = ImprovementEvidence.recent(PK, limit=3)
        assert len(rows) == 3
        stamps = [r.created_at for r in rows if r.created_at]
        assert stamps == sorted(stamps, reverse=True)

    def test_partition_is_scoped_to_project_key(self):
        ImprovementEvidence.record_once(PK, "other", source_ref="scoped-1")
        assert ImprovementEvidence.recent("test-3177-other-project", limit=10) == []


class TestClassifyCorrection:
    @pytest.mark.parametrize(
        "text",
        [
            "no, i meant the whole journey, not just that endpoint",
            "that's the wrong approach entirely",
            "you missed the point of this feature",
            "this is the wrong abstraction",
        ],
    )
    def test_architectural_phrasings(self, text):
        assert improvement_collect.classify_correction(text) == "architectural"

    @pytest.mark.parametrize(
        "text",
        ["i prefer snake_case here", "the wording is off", "nit: rename this variable"],
    )
    def test_preference_phrasings(self, text):
        assert improvement_collect.classify_correction(text) == "preference"

    @pytest.mark.parametrize(
        "text",
        ["also add a CSV export", "on second thought, let's do both"],
    )
    def test_scope_phrasings(self, text):
        assert improvement_collect.classify_correction(text) == "scope"

    @pytest.mark.parametrize(
        "text",
        ["actually, hold on", "i said tuesday", "please don't", "that's wrong"],
    )
    def test_ambiguous_phrasings_stay_unknown(self, text):
        """The common case. An honest 'unknown' beats a confident wrong label."""
        assert improvement_collect.classify_correction(text) == "unknown"

    def test_every_classification_is_a_declared_value(self):
        for text in ("wrong approach", "nit", "also add", "hello"):
            assert improvement_collect.classify_correction(text) in EVIDENCE_CLASSIFICATIONS


def _session_with_transcript(tmp_path, name, lines):
    log = tmp_path / f"{name}.log"
    log.write_text("\n".join(lines))
    session = MagicMock()
    session.session_id = name
    session.log_path = str(log)
    session.created_at = datetime.now(UTC)
    return session


class TestCollectCorrections:
    def test_writes_one_row_per_session_with_a_correction(self, tmp_path):
        sessions = [
            _session_with_transcript(
                tmp_path, "sess-a", ["USER: no, i meant the other approach", "ASSISTANT: ok"]
            ),
            _session_with_transcript(tmp_path, "sess-b", ["USER: sounds good", "ASSISTANT: done"]),
        ]
        with patch.object(improvement_collect, "_recent_sessions", return_value=sessions):
            written = improvement_collect.collect_corrections(PK)

        assert written == 1
        rows = list(ImprovementEvidence.query.filter(project_key=PK, kind="correction"))
        assert [r.source_session_id for r in rows] == ["sess-a"]

    def test_a_session_contributes_one_row_however_many_matches(self, tmp_path):
        session = _session_with_transcript(
            tmp_path,
            "sess-many",
            ["USER: no, i meant X", "USER: that's wrong", "USER: i said Y"],
        )
        with patch.object(improvement_collect, "_recent_sessions", return_value=[session]):
            assert improvement_collect.collect_corrections(PK) == 1

    def test_rerunning_the_tick_writes_nothing_new(self, tmp_path):
        """The tick re-reads an overlapping window by design; dedup makes that free."""
        session = _session_with_transcript(tmp_path, "sess-idem", ["USER: that's wrong"])
        with patch.object(improvement_collect, "_recent_sessions", return_value=[session]):
            assert improvement_collect.collect_corrections(PK) == 1
            assert improvement_collect.collect_corrections(PK) == 0

    def test_missing_transcript_is_skipped_not_fatal(self, tmp_path):
        good = _session_with_transcript(tmp_path, "sess-good", ["USER: that's wrong"])
        gone = MagicMock()
        gone.session_id = "sess-gone"
        gone.log_path = str(tmp_path / "does-not-exist.log")
        gone.created_at = datetime.now(UTC)

        with patch.object(improvement_collect, "_recent_sessions", return_value=[gone, good]):
            assert improvement_collect.collect_corrections(PK) == 1

    def test_non_user_lines_are_not_scanned(self, tmp_path):
        """A correction pattern in the agent's own output is not a human correcting it."""
        session = _session_with_transcript(
            tmp_path, "sess-agent", ["ASSISTANT: actually, that's wrong of me"]
        )
        with patch.object(improvement_collect, "_recent_sessions", return_value=[session]):
            assert improvement_collect.collect_corrections(PK) == 0


def _memory(memory_id, content, source="human", reference="", superseded_by=""):
    m = MagicMock()
    m.memory_id = memory_id
    m.content = content
    m.source = source
    m.reference = reference
    m.superseded_by = superseded_by
    m.created_at = datetime.now(UTC)
    return m


class TestCollectInspirations:
    def test_records_tom_sourced_memories(self):
        records = [
            _memory("m1", "read this: https://example.com/paper", reference="https://example.com"),
            _memory("m2", "an agent's own observation", source="agent"),
        ]
        with patch("tools.memory_search.fetch_all_records", return_value=records):
            written = improvement_collect.collect_inspirations(PK)

        assert written == 1
        rows = list(ImprovementEvidence.query.filter(project_key=PK, kind="inspiration"))
        assert [r.source_ref for r in rows] == ["memory:m1"]
        assert rows[0].detail == "https://example.com"

    def test_preserves_the_original_text(self):
        body = "worth looking at Meta Muse 1.3 for cheap tokens"
        with patch("tools.memory_search.fetch_all_records", return_value=[_memory("m3", body)]):
            improvement_collect.collect_inspirations(PK)
        rows = list(ImprovementEvidence.query.filter(project_key=PK, kind="inspiration"))
        assert any(r.text == body for r in rows)

    def test_superseded_memories_are_skipped(self):
        """A consolidated memory is represented by its replacement; both would double-count."""
        records = [_memory("m4", "old idea", superseded_by="m5")]
        with patch("tools.memory_search.fetch_all_records", return_value=records):
            assert improvement_collect.collect_inspirations(PK) == 0

    def test_rerunning_the_tick_writes_nothing_new(self):
        records = [_memory("m6", "an idea")]
        with patch("tools.memory_search.fetch_all_records", return_value=records):
            assert improvement_collect.collect_inspirations(PK) == 1
            assert improvement_collect.collect_inspirations(PK) == 0

    def test_uses_enumeration_not_search(self):
        """search() is a relevance-ranked top-N with no source parameter.

        An adapter built on it would silently under-read, which is the Risk 2
        failure this whole plan exists to prevent. Pin the seam.
        """
        with patch("tools.memory_search.fetch_all_records", return_value=[]) as fetch:
            with patch("tools.memory_search.search") as search:
                improvement_collect.collect_inspirations(PK)
        fetch.assert_called_once_with(PK)
        search.assert_not_called()

    def test_a_broken_memory_backend_is_not_fatal(self):
        with patch("tools.memory_search.fetch_all_records", side_effect=RuntimeError("redis down")):
            assert improvement_collect.collect_inspirations(PK) == 0


class TestCollectExpectationCoverage:
    def test_writes_a_coverage_row_even_with_no_jobs(self):
        with patch("models.job.Job.with_open_expectations", return_value=[]):
            written = improvement_collect.collect_expectation_coverage(PK)
        assert written == 1
        rows = list(ImprovementEvidence.query.filter(project_key=PK, kind="shipped_work"))
        assert any("coverage" in (r.source_ref or "") for r in rows)

    def test_records_a_liveness_row_per_gone_owner(self):
        job = MagicMock()
        job.room_id = f"{PK}|someone"
        job.job_id = "job-1"
        job.goal_is_corrupt.return_value = False
        job.open_expectations.return_value = [
            {"id": "e1", "owner": "session/lane-a", "what": "ship the thing"}
        ]

        with (
            patch("models.job.Job.with_open_expectations", return_value=[job]),
            patch(
                "reflections.expectation_reconciler._owner_is_gone",
                return_value=True,
            ),
        ):
            written = improvement_collect.collect_expectation_coverage(PK)

        assert written == 2  # one liveness row, one coverage row
        rows = list(ImprovementEvidence.query.filter(project_key=PK, kind="owner_liveness"))
        assert rows and rows[0].source_ref == "expectation:job-1:e1"

    def test_a_live_owner_records_no_liveness_row(self):
        job = MagicMock()
        job.room_id = f"{PK}|someone"
        job.job_id = "job-2"
        job.goal_is_corrupt.return_value = False
        job.open_expectations.return_value = [{"id": "e2", "owner": "session/lane-b", "what": "x"}]

        with (
            patch("models.job.Job.with_open_expectations", return_value=[job]),
            patch("reflections.expectation_reconciler._owner_is_gone", return_value=False),
        ):
            written = improvement_collect.collect_expectation_coverage(PK)

        assert written == 1  # coverage row only
        assert not list(ImprovementEvidence.query.filter(project_key=PK, kind="owner_liveness"))

    def test_coverage_text_carries_the_denominator(self):
        """A rate with no denominator is what makes 'fewer rescues' unreadable."""
        job = MagicMock()
        job.room_id = f"{PK}|someone"
        job.job_id = "job-3"
        job.goal_is_corrupt.return_value = False
        job.open_expectations.return_value = [
            {"id": "e3", "owner": "session/lane-c", "what": "x"},
            {"id": "e4", "owner": "session/lane-d", "what": "y"},
        ]

        with (
            patch("models.job.Job.with_open_expectations", return_value=[job]),
            patch("reflections.expectation_reconciler._owner_is_gone", return_value=None),
        ):
            improvement_collect.collect_expectation_coverage(PK)

        rows = [
            r
            for r in ImprovementEvidence.query.filter(project_key=PK, kind="shipped_work")
            if (r.source_ref or "").startswith("coverage:")
        ]
        assert rows
        assert "scanned=2" in (rows[0].detail or "")
        assert "unknown=2" in (rows[0].detail or "")


class TestRunImprovementCollect:
    def test_returns_a_reflection_result_dict(self):
        with (
            patch.object(improvement_collect, "collect_corrections", return_value=2),
            patch.object(improvement_collect, "collect_inspirations", return_value=1),
            patch.object(improvement_collect, "collect_expectation_coverage", return_value=1),
        ):
            result = improvement_collect.run_improvement_collect()

        assert result["status"] == "success"
        assert result["counts"] == {
            "corrections": 2,
            "inspirations": 1,
            "expectation_coverage": 1,
        }
        assert "4 new evidence row" in result["summary"]
        assert result["findings"] == []
        assert isinstance(result["duration"], float)

    def test_one_broken_adapter_degrades_the_tick_rather_than_ending_it(self):
        with (
            patch.object(
                improvement_collect, "collect_corrections", side_effect=RuntimeError("boom")
            ),
            patch.object(improvement_collect, "collect_inspirations", return_value=3),
            patch.object(improvement_collect, "collect_expectation_coverage", return_value=0),
        ):
            result = improvement_collect.run_improvement_collect()

        assert result["status"] == "success"
        assert result["counts"]["corrections"] == 0
        assert result["counts"]["inspirations"] == 3
        assert any("corrections-failed" in f for f in result["findings"])

    def test_all_three_failing_is_an_error(self):
        with (
            patch.object(improvement_collect, "collect_corrections", side_effect=RuntimeError("a")),
            patch.object(
                improvement_collect, "collect_inspirations", side_effect=RuntimeError("b")
            ),
            patch.object(
                improvement_collect, "collect_expectation_coverage", side_effect=RuntimeError("c")
            ),
        ):
            result = improvement_collect.run_improvement_collect()

        assert result["status"] == "error"
        assert len(result["findings"]) == 3


class TestVocabulariesStayInSync:
    def test_adapters_only_emit_declared_kinds(self):
        for kind in ("correction", "inspiration", "shipped_work", "owner_liveness"):
            assert kind in EVIDENCE_KINDS
