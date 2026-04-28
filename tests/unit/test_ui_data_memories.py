"""Tests for the memories data access layer (`ui.data.memories`)."""

from types import SimpleNamespace
from unittest.mock import patch

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


class TestResolveProjectKey:
    def test_explicit_value_wins(self):
        from ui.data.memories import _resolve_project_key

        assert _resolve_project_key("explicit") == "explicit"

    def test_falls_back_to_env(self, monkeypatch):
        from ui.data.memories import _resolve_project_key

        monkeypatch.setenv("VALOR_PROJECT_KEY", "from-env")
        assert _resolve_project_key(None) == "from-env"
        assert _resolve_project_key("") == "from-env"

    def test_falls_back_to_default(self, monkeypatch):
        from config.memory_defaults import DEFAULT_PROJECT_KEY
        from ui.data.memories import _resolve_project_key

        monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)
        assert _resolve_project_key(None) == DEFAULT_PROJECT_KEY


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
