"""PM-prefix classification: fallback token stripping (PR #1930 review, A1).

The strict path (token alone on the first line) excludes the token from the
payload by construction. The FALLBACK path (token mid-line or with light
surrounding text — a compliance miss that is still classifiable) must strip
the matched token from the payload: the payload is delivered verbatim to the
human, and the persona rule is that no raw system strings ever reach the CEO.
"""

from __future__ import annotations

import pytest

from agent.session_runner.router import (
    PM_TURN_JSON_SCHEMA,
    classify_pm_prefix,
    validate_structured_route,
)


class TestStrictPrefix:
    def test_strict_user_token_excluded_from_payload(self):
        r = classify_pm_prefix("[/user]\nHere's the answer")
        assert r.destination == "user"
        assert r.compliance_miss is False
        assert r.payload == "Here's the answer"

    def test_unknown_when_no_token(self):
        r = classify_pm_prefix("just prose, no token anywhere")
        assert r.destination == "unknown"
        assert r.compliance_miss is True
        assert r.payload == ""


class TestFallbackTokenStripping:
    @pytest.mark.parametrize(
        ("text", "destination", "expected_payload"),
        [
            # The compliance-miss shape from the review: token not alone on
            # its line — the literal token must never reach the human.
            ("[/user] Here's the answer", "user", "Here's the answer"),
            ("[/complete] shipped the fix", "complete", "shipped the fix"),
            # Token preceded by light prose (within the fallback window).
            ("Sure. [/user] the real answer", "user", "Sure.\nthe real answer"),
        ],
    )
    def test_fallback_payload_has_no_raw_token(self, text, destination, expected_payload):
        r = classify_pm_prefix(text)
        assert r.destination == destination
        assert r.compliance_miss is True
        assert "[/user]" not in r.payload
        assert "[/complete]" not in r.payload
        assert r.payload == expected_payload

    def test_fallback_multiline_keeps_content(self):
        r = classify_pm_prefix("[/user] first line\nsecond line")
        assert r.destination == "user"
        assert "[/user]" not in r.payload
        assert "first line" in r.payload
        assert "second line" in r.payload


class TestBlockedReasonSchemaField:
    """Issue #2158: the optional structured ``blocked_reason`` escape hatch."""

    def test_schema_declares_optional_blocked_reason(self):
        props = PM_TURN_JSON_SCHEMA["properties"]
        assert props["blocked_reason"] == {"type": "string"}
        # Optional — must NOT be in required.
        assert "blocked_reason" not in PM_TURN_JSON_SCHEMA["required"]

    def test_structured_route_carries_blocked_reason(self):
        r = validate_structured_route(
            {"route": "complete", "message": "abandoning", "blocked_reason": "superseded by #9999"}
        )
        assert r is not None
        assert r.destination == "complete"
        assert r.blocked_reason == "superseded by #9999"

    def test_structured_route_absent_blocked_reason_is_none(self):
        r = validate_structured_route({"route": "complete", "message": "done"})
        assert r is not None
        assert r.blocked_reason is None

    def test_whitespace_blocked_reason_normalized_to_none(self):
        r = validate_structured_route(
            {"route": "complete", "message": "done", "blocked_reason": "   \n  "}
        )
        assert r is not None
        assert r.blocked_reason is None

    def test_regex_fallback_result_has_no_blocked_reason(self):
        # The prefix-token convention carries no such slot — always None.
        r = classify_pm_prefix("[/complete]\nshipped")
        assert r.blocked_reason is None


class TestAskCoverageSchema:
    """Issue #3027: the ``ask_coverage`` per-clause disposition array.

    Phase A (this plan) is optional-but-prompted: the field is declared in
    the schema's ``properties`` but deliberately absent from ``required`` —
    tightening to required is a separate follow-up (#3035).
    """

    def test_schema_declares_optional_ask_coverage(self):
        props = PM_TURN_JSON_SCHEMA["properties"]
        assert "ask_coverage" in props
        assert props["ask_coverage"]["type"] == "array"
        assert "ask_coverage" not in PM_TURN_JSON_SCHEMA["required"]

    def test_absent_ask_coverage_is_valid(self):
        r = validate_structured_route({"route": "user", "message": "hi"})
        assert r is not None
        assert r.ask_coverage == []

    def test_empty_list_ask_coverage_is_valid(self):
        r = validate_structured_route({"route": "user", "message": "hi", "ask_coverage": []})
        assert r is not None
        assert r.ask_coverage == []

    def test_continue_route_with_empty_ask_coverage_is_valid(self):
        """route: "continue" turns address no human — [] must be accepted."""
        r = validate_structured_route(
            {"route": "continue", "message": "still working", "ask_coverage": []}
        )
        assert r is not None
        assert r.destination == "continue"
        assert r.ask_coverage == []

    def test_delivered_with_evidence_is_valid(self):
        r = validate_structured_route(
            {
                "route": "user",
                "message": "merged it",
                "ask_coverage": [
                    {"item": "merge to main", "disposition": "delivered", "evidence": "PR #123"}
                ],
            }
        )
        assert r is not None
        assert r.ask_coverage == [
            {"item": "merge to main", "disposition": "delivered", "evidence": "PR #123"}
        ]

    def test_delivered_with_empty_evidence_invalidates_whole_turn(self):
        """A bare completion claim naming no artifact must fall back to the
        regex classifier — a 'delivered' disposition with no evidence is
        treated as invalid structured output, same as a bad route enum."""
        r = validate_structured_route(
            {
                "route": "user",
                "message": "merged it",
                "ask_coverage": [
                    {"item": "merge to main", "disposition": "delivered", "evidence": ""}
                ],
            }
        )
        assert r is None

    def test_delivered_with_whitespace_only_evidence_invalidates_whole_turn(self):
        r = validate_structured_route(
            {
                "route": "user",
                "message": "merged it",
                "ask_coverage": [
                    {"item": "merge to main", "disposition": "delivered", "evidence": "   "}
                ],
            }
        )
        assert r is None

    def test_whitespace_item_dropped_not_fatal(self):
        r = validate_structured_route(
            {
                "route": "user",
                "message": "hi",
                "ask_coverage": [
                    {"item": "  ", "disposition": "not_started", "evidence": ""},
                    {
                        "item": "test on stage",
                        "disposition": "blocked",
                        "evidence": "no stage access",
                    },
                ],
            }
        )
        assert r is not None
        assert r.ask_coverage == [
            {"item": "test on stage", "disposition": "blocked", "evidence": "no stage access"}
        ]

    def test_invalid_disposition_dropped_not_fatal(self):
        r = validate_structured_route(
            {
                "route": "user",
                "message": "hi",
                "ask_coverage": [{"item": "x", "disposition": "banana", "evidence": ""}],
            }
        )
        assert r is not None
        assert r.ask_coverage == []

    def test_non_list_ask_coverage_treated_as_absent(self):
        r = validate_structured_route(
            {"route": "user", "message": "hi", "ask_coverage": "not-a-list"}
        )
        assert r is not None
        assert r.ask_coverage == []

    def test_regex_fallback_result_has_no_ask_coverage(self):
        r = classify_pm_prefix("[/user]\nhello")
        assert r.ask_coverage == []
