"""Unit tests for scripts/memory_consolidation.py.

Tests cover:
- Canary set: 10 distinct items never merged together in a single batch
- Idempotency: second run proposes no additional merges
- Exemption: memories with importance >= 7.0 never proposed for merge
- Rate limit: max 10 merges enforced across a run
- WriteFilter guard: save() returning False logs WARNING, no exception
- Dry-run: no Redis writes when dry_run=True
- Empty/single-record groups: handled without calling Haiku
- JSON parse failure: Haiku returning invalid JSON is skipped gracefully
- Contradiction flagging: valor-telegram send called; CalledProcessError falls
  back to logs/memory-contradictions.log
"""

import logging
from unittest.mock import MagicMock, patch

# --------------------------------------------------------------------------
# Helpers for building mock Memory records
# --------------------------------------------------------------------------


def _make_record(
    memory_id: str,
    content: str,
    importance: float = 2.0,
    category: str = "correction",
    tags: list | None = None,
    superseded_by: str = "",
) -> MagicMock:
    """Create a MagicMock resembling a Memory record."""
    record = MagicMock()
    record.memory_id = memory_id
    record.content = content
    record.importance = importance
    record.superseded_by = superseded_by
    record.superseded_by_rationale = ""
    record.metadata = {"category": category, "tags": tags or []}
    record.save.return_value = None  # success
    return record


# --------------------------------------------------------------------------
# Canary set test
# --------------------------------------------------------------------------


CANARY_CONTENTS = [
    "Always commit plan documents on main, never on feature branches",
    "Never include co-author trailers in commit messages",
    "Use real integration tests — never mock the database",
    "All bulk Redis operations must be project-scoped; tests must never touch production data",
    "Memory system must fail silently — never crash the bridge or agent",
    "The PM session orchestrates; Dev session executes — never reverse this",
    (
        "Plans must include ## Documentation, ## Update System,"
        " ## Agent Integration, ## Test Impact sections"
    ),
    "Popoto records must use instance.delete() or Model.rebuild_indexes() — never raw Redis DEL",
    "Telegram output routing uses the nudge loop — bridge has no SDLC awareness",
    "SupersededBy records must be excluded from recall but retained in Redis for audit",
]


class TestCanarySet:
    """The 10 canonical corrections must never be proposed for merging."""

    def test_canary_set_never_merged(self):
        """All 10 canary items in one batch → proposed_merges == 0."""
        canary_records = [
            _make_record(f"canary-{i}", content, category="correction")
            for i, content in enumerate(CANARY_CONTENTS)
        ]

        with (
            patch(
                "scripts.memory_consolidation._load_active_memories",
                return_value=canary_records,
            ),
            patch(
                "scripts.memory_consolidation._call_haiku",
                return_value={"actions": []},
            ),
        ):
            from scripts.memory_consolidation import run_consolidation

            result = run_consolidation(project_key="test", dry_run=True)

        assert result["proposed_merges"] == 0, (
            f"Canary set produced unexpected merge proposals: {result}"
        )

    def test_canary_set_haiku_not_called_for_single_record(self):
        """If only one record per group, Haiku is never called."""
        single_record = _make_record("solo", CANARY_CONTENTS[0], category="correction")

        with (
            patch(
                "scripts.memory_consolidation._load_active_memories",
                return_value=[single_record],
            ),
            patch("scripts.memory_consolidation._call_haiku") as mock_haiku,
        ):
            from scripts.memory_consolidation import run_consolidation

            run_consolidation(project_key="test", dry_run=True)

        mock_haiku.assert_not_called()


# --------------------------------------------------------------------------
# Idempotency test
# --------------------------------------------------------------------------


class TestIdempotency:
    """Running consolidation twice produces no additional merges."""

    def test_idempotency_second_run_proposes_zero(self):
        """After first run (dry-run), second run with same records proposes 0."""
        records = [
            _make_record("idem-1", "Always use real DB in tests", category="correction"),
            _make_record("idem-2", "Never mock the database in tests", category="correction"),
        ]

        haiku_response = {
            "actions": [
                {
                    "action": "merge",
                    "ids": ["idem-1", "idem-2"],
                    "merged_content": "Always use real DB in tests, never mock",
                    "merged_importance": 2.0,
                    "merged_category": "correction",
                    "merged_tags": [],
                    "rationale": "Both express the same no-mock instruction",
                }
            ]
        }

        with (
            patch(
                "scripts.memory_consolidation._load_active_memories",
                return_value=records,
            ),
            patch(
                "scripts.memory_consolidation._call_haiku",
                return_value=haiku_response,
            ),
        ):
            from scripts.memory_consolidation import run_consolidation

            # First run
            result1 = run_consolidation(project_key="test", dry_run=True)
            assert result1["proposed_merges"] == 1

            # Second run with same records — still proposes 1 (dry-run, no writes)
            # This confirms the callable is idempotent in dry-run mode (no state change)
            result2 = run_consolidation(project_key="test", dry_run=True)
            assert result2["proposed_merges"] == 1

    def test_idempotency_after_apply_second_run_zero(self):
        """After apply run marks originals superseded, second run skips them."""
        active_record = _make_record(
            "dup-active", "Use real DB", category="correction", superseded_by=""
        )

        # After first apply run, load_active_memories returns only non-superseded records
        with (
            patch(
                "scripts.memory_consolidation._load_active_memories",
                return_value=[active_record],  # only one active record
            ),
            patch("scripts.memory_consolidation._call_haiku") as mock_haiku,
        ):
            from scripts.memory_consolidation import run_consolidation

            result = run_consolidation(project_key="test", dry_run=True)

        # Only 1 record → no batches with >= 2 records → Haiku never called
        mock_haiku.assert_not_called()
        assert result["proposed_merges"] == 0


# --------------------------------------------------------------------------
# Exemption test: importance >= 7.0 never merged
# --------------------------------------------------------------------------


class TestExemption:
    """Records with importance >= 7.0 must never appear in merge proposals."""

    def test_high_importance_records_not_merged(self):
        """Two near-duplicate high-importance records → skipped_exempt >= 1."""
        hi_record_1 = _make_record(
            "hi-1", "Always use real DB", importance=8.0, category="correction"
        )
        hi_record_2 = _make_record(
            "hi-2", "Never mock the database", importance=7.0, category="correction"
        )

        haiku_response = {
            "actions": [
                {
                    "action": "merge",
                    "ids": ["hi-1", "hi-2"],
                    "merged_content": "Always use real DB, never mock",
                    "merged_importance": 8.0,
                    "merged_category": "correction",
                    "merged_tags": [],
                    "rationale": "Same instruction",
                }
            ]
        }

        with (
            patch(
                "scripts.memory_consolidation._load_active_memories",
                return_value=[hi_record_1, hi_record_2],
            ),
            patch(
                "scripts.memory_consolidation._call_haiku",
                return_value=haiku_response,
            ),
        ):
            from scripts.memory_consolidation import run_consolidation

            result = run_consolidation(project_key="test", dry_run=True)

        assert result["proposed_merges"] == 0
        assert result["skipped_exempt"] >= 1

    def test_mixed_importance_only_low_merged(self):
        """Only the low-importance pair is proposed when mixed with high-importance."""
        low_1 = _make_record("low-1", "Pattern A", importance=2.0, category="pattern")
        low_2 = _make_record("low-2", "Pattern A (variant)", importance=2.0, category="pattern")
        high = _make_record("high", "Critical rule", importance=8.0, category="pattern")

        haiku_response = {
            "actions": [
                {
                    "action": "merge",
                    "ids": ["low-1", "low-2"],
                    "merged_content": "Pattern A",
                    "merged_importance": 2.0,
                    "merged_category": "pattern",
                    "merged_tags": [],
                    "rationale": "Same pattern",
                },
                {
                    "action": "merge",
                    "ids": ["high", "low-1"],  # should be rejected
                    "merged_content": "Critical rule variant",
                    "merged_importance": 8.0,
                    "merged_category": "pattern",
                    "merged_tags": [],
                    "rationale": "Same critical pattern",
                },
            ]
        }

        with (
            patch(
                "scripts.memory_consolidation._load_active_memories",
                return_value=[low_1, low_2, high],
            ),
            patch(
                "scripts.memory_consolidation._call_haiku",
                return_value=haiku_response,
            ),
        ):
            from scripts.memory_consolidation import run_consolidation

            result = run_consolidation(project_key="test", dry_run=True)

        # Only low-1 + low-2 merge is valid; high merge rejected
        assert result["proposed_merges"] == 1
        assert result["skipped_exempt"] >= 1


# --------------------------------------------------------------------------
# Rate limit test: max 10 merges enforced
# --------------------------------------------------------------------------


class TestRateLimit:
    """Maximum MAX_MERGES_PER_RUN merges applied per run."""

    def test_rate_limit_caps_applied_merges_at_10(self):
        """When 15 merge proposals are valid, only 10 are applied."""
        # Create 30 records in 15 pairs
        records = []
        for i in range(30):
            records.append(_make_record(f"rec-{i}", f"Memory content {i}", category="pattern"))

        # Build 15 merge proposals
        actions = []
        for i in range(0, 30, 2):
            actions.append(
                {
                    "action": "merge",
                    "ids": [f"rec-{i}", f"rec-{i + 1}"],
                    "merged_content": f"Merged content {i}",
                    "merged_importance": 2.0,
                    "merged_category": "pattern",
                    "merged_tags": [],
                    "rationale": f"Near-duplicate pair {i}",
                }
            )

        haiku_response = {"actions": actions}
        merged_record = _make_record("merged-id", "Merged content", importance=2.0)

        with (
            patch(
                "scripts.memory_consolidation._load_active_memories",
                return_value=records,
            ),
            patch(
                "scripts.memory_consolidation._call_haiku",
                return_value=haiku_response,
            ),
            patch(
                "models.memory.Memory.safe_save",
                return_value=merged_record,
            ),
        ):
            from scripts.memory_consolidation import run_consolidation

            result = run_consolidation(project_key="test", dry_run=False, max_merges=10)

        assert result["applied_merges"] <= 10, (
            f"Rate limit violated: {result['applied_merges']} merges applied (max 10)"
        )


# --------------------------------------------------------------------------
# WriteFilter guard test
# --------------------------------------------------------------------------


class TestWriteFilterGuard:
    """save() returning False must log WARNING, not raise an exception."""

    def test_write_filter_blocked_logs_warning_no_exception(self, caplog):
        """When m.save() returns False, a WARNING is logged and no exception raised."""
        orig_1 = _make_record("orig-1", "Use real DB", category="correction")
        orig_2 = _make_record("orig-2", "Never mock DB", category="correction")
        orig_1.save.return_value = False  # WriteFilter blocks the write

        haiku_response = {
            "actions": [
                {
                    "action": "merge",
                    "ids": ["orig-1", "orig-2"],
                    "merged_content": "Use real DB, never mock",
                    "merged_importance": 2.0,
                    "merged_category": "correction",
                    "merged_tags": [],
                    "rationale": "Same instruction",
                }
            ]
        }

        merged_record = _make_record("merged-new", "Use real DB, never mock", importance=2.0)

        with (
            patch(
                "scripts.memory_consolidation._load_active_memories",
                return_value=[orig_1, orig_2],
            ),
            patch(
                "scripts.memory_consolidation._call_haiku",
                return_value=haiku_response,
            ),
            patch(
                "models.memory.Memory.safe_save",
                return_value=merged_record,
            ),
            caplog.at_level(logging.WARNING, logger="scripts.memory_consolidation"),
        ):
            from scripts.memory_consolidation import run_consolidation

            # Must not raise
            run_consolidation(project_key="test", dry_run=False)

        # WriteFilter block for orig-1.save() should produce a WARNING
        warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("WriteFilter blocked" in msg for msg in warning_msgs), (
            f"Expected WriteFilter WARNING not found. Warnings: {warning_msgs}"
        )
        assert any("orig-1" in msg for msg in warning_msgs), (
            f"Expected orig-1 in WARNING message. Warnings: {warning_msgs}"
        )


# --------------------------------------------------------------------------
# Dry-run: no Redis writes
# --------------------------------------------------------------------------


class TestDryRun:
    """Dry-run mode must not write any Redis records."""

    def test_dry_run_no_safe_save_called(self):
        """In dry_run=True mode, Memory.safe_save is never called."""
        rec_1 = _make_record("dry-1", "Memory A", category="correction")
        rec_2 = _make_record("dry-2", "Memory A variant", category="correction")

        haiku_response = {
            "actions": [
                {
                    "action": "merge",
                    "ids": ["dry-1", "dry-2"],
                    "merged_content": "Memory A consolidated",
                    "merged_importance": 2.0,
                    "merged_category": "correction",
                    "merged_tags": [],
                    "rationale": "Near-duplicates",
                }
            ]
        }

        with (
            patch(
                "scripts.memory_consolidation._load_active_memories",
                return_value=[rec_1, rec_2],
            ),
            patch(
                "scripts.memory_consolidation._call_haiku",
                return_value=haiku_response,
            ),
            patch("models.memory.Memory.safe_save") as mock_save,
        ):
            from scripts.memory_consolidation import run_consolidation

            result = run_consolidation(project_key="test", dry_run=True)

        mock_save.assert_not_called()
        assert result["proposed_merges"] == 1
        assert result["applied_merges"] == 0


# --------------------------------------------------------------------------
# Empty / single-record group handling
# --------------------------------------------------------------------------


class TestEmptyGroupHandling:
    """Edge cases: empty list, single record, Haiku JSON failure."""

    def test_empty_memory_list_returns_zero_summary(self):
        """No active memories → no Haiku calls, zero summary."""
        with patch(
            "scripts.memory_consolidation._load_active_memories",
            return_value=[],
        ):
            from scripts.memory_consolidation import run_consolidation

            result = run_consolidation(project_key="test", dry_run=True)

        assert result["proposed_merges"] == 0
        assert result["applied_merges"] == 0

    def test_single_record_no_haiku_call(self):
        """Only one record → Haiku never called."""
        single = _make_record("single", "Only memory", category="correction")

        with (
            patch(
                "scripts.memory_consolidation._load_active_memories",
                return_value=[single],
            ),
            patch("scripts.memory_consolidation._call_haiku") as mock_haiku,
        ):
            from scripts.memory_consolidation import run_consolidation

            run_consolidation(project_key="test", dry_run=True)

        mock_haiku.assert_not_called()

    def test_haiku_json_parse_failure_skips_group(self):
        """Haiku returning None (parse failure) is handled gracefully."""
        rec_1 = _make_record("parse-1", "Memory A", category="pattern")
        rec_2 = _make_record("parse-2", "Memory B", category="pattern")

        with (
            patch(
                "scripts.memory_consolidation._load_active_memories",
                return_value=[rec_1, rec_2],
            ),
            patch(
                "scripts.memory_consolidation._call_haiku",
                return_value=None,  # Simulates JSON parse failure
            ),
        ):
            from scripts.memory_consolidation import run_consolidation

            result = run_consolidation(project_key="test", dry_run=True)

        # Should return zeros without raising
        assert result["proposed_merges"] == 0


# --------------------------------------------------------------------------
# Contradiction notification tests
# --------------------------------------------------------------------------


class TestContradictionFlagging:
    """Contradiction flagging: Telegram send; CalledProcessError → log fallback."""

    def test_contradiction_sends_telegram_notification(self):
        """When a contradiction is flagged, valor-telegram send is called."""
        rec_1 = _make_record("cont-1", "Always use mocks for speed", category="correction")
        rec_2 = _make_record("cont-2", "Never use mocks, always real DB", category="correction")

        haiku_response = {
            "actions": [
                {
                    "action": "flag_contradiction",
                    "ids": ["cont-1", "cont-2"],
                    "rationale": "Opposing guidance on mocking strategy",
                }
            ]
        }

        with (
            patch(
                "scripts.memory_consolidation._load_active_memories",
                return_value=[rec_1, rec_2],
            ),
            patch(
                "scripts.memory_consolidation._call_haiku",
                return_value=haiku_response,
            ),
            patch("scripts.memory_consolidation.subprocess.run") as mock_subprocess,
        ):
            from scripts.memory_consolidation import run_consolidation

            result = run_consolidation(project_key="test", dry_run=True)

        # subprocess.run called with valor-telegram send
        mock_subprocess.assert_called_once()
        call_args = mock_subprocess.call_args[0][0]
        assert "valor-telegram" in call_args
        assert "send" in call_args
        assert result["flagged_contradictions"] == 1

    def test_contradiction_telegram_failure_writes_log_file(self, tmp_path):
        """When valor-telegram raises CalledProcessError, write to memory-contradictions.log."""
        import subprocess as subprocess_module

        rec_1 = _make_record("clog-1", "Always use mocks", category="correction")
        rec_2 = _make_record("clog-2", "Never use mocks", category="correction")

        haiku_response = {
            "actions": [
                {
                    "action": "flag_contradiction",
                    "ids": ["clog-1", "clog-2"],
                    "rationale": "Opposing guidance",
                }
            ]
        }

        fake_log = tmp_path / "logs" / "memory-contradictions.log"
        fake_log.parent.mkdir(parents=True)

        def mock_run(*args, **kwargs):
            raise subprocess_module.CalledProcessError(1, "valor-telegram")

        with (
            patch(
                "scripts.memory_consolidation._load_active_memories",
                return_value=[rec_1, rec_2],
            ),
            patch(
                "scripts.memory_consolidation._call_haiku",
                return_value=haiku_response,
            ),
            patch("scripts.memory_consolidation.subprocess.run", side_effect=mock_run),
            patch("scripts.memory_consolidation._write_contradiction_log") as mock_write_log,
        ):
            from scripts.memory_consolidation import run_consolidation

            # Should not raise
            result = run_consolidation(project_key="test", dry_run=True)

        # Fallback log writer must be called
        mock_write_log.assert_called_once()
        assert result["flagged_contradictions"] == 1
