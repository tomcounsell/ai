"""Unit tests for models.crash_signature — Popoto library model.

Tests cover:
- upsert_occurrence increments the counter
- project scoping isolation (different project_keys don't mix)
- policy derivation thresholds (min_occurrences, min_success_ratio)
- outcome recording and confidence calculation
- NON_RESUMABLE_DETERMINISTIC signature never becomes auto-eligible

Cleanup: every test that creates a CrashSignature record deletes it in a
finally block using the Popoto ORM (instance.delete()) — never raw Redis.
Test hashes use a recognizable "test-csig-" prefix to make stale records
easy to spot in a running system.
"""

from __future__ import annotations

import hashlib

from models.crash_signature import NON_RESUMABLE_DETERMINISTIC, CrashSignature

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_hash(label: str) -> str:
    """Produce a deterministic 16-char test hash from a label string."""
    return hashlib.sha256(f"test-csig-{label}".encode()).hexdigest()[:16]


def _make_record(label: str, **kwargs) -> CrashSignature:
    """Create and save a CrashSignature record for testing.

    Caller is responsible for cleanup via finally/delete().
    """
    h = _make_hash(label)
    defaults = {
        "human_form": f"test_form_{label}",
        "signature_class": "idle_gap|terminal_failed",
        "resumable": True,
    }
    defaults.update(kwargs)
    return CrashSignature.get_or_create_by_hash(h, **defaults)


def _cleanup(*records: CrashSignature) -> None:
    """Delete test records, swallowing any errors."""
    for r in records:
        try:
            r.delete()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# get_or_create_by_hash
# ---------------------------------------------------------------------------


class TestGetOrCreateByHash:
    def test_creates_new_record_on_first_call(self):
        h = _make_hash("create-new")
        record = None
        try:
            record = CrashSignature.get_or_create_by_hash(
                h, human_form="test_form", signature_class="test_class"
            )
            assert record.signature_hash == h
            assert record.human_form == "test_form"
        finally:
            _cleanup(*([record] if record else []))

    def test_returns_existing_record_on_second_call(self):
        h = _make_hash("idempotent")
        first = None
        try:
            first = CrashSignature.get_or_create_by_hash(h, human_form="form_a")
            second = CrashSignature.get_or_create_by_hash(h, human_form="form_b")
            # human_form should stay as "form_a" — existing record returned unchanged
            assert second.human_form == "form_a"
        finally:
            _cleanup(*([first] if first else []))

    def test_get_by_hash_returns_none_for_missing(self):
        h = _make_hash("definitely-does-not-exist-xyz")
        result = CrashSignature.get_by_hash(h)
        assert result is None


# ---------------------------------------------------------------------------
# upsert_occurrence
# ---------------------------------------------------------------------------


class TestUpsertOccurrence:
    def test_upsert_increments_occurrence_count(self):
        record = None
        try:
            record = _make_record("upsert-increment")
            assert record.occurrence_count_int == 0
            record.upsert_occurrence("sess-001", "failed")
            assert record.occurrence_count_int == 1
            record.upsert_occurrence("sess-002", "failed")
            assert record.occurrence_count_int == 2
        finally:
            _cleanup(*([record] if record else []))

    def test_upsert_sets_project_key(self):
        record = None
        try:
            record = _make_record("upsert-project")
            record.upsert_occurrence("sess-001", "failed", project_key="test-proj-a")
            # Re-read from Redis to confirm persistence
            fresh = CrashSignature.get_by_hash(record.signature_hash)
            assert fresh is not None
            assert fresh.project_key == "test-proj-a"
        finally:
            _cleanup(*([record] if record else []))

    def test_upsert_does_not_overwrite_project_key_when_none(self):
        record = None
        try:
            record = _make_record("upsert-no-overwrite")
            record.upsert_occurrence("sess-001", "failed", project_key="proj-x")
            record.upsert_occurrence("sess-002", "failed", project_key=None)
            fresh = CrashSignature.get_by_hash(record.signature_hash)
            assert fresh is not None
            assert fresh.project_key == "proj-x"
        finally:
            _cleanup(*([record] if record else []))


# ---------------------------------------------------------------------------
# Project scoping isolation
# ---------------------------------------------------------------------------


class TestProjectScopingIsolation:
    def test_all_for_project_returns_only_matching_project(self):
        rec_a = None
        rec_b = None
        try:
            rec_a = _make_record("proj-iso-a")
            rec_b = _make_record("proj-iso-b")
            rec_a.upsert_occurrence("s1", "failed", project_key="test-proj-isolation-alpha")
            rec_b.upsert_occurrence("s2", "failed", project_key="test-proj-isolation-beta")

            alpha_records = CrashSignature.all_for_project("test-proj-isolation-alpha")
            beta_records = CrashSignature.all_for_project("test-proj-isolation-beta")

            alpha_hashes = {r.signature_hash for r in alpha_records}
            beta_hashes = {r.signature_hash for r in beta_records}

            assert rec_a.signature_hash in alpha_hashes
            assert rec_b.signature_hash not in alpha_hashes

            assert rec_b.signature_hash in beta_hashes
            assert rec_a.signature_hash not in beta_hashes
        finally:
            _cleanup(*filter(None, [rec_a, rec_b]))

    def test_all_for_project_returns_empty_for_unknown_project(self):
        results = CrashSignature.all_for_project("test-proj-definitely-nonexistent-xyz")
        assert results == []


# ---------------------------------------------------------------------------
# Outcome recording and confidence
# ---------------------------------------------------------------------------


class TestOutcomeRecording:
    def test_record_outcome_increments_attempts(self):
        record = None
        try:
            record = _make_record("outcome-attempts")
            record.record_outcome("auto_resume", recovered=True)
            tallies = record._load_tallies()
            assert tallies["auto_resume"]["attempts"] == 1
        finally:
            _cleanup(*([record] if record else []))

    def test_record_outcome_tracks_recovered_and_failed(self):
        record = None
        try:
            record = _make_record("outcome-track")
            record.record_outcome("auto_resume", recovered=True)
            record.record_outcome("auto_resume", recovered=True)
            record.record_outcome("auto_resume", recovered=False)
            tallies = record._load_tallies()
            bucket = tallies["auto_resume"]
            assert bucket["attempts"] == 3
            assert bucket["recovered"] == 2
            assert bucket["failed"] == 1
        finally:
            _cleanup(*([record] if record else []))

    def test_policy_confidence_returns_ratio(self):
        record = None
        try:
            record = _make_record("confidence-ratio")
            # 3 recovered out of 4 attempts = 0.75
            for _ in range(3):
                record.record_outcome("auto_resume", recovered=True)
            record.record_outcome("auto_resume", recovered=False)
            conf = record.policy_confidence("auto_resume")
            assert abs(conf - 0.75) < 1e-9
        finally:
            _cleanup(*([record] if record else []))

    def test_policy_confidence_zero_for_unknown_strategy(self):
        record = None
        try:
            record = _make_record("confidence-unknown")
            conf = record.policy_confidence("unknown_strategy")
            assert conf == 0.0
        finally:
            _cleanup(*([record] if record else []))

    def test_policy_confidence_zero_for_no_attempts(self):
        record = None
        try:
            record = _make_record("confidence-no-attempts")
            assert record.policy_confidence("auto_resume") == 0.0
        finally:
            _cleanup(*([record] if record else []))

    def test_record_outcome_multiple_strategies_independent(self):
        record = None
        try:
            record = _make_record("multi-strategy")
            record.record_outcome("strategy_a", recovered=True)
            record.record_outcome("strategy_b", recovered=False)
            assert record.policy_confidence("strategy_a") == 1.0
            assert record.policy_confidence("strategy_b") == 0.0
        finally:
            _cleanup(*([record] if record else []))


# ---------------------------------------------------------------------------
# Policy thresholds and auto-eligibility
# ---------------------------------------------------------------------------


class TestAutoEligibility:
    def test_not_eligible_below_min_occurrences(self):
        record = None
        try:
            record = _make_record("elig-low-count")
            # 2 occurrences, but min is 3
            record.upsert_occurrence("s1", "failed")
            record.upsert_occurrence("s2", "failed")
            for _ in range(4):
                record.record_outcome("auto_resume", recovered=True)
            assert record.is_auto_eligible(min_occurrences=3) is False
        finally:
            _cleanup(*([record] if record else []))

    def test_not_eligible_below_min_success_ratio(self):
        record = None
        try:
            record = _make_record("elig-low-ratio")
            for _ in range(5):
                record.upsert_occurrence(f"s{_}", "failed")
            # 1 out of 2 = 0.5, below 0.7
            record.record_outcome("auto_resume", recovered=True)
            record.record_outcome("auto_resume", recovered=False)
            assert record.is_auto_eligible(min_occurrences=3, min_success_ratio=0.7) is False
        finally:
            _cleanup(*([record] if record else []))

    def test_eligible_when_thresholds_met(self):
        record = None
        try:
            record = _make_record("elig-meets-threshold")
            for i in range(5):
                record.upsert_occurrence(f"s{i}", "failed")
            # 4 recovered, 1 failed = 0.8 >= 0.7
            for _ in range(4):
                record.record_outcome("auto_resume", recovered=True)
            record.record_outcome("auto_resume", recovered=False)
            assert record.is_auto_eligible(min_occurrences=3, min_success_ratio=0.7) is True
        finally:
            _cleanup(*([record] if record else []))

    def test_non_resumable_deterministic_never_auto_eligible(self):
        """NON_RESUMABLE_DETERMINISTIC signature is never auto-eligible, regardless of counts."""
        record = None
        try:
            h = _make_hash("non-resumable-eligible")
            record = CrashSignature.get_or_create_by_hash(
                h,
                human_form="NON_RESUMABLE_DETERMINISTIC[no_turn_start]",
                signature_class=NON_RESUMABLE_DETERMINISTIC,
                resumable=False,
            )
            # Simulate many occurrences and high success rate
            for i in range(10):
                record.upsert_occurrence(f"s{i}", "failed")
                record.record_outcome("auto_resume", recovered=True)
            assert record.is_auto_eligible(min_occurrences=3, min_success_ratio=0.7) is False
        finally:
            _cleanup(*([record] if record else []))


# ---------------------------------------------------------------------------
# Truthy / bool coercion for Popoto string fields
# ---------------------------------------------------------------------------


class TestTruthyCoercion:
    def test_resumable_stored_as_string_reads_as_bool(self):
        record = None
        try:
            h = _make_hash("truthy-resumable")
            record = CrashSignature.get_or_create_by_hash(h, resumable=True)
            # Force re-read from Redis
            fresh = CrashSignature.get_by_hash(h)
            assert fresh is not None
            assert fresh.is_resumable is True
        finally:
            _cleanup(*([record] if record else []))

    def test_non_resumable_stored_as_string_reads_as_false(self):
        record = None
        try:
            h = _make_hash("truthy-non-resumable")
            record = CrashSignature.get_or_create_by_hash(h, resumable=False)
            fresh = CrashSignature.get_by_hash(h)
            assert fresh is not None
            assert fresh.is_resumable is False
        finally:
            _cleanup(*([record] if record else []))
