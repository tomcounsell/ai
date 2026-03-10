"""Tests for agent/checkpoint.py — stage-aware checkpoint persistence."""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.checkpoint import (
    CHECKPOINT_DIR,
    STAGE_ORDER,
    PipelineCheckpoint,
    build_compact_context,
    cleanup_old_checkpoints,
    delete_checkpoint,
    get_next_stage,
    load_checkpoint,
    record_stage_completion,
    save_checkpoint,
)


@pytest.fixture
def tmp_checkpoint_dir(tmp_path, monkeypatch):
    """Redirect CHECKPOINT_DIR to a temp directory for test isolation."""
    monkeypatch.setattr("agent.checkpoint.CHECKPOINT_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def sample_checkpoint():
    """Create a basic PipelineCheckpoint for testing."""
    return PipelineCheckpoint(
        session_id="tg_valor_123_456",
        slug="my-feature",
    )


class TestPipelineCheckpointDataclass:
    def test_defaults(self):
        cp = PipelineCheckpoint(session_id="s1", slug="slug1")
        assert cp.session_id == "s1"
        assert cp.slug == "slug1"
        assert cp.timestamp == ""
        assert cp.current_stage == ""
        assert cp.completed_stages == []
        assert cp.artifacts == {}
        assert cp.retry_counts == {}
        assert cp.human_messages == []


class TestSaveAndLoadCheckpoint:
    def test_save_and_load_roundtrip(self, tmp_checkpoint_dir, sample_checkpoint):
        save_checkpoint(sample_checkpoint)
        loaded = load_checkpoint("my-feature")
        assert loaded is not None
        assert loaded.session_id == "tg_valor_123_456"
        assert loaded.slug == "my-feature"
        assert loaded.timestamp != ""  # save_checkpoint sets timestamp

    def test_save_creates_directory(self, tmp_path, monkeypatch):
        nested = tmp_path / "nested" / "dir"
        monkeypatch.setattr("agent.checkpoint.CHECKPOINT_DIR", nested)
        cp = PipelineCheckpoint(session_id="s1", slug="test-slug")
        save_checkpoint(cp)
        assert (nested / "test-slug.json").exists()

    def test_save_atomic_write(self, tmp_checkpoint_dir, sample_checkpoint):
        """Verify no .tmp file left after save."""
        save_checkpoint(sample_checkpoint)
        assert not (tmp_checkpoint_dir / "my-feature.tmp").exists()
        assert (tmp_checkpoint_dir / "my-feature.json").exists()

    def test_save_sets_timestamp(self, tmp_checkpoint_dir, sample_checkpoint):
        save_checkpoint(sample_checkpoint)
        loaded = load_checkpoint("my-feature")
        assert loaded.timestamp != ""
        # Should be a valid ISO-ish timestamp
        assert "T" in loaded.timestamp

    def test_load_missing_returns_none(self, tmp_checkpoint_dir):
        result = load_checkpoint("nonexistent")
        assert result is None

    def test_load_corrupt_file_returns_none(self, tmp_checkpoint_dir):
        (tmp_checkpoint_dir / "corrupt.json").write_text("not valid json{{{")
        result = load_checkpoint("corrupt")
        assert result is None

    def test_load_invalid_fields_returns_none(self, tmp_checkpoint_dir):
        """JSON is valid but fields don't match PipelineCheckpoint."""
        (tmp_checkpoint_dir / "bad-fields.json").write_text('{"foo": "bar"}')
        result = load_checkpoint("bad-fields")
        assert result is None


class TestDeleteCheckpoint:
    def test_delete_existing(self, tmp_checkpoint_dir, sample_checkpoint):
        save_checkpoint(sample_checkpoint)
        assert (tmp_checkpoint_dir / "my-feature.json").exists()
        delete_checkpoint("my-feature")
        assert not (tmp_checkpoint_dir / "my-feature.json").exists()

    def test_delete_nonexistent_no_error(self, tmp_checkpoint_dir):
        delete_checkpoint("nonexistent")  # Should not raise

    def test_delete_then_load_returns_none(self, tmp_checkpoint_dir, sample_checkpoint):
        save_checkpoint(sample_checkpoint)
        delete_checkpoint("my-feature")
        assert load_checkpoint("my-feature") is None


class TestRecordStageCompletion:
    def test_records_stage(self, sample_checkpoint):
        cp = record_stage_completion(sample_checkpoint, "PLAN")
        assert "PLAN" in cp.completed_stages
        assert cp.current_stage == "PLAN"

    def test_deduplicates_stages(self, sample_checkpoint):
        record_stage_completion(sample_checkpoint, "PLAN")
        record_stage_completion(sample_checkpoint, "PLAN")
        assert sample_checkpoint.completed_stages.count("PLAN") == 1

    def test_accumulates_artifacts(self, sample_checkpoint):
        record_stage_completion(
            sample_checkpoint, "PLAN", artifacts={"plan_path": "docs/plans/x.md"}
        )
        record_stage_completion(
            sample_checkpoint,
            "BUILD",
            artifacts={"pr_url": "https://github.com/org/repo/pull/42"},
        )
        assert sample_checkpoint.artifacts["plan_path"] == "docs/plans/x.md"
        assert "pr_url" in sample_checkpoint.artifacts

    def test_uppercases_stage(self, sample_checkpoint):
        record_stage_completion(sample_checkpoint, "plan")
        assert "PLAN" in sample_checkpoint.completed_stages

    def test_none_artifacts_no_error(self, sample_checkpoint):
        record_stage_completion(sample_checkpoint, "TEST", artifacts=None)
        assert "TEST" in sample_checkpoint.completed_stages


class TestGetNextStage:
    def test_empty_returns_first(self, sample_checkpoint):
        assert get_next_stage(sample_checkpoint) == "ISSUE"

    def test_after_plan_returns_build(self, sample_checkpoint):
        sample_checkpoint.completed_stages = ["ISSUE", "PLAN"]
        assert get_next_stage(sample_checkpoint) == "BUILD"

    def test_all_completed_returns_none(self, sample_checkpoint):
        sample_checkpoint.completed_stages = list(STAGE_ORDER)
        assert get_next_stage(sample_checkpoint) is None

    def test_skipped_stages(self, sample_checkpoint):
        """If ISSUE is skipped but PLAN done, next is ISSUE (first incomplete)."""
        sample_checkpoint.completed_stages = ["PLAN"]
        assert get_next_stage(sample_checkpoint) == "ISSUE"


class TestBuildCompactContext:
    def test_basic_context(self, sample_checkpoint):
        sample_checkpoint.completed_stages = ["ISSUE", "PLAN", "BUILD"]
        sample_checkpoint.artifacts = {"pr_url": "https://github.com/org/repo/pull/42"}
        ctx = build_compact_context(sample_checkpoint)
        assert "my-feature" in ctx
        assert "PLAN" in ctx
        assert "BUILD" in ctx
        assert "TEST" in ctx  # next stage after ISSUE, PLAN, BUILD
        assert "pr_url" in ctx

    def test_empty_checkpoint(self, sample_checkpoint):
        ctx = build_compact_context(sample_checkpoint)
        assert "my-feature" in ctx
        assert "ISSUE" in ctx  # next stage

    def test_all_completed(self, sample_checkpoint):
        sample_checkpoint.completed_stages = list(STAGE_ORDER)
        ctx = build_compact_context(sample_checkpoint)
        assert "Next stage" not in ctx


class TestCleanupOldCheckpoints:
    def test_removes_old_files(self, tmp_checkpoint_dir):
        old_file = tmp_checkpoint_dir / "old-slug.json"
        old_file.write_text('{"session_id": "s1", "slug": "old-slug"}')
        # Set mtime to 10 days ago
        old_mtime = time.time() - (10 * 86400)
        import os

        os.utime(old_file, (old_mtime, old_mtime))

        removed = cleanup_old_checkpoints(max_age_days=7)
        assert "old-slug" in removed
        assert not old_file.exists()

    def test_keeps_recent_files(self, tmp_checkpoint_dir):
        recent_file = tmp_checkpoint_dir / "recent-slug.json"
        recent_file.write_text('{"session_id": "s1", "slug": "recent-slug"}')
        removed = cleanup_old_checkpoints(max_age_days=7)
        assert removed == []
        assert recent_file.exists()

    def test_empty_dir_no_error(self, tmp_checkpoint_dir):
        removed = cleanup_old_checkpoints(max_age_days=7)
        assert removed == []

    def test_nonexistent_dir_no_error(self, monkeypatch):
        monkeypatch.setattr(
            "agent.checkpoint.CHECKPOINT_DIR", Path("/nonexistent/path")
        )
        removed = cleanup_old_checkpoints(max_age_days=7)
        assert removed == []


class TestFullLifecycle:
    def test_save_crash_load_resume_complete_cleanup(self, tmp_checkpoint_dir):
        """E2E: save -> 'crash' -> load -> resume -> complete -> cleanup."""
        # Phase 1: Create checkpoint after PLAN stage
        cp = PipelineCheckpoint(session_id="s1", slug="lifecycle-test")
        record_stage_completion(cp, "PLAN", artifacts={"plan_path": "docs/plans/x.md"})
        save_checkpoint(cp)

        # Phase 2: Simulate crash — discard in-memory state
        del cp

        # Phase 3: Load from disk (revival)
        loaded = load_checkpoint("lifecycle-test")
        assert loaded is not None
        assert loaded.completed_stages == ["PLAN"]
        assert loaded.artifacts["plan_path"] == "docs/plans/x.md"
        assert get_next_stage(loaded) == "ISSUE"  # ISSUE wasn't completed

        # Phase 4: Resume — record BUILD completion
        record_stage_completion(
            loaded, "BUILD", artifacts={"pr_url": "https://example.com/pull/1"}
        )
        save_checkpoint(loaded)

        # Phase 5: Verify accumulation
        reloaded = load_checkpoint("lifecycle-test")
        assert "PLAN" in reloaded.completed_stages
        assert "BUILD" in reloaded.completed_stages
        assert reloaded.artifacts["plan_path"] == "docs/plans/x.md"
        assert reloaded.artifacts["pr_url"] == "https://example.com/pull/1"

        # Phase 6: Successful completion — delete checkpoint
        delete_checkpoint("lifecycle-test")
        assert load_checkpoint("lifecycle-test") is None
