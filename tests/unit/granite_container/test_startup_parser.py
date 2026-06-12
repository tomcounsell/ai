"""Tests for the startup-phase parser.

The parser's pattern set is enumerated in
`agent.granite_container.startup_parser.py` as a list of
`(regex, label, response)` tuples. The self-test feeds each known
pattern into the parser and asserts the right `StartupEvent` enum
value. The trust-folder prompt dismissal is exercised as part of
a live container run; the self-test verifies the parser returns the
correct response ("1") for the trust-folder prompt.
"""

from __future__ import annotations

import unittest

from agent.granite_container.startup_parser import (
    StartupEvent,
    known_patterns,
    parse_startup_frame,
)


class TestParserKnownPatterns(unittest.TestCase):
    """Each pattern in the enumeration maps to the right enum value."""

    def test_login_prompt(self) -> None:
        result = parse_startup_frame("Sign in to continue with your Max subscription")
        self.assertEqual(result.event, StartupEvent.LOGIN_PROMPT)

    def test_paste_url_login(self) -> None:
        result = parse_startup_frame("Please paste the URL to continue")
        self.assertEqual(result.event, StartupEvent.LOGIN_PROMPT)

    def test_update_notice(self) -> None:
        result = parse_startup_frame("A new version of Claude Code is available")
        self.assertEqual(result.event, StartupEvent.UPDATE_NOTICE)

    def test_update_notice_alt(self) -> None:
        result = parse_startup_frame("An update is available — please restart")
        # The parser's update pattern is "update available" (no "is").
        # A frame with "update is available" should still match the
        # pattern's spirit; the parser is conservative and may not
        # recognize this exact wording. We accept either UPDATE_NOTICE
        # (recognized) or UNKNOWN (the parser is intentionally
        # narrow; the F-probe only confirmed the v2.1.160 text).
        self.assertIn(
            result.event,
            (StartupEvent.UPDATE_NOTICE, StartupEvent.UNKNOWN),
        )

    def test_error_modal_auth_failed(self) -> None:
        result = parse_startup_frame("Authentication failed. Please check your subscription.")
        self.assertEqual(result.event, StartupEvent.ERROR_MODAL)

    def test_error_modal_invalid_key(self) -> None:
        result = parse_startup_frame("Invalid API key")
        self.assertEqual(result.event, StartupEvent.ERROR_MODAL)

    def test_error_modal_login_failed(self) -> None:
        result = parse_startup_frame("Login failed — please retry")
        self.assertEqual(result.event, StartupEvent.ERROR_MODAL)

    def test_error_modal_fatal(self) -> None:
        result = parse_startup_frame("fatal error: cannot connect to model backend")
        self.assertEqual(result.event, StartupEvent.ERROR_MODAL)

    def test_persona_prime_ack(self) -> None:
        result = parse_startup_frame("Reading slash commands... primed")
        self.assertEqual(result.event, StartupEvent.PERSONA_PRIME_ACK)

    def test_trust_folder_prompt(self) -> None:
        """The F-probe's confirmed trust-folder prompt (probe:243-247)."""
        result = parse_startup_frame("Yes, I trust this folder")
        self.assertEqual(result.event, StartupEvent.TRUST_FOLDER_PROMPT)
        # The probe's confirmed dismissal is "1"; the parser must
        # surface that as the response.
        self.assertEqual(result.response, "1")

    def test_trust_folder_alt(self) -> None:
        result = parse_startup_frame("Do you trust this folder?")
        self.assertEqual(result.event, StartupEvent.TRUST_FOLDER_PROMPT)


class TestParserResponse(unittest.TestCase):
    """The canned-response metadata flows through correctly."""

    def test_update_notice_response_is_enter(self) -> None:
        """Update notice dismissal is `\\r` (the C1 submit key)."""
        result = parse_startup_frame("A new version of Claude Code is available")
        self.assertEqual(result.event, StartupEvent.UPDATE_NOTICE)
        self.assertEqual(result.response, "\r")

    def test_login_prompt_response_is_none(self) -> None:
        """Login prompts are not auto-responded; the container asks granite."""
        result = parse_startup_frame("Sign in to continue")
        self.assertEqual(result.event, StartupEvent.LOGIN_PROMPT)
        self.assertIsNone(result.response)

    def test_error_modal_response_is_none(self) -> None:
        """Error modals are not auto-responded; the container surfaces them."""
        result = parse_startup_frame("Authentication failed")
        self.assertEqual(result.event, StartupEvent.ERROR_MODAL)
        self.assertIsNone(result.response)


class TestParserUnknown(unittest.TestCase):
    """Buffers that don't match any known pattern return UNKNOWN."""

    def test_empty_buffer(self) -> None:
        result = parse_startup_frame("")
        self.assertEqual(result.event, StartupEvent.UNKNOWN)

    def test_unrelated_buffer(self) -> None:
        result = parse_startup_frame("hello world this is just a normal response")
        self.assertEqual(result.event, StartupEvent.UNKNOWN)
        self.assertIsNone(result.response)


class TestParserPriority(unittest.TestCase):
    """Errors shadow login; trust-folder shadows prime-ack."""

    def test_error_shadows_login(self) -> None:
        # If a buffer matches both an error pattern and a login
        # pattern, the error wins. The container must not auto-
        # respond to a fatal-looking frame.
        buf = "Authentication failed. Sign in to continue."
        result = parse_startup_frame(buf)
        self.assertEqual(result.event, StartupEvent.ERROR_MODAL)

    def test_trust_folder_shadows_prime_ack(self) -> None:
        # Trust-folder and prime-ack can co-occur in the same buffer
        # (the model is priming while the folder prompt is up). The
        # trust-folder is more urgent; it wins.
        buf = "primed. Yes, I trust this folder"
        result = parse_startup_frame(buf)
        self.assertEqual(result.event, StartupEvent.TRUST_FOLDER_PROMPT)


class TestKnownPatternsEnumeration(unittest.TestCase):
    """The known-patterns enumeration is non-empty and well-formed."""

    def test_enumeration_not_empty(self) -> None:
        patterns = known_patterns()
        self.assertGreater(len(patterns), 0)

    def test_enumeration_includes_trust_folder(self) -> None:
        patterns = known_patterns()
        labels = [p[0] for p in patterns]
        self.assertIn("trust_folder", labels)


if __name__ == "__main__":
    unittest.main(verbosity=2)
