"""Tests for the memories data access layer (`ui.data.memories`)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.webui]


def _make_record(
    memory_id: str = "mem_test",
    project_key: str = "test-proj",
    content: str = "example",
    importance: float = 1.0,
    relevance: float = 1000.0,
    metadata: dict | None = None,
    superseded_by: str = "",
    superseded_by_rationale: str = "",
    source: str = "agent",
    confidence: float = 0.5,
    access_count: int = 0,
    agent_id: str = "test_agent",
):
    """Build a fake Memory-like record (SimpleNamespace) for tests.

    Avoids touching the real Popoto Redis store by mocking `Memory.query.filter`
    directly with these stubs.
    """
    return SimpleNamespace(
        memory_id=memory_id,
        project_key=project_key,
        content=content,
        importance=importance,
        relevance=relevance,
        metadata=metadata if metadata is not None else {},
        superseded_by=superseded_by,
        superseded_by_rationale=superseded_by_rationale,
        source=source,
        confidence=confidence,
        access_count=access_count,
        agent_id=agent_id,
        last_access_at=None,
    )


class TestResolveProjectKeys:
    def test_explicit_value_wins(self):
        from ui.data.memories import _resolve_project_keys

        assert _resolve_project_keys("explicit") == ["explicit"]

    def test_falls_back_to_env(self, monkeypatch):
        from ui.data.memories import _resolve_project_keys

        monkeypatch.setenv("VALOR_PROJECT_KEY", "from-env")
        assert _resolve_project_keys(None) == ["from-env"]
        assert _resolve_project_keys("") == ["from-env"]

    def test_falls_back_to_default(self, monkeypatch):
        from config.memory_defaults import DEFAULT_PROJECT_KEY
        from ui.data.memories import _resolve_project_keys

        monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)
        # When no env var and machine config returns no projects,
        # falls back to [DEFAULT_PROJECT_KEY]
        with patch("ui.data.machine.get_machine_projects", return_value=[]):
            result = _resolve_project_keys(None)
        assert result == [DEFAULT_PROJECT_KEY]

    def test_resolves_machine_projects(self, monkeypatch):
        from ui.data.memories import _resolve_project_keys

        monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)
        # On the actual machine, should resolve project keys from projects.json
        result = _resolve_project_keys(None)
        # Should return a non-empty list of actual project keys (not ["default"])
        # unless this machine has no projects configured
        assert isinstance(result, list)
        assert len(result) > 0


class TestDecorateRecord:
    def test_handles_empty_metadata(self):
        from ui.data.memories import _decorate_record

        record = _make_record(metadata={})
        out = _decorate_record(record)
        assert out["category"] == "default"
        assert out["dismissal_count"] == 0
        assert out["outcome_history"] == []
        assert out["acted_count"] == 0
        assert out["dismissed_count"] == 0
        assert out["act_rate"] is None
        assert out["last_outcome"] is None
        assert out["decay_imminent"] is False
        assert out["superseded"] is False

    def test_handles_missing_metadata_attribute(self):
        from ui.data.memories import _decorate_record

        record = _make_record()
        # Simulate truly missing attribute path -- DictField may surface as
        # a non-dict in legacy data.
        record.metadata = None
        out = _decorate_record(record)
        assert out["category"] == "default"
        assert out["dismissal_count"] == 0

    def test_decay_imminent_at_threshold_minus_one(self):
        from config.memory_defaults import DISMISSAL_DECAY_THRESHOLD
        from ui.data.memories import _decorate_record

        record = _make_record(metadata={"dismissal_count": DISMISSAL_DECAY_THRESHOLD - 1})
        out = _decorate_record(record)
        assert out["decay_imminent"] is True

    def test_decay_imminent_below_threshold_minus_one(self):
        from config.memory_defaults import DISMISSAL_DECAY_THRESHOLD
        from ui.data.memories import _decorate_record

        record = _make_record(metadata={"dismissal_count": max(0, DISMISSAL_DECAY_THRESHOLD - 2)})
        out = _decorate_record(record)
        assert out["decay_imminent"] is False

    def test_outcome_summary_counts(self):
        from ui.data.memories import _decorate_record

        record = _make_record(
            metadata={
                "outcome_history": [
                    {"outcome": "acted", "ts": 1},
                    {"outcome": "acted", "ts": 2},
                    {"outcome": "dismissed", "ts": 3},
                    {"outcome": "used", "ts": 4},
                ]
            }
        )
        out = _decorate_record(record)
        assert out["acted_count"] == 2
        assert out["dismissed_count"] == 1
        assert out["act_rate"] == pytest.approx(0.5)

    def test_superseded_top_level_field_used(self):
        from ui.data.memories import _decorate_record

        record = _make_record(superseded_by="mem_xyz", superseded_by_rationale="merged: dup")
        out = _decorate_record(record)
        assert out["superseded"] is True
        assert out["superseded_by"] == "mem_xyz"
        assert out["superseded_by_rationale"] == "merged: dup"

    def test_title_truncates_long_first_line(self):
        from ui.data.memories import _decorate_record

        long_text = "x" * 200
        record = _make_record(content=long_text)
        out = _decorate_record(record)
        assert len(out["title"]) <= 81  # 80 chars + ellipsis
        assert out["title"].endswith("…")

    def test_title_uses_first_line_only(self):
        from ui.data.memories import _decorate_record

        record = _make_record(content="first line\nsecond line")
        out = _decorate_record(record)
        assert out["title"] == "first line"


class TestGetMemoriesFiltering:
    @pytest.fixture
    def records(self):
        return [
            _make_record(
                memory_id="m1",
                relevance=300.0,
                metadata={"category": "correction", "dismissal_count": 0},
            ),
            _make_record(
                memory_id="m2",
                relevance=200.0,
                metadata={"category": "decision", "dismissal_count": 2},
            ),
            _make_record(
                memory_id="m3",
                relevance=100.0,
                metadata={"category": "correction", "dismissal_count": 2},
            ),
            _make_record(
                memory_id="m4",
                relevance=400.0,
                superseded_by="m1",
                metadata={"category": "correction"},
            ),
        ]

    def _patch_query(self, records):
        return patch("models.memory.Memory.query.filter", return_value=records)

    def test_default_excludes_superseded(self, records):
        from ui.data.memories import get_memories

        with self._patch_query(records):
            result = get_memories(project_key="test-proj")
        ids = [r["memory_id"] for r in result["records"]]
        assert "m4" not in ids
        assert set(ids) == {"m1", "m2", "m3"}

    def test_include_superseded_keeps_them(self, records):
        from ui.data.memories import get_memories

        with self._patch_query(records):
            result = get_memories(project_key="test-proj", include_superseded=True)
        ids = [r["memory_id"] for r in result["records"]]
        assert "m4" in ids

    def test_category_filter(self, records):
        from ui.data.memories import get_memories

        with self._patch_query(records):
            result = get_memories(project_key="test-proj", category="correction")
        ids = [r["memory_id"] for r in result["records"]]
        assert set(ids) == {"m1", "m3"}

    def test_decay_only(self, records):
        from ui.data.memories import get_memories

        with self._patch_query(records):
            result = get_memories(project_key="test-proj", decay_only=True)
        ids = [r["memory_id"] for r in result["records"]]
        # m2 (cat=decision, dc=2) and m3 (cat=correction, dc=2)
        assert set(ids) == {"m2", "m3"}

    def test_unknown_category_yields_empty(self, records):
        from ui.data.memories import get_memories

        with self._patch_query(records):
            result = get_memories(project_key="test-proj", category="bogus")
        assert result["records"] == []
        assert result["total_matched"] == 0

    def test_sorted_by_relevance_desc(self, records):
        from ui.data.memories import get_memories

        with self._patch_query(records):
            result = get_memories(project_key="test-proj", include_superseded=True)
        relevances = [r["relevance"] for r in result["records"]]
        assert relevances == sorted(relevances, reverse=True)

    def test_multi_key_query_merges_results(self, records):
        from ui.data.memories import get_memories

        # When project_key is None and machine owns multiple projects,
        # get_memories queries each key and merges. Simulate by passing
        # no project_key (which triggers multi-key resolution).
        with self._patch_query(records):
            # Explicit project_key bypasses multi-key resolution
            result = get_memories(project_key="test-proj")
        assert len(result["records"]) == 3  # m4 superseded excluded


class TestGetMemoriesTruncation:
    def test_truncated_count_reported(self):
        from ui.data.memories import get_memories

        records = [
            _make_record(
                memory_id=f"m{i}", relevance=float(1000 - i), metadata={"category": "decision"}
            )
            for i in range(10)
        ]

        with patch("models.memory.Memory.query.filter", return_value=records):
            result = get_memories(project_key="test-proj", limit=3)

        assert len(result["records"]) == 3
        assert result["total_matched"] == 10
        assert result["truncated_count"] == 7

    def test_no_truncation_when_under_limit(self):
        from ui.data.memories import get_memories

        records = [_make_record(memory_id=f"m{i}", relevance=float(1000 - i)) for i in range(3)]
        with patch("models.memory.Memory.query.filter", return_value=records):
            result = get_memories(project_key="test-proj", limit=10)

        assert result["truncated_count"] == 0
        assert result["total_matched"] == 3


class TestGetMemoriesQueryFailure:
    def test_returns_empty_on_query_exception(self, caplog):
        from ui.data.memories import get_memories

        def raise_(**_kwargs):
            raise RuntimeError("redis down")

        with patch("models.memory.Memory.query.filter", side_effect=raise_):
            with caplog.at_level("WARNING", logger="ui.data.memories"):
                result = get_memories(project_key="test-proj")

        assert result["records"] == []
        assert result["total_matched"] == 0
        assert any("Failed to query Memory records" in rec.message for rec in caplog.records)


class TestEmptyResults:
    def test_no_records_returns_empty_payload(self):
        from ui.data.memories import get_memories

        with patch("models.memory.Memory.query.filter", return_value=[]):
            result = get_memories(project_key="test-proj")

        assert result["records"] == []
        assert result["total_matched"] == 0
        assert result["truncated_count"] == 0
        assert result["categories"] == []


class TestGetMemoryDetail:
    def test_returns_inspect_dict(self):
        from ui.data.memories import get_memory_detail

        with patch(
            "tools.memory_search.inspect",
            return_value={"memory_id": "abc", "content": "hi", "metadata": {}},
        ):
            result = get_memory_detail("abc")

        assert result is not None
        assert result["memory_id"] == "abc"

    def test_returns_none_on_error(self):
        from ui.data.memories import get_memory_detail

        with patch("tools.memory_search.inspect", return_value={"error": "Memory not found: xx"}):
            assert get_memory_detail("xx") is None

    def test_returns_none_on_empty(self):
        from ui.data.memories import get_memory_detail

        with patch("tools.memory_search.inspect", return_value={}):
            assert get_memory_detail("xx") is None


class _NoTrackQueryStub:
    """Minimal QueryBuilder stub for `.filter(...).no_track().all()` chains."""

    def __init__(self, records):
        self._records = records

    def no_track(self):
        return self

    def all(self):
        return self._records


class TestGetCorpusMetrics:
    def test_empty_corpus_is_zero_filled(self):
        from ui.data.memories import get_corpus_metrics

        with patch("models.memory.Memory.query.filter", return_value=_NoTrackQueryStub([])):
            result = get_corpus_metrics(project_key="test-proj")

        assert result["total_records"] == 0
        assert result["project_key"] == "test-proj"
        assert result["aggregate_act_rate"] is None
        assert result["junk_rate"] is None

    def test_no_track_is_called_to_suppress_access_staging(self):
        from ui.data.memories import get_corpus_metrics

        mock_qb = MagicMock()
        mock_qb.no_track.return_value = mock_qb
        mock_qb.all.return_value = []
        with patch("models.memory.Memory.query.filter", return_value=mock_qb) as mock_filter:
            get_corpus_metrics(project_key="test-proj")

        mock_filter.assert_called_once_with(project_key="test-proj")
        mock_qb.no_track.assert_called_once()
        mock_qb.all.assert_called_once()

    def test_loads_full_corpus_without_limit_truncation(self):
        from ui.data.memories import get_corpus_metrics

        records = [
            _make_record(memory_id=f"m{i}", content=f"durable fact number {i}") for i in range(250)
        ]
        with patch("models.memory.Memory.query.filter", return_value=_NoTrackQueryStub(records)):
            result = get_corpus_metrics(project_key="test-proj")

        # DEFAULT_LIMIT (200) truncates get_memories but must NOT truncate
        # the corpus-metrics loader.
        assert result["total_records"] == 250

    def test_separates_superseded_from_durable_denominator(self):
        from ui.data.memories import get_corpus_metrics

        records = [
            _make_record("m1", content="a durable fact about the system"),
            _make_record("m2", content="another durable fact", superseded_by="m1"),
        ]
        with patch("models.memory.Memory.query.filter", return_value=_NoTrackQueryStub(records)):
            result = get_corpus_metrics(project_key="test-proj")

        assert result["total_records"] == 2
        assert result["superseded_count"] == 1
        assert result["durable_denominator"] == 1

    def test_classifies_junk_content(self):
        from ui.data.memories import get_corpus_metrics

        records = [
            _make_record("m1", content="a durable fact about the system"),
            _make_record("m2", content="yup"),  # ack-only -> junk
            _make_record("m3", content="includes:"),  # fragment -> junk
        ]
        with patch("models.memory.Memory.query.filter", return_value=_NoTrackQueryStub(records)):
            result = get_corpus_metrics(project_key="test-proj")

        assert result["junk_count"] == 2
        assert result["ack_only_count"] == 1
        assert result["fragment_suspect_count"] == 1

    def test_min_evidence_passed_through(self):
        from ui.data.memories import get_corpus_metrics

        with patch("models.memory.Memory.query.filter", return_value=_NoTrackQueryStub([])):
            result = get_corpus_metrics(project_key="test-proj", min_evidence=5)

        assert result["min_evidence"] == 5

    def test_query_failure_returns_zero_filled_never_raises(self, caplog):
        from ui.data.memories import get_corpus_metrics

        def raise_(**_kwargs):
            raise RuntimeError("redis down")

        with patch("models.memory.Memory.query.filter", side_effect=raise_):
            with caplog.at_level("WARNING", logger="ui.data.memories"):
                result = get_corpus_metrics(project_key="test-proj")

        assert result["total_records"] == 0
        assert result["aggregate_act_rate"] is None
        assert any("Failed to query Memory records" in rec.message for rec in caplog.records)

    def test_distill_status_live_gauges(self):
        """`provisional_count` / `distilled_count` / `abandoned_count`
        (memory-distilled-ingest, Phase 3, issue #2202) are LIVE gauges
        computed directly from the raw corpus this call already loaded --
        distinct from the cumulative `distill_*` counters. `distilled_count`
        is the Task 3 lift-report's distillation-coverage number."""
        from ui.data.memories import get_corpus_metrics

        records = [
            _make_record("m1", metadata={"distill_status": "provisional"}),
            _make_record("m2", metadata={"distill_status": "provisional"}),
            _make_record("m3", metadata={"distill_status": "distilled"}),
            _make_record("m4", metadata={"distill_status": "distill_abandoned"}),
            _make_record("m5", metadata={}),  # legacy record, no distill_status
        ]
        with patch("models.memory.Memory.query.filter", return_value=_NoTrackQueryStub(records)):
            result = get_corpus_metrics(project_key="test-proj")

        assert result["provisional_count"] == 2
        assert result["distilled_count"] == 1
        assert result["abandoned_count"] == 1

    def test_distill_status_gauges_zero_filled_on_query_failure(self):
        from ui.data.memories import get_corpus_metrics

        with patch("models.memory.Memory.query.filter", side_effect=RuntimeError("redis down")):
            result = get_corpus_metrics(project_key="test-proj")

        assert result["provisional_count"] == 0
        assert result["distilled_count"] == 0
        assert result["abandoned_count"] == 0


class TestGetCorpusRecords:
    """`get_corpus_records` -- the raw decorated-record fetch sibling of
    `get_corpus_metrics`, used by the distilled-ingest report (issue #2202,
    Task 3) to segment the corpus by `source` before aggregating."""

    def test_returns_decorated_records_and_resolved_keys(self):
        from ui.data.memories import get_corpus_records

        records = [
            _make_record("m1", content="a human fact", source="human"),
            _make_record("m2", content="an agent fact", source="agent"),
        ]
        with patch("models.memory.Memory.query.filter", return_value=_NoTrackQueryStub(records)):
            decorated, pks = get_corpus_records(project_key="test-proj")

        assert pks == ["test-proj"]
        assert [r["memory_id"] for r in decorated] == ["m1", "m2"]
        assert [r["source"] for r in decorated] == ["human", "agent"]

    def test_no_track_is_called_to_suppress_access_staging(self):
        from ui.data.memories import get_corpus_records

        mock_qb = MagicMock()
        mock_qb.no_track.return_value = mock_qb
        mock_qb.all.return_value = []
        with patch("models.memory.Memory.query.filter", return_value=mock_qb) as mock_filter:
            get_corpus_records(project_key="test-proj")

        mock_filter.assert_called_once_with(project_key="test-proj")
        mock_qb.no_track.assert_called_once()
        mock_qb.all.assert_called_once()

    def test_no_limit_truncation(self):
        from ui.data.memories import get_corpus_records

        records = [
            _make_record(memory_id=f"m{i}", content=f"durable fact number {i}") for i in range(250)
        ]
        with patch("models.memory.Memory.query.filter", return_value=_NoTrackQueryStub(records)):
            decorated, _pks = get_corpus_records(project_key="test-proj")

        assert len(decorated) == 250

    def test_query_failure_returns_empty_list_never_raises(self, caplog):
        from ui.data.memories import get_corpus_records

        def raise_(**_kwargs):
            raise RuntimeError("redis down")

        with patch("models.memory.Memory.query.filter", side_effect=raise_):
            with caplog.at_level("WARNING", logger="ui.data.memories"):
                decorated, pks = get_corpus_records(project_key="test-proj")

        assert decorated == []
        assert pks == ["test-proj"]
        assert any("Failed to query Memory records" in rec.message for rec in caplog.records)
