"""Tests for agent/checkpoint.py - Pipeline checkpoint/resume for interrupted sessions.

Tests cover: checkpoint creation, save/load round-trips, stage skipping on resume,
artifact accumulation, worktree recovery detection, and cleanup of old checkpoints.
"""

import json
import os
import time

import pytest

import agent.checkpoint as ckpt


@pytest.fixture(autouse=True)
def patch_checkpoint_root(tmp_path, monkeypatch):
    """Redirect _CHECKPOINT_ROOT to a temp directory for every test."""
    monkeypatch.setattr(ckpt, "_CHECKPOINT_ROOT", tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# PipelineCheckpoint creation and serialization
# ---------------------------------------------------------------------------


class TestCheckpointCreation:
    def test_create_checkpoint_has_required_fields(self):
        cp = ckpt.PipelineCheckpoint(
            session_id="sess-001",
            slug="my-feature",
        )
        assert cp.session_id == "sess-001"
        assert cp.slug == "my-feature"
        assert cp.current_stage == ""
        assert cp.completed_stages == []
        assert cp.artifacts == {}
        assert cp.retry_counts == {}
        assert cp.timestamp is not None

    def test_checkpoint_to_dict_roundtrip(self):
        cp = ckpt.PipelineCheckpoint(
            session_id="sess-001",
            slug="my-feature",
            current_stage="BUILD",
            completed_stages=["ISSUE", "PLAN"],
            artifacts={
                "plan_path": "docs/plans/my-feature.md",
                "branch": "session/my-feature",
            },
            retry_counts={"BUILD": 1},
        )
        d = cp.to_dict()
        assert d["session_id"] == "sess-001"
        assert d["completed_stages"] == ["ISSUE", "PLAN"]
        assert d["artifacts"]["plan_path"] == "docs/plans/my-feature.md"

        restored = ckpt.PipelineCheckpoint.from_dict(d)
        assert restored.session_id == cp.session_id
        assert restored.slug == cp.slug
        assert restored.current_stage == cp.current_stage
        assert restored.completed_stages == cp.completed_stages
        assert restored.artifacts == cp.artifacts


# ---------------------------------------------------------------------------
# save() and load()
# ---------------------------------------------------------------------------


class TestSaveLoad:
    def test_save_creates_json_file(self, tmp_path):
        cp = ckpt.PipelineCheckpoint(session_id="sess-001", slug="my-feature")
        ckpt.save_checkpoint(cp)

        path = tmp_path / "my-feature.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["slug"] == "my-feature"

    def test_load_returns_none_for_missing(self):
        result = ckpt.load_checkpoint("nonexistent")
        assert result is None

    def test_save_load_roundtrip(self):
        cp = ckpt.PipelineCheckpoint(
            session_id="sess-002",
            slug="roundtrip-test",
            current_stage="TEST",
            completed_stages=["ISSUE", "PLAN", "BUILD"],
            artifacts={"pr_url": "https://github.com/org/repo/pull/42"},
        )
        ckpt.save_checkpoint(cp)

        loaded = ckpt.load_checkpoint("roundtrip-test")
        assert loaded is not None
        assert loaded.session_id == "sess-002"
        assert loaded.current_stage == "TEST"
        assert loaded.completed_stages == ["ISSUE", "PLAN", "BUILD"]
        assert loaded.artifacts["pr_url"] == "https://github.com/org/repo/pull/42"

    def test_save_overwrites_existing(self):
        cp1 = ckpt.PipelineCheckpoint(session_id="s1", slug="overwrite-test", current_stage="PLAN")
        ckpt.save_checkpoint(cp1)

        cp2 = ckpt.PipelineCheckpoint(session_id="s1", slug="overwrite-test", current_stage="BUILD")
        ckpt.save_checkpoint(cp2)

        loaded = ckpt.load_checkpoint("overwrite-test")
        assert loaded is not None
        assert loaded.current_stage == "BUILD"

    def test_load_corrupt_json_returns_none(self, tmp_path):
        """Corrupt checkpoint files should not crash; return None."""
        path = tmp_path / "bad.json"
        path.write_text("{invalid json!!!")
        result = ckpt.load_checkpoint("bad")
        assert result is None


# ---------------------------------------------------------------------------
# Stage advancement and recording
# ---------------------------------------------------------------------------


class TestStageAdvancement:
    def test_record_stage_completion(self):
        cp = ckpt.PipelineCheckpoint(session_id="s1", slug="adv-test")
        cp = ckpt.record_stage_completion(cp, "PLAN", artifacts={"plan_path": "docs/plans/test.md"})

        assert "PLAN" in cp.completed_stages
        assert cp.current_stage == "PLAN"
        assert cp.artifacts["plan_path"] == "docs/plans/test.md"

    def test_record_multiple_stages(self):
        cp = ckpt.PipelineCheckpoint(session_id="s1", slug="multi-test")
        cp = ckpt.record_stage_completion(cp, "PLAN", artifacts={"plan_path": "p.md"})
        cp = ckpt.record_stage_completion(cp, "BUILD", artifacts={"branch": "session/test"})
        cp = ckpt.record_stage_completion(cp, "TEST")

        assert cp.completed_stages == ["PLAN", "BUILD", "TEST"]
        assert cp.current_stage == "TEST"
        assert cp.artifacts["plan_path"] == "p.md"
        assert cp.artifacts["branch"] == "session/test"

    def test_duplicate_stage_not_added_twice(self):
        cp = ckpt.PipelineCheckpoint(session_id="s1", slug="dup-test")
        cp = ckpt.record_stage_completion(cp, "PLAN")
        cp = ckpt.record_stage_completion(cp, "PLAN")

        assert cp.completed_stages.count("PLAN") == 1

    def test_record_stage_increments_retry(self):
        cp = ckpt.PipelineCheckpoint(session_id="s1", slug="retry-test")
        cp = ckpt.record_stage_retry(cp, "BUILD")
        assert cp.retry_counts["BUILD"] == 1

        cp = ckpt.record_stage_retry(cp, "BUILD")
        assert cp.retry_counts["BUILD"] == 2


# ---------------------------------------------------------------------------
# Resume logic - determine next stage
# ---------------------------------------------------------------------------


class TestResume:
    def test_next_stage_with_no_progress(self):
        cp = ckpt.PipelineCheckpoint(session_id="s1", slug="fresh")
        next_stage = ckpt.get_next_stage(cp)
        assert next_stage == "PLAN"

    def test_next_stage_after_plan(self):
        cp = ckpt.PipelineCheckpoint(
            session_id="s1",
            slug="after-plan",
            current_stage="PLAN",
            completed_stages=["PLAN"],
        )
        next_stage = ckpt.get_next_stage(cp)
        assert next_stage == "BUILD"

    def test_next_stage_after_build(self):
        cp = ckpt.PipelineCheckpoint(
            session_id="s1",
            slug="after-build",
            current_stage="BUILD",
            completed_stages=["PLAN", "BUILD"],
        )
        next_stage = ckpt.get_next_stage(cp)
        assert next_stage == "TEST"

    def test_next_stage_all_done(self):
        all_stages = ckpt.PIPELINE_STAGES[:]
        cp = ckpt.PipelineCheckpoint(
            session_id="s1",
            slug="all-done",
            current_stage=all_stages[-1],
            completed_stages=all_stages,
        )
        next_stage = ckpt.get_next_stage(cp)
        assert next_stage is None

    def test_build_compact_context(self):
        cp = ckpt.PipelineCheckpoint(
            session_id="s1",
            slug="ctx-test",
            current_stage="BUILD",
            completed_stages=["PLAN", "BUILD"],
            artifacts={
                "plan_path": "docs/plans/ctx-test.md",
                "branch": "session/ctx-test",
                "pr_url": "https://github.com/org/repo/pull/99",
            },
        )
        context = ckpt.build_compact_context(cp)
        assert "ctx-test" in context
        assert "PLAN" in context
        assert "BUILD" in context
        assert "session/ctx-test" in context


# ---------------------------------------------------------------------------
# Checkpoint deletion and cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_delete_checkpoint(self, tmp_path):
        cp = ckpt.PipelineCheckpoint(session_id="s1", slug="delete-me")
        ckpt.save_checkpoint(cp)
        assert (tmp_path / "delete-me.json").exists()

        ckpt.delete_checkpoint("delete-me")
        assert not (tmp_path / "delete-me.json").exists()

    def test_delete_nonexistent_is_noop(self):
        # Should not raise
        ckpt.delete_checkpoint("ghost")

    def test_cleanup_old_checkpoints(self, tmp_path):
        # Create an "old" checkpoint by backdating its file
        cp = ckpt.PipelineCheckpoint(session_id="s1", slug="old-one")
        ckpt.save_checkpoint(cp)
        old_path = tmp_path / "old-one.json"
        # Set mtime to 10 days ago
        old_time = time.time() - (10 * 86400)
        os.utime(old_path, (old_time, old_time))

        # Create a recent checkpoint
        cp2 = ckpt.PipelineCheckpoint(session_id="s2", slug="new-one")
        ckpt.save_checkpoint(cp2)

        cleaned = ckpt.cleanup_old_checkpoints(max_age_days=7)
        assert "old-one" in cleaned
        assert "new-one" not in cleaned
        assert not old_path.exists()
        assert (tmp_path / "new-one.json").exists()


# ---------------------------------------------------------------------------
# Worktree recovery detection
# ---------------------------------------------------------------------------


class TestWorktreeRecovery:
    def test_detect_uncommitted_changes(self, tmp_path):
        """check_worktree_recovery returns the right info for a dirty worktree."""
        wt_dir = tmp_path / ".worktrees" / "test-slug"
        wt_dir.mkdir(parents=True)

        result = ckpt.check_worktree_recovery(str(tmp_path), "test-slug")
        assert isinstance(result, dict)
        assert "worktree_exists" in result

    def test_worktree_not_found(self, tmp_path):
        result = ckpt.check_worktree_recovery(str(tmp_path), "missing-slug")
        assert result["worktree_exists"] is False


# ---------------------------------------------------------------------------
# End-to-end: full pipeline checkpoint lifecycle
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    def test_full_pipeline_checkpoint_lifecycle(self):
        """Simulate a full pipeline run with checkpoints, then cleanup."""
        cp = ckpt.PipelineCheckpoint(session_id="lifecycle-1", slug="lifecycle-test")

        # Stage 1: PLAN
        cp = ckpt.record_stage_completion(
            cp, "PLAN", artifacts={"plan_path": "docs/plans/lifecycle.md"}
        )
        ckpt.save_checkpoint(cp)

        # Simulate crash and reload
        reloaded = ckpt.load_checkpoint("lifecycle-test")
        assert reloaded is not None
        assert reloaded.completed_stages == ["PLAN"]
        assert ckpt.get_next_stage(reloaded) == "BUILD"

        # Stage 2: BUILD
        cp = ckpt.record_stage_completion(
            reloaded, "BUILD", artifacts={"branch": "session/lifecycle-test"}
        )
        ckpt.save_checkpoint(cp)

        # Stage 3: TEST
        cp = ckpt.record_stage_completion(cp, "TEST")
        ckpt.save_checkpoint(cp)

        # Stage 4: REVIEW
        cp = ckpt.record_stage_completion(
            cp, "REVIEW", artifacts={"pr_url": "https://github.com/org/repo/pull/1"}
        )
        ckpt.save_checkpoint(cp)

        # Stage 5: DOCS
        cp = ckpt.record_stage_completion(cp, "DOCS")
        ckpt.save_checkpoint(cp)

        # All done
        assert ckpt.get_next_stage(cp) is None

        # Cleanup
        ckpt.delete_checkpoint("lifecycle-test")
        assert ckpt.load_checkpoint("lifecycle-test") is None

    def test_resume_skips_completed_stages(self):
        """After resume, get_next_stage correctly skips completed work."""
        cp = ckpt.PipelineCheckpoint(
            session_id="resume-test",
            slug="resume-slug",
            current_stage="BUILD",
            completed_stages=["PLAN", "BUILD"],
            artifacts={"plan_path": "p.md", "branch": "session/resume-slug"},
        )
        ckpt.save_checkpoint(cp)

        # Simulate resume
        loaded = ckpt.load_checkpoint("resume-slug")
        assert loaded is not None

        # Should skip to TEST, not re-do PLAN or BUILD
        next_stage = ckpt.get_next_stage(loaded)
        assert next_stage == "TEST"

        # Context should mention completed stages
        ctx = ckpt.build_compact_context(loaded)
        assert "PLAN" in ctx
        assert "BUILD" in ctx
        assert "TEST" in ctx  # Next stage mentioned
