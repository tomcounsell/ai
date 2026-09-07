"""Schema-gate tests for the eight Improvement* models (#3177).

The schema gate's rule is a cardinality rule: never index a pid, uuid,
timestamp, or monotonic counter. These tests enforce it structurally — by
enumerating each model's ``IndexedField``s and checking every one against a
declared, bounded vocabulary — rather than by trusting the docstrings.

They also pin the two things a reader of ``docs/plans/recursive-self-improvement.md``
would want to verify without reading eight modules: the partition and recency
shape every record shares, and the TTL decision each model made.

Uses the autouse ``redis_test_db`` fixture (tests/conftest.py), which claims a
per-worker test DB — production Redis is never touched. Seeded records use a
test-scoped ``project_key``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from popoto import AutoKeyField, IndexedField, IntField, KeyField, SortedField

from models import (
    ImprovementCase,
    ImprovementCharter,
    ImprovementEvaluation,
    ImprovementEvidence,
    ImprovementExperiment,
    ImprovementInvestigation,
    ImprovementModelRevision,
    ImprovementRelease,
)
from models.improvement_case import CASE_PRIORITIES, CASE_STATES
from models.improvement_charter import CHARTER_STATES
from models.improvement_evaluation import EVALUATION_STATES, EVALUATION_VERDICTS
from models.improvement_evidence import EVIDENCE_CLASSIFICATIONS, EVIDENCE_KINDS
from models.improvement_experiment import EXPERIMENT_STATES
from models.improvement_investigation import INVESTIGATION_KINDS, INVESTIGATION_STATES
from models.improvement_model_revision import MODEL_REVISION_STATES
from models.improvement_release import RELEASE_STATES

PK = "test-3177-models"

#: Every model, with the bounded vocabulary each of its IndexedFields may hold.
#: A new IndexedField with no declared vocabulary fails
#: ``test_every_indexed_field_has_a_declared_vocabulary`` — that is the gate.
INDEXED_VOCABULARIES: dict[type, dict[str, tuple[str, ...]]] = {
    ImprovementCharter: {"state": CHARTER_STATES},
    ImprovementEvidence: {
        "kind": EVIDENCE_KINDS,
        "classification": EVIDENCE_CLASSIFICATIONS,
    },
    ImprovementModelRevision: {"state": MODEL_REVISION_STATES},
    ImprovementCase: {"state": CASE_STATES, "priority": CASE_PRIORITIES},
    ImprovementInvestigation: {
        "kind": INVESTIGATION_KINDS,
        "state": INVESTIGATION_STATES,
    },
    ImprovementExperiment: {"state": EXPERIMENT_STATES},
    ImprovementEvaluation: {
        "state": EVALUATION_STATES,
        "verdict": EVALUATION_VERDICTS,
    },
    ImprovementRelease: {"state": RELEASE_STATES},
}

ALL_MODELS = tuple(INDEXED_VOCABULARIES)

#: The models that must expire, and the TTL each one committed to.
#: Evidence and investigations follow ReflectionRun's 30-day horizon.
EXPIRING_MODELS: dict[type, int] = {
    ImprovementEvidence: 86400 * 30,
    ImprovementInvestigation: 86400 * 30,
}

#: The models that must be immortal, like Job. An evaluation, a release, or a
#: case that expired would strand the lineage that makes a verdict auditable.
IMMORTAL_MODELS = (
    ImprovementCharter,
    ImprovementModelRevision,
    ImprovementCase,
    ImprovementExperiment,
    ImprovementEvaluation,
    ImprovementRelease,
)

#: Cardinality tripwire: field names that must never carry an index, whatever
#: model they appear on. These are the shapes the schema gate exists to stop.
FORBIDDEN_INDEX_NAMES = (
    "id",
    "revision",
    "version",
    "created_at",
    "updated_at",
    "trials",
    "case_id",
    "experiment_id",
    "evaluation_id",
    "job_id",
    "source_session_id",
    "source_ref",
    "contract_digest",
    "model_revision_id",
    "supersedes_id",
)


def _fields(model: type) -> dict:
    return dict(model._meta.fields)


def _indexed_names(model: type) -> set[str]:
    return {name for name, f in _fields(model).items() if isinstance(f, IndexedField)}


class TestSchemaShape:
    """The partition + recency shape every improvement record shares."""

    @pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
    def test_has_autokey_id_and_project_key(self, model):
        fields = _fields(model)
        assert isinstance(fields["id"], AutoKeyField)
        assert isinstance(fields["project_key"], KeyField)

    @pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
    def test_recency_sort_is_partitioned_by_project_key(self, model):
        created_at = _fields(model)["created_at"]
        assert isinstance(created_at, SortedField)
        # A recency sort that is not partitioned is a single unbounded sorted
        # set shared by every project — the read this partition exists to avoid.
        # popoto normalizes partition_by to a tuple.
        partition = getattr(created_at, "partition_by", None)
        partition = (partition,) if isinstance(partition, str) else tuple(partition or ())
        assert partition == ("project_key",)

    @pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
    def test_is_a_flat_module_not_a_subpackage(self, model):
        # models/ has no sub-packages; the plan's recon revised
        # models/improvement/ to flat modules for exactly that reason.
        assert model.__module__.startswith("models.improvement_")
        assert model.__module__.count(".") == 1, f"{model.__name__} is not a flat module"


class TestIndexCardinalityGate:
    """Every index is low-cardinality, and provably so."""

    @pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
    def test_every_indexed_field_has_a_declared_vocabulary(self, model):
        declared = set(INDEXED_VOCABULARIES[model])
        assert _indexed_names(model) == declared, (
            f"{model.__name__} indexes {_indexed_names(model)} but this test declares "
            f"{declared}. A new IndexedField needs a bounded vocabulary here, or it "
            "does not belong on an index."
        )

    @pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
    def test_declared_vocabularies_are_small(self, model):
        for name, vocabulary in INDEXED_VOCABULARIES[model].items():
            assert 2 <= len(vocabulary) <= 8, (
                f"{model.__name__}.{name} has {len(vocabulary)} values; an index set "
                "per value stops being cheap well before this."
            )
            assert len(set(vocabulary)) == len(vocabulary), "duplicate value in vocabulary"

    @pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
    def test_unbounded_fields_are_never_indexed(self, model):
        indexed = _indexed_names(model)
        for forbidden in FORBIDDEN_INDEX_NAMES:
            assert forbidden not in indexed, (
                f"{model.__name__}.{forbidden} is indexed. Ids, digests, monotonic "
                "counters, and timestamps are unbounded — that is the anti-pattern "
                "the schema gate exists to stop."
            )

    @pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
    def test_int_counters_are_plain_fields(self, model):
        for name, f in _fields(model).items():
            if isinstance(f, IntField):
                assert not isinstance(f, IndexedField), (
                    f"{model.__name__}.{name} is a monotonic-looking counter on an index"
                )

    @pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
    def test_index_defaults_are_inside_their_vocabulary(self, model):
        for name, vocabulary in INDEXED_VOCABULARIES[model].items():
            default = getattr(_fields(model)[name], "default", None)
            assert default in vocabulary, (
                f"{model.__name__}.{name} defaults to {default!r}, which is not in its "
                "declared vocabulary — every row would be written to an index set no "
                "reader knows to look in."
            )


class TestTTLDecisions:
    """Every model recorded a TTL decision, and the record matches the plan."""

    @pytest.mark.parametrize(
        "model,expected_ttl",
        list(EXPIRING_MODELS.items()),
        ids=lambda x: x.__name__ if isinstance(x, type) else str(x),
    )
    def test_expiring_models_declare_the_agreed_ttl(self, model, expected_ttl):
        assert getattr(model._meta, "ttl", None) == expected_ttl

    @pytest.mark.parametrize("model", IMMORTAL_MODELS, ids=lambda m: m.__name__)
    def test_immortal_models_declare_no_ttl(self, model):
        assert not getattr(model._meta, "ttl", None), (
            f"{model.__name__} is lineage; a TTL on it would strand the verdicts that cite it."
        )

    @pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
    def test_ttl_decision_is_recorded_in_the_docstring(self, model):
        import importlib

        doc = importlib.import_module(model.__module__).__doc__ or ""
        assert "TTL decision" in doc, (
            f"{model.__module__} does not record its TTL decision. The plan requires a "
            "model-level TTL decision in the docstring, not just in Meta."
        )


class TestRoundTrip:
    """Each model saves and reads back through the ORM."""

    @pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
    def test_create_and_query_by_project_key(self, model):
        row = model.create(project_key=PK, created_at=datetime.now(UTC))
        assert row is not None
        found = list(model.query.filter(project_key=PK))
        assert any(getattr(r, "id", None) == row.id for r in found)

    @pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
    def test_index_filter_reads_back(self, model):
        name, vocabulary = next(iter(INDEXED_VOCABULARIES[model].items()))
        value = vocabulary[0]
        model.create(project_key=PK, created_at=datetime.now(UTC), **{name: value})
        found = list(model.query.filter(project_key=PK, **{name: value}))
        assert found, f"{model.__name__}.{name}={value!r} did not read back through its index"


class TestExportedFromModelsPackage:
    def test_all_eight_are_exported(self):
        import models as m

        exported = sorted(n for n in m.__all__ if n.startswith("Improvement"))
        assert len(exported) == 8, exported
        assert exported == sorted(model.__name__ for model in ALL_MODELS)

    def test_no_models_improvement_subpackage(self):
        import pathlib

        import models

        root = pathlib.Path(models.__file__).parent
        assert not (root / "improvement").is_dir(), (
            "models/ has no sub-packages; the eight improvement records are flat modules."
        )
