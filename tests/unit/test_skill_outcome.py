"""Tests for agent.skill_outcome — SkillOutcome dataclass and parser.

Covers:
- Round-trip serialization (to_dict / from_dict / to_json)
- parse_outcome_from_text with valid, missing, and malformed blocks
- format_outcome produces parseable output
- Edge cases: empty strings, None, extra fields, all status values
"""

from agent.skill_outcome import (
    VALID_STAGES,
    VALID_STATUSES,
    SkillOutcome,
    format_outcome,
    parse_outcome_from_text,
)

# --- Construction and Serialization ---


class TestSkillOutcomeConstruction:
    def test_minimal_construction(self):
        o = SkillOutcome(status="success", stage="BUILD")
        assert o.status == "success"
        assert o.stage == "BUILD"
        assert o.artifacts == {}
        assert o.notes == ""
        assert o.failure_reason is None
        assert o.next_skill is None

    def test_full_construction(self):
        o = SkillOutcome(
            status="fail",
            stage="TEST",
            artifacts={"test_count": 42, "failures": ["test_foo"]},
            notes="42 tests ran, 1 failure",
            failure_reason="test_foo assertion error",
            next_skill="/do-patch",
        )
        assert o.status == "fail"
        assert o.stage == "TEST"
        assert o.artifacts["test_count"] == 42
        assert o.failure_reason == "test_foo assertion error"
        assert o.next_skill == "/do-patch"


class TestToDict:
    def test_minimal_to_dict(self):
        o = SkillOutcome(status="success", stage="BUILD")
        d = o.to_dict()
        assert d == {
            "status": "success",
            "stage": "BUILD",
            "artifacts": {},
            "notes": "",
        }
        # None fields should be omitted
        assert "failure_reason" not in d
        assert "next_skill" not in d

    def test_full_to_dict(self):
        o = SkillOutcome(
            status="fail",
            stage="TEST",
            artifacts={"pr_url": "https://github.com/org/repo/pull/1"},
            notes="Build failed",
            failure_reason="Lint errors",
            next_skill="/do-patch",
        )
        d = o.to_dict()
        assert d["failure_reason"] == "Lint errors"
        assert d["next_skill"] == "/do-patch"
        assert d["artifacts"]["pr_url"] == "https://github.com/org/repo/pull/1"


class TestFromDict:
    def test_round_trip(self):
        original = SkillOutcome(
            status="success",
            stage="BUILD",
            artifacts={"pr_url": "https://github.com/org/repo/pull/42"},
            notes="PR created",
            next_skill="/do-test",
        )
        d = original.to_dict()
        restored = SkillOutcome.from_dict(d)
        assert restored.status == original.status
        assert restored.stage == original.stage
        assert restored.artifacts == original.artifacts
        assert restored.notes == original.notes
        assert restored.next_skill == original.next_skill

    def test_minimal_dict(self):
        d = {"status": "skipped", "stage": "DOCS"}
        o = SkillOutcome.from_dict(d)
        assert o.status == "skipped"
        assert o.artifacts == {}

    def test_missing_required_field_raises(self):
        try:
            SkillOutcome.from_dict({"status": "success"})
            assert False, "Should have raised KeyError"
        except KeyError:
            pass

    def test_non_dict_raises(self):
        try:
            SkillOutcome.from_dict("not a dict")
            assert False, "Should have raised TypeError"
        except TypeError:
            pass

    def test_extra_fields_ignored(self):
        d = {"status": "success", "stage": "BUILD", "extra_field": "ignored"}
        o = SkillOutcome.from_dict(d)
        assert o.status == "success"
        assert not hasattr(o, "extra_field")


class TestToJson:
    def test_json_roundtrip(self):
        import json

        o = SkillOutcome(
            status="success",
            stage="BUILD",
            artifacts={"branch": "session/my-feature"},
            notes="Done",
        )
        j = o.to_json()
        d = json.loads(j)
        assert d["status"] == "success"
        assert d["artifacts"]["branch"] == "session/my-feature"


class TestIsTerminal:
    def test_success_is_terminal(self):
        assert SkillOutcome(status="success", stage="BUILD").is_terminal()

    def test_fail_is_terminal(self):
        assert SkillOutcome(status="fail", stage="TEST").is_terminal()

    def test_skipped_is_terminal(self):
        assert SkillOutcome(status="skipped", stage="DOCS").is_terminal()

    def test_partial_not_terminal(self):
        assert not SkillOutcome(status="partial", stage="BUILD").is_terminal()

    def test_retry_not_terminal(self):
        assert not SkillOutcome(status="retry", stage="TEST").is_terminal()


# --- format_outcome ---


class TestFormatOutcome:
    def test_format_produces_html_comment(self):
        o = SkillOutcome(status="success", stage="BUILD", notes="Done")
        result = format_outcome(o)
        assert result.startswith("<!-- OUTCOME ")
        assert result.endswith(" -->")

    def test_format_is_parseable(self):
        o = SkillOutcome(
            status="success",
            stage="BUILD",
            artifacts={"pr_url": "https://github.com/org/repo/pull/99"},
            notes="All good",
        )
        formatted = format_outcome(o)
        parsed = parse_outcome_from_text(formatted)
        assert parsed is not None
        assert parsed.status == "success"
        assert parsed.stage == "BUILD"
        assert parsed.artifacts["pr_url"] == "https://github.com/org/repo/pull/99"


# --- parse_outcome_from_text ---


class TestParseOutcomeFromText:
    def test_empty_string(self):
        assert parse_outcome_from_text("") is None

    def test_none_input(self):
        assert parse_outcome_from_text(None) is None

    def test_no_outcome_block(self):
        text = "This is a normal response with no outcome block."
        assert parse_outcome_from_text(text) is None

    def test_valid_outcome_in_prose(self):
        text = (
            "Build complete! PR created at https://github.com/org/repo/pull/42\n\n"
            '<!-- OUTCOME {"status":"success","stage":"BUILD",'
            '"artifacts":{"pr_url":"https://github.com/org/repo/pull/42"},'
            '"notes":"PR created with 3 commits"} -->\n'
        )
        o = parse_outcome_from_text(text)
        assert o is not None
        assert o.status == "success"
        assert o.stage == "BUILD"
        assert o.artifacts["pr_url"] == "https://github.com/org/repo/pull/42"
        assert o.notes == "PR created with 3 commits"

    def test_outcome_with_failure(self):
        text = (
            '<!-- OUTCOME {"status":"fail","stage":"TEST",'
            '"notes":"3 tests failed",'
            '"failure_reason":"assertion errors in test_auth.py"} -->'
        )
        o = parse_outcome_from_text(text)
        assert o is not None
        assert o.status == "fail"
        assert o.failure_reason == "assertion errors in test_auth.py"

    def test_outcome_with_next_skill(self):
        text = (
            '<!-- OUTCOME {"status":"success","stage":"BUILD",'
            '"notes":"Build done","next_skill":"/do-test"} -->'
        )
        o = parse_outcome_from_text(text)
        assert o is not None
        assert o.next_skill == "/do-test"

    def test_malformed_json(self):
        text = "<!-- OUTCOME {not valid json} -->"
        assert parse_outcome_from_text(text) is None

    def test_json_missing_status(self):
        text = '<!-- OUTCOME {"stage":"BUILD","notes":"no status"} -->'
        assert parse_outcome_from_text(text) is None

    def test_json_missing_stage(self):
        text = '<!-- OUTCOME {"status":"success","notes":"no stage"} -->'
        assert parse_outcome_from_text(text) is None

    def test_json_array_instead_of_object(self):
        text = '<!-- OUTCOME ["not", "a", "dict"] -->'
        assert parse_outcome_from_text(text) is None

    def test_empty_artifacts_valid(self):
        text = '<!-- OUTCOME {"status":"success","stage":"REVIEW","artifacts":{}} -->'
        o = parse_outcome_from_text(text)
        assert o is not None
        assert o.artifacts == {}

    def test_unknown_status_still_parses(self):
        """Unknown status values are accepted by the parser (consumer decides)."""
        text = '<!-- OUTCOME {"status":"unknown_status","stage":"BUILD"} -->'
        o = parse_outcome_from_text(text)
        assert o is not None
        assert o.status == "unknown_status"

    def test_outcome_surrounded_by_markdown(self):
        text = (
            "## Build Report\n\n"
            "Everything went well.\n\n"
            "### Summary\n"
            "- 5 files changed\n"
            "- 120 lines added\n\n"
            '<!-- OUTCOME {"status":"success","stage":"BUILD",'
            '"artifacts":{"branch":"session/typed_skill_outcomes"},'
            '"notes":"5 files changed"} -->\n\n'
            "Thanks for reviewing!"
        )
        o = parse_outcome_from_text(text)
        assert o is not None
        assert o.status == "success"
        assert o.artifacts["branch"] == "session/typed_skill_outcomes"

    def test_multiline_outcome_block(self):
        text = (
            "<!-- OUTCOME {\n"
            '  "status": "success",\n'
            '  "stage": "DOCS",\n'
            '  "notes": "Docs updated"\n'
            "} -->"
        )
        o = parse_outcome_from_text(text)
        assert o is not None
        assert o.status == "success"
        assert o.stage == "DOCS"

    def test_first_outcome_wins(self):
        """When multiple OUTCOME blocks exist, the first one is parsed."""
        text = (
            '<!-- OUTCOME {"status":"fail","stage":"TEST"} -->\n'
            "Some retry happened...\n"
            '<!-- OUTCOME {"status":"success","stage":"TEST"} -->'
        )
        o = parse_outcome_from_text(text)
        assert o is not None
        assert o.status == "fail"  # First one wins


# --- Constants ---


class TestConstants:
    def test_valid_statuses(self):
        assert "success" in VALID_STATUSES
        assert "fail" in VALID_STATUSES
        assert "partial" in VALID_STATUSES
        assert "retry" in VALID_STATUSES
        assert "skipped" in VALID_STATUSES
        assert len(VALID_STATUSES) == 5

    def test_valid_stages(self):
        assert "PLAN" in VALID_STAGES
        assert "BUILD" in VALID_STAGES
        assert "TEST" in VALID_STAGES
        assert "REVIEW" in VALID_STAGES
        assert "DOCS" in VALID_STAGES
        assert len(VALID_STAGES) == 5
