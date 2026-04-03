"""Integration tests for artifact-based pipeline stage inference.

Tests exercise `_infer_stage_from_artifacts()` and `get_display_progress()` from
`bridge.pipeline_state` against real filesystem artifacts and the real `gh` CLI.

Prerequisites:
    - `gh` CLI authenticated (gh auth status)
    - At least one plan file in docs/plans/
    - At least one merged PR with a session/ branch prefix

No mocks on subprocess or Path -- the only mock is AgentSession (requires Redis).
Tests discover artifacts dynamically and skip gracefully when unavailable.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bridge.pipeline_graph import DISPLAY_STAGES
from bridge.pipeline_state import PipelineStateMachine


# ---------------------------------------------------------------------------
# Fixtures -- dynamic artifact discovery
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def plan_slug():
    """Discover a real plan slug from docs/plans/.

    Scans for any .md file in docs/plans/ and returns its stem as the slug.
    Skips the entire module if no plan files exist.
    """
    plans_dir = Path("docs/plans")
    if not plans_dir.exists():
        pytest.skip("docs/plans/ directory does not exist")

    plan_files = sorted(plans_dir.glob("*.md"))
    if not plan_files:
        pytest.skip("No plan files found in docs/plans/")

    return plan_files[0].stem


@pytest.fixture(scope="module")
def plan_path(plan_slug):
    """Return the Path to the discovered plan file."""
    return Path(f"docs/plans/{plan_slug}.md")


@pytest.fixture(scope="module")
def merged_pr_slug():
    """Discover a slug from a real merged PR with a session/ branch.

    Queries GitHub for a recently merged PR whose head branch starts with
    'session/'. Skips if none found or gh CLI is unavailable.
    """
    try:
        result = subprocess.run(
            [
                "gh", "pr", "list",
                "--state", "merged",
                "--limit", "20",
                "--json", "headRefName,number",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("gh CLI unavailable or timed out")

    if result.returncode != 0:
        pytest.skip(f"gh pr list failed: {result.stderr}")

    try:
        prs = json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.skip("Could not parse gh pr list output")

    for pr in prs:
        branch = pr.get("headRefName", "")
        if branch.startswith("session/"):
            return branch.removeprefix("session/")

    pytest.skip("No merged PRs with session/ branch prefix found")


@pytest.fixture(scope="module")
def merged_pr_data(merged_pr_slug):
    """Fetch full PR data for the merged PR slug."""
    try:
        result = subprocess.run(
            [
                "gh", "pr", "view",
                f"session/{merged_pr_slug}",
                "--json", "number,reviewDecision,state,statusCheckRollup,files",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("gh CLI unavailable or timed out")

    if result.returncode != 0:
        pytest.skip(f"gh pr view failed for session/{merged_pr_slug}")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.skip("Could not parse gh pr view output")


def _make_state_machine(stage_states=None):
    """Create a PipelineStateMachine with a mock AgentSession.

    The session is mocked because it requires Redis. All artifact inference
    uses real filesystem and real gh CLI.
    """
    session = MagicMock()
    session.stage_states = stage_states or {}
    return PipelineStateMachine(session=session)


# ---------------------------------------------------------------------------
# Tests: _infer_stage_from_artifacts with real plan files
# ---------------------------------------------------------------------------


class TestPlanFileInference:
    """Test inference from real plan files on disk."""

    def test_plan_inferred_from_existing_file(self, plan_slug):
        """A real plan file should cause PLAN to be inferred as completed."""
        sm = _make_state_machine()
        inferred = sm._infer_stage_from_artifacts(plan_slug)
        assert inferred.get("PLAN") == "completed", (
            f"Expected PLAN=completed for slug '{plan_slug}' but got {inferred.get('PLAN')}"
        )

    def test_issue_inferred_from_existing_plan(self, plan_slug):
        """ISSUE should be inferred as completed when a plan file exists."""
        sm = _make_state_machine()
        inferred = sm._infer_stage_from_artifacts(plan_slug)
        assert inferred.get("ISSUE") == "completed", (
            f"Expected ISSUE=completed for slug '{plan_slug}' but got {inferred.get('ISSUE')}"
        )

    def test_critique_inferred_from_ready_frontmatter(self, plan_slug, plan_path):
        """CRITIQUE should be completed if the plan has status: Ready."""
        plan_text = plan_path.read_text()
        has_ready = "status: Ready" in plan_text or "status: ready" in plan_text

        sm = _make_state_machine()
        inferred = sm._infer_stage_from_artifacts(plan_slug)

        if has_ready:
            assert inferred.get("CRITIQUE") == "completed", (
                "Plan has status: Ready but CRITIQUE not inferred as completed"
            )
        else:
            assert "CRITIQUE" not in inferred, (
                "Plan does not have status: Ready but CRITIQUE was inferred"
            )


# ---------------------------------------------------------------------------
# Tests: _infer_stage_from_artifacts with real GitHub PRs
# ---------------------------------------------------------------------------


class TestGitHubPRInference:
    """Test inference from real merged PRs via gh CLI."""

    def test_build_inferred_from_merged_pr(self, merged_pr_slug, merged_pr_data):
        """BUILD should be completed when a PR exists for the slug."""
        sm = _make_state_machine()
        inferred = sm._infer_stage_from_artifacts(merged_pr_slug)
        assert inferred.get("BUILD") == "completed", (
            f"Expected BUILD=completed for merged PR slug '{merged_pr_slug}'"
        )

    def test_merge_inferred_from_merged_pr(self, merged_pr_slug, merged_pr_data):
        """MERGE should be completed for a merged PR."""
        sm = _make_state_machine()
        inferred = sm._infer_stage_from_artifacts(merged_pr_slug)

        if merged_pr_data.get("state", "").upper() == "MERGED":
            assert inferred.get("MERGE") == "completed", (
                f"PR state is MERGED but MERGE not inferred for slug '{merged_pr_slug}'"
            )

    def test_review_inference_matches_pr_data(self, merged_pr_slug, merged_pr_data):
        """REVIEW inference should match actual review decision from PR data."""
        sm = _make_state_machine()
        inferred = sm._infer_stage_from_artifacts(merged_pr_slug)

        review_decision = (merged_pr_data.get("reviewDecision") or "").upper()
        if review_decision in ("APPROVED", "CHANGES_REQUESTED"):
            assert inferred.get("REVIEW") == "completed"
        else:
            # No review decision means REVIEW should not be inferred
            assert "REVIEW" not in inferred

    def test_docs_inference_matches_pr_files(self, merged_pr_slug, merged_pr_data):
        """DOCS inference should match whether PR actually touched docs/."""
        sm = _make_state_machine()
        inferred = sm._infer_stage_from_artifacts(merged_pr_slug)

        files = merged_pr_data.get("files") or []
        has_docs = any(
            (f.get("path") or "").startswith("docs/")
            and not (f.get("path") or "").startswith("docs/plans/")
            for f in files
        )

        if has_docs:
            assert inferred.get("DOCS") == "completed", (
                f"PR has docs/ files but DOCS not inferred for slug '{merged_pr_slug}'"
            )
        else:
            assert "DOCS" not in inferred


# ---------------------------------------------------------------------------
# Tests: Edge cases and failure paths
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test graceful handling of missing/invalid slugs."""

    def test_nonexistent_slug_returns_empty(self):
        """A slug with no plan file and no PR should return empty dict."""
        sm = _make_state_machine()
        inferred = sm._infer_stage_from_artifacts("nonexistent-slug-xyz-999")
        # Should not crash; may return empty or partial dict
        assert isinstance(inferred, dict)
        # No plan file for this slug, so PLAN should not be inferred
        assert "PLAN" not in inferred

    def test_empty_slug_returns_empty(self):
        """Empty string slug should not crash."""
        sm = _make_state_machine()
        inferred = sm._infer_stage_from_artifacts("")
        assert isinstance(inferred, dict)

    def test_slug_with_special_characters(self):
        """Slug with special characters should not crash."""
        sm = _make_state_machine()
        inferred = sm._infer_stage_from_artifacts("slug-with-special/chars@#$")
        assert isinstance(inferred, dict)


# ---------------------------------------------------------------------------
# Tests: get_display_progress integration
# ---------------------------------------------------------------------------


class TestDisplayProgress:
    """Test get_display_progress with real artifacts."""

    def test_display_progress_without_slug_returns_stored_state(self):
        """Without slug, get_display_progress returns stored state only."""
        sm = _make_state_machine({"PLAN": "completed", "BUILD": "in_progress"})
        progress = sm.get_display_progress()

        assert progress["PLAN"] == "completed"
        assert progress["BUILD"] == "in_progress"
        # Stages without stored state should be pending
        for stage in DISPLAY_STAGES:
            assert stage in progress

    def test_display_progress_with_plan_slug_fills_gaps(self, plan_slug):
        """With a real slug, display progress should fill pending gaps."""
        sm = _make_state_machine()
        progress = sm.get_display_progress(slug=plan_slug)

        # All display stages should be present
        for stage in DISPLAY_STAGES:
            assert stage in progress

        # PLAN should be inferred as completed from the real plan file
        assert progress["PLAN"] == "completed"
        assert progress["ISSUE"] == "completed"

    def test_display_progress_stored_state_takes_precedence(self, plan_slug):
        """Stored non-pending/non-ready state should override inferred state."""
        sm = _make_state_machine({"PLAN": "in_progress"})
        progress = sm.get_display_progress(slug=plan_slug)

        # Stored in_progress should NOT be overridden by inferred completed
        assert progress["PLAN"] == "in_progress"

    def test_display_progress_with_merged_pr_slug(self, merged_pr_slug):
        """Display progress with a merged PR slug should show BUILD completed."""
        sm = _make_state_machine()
        progress = sm.get_display_progress(slug=merged_pr_slug)

        assert progress.get("BUILD") == "completed"

    def test_display_progress_returns_all_display_stages(self, plan_slug):
        """Result should contain exactly the DISPLAY_STAGES keys."""
        sm = _make_state_machine()
        progress = sm.get_display_progress(slug=plan_slug)

        assert set(progress.keys()) == set(DISPLAY_STAGES)
