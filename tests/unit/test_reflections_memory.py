"""Tests for reflections/memory_management.py.

Tests cover:
- run_memory_decay_prune: dry_run mode, cap enforcement, empty queryset
- run_memory_quality_audit: empty queryset, low-confidence flagging
- run_knowledge_reindex: missing vault dir, KnowledgeDocument unavailable
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _popoto_has_sweep_stale_tempfiles() -> bool:
    """Probe whether popoto>=1.6.0 EmbeddingField is installed."""
    try:
        from popoto.fields.embedding_field import EmbeddingField

        return hasattr(EmbeddingField, "sweep_stale_tempfiles")
    except ImportError:
        return False


_REQUIRES_POPOTO_1_6 = pytest.mark.skipif(
    not _popoto_has_sweep_stale_tempfiles(),
    reason="popoto<1.6.0 — EmbeddingField.sweep_stale_tempfiles not yet available",
)


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
        mock_memory.created_at = old_time

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
        mock_memory.created_at = old_time

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
            m.created_at = old_time
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
        important_memory.created_at = old_time

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
        accessed_memory.created_at = old_time

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
            m.created_at = old_time
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


def _make_memory(
    *,
    memory_id="mem_x",
    agent_id="extraction-tg_test",
    content="test content",
    importance=1.0,
    access_count=0,
    superseded_by="",
    created_at=None,
    confidence=0.5,
    metadata=None,
):
    """Build a MagicMock Memory record matching the production field shape."""
    if created_at is None:
        # Default to 1 hour old — well inside both Layer 2's last-7d and Layer 3's
        # last-24h windows, avoiding the boundary flake when records sit exactly on
        # ``now - 86400``.
        created_at = time.time() - 3600
    m = MagicMock()
    m.memory_id = memory_id
    m.agent_id = agent_id
    m.content = content
    m.importance = importance
    m.access_count = access_count
    m.superseded_by = superseded_by
    m.created_at = created_at
    m.confidence = confidence
    m.metadata = metadata if metadata is not None else {}
    # save() returns truthy by default; tests override to False to simulate WriteFilter veto
    m.save.return_value = True
    return m


# ----------- Layer 0: Legacy zero-access + low-confidence (preserved) -----------


class TestMemoryHealthAuditLayer0:
    """Layer 0 — legacy zero-access + low-confidence flagging (read-only)."""

    def test_empty_queryset(self):
        """Handles empty Memory queryset without error."""
        from reflections.memory_management import run_memory_quality_audit

        with patch("models.memory.Memory") as mock_model:
            mock_model.query.all.return_value = []
            result = run_async(run_memory_quality_audit())

        assert_valid_result(result)
        assert "no memories to audit" in result["summary"]

    def test_zero_access_flag(self):
        """Flags memories with zero access after 30 days."""
        from reflections.memory_management import run_memory_quality_audit

        old_time = time.time() - (40 * 86400)
        # Use non-extraction agent_id so Layer 1 doesn't touch it
        old_memory = _make_memory(
            memory_id="mem_old",
            agent_id="human-save",
            content="some old observation",
            importance=1.0,
            access_count=0,
            created_at=old_time,
            confidence=0.5,
        )

        with patch("models.memory.Memory") as mock_model:
            mock_model.query.all.return_value = [old_memory]
            result = run_async(run_memory_quality_audit())

        assert_valid_result(result)
        zero_access_findings = [f for f in result["findings"] if "Zero-access" in f]
        assert len(zero_access_findings) >= 1

    def test_low_confidence_flag(self):
        """Flags memories with very low confidence."""
        from reflections.memory_management import run_memory_quality_audit

        new_time = time.time() - (1 * 86400)  # 1 day old (won't be flagged for zero-access)
        low_conf_memory = _make_memory(
            memory_id="mem_low_conf",
            agent_id="human-save",
            importance=1.0,
            access_count=5,
            created_at=new_time,
            confidence=0.05,
        )

        with patch("models.memory.Memory") as mock_model:
            mock_model.query.all.return_value = [low_conf_memory]
            result = run_async(run_memory_quality_audit())

        assert_valid_result(result)
        low_conf_findings = [f for f in result["findings"] if "Low-confidence" in f]
        assert len(low_conf_findings) >= 1

    def test_skips_superseded_memories(self):
        """Skips memories that are already superseded for Layer 0 flagging."""
        from reflections.memory_management import run_memory_quality_audit

        old_time = time.time() - (40 * 86400)
        superseded = _make_memory(
            memory_id="mem_superseded",
            agent_id="human-save",
            importance=0.05,
            access_count=0,
            superseded_by="mem_new",
            created_at=old_time,
            confidence=0.05,
        )

        with patch("models.memory.Memory") as mock_model:
            mock_model.query.all.return_value = [superseded]
            result = run_async(run_memory_quality_audit())

        assert_valid_result(result)
        findings_with_id = [f for f in result["findings"] if "mem_superseded" in f]
        assert len(findings_with_id) == 0

    def test_redis_unavailable_returns_error(self):
        """Returns error dict when Redis is unavailable."""
        from reflections.memory_management import run_memory_quality_audit

        with patch("models.memory.Memory") as mock_model:
            mock_model.query.all.side_effect = Exception("redis unavailable")
            result = run_async(run_memory_quality_audit())

        assert result["status"] == "error"


# ----------- Layer 1: deterministic supersede via _looks_like_refusal -----------


class TestMemoryHealthAuditLayer1:
    """Layer 1 — supersedes extraction-* records matching _looks_like_refusal."""

    def test_supersedes_refusal_records_happy_path(self):
        """Refusal-content extraction-* records get superseded_by set."""
        from reflections.memory_management import (
            CLEANUP_RATIONALE,
            CLEANUP_SUPERSEDED_BY,
            run_memory_quality_audit,
        )

        junk = _make_memory(
            memory_id="mem_junk",
            agent_id="extraction-tg_test_1",
            content="There is no agent session response to analyze.",
        )

        with (
            patch("models.memory.Memory") as mock_model,
            patch(
                "reflections.memory_management._file_anomaly_issue",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            mock_model.query.all.return_value = [junk]
            result = run_async(run_memory_quality_audit())

        assert_valid_result(result)
        assert junk.superseded_by == CLEANUP_SUPERSEDED_BY
        assert junk.superseded_by_rationale == CLEANUP_RATIONALE
        junk.save.assert_called()

    def test_blast_radius_gate_non_extraction_untouched(self):
        """Records whose agent_id is not 'extraction-*' are never superseded by Layer 1."""
        from reflections.memory_management import run_memory_quality_audit

        # All have refusal content, but only the extraction-* one should be superseded.
        human = _make_memory(
            memory_id="mem_human",
            agent_id="human-save",
            content="There is no agent session response to analyze.",
        )
        post_merge = _make_memory(
            memory_id="mem_post_merge",
            agent_id="post-merge-learning",
            content="There is no agent session response to analyze.",
        )
        telegram = _make_memory(
            memory_id="mem_tg",
            agent_id="telegram-179144806",
            content="There is no agent session response to analyze.",
        )
        extraction = _make_memory(
            memory_id="mem_ext",
            agent_id="extraction-tg_test_1",
            content="There is no agent session response to analyze.",
        )

        with (
            patch("models.memory.Memory") as mock_model,
            patch(
                "reflections.memory_management._file_anomaly_issue",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            mock_model.query.all.return_value = [human, post_merge, telegram, extraction]
            run_async(run_memory_quality_audit())

        human.save.assert_not_called()
        post_merge.save.assert_not_called()
        telegram.save.assert_not_called()
        extraction.save.assert_called()

    def test_caps_at_50_per_run(self):
        """Even with 100+ junk records, no more than MAX_LAYER1_SUPERSEDES_PER_RUN are written."""
        from reflections.memory_management import (
            MAX_LAYER1_SUPERSEDES_PER_RUN,
            run_memory_quality_audit,
        )

        junks = [
            _make_memory(
                memory_id=f"mem_junk_{i}",
                agent_id=f"extraction-session_{i}",
                content="There is no agent session response to analyze.",
            )
            for i in range(MAX_LAYER1_SUPERSEDES_PER_RUN + 20)
        ]

        with (
            patch("models.memory.Memory") as mock_model,
            patch(
                "reflections.memory_management._file_anomaly_issue",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            mock_model.query.all.return_value = junks
            run_async(run_memory_quality_audit())

        save_calls = sum(1 for j in junks if j.save.called)
        assert save_calls == MAX_LAYER1_SUPERSEDES_PER_RUN

    def test_skips_already_superseded_records(self):
        """Already-superseded records are not re-superseded (idempotency)."""
        from reflections.memory_management import run_memory_quality_audit

        already = _make_memory(
            memory_id="mem_already",
            agent_id="extraction-tg_test_1",
            content="There is no agent session response to analyze.",
            superseded_by="cleanup-junk-extraction",
        )

        with (
            patch("models.memory.Memory") as mock_model,
            patch(
                "reflections.memory_management._file_anomaly_issue",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            mock_model.query.all.return_value = [already]
            run_async(run_memory_quality_audit())

        already.save.assert_not_called()

    def test_sets_exact_superseded_by_value_and_rationale(self):
        """Pin the exact constants — drift would silently break the cleanup convention."""
        from reflections.memory_management import (
            CLEANUP_RATIONALE,
            CLEANUP_SUPERSEDED_BY,
        )

        assert CLEANUP_SUPERSEDED_BY == "cleanup-junk-extraction"
        assert CLEANUP_RATIONALE == "auto-cleanup: refusal/json-shrapnel from issue #1212"

    def test_per_record_save_failure_does_not_abort_layer(self):
        """One save() raising must not prevent later records from being processed."""
        from reflections.memory_management import run_memory_quality_audit

        bad = _make_memory(
            memory_id="mem_bad",
            agent_id="extraction-tg_test_1",
            content="There is no agent session response to analyze.",
        )
        bad.save.side_effect = Exception("simulated redis blip")

        good = _make_memory(
            memory_id="mem_good",
            agent_id="extraction-tg_test_2",
            content="There is no agent session response to analyze.",
        )

        with (
            patch("models.memory.Memory") as mock_model,
            patch(
                "reflections.memory_management._file_anomaly_issue",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            mock_model.query.all.return_value = [bad, good]
            result = run_async(run_memory_quality_audit())

        assert_valid_result(result)
        good.save.assert_called()


# ----------- Layer 2: heuristic anomaly detection -----------


class TestMemoryHealthAuditLayer2:
    """Layer 2 — 4 anomaly signals computed against post-Layer-1 corpus."""

    def test_default_category_skew_above_threshold_files_issue(self):
        """When >70% of last-7d extraction-* records have no category, file an issue."""
        from reflections.memory_management import run_memory_quality_audit

        # 10 records, 8 with no category, 2 with categorized — 80% > 70% threshold
        records = []
        for i in range(8):
            records.append(
                _make_memory(
                    memory_id=f"mem_nocat_{i}",
                    agent_id=f"extraction-s{i}",
                    content="legitimate observation about something",
                    metadata={},
                )
            )
        for i in range(2):
            records.append(
                _make_memory(
                    memory_id=f"mem_cat_{i}",
                    agent_id=f"extraction-s{i + 100}",
                    content="another legit obs",
                    metadata={"category": "correction"},
                )
            )

        with (
            patch("models.memory.Memory") as mock_model,
            patch(
                "reflections.memory_management._file_anomaly_issue",
                new_callable=AsyncMock,
            ) as mock_file,
        ):
            mock_file.return_value = True
            mock_model.query.all.return_value = records
            run_async(run_memory_quality_audit())

        signals_filed = [c.kwargs.get("signal_name") for c in mock_file.call_args_list]
        assert "category-default-skew" in signals_filed

    def test_importance_1_skew_files_issue(self):
        """When >85% of last-7d records have importance==1.0, file an issue."""
        from reflections.memory_management import run_memory_quality_audit

        records = []
        # 9 at importance=1.0, 1 at importance=4.0 — 90% > 85% threshold
        for i in range(9):
            records.append(
                _make_memory(
                    memory_id=f"mem_imp1_{i}",
                    agent_id=f"extraction-s{i}",
                    content="some observation",
                    importance=1.0,
                    metadata={"category": "pattern"},
                )
            )
        records.append(
            _make_memory(
                memory_id="mem_imp4",
                agent_id="extraction-s100",
                content="another observation",
                importance=4.0,
                metadata={"category": "correction"},
            )
        )

        with (
            patch("models.memory.Memory") as mock_model,
            patch(
                "reflections.memory_management._file_anomaly_issue",
                new_callable=AsyncMock,
            ) as mock_file,
        ):
            mock_file.return_value = True
            mock_model.query.all.return_value = records
            run_async(run_memory_quality_audit())

        signals_filed = [c.kwargs.get("signal_name") for c in mock_file.call_args_list]
        assert "importance-1.0-skew" in signals_filed

    def test_agent_id_clustering_above_threshold_files_issue(self):
        """When a single agent_id produces >10 junk records superseded *this run*, file an issue."""
        from reflections.memory_management import run_memory_quality_audit

        # 12 refusal-content records all from same agent_id — Layer 1 will supersede them all,
        # then Layer 2 detects the cluster.
        records = [
            _make_memory(
                memory_id=f"mem_clust_{i}",
                agent_id="extraction-tg_valor_stuck",
                content="There is no agent session response to analyze.",
            )
            for i in range(12)
        ]

        with (
            patch("models.memory.Memory") as mock_model,
            patch(
                "reflections.memory_management._file_anomaly_issue",
                new_callable=AsyncMock,
            ) as mock_file,
        ):
            mock_file.return_value = True
            mock_model.query.all.return_value = records
            run_async(run_memory_quality_audit())

        signals_filed = [c.kwargs.get("signal_name") for c in mock_file.call_args_list]
        cluster_signals = [s for s in signals_filed if s.startswith("agent-id-cluster-")]
        assert len(cluster_signals) >= 1

    def test_agent_id_cluster_idempotent_on_backlog(self):
        """Records superseded in prior runs do NOT re-trigger the signal."""
        from reflections.memory_management import run_memory_quality_audit

        # All from same agent_id, but already superseded by prior runs.
        # Layer 1 will not supersede them again, so just_superseded_agent_ids is empty.
        records = [
            _make_memory(
                memory_id=f"mem_old_clust_{i}",
                agent_id="extraction-tg_valor_old_stuck",
                content="There is no agent session response to analyze.",
                superseded_by="cleanup-junk-extraction",  # already superseded
            )
            for i in range(15)
        ]

        with (
            patch("models.memory.Memory") as mock_model,
            patch(
                "reflections.memory_management._file_anomaly_issue",
                new_callable=AsyncMock,
            ) as mock_file,
        ):
            mock_file.return_value = True
            mock_model.query.all.return_value = records
            run_async(run_memory_quality_audit())

        signals_filed = [c.kwargs.get("signal_name") for c in mock_file.call_args_list]
        cluster_signals = [s for s in signals_filed if s.startswith("agent-id-cluster-")]
        # Backlog must NOT re-fire — agent-id-cluster signal is gated on records superseded
        # IN THIS RUN, not the cumulative backlog.
        assert len(cluster_signals) == 0

    def test_html_escape_rate_jump_files_issue(self):
        """When HTML escapes appear in >10% AND WoW jump >2x, file an issue."""
        from reflections.memory_management import run_memory_quality_audit

        now = time.time()
        records = []
        # Last 7d: 5/10 records have HTML escape — 50% ratio
        for i in range(5):
            records.append(
                _make_memory(
                    memory_id=f"mem_html_{i}",
                    agent_id=f"extraction-s{i}",
                    content=f"some content with &amp; and &lt;tag&gt; record {i}",
                    metadata={"category": "pattern"},
                    created_at=now - (1 * 86400),  # 1 day ago
                )
            )
        for i in range(5):
            records.append(
                _make_memory(
                    memory_id=f"mem_clean_{i}",
                    agent_id=f"extraction-s{i + 100}",
                    content=f"clean observation {i}",
                    metadata={"category": "pattern"},
                    created_at=now - (1 * 86400),
                )
            )
        # Prior week (7-14d ago): 0/5 have HTML escapes — 0% baseline
        for i in range(5):
            records.append(
                _make_memory(
                    memory_id=f"mem_prior_{i}",
                    agent_id=f"extraction-s{i + 200}",
                    content=f"clean prior observation {i}",
                    metadata={"category": "pattern"},
                    created_at=now - (10 * 86400),  # 10 days ago
                )
            )

        with (
            patch("models.memory.Memory") as mock_model,
            patch(
                "reflections.memory_management._file_anomaly_issue",
                new_callable=AsyncMock,
            ) as mock_file,
        ):
            mock_file.return_value = True
            mock_model.query.all.return_value = records
            run_async(run_memory_quality_audit())

        signals_filed = [c.kwargs.get("signal_name") for c in mock_file.call_args_list]
        assert "html-escape-rate" in signals_filed

    def test_only_counts_extraction_records(self):
        """Layer 2 signals must ignore non-extraction-* records."""
        from reflections.memory_management import run_memory_quality_audit

        # 10 human-save records with no category — should NOT trigger category-default-skew.
        records = [
            _make_memory(
                memory_id=f"mem_h_{i}",
                agent_id="human-save",
                content="legit human observation",
                importance=8.0,
                metadata={},
            )
            for i in range(10)
        ]

        with (
            patch("models.memory.Memory") as mock_model,
            patch(
                "reflections.memory_management._file_anomaly_issue",
                new_callable=AsyncMock,
            ) as mock_file,
        ):
            mock_file.return_value = True
            mock_model.query.all.return_value = records
            run_async(run_memory_quality_audit())

        signals_filed = [c.kwargs.get("signal_name") for c in mock_file.call_args_list]
        assert "category-default-skew" not in signals_filed

    def test_below_threshold_files_no_issue(self):
        """When all signals stay under threshold, no issue is filed."""
        from reflections.memory_management import run_memory_quality_audit

        # Healthy corpus: 10 records, 4 no-category (40% < 70%), varied importance.
        records = []
        for i in range(4):
            records.append(
                _make_memory(
                    memory_id=f"mem_ok_a_{i}",
                    agent_id=f"extraction-s{i}",
                    content=f"healthy obs {i}",
                    importance=1.0,
                    metadata={},
                )
            )
        for i in range(6):
            records.append(
                _make_memory(
                    memory_id=f"mem_ok_b_{i}",
                    agent_id=f"extraction-s{i + 100}",
                    content=f"healthy obs {i}",
                    importance=4.0,
                    metadata={"category": "correction"},
                )
            )

        with (
            patch("models.memory.Memory") as mock_model,
            patch(
                "reflections.memory_management._file_anomaly_issue",
                new_callable=AsyncMock,
            ) as mock_file,
        ):
            mock_file.return_value = True
            mock_model.query.all.return_value = records
            run_async(run_memory_quality_audit())

        # No issue should be filed because no signal crossed threshold.
        assert mock_file.call_count == 0

    def test_has_no_category_predicate_handles_both_shapes(self):
        """_has_no_category covers metadata={}, missing key, empty string, and 'default' literal."""
        from reflections.memory_management import _has_no_category

        # Line-based fallback path: metadata={}
        assert _has_no_category({}) is True
        # None metadata
        assert _has_no_category(None) is True
        # Missing category key
        assert _has_no_category({"file_paths": []}) is True
        # Empty category string
        assert _has_no_category({"category": ""}) is True
        # Legacy 'default' literal
        assert _has_no_category({"category": "default"}) is True
        # Real category — should be False
        assert _has_no_category({"category": "correction"}) is False
        assert _has_no_category({"category": "decision"}) is False
        assert _has_no_category({"category": "pattern"}) is False
        assert _has_no_category({"category": "surprise"}) is False


# ----------- Layer 3: Gemma classification (fail-soft) -----------


class TestMemoryHealthAuditLayer3:
    """Layer 3 — Gemma classification with wallclock budget and fail-soft."""

    def test_ollama_unavailable_fails_soft(self):
        """When ollama.chat raises ConnectionRefusedError, audit completes layers 0+1+2 cleanly."""
        from reflections.memory_management import run_memory_quality_audit

        records = [
            _make_memory(
                memory_id="mem_x",
                agent_id="extraction-s1",
                content="some content for layer 3",
                metadata={"category": "correction"},
            )
        ]

        def raise_connection_refused(*a, **kw):
            raise ConnectionRefusedError("ollama daemon down")

        with (
            patch("models.memory.Memory") as mock_model,
            patch(
                "reflections.memory_management._file_anomaly_issue",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "reflections.memory_management._gemma_classify",
                side_effect=raise_connection_refused,
            ),
        ):
            mock_model.query.all.return_value = records
            result = run_async(run_memory_quality_audit())

        # Audit must complete successfully despite Layer 3 failing.
        assert_valid_result(result)
        assert result["status"] == "ok"

    def test_ollama_classifies_junk_files_issue(self):
        """When >=3 records share an anomaly_signal, an issue is filed for that signal."""
        from reflections.memory_management import run_memory_quality_audit

        # Use importance=4.0 + categorized to avoid Layer 2 importance-1.0-skew firing.
        records = [
            _make_memory(
                memory_id=f"mem_l3_{i}",
                agent_id=f"extraction-s{i}",
                content=f"some weird content {i}",
                importance=4.0,
                metadata={"category": "correction"},
            )
            for i in range(5)
        ]

        with (
            patch("models.memory.Memory") as mock_model,
            patch(
                "reflections.memory_management._file_anomaly_issue",
                new_callable=AsyncMock,
            ) as mock_file,
            patch(
                "reflections.memory_management._gemma_classify",
                return_value={
                    "is_junk": True,
                    "anomaly_signal": "json-key-as-content",
                    "why": "test",
                },
            ),
        ):
            mock_file.return_value = True
            mock_model.query.all.return_value = records
            run_async(run_memory_quality_audit())

        signals_filed = [c.kwargs.get("signal_name") for c in mock_file.call_args_list]
        assert "gemma-json-key-as-content" in signals_filed

    def test_ollama_classifies_clean_no_issue(self):
        """When all gemma verdicts say is_junk=False, no Layer 3 issue is filed."""
        from reflections.memory_management import run_memory_quality_audit

        records = [
            _make_memory(
                memory_id=f"mem_l3c_{i}",
                agent_id=f"extraction-s{i}",
                content=f"valid observation {i}",
                metadata={"category": "correction"},
            )
            for i in range(5)
        ]

        verdicts = [{"is_junk": False, "anomaly_signal": None, "why": "clean"}] * 5

        with (
            patch("models.memory.Memory") as mock_model,
            patch(
                "reflections.memory_management._file_anomaly_issue",
                new_callable=AsyncMock,
            ) as mock_file,
            patch(
                "reflections.memory_management._gemma_classify",
                side_effect=verdicts,
            ),
        ):
            mock_file.return_value = True
            mock_model.query.all.return_value = records
            run_async(run_memory_quality_audit())

        signals_filed = [c.kwargs.get("signal_name") for c in mock_file.call_args_list]
        gemma_signals = [s for s in signals_filed if s and s.startswith("gemma-")]
        assert len(gemma_signals) == 0

    def test_layer3_skipped_in_summary_when_ollama_down(self):
        """Findings list mentions layer-3 skipped when ollama is unavailable for all calls."""
        from reflections.memory_management import run_memory_quality_audit

        records = [
            _make_memory(
                memory_id=f"mem_l3s_{i}",
                agent_id=f"extraction-s{i}",
                content=f"some content {i}",
                metadata={"category": "correction"},
            )
            for i in range(3)
        ]

        with (
            patch("models.memory.Memory") as mock_model,
            patch(
                "reflections.memory_management._file_anomaly_issue",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "reflections.memory_management._gemma_classify",
                return_value=None,  # all calls fail
            ),
        ):
            mock_model.query.all.return_value = records
            result = run_async(run_memory_quality_audit())

        layer3_findings = [f for f in result["findings"] if "layer-3" in f.lower()]
        assert any("skipped" in f.lower() or "unavailable" in f.lower() for f in layer3_findings)

    def test_wallclock_budget_exceeded_aborts_remaining(self):
        """A slow gemma call past the wallclock budget skips remaining records."""
        from reflections.memory_management import run_memory_quality_audit

        records = [
            _make_memory(
                memory_id=f"mem_l3w_{i}",
                agent_id=f"extraction-s{i}",
                content=f"some content {i}",
                metadata={"category": "correction"},
            )
            for i in range(20)
        ]

        # Patch _time.monotonic so the deadline is exceeded immediately. Asyncio's
        # event loop also calls monotonic internally — to be robust against that,
        # advance 100s per call so the deadline (start + 30s) is always exceeded
        # before the second loop iteration.
        counter = [0.0]

        def fake_monotonic():
            counter[0] += 100.0
            return counter[0]

        with (
            patch("models.memory.Memory") as mock_model,
            patch(
                "reflections.memory_management._file_anomaly_issue",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "reflections.memory_management._gemma_classify",
                return_value={"is_junk": False, "anomaly_signal": None, "why": "ok"},
            ),
            patch("reflections.memory_management._time.monotonic", side_effect=fake_monotonic),
        ):
            mock_model.query.all.return_value = records
            result = run_async(run_memory_quality_audit())

        # The audit should still complete cleanly with status=ok and a budget-exceeded finding.
        assert_valid_result(result)
        budget_findings = [
            f for f in result["findings"] if "wallclock" in f.lower() or "budget" in f.lower()
        ]
        assert len(budget_findings) >= 1

    def test_per_call_timeout_treated_as_unavailable(self):
        """A gemma call returning None via TimeoutError is counted as unavailable."""
        from reflections.memory_management import run_memory_quality_audit

        records = [
            _make_memory(
                memory_id=f"mem_l3t_{i}",
                agent_id=f"extraction-s{i}",
                content=f"some content {i}",
                metadata={"category": "correction"},
            )
            for i in range(3)
        ]

        # All calls return None (whether due to timeout, ollama down, etc.)
        with (
            patch("models.memory.Memory") as mock_model,
            patch(
                "reflections.memory_management._file_anomaly_issue",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "reflections.memory_management._gemma_classify",
                return_value=None,
            ),
        ):
            mock_model.query.all.return_value = records
            result = run_async(run_memory_quality_audit())

        assert_valid_result(result)
        # Status must be ok — Layer 3 unavailability doesn't break the audit.
        assert result["status"] == "ok"


# ----------- Duplicate-issue detection -----------


def _make_fake_subproc(stdout: bytes = b"", returncode: int = 0):
    """Return a fake object compatible with asyncio.create_subprocess_exec output.

    The real call returns a Process whose ``.communicate()`` is awaitable.
    AsyncMock gives us awaitable .communicate() automatically.
    """
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.returncode = returncode
    return proc


class TestDuplicateIssueDetection:
    """Tests for _find_open_audit_issue dedup logic."""

    def test_skips_filing_when_open_issue_exists_for_same_signal(self):
        """Existing open issue with matching title prefix → skip filing."""
        from reflections.memory_management import _file_anomaly_issue

        with patch(
            "reflections.memory_management._find_open_audit_issue",
            new_callable=AsyncMock,
            return_value=42,
        ):
            filed = run_async(
                _file_anomaly_issue(
                    signal_name="category-default-skew",
                    observed="80%",
                    threshold="> 70%",
                    sample_ids=["mem_a", "mem_b"],
                    evidence="some evidence",
                )
            )

        assert filed is False

    def test_files_new_issue_when_no_open_dup(self):
        """When _find_open_audit_issue returns None, _file_anomaly_issue invokes gh create."""
        from reflections.memory_management import _file_anomaly_issue

        fake_proc = _make_fake_subproc(returncode=0)
        with (
            patch(
                "reflections.memory_management._find_open_audit_issue",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "reflections.memory_management.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=fake_proc,
            ) as mock_exec,
        ):
            filed = run_async(
                _file_anomaly_issue(
                    signal_name="importance-1.0-skew",
                    observed="90%",
                    threshold="> 85%",
                    sample_ids=["mem_a"],
                    evidence="evidence text",
                )
            )

        assert filed is True
        mock_exec.assert_called_once()
        # Verify gh issue create command shape (positional args to create_subprocess_exec).
        cmd = list(mock_exec.call_args.args)
        assert "gh" in cmd
        assert "issue" in cmd
        assert "create" in cmd
        assert "--label" in cmd
        assert "memory" in cmd
        assert "investigation" in cmd

    def test_gh_search_failure_suppresses_filing_for_run(self):
        """When _find_open_audit_issue returns -1 (gh failed), _file_anomaly_issue skips."""
        from reflections.memory_management import _file_anomaly_issue

        with patch(
            "reflections.memory_management._find_open_audit_issue",
            new_callable=AsyncMock,
            return_value=-1,
        ):
            filed = run_async(
                _file_anomaly_issue(
                    signal_name="html-escape-rate",
                    observed="50%",
                    threshold="> 10%",
                    sample_ids=["mem_a"],
                    evidence="evidence",
                )
            )

        assert filed is False

    def test_gh_create_failure_does_not_crash_audit(self):
        """When gh issue create raises, _file_anomaly_issue returns False (does not raise)."""
        from reflections.memory_management import _file_anomaly_issue

        with (
            patch(
                "reflections.memory_management._find_open_audit_issue",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "reflections.memory_management.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                side_effect=Exception("gh CLI broke"),
            ),
        ):
            filed = run_async(
                _file_anomaly_issue(
                    signal_name="test",
                    observed="x",
                    threshold="y",
                    sample_ids=["a"],
                    evidence="z",
                )
            )

        assert filed is False

    def test_find_open_audit_issue_uses_title_prefix_only_no_label_filter(self):
        """_find_open_audit_issue dup-checks on title prefix only (resolves critique C4).

        Labels are descriptive but not part of the dup key — an operator who
        relabels (or strips labels from) an open issue must not cause re-filing.
        """
        from reflections.memory_management import _find_open_audit_issue

        fake_proc = _make_fake_subproc(stdout=b"[]", returncode=0)
        with patch(
            "reflections.memory_management.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=fake_proc,
        ) as mock_exec:
            run_async(_find_open_audit_issue("category-default-skew"))

            cmd = list(mock_exec.call_args.args)
            # The --label flag must not appear at all
            assert "--label" not in cmd
            # And the search query must not include any label: term either
            assert "--search" in cmd
            search_idx = cmd.index("--search")
            search_query = cmd[search_idx + 1]
            assert "label:" not in search_query
            # The structured title prefix is the sole dup-check key
            assert "[memory-audit] category-default-skew:" in search_query


# ----------- Quiescence + result shape -----------


class TestAuditQuiescence:
    """Tests that on a clean corpus, the audit produces well-formed zero-finding results."""

    def test_clean_corpus_healthy_extractor_zero_findings(self):
        """Clean corpus + healthy extractor reports zero supersedes/anomalies/issues."""
        from reflections.memory_management import run_memory_quality_audit

        # Healthy: extraction-* records with categories, varied importance, recent.
        records = [
            _make_memory(
                memory_id=f"mem_h_{i}",
                agent_id=f"extraction-s{i}",
                content=f"healthy observation {i}",
                importance=4.0 if i % 2 == 0 else 1.0,
                metadata={"category": "correction"},
            )
            for i in range(20)
        ]

        with (
            patch("models.memory.Memory") as mock_model,
            patch(
                "reflections.memory_management._file_anomaly_issue",
                new_callable=AsyncMock,
            ) as mock_file,
            patch(
                "reflections.memory_management._gemma_classify",
                return_value={"is_junk": False, "anomaly_signal": None, "why": "clean"},
            ),
        ):
            mock_file.return_value = True
            mock_model.query.all.return_value = records
            result = run_async(run_memory_quality_audit())

        assert_valid_result(result)
        # No issues filed
        assert mock_file.call_count == 0
        # Summary should report 0/0/0
        assert "0 superseded" in result["summary"]
        assert "0 anomalies" in result["summary"]
        assert "0 issues filed" in result["summary"]

    def test_returns_well_formed_result_dict(self):
        """Audit always returns {status, findings, summary} regardless of corpus state."""
        from reflections.memory_management import run_memory_quality_audit

        with patch("models.memory.Memory") as mock_model:
            mock_model.query.all.return_value = []
            result = run_async(run_memory_quality_audit())

        assert isinstance(result, dict)
        assert set(result.keys()) >= {"status", "findings", "summary"}
        assert result["status"] in ("ok", "error")

    def test_layer1_supersedes_never_trigger_issue(self):
        """No matter how many records Layer 1 supersedes, no Layer-1 issue is filed."""
        from reflections.memory_management import run_memory_quality_audit

        records = [
            _make_memory(
                memory_id=f"mem_junk_{i}",
                agent_id=f"extraction-s{i}",
                content="There is no agent session response to analyze.",
                metadata={"category": "correction"},
            )
            for i in range(5)
        ]

        with (
            patch("models.memory.Memory") as mock_model,
            patch(
                "reflections.memory_management._file_anomaly_issue",
                new_callable=AsyncMock,
            ) as mock_file,
            patch(
                "reflections.memory_management._gemma_classify",
                return_value=None,  # Layer 3 unavailable, so no Layer-3 issues
            ),
        ):
            mock_file.return_value = True
            mock_model.query.all.return_value = records
            run_async(run_memory_quality_audit())

        # Layer 1 supersedes are silent — no agent-id-cluster either (5 < 10 threshold).
        # Verify no signal called "layer1-*" or similar exists in any filed issues.
        signals_filed = [c.kwargs.get("signal_name") for c in mock_file.call_args_list]
        layer1_signals = [s for s in signals_filed if s and s.startswith("layer1-")]
        assert len(layer1_signals) == 0


# ----------- Cross-module dependency stability -----------


def test_run_memory_quality_audit_imports_looks_like_refusal_directly():
    """Lock in the cross-module dependency: reflections imports _looks_like_refusal directly."""
    import reflections.memory_management as mm
    from agent.memory_extraction import _looks_like_refusal

    # Module-level import, not just a function-local reuse.
    assert mm._looks_like_refusal is _looks_like_refusal


# ============================================================
# Public-seam rename: extract_json_payload (resolves critique C2)
# ============================================================


class TestPublicSeam:
    """Locks the agent.memory_extraction.extract_json_payload public surface."""

    def test_extract_json_payload_is_public(self):
        """The public name resolves and is callable."""
        import agent.memory_extraction as mm
        from agent.memory_extraction import extract_json_payload

        assert callable(extract_json_payload)
        assert hasattr(mm, "extract_json_payload")

    def test_underscore_name_retired(self):
        """The legacy underscore name no longer exists (no legacy code tolerance)."""
        import agent.memory_extraction as mm

        assert not hasattr(mm, "_extract_json_payload"), (
            "Legacy underscore name still present — public-seam rename incomplete"
        )

    def test_reflections_imports_public_name(self):
        """reflections.memory_management imports extract_json_payload from the public seam."""
        import reflections.memory_management as mm
        from agent.memory_extraction import extract_json_payload

        assert mm.extract_json_payload is extract_json_payload


# ============================================================
# Layer 1 escape hatch: MEMORY_AUDIT_LAYER1_CAP env var (resolves critique C5)
# ============================================================


class TestLayer1EscapeHatch:
    """Tests _resolve_layer1_cap() env-var resolver."""

    def test_unset_returns_default(self, monkeypatch):
        from reflections.memory_management import DEFAULT_LAYER1_CAP, _resolve_layer1_cap

        monkeypatch.delenv("MEMORY_AUDIT_LAYER1_CAP", raising=False)
        assert _resolve_layer1_cap() == DEFAULT_LAYER1_CAP == 50

    def test_zero_returns_none_uncapped(self, monkeypatch):
        from reflections.memory_management import _resolve_layer1_cap

        monkeypatch.setenv("MEMORY_AUDIT_LAYER1_CAP", "0")
        assert _resolve_layer1_cap() is None

    def test_positive_int_overrides(self, monkeypatch):
        from reflections.memory_management import _resolve_layer1_cap

        monkeypatch.setenv("MEMORY_AUDIT_LAYER1_CAP", "200")
        assert _resolve_layer1_cap() == 200

    def test_garbage_falls_back_to_default(self, monkeypatch):
        from reflections.memory_management import DEFAULT_LAYER1_CAP, _resolve_layer1_cap

        monkeypatch.setenv("MEMORY_AUDIT_LAYER1_CAP", "not-an-int")
        assert _resolve_layer1_cap() == DEFAULT_LAYER1_CAP

    def test_negative_falls_back_to_default(self, monkeypatch):
        from reflections.memory_management import DEFAULT_LAYER1_CAP, _resolve_layer1_cap

        monkeypatch.setenv("MEMORY_AUDIT_LAYER1_CAP", "-5")
        assert _resolve_layer1_cap() == DEFAULT_LAYER1_CAP


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


# ============================================================
# run_embedding_orphan_sweep (#1214)
# ============================================================


class TestEmbeddingOrphanSweep:
    """Tests for the new ``run_embedding_orphan_sweep`` reflection."""

    def test_stub_short_circuit_when_popoto_old(self):
        """If the installed Popoto lacks ``sweep_stale_tempfiles`` (the 1.5.x
        stub), the sweep must short-circuit with a clear "skipped" status.

        The capability probe ``hasattr(EmbeddingField, "sweep_stale_tempfiles")``
        is a deterministic across-version signal: 1.6.0 adds the method, 1.5.x
        does not. We use ``spec=[]`` to construct a fake EmbeddingField that
        explicitly does not expose that attribute (mirroring the 1.5.x API
        surface)."""
        from reflections.memory_management import run_embedding_orphan_sweep

        # spec=[] — empty allowlist of attributes; hasattr() returns False
        # for sweep_stale_tempfiles, mimicking the popoto 1.5.x stub surface.
        fake_field = MagicMock(spec=[])
        with patch(
            "popoto.fields.embedding_field.EmbeddingField",
            fake_field,
        ):
            result = run_async(run_embedding_orphan_sweep())

        assert_valid_result(result)
        assert result["status"] == "ok"
        assert any("popoto<1.6" in f for f in result["findings"]), (
            f"expected popoto<1.6 marker in findings, got {result['findings']}"
        )
        assert "skipped" in result["summary"].lower()

    @_REQUIRES_POPOTO_1_6
    def test_dry_run_default(self):
        """Default mode is dry-run — does not call garbage_collect/sweep."""
        # Real (non-stub) popoto installed, so the capability probe passes.
        # Dry-run path: must NOT invoke garbage_collect or sweep_stale_tempfiles.
        from popoto.fields.embedding_field import EmbeddingField

        from reflections.memory_management import run_embedding_orphan_sweep

        with (
            patch.dict("os.environ", {"EMBEDDING_ORPHAN_SWEEP_APPLY": "false"}),
            patch.object(EmbeddingField, "garbage_collect") as gc_spy,
            patch.object(EmbeddingField, "sweep_stale_tempfiles") as sweep_spy,
            patch(
                "scripts.popoto_index_cleanup._count_disk_orphans",
                return_value=42,
            ),
        ):
            result = run_async(run_embedding_orphan_sweep())

        assert_valid_result(result)
        assert result["status"] == "ok"
        assert gc_spy.call_count == 0, "dry-run must not call garbage_collect"
        assert sweep_spy.call_count == 0, "dry-run must not call sweep_stale_tempfiles"
        assert any("DRY RUN" in f for f in result["findings"])

    @_REQUIRES_POPOTO_1_6
    def test_apply_mode_calls_both_sweeps(self):
        """Apply mode invokes garbage_collect AND sweep_stale_tempfiles."""
        from popoto.fields.embedding_field import EmbeddingField

        from reflections.memory_management import run_embedding_orphan_sweep

        with (
            patch.dict("os.environ", {"EMBEDDING_ORPHAN_SWEEP_APPLY": "true"}),
            patch.object(EmbeddingField, "garbage_collect", return_value=7) as gc_spy,
            patch.object(EmbeddingField, "sweep_stale_tempfiles", return_value=3) as sweep_spy,
        ):
            result = run_async(run_embedding_orphan_sweep())

        assert_valid_result(result)
        assert result["status"] == "ok"
        assert gc_spy.call_count == 1
        assert sweep_spy.call_count == 1
        assert any("Removed 7 orphan" in f for f in result["findings"])
        assert any("3 stale tmp" in f for f in result["findings"])

    @_REQUIRES_POPOTO_1_6
    def test_handles_garbage_collect_exception(self):
        """A failure inside garbage_collect must NOT crash the reflection."""
        from popoto.fields.embedding_field import EmbeddingField

        from reflections.memory_management import run_embedding_orphan_sweep

        with (
            patch.dict("os.environ", {"EMBEDDING_ORPHAN_SWEEP_APPLY": "true"}),
            patch.object(
                EmbeddingField,
                "garbage_collect",
                side_effect=RuntimeError("synthetic failure"),
            ),
            patch.object(EmbeddingField, "sweep_stale_tempfiles", return_value=0),
        ):
            result = run_async(run_embedding_orphan_sweep())

        assert_valid_result(result)
        # Reflection wrapper should not crash; status should be "ok"
        # with the error noted in findings.
        assert result["status"] == "ok"
        assert any("garbage_collect error" in f for f in result["findings"])
