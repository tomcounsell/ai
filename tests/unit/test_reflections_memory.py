"""Tests for reflections/memory_management.py.

Tests cover:
- run_memory_decay_prune: dry_run mode, cap enforcement, empty queryset
- run_memory_quality_audit: empty queryset, low-confidence flagging
- run_knowledge_reindex: missing vault dir, KnowledgeDocument unavailable
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch


def run_async(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


def assert_valid_result(result: dict, expected_status: str = "ok") -> None:
    """Assert the result dict has required keys."""
    assert isinstance(result, dict)
    assert "status" in result
    assert "findings" in result
    assert "summary" in result
    assert result["status"] in ("ok", "error", "skipped")
    assert isinstance(result["findings"], list)
    assert isinstance(result["summary"], str)


# ============================================================
# run_memory_decay_prune
# ============================================================


class TestMemoryDecayPrune:
    """Tests for run_memory_decay_prune()."""

    def test_dry_run_default(self):
        """Default mode is dry_run=True — no deletions, log findings."""
        from reflections.memory_management import run_memory_decay_prune

        # Create a candidate memory: low importance, zero access, old
        old_time = time.time() - (40 * 86400)  # 40 days ago
        mock_memory = MagicMock()
        mock_memory.memory_id = "mem_001"
        mock_memory.importance = 0.05  # below WF_MIN_THRESHOLD
        mock_memory.access_count = 0
        mock_memory.superseded_by = ""
        mock_memory.created_at = MagicMock()
        mock_memory.created_at.timestamp.return_value = old_time

        with (
            patch("models.memory.Memory") as mock_model,
            patch.dict("os.environ", {"MEMORY_DECAY_PRUNE_APPLY": "false"}),
        ):
            mock_model.query.all.return_value = [mock_memory]
            result = run_async(run_memory_decay_prune())

        assert_valid_result(result)
        assert "DRY RUN" in result["summary"]
        # Should NOT have called delete()
        mock_memory.delete.assert_not_called()

    def test_apply_mode_deletes_candidates(self):
        """MEMORY_DECAY_PRUNE_APPLY=true causes actual deletion."""
        from reflections.memory_management import run_memory_decay_prune

        old_time = time.time() - (40 * 86400)
        mock_memory = MagicMock()
        mock_memory.memory_id = "mem_002"
        mock_memory.importance = 0.05
        mock_memory.access_count = 0
        mock_memory.superseded_by = ""
        mock_memory.created_at = MagicMock()
        mock_memory.created_at.timestamp.return_value = old_time

        with (
            patch("models.memory.Memory") as mock_model,
            patch.dict("os.environ", {"MEMORY_DECAY_PRUNE_APPLY": "true"}),
        ):
            mock_model.query.all.return_value = [mock_memory]
            result = run_async(run_memory_decay_prune())

        assert_valid_result(result)
        assert "APPLIED" in result["summary"]
        mock_memory.delete.assert_called_once()

    def test_cap_at_50_deletions(self):
        """Caps at MAX_PRUNE_PER_RUN (50) deletions even if more candidates exist."""
        from reflections.memory_management import MAX_PRUNE_PER_RUN, run_memory_decay_prune

        old_time = time.time() - (40 * 86400)

        def make_candidate(i):
            m = MagicMock()
            m.memory_id = f"mem_{i}"
            m.importance = 0.05
            m.access_count = 0
            m.superseded_by = ""
            m.created_at = MagicMock()
            m.created_at.timestamp.return_value = old_time
            return m

        candidates = [make_candidate(i) for i in range(MAX_PRUNE_PER_RUN + 20)]

        with (
            patch("models.memory.Memory") as mock_model,
            patch.dict("os.environ", {"MEMORY_DECAY_PRUNE_APPLY": "true"}),
        ):
            mock_model.query.all.return_value = candidates
            result = run_async(run_memory_decay_prune())

        assert_valid_result(result)
        # Count delete() calls — should be exactly MAX_PRUNE_PER_RUN
        delete_calls = sum(1 for m in candidates if m.delete.called)
        assert delete_calls == MAX_PRUNE_PER_RUN

    def test_empty_queryset(self):
        """Handles empty Memory queryset without error."""
        from reflections.memory_management import run_memory_decay_prune

        with patch("models.memory.Memory") as mock_model:
            mock_model.query.all.return_value = []
            result = run_async(run_memory_decay_prune())

        assert_valid_result(result)

    def test_skips_high_importance_memories(self):
        """Memories with importance >= 7.0 are exempt from pruning."""
        from reflections.memory_management import run_memory_decay_prune

        old_time = time.time() - (40 * 86400)
        important_memory = MagicMock()
        important_memory.memory_id = "mem_important"
        important_memory.importance = 7.5  # above exempt threshold
        important_memory.access_count = 0
        important_memory.superseded_by = ""
        important_memory.created_at = MagicMock()
        important_memory.created_at.timestamp.return_value = old_time

        with (
            patch("models.memory.Memory") as mock_model,
            patch.dict("os.environ", {"MEMORY_DECAY_PRUNE_APPLY": "true"}),
        ):
            mock_model.query.all.return_value = [important_memory]
            result = run_async(run_memory_decay_prune())

        assert_valid_result(result)
        important_memory.delete.assert_not_called()

    def test_skips_memories_with_access(self):
        """Memories with access_count > 0 are exempt from pruning."""
        from reflections.memory_management import run_memory_decay_prune

        old_time = time.time() - (40 * 86400)
        accessed_memory = MagicMock()
        accessed_memory.memory_id = "mem_accessed"
        accessed_memory.importance = 0.05
        accessed_memory.access_count = 3  # has been accessed
        accessed_memory.superseded_by = ""
        accessed_memory.created_at = MagicMock()
        accessed_memory.created_at.timestamp.return_value = old_time

        with (
            patch("models.memory.Memory") as mock_model,
            patch.dict("os.environ", {"MEMORY_DECAY_PRUNE_APPLY": "true"}),
        ):
            mock_model.query.all.return_value = [accessed_memory]
            result = run_async(run_memory_decay_prune())

        assert_valid_result(result)
        accessed_memory.delete.assert_not_called()

    def test_handles_delete_failure_gracefully(self):
        """Continues pruning other memories if one delete() fails."""
        from reflections.memory_management import run_memory_decay_prune

        old_time = time.time() - (40 * 86400)

        def make_candidate(i, fail=False):
            m = MagicMock()
            m.memory_id = f"mem_{i}"
            m.importance = 0.05
            m.access_count = 0
            m.superseded_by = ""
            m.created_at = MagicMock()
            m.created_at.timestamp.return_value = old_time
            if fail:
                m.delete.side_effect = Exception("already deleted")
            return m

        c1 = make_candidate(1, fail=True)
        c2 = make_candidate(2)

        with (
            patch("models.memory.Memory") as mock_model,
            patch.dict("os.environ", {"MEMORY_DECAY_PRUNE_APPLY": "true"}),
        ):
            mock_model.query.all.return_value = [c1, c2]
            result = run_async(run_memory_decay_prune())

        assert_valid_result(result)
        # c2 should still have been attempted
        c2.delete.assert_called_once()

    def test_redis_unavailable_returns_error(self):
        """Returns error dict when Redis is unavailable."""
        from reflections.memory_management import run_memory_decay_prune

        with patch("models.memory.Memory") as mock_model:
            mock_model.query.all.side_effect = Exception("redis unavailable")
            result = run_async(run_memory_decay_prune())

        assert result["status"] == "error"


# ============================================================
# run_memory_quality_audit
# ============================================================


class TestMemoryQualityAudit:
    """Tests for run_memory_quality_audit()."""

    def test_empty_queryset(self):
        """Handles empty Memory queryset without error."""
        from reflections.memory_management import run_memory_quality_audit

        with patch("models.memory.Memory") as mock_model:
            mock_model.query.all.return_value = []
            result = run_async(run_memory_quality_audit())

        assert_valid_result(result)
        assert "no memories to audit" in result["summary"]

    def test_flags_zero_access_old_memories(self):
        """Flags memories with zero access after 30 days."""
        from reflections.memory_management import run_memory_quality_audit

        old_time = time.time() - (40 * 86400)
        old_memory = MagicMock()
        old_memory.memory_id = "mem_old"
        old_memory.importance = 1.0
        old_memory.access_count = 0
        old_memory.superseded_by = ""
        old_memory.created_at = MagicMock()
        old_memory.created_at.timestamp.return_value = old_time
        old_memory.confidence = 0.5

        with patch("models.memory.Memory") as mock_model:
            mock_model.query.all.return_value = [old_memory]
            result = run_async(run_memory_quality_audit())

        assert_valid_result(result)
        zero_access_findings = [f for f in result["findings"] if "Zero-access" in f]
        assert len(zero_access_findings) >= 1

    def test_flags_low_confidence_memories(self):
        """Flags memories with very low confidence."""
        from reflections.memory_management import run_memory_quality_audit

        new_time = time.time() - (1 * 86400)  # 1 day old (won't be flagged for zero-access)
        low_conf_memory = MagicMock()
        low_conf_memory.memory_id = "mem_low_conf"
        low_conf_memory.importance = 1.0
        low_conf_memory.access_count = 5
        low_conf_memory.superseded_by = ""
        low_conf_memory.created_at = MagicMock()
        low_conf_memory.created_at.timestamp.return_value = new_time
        low_conf_memory.confidence = 0.05  # very low confidence

        with patch("models.memory.Memory") as mock_model:
            mock_model.query.all.return_value = [low_conf_memory]
            result = run_async(run_memory_quality_audit())

        assert_valid_result(result)
        low_conf_findings = [f for f in result["findings"] if "Low-confidence" in f]
        assert len(low_conf_findings) >= 1

    def test_skips_superseded_memories(self):
        """Skips memories that are already superseded."""
        from reflections.memory_management import run_memory_quality_audit

        old_time = time.time() - (40 * 86400)
        superseded = MagicMock()
        superseded.memory_id = "mem_superseded"
        superseded.importance = 0.05
        superseded.access_count = 0
        superseded.superseded_by = "mem_new"  # superseded
        superseded.created_at = MagicMock()
        superseded.created_at.timestamp.return_value = old_time
        superseded.confidence = 0.05

        with patch("models.memory.Memory") as mock_model:
            mock_model.query.all.return_value = [superseded]
            result = run_async(run_memory_quality_audit())

        assert_valid_result(result)
        # Should not flag the superseded memory
        findings_with_id = [f for f in result["findings"] if "mem_superseded" in f]
        assert len(findings_with_id) == 0

    def test_redis_unavailable_returns_error(self):
        """Returns error dict when Redis is unavailable."""
        from reflections.memory_management import run_memory_quality_audit

        with patch("models.memory.Memory") as mock_model:
            mock_model.query.all.side_effect = Exception("redis unavailable")
            result = run_async(run_memory_quality_audit())

        assert result["status"] == "error"


# ============================================================
# run_knowledge_reindex
# ============================================================


class TestKnowledgeReindex:
    """Tests for run_knowledge_reindex()."""

    def test_missing_vault_returns_skipped(self, tmp_path):
        """Returns skipped result when ~/src/work-vault/ doesn't exist."""
        from reflections.memory_management import run_knowledge_reindex

        fake_home = tmp_path  # work-vault doesn't exist here
        with patch("pathlib.Path.home", return_value=fake_home):
            result = run_async(run_knowledge_reindex())

        assert_valid_result(result)
        assert "not found" in result["summary"]

    def test_knowledge_document_unavailable_returns_stub(self, tmp_path):
        """Returns stub result when tools.knowledge.indexer is not available."""
        from reflections.memory_management import run_knowledge_reindex

        vault_path = tmp_path / "src" / "work-vault"
        vault_path.mkdir(parents=True)

        import sys

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch.dict(sys.modules, {"tools.knowledge.indexer": None}),
        ):
            result = run_async(run_knowledge_reindex())

        assert_valid_result(result)
        assert "not available" in result["summary"] or "not found" in result["summary"]

    def test_successful_reindex(self, tmp_path):
        """Returns valid result when reindex_vault succeeds."""
        from reflections.memory_management import run_knowledge_reindex

        vault_path = tmp_path / "src" / "work-vault"
        vault_path.mkdir(parents=True)

        mock_indexer = MagicMock()
        mock_indexer.reindex_vault.return_value = {"indexed": 10, "skipped": 5, "errors": []}

        import sys

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch.dict(sys.modules, {"tools.knowledge.indexer": mock_indexer}),
        ):
            result = run_async(run_knowledge_reindex())

        assert_valid_result(result)
