"""Tests for delivery-choice filtering in filter_tool_logs().

Delivery-choice prefixes (REACT:, SEND, EDIT:, SILENT, CONTINUE) are internal
agent control signals that should be filtered from user-facing output. They are
parsed by the stop hook to set delivery_action, but can leak through the nudge
loop's parallel send path as literal text.
"""

from bridge.response import filter_tool_logs


class TestDeliveryChoiceFiltering:
    """Delivery-choice lines are filtered from agent output."""

    def test_react_with_emoji(self):
        """REACT: followed by emoji is filtered completely."""
        assert filter_tool_logs("REACT: \U0001f605") == ""

    def test_react_no_emoji(self):
        """REACT: with no emoji is still filtered."""
        assert filter_tool_logs("REACT:") == ""

    def test_react_whitespace_only(self):
        """REACT: with trailing whitespace is filtered."""
        assert filter_tool_logs("REACT:  ") == ""

    def test_send(self):
        """Bare SEND is filtered."""
        assert filter_tool_logs("SEND") == ""

    def test_edit_with_content(self):
        """EDIT: followed by revised text is filtered."""
        assert filter_tool_logs("EDIT: revised text here") == ""

    def test_silent(self):
        """Bare SILENT is filtered."""
        assert filter_tool_logs("SILENT") == ""

    def test_continue(self):
        """Bare CONTINUE is filtered."""
        assert filter_tool_logs("CONTINUE") == ""

    def test_case_insensitive_react(self):
        """Lowercase react: is also filtered."""
        assert filter_tool_logs("react: \U0001f605") == ""

    def test_case_insensitive_send(self):
        """Lowercase send is also filtered."""
        assert filter_tool_logs("send") == ""

    def test_mixed_content_filters_only_delivery_line(self):
        """In multi-line text, only the delivery-choice line is removed."""
        result = filter_tool_logs("Hello\nREACT: \U0001f605")
        assert "Hello" in result
        assert "REACT" not in result

    def test_mixed_content_preserves_other_lines(self):
        """Multi-line text with delivery choice in middle preserves other lines."""
        result = filter_tool_logs("Line one\nSILENT\nLine three")
        assert "Line one" in result
        assert "Line three" in result
        assert "SILENT" not in result


class TestNoFalsePositives:
    """Normal text containing delivery-choice words is NOT filtered."""

    def test_reacting_in_sentence(self):
        """Word 'reacting' in normal text is not filtered."""
        text = "I am reacting to this"
        assert "reacting" in filter_tool_logs(text)

    def test_send_as_substring(self):
        """'send' as part of a longer message is not filtered."""
        text = "Please send the report"
        assert "send" in filter_tool_logs(text)

    def test_react_in_middle_of_sentence(self):
        """'react' embedded in a sentence is not filtered."""
        text = "We should react quickly to this issue"
        assert "react" in filter_tool_logs(text)

    def test_edit_in_sentence(self):
        """'edit' in a normal sentence is not filtered."""
        text = "You can edit the document later"
        assert "edit" in filter_tool_logs(text)

    def test_continue_in_sentence(self):
        """'continue' in a normal sentence is not filtered."""
        text = "Let me continue with the explanation"
        assert "continue" in filter_tool_logs(text)

    def test_silent_in_sentence(self):
        """'silent' in a normal sentence is not filtered."""
        text = "The system was silent for a while"
        assert "silent" in filter_tool_logs(text)
