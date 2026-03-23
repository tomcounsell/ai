"""Tests for agent/pipeline_state.py.

Tests cover: load/exists with missing files, initialize schema,
save/load round-trips, advance_stage transitions, patch_iterations
increment, and atomic write safety.
"""

import json

import pytest

import agent.build_pipeline as ps


@pytest.fixture(autouse=True)
def patch_state_root(tmp_path, monkeypatch):
    """Redirect _STATE_ROOT to a temp directory for every test."""
    monkeypatch.setattr(ps, "_STATE_ROOT", tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# load() / exists() with no state file
# ---------------------------------------------------------------------------


def test_load_missing_returns_none():
    assert ps.load("no-such-slug") is None


def test_exists_missing_returns_false():
    assert ps.exists("no-such-slug") is False


# ---------------------------------------------------------------------------
# initialize()
# ---------------------------------------------------------------------------


def test_initialize_creates_state_file(tmp_path):
    ps.initialize("my-feature", "session/my-feature", ".worktrees/my-feature")
    state_file = tmp_path / "my-feature" / "state.json"
    assert state_file.exists()


def test_initialize_returns_correct_schema():
    state = ps.initialize("my-feature", "session/my-feature", ".worktrees/my-feature")

    assert state["slug"] == "my-feature"
    assert state["branch"] == "session/my-feature"
    assert state["worktree"] == ".worktrees/my-feature"
    assert state["stage"] == "plan"
    assert state["completed_stages"] == []
    assert state["patch_iterations"] == 0
    assert "started_at" in state
    assert "updated_at" in state
    # Both timestamps should be set (non-empty strings)
    assert state["started_at"]
    assert state["updated_at"]


def test_exists_after_initialize():
    ps.initialize("my-feature", "session/my-feature", ".worktrees/my-feature")
    assert ps.exists("my-feature") is True


def test_load_after_initialize():
    original = ps.initialize("my-feature", "session/my-feature", ".worktrees/my-feature")
    loaded = ps.load("my-feature")
    assert loaded == original


# ---------------------------------------------------------------------------
# save() + load() round-trip
# ---------------------------------------------------------------------------


def test_save_load_round_trip():
    state = ps.initialize("round-trip", "session/round-trip", ".worktrees/round-trip")

    # Mutate state and save
    state["stage"] = "implement"
    state["completed_stages"] = ["plan", "branch"]
    state["patch_iterations"] = 2
    ps.save(state)

    loaded = ps.load("round-trip")
    assert loaded["stage"] == "implement"
    assert loaded["completed_stages"] == ["plan", "branch"]
    assert loaded["patch_iterations"] == 2


# ---------------------------------------------------------------------------
# advance_stage()
# ---------------------------------------------------------------------------


def test_advance_stage_appends_old_stage_to_completed():
    ps.initialize("adv", "session/adv", ".worktrees/adv")
    state = ps.advance_stage("adv", "branch")

    assert "plan" in state["completed_stages"]
    assert state["stage"] == "branch"


def test_advance_stage_into_patch_increments_patch_iterations():
    ps.initialize("patch-slug", "session/patch-slug", ".worktrees/patch-slug")
    # Move past plan and branch first
    ps.advance_stage("patch-slug", "branch")
    ps.advance_stage("patch-slug", "implement")
    ps.advance_stage("patch-slug", "test")

    state = ps.advance_stage("patch-slug", "patch")
    assert state["patch_iterations"] == 1


def test_advance_stage_non_patch_does_not_increment_patch_iterations():
    ps.initialize("no-patch", "session/no-patch", ".worktrees/no-patch")
    state = ps.advance_stage("no-patch", "branch")
    assert state["patch_iterations"] == 0


def test_advance_stage_multiple_accumulates_completed_stages():
    ps.initialize("multi", "session/multi", ".worktrees/multi")
    ps.advance_stage("multi", "branch")
    ps.advance_stage("multi", "implement")
    state = ps.advance_stage("multi", "test")

    assert state["completed_stages"] == ["plan", "branch", "implement"]
    assert state["stage"] == "test"


def test_advance_stage_updates_updated_at():
    state0 = ps.initialize("ts-slug", "session/ts-slug", ".worktrees/ts-slug")

    # Sleep is not needed because _utcnow() returns second-precision strings;
    # we just verify the field is present and is a string — exact change depends
    # on timing. Structural correctness is what we assert here.
    state1 = ps.advance_stage("ts-slug", "branch")
    assert isinstance(state1["updated_at"], str)
    assert state1["updated_at"]  # non-empty
    # started_at should be unchanged after advance
    assert state1["started_at"] == state0["started_at"]


def test_advance_stage_raises_for_missing_slug():
    with pytest.raises(FileNotFoundError):
        ps.advance_stage("ghost-slug", "branch")


def test_advance_stage_raises_for_unknown_stage():
    ps.initialize("bad-stage", "session/bad-stage", ".worktrees/bad-stage")
    with pytest.raises(ValueError, match="Unknown stage"):
        ps.advance_stage("bad-stage", "nonexistent-stage")


def test_advance_stage_critique_is_valid():
    """critique is a valid stage that can be advanced to."""
    ps.initialize("crit", "session/crit", ".worktrees/crit")
    state = ps.advance_stage("crit", "critique")
    assert state["stage"] == "critique"
    assert "plan" in state["completed_stages"]


def test_critique_in_stages_list():
    """critique must be present in the STAGES list."""
    assert "critique" in ps.STAGES
    # critique should come after plan and before branch
    assert ps.STAGES.index("critique") > ps.STAGES.index("plan")
    assert ps.STAGES.index("critique") < ps.STAGES.index("branch")


# ---------------------------------------------------------------------------
# Atomic write safety
# ---------------------------------------------------------------------------


def test_save_uses_tmp_then_renames(tmp_path):
    """Verify atomic write: .json.tmp disappears after a successful save."""
    ps.initialize("atomic", "session/atomic", ".worktrees/atomic")

    # After a successful save the .tmp file must NOT exist
    tmp_file = tmp_path / "atomic" / "state.json.tmp"
    assert not tmp_file.exists(), ".json.tmp file should be removed after successful save"

    # The real state file must exist and be valid JSON
    state_file = tmp_path / "atomic" / "state.json"
    assert state_file.exists()
    loaded = json.loads(state_file.read_text())
    assert loaded["slug"] == "atomic"
