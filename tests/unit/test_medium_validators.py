"""Unit tests for per-medium wire-format validators.

Covers ``validate_telegram``, ``validate_email``, ``_validate_for_medium``,
and ``format_violations`` in ``bridge.message_drafter``.

These pure-function validators are called from the drafter's review-gate
presentation to surface wire-format mistakes to the agent *before* the
message is sent. Coverage exists in ``test_tool_call_delivery.py`` for
stop-hook classification end-to-end; this file covers the validators as
standalone units so each rule can be exercised in isolation.

Closes the Task 12 residual gap from the original message-drafter plan
(follow-up: docs/plans/message-drafter-followup.md).
"""

from __future__ import annotations

import pytest

from bridge.message_drafter import (
    Violation,
    _validate_for_medium,
    format_violations,
    validate_email,
    validate_telegram,
)


class TestValidateTelegram:
    """``validate_telegram`` should only trip on markdown tables."""

    def test_empty_string_returns_empty_list(self) -> None:
        assert validate_telegram("") == []

    def test_plain_prose_passes(self) -> None:
        assert validate_telegram("Just some plain text with no formatting.") == []

    def test_markdown_table_trips_rule(self) -> None:
        text = "Some prose\n\n| Name | Value |\n| --- | --- |\n| foo | bar |\n"
        violations = validate_telegram(text)
        assert violations
        assert all(v.rule == "no_markdown_tables" for v in violations)
        # The separator row is on line 4
        assert any(v.line == 4 for v in violations)

    def test_table_with_colons_alignment_trips_rule(self) -> None:
        text = "| Left | Right |\n| :--- | ---: |\n| a | b |"
        violations = validate_telegram(text)
        assert len(violations) == 1
        assert violations[0].rule == "no_markdown_tables"

    def test_multiple_tables_each_produce_violation(self) -> None:
        text = (
            "| a | b |\n| --- | --- |\n| 1 | 2 |\n\n"
            "Later:\n\n"
            "| x | y |\n| --- | --- |\n| 3 | 4 |\n"
        )
        violations = validate_telegram(text)
        assert len(violations) == 2
        assert all(v.rule == "no_markdown_tables" for v in violations)

    def test_bold_and_headings_pass(self) -> None:
        """Telegram allows markdown formatting other than tables."""
        text = "**Bold header**\n\n# Heading\n\n- bullet\n- bullet\n"
        assert validate_telegram(text) == []

    def test_snippet_is_truncated_to_80_chars(self) -> None:
        long_sep = "| " + " | ".join(["-" * 40] * 5) + " |"
        text = "| a | b | c | d | e |\n" + long_sep + "\n| 1 | 2 | 3 | 4 | 5 |"
        violations = validate_telegram(text)
        assert len(violations) == 1
        assert len(violations[0].snippet) <= 80


class TestValidateEmail:
    """``validate_email`` rejects markdown — fenced/inline code, headings, bold, italic,
    links, bullets, and tables (via ``validate_telegram``)."""

    def test_empty_string_returns_empty_list(self) -> None:
        assert validate_email("") == []

    def test_plain_prose_passes(self) -> None:
        assert validate_email("Hi Tom, just checking in on the PR.") == []

    def test_fenced_code_trips_rule(self) -> None:
        violations = validate_email("intro\n\n```python\ndef foo(): ...\n```\n")
        assert any(v.rule == "no_fenced_code" for v in violations)

    def test_inline_code_trips_rule(self) -> None:
        violations = validate_email("Use `foo()` to solve it.")
        assert any(v.rule == "no_inline_code" for v in violations)

    def test_heading_trips_rule(self) -> None:
        violations = validate_email("# My heading\n\nbody")
        assert any(v.rule == "no_markdown_headings" for v in violations)

    def test_bold_trips_rule(self) -> None:
        violations = validate_email("This is **bold** text.")
        assert any(v.rule == "no_bold_markdown" for v in violations)

    def test_italic_trips_rule(self) -> None:
        violations = validate_email("This is *italic* text.")
        assert any(v.rule == "no_italic_markdown" for v in violations)

    def test_markdown_link_trips_rule(self) -> None:
        violations = validate_email("See [docs](https://example.com) for more.")
        assert any(v.rule == "no_markdown_links" for v in violations)

    def test_bullet_trips_rule(self) -> None:
        violations = validate_email("list:\n- one\n- two\n")
        assert any(v.rule == "no_markdown_bullets" for v in violations)

    def test_table_trips_rule(self) -> None:
        """Email also inherits Telegram's table detection."""
        violations = validate_email("| a | b |\n| --- | --- |\n| 1 | 2 |\n")
        assert any(v.rule == "no_markdown_tables" for v in violations)

    def test_multiple_rules_produce_multiple_violations(self) -> None:
        text = "# Heading\n\n**Bold** and *italic* and `code`.\n"
        violations = validate_email(text)
        rules = {v.rule for v in violations}
        assert "no_markdown_headings" in rules
        assert "no_bold_markdown" in rules
        assert "no_italic_markdown" in rules
        assert "no_inline_code" in rules

    def test_violation_line_number_points_to_first_match(self) -> None:
        text = "line one\nline two with `code`\nline three\n"
        violations = validate_email(text)
        code_vs = [v for v in violations if v.rule == "no_inline_code"]
        assert code_vs
        assert code_vs[0].line == 2


class TestValidateForMedium:
    """``_validate_for_medium`` dispatches on medium string."""

    def test_telegram_medium_dispatches_to_telegram_validator(self) -> None:
        text = "| a | b |\n| --- | --- |\n"
        vs = _validate_for_medium(text, "telegram")
        assert len(vs) == 1
        assert vs[0].rule == "no_markdown_tables"

    def test_email_medium_dispatches_to_email_validator(self) -> None:
        vs = _validate_for_medium("**bold**", "email")
        assert any(v.rule == "no_bold_markdown" for v in vs)

    def test_unknown_medium_returns_empty_list(self) -> None:
        vs = _validate_for_medium("| a | b |\n| --- | --- |\n", "slack")
        assert vs == []

    def test_empty_medium_returns_empty_list(self) -> None:
        assert _validate_for_medium("whatever", "") == []

    def test_empty_text_returns_empty_list(self) -> None:
        assert _validate_for_medium("", "telegram") == []
        assert _validate_for_medium("", "email") == []


class TestFormatViolations:
    """``format_violations`` renders violations as a ⚠️-prefixed note."""

    def test_empty_list_returns_empty_string(self) -> None:
        assert format_violations([], medium="telegram") == ""

    def test_single_violation_renders_warning_prefix(self) -> None:
        vs = [Violation(rule="no_markdown_tables", line=4, snippet="| --- | --- |")]
        out = format_violations(vs, medium="telegram")
        assert "⚠" in out  # ⚠️ emoji prefix
        assert "1 wire-format violation(s)" in out
        assert "medium=telegram" in out
        assert "no_markdown_tables" in out
        assert "line 4" in out

    def test_multiple_violations_produce_multiline_output(self) -> None:
        vs = [
            Violation(rule="no_markdown_headings", line=1, snippet="# Heading"),
            Violation(rule="no_bold_markdown", line=3, snippet="**bold**"),
            Violation(rule="no_markdown_bullets", line=5, snippet="- item"),
        ]
        out = format_violations(vs, medium="email")
        lines = out.splitlines()
        assert len(lines) == 4  # header + 3 violations
        assert "3 wire-format violation(s)" in lines[0]
        assert "no_markdown_headings" in out
        assert "no_bold_markdown" in out
        assert "no_markdown_bullets" in out

    def test_violation_without_line_omits_line_prefix(self) -> None:
        vs = [Violation(rule="no_inline_code", line=None, snippet="`code`")]
        out = format_violations(vs, medium="email")
        assert "line None" not in out  # the rendering uses an empty string when line is None

    def test_medium_echoed_in_header(self) -> None:
        vs = [Violation(rule="no_markdown_tables", line=1, snippet="| --- |")]
        assert "medium=telegram" in format_violations(vs, medium="telegram")
        assert "medium=email" in format_violations(vs, medium="email")
        assert "medium=slack" in format_violations(vs, medium="slack")


class TestValidatorContract:
    """Contract-level assertions that document the validator API."""

    def test_validators_never_mutate_input(self) -> None:
        text = "# heading\n**bold**\n"
        original = text
        validate_telegram(text)
        validate_email(text)
        assert text == original

    def test_validators_return_distinct_violation_instances(self) -> None:
        """Each call produces a fresh list — no shared mutable state."""
        vs1 = validate_email("**bold**")
        vs2 = validate_email("**bold**")
        assert vs1 is not vs2

    @pytest.mark.parametrize(
        "medium,text,expected_rule",
        [
            ("telegram", "| a | b |\n| --- | --- |\n", "no_markdown_tables"),
            ("email", "**x**", "no_bold_markdown"),
            ("email", "# x", "no_markdown_headings"),
            ("email", "```\nx\n```", "no_fenced_code"),
            ("email", "- item\n", "no_markdown_bullets"),
            ("email", "[a](b)", "no_markdown_links"),
        ],
    )
    def test_parametrized_rule_detection(
        self, medium: str, text: str, expected_rule: str
    ) -> None:
        vs = _validate_for_medium(text, medium)
        assert any(v.rule == expected_rule for v in vs)
