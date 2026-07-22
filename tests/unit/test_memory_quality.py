"""Unit tests for the shared junk-definition heuristics (agent/memory_quality.py).

Covers the Phase-1 (issue #2200) success criteria for
`docs/plans/memory-telemetry-baseline.md`: ack-only detection, fragment
detection, durable full-fact classification, and the deterministic
None/empty/whitespace disposition. Pure-function tests -- no Redis, no
popoto, no network.

Marker: sdlc (feature tests for issue #2200).
"""

from __future__ import annotations

import pytest

from agent.memory_quality import classify_content, is_ack_only, is_fragment

pytestmark = pytest.mark.sdlc


class TestClassifyContentAckOnly:
    @pytest.mark.parametrize(
        "content",
        ["Yup", "Ahhh", "ok", "thanks", "Yeah", "Ohhh", "Thank you", "no thanks"],
    )
    def test_ack_only_utterances_classified_as_ack_only(self, content):
        assert classify_content(content) == "ack_only"
        assert is_ack_only(content) is True

    @pytest.mark.parametrize("content", ["Yup", "Ahhh", "ok"])
    def test_is_ack_only_true_for_bare_acknowledgements(self, content):
        assert is_ack_only(content) is True


class TestClassifyContentFragment:
    @pytest.mark.parametrize(
        "content",
        [
            "includes:",
            "The deployment process:",
            "The list is (unclosed",
            "Config has [one, two",
            "-",
            "1.",
        ],
    )
    def test_fragment_content_classified_as_fragment(self, content):
        assert classify_content(content) == "fragment"
        assert is_fragment(content) is True

    def test_dangling_colon_with_no_body_is_fragment(self):
        assert is_fragment("includes:") is True

    def test_multiline_colon_with_body_is_not_dangling(self):
        content = "Steps:\n- one\n- two"
        assert is_fragment(content) is False


class TestClassifyContentDurable:
    @pytest.mark.parametrize(
        "content",
        [
            "The deployment uses blue-green rollout with automated rollback "
            "on error rate spikes above 5%.",
            "User prefers black formatting over ruff lint enforcement.",
            "The worker restarts the bridge after code changes to agent/.",
        ],
    )
    def test_full_fact_sentences_classified_as_durable(self, content):
        assert classify_content(content) == "durable"
        assert is_ack_only(content) is False
        assert is_fragment(content) is False


class TestClassifyContentEdgeCases:
    @pytest.mark.parametrize("content", ["", None, "   ", "\n\t  "])
    def test_empty_none_whitespace_classify_as_fragment_without_raising(self, content):
        assert classify_content(content) == "fragment"

    def test_is_ack_only_false_for_none_and_empty(self):
        assert is_ack_only(None) is False
        assert is_ack_only("") is False
        assert is_ack_only("   ") is False

    def test_is_fragment_true_for_none_and_empty(self):
        assert is_fragment(None) is True
        assert is_fragment("") is True
        assert is_fragment("   ") is True

    def test_no_module_level_heavy_imports(self):
        """agent/memory_quality.py must stay a dependency-light leaf."""
        import agent.memory_quality as mq

        source = open(mq.__file__).read()
        for banned in ("import redis", "import popoto", "from redis", "from popoto", "from models"):
            assert banned not in source
