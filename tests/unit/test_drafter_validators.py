"""Unit tests for the per-medium wire-format validators in bridge.message_drafter.

Covers:
- validate_telegram: only rejects markdown table separator rows
- validate_email: rejects all markdown syntax (fenced/inline code, headings,
  bold/italic, markdown links, markdown bullets), plus tables inherited from
  telegram rules.
- format_violations: renders the ⚠️ presentation string
- draft_message: the short-output early return still runs the medium validator
  and surfaces violations on the returned MessageDraft.

These tests are offline — no Anthropic / OpenRouter calls. We stay under the
SHORT_OUTPUT_THRESHOLD so draft_message skips the LLM path.
"""

from __future__ import annotations

import pytest

from bridge.message_drafter import (
    LOCAL_FILE_PATH_RULE,
    SHORT_OUTPUT_THRESHOLD,
    MessageDraft,
    Violation,
    detect_local_file_reference,
    draft_message,
    format_violations,
    validate_email,
    validate_telegram,
)


class TestDetectLocalFileReference:
    """detect_local_file_reference flags machine-local paths and open refs.

    Mirrors tests/unit/test_medium_validators.py's TestDetectLocalFileReference
    class — this file duplicates that file's validator coverage by existing
    convention (see docs/plans/message-drafter-file-path-flagging.md's Test
    Impact section; de-duplicating the two files is explicitly out of scope).
    """

    def test_empty_string_returns_empty_list(self):
        assert detect_local_file_reference("") == []

    def test_ordinary_prose_returns_empty_list(self):
        text = "Everything looks good. The task is complete, no issues found."
        assert detect_local_file_reference(text) == []

    def test_standalone_slash_and_tilde_are_not_flagged(self):
        text = "Use a / to separate paths, or ~ for home."
        assert detect_local_file_reference(text) == []

    def test_tmp_path_detected(self):
        violations = detect_local_file_reference("Done. Saved to /tmp/x.txt.")
        assert len(violations) == 1
        assert violations[0].rule == LOCAL_FILE_PATH_RULE

    def test_tilde_path_detected(self):
        violations = detect_local_file_reference("cd ~/projects/ai && run tests")
        assert len(violations) == 1
        assert violations[0].rule == LOCAL_FILE_PATH_RULE

    def test_users_path_detected(self):
        violations = detect_local_file_reference("Log is at /Users/tomcounsell/out.log")
        assert len(violations) == 1
        assert violations[0].rule == LOCAL_FILE_PATH_RULE

    def test_home_linux_path_detected(self):
        violations = detect_local_file_reference("Config is at /home/deploy/app.conf")
        assert len(violations) == 1
        assert violations[0].rule == LOCAL_FILE_PATH_RULE

    def test_bare_open_dash_a_detected(self):
        violations = detect_local_file_reference("Open with open -a TextEdit /tmp/x.txt")
        rules = {v.rule for v in violations}
        assert LOCAL_FILE_PATH_RULE in rules

    def test_backtick_wrapped_open_command_detected(self):
        violations = detect_local_file_reference("Run `open -a TextEdit /tmp/x.txt` to view it.")
        rules = {v.rule for v in violations}
        assert LOCAL_FILE_PATH_RULE in rules

    def test_ordinary_url_without_local_segment_passes(self):
        text = "See https://example.com/docs for more, or https://github.com/org/repo/pull/42."
        assert detect_local_file_reference(text) == []

    def test_remote_etc_path_passes(self):
        text = "The config lives at /etc/nginx/nginx.conf on your server."
        assert detect_local_file_reference(text) == []

    def test_code_block_with_unrelated_path_passes(self):
        text = "```\npath: /var/log/syslog\n```"
        assert detect_local_file_reference(text) == []


class TestValidateTelegram:
    """validate_telegram only rejects markdown table separator rows."""

    def test_plain_text_passes(self):
        assert validate_telegram("Hello, world!") == []

    def test_bullets_pass(self):
        text = "- first item\n- second item\n- third item"
        assert validate_telegram(text) == []

    def test_bold_passes(self):
        assert validate_telegram("This is **bold** text.") == []

    def test_code_passes(self):
        # Both inline code and fenced code are fine on Telegram
        assert validate_telegram("Use `make test` to run tests.") == []
        assert validate_telegram("```python\nprint('hi')\n```") == []

    def test_heading_passes(self):
        # Telegram tolerates markdown headings (the bridge renders them inline)
        assert validate_telegram("# Heading\ncontent") == []

    def test_detects_markdown_table_separator(self):
        text = "| a | b |\n| --- | --- |\n| 1 | 2 |"
        violations = validate_telegram(text)
        assert len(violations) == 1, f"expected 1 violation, got {violations}"
        assert violations[0].rule == "no_markdown_tables"
        assert violations[0].line == 2

    def test_detects_right_aligned_table_separator(self):
        # The ---: variant signals right-alignment in GFM tables. The validator
        # regex requires at least two column groups, so use a 2-col table.
        text = "| a | b |\n|---:|---:|\n| 1 | 2 |"
        violations = validate_telegram(text)
        assert len(violations) == 1, f"expected 1 violation, got {violations}"
        assert violations[0].rule == "no_markdown_tables"

    def test_detects_centered_table_separator(self):
        # The :---: variant signals centered alignment
        text = "| a | b |\n|:---:|:---:|\n| 1 | 2 |"
        violations = validate_telegram(text)
        assert len(violations) == 1, f"expected 1 violation, got {violations}"
        assert violations[0].rule == "no_markdown_tables"

    def test_empty_text_passes(self):
        assert validate_telegram("") == []


class TestValidateEmail:
    """validate_email rejects all markdown syntax plus tables."""

    def test_plain_prose_passes(self):
        text = "Hi Tom,\n\nThanks for the update. I'll take a look today.\n\n-- Valor"
        assert validate_email(text) == []

    def test_detects_fenced_code(self):
        text = "Here's the snippet:\n```python\nprint('hi')\n```"
        violations = validate_email(text)
        rules = {v.rule for v in violations}
        assert "no_fenced_code" in rules

    def test_detects_inline_code(self):
        text = "Run `make test` and let me know."
        violations = validate_email(text)
        rules = {v.rule for v in violations}
        assert "no_inline_code" in rules

    def test_detects_heading(self):
        text = "## Summary\nThings went well."
        violations = validate_email(text)
        rules = {v.rule for v in violations}
        assert "no_markdown_headings" in rules

    def test_detects_bold(self):
        text = "This is **very important** to note."
        violations = validate_email(text)
        rules = {v.rule for v in violations}
        assert "no_bold_markdown" in rules

    def test_detects_italic(self):
        text = "This is *emphasized* text."
        violations = validate_email(text)
        rules = {v.rule for v in violations}
        assert "no_italic_markdown" in rules

    def test_detects_markdown_link(self):
        text = "See [the plan](https://example.com/plan) for details."
        violations = validate_email(text)
        rules = {v.rule for v in violations}
        assert "no_markdown_links" in rules

    def test_detects_markdown_bullet(self):
        text = "Agenda:\n- first\n- second"
        violations = validate_email(text)
        rules = {v.rule for v in violations}
        assert "no_markdown_bullets" in rules

    def test_detects_table_inherited_from_telegram(self):
        text = "| a | b |\n| --- | --- |\n| 1 | 2 |"
        violations = validate_email(text)
        rules = {v.rule for v in violations}
        assert "no_markdown_tables" in rules

    def test_empty_text_passes(self):
        assert validate_email("") == []

    def test_plain_url_passes(self):
        # A bare URL is fine — only the markdown-link syntax [text](url) is rejected
        text = "See https://example.com/plan for details."
        assert validate_email(text) == []


class TestFormatViolations:
    """format_violations renders violations as a ⚠️-prefixed presentation block."""

    def test_empty_list_returns_empty_string(self):
        assert format_violations([], "telegram") == ""

    def test_populated_list_has_warning_prefix(self):
        violations = [
            Violation(rule="no_markdown_tables", line=2, snippet="| --- | --- |"),
        ]
        rendered = format_violations(violations, "telegram")
        assert rendered.startswith("⚠️")
        assert "no_markdown_tables" in rendered
        assert "telegram" in rendered
        assert "line 2" in rendered

    def test_per_rule_bullets(self):
        violations = [
            Violation(rule="no_fenced_code", line=1, snippet="```python"),
            Violation(rule="no_inline_code", line=5, snippet="`x`"),
        ]
        rendered = format_violations(violations, "email")
        # One bullet per violation
        bullet_lines = [line for line in rendered.split("\n") if line.strip().startswith("•")]
        assert len(bullet_lines) == 2
        assert any("no_fenced_code" in b for b in bullet_lines)
        assert any("no_inline_code" in b for b in bullet_lines)

    def test_violation_without_line_number(self):
        violations = [Violation(rule="no_markdown_tables", line=None, snippet="")]
        rendered = format_violations(violations, "telegram")
        # Must not crash and must omit the "line N" phrase cleanly
        assert "no_markdown_tables" in rendered
        assert "line None" not in rendered


class TestDraftMessageViolations:
    """draft_message surfaces validator violations even on the short-output early return.

    A short markdown-table input is well under SHORT_OUTPUT_THRESHOLD (200 chars)
    and contains no '?' / fenced code / artifacts, so the drafter bypasses the
    LLM. The violation list must still be populated — otherwise the review gate
    would silently ship malformed content.
    """

    @pytest.mark.asyncio
    async def test_table_triggers_no_markdown_tables_violation(self):
        text = "| a | b |\n|---|---|\n| 1 | 2 |"
        # Sanity: stay under the short-output threshold to bypass the LLM.
        assert len(text) < SHORT_OUTPUT_THRESHOLD

        result = await draft_message(text, medium="telegram")

        assert isinstance(result, MessageDraft)
        assert not hasattr(result, "was_drafted"), (
            "was_drafted field removed in passthrough refactor"
        )
        assert len(result.violations) >= 1, f"expected >=1 violation, got {result.violations}"
        rules = {v.rule for v in result.violations}
        assert "no_markdown_tables" in rules
        # Per docs/plans/message-drafter-file-path-flagging.md (Risk 2): ANY
        # non-empty violations list now promotes to needs_self_draft=True,
        # not just the local-file-path rule — a markdown-table violation
        # also defers delivery instead of shipping verbatim.
        assert result.needs_self_draft is True
        assert result.text == ""

    @pytest.mark.asyncio
    async def test_clean_short_text_has_no_violations(self):
        text = "All done — tests passed."
        assert len(text) < SHORT_OUTPUT_THRESHOLD
        result = await draft_message(text, medium="telegram")
        assert result.violations == []
        assert result.text == text
        assert not hasattr(result, "was_drafted"), (
            "was_drafted field removed in passthrough refactor"
        )

    @pytest.mark.asyncio
    async def test_email_medium_rejects_inline_code(self):
        text = "Run `pytest` now."
        assert len(text) < SHORT_OUTPUT_THRESHOLD
        result = await draft_message(text, medium="email")
        rules = {v.rule for v in result.violations}
        assert "no_inline_code" in rules
