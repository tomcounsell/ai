"""Tests for bridge.coach — coaching message builder."""

import tempfile
from pathlib import Path

from bridge.coach import (
    SKILL_DETECTORS,
    STAGE_TO_SKILL,
    _build_heuristic_rejection_coaching,
    _build_sdlc_stage_coaching,
    _detect_active_skill,
    _extract_success_criteria,
    build_coaching_message,
    detect_skill_from_phase,
)
from bridge.summarizer import ClassificationResult, OutputType


def _make_classification(
    output_type,
    confidence=0.95,
    reason="test",
    rejected=False,
    coaching_message=None,
):
    return ClassificationResult(
        output_type=output_type,
        confidence=confidence,
        reason=reason,
        was_rejected_completion=rejected,
        coaching_message=coaching_message,
    )


class TestBuildCoachingMessage:
    """Tests for the main build_coaching_message function."""

    def test_llm_coaching_message_used_when_present(self):
        """LLM-generated coaching_message is used as Tier 1 when available."""
        classification = _make_classification(
            OutputType.STATUS_UPDATE,
            rejected=True,
            coaching_message="Run pytest and paste the output.",
        )
        msg = build_coaching_message(classification)
        assert msg.startswith("[System Coach]")
        assert "Run pytest and paste the output." in msg

    def test_llm_coaching_takes_priority_over_skill(self):
        """LLM coaching takes priority over skill-aware coaching."""
        classification = _make_classification(
            OutputType.STATUS_UPDATE,
            rejected=True,
            coaching_message="Show the test results.",
        )
        msg = build_coaching_message(classification, job_message_text="/do-build foo.md")
        assert "Show the test results." in msg
        assert "Implementing" not in msg

    def test_llm_coaching_takes_priority_over_plan_file(self):
        """LLM coaching takes priority even when plan file is available."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Plan\n\n## Success Criteria\n\n- [ ] Item 1\n\n## End\n")
            f.flush()
            classification = _make_classification(
                OutputType.STATUS_UPDATE,
                rejected=True,
                coaching_message="Include commit hashes.",
            )
            msg = build_coaching_message(classification, plan_file=f.name)
            assert "Include commit hashes." in msg
            assert "Item 1" not in msg
            Path(f.name).unlink()

    def test_heuristic_fallback_when_no_coaching_message(self):
        """Falls back to static rejection coaching when coaching_message is None."""
        classification = _make_classification(
            OutputType.STATUS_UPDATE, rejected=True, coaching_message=None
        )
        msg = build_coaching_message(classification)
        assert msg.startswith("[System Coach]")
        assert "concrete proof" in msg
        assert "wasn't accepted" in msg

    def test_non_rejected_status_gets_continue(self):
        """Genuine status updates produce plain 'continue'."""
        classification = _make_classification(OutputType.STATUS_UPDATE)
        msg = build_coaching_message(classification)
        assert msg == "continue"

    def test_skill_aware_coaching_with_plan_criteria(self):
        """When a plan file with success criteria exists, coach quotes them."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(
                "# Plan\n\n## Success Criteria\n\n- [ ] Tests pass\n- [ ] Docs updated\n\n## Next\n"
            )
            f.flush()
            classification = _make_classification(OutputType.STATUS_UPDATE)
            msg = build_coaching_message(classification, plan_file=f.name)
            assert "[System Coach]" in msg
            assert "Tests pass" in msg
            assert "Docs updated" in msg
            Path(f.name).unlink()

    def test_plan_file_without_criteria_points_to_file(self):
        """When plan exists but has no criteria section, coach points to file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Plan\n\n## Risks\n\nSome risks.\n")
            f.flush()
            classification = _make_classification(OutputType.STATUS_UPDATE)
            msg = build_coaching_message(classification, plan_file=f.name)
            assert "[System Coach]" in msg
            assert f.name in msg  # Points to the file path
            assert "success criteria" in msg.lower()
            Path(f.name).unlink()

    def test_skill_detected_from_message(self):
        """When /do-build is in message text, skill-specific coaching is used."""
        classification = _make_classification(OutputType.STATUS_UPDATE)
        msg = build_coaching_message(classification, job_message_text="/do-build docs/plans/foo.md")
        assert "[System Coach]" in msg
        assert "Implementing" in msg  # From SKILL_DETECTORS description

    def test_no_skill_no_rejection_returns_continue(self):
        """Plain status update with no context returns 'continue'."""
        classification = _make_classification(OutputType.STATUS_UPDATE)
        msg = build_coaching_message(classification, plan_file=None, job_message_text="hello")
        assert msg == "continue"

    def test_nonexistent_plan_file_falls_through_to_skill(self):
        """Nonexistent plan file falls through to skill detection from message."""
        classification = _make_classification(OutputType.STATUS_UPDATE)
        msg = build_coaching_message(
            classification,
            plan_file="/nonexistent/plan.md",
            job_message_text="/do-test",
        )
        # Nonexistent file is ignored, falls through to /do-test skill coaching
        assert "[System Coach]" in msg
        assert "Running test suites" in msg

    def test_empty_coaching_message_falls_through(self):
        """Empty string coaching_message treated as absent, falls to heuristic."""
        classification = _make_classification(
            OutputType.STATUS_UPDATE, rejected=True, coaching_message=""
        )
        msg = build_coaching_message(classification)
        assert "concrete proof" in msg  # Heuristic fallback


class TestExtractSuccessCriteria:
    """Tests for plan success criteria extraction."""

    def test_extracts_criteria(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Plan\n\n## Success Criteria\n\n- [ ] Item 1\n- [ ] Item 2\n\n## Risks\n")
            f.flush()
            result = _extract_success_criteria(f.name)
            assert "Item 1" in result
            assert "Item 2" in result
            Path(f.name).unlink()

    def test_nonexistent_file(self):
        assert _extract_success_criteria("/nonexistent/path.md") is None

    def test_no_section(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Plan\n\n## Risks\n\nSome risks.\n")
            f.flush()
            assert _extract_success_criteria(f.name) is None
            Path(f.name).unlink()

    def test_empty_section(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Plan\n\n## Success Criteria\n## Risks\n")
            f.flush()
            assert _extract_success_criteria(f.name) is None
            Path(f.name).unlink()

    def test_truncates_long_criteria(self):
        """Criteria longer than 500 chars get truncated."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            long_criteria = "- [ ] " + "x" * 600 + "\n"
            f.write(f"# Plan\n\n## Success Criteria\n\n{long_criteria}\n## End\n")
            f.flush()
            classification = _make_classification(OutputType.STATUS_UPDATE)
            msg = build_coaching_message(classification, plan_file=f.name)
            assert "..." in msg  # Truncated
            Path(f.name).unlink()


class TestDetectActiveSkill:
    """Tests for skill detection from message text."""

    def test_detects_do_build(self):
        result = _detect_active_skill("/do-build docs/plans/foo.md")
        assert result is not None
        assert result["phase"] == "build"

    def test_detects_do_plan(self):
        result = _detect_active_skill("/do-plan my-feature")
        assert result is not None
        assert result["phase"] == "plan"

    def test_detects_do_test(self):
        result = _detect_active_skill("/do-test")
        assert result is not None
        assert result["phase"] == "test"

    def test_detects_do_docs(self):
        result = _detect_active_skill("/do-docs 42")
        assert result is not None
        assert result["phase"] == "document"

    def test_no_skill(self):
        assert _detect_active_skill("just a regular message") is None

    def test_empty_string(self):
        assert _detect_active_skill("") is None

    def test_none_input(self):
        assert _detect_active_skill(None) is None


class TestDetectSkillFromPhase:
    """Tests for skill detection from workflow phase."""

    def test_build_phase(self):
        result = detect_skill_from_phase("build")
        assert result is not None
        assert result["phase"] == "build"

    def test_plan_phase(self):
        result = detect_skill_from_phase("plan")
        assert result is not None
        assert result["phase"] == "plan"

    def test_test_phase(self):
        result = detect_skill_from_phase("test")
        assert result is not None

    def test_document_phase(self):
        result = detect_skill_from_phase("document")
        assert result is not None

    def test_unknown_phase(self):
        assert detect_skill_from_phase("unknown") is None

    def test_none_phase(self):
        assert detect_skill_from_phase(None) is None


class TestSkillDetectorsMapping:
    """Tests for the SKILL_DETECTORS configuration."""

    def test_all_four_sdlc_skills_present(self):
        assert "/do-plan" in SKILL_DETECTORS
        assert "/do-build" in SKILL_DETECTORS
        assert "/do-test" in SKILL_DETECTORS
        assert "/do-docs" in SKILL_DETECTORS

    def test_each_skill_has_required_keys(self):
        for trigger, info in SKILL_DETECTORS.items():
            assert "phase" in info, f"{trigger} missing 'phase'"
            assert "description" in info, f"{trigger} missing 'description'"
            assert "evidence_hint" in info, f"{trigger} missing 'evidence_hint'"


class TestHeuristicRejectionCoaching:
    """Tests for the static/heuristic rejection coaching fallback."""

    def test_contains_system_coach_prefix(self):
        msg = _build_heuristic_rejection_coaching()
        assert msg.startswith("[System Coach]")

    def test_explanatory_tone(self):
        """Rejection coaching should explain what happened, not bark commands."""
        msg = _build_heuristic_rejection_coaching()
        assert "looked like a completion" in msg
        assert "wasn't accepted" in msg

    def test_contains_guidance(self):
        msg = _build_heuristic_rejection_coaching()
        assert "concrete proof" in msg


class TestSdlcStageCoaching:
    """Tests for SDLC stage progress coaching (Tier 1c)."""

    def test_plan_completed_build_pending_mentions_do_build(self):
        """After PLAN completes, coaching should mention /do-build."""
        progress = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "BUILD": "pending",
            "TEST": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
        }
        msg = _build_sdlc_stage_coaching(progress)
        assert msg is not None
        assert "[System Coach]" in msg
        assert "/do-build" in msg
        assert "PLAN" in msg  # mentions completed stage
        assert "BUILD" in msg  # mentions next stage

    def test_build_completed_test_pending_mentions_do_test(self):
        """After BUILD completes, coaching should mention /do-test."""
        progress = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "BUILD": "completed",
            "TEST": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
        }
        msg = _build_sdlc_stage_coaching(progress)
        assert msg is not None
        assert "/do-test" in msg

    def test_test_completed_review_pending_mentions_do_pr_review(self):
        """After TEST completes, coaching should mention /do-pr-review."""
        progress = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "REVIEW": "pending",
            "DOCS": "pending",
        }
        msg = _build_sdlc_stage_coaching(progress)
        assert msg is not None
        assert "/do-pr-review" in msg

    def test_review_completed_docs_pending_mentions_do_docs(self):
        """After REVIEW completes, coaching should mention /do-docs."""
        progress = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "REVIEW": "completed",
            "DOCS": "pending",
        }
        msg = _build_sdlc_stage_coaching(progress)
        assert msg is not None
        assert "/do-docs" in msg

    def test_all_completed_returns_none(self):
        """When all stages are completed, returns None to fall through."""
        progress = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "REVIEW": "completed",
            "DOCS": "completed",
        }
        msg = _build_sdlc_stage_coaching(progress)
        assert msg is None

    def test_empty_dict_returns_none(self):
        """Empty progress dict returns None to fall through."""
        msg = _build_sdlc_stage_coaching({})
        assert msg is None

    def test_none_returns_none(self):
        """None input returns None."""
        msg = _build_sdlc_stage_coaching(None)
        assert msg is None

    def test_all_pending_returns_none(self):
        """All stages pending (no progress) returns None."""
        progress = {
            "ISSUE": "pending",
            "PLAN": "pending",
            "BUILD": "pending",
            "TEST": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
        }
        msg = _build_sdlc_stage_coaching(progress)
        assert msg is None

    def test_in_progress_stage_is_reported(self):
        """In-progress stages are mentioned in the coaching message."""
        progress = {
            "ISSUE": "completed",
            "PLAN": "in_progress",
            "BUILD": "pending",
            "TEST": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
        }
        msg = _build_sdlc_stage_coaching(progress)
        assert msg is not None
        assert "In progress: PLAN" in msg

    def test_contains_do_not_investigate_directive(self):
        """Coaching should include explicit 'do NOT investigate' directive."""
        progress = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "BUILD": "pending",
            "TEST": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
        }
        msg = _build_sdlc_stage_coaching(progress)
        assert msg is not None
        assert "Do NOT investigate" in msg

    def test_issue_pending_skips_to_plan(self):
        """ISSUE has no skill mapping, so coaching skips to PLAN."""
        progress = {
            "ISSUE": "pending",
            "PLAN": "pending",
            "BUILD": "pending",
            "TEST": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
        }
        # All pending with nothing completed -> returns None
        msg = _build_sdlc_stage_coaching(progress)
        assert msg is None


class TestSdlcCoachingIntegration:
    """Tests that SDLC coaching integrates correctly with build_coaching_message."""

    def test_sdlc_progress_produces_coaching(self):
        """build_coaching_message with sdlc_stage_progress produces SDLC coaching."""
        classification = _make_classification(OutputType.STATUS_UPDATE)
        progress = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "BUILD": "pending",
            "TEST": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
        }
        msg = build_coaching_message(classification, sdlc_stage_progress=progress)
        assert "[System Coach]" in msg
        assert "/do-build" in msg

    def test_sdlc_progress_none_falls_through(self):
        """Without sdlc_stage_progress, existing coaching tiers are unchanged."""
        classification = _make_classification(OutputType.STATUS_UPDATE)
        msg = build_coaching_message(classification, sdlc_stage_progress=None)
        assert msg == "continue"

    def test_sdlc_all_completed_falls_through_to_skill(self):
        """All-completed progress falls through to skill-aware coaching."""
        classification = _make_classification(OutputType.STATUS_UPDATE)
        all_done = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "REVIEW": "completed",
            "DOCS": "completed",
        }
        msg = build_coaching_message(
            classification,
            sdlc_stage_progress=all_done,
            job_message_text="/do-build foo.md",
        )
        # Should fall through to skill-aware coaching since SDLC coaching returns None
        assert "[System Coach]" in msg
        assert "Implementing" in msg  # skill-aware for /do-build

    def test_llm_coaching_takes_priority_over_sdlc(self):
        """LLM coaching (Tier 1) takes priority over SDLC coaching (Tier 1c)."""
        classification = _make_classification(
            OutputType.STATUS_UPDATE,
            coaching_message="Do something specific.",
        )
        progress = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "BUILD": "pending",
            "TEST": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
        }
        msg = build_coaching_message(classification, sdlc_stage_progress=progress)
        assert "Do something specific." in msg
        assert "/do-build" not in msg  # SDLC coaching was not used

    def test_heuristic_rejection_takes_priority_over_sdlc(self):
        """Heuristic rejection coaching (Tier 1b) takes priority over SDLC (Tier 1c)."""
        classification = _make_classification(
            OutputType.STATUS_UPDATE, rejected=True, coaching_message=None
        )
        progress = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "BUILD": "pending",
            "TEST": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
        }
        msg = build_coaching_message(classification, sdlc_stage_progress=progress)
        assert "concrete proof" in msg  # Heuristic rejection coaching
        assert "/do-build" not in msg

    def test_existing_coaching_without_sdlc_unchanged(self):
        """Existing coaching behavior is not affected when sdlc_stage_progress is absent."""
        classification = _make_classification(OutputType.STATUS_UPDATE)
        msg = build_coaching_message(classification, plan_file=None, job_message_text="hello")
        assert msg == "continue"


class TestStageToSkillMapping:
    """Tests for the STAGE_TO_SKILL configuration."""

    def test_all_actionable_stages_have_skills(self):
        """All stages that need skill invocation are mapped."""
        assert "PLAN" in STAGE_TO_SKILL
        assert "BUILD" in STAGE_TO_SKILL
        assert "TEST" in STAGE_TO_SKILL
        assert "REVIEW" in STAGE_TO_SKILL
        assert "DOCS" in STAGE_TO_SKILL

    def test_issue_stage_not_mapped(self):
        """ISSUE stage has no corresponding skill (it's a manual step)."""
        assert "ISSUE" not in STAGE_TO_SKILL

    def test_skill_names_are_correct(self):
        """Skill names match the actual /do-* commands."""
        assert STAGE_TO_SKILL["PLAN"] == "/do-plan"
        assert STAGE_TO_SKILL["BUILD"] == "/do-build"
        assert STAGE_TO_SKILL["TEST"] == "/do-test"
        assert STAGE_TO_SKILL["REVIEW"] == "/do-pr-review"
        assert STAGE_TO_SKILL["DOCS"] == "/do-docs"
