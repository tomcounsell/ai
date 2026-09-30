"""Tests for ``agent.byob_skill_triggers.infer_requires_real_chrome``.

The inference function runs on every bridge-spawned message. It must:

1. Return ``True`` for messages that genuinely intend to drive a
   BYOB-migrated skill (first-person / intent phrasing).
2. Return ``False`` for casual mentions of a platform name (e.g. quoting
   a URL, mentioning the platform in passing).
3. Never raise -- ``None`` / empty / non-string inputs return ``False``.

Per ``docs/plans/agent_browser_to_byob_skill_migration.md``: false-positives
serialize unrelated PM sessions behind the real-Chrome slot (annoying but
safe); false-negatives let two real-Chrome sessions race (corrupting
state). The asymmetry justifies tightening on word-boundaries while still
matching common casual-but-genuine intent phrasing.
"""

from __future__ import annotations

import pytest

from agent.byob_skill_triggers import (
    BYOB_SKILL_TRIGGERS,
    infer_requires_real_chrome,
)


class TestPositiveMatches:
    """Genuine intent → must return True."""

    @pytest.mark.parametrize(
        "msg",
        [
            "check my linkedin DMs",
            "Check my LinkedIn DMs",
            "CHECK MY LINKEDIN",
            "open linkedin",
            "read linkedin messages",
            "browse linkedin feed",
            "reply on linkedin to that comment",
            "reply to linkedin DMs",
            "list my linkedin DMs",
            "show me my LinkedIn inbox",
            "what's in the LinkedIn feed today?",
            "the linkedin notifications need triage",
            "LinkedIn comments piling up — clear them",
            "send an in-mail to ericson",
            "any new InMails?",
            "any new in mails?",
            "/linkedin check messages",
            "linkedin DMs",
            "linkedin messages",
            "linkedin feed",
        ],
    )
    def test_positive(self, msg: str) -> None:
        assert infer_requires_real_chrome(msg) is True, msg


class TestNegativeMatches:
    """Casual mentions / unrelated text → must return False."""

    @pytest.mark.parametrize(
        "msg",
        [
            "",
            "   ",
            "hello",
            "what time is it",
            "I bought a dishwasher today",
            # Bare 'linkedin' without first-person/intent phrasing:
            "the website is linkedin.com (informational mention)",
            "they used linkedin to recruit her",  # third-person observation
            # URL quoting: 'linkedin.com' is part of a URL with no intent.
            "https://example.com/linkedin-post",
            # Word boundary: 'linkedin' embedded in another word should not
            # match a plain 'linkedin' substring (regex word-boundary
            # protects against this).
            "delinkedinks doesn't exist as a word",
            # The plain platform name without action intent
            "linkedin",
            "LinkedIn",
            # Past-tense recap, no current action intent — these correctly
            # do not match because there is no first-person/imperative
            # phrasing. (Operator can still pass --needs-real-chrome
            # explicitly if they want.)
            "I checked linkedin last week",
            "she opened linkedin yesterday",
        ],
    )
    def test_negative(self, msg: str) -> None:
        assert infer_requires_real_chrome(msg) is False, msg


class TestDefensive:
    """Must never raise on invalid input -- bridge enqueue path safety."""

    def test_none_returns_false(self) -> None:
        assert infer_requires_real_chrome(None) is False

    def test_empty_returns_false(self) -> None:
        assert infer_requires_real_chrome("") is False

    def test_whitespace_only_returns_false(self) -> None:
        assert infer_requires_real_chrome("   \t\n") is False

    def test_non_string_does_not_raise(self) -> None:
        # The contract is "never raise" -- not "must return False for ints".
        # The function defensively str()s and then runs the regex; whatever
        # it returns is acceptable as long as no exception escapes.
        result = infer_requires_real_chrome(12345)  # type: ignore[arg-type]
        assert isinstance(result, bool)

    def test_object_with_failing_str_returns_false(self) -> None:
        class Bad:
            def __str__(self) -> str:
                raise RuntimeError("boom")

        assert infer_requires_real_chrome(Bad()) is False  # type: ignore[arg-type]


class TestRegistry:
    """Sanity checks on the BYOB_SKILL_TRIGGERS registry shape."""

    def test_linkedin_present(self) -> None:
        assert "linkedin" in BYOB_SKILL_TRIGGERS

    def test_each_skill_has_patterns(self) -> None:
        for skill, patterns in BYOB_SKILL_TRIGGERS.items():
            assert patterns, f"{skill} has no trigger patterns"
            assert all(isinstance(p, str) for p in patterns), f"{skill} has non-string patterns"
