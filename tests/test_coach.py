"""Tests for bridge.coach — coaching message builder."""

import tempfile
from pathlib import Path

from bridge.coach import (
    SKILL_DETECTORS,
    _build_rejection_coaching,
    _detect_active_skill,
    _extract_success_criteria,
    build_coaching_message,
    detect_skill_from_phase,
)
from bridge.summarizer import ClassificationResult, OutputType


def _make_classification(output_type, confidence=0.95, reason="test", rejected=False):
    return ClassificationResult(
        output_type=output_type,
        confidence=confidence,
        reason=reason,
        was_rejected_completion=rejected,
    )


class TestBuildCoachingMessage:
    """Tests for the main build_coaching_message function."""

    def test_rejected_completion_gets_coaching(self):
        """Rejected completions produce [System Coach] messages."""
        classification = _make_classification(OutputType.STATUS_UPDATE, rejected=True)
        msg = build_coaching_message(classification)
        assert msg.startswith("[System Coach]")
        assert "concrete proof" in msg

    def test_non_rejected_status_gets_continue(self):
        """Genuine status updates produce plain 'continue'."""
        classification = _make_classification(OutputType.STATUS_UPDATE)
        msg = build_coaching_message(classification)
        assert msg == "continue"

    def test_skill_aware_coaching_with_plan_criteria(self):
        """When a plan file with success criteria exists, coach quotes them."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(
                "# Plan\n\n## Success Criteria\n\n"
                "- [ ] Tests pass\n- [ ] Docs updated\n\n## Next\n"
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
        msg = build_coaching_message(
            classification, job_message_text="/do-build docs/plans/foo.md"
        )
        assert "[System Coach]" in msg
        assert "Implementing" in msg  # From SKILL_DETECTORS description

    def test_no_skill_no_rejection_returns_continue(self):
        """Plain status update with no context returns 'continue'."""
        classification = _make_classification(OutputType.STATUS_UPDATE)
        msg = build_coaching_message(
            classification, plan_file=None, job_message_text="hello"
        )
        assert msg == "continue"

    def test_rejection_takes_priority_over_skill(self):
        """Rejection coaching takes priority over skill coaching."""
        classification = _make_classification(OutputType.STATUS_UPDATE, rejected=True)
        msg = build_coaching_message(
            classification, job_message_text="/do-build foo.md"
        )
        assert "wasn't accepted" in msg

    def test_rejection_takes_priority_over_plan_file(self):
        """Rejection coaching takes priority even when plan file is available."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Plan\n\n## Success Criteria\n\n- [ ] Item 1\n\n## End\n")
            f.flush()
            classification = _make_classification(
                OutputType.STATUS_UPDATE, rejected=True
            )
            msg = build_coaching_message(classification, plan_file=f.name)
            assert "wasn't accepted" in msg
            assert "Item 1" not in msg  # Rejection takes priority
            Path(f.name).unlink()

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


class TestExtractSuccessCriteria:
    """Tests for plan success criteria extraction."""

    def test_extracts_criteria(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(
                "# Plan\n\n## Success Criteria\n\n"
                "- [ ] Item 1\n- [ ] Item 2\n\n## Risks\n"
            )
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


class TestRejectionCoaching:
    """Tests for rejection coaching message content."""

    def test_contains_system_coach_prefix(self):
        msg = _build_rejection_coaching()
        assert msg.startswith("[System Coach]")

    def test_explanatory_tone(self):
        """Rejection coaching should explain what happened, not bark commands."""
        msg = _build_rejection_coaching()
        assert "looked like a completion" in msg
        assert "wasn't accepted" in msg

    def test_contains_guidance(self):
        msg = _build_rejection_coaching()
        assert "concrete proof" in msg
