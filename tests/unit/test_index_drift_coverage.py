"""Coverage tests for the model-parameterized index-drift registry.

``agent/index_drift.py`` used to hardcode ``AgentSession`` throughout. It is
now driven by :class:`~agent.index_drift.ModelDriftSpec` and a registry
(:data:`~agent.index_drift.DRIFT_COVERED_MODELS`). These tests assert:

  - AgentSession is covered out of the box and behaves identically through the
    generic path (the back-compat entry point still works).
  - Registering a new model literally extends the reconciled set -- so a
    future ``Room``/``Job`` registration is enough to gain drift coverage
    (Risk 3: "drift detection silently narrows"). This is the property the
    plan's Verification rows for Room/Job coverage rely on.

Real Redis via the autouse ``redis_test_db`` fixture (tests/conftest.py) --
no production Redis is touched; all seeded records use a test-scoped
``project_key``.
"""

from __future__ import annotations

import uuid

import pytest

from agent import index_drift
from agent.index_drift import (
    DRIFT_COVERED_MODELS,
    ModelDriftSpec,
    covered_model_names,
    reconcile_all_indexes,
    reconcile_model_index,
    register_drift_model,
)


@pytest.fixture
def restore_registry():
    """Snapshot and restore DRIFT_COVERED_MODELS around a test that mutates it."""
    original = dict(DRIFT_COVERED_MODELS)
    yield
    DRIFT_COVERED_MODELS.clear()
    DRIFT_COVERED_MODELS.update(original)


def test_agentsession_is_covered_by_default():
    """The generalized registry must still cover AgentSession out of the box."""
    assert "AgentSession" in DRIFT_COVERED_MODELS
    assert "AgentSession" in covered_model_names()

    spec = DRIFT_COVERED_MODELS["AgentSession"]
    assert spec.prefix == "AgentSession"
    assert spec.log_prefix == "[index-drift] AgentSession"
    # AgentSession keeps its dedicated, monkeypatchable hash counter.
    assert spec.counter_attr == "_count_agentsession_hashes"


def test_reconcile_all_includes_every_registered_model():
    """reconcile_all_indexes must return a result for each covered model."""
    results = reconcile_all_indexes()
    assert set(results.keys()) == set(covered_model_names())
    for value in results.values():
        assert isinstance(value, tuple)
        assert len(value) == 4  # (hash_count, queryable_count, drifted, truncated)


def test_agentsession_reconciles_cleanly_through_generic_path():
    """A real saved AgentSession is drift-free through reconcile_model_index."""
    from models.agent_session import AgentSession

    session = AgentSession(
        chat_id=f"idx-cov-{uuid.uuid4().hex[:8]}",
        project_key=f"test-index-drift-cov-{uuid.uuid4().hex[:8]}",
        working_dir="/tmp/test-index-drift-cov",
    )
    session.save()
    try:
        hash_count, queryable_count, drifted, truncated = reconcile_model_index(
            DRIFT_COVERED_MODELS["AgentSession"]
        )
        assert hash_count >= 1
        assert queryable_count >= 1
        assert drifted is False
        assert truncated is False
    finally:
        session.delete()


def test_registering_a_model_extends_coverage(restore_registry):
    """Registering a new spec literally widens the reconciled model set.

    This is the anti-narrowing property (Risk 3): a future Room/Job spec gains
    drift coverage purely by calling register_drift_model -- no edits to the
    reconciliation core. A fake in-memory model stands in for Room/Job (which
    do not exist yet).
    """

    class _FakeQuery:
        @staticmethod
        def all():
            return []

    class _FakeModel:
        query = _FakeQuery()

    before = set(covered_model_names())
    assert "FakeCoveredModel" not in before

    register_drift_model(
        ModelDriftSpec(
            name="FakeCoveredModel",
            model_loader=lambda: _FakeModel,
        )
    )

    after = set(covered_model_names())
    assert after == before | {"FakeCoveredModel"}

    # The generic sweep now reconciles it too -- coverage genuinely extended,
    # not just registry bookkeeping. No hashes exist for the fake prefix, so it
    # reconciles clean (0, 0, no drift, not truncated).
    results = reconcile_all_indexes()
    assert "FakeCoveredModel" in results
    assert results["FakeCoveredModel"] == (0, 0, False, False)


@pytest.mark.parametrize(
    "tolerance_env, env_value, expected",
    [
        (None, None, 0),  # no env var -> default_tolerance
        ("SOME_DRIFT_TOL", None, 5),  # env unset -> default_tolerance
        ("SOME_DRIFT_TOL", "9", 9),  # env set -> override
    ],
)
def test_spec_tolerance_resolution(monkeypatch, tolerance_env, env_value, expected):
    """ModelDriftSpec.tolerance() honors its env override, else its default."""
    monkeypatch.delenv("SOME_DRIFT_TOL", raising=False)
    if env_value is not None:
        monkeypatch.setenv(tolerance_env, env_value)

    spec = ModelDriftSpec(
        name="ToleranceProbe",
        model_loader=lambda: None,
        tolerance_env=tolerance_env,
        default_tolerance=5 if tolerance_env else 0,
    )
    assert spec.tolerance() == expected


def test_counter_attr_is_resolved_by_name_at_call_time(monkeypatch, restore_registry):
    """A spec's counter_attr is resolved from module globals at call time.

    This is what lets tests monkeypatch ``_count_agentsession_hashes`` and have
    reconcile honor it -- proving the back-compat patch point survives the
    generalization.
    """
    monkeypatch.setattr(index_drift, "_count_agentsession_hashes", lambda: (0, True))

    class _EmptyQuery:
        @staticmethod
        def all():
            return []

    class _EmptyModel:
        query = _EmptyQuery()

    spec = ModelDriftSpec(
        name="AgentSession",  # reuse the name so counter_attr resolves
        model_loader=lambda: _EmptyModel,
        counter_attr="_count_agentsession_hashes",
    )
    hash_count, queryable_count, drifted, truncated = reconcile_model_index(spec)
    assert hash_count == 0
    assert queryable_count == 0
    assert drifted is False
    assert truncated is False
