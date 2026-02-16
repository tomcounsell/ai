"""Tests for bridge.coach — coaching message builder."""

import tempfile
from pathlib import Path

from bridge.coach import (
    _build_rejection_coaching,
    _detect_skill,
    _extract_success_criteria,
    build_coaching_message,
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
        classification = _make_classification(
            OutputType.STATUS_UPDATE, rejected=True
        )
        msg = build_coaching_message(classification)
        assert msg.startswith("[System Coach]")
        assert "concrete evidence" in msg

    def test_non_rejected_status_gets_continue(self):
        """Genuine status updates produce plain 'continue'."""
        classification = _make_classification(OutputType.STATUS_UPDATE)
        msg = build_coaching_message(classification)
        assert msg == "continue"

    def test_skill_aware_coaching_with_plan(self):
        """When a plan file with success criteria exists, coach references it."""
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
            Path(f.name).unlink()

    def test_skill_detected_from_message(self):
        """When /do-build is in message text, generic skill coaching is used."""
        classification = _make_classification(OutputType.STATUS_UPDATE)
        msg = build_coaching_message(
            classification, job_message_text="/do-build docs/plans/foo.md"
        )
        assert "[System Coach]" in msg

    def test_no_skill_no_rejection_returns_continue(self):
        """Plain status update with no context returns 'continue'."""
        classification = _make_classification(OutputType.STATUS_UPDATE)
        msg = build_coaching_message(
            classification, plan_file=None, job_message_text="hello"
        )
        assert msg == "continue"

    def test_rejection_takes_priority_over_skill(self):
        """Rejection coaching takes priority over skill coaching."""
        classification = _make_classification(
            OutputType.STATUS_UPDATE, rejected=True
        )
        msg = build_coaching_message(
            classification, job_message_text="/do-build foo.md"
        )
        assert "concrete evidence" in msg


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


class TestDetectSkill:
    """Tests for skill detection from message text."""

    def test_detects_do_build(self):
        assert _detect_skill("/do-build docs/plans/foo.md") is True

    def test_detects_do_plan(self):
        assert _detect_skill("/do-plan my-feature") is True

    def test_detects_do_test(self):
        assert _detect_skill("/do-test") is True

    def test_detects_do_docs(self):
        assert _detect_skill("/do-docs 42") is True

    def test_no_skill(self):
        assert _detect_skill("just a regular message") is False

    def test_empty_string(self):
        assert _detect_skill("") is False


class TestRejectionCoaching:
    """Tests for rejection coaching message content."""

    def test_contains_system_coach_prefix(self):
        msg = _build_rejection_coaching()
        assert msg.startswith("[System Coach]")

    def test_contains_guidance(self):
        msg = _build_rejection_coaching()
        assert "test output" in msg or "concrete evidence" in msg
