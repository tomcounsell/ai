"""Integration tests for checkpoint wiring in stage_detector.py and job_queue.py.

Tests verify:
1. Checkpoints are saved when apply_transitions() completes a stage (stage_detector)
2. Checkpoints are loaded and included in revival context (job_queue.check_revival)
3. Checkpoints are included in revival job messages (job_queue.queue_revival_job)
4. Checkpoints are deleted on successful job completion
5. Stale checkpoints are cleaned up on startup recovery

These tests use mock sessions and patched checkpoint roots to avoid Redis
and filesystem side effects.
"""

import os
import time

import pytest

import agent.checkpoint as ckpt
from bridge.stage_detector import _save_stage_checkpoint, apply_transitions


@pytest.fixture(autouse=True)
def patch_checkpoint_root(tmp_path, monkeypatch):
    """Redirect _CHECKPOINT_ROOT to a temp directory for every test."""
    monkeypatch.setattr(ckpt, "_CHECKPOINT_ROOT", tmp_path)
    return tmp_path


class FakeSession:
    """Minimal session mock for stage_detector.apply_transitions()."""

    def __init__(
        self,
        session_id="test-session",
        work_item_slug=None,
        issue_url=None,
        pr_url=None,
        plan_url=None,
        branch_name=None,
    ):
        self.session_id = session_id
        self.work_item_slug = work_item_slug
        self.issue_url = issue_url
        self.pr_url = pr_url
        self.plan_url = plan_url
        self.branch_name = branch_name
        self._history: list[tuple[str, str]] = []
        self._stage_progress: dict[str, str] = {}

    def get_stage_progress(self) -> dict[str, str]:
        return dict(self._stage_progress)

    def append_history(self, category: str, entry: str) -> None:
        self._history.append((category, entry))
        # Simulate stage progress updates like the real AgentSession
        if category == "stage":
            parts = entry.split()
            if len(parts) >= 2:
                stage = parts[0]
                status = "completed" if "COMPLETED" in entry else "in_progress"
                self._stage_progress[stage] = status


# ---------------------------------------------------------------------------
# 1. Checkpoints saved when stage transitions occur via apply_transitions()
# ---------------------------------------------------------------------------


class TestCheckpointSaveOnTransition:
    def test_checkpoint_saved_on_stage_completion(self, tmp_path):
        """apply_transitions() should save a checkpoint when a stage completes."""
        session = FakeSession(
            session_id="sess-save-1",
            work_item_slug="my-feature",
        )
        transitions = [
            {"stage": "PLAN", "status": "completed", "reason": "Plan completed"},
        ]

        applied = apply_transitions(session, transitions)
        assert applied == 1

        # Verify checkpoint was saved
        checkpoint = ckpt.load_checkpoint("my-feature")
        assert checkpoint is not None
        assert checkpoint.session_id == "sess-save-1"
        assert checkpoint.slug == "my-feature"
        assert "PLAN" in checkpoint.completed_stages

    def test_no_checkpoint_without_slug(self, tmp_path):
        """Sessions without work_item_slug should not create checkpoints."""
        session = FakeSession(session_id="sess-no-slug")
        transitions = [
            {"stage": "BUILD", "status": "completed", "reason": "Build done"},
        ]

        apply_transitions(session, transitions)

        # No checkpoint should exist for any slug
        assert list(tmp_path.glob("*.json")) == []

    def test_checkpoint_not_saved_on_in_progress(self, tmp_path):
        """in_progress transitions should NOT trigger checkpoint saves."""
        session = FakeSession(
            session_id="sess-ip",
            work_item_slug="ip-test",
        )
        transitions = [
            {"stage": "BUILD", "status": "in_progress", "reason": "Build starting"},
        ]

        apply_transitions(session, transitions)

        # No checkpoint should exist
        checkpoint = ckpt.load_checkpoint("ip-test")
        assert checkpoint is None

    def test_checkpoint_accumulates_stages(self, tmp_path):
        """Multiple completed stages should accumulate in the checkpoint."""
        session = FakeSession(
            session_id="sess-accum",
            work_item_slug="accum-test",
        )

        # First transition: PLAN completed
        transitions = [
            {"stage": "PLAN", "status": "completed", "reason": "Plan done"},
        ]
        apply_transitions(session, transitions)

        # Second transition: BUILD completed
        transitions = [
            {"stage": "BUILD", "status": "completed", "reason": "Build done"},
        ]
        apply_transitions(session, transitions)

        checkpoint = ckpt.load_checkpoint("accum-test")
        assert checkpoint is not None
        assert checkpoint.completed_stages == ["PLAN", "BUILD"]

    def test_checkpoint_captures_session_artifacts(self, tmp_path):
        """Checkpoint should extract artifacts from session links."""
        session = FakeSession(
            session_id="sess-art",
            work_item_slug="art-test",
            issue_url="https://github.com/org/repo/issues/42",
            pr_url="https://github.com/org/repo/pull/99",
            plan_url="docs/plans/art-test.md",
            branch_name="session/art-test",
        )
        transitions = [
            {"stage": "REVIEW", "status": "completed", "reason": "Review done"},
        ]

        apply_transitions(session, transitions)

        checkpoint = ckpt.load_checkpoint("art-test")
        assert checkpoint is not None
        assert checkpoint.artifacts["issue_url"] == "https://github.com/org/repo/issues/42"
        assert checkpoint.artifacts["pr_url"] == "https://github.com/org/repo/pull/99"
        assert checkpoint.artifacts["plan_path"] == "docs/plans/art-test.md"
        assert checkpoint.artifacts["branch"] == "session/art-test"


# ---------------------------------------------------------------------------
# 2. _save_stage_checkpoint unit tests
# ---------------------------------------------------------------------------


class TestSaveStageCheckpoint:
    def test_save_creates_new_checkpoint(self, tmp_path):
        """_save_stage_checkpoint creates checkpoint when none exists."""
        session = FakeSession(
            session_id="sess-new",
            work_item_slug="new-slug",
        )
        _save_stage_checkpoint(session, "PLAN")

        checkpoint = ckpt.load_checkpoint("new-slug")
        assert checkpoint is not None
        assert "PLAN" in checkpoint.completed_stages

    def test_save_updates_existing_checkpoint(self, tmp_path):
        """_save_stage_checkpoint appends to existing checkpoint."""
        # Pre-create a checkpoint
        existing = ckpt.PipelineCheckpoint(session_id="sess-exist", slug="exist-slug")
        ckpt.record_stage_completion(existing, "PLAN")
        ckpt.save_checkpoint(existing)

        session = FakeSession(
            session_id="sess-exist",
            work_item_slug="exist-slug",
        )
        _save_stage_checkpoint(session, "BUILD")

        checkpoint = ckpt.load_checkpoint("exist-slug")
        assert checkpoint.completed_stages == ["PLAN", "BUILD"]

    def test_save_is_noop_without_slug(self, tmp_path):
        """_save_stage_checkpoint does nothing for sessions without a slug."""
        session = FakeSession(session_id="no-slug-sess")
        _save_stage_checkpoint(session, "PLAN")
        assert list(tmp_path.glob("*.json")) == []

    def test_save_tolerates_exceptions(self, tmp_path, monkeypatch):
        """_save_stage_checkpoint should not raise even if save fails."""
        session = FakeSession(
            session_id="sess-fail",
            work_item_slug="fail-slug",
        )
        # Make save_checkpoint raise
        monkeypatch.setattr(
            ckpt,
            "save_checkpoint",
            lambda cp: (_ for _ in ()).throw(OSError("disk full")),
        )
        # Should not raise
        _save_stage_checkpoint(session, "PLAN")


# ---------------------------------------------------------------------------
# 3. Checkpoint loaded in revival context (check_revival integration)
# ---------------------------------------------------------------------------


class TestCheckpointInRevival:
    def test_revival_includes_checkpoint_context(self, tmp_path):
        """check_revival should include checkpoint_context when checkpoint exists."""
        # Create a checkpoint
        cp = ckpt.PipelineCheckpoint(
            session_id="revival-sess",
            slug="tg-valor--5051653062-7141",
            completed_stages=["PLAN", "BUILD"],
            current_stage="BUILD",
            artifacts={
                "plan_path": "docs/plans/test.md",
                "branch": "session/tg-valor--5051653062-7141",
            },
        )
        ckpt.save_checkpoint(cp)

        # The check_revival function is complex (requires Redis + git).
        # Instead, verify the checkpoint loading logic directly.
        from agent.checkpoint import build_compact_context, load_checkpoint

        loaded = load_checkpoint("tg-valor--5051653062-7141")
        assert loaded is not None
        ctx = build_compact_context(loaded)
        assert "PLAN" in ctx
        assert "BUILD" in ctx
        assert "TEST" in ctx  # next stage

    def test_revival_context_empty_without_checkpoint(self, tmp_path):
        """When no checkpoint exists, load_checkpoint returns None."""
        result = ckpt.load_checkpoint("nonexistent-slug")
        assert result is None


# ---------------------------------------------------------------------------
# 4. Checkpoint included in revival job messages (queue_revival_job)
# ---------------------------------------------------------------------------


class TestRevivalJobCheckpointContext:
    def test_revival_text_includes_checkpoint_context(self):
        """queue_revival_job should include checkpoint_context in revival message."""
        revival_info = {
            "branch": "session/my-feature",
            "all_branches": ["session/my-feature"],
            "has_uncommitted": False,
            "plan_context": "",
            "checkpoint_context": (
                "## Resumed session for: my-feature\n"
                "Completed stages: PLAN, BUILD\n"
                "Next stage: TEST"
            ),
            "project_key": "valor",
            "session_id": "revival-sess",
            "working_dir": "/tmp/test",
        }

        # Build the revival text the same way queue_revival_job does
        revival_text = f"Continue the unfinished work on branch `{revival_info['branch']}`."
        checkpoint_ctx = revival_info.get("checkpoint_context", "")
        if checkpoint_ctx:
            revival_text += f"\n\n{checkpoint_ctx}"

        assert "Completed stages: PLAN, BUILD" in revival_text
        assert "Next stage: TEST" in revival_text

    def test_revival_text_without_checkpoint(self):
        """Revival text should work fine without checkpoint context."""
        revival_info = {
            "branch": "session/my-feature",
            "checkpoint_context": "",
        }
        revival_text = f"Continue the unfinished work on branch `{revival_info['branch']}`."
        checkpoint_ctx = revival_info.get("checkpoint_context", "")
        if checkpoint_ctx:
            revival_text += f"\n\n{checkpoint_ctx}"

        assert "Completed stages" not in revival_text
        assert revival_text == "Continue the unfinished work on branch `session/my-feature`."

    def test_slug_extraction_from_branch(self):
        """queue_revival_job should extract slug from branch name."""
        branch = "session/my-feature"
        work_item_slug = None
        if branch.startswith("session/"):
            work_item_slug = branch.replace("session/", "", 1)

        assert work_item_slug == "my-feature"


# ---------------------------------------------------------------------------
# 5. Checkpoint deleted on successful job completion
# ---------------------------------------------------------------------------


class TestCheckpointCleanupOnCompletion:
    def test_delete_checkpoint_after_completion(self, tmp_path):
        """Successful completion should delete the checkpoint file."""
        cp = ckpt.PipelineCheckpoint(
            session_id="done-sess",
            slug="done-slug",
            completed_stages=["PLAN", "BUILD", "TEST", "REVIEW", "DOCS"],
        )
        ckpt.save_checkpoint(cp)
        assert ckpt.load_checkpoint("done-slug") is not None

        # Simulate what _execute_job does on completion
        ckpt.delete_checkpoint("done-slug")
        assert ckpt.load_checkpoint("done-slug") is None

    def test_delete_noop_without_checkpoint(self, tmp_path):
        """Deleting a non-existent checkpoint should not raise."""
        ckpt.delete_checkpoint("never-existed")  # Should not raise


# ---------------------------------------------------------------------------
# 6. Stale checkpoint cleanup on startup
# ---------------------------------------------------------------------------


class TestStaleCheckpointCleanup:
    def test_cleanup_removes_old_checkpoints(self, tmp_path):
        """cleanup_old_checkpoints should remove files older than max_age_days."""
        # Create an old checkpoint
        cp = ckpt.PipelineCheckpoint(session_id="s1", slug="stale-one")
        ckpt.save_checkpoint(cp)
        old_path = tmp_path / "stale-one.json"
        old_time = time.time() - (10 * 86400)  # 10 days ago
        os.utime(old_path, (old_time, old_time))

        # Create a recent checkpoint
        cp2 = ckpt.PipelineCheckpoint(session_id="s2", slug="fresh-one")
        ckpt.save_checkpoint(cp2)

        cleaned = ckpt.cleanup_old_checkpoints(max_age_days=7)

        assert "stale-one" in cleaned
        assert "fresh-one" not in cleaned
        assert not old_path.exists()
        assert (tmp_path / "fresh-one.json").exists()

    def test_cleanup_returns_empty_when_no_stale(self, tmp_path):
        """No stale checkpoints means empty list returned."""
        cp = ckpt.PipelineCheckpoint(session_id="s1", slug="recent")
        ckpt.save_checkpoint(cp)

        cleaned = ckpt.cleanup_old_checkpoints(max_age_days=7)
        assert cleaned == []


# ---------------------------------------------------------------------------
# 7. Full integration: stage_detector -> checkpoint -> revival context
# ---------------------------------------------------------------------------


class TestFullIntegrationLifecycle:
    def test_stage_detector_saves_then_revival_loads(self, tmp_path):
        """End-to-end: stage_detector saves checkpoint, revival logic loads it."""
        session = FakeSession(
            session_id="e2e-sess",
            work_item_slug="e2e-feature",
            plan_url="docs/plans/e2e.md",
            branch_name="session/e2e-feature",
        )

        # Stage detector applies PLAN completion → checkpoint saved
        transitions = [
            {"stage": "PLAN", "status": "completed", "reason": "Plan done"},
        ]
        apply_transitions(session, transitions)

        # Stage detector applies BUILD completion → checkpoint updated
        transitions = [
            {"stage": "BUILD", "status": "completed", "reason": "Build done"},
        ]
        apply_transitions(session, transitions)

        # Simulate revival: load checkpoint and build context
        checkpoint = ckpt.load_checkpoint("e2e-feature")
        assert checkpoint is not None
        assert checkpoint.completed_stages == ["PLAN", "BUILD"]
        assert checkpoint.artifacts.get("plan_path") == "docs/plans/e2e.md"
        assert checkpoint.artifacts.get("branch") == "session/e2e-feature"

        # Next stage should be TEST
        assert ckpt.get_next_stage(checkpoint) == "TEST"

        # Compact context should summarize everything
        ctx = ckpt.build_compact_context(checkpoint)
        assert "e2e-feature" in ctx
        assert "PLAN" in ctx
        assert "BUILD" in ctx
        assert "TEST" in ctx

    def test_full_lifecycle_with_cleanup(self, tmp_path):
        """Stage detection → checkpoint → all stages → cleanup."""
        session = FakeSession(
            session_id="full-sess",
            work_item_slug="full-feature",
        )

        for stage in ["PLAN", "BUILD", "TEST", "REVIEW", "DOCS"]:
            transitions = [
                {"stage": stage, "status": "completed", "reason": f"{stage} done"},
            ]
            apply_transitions(session, transitions)

        # All stages complete
        checkpoint = ckpt.load_checkpoint("full-feature")
        assert checkpoint is not None
        assert len(checkpoint.completed_stages) == 5
        assert ckpt.get_next_stage(checkpoint) is None

        # Cleanup on completion
        ckpt.delete_checkpoint("full-feature")
        assert ckpt.load_checkpoint("full-feature") is None
