"""Integration tests for the dashboard /memories route."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration, pytest.mark.webui]


@pytest.fixture
def client():
    from ui.app import create_app

    app = create_app()
    return TestClient(app)


def _stub_record(memory_id="m1", category="correction", **overrides):
    base = dict(
        memory_id=memory_id,
        project_key="test-proj",
        content=f"content for {memory_id}",
        importance=1.0,
        relevance=1000.0 - hash(memory_id) % 100,
        metadata={"category": category, "outcome_history": []},
        superseded_by="",
        superseded_by_rationale="",
        source="agent",
        confidence=0.5,
        access_count=0,
        agent_id="test_agent",
        last_access_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestMemoriesPage:
    def test_route_registered(self):
        from ui.app import create_app

        app = create_app()
        paths = {r.path for r in app.routes}
        assert "/memories" in paths
        assert "/_partials/memories/" in paths

    def test_get_returns_200_with_no_records(self, client):
        with patch("models.memory.Memory.query.filter", return_value=[]):
            resp = client.get("/memories")
        assert resp.status_code == 200
        assert "Memories" in resp.text
        # Empty-state hint points at the CLI.
        assert "tools.memory_search" in resp.text

    def test_get_renders_records(self, client):
        records = [
            _stub_record("m1", category="correction"),
            _stub_record("m2", category="decision"),
        ]
        with patch("models.memory.Memory.query.filter", return_value=records):
            resp = client.get("/memories")
        assert resp.status_code == 200
        # Records are grouped by category in the body.
        assert "correction" in resp.text
        assert "decision" in resp.text
        # First-line title should render.
        assert "content for m1" in resp.text

    def test_category_query_param_filters(self, client):
        records = [
            _stub_record("m1", category="correction"),
            _stub_record("m2", category="decision"),
        ]
        with patch("models.memory.Memory.query.filter", return_value=records):
            resp = client.get("/memories?category=correction")
        assert resp.status_code == 200
        assert "content for m1" in resp.text
        assert "content for m2" not in resp.text

    def test_unknown_category_renders_empty_state(self, client):
        records = [_stub_record("m1", category="correction")]
        with patch("models.memory.Memory.query.filter", return_value=records):
            resp = client.get("/memories?category=bogus")
        assert resp.status_code == 200
        assert "No memories match this filter" in resp.text

    def test_decay_query_param_works(self, client):
        from config.memory_defaults import DISMISSAL_DECAY_THRESHOLD

        records = [
            _stub_record(
                "m1",
                category="correction",
                metadata={"category": "correction", "dismissal_count": 0, "outcome_history": []},
            ),
            _stub_record(
                "m2",
                category="correction",
                metadata={
                    "category": "correction",
                    "dismissal_count": DISMISSAL_DECAY_THRESHOLD - 1,
                    "outcome_history": [],
                },
            ),
        ]
        with patch("models.memory.Memory.query.filter", return_value=records):
            resp = client.get("/memories?decay=true")
        assert resp.status_code == 200
        assert "content for m2" in resp.text
        assert "content for m1" not in resp.text

    def test_show_superseded_default_off(self, client):
        records = [
            _stub_record("m1", category="correction"),
            _stub_record(
                "m2",
                category="correction",
                superseded_by="m1",
                superseded_by_rationale="dup",
            ),
        ]
        with patch("models.memory.Memory.query.filter", return_value=records):
            resp = client.get("/memories")
        assert "content for m1" in resp.text
        assert "content for m2" not in resp.text

    def test_show_superseded_on(self, client):
        records = [
            _stub_record("m1", category="correction"),
            _stub_record(
                "m2",
                category="correction",
                superseded_by="m1",
                superseded_by_rationale="dup",
            ),
        ]
        with patch("models.memory.Memory.query.filter", return_value=records):
            resp = client.get("/memories?show_superseded=true")
        assert "content for m1" in resp.text
        assert "content for m2" in resp.text


class TestMemoriesPartial:
    def test_partial_returns_html_fragment(self, client):
        with patch("models.memory.Memory.query.filter", return_value=[]):
            resp = client.get("/_partials/memories/")
        assert resp.status_code == 200
        # Partial should NOT include a <html> wrapper.
        assert "<html" not in resp.text.lower()
        # But should include the empty state.
        assert "No memories match" in resp.text

    def test_partial_propagates_query_params(self, client):
        records = [
            _stub_record("m1", category="correction"),
            _stub_record("m2", category="decision"),
        ]
        with patch("models.memory.Memory.query.filter", return_value=records):
            resp = client.get("/_partials/memories/?category=correction")
        assert resp.status_code == 200
        assert "content for m1" in resp.text
        assert "content for m2" not in resp.text


class TestErrorHandling:
    def test_data_layer_failure_does_not_500(self, client):
        # The data layer swallows query exceptions and returns an empty
        # payload — the page still renders the empty state, never a traceback.
        def raise_(**_kwargs):
            raise RuntimeError("redis down")

        with patch("models.memory.Memory.query.filter", side_effect=raise_):
            resp = client.get("/memories")
        assert resp.status_code == 200
        assert "No memories match" in resp.text


class TestMemoriesMetricsJson:
    def test_route_registered(self):
        from ui.app import create_app

        app = create_app()
        paths = {r.path for r in app.routes}
        assert "/memories/metrics.json" in paths

    def test_get_returns_200_with_expected_keys_on_empty_corpus(self, client):
        with patch("models.memory.Memory.query.filter") as mock_filter:
            mock_filter.return_value.no_track.return_value.all.return_value = []
            resp = client.get("/memories/metrics.json")
        assert resp.status_code == 200
        body = resp.json()
        # Top-level keys from tools.memory_eval.ingest_quality.compute_corpus_metrics.
        for key in (
            "total_records",
            "superseded_count",
            "durable_denominator",
            "min_evidence",
            "act_rate_definition",
            "aggregate_act_rate",
            "aggregate_dismissal_rate",
            "junk_count",
            "junk_rate",
            "ack_only_count",
            "fragment_suspect_count",
            "source_counts",
            "importance_histogram",
            "confidence_histogram",
            "decay_imminent_count",
            "never_injected_count",
            # Issue #2201 write-gate counters.
            "gate_rejected_ack",
            "gate_rejected_fragment",
            "gate_rejected_short",
            "gate_fallback_dropped",
        ):
            assert key in body
        assert body["total_records"] == 0
        assert body["aggregate_act_rate"] is None
        # Zero-filled on an empty corpus (no gate rejections have occurred).
        assert body["gate_rejected_ack"] == 0
        assert body["gate_rejected_fragment"] == 0
        assert body["gate_rejected_short"] == 0
        assert body["gate_fallback_dropped"] == 0

    def test_get_reflects_records(self):
        from ui.app import create_app

        records = [
            _stub_record("m1", category="correction", content="a durable fact"),
            _stub_record("m2", category="decision", content="yup"),
        ]
        app = create_app()
        client = TestClient(app)
        with patch("models.memory.Memory.query.filter") as mock_filter:
            mock_filter.return_value.no_track.return_value.all.return_value = records
            resp = client.get("/memories/metrics.json")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_records"] == 2
        assert body["junk_count"] == 1  # "yup" is ack-only junk

    def test_query_params_are_accepted(self, client):
        with patch("models.memory.Memory.query.filter") as mock_filter:
            mock_filter.return_value.no_track.return_value.all.return_value = []
            resp = client.get("/memories/metrics.json?project_key=other-proj&min_evidence=5")
        assert resp.status_code == 200
        body = resp.json()
        assert body["min_evidence"] == 5
        assert body["project_key"] == "other-proj"

    def test_never_500s_on_query_failure(self, client):
        def raise_(**_kwargs):
            raise RuntimeError("redis down")

        with patch("models.memory.Memory.query.filter", side_effect=raise_):
            resp = client.get("/memories/metrics.json")
        assert resp.status_code == 200
        assert resp.json()["total_records"] == 0

    def test_gate_counters_zero_fill_and_never_500_when_redis_down(self, client):
        """`_sum_gate_counter` (issue #2201) is best-effort: a raising Redis
        GET must never surface as a 500, and the four gate_* fields must
        still be present, zero-filled."""

        class _RaisingRedis:
            def get(self, *_args, **_kwargs):
                raise ConnectionError("redis unreachable")

        with (
            patch("models.memory.Memory.query.filter") as mock_filter,
            patch("popoto.redis_db.POPOTO_REDIS_DB", _RaisingRedis()),
        ):
            mock_filter.return_value.no_track.return_value.all.return_value = []
            resp = client.get("/memories/metrics.json")

        assert resp.status_code == 200
        body = resp.json()
        assert body["gate_rejected_ack"] == 0
        assert body["gate_rejected_fragment"] == 0
        assert body["gate_rejected_short"] == 0
        assert body["gate_fallback_dropped"] == 0

    def test_min_evidence_zero_is_rejected_not_500(self, client):
        # min_evidence=0 previously reached compute_corpus_metrics and raised
        # ZeroDivisionError on a record with evidence_i == 0 (0 >= 0 passed
        # the gate). The route now constrains min_evidence with
        # Query(2, ge=1), so an out-of-range value is rejected by FastAPI's
        # own validation (422) before it ever reaches app code -- never a 500.
        with patch("models.memory.Memory.query.filter") as mock_filter:
            mock_filter.return_value.no_track.return_value.all.return_value = []
            resp = client.get("/memories/metrics.json?min_evidence=0")
        assert resp.status_code == 422
        assert resp.status_code != 500


class TestIndexLink:
    def test_index_links_to_memories(self, client):
        # Even with no data, the dashboard root should point at /memories.
        with (
            patch("ui.data.sdlc.get_all_sessions", return_value=[]),
            patch("ui.data.reflections.get_grouped_reflections", return_value=[]),
            # Patch both the source hub (covers function-level importers like
            # ui.data.memories / ui.app) and ui.data.machine's module-level
            # binding of the same function.
            patch("config.machine.get_machine_name", return_value="test-host"),
            patch("ui.data.machine.get_machine_name", return_value="test-host"),
            patch("ui.data.machine.get_machine_projects", return_value=[]),
        ):
            resp = client.get("/")
        assert resp.status_code == 200
        assert 'href="/memories"' in resp.text
