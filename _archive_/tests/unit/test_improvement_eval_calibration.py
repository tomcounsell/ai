"""Unit tests for serves-charter judge calibration (lane 4, task 3b).

Covers ``tools.improvement_eval.calibration``: the frozen reference set of
retained architectural corrections, the declared floor with its boundary
behavior, content-addressed freezing cited by digest, and the three reported
numbers (Cohen's kappa, raw agreement, and paired position-swap consistency),
computed in pure Python with no statistics dependency, including kappa's
degeneracy on the one-class reference shape.

Uses the autouse ``redis_test_db`` fixture (tests/conftest.py); every row is
written under a test-scoped ``project_key``.
"""

from __future__ import annotations

import hashlib
import json

import pytest

PK = "test-3216-calibration"


def _seed_architectural(n, project_key=PK):
    from models.improvement_evidence import ImprovementEvidence

    rows = []
    for i in range(n):
        row = ImprovementEvidence.record_once(
            project_key,
            "correction",
            classification="architectural",
            source_ref=f"calibration-seed-{i}",
            text=f"start over, the whole flow misses the user journey ({i})",
        )
        assert row is not None
        rows.append(row)
    return rows


def _stub_judge(expected="APPROVED"):
    def judge_fn(text, *, swapped=False):
        return expected

    return judge_fn


class TestCohensKappa:
    def test_perfect_agreement_is_one(self):
        from tools.improvement_eval.calibration import cohens_kappa

        labels = ["APPROVED", "APPROVED", "CHANGES REQUESTED", "CHANGES REQUESTED"]
        assert cohens_kappa(labels, list(labels)) == 1.0

    def test_chance_level_agreement_is_zero(self):
        from tools.improvement_eval.calibration import cohens_kappa

        assert cohens_kappa(["A", "A", "B", "B"], ["A", "B", "A", "B"]) == 0.0

    def test_partial_agreement_worked_example(self):
        from tools.improvement_eval.calibration import cohens_kappa

        # po = 3/4; pe = (3/4)(2/4) + (1/4)(2/4) = 1/2; kappa = (0.75-0.5)/0.5.
        assert cohens_kappa(["Y", "Y", "Y", "N"], ["Y", "Y", "N", "N"]) == pytest.approx(0.5)

    def test_constant_judge_on_constant_reference_is_perfect(self):
        from tools.improvement_eval.calibration import cohens_kappa

        assert cohens_kappa(["A", "A"], ["A", "A"]) == 1.0

    def test_empty_inputs_raise(self):
        from tools.improvement_eval.calibration import cohens_kappa

        with pytest.raises(ValueError):
            cohens_kappa([], [])

    def test_mismatched_lengths_raise(self):
        from tools.improvement_eval.calibration import cohens_kappa

        with pytest.raises(ValueError):
            cohens_kappa(["A"], ["A", "B"])


class TestPositionSwapConsistency:
    def test_identical_runs_are_fully_consistent(self):
        from tools.improvement_eval.calibration import position_swap_consistency

        labels = ["APPROVED", "CHANGES REQUESTED", "APPROVED"]
        assert position_swap_consistency(labels, list(labels)) == 1.0

    def test_half_flipped_is_half_consistent(self):
        from tools.improvement_eval.calibration import position_swap_consistency

        assert position_swap_consistency(["A", "A", "B", "B"], ["A", "B", "B", "A"]) == 0.5

    def test_empty_inputs_raise(self):
        from tools.improvement_eval.calibration import position_swap_consistency

        with pytest.raises(ValueError):
            position_swap_consistency([], [])

    def test_mismatched_lengths_raise(self):
        from tools.improvement_eval.calibration import position_swap_consistency

        with pytest.raises(ValueError):
            position_swap_consistency(["A"], ["A", "B"])


class TestReferenceSetFloor:
    def test_exactly_at_floor_passes(self):
        from tools.improvement_eval import calibration

        _seed_architectural(calibration.MIN_REFERENCE_SET_SIZE, project_key=PK + "-floor-ok")
        result = calibration.calibrate(PK + "-floor-ok", _stub_judge())
        assert result.size == calibration.MIN_REFERENCE_SET_SIZE
        assert result.digest
        assert result.artifact_ref.startswith("$CF:")

    def test_reference_set_below_floor_yields_infra_failure(self):
        from tools.improvement_eval import calibration
        from tools.improvement_eval.errors import InfraFailure

        _seed_architectural(calibration.MIN_REFERENCE_SET_SIZE - 1, project_key=PK + "-floor-low")
        with pytest.raises(InfraFailure) as excinfo:
            calibration.calibrate(PK + "-floor-low", _stub_judge())
        assert str(calibration.MIN_REFERENCE_SET_SIZE) in str(excinfo.value)

    def test_size_zero_raises_infra_failure(self):
        from tools.improvement_eval import calibration as calibration_module
        from tools.improvement_eval.errors import InfraFailure

        with pytest.raises(InfraFailure):
            calibration_module.calibrate(PK + "-empty", _stub_judge())

    def test_floor_constant_carries_its_rationale(self):
        from tools.improvement_eval import calibration

        assert calibration.MIN_REFERENCE_SET_SIZE >= 1
        assert "30" in calibration.__doc__
        assert str(calibration.MIN_REFERENCE_SET_SIZE) in calibration.__doc__


class TestFrozenReferenceSet:
    def test_frozen_bytes_roundtrip_through_the_store(self, tmp_path):
        from models.verifying_artifact_store import VerifyingArtifactStore
        from tools.improvement_eval import calibration

        _seed_architectural(3, project_key=PK + "-freeze")
        items = calibration.collect_architectural_reference(PK + "-freeze")
        assert len(items) == 3
        store = VerifyingArtifactStore(base_path=str(tmp_path))
        frozen = calibration.freeze_reference_set(items, store=store)
        loaded = json.loads(store.load(frozen.artifact_ref).decode("utf-8"))
        assert len(loaded) == 3
        assert frozen.digest == hashlib.sha256(store.load(frozen.artifact_ref)).hexdigest()

    def test_item_order_does_not_change_the_digest(self, tmp_path):
        from models.verifying_artifact_store import VerifyingArtifactStore
        from tools.improvement_eval import calibration

        _seed_architectural(3, project_key=PK + "-order")
        items = calibration.collect_architectural_reference(PK + "-order")
        store = VerifyingArtifactStore(base_path=str(tmp_path))
        forward = calibration.freeze_reference_set(items, store=store)
        backward = calibration.freeze_reference_set(list(reversed(items)), store=store)
        assert forward.digest == backward.digest

    def test_recalibration_writes_a_new_artifact(self, tmp_path):
        from models.verifying_artifact_store import VerifyingArtifactStore
        from tools.improvement_eval import calibration

        _seed_architectural(2, project_key=PK + "-recal")
        store = VerifyingArtifactStore(base_path=str(tmp_path))
        first = calibration.freeze_reference_set(
            calibration.collect_architectural_reference(PK + "-recal"), store=store
        )
        _seed_architectural(1, project_key=PK + "-recal-extra")
        extra = calibration.collect_architectural_reference(PK + "-recal-extra")
        combined = calibration.collect_architectural_reference(PK + "-recal") + extra
        second = calibration.freeze_reference_set(combined, store=store)
        assert second.digest != first.digest
        assert store.load(first.artifact_ref)
        assert store.load(second.artifact_ref)

    def test_calibration_record_cites_digest_and_all_three_numbers(self):
        from tools.improvement_eval import calibration

        _seed_architectural(calibration.MIN_REFERENCE_SET_SIZE, project_key=PK + "-record")

        def constant_judge(text, *, swapped=False):
            return "APPROVED"

        result = calibration.calibrate(PK + "-record", constant_judge)
        assert result.digest
        assert result.artifact_ref.startswith("$CF:")
        assert result.kappa == 0.0  # disagrees on every item
        assert result.raw_agreement == 0.0
        assert result.position_swap_consistency == 1.0  # consistently wrong is still consistent

    def test_kappa_is_degenerate_on_the_one_class_reference_shape(self):
        """Every reference label is EXPECTED_VERDICT, so kappa cannot rank imperfect judges.

        A judge that agrees on n-1 of n items and one that agrees on 0 of n
        both score kappa 0.0 (``po == pe`` against a one-class reference);
        only the perfect judge scores 1.0. Raw agreement is the figure that
        separates them, which is why it is recorded alongside.
        """
        from tools.improvement_eval import calibration

        n = calibration.MIN_REFERENCE_SET_SIZE
        _seed_architectural(n, project_key=PK + "-oneclass")

        def all_but_one(text, *, swapped=False):
            return "APPROVED" if "(0)" in text else calibration.EXPECTED_VERDICT

        partial = calibration.calibrate(PK + "-oneclass", all_but_one)
        perfect = calibration.calibrate(PK + "-oneclass", _stub_judge(calibration.EXPECTED_VERDICT))

        assert partial.kappa == 0.0
        assert partial.raw_agreement == pytest.approx((n - 1) / n)
        assert perfect.kappa == 1.0
        assert perfect.raw_agreement == 1.0
        assert "uninformative" in calibration.__doc__

    def test_module_hygiene_no_forbidden_surfaces(self):
        import pathlib

        source = pathlib.Path("tools/improvement_eval/calibration.py").read_text()
        assert "ImprovementRelease" not in source
        assert "scipy" not in source
        assert "statsmodels" not in source
        assert "ImprovementCharter" not in source
