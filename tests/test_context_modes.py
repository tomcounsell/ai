"""Tests for agent.context_modes — context builder functions for SDLC pipeline."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field

import pytest

from agent.context_modes import (
    build_compact_context,
    build_full_context,
    build_minimal_context,
    build_steering_context,
    get_context_mode,
)


@dataclass
class MockSession:
    """Lightweight mock of AgentSession for testing context builders."""

    session_id: str = "sess-abc-123"
    correlation_id: str | None = "corr-xyz-789"
    project_key: str = "ai"
    status: str = "running"
    issue_url: str | None = "https://github.com/org/repo/issues/42"
    plan_url: str | None = "https://github.com/org/repo/blob/main/docs/plans/my-feature.md"
    pr_url: str | None = None
    branch_name: str | None = "session/my-feature"
    context_summary: str | None = "Implementing context modes for SDLC pipeline"
    expectations: str | None = "Waiting for test results"
    history: list = field(
        default_factory=lambda: [
            "[stage] ISSUE COMPLETED",
            "[stage] PLAN COMPLETED",
            "[stage] BUILD IN_PROGRESS",
            "[user] Please start building",
            "[system] Auto-continued",
        ]
    )
    queued_steering_messages: list = field(default_factory=list)
    classification_type: str | None = "sdlc"
    work_item_slug: str | None = "my-feature"
    working_dir: str | None = "/Users/test/src/ai"

    def get_stage_progress(self) -> dict[str, str]:
        """Parse history to determine stage status."""
        stages = ["ISSUE", "PLAN", "BUILD", "TEST", "REVIEW", "DOCS"]
        progress = {s: "pending" for s in stages}
        for entry in self.history:
            if not isinstance(entry, str) or "[stage]" not in entry.lower():
                continue
            entry_upper = entry.upper()
            for stage in stages:
                if stage in entry_upper:
                    if "COMPLETED" in entry_upper:
                        progress[stage] = "completed"
                    elif "IN_PROGRESS" in entry_upper:
                        progress[stage] = "in_progress"
                    elif "FAILED" in entry_upper:
                        progress[stage] = "failed"
        return progress

    def get_links(self) -> dict[str, str]:
        links = {}
        if self.issue_url:
            links["issue"] = self.issue_url
        if self.plan_url:
            links["plan"] = self.plan_url
        if self.pr_url:
            links["pr"] = self.pr_url
        return links

    def get_history_list(self) -> list:
        return self.history if isinstance(self.history, list) else []


# ---------------------------------------------------------------------------
# build_full_context
# ---------------------------------------------------------------------------


class TestBuildFullContext:
    def test_passthrough_with_enriched_message(self):
        session = MockSession()
        msg = "Here is the full enriched context with lots of detail..."
        result = build_full_context(session, enriched_message=msg)
        assert "context_mode: full" in result
        assert msg in result

    def test_builds_basic_context_when_empty(self):
        session = MockSession()
        result = build_full_context(session, enriched_message="")
        assert "context_mode: full" in result
        assert "sess-abc-123" in result
        assert "ai" in result

    def test_builds_basic_context_when_no_message(self):
        session = MockSession()
        result = build_full_context(session)
        assert "context_mode: full" in result
        assert result.strip()  # Never empty

    def test_none_session_fields_graceful(self):
        session = MockSession(
            correlation_id=None,
            issue_url=None,
            plan_url=None,
            pr_url=None,
            branch_name=None,
            context_summary=None,
            expectations=None,
        )
        result = build_full_context(session)
        assert "context_mode: full" in result
        assert result.strip()


# ---------------------------------------------------------------------------
# build_compact_context
# ---------------------------------------------------------------------------


class TestBuildCompactContext:
    def test_basic_compact_context(self):
        session = MockSession()
        result = build_compact_context(session)
        assert "context_mode: compact" in result
        assert "sess-abc-123" in result
        assert "corr-xyz-789" in result
        assert "ai" in result
        assert "session/my-feature" in result

    def test_includes_stage_progress(self):
        session = MockSession()
        result = build_compact_context(session)
        assert "ISSUE" in result
        assert "completed" in result
        assert "BUILD" in result

    def test_includes_links(self):
        session = MockSession()
        result = build_compact_context(session)
        assert "issues/42" in result

    def test_includes_last_5_history(self):
        session = MockSession()
        result = build_compact_context(session)
        # Should include last 5 entries (all of them in this case)
        assert "ISSUE COMPLETED" in result
        assert "Auto-continued" in result

    def test_limits_to_last_5_history(self):
        session = MockSession(history=[f"[system] Event {i}" for i in range(10)])
        result = build_compact_context(session)
        # Should contain event 5-9 (last 5), not 0-4
        assert "Event 5" in result
        assert "Event 9" in result
        assert "Event 0" not in result

    def test_with_plan_path_reads_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            lines = [f"Line {i}\n" for i in range(50)]
            f.writelines(lines)
            f.flush()
            plan_path = f.name

        try:
            session = MockSession()
            result = build_compact_context(session, plan_path=plan_path)
            assert "Line 0" in result
            assert "Line 29" in result
            # Line 30+ should NOT be in the plan summary
            assert "Line 30" not in result
        finally:
            os.unlink(plan_path)

    def test_with_missing_plan_path(self):
        session = MockSession()
        result = build_compact_context(session, plan_path="/nonexistent/plan.md")
        # Should not crash, just skip the plan
        assert "context_mode: compact" in result

    def test_with_previous_artifacts(self):
        session = MockSession()
        artifacts = {"commit": "abc123", "url": "https://example.com"}
        result = build_compact_context(session, previous_artifacts=artifacts)
        assert "abc123" in result
        assert "https://example.com" in result

    def test_includes_context_summary_and_expectations(self):
        session = MockSession()
        result = build_compact_context(session)
        assert "Implementing context modes" in result
        assert "Waiting for test results" in result

    def test_none_fields_graceful(self):
        session = MockSession(
            correlation_id=None,
            branch_name=None,
            context_summary=None,
            expectations=None,
            history=[],
        )
        result = build_compact_context(session)
        assert "context_mode: compact" in result
        assert result.strip()


# ---------------------------------------------------------------------------
# build_minimal_context
# ---------------------------------------------------------------------------


class TestBuildMinimalContext:
    def test_basic_minimal_context(self):
        session = MockSession()
        result = build_minimal_context(session, task_description="Implement the parser")
        assert "context_mode: minimal" in result
        assert "Implement the parser" in result
        assert "session/my-feature" in result

    def test_includes_working_dir(self):
        session = MockSession()
        result = build_minimal_context(session, task_description="Build it")
        assert "/Users/test/src/ai" in result

    def test_includes_relevant_files(self):
        session = MockSession()
        result = build_minimal_context(
            session,
            task_description="Fix the bug",
            relevant_files=["src/main.py", "tests/test_main.py"],
        )
        assert "src/main.py" in result
        assert "tests/test_main.py" in result

    def test_includes_links_if_present(self):
        session = MockSession()
        result = build_minimal_context(session, task_description="Do task")
        assert "issues/42" in result

    def test_no_history_in_minimal(self):
        session = MockSession()
        result = build_minimal_context(session, task_description="Do task")
        assert "ISSUE COMPLETED" not in result
        assert "Auto-continued" not in result

    def test_no_stage_progress_in_minimal(self):
        session = MockSession()
        result = build_minimal_context(session, task_description="Do task")
        # Should not contain the stage progress table
        assert "stage_progress" not in result.lower()

    def test_raises_on_empty_task_description(self):
        session = MockSession()
        with pytest.raises(ValueError, match="task_description"):
            build_minimal_context(session, task_description="")

    def test_raises_on_whitespace_task_description(self):
        session = MockSession()
        with pytest.raises(ValueError, match="task_description"):
            build_minimal_context(session, task_description="   ")

    def test_none_fields_graceful(self):
        session = MockSession(
            branch_name=None,
            working_dir=None,
            issue_url=None,
            pr_url=None,
        )
        result = build_minimal_context(session, task_description="A task")
        assert "context_mode: minimal" in result
        assert "A task" in result


# ---------------------------------------------------------------------------
# build_steering_context
# ---------------------------------------------------------------------------


class TestBuildSteeringContext:
    def test_basic_steering_context(self):
        session = MockSession()
        result = build_steering_context(session)
        assert "context_mode: steering" in result

    def test_includes_current_stage(self):
        session = MockSession()
        result = build_steering_context(session)
        # BUILD is in_progress, so it should be the current stage
        assert "BUILD" in result

    def test_includes_completed_stages(self):
        session = MockSession()
        result = build_steering_context(session)
        assert "ISSUE" in result
        assert "PLAN" in result

    def test_includes_next_expected_stage(self):
        session = MockSession()
        result = build_steering_context(session)
        # After BUILD, next is TEST
        assert "TEST" in result

    def test_includes_queued_messages(self):
        session = MockSession(
            queued_steering_messages=[
                "Please also add docs",
                "Don't forget the tests",
            ]
        )
        result = build_steering_context(session)
        assert "Please also add docs" in result
        assert "Don't forget the tests" in result

    def test_no_queued_messages(self):
        session = MockSession(queued_steering_messages=[])
        result = build_steering_context(session)
        assert "context_mode: steering" in result
        # Should still be valid context

    def test_includes_links(self):
        session = MockSession()
        result = build_steering_context(session)
        assert "issues/42" in result

    def test_includes_context_summary_and_expectations(self):
        session = MockSession()
        result = build_steering_context(session)
        assert "Implementing context modes" in result
        assert "Waiting for test results" in result

    def test_all_completed_stages(self):
        session = MockSession(
            history=[
                "[stage] ISSUE COMPLETED",
                "[stage] PLAN COMPLETED",
                "[stage] BUILD COMPLETED",
                "[stage] TEST COMPLETED",
                "[stage] REVIEW COMPLETED",
                "[stage] DOCS COMPLETED",
            ]
        )
        result = build_steering_context(session)
        assert "context_mode: steering" in result

    def test_none_fields_graceful(self):
        session = MockSession(
            context_summary=None,
            expectations=None,
            queued_steering_messages=[],
            history=[],
        )
        result = build_steering_context(session)
        assert "context_mode: steering" in result


# ---------------------------------------------------------------------------
# get_context_mode
# ---------------------------------------------------------------------------


class TestGetContextMode:
    def test_valid_frontmatter(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("---\ntitle: Build\ncontext_fidelity: minimal\n---\n# Build Skill\n")
            f.flush()
            path = f.name
        try:
            assert get_context_mode(path) == "minimal"
        finally:
            os.unlink(path)

    def test_full_fidelity(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("---\ncontext_fidelity: full\n---\n# Full Skill\n")
            f.flush()
            path = f.name
        try:
            assert get_context_mode(path) == "full"
        finally:
            os.unlink(path)

    def test_missing_field_returns_compact(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("---\ntitle: Some Skill\n---\n# Skill\n")
            f.flush()
            path = f.name
        try:
            assert get_context_mode(path) == "compact"
        finally:
            os.unlink(path)

    def test_no_frontmatter_returns_compact(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Just a markdown file\nNo frontmatter here.\n")
            f.flush()
            path = f.name
        try:
            assert get_context_mode(path) == "compact"
        finally:
            os.unlink(path)

    def test_missing_file_returns_compact(self):
        assert get_context_mode("/nonexistent/path/SKILL.md") == "compact"

    def test_empty_file_returns_compact(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("")
            f.flush()
            path = f.name
        try:
            assert get_context_mode(path) == "compact"
        finally:
            os.unlink(path)
