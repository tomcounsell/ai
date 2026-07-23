"""Unit tests for reflections/memory/memory_distill_backfill.py (memory-distilled-ingest, Phase 3).

Covers, per docs/plans/memory-distilled-ingest.md "Task 2 / build-reflection":
  - Attempt cap -> terminal distill_abandoned (no distillation attempted once capped).
  - save()-return inspection: a forced False on the settled write bumps
    attempts rather than silently dropping the update.
  - Ascending scan ordering by distill_last_attempt_at (poison-pill records
    sink to the back within the per-run cap).
  - The scan-sort defensive key never raises TypeError on a mix of
    present/missing distill_last_attempt_at.
  - Race-1 re-read-before-save guard: a record whose distill_status flips
    between scan and save is skipped, never clobbered.
  - sweep_provisional_to_abandoned() idempotency.
  - gate_reason() applied to the distillation OUTPUT routes a low-quality
    fact to the refused/fail-open path, never a settled write.
  - Single-write ordering: the attempt-counter bump and the outcome land in
    ONE save() call per record (except the rare write-filter-drop defensive
    backstop, which is explicitly a second, separate save).

Uses MagicMock Memory records patched via `models.memory.Memory`, mirroring
the established pattern in tests/unit/test_reflections_memory.py -- this
module's LLM call (`distill_human_prompt_async`) and content gate
(`gate_reason`) are mocked at their import source so the reflection's control
flow is tested in isolation. Real Redis + real Memory + a stubbed LLM call is
covered separately in tests/integration/test_memory_distill_backfill_integration.py.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def run_async(coro):
    return asyncio.run(coro)


def _make_provisional(
    *,
    memory_id="mem_prov_1",
    content="Tom wants the justfile rewritten",
    attempts=0,
    last_attempt_at=0,
    project_key="test-distill",
    status="provisional",
    superseded_by="",
    extra_meta=None,
):
    """Build a MagicMock Memory record shaped like a provisional distillation candidate."""
    meta = {
        "distill_status": status,
        "distill_attempts": attempts,
        "distill_last_attempt_at": last_attempt_at,
    }
    if extra_meta:
        meta.update(extra_meta)
    m = MagicMock()
    m.memory_id = memory_id
    m.content = content
    m.metadata = meta
    m.project_key = project_key
    m.superseded_by = superseded_by
    m.importance = 3.0
    m.save.return_value = True
    return m


def _no_op_llm_none():
    return AsyncMock(return_value=None)


class TestAttemptCeiling:
    """Attempt cap -> terminal distill_abandoned, no distillation attempted."""

    def test_cap_reached_transitions_without_calling_llm(self):
        from config.memory_defaults import MAX_DISTILL_ATTEMPTS
        from reflections.memory.memory_distill_backfill import run as run_backfill

        capped = _make_provisional(memory_id="mem_capped", attempts=MAX_DISTILL_ATTEMPTS)

        mock_llm = AsyncMock()
        with (
            patch("models.memory.Memory") as mock_model,
            patch("agent.memory_extraction.distill_human_prompt_async", mock_llm),
            patch.dict("os.environ", {"MEMORY_DISTILL_BACKFILL_APPLY": "true"}),
        ):
            mock_model.query.all.return_value = [capped]
            result = run_async(run_backfill())

        assert result["status"] == "ok"
        mock_llm.assert_not_called()
        assert capped.metadata["distill_status"] == "distill_abandoned"
        capped.save.assert_called_once()
        assert capped.save.call_args.kwargs.get("update_fields") == ["metadata"]

    def test_attempts_count_unchanged_on_cap_transition(self):
        """No distillation attempt happened, so distill_attempts is not re-bumped."""
        from config.memory_defaults import MAX_DISTILL_ATTEMPTS
        from reflections.memory.memory_distill_backfill import run as run_backfill

        capped = _make_provisional(memory_id="mem_capped2", attempts=MAX_DISTILL_ATTEMPTS)

        with (
            patch("models.memory.Memory") as mock_model,
            patch(
                "agent.memory_extraction.distill_human_prompt_async",
                AsyncMock(),
            ),
            patch.dict("os.environ", {"MEMORY_DISTILL_BACKFILL_APPLY": "true"}),
        ):
            mock_model.query.all.return_value = [capped]
            run_async(run_backfill())

        assert capped.metadata["distill_attempts"] == MAX_DISTILL_ATTEMPTS


class TestSaveReturnInspection:
    """A forced False return from the settled-distillation save must not be silently dropped."""

    def test_write_filter_drop_bumps_attempts_instead_of_silent_loss(self):
        from reflections.memory.memory_distill_backfill import run as run_backfill

        record = _make_provisional(memory_id="mem_wfdrop")
        # First save() (the settled content+importance+metadata write) returns
        # False (write-filter drop); the defensive second save() succeeds.
        record.save.side_effect = [False, True]

        mock_llm = AsyncMock(
            return_value={"fact": "Tom prefers dark mode always", "category": "pattern"}
        )
        with (
            patch("models.memory.Memory") as mock_model,
            patch("agent.memory_extraction.distill_human_prompt_async", mock_llm),
            patch("agent.memory_quality.gate_reason", return_value=None),
            patch.dict("os.environ", {"MEMORY_DISTILL_BACKFILL_APPLY": "true"}),
        ):
            mock_model.query.all.return_value = [record]
            mock_model.query.filter.return_value.first.return_value = record
            result = run_async(run_backfill())

        assert result["status"] == "ok"
        assert record.save.call_count == 2
        # The record was NOT left silently un-updated: the attempt counter bumped.
        assert record.metadata["distill_attempts"] == 1
        # And it stayed provisional (not falsely marked distilled) since the
        # settled write never actually landed.
        assert record.metadata["distill_status"] != "distilled"

    def test_write_filter_drop_increments_distill_failed_not_distilled(self):
        from reflections.memory.memory_distill_backfill import run as run_backfill

        record = _make_provisional(memory_id="mem_wfdrop2")
        record.save.side_effect = [False, True]

        mock_llm = AsyncMock(
            return_value={"fact": "Tom prefers dark mode always", "category": "pattern"}
        )
        with (
            patch("models.memory.Memory") as mock_model,
            patch("agent.memory_extraction.distill_human_prompt_async", mock_llm),
            patch("agent.memory_quality.gate_reason", return_value=None),
            patch("models.memory_distill_gate._increment_distill_counter") as mock_counter,
            patch.dict("os.environ", {"MEMORY_DISTILL_BACKFILL_APPLY": "true"}),
        ):
            mock_model.query.all.return_value = [record]
            mock_model.query.filter.return_value.first.return_value = record
            run_async(run_backfill())

        reasons = [c.args[1] for c in mock_counter.call_args_list]
        assert "distill_failed" in reasons
        assert "distilled" not in reasons


class TestAscendingOrdering:
    """Scan orders ascending by distill_last_attempt_at -- fresh records processed first."""

    def test_poison_pill_sinks_below_cap(self):
        from reflections.memory.memory_distill_backfill import run as run_backfill

        poison = _make_provisional(memory_id="mem_poison", last_attempt_at=99999, attempts=1)
        fresh_records = [
            _make_provisional(memory_id=f"mem_fresh_{i}", last_attempt_at=0) for i in range(3)
        ]

        mock_llm = _no_op_llm_none()
        with (
            patch("models.memory.Memory") as mock_model,
            patch("agent.memory_extraction.distill_human_prompt_async", mock_llm),
            patch("config.memory_defaults.MAX_DISTILL_PER_RUN", 3),
            patch.dict("os.environ", {"MEMORY_DISTILL_BACKFILL_APPLY": "true"}),
        ):
            mock_model.query.all.return_value = [poison, *fresh_records]
            run_async(run_backfill())

        # Cap is 3 -- only the 3 fresh (last_attempt_at=0) records should have
        # been processed; the poison-pill (last_attempt_at=99999) sinks below
        # the cap and its save() is never called.
        poison.save.assert_not_called()
        for f in fresh_records:
            f.save.assert_called_once()


class TestScanSortDefensiveKey:
    """The .get(..., 0) sort key never raises TypeError on a missing key."""

    def test_missing_last_attempt_at_key_does_not_raise(self):
        from reflections.memory.memory_distill_backfill import run as run_backfill

        legacy = _make_provisional(memory_id="mem_legacy")
        # Simulate a legacy record missing the seed key entirely.
        del legacy.metadata["distill_last_attempt_at"]
        fresh = _make_provisional(memory_id="mem_fresh", last_attempt_at=0)

        with (
            patch("models.memory.Memory") as mock_model,
            patch.dict("os.environ", {"MEMORY_DISTILL_BACKFILL_APPLY": "false"}),
        ):
            mock_model.query.all.return_value = [legacy, fresh]
            # Must not raise TypeError comparing None-ish vs float.
            result = run_async(run_backfill())

        assert result["status"] == "ok"


class TestRace1Guard:
    """A record whose distill_status flips between scan and save is skipped, never clobbered."""

    def test_concurrently_settled_record_is_skipped_not_overwritten(self):
        from reflections.memory.memory_distill_backfill import run as run_backfill

        scanned = _make_provisional(memory_id="mem_race")
        # Simulate a concurrent run already having distilled this record by
        # the time the re-read happens.
        concurrent = _make_provisional(
            memory_id="mem_race", status="distilled", extra_meta={"distill_status": "distilled"}
        )

        mock_llm = AsyncMock(
            return_value={"fact": "Tom prefers dark mode always", "category": "pattern"}
        )
        with (
            patch("models.memory.Memory") as mock_model,
            patch("agent.memory_extraction.distill_human_prompt_async", mock_llm),
            patch("agent.memory_quality.gate_reason", return_value=None),
            patch.dict("os.environ", {"MEMORY_DISTILL_BACKFILL_APPLY": "true"}),
        ):
            mock_model.query.all.return_value = [scanned]
            mock_model.query.filter.return_value.first.return_value = concurrent
            run_async(run_backfill())

        # The write must be skipped entirely -- neither object's save() is called.
        scanned.save.assert_not_called()
        concurrent.save.assert_not_called()

    def test_record_deleted_between_scan_and_save_is_skipped(self):
        """first() returning None (record vanished) is also treated as a skip."""
        from reflections.memory.memory_distill_backfill import run as run_backfill

        scanned = _make_provisional(memory_id="mem_vanished")

        mock_llm = AsyncMock(
            return_value={"fact": "Tom prefers dark mode always", "category": "pattern"}
        )
        with (
            patch("models.memory.Memory") as mock_model,
            patch("agent.memory_extraction.distill_human_prompt_async", mock_llm),
            patch("agent.memory_quality.gate_reason", return_value=None),
            patch.dict("os.environ", {"MEMORY_DISTILL_BACKFILL_APPLY": "true"}),
        ):
            mock_model.query.all.return_value = [scanned]
            mock_model.query.filter.return_value.first.return_value = None
            run_async(run_backfill())

        scanned.save.assert_not_called()


class TestSweepProvisionalToAbandoned:
    """sweep_provisional_to_abandoned() -- one-off idempotent drain."""

    def test_transitions_provisional_leaves_distilled_untouched(self):
        from reflections.memory.memory_distill_backfill import (
            sweep_provisional_to_abandoned,
        )

        provisional = _make_provisional(memory_id="mem_sweep_prov")
        distilled = _make_provisional(memory_id="mem_sweep_dist", status="distilled")

        with patch("models.memory.Memory") as mock_model:
            mock_model.query.all.return_value = [provisional, distilled]
            result = sweep_provisional_to_abandoned()

        assert result["status"] == "ok"
        assert result["abandoned"] == 1
        assert provisional.metadata["distill_status"] == "distill_abandoned"
        provisional.save.assert_called_once()
        assert provisional.save.call_args.kwargs.get("update_fields") == ["metadata"]
        distilled.save.assert_not_called()

    def test_second_run_is_a_no_op(self):
        from reflections.memory.memory_distill_backfill import (
            sweep_provisional_to_abandoned,
        )

        provisional = _make_provisional(memory_id="mem_sweep_idem")

        with patch("models.memory.Memory") as mock_model:
            mock_model.query.all.return_value = [provisional]
            first_result = sweep_provisional_to_abandoned()

            # Second run sees the SAME (now-abandoned, mutated in place) record.
            second_result = sweep_provisional_to_abandoned()

        assert first_result["abandoned"] == 1
        assert second_result["abandoned"] == 0
        # save() was only ever called once, across both runs.
        provisional.save.assert_called_once()

    def test_content_and_importance_untouched(self):
        from reflections.memory.memory_distill_backfill import (
            sweep_provisional_to_abandoned,
        )

        provisional = _make_provisional(memory_id="mem_sweep_content", content="verbatim text")
        provisional.importance = 3.0

        with patch("models.memory.Memory") as mock_model:
            mock_model.query.all.return_value = [provisional]
            sweep_provisional_to_abandoned()

        assert provisional.content == "verbatim text"
        assert provisional.importance == 3.0


class TestGateReasonOnDistillationOutput:
    """A low-quality distillation output is refused, never settled."""

    def test_gate_hit_routes_to_refused_not_distilled(self):
        from reflections.memory.memory_distill_backfill import run as run_backfill

        record = _make_provisional(memory_id="mem_refused")

        mock_llm = AsyncMock(return_value={"fact": "ok", "category": "pattern"})
        with (
            patch("models.memory.Memory") as mock_model,
            patch("agent.memory_extraction.distill_human_prompt_async", mock_llm),
            patch("agent.memory_quality.gate_reason", return_value="short"),
            patch("models.memory_distill_gate._increment_distill_counter") as mock_counter,
            patch.dict("os.environ", {"MEMORY_DISTILL_BACKFILL_APPLY": "true"}),
        ):
            mock_model.query.all.return_value = [record]
            run_async(run_backfill())

        # Content must NOT have been overwritten with the low-quality fact.
        assert record.content != "ok"
        assert record.metadata["distill_status"] != "distilled"
        assert record.metadata["distill_attempts"] == 1
        reasons = [c.args[1] for c in mock_counter.call_args_list]
        assert "distill_refused" in reasons
        assert "distilled" not in reasons
        assert "distill_failed" not in reasons

    def test_gate_hit_is_single_write(self):
        from reflections.memory.memory_distill_backfill import run as run_backfill

        record = _make_provisional(memory_id="mem_refused_single")

        mock_llm = AsyncMock(return_value={"fact": "ok", "category": "pattern"})
        with (
            patch("models.memory.Memory") as mock_model,
            patch("agent.memory_extraction.distill_human_prompt_async", mock_llm),
            patch("agent.memory_quality.gate_reason", return_value="fragment"),
            patch.dict("os.environ", {"MEMORY_DISTILL_BACKFILL_APPLY": "true"}),
        ):
            mock_model.query.all.return_value = [record]
            run_async(run_backfill())

        record.save.assert_called_once()
        assert record.save.call_args.kwargs.get("update_fields") == ["metadata"]


class TestSingleWriteOrdering:
    """The attempt-counter bump and the outcome land in ONE save() call."""

    def test_llm_failure_is_single_write(self):
        from reflections.memory.memory_distill_backfill import run as run_backfill

        record = _make_provisional(memory_id="mem_fail_single")

        with (
            patch("models.memory.Memory") as mock_model,
            patch("agent.memory_extraction.distill_human_prompt_async", _no_op_llm_none()),
            patch.dict("os.environ", {"MEMORY_DISTILL_BACKFILL_APPLY": "true"}),
        ):
            mock_model.query.all.return_value = [record]
            run_async(run_backfill())

        record.save.assert_called_once()
        assert record.save.call_args.kwargs.get("update_fields") == ["metadata"]
        assert record.metadata["distill_attempts"] == 1

    def test_settled_distillation_is_single_write(self):
        from reflections.memory.memory_distill_backfill import run as run_backfill

        record = _make_provisional(memory_id="mem_settle_single")

        mock_llm = AsyncMock(
            return_value={"fact": "Tom prefers dark mode across every tool", "category": "decision"}
        )
        with (
            patch("models.memory.Memory") as mock_model,
            patch("agent.memory_extraction.distill_human_prompt_async", mock_llm),
            patch("agent.memory_quality.gate_reason", return_value=None),
            patch.dict("os.environ", {"MEMORY_DISTILL_BACKFILL_APPLY": "true"}),
        ):
            mock_model.query.all.return_value = [record]
            mock_model.query.filter.return_value.first.return_value = record
            run_async(run_backfill())

        record.save.assert_called_once()
        call = record.save.call_args
        assert call.kwargs.get("update_fields") == ["content", "importance", "metadata"]
        assert record.metadata["distill_status"] == "distilled"
        assert record.metadata["distill_attempts"] == 1
        assert record.content == "Tom prefers dark mode across every tool"


class TestDryRunMode:
    """MEMORY_DISTILL_BACKFILL_APPLY=false forces dry-run -- no writes at all."""

    def test_dry_run_writes_nothing(self):
        from reflections.memory.memory_distill_backfill import run as run_backfill

        record = _make_provisional(memory_id="mem_dryrun")

        with (
            patch("models.memory.Memory") as mock_model,
            patch.dict("os.environ", {"MEMORY_DISTILL_BACKFILL_APPLY": "false"}),
        ):
            mock_model.query.all.return_value = [record]
            result = run_async(run_backfill())

        assert "DRY RUN" in result["summary"]
        record.save.assert_not_called()

    def test_apply_defaults_true_when_unset(self):
        """Unlike memory_embedding_backfill, this reflection applies by default."""
        from reflections.memory.memory_distill_backfill import _apply_mode_enabled

        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("MEMORY_DISTILL_BACKFILL_APPLY", None)
            assert _apply_mode_enabled() is True


class TestEmptyAndErrorHandling:
    def test_empty_queryset(self):
        from reflections.memory.memory_distill_backfill import run as run_backfill

        with patch("models.memory.Memory") as mock_model:
            mock_model.query.all.return_value = []
            result = run_async(run_backfill())

        assert result["status"] == "ok"

    def test_redis_unavailable_returns_error(self):
        from reflections.memory.memory_distill_backfill import run as run_backfill

        with patch("models.memory.Memory") as mock_model:
            mock_model.query.all.side_effect = Exception("redis unavailable")
            result = run_async(run_backfill())

        assert result["status"] == "error"

    def test_one_poisoned_record_does_not_abort_batch(self):
        """A per-record exception is caught -- the batch continues."""
        from reflections.memory.memory_distill_backfill import run as run_backfill

        bad = _make_provisional(memory_id="mem_bad")
        bad.save.side_effect = Exception("simulated redis blip")
        good = _make_provisional(memory_id="mem_good")

        with (
            patch("models.memory.Memory") as mock_model,
            patch("agent.memory_extraction.distill_human_prompt_async", _no_op_llm_none()),
            patch.dict("os.environ", {"MEMORY_DISTILL_BACKFILL_APPLY": "true"}),
        ):
            mock_model.query.all.return_value = [bad, good]
            result = run_async(run_backfill())

        assert result["status"] == "ok"
        good.save.assert_called_once()

    def test_superseded_records_are_excluded_from_scan(self):
        from reflections.memory.memory_distill_backfill import run as run_backfill

        superseded = _make_provisional(memory_id="mem_superseded", superseded_by="mem_other")

        with (
            patch("models.memory.Memory") as mock_model,
            patch("agent.memory_extraction.distill_human_prompt_async", _no_op_llm_none()),
            patch.dict("os.environ", {"MEMORY_DISTILL_BACKFILL_APPLY": "true"}),
        ):
            mock_model.query.all.return_value = [superseded]
            run_async(run_backfill())

        superseded.save.assert_not_called()
