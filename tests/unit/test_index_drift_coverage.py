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

# Every reference goes through the MODULE object, never a collection-time
# ``from agent.index_drift import DRIFT_COVERED_MODELS`` binding. A reload
# elsewhere in the worker rebinds that name to a new dict, and a name bound here
# at collection time would then point at the orphan: the fixture below would
# snapshot and restore the stale dict while the test wrote into the live one,
# silently no-opping the cleanup and stranding a fake model registration for the
# rest of the worker (#2628).
from agent import index_drift


@pytest.fixture
def restore_registry():
    """Snapshot and restore DRIFT_COVERED_MODELS around a test that mutates it."""
    original = dict(index_drift.DRIFT_COVERED_MODELS)
    yield
    index_drift.DRIFT_COVERED_MODELS.clear()
    index_drift.DRIFT_COVERED_MODELS.update(original)


def test_restore_registry_is_not_defeated_by_a_reload():
    """The restore must reach whichever dict is LIVE, even after a reload.

    This is the binding measurement for the second writer of the rotating
    failure set (#2628). ``restore_registry`` used to snapshot and restore the
    collection-time ``from agent.index_drift import DRIFT_COVERED_MODELS``
    binding. An ``importlib.reload`` elsewhere in the worker rebinds that name to
    a brand-new dict, so the fixture then cleaned the ORPHAN while
    ``register_drift_model`` wrote into the live one: the cleanup silently
    no-opped and a fake in-memory model stayed registered for the rest of that
    worker's life, changing what every later drift test saw.
    ``--dist=loadfile`` decides whether the reloader and the victim share a
    worker, which is why the damage landed only some runs.

    The fixture's body is driven directly rather than requested, so the reload
    and the restore are observable inside one test instead of across two.
    """
    import contextlib
    import importlib

    original_registry = index_drift.DRIFT_COVERED_MODELS
    original_contents = dict(original_registry)

    restore = restore_registry.__wrapped__()
    next(restore)  # snapshot phase
    try:
        importlib.reload(index_drift)
        index_drift.register_drift_model(
            index_drift.ModelDriftSpec(
                name="FakeReloadProbe",
                model_loader=lambda: None,
            )
        )
        with contextlib.suppress(StopIteration):
            next(restore)  # restore phase

        assert "FakeReloadProbe" not in index_drift.covered_model_names(), (
            "the restore cleaned an orphaned registry while the registration "
            "landed in the live one, stranding a fake model for the rest of the "
            "worker"
        )
    finally:
        index_drift.DRIFT_COVERED_MODELS = original_registry
        original_registry.clear()
        original_registry.update(original_contents)


def test_agentsession_is_covered_by_default():
    """The generalized registry must still cover AgentSession out of the box."""
    assert "AgentSession" in index_drift.DRIFT_COVERED_MODELS
    assert "AgentSession" in index_drift.covered_model_names()

    spec = index_drift.DRIFT_COVERED_MODELS["AgentSession"]
    assert spec.prefix == "AgentSession"
    assert spec.log_prefix == "[index-drift] AgentSession"
    # AgentSession keeps its dedicated, monkeypatchable hash counter.
    assert spec.counter_attr == "_count_agentsession_hashes"


def test_reconcile_all_includes_every_registered_model():
    """reconcile_all_indexes must return a result for each covered model."""
    results = index_drift.reconcile_all_indexes()
    assert set(results.keys()) == set(index_drift.covered_model_names())
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
        hash_count, queryable_count, drifted, truncated = index_drift.reconcile_model_index(
            index_drift.DRIFT_COVERED_MODELS["AgentSession"]
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

    before = set(index_drift.covered_model_names())
    assert "FakeCoveredModel" not in before

    index_drift.register_drift_model(
        index_drift.ModelDriftSpec(
            name="FakeCoveredModel",
            model_loader=lambda: _FakeModel,
        )
    )

    after = set(index_drift.covered_model_names())
    assert after == before | {"FakeCoveredModel"}

    # The generic sweep now reconciles it too -- coverage genuinely extended,
    # not just registry bookkeeping. No hashes exist for the fake prefix, so it
    # reconciles clean (0, 0, no drift, not truncated).
    results = index_drift.reconcile_all_indexes()
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

    spec = index_drift.ModelDriftSpec(
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

    spec = index_drift.ModelDriftSpec(
        name="AgentSession",  # reuse the name so counter_attr resolves
        model_loader=lambda: _EmptyModel,
        counter_attr="_count_agentsession_hashes",
    )
    hash_count, queryable_count, drifted, truncated = index_drift.reconcile_model_index(spec)
    assert hash_count == 0
    assert queryable_count == 0
    assert drifted is False
    assert truncated is False
