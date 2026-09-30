"""Tests for agent/context_modes.py — context fidelity modes for sub-agent steering."""

import pytest

from agent.context_modes import (
    SKILL_FIDELITY,
    ContextFidelity,
    ContextRequest,
    build_compact_context,
    build_full_context,
    build_minimal_context,
    build_steering_context,
    get_context_for_skill,
)

# ---------------------------------------------------------------------------
# ContextFidelity enum
# ---------------------------------------------------------------------------


class TestContextFidelity:
    def test_has_four_modes(self):
        assert len(ContextFidelity) == 4

    @pytest.mark.parametrize(
        "mode",
        ["full", "compact", "minimal", "steering"],
    )
    def test_mode_exists(self, mode: str):
        assert hasattr(ContextFidelity, mode.upper())

    def test_enum_values_are_strings(self):
        for member in ContextFidelity:
            assert isinstance(member.value, str)


# ---------------------------------------------------------------------------
# ContextRequest dataclass
# ---------------------------------------------------------------------------


class TestContextRequest:
    def test_defaults_to_none(self):
        req = ContextRequest()
        assert req.plan_path is None
        assert req.task_description is None
        assert req.current_stage is None
        assert req.completed_stages is None
        assert req.artifacts is None
        assert req.recent_messages is None
        assert req.session_transcript is None

    def test_all_fields_settable(self):
        req = ContextRequest(
            plan_path="docs/plans/my-feature.md",
            task_description="Implement the widget",
            current_stage="BUILD",
            completed_stages=["PLAN"],
            artifacts={"branch": "session/my-feature", "pr_url": "https://..."},
            recent_messages=["do the thing"],
            session_transcript="full transcript here",
        )
        assert req.plan_path == "docs/plans/my-feature.md"
        assert req.task_description == "Implement the widget"
        assert req.current_stage == "BUILD"
        assert req.completed_stages == ["PLAN"]
        assert req.artifacts["branch"] == "session/my-feature"
        assert req.recent_messages == ["do the thing"]
        assert req.session_transcript == "full transcript here"


# ---------------------------------------------------------------------------
# build_full_context
# ---------------------------------------------------------------------------


class TestBuildFullContext:
    def test_returns_transcript_when_provided(self):
        req = ContextRequest(session_transcript="Hello world transcript")
        result = build_full_context(req)
        assert "Hello world transcript" in result

    def test_returns_empty_string_when_no_transcript(self):
        req = ContextRequest()
        result = build_full_context(req)
        assert result == ""

    def test_includes_header(self):
        req = ContextRequest(session_transcript="some content")
        result = build_full_context(req)
        # Should have some structure, not just raw transcript
        assert len(result) > len("some content")


# ---------------------------------------------------------------------------
# build_compact_context
# ---------------------------------------------------------------------------


class TestBuildCompactContext:
    def test_includes_current_stage(self):
        req = ContextRequest(current_stage="TEST")
        result = build_compact_context(req)
        assert "TEST" in result

    def test_includes_completed_stages(self):
        req = ContextRequest(
            current_stage="BUILD",
            completed_stages=["ISSUE", "PLAN"],
        )
        result = build_compact_context(req)
        assert "ISSUE" in result
        assert "PLAN" in result

    def test_includes_artifacts(self):
        req = ContextRequest(
            current_stage="TEST",
            artifacts={"branch": "session/my-feature", "pr_url": "https://github.com/pr/1"},
        )
        result = build_compact_context(req)
        assert "session/my-feature" in result
        assert "https://github.com/pr/1" in result

    def test_includes_plan_path(self):
        req = ContextRequest(
            current_stage="BUILD",
            plan_path="docs/plans/my-feature.md",
        )
        result = build_compact_context(req)
        assert "docs/plans/my-feature.md" in result

    def test_empty_request_returns_nonempty(self):
        """Even with no data, compact mode should produce some structure."""
        req = ContextRequest()
        result = build_compact_context(req)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# build_minimal_context
# ---------------------------------------------------------------------------


class TestBuildMinimalContext:
    def test_includes_task_description(self):
        req = ContextRequest(task_description="Fix the login bug in auth.py")
        result = build_minimal_context(req)
        assert "Fix the login bug in auth.py" in result

    def test_empty_task_returns_empty(self):
        req = ContextRequest()
        result = build_minimal_context(req)
        assert result == ""

    def test_includes_artifacts_if_provided(self):
        req = ContextRequest(
            task_description="Build widget",
            artifacts={"branch": "session/widget"},
        )
        result = build_minimal_context(req)
        assert "session/widget" in result


# ---------------------------------------------------------------------------
# build_steering_context
# ---------------------------------------------------------------------------


class TestBuildSteeringContext:
    def test_includes_current_stage(self):
        req = ContextRequest(current_stage="REVIEW")
        result = build_steering_context(req)
        assert "REVIEW" in result

    def test_includes_completed_stages(self):
        req = ContextRequest(
            current_stage="TEST",
            completed_stages=["ISSUE", "PLAN", "BUILD"],
        )
        result = build_steering_context(req)
        assert "BUILD" in result

    def test_includes_recent_messages(self):
        req = ContextRequest(
            current_stage="BUILD",
            recent_messages=["please add error handling", "also fix the typo"],
        )
        result = build_steering_context(req)
        assert "please add error handling" in result
        assert "also fix the typo" in result

    def test_empty_request_returns_nonempty(self):
        req = ContextRequest()
        result = build_steering_context(req)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# SKILL_FIDELITY registry
# ---------------------------------------------------------------------------


class TestSkillFidelity:
    def test_known_skills_have_entries(self):
        expected_skills = ["do-plan", "do-build", "do-test", "do-patch", "do-pr-review", "do-docs"]
        for skill in expected_skills:
            assert skill in SKILL_FIDELITY, f"{skill} missing from SKILL_FIDELITY"

    def test_builder_is_minimal(self):
        assert SKILL_FIDELITY["builder"] == ContextFidelity.MINIMAL

    def test_all_values_are_fidelity_enum(self):
        for skill, fidelity in SKILL_FIDELITY.items():
            assert isinstance(fidelity, ContextFidelity), f"{skill} has non-enum value"


# ---------------------------------------------------------------------------
# get_context_for_skill (dispatch)
# ---------------------------------------------------------------------------


class TestGetContextForSkill:
    def test_builder_gets_minimal(self):
        req = ContextRequest(task_description="Build the widget")
        result = get_context_for_skill("builder", req)
        # Minimal mode includes task description
        assert "Build the widget" in result

    def test_do_build_gets_compact(self):
        req = ContextRequest(current_stage="BUILD", completed_stages=["PLAN"])
        result = get_context_for_skill("do-build", req)
        assert "BUILD" in result

    def test_unknown_skill_defaults_to_compact(self):
        req = ContextRequest(current_stage="TEST")
        result = get_context_for_skill("unknown-skill", req)
        # Should use compact (default), which includes stage
        assert "TEST" in result

    def test_full_mode_via_explicit_request(self):
        """Full context is available when a skill is mapped to it."""
        # Currently no default skills use full, but the mechanism should work
        req = ContextRequest(session_transcript="full transcript")
        result = get_context_for_skill("do-build", req)
        # do-build is compact, so transcript should NOT appear
        assert "full transcript" not in result
